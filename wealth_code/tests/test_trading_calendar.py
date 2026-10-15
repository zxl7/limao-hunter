from __future__ import annotations

from common.trading_calendar import is_a_share_trading_day, next_a_share_trading_day
from pipelines.pipeline_verify import _next_day


def test_national_day_uses_configured_a_share_calendar():
    assert is_a_share_trading_day("2026-10-01") is False
    assert next_a_share_trading_day("2026-09-30") == "2026-10-08"
    assert _next_day("2026-09-30") == "2026-10-08"


def test_weekend_still_skips_to_next_trading_day():
    assert next_a_share_trading_day("2026-09-25") == "2026-09-28"


def test_mid_autumn_2026_is_a_market_holiday():
    """2026-09-25（周五·中秋）休市；节前最后交易日 09-24 的下一个交易日 = 09-28。

    ⛔ 2026-10-09 补录：该日曾漏列 closed_dates → 上游在 09-25 拿到 09-24 的涨停池 →
    归档出与 09-24 逐字段相同的幽灵日 `zt_2026-09-25.json`，污染交易日序列。
    """
    assert is_a_share_trading_day("2026-09-25") is False
    assert next_a_share_trading_day("2026-09-24") == "2026-09-28"
