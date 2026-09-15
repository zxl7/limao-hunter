#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主导决策执行层（2026-09-09 用户「吸收这套决策，结合选股财富密码系统」）。

把盘后一次性「买入决策文案」固化为**机械决策层**：读真实文件 → 输出四段式决策结构
（结论 / 依据 / 交易计划含触发 / 风险纪律），eod 卡自动渲染「🎯 明日执行判定」段。
原则：
- 纯函数可单测；输入缺字段 → 诚实降级（verdict=unknown + 标注缺哪些），禁编造。
- 决策只给「环境判定 + 触发式执行计划」，不替人做最终动作（衔接人机混合决策 design）。
- 改门控阈值只动本文件顶部常量（可逆）；数据源=feedback_matrix.json 唯一权威。
口径基准（09-09 主导决策实盘校准）：
  ice 冰点 → 不买·空仓待命；退潮/risk_off/趋势防守 → 观望·触发式；
  修复(≥4.5) → 选择性可做（1 只容量核心·≤30% 仓）；亢奋(≥7) → 按接力池正常评估。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# ── 门控阈值（可逆常量·改动勿动结构）────────────────────────────────────
DRAWDOWN_HARD = 10.0      # 净值回撤 ≥10% → 硬禁买 3 交易日（账本纪律叠加）
REPAIR_MIN = 4.5          # 情绪分 ≥4.5 = 修复档（可选择性参与）
RISKON_MIN = 7.0          # 情绪分 ≥7 = 亢奋档（正常接力评估）
NUKE_CONVERGE = 5         # 核按钮 ≤5 = 亏钱效应收敛（待命触发之一）
PROMO_OK = 30.0           # 晋级率 ≥30%（待命触发之一·心法纪律）
GAP_PARTICIPATE = 3.0     # 竞价高开 ≤3% 才可参与（>3% 防诱多）
GAP_ABANDON = 5.0         # 竞价高开 >5% 一律放弃
STOP_LOSS_PCT = 3.0       # 止损 -3% 或破当日均价线（五日线=生命线）
POSITION_MAX = 0.5        # 单票仓位上限 50%（2026-09-14 用户「买入仓位不超过 50%」·原 30%）
HARD_BLOCK_DAYS = 3       # 破线硬禁买天数

VERDICT_LABEL = {
    "hard_block": "⛔ 不买·已跌破止损线",
    "wait_ice": "🧊 不买·市场很冷，空仓等着",
    "wait": "👀 观望·等信号再动",
    "selective": "✅ 可做·市场在转好，挑着做",
    "buy": "🔴 可买·按候选名单执行",
    "unknown": "🟡 数据不足·不决策",
}


def build_decision(
    *,
    regime: str | None = None,
    sentiment: float | None = None,
    promote_rate: float | None = None,
    nuke_count: int | None = None,
    avg_pct: float | None = None,
    promote_downtrend: bool = False,
    defense_level: int = 0,
    nav_drawdown_pct: float | None = None,
) -> dict[str, Any]:
    """机械决策（纯函数）。输入全部为真实指标值，返回四段式结构。

    门控顺序（高优先级先判）：
      1) nav 回撤 ≥10%           → hard_block（3 交易日禁买）
      2) regime=ice 或 sentiment≤2 → wait_ice（冰点空仓待命）
      3) risk_off 或 defense≥1 或 (sentiment≤4 且 promote 走弱) → wait（观望·触发式）
      4) sentiment≥7             → buy（亢奋·接力池正常）
      5) sentiment≥4.5           → selective（修复·精选可做）
      6) 其余（3-4.4 分歧）       → wait（防守）
    """
    # ── ① 破线硬禁买（最高优先级·账本纪律）──
    if nav_drawdown_pct is not None and nav_drawdown_pct >= DRAWDOWN_HARD:
        return {
            "verdict": "hard_block",
            "tone": VERDICT_LABEL["hard_block"],
            "conclusion": (f"账户从最高点回撤了 {nav_drawdown_pct:.1f}%，已经跌破 -{DRAWDOWN_HARD:.0f}% 的止损线，"
                           f"接下来 {HARD_BLOCK_DAYS} 个交易日禁买——先止血，再谈进攻"),
            "basis": {"nav_drawdown_pct": round(nav_drawdown_pct, 2)},
            "triggers": [f"账户回撤收窄到 {DRAWDOWN_HARD - 2:.0f}% 以内，且做多气氛回到 {REPAIR_MIN} 分以上"],
            "rules": {"position": 0, "note": "禁买期间·只做减仓和防守"},
            "risks": ["禁买期间如果市场转好会踏空：按纪律 3 天后再重新评估，不抢跑"],
        }

    if sentiment is None:
        return {
            "verdict": "unknown", "tone": VERDICT_LABEL["unknown"],
            "conclusion": "市场气氛数据还没出来——先不决策，等 9:26 数据更新",
            "basis": {}, "triggers": [], "rules": {"position": 0}, "risks": [],
        }

    # ── ② 冰点（ice ≤2）──
    if regime == "ice" or sentiment <= 2.0:
        ice_note = "，而且昨天涨停的票今天能继续涨停的越来越少" if promote_downtrend else ""
        return {
            "verdict": "wait_ice",
            "tone": VERDICT_LABEL["wait_ice"],
            "conclusion": (f"今天做多气氛 {sentiment:.1f} 分（满分10）{ice_note}，市场很冷——"
                           "空仓就是最确定的赚钱方式，一股不买，等着市场转好"),
            "basis": {"sentiment": round(sentiment, 1), "promote_rate": promote_rate,
                      "nuke_count": nuke_count,
                      "regime": regime, "defense_level": defense_level},
            "triggers": [
                f"开盘做多气氛 ≥{REPAIR_MIN:.1f} 分，昨天涨停今天被砸的少于 {NUKE_CONVERGE} 只，"
                "并且出现板块级的真实利好消息",
                "（三条满足两条就可以重新评估，否则继续空着）"],
            "rules": {"position": 0,
                      "entry": f"如果重新评估：只做开盘涨幅 ≤{GAP_PARTICIPATE:.0f}% 的，涨超 {GAP_ABANDON:.0f}% 就放弃",
                      "stop": f"跌 {STOP_LOSS_PCT:.0f}% 或跌破当天均价就走", "note": "不要越跌越买摊低成本"},
            "risks": ["市场转好第一天会踏空：转好不会一天完成，等 10:00 后确认昨天涨停票今天的表现再动",
                      "突发利空扩大：昨天涨停今天被砸的票不减少，就全天不动"],
        }

    # ── ③ 退潮/防守（risk_off、趋势防守、或分歧走弱）──
    risk_off = regime == "risk_off" or defense_level >= 1 or (
        sentiment <= 4.0 and promote_downtrend)
    if risk_off:
        return {
            "verdict": "wait",
            "tone": VERDICT_LABEL["wait"],
            "conclusion": (f"今天做多气氛 {sentiment:.1f} 分（满分10），环境偏防守"
                           f"{'，而且昨天涨停的票今天能继续涨停的越来越少' if promote_downtrend else ''}"
                           "——观望为主，等信号明确了再动手，不主动进攻"),
            "basis": {"sentiment": round(sentiment, 1), "promote_rate": promote_rate,
                      "nuke_count": nuke_count, "avg_pct": avg_pct,
                      "regime": regime, "defense_level": defense_level},
            "triggers": [f"做多气氛 ≥{REPAIR_MIN:.1f} 分，昨天涨停今天被砸的少于 {NUKE_CONVERGE} 只，"
                         f"且昨天涨停今天继续涨停的比例 ≥{PROMO_OK:.0f}%"],
            "rules": {"position": f"≤{POSITION_MAX * 100:.0f}% 单票", "count": "最多 1 只·只做成交活跃的龙头",
                      "entry": f"开盘涨幅 ≤{GAP_PARTICIPATE:.0f}% 才参与，超 {GAP_ABANDON:.0f}% 放弃",
                      "stop": f"跌 {STOP_LOSS_PCT:.0f}% 或跌破当天均价就走", "note": "一次买够·不要越跌越买"},
            "risks": ["市场只转好一天就会错过：等昨天涨停今天继续涨停的比例回到 30% 以上，第二天再评估",
                      "出现政策级真实利好的例外：盘前有板块级的硬消息（不是跟着美股涨），"
                      "且开盘做多气氛 4.5 分以上，才允许按「挑着做」执行"],
        }

    # ── ④ 亢奋（≥7）──
    if sentiment >= RISKON_MIN:
        return {
            "verdict": "buy",
            "tone": VERDICT_LABEL["buy"],
            "conclusion": f"今天做多气氛 {sentiment:.1f} 分（满分10），市场很热——候选名单按正常流程评估执行",
            "basis": {"sentiment": round(sentiment, 1), "promote_rate": promote_rate,
                      "nuke_count": nuke_count, "regime": regime},
            "triggers": [],
            "rules": {"position": f"单票 ≤{POSITION_MAX * 100:.0f}%·可以分散多只",
                      "entry": "按开盘涨幅和成交量对照预期：明显强于预期就买，开盘涨超 5% 先看着不追",
                      "stop": f"跌 {STOP_LOSS_PCT:.0f}% 或跌破 5 日线就走"},
            "risks": ["市场转冷很快：开盘涨超 5% 也只观察不追"],
        }

    # ── ⑤ 修复（4.5-6.9）──
    return {
        "verdict": "selective",
        "tone": VERDICT_LABEL["selective"],
        "conclusion": (f"今天做多气氛 {sentiment:.1f} 分（满分10），市场在转好——可以做但要挑："
                       "只做 1 只有真实逻辑支撑的龙头，不碰没逻辑的跟风票"),
        "basis": {"sentiment": round(sentiment, 1), "promote_rate": promote_rate,
                  "nuke_count": nuke_count, "regime": regime},
        "triggers": [],
        "rules": {"position": f"≤{POSITION_MAX * 100:.0f}% 单票·≤1-2 只",
                  "entry": f"开盘涨幅 ≤{GAP_PARTICIPATE:.0f}% 才参与，超 {GAP_ABANDON:.0f}% 放弃",
                  "stop": f"跌 {STOP_LOSS_PCT:.0f}% 或跌破当天均价、5 日线就走",
                  "note": "只做当天最强方向里的龙头"},
        "risks": ["转好可能不真实（缩量反弹）：要看昨天涨停今天继续涨停的比例能否回到 30%，回不去就降为观望"],
    }


def _json_load(p: Path) -> dict:
    import json
    return json.loads(p.read_text(encoding="utf-8"))


def _json_loads_line(line: str) -> dict | None:
    import json
    try:
        return json.loads(line)
    except Exception:
        return None


def decision_from_files(root: Path, *, date: str | None = None) -> dict[str, Any]:
    """从真实文件载入指标 → build_decision。文件缺失 → 对应字段 None（诚实降级）。"""
    fb_p = root / "cache" / "feedback_matrix.json"
    fb: dict = {}
    try:
        fb = _json_load(fb_p) or {}
    except Exception:
        pass
    trend = fb.get("trend") or {}
    # 账本净值回撤
    dd = None
    try:
        navs = [_json_loads_line(l) for l in (root / "wealth_code" / "cache" / "account_nav_history.jsonl").read_text().splitlines()]
        navs = [n for n in navs if n and isinstance(n.get("nav"), (int, float))]
        if navs:
            peak = max(float(n["nav"]) for n in navs)
            cur = float(navs[-1]["nav"])
            if peak > 0:
                dd = round((cur / peak - 1) * -100, 2)
    except Exception:
        pass
    return build_decision(
        regime=fb.get("regime"),
        sentiment=fb.get("sentiment_score"),
        promote_rate=fb.get("promote_rate"),
        nuke_count=fb.get("nuke_count"),
        avg_pct=fb.get("avg_pct"),
        promote_downtrend=bool(trend.get("promote_downtrend")),
        defense_level=int(trend.get("defense_level") or 0),
        nav_drawdown_pct=dd,
    )
