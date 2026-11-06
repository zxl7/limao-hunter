# -*- coding: utf-8 -*-
"""2026-11-05 用户「量能区间优化 · 不限制只数 · 平均分配仓位」回归锁。

用户原话：「量能这一块的区间优化一下吧，不然的话，很容易没有达到我们量能的标准，
然后能买几个股，我们是不限制的，只要达标了，我们就按照单票最多50%，
平均分配仓位买入」。

锁死六件事（⛔ 任一松动都会出现「卡面说一套、账本记另一套」）：
  ① 量能改**分档区间**：full ≥0.70 / soft 0.35~0.70 / dry <0.35 / unknown 缺数据。
     只有 dry 才否决；soft 达标可买。⛔ 0.35 地板不拆（31 日回放在实盘救钱）。
  ② 三处同源：pool_call 裁决 / ai_caliber.decide 账本成交 / hibernate 阀门
     共用 `ai_caliber.volume_band`，⛔ 禁另写一套阈值。
  ③ 不限制只数：effective_quota>=1 时所有过完个股闸的票都参与，不再按数字截断。
  ④ 0 名额仍空仓：hibernate / 方案禁买 / 冰点 → 绝不放宽。
  ⑤ 平均分配：N 只 → 每票 min(单票硬顶 50%, 100%/N)；单票场景 50% 不打满 100%。
  ⑥ 三处同值：逐票 position_cap / meta.slot_policy / 账本重裁口径一致，且开关可逆。

全隔离：不读真实 cache / 台账 / 网络，不推群。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
for _p in (str(WC), str(WC / "pipelines"), str(WC / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import ai_caliber as ac          # noqa: E402
from core import pool_call as pc           # noqa: E402
from core import risk_limits as rl         # noqa: E402
import build_cards as bc                   # noqa: E402


@pytest.fixture(autouse=True)
def _new_rules(monkeypatch):
    """显式打开 11-05 新规则（⛔ 不依赖模块默认值，断言自己说清开的是哪套）。"""
    monkeypatch.setattr(ac, "AI_VOLUME_BANDS", True)
    monkeypatch.setattr(pc, "POOL_CALL_UNLIMITED_SLOTS", True)
    monkeypatch.setattr(pc, "POOL_CALL_EVEN_ALLOCATION", True)
    monkeypatch.setattr(pc, "POOL_CALL_EVEN_RESPECT_DRAWDOWN", False)
    monkeypatch.setattr(pc, "POOL_CALL_VOLUME_SCALE_POSITION", False)
    monkeypatch.setattr(pc, "POOL_CALL_VOLUME_SOFT", False)


def _cand(code, boards=2, need=1.0, **kw):
    c = {"code": code, "name": f"票{code}", "boards": boards, "sector": "测试题材",
         "bucket_label": "接力池",
         "expectation": {"expected_range": [1.0, 4.0], "auction_amount_min_yi": need}}
    c.update(kw)
    return c


def _q(amt=2.0, gap=3.0):
    return {"prev_close": 10.0,
            "auction_price": round(10.0 * (1 + gap / 100.0), 3),
            "auction_amount_yi": amt}


def _call(n, *, amts=None, stance="attack", plan=None, **kw):
    codes = [f"{i + 1:06d}" for i in range(n)]
    amts = amts or [2.0] * n
    return pc.call_pool(
        {"relay": [_cand(c, boards=5 - i) for i, c in enumerate(codes)], "watch": []},
        {c: _q(a) for c, a in zip(codes, amts)},
        plan={"stance": stance, "views": {}}, stance=stance,
        line_cores={}, sector_flow={}, zt_series={}, zt_days=[],
        market_open=None, relay_plan=plan, **kw)


def _parts(res):
    return [c for c, v in res["views"].items() if v.get("view") == "participate"]


# ── ① 量能分档区间（唯一判据源）────────────────────────────────────────
@pytest.mark.parametrize("ratio,band,block", [
    (1.50, "full", False), (0.70, "full", False),
    (0.60, "soft", False), (0.50, "soft", False), (0.35, "soft", False),
    (0.34, "dry", True), (0.22, "dry", True), (0.0, "dry", True),
])
def test_volume_band_boundaries(ratio, band, block):
    b = ac.volume_band(ratio, 1.0)
    assert b["band"] == band and b["block"] is block


def test_volume_band_unknown_is_tolerant():
    """缺目标 / 缺报价 / 目标 <=0 → unknown，⛔ 不判、不编造。"""
    for amt, need in ((None, 1.0), (1.0, None), (1.0, 0), (1.0, -1), (float("nan"), 1.0)):
        b = ac.volume_band(amt, need)
        assert b["band"] == "unknown" and b["block"] is False


def test_volume_band_scale_is_monotonic():
    """soft 档量越弱仓位系数越低（区间而非单线）。"""
    assert ac.volume_band(0.60, 1.0)["scale"] > ac.volume_band(0.40, 1.0)["scale"]
    assert ac.volume_band(0.40, 1.0)["scale"] > 0


# ── ② 三处同源（卡面 view / 账本 decide / 阀门）──────────────────────────
def test_decide_and_call_pool_agree_on_soft_and_dry():
    """⛔ 防双轨：soft 两边都放行、dry 两边都否决（卡面说买、账本不成交是事故）。"""
    dec = {"view": "participate", "bias": "focus"}
    for ratio, want_can in ((0.60, True), (0.50, True), (0.30, False)):
        can, _, meta = ac.decide(dec, 3.0, expected_range=[1.0, 4.0],
                                 auction_amount_yi=ratio, need_yi=1.0)
        assert can is want_can, (ratio, meta)
        res = pc.call_pool({"relay": [_cand("000001")], "watch": []},
                           {"000001": _q(ratio)},
                           plan={"stance": "attack", "views": {}}, stance="attack",
                           line_cores={}, sector_flow={}, zt_series={}, zt_days=[],
                           market_open=None, relay_plan=None)
        assert (res["views"]["000001"]["view"] == "participate") is want_can


def test_decide_green_open_not_judged_by_volume():
    """量能门只判红开（gap>0）：平开且落在预期区间内，量再低也不由量能门否决。
    ⚠️ 绿开是否可买仍由 gap_zone / pool_call 的『没红开』闸决定，此处只锁量能门边界。"""
    can, note, meta = ac.decide({"view": "participate"}, 0.0,
                                expected_range=[-3.0, 3.0],
                                auction_amount_yi=0.01, need_yi=1.0)
    assert can is True, note
    assert "volume_band" not in meta       # 量能门根本没进入判定


def test_hibernate_valve_still_requires_full():
    """逃生阀宁严勿松：soft 档不配开阀（判据仍走 volume_band 唯一源）。"""
    assert ac.volume_band(0.60, 1.0)["band"] != "full"
    assert ac.volume_band(0.80, 1.0)["band"] == "full"


# ── ③ 不限制只数 ──────────────────────────────────────────────────────
def test_unlimited_slots_bypass_numeric_quota():
    """attack 原名额 3，6 只全达标 → 6 只都参与（不再截断）。"""
    res = _call(6)
    assert len(_parts(res)) == 6
    assert res["meta"]["slot_policy"]["unlimited_slots"] is True


def test_unlimited_slots_switch_reverts(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_UNLIMITED_SLOTS", False)
    assert len(_parts(_call(6))) == 3


def test_volume_dry_still_blocks_even_when_unlimited():
    """不限制只数 ≠ 放宽个股闸：真没量的票仍 watch。"""
    res = _call(5, amts=[2.0, 2.0, 0.20, 2.0, 2.0])
    assert len(_parts(res)) == 4
    assert res["views"]["000003"]["view"] == "watch"
    assert res["views"]["000003"]["volume_band"] == "dry"


def test_five_percent_hard_line_still_blocks(monkeypatch):
    """5% 硬线不因「不限制只数」松动。"""
    codes = ["000001", "000002"]
    res = pc.call_pool({"relay": [_cand(c) for c in codes], "watch": []},
                       {"000001": _q(2.0), "000002": _q(2.0, gap=6.5)},
                       plan={"stance": "attack", "views": {}}, stance="attack",
                       line_cores={}, sector_flow={}, zt_series={}, zt_days=[],
                       market_open=None, relay_plan=None)
    assert res["views"]["000002"]["view"] == "skip"
    assert len(_parts(res)) == 1


# ── ④ 0 名额仍空仓 ────────────────────────────────────────────────────
def test_zero_quota_never_widened():
    assert _parts(_call(4, stance="hibernate")) == []


def test_plan_forbids_buy_never_widened():
    blocked = {"enabled": True, "can_buy": False, "quota": 0,
               "per_pick_cap": 0.3, "total_cap": 0.3}
    assert _parts(_call(4, plan=blocked)) == []


# ── ⑤ 平均分配 + 单票 50% 硬顶 ────────────────────────────────────────
@pytest.mark.parametrize("n,want", [(1, 0.5), (2, 0.5), (3, 0.3333),
                                    (4, 0.25), (5, 0.2), (6, 0.1667)])
def test_even_allocation_formula(n, want):
    res = _call(n)
    caps = [v["position_cap"] for v in res["views"].values()
            if v.get("view") == "participate"]
    assert len(caps) == n
    assert all(c == pytest.approx(want, abs=1e-4) for c in caps)
    assert all(c <= rl.MAX_POSITION_PER_STOCK + 1e-9 for c in caps)
    assert sum(caps) <= 1.0 + 1e-3          # 总仓不放大


def test_all_participants_carry_same_cap_and_reason():
    res = _call(3)
    for v in res["views"].values():
        if v.get("view") != "participate":
            continue
        assert v["even_allocation"] is True
        assert v["allocation_count"] == 3
        assert "3只均分" in v["reason"]


def test_single_pick_keeps_ledger_marker():
    """账本 ai_ledger 靠逐票 single_pick 重裁仓位，双轨必须同值。"""
    res = _call(1)
    v = res["views"]["000001"]
    assert v["single_pick"] is True and v["position_cap"] == pytest.approx(0.5)
    assert res["meta"]["single_pick"] is True


def test_even_allocation_switch_reverts(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_EVEN_ALLOCATION", False)
    res = _call(3)
    assert not any(v.get("even_allocation") for v in res["views"].values())


def test_volume_bands_switch_reverts(monkeypatch):
    """AI_VOLUME_BANDS=0 → 逐字回退旧 0.7 单线（soft 归 dry）。"""
    monkeypatch.setattr(ac, "AI_VOLUME_BANDS", False)
    assert ac.volume_band(0.60, 1.0)["band"] == "dry"
    res = pc.call_pool({"relay": [_cand("000001")], "watch": []},
                       {"000001": _q(0.60)},
                       plan={"stance": "attack", "views": {}}, stance="attack",
                       line_cores={}, sector_flow={}, zt_series={}, zt_days=[],
                       market_open=None, relay_plan=None)
    v = res["views"]["000001"]
    assert v["view"] == "watch" and "量不够" in v["reason"]


# ── ⑥ 卡面 / meta / 账本三处同值 ──────────────────────────────────────
def test_slot_policy_exposes_even_allocation():
    sp = _call(4)["meta"]["slot_policy"]
    assert sp["even_allocation"] is True
    assert sp["even_cap"] == pytest.approx(0.25)
    assert sp["allocation_count"] == sp["participate_count"] == 4


def test_card_shows_even_allocation_not_legacy_tier():
    """多票卡面读真实参与数 + 均分上限，⛔ 不再写弱档 30%。"""
    meta = {"slot_policy": {"single_pick": False, "effective_quota": 2,
                            "participate_count": 4, "allocation_count": 4,
                            "even_allocation": True, "even_cap": 0.25}}
    plan = {"enabled": True, "can_buy": True, "tier": "weak", "tier_label": "弱·收紧",
            "per_pick_cap": 0.30, "total_cap": 0.30, "quota": 1,
            "drawdown_pct": None, "drawdown_level": None, "drawdown_mult": 1.0,
            "_diag": {}}
    import json as _json
    block = bc._relay_position_block(plan, pool_meta=meta)
    text = _json.dumps(block, ensure_ascii=False)
    assert "4 只" in text and "25%" in text
    assert "30%" not in text


def test_ai_plan_block_shows_unlimited_not_legacy_cap():
    """「今日买入」段在不限名额时读真实参与数，⛔ 不再写「买 2 只以内」。"""
    import json as _json
    pool = {"participate": ["000001", "000002", "000003", "000004"],
            "pool_size": 21, "effective_quota": 2, "stance": "defend",
            "slot_policy": {"unlimited_slots": True, "even_allocation": True,
                            "even_cap": 0.25, "allocation_count": 4}}
    block = bc._ai_plan_block({"views": {"000001": {"bias": "重点看"}}},
                              [], pool_summary=pool)
    text = _json.dumps(block, ensure_ascii=False)
    assert "买 4 只" in text and "25%" in text
    assert "2 只以内" not in text

    # 回退口径仍写旧档位，避免历史卡片串味
    pool["slot_policy"] = {"unlimited_slots": False}
    text2 = _json.dumps(bc._ai_plan_block(
        {"views": {"000001": {"bias": "重点看"}}}, [], pool_summary=pool),
        ensure_ascii=False)
    assert "2 只以内" in text2
