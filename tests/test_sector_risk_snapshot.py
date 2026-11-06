# -*- coding: utf-8 -*-
"""板块子路回退、参考隔离与诊断契约。"""
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("ip_sector_risk", SCRIPTS / "intraday_pulse.py")
ip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ip)


def board(_host, _fs, po=0, fid="f3", **_kwargs):
    if fid == "f3":
        return [{"f14": "半导体", "f12": "002", "f3": -3.0, "f62": -1e8}]
    return [{"f14": "机器人" if po else "半导体", "f12": "001" if po else "002",
             "f3": 2.0 if po else -3.0, "f62": 1e8 if po else -1e8}]


@pytest.fixture
def setup(monkeypatch, tmp_path):
    clock = [datetime(2026, 11, 5, 10, 25, tzinfo=ip.BJ_TZ)]
    monkeypatch.setattr(ip, "now", lambda: clock[0])
    monkeypatch.setattr(ip, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ip, "fetch_sector_flow_details", lambda codes: {})
    monkeypatch.setattr(ip, "_em_board_get", board)
    monkeypatch.setenv("SECTOR_RISK_SPLIT_CACHE", "1")
    monkeypatch.setenv("SECTOR_RISK_REFERENCE_ENABLED", "1")
    return clock, tmp_path


@pytest.mark.parametrize("age,reference", [(59, False), (60, False), (600, False), (601, True), (3840, True)])
def test_reference_boundaries(setup, monkeypatch, age, reference):
    clock, path = setup
    good = ip.build_sector_risk()
    original = json.loads((path / "sector_risk_cache.json").read_text())
    clock[0] += timedelta(seconds=age)
    monkeypatch.setattr(ip, "_em_board_get", lambda *a, **kw: [])
    result = ip.build_sector_risk()
    assert result["asof"] == good["asof"]
    assert result["age_seconds"] == age
    assert result["reference_only"] is reference
    assert result["inflow_top"] == good["inflow_top"]
    assert result["ok"] is (age < 60)
    assert result["stale"] is (age >= 60)
    assert json.loads((path / "sector_risk_cache.json").read_text())["ts"] == original["ts"]


@pytest.mark.parametrize("failed", ["f3", "f62"])
def test_independent_sources(setup, monkeypatch, failed):
    clock, path = setup
    good = ip.build_sector_risk()
    pool = (path / "sector_fund_pool.json").read_text()
    clock[0] += timedelta(seconds=3840)
    monkeypatch.setattr(ip, "_em_board_get", lambda *a, **kw: [] if kw.get("fid", "f3") == failed else board(*a, **kw))
    result = ip.build_sector_risk()
    bad, valid = ("breadth", "flow") if failed == "f3" else ("flow", "breadth")
    assert result["sources"][bad]["reference_only"] is True
    assert result["sources"][valid]["ok"] is True
    assert result["sources"][valid]["age_seconds"] == 0
    assert result["sources"][bad]["asof"] == good["asof"]
    assert result["inflow_top"] and result["declining"]
    if failed == "f62":
        assert (path / "sector_fund_pool.json").read_text() == pool
    else:
        assert json.loads((path / "sector_fund_pool.json").read_text())["ts"] == clock[0].timestamp()


def test_cross_day_and_future_rejected(setup, monkeypatch):
    clock, _ = setup
    ip.build_sector_risk()
    monkeypatch.setattr(ip, "_em_board_get", lambda *a, **kw: [])
    clock[0] += timedelta(days=1)
    result = ip.build_sector_risk()
    assert result["inflow_top"] == [] and result["asof"] is None
    clock[0] -= timedelta(days=1, seconds=1)
    assert ip.build_sector_risk()["inflow_top"] == []


def test_disable_reference_and_split(setup, monkeypatch):
    clock, _ = setup
    ip.build_sector_risk()
    clock[0] += timedelta(seconds=601)
    monkeypatch.setattr(ip, "_em_board_get", lambda *a, **kw: [])
    monkeypatch.setenv("SECTOR_RISK_REFERENCE_ENABLED", "0")
    assert ip.build_sector_risk()["inflow_top"] == []
    monkeypatch.setenv("SECTOR_RISK_SPLIT_CACHE", "0")
    assert ip.build_sector_risk()["inflow_top"] == []


def test_failed_attempt_throttled(setup, monkeypatch):
    clock, _ = setup
    ip.build_sector_risk()
    clock[0] += timedelta(seconds=601)
    calls = []
    monkeypatch.setattr(ip, "_em_board_get", lambda *a, **kw: calls.append(1) or [])
    ip.build_sector_risk()
    count = len(calls)
    clock[0] += timedelta(seconds=10)
    assert ip.build_sector_risk()["age_seconds"] == 611
    assert len(calls) == count


@pytest.mark.parametrize("payload,reason", [({}, "empty"), ({"data": {"diff": [{"f14": "测试", "f62": "-"}]}}, "fields")])
def test_diagnostics_empty_fields(monkeypatch, caplog, payload, reason):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(payload).encode()
    monkeypatch.setenv("SECTOR_RISK_DIAGNOSTICS", "1")
    monkeypatch.setattr(ip.urllib.request, "urlopen", lambda *a, **kw: Response())
    assert ip._em_board_get(ip.EM_BOARD_HOSTS[0], "m:90+t:2", fid="f62", tries=1) == []
    assert f"reason={reason}" in caplog.text
    assert "attempt=1" in caplog.text and "category=m:90+t:2" in caplog.text


@pytest.mark.parametrize("fid", ["f3", "f62"])
@pytest.mark.parametrize("invalid", [None, {}, {"f14": "停牌", "f3": "-", "f62": "-"},
                                     {"f14": "缺字段"}, {"f14": "非有限值", "f3": float("nan"), "f62": float("inf")}])
def test_board_keeps_99_valid_rows(monkeypatch, caplog, fid, invalid):
    valid = [{"f14": f"板块{i}", "f3": -1.0, "f62": 1e8} for i in range(99)]
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"data": {"diff": valid + [invalid]}}).encode()
    calls = []
    monkeypatch.setenv("SECTOR_RISK_DIAGNOSTICS", "1")
    monkeypatch.setattr(ip.urllib.request, "urlopen", lambda *a, **kw: calls.append(1) or Response())
    assert ip._em_board_get(ip.EM_BOARD_HOSTS[0], "m:90+t:2", fid=fid, tries=3) == valid
    assert len(calls) == 1
    assert "reason=filtered_fields" in caplog.text
    assert "rows=99 dropped=1" in caplog.text


def test_diagnostics_timeout_no_secret(monkeypatch, caplog):
    def fail(*a, **kw): raise TimeoutError("secret-token")
    monkeypatch.setenv("SECTOR_RISK_DIAGNOSTICS", "1")
    monkeypatch.setattr(ip.urllib.request, "urlopen", fail)
    assert ip._em_board_get(ip.EM_BOARD_HOSTS[0], "m:90+t:3", tries=1) == []
    assert "reason=timeout" in caplog.text
    assert "secret-token" not in caplog.text


def test_pool_uses_atomic_writer(setup, monkeypatch):
    writes = []
    save = ip._save_json
    monkeypatch.setattr(ip, "_save_json", lambda path, data: (writes.append(path.name), save(path, data))[-1])
    ip.build_sector_risk()
    assert "sector_fund_pool.json" in writes


def test_partial_without_cache_preserves_valid_flow(setup, monkeypatch):
    monkeypatch.setattr(ip, "_em_board_get", lambda *a, **kw: [] if kw.get("fid", "f3") == "f3" else board(*a, **kw))
    result = ip.build_sector_risk()
    assert result["inflow_top"] and result["outflow_top"]
    assert result["sources"]["flow"]["ok"] is True
    assert result["sources"]["breadth"]["status"] == "unavailable"
    assert result["status"] == "partial" and result["complete"] is False


def test_legacy_cache_migration(setup, monkeypatch):
    clock, path = setup
    good = ip.build_sector_risk()
    cache = path / "sector_risk_cache.json"
    cache.write_text(json.dumps({"ts": clock[0].timestamp(), "data": good}))
    clock[0] += timedelta(seconds=3840)
    monkeypatch.setattr(ip, "_em_board_get", lambda *a, **kw: [])
    result = ip.build_sector_risk()
    assert result["reference_only"] is True
    assert result["asof"] == good["asof"] and result["age_seconds"] == 3840


def test_decision_filter_preserves_display():
    from common.em_flow import sector_risk_for_decision
    raw = {"reference_only": True, "inflow_top": [{"name": "机器人"}], "outflow_top": [{"name": "半导体"}], "declining": [{"name": "半导体"}]}
    assert sector_risk_for_decision(raw)["inflow_top"] == []
    assert sector_risk_for_decision(raw)["declining"] == []
    assert raw["inflow_top"]


def test_theme_context_and_capital_guard(monkeypatch):
    import theme_rotation as tr
    raw = {"status": "reference", "asof": "2026-11-05T10:25:00+08:00", "error": "接口失败",
           "reference_only": True, "outflow_top": [{"name": "半导体", "flow_yi": -2}]}
    monkeypatch.setattr(tr, "_load", lambda p, *a, **kw: {"sector_risk": raw} if p.name == "latest.json" else {})
    context = tr._market_context()
    for key in ("status", "asof", "error", "reference_only"):
        assert context[key] == raw[key]
    assert context["outflow_top"]
    assert tr._capital_state("半导体", context) == ("neutral", 0.0)


def test_reference_blocked_in_signal_and_reflow(tmp_path, monkeypatch):
    import live_signal as ls
    import pulse_aggregator as pa
    import short_spirit as ss
    from wealth_code.core.reflow_scan import _strong_sectors
    from wealth_code.pipelines.build_cards import _live_confirm
    from wealth_code.core.relay_score import _load_sector_flow_map
    raw = {"reference_only": True, "inflow_top": [{"name": "测试资金板块", "flow_yi": 20}],
           "outflow_top": []}
    live = {"sector_risk": raw}
    assert "测试资金板块" not in ss.hot_keywords(live)
    assert pa._sector_flow("测试资金板块", live) is None
    monkeypatch.setattr(ls, "_load_fund_pool", lambda: {})
    assert "20" not in ls._sector_money_txt(live, "测试资金板块")
    assert _live_confirm("测试资金板块", live) == _live_confirm("测试资金板块", {})
    snap = tmp_path / "pulse_snapshots"
    snap.mkdir()
    (snap / "latest.json").write_text(json.dumps(live))
    assert "测试资金板块" not in _strong_sectors(tmp_path)
    (tmp_path / "theme_rotation.json").write_text(json.dumps({"market_context": raw}))
    assert _load_sector_flow_map(tmp_path) == {}
