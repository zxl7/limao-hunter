#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""晋级概率（一进二/二进三）· 竞价口径。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import promotion_odds as po  # noqa: E402


def test_gap_band():
    assert po.gap_band(-1) == "<0"
    assert po.gap_band(2.9) == "0-3"
    assert po.gap_band(4) == "3-5"
    assert po.gap_band(6) == "5-7"
    assert po.gap_band(8) == ">=7"
    assert po.gap_band(None) == "na"


def test_monotonic_in_gap():
    """竞价越高，晋级概率越高（历史单调）。"""
    seq = [po.odds(1, g)["pct"] for g in (-2, 1, 4, 6, 9)]
    assert seq == sorted(seq) and seq[-1] >= 50
    seq2 = [po.odds(2, g)["pct"] for g in (-2, 1, 4, 6, 9)]
    assert seq2 == sorted(seq2)


def test_likely_threshold():
    assert po.odds(1, 9.0)["likely"] is True     # >=7 档 59.3%
    assert po.odds(1, -1.0)["likely"] is False   # <0 档 3.1%


def test_no_quotes_is_not_likely():
    r = po.odds(1, None)
    assert r["likely"] is False and r["band"] == "na"
