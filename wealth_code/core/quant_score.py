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

import os
from typing import Any

from core import front_core as _front_core

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

# 🔓 三档严格判据的 tide 门槛（2026-11-04 用户「放宽」）。
# ⛔ 原为字面量 `>= 0`，而 `tide_relay_gate` 恒等于
#    {"risk_on": 4.0, "neutral": 0.0, "risk_off": -4.0}[regime] ——
#    即 **risk_off 日该值永远是 -4.0，三档从数学上不可能命中任何票**，
#    与上面 2026-08-18「risk_off 只做档位收严、不一票否决」的原意矛盾。
#    实证 11-04：全市场最高 3 板（无 6 板 → 高度突破档必然空），
#    6 只 3 板里紫竹/闽发/高争/大晟/浙文全被这一条卡掉，最终 relay=0。
# ✅ 放到 -4（与高度突破档同值）：risk_off 仍比 neutral 严一档，
#    但不再一票否决 —— 个股层还有 raw/leaderFactor/breakRisk 等各自的硬门槛兜底。
# ⛔ 可逆：ENABLE_TIDE_GATE_RELAX=0 → 逐字回到 `>= 0`。
TIDE_GATE_STRICT_FLOOR = (-4.0
                          if (os.environ.get("ENABLE_TIDE_GATE_RELAX") or "1").strip().lower()
                          not in {"0", "false", "off", "no", ""} else 0.0)
# ============================================================================
# 🔒 低板孤军不进接力池（2026-10-12 用户「你推了一个没有板块的独苗！这在接力中
#    非常不可取」·C 方案）：低板（boards ≤ LONE_LOWBOARD_MAX）且孤军
#    （行业/题材只剩自己，或 core_theme 无有效题材 {空/其他/—}）→ 移出接力池
#    （line 593 自动回落观察池）。
# ⛔ 高标独立龙头（boards ≥ 3 的孤军）**保留**——2026-08-24 用户原意「认可独立总龙头」。
# ⛔ 可逆：ENABLE_LONE_LOWBOARD_EXCLUDE=False → 逐字回到改动前（孤军照进接力池）。
ENABLE_LONE_LOWBOARD_EXCLUDE = True
LONE_LOWBOARD_MAX = 2

# 🔢 排序用打分（2026-11-04 用户「平起平坐没问题，但是在排序的时候尽量做好打分这块，
#    按照打分来排序」）。
# ⛔ 原排序键里 **板数不在有效位、综合分排最后**，实测 11-03：4 板东方海洋排第 7、
#    raw 最低（63.7）的圣阳股份排第 1 —— 那不是「按打分」，是「按带动 + 连续天数」。
# ✅ 现口径：角色 → 是否板块龙一 → **板数高** → **综合分高** → 带动 → 其余。
#    主序仍是龙头优先（「接力票必须是龙头」铁律不动），打分只在**同角色同板数**内定先后。
# ⛔ 可逆：ENABLE_SCORE_PRIORITY_SORT=0 → 逐字回到旧键。
_SCORE_SORT = (os.environ.get("ENABLE_SCORE_PRIORITY_SORT") or "1").strip().lower() not in {
    "0", "false", "off", "no", ""
}

# 🔢 观察池容量（2026-11-04 用户「扩容」）。
# ⛔ 原为硬编码 `watch_sorted[:8]`。实证 11-04：涨停 119 只的日子，
#    龙版传媒（3 板·短剧 6 家·成交额达标）排 `selection_rank=11`，
#    被判 `watch_capacity_cutoff` 截掉 —— 而它前一天就在观察池里、当天刚连板。
#    「观察池」的本义是把值得盯的票留在视野里，池子小到把连续晋级票挤出去，就失去意义。
# ✅ 默认 12（8 → 12）。`WATCH_POOL_CAP=8` 回退旧容量。
def _watch_cap() -> int:
    try:
        return max(1, int(os.environ.get("WATCH_POOL_CAP") or 12))
    except (TypeError, ValueError):
        return 12


# 🔓 观察池准入放宽（2026-11-04 用户「观察池现在都是准龙头」）。
# ⛔ 原判据只认 `two_pool.watch_kind()` 的两类锚点（一字核心 / 容量核心），
#    把既有的 `高标/题材核心`、`补充观察` 两条路径一并废掉 ——
#    实证 11-04：龙版传媒（3板·短剧6家·昨日已在池）被判「不符合两类观察资格」删掉。
# ✅ 放宽为「`watch_kind` 命中 **或** 角色是龙头/准龙头」→ 三档分组逻辑重新生效。
# ⛔ `ENABLE_WATCH_WIDEN=0` 逐字回到旧行为（只认两类锚点）。
_WATCH_WIDEN = (os.environ.get("ENABLE_WATCH_WIDEN") or "1").strip().lower() not in {
    "0", "false", "off", "no", ""
}

# 🎯 2 板及以上**全部**进观察池（2026-11-04 用户）。
# 用户原话：「从 2 进 3 开始仔细观察，相当于 2 进 3 以上的票全部都要进观察池…
#            首板这边的票，有选择性地进观察池。」
# ⛔ 这条**优先于** `watch_kind` 角色判据：2 板以上的票不再要求「是龙头」才可见。
#    理由：首板看不出东西（用户原话），2 进 3 才是真正要盯的位置；
#    把它们挡在池外等于把观察池变成「龙一专榜」，看不到同身位的其他票。
# ⛔ 只保留**硬性无交易权限/必排**两类例外：688（无权限）、ST。
# ⛔ 全部进 = **不占 `WATCH_POOL_CAP` 名额**（容量只约束首板那批），
#    否则涨停多的时候「全部都要进」会被容量悄悄截断（历史 2 板+ 最多 16 只）。
# ⛔ `ENABLE_WATCH_ALL_MULTI_BOARD=0` 回退（回到只认角色/一类锚点的旧口径）。
_WATCH_ALL_MULTI = (os.environ.get("ENABLE_WATCH_ALL_MULTI_BOARD") or "1").strip().lower() not in {
    "0", "false", "off", "no", ""
}


def _watch_hard_excluded(cand: dict[str, Any]) -> bool:
    """观察池硬性排除：无交易权限 / 必排标的。⛔ 这两类不因任何放宽规则进入。"""
    code = str(cand.get("code") or "")
    name = str(cand.get("name") or "").upper()
    return code.startswith("688") or "ST" in name


# 🔁 昨日在池且今日晋级者优先占位（2026-11-04 用户「b 占位」）。
# 逻辑：昨天我已经在盯它，今天它晋级了 → 该继续盯，而不是被新票挤出视野。
# ⛔ 只影响观察池**排序先后**，不改入池资格（没资格的票不会因此进来）。
#    `ENABLE_WATCH_CARRYOVER=0` 回退。
_WATCH_CARRYOVER = (os.environ.get("ENABLE_WATCH_CARRYOVER") or "1").strip().lower() not in {
    "0", "false", "off", "no", ""
}
# ⛔ 落盘口径说明必须与实际排序键同源（两处调同一函数，禁止各写一份）
# ⛔ 刻意做成**函数**而非模块级常量：常量在 import 时固化，测试要验证开关切换
#    就只能 `importlib.reload`，而 reload 会让其他模块已持有的引用失效
#    （2026-11-04 实测污染 `test_snapshot_guard` 4 例：TypeError）。
def sort_note() -> str:
    """排序口径说明（随 `ENABLE_SCORE_PRIORITY_SORT` 变化）。"""
    return ("板块龙→板数→综合分→带动性→题材广度" if _SCORE_SORT
            else "板块龙→连板高度→带动性→题材广度→综合分")
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
# P2 · 新方向首板独立观察槽（2026-10-18 用户「按顺序处理」）
# ----------------------------------------------------------------------------
# 【治什么】用户 10-16 原话：「真正的高度要看冰山冷热4进5、新华文轩3进4；新方向里
#   军工的长城军工、低空的万丰奥威与金盾股份是最强的首板接棒候选」——但选股里
#   首板只能靠 relay 的 relaxed/broad 兜底、或在 watch 8 位里跟题材内票抢位置
#   （10-16 万丰奥威排第 9 被 watch 容量截断，直接消失）。
#   首板 ≠ 能进接力池（1进2 有容量/raw 硬门槛，不放松），但**「新方向刚冒头」需要
#   一个不被容量挤掉的固定观察位**。
#
# 【做法】relay / watch 之外**独立第三块** `new_direction_first_board`：
#   · boards == 1（首板）且未进 relay/watch
#   · core_theme 有效（非空/其他/—，即真提炼出了方向）
#   · 该题材今日涨停家数 ≤ NEW_DIRECTION_MAX_THEME_MATES（尚未成梯队 = 还是"方向"）
#   · 按 raw_score 取前 NEW_DIRECTION_FIRST_BOARD_SLOTS 只
#   ⛔ 不占接力名额、不改 relay/watch 任何一位、不参与买入裁决（纯观察）。
# ⛔ 可逆：ENABLE_NEW_DIRECTION_FIRST_BOARD=0 → 关闭（旧行为，无第三块）。
# ============================================================================
NEW_DIRECTION_FIRST_BOARD_SLOTS = 3
NEW_DIRECTION_MAX_THEME_MATES = 3
_INVALID_THEMES = {"", "其他", "—", "-", "无", "未知"}
# ============================================================================


def new_direction_first_board_enabled() -> bool:
    """P2 新方向首板观察槽开关（默认开；ENABLE_NEW_DIRECTION_FIRST_BOARD=0 回旧行为）。"""
    return os.environ.get("ENABLE_NEW_DIRECTION_FIRST_BOARD", "1") not in ("0", "false", "False", "")


def _theme_mate_count(cand: dict[str, Any]) -> int | None:
    """题材内涨停家数（优先 theme_mates_cnt，兜底 sector_mates_cnt）；无数据 → None。"""
    for k in ("theme_mates_cnt", "sector_mates_cnt"):
        v = cand.get(k)
        if v is None or v == "":
            continue
        try:
            return int(float(v))
        except (TypeError, ValueError):
            continue
    return None


def _is_new_direction(cand: dict[str, Any], max_theme_mates: int) -> bool:
    """「新方向」判据：题材尚未成梯队（家数少）或梯队健康度低。

    ⛔ 无数据时不猜——家数与梯队健康度都缺 → 判 False（不编造"新方向"）。
    """
    mates = _theme_mate_count(cand)
    if mates is not None:
        return mates <= max_theme_mates
    lh = cand.get("ladder_health")
    if lh is None or lh == "":
        return False
    return _to_num(lh, 0.0) <= 3.0


def select_new_direction_first_board(
    candidates: list[dict[str, Any]],
    *,
    exclude_codes: set[str] | None = None,
    slots: int = NEW_DIRECTION_FIRST_BOARD_SLOTS,
    max_theme_mates: int = NEW_DIRECTION_MAX_THEME_MATES,
) -> list[dict[str, Any]]:
    """新方向首板独立观察槽（P2 · 纯观察·不占接力名额）。

    入选：boards==1 ∧ 未进 relay/watch ∧ core_theme 有效 ∧ 题材未成梯队。
    排序：raw_score desc；截 slots。

    返回的是**入参行的浅引用**（不回写、不改 relay/watch 任何一位）；调用方自行加
    bucket/label 标记。⛔ 本名单只解决"新方向首板看不见"，不构成买入依据。
    """
    ex = {str(c) for c in (exclude_codes or set())}
    picked: list[dict[str, Any]] = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        code = str(c.get("code") or "")
        name = str(c.get("name") or "")
        if not code or not name or code in ex:
            continue
        if code.startswith("688") or "ST" in name.upper():
            continue
        if _to_num(c.get("boards"), 1) != 1:
            continue
        if str(c.get("core_theme") or "").strip() in _INVALID_THEMES:
            continue
        if not _is_new_direction(c, max_theme_mates):
            continue
        picked.append(c)

    def _score(r: dict[str, Any]) -> float:
        v = r.get("raw_score")
        return _to_num(v if v is not None else r.get("_raw"))

    picked.sort(key=_score, reverse=True)
    return picked[:max(0, int(slots))]


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

    # —— 9.6) 前排核心分（2026-10-21 用户贴 92科比 定调·`core/front_core`）——
    # 补的正是系统原先「只在卡面展示、没进选股」的三块：筹码结构、主力资金回流、封板加速。
    # ⛔ 上限 ±6，小于高度档（+8）与历史晋级（±7）：同高度里比谁更靠前，不许顶掉空间龙。
    front_core = _front_core.score(cand)
    front_core_bonus = _to_num(front_core.get("bonus"), 0.0)

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
        weighted + identity_edge + height_breakout_bonus + historical_relay_factor
        + front_core_bonus + market_gate_adjust + tide_adjust
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
        # 前排核心（92科比 口径）·子分留痕供卡面/复盘解释，⛔ 不进四档资格
        "front_core_score": _to_num(front_core.get("score"), 50.0),
        "front_core_bonus": round(front_core_bonus, 2),
        "front_core_subs": front_core.get("subs") or {},
        "front_core_facts": front_core.get("facts") or {},
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
        and _to_num(row.get("tideRelayGate")) >= TIDE_GATE_STRICT_FLOOR
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
        and _to_num(row.get("tideRelayGate")) >= TIDE_GATE_STRICT_FLOOR
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
        and _to_num(row.get("tideRelayGate")) >= TIDE_GATE_STRICT_FLOOR
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


def _is_carryover_promoted(m: dict[str, Any], prev_pool_boards: dict[str, int]) -> bool:
    """该票是否「昨日已在池 ∧ 今日板数更高」。

    ⛔ 只在 `prev_pool_boards` 有数据时调用（调用方已判空）。
    ⛔ 板数取今日 `lbc`/`boards`，缺失按 0 —— 不猜，不晋级就不标记。
    ⛔ 不要求「必须是首发入池理由相同」：昨日在池这件事本身已证明它在视野内。
    """
    cand = m.get("_cand") if isinstance(m.get("_cand"), dict) else {}
    code = str(cand.get("code") or m.get("code") or "")
    if not code or code not in prev_pool_boards:
        return False
    today = _to_num(cand.get("boards") or m.get("lbc"))
    return today > _to_num(prev_pool_boards.get(code))


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


def select_relay_watch(scored: list[dict[str, Any]], env: dict[str, Any], pulse: dict[str, Any] | None, ai: dict[str, Any] | None, *, comparison=None, first_board_out: list[dict[str, Any]] | None = None, prev_pool_boards: dict[str, int] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str]:
    """
    faithful §4.4 四档接力选择 + 观察池生成。

    返回 (relay, watch, diagnostics, selection_mode)。
    relay/watch 元素为原始 pipeline_eod 候选（带 raw_score / relay_rank / bucket），
    不含 tactics 中间字段，供 pipeline_eod 继续回填 dim_panel/expectation/narrative。

    `first_board_out`（P2·2026-10-18）：传入 list 则**额外**填充「新方向首板独立观察槽」，
    ⛔ 不占 relay/watch 任何名额、不改变二者内容；不传 = 旧行为（无第三块）。

    `prev_pool_boards`（2026-11-04 用户「b 占位」）：`{code: 昨日板数}`，
    来自**上一交易日**的 `candidates-<D>.json` 的 relay+watch。
    传入时，观察池里「昨日已在池 ∧ 今日板数更高」的票会被提到前面优先占位
    （⛔ 只调顺序，不改资格）。不传 = 旧行为。
    """
    from copy import deepcopy
    from core import leader_cycle
    scored = deepcopy(scored)
    entries = (comparison or {}).get("entries") or {}
    active = bool(leader_cycle.ENABLE_PRESELECT_STATUS and entries
                  and all(str(c.get("code")) in entries for c in scored))
    core_enabled = os.environ.get("ENABLE_CORE_BEFORE_CUTOFF", "1") not in ("0", "false", "False", "")
    core_active = bool(active and core_enabled)

    def selection_key(m):
        old_key = entries[str(m.get("code"))]["key"]
        if core_active:
            cand = m.get("_cand") or {}
            confirmed_dragon = (cand.get("is_sector_dragon") is True
                                and ("dragon_rank" not in cand or
                                     (not isinstance(cand["dragon_rank"], bool)
                                      and _to_num(cand["dragon_rank"], 0) == 1)))
            from core import two_pool
            rate_key = (two_pool.promotion_rate(cand),) if two_pool.enabled() else ()
            if _SCORE_SORT:
                # 🔢 2026-11-04 用户「按照打分来排序」。
                # ⛔ 原键 = (角色, 龙一, 带动, 连续天数, 历史最高板, 联动, 竞争, -线内排名, 晋级率, raw)
                #    —— **板数不在有效位、综合分排最后**，实测 11-03：4 板东方海洋排第 7、
                #    raw 最低（63.7）的圣阳股份排第 1。这不是「按打分排序」，是「按带动+连续天数」。
                # ✅ 现键 = (角色, 龙一, **板数**, **综合分**, 带动, 连续天数, 历史, 联动, 竞争, -线内排名, 晋级率)
                #    —— 主序仍是「角色 → 是否板块龙一」（龙头铁律不动），
                #    之后**先比板数、再比打分**，与 `_leader_preference_key` 的口径对齐。
                # ⛔⛔ 符号方向：本函数被 `sorted(..., reverse=True)` 使用 → **key 越大越优先**。
                #     → 板数与分数都必须取**正值**（大在前）。写成 `-boards` 会变成「板数低的优先」、
                #       `-raw` 会变成「分数低的优先」——2026-11-04 第一版就这么写错了，
                #       被 `test_unconfirmed_or_conflicting_rank_not_preferred` 当场抓出
                #       （raw 最低的 000013 反而被顶进接力池）。
                return (old_key[0], int(confirmed_dragon),
                        _to_num(cand.get("boards") or m.get("lbc")),
                        _to_num(m.get("_raw")),
                        *old_key[1:], *rate_key)
            return (old_key[0], int(confirmed_dragon), *old_key[1:], *rate_key, _to_num(m.get("_raw")))
        if _SCORE_SORT:
            # 非 active 分支同样插入板数与分数（原键末尾只有 raw，板数在 `_leader_preference_key` 里）
            # ⛔ 同一条符号纪律：reverse=True → 取正值。
            return (old_key[0], _to_num(m.get("lbc")), _to_num(m.get("_raw")), *old_key[1:])
        return (*old_key, _to_num(m.get("_raw")))
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

        P3（2026-10-18）：第二位插入 `review_bonus`（复盘推演倾斜 ±1.2）。
        ⛔ 刻意放在"板块龙"之后：复盘只能调整**同一识别度层级内**的先后，
           不能把复盘点赞的跟风票顶到板块龙前面，也不能改 raw_score / 入池资格。
        """
        cand = row.get("_cand") if isinstance(row.get("_cand"), dict) else {}
        if _SCORE_SORT:
            # 🔢 2026-11-04：把综合分从**最后一位**提到板数之后。
            #    原序 = 板块龙 → 复盘倾斜 → 板数 → 带动 → 题材广度 → 龙头因子 → 综合分
            #    —— 同板数内先比带动、再比题材广度，分数几乎不起作用。
            #    现序 = 板块龙 → 复盘倾斜 → 板数 → **综合分** → 带动 → 题材广度 → 龙头因子
            #    ⛔ 板块龙/复盘/板数三位一个字没动（「接力票必须是龙头」铁律）。
            return (
                float(bool(cand.get("is_sector_dragon"))),
                _to_num(cand.get("review_bonus")),
                _to_num(row.get("lbc")),
                _to_num(row.get("_raw")),
                _to_num(cand.get("carry_cnt")),
                _to_num(cand.get("theme_mates_cnt")),
                _to_num(row.get("leaderFactorScore")),
            )
        return (
            float(bool(cand.get("is_sector_dragon"))),
            _to_num(cand.get("review_bonus")),
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
    from core import two_pool
    if two_pool.enabled():
        # ⛔ 2026-11-04 时序 bug（实证「龙版传媒连板了却不在观察池」）：
        #    原实现直接拿 `m["_cand"]` 判 `watch_kind`，但**此时 leader_role 还没注入**
        #    （注入发生在下面的 `_restore`，从 `entries` 取）→ `core` 判据恒 False
        #    → `watch_kind` 返回 None → **几乎全部候选被这条过滤误杀**。
        #    实证 11-04：龙版传媒 3 板·短剧 6 家·昨日就在池里，`_cand.leader_role=None`
        #    但 `entries[605577].leader_role = "准龙头"`，只因判早了就被丢掉。
        # ✅ 先按 `entries` 补 leader_role/core_theme 再判，判据才拿得到真实角色。
        _wc: list[dict[str, Any]] = []
        for m in watch_cands:
            cand = dict(m.get("_cand") or {})
            ev = entries.get(str(m.get("code"))) if active else None
            if isinstance(ev, dict):
                if not cand.get("leader_role"):
                    cand["leader_role"] = ev.get("leader_role")
                if not cand.get("core_theme"):
                    cand["core_theme"] = ev.get("core_theme")
                # `is_sector_dragon` 由更晚的 `_attach_sector_ladder` 判定；
                # 这里用「角色是龙头/准龙头」等价兜底，避免因字段未生成而误杀。
                if cand.get("is_sector_dragon") is None and ev.get("leader_role") in {"龙头", "准龙头"}:
                    cand["leader_role"] = ev.get("leader_role")
            # ⛔ 2026-11-04 用户「观察池现在都是准龙头」（其实反了：是靠 watch_kind 进来的
            #    只剩一字/容量两类，普通龙头全被挡）。原判据只认 `watch_kind` 两类锚点，
            #    把 `高标/题材核心`、`补充观察` 两条既有路径一并废掉。
            # ✅ 放宽为：`watch_kind` 命中 **或** 角色是龙头/准龙头 → 进观察池候选，
            #    分组仍由下方既有逻辑决定（≥3板→高标/题材核心，容量→容量核心，其余→补充观察）。
            #    ⛔ 只放宽「能不能进观察池」，买方资格仍由 `buyable=False` 与后续闸门把守。
            _role = str(cand.get("leader_role") or "")
            _b = _to_num(cand.get("boards") or m.get("lbc"))
            # 🎯 2 板及以上 → **无条件进观察池**（用户「2 进 3 以上的票全部都要进」）。
            #    只挡 688/ST 这类无交易权限的。⛔ 不要求龙头身份 —— 那正是本轮要放开的。
            if _WATCH_ALL_MULTI and _b >= 2 and not _watch_hard_excluded(cand):
                m["_multi_board"] = True
                _wc.append(m)
                continue
            if _watch_hard_excluded(cand):
                continue
            _hit = two_pool.watch_kind(cand) or (
                _WATCH_WIDEN and _role in {"龙头", "准龙头"})
            if _hit:
                _wc.append(m)
        watch_cands = _wc
    watch_sorted = sorted(watch_cands, key=priority_key, reverse=True)

    # 🔁 昨日在池且今日晋级 → 优先占位（2026-11-04 用户「b 占位」）。
    # ⛔ 用 `sorted` 的稳定排序做**二次分层**：先按 carryover 标记降序，再保留原 priority 顺序。
    #    ⛔ 绝不改变入池资格 —— 没通过上层筛选的票不会因为这条进来。
    if _WATCH_CARRYOVER and prev_pool_boards:
        watch_sorted = sorted(
            watch_sorted,
            key=lambda m: 1 if _is_carryover_promoted(m, prev_pool_boards) else 0,
            reverse=True,
        )
        for m in watch_sorted:
            if _is_carryover_promoted(m, prev_pool_boards):
                m["_carryover_promoted"] = True

    # 🎯 2026-11-04 用户「2 进 3 以上的票全部都要进观察池；首板有选择性地进」：
    #    **多板票不占容量名额** —— 容量只截断首板那批。
    #    ⛔ 若统一 `[:cap]` 截断，涨停 100+ 的日子 2 板以上会被首板高分票挤出去，
    #      「全部都要进」就成了一句空话（历史 2 板+ 中位 11 / 最多 16 > 旧容量 8）。
    #    ⛔ 多板内部自己排：**板数降序 → 原 priority**（身位优先，同板数仍龙头优先）。
    if _WATCH_ALL_MULTI:
        _multi = [m for m in watch_sorted if m.get("_multi_board")]
        _first = [m for m in watch_sorted if not m.get("_multi_board")]
        # 稳定二次排序：先按板数降序（板数是这一档的主变量）
        _multi = sorted(
            _multi,
            key=lambda m: _to_num((m.get("_cand") or {}).get("boards") or m.get("lbc")),
            reverse=True,
        )
        watch_mapped = _multi + _first[:_watch_cap()]
    else:
        watch_mapped = watch_sorted[:_watch_cap()]

    # 4) 还原为原始候选并附 rank/bucket
    def _restore(m: dict[str, Any]) -> dict[str, Any]:
        c = dict(m.get("_cand") or {})
        c["raw_score"] = m.get("_raw")
        # ⛔ `_carryover_promoted` 标在 `m` 上、不在 `_cand` 里 ——
        #    不手动带过来，占位标记会在还原时丢掉（页面/诊断都看不到「它为什么排前面」）。
        #    ⚠️ 落盘字段去掉前导下划线（`_` 是模块内部标记的惯例，对外字段不该带，
        #       前端 lint 也会把 dangling underscore 判成警告）。
        if m.get("_carryover_promoted"):
            c["carryover_promoted"] = True
        c["quant_dims"] = raw_by_code.get(str(c.get("code")), {})
        if active:
            evidence = entries[str(c.get("code"))]
            for key in ("leader_role", "leader_evidence", "core_theme"):
                c[key] = deepcopy(evidence[key])
            c["preselect_evidence"] = deepcopy(evidence)
        return c

    relay = [_restore(m) for m in relay_mapped]
    watch = [_restore(m) for m in watch_mapped]

    # P2（2026-10-18 用户「按顺序处理」）：**新方向首板独立观察槽**——relay/watch 之外第三块。
    # 治「首板只能靠 relaxed/broad 兜底、或在 watch 8 位里被题材内票挤掉」
    # （实证 10-16：万丰奥威 watch 排第 9 被容量截断 → 复盘点名、选股消失）。
    # ⛔ 只填外部容器，relay/watch 一位不动、不占名额、不参与买入裁决。
    from core import two_pool
    if first_board_out is not None and new_direction_first_board_enabled() and not two_pool.enabled():
        _used_codes = {str(x.get("code") or "") for x in (relay + watch)}
        _first_board = select_new_direction_first_board(
            [_restore(m) for m in by_raw], exclude_codes=_used_codes
        )
        for _i, _row in enumerate(_first_board, start=1):
            _row["bucket"] = "new_direction"
            _row["bucket_label"] = "新方向首板"
            _row["new_direction_rank"] = _i
            _row["new_direction_note"] = "首板·题材未成梯队（独立观察槽·不占接力名额）"
        first_board_out.extend(_first_board)

    for idx, it in enumerate(relay, start=1):
        it["relay_rank"] = idx
        it["leader_preference_rank"] = idx
        it["leader_preference_note"] = sort_note()
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
        it["leader_preference_note"] = sort_note()
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
        # P2（2026-10-18）：新方向首板独立观察槽条数（不占 relay/watch 名额）
        "new_direction_first_board_count": len(first_board_out or []),
        "selection_mode": mode,
        "relay_feedback": env.get("relay_feedback"),
        "mean_raw_score": round(sum(_to_num(c.get("raw_score")) for c in scored) / max(1, len(scored)), 2),
    }
    diagnostics["core_before_cutoff"] = {
        "enabled": core_enabled,
        "active": core_active,
        "priority_rule": ("旧key[0]角色→已确认题材龙一→旧key[1:]→raw"
                          if core_active else "沿用旧排序"),
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
    if two_pool.enabled():
        relay, watch, pool_diag = two_pool.finalize(relay, watch, stage="select_relay_watch")
        for i, r in enumerate(relay, 1):
            r["relay_rank"] = i
        selected = {str(r.get("code")) for r in relay + watch}
        pool_diag["excluded"] = [{"code": c.get("code"), "name": c.get("name"),
                                  "reason": "未通过既有接力资格或不符合两类观察资格"}
                                 for c in scored if str(c.get("code")) not in selected]
        diagnostics["two_pool_selection"] = pool_diag
        diagnostics.update(relay_count=len(relay), watch_count=len(watch))
    # 仅在资格与名单定稿后补展示理由，避免理由被误当作闸门降级证据。
    for it in watch:
        if it.get("watch_reason"):
            continue
        group = it.get("watch_group")
        if group == "高标/题材核心":
            it["watch_reason"] = "多板持续观察，跟踪连板延续与题材强弱；不参与"
        elif group == "容量核心":
            it["watch_reason"] = "容量观察，跟踪板块资金态度；不参与"
        elif it.get("leader_role") in {"龙头", "准龙头"}:
            it["watch_reason"] = f"{it['leader_role']}补充观察，跟踪题材地位变化；不参与"
        else:
            it["watch_reason"] = "补充观察，跟踪后续表现；不参与"
    return relay, watch, diagnostics, mode
