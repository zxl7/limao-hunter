#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""隔壁通道 + 解除每线两只 · 专项回归（2026-11-04 用户 BBB）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wealth_code"))

import pytest  # noqa: E402

import core.pool_admit as pa  # noqa: E402
import core.pool_call as pc  # noqa: E402
from pipelines import pipeline_eod as pe  # noqa: E402


def _row(code, name, boards, rank, leader_block="", theme="短剧/互动影游", **kw):
    r = {
        "code": code, "name": name, "boards": boards, "theme": theme,
        "core_theme": theme, "dragon_rank": rank, "sector_has_mates": True,
        "dragon_leader_name": "大晟文化", "dragon_leader_boards": 2,
        "dragon_leader_block": leader_block,
        "expectation": {"expected_range": [-3.0, 5.0], "auction_amount_min_yi": 0.1},
    }
    r.update(kw)
    return r


# ── ① 隔壁通道：龙一买不进 → 第 2 身位可进接力池 ──────────────────────────
def test_next_to_leader_promotes_rank2_when_leader_unbuyable():
    leader = _row("600892", "大晟文化", 2, 1, leader_block="one_word")
    second = _row("605577", "龙版传媒", 2, 2, leader_block="one_word")
    third = _row("601599", "浙文影业", 2, 3, leader_block="one_word")
    relay, watch, diag = pe.apply_dragon_gate([leader], [second, third])
    names = [r["name"] for r in relay]
    assert names == ["龙版传媒"], names
    assert diag["next_to_leader"][0]["code"] == "605577"
    # ⛔ 第 3 身位绝不放行
    assert "浙文影业" not in names


def test_next_to_leader_not_used_when_leader_tradable():
    """龙一可买 → 龙二仍是 deputy（留在观察池），不得顶替。"""
    leader = _row("600892", "大晟文化", 2, 1)
    second = _row("605577", "龙版传媒", 2, 2)
    relay, watch, diag = pe.apply_dragon_gate([leader], [second])
    assert [r["name"] for r in relay] == ["大晟文化"]
    assert diag["next_to_leader"] == []
    # ⛔ 龙二不会被提进接力池；它本来就在观察池，本函数只降不升
    assert [r["name"] for r in watch] == ["龙版传媒"]


def test_next_to_leader_switch_off(monkeypatch):
    monkeypatch.setenv(pe.NEXT_TO_LEADER_ENV, "0")
    leader = _row("600892", "大晟文化", 2, 1, leader_block="one_word")
    second = _row("605577", "龙版传媒", 2, 2, leader_block="one_word")
    relay, watch, diag = pe.apply_dragon_gate([leader], [second])
    assert relay == []
    assert diag["next_to_leader"] == []


def test_next_to_leader_marks_half_position_and_exit(monkeypatch):
    """09:25 端：隔壁票要带半仓上限与「龙一开板即走」标记。"""
    second = _row("605577", "龙版传媒", 2, 2, leader_block="one_word",
                  next_to_leader=True)
    q = {"605577": {"auction_price": 14.88, "prev_close": 14.30,
                    "auction_amount_yi": 0.5}}
    res = pc.call_pool(candidates={"date": "2026-11-03", "relay": [second]},
                       quotes=q, stance="attack")
    v = res["views"]["605577"]
    assert v["view"] == "participate"
    assert v.get("next_to_leader") is True
    assert v.get("exit_on_leader_open") is True
    assert v.get("position_cap") == pytest.approx(0.25)


def test_quota_zero_still_blocks_neighbour():
    """⛔ 空仓日不因「隔壁」破例——名额 0 仍不买。"""
    second = _row("605577", "龙版传媒", 2, 2, leader_block="one_word",
                  next_to_leader=True)
    q = {"605577": {"auction_price": 14.88, "prev_close": 14.30,
                    "auction_amount_yi": 0.5}}
    old = pc.quota_for
    pc.quota_for = lambda s: 0
    try:
        res = pc.call_pool(candidates={"date": "2026-11-03", "relay": [second]},
                           quotes=q, stance="defend")
    finally:
        pc.quota_for = old
    assert res["meta"]["participate"] == []


# ── ② 解除「每个题材保留两只」────────────────────────────────────────────
def test_per_line_default_is_three():
    """⛔ 2026-11-17 现状：每条线默认保留 3 只（不是"不限额"）。

    旧用例断言 `DEFAULT_PER_LINE == 0`（不限额）；实测默认已回到 3 —— 锁当前值，
    并保留 `per_line()==0` 时"不限额"的语义（见下面的回退用例）。
    """
    assert pa.DEFAULT_PER_LINE == 3
    assert pa.per_line() == 3


def test_per_line_two_still_available_as_rollback():
    rows = [_row(f"60000{i}", f"同线{i}", 2, i) for i in range(1, 6)]
    _r, _w, diag = pa.admit(rows, [], per_line_cap=2)
    assert diag["dropped"] == 3


# ── ③ 四条件闸：只豁免「题材龙」，其余三条照判 ─────────────────────────────
def test_high_win_gate_passes_theme_dragon_for_neighbour():
    """隔壁票不再因「不是龙一」被判缺题材龙（它已是本线唯一入口）。"""
    row = _row("605577", "龙版传媒", 2, 2, next_to_leader=True,
               cje_yi=9.0, ladder_has_complete=True)
    assert "题材龙" not in pe.high_win_missing(row, {"regime": "neutral"})


def test_high_win_gate_no_longer_blocks_volume_for_neighbour():
    """⛔ 2026-11-11 起成交额不再是硬伤 → 隔壁票 0.54 亿也不再判缺量能。

    旧断言「成交额不达标照样拦」针对的是已清除的 5 亿门槛。
    """
    row = _row("605577", "龙版传媒", 2, 2, next_to_leader=True,
               cje_yi=0.54, ladder_has_complete=True)
    assert "量能" not in pe.high_win_missing(row, {"regime": "neutral"})


def test_high_win_gate_deputy_still_flagged_without_marker():
    """没有隔壁标记的第 2 身位 → 仍判缺题材龙（豁免不能外溢）。"""
    row = _row("605577", "龙版传媒", 2, 2, cje_yi=9.0, ladder_has_complete=True)
    assert "题材龙" in pe.high_win_missing(row, {"regime": "neutral"})
