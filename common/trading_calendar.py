#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股交易日历 —— **唯一真源**（2026-10-09 定·全仓清扫）。

═══════════════════════════════════════════════════════════════════════
铁律：**「某一天是不是A股交易日」，只允许本模块回答。**
全仓库禁止再出现 `weekday() < 5` / `weekday() >= 5` 之类的自行判定；
`tests/test_trading_day_single_source.py` 会静态扫描并拦截（只放行带
`trading-day-fallback` 标记的降级兜底，以及已登记的白名单文件）。
`common/market_time.is_trading_day / in_trading` 是委托本模块的薄封装。
═══════════════════════════════════════════════════════════════════════

判定规则（顺序固定，不可被配置覆盖）：
  ① 周末恒休市 —— 沪深北交易所周末一律不开市。国务院调休安排里的「周末补班日」
     只约束机关/企事业单位，**不构成交易日**。故 `weekday() >= 5` 直接 False，
     **不读任何配置**。
  ② 工作日命中 `closed_dates`（法定休市）→ 休市；其余工作日 → 开市。

事故背景（2026-10-09）：
  · `config/a_share_calendar.json` 的 `open_dates` 把 2026-01-04 / 02-14 / 02-28 /
    05-09 / 09-20 / **10-10** 六个**周末**补班日列为开市日，且优先级高于周末回退
    → `next_a_share_trading_day('2026-10-09')` 错返 `2026-10-10`（周六），盘后 AI
    预案视图被写成 `ai_view_2026-10-10.json`，而周一竞价卡只读
    `ai_view_2026-10-12.json` ⇒ AI 判断整段消失。
  · 同时 `closed_dates` 漏列 2026-09-25（中秋·周五休市）→ 各处 `weekday()` 判定无感
    → 接口回吐 09-24 的完整池 → 归档出与 09-24 **逐字段相同**的幽灵日。
  处置：删除 `open_dates` 判定分支（字段清空、废弃），只保留 `closed_dates`。

依据：国办《2026年部分节假日安排的通知》+ 沪深北交易所 2026 年休市公告
（交易所原文明确「2月14日(星期六)、2月28日(星期六)为**周末休市**」）。

⚠️ 覆盖度风险（2026-10-14 审计 R1）：`closed_dates` **只覆盖 2026 年**，
   2027-01-01 起整年节假日会被判为交易日（幽灵交易日）。本模块提供
   `covered_years()` / `is_year_covered()` / `uncovered_years()` 供调用方**告警**，
   ⛔ 但**不**把「未覆盖」并入休市判定（那会连续拦掉整年真实交易日）。
   维护动作：国办公布次年安排后立即补录 `closed_dates`。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALENDAR_PATH = ROOT / "config" / "a_share_calendar.json"

# 向前/向后搜索上限（自然日）。一年内的最长连续休市远小于此，纯兜底防死循环。
_MAX_SCAN_DAYS = 370


def _closed_dates(path: Path | None = None) -> set[str]:
    """法定休市日集合。读失败 → 空集（此时只靠周末硬规则，不误拦工作日）。"""
    try:
        doc = json.loads((path or CALENDAR_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    values = doc.get("closed_dates") if isinstance(doc, dict) else []
    return {str(value) for value in values if len(str(value)) == 10}


def _coerce_date(value: date | datetime | str) -> date | None:
    """宽松解析成 date；非法输入返回 None（⛔ 不猜测）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def is_a_share_trading_day(
    value: date | datetime | str, *, calendar_path: Path | None = None
) -> bool:
    """`value` 是否为A股交易日（周末硬规则 + 法定休市表）。非法输入 → False。"""
    day = _coerce_date(value)
    if day is None:
        return False
    # ① 周末恒休市（含调休补班日）：硬规则，不读配置、不可被覆盖
    if day.weekday() >= 5:
        return False
    # ② 法定休市日 → 休市；其余工作日 → 开市
    return day.isoformat() not in _closed_dates(calendar_path)


def next_a_share_trading_day(
    date10: str, *, calendar_path: Path | None = None
) -> str | None:
    """返回**下一**A股交易日（不含入参当日）；入参非法时拒绝猜测（None）。"""
    day = _coerce_date(date10)
    if day is None:
        return None
    for _ in range(_MAX_SCAN_DAYS):
        day += timedelta(days=1)
        if is_a_share_trading_day(day, calendar_path=calendar_path):
            return day.isoformat()
    return None


def prev_a_share_trading_day(
    date10: str, *, calendar_path: Path | None = None
) -> str | None:
    """返回**上一**A股交易日（不含入参当日）；入参非法时拒绝猜测（None）。"""
    day = _coerce_date(date10)
    if day is None:
        return None
    for _ in range(_MAX_SCAN_DAYS):
        day -= timedelta(days=1)
        if is_a_share_trading_day(day, calendar_path=calendar_path):
            return day.isoformat()
    return None


def last_a_share_trading_day(
    value: date | datetime | str | None = None, *, calendar_path: Path | None = None
) -> date | None:
    """**≤ `value` 的最近一个A股交易日**（含当日）。`value=None` → 用今天。

    用途：体检/新鲜度判定要回答「上一个交易日是哪天」，不能只跳周末——
    否则长假/中秋这类**工作日休市**会被错当成交易日（09-25 事故同源）。
    """
    day = _coerce_date(value) if value is not None else datetime.now().date()
    if day is None:
        return None
    for _ in range(_MAX_SCAN_DAYS):
        if is_a_share_trading_day(day, calendar_path=calendar_path):
            return day
        day -= timedelta(days=1)
    return None


# ══════════════════════════════════════════════════════════════════════
# 日历覆盖度（2026-10-14 数据血缘审计 R1：跨年缺日历守卫）
# ══════════════════════════════════════════════════════════════════════
# 【风险 R1】`config/a_share_calendar.json` 的 `closed_dates` **只覆盖 2026 年**
#   （文件 note 自陈「每年公布安排后维护下一年度」）。而 `is_a_share_trading_day`
#   的规则是「工作日不在 closed_dates 表内 = 开市」⇒ 一旦跨到 2027，
#   元旦 / 春节 / 清明 / 劳动节… **全部会被判为交易日** → 幽灵交易日 + 幽灵归档，
#   与 2026-09-25（中秋漏列）事故**同型**，且这次是**整年**级别的。
#
# ⛔ 本组函数**只报告覆盖度，不改变交易日判定**——判定规则仍是
#   「周末硬规则 + closed_dates」。原因：把「未覆盖」直接判成休市，会在日历
#   维护滞后时**连续拦掉整年**的真实交易日，伤害远大于幽灵日。
#   故策略是「判定照旧 + 显式告警」，由调用方（落盘守护）决定怎么提醒人。
#
# 判定口径：「某年度被覆盖」= `closed_dates` 中至少有一条落在该年度。
#   A 股每年至少含元旦/春节/清明/劳动/端午/国庆的工作日休市，**任何年度都不可能为 0**
#   ⇒ 「条数 ≥ 1」是安全判据（不会把「已覆盖但节假日少」的年份误判成未覆盖）。


def covered_years(*, calendar_path: Path | None = None) -> set[int]:
    """`closed_dates` 覆盖到的年度集合（读失败 → 空集）。"""
    years: set[int] = set()
    for value in _closed_dates(calendar_path):
        try:
            years.add(int(value[:4]))
        except ValueError:
            continue
    return years


def is_year_covered(year: int, *, calendar_path: Path | None = None) -> bool:
    """`year` 年度是否已被休市表覆盖（见上方口径说明）。"""
    return int(year) in covered_years(calendar_path=calendar_path)


def uncovered_years(years, *, calendar_path: Path | None = None) -> list[int]:
    """给定年度序列中**未被覆盖**的那些（保持升序、去重）。⛔ 只报告，不改判定。"""
    seen = sorted({int(y) for y in years})
    return [y for y in seen if not is_year_covered(y, calendar_path=calendar_path)]


def calendar_note(*, calendar_path: Path | None = None) -> str:
    """文件 note 原文（供告警文案引用依据来源）；读不到 → 空串。"""
    try:
        doc = json.loads((calendar_path or CALENDAR_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    note = doc.get("note") if isinstance(doc, dict) else ""
    return str(note or "")
