#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""👑 高标处境 + ⚠️ 明日跌停预判（写 JSON 供复盘卡引用）

    python3 scripts/review_extras.py [--date 2026-11-13]

输出 `cache/review_extras_<date>.json` + `cache/review_extras.json`（最新）。
⛔ 只读汇总。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import review_extras as rx  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    a = ap.parse_args(argv)
    d = rx.build(a.date)
    body = json.dumps(d, ensure_ascii=False, indent=1)
    (ROOT / "cache" / f"review_extras_{d['date']}.json").write_text(body, encoding="utf-8")
    (ROOT / "cache" / "review_extras.json").write_text(body, encoding="utf-8")
    hb, dr = d["high_board"], d["dump_risk"]
    print(f"[{d['date']}] 高标 {hb['max_boards']} 板 · {hb['count']} 只")
    for r in hb["rows"]:
        print(f"  👑 {r['name']}({r['code']}) {r['boards']}板 封{r['fbt']} 炸{r['zbc']} "
              f"同板位晋级率 {r['next_odds']}%" + ("｜第二波" if r["second_wave"] else ""))
    print(f"⚠️ 明日跌停风险 {dr['level']}（命中 {dr['hits']}）→ {dr['verdict']}")
    for s in dr["signals"]:
        print(f"  {'●' if s['hit'] else '○'} {s['name']}：{s['value']}")
    if dr["prev_zt_to_limit_down"]:
        print(f"  昨日涨停今日跌停 {dr['prev_zt_to_limit_down_count']} 只："
              + "、".join(dr["prev_zt_to_limit_down"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
