#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一质量门入口（2026-09-02 · 中枢体检 P0-2）。

背景：体检里的「质量门」此前跑不通——validate_tasks 依赖 PyYAML、pytest 未安装，
而调度器用 `python3`（系统解释器）执行，与装依赖的隔离环境不是同一个解释器，
于是出现「脚本没错但一跑就 ModuleNotFoundError」。

本入口做的事：
  ① 自动挑选可用解释器（隔离环境 > 当前解释器），保证 yaml/pytest 可用
  ② 依次跑：validate_config → validate_tasks → validate_schema → pytest
  ③ 统一输出 PASS/WARN/FAIL 汇总，任一 FAIL → exit 1（可被定时任务/CI 判定）

用法：
  python3 scripts/quality_gate.py            # 人类可读报告
  python3 scripts/quality_gate.py --json     # JSON（供体检/中枢消费）
  python3 scripts/quality_gate.py --skip-pytest   # 快速模式（跳过单测）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "bin" / "python"

CHECKS = [
    ("配置完整性", [str(ROOT / "scripts" / "validate_config.py")]),
    ("任务注册表", [str(ROOT / "scripts" / "validate_tasks.py")]),
    ("数据契约", [str(ROOT / "scripts" / "validate_schema.py")]),
]


def pick_python() -> str:
    """优先用装了 PyYAML/pytest 的隔离环境；否则退回当前解释器。"""
    cands = [str(VENV_PY), sys.executable]
    for py in cands:
        if not (py and Path(py).exists()):
            continue
        try:
            r = subprocess.run([py, "-c", "import yaml, pytest"],
                               capture_output=True, timeout=60)
            if r.returncode == 0:
                return py
        except Exception:
            continue
    return sys.executable


def _run(py: str, args: list[str], timeout: int = 600) -> tuple[int, str]:
    try:
        r = subprocess.run([py] + args, cwd=str(ROOT), capture_output=True,
                           text=True, timeout=timeout,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）"
    except Exception as e:
        return 127, f"执行异常: {e}"


def main() -> int:
    as_json = "--json" in sys.argv
    skip_pytest = "--skip-pytest" in sys.argv
    py = pick_python()
    results = []

    for name, script in CHECKS:
        code, out = _run(py, script)
        results.append({
            "name": name,
            "ok": code == 0,
            "exit": code,
            "detail": out.splitlines()[-6:] if out else [],
        })

    if not skip_pytest:
        code, out = _run(py, ["-m", "pytest", "tests/", "-q"])
        tail = [ln for ln in out.splitlines() if ln.strip()][-3:]
        results.append({
            "name": "单元测试",
            "ok": code == 0,
            "exit": code,
            "detail": tail,
        })

    failed = [r for r in results if not r["ok"]]
    payload = {
        "python": py,
        "all_pass": not failed,
        "results": results,
    }

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"== 狸猫猎手质量门（解释器 {py}）==")
        for r in results:
            flag = "✅ PASS" if r["ok"] else "❌ FAIL"
            print(f"\n[{flag}] {r['name']}（exit={r['exit']}）")
            for ln in r["detail"]:
                print(f"   {ln}")
        print(f"\n结果: {'✅ 全部通过' if not failed else f'❌ {len(failed)} 项失败'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
