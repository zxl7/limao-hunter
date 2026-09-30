#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全项目语法守护（2026-09-18）。

⛔ 背景（真实事故）：`scripts/intraday_beat.py` 因编辑引入**语法错误**
（`{"tag": "plain_text", content": ...}` 少一个引号）→ 脚本运行即崩、节拍卡静默停推，
而**没有任何测试 import 它** → 全量 pytest 仍 **449 passed**、系统表面"全绿"。

教训：**「测试全绿」≠「脚本能跑」**——未被 import 的脚本是回归网的盲区。
本测试把「全项目可编译」纳入回归网，任何语法错误立刻 FAIL。
"""
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("scripts", "wealth_code", "common", "tests")
MIN_EXPECTED = 50          # 扫描面守卫：文件数骤降说明扫描逻辑坏了


def _py_files() -> list[Path]:
    files: list[Path] = []
    for d in SCAN_DIRS:
        p = ROOT / d
        if p.exists():
            files += [f for f in p.rglob("*.py") if "__pycache__" not in str(f)]
    return sorted(files)


def test_all_python_files_compile(tmp_path):
    """全项目 .py 必须全部可编译（防"语法错误 + 无测试覆盖 = 静默崩溃"）。"""
    files = _py_files()
    assert len(files) >= MIN_EXPECTED, f"扫描文件数异常偏少: {len(files)}（检查 SCAN_DIRS）"
    bad: list[str] = []
    for f in files:
        try:
            py_compile.compile(str(f), doraise=True,
                               cfile=str(tmp_path / f"{f.stem}_{abs(hash(str(f)))}.pyc"))
        except py_compile.PyCompileError as e:
            last = [ln for ln in str(e).splitlines() if ln.strip()][-1:]
            bad.append(f"{f.relative_to(ROOT)} → {last[0][:140] if last else 'compile error'}")
        except Exception as e:      # noqa: BLE001
            bad.append(f"{f.relative_to(ROOT)} → {type(e).__name__}: {str(e)[:120]}")
    assert bad == [], "存在语法错误的文件：\n" + "\n".join(bad)


def test_live_signal_and_beat_importable():
    """高频盘中链的两个关键脚本必须可 import（曾因语法错误静默崩溃·无人发现）。"""
    import importlib
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    for mod in ("intraday_beat", "card_lint", "card_copy_rules", "live_signal"):
        m = importlib.import_module(mod)
        assert m is not None, mod
