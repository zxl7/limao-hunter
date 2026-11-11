#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据日报 + 错杀清单（2026-11-11 · 只读，不改任何结论）

    python3 scripts/rules_report.py                 # 今天：rules_fired；错杀清单只对已有次日行情的日子
    python3 scripts/rules_report.py --date 2026-11-10
    python3 scripts/rules_report.py --missed 2026-11-10   # 只出某天的错杀清单

产物：
- `cache/rules_fired_<date>.json`  —— 每条判据今天命中几次 + 零命中候选 + 三态分布
- `cache/missed_list_<date>.json`  —— 被拦下的票次日表现（按判据汇总）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core import missed_list as ML  # noqa: E402
from wealth_code.core import rules_fired as RF  # noqa: E402

CACHE = ROOT / "cache"


def _write(name: str, payload: dict) -> Path:
    p = CACHE / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="判据日报 / 错杀清单（只读）")
    ap.add_argument("--date", default=None, help="交易日；默认最近一个有 pool_call 的日子")
    ap.add_argument("--missed", default=None, help="只出该日的错杀清单")
    args = ap.parse_args(argv)

    wc = ROOT / "wealth_code" / "cache"
    days = sorted(p.stem.replace("pool_call-", "") for p in wc.glob("pool_call-*.json"))
    if not days:
        print("[rules_report] 没有 pool_call 冻结件，跳过")
        return 1
    day = args.missed or args.date or days[-1]
    names = RF.registry_names()

    fired = RF.build(day, names=names)
    if fired is None:
        print(f"[rules_report] {day} 没有冻结件，跳过")
        return 1
    _write(f"rules_fired_{day}.json", fired)
    top = fired["fired"][0] if fired["fired"] else None
    print(f"[rules_fired] {day} · 买 {fired['bought']}/{fired['total_views']}"
          f"｜命中最多：{top['name']} x{top['hits']}（{top['id']}）" if top
          else f"[rules_fired] {day} · 无命中")
    if fired["zero_hit"]:
        print(f"  零命中（待删候选）：{'、'.join(fired['zero_hit'])}")

    miss = ML.build(day, names=names)
    if miss is None:
        return 0
    _write(f"missed_list_{day}.json", miss)
    if miss["settled"]:
        worst = miss["by_rule"][0] if miss["by_rule"] else None
        print(f"[missed] {day} · 被拦 {miss['total_blocked']} 只（已结算 {miss['settled']}）"
              + (f"｜次日最好的判据：{worst['name']} 均 {worst['avg_pct']:+.2f}%（n={worst['n']}）"
                 if worst else ""))
    else:
        print(f"[missed] {day} · 被拦 {miss['total_blocked']} 只，次日行情未到，暂不统计")
    lb = miss.get("leader_blocked") or {}
    if lb.get("n"):
        if lb.get("settled"):
            print(f"  🐉 龙头被拦 {lb['n']} 只（已结算 {lb['settled']}）："
                  f"次日均 {lb['avg_pct']:+.2f}%、胜 {lb['win_pct']:.0f}%"
                  f"｜{'、'.join(lb['codes'][:6])}")
        else:
            print(f"  🐉 龙头被拦 {lb['n']} 只（次日行情未到）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
