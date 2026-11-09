#!/usr/bin/env python3
"""周期优化 A/B：盘后预案要不要 / 定调从哪来（35 日真实链路）。

三件事分开验，一次跑完：

| 配置 | 盘后预案 | 定调来源 | 说明 |
|---|---|---|---|
| P0_plan | 读（旧） | 盘后 LLM 拍的 stance | 11-06 之前的生产 |
| P1_off  | 不读 | 无（缺省名额 2） | 只关预案 |
| P2_mech | 不读 | 09:25 竞价机械定调 | `core/stance_intraday.py` |

D 口径（关回避 / 深低开豁免 / 龙一基因·换位）全程开着，只看「周期」这一段的影响。

用法：
    python3 scripts/research_cycle_ab.py
    python3 scripts/research_cycle_ab.py --json     # → cache/research/cycle_ab_bt.json

⛔ 只读回放，不改生产文件、不写冻结件。
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

from core import pool_call as P          # noqa: E402
from core import stance_intraday as SI   # noqa: E402

OUT = ROOT / "cache" / "research" / "cycle_ab_bt.json"
CONFIGS = ("P0_plan", "P1_off", "P2_mech")
LABEL = {"P0_plan": "P0 盘后预案", "P1_off": "P1 关预案", "P2_mech": "P2 机械定调"}


def _d_caliber() -> None:
    """D 口径（2026-11-06 新口径）全开——本 A/B 不测它，只固定它。"""
    P.POOL_CALL_BIAS_AVOID_VETO = False
    P.POOL_CALL_DEEP_LOW_RESCUE = True
    P.POOL_CALL_DRAGON_GENE = True
    P.POOL_CALL_DRAGON_GENE_SWAP = True
    P.POOL_CALL_DEV_ENV_OFFSET = False


def run(cfg: str, days: list[str]) -> dict:
    _d_caliber()
    picks: list[dict] = []
    for d in days:
        day = R.replay_day(d, days)
        if not day:
            continue
        kw = dict(day["kw"])
        if cfg == "P1_off":
            kw["stance"] = None
        elif cfg == "P2_mech":
            kw["stance"] = SI.stance_from_open(kw.get("market_open")).get("stance")
        try:
            res = P.call_pool(day["cands"], day["quotes"], **kw)
        except Exception as exc:              # noqa: BLE001
            picks.append({"date": d, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for code, v in (res.get("views") or {}).items():
            if v.get("view") != "participate":
                continue
            ret, note = R.day_return(code, d)
            picks.append({"date": d, "code": code, "name": v.get("name"),
                          "gap_pct": v.get("gap_pct"), "ret": ret, "note": note})
    return {"picks": picks}


def summarize(picks: list[dict]) -> dict:
    by_day: dict[str, list[float]] = {}
    for r in picks:
        if r.get("ret") is not None:
            by_day.setdefault(r["date"], []).append(r["ret"])
    da = {d: statistics.mean(v) for d, v in by_day.items()}
    vals = list(da.values())
    nav, peak, mdd = 1.0, 1.0, 0.0
    for x in vals:
        nav *= 1 + x / 100.0
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1)
    n = sum(len(v) for v in by_day.values())
    return {
        "days": len(vals), "picks": n,
        "day_win": round(100 * sum(1 for x in vals if x > 0) / len(vals), 1) if vals else 0.0,
        "day_avg": round(statistics.mean(vals), 2) if vals else 0.0,
        "compound": round((nav - 1) * 100, 2),
        "mdd": round(mdd * 100, 2),
        "by_day": {d: round(v, 2) for d, v in sorted(da.items())},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--days", type=int, default=35)
    args = ap.parse_args()

    days = R.trading_days()[-args.days:]
    print(f"回放窗口：{days[0]} ~ {days[-1]}（{len(days)} 个交易日）\n")

    detail, summary = {}, {}
    for cfg in CONFIGS:
        picks = run(cfg, days)["picks"]
        detail[cfg] = picks
        summary[cfg] = summarize(picks)

    hdr = f"{'配置':<12}{'天数':>5}{'笔数':>6}{'日胜':>8}{'日均':>9}{'复利':>10}{'回撤':>10}"
    print(hdr)
    print("-" * len(hdr))
    for cfg in CONFIGS:
        s = summary[cfg]
        print(f"{LABEL[cfg]:<12}{s['days']:>5}{s['picks']:>6}{s['day_win']:>7.1f}%"
              f"{s['day_avg']:>8.2f}%{s['compound']:>9.2f}%{s['mdd']:>9.2f}%")

    # ── 逐笔差异 ──────────────────────────────────────
    print("\n" + "=" * 70)
    base = {(r["date"], r["code"]): r for r in detail["P0_plan"] if r.get("code")}
    for cfg in CONFIGS[1:]:
        cur = {(r["date"], r["code"]): r for r in detail[cfg] if r.get("code")}
        added = [v for k, v in cur.items() if k not in base]
        removed = [v for k, v in base.items() if k not in cur]
        print(f"\n【{LABEL['P0_plan']} → {LABEL[cfg]}】新增 {len(added)} 笔 / 砍掉 {len(removed)} 笔")
        if not added and not removed:
            print("  ⭐ 逐笔完全相同 —— 盘后预案对最终买入**零影响**")
        for r in sorted(added, key=lambda x: -(x["ret"] if x["ret"] is not None else -99)):
            t = f"{r['ret']:+.2f}%" if r["ret"] is not None else r["note"]
            print(f"    + {r['date']} {r['code']} {r['name']} {t}")
        for r in sorted(removed, key=lambda x: -(x["ret"] if x["ret"] is not None else -99)):
            t = f"{r['ret']:+.2f}%" if r["ret"] is not None else r["note"]
            print(f"    - {r['date']} {r['code']} {r['name']} {t}")

    if args.json:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"window": [days[0], days[-1]], "summary": summary, "detail": detail},
                  open(OUT, "w"), ensure_ascii=False, indent=1)
        print(f"\n已落盘：{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
