from core.signal_eval import evaluate_realtime_signal


def _record():
    return {
        "code": "000001",
        "name": "测试",
        "expectation": {
            "expected_range": [2.0, 5.0],
            "super_gap_min": 5.5,
            "auction_amount_min_yi": 1.0,
            "auction_amount_super_yi": 1.5,
        },
    }


def test_expected_gap_and_amount_returns_buy():
    result = evaluate_realtime_signal(_record(), {"prev_close": 10, "auction_price": 10.4, "auction_amount_yi": 0.8})
    assert result["decision_status"] in {"buy", "expected"}
    assert result["gap_pct"] == 4.0


def test_high_gap_returns_pending():
    result = evaluate_realtime_signal(_record(), {"prev_close": 10, "auction_price": 10.8, "auction_amount_yi": 1.0})
    assert result["decision_status"] == "pending"


def test_missing_quote_is_unavailable():
    result = evaluate_realtime_signal(_record(), None)
    assert result["decision_status"] == "unavailable"
