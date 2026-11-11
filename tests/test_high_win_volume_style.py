#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四条件闸·量能这一条（2026-11-11 用户「5 亿的限制 pass 掉·全系统清除」后重写）。

## 口径变更（别再把 5 亿加回来）

11-04 曾按封板形态给量能分档：一字板放行（封死→成交额天然小）、换手板 1~2 板守 5 亿
（「换手还缩量=少数资金对倒」）。11-11 用户明确推翻：

> 「这个 5 亿的限制把它 pass 掉吧 不要了 **全系统清除这个限制**」

直接证据（同日盘后）：光通信 10 家全首板、成交额 222.6 亿，其中
**青山纸业 3.29 亿（换手 4.6%）、华脉科技 1.55 亿（换手 5.7%）被 5 亿线误杀** ——
它们是缩量锁筹（筹码没人卖才能缩量封板），不是流动性缺陷。
晋级率与成交额甚至单调负相关（≤1.5 亿 27.5% → ≥10 亿 8.1%），
拿成交额当硬门槛等于系统性地扔掉晋级率最高的那一档。

## 现在锁什么

1. **量能这一条只剩「数据防呆」**：跌破 `DATA_SANITY_MIN_YI`（0.05 亿）才拦，
   挡的是 NaN/0/字段错乱，⛔ 不是流动性判据。
2. 一字 / 换手 / 形态未知 **一律不再因成交额被拦**。
3. 热题材/强封的进场豁免不再需要「防后门」——因为量能本身已不设成交额线。
4. 其余三条（情绪 / 题材龙 / 梯队）判定完全不变。
5. 开关仍在（`ENABLE_HIGH_WIN_VOLUME_BY_STYLE`），但两种口径下都不再卡成交额。

⚠️ 若日后要恢复「量能」这条的区分力，**必须换一个不是成交额的量**（换手率/量比/封单比）
并先做 A/B，⛔ 不许把 5 亿线悄悄加回来。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE))

from core import seal_gate as sg          # noqa: E402
from pipelines import pipeline_eod as pe  # noqa: E402


def _row(**kw):
    """四条件齐备的基准行；用例只破坏想测的那一条。"""
    base = {
        "code": "600892", "name": "测试票", "boards": 2,
        "cje_yi": 8.0, "dragon_rank": 1, "is_sector_dragon": True,
        "sector_has_mates": True, "theme_mates_cnt": 6,
        "sector_is_lone": False, "theme_is_lone": False,
        "ladder_health": 10.0, "ladder_has_complete": True,
        "fbt": "092500", "seal_amt": 2.0, "lt_mktcap": 50.0, "zbc": 0,
    }
    base.update(kw)
    return base


_ENV = {"regime": "risk_on", "relay_feedback": {"pressure": False}}


# ── ① 成交额不再产生「缺量能」────────────────────────────────────────────
def test_yizi_small_turnover_passes_volume():
    """一字板 0.34 亿（大晟文化真实值）→ 量能 PASS。"""
    r = _row(cje_yi=0.34, is_one_word=True, hs=1.26)
    assert "量能" not in pe.high_win_missing(r, _ENV, 4)


def test_turnover_board_small_turnover_also_passes():
    """换手板 2.54 亿（紫竹高科真实值）→ **量能 PASS**（11-11 起不再卡 5 亿）。

    ⛔ 这条是回归锁：哪天有人把 5 亿线加回来，它会立刻变红。
    """
    r = _row(cje_yi=2.54, is_one_word=False, hs=8.70, fbt="094848")
    assert "量能" not in pe.high_win_missing(r, _ENV, 4)


def test_shrink_seal_stocks_pass_volume():
    """11-11 光通信被误杀的两只：青山纸业 3.29 亿 / 华脉科技 1.55 亿 → 量能 PASS。"""
    assert "量能" not in pe.high_win_missing(
        _row(cje_yi=3.29, is_one_word=False, hs=4.6), _ENV, 4)
    assert "量能" not in pe.high_win_missing(
        _row(cje_yi=1.55, is_one_word=False, hs=5.7), _ENV, 4)


def test_data_sanity_line_still_blocks():
    """⛔ 防呆线还在：0.01 亿（疑似字段异常）→ 仍算缺量能。"""
    assert "量能" in pe.high_win_missing(_row(cje_yi=0.01), _ENV, 4)
    assert "量能" in pe.high_win_missing(_row(cje_yi=0.01, is_one_word=True), _ENV, 4)


def test_unknown_style_not_blocked_by_turnover():
    """无 `is_one_word` 字段（旧候选）→ 也不再因成交额被拦。"""
    r = _row(cje_yi=0.54)
    r.pop("is_one_word", None)
    assert sg.board_style(r) == "unknown"
    assert "量能" not in pe.high_win_missing(r, _ENV, 4)


def test_theme_hot_and_strong_seal_no_longer_need_backdoor_guard():
    """热题材/强封的进场豁免不需要「防后门」了 —— 量能本身不设成交额线。"""
    assert "量能" not in pe.high_win_missing(
        _row(cje_yi=1.0, boards=4, is_one_word=False, theme_mates_cnt=17,
             seal_gate={"source": "theme_hot"}), _ENV, 8)
    assert "量能" not in pe.high_win_missing(
        _row(cje_yi=1.0, boards=4, is_one_word=False,
             seal_gate={"source": "strong"}), _ENV, 8)


def test_high_volume_still_passes():
    r = _row(cje_yi=8.0)
    r.pop("is_one_word", None)
    assert "量能" not in pe.high_win_missing(r, _ENV, 4)


# ── ② 开关仍在，但两种口径都不卡成交额 ───────────────────────────────────
def test_switch_off_also_does_not_block(monkeypatch):
    """`=0` 回到纯数值口径 —— 纯数值门槛已是防呆线，故同样不拦。"""
    monkeypatch.setenv(pe.HIGH_WIN_VOLUME_BY_STYLE_ENV, "0")
    assert "量能" not in pe.high_win_missing(_row(cje_yi=0.54, is_one_word=True), _ENV, 4)
    assert "量能" not in pe.high_win_missing(_row(cje_yi=2.54, is_one_word=False), _ENV, 4)
    assert "量能" in pe.high_win_missing(_row(cje_yi=0.01), _ENV, 4)


def test_switch_default_on():
    assert pe.high_win_volume_by_style() is True


# ── ③ 不误伤其它三条 ────────────────────────────────────────────────────
def test_other_conditions_unaffected_by_volume_style():
    """量能放行后，情绪/题材龙/梯队的判定完全不变。"""
    assert pe.high_win_missing(
        _row(cje_yi=0.34, is_one_word=True), {"regime": "ice"}, 4) == ["情绪"]
    assert "题材龙" in pe.high_win_missing(
        _row(cje_yi=0.34, is_one_word=True, dragon_rank=3), _ENV, 4)
    assert "梯队" in pe.high_win_missing(
        # ⚠️ 2026-11-06：1~2 板已免判梯队（见 ENABLE_HIGH_WIN_LADDER_BY_BOARDS），
        #    本用例要验「梯队照判」，故取 3 板（该档仍守 6.0）。
        _row(cje_yi=0.34, is_one_word=True, boards=3,
             ladder_has_complete=False, ladder_health=2.0), _ENV, 4)
