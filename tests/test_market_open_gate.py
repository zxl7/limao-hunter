# -*- coding: utf-8 -*-
"""昨涨停完整样本 09:25 竞价回暖闸回归锁。"""
import pytest
import sys
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

from core import market_open, pool_call  # noqa: E402


def _row(code, boards=1, amount=10.0):
    return {"code": code, "name": f"票{code}", "boards": boards, "cje_yi": amount}


def _q(gap, amount=0.2):
    return {"prev_close": 10.0, "auction_price": round(10 * (1 + gap / 100), 3),
            "auction_amount_yi": amount}


def _cand(code, boards=3):
    return {"code": code, "name": f"候选{code}", "boards": boards, "sector": "测试题材",
            "expectation": {"expected_range": [1.0, 3.0], "auction_amount_min_yi": 0.1}}


def test_full_market_open_needs_complete_sample_and_three_signals():
    rows = [_row("000001", 3), _row("000002", 2), _row("000003", 1),
            _row("000004", 1), _row("000005", 1)]
    quotes = {"000001": _q(2, 0.2), "000002": _q(1, 0.2), "000003": _q(1, 0.2),
              "000004": _q(-1, 0.2), "000005": _q(2, 0.2)}
    read = market_open.build_market_open(rows, quotes)
    assert read["coverage"] == 1.0
    assert read["red_open_ratio"] == 0.8
    assert read["leader_red_open_ratio"] == 1.0
    assert read["leader_volume_median_ratio"] == 0.02
    # 连板只有两只，样本不足，不能因为红开漂亮放宽空仓。
    assert read["ready"] is False


def test_hibernate_releases_two_when_full_market_recovers(monkeypatch):
    # 2026-11-06 起 `POOL_CALL_EOD_PLAN=0` 默认不读预案的 stance；本用例验的是
    # 「hibernate + 竞价回暖 → 只放 1 只」这条旧机制，须显式打开预案消费。
    monkeypatch.setattr(pool_call, "POOL_CALL_EOD_PLAN", True)
    monkeypatch.setattr(market_open, "MIN_LEADERS", 2)
    rows = [_row("000001", 3), _row("000002", 2), _row("000003", 1),
            _row("000004", 1), _row("000005", 1)]
    quotes = {code: _q(2, 0.2) for code in ["000001", "000002", "000003", "000004", "000005"]}
    read = market_open.build_market_open(rows, quotes)
    assert read["ready"] is True
    cands = {"relay": [_cand("000001", 3), _cand("000002", 2)], "watch": []}
    res = pool_call.call_pool(cands, quotes, plan={"stance": "hibernate", "views": {}},
                              market_open=read, line_cores={})
    bought = [code for code, view in res["views"].items() if view["view"] == "participate"]
    # 2026-11-17：回暖保底名额由「1 只」放宽到「2 只」（与 ice_unfreeze 同源），
    # 因此两只都放行；⛔ 仍然不放第三只（不是取消名额，只是保底数变了）。
    assert bought == ["000001", "000002"]
    assert res["meta"]["market_open_relief"] is True
    assert res["meta"]["effective_quota"] == 2
    # 文案仍是「只试这一只」（口径短语未随名额改），断言只校验关键短语
    assert "空仓日只试这一只" in res["views"]["000001"]["reason"]


def test_hibernate_stays_closed_when_leaders_open_weakly(monkeypatch):
    monkeypatch.setattr(pool_call, "POOL_CALL_EOD_PLAN", True)
    monkeypatch.setattr(market_open, "MIN_LEADERS", 2)
    rows = [_row("000001", 3), _row("000002", 2), _row("000003", 1),
            _row("000004", 1), _row("000005", 1)]
    quotes = {"000001": _q(-1, 0.2), "000002": _q(0, 0.2), "000003": _q(2, 0.2),
              "000004": _q(2, 0.2), "000005": _q(2, 0.2)}
    read = market_open.build_market_open(rows, quotes)
    assert read["ready"] is False
    cands = {"relay": [_cand("000003", 3)], "watch": []}
    res = pool_call.call_pool(cands, quotes, plan={"stance": "hibernate", "views": {}},
                              market_open=read, line_cores={})
    assert res["views"]["000003"]["view"] == "watch"
    assert res["meta"]["effective_quota"] == 0
