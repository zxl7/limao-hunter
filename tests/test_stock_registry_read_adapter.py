#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""底库核心读取适配层回归锁。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for path in (str(ROOT), str(ROOT / "wealth_code")):
    if path not in sys.path:
        sys.path.insert(0, path)

from common import stock_registry as sr  # noqa: E402
from adapters import input_zt  # noqa: E402
from adapters import stock_registry_reader as reader  # noqa: E402
from core import defense_gate as dg  # noqa: E402
from pipelines import pipeline_verify as pv  # noqa: E402


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "registry.db"
    sr.init_db(path)
    return path


def test_registry_series_is_primary_shape(db, monkeypatch):
    monkeypatch.setenv("STOCK_REGISTRY_READ_MODE", "registry")
    sr.upsert_one(
        {"code": "000001", "name": "甲", "boards": 2, "limit_up_close": 1},
        trade_date="2026-10-13", asof_ts="2026-10-13T15:00:00+08:00",
        source="eod:test", db_path=db,
    )
    sr.upsert_one(
        {"code": "000001", "name": "甲", "boards": 3, "limit_up_close": 1},
        trade_date="2026-10-14", asof_ts="2026-10-14T15:00:00+08:00",
        source="eod:test", db_path=db,
    )
    result = reader.load_zt_series_from_registry(days=20, upto="2026-10-14", db_path=db)
    assert result == ({"000001": [("2026-10-13", 2), ("2026-10-14", 3)]},
                      ["2026-10-13", "2026-10-14"])


def test_defense_gate_explicit_root_keeps_fixture_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REGISTRY_READ_MODE", "registry")
    (tmp_path / "zt_2026-10-13.json").write_text(
        json.dumps({"zt_list": [{"code": "000001", "boards": 2}]}), encoding="utf-8"
    )
    series, days = dg.load_zt_series(root=tmp_path, upto="2026-10-13")
    assert series == {"000001": [("2026-10-13", 2)]}
    assert days == ["2026-10-13"]


def test_input_zt_registry_primary_preserves_snapshot_supplements(db, monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REGISTRY_READ_MODE", "registry")
    monkeypatch.setenv("LIMAO_REGISTRY_DB", str(db))
    monkeypatch.setenv("WEALTH_USE_STOCK_REGISTRY", "1")
    raw = {
        "date": "2026-10-14",
        "zt_list": [{
            "code": "000001", "name": "甲", "pct": 10.0, "boards": 2,
            "sector": "传媒", "fbt": "093000", "zbc": 1, "cje_yi": 8.2,
        }],
    }
    (tmp_path / "yesterday_zt.json").write_text(json.dumps(raw), encoding="utf-8")
    sr.upsert_one(
        {"code": "000001", "name": "甲", "boards": 2, "pct": 10.0,
         "price": 12.3, "limit_up_close": 1, "industry": "出版",
         "themes": ["传媒"]},
        trade_date="2026-10-14", asof_ts="2026-10-14T15:00:00+08:00",
        source="eod:test", db_path=db,
    )
    result = input_zt._load_xuangubao(tmp_path)
    assert result["registry_primary"] is True
    assert result["ztgc"][0]["dm"] == "000001"
    assert result["ztgc"][0]["hy"] == "出版"
    assert result["ztgc"][0]["fbt"] == "093000"
    assert result["ztgc"][0]["zbc"] == 1


def test_verify_full_pool_prefers_registry_and_supplements_legacy(db, monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_REGISTRY_READ_MODE", "registry")
    monkeypatch.setenv("LIMAO_REGISTRY_DB", str(db))
    legacy = {
        "date": "2026-10-14",
        "zt_list": [{"code": "000001", "name": "旧名", "boards": 9,
                      "cje_yi": 8.8, "fbt": "093000"}],
    }
    hist = tmp_path / "cache" / "zt_history"
    hist.mkdir(parents=True)
    (hist / "zt_2026-10-14.json").write_text(json.dumps(legacy), encoding="utf-8")
    sr.upsert_one(
        {"code": "000001", "name": "底库名", "boards": 2, "pct": 10.01,
         "price": 12.3, "limit_up_close": 1},
        trade_date="2026-10-14", asof_ts="2026-10-14T15:00:00+08:00",
        source="eod:test", db_path=db,
    )
    monkeypatch.setattr(pv, "ROOT", tmp_path / "wealth_code")
    rows = pv._load_full_zt_rows("2026-10-14")
    assert rows == [{"code": "000001", "name": "底库名", "boards": 2,
                     "pct": 10.01, "price": 12.3, "cje_yi": 8.8,
                     "fbt": "093000"}]


def test_read_mode_legacy_does_not_query_registry(monkeypatch):
    monkeypatch.setenv("STOCK_REGISTRY_READ_MODE", "legacy")
    assert reader.get_stock("000001") is None
    assert reader.load_zt_series_from_registry(days=20) == ({}, [])
