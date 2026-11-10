#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材周期相位判定（theme_phase_v1·2026-11-03）。

## 为什么要有这个模块

`theme_rotation.py` 的 `phase` 字段只有一个值 `re-entry-candidate`，语义是
「这题材曾经爆发过、冷却 ≥2 天、现在可能回来」——它回答的是**跨周期回流**
（旧题材复活），完全回答不了「**这波主线走到第几天了**」。

实证缺口（2026-11-03 盘中）：传媒 11-02 是 27 家、11-03 是 9 家，
在旧逻辑眼里两天都是 `boom`（`BOOM_ZT=3` 都满足）→ **高潮和首日启动长得一模一样**。
同一件事，第 1 天和第 4 天的操作是相反的，所以必须把「第几天」算出来。

## 相位定义（2026-11-03 调研定·32 交易日涨停池 + 44 天题材库实证）

| 相位 | 含义 | 判据（全部只看家数序列，不看价格） |
|---|---|---|
| `start` 启动 | 新线第一次冒头 | 今日是窗口峰值 ∧ ≥START_ZT 家 ∧ 此前 ≤START_PREV_MAX 家 |
| `ferment` 发酵 | 主升中段 | 峰值在前几天 ∧ 今日 ≥峰值×HOLD_RATIO ∧ 未创新高 |
| `climax` 高潮 | 家数最多那天 | 今日 = 窗口峰值 ∧ 已是第 ≥CLIMAX_MIN_DAY 天（启动过至少 2 天） |
| `diverge` 分歧 | 开始掉队 | 今日 < 前值×(1−DIVERGE_DROP) ∧ 仍 ≥峰值×DIVERGE_FLOOR |
| `reflow` 回流 | 分歧后资金回来 | 前一日处于分歧位 ∧ 今日 ≥前值×REFLOW_RISE ∧ ≥峰值×DIVERGE_FLOOR |
| `ebb` 退潮 | 线走完了 | 今日 < 峰值×EBB_FLOOR，或分歧后反抽未过前高再度下跌 |
| `dormant` 沉寂 | 没线 | 整窗 ≤DORMANT_ZT 家 |

## 三条铁律（每条都有实证支撑）

1. **家数不能单独当判据**。32 日四象限检验：家数高/低 × 晋级率高/低，
   四格的下一天晋级率分别是 20.7% / 25.8% / 19.2% / 22.5% ——**几乎没有差异**。
   所以本模块只回答「现在在第几天」，⛔ 绝不输出「能不能买」。
2. **分歧当天分不出回流还是退潮**。12 次大盘级分歧里 8 回流 / 2 退潮 / 2 横，
   而且炸板率、连板占比、一字数都区分不开（10-27 炸板率 68% 照样大回流）。
   → 分歧位的输出只给 **2/3 先验**，⛔ 不编方向。
3. **退潮的真信号是「反抽不过前高」**（机器人 10-23:40 → 10-27:14 → 10-28:24 → 10-29:6）。
   第一次腰斩不算退潮，所以 `ebb` 要求「已反抽过且仍掉」。

## 边界

- 纯函数、无 IO、无全局状态；调用方自己传序列。
- 家数序列允许 `None`（当日缺席 ≠ 0）。⛔ 缺数据不当 0，按「跳过」处理。
- 窗口长度建议 ≥8 个交易日；<5 天时置信度降为 `weak`。
- 可逆：调用方用 `ENABLE_THEME_PHASE` 控制是否写字段，本模块本身无开关。
"""

from __future__ import annotations

SCHEMA = "theme_phase_v1"

# —— 阈值（全部可调，但改之前先看上面的实证）——
START_ZT = 5            # 启动：今日 ≥5 家才算「一条线冒头」（3 家太容易被单票带动）
START_PREV_MAX = 2      #       且此前整窗最高不超过 2 家（否则是回流不是启动）
CLIMAX_MIN_DAY = 3      # 高潮：至少走到第 3 天才可能叫高潮（第 1 天永远是启动）
HOLD_RATIO = 0.70       # 发酵：今日家数 ≥ 峰值 70% 算「还在高位」
DIVERGE_DROP = 0.30     # 分歧：较前值掉 ≥30%
DIVERGE_FLOOR = 0.25    #       且仍 ≥ 峰值 25%（低于这条线是退潮不是分歧）
REFLOW_RISE = 1.15      # 回流：较分歧日回升 ≥15%
REFLOW_MIN_ZT = 4       # 回流：绝对家数下限（「1 家变 2 家」不是回流，是杂毛冒头）
REFLOW_NEAR_LOW = 1.3   # 回流：前一日须接近峰值后的低点（≤ 低点×1.3），
                        #       否则是「高位第一次松动」不是「分歧后回来」
EBB_FLOOR = 0.25        # 退潮：低于峰值 25%
EBB_RETRACE_CEIL = 0.50  # 反抽未过前高：当前仍不到峰值 50% → 判退潮（不是新一轮）
DORMANT_ZT = 2          # 沉寂：整窗 ≤2 家
MIN_DAYS_FULL = 8       # 满窗天数（不足则 quality=weak）

PHASE_CN = {
    "start": "启动", "ferment": "发酵", "climax": "高潮",
    "diverge": "分歧", "reflow": "回流", "ebb": "退潮", "dormant": "沉寂",
}

# 每个相位「默认允许做什么」——只给方向，不给票。⛔ 不是买卖建议。
PHASE_ACTION = {
    "start": "值得盯：第一天，位置最低，但还没证实是主线",
    "ferment": "主线中段：后面的票还跟得上，重点看这条线的龙一",
    "climax": "家数最多但质量最差：不追高，看核心封单有没有松",
    "diverge": "分歧位：2/3 概率回流，方向等次日竞价定，不在这里猜",
    "reflow": "回流确认：资金回来了，看是不是创新高（不创新高就是退潮）",
    "ebb": "线走完了：不参与，等下一次启动",
    "dormant": "没线：不看",
}


def _clean(series) -> list:
    """去掉尾部 None，内部 None 保留占位（用于算「第几天」的真实交易日跨度）。"""
    out = list(series or [])
    while out and out[-1] is None:
        out.pop()
    return out


def _values(series) -> list:
    return [v for v in series if v is not None]


def _idx_of_peak(series) -> int:
    """峰值位置（按序列下标，含 None 占位）。并列取最早。"""
    best_i, best_v = -1, None
    for i, v in enumerate(series):
        if v is None:
            continue
        if best_v is None or v > best_v:
            best_i, best_v = i, v
    return best_i


def judge(series, *, today: int | None = None) -> dict:
    """判定一条题材家数序列的当前相位。

    参数
      series  家数序列，按交易日升序，末项=今日。允许 None。
      today   今日家数；不传则取序列末项。
    """
    s = _clean(series)
    if not s:
        return _out("dormant", None, quality="empty", reason="无数据")

    if today is not None and (not s or s[-1] != today):
        s = s + [today]

    vals = _values(s)
    if not vals:
        return _out("dormant", None, quality="empty", reason="整窗无有效家数")

    cur = s[-1]
    if cur is None:
        return _out("dormant", None, quality="missing",
                    reason="今日家数缺失（缺席≠0）", peak=max(vals))

    peak = max(vals)
    pk_i = _idx_of_peak(s)
    # 「第几天」= 从**峰值所在这一波**的起点到今天经过的交易日数。
    # 起点定义：峰值往前找，直到家数跌回 START_PREV_MAX 以下（那之前属于上一波）。
    # ⚠️ 不能从末尾往前找：那样「今天只有 1 家」时会退化成人人都是第 1 天。
    start_i = 0
    for i in range(pk_i, -1, -1):
        v = s[i]
        start_i = i
        if v is not None and v <= START_PREV_MAX:
            start_i = i + 1
            break
    day_n = max(1, len(s) - start_i)

    prev = None
    for v in reversed(s[:-1]):
        if v is not None:
            prev = v
            break

    quality = "full" if len(vals) >= MIN_DAYS_FULL else "weak"
    base = dict(peak=peak, peak_idx=pk_i, prev=prev, cur=cur, days=len(vals))

    # ── 沉寂 ──
    if peak <= DORMANT_ZT:
        return _out("dormant", day_n, quality=quality,
                    reason=f"整窗最高 {peak} 家，没成线", **base)

    # ── 没成线：整窗峰值不到 START_ZT ──
    #    2026-11-03 实测踩到：白酒 峰 4 → 今 1 被判「退潮」，但 4 家本来就不构成一条线，
    #    标「退潮」只会在终端刷出一片假风险。≤DORMANT_ZT 已归沉寂，这里管 3~4 家。
    if peak < START_ZT:
        return _out("dormant", day_n, quality=quality,
                    reason=f"整窗最高 {peak} 家，没到 {START_ZT} 家门槛，本就没成线",
                    **base)

    ratio_peak = cur / peak if peak else 0
    peak_in_past = pk_i < len(s) - 1
    tail = [v for v in s[pk_i + 1:] if v is not None]     # 峰值之后的点
    tail_min = min(tail) if tail else None
    tail_max = max(tail) if tail else None

    # ── 退潮 A：已明显低于峰值 ──
    #    ⚠️ 与判据 B 同一条铁律：峰值后必须已有 ≥2 个点才叫退潮。
    #    2026-11-03 实测踩到：短剧 29→6（21%）、AI应用 7→0（0%）全是峰值后第 1 天，
    #    被 A 直接判退潮 —— 而当天只看得见「掉下来」，看不见「反抽不过前高」，
    #    下午的退潮卡会让用户在回流日错过。这就是自己文档里写了却没落实的那条。
    if ratio_peak < EBB_FLOOR and len(tail) >= 2:
        return _out("ebb", day_n, quality=quality,
                    reason=f"今日 {cur} 家仅为峰值 {peak} 家的 {ratio_peak*100:.0f}%",
                    **base)

    # ── 第一次腰斩：峰值后第 1 天就掉到 25% 以下 ──
    #    只给「分歧」，方向等次日反抽定（先验 2/3 回流）。
    if ratio_peak < EBB_FLOOR and len(tail) < 2:
        return _out("diverge", day_n, quality=quality,
                    reason=f"今日 {cur} 家仅为峰值 {peak} 家的 {ratio_peak*100:.0f}%，"
                           f"第 1 天掉下来 —— 是分歧还是退潮，等明日反抽定",
                    **base)

    # ── 退潮 B：**反抽不过前高**（2026-11-03 调研的核心判据）
    #    机器人 40 → 27 → 14 → 24 → 6 就是这条：第一次腰斩不算退潮，反抽没创新高才算。
    #    ⚠️ 必须要求峰值后有 ≥2 个点：只有 1 个尾部点时那是「第一次腰斩」= 分歧，
    #    不是退潮（传媒 11-03 27→9 就是这种，判成退潮会提前喊线走完）。
    if (peak_in_past and tail_max is not None and len(tail) >= 2
            and tail_max < peak and ratio_peak < EBB_RETRACE_CEIL):
        return _out("ebb", day_n, quality=quality,
                    reason=f"峰值 {peak} 家已过，反抽最高只到 {tail_max} 家、今日 {cur} 家"
                           f"（峰值的 {ratio_peak*100:.0f}%）",
                    **base)

    # ── 回流：峰值在过去 ∧ 前一日接近低点 ∧ 今日明显回升 ∧ 家数够 ──
    if (peak_in_past and tail_min is not None and prev is not None
            and prev <= tail_min * REFLOW_NEAR_LOW
            and cur >= prev * REFLOW_RISE
            and cur >= max(REFLOW_MIN_ZT, peak * DIVERGE_FLOOR)):
        out = _out("reflow", day_n, quality=quality,
                   reason=f"{prev} → {cur} 家，分歧后回升 {(cur/prev-1)*100:+.0f}%"
                          f"（峰值 {peak} 家的 {ratio_peak*100:.0f}%）",
                   **base)
        if ratio_peak < 0.60:
            out["is_retrace_fail"] = True
            out["action"] = "弱回流：还没回到前高的六成，别当第二轮做"
        return out

    # ── 分歧：较前值掉 ≥30%，仍在峰值 25% 之上 ──
    if prev is not None and prev > 0 and cur <= prev * (1 - DIVERGE_DROP):
        return _out("diverge", day_n, quality=quality,
                    reason=f"{prev} → {cur} 家，掉 {(1-cur/prev)*100:.0f}%，"
                           f"仍在峰值 {peak} 家的 {ratio_peak*100:.0f}%",
                    **base)

    # ── 今日 = 窗口峰值：≥第 3 天算高潮，第 1~2 天算启动 ──
    # ⚠️ 必须覆盖「家数持平在峰值」的情况（如 [0,0,22,22]）：旧写法
    #    climax 卡 day_n>=3、start 卡 hist<=2，两边都不命中 → 掉进兜底被标「分歧」，
    #    而它今天明明是最高家数、根本没跌。2026-11-03 AI应用实测踩到。
    if pk_i == len(s) - 1:
        if day_n >= CLIMAX_MIN_DAY:
            return _out("climax", day_n, quality=quality,
                        reason=f"今日 {cur} 家 = 窗口峰值，已是第 {day_n} 天",
                        **base)
        return _out("start", day_n, quality=quality,
                    reason=f"家数 {cur} 家是窗口最高，第 {day_n} 天"
                           + ("（家数不足 " + str(START_ZT) + " 家，算弱启动）"
                              if cur < START_ZT else ""),
                    **base)

    # ── 发酵：峰值在前几天，今日仍在高位 ──
    if ratio_peak >= HOLD_RATIO:
        return _out("ferment", day_n, quality=quality,
                    reason=f"今日 {cur} 家 = 峰值的 {ratio_peak*100:.0f}%，仍在高位",
                    **base)

    # ── 兜底：既不是高位也不是分歧 → 按「走弱中」归分歧，不给 ferment 的乐观措辞
    return _out("diverge", day_n, quality=quality,
                reason=f"家数 {cur}（峰值 {peak} 的 {ratio_peak*100:.0f}%）"
                       f"既未回到高位也未跌破退潮线，按走弱处理",
                **base)


def _out(phase, day_n, *, quality=None, reason="", peak=None, peak_idx=None,
         prev=None, cur=None, days=None) -> dict:
    return {
        "schema": SCHEMA,
        "phase": phase,
        "phase_cn": PHASE_CN.get(phase, phase),
        "day_n": day_n,
        "peak": peak,
        "peak_idx": peak_idx,
        "prev": prev,
        "cur": cur,
        "days": days,
        "quality": quality,
        "reason": reason,
        "action": PHASE_ACTION.get(phase, ""),
    }


def line(rows) -> str:
    """一行汇总（给卡面/终端用）：只报非沉寂的相位分布。"""
    rows = [r for r in (rows or []) if r and r.get("phase")]
    if not rows:
        return ""
    cnt: dict = {}
    for r in rows:
        cnt[r["phase_cn"]] = cnt.get(r["phase_cn"], 0) + 1
    order = ["启动", "发酵", "高潮", "分歧", "回流", "退潮", "沉寂"]
    parts = [f"{k} {cnt[k]}" for k in order if cnt.get(k)]
    return "｜".join(parts)


def judge_many(mapping: dict) -> dict:
    """批量判定：{题材名: 家数序列} → {题材名: 判定结果}。"""
    return {k: judge(v) for k, v in (mapping or {}).items()}
