#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""落盘「板块强度 + 同题材身位」快照（2026-11-11 · P0）

## 这个脚本存在的唯一理由

用户方案要「先选前三强板块，再在板块内按身位推龙一」，但**现有归档里没有这两样东西**：

- `zt_list` 只有逐票字段，没有「这个板块今天排第几」；
- `dragon_rank` 只在候选链路里算，**每天算完就丢**，没有历史。

半年后要回测「板块优先是否更好」，就必须**从今天开始每天存**。
所以本脚本是**纯落盘**：只读 `cache/zt_history`、`cache/theme_snapshot`、`cache/kline_qfq`，
只写 `cache/theme_strength_<date>.json` 与 `cache/ladder_rank_<date>.json`。

## ⛔ 边界

- 不改候选池、不改 `leader_key`、不改 09:25 闸门、不推群；
- 已存在的快照**直接覆盖**（纯派生数据，可随时重建）；
- 缺 `zt_list` 的归档日跳过（盘中不完整文件不算数），并在汇总里报出来。

用法：
    python3 scripts/snapshot_theme_ladder.py              # 全部归档日（幂等重建）
    python3 scripts/snapshot_theme_ladder.py --backfill 60
    python3 scripts/snapshot_theme_ladder.py --date 2026-11-11
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core.ladder_rank import KlineReturns, build_ladder  # noqa: E402
from wealth_code.core.theme_strength import build_theme_strength  # noqa: E402

CACHE = ROOT / "cache"
ZT_DIR = CACHE / "zt_history"


def archive_days() -> list[str]:
    """归档日即交易日序列（⛔ 不按日历猜）。"""
    return sorted(p.stem.replace("zt_", "") for p in ZT_DIR.glob("zt_*.json"))


def load_zt(day: str) -> list[dict] | None:
    """缺 `zt_list` 键 → None（不完整归档）；`zt_list: []` 是有效数据（当日 0 涨停）。"""
    try:
        d = json.loads((ZT_DIR / f"zt_{day}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if "zt_list" not in d:
        return None
    rows = d.get("zt_list") or []
    return [r for r in rows if isinstance(r, dict)]


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def snapshot_one(day: str, klines: KlineReturns) -> tuple[int, int] | None:
    """写一天的两份快照；返回 (板块数, 成员数)，不完整归档返回 None。"""
    rows = load_zt(day)
    if rows is None:
        return None
    strength = build_theme_strength(day, rows)
    ladder = build_ladder(day, rows, klines=klines)
    write_json(CACHE / f"theme_strength_{day}.json", strength)
    write_json(CACHE / f"ladder_rank_{day}.json", ladder)
    return strength["theme_count"], sum(t["zt_count"] for t in strength["themes"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="落盘板块强度 + 同题材身位快照（只读计算）")
    ap.add_argument("--date", help="只处理某一天（YYYY-MM-DD）")
    ap.add_argument("--backfill", type=int, default=0, help="只处理最近 N 个归档日；0=全部")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    days = archive_days()
    if args.date:
        days = [args.date]
    elif args.backfill > 0:
        days = days[-args.backfill:]

    klines = KlineReturns()
    ok = skipped = 0
    last = None
    for day in days:
        got = snapshot_one(day, klines)
        if got is None:
            skipped += 1
            continue
        ok += 1
        last = (day, got)
        if not args.quiet and len(days) <= 5:
            print(f"  {day} · 板块 {got[0]} 个 · 涨停 {got[1]} 只")

    if last:
        print(f"[snapshot_theme_ladder] 写入 {ok} 天（跳过不完整归档 {skipped} 天）"
              f"｜最近 {last[0]}：板块 {last[1][0]} 个 / 涨停 {last[1][1]} 只")
    else:
        print(f"[snapshot_theme_ladder] 无可用归档日（跳过 {skipped} 天）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
