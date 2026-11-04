#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封单趋势：锁住「封单变大不是买入理由」这个反直觉结论（2026-11-03）。

## 为什么要有这个文件

NIKA 对青岛食品（001219 · 6 板）的质疑里有一句**事实正确**的话：「封单比昨天还大」。
核验：10-30 封单 0.57 亿 → 11-02 0.72 亿（+26%），占比 1.50% → 1.73%。

若只看这句，会认为系统把它判 watch 是错的。但用 33 个归档日 × 78 个可成交样本测下来，
**方向是反的**：封单越堆越厚，次日越差。

| 分组 | n | 均 | 胜率 |
|---|---|---|---|
| 封单额缩小 >20% | 28 | +0.71% | 53.6% |
| 封单额放大 >20% | 37 | **−1.65%** | **37.8%** |
| 薄<2% ∧ 占比变厚（青岛那一类） | 15 | **−2.19%** | 40.0% |

这与用户既有心法「连板缩量 = 锁筹」同向：封单越堆越厚 = 越多人排队接货。

⛔ 本文件锁的是「结论与方向」。若将来有人把「封单变大」写进买入理由，必须让这里变红。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "wealth_code")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.seal_trend as stv  # noqa: E402


def test_seal_is_amount_not_ratio():
    """⛔ `seal` 是封单额（亿元），不是百分比 —— 这是最容易读错的一个字段。

    实证：青岛食品 11-02 `seal=0.72`、`lt_yi=41.71` → 占比 1.73%（不是 0.72%）。
    """
    row = {"seal": 0.72, "lt_yi": 41.71}
    assert stv.seal_ratio_pct(row) is not None
    assert abs(stv.seal_ratio_pct(row) - 1.73) < 0.01


def test_seal_ratio_missing_is_none():
    """缺流通市值 → None（⛔ 不拿封单额当百分比用）。"""
    assert stv.seal_ratio_pct({"seal": 0.72}) is None
    assert stv.seal_ratio_pct({"seal": 0.72, "lt_yi": 0}) is None
    assert stv.seal_ratio_pct({}) is None


def test_growing_seal_is_worse_than_shrinking():
    """核心结论：封单额放大 >20% 的一组，必须比缩小 >20% 的一组更差。

    若哪天数据翻转（放大组反而更好），说明市场结构变了，需要重新评估本结论。
    """
    stats = stv.group_stats(stv.build()["samples"])
    grow, shrink = stats["amt_grow"], stats["amt_shrink"]
    assert grow["n"] >= 20 and shrink["n"] >= 15, "样本不足，需重跑"
    assert grow["avg"] < shrink["avg"], (
        f"放大 {grow['avg']}% 不再差于缩小 {shrink['avg']}% —— 结论翻转，需重审")
    assert grow["win"] < shrink["win"]


def test_thin_growing_is_the_worst_bucket():
    """薄封单却在变厚（青岛食品那一类）是最差的一组之一。"""
    stats = stv.group_stats(stv.build()["samples"])
    tg = stats["thin_growing"]
    assert tg["n"] >= 10
    assert tg["avg"] < 0, f"薄∧变厚 应仍为负，实际 {tg['avg']}%"


def test_trace_shows_qingdao_seal_collapse():
    """单票轨迹必须能看出：青岛食品 6 板的封单其实是**远低于** 3~4 板的。

    「比昨天还大」是对的（0.57→0.72），但比健康日（10-29 的 1.65 亿）是腰斩的 ——
    比较基准选哪天，会得出完全相反的结论。这条锁住轨迹可回查。
    """
    rows = {r["date"]: r for r in stv.trace("001219")}
    assert "2026-10-29" in rows and "2026-11-02" in rows
    d4, d6 = rows["2026-10-29"], rows["2026-11-02"]
    assert d4["seal_ratio_pct"] > d6["seal_ratio_pct"], "4板封单占比应高于6板"
    assert d6["seal_yi"] > rows["2026-10-30"]["seal_yi"], "对 10-30 确实是变大（NIKA 没说错）"
    assert d6["seal_yi"] < d4["seal_yi"], "但对 10-29 是腰斩"


def test_entry_caliber_matches_ai_caliber():
    """口径单一源：5% 硬线与 ≥2 板门槛必须与生产同值。"""
    assert stv.ENTRY_MAX_GAP == 5.0
    from core import ai_caliber
    assert float(ai_caliber.USER_ENTRY_MAX_GAP_PCT) == stv.ENTRY_MAX_GAP


def test_thin_threshold_matches_defense_gate():
    """薄封单阈值必须与 `defense_gate.DIST_SEAL_RATIO_MAX` 同值，⛔ 不许各写一个。"""
    from core import defense_gate
    assert float(defense_gate.DIST_SEAL_RATIO_MAX) == stv.THICK_THRESHOLD
