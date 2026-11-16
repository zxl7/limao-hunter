#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补全题材快照（2026-11-16 用户「过一遍系统自查，补全题材数据」）

## 为什么需要
`cache/theme_snapshot/theme_<date>.json` 是「当日题材归属」的冻结快照，
选股/复盘/连板台账全部按它聚合。缺快照的日子：
- `board_track` / `board_leader_daily` 无法回放；
- `promotion_plan` / `practice_0925` 的题材维度是空的；
- 复盘卡的「最强线」判不出来。

## 重要边界（⛔ 必须知道）
题材库**没有历史版本**——接口返回的是**当前**归属。所以补出来的快照是
「用今天的题材库回填历史日期」，**不等于当时的真实归属**（题材会改名/归并）。
因此本脚本写的快照 `source` 标成 `backfill`，与真实当日快照（`live`）区分开，
下游要区分时看这个字段。

## 用法
    python3 scripts/backfill_theme_snapshot.py --dry-run    # 只看缺哪些天
    python3 scripts/backfill_theme_snapshot.py              # 补（不覆盖已有）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "common"))

CACHE = ROOT / "cache"


def _zt_days() -> list[str]:
    return sorted(p.stem.replace("zt_", "") for p in (CACHE / "zt_history").glob("zt_*.json"))


def _theme_days() -> set[str]:
    return {p.stem.replace("theme_", "") for p in (CACHE / "theme_snapshot").glob("theme_*.json")}


def _valid_zt(day: str) -> bool:
    """只要收盘完整池（盘中快照 intraday_only 不算）。"""
    try:
        d = json.loads((CACHE / "zt_history" / f"zt_{day}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return bool(d.get("zt_list")) and d.get("intraday_only") in (None, False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    have = _theme_days()
    missing = [d for d in _zt_days() if d not in have and _valid_zt(d)]
    print(f"[backfill] 涨停历史 {len(_zt_days())} 天 · 已有题材快照 {len(have)} 天 · 缺 {len(missing)} 天")
    if not missing:
        print("[backfill] 无缺口")
        return 0
    print("  缺口:", ", ".join(missing))
    if a.dry_run:
        return 0

    from common.stock_theme import ThemeFetcher, save_theme_snapshot
    mapping = ThemeFetcher().fetch_all()
    if not mapping:
        print("[backfill] ⛔ 题材接口返回空，未写入（不编造）")
        return 1
    print(f"[backfill] 取到题材映射 {len(mapping)} 只")

    # ⛔ 覆盖度守卫（2026-11-16 实测）：接口返回的是**当日涨停池**，不是全市场题材库。
    #    拿今天这 21 只去回填历史日，等于给历史日只标 21 只 → 下游会当成"补完了"。
    #    要求「该日涨停票里 ≥60% 能命中题材」才允许写入，否则跳过并报缺口。
    written, skipped = 0, []
    for d in missing:
        try:
            zt = json.loads((CACHE / "zt_history" / f"zt_{d}.json").read_text(encoding="utf-8"))["zt_list"]
        except Exception:  # noqa: BLE001
            continue
        codes = [str(r.get("code")) for r in zt if r.get("code")]
        hit = sum(1 for c in codes if c in mapping)
        cover = hit / len(codes) if codes else 0
        if cover < 0.6:
            skipped.append((d, f"{hit}/{len(codes)}={cover:.0%}"))
            continue
        if save_theme_snapshot(d, mapping, source="backfill"):
            written += 1
    print(f"[backfill] 写入 {written} 天 · source=backfill（当前库回填，非当日真值）")
    if skipped:
        print(f"[backfill] ⛔ 覆盖不足跳过 {len(skipped)} 天（接口只有当日池，回填会误导）：")
        for d, c in skipped[:8]:
            print(f"    {d} 覆盖 {c}")
    return 0 if written or not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
