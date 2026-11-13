#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块核心观察池（2026-11-13）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import board_core_watch as b  # noqa: E402


def test_picks_are_recent_and_clean():
    p = b.picks()
    assert isinstance(p, list)
    for x in p:
        assert not x["code"].startswith(("688", "689"))
        assert "ST" not in x["name"].upper()
        assert x["core_score"] >= b.MIN_SCORE


def test_exclude_and_cap(monkeypatch):
    monkeypatch.setattr(b, "MAX_N", 2, raising=False)
    p = b.picks()
    assert len(p) <= 2
    if p:
        assert b.picks({p[0]["code"]}) == [] or p[0]["code"] not in {x["code"] for x in b.picks({p[0]["code"]})}


def test_disabled(monkeypatch):
    monkeypatch.setattr(b, "ENABLED", False)
    assert b.picks() == []
