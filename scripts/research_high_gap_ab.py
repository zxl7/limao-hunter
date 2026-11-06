#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两个新开关的真实链路 A/B 回放（2026-11-06）。

## 验的是什么

| 配置 | `POOL_CALL_HIGH_GAP_REJECT` | `POOL_CALL_RECHECK_REPAIR` |
|------|------|------|
| A 现状（11-05 行为） | 0 | 0 |
| B 只放宽「历史连板修复」复核 | 0 | 1 |
| C 只加顶部空间硬闸 | 1 | 0 |
| D 两个都上（建议） | 1 | 1 |

⛔ **这是验收点**：`scripts/research_open_gap_band.py` 只是统计近似（不含名额/排序/
量能 full/身位/锚点闸），开关到底改不改，必须穿过生产门控看真实买入名单。
⛔ 收益口径 = T 日开盘买 → T 日收盘卖（与 `pool_call_replay` 同日游一致），
   一字板/无 K 线剔除。⛔ 不回填成「盈利日」，数字是多少就报多少。

用法：
  python3 scripts/research_high_gap_ab.py            # 汇总
  python3 scripts/research_high_gap_ab.py -v         # 逐日名单
"""
from __future__ import annotations

import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WC = ROOT / "wealth_code"
for p in (str(WC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import json                           # noqa: E402
from datetime import datetime         # noqa: E402
import pool_call_replay as R          # noqa: E402  复用数据加载/收益口径
from core import pool_call            # noqa: E402

CFGS = [
    ("A 现状（两个都关）", 0, 0),
    ("B 只放宽历史修复复核", 0, 1),
    ("C 只加顶部空间硬闸", 1, 0),
    ("D 两个都上", 1, 1),
]

OUT = ROOT / "cache" / "research" / "high_gap_ab_bt.json"


def run(day: dict, high_gap: int, repair: int) -> dict:
    saved = pool_call.POOL_CALL_HIGH_GAP_REJECT
    old_env = os.environ.get("POOL_CALL_RECHECK_REPAIR")
    try:
        pool_call.POOL_CALL_HIGH_GAP_REJECT = bool(high_gap)
        os.environ["POOL_CALL_RECHECK_REPAIR"] = "1" if repair else "0"
        res = pool_call.call_pool(day["cands"], day["quotes"], **day["kw"])
    except Exception as e:                                   # noqa: BLE001
        return {"error": str(e), "picks": []}
    finally:
        pool_call.POOL_CALL_HIGH_GAP_REJECT = saved
        if old_env is None:
            os.environ.pop("POOL_CALL_RECHECK_REPAIR", None)
        else:
            os.environ["POOL_CALL_RECHECK_REPAIR"] = old_env

    views = (res or {}).get("views") or {}
    picks = []
    for code, v in views.items():
        if v.get("view") != "participate":
            continue
        ret, note = R.day_return(code, day["date"])
        picks.append({"code": code, "name": v.get("name"),
                      "boards": v.get("boards"), "gap_pct": v.get("gap_pct"),
                      "room": v.get("gap_room_pct"), "ret": ret, "note": note,
                      "cap": v.get("position_cap")})
    return {"picks": picks, "quota": (res.get("meta") or {}).get("effective_quota")}


def summ(per_day: list[list[dict]]) -> dict:
    rets = []
    for picks in per_day:
        vals = [p["ret"] for p in picks if p["ret"] is not None]
        if vals:
            rets.append(sum(vals) / len(vals))
    n = sum(len([p for p in pk if p["ret"] is not None]) for pk in per_day)
    if not rets:
        return {"days": 0, "picks": n}
    return {"days": len(rets), "picks": n,
            "win": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            "avg": round(statistics.mean(rets), 2),
            "med": round(statistics.median(rets), 2),
            "total": round(sum(rets), 2)}


def main() -> int:
    verbose = "-v" in sys.argv
    days = R.trading_days()
    rows = []
    for d in days:
        day = R.replay_day(d, days)
        if not day:
            continue
        rows.append({"date": d, **{lab: run(day, hg, rp) for lab, hg, rp in CFGS}})
    if not rows:
        print("⛔ 无可回放交易日")
        return 1

    _repair_env = os.environ.get("POOL_CALL_RECHECK_REPAIR")
    _is_d = bool(pool_call.POOL_CALL_HIGH_GAP_REJECT) and _repair_env != "0"
    print(f"\n【生产默认值核对】POOL_CALL_HIGH_GAP_REJECT={pool_call.POOL_CALL_HIGH_GAP_REJECT}"
          f"｜HIGH_GAP_ROOM_MIN={pool_call.HIGH_GAP_ROOM_MIN}"
          f"｜POOL_CALL_RECHECK_REPAIR={_repair_env if _repair_env is not None else '未设置(默认开)'}"
          f" → {'≡ D 组 ✅' if _is_d else '⚠️ 与 D 组不一致，生产还没按新模式跑'}")

    print(f"\n{'='*96}")
    print(f"顶部空间硬闸 × 历史修复复核 · 真实链路 A/B   窗口 {rows[0]['date']} ~ {rows[-1]['date']}"
          f"（{len(rows)} 个交易日）")
    print(f"{'='*96}")
    print(f"  {'配置':<26}{'出手天':>7}{'笔数':>6}{'日胜率':>8}{'日均':>9}{'中位':>8}{'累计':>9}")
    for lab, _, _ in CFGS:
        s = summ([r[lab]["picks"] for r in rows])
        if not s.get("days"):
            print(f"  {lab:<26}{'—':>7}{s.get('picks', 0):>6}      无成交")
            continue
        print(f"  {lab:<26}{s['days']:>7}{s['picks']:>6}{s['win']:>8.1f}"
              f"{s['avg']:>+9.2f}{s['med']:>+8.2f}{s['total']:>+9.2f}")

    base = [sorted(p["code"] for p in r[CFGS[0][0]]["picks"]) for r in rows]
    for lab, _, _ in CFGS[1:]:
        cur = [sorted(p["code"] for p in r[lab]["picks"]) for r in rows]
        ch = [r["date"] for r, a, b in zip(rows, base, cur) if a != b]
        print(f"\n  {lab} vs A：名单变化 {len(ch)} 天"
              + (f"（{'、'.join(ch[:12])}）" if ch else ""))

    def fmt(p):
        rt = "无K线" if p["ret"] is None else f"{p['ret']:+.2f}%"
        room = "" if p.get("room") is None else f" 剩{p['room']:.1f}"
        return f"{p['name']}({p['boards']}板{p['gap_pct']:+.1f}%{room}→{rt})"

    print("\n  【逐日：只在有名单差异的日子展开】")
    for i, r in enumerate(rows):
        names = {lab: tuple(sorted(p["code"] for p in r[lab]["picks"])) for lab, _, _ in CFGS}
        if len(set(names.values())) == 1 and not verbose:
            continue
        print(f"  {r['date']}")
        for lab, _, _ in CFGS:
            got = ", ".join(fmt(p) for p in r[lab]["picks"]) or "—"
            print(f"      {lab:<24} {r[lab].get('quota', '?')} 号 | {got}")

    # ── 基线归档（供以后逐笔对比：新改动跑同一脚本，diff 这个文件即可）──
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "生产模式基线 = D 组（顶部空间硬闸 1 + 历史修复复核 1）",
        "window": [rows[0]["date"], rows[-1]["date"]],
        "days": len(rows),
        "cfgs": [lab for lab, _, _ in CFGS],
        "production_default_is_D": bool(pool_call.POOL_CALL_HIGH_GAP_REJECT),
        "params": {"HIGH_GAP_ROOM_MIN": pool_call.HIGH_GAP_ROOM_MIN,
                   "POOL_CALL_RECHECK_REPAIR": os.environ.get("POOL_CALL_RECHECK_REPAIR", "1")},
        "summary": {lab: summ([r[lab]["picks"] for r in rows]) for lab, _, _ in CFGS},
        "daily": [{"date": r["date"],
                   "cfgs": {lab: r[lab] for lab, _, _ in CFGS}} for r in rows],
        "cost_note": "收益=T日开盘买→T日收盘卖；一字板剔除；窗口内真实链路门控，不回填盈利日",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  基线已落盘 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
