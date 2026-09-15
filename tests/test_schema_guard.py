#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据契约守卫态 + 质量门回归（2026-09-02 · 中枢体检 P0-2）。

背景：早盘 09:26 前 feedback_matrix 写 pre_auction 守卫标记（error+msg），
schema 契约却要求完整字段（up_rate 等）→ 每日早盘质量门必红。
修复原则：守卫态文件由 AI 任务读 error 跳块，校验器只查占位结构、不再误报缺字段；
但「非守卫态的真实文件缺字段」依然必须 FAIL（防数据血缘断链）。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import validate_schema as vs  # noqa: E402


@pytest.fixture
def tmpfile():
    import tempfile, shutil
    d = pathlib.Path(tempfile.mkdtemp(dir=str(ROOT / ".pytest_tmp")))
    yield d / "feedback_matrix.json"   # 需命中契约文件名，x.json 不在契约内会保守通过
    shutil.rmtree(d, ignore_errors=True)


def test_guard_state_passes(tmpfile):
    """pre_auction / non_trading_day 等守卫态占位 → 契约通过。"""
    tmpfile.write_text(json.dumps(
        {"generated_ts": "2026-09-03 08:58:00", "error": "pre_auction",
         "msg": "9:26 竞价定价后首轮生效"}), encoding="utf-8")
    ok, errs = vs.validate_one(tmpfile)
    assert ok, errs


def test_guard_state_without_generated_ts_fails(tmpfile):
    tmpfile.write_text(json.dumps({"error": "pre_auction"}), encoding="utf-8")
    ok, errs = vs.validate_one(tmpfile)
    assert not ok
    assert any("generated_ts" in e for e in errs)


def test_real_file_missing_field_still_fails(tmpfile):
    """真实（非守卫态）文件缺 up_rate → 必须 FAIL（契约不能放松）。"""
    tmpfile.write_text(json.dumps(
        {"generated_ts": "2026-09-03 09:30:00", "prev_date": "2026-09-02",
         "prev_zt_count": 51, "promote_rate": 0.3, "nuke_count": 2,
         "broken_rate": 0.2}), encoding="utf-8")  # 缺 up_rate
    ok, errs = vs.validate_one(tmpfile)
    assert not ok
    assert any("up_rate" in e for e in errs)


def test_valid_full_file_passes(tmpfile):
    tmpfile.write_text(json.dumps(
        {"generated_ts": "2026-09-03 09:30:00", "prev_date": "2026-09-02",
         "prev_zt_count": 51, "promote_rate": 0.3, "nuke_count": 2,
         "broken_rate": 0.2, "up_rate": 55.0}), encoding="utf-8")
    ok, errs = vs.validate_one(tmpfile)
    assert ok, errs
