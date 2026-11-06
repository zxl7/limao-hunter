#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产模式基线报表（读 `cache/research/high_gap_ab_bt.json`，不重跑、不改判据）。

用途：用户 2026-11-06 定「以后按这个模式跑」= **D 组**
（`POOL_CALL_HIGH_GAP_REJECT=1` + `POOL_CALL_RECHECK_REPAIR=1`，两者都是代码默认值，
plist / .env 均未覆盖）。本脚本只把基线摊开成人能看的表，便于以后逐笔对账。

输出：① 逐笔明细 ② A 组按开盘幅度分档（被砍档位的真实代价）③ 剔除/新增清单
④ 逐日累计曲线（等权）。

用法：python3 scripts/report_high_gap_baseline.py
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "cache" / "research" / "high_gap_ab_bt.json"
A = "A 现状（两个都关）"
D = "D 两个都上"


def agg(vals: list[float]) -> str:
    if not vals:
        return "—"
    w = sum(1 for v in vals if v > 0) / len(vals) * 100
    return f"n={len(vals):<3} {statistics.mean(vals):+6.2f}%  胜 {w:5.1f}%"


def main() -> int:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    days = d["daily"]
    print(f"\n生产模式基线（D 组）  窗口 {d['window'][0]} ~ {d['window'][1]}  {d['days']} 个交易日")
    print(f"参数 {d['params']}｜生成于 {d['generated_at']}")

    # ── ① 逐笔明细 ──────────────────────────────────────────────
    print("\n=== ① D 组逐笔（生产模式真实买入名单）===")
    print(f"  {'日期':<12}{'代码':<8}{'名称':<9}{'板':>2}{'今开':>8}{'剩余':>7}{'仓位':>7}{'当日':>9}")
    all_picks: list[dict] = []
    for day in days:
        for p in day["cfgs"][D]["picks"]:
            all_picks.append({"date": day["date"], **p})
    for p in all_picks:
        rt = "无K线" if p["ret"] is None else f"{p['ret']:+.2f}%"
        room = "—" if p.get("room") is None else f"{p['room']:+.1f}"
        cap = "—" if p.get("cap") is None else f"{p['cap']*100:.0f}%"
        print(f"  {p['date']:<12}{p['code']:<8}{(p['name'] or ''):<9}"
              f"{p['boards'] or 0:>2}{p['gap_pct']:>+8.2f}{room:>7}{cap:>7}{rt:>9}")

    # ── ② A 组按今开幅度分档（闸砍掉的是不是亏的）────────────────
    print("\n=== ② A 组 73 笔按今开幅度分档（闸值不值）===")
    a_picks = [{"date": day["date"], **p} for day in days for p in day["cfgs"][A]["picks"]]
    bands = [("≤−5%", None, -5), ("−5~−2%", -5, -2), ("−2~0%", -2, 0),
             ("0~2%", 0, 2), ("2~5%", 2, 5), ("5~8%", 5, 8), ("≥8%", 8, None)]
    for lab, lo, hi in bands:
        sel = [p["ret"] for p in a_picks
               if p["ret"] is not None
               and (lo is None or p["gap_pct"] > lo)
               and (hi is None or p["gap_pct"] <= hi)]
        print(f"  {lab:<8} {agg(sel)}")

    # ── ③ 剔除 / 新增清单 ───────────────────────────────────────
    print("\n=== ③ 闸带来的名单变化（15 天）===")
    cut, add = [], []
    for day in days:
        a_codes = {p["code"]: p for p in day["cfgs"][A]["picks"]}
        d_codes = {p["code"]: p for p in day["cfgs"][D]["picks"]}
        for c, p in a_codes.items():
            if c not in d_codes:
                cut.append({"date": day["date"], **p})
        for c, p in d_codes.items():
            if c not in a_codes:
                add.append({"date": day["date"], **p})
    print(f"  【被砍 {len(cut)} 笔】")
    for p in sorted(cut, key=lambda x: (x["ret"] if x["ret"] is not None else 0)):
        rt = "无K线" if p["ret"] is None else f"{p['ret']:+.2f}%"
        print(f"    {p['date']} {p['code']} {(p['name'] or ''):<6} 开{p['gap_pct']:+6.2f}% "
              f"{p['boards'] or 0}板 → {rt}")
    good = [p for p in cut if p["ret"] is not None and p["ret"] > 0]
    bad = [p for p in cut if p["ret"] is not None and p["ret"] <= 0]
    print(f"  砍对 {len(bad)} 笔 / 砍错 {len(good)} 笔"
          f"（砍对合计 {sum(p['ret'] for p in bad):+.1f}pct，砍错合计 {sum(p['ret'] for p in good):+.1f}pct）")
    print(f"  【新增 {len(add)} 笔】" + ("（本次为空，闸只减不增）" if not add else ""))
    for p in add:
        rt = "无K线" if p["ret"] is None else f"{p['ret']:+.2f}%"
        print(f"    {p['date']} {p['code']} {(p['name'] or ''):<6} 开{p['gap_pct']:+6.2f}% → {rt}")

    # ── ④ 逐日累计曲线 ──────────────────────────────────────────
    print("\n=== ④ 逐日累计（等权，交易仅当日的天数跳过）===")
    for lab in (A, D):
        eq, peak, mdd, line = 1.0, 1.0, 0.0, []
        for day in days:
            vals = [p["ret"] for p in day["cfgs"][lab]["picks"] if p["ret"] is not None]
            if not vals:
                continue
            r = statistics.mean(vals) / 100
            eq *= 1 + r
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1)
            line.append((day["date"], eq))
        print(f"  {lab:<22} 期末 {eq:.4f}（{(eq-1)*100:+.2f}%）"
              f"｜最大回撤 {mdd*100:.2f}%｜交易日 {len(line)}")
        if lab == D:
            print("    " + " ".join(f"{dt[5:]}:{(e-1)*100:+.1f}" for dt, e in line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
