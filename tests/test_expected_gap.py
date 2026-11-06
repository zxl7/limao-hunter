# -*- coding: utf-8 -*-
"""逐票「昨日涨停质量 → 今天该开多少」的口径锁（2026-11-05 用户纠偏）。

用户原话：「根据昨日的涨停的质量（时间，量能，封单大小）计算一个可能的
竞价涨幅！大于 5% 不买倒是真的！」

⛔ 本文件锁的是「预期开幅怎么算」，不是「买了能不能赚」。
⛔ 全隔离：不读盘、不抓行情、不写缓存、不推群。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
for _p in (str(WC), str(WC / "pipelines")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import expected_gap as eg            # noqa: E402
from core import relay_forecast as rf          # noqa: E402


def row(**kw):
    base = {"code": "600001", "fbt": "100000", "seal_amt": 0.5,
            "lt_mktcap": 50.0, "hs": 6.0, "zbc": 0,
            "boards": 2, "is_20cm": False}
    base.update(kw)
    return base


# ── 字段兼容（候选口径 / 涨停归档口径 / 复盘样本口径）────────────────────
@pytest.mark.parametrize("kw,exp_fm", [
    ({"fbt": "092500"}, 565), ({"fbt": "144500"}, 885),
    ({"fbt": "", "fbt_min": 570}, 570),
    ({"fbt": "", "fbt_hm": 930}, 570),
])
def test_fbt_minutes_accepts_all_sources(kw, exp_fm):
    assert eg.fbt_minutes(row(**kw)) == exp_fm


@pytest.mark.parametrize("kw,exp", [
    ({"seal_ratio": 4.2}, 4.2), ({"seal_ratio_pct": 1.4}, 1.4),
    ({"seal_amt": 2.0, "lt_mktcap": 40.0}, 5.0),
    ({"seal_amt": None, "lt_mktcap": None, "seal": 0.7, "lt_yi": 45.36},
     pytest.approx(1.543, abs=0.01)),
])
def test_seal_ratio_accepts_all_sources(kw, exp):
    assert eg.seal_ratio_pct(row(**kw)) == exp


def test_missing_quality_is_not_guessed():
    buckets, missing = eg.quality_buckets({})
    assert buckets == {"is20": "10cm"}
    assert eg.expected_gap_pct({}) is None
    assert eg.expected_gap_pct(None) is None
    # 只有板数 + 20cm 也不算质量证据
    assert eg.expected_gap_pct({"boards": 2, "is_20cm": True}) is None


def test_partial_quality_still_computes_with_two_core_factors():
    e = eg.expected_gap_pct({"fbt": "092500", "hs": 2.0})
    assert isinstance(e, float)
    d = eg.detail({"fbt": "092500", "hs": 2.0})
    assert d["expected_gap_schema"] == eg.SCHEMA
    assert "seal" in d["expected_gap_missing"]


# ── 质量方向单调：强封 > 弱封 ────────────────────────────────────────────
def test_strong_seal_early_low_turnover_expects_higher_gap():
    weak = eg.expected_gap_pct(row(fbt="144500", seal_amt=0.1,
                                   hs=18.0, zbc=3, boards=1))
    strong = eg.expected_gap_pct(row(fbt="092500", seal_amt=3.0,
                                     hs=2.0, zbc=0, boards=4))
    assert strong > weak + 3


def test_expected_gap_is_clipped():
    assert eg.E_MIN <= eg.expected_gap_pct(row()) <= eg.E_MAX
    assert eg.expected_gap_pct(row(fbt="092500", seal_ratio=20,
                                    hs=0.5, zbc=0, boards=8)) <= eg.E_MAX


def test_factors_are_transparent():
    d = eg.detail(row())
    assert d["expected_gap_factors"]["fbt"] == "b<=1000"
    assert d["expected_gap_factors"]["zbc"] == "a0"
    assert d["expected_gap_factors"]["boards"] == "b2"
    assert d["expected_gap_missing"] == []


# ── 相对预期与三档描述 ───────────────────────────────────────────────────
@pytest.mark.parametrize("gap,exp,band", [
    (4.0, 2.0, "above"), (2.5, 2.0, "in_line"),
    (0.0, 2.0, "in_line"), (-0.1, 2.0, "below"),
    (None, 2.0, "unknown"), (4.0, None, "unknown"),
])
def test_deviation_and_band(gap, exp, band):
    assert eg.band(gap, exp) == band
    assert eg.deviation(4.0, 1.5) == 2.5
    assert eg.deviation("x", 1.5) is None


# ── 与既有预测链路共用同一事实源 ────────────────────────────────────────
def test_forecast_one_carries_quality_expectation():
    f = rf.forecast_one(row(), 4, {"groups": {}, "by_boards": {}})
    assert f["expected_gap_pct"] == eg.expected_gap_pct(row())
    assert "seal" in f["expected_gap_factors"]
    # ⛔ 不覆盖既有分组口径：gap_median 仍由 historical groups 决定
    assert "gap_median" in f


def test_switch_off(monkeypatch):
    monkeypatch.setenv("POOL_CALL_EXPECTED_GAP", "0")
    assert eg.enabled() is False
    monkeypatch.setenv("POOL_CALL_EXPECTED_GAP", "1")
    assert eg.enabled() is True
