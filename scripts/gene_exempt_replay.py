#!/usr/bin/env python3
"""强基因豁免 · 历史回放 A/B/C（2026-11-10 用户「拉一下历史的战绩，我看看效果」）。

口径**完全复用** `scripts/pool_call_replay.py`，不另写一套：
  · 候选 = `candidates-<T-1>.json`｜竞价 = `auction_quotes-<T>.json`
  · 环境读数（market_open / relay_position / stance）= 当日 `pool_call-<T>.json` 快照
    ⛔ 不重跑环境判定（那会引入「今天的数据判昨天」的前视偏差）
  · 收益 = **T 日开盘买 → T 日收盘卖**（同日游），一字板/停牌剔除

只切两个维度：
  A 豁免关（`POOL_CALL_GENE_EXEMPT=0`）—— 基线，soft 量一律拦
  B 豁免开 + 门槛 2 板
  C 豁免开 + 门槛 3 板（当前默认）
⇒ 差值 = 豁免带来的真实增量；B vs C = 门槛收紧的增量。

用法：
  python3 scripts/gene_exempt_replay.py            # 总表 + 豁免明细
  python3 scripts/gene_exempt_replay.py --json     # 结构化
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WC = ROOT / "wealth_code"
for _p in (str(SCRIPTS), str(WC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pool_call_replay as PCR                     # noqa: E402  同一把尺
from core import gene_recent                       # noqa: E402
from core import pool_call                         # noqa: E402

CONFIGS = [
    ("A 豁免关（基线）", False, 3),
    ("B 门槛2板", True, 2),
    ("C 门槛3板（默认）", True, 3),
]


def _run(day: dict, enabled: bool, min_boards: int) -> dict:
    """跑一次 call_pool 并把「豁免让开」的票与当日收益一起带出来。"""
    saved = (pool_call.POOL_CALL_GENE_EXEMPT, gene_recent.GENE_MIN_BOARDS)
    try:
        pool_call.POOL_CALL_GENE_EXEMPT = bool(enabled)
        gene_recent.GENE_MIN_BOARDS = int(min_boards)
        res = pool_call.call_pool(day["cands"], day["quotes"], **day["kw"])
    except Exception as exc:                                  # noqa: BLE001
        return {"picks": [], "exempted": [], "error": str(exc)}
    finally:
        pool_call.POOL_CALL_GENE_EXEMPT, gene_recent.GENE_MIN_BOARDS = saved

    views = (res or {}).get("views") or {}
    picks, exempted = [], []
    for code, v in views.items():
        is_ex = bool(v.get("gene_exempt"))
        if v.get("view") != "participate" and not is_ex:
            continue
        ret, note = PCR.day_return(code, day["date"])
        row = {"code": code, "name": v.get("name"), "boards": v.get("boards"),
               "gap_pct": v.get("gap_pct"), "ret": ret, "note": note,
               "view": v.get("view"), "gene_exempt": is_ex,
               "band": v.get("volume_band"),
               "gene": {k: (v.get("recent_gene") or {}).get(k)
                        for k in ("max_boards", "zt_n", "truncated")}}
        if v.get("view") == "participate":
            picks.append(row)
        if is_ex:
            exempted.append(row)
    return {"picks": picks, "exempted": exempted}


def analyze() -> dict:
    days = PCR.trading_days()
    rows = []
    for d in days:
        day = PCR.replay_day(d, days)
        if not day:
            continue
        rows.append({"date": d,
                     **{label: _run(day, en, mb) for label, en, mb in CONFIGS}})

    labels = [c[0] for c in CONFIGS]
    stats = {lb: PCR.summarize([r[lb]["picks"] for r in rows]) for lb in labels}

    # 名单变化：C 与 A 出手名单不同的交易日
    def codes_of(r, lb):
        return sorted(p["code"] for p in r[lb]["picks"])

    diff_ab = [r["date"] for r in rows if codes_of(r, labels[0]) != codes_of(r, labels[1])]
    diff_ac = [r["date"] for r in rows if codes_of(r, labels[0]) != codes_of(r, labels[2])]
    diff_bc = [r["date"] for r in rows if codes_of(r, labels[1]) != codes_of(r, labels[2])]

    # 豁免明细（只统计「靠豁免才买到」的票：豁免 ∧ 最终 participate）
    def only_by_exempt(lb):
        out = []
        for r in rows:
            for p in r[lb]["exempted"]:
                if p.get("view") == "participate":
                    out.append({**p, "date": r["date"]})
        return out

    ex_b, ex_c = only_by_exempt(labels[1]), only_by_exempt(labels[2])
    def brief(xs):
        vals = [x["ret"] for x in xs if x["ret"] is not None]
        if not vals:
            return {"n": len(xs), "avg": None, "win": None, "sum": None}
        return {"n": len(xs),
                "avg": round(statistics.mean(vals), 2),
                "win": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
                "sum": round(sum(vals), 2)}
    return {
        "window": [rows[0]["date"], rows[-1]["date"]] if rows else None,
        "days": len(rows), "stats": stats,
        "diff": {"A≠B": diff_ab, "A≠C": diff_ac, "B≠C": diff_bc},
        "exempt_bought": {"B": ex_b, "C": ex_c},
        "exempt_brief": {"B": brief(ex_b), "C": brief(ex_c)},
        "rows": rows,
    }


def render(a: dict) -> None:
    print(f"\n{'='*92}")
    print(f"强基因豁免·历史回放   窗口 {a['window'][0]} ~ {a['window'][1]}"
          f"（{a['days']} 个交易日）｜收益口径：T 日开盘买→T 日收盘卖")
    print(f"{'='*92}")
    print(f"  {'配置':<22}{'出手天数':>9}{'笔数':>6}{'日胜率':>8}{'日均':>8}{'中位':>8}{'累计':>9}")
    for lb, st in a["stats"].items():
        if not st.get("days"):
            print(f"  {lb:<22}无成交")
            continue
        print(f"  {lb:<22}{st['days']:>9}{st['picks']:>6}{st['win_rate']:>7}%"
              f"{st['avg']:>8}%{st['median']:>7}%{st['total']:>8}%")

    d = a["diff"]
    print(f"\n  名单变化：A≠B {len(d['A≠B'])} 天 / A≠C {len(d['A≠C'])} 天"
          f" / B≠C {len(d['B≠C'])} 天")
    if d["B≠C"]:
        print(f"    B、C 名单不同的日子：{', '.join(d['B≠C'])}")

    for tag in ("B", "C"):
        xs, br = a["exempt_bought"][tag], a["exempt_brief"][tag]
        print(f"\n【{tag} 靠豁免才买到的票】{br['n']} 笔"
              + (f"，均值 {br['avg']}%、胜率 {br['win']}%、累计 {br['sum']}%"
                 if br["avg"] is not None else ""))
        for x in xs:
            g = x.get("gene") or {}
            ret = "无K线" if x["ret"] is None else f"{x['ret']:+.2f}%"
            print(f"  {x['date']}  {x.get('name') or '—':6s}({x['code']}) "
                  f"{x.get('boards')}板 竞价{x.get('gap_pct')}% "
                  f"近月{g.get('max_boards')}板·{g.get('zt_n')}次 "
                  f"量{x.get('band')} → {ret}")
    print("\n⛔ 历史回放 ≠ 实盘：单日样本少、同日游口径（不含隔夜）、一字板剔除。"
          "\n   只用来判断「这条开关有没有增量」，不作收益承诺。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    a = analyze()
    if args.json:
        print(json.dumps({k: v for k, v in a.items() if k != "rows"},
                         ensure_ascii=False, indent=1))
    else:
        render(a)
    out = ROOT / "cache/relay_review/gene_exempt_replay.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({k: v for k, v in a.items() if k != "rows"},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
