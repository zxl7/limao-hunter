# -*- coding: utf-8 -*-
"""跌停/炸板池 科创板过滤回归锁（2026-09-17 修）。

铁律：用户**无科创板交易权限** → 任何卡片/名单禁出现 688/689。
实证缺陷：2026-09-17 炸板池含「近岸蛋白(688137)」被盘中实时页渲染
（前端只有名字、无代码 → 无法自行过滤，必须在源头剔除）。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("ip_pool", SCRIPTS / "intraday_pulse.py")
ip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ip)


def test_star_market_filtered_from_both_pools():
    raw = {
        "broken_pool": {"data": [
            {"symbol": "603001.SH", "stock_chi_name": "正常票"},
            {"symbol": "688137.SH", "stock_chi_name": "近岸蛋白"},
            {"symbol": "689009.SH", "stock_chi_name": "九号公司"},
        ]},
        "ld_pool": {"data": [
            {"symbol": "688981.SH", "stock_chi_name": "中芯国际"},
            {"symbol": "000001.SZ", "stock_chi_name": "平安银行"},
        ]},
    }
    bk = ip.parse_pool(raw, "broken_pool")
    ld = ip.parse_pool(raw, "ld_pool")
    assert bk["names"] == ["正常票"], "688/689 必须剔除"
    assert ld["names"] == ["平安银行"]
    assert "近岸蛋白" not in bk["names"]


def test_count_matches_names_after_filter():
    """⛔ count 必须等于剔除后的名单长度（否则「数量含 688、名单不含」自相矛盾）。"""
    raw = {"broken_pool": {"data": [
        {"symbol": "688111.SH", "stock_chi_name": "A"},
        {"symbol": "300001.SZ", "stock_chi_name": "B"},
        {"symbol": "600001.SH", "stock_chi_name": "C"},
    ]}}
    p = ip.parse_pool(raw, "broken_pool")
    assert p["count"] == len(p["names"]) == 2


def test_previous_theme_count_uses_yesterday_pool_not_intraday_snapshot():
    """主线题材的「昨日」只统计昨日涨停池中的当前成分股。"""
    stocks = [
        {"code": "600001.SH", "name": "甲"},
        {"code": "000002.SZ", "name": "乙"},
        {"code": "300003.SZ", "name": "丙"},
    ]
    assert ip._previous_theme_zt_count(stocks, {"600001", "300003", "999999"}) == 2
    assert ip._previous_theme_zt_count(stocks, set()) == 0
    assert ip._previous_theme_zt_count(stocks, None) is None


def test_missing_code_not_dropped():
    """无 symbol/code 字段时**不得**误删（只按代码判 688，不按名字猜）。"""
    raw = {"broken_pool": {"data": [{"stock_chi_name": "无代码票"}]}}
    p = ip.parse_pool(raw, "broken_pool")
    assert p["names"] == ["无代码票"]


def test_empty_and_bad_shapes():
    assert ip.parse_pool({}, "broken_pool") == {"count": 0, "names": [], "items": []}
    assert ip.parse_pool({"broken_pool": {"data": "not a list"}}, "broken_pool") == {"count": 0, "names": [], "items": []}
