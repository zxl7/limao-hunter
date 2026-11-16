#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材共振（2026-11-16 用户定调：主线题材为核心逻辑，全局统一）

## 一句话口径
> 单票的「地位」决定买谁；**线的「共振」决定买不买这条线**。顺序不能倒。

## 三个信号（各 1 分，满分 3；只用 09:25 可得信息）
1. **同线出现一字/大幅高开**：该线昨日涨停股里，今天竞价最高涨幅 ≥ `strong_gap`（默认 +7%）
2. **老龙红开**：该线**昨日最高板**那只票，今天竞价 > 0
3. **成线**：该线昨日涨停家数 ≥ `min_mates`（默认 3）

## 为什么必须用「昨日完整涨停池」
首板常常就是方向本身（11-16 凯撒文化 +10.1% 一字）。
若只统计候选池（首板被"只看不买"剔除），最强的方向证据会整体丢失。

## 谁在用
- `core/pool_call.py`：强共振线（≥2）且为当日最强线 → 该线**非核心可买票**半仓参与
- `core/review_panel.py`：复盘「板块梯队」给出同一批线的家数/高度（判断依据）
- 卡片：买单理由里直接写「某某 共振 3/3·非核心半仓跟主线」
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

STRONG_GAP = 7.0
MIN_MATES = 3
STRONG_SCORE = 2


def _gap_of(q: dict[str, Any] | None) -> float | None:
    if not isinstance(q, dict):
        return None
    ap, pc = q.get("auction_price"), q.get("prev_close")
    try:
        ap, pc = float(ap), float(pc)
    except (TypeError, ValueError):
        return None
    if ap <= 0 or pc <= 0:
        return None
    return (ap / pc - 1) * 100


def compute(
    rows: Iterable[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    line_key: Callable[[dict[str, Any]], str],
    *,
    strong_gap: float = STRONG_GAP,
    min_mates: int = MIN_MATES,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """返回 ({线名: {score, gap, leader_red, mates, mates_n, max_boards}}, 最强线名)。

    `rows` = **昨日完整涨停池**（含首板）；`line_key` 负责把票归到线（族归并）。
    报价缺失的票不计入，不补 0。
    """
    mates: dict[str, int] = {}
    max_gap: dict[str, float] = {}
    max_boards: dict[str, int] = {}
    leader_gap: dict[str, float] = {}

    for r in rows or []:
        line = line_key(r)
        if not line:
            continue
        mates[line] = mates.get(line, 0) + 1
        code = str(r.get("code") or "").strip().zfill(6)
        g = _gap_of((quotes or {}).get(code))
        if g is None:
            continue
        if line not in max_gap or g > max_gap[line]:
            max_gap[line] = g
        b = int(r.get("boards") or 0)
        if b >= max_boards.get(line, 0):
            max_boards[line] = b
            leader_gap[line] = g

    out: dict[str, dict[str, Any]] = {}
    for line in set(list(mates) + list(max_gap)):
        f1 = 1 if max_gap.get(line, -99.0) >= strong_gap else 0
        f2 = 1 if leader_gap.get(line, -99.0) > 0 else 0
        f3 = 1 if mates.get(line, 0) >= min_mates else 0
        out[line] = {
            "score": f1 + f2 + f3,
            "gap": f1,
            "leader_red": f2,
            "mates": f3,
            "mates_n": mates.get(line, 0),
            "max_boards": max_boards.get(line, 0),
            "max_gap": round(max_gap.get(line, 0.0), 2),
        }

    strongest = max(mates, key=lambda k: (mates[k], out.get(k, {}).get("score", 0)), default=None)
    return out, strongest


def is_strong(info: dict[str, Any] | None) -> bool:
    return bool(info) and int(info.get("score") or 0) >= STRONG_SCORE
