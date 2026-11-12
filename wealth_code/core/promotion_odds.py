#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🥇 晋级概率（一进二 / 二进三）· 竞价口径（2026-11-12 用户）

用户原话：「一进二我想打板，二进三我想定心」「竞价这里也需要 1进2 和 2进3
大概率成功的个股预案」。

## 口径（只读，全部来自本机 40 个交易日）
- 分子：昨日 N 板的票，今日仍在涨停池
- 分母：昨日 N 板的票
- 分层：**当日竞价涨幅档**（09:25 唯一可观测变量）

## 实测（2026-09-10 ~ 11-12，40 日）

| 竞价涨幅 | 1→2 | 2→3 |
|---|---:|---:|
| <0% | 3.1% | 3.2% |
| 0~3% | 10.3% | 15.8% |
| 3~5% | 21.9% | 32.1% |
| 5~7% | 37.5% | 50.0% |
| ≥7% | **59.3%** | **64.5%** |

## 用法
`plan(quotes, boards_map)` → 每条给：竞价档、晋级概率、是否"大概率"（≥40%）。
⛔ 概率是历史条件频率，不是预测；⛔ 不改任何买卖判据。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

GAP_BANDS = ("<0", "0-3", "3-5", "5-7", ">=7")

# 兜底常量（与上方实测算术一致；运行时优先用当日缓存表）
FALLBACK = {
    1: {"<0": 3.1, "0-3": 10.3, "3-5": 21.9, "5-7": 37.5, ">=7": 59.3},
    2: {"<0": 3.2, "0-3": 15.8, "3-5": 32.1, "5-7": 50.0, ">=7": 64.5},
}
MIN_N = 8          # 单档样本 <8 → 回退到该板位总体率
LIKELY = 40.0      # ≥40% 记「大概率」


def gap_band(gap: float | None) -> str:
    if gap is None:
        return "na"
    if gap < 0:
        return "<0"
    if gap < 3:
        return "0-3"
    if gap < 5:
        return "3-5"
    if gap < 7:
        return "5-7"
    return ">=7"


def _scan() -> tuple[dict, dict]:
    """扫 40 日 zt + 竞价 → {(boards, band): (promoted, total)}。"""
    days = sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))
    rows = {d: {str(r["code"]): r for r in json.loads(
        (ROOT / "cache" / "zt_history" / f"zt_{d}.json").read_text(encoding="utf-8"))["zt_list"]}
        for d in days}
    au = {}
    for p in (ROOT / "wealth_code" / "cache").glob("auction_quotes-*.json"):
        au[p.stem.replace("auction_quotes-", "")] = json.loads(p.read_text(encoding="utf-8"))
    table: dict[tuple[int, str], list[int]] = {}
    for i in range(len(days) - 1):
        d, nd = days[i], days[i + 1]
        q = au.get(nd) or {}
        if not q:
            continue
        for code, r in rows[d].items():
            b = int(r.get("boards") or 0)
            if b not in (1, 2):
                continue
            hit = q.get(code) or {}
            ap, pc = hit.get("auction_price"), hit.get("prev_close")
            gap = round((ap / pc - 1) * 100, 2) if ap and pc else None
            k = (b, gap_band(gap))
            cell = table.setdefault(k, [0, 0])
            cell[0] += 1 if code in rows[nd] else 0
            cell[1] += 1
    return table, {b: [sum(v[0] for (bb, _), v in table.items() if bb == b),
                       sum(v[1] for (bb, _), v in table.items() if bb == b)] for b in (1, 2)}


def rates() -> dict[str, dict[str, float]]:
    """{(boards,band): pct} + 总体率；结果落 `cache/promotion_rates.json`。"""
    cache = ROOT / "cache" / "promotion_rates.json"
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    table, totals = _scan()
    out: dict[str, Any] = {"days": 40, "grid": {}, "overall": {}}
    for (b, band), (pr, n) in table.items():
        if n >= MIN_N:
            out["grid"][f"{b}|{band}"] = round(100 * pr / n, 1)
    for b, (pr, n) in totals.items():
        out["overall"][str(b)] = round(100 * pr / n, 1) if n else None
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def odds(boards: int, gap: float | None) -> dict[str, Any]:
    """单票晋级概率（boards 只支持 1/2）。"""
    if boards not in (1, 2):
        return {"pct": None, "band": "na", "likely": False, "why": "板位不在 1/2"}
    band = gap_band(gap)
    if band == "na":
        p = FALLBACK[boards]["0-3"]      # 无竞价 → 用中性档，标注未知
        return {"pct": p, "band": "na", "likely": False,
                "why": "无竞价数据 → 按中性档，不判大概率"}
    try:
        p = rates()["grid"].get(f"{boards}|{band}")
    except Exception:  # noqa: BLE001
        p = None
    if p is None:
        p = FALLBACK[boards].get(band)
    return {"pct": p, "band": band, "likely": bool(p and p >= LIKELY),
            "why": f"{boards}板 · 竞价 {band}% 档 → 历史晋级 {p}%"}


def plan(items: list[dict], quotes: dict[str, Any]) -> list[dict]:
    """items: [{code,name,boards,theme,score?}]；quotes: 09:25 竞价。"""
    out = []
    for it in items:
        code = str(it.get("code"))
        q = quotes.get(code) or {}
        ap, pc = q.get("auction_price"), q.get("prev_close")
        gap = round((ap / pc - 1) * 100, 2) if ap and pc else None
        o = odds(int(it.get("boards") or 1), gap)
        out.append({**{k: it.get(k) for k in ("code", "name", "boards", "theme", "score")},
                    "gap": gap, **o})
    out.sort(key=lambda x: -(x.get("pct") or 0))
    return out
