#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材库沉淀（2026-11-16 用户「我们不能沉淀吗？」）

## 要解决的问题
每日题材快照 `cache/theme_snapshot/theme_<date>.json` 是**按天孤立**的：
- 想回答「这只票历史上属过哪些题材」「这条线的成员怎么变」只能一天天翻；
- 题材源（选股宝）不提供历史，快照丢了就永久丢了。

本脚本把每天的快照**合并成一份累计库**：`cache/theme_library.json`
- 每条 = 一个股票代码 → 出现过的题材集合、首见日、末见日、出现天数；
- 幂等：每天跑一次只会追加当天信息，不会丢历史。

## 边界（⛔ 别误会）
- 只沉淀「我们**已经抓到过**的题材」，不编造没抓到的日子；
- 题材名会随选股宝改名/归并漂移，所以 `themes_seen` 保留**所有历史名字**，
  `theme_latest` 是最新一次的名字（下游做强度统计建议用 latest + theme_base）。
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"
SNAP = CACHE / "theme_snapshot"
OUT = CACHE / "theme_library.json"
BJ = ZoneInfo("Asia/Shanghai")


def build() -> dict:
    entries: dict[str, dict] = {}
    days = []
    for p in sorted(SNAP.glob("theme_*.json")):
        day = p.stem.replace("theme_", "")
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        mapping = snap.get("mapping") or {}
        if not mapping:
            continue
        days.append(day)
        for code, info in mapping.items():
            if not isinstance(info, dict):
                continue
            theme = str(info.get("theme") or "").strip()
            base = str(info.get("theme_base") or theme).strip()
            if not theme:
                continue
            e = entries.setdefault(str(code), {
                "themes_seen": [],
                "theme_latest": "",
                "theme_base_latest": "",
                "first_day": day,
                "last_day": day,
                "day_count": 0,
            })
            seen = e["themes_seen"]
            for t in (theme, base):
                if t and t not in seen:
                    seen.append(t)
            e["theme_latest"] = theme
            e["theme_base_latest"] = base
            e["first_day"] = min(e["first_day"], day)
            e["last_day"] = max(e["last_day"], day)
            e["day_count"] += 1

    return {
        "schema": "theme_library_v1",
        "generated_ts": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "选股宝 plates（经 theme_snapshot 每日沉淀）",
        "days": days,
        "day_count": len(days),
        "code_count": len(entries),
        "entries": entries,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--query", help="查某只票的题材历史（代码，如 603396）")
    a = ap.parse_args(argv)

    lib = build()
    if a.query:
        e = (lib["entries"].get(a.query) or {})
        print(json.dumps({"code": a.query, **e}, ensure_ascii=False, indent=1) if e else "无记录")
        return 0

    if not lib["day_count"]:
        print("[theme_lib] 没有可用快照，未写入")
        return 1
    OUT.write_text(json.dumps(lib, ensure_ascii=False), encoding="utf-8")
    if not a.quiet:
        print(f"[theme_lib] {lib['day_count']} 天 · {lib['code_count']} 只 → {OUT}")
        print(f"[theme_lib] 覆盖 {lib['days'][0]} ~ {lib['days'][-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
