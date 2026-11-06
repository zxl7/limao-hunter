#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四条件闸·量能按封板形态分档（2026-11-04 用户「必须补齐」）。

用户原话：「要考虑它是不是一字涨停。昨天龙版一字涨停，这个时候缩量是很正常的，
          除非你是换手，换手的话量能太低就肯定说不过去，比如说今天的紫竹。」

## 锁什么

1. **一字板不因缩量被判量能缺陷**（封死 → 成交额天然小，是结构必然）。
2. **换手板 1~2 板守 5 亿**，即使成交额超过旧的 1.5 亿档位也要拦。
3. **形态未知时不猜**：走原门槛，既不没收也不放宽。
4. **不复用进场闸的豁免结论**：热题材/强封放行进池 ≠ 量能达标，
   ⛔ 否则「换手板缩量」会从后门溜过四条件闸。
5. 开关 `ENABLE_HIGH_WIN_VOLUME_BY_STYLE=0` 逐字回退旧纯数值口径。

## 真实锚点（2026-11-03 候选库 → 11-04 竞价）

| 票 | 形态 | 换手 | 成交额 | 新判 |
|---|---|---|---|---|
| 大晟文化 600892 | 一字 | 1.26% | 0.34 亿 | 量能 PASS |
| 龙版传媒 605577 | 一字 | 0.85% | 0.54 亿 | 量能 PASS |
| 紫竹高科 002058 | 换手 | 8.70% | 2.54 亿 | **缺量能** |
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
        # 封板画像（用于 seal_gate 打分，任一用例可按需覆盖）
        "fbt": "092500", "seal_amt": 2.0, "lt_mktcap": 50.0, "zbc": 0,
    }
    base.update(kw)
    return base


_ENV = {"regime": "risk_on", "relay_feedback": {"pressure": False}}


# ── ① 一字板：缩量不判缺陷 ───────────────────────────────────────────────
def test_yizi_small_turnover_passes_volume():
    """一字板 0.34 亿（大晟文化真实值）→ 量能 PASS。"""
    r = _row(cje_yi=0.34, is_one_word=True, hs=1.26)
    assert "量能" not in pe.high_win_missing(r, _ENV, 4)


def test_yizi_small_turnover_passes_volume_longban():
    """一字板 0.54 亿（龙版传媒真实值）→ 量能 PASS。"""
    r = _row(cje_yi=0.54, is_one_word=True, hs=0.85)
    assert "量能" not in pe.high_win_missing(r, _ENV, 4)


def test_yizi_still_guards_data_sanity_line():
    """⛔ 一字不是「无门槛」：跌破数据防呆线仍拦（挡字段异常，不是流动性判据）。"""
    r = _row(cje_yi=0.01, is_one_word=True)
    assert "量能" in pe.high_win_missing(r, _ENV, 4)


# ── ② 换手板：1~2 板守 5 亿 ──────────────────────────────────────────────
def test_turnover_board_low_volume_blocked():
    """换手板 2.54 亿（紫竹高科真实值）→ 缺量能（旧口径会误判 PASS）。"""
    r = _row(cje_yi=2.54, is_one_word=False, hs=8.70, fbt="094848")
    assert "量能" in pe.high_win_missing(r, _ENV, 4)


def test_turnover_board_enough_volume_passes():
    """换手板 7.88 亿（东方海洋真实值）→ 量能 PASS。"""
    r = _row(cje_yi=7.88, is_one_word=False, hs=14.58, boards=4)
    assert "量能" not in pe.high_win_missing(r, _ENV, 4)


def test_turnover_board_threshold_is_five_yi_not_one_point_five():
    """卡在旧 1.5 亿与 5 亿之间（3 亿）→ 换手板仍缺量能，一字板放行。"""
    assert "量能" in pe.high_win_missing(
        _row(cje_yi=3.0, is_one_word=False), _ENV, 4)
    assert "量能" not in pe.high_win_missing(
        _row(cje_yi=3.0, is_one_word=True), _ENV, 4)


# ── ③ 形态未知：不猜 ────────────────────────────────────────────────────
def test_unknown_style_uses_original_floor():
    """无 `is_one_word` 字段（旧候选）→ 走原档位门槛，不按一字放行。"""
    r = _row(cje_yi=0.54)                    # 1~2 板原门槛 1.5 亿
    r.pop("is_one_word", None)
    assert sg.board_style(r) == "unknown"
    assert "量能" in pe.high_win_missing(r, _ENV, 4)


def test_unknown_style_high_volume_still_passes():
    r = _row(cje_yi=8.0)
    r.pop("is_one_word", None)
    assert "量能" not in pe.high_win_missing(r, _ENV, 4)


# ── ④ 不复用进场闸豁免结论（防后门）────────────────────────────────────
def test_theme_hot_exemption_does_not_bypass_volume_gate():
    """⛔ 热题材（17 家）让票进了池，但换手板缩量仍要在四条件闸被拦。

    这正是「同一事实只判一次」的反面教材：强封/厚封/热题材豁免的理由是
    封板质量与题材家数，**不是「成交额够」**，拿它当量能达标会让缩量换手板溜过。
    """
    r = _row(cje_yi=1.0, boards=4, is_one_word=False, theme_mates_cnt=17,
             seal_gate={"source": "theme_hot"})
    assert "量能" in pe.high_win_missing(r, _ENV, 8)


def test_strong_seal_exemption_does_not_bypass_volume_gate():
    """强封放行进池 ≠ 量能达标：换手板 1 亿仍缺量能；一字板则放行。"""
    assert "量能" in pe.high_win_missing(
        _row(cje_yi=1.0, boards=4, is_one_word=False,
             seal_gate={"source": "strong"}), _ENV, 8)
    assert "量能" not in pe.high_win_missing(
        _row(cje_yi=1.0, boards=4, is_one_word=True,
             seal_gate={"source": "strong"}), _ENV, 8)


# ── ⑤ 开关可逆 ──────────────────────────────────────────────────────────
def test_switch_off_restores_pure_numeric(monkeypatch):
    """`=0` → 逐字回到「cje < 档位门槛」旧口径：一字板缩量重新被判缺量能。"""
    r = _row(cje_yi=0.54, is_one_word=True)
    monkeypatch.setenv(pe.HIGH_WIN_VOLUME_BY_STYLE_ENV, "0")
    assert "量能" in pe.high_win_missing(r, _ENV, 4)


def test_switch_default_on():
    assert pe.high_win_volume_by_style() is True


# ── ⑥ 不误伤其它三条 ────────────────────────────────────────────────────
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
