#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文案 = 行为（2026-11-10 19:36 用户「暂时不动，目前只想要文案和行为一致」）。

背景：早上刚修完「卡面说别碰、裁决却买」，晚上改文案时又差点犯同一件事——
      用户给 `weak`（低开·无预期）的文案是「放弃」，但 ⑥b 判据按「缺数据不裁决」
      **并不拦这一档**。照抄就会立刻重演早上的自相矛盾。

锁三件事：
  1. `ATT_ACTION` 里出现「放弃」的态度，⑥b 必须真把它拦成 watch；
  2. 动作写「可低吸 / 观察接力 / 看量能」的态度，⑥b ⛔ 不许拦；
  3. `market_verdict.note` 只陈述事实，建议归 `advice`（这一层不进裁决）。
"""
import pytest

from core import anchor_call as ac
from core import pool_call


def _q(prev: float, gap: float) -> dict:
    return {"prev_close": prev, "auction_price": round(prev * (1 + gap / 100.0), 3)}


def _cand(code="600002", name="线内票", boards=3, sector="同一线"):
    return {"code": code, "name": name, "boards": boards, "sector": sector,
            "bucket_label": "接力池",
            "expectation": {"expected_range": [0.0, 3.0], "auction_amount_min_yi": 0.1}}


def _run(anchor_gap: float, anchor_expected: float | None = None):
    """跑一次池内裁决：候选 600002 开 +1%，锚点 600001 按给定开幅/预期。"""
    cores = {"同一线": {"code": "600001", "name": "锚点票", "boards": 3, "broken": False}}
    exp = None if anchor_expected is None else {"600001": anchor_expected}
    res = pool_call.call_pool({"relay": [_cand()], "watch": []},
                              {"600002": _q(10.0, 1.0), "600001": _q(10.0, anchor_gap)},
                              plan={"stance": "defend", "views": {}},
                              line_cores=cores,
                              expected_gaps=exp)
    line = ((res.get("meta") or {}).get("defense") or {}).get("anchor") or {}
    att = ((line.get("lines") or {}).get("同一线") or {}).get("attitude")
    return att, res["views"]["600002"], line


# ── 每一档：开幅 / 锚点该开 → 期望态度 + ⑥b 是否拦 ─────────────────────────
CASES = [
    # (锚点开幅, 锚点预期, 态度, ⑥b 是否拦)
    (10.0, None, ac.ATT_YIZI, False),
    (6.0, None, ac.ATT_BIG_GAP, False),
    (3.0, None, ac.ATT_STRONG, False),
    (1.0, None, ac.ATT_FLAT, False),
    (-0.5, None, ac.ATT_WEAK, False),        # ⚠️ 缺预期 → 不裁决（本条是本次的核心）
    (-3.0, None, ac.ATT_BREAK, True),
    (-0.5, 0.2, ac.ATT_LOW, False),          # DEV −0.7
    (-6.5, -0.2, ac.ATT_DEEP_LOW, False),    # DEV −6.3 ≤ 深低开线
]


@pytest.mark.parametrize("gap,expected,att,blocked", CASES)
def test_card_action_matches_real_behavior(gap, expected, att, blocked):
    """卡面动作与 ⑥b 实际行为必须一一对应 —— 写「放弃」就必须真拦。"""
    got_att, view, _ = _run(gap, expected)
    assert got_att == att, f"按 DEV 口径应判 {att}，实际 {got_att}"
    act = ac.attitude_action(att)
    says_reject = ac.ACTION_REJECT in act
    assert says_reject == blocked, (
        f"{att} 的动作写「{act}」，但 ⑥b 实际{'拦' if blocked else '不拦'} ⇒ 文案与行为不一致")
    assert (view["view"] == "watch") == blocked


def test_weak_is_not_rejected_but_says_watch_volume():
    """`weak`＝拿不到该票预期 + 低开：卡面⛔ 不许写「放弃」，写「看量能」交给后面的闸。"""
    act, label = ac.attitude_action(ac.ATT_WEAK), ac.attitude_label(ac.ATT_WEAK)
    assert ac.ACTION_REJECT not in act
    assert "量能" in act
    assert "无预期" in label, "没有预期可依，就不能说『低于预期』（那需要一个预期）"


def test_only_break_says_reject():
    """全表扫一遍：说「放弃」的动作有且只有 `break` —— 与 ⑥b 的硬拦集合完全相等。"""
    rej = {a for a, t in ac.ATT_ACTION.items() if ac.ACTION_REJECT in t}
    assert rej == {ac.ATT_BREAK}


def test_market_note_is_fact_advice_is_suggestion():
    """`market_verdict` 不进裁决 ⇒ note 只许陈述事实，「空仓」这类结论只能出现在 advice。"""
    off = ac.market_verdict([ac.ATT_BREAK, ac.ATT_BREAK])
    assert off["level"] == "off"
    assert "空仓" not in off["note"], "note 里写空仓＝系统并不执行的承诺"
    assert "空仓" in off["advice"]
    assert "不强制" in off["advice"] or "逐线" in off["advice"]

    single = ac.market_verdict([ac.ATT_BREAK, ac.ATT_FLAT])
    assert single["level"] == "single" and "最强" in single["advice"]

    normal = ac.market_verdict([ac.ATT_STRONG, ac.ATT_YIZI])
    assert normal["level"] == "normal" and normal["advice"]


def test_every_attitude_has_label_and_action_key():
    """档位表不许漏键：缺键会让卡面走默认分支（11-10 排错时踩过）。"""
    for att in ac.ATT_LABEL:
        assert att in ac.ATT_ACTION, f"{att} 缺动作文案"
