# -*- coding: utf-8 -*-
"""可执行净值账本（`scripts/research_executable_nav.py`）回归锁 —— 2026-11-09

为什么锁这四条：这套模拟一旦有账务错误，**不会报错、只会给出一个好看的净值**，
而它要回答的正是「这套策略能不能用真金下单」。所以宁可把最基础的会计恒等式
和三条成交约束都钉死，也不接受「看起来对」。

锁定：
  ① 会计恒等式：权益 ≡ 现金 + Σ(股数 × 收盘价)
  ② 费用不得透支：带费用买入后现金不得为负，且股数必须是 100 的整数倍
  ③ 开盘涨停 / 一字板 → **买不到**（不成交、计入 buy_blocked、现金不动）
  ④ 收盘跌停 → **卖不出**（持仓保留、计入 sell_blocked）

全隔离：直接注入 `_BARS/_IDX` 假 K 线，不触真实 cache、不读磁盘、不联网。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "executable_nav", ROOT / "scripts" / "research_executable_nav.py")
    mod = importlib.util.module_from_spec(spec)      # type: ignore[arg-type]
    spec.loader.exec_module(mod)                     # type: ignore[union-attr]
    return mod


@pytest.fixture()
def mod():
    m = _load()
    saved_bars, saved_idx = dict(m._BARS), dict(m._IDX)
    yield m
    m._BARS.clear()
    m._BARS.update(saved_bars)
    m._IDX.clear()
    m._IDX.update(saved_idx)


def _feed(m, code: str, rows: list[dict]) -> None:
    m._BARS[code] = rows
    m._IDX[code] = {str(r["date"]): i for i, r in enumerate(rows)}


def _acct(m, capital=100_000.0, **kw):
    return m.Account(capital, max_positions=5, use_fill=kw.pop("use_fill", True),
                     use_cost=kw.pop("use_cost", True), slippage=kw.pop("slippage", 0.0))


def test_equity_identity(mod):
    """权益必须恒等于「现金 + 持仓市值」，且股数为整手。"""
    _feed(mod, "600001", [
        {"date": "2026-11-02", "open": 10.0, "close": 10.5, "high": 10.8, "low": 9.9},
        {"date": "2026-11-03", "open": 10.2, "close": 11.0, "high": 11.2, "low": 10.1},
    ])
    acct = _acct(mod, 100_000)
    assert acct._buy("600001", "测试股", "2026-11-02", 30_000)
    p = acct.pos["600001"]
    assert p["shares"] % 100 == 0
    expect = acct.cash + p["shares"] * 10.5
    assert acct.equity("2026-11-02") == pytest.approx(expect, abs=1e-6)


def test_fee_must_not_overdraw(mod):
    """费用计入后现金不得为负；预算刚好时按手数回退，而不是透支。"""
    _feed(mod, "600001", [
        {"date": "2026-11-02", "open": 10.0, "close": 10.0, "high": 10.0, "low": 10.0},
    ])
    mod.COMMISSION_MIN = 5.0
    acct = _acct(mod, 10_000)
    acct._buy("600001", "测试股", "2026-11-02", 9_999)     # 差一点买满
    assert acct.cash >= -1e-9
    if acct.pos:
        p = acct.pos["600001"]
        assert p["shares"] % 100 == 0
        assert p["shares"] * p["px"] <= 9_999 + 1e-6


def test_limit_up_open_is_not_buyable(mod):
    """前收 10.00 → 主板涨停 11.00；开盘即涨停 ⇒ 买不到。"""
    _feed(mod, "600001", [
        {"date": "2026-11-02", "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
        {"date": "2026-11-03", "open": 11.0, "close": 11.0, "high": 11.0, "low": 11.0},
    ])
    acct = _acct(mod, 100_000)
    assert acct._buy("600001", "测试股", "2026-11-03", 50_000) is False
    assert acct.buy_blocked == 1
    assert acct.cash == pytest.approx(100_000)


def test_limit_down_close_is_not_sellable(mod):
    """前收 10.00 → 跌停 9.00；收盘跌停 ⇒ 卖不出，持仓必须保留。"""
    _feed(mod, "600001", [
        {"date": "2026-11-02", "open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
        {"date": "2026-11-03", "open": 9.8, "close": 9.0, "high": 9.9, "low": 9.0},
    ])
    acct = _acct(mod, 100_000)
    assert acct._buy("600001", "测试股", "2026-11-02", 50_000)
    assert acct._sell("600001", "2026-11-03", "close") is False
    assert acct.sell_blocked == 1
    assert "600001" in acct.pos
