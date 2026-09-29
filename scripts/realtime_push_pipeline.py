#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时推送层：仅消费已发布的数据就绪标记，绝不自行抓取或重算行情。"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PULSE = ROOT / "cache" / "pulse_snapshots"
READY = PULSE / "data_ready.json"
MAX_AGE_SECONDS = 120

STEPS = (
    ("live_signal", ("scripts/live_signal.py",), "signal.log"),
    ("newsflash_express", ("scripts/newsflash_express.py",), "express.log"),
    ("intraday_beat", ("scripts/intraday_beat.py",), "beat.log"),
)


def _ready() -> tuple[bool, str]:
    try:
        marker = json.loads(READY.read_text(encoding="utf-8"))
        source = datetime.fromisoformat(str(marker.get("source_ts") or ""))
    except (OSError, ValueError, TypeError):
        return False, "缺少或无法解析数据就绪标记"
    age = (datetime.now(timezone.utc) - source.astimezone(timezone.utc)).total_seconds()
    if not marker.get("ready"):
        return False, str(marker.get("reason") or "数据层未就绪")
    if age < 0 or age > MAX_AGE_SECONDS:
        return False, f"行情快照过期 {age:.0f}s"
    return True, str(marker.get("source_ts"))


def _run(label: str, args: tuple[str, ...], log_name: str) -> int:
    with (PULSE / log_name).open("a", encoding="utf-8") as out:
        out.write(f"[{datetime.now().strftime('%H:%M:%S')}] >>> {label}\n")
        return subprocess.run([sys.executable, *args], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT).returncode


def main() -> None:
    ok, detail = _ready()
    if not ok:
        print(f"[realtime-push] skip: {detail}")
        return
    results = {label: _run(label, args, log) for label, args, log in STEPS}
    print(f"[realtime-push] source={detail} steps={results}")


if __name__ == "__main__":
    main()
