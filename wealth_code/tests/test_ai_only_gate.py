from core import ai_caliber
from pipelines import pipeline_verify


def test_ai_watch_cannot_be_overridden_by_valve():
    candidate = {"code": "600371", "name": "万向德农", "expectation": {"expected_range": [0, 0.5], "auction_amount_min_yi": 0.46}}
    quote = {"prev_close": 10, "auction_price": 10.2, "auction_amount_yi": 1}
    ok, _, meta = ai_caliber.decide({"view": None, "bias": "normal"}, 4.63, expected_range=[0, 0.5], auction_amount_yi=0.52, need_yi=0.46, valve_hit=True)
    assert ok is False
    assert meta["ai_view"] == "watch"
    record = pipeline_verify._build_record(candidate, quote, 9.0, date10="2026-09-23", ai_decisions={"600371": {"view": "watch"}}, valve_code="600371")
    assert record["performance"]["open_check"]["can_enter"] is False


def test_missing_ai_plan_blocks_trade():
    candidate = {"code": "000001", "name": "样本", "expectation": {"expected_range": [0, 3]}}
    quote = {"prev_close": 10, "auction_price": 10.2}
    record = pipeline_verify._build_record(candidate, quote, 10.5, date10="2026-09-23", ai_decisions=None)
    check = record["performance"]["open_check"]
    assert check["caliber"] == "ai_missing_no_trade"
    assert check["can_enter"] is False
