#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""弱转强（昨日炸板/断板 + 今日竞价企稳 · 2026-11-12）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import weak_to_strong as w  # noqa: E402


def test_broken_yesterday_then_stabilise():
    prev = {"A": {"zbc": 3}}
    r = w.judge("A", "2026-11-13", 5.0, prev=prev)
    assert r["state"] == "弱转强"


def test_too_strong_is_sealed_risk():
    prev = {"A": {"zbc": 2}}
    assert w.judge("A", "2026-11-13", 9.0, prev=prev)["state"] == "过强"


def test_weak_open_not_yet():
    prev = {"A": {"zbc": 1}}
    assert w.judge("A", "2026-11-13", 1.0, prev=prev)["state"] == "未转强"


def test_in_pool_not_applicable():
    prev = {"A": {"zbc": 0}}
    assert w.judge("A", "2026-11-13", 5.0, prev=prev)["state"] == "未知"


def test_missing_quote():
    prev = {"A": {"zbc": 2}}
    assert w.judge("A", "2026-11-13", None, prev=prev)["state"] == "未知"
