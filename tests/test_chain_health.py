#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据链体检（2026-11-12）：只读汇总，不参与裁决。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core import chain_health as ch  # noqa: E402


def _pool(**views):
    return {"views": views, "meta": {}}


def test_counts_fired_and_dormant():
    res = ch.build(_pool(
        a={"view": "watch", "reason": "离涨停只剩 1.7 个点 → 上方空间不够"},
        b={"view": "watch", "reason": "同真实题材核心是 X → 只观察"},
        c={"view": "participate", "reason": "符合预期 → 买入"},
    ))
    assert res["fired"].get("space") == 1
    assert res["fired"].get("core") == 1
    assert "空间闸" in res["line"] and "参与 1" in res["line"]
    assert "一字" in res["dormant"]


def test_missing_data_is_reported():
    res = ch.build(_pool(a={"view": "watch", "reason": "x"}))
    assert res["data"]["catalyst"] is False
    assert res["data"]["radar_lines"] == 0
    assert "缺数据" in res["line"]


def test_states_included_when_present():
    res = ch.build(_pool(a={"view": "watch", "reason": "x", "trade_state": "A"}))
    assert res["states"] == {"A": 1}
    assert "三态 A1" in res["line"]
