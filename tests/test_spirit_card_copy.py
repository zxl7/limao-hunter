#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短线精灵卡面文案（2026-11-13 用户「最强 中稀有色 −8.19% 感觉不对」）。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import short_spirit as sp  # noqa: E402


def _text(card) -> str:
    parts = []
    for el in card.get("elements", []):
        c = ((el.get("text") or {}).get("content")) if isinstance(el, dict) else None
        if c:
            parts.append(c)
    return "\n".join(parts)


def _sig(t, n, c, pct, sec="小金属"):
    return {"type": t, "name": n, "code": c, "pct": pct, "sector": sec, "reason": t}


def test_down_only_never_says_strongest():
    card = sp.build_card([_sig("快速下跌", "中稀有色", "600259", -8.19)], "morning", datetime.now())
    txt = _text(card)
    assert "最强" not in txt and "领跌" not in txt      # 单条连摘要头都不写
    assert "中稀有色" in txt


def test_multi_down_says_leading_decline():
    card = sp.build_card([
        _sig("快速下跌", "甲", "600001", -8.19),
        _sig("快速下跌", "乙", "600002", -5.0),
    ], "morning", datetime.now())
    txt = _text(card)
    assert "最强" not in txt and "领跌" in txt


def test_mixed_says_strongest_on_up_side():
    card = sp.build_card([
        _sig("涨停", "好当家", "600467", 10.08, "大消费"),
        _sig("快速下跌", "中稀有色", "600259", -8.19),
    ], "morning", datetime.now())
    txt = _text(card)
    assert "最强" in txt and "好当家" in txt
