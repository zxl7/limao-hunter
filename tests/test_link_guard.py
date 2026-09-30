#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""链路断档守卫（scripts/link_guard.py）回归测试（2026-09-30 立）。

事故：2026-09-30 早盘 `intraday_pulse.py` 因未定义名（`_load`/`CACHE`）每轮崩溃，
      `latest.json` 卡在昨日 → `data_ready.ready=False` → 推送层 98 轮全 skip，
      异动群 09:15-09:41 零推送且**零告警**，早盘信号整段丢失、靠用户自己发现。

本测试锁死守卫的三条边界（全部在 tmp_path 里跑，不碰生产、不真推飞书）：
  · 连续断档第 5 轮才告警，冷却期内不重复；
  · 恢复即清零，不残留计数；
  · 标记文件陈旧（守护没在跑）判为断档，且文案带上分钟数；
  · LINK_GUARD=0 开关能真正关闭（回滚路径有效）。
"""
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("link_guard", ROOT / "scripts" / "link_guard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def guard(tmp_path, monkeypatch):
    """把守卫的全部路径指向 tmp_path，并拦下真实推送。"""
    m = _load()
    m.PULSE = tmp_path
    m.READY = tmp_path / "data_ready.json"
    m.STATE = tmp_path / ".state.json"
    m.ALERT_CARD = tmp_path / "card.json"
    sent = []
    monkeypatch.setattr(m, "_push_alert", lambda detail, streak: (sent.append((detail, streak)), True)[1])
    monkeypatch.setattr(m, "_in_session", lambda now: True)
    m.sent = sent
    return m


def _write_ready(guard, ready, date, generated_ts):
    guard.READY.write_text(json.dumps(
        {"ready": ready, "date": date, "generated_ts": generated_ts,
         "reason": "关键实时数据未完成或题材快照与行情时间戳不一致"}, ensure_ascii=False), encoding="utf-8")


def test_alerts_once_after_streak_then_cooldown(guard):
    """连续断档：前 4 轮静默，第 5 轮告警，冷却期内不再重复。"""
    now = datetime.now()
    _write_ready(guard, False, "2026-09-29", now.isoformat(timespec="seconds"))

    for _ in range(4):
        guard.main()
    assert guard.sent == [], "不足 5 轮不应告警"

    guard.main()
    assert len(guard.sent) == 1, "第 5 轮应告警"
    assert "2026-09-29" in guard.sent[0][0], "文案应说明数据日期"

    for _ in range(3):
        guard.main()
    assert len(guard.sent) == 1, "冷却期内不得重复刷屏"


def test_recovery_resets_streak(guard):
    """恢复就绪后计数清零，且不再告警。"""
    now = datetime.now()
    _write_ready(guard, False, "2026-09-29", now.isoformat(timespec="seconds"))
    for _ in range(3):
        guard.main()
    assert json.loads(guard.STATE.read_text())["fail_streak"] == 3

    _write_ready(guard, True, now.strftime("%Y-%m-%d"), now.isoformat(timespec="seconds"))
    guard.main()
    assert json.loads(guard.STATE.read_text())["fail_streak"] == 0


def test_stale_marker_reports_minutes(guard):
    """守护没在跑（标记陈旧）也算断档，且文案带分钟数。"""
    now = datetime.now()
    _write_ready(guard, True, now.strftime("%Y-%m-%d"),
                 (now - timedelta(minutes=22)).isoformat(timespec="seconds"))
    for _ in range(5):
        guard.main()
    assert len(guard.sent) == 1
    assert "22 分钟" in guard.sent[0][0]


def test_missing_marker_is_not_an_alert(guard):
    """标记文件尚未生成（守护首轮未落地）不算断档，避免开盘瞬间误报。"""
    for _ in range(6):
        guard.main()
    assert guard.sent == []


def test_kill_switch_disables_guard(guard, monkeypatch):
    """LINK_GUARD=0 必须真正关停（回滚路径）。"""
    monkeypatch.setenv("LINK_GUARD", "0")
    now = datetime.now()
    _write_ready(guard, False, "2026-09-29", now.isoformat(timespec="seconds"))
    for _ in range(8):
        guard.main()
    assert guard.sent == []
    assert not guard.STATE.exists(), "关闭状态下不应写状态"


def test_out_of_session_does_not_alert(guard, monkeypatch):
    """非交易时段不打扰。"""
    monkeypatch.setattr(guard, "_in_session", lambda now: False)
    now = datetime.now()
    _write_ready(guard, False, "2026-09-29", now.isoformat(timespec="seconds"))
    for _ in range(8):
        guard.main()
    assert guard.sent == []
