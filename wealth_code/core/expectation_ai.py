#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 短线情绪·龙头战法 期望层（确定性引擎版）

把 a-stock-sentiment 技能的分析框架（情绪双维度 + 龙头三要素 + 情绪周期四阶段
+ 涨停反馈矩阵/连板高度/负反馈预警）编码为确定性引擎，替代 pipeline_eod 中
「只看板数给区间」的裸启发式 _build_expectation。

为什么是「确定性引擎」而不是在线调 LLM：
- 这是无人值守的 09:25 / 17:30 自动化，需要 100% 稳定可复现，不能依赖外部 LLM
  的可用性 / 速率 / 非确定性。
- 框架本身即方法论：情绪周期相位 + 龙头三要素打分 + 赚钱/亏钱效应加权，是
  量化情绪信号的通用做法，确定性实现即可给出「AI 级」研判，且每票可解释。
- 保留 live 注入口：若日后接入 LLM，只需在 build_ai_expectation 外包裹一层
  自然语言改写（不影响自动化稳定路径）。

输入（来自上游）：
- candidate：pipeline_eod._score_all 产出的候选（含 score/boards/reason/tags/
  theme/is_20cm/relay_rank + 组件分 ign_score/exp_score/lead_score/themeTier，
  由 pipeline_eod 注入）
- env：input_zt.load_environment 产出的情绪代理（sentiment_score/nuke_count/
  broken_rate/promote_rate/regime）
- theme_ctx：可选，theme_roll 产出的 {题材: {phase, confidence, cooled_days}}，
  用于给「处于 boom / 回流候选」的题材额外加分

输出：与 _build_expectation 完全兼容的 expectation dict，并附带 AI 诊断字段
（phase / dragon_diagnosis / confidence / ai_note），供 signal_eval 与卡片端使用。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "expectation_rules.json"


def _load_rules() -> dict[str, Any]:
    """读规则配置（参数可配置：改 JSON 不动代码）。失败时返回空 dict（引擎按默认值兜底）。"""
    try:
        return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ----------------------------------------------------------------------------
# 〇、涨停形态识别（短线表现维度）
#   一字板 / 秒板(快速板) / 缩量板 / 炸板回封 / 放量烂板 / 温和放量
#   优先级：一字 > 炸板(回封) > 快速 > 缩量 > 放量 > 温和
# ----------------------------------------------------------------------------

def classify_seal_form(cand: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    识别候选的涨停形态（基于 fbt 首封时间 / oneWord / zbc 炸板数 / hs 换手率）。

    返回 {"form": 形态名, "zbc": 炸板数, "hint": 语义提示, "extra_gap": 附加调整}
    """
    rules = rules or _load_rules()
    fc = rules.get("form_classify") or {}
    adj = rules.get("form_adjust") or {}

    # 取 fbt 前 4 位（HHMM）做比较，兼容 "0925" 与 "092502"(HHMMSS) 两种格式；
    # ⚠️ 不可用 zfill(6)：4 位 "1000".zfill(6)="001000" 会错误越过 "0926" 兜底判定。
    _fbt_raw = str(cand.get("fbt") or "9999")
    _fbt_head = _fbt_raw[:4]
    fbt4 = _fbt_head.zfill(4) if _fbt_head.isdigit() else "9999"
    one_word = bool(cand.get("oneWord")) or bool((cand.get("reason") or {}).get("ignition", {}).get("oneWord"))
    zbc = int(cand.get("zbc") or 0)
    hs = float(cand.get("hs") or 0.0)

    # 一字板：oneWord 标记优先；无标记时按 fbt≤0926 且 zbc=0 且 hs 极低兜底
    ow = fc.get("one_word") or {}
    if one_word or (fbt4 <= ow.get("max_fbt", "0926") and zbc == 0 and hs <= float(ow.get("max_hs", 3.0))):
        return {"form": "一字板", "zbc": zbc, "hint": adj.get("一字板", {}).get("hint", "一字锁筹·高开溢价强")}

    # 炸板回封：zbc≥1（含炸板王 zbc≥5 附加下调）
    if zbc >= 1:
        form = "放量烂板" if (hs >= float(fc.get("blow_vol_zbc", {}).get("min_hs", 12.0)) and zbc >= 1) else "炸板回封"
        extra = 0.0
        if zbc >= int(fc.get("zbc_king", {}).get("min_zbc", 5)):
            form = "放量烂板"
            extra = float(adj.get("zbc_king_extra", {}).get("gap", -1.0))
        return {"form": form, "zbc": zbc, "hint": adj.get(form, {}).get("hint", "盘中分歧"),
                "extra_gap": extra, "zbc_king": zbc >= int(fc.get("zbc_king", {}).get("min_zbc", 5))}

    # 快速板：fbt≤0935 且无炸板
    fb = fc.get("fast_board") or {}
    if fbt4 <= fb.get("max_fbt", "0935"):
        return {"form": "快速板", "zbc": zbc, "hint": adj.get("快速板", {}).get("hint", "早盘共识·溢价中上")}

    # 缩量板：hs < 5
    sv = fc.get("shrink_vol") or {}
    if hs < float(sv.get("max_hs", 5.0)):
        return {"form": "缩量板", "zbc": zbc, "hint": adj.get("缩量板", {}).get("hint", "缩量惜售·溢价强")}

    # 放量板：zbc=0 且 hs ≥ 15（放量换手，健康放量或高位分歧，中性偏谨慎）
    bv = fc.get("blow_vol") or {}
    if hs >= float(bv.get("min_hs", 15.0)):
        return {"form": "放量板", "zbc": zbc, "hint": adj.get("放量板", {}).get("hint", "放量换手·略谨慎")}

    return {"form": "温和放量", "zbc": zbc, "hint": adj.get("温和放量", {}).get("hint", "健康换手")}


# ----------------------------------------------------------------------------
# 〇、板块情况评估（梯队位置 × 板块情绪）
# ----------------------------------------------------------------------------

def sector_context(cand: dict[str, Any], env: dict[str, Any],
                   theme_ctx: dict[str, Any] | None, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    板块维度评估：
      tier_position：板块龙(龙头) / 容量核心 / 跟风(补涨)
      sector_sentiment：强 / 中 / 弱（由 theme_ctx 题材相位 + 主线判定推导）
    返回 {"tier": ..., "sentiment": ..., "tier_hint": ..., "sent_hint": ...}
    """
    rules = rules or _load_rules()
    tp = rules.get("tier_position") or {}
    ss = rules.get("sector_sentiment") or {}

    # —— 梯队位置 ——
    if bool(cand.get("is_sector_dragon")):
        tier = "dragon"
    elif bool(cand.get("is_capacity_core")) or float(cand.get("cap_score") or 0) >= 60:
        tier = "capacity"
    elif str(cand.get("style_tag") or "") == "龙头" and int(cand.get("relay_rank") or 0) <= 2:
        tier = "dragon"
    else:
        tier = "follower"

    # —— 板块情绪：题材相位（theme_ctx）优先，主线判定（daily_verdict main）加权 ——
    theme = str(cand.get("theme") or "")
    t_phase = ""
    if theme_ctx:
        t_phase = str((theme_ctx.get(theme) or {}).get("phase") or "")
    phase_map = ss.get("phase_map") or {}
    sentiment = phase_map.get(t_phase, "mid")
    verdict = (cand.get("daily_verdict") or {}).get("flags") or []
    if "main" in verdict and sentiment != "strong":
        sentiment = "mid" if sentiment == "weak" else sentiment  # 主线不直接提为强，稳定为主
    # 梯队中的龙头 + 主线 → 板块强
    if tier == "dragon" and "main" in verdict:
        sentiment = "strong"

    return {
        "tier": tier,
        "sentiment": sentiment,
        "tier_hint": (tp.get(tier) or {}).get("hint", tier),
        "sent_hint": (ss.get(sentiment) or {}).get("hint", sentiment),
    }


def _apply_form_sector_adjust(form_info: dict[str, Any], sec: dict[str, Any],
                              rules: dict[str, Any]) -> tuple[float, float, float]:
    """应用 形态 + 板块 的涨幅调整与置信度调整，返回 (gap_delta_lo, gap_delta_hi, conf_delta)。"""
    fa = rules.get("form_adjust") or {}
    sa = rules.get("sector_adjust") or {}

    g_lo = g_hi = 0.0
    conf = 0.0
    form = form_info.get("form") or "温和放量"
    fj = fa.get(form) or {}
    gap_adj = fj.get("gap") or [0.0, 0.0]
    if isinstance(gap_adj, list) and len(gap_adj) == 2:
        g_lo += float(gap_adj[0])
        g_hi += float(gap_adj[1])
    elif isinstance(gap_adj, (int, float)):
        g_lo += float(gap_adj)
        g_hi += float(gap_adj)
    conf += float(fj.get("conf_delta") or 0.0)
    g_lo += float(form_info.get("extra_gap") or 0.0)
    g_hi += float(form_info.get("extra_gap") or 0.0)

    key = f"{sec['tier']}_{sec['sentiment']}"
    sj = sa.get(key) or {}
    g_lo += float(sj.get("gap") or 0.0)
    g_hi += float(sj.get("gap") or 0.0)
    conf += float(sj.get("conf_delta") or 0.0)
    return g_lo, g_hi, conf


# ----------------------------------------------------------------------------
# 一、情绪周期四阶段（必读 a-stock-sentiment · 情绪周期四阶段模型）
#   冰点 → 修复 → 亢奋 → 分歧 → (回到冰点)
# ----------------------------------------------------------------------------

def sentiment_phase(env: dict[str, Any]) -> tuple[str, str]:
    """
    由情绪反馈矩阵判定当前周期阶段（确定性规则，对齐技能框架）。

    返回 (phase, note)：
      - 亢奋：赚钱效应强、亏钱效应弱（高情绪分 + 核按钮少 + 晋级率高）
      - 修复：情绪自冰点回暖（中等偏上）
      - 分歧：两极分化 / 高位淘汰（中等）
      - 冰点：亏钱效应主导（低情绪分 / 核按钮多 / 断板率高）

    ⚠️ 优先级：优先采用上游真实情绪标签 `sentiment_label`（由 11盘中猎手 基于
    a-stock-sentiment 框架判定的权威结论），保证 AI 层与实时情绪一致；仅当该标签
    缺失/不可识别时，才用下方阈值推导兜底。
    """
    # 缺分不能被旧标签或 `or 5` 重新包装成修复；风险事实仍在原字段保留。
    raw_score = env.get("sentiment_score")
    if (isinstance(raw_score, bool) or not isinstance(raw_score, (int, float))
            or not math.isfinite(raw_score) or not 1 <= raw_score <= 10):
        return "未知", "情绪评分未就绪；核按钮、破位等独立风险仍须单独核对"
    # —— 优先用真实标签 ——
    label = str(env.get("sentiment_label") or "").strip()
    KNOWN = {"冰点", "修复", "分歧", "亢奋"}
    if label in KNOWN:
        sscore = float(raw_score)
        nuke = int(env.get("nuke_count") or 0)
        broken = float(env.get("broken_rate") or 0.0)
        note_map = {
            "冰点": f"情绪分{sscore:.1f}/10、核按钮{nuke}家、断板率{broken:.0f}%，亏钱效应主导",
            "修复": f"情绪分{sscore:.1f}/10、自冰点回暖",
            "分歧": f"情绪分{sscore:.1f}/10、核按钮{nuke}家，两极分化/高位淘汰",
            "亢奋": f"情绪分{sscore:.1f}/10、核按钮{nuke}家，赚钱效应主导",
        }
        return (label, note_map[label])

    # —— 兜底：无真实标签时用阈值推导 ——
    sscore = float(raw_score)
    nuke = int(env.get("nuke_count") or 0)
    broken = float(env.get("broken_rate") or 0.0)
    promote = float(env.get("promote_rate") or 0.0)

    # 亏钱效应判定（负反馈预警）：核按钮≥3 或 断板率≥45 -> 退潮/冰点信号
    money_losing = (nuke >= 3) or (broken >= 45)
    # 赚钱效应判定：高情绪分 + 晋级率合格
    money_making = (sscore >= 6.0) and (promote >= 40 or nuke <= 2)

    if sscore >= 7.0 and nuke <= 2 and not money_losing:
        return ("亢奋", f"情绪分{sscore:.1f}/10、核按钮{nuke}家，赚钱效应主导")
    if sscore <= 3.5 or (nuke >= 5) or (broken >= 50):
        return ("冰点", f"情绪分{sscore:.1f}/10、核按钮{nuke}家、断板率{broken:.0f}%，亏钱效应主导")
    if money_making and sscore >= 5.5:
        return ("修复", f"情绪分{sscore:.1f}/10、晋级率{promote:.0f}%，自冰点回暖")
    return ("分歧", f"情绪分{sscore:.1f}/10、核按钮{nuke}家，两极分化/高位淘汰")


# ----------------------------------------------------------------------------
# 二、龙头三要素诊断（带领性 × 突破性 × 唯一性）
# ----------------------------------------------------------------------------

def _star(frac: float) -> int:
    """0~1 占比 -> 1~5 星（整数）。"""
    return max(1, min(5, round(max(0.0, min(1.0, frac)) * 5)))


def dragon_three_diagnosis(cand: dict[str, Any], *, use_relay_rank: bool = True) -> dict[str, Any]:
    """
    对单只候选做龙头三要素打分（确定性，来自候选已有因子 + 连板高度）。

    带领性(leadership)：来自 score_leadership 组件分（lead_score/20）；
      缺失时由「先发」标签近似。
    突破性(breakthrough)：来自连板高度（打开空间）+ 一字/强封加分。
    唯一性(uniqueness)：来自题材层级（一级题材）+ 接力排名 + 龙头身份。

    综合 -> 真龙头(主升) / 准龙头 / 跟风(补涨)。
    """
    boards = int(cand.get("boards") or 1)
    tags = cand.get("tags") or []
    lead_score = cand.get("lead_score")
    theme_tier = cand.get("themeTier") or ((cand.get("reason") or {}).get("themeTier") or 2)
    relay_rank = int(cand.get("relay_rank") or 0)

    # —— 带领性 ——
    if lead_score is not None:
        lead_frac = float(lead_score) / 20.0
    else:
        lead_frac = 0.7 if ("先发" in tags or "空间" in tags) else 0.4
    lead_stars = _star(lead_frac)

    # —— 突破性（连板高度即空间突破）——
    if boards >= 5:
        brk_frac = 1.0
    elif boards == 4:
        brk_frac = 0.85
    elif boards == 3:
        brk_frac = 0.65
    elif boards == 2:
        brk_frac = 0.45
    else:
        brk_frac = 0.3
    if ("一字板" in tags) or ("强封" in tags):
        brk_frac = min(1.0, brk_frac + 0.1)
    brk_stars = _star(brk_frac)

    # —— 唯一性 ——
    uniq_frac = 0.5
    if theme_tier == 1:
        uniq_frac += 0.25          # 一级题材卡位
    if use_relay_rank and relay_rank == 1:
        uniq_frac += 0.15          # 接力池头名
    if ("一级题材" in tags) or ("带动板块" in tags):
        uniq_frac += 0.1
    uniq_stars = _star(uniq_frac)

    composite = (lead_stars + brk_stars + uniq_stars) / 3.0
    if composite >= 4.0 and brk_stars >= 4:
        identity = "真龙头(主升)"
    elif composite >= 3.0:
        identity = "准龙头"
    else:
        identity = "跟风/补涨"

    def _stars(n: int) -> str:
        return "★" * n + "☆" * (5 - n)

    return {
        "leadership": {"stars": lead_stars, "text": _stars(lead_stars)},
        "breakthrough": {"stars": brk_stars, "text": _stars(brk_stars)},
        "uniqueness": {"stars": uniq_stars, "text": _stars(uniq_stars)},
        "composite": round(composite, 2),
        "identity": identity,
    }


# ----------------------------------------------------------------------------
# 三、期望区间构建（情绪 + 龙头 + 题材动量 三方加权）
# ----------------------------------------------------------------------------

def _base_range() -> tuple[float, float]:
    """统一基础竞价预期区间（2026-08-12 调整：压低正常预期）。
    普通涨停次日正常红开 ~0~2.5% 即为有溢价、达标；炸板类再压低（红开即超预期）。
    数值来自 config/expectation_rules.json 的 base_range，改参数不动代码。"""
    rules = _load_rules()
    br = (rules or {}).get("base_range") or [0.0, 2.5]
    try:
        return float(br[0]), float(br[1])
    except (TypeError, IndexError, ValueError):
        return 0.0, 2.5


def _non_tradeable_expectation(
    cand: dict[str, Any],
    form_info: dict[str, Any],
    env: dict[str, Any],
    theme_ctx: dict[str, Any] | None,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """一字板等无法在集合竞价买入的标的：不产出可交易竞价预期区间。

    仅作板块强度参考；预期侧标注「无参与机会」，卡片端据此跳过可交易区间渲染。
    """
    is_20cm = bool(cand.get("is_20cm"))
    cm = "⚠️20cm波动大·紧止损" if is_20cm else ""
    note = "一字板当日封死，竞价无法买入，不计入可交易预期；仅作板块强度参考。"
    expected_text = f"一字封死·无参与机会·无竞价预期{cm}"
    return {
        "is_ai": True,
        "expected_range": None,
        "non_tradeable": True,
        "seal_form": form_info.get("form"),
        "seal_zbc": form_info.get("zbc"),
        "super_gap_min": None,
        "auction_amount_min_yi": 0.0,
        "auction_amount_range": [0.0, 0.0],
        "auction_amount_super_yi": 0.0,
        "auction_boom_yi": 0.0,
        "boom_label": (rules.get("auction_boom") or {}).get("label", "🔺爆量高开"),
        "gap_strength": "na",
        "gap_strength_hint": "—无参与",
        "phase": "—",
        "phase_note": "一字封死",
        "theme_phase": "—",
        "dragon_diagnosis": {},
        "sector_tier": "—",
        "sector_sentiment": "—",
        "confidence": 0.0,
        "expected_text": expected_text,
        "super_text": expected_text,
        "low_text": expected_text,
        "ai_note": note,
    }


def build_ai_expectation(
    cand: dict[str, Any],
    env: dict[str, Any],
    theme_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    AI 期望层主入口：结合 情绪周期 + 龙头三要素 + 题材动量，产出竞价预期。

    返回 expectation dict（兼容 signal_eval，并附 AI 诊断）：
      expected_range / super_gap_min / auction_amount_min_yi
      expected_text / super_text / low_text
      phase / dragon_diagnosis / theme_phase / confidence / ai_note
    """
    is_20cm = bool(cand.get("is_20cm"))

    # —— 一字板：无法在竞价买入，不给可交易竞价预期（无参与机会）——
    _rules0 = _load_rules()
    _fi0 = classify_seal_form(cand, _rules0)
    if _fi0.get("form") == "一字板":
        return _non_tradeable_expectation(cand, _fi0, env, theme_ctx, _rules0)
    theme = str(cand.get("theme") or "其他")

    phase, phase_note = sentiment_phase(env)
    dragon = dragon_three_diagnosis(cand)

    # —— 基础区间（统一基准，不再按板数分级）——
    lo, hi = _base_range()

    # —— 情绪相位调整 ——
    if phase == "亢奋":
        lo += 1.0
        hi += 2.0
    elif phase == "修复":
        lo += 0.5
        hi += 1.0
    elif phase == "分歧":
        hi -= 1.0
    elif phase == "冰点":
        lo -= 1.0
        hi -= 2.0

    # —— 短线表现维度（涨停形态）＋ 板块维度（梯队 × 情绪）——（2026-08-12 新增）
    rules = _load_rules()
    form_info = classify_seal_form(cand, rules)
    sec = sector_context(cand, env, theme_ctx, rules)
    gap_lo_d, gap_hi_d, conf_d = _apply_form_sector_adjust(form_info, sec, rules)
    lo += gap_lo_d
    hi += gap_hi_d

    # —— 大溢价核心驱动：龙头带领性 + 容量梯队（门控于环境阶段）——
    # 设计依据（用户铁律 + 原版 quant-review 选股 6 维评分）：
    #   正常票小高开；仅「真龙头(is_sector_dragon) + 容量核心(is_capacity_core)」
    #   且处于加速期(亢奋/修复) 才给 5%+ 溢价（最后阶段疯狂加速）；
    #   真龙头但容量未跟上 → 溢价有限；跟风/补涨 → 压缩下沿。
    lp = rules.get("leadership_premium") or {}
    is_dragon = (dragon["identity"].startswith("真龙头")) or bool(cand.get("is_sector_dragon"))
    is_cap = bool(cand.get("is_capacity_core")) or float(cand.get("cap_score") or 0) >= 60
    if is_dragon and is_cap:
        blk = lp.get("dragon_capacity") or {}
    elif is_dragon:
        blk = lp.get("dragon_only") or {}
    elif is_cap:
        blk = lp.get("capacity_only") or {}
    else:
        blk = lp.get("follower") or {}
    # 未知环境不提供依赖情绪确认的龙头溢价，非环境项保持原规则。
    env_gate = 0.0 if phase == "未知" else float((lp.get("env_gate") or {}).get(phase, 0.5))
    if blk.get("need_env"):
        g = env_gate
    else:
        g = 1.0
    lg_lo, lg_hi = blk.get("gap", [0.0, 0.0])
    lo += round(lg_lo * g, 1)
    hi += round(lg_hi * g, 1)
    conf_d += float(blk.get("conf_delta") or 0.0) * (g if blk.get("need_env") else 1.0)

    # —— 题材动量（来自 theme_roll）——
    theme_phase = "—"
    if theme_ctx:
        tc = theme_ctx.get(theme) or {}
        theme_phase = tc.get("phase") or "—"
        if theme_phase in ("boom", "re-entry-candidate"):
            hi += 0.5  # 题材处于爆发/回流，竞价容错更高

    # —— 东财次日预期题材命中（P0 孤岛打通·2026-08-17）——
    # em_expect = 东财「明日题材预期」（含催化标题），此前抓了无人消费（纯孤岛）。
    # 现在：候选 theme 命中 em_expect 题材 → 竞价预期上浮 + 催化来源标注（情绪传递：消息面→题材→个股）。
    # ⚠️ 命名体系差异：候选 theme=申万行业名（通信设备/半导体），em_expect name=题材名（CPO概念/稀土永磁），
    # 精确相等几乎不命中 → 用「子串包含 + 核心词映射」双通道（如 半导体⇄CPO/光模块，元件⇄PCB/覆铜板）。
    _EM_ALIAS = {
        "通信设备": ["CPO", "光模块", "光通信", "液冷", "AI服务器"],
        "元件": ["PCB", "覆铜板", "电子布", "MLCC"],
        "半导体": ["芯片", "半导体", "国产芯片", "存储"],
        "光学光电": ["光学", "光电子", "消费电子"],
        "小金属": ["稀土", "磁材", "稀有金属"],
        "工业金属": ["铜", "铝", "有色"],
        "电网设备": ["电网", "特高压", "电力设备"],
        "装修装饰": ["装修", "家居", "地产链"],
        "通用设备": ["机器人", "机械", "减速器"],
        "专用设备": ["半导体设备", "锂电设备", "光伏设备"],
        "医疗服务": ["医疗", "医药", "创新药", "中药"],
        "IT服务Ⅱ": ["算力", "AI应用", "软件", "数字经济"],
    }
    em_hit = ""
    for _ee in (env.get("em_expect") or []):
        _nm = str(_ee.get("name") or "")
        if not _nm:
            continue
        _hit = (_nm == theme) or (_nm in str(cand.get("sector") or ""))
        if not _hit:
            for _t in (cand.get("themes") or []):
                if _nm in str(_t):
                    _hit = True
                    break
        if not _hit:
            # 核心词别名映射（申万行业 → 题材关键词）
            for _kw in _EM_ALIAS.get(theme, []):
                if _kw in _nm or _nm in _kw:
                    _hit = True
                    break
        if _hit:
            em_hit = str(_ee.get("title") or _nm)
            hi += 0.5
            lo += 0.2
            break

    # —— 区间宽度钳制 + 涨停阈值校验（2026-08-17 用户「敞口过大·压缩至1~2个点」）——
    # 原因：base_range[0,2.5] + 相位(亢奋lo+1/hi+2) + 形态 + 板块 + 溢价叠加后，
    # 实测敞口 3.7~4.8 点（如 [1.5,6.0]），进场价管理困难。规则：
    #   hi = min(hi, lo + max_range_width)  —— 以 lo 为锚，宽度 ≤ max_range_width（配置默认 2.0）
    #   hi ≤ 涨停阈值（主板 9.9% / 20cm 19.9%）—— 竞价预期不可能超过涨停价
    _max_w = float((rules or {}).get("max_range_width") or 2.0)
    lo = round(max(0.0, lo), 1)
    _zt_cap = 19.9 if is_20cm else 9.9
    hi = round(max(lo + 0.5, min(hi, lo + _max_w, _zt_cap)), 1)

    # 一字板已在上游 _rank_and_split 排除出可交易池，且函数入口已防御返回；
    # 此处兜底：万一进入也不产出可交易竞价区间（无参与机会）。
    if form_info.get("form") == "一字板":
        return _non_tradeable_expectation(cand, form_info, env, theme_ctx, rules)

    # —— 超预期阈值 ——
    super_gap_min = round(hi + 1.0, 1)
    if phase in ("冰点", "分歧"):
        super_gap_min = round(hi + 2.0, 1)  # 退潮期更挑剔

    # —— 量价预期（用户指定口径）：竞价量能 = 昨日成交量(涨停当日成交额 cje_yi) 的 5%~10% ——
    # 参考 quant-review zt_analysis.auc_need = max(0.15, cje_yi * auc_ratio)，auc_ratio 按强弱浮动。
    # 这里把"单点阈值"升级为"区间 + 情绪联动"：
    #   基准区间 [5%, 10%] → 情绪强(亢奋/修复)开高、情绪弱(分歧/冰点)开低；
    #   真龙头量足确认(+1%)、跟风压缩(-0.5%)、20cm 竞价量占比天然低(-1%)。
    # 2026-08-18 下调（用户「量能区间再往下放一点」）：基准 5/10%→4/9%、亢奋/修复加成减半，
    # 符合预期但竞价量能稍弱的龙头（如金螳螂 4.9%昨量）不再被预期线硬拒。
    AMOUNT_RATIO_BASE = (0.04, 0.09)
    AMOUNT_RATIO_ADJ = {"亢奋": 0.015, "修复": 0.005, "分歧": -0.01, "冰点": -0.02}
    AMOUNT_FLOOR_YI = 0.15  # 量能下限（亿），避免小票要求不切实际
    cje_yi = float(cand.get("cje_yi") or 0.0)
    delta = AMOUNT_RATIO_ADJ.get(phase, 0.0)
    if dragon["identity"].startswith("真龙头"):
        delta += 0.01
    elif dragon["identity"] == "跟风/补涨":
        delta -= 0.005
    if is_20cm:
        delta -= 0.01  # 20cm 竞价量占昨量比例天然更低
    ratio_lo = round(max(0.03, min(0.12, AMOUNT_RATIO_BASE[0] + delta)), 3)
    ratio_hi = round(max(0.05, min(0.15, AMOUNT_RATIO_BASE[1] + delta)), 3)
    if ratio_hi < ratio_lo + 0.02:
        ratio_hi = ratio_lo + 0.02
    # 预期线（expected 量能下限，参考 QR 下限 0.15 亿）与 超预期线（super 更足）
    # 形态量能调整：炸板/放量要求量能更足确认承接，缩量板允许更低
    amount_delta = float((rules.get("form_adjust") or {}).get(form_info.get("form") or "", {}).get("amount_delta") or 0.0)
    ratio_lo_adj = round(max(0.03, min(0.12, ratio_lo + amount_delta)), 3)
    ratio_hi_adj = round(max(0.05, min(0.15, ratio_hi + amount_delta)), 3)
    amount_min = round(max(AMOUNT_FLOOR_YI, cje_yi * ratio_lo_adj), 2)
    amount_super = round(max(AMOUNT_FLOOR_YI + 0.05, cje_yi * ratio_hi_adj), 2)

    # —— 爆量高开线（2026-08-12 新增）：竞价额 ≥ 昨量×ratio 且 涨幅 ≥ 预期上沿 ——
    ab = rules.get("auction_boom") or {}
    boom_ratio = float(ab.get("ratio_of_prev_vol", 0.15))
    boom_min_yi = float(ab.get("min_yi", 0.20))
    auction_boom_yi = round(max(boom_min_yi, cje_yi * boom_ratio), 2)
    boom_label = ab.get("label", "🔺爆量高开")

    # —— 高开强度标签（预期侧，供卡片标注）——
    # 由「龙头带领性 + 容量梯队 + 环境阶段」决定溢价强度档位：
    #   强 = 真龙头+容量核心+加速期(亢奋，满门控)；中上 = 真龙头+容量但非亢奋(门控打折)；
    #   弱 = 炸板/烂板/板块弱/退潮。
    if phase == "未知":
        gap_strength, gap_strength_hint = "unknown", "情绪待确认·仅保留个股形态参考"
    elif is_dragon and is_cap and phase == "亢奋":
        gap_strength, gap_strength_hint = "strong", "🔺龙头+容量·加速溢价强"
    elif is_dragon and is_cap:
        gap_strength, gap_strength_hint = "mid", "⬆龙头+容量·高开预期中上"
    elif (is_dragon or is_cap) and sec["sentiment"] != "weak":
        gap_strength, gap_strength_hint = "mid", "⬆正常高开预期"
    elif form_info.get("form") in ("炸板回封", "放量烂板") or sec["sentiment"] == "weak" or phase in ("冰点", "分歧"):
        gap_strength, gap_strength_hint = "weak", "⚠️高开谨慎/低开风险"
    else:
        gap_strength, gap_strength_hint = "mid", "⬆正常高开预期"

    # —— 置信度（0~1）：情绪(0.3) + 龙头(0.3) + 形态(0.2) + 板块(0.2) ——
    # 未知项不给置信贡献，也不重分配其权重；0 贡献不代表观测为冰点。
    phase_score = {"亢奋": 0.9, "修复": 0.7, "分歧": 0.45, "冰点": 0.2}.get(phase, 0.0)
    dragon_score = dragon["composite"] / 5.0
    form_conf = {"一字板": 0.9, "缩量板": 0.8, "快速板": 0.7, "温和放量": 0.6, "放量板": 0.5,
                 "炸板回封": 0.45, "放量烂板": 0.35}.get(form_info.get("form"), 0.5)
    sec_conf = {"strong": 0.8, "mid": 0.6, "weak": 0.4}.get(sec["sentiment"], 0.5)
    confidence = round(phase_score * 0.3 + dragon_score * 0.3 + form_conf * 0.2 + sec_conf * 0.2 + conf_d, 2)
    mood = str(cand.get("relay_mood") or "insufficient")
    # 路径情绪只改变预判把握度和竞价确认要求，不凭昨日均值改写个股高开价区间。
    confidence += {"supportive": 0.05, "pressured": -0.05}.get(mood, 0.0)
    confidence = round(max(0.05, min(0.98, confidence)), 2)

    # —— 文案 ——
    ident = dragon["identity"]
    if phase == "冰点":
        phase_hint = "情绪冰点：只低吸不追高，竞价达标也控仓"
    elif phase == "分歧":
        phase_hint = "情绪分歧：高位淘汰加剧，竞价弱于预期直接放弃"
    elif phase == "亢奋":
        phase_hint = "情绪亢奋：龙头加速，竞价高开即强，可放宽超预期"
    elif phase == "修复":
        phase_hint = "情绪修复：试错为主，按预期区间执行"
    else:
        phase_hint = "情绪待确认：区间仅为个股形态参考，不视作环境放行"

    cm = "⚠️20cm波动大·紧止损" if is_20cm else ""
    amt_lo_pct = round(ratio_lo_adj * 100.0, 0)
    amt_hi_pct = round(ratio_hi_adj * 100.0, 0)
    form_tag = f"[{form_info.get('form')}{('×' + str(form_info['zbc']) if form_info.get('zbc', 0) >= 1 else '')}]"
    # 炸板类（炸板回封/放量烂板/任何炸板）→ 次日红开即超预期，需辨真假高开
    blow_warn = ""
    if form_info.get("form") in ("炸板回封", "放量烂板") or (form_info.get("zbc") or 0) >= 1:
        blow_warn = "｜次日红开即超预期·辨真假高开(看进价与量能是否跟上)"
    stage_name = {"one_to_two": "1进2", "two_to_three": "2进3",
                  "high_relay": "高位接力", "n_plus_n": "断板后二波"}.get(cand.get("relay_stage"), "接力")
    leg = cand.get("n_plus_n") or {}
    if leg:
        stage_name = f"前段{leg['prior_boards']}板、断板后再走{leg['second_boards']}板"
    buy_preview = {"supportive": f"{stage_name}反馈转好，仍等次日竞价确认",
                   "pressured": f"{stage_name}反馈偏弱，次日须达到计划量能才考虑",
                   "mixed": f"{stage_name}反馈分化，次日看竞价量价"}.get(
                       mood, f"{stage_name}样本或竞价不足，按原计划观察")
    expected_text = (
        f"{form_tag}{sec['tier_hint']}·{sec['sent_hint']} {gap_strength_hint} | "
        f"竞价涨幅 {lo}%~{hi}% 且量能≥{amount_min:.2f}亿(昨量{amt_lo_pct:.0f}%~{amt_hi_pct:.0f}%)，再按计划判断"
        + (f"；{cm}" if cm else "")
        + blow_warn
        + f"｜{buy_preview}"
    )
    super_text = (
        f"竞价涨幅≥{super_gap_min:.0f}% 且量能≥{amount_super:.2f}亿(昨量{amt_hi_pct:.0f}%+)，超预期直接买"
        + (f"；{cm}" if cm else "")
    )
    if phase == "未知":
        super_text = f"情绪待确认：涨幅≥{super_gap_min:.0f}%且量能≥{amount_super:.2f}亿仅作量价参考，不构成环境放行"
    low_text = f"竞价未达 {lo}% 或量能<{amount_min:.2f}亿，{phase_hint}，不买入"

    ai_note = (
        f"情绪:{phase}({phase_note}) | 形态:{form_info.get('form')}"
        f"(炸板{form_info.get('zbc', 0)}) | 板块:{sec['tier_hint']}+{sec['sent_hint']}"
        f" | 龙头:{ident}(综{dragon['composite']})"
        + (f" | 题材:{theme_phase}" if theme_phase != "—" else "")
        + (f" | 📡东财预期:{em_hit[:24]}" if em_hit else "")
        + f" | 量昨{amt_lo_pct:.0f}~{amt_hi_pct:.0f}% | 爆量线{auction_boom_yi:.2f}亿 | 置信{confidence}"
    )

    return {
        "expected_range": [lo, hi],
        "super_gap_min": super_gap_min,
        "auction_amount_min_yi": amount_min,
        "auction_amount_super_yi": amount_super,
        # 量价预期（占昨日成交量比例，%）：供卡片「量昨x~y%」展示
        "auction_amount_range": [amt_lo_pct, amt_hi_pct],
        "amount_ratio": [ratio_lo_adj, ratio_hi_adj],
        # —— 爆量高开线（新增）——
        "auction_boom_yi": auction_boom_yi,
        "boom_label": boom_label,
        # —— 高开强度标签（新增）——
        "gap_strength": gap_strength,
        "gap_strength_hint": gap_strength_hint,
        "relay_mood": mood,
        "buy_preview": buy_preview,
        # —— 形态 / 板块诊断（新增）——
        "seal_form": form_info.get("form", ""),
        "seal_zbc": form_info.get("zbc", 0),
        "sector_tier": sec["tier"],
        "sector_sentiment": sec["sentiment"],
        "expected_text": expected_text,
        "super_text": super_text,
        "low_text": low_text,
        # —— AI 诊断字段 ——
        "phase": phase,
        "phase_note": phase_note,
        "sentiment_available": phase != "未知",
        "sentiment_model": env.get("sentiment_model"),
        "sentiment_quality": env.get("sentiment_quality", "unavailable"),
        "sentiment_inputs_missing": env.get("sentiment_inputs_missing", []),
        "dragon_diagnosis": dragon,
        "theme_phase": theme_phase,
        "confidence": confidence,
        "ai_note": ai_note,
        "is_ai": True,
    }
