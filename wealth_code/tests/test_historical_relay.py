import json
from pathlib import Path

from core.historical_relay import annotate_historical_relay
from core.quant_score import compute_raw_score


def _write(path: Path, date: str, rows: list[dict]):
    path.joinpath(f"zt_{date}.json").write_text(json.dumps({"date": date, "zt_list": rows}), encoding="utf-8")


def test_history_probability_uses_next_trading_day(tmp_path):
    _write(tmp_path, "2026-01-02", [{"code": "000001", "boards": 2}, {"code": "000002", "boards": 2}])
    _write(tmp_path, "2026-01-05", [{"code": "000001", "boards": 3}])
    cand = [{"code": "000001", "boards": 2}]
    diag = annotate_historical_relay(cand, tmp_path, "2026-01-06", {"promote_rate": 35, "lb_promote_rate": 25, "sentiment_score": 5})
    assert diag["history_days"] == 2
    assert cand[0]["history_relay_prob"] == 0.625
    assert cand[0]["history_relay_factor"] == 5.5
    assert cand[0]["history_relay_samples"] == 2


def test_missing_history_is_neutral(tmp_path):
    cand = [{"code": "000001", "boards": 3}]
    annotate_historical_relay(cand, tmp_path, "2026-01-06", {})
    assert cand[0]["history_relay_prob"] is None
    assert cand[0]["history_relay_factor"] == 0.0


def test_quant_score_exposes_history_factor():
    cand = {"boards": 2, "history_relay_factor": 4.0, "history_relay_prob": 0.55}
    raw = compute_raw_score(cand, {"sentiment_score": 5, "nuke_count": 0, "broken_rate": 0, "regime": "neutral"}, None, None)
    assert raw["historical_relay_factor"] == 4.0
    assert raw["history_relay_prob"] == 0.55
