#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账本口径：收益必须按仓位加权（2026-11-13 用户）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import nav_ledger as nl  # noqa: E402


def _payload(rows):
    return {"records": [
        {"date10": "2026-11-11",
         "performance": {"open_check": {"can_enter": True},
                         "next_day": {"status": "covered", "entry_date": "2026-11-12",
                                      "return_pct": ret}}}
        for ret in rows]}


def test_weighted_uses_position_cap(monkeypatch):
    monkeypatch.setattr(nl, "_load_position_caps",
                        lambda d: {"A": 0.375, "B": 0.5}, raising=False)
    payload = {"records": [
        {"date10": "x", "code": "A",
         "performance": {"open_check": {"can_enter": True},
                         "next_day": {"status": "covered", "entry_date": "2026-11-12",
                                      "return_pct": -8.73}}},
        {"date10": "x", "code": "B",
         "performance": {"open_check": {"can_enter": True},
                         "next_day": {"status": "covered", "entry_date": "2026-11-12",
                                      "return_pct": 2.0}}},
    ]}
    rows = nl._build_incremental_rows_from_payload(payload)
    r = rows[0]
    assert r["weight"] == "position"
    # -8.73*0.375 + 2.0*0.5 = -3.2738 + 1.0 = -2.2738
    assert abs(r["account_return_pct"] - (-2.2738)) < 1e-6
    assert abs(r["avg_return_pct"] - (-3.365)) < 1e-6      # 等权仍在，仅作对照


def test_fallback_when_no_caps(monkeypatch):
    monkeypatch.setattr(nl, "_load_position_caps", lambda d: {}, raising=False)
    rows = nl._build_incremental_rows_from_payload(_payload([-4.0, 2.0]))
    assert rows[0]["weight"] == "equal_fallback"
    assert rows[0]["account_return_pct"] == rows[0]["avg_return_pct"]
