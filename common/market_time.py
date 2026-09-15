#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一 A 股时间/交易日工具（2026-08-17 架构重构 P0-2 · 复用优先：合并 13 文件各自实现）

背景：intraday_pulse/feedback_matrix/ma5_check/capture_daily/build_cards/theme_roll 等
13 个文件各自定义 BJ_TZ / now / today / in_trading / is_trading_day，
时区常量漂移会导致推送时点错乱（历史踩坑）。本模块为唯一实现。

设计（兼容既有调用）：
- BJ_TZ：北京时区常量（timezone(timedelta(hours=8))）
- now()：当前北京时间 datetime（aware）
- today()：'YYYY-MM-DD'；today_c()：'YYYYMMDD'
- is_trading_day(dt)：周一到周五（法定节假日由下游涨停池空/自交守卫兜底，与 feedback_matrix 一致）
- in_trading(asof=None)：交易时段判定（asof='HH:MM' 流程触发时间；兼容 intraday_pulse 语义）
  —— 早盘 09:00-11:30(540-690) 或 下午 13:00-15:00(780-900)
- ts()：毫秒时间戳

铁律：任何脚本需要时区/交易日/交易时段判断，一律 import 本模块；禁止再自写 BJ_TZ/in_trading。
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional

BJ_TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    """当前北京时间（aware datetime）。"""
    return datetime.now(BJ_TZ)


def today() -> str:
    return now().strftime("%Y-%m-%d")


def today_c() -> str:
    return now().strftime("%Y%m%d")


def ts() -> int:
    """毫秒时间戳（int）。"""
    return int(_time.time() * 1000)


def is_trading_day(dt: Optional[datetime] = None) -> bool:
    """A股交易日：周一到周五。法定节假日由下游「涨停池空/自交」双保险守卫兜底。"""
    d = dt if dt is not None else now()
    return d.weekday() < 5


def in_trading(asof: Optional[str] = None) -> bool:
    """交易时段判定。asof='HH:MM' 流程触发时间（守卫按触发时间判定，
    而非脚本执行/完成时间——与 intraday_pulse 语义一致）。"""
    n = now()
    if n.weekday() >= 5:
        return False
    if asof:
        try:
            hh, mm = asof.strip().split(":")
            t = int(hh) * 60 + int(mm)
        except Exception:
            t = n.hour * 60 + n.minute
    else:
        t = n.hour * 60 + n.minute
    return (540 <= t <= 690) or (780 <= t <= 900)


def parse_dt(value: str | float | int) -> Optional[datetime]:
    """宽松解析时间 → 北京时间 aware datetime。
    支持 ISO 字符串（'YYYY-MM-DD HH:MM:SS'/'2026-08-17T15:00'）与时间戳(秒/毫秒)。
    解析失败返回 None。"""
    try:
        if isinstance(value, str):
            s = value.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BJ_TZ)
            return dt.astimezone(BJ_TZ)
        v = float(value)
        if v > 1e12:  # 毫秒
            v = v / 1000.0
        return datetime.fromtimestamp(v, BJ_TZ)
    except (ValueError, TypeError, OverflowError):
        return None


def age_seconds(ts_value: str | float | int | None) -> Optional[float]:
    """距给定时间点的秒数（正=过去）。None/解析失败返回 None。"""
    if ts_value is None:
        return None
    dt = parse_dt(ts_value)
    if dt is None:
        return None
    return (now() - dt).total_seconds()
