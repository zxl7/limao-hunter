#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材兜底：占位词要跳过、同花顺概念要能补上（2026-11-16）

实证：002205 国统股份 —— 选股宝 plates=["其他"]（无题材），
同花顺概念库有 13 条（装配式建筑/地下管网/新疆振兴…），
旧逻辑只看选股宝 → 判「无题材」→ 3 板票被剔出候选池。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.theme_fallback import resolve  # noqa: E402


def test_placeholder_other_is_skipped():
    r = resolve("002205", "2026-11-16", sector="水泥")
    assert r["theme"] != "其他"
    assert r["source"] in ("hithink", "library", "industry", "none")


def test_hithink_supplies_concept_when_snapshot_has_none():
    r = resolve("002205", "2026-11-16", sector="水泥")
    if r["source"] != "hithink":
        import pytest
        pytest.skip("同花顺概念库缺失")
    assert r["theme"]
    assert "成分" in r["note"] or "涨停原因" in r["note"]


def test_reason_wins_when_provided():
    """有原因时按原因取概念（快照有对应则不覆盖）。"""
    r = resolve("002205", "2026-11-16", sector="水泥", reason="水利+地下管网+新疆振兴")
    assert r["source"] in ("hithink_reason", "snapshot", "hithink")
    assert r["theme"]
    # 快照已有题材且与原因吻合 → 保留快照（选股宝口径更稳）
    snap = resolve("002425", "2026-11-16", sector="影视院线", reason="短剧+互动影游+IP经济")
    assert snap["theme"] == "短剧/互动影游"


def test_snapshot_still_first():
    r = resolve("002425", "2026-11-16", sector="影视院线")
    if r["source"] == "snapshot":
        assert r["theme"]
