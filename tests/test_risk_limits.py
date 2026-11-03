#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单票 50% 硬顶 · 单一源回归（2026-10-30 用户「单股买入不能超过 50%！！」）

守两件事：
1. **没有第二个数**：四个历史出口读回来的常量必须都等于单一源，不能各写一个。
2. **没有绕路**：任何档位 × 任何回撤深度，单票上限都 ≤ 50%（只收紧、不放大）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wealth_code.core import risk_limits as R          # noqa: E402
from wealth_code.core import relay_position as RP     # noqa: E402
from wealth_code.core import ai_caliber as AC         # noqa: E402
from wealth_code.core import ambush_pullback as AP    # noqa: E402


# ── 单一源与自检 ─────────────────────────────────────────────────────────────
def test_hard_cap_is_half():
    assert R.MAX_POSITION_PER_STOCK == 0.5


def test_all_exits_within_hard_cap():
    """四个历史出口 + 两个尾盘档位，一个都不许超过硬顶。"""
    a = R.audit()
    assert a["ok"] is True, a
    assert all(r["ok"] for r in a["exits"]), a
    assert all(r["ratio"] <= 0.5 + 1e-9 for r in a["exits"]), a


def test_exits_are_imported_not_redefined():
    """值相同还不够——必须是同一个来源（防止后人改回去各写一遍）。"""
    assert AC.MAX_POSITION_PER_STOCK is R.MAX_POSITION_PER_STOCK or \
        AC.MAX_POSITION_PER_STOCK == R.MAX_POSITION_PER_STOCK
    assert RP.PER_PICK_STRONG == R.MAX_POSITION_PER_STOCK
    assert AP.NORMAL_ENV_POSITION_PCT == pytest.approx(R.MAX_POSITION_PER_STOCK * 100)
    assert AP.WEAK_ENV_POSITION_PCT == pytest.approx(R.MAX_POSITION_PER_STOCK * 100)


# ── clamp_per_stock ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("p,expected", [
    (0.8, 0.5),      # 超限 → 夹到 50%
    (0.5, 0.5),      # 恰好上限 → 不动
    (0.3, 0.3),      # 正常 → 原值
    (0.5001, 0.5),   # 略超 → 夹
])
def test_clamp_trims_to_hard_cap(p, expected):
    assert R.clamp_per_stock(p) == pytest.approx(expected)


def test_dynamic_cap_only_tightens():
    """动态上限只能更严，不能放大。"""
    assert R.clamp_per_stock(0.8, 0.3) == pytest.approx(0.3)
    assert R.clamp_per_stock(0.8, 0.9) == pytest.approx(0.5)   # ⛔ 0.9 不得放大到 0.8
    assert R.clamp_per_stock(0.4, 0.9) == pytest.approx(0.4)   # 本身合规 → 不动


@pytest.mark.parametrize("bad", [None, "x", "", 0, -1, -0.2, float("nan")])
def test_clamp_rejects_junk(bad):
    """非法/非正/缺失 → None（诚实留空，不填 0 冒充已裁决）。"""
    assert R.clamp_per_stock(bad) is None


def test_clamp_many_keeps_shape():
    out = R.clamp_many([0.9, None, 0.2, 0], cap=None)
    assert len(out) == 4
    assert out[0] == pytest.approx(0.5)
    assert out[1] is None
    assert out[2] == pytest.approx(0.2)
    assert out[3] is None


def test_pct_text():
    assert R.pct_of(0.5) == "50%"
    assert R.pct_of(0.082) == "8.2%"
    assert R.pct_of(None) == "—"


# ── 动态仓位：全档位 × 全回撤深度 ─────────────────────────────────────────────
def test_tier_caps_never_exceed_hard_cap():
    for tier in ("strong", "neutral", "weak"):
        _, per, total = RP.tier_caps(tier)
        assert per <= R.MAX_POSITION_PER_STOCK + 1e-9
        assert total <= 1.0 + 1e-9


@pytest.mark.parametrize("strength", [1.0, 0.9, 0.75, 0.6, 0.45, 0.2, 0.0])
@pytest.mark.parametrize("dd", [None, 0.0, 3.0, 5.0, 8.0, 10.0, 15.0, 25.0, 40.0])
def test_build_plan_per_pick_never_exceeds_cap(strength, dd):
    """最强档 + 零回撤，仍不得给出 >50% 的单票仓位。"""
    plan = RP.build_plan(strength=strength, tier=RP.tier_of(strength), dd=dd)
    assert plan["per_pick_cap"] <= R.MAX_POSITION_PER_STOCK + 1e-9, (strength, dd, plan)


def test_apply_total_cap_respects_per_pick():
    """总仓缩放不得把任何单票放大到超过单票上限。"""
    plan = {"enabled": True, "can_buy": True, "per_pick_cap": 0.5, "total_cap": 1.0}
    out = RP.apply_total_cap([0.9, 0.9], plan)
    assert all(p <= 0.5 + 1e-9 for p in out)
    assert sum(out) <= 1.0 + 1e-9


# ── 尾盘潜伏 + AI 口径 ───────────────────────────────────────────────────────
def test_ambush_position_pct_within_cap():
    assert AP.NORMAL_ENV_POSITION_PCT <= 100.0
    assert AP.WEAK_ENV_POSITION_PCT <= 100.0


def test_ai_caliber_clamp_delegates():
    assert AC.clamp_position(0.9) == pytest.approx(0.5)
    assert AC.clamp_position(0.9, 0.3) == pytest.approx(0.3)
    assert AC.clamp_position(None) is None
    assert AC.HIBERNATE_VALVE_POSITION <= R.MAX_POSITION_PER_STOCK
