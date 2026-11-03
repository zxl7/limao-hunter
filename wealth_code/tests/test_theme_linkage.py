from copy import deepcopy

import pytest

from core.signal_eval import evaluate_realtime_signal
from core.theme_linkage import annotate_eod, apply_auction_gate
from core.theme_resonance import _num, build_eod_facts
from core.theme_resonance import annotate_eod as annotate_resonance


def _candidate(*, boards=1, pool_count=1, reported_count=None):
    row = {
        "code": "000001",
        "name": "测试股",
        "boards": boards,
        "core_theme": "机器人",
        "theme_name": "机器人",
        "theme_pool_zt_count": pool_count,
        "theme_mates_cnt": max(pool_count or 0, reported_count or 0),
    }
    if reported_count is not None:
        row["theme_reported_zt_count"] = reported_count
    annotate_eod([row])
    return row


def _record(candidate):
    return {
        **candidate,
        "expectation": {
            "expected_range": [2.0, 5.0],
            "super_gap_min": 5.5,
            "auction_amount_min_yi": 1.0,
            "auction_amount_super_yi": 1.5,
        },
    }


def test_eod_facts_keep_pool_and_reported_counts_separate():
    """外部板块计数不能覆盖完整涨停池对“独苗”的客观观察。"""
    row = _candidate(pool_count=1, reported_count=5)
    facts = row["linkage_facts"]
    assert facts["eod_status"] == "standalone"
    assert facts["theme_pool_zt_count"] == 1
    assert facts["theme_reported_zt_count"] == 5
    assert facts["source_conflict"] is True


def test_linked_theme_is_preferred_when_auction_has_support():
    row = _candidate(pool_count=3)
    result = apply_auction_gate(row, {"mates_quoted": 2, "red_open": 1})
    assert result["block"] is False
    assert result["action"] == "prefer"


def test_lowboard_standalone_without_red_open_is_blocked(monkeypatch):
    """修复漏洞：watch 内的低板独苗即使本票量价达标，也不能直接 buy。"""
    monkeypatch.setenv("THEME_LINKAGE_CANDIDATE_MODE", "soft")
    row = _candidate(boards=1, pool_count=1)
    result = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 0},
    )
    assert result["decision_status"] == "pending"
    assert result["signal_label"] == "板块联动待确认"


def test_missing_mate_quotes_is_unknown_and_not_blocked(monkeypatch):
    """数据不足必须保留 unknown，不能以“没有板块支持”误杀。"""
    monkeypatch.setenv("THEME_LINKAGE_CANDIDATE_MODE", "soft")
    row = _candidate(boards=1, pool_count=1)
    result = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 0, "red_open": 0},
    )
    assert result["decision_status"] == "buy"


def test_high_board_standalone_stays_available_in_soft_mode(monkeypatch):
    """软模式不粗暴误杀高标独立核心。"""
    monkeypatch.setenv("THEME_LINKAGE_CANDIDATE_MODE", "soft")
    row = _candidate(boards=3, pool_count=1)
    result = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 0},
    )
    assert result["decision_status"] == "buy"


def test_annotate_mode_only_leaves_evidence(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_CANDIDATE_MODE", "annotate")
    row = _candidate(boards=1, pool_count=1)
    result = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 0},
    )
    assert result["decision_status"] == "buy"


def test_sidecar_stage_records_facts_without_prefer_or_auction_block(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "sidecar_record")
    monkeypatch.setenv("THEME_LINKAGE_CANDIDATE_MODE", "soft")
    row = _candidate(boards=1, pool_count=1)

    assert row["linkage_facts"]["eod_status"] == "standalone"
    assert row["linkage_eod_hint"]["rollout_stage"] == "sidecar_record"
    assert "linkage_discipline" not in row
    assert "linkage_prefer" not in row

    result = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 0},
    )
    assert result["decision_status"] == "buy"


def test_candidate_prefer_only_adds_display_hint(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "candidate_prefer")
    monkeypatch.setenv("THEME_LINKAGE_CANDIDATE_MODE", "soft")
    row = _candidate(boards=1, pool_count=3)

    assert row["linkage_prefer"] is True
    assert row["linkage_eod_hint"]["action"] == "prefer"
    assert "score" not in row and "bucket" not in row

    result = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 1},
    )
    assert result["decision_status"] == "buy"


def test_auction_gate_stage_keeps_soft_lowboard_scope(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "auction_gate")
    monkeypatch.setenv("THEME_LINKAGE_CANDIDATE_MODE", "soft")
    row = _candidate(boards=3, pool_count=1)

    result = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 0},
    )
    assert result["decision_status"] == "buy"


def test_strict_stage_does_not_enable_strict_mode_implicitly(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "strict")
    monkeypatch.setenv("THEME_LINKAGE_CANDIDATE_MODE", "soft")
    row = _candidate(boards=3, pool_count=1)

    result = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 0},
    )
    assert result["decision_status"] == "buy"


def test_strict_mode_expands_scope_only_after_strict_stage(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_CANDIDATE_MODE", "strict")
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "candidate_prefer")
    row = _candidate(boards=3, pool_count=1)

    before_gate = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 0},
    )
    assert before_gate["decision_status"] == "buy"

    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "strict")
    after_gate = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 0},
    )
    assert after_gate["decision_status"] == "pending"


def test_resonance_structure_and_description_do_not_confirm_technical_or_catalyst(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "candidate_prefer")
    row = _candidate(boards=2, pool_count=3)
    row.update({
        "sector_flow_yi": 12.5,
        "ladder_health": 7.0,
        "carry_cnt": 1,
        "core_theme_logic": "机器人产业订单扩张",
    })

    annotate_resonance([row])

    facts = row["theme_resonance_facts"]
    assert facts["status"] == "unknown"
    assert facts["linkage_status"] == "linked"
    assert facts["flow_status"] == "inflow"
    assert facts["structure_status"] == "confirmed"
    assert facts["technical_status"] == "unknown"
    assert facts["catalyst_status"] == "described"
    assert set(facts["missing_dimensions"]) == {"technical", "catalyst"}
    assert "未接独立技术证据" in facts["missing_dimensions"]["technical"]
    assert facts["evidence_version"] == "theme_resonance_evidence_v2"
    assert facts["sources"] == {
        "linkage": ["linkage_facts.schema", "linkage_facts.eod_status"],
        "catalyst": ["core_theme_logic"],
        "flow": ["sector_flow_yi"],
        "structure": ["ladder_health", "carry_cnt"],
        "technical": [],
    }
    assert row["theme_resonance_prefer"] is False
    assert "score" not in row and "bucket" not in row


def test_resonance_marks_flow_conflict_without_blocking_existing_auction_decision(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "auction_gate")
    row = _candidate(boards=2, pool_count=3)
    row.update({"sector_flow_yi": -8.0, "ladder_health": 7.0, "carry_cnt": 2})

    annotate_resonance([row])

    facts = row["theme_resonance_facts"]
    assert facts["status"] == "contradicted"
    assert "本次观测板块资金净流出" in facts["note"]
    assert "不代表持续退潮" in facts["note"]
    assert "technical" in facts["missing_dimensions"]
    assert "catalyst" in facts["missing_dimensions"]
    result = evaluate_realtime_signal(
        _record(row),
        {"prev_close": 10, "auction_price": 10.3, "auction_amount_yi": 1.0},
        sector_support={"mates_quoted": 2, "red_open": 1},
    )
    assert result["decision_status"] == "buy"


def test_resonance_keeps_missing_data_unknown_and_unpreferred(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "candidate_prefer")
    row = _candidate(boards=1, pool_count=3)

    annotate_resonance([row])

    facts = row["theme_resonance_facts"]
    assert facts["status"] == "unknown"
    assert facts["flow_status"] == "unknown"
    assert facts["technical_status"] == "unknown"
    assert set(facts["missing_dimensions"]) == {"technical", "catalyst", "flow", "structure"}
    assert row["theme_resonance_prefer"] is False


@pytest.mark.parametrize("verified", ["single_verified", "sustained_verified"])
def test_resonance_verified_catalyst_still_lacks_independent_technical(verified):
    row = _candidate(pool_count=3)
    row.update({
        "leader_evidence": {"dimensions": {"catalyst": {"status": verified}}},
        "sector_flow_yi": 12.5,
        "ladder_health": 7.0,
        "carry_cnt": 2,
    })
    facts = build_eod_facts(row)
    assert facts["status"] == "unknown"
    assert facts["catalyst_status"] == "verified"
    assert facts["structure_status"] == "confirmed"
    assert facts["technical_status"] == "unknown"
    assert set(facts["missing_dimensions"]) == {"technical"}
    assert facts["sources"]["catalyst"] == ["leader_evidence.dimensions.catalyst.status"]
    assert facts["sources"]["technical"] == []


@pytest.mark.parametrize("ladder,carry,expected", [
    (None, None, "unknown"),
    (6, 0, "confirmed"),
    (None, 1, "confirmed"),
    (3, 0, "weak"),
    (3, None, "weak"),
    (5, 0, "developing"),
    (None, 0, "developing"),
])
def test_resonance_ladder_and_carry_only_describe_structure(ladder, carry, expected):
    row = _candidate(pool_count=3)
    row.update({"sector_flow_yi": 1, "ladder_health": ladder, "carry_cnt": carry})
    facts = build_eod_facts(row)
    assert facts["structure_status"] == expected
    assert facts["technical_status"] == "unknown"
    assert facts["status"] == "unknown"
    assert ("structure" in facts["missing_dimensions"]) == (expected == "unknown")


@pytest.mark.parametrize("flow", [None, 0, 1, -1])
def test_resonance_standalone_keeps_unconfirmed_and_missing_evidence(flow):
    row = _candidate(pool_count=1)
    row["sector_flow_yi"] = flow
    facts = build_eod_facts(row)
    assert facts["status"] == "unconfirmed"
    assert facts["linkage_status"] == "standalone"
    assert {"technical", "catalyst", "structure"} <= set(facts["missing_dimensions"])
    assert ("flow" in facts["missing_dimensions"]) == (flow is None)


@pytest.mark.parametrize("row", [
    {"linkage_facts": ["linked"]},
    {"linkage_facts": {"schema": "linkage_facts_v1", "eod_status": ["linked"]}},
    {"leader_evidence": ["single_verified"]},
    {"leader_evidence": {"dimensions": ["catalyst"]}},
    {"leader_evidence": {"dimensions": {"catalyst": ["single_verified"]}}},
    {"leader_evidence": {"dimensions": {"catalyst": True}}},
    {"leader_evidence": {"dimensions": {"catalyst": "single_verified"}}},
    {"leader_evidence": {"dimensions": {"catalyst": 1}}},
    {"leader_evidence": {"dimensions": {"catalyst": None}}},
    {"leader_evidence": {"dimensions": {"catalyst": {"status": ["single_verified"]}}}},
])
def test_resonance_invalid_nested_evidence_is_safe(row):
    before = deepcopy(row)
    facts = build_eod_facts(row)
    assert facts["status"] == "unknown"
    assert facts["catalyst_status"] == "unknown"
    assert facts["technical_status"] == "unknown"
    assert "catalyst" in facts["missing_dimensions"]
    assert row == before


@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), float("-inf"),
    "nan", "NaN", "inf", "-Infinity", "1e9999", True, False, 10 ** 1000,
    None, "", "bad", [], {},
], ids=[
    "nan", "inf", "negative-inf", "nan-text", "NaN-text", "inf-text",
    "negative-inf-text", "overflow-text", "true", "false", "overflow-int",
    "none", "empty", "bad-text", "list", "dict",
])
def test_resonance_rejects_invalid_numbers(value):
    assert _num(value) is None
    row = _candidate(pool_count=3)
    row.update({"sector_flow_yi": value, "ladder_health": value, "carry_cnt": value})
    facts = build_eod_facts(row)
    assert facts["status"] == "unknown"
    assert facts["flow_status"] == "unknown"
    assert facts["structure_status"] == "unknown"
    assert facts["sector_flow_yi"] is None
    assert facts["ladder_health"] is None
    assert facts["carry_cnt"] is None
    assert {"flow", "structure", "technical"} <= set(facts["missing_dimensions"])


@pytest.mark.parametrize("value,expected", [(0, 0.0), (-8, -8.0), ("12.5", 12.5)])
def test_resonance_accepts_finite_numbers(value, expected):
    assert _num(value) == expected


def test_resonance_unverified_news_is_not_verified_catalyst():
    row = _candidate(pool_count=3)
    row.update({
        "leader_evidence": {"dimensions": {"catalyst": {"status": "unverified_news"}}},
        "core_theme_logic": "题材描述",
        "sector_flow_yi": 1,
        "ladder_health": 7,
    })
    facts = build_eod_facts(row)
    assert facts["status"] == "unknown"
    assert facts["catalyst_status"] == "unverified"
    assert "catalyst" in facts["missing_dimensions"]
    assert facts["sources"]["catalyst"] == ["leader_evidence.dimensions.catalyst.status"]


def test_resonance_sources_do_not_invent_missing_fields():
    facts = build_eod_facts({})
    assert all(fields == [] for fields in facts["sources"].values())
    assert set(facts["missing_dimensions"]) == {"linkage", "catalyst", "flow", "structure", "technical"}


def test_resonance_asof_is_optional_keyword_only_and_build_is_pure():
    row = _candidate(pool_count=3)
    before = deepcopy(row)
    asof = "2026-10-21T15:00:00+08:00"
    assert build_eod_facts(row)["asof"] is None
    facts = build_eod_facts(row, asof=asof)
    assert facts["asof"] == asof
    assert build_eod_facts(row, asof=asof) == facts
    assert row == before
    with pytest.raises(TypeError):
        build_eod_facts(row, asof)
    with pytest.raises(TypeError):
        annotate_resonance([row], asof)


def test_resonance_annotation_is_idempotent_and_preserves_scores_order_and_rank(monkeypatch):
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "candidate_prefer")
    rows = [_candidate(pool_count=3), _candidate(pool_count=1)]
    rows[0].update({"score": 60, "raw_score": 55, "bucket": "watch", "rank": 2})
    rows[1].update({"score": 90, "raw_score": 85, "bucket": "relay", "rank": 1})
    before = deepcopy(rows)
    identities = [id(row) for row in rows]
    asof = "2026-10-21T15:00:00+08:00"
    assert annotate_resonance(rows, asof=asof) is rows
    once = deepcopy(rows)
    assert annotate_resonance(rows, asof=asof) == once
    assert [id(row) for row in rows] == identities
    for row, original in zip(rows, before):
        assert all(row[key] == value for key, value in original.items())
        assert row["theme_resonance_facts"]["asof"] == asof
        assert row["theme_resonance_prefer"] is False


@pytest.mark.parametrize("old_prefer", [True, False])
def test_resonance_rollout_downgrade_removes_stale_prefer(monkeypatch, old_prefer):
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "candidate_prefer")
    row = _candidate(pool_count=3)
    annotate_resonance([row])
    row["theme_resonance_prefer"] = old_prefer
    before_facts = deepcopy(row["theme_resonance_facts"])
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "sidecar_record")
    annotate_resonance([row])
    assert "theme_resonance_prefer" not in row
    assert row["theme_resonance_facts"] == before_facts
    once = deepcopy(row)
    annotate_resonance([row])
    assert row == once
