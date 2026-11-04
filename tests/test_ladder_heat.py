#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""梯次热度：锁住「梯次轮动不成立」这个已验证结论（2026-11-03）。

## 背景

用户转述 NIKA 的建议：「前一天一进二好，多做一进二；前一天二进三全是天地、大面，提示少做」。
这是**梯次轮动**假设。落地前必须验证一件事：**梯次表现存在日间自相关吗？**

`scripts/ladder_heat.py` 用 33 个归档交易日实测：

| 梯次 | 可成交样本 | 日均 | 日胜率 | 自相关 ρ | 判定 |
|---|---|---|---|---|---|
| 1进2 | 581 | −0.41% | 43.1% | **−0.080** | 无关，且本身亏 |
| 2进3 | 54 | −1.48% | 38.4% | +0.194 | 有自相关，但本身最差 |
| 3进4 | 12 | +2.46% | 75.0% | +0.333 | 样本不足（9 天配对） |
| 高位 | 12 | +1.41% | 58.3% | −0.336 | 负相关，样本不足 |

→ **没有一档同时满足「昨日可预示今日 ∧ 本身赚钱 ∧ 样本足够」**，故不做轮动。

⛔ 本文件锁的就是这个「不做」。若将来有人（包括 AI 自己）想加「按昨日梯次排名分配出手权重」，
   必须先让这里的断言变红 —— 逼他拿出新证据，而不是凭直觉加开关。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# ⛔ 不依赖外部 PYTHONPATH：项目根（scripts.*）与 wealth_code（core.*）都要进
for _p in (str(ROOT), str(ROOT / "wealth_code")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.ladder_heat as lh  # noqa: E402


def test_verdict_defaults_to_no_rotation():
    """当前数据下，结论必须是「不做梯次轮动」。"""
    payload = lh.build()
    assert payload["ok"] is True, "归档数据应可用"
    sug = lh.latest_suggestion(payload)
    assert sug["rotatable"] == [], (
        "⛔ 出现了可轮动档，说明数据变了 —— 请先核对 ladder_heat 报告，"
        "确认满足「自相关∧本身赚钱∧样本≥30」三条后才允许改结论")
    assert "不做梯次轮动" in sug["verdict"]


def test_every_tier_has_a_concrete_reason():
    """每个被拒的档都要写清是哪一条不满足 —— ⛔ 不许只给「不行」两个字。"""
    payload = lh.build()
    sug = lh.latest_suggestion(payload)
    why = {r["tier"]: r["why"] for r in sug["not_rotatable"]}
    assert set(why) == set(lh.ORDER)
    for tier, reasons in why.items():
        assert reasons, f"{tier} 没写原因"
        assert all(isinstance(x, str) and x for x in reasons)


def test_first_board_has_no_autocorrelation():
    """1进2 是样本最大的一档（581），自相关必须≈0 —— 这正是推翻「多做一进二」的依据。"""
    payload = lh.build()
    rho = payload["autocorr"]["1进2"]["spearman"]
    assert rho is not None and abs(rho) < 0.15, f"1进2 ρ={rho}，若显著为正则轮动假设复活，需重审"


def test_tier_2to3_is_the_worst_tier():
    """2进3 是全档最差（日均 −1.48%）—— 量化印证 NIKA「二进三全是天地大面」。"""
    bt = lh.build()["by_tier"]
    t23 = bt["2进3"]["avg_ret_days"]
    t12 = bt["1进2"]["avg_ret_days"]
    assert t23 is not None and t12 is not None
    assert t23 < t12 < 0, f"2进3={t23} 1进2={t12}，应由差到好都不为正"


def test_high_tiers_are_positive_but_thin():
    """≥3板 是唯一正收益档，但可成交样本都 <30 —— ⛔ 只认方向，不许当结论。"""
    bt = lh.build()["by_tier"]
    for tier in ("3进4", "高位"):
        assert (bt[tier]["avg_ret_days"] or 0) > 0, f"{tier} 应为正收益档"
        assert bt[tier]["n_fill_total"] < 30, f"{tier} 样本变多了，可以重新评估显著性"


def test_entry_caliber_matches_ai_caliber():
    """口径必须与 ai_caliber 的 5% 硬线同值，⛔ 不许各写一个。"""
    assert lh.ENTRY_MAX_GAP == 5.0
    from core import ai_caliber
    assert float(ai_caliber.USER_ENTRY_MAX_GAP_PCT) == lh.ENTRY_MAX_GAP
