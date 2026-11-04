"""数据底座的只读查询投影。

此模块是终端数据底座的唯一查询边界：
- 只接受 6 位证券代码；
- 市场日K最多返回最近 20 个交易日；
- 个股事实只返回固定字段白名单；
- 不返回数据库路径、SQL、原始扩展字段或文件系统信息。

它不参与候选、裁决、推送、调度或任何落盘操作。
"""
from __future__ import annotations

from datetime import date, datetime
import math
import re
from typing import Any

CODE_RE = re.compile(r"^\d{6}$")
MARKET_DAILY_LIMIT_MAX = 20


def normalize_code(code: Any) -> str | None:
    token = str(code or "").strip()
    return token if CODE_RE.fullmatch(token) else None


def normalize_limit(value: Any, *, default: int = MARKET_DAILY_LIMIT_MAX) -> int | None:
    if value is None or value == "":
        return default
    try:
        limit = int(str(value))
    except (TypeError, ValueError):
        return None
    return limit if 1 <= limit <= MARKET_DAILY_LIMIT_MAX else None


def _json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _project_bar(row: dict[str, Any]) -> dict[str, Any]:
    """日K固定字段白名单。题材、策略与数据库内部字段不得穿透。"""
    fields = (
        "code", "thscode", "trade_date", "open", "high", "low", "close",
        "volume", "amount", "currency", "source", "ingested_at",
    )
    return {key: _json_value(row.get(key)) for key in fields}


def market_daily(code: Any, limit: Any = None) -> dict[str, Any]:
    """返回单票近月原始未复权OHLCV；无数据与无数据库明确区分。"""
    token = normalize_code(code)
    if not token:
        raise ValueError("code 必须为 6 位数字")
    take = normalize_limit(limit)
    if take is None:
        raise ValueError(f"limit 必须为 1 到 {MARKET_DAILY_LIMIT_MAX} 的整数")

    from common import market_daily_store as store

    bars = store.recent_bars(token, limit=take)
    from common.rsi_evidence import build_evidence
    asof = max((str(row["trade_date"]) for row in bars), default=date.today().isoformat())
    return {
        "code": token,
        "limit": take,
        "bars": [_project_bar(row) for row in bars],
        "rsi_evidence": build_evidence(token, bars, asof=asof),
    }


def _project_technical(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    fields = (
        "bar_date", "source", "status", "close", "high", "low", "ma5", "ma10",
        "ma20", "ret_3d", "ret_5d", "ret_10d", "high_20d", "high_60d",
        "new_high_20d", "new_high_60d", "ma_bullish", "volume_ratio_5", "data_quality",
    )
    return {key: _json_value(row.get(key)) for key in fields}


def _project_stock(row: dict[str, Any]) -> dict[str, Any]:
    """个股事实固定字段白名单；扩展原文与内部留痕不对终端开放。"""
    fields = (
        "code", "name", "market", "is_20cm", "industry", "themes", "is_limit_up_today",
        "is_limit_up_basis", "limit_up_now", "limit_up_touched", "limit_up_close", "boards",
        "pct", "price", "daily_asof_date", "daily_is_latest_trading_day", "first_seen_date",
        "last_seen_ts", "source", "data_quality_flags",
    )
    result = {key: _json_value(row.get(key)) for key in fields}
    result["technical"] = _project_technical(row.get("technical"))
    return result


def stock_registry(code: Any) -> dict[str, Any]:
    """返回系统实际观测过的单票事实；未命中不被解释为市场中不存在。"""
    token = normalize_code(code)
    if not token:
        raise ValueError("code 必须为 6 位数字")

    from common import stock_registry as registry

    row = registry.get_by_code(token)
    return {
        "code": token,
        "found": row is not None,
        "stock": _project_stock(row) if row else None,
    }


def stock_profile(code: Any) -> dict[str, Any]:
    """单票事实画像（跨日：身份/当日/历史/技术/题材/股性/连板轨迹）。

    与 `stock_registry` 的区别：那个是底库单表投影，这个是**跨源聚合**，
    每维带 `status`（ok/stale/missing/error）与 `asof`，缺失不等于 0。
    仍不返回数据库路径、SQL 或文件系统信息。
    """
    token = normalize_code(code)
    if not token:
        raise ValueError("code 必须为 6 位数字")

    from common import stock_profile as profile

    view = profile.build_profile(token)
    if not view.get("enabled", True):
        return {"code": token, "found": False, "disabled": True,
                "reason": "stock_profile 已关闭（ENABLE_STOCK_PROFILE=0）"}
    return {"code": token, "found": bool(view.get("found")), "profile": view}
