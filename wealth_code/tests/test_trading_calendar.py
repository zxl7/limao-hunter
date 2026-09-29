from __future__ import annotations

from common.trading_calendar import is_a_share_trading_day, next_a_share_trading_day
from pipelines.pipeline_verify import _next_day


def test_national_day_uses_configured_a_share_calendar():
    assert is_a_share_trading_day("2026-10-01") is False
    assert next_a_share_trading_day("2026-09-30") == "2026-10-08"
    assert _next_day("2026-09-30") == "2026-10-08"


def test_weekend_still_skips_to_next_trading_day():
    assert next_a_share_trading_day("2026-09-25") == "2026-09-28"
