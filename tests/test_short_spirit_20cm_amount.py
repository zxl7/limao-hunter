# -*- coding: utf-8 -*-
"""短线精灵·20cm 高辨识度豁免成交额门槛（2026-10-08 用户「直接盘中开始做」）回归锁。

事故背景（10-08 真实盘面）：
  领湃科技(300530) 首板 20cm 涨停（锂电池），全天成交仅 3.47 亿 < 下午门槛 10 亿
  → 被 `noise_filter` 成交额门槛剔除；而同方向连板龙头(时代万恒4板/传艺科技3板)
  走 `_leaders_as_pool` 硬塞 amount_yi=999 绕过了门槛 → 被推。
  即「首板 20cm 高辨识度票」是系统性盲区（既非连板、又够不到成交额门槛）。

本测试锁死：
  1. _amount_exempt 的准入边界（20cm + 涨幅 + 流动性 + 已识别）
  2. 主板/科创板/低流动性/非主线 一律不豁免（防刷屏）
  3. noise_filter 集成：领湃式票在开关开时 KEEP、关时 DROP（可逆）
  4. _leaders_as_pool 兜底池纳入首板 20cm 涨停（东财抓取失败路径）
"""
import importlib.util
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("short_spirit", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)


def _sample(code="300530", name="领湃科技", pct=19.98, amt=3.47, mc=41.4, ind="锂电池"):
    return {"code": code, "name": name, "pct": pct, "price": 24.08,
            "amount_yi": amt, "market_cap_yi": mc, "float_cap_yi": mc,
            "list_date": "20150101", "prev_close": 20.0, "main_flow_yi": 0.5,
            "industry": ind}


def _cfg():
    return ss.load_cfg()


# ── 1. _amount_exempt 准入边界 ────────────────────────────────────────────────
def test_20cm_identified_exempt():
    """创业板 20cm + 涨幅达冲板线 + 已识别（leaders 榜内）+ 成交额≥1亿 → 豁免。"""
    s = _sample()  # 领湃：300530 / 19.98% / 3.47亿 / 锂电池
    assert ss._amount_exempt(s, identified_codes={"300530"}, boards_map={}, hot_kws=set()) is True


def test_20cm_hot_industry_exempt():
    """未在 leaders 榜，但行业命中当日热点关键词 → 也算已识别。"""
    s = _sample(code="300999", name="某电池票", pct=16.0, amt=2.0, ind="锂电池")
    assert ss._amount_exempt(s, identified_codes=set(), boards_map={}, hot_kws={"锂电池"}) is True


def test_20cm_boards_exempt():
    """连板≥1 也算已识别（与市值豁免的 boards>=2 不同：此处 board>=1 即高辨识度）。"""
    s = _sample(code="300888", name="某二板", pct=15.5, amt=1.5, ind="其它")
    assert ss._amount_exempt(s, identified_codes=set(), boards_map={"300888": 1}, hot_kws=set()) is True


def test_20cm_not_identified_blocked():
    """20cm 但既非 leaders/top5、又非连板、行业也不在热点 → 不豁免（防刷屏）。"""
    s = _sample(code="301234", name="某20cm非主线", pct=17.0, amt=2.0, ind="其它行业")
    assert ss._amount_exempt(s, identified_codes=set(), boards_map={}, hot_kws={"锂电池"}) is False


def test_20cm_below_liquidity_floor_blocked():
    """成交额 < SPIRIT_AMT_EXEMPT_MIN_YI（1 亿）→ 即便已识别也不豁免（挡无成交小票）。"""
    s = _sample(code="301999", name="某20cm小成交", pct=16.0, amt=0.5, ind="锂电池")
    assert ss._amount_exempt(s, identified_codes=set(), boards_map={}, hot_kws={"锂电池"}) is False


def test_20cm_below_pct_threshold_blocked():
    """涨幅 < 15%（20cm 冲板线）→ 不豁免（低强度不动量门槛）。"""
    s = _sample(code="300777", name="某20cm弱", pct=9.0, amt=3.0, ind="锂电池")
    assert ss._amount_exempt(s, identified_codes=set(), boards_map={}, hot_kws={"锂电池"}) is False


def test_mainboard_not_exempt():
    """主板（非 20cm）低成交额一律不豁免——成交额门槛即其保护。"""
    for c in ("002866", "600241", "000001"):
        s = _sample(code=c, name="主板票", pct=10.0, amt=3.0, ind="锂电池")
        assert ss._amount_exempt(s, identified_codes={c}, boards_map={c: 4}, hot_kws={"锂电池"}) is False


def test_star_market_not_exempt():
    """科创板 688 剔除（用户无权限）。"""
    s = _sample(code="688001", name="科创票", pct=19.9, amt=5.0, ind="锂电池")
    assert ss._amount_exempt(s, identified_codes={"688001"}, boards_map={}, hot_kws={"锂电池"}) is False


def test_switch_off_disables_exemption():
    """⛔ 可逆开关：ENABLE_SPIRIT_AMT_EXEMPT_20CM=False → 回到改动前行为。"""
    old = ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM
    try:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = False
        s = _sample()
        assert ss._amount_exempt(s, identified_codes={"300530"}, boards_map={}, hot_kws={"锂电池"}) is False
    finally:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = old


# ── 2. noise_filter 集成 ──────────────────────────────────────────────────────
def test_noise_filter_keeps_lipai_when_enabled():
    """下午时段门槛 10 亿：领湃 3.47 亿本应被挡，豁免开 → KEEP。

    市值取 120 亿以**隔离成交额这一道门**（不受 A/B 市值开关状态影响）。
    """
    old = ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM
    try:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = True
        out = ss.noise_filter([_sample(mc=120.0)], _cfg(), datetime.now(),
                              exempt_codes=set(), boards_map={},
                              identified_codes={"300530"}, hot_kws={"锂电池"})
        assert [s["code"] for s in out] == ["300530"], "首板 20cm 高辨识度票应豁免成交额门槛"
    finally:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = old


def test_noise_filter_drops_lipai_when_disabled():
    """同一输入，开关关 → DROP（证明确实是本改动带来的行为变化，且可逆）。"""
    old = ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM
    try:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = False
        out = ss.noise_filter([_sample(mc=120.0)], _cfg(), datetime.now(),
                              exempt_codes=set(), boards_map={},
                              identified_codes={"300530"}, hot_kws={"锂电池"})
        assert out == []
    finally:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = old


def test_noise_filter_mainboard_amount_gate_intact():
    """不误伤：主板低成交额票（非 20cm）仍被成交额门槛挡。"""
    s = _sample(code="600241", name="时代万恒", pct=10.0, amt=3.15, mc=50.0, ind="锂电池")
    out = ss.noise_filter([s], _cfg(), datetime.now(),
                          exempt_codes=set(), boards_map={"600241": 4},
                          identified_codes={"600241"}, hot_kws={"锂电池"})
    assert out == [], "主板票不应因本次改动而绕过成交额门槛（其走高板兜底路径）"


# ── 3. C 兜底池（东财抓取失败路径）────────────────────────────────────────────
_LEADERS_FIXTURE = {"leaders": [
    {"code": "300530", "name": "领湃科技", "boards": "", "pct": 19.98,
     "price": 24.08, "sector": "锂电池", "is_20cm": True},
    {"code": "600865", "name": "百大", "boards": "3天3板", "pct": 10.04,
     "price": 12.49, "sector": "大消费"},
    {"code": "600999", "name": "主板一板", "boards": "1天1板", "pct": 10.0,
     "price": 8.0, "sector": "X"},
    {"code": "300777", "name": "20cm弱票", "boards": "", "pct": 9.0,
     "price": 10.0, "sector": "X", "is_20cm": True},
]}


def test_leaders_as_pool_includes_20cm_first_board():
    """首板 20cm 涨停（boards 空）也纳入兜底池 —— 领湃式票在东财空池时可推。"""
    old = ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM
    try:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = True
        codes = [p["code"] for p in ss._leaders_as_pool(_LEADERS_FIXTURE)]
        assert "300530" in codes, "首板 20cm 涨停应入兜底池"
        assert "600865" in codes, "连板>=2 仍入兜底池（原行为不变）"
        assert "600999" not in codes, "主板首板（非 20cm）仍不入兜底池（防刷屏）"
        assert "300777" not in codes, "20cm 但涨幅未达涨停线 → 不入兜底池（防刷屏）"
    finally:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = old


def test_leaders_as_pool_switch_off_restores_old_behavior():
    """⛔ 开关关 → 兜底池回到「只 boards>=2」（证整体可逆、无遗漏路径）。"""
    old = ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM
    try:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = False
        codes = [p["code"] for p in ss._leaders_as_pool(_LEADERS_FIXTURE)]
        assert codes == ["600865"], f"关开关后应只剩连板>=2，实际={codes}"
    finally:
        ss.ENABLE_SPIRIT_AMT_EXEMPT_20CM = old
