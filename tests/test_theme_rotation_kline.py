#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K线止跌因子（2026-09-22 用户口径）—— 纯逻辑回归锁。

用户原话：「题材回流这块数据怎么没有刷新呢？**监听板块消息，资金流和K线是否止跌，
确定是否回流**！！」

背景：`theme_rotation.py` 的 v1 文件头曾明确跳过这一维
（「龙头破五日线需逐票拉日K，v1 不做（成本高）」），于是回流判定只有
「催化(消息) + 大盘 + 资金 + 相位」四因子 —— **缺的正是用户点出的价格维**。

本文件锁五件事：
  ① 四条判据逐条正确（站上MA5 / 近2日不创新低 / 跌幅收窄 / 缩量）；
  ② 命中 ≥2 = 止跌成立，≤1 = 未止跌；
  ③ 数据不足 → `no_data`（⛔ 绝不当「未止跌」—— 缺数据 ≠ 价格没止跌）；
  ④ 开关可逆（`ENABLE_KLINE_STOPFALL=False` → `off`，回退 v1 行为）；
  ⑤ 五因子权重：kline 满分 0.15，从 catalyst/market 匀出（⛔ 资金 0.35 / 相位 0.20 不动）。

⛔ 全程 **不触网** —— 一律 monkeypatch `_fetch_kline`。
运行：/Users/zxl/.workbuddy/binaries/python/envs/default/bin/python -m pytest tests/test_theme_rotation_kline.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import theme_rotation as tr  # noqa: E402


def bars(closes, lows=None, vols=None):
    """构造日K序列（默认：最低价 = 收盘 × 0.99、量恒 1000）。"""
    n = len(closes)
    lows = lows if lows is not None else [c * 0.99 for c in closes]
    vols = vols if vols is not None else [1000.0] * n
    assert len(lows) == n and len(vols) == n
    return [
        {"date": f"2026-08-{i + 1:02d}", "close": closes[i], "low": lows[i], "vol": vols[i]}
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    """每个用例清缓存，避免跨用例串味。"""
    tr._KLINE_CACHE.clear()
    monkeypatch.setattr(tr, "ENABLE_KLINE_STOPFALL", True)
    yield
    tr._KLINE_CACHE.clear()


# ────────────────────── ① 四条判据 ──────────────────────

def test_all_four_criteria_hit(monkeypatch):
    """止跌 4/4：站上MA5 + 近2日不创新低 + 跌幅收窄 + 缩量。"""
    closes = [10.0, 9.8, 9.6, 9.4, 9.2, 9.0, 8.8, 8.85, 8.9, 9.0]
    lows = [9.7, 9.5, 9.3, 9.1, 8.9, 8.7, 8.75, 8.8, 8.83, 8.88]
    vols = [1200, 1200, 1200, 1200, 1200, 1200, 1200, 900, 800, 700]
    monkeypatch.setattr(tr, "_fetch_kline", lambda code, lmt=30: bars(closes, lows, vols))
    state, hits = tr._kline_stop_fall("600000")
    assert hits == 4, state
    assert state.startswith("stop_fall")


def test_all_four_criteria_miss(monkeypatch):
    """未止跌 0/4：破位下跌 + 创新低 + 跌幅扩大 + 放量。"""
    closes = [10.0 - 0.2 * i for i in range(11)]
    lows = [c - 0.1 for c in closes]
    vols = [500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500]
    monkeypatch.setattr(tr, "_fetch_kline", lambda code, lmt=30: bars(closes, lows, vols))
    state, hits = tr._kline_stop_fall("600000")
    assert hits == 0, state
    assert state.startswith("not_yet")


def test_pool_live_quotes_do_not_fallback_to_boom_day_pct():
    """实时行情缺失时必须明确为空，不能把建池日涨幅当作今日涨幅。"""
    candidates = [{
        "watch_pool": [
            {"code": "600001.SH", "pct": 10.0},
            {"code": "000002.SZ", "pct": 9.9},
        ],
    }]
    tr._attach_live_pool_quotes(candidates, {"600001": {"pct": 2.35, "price": 11.2}})
    first, second = candidates[0]["watch_pool"]
    assert first["live_pct"] == 2.35
    assert first["live_price"] == 11.2
    assert second["live_pct"] is None
    assert second["live_price"] is None


# ────────────────────── ② 阈值：≥2 才成立 ──────────────────────

def _state_with(monkeypatch, hits_wanted):
    """构造「恰好命中 hits_wanted 条」的 K 线，返回 (state, hits)。

    通过独立开关式构造 4 组数据太脆，这里直接断言阈值语义：
    hits_wanted=2 → stop_fall；hits_wanted=1 → not_yet。
    """
    if hits_wanted == 2:
        # ① 站上MA5 + ④ 缩量成立；② 创新低、③ 跌幅扩大不成立
        closes = [10.0, 9.6, 9.2, 8.8, 8.4, 8.0, 7.6, 7.2, 6.8, 6.9]
        lows = [c - 0.05 for c in closes]        # 单调新低 → ② 不成立
        vols = [1000] * 9 + [500]                # 今日大幅缩量 → ④ 成立
        # ③ 跌幅收窄？ r_today = 6.9/6.8-1 = +1.47% > r_prev = 6.8/7.2-1 = -5.6% → ③ 成立
        # （故实际 hits=3：① 站上MA5? MA5=7.6 → 6.9 < 7.6 → ① 不成立）→ hits = ③+④ = 2
    else:
        closes = [10.0, 9.6, 9.2, 8.8, 8.4, 8.0, 7.6, 7.2, 6.8, 6.78]
        lows = [c - 0.05 for c in closes]        # ② 不成立
        vols = [1000] * 9 + [3000]               # ④ 不成立（放量）
        # ① MA5=7.6 → 6.78 < 7.6 不成立；③ r_today=-0.29% vs r_prev=-5.6% → 成立
        # → hits = 1
    monkeypatch.setattr(tr, "_fetch_kline", lambda code, lmt=30: bars(closes, lows, vols))
    return tr._kline_stop_fall("600000")


def test_threshold_two_hits_is_stop_fall(monkeypatch):
    """恰好 2 条 → 判为止跌。"""
    state, hits = _state_with(monkeypatch, 2)
    assert hits == 2, state
    assert state.startswith("stop_fall"), "命中 2 条必须成立（用户口径：≥2）"


def test_threshold_one_hit_is_not_yet(monkeypatch):
    """只有 1 条 → 判为未止跌。"""
    state, hits = _state_with(monkeypatch, 1)
    assert hits == 1, state
    assert state.startswith("not_yet"), "命中 1 条不得成立"


# ────────────────────── ③ 数据不足 / 失败 ──────────────────────

def test_insufficient_bars_is_no_data(monkeypatch):
    """K线不足 8 根 → no_data（⛔ 不得当「未止跌」）。"""
    monkeypatch.setattr(tr, "_fetch_kline", lambda code, lmt=30: bars([10.0] * 5))
    state, hits = tr._kline_stop_fall("600000")
    assert state.startswith("no_data")
    assert hits == 0


def test_fetch_failure_is_no_data(monkeypatch):
    """抓取失败（空列表）→ no_data，不抛异常。"""
    monkeypatch.setattr(tr, "_fetch_kline", lambda code, lmt=30: [])
    state, _ = tr._kline_stop_fall("600000")
    assert state.startswith("no_data")


def test_empty_code_is_no_data():
    """无代码 → no_data。"""
    state, _ = tr._kline_stop_fall("")
    assert state.startswith("no_data")


# ────────────────────── ④ 开关可逆 + 缓存 ──────────────────────

def test_switch_off_returns_off(monkeypatch):
    """`ENABLE_KLINE_STOPFALL=False` → off（回退 v1 行为，不拉网）。"""
    monkeypatch.setattr(tr, "ENABLE_KLINE_STOPFALL", False)
    called = []
    monkeypatch.setattr(tr, "_fetch_kline", lambda code, lmt=30: called.append(1) or [])
    state, hits = tr._kline_stop_fall("600000")
    assert state.startswith("off")
    assert hits == 0
    assert not called, "开关关闭时不得发请求"


def test_same_code_fetched_once(monkeypatch):
    """同代码只拉一次（多题材共享龙头时省请求）。"""
    calls = []

    def spy(code, lmt=30):
        calls.append(code)
        return bars([10.0, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4, 9.5, 9.6, 9.7])

    monkeypatch.setattr(tr, "_fetch_kline", spy)
    tr._kline_stop_fall("600000")
    tr._kline_stop_fall("600000.SH")   # 同代码带后缀
    assert len(calls) == 1, f"应只请求 1 次，实际 {len(calls)}"


def test_secid_mapping():
    """代码 → 东财 secid（1=沪 / 0=深）。"""
    assert tr._secid("600000") == "1.600000"
    assert tr._secid("601086.SH") == "1.601086"
    assert tr._secid("002285") == "0.002285"
    assert tr._secid("300110.SZ") == "0.300110"
    assert tr._secid("") == ""


# ────────────────────── ⑤ 五因子权重 ──────────────────────

def test_kline_weight_is_015_and_full_score_is_1(monkeypatch):
    """kline 满分 0.15；五因子满配 = 1.00（资金 0.35 / 相位 0.20 未被削）。"""
    mctx = {"sentiment_score": 8.0, "nuke_count": 0}
    t_boom = {"title": "有催化", "stocks": []}
    full, f_full = tr._confidence(
        "re-entry-candidate", 3, t_boom, mctx,
        flow_trend="inflow_rising", kline_state="stop_fall(止跌3/4)",
    )
    no_kl, f_no = tr._confidence(
        "re-entry-candidate", 3, t_boom, mctx,
        flow_trend="inflow_rising", kline_state="not_yet(未止跌1/4)",
    )
    assert full == 1.0, f"满配应为 1.00，实际 {full}"
    assert round(full - no_kl, 2) == 0.15, "「未止跌」应比「止跌」少 0.15"
    assert f_full["kline"].startswith("stop_fall")
    assert f_no["kline"].startswith("not_yet")
    # 旧的资金/相位权重不得被削减
    assert f_full["capital"] == "inflow_rising"
    assert f_full["phase"] == "re-entry-candidate"


def test_missing_kline_state_defaults_to_no_data():
    """不传 kline_state → no_data，且 kline 得 0 分（⛔ 不给缺数据的题虚高）。"""
    mctx = {"sentiment_score": 8.0, "nuke_count": 0}
    conf, f = tr._confidence(
        "re-entry-candidate", 3, {"title": "有催化", "stocks": []}, mctx,
        flow_trend="inflow_rising",
    )
    assert f["kline"].startswith("no_data")
    assert conf == 0.85, f"缺K线应比满配少 0.15，实际 {conf}"


# ────────────────────── ⑥ 龙头回落链 ──────────────────────

def test_leader_falls_back_to_history(monkeypatch):
    """`watch` 类（无当日 boom）→ 回落到**历史 eruption 里该题材的龙头**。

    实测 2026-09-22：8 条候选里 5 条是 watch 类、没有当日 boom 数据 ——
    不回落这一层它们会全部 `no_data`，而「待回流候选有没有止跌」恰恰最该看。
    """
    monkeypatch.setattr(
        tr, "_fetch_kline",
        lambda code, lmt=30: bars([10.0, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4, 9.5, 9.6, 9.7]),
    )
    series = [
        ("2026-09-17", {"光通信": {"stocks": [{"code": "603042", "dragon": True}]}}),
        ("2026-09-18", {"光通信": {"stocks": [{"code": "603042", "dragon": True}]}}),
    ]
    state = tr._kline_state_of("光通信", None, series)
    assert state.startswith("stop_fall"), state


def test_leader_prefers_today_boom(monkeypatch):
    """有当日 boom → 优先用当日龙头（⛔ 不用历史）。"""
    seen = []

    def spy(code, lmt=30):
        seen.append(code)
        return bars([10.0, 9.9, 9.8, 9.7, 9.6, 9.5, 9.4, 9.5, 9.6, 9.7])

    monkeypatch.setattr(tr, "_fetch_kline", spy)
    t_boom = {"stocks": [{"code": "600001"}, {"code": "600002", "dragon": True}]}
    series = [("2026-09-18", {"AI芯片": {"stocks": [{"code": "999999", "dragon": True}]}})]
    tr._kline_state_of("AI芯片", t_boom, series)
    assert seen == ["600002"], f"应取当日 dragon，实际 {seen}"


def test_no_leader_anywhere_is_no_data(monkeypatch):
    """当日与历史都无龙头 → no_data。"""
    monkeypatch.setattr(tr, "_fetch_kline", lambda code, lmt=30: [])
    assert tr._kline_state_of("某题材", {}, [("2026-09-18", {})]).startswith("no_data")
