#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前排核心评分 · 92科比选票口径（2026-10-21 用户贴图定调）。

## 来源
用户原文（图片·92科比）：
- 「选票，往前排核心选。涨停、热度、换手、辨识度、筹码结构、股性这些」
- 「一定要带板块回流、带板块起爆」
- 「连板只能按照 龙头战法＋连板情绪＋核心＋放量＋加速 等层面一起看」
- 「首板第二天一进二一定要扫描一下做初步筛选，二板就已经大概能选出来了」
- 「看高做低，做高看低」

用户补充（同轮）：「后排接力严格要谨慎！很有可能没有溢价！」

## 落点
1. `score(it)` → 0~100 的「前排核心分」+ 六个子分。**纯函数、只读候选字段**，
   缺失字段一律回中性值（50），⛔ 不猜、不编造。
2. `row_tier(...)` → front / second / back 三档身位，供 `core/pool_call` 后排接力闸用。

## 与既有因子的边界（⛔ 不重复计算）
- 高度/带动/容量/历史晋级 → `core/quant_score` 已在算，本模块**不重算**。
- 本模块补的正是系统原先**只在卡面展示、没进选股**的三块：
  **筹码结构**（chip_profit_rate / chip_conc90）、**主力资金回流**（main_flow_*）、
  **封板加速**（fbt 早 + 炸板少 + 封单厚）。
- 开关 `FRONT_CORE_ENABLED`：关掉 → `score()` 返回中性 50、bonus=0，行为回到旧版。
"""
from __future__ import annotations

import os
from typing import Any

ENABLED_ENV = "FRONT_CORE_ENABLED"
BONUS_ENV = "FRONT_CORE_BONUS_MAX"

# raw_score 加分上限（2026-10-21）：⛔ 只影响排序/展示，不改四档入池资格。
# 定 6.0 的理由：历史晋级因子封顶 ±7、高度突破 +8，前排核心属于「同等高度里谁更靠前」，
# 分量必须小于「高度」这一档，否则会把低位高分票顶到空间龙前面（＝回到旧毛病）。
FRONT_CORE_BONUS_MAX = 6.0
NEUTRAL = 50.0


def _enabled() -> bool:
    return str(os.environ.get(ENABLED_ENV, "1")).strip().lower() not in ("0", "false", "no", "off")


def _num(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _fbt_min(fbt: Any) -> int | None:
    """封板时间 HHMMSS → 当日分钟数；非法/缺失 → None（不猜）。"""
    s = str(fbt or "").strip()
    if len(s) < 5 or not s.isdigit():
        return None
    try:
        h, m = int(s[:2]), int(s[2:4])
    except ValueError:
        return None
    if not (9 <= h <= 15 and 0 <= m < 60):
        return None
    return h * 60 + m


def _linear(v: float, lo: float, hi: float, *, invert: bool = False) -> float:
    """把 v 映射到 0~100（区间外截断）；invert=True 表示 v 越小越好。"""
    if hi == lo:
        return NEUTRAL
    r = (v - lo) / (hi - lo)
    r = max(0.0, min(1.0, r))
    return (1.0 - r) * 100.0 if invert else r * 100.0


def _first_in_line(it: dict[str, Any]) -> bool | None:
    """同线首封（reason.lead.themeFirst 与自身 fbt 一致）；缺字段 → None。"""
    fbt = str(it.get("fbt") or "").strip()
    lead = ((it.get("reason") or {}).get("lead") or {}) if isinstance(it.get("reason"), dict) else {}
    first = str(lead.get("themeFirst") or "").strip()
    if not fbt or not first:
        return None
    return fbt == first


# ── 六个子分（各 0~100，缺失 → NEUTRAL）────────────────────────────────────

def sub_front(it: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """① 前排身位（92科比「往前排核心选」）——同线谁最靠前。"""
    facts: dict[str, Any] = {}
    score = 50.0
    dragon = bool(it.get("is_sector_dragon"))
    role = str(it.get("leader_role") or "")
    first = _first_in_line(it)
    mates_raw = _num(it.get("theme_mates_cnt"))
    if mates_raw is None:
        mates_raw = _num(it.get("sector_mates_cnt"))
    # ⛔ 缺失 ≠ 0 家：字段没有就**不给方向性判断**（中性），绝不因为「没数据」判成孤票。
    mates = mates_raw
    carry_raw = _num(it.get("carry_cnt"))
    carry = carry_raw
    facts.update({"is_sector_dragon": dragon, "leader_role": role or None,
                  "first_in_line": first, "mates": mates, "carry_cnt": carry})
    if dragon:
        score += 22.0
    if role in ("龙头", "准龙头"):
        score += 10.0
    elif role == "跟风":
        score -= 18.0
    if first is True:
        score += 12.0
    elif first is False:
        score -= 8.0
    # 带动性：封板后带起几家（≥5 家＝真带队）
    if carry is not None:
        if carry >= 5:
            score += 12.0
        elif carry >= 3:
            score += 6.0
        elif carry <= 0:
            score -= 6.0
    # 题材成片（起爆的宽度）——孤票谈不上「前排核心」
    if mates is not None:
        if mates >= 5:
            score += 8.0
        elif mates <= 1:
            score -= 12.0
    return max(0.0, min(100.0, score)), facts


def sub_seal(it: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """② 封板质量与加速（「放量＋加速」的加速腿＝封得早、封得稳、封单厚）。"""
    facts: dict[str, Any] = {}
    parts: list[float] = []
    fm = _fbt_min(it.get("fbt"))
    if fm is not None:
        # 09:30=570 → 100；11:30=690 → 20；越早越高（92科比：加速＝抢在前面）
        parts.append(_linear(fm, 570.0, 690.0, invert=True) * 0.9 + 10.0)
        facts["fbt"] = it.get("fbt")
    zbc = _num(it.get("zbc"))
    if zbc is not None:
        parts.append(_linear(zbc, 0.0, 5.0, invert=True))
        facts["zbc"] = zbc
    ratio = _num(it.get("seal_ratio_pct"))
    if ratio is not None:
        parts.append(_linear(ratio, 0.2, 3.0))     # 封单/流通市值 3% 以上算厚
        facts["seal_ratio_pct"] = ratio
    if not parts:
        return NEUTRAL, facts
    return sum(parts) / len(parts), facts


def sub_turnover(it: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """③ 换手与热度（换手健康区 3%~25%，过低没换手、过高是派发）。"""
    facts: dict[str, Any] = {}
    parts: list[float] = []
    hs = _num(it.get("hs"))
    if hs is not None:
        if hs < 3:
            s = _linear(hs, 0.0, 3.0) * 0.6           # 换手过低：给分但不满分
        elif hs <= 25:
            s = 100.0
        else:
            s = _linear(hs, 25.0, 45.0, invert=True)
        parts.append(s)
        facts["hs"] = round(hs, 2)
    cje = _num(it.get("cje_yi"))
    if cje is not None:
        parts.append(_linear(cje, 2.0, 25.0))          # 成交额 25 亿以上封顶
        facts["cje_yi"] = round(cje, 2)
    if not parts:
        return NEUTRAL, facts
    return sum(parts) / len(parts), facts


def sub_chip(it: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """④ 筹码结构（⛔ 系统原先只在卡面显示、没进选股——本轮补上）。

    获利盘：低位获利盘（<70%）上方没有套牢兑现盘，最好；≥95% 全员盈利＝一有风吹草动就砸。
    集中度（90%）：数值越小筹码越集中，越容易封得动。
    """
    facts: dict[str, Any] = {}
    parts: list[float] = []
    prof = _num(it.get("chip_profit_rate"))
    if prof is not None:
        if prof <= 70:
            s = 100.0
        elif prof <= 90:
            s = _linear(prof, 70.0, 90.0, invert=True) * 0.5 + 40.0
        else:
            s = _linear(prof, 90.0, 100.0, invert=True) * 0.4   # ≥90 高分位＝抛压最重
        parts.append(s)
        facts["chip_profit_rate"] = round(prof, 2)
    conc = _num(it.get("chip_conc90"))
    if conc is not None:
        parts.append(_linear(conc, 8.0, 30.0, invert=True))     # 集中度越低越好
        facts["chip_conc90"] = round(conc, 2)
    if not parts:
        return NEUTRAL, facts
    return sum(parts) / len(parts), facts


def sub_capital(it: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """⑤ 板块回流（92科比「一定要带板块回流、带板块起爆」——个股层资金口径）。

    ⛔ 只用真实成交口径（主力净流入），板块整体回流由 `core/sector_support` 与
    `defense_gate.sector_drain_verdict` 另算，本函数不重复判定。
    """
    facts: dict[str, Any] = {}
    parts: list[float] = []
    mf = _num(it.get("main_flow_yi"))
    if mf is not None:
        parts.append(100.0 if mf > 0 else _linear(mf, -3.0, 0.0) * 0.5)
        facts["main_flow_yi"] = round(mf, 2)
    mf5 = _num(it.get("main_flow_5d_yi"))
    if mf5 is not None:
        parts.append(100.0 if mf5 > 0 else _linear(mf5, -5.0, 0.0) * 0.5)
        facts["main_flow_5d_yi"] = round(mf5, 2)
    if not parts:
        return NEUTRAL, facts
    return sum(parts) / len(parts), facts


def sub_gene(it: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """⑥ 股性（历史同路径晋级概率）。⛔ 与 quant_score 的 history_relay_factor 同源不重算，
    这里只做 0~100 归一，供展示与合成。"""
    facts: dict[str, Any] = {}
    prob = _num(it.get("history_relay_prob"))
    n = _num(it.get("history_relay_samples")) or 0.0
    if prob is None:
        return NEUTRAL, facts
    facts.update({"history_relay_prob": prob, "samples": int(n)})
    # 样本 <5 打折（小样本不可尽信）
    base = _linear(prob, 0.0, 60.0)
    if n < 5:
        base = NEUTRAL + (base - NEUTRAL) * 0.5
    return base, facts


_WEIGHTS = (("front", 0.30), ("seal", 0.20), ("capital", 0.18),
            ("chip", 0.12), ("turnover", 0.12), ("gene", 0.08))


def score(it: dict[str, Any]) -> dict[str, Any]:
    """前排核心分（0~100）+ 子分 + 事实留痕。字段缺失全走中性，绝不编造。"""
    if not _enabled():
        return {"enabled": False, "score": NEUTRAL, "bonus": 0.0, "subs": {}, "facts": {}}
    subs: dict[str, float] = {}
    facts: dict[str, Any] = {}
    for name, fn in (("front", sub_front), ("seal", sub_seal), ("capital", sub_capital),
                     ("chip", sub_chip), ("turnover", sub_turnover), ("gene", sub_gene)):
        s, f = fn(it)
        subs[name] = round(s, 1)
        facts.update(f)
    total = sum(subs[k] * w for k, w in _WEIGHTS)
    bonus = (total - NEUTRAL) / NEUTRAL * _bonus_max()
    return {"enabled": True, "score": round(total, 1),
            "bonus": round(max(-_bonus_max(), min(_bonus_max(), bonus)), 2),
            "subs": subs, "facts": facts}


def _bonus_max() -> float:
    try:
        return max(0.0, float(os.environ.get(BONUS_ENV, FRONT_CORE_BONUS_MAX)))
    except (TypeError, ValueError):
        return FRONT_CORE_BONUS_MAX


# ── 身位判定（后排接力闸用）────────────────────────────────────────────────

TIER_FRONT = "front"
TIER_SECOND = "second"
TIER_BACK = "back"

TIER_LABEL = {TIER_FRONT: "前排核心", TIER_SECOND: "次排", TIER_BACK: "后排跟风"}


def row_tier(it: dict[str, Any], *, line_is_core: bool | None = None,
             line_max_boards: int | None = None,
             boards: int | None = None,
             core_boards: int | None = None,
             line_rank: int | None = None) -> str:
    """同线身位：front（本条线最靠前那批）/ second（差 1 板）/ back（再往后）。

    ⛔ **不按池内名次切档**（`line_rank` 只在没有任何板数信息时兜底）：同板数的两只票，
    名次先后只是排序副产物，把其中一只算「前排」另一只算「后排」是假的——真正的身位是
    **板数相对这条线最高板的差**。

    参照系取「池内本线最高板」与「池外核心板数」的较大者：
    池外可能坐着这条线真正的核心（如 10-21 传媒线 新华传媒 11 板·已断板），
    只看池内会把「池内第一」误当「这条线第一」。

    判据（宁严勿松——用户：「后排接力严格要谨慎，很有可能没有溢价」）：
      ① 自己就是这条线的核心 → front；
      ② 板数 = 本线最高 → front；差 1 板 → second；差 ≥2 板 → back；
      ③ 无板数信息 → 看 `is_sector_dragon` / `leader_role`，最后中性判 second
         （⛔ 不因缺数据就放行成前排）。
    """
    if line_is_core:
        return TIER_FRONT
    b = boards if isinstance(boards, int) and boards > 0 else None
    refs = [x for x in (line_max_boards, core_boards) if isinstance(x, int) and x > 0]
    top = max(refs) if refs else None
    if b is not None and top is not None:
        if b >= top:
            return TIER_FRONT
        return TIER_SECOND if (top - b) == 1 else TIER_BACK
    if isinstance(line_rank, int) and line_rank > 0:
        if line_rank == 1:
            return TIER_FRONT
        return TIER_SECOND if line_rank == 2 else TIER_BACK
    if bool(it.get("is_sector_dragon")):
        return TIER_FRONT
    role = str(it.get("leader_role") or "")
    if role == "龙头":
        return TIER_FRONT
    if role == "准龙头":
        return TIER_SECOND
    if role == "跟风":
        return TIER_BACK
    return TIER_SECOND


def premium_note(tier: str, *, line_core_name: str | None = None,
                 boards_gap: int | None = None) -> str:
    """卡面白话：为什么这只后排很可能没溢价（⛔ 禁黑话，段内只说一次）。"""
    core = f"{line_core_name}" if line_core_name else "这条线的核心"
    if tier == TIER_BACK:
        extra = f"，比{core}低{boards_gap}个板" if boards_gap and boards_gap > 0 else ""
        return f"排在{core}后面{extra} → 后排跟风，涨的时候它最后占钱、跌的时候它先砸，溢价靠不住"
    if tier == TIER_SECOND:
        return f"是{core}后面的第二只 → 得等核心买不进去才有它的份，溢价打折"
    return ""
