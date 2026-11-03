#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场轻量日频底库（market daily store）。

定位：为短线接力提供最近一个月（20 个交易日）的全市场**客观日线横截面**。
它与现有两类数据严格分开：

- ``common.stock_registry``：系统已经看见过的个股对象、盘中观测与技术派生；不是全市场。
- ``cache/theme_snapshot`` / ``cache/top5_history``：题材映射与题材热度历史；题材会漂移，按日冻结。
- 本模块：按 ``(code, trade_date)`` 保存全市场 OHLCV 原始事实，只保留 20 个交易日。

本模块不抓取、不推送、不产生买卖结论，也不把题材名称写进日线表。后续消费层若要判断
某日某票的题材，必须按 ``code + trade_date`` 关联当天题材快照，不能读取当前题材倒灌历史。

输入契约：同花顺 Market Dump 的 Parquet（``daily-k`` 或 ``daily-k-10d``）：
``thscode,date_ms,open_price,high_price,low_price,close_price,volume,turnover``。
日线原始价格为未复权；接力场景只用于近 20 日位置、成交和活跃度，当前不引入复权因子。

写入契约：只有显式 CLI/调用才写 ``cache/market_daily/market_daily.duckdb``；无自动任务、
无既有策略/推送链路接入。``MARKET_DAILY_ENABLED=0`` 可整体关掉写入。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STORE_DIR = ROOT / "cache" / "market_daily"
DEFAULT_DB = STORE_DIR / "market_daily.duckdb"
DEFAULT_KEEP_DAYS = 20
SCHEMA_VERSION = "1"
_BJ = timezone(timedelta(hours=8))
_REQUIRED_COLUMNS = {
    "thscode", "date_ms", "open_price", "high_price", "low_price",
    "close_price", "volume", "turnover",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_daily_bars(
  code VARCHAR NOT NULL,
  thscode VARCHAR NOT NULL,
  trade_date DATE NOT NULL,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  close DOUBLE,
  volume DOUBLE,
  amount DOUBLE,
  currency VARCHAR,
  source VARCHAR NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_market_daily_date ON market_daily_bars(trade_date);
CREATE INDEX IF NOT EXISTS idx_market_daily_code_date ON market_daily_bars(code, trade_date);

CREATE TABLE IF NOT EXISTS market_daily_ingests(
  id VARCHAR PRIMARY KEY,
  source VARCHAR NOT NULL,
  source_path VARCHAR NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL,
  input_rows BIGINT NOT NULL,
  min_trade_date DATE,
  max_trade_date DATE,
  retained_trade_days INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market_daily_meta(
  key VARCHAR PRIMARY KEY,
  value VARCHAR NOT NULL
);
"""


def enabled() -> bool:
    """总开关；关闭后所有写入入口明确返回 disabled。"""
    return str(os.environ.get("MARKET_DAILY_ENABLED", "1")).strip().lower() not in {
        "0", "false", "no", "off",
    }


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """显式路径优先，测试可注入临时库。"""
    if db_path is not None:
        return Path(db_path)
    raw = str(os.environ.get("LIMAO_MARKET_DAILY_DB", "")).strip()
    return Path(raw) if raw else DEFAULT_DB


def _now() -> datetime:
    return datetime.now(_BJ)


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise RuntimeError("缺少 duckdb：全市场日频底库未初始化") from exc
    return duckdb


def connect(db_path: str | Path | None = None):
    """打开并初始化独立 DuckDB；不会触碰 stock_registry。"""
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _duckdb().connect(str(path))
    conn.execute(_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO market_daily_meta VALUES ('schema_version', ?)",
        [SCHEMA_VERSION],
    )
    return conn


def connect_readonly(db_path: str | Path | None = None):
    """严格只读；缺库报错，不建目录、不初始化、不更新元信息。"""
    path = resolve_db_path(db_path)
    if not path.is_file():
        raise FileNotFoundError("近月日K数据库未初始化")
    return _duckdb().connect(str(path), read_only=True)


def init_db(db_path: str | Path | None = None) -> Path:
    """显式初始化空库。"""
    path = resolve_db_path(db_path)
    conn = connect(path)
    conn.close()
    return path


def _parquet_columns(conn, parquet_path: Path) -> set[str]:
    rows = conn.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(parquet_path)]).fetchall()
    return {str(row[0]) for row in rows}


def _assert_parquet_contract(conn, parquet_path: Path) -> None:
    if not parquet_path.exists() or not parquet_path.is_file():
        raise FileNotFoundError(f"日K Parquet 不存在：{parquet_path}")
    cols = _parquet_columns(conn, parquet_path)
    missing = sorted(_REQUIRED_COLUMNS - cols)
    if missing:
        raise ValueError(f"日K Parquet 缺字段：{', '.join(missing)}")


def _retained_dates(conn, keep_days: int) -> list[Any]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM market_daily_bars ORDER BY trade_date DESC LIMIT ?",
        [keep_days],
    ).fetchall()]


def prune(*, keep_days: int = DEFAULT_KEEP_DAYS, db_path: str | Path | None = None) -> dict[str, Any]:
    """按最近 N 个**实际有数据的交易日**滚动保留，非日历日截断。"""
    if keep_days <= 0:
        raise ValueError("keep_days 必须大于 0")
    conn = connect(db_path)
    try:
        before = int(conn.execute("SELECT COUNT(*) FROM market_daily_bars").fetchone()[0])
        kept = _retained_dates(conn, int(keep_days))
        if not kept:
            return {"before": before, "deleted": 0, "kept_days": 0, "min_date": None, "max_date": None}
        floor = min(kept)
        conn.execute("DELETE FROM market_daily_bars WHERE trade_date < ?", [floor])
        after = int(conn.execute("SELECT COUNT(*) FROM market_daily_bars").fetchone()[0])
        return {
            "before": before,
            "deleted": before - after,
            "kept_days": len(kept),
            "min_date": str(floor),
            "max_date": str(max(kept)),
        }
    finally:
        conn.close()


def import_hithink_parquet(
    parquet_path: str | Path,
    *,
    source: str = "hithink:market_dump",
    keep_days: int = DEFAULT_KEEP_DAYS,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """导入同花顺全市场日K或10日增量 Parquet，并裁成最近 N 个交易日。

    同一 ``(code, trade_date)`` 使用最新导入的同花顺原始条覆盖，解决 10 日增量与本地
    重叠；不做复权、不混入题材、也不把未命中个股的任何状态推断为 0。
    """
    if not enabled():
        return {"ok": True, "status": "disabled", "written": 0}
    if keep_days <= 0:
        raise ValueError("keep_days 必须大于 0")
    path = Path(parquet_path)
    conn = connect(db_path)
    try:
        _assert_parquet_contract(conn, path)
        input_rows = int(conn.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(path)]).fetchone()[0])
        if input_rows <= 0:
            return {"ok": False, "status": "empty_input", "written": 0, "path": str(path)}
        before = int(conn.execute("SELECT COUNT(*) FROM market_daily_bars").fetchone()[0])
        now = _now()
        # 仅保留恰好 6 位股票代码。数据层保留沪深京全市场事实；688/北交所过滤属于策略层，
        # 不能在底层丢数据。
        conn.execute(
            """
            INSERT OR REPLACE INTO market_daily_bars
              (code, thscode, trade_date, open, high, low, close, volume, amount, currency, source, ingested_at)
            SELECT
              regexp_extract(thscode, '^([0-9]{6})', 1) AS code,
              thscode,
              CAST(epoch_ms(date_ms) AS DATE) AS trade_date,
              open_price, high_price, low_price, close_price, volume, turnover,
              currency, ?, ?
            FROM read_parquet(?)
            WHERE regexp_matches(thscode, '^[0-9]{6}\\.(SH|SZ|BJ)$')
              AND date_ms IS NOT NULL
            """,
            [source, now, str(path)],
        )
        rows_after_upsert = int(conn.execute("SELECT COUNT(*) FROM market_daily_bars").fetchone()[0])
        dates = conn.execute(
            "SELECT MIN(CAST(epoch_ms(date_ms) AS DATE)), MAX(CAST(epoch_ms(date_ms) AS DATE)) "
            "FROM read_parquet(?)", [str(path)]
        ).fetchone()
        floor_rows = _retained_dates(conn, int(keep_days))
        floor = min(floor_rows) if floor_rows else None
        if floor is not None:
            conn.execute("DELETE FROM market_daily_bars WHERE trade_date < ?", [floor])
        after = int(conn.execute("SELECT COUNT(*) FROM market_daily_bars").fetchone()[0])
        retained_dates = _retained_dates(conn, int(keep_days))
        conn.execute(
            """
            INSERT INTO market_daily_ingests
              (id, source, source_path, imported_at, input_rows, min_trade_date, max_trade_date, retained_trade_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [uuid.uuid4().hex, source, str(path), now, input_rows, dates[0], dates[1], len(retained_dates)],
        )
        return {
            "ok": True,
            "status": "imported",
            "path": str(path),
            "input_rows": input_rows,
            "rows_before": before,
            "rows_after_upsert": rows_after_upsert,
            "rows": after,
            "written_net": after - before,
            "source_date_min": str(dates[0]) if dates[0] else None,
            "source_date_max": str(dates[1]) if dates[1] else None,
            "retained_trade_days": len(retained_dates),
            "retained_date_min": str(min(retained_dates)) if retained_dates else None,
            "retained_date_max": str(max(retained_dates)) if retained_dates else None,
            "pruned_rows": rows_after_upsert - after,
        }
    finally:
        conn.close()


def status(*, db_path: str | Path | None = None) -> dict[str, Any]:
    """只读查看底库覆盖和最近一次导入。"""
    conn = connect_readonly(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) FROM market_daily_bars"
        ).fetchone()
        latest = conn.execute(
            """SELECT source, source_path, CAST(imported_at AS VARCHAR), input_rows,
                      min_trade_date, max_trade_date, retained_trade_days
               FROM market_daily_ingests ORDER BY imported_at DESC, id DESC LIMIT 1"""
        ).fetchone()
        return {
            "db_path": str(resolve_db_path(db_path)),
            "rows": int(row[0]),
            "trade_days": int(row[1]),
            "date_min": str(row[2]) if row[2] else None,
            "date_max": str(row[3]) if row[3] else None,
            "latest_ingest": dict(zip(
                ["source", "source_path", "imported_at", "input_rows", "min_trade_date", "max_trade_date", "retained_trade_days"],
                latest,
            )) if latest else None,
        }
    finally:
        conn.close()


def recent_bars(code: str, *, limit: int = 20, db_path: str | Path | None = None,
                asof: str | None = None) -> list[dict[str, Any]]:
    """读取某票最近日线；仅数据查询，未接入任何交易判断。"""
    token = str(code or "").strip().split(".")[0]
    if len(token) != 6 or not token.isdigit():
        return []
    cutoff = datetime.strptime(asof, "%Y-%m-%d").date() if asof is not None else None
    conn = connect_readonly(db_path)
    try:
        cols = "code,thscode,trade_date,open,high,low,close,volume,amount,currency,source,CAST(ingested_at AS VARCHAR) AS ingested_at"
        clause = " AND trade_date <= ?" if cutoff is not None else ""
        params = [token, cutoff] if cutoff is not None else [token]
        result = conn.execute(
            f"SELECT {cols} FROM market_daily_bars WHERE code=?{clause} ORDER BY trade_date DESC LIMIT ?",
            params + [max(1, min(DEFAULT_KEEP_DAYS, int(limit)))],
        )
        names = [d[0] for d in result.description]
        return [dict(zip(names, r)) for r in result.fetchall()]
    finally:
        conn.close()
