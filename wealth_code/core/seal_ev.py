#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封板期望模型（2026-11-18 用户口径：「买的是当天能封住涨停的几率」）。

## 目标函数（唯一）

    期望 T+1 = P(封板) × 封板溢价(gap) + (1−P) × 未封损失(gap)

为什么不是「当日收益」：账本 19 日实测——

    封住涨停 62 笔：当日 +4.19%，**T+1 +6.85%**
    没封住  172 笔：当日 −1.76%，**T+1 −3.01%**
    ⇒ 钱全在「封板」上。

## 两个组成部分

1. **P(封板)**：逻辑回归，权重来自 `scripts/seal_prob_model.py` 的离线拟合
   （留一交叉验证 AUC **0.773**）。权重在这里**冻结**，⛔ 不在生产里训练。
2. **溢价/损失表**：按开盘幅度分档的经验均值（同样冻结，表格见 `PREM_TABLE`）。

## ⛔ 边界

- 只读字段：`gap`（竞价开幅）、`eod_score`（盘后分）、`mates`（同线家数）、`zbc`（炸板次数）；
- 全部来自已有候选/裁决数据，不引入新数据源；
- 缺字段 → 该分项按 0 处理（与训练口径一致），并在 `missing` 里留痕。
"""
from __future__ import annotations

import math
from typing import Any

# —— 冻结权重（来自 scripts/seal_prob_model.py，2026-11-18 拟合，19 日 234 条）——
# 输入量纲：gap/10、eod/100、mates/10、zbc/10，最后一维是 bias
W = {
    "gap": 2.762,
    "eod": 0.280,
    "mates": -0.252,
    "zbc": 0.828,
    "bias": -2.078,
}

# —— 按开幅分档的「封板者 T+1 / 未封者 T+1」经验均值（%）——
# 来源：19 日账本，见 架构/评分回放结论.md 第八节
PREM_TABLE_RAW: list[tuple[float, float, float, float]] = [
    # (下界, 上界, 封板溢价, 未封损失)
    (-999.0, -3.0, 21.22, -0.44),
    (-3.0, 0.0, 8.61, -2.84),
    (0.0, 2.0, 11.28, -1.81),
    (2.0, 5.0, 9.08, -5.94),
    (5.0, 8.0, 5.20, -3.13),
    (8.0, 999.0, 4.02, -10.08),
]

# 兜底（gap 缺失时）：整体均值
FALLBACK_PREM, FALLBACK_LOSS = 6.85, -3.01


def _pava(values: list[float], *, decreasing: bool) -> list[float]:
    """保序回归（PAVA）：把非单调的经验值压成单调序列。

    ⛔ 为什么必须单调：19 日样本里 「0~2% 封板溢价 +11.28%」反而高于「−3~0% 的 +8.61%」，
       这是小样本噪声。若不处理，策略会因一两个样本的抖动而改变取向；
       单调化只抹平非单调的噪声，不改整体方向。
    """
    vals = [-v for v in values] if decreasing else list(values)
    blocks: list[list[float]] = [[v, 1.0] for v in vals]     # [均值, 权重]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            total = blocks[i][1] + blocks[i + 1][1]
            mean = (blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / total
            blocks[i:i + 2] = [[mean, total]]
            i = max(0, i - 1)
        else:
            i += 1
    out: list[float] = []
    for mean, weight in blocks:
        out.extend([mean] * int(weight))
    return [-v for v in out] if decreasing else out


# —— 单调化后的生产表（溢价单调递减、未封损失单调下降）——
_prem = _pava([r[2] for r in PREM_TABLE_RAW], decreasing=True)
_loss = _pava([r[3] for r in PREM_TABLE_RAW], decreasing=True)  # 损失也是「越开越高越差」→ 单调递减
PREM_TABLE: list[tuple[float, float, float, float]] = [
    (PREM_TABLE_RAW[i][0], PREM_TABLE_RAW[i][1], round(_prem[i], 2), round(_loss[i], 2))
    for i in range(len(PREM_TABLE_RAW))
]


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def prem_loss(gap: float | None) -> tuple[float, float, str]:
    """按开幅取 (封板溢价, 未封损失, 分档标签)。"""
    if gap is None:
        return FALLBACK_PREM, FALLBACK_LOSS, "缺开幅"
    g = float(gap)
    for lo, hi, prem, loss in PREM_TABLE:
        if lo <= g < hi:
            return prem, loss, f"[{lo:g},{hi:g})"
    return FALLBACK_PREM, FALLBACK_LOSS, "越界"


def p_seal(*, gap: float | None, eod_score: float | None,
           mates: float | None = None, zbc: float | None = None) -> tuple[float, list[str]]:
    """封板概率（0~1）+ 缺失字段留痕。"""
    missing: list[str] = []
    x_gap = 0.0 if gap is None else _f(gap) / 10.0
    x_eod = 0.0 if eod_score is None else _f(eod_score) / 100.0
    x_mates = 0.0 if mates is None else _f(mates) / 10.0
    x_zbc = 0.0 if zbc is None else _f(zbc) / 10.0
    if gap is None:
        missing.append("gap")
    if eod_score is None:
        missing.append("eod_score")
    z = (W["gap"] * x_gap + W["eod"] * x_eod + W["mates"] * x_mates
         + W["zbc"] * x_zbc + W["bias"])
    try:
        p = 1.0 / (1.0 + math.exp(-z))
    except OverflowError:
        p = 0.0 if z < 0 else 1.0
    return round(p, 4), missing


def evaluate(*, gap: float | None, eod_score: float | None,
             mates: float | None = None, zbc: float | None = None) -> dict[str, Any]:
    """单只票的封板期望。返回 `{p_seal, prem, loss, ev, band, missing, why}`。"""
    p, missing = p_seal(gap=gap, eod_score=eod_score, mates=mates, zbc=zbc)
    prem, loss, band = prem_loss(gap)
    ev = p * prem + (1.0 - p) * loss
    return {
        "p_seal": p,
        "prem": prem,
        "loss": loss,
        "ev": round(ev, 2),
        "band": band,
        "missing": missing,
        "why": (f"封板 {p * 100:.0f}% × 溢价 {prem:+.1f}% "
                f"+ 未封 {100 - p * 100:.0f}% × {loss:+.1f}% = {ev:+.2f}%"),
    }
