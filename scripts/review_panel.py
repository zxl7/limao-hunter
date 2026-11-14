#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧭 复盘三块面板（命令行）

    python3 scripts/review_panel.py                 # 最新交易日
    python3 scripts/review_panel.py --date 2026-11-13
    python3 scripts/review_panel.py --json

输出 `cache/review_panel_<date>.json`；⛔ 只读汇总。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import review_panel as rp  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d = rp.build(a.date)
    (ROOT / "cache" / f"review_panel_{d['date']}.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.json:
        print(json.dumps(d, ensure_ascii=False))
        return 0
    r, m = d["risk"], d["money"]
    print(f"[{d['date']}] 风险 {r['level']}（命中 {r['hits']}）")
    for s in r["signals"]:
        print(f"  {'●' if s['hit'] else '○'} {s['name']}：{s['value']}")
    print("赚钱效应：")
    print(f"  昨日涨停池 均 {m['zt_pool_avg']}% · 红盘 {m['red_ratio']}%")
    if m["relay_best"]:
        print(f"  接力（昨日{m['relay_max_boards']}板）：均 {m['relay_avg']}% · "
              f"最强 {m['relay_best']['name']} {m['relay_best']['pct']:+.2f}% · "
              f"最弱 {m['relay_worst']['name']} {m['relay_worst']['pct']:+.2f}%")
    if m["overnight_best"]:
        print(f"  隔夜（昨日2板）：均 {m['overnight_avg']}% · "
              f"最强 {m['overnight_best']['name']} {m['overnight_best']['pct']:+.2f}% · "
              f"最弱 {m['overnight_worst']['name']} {m['overnight_worst']['pct']:+.2f}%")
    print("主线与板块：")
    for t in d["themes"][:8]:
        bar = "█" * max(1, int(t["strength"] * 10))
        print(f"  {t['name']:<12}{bar:<10} {t['zt']}家 最高{t['max_boards']}板 "
              f"启动第{t['day']}天 首封{t['first_seal']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
