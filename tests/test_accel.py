#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加速/减速（封板时间提前/推后 · 2026-11-12）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import accel  # noqa: E402


def test_unknown_when_not_enough(monkeypatch):
    monkeypatch.setattr(accel, "history", lambda *a, **k: [{"date": "d1", "fbt": 600, "boards": 1}])
    assert accel.accel_of("X", "d2")["state"] == "未知"


def test_accel_when_earlier(monkeypatch):
    monkeypatch.setattr(accel, "history", lambda *a, **k: [
        {"date": "d1", "fbt": 660, "boards": 1}, {"date": "d2", "fbt": 600, "boards": 2}])
    r = accel.accel_of("X", "d2")
    assert r["state"] == "加速" and r["delta_min"] == -60


def test_decel_when_later(monkeypatch):
    monkeypatch.setattr(accel, "history", lambda *a, **k: [
        {"date": "d1", "fbt": 570, "boards": 1}, {"date": "d2", "fbt": 900, "boards": 2}])
    assert accel.accel_of("X", "d2")["state"] == "减速"


def test_flat_within_30min(monkeypatch):
    monkeypatch.setattr(accel, "history", lambda *a, **k: [
        {"date": "d1", "fbt": 600, "boards": 1}, {"date": "d2", "fbt": 610, "boards": 2}])
    assert accel.accel_of("X", "d2")["state"] == "平"


def test_bonus_sign():
    assert accel.rank_bonus("加速") >= 0 and accel.rank_bonus("减速") <= 0
