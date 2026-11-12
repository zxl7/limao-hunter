#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块竞价雷达（2026-11-12）：早封/晚封分层 + 四档判断。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core import theme_auction_radar as R  # noqa: E402


def _zt(code, fbt, boards=1):
    return {"code": code, "name": code, "fbt": fbt, "boards": boards}


def _q(prev, ap):
    return {"prev_close": prev, "auction_price": ap}


def test_early_vs_late_split():
    rows = [_zt("600001", "093000"), _zt("600002", "093500"),
            _zt("600003", "134000"), _zt("600004", "140000")]
    quotes = {"600001": _q(10, 11), "600002": _q(10, 10.9),
              "600003": _q(10, 10.01), "600004": _q(10, 10.0)}
    out = R.build(rows, quotes, lambda c, r: "测试线")
    assert len(out) == 1
    it = out[0]
    assert it["early"]["n"] == 2 and it["late"]["n"] == 2
    assert it["early"]["median"] > it["late"]["median"]
    assert it["level"] == "early_only"


def test_broad_when_both_groups_strong():
    rows = [_zt("600001", "093000"), _zt("600002", "093500"),
            _zt("600003", "134000"), _zt("600004", "140000")]
    quotes = {c: _q(10, 10.6) for c in ("600001", "600002", "600003", "600004")}
    out = R.build(rows, quotes, lambda c, r: "全强线")
    assert out[0]["level"] == "broad"


def test_missing_quotes_skip():
    rows = [_zt("600001", "093000")]
    assert R.build(rows, {}, lambda c, r: "线") == []
