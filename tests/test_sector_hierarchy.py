#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块结构分层（地位理论 · 2026-11-12）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import sector_hierarchy as sh  # noqa: E402


def _rows():
    return [
        {"code": "A", "name": "龙一", "boards": 5, "fbt": "093000"},
        {"code": "B", "name": "补涨", "boards": 3, "fbt": "094000"},
        {"code": "C", "name": "小弟", "boards": 1, "fbt": "143000"},
        {"code": "D", "name": "孤军", "boards": 2, "fbt": "093000"},
        {"code": "E", "name": "他线", "boards": 1, "fbt": "100000"},
    ]


def _theme(r):
    return {"A": "主线", "B": "主线", "C": "主线", "D": "孤线", "E": "其他"}.get(r["code"], "—")


def test_roles():
    res = sh.classify(_rows(), _theme)
    assert res["A"]["role"] == "阶段龙"        # 线只有 3 家，不够总龙门槛（≥5）
    assert res["B"]["role"] in ("阶段龙", "补涨龙")
    assert res["C"]["role"] == "跟风"
    assert res["D"]["role"] == "未知"          # 孤军不判


def test_overall_dragon_needs_big_line():
    rows = [{"code": "A", "name": "龙一", "boards": 5, "fbt": "093000"}] + [
        {"code": f"X{i}", "name": f"小弟{i}", "boards": 1, "fbt": "140000"} for i in range(5)]
    res = sh.classify(rows, lambda r: "主线")
    assert res["A"]["role"] == "总龙"


def test_bonus_order():
    assert sh.rank_bonus("总龙") > sh.rank_bonus("阶段龙") > sh.rank_bonus("补涨龙") > sh.rank_bonus("跟风")
