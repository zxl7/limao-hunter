#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材催化强度契约（2026-11-11）。

1. 非法/缺失字段 → 降级 unknown，⛔ 不抛错（09:25 不能被建议文件打断）；
2. 越界 catalyst → 丢弃并记错误；
3. 文件缺失 → 返回 None（= 没有 LLM 意见，⛔ 不等于「催化弱」）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core import ai_advice as AA  # noqa: E402


def test_validate_ok():
    d = AA.validate({"date": "2026-11-12", "themes": {
        "机器人": {"catalyst": 0.8, "basis": "宇树上市", "sources": ["财联社"]}}})
    assert d["valid"] is True and d["themes"]["机器人"]["level"] == "strong"


def test_level_inferred_from_score():
    d = AA.validate({"themes": {"A": {"catalyst": 0.4}, "B": {"catalyst": 0.1}}})
    assert d["themes"]["A"]["level"] == "normal"
    assert d["themes"]["B"]["level"] == "weak"


def test_out_of_range_dropped():
    d = AA.validate({"themes": {"A": {"catalyst": 1.8, "level": "strong"}}})
    assert d["themes"]["A"]["catalyst"] is None
    assert d["themes"]["A"]["level"] == "unknown"
    assert d["errors"]


def test_missing_theme_is_unknown():
    d = AA.validate({"themes": {"A": {}}})
    assert d["themes"]["A"]["level"] == "unknown"


def test_load_missing_returns_none(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(AA, "CACHE", tmp_path)
    assert AA.load("2026-11-12") is None


def test_load_reads_and_validates(tmp_path: Path, monkeypatch):
    (tmp_path / "ai_advice_2026-11-12.json").write_text(json.dumps(
        {"date": "2026-11-12", "themes": {"机器人": {"catalyst": 0.7}}}), encoding="utf-8")
    monkeypatch.setattr(AA, "CACHE", tmp_path)
    d = AA.load("2026-11-12")
    assert d and d["valid"] and AA.level_of(d, "机器人") == "strong"
