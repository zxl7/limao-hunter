#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块龙一判定 · 契约锁（2026-10-27 用户「推的票必须是板块龙一」）。

## 用户原话

> 「推的票 必须是板块龙一」

## 本文件锁死的性质

1. **分组按炒作题材**，⛔ 不按申万行业（行业内每只都可能成"龙一"＝票太多的病根）。
2. **判据分层**：板数 → 封板时段档 ±5 分钟 → 封单/流通% → 带动家数 → 更早封板 → 代码。
   ⛔ 加权求和（旧实现用成交额参与排序，把 8.1 亿的多伦顶到龙一，而卡面推的是赛福天）。
3. **秒差不等于强弱**：同一批集合竞价封板（相差 1 秒）→ 同档，改比封单厚度。
4. **没有板块就没有龙一**：题材缺失 / 涨停独苗（< mates_min 家）→ `dragon_rank=0`，
   ⛔ 不是"它是自己那一家的龙一"。
5. **缺数据不编造**：封单比缺失 → 该维不参与比较并标 `seal_ratio_missing`，⛔ 不当 0。
6. **准入闸只放龙一**：`dragon_rank != 1` → 移出接力池；龙一一字/ST 买不进 → 同线不拿跟风顶替。
7. **单一源**：`pool_admit.strength_key` 必须与 `sector_leader.leader_key` 同源同序。
8. **可逆**：`ENABLE_SECTOR_DRAGON_ONLY=0` → 逐字节回到旧池。
"""
import copy
import os

import pytest

import core.pool_admit as pa
import core.sector_leader as sl
from pipelines.pipeline_eod import apply_dragon_gate


def _row(code, name, boards, fbt, seal_yi=1.0, lt_yi=100.0, theme="机器人",
         carry=0, **kw):
    r = {"code": code, "name": name, "boards": boards, "fbt": fbt,
         "seal": seal_yi, "lt_yi": lt_yi, "theme_em_name": theme,
         "carry_cnt": carry}
    r.update(kw)
    return r


def _ranks(res, theme="机器人"):
    b = next(x for x in res["boards"] if x["theme"] == theme)
    return {x["code"]: x["dragon_rank"] for x in b["dragon_ranks"]}


# ══════════════════════════════════════════════════════════════════════════
# 1. 判据分层
# ══════════════════════════════════════════════════════════════════════════

def test_same_band_thicker_seal_wins():
    """10-27 实测场景：三只 4 板，多伦 09:25:01 封单 0.24%、赛福天 09:25:02 封 0.85%。

    差 1 秒没有信息量（同一批竞价秒板）→ 同档 → 比封单厚度 → 赛福天是龙一。
    ⛔ 旧实现用成交额排（多伦 8.1 亿 > 赛福天 3.02 亿）→ 判多伦，与卡面推的票打架。
    """
    rows = [
        _row("603528", "多伦科技", 4, "092501", seal_yi=0.17, lt_yi=69.53),
        _row("603028", "赛福天", 4, "092502", seal_yi=0.25, lt_yi=29.29),
        _row("000678", "襄阳轴承", 4, "093409", seal_yi=1.28, lt_yi=73.58),
    ]
    res = sl.compute(rows, {})
    ranks = _ranks(res)
    assert ranks["603028"] == 1          # 同档封单最厚
    assert ranks["603528"] == 2
    assert ranks["000678"] == 3          # 晚一档，即便封单更厚也排在后面


def test_earlier_band_wins_over_thicker_seal_in_later_band():
    """封得早 = 主动走强：09:30 封的薄封，仍排在 09:40 封的厚封之前（同板数）。"""
    rows = [
        _row("A", "早封薄单", 3, "093000", seal_yi=0.1, lt_yi=100),
        _row("B", "晚封厚单", 3, "094000", seal_yi=8.0, lt_yi=100),
    ]
    assert _ranks(sl.compute(rows, {}))["A"] == 1


def test_boards_dominate_everything():
    """板数第一：低板票封得再早、封得再厚，也不能顶掉高板票。"""
    rows = [
        _row("LOW", "低板", 2, "092500", seal_yi=9.0, lt_yi=100),
        _row("HIGH", "高板", 4, "145500", seal_yi=0.1, lt_yi=100),
    ]
    assert _ranks(sl.compute(rows, {}))["HIGH"] == 1


def test_carry_breaks_tie_within_same_band_and_seal():
    """同档、同封单 → 带动家数多者为龙一（谁点火了这条线）。"""
    rows = [
        _row("X", "无带动", 3, "092500", seal_yi=1.0, lt_yi=100, carry=0),
        _row("Y", "有带动", 3, "092501", seal_yi=1.0, lt_yi=100, carry=6),
    ]
    assert _ranks(sl.compute(rows, {}))["Y"] == 1


# ══════════════════════════════════════════════════════════════════════════
# 2. 「没有板块就没有龙一」
# ══════════════════════════════════════════════════════════════════════════

def test_lone_stock_is_not_a_dragon():
    """单独 1 家涨停 ≠ 板块龙一（mates_min 默认 2）。"""
    rows = [_row("S", "独苗", 3, "092500", theme="孤军题材")]
    res = sl.compute(rows, {})
    assert res["by_code"]["S"]["dragon_rank"] == 0
    assert res["by_code"]["S"]["is_sector_dragon"] is False
    assert res["by_code"]["S"]["has_sector"] is False
    assert res["boards"] and res["boards"][0]["has_sector"] is False


def test_theme_missing_group_not_judged():
    """取不到题材 = 数据缺失，⛔ 不判龙一（缺数据不当不利证据，也不给优势）。"""
    rows = [{"code": "M1", "name": "甲", "boards": 2, "fbt": "093000"},
            {"code": "M2", "name": "乙", "boards": 1, "fbt": "100000"}]
    res = sl.compute(rows, {})
    assert res["by_code"]["M1"]["dragon_rank"] == 0
    assert all(b["has_sector"] is False for b in res["boards"])


def test_theme_map_supports_both_shapes():
    """题材快照 `{code:{theme}}` 与 code2themes `{code:[...]}` 两种形态都要吃得下。"""
    rows = [{"code": "P1", "name": "甲", "boards": 2, "fbt": "093000"},
            {"code": "P2", "name": "乙", "boards": 1, "fbt": "100000"}]
    m1 = {"P1": {"theme": "机器人"}, "P2": {"theme": "机器人"}}
    m2 = {"P1": ["机器人", "人形机器人"], "P2": ["机器人"]}
    for m in (m1, m2):
        res = sl.compute(rows, m)
        assert res["by_code"]["P1"]["is_sector_dragon"] is True
        assert res["by_code"]["P1"]["theme"] == "机器人"


# ══════════════════════════════════════════════════════════════════════════
# 3. 缺数据不编造
# ══════════════════════════════════════════════════════════════════════════

def test_missing_seal_ratio_is_flagged_not_zeroed():
    """封单比缺失 → `seal_ratio_pct=None` + `seal_ratio_missing=True`，⛔ 不写成 0。"""
    rows = [
        {"code": "N1", "name": "无封单数据", "boards": 3, "fbt": "092500",
         "theme_em_name": "机器人"},
        _row("N2", "有封单", 3, "092500", seal_yi=2.0, lt_yi=100),
    ]
    res = sl.compute(rows, {})
    assert res["by_code"]["N1"]["seal_ratio_pct"] is None
    assert res["by_code"]["N1"]["seal_ratio_missing"] is True
    # 有数据者优先（缺失不给优势），这是唯一的"惩罚"，且只发生在同档同板数时
    assert res["by_code"]["N2"]["dragon_rank"] == 1


def test_missing_fbt_does_not_crash():
    rows = [_row("F1", "无时间", 2, ""), _row("F2", "有时间", 2, "093000")]
    res = sl.compute(rows, {})
    assert res["by_code"]["F2"]["dragon_rank"] == 1
    assert res["by_code"]["F1"]["dragon_rank"] == 2


def test_deterministic():
    """同一输入两次结果逐字节一致（用户「可复现」铁律）。"""
    rows = [
        _row("D1", "甲", 3, "092500", seal_yi=1.0, lt_yi=100),
        _row("D2", "乙", 3, "092500", seal_yi=1.0, lt_yi=100),
    ]
    a = sl.compute(rows, {})
    b = sl.compute(copy.deepcopy(rows), {})
    assert _ranks(a) == _ranks(b)


# ══════════════════════════════════════════════════════════════════════════
# 4. 龙一准入闸
# ══════════════════════════════════════════════════════════════════════════

def _with_dragon(row, rank, leader_code="LEAD", leader_name="龙头",
                 block="", has_sector=True):
    r = dict(row)
    r.update({
        "dragon_rank": rank, "is_sector_dragon": rank == 1,
        "sector_has_mates": has_sector,
        "dragon_leader_code": leader_code, "dragon_leader_name": leader_name,
        "dragon_leader_boards": 4, "dragon_leader_block": block,
    })
    return r


def test_gate_demotes_deputy_to_watch():
    relay = [_with_dragon(_row("L", "龙一", 4, "092500"), 1, "L", "龙一"),
             _with_dragon(_row("D", "龙二", 4, "092501"), 2, "L", "龙一")]
    keep, watch, diag = apply_dragon_gate(relay, [])
    assert [r["code"] for r in keep] == ["L"]
    assert [r["code"] for r in watch] == ["D"]
    assert watch[0]["dragon_gate"] == "deputy"
    assert watch[0]["bucket"] == "watch"          # 桶标签必须同步（否则卡面自相矛盾）
    assert keep[0]["relay_rank"] == 1
    assert diag["moved_names"] == ["龙二"]


def test_gate_blocks_unbuyable_leader():
    """龙一一字/ST 买不进 → 它自己也不给买单资格（同线跟风已在上一轮被判 deputy）。"""
    relay = [_with_dragon(_row("L", "一字龙", 4, "092500", is_one_word=True),
                          1, "L", "一字龙", block="one_word")]
    keep, watch, diag = apply_dragon_gate(relay, [])
    assert keep == []
    assert watch[0]["dragon_gate"] == "leader_unbuyable"


def test_gate_exempts_no_sector_stocks():
    """无板块（题材缺失 / 独苗）→ 不受龙一闸约束（沿用孤军/高标逻辑）。"""
    relay = [_with_dragon(_row("S", "独苗", 3, "092500"), 0, has_sector=False)]
    keep, watch, _ = apply_dragon_gate(relay, [])
    assert [r["code"] for r in keep] == ["S"]
    assert watch == []


def test_gate_switch_is_reversible(monkeypatch):
    """`ENABLE_SECTOR_DRAGON_ONLY=0` → 一只不动（可回滚）。"""
    monkeypatch.setenv("ENABLE_SECTOR_DRAGON_ONLY", "0")
    relay = [_with_dragon(_row("L", "龙一", 4, "092500"), 1),
             _with_dragon(_row("D", "龙二", 4, "092501"), 2)]
    keep, watch, diag = apply_dragon_gate(relay, [])
    assert len(keep) == 2 and watch == []
    assert diag["enabled"] is False


def test_gate_missing_fields_does_not_block():
    """旧候选文件没有 `dragon_rank` 字段 → 不裁决（诚实降级，⛔ 不误拦）。"""
    old = _row("OLD", "旧数据", 3, "092500")
    keep, watch, _ = apply_dragon_gate([old], [])
    assert [r["code"] for r in keep] == ["OLD"]


# ══════════════════════════════════════════════════════════════════════════
# 5. 单一源（准入闸与龙一判定必须同一把尺子）
# ══════════════════════════════════════════════════════════════════════════

def test_pool_admit_strength_key_is_same_source():
    """`pool_admit.strength_key` 必须与 `sector_leader.leader_key` 完全同序。

    历史病：准入闸用 (板数, 带动, 封板早, 综合分)，梯队层用 (板数, 涨幅, 成交额)，
    两套判出两个龙一 → 建池与闸门各认一套，推出去的票自然打架。
    """
    rows = [
        _row("A", "甲", 4, "092501", seal_yi=0.17, lt_yi=69),
        _row("B", "乙", 4, "092502", seal_yi=0.25, lt_yi=29),
        _row("C", "丙", 4, "093409", seal_yi=1.28, lt_yi=73),
    ]
    bands = sl.time_bands(rows)
    by_sl = sorted(rows, key=lambda r: sl.leader_key(r, band=bands[r["code"]]))
    by_pa = sorted(rows, key=lambda r: pa.strength_key(r, band=bands[r["code"]]))
    assert [r["code"] for r in by_sl] == [r["code"] for r in by_pa]
    # 档位由封板时刻算出来：A/B 同档（09:25）→ 比封单 B 胜；C 晚一档（09:34）排后
    assert bands["A"] == bands["B"] != bands["C"]
    assert by_sl[0]["code"] == "B"
