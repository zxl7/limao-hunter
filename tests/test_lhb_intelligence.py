from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("lhb_intelligence", ROOT / "scripts" / "lhb_intelligence.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_signal_normalization_filters_ineligible_and_sells():
    m = _mod()
    docs = [{"date": "2026-09-01", "hotmoney": [{"name": "宁波桑田路", "rows": [
        {"ticker": "600001", "name": "沪深票", "hot_money_item_net_value": 100000000},
        {"ticker": "688001", "name": "科创票", "hot_money_item_net_value": 100000000},
        {"ticker": "600002", "name": "卖出票", "hot_money_item_net_value": -100000000},
    ]}], "stocks": [{"ticker": "300001", "name": "机构票", "org_net_value": 200000000}]}]
    rows = m._signals(docs)
    assert [(x["actor_type"], x["code"]) for x in rows] == [("hotmoney", "600001"), ("institution", "300001")]


def test_returns_use_t1_open_and_do_not_shift_calendar():
    m = _mod()
    sig = {"trade_date": "20260901", "code": "600001"}
    bars = {"600001": {
        "20260901": {"open": 9, "close": 10},
        "20260902": {"open": 11, "close": 12},
        "20260903": {"open": 12, "close": 12},
        "20260904": {"open": 13, "close": 15},
    }}
    assert m._returns(sig, bars, ["20260901", "20260902", "20260903", "20260904"]) == {
        "t1_open": 10.0, "t1_close": 20.0, "t3_close": 50.0,
    }


def test_today_observations_aggregate_seats_and_detect_cobuy():
    m = _mod()
    rows = [
        {"actor_type": "hotmoney", "actor": "甲席位", "code": "600001", "name": "甲股", "net_buy_yi": 1.2},
        {"actor_type": "hotmoney", "actor": "乙席位", "code": "600001", "name": "甲股", "net_buy_yi": 0.8},
        {"actor_type": "hotmoney", "actor": "乙席位", "code": "600002", "name": "乙股", "net_buy_yi": 0.3},
        {"actor_type": "institution", "actor": "机构", "code": "600001", "name": "甲股", "net_buy_yi": 2.0},
    ]
    hotmoney, co_buys = m._today_observations(rows)
    assert hotmoney[0]["actor"] == "甲席位"
    assert hotmoney[0]["net_buy_yi"] == 1.2
    assert len(co_buys) == 1
    assert {k: co_buys[0][k] for k in ("code", "name", "seat_count", "net_buy_yi")} == {
        "code": "600001", "name": "甲股", "seat_count": 2, "net_buy_yi": 2.0,
    }
    assert set(co_buys[0]["actors"]) == {"甲席位", "乙席位"}


def test_stat_needs_five_mature_samples_to_evaluate():
    m = _mod()
    rows = [{"returns": {"t1_open": 1.0, "t1_close": 2.0, "t3_close": 3.0}} for _ in range(4)]
    stat = m._stat(rows, "成都系", "hotmoney")
    assert stat["qualified"] is False
    assert stat["label"] == "样本不足，暂不评价"
    rows.append({"returns": {"t1_open": -1.0, "t1_close": 0.0, "t3_close": 1.0}})
    stat = m._stat(rows, "成都系", "hotmoney")
    assert stat["qualified"] is True
    assert stat["t1_open_avg_pct"] == 0.6
    assert stat["label"] == "次日溢价偏弱"
