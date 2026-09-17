#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预期区间门（方案 A · 2026-09-17 用户「A 计划」）回归锁。

核心断言：**判据唯一源 + 卡面/账本一致**——
  · `ai_caliber.gap_zone` 是「低于预期/符合预期/超预期」的唯一实现
  · 同一案例，卡面动态结论（`build_cards._ai_dyn_verdict`）与账本裁决
    （`ai_caliber.decide`）必须同向，不得再出现「卡面说观察、账本却买入」。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE / "wealth_code" / "pipelines"))

from core import ai_caliber  # noqa: E402
from build_cards import _ai_dyn_verdict  # noqa: E402


# ── 1) 判据函数三档 + 容差 ────────────────────────────────────────
def test_gap_zone_three_bands():
    assert ai_caliber.gap_zone(5.0, [1.0, 3.0]) == "above"
    assert ai_caliber.gap_zone(2.0, [1.0, 3.0]) == "ok"
    assert ai_caliber.gap_zone(-1.0, [1.0, 3.0]) == "below"


def test_gap_zone_tolerance():
    """容差 0.5pp：超上沿/破下沿 0.5pp 内仍算符合预期（防误杀）。"""
    tol = ai_caliber.EXPECTED_ZONE_TOL
    assert tol == 0.5
    assert ai_caliber.gap_zone(3.4, [1.0, 3.0]) == "ok"
    assert ai_caliber.gap_zone(3.6, [1.0, 3.0]) == "above"
    assert ai_caliber.gap_zone(0.6, [1.0, 3.0]) == "ok"
    assert ai_caliber.gap_zone(0.4, [1.0, 3.0]) == "below"


def test_gap_zone_unknown_degrades():
    """无区间/无竞价 → unknown（调用方回退旧口径·不误伤）。"""
    assert ai_caliber.gap_zone(None, [1.0, 3.0]) == "unknown"
    assert ai_caliber.gap_zone(2.0, None) == "unknown"
    assert ai_caliber.gap_zone(2.0, [1.0]) == "unknown"
    assert ai_caliber.gap_zone("x", [1.0, 3.0]) == "unknown"


# ── 2) 账本裁决：三档 → 买/不买 ───────────────────────────────────
def test_decide_zone_gates():
    led = {"view": "participate", "entry_max_gap": 5.0}
    # 落区间 → 买
    can, _, meta = ai_caliber.decide(led, 2.0, expected_range=[1.0, 3.0])
    assert can is True and meta["zone"] == "ok"
    # 超预期（≤5%·不被 5% 硬线先拦）→ 不买，理由为「高于预期区间」
    can, note, meta = ai_caliber.decide(led, 4.2, expected_range=[1.0, 3.0])
    assert can is False and meta["zone"] == "above" and "高于预期区间" in note
    # 低于预期 → 不买
    can, note, meta = ai_caliber.decide(led, -9.96, expected_range=[0.0, 2.0])
    assert can is False and meta["zone"] == "below" and "低于预期区间" in note
    # 超 5% 硬线优先报（7.33% 同时超线且超预期 → 报 5% 硬线）
    can, note, _ = ai_caliber.decide(led, 7.33, expected_range=[1.4, 3.4])
    assert can is False and "超过 5%" in note


def test_decide_without_range_falls_back():
    """无预期区间 → 回退旧口径（只按 ≤5% 硬线）。"""
    led = {"view": "participate", "entry_max_gap": 2.0}
    can, _, meta = ai_caliber.decide(led, 4.54, expected_range=None)
    assert can is True and meta["zone"] == "unknown"


# ── 3) ⛔ 核心：卡面与账本同向 ────────────────────────────────────
def test_card_and_ledger_agree():
    cases = [
        # (竞价, 预期区间, 账本应买?, 卡面关键字)
        (7.33, [1.4, 3.4], False, "继续观察"),   # 西陇科学（超5% 且超预期）
        (4.20, [1.0, 3.0], False, "继续观察"),   # ≤5% 但超预期 → 仍观察
        (2.00, [1.0, 3.0], True, "符合预期"),
        (-9.96, [0.0, 2.0], False, "放弃"),      # 闽东电力（大幅低开）
    ]
    for gap, rng, expect_buy, kw in cases:
        can, note, meta = ai_caliber.decide(
            {"view": "participate", "entry_max_gap": 5.0}, gap, expected_range=rng)
        it = {"code": "x", "expectation": {"expected_range": rng},
              "performance": {"open_check": {"gap_pct": gap}}}
        dyn = _ai_dyn_verdict(it)
        assert can is expect_buy, (gap, rng, can, note)
        assert dyn is not None and kw in dyn[0], (gap, rng, dyn)
        # 买与"符合预期"、不买与"观察/放弃"必须同向
        assert (dyn[0].find("符合预期") >= 0) is expect_buy, (gap, rng, dyn)
