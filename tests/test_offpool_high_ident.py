#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""池外高辨识度名单回归锁（P1 · 2026-10-18 用户「按顺序处理」）。

背景：涨停 61 只 → 成交额<5亿 剔掉 45 只(74%) → 只剩 16 只进评分。
复盘点名的新华传媒(2.47亿)、金盾股份(3.35亿) 死在这一刀上，**选股里查无此票**。
本名单把它们捞回来展示，但**绝不放松成交额纪律**。

铁律（本文件锁死）：
1. `buyable=False` 恒为 False —— 看得到 ≠ 买得到
2. 只按连板高度/唯一高度入选，不按涨跌幅/成交额"猜"
3. ST / 688 一律不出现
4. 排序 boards desc → cje desc，截 topn
5. 不参与 relay/watch（本函数只返回独立名单，不修改入参）
"""
import importlib.util
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE / "wealth_code" / "pipelines"))
sys.path.insert(0, str(BASE))

_spec = importlib.util.spec_from_file_location(
    "pipeline_eod", BASE / "wealth_code" / "pipelines" / "pipeline_eod.py"
)
pe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pe)


def _row(code, name, boards, cje, **kw):
    d = {"code": code, "name": name, "boards": boards, "cje_yi": cje,
         "sector": "测试题材", "pct": 10.0}
    d.update(kw)
    return d


def test_three_boards_offpool_is_listed_but_not_buyable():
    rows = [_row("600001", "三连板甲", 3, 2.47)]
    out = pe.select_offpool_high_ident(rows, market_max_boards=5, market_max_count=1)
    assert len(out) == 1
    item = out[0]
    assert item["name"] == "三连板甲"
    assert item["buyable"] is False, "⛔ 池外名单永不可买"
    assert "2.47亿" in item["offpool_reason"]
    assert item["ident_reason"] == "3连板"


def test_two_boards_not_unique_is_excluded():
    rows = [_row("600002", "二连板乙", 2, 3.0)]
    out = pe.select_offpool_high_ident(rows, market_max_boards=5, market_max_count=1)
    assert out == [], "2 板且非唯一高度不进名单"


def test_unique_market_height_is_listed_even_at_two_boards():
    """全市场唯一 2 板（最高高度且只有它一只）→ 入选，标唯一高度。"""
    rows = [_row("600003", "唯一二板", 2, 4.0)]
    out = pe.select_offpool_high_ident(rows, market_max_boards=2, market_max_count=1)
    assert len(out) == 1
    assert out[0]["ident_reason"] == "全市场唯一高度"


def test_two_stocks_at_top_height_not_unique():
    """全市场有两只 5 板 → 不是唯一高度；但仍因 ≥3 板入选，理由=5连板。"""
    rows = [_row("600004", "五板A", 5, 2.0), _row("600005", "五板B", 5, 1.0)]
    out = pe.select_offpool_high_ident(rows, market_max_boards=5, market_max_count=2)
    assert len(out) == 2
    assert all(x["ident_reason"] == "5连板" for x in out)


def test_highest_board_in_pool_means_no_unique_for_offpool():
    """最高板在池内（全市场 count=2，池外 1 只）→ 池外那只不算唯一高度。"""
    rows = [_row("600006", "池外五板", 5, 2.0)]
    out = pe.select_offpool_high_ident(rows, market_max_boards=5, market_max_count=2)
    assert len(out) == 1
    assert out[0]["ident_reason"] == "5连板", "不得标唯一高度"


def test_st_excluded():
    rows = [_row("600007", "*ST退市", 4, 1.0), _row("600008", "st小写", 4, 1.0)]
    out = pe.select_offpool_high_ident(rows, market_max_boards=4, market_max_count=2)
    assert out == [], "⛔ 铁律：ST/*ST 一律不看"


def test_688_excluded():
    rows = [_row("688111", "科创甲", 4, 1.0)]
    out = pe.select_offpool_high_ident(rows, market_max_boards=4, market_max_count=1)
    assert out == [], "⛔ 无科创板权限，688 一律不进"


def test_sort_by_boards_then_turnover():
    rows = [
        _row("600011", "三板低量", 3, 1.0),
        _row("600012", "五板", 5, 0.5),
        _row("600013", "三板高量", 3, 4.5),
    ]
    out = pe.select_offpool_high_ident(rows, market_max_boards=5, market_max_count=1)
    assert [x["name"] for x in out] == ["五板", "三板高量", "三板低量"]


def test_topn_truncates():
    rows = [_row(f"60010{i}", f"三板{i}", 3, float(i)) for i in range(10)]
    out = pe.select_offpool_high_ident(rows, market_max_boards=5, market_max_count=1, topn=3)
    assert len(out) == 3
    assert [x["name"] for x in out] == ["三板9", "三板8", "三板7"]


def test_empty_input():
    assert pe.select_offpool_high_ident([], market_max_boards=5, market_max_count=1) == []
    assert pe.select_offpool_high_ident(None, market_max_boards=0) == []


def test_does_not_mutate_input_rows():
    rows = [_row("600020", "三板丙", 3, 2.0)]
    snapshot = [dict(r) for r in rows]
    pe.select_offpool_high_ident(rows, market_max_boards=5, market_max_count=1)
    assert rows == snapshot, "本函数只读入参，不得回写 relay/watch 使用的行"


def test_missing_code_or_name_skipped():
    rows = [{"code": "", "name": "无名", "boards": 4, "cje_yi": 1.0},
            {"code": "600030", "name": "", "boards": 4, "cje_yi": 1.0}]
    assert pe.select_offpool_high_ident(rows, market_max_boards=4, market_max_count=1) == []


def test_toggle_reads_env(monkeypatch):
    monkeypatch.delenv("ENABLE_OFFPOOL_HIGH_IDENT", raising=False)
    assert pe.offpool_high_ident_enabled() is True
    monkeypatch.setenv("ENABLE_OFFPOOL_HIGH_IDENT", "0")
    assert pe.offpool_high_ident_enabled() is False
