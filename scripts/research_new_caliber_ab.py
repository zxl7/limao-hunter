#!/usr/bin/env python3
"""11-06 新口径三开关 A/B（35 日真实链路）。

用户 2026-11-06 三条裁定 → 三个可逆开关：

| # | 用户原话 | 开关 | 默认 | 作用 |
|---|---|---|---|---|
| ① | 「盘后预案这个功能关了，**全系统关闭**」 | `POOL_CALL_BIAS_AVOID` | 关（=不否决） | 盘后 `bias=avoid` 不再 skip，只留痕 |
| ② | 「今天受外盘影响…绿盘那么多票，今天这个低开是可以理解的」 | `POOL_CALL_DEV_ENV_OFFSET` | 开 | 池内中位开幅明显为负时，作为当日基准从每票 DEV 扣掉 |
| ③ | 「东方海洋上次是四连板，皇台只有三连板，明显东方海洋要强」 | `POOL_CALL_DRAGON_GENE` | 开 | 09:25 同板数下按「上一波连板高度」复核龙一，不被按跟风拦 |

用法：
    python3 scripts/research_new_caliber_ab.py            # 打印表
    python3 scripts/research_new_caliber_ab.py --json     # 落 cache/research/new_caliber_ab_bt.json

⛔ 只读回放，不改任何生产文件、不写 pool_call 冻结件。
⛔ 收益口径 = 当日 open→close（与既有基线 `research_high_gap_ab.py` 同一套 `day_return`）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "wealth_code"))

spec = importlib.util.spec_from_file_location("pcr", ROOT / "scripts" / "pool_call_replay.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

from core import pool_call as P  # noqa: E402

OUT = ROOT / "cache" / "research" / "new_caliber_ab_bt.json"

# 配置：(盘后回避否决, 低开环境偏移, 龙一基因复核, 深低开豁免)
CONFIGS: dict[str, tuple[bool, bool, bool, bool]] = {
    "A_old":      (True,  False, False, False),   # 旧口径（11-06 之前的生产）
    "B_noavoid":  (False, False, False, False),   # 只关盘后回避
    "C_rescue":   (False, False, False, True),    # +深低开豁免
    "D_new":      (False, False, True,  True),    # +龙一基因复核（新口径全开）
    "E_env":      (False, True,  True,  True),    # 额外对照：再叠低开环境偏移
}


def _apply(cfg: tuple[bool, bool, bool, bool]) -> tuple[bool, ...]:
    avoid, env, gene, rescue = cfg
    saved = (P.POOL_CALL_BIAS_AVOID_VETO, P.POOL_CALL_DEV_ENV_OFFSET,
             P.POOL_CALL_DRAGON_GENE, P.POOL_CALL_DEEP_LOW_RESCUE,
             P.POOL_CALL_DRAGON_GENE_SWAP)
    P.POOL_CALL_BIAS_AVOID_VETO = avoid
    P.POOL_CALL_DEV_ENV_OFFSET = env
    P.POOL_CALL_DRAGON_GENE = gene
    P.POOL_CALL_DRAGON_GENE_SWAP = gene      # 换位只在基因复核开时才有意义
    P.POOL_CALL_DEEP_LOW_RESCUE = rescue
    return saved


def _restore(saved: tuple[bool, ...]) -> None:
    (P.POOL_CALL_BIAS_AVOID_VETO,
     P.POOL_CALL_DEV_ENV_OFFSET,
     P.POOL_CALL_DRAGON_GENE,
     P.POOL_CALL_DEEP_LOW_RESCUE,
     P.POOL_CALL_DRAGON_GENE_SWAP) = saved


def run(cfg_name: str, days: list[str]) -> list[dict]:
    saved = _apply(CONFIGS[cfg_name])
    rows: list[dict] = []
    try:
        for d in days:
            day = R.replay_day(d, days)
            if not day:
                continue
            try:
                res = P.call_pool(day["cands"], day["quotes"], **day["kw"])
            except Exception as exc:            # noqa: BLE001
                rows.append({"date": d, "error": f"{type(exc).__name__}: {exc}"})
                continue
            for c, v in (res.get("views") or {}).items():
                if v.get("view") != "participate":
                    continue
                ret, note = R.day_return(c, d)
                rows.append({
                    "date": d, "code": c, "name": v.get("name"),
                    "boards": v.get("boards"), "gap_pct": v.get("gap_pct"),
                    "zone": v.get("zone"), "bias": v.get("bias"),
                    "recheck_class": v.get("recheck_class"),
                    "forecast_verdict": v.get("forecast_verdict"),
                    "gene_leader_override": (v.get("gene_leader_override") or {}).get("note"),
                    "deep_low_rescue": (v.get("deep_low_rescue") or {}).get("dev"),
                    "dev_env_offset": v.get("dev_env_offset"),
                    "bias_avoided": bool(v.get("bias_avoided")),
                    "ret": ret, "note": note,
                })
    finally:
        _restore(saved)
    return rows


def summarize(rows: list[dict]) -> dict:
    picks = [r for r in rows if r.get("ret") is not None]
    by_day: dict[str, list[float]] = {}
    for r in picks:
        by_day.setdefault(r["date"], []).append(r["ret"])
    day_avg = {d: statistics.mean(v) for d, v in by_day.items()}
    vals = list(day_avg.values())
    nav, peak, mdd = 1.0, 1.0, 0.0
    for x in vals:
        nav *= (1 + x / 100.0)
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1)
    return {
        "days": len(vals),
        "picks": len(picks),
        "day_win": round(100 * sum(1 for x in vals if x > 0) / len(vals), 1) if vals else 0.0,
        "pick_win": round(100 * sum(1 for r in picks if r["ret"] > 0) / len(picks), 1) if picks else 0.0,
        "day_avg": round(statistics.mean(vals), 2) if vals else 0.0,
        "pick_avg": round(statistics.mean([r["ret"] for r in picks]), 2) if picks else 0.0,
        "compound": round((nav - 1) * 100, 2),
        "mdd": round(mdd * 100, 2),
        "no_kline": sum(1 for r in rows if r.get("ret") is None and r.get("note") == "no_kline"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="落 cache/research/new_caliber_ab_bt.json")
    ap.add_argument("--start", default=None, help="起始日（含），默认取 zt_history 全部")
    ap.add_argument("--days", type=int, default=35, help="回看交易日数，默认 35")
    args = ap.parse_args()

    all_days = R.trading_days()
    days = all_days[-args.days:] if args.days else all_days
    if args.start:
        days = [d for d in days if d >= args.start]
    print(f"回放窗口：{days[0]} ~ {days[-1]}（{len(days)} 个交易日）\n")

    out: dict[str, dict] = {}
    detail: dict[str, list[dict]] = {}
    for name in CONFIGS:
        rows = run(name, days)
        detail[name] = rows
        out[name] = summarize(rows)

    hdr = f"{'配置':<13}{'天数':>5}{'笔数':>6}{'日胜':>8}{'单笔胜':>8}{'日均':>9}{'单笔均':>9}{'期末复利':>10}{'最大回撤':>10}"
    print(hdr)
    print("-" * len(hdr))
    label = {
        "A_old": "A 旧口径",
        "B_noavoid": "B 关回避",
        "C_rescue": "C +深低开豁免",
        "D_new": "D +基因复核",
        "E_env": "E +低开偏移",
    }
    for name, s in out.items():
        print(f"{label[name]:<13}{s['days']:>5}{s['picks']:>6}{s['day_win']:>7.1f}%"
              f"{s['pick_win']:>7.1f}%{s['day_avg']:>8.2f}%{s['pick_avg']:>8.2f}%"
              f"{s['compound']:>9.2f}%{s['mdd']:>9.2f}%")

    # ── 逐层砍/加清单 ────────────────────────────────────
    print("\n" + "=" * 72)
    base = {(r["date"], r["code"]): r for r in detail["A_old"] if r.get("code")}
    prev_name = "A_old"
    for name in list(CONFIGS)[1:]:
        cur = {(r["date"], r["code"]): r for r in detail[name] if r.get("code")}
        added = [v for k, v in cur.items() if k not in base]
        removed = [v for k, v in base.items() if k not in cur]
        print(f"\n【{label[prev_name]} → {label[name]}】新增 {len(added)} 笔 / 砍掉 {len(removed)} 笔")
        va = [r["ret"] for r in added if r["ret"] is not None]
        vr = [r["ret"] for r in removed if r["ret"] is not None]
        if va:
            print(f"  新增：n={len(va)} 均 {statistics.mean(va):+.2f}% "
                  f"胜 {100 * sum(1 for x in va if x > 0) / len(va):.0f}%")
        if vr:
            print(f"  砍掉：n={len(vr)} 均 {statistics.mean(vr):+.2f}% "
                  f"胜 {100 * sum(1 for x in vr if x > 0) / len(vr):.0f}%")
        for r in sorted(added, key=lambda x: -(x["ret"] if x["ret"] is not None else -99)):
            t = f"{r['ret']:+.2f}%" if r["ret"] is not None else r["note"]
            print(f"    + {r['date']} {r['code']} {r['name']:<6} {r['boards']}板 "
                  f"开{r['gap_pct']:+.2f}% {r['zone']:<7} {t}")
        base = cur
        prev_name = name

    if args.json:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"window": [days[0], days[-1]], "configs": CONFIGS,
                   "summary": out, "detail": detail},
                  open(OUT, "w"), ensure_ascii=False, indent=1)
        print(f"\n已落盘：{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
