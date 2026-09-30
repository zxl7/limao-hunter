#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中分析链的**时段守卫**（2026-09-29 立）。

锁住的事故：`realtime_analytics.py` 每轮无条件覆盖 `cache/theme_rotation.json`，
而收盘后 `pulse_snapshots/latest.json` 的 `ts` 仍是**当日 15:00:08** ⇒ 原守卫
（只比「快照日期 == 今日」）放行，于是 09-29 在 **17:41 / 17:51** 又写了两次
实时版本（`realtime=true`），把盘后链的产物盖成**盘中态**。

⇒ 时段守卫：只在**本交易日 09:25 ~ 16:00** 写盘；16:00 与
`guard_eod_chain.EOD_COVER_HHMM` 对齐，此后产出一律归盘后链。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import realtime_analytics as ra          # noqa: E402


class _FrozenDT(datetime):
    """冻结 `datetime.now()`，用于驱动时段守卫。"""

    frozen: datetime = datetime(2026, 9, 29, 10, 0)

    @classmethod
    def now(cls, tz=None):               # noqa: D401
        return cls.frozen


def _seed(tmp_path: Path) -> None:
    """当日 15:00 快照（收盘口径）——正是收盘后仍在场的「旧盘面」。"""
    (tmp_path / "latest.json").write_text(
        json.dumps({"ts": "2026-09-29T15:00:08.559819+08:00"}), encoding="utf-8"
    )


def _patch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, when: datetime) -> None:
    monkeypatch.setattr(ra, "PULSE", tmp_path)
    monkeypatch.setattr(ra, "CACHE", tmp_path / "cache")
    (tmp_path / "cache").mkdir(exist_ok=True)
    monkeypatch.setattr(ra, "STATE", tmp_path / ".ts")
    monkeypatch.setattr(ra, "LOCK", tmp_path / ".lock")
    _FrozenDT.frozen = when
    monkeypatch.setattr(ra, "datetime", _FrozenDT)
    _seed(tmp_path)


# ────────────────── 1. 时段判据边界 ──────────────────
@pytest.mark.parametrize(
    "when,expected",
    [
        (datetime(2026, 9, 29, 9, 24), False),   # 竞价纪元前
        (datetime(2026, 9, 29, 9, 25), True),
        (datetime(2026, 9, 29, 10, 0), True),
        (datetime(2026, 9, 29, 15, 0), True),
        (datetime(2026, 9, 29, 15, 59), True),
        (datetime(2026, 9, 29, 16, 0), False),   # 与 EOD_COVER_HHMM 对齐
        (datetime(2026, 9, 29, 17, 51), False),  # 09-29 事故时刻
        (datetime(2026, 9, 26, 10, 0), False),   # 周六
    ],
)
def test_session_window_boundaries(when: datetime, expected: bool) -> None:
    assert ra._session_open(when) is expected


def test_session_window_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """留可逆开关：排障时允许回退到「不限时段」。"""
    monkeypatch.setenv("REALTIME_ANALYTICS_SESSION_ONLY", "0")
    assert ra._session_open(datetime(2026, 9, 29, 23, 0)) is True


# ────────────────── 2. main() 收盘后零写盘 ──────────────────
def test_main_writes_nothing_after_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ 核心断言：17:51 跑一轮，**一个文件都不许写**（否则覆盖盘后链产物）。"""
    _patch(monkeypatch, tmp_path, datetime(2026, 9, 29, 17, 51))

    def _boom(*a, **k):
        raise AssertionError("收盘后不得写盘（会覆盖盘后链产物）")

    monkeypatch.setattr(ra, "_write", _boom)
    ra.main()                                   # 不抛 = 未写盘
    assert not (tmp_path / "realtime_analytics.json").exists()
    assert not (tmp_path / "cache" / "theme_rotation.json").exists()


def test_main_still_writes_in_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """盘中照常写 —— 守卫不得误伤正常时段。"""
    _patch(monkeypatch, tmp_path, datetime(2026, 9, 29, 10, 0))

    class _TR:                                  # 桩掉真实引擎，只验写盘行为
        @staticmethod
        def build(**_kwargs):
            return {"date": "2026-09-29", "candidates": []}

    monkeypatch.setitem(sys.modules, "theme_rotation", _TR)
    ra.main()
    assert (tmp_path / "realtime_analytics.json").exists()
    tr = tmp_path / "cache" / "theme_rotation.json"
    assert tr.exists()
    assert json.loads(tr.read_text(encoding="utf-8"))["realtime"] is True
