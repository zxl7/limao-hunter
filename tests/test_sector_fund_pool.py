# -*- coding: utf-8 -*-
"""板块资金池单测（2026-09-08 优化A 升级·本地数据池版）。

锁定 live_signal._load_fund_pool / _pool_flow / _sector_money_txt 三件：
  1) 资金池直接命中（精确/剥尾缀/子串·防泛板块吞具体板块）
  2) 聚合题材别名 SECTOR_ALIAS 成员加总（板块口径合计·成员缺省跳过）
  3) 兜底 sector_risk top10（池过期/未建时）+ 全 miss 诚实文案
  4) 池过期/损坏 → 返回 None（走旧兜底·不崩溃）
"""
import importlib.util
import json
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("live_signal", SCRIPTS / "live_signal.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

POOL = {
    "ts": time.time(),
    "date": "2026-09-08",
    "boards": {
        # 精确可命中
        "机器人": {"pct": 2.0, "flow_yi": -1.5},
        "机器人概念": {"pct": 3.0, "flow_yi": -13.7},
        "PCB": {"pct": 4.0, "flow_yi": 6.55},
        "通信": {"pct": 1.0, "flow_yi": 14.24},
        "光通信模块": {"pct": 5.0, "flow_yi": 16.75},
        "存储芯片": {"pct": 2.0, "flow_yi": -16.5},
        "数字货币": {"pct": 1.0, "flow_yi": 3.5},
        # alias 大消费成员
        "新零售": {"pct": 1.0, "flow_yi": -6.4},
        "一般零售": {"pct": 1.0, "flow_yi": -1.2},
        "食品饮料": {"pct": 0.5, "flow_yi": -1.5},
        # alias 大农业成员
        "农业种植": {"pct": 3.0, "flow_yi": 8.9},
        "生态农业": {"pct": 2.0, "flow_yi": 2.0},
        "养殖业": {"pct": 1.0, "flow_yi": -1.7},
        # alias 大金融成员
        "非银金融": {"pct": 1.0, "flow_yi": 3.6},
        "多元金融": {"pct": 1.0, "flow_yi": 2.2},
        "金融控股": {"pct": 1.0, "flow_yi": 1.9},
    },
}

_TMP = Path("/tmp/_sfp_test_pool.json")


def _with_pool(boards):
    """把给定 boards 写成临时池文件并令 _load_fund_pool 读它（返回原 FUND_POOL 备份）。"""
    bak = ls.FUND_POOL
    _TMP.write_text(json.dumps({"ts": time.time(), "date": "2026-09-08", "boards": boards}))
    ls.FUND_POOL = _TMP
    return bak


def _txt(board):
    return ls._sector_money_txt({"sector_risk": {"inflow_top": [], "outflow_top": []}}, board)


def test_pool_exact_hit():
    f, nm = ls._pool_flow("机器人", POOL)
    assert nm == "机器人" and f == -1.5
    f, nm = ls._pool_flow("PCB板", POOL)
    assert nm == "PCB" and f == 6.55  # 剥尾缀/子串归一


def test_substr_picks_most_specific():
    # 光通信 → 必须是「光通信模块」而非泛板块「通信」（前后缀亲缘+更长优先）
    f, nm = ls._pool_flow("光通信", POOL)
    assert nm == "光通信模块" and f == 16.75
    f, nm = ls._pool_flow("通信", POOL)
    assert nm == "通信" and f == 14.24


def test_alias_aggregate_sum():
    """聚合题材（无同名资金板块）→ 成员板块净额加总 = 板块口径合计。"""
    bak = _with_pool(POOL["boards"])
    try:
        txt = _txt("大消费")
        assert "板块合计净额" in txt and "green" in txt and "-9.1亿" in txt  # -6.4-1.2-1.5=-9.1
        txt = _txt("大农业")
        assert "板块合计净额" in txt and "red" in txt and "+9.2亿" in txt  # 8.9+2.0-1.7=9.2
    finally:
        ls.FUND_POOL = bak


def test_alias_member_missing_skipped():
    """成员缺失自动跳过不报错（当日某板块不在池/停更）。"""
    boards = dict(POOL["boards"])
    boards.pop("新零售", None)  # 大消费少一成员 → 剩 -1.2-1.5=-2.7
    bak = _with_pool(boards)
    try:
        txt = _txt("大消费")
        assert "板块合计净额" in txt and "-2.7亿" in txt
    finally:
        ls.FUND_POOL = bak


def test_fallback_top10_when_pool_stale():
    """池过期/未建 → sector_risk top10 兜底仍工作 + 全 miss 诚实文案。"""
    ls.FUND_POOL = Path("/tmp/_sfp_stale_test.json")
    Path("/tmp/_sfp_stale_test.json").write_text(
        json.dumps({"ts": time.time() - 3600, "boards": POOL["boards"]}))
    try:
        assert ls._load_fund_pool() is None  # 过期 → None
        latest = {"sector_risk": {"inflow_top": [{"name": "大农业", "flow_yi": 12.5}], "outflow_top": []}}
        txt = ls._sector_money_txt(latest, "大农业")
        assert "净流入" in txt and "+12.5亿" in txt  # 旧兜底命中
        # 全 miss → 诚实文案
        assert "资金未入榜" in ls._sector_money_txt(latest, "不存在板块XYZ")
    finally:
        Path("/tmp/_sfp_stale_test.json").unlink(missing_ok=True)


def test_load_fund_pool_corrupt_returns_none():
    ls.FUND_POOL = Path("/tmp/_sfp_corrupt_test.json")
    Path("/tmp/_sfp_corrupt_test.json").write_text("{not json")
    try:
        assert ls._load_fund_pool() is None
    finally:
        Path("/tmp/_sfp_corrupt_test.json").unlink(missing_ok=True)


def test_enabled_off_returns_empty():
    assert ls._sector_money_txt({}, "大农业", enabled=False) == ""


def test_family_members_aggregate():
    """家族板块(泛农业)：对本次聚合成员(大农业→alias)逐个解析加总，头=家族名。"""
    bak = _with_pool(POOL["boards"])
    try:
        # 泛农业 family_members=[大农业] → 大农业 alias: 农业种植+8.9 生态农业+2.0 养殖业-1.7 → 9.2
        txt = ls._sector_money_txt(
            {"sector_risk": {}}, "泛农业",
            family_members=[{"name": "大农业", "zt_count": 15, "leader": "金健米业"}])
        assert "泛农业板块合计净额" in txt and "+9.2亿" in txt
        # 直连单板块家族成员名与 alias 展开不重复计（大农业只在 alias 出现一次）
        txt2 = ls._sector_money_txt(
            {"sector_risk": {}}, "泛农业",
            family_members=["大农业", "农业种植"])  # 农业种植 池直连(8.9) 与大农业 alias 里同板块去重
        assert "板块合计净额" in txt2 and "+9.2亿" in txt2
        # 家族成员全 miss → 点名成员诚实文案
        txt3 = ls._sector_money_txt({"sector_risk": {}}, "泛农业",
                                    family_members=["ZZZ不存在板块甲", "ZZZ不存在板块乙"])
        assert "资金未入榜" in txt3 and "ZZZ不存在板块甲" in txt3
    finally:
        ls.FUND_POOL = bak
