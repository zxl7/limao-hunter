#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
八维入场研判面板（对齐 quant-review zt_analysis / dragon_tactics_core 口径）。

wealth_code 真实可算维度：
  - 龙头(score / leader_score)
  - 容量(cap_score)
  - 梯队(ladder_health × 10)

其余维度（环境 / 板块 / 接力 / 风险 / 潮汐）当前无 quant 原版因子模型
（quant 的 leaderFactorScore / relayFactorScore / breakRisk / environmentScore /
tide* 依赖一整套上游特征管线，wealth_code 未移植），故用现有真实数据派生代理值：
  - 环境  ← feedback_matrix 情绪分 ×10
  - 板块  ← theme_ctx 题材相位 + 日报 verdict flags
  - 接力  ← env 周期 + 梯队健康 + 龙头/容量身份
  - 风险  ← 核按钮数 + 断板率 + (10 − 情绪分)
  - 潮汐  ← env 周期（risk_on→升潮+4 / neutral→平潮0 / risk_off→退潮−4）

所有代理维度显式标 proxied=True，卡片端打「代」标记并附脚注，守数据诚实
（用户铁律：消息和数据的准确性是底层要求，代理值不得冒充原版因子分）。

输出 dim_panel：
  {环境, 板块, 龙头, 接力, 容量, 风险, 潮汐, 梯队} 每项 {score, label, proxied, note}
  + _band（综合档位：强候选/重点/条件/只观察，取龙头分为主）
  + _mid_carry（中位承接：充足/不足，来自梯队完整性）
"""

from __future__ import annotations

from typing import Any


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# —— 维度档位（对齐 quant-review zt_analysis._factor_band）——
def _factor_band(score: float) -> str:
    if score >= 80:
        return "强候选"
    if score >= 70:
        return "重点候选"
    if score >= 60:
        return "条件触发"
    return "只观察"


# —— 环境接力标签（对齐 quant-review zt_analysis 环境分映射）——
def _env_relay_label(score: float) -> str:
    if score >= 80:
        return "进攻窗口"
    if score >= 70:
        return "接力可做"
    if score >= 58:
        return "领头羊"
    return "防守"


def _risk_label(score: float) -> str:
    """breakRisk 越高=风险越大。"""
    if score >= 70:
        return "高"
    if score >= 50:
        return "中"
    return "低"


def _ladder_label(score: float) -> str:
    """梯队健康度(0~100)。"""
    if score >= 80:
        return "完整"
    if score >= 60:
        return "良好"
    if score >= 40:
        return "一般"
    return "弱"


def _sector_score(theme: str, theme_ctx: dict[str, Any] | None, dv_flags: list[str]) -> tuple[float, str]:
    """板块强度代理：题材相位 boom / 日报 main → 强(85)；avoid_breadth → 弱(45)；其余中(65)。"""
    if theme_ctx:
        ph = (theme_ctx.get(theme) or {}).get("phase") or ""
        if ph in ("boom", "re-entry-candidate", "re-entry"):
            return 85.0, "强"
    if "main" in dv_flags:
        return 85.0, "强"
    if "avoid_breadth" in dv_flags:
        return 45.0, "弱"
    if "observe" in dv_flags:
        return 65.0, "中"
    return 65.0, "中"


def _relay_score(env: dict[str, Any], cand: dict[str, Any]) -> float:
    """接力适配代理：env 周期基调 + 梯队健康 + 龙头/容量身份。"""
    regime = env.get("regime")
    base = {"risk_on": 80.0, "neutral": 62.0, "risk_off": 45.0}.get(regime, 62.0)
    lad = (float(cand.get("ladder_health") or 0.0) / 10.0) * 15.0  # 最高 +15
    dragon = 10.0 if cand.get("is_sector_dragon") else 0.0
    cap = 5.0 if cand.get("is_capacity_core") else 0.0
    return _clamp(base + lad + dragon + cap)


def _break_risk(env: dict[str, Any], cand: dict[str, Any]) -> float:
    """断板/核按钮风险代理（越高越危险）。"""
    nuke = int(env.get("nuke_count") or 0)
    broken = float(env.get("broken_rate") or 0.0)
    sscore = float(env.get("sentiment_score") or 5.0)
    return _clamp(nuke * 8.0 + broken * 0.8 + (10.0 - sscore) * 2.0)


def _tide_proxy(env: dict[str, Any]) -> tuple[float, str]:
    """潮汐代理（环境周期映射为有符号潮汐值）。"""
    regime = env.get("regime")
    if regime == "risk_on":
        return 4.0, "升潮"
    if regime == "risk_off":
        return -4.0, "退潮"
    return 0.0, "平潮"


def build_dimension_panel(
    cand: dict[str, Any],
    env: dict[str, Any],
    theme_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对单只候选产出八维入场研判面板。

    Returns:
        {
          "环境": {score, label, proxied, note},
          "板块": {...}, "龙头": {...}, "接力": {...}, "容量": {...},
          "风险": {...}, "潮汐": {...}, "梯队": {...},
          "_band": str,        # 综合档位（龙头分为主）
          "_mid_carry": str,   # 中位承接：充足/不足
        }
    """
    theme = str(cand.get("theme") or "")
    dv = cand.get("daily_verdict") or {}
    dv_flags = dv.get("flags") or [] if isinstance(dv, dict) else []

    # —— 真实维度（来自 wealth_code 既有因子）——
    leader = _clamp(float(cand.get("score") or 0.0))
    capacity = _clamp(float(cand.get("cap_score") or 0.0))
    ladder = _clamp((float(cand.get("ladder_health") or 0.0)) * 10.0)

    # —— 代理维度（基于真实数据派生）——
    env_score = _clamp(float(env.get("sentiment_score") or 5.0) * 10.0)
    sector_s, sector_l = _sector_score(theme, theme_ctx, dv_flags)
    relay_s = _relay_score(env, cand)
    risk_s = _break_risk(env, cand)
    tide_s, tide_l = _tide_proxy(env)

    panel = {
        "环境": {"score": round(env_score, 1), "label": _env_relay_label(env_score),
                 "proxied": True, "note": "情绪分×10"},
        "板块": {"score": round(sector_s, 1), "label": sector_l,
                 "proxied": True, "note": "题材相位/日报verdict"},
        "龙头": {"score": round(leader, 1), "label": _factor_band(leader),
                 "proxied": False, "note": "leader_score"},
        "接力": {"score": round(relay_s, 1), "label": _factor_band(relay_s),
                 "proxied": True, "note": "环境+梯队+龙头"},
        "容量": {"score": round(capacity, 1), "label": _factor_band(capacity),
                 "proxied": False, "note": "cap_score"},
        "风险": {"score": round(risk_s, 1), "label": _risk_label(risk_s),
                 "proxied": True, "note": "核按钮+断板率"},
        "潮汐": {"score": tide_s, "label": tide_l,
                 "proxied": True, "note": "环境周期代理"},
        "梯队": {"score": round(ladder, 1), "label": _ladder_label(ladder),
                 "proxied": False, "note": "ladder_health×10"},
    }

    # 中位承接（2026-08-17 修正：不再用 has_complete 布尔值——它是「龙一+龙二+龙三+≥3补涨
    # 全有」的极严格口径，涨停分散日几乎恒 False 导致全部候选刷「中位承接不足」。
    # 改用梯队健康度分级：A级(≥8)=充足 / B级(6~8)=一般 / C级(4~6)=偏弱 / D级(<4)=不足。
    # 渲染层仅对 C/D（真·梯队薄弱）提示，避免 A 级健康板块被误标。）
    _lh = float(cand.get("ladder_health") or 0.0)
    if _lh >= 8.0:
        panel["_mid_carry"] = "充足"
    elif _lh >= 6.0:
        panel["_mid_carry"] = "一般"
    else:
        panel["_mid_carry"] = "不足"
    # 综合档位（以龙头分为主，参考八维）
    panel["_band"] = _factor_band(leader)
    return panel


def relay_selection_mode_for(env: dict[str, Any], relay_count: int) -> str:
    """接力选择模式（对齐 quant 级联，但用 env 周期替代原版 tide gate）。

    strict    → 情绪升温，接力池充足（严格主升）
    relaxed   → 情绪中性，常规接力
    broad     → 情绪退潮，启用宽题材兜底（放宽至泛化题材/低位）
    emergency → 情绪退潮且接力池极薄，应急兜底（仅超预期才看）
    freeze    → 冰点（P1-6·2026-08-17 用户定）：情绪分≤2.0，**不产接力池**，
                只留观察池——冰点期只观察不接力（比 risk_off 的 broad/emergency 更防守，
                宽题材兜底在冰点同样危险）
    """
    regime = env.get("regime")
    if regime == "ice":
        return "freeze"
    if regime == "risk_on":
        return "strict"
    if regime == "neutral":
        return "relaxed"
    # risk_off
    return "broad" if relay_count >= 2 else "emergency"
