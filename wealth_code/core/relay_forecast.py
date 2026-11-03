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

1. **红开可买 **≠** 全部可成交**：`可执行` = gap ≤ 5%（剔除买不到的）；`红开可买` =
   `0 < gap ≤ 5`（现行「不接低开」纪律下真能下单的子集）。⛔ 混用会得出
   「封板越差越好」的荒谬结论。
2. **封板质量只预测「明天高开多少」，不预测「买了能赚多少」**：
   四条齐票晋级 63.9%，但红开可买平均 −2.28%——质量换来的高开被市场砍掉。
3. **缺失字段不算命中**：缺 `zbc` 曾被当「零炸板」白送一条（2026-11-02 修的真 bug）。

## 只读边界

不写账本、不排队列、不改评分/名额/仓位。只给候选挂展示字段 `forecast`，
并在 `pool_call` 里作为**可选**的第 ⑬ 道闸（`POOL_CALL_FORECAST_GATE`，默认关）。
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

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


# ── 单票推测 ────────────────────────────────────────────────────────
def forecast_one(row: dict, market_max: int, model: dict | None = None) -> dict:
    """单只候选的次日推测（纯函数·不改入参）。`row` 可以是候选 dict 或台账行。"""
    model = model if model is not None else build_model()
    boards = int(_num(row.get("boards")) or 0)
    gtt = row.get("gap_to_top")
    gap_to_top = int(gtt) if gtt is not None else max(0, int(market_max or 0) - boards)
    fcnt = factor_count(row)
    key = (pos_key(gap_to_top), "封板强(≥3)" if fcnt >= 3 else "封板弱(≤2)")
    g = (model.get("groups") or {}).get(key) or {}

    promo_board = ((model.get("by_boards") or {}).get(boards) or {}).get("promo")
    reasons: list[str] = []

    if gap_to_top >= 2:
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
        "note": "只预测「明早大概怎么开、这档历史胜率」，⛔ 不是买入指令",
    }


def annotate(rows: list[dict], market_max: int | None = None, *,
             model: dict | None = None, cutoff: str | None = None) -> list[dict]:
    """给候选原地挂 `forecast`（保持原顺序、不动其他字段、失败不打断主链）。"""
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
            c["forecast"] = forecast_one(c, mm, m)
        except Exception as e:                               # noqa: BLE001
            c["forecast"] = {"schema": SCHEMA, "error": repr(e)}
    return rows


# ── 展示（纯文本，颜色由调用方决定）──────────────────────────────────
def line_for(cand: dict) -> tuple[str, str]:
    """选股卡逐票一行 → (verdict, text)。无可推测时返回 ("", "")。"""
    f = cand.get("forecast") or {}
    if not f or f.get("error") or f.get("gap_median") is None:
        return "", ""
    v = str(f.get("verdict") or "")
    med = f.get("gap_median")
    p5 = f.get("p_over5")
    seg = [f"明早大概率开在 {med:+.1f}%"]
    if p5 is not None:
        seg.append(f"{int(p5)}% 概率高开过 5%（买不到）")
    if f.get("buy_win") is not None:
        seg.append(f"这档真买到时胜率 {f['buy_win']}%（{f.get('buy_n')} 笔）")
    return v, "｜".join(seg)


def summary_line(rows: list[dict]) -> str:
    """选股卡顶部一句汇总：明天值得等几只、几只根本买不到。无数据 → ""。"""
    fs = [r.get("forecast") or {} for r in rows or [] if isinstance(r, dict)]
    fs = [f for f in fs if f and not f.get("error") and f.get("gap_median") is not None]
    if not fs:
        return ""
    wait = sum(1 for f in fs if f.get("verdict") == VERDICT_WAIT)
    watch = sum(1 for f in fs if f.get("verdict") == VERDICT_WATCH)
    drop = sum(1 for f in fs if f.get("verdict") == VERDICT_DROP)
    hard = sum(1 for f in fs if (f.get("p_over5") or 0) >= 50)
    tail = f"；其中 {hard} 只多半高开过 5%（买不到）" if hard else ""
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
