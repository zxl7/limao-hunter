#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回流前兆「资金趋势」主判据守卫（2026-09-15 立·口径切换）。

背景（承接 test_reflow_name_hit.py 的「电子布」刷屏事故）：
  09-15 上午先修了「名称假命中」（榜内泛名「电子」吞题材「电子布」）+ 加了指纹去重；
  但用户拍板要做**口径切换**——把触发主判据从「当日实时资金榜命中」改为
  **theme_rotation 引擎的 flow_trend（5 日资金趋势·盘后算）**，实时榜降级为佐证。

为什么必须切：
  该实时榜是东财**泛行业板块名**（PCB/半导体/电子/国产芯片…），而引擎候选是
  **概念题材名**（MLCC/电子布/CPO概念/船舶制造…），两套命名体系重叠度极低。
  若把「实时榜命中」当硬门 → 本卡近乎恒不触发（09-15 实测：8 个候选 0 命中）；
  若当唯一条件 → 又被泛名污染成刷屏源。故：**flow_trend 作硬门，实时榜只作佐证**。

本测试锁死：
  · 只有 `inflow_rising` 放行（neutral / inflow_fading / outflow_shrinking 均挡）；
  · `flow_trend` 缺失时回退 `confidence_factors.capital`，且能剥掉中文后缀
    （"neutral(无趋势)" → "neutral"，否则比较永不相等）；
  · 开关 `REFLOW_FLOW_TREND_ALLOW` 存在且可逆（改集合即改行为）；
  · 事故回归：09-15 真实候选「电子布」在**名称维度**不再假命中，
    同时其 flow_trend=inflow_rising 能正常通过资金趋势门（说明它被挡在别处而非误杀）。
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("lsmod_gate", ROOT / "scripts" / "live_signal.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()
flow_ok = M._reflow_flow_ok


# ── ① 正例：只有「流入上升中」放行 ────────────────────────────────────────
def test_inflow_rising_passes():
    assert flow_ok({"flow_trend": "inflow_rising"}) is True


def test_inflow_rising_passes_with_extra_fields():
    """真实候选对象带 confidence/watch_pool 等字段时不受影响。"""
    c = {"theme": "MLCC", "phase": "watch", "flow_trend": "inflow_rising",
         "confidence": 0.45, "watch_pool": [{"code": "002585.SZ", "name": "双星新材"}]}
    assert flow_ok(c) is True


# ── ② 反例：其余趋势一律挡（防"什么都能推" ──────────────────────────────
def test_other_trends_blocked():
    for t in ("neutral", "inflow_fading", "outflow_shrinking", "outflow_rising", "dead", ""):
        assert flow_ok({"flow_trend": t}) is False, t


def test_missing_field_blocks():
    assert flow_ok({}) is False
    assert flow_ok({"theme": "X"}) is False


# ── ③ capital 兜底 + 中文后缀剥离（⛔ 后缀不剥 → 比较永不相等）──────────
def test_fallback_to_capital_strips_chinese_suffix():
    assert flow_ok({"confidence_factors": {"capital": "inflow_rising"}}) is True
    # 引擎真实输出带中文后缀：须剥掉再比，否则恒 False（静默漏推）
    assert flow_ok({"confidence_factors": {"capital": "inflow_rising(流入上升)"}}) is True
    assert flow_ok({"confidence_factors": {"capital": "neutral(无趋势)"}}) is False
    assert flow_ok({"confidence_factors": {"capital": "inflow_fading(流入转弱)"}}) is False


def test_flow_trend_takes_precedence_over_capital():
    """两者并存时以 flow_trend 为准（纯值·不受后缀影响）。"""
    assert flow_ok({"flow_trend": "inflow_rising",
                    "confidence_factors": {"capital": "neutral(无趋势)"}}) is True
    assert flow_ok({"flow_trend": "neutral",
                    "confidence_factors": {"capital": "inflow_rising"}}) is False


# ── ④ 开关存在且可逆 ────────────────────────────────────────────────────
def test_switch_exists_and_is_reversible():
    assert isinstance(M.REFLOW_FLOW_TREND_ALLOW, set)
    assert "inflow_rising" in M.REFLOW_FLOW_TREND_ALLOW
    src = (ROOT / "scripts" / "live_signal.py").read_text(encoding="utf-8")
    assert "REFLOW_FLOW_TREND_ALLOW = {" in src          # 常量可改即行为可逆
    assert "_reflow_flow_ok(c)" in src                    # 判定已被调用（防"定义了却没用"）
    assert "skip_flow=_st_flow" in src                    # 漏斗可观测（零触发可定位卡在哪层）


# ── ⑤ 事故回归：09-15 真实候选数据的双维度行为 ───────────────────────────
def test_incident_regression_on_real_candidates():
    """用 09-15 真实 theme_rotation 候选（若在）回归：电子布不再被名称假命中，
    但其 flow_trend=inflow_rising 正常过闸 → 说明当天它是被**指纹去重**挡住的，
    不是被 flow 门误杀（证两份守卫各司其职、无重复兜底）。"""
    p = ROOT / "cache" / "theme_rotation.json"
    if not p.exists():
        return  # 无留存数据则跳过（测试不为数据依赖而失败）
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    cands = {c.get("theme"): c for c in (data.get("candidates") or [])}
    dzb = cands.get("电子布")
    if not dzb:
        return
    # ① 资金趋势门：电子布当天确为 inflow_rising → 过闸（不被误杀）
    if str(dzb.get("flow_trend") or "") == "inflow_rising":
        assert flow_ok(dzb) is True
    # ② 名称维度：泛名「电子」不得命中（事故本体·由 test_reflow_name_hit 锁死）
    assert M._reflow_name_hit("电子布", "电子") is False
