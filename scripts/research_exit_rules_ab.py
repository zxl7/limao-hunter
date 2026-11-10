#!/usr/bin/env python3
"""卖出规则 A/B：同一批买入名单、只换「什么时候卖」。

## 为什么做这个（2026-11-10 用户）

> 「昨天的阳光 t0 收盘没有赚，但是今天开盘涨停了，收益涨停了都能加入逻辑？该怎么设计呢」

阳光照明(600261) 11-09 竞价买 5.32 → 收盘 5.32（**T0 = 0.00%**）→ 11-10 收 5.85（**T1 = +9.96%**）。
这是「T0 不赚、T1 大赚」的典型案例。问题是：**这是规律还是个例？**

## 先看分布（78 笔 · v1.1 全开 · 三个时点齐全）

T0 与 T1 相关系数 **+0.746**（强正相关，不是两个独立的机会源）：

| T0 档 | n | 继续持有的增量（T1C−T0） | 继续持有变好的占比 |
|---|---|---|---|
| ≤−5% | 8 | **−3.09%** | 12% |
| −5~0 | 26 | **−2.61%** | 27% |
| 0~5 | 19 | +0.26% | 53% |
| 5~9 | 16 | **+2.14%** | 62% |
| ≥9（近涨停） | 9 | **−1.62%** | 44% |

⇒ **亏的票继续持有更亏；赚的票继续持有略赚；近涨停继续持有反而亏。**
阳光照明属于「0~5」档那 53%，但该档平均只有 **+0.26%** —— 它是运气好的那一端，不是规律。

分段拆解（78 笔均值）：
```
当日日内 开→收        +1.78%
隔夜     收→次日开    −1.78%   ← 隔夜是净亏
次日日内 次日开→次日收 +0.91%
```

⚠️ **这段修正了一条旧结论**：此前写「超额几乎全在隔夜」是**读反了**。
隔夜均值是 **−1.78%**，它把当日赚的吐了回去；收益在当日日内，次日日内补一点，补不满。

## 六个方案

    python3 scripts/research_exit_rules_ab.py

⚠️ C/E/F 依赖「收盘前就知道 T0」，实操需 **14:55 左右**决策；回放用收盘价近似。
   这意味着它们有**轻微未来函数风险**（14:55 价与收盘价可能不同向），
   ⛔ 上线前必须在实盘用 14:55 真实价复核，⛔ 不能拿回放数字直接当业绩。

⛔ 本脚本只读不写，不改任何判据、账本、冻结件。
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "cache" / "research" / "t1_caliber_ab_bt.json"
OUT = ROOT / "cache" / "research" / "exit_rules_ab.json"

CFG = "L4_v11"


def load_rows() -> list[dict]:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    rows = d["rows"].get(CFG) or []
    return [r for r in rows
            if all(r.get(k) is not None for k in ("D0", "T1C", "T1O"))]


def stats(pairs: list[tuple[str, float]]) -> dict:
    by: dict[str, list[float]] = {}
    for dt, v in pairs:
        by.setdefault(dt, []).append(v)
    da = [st.mean(v) for v in by.values()]
    nav = peak = 1.0
    mdd = 0.0
    for x in da:
        nav *= 1 + x / 100
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1)
    return {"days": len(da), "picks": len(pairs),
            "day_win": round(100 * sum(1 for x in da if x > 0) / len(da), 1),
            "day_avg": round(st.mean(da), 2),
            "compound": round((nav - 1) * 100, 1),
            "mdd": round(mdd * 100, 1)}


def hold_profit(r: dict) -> float:
    """次日开盘低于成本就走，否则持到次日收盘（盘中止损）。"""
    return r["T1O"] if r["T1O"] < 0 else r["T1C"]


def e_rule(r: dict) -> float:
    """盈利留仓 + 次日开盘止损。"""
    if r["D0"] <= 0:
        return r["D0"]
    return hold_profit(r)


def f_rule(r: dict) -> float:
    """E 的基础上：当日已近涨停（≥9%）不留仓 —— 涨停附近是卖点不是起点。"""
    if r["D0"] <= 0 or r["D0"] >= 9:
        return r["D0"]
    return hold_profit(r)


PLANS: list[tuple[str, str, object]] = [
    ("A", "当日收盘卖（现行策略口径）", lambda r: r["D0"]),
    ("B", "次日收盘卖（T+1）", lambda r: r["T1C"]),
    ("C", "盈利留仓·亏的当日走", lambda r: r["T1C"] if r["D0"] > 0 else r["D0"]),
    ("D", "次日开盘止损·否则持到收", lambda r: hold_profit(r)),
    ("E", "盈利留仓 + 次日开盘止损", e_rule),
    ("F", "E 基础上·近涨停当日卖", f_rule),
]


def breakdown(rows: list[dict]) -> dict:
    out: dict = {}
    n = len(rows)
    out["segments"] = {
        "intraday_d0": round(st.mean([r["D0"] for r in rows]), 2),
        "overnight": round(st.mean([r["T1O"] - r["D0"] for r in rows]), 2),
        "next_intraday": round(st.mean([r["T1C"] - r["T1O"] for r in rows]), 2),
        "total_t1c": round(st.mean([r["T1C"] for r in rows]), 2),
    }
    xs = [r["D0"] for r in rows]
    ys = [r["T1C"] for r in rows]
    mx, my = st.mean(xs), st.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    out["corr_d0_t1c"] = round(cov / (st.stdev(xs) * st.stdev(ys)), 3)

    bands = [("≤−5", None, -5), ("−5~0", -5, 0), ("0~5", 0, 5),
             ("5~9", 5, 9), ("≥9", 9, None)]
    out["by_d0"] = {}
    for lab, lo, hi in bands:
        sub = [r for r in rows
               if (lo is None or r["D0"] > lo) and (hi is None or r["D0"] <= hi)]
        if not sub:
            continue
        out["by_d0"][lab] = {
            "n": len(sub),
            "d0": round(st.mean([r["D0"] for r in sub]), 2),
            "t1c": round(st.mean([r["T1C"] for r in sub]), 2),
            "hold_delta": round(st.mean([r["T1C"] - r["D0"] for r in sub]), 2),
            "better_ratio": round(100 * sum(1 for r in sub if r["T1C"] > r["D0"]) / len(sub), 1),
        }
    return out


def render(res: dict) -> None:
    rows = res["rows"]
    print(f"样本 {len(rows)} 笔 · v1.1 全开 · 三个时点齐全\n")

    print("=== 分段拆解（全部样本均值）===")
    seg = res["breakdown"]["segments"]
    print(f"  当日日内 开→收         {seg['intraday_d0']:+.2f}%")
    print(f"  隔夜     收→次日开     {seg['overnight']:+.2f}%   ← 负贡献")
    print(f"  次日日内 次日开→次日收  {seg['next_intraday']:+.2f}%")
    print(f"  合计     开→次日收     {seg['total_t1c']:+.2f}%")
    print(f"  T0 与 T1 相关系数      {res['breakdown']['corr_d0_t1c']:+.3f}\n")

    print("=== 按 T0 分档：继续持有会更好还是更差 ===")
    print(f"{'T0 档':<8}{'n':>4}{'T0':>8}{'T1C':>8}{'持有增量':>10}{'变好占比':>9}")
    print("-" * 50)
    for lab, b in (res["breakdown"]["by_d0"] or {}).items():
        print(f"{lab:<8}{b['n']:>4}{b['d0']:>+8.2f}{b['t1c']:>+8.2f}"
              f"{b['hold_delta']:>+10.2f}{b['better_ratio']:>8.0f}%")

    print("\n=== 六个卖出方案（同批名单，只换卖出时点）===")
    print(f"{'':<4}{'方案':<26}{'天':>4}{'笔':>4}{'日胜':>7}{'日均':>8}{'复利':>9}{'回撤':>8}")
    print("-" * 74)
    for key, label, _ in PLANS:
        m = res["plans"][key]
        star = " ⭐" if key == res["best"] else ""
        print(f"{key:<4}{label:<26}{m['days']:>4}{m['picks']:>4}{m['day_win']:>6.1f}%"
              f"{m['day_avg']:>+8.2f}%{m['compound']:>+8.1f}%{m['mdd']:>7.1f}%{star}")

    print("\n⛔ C/E/F 依赖「收盘前知道 T0」，实操需 14:55 决策；回放用收盘价近似，")
    print("   存在轻微未来函数风险（14:55 价与收盘价可能不同向）—— 上线前必须用真实 14:55 价复核。")
    print("⛔ 样本 78 笔 / 24 天，窗口偏强；⛔ 不外推，也不据此宣称年化。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = load_rows()
    if not rows:
        print(f"⛔ 无样本（先跑 research_t1_caliber_ab.py 生成 {SRC.name}）")
        return 3

    plans = {k: stats([(r["date"], f(r)) for r in rows]) for k, _, f in PLANS}
    best = max((k for k in plans if k != "A"), key=lambda k: plans[k]["compound"])
    res = {"source": str(SRC.relative_to(ROOT)), "config": CFG,
           "n_picks": len(rows), "rows": rows,
           "breakdown": breakdown(rows), "plans": plans, "best": best}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({k: v for k, v in res.items() if k != "rows"},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "rows"},
                         ensure_ascii=False, indent=2))
    else:
        render(res)
        print(f"\n产物：{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
