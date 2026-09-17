#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价验证卡「今日竞价动态结论」回归锁（2026-09-17 用户「不要写死板·竞价时点动态判断」）。

背景：AI 盘后 view 是盘后一次性判定（无法预知次日竞价强弱）→ 用户要求「观察要有依据」：
· 明显不及预期 → 归「放弃」（不再无脑挂观察）
· 带量高开转强且超 5% 上限 → 「继续观察」（不追）
· 转强且 ≤5% → 「可参与」
· 无竞价数据 → 不加行（优雅降级）

⛔ 只影响卡面呈现，不改账本/成绩口径（AI 决策时序铁律：决策须竞价前定死）。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code" / "pipelines"))
sys.path.insert(0, str(BASE / "wealth_code"))

from build_cards import _ai_dyn_verdict, _ai_view_line  # noqa: E402


def _mk(code, gap, rng, status="pending"):
    return {
        "code": code,
        "name": "测试票",
        "expectation": {"expected_range": rng},
        "performance": {"open_check": {"gap_pct": gap, "decision_status": status,
                                       "auction_amount_yi": 5.0}},
    }


def test_below_expectation_gives_up():
    """竞价低于预期下沿 → 放弃（绿·不再挂观察）。"""
    d = _ai_dyn_verdict(_mk("000993", -9.96, [0.0, 2.0], status="reject"))
    assert d is not None
    txt, color = d
    assert "不及预期" in txt and "放弃" in txt
    assert color == "green"
    assert "-9.96%" in txt


def test_super_gap_always_keeps_watch():
    """超预期＝高开太多 → 一律「继续观察」，不分是否超 5%、⛔ 不给「可参与」（2026-09-17 用户）。"""
    for code, gap, rng in (("002584", 7.33, [1.4, 3.4]), ("000001", 4.2, [1.0, 3.0])):
        d = _ai_dyn_verdict(_mk(code, gap, rng))
        assert d is not None, code
        txt, color = d
        assert "继续观察" in txt, (code, txt)
        assert "可参与" not in txt, (code, txt)
        assert color == "yellow"


def test_in_range_is_as_expected():
    """落在预期区间 → 符合预期（红）。"""
    d = _ai_dyn_verdict(_mk("000002", 2.0, [1.0, 3.0]))
    assert d is not None
    txt, color = d
    assert "符合预期" in txt and color == "red"


def test_no_quote_degrades_gracefully():
    """无竞价数据 → None（不加行·不误伤）。"""
    r = {"code": "000003", "name": "X", "expectation": {"expected_range": [1.0, 2.0]},
         "performance": {"open_check": {}}}
    assert _ai_dyn_verdict(r) is None


def test_no_expectation_degrades():
    """无预期区间 → None（旧数据向后兼容）。"""
    r = {"code": "000004", "name": "Y", "expectation": {},
         "performance": {"open_check": {"gap_pct": 3.0}}}
    assert _ai_dyn_verdict(r) is None


def test_ai_view_line_carries_dynamic_verdict():
    """AI 判断行 = 盘后静态 view + 今日动态结论（同一行呈现转变）。"""
    it = _mk("000993", -9.96, [0.0, 2.0], status="reject")
    av = {"000993": {"view": "watch", "reason": "全场最高板（6板）"}}
    line = _ai_view_line(it, av)
    assert "🤖 AI 判断：观察" in line
    assert "放弃" in line            # 动态结论覆盖静态观感
    assert "-9.96%" in line


def test_ai_view_line_no_ai_view_returns_empty():
    """无 AI 观点 → 空串（不占位·原行为不变）。"""
    it = _mk("000005", 1.0, [0.0, 2.0])
    assert _ai_view_line(it, {}) == ""
