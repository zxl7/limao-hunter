#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""9:25 竞价三态分层（2026-11-05 用户定调 + 同日纠偏）。

用户原话边界：
  「系统只需要竞价数据就判断后期走势，系统直接买入，
    其他的机会交给人工二次自己交易不做记录。」
  「我没有说固定涨幅区间，我说的是根据昨日的涨停的质量（时间，量能，封单大小）
    计算一个可能的竞价涨幅！大于 5% 不买倒是真的！」

⛔ **二次纠偏后的核心变化：删掉固定涨幅区间 A(3%~5%)。**
   分层不再看「开在百分之几」，而看「实际开幅 vs 这只票按昨日质量应该开多少」。
   逐票预期的唯一计算源是 `core/expected_gap.py`（纯函数，只用昨收归档质量字段）。

本模块把这句话落成**纯计算**的判据，⛔ 不读盘、不抓行情、不写缓存、不看盘中：

  · 唯一输入（09:25 之前就已存在的事前事实）：
      gap_pct           = 今晨竞价开幅（%）
      amount_yi         = 今晨 09:25 竞价成交额（亿元，⛔ 非 09:30 后累计额）
      prev_amount_yi    = 前一交易日全天成交额（亿元，昨收涨停归档 cje_yi）
      expected_gap_pct  = 昨日封板质量算出的「今天该开多少」（%）
  · R = amount_yi / prev_amount_yi × 100（%）
  · DEV = gap − expected（相对预期偏离，百分点）

三态（阈值全部可用环境变量覆盖，便于回滚与迭代）：
  A  DEV ≥ +0.5 且 R ≥ 3%   → 系统直接参与（仍须过全部既有闸）
  B  DEV ≥ −2.0 且 R ≥ 3%   → 人工二次机会，⛔ 系统不记录
  C  其余（明显低于预期 / 量不足 / 竞价封死）→ 排除
  unknown 开幅、竞价额、昨成交额、质量预期任一缺失 → **不裁决**，不猜、不当 C。

⛔ **2026-11-05 用户删除 5% 与红开两条绝对线**：`max_gap_pct` 语义由「5% 硬线」
   改为「该票涨停幅度（10 / 20）」，只用来判**竞价封死物理买不进**，
   ⛔ 不再因「高开超过 5%」或「没红开」把票判 C。强票该高开、弱票该低开。

实证（2026-11-05 本地 26 日 / 1001 票次，截至 11-04；训练段 301、样本外 700）：
  · 质量模型样本外 Spearman(预期, 实际开幅) 0.288；预测最高四分位实际开中位 4.20%、
    最低四分位 0.16% —— 昨日质量确实能预测「会开多高」。
  · gap≤5 内 DEV 与收盘晋级率单调：DEV<−2 为 3.9%，DEV>+2 为 21.3%；
    DEV>+0.5 且 R≥3 为 32/130＝24.6%（前 24.4%／后 24.7%），
    同期 gap≤5 且 R≥3 基准 12.9%。
  ⚠️ **反证**：控制绝对开幅后 DEV 增量不稳（0~2% 红开组内超预期 5.9% < 平预期 10.8%）；
     原冻结候选池 211 票次里新 A 仅 6/36＝16.7%，与全池基准 16.1% 几乎无差。
     ⛔ 晋级率 ≠ 买入胜率 ≠ 可执行收益；模型参数来自同一批历史，须逐日对照再评估。

⛔ 不实现「盘中待确认」：B 态不产生 pending_confirm、不读分时、不回流成交。
   B/C 的 `manual_only` 由 `pool_call` 在最终 view 确定后落，仅供下游拦截系统记录。

开关：
  POOL_CALL_AUCTION_STATE=0        全量回退（不标注、不排序、不拦截）
  POOL_CALL_EXPECTED_GAP=0         不算质量预期 → 全部 unknown，不裁决（pool_call 控制）
  POOL_CALL_A_STATE_ONLY=1         额外硬闸「只有 A 能系统买入」；**默认 0（影子）**
  POOL_CALL_A_STATE_PRIORITY=0     关掉名额排序的 A 态同分优先
  POOL_CALL_A_DEV_MIN / B_DEV_MIN / A_R_MIN / B_R_MIN  阈值覆盖

⚠️ **硬闸默认关（反证）**：21 个可核验交易日冻结候选里，旧链共 10 次参与、2 次晋级；
   旧的固定区间 A 只留 3 次且 0 晋级，被挡下的 7 次里含**全部 2 次晋级**
   （09-22 内蒙新华 603230、10-21 南威软件 603636）；改成相对预期后
   保留 4 次仍 0 晋级、挡下 6 次含全部 2 次晋级。⛔ 两种 A 定义都未证明能提升自有池命中。
   故：执行边界（只用竞价、人工机会不记录）**默认生效**；
   「只买 A」这条**额外硬闸默认影子**，逐日看 `meta.auction_state.filtered` 再决定。
"""

from __future__ import annotations

import math
import os
from typing import Any

STATE_A = "A"
STATE_B = "B"
STATE_C = "C"
STATE_UNKNOWN = "unknown"

PHYSICAL_LIMIT_TOL = 0.2     # 竞价价距涨停 0.2pct 内 → 物理买不进（与 ai_caliber 同值）

ROUTE_SYSTEM = "system"
ROUTE_MANUAL = "manual"
ROUTE_UNKNOWN = "unknown"


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default) != "0"


def enabled() -> bool:
    """总开关：`POOL_CALL_AUCTION_STATE=0` 逐字回退到 11-05 之前的行为。"""
    return _flag("POOL_CALL_AUCTION_STATE", "1")


def a_state_only() -> bool:
    """「系统只买 A」额外硬闸，默认 **关（影子）**。

    用户的执行边界（只用竞价、直接买、人工机会不记录）已由分层标注 +
    `manual_only` 下游闸 + 无盘中确认实现，**不需要靠这条硬闸**。
    本闸会把旧链参与票砍到只留 A，而自有候选池复核显示它挡掉了全部 2 次晋级
    （见模块 docstring）。故默认只标注、旧链照买，逐日看
    `meta.auction_state.filtered` 对照；确认有效再置 1。
    """
    return enabled() and os.environ.get("POOL_CALL_A_STATE_ONLY", "0") == "1"


def a_state_priority() -> bool:
    """名额受限时 A 态作为**同分优先键**（排在既有排序键之后，不推翻身位/龙一等证据）。"""
    return enabled() and _flag("POOL_CALL_A_STATE_PRIORITY", "1")


def _num(v: Any) -> float | None:
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _cfg(name: str, default: float) -> float:
    n = _num(os.environ.get(name))
    return default if n is None else n


def ratio_pct(amount_yi: Any, prev_amount_yi: Any) -> float | None:
    """R＝竞价成交额占昨日全天成交额的百分比；任一缺失/非正 → None。"""
    amt = _num(amount_yi)
    prev = _num(prev_amount_yi)
    if amt is None or prev is None or amt < 0 or prev <= 0:
        return None
    return 100.0 * amt / prev


def prev_amounts_from_rows(rows: Any) -> dict[str, float]:
    """从**昨日完整涨停池**行里取 cje_yi（亿元）→ {6位代码: 昨全天成交额}。

    ⛔ 调用方传入的是 09:25 前已冻结的昨收归档，不是当日盘中数据。
    ⛔ 缺字段/非法值直接跳过，让该票走 unknown，不编造分母。
    """
    out: dict[str, float] = {}
    if not isinstance(rows, (list, tuple)):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip().zfill(6)
        amt = _num(row.get("cje_yi"))
        if code and code != "000000" and amt is not None and amt > 0:
            out[code] = amt
    return out


def classify(*, gap_pct: Any, amount_yi: Any, prev_amount_yi: Any,
             expected_gap_pct: Any = None,
             max_gap_pct: float = 5.0) -> dict[str, Any]:
    """单票竞价分层。返回可整段塞进冻结件 views[code] 的扁平字段。

    ⛔ 分层看「实际开幅 − 逐票质量预期」，⛔ 不看固定涨幅区间。
    `expected_gap_pct` 由 `core/expected_gap.py` 从昨收归档现算；
    缺它 = unknown 不裁决，⛔ 不退回固定区间、不猜。
    `max_gap_pct` 由调用方传 `ai_caliber.limit_up_pct(is_20cm)`（10 / 20），
    只用于判「竞价封死买不进」，⛔ 不再是 5% 高开硬线。
    """
    gap = _num(gap_pct)
    ratio = ratio_pct(amount_yi, prev_amount_yi)
    exp = _num(expected_gap_pct)
    cap = _num(max_gap_pct)
    cap = 10.0 if cap is None or cap <= 0 else cap   # 调用方传 10 / 20（涨停幅度）
    dev = round(gap - exp, 2) if gap is not None and exp is not None else None

    def _row(state: str, route: str, reason: str) -> dict[str, Any]:
        return {"auction_state": state,
                "auction_r": round(ratio, 2) if ratio is not None else None,
                "auction_route": route,
                "expected_gap_pct": exp,
                "gap_vs_expected": dev,
                "auction_state_reason": reason}

    if gap is None or ratio is None:
        return _row(STATE_UNKNOWN, ROUTE_UNKNOWN,
                    "竞价开幅或昨全天成交额缺失 → 不裁决")
    # 唯一保留的绝对线：**竞价封死买不进**（用户既定「一字板不参与」）。
    # ⛔ 不是「高开太多」：删 5% 后强票高开只要符合它自己的预期照样进 A。
    if gap >= cap - PHYSICAL_LIMIT_TOL:
        return _row(STATE_C, ROUTE_MANUAL,
                    f"竞价 {gap:+.2f}% 已封在涨停附近 → 买不进")
    if exp is None or dev is None:
        return _row(STATE_UNKNOWN, ROUTE_UNKNOWN,
                    "昨日涨停质量缺料，算不出逐票预期开幅 → 不裁决")

    a_dev = _cfg("POOL_CALL_A_DEV_MIN", 0.5)
    # 与主链 ai_caliber.EXPECTED_GAP_DEV_MIN 共用一个环境变量，避免两处阈值漂移。
    b_dev = _cfg("POOL_CALL_B_DEV_MIN",
                 _cfg("POOL_CALL_EXPECTED_DEV_MIN", -2.0))
    a_r = _cfg("POOL_CALL_A_R_MIN", 3.0)
    b_r = _cfg("POOL_CALL_B_R_MIN", 3.0)
    if dev >= a_dev and ratio >= a_r:
        return _row(STATE_A, ROUTE_SYSTEM,
                    f"开 {gap:+.2f}%，按昨日质量该开 {exp:+.2f}%，"
                    f"超预期 {dev:+.2f}pct；竞价额占昨全天 {ratio:.1f}% ≥ {a_r:g}%")
    if dev >= b_dev and ratio >= b_r:
        return _row(STATE_B, ROUTE_MANUAL,
                    f"开 {gap:+.2f}%，该开 {exp:+.2f}%，偏离 {dev:+.2f}pct → 人工二次机会")
    return _row(STATE_C, ROUTE_MANUAL,
                f"开 {gap:+.2f}%，该开 {exp:+.2f}%，低于预期 {dev:+.2f}pct "
                f"或竞价额仅占昨全天 {ratio:.1f}%")


def system_buy_eligible(mark: dict[str, Any] | None) -> bool:
    """A 态硬闸下是否仍允许系统买入。

    ⚠️ `unknown` 返回 True —— 沿用「缺数据不裁决」纪律：不因昨日归档缺料
       而把本来达标的票悄悄踢出系统。⛔ 绝不把 unknown 当 dry/量不足。
    """
    state = str((mark or {}).get("auction_state") or STATE_UNKNOWN)
    return state in (STATE_A, STATE_UNKNOWN)


def manual_only_of(mark: dict[str, Any]) -> bool:
    """最终冻结件落 `manual_only`：只有「人工路线 ∧ 最终不是系统参与」才拦。

    ⛔ 参与票永远 False —— 否则会二次否决 11-05 刚打通的竞价复核翻案链。
    """
    return (str(mark.get("auction_route") or "") == ROUTE_MANUAL
            and str(mark.get("view") or "") != "participate")
