#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hibernate 竞价逃生阀回归锁（2026-09-18 用户拍板「A」）。

背景（`scripts/ai_self_review.py` 自评）：09-16 / 09-18 两个 hibernate 日全空仓（0 参与），
而执行日候选池均有正收益 → 用户拍板给 hibernate 加「竞价逃生阀」：
hibernate 日不主动买，但若执行日竞价出现「带量转强（0.5%~5%）∧ 方向内板数最高」的**唯一一只**
（且 AI 只判了「观察」）→ 允许小仓试错（0.2 仓）。

⛔ 本测试锁死的边界（防回归）：
  1. 只有 `stance == hibernate` 才开阀（ice / defend / None 一律不开）
  2. `HIBERNATE_VALVE_ENABLED = False` → 一键回退（逐字回到绝对空仓）
  3. 平开（< 0.5%）不算转强；超 5% 硬线不追；无量高开不追
  4. 只对 AI 判「观察」的票开阀（判「放弃」= 更重的否决 → 阀门不越权）
  5. 每日最多 1 只；同方向取板数最高（并列取 score 高）
  6. `decide(valve_hit=True)` 才放行；不放行时逐字回到「AI 判观察 → 不买」
"""
import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE / "wealth_code" / "pipelines"))

from core import ai_caliber as ac  # noqa: E402


def _cand(code, name, *, boards=1, score=50.0, theme="AI算力", need=1.0, mates=4):
    return {
        "code": code, "name": name, "boards": boards, "score": score,
        "theme": theme, "theme_name": theme, "theme_mates_cnt": mates,
        "expectation": {"expected_range": [0.0, 3.0], "auction_amount_min_yi": need},
    }


def _q(gap_pct, *, amount=2.0, prev=10.0):
    return {"prev_close": prev, "auction_price": round(prev * (1 + gap_pct / 100.0), 3),
            "auction_amount_yi": amount}


# ── ① 只有 hibernate 开阀 ────────────────────────────────────────────────────
@pytest.mark.parametrize("stance", ["defend", "selective", "attack", "ice", None, ""])
def test_only_hibernate_opens_valve(stance):
    cands = {"watch": [_cand("600000", "示例A", boards=5)]}
    v = ac.pick_hibernate_valve(cands, {"600000": _q(2.0)}, stance=stance)
    assert v is None


def test_hibernate_with_no_qualified_candidate_returns_none():
    cands = {"watch": [_cand("600000", "示例A")]}
    # 平开（+0.2% < MIN_GAP 0.5）→ 不算转强
    assert ac.pick_hibernate_valve(cands, {"600000": _q(0.2)}, stance="hibernate") is None


# ── ② 可逆开关 ──────────────────────────────────────────────────────────────
def test_kill_switch_disables_valve():
    cands = {"watch": [_cand("600000", "示例A", boards=5)]}
    q = {"600000": _q(2.0)}
    assert ac.pick_hibernate_valve(cands, q, stance="hibernate") is not None
    assert ac.pick_hibernate_valve(cands, q, stance="hibernate", enabled=False) is None
    assert ac.HIBERNATE_VALVE_ENABLED is True or ac.HIBERNATE_VALVE_ENABLED is False  # 常量存在


# ── ③ 转强/硬线/量能三条门槛 ────────────────────────────────────────────────
def test_gap_bounds():
    cands = {"watch": [_cand("600000", "示例A")]}
    assert ac.pick_hibernate_valve(cands, {"600000": _q(-1.0)}, stance="hibernate") is None   # 绿开
    assert ac.pick_hibernate_valve(cands, {"600000": _q(0.4)}, stance="hibernate") is None   # 平开
    assert ac.pick_hibernate_valve(cands, {"600000": _q(0.6)}, stance="hibernate") is not None  # 刚过下沿
    assert ac.pick_hibernate_valve(cands, {"600000": _q(5.01)}, stance="hibernate") is None  # 超硬线
    assert ac.pick_hibernate_valve(cands, {"600000": _q(5.0)}, stance="hibernate") is not None  # 顶格可买


def test_volume_weak_rejected():
    cands = {"watch": [_cand("600000", "示例A", need=2.0)]}
    # 量能 1.0 < 目标 2.0 × 0.7 → 无量高开不追
    assert ac.pick_hibernate_valve(cands, {"600000": _q(2.0, amount=1.0)}, stance="hibernate") is None
    assert ac.pick_hibernate_valve(cands, {"600000": _q(2.0, amount=1.5)}, stance="hibernate") is not None


def test_st_rejected():
    cands = {"watch": [_cand("600000", "*ST示例", boards=9)]}
    assert ac.pick_hibernate_valve(cands, {"600000": _q(3.0)}, stance="hibernate") is None


# ── ④ 只对「观察」开阀 ──────────────────────────────────────────────────────
def test_only_watch_view_eligible():
    cands = {"watch": [_cand("600000", "观察票", boards=3), _cand("600001", "放弃票", boards=6)]}
    q = {"600000": _q(2.0), "600001": _q(2.5)}
    v = ac.pick_hibernate_valve(cands, q, stance="hibernate",
                                views={"600000": "watch", "600001": "skip"})
    assert v is not None and v["code"] == "600000"
    # 判「放弃」的票单独出现 → 不开阀
    assert ac.pick_hibernate_valve(cands, q, stance="hibernate",
                                  views={"600001": "skip", "600000": "skip"}) is None


# ── ⑤ 唯一 1 只 + 方向内板数最高 ────────────────────────────────────────────
def test_pick_one_and_leader_within_theme():
    cands = {"watch": [
        _cand("600000", "同向低板", boards=1, score=99.0, theme="AI算力"),
        _cand("600002", "同向高板", boards=3, score=10.0, theme="AI算力"),
        _cand("600003", "别向高板", boards=4, score=20.0, theme="机器人"),
    ]}
    q = {"600000": _q(2.0), "600002": _q(2.0), "600003": _q(2.0)}
    v = ac.pick_hibernate_valve(cands, q, stance="hibernate",
                                views={c: "watch" for c in q})
    assert v is not None
    assert v["code"] == "600003"                       # 全池板数最高 → 唯一放行
    assert v["position"] == ac.HIBERNATE_VALVE_POSITION
    assert ac.HIBERNATE_VALVE_POSITION <= ac.MAX_POSITION_PER_STOCK
    assert "买一小笔" in v["reason"] and "方向" in v["reason"]   # 大白话理由
    # 排除别向高板后，同方向应取板数最高的那只（而非 score 最高的）
    cands2 = {"watch": cands["watch"][:2]}
    v2 = ac.pick_hibernate_valve(cands2, q, stance="hibernate",
                                 views={c: "watch" for c in q})
    assert v2 is not None and v2["code"] == "600002"


def test_missing_quote_or_expectation_is_safe():
    cands = {"watch": [_cand("600000", "示例A", need=0.0)]}
    assert ac.pick_hibernate_valve(cands, {"600000": _q(2.0)}, stance="hibernate") is None
    assert ac.pick_hibernate_valve(cands, {}, stance="hibernate") is None
    assert ac.pick_hibernate_valve(None, {"600000": _q(2.0)}, stance="hibernate") is None


# ── ⑥ decide 的阀门放行（口径不变：≤5% ∧ 不破预期下沿 ∧ 带量）──────────────
def test_decide_valve_hit_allows_watch():
    d = {"view": "watch", "entry_max_gap": 3.0}
    ok, note, meta = ac.decide(d, 2.0, valve_hit=True)
    assert ok is True and meta.get("via") == "hibernate_valve"
    assert "试错" in note
    # 不放行 → 逐字回到旧行为
    ok2, note2, meta2 = ac.decide(d, 2.0)
    assert ok2 is False and "不买" in note2
    # 阀门也受硬线/区间/量能约束
    assert ac.decide(d, 6.0, valve_hit=True)[0] is False            # 超 5% 硬线
    assert ac.decide(d, -8.0, expected_range=[0.0, 3.0], valve_hit=True)[0] is False  # 破下沿
    assert ac.decide(d, 2.0, auction_amount_yi=0.1, need_yi=2.0, valve_hit=True)[0] is False  # 无量
    assert ac.decide(None, 2.0, valve_hit=True)[0] is False         # 无 AI 决策 → 阀门不越权


# ── ⑦ verify 集成：hibernate 日阀门落到 open_check ──────────────────────────
def test_verify_integration(monkeypatch):
    from pipelines import pipeline_verify as pv
    cands = {
        "date": "2026-09-17", "environment": {"regime": "ice"},
        "relay": [], "watch": [_cand("600000", "示例A", boards=3, theme="AI算力")],
    }
    res = pv.run_verify(
        candidates=cands,
        quotes={"600000": _q(2.0)},
        next_close_map={"600000": 10.0 * 1.02 * 1.08},
        out_nav=False, persist=False,
    )
    rec = res["payload"]["records"][0]
    oc = rec["performance"]["open_check"]
    # 未开阀（无 AI 决策 → caliber=mechanical_fallback）
    assert oc["hibernate_valve"] is False

    monkeypatch.setattr(ac, "load_ai_decisions", lambda _d: {"600000": {"view": "watch"}})
    monkeypatch.setattr(ac, "ai_stance", lambda _d, *_a, **_k: "hibernate")
    res2 = pv.run_verify(
        candidates=cands,
        quotes={"600000": _q(2.0)},
        next_close_map={"600000": 10.0 * 1.02 * 1.08},
        out_nav=False, persist=False,
    )
    oc2 = res2["payload"]["records"][0]["performance"]["open_check"]
    assert oc2["can_enter"] is True and oc2["hibernate_valve"] is True
    assert res2["summary"]["valve_used"] is True if "valve_used" in res2["summary"] else True
    assert res2["summary"]["hibernate_valve"]["code"] == "600000"
    # 关阀后同一数据 → 不成交（回归到「AI 判观察 → 不买」）
    monkeypatch.setattr(ac, "HIBERNATE_VALVE_ENABLED", False)
    res3 = pv.run_verify(
        candidates=cands,
        quotes={"600000": _q(2.0)},
        next_close_map={"600000": 10.0 * 1.02 * 1.08},
        out_nav=False, persist=False,
    )
    assert res3["payload"]["records"][0]["performance"]["open_check"]["can_enter"] is False


# ── ⑧ 卡面标注：阀票必须与普通买入同列且标明「破例·小仓」────────────────────
def test_card_annotation(monkeypatch):
    """AI 判「观察」却成交 → 卡面必须改写标签，否则出现「观察」+ 有持仓的自相矛盾。"""
    from pipelines import build_cards as bc

    rec = {"code": "600000", "name": "示例A", "boards": 5,
           "performance": {"open_check": {"hibernate_valve": True, "gap_pct": 2.0}}}
    raw = {"date": "2026-09-21", "decision_date": "2026-09-18", "stance": "hibernate",
           "stance_label": "空仓待命·一股不买", "views": {"600000": {"view": "watch", "reason": ""}}}
    txt = json.dumps(bc._ai_plan_block(raw, [rec]), ensure_ascii=False)
    assert "破例" in txt and "小仓试错" in txt
    assert "只买 2 成" in txt                       # ⛔ 0.2 → 「2 成」（×10 不是 ×100）
    assert "20 成" not in txt
    # 个股段标签改写（观察 → 破例小仓买）
    line = bc._ai_view_line(rec, {"600000": {"view": "watch", "reason": ""}})
    assert "破例小仓买" in line
    # 非阀票不受影响（逐字回到旧行为）
    rec2 = {"code": "600001", "name": "普通观察", "performance": {"open_check": {"gap_pct": 1.0}}}
    line2 = bc._ai_view_line(rec2, {"600001": {"view": "watch", "reason": ""}})
    assert "破例" not in line2 and "观察" in line2
