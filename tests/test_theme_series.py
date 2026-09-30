#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材序列数据层回归：不依赖已退役 HTML 展示器。"""
import json

from common import theme_series as ts
from scripts import realtime_data_pipeline as pipeline


def test_export_series_preserves_terminal_contract(tmp_path, monkeypatch):
    history = tmp_path / "zt_history"
    latest = tmp_path / "latest.json"
    out = tmp_path / "theme_series.json"
    history.mkdir()
    (history / "zt_2026-09-28.json").write_text(json.dumps({
        "date": "2026-09-28", "date_raw": "20260928", "zt_count": 2,
        "max_boards": 2,
        "zt_list": [
            {"name": "芯片甲", "code": "600001", "sector": "芯片", "boards": 2},
            {"name": "机器人乙", "code": "600002", "sector": "机器人", "boards": 1},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    latest.write_text(json.dumps({
        "ts": "2026-09-29T10:01:00+08:00", "total_zt": 3,
        "top5": [{"name": "半导体", "zt_count": 3}],
        "ladder": [{"boards": 3}], "leaders": [
            {"name": "芯片丙", "code": "600003", "sector": "芯片", "boards": 3},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ts, "ZT_HISTORY", history)
    monkeypatch.setattr(ts, "LATEST", latest)
    monkeypatch.setattr(ts, "SERIES_JSON", out)

    result = ts.export_series_json(max_days=5)

    assert result["dates"] == ["2026-09-28"]
    assert result["daily_totals"] == [2]
    assert result["themes"]["半导体/芯片"] == [1]
    assert result["theme_leaders"]["半导体/芯片"][0]["code"] == "600001"
    assert json.loads(out.read_text(encoding="utf-8"))["table_rows"] == result["table_rows"]


def test_data_ready_records_theme_series_step(tmp_path, monkeypatch):
    pulse = tmp_path / "pulse_snapshots"
    pulse.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    (pulse / "latest.json").write_text(json.dumps({"ts": "2026-09-29T10:01:00+08:00"}), encoding="utf-8")
    (cache / "feedback_matrix.json").write_text("{}", encoding="utf-8")
    (cache / "theme_rotation.json").write_text(json.dumps({"source_ts": "2026-09-29T10:01:00+08:00"}), encoding="utf-8")
    monkeypatch.setattr(pipeline, "PULSE", pulse)
    monkeypatch.setattr(pipeline, "CACHE", cache)
    monkeypatch.setattr(pipeline, "READY", pulse / "data_ready.json")
    monkeypatch.setattr(pipeline, "_run", lambda *_args: 0)
    monkeypatch.setattr(pipeline, "_publish_theme_series", lambda: 0)

    pipeline.main()

    ready = json.loads((pulse / "data_ready.json").read_text(encoding="utf-8"))
    assert ready["ready"] is True
    assert ready["steps"]["theme_series"] == 0
