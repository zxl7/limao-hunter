# -*- coding: utf-8 -*-
"""2026-11-05 用户「系统只需要竞价数据就判断后期走势，系统直接买入，
其他的机会交给人工二次自己交易不做记录」——竞价三态分层全链路锁。

同日二次纠偏：「我没有说固定涨幅区间，我说的是根据昨日的涨停的质量
（时间，量能，封单大小）计算一个可能的竞价涨幅！大于 5% 不买倒是真的！」

本文件锁死七件事：
  ① 分层只用 09:25 之前的事前事实（开幅 / 竞价额 / 昨全天成交额 / 逐票质量预期），
     ⛔ 不读分时；⛔ 不再有固定涨幅区间；
  ② 11-05 用户删除 5% 与红开两条绝对线：注入的是**涨停幅度**（10/20），
     只判「竞价封死买不进」；强票高开、弱票低开都按逐票预期管理；
  ③ 缺数据 = unknown，不裁决、不猜、不踢出系统；缺质量预期也 unknown；
  ④ 同一开幅、不同质量预期 → 不同态（证明判据是「实际 vs 该开多少」）；
  ⑤ B/C = 人工二次机会，manual_only 由最终 view 决定，⛔ 参与票恒不标；
  ⑥ 下游（decide / 账本结算 / 验证记录）统一拦住人工机会，不写系统成交；
  ⑦ 全部可逆：POOL_CALL_AUCTION_STATE=0 全量回退，
     POOL_CALL_EXPECTED_GAP=0 不裁决，POOL_CALL_A_STATE_ONLY=0 影子对照。

⚠️ 本文件不证明「A 闸能提高胜率」——那是描述统计，不是本测试的主张。
全隔离：不读真实 cache / 台账 / 行情，不推群、不落盘。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
for _p in (str(WC), str(WC / "pipelines"), str(WC / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if str(WC.parent) not in sys.path:
    sys.path.insert(0, str(WC.parent))

from core import auction_state as ast          # noqa: E402
from core import ai_caliber as ac              # noqa: E402
from core import expected_gap as eg            # noqa: E402
from core import pool_call as pc               # noqa: E402
from core import two_pool as tp                # noqa: E402
import ai_ledger as ledger                     # noqa: E402
import pipeline_verify as pv                   # noqa: E402


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setenv("POOL_CALL_AUCTION_STATE", "1")
    monkeypatch.setenv("POOL_CALL_EXPECTED_GAP", "1")
    monkeypatch.setenv("POOL_CALL_A_STATE_ONLY", "1")
    monkeypatch.setenv("POOL_CALL_A_STATE_PRIORITY", "1")
    for k in ("POOL_CALL_A_DEV_MIN", "POOL_CALL_B_DEV_MIN",
              "POOL_CALL_A_R_MIN", "POOL_CALL_B_R_MIN",
              "POOL_CALL_A_GAP_MIN", "POOL_CALL_A_GAP_MAX",
              "POOL_CALL_B_GAP_MIN", "POOL_CALL_B_GAP_MAX"):
        monkeypatch.delenv(k, raising=False)


def C(**kw):
    p = dict(gap_pct=4.0, amount_yi=0.5, prev_amount_yi=10.0,
             expected_gap_pct=2.0, max_gap_pct=10.0)  # 10 = 10cm 涨停幅度（物理线）
    p.update(kw)
    return ast.classify(**p)


# ── ① R 与昨收分母 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("amt,prev,exp", [
    (0.5, 10.0, 5.0), (0.0, 10.0, 0.0), (1.0, 2.0, 50.0),
])
def test_ratio_pct(amt, prev, exp):
    assert ast.ratio_pct(amt, prev) == pytest.approx(exp)


@pytest.mark.parametrize("amt,prev", [
    (None, 10), (0.5, None), (0.5, 0), (0.5, -1), (-1, 10),
    ("abc", 10), ("nan", 10), (float("inf"), 10),
])
def test_ratio_invalid_is_none(amt, prev):
    assert ast.ratio_pct(amt, prev) is None


def test_prev_amounts_from_rows_normalizes_and_skips_bad():
    rows = [{"code": "1", "cje_yi": 8.5}, {"code": "600001", "cje_yi": 10},
            {"code": "600002", "cje_yi": 0}, {"code": "600003", "cje_yi": "x"},
            {"code": "", "cje_yi": 3}, "junk", None]
    assert ast.prev_amounts_from_rows(rows) == {"000001": 8.5, "600001": 10.0}
    assert ast.prev_amounts_from_rows(None) == {}


# ── ② 相对预期分层 + 5% 硬线 ───────────────────────────────────────────────
def test_a_state_core_case():
    r = C()
    assert r["auction_state"] == "A"
    assert r["auction_route"] == "system"
    assert r["auction_r"] == 5.0
    assert r["expected_gap_pct"] == 2.0
    assert r["gap_vs_expected"] == 2.0


@pytest.mark.parametrize("gap,state", [
    (2.5, "A"),        # DEV = +0.5，A 下沿含等号
    (2.49, "B"),
    (5.0, "A"),        # 11-05 删 5% 线：强票开 5% 属符合预期
    (5.01, "A"),       # ⛔ 不再因「高开超 5%」排除（用户删除该线）
    (9.9, "C"),        # 唯一保留的绝对线：竞价封死买不进
    (0.0, "B"),        # DEV = −2.0，B 下沿含等号
    (-0.01, "C"),
    (-2.0, "C"),
])
def test_dev_boundaries(gap, state):
    assert C(gap_pct=gap, amount_yi=1.0, prev_amount_yi=10.0)["auction_state"] == state


@pytest.mark.parametrize("amt,r", [(0.1, 1.0), (0.29, 2.9), (0.3, 3.0)])
def test_r_threshold_for_a(amt, r):
    got = C(amount_yi=amt, prev_amount_yi=10.0)
    assert got["auction_r"] == pytest.approx(r)
    assert got["auction_state"] == ("A" if r >= 3.0 else "C")


def test_b_requires_r():
    assert C(gap_pct=1.0, amount_yi=0.1, prev_amount_yi=10.0)["auction_state"] == "C"


def test_physical_limit_injected_not_hardcoded():
    """11-05 删 5% 线后，注入的是**涨停幅度**（物理买不进线），不是高开幅度上限。"""
    assert C(gap_pct=9.9, max_gap_pct=10.0)["auction_state"] == "C"   # 10cm 封死
    assert C(gap_pct=9.7, max_gap_pct=10.0)["auction_state"] == "A"   # 还买得到
    assert C(gap_pct=15.0, max_gap_pct=20.0)["auction_state"] == "A"  # 20cm 高开不是封死
    assert C(gap_pct=19.9, max_gap_pct=20.0)["auction_state"] == "C"


def test_same_gap_different_quality_expectation_changes_state():
    """用户纠偏的锁：不存在「固定涨幅区间」，同一开幅因质量预期不同而分层不同。"""
    assert C(gap_pct=2.0, expected_gap_pct=1.0)["auction_state"] == "A"
    assert C(gap_pct=2.0, expected_gap_pct=3.0)["auction_state"] == "B"
    assert C(gap_pct=2.0, expected_gap_pct=5.0)["auction_state"] == "C"


def test_strong_seal_expects_high_gap_so_mild_open_is_below_expectation():
    """昨日强封 → 质量预期可 >5%；此时 ≤5% 的可成交开幅按定义必是低于预期。"""
    row = {"fbt": "092500", "seal_amt": 2.1, "lt_mktcap": 39.2,
           "hs": 2.2, "zbc": 0, "boards": 4, "is_20cm": False}
    e = eg.expected_gap_pct(row)
    assert e > 5
    assert C(gap_pct=4.0, expected_gap_pct=e)["auction_state"] == "C"
    assert C(gap_pct=9.9, expected_gap_pct=e)["auction_state"] == "C"  # 竞价封死买不进


def test_env_thresholds_override(monkeypatch):
    monkeypatch.setenv("POOL_CALL_A_DEV_MIN", "0.0")
    monkeypatch.setenv("POOL_CALL_A_R_MIN", "8.0")
    assert C(gap_pct=2.0, amount_yi=1.0)["auction_state"] == "A"   # DEV=0 且 R=10 → A
    # R=5 未达 A 的 8%，但仍达 B 的 3% → 归人工二次机会，不是排除。
    assert C(gap_pct=2.0, amount_yi=0.5)["auction_state"] == "B"
    assert C(gap_pct=2.0, amount_yi=0.1)["auction_state"] == "C"   # R=1 才落 C


# ── ③ 缺数据不裁决 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("kw", [
    {"gap_pct": None}, {"amount_yi": None}, {"prev_amount_yi": None},
    {"prev_amount_yi": 0}, {"gap_pct": "x"}, {"amount_yi": -1},
    {"expected_gap_pct": None}, {"expected_gap_pct": "x"},
])
def test_unknown_never_guessed(kw):
    r = C(**kw)
    assert r["auction_state"] == "unknown"
    assert r["auction_route"] == "unknown"


def test_unknown_is_not_rejected_by_hard_filter():
    assert ast.system_buy_eligible({"auction_state": "unknown"}) is True
    assert ast.system_buy_eligible({}) is True
    assert ast.system_buy_eligible({"auction_state": "A"}) is True
    assert ast.system_buy_eligible({"auction_state": "B"}) is False
    assert ast.system_buy_eligible({"auction_state": "C"}) is False


# ── ④ manual_only 由最终 view 决定 ────────────────────────────────────────
@pytest.mark.parametrize("route,view,exp", [
    ("manual", "watch", True), ("manual", "skip", True),
    ("manual", "participate", False), ("system", "watch", False),
    ("unknown", "watch", False), (None, "watch", False),
])
def test_manual_only_of(route, view, exp):
    assert ast.manual_only_of({"auction_route": route, "view": view}) is exp


# ── ⑤ 开关可逆 ────────────────────────────────────────────────────────────
def test_hard_filter_is_off_by_default(monkeypatch):
    """自有候选池反证：A 闸会挡掉全部 2 次晋级 → 出厂默认必须只标注。"""
    monkeypatch.delenv("POOL_CALL_A_STATE_ONLY", raising=False)
    assert ast.a_state_only() is False
    r = _run(10.1)                                  # B 态，默认不减仓
    assert r["views"]["600001"]["view"] == "participate"
    assert r["meta"]["auction_state"]["a_state_only"] is False


def test_switches(monkeypatch):
    assert ast.enabled() and ast.a_state_only() and ast.a_state_priority()
    monkeypatch.setenv("POOL_CALL_A_STATE_ONLY", "0")
    assert ast.a_state_only() is False
    monkeypatch.setenv("POOL_CALL_EXPECTED_GAP", "0")
    assert eg.enabled() is False
    monkeypatch.setenv("POOL_CALL_AUCTION_STATE", "0")
    assert ast.enabled() is False
    assert ast.a_state_only() is False      # 总开关关闭时其余一并失效
    assert ast.a_state_priority() is False
    monkeypatch.setenv("POOL_CALL_AUCTION_STATE", "1")
    monkeypatch.setenv("POOL_CALL_A_STATE_PRIORITY", "0")
    assert ast.a_state_priority() is False


# ── ⑥ 冻结主链集成 ────────────────────────────────────────────────────────
def _cand():
    return {"date": "2026-11-04", "environment": {"max_boards": 3},
            "relay": [{"code": "600001", "name": "测试票", "boards": 2,
                       "expectation": {"expected_range": [-1, 6],
                                       "auction_amount_min_yi": 0.1},
                       "theme_name": "测试线", "leader_evidence": {},
                       # 昨日封板质量（09:25 前已冻结）
                       "fbt": "100000", "seal_amt": 0.2, "lt_mktcap": 40.0,
                       "hs": 6.0, "zbc": 0, "is_20cm": False}],
            "watch": [], "low_pool": []}


def _run(price, amount=0.5, prev=None, mode="0", expected=None, cand=None):
    import os
    os.environ["POOL_CALL_A_STATE_ONLY"] = mode
    rows = [{"code": "600001", "cje_yi": 10.0, "boards": 2}]
    quotes = {"600001": {"prev_close": 10.0, "auction_price": price,
                         "auction_amount_yi": amount,
                         "time": "2026-11-05 09:25:03"}}
    kw = {}
    if expected != "auto":           # auto = 走生产路径：由候选质量现算
        kw["expected_gaps"] = {"600001": 2.0} if expected is None else expected
    return pc.call_pool(
        cand or _cand(), quotes, plan={"stance": "attack"}, zt_rows=rows,
        theme_open_rows=rows, prev_amounts={"600001": 10.0} if prev is None else prev,
        line_cores={}, market_open={}, sector_flow={}, zt_series={}, zt_days=[],
        market_max_boards=3, **kw)


def test_hard_filter_blocks_b_and_records_shadow():
    r = _run(10.1, mode="1")                        # +1%，预期 +2% → DEV −1 → B
    v = r["views"]["600001"]
    assert v["auction_state"] == "B"
    assert v["view"] == "watch"
    assert v["manual_only"] is True
    assert v["expected_gap_pct"] == 2.0
    assert v["gap_vs_expected"] == -1.0
    assert r["meta"]["auction_state"]["filtered"] == ["600001"]
    assert r["meta"]["auction_state"]["manual_only"] == ["600001"]
    assert r["meta"]["auction_state"]["a_state_only"] is True


def test_hard_filter_keeps_a():
    r = _run(10.4)                                  # +4%，预期 +2% → DEV +2 → A
    v = r["views"]["600001"]
    assert v["auction_state"] == "A"
    assert v["view"] == "participate"
    assert v["manual_only"] is False
    assert r["meta"]["auction_state"]["system"] == ["600001"]
    assert r["meta"]["auction_state"]["filtered"] == []


def test_shadow_mode_keeps_old_chain():
    r = _run(10.1, mode="0")
    v = r["views"]["600001"]
    assert v["view"] == "participate"
    assert v["manual_only"] is False
    assert r["meta"]["auction_state"]["filtered"] == []


def test_quality_model_is_used_when_no_explicit_expectation():
    """生产路径：不注入时由候选的昨日质量现算，而不是固定开幅区间。"""
    r = _run(10.4, expected="auto")
    v = r["views"]["600001"]
    assert v["expected_gap_pct"] is not None
    assert isinstance(v["gap_vs_expected"], float)
    assert r["meta"]["auction_state"]["expected_gap_covered"] == 1


def test_expected_gap_switch_off_never_guesses(monkeypatch):
    monkeypatch.setenv("POOL_CALL_EXPECTED_GAP", "0")
    r = _run(10.4, expected="auto")                 # 关开关、也不注入
    v = r["views"]["600001"]
    assert v["auction_state"] == "unknown"
    assert v.get("expected_gap_pct") is None
    assert v["view"] == "participate"               # 不裁决 → 旧链照买


def test_missing_prev_amount_is_not_rejected():
    r = _run(10.1, prev={})
    v = r["views"]["600001"]
    assert v["auction_state"] == "unknown"
    assert v["view"] == "participate"              # 缺数据不裁决
    assert v["manual_only"] is False


def test_full_rollback_has_no_new_fields(monkeypatch):
    # 两个总开关一起回退 = 逐字回到 11-05 之前的字段结构与裁决口径。
    monkeypatch.setenv("POOL_CALL_AUCTION_STATE", "0")
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "absolute")
    r = _run(10.1)
    v = r["views"]["600001"]
    assert "auction_state" not in v and "manual_only" not in v
    assert "expected_gap_pct" not in v
    assert r["meta"]["auction_state"]["enabled"] is False
    assert v["view"] == "participate"


def test_no_intraday_confirmation_machinery():
    """用户边界：系统只看竞价，⛔ 不产生待确认态 / 盘中确认字段。"""
    r = _run(10.1)
    blob = str(r)
    for banned in ("pending_confirm", "confirm_at", "intraday_confirm",
                   "minute_bar", "confirm_price"):
        assert banned not in blob


def test_no_fixed_gap_window_literals_in_definition():
    r = _run(10.4)
    definition = r["meta"]["auction_state"]["definition"]
    assert "3<gap" not in definition and "DEV" in definition


def test_meta_counts_cover_all_views():
    r = _run(10.4)
    counts = r["meta"]["auction_state"]["counts"]
    assert sum(counts.values()) == len(r["views"])


def test_apply_to_views_propagates_manual_only():
    r = _run(10.1, mode="1")
    out = pc.apply_to_views(r, {"600001": {"view": "participate"}})
    assert out["600001"]["manual_only"] is True
    assert out["600001"]["auction_state"] == "B"
    assert out["600001"]["expected_gap_pct"] == 2.0


# ── ⑦ 下游统一「不记录」────────────────────────────────────────────────────
def test_inject_pool_views_carries_manual_only():
    r = _run(10.1, mode="1")
    d = pv._inject_pool_views({}, r, _cand())["600001"]
    assert d["manual_only"] is True
    assert d["auction_state"] == "B"
    assert d["expected_gap_pct"] == 2.0


def test_decide_blocks_manual_only():
    ok, note, meta = ac.decide(
        {"view": "watch", "manual_only": True, "auction_state": "B"},
        1.0, expected_range=[-1, 6], auction_amount_yi=0.5, need_yi=0.1)
    assert ok is False and "人工二次机会" in note


def test_decide_does_not_block_unmarked_or_a():
    for row in ({"view": "participate", "manual_only": False, "auction_state": "A"},
                {"view": "participate", "auction_state": "A"},
                {"view": "participate", "manual_only": False, "auction_state": "unknown"}):
        ok, _, _ = ac.decide(row, 4.0, expected_range=[-1, 6],
                             auction_amount_yi=0.5, need_yi=0.1)
        assert ok is True


def test_decide_rechecked_entry_not_blocked_by_layer():
    """翻案票优先：即便被误打 manual_only，也不得二次否决 11-05 翻案链。"""
    row = {"view": "participate", "rechecked_from_observe": True,
           "recheck_class": "state", "manual_only": True, "auction_state": "B"}
    assert tp.rechecked_entry(row) is True
    ok, _, _ = ac.decide(row, 1.0, expected_range=[-1, 6],
                         auction_amount_yi=0.5, need_yi=0.1)
    assert ok is True


def test_ledger_settle_carries_manual_only():
    d = ledger._settle_decision(
        {"code": "600001", "view": "watch"}, "watch",
        {"view": "watch", "manual_only": True, "auction_state": "B"})
    assert d["manual_only"] is True and d["auction_state"] == "B"
    ok, _, _ = ac.decide(d, 1.0, expected_range=[-1, 6],
                         auction_amount_yi=0.5, need_yi=0.1)
    assert ok is False


def test_verify_record_manual_only_has_no_entry(monkeypatch):
    monkeypatch.setattr(pv, "evaluate_realtime_signal", lambda *a, **k: {
        "decision_status": "buy", "signal_status": "super", "gap_pct": 1.0,
        "auction_amount_yi": 0.5, "auction_amount_need_yi": 0.1})
    monkeypatch.setattr(ac, "AI_CALIBER_ACCOUNT", True)
    cand = {"code": "600001", "name": "测试票", "boards": 2,
            "expectation": {"expected_range": [-1, 6], "auction_amount_min_yi": 0.1}}
    quote = {"prev_close": 10.0, "auction_price": 10.1}
    dec = {"600001": {"view": "participate"}}
    base = pv._build_record(cand, quote, 10.5, date10="2026-11-04",
                            ai_decisions=dec, pool_views=None)
    assert base["performance"]["open_check"]["can_enter"] is True
    manual = pv._build_record(
        cand, quote, 10.5, date10="2026-11-04", ai_decisions=dec,
        pool_views={"600001": {"view": "watch", "manual_only": True,
                               "auction_state": "B"}})
    assert manual["performance"]["open_check"]["can_enter"] is False
    assert manual["performance"]["next_day"]["status"] == "skipped"
