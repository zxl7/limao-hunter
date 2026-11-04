# -*- coding: utf-8 -*-
"""名额下限 2 + 单票 50% 场景（2026-11-04 用户授权）。

用户原话：「竞价交易决策，基于当前逻辑，为减小大盘情绪因子的影响，每日尽量推两个个股。
如果确实只能推一个票，那么买入仓位一定要控死在50%，交易纪律也要同步仓位标准。」

锁死六件事（⛔ 任何一条松动都会让「卡面说一套、账本记另一套」）：
  ① 名额 1 → 2：被情绪/弱档压到 1 的名额在**允许买**时放宽到 2（attack 的 3 不动，
     defend 的 1 也不动——原 stance 就是上限）。
  ② 名额 0 仍 0：冰点 / 环境 block 无回暖 / 方案禁买 → **绝不放宽**，仍空仓。
  ③ 凑不出第二只仍 1 只：名额放宽**不放宽任何单票准入**（5% 硬线 / 量能门原样），
     并且如实落 `single_pick=True`。
  ④ 单票上限 50%：`relay_position.single_pick_cap` = MAX_POSITION_PER_STOCK × 回撤乘数，
     不是强制打满；`RELAY_SINGLE_PICK_CAP=0` 回退。
  ⑤ 两票总仓不放大：仍走 per_pick_cap / total_cap / apply_total_cap。
  ⑥ 三处同值：竞价理由 / 卡面 / 账本 position 的仓位口径一致；观察票不占名额。

全隔离：tmp_path + monkeypatch，不触真实 cache / 台账 / 网络，不推群。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
WC = BASE / "wealth_code"
for _p in (str(WC), str(WC / "pipelines"), str(WC / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import pool_call, relay_position as rp, risk_limits as rl  # noqa: E402
import build_cards as bc  # noqa: E402

_spec = importlib.util.spec_from_file_location("ai_ledger_sp", WC / "scripts" / "ai_ledger.py")
al = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(al)


# ── 夹具 ────────────────────────────────────────────────────────────
def _cand(code, name, boards=1, sector="测试题材", exp=(1.0, 3.0), need=1.0, **kw):
    c = {"code": code, "name": name, "boards": boards, "sector": sector,
         "bucket_label": "接力池",
         "expectation": {"expected_range": list(exp), "auction_amount_min_yi": need}}
    c.update(kw)
    return c


def _q(prev, gap_pct, amt=20.0):
    return {"prev_close": prev,
            "auction_price": round(prev * (1 + gap_pct / 100.0), 3),
            "auction_amount_yi": amt}


# 弱档方案（quota 1 / 单票 30% / 总仓 30%）——「被情绪压到 1」的典型
WEAK_PLAN = {
    "enabled": True, "can_buy": True, "tier": "weak", "tier_label": "弱·收紧",
    "strength": 0.30, "quota": 1, "per_pick_cap": 0.30, "total_cap": 0.30,
    "drawdown_pct": None, "drawdown_level": None, "drawdown_mult": 1.0,
    "theme_confidence": 0.0, "hints": [], "_diag": {},
}


def _call(cands, quotes, *, stance="selective", plan=None):
    return pool_call.call_pool(
        cands, quotes, plan={"stance": stance, "views": {}}, stance=stance,
        line_cores={}, sector_flow={}, zt_series={}, zt_days=[],
        market_open=None, relay_plan=plan)


def _participants(res):
    return [c for c, v in res["views"].items() if v.get("view") == "participate"]


# ── ① 名额 1 → 2 ─────────────────────────────────────────────────────
def test_min_two_slots_widens_weak_quota_one_to_two():
    cands = {"relay": [_cand("000001", "甲票", boards=2),
                       _cand("000002", "乙票", boards=1)], "watch": []}
    quotes = {"000001": _q(10.0, 3.0, amt=5.0), "000002": _q(10.0, 2.0, amt=5.0)}
    res = _call(cands, quotes, plan=WEAK_PLAN)
    sp = res["meta"]["slot_policy"]
    assert res["meta"]["effective_quota"] == 2
    assert len(_participants(res)) == 2
    assert sp["min_two_slots_applied"] is True
    assert sp["requested_quota"] == 2          # selective 原名额
    assert sp["participate_count"] == 2
    assert res["meta"]["single_pick"] is False


def test_min_two_slots_switch_off_reverts():
    """`POOL_CALL_MIN_TWO_SLOTS=False` → 逐字回退旧名额（只有 1 只）。"""
    cands = {"relay": [_cand("000001", "甲票", boards=2),
                       _cand("000002", "乙票", boards=1)], "watch": []}
    quotes = {"000001": _q(10.0, 3.0, amt=5.0), "000002": _q(10.0, 2.0, amt=5.0)}
    old = pool_call.POOL_CALL_MIN_TWO_SLOTS
    pool_call.POOL_CALL_MIN_TWO_SLOTS = False
    try:
        res = _call(cands, quotes, plan=WEAK_PLAN)
    finally:
        pool_call.POOL_CALL_MIN_TWO_SLOTS = old
    assert res["meta"]["effective_quota"] == 1
    assert len(_participants(res)) == 1
    assert res["meta"]["slot_policy"]["min_two_slots_applied"] is False


def test_defend_stance_stays_one_and_attack_keeps_three():
    """原 stance 就是上限：defend 1 只不被放宽；attack 3 只不被压到 2。"""
    codes = [f"{i:06d}" for i in range(1, 6)]
    cands = {"relay": [_cand(c, f"票{c}", boards=5 - i) for i, c in enumerate(codes)],
             "watch": []}
    quotes = {c: _q(10.0, 2.0, amt=5.0) for c in codes}
    d = _call(cands, quotes, stance="defend")
    assert d["meta"]["effective_quota"] == 1 and len(_participants(d)) == 1
    a = _call(cands, quotes, stance="attack")
    assert a["meta"]["effective_quota"] == 3 and len(_participants(a)) == 3


# ── ② 名额 0 绝不放宽 ────────────────────────────────────────────────
def test_quota_zero_never_widened():
    """方案禁买（quota→0）→ 名额仍 0、空仓，⛔ 不因「尽量推两个」放宽。"""
    blocked = dict(WEAK_PLAN, can_buy=False, quota=0, drawdown_mult=0.0,
                   drawdown_pct=30.0, drawdown_level="heavy")
    cands = {"relay": [_cand("000001", "甲票"), _cand("000002", "乙票")], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, amt=5.0), "000002": _q(10.0, 2.0, amt=5.0)}
    res = _call(cands, quotes, plan=blocked)
    assert res["meta"]["effective_quota"] == 0
    assert _participants(res) == []
    assert res["meta"]["slot_policy"]["min_two_slots_applied"] is False
    assert res["meta"]["single_pick"] is False


def test_hibernate_without_relief_stays_zero():
    """hibernate（原 stance 名额 0）+ 竞价未回暖 → 名额 0，绝不放宽到 2。"""
    cands = {"relay": [_cand("000001", "甲票"), _cand("000002", "乙票")], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, amt=5.0), "000002": _q(10.0, 2.0, amt=5.0)}
    res = _call(cands, quotes, stance="hibernate", plan=WEAK_PLAN)
    assert res["meta"]["effective_quota"] == 0 and _participants(res) == []


# ── ③ 凑不出第二只 + 准入不放宽 ──────────────────────────────────────
def test_second_pick_rejected_by_five_percent_line_stays_one():
    """名额放宽 ≠ 放宽 5% 硬线：第二只高开 6.5% 仍 skip，如实只有 1 只。"""
    cands = {"relay": [_cand("000001", "甲票", boards=2),
                       _cand("000002", "乙票", boards=1)], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, amt=5.0), "000002": _q(10.0, 6.5, amt=5.0)}
    res = _call(cands, quotes, plan=WEAK_PLAN)
    assert res["views"]["000002"]["view"] == "skip"
    assert "5%" in res["views"]["000002"]["reason"]
    assert len(_participants(res)) == 1
    assert res["meta"]["slot_policy"]["min_two_slots_applied"] is True   # 名额确实放了
    assert res["meta"]["single_pick"] is True                            # 但只成交 1 只
    assert res["meta"]["slot_policy"]["participate_count"] == 1


def test_second_pick_rejected_by_volume_gate_stays_one():
    """量能门不因名额放宽松动：第二只无量 → watch（不占名额）。"""
    cands = {"relay": [_cand("000001", "甲票", boards=2, need=1.0),
                       _cand("000002", "乙票", boards=1, need=10.0)], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, amt=5.0), "000002": _q(10.0, 2.0, amt=2.0)}
    res = _call(cands, quotes, plan=WEAK_PLAN)
    assert res["views"]["000002"]["view"] == "watch"
    assert len(_participants(res)) == 1 and res["meta"]["single_pick"] is True


# ── ④ 单票上限 50%（含回撤乘数 / 开关）────────────────────────────────
def test_single_pick_cap_is_hard_cap():
    cap = rp.single_pick_cap({"enabled": True, "can_buy": True, "drawdown_mult": 1.0})
    assert cap == pytest.approx(rl.MAX_POSITION_PER_STOCK) == pytest.approx(0.5)


def test_single_pick_cap_keeps_drawdown_multiplier():
    """回撤大时仍收紧：0.5 × 0.2751 = 0.1376（⛔ 不是永远 50%）。"""
    cap = rp.single_pick_cap({"enabled": True, "can_buy": True, "drawdown_mult": 0.2751})
    assert cap == pytest.approx(round(rl.MAX_POSITION_PER_STOCK * 0.2751, 4))
    assert 0.13 < cap < 0.14


def test_single_pick_cap_switch_off(monkeypatch):
    monkeypatch.setenv("RELAY_SINGLE_PICK_CAP", "0")
    assert rp.single_pick_cap({"enabled": True, "can_buy": True, "drawdown_mult": 1.0}) is None
    monkeypatch.setenv("RELAY_SINGLE_PICK_CAP", "1")
    assert rp.single_pick_cap({"enabled": True, "can_buy": True,
                               "drawdown_mult": 1.0}) == pytest.approx(0.5)


def test_single_pick_cap_honest_degrade():
    assert rp.single_pick_cap(None) is None
    assert rp.single_pick_cap({"enabled": False, "can_buy": True}) is None
    assert rp.single_pick_cap({"enabled": True, "can_buy": False}) == 0.0


# ── ⑤ 两票总仓不放大 ─────────────────────────────────────────────────
def test_two_picks_total_cap_not_inflated():
    """名额放宽到 2 后总仓仍 ≤ 档位 total_cap（等比缩放，不偏袒）。"""
    out = rp.apply_total_cap([0.5, 0.5], WEAK_PLAN)
    assert sum(out) == pytest.approx(0.30, abs=1e-9)
    assert out[0] == pytest.approx(out[1])          # 等比，不偏袒


# ── ⑥ 观察票不占名额 / 不计入「只有一只」────────────────────────────────
def test_observe_only_never_counts_or_takes_slot():
    """observe_only 只可能是 watch：既不占名额，也不让「一只」变「两只」。"""
    obs = _cand("000009", "观察核心", boards=3, sector="另一条线")
    obs.update({"participation_eligibility": "observe_only", "watch_only": True,
                "buyable": False, "watch_reason": "板块观察参照，不参与交易"})
    cands = {"relay": [_cand("000001", "甲票", boards=2),
                       _cand("000002", "乙票", boards=1)], "watch": [obs]}
    quotes = {c: _q(10.0, 2.0, amt=5.0) for c in ("000001", "000002", "000009")}
    res = _call(cands, quotes, plan=WEAK_PLAN)
    assert res["views"]["000009"]["view"] == "watch"
    assert res["views"]["000009"]["participation_eligibility"] == "observe_only"
    assert len(_participants(res)) == 2                 # 观察票未被算进名额
    assert res["meta"]["single_pick"] is False


def test_observe_only_with_single_pick_still_single():
    obs = _cand("000009", "观察核心", boards=3, sector="另一条线")
    obs.update({"participation_eligibility": "observe_only", "watch_only": True,
                "buyable": False})
    cands = {"relay": [_cand("000001", "甲票", boards=2)], "watch": [obs]}
    quotes = {"000001": _q(10.0, 2.0, amt=5.0), "000009": _q(10.0, 2.0, amt=5.0)}
    res = _call(cands, quotes, plan=WEAK_PLAN)
    assert res["meta"]["single_pick"] is True
    assert res["views"]["000001"]["position_cap"] == pytest.approx(0.5)


# ── ⑦ 三处同值：竞价理由 / 卡面 / 账本 ───────────────────────────────
def test_single_pick_reason_states_cap():
    """竞价理由必须直说「今日仅1只·单票上限 50%」，与卡面/账本同一个数。"""
    cands = {"relay": [_cand("000001", "甲票")], "watch": []}
    res = _call(cands, {"000001": _q(10.0, 2.0, amt=5.0)}, plan=WEAK_PLAN)
    v = res["views"]["000001"]
    assert v["single_pick"] is True
    assert v["position_cap"] == pytest.approx(0.5)
    assert "今日仅1只·单票上限 50%" in v["reason"]


def _texts(o, out=None):
    out = [] if out is None else out
    if isinstance(o, dict):
        tx = o.get("text")
        if isinstance(tx, dict) and isinstance(tx.get("content"), str):
            out.append(tx["content"])
        for v in o.values():
            _texts(v, out)
    elif isinstance(o, list):
        for v in o:
            _texts(v, out)
    return out


def _plan_for_card(**kw):
    base = dict(strength=0.30, tier="weak", theme_day_count=31,
                theme_info={"day_count": 31, "theme_count": 164, "confidence": 0.5})
    base.update(kw)
    return rp.build_plan(**base)


def test_card_single_pick_shows_50_not_30():
    plan = _plan_for_card(dd=None)
    meta = {"slot_policy": {"single_pick": True, "single_pick_cap": 0.5,
                            "effective_quota": 1, "participate_count": 1}}
    content = bc._relay_position_block(plan, pool_meta=meta)[0]["text"]["content"]
    assert "只买 1 只" in content
    assert "单票上限 50%" in content
    assert "30%" not in content, "单票场景不得再出现弱档 30%"


def test_card_single_pick_pressed_by_drawdown():
    plan = _plan_for_card(dd=19.64)             # ×0.2751 → 13.8%
    cap = rp.single_pick_cap(plan)
    meta = {"slot_policy": {"single_pick": True, "single_pick_cap": cap,
                            "effective_quota": 1, "participate_count": 1}}
    content = bc._relay_position_block(plan, pool_meta=meta)[0]["text"]["content"]
    assert "行情好的话能给到：最多 1 只 · 每只不超过 50%" in content
    assert f"单票上限 {rl.pct_of(cap)}" in content
    assert "按比例收着做" in content


def test_card_two_picks_shows_widened_quota_and_tier_caps():
    plan = _plan_for_card(dd=None)
    meta = {"slot_policy": {"single_pick": False, "single_pick_cap": None,
                            "effective_quota": 2, "participate_count": 2}}
    content = bc._relay_position_block(plan, pool_meta=meta)[0]["text"]["content"]
    assert "最多同时持有 2 只" in content        # 名额放宽后不写 1
    assert "每只不超过 30%" in content
    assert "总仓位不超过 30%" in content        # 总仓不放大


def test_card_without_pool_meta_keeps_old_rendering():
    plan = _plan_for_card(dd=None)
    content = bc._relay_position_block(plan)[0]["text"]["content"]
    assert "最多同时持有 1 只 · 每只不超过 30% · 总仓位不超过 30%" in content


def test_verify_card_uses_frozen_single_pick(monkeypatch):
    """集成锁：竞价验证卡消费冻结 meta 的 single_pick（不是自己重算名额）。"""
    injected = _plan_for_card(dd=None)
    monkeypatch.setattr(rp, "build_plan_from_files", lambda *a, **k: injected)
    verify_result = {
        "summary": {
            "trade_date10": "2026-11-04", "total_candidates": 1, "buy_signals": 1,
            "hit_signals": 0, "covered": 10, "nav_latest": 1.0, "caliber": "ai",
            "mech_buy_signals": 1,
            "pool_call": {"quota": 2, "effective_quota": 1, "pool_size": 2,
                          "participate": ["600693"],
                          "slot_policy": {"single_pick": True, "single_pick_cap": 0.5,
                                          "effective_quota": 1, "participate_count": 1}},
        },
        "payload": {"records": [
            {"code": "600693", "name": "测试A", "theme": "PCB",
             "performance": {"open_check": {"gap_pct": 2.0, "decision_status": "super",
                                            "ai_view": "participate"}}},
        ]},
    }
    card = bc.build_verify_card(verify_result)
    plain = "\n".join(_texts(card))
    assert "单票上限 50%" in plain
    assert "定下买 1 只以内" in plain           # 名额同样用 effective_quota


# ── ⑧ 账本 position 与卡面同值 ───────────────────────────────────────
def _isolate_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(al, "CACHE", tmp_path)
    monkeypatch.setattr(al, "DECISIONS", tmp_path / "ai_decisions.jsonl")
    monkeypatch.setattr(al, "AI_LEDGER", tmp_path / "ai_nav_history.jsonl")
    monkeypatch.setattr(al, "PY_LEDGER", tmp_path / "account_nav_history.jsonl")
    monkeypatch.setattr(al, "AI_VIEW_DIR", tmp_path)
    monkeypatch.setattr(al, "KLINE_DIR", tmp_path / "kline_qfq")
    # 规划期档位固定为弱档（不读真实 cache 的强弱/回撤）
    monkeypatch.setattr(al._rp, "build_plan_from_files",
                        lambda *a, **k: dict(WEAK_PLAN, nav_peak=None, nav_latest=None))


class _A:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _freeze_pool(tmp_path, trade_date, views, meta=None):
    (tmp_path / f"pool_call-{trade_date}.json").write_text(json.dumps({
        "views": views,
        "meta": meta or {"slot_policy": {"single_pick": True, "single_pick_cap": 0.5}},
    }, ensure_ascii=False), encoding="utf-8")


def _picks(tmp_path, rows):
    p = tmp_path / "picks.json"
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_ledger_single_pick_position_reaches_50(monkeypatch, tmp_path):
    """单票场景：账本 position 从弱档 30% 重裁到 50%（与卡面/理由同值）。"""
    _isolate_ledger(monkeypatch, tmp_path)
    f = _picks(tmp_path, [{"code": "000001", "name": "甲票", "reason": "唯一合格",
                           "bias": "focus", "position": 0.5}])
    assert al.cmd_record(_A(decision_date="2026-11-03", file=f,
                            trade_date="2026-11-04")) == 0
    row = al._read_jsonl(al.DECISIONS)[0]
    assert row["position"] == pytest.approx(0.30)          # 规划期仍按弱档 30%
    assert row["position_requested"] == pytest.approx(0.50)

    _freeze_pool(tmp_path, "2026-11-04",
                 {"000001": {"view": "participate", "single_pick": True,
                             "position_cap": 0.5, "reason": "x"}})
    (tmp_path / "auction_quotes-2026-11-04.json").write_text(json.dumps(
        {"000001": {"prev_close": 10.0, "auction_price": 10.2}}), encoding="utf-8")
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.6)
    assert al.cmd_settle(_A(trade_date="2026-11-04")) == 0

    row = al._read_jsonl(al.DECISIONS)[0]
    assert row["position"] == pytest.approx(0.50)          # 账本 = 卡面 50%
    assert row["position_cap"] == pytest.approx(0.50)


def test_ledger_single_pick_does_not_force_full_50(monkeypatch, tmp_path):
    """不是强制打满：AI 只报 20% → 仍记 20%（上限只封顶，不抬仓）。"""
    _isolate_ledger(monkeypatch, tmp_path)
    f = _picks(tmp_path, [{"code": "000001", "name": "甲票", "reason": "唯一合格",
                           "bias": "focus", "position": 0.20}])
    al.cmd_record(_A(decision_date="2026-11-03", file=f, trade_date="2026-11-04"))
    _freeze_pool(tmp_path, "2026-11-04",
                 {"000001": {"view": "participate", "single_pick": True,
                             "position_cap": 0.5}})
    (tmp_path / "auction_quotes-2026-11-04.json").write_text(json.dumps(
        {"000001": {"prev_close": 10.0, "auction_price": 10.2}}), encoding="utf-8")
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.6)
    al.cmd_settle(_A(trade_date="2026-11-04"))
    assert al._read_jsonl(al.DECISIONS)[0]["position"] == pytest.approx(0.20)


def test_ledger_two_picks_keep_tier_caps(monkeypatch, tmp_path):
    """两票场景：不走单票重裁，仍按档位 per_pick/total 等比裁（总仓 30%）。"""
    _isolate_ledger(monkeypatch, tmp_path)
    f = _picks(tmp_path, [
        {"code": "000001", "name": "甲票", "reason": "a", "bias": "focus", "position": 0.5},
        {"code": "000002", "name": "乙票", "reason": "b", "bias": "normal", "position": 0.5},
    ])
    al.cmd_record(_A(decision_date="2026-11-03", file=f, trade_date="2026-11-04"))
    rows = al._read_jsonl(al.DECISIONS)
    assert [r["position"] for r in rows] == [pytest.approx(0.15), pytest.approx(0.15)]
    _freeze_pool(tmp_path, "2026-11-04", {
        "000001": {"view": "participate", "single_pick": False, "position_cap": 0.5},
        "000002": {"view": "participate", "single_pick": False, "position_cap": 0.5},
    })
    (tmp_path / "auction_quotes-2026-11-04.json").write_text(json.dumps(
        {"000001": {"prev_close": 10.0, "auction_price": 10.2},
         "000002": {"prev_close": 10.0, "auction_price": 10.2}}), encoding="utf-8")
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.6)
    al.cmd_settle(_A(trade_date="2026-11-04"))
    rows = al._read_jsonl(al.DECISIONS)
    assert [r["position"] for r in rows] == [pytest.approx(0.15), pytest.approx(0.15)]
    assert all("position_cap" not in r for r in rows), "两票场景不得套用单票上限"


def test_ledger_no_single_marker_keeps_position(monkeypatch, tmp_path):
    """冻结件无 single_pick 标记（开关回退/旧数据）→ 账本 position 原样不动。"""
    _isolate_ledger(monkeypatch, tmp_path)
    f = _picks(tmp_path, [{"code": "000001", "name": "甲票", "reason": "唯一合格",
                           "bias": "focus", "position": 0.5}])
    al.cmd_record(_A(decision_date="2026-11-03", file=f, trade_date="2026-11-04"))
    _freeze_pool(tmp_path, "2026-11-04", {"000001": {"view": "participate"}})
    (tmp_path / "auction_quotes-2026-11-04.json").write_text(json.dumps(
        {"000001": {"prev_close": 10.0, "auction_price": 10.2}}), encoding="utf-8")
    monkeypatch.setattr(al, "_close_of", lambda code, d: 10.6)
    al.cmd_settle(_A(trade_date="2026-11-04"))
    assert al._read_jsonl(al.DECISIONS)[0]["position"] == pytest.approx(0.30)
