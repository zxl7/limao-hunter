#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主线共振回归（2026-11-16 用户：「整个板块和老龙的共振，预示题材强度」）

用**当天真实存档**做金标准：
- 候选池 `wealth_code/cache/candidates-2026-11-13.json`
- 竞价 `wealth_code/cache/auction_quotes-2026-11-16.json`
- 昨日涨停 `cache/zt_history/zt_2026-11-13.json`

当天的事实：
| 票 | 线 | 竞价 | 旧结果 | 期望 |
|---|---|---|---|---|
| 横店影视 603103 | 短剧/传媒（昨日 7 家） | +4.56% | watch（非核心） | **participate（半仓）** |
| 金辰股份 603396 | 绿电/光伏（昨日 3 家） | −7.45% | participate（错杀档） | **watch** |

共振判定：同线有票 ≥+7% 一字/大高开（金逸 +10.0）+ 老龙红开 + 昨日 ≥3 家 → 3/3。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

CAND = ROOT / "wealth_code" / "cache" / "candidates-2026-11-13.json"
QUOTES = ROOT / "wealth_code" / "cache" / "auction_quotes-2026-11-16.json"
ZT = ROOT / "cache" / "zt_history" / "zt_2026-11-13.json"


@pytest.fixture(scope="module")
def result():
    if not (CAND.exists() and QUOTES.exists() and ZT.exists()):
        pytest.skip("缺少 11-16 竞价存档（该回归只在本地有数据时跑）")
    from core import pool_call as pc
    cand = json.loads(CAND.read_text(encoding="utf-8"))
    quotes = json.loads(QUOTES.read_text(encoding="utf-8"))
    zt = json.loads(ZT.read_text(encoding="utf-8"))["zt_list"]
    return pc.call_pool(cand, quotes, plan={}, zt_rows=zt)


def test_strong_line_non_core_gets_resonance_credit(result):
    """最强线（传媒）的非核心票：共振成立 → 解除「非核心」否决，但仍须过封板期望。

    ⛔ 2026-11-18 契约更新（用户「买的是当天能封住涨停的几率」）：
       共振只负责「身份」这一层，不再等于必买；本例开在 +4.56%（高开、溢价已被吃掉），
       封板期望为负 → 仍然 watch，但**共振留痕必须写出**。
    """
    v = result["views"]["603103"]
    assert isinstance(v.get("sector_resonance"), dict), "共振身份必须留痕"
    assert v["sector_resonance"]["score"] >= 2
    ev = v.get("seal_ev") or {}
    assert isinstance(ev.get("p_seal"), (int, float)), "封板期望必须算出"
    if float(ev.get("ev", 0)) < 0:
        assert v["view"] != "participate", "负期望不得参与"
    else:
        assert v["view"] == "participate" and v["position_cap"] <= 0.5


def test_weak_line_still_blocked(result):
    """弱线（光伏）的深低开仍按走弱处理 —— 共振不能救弱线。"""
    v = result["views"]["603396"]
    assert v["view"] != "participate"


def test_first_board_counts_into_resonance(result):
    """昨日**首板**的竞价也算题材强度证据（用户 11-16：题材强度必须用昨日涨停的板块个股）。

    凯撒文化 002425 昨日首板、不在候选池，但竞价 +10.1%（一字）——
    它在报价文件里，必须被算进「传媒族」的共振，而不是被候选池挡掉。
    """
    q = json.loads(QUOTES.read_text(encoding="utf-8"))
    assert "002425" in q, "首板报价应已冻结进 auction_quotes（full_zt_codes）"
    gap = (q["002425"]["auction_price"] / q["002425"]["prev_close"] - 1) * 100
    assert gap >= 7.0
    assert result["views"]["603103"].get("sector_resonance", {}).get("score", 0) >= 2


def test_off_switch_restores_old_behaviour(monkeypatch):
    """回退开关：关掉后横店应回到 watch（可逆性证明）。"""
    if not (CAND.exists() and QUOTES.exists() and ZT.exists()):
        pytest.skip("缺少 11-16 竞价存档")
    from core import pool_call as pc
    monkeypatch.setattr(pc, "POOL_CALL_SECTOR_RESONANCE", False)
    cand = json.loads(CAND.read_text(encoding="utf-8"))
    quotes = json.loads(QUOTES.read_text(encoding="utf-8"))
    zt = json.loads(ZT.read_text(encoding="utf-8"))["zt_list"]
    res = pc.call_pool(cand, quotes, plan={}, zt_rows=zt)
    assert res["views"]["603103"]["view"] != "participate"
