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


def _pct_index(day_set: set[str]) -> dict[str, dict[str, float]]:
    """一次扫描 kline → {date: {code: pct}}（只保留需要的日期，降低 IO）。"""
    out: dict[str, dict[str, float]] = {d: {} for d in day_set}
    for f in (ROOT / "cache" / "kline_qfq").glob("*.json"):
        try:
            bars = json.loads(f.read_text(encoding="utf-8"))["bars"]
        except Exception:  # noqa: BLE001
            continue
        prev = None
        for b in bars:
            d = str(b.get("date"))[:10]
            if d in day_set and prev:
                try:
                    out[d][f.stem] = round((float(b["close"]) / float(prev) - 1) * 100, 2)
                except Exception:  # noqa: BLE001
                    pass
            prev = b.get("close") or prev
    return out


def build(days: int = 20) -> dict:
    all_days = _zt_days()[-days:]
    s5 = _load(ROOT / "cache" / "short_term" / "summary_5d.json", {})
    by_day = {d.get("date"): d for d in (s5.get("days") or []) if d.get("date")}
    pulse = _load(ROOT / "cache" / "pulse_snapshots" / "latest.json", {})
    fb = _load(ROOT / "cache" / "feedback_matrix.json", {})
    latest = all_days[-1] if all_days else None
    pct_idx = _pct_index(set(all_days))
    rows = []
    for i, d in enumerate(all_days):
        prev_day = all_days[i - 1] if i > 0 else None
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
        # 📊 事实指标：从 zt_history + kline 回算（summary_5d 缺的历史日靠这个补齐）
        if prev_day:
            prev_codes = [str(r.get("code")) for r in
                          (_load(ROOT / "cache" / "zt_history" / f"zt_{prev_day}.json", {}).get("zt_list") or [])]
            today_codes = {str(r.get("code")) for r in zt_list}
            vals = [pct_idx.get(d, {}).get(c) for c in prev_codes]
            vals = [v for v in vals if v is not None]
            if prev_codes and vals:
                row["promote_rate"] = row["promote_rate"] if row["promote_rate"] is not None else round(
                    100 * sum(1 for c in prev_codes if c in today_codes) / len(prev_codes), 1)
                row["avg_pct"] = round(sum(vals) / len(vals), 2)
                row["up_rate"] = round(100 * sum(1 for v in vals if v > 0) / len(vals), 1)
                row["nuke"] = row["nuke"] if row["nuke"] is not None else sum(
                    1 for c, v in zip(prev_codes, vals) if v <= (-19.9 if c.startswith(("30", "68")) else -9.9))
        if d == latest:
            row["dt"] = (pulse.get("limit_down") or {}).get("count")
            if row["promote_rate"] is None and isinstance(fb.get("promote_rate"), (int, float)):
                row["promote_rate"] = fb.get("promote_rate")
            if row["nuke"] is None and isinstance(fb.get("nuke_count"), (int, float)):
                row["nuke"] = fb.get("nuke_count")
        rows.append(row)
    return {"days": len(rows), "rows": rows, "sources": {"summary_5d": bool(by_day), "pulse": bool(pulse)}}
