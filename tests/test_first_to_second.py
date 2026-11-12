#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一进二打板候选（2026-11-12 用户「一进二打板·二进三定心」）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import first_to_second_watch as w  # noqa: E402


def test_only_first_boards_and_filters():
    zt = ROOT / "cache" / "zt_history"
    days = sorted(p.stem.replace("zt_", "") for p in zt.glob("zt_*.json"))
    day = days[-1]
    res = w.build(day)
    picks = res["picks"]
    assert picks, "应至少有一进二候选"
    for p in picks:
        assert not p["code"].startswith(("688", "689"))
        assert p["zbc"] <= 2
        assert "ST" not in p["name"].upper()
        # 一字（09:25 封 + 零炸）必须被排除
        assert not (p["fbt"][:4] == "0925" and p["zbc"] == 0)


def test_sorted_by_score():
    zt = ROOT / "cache" / "zt_history"
    day = sorted(p.stem.replace("zt_", "") for p in zt.glob("zt_*.json"))[-1]
    picks = w.build(day)["picks"]
    assert picks == sorted(picks, key=lambda x: -x["score"])
