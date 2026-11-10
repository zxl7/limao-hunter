#!/usr/bin/env python3
"""把三份回放产物合成一份「回测战绩」快照，供终端 `/api/research/backtest` 只读渲染。

## 为什么要有这个合成层

三份 A/B 产物各管一段，前端直接读三份异构 JSON 会逼它做业务判断（那是后端的活）：

| 源文件 | 内容 |
|---|---|
| `cache/research/t1_caliber_ab_bt.json` | 逐层阶梯 × 三种收益口径（D0 / T1C / T1O） |
| `cache/research/new_caliber_ab_bt.json` | 四条改动的逐笔增量 |
| `cache/pool_ledger/*.json` | 每日真实台账（池内全买 + 实际成交） |

本脚本把它们压成 **一个稳定 schema**，并把统计量（t、Sharpe、中位、盈亏比）在
Python 侧算好 —— 前端只负责显示，⛔ 不在前端重算任何指标（口径会双轨）。

## 产出

`cache/research/backtest_summary.json`（覆盖写，幂等）

## ⛔ 两条记录的铁律（2026-11-06 用户拍板）

`records[]` 里既有「策略口径（收盘价）」也有「T+1 口径（次日收盘）」：
它们**并列展示、互不替代**，⛔ 前端不得把两者相加、相减或互相换算。

用法：
    python3 scripts/build_backtest_summary.py
    python3 scripts/build_backtest_summary.py --check   # 只校验产物是否可读
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "cache" / "research"
LEDGER = ROOT / "cache" / "pool_ledger"
OUT = RESEARCH / "backtest_summary.json"

# 唯一输入：`research_t1_caliber_ab.py` 的产物（三口径 × 五层阶梯都在里面）。
# ⛔ 2026-11-09 删掉原来的 `LAYER_SRC = new_caliber_ab_bt.json` —— 它从头到尾没被读过，
#    留着会让人以为阶梯数据来自两个文件，实际只有 T1_SRC 一个源。
T1_SRC = RESEARCH / "t1_caliber_ab_bt.json"

# 逐层阶梯的展示顺序与文案（⛔ 与 `research_t1_caliber_ab.CONFIGS` 同名）
LAYER_ORDER = ["L0_v10", "L1_noavoid", "L2_rescue", "L3_gene", "L4_v11"]
LAYER_LABEL = {
    "L0_v10": "v1.0 旧口径",
    "L1_noavoid": "① 关盘后回避",
    "L2_rescue": "② +深低开豁免",
    "L3_gene": "③ +龙一基因·换位",
    "L4_v11": "④ +关盘后预案",
}
LAYER_NOTE = {
    "L0_v10": "读盘后预案的回避否决；DEV<−2 一律放弃",
    "L1_noavoid": "盘后「回避」不再一步否决，只留痕",
    "L2_rescue": "DEV≤−6 放行（被砸出来的错杀档）",
    "L3_gene": "同板数下按上一波连板高度顶替原龙一",
    "L4_v11": "预案整链不读，名额走缺省 2 只",
}

# 三口径的展示元信息。`key` = `rows[].字段名`
CALIBERS = [
    {"id": "close", "field": "D0", "label": "策略口径", "sub": "竞价买 → 当日收盘",
     "note": "与每日台账同源，用于判据迭代", "primary": True},
    {"id": "t1", "field": "T1C", "label": "T+1 口径", "sub": "竞价买 → 次日收盘",
     "note": "A 股真实可下单，用于评估可执行性", "primary": False},
    {"id": "t1open", "field": "T1O", "label": "T+1 最保守", "sub": "竞价买 → 次日开盘",
     "note": "不承担次日日内波动", "primary": False},
]


def jl(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _stats(vals: list[float], navs: list[float]) -> dict:
    """一套指标算完。⛔ 前端不得重算，口径只在这里。"""
    if not vals:
        return {"days": 0, "picks": 0}
    n = len(vals)
    m = st.mean(vals)
    sd = st.stdev(vals) if n > 1 else 0.0
    gains = [x for x in vals if x > 0]
    losses = [x for x in vals if x < 0]
    return {
        "days": n,
        "day_win": round(100 * len(gains) / n, 1),
        "day_avg": round(m, 2),
        "sharpe": round(m / sd * math.sqrt(252), 2) if sd else None,
        "t": round(m / (sd / math.sqrt(n)), 2) if sd and n > 1 else None,
        "compound": round((navs[-1] - 1) * 100, 2) if navs else 0.0,
        "mdd": round(min(navs[i] / max(navs[: i + 1]) - 1 for i in range(len(navs))) * 100, 2)
        if navs else 0.0,
        "gain_avg": round(st.mean(gains), 2) if gains else None,
        "loss_avg": round(st.mean(losses), 2) if losses else None,
        "pl_ratio": round(abs(st.mean(gains) / st.mean(losses)), 2)
        if gains and losses and st.mean(losses) else None,
    }


def build_caliber(rows: list[dict], cal: dict) -> dict:
    """按某一口径把逐笔压成「日序列 + 指标 + 净值曲线」。"""
    by_day: dict[str, list[float]] = {}
    for r in rows:
        v = r.get(cal["field"])
        if v is None:
            continue
        by_day.setdefault(str(r.get("date")), []).append(float(v))
    dates = sorted(by_day)
    day_ret = [st.mean(by_day[d]) for d in dates]
    allp = [x for d in dates for x in by_day[d]]

    nav, navs, equity = 1.0, [], []
    for d, x in zip(dates, day_ret):
        nav *= 1 + x / 100
        navs.append(nav)
        equity.append({"date": d, "ret": round(x, 2), "nav": round(nav, 4),
                       "n": len(by_day[d])})

    s = _stats(day_ret, navs)
    s["picks"] = len(allp)
    s["pick_win"] = round(100 * sum(1 for x in allp if x > 0) / len(allp), 1) if allp else 0.0
    s["pick_avg"] = round(st.mean(allp), 2) if allp else 0.0
    s["pick_med"] = round(st.median(allp), 2) if allp else 0.0
    return {"id": cal["id"], "label": cal["label"], "sub": cal["sub"],
            "note": cal["note"], "primary": cal["primary"],
            "metrics": s, "equity": equity}


def build_layers(t1: dict) -> list[dict]:
    """逐层阶梯：每层两个口径并排（⛔ 不合并成一个数）。"""
    out = []
    prev_close = None
    for key in LAYER_ORDER:
        s = ((t1.get("summary") or {}).get(key) or {})
        c, tc = s.get("T0") or {}, s.get("T1C") or {}
        if not c:
            continue
        delta = (round(c["compound"] - prev_close, 2)
                 if prev_close is not None and c.get("compound") is not None else None)
        out.append({
            "key": key, "label": LAYER_LABEL[key], "note": LAYER_NOTE[key],
            "close": {"days": c.get("days"), "picks": c.get("picks"),
                      "day_win": c.get("day_win"), "pick_win": c.get("pick_win"),
                      "day_avg": c.get("day_avg"), "compound": c.get("compound"),
                      "mdd": c.get("mdd")},
            "t1": {"days": tc.get("days"), "picks": tc.get("picks"),
                   "day_win": tc.get("day_win"), "pick_win": tc.get("pick_win"),
                   "day_avg": tc.get("day_avg"), "compound": tc.get("compound"),
                   "mdd": tc.get("mdd")},
            "delta_close": delta,
        })
        prev_close = c.get("compound")
    return out


def build_live() -> dict:
    """真实台账战绩（每日一行）—— 池内全买 + 实际成交只数。⛔ 不是策略业绩。"""
    files = sorted(LEDGER.glob("*.json"))
    rows = []
    for f in files:
        d = jl(f)
        if not isinstance(d, dict):
            continue
        sm = d.get("summary") or {}
        agg = sm.get("auction_buy_close_sell") or {}
        if not agg.get("n"):
            continue
        rows.append({"date": d.get("date") or f.stem,
                     "pool": sm.get("pool"), "tradable": sm.get("tradable"),
                     "avg": agg.get("avg"), "win": agg.get("win"),
                     "n": agg.get("n"),
                     "t1_avg": (sm.get("auction_buy_next_close_sell") or {}).get("avg"),
                     "participate": sum(
                         1 for r in (d.get("rows") or []) if r.get("view") == "participate")})
    if not rows:
        return {"days": 0, "rows": []}
    avgs = [r["avg"] for r in rows if r.get("avg") is not None]
    return {
        "days": len(rows),
        "day_win": round(100 * sum(1 for x in avgs if x > 0) / len(avgs), 1) if avgs else None,
        "day_avg": round(st.mean(avgs), 2) if avgs else None,
        "cum": round(sum(avgs), 2) if avgs else None,
        "traded": sum(r["participate"] for r in rows),
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验产物可读")
    a = ap.parse_args()

    if a.check:
        d = jl(OUT)
        if not isinstance(d, dict):
            print(f"⛔ 产物缺失或不可读：{OUT}")
            return 2
        print(f"✅ {OUT.relative_to(ROOT)}  records={len(d.get('records') or [])} "
              f"layers={len(d.get('layers') or [])} live_days={(d.get('live') or {}).get('days')}")
        return 0

    t1 = jl(T1_SRC) or {}
    if not t1.get("rows"):
        print(f"⛔ 缺 {T1_SRC.relative_to(ROOT)}（先跑 scripts/research_t1_caliber_ab.py --json）")
        return 2

    full_rows = (t1.get("rows") or {}).get("L4_v11") or []
    records = [build_caliber(full_rows, c) for c in CALIBERS]
    records = [r for r in records if r["metrics"].get("days")]

    win = t1.get("window") or [None, None]
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window": {"start": win[0], "end": win[1],
                   "days": t1.get("trading_days") or len({str(r.get("date")) for r in full_rows})},
        "version": "v1.1",
        "records": records,
        "layers": build_layers(t1),
        "live": build_live(),
        "caveats": [
            "策略口径违反 A 股 T+1（当天买当天卖），只作判据迭代标准，不是实盘收益",
            "T+1 口径是真实可执行量级；两条记录并列展示，禁止互相换算",
            "样本 24~35 天、无样本外段；T+1 口径 t=1.06 不显著",
            "13 个参数调在同一段窗口；前 5 笔贡献 41%、前 10 笔贡献 73%",
            "只有一个市场状态（连板活跃期），无弱市样本",
        ],
    }
    RESEARCH.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 已写 {OUT.relative_to(ROOT)}  "
          f"records={len(records)} layers={len(payload['layers'])} "
          f"live_days={payload['live']['days']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
