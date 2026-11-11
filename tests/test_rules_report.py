#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据日报 / 错杀清单（2026-11-11）契约测试。

锁定：
1. 归因**优先读字段**，字段缺失才回退文案（旧冻结件）；
2. **零命中只统计有归因方式的判据**（没有映射的不进待删候选）；
3. 错杀清单**没有次日行情就是 pending**，⛔ 不当 0、不挑最低价。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core import missed_list as ML  # noqa: E402
from wealth_code.core import rules_fired as RF  # noqa: E402


def _views(tmp_path: Path, views: dict) -> Path:
    d = tmp_path / "wc"
    d.mkdir(exist_ok=True)
    (d / "pool_call-2026-11-11.json").write_text(
        json.dumps({"trade_date": "2026-11-11", "views": views}, ensure_ascii=False),
        encoding="utf-8")
    return d


def test_attribute_prefers_field(tmp_path: Path):
    wc = _views(tmp_path, {"000001": {"view": "skip", "high_gap_reject": True}})
    data = RF.build("2026-11-11", wc_cache=wc)
    assert data is not None
    ids = [f["id"] for f in data["fired"]]
    assert "gate.gap_room" in ids


def test_attribute_falls_back_to_reason(tmp_path: Path):
    wc = _views(tmp_path, {"000001": {"view": "skip", "reason": "今天开在涨停附近，竞价就封死了 → 买不进"}})
    data = RF.build("2026-11-11", wc_cache=wc)
    ids = [f["id"] for f in data["fired"]]
    assert "gate.seal_auction" in ids


def test_zero_hit_only_mapped_rules(tmp_path: Path):
    wc = _views(tmp_path, {"000001": {"view": "skip", "high_gap_reject": True}})
    data = RF.build("2026-11-11", wc_cache=wc)
    assert "gate.backrow" in data["zero_hit"]          # 有映射、今天没命中
    assert all(z in RF.RULE_SIGNALS for z in data["zero_hit"])


def test_bought_not_attributed(tmp_path: Path):
    wc = _views(tmp_path, {"000001": {"view": "participate", "reason": "上方空间不够"}})
    data = RF.build("2026-11-11", wc_cache=wc)
    assert data["fired"] == [] and data["bought"] == 1


def test_missed_pending_without_next_bar(tmp_path: Path):
    wc = _views(tmp_path, {"000001": {"view": "skip", "name": "甲", "high_gap_reject": True}})
    kd = tmp_path / "kline"
    kd.mkdir()
    (kd / "000001.json").write_text(json.dumps(
        {"bars": [{"date": "2026-11-11", "close": 10.0}]}), encoding="utf-8")
    data = ML.build("2026-11-11", wc_cache=wc, kline_dir=kd)
    assert data["settled"] == 0
    assert data["rows"][0]["pending"] is True
    assert data["rows"][0]["next_ret_pct"] is None


def test_missed_settles_next_close(tmp_path: Path):
    wc = _views(tmp_path, {"000001": {"view": "skip", "name": "甲", "high_gap_reject": True}})
    kd = tmp_path / "kline"
    kd.mkdir()
    (kd / "000001.json").write_text(json.dumps({"bars": [
        {"date": "2026-11-11", "close": 10.0},
        {"date": "2026-11-12", "close": 11.0},
    ]}), encoding="utf-8")
    data = ML.build("2026-11-11", wc_cache=wc, kline_dir=kd)
    assert data["settled"] == 1
    assert data["rows"][0]["next_ret_pct"] == 10.0
    assert data["by_rule"][0]["avg_pct"] == 10.0
