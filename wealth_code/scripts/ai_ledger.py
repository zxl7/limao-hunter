#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 判断平行台账（2026-09-10 用户「把我的思考记到真实台账·和 Python 脚本轨对比」）。

背景（2026-09-14 更新·用户「全量以 AI 为唯一口径！」）：
  · **账户账本** `account_nav_history.jsonl` 的买入口径已切到 AI 决策
    （见 `core/ai_caliber.py`：成交 = AI 判 participate ∧ 竞价涨幅 ≤ AI 自定 entry_max_gap
     ∧ 有竞价数据），机械 signal_eval 降级为**参考信号**（卡面照常渲染，不再决定买卖与战绩）。
  · 本轨 `ai_nav_history.jsonl` 因而成为 **AI 口径的独立计分本**：同口径、同算法
    （T+1 竞价价买入 → T+1 收盘价结算·等权·nav 递推），但起点独立 1.0——
    账户账本含 09-14 之前的机械口径历史（14 个交易日），无法拆出「AI 判断本身」的成绩，
    故保留本轨单列。⛔ 两轨每日成交集合同源，数字应逐日一致（仅起点不同）；不一致即为 bug。

文件（全部 append-only，禁覆盖）：
  wealth_code/cache/ai_decisions.jsonl   AI 每日决策（decision_date=T 盘后 · trade_date=T+1 执行）
  wealth_code/cache/ai_nav_history.jsonl AI 轨账本（字段与 Python 轨同构 + strategy 区分）

口径（与 pipeline_verify 同源）：
  return_pct = (T+1 收盘 − T+1 竞价成交价) / T+1 竞价成交价 × 100
  avg_return_pct = 当日实际成交票等权均值；nav_t = nav_{t-1} × (1 + avg/100)
  入场规则由 AI 决策自带（entry_max_gap，默认 ≤3% 才参与、超则放弃）；竞价价缺失→跳过（诚实）
  ⛔ 0 成交日**不写账本行**（与账户账本同语义：账本只记有实际买入的交易日）

🛡️ 交易闸门留痕（2026-09-23·宪法【🛡️ 交易闸门三条】）：picks 可选带
  `target_pct` / `stop_pct` / `hold_horizon` → **原样**透传到决策台账与 ai_view；
  赔率 < 2 或周期标签非法 → 只追加 `payoff_warning` / `hold_horizon_warning`。
  ⛔ 纯留痕：缺失即 None、不校验、不抛错、不阻断落盘，⛔ 不改 bias/view、不参与裁决。

用法：
  python3 wealth_code/scripts/ai_ledger.py record --decision-date 2026-09-10 --file /tmp/ai_picks.json
  python3 wealth_code/scripts/ai_ledger.py settle --trade-date 2026-09-11
  python3 wealth_code/scripts/ai_ledger.py status
  python3 wealth_code/scripts/ai_ledger.py compare --days 30
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
BASE = ROOT.parent                                      # 狸猫猎手/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# ⛔ 项目根也必须进 sys.path（2026-09-15 修）：`common/` 包在**项目根**、不在 wealth_code/ 下。
#    以脚本方式运行本文件时 sys.path[0] = 脚本所在目录（wealth_code/scripts），cwd **不入** sys.path，
#    于是 `_close_of` 里的 `import common.tencent_kline` 抛 ModuleNotFoundError → 被 except 静默吞掉
#    → 返回值恒为 None → **AI 轨结算永远记「0 成交」**（09-14 补记那次是经 cwd 命中才偶然成功）。
#    由 tests/test_ai_ledger_close_path.py 锁死（断言 BASE 已在 sys.path ∧ common 可解析）。
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

BJ_TZ = timezone(timedelta(hours=8))
CACHE = ROOT / "cache"
DECISIONS = CACHE / "ai_decisions.jsonl"
AI_LEDGER = CACHE / "ai_nav_history.jsonl"
# T+1 口径（2026-11-11 用户「两套口径不混账」）：同一批买入，**次日收盘**才卖。
# ⛔ 独立文件、独立净值序列，绝不与策略口径混成一条线。
AI_LEDGER_T1 = CACHE / "ai_nav_history_t1.jsonl"
PY_LEDGER = CACHE / "account_nav_history.jsonl"
# ⛔ AI 判断视图目录 = 狸猫猎手/cache（**不是** wealth_code/cache）——2026-09-11 修：
#    消费方 build_cards._load_ai_view 读 ROOT.parent/cache，此前本脚本写 wealth_code/cache，
#    写进去也读不到（两侧测试各自 mock 自己那半 → 接缝零覆盖，09-10 上线至今未暴露）。
#    改动此常量必须同步 build_cards.AI_VIEW_DIR，并由 test_ai_view_dir_seam 锁死。
AI_VIEW_DIR = BASE / "cache"
BASE_NAV = 1.0                      # AI 轨起点净值（独立计）
DEFAULT_ENTRY_MAX_GAP = 3.0         # AI 自定上限的兜底值（仅留痕·⛔ 不参与裁决）
STRATEGY = "ai_judgement_next_day_close"
STRATEGY_T1 = "ai_judgement_t1_close"     # 次日收盘卖（可执行性口径）

# ── 统一买入口径（2026-09-14 用户「AI 决定要买的，低于 5% 直接买」）────────────
# 单一源 = `core/ai_caliber.USER_ENTRY_MAX_GAP_PCT`；本轨结算必须与账户账本同值，
# 否则两轨在「同一票买没买到」上会打架（09-14 闽东电力实证：账户口径已修，
# AI 轨若漏改会继续按 AI 自定 2% 记 0 成交 → 与卡面/账户账本三方不一致）。
# 单票仓位上限 50% 同理（`MAX_POSITION_PER_STOCK`，写入台账时裁剪）。
try:
    from core import ai_caliber as _ai_caliber
except Exception:                                     # 独立运行兜底（sys.path 已含 ROOT）
    _ai_caliber = None

USER_ENTRY_MAX_GAP_PCT = getattr(_ai_caliber, "USER_ENTRY_MAX_GAP_PCT", 5.0)
MAX_POSITION_PER_STOCK = getattr(_ai_caliber, "MAX_POSITION_PER_STOCK", 0.5)

# 🎯 早盘接力·动态仓位（2026-10-13 用户需求）：落盘时把单票/总仓上限裁到动态方案以内，
# 与 pool_call / 卡面共用同一套方案（relay_position 唯一判据源）。
try:
    from core import relay_position as _rp
except Exception:                                     # 独立运行兜底
    _rp = None

# ── 🎯 9:25 池内裁决（2026-09-21 用户「接力池＝提前缩范围，不是提前拍板」）────────────
# 盘后（T-1）只写**预案**（bias：focus/normal/avoid），**不再定"买哪只"**；
# 次日 09:25 竞价落地后由 `core/pool_call` 在池内判命中 → 结论冻结在
# `wealth_code/cache/pool_call-<执行日>.json`。本轨结算必须读**同一份冻结结论**，
# 否则会出现「卡上买 A、账户账本买 B、AI 轨买 C」三方打架。
def _pool_call_views(trade_date: str) -> dict:
    """读当日已冻结的 9:25 池内裁决结论 → {code: {view, reason, ...}}。缺失 → {}。

    ⚠️ 路径**跟随模块全局 `CACHE` 动态求值**（⛔ 不用导入期快照的常量）——与
    `build_cards._load_ai_stats` 同款模式：测试 monkeypatch `al.CACHE` 才生效，
    否则单测会去读真实生产目录（且掩盖路径接缝）。
    """
    try:
        p = CACHE / f"pool_call-{trade_date}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        v = d.get("views")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _requires_pool_call(row: dict) -> bool:
    """9:25 模式的预案必须等冻结件，盘后倾向不能替代实际买入。"""
    # 新链路已由 pool_call 接管“买哪只”；缺冻结件宁可不成交。
    # 9-21 前的历史行仍按旧口径读取，避免篡改历史结算语义。
    return (row.get("execution_mode") == "pool_call_925"
            or str(row.get("decision_date") or "") >= "2026-09-21")


def _effective_view(row: dict, pool_views: dict) -> str:
    """该票的**生效**判断：9:25 池内裁决优先，缺失则回退**昨日预案档** bias。

    ⛔ 2026-09-22「彻底不派」：盘后只写 bias（focus/normal/avoid），⛔ 不再派生 view。
    故 9:25 无结论时，回退口径从「读盘后 view」改为「读盘后 bias 反推终态」：
      focus→participate / normal→watch / avoid→skip —— 与旧派生口径**逐字一致**，
    保证结算/账本时序不被改写。真·旧数据（既无 9:25 也无 bias，只有 legacy view）
    仍走 `row.get("view") or "participate"` 兜底（缺 view 视为 participate）。
    """
    from core.two_pool import observation_only, rechecked_entry
    code = str(row.get("code") or "").strip()
    m = pool_views.get(code) if isinstance(pool_views, dict) else None
    # 🔁 11-05：冻结件里被竞价复核翻案的观察票，账本必须记 participate。
    #    ⛔ 否则「卡面说买、账本记 watch」——盘后身份把 09:25 的实判二次否决。
    if rechecked_entry(m):
        return "participate"
    if observation_only(row):
        return "watch"
    if isinstance(m, dict) and str(m.get("view") or "").strip():
        return str(m["view"])
    _bias = str(row.get("bias") or "").strip()
    if _bias == "focus":
        return "participate"
    if _bias == "avoid":
        return "skip"
    if _bias == "normal":
        return "watch"
    return str(row.get("view") or "participate")


def _settle_decision(row: dict, effective_view: str,
                     pool_mark: dict | None) -> dict:
    """结算用决策行 = 原始行 + 生效判断 + 竞价复核翻案标记。

    ⛔ 翻案标记必须一并带上：否则 `ai_caliber.decide` 会拿盘后 observe_only
    身份把冻结件已判 participate 的票判回不买（11-05「判了买、结算不买」）。
    """
    d = dict(row)
    d["view"] = effective_view
    from core.two_pool import rechecked_entry
    if rechecked_entry(pool_mark):
        d["rechecked_from_observe"] = True
        d["recheck_class"] = (pool_mark or {}).get("recheck_class") or "state"
    # 🎯 11-05 竞价三态：人工二次机会的标记一并灌入结算行，
    #    让 AI 轨与账户账本同走 ai_caliber.decide 的「不记录」闸。
    if (pool_mark or {}).get("manual_only") is True:
        d["manual_only"] = True
        d["auction_state"] = pool_mark.get("auction_state")
    # 🎯 11-05：冻结件的逐票质量预期优先于盘后候选，确保结算与 09:25 终裁同源。
    for _k in ("expected_gap_pct", "gap_vs_expected", "is_20cm"):
        if (pool_mark or {}).get(_k) is not None:
            d[_k] = pool_mark[_k]
    return d


def _cand_fields_of(decision_date: str, code: str) -> dict:
    """从盘后候选文件取该票的 gate 字段（预期区间 + 量能目标）—— 2026-09-17「A 计划/A2/量能门」。

    背景：AI 轨结算须与账户账本同走 `ai_caliber.decide`（含预期区间门 + 量能门），
    但决策台 `ai_decisions.jsonl` 不存这两项 → 在 record 时从
    `candidates-<decision_date>.json` 补写进决策行，settle 直接读行内字段
    （自包含·不依赖候选文件事后留存）。
    返回 {} 表示取不到（诚实降级·结算时对应门不生效）。
    """
    p = CACHE / f"candidates-{decision_date}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for k in ("relay", "watch", "low_pool"):
        for it in (d.get(k) or []):
            if str(it.get("code") or "") == str(code):
                exp = it.get("expectation") or {}
                out: dict = {}
                er = exp.get("expected_range")
                if isinstance(er, (list, tuple)) and len(er) == 2:
                    out["expected_range"] = [float(er[0]), float(er[1])]
                need = exp.get("auction_amount_min_yi")
                if isinstance(need, (int, float)) and need > 0:
                    out["amount_need_yi"] = float(need)
                # 🎯 11-05 用户删 5%/红开：决策行自带逐票质量预期 E 与 20cm 标志，
                #    settle → ai_caliber.decide 才能与 09:25 池内裁决同源判「是否符合预期」。
                #    ⛔ 缺 E 时 decide 走「不裁决」，不猜、不否决。
                try:
                    from core import expected_gap as _eg
                    _e = _eg.expected_gap_pct(it)
                    if _e is not None:
                        out["expected_gap_pct"] = _e
                except Exception:
                    pass
                out["is_20cm"] = bool(it.get("is_20cm"))
                return out
    return {}


# ── 预案档 ↔ 旧口径 view 的互转（2026-09-21 语义迁移）─────────────────────────
# 盘后写 `bias`（预案档），9:25 池内裁决写 `view`（终态）。为了让**旧消费方**
# （尚未改造的脚本 / 历史审计）不炸，`bias` 落盘时同时派生一个同义 `view`；
# 反之若调用方只给了 `view`，也补一个 `bias` 便于卡面渲染「昨日预案」。
_BIAS_TO_VIEW = {"focus": "participate", "normal": "watch", "avoid": "skip"}
_VIEW_TO_BIAS = {"participate": "focus", "watch": "normal", "skip": "avoid"}


# ── ⛔ bias 越权兜底（2026-09-22 用户抓包「接力池#1 真龙被判观察」）────────────────
# 背景（实证 2026-09-21 盘后 → 09-22 执行）：
#   · 接力池 **#1** 内蒙新华(603230)：09:25:02 **秒板**、带动 **5 家**、带 **「先发」** tag、
#     龙头分 65.1 —— AI 判 **`normal`（一般/观察）**；
#   · 接力池 **#6** 世联行(002285)：**13:17 午后**封板、**无「先发」**、龙头分 39.31 —— AI 判 **`focus`（重点看）**。
#   AI 给出的降级理由是「收入降24%、净利降82%、炒名字、砸开3次」——**全部不属于四类硬伤**
#   （🤖-1c②：一字买不到 / ST / 成交额<5亿 / 纯跟风无带动；且「高位炸板=辨识度」按 P2-9 是**加分**）。
#   ⛔ 危害面：裁决链第③条「昨日预案＝回避 → **放弃**」是**硬否决** ——
#     若 AI 给真龙 `avoid`，09:25 会直接不买（09-22 只是恰好给了 `normal` 才被机械层救回）。
# 本兜底只做**一件事**：`avoid` 必须命中四类硬伤，否则降级 `normal`（+ `bias_warning` 留痕）。
#   ⛔ 只降这一档（avoid→normal），**不触碰** focus/normal —— 不反压 AI 的判断空间；
#   ⛔ 不改写 AI 的 `reason` 原文（显示层照旧保留 AI 说了什么）；
#   ⛔ 取不到候选文件（缺席/字段缺）→ 视为「无硬伤信息」→ **不降级**（宁松不误伤）。
ENABLE_BIAS_AVOID_GUARD = True
# 四类硬伤里的「成交额过小」阈值
# 🔴 2026-11-11 用户「5 亿的限制全系统清除」→ 成交额不再是 `avoid` 的硬伤理由。
#    保留常量只为可逆回退（`HARD_FLAW_MIN_TURNOVER_YI=5.0` 即可恢复旧口径）。
#    ⛔ 现在只剩三件真硬伤：一字板买不到 / ST / 纯跟风无带动。
HARD_FLAW_MIN_TURNOVER_YI = 0.05
# 机械「强龙」证据阈值：首封 ≤ **09:30** 才算真秒板（开盘即封）
# ⛔ 不用 relay_score 的 ≤10:00 档：实证 09-21 候选 16 只里 09:31~09:56 封板的有 8 只，
#    用 ≤10:00 会把它们全标成"秒板" → 告警退化为噪声（同「全他妈打上孤军」的教训）。
LEADER_SECOND_BOARD_MAX_FBT = 930


def _cand_stock_of(decision_date: str, code: str) -> dict:
    """从盘后候选文件取该票原始条目（relay/watch）。取不到 → {}（诚实降级）。"""
    p = CACHE / f"candidates-{decision_date}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for bucket in ("relay", "watch", "low_pool"):
        for it in (d.get(bucket) or []):
            if str(it.get("code") or "") == str(code):
                return {**it, "_bucket": bucket}
    return {}


def _mech_hard_flaws(it: dict) -> list[str]:
    """四类硬伤（🤖-1c②）的**机械**判定 —— 命中任一，AI 才有资格给 `avoid`。"""
    flaws: list[str] = []
    if it.get("is_one_word"):
        flaws.append("一字板买不到")
    if it.get("is_st"):
        flaws.append("ST")
    cje = it.get("cje_yi")
    if isinstance(cje, (int, float)) and 0 < cje < HARD_FLAW_MIN_TURNOVER_YI:
        flaws.append(f"成交额<{HARD_FLAW_MIN_TURNOVER_YI:g}亿")
    # 纯跟风无带动：行业与题材皆孤（无板块效应）**且**封板后没带动任何同伴
    lone = (bool(it.get("sector_is_lone"))
            and not bool(it.get("theme_has_sector_effect"))
            and int(it.get("carry_cnt") or 0) == 0)
    if lone:
        flaws.append("纯跟风无带动")
    return flaws


def _mech_leader_marks(it: dict) -> str:
    """机械「强龙」证据（仅用于**告警留痕**，⛔ 不改变 bias）。

    ⛔ **判据必须能分开人**，否则退化成噪声（同「全他妈打上孤军」的教训）：
       · **含**：接力池 **第1** / 首封 **≤09:30**（真秒板·开盘即封）—— 实测各只命中 1~2 只；
       · **不含**：`先发` tag（09-21 实测 16 只里 **13 只**都有 → 零区分度）；
         `fbt ≤10:00`（会把 09:31~09:56 的 8 只全当"秒板"）。
       · `带动≥4家` 仅作**附加说明**（有主证据时才附），不单独触发告警。
    """
    marks: list[str] = []
    # 接力池第 1（`relay_rank` 仅接力池有 → 直接判字段即可，不依赖外部注入的 `_bucket`）
    if int(it.get("relay_rank") or 0) == 1:
        marks.append("接力池第1")
    fbt = "".join(ch for ch in str(it.get("fbt") or "") if ch.isdigit())
    if len(fbt) >= 4 and int(fbt[:4]) <= LEADER_SECOND_BOARD_MAX_FBT:
        marks.append(f"{fbt[:2]}:{fbt[2:4]}秒板")
    carry = int(it.get("carry_cnt") or 0)
    if marks and carry >= 4:
        marks.append(f"带动{carry}家")
    return "·".join(marks)


# ── 🛡️ 交易闸门三条 · 赔率 / 周期留痕（2026-09-23 用户定·延迟任务落地）────────────
# 依据 = `config/shared_rules.md`【🛡️ 交易闸门三条 · 交割单实证反推】：
#   🛡️-1 赔率闸门 —— 进场前写死「目标涨幅 / 止损幅度」，**目标/止损 < 2 → ⛔ 不做**；
#   🛡️-2 周期闸门 —— 进场时贴「兑现周期标签」（主线核心按周/月 · 纯情绪票才日内~3天）。
# 本段只做**留痕**：⛔ 不做硬拦、⛔ 不改 `bias` / `view`、⛔ 不参与买卖裁决 ——
#   闸门靠纪律与宪法约束；代码硬拦会误杀真实机会，代价大于漏记一条告警。
#   · `target_pct` / `stop_pct` —— 目标涨幅 / 止损幅度（百分数·如 12.0 / 5.0）
#   · `hold_horizon`           —— 兑现周期标签·合法值 `intraday` / `swing` / `mainline`
# ⛔ 三字段**全部可选**：缺失即 None、不做必填校验、不抛错、不阻断落盘。
ENABLE_PAYOFF_WARNING = True
# 🛡️-1 赔率下限 = 2（⛔ 阈值单一源 = `scripts/behavior_attribution.GATE_PAYOFF_MIN`，改须两处同步）
PAYOFF_MIN = 2.0
# 🛡️-2 周期标签合法档（非法值原样留痕 + 告警·⛔ 不抛错）
HOLD_HORIZON_VALID = ("intraday", "swing", "mainline")
# 档位白话（告警文案用·⛔ 卡片话术铁律：英文档位不上「人看的」文案）
_HOLD_HORIZON_PLAIN = "日内/波段/主线核心"


def _num_or_none(v: Any) -> float | None:
    """宽容取数 → float；缺 / 空 / 非数 / 布尔 → None（⛔ 不抛错·不阻断落盘）。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _payoff_of(pk: dict) -> tuple[float | None, float | None, str | None]:
    """逐票读 `target_pct` / `stop_pct` → (目标, 止损, 赔率告警)。

    赔率 = |目标 / 止损|；**两值都给 ∧ 止损 ≠ 0 ∧ 赔率 < 2** → 出大白话告警
    （🛡️-1：赔率不足 2 → 按纪律不做）。⛔ 只加告警字段，⛔ 不改 `bias` / `view`；
    ⛔ 不做必填校验、不抛错——缺任一值即静默返回 None（诚实降级）。
    """
    tgt = _num_or_none(pk.get("target_pct"))
    stp = _num_or_none(pk.get("stop_pct"))
    warn: str | None = None
    if ENABLE_PAYOFF_WARNING and tgt is not None and stp is not None and stp != 0:
        ratio = abs(tgt / stp)
        if ratio < PAYOFF_MIN:
            warn = (f"赔率 {round(ratio, 2):g} 不足 {PAYOFF_MIN:g}"
                    f"（目标 {tgt:g}% / 止损 {stp:g}%）· 按纪律应不做")
    return tgt, stp, warn


def _horizon_of(pk: dict) -> tuple[str | None, str | None]:
    """逐票读 `hold_horizon` → (留痕值, 告警)。非法值**原样留痕** + 告警，⛔ 不抛错。

    ⛔ 不做大小写 / 别名归一：只按宪法 🛡️-2 的三个合法档**精确匹配**（两端空白容忍）——
    写错档位本身就是要留痕的行为，代 AI 猜档会掩盖输入质量问题。
    """
    raw = pk.get("hold_horizon")
    if raw is None:
        return None, None
    val = str(raw).strip()
    if not val:
        return None, None
    if val in HOLD_HORIZON_VALID:
        return val, None
    return val, (f"周期标签「{val}」不是标准档（可选 {_HOLD_HORIZON_PLAIN}）"
                 "· 已原样留痕，请复核")


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _append_jsonl(p: Path, row: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _nav_now() -> float:
    rows = _read_jsonl(AI_LEDGER)
    return float(rows[-1]["nav"]) if rows else BASE_NAV


def _next_trade_day(date10: str) -> str:
    """次一A股交易日；统一法定休市日历，禁止仅跳周末。"""
    from common.trading_calendar import next_a_share_trading_day
    return next_a_share_trading_day(date10) or date10


def _write_ai_view(trade_date: str, decision_date: str, views: dict,
                   meta: dict | None = None, *, replace: bool = False) -> Path:
    """写 AI 判断视图（合并进竞价验证·build_verify_card 消费）。

    meta（2026-09-11 用户「竞价卡要告诉我今天买什么」）：stance/stance_label/direction/note
    → 竞价卡顶部「🤖 AI 今日买入计划」段的定调与方向；缺省则只渲染逐票 AI 判断行。
    幂等：同 trade_date 重复写按 code 合并（后写覆盖同 code），不丢已有 views。

    `replace=True`（2026-10-27 补·治幽灵票）：**不合并、整体以本次为准**。
      踩坑实证：同日因池子规则修正重跑了一次决策（5 只 → 4 只），合并逻辑把上一版
      已被踢出池的 襄阳轴承/文投控股/国芳集团 留在 `views` 里，次日竞价卡会显示
      「这三只要买」——而它们根本不在候选池，属于凭空多出来的决策。
      ⛔ 默认仍合并（兼容多次分批写入），只在显式传 `--replace` 时覆盖。
    """
    p = AI_VIEW_DIR / f"ai_view_{trade_date}.json"
    prev: dict = {}
    try:
        prev = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    p.parent.mkdir(parents=True, exist_ok=True)
    merged_views = {} if replace else dict(prev.get("views") or {})
    if not replace:
        _ghosts = sorted(set(merged_views) - set(views))
        if _ghosts:
            print(f"[ai_ledger] ⚠️ 视图里还有 {len(_ghosts)} 只上一版遗留（{','.join(_ghosts)}）；"
                  f"若本次是同日整体重判，请带 --replace 覆盖", file=sys.stderr)
    merged_views.update(views)
    out = {k: v for k, v in prev.items() if k not in ("views", "date", "decision_date", "ts")}
    out.update({
        "date": trade_date,
        "decision_date": decision_date,
        "views": merged_views,
        "source": "AI",
        "ts": datetime.now(BJ_TZ).isoformat(),
    })
    for k in ("stance", "stance_label", "direction", "note", "decided_at", "entry_max_gap"):
        if meta and meta.get(k) is not None:
            out[k] = meta[k]
    out.setdefault("decided_at", out.get("ts"))
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# ── 1) 记录 AI 决策 ───────────────────────────────────────────────
def cmd_record(args) -> int:
    ddate = args.decision_date or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    picks: list[dict] = []
    meta: dict = {}
    if args.file:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            picks = raw
        else:
            picks = raw.get("picks") or []
            meta = {k: raw.get(k) for k in ("stance", "stance_label", "direction", "note", "entry_max_gap")}
            meta = {k: v for k, v in meta.items() if v is not None}
    if not picks:
        print("ERROR: 无决策内容（--file 需为 [{code,name,reason,entry_max_gap?,position?}]）", file=sys.stderr)
        return 2
    trade_date = str(getattr(args, "trade_date", None) or _next_trade_day(ddate))
    existing = {(r.get("decision_date"), r.get("code")) for r in _read_jsonl(DECISIONS)}
    view_map: dict[str, dict] = {}
    n = 0

    # 🎯 早盘接力·动态仓位（2026-10-13 用户需求）——规划口径（T-1 盘后落盘）。
    # ⛔ 边界：落盘时**只取行情强弱档位的基础仓位上限**（名额/单票/总仓），**不含净值回撤**
    #    的软/硬线裁断（回撤线只在 09:25 执行时点才生效，由 pool_call / 卡面叠加）。
    #    题材沉淀已积累 → 给档位（与 relay_position.build_plan_from_files 同源判定）。
    _relay_plan = None
    _relay_per_cap = None
    _relay_total_cap = None
    if _rp is not None and _rp.RELAY_POSITION_ENABLED:
        try:
            # 落盘时无「今晨竞价」→ S_m 诚实取中性 0.5；档位由 昨日强势 + 题材沉淀 决定。
            _relay_plan = _rp.build_plan_from_files(candidates=picks, quotes=None)
            _rq, _relay_per_cap, _relay_total_cap = _rp.tier_caps(_relay_plan.get("tier"))
        except Exception as _e:                       # 诚实降级·不得阻断落盘
            print(f"  ⚠️ 动态仓位方案跳过（落盘仍按原 50% 上限）：{_e}", file=sys.stderr)
            _relay_plan = None

    # 本批次落盘的逐票 position 收集（供总仓上限等比裁断）
    _new_rows: list[dict] = []
    _new_positions: list[float | None] = []
    for pk in picks:
        code = str(pk.get("code") or "").strip()
        if len(code) != 6:
            print(f"  跳过非法代码: {pk}", file=sys.stderr)
            continue
        if (ddate, code) in existing:
            print(f"  跳过重复决策: {ddate} {code}")
            continue
        # 🎯 2026-09-21 语义迁移：盘后输入的是**预案档** `bias`（focus/normal/avoid），
        #    ⛔ 不再是「买哪只」的终态。为兼容旧消费方，落盘时同步派生同义 `view`
        #    （focus→participate / normal→watch / avoid→skip）——9:25 池内裁决会把它覆盖成实判。
        _bias = str(pk.get("bias") or "").strip()
        if _bias not in _BIAS_TO_VIEW:
            # 只给了旧口径 view → 反推预案档；两者都没给 → **空**（沿用旧行为：`view=None`，
            # `ai_caliber.decide` 把缺 view 视为 participate），⛔ 不得默认成 watch 而悄悄改口径。
            _bias = _VIEW_TO_BIAS.get(str(pk.get("view") or "").strip(), "")
        # ⛔ bias 越权兜底（2026-09-24 降噪）：
        #    `avoid` 无四类硬伤 → **降级 normal**（avoid 会在 09:25 硬否决买入）并显著告警。
        #    `normal` 与机械强龙不同不再告警：09:25 已将强龙与 focus 同档排序，
        #    原始 bias、机械强龙事实与最终裁决都已落盘，可供事后归因，不能再制造待处理假告警。
        # 同一冻结候选同时提供越权校验与台账排序评分，避免分别读取后期可能变化的文件。
        _candidate = _cand_stock_of(ddate, code)
        _bias_warning = ""
        if _bias:
            if _bias == "avoid" and ENABLE_BIAS_AVOID_GUARD and _candidate:
                if not _mech_hard_flaws(_candidate):
                    _bias = "normal"
                    _bias_warning = ("AI 原判 avoid，但候选无真硬伤（一字买不到/ST/纯跟风无带动）"
                                     "→ 已降级 normal（avoid 会在 09:25 直接否决买入）")
                    print(f"  ⚠️ bias=avoid 无硬伤 → 降级 normal：{code} {pk.get('name') or ''}",
                          file=sys.stderr)
            # `normal` 与机械强龙的差异仅保留在 bias、mechanical_leader 和 09:25 裁决中，
            # 不写 `bias_warning`。融合排序已处理该差异；只有 avoid 的硬否决风险才需要显著提示。
        # 🛡️ 交易闸门留痕（2026-09-23）：逐票**可选**读目标涨幅 / 止损幅度 / 兑现周期标签
        #    ⛔ 缺即 None、不校验、不抛错——见上方 `ENABLE_PAYOFF_WARNING` 段
        _tgt, _stp, _payoff_warning = _payoff_of(pk)
        _horizon, _horizon_warning = _horizon_of(pk)
        # 🎯 2026-11-04「只能推一只时仓位控死50%」：AI **原始申报**的单票仓位另存一份，
        #    因为下面 `position` 会被行情档位（弱档 30% / 中档 40%）压小。
        #    09:25 真实只有 1 只参与时，结算按「单票上限 50%」重裁要用原始申报，
        #    ⛔ 不得拿被压过的 30% 反推（否则「AI 想给 5 成」会被永久记成 3 成）。
        _pk_pos = pk.get("position")
        try:
            _pk_pos_f = float(_pk_pos) if _pk_pos is not None else None
        except (TypeError, ValueError):
            _pk_pos_f = None
        row = {
            "decision_date": ddate,
            "code": code,
            "name": pk.get("name") or "",
            "reason": (pk.get("reason") or "").strip(),
            "factors": pk.get("factors") or {},
            # 候选冻结时的排序评分（满分 100）：供 AI 台账按当日真实候选口径复盘，绝不在结算时重算。
            "participation_eligibility": _candidate.get("participation_eligibility"),
            "ranking_score": _candidate.get("score"),
            "entry_max_gap": float(pk.get("entry_max_gap", DEFAULT_ENTRY_MAX_GAP)),
            # 单票仓位上限：默认 50%（2026-09-14 用户「买入仓位不超过 50%」）；
            # 🎯 若早盘接力动态方案启用 → 按行情强弱档位的基础单票上限裁（行情转弱自动收紧）。
            "position": ((_ai_caliber.clamp_position(pk.get("position"), _relay_per_cap)
                          if _ai_caliber else pk.get("position"))),
            # AI 原始申报仓位（未经行情档位裁剪）——仅供 09:25 单票场景重裁，⛔ 不参与裁决
            "position_requested": _pk_pos_f,
            # 盘后预案档（focus/normal/avoid·昨日唯一结论）；未给 → None（旧口径兼容）
            "bias": _bias or None,
            # ⛔ 越权兜底留痕（2026-09-22）：非空 = 该票的 bias 与机械证据冲突（或已被降级）
            "bias_warning": _bias_warning or None,
            # 🛡️ 交易闸门三条留痕（2026-09-23）：三字段**全可选**·缺失即 None
            #    ⛔ 纯留痕：不校验、不抛错、不阻断落盘、不改 bias/view（见上方常量段）
            "target_pct": _tgt,
            "stop_pct": _stp,
            "payoff_warning": _payoff_warning,
            "hold_horizon": _horizon,
            "hold_horizon_warning": _horizon_warning,
            # ⛔ 2026-09-22「彻底不派」：盘后 ⛔ 不再派生 view，仅保留调用方显式传入（通常为 None）
            "view": (pk.get("view") or None),
            # 新链路的成交授权只能来自次日 9:25 冻结件，供结算与审计识别。
            "execution_mode": "pool_call_925" if ddate >= "2026-09-21" else "legacy_plan",
            "source": "AI",
            "ts": datetime.now(BJ_TZ).isoformat(),
            "settled": False,
        }
        if not row["reason"]:
            print(f"  跳过无理由决策: {code}（reason 必填·数据即事实）", file=sys.stderr)
            continue
        # 方案 A / A2 / 量能门（2026-09-17）：补写盘后预期区间 + 量能目标
        # → 次日结算走与账户账本**同一** `ai_caliber.decide`（区间门 + 量能门同源）
        _cf = _cand_fields_of(ddate, code) if _ai_caliber else {}
        if _cf:
            row.update(_cf)
        _append_jsonl(DECISIONS, row)
        _new_rows.append(row)
        _new_positions.append(row.get("position"))
        n += 1
        print(f"  ✓ 记录 AI 决策 {ddate} {row['name']}({code}) {row['reason'][:40]}")
        # 🤖 合并进竞价验证（2026-09-10 用户）：同步写 ai_view_{trade_date}.json
        # → 次日 build_verify_card 渲染「🤖 AI 判断」行，与机器信号同卡对照（不再另开平行台账查看）
        if row.get("view") or row.get("bias"):
            view = view_map.setdefault(code, {})
            # ⛔ entry_max_gap 必须带进 views（2026-09-14 修）：此前只 UPDATE view/reason/factors，
            #    导致 build_cards._ai_plan_block 取不到逐票上限 → 回落到顶层 meta 默认 3.0，
            #    卡面显示「高开超上限+3%」而 AI 理由里写「开超2%就不买」（闽东电力实证），
            #    且与结算口径不一致（cmd_settle 读决策行的 2.0）。三处必须同值。
            view.update({
                "view": row["view"],
                # 🎯 2026-09-21：盘后预案档随视图落盘 —— 次日 09:25 池内裁决读它做排序/否决
                #    （focus 优先、avoid 直接否决），卡面同时展示「昨日预案」与「今日实判」。
                "bias": row["bias"],
                "reason": row["reason"],
                "factors": row["factors"],
                "entry_max_gap": row["entry_max_gap"],
                # ⛔ 兜底留痕随视图落盘 → AI 台账/竞价卡可直接看到「这条 bias 被判越权」
                "bias_warning": row.get("bias_warning"),
                # 🛡️ 赔率 / 周期留痕随视图落盘（2026-09-23）——与 bias/reason 同级，
                #    供 AI 台账「判断明细」与后续卡面排期消费（本次 ⛔ 不改渲染）
                "target_pct": row.get("target_pct"),
                "stop_pct": row.get("stop_pct"),
                "payoff_warning": row.get("payoff_warning"),
                "hold_horizon": row.get("hold_horizon"),
                "hold_horizon_warning": row.get("hold_horizon_warning"),
            })
    # 🎯 早盘接力·总仓上限（规划口径·T-1 盘后）：本批次落盘的单票计划仓位之和裁到 ≤ total_cap。
    # ⛔ 边界：此处于「规划」阶段，只按行情强弱档位的基础总仓上限等比缩放，不做回撤硬/软线拦截
    #    （回撤拦截只在 09:25 执行层生效）。等比缩放保持各票相对权重，不偏袒某一只。
    if _rp is not None and _relay_total_cap and _new_rows:
        try:
            _scaled = _rp.apply_total_cap(_new_positions, {
                "enabled": True, "can_buy": True,
                "per_pick_cap": _relay_per_cap, "total_cap": _relay_total_cap,
            })
            _changed = False
            for _row, _p in zip(_new_rows, _scaled):
                if isinstance(_p, (int, float)) and _p > 0:
                    if abs(float(_row.get("position") or 0) - float(_p)) > 1e-6:
                        _changed = True
                    _row["position"] = round(float(_p), 4)
            if _changed:
                # 重写 DECISIONS 文件（只更新本批次落盘的 position 字段·其余行原样保留）
                _all = _read_jsonl(DECISIONS)
                _key2pos = {(r.get("decision_date"), r.get("code")): r.get("position")
                            for r in _new_rows}
                for _r in _all:
                    _k = (_r.get("decision_date"), _r.get("code"))
                    if _k in _key2pos:
                        _r["position"] = _key2pos[_k]
                        _r["relay_total_cap_applied"] = round(float(_relay_total_cap), 4)
                DECISIONS.write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in _all) + "\n",
                    encoding="utf-8")
                print(f"  🎯 动态总仓上限已套用：本批计划仓位之和已裁到 ≤ {_relay_total_cap:.0%}")
        except Exception as _e:                       # 诚实降级·不得阻断落盘
            print(f"  ⚠️ 动态总仓上限跳过：{_e}", file=sys.stderr)

    if view_map:
        meta.setdefault("entry_max_gap", DEFAULT_ENTRY_MAX_GAP)
        p = _write_ai_view(trade_date, ddate, view_map, meta=meta,
                           replace=bool(getattr(args, "replace", False)))
        print(f"[ai_ledger] 🤖 AI 判断视图已写 {p}（{len(view_map)} 只·供竞价验证卡渲染"
              f"{'·含今日买入计划' if meta.get('stance_label') or meta.get('note') else ''}）")
    print(f"[ai_ledger] 记录 {n} 条 AI 决策 → {DECISIONS.name}")
    return 0


# ── 2) 结算（同口径：T+1 竞价买 → T+1 收盘卖）────────────────────────
def _load_quotes(trade_date: str) -> dict:
    p = CACHE / f"auction_quotes-{trade_date}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── 竞价报价新鲜度校验（2026-11-03 修）────────────────────────────────────────
# 事故：`auction_quotes-2026-09-30.json` 里 603533 掌阅科技的记录是
#   {"prev_close": 21.5, "auction_price": 21.76, "time": "2026-09-29 15:00:01"}
# —— prev_close 是 **09-28** 收盘、21.76 是 **09-29 开盘**，整条记录其实是 09-29 的竞价，
#   却在 09-30 09:27 被抓取落进 09-30 的文件（上游当时还没更新到当日集合竞价）。
# 后果：结算按 (09-30收盘 23.88 − 09-29竞价 21.76)/21.76 = **+9.74%**，
#   而真实口径 (23.88 − 23.90)/23.90 = **−0.08%**，一日虚增 9.8pp 并带歪其后全部净值。
#
# 判据（⛔ 不用「今天开不开盘」，只看这份报价是不是当日的）：
#   ① 报价自带 `time` 的**日期部分** ≠ 交易日的 → 跨日，判定陈旧（最硬证据，零额外请求）
#   ② 有当日日K时，竞价价与当日开盘价偏差 > QUOTE_MAX_DEV_PCT → 判定异常
#      （A股集合竞价成交价 **就是** 开盘价，正常应逐分一致；实测 13/14 条历史偏差 0.00%）
# 命中后 **不跳过**，而是换成日K开盘价并连带换前收（同为前复权，口径一致），
# 同时把 price_src / quote_time 写进账本 —— 交易确实发生过，要按真实价记，且可事后追溯。
QUOTE_MAX_DEV_PCT = 5.0        # 竞价价与开盘价允许的最大偏差（%），超出即视为取错行
# 日K缓存目录：**模块级变量**而非函数内字面量 —— 单测用 `monkeypatch.setattr(al, "KLINE_DIR", …)`
# 隔离，否则校验会去读真实生产K线，把测试桩里的假价格覆盖掉（2026-11-03 实测 5 条用例因此变红）。
KLINE_DIR = BASE / "cache" / "kline_qfq"


def _cached_bars(code: str) -> list[dict]:
    """读 `cache/kline_qfq/<code>.json` 的 bars（前复权·含 open/close）。缺失→[]。"""
    try:
        p = KLINE_DIR / f"{code}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        bars = d.get("bars") or []
        return [b for b in bars if isinstance(b, dict) and b.get("date")]
    except Exception:
        return []


def _open_of(code: str, trade_date: str, allow_network: bool = False) -> float | None:
    """当日开盘价（= 集合竞价成交价）。先查本地K线缓存；陈旧时才允许联网补。"""
    key = str(trade_date).replace("-", "")
    for b in _cached_bars(code):
        if str(b.get("date")).replace("-", "") == key:
            try:
                return float(b["open"])
            except Exception:
                return None
    if not allow_network:
        return None
    try:
        import common.tencent_kline as tk
        for b in tk.fetch_kline_ohlc(code, n=8):
            if str(b.get("date", "")).replace("-", "") == key and b.get("open"):
                return float(b["open"])
    except Exception:
        pass
    return None


def _prev_close_of(code: str, trade_date: str, allow_network: bool = False) -> float | None:
    """上一交易日收盘价（前复权）。用于替换陈旧报价里的 prev_close。"""
    key = str(trade_date).replace("-", "")
    bars = _cached_bars(code)
    for i, b in enumerate(bars):
        if str(b.get("date")).replace("-", "") == key:
            if i == 0:
                break
            try:
                return float(bars[i - 1]["close"])
            except Exception:
                return None
    if not allow_network:
        return None
    try:
        import common.tencent_kline as tk
        rows = [r for r in tk.fetch_kline_ohlc(code, n=8) if r.get("date")]
        for i, b in enumerate(rows):
            if str(b.get("date", "")).replace("-", "") == key and i > 0:
                return float(rows[i - 1]["close"])
    except Exception:
        pass
    return None


def _resolve_auction(code: str, trade_date: str, q: dict) -> dict:
    """把一条报价解析成可信的 (ap, prev_close, src, note)。

    返回 {"ap": float|None, "prev_close": float|None, "src": "auction_quote"|"kline_open",
          "note": str|None, "quote_time": str|None}
    """
    ap = q.get("auction_price")
    prev = q.get("prev_close")
    qtime = str(q.get("time") or "").strip() or None
    qday = (qtime or "")[:10].replace("/", "-")
    stale = bool(qday) and qday != trade_date
    # 先用缓存判断偏差（零网络）；只在已判陈旧时才允许联网补价
    open_px = _open_of(code, trade_date, allow_network=stale)
    dev = None
    if open_px and ap is not None and float(open_px) > 0:
        dev = abs(float(ap) - float(open_px)) / float(open_px) * 100
    bad_dev = dev is not None and dev > QUOTE_MAX_DEV_PCT
    if (stale or bad_dev) and open_px:
        fixed_prev = _prev_close_of(code, trade_date, allow_network=stale) or prev
        why = (f"竞价报价跨日（time={qtime}）" if stale
               else f"竞价价 {ap} 与当日开盘价 {open_px} 偏差 {dev:.2f}%")
        return {
            "ap": float(open_px), "prev_close": fixed_prev, "src": "kline_open",
            "note": f"{why} → 改用 {trade_date} 开盘价 {open_px} 结算",
            "quote_time": qtime,
        }
    return {
        "ap": float(ap) if ap is not None else None, "prev_close": prev,
        "src": "auction_quote", "note": None, "quote_time": qtime,
    }


def _close_of(code: str, trade_date: str) -> float | None:
    """T+1 收盘价（前复权）。**先本机缓存**，缺失才联网 —— 与 `_open_of` 同一策略。

    ⛔ 2026-11-11：原来只走网络，回填时逐票 0.5s 且大量失败 → T+1 口径几乎写不出来。
       缓存里已有同一份前复权日K，先查缓存既快又不依赖网络。
    """
    key = str(trade_date).replace("-", "")
    for b in _cached_bars(code):
        if str(b.get("date")).replace("-", "") == key:
            try:
                return float(b["close"])
            except Exception:  # noqa: BLE001
                break
    try:
        import common.tencent_kline as tk
        rows = tk.fetch_kline(code, n=8)          # [(date, close), ...]
        key = str(trade_date).replace("-", "")
        for d, c in rows:
            if str(d).replace("-", "") == key:
                return float(c)
    except Exception:
        pass
    return None


def cmd_settle(args) -> int:
    tdate = args.trade_date
    if not tdate:
        print("ERROR: --trade-date 必填（AI 决策的执行日 T+1）", file=sys.stderr)
        return 2
    rows = _read_jsonl(DECISIONS)
    due = [r for r in rows if not r.get("settled") and str(r.get("decision_date") or "") < tdate]
    # 🎯 9:25 池内裁决与账户账本 / 卡面读同一份冻结件，三方同源。
    pool_views = _pool_call_views(tdate)
    if pool_views:
        print(f"[ai_ledger] 🎯 读当日 9:25 池内裁决 {len(pool_views)} 只 → 买哪只以它为准")
    # ⛔ 新链路缺冻结件不允许回退盘后预案。focus 只是缩池与排序依据，不是成交授权。
    settlement_blocks: dict[str, str] = {}
    effective_views: dict[str, str] = {}
    for r in due:
        code = str(r.get("code") or "")
        frozen = pool_views.get(code)
        if _requires_pool_call(r) and not (isinstance(frozen, dict) and str(frozen.get("view") or "").strip()):
            settlement_blocks[code] = "9:25 池内裁决冻结件缺失 → 不记实际买入"
            effective_views[code] = "watch"
        else:
            effective_views[code] = _effective_view(r, pool_views)
    # ⛔ 只有「生效判断 = participate」才计入 AI 收益；watch/skip 只留结算痕迹。
    todo = [r for r in due if effective_views.get(str(r.get("code") or "")) == "participate"]
    others = [r for r in due if effective_views.get(str(r.get("code") or "")) != "participate"]
    if not due:
        print(f"[ai_ledger] {tdate} 无待结算 AI 决策")
        return 0

    # ── 🎯 单票场景仓位重裁（2026-11-04 用户「只能推一只时仓位控死50%」）────────
    # 判定源＝09:25 **冻结**的池内裁决（`pool_call-<trade_date>.json`）：
    #   · `meta.slot_policy.single_pick=True` ∧ 该票 `position_cap` 有值 → 单票上限生效；
    #   · 实际上限 = position_cap（= MAX_POSITION_PER_STOCK × 回撤乘数，单一源见
    #     core/relay_position.single_pick_cap）。
    # 重裁 = min(AI 原始申报, 上限)：⛔ 不是强制打满 50%，AI 报 3 成仍记 3 成；
    #   ⛔ 也不让弱档 30%/中档 40% 继续压低（那正是「卡面说 50%、账本记 30%」的病根）。
    # 两票及以上完全不进这个分支（沿用 record 时的 per_pick/total 裁剪）。
    for r in todo:
        _fz = pool_views.get(str(r.get("code") or "")) or {}
        # 🎯 2026-11-05：平均分配后每只 participate 票都带 position_cap，
        #    不再只有单票场景重裁（多票沿用 record 档位会与卡面/裁决打架）。
        _cap = (_fz.get("position_cap")
                if (_fz.get("single_pick") or _fz.get("even_allocation")) else None)
        if not isinstance(_cap, (int, float)) or float(_cap) <= 0:
            continue
        _base = r.get("position_requested")
        if not isinstance(_base, (int, float)):
            _base = r.get("position")
        if isinstance(_base, (int, float)) and float(_base) > 0:
            r["position"] = round(min(float(_base), float(_cap)), 4)
        # 留痕：账本可直接回答「这笔用的是单票上限」
        r["position_cap"] = round(float(_cap), 4)

    quotes = _load_quotes(tdate)
    # AI 决策为唯一入场授权：不再用逃生阀把观察票提升为成交。
    traded, skipped = [], []
    for r in others:
        _code = str(r.get("code") or "")
        _v = effective_views.get(_code, "watch")
        _why = (pool_views.get(_code) or {}).get("reason") if pool_views else None
        skipped.append((_code, settlement_blocks.get(_code) or
                        (f"9:25 池内裁决={_v}·不参与收益统计" + (f"（{_why}）" if _why else ""))))
    for r in todo:
        code = str(r["code"])
        q = quotes.get(code) or {}
        # 报价新鲜度校验（2026-11-03）：跨日/与开盘价严重不符的报价必须换掉，
        # 否则一条 09-29 的竞价会让 09-30 记出 +9.74%（真实 −0.08%），并带歪其后全部净值。
        _res = _resolve_auction(code, tdate, q)
        if _res["note"]:
            print(f"  ⚠️ {code} {r.get('name') or ''}：{_res['note']}", file=sys.stderr)
        ap = _res["ap"]
        prev_close = _res["prev_close"]
        if ap is None:
            skipped.append((code, "无竞价数据（auction_quotes 缺失该票）"))
            continue
        gap = ((float(ap) - float(prev_close)) / float(prev_close) * 100) if prev_close else None
        # ⛔ 统一裁决：与**账户账本同一个函数** `ai_caliber.decide`。
        #    11-05 用户删 5%/红开后 = 参与 ∧ 竞价未封死 ∧ 不明显低于逐票质量预期 ∧ 量能达标。
        #    ⛔ 此处禁再手写任何 gap 幅度线（两轨同票不同结论的老坑）。
        if _ai_caliber is not None:
            # 🎯 生效判断 = 9:25 池内裁决（缺失才回退行内 view）——与账户账本同一个 decide
            #    🔁 11-05：翻案标记由 `_settle_decision` 一并灌入，结算不再二次否决。
            _dec = _settle_decision(r, effective_views.get(code, "watch"),
                                    (pool_views or {}).get(code))
            _can, _note, _ = _ai_caliber.decide(
                _dec, gap, expected_range=r.get("expected_range"),
                auction_amount_yi=q.get("auction_amount_yi"),
                need_yi=r.get("amount_need_yi"))
            if not _can:
                skipped.append((code, _note))
                continue
        elif gap is not None and gap > USER_ENTRY_MAX_GAP_PCT:
            skipped.append((code, f"竞价高开 {gap:.2f}% 超过 {USER_ENTRY_MAX_GAP_PCT:g}% → 不追"))
            continue
        close = _close_of(code, tdate)
        if close is None:
            skipped.append((code, f"缺 T+1 收盘价（{tdate}）"))
            continue
        ret = round((float(close) - float(ap)) / float(ap) * 100, 2)
        _rec = {"code": code, "name": r.get("name") or "", "auction_price": float(ap),
                "close": float(close), "return_pct": ret, "reason": r.get("reason")}
        # 只有「换过价」才留痕：正常行不加字段，避免历史结构无谓变宽
        if _res["src"] != "auction_quote":
            _rec["price_src"] = _res["src"]
            _rec["quote_time"] = _res["quote_time"]
        traded.append(_rec)
    # 账本：**只记有实际买入的交易日**（2026-09-14 用户「全量以 AI 为唯一口径」后与
    # 账户账本 account_nav_history.jsonl 同语义——那边由 nav_ledger 只收 can_enter ∧ covered
    # 的样本，0 成交不落行；此处若给空仓日写 0% 行，会让「记录天数」与胜率口径两轨打架）。
    avg = round(sum(t["return_pct"] for t in traded) / len(traded), 4) if traded else 0.0
    if traded:
        nav = round(_nav_now() * (1 + avg / 100.0), 6)
        _append_jsonl(AI_LEDGER, {
            "trade_date": tdate,
            "recommendation_date": sorted({str(r["decision_date"]) for r in due})[-1],
            "strategy": STRATEGY,
            "stock_count": len(traded),
            "avg_return_pct": avg,
            "nav": nav,
            "stocks": traded,
            "skipped": [{"code": c, "note": n} for c, n in skipped],
            "decided": len(todo),
            "not_picked": len(others),
            "ts": datetime.now(BJ_TZ).isoformat(),
        })

        # ── 📏 T+1 口径（2026-11-11）──────────────────────────────────────
        # 同一批成交票：竞价价买（tdate）→ **次一交易日收盘**卖。
        # ⛔ 独立文件 + 独立净值；次日行情没落盘就**整批不写**（不写半截、不补 0）。
        _nxt = _next_trade_day(tdate)
        _t1 = []
        for t in traded:
            _c = str(t["code"])
            _c1 = _close_of(_c, _nxt)
            if _c1 is None or not t["auction_price"]:
                _t1 = []
                break
            _t1.append({**t, "exit_date": _nxt, "exit_close": float(_c1),
                        "return_pct": round((float(_c1) - t["auction_price"]) / t["auction_price"] * 100, 2)})
        if _t1:
            _rows = _read_jsonl(AI_LEDGER_T1)
            _base = float(_rows[-1]["nav"]) if _rows else BASE_NAV
            _avg1 = round(sum(x["return_pct"] for x in _t1) / len(_t1), 4)
            _append_jsonl(AI_LEDGER_T1, {
                "trade_date": tdate,
                "recommendation_date": sorted({str(r["decision_date"]) for r in due})[-1],
                "strategy": STRATEGY_T1,
                "caliber": "竞价价买 → 次日收盘卖",
                "exit_date": _nxt,
                "stock_count": len(_t1),
                "avg_return_pct": _avg1,
                "nav": round(_base * (1 + _avg1 / 100.0), 6),
                "stocks": _t1,
                "ts": datetime.now(BJ_TZ).isoformat(),
            })
            print(f"[ai_ledger] T+1 口径已记 {len(_t1)} 只 → {_nxt} 收盘结算，均 {_avg1:+.2f}%")
        else:
            print(f"[ai_ledger] T+1 口径跳过（{_nxt} 收盘价未落盘）")
    else:
        nav = _nav_now()
        print(f"  AI 今日 0 成交（{len(skipped)} 只未买到/未选）→ 不写账本行（净值 {nav} 不变）")
    # 标记已结算（重写 decisions 文件·保留全部字段·due 全量=含未选中的票，防反复待结算）
    settled_keys = {(str(r["decision_date"]), str(r["code"])) for r in due}
    for r in rows:
        if (str(r.get("decision_date")), str(r.get("code"))) in settled_keys:
            r["settled"] = True
            r["settled_trade_date"] = tdate
            if str(r.get("code") or "") in settlement_blocks:
                r["settlement_note"] = settlement_blocks[str(r.get("code") or "")]
    DECISIONS.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"[ai_ledger] {tdate} 结算：AI 选中 {len(todo)} 只 → 成交 {len(traded)} 只·跳过 {len(skipped)} 只 | "
          f"avg={avg}% nav={nav}")
    for t in traded:
        print(f"    ✓ {t['name']}({t['code']}) 竞价{t['auction_price']} → 收盘{t['close']} = {t['return_pct']}%")
    for c, n in skipped:
        print(f"    – {c}: {n}")
    return 0


# ── 3) 状态 / 对比 ────────────────────────────────────────────────
def _stats(rows: list[dict]) -> dict:
    if not rows:
        return {"days": 0, "win_rate": None, "avg_return_pct": None, "nav": None}
    wins = sum(1 for r in rows if float(r.get("avg_return_pct") or 0) > 0)
    avg = sum(float(r.get("avg_return_pct") or 0) for r in rows) / len(rows)
    return {"days": len(rows), "win_rate": round(wins / len(rows) * 100, 1),
            "avg_return_pct": round(avg, 3), "nav": float(rows[-1].get("nav") or 0)}


def cmd_status(args) -> int:
    ai = _stats(_read_jsonl(AI_LEDGER))
    print(f"[AI 轨] 交易日 {ai['days']} | 胜率 {ai['win_rate']}% | 日均 {ai['avg_return_pct']}% | 净值 {ai['nav']}")
    pend = [r for r in _read_jsonl(DECISIONS) if not r.get("settled")]
    for r in pend:
        print(f"  待结算: {r['decision_date']} {r['name']}({r['code']}) — {str(r.get('reason'))[:50]}")
    return 0


def cmd_compare(args) -> int:
    days = int(args.days or 30)
    ai_rows = _read_jsonl(AI_LEDGER)[-days:]
    py_rows = _read_jsonl(PY_LEDGER)[-days:]
    a, p = _stats(ai_rows), _stats(py_rows)

    def _f(v, suffix="%"):
        # ⛔ AI 轨尚无结算行时（首日/任务漏跑）胜率与净值均为 None，
        #    直接 f-string 格式化会 TypeError 崩掉（2026-09-14 实测）→ 诚实显示「—」。
        return f"{v}{suffix}" if v is not None else "—"

    print(f"== AI 判断轨 vs Python 脚本轨（近 {days} 个结算日）==")
    print(f"  {'轨':<14}{'交易日':>6}{'胜率':>10}{'日均收益':>12}{'净值':>12}")
    print(f"  {'AI 判断（我）':<14}{a['days']:>6}{_f(a['win_rate']):>10}{_f(a['avg_return_pct']):>12}{_f(a['nav'], ''):>12}")
    print(f"  {'Python 脚本':<14}{p['days']:>6}{_f(p['win_rate']):>10}{_f(p['avg_return_pct']):>12}{_f(p['nav'], ''):>12}")
    if a["days"] == 0:
        print("  ℹ️ AI 轨还没有结算记录（首日要等当日 15:30 接力卡任务 settle 落首行）")
    if a["nav"] and p["nav"]:
        print(f"  → 净值比 AI/Python = {a['nav'] / p['nav']:.3f}")
    return 0


def cmd_research_input(args) -> int:
    """操盘手只读研究输入；读取冻结候选字段，不运行record/settle。"""
    from datetime import date as _date
    decision_date = _date.fromisoformat(args.decision_date).isoformat()
    payload = json.loads((CACHE / f"candidates-{decision_date}.json").read_text(encoding="utf-8"))
    picks = []
    for pool in ("relay", "watch"):
        for candidate in payload.get(pool, []):
            evidence = candidate.get("rsi_evidence")
            valid = (isinstance(evidence, dict) and evidence.get("schema") == "rsi_evidence_v1"
                     and evidence.get("code") == candidate.get("code")
                     and evidence.get("asof") == decision_date)
            picks.append({"pool": pool, "candidate": candidate,
                          "rsi_input_status": evidence.get("quality") if valid else "unavailable",
                          "rsi_evidence": evidence if valid else None})
    print(json.dumps({"schema": "operator_research_input_v1", "decision_date": decision_date,
                      "usage": "research_only", "environment": payload.get("environment"),
                      "picks": picks}, ensure_ascii=False, allow_nan=False))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="AI 判断平行台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    research = sub.add_parser("research-input", help="只读导出操盘手候选及RSI研究输入")
    research.add_argument("--decision-date", required=True)
    research.set_defaults(func=cmd_research_input)
    r = sub.add_parser("record"); r.add_argument("--decision-date"); r.add_argument("--file", required=True)
    r.add_argument("--trade-date", help="执行日（默认=decision_date 次一交易日）")
    # 2026-10-27：同日整体重判时用（池子规则改动后重跑），不留下上一版的幽灵票。
    r.add_argument("--replace", action="store_true",
                   help="整卡覆盖 AI 判断视图（不合并上一版 views）·同日重判用")
    r.set_defaults(func=cmd_record)
    s = sub.add_parser("settle"); s.add_argument("--trade-date", required=True); s.set_defaults(func=cmd_settle)
    st = sub.add_parser("status"); st.set_defaults(func=cmd_status)
    c = sub.add_parser("compare"); c.add_argument("--days", type=int, default=30); c.set_defaults(func=cmd_compare)
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
