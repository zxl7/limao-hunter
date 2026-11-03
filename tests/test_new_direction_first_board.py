#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新方向首板独立观察槽回归锁（P2 · 2026-10-18 用户「按顺序处理」）。

背景：用户 10-16 原话「新方向里军工的长城军工、低空的万丰奥威与金盾股份是最强的
首板接棒候选」——但万丰奥威在 watch 排第 9，被 8 位容量截断，选股里直接消失。
本槽给「新方向首板」一个不被容量挤掉的固定观察位。

铁律（本文件锁死）：
1. 只收 boards==1，且**不占 relay/watch 名额**
2. 题材已成型（家数多）→ 不抢 relay/watch 的活，不进本槽
3. 缺数据不猜：题材家数与梯队健康度都缺 → 不进（不编造"新方向"）
4. ST / 688 一律不出现
5. 纯观察：不回写入参行、不改 relay/watch
"""
import os
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))

from core import quant_score as qs  # noqa: E402


def _c(code, name, boards=1, theme="军工", mates=1, raw=50.0, **kw):
    d = {"code": code, "name": name, "boards": boards, "core_theme": theme,
         "theme_mates_cnt": mates, "raw_score": raw, "sector": "测试",
         "theme": "测试行业", "pct": 10.0}
    d.update(kw)
    return d


def test_first_board_new_direction_selected():
    rows = [_c("600001", "首板甲", theme="低空经济", mates=2)]
    out = qs.select_new_direction_first_board(rows)
    assert [x["name"] for x in out] == ["首板甲"]


def test_formed_theme_excluded():
    """题材已成梯队（家数 8）→ 交给 relay/watch，不占新方向槽。"""
    rows = [_c("600002", "首板乙", theme="机器人", mates=8)]
    assert qs.select_new_direction_first_board(rows) == []


def test_second_board_excluded():
    rows = [_c("600003", "二板丙", boards=2, mates=1)]
    assert qs.select_new_direction_first_board(rows) == []


def test_codes_already_in_pools_excluded():
    rows = [_c("600004", "首板丁"), _c("600005", "首板戊")]
    out = qs.select_new_direction_first_board(rows, exclude_codes={"600004"})
    assert [x["name"] for x in out] == ["首板戊"]


@pytest.mark.parametrize("theme", ["", "其他", "—", "-", "无", "未知"])
def test_invalid_theme_excluded(theme):
    rows = [_c("600006", "首板己", theme=theme, mates=1)]
    assert qs.select_new_direction_first_board(rows) == []


def test_st_and_688_excluded():
    rows = [_c("600007", "*ST退市"), _c("688111", "科创庚"), _c("600008", "正常首板")]
    out = qs.select_new_direction_first_board(rows)
    assert [x["name"] for x in out] == ["正常首板"]


def test_sort_by_raw_desc_and_slots():
    rows = [
        _c("600011", "低分", raw=40.0),
        _c("600012", "高分", raw=70.0),
        _c("600013", "中分", raw=55.0),
        _c("600014", "次高分", raw=65.0),
    ]
    out = qs.select_new_direction_first_board(rows, slots=3)
    assert [x["name"] for x in out] == ["高分", "次高分", "中分"]


def test_fallback_to_ladder_health_when_mates_missing():
    """家数缺失时用梯队健康度：≤3 = 未成形 → 收；>3 → 不收。"""
    thin = _c("600021", "弱梯队", mates=None, ladder_health=2)
    thick = _c("600022", "强梯队", mates=None, ladder_health=8)
    out = qs.select_new_direction_first_board([thin, thick])
    assert [x["name"] for x in out] == ["弱梯队"]


def test_no_data_is_not_guessed_as_new_direction():
    """⛔ 家数与梯队健康度都缺 → 不判为新方向（缺数据不编造）。"""
    rows = [_c("600031", "无数据首板", mates=None)]
    rows[0].pop("ladder_health", None)
    assert qs.select_new_direction_first_board(rows) == []


def test_sector_mates_cnt_fallback():
    rows = [_c("600041", "行业兜底", mates=None, sector_mates_cnt=2)]
    out = qs.select_new_direction_first_board(rows)
    assert [x["name"] for x in out] == ["行业兜底"]


def test_does_not_mutate_input():
    rows = [_c("600051", "首板辛")]
    snapshot = [dict(r) for r in rows]
    qs.select_new_direction_first_board(rows)
    assert rows == snapshot, "⛔ 只读入参，不得回写 relay/watch 使用的行"


def test_empty_and_invalid_input():
    assert qs.select_new_direction_first_board([]) == []
    assert qs.select_new_direction_first_board(None) == []


def test_zero_slots():
    rows = [_c("600061", "首板壬")]
    assert qs.select_new_direction_first_board(rows, slots=0) == []


def test_toggle_reads_env(monkeypatch):
    monkeypatch.delenv("ENABLE_NEW_DIRECTION_FIRST_BOARD", raising=False)
    assert qs.new_direction_first_board_enabled() is True
    monkeypatch.setenv("ENABLE_NEW_DIRECTION_FIRST_BOARD", "0")
    assert qs.new_direction_first_board_enabled() is False
    monkeypatch.delenv("ENABLE_NEW_DIRECTION_FIRST_BOARD")


def test_slots_and_mates_constants_sane():
    """常量护栏：槽位 2~3、题材家数阈值 >1（1 家=孤军，不算"方向"）。"""
    assert 1 <= qs.NEW_DIRECTION_FIRST_BOARD_SLOTS <= 3
    assert qs.NEW_DIRECTION_MAX_THEME_MATES >= 2


def test_env_does_not_leak_into_other_tests():
    assert os.environ.get("ENABLE_NEW_DIRECTION_FIRST_BOARD") in (None, "", "1")
