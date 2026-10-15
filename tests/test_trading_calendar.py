# -*- coding: utf-8 -*-
"""A股交易日历 + 盘中 in_trading 节假日门禁回归测试（2026-09-30）。

覆盖：
1. common.trading_calendar.is_a_share_trading_day：closed_dates 休市 / 普通周末 / 普通交易日 /
   字符串与 datetime 输入。
2. next_a_share_trading_day 跨假期/周末正确跳跃。
3. 6 个盘中脚本的 in_trading() 在国庆休市日(2026-10-05)返回 False、周末调休补班日(2026-10-10)返回
   False、普通交易日(2026-10-08)返回 True（is_a_share_trading_day 为唯一权威）。

⛔ 2026-10-09 修正：**A股周末恒休市，调休补班日不开市**（沪深北交易所周末一律休市，
补班只约束机关/企事业单位）。原测试断言 6 个周末补班日为交易日，是缺陷的源头
——它让 next_a_share_trading_day('2026-10-09') 错返 10-10（周六）。
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
MAKEUP = datetime(2026, 10, 10, 10, 30)   # 周六 · 调休补班（A股仍休市）
WEEKEND = datetime(2026, 10, 3, 10, 30)   # 周六 · 普通周末（国庆假期内）

# 历史上被误填进 open_dates 的 6 个周末补班日：全部必须休市
WEEKEND_MAKEUP_DATES = ("2026-01-04", "2026-02-14", "2026-02-28",
                        "2026-05-09", "2026-09-20", "2026-10-10")


# ── 1. is_a_share_trading_day ─────────────────────────────────────────────
class TestIsAShareTradingDay:
    def test_weekend_makeup_days_stay_closed(self):
        # 铁律：A股周末恒休市，调休补班日不构成交易日
        for d in WEEKEND_MAKEUP_DATES:
            assert tc.is_a_share_trading_day(d) is False, d

    def test_closed_dates_holidays(self):
        # 国庆休市日（含工作日 10-05/10-06/10-07）必须休市
        for d in ("2026-10-01", "2026-10-02", "2026-10-05",
                  "2026-10-06", "2026-10-07", "2026-01-01", "2026-02-18"):
            assert tc.is_a_share_trading_day(d) is False, d

    def test_mid_autumn_workday_holiday_is_closed(self):
        """⛔ 回归：2026-09-25（周五·中秋节）是**工作日休市**，必须判 False。

        缺陷复现：closed_dates 曾漏列该日 → 上游在 09-25 拿到 09-24 的涨停池 →
        归档出一份与 09-24 逐字段相同的幽灵日 `zt_2026-09-25.json`，
        污染断板/形态/位置判据的交易日序列。
        """
        assert tc.is_a_share_trading_day("2026-09-25") is False
        assert tc.is_a_share_trading_day("2026-09-24") is True   # 节前最后一日
        assert tc.is_a_share_trading_day("2026-09-28") is True   # 节后首日

    def test_normal_weekend_not_open(self):
        # 普通周末与补班周末一律休市
        assert tc.is_a_share_trading_day(WEEKEND) is False
        assert tc.is_a_share_trading_day("2026-01-03") is False
        assert tc.is_a_share_trading_day("2026-10-11") is False  # 周日

    def test_normal_trading_day(self):
        assert tc.is_a_share_trading_day(TRADING) is True
        assert tc.is_a_share_trading_day("2026-10-09") is True  # 周五

    def test_accepts_datetime_and_string(self):
        assert tc.is_a_share_trading_day(HOLIDAY) is False
        assert tc.is_a_share_trading_day("2026-10-05") is False
        assert tc.is_a_share_trading_day(MAKEUP) is False
        assert tc.is_a_share_trading_day("2026-10-10") is False

    def test_invalid_input_false(self):
        assert tc.is_a_share_trading_day("not-a-date") is False
        assert tc.is_a_share_trading_day("2026-13-40") is False


# ── 2. next_a_share_trading_day ───────────────────────────────────────────
class TestNextTradingDay:
    def test_skips_holiday_weekend(self):
        # 国庆假期最后一天 10-07 之后 → 10-08 周四
        assert tc.next_a_share_trading_day("2026-10-07") == "2026-10-08"

    def test_friday_after_national_day_skips_weekend_not_makeup(self):
        """⛔ 回归：2026-10-09（周五）之后是 10-12（周一），**不是** 10-10（周六）。

        缺陷复现：原 open_dates 含 10-10 → 返回 10-10 → 盘后 AI 预案被写成
        ai_view_2026-10-10.json，周一竞价卡读不到 → AI 判断整段消失。
        """
        assert tc.next_a_share_trading_day("2026-10-09") == "2026-10-12"
        assert tc.is_a_share_trading_day("2026-10-10") is False

    def test_makeup_weekend_never_bridges(self):
        """6 个历史误填的周末补班日均不得作为「下一交易日」出现。"""
        bad = set(WEEKEND_MAKEUP_DATES)
        for start in ("2026-01-02", "2026-02-13", "2026-02-27",
                      "2026-05-08", "2026-09-18", "2026-10-09"):
            nxt = tc.next_a_share_trading_day(start)
            assert nxt not in bad, f"{start} 的下一交易日错跳到周末 {nxt}"

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

    def test_makeup_saturday_returns_false(self):
        for name in SCRIPTS:
            mod = _LOADED.get(name)
            if mod is None:
                pytest.skip(f"{name} 未导入")
            assert _in_trading_with(mod, MAKEUP) is False, f"{name} 补班周六未拦截（A股周末休市）"

    def test_normal_trading_day_returns_true(self):
        for name in SCRIPTS:
            mod = _LOADED.get(name)
            if mod is None:
                pytest.skip(f"{name} 未导入")
            assert _in_trading_with(mod, TRADING) is True, f"{name} 普通交易日未放行"


# ── 4. 反射守卫：in_trading 体内必须含节假日门禁（防被误删）─────────────────
def test_in_trading_body_contains_gate():
    """`in_trading` 必须有节假日门禁 —— **直调唯一真源**，或**委托本模块的薄封装**。

    2026-10-09 唯一化后，`short_spirit` 把门禁抽成模块级 `is_trading_day()` 薄封装
    （内部转发 `common.trading_calendar`），故两种形态都接受；但**必须有其一**，
    且薄封装本身必须真的转发唯一真源（防「把自实现藏进 helper」逃逸）。
    """
    for name in SCRIPTS:
        path = ROOT / "scripts" / f"{name}.py"
        src = path.read_text(encoding="utf-8")
        idx = src.find("def in_trading")
        assert idx >= 0, f"{name} 未定义 in_trading"
        # 取函数起始后 25 行作为函数体片段
        body = src[idx: idx + src[idx:].find("\n\ndef ") if "\n\ndef " in src[idx:] else idx + 1200]
        direct = "is_a_share_trading_day" in body
        delegated = "is_trading_day(" in body
        assert direct or delegated, (
            f"{name}.in_trading 缺少节假日门禁（既未直调唯一真源，也未委托本地薄封装）"
        )
        if delegated and not direct:
            helper = src[src.find("def is_trading_day"):]
            helper = helper[: helper.find("\n\ndef ") if "\n\ndef " in helper else 1200]
            assert "is_a_share_trading_day" in helper, (
                f"{name}.is_trading_day 未转发唯一真源（门禁被架空）"
            )


# ── 5. 配置表纪律（2026-10-09 缺陷复发的防线）───────────────────────────────
def _calendar_doc() -> dict:
    import json
    return json.loads((ROOT / "config" / "a_share_calendar.json").read_text(encoding="utf-8"))


class TestCalendarConfigDiscipline:
    def test_closed_dates_only_weekdays(self):
        """closed_dates 只准列**工作日**休市日——周末由「周末恒休市」规则覆盖。

        往里写周末 = 维护者仍按「补班开市」思路填表 → 缺陷复发信号。
        """
        bad = [d for d in sorted(tc._closed_dates())
               if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5]
        assert not bad, f"closed_dates 含周末（应删：周末恒休市）：{bad}"

    def test_open_dates_field_must_stay_empty(self):
        """open_dates 已废弃且恒为空——A股不存在「周末开市」，非空即缺陷复发。"""
        values = _calendar_doc().get("open_dates")
        assert values == [], f"open_dates 非空（A股周末恒休市）：{values}"

    def test_2026_official_holiday_weekdays_all_closed(self):
        """2026 年国务院安排里的**工作日**休市日必须全部在表内（含 09-25 中秋）。"""
        expected = {
            "2026-01-01", "2026-01-02",                                     # 元旦
            "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
            "2026-02-20", "2026-02-23",                                     # 春节（2/15–2/23 休市）
            "2026-04-06",                                                   # 清明
            "2026-05-01", "2026-05-04", "2026-05-05",                       # 劳动节
            "2026-06-19",                                                   # 端午
            "2026-09-25",                                                   # 中秋（曾漏列）
            "2026-10-01", "2026-10-02", "2026-10-05",
            "2026-10-06", "2026-10-07",                                     # 国庆
        }
        missing = expected - tc._closed_dates()
        assert not missing, f"closed_dates 缺休市日：{sorted(missing)}"
