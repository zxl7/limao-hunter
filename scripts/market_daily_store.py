#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场轻量日频底库命令行入口。

本脚本只维护 ``common.market_daily_store`` 的独立 DuckDB，不接入候选、盘中推送、
买卖裁决或题材判断。

用法：
  python3 scripts/market_daily_store.py init
  python3 scripts/market_daily_store.py import --parquet PATH [--keep-days 20]
  python3 scripts/market_daily_store.py status
  python3 scripts/market_daily_store.py bars 600000 [--limit 20]
  python3 scripts/market_daily_store.py prune [--keep-days 20]

推荐同步方式：同花顺 ``daily-k-10d`` 下载完成后，显式执行 import。该增量自带重叠日，
底库按 (code, trade_date) 覆盖并最终只保留最近 20 个实际交易日。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import market_daily_store as store  # noqa: E402


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="全市场轻量日频底库（最近20交易日）")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="初始化独立 DuckDB")
    imp = sub.add_parser("import", help="导入同花顺 daily-k / daily-k-10d Parquet")
    imp.add_argument("--parquet", required=True, type=Path)
    imp.add_argument("--keep-days", type=int, default=store.DEFAULT_KEEP_DAYS)
    imp.add_argument("--source", default="hithink:market_dump")
    sub.add_parser("status", help="查看覆盖范围和最近导入")
    bars = sub.add_parser("bars", help="查询单票最近日线")
    bars.add_argument("code")
    bars.add_argument("--limit", type=int, default=20)
    prune = sub.add_parser("prune", help="裁剪为最近N个实际交易日")
    prune.add_argument("--keep-days", type=int, default=store.DEFAULT_KEEP_DAYS)
    args = ap.parse_args(argv)

    if args.command == "init":
        _print({"ok": True, "db_path": str(store.init_db())})
        return 0
    if args.command == "import":
        _print(store.import_hithink_parquet(
            args.parquet, source=args.source, keep_days=args.keep_days,
        ))
        return 0
    if args.command == "status":
        _print(store.status())
        return 0
    if args.command == "bars":
        _print(store.recent_bars(args.code, limit=args.limit))
        return 0
    if args.command == "prune":
        _print(store.prune(keep_days=args.keep_days))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
