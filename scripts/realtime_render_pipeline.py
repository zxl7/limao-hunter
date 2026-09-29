#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""展示兼容层：只从已发布快照生成旧 HTML；React 终端直接通过只读 API 渲染。"""
from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PULSE = ROOT / "cache" / "pulse_snapshots"


def main() -> None:
    try:
        ready = json.loads((PULSE / "data_ready.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        ready = {}
    if not ready.get("ready"):
        print("[realtime-render] skip: 数据层未发布可渲染快照")
        return
    for label, args, log_name in (
        ("gen_dashboard", ("HTML/gen_dashboard.py",), "dash.log"),
        ("gen_watch", ("HTML/gen_watch.py",), "watch.log"),
    ):
        with (PULSE / log_name).open("a", encoding="utf-8") as out:
            out.write(f"[render] {label}\n")
            subprocess.run([sys.executable, *args], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)


if __name__ == "__main__":
    main()
