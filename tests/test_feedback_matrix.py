import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "scripts" / "feedback_matrix.py"
spec = importlib.util.spec_from_file_location("feedback_matrix", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_strong_breadth_and_volume_is_euphoric():
    score, label = mod.sentiment_score(
        0.55, 0.50, 3.0, 0, 0.10, 1, 1.0, 0.80, 1.5,
        market_amount_yi=28000, breadth_up=4200, breadth_down=900,
    )
    assert score >= 9.0
    assert label == "亢奋"


def test_weak_market_is_not_euphoric():
    score, label = mod.sentiment_score(
        0.10, 0.08, -2.0, 5, 0.60, -1, -0.5, 0.15, -1.0,
        market_amount_yi=9000, breadth_up=800, breadth_down=4200,
    )
    assert score <= 3.0
    assert label in {"退潮", "分歧"}


def test_sentiment_returns_bounded_score():
    score, _ = mod.sentiment_score(1, 1, 20, 0, 0, 1, 10, 1, 10)
    assert 0 <= score <= 10
