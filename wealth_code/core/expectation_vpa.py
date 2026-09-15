#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量价驱动的竞价预期引擎（Volume-Price Auction expectation）

替代 expectation_ai.build_ai_expectation 的「板数→预期」刚性映射，
改为「晋级路径 → 竞价预期」弹性框架。

核心理念（2026-08-12 重构）：
- 不看板数看路径：6 板爆量分歧 vs 6 板缩量加速 → 路径不同，预期不同
- 路径 = 换手状态(锁仓/换手/爆量) × 封板速度(一字/秒板/午后) × 炸板情况
- 5 条路径 → 情绪相位 × 龙头身份 → 竞价预期区间 + 量能需求

数据依赖（全部已在 candidates-*.json 中）：
- hs(换手率)、fbt(封板时间)、zbc(炸板次数)、oneWord(一字板)、cje_yi(成交额)
- dragon_diagnosis.identity(龙头身份)
- environment.sentiment_label(情绪周期)
"""

from __future__ import annotations

from typing import Any

from core.expectation_ai import sentiment_phase, dragon_three_diagnosis


# ── Step 1: 换手状态判定 ──────────────────────────────────────────

def _turnover_tier(hs: float) -> str:
    """换手率 → 状态标签。"""
    if hs <= 0:
        return "未知"
    if hs < 3:
        return "极低"       # 一字/准一字，锁仓
    if hs < 8:
        return "低"         # 缩量换手
    if hs < 15:
        return "中"         # 正常换手
    if hs < 25:
        return "高"         # 分歧换手
    return "极高"           # 爆量分歧


def _seal_speed(fbt: str | None, one_word: bool) -> str:
    """封板时间 → 速度标签。"""
    if one_word:
        return "一字"
    if not fbt or len(str(fbt)) < 4:
        return "未知"
    t = str(fbt)[:4]
    if t <= "0930":
        return "秒板"
    if t <= "1000":
        return "早盘"
    if t <= "1130":
        return "上午"
    return "午后"


def _broken_tier(zbc: int) -> str:
    """炸板次数 → 标签。"""
    if zbc <= 0:
        return "无"
    if zbc == 1:
        return "1次"
    return "多次"


# ── Step 2: 晋级路径分类 ──────────────────────────────────────────

def classify_promotion_path(cand: dict[str, Any]) -> dict[str, Any]:
    """
    换手 + 封板速度 + 炸板 → 晋级路径 + 路径标签。

    返回 {
        "path": "缩量加速" | "温和放量" | "放量攻击" | "分歧换手" | "弱势回封",
        "turnover_tier": "极低"|"低"|"中"|"高"|"极高",
        "seal_speed": "一字"|"秒板"|"早盘"|"上午"|"午后",
        "broken_tier": "无"|"1次"|"多次",
    }
    """
    hs = float(cand.get("hs") or 0)
    fbt = str(cand.get("fbt") or "") if cand.get("fbt") else None
    zbc = int(cand.get("zbc") or 0)
    one_word = bool(((cand.get("reason") or {}).get("ignition") or {}).get("oneWord"))

    t_tier = _turnover_tier(hs)
    s_speed = _seal_speed(fbt, one_word)
    b_tier = _broken_tier(zbc)

    # ── 路径判定树 ──
    boards = int(cand.get("boards") or 1)
    path = "温和放量"  # 默认

    # 弱势回封：炸板 ≥1 且 午后封板
    if zbc >= 1 and s_speed in ("午后",):
        path = "弱势回封"

    # 分歧换手：换手高/极高 且(炸板≥2 或 午后封板)
    elif t_tier in ("高", "极高") and (zbc >= 2 or s_speed == "午后"):
        path = "分歧换手"

    # 放量攻击：换手高/极高 + 封板早(一字/秒板/早盘) + 炸板≤1
    elif t_tier in ("高", "极高") and s_speed in ("一字", "秒板", "早盘", "上午") and zbc <= 1:
        path = "放量攻击"

    # 分歧换手（备选）：换手高 + 炸板≥2（不管封板速度）
    elif t_tier in ("高", "极高") and zbc >= 2:
        path = "分歧换手"

    # ⚠️ 1板修正：首板不存在"加速"概念，归入温和放量
    #    1板缩量秒封 ≠ 加速，只是主力首封→次日需放量确认
    elif boards <= 1 and s_speed not in ("午后",) and zbc <= 1:
        path = "温和放量"

    # 缩量加速：≥2板 + 换手低/极低 + 封板一字/秒板 + 无炸板
    elif t_tier in ("极低", "低") and s_speed in ("一字", "秒板") and zbc == 0:
        path = "缩量加速"

    # 缩量加速（备选）：≥2板 + 换手极低/低 + 早盘封板 + 无炸板
    elif t_tier in ("极低", "低") and s_speed in ("一字", "秒板", "早盘") and zbc == 0:
        path = "缩量加速"

    # 温和放量：换手中/低 + 上午前封板
    elif t_tier in ("低", "中") and s_speed not in ("午后",) and zbc <= 1:
        path = "温和放量"

    # 放量攻击（备选）：换手中 + 早盘封板
    elif t_tier == "中" and s_speed in ("一字", "秒板", "早盘") and zbc == 0:
        path = "放量攻击"

    return {
        "path": path,
        "turnover_tier": t_tier,
        "seal_speed": s_speed,
        "broken_tier": b_tier,
    }


# ── Step 3: 竞价涨幅预期映射 ────────────────────────────────────────

# 路径 × 情绪 → 基础竞价预期区间 (lo, hi)
# ⚠️ 2026-08-12 保守化升级（用户反馈"预期偏高打不到"）：
#   ① 区间整体下移 1~1.5 点；② 上沿硬封顶 5.0%（signal_eval CAUTION_GAP_PCT=5.0，
#      竞价>5% 一律转 pending 不直接买 → 预期窗口必须落在可执行区，否则"预期8%但5%买不了"自相矛盾）
_PATH_PHASE_RANGE: dict[str, dict[str, tuple[float, float]]] = {
    "缩量加速": {
        "亢奋": (3.5, 5.0),
        "修复": (2.5, 4.5),
        "分歧": (1.5, 4.0),
        "冰点": (0.5, 3.0),
    },
    "温和放量": {
        "亢奋": (2.5, 4.5),
        "修复": (1.5, 3.5),
        "分歧": (0.5, 3.0),
        "冰点": (-0.5, 2.0),
    },
    "放量攻击": {
        "亢奋": (3.5, 5.0),
        "修复": (2.5, 4.5),
        "分歧": (1.5, 4.0),
        "冰点": (0.5, 3.0),
    },
    "分歧换手": {
        "亢奋": (0.0, 3.0),
        "修复": (-1.0, 2.0),
        "分歧": (-2.0, 1.5),
        "冰点": (-3.0, 0.5),
    },
    "弱势回封": {
        "亢奋": (-2.0, 1.0),
        "修复": (-3.0, 0.5),
        "分歧": (-4.0, 0.0),
        "冰点": (-5.0, -1.0),
    },
}

# 龙头身份调整（在基础区间上加）——2026-08-12 收窄，避免龙头溢价把 hi 顶过执行线
_DRAGON_ADJUST: dict[str, tuple[float, float]] = {
    "真龙头(主升)": (0.0, 0.5),
    "准龙头": (0.0, 0.3),
    "跟风/补涨": (-1.0, -1.0),
}

# 昨日一字板修正（2026-08-12 加）：一字/缩量加速票次日常态一字或接近一字开盘，
# 竞价>5% 又转 pending → 报 4.5~8% 的预期等于画饼。修正后区间语义 =
# 「若开板回踩到该区间才存在合理买点」；lo-1.5 / hi-2.0
_ONE_WORD_ADJUST: tuple[float, float] = (-1.5, -2.0)

# 预期上沿硬封顶（= signal_eval 的 CAUTION_GAP_PCT，超过则 pending 不可执行）
_EXPECT_HI_CAP: float = 5.0

# 超预期线 = hi + offset
_SUPER_GAP_OFFSET: dict[str, float] = {
    "亢奋": 1.0,
    "修复": 1.0,
    "分歧": 2.0,
    "冰点": 2.0,
}


# ── Step 4: 竞价量能预期 ──────────────────────────────────────────

# 路径 → (竞价量占昨量比例区间, 需量下限亿)
_PATH_AMOUNT: dict[str, tuple[tuple[float, float], float]] = {
    "缩量加速": ((0.03, 0.06), 0.10),
    "温和放量": ((0.04, 0.08), 0.12),
    "放量攻击": ((0.06, 0.10), 0.15),
    "分歧换手": ((0.08, 0.15), 0.15),
    "弱势回封": ((0.05, 0.10), 0.12),
}

_PHASE_AMOUNT_DELTA: dict[str, float] = {
    "亢奋": 0.01,
    "修复": 0.005,
    "分歧": -0.005,
    "冰点": -0.01,
}


def build_vpa_expectation(
    cand: dict[str, Any],
    env: dict[str, Any],
    theme_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    量价驱动的竞价预期主入口。

    步骤：
    1. 判定情绪相位 + 龙头三要素
    2. 归类晋级路径（换手×封板×炸板）
    3. 路径 × 情绪 → 竞价涨幅预期区间
    4. 龙头身份微调
    5. 路径 → 竞价量能预期

    返回：与 signal_eval 兼容的 expectation dict。
    """
    phase, phase_note = sentiment_phase(env)
    dragon = dragon_three_diagnosis(cand)
    path_info = classify_promotion_path(cand)
    path = path_info["path"]
    ident = dragon["identity"]

    # ── 竞价涨幅预期 ──
    phase_ranges = _PATH_PHASE_RANGE.get(path, _PATH_PHASE_RANGE["温和放量"])
    lo, hi = phase_ranges.get(phase, phase_ranges["分歧"])

    # 龙头身份调整
    d_lo, d_hi = _DRAGON_ADJUST.get(ident, (0.0, 0.0))
    lo += d_lo
    hi += d_hi

    # 真龙头在冰点/分歧期不推高 lo（避免犯旧版错误）
    if ident.startswith("真龙头") and phase in ("冰点", "分歧"):
        pass  # 保持基础区间，不加溢价

    # 昨日一字板修正（2026-08-12 加）：次日常态一字/近一字开盘，预期区间改为「回踩可买窗口」
    one_word = bool(((cand.get("reason") or {}).get("ignition") or {}).get("oneWord"))
    if one_word and path in ("缩量加速", "温和放量", "放量攻击"):
        adj_lo, adj_hi = _ONE_WORD_ADJUST
        lo += adj_lo
        hi += adj_hi

    # 题材动量加成
    theme_phase_tag = "—"
    theme_conf = 0.0
    if theme_ctx:
        theme = str(cand.get("theme") or "其他")
        tc = theme_ctx.get(theme) or {}
        theme_phase_tag = tc.get("phase") or "—"
        theme_conf = float(tc.get("confidence") or 0.0)
        if theme_phase_tag in ("boom", "re-entry-candidate"):
            hi += 0.5

    lo = round(max(-5.0, lo), 1)
    hi = round(max(lo + 0.5, hi), 1)
    # 上沿硬封顶（2026-08-12 加）：>5% 竞价转 pending 不可执行，预期窗口必须真实可买到
    if hi > _EXPECT_HI_CAP:
        hi = _EXPECT_HI_CAP
        lo = min(lo, round(hi - 0.5, 1))

    # 超预期线 = hi + 相位偏移
    super_offset = _SUPER_GAP_OFFSET.get(phase, 2.0)
    super_gap_min = round(hi + super_offset, 1)

    # ── 竞价量能预期 ──
    amt_info = _PATH_AMOUNT.get(path, _PATH_AMOUNT["温和放量"])
    (ratio_lo, ratio_hi), amt_floor = amt_info
    delta = _PHASE_AMOUNT_DELTA.get(phase, 0.0)

    # 真龙头 +0.005，跟风 -0.005，20cm -0.01
    if ident.startswith("真龙头"):
        delta += 0.005
    elif ident == "跟风/补涨":
        delta -= 0.005
    if bool(cand.get("is_20cm")):
        delta -= 0.01

    r_lo = round(max(0.02, min(0.15, ratio_lo + delta)), 3)
    r_hi = round(max(0.04, min(0.18, ratio_hi + delta)), 3)
    if r_hi < r_lo + 0.02:
        r_hi = r_lo + 0.02

    cje_yi = float(cand.get("cje_yi") or 0.0)
    amount_min = round(max(amt_floor, cje_yi * r_lo), 2)
    amount_super = round(max(amt_floor + 0.05, cje_yi * r_hi), 2)

    # ── 置信度 ──
    phase_score = {"亢奋": 0.9, "修复": 0.7, "分歧": 0.45, "冰点": 0.2}.get(phase, 0.5)
    dragon_score = dragon["composite"] / 5.0
    confidence = round(phase_score * 0.35 + dragon_score * 0.35 + theme_conf * 0.15 + 0.15, 2)

    # ── 文案 ──
    path_labels = {
        "缩量加速": "缩量加速·锁仓一致",
        "温和放量": "温和放量·健康换手",
        "放量攻击": "放量攻击·增量博弈",
        "分歧换手": "分歧换手·爆量消化",
        "弱势回封": "弱势回封·勉强封板",
    }

    if phase == "冰点":
        phase_hint = "情绪冰点：只低吸不追高"
    elif phase == "分歧":
        phase_hint = "情绪分歧：高位淘汰加剧"
    elif phase == "亢奋":
        phase_hint = "情绪亢奋：龙头加速"
    else:
        phase_hint = "情绪修复：试错为主"

    cm = "⚠️20cm波动大·紧止损" if cand.get("is_20cm") else ""
    amt_lo_pct = round(r_lo * 100.0, 0)
    amt_hi_pct = round(r_hi * 100.0, 0)

    expected_text = (
        f"[{ident}|{path_labels.get(path, path)}] 竞价涨幅落 {lo}%~{hi}% "
        f"且量能≥{amount_min:.2f}亿(昨量{amt_lo_pct:.0f}%~{amt_hi_pct:.0f}%)，按计划买入"
        + (f"；{cm}" if cm else "")
    )
    super_text = (
        f"竞价涨幅≥{super_gap_min:.0f}% 且量能≥{amount_super:.2f}亿(昨量{amt_hi_pct:.0f}%+)，超预期直接买"
        + (f"；{cm}" if cm else "")
    )
    low_text = f"竞价未达 {lo}% 或量能<{amount_min:.2f}亿，{phase_hint}，不买入"

    ai_note = (
        f"情绪:{phase}({phase_note}) | 路径:{path_labels.get(path, path)} "
        f"| 龙头:{ident}(综{dragon['composite']})"
        + (f" | 题材:{theme_phase_tag}" if theme_phase_tag != "—" else "")
        + (f" | 昨日一字·回踩买" if one_word and path in ("缩量加速", "温和放量", "放量攻击") else "")
        + f" | 量昨{amt_lo_pct:.0f}~{amt_hi_pct:.0f}% | 置信{confidence}"
    )

    return {
        "expected_range": [lo, hi],
        "super_gap_min": super_gap_min,
        "auction_amount_min_yi": amount_min,
        "auction_amount_super_yi": amount_super,
        "auction_amount_range": [amt_lo_pct, amt_hi_pct],
        "amount_ratio": [r_lo, r_hi],
        "expected_text": expected_text,
        "super_text": super_text,
        "low_text": low_text,
        # 诊断字段
        "phase": phase,
        "phase_note": phase_note,
        "dragon_diagnosis": dragon,
        "theme_phase": theme_phase_tag,
        "confidence": confidence,
        "ai_note": ai_note,
        "is_ai": True,
        # VPA 特有字段
        "vpa_path": path,
        "vpa_path_label": path_labels.get(path, path),
        "vpa_turnover_tier": path_info["turnover_tier"],
        "vpa_seal_speed": path_info["seal_speed"],
        "vpa_broken_tier": path_info["broken_tier"],
    }
