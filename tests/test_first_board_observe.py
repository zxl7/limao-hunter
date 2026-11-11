#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首板观察判据（2026-11-11 用户「股性强的票，首板就要关注」）。

锁死：
1. 封得干脆（≤10:00 封 + 零炸板）→ 观察；
2. 股性强（近一年涨停 ≥6 次 ∧ 最高连板 ≥2）→ 观察（哪怕封得晚）；
3. 二板及以上、一字、都不沾 → 不进。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from pipelines.pipeline_eod import first_board_observe_reason as why  # noqa: E402


def _row(**kw):
    r = {"boards": 1, "fbt": "095748", "zbc": 0, "is_one_word": False}
    r.update(kw)
    return r


def test_early_seal_gets_observed():
    assert "封板" in (why(_row(fbt="092501"), None) or "")


def test_late_seal_strong_gene_gets_observed():
    """襄阳型：封得晚，但股性强 → 首板也要进观察。"""
    g = {"zt_n": 9, "max_lbc": 5}
    r = why(_row(fbt="133000", zbc=2), g)
    assert r and "股性强" in r


def test_late_seal_weak_gene_skipped():
    assert why(_row(fbt="133000", zbc=2), {"zt_n": 2, "max_lbc": 1}) is None


def test_second_board_never_first_board_observed():
    assert why(_row(boards=2), {"zt_n": 9, "max_lbc": 5}) is None


def test_one_word_skipped():
    assert why(_row(is_one_word=True), {"zt_n": 9, "max_lbc": 5}) is None


def test_missing_gene_is_neutral():
    """没有基因档案 ≠ 股性强：封得晚就只是普通首板。"""
    assert why(_row(fbt="133000", zbc=1), None) is None
