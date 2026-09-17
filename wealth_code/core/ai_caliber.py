#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 口径层（2026-09-14 用户「全量以 AI 为唯一口径！」）。

背景：同一张竞价验证卡上曾并存两套买入口径 ——
  · 机械轨：`core/signal_eval.py` 按预期区间/量能/孤军门控判 buy → `can_enter`
  · AI 轨：AI 盘后对候选逐票下判断（participate / watch / skip + 自定入场上限 entry_max_gap）
用户 2026-09-14 先统一「卡面显示」，随后拍板「全量以 AI 为唯一口径」——本模块把
**账户净值账本（唯一真实战绩）的成交集合与入场规则**一并切到 AI 口径：

    成交 = AI 判 participate
         ∧ 当日竞价涨幅 ≤ 用户 5% 硬线（USER_ENTRY_MAX_GAP_PCT·2026-09-14 用户纪律）
         ∧ 该票当日有竞价数据（否则无从成交）
    ⛔ AI 自定 entry_max_gap 自 2026-09-14 起降级为留痕字段（ai_declared_limit），
       **不再参与裁决**——AI 决定「买不买」，5% 决定「什么价买得下手」。
    ⛔ 机械 signal_eval 结果降级为「参考信号」：仍照常渲染在卡面（✅ 机械参考段），
       但不再决定买卖与净值战绩。

三条不可违反的边界：
  1. **可逆**：`AI_CALIBER_ACCOUNT = False` → 一键回退机械口径（旧行为逐字不变）。
  2. **诚实降级**：该执行日无任何 AI 决策行（任务漏跑/首日上线）→ 自动回退机械口径，
     并在 summary 标 `caliber="mechanical_fallback"` + 打印显式告警。
     ⛔ 绝不静默按「零成交」结算——那会让净值无声冻结，用户看不出原因。
  3. **不改历史**：账本 append-only，仅影响本次结算的批次；历史行数值不变
     （nav_ledger 重算只按行内 avg_return_pct 复利，不回头改已记行）。

数据源与接缝：
  `wealth_code/cache/ai_decisions.jsonl`（由 `scripts/ai_ledger.py record` 写入，
  decision_date=T 盘后 · trade_date=T+1 执行）。历史行无 trade_date 字段 → 用
  `next_trade_day(decision_date)` 推导（与 pipeline_verify._next_day 同算法）。
  ⚠️ 该文件在 wealth_code/cache，与「AI 判断视图」(`狸猫猎手/cache/ai_view_*.json`)
      **不同目录**，勿混。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ── 开关（用户拍板「全量以 AI 为唯一口径」）────────────────────────────────
# True  = 账户账本 / 持仓 / 战绩统计口径 = AI 决策（本模块接管）
# False = 一键回退机械口径（signal_eval can_enter，旧行为）
AI_CALIBER_ACCOUNT = True

DEFAULT_ENTRY_MAX_GAP = 3.0     # AI 决策缺 entry_max_gap 时的兜底值（仅留痕·不参与裁决）

# ── 用户纪律硬线（2026-09-14 用户「AI 决定要买的，低于 5% 直接买」）──────────────
# 背景：AI 盘后决策自带 entry_max_gap（默认 3.0·可自降到 2.0）→ 一度把用户 2026-08-17
#   定下的「竞价高开 ≤5% 一律执行」纪律整个架空。实证（09-14 闽东电力 000993）：
#   AI 判 participate（=要买），却因**自己**设的 2% 把 +4.54% 的竞价拒掉；该票当日
#   涨停 +10.01%，账户白白错过 +5.24%。逻辑不自洽：AI 定方向，不该再用隐藏字段票否决自己。
# 现行规则（不可再犯）：**AI 只决定「买不买」；买价上限统一 = 用户 5% 硬线。**
#   AI 自定的 entry_max_gap 降级为留痕字段（ai_declared_limit），⛔ 不参与裁决。
#   与 signal_eval.CAUTION_GAP_PCT = 5.0 同源同值（用户 2026-08-17 定的同一条纪律）。
USER_ENTRY_MAX_GAP_PCT = 5.0
# 单票仓位上限（2026-09-14 用户「买入仓位不超过 50%」）——AI 给的操作建议仓位一律裁剪到此。
MAX_POSITION_PER_STOCK = 0.5

# ── 预期区间门（2026-09-17 用户「A 计划」）────────────────────────────────
# 背景：此前「卡面动态结论」与「账本成交」是**两套独立逻辑**——卡面看预期区间给三档，
#   账本只看 `participate ∧ ≤5%`，实测出现「卡面说继续观察、账本却买入」的自相矛盾。
# 方案 A（用户拍板）= 把卡面判定**下沉为执行条件**，二者共用 `gap_zone` 这**唯一判据源**：
#    成交 = AI 判 participate ∧ 竞价 ≤ 5% 硬线 ∧ 竞价落在预期区间（含容差）
# 用户口径：「高开太多（超预期）→ 观察」「不及预期 → 放弃」「符合预期 → 买」。
EXPECTED_ZONE_TOL = 0.5   # 预期区间容差（pp）：超上沿 / 破下沿此范围内仍算「符合预期」（防误杀）


def gap_zone(gap_pct: Any, expected_range: Any,
             tol: float = EXPECTED_ZONE_TOL) -> str:
    """竞价涨幅相对**盘后预期区间**的三档判定 —— **唯一判据源**（账本 + 卡面共用）。

    返回：'below' 低于预期（放弃）/ 'ok' 符合预期（可买）/ 'above' 超预期＝高开太多（观察）
          / 'unknown' 无预期区间或无竞价数据（调用方回退旧口径·不误伤）。
    ⛔ 本函数是「超预期/符合/低于预期」的唯一实现——任何地方不得再自写一套（防双轨脱节）。
    """
    if gap_pct is None or not (isinstance(expected_range, (list, tuple)) and len(expected_range) == 2):
        return "unknown"
    try:
        lo, hi, g = float(expected_range[0]), float(expected_range[1]), float(gap_pct)
    except (TypeError, ValueError):
        return "unknown"
    if g < lo - tol:
        return "below"
    if g > hi + tol:
        return "above"
    return "ok"

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
if str(ROOT) not in sys.path:                          # 允许独立运行
    sys.path.insert(0, str(ROOT))


def decisions_path() -> Path:
    """AI 决策台账路径（跟随 ROOT 计算，便于测试 monkeypatch 隔离）。"""
    return ROOT / "cache" / "ai_decisions.jsonl"


def next_trade_day(date10: str) -> str:
    """次一交易日（跳周末）——与 `pipelines/pipeline_verify._next_day` 同算法同口径。"""
    try:
        d = datetime.strptime(str(date10), "%Y-%m-%d") + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return str(date10)


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def load_ai_decisions(trade_date: str) -> dict[str, dict] | None:
    """读该执行日（trade_date=T+1）的 AI 决策 → {code: 决策行}。

    返回 None 表示**该日无任何 AI 决策**（缺失·非「AI 决定空仓」）——
    调用方必须据此回退机械口径并告警，不得当成零成交。
    返回 {} 表示有决策但都不适用（理论上不会出现；防御性保留）。
    """
    if not trade_date:
        return None
    rows = _read_jsonl(decisions_path())
    if not rows:
        return None
    hit: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("code") or "").strip()
        if len(code) != 6:
            continue
        # 优先用行内 trade_date；历史行缺失 → 由 decision_date 推导（同 next_day 算法）
        td = str(r.get("trade_date") or "").strip() or next_trade_day(str(r.get("decision_date") or ""))
        if td == str(trade_date):
            hit[code] = r
    return hit if hit else None


def view_label(view: Any) -> str:
    """AI 判断的白话标签（卡面/日志共用·禁机器黑话上卡）。"""
    return {"participate": "参与", "watch": "观察", "skip": "放弃"}.get(str(view or ""), "—")


def ai_declared_limit(decision: dict | None) -> float:
    """该票 **AI 自定**入场上限（entry_max_gap）——⛔ 仅留痕/展示，不参与裁决。"""
    try:
        v = (decision or {}).get("entry_max_gap")
        return float(v) if v is not None else DEFAULT_ENTRY_MAX_GAP
    except Exception:
        return DEFAULT_ENTRY_MAX_GAP


def entry_limit(decision: dict | None) -> float:
    """**生效**入场上限 = 用户 5% 硬线（2026-09-14 用户纪律·AI 自定值已失效）。

    保留 `decision` 入参只为向后兼容调用签名；返回值恒为 USER_ENTRY_MAX_GAP_PCT。
    """
    return USER_ENTRY_MAX_GAP_PCT


def clamp_position(p: Any) -> float | None:
    """单票仓位裁剪到 ≤ MAX_POSITION_PER_STOCK（2026-09-14 用户「买入仓位不超过 50%」）。

    非法/非正/缺失 → None（诚实留空，不编造仓位）。
    """
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return round(min(v, MAX_POSITION_PER_STOCK), 4)


def decide(decision: dict | None, gap_pct: float | None,
           expected_range: Any = None) -> tuple[bool, str, dict]:
    """按 AI 口径裁决单票能否成交（2026-09-14 纪律 ＋ 2026-09-17「A 计划」区间门）。

    入参：decision = AI 决策行（None=该票未被 AI 判断）；gap_pct = 当日竞价涨幅(%)；
         expected_range = 盘后预期区间 [lo, hi]（缺省 None → 回退旧口径·只按 5% 硬线）。
    返回：(can_enter, note, meta)

    裁决链（方案 A·**卡面 = 账本**）：
      AI 判 participate ∧ 有竞价数据
        ∧ 竞价 ≤ USER_ENTRY_MAX_GAP_PCT(5%) 硬线
        ∧ 竞价落在预期区间（`gap_zone`·含 EXPECTED_ZONE_TOL 容差）
      → 成交
    ⛔ AI 自定 entry_max_gap 仅留痕（meta.ai_declared_limit），不参与裁决。
    ⛔ 判据**唯一源 = `gap_zone`**（卡面 `build_cards._ai_dyn_verdict` 同调此函数·禁另写一套）。
    """
    base = {"ai_gap_limit": USER_ENTRY_MAX_GAP_PCT,
            "ai_declared_limit": ai_declared_limit(decision)}
    if decision is None:
        return False, "AI 昨日未对这只票下判断 → 不买", {"ai_view": None, **base}
    # ⚠️ 缺失 view 视为 participate（历史兼容·与 `ai_ledger.cmd_settle` 的 todo 筛选
    #    `r.get("view") or "participate"` 同口径）——09-11 前决策行无 view 字段，
    #    若此处严格判空 → 与结算层口径打架（同票「结算层算参与、裁决层判不买」）。
    view = str(decision.get("view") or "participate")
    if view != "participate":
        return False, f"AI 判「{view_label(view)}」→ 不买", {"ai_view": view or None, **base}
    if gap_pct is None:
        return False, "无竞价数据 → 不知道能不能买到，不买（诚实跳过）", {
            "ai_view": "participate", **base}
    _g = float(gap_pct)
    _zone = gap_zone(_g, expected_range)
    base["zone"] = _zone
    if _g > USER_ENTRY_MAX_GAP_PCT:
        return False, (f"竞价高开 {_g:+.2f}% 超过 {USER_ENTRY_MAX_GAP_PCT:g}%"
                       " → 不追（纪律：高开超 5% 的不买）"), {
            "ai_view": "participate", **base}
    if _zone == "below":
        return False, (f"竞价 {_g:+.2f}% 低于预期区间 → 放弃（不及预期·不接）"), {
            "ai_view": "participate", **base}
    if _zone == "above":
        return False, (f"竞价 {_g:+.2f}% 高于预期区间（高开太多）→ 观察（不追）"), {
            "ai_view": "participate", **base}
    return True, (f"AI 要买·竞价 {_g:+.2f}% 在预期区间内且 ≤{USER_ENTRY_MAX_GAP_PCT:g}%"
                  " → 买入"), {
        "ai_view": "participate", **base}
