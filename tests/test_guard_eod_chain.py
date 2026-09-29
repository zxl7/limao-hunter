#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘后链落盘守护守卫（2026-09-22 立）

锁住「盘中口径 ≠ 盘后链跑过」这条判据 —— 它是本守护存在的理由：
原实现只查 `exists()` / `date` 字段，而 eruption / sector_flow / emotion / theme_rotation /
carrier_state **盘中也会被写**（盘中回流补跑），于是**盘后链整条未执行**时守护静默放行。
实测事故：2026-09-22 五处产物 mtime 全在 10:41~11:12，守护 17:50 零告警。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import guard_eod_chain as g          # noqa: E402


def _touch(p: Path, when: datetime) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}", encoding="utf-8")
    ts = when.timestamp()
    os.utime(p, (ts, ts))


TODAY = "2026-09-22"
DAY = datetime(2026, 9, 22, 11, 12)          # 盘中
EOD = datetime(2026, 9, 22, 17, 25)          # 盘后链
PREV = datetime(2026, 9, 21, 17, 25)         # 前一日盘后


# ────────────────── 1. _cover_state 判据 ──────────────────
def test_cover_state_missing(tmp_path: Path) -> None:
    ok, why = g._cover_state(tmp_path / "nope.json", TODAY)
    assert ok is False
    assert "不存在" in why


def test_cover_state_intraday_is_not_covered(tmp_path: Path) -> None:
    """⛔ 盘中写入必须判为「未覆盖」——这是本次修的核心。"""
    p = tmp_path / "x.json"
    _touch(p, DAY)
    ok, why = g._cover_state(p, TODAY)
    assert ok is False
    assert "盘中口径" in why
    assert "11:12" in why


def test_cover_state_eod_is_covered(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    _touch(p, EOD)
    ok, why = g._cover_state(p, TODAY)
    assert ok is True
    assert why == "17:25"


def test_cover_state_previous_day_is_not_covered(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    _touch(p, PREV)
    ok, why = g._cover_state(p, TODAY)
    assert ok is False
    assert "非当日" in why


def test_cover_state_boundary_at_threshold(tmp_path: Path) -> None:
    """恰在 16:00 整 → 视为已覆盖（≥ 判据，不是 >）。"""
    p = tmp_path / "x.json"
    _touch(p, datetime(2026, 9, 22, 16, 0))
    ok, _ = g._cover_state(p, TODAY)
    assert ok is True
    p2 = tmp_path / "y.json"
    _touch(p2, datetime(2026, 9, 22, 15, 59))
    ok2, why2 = g._cover_state(p2, TODAY)
    assert ok2 is False and "盘中口径" in why2


# ────────────────── 2. _missing_of 组合判据 ──────────────────
def _seed(cache: Path, *, at: datetime, with_latest: bool) -> None:
    _touch(cache / "eruption_history" / f"eruption_{TODAY}.json", at)
    _touch(cache / "sector_flow_history" / f"sector_flow_{TODAY}.json", at)
    _touch(cache / "short_term" / "daily" / f"emotion_{TODAY}.json", at)
    if with_latest:
        p = cache / "theme_rotation.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"date": TODAY}), encoding="utf-8")
        ts = at.timestamp()
        os.utime(p, (ts, ts))
        _touch(cache / "carrier_state.json", at)


def test_missing_of_all_green_when_eod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=EOD, with_latest=True)
    assert g._missing_of(TODAY, live=True) == []


def test_missing_of_flags_intraday_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """事故复现：文件全在但都是盘中写的 → 必须 5 项全报。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=DAY, with_latest=True)
    miss = g._missing_of(TODAY, live=True)
    names = {m[0] for m in miss}
    assert len(miss) == 5, f"应报 5 项盘中口径，实得 {miss}"
    assert names == {"题材爆发快照", "板块资金流", "情绪日快照", "题材回流引擎", "载体证伪"}
    assert all("盘中口径" in m[1] for m in miss)


def test_missing_of_live_false_skips_latest_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """历史查询不做「只保留最新快照」的两项的判定（否则必然误报）。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=EOD, with_latest=False)          # theme_rotation/carrier 缺失
    assert g._missing_of(TODAY, live=False) == []
    assert {m[0] for m in g._missing_of(TODAY, live=True)} == {"题材回流引擎", "载体证伪"}


def test_missing_of_flags_absent_dated_products(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """按日期命名的三件套完全缺失（09-21 事故形态）→ 历史查询也能查出。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    miss = g._missing_of("2026-09-21", live=False)
    assert len(miss) == 3
    assert all("不存在" in m[1] for m in miss)


def test_theme_rotation_date_mismatch_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """date 字段是昨日 → 报到「读取失败/日期不符」，且不因 mtime 正常而放过。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=EOD, with_latest=False)
    p = tmp_path / "theme_rotation.json"
    p.write_text(json.dumps({"date": "2026-09-21"}), encoding="utf-8")
    ts = EOD.timestamp()
    os.utime(p, (ts, ts))
    _touch(tmp_path / "carrier_state.json", EOD)
    miss = g._missing_of(TODAY, live=True)
    assert len(miss) == 1 and miss[0][0] == "题材回流引擎"
    assert "2026-09-21" in miss[0][1]


# ────────────────── 3. 推卡走测试群（内部验证卡铁律） ──────────────────
def test_alert_goes_to_test_webhook() -> None:
    src = (BASE / "scripts" / "guard_eod_chain.py").read_text(encoding="utf-8")
    assert "feishu_webhook_test" in src
    assert "feishu_webhook_prod" not in src
