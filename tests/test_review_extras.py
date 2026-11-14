#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高标处境 + 明日跌停预判（2026-11-14）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import review_extras as rx  # noqa: E402


def test_high_board_picks_max():
    zt = [{"code": "A", "name": "甲", "boards": 4, "fbt": "093000", "zbc": 1},
          {"code": "B", "name": "乙", "boards": 2, "fbt": "100000", "zbc": 0}]
    hb = rx.high_board("2026-11-13", zt, {"A": 10.0})
    assert hb["max_boards"] == 4 and hb["count"] == 1
    assert hb["rows"][0]["next_odds"] == 44.2
    assert "断板" in hb["rows"][0]["key"]


def test_dump_risk_counts_prev_zt_to_limit_down():
    pulse = {"limit_down": {"count": 12, "names": ["甲", "外"]}, "broken": {"count": 30},
             "index": [{"name": "上证", "pct": -1.0}]}
    prev = [{"code": "A", "name": "甲", "boards": 4}]
    r = rx.dump_risk("2026-11-13", pulse, {"nuke_count": 11, "promote_rate": 20},
                     prev, [{"code": "B", "boards": 3}], {"A": -9.0})
    assert r["prev_zt_to_limit_down_count"] == 1
    assert r["level"] == "高" and r["verdict"]


def test_high_board_dump_upgrades_level():
    pulse = {"limit_down": {"count": 2}, "broken": {"count": 3}, "index": []}
    prev = [{"code": "A", "name": "甲", "boards": 4}, {"code": "B", "name": "乙", "boards": 3}]
    r = rx.dump_risk("2026-11-13", pulse, {}, prev, [], {"A": -8.0, "B": -6.0})
    assert any(s["name"] == "高位股集体大面" for s in r["signals"])
