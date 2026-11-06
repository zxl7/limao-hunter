#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐票「昨日涨停质量 → 今天竞价理论上该开多少」（2026-11-05 用户纠偏）。

用户原话：
  「我没有说固定涨幅区间，我说的是根据昨日的涨停的质量（时间，量能，封单大小）
    计算一个可能的竞价涨幅！大于 5% 不买倒是真的！」

⛔ 所以本模块**不设固定买入涨幅区间**。它只回答一件事：
  这只票昨天的封板时间 / 封单厚度 / 量能（换手）/ 炸板 / 板位，
  对应历史上今天 09:25 集合竞价**通常开在几个点**。
  `gap_vs_expected = 实际开幅 − 质量预期开幅` 才交给 `ai_caliber.gap_zone` 裁决 /
  `auction_state` 分层。
  ⚠️ 同日 17:00 用户后续又删掉「5% 硬线 + 必须红开」（即上方原话里的
     「大于 5% 不买倒是真的」已被推翻）：本模块本就**不含任何固定涨幅线**，
     删线后 E 直接成为「是否符合预期」的唯一基准 —— 强票高开、弱票低开都按
     各自 E 判，⛔ 不因 gap>5 或 gap≤0 另设门槛；唯一绝对线是竞价封死买不进。

## 模型（透明、可重算、无新依赖）

  预期开幅 = BASE + Σ 各质量分档的偏移，再 clip 到 [-5, +10]%
  偏移 = n/(n+40) × (该档历史竞价开幅中位数 − 全局中位数)   ← 小样本自动收缩

  六个质量因子，全部来自**昨收盘后已冻结**的归档，09:25 之前就存在：
    fbt      首封时刻（≤09:30 / ≤10:00 / ≤11:30 / ≤14:00 / 更晚）
    seal     封单/流通市值（<1% / 1~3% / 3~6% / ≥6%）
    hs       换手率（<3 / 3~8 / 8~15 / ≥15）——成交额/流通盘的代理
    zbc      炸板次数（0 / 1 / ≥2）
    boards   板数（1 / 2 / ≥3）
    is20     是否 20cm 标的

  参数训练窗口：2026-09-18 ~ 10-14（301 票次），样本外 10-15 ~ 11-04
  （700 票次）。样本为本地 `auction_quotes-<T>.json` 的 09:25 真实报价 ×
  前一交易日 `cache/zt_history/zt_<T-1>.json` 完整收盘涨停池。
  样本外 Spearman(预期, 实际) = 0.288，MAE 3.30pct；预测最高四分位
  实际开幅中位 4.20%，最低四分位 0.16%。

## ⚠️ 必须如实保留的反证

  `gap_vs_expected` 与收盘晋级率单调（样本外 −2% 以下 3.9% → +2% 以上 21.3%），
  但**控制绝对开幅后增量不稳**（0~2% 红开组内，超预期组反而 5.9% < 平预期 10.8%）。
  即：质量模型能预测「会开多高」，尚未证明它能独立于开幅预测「买了能不能晋级」。
  现有 AI `expectation.expected_range` 下沿与本模型相关性仅 0.117，
  说明盘后预期区间此前基本没用到昨日质量——本模块的直接价值是**把预期客观化**，
  ⛔ 不是已证明的胜率提升。晋级率 ≠ 买入胜率 ≠ 可执行收益。

## 只读边界

  纯函数：不读盘、不抓行情、不写缓存、不改候选、不产卡、不推送。
  与 `core/relay_forecast.py` 共用同一事实源（同一份昨收涨停归档），
  本模块只提供计算，⛔ 不新开预测链路。

开关：
  POOL_CALL_EXPECTED_GAP=0    全量回退：不算预期、不写字段、分层走 unknown 不裁决
"""

from __future__ import annotations

import math
import os
from typing import Any

SCHEMA = "quality_expected_gap_v1"

# 训练段全局中位数（%）
QUALITY_BASE = 1.083

# 收缩后的分档偏移（pct 点）。来源见模块 docstring，重算脚本见项目日志 11-05。
QUALITY_DELTAS: dict[str, dict[str, float]] = {
    "fbt": {"a<=0930": 1.576, "b<=1000": -0.070, "c<=1130": 0.286,
            "d<=1400": -0.528, "e>1400": -0.455},
    "seal": {"a<1": -0.281, "b1-3": 0.197, "c3-6": 2.049, "d>=6": 1.134},
    "zbc": {"a0": 0.211, "b1": 0.421, "c>=2": -0.674},
    "hs": {"a<3": 1.044, "b3-8": 0.059, "c8-15": 0.250, "d>=15": -0.969},
    "boards": {"a1": 0.089, "b2": -0.480, "c>=3": 1.650},
    "is20": {"10cm": -0.001, "20cm": 0.117},
}

E_MIN, E_MAX = -5.0, 10.0


def enabled() -> bool:
    """`POOL_CALL_EXPECTED_GAP=0` 逐字回退到「无质量预期」的 11-05 行为。"""
    return os.environ.get("POOL_CALL_EXPECTED_GAP", "1") != "0"


def _num(v: Any) -> float | None:
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _bucket(v: float | None, edges: tuple[float, ...],
            names: tuple[str, ...]) -> str | None:
    if v is None:
        return None
    for i, e in enumerate(edges):
        if v < e:
            return names[i]
    return names[-1]


def fbt_minutes(row: dict[str, Any]) -> float | None:
    """首封时刻 → 当日分钟数（09:30 = 570）。兼容四种既有字段名。"""
    m = _num(row.get("fbt_min"))
    if m is not None:
        return m
    hm = row.get("fbt_hm")
    if isinstance(hm, (int, float)) and not isinstance(hm, bool):
        h, mm = int(hm) // 100, int(hm) % 100
        return h * 60 + mm if 0 <= mm < 60 else None
    s = str(row.get("fbt") or "").strip()
    if len(s) >= 4 and s[:4].isdigit():
        h, mm = int(s[:2]), int(s[2:4])
        return h * 60 + mm if 0 <= mm < 60 else None
    return None


def seal_ratio_pct(row: dict[str, Any]) -> float | None:
    """封单/流通市值（%）。已是百分数的字段优先，缺失才用金额/市值现算。"""
    for k in ("seal_ratio", "seal_ratio_pct"):
        v = _num(row.get(k))
        if v is not None:
            return v
    for amt_k, lt_k in (("seal_amt", "lt_mktcap"), ("seal", "lt_yi"),
                        ("seal_yi", "lt_yi")):
        amt, lt = _num(row.get(amt_k)), _num(row.get(lt_k))
        if amt is not None and lt is not None and lt > 0:
            return 100.0 * amt / lt
    return None


def quality_buckets(row: dict[str, Any] | None) -> tuple[dict[str, str], list[str]]:
    """返回 (可用分档, 缺失因子)。⛔ 缺字段不算命中、不猜、不补默认值。"""
    if not isinstance(row, dict):
        return {}, list(QUALITY_DELTAS)
    fm = fbt_minutes(row)
    sr = seal_ratio_pct(row)
    hs = _num(row.get("hs"))
    zbc = _num(row.get("zbc"))
    bd = _num(row.get("boards"))
    is20 = bool(row.get("is_20cm"))
    raw = {
        "fbt": _bucket(fm, (571, 601, 691, 841),
                       ("a<=0930", "b<=1000", "c<=1130", "d<=1400", "e>1400")),
        "seal": _bucket(sr, (1, 3, 6), ("a<1", "b1-3", "c3-6", "d>=6")),
        "zbc": None if zbc is None else ("a0" if zbc == 0 else "b1" if zbc == 1 else "c>=2"),
        "hs": _bucket(hs, (3, 8, 15), ("a<3", "b3-8", "c8-15", "d>=15")),
        "boards": None if bd is None else ("a1" if bd <= 1 else "b2" if bd == 2 else "c>=3"),
        "is20": "20cm" if is20 else "10cm",
    }
    used = {k: v for k, v in raw.items() if v is not None}
    missing = [k for k, v in raw.items() if v is None]
    return used, missing


def expected_gap_pct(row: dict[str, Any] | None) -> float | None:
    """逐票预期竞价开幅（%）。核心质量缺料 → None，让下游「不裁决」。

    ⛔ 用户点名的三要素「时间 / 量能 / 封单」至少要有两项可用才算得出预期；
       只有 `is20`（永远可由 code/标志推得）+ 板数不算质量证据，不许拿基础值蒙混。
    """
    buckets, _ = quality_buckets(row)
    core = [k for k in ("fbt", "seal", "hs") if k in buckets]
    if len(core) < 2:
        return None
    e = QUALITY_BASE + sum(QUALITY_DELTAS[k].get(v, 0.0) for k, v in buckets.items())
    return round(max(E_MIN, min(E_MAX, e)), 2)


def detail(row: dict[str, Any] | None) -> dict[str, Any]:
    """冻结件/卡面留痕用：预期值、命中分档、缺失因子、口径版本。"""
    buckets, missing = quality_buckets(row)
    e = expected_gap_pct(row)
    return {"expected_gap_schema": SCHEMA,
            "expected_gap_pct": e,
            "expected_gap_factors": buckets,
            "expected_gap_missing": missing}


def deviation(gap_pct: Any, expected: Any) -> float | None:
    """`gap_vs_expected`＝实际开幅 − 质量预期开幅；任一缺失 → None。"""
    g, e = _num(gap_pct), _num(expected)
    if g is None or e is None:
        return None
    return round(g - e, 2)


def band(gap_pct: Any, expected: Any) -> str:
    """相对预期的三档描述（只用于留痕，⛔ 不是买入指令）。"""
    d = deviation(gap_pct, expected)
    if d is None:
        return "unknown"
    if d > 0.5:
        return "above"
    if d >= -2.0:
        return "in_line"
    return "below"
