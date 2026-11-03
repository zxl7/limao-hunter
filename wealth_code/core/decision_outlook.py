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
# ⚠️ 2026-10-13 用户口径变更（B「改为动态仓位控制」）：
#    原「回撤 ≥10% → 硬禁买 3 交易日」**取消禁买**——改为按回撤深度**动态压缩单票上限**。
#    与 core.relay_position.drawdown_multiplier 同曲线（单一源；不可导入时用等价内联兜底）。
DRAWDOWN_HARD = 10.0      # 回撤警戒线（≥ → 进入深度压仓·⛔ 不再禁买）
REPAIR_MIN = 4.5          # 情绪分 ≥4.5 = 修复档（可选择性参与）
RISKON_MIN = 7.0          # 情绪分 ≥7 = 亢奋档（正常接力评估）
NUKE_CONVERGE = 5         # 核按钮 ≤5 = 亏钱效应收敛（待命触发之一）
PROMO_OK = 30.0           # 晋级率 ≥30%（待命触发之一·心法纪律）
GAP_PARTICIPATE = 5.0     # 竞价高开 ≤5% 才可参与（2026-09-14 用户「低于5%直接买」硬线·原 3.0 已废）
GAP_ABANDON = 5.0         # 竞价高开 >5% 一律放弃
STOP_LOSS_PCT = 3.0       # 止损 -3% 或破当日均价线（五日线=生命线）
# 单票仓位上限 50%（2026-09-14 用户「买入仓位不超过 50%」·原 30%）
# ⛔ 单一源 = core/risk_limits.MAX_POSITION_PER_STOCK（此处仅转出，禁再写数字）
try:  # noqa: E402
    from wealth_code.core.risk_limits import MAX_POSITION_PER_STOCK as POSITION_MAX  # noqa: E402
except ImportError:  # pragma: no cover
    from core.risk_limits import MAX_POSITION_PER_STOCK as POSITION_MAX  # noqa: E402
DRAWDOWN_FLOOR_AT = 25.0  # 回撤触底点（≥ → 压到最低乘数）
DRAWDOWN_MIN_MULT = 0.15  # 最低乘数（⛔ 不归零）

VERDICT_LABEL = {
    "deleverage": "🟠 可小仓·回撤过线压着做",
    "wait_ice": "🧊 不买·市场很冷，空仓等着",
    "wait": "👀 观望·等信号再动",
    "selective": "✅ 可做·市场在转好，挑着做",
    "buy": "🔴 可买·按候选名单执行",
    "unknown": "🟡 数据不足·不决策",
}


def _drawdown_mult(dd: float) -> float:
    """回撤 → 仓位乘数（与 core.relay_position.drawdown_multiplier 同曲线·单一源）。

    导入失败（如单测以 spec_from_file_location 独立加载本文件）→ 用等价内联兜底，
    保证判定值与生产一致（⛔ 两处曲线必须同步改）。
    """
    try:
        from core.relay_position import drawdown_multiplier as _m
        return _m(dd)[0]
    except Exception:
        if dd < 5.0:
            return 1.0
        if dd < DRAWDOWN_HARD:
            return round(1.0 + (dd - 5.0) / (DRAWDOWN_HARD - 5.0) * (0.5 - 1.0), 4)
        t = min(1.0, (dd - DRAWDOWN_HARD) / max(DRAWDOWN_FLOOR_AT - DRAWDOWN_HARD, 1e-6))
        return round(0.5 + t * (DRAWDOWN_MIN_MULT - 0.5), 4)


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
    relay_per_pick_cap: float | None = None,
) -> dict[str, Any]:
    """机械决策（纯函数）。输入全部为真实指标值，返回四段式结构。

    门控顺序（高优先级先判）：
      1) nav 回撤 ≥10%           → deleverage（可小仓·按回撤深度压单票上限·⛔ 不禁买）
      2) regime=ice 或 sentiment≤2 → wait_ice（冰点空仓待命）
      3) risk_off 或 defense≥1 或 (sentiment≤4 且 promote 走弱) → wait（观望·触发式）
      4) sentiment≥7             → buy（亢奋·接力池正常）
      5) sentiment≥4.5           → selective（修复·精选可做）
      6) 其余（3-4.4 分歧）       → wait（防守）
    """
    # ── ① 回撤过线（最高优先级·账本纪律）──────────────────────────────────
    # ⚠️ 2026-10-13 用户口径 B：「改为动态仓位控制」——**取消硬禁买 3 天**，
    #    改为按回撤深度把单票上限压小（越深越小，最低 ×0.15），风险靠仓位控制。
    if nav_drawdown_pct is not None and nav_drawdown_pct >= DRAWDOWN_HARD:
        _mult = _drawdown_mult(nav_drawdown_pct)
        # 🎯 单票上限单一源（2026-10-14）：接力动态仓位方案（core.relay_position）在跑时，
        #    以它的 per_pick_cap 为准（= 行情强弱档位 × 回撤乘数）；否则回落到
        #    POSITION_MAX × 回撤乘数。⛔ 两处各算一个数会让同一张卡出现「14% / 8%」两个上限
        #    （用户「消息和数据的准确性是底层要求」）——卡面动态仓位块与本段必须同值。
        if isinstance(relay_per_pick_cap, (int, float)) and 0 < float(relay_per_pick_cap) <= POSITION_MAX:
            _cap = round(float(relay_per_pick_cap), 4)
        else:
            _cap = round(POSITION_MAX * _mult, 4)
        _cap_txt = f"{_cap * 100:.1f}%" if _cap * 100 < 10 else f"{_cap * 100:.0f}%"
        return {
            "verdict": "deleverage",
            "tone": VERDICT_LABEL["deleverage"],
            "conclusion": (f"账户从最高点回撤了 {nav_drawdown_pct:.1f}%，已经过了 -{DRAWDOWN_HARD:.0f}% 的警戒线——"
                           f"不禁止买，但要压着做：单票最多 {_cap_txt}，最多买 1 只，"
                           f"先把回撤收窄再说进攻"),
            "basis": {"nav_drawdown_pct": round(nav_drawdown_pct, 2)},
            "triggers": [f"账户回撤收窄到 {DRAWDOWN_HARD - 2:.0f}% 以内，且做多气氛回到 {REPAIR_MIN} 分以上，"
                         "再恢复正常仓位"],
            "rules": {"position": f"≤{_cap_txt} 单票（接力池当日上限见竞价卡）",
                      "count": "最多 1 只·只做最有把握的",
                      "stop": f"跌 {STOP_LOSS_PCT:.0f}% 或跌破当天均价就走",
                      "note": "回撤没收窄之前不加仓、不摊成本"},
            "risks": ["仓位小的时候市场转好会赚得少：先保命，等回撤收窄再放大，不抢跑"],
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


def decision_from_files(root: Path, *, date: str | None = None,
                        relay_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """从真实文件载入指标 → build_decision。文件缺失 → 对应字段 None（诚实降级）。

    🎯 `relay_plan`（2026-10-14）：调用方已算好的接力动态仓位方案（`core.relay_position`）。
    传入 → 本函数的「单票上限」与卡面动态仓位块同源同值；不传 → 自行读文件补算
    （读不到 → 回落 POSITION_MAX × 回撤乘数，不阻断）。
    """
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
    # 🎯 接力动态仓位的单票上限（唯一判据源＝core.relay_position·2026-10-14）
    _rp_cap = None
    try:
        _plan = relay_plan
        if not isinstance(_plan, dict):
            from core.relay_position import build_plan_from_files as _build_plan
            _plan = _build_plan(root)
        if isinstance(_plan, dict) and _plan.get("enabled", True):
            _rp_cap = _plan.get("per_pick_cap")
    except Exception:
        _rp_cap = None
    return build_decision(
        regime=fb.get("regime"),
        sentiment=fb.get("sentiment_score"),
        promote_rate=fb.get("promote_rate"),
        nuke_count=fb.get("nuke_count"),
        avg_pct=fb.get("avg_pct"),
        promote_downtrend=bool(trend.get("promote_downtrend")),
        defense_level=int(trend.get("defense_level") or 0),
        nav_drawdown_pct=dd,
        relay_per_pick_cap=_rp_cap,
    )
