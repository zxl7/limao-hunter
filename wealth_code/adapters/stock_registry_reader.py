#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股事实底库只读适配层。

业务模块只通过本层读取个股身份、逐日涨停事实和技术派生；不在这里写库，
也不把策略候选、市场情绪或 AI 观点塞进个股事实层。

读取模式：
- ``registry``：只读 SQLite 底库，缺料即诚实返回空；
- ``registry_fallback``（默认）：底库优先，底库无可用记录时由调用方决定是否回退旧归档；
- ``legacy``：仅供夹具/回放显式指定，不读底库。
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from common import stock_registry as registry


READ_MODE_ENV = "STOCK_REGISTRY_READ_MODE"
DEFAULT_READ_MODE = "registry_fallback"


def read_mode() -> str:
    value = os.getenv(READ_MODE_ENV, DEFAULT_READ_MODE).strip().lower()
    if value in {"registry", "registry_fallback", "legacy"}:
        return value
    return DEFAULT_READ_MODE


def registry_first_enabled() -> bool:
    return read_mode() in {"registry", "registry_fallback"}


def _date10(value: str | None) -> str:
    return str(value or "").strip()[:10]


def _date_from_for_window(end: str, days: int) -> str:
    """用足够宽的自然日窗口覆盖指定交易日数量，不把周末自行当交易日。"""
    try:
        end_date = date.fromisoformat(end)
    except ValueError:
        return ""
    # 长假和临时休市也可能存在，给 20 个交易日留 180 个自然日冗余。
    span = max(30, int(days) * 9)
    return (end_date - timedelta(days=span)).isoformat()


def load_zt_series_from_registry(
    *,
    days: int,
    upto: str | None = None,
    db_path: str | Path | None = None,
) -> tuple[dict[str, list[tuple[str, int]]], list[str]]:
    """从 ``stock_daily`` 读取收盘涨停序列。

    返回形状与 ``defense_gate.load_zt_series`` 完全一致：
    ``series[code] = [(trade_date, boards), ...]`` 和有涨停事实的交易日列表。
    只读取 ``limit_up_close=1``，不把未命中底库推断为未涨停。
    """
    if not registry_first_enabled() or read_mode() == "legacy":
        return {}, []
    end = _date10(upto) or registry.latest_trading_day() or date.today().isoformat()
    start = _date_from_for_window(end, days)
    if not start:
        return {}, []
    try:
        rows = registry.query_date_range(
            start,
            end,
            limit=max(2000, int(days) * 500),
            only_limit_up=True,
            db_path=db_path,
        )
    except Exception:
        return {}, []
    if not rows:
        return {}, []

    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        day = _date10(row.get("trade_date"))
        code = registry.normalize_code(row.get("code"))
        if not day or not code:
            continue
        by_day.setdefault(day, []).append(row)
    all_days = sorted(by_day)
    selected_days = all_days[-max(1, int(days)):]
    keep = set(selected_days)
    series: dict[str, list[tuple[str, int]]] = {}
    for day in selected_days:
        for row in by_day.get(day, []):
            try:
                boards = int(row.get("boards") or 0)
            except (TypeError, ValueError):
                boards = 0
            series.setdefault(registry.normalize_code(row["code"]), []).append((day, boards))
    series = {
        code: sorted(values, key=lambda x: x[0])
        for code, values in series.items()
        if values
    }
    return series, selected_days


def get_stock(code: str, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """读取一票的身份、标签、最新底库事实和技术派生。"""
    if not registry_first_enabled():
        return None
    try:
        return registry.get_by_code(code, db_path=db_path)
    except Exception:
        return None


def get_daily_facts(
    date_from: str,
    date_to: str,
    *,
    only_limit_up: bool = False,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """读取逐日事实；失败返回空，由上层执行显式降级。"""
    if not registry_first_enabled():
        return []
    try:
        return registry.query_date_range(
            date_from,
            date_to,
            only_limit_up=only_limit_up,
            db_path=db_path,
        )
    except Exception:
        return []
