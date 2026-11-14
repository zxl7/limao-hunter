#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""📈 复盘多日序列（2026-11-14 用户「连续几天的复盘做成折线图」）

把散在多处的日频数据汇成一条时间轴，供终端画图：
- `zt_history/zt_<date>.json`：涨停家数、最高板（全量历史，40 日）
- `cache/short_term/summary_5d.json`：情绪分 / 晋级率 / 核按钮（近 5 日权威）
- `cache/pulse_snapshots/latest.json`：跌停家数（仅当日）
- `cache/feedback_matrix.json`：当日晋级率/炸板率兜底

输出结构：
```json
{"days": 20, "rows": [{"date","zt","dt","max_boards","sentiment","promote_rate","nuke"}]}
```
⛔ 只读汇总。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def _zt_days() -> list[str]:
    return sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))


def build(days: int = 20) -> dict:
    all_days = _zt_days()[-days:]
    s5 = _load(ROOT / "cache" / "short_term" / "summary_5d.json", {})
    by_day = {d.get("date"): d for d in (s5.get("days") or []) if d.get("date")}
    pulse = _load(ROOT / "cache" / "pulse_snapshots" / "latest.json", {})
    fb = _load(ROOT / "cache" / "feedback_matrix.json", {})
    latest = all_days[-1] if all_days else None
    rows = []
    for d in all_days:
        zt_list = _load(ROOT / "cache" / "zt_history" / f"zt_{d}.json", {}).get("zt_list") or []
        s = by_day.get(d) or {}
        row = {
            "date": d,
            "zt": len(zt_list) or None,
            "max_boards": max((int(r.get("boards") or 0) for r in zt_list), default=0) or None,
            "sentiment": s.get("sentiment_score"),
            "promote_rate": s.get("promote_rate"),
            "nuke": s.get("nuke_count"),
            "dt": None,
        }
        if d == latest:
            row["dt"] = (pulse.get("limit_down") or {}).get("count")
            if row["promote_rate"] is None and isinstance(fb.get("promote_rate"), (int, float)):
                row["promote_rate"] = fb.get("promote_rate")
            if row["nuke"] is None and isinstance(fb.get("nuke_count"), (int, float)):
                row["nuke"] = fb.get("nuke_count")
        rows.append(row)
    return {"days": len(rows), "rows": rows, "sources": {"summary_5d": bool(by_day), "pulse": bool(pulse)}}
