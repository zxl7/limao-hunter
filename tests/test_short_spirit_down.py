# -*- coding: utf-8 -*-
"""短线精灵下跌支路回归：过滤、分类和跌停当下性均不依赖实时接口。"""
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit_down", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


CFG = {
    "min_pct": 3.0, "min_pct_down": 7.5, "min_market_cap_yi": 100,
    "amount_morning_yi": 5, "amount_afternoon_yi": 10, "max_new_stock_days": 365,
    "top_per_sector": 2, "max_signals": 10,
    "weight": {"pct": 0.4, "amount": 0.2, "resonance": 0.3, "flow": 0.1},
}


def _stock(code="600001", name="测试股", pct=-8.0, price=9.2, prev_close=10.0,
           cap=120.0, amount=12.0, industry="消费"):
    return {"code": code, "name": name, "pct": pct, "price": price,
            "prev_close": prev_close, "market_cap_yi": cap, "amount_yi": amount,
            "float_cap_yi": cap, "list_date": "20200101", "main_flow_yi": -1.0,
            "industry": industry}


def test_signal_type_down_distinguishes_limit_open_and_fast_drop():
    assert ss.signal_type_down(_stock(pct=-10.0, price=9.0))[0] == "跌停"
    assert ss.signal_type_down(_stock(pct=-10.0, price=9.05))[0] == "打开跌停"
    assert ss.signal_type_down(_stock(pct=-7.5, price=9.25))[0] == "快速下跌"


def test_build_signals_down_filters_and_sorts(tmp_path, monkeypatch):
    pool = [
        _stock("600001", pct=-8.0, industry="A"),
        _stock("600002", pct=-9.0, industry="A"),
        _stock("688001", pct=-18.0),
        _stock("600003", name="*ST 测试", pct=-9.0),
        _stock("600004", cap=99.0, pct=-9.0),
        _stock("600005", amount=4.0, pct=-9.0),
        _stock("600006", pct=-9.0, industry="B"),
    ]
    monkeypatch.setattr(ss, "fetch_pool_down", lambda cfg: pool)
    monkeypatch.setattr(ss, "session", lambda: "morning")
    monkeypatch.setattr(ss, "ROUND_DOWN_PATH", tmp_path / "round.json")
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_LOWCAP", False)
    cfg = {**CFG, "top_per_sector": 1}
    signals, _, kept, total = ss.build_signals_down({"top5": [], "leaders": []}, datetime(2026, 9, 25, 10, 0), cfg)
    assert total == len(pool) and kept == 3
    assert [x["code"] for x in signals] == ["600002", "600006"], "同板块仅保留跌幅最深的两只且按跌幅排序"


def test_limit_down_timeliness_allows_reopen_then_new_limit(tmp_path, monkeypatch):
    rp = tmp_path / "round.json"
    monkeypatch.setattr(ss, "ROUND_DOWN_PATH", rp)
    now = datetime(2026, 9, 25, 10, 0)
    limit = [{"code": "600001", "type": "跌停"}]
    assert ss._timeliness_gate_down(limit, [], now) == limit
    assert ss._timeliness_gate_down(limit, [], now) == []
    ss._timeliness_gate_down([], [], now)  # 本轮打开，观测快照移除该股
    assert ss._timeliness_gate_down(limit, [], now) == limit
    assert json.loads(rp.read_text(encoding="utf-8"))["dt"] == ["600001"]


def test_fetch_pool_marks_partial_page_failure(monkeypatch):
    """第 2 页异常时仍保留第 1 页，同时输出完整性状态供前端与审计识别。"""
    calls = {"n": 0}

    def fake_get(url):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("page 2 unavailable")
        return {"data": {"diff": [{"f12": "600001", "f14": "测试股", "f3": 8,
                                  "f2": 10, "f6": 1000000000, "f20": 12000000000,
                                  "f21": 10000000000, "f26": "20200101", "f18": 9,
                                  "f62": 100000000, "f100": "测试"}]}}

    monkeypatch.setattr(ss, "http_get", fake_get)
    pool = ss.fetch_pool({})
    assert len(pool) == 1
    assert ss.FETCH_STATUS["up"]["complete"] is False
    assert ss.FETCH_STATUS["up"]["failed_pages"] == [2]


def test_main_skips_when_another_process_holds_lock(tmp_path, monkeypatch):
    """守护多分支并发时，后到实例不进入读改写与推送流程。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    lock_path = cache / "short_spirit.lock"
    holder = subprocess.Popen([sys.executable, "-c", (
        "import fcntl, pathlib, sys, time; p=pathlib.Path(sys.argv[1]); "
        "f=p.open('a+'); fcntl.flock(f.fileno(), fcntl.LOCK_EX); "
        "print('locked', flush=True); time.sleep(10)"), str(lock_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(ss, "ROOT", tmp_path)
        monkeypatch.setattr(sys, "argv", ["short_spirit.py", "--send", "unused.json"])
        assert ss.main() == 0
    finally:
        holder.terminate()
        holder.wait(timeout=3)
