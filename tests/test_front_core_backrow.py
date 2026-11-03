#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前排核心分 & 后排接力闸契约锁（2026-10-21 用户贴 92科比 定调）。

来源：用户贴图「选票，往前排核心选；涨停/热度/换手/辨识度/筹码结构/股性；
一定要带板块回流、带板块起爆；连板按 龙头战法＋连板情绪＋核心＋放量＋加速 一起看；
首板第二天一进二先扫描、二板就选出来；看高做低、做高看低」
＋同轮口述「后排接力严格要谨慎！很有可能没有溢价！」

锁定三件事：
  1. 打分只补系统原先没进选股的维度（筹码结构/主力资金/封板加速），缺失一律中性，⛔ 不编造；
  2. 身位判定按「板数相对本线最高板」，⛔ 不按池内名次（同板数不该分出前后排）；
  3. 后排闸只在「前排还在且买得到」时拦 —— 核心断板/一字买不到属「买隔壁」，不拦。
"""
import json

import pytest

from core import front_core as fc
from core import pool_call


# ── 1) 打分：缺失字段→中性；极端值方向正确 ──────────────────────────────
def test_score_neutral_when_all_fields_missing():
    s = fc.score({})
    assert s["score"] == pytest.approx(50.0, abs=0.1)
    assert s["bonus"] == pytest.approx(0.0, abs=0.01)


def test_score_rewards_chip_structure_and_capital():
    """获利盘低 + 筹码集中 + 主力净流入 → 筹码/资金子分应明显高于极端反例。"""
    good = fc.score({"chip_profit_rate": 40.0, "chip_conc90": 10.0,
                     "main_flow_yi": 2.0, "main_flow_5d_yi": 1.0})
    bad = fc.score({"chip_profit_rate": 100.0, "chip_conc90": 30.0,
                    "main_flow_yi": -2.0, "main_flow_5d_yi": -3.0})
    assert good["subs"]["chip"] > bad["subs"]["chip"]
    assert good["subs"]["capital"] > bad["subs"]["capital"]
    assert good["score"] > bad["score"]


def test_score_bonus_is_bounded():
    """加分上限 ±6 —— 不许压过高度档（+8）与历史晋级（±7）。"""
    best = fc.score({"is_sector_dragon": True, "leader_role": "龙头", "boards": 5,
                     "theme_mates_cnt": 8, "carry_cnt": 9, "fbt": "093000", "zbc": 0,
                     "seal_ratio_pct": 5.0, "hs": 10.0, "cje_yi": 30.0,
                     "chip_profit_rate": 40.0, "chip_conc90": 9.0,
                     "main_flow_yi": 5.0, "main_flow_5d_yi": 5.0,
                     "history_relay_prob": 80.0, "history_relay_samples": 30})
    assert best["bonus"] <= fc.FRONT_CORE_BONUS_MAX + 1e-9


def test_score_switch_off_is_neutral(monkeypatch):
    monkeypatch.setenv("FRONT_CORE_ENABLED", "0")
    s = fc.score({"is_sector_dragon": True, "boards": 5, "main_flow_yi": 9.0})
    assert s["enabled"] is False and s["bonus"] == 0.0


# ── 2) 身位：按板数差，不按池内名次 ─────────────────────────────────────
def test_tier_uses_boards_not_pool_rank():
    """同板数的两只票必须同档 —— 池内名次只是排序副产物。"""
    assert fc.row_tier({}, line_max_boards=3, boards=3, line_rank=1) == fc.TIER_FRONT
    assert fc.row_tier({}, line_max_boards=3, boards=3, line_rank=2) == fc.TIER_FRONT
    assert fc.row_tier({}, line_max_boards=3, boards=2, line_rank=1) == fc.TIER_SECOND
    assert fc.row_tier({}, line_max_boards=3, boards=1, line_rank=1) == fc.TIER_BACK


def test_tier_counts_core_outside_pool():
    """池外核心 11 板 → 池内 1 板的首板是后排（10-21 南威软件实况）。"""
    assert fc.row_tier({}, line_max_boards=1, boards=1, core_boards=11) == fc.TIER_BACK


def test_tier_self_core_is_front():
    assert fc.row_tier({}, line_is_core=True) == fc.TIER_FRONT


# ── 3) 后排闸：只在「前排还在」时拦 ─────────────────────────────────────
def _cand(code, name, boards, sector="同一线"):
    return {"code": code, "name": name, "boards": boards, "sector": sector,
            "bucket_label": "接力池",
            "expectation": {"expected_range": [0.0, 3.0], "auction_amount_min_yi": 0.1}}


def _q(gap, amt=2.0):
    return {"prev_close": 10.0, "auction_price": round(10.0 * (1 + gap / 100), 3),
            "auction_amount_yi": amt}


def test_backrow_blocks_when_front_still_available():
    """同线核心仍在板 → 买后排＝买小弟 → 不占名额，理由指名前排。"""
    cores = {"同一线": {"code": "600001", "name": "核心票", "boards": 4, "broken": False}}
    res = pool_call.call_pool(
        {"relay": [_cand("600002", "后排票", 2)], "watch": []},
        {"600002": _q(1.0), "600001": _q(1.0)},
        plan={"stance": "defend", "views": {}}, line_cores=cores)
    v = res["views"]["600002"]
    assert v["view"] == "watch" and v.get("backrow_gate") is True
    assert "核心票" in v["reason"] and "前排" in v["reason"]
    assert (res["meta"]["defense"]["backrow"]["blocked"]) == ["600002"]


def test_backrow_does_not_block_when_core_broken():
    """核心已断板 → 这条线没有前排 → 后排就是唯一入口（买隔壁），⛔ 后排闸不拦。

    ⚠️ 板数差 ≥2 的极端情形由**跟风闸**（⑦·09-21 用户拍板）先拦，那不是后排闸的活；
    这里取「差 1 板」验证后排闸自己的边界。
    """
    cores = {"同一线": {"code": "600001", "name": "核心票", "boards": 2, "broken": True}}
    res = pool_call.call_pool(
        {"relay": [_cand("600002", "后排票", 1)], "watch": []},
        {"600002": _q(1.0)},
        plan={"stance": "defend", "views": {}}, line_cores=cores)
    v = res["views"]["600002"]
    assert v["view"] == "participate"
    assert v.get("backrow_gate") is None
    assert v.get("backrow_note") == "core_broken"


def test_backrow_does_not_block_when_core_unbuyable():
    """核心一字/高开超 5%（买不到）→ 后排是唯一入口，不拦。"""
    cores = {"同一线": {"code": "600001", "name": "核心票", "boards": 5, "broken": False}}
    res = pool_call.call_pool(
        {"relay": [_cand("600002", "后排票", 2)], "watch": []},
        {"600002": _q(1.0), "600001": _q(7.0)},          # 核心高开 +7% > 5% 硬线
        plan={"stance": "defend", "views": {}}, line_cores=cores)
    assert res["views"]["600002"]["view"] == "participate"
    assert res["views"]["600002"].get("backrow_note") == "core_unbuyable"


def test_backrow_switch_off_restores_old_behaviour(monkeypatch):
    monkeypatch.setattr(pool_call, "POOL_CALL_BACKROW_GATE", False)
    cores = {"同一线": {"code": "600001", "name": "核心票", "boards": 4, "broken": False}}
    res = pool_call.call_pool(
        {"relay": [_cand("600002", "后排票", 2)], "watch": []},
        {"600002": _q(1.0), "600001": _q(1.0)},
        plan={"stance": "defend", "views": {}}, line_cores=cores)
    assert res["views"]["600002"]["view"] == "participate"
    assert res["views"]["600002"].get("line_tier") is None


def test_backrow_no_core_data_degrades_to_no_verdict():
    """没有同线核心数据 → 不裁决（诚实降级），⛔ 不靠猜拦票。"""
    res = pool_call.call_pool(
        {"relay": [_cand("600002", "后排票", 2)], "watch": []},
        {"600002": _q(1.0)}, plan={"stance": "defend", "views": {}}, line_cores={})
    assert res["views"]["600002"]["view"] == "participate"
