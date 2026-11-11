#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价广度（2026-11-11）契约测试。

锁定三件事：
1. **规模不够不判**：n < 8 → `applicable=False`（1 只票的水下 100% 没有信息量）；
2. **分层正确**：水下 ≥40% → 分化；中位 < −1% 或水下 ≥50% → 弱；
3. **缺价不编造**：没有竞价 → `median_gap=None`、`level="n/a"`，⛔ 不当成 0。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core.auction_breadth import build_breadth, gap_pct, theme_breadth  # noqa: E402


def q(pct: float, base: float = 10.0) -> dict:
    """造一条竞价：开在 base 的 pct% 上。"""
    return {"prev_close": base, "auction_price": round(base * (1 + pct / 100), 4)}


def members(n: int) -> list[dict]:
    return [{"code": f"{i:06d}"} for i in range(n)]


def quotes_for(codes: list[str], pcts: list[float]) -> dict:
    return {c: q(p) for c, p in zip(codes, pcts)}


def test_gap_pct_handles_missing():
    assert gap_pct(None) is None
    assert gap_pct({"prev_close": 0, "auction_price": 10}) is None
    assert gap_pct({"prev_close": 10, "auction_price": 11}) == 10.0


def test_small_theme_not_judged():
    ms = members(5)
    codes = [m["code"] for m in ms]
    b = theme_breadth(ms, quotes_for(codes, [-3] * 5))
    assert b["applicable"] is False
    assert b["level"] == "n/a"
    assert b["n"] == 5 and b["underwater"] == 5


def test_divergent_when_back_rows_underwater():
    ms = members(10)
    codes = [m["code"] for m in ms]
    # 前五强、后五水下 → 分化
    b = theme_breadth(ms, quotes_for(codes, [3, 3, 3, 3, 3, -1, -2, -2, -1, -3]),
                      front_codes=codes[:5])
    assert b["applicable"] is True
    assert b["underwater"] == 5 and b["under_ratio"] == 0.5
    assert b["level"] == "weak"          # 水下 50% 触发「弱」优先于「分化」
    assert b["median_gap"] is not None


def test_divergent_threshold():
    ms = members(10)
    codes = [m["code"] for m in ms]
    # 4/10 水下（40%）、中位仍为正 → 分化
    b = theme_breadth(ms, quotes_for(codes, [2, 2, 2, 2, 2, 2, -1, -2, -1, -1]),
                      front_codes=codes[:5])
    assert b["under_ratio"] == 0.4
    assert b["level"] == "divergent"
    assert b["front_gap"] is not None and b["divergence"] is not None


def test_no_quotes_is_na():
    ms = members(10)
    b = theme_breadth(ms, {})
    assert b["level"] == "n/a" and b["median_gap"] is None


def test_build_breadth_sorted_by_size():
    themes = {"大": members(9), "小": members(2)}
    codes_big = [m["code"] for m in themes["大"]]
    quotes = {c: q(1.0) for c in codes_big}
    d = build_breadth("2026-11-11", themes, quotes)
    assert d["schema"] == "auction_breadth_v1"
    assert [t["theme"] for t in d["themes"]] == ["大", "小"]
    assert d["themes"][1]["applicable"] is False


def test_partial_quote_coverage_not_judged():
    """竞价只覆盖一部分 → 不判广度（1/2=50% 这种百分比纯属误导）。"""
    ms = members(10)
    codes = [m["code"] for m in ms]
    quotes = {c: q(-2.0) for c in codes[:5]}      # 只覆盖 5/10
    b = theme_breadth(ms, quotes)
    assert b["covered"] == 5 and b["cover_ratio"] == 0.5
    assert b["applicable"] is False
    assert b["level"] == "n/a"
    assert "覆盖" in b["note"]


def test_full_coverage_judged():
    ms = members(10)
    codes = [m["code"] for m in ms]
    b = theme_breadth(ms, {c: q(1.0) for c in codes})
    assert b["cover_ratio"] == 1.0 and b["applicable"] is True and b["level"] == "ok"
