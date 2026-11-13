#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""弱转强反包候选（2026-11-13 用户「文投昨天断板今天反包」）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import w2s_watch as w  # noqa: E402


def test_candidates_include_broken_leader():
    cand = w.candidates("2026-11-13")
    codes = {c["code"] for c in cand}
    assert "600715" in codes                    # 文投：前日 3 板 → 昨日断板
    hit = next(c for c in cand if c["code"] == "600715")
    assert "断板" in hit["prev"]
    assert hit["theme"]


def test_candidates_exclude_st_and_star(monkeypatch):
    monkeypatch.setattr(w, "_pool", lambda d: {"600001": {"code": "600001", "name": "*ST测试", "zbc": 2},
                                               "688001": {"code": "688001", "name": "科创", "zbc": 1},
                                               "600002": {"code": "600002", "name": "正常", "zbc": 3}})
    monkeypatch.setattr(w, "_days", lambda: ["2026-11-11", "2026-11-12", "2026-11-13"])
    cand = w.candidates("2026-11-13")
    codes = {c["code"] for c in cand}
    assert "600002" in codes and "600001" not in codes and "688001" not in codes


def test_disabled_switch(monkeypatch):
    monkeypatch.setattr(w, "ENABLED", False)
    assert w.candidates("2026-11-13") == []
