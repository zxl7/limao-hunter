#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""92科比完整框架落地契约锁（2026-10-21 学习后新增的三组判据）。

锁四件事：
  1. **周期四阶段**与现有 `regime` 是两件事（同一个温度在不同位置含义相反），
     判据要「当日读数 + 方向」两条腿，单腿命中不下结论；
  2. **中位股排除**只在市场高度 ≥5 板时存在中位区，⛔ 高度不够不裁决（否则砍光整条线）；
  3. **涨停时间加速**（越走越早）是龙头确认尺，⛔ 加分不否决；
  4. **爆量检验**必须「先爆量、后缩量」两段齐，缺一不算通过，⛔ 不许把没检验过的叫龙头。
"""
import pytest

from core import anchor_call as ac
from core import cycle_phase as cp


# ── 1) 周期四阶段 ───────────────────────────────────────────────────────
def _fb(**kw):
    base = {"promote_rate": 30.0, "avg_pct": 0.5, "up_rate": 55.0, "now_zt_count": 60,
            "now_max_boards": 5, "prev_max_boards": 5, "nuke_count": 3,
            "broken_rate": 25.0, "break_count": 0, "trend": {"history": []}}
    base.update(kw)
    return base


def test_phase_ebbing_needs_three_legs():
    """主跌要三腿齐：溢价负 + 涨少跌多 + 晋级率低。只中两条不判主跌。"""
    heavy = cp.phase_of(_fb(promote_rate=18, avg_pct=-2.5, up_rate=30))
    assert heavy["phase"] == cp.PHASE_EBBING
    # 只缺一条（涨的还不少）→ 不判主跌
    light = cp.phase_of(_fb(promote_rate=18, avg_pct=-2.5, up_rate=60))
    assert light["phase"] != cp.PHASE_EBBING


def test_phase_advancing_requires_direction_not_just_level():
    """主升：晋级率够高 + 溢价正，且方向不能是在往下掉。"""
    up = cp.phase_of(_fb(promote_rate=35, avg_pct=1.5,
                         trend={"history": [{"pr": 20, "ap": 0.2}, {"pr": 26, "ap": 0.8}]}))
    assert up["phase"] == cp.PHASE_ADVANCING
    down = cp.phase_of(_fb(promote_rate=35, avg_pct=1.5,
                           trend={"history": [{"pr": 55, "ap": 3.0}, {"pr": 45, "ap": 2.2}]}))
    assert down["phase"] != cp.PHASE_ADVANCING


def test_phase_high_volatile_needs_height_and_falling_promote():
    """高位震荡：高度≥5 + 晋级率方向向下 + 炸板多（见顶前兆）。"""
    r = cp.phase_of(_fb(now_max_boards=7, promote_rate=32, avg_pct=1.0, broken_rate=40,
                        trend={"history": [{"pr": 50, "ap": 2.0}, {"pr": 40, "ap": 1.2}]}))
    assert r["phase"] == cp.PHASE_HIGH_VOLATILE


def test_phase_bottom_mentions_where_it_came_from():
    """低位震荡必须写清「从哪修上来的」——否则会被读成「长期趴底」。"""
    r = cp.phase_of(_fb(promote_rate=22, avg_pct=1.0,
                        trend={"history": [{"pr": 16, "ap": -2.1}, {"pr": 18, "ap": -1.6}]}))
    assert r["phase"] == cp.PHASE_BOTTOM
    assert "修复" in r["why"] or "改善" in r["why"]


def test_phase_unknown_on_no_data():
    r = cp.phase_of({})
    assert r["phase"] == cp.PHASE_UNKNOWN


def test_phase_carries_position_and_play():
    """每个阶段都必须带仓位档与手法——文档「周期决定仓位」是核心结论。"""
    for ph in (cp.PHASE_BOTTOM, cp.PHASE_ADVANCING, cp.PHASE_HIGH_VOLATILE,
               cp.PHASE_EBBING, cp.PHASE_UNKNOWN):
        assert ph in cp.PHASE_POSITION and ph in cp.PHASE_PLAY
    assert cp.PHASE_POSITION[cp.PHASE_ADVANCING]["level"] == "heavy"
    assert cp.PHASE_POSITION[cp.PHASE_EBBING]["level"] == "off"
    assert cp.PHASE_POSITION[cp.PHASE_HIGH_VOLATILE]["level"] == "light"


# ── 2) 中位股排除 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("h,band", [(8, (3, 6)), (5, (3, 3)), (7, (3, 5)),
                                    (4, None), (3, None), (None, None)])
def test_mid_band(h, band):
    assert cp.mid_band(h) == band


def test_mid_board_excludes_middle_keeps_high_and_low():
    """H=8：8板（龙头）与 1板（低位）都不在中位，4板在中位。"""
    assert cp.is_mid_board(8, 8) is False
    assert cp.is_mid_board(1, 8) is False
    assert cp.is_mid_board(4, 8) is True
    assert cp.is_mid_board(7, 8) is False        # 高位区 H-1


def test_mid_board_no_verdict_without_height():
    """⛔ 高度缺失或不足 → 一律不裁决（不猜、不误杀）。"""
    assert cp.is_mid_board(4, None) is False
    assert cp.is_mid_board(3, 4) is False


# ── 3) 涨停时间加速 ─────────────────────────────────────────────────────
def test_fbt_acceleration_detects_earlier_and_earlier():
    r = ac.fbt_acceleration([("d1", "144500"), ("d2", "103000"), ("d3", "093500")])
    assert r["trend"] == "accelerating"
    assert r["deltas"] == [-255.0, -55.0]


def test_fbt_acceleration_detects_later_and_later():
    r = ac.fbt_acceleration([("d1", "093500"), ("d2", "103000"), ("d3", "142000")])
    assert r["trend"] == "decelerating"


def test_fbt_acceleration_ignores_small_noise():
    """几分钟的差异属噪声，不能算「提前」（阈值 10 分钟）。"""
    r = ac.fbt_acceleration([("d1", "093500"), ("d2", "093800")])
    assert r["trend"] == "flat"


def test_fbt_acceleration_unknown_with_one_point():
    assert ac.fbt_acceleration([("d1", "093500")])["trend"] == "unknown"
    assert ac.fbt_acceleration([])["trend"] == "unknown"


# ── 4) 爆量检验 ─────────────────────────────────────────────────────────
def test_volume_check_pass_needs_boom_then_cool():
    r = ac.volume_check([("d1", 3.2), ("d2", 7.8), ("d3", 4.1)])
    assert r["passed"] is True and r["boom_ratio"] == pytest.approx(2.44, abs=0.01)


def test_volume_check_fails_without_boom():
    """没有任何一天放量 1.5 倍 → 没做过爆量检验 → 不能确认为龙头。"""
    r = ac.volume_check([("d1", 3.2), ("d2", 3.5), ("d3", 3.1)])
    assert r["passed"] is False
    assert "不能算确认过的龙头" in r["detail"]


def test_volume_check_boom_but_no_cool():
    """爆量了但之后没缩量 → 只做了一半检验，也不算通过。"""
    r = ac.volume_check([("d1", 3.0), ("d2", 6.0), ("d3", 6.5)])
    assert r["passed"] is False and r["cooled"] is False


def test_volume_check_unknown_when_too_short():
    assert ac.volume_check([("d1", 3.0), ("d2", 6.0)])["passed"] is None


# ── 5) 弱转强真伪 ───────────────────────────────────────────────────────
def test_weak_to_strong_gap_up_is_bait():
    """文档 §6.2：昨天封得弱、今天竞价高开 → 做盘嫌疑，不是真弱转强。"""
    r = ac.weak_to_strong({"zbc": 3, "fbt": "142000"},
                          {"prev_close": 10.0, "auction_price": 10.5})
    assert r["applies"] is True and r["confidence"] == "high_bait"
    assert "做盘嫌疑" in r["detail"]


def test_weak_to_strong_flat_is_likely():
    r = ac.weak_to_strong({"zbc": 3, "fbt": "142000"},
                          {"prev_close": 10.0, "auction_price": 10.02})
    assert r["confidence"] == "likely"


def test_weak_to_strong_not_applicable_when_prev_was_strong():
    """昨天封得硬（没炸板、早盘封）→ 不属于弱转强场景，不套这套判据。"""
    r = ac.weak_to_strong({"zbc": 0, "fbt": "093500"},
                          {"prev_close": 10.0, "auction_price": 10.5})
    assert r["applies"] is False


# ── 6) 中位股闸接进 9:25 裁决 ───────────────────────────────────────────
def _c(code, name, boards):
    return {"code": code, "name": name, "boards": boards, "sector": "合成线",
            "bucket_label": "接力池",
            "expectation": {"expected_range": [0.0, 3.0], "auction_amount_min_yi": 0.1}}


def _q(gap=2.0):
    return {"prev_close": 10.0, "auction_price": round(10.0 * (1 + gap / 100), 3),
            "auction_amount_yi": 2.0}


def _run(h=8):
    from core import pool_call
    cands = {"relay": [_c("600001", "龙头票", 8), _c("600002", "中位票", 4),
                       _c("600003", "低位票", 1)], "watch": [],
             "environment": {"max_boards": h}}
    q = {c: _q() for c in ("600001", "600002", "600003")}
    # ⛔ 本文件只测「中位股闸」这一个变量：身位闸（2026-11-02 立）会把 1 板
    #    （身位差 7 板）提前拦成 watch，那是另一道闸，混进来会让本用例的断言
    #    「低位也要参与」失去意义 → 显式关闭，保持单变量。
    _old = pool_call.POOL_CALL_LEADER_ONLY
    pool_call.POOL_CALL_LEADER_ONLY = False
    try:
        return pool_call.call_pool(cands, q, plan={"stance": "attack", "views": {}},
                                   line_cores={})
    finally:
        pool_call.POOL_CALL_LEADER_ONLY = _old


def test_midboard_gate_blocks_middle_keeps_high_and_low():
    r = _run(8)
    v = r["views"]
    assert v["600001"]["view"] == "participate"        # 最高板：要
    assert v["600003"]["view"] == "participate"        # 低位：也要
    assert v["600002"]["view"] == "watch" and v["600002"].get("midboard_gate") is True
    assert "两头不靠" in v["600002"]["reason"]
    assert r["meta"]["defense"]["midboard"]["blocked"] == ["600002"]


def test_midboard_gate_off_when_market_low():
    """市场最高只 4 板 → 没有中位区 → 4 板票照常参与（⛔ 不许砍光整条线）。"""
    r = _run(4)
    assert r["views"]["600002"].get("midboard_gate") is None


def test_midboard_gate_switch_off(monkeypatch):
    from core import pool_call
    monkeypatch.setattr(pool_call, "POOL_CALL_MIDBOARD_GATE", False)
    r = _run(8)
    assert r["views"]["600002"].get("midboard_gate") is None
