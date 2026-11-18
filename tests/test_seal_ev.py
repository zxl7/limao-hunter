#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封板期望（`core/seal_ev.py`）回归锁。

口径（2026-11-18 用户）：「我买的是当天能封住涨停的几率」。
    期望 T+1 = P(封板) × 封板溢价(gap) + (1−P) × 未封损失(gap)

三条不变量：
1. 开幅越高 → P(封板) 越高（模型 gap 权重为正，方向不能反）；
2. 封板溢价随开幅递减、未封损失随开幅递增（表本身要单调，别写反）；
3. 缺字段不崩、分项按 0（与训练口径一致），并留痕 `missing`。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import seal_ev as se  # noqa: E402


def test_higher_gap_means_higher_seal_probability():
    low, _ = se.p_seal(gap=-3.0, eod_score=60, mates=5, zbc=0)
    mid, _ = se.p_seal(gap=2.0, eod_score=60, mates=5, zbc=0)
    high, _ = se.p_seal(gap=9.0, eod_score=60, mates=5, zbc=0)
    assert low < mid < high


def test_premium_decreases_and_loss_worsens_with_gap():
    """封板溢价随开幅递减；未封损失随开幅递增（表的方向不能写反）。"""
    prem = [se.prem_loss(g)[0] for g in (-5, -1, 1, 3, 6, 9)]
    loss = [se.prem_loss(g)[1] for g in (-5, -1, 1, 3, 6, 9)]
    # 单调化（PAVA）后：溢价单调不增、未封损失单调不减（负数越来越小）
    assert prem == sorted(prem, reverse=True)
    assert loss == sorted(loss, reverse=True)


def test_expected_value_combines_both_sides():
    r = se.evaluate(gap=9.0, eod_score=80, mates=8, zbc=0)
    expect = r["p_seal"] * r["prem"] + (1 - r["p_seal"]) * r["loss"]
    assert r["ev"] == pytest.approx(expect, abs=0.02)
    assert "封板" in r["why"] and "未封" in r["why"]


def test_missing_fields_do_not_crash_and_are_traced():
    r = se.evaluate(gap=None, eod_score=None)
    assert 0.0 <= r["p_seal"] <= 1.0
    assert "gap" in r["missing"] and "eod_score" in r["missing"]
    assert r["band"] == "缺开幅"


def test_low_gap_has_small_loss_big_premium():
    """低开档：封板溢价最大、未封损失最小（这是"低吸"的理论依据）。"""
    prem, loss, _ = se.prem_loss(-5.0)
    assert prem > 20 and abs(loss) < 1.0


def test_high_gap_has_small_premium_big_loss():
    prem, loss, _ = se.prem_loss(9.0)
    assert prem < 5 and loss < -9


def test_probability_bounds():
    for gap in (-10, 0, 5, 10, 20):
        p, _ = se.p_seal(gap=gap, eod_score=100, mates=20, zbc=20)
        assert 0.0 <= p <= 1.0
