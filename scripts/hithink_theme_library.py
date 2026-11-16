#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同花顺概念库沉淀（2026-11-16 用户「A 试试」）

## 它和选股宝库的分工
| 库 | 来源 | 覆盖 | 用途 |
|---|---|---|---|
| `cache/theme_snapshot/theme_<date>.json` | 选股宝 plates | **当日涨停池**（20~130 只） | 每日选股/复盘**主口径** |
| `cache/theme_library.json` | 上面的每日快照合并 | 累计（已 1289 只） | 历史回溯 |
| **`cache/theme_library_hithink.json`** | 同花顺 `index.catalog` + `index.constituents` | **全市场概念成分** | 历史缺口回填 / 交叉校验 |

⛔ 不混口径：同花顺只做「兜底与校验」，主链仍用选股宝（用户 11-03 定调）。

## 实现
- `index.catalog` 拉 390 个概念指数（`885xxx.TI`）；
- 逐个 `index.constituents --thscode` 拉成分股，原始响应缓存到 `cache/hithink_concepts/`；
- 汇总成 `code → {name, concepts[]}`，写 `cache/theme_library_hithink.json`；
- 支持 `--limit`（试跑）、断点续跑（已缓存的跳过）、`--refresh` 强制重拉。

## 用法
    python3 scripts/hithink_theme_library.py --limit 5     # 小样试跑
    python3 scripts/hithink_theme_library.py               # 全量（390 个概念）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"
RAW = CACHE / "hithink_concepts"
OUT = CACHE / "theme_library_hithink.json"
BJ = ZoneInfo("Asia/Shanghai")
CLI = "hithink-finance"


def _run(args: list[str], out: Path) -> dict | None:
    cmd = [CLI, *args, "--format", "json", "--output", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return None
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _catalog(refresh: bool) -> list[dict]:
    p = CACHE / "hithink_index_catalog.json"
    if p.exists() and not refresh:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d.get("data", {}).get("item") or []
        except Exception:  # noqa: BLE001
            pass
    d = _run(["index", "catalog"], p)
    return (d or {}).get("data", {}).get("item") or []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个概念（试跑用）")
    ap.add_argument("--refresh", action="store_true", help="强制重拉已缓存的概念")
    ap.add_argument("--sleep", type=float, default=0.25, help="每次调用间隔秒")
    a = ap.parse_args(argv)

    RAW.mkdir(parents=True, exist_ok=True)
    concepts = _catalog(a.refresh)
    if not concepts:
        print("[thlib] ⛔ catalog 为空（认证/网络问题）")
        return 1
    print(f"[thlib] 概念目录 {len(concepts)} 个")
    todo = concepts[: a.limit] if a.limit else concepts

    entries: dict[str, dict] = {}
    fetched = skipped = failed = 0
    for i, c in enumerate(todo, 1):
        code = str(c.get("thscode") or "")
        name = str(c.get("name") or "")
        if not code:
            continue
        raw = RAW / f"{code}.json"
        data = None
        if raw.exists() and not a.refresh:
            try:
                data = json.loads(raw.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = None
        if data is None:
            data = _run(["index", "constituents", "--thscode", code], raw)
            fetched += 1
            time.sleep(a.sleep)
        else:
            skipped += 1
        items = ((data or {}).get("data") or {}).get("item") or []
        if not items:
            failed += 1
            continue
        for it in items:
            ticker = str(it.get("ticker") or "")
            if not ticker:
                continue
            e = entries.setdefault(ticker, {"name": str(it.get("name") or ""), "concepts": []})
            if name and name not in e["concepts"]:
                e["concepts"].append(name)
        if i % 50 == 0:
            print(f"  … {i}/{len(todo)}（新拉 {fetched} · 复用 {skipped} · 空 {failed}）")

    payload = {
        "schema": "theme_library_hithink_v1",
        "generated_ts": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "同花顺 index.catalog / index.constituents",
        "concept_count": len(todo),
        "code_count": len(entries),
        "entries": entries,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[thlib] 完成：{len(todo)} 个概念 · {len(entries)} 只票 · 新拉 {fetched} · 空 {failed}")
    print(f"[thlib] → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
