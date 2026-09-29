from __future__ import annotations

import json
from pathlib import Path

from core.execution_audit import AuditPaths, build_execution_audit, next_completed_trade_day


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _candidate(date10: str, regime: str = "neutral") -> dict:
    return {
        "schema": "wealth_code_candidates_v1",
        "date": date10,
        "generated_at_bj": f"{date10} 15:05:00",
        "environment": {"regime": regime},
        "relay": [{"code": "000001", "name": "样本股", "relay_rank": 1,
                   "expectation": {"expected_range": [0, 3], "auction_amount_min_yi": 1}}],
        "watch": [],
    }


def test_next_completed_trade_day_skips_unobserved_holiday():
    assert next_completed_trade_day(["2026-10-01", "2026-10-09"], "2026-10-01") == "2026-10-09"
    assert next_completed_trade_day(["2026-10-01"], "2026-10-01") is None


def test_execution_audit_reconciles_frozen_batch_to_ledger(tmp_path: Path):
    root = tmp_path / "hunter"
    wc = root / "wealth_code" / "cache"
    _write(wc / "candidates-2026-09-24.json", _candidate("2026-09-24"))
    _write(wc / "auction_quotes-2026-09-25.json", {
        "000001": {"prev_close": 10, "auction_price": 10.2, "auction_amount_yi": 1.2, "time": "09:25:00"}
    })
    _write(wc / "pool_call-2026-09-25.json", {"views": {"000001": {"view": "participate"}}})
    _jsonl(wc / "ai_decisions.jsonl", [{"decision_date": "2026-09-24", "code": "000001", "view": "participate"}])
    _jsonl(wc / "account_nav_history.jsonl", [{
        "trade_date": "2026-09-25", "recommendation_date": "2026-09-24", "caliber": "ai",
        "stocks": [{"code": "000001", "name": "样本股", "return_pct": 2.5}],
    }])

    report = build_execution_audit(AuditPaths(root))
    batch = report["batches"][0]
    stock = batch["stocks"][0]
    assert batch["trade_date"] == "2026-09-25"
    assert batch["integrity"]["complete"] is True
    assert stock["execution"]["entered"] is True
    assert stock["execution"]["gross_return_pct"] == 2.5
    assert stock["quote"]["time"] == "09:25:00"


def test_execution_audit_marks_missing_plan_as_unverified(tmp_path: Path):
    root = tmp_path / "hunter"
    wc = root / "wealth_code" / "cache"
    _write(wc / "candidates-2026-09-24.json", _candidate("2026-09-24"))
    _write(wc / "auction_quotes-2026-09-25.json", {
        "000001": {"prev_close": 10, "auction_price": 10.2, "auction_amount_yi": 1.2, "time": "09:25:00"}
    })

    report = build_execution_audit(AuditPaths(root))
    stock = report["batches"][0]["stocks"][0]
    assert report["batches"][0]["caliber"]["expected"] == "unverified_without_frozen_ai_plan"
    assert "AI盘后预案缺失" in " ".join(stock["integrity"]["issues"])


def test_execution_audit_marks_ice_ai_entry_for_review(tmp_path: Path):
    root = tmp_path / "hunter"
    wc = root / "wealth_code" / "cache"
    _write(wc / "candidates-2026-09-24.json", _candidate("2026-09-24", regime="ice"))
    _write(wc / "auction_quotes-2026-09-25.json", {
        "000001": {"prev_close": 10, "auction_price": 10.2, "auction_amount_yi": 1.2, "time": "09:25:00"}
    })
    _write(wc / "pool_call-2026-09-25.json", {"views": {"000001": {"view": "participate"}}})
    _jsonl(wc / "ai_decisions.jsonl", [{"decision_date": "2026-09-24", "code": "000001", "view": "participate"}])
    _jsonl(wc / "account_nav_history.jsonl", [{
        "trade_date": "2026-09-25", "recommendation_date": "2026-09-24", "caliber": "ai",
        "stocks": [{"code": "000001", "return_pct": 1.0}],
    }])

    report = build_execution_audit(AuditPaths(root))
    stock = report["batches"][0]["stocks"][0]
    assert stock["mechanical"]["ice_ban"] is True
    assert stock["ai"]["can_enter"] is True
    assert stock["ai"]["final_can_enter"] is False
    assert stock["ai"]["ice_conflict"] is True
    assert "冰点期由AI放行" in " ".join(stock["integrity"]["issues"])
