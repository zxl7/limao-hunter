#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🥈 第二波高位节点（2026-11-12 用户口径）

用户原话：「像光洋这种老票，3 板是一个节点…这个位置只观察不要买」
「我说的应该是最近一个月第一波已经走完了，然后第二波三板危险，跟着近一年跨度太大了。」

判据（全部次日决策前可知，静态可回测）：
  · 当前连续涨停 ≥3 天；
  · 这波之前 `window`（默认 20 = 近一个月）个交易日内已有 ≥3 次涨停（第一波走完）。

⛔ 只看「第二波」，不看「近一年涨停多少次」——跨度太大会把第一波也误杀。
⛔ 只决定「身份/展示」，买不买仍由 09:25 各闸判。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KLINE = ROOT / "cache" / "kline_qfq"

MIN_BOARDS = 3
PRIOR_ZT = 3
WINDOW = 20


def prior_wave_zt(code: str, window: int = WINDOW) -> tuple[int, int] | None:
    """返回 `(当前连续涨停天数, 这波之前 window 日内的涨停次数)`；缺 K 线 → None。

    ⛔ 涨停判定用收盘涨幅（10cm ≥9.8% / 20cm ≥19.7%），与账本口径一致。
    """
    try:
        bars = json.loads((KLINE / f"{code}.json").read_text(encoding="utf-8")).get("bars") or []
    except Exception:  # noqa: BLE001
        return None
    bars = [b for b in bars if b.get("close") and b.get("date")]
    if len(bars) < 3:
        return None
    flags: list[bool] = []
    for i in range(1, len(bars)):
        try:
            r = float(bars[i]["close"]) / float(bars[i - 1]["close"]) - 1
        except (TypeError, ValueError, ZeroDivisionError):
            flags.append(False)
            continue
        flags.append(r >= 0.098 or r >= 0.197)
    streak = 0
    for ok in reversed(flags):
        if ok:
            streak += 1
        else:
            break
    idx = len(flags) - streak
    prior = sum(1 for x in flags[max(0, idx - window):idx] if x)
    return streak, prior


def second_wave(cur_boards: int, code: str, *, window: int = WINDOW,
                min_boards: int = MIN_BOARDS, prior_zt: int = PRIOR_ZT) -> dict | None:
    """命中第二波高位节点 → 返回说明 dict；否则 None。"""
    try:
        b = int(cur_boards)
    except (TypeError, ValueError):
        return None
    if b < min_boards:
        return None
    pw = prior_wave_zt(code, window)
    if pw is None:
        return None
    streak, prior = pw
    if streak < min_boards or prior < prior_zt:
        return None
    return {"streak": streak, "prior": prior, "window": window,
            "note": (f"第二波高位节点：近 {window} 日已有 {prior} 次涨停（第一波走完），"
                     f"现在第 {streak} 板 → 只观察，不买")}
