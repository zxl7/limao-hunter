#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生产只读服务的数据底座端点回归锁。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_serve():
    spec = importlib.util.spec_from_file_location("serve_data_gateway_test", ROOT / "serve.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def served(monkeypatch, tmp_path):
    from common import market_daily_store
    db = tmp_path / "daily.duckdb"
    conn = market_daily_store.connect(db)
    conn.execute("INSERT INTO market_daily_bars VALUES ('002724', '002724.SZ', '2026-10-22', 10, 11, 9, 10, 100, 1000, 'CNY', 'fixture', current_timestamp)")
    conn.close()
    monkeypatch.setenv("LIMAO_MARKET_DAILY_DB", str(db))
    module = _load_serve()
    from common import stock_registry

    monkeypatch.setattr(
        stock_registry,
        "get_by_code",
        lambda code: {
            "code": code,
            "name": "核验样本",
            "market": "SZ",
            "themes": ["题材样本"],
            "technical": {"bar_date": "2026-10-22", "status": "ok"},
            "ext": {"internal": "must-not-leak"},
            "db_path": "must-not-leak",
        },
    )
    monkeypatch.setattr(module, "write_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "SHARE_TOKEN", "")
    httpd = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        yield module, f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


def _get(base: str, path: str, headers: dict[str, str] | None = None) -> tuple[int, object]:
    request = Request(base + path, headers=headers or {})
    try:
        with urlopen(request, timeout=8) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_static_data_archives_are_not_mistaken_for_parameterized_queries(served):
    _, base = served
    for path in ("/api/data/exit-peaks", "/api/data/theme-snapshot", "/api/data/top5-history"):
        status, payload = _get(base, path)
        assert status == 200
        assert payload is not None


def test_data_gateway_query_boundary_and_hidden_database_path(served):
    _, base = served

    status, daily = _get(base, "/api/data/market-daily?code=002724&limit=20")
    assert status == 200
    assert isinstance(daily, dict)
    assert daily["code"] == "002724"
    assert 0 < len(daily["bars"]) <= 20
    assert set(daily["bars"][0]) == {
        "code", "thscode", "trade_date", "open", "high", "low", "close",
        "volume", "amount", "currency", "source", "ingested_at",
    }

    status, profile = _get(base, "/api/data/stock-registry?code=002724")
    assert status == 200, profile
    assert isinstance(profile, dict)
    assert profile["code"] == "002724"
    assert profile["found"] is True
    assert profile["stock"]["name"] == "核验样本"
    assert isinstance(profile["stock"], dict)
    assert "ext" not in profile["stock"]
    assert "db_path" not in profile["stock"]

    for path in (
        "/api/data/market-daily?code=abc",
        "/api/data/market-daily?code=002724&limit=21",
        "/api/data/stock-registry?code=002724&sql=select",
    ):
        status, rejected = _get(base, path)
        assert status == 400
        assert isinstance(rejected, dict)
        assert rejected["error"] == "invalid_request"

    status, hidden = _get(base, "/api/data/market_daily.duckdb")
    assert status == 404
    assert isinstance(hidden, dict)
    assert hidden["error"] == "not_found"


def test_data_gateway_respects_share_token(served, monkeypatch):
    module, base = served
    monkeypatch.setattr(module, "SHARE_TOKEN", "verify-token")

    status, denied = _get(base, "/api/data/stock-registry?code=002724")
    assert status == 403
    assert isinstance(denied, dict)
    assert denied["error"] == "forbidden"

    status, profile = _get(
        base,
        "/api/data/stock-registry?code=002724",
        {"Authorization": "Bearer verify-token"},
    )
    assert status == 200
    assert isinstance(profile, dict)
    assert profile["found"] is True


def test_stock_profile_supported_query_and_missing_capability(served, monkeypatch):
    from common import data_gateway

    _, base = served
    monkeypatch.setattr(data_gateway, "stock_profile", lambda code: {"code": code, "found": True}, raising=False)
    status, body = _get(base, "/api/data/stock-profile?code=002724")
    assert status == 200
    assert body == {"code": "002724", "found": True}
    status, body = _get(base, "/api/data/stock-profile?code=002724&sql=select")
    assert status == 400
    assert body["error"] == "invalid_request"
    monkeypatch.delattr(data_gateway, "stock_profile")
    status, body = _get(base, "/api/data/stock-profile?code=002724")
    assert status == 503
    assert body["error"] == "data_unavailable"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("selection", [{}, {"mode": "all", "limit": 1}])
def test_freshness_matches_returned_file_not_unrelated_entry(monkeypatch, tmp_path, nested, selection):
    module = _load_serve()
    monkeypatch.setattr(module, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(module.data_catalog, "meta_for", lambda route: {"stale_sec": 60})
    if nested:
        target = tmp_path / "2026-11-04" / "payload.json"
        target.parent.mkdir()
        defn = {"dir": ".", "dirPattern": r"^\d{4}-\d{2}-\d{2}$", "file": "payload.json"}
    else:
        target = tmp_path / "data_20261104.json"
        defn = {"dir": ".", "pattern": r"^data_\d{8}\.json$"}
    defn.update(selection)
    target.write_text('{"value": 1}', encoding="utf-8")
    old = int(time.time()) - 300
    os.utime(target, (old, old))
    (tmp_path / "zzz_unrelated.json").write_text("{}", encoding="utf-8")
    fresh = module.route_freshness("/fixture", defn)
    assert fresh["exists"] is True
    assert fresh["mtime"] == old
    assert fresh["age_sec"] >= 300
    assert fresh["stale"] is True
    payload = module.read_route(str(tmp_path), defn)
    if selection.get("mode") == "all":
        assert len(payload) == 1
        assert payload[0]["data"] == {"value": 1}
    else:
        assert payload == {"value": 1}


@pytest.mark.parametrize("defn", [
    {"dir": ".", "pattern": r"^data_\d{8}\.json$"},
    {"dir": ".", "dirPattern": r"^\d{4}-\d{2}-\d{2}$", "file": "missing.json"},
])
def test_freshness_missing_is_unknown(monkeypatch, tmp_path, defn):
    module = _load_serve()
    monkeypatch.setattr(module, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(module.data_catalog, "meta_for", lambda route: {"stale_sec": 60})
    (tmp_path / "2026-11-04").mkdir()
    (tmp_path / "zzz_unrelated.json").write_text("{}", encoding="utf-8")
    fresh = module.route_freshness("/fixture", defn)
    assert fresh["exists"] is False
    assert fresh["mtime"] is fresh["age_sec"] is fresh["stale"] is None
    headers = module.freshness_headers(fresh)
    assert headers["X-Data-Age"] == headers["X-Data-Stale"] == "unknown"
    assert headers["X-Data-Exists"] == "0"


def test_conditional_response_checks_staleness_and_preserves_shape(served, monkeypatch, tmp_path):
    module, base = served
    target = tmp_path / "payload.json"
    body = {"value": 1, "token": "must-not-leak", "_meta": {"business": True}}
    target.write_text(json.dumps(body), encoding="utf-8")
    old = int(time.time()) - 300
    os.utime(target, (old, old))
    monkeypatch.setattr(module, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(module, "PUBLIC_ROUTES", {"/api/fixture": {"file": "payload.json"}})
    monkeypatch.setattr(module.data_catalog, "meta_for", lambda route: {"stale_sec": 60})
    monkeypatch.setattr(module, "_STRICT_STALE", False)
    with urlopen(base + "/api/fixture", timeout=8) as response:
        etag = response.headers["ETag"]
        assert json.loads(response.read()) == {**body, "token": "***"}
        assert response.headers["X-Data-Stale"] == "1"
        assert response.headers["X-Data-Stale-Sec"] == "60"
        assert response.headers["X-Data-Mtime"] == str(float(old))
    request = Request(base + "/api/fixture", headers={"If-None-Match": etag})
    with pytest.raises(HTTPError) as error:
        urlopen(request, timeout=8)
    assert error.value.code == 304
    assert error.value.headers["X-Data-Stale"] == "1"
    assert error.value.headers["X-Data-Exists"] == "1"
    assert error.value.read() == b""
    monkeypatch.setattr(module, "_STRICT_STALE", True)
    status, rejected = _get(base, "/api/fixture", {"If-None-Match": etag})
    assert status == 404
    assert rejected["error"] == "data_stale"


def test_unknown_stale_threshold_does_not_claim_fresh(monkeypatch, tmp_path):
    module = _load_serve()
    monkeypatch.setattr(module, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(module.data_catalog, "meta_for", lambda route: {})
    (tmp_path / "payload.json").write_text("{}", encoding="utf-8")
    fresh = module.route_freshness("/fixture", {"file": "payload.json"})
    assert fresh["exists"] is True
    assert fresh["stale"] is None
    assert module.freshness_headers(fresh)["X-Data-Stale"] == "unknown"
