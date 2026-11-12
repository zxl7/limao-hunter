#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价推票 → 持有 → 断板退出（2026-11-12 用户规则）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import streak_hold as sh  # noqa: E402


def _rows():
    return [
        {"code": "A", "name": "甲", "boards": 1, "pct": 59.3, "gap": 8.0, "score": 80},
        {"code": "B", "name": "乙", "boards": 2, "pct": 64.5, "gap": 9.0},
        {"code": "C", "name": "丙", "boards": 1, "pct": 21.9, "gap": 4.0},   # <40，不入选
        {"code": "D", "name": "丁", "boards": 2, "pct": 50.0, "gap": 6.0},
    ]


def test_rank_filters_and_orders_by_pct():
    ranked = sh.rank_candidates(_rows())
    assert [r["code"] for r in ranked] == ["B", "A", "D"]      # 64.5 > 59.3 > 50.0
    assert [r["priority"] for r in ranked] == ["P1", "P2", "P3"]


def test_open_only_top_n_and_no_duplicate():
    q = {c: {"auction_price": 10.0} for c in "ABD"}
    h, opened = sh.open_positions({"open": [], "closed": []}, _rows(), q, "2026-11-13")
    assert [o["code"] for o in opened] == ["B", "A"]           # 只前 2
    h2, opened2 = sh.open_positions(h, _rows(), q, "2026-11-13")
    assert opened2 == []                                       # 已满/不重复


def test_settle_keeps_if_in_zt_and_exits_on_break():
    h = {"open": [
        {"code": "B", "name": "乙", "entry_price": 10.0, "entry_pct": 64.5, "priority": "P1", "status": "持有中"},
        {"code": "A", "name": "甲", "entry_price": 10.0, "entry_pct": 59.3, "priority": "P2", "status": "持有中"},
    ], "closed": []}
    # 收盘只发信号（T+1：当日不能卖）
    h, signaled = sh.settle(h, [{"code": "B", "boards": 3}], "2026-11-14")
    assert [x["code"] for x in signaled] == ["A"]
    assert signaled[0]["status"] == "待退出（T+1）"
    assert h["open"][0]["code"] == "B" and h["open"][0]["boards_now"] == 3
    # 同日不可执行
    h, done = sh.execute(h, "2026-11-14", {"A": 9.0})
    assert done == []
    # 下一交易日开盘卖出
    h, done = sh.execute(h, "2026-11-17", {"A": 9.0})
    assert [x["code"] for x in done] == ["A"]
    assert done[0]["exit_reason"].startswith("断板")
    assert done[0]["ret_pct"] == -10.0


def test_report_has_five_fields():
    h = {"open": [{"code": "B", "name": "乙", "entry_pct": 64.5, "priority": "P1", "status": "持有中"}],
         "closed": [{"code": "A", "name": "甲", "entry_pct": 59.3, "priority": "P2",
                     "exit_hint": "断板", "exit_date": "2026-11-14"}]}
    rows = sh.report(h)
    assert set(rows[0]) == {"代码", "名称", "晋级率", "推荐优先级", "持有状态", "断板退出提示"}
    assert rows[1]["持有状态"] == "已退出"
