from core.quant_score import compute_raw_score, map_tactics_fields


def _cand(code, factor=0):
    return {
        "code": code, "name": code, "boards": 2, "theme": "人工智能",
        "ign_score": 8, "exp_score": 8, "lead_score": 8, "cap_score": 8,
        "ladder_health": 8, "hs": 10, "cje_yi": 10, "zbc": 0,
        "history_relay_factor": factor,
    }


def test_history_factor_changes_raw_score_ordering():
    env = {"sentiment_score": 5, "nuke_count": 0, "broken_rate": 0, "regime": "neutral"}
    low = compute_raw_score(_cand("000001", 0), env, None, None)
    high = compute_raw_score(_cand("000002", 7), env, None, None)
    assert high["raw_score"] > low["raw_score"]


def test_mapping_keeps_core_candidate_eligible():
    env = {"sentiment_score": 7, "nuke_count": 0, "broken_rate": 0, "regime": "risk_on"}
    raw = compute_raw_score(_cand("000001", 4), env, None, None)
    mapped = map_tactics_fields(_cand("000001", 4), env, None, None, raw)
    assert mapped["_raw"] == raw["raw_score"]
    assert mapped["hasTradeTheme"] is True
    assert mapped["lbc"] == 2
