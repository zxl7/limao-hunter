#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材归位体检（2026-11-11）契约测试。

1. `theme_source` 有值 → 以其为准；
2. 历史行无 source → 用「theme == sector」推断兜底（⛔ 不用 industry_sector，
   那个字段名在候选里常被写成题材，会把全部票误判成兜底）；
3. 漂移检测区分「真漂移（base 变）」与「同义换名（只有 theme 变）」。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("theme_coverage", ROOT / "scripts" / "theme_coverage.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["theme_coverage"] = m
    spec.loader.exec_module(m)
    return m


def test_pick_respects_source():
    m = _mod()
    assert m._pick({"core_theme": "机器人", "theme_source": "snapshot"})[:2] == ("机器人", "snapshot")
    assert m._pick({"core_theme": "焦炭Ⅱ", "theme_source": "sector"})[1] == "sector"


def test_pick_infers_by_sector_only():
    m = _mod()
    # theme 与 sector 相同 → 行业兜底
    assert m._pick({"theme": "焦炭Ⅱ", "sector": "焦炭Ⅱ"})[1] == "sector"
    # theme 与 sector 不同 → 真题材（哪怕 industry_sector 写成题材）
    assert m._pick({"theme": "机器人", "sector": "通用设备",
                    "industry_sector": "机器人"})[1] == "snapshot"
    assert m._pick({"theme": ""})[1] == "empty"


def test_flips_split_real_and_alias(tmp_path: Path, monkeypatch):
    m = _mod()
    d = tmp_path / "cand"
    d.mkdir()
    def w(day, rows):
        (d / f"candidates-{day}.json").write_text(json.dumps({"relay": rows}), encoding="utf-8")
    w("2026-11-09", [{"code": "1", "name": "甲", "core_theme": "创新药", "theme_base": "医药"}])
    # ⛔ 同一天只能写一次（第二次会覆盖第一次，丢行）
    w("2026-11-10", [
        {"code": "1", "name": "甲", "core_theme": "医药", "theme_base": "医药"},
        {"code": "2", "name": "乙", "core_theme": "塑料", "theme_base": "塑料"},
    ])
    w("2026-11-11", [{"code": "2", "name": "乙", "core_theme": "铜", "theme_base": "铜"}])
    monkeypatch.setattr(m, "CAND", d)
    f = m.flips()
    real = {x["code"] for x in f["real_drift"]}
    alias = {x["code"] for x in f["alias_ok"]}
    assert "2" in real          # base 变 → 真漂移
    assert "1" in alias         # 只是细名变 → 同义换名
