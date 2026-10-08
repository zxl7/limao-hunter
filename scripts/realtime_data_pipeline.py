#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时数据层：采集、计算并原子发布数据就绪标记；不发送任何消息。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
PULSE = CACHE / "pulse_snapshots"
READY = PULSE / "data_ready.json"

STEPS = (
    ("intraday_pulse", ("scripts/intraday_pulse.py", "--mode", "intra", "--json-only", "--force"), "intraday.log"),
    ("gen_candidates", ("scripts/gen_candidates.py",), "candidates.log"),
    ("feedback_matrix", ("scripts/feedback_matrix.py",), "feedback.log"),
    ("ma5_check", ("scripts/ma5_check.py",), "ma5.log"),
    ("memory_detect", ("scripts/memory_lib.py", "detect"), "detect.log"),
    ("intraday_timeline", ("scripts/intraday_timeline.py",), "timeline.log"),
    ("realtime_analytics", ("scripts/realtime_analytics.py",), "analytics.log"),
)


def _publish_theme_series() -> int:
    """发布题材序列及当日盘中时间点；展示端只读取已发布 JSON。"""
    try:
        # 本脚本以 `scripts/realtime_data_pipeline.py` 启动时 sys.path[0]=scripts/，
        # 不含仓库根 → `import common.*` 会 ModuleNotFoundError（2026-09-30 事故）。
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from common.theme_series import export_series_json, record_intraday_point

        record_intraday_point()
        export_series_json()
        return 0
    except Exception as exc:  # noqa: BLE001 — 单独记入数据就绪状态，禁止旧序列冒充新产物
        print(f"[theme-series] publish failed: {exc}", file=sys.stderr)
        return 1


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write_ready(value: dict) -> None:
    tmp = READY.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, READY)


def _compute_ready(*, results: dict, feedback_error, rotation_date) -> tuple[bool, str]:
    """计算数据是否就绪（纯函数，便于回归测试）。

    ⛔ 2026-10-08 修复前：`same_source` 拿 `rotation.source_ts` 与每 30s 刷新的
       `latest.ts` 做**精确相等**比较——两者由不同进程（realtime_analytics / pulse_fetcher）
       按不同节奏写，存在天然竞态，实际几乎永远不相等 → `ready` 永远 False → 守卫每个
       交易日误报「盘面数据断了」。

    现改为：
      · `same_source` 用**日期级**新鲜度：题材轮动引擎已产出今日快照（date==今日）即视为
        同步，与盘中实时时间戳解耦，彻底消除竞态。
      · feedback_matrix 在 09:26 前盘前刻意写 `error="pre_auction"`（防未开盘假象），属
        **良性盘前标记**而非数据断档；只有真实错误才阻断就绪。
    """
    required_ok = all(
        results.get(label) == 0
        for label in ("intraday_pulse", "feedback_matrix", "realtime_analytics", "theme_series")
    )
    same_source = bool(rotation_date == datetime.now().strftime("%Y-%m-%d"))
    feedback_ok = not (feedback_error and feedback_error != "pre_auction")
    ready = bool(required_ok and same_source and feedback_ok)
    if ready:
        reason = ""
    elif not required_ok:
        reason = "关键实时计算步骤未全部完成"
    elif not same_source:
        reason = f"题材轮动引擎今日快照缺失(date={rotation_date})"
    elif not feedback_ok:
        reason = f"反馈矩阵报错：{feedback_error}"
    else:
        reason = "关键实时数据未完成"
    return ready, reason


def _run(label: str, args: tuple[str, ...], log_name: str) -> int:
    log = PULSE / log_name
    with log.open("a", encoding="utf-8") as out:
        out.write(f"[{datetime.now().strftime('%H:%M:%S')}] >>> {label}\n")
        return subprocess.run([sys.executable, *args], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT).returncode


def main() -> None:
    PULSE.mkdir(parents=True, exist_ok=True)
    results = {label: _run(label, args, log) for label, args, log in STEPS}
    results["theme_series"] = _publish_theme_series()
    latest = _json(PULSE / "latest.json")
    feedback = _json(CACHE / "feedback_matrix.json")
    rotation = _json(CACHE / "theme_rotation.json")
    source_ts = latest.get("ts")
    ready, reason = _compute_ready(
        results=results,
        feedback_error=feedback.get("error"),
        rotation_date=rotation.get("date"),
    )
    # 推送只认这份标记；任一步关键计算失败，保留原因而不把旧数据当作新数据推送。
    _write_ready({
        "ready": ready,
        "date": str(source_ts or "")[:10],
        "source_ts": source_ts,
        "generated_ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "steps": results,
        "reason": reason,
    })
    print(f"[realtime-data] ready={ready} source={source_ts} steps={results}")


if __name__ == "__main__":
    main()
