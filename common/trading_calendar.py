#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股交易日历的唯一入口。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALENDAR_PATH = ROOT / "config" / "a_share_calendar.json"


def _closed_dates(path: Path | None = None) -> set[str]:
    try:
        doc = json.loads((path or CALENDAR_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    values = doc.get("closed_dates") if isinstance(doc, dict) else []
    return {str(value) for value in values if len(str(value)) == 10}


def is_a_share_trading_day(value: date | datetime | str, *, calendar_path: Path | None = None) -> bool:
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    else:
        try:
            day = datetime.strptime(str(value), "%Y-%m-%d").date()
        except ValueError:
            return False
    return day.weekday() < 5 and day.isoformat() not in _closed_dates(calendar_path)


def next_a_share_trading_day(date10: str, *, calendar_path: Path | None = None) -> str | None:
    """返回下一A股交易日；输入非法时拒绝猜测。"""
    try:
        day = datetime.strptime(str(date10), "%Y-%m-%d").date()
    except ValueError:
        return None
    for _ in range(370):
        day += timedelta(days=1)
        if is_a_share_trading_day(day, calendar_path=calendar_path):
            return day.isoformat()
    return None
