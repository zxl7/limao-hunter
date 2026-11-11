#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""错杀清单（2026-11-11 · 只读）

## 它回答什么

「**被拦下的票，后来涨了没？**」
判据只加不删、谁也不敢删，就是因为没人统计过被拦下的那批事后怎么样。
本模块把「09:25 没买的票」按判据归因，再看**次一交易日**的表现：

- 逐票：`next_ret_pct` = 次日收盘 / 当日收盘 − 1
- 按判据：命中 n / 次日均值 / 次日上涨占比

## ⛔ 边界

- 只统计**次日收盘**，不挑最低价、不挑最高价（⛔ 事后择优＝自欺）；
- 没有次日行情（今天决策、明天还没到）→ 该票 `pending=true`，不参与统计；
- 停牌/缺 K 线 → 跳过，⛔ 不当 0。

产物：`cache/missed_list_<date>.json`
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .rules_fired import attribute, load_views, registry_names

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
KLINE_DIR = CACHE / "kline_qfq"

SCHEMA = "missed_list_v1"


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _kline(code: str, kline_dir: Path | None = None) -> tuple[dict[str, int], list[float]] | None:
    p = (kline_dir or KLINE_DIR) / f"{code}.json"
    try:
        bars = [b for b in (json.loads(p.read_text(encoding="utf-8")).get("bars") or [])
                if b.get("close") is not None]
    except Exception:  # noqa: BLE001
        return None
    if not bars:
        return None
    idx = {str(b.get("date"))[:10]: i for i, b in enumerate(bars)}
    return idx, [float(b["close"]) for b in bars]


def next_ret(code: str, day: str, kline_dir: Path | None = None) -> float | None:
    k = _kline(code, kline_dir)
    if not k:
        return None
    idx, closes = k
    i = idx.get(day)
    if i is None or i + 1 >= len(closes) or closes[i] <= 0:
        return None
    return round((closes[i + 1] / closes[i] - 1) * 100, 2)


def build(day: str, *, names: Mapping[str, str] | None = None,
          kline_dir: Path | None = None,
          wc_cache: Path | None = None) -> dict[str, Any] | None:
    views = load_views(day, wc_cache) if wc_cache else load_views(day)
    if views is None:
        return None
    names = names or registry_names()
    rows: list[dict[str, Any]] = []
    for code, v in views.items():
        if not isinstance(v, dict) or str(v.get("view")) == "participate":
            continue
        r = next_ret(code, day, kline_dir)
        rows.append({
            "code": code,
            "name": str(v.get("name") or ""),
            "view": v.get("view"),
            "reason": str(v.get("reason") or "")[:120],
            "rules": attribute(v),
            "next_ret_pct": r,
            "pending": r is None,
        })

    by_rule: dict[str, list[float]] = {}
    for row in rows:
        if row["pending"]:
            continue
        for rid in row["rules"]:
            by_rule.setdefault(rid, []).append(row["next_ret_pct"])

    summary = []
    for rid, vals in by_rule.items():
        n = len(vals)
        summary.append({
            "id": rid, "name": names.get(rid, rid), "n": n,
            "avg_pct": round(sum(vals) / n, 2),
            "win_pct": round(100 * sum(1 for x in vals if x > 0) / n, 1),
        })
    summary.sort(key=lambda x: -x["avg_pct"])

    return {
        "schema": SCHEMA,
        "date": day,
        "generated_at": _bj_now().isoformat(timespec="seconds"),
        "total_blocked": len(rows),
        "settled": sum(1 for r in rows if not r["pending"]),
        "note": "只统计次日收盘；pending 表示次日行情还没落盘",
        "by_rule": summary,
        "rows": sorted(rows, key=lambda r: (r["pending"], -(r["next_ret_pct"] or 0))),
    }
