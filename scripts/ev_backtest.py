#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封板期望策略回测（2026-11-18）。

## 比什么

| 方案 | 规则 |
|---|---|
| OLD 现状 | 09:25 裁决里 `participate` 的票（闸门说了算） |
| NEW-0 | 全池按期望降序，**只买期望 ≥ 0**，最多 N 只 |
| NEW-TopN | 全池按期望降序取前 N（不看门槛） |

收益口径：**T+1**（竞价价买 → 次日收盘卖，`t1_ret`）——与系统的「持有到断板」最接近。
另报「当日封板率」与「当日收益」作参考。

## ⛔ 回测边界（必须在读结果时记住）

1. 本回测**只应用期望层**，不重放一进二/弱线/后排/炸板等硬闸 ——
   所以 NEW 的笔数会明显多于 OLD，**不是**"新策略一定会买这么多"；
2. 样本 19 个交易日，属于弱势市；
3. 参数（权重/溢价表）就是在同一批数据上拟合的 → **有过拟合风险**，只能看方向。

用法：`python3 scripts/ev_backtest.py`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import seal_ev as se  # noqa: E402


def _j(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _cand_for(day: str) -> dict[str, dict]:
    files = sorted((ROOT / "wealth_code/cache").glob("candidates-*.json"))
    prev = [f for f in files if f.stem.split("-", 1)[1] < day]
    if not prev:
        return {}
    c = _j(prev[-1]) or {}
    return {str(r.get("code")): r for r in (c.get("relay") or []) + (c.get("watch") or [])}


def load() -> list[dict]:
    rows: list[dict] = []
    for f in sorted((ROOT / "cache/pool_ledger").glob("*.json")):
        day = f.stem
        led = _j(f) or {}
        cands = _cand_for(day)
        call = _j(ROOT / "wealth_code/cache" / f"pool_call-{day}.json") or {}
        views = call.get("views") or {}
        quota = int((call.get("meta") or {}).get("effective_quota") or 0)
        for r in led.get("rows") or []:
            code = str(r.get("code") or "")
            c = cands.get(code) or {}
            ev = se.evaluate(gap=r.get("gap_pct"), eod_score=c.get("score"),
                             mates=c.get("theme_mates_cnt"), zbc=c.get("zbc"))
            rows.append({
                "day": day, "code": code, "name": r.get("name"),
                "gap": r.get("gap_pct"),
                "ev": ev["ev"], "p_seal": ev["p_seal"],
                "view": (views.get(code) or {}).get("view"),
                "t1": r.get("t1_ret"), "au": r.get("au_ret"),
            "sealed": 1.0 if isinstance(r.get("chg_pct"), (int, float))
                and float(r["chg_pct"]) >= float(r.get("limit_up_pct") or 10) - 0.2 else 0.0,
                "quota": quota,
            })
    return rows


def _stats(v: list[dict], key: str = "t1") -> str:
    xs = [x[key] for x in v if isinstance(x.get(key), (int, float))]
    if not xs:
        return "0 笔"
    win = 100.0 * sum(1 for x in xs if x > 0) / len(xs)
    sealed = 100.0 * sum(x["sealed"] for x in v) / len(v)
    return (f"{len(xs):3d} 笔 | 均值 {sum(xs) / len(xs):+.2f}% | 胜率 {win:4.0f}% | "
            f"封板率 {sealed:4.0f}%")


def main() -> int:
    rows = [r for r in load() if isinstance(r.get("t1"), (int, float))]
    days = sorted({r["day"] for r in rows})
    print(f"样本 {len(rows)} 条 / {len(days)} 日\n")

    old, new0, newn = [], [], []
    for d in days:
        seg = [r for r in rows if r["day"] == d]
        quota = max(1, seg[0].get("quota") or 2)
        old.extend([r for r in seg if r.get("view") == "participate"])
        by_ev = sorted(seg, key=lambda x: -x["ev"])
        new0.extend([r for r in by_ev if r["ev"] >= 0][:quota])
        newn.extend(by_ev[:quota])

    print("== T+1（竞价买 → 次日收盘卖）==")
    print(f"OLD 现状        {_stats(old)}")
    print(f"NEW-0（期望≥0）  {_stats(new0)}")
    print(f"NEW-TopN        {_stats(newn)}")

    print("\n== 当日（竞价买 → 当日收盘卖）==")
    print(f"OLD 现状        {_stats(old, 'au')}")
    print(f"NEW-0（期望≥0）  {_stats(new0, 'au')}")
    print(f"NEW-TopN        {_stats(newn, 'au')}")

    print("\n== 期望分档（全池按 EV 分四档，看是否单调）==")
    srt = sorted(rows, key=lambda x: -x["ev"])
    k = max(1, len(srt) // 4)
    for i, label in enumerate(("Q1 最高", "Q2", "Q3", "Q4 最低")):
        part = srt[i * k:(i + 1) * k]
        print(f"{label}  {_stats(part)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
