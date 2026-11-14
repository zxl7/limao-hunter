#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘三块面板（2026-11-14）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import review_panel as rp  # noqa: E402


def test_risk_level_scales_with_hits():
    pulse = {"index": [{"name": "上证", "pct": -1.2}],
             "limit_down": {"count": 15}, "broken": {"count": 40}}
    fb = {"nuke_count": 12, "promote_rate": 18}
    prev = [{"code": "A", "boards": 6}]
    today = [{"code": "B", "boards": 3}]
    r = rp.risk_block(pulse, fb, prev, today)
    assert r["hits"] >= 4 and r["level"] == "高"


def test_risk_low_when_calm():
    pulse = {"index": [{"name": "上证", "pct": 0.8}], "limit_down": {"count": 1}, "broken": {"count": 5}}
    r = rp.risk_block(pulse, {"nuke_count": 0, "promote_rate": 45}, [{"code": "A", "boards": 3}],
                      [{"code": "B", "boards": 4}])
    assert r["level"] == "低"


def test_money_best_worst_from_pct():
    prev = [{"code": "A", "name": "甲", "boards": 5}, {"code": "B", "name": "乙", "boards": 5},
            {"code": "C", "name": "丙", "boards": 2}]
    m = rp.money_block({}, prev, {"A": 3.1, "B": -5.0, "C": 9.9})
    assert m["relay_best"]["name"] == "甲" and m["relay_worst"]["name"] == "乙"
    assert m["overnight_best"]["name"] == "丙"
    assert m["zt_pool_avg"] == round((3.1 - 5.0 + 9.9) / 3, 2)


def test_build_smoke():
    d = rp.build("2026-11-13")
    assert d["date"] == "2026-11-13"
    assert set(d) >= {"risk", "money", "themes"}
    assert isinstance(d["themes"], list)
