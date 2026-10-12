#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财富密码 · 综合分模型（忠实 spec §4.3 十一维 raw_score）

设计铁律（用户 2026-08-13 明确要求：严格贴合原量化系统）：
- 评分权重严格按 spec §4.3：
    raw_score = environment*0.16 + sector_sentiment*0.22 + leader_factor*0.21
              + relay_factor*0.17 + capacity*0.14 + risk_control*0.08 + opportunity*0.02
              + identity_edge + height_breakout_bonus + market_gate_adjust + tide_adjust
- spec 原版「环境/板块/接力/风险/潮汐」四+一维原本是代理估算（依赖一整套上游因子管线）。
  本模块把它们替换为**真实数据驱动**：
    · environment_score      ← pulse 三大指数均值涨跌幅 + feedback_matrix 情绪分
    · sector_sentiment_score ← pulse 板块风险(下跌家数/反向) + 强势板块净流入 + daily_verdict 主线/回避
    · risk_control_score     ← feedback_matrix 核按钮/破位/断板率（越高=风险越可控的反向映射）
    · tide_adjust            ← 真实 env regime（risk_on/neutral/risk_off）
- AI 研判**不另立打分**，而是作为 spec 自有维度的**输入**折叠进去（ai_env_score 调制
  环境/板块分；sector_priority/avoid 调制板块/风险分；market_regime 影响 tide_adjust）。
  → AI 是维度的输入，不是平行打分，保证权重结构仍是 spec 原版。
- 接力/观察选择严格按 spec §4.4 四档（高度突破/高标龙头/核心接力/1进2）+ strict→relaxed→broad→emergency 级联。

纯算法层，仅依赖标准库。
"""

from __future__ import annotations

from typing import Any

# ============================================================================
# P2-9 分池校准常量（2026-08-17 用户"情绪核心"战法回标）
# 数据实证：8/14 买入 3 只全来自观察池（接力池 5 只全灭），大赢家
# 共进(3板·炸板多)+7.33%、蓝盾(5板20cm·炸5次)+11.32% 均因 breakRisk/open
# 硬门槛被 relay 档拒绝、只能落观察池后排 → 与"只做最高辨识度票"思路冲突。
# ⛔ 可逆开关：所有阈值集中于此，回退旧行为只需还原下述三组数值。
# ----------------------------------------------------------------------------
# ① 炸板风险：高位(≥3板)烂板=人气分歧后封死=辨识度，惩罚降至 5.0/次；
#    低位(<3板)烂板=封板质量差，惩罚保持 12.0/次。
ZBC_PENALTY_HIGH_BOARD = 5.0   # boards>=3 的高位烂板惩罚/次
ZBC_PENALTY_LOW_BOARD = 12.0   # boards<3  的低位烂板惩罚/次
# ② relay 档 breakRisk 上限：高标龙头/主线接力容忍中高炸板（辨识度优先）
BREAK_RISK_MAX_HEIGHT_BREAKOUT = 80.0   # 高度突破档（原 68）
BREAK_RISK_MAX_HIGH_MARK = 85.0         # 高标龙头档（原 68）
BREAK_RISK_MAX_CORE = 85.0              # 主线接力档（原 76）
# ③ 炸板次数(open)上限：高位烂板允许分歧回封
OPEN_MAX_HIGH_MARK = 8   # 高标龙头档（原 2）
OPEN_MAX_CORE = 12       # 主线接力档（原 8）
# ============================================================================
# 低位接力倾斜方向（2026-09-01 用户「喜欢 2进3·首板看不出龙头」·轻微调整）
# 实证逻辑：首板（1进2）龙头辨识度未现，2板（2进3）已现龙头雏形 → 2进3 倾斜优先。
# 纯权重对调（boards==2 加分 > boards==1），⛔ 不放松 one2 硬门槛、不塞池、不动档位。
# 原值：boards==1(+4.0) / boards==2(+3.0)（1进2 优先）——回退旧行为还原两组数值即可。
RELAY_BONUS_2TO3 = 4.0   # 2进3（2板进3板·已现龙头雏形）
RELAY_BONUS_1TO2 = 3.0   # 1进2（首板·龙头辨识度未现）
RELAY_MOOD_SUPPORT_ADJUST = 5.0
RELAY_MOOD_PRESSURE_ADJUST = -5.0
# ============================================================================
# 2026-08-18「重溢价·轻大盘」tide 门控修正（用户定·最高优先级）
# 实证：8/18 晋级率16%+核按钮17+真破位2 → risk_off → tide 一票否决清零接力池，
# 与「个股层轻大盘」矛盾——大盘退潮 ≠ 所有个股接力失效，应「降档收严」而非「清零」。
# 新行为：risk_off 只做档位收严（高度突破/高标龙头照常、relaxed/broad/emergency 兜底，
# 主线接力/1进2 中位档 tideRelayGate>=0 继续收严）；ice（情绪分≤2）冰点禁买保留清零。
# ⛔ 可逆开关：True=旧行为(risk_off 一票否决清零) / False=新行为(risk_off 降档收严)
TIDE_BLOCK_ON_RISK_OFF = False
# ============================================================================
# 🔒 低板孤军不进接力池（2026-10-12 用户「你推了一个没有板块的独苗！这在接力中
#    非常不可取」·C 方案）：低板（boards ≤ LONE_LOWBOARD_MAX）且孤军
#    （行业/题材只剩自己，或 core_theme 无有效题材 {空/其他/—}）→ 移出接力池
#    （line 593 自动回落观察池）。
# ⛔ 高标独立龙头（boards ≥ 3 的孤军）**保留**——2026-08-24 用户原意「认可独立总龙头」。
# ⛔ 可逆：ENABLE_LONE_LOWBOARD_EXCLUDE=False → 逐字回到改动前（孤军照进接力池）。
ENABLE_LONE_LOWBOARD_EXCLUDE = True
LONE_LOWBOARD_MAX = 2
# ============================================================================
# ----------------------------------------------------------------------------
# 2026-09-09 用户「冰点也要给我推接力池·买不买由人来决定」：解除 P1-6 ice freeze——
# ice（情绪≤2）不再强制清零接力池，改「跳过 strict 直接走 relaxed→broad→emergency
# 收严级联」，能选出多少推多少（选不出自然空·诚实），候选仅供参考、买不买由人决定；
# signal_eval 的 ice buy 硬开关属「买入规则」，用户后续单独优化，本次不动。
# ⛔ 可逆开关：True=回退旧行为(ice freeze 清零) / False=新行为(ice 解冻·收严级联产池)
ICE_FREEZE_RELAY = False
# ============================================================================
# 历史连板晋级因子（2026-09-05）：由 pipeline_eod 预先回填。
# 只作为小幅排序修正，概率无历史后验时不生效；可逆开关保留。
HISTORICAL_RELAY_FACTOR_ENABLED = True
HISTORICAL_RELAY_FACTOR_MAX = 7.0
# ============================================================================


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _to_num(v: Any, d: float = 0.0) -> float:
    try:
        if v is None:
            return float(d)
        if isinstance(v, str):
            v = v.replace("%", "").replace("板", "").strip()
        n = float(v)
        return n if __import__("math").isfinite(n) else float(d)
    except Exception:
        return float(d)


# ============================================================================
# 一、十一维 raw_score（spec §4.3，真实环境驱动）
# ============================================================================

def compute_raw_score(cand: dict[str, Any], env: dict[str, Any], pulse: dict[str, Any] | None, ai: dict[str, Any] | None) -> dict[str, Any]:
    """
    对单只候选计算 spec §4.3 十一维分。

    cand: pipeline_eod._score_all 产出（含 ign/exp/lead/cap_score/ladder/theme/daily_verdict 等）
    env : input_zt.load_environment 产出（feedback_matrix 衍生：sentiment_score/nuke_count/broken_rate/regime）
    pulse: input_env.load_pulse 产出（三大指数/情绪/板块风险/弱板块/强势板块），可空
    ai  : ai_env.json 解析（market_regime/ai_env_score/sector_priority/sector_avoid），可空
    """
    sentiment_score = _to_num(env.get("sentiment_score"), 5.0)
    nuke = int(env.get("nuke_count") or 0)
    broken = _to_num(env.get("broken_rate"), 0.0)
    regime = env.get("regime") or "neutral"
    theme = str(cand.get("theme") or "")
    dv_flags = (cand.get("daily_verdict") or {}).get("flags") or [] if isinstance(cand.get("daily_verdict"), dict) else []
    ign = _to_num(cand.get("ign_score"))
    exp = _to_num(cand.get("exp_score"))
    lead = _to_num(cand.get("lead_score"))
    cap = _to_num(cand.get("cap_score"))
    ladder = _to_num(cand.get("ladder_health"), 0.0)  # 0~10
    boards = int(_to_num(cand.get("boards"), 1))
    zbc = int(_to_num(cand.get("zbc"), 0))
    is_dragon = bool(cand.get("is_sector_dragon"))
    theme_bonus = _to_num(cand.get("themeBonus")) or _to_num((cand.get("reason") or {}).get("themeBonus"))
    tags = cand.get("tags") or []

    # —— 1) environment_score（真实：pulse 指数 + feedback 情绪）——
    avg_idx = 0.0
    if pulse:
        idxs = pulse.get("index") or []
        if idxs:
            avg_idx = sum(_to_num(i.get("pct")) for i in idxs) / len(idxs)
    environment_score = _clamp(50.0 + avg_idx * 6.0 + (sentiment_score - 5.0) * 4.0)

    # —— 2) sector_sentiment_score（真实：pulse 板块风险 + 强势板块 + daily_verdict）——
    sector_s = 65.0
    if pulse:
        sr = pulse.get("sector_risk") or {}
        declining = sr.get("declining") or []
        inflow = sr.get("inflow_top") or []
        sector_s += min(len(inflow) * 2.0, 10.0)          # 强势板块净流入 → 加分
        sector_s -= min(len(declining) * 1.5, 15.0)        # 下跌板块多 → 减分
        if sr.get("ok") is False:
            sector_s -= 8.0
    if "main" in dv_flags:
        sector_s += 10.0
    if "avoid_breadth" in dv_flags:
        sector_s -= 20.0
    if "observe" in dv_flags:
        sector_s -= 8.0
    sector_sentiment_score = _clamp(sector_s)

    # —— 3) leader_factor_score（复用龙头分量）——
    leader_factor_score = _clamp(ign + exp + lead)

    # —— 4) relay_factor_score（梯队 + 高度 + 日报主线）——
    # 创业板(300/301)开放（2026-08-13 反转 08-04「主板倾斜」）：
    # 原 `0.0 if is_20cm else 12.0` = 主板 +12 / 创业板 +0 的倾斜。
    # 正确反转 = 创业板也拿同等 +12（板中性常量），而非把主板降级。
    # 主板/创业板按同一套封板质量→竞价溢价情绪周期对称参与，不叠加 20cm 折让。
    # 同路径的真实晋级、竞价溢价、失败核按钮只微调接力维度；小样本不调分。
    mood_adjust = {"supportive": RELAY_MOOD_SUPPORT_ADJUST,
                   "pressured": RELAY_MOOD_PRESSURE_ADJUST}.get(cand.get("relay_mood"), 0.0)
    relay_factor_score = _clamp(
        45.0 + 12.0 + ladder * 4.0
        + (10.0 if boards >= 3 else (5.0 if boards >= 2 else 0.0))   # 高度加分（保留，不反转）
        + (8.0 if "main" in dv_flags else 0.0)
        + (RELAY_BONUS_2TO3 if boards == 2 else (RELAY_BONUS_1TO2 if boards == 1 else 0.0))    # 低位接力倾斜：2进3优先（2026-09-01 用户）
        + mood_adjust
    )

    # —— 5) capacity_score（容量因子，系统真实）——
    capacity_score = _clamp(cap)

    # —— 6) risk_control_score（真实：feedback 核按钮/破位/断板率，越高=越可控）——
    risk_control_score = _clamp(
        100.0 - nuke * 4.0 - broken * 0.8 - (10.0 - sentiment_score) * 2.0 - (15.0 if zbc >= 3 else 0.0)
    )

    # —— 7) opportunity_score（题材机会：一级题材 + 爆发命中 + 热门题材匹配）——
    opp = 40.0
    if theme_bonus > 0:
        opp += 30.0
    exp_r = cand.get("reason", {}).get("explosion") or {}
    if exp_r.get("hit"):
        opp += 20.0
    if pulse:
        hot = {str(h.get("sector") or "") for h in (pulse.get("hot_spots") or [])}
        if theme in hot:
            opp += 10.0
    opportunity_score = _clamp(opp)

    # —— 8) identity_edge（身份优势：题材带领性/唯一性）——
    identity_edge = 0.0
    if is_dragon:
        identity_edge += 5.0
    if lead >= 15:
        identity_edge += 3.0
    if "跟风" in tags:
        identity_edge -= 3.0

    # —— 9) height_breakout_bonus（高度突破加成）——
    height_breakout_bonus = 8.0 if boards >= 5 else (4.0 if boards >= 3 else 0.0)

    # —— 9.5) 历史连板晋级因子：历史概率 + 当前情绪已在 pipeline_eod 回填。
    # 不把概率伪装成确定性；无样本为 0，且修正封顶，防止压过龙头/板块/梯队硬因子。
    historical_relay_factor = _to_num(cand.get("history_relay_factor"), 0.0)
    if not HISTORICAL_RELAY_FACTOR_ENABLED:
        historical_relay_factor = 0.0
    historical_relay_factor = max(-HISTORICAL_RELAY_FACTOR_MAX, min(HISTORICAL_RELAY_FACTOR_MAX, historical_relay_factor))

    # —— 10) market_gate_adjust（开仓闸门：环境周期）——
    market_gate_adjust = {"risk_on": 2.0, "neutral": 0.0, "risk_off": -5.0}.get(regime, 0.0)

    # —— 11) tide_adjust（潮汐：真实 env regime；AI market_regime 可强化）——
    tide_adjust = {"risk_on": 3.0, "neutral": 0.0, "risk_off": -4.0}.get(regime, 0.0)
    ai_regime = (ai or {}).get("market_regime")
    if ai_regime in ("risk_on", "neutral", "risk_off"):
        # AI 研判与环境共振才强化，分歧时取较弱者（保守）
        ai_tide = {"risk_on": 3.0, "neutral": 0.0, "risk_off": -4.0}[ai_regime]
        tide_adjust = min(tide_adjust, ai_tide) if ai_tide < tide_adjust else max(tide_adjust, ai_tide)
        tide_adjust = (tide_adjust + ai_tide) / 2.0

    # —— AI 折叠进 spec 自有维度（不新增打分维度）——
    ai_env_score = _to_num((ai or {}).get("ai_env_score"), 5.0)  # 0-10
    ai_delta = (ai_env_score - 5.0) * 3.0  # ±15 上限
    environment_score = _clamp(environment_score + ai_delta)
    sector_sentiment_score = _clamp(sector_sentiment_score + ai_delta)
    ai_priority = set((ai or {}).get("sector_priority") or [])
    ai_avoid = set((ai or {}).get("sector_avoid") or [])
    if theme in ai_priority:
        sector_sentiment_score = _clamp(sector_sentiment_score + 6.0)
    if theme in ai_avoid:
        sector_sentiment_score = _clamp(sector_sentiment_score - 15.0)
        risk_control_score = _clamp(risk_control_score - 10.0)

    # —— 加权合成（spec §4.3 权重精确）——
    weighted = (
        environment_score * 0.16
        + sector_sentiment_score * 0.22
        + leader_factor_score * 0.21
        + relay_factor_score * 0.17
        + capacity_score * 0.14
        + risk_control_score * 0.08
        + opportunity_score * 0.02
    )
    raw_score = _clamp(
        weighted + identity_edge + height_breakout_bonus + historical_relay_factor + market_gate_adjust + tide_adjust
    )

    return {
        "environment_score": round(environment_score, 2),
        "sector_sentiment_score": round(sector_sentiment_score, 2),
        "leader_factor_score": round(leader_factor_score, 2),
        "relay_factor_score": round(relay_factor_score, 2),
        "relay_mood_adjust": mood_adjust,
        "capacity_score": round(capacity_score, 2),
        "risk_control_score": round(risk_control_score, 2),
        "opportunity_score": round(opportunity_score, 2),
        "identity_edge": round(identity_edge, 2),
        "height_breakout_bonus": round(height_breakout_bonus, 2),
        "historical_relay_factor": round(historical_relay_factor, 2),
        "history_relay_prob": cand.get("history_relay_prob"),
        "history_relay_samples": int(_to_num(cand.get("history_relay_samples"), 0)),
        "market_gate_adjust": round(market_gate_adjust, 2),
        "tide_adjust": round(tide_adjust, 2),
        "raw_score": round(raw_score, 2),
    }


# ============================================================================
# 二、字段映射（把可用字段映射成 spec §4.4 选择器需要的命名）
# ============================================================================

def map_tactics_fields(cand: dict[str, Any], env: dict[str, Any], pulse: dict[str, Any] | None, ai: dict[str, Any] | None, raw: dict[str, Any]) -> dict[str, Any]:
    """把 pipeline_eod 候选 + 真实环境 + raw_score 映射成 spec §4.4 选择器字段。"""
    boards = int(_to_num(cand.get("boards"), 1))
    zbc = int(_to_num(cand.get("zbc"), 0))
    ign = _to_num(cand.get("ign_score"))
    exp = _to_num(cand.get("exp_score"))
    lead = _to_num(cand.get("lead_score"))
    cap = _to_num(cand.get("cap_score"))
    ladder = _to_num(cand.get("ladder_health"), 0.0)
    theme = str(cand.get("theme") or "")
    theme_bonus = _to_num(cand.get("themeBonus")) or _to_num((cand.get("reason") or {}).get("themeBonus"))
    seal_amt = _to_num(cand.get("seal_amt"))
    hs = _to_num(cand.get("hs"))
    cje = _to_num(cand.get("cje_yi"))
    is_one_word = bool(cand.get("is_one_word"))
    is_dragon = bool(cand.get("is_sector_dragon"))
    regime = env.get("regime") or "neutral"

    is_shrink = bool(hs > 0 and hs < 3 and cje < 5 and zbc == 0)
    # 题材泛化（广度无效应）：仅当题材本身属泛化/宽度标签才判 broad；选股宝真实板块默认非泛化
    GENERIC = {"小盘", "中盘", "大盘", "融资融券", "QFII持股", "基金重仓", "年度强势",
               "深股通", "沪股通", "富时罗素", "昨日涨停", "昨日连板"}
    is_broad_only = theme in GENERIC
    has_trade_theme = theme not in ("", "其他") and not is_broad_only

    tide_status = {"risk_on": "confirmed_mainline", "neutral": "observe_candidate", "risk_off": "afterglow_risk"}.get(regime, "observe_candidate")
    tide_action = "avoid" if regime == "risk_off" else "allow"
    tide_relay_gate = {"risk_on": 4.0, "neutral": 0.0, "risk_off": -4.0}.get(regime, 0.0)

    # 个股断板风险（spec 因子：个股自身特征为主 + 轻微环境调制；绝不用全市场核按钮数直接灌入）
    # P2-9 分池校准（2026-08-17 用户"情绪核心"战法回标）：
    # 旧公式 zbc*12.0 对高位烂板一票否决——共进(3板炸11)+7.33%、蓝盾(5板炸5)+11.32%
    # 全是 8/14 大赢家却因 breakRisk=100 被所有 relay 档拒之门外、只能落观察池后排。
    # 区分对待：高位(≥3板)烂板=人气分歧后封死=辨识度（惩罚降至 5.0/次）；
    # 低位(<3板)烂板=封板质量差（惩罚保持 12.0/次）。一票否决仅剩 tide 门控。
    regime_risk_mod = {"risk_on": 0.0, "neutral": 8.0, "risk_off": 22.0}.get(regime, 8.0)
    zbc_penalty = ZBC_PENALTY_HIGH_BOARD if boards >= 3 else ZBC_PENALTY_LOW_BOARD
    break_risk = _clamp(
        20.0 + zbc * zbc_penalty + (15.0 if is_one_word else 0.0) + (10.0 if is_shrink else 0.0)
        + (10.0 if not has_trade_theme else 0.0) + regime_risk_mod
    )

    return {
        "name": cand.get("name"),
        "code": cand.get("code"),
        "lbc": boards,
        "open": zbc,
        "_raw": raw["raw_score"],
        "leaderFactorScore": _clamp(ign + exp + lead),
        "relayFactorScore": raw["relay_factor_score"],
        "leaderPhilosophyScore": _clamp(lead * 5.0),
        "stepContextScore": _clamp(45.0 + ladder * 4.0 + (8.0 if theme_bonus > 0 else 0.0)),
        "capacityFactorScore": cap,
        "qualityScore": _clamp(70.0 + (0.0 if is_one_word else 8.0) + (5.0 if seal_amt > 0 else 0.0) - (10.0 if zbc >= 3 else 0.0)),
        "breakRisk": break_risk,
        "tideRelayGate": tide_relay_gate,
        "hasTradeTheme": has_trade_theme,
        "isBroadOnly": is_broad_only,
        "isYizi": is_one_word,
        "isShrinkSeal": is_shrink,
        "tideAction": tide_action,
        "tideStatus": tide_status,
        "tidePhase": "traverse" if regime != "risk_off" else "ebbing",
        "themeLadderProfile": {
            "score": _clamp(ladder * 10.0),
            "gapCount": 0,
            "leaderBoards": boards,
            "hasCarry": is_dragon,
            "frontCount": ladder,
        },
        "_cand": cand,  # 回指原始候选，供后续回填
    }


# ============================================================================
# 三、faithful §4.4 四档接力/观察选择
# ============================================================================

def _tide_relay_blocked(row: dict[str, Any]) -> bool:
    action = str(row.get("tideAction") or "")
    status = str(row.get("tideStatus") or "")
    phase = str(row.get("tidePhase") or "")
    # 2026-08-18 用户「重溢价·轻大盘」：risk_off 不再一票否决接力池（降档收严由
    # tideRelayGate 数值门槛 + 各档个股硬门槛承担）；仅 AI 显式风险信号仍硬拦。
    # 开关 TIDE_BLOCK_ON_RISK_OFF=True 可回退旧行为（risk_off 清零）。
    if action == "avoid" and TIDE_BLOCK_ON_RISK_OFF:
        return True
    if status == "afterglow_risk" and TIDE_BLOCK_ON_RISK_OFF:
        return True
    if status in {"rebound_warning"}:
        return True
    if status in {"shrinking_rebound", "volume_rebound"} and phase == "ebbing":
        return True
    return False


def _relay_height_breakout_ok(row: dict[str, Any]) -> bool:
    if _tide_relay_blocked(row):
        return False
    return bool(
        (_to_num(row.get("lbc")) >= 6)
        and (_to_num(row.get("_raw")) >= 72)
        and (_to_num(row.get("leaderFactorScore")) >= 76)
        and (_to_num(row.get("relayFactorScore")) >= 70)
        and (_to_num(row.get("breakRisk")) < BREAK_RISK_MAX_HEIGHT_BREAKOUT)
        and (_to_num(row.get("stepContextScore")) >= 70)
        and (_to_num(row.get("open")) <= 2)
        and (_to_num(row.get("tideRelayGate")) >= -4)
    )


def _relay_core_ok(row: dict[str, Any]) -> bool:
    if _tide_relay_blocked(row):
        return False
    return bool(
        row.get("hasTradeTheme")
        and not row.get("isBroadOnly")
        and not row.get("isYizi")
        and not row.get("isShrinkSeal")
        and 2 <= _to_num(row.get("lbc")) <= 5
        and _to_num(row.get("_raw")) >= 60
        and _to_num(row.get("open")) < OPEN_MAX_CORE
        and _to_num(row.get("breakRisk")) < BREAK_RISK_MAX_CORE
        and _to_num(row.get("stepContextScore")) >= 38
        and _to_num(row.get("tideRelayGate")) >= 0
    )


def _relay_high_mark_ok(row: dict[str, Any]) -> bool:
    if _tide_relay_blocked(row):
        return False
    return bool(
        row.get("hasTradeTheme")
        and not row.get("isBroadOnly")
        and not row.get("isYizi")
        and not row.get("isShrinkSeal")
        and 3 <= _to_num(row.get("lbc")) <= 5
        and _to_num(row.get("_raw")) >= 74
        and _to_num(row.get("leaderFactorScore")) >= 72
        and _to_num(row.get("leaderPhilosophyScore")) >= 76
        and _to_num(row.get("breakRisk")) < BREAK_RISK_MAX_HIGH_MARK
        and _to_num(row.get("open")) <= OPEN_MAX_HIGH_MARK
        and _to_num(row.get("tideRelayGate")) >= 0
    )


def _relay_one_to_two_ok(row: dict[str, Any]) -> bool:
    if _tide_relay_blocked(row):
        return False
    return bool(
        row.get("hasTradeTheme")
        and not row.get("isBroadOnly")
        and not row.get("isYizi")
        and not row.get("isShrinkSeal")
        and _to_num(row.get("lbc")) == 1
        and _to_num(row.get("open")) < 3
        and _to_num(row.get("_raw")) >= 72
        and _to_num(row.get("stepContextScore")) >= 55
        and _to_num(row.get("leaderFactorScore")) >= 60
        and _to_num(row.get("relayFactorScore")) >= 64
        and _to_num(row.get("leaderPhilosophyScore")) >= 66
        and _to_num(row.get("capacityFactorScore")) >= 68
        and _to_num(row.get("qualityScore")) >= 80
        and _to_num(row.get("breakRisk")) < 68
        and _to_num(row.get("tideRelayGate")) >= 0
    )


def _relay_relaxed_ok(row: dict[str, Any]) -> bool:
    if _tide_relay_blocked(row):
        return False
    return bool(
        row.get("hasTradeTheme")
        and not row.get("isBroadOnly")
        and not row.get("isYizi")
        and not row.get("isShrinkSeal")
        and 2 <= _to_num(row.get("lbc")) <= 4
        and _to_num(row.get("open")) < 8
        and _to_num(row.get("stepContextScore")) >= 50
        and _to_num(row.get("breakRisk")) < 90
        and _to_num(row.get("leaderFactorScore")) >= 58
        and _to_num(row.get("capacityFactorScore")) >= 55
        and _to_num(row.get("tideRelayGate")) >= -6
    )


def _relay_broad_ok(row: dict[str, Any]) -> bool:
    return bool(
        (not _tide_relay_blocked(row))
        and not row.get("isYizi")
        and 1 <= _to_num(row.get("lbc")) <= 6
        and _to_num(row.get("open")) < 12
        and _to_num(row.get("breakRisk")) < 95
        and _to_num(row.get("_raw")) >= 55
    )


def _relay_emergency_ok(row: dict[str, Any]) -> bool:
    # 2026-08-18「轻大盘」：risk_off 不再拦 emergency 兜底档（开关回退旧行为）
    tide_ok = (str(row.get("tideAction") or "") != "avoid") or (not TIDE_BLOCK_ON_RISK_OFF)
    return bool(
        tide_ok
        and 1 <= _to_num(row.get("lbc")) <= 7
        and _to_num(row.get("open")) < 15
        and _to_num(row.get("breakRisk")) < 98
        and _to_num(row.get("_raw")) >= 45
    )


def _relay_fallback(by_raw: list[dict[str, Any]], *, selection_key=None) -> tuple[list[dict[str, Any]], str]:
    """非 strict 场景的收严级联（risk_off 降档 / ice 解冻共用）：relaxed → broad → emergency → none。
    每档取 raw_score 前 3；无候选则自然下探，全无 → ([], "none")（诚实空，不强制塞）。"""
    def _top(pred, cap=3):
        return sorted([m for m in by_raw if pred(m)],
                      key=selection_key or (lambda m: _to_num(m.get("_raw"))), reverse=True)[:cap]
    relaxed = _top(_relay_relaxed_ok)
    if relaxed:
        return relaxed, "relaxed"
    broad = _top(_relay_broad_ok)
    if broad:
        return broad, "broad"
    emg = _top(_relay_emergency_ok)
    return (emg, "emergency") if emg else ([], "none")


def _is_lone_lowboard(cand: dict[str, Any]) -> bool:
    """低板孤军（2026-10-12 用户「你推了一个没有板块的独苗！这在接力中非常不可取」·C 方案）。

    True = 低板（boards ≤ LONE_LOWBOARD_MAX）**且**孤军：
      · 行业/题材只剩自己（`sector_is_lone` / `theme_is_lone`），或
      · 无有效题材（`core_theme` ∈ {空 / "其他" / "—"}，即"没提炼出板块"）。
    ⛔ 高标（boards ≥ 3）的孤军返回 False —— 保留独立总龙头（2026-08-24 用户原意）。
    """
    _ct = str(cand.get("core_theme") or "").strip()
    lone = (bool(cand.get("sector_is_lone")) or bool(cand.get("theme_is_lone"))
            or _ct in ("", "其他", "—"))
    return _to_num(cand.get("boards"), 1) <= LONE_LOWBOARD_MAX and lone


def select_relay_watch(scored: list[dict[str, Any]], env: dict[str, Any], pulse: dict[str, Any] | None, ai: dict[str, Any] | None, *, comparison=None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str]:
    """
    faithful §4.4 四档接力选择 + 观察池生成。

    返回 (relay, watch, diagnostics, selection_mode)。
    relay/watch 元素为原始 pipeline_eod 候选（带 raw_score / relay_rank / bucket），
    不含 tactics 中间字段，供 pipeline_eod 继续回填 dim_panel/expectation/narrative。
    """
    from copy import deepcopy
    from core import leader_cycle
    scored = deepcopy(scored)
    entries = (comparison or {}).get("entries") or {}
    active = bool(leader_cycle.ENABLE_PRESELECT_STATUS and entries
                  and all(str(c.get("code")) in entries for c in scored))
    def selection_key(m):
        return (*entries[str(m.get("code"))]["key"], _to_num(m.get("_raw")))
    raw_key = selection_key if active else lambda m: _to_num(m.get("_raw"))
    # 1) 计算 raw_score + 映射 tactics 字段
    mapped: list[dict[str, Any]] = []
    raw_by_code: dict[str, dict[str, Any]] = {}
    for c in scored:
        raw = compute_raw_score(c, env, pulse, ai)
        c["raw_score"] = raw["raw_score"]          # 回写，供展示/排序
        c["quant_dims"] = raw
        raw_by_code[str(c.get("code"))] = raw
        mapped.append(map_tactics_fields(c, env, pulse, ai, raw))

    def _tier(order: list, pred, cap: int | None = None):
        out = [m for m in order if pred(m)]
        if cap is not None:
            out = out[:cap]
        return out

    def _leader_preference_key(row: dict[str, Any]) -> tuple[float, ...]:
        """龙头优先：先锁定题材第一，再在同级比较综合质量。

        此键只决定 relay/watch 的展示和执行优先级，不改变四档入池资格、AI 预案或量化权重。
        """
        cand = row.get("_cand") if isinstance(row.get("_cand"), dict) else {}
        return (
            float(bool(cand.get("is_sector_dragon"))),
            _to_num(row.get("lbc")),
            _to_num(cand.get("carry_cnt")),
            _to_num(cand.get("theme_mates_cnt")),
            _to_num(row.get("leaderFactorScore")),
            _to_num(row.get("_raw")),
        )

    # 2) 四档（严格）集合
    by_raw = sorted(mapped, key=raw_key, reverse=True)
    priority_key = selection_key if active else _leader_preference_key
    height = _tier(by_raw, _relay_height_breakout_ok)
    high = _tier(by_raw, _relay_high_mark_ok)
    core = _tier(by_raw, _relay_core_ok)
    one2 = _tier(by_raw, _relay_one_to_two_ok, cap=3)

    strict_pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in [*height, *high, *core, *one2]:
        nm = str(m.get("name") or "")
        if nm and nm not in seen:
            seen.add(nm)
            strict_pool.append(m)

    relay_mapped: list[dict[str, Any]]
    mode: str
    # P1-6 冰点 freeze（2026-08-17 用户定）→ 2026-09-09 用户「买不买由人来决定」解冻：
    # regime==ice 旧行为=不产接力池（relay 置空·全部进观察池·buy 由 signal_eval 硬拦）；
    # 新行为（ICE_FREEZE_RELAY=False）= ice 跳过 strict（不按严格主升），直接走
    # relaxed→broad→emergency 收严级联，选出多少推多少——冰点不保证有货，但绝不强制清零。
    if env.get("regime") == "ice":
        if ICE_FREEZE_RELAY:                    # 回退开关：旧 freeze 清零
            relay_mapped, mode = [], "freeze"
        else:                                   # 解冻：跳过 strict 走收严级联
            relay_mapped, mode = _relay_fallback(by_raw, selection_key=raw_key)
    elif strict_pool:
        # 三项市场反馈同时走弱才收窄接力名额；个股四档资格和排序不变。
        pressure = bool((env.get("relay_feedback") or {}).get("pressure"))
        relay_mapped = sorted(strict_pool, key=raw_key, reverse=True)[:3 if pressure else 8]
        mode = "strict_pressure" if pressure else "strict"
    else:
        relay_mapped, mode = _relay_fallback(by_raw, selection_key=raw_key)

    # 四档决定“能否入池”；池内按龙头战法聚焦第一，避免低位高分压过题材龙与空间高度。
    relay_mapped = sorted(relay_mapped, key=priority_key, reverse=True)

    # 🔒 低板孤军过滤（2026-10-12 用户「接力不推没板块的独苗」·C 方案）：
    #   低板（boards ≤ LONE_LOWBOARD_MAX）且孤军 → 移出接力池（下方 593 行自动回落观察池）。
    #   孤军 = 行业/题材只剩自己（sector_is_lone/theme_is_lone）**或** 无有效题材
    #   （core_theme ∈ {空/其他/—}，即"没提炼出板块"）。
    #   ⛔ 高标独立龙头（boards≥3 的孤军）保留 —— 2026-08-24 用户「认可独立总龙头」原意。
    if ENABLE_LONE_LOWBOARD_EXCLUDE:
        relay_mapped = [
            m for m in relay_mapped
            if not _is_lone_lowboard(m.get("_cand") if isinstance(m.get("_cand"), dict) else {})
        ]

    relay_codes = {str(m.get("code")) for m in relay_mapped}

    # 3) 观察池：排除接力，按 raw_score 排序，容量/高标分组，最多 8
    watch_cands = [m for m in by_raw if str(m.get("code")) not in relay_codes]
    # 梯队必须观察（题材龙一若未进接力，纳入）——保留 spec watch 完整性
    watch_sorted = sorted(watch_cands, key=priority_key, reverse=True)
    watch_mapped = watch_sorted[:8]

    # 4) 还原为原始候选并附 rank/bucket
    def _restore(m: dict[str, Any]) -> dict[str, Any]:
        c = dict(m.get("_cand") or {})
        c["raw_score"] = m.get("_raw")
        c["quant_dims"] = raw_by_code.get(str(c.get("code")), {})
        if active:
            evidence = entries[str(c.get("code"))]
            for key in ("leader_role", "leader_evidence", "core_theme"):
                c[key] = deepcopy(evidence[key])
            c["preselect_evidence"] = deepcopy(evidence)
        return c

    relay = [_restore(m) for m in relay_mapped]
    watch = [_restore(m) for m in watch_mapped]

    for idx, it in enumerate(relay, start=1):
        it["relay_rank"] = idx
        it["leader_preference_rank"] = idx
        it["leader_preference_note"] = "板块龙→连板高度→带动性→题材广度→综合分"
        it["relay_selection_mode"] = mode
        it["is_broad_fallback"] = mode in ("broad", "emergency")
        it["bucket"] = "relay"
        it["bucket_label"] = "接力池"
        # 命中的 §4.4 标签（用于卡片 reason_text）
        hits = []
        if _relay_height_breakout_ok(next(x for x in mapped if x.get("code") == it.get("code"))):
            hits.append("高度突破")
        if _relay_high_mark_ok(next(x for x in mapped if x.get("code") == it.get("code"))):
            hits.append("高标龙头")
        if _relay_core_ok(next(x for x in mapped if x.get("code") == it.get("code"))):
            hits.append("主线接力")
        if _relay_one_to_two_ok(next(x for x in mapped if x.get("code") == it.get("code"))):
            hits.append("1进2")
        it["hit_rules"] = hits
    for idx, it in enumerate(watch, start=1):
        it["watch_rank"] = idx
        it["leader_preference_rank"] = idx
        it["leader_preference_note"] = "板块龙→连板高度→带动性→题材广度→综合分"
        it["bucket"] = "watch"
        it["bucket_label"] = "观察池"
        style = it.get("style_tag")
        # P2-9 分池校准（2026-08-17 用户"情绪核心"战法）：高辨识度高标(≥3板)优先归
        # 「高标/题材核心」，再容量核心，最后补充观察。旧逻辑 style_tag==容量 优先，
        # 把 5 板空间龙(蓝盾 20cm)归入容量核心埋没辨识度——情绪核心下辨识度 > 容量标签。
        if _to_num(it.get("boards"), 1) >= 3:
            it["watch_group"] = "高标/题材核心"
        elif style == "容量":
            it["watch_group"] = "容量核心"
        else:
            it["watch_group"] = "补充观察"

    diagnostics = {
        "scored": len(scored),
        "strict_pool": len(strict_pool),
        "height_breakout_eligible": sum(1 for m in mapped if _relay_height_breakout_ok(m)),
        "high_mark_eligible": sum(1 for m in mapped if _relay_high_mark_ok(m)),
        "core_eligible": sum(1 for m in mapped if _relay_core_ok(m)),
        "one_to_two_eligible": sum(1 for m in mapped if _relay_one_to_two_ok(m)),
        "relay_count": len(relay),
        "watch_count": len(watch),
        "selection_mode": mode,
        "relay_feedback": env.get("relay_feedback"),
        "mean_raw_score": round(sum(_to_num(c.get("raw_score")) for c in scored) / max(1, len(scored)), 2),
    }
    if active:
        old_relay, old_watch, _, old_mode = select_relay_watch(scored, env, pulse, ai)
        old_positions = {c["code"]: (bucket, i) for bucket, rs in (("relay", old_relay), ("watch", old_watch))
                         for i, c in enumerate(rs, 1)}
        new_positions = {c["code"]: (bucket, i) for bucket, rs in (("relay", relay), ("watch", watch))
                         for i, c in enumerate(rs, 1)}
        predicates = {"height": _relay_height_breakout_ok, "high": _relay_high_mark_ok,
                      "core": _relay_core_ok, "one2": _relay_one_to_two_ok,
                      "relaxed": _relay_relaxed_ok, "broad": _relay_broad_ok, "emergency": _relay_emergency_ok}
        diagnostics["preselect_status"] = {"asof": comparison["asof"], "cutoff": comparison["cutoff"],
            "quality": comparison["quality"], "old_mode": old_mode,
            "rows": [{"code": m["code"], "raw": m["_raw"], "evidence": entries[m["code"]],
                      "eligible": {k: bool(pred(m)) for k, pred in predicates.items()},
                      "old_position": old_positions.get(m["code"]),
                      "new_position": new_positions.get(m["code"]),
                      "selection_rank": next(i for i, row in enumerate(by_raw, 1) if row["code"] == m["code"]),
                      "one2_rank": next((i for i, row in enumerate([r for r in by_raw if _relay_one_to_two_ok(r)], 1)
                                         if row["code"] == m["code"]), None),
                      "watch_rank_before_cap": next((i for i, row in enumerate(watch_sorted, 1)
                                                     if row["code"] == m["code"]), None),
                      "outcome": "selected" if m["code"] in new_positions else "watch_capacity_cutoff"}
                     for m in mapped]}
        for c in relay + watch:
            c["leader_preference_note"] = "当前角色→带动与连续性→已核实历史地位→竞争→原始分"
    return relay, watch, diagnostics, mode
