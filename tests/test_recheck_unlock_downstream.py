# -*- coding: utf-8 -*-
"""2026-11-05 用户「竞价强就真能买！」——翻案票的最后一公里。

起因：`pool_call` 已允许状态类观察票被 09:25 真实报价翻案为 `participate`，
但下游三处仍拿**盘后** `observe_only` 身份二次否决：
  · `ai_ledger._effective_view`  → 账本记 watch，不成交；
  · `ai_caliber.decide`          → AI 口径判不买；
  · `pipeline_verify._build_record` → 验证/净值记录判不入场；
  卡面说买、账本不买。本文件锁死「以冻结件为准」的单一判据
`two_pool.rechecked_entry`，且 `POOL_CALL_RECHECK_UNLOCK=0` 可逐字回退。

⛔ 只放行「state 类 + 复核翻案 + view=participate」；hard/restricted、复核后仍
   watch 的观察票、缺字段、老冻结件一律不放行。⛔ 不改候选身份、不改 buyable。

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
from core import two_pool as tp            # noqa: E402
import ai_ledger as ledger                 # noqa: E402
import pipeline_verify as pv              # noqa: E402


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setenv("POOL_CALL_RECHECK_UNLOCK", "1")
    # 本文件锁「只有 state 类能翻案」的保守口径（回退路径）。
    monkeypatch.setenv("POOL_CALL_ALL_TRADEABLE", "0")
    monkeypatch.setattr(ac, "AI_VOLUME_BANDS", True)


def _mark(**kw):
    m = {"code": "600001", "name": "翻案票", "view": "participate",
         "rechecked_from_observe": True, "recheck_class": "state",
         "auction_recheck": {"class": "state", "volume_band": "full",
                             "verdict": "状态类·进入完整竞价裁决"},
         "volume_band": "full", "position_cap": 0.5, "even_allocation": True}
    m.update(kw)
    return m


def _decision(**kw):
    d = {"code": "600001", "view": "participate", "bias": "focus",
         "participation_eligibility": "observe_only", "buyable": False,
         "rechecked_from_observe": True, "recheck_class": "state"}
    d.update(kw)
    return d


# ── ① 单一判据 ────────────────────────────────────────────────────────────
def test_rechecked_entry_true_for_state_participate():
    assert tp.rechecked_entry(_mark()) is True


@pytest.mark.parametrize("kw", [
    {"view": "watch"},                          # 复核后仍观察
    {"recheck_class": "hard"},                  # 事实/赔率硬闸
    {"recheck_class": "restricted"},            # 个股级准入未过
    {"rechecked_from_observe": False},          # 正常接力票
    {"rechecked_from_observe": None},           # 老冻结件缺字段
])
def test_rechecked_entry_conservative(kw):
    assert tp.rechecked_entry(_mark(**kw)) is False


def test_rechecked_entry_none_and_switch(monkeypatch):
    assert tp.rechecked_entry(None) is False
    assert tp.rechecked_entry("not a dict") is False
    monkeypatch.setenv("POOL_CALL_RECHECK_UNLOCK", "0")
    assert tp.rechecked_entry(_mark()) is False


# ── ② 账本：冻结件翻案 → participate ─────────────────────────────────────
def test_ledger_unlocks_rechecked_observe_row():
    row = {"code": "600001", "participation_eligibility": "observe_only",
           "bias": "normal"}
    assert ledger._effective_view(row, {"600001": _mark()}) == "participate"


def test_ledger_keeps_plain_observe_row_watch():
    row = {"code": "600001", "participation_eligibility": "observe_only",
           "bias": "focus"}
    assert ledger._effective_view(row, {"600001": _mark(view="watch")}) == "watch"


def test_ledger_switch_rollback(monkeypatch):
    monkeypatch.setenv("POOL_CALL_RECHECK_UNLOCK", "0")
    row = {"code": "600001", "participation_eligibility": "observe_only"}
    assert ledger._effective_view(row, {"600001": _mark()}) == "watch"


def test_ledger_missing_pool_views_is_safe():
    row = {"code": "600001", "participation_eligibility": "observe_only"}
    assert ledger._effective_view(row, None) == "watch"


def test_settle_decision_carries_unlock_marks():
    row = {"code": "600001", "participation_eligibility": "observe_only",
           "bias": "normal"}
    d = ledger._settle_decision(row, "participate", _mark())
    assert d["view"] == "participate"
    assert tp.rechecked_entry(d) is True
    ok, note, _ = ac.decide(d, 2.0, expected_range=[1.0, 4.0],
                            auction_amount_yi=1.0, need_yi=1.0)
    assert ok is True, note


def test_settle_decision_plain_observe_has_no_marks():
    row = {"code": "600001", "participation_eligibility": "observe_only"}
    d = ledger._settle_decision(row, "watch", _mark(view="watch"))
    assert tp.rechecked_entry(d) is False
    ok, _, _ = ac.decide(d, 2.0, expected_range=[1.0, 4.0])
    assert ok is False


# ── ③ AI 口径：不再被盘后身份否决 ─────────────────────────────────────────
def test_decide_allows_rechecked_entry():
    ok, note, meta = ac.decide(_decision(), 2.0, expected_range=[1.0, 4.0],
                               auction_amount_yi=1.0, need_yi=1.0)
    assert ok is True, note
    assert meta["ai_view"] == "participate"


def test_decide_still_blocks_plain_observe():
    ok, note, _ = ac.decide(_decision(rechecked_from_observe=False), 2.0,
                            expected_range=[1.0, 4.0],
                            auction_amount_yi=1.0, need_yi=1.0)
    assert ok is False and "不参与交易" in note


def test_decide_unlock_does_not_bypass_price_or_volume():
    # 翻案 ≠ 免检：超 5% 与真没量照样否决。
    ok, _, _ = ac.decide(_decision(), 6.5, expected_range=[1.0, 4.0],
                         auction_amount_yi=1.0, need_yi=1.0)
    assert ok is False
    ok, _, _ = ac.decide(_decision(), 2.0, expected_range=[1.0, 4.0],
                         auction_amount_yi=0.1, need_yi=1.0)
    assert ok is False


def test_decide_switch_rollback(monkeypatch):
    monkeypatch.setenv("POOL_CALL_RECHECK_UNLOCK", "0")
    ok, _, _ = ac.decide(_decision(), 2.0, expected_range=[1.0, 4.0],
                         auction_amount_yi=1.0, need_yi=1.0)
    assert ok is False


# ── ④ 决策行注入：翻案标记跟着 view 一起走 ───────────────────────────────
def test_inject_pool_views_carries_recheck_marks():
    out = pv._inject_pool_views(
        {"600001": {"code": "600001", "view": "watch",
                    "participation_eligibility": "observe_only"}},
        {"views": {"600001": _mark()}})
    r = out["600001"]
    assert r["view"] == "participate"
    assert r["rechecked_from_observe"] is True
    assert r["recheck_class"] == "state"
    assert r["auction_recheck"]["volume_band"] == "full"
    assert tp.rechecked_entry(r) is True          # decide 拿到就能放行


def test_inject_pool_views_no_marks_for_normal_buy():
    out = pv._inject_pool_views({}, {"views": {"600002": {
        "code": "600002", "view": "participate", "reason": "买入"}}})
    assert tp.rechecked_entry(out["600002"]) is False


# ── ⑤ 验证记录：身份闸让位冻结件，且留痕 ─────────────────────────────────
def _fake_signal(monkeypatch):
    def _eval(cand, quote, **kw):
        return {"decision_status": "buy", "signal_status": "super",
                "gap_pct": 2.0, "auction_amount_yi": 1.0,
                "auction_amount_need_yi": 1.0, "note": ""}
    monkeypatch.setattr(pv, "evaluate_realtime_signal", _eval)


def test_build_record_recheck_unlocked(monkeypatch):
    _fake_signal(monkeypatch)
    cand = {"code": "600001", "participation_eligibility": "observe_only",
            "expectation": {"expected_range": [1.0, 4.0]}}
    rec = pv._build_record(
        cand, {"auction_price": 10.0}, 11.0, date10="2026-11-05",
        ai_decisions={"600001": _decision()},
        pool_views={"600001": _mark()})
    assert rec["recheck_unlocked"] is True
    assert rec["performance"]["open_check"]["can_enter"] is True


def test_build_record_plain_observe_still_blocked(monkeypatch):
    _fake_signal(monkeypatch)
    cand = {"code": "600001", "participation_eligibility": "observe_only",
            "expectation": {"expected_range": [1.0, 4.0]}}
    rec = pv._build_record(
        cand, {"auction_price": 10.0}, 11.0, date10="2026-11-05",
        ai_decisions={"600001": _decision()},
        pool_views={"600001": _mark(view="watch")})
    assert rec["recheck_unlocked"] is False
    assert rec["performance"]["open_check"]["can_enter"] is False


def test_build_record_switch_rollback(monkeypatch):
    monkeypatch.setenv("POOL_CALL_RECHECK_UNLOCK", "0")
    _fake_signal(monkeypatch)
    cand = {"code": "600001", "participation_eligibility": "observe_only",
            "expectation": {"expected_range": [1.0, 4.0]}}
    rec = pv._build_record(
        cand, {"auction_price": 10.0}, 11.0, date10="2026-11-05",
        ai_decisions={"600001": _decision()},
        pool_views={"600001": _mark()})
    assert rec["performance"]["open_check"]["can_enter"] is False
