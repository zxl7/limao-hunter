#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修正观察池里的「板块核心观察」行（2026-11-16 数据错乱的事后修复）

## 背景
`board_core_watch.picks()` 允许「最近 3 日有涨停」，于是断板票会带着**上次涨停的高度**
混进今天的观察池（实证：603366 日出东方 11-12 五板后断板，11-16 仍以 boards=5 出现）。

## 这个脚本做什么
- 只动 `watch` 里 `watch_group == "板块核心观察"` 的行，**接力池与其他观察行一律不碰**；
- 用当日涨停池重算高度：今天在板 → 今日板数；今天断板 → 0 + 备注；
- 写之前先备份到 `cache/backups/`。

## 用法
    python3 scripts/fix_watch_pool.py --date 2026-11-16 --dry-run
    python3 scripts/fix_watch_pool.py --date 2026-11-16
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

CAND_DIR = ROOT / "wealth_code" / "cache"
ZT_DIR = ROOT / "cache" / "zt_history"
BJ = ZoneInfo("Asia/Shanghai")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    cpath = CAND_DIR / f"candidates-{a.date}.json"
    zpath = ZT_DIR / f"zt_{a.date}.json"
    if not cpath.exists() or not zpath.exists():
        print(f"[fix] 缺文件：{cpath.name} 或 {zpath.name}")
        return 1

    cand = json.loads(cpath.read_text(encoding="utf-8"))
    zt = json.loads(zpath.read_text(encoding="utf-8")).get("zt_list") or []
    today = {str(r.get("code") or "").zfill(6): int(r.get("boards") or 0) for r in zt}

    from core import board_core_watch as bc
    watch = list(cand.get("watch") or [])
    relay_codes = {str(r.get("code")) for r in (cand.get("relay") or [])}
    kept = [w for w in watch if w.get("watch_group") != "板块核心观察"]
    kept_codes = {str(w.get("code")) for w in kept} | relay_codes

    picks = bc.picks(kept_codes)
    added = []
    for p in picks:
        code = str(p["code"])
        b_today = today.get(code, 0)
        last = str(p.get("last_zt_date") or "")
        added.append({
            **{k: v for k, v in p.items() if k != "last_zt_date"},
            "boards": b_today,
            "last_zt_date": last,
            "is_today_zt": bool(b_today),
            "boards_note": "" if b_today else f"上次涨停 {last}·{p['boards']}板（今天已断板）",
            "core_theme": p.get("sector"),
            "participation_eligibility": "observe_only", "buyable": False,
            "watch_only": True, "watch_group": "板块核心观察",
            "watch_reason": (f"这条线的核心（{p.get('sector')}·core {p.get('core_score')}"
                             f"·参与{p.get('join_rate')}%）：只看它动不动，不买"),
            "board_core_score": p.get("core_score"),
            "board_core_join": p.get("join_rate"),
        })

    before = [w for w in watch if w.get("watch_group") == "板块核心观察"]
    print(f"[fix] {a.date} 观察池：核心观察 {len(before)} 行 → 重算 {len(added)} 行")
    for w in added:
        flag = "在板" if w["is_today_zt"] else "断板"
        print(f"   {w['code']} {w['name']:<6} boards={w['boards']} ({flag}) {w['boards_note']}")

    if a.dry_run:
        return 0

    bak = ROOT / "cache" / "backups" / f"candidates-{a.date}.json.bak_{datetime.now(BJ).strftime('%Y%m%d_%H%M%S')}"
    bak.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cpath, bak)
    cand["watch"] = kept + added
    cpath.write_text(json.dumps(cand, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[fix] 已写回 {cpath.name}（备份 {bak.name}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
