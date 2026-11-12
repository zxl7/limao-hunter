#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二波高位节点（2026-11-12 用户口径：近一个月第一波走完，第二波三板危险）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core import second_wave as sw  # noqa: E402


def test_guangyang_is_second_wave():
    """光洋 002708：3 板 + 前 20 日 4 次涨停（10-26~10-29 第一波）→ 命中。"""
    hit = sw.second_wave(3, "002708")
    assert hit and hit["prior"] >= 3


def test_dadongfang_is_first_wave():
    """大东方 600327：4 板但前 20 日只有 1 次涨停 → 第一波，不命中。"""
    assert sw.second_wave(4, "600327") is None


def test_two_boards_not_enough():
    """2 板不算节点（用户口径是 3 板）。"""
    assert sw.second_wave(2, "002708") is None


def test_missing_kline_is_neutral():
    """缺 K 线 → None（缺数据不判，⛔ 不当成第二波）。"""
    assert sw.prior_wave_zt("999999") is None
    assert sw.second_wave(3, "999999") is None
