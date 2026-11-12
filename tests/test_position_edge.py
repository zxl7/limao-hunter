#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仓位/暴露控制（2026-11-12 吸收 quant-review 四条优点）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core import position_edge as pe  # noqa: E402


def test_edge_tiers():
    assert pe.edge_tier(0.80, 1.2)["tier"] == "满仓区"   # 0.96
    assert pe.edge_tier(0.80, 1.0)["tier"] == "重仓区"   # 0.80
    assert pe.edge_tier(0.50, 1.5)["tier"] == "正常区"   # 0.75
    assert pe.edge_tier(0.40, 1.5)["tier"] == "试错区"   # 0.60
    assert pe.edge_tier(0.30, 1.0)["tier"] == "空仓区"   # 0.30


def test_edge_missing_is_neutral():
    r = pe.edge_tier(None)
    assert r["max_position"] == 0.5 and "不猜" in r["why"]


def test_streak_rule():
    assert pe.streak_coeff([0.01, -0.02, -0.016])["coeff"] == 0.0
    assert pe.streak_coeff([0.01, -0.02, 0.03])["coeff"] == 1.0
    assert pe.streak_coeff([])["coeff"] == 1.0


def test_crash_cap():
    assert pe.crash_cap({"broken_rate": 65, "nuke_count": 10})["cap"] == 0.5
    assert pe.crash_cap({"broken_rate": 65, "nuke_count": 35})["cap"] == 0.0
    assert pe.crash_cap({"broken_rate": 40, "nuke_count": 5})["cap"] == 1.0
    assert pe.crash_cap(None)["cap"] == 1.0


def test_fullpos_requires_data():
    # 缺数据 → 不许满仓（0.8），⛔ 不猜
    assert pe.fullpos_cap()["cap"] == 0.8
    assert pe.fullpos_cap(index_ok=True, sector_ok=True, stock_ok=True)["cap"] == 1.0
    assert pe.fullpos_cap(index_ok=True, sector_ok=False, stock_ok=True)["cap"] == 0.8


def test_combine_takes_min_and_names_driver():
    r = pe.combine(win_rate=0.8, odds=1.2, returns=[0.01, -0.005],
                   fb={"broken_rate": 40, "nuke_count": 5},
                   index_ok=True, sector_ok=True, stock_ok=True)
    # edge=0.96→满仓1.0；streak=1.0；crash=1.0；fullpos=1.0 → 取小 1.0
    assert r["exposure"] == 1.0
