#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""次日开盘推测 —— 选股卡 / 竞价卡共用的同一个模型（2026-11-02 立）

## 为什么放在 core 而不是新开一条链

2026-11-02 用户原话：「你要和我的原有的选股卡与竞价卡结合，不是新开一个新的链路和选股，
优化原有的选股逻辑+竞价推荐逻辑，才是王道」。

故本模块**不是**独立推送链路，只是既有两张卡的共用一个判据层：

| 谁用 | 什么时候 | 用它的什么 |
|---|---|---|
| 选股卡（15:30 `pipeline_eod`） | 盘后出候选时 | 给每只票挂 `forecast`：明早大概率开在几个点、几成概率买不到、这档真买到时的历史胜率 |
| 竞价卡（09:25 `pool_call`） | 竞价落地后 | 拿实测开盘落在哪一档 → 换算成这档的历史胜率，写进买入理由；`放弃` 档可拦（可逆开关） |

## 模型两段（全部来自 `cache/relay_review/latest.json` 的真实归档样本，⛔ 不写死数字）

1. **封板质量 + 身位 → 次日开盘幅度分布**
   ⛔ 身位是主变量，板数只是身位的代理：2 板在 6 板市场里必然是后排。
2. **开盘档 × 身位 → 纪律内可成交的胜率 / 期望**

## ⛔ 三条口径铁律（踩过的坑，别再犯）

1. **`0 < gap ≤ 5` 只是「红开小高开」这个统计子集，⛔ 不是「全部可成交」**：
   2026-11-05 用户删掉「5% 高开」这条绝对线后（「有些票今天很强势明天它就可以高开，
   有些票今天会弱势那么它低开一样能拉起来」），**买不到的唯一事实是竞价封死**
   （`gap ≥ 涨停幅度 − 0.2pct`），而低开是可以买的（由 `ai_caliber.gap_zone` 的
   `DEV = gap − E` 裁决）。本模块的 `buyable` 沿用 `0 < gap ≤ 5` **仅作统计子集**，
   卡面读数必须说清是「红开 0~5% 这档」的胜率，⛔ 不得再写成「买不到 / 买不进」。
   ⚠️ 若换成「未封死即算可买」，各档胜率会明显变化（差1板×弱封 62.5%→48.5%、
   差≥2板×弱封 41.2%→45.3%，n 从 8/51 变 33/161）——**尚未切换**，见 shared_rules。
2. **封板质量只预测「明天高开多少」，不预测「买了能赚多少」**：
   四条齐票晋级 63.9%，但红开可买平均 −2.28%——质量换来的高开被市场砍掉。
3. **缺失字段不算命中**：缺 `zbc` 曾被当「零炸板」白送一条（2026-11-02 修的真 bug）。

## 身位口径（11-06）

`verdict` 的「放弃」按**同题材**身位判（`RELAY_FORECAST_LINE_POS`，默认开），
⛔ 不再由全市场最高板一票否决 —— 跨题材没有传导关系。详见 `line_gap_of` 上方注释。

## 只读边界

不写账本、不排队列、不改评分/名额/仓位。只给候选挂展示字段 `forecast`，
并在 `pool_call` 里作为**可选**的第 ⑬ 道闸（`POOL_CALL_FORECAST_GATE`，默认关）。
"""
from __future__ import annotations

import json
import math
import os
import statistics
from pathlib import Path
from typing import Any

from core import expected_gap as _eg        # 11-05 用户纠偏：质量预期开幅，同一事实源

SCHEMA = "relay_forecast_v1"

# ── 阈值（⛔ 与 relay_review_30d / board_track 同源，不另立第二套）──────────────
EARLY_SEAL_HM = 930       # 09:30 之前封板算「早封」（与 defense_gate.DIST_FBT_LATE_HM 同量纲）
THICK_SEAL_RATIO = 3.0    # 封单/流通 ≥ 3% 算「厚封」
LOW_TURNOVER = 10.0       # 换手 < 10% 算「低换手」
CLIFF_BOARDS = 6          # 6 板以上晋级率断崖（6进7 实测 12.5%，n=8）
CLIFF_PROMO_MAX = 25.0    # 崖位晋级率上限：低于此只观察、不重仓
MIN_GROUP_N = 5           # 分组样本下限：不足则该组不给结论（⛔ 不拿 3 个样本下注）
WILSON_Z = 1.96

_SRC = Path(__file__).resolve().parents[2] / "cache/relay_review/latest.json"

BANDS = (("低开>3%", None, -3.0),
         ("低开0~3%", -3.0, 0.0),
         ("高开0~3%", 0.0, 3.0),
         ("高开3~5%", 3.0, 5.0),
         ("高开>5%", 5.0, None))

VERDICT_WAIT = "可等"
VERDICT_WATCH = "只观察"
VERDICT_DROP = "放弃"


# ── 小工具 ──────────────────────────────────────────────────────────
def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def wilson_lb(k: int, n: int, z: float = WILSON_Z) -> float | None:
    """胜率 Wilson 95% 下界（%）。小样本自动降权——「高胜率优先」下唯一诚实的排序键。"""
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    hw = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(max(0.0, c - hw) * 100, 1)


def seal_ratio_pct(row: dict) -> float | None:
    """封单/流通 %。三源择一（已是百分数的字段优先，缺失才现算），量纲统一为 %。"""
    v = _num(row.get("seal_ratio"))
    if v is not None:
        return v
    v = _num(row.get("seal_ratio_pct"))
    if v is not None:
        return v
    amt, lt = _num(row.get("seal_amt")), _num(row.get("lt_mktcap"))
    if amt is not None and lt and lt > 0:
        return amt / lt * 100.0
    # zt_history 口径：seal 亿元 / lt_yi 亿元
    amt, lt = _num(row.get("seal")), _num(row.get("lt_yi"))
    if amt is not None and lt and lt > 0:
        return amt / lt * 100.0
    return None


def fbt_hm(row: dict) -> int | None:
    """首封时刻 → HHMM 整数（09:30 = 930）。兼容 `fbt_min`（分钟数）与 `fbt`（HHMMSS）。"""
    v = row.get("fbt_hm")
    if isinstance(v, int):
        return v
    m = _num(row.get("fbt_min"))
    if m is not None:
        m = int(m)
        return (m // 60) * 100 + (m % 60)
    s = str(row.get("fbt") or "").strip()
    if len(s) >= 5 and s[:4].isdigit():
        hh, mm = int(s[:2]), int(s[2:4])
        if hh <= 23 and mm <= 59:
            return hh * 100 + mm
    return None


def factor_count(row: dict) -> int:
    """封板四因子命中数。⛔ 缺字段不算命中（缺 zbc 曾被白送一条，2026-11-02 已修）。"""
    n = 0
    hm = fbt_hm(row)
    if hm is not None and hm <= EARLY_SEAL_HM:
        n += 1
    sr = seal_ratio_pct(row)
    if sr is not None and sr >= THICK_SEAL_RATIO:
        n += 1
    hs = _num(row.get("hs"))
    if hs is not None and hs < LOW_TURNOVER:
        n += 1
    if row.get("zbc") is not None and int(row.get("zbc") or 0) == 0:
        n += 1
    return n


def pos_key(gap_to_top: Any) -> str:
    g = 0 if gap_to_top is None else int(gap_to_top)
    return "最高板" if g == 0 else ("差1板" if g == 1 else "差≥2板")


def band_of(gap: Any) -> str | None:
    g = _num(gap)
    if g is None:
        return None
    for name, lo, hi in BANDS:
        if (lo is None or g > lo) and (hi is None or g <= hi):
            return name
    return None


# ── 建模 ────────────────────────────────────────────────────────────
def load_samples(cutoff: str | None = None) -> list[dict]:
    """读归档样本。`cutoff`：只用该日期**之前**的样本（滚动回测防前视偏差）。"""
    try:
        d = json.loads(_SRC.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return []
    rows = d.get("samples") or []
    if cutoff:
        rows = [x for x in rows if str(x.get("date") or "") < str(cutoff)]
    return rows


def build_model(samples: list[dict] | None = None, *, cutoff: str | None = None) -> dict:
    """两段模型。返回 {groups, by_boards, meta}。样本不足的组直接不出（不给假结论）。"""
    rows = samples if samples is not None else load_samples(cutoff)
    if not rows:
        return {"groups": {}, "by_boards": {}, "meta": {"sample_n": 0}}

    buckets: dict[tuple[str, str], list[dict]] = {}
    for x in rows:
        key = (pos_key(x.get("gap_to_top")),
               "封板强(≥3)" if factor_count(x) >= 3 else "封板弱(≤2)")
        buckets.setdefault(key, []).append(x)

    groups: dict[tuple[str, str], dict] = {}
    for key, g in buckets.items():
        gaps = sorted(float(x["open_gap"]) for x in g if _num(x.get("open_gap")) is not None)
        if len(gaps) < MIN_GROUP_N:
            continue

        def q(p: float) -> float:
            return round(gaps[min(len(gaps) - 1, int(len(gaps) * p))], 2)

        cnt: dict[str, int] = {}
        for x in g:
            b = band_of(x.get("open_gap"))
            if b:
                cnt[b] = cnt.get(b, 0) + 1
        nb = len(gaps) or 1

        # ⛔ 「红开 0~5%」统计子集，**不是**全部可成交（11-05 删线后 5% 不再等于买不到；
        #    低开也能买）。保留此定义只为与历史归档口径可比，卡面须写明是这一档的胜率。
        buyable = [x for x in g
                   if 0 < (_num(x.get("open_gap")) or 0) <= 5
                   and x.get("relay_return") is not None]
        rets = [float(x["relay_return"]) for x in buyable]
        win = sum(1 for v in rets if v > 0)

        # 每个开盘档单独的历史胜率（竞价卡按「实测落在哪档」取用）
        by_band: dict[str, dict] = {}
        for name, lo, hi in BANDS:
            sub = [x for x in g
                   if _num(x.get("open_gap")) is not None
                   and (lo is None or _num(x["open_gap"]) > lo)
                   and (hi is None or _num(x["open_gap"]) <= hi)
                   and x.get("relay_return") is not None]
            r = [float(x["relay_return"]) for x in sub]
            if not r:
                continue
            w = sum(1 for v in r if v > 0)
            by_band[name] = {"n": len(r), "win": round(w / len(r) * 100, 1),
                             "win_lb": wilson_lb(w, len(r)),
                             "avg": round(statistics.mean(r), 2)}

        groups[key] = {
            "n": len(g), "n_gap": len(gaps),
            "gap_p25": q(.25), "gap_median": q(.5), "gap_p75": q(.75),
            "bands": {b: round(cnt.get(b, 0) / nb * 100) for b, _, _ in BANDS},
            "promo_rate": round(sum(1 for x in g if x.get("promoted")) / len(g) * 100, 1),
            "buy_n": len(rets),
            "buy_win": round(win / len(rets) * 100, 1) if rets else None,
            "buy_win_lb": wilson_lb(win, len(rets)) if rets else None,
            "buy_avg": round(statistics.mean(rets), 2) if rets else None,
            "buy_median": round(statistics.median(rets), 2) if rets else None,
            "by_band": by_band,
        }

    by_boards: dict[int, dict] = {}
    for b in range(2, 9):
        g = [x for x in rows if int(_num(x.get("boards")) or 0) == b]
        if len(g) < 3:
            continue
        by_boards[b] = {"n": len(g),
                        "promo": round(sum(1 for x in g if x.get("promoted")) / len(g) * 100, 1)}

    dates = sorted({str(x.get("date")) for x in rows if x.get("date")})
    return {"groups": groups, "by_boards": by_boards,
            "meta": {"sample_n": len(rows),
                     "window": {"start": dates[0] if dates else None,
                                "end": dates[-1] if dates else None}}}


# ── 同题材身位（11-06 用户拍板 A 的延伸）─────────────────────────────
# 用户原话：「有时候龙头，比如说板块之间非板块之间的切换，比如说你传媒板块你已经涨了
#   五板龙头了，但是光伏板块才一个板，这种身位差其实是没有影响的呀，它是走一个新的逻辑，
#   甚至说可以做一个资金的承接方向，你这种身位差估计只能在同一个板块里面适用适用，
#   不同板块之间它是没有关系的」
#
# ⇒ 选股卡这枚「放弃」标签原本由 `gap_to_top >= 2`（**全市场**最高板）触发，
#   与 11-06 上午刚改掉的 ⑪b 身位闸同源同病。35 日实测（`scripts/research_position_gap.py`
#   C 段 / `cache/research/position_gap_bt.json`）：
#
#   | 三桶 | 市场分母（旧） | 题材分母（新） |
#   |---|---|---|
#   | 最高板 | n=13 +1.77% / 53.8% | n=289 −1.10% / 40.1% |
#   | 差1板  | n=20 −2.89% / 35.0% | n=103 **+0.52% / 53.4%** |
#   | 差≥2板 | n=512 −0.93% / 41.8% | n=153 **−1.62% / 37.3%** |
#   | 区分度 | **0.6pct**（≈抛硬币） | **6.3pct** |
#
#   relay 自己的旧可买口径 `0<gap≤5` 下更极端：题材分母 保留 +50.9%(n=179) vs
#   放弃 32.8%(n=67) = **18.1pct**；市场分母「保留」只剩 n=7，不可用。
#
# ⛔ **不改统计模型本身**：`build_model` 的分组 key 仍是市场分母（归档样本 370 条只带
#   `sector`、没有题材，plates 快照仅近 15 天无法回填）。本开关只改 verdict 的**判据**，
#   `buy_win` 等数字来源不变。
RELAY_FORECAST_LINE_POS = os.environ.get("RELAY_FORECAST_LINE_POS", "1") != "0"


def line_gap_of(row: dict, theme_max_boards: int | None = None) -> tuple[int | None, int | None]:
    """同题材身位差 → (line_gap, theme_max_boards)。算不出 → (None, None)。

    取数优先级：`row["line_gap"]`（调用方已算）→ `row["theme_max_boards"]`
    → 入参 `theme_max_boards`。⛔ 缺题材就返回 None（不裁决），绝不退回行业 sector。
    """
    g = _num(row.get("line_gap"))
    if g is not None:
        return int(g), _num(row.get("theme_max_boards"))
    boards = int(_num(row.get("boards")) or 0)
    tm = _num(row.get("theme_max_boards"))
    if tm is None:
        tm = _num(theme_max_boards)
    if not boards or tm is None or tm < boards:
        return None, None
    return int(tm) - boards, int(tm)


# ── 单票推测 ────────────────────────────────────────────────────────
def forecast_one(row: dict, market_max: int, model: dict | None = None, *,
                 theme_max_boards: int | None = None,
                 line_pos: bool | None = None) -> dict:
    """单只候选的次日推测（纯函数·不改入参）。`row` 可以是候选 dict 或台账行。

    `theme_max_boards`：该票所属炒作题材昨日最高板（由调用方按选股宝 plates 算好注入）。
    """
    use_line = RELAY_FORECAST_LINE_POS if line_pos is None else bool(line_pos)
    model = model if model is not None else build_model()
    boards = int(_num(row.get("boards")) or 0)
    gtt = row.get("gap_to_top")
    gap_to_top = int(gtt) if gtt is not None else max(0, int(market_max or 0) - boards)
    line_gap, theme_max = line_gap_of(row, theme_max_boards)
    fcnt = factor_count(row)
    key = (pos_key(gap_to_top), "封板强(≥3)" if fcnt >= 3 else "封板弱(≤2)")
    g = (model.get("groups") or {}).get(key) or {}

    promo_board = ((model.get("by_boards") or {}).get(boards) or {}).get("promo")
    reasons: list[str] = []
    pos_basis = "line" if (use_line and line_gap is not None) else "market"

    if pos_basis == "line":
        # 同题材身位优先 —— 只有「在本题材里真是后排」才判放弃
        if line_gap >= 2:
            verdict = VERDICT_DROP
            reasons.append(
                f"{theme_max} 板是本题材最高、它 {boards} 板＝这条线第 {line_gap + 1} 档；"
                f"同题材身位差 ≥2 这档 35 日实测 −1.62%、胜 37.3%"
                f"（同题材差 1 板那档是 +0.52%/53.4%）")
        elif boards >= CLIFF_BOARDS and (promo_board or 0) < CLIFF_PROMO_MAX:
            verdict = VERDICT_WATCH
            reasons.append(f"{boards} 板是历史崖位：这档晋级率只有 {promo_board}%"
                           f"（{CLIFF_BOARDS} 板以上断崖）")
            if g.get("buy_win") is not None:
                reasons.append(f"即便买到，这组胜率 {g.get('buy_win')}%、平均 {g.get('buy_avg')}%")
        elif line_gap == 1:
            verdict = VERDICT_WAIT
            reasons.append(
                f"本题材最高 {theme_max} 板、它 {boards} 板＝只差 1 板（最快的一档）；"
                f"这档 35 日实测 +0.52%、胜 53.4%，是身位里最好的一档")
        else:                                   # line_gap == 0：自己就是这条线的锚
            verdict = VERDICT_WATCH
            reasons.append(
                f"它自己就是本题材最高板（{boards} 板）：这档 35 日实测 −1.10%、胜 40.1%，"
                f"不是不好，是没有「差 1 板」那档的确定性 → 先看不动")
    elif use_line:
        # 有开关但算不出题材（行业兜底/数据缺）→ ⛔ 不裁决，不拿全市场差否决
        if boards >= CLIFF_BOARDS and (promo_board or 0) < CLIFF_PROMO_MAX:
            verdict = VERDICT_WATCH
            reasons.append(f"{boards} 板是历史崖位：这档晋级率只有 {promo_board}%"
                           f"（{CLIFF_BOARDS} 板以上断崖）")
        else:
            verdict = VERDICT_WATCH
            reasons.append("缺题材归属（落行业兜底）→ 身位不裁决；"
                           f"全市场身位差 {gap_to_top} 板只是参照，不据此否决")
    elif gap_to_top >= 2:
        verdict = VERDICT_DROP
        reasons.append(f"身位差 {gap_to_top} 板＝后排；这档真买到时胜率只有 "
                       f"{g.get('buy_win')}%、平均 {g.get('buy_avg')}%")
        if boards <= 3 and int(market_max or 0) >= 4:
            reasons.append(f"{boards} 板在 {market_max} 板的市场里就是后排"
                           "（「2板进3板」只在市场最高≤3板时才有意义）")
    elif boards >= CLIFF_BOARDS and (promo_board or 0) < CLIFF_PROMO_MAX:
        verdict = VERDICT_WATCH
        reasons.append(f"{boards} 板是历史崖位：这档晋级率只有 {promo_board}%"
                       f"（{CLIFF_BOARDS} 板以上断崖）")
        if g.get("buy_win") is not None:
            reasons.append(f"即便买到，这组胜率 {g.get('buy_win')}%、平均 {g.get('buy_avg')}%")
    elif gap_to_top <= 1 and (g.get("buy_win_lb") or 0) >= 30:
        verdict = VERDICT_WAIT
        reasons.append(f"身位{'最高板' if gap_to_top == 0 else '差1板'}，"
                       f"这组真买到时胜率 {g.get('buy_win')}%（下界 {g.get('buy_win_lb')}%）、"
                       f"平均 {g.get('buy_avg')}%")
    else:
        verdict = VERDICT_WATCH
        reasons.append(f"这组真买到时胜率下界只有 {g.get('buy_win_lb')}%，样本不足以下注")

    return {
        "schema": SCHEMA,
        "boards": boards,
        "gap_to_top": gap_to_top,
        "line_gap": line_gap,
        "theme_max_boards": theme_max,
        "position_basis": pos_basis,
        "factor_count": fcnt,
        "group": f"{key[0]}·{key[1]}",
        "group_n": g.get("n"),
        "gap_median": g.get("gap_median"),
        "gap_p25": g.get("gap_p25"),
        "gap_p75": g.get("gap_p75"),
        "bands": g.get("bands") or {},
        "p_over5": (g.get("bands") or {}).get("高开>5%"),
        "promo_rate": g.get("promo_rate"),
        "promo_board": promo_board,
        "buy_n": g.get("buy_n"),
        "buy_win": g.get("buy_win"),
        "buy_win_lb": g.get("buy_win_lb"),
        "buy_avg": g.get("buy_avg"),
        "by_band": g.get("by_band") or {},
        "verdict": verdict,
        "reasons": reasons,
        # 🎯 11-05 用户纠偏：同一模型里再挂一份「按昨日封板质量，今天该开多少」。
        #    `gap_median` 是「身位×封板强弱」分组的历史开幅中位数（会到 9.98%，
        #    与 >5% 不买冲突）；`expected_gap_pct` 是六质量因子的收缩加性点估计，
        #    09:25 拿它跟实测开幅相减才是分层判据。⛔ 两者用途不同，不互相覆盖。
        "expected_gap_pct": _eg.expected_gap_pct(row),
        "expected_gap_factors": _eg.detail(row)["expected_gap_factors"],
        "note": "只预测「明早大概怎么开、这档历史胜率」，⛔ 不是买入指令",
    }


def annotate(rows: list[dict], market_max: int | None = None, *,
             model: dict | None = None, cutoff: str | None = None,
             theme_max_boards: dict[str, int] | None = None,
             theme_of: dict[str, str] | None = None,
             line_pos: bool | None = None) -> list[dict]:
    """给候选原地挂 `forecast`（保持原顺序、不动其他字段、失败不打断主链）。

    `theme_max_boards`：`{题材名: 昨日最高板}`（调用方按选股宝 plates 算）。
    `theme_of`：`{代码: 题材名}`（缺省读候选行自己的 `theme`/`core_theme`）。
    ⛔ 两者都缺 → 该票 `position_basis=market` → 不裁决，不退回行业 sector。
    """
    if not rows:
        return rows
    try:
        m = model if model is not None else build_model(cutoff=cutoff)
    except Exception as e:                                   # noqa: BLE001
        for c in rows:
            if isinstance(c, dict):
                c.setdefault("forecast", {"schema": SCHEMA, "error": repr(e)})
        return rows
    if not (m.get("groups")):
        return rows
    # 市场最高板：调用方没给就从这批候选现算（⛔ 不猜）
    mm = int(market_max or 0)
    if mm <= 0:
        mm = max((int(_num(c.get("boards")) or 0) for c in rows if isinstance(c, dict)), default=0)
    for c in rows:
        if not isinstance(c, dict):
            continue
        try:
            _code = str(c.get("code") or "")
            _th = (theme_of or {}).get(_code) or str(
                c.get("theme") or c.get("core_theme") or "").strip()
            # 缺字段时也把可算出的 line_gap 写回行内，卡面/账本可直接读同一事实
            _tm = (theme_max_boards or {}).get(_th) if (_th and theme_max_boards) else None
            c["forecast"] = forecast_one(c, mm, m, theme_max_boards=_tm, line_pos=line_pos)
        except Exception as e:                               # noqa: BLE001
            c["forecast"] = {"schema": SCHEMA, "error": repr(e)}
    return rows


# ── 展示（纯文本，颜色由调用方决定）──────────────────────────────────
def line_for(cand: dict) -> tuple[str, str]:
    """选股卡逐票一行 → (verdict, text)。无可推测时返回 ("", "")。

    ⛔ 11-05 用户删掉「5% 高开」这条绝对线后，「高开过 5%」**只是一个开幅分布事实**，
    不再是「买不到」。今天唯一算买不到的是**竞价封死**（≥涨停−0.2pct），那要等 09:25
    才有报价，盘后推不出来 → 所以这里只说分布，不贴买不到的结论。
    """
    f = cand.get("forecast") or {}
    if not f or f.get("error") or f.get("gap_median") is None:
        return "", ""
    v = str(f.get("verdict") or "")
    med = f.get("gap_median")
    p5 = f.get("p_over5")
    seg = [f"明早大概率开在 {med:+.1f}%"]
    if p5 is not None:
        seg.append(f"{int(p5)}% 概率高开过 5%")
    # ⛔ 样本不足不报胜率：「100.0%（3 笔）」这种读数会误导（MIN_GROUP_N 同源纪律）
    if f.get("buy_win") is not None and (f.get("buy_n") or 0) >= MIN_GROUP_N:
        seg.append(f"红开 0~5% 这档胜率 {f['buy_win']}%（{f.get('buy_n')} 笔）")
    return v, "｜".join(seg)


def summary_line(rows: list[dict]) -> str:
    """选股卡顶部一句汇总：明天值得等几只、几只根本买不到。无数据 → ""。

    ⛔ 「高开过 5%」只作开幅分布提示，不写「买不到」（5% 线 11-05 已废）。
    """
    fs = [r.get("forecast") or {} for r in rows or [] if isinstance(r, dict)]
    fs = [f for f in fs if f and not f.get("error") and f.get("gap_median") is not None]
    if not fs:
        return ""
    wait = sum(1 for f in fs if f.get("verdict") == VERDICT_WAIT)
    watch = sum(1 for f in fs if f.get("verdict") == VERDICT_WATCH)
    drop = sum(1 for f in fs if f.get("verdict") == VERDICT_DROP)
    high = sum(1 for f in fs if (f.get("p_over5") or 0) >= 50)
    tail = f"；其中 {high} 只多半高开过 5%" if high else ""
    return (f"🔮 明早：可等 {wait} 只 ｜ 只观察 {watch} 只 ｜ 放弃 {drop} 只{tail}")


def band_win_for(cand: dict, actual_gap: float | None) -> dict | None:
    """竞价卡用：拿**实测开盘**去查「这档历史胜率」。查不到 → None（⛔ 不编数字）。"""
    f = cand.get("forecast") or {}
    b = band_of(actual_gap)
    if not b:
        return None
    hit = (f.get("by_band") or {}).get(b)
    if not hit:
        return None
    return {"band": b, **hit}
