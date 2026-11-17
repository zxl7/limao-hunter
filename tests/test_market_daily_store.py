#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场轻量日频底库回归锁：独立、滚动 20 交易日、绝不混入题材。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="缺 duckdb：全市场日频底库未安装")
import duckdb  # noqa: E402  （importorskip 之后再导入，缺库时整模块跳过而不是报错）

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import market_daily_store as store  # noqa: E402


def _make_parquet(path: Path, days: int = 22) -> Path:
    """DuckDB 造最小同花顺 daily-k 契约数据，不依赖 pyarrow/pandas。"""
    rows = []
    start = date(2026, 9, 1)
    for i in range(days):
        day = start + timedelta(days=i)
        # 同一日两只票，检查裁剪按交易日而不是按行数。
        for code, suffix in (("600000", "SH"), ("300001", "SZ")):
            px = 10 + i
            rows.append((f"{code}.{suffix}", "CNY", "1d", "none", day.isoformat(),
                         float(px), float(px + 1), float(px - 1), float(px + 0.5),
                         1000.0 + i, 10000.0 + i))
    conn = duckdb.connect()
    conn.execute("""CREATE TABLE src(
        thscode VARCHAR, currency VARCHAR, interval VARCHAR, adjusted VARCHAR, date_text VARCHAR,
        open_price DOUBLE, high_price DOUBLE, low_price DOUBLE, close_price DOUBLE,
        volume DOUBLE, turnover DOUBLE)""")
    conn.executemany("INSERT INTO src VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.execute(
        """COPY (
            SELECT thscode, currency, interval, adjusted,
                   epoch_ms(CAST(date_text AS DATE)::TIMESTAMP) AS date_ms,
                   open_price, high_price, low_price, close_price, volume, turnover
            FROM src
        ) TO ? (FORMAT PARQUET)""",
        [str(path)],
    )
    conn.close()
    return path


def test_import_prunes_by_trade_days_and_preserves_minimal_ohlcv(tmp_path):
    source = _make_parquet(tmp_path / "daily-k-10d.parquet", days=22)
    db = tmp_path / "market_daily.duckdb"

    out = store.import_hithink_parquet(source, keep_days=20, db_path=db)
    state = store.status(db_path=db)

    assert out["ok"] is True and out["retained_trade_days"] == 20
    assert state["trade_days"] == 20
    assert state["rows"] == 40, "20 个交易日 × 2 只股票，不能按行数误删"
    assert state["date_min"] == "2026-09-03"
    bars = store.recent_bars("600000", db_path=db)
    assert len(bars) == 20
    assert bars[0]["thscode"] == "600000.SH"
    assert bars[0]["open"] is not None and bars[0]["amount"] is not None
    assert "theme" not in bars[0] and "sector" not in bars[0], "题材/行业不得混入日线事实表"


def test_reimport_is_idempotent_for_overlapping_increment(tmp_path):
    source = _make_parquet(tmp_path / "daily-k-10d.parquet", days=12)
    db = tmp_path / "market_daily.duckdb"
    first = store.import_hithink_parquet(source, keep_days=20, db_path=db)
    second = store.import_hithink_parquet(source, keep_days=20, db_path=db)

    assert first["rows"] == 24
    assert second["rows"] == 24
    assert store.status(db_path=db)["trade_days"] == 12


def test_bad_parquet_contract_loudly_fails_without_creating_daily_rows(tmp_path):
    path = tmp_path / "bad.parquet"
    conn = duckdb.connect()
    conn.execute("CREATE TABLE bad(thscode VARCHAR, close_price DOUBLE)")
    conn.execute("COPY bad TO ? (FORMAT PARQUET)", [str(path)])
    conn.close()
    db = tmp_path / "market_daily.duckdb"

    with pytest.raises(ValueError, match="缺字段"):
        store.import_hithink_parquet(path, db_path=db)
    assert store.status(db_path=db)["rows"] == 0


def test_disabled_store_is_explicit_noop(tmp_path, monkeypatch):
    source = _make_parquet(tmp_path / "daily-k.parquet", days=2)
    monkeypatch.setenv("MARKET_DAILY_ENABLED", "0")
    out = store.import_hithink_parquet(source, db_path=tmp_path / "market_daily.duckdb")
    assert out == {"ok": True, "status": "disabled", "written": 0}
