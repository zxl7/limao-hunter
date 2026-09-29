# -*- coding: utf-8 -*-
"""事件级审计落盘（2026-09-17 A3）回归锁。

目标：`short_spirit` 除覆盖写快照外，另有 **append-only 逐事件**落盘
（`cache/short_spirit_events_YYYYMMDD.jsonl`），使「合并后不丢不重 / 送达状态」可核验。

⛔ 硬约束：落盘任何异常都**不得**影响主流程（盘中链路安全第一）。
隔离：全部落 tmp_path，不推送、不联网。
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit_ev", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def test_append_event_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    ss.append_event({"kind": "scan", "count": 2})
    p = ss._events_path()
    assert p.exists(), "应创建按日切分的事件文件"
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "scan" and rec["count"] == 2
    assert "at" in rec, "每条事件必须带时间（原缺陷：只有整批 ts）"


def test_append_event_is_append_only(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    ss.append_event({"kind": "scan", "count": 1})
    ss.append_event({"kind": "pushed", "count": 3})
    lines = ss._events_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "追加写不得覆盖历史（原缺陷：覆盖写）"
    assert [json.loads(x)["kind"] for x in lines] == ["scan", "pushed"]


def test_append_event_never_raises(tmp_path, monkeypatch):
    """落盘失败（目录不可写）必须被吞掉，不得打断盘中主流程。"""
    bad = tmp_path / "not_a_dir"
    bad.write_text("x", encoding="utf-8")   # 让 ROOT/cache 无法创建
    monkeypatch.setattr(ss, "ROOT", bad)
    ss.append_event({"kind": "scan"})       # 不应抛异常


def test_scan_event_written_by_main(tmp_path, monkeypatch):
    """走一次 main（--dry-run）应落一条 scan 事件，含逐事件 items。"""
    from datetime import datetime as _dt
    monkeypatch.setattr(ss, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir()
    snap = tmp_path / "latest.json"
    snap.write_text(json.dumps({"top5": [], "boards": {}}), encoding="utf-8")
    monkeypatch.setattr(ss, "SNAP", snap)
    monkeypatch.setattr(ss, "PUSHED_PATH", tmp_path / "pushed.json")
    monkeypatch.setattr(ss, "OUT_PATH", tmp_path / "out.json")
    monkeypatch.setattr(ss, "in_trading", lambda: True)
    monkeypatch.setattr(ss, "load_cfg", lambda: {"min_pct": 5.0, "min_market_cap_yi": 30,
        "amount_morning_yi": 3.0, "amount_afternoon_yi": 2.0, "batch_threshold": 999,
        "batch_timeout_min": 30, "max_signals": 5, "weight": {}})
    monkeypatch.setattr(ss, "build_signals", lambda d, n, c: (
        [{"name": "股1", "code": "600001", "sector": "S", "type": "涨停", "strength": 9.0}],
        "morning", 1, 1))
    # ⛔ 下跌支路（2026-09-21 新增）同样必须 mock —— 否则 main() 会去抓真实跌幅榜（触网）
    monkeypatch.setattr(ss, "build_signals_down", lambda d, n, c: ([], "morning", 0, 0))
    monkeypatch.setattr(sys, "argv", ["short_spirit.py", "--dry-run"])
    monkeypatch.setattr(os, "system", lambda *a, **k: 0)
    ss.main()
    recs = [json.loads(x) for x in ss._events_path().read_text(encoding="utf-8").strip().splitlines()]
    scans = [r for r in recs if r["kind"] == "scan"]
    assert scans, "main 应落 scan 事件"
    assert scans[0]["items"][0]["code"] == "600001", "应含逐事件明细"
    assert _dt.now().strftime("%Y%m%d") in ss._events_path().name
