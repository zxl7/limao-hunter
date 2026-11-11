#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影子排序（2026-11-11）契约测试。

锁定：
1. **提议顺序 = 板数 → 20日涨幅 → 5日涨幅 → 身位 → 代码**；
2. **缺涨幅不冒充**：`momentum_missing=true`，排序按 0（不奖不罚）；
3. **分歧可复现**：现行第一 ≠ 提议第一 时进入 `disagreements`。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("shadow_rank_diff", ROOT / "scripts" / "shadow_rank_diff.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["shadow_rank_diff"] = m
    spec.loader.exec_module(m)
    return m


def _write_candidates(tmp_path: Path, day: str, rows: list[dict]) -> Path:
    d = tmp_path / "candidates"
    d.mkdir(exist_ok=True)
    (d / f"candidates-{day}.json").write_text(
        json.dumps({"date": day, "relay": rows, "watch": []}, ensure_ascii=False),
        encoding="utf-8")
    return d


def test_proposed_order_prefers_momentum_within_same_boards(tmp_path: Path, monkeypatch):
    m = _mod()
    d = _write_candidates(tmp_path, "2026-11-11", [
        {"code": "000001", "name": "低涨幅", "boards": 2, "core_theme": "A",
         "relay_rank": 1, "ret_20d_pct": 10.0, "ret_5d_pct": 5.0},
        {"code": "000002", "name": "高涨幅", "boards": 2, "core_theme": "A",
         "relay_rank": 2, "ret_20d_pct": 80.0, "ret_5d_pct": 30.0},
    ])
    monkeypatch.setattr(m, "CAND_DIR", d)
    data = m.build("2026-11-11")
    assert data is not None
    top = [r for r in data["stocks"] if r["theme"] == "A"]
    top.sort(key=lambda r: r["rank_proposed"])
    assert top[0]["code"] == "000002"          # 同板数 → 20日涨幅高者在前
    assert data["disagreements"][0]["proposed_name"] == "高涨幅"


def test_missing_momentum_is_flagged(tmp_path: Path, monkeypatch):
    m = _mod()
    d = _write_candidates(tmp_path, "2026-11-11", [
        {"code": "000001", "name": "无涨幅", "boards": 1, "core_theme": "A", "relay_rank": 1},
    ])
    monkeypatch.setattr(m, "CAND_DIR", d)
    data = m.build("2026-11-11")
    assert data["coverage"]["momentum_missing"] == 1
    assert data["stocks"][0]["momentum_missing"] is True


def test_boards_dominate_momentum(tmp_path: Path, monkeypatch):
    """板数优先：1 板再高涨幅也不越过 2 板（与 11-11 回测结论一致）。"""
    m = _mod()
    d = _write_candidates(tmp_path, "2026-11-11", [
        {"code": "000001", "name": "1板高涨幅", "boards": 1, "core_theme": "A",
         "relay_rank": 1, "ret_20d_pct": 200.0, "ret_5d_pct": 50.0},
        {"code": "000002", "name": "2板", "boards": 2, "core_theme": "A",
         "relay_rank": 2, "ret_20d_pct": 5.0, "ret_5d_pct": 1.0},
    ])
    monkeypatch.setattr(m, "CAND_DIR", d)
    data = m.build("2026-11-11")
    top = sorted(data["stocks"], key=lambda r: r["rank_proposed"])
    assert top[0]["code"] == "000002"
