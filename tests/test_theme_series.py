#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材序列数据层回归：不依赖已退役 HTML 展示器。"""
import json
from datetime import datetime

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
    # ⛔ same_source 现为日期级校验：必须带 date==今日 才视为同步（不再比 source_ts 精确相等）
    (cache / "theme_rotation.json").write_text(json.dumps(
        {"date": datetime.now().strftime("%Y-%m-%d"), "source_ts": "2026-09-29T10:01:00+08:00"},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pipeline, "PULSE", pulse)
    monkeypatch.setattr(pipeline, "CACHE", cache)
    monkeypatch.setattr(pipeline, "READY", pulse / "data_ready.json")
    monkeypatch.setattr(pipeline, "_run", lambda *_args: 0)
    monkeypatch.setattr(pipeline, "_publish_theme_series", lambda: 0)

    pipeline.main()

    ready = json.loads((pulse / "data_ready.json").read_text(encoding="utf-8"))
    assert ready["ready"] is True
    assert ready["steps"]["theme_series"] == 0
    assert ready["steps"]["intraday_exit_peaks"] == 0
    # 峰值采集是卖出侧未来证据的旁路：即便异常也不能阻断现有数据就绪和推送链。
    results = dict(ready["steps"])
    results["intraday_exit_peaks"] = 1
    assert pipeline._compute_ready(
        results=results, feedback_error=None, rotation_date=datetime.now().strftime("%Y-%m-%d")
    ) == (True, "")


def test_compute_ready_date_level_and_pre_auction_benign():
    """2026-10-08 修复回归：same_source 按日期级判定；pre_auction 属良性盘前标记。"""
    today = datetime.now().strftime("%Y-%m-%d")
    ok = {k: 0 for k in ("intraday_pulse", "feedback_matrix", "realtime_analytics", "theme_series")}

    # 全绿：今日快照 + 无错误 → 就绪
    assert pipeline._compute_ready(results=ok, feedback_error=None, rotation_date=today) == (True, "")

    # 盘前 pre_auction 标记：良性，不应阻断
    assert pipeline._compute_ready(results=ok, feedback_error="pre_auction", rotation_date=today) == (True, "")

    # 题材引擎今日快照缺失（date 非今日）：判未就绪，且原因指明
    ready, reason = pipeline._compute_ready(results=ok, feedback_error=None, rotation_date="2026-09-28")
    assert ready is False
    assert "今日快照缺失" in reason

    # 真实 feedback 错误（非 pre_auction）：判未就绪
    ready, reason = pipeline._compute_ready(results=ok, feedback_error="timeout", rotation_date=today)
    assert ready is False
    assert "反馈矩阵报错" in reason

    # 关键计算步骤失败：判未就绪
    bad = dict(ok); bad["intraday_pulse"] = 1
    ready, reason = pipeline._compute_ready(results=bad, feedback_error=None, rotation_date=today)
    assert ready is False
    assert "步骤未全部完成" in reason


def test_compute_ready_blocks_inconsistent_zt_counts():
    today = datetime.now().strftime("%Y-%m-%d")
    ok = {k: 0 for k in ("intraday_pulse", "feedback_matrix", "realtime_analytics", "theme_series")}
    ready, reason = pipeline._compute_ready(
        results=ok, feedback_error=None, rotation_date=today,
        consistency_error="涨停计数不一致 feedback=59, codes=61, zt_history=61",
    )
    assert ready is False
    assert "口径不一致" in reason


def test_compute_ready_old_exact_source_ts_no_longer_blocks():
    """旧逻辑用 source_ts 精确相等判 same_source，会因竞态恒 False；新逻辑不依赖它。

    即便 rotation 带一个与 latest 不同的 source_ts，只要 date==今日 即视为同步。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    ok = {k: 0 for k in ("intraday_pulse", "feedback_matrix", "realtime_analytics", "theme_series")}
    ready, _ = pipeline._compute_ready(
        results=ok, feedback_error=None, rotation_date=today)
    assert ready is True

