#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""早盘接力·动态仓位管理（2026-10-13 用户需求）。

把「昨日强势 + 今晨竞价 + 题材沉淀 + 净值回撤」四项真实信号，合成一个**连续的行情强弱分 S**
（0..1），再映射成可执行的仓位方案（名额 / 单票上限 / 总仓上限 / 定调），并用净值回撤做**降档守卫**，
把回撤压在更低水平。

设计原则（与既有模块同构）：
- 纯函数可单测；缺字段 → 诚实降级（对应分量取中性 0.5，标 missing），禁编造。
- 改判定口径只动本文件顶部常量（可逆）。
- 唯一判据源：本模块是接力动态仓位的唯一实现；pool_call / build_cards / ai_caliber 都来取数，禁另写一套。
- 不替代既有宏观闸门（decision_outlook 的 hard_block / ice / risk_off）——
  本模块是在「允许买」前提下，对仓位做连续微调，并把回撤软/硬线作为**最终**裁断。

────────────────────────────────────────────────────────────────────────
强弱判定（S = 0.55·S_yesterday + 0.45·S_morning）
  昨日强势 S_y（六维加权）：情绪分 / 晋级率 / 核按钮 / 炸板率 / 昨涨停次日均涨幅 / 涨跌家数占比
  今晨竞价 S_m（市场_open 六四开）：昨日完整涨停池 09:25 红开比 + 连板红开比 + 竞价量能达标比
                               + 候选池自身红开比（0<gap≤5% 计红开，gap>5% 计透支）

仓位规则（按 S 分三档，再乘回撤降档系数）
  强（S≥0.75）  ：名额 3 / 单票≤50% / 总仓≤100%
  中（0.45≤S<0.75）：名额 2 / 单票≤40% / 总仓≤60%
  弱（S<0.45）  ：名额 1 / 单票≤30% / 总仓≤30%

回撤控制（衡量＝账户净值账本峰值→当前 · 2026-10-13 用户口径 B「改为动态仓位控制」）
  ⛔ **不禁买**：回撤只压仓位、不清零（乘数恒 >0 ⇒ can_buy 恒 True），风险靠仓位大小控制。
  曲线（连续分段线性·乘数同乘「名额 / 单票 / 总仓」）：
    <5%            → ×1.00（正常）
    5% ~ 10%       → ×1.00 ~ ×0.50（起降段）
    10% ~ 25%      → ×0.50 ~ ×0.15（过警戒线后继续压）
    ≥25%           → ×0.15（触底·仍可买，只是很小）
  名额保底 1：弱档名额 1 被压后四舍五入成 0 会变成"变相不买" → 显式保底 1 只。

题材沉淀置信加成（用户 2026-10-13「题材已沉淀·接力更友好」）
  全局：top5_history 累计归档天数 day_count 越多 → 题材库越全 → 给 S 一个温和加成（封顶 +15%）。
  个股：候选所属题材若已在题材库、且被追踪 ≥ HOT_THEME_MIN_DAYS 天 → known_theme=True（更友好）。
  候选池整体 known 占比越高 → 再给 S 一个温和上修（封顶 +8%）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# core 既可由财富密码 CLI 导入，也可由项目根脚本导入；日历/路径按项目根处理。
ROOT = Path(__file__).resolve().parent.parent            # wealth_code/
HUNTER_ROOT = ROOT.parent                                # 狸猫猎手/
if str(HUNTER_ROOT) not in sys.path:
    sys.path.insert(0, str(HUNTER_ROOT))

# ── 总开关（可逆·与既有 POOL_CALL_* / AI_CALIBER_ACCOUNT 同构）────────────────
# True  = 接力走动态仓位（本模块裁决名额/单票/总仓上限）
# False = 逐字回退「名额只由盘后 stance 定、单票恒 ≤50%」的旧行为
RELAY_POSITION_ENABLED = True

# ── 强弱合成权重 ───────────────────────────────────────────────────────────
W_YESTERDAY = 0.55
W_MORNING = 0.45

# 昨日六维权重（和=1）
W_SENTIMENT = 0.30
W_PROMOTE = 0.20
W_NUKE = 0.20
W_BROKEN = 0.15
W_AVGPCT = 0.10
W_UPRATE = 0.05

# 各维归一区间/分母（越接近越好/越差已通过方向处理）
AVGPCT_LO, AVGPCT_HI = -2.0, 4.0      # 昨涨停次日均涨幅 -2..+4 → 0..1
UPRATE_LO, UPRATE_HI = 30.0, 70.0     # 涨跌家数占比 30..70 → 0..1
NUKE_NORM = 10.0                      # 核按钮 ≥10 只 = 满分弱
BROKEN_NORM = 40.0                    # 炸板率 ≥40% = 满分弱
PROMOTE_NORM = 30.0                   # 晋级率 30% = 满分强

# 今晨竞价权重（市场_open 六四开）
W_OPEN = 0.6
W_CAND = 0.4

# ── 三档阈值（S 连续分）────────────────────────────────────────────────────
TIER_STRONG = 0.75
TIER_NEUTRAL = 0.45

# 各档仓位方案（名额 / 单票上限 / 总仓上限）
QUOTA_STRONG, PER_PICK_STRONG, TOTAL_STRONG = 3, 0.50, 1.00
QUOTA_NEUTRAL, PER_PICK_NEUTRAL, TOTAL_NEUTRAL = 2, 0.40, 0.60
QUOTA_WEAK, PER_PICK_WEAK, TOTAL_WEAK = 1, 0.30, 0.30

# ── 回撤 → 仓位（2026-10-13 用户口径 B「改为动态仓位控制」）──────────────────
# ⛔ 不再有「禁买档」：回撤再深也只把仓位压小、不清零（can_buy 恒 True）。
#    风险靠「仓位变小」控制，而非「一刀切不买」——避免深回撤时长期空转踏空。
#    曲线（分段线性·连续动态）：<5% → 1.0 ｜ 5%~10% 线性 → 0.5 ｜ 10%~25% 线性 → 0.15 ｜ ≥25% → 0.15。
DRAWDOWN_SOFT = 5.0                   # 起降点：回撤 ≥5% 开始压仓位
DRAWDOWN_HARD = 10.0                  # 重度警戒线（**仅提示**·不再禁买）→ 该点已压到 0.5
DRAWDOWN_MULT_AT_HARD = 0.5           # 到重度警戒线时的乘数
DRAWDOWN_FLOOR_AT = 25.0              # 触底点：回撤 ≥25% 压到最低乘数
DRAWDOWN_MIN_MULT = 0.15              # 最低乘数（⛔ 不归零·永远保留可买能力）

# ── 题材沉淀置信 ───────────────────────────────────────────────────────────
THEME_TARGET_DAYS = 60                # 满库参考天数（约一季）
THEME_BONUS_GAIN = 0.15               # S 全局加成封顶 +15%
HOT_THEME_MIN_DAYS = 3                # 个股题材被追踪≥此天数 → known_theme
KNOWN_RATIO_GAIN = 0.08               # 候选整体 known 占比对 S 的上修封顶 +8%


# ─────────────────────────────────────────────────────────────────────────────
# 基础工具
# ─────────────────────────────────────────────────────────────────────────────
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def clamp01(x: float) -> float:
    return clamp(x, 0.0, 1.0)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lin_norm(v: Any, lo: float, hi: float) -> float | None:
    """线性归一到 0..1（v≤lo→0，v≥hi→1）；非数 → None（诚实降级）。"""
    n = _num(v)
    if n is None:
        return None
    if hi == lo:
        return 1.0 if n >= hi else 0.0
    return clamp01((n - lo) / (hi - lo))


# ─────────────────────────────────────────────────────────────────────────────
# ① 昨日强势 S_y（feedback_matrix.json · 唯一权威）
# ─────────────────────────────────────────────────────────────────────────────
def strength_yesterday(fb: dict[str, Any] | None) -> tuple[float, dict[str, Any]]:
    """六维加权合成「昨日强势分」∈[0,1]，越高=昨日环境越强。

    各分量（已朝「强=1」方向归一）：
      sentiment 情绪分/10
      promote   晋级率/PROMOTE_NORM（≥30% 记满分）
      nuke      1 - 核按钮/NUKE_NORM（核按钮越多越弱）
      broken    1 - 炸板率/BROKEN_NORM
      avgpct    (avg_pct - AVGPCT_LO)/(AVGPCT_HI - AVGPCT_LO)
      uprate    (up_rate - UPRATE_LO)/(UPRATE_HI - UPRATE_LO)
    缺字段 → 该分量 0.5（中性），并在 parts 标 missing。
    """
    fb = fb if isinstance(fb, dict) else {}
    parts: dict[str, Any] = {}

    def comp(key: str, val: float | None) -> float:
        if val is None:
            parts[f"{key}_missing"] = True
            return 0.5
        return val

    s_sent = comp("sentiment", _lin_norm(fb.get("sentiment_score"), 0.0, 10.0))
    s_promo = comp("promote", _lin_norm(fb.get("promote_rate"), 0.0, PROMOTE_NORM))
    # 核按钮/炸板率：缺原始数据 → 传 None 给 comp（中性 0.5），不把「缺」当「无弱点=强」。
    _nuke = _num(fb.get("nuke_count"))
    _broken = _num(fb.get("broken_rate"))
    s_nuke = comp("nuke", None if _nuke is None else 1.0 - clamp01(_nuke / NUKE_NORM))
    s_broken = comp("broken", None if _broken is None else 1.0 - clamp01(_broken / BROKEN_NORM))
    s_avg = comp("avgpct", _lin_norm(fb.get("avg_pct"), AVGPCT_LO, AVGPCT_HI))
    s_up = comp("uprate", _lin_norm(fb.get("up_rate"), UPRATE_LO, UPRATE_HI))

    score = (
        W_SENTIMENT * s_sent + W_PROMOTE * s_promo + W_NUKE * s_nuke
        + W_BROKEN * s_broken + W_AVGPCT * s_avg + W_UPRATE * s_up
    )
    parts.update({
        "sentiment": round(s_sent, 3), "promote": round(s_promo, 3),
        "nuke": round(s_nuke, 3), "broken": round(s_broken, 3),
        "avgpct": round(s_avg, 3), "uprate": round(s_up, 3),
        "score": round(clamp01(score), 4),
    })
    return clamp01(score), parts


# ─────────────────────────────────────────────────────────────────────────────
# ② 今晨竞价 S_m（market_open 09:25 读数 + 候选池自身竞价）
# ─────────────────────────────────────────────────────────────────────────────
def _gap_of(q: dict[str, Any] | None) -> float | None:
    if not isinstance(q, dict):
        return None
    p, prev = _num(q.get("auction_price")), _num(q.get("prev_close"))
    if p is None or prev is None or p <= 0 or prev <= 0:
        return None
    return round((p / prev - 1) * 100, 2)


def _flatten_candidates(candidates: Any) -> list[dict]:
    if isinstance(candidates, dict):
        arr: list[dict] = []
        for k in ("relay", "watch", "early_ambush"):
            arr.extend([c for c in (candidates.get(k) or []) if isinstance(c, dict)])
        return arr
    if isinstance(candidates, list):
        return [c for c in candidates if isinstance(c, dict)]
    return []


def strength_morning(market_open: dict[str, Any] | None,
                     candidates: Any = None,
                     quotes: dict[str, dict[str, Any]] | None = None) -> tuple[float, dict[str, Any]]:
    """合成「今晨竞价分」∈[0,1]。

    市场_open 分量（来自 core.market_open.build_market_open 读数）：
      红开比 red_open_ratio + 连板红开比 leader_red_open_ratio + 竞价量能达标比
      leader_volume_pass_ratio（三者各 1/3，缺则用该分量 0.5 中性）。
    候选池分量：候选票里 0<gap≤5% 的占比（红开且未透支），减去 gap>5% 的透支惩罚。
    全缺 → S_m=0.5（中性·诚实降级）。
    """
    parts: dict[str, Any] = {}
    mo = market_open if isinstance(market_open, dict) else {}

    def _open_sub(key: str, fallback: float = 0.5) -> float:
        n = _num(mo.get(key))
        if n is None:
            parts[f"open_{key}_missing"] = True
            return fallback
        return clamp01(n)

    open_score = (
        (_open_sub("red_open_ratio") + _open_sub("leader_red_open_ratio")
         + _open_sub("leader_volume_pass_ratio")) / 3.0
    )
    parts["open_score"] = round(open_score, 3)
    parts["open_ready"] = bool(mo.get("ready"))

    # 候选池自身竞价分布
    cands = _flatten_candidates(candidates)
    qs = quotes or {}
    gaps: list[float] = []
    for c in cands:
        code = str(c.get("code") or "").strip()
        if len(code) == 6:
            g = _gap_of(qs.get(code))
            if g is not None:
                gaps.append(g)
    cand_score = 0.5
    if gaps:
        red_ok = sum(1 for g in gaps if 0 < g <= 5.0)
        over = sum(1 for g in gaps if g > 5.0)
        cand_score = clamp01(red_ok / len(gaps) - 0.5 * (over / len(gaps)))
        parts["cand_count"] = len(gaps)
        parts["cand_red_ok"] = red_ok
        parts["cand_over"] = over
    else:
        parts["cand_missing"] = True
    parts["cand_score"] = round(cand_score, 3)

    score = W_OPEN * open_score + W_CAND * cand_score
    parts["score"] = round(clamp01(score), 4)
    return clamp01(score), parts


# ─────────────────────────────────────────────────────────────────────────────
# ③ 题材沉淀置信（top5_history/theme_index.json）
# ─────────────────────────────────────────────────────────────────────────────
def theme_accumulation_confidence(theme_index: dict[str, Any] | None) -> tuple[float, dict[str, Any]]:
    """全局题材库置信 = 累计归档天数 / 满库参考天数，封顶 1。

    返回 (confidence, {day_count, theme_count})；缺文件 → (0.0, {missing:True})。
    """
    info: dict[str, Any] = {}
    if not isinstance(theme_index, dict) or "day_count" not in theme_index:
        info["missing"] = True
        return 0.0, info
    day_count = int(theme_index.get("day_count") or 0)
    info["day_count"] = day_count
    info["theme_count"] = int(theme_index.get("theme_count") or 0)
    conf = clamp01(day_count / THEME_TARGET_DAYS)
    info["confidence"] = round(conf, 3)
    return conf, info


def _candidate_theme_known_ratio(candidates: Any,
                                 theme_index: dict[str, Any] | None) -> tuple[float, list[dict]]:
    """候选池里「所属题材已被题材库追踪 ≥ HOT_THEME_MIN_DAYS」的占比。

    返回 (known_ratio, [标记了 known_theme 的候选])；题材库缺 → (0.0, 候选原样)。
    """
    cands = _flatten_candidates(candidates)
    if not cands:
        return 0.0, cands
    themes = (theme_index or {}).get("themes") if isinstance(theme_index, dict) else None
    if not isinstance(themes, dict):
        for c in cands:
            c["known_theme"] = False
        return 0.0, cands
    known = 0
    marked: list[dict] = []
    for c in cands:
        fam = str(c.get("theme_name") or c.get("theme") or c.get("sector") or "").strip()
        meta = themes.get(fam)
        d = int((meta or {}).get("days") or 0)
        c = dict(c)
        if d >= HOT_THEME_MIN_DAYS:
            c["known_theme"] = True
            c["theme_tracked_days"] = d
            known += 1
        else:
            c["known_theme"] = False
        marked.append(c)
    ratio = known / len(cands) if cands else 0.0
    return ratio, marked


# ─────────────────────────────────────────────────────────────────────────────
# ④ 净值回撤（账户账本峰值→当前；与 decision_outlook 同源算法）
# ─────────────────────────────────────────────────────────────────────────────
def nav_drawdown(root: Path | None = None) -> tuple[float | None, float | None, float | None]:
    """读 account_nav_history.jsonl → (回撤%, 峰值, 当前)。

    回撤% = (当前/峰值 - 1) * -100。缺文件/无数据 → (None, None, None)（诚实降级）。
    ⚠️ 净值账本固定在 wealth_code/cache（与 decision_outlook.decision_from_files 同源路径），
       不随 root 参数变化。
    """
    p = ROOT / "cache" / "account_nav_history.jsonl"
    if not p.exists():
        return None, None, None
    try:
        navs: list[float] = []
        latest: float | None = None
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            v = _num(row.get("nav"))
            if v is None:
                continue
            navs.append(v)
            latest = v
        if not navs or latest is None:
            return None, None, None
        peak = max(navs)
        if peak <= 0:
            return None, None, None
        dd = round((latest / peak - 1) * -100, 2)
        return dd, round(peak, 6), round(latest, 6)
    except Exception:
        return None, None, None


def drawdown_multiplier(dd: float | None) -> tuple[float, str | None]:
    """回撤 → 仓位乘数（**连续**动态压缩）。

    ⛔ 2026-10-13 用户口径（B·改为动态仓位控制）：**取消禁买**——回撤再深也只压仓位，
       乘数恒 >0 ⇒ `can_buy` 恒 True。风险用「仓位变小」控制，不用「一刀切不买」。
    曲线（分段线性）：<5% → 1.0 ｜ 5%~10% → 1.0~0.5 ｜ 10%~25% → 0.5~0.15 ｜ ≥25% → 0.15。
    None（无净值数据）→ (1.0, None)（不降档·诚实降级）。
    """
    if dd is None:
        return 1.0, None
    if dd < DRAWDOWN_SOFT:
        return 1.0, "normal"
    if dd < DRAWDOWN_HARD:
        t = (dd - DRAWDOWN_SOFT) / (DRAWDOWN_HARD - DRAWDOWN_SOFT)
        return round(1.0 + t * (DRAWDOWN_MULT_AT_HARD - 1.0), 4), "soft"
    span = max(DRAWDOWN_FLOOR_AT - DRAWDOWN_HARD, 1e-6)
    t = min(1.0, (dd - DRAWDOWN_HARD) / span)
    mult = DRAWDOWN_MULT_AT_HARD + t * (DRAWDOWN_MIN_MULT - DRAWDOWN_MULT_AT_HARD)
    return round(mult, 4), "heavy"


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ 合成 S → 三档 → 仓位方案
# ─────────────────────────────────────────────────────────────────────────────
def tier_of(s: float) -> str:
    if s >= TIER_STRONG:
        return "strong"
    if s >= TIER_NEUTRAL:
        return "neutral"
    return "weak"


_TIER_PLAN = {
    "strong": (QUOTA_STRONG, PER_PICK_STRONG, TOTAL_STRONG),
    "neutral": (QUOTA_NEUTRAL, PER_PICK_NEUTRAL, TOTAL_NEUTRAL),
    "weak": (QUOTA_WEAK, PER_PICK_WEAK, TOTAL_WEAK),
}

_TIER_LABEL = {"strong": "强·可放大", "neutral": "中·正常", "weak": "弱·收紧"}


def tier_caps(tier: str) -> tuple[int, float, float]:
    """某档的**基础**仓位方案（名额 / 单票上限 / 总仓上限）·与回撤无关。

    ⛔ 边界：这是「规划口径」（T-1 盘后落盘用）——只考虑行情强弱档位，
       不含净值回撤的软/硬线裁断（回撤线只在 09:25 执行时点才可知、才生效）。
       回撤降档由 build_plan / refine_effective_quota / apply_total_cap 在执行层叠加。
    """
    return _TIER_PLAN.get(tier, _TIER_PLAN["neutral"])


def build_plan(*, strength: float, tier: str,
               theme_confidence: float = 0.0,
               known_ratio: float = 0.0,
               dd: float | None = None,
               theme_day_count: int = 0,
               s_components: dict | None = None,
               theme_info: dict | None = None,
               morning_parts: dict | None = None,
               yesterday_parts: dict | None = None) -> dict[str, Any]:
    """由连续分 S + 回撤 + 题材置信 → 完整仓位方案（唯一输出结构）。"""
    s = clamp01(strength)
    tier = tier_of(s)
    quota, per_pick, total = _TIER_PLAN[tier]

    mult, dd_level = drawdown_multiplier(dd)
    # ⛔ 2026-10-13 口径（B）：**回撤不再禁买**——mult 恒 >0 ⇒ can_buy 恒 True。
    #    保留 can_buy 字段（schema 兼容 / 供外部注入 plan 时表达"未启用"），但本模块不产 False。
    can_buy = mult > 0.0
    eff_quota = int(round(quota * mult)) if can_buy else 0
    # 名额保底 1：弱档名额=1 时被乘数四舍五入成 0 会变成"变相不买" → 显式保底。
    # （缩的是单票/总仓尺寸，不是把「可买」变成「不买」。）
    if can_buy and quota >= 1 and eff_quota < 1:
        eff_quota = 1
    eff_per_pick = round(per_pick * mult, 4) if can_buy else 0.0
    eff_total = round(total * mult, 4) if can_buy else 0.0

    hints: list[str] = []
    if dd_level == "heavy":
        hints.append(f"账户回撤 -{dd:.2f}% 已过警戒线 {DRAWDOWN_HARD:.0f}% → "
                     f"仓位已自动压到 {mult * 100:.0f}%（动态控制·不禁买）")
    elif dd_level == "soft":
        hints.append(f"账户回撤 -{dd:.2f}% 已过起降线 {DRAWDOWN_SOFT:.0f}% → "
                     f"仓位已自动压到 {mult * 100:.0f}%（动态控制）")
    if theme_confidence >= 0.5 and known_ratio >= 0.6:
        hints.append("题材库已沉淀（多数候选属已追踪题材）→ 接力置信更高·可正常给仓位")
    elif theme_day_count and theme_confidence < 0.5:
        hints.append(f"题材库仍在积累（已归档 {theme_day_count} 天）→ 陌生题材从严")

    return {
        "enabled": RELAY_POSITION_ENABLED,
        "strength": round(s, 4),
        "tier": tier,
        "tier_label": _TIER_LABEL[tier],
        "quota": eff_quota,
        "per_pick_cap": eff_per_pick,
        "total_cap": eff_total,
        "can_buy": can_buy,
        "drawdown_pct": dd,
        "drawdown_level": dd_level,
        "drawdown_mult": mult,
        "theme_confidence": round(theme_confidence, 3),
        "theme_known_ratio": round(known_ratio, 3),
        "hints": hints,
        # 诊断明细（卡面/日志用·禁直接上卡）
        "_diag": {
            "s_components": s_components or {},
            "morning": morning_parts or {},
            "yesterday": yesterday_parts or {},
            "theme_info": theme_info or {},
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# ⑥ 编排：读真实文件 → 方案（pool_call / build_cards 统一入口）
# ─────────────────────────────────────────────────────────────────────────────
def _load_feedback(root: Path) -> dict:
    p = root / "cache" / "feedback_matrix.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load_theme_index(root: Path) -> dict | None:
    p = root / "cache" / "top5_history" / "theme_index.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return None


def build_plan_from_files(root: Path | None = None, *,
                          feedback: dict | None = None,
                          market_open: dict | None = None,
                          candidates: Any = None,
                          quotes: dict | None = None,
                          theme_index: dict | None = None) -> dict[str, Any]:
    """读真实文件合成动态仓位方案。各数据源均可外部注入（测试/管线复用）。

    入参缺省 → 从项目根 cache 读；读不到 → 对应分量诚实降级（不编造）。
    """
    base = Path(root) if root else HUNTER_ROOT
    fb = feedback if isinstance(feedback, dict) else _load_feedback(base)
    ti = theme_index if isinstance(theme_index, dict) else _load_theme_index(base)

    s_y, y_parts = strength_yesterday(fb)
    s_m, m_parts = strength_morning(market_open, candidates, quotes)

    # 题材沉淀加成（先算全局置信，再算候选 known 占比）
    theme_conf, theme_info = theme_accumulation_confidence(ti)
    known_ratio, _ = _candidate_theme_known_ratio(candidates, ti)

    s_raw = W_YESTERDAY * s_y + W_MORNING * s_m
    # 全局题材加成：封顶 +THEME_BONUS_GAIN
    s_bonus = min(1.0, s_raw * (1.0 + THEME_BONUS_GAIN * theme_conf))
    # 候选 known 占比上修：封顶 +KNOWN_RATIO_GAIN（只在未封顶空间内加）
    gain_known = KNOWN_RATIO_GAIN * known_ratio * (1.0 - s_bonus)
    s_final = clamp01(s_bonus + gain_known)

    dd, peak, cur = nav_drawdown(base)
    plan = build_plan(
        strength=s_final, tier=tier_of(s_final),
        theme_confidence=theme_conf, known_ratio=known_ratio, dd=dd,
        theme_day_count=int((theme_info or {}).get("day_count") or 0),
        s_components={"s_yesterday": round(s_y, 4), "s_morning": round(s_m, 4),
                      "s_raw": round(s_raw, 4), "s_final": round(s_final, 4),
                      "theme_bonus_gain": round(s_bonus - s_raw, 4),
                      "known_gain": round(gain_known, 4)},
        theme_info=theme_info, morning_parts=m_parts, yesterday_parts=y_parts,
    )
    plan["nav_peak"] = peak
    plan["nav_latest"] = cur
    return plan


# ── 给 pool_call 用的便捷取数（不重复合成，直接拿方案）─────────────────────────
def refine_effective_quota(effective_quota: int, plan: dict[str, Any] | None) -> int:
    """pool_call 在算完防守闸后，用动态仓位方案对名额做**最终向下裁断**。

    动态方案只在「允许买」时收紧名额（取 min）；方案禁买 → 名额归 0。
    绝不放大名额（不越权覆盖 stance / 防守闸）。
    """
    if not isinstance(plan, dict) or not plan.get("enabled", True):
        return effective_quota
    if not plan.get("can_buy", True):
        return 0
    return min(int(effective_quota), int(plan.get("quota", effective_quota)))


def apply_total_cap(positions: list[float | None], plan: dict[str, Any] | None) -> list[float | None]:
    """把一组单票计划仓位（与同批次候选一一对应）裁到「总仓上限」以内。

    规则（与卡面/决策层同源，唯一判据源）：
      · 方案缺失 / 未启用（开关关）→ **原样返回**（诚实降级·回退旧口径，⛔ 不得归零：
        「没有动态方案」≠「不许买」，开关一关就归零会误伤全系统）。
      · 方案启用但 can_buy=False（回撤破硬线）→ 全部归 0（与 refine_effective_quota 一致）。
      · 总仓上限 total_cap 缺失或为 0 → 原样返回（不编造）。
      · 各单票先各自夹紧到 per_pick_cap（调用方已夹过则幂等），再求总和；
        总和 > total_cap → **等比缩放**到 total_cap（保持各票相对权重，不偏袒）。
    返回长度与原列表一致；非数值项（None）保持 None。
    """
    if not isinstance(plan, dict) or not plan.get("enabled", True):
        return list(positions)                      # 无方案/开关关 → 不动（回退旧口径）
    if not plan.get("can_buy", True):
        return [0.0 if isinstance(p, (int, float)) else None for p in positions]
    total_cap = plan.get("total_cap")
    if not isinstance(total_cap, (int, float)) or total_cap <= 0:
        return list(positions)
    per_cap = plan.get("per_pick_cap")
    _vals: list[float] = []
    _idx: list[int] = []
    for i, p in enumerate(positions):
        if isinstance(p, (int, float)) and p > 0:
            v = min(p, float(per_cap)) if isinstance(per_cap, (int, float)) and per_cap > 0 else float(p)
            _vals.append(v)
            _idx.append(i)
    s = sum(_vals)
    if s <= total_cap or s <= 0:
        out = list(positions)
        for k, i in enumerate(_idx):
            out[i] = round(_vals[k], 4)
        return out
    scale = total_cap / s
    out = list(positions)
    for k, i in enumerate(_idx):
        out[i] = round(_vals[k] * scale, 4)
    return out


if __name__ == "__main__":
    import pprint
    pprint.pprint(build_plan_from_files())
