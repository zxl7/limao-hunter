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
from datetime import datetime
from pathlib import Path
from typing import Any

# core 既可能由财富密码 CLI 导入，也可能由项目根脚本导入；日历在项目根 common/。
HUNTER_ROOT = Path(__file__).resolve().parents[2]
if str(HUNTER_ROOT) not in sys.path:
    sys.path.insert(0, str(HUNTER_ROOT))
from common.trading_calendar import next_a_share_trading_day

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

# ── 量能门（2026-09-17 用户「带量高开＝转强」·无量高开＝没有真实资金托底）────────
# AI 判「参与」后，此前**不看竞价量能**（量能只在机械参考层）。用户重视「带量高开」，
# 故把量能纳入 AI 口径：**红开但量能 < 目标×VOLUME_WEAK_RATIO → 降级「观察」**。
# ⛔ 仅拦「红开无量」（诱多典型）；绿开/平开本就不买，不受此门影响；无目标/无数据 → 不判（宽容）。
VOLUME_WEAK_RATIO = 0.7

# ── hibernate 竞价逃生阀（2026-09-18 用户拍板「A」·自评实证）────────────────────
# 背景（`scripts/ai_self_review.py` 自评）：09-16、09-18 两个 hibernate 日全空仓（0 参与），
#   而两个执行日的候选池均收益 **+2.57% / +1.03%**（机会成本合计 +3.60%）。
#   根因=stance 由**依据日盘后**用「昨涨停今晋级率」这类**滞后指标**一次性定死 →
#   退潮末端必然判 hibernate，而次日恰恰是修复日（龙头战法「退潮末期→修复」经典窗口）。
# 规则（宁严勿松·全满足才放 1 只）：hibernate 日不主动买，但若执行日竞价出现
#   「红开且 ≤ 5% 硬线 ∧ 带量（≥目标 × VOLUME_WEAK_RATIO）∧ 方向内位置最高的那只」
#   → 允许**小仓试错**（HIBERNATE_VALVE_POSITION），仍不解除其余四类硬伤。
# ⛔ 可逆（架构改动必留开关）：`HIBERNATE_VALVE_ENABLED = False` → 逐字回到
#   「hibernate 绝对空仓」旧行为；账本 append-only，改开关不动历史行。
# ⛔ 唯一判据源：本函数是阀门的唯一实现，verify / ai_ledger / 卡面必须同调，禁另写一套。
HIBERNATE_VALVE_ENABLED = True
HIBERNATE_VALVE_POSITION = 0.2          # 试错仓位（≤ MAX_POSITION_PER_STOCK）
HIBERNATE_VALVE_MAX_PER_DAY = 1         # 每日最多 1 只
HIBERNATE_VALVE_STANCES = ("hibernate",)  # 仅 hibernate 开阀（ice 仍绝对空仓·见 skill §3）
HIBERNATE_VALVE_MIN_GAP = 0.5           # 转强下沿：低于此视为「平开」不算转强（0.5~5% 才是带量转强）
# 只对 AI 判「观察」的票开阀：AI 判「放弃」= 语义更重的否决（纯跟风/传闻等）→ 阀门不越权放行。
HIBERNATE_VALVE_VIEWS = ("watch",)


def gap_zone(gap_pct: Any, expected_range: Any,
             tol: float = EXPECTED_ZONE_TOL) -> str:
    """竞价涨幅的「高开太多 / 符合 / 不及预期」三档判定 —— **唯一判据源**（账本 + 卡面共用）。

    ⚠️ **2026-09-17 A2 修订**（用户「高开太多＝绝对高开幅度」·修误杀）：
      原判据「超**预期区间上沿**就观察」受 `expectation_rules.max_range_width = 2.0` 钳制
      —— 区间只有 2pp 宽（如 [1.4,3.4]），「超预期」门槛实际落在 **2~4%**，把 3.6% 这类
      **≤5% 的合理高开**也拦成「观察」（实证 09-17：北自科技 +3.81%、万向德农 +3.59%
      被误杀 = 14% 误杀率）。现改**绝对线**：
        · 'above'（高开太多·观察）= gap > `USER_ENTRY_MAX_GAP_PCT`(5%)  ← 用户 5% 硬线
        · 'below'（不及预期·放弃）= gap < 区间**下沿** lo - tol
        · 'ok'    = 其余 → 可买
      区间**上沿（hi）不再参与** above 判定（只取 lo 判「低于预期」）。

    返回 'below' / 'ok' / 'above' / 'unknown'（无竞价数据时 unknown）。
    ⛔ 本函数是唯一实现——任何地方不得再自写一套（防账本/卡面双轨脱节）。
    """
    if gap_pct is None:
        return "unknown"
    try:
        g = float(gap_pct)
    except (TypeError, ValueError):
        return "unknown"
    # ① 高开太多（绝对线·不依赖预期区间——区间偏窄时不再误杀合理高开）
    if g > USER_ENTRY_MAX_GAP_PCT:
        return "above"
    # ② 不及预期（需预期区间下沿）
    lo = None
    if isinstance(expected_range, (list, tuple)) and len(expected_range) == 2:
        try:
            lo = float(expected_range[0])
        except (TypeError, ValueError):
            lo = None
    if lo is not None and g < lo - tol:
        return "below"
    return "ok"

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
if str(ROOT) not in sys.path:                          # 允许独立运行
    sys.path.insert(0, str(ROOT))


def decisions_path() -> Path:
    """AI 决策台账路径（跟随 ROOT 计算，便于测试 monkeypatch 隔离）。"""
    return ROOT / "cache" / "ai_decisions.jsonl"


def next_trade_day(date10: str) -> str:
    """次一A股交易日；与 `pipeline_verify._next_day` 共享法定休市日历。"""
    return next_a_share_trading_day(date10) or str(date10)


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
           expected_range: Any = None,
           auction_amount_yi: float | None = None,
           need_yi: float | None = None,
           *, valve_hit: bool = False) -> tuple[bool, str, dict]:
    """按 AI 决策结算单票；机械信号和逃生阀均不能放行未参与的标的。

    ``valve_hit`` 保留调用签名兼容，但不再拥有任何放行权限。只有 AI 的
    ``view=participate``，并同时通过竞价价格、预期区间和量能校验，才可成交。
    """
    base = {"ai_gap_limit": USER_ENTRY_MAX_GAP_PCT,
            "ai_declared_limit": ai_declared_limit(decision)}
    if decision is None:
        return False, "AI 昨日未对这只票下判断 → 不买", {"ai_view": None, **base}

    raw_view = str(decision.get("view") or "").strip()
    if not raw_view:
        # 新版盘后预案以 bias 表达意图；只有 focus 才是 AI 明确参与。
        raw_view = {"focus": "participate", "normal": "watch", "avoid": "skip"}.get(
            str(decision.get("bias") or "").strip(), "missing")
    if raw_view != "participate":
        return False, f"AI 判「{view_label(raw_view)}」→ 不买", {"ai_view": raw_view or None, **base}
    if gap_pct is None:
        return False, "无竞价数据 → 不知道能不能买到，不买（诚实跳过）", {
            "ai_view": raw_view, **base}

    gap = float(gap_pct)
    zone = gap_zone(gap, expected_range)
    base["zone"] = zone
    if zone == "above":
        return False, (f"竞价高开 {gap:+.2f}% 超过 {USER_ENTRY_MAX_GAP_PCT:g}% 上限（高开太多）"
                       " → 不追（纪律：高开超 5% 的不买）"), {"ai_view": raw_view, **base}
    if zone == "below":
        return False, (f"竞价 {gap:+.2f}% 低于预期区间 → 放弃（不及预期·不接）"), {
            "ai_view": raw_view, **base}
    if (auction_amount_yi is not None and need_yi and float(need_yi) > 0 and gap > 0
            and float(auction_amount_yi) < float(need_yi) * VOLUME_WEAK_RATIO):
        base["volume_weak"] = True
        return False, (f"竞价量能 {float(auction_amount_yi):.2f}亿 < 目标 {float(need_yi):.2f}亿"
                       f"×{VOLUME_WEAK_RATIO:g}（无量的高开）→ 观察·不追"), {
            "ai_view": raw_view, **base}
    return True, (f"AI 要买·竞价 {gap:+.2f}% 未超 {USER_ENTRY_MAX_GAP_PCT:g}% 上限、未低于预期"
                  " → 买入"), {"ai_view": raw_view, **base}


# ─────────────────────────────────────────────────────────────────────────────
# hibernate 竞价逃生阀（2026-09-18 用户拍板「A」）
# ─────────────────────────────────────────────────────────────────────────────
def ai_stance(trade_date: str, root: Path | None = None) -> str | None:
    """读该执行日的 AI 定调 stance（`cache/ai_view_{执行日}.json`·依据日盘后自定）。

    返回 'hibernate' / 'defend' / 'selective' / 'attack'；文件缺失/字段缺失 → None（不编造）。
    ⚠️ ai_view 在**项目根 cache/**（非 wealth_code/cache）→ 默认根取 ROOT.parent。
    """
    base = Path(root) if root else ROOT.parent
    p = base / "cache" / f"ai_view_{trade_date}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return str(d.get("stance") or "").strip() or None


def _flatten_candidates(candidates: Any) -> list[dict]:
    """候选池展平：支持 {relay:[], watch:[]} 与直接传 list（禁对同一只票重复计入）。"""
    if isinstance(candidates, dict):
        arr: list[dict] = []
        for k in ("relay", "watch", "early_ambush"):
            arr.extend([c for c in (candidates.get(k) or []) if isinstance(c, dict)])
        return arr
    if isinstance(candidates, list):
        return [c for c in candidates if isinstance(c, dict)]
    return []


def _valve_reason(c: dict, gap: float, boards: Any) -> str:
    """逃生阀放行理由（**大白话**·过 card_copy_rules 门禁：禁身位/梯队/龙一/研判等黑话）。

    ⏱️ 时点口径（2026-09-18）：定调是**昨日收盘**定的、竞价是**今日**开的 —— 两个时点必须分开写，
    ⛔ 禁把「昨日定的空仓」写成「今天是空仓日」（次日读卡会被读成当日结论）。
    """
    th = str(c.get("theme_name") or c.get("theme") or "").strip()
    mates = c.get("theme_mates_cnt")
    _b = f"{int(boards)}板" if isinstance(boards, (int, float)) and boards else "首板"
    _dir = f"{th}方向" if th else "这个方向"
    _mate = f"、同方向还有 {int(mates)} 家" if isinstance(mates, (int, float)) and mates else ""
    return (f"昨日收盘定的是空仓，但它今日开在 {gap:+.2f}% 并且有量，是{_dir}里板数最高的一只"
            f"（{_b}{_mate}）→ 只买一小笔试错")


def pick_hibernate_valve(candidates: Any, quotes: dict[str, dict] | None, *,
                         stance: str | None,
                         views: dict[str, Any] | None = None,
                         enabled: bool | None = None,
                         max_pick: int | None = None) -> dict | None:
    """hibernate 日「竞价逃生阀」：从候选池挑出**唯一 1 只**允许小仓试错的票。

    触发条件（**全满足**·宁严勿松）：
      ① 依据日 AI 定调 = hibernate（`HIBERNATE_VALVE_STANCES`；ice 不开阀）
      ② AI 对该票只判了「观察」（`HIBERNATE_VALVE_VIEWS`）——判「放弃」的不放行（不越权）
      ③ **带量转强**：竞价红开且 ≥ HIBERNATE_VALVE_MIN_GAP(0.5%)、≤ 5% 硬线；量能 ≥ 目标 × VOLUME_WEAK_RATIO
      ④ **方向内位置最高**：同 `theme_name` 里 `boards` 最高（并列取 `score` 高）
      ⑤ 非 ST（硬伤照旧）；每日最多 `HIBERNATE_VALVE_MAX_PER_DAY` 只

    views: {code: "watch"/"skip"/"participate"}；缺省 None → 不做 ② 过滤（向后兼容）。
    返回 {"code","name","gap_pct","boards","theme_name","position","reason"}；无合格者 → None。
    ⛔ 只读：不写账本、不改阈值；成交与否仍由 `decide(..., valve_hit=True)` 落在同一口径上。
    ⛔ 实测（2026-09-18 回测 2 个 hibernate 日）：09-16 抓到 +7.32%、09-18 抓到 -3.27%
      → 均值 +2.03%·**样本极小·非稳赚阀**，定位=「空仓日的试错通道」，故仓位压到 0.2。
    """
    if not (HIBERNATE_VALVE_ENABLED if enabled is None else bool(enabled)):
        return None
    if str(stance or "") not in HIBERNATE_VALVE_STANCES:
        return None
    cands = _flatten_candidates(candidates)
    if not cands or not quotes:
        return None
    try:
        from core.signal_eval import evaluate_realtime_signal
    except Exception:                                   # 独立运行兜底
        from signal_eval import evaluate_realtime_signal  # type: ignore

    pool: list[dict] = []
    for c in cands:
        code = str(c.get("code") or "").strip()
        if len(code) != 6:
            continue
        if views is not None and str(views.get(code) or "") not in HIBERNATE_VALVE_VIEWS:
            continue                                    # ② 只对「观察」开阀
        name = str(c.get("name") or "")
        if "ST" in name.upper():                        # ⑤ 硬伤照旧
            continue
        q = quotes.get(code)
        if not isinstance(q, dict):
            continue
        ap, pc = q.get("auction_price"), q.get("prev_close")
        try:
            gap = (float(ap) - float(pc)) / float(pc) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if not (HIBERNATE_VALVE_MIN_GAP <= gap <= USER_ENTRY_MAX_GAP_PCT):   # ③ 转强且不超硬线
            continue
        sig = evaluate_realtime_signal(c, q)
        need = float(sig.get("auction_amount_need_yi") or 0.0)
        amt = float(sig.get("auction_amount_yi") or 0.0)
        if need <= 0 or amt < need * VOLUME_WEAK_RATIO:  # ③ 带量
            continue
        pool.append({"code": code, "name": name, "gap_pct": round(gap, 2),
                     "boards": c.get("boards") or 0, "score": c.get("score") or 0.0,
                     "theme_name": str(c.get("theme_name") or c.get("theme") or "").strip(),
                     "cand": c})

    # ④ 方向内位置最高：同 theme_name 里 boards 最高（并列看 score）
    best_in_theme: dict[str, dict] = {}
    for x in pool:
        t = x["theme_name"]
        cur = best_in_theme.get(t)
        if cur is None or (x["boards"], x["score"]) > (cur["boards"], cur["score"]):
            best_in_theme[t] = x
    leaders = list(best_in_theme.values())
    if not leaders:
        return None
    leaders.sort(key=lambda x: (x["boards"], x["score"]), reverse=True)
    n = HIBERNATE_VALVE_MAX_PER_DAY if max_pick is None else int(max_pick)
    leaders = leaders[:max(0, n)]
    if not leaders:
        return None
    top = leaders[0]
    return {"code": top["code"], "name": top["name"], "gap_pct": top["gap_pct"],
            "boards": top["boards"], "theme_name": top["theme_name"],
            "position": HIBERNATE_VALVE_POSITION,
            "reason": _valve_reason(top["cand"], top["gap_pct"], top["boards"]),
            "candidates": [x["code"] for x in leaders]}
