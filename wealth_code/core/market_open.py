#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""昨日完整涨停池的 09:25 竞价反馈。

用途：昨天的接力环境先决定常规名额；今天只用冻结在 09:25 的整批昨涨停股竞价，
检查资金是否出现集体回暖。它不能把弱市直接翻成进攻，只能在原本空仓时放宽一个
池内名额，且只服务 ``core.pool_call`` 的当日冻结裁决。
"""
from __future__ import annotations

from statistics import median
from typing import Any

# 可逆开关与保守阈值。完整样本不足时一律不放宽。
MARKET_OPEN_GATE_ENABLED = True
MIN_COVERAGE = 0.70
MIN_FULL_RED_RATIO = 0.55
MIN_LEADER_RED_RATIO = 0.60
MIN_LEADER_VOLUME_RATIO = 0.010
MIN_LEADER_VOLUME_PASS_RATIO = 0.60
MIN_LEADERS = 3


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gap(q: dict[str, Any] | None) -> float | None:
    if not isinstance(q, dict):
        return None
    p, prev = _num(q.get("auction_price")), _num(q.get("prev_close"))
    if p is None or prev is None or p <= 0 or prev <= 0:
        return None
    return round((p / prev - 1) * 100, 2)


def build_market_open(rows: list[dict[str, Any]] | None,
                      quotes: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """按完整昨涨停池计算 09:25 回暖读数，不做任何买卖裁决。"""
    pool = [r for r in (rows or []) if isinstance(r, dict) and len(str(r.get("code") or "")) == 6]
    by_code = {str(r["code"]): r for r in pool}
    valid: list[tuple[dict[str, Any], float, float | None]] = []
    for code, row in by_code.items():
        q = (quotes or {}).get(code)
        gap = _gap(q)
        if gap is None:
            continue
        amount = _num((q or {}).get("auction_amount_yi"))
        prior = _num(row.get("cje_yi"))
        ratio = amount / prior if amount is not None and prior is not None and prior > 0 else None
        valid.append((row, gap, ratio))

    leaders = [x for x in valid if int(_num(x[0].get("boards")) or 0) >= 2]
    red = sum(1 for _, gap, _ in valid if gap > 0)
    leader_red = sum(1 for _, gap, _ in leaders if gap > 0)
    leader_ratios = [ratio for _, _, ratio in leaders if ratio is not None]
    coverage = len(valid) / len(pool) if pool else 0.0
    full_red_ratio = red / len(valid) if valid else 0.0
    leader_red_ratio = leader_red / len(leaders) if leaders else 0.0
    leader_volume_median = median(leader_ratios) if leader_ratios else None
    leader_volume_pass_ratio = (
        sum(1 for ratio in leader_ratios if ratio >= MIN_LEADER_VOLUME_RATIO) / len(leader_ratios)
        if leader_ratios else 0.0
    )
    ready = bool(
        MARKET_OPEN_GATE_ENABLED
        and coverage >= MIN_COVERAGE
        and len(leaders) >= MIN_LEADERS
        and full_red_ratio >= MIN_FULL_RED_RATIO
        and leader_red_ratio >= MIN_LEADER_RED_RATIO
        and leader_volume_median is not None
        and leader_volume_median >= MIN_LEADER_VOLUME_RATIO
        and leader_volume_pass_ratio >= MIN_LEADER_VOLUME_PASS_RATIO
    )
    return {
        "enabled": MARKET_OPEN_GATE_ENABLED,
        "pool_size": len(pool),
        "quoted": len(valid),
        "coverage": round(coverage, 4),
        "red_open_count": red,
        "red_open_ratio": round(full_red_ratio, 4),
        "leader_count": len(leaders),
        "leader_red_open_count": leader_red,
        "leader_red_open_ratio": round(leader_red_ratio, 4),
        "leader_volume_median_ratio": round(leader_volume_median, 5) if leader_volume_median is not None else None,
        "leader_volume_pass_ratio": round(leader_volume_pass_ratio, 4),
        "ready": ready,
        "reason": (
            "昨涨停整体红开、连板红开和竞价量都达标，允许空仓日只试 1 只池内最强票"
            if ready else "昨涨停竞价没有同时达到红开与量能要求，维持原有名额"
        ),
    }
