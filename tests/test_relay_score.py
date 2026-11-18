#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价系数（`core.relay_score.auction_multiplier`）回归锁。

背景（2026-11-18）：体系里原有五维盘后评分 `score_relay_candidate`（领涨/容量/
板块联动/梯队/资金），**缺的是竞价阶段的赔率修正**。新增的只有一条：
    终评 = 盘后分 × 竞价系数，系数 ∈ [0.6, 1.0]，**只降不升**。
⛔ 不新增维度、不改原函数，避免又长出一套指标体系。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import relay_score as rs  # noqa: E402


def test_multiplier_bounds():
    """系数恒在 [0.6, 1.0]：再好也不加分，再差也不归零。"""
    perfect = rs.auction_multiplier(gap=1, expected_gap=1, amt_yi=5, need_yi=1)
    worst = rs.auction_multiplier(gap=-10, expected_gap=5, amt_yi=0, need_yi=1)
    assert perfect["mult"] == pytest.approx(1.0)
    # 极端差也只逼近下限（0.6 + 0.4×极小匹配），不会触到 0.6 整
    assert worst["mult"] == pytest.approx(0.6, abs=0.02)


def test_perfect_auction_never_adds_points():
    r = rs.auction_multiplier(gap=0, expected_gap=0, amt_yi=10, need_yi=1)
    assert r["mult"] <= 1.0


def test_near_limit_open_is_penalized():
    """离涨停只剩 1 点 = 上方没肉 → 开幅分 0，系数被压低。"""
    r = rs.auction_multiplier(gap=9.0, expected_gap=2.0, amt_yi=2, need_yi=1)
    assert r["parts"]["gap"] == 0.0
    assert r["mult"] < 0.85
    assert "离涨停" in r["notes"]["gap"]


def test_deep_low_open_is_penalized():
    r = rs.auction_multiplier(gap=-7.0, expected_gap=-1.0, amt_yi=1, need_yi=1)
    assert r["parts"]["gap"] <= 0.1
    assert r["mult"] < 0.8


def test_missing_fields_are_neutral_not_zero():
    """缺预期/量能 → 分项 0.5（中性），⛔ 不能当成"最差"直接压到底。"""
    r = rs.auction_multiplier(gap=None, expected_gap=None, amt_yi=None, need_yi=None)
    assert r["match"] == pytest.approx(0.5)
    assert r["mult"] > 0.6


def test_dev_within_band_scores_high():
    same = rs.auction_multiplier(gap=1.0, expected_gap=1.0, amt_yi=1, need_yi=1)
    deviated = rs.auction_multiplier(gap=1.0, expected_gap=-2.0, amt_yi=1, need_yi=1)
    assert same["parts"]["dev"] > deviated["parts"]["dev"]


def test_after_auction_only_scales_down():
    """端到端：终评 ≤ 盘后分（只降不升）。"""
    cand = {
        "code": "000001", "name": "样本", "theme": "测试", "boards": 2,
        "score": 70.0, "dragon_rank": 1, "theme_mates_cnt": 5,
        "cje_yi": 6.0, "fbt": "093000", "ladder_health": 6.0,
        "ladder_has_complete": True, "sector_is_lone": False,
    }
    env = {"regime": "risk_on", "sentiment_score": 6.0, "max_boards": 3}
    r = rs.score_relay_after_auction(cand, env, gap=1.0, expected_gap=1.0,
                                     amt_yi=2.0, need_yi=1.0)
    assert r["score"] <= r["score_eod"]
    assert 0.6 <= r["auction"]["mult"] <= 1.0
