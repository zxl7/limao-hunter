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


def test_in_session_exempts_holiday_and_honors_makeup():
    """真实 _in_session：休市日（含国庆）False，补班周六/普通交易日 True。

    2026-10-06 国庆误报事故回归：原实现只判 weekday<5，把休市周二当盘中，
    看到 data_ready.ready=false（休市无数据本属正常）即误判「盘面数据断了」。
    现委托 is_a_share_trading_day，须正确识别节假日与调休补班。
    """
    m = _load()  # 不 mock _in_session，测真实实现
    cases = [
        (datetime(2026, 10, 1, 10, 0), False),   # 国庆假期周四
        (datetime(2026, 10, 6, 10, 0), False),   # 国庆假期周二（事故日）
        (datetime(2026, 10, 8, 10, 0), True),    # 节后首个交易日周四
        (datetime(2026, 10, 10, 10, 0), True),   # 周六调休补班开市
        (datetime(2026, 10, 4, 10, 0), False),   # 周日周末
        (datetime(2026, 10, 6, 7, 0), False),    # 国庆清晨（非时段，兜底也为 False）
    ]
    for dt, expected in cases:
        assert m._in_session(dt) is expected, f"_in_session({dt}) 应为 {expected}"


def test_holiday_does_not_alert(guard, monkeypatch):
    """法定节假日（如 2026-10-06 国庆）整体短路，绝不告警。"""
    # 用真实 _in_session 覆盖 fixture 的 mock（真实实现会判国庆→False→main 直接 return）
    real = _load()
    monkeypatch.setattr(guard, "_in_session", real._in_session)
    fixed = datetime(2026, 10, 6, 10, 0)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(guard, "datetime", FakeDT)
    _write_ready(guard, False, "2026-10-05", fixed.isoformat(timespec="seconds"))
    for _ in range(8):
        guard.main()
    assert guard.sent == [], "国庆休市日不应推送任何链路断档告警"


def test_session_start_is_market_open_not_pre_auction():
    """2026-10-08 修复：守卫从连续竞价开盘 09:30 起算，盘前 09:15-09:30 不查。

    盘前无连续竞价数据，「数据未就绪」是常态而非断档；原 09:15 起点会让守卫每个
    交易日 09:20 左右误报「盘面数据断了」。真实 _in_session 须：09:20（盘前）→ False，
    09:35（开盘后）→ True。
    """
    m = _load()  # 不 mock，测真实实现
    assert m._in_session(datetime(2026, 10, 8, 9, 20)) is False, "盘前 09:20 不应判为盘中"
    assert m._in_session(datetime(2026, 10, 8, 9, 35)) is True, "开盘后 09:35 应判为盘中"


def test_pre_open_does_not_alert_even_if_data_not_ready(guard, monkeypatch):
    """盘前（09:20 交易日）即便 data_ready=false（盘前常态），守卫也不告警。

    复现 2026-10-08 误报场景：09:19 推了「盘面数据断了」，根因是盘前 + same_source
    竞态 + pre_auction 三重叠加。修复后守卫 09:20 处于非盘中（SESSION_START=930），
    整体短路，绝不推送。
    """
    real = _load()
    monkeypatch.setattr(guard, "_in_session", real._in_session)
    fixed = datetime(2026, 10, 8, 9, 20)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(guard, "datetime", FakeDT)
    _write_ready(guard, False, "2026-10-05", fixed.isoformat(timespec="seconds"))
    for _ in range(8):
        guard.main()
    assert guard.sent == [], "盘前 09:20 即便数据未就绪也不应告警"
