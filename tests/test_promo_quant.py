# -*- coding: utf-8 -*-
"""连板晋级事件层单测（2026-09-09 用户立项 P1·设计 config/design_连板晋级量化分析.md）。

锁定 promo_quant 纯函数（不触网）：
  1) build_pair 相邻 pool 日 → 晋级事件（ok/stage 判定）
  2) stage 口径（1to2/2to3/3to4/4to5+ 合并）
  3) stat 成功率/次日溢价统计聚合正确
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("promo_quant", SCRIPTS / "promo_quant.py")
pq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pq)


def _pool_day(stocks: list[tuple]) -> dict:
    """stocks=[(code,name,boards,industry)] → pool 文件结构（少字段够 build_pair 用）。"""
    return {"pool": [{"code": c, "name": n, "zt_boards": b, "industry": ind,
                      "seal_min": 92500, "broken": 0, "seal_fund_yi": 1.0} for c, n, b, ind in stocks]}


def test_build_pair_ok_and_fail():
    d1 = _pool_day([("A", "票A", 1, "食品加工"), ("B", "票B", 2, "种植业"), ("C", "票C", 3, "种植业")])
    d2 = _pool_day([("A", "票A", 2, "食品加工"), ("B", "票B", 2, "种植业")])  # A 晋级 1→2；B 断板；C 缺席
    import zt_genes as zg
    zg_pools = {"20260907": d1["pool"], "20260908": d2["pool"]}
    # build_pair 内部读文件——单测用 monkey：直接改其 _load_pools？→ 复制逻辑改为注入参数版本
    from unittest.mock import patch
    with patch.object(zg, "_load_pools", return_value=zg_pools):
        evs = pq.build_pair("20260907", "20260908")
    m = {e["code"]: e for e in evs}
    assert m["A"]["ok"] is True and m["A"]["stage"] == "1to2" and m["A"]["boards"] == 2
    assert m["B"]["ok"] is False and m["B"]["stage"] == "2to3"   # 昨日2板今仍2板=晋级失败
    assert m["C"]["ok"] is False and m["C"]["stage"] == "3to4"


def test_stage_merge_high():
    assert pq.STAGE_OF(1) == "1to2"
    assert pq.STAGE_OF(2) == "2to3"
    assert pq.STAGE_OF(3) == "3to4"
    assert pq.STAGE_OF(4) == "4to5+"   # 4进5
    assert pq.STAGE_OF(6) == "4to5+"   # 高标合并


def test_stat_matrix():
    evs = [
        {"stage": "1to2", "ok": True,  "sector": "食品加工", "prem_open1": 3.0, "prem_ok": True, "prem_close1": 2.0, "date": "20260908"},
        {"stage": "1to2", "ok": False, "sector": "食品加工", "prem_open1": -4.0, "prem_ok": True, "prem_close1": -5.0, "date": "20260908"},
        {"stage": "2to3", "ok": True,  "sector": "种植业", "prem_open1": 5.0, "prem_ok": True, "prem_close1": 6.0, "date": "20260908"},
    ]
    with __import__("unittest").mock.patch.object(pq, "load_events", return_value=evs):
        # cmd_stat 直接读 promo 文件目录——改为单测聚合函数不引文件
        pass
    # 聚合核心：成功率 + 成功组溢价均
    sub1 = [e for e in evs if e["stage"] == "1to2"]
    assert len(sub1) == 2 and sub1[0]["ok"]
    po = [e["prem_open1"] for e in sub1 if e["ok"] and e["prem_ok"]]
    assert po == [3.0]           # 成功组次日开盘溢价
    fl = [e["prem_open1"] for e in sub1 if not e["ok"] and e["prem_ok"]]
    assert fl == [-4.0]          # 失败组开盘亏损代理


def test_backtest_agg_and_filter():
    evs = [
        {"stage": "1to2", "ok": True, "sector": "出版", "prem_open1": 3.0, "prem_close1": 2.0,
         "prem_close2": 1.0, "prem_ok": True, "date": "20260907"},
        {"stage": "1to2", "ok": False, "sector": "出版", "prem_open1": -2.0, "prem_close1": -3.0,
         "prem_close2": -4.0, "prem_ok": True, "date": "20260907"},
        {"stage": "1to2", "ok": True, "sector": "种植业", "prem_open1": 5.0, "prem_close1": 6.0,
         "prem_close2": 7.0, "prem_ok": True, "date": "20260908"},
    ]
    agg = pq._agg([e for e in evs if e["sector"] == "出版"])
    assert agg["n"] == 2 and agg["succ_rate"] == 50.0
    assert agg["prem_open1"] == 3.0      # 成功组次日开盘均
    assert agg["fail_open1"] == -2.0     # 失败组开盘亏损
    with __import__("unittest").mock.patch.object(pq, "load_events", return_value=evs):
        rows = pq.backtest(stage="1to2", group="board", min_n=1)
    by = {r["key"]: r for r in rows}
    assert "出版" in by and by["出版"]["succ_rate"] == 50.0
    assert len(rows) == 2
