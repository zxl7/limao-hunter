# -*- coding: utf-8 -*-
"""2026-11-05 用户拍板 A：「观察肯定也要再通过竞价这里再判一遍，而不是直接 pass 掉」。

起因：盘后 20 只 `observe_only` 在 09:25 被 `pool_call` 一步 `continue` 短路，
连今早开盘/竞价额都不读就写了「0 买入」。用户要的是**逐票复核**，不是全放行。

锁死七件事：
  ① 分类单一源 `two_pool.recheck_class`：state / hard / restricted；缺字段一律保守。
  ② 所有 observe_only 票都写 `auction_recheck`（今早开盘、竞价额、量能档、结论）。
  ③ `hard`（一字/688/ST/最高板薄封）复核后仍观察，理由带今早实测，不再一句固定话术。
  ④ `restricted`（个股级准入未过）复核后仍观察，竞价不代替准入。
     ⚠️ 11-06 起「历史连板修复」组改判 state（用户「可以放进来」）——该组拒因
     「非价量N形态确认」是量价形态条件，09:25 竞价恰好是最直接的量价证据。
     ⛔ 只是把「能不能复核」修对，不放宽任何个股闸（实测在真实链路里名单 0 变化，
     因为真正挡住它的是「预案回避」与 DEV<−2，不在这道分类上）。
  ⑤ `state` 才进完整裁决链；量能须 full 档才允许翻案；5% / 低开 / 预案等闸照旧。
  ⑥ 身份不变：候选行仍是 observe_only + buyable=False，⛔ 不靠删字段偷渡。
  ⑦ `POOL_CALL_RECHECK_OBSERVE=0` 逐字回退旧短路；0 名额仍空仓。

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
from core import two_pool as tp            # noqa: E402


@pytest.fixture(autouse=True)
def _new_rules(monkeypatch):
    """显式打开本轮与上一轮新规则，⛔ 不依赖模块默认值。"""
    monkeypatch.setattr(ac, "AI_VOLUME_BANDS", True)
    monkeypatch.setattr(pc, "POOL_CALL_UNLIMITED_SLOTS", True)
    monkeypatch.setattr(pc, "POOL_CALL_EVEN_ALLOCATION", True)
    monkeypatch.setattr(pc, "POOL_CALL_EVEN_RESPECT_DRAWDOWN", False)
    monkeypatch.setattr(pc, "POOL_CALL_VOLUME_SCALE_POSITION", False)
    monkeypatch.setattr(pc, "POOL_CALL_VOLUME_SOFT", False)
    monkeypatch.setattr(pc, "POOL_CALL_RECHECK_OBSERVE", True)
    monkeypatch.setattr(pc, "POOL_CALL_RECHECK_STATE_FULL", True)
    # ⛔ 2026-11-10：本文件锁 11-03 两池契约的**回退口径**（观察池默认不可买）；
    #    新默认「全池可交易」的回归锁见 tests/test_all_tradeable.py。
    monkeypatch.setenv("POOL_CALL_ALL_TRADEABLE", "0")


def _watch(code, *, kind="state", boards=2, need=1.0, **kw):
    r = {
        "code": code, "name": f"票{code}", "boards": boards,
        "sector": "测试题材", "core_theme": "测试题材",
        "bucket": "watch", "bucket_label": "观察池",
        "participation_eligibility": "observe_only",
        "watch_only": True, "buyable": False,
        "expectation": {"expected_range": [1.0, 4.0], "auction_amount_min_yi": need},
    }
    if kind == "state":
        r.update(watch_group="过筛未过·只观察",
                 watch_reason="没通过接力资格（同题材仅 1 家，未成线）—— 位置要看，不参与")
    elif kind == "state_turnover":
        r.update(watch_group="过筛未过·只观察",
                 watch_reason="没通过接力资格（成交额/题材门槛未过）—— 位置要看，不参与")
    elif kind == "hard_one_word":
        r.update(watch_group="一字龙头", is_one_word=True,
                 watch_reason="一字封死买不进，仅作这条线的强弱参照；不参与")
    elif kind == "hard_thin":
        r.update(watch_group="最高板封单过薄",
                 watch_reason="它是今天全市场最高 3 板，但封单只占流通盘的 0.30% → 只看不买")
    elif kind == "hard_st":
        r["name"] = "ST测试"
    elif kind == "hard_688":
        r["code"] = "688001"
    elif kind == "restricted":
        # ⚠️ 2026-11-06：「历史连板修复」组已升为 state（用户「可以放进来」），
        #    这里换一个真正属于「个股级准入未过」的例子——`补充观察` 组 + 历史修复被拦。
        r.update(watch_group="补充观察",
                 watch_reason="前置准入未过（昨日同线核心已断板）—— 只观察，不参与",
                 historical_repair={"blocked_by": ["前置准入", "题材龙"],
                                    "eligibility": "observe_only"})
    elif kind == "repair":
        r.update(watch_group="历史连板修复",
                 watch_reason="逐日历史连板后断板再涨停；非价量N形态确认")
    r.update(kw)
    return r


def _q(amt=2.0, gap=3.0):
    return {"prev_close": 10.0,
            "auction_price": round(10.0 * (1 + gap / 100.0), 3),
            "auction_amount_yi": amt}


def _call(rows, *, amt=2.0, gap=3.0, stance="attack", **kw):
    return pc.call_pool(
        {"relay": [], "watch": rows},
        {str(r["code"]): _q(amt, gap) for r in rows},
        plan={"stance": stance, "views": {}}, stance=stance,
        line_cores={}, sector_flow={}, zt_series={}, zt_days=[],
        market_open=None, relay_plan=None, **kw)


def _parts(res):
    return [c for c, v in res["views"].items() if v.get("view") == "participate"]


# ── ① 分类单一源 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind,expected", [
    ("state", "state"), ("state_turnover", "state"),
    ("repair", "state"),                      # 🔁 11-06 用户「可以放进来」
    ("hard_one_word", "hard"), ("hard_thin", "hard"),
    ("hard_st", "hard"), ("hard_688", "hard"),
    ("restricted", "restricted"),
])
def test_recheck_class(kind, expected):
    assert tp.recheck_class(_watch("000001", kind=kind)) == expected


def test_repair_group_recheck_is_reversible(monkeypatch):
    """「历史连板修复」组：`POOL_CALL_RECHECK_REPAIR=0` 逐字回到整组 restricted。"""
    r = _watch("000001", kind="repair")
    assert tp.recheck_class(r) == "state"
    monkeypatch.setenv("POOL_CALL_RECHECK_REPAIR", "0")
    assert tp.recheck_class(r) == "restricted"


def test_repair_group_needs_both_group_and_reason():
    """⛔ 判据是「组名 ∧ 该组固定拒因」双命中，不按「带修复档案就放行」。"""
    r = _watch("000001", kind="repair")
    r["watch_group"] = "补充观察"                     # 组名不对 → 不放行
    assert tp.recheck_class(r) == "restricted"
    r2 = _watch("000001", kind="repair")
    r2["watch_reason"] = "别的理由"                   # 拒因不对 → 不放行
    assert tp.recheck_class(r2) == "restricted"


def test_recheck_class_conservative_on_missing_fields():
    """缺分组/理由/老候选一律 restricted，⛔ 不误放行。"""
    assert tp.recheck_class({"code": "000001", "name": "无标注"}) == "restricted"
    assert tp.recheck_class(_watch("000001", kind="state", historical_repair={
        "blocked_by": ["前置准入"]})) == "state"   # 状态类优先于修复档案


def test_finalize_stamps_recheck_class_without_changing_identity():
    """finalize 只打标注，不改 observe_only / buyable=False。"""
    _, watch, _ = tp.finalize([], [_watch("000001"), _watch("000002", kind="hard_one_word")],
                              stage="test")
    assert {r["recheck_class"] for r in watch} == {"state", "hard"}
    assert all(tp.observation_only(r) and r.get("buyable") is False for r in watch)


# ── ②③④ 硬闸 / 个股级准入：复核留痕但仍不买 ─────────────────────────────
@pytest.mark.parametrize("kind,gap", [
    # 🕯 2026-11-12：一字龙头开 +3% 属「次日低吸窗口」→ 会参与；
    #    本用例锁「硬闸仍不买」，所以给它 +7%（窗口外）。
    ("hard_one_word", 7.0), ("hard_thin", 3.0), ("hard_st", 3.0), ("restricted", 3.0),
])
def test_hard_and_restricted_are_rechecked_then_still_watch(kind, gap):
    r = _watch("000001", kind=kind)
    res = _call([r], amt=2.0, gap=gap)
    v = res["views"][str(r["code"])]
    assert v["view"] == "watch"
    assert v["participation_eligibility"] == "observe_only"
    rc = v["auction_recheck"]
    assert rc["gap_pct"] == pytest.approx(gap, abs=0.05)
    assert rc["amount_yi"] == 2.0 and rc["volume_band"] == "full"
    assert "今早开" in v["reason"] and "量足" in v["reason"]
    assert res["meta"]["recheck"]["observed"] == 1
    assert res["meta"]["recheck"]["participated"] == []
    assert not _parts(res)


def test_hard_reason_is_not_the_old_generic_line():
    """用户要的就是这句「别再直接 pass 掉」。"""
    v = _call([_watch("000001", kind="hard_thin")])["views"]["000001"]
    assert "板块观察参照，不参与交易" not in v["reason"]
    assert "封单只占流通盘的 0.30%" not in v["reason"]   # 不再照抄盘后原话
    assert "今早开" in v["reason"]


# ── ⑤ 状态类：足量才翻案，其余闸照旧 ─────────────────────────────────────
def test_state_unlocks_only_with_full_volume():
    r = _watch("000001")
    res = _call([r], amt=2.0, gap=3.0)
    v = res["views"]["000001"]
    assert v["view"] == "participate", v["reason"]
    assert v.get("rechecked_from_observe") is True
    assert res["meta"]["recheck"]["state_entered"] == 1
    assert res["meta"]["recheck"]["participated"] == ["000001"]


@pytest.mark.parametrize("amt,band", [(0.5, "soft"), (0.2, "dry"), (None, "unknown")])
def test_state_requires_full_volume_evidence(amt, band):
    v = _call([_watch("000001")], amt=amt)["views"]["000001"]
    assert v["view"] == "watch"
    assert v["auction_recheck"]["volume_band"] == band
    assert (v.get("recheck_gate") or "量" in v["reason"])


def test_state_soft_volume_allowed_when_guard_off(monkeypatch):
    """关掉举证要求后按常规量能门：soft 可买（可逆性验证）。"""
    monkeypatch.setattr(pc, "POOL_CALL_RECHECK_STATE_FULL", False)
    v = _call([_watch("000001")], amt=0.5)["views"]["000001"]
    assert v["view"] == "participate"


def test_state_still_blocked_by_5pct_and_green_open():
    assert _call([_watch("000001")], gap=6.0)["views"]["000001"]["view"] == "skip"
    assert _call([_watch("000001")], gap=-1.0)["views"]["000001"]["view"] != "participate"


def test_state_no_longer_blocked_by_avoid_plan(monkeypatch):
    """盘后「回避」默认不再否决（2026-11-06）；旧行为用开关逐字复原。"""
    r = _watch("000001")
    kw = dict(plan={"stance": "attack",
                    "views": {"000001": {"bias": "avoid"}}},
              stance="attack", line_cores={}, sector_flow={},
              zt_series={}, zt_days=[], market_open=None, relay_plan=None)
    res = pc.call_pool({"relay": [], "watch": [r]}, {"000001": _q()}, **kw)
    assert res["views"]["000001"]["view"] == "participate"

    monkeypatch.setattr(pc, "POOL_CALL_EOD_PLAN", True)   # 要验「回避」必须先消费预案
    monkeypatch.setattr(pc, "POOL_CALL_BIAS_AVOID_VETO", True)
    res = pc.call_pool({"relay": [], "watch": [r]}, {"000001": _q()}, **kw)
    assert res["views"]["000001"]["view"] != "participate"


def test_zero_quota_still_empty_even_for_state(monkeypatch):
    """hibernate / 0 名额：⛔ 不放宽，观察复核不变成放行。"""
    assert not _parts(_call([_watch("000001")], stance="hibernate"))


# ── ⑥⑦ 身份与回退 ───────────────────────────────────────────────────────
def test_identity_of_candidate_row_is_not_mutated():
    r = _watch("000001")
    _call([r], amt=2.0)
    assert r["participation_eligibility"] == "observe_only"
    assert r["buyable"] is False and r["watch_only"] is True


def test_fallback_is_verbatim_old_shortcut(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_RECHECK_OBSERVE", False)
    r = _watch("000001", kind="hard_thin")
    res = _call([r])
    v = res["views"]["000001"]
    assert v["view"] == "watch" and "auction_recheck" not in v
    assert v["reason"] == r["watch_reason"]
    assert res["meta"]["recheck"]["observed"] == 0


def test_mixed_pool_meta_counts_all_observe_tickets():
    rows = [_watch(f"{i:06d}", kind=k) for i, k in enumerate(
        ["state", "hard_one_word", "hard_thin", "restricted", "state_turnover"], 1)]
    res = _call(rows, amt=2.0, gap=3.0)
    rc = res["meta"]["recheck"]
    # 🕯 2026-11-12：一字龙头开 +3%（2~5% 低吸窗口）→ 也进入完整裁决
    #    ⇒ state_entered 2 → 3；参与集合可能包含 000002（一字票）。
    assert rc["observed"] == 5 and rc["state_entered"] == 3
    assert set(rc["participated"]) <= {"000001", "000002", "000005"}
