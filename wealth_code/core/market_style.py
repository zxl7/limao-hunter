#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指数环境 · 风格切换层（🌊 宪法 2026-10-19「上海 7.10 聚会心得」·用户定）

一句话：**情绪分回答「能不能做」，指数形态回答「用哪种方式做」。**

## 为什么单独一层
`feedback_matrix.sentiment_score` 是「温度」——0~10 一个数，只判买不买、几成仓；
但它**判不出该买哪种票**。同一分位下：
  - 指数主升（三指数皆红 + 放量）→ **只做最强**（最高板／板块龙一／容量核心），做跟风是浪费最强一段
  - 指数分歧（不同向／下跌）→ **只做逆势三类**（抗跌／托举／反攻），做高位补涨是往刀口上撞
两者在旧链路里是同一套分数排序 → 这是本次吸收的唯一结构性缺口。

## 两档判据（全部取现成字段·⛔ 不新增落盘链路）
| 档 | 判据 |
|---|---|
| 主升 attack | 三指数皆红 ∧ `idx_avg ≥ +0.8%` ∧ 量能 ≥ 25000 亿 ∧ 情绪分 ≥ 7 |
| 分歧 shift  | `idx_avg ≤ −0.5%` ∨ 三指数不同向（有红有绿） |
| 中性 neutral | 其余（含三指数同向小涨小跌）→ 沿用 selective，⛔ 不硬贴标签 |

## 标注口径（annotate_candidates）
- 主升：**最强** = ①全市场最高板（≥2 板）②板块龙一且板块 ≥3 家涨停 ③容量核心且当天涨幅 ≥5%；其余标「强市跟风」
- 分歧：**逆势领涨** = 板块龙一 ∧ 板块 ≥3 家 ∧ 同板块首封最早（`reason.lead.themeFirst == fbt`）；
  **高位封单薄** = ≥4 板 ∧ `封单/流通 < 2%`（与 `defense_gate` 派发特征同口径）
- ⛔ 抗跌 ≠ 抄跌得最多的票：候选是今日涨停池，全是红的，所以本层**不对候选判"抗跌"**——
  抗跌只在盘中（`latest.quotes` 未封票）有意义，属巡检/猎手层，不在此处硬凑。
- ⛔ 托举需**个股级**主力净流入，现有 `sector_risk` 只有板块级 → **本层不判托举**（缺数据不编造）。

## 边界
1. 纯函数，只读入参，⛔ 不写盘、不产买卖结论（买不买由 `decision_outlook` 判）。
2. 不改 13 因子情绪分——指数已是因子之一，再塞会让分数语义混乱。
3. 开关 `ENABLE_MARKET_STYLE=0` → 全部返回中性/不标注。
"""
from __future__ import annotations

import os
from typing import Any

# ── 可逆开关 ──────────────────────────────────────────────
ENABLE_MARKET_STYLE = os.environ.get("ENABLE_MARKET_STYLE", "1") != "0"

# ── 形态阈值（改这里，勿改结构）─────────────────────────────
IDX_AVG_ATTACK = 0.8        # 主升：三指数平均涨幅下限(%)
AMOUNT_ATTACK_YI = 25000.0   # 主升：两市成交额下限(亿) —— 宪法放量口径 2.5 万亿
SENT_ATTACK = 7.0            # 主升：情绪分下限（偏强/亢奋）
IDX_AVG_SHIFT = -0.5         # 分歧：指数平均涨幅上限(%)
ICE_SENT = 2.5               # 冰点：情绪分低于此 → 提示空仓优先（⛔ 不覆盖形态）

# ── 标注阈值 ──────────────────────────────────────────────
HIGH_BOARDS = 4              # 高位判定：≥4 板
SEAL_RATIO_THIN = 2.0        # 封单/流通 <2% = 派发特征（与 defense_gate 同口径）
DRAGON_MATES_MIN = 3         # 板块 ≥3 家涨停才算"有板块效应"（含自身）
CAP_PCT_MIN = 5.0            # 容量核心须当天在动（≥5%）

STYLE_ATTACK = "attack"
STYLE_SHIFT = "shift"
STYLE_NEUTRAL = "neutral"

STYLE_LABEL = {
    STYLE_ATTACK: "主升",
    STYLE_SHIFT: "分歧",
    STYLE_NEUTRAL: "没方向",
}

TRADING_RULE = {
    # ⛔ 卡面人话铁律（宪法 2202 术语换算表）：容量核心→板块大票、冰点档→市场很冷，禁上卡。
    STYLE_ATTACK: "只做最强：最高板／板块龙一／成交额最大的板块大票",
    STYLE_SHIFT: "只做逆势：抗跌、资金托举、率先反攻的票",
    STYLE_NEUTRAL: "挑着做：不硬贴风格，按原分数排序",
}


def _f(v: Any) -> float | None:
    """安全转 float（None/空/异常 → None，⛔ 不返 0 冒充有数据）。"""
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def index_snapshot(index_rows: Any) -> list[dict[str, Any]]:
    """`latest.json.index` → [{name,pct}]，剔除无 pct 项。"""
    out: list[dict[str, Any]] = []
    for row in (index_rows or []):
        if not isinstance(row, dict):
            continue
        pct = _f(row.get("pct"))
        if pct is None:
            continue
        out.append({"name": str(row.get("name") or ""), "pct": pct})
    return out


def detect_style(index_rows: Any, feedback: dict[str, Any] | None = None) -> dict[str, Any]:
    """指数形态判定（纯函数）。缺数据 → neutral + data_gap，⛔ 不猜。

    返回：
      {style, label, trading_rule, rule(判据一句话), evidence{...}, ice(bool), data_gap[..]}
    """
    fb = feedback or {}
    snap = index_snapshot(index_rows)
    sent = _f(fb.get("sentiment_score"))
    if sent is None:
        sent = _f(fb.get("sentiment"))

    data_gap: list[str] = []
    if not snap:
        data_gap.append("index")

    # 指数平均涨幅：优先自算（三指数等权），缺则退回 feedback.idx_avg
    idx_avg: float | None = None
    if snap:
        idx_avg = round(sum(r["pct"] for r in snap) / len(snap), 2)
    else:
        idx_avg = _f(fb.get("idx_avg"))

    amount = _f(fb.get("market_amount_yi"))
    if amount is None or amount <= 0:
        amount = _f(fb.get("market"))
        if amount is None:
            data_gap.append("amount")

    has_up = any(r["pct"] > 0 for r in snap)
    has_down = any(r["pct"] < 0 for r in snap)
    mixed = has_up and has_down

    style = STYLE_NEUTRAL
    rule = "指数没有明确方向"
    if not ENABLE_MARKET_STYLE:
        rule = "风格层已关闭（ENABLE_MARKET_STYLE=0）"
    elif snap and idx_avg is not None:
        all_up = has_up and not has_down
        if (all_up and idx_avg >= IDX_AVG_ATTACK
                and (amount or 0.0) >= AMOUNT_ATTACK_YI
                and (sent or 0.0) >= SENT_ATTACK):
            style = STYLE_ATTACK
            rule = "三指数皆红 · 放量 · 情绪偏强"
        elif mixed:
            style = STYLE_SHIFT
            rule = "三指数不同向（有红有绿）"
        elif idx_avg <= IDX_AVG_SHIFT:
            style = STYLE_SHIFT
            rule = "三指数同向下跌"

    return {
        "style": style,
        "label": STYLE_LABEL[style],
        "trading_rule": TRADING_RULE[style],
        "rule": rule,
        "enabled": ENABLE_MARKET_STYLE,
        "ice": bool(sent is not None and sent < ICE_SENT),
        # ⛔ 语义矛盾门禁（宪法【核心判断原则】③）：冰点档下"只做最强/只做逆势"与
        # "空仓待命"（decision_outlook wait_ice）不得并存 → 显式降级为"标注只看不买"。
        "ice_note": (f"市场很冷（情绪 {sent:.1f}/10）→ 空仓优先，下面的标注只看不买"
                     if (sent is not None and sent < ICE_SENT) else None),
        "evidence": {
            "index": snap,
            "idx_avg": idx_avg,
            "amount_yi": amount,
            "sentiment": sent,
            "mixed": mixed,
        },
        "data_gap": data_gap,
    }


def seal_ratio_pct(row: dict[str, Any]) -> float | None:
    """封单/流通市值(%) —— 封单归一化的唯一口径。

    ⛔ 绝对"X 亿"没有跨标的可比性（小盘 5000 万可能比大盘 5 亿更厚）。
    """
    seal = _f(row.get("seal_amt"))
    lt = _f(row.get("lt_mktcap"))
    if seal is None or lt is None or lt <= 0:
        return None
    return round(seal / lt * 100.0, 2)


def _first_in_sector(row: dict[str, Any]) -> bool:
    """同板块首封（`reason.lead.themeFirst` 与自身 fbt 一致）。"""
    fbt = str(row.get("fbt") or "")
    reason = row.get("reason") or {}
    lead = reason.get("lead") or {}
    first = str(lead.get("themeFirst") or "")
    return bool(fbt) and bool(first) and fbt == first


def _boards(row: dict[str, Any]) -> int:
    try:
        return int(row.get("boards") or row.get("lbc") or 1)
    except (TypeError, ValueError):
        return 1


def annotate_candidates(
    rows: list[dict[str, Any]],
    style_info: dict[str, Any],
    *,
    max_boards: int | None = None,
) -> dict[str, Any]:
    """给候选打 `market_style` / `style_bucket` / `style_flag` / `seal_ratio_pct`。

    ⛔ **只加标记**：不排序、不改分、不塞池、不改入池资格。
    返回统计（写 diagnostics 用）。
    """
    style = str((style_info or {}).get("style") or STYLE_NEUTRAL)
    if not ENABLE_MARKET_STYLE:
        style = STYLE_NEUTRAL

    if max_boards is None:
        max_boards = max([_boards(r) for r in rows] or [1])

    counts = {"core": 0, "follow": 0, "hedge": 0, "caution": 0, "none": 0}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ratio = seal_ratio_pct(row)
        if ratio is not None:
            row["seal_ratio_pct"] = ratio

        bucket: str | None = None
        flag: str | None = None
        boards = _boards(row)
        mates = int(row.get("sector_mates_cnt") or 0)
        is_dragon = bool(row.get("is_sector_dragon"))
        pct = _f(row.get("pct")) or 0.0

        if style == STYLE_ATTACK:
            is_top = max_boards >= 2 and boards >= max_boards
            is_dragon_ok = is_dragon and mates >= DRAGON_MATES_MIN
            is_cap = bool(row.get("is_capacity_core")) and pct >= CAP_PCT_MIN
            if is_top or is_dragon_ok or is_cap:
                bucket, flag = "core", "🎯最强"
            else:
                bucket, flag = "follow", "⚠️强市跟风"
        elif style == STYLE_SHIFT:
            thin_high = (boards >= HIGH_BOARDS and ratio is not None
                         and ratio < SEAL_RATIO_THIN)
            if is_dragon and mates >= DRAGON_MATES_MIN and _first_in_sector(row) and not thin_high:
                bucket, flag = "hedge", "🛡逆势领涨"
            elif thin_high:
                bucket, flag = "caution", "⚠️高位封单薄"

        row["market_style"] = style
        row["style_bucket"] = bucket
        row["style_flag"] = flag
        counts[bucket or "none"] += 1

    return {
        "available": bool(ENABLE_MARKET_STYLE),
        "style": style,
        "label": (style_info or {}).get("label"),
        "max_boards": max_boards,
        "counts": counts,
    }


def no_trend_hint(feedback: dict[str, Any] | None) -> str | None:
    """看不出趋势 → 提示降频（宪法 🌊 章第 5 条）。

    判据：近 3 期晋级率极差 ≤2pp 且近 3 期溢价极差 ≤1.5pp → 无方向。
    ⛔ 走弱趋势（`promote_downtrend` / `avg_pct_weak`）不算"没方向"，返回 None 交给趋势防守铁律。
    """
    fb = feedback or {}
    trend = fb.get("trend") or {}
    if trend.get("promote_downtrend") or trend.get("avg_pct_weak"):
        return None
    hist = [h for h in (trend.get("history") or []) if isinstance(h, dict)]
    if len(hist) < 3:
        return None
    tail = hist[-3:]
    prs = [_f(h.get("pr")) for h in tail]
    aps = [_f(h.get("ap")) for h in tail]
    if any(v is None for v in prs) or any(v is None for v in aps):
        return None
    if (max(prs) - min(prs)) <= 2.0 and (max(aps) - min(aps)) <= 1.5:
        return "看不出趋势（晋级率/溢价这几天没方向）→ 少出手，别换来换去"
    return None
