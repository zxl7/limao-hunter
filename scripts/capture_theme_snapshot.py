#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓当日题材快照（**选股宝 plates 为基座** · 2026-11-16 用户「用选股宝的题材为基座」）

## 为什么单独有这个脚本
`pipeline_eod` 15:30 首跑时会调 `common.stock_theme.get_theme_map()` 落快照。
但选股宝 `surge_stock/stocks?uplimit=true` 是**盘中活、盘后随时可能关**的接口：
某天 15:30 抓空 → 当天快照永久缺失（题材归属没有历史版本，事后补不回来）。

本脚本的作用：**兜底重试 + 覆盖度自检**。
- 已有快照且覆盖够 → 什么都不做（⛔ 不覆盖首版）；
- 没有快照 / 覆盖不足 → 抓一次写入，并把覆盖度打出来；
- 覆盖不足（<60% 涨停票命中）→ 报警但**不写半成品**（避免下游误以为补完了）。

## 用法
    python3 scripts/capture_theme_snapshot.py                # 今天
    python3 scripts/capture_theme_snapshot.py --date 2026-11-16
    python3 scripts/capture_theme_snapshot.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "cache"
BJ = ZoneInfo("Asia/Shanghai")


def _today() -> str:
    return datetime.now(BJ).strftime("%Y-%m-%d")


def _zt_codes(day: str) -> list[str]:
    p = CACHE / "zt_history" / f"zt_{day}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [str(r.get("code")) for r in (d.get("zt_list") or []) if r.get("code")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=_today())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="池子未定稿也写（⛔ 会冻结残缺映射，慎用）")
    a = ap.parse_args(argv)
    day = a.date

    from common.stock_theme import load_theme_snapshot, ThemeFetcher, save_theme_snapshot

    codes = _zt_codes(day)
    if not codes and not a.force:
        # ⛔ 池子没定稿就写快照 = 把盘中 21 只冻结成「当日题材映射」，
        #    15:30 EOD 会读到它并跳过重抓（save 默认不覆盖）→ 全天题材缺失。
        print(f"[theme] {day} 还没有收盘涨停池（盘中/未落盘）→ 先不写，等 EOD 或 --force")
        return 2
    snap = load_theme_snapshot(day, cache_dir=CACHE)
    if snap:
        m = snap.get("mapping") or {}
        hit = sum(1 for c in codes if c in m)
        cover = hit / len(codes) if codes else 1.0
        print(f"[theme] {day} 已有快照 {len(m)} 只 · 覆盖 {hit}/{len(codes)}={cover:.0%}（source={snap.get('meta', {}).get('source')}）")
        if cover >= 0.6 or not codes:
            return 0
        print("[theme] 覆盖不足，尝试重抓")

    mapping = ThemeFetcher().fetch_all()
    print(f"[theme] {day} 选股宝返回 {len(mapping)} 只")
    if not mapping:
        print("[theme] ⛔ 接口为空（盘后关闭？）→ 不写快照，等下次重试")
        return 1
    hit = sum(1 for c in codes if c in mapping)
    cover = hit / len(codes) if codes else 1.0
    print(f"[theme] 今日涨停 {len(codes)} 只 · 命中题材 {hit}（{cover:.0%}）")
    if codes and cover < 0.6:
        print("[theme] ⛔ 覆盖不足 60%，不写半成品（避免下游误判「题材已补全」）")
        return 1
    if a.dry_run:
        return 0
    p = save_theme_snapshot(day, mapping, cache_dir=CACHE, source="live")
    print(f"[theme] 已写入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
