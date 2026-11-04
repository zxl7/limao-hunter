#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据底座只读查询命令行适配器。

供终端开发期中间件调用；生产服务直接复用 common.data_gateway。
仅支持受限单票查询，绝不开放路径、SQL或数据库下载。
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

from common import data_gateway  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="数据底座只读查询")
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("market-daily", help="查询单票近20个交易日日K")
    daily.add_argument("--code", required=True)
    daily.add_argument("--limit")

    registry = sub.add_parser("stock-registry", help="查询系统已观测的单票事实")
    registry.add_argument("--code", required=True)

    profile = sub.add_parser("stock-profile", help="查询单票事实画像（跨源聚合·只读）")
    profile.add_argument("--code", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "market-daily":
            payload = data_gateway.market_daily(args.code, args.limit)
        elif args.command == "stock-profile":
            payload = data_gateway.stock_profile(args.code)
        else:
            payload = data_gateway.stock_registry(args.code)
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"error": "invalid_request", "message": str(exc)}, ensure_ascii=False))
        return 2

    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
