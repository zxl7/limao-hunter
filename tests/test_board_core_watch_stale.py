#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块核心观察池：不许把「上次涨停的高度」冒充成「今天的高度」（2026-11-16 数据错乱）

实证：603366 日出东方 11-12 五板后断板，11-16 仍以 boards=5 混进观察池，
pct/fbt/seal 全空 —— 页面看起来像「当前 5 板高标」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import board_core_watch as bc  # noqa: E402


def test_picks_expose_last_zt_date():
    picks = bc.picks(set())
    if not picks:
        import pytest
        pytest.skip("当前无核心观察票（依赖 board_core/summary.json）")
    for p in picks:
        assert p.get("last_zt_date"), f"{p['name']} 缺 last_zt_date，无法判断是否断板"


def test_stale_board_is_flagged():
    """断板票：last_zt_date 不等于今天 → 调用方必须把 boards 归零。"""
    picks = bc.picks(set())
    target = next((p for p in picks if p["code"] == "603366"), None)
    if target is None:
        import pytest
        pytest.skip("603366 不在当前核心榜（数据已更新）")
    assert target["last_zt_date"] < "2026-11-16"
    assert target["boards"] == 5      # picks 保留「上次涨停时的高度」
    # ⛔ 这就是错乱的来源：pipeline_eod 注入时必须以「今天在不在池」为准
    assert target["last_zt_date"] == "2026-11-12"
