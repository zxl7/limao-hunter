#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""涨停股全量过筛 CLI（2026-11-04 用户「每天把涨停股全部过一遍」）。

```bash
python3 scripts/daily_screen.py                    # 最近一个有涨停归档的交易日
python3 scripts/daily_screen.py --date 2026-11-03  # 指定日
python3 scripts/daily_screen.py --all --days 10    # 连续多日，看分布趋势
python3 scripts/daily_screen.py --json             # 机读
python3 scripts/daily_screen.py --code 002058      # 单票全维度展开
```

⛔ 只读：不改候选、不改裁决、不推送。产物落 `cache/daily_screen/`。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "wealth_code"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import daily_screen as DS  # noqa: E402


def list_dates(root: Path) -> list[str]:
    out = []
    for f in sorted((root / "cache" / "zt_history").glob("zt_*.json")):
        date = f.stem[3:]
        if len(date) != 10:
            continue
        try:
            if (json.loads(f.read_text(encoding="utf-8")).get("zt_list") or []):
                out.append(date)
        except Exception:
            continue
    return out


def show(result: dict, *, verbose: bool = True) -> None:
    c = result["counts"]
    total = result["zt_count"]
    print(f"\n📋 {result['date']}  全量涨停 {total} 只"
          f"（最高 {result.get('market_max_boards')} 板）")
    print(f"   ✅ 可看 {c.get('pass', 0)} · 👀 只看 {c.get('watch_only', 0)}"
          f" · ⛔ 过掉 {c.get('reject', 0)}")

    if verbose and result.get("reject_reasons"):
        print("   主要过掉原因：")
        for k, v in list(result["reject_reasons"].items())[:6]:
            print(f"     {v:>3} 只 · {k}")

    for s in result["screened"]:
        if s["verdict"] == "reject":
            continue
        mark = "✅" if s["verdict"] == "pass" else "👀"
        print(f"\n   {mark} {s['name']}({s['code']}) {s['boards']}板 · {s['theme']}")
        for x in s["supports"]:
            print(f"      + {x}")
        for x in s["watch_reasons"]:
            print(f"      · {x}")
        for x in s["unknown"]:
            print(f"      ? {x}")

    if verbose:
        print("\n   ⛔ 过掉的（前 10，按板数）")
        n = 0
        for s in result["screened"]:
            if s["verdict"] != "reject":
                continue
            if n >= 10:
                break
            n += 1
            print(f"      {s['name']}({s['code']}) {s['boards']}板"
                  f"：{'；'.join(s['rejects'])}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--days", type=int, default=0, help="最近 N 个归档日")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--code", default="")
    ap.add_argument("--no-persist", action="store_true")
    args = ap.parse_args()

    dates = list_dates(ROOT)
    if not dates:
        print("⛔ 没有涨停归档（cache/zt_history 为空）")
        return 1

    if args.date:
        targets = [args.date]
    elif args.days:
        targets = dates[-args.days:]
    elif args.all:
        targets = dates
    else:
        targets = [dates[-1]]

    results = []
    for d in targets:
        r = DS.build_from_files(ROOT, d)
        if not r:
            print(f"⚠️ {d} 无涨停数据（休市或未归档），跳过")
            continue
        results.append(r)
        if not args.no_persist:
            DS.persist(ROOT, r)

    if args.code:
        for r in results:
            hit = next((s for s in r["screened"] if s["code"] == args.code), None)
            if not hit:
                continue
            print(f"\n🔍 {hit['name']}({hit['code']}) · {r['date']}")
            print(f"   结论：{hit['verdict']} · 板数 {hit['boards']} · 题材 {hit['theme']}"
                  f"（{hit['theme_members']} 家）")
            for lab, key in (("硬伤", "rejects"), ("支持", "supports"),
                             ("只看不买", "watch_reasons"), ("缺数据", "unknown")):
                for x in hit[key]:
                    print(f"   {lab}：{x}")
            if args.json:
                print(json.dumps(hit, ensure_ascii=False, indent=1))
        return 0

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0

    for r in results:
        show(r, verbose=len(results) <= 3)

    if len(results) > 1:
        print("\n📊 多日分布（pass / watch_only / reject）")
        for r in results:
            c = r["counts"]
            print(f"   {r['date']}  {r['zt_count']:>3} 只 → "
                  f"{c.get('pass', 0):>2} / {c.get('watch_only', 0):>2} / {c.get('reject', 0):>3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
