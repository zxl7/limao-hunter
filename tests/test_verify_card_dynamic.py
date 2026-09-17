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


def test_above_5pct_keeps_watch():
    """高开太多（>5% 硬线）→「继续观察」·⛔ 不给「可参与」（A2：判据＝绝对线）。"""
    for code, gap, rng in (("002584", 7.33, [1.4, 3.4]), ("003026", 9.99, [0.9, 2.9])):
        d = _ai_dyn_verdict(_mk(code, gap, rng))
        assert d is not None, code
        txt, color = d
        assert "继续观察" in txt and "可参与" not in txt, (code, txt)
        assert color == "yellow"


def test_below_5pct_high_open_not_mis_killed():
    """⛔ A2 核心回归点：≤5% 的合理高开不再因「超预期区间上沿」被误杀 → 符合预期。

    背景：预期区间被 `max_range_width=2.0` 钳到 2pp 宽 →「超预期」门槛实际 2~4%，
    实证误杀 3.81%（北自科技）/3.59%（万向德农）= 14%。A2 改绝对 5% 线后恢复。
    """
    for code, gap, rng in (("000011", 3.81, [0.0, 2.0]), ("000012", 3.59, [0.9, 2.9]),
                           ("000013", 4.2, [1.0, 3.0])):
        d = _ai_dyn_verdict(_mk(code, gap, rng))
        assert d is not None and "符合预期" in d[0], (code, gap, rng, d)
        assert d[1] == "red"


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


def test_no_expectation_still_judged_by_absolute_line():
    """A2：无预期区间仍按**绝对 5% 线**判（above 不依赖区间·不再整行省略）。"""
    r = {"code": "000004", "name": "Y", "expectation": {},
         "performance": {"open_check": {"gap_pct": 3.0}}}
    d = _ai_dyn_verdict(r)
    assert d is not None and "符合预期" in d[0]
    r2 = {"code": "000005", "name": "Z", "expectation": {},
          "performance": {"open_check": {"gap_pct": 6.0}}}
    assert "继续观察" in _ai_dyn_verdict(r2)[0]


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


def test_ai_reason_deduped_when_narrative_present():
    """⛔ 理由去重（2026-09-17 用户「风险提示了两遍」）。

    有**点评**（narrative·4 段含 ⚠️风险）时，不再渲染 AI 理由正文——否则同票出现两条风险
    （实测：narrative 与 reason 各带一段 ⚠️风险，成对出现在相邻 block）；无点评时以理由兜底。
    """
    it = _mk("000006", 2.0, [1.0, 3.0])
    it["narrative"] = "今日走势：…\n⚠️风险：点评里的风险段"
    av = {"000006": {"view": "watch", "reason": "依据：…\n⚠️风险：AI理由里的风险段"}}
    line = _ai_view_line(it, av)
    assert "🤖 AI 判断：观察" in line
    assert "点评里的风险段" not in line          # 理由正文整体不渲染
    assert "AI理由里的风险段" not in line
    # 无 narrative → 理由兜底（信息不丢）
    it2 = {k: v for k, v in it.items() if k != "narrative"}
    line2 = _ai_view_line(it2, av)
    assert "AI理由里的风险段" in line2
