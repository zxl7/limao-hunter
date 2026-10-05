# -*- coding: utf-8 -*-
"""A股交易日历 + 盘中 in_trading 节假日门禁回归测试（2026-09-30）。

覆盖：
1. common.trading_calendar.is_a_share_trading_day：open_dates 补班开市 / closed_dates 休市 /
   普通周末 / 普通交易日 / 字符串与 datetime 输入。
2. next_a_share_trading_day 跨假期/补班正确跳跃。
3. 6 个盘中脚本的 in_trading() 在国庆休市日(2026-10-05)返回 False、补班开市日(2026-10-10)返回 True、
   普通交易日(2026-10-08)返回 True（is_a_share_trading_day 为唯一权威）。
"""
import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "scripts"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import common.trading_calendar as tc  # noqa: E402

BJ_TZ = timezone(timedelta(hours=8))

# 关键日期
HOLIDAY = datetime(2026, 10, 5, 10, 30)   # 周一 · 国庆休市
TRADING = datetime(2026, 10, 8, 10, 30)   # 周四 · 普通交易日
MAKEUP = datetime(2026, 10, 10, 10, 30)   # 周六 · 调休补班开市
WEEKEND = datetime(2026, 10, 3, 10, 30)   # 周六 · 普通周末（国庆假期内）


# ── 1. is_a_share_trading_day ─────────────────────────────────────────────
class TestIsAShareTradingDay:
    def test_open_dates_makeup_days(self):
        # 6 个调休补班开市日（含周末）必须开市
        for d in ("2026-01-04", "2026-02-14", "2026-02-28",
                  "2026-05-09", "2026-09-20", "2026-10-10"):
            assert tc.is_a_share_trading_day(d) is True, d

    def test_closed_dates_holidays(self):
        # 国庆休市日（含工作日 10-05/10-06/10-07）必须休市
        for d in ("2026-10-01", "2026-10-02", "2026-10-05",
                  "2026-10-06", "2026-10-07", "2026-01-01", "2026-02-18"):
            assert tc.is_a_share_trading_day(d) is False, d

    def test_normal_weekend_not_open(self):
        # 普通周末（不在 open_dates）休市
        assert tc.is_a_share_trading_day(WEEKEND) is False
        assert tc.is_a_share_trading_day("2026-01-03") is False

    def test_normal_trading_day(self):
        assert tc.is_a_share_trading_day(TRADING) is True
        assert tc.is_a_share_trading_day("2026-10-09") is True  # 周五

    def test_accepts_datetime_and_string(self):
        assert tc.is_a_share_trading_day(HOLIDAY) is False
        assert tc.is_a_share_trading_day("2026-10-05") is False
        assert tc.is_a_share_trading_day(MAKEUP) is True

    def test_invalid_input_false(self):
        assert tc.is_a_share_trading_day("not-a-date") is False
        assert tc.is_a_share_trading_day("2026-13-40") is False


# ── 2. next_a_share_trading_day ───────────────────────────────────────────
class TestNextTradingDay:
    def test_skips_holiday_weekend(self):
        # 国庆假期最后一天 10-07 之后 → 10-08 周四
        assert tc.next_a_share_trading_day("2026-10-07") == "2026-10-08"

    def test_makeup_day_bridges_weekend(self):
        # 10-10 补班周六之后 → 10-11 周日休市 → 10-12 周一
        assert tc.next_a_share_trading_day("2026-10-10") == "2026-10-12"

    def test_invalid_returns_none(self):
        assert tc.next_a_share_trading_day("garbage") is None


# ── 3. in_trading 节假日门禁（6 个盘中脚本）────────────────────────────────
SCRIPTS = ["live_signal", "short_spirit", "intraday_pulse",
           "newsflash_express", "intraday_beat", "schedule_sentinel"]

# in_trading 取“当前时间”的来源分两类，需分别 patch：
#   ("datetime", "now") → from datetime import datetime，调 datetime.now()（类方法）
#   ("now",)            → 模块级 now() 函数
#   None                → 直接接收 dt 参数（schedule_sentinel）
SCRIPT_NOW_PATCH = {
    "live_signal": ("datetime", "now"),
    "short_spirit": ("datetime", "now"),
    "intraday_pulse": ("now",),
    "newsflash_express": ("now",),
    "intraday_beat": ("now",),
    "schedule_sentinel": None,
}


def _load_modules():
    loaded = {}
    for name in SCRIPTS:
        try:
            mod = importlib.import_module(name)
            loaded[name] = mod
        except Exception as e:  # pragma: no cover - 仅记录导入失败
            print(f"[warn] {name} 导入失败: {e}")
    return loaded


_LOADED = _load_modules()


def _in_trading_with(mod, dt):
    """在 mod.in_trading 上固定 now=dt，返回结果。"""
    spec = SCRIPT_NOW_PATCH.get(mod.__name__.split(".")[-1])
    if spec is None:
        return mod.in_trading(dt)
    if spec[0] == "datetime":
        # from datetime import datetime：datetime.now 是 C 级不可变槽，
        # 改为替换模块级 datetime 名为带 .now() 的伪对象。
        fake = type("FakeDateTime", (), {"now": staticmethod(lambda *a, **k: dt)})
        with patch.object(mod, "datetime", fake):
            return mod.in_trading()
    # 模块级 now() 函数（可被 patch 替换）
    with patch.object(mod, "now", return_value=dt):
        return mod.in_trading()


class TestInTradingHolidayGate:
    def test_holiday_returns_false(self):
        for name in SCRIPTS:
            mod = _LOADED.get(name)
            if mod is None:
                pytest.skip(f"{name} 未导入")
            assert _in_trading_with(mod, HOLIDAY) is False, f"{name} 国庆休市日未拦截"

    def test_makeup_saturday_returns_true(self):
        for name in SCRIPTS:
            mod = _LOADED.get(name)
            if mod is None:
                pytest.skip(f"{name} 未导入")
            assert _in_trading_with(mod, MAKEUP) is True, f"{name} 补班周六未放行"

    def test_normal_trading_day_returns_true(self):
        for name in SCRIPTS:
            mod = _LOADED.get(name)
            if mod is None:
                pytest.skip(f"{name} 未导入")
            assert _in_trading_with(mod, TRADING) is True, f"{name} 普通交易日未放行"


# ── 4. 反射守卫：in_trading 体内必须含节假日门禁（防被误删）─────────────────
def test_in_trading_body_contains_gate():
    for name in SCRIPTS:
        path = ROOT / "scripts" / f"{name}.py"
        src = path.read_text(encoding="utf-8")
        idx = src.find("def in_trading")
        assert idx >= 0, f"{name} 未定义 in_trading"
        # 取函数起始后 25 行作为函数体片段
        body = src[idx: idx + src[idx:].find("\n\ndef ") if "\n\ndef " in src[idx:] else idx + 1200]
        assert "is_a_share_trading_day" in body, (
            f"{name}.in_trading 缺少 is_a_share_trading_day 门禁（被移除？）"
        )
