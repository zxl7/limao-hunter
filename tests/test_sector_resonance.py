#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材共振共享口径（core/sector_resonance.py）单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import sector_resonance as sr  # noqa: E402

def key(row):
    return str(row.get("line") or "")

QUOTES = {
    "000001": {"prev_close": 10.0, "auction_price": 11.0},   # +10% 一字
    "000002": {"prev_close": 10.0, "auction_price": 10.5},   # +5% 红开
    "000003": {"prev_close": 10.0, "auction_price": 9.5},    # -5%
    "000004": {"prev_close": 10.0, "auction_price": 10.2},   # +2%
}
ROWS = [
    {"code": "000001", "line": "传媒", "boards": 1},
    {"code": "000002", "line": "传媒", "boards": 2},
    {"code": "000003", "line": "传媒", "boards": 1},
    {"code": "000004", "line": "光伏", "boards": 3},
]


def test_three_signals_full_score():
    info, strongest = sr.compute(ROWS, QUOTES, key)
    assert info["传媒"]["score"] == 3          # 一字 + 老龙红开 + 3 家
    assert info["传媒"]["max_gap"] == 10.0
    assert strongest == "传媒"


def test_weak_line_scores_low():
    info, _ = sr.compute(ROWS, QUOTES, key)
    assert info["光伏"]["score"] == 1          # 只有 1 家，只够「老龙红开」一分


def test_missing_quote_is_unknown_not_zero():
    info, _ = sr.compute([{"code": "000009", "line": "孤军", "boards": 2}], {}, key)
    assert info["孤军"]["max_gap"] == 0.0
    assert info["孤军"]["leader_red"] == 0


def test_is_strong_threshold():
    assert sr.is_strong({"score": 2}) is True
    assert sr.is_strong({"score": 1}) is False
    assert sr.is_strong(None) is False
