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


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write_ready(value: dict) -> None:
    tmp = READY.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, READY)


def _run(label: str, args: tuple[str, ...], log_name: str) -> int:
    log = PULSE / log_name
    with log.open("a", encoding="utf-8") as out:
        out.write(f"[{datetime.now().strftime('%H:%M:%S')}] >>> {label}\n")
        return subprocess.run([sys.executable, *args], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT).returncode


def main() -> None:
    PULSE.mkdir(parents=True, exist_ok=True)
    results = {label: _run(label, args, log) for label, args, log in STEPS}
    latest = _json(PULSE / "latest.json")
    feedback = _json(CACHE / "feedback_matrix.json")
    rotation = _json(CACHE / "theme_rotation.json")
    source_ts = latest.get("ts")
    required_ok = all(results.get(label) == 0 for label in ("intraday_pulse", "feedback_matrix", "realtime_analytics"))
    same_source = bool(source_ts and rotation.get("source_ts") == source_ts)
    ready = required_ok and same_source and not feedback.get("error")
    # 推送只认这份标记；任一步关键计算失败，保留原因而不把旧数据当作新数据推送。
    _write_ready({
        "ready": ready,
        "date": str(source_ts or "")[:10],
        "source_ts": source_ts,
        "generated_ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "steps": results,
        "reason": "" if ready else "关键实时数据未完成或题材快照与行情时间戳不一致",
    })
    print(f"[realtime-data] ready={ready} source={source_ts} steps={results}")


if __name__ == "__main__":
    main()
