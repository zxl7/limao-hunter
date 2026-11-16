#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用同花顺概念库回填缺失的题材快照（2026-11-16）

## 为什么要这个
选股宝快照只覆盖 11-04 之后（模块上线时间），09-11~10-15 共 19 天没有题材归属。
同花顺 `index.constituents` 给的是**全市场概念成分**，可以按当日涨停票反查概念，
把缺口补上——**但口径不同，必须标清楚**。

## 口径差异（⛔ 不能假装一样）
| | 选股宝（主） | 同花顺（本次回填） |
|---|---|---|
| 字段 | `plates` = 炒作题材 | 概念指数成分（一只票可属多个概念） |
| 家数 | 当日涨停里的同题材家数 | 全市场成分，需按当日涨停池再过滤 |
| 用途 | 选股/复盘主口径 | **只做历史回填与校验** |

写出的快照 `source = "hithink_backfill"`，下游可据此区分；主链（`get_theme_map`）
读快照时不看 source，所以**只在没有快照的日子才写**，绝不覆盖选股宝首版。

## 用法
    python3 scripts/backfill_theme_from_hithink.py --dry-run
    python3 scripts/backfill_theme_from_hithink.py
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"
SNAP = CACHE / "theme_snapshot"
HITHINK = CACHE / "theme_library_hithink.json"
BJ = ZoneInfo("Asia/Shanghai")


def _zt_days() -> list[str]:
    return sorted(p.stem.replace("zt_", "") for p in (CACHE / "zt_history").glob("zt_*.json"))


def _zt_codes(day: str) -> list[str]:
    try:
        d = json.loads((CACHE / "zt_history" / f"zt_{day}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if d.get("intraday_only") not in (None, False):
        return []
    return [str(r.get("code")) for r in (d.get("zt_list") or []) if r.get("code")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-cover", type=float, default=0.6)
    ap.add_argument("--force", action="store_true", help="重写 source=hithink_backfill 的日期")
    ap.add_argument("--max-members", type=int, default=300,
                    help="概念全局成分数超过此值视为过宽（如新能源汽车 1068 只），不选它当题材")
    a = ap.parse_args(argv)

    if not HITHINK.exists():
        print("[fill] ⛔ 缺 theme_library_hithink.json，先跑 hithink_theme_library.py")
        return 1
    lib = json.loads(HITHINK.read_text(encoding="utf-8"))
    entries = lib.get("entries") or {}
    print(f"[fill] 同花顺概念库：{lib.get('concept_count')} 概念 · {len(entries)} 只票")

    # 概念全局成分数（判断"过宽"用）
    global_count: Counter[str] = Counter()
    for e in entries.values():
        for cons in (e.get("concepts") or []):
            global_count[cons] += 1

    have = {p.stem.replace("theme_", "") for p in SNAP.glob("theme_*.json")}
    if a.force:
        # 只放开「我们自己回填」的那批，⛔ 绝不覆盖选股宝首版
        for fp in SNAP.glob("theme_*.json"):
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if d.get("source") == "hithink_backfill":
                have.discard(fp.stem.replace("theme_", ""))
    missing = [d for d in _zt_days() if d not in have and _zt_codes(d)]
    if not missing:
        print("[fill] 无缺口")
        return 0
    print(f"[fill] 缺口 {len(missing)} 天：{', '.join(missing[:5])}{' …' if len(missing) > 5 else ''}")

    written = skipped = 0
    for day in missing:
        codes = _zt_codes(day)
        # 🎯 两遍法：先统计「当日涨停池里各概念出现次数」，再给每只票挑它在池内最热的概念。
        #    ⛔ 不能取 concepts[0]（目录顺序无意义）：实测会得出「新能源汽车 12 家」这种假主线。
        pool_count: Counter[str] = Counter()
        for c in codes:
            for cons in ((entries.get(c) or {}).get("concepts") or []):
                pool_count[cons] += 1
        mapping = {}
        for c in codes:
            cons = (entries.get(c) or {}).get("concepts") or []
            if not cons:
                continue
            # 优先「不过宽」的概念；全都被判过宽时退回全集
            cands = [x for x in cons if global_count[x] <= a.max_members] or cons
            best = max(cands, key=lambda x: (pool_count[x], -global_count[x], x))
            mapping[c] = {"theme": best, "theme_base": best, "concepts": cons}
        cover = len(mapping) / len(codes) if codes else 0
        if cover < a.min_cover:
            print(f"  {day} 覆盖 {len(mapping)}/{len(codes)}={cover:.0%} → 跳过")
            skipped += 1
            continue
        if a.dry_run:
            print(f"  {day} 覆盖 {len(mapping)}/{len(codes)}={cover:.0%} → 可写")
            written += 1
            continue
        payload = {
            "schema": "theme_snapshot_v1",
            "date": day,
            "generated_at": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S"),
            "source": "hithink_backfill",
            "count": len(mapping),
            "mapping": mapping,
        }
        (SNAP / f"theme_{day}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        written += 1
        print(f"  {day} 覆盖 {len(mapping)}/{len(codes)}={cover:.0%} → 已写 source=hithink_backfill")

    print(f"[fill] {'可写' if a.dry_run else '写入'} {written} 天 · 跳过 {skipped} 天")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
