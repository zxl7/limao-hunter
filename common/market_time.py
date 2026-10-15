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
- is_trading_day(dt)：**委托 `common.trading_calendar`（唯一真源）**
- in_trading(asof=None)：交易时段判定（asof='HH:MM' 流程触发时间；兼容 intraday_pulse 语义）
  —— 早盘 09:00-11:30(540-690) 或 下午 13:00-15:00(780-900)
- ts()：毫秒时间戳
- parse_dt()/age_seconds()：宽松解析（含时间戳）与龄期
- iso_bj()/iso_bj_from_unix()：**规范时间戳**（ISO8601 带 +08:00）—— 写 generated_ts / source_ts 用它
  （字段命名规范见文件末尾「时间戳字段规范」块；2026-10-14 审计 P1-⑦ 立）

铁律：
1. 任何脚本需要时区判断，一律 import 本模块；禁止再自写 BJ_TZ。
2. **交易日判定唯一真源 = `common.trading_calendar`**（2026-10-09 定）。本模块只做
   薄封装转发，**不得自行实现**「什么算交易日」。历史教训：本模块曾用
   `weekday() < 5`，把 2026-09-25（中秋·周五休市）当交易日 → 幽灵日归档，
   并被 feedback_matrix 等下游照抄，扩散成多份自实现。
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
    """A股交易日 —— **转发唯一真源** `common.trading_calendar.is_a_share_trading_day`。

    ⛔ 不得在本函数里自行判定（曾用 `weekday() < 5` → 工作日休市漏网）。
    `try/except` 仅用于「common 包不可导入」的极端环境，此时退回周末兜底。
    """
    d = dt if dt is not None else now()
    try:
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(d)
    except Exception:  # pragma: no cover - 极端环境兜底
        return d.weekday() < 5  # trading-day-fallback: 唯一真源不可导入时的周末兜底


def in_trading(asof: Optional[str] = None) -> bool:
    """交易时段判定（**交易日 + 时段**）。asof='HH:MM' 流程触发时间（守卫按触发时间
    判定，而非脚本执行/完成时间——与 intraday_pulse 语义一致）。

    交易日部分转发唯一真源；时段部分为本模块职责。
    """
    n = now()
    if not is_trading_day(n):
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


# ────────────────────────────────────────────────────────────────
# 时间戳字段规范（2026-10-14 数据血缘审计 P1-⑦ 立）
# ────────────────────────────────────────────────────────────────
# 事故背景：同一个字段名 `generated_ts` 在全系统并存 **3 种形态**
#   · `2026-10-13 17:19:11`            → 空格分隔、无时区（feedback_matrix / hithink / zt）
#   · `2026-10-13T17:19:11.961178`     → T 分隔、**naive**（capture_daily / theme_rotation / relay_shadow）
#   · `2026-10-13T17:20:03+08:00`      → ISO8601 带偏移（carrier_state / realtime_data_pipeline）
# 危害：aware 与 naive 相减直接 TypeError（`link_guard._age_seconds` 因此静默返回 None、
# 体检查不出断档）；`strptime("%Y-%m-%d %H:%M:%S")` 型读者遇带偏移值直接抛错；
# 更隐蔽的是 **`generated_ts` 有的存「采集时刻」、有的存「行情源时刻」**
# （实测 `emotion_*.json` 的 generated_ts 恒为 15:00:12 = 收盘源时刻，不是写入时刻）。
#
# ⛔ 字段命名规范（新代码一律遵守；存量逐步迁移，禁止一次性批量改写）：
#   generated_ts —— **本机采集/生成时刻**，ISO8601 带 +08:00（用本模块 `iso_bj()` 产出）
#   source_ts    —— **行情源自身的数据时刻**（如东财 f124、快照 ts），同一格式
#   data_date    —— 业务交易日 `YYYY-MM-DD`（与采集时刻解耦）
#   ⛔ 不要用 `ts` 同时表达「采集时刻」和「源时刻」（历史混用，遇歧义时按上面两项拆开）
# 判定「今天该有的产出出现了没」时，⛔ 不能用 mtime 单独作判据（见 config/skill_contract.md §1）。


def iso_bj(dt: Optional[datetime] = None) -> str:
    """**规范时间戳**：ISO8601 带 `+08:00`，秒级（如 `2026-10-14T17:19:11+08:00`）。

    写 `generated_ts` / `source_ts` 一律用它 —— 既有人读（带时区无歧义），
    也有机器读（`datetime.fromisoformat` 直接可用，且与 `now()` 同为 aware，可相减）。
    保留字面量切片兼容：`[:10]` 仍是日期、`[11:16]` 仍是 `HH:MM`（与旧格式一致）。
    """
    return (dt or now()).astimezone(BJ_TZ).isoformat(timespec="seconds")


def iso_bj_from_unix(sec: float | int | None) -> Optional[str]:
    """Unix 秒（如东财 `f124`）→ 规范时间戳；非法/缺失返回 None（⛔ 不拿本机时刻顶替）。"""
    try:
        v = float(sec)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    try:
        return datetime.fromtimestamp(v, BJ_TZ).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None

