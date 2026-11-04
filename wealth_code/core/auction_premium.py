#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""昨日连板 · 今晨竞价溢价榜（2026-11-04 用户）。

## 用户原话

> 「今日的竞价能看到昨天连板的那些票开出来是什么样的情况，也可以结合热度看一下，
>   这些热度高的票开出来是什么样的情况。**大于 0 至少是有溢价的，那就非常不错；
>   如果没有溢价，那就要谨慎。**」

## 这个榜回答什么

连板票的**次日竞价溢价**是这条线当天还认不认的直接读数：

| 竞价涨幅 | 含义 | 处置 |
|---|---|---|
| `> 0` | 有人愿意溢价接昨天的获利盘 | 这条线还活着，可以往下看 |
| `= 0` | 平开，承接意愿不明 | 等 09:30 之后的量能 |
| `< 0` | **折价走**，昨天买的人先跑 | **谨慎**，别在低开里找理由 |

⛔ 首板不进这个榜：首板次日竞价没有「获利盘兑现」这层压力，
   溢价高低说明不了连板情绪（用户：「第一板是看不出什么东西的」）。
⛔ 只做**读数**，不产出买卖建议 —— 是否参与仍由 09:25 池内裁决决定。

## 热度怎么算

`theme_hot` = 该题材**昨日**涨停家数（来自昨日完整涨停池，与选股同一份题材口径）。
⛔ 不另抓实时热度榜：项目没有可靠数据源（试探接口均 404），
   且「昨日题材内涨停家数」本身就是这条线最强度的客观证据。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SCHEMA = "auction_premium_v1"

# 入榜最低板数：2 板（1进2 的首板不算「连板」）
MIN_BOARDS = 2

VERDICT_PREMIUM = "premium"    # gap > 0：有溢价
VERDICT_FLAT = "flat"          # gap == 0：平开
VERDICT_DISCOUNT = "discount"  # gap < 0：折价·谨慎


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _boards_of(row: Mapping[str, Any]) -> int:
    b = _num(row.get("boards") if row.get("boards") is not None else row.get("lbc"))
    return int(b) if b is not None else 0


def _theme_of(row: Mapping[str, Any]) -> str:
    for k in ("core_theme", "theme", "sector", "hy"):
        v = str(row.get(k) or "").strip()
        if v:
            return v
    return ""


def _gap_pct(quote: Mapping[str, Any] | None) -> float | None:
    """竞价涨幅（%）。⛔ 缺 prev_close / auction_price 一律 None，不拿 0 冒充。"""
    if not isinstance(quote, Mapping):
        return None
    prev = _num(quote.get("prev_close"))
    price = _num(quote.get("auction_price"))
    if prev is None or price is None or prev <= 0:
        return None
    return (price / prev - 1) * 100


def build_premium_board(
    zt_rows: Sequence[Mapping[str, Any]] | None,
    quotes: Mapping[str, Mapping[str, Any]] | None,
    *,
    prev_date: str = "",
    trade_date: str = "",
    min_boards: int = MIN_BOARDS,
) -> dict[str, Any]:
    """构造昨日连板今晨竞价溢价榜。

    `zt_rows`   = **昨日**完整涨停池（`_load_full_zt_rows(今日)` 的产物）
    `quotes`    = 今晨 09:25 竞价报价（`{code: {prev_close, auction_price, auction_amount_yi}}`）
    """
    rows = [r for r in (zt_rows or []) if isinstance(r, Mapping)]
    quotes = quotes or {}

    # 题材热度 = 该题材昨日涨停家数（全池统计，含首板 —— 线宽本身就是强度）
    theme_all: dict[str, int] = {}
    for r in rows:
        th = _theme_of(r)
        if th:
            theme_all[th] = theme_all.get(th, 0) + 1

    board_rows: list[dict[str, Any]] = []
    no_quote: list[str] = []
    for r in rows:
        b = _boards_of(r)
        if b < min_boards:
            continue
        code = str(r.get("code") or r.get("dm") or "")[:6]
        if not code:
            continue
        q = quotes.get(code)
        gap = _gap_pct(q)
        if gap is None:
            no_quote.append(str(r.get("name") or code))
            continue
        th = _theme_of(r)
        amount = _num((q or {}).get("auction_amount_yi"))
        board_rows.append({
            "code": code,
            "name": str(r.get("name") or r.get("mc") or code),
            "boards": b,
            "theme": th,
            "theme_hot": theme_all.get(th, 0),
            # 与昨日成交额比：判断这个溢价是「有人真金买」还是「几手撮合」
            "amount_yi": round(amount, 2) if amount is not None else None,
            "gap_pct": round(gap, 2),
            "verdict": (VERDICT_PREMIUM if gap > 0
                        else VERDICT_DISCOUNT if gap < 0
                        else VERDICT_FLAT),
        })

    # 排序：板数高者先（身位是主变量），同板数按竞价溢价高者先
    board_rows.sort(key=lambda x: (-x["boards"], -x["gap_pct"]))

    gaps = [r["gap_pct"] for r in board_rows]
    premium = sum(1 for g in gaps if g > 0)
    flat = sum(1 for g in gaps if g == 0)
    discount = sum(1 for g in gaps if g < 0)

    def _avg(v: list[float]) -> float | None:
        return round(sum(v) / len(v), 2) if v else None

    def _median(v: list[float]) -> float | None:
        if not v:
            return None
        s = sorted(v)
        m = len(s) // 2
        return round((s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2), 2)

    # 按板数分档统计：3板+ 与 2板 的溢价往往不同（高位更能看出接不接）
    by_boards: dict[str, dict[str, Any]] = {}
    for b in sorted({r["boards"] for r in board_rows}, reverse=True):
        sub = [r for r in board_rows if r["boards"] == b]
        sg = [r["gap_pct"] for r in sub]
        by_boards[str(b)] = {
            "count": len(sub),
            "premium_count": sum(1 for g in sg if g > 0),
            "avg_gap": _avg(sg),
            "median_gap": _median(sg),
            "names": [r["name"] for r in sub],
        }

    return {
        "schema": SCHEMA,
        "prev_date": prev_date,
        "trade_date": trade_date,
        "min_boards": min_boards,
        "count": len(board_rows),
        "premium_count": premium,
        "flat_count": flat,
        "discount_count": discount,
        "premium_ratio": round(premium / len(board_rows), 4) if board_rows else None,
        "avg_gap": _avg(gaps),
        "median_gap": _median(gaps),
        "by_boards": by_boards,
        "rows": board_rows,
        # ⛔ 报价缺失单独列出，不当「平开」：拿不到数据 ≠ 没有溢价
        "no_quote": no_quote,
    }
