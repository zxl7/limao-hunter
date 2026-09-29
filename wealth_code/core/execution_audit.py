#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""财富密码执行审计。

本模块只读既有冻结件和账本，重建「推荐日 -> 执行日 -> 实际成交 -> 收盘收益」证据链。
它不参与候选评分、AI 决策或账户净值计算，避免把审计层反向写入生产策略。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


GROSS_RETURN_LABEL = "竞价至当日收盘毛收益"


@dataclass(frozen=True)
class AuditPaths:
    """审计所需的仓内路径；测试通过临时根目录注入，生产不依赖绝对路径。"""

    hunter_root: Path

    @property
    def wealth_cache(self) -> Path:
        return self.hunter_root / "wealth_code" / "cache"

    @property
    def cache(self) -> Path:
        return self.hunter_root / "cache"

    @property
    def candidates_dir(self) -> Path:
        return self.wealth_cache

    @property
    def nav_path(self) -> Path:
        return self.wealth_cache / "account_nav_history.jsonl"

    @property
    def decisions_path(self) -> Path:
        return self.wealth_cache / "ai_decisions.jsonl"


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _date_from_name(path: Path, prefix: str) -> str | None:
    stem = path.stem
    value = stem[len(prefix):] if stem.startswith(prefix) else ""
    return value if len(value) == 10 else None


def completed_trade_days(paths: AuditPaths) -> list[str]:
    """已完成交易日历。

    仅使用实际已落盘的竞价快照、账户结算和涨停归档；用于历史审计时，不能把法定
    节假日按工作日臆造成交易日。
    """
    days: set[str] = set()
    for path in paths.wealth_cache.glob("auction_quotes-????-??-??.json"):
        value = _date_from_name(path, "auction_quotes-")
        if value:
            days.add(value)
    for row in _read_jsonl(paths.nav_path):
        value = str(row.get("trade_date") or "").strip()
        if len(value) == 10:
            days.add(value)
    for path in (paths.cache / "zt_history").glob("zt_*.json"):
        blob = _load_json(path, {})
        value = str(blob.get("date") or path.stem.removeprefix("zt_"))[:10]
        # 涨停池必须实际有内容，避免非交易日接口返回的空/旧快照污染日历。
        if len(value) == 10 and isinstance(blob.get("zt_list"), list) and blob["zt_list"]:
            days.add(value)
    return sorted(days)


def next_completed_trade_day(days: list[str], recommendation_date: str) -> str | None:
    """从真实完成日历取下一日；无证据时返回 None，不用周末规则猜测。"""
    later = [day for day in days if day > recommendation_date]
    return later[0] if later else None


def _candidate_files(paths: AuditPaths) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for path in paths.candidates_dir.glob("candidates-????-??-??.json"):
        date10 = _date_from_name(path, "candidates-")
        if date10:
            out.append((date10, path))
    return sorted(out)


def _index_decisions(paths: AuditPaths) -> dict[tuple[str, str], dict[str, Any]]:
    """按决策日与代码建立索引；决策日就是盘后冻结候选的日期。"""
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _read_jsonl(paths.decisions_path):
        date10 = str(row.get("decision_date") or "").strip()
        code = str(row.get("code") or "").strip()
        if len(date10) == 10 and len(code) == 6:
            indexed[(date10, code)] = row
    return indexed


def _index_nav(paths: AuditPaths) -> dict[tuple[str, str], dict[str, Any]]:
    """账户账本是实际成交/收益唯一来源，按执行日与代码索引。"""
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _read_jsonl(paths.nav_path):
        trade_date = str(row.get("trade_date") or "").strip()
        for stock in row.get("stocks") or []:
            if not isinstance(stock, dict):
                continue
            code = str(stock.get("code") or "").strip()
            if len(trade_date) == 10 and len(code) == 6:
                indexed[(trade_date, code)] = {"ledger": row, "stock": stock}
    return indexed


def _pool_views(paths: AuditPaths, trade_date: str) -> tuple[dict[str, Any], bool]:
    path = paths.wealth_cache / f"pool_call-{trade_date}.json"
    doc = _load_json(path, {})
    views = doc.get("views") if isinstance(doc, dict) else None
    return (views if isinstance(views, dict) else {}), path.exists()


def _mechanical_result(candidate: dict[str, Any], quote: dict[str, Any] | None, regime: str | None) -> dict[str, Any]:
    """复用生产机械规则；冰点买点按生产链路降为 hit，保证审计口径一致。"""
    try:
        from core.signal_eval import evaluate_realtime_signal
    except ImportError:  # pragma: no cover - 仅独立导入环境
        from wealth_code.core.signal_eval import evaluate_realtime_signal

    signal = evaluate_realtime_signal(candidate, quote, regime=regime)
    decision = str(signal.get("decision_status") or "")
    ice_ban = regime == "ice" and decision == "buy"
    return {
        "status": "hit" if ice_ban else decision or None,
        "can_enter": bool(decision == "buy" and not ice_ban),
        "ice_ban": ice_ban,
        "gap_pct": signal.get("gap_pct"),
        "quote_time": signal.get("quote_time") or None,
    }


def _ai_result(
    decision: dict[str, Any] | None,
    pool_view: dict[str, Any] | None,
    candidate: dict[str, Any],
    quote: dict[str, Any] | None,
    regime: str | None,
) -> dict[str, Any]:
    """以生产 AI 规则复算裁决，池内冻结 view 优先覆盖盘后预案。"""
    if decision is None:
        return {"status": "missing", "can_enter": None, "view": None}
    try:
        from core import ai_caliber
    except ImportError:  # pragma: no cover
        from wealth_code.core import ai_caliber

    effective = dict(decision)
    if isinstance(pool_view, dict):
        for key in ("view", "bias", "reason", "position"):
            if pool_view.get(key) is not None:
                effective[key] = pool_view[key]
    gap = None
    amount = None
    if isinstance(quote, dict):
        try:
            prev = float(quote.get("prev_close") or 0)
            auction = float(quote.get("auction_price") or 0)
            gap = round((auction - prev) / prev * 100, 2) if prev > 0 and auction > 0 else None
        except (TypeError, ValueError):
            gap = None
        amount = quote.get("auction_amount_yi")
    can_enter, _note, meta = ai_caliber.decide(
        effective,
        gap,
        expected_range=(candidate.get("expectation") or {}).get("expected_range"),
        auction_amount_yi=amount,
        need_yi=(candidate.get("expectation") or {}).get("auction_amount_min_yi"),
    )
    # AI 原始判定与账户最终可入场必须分开：冰点硬闸不可被 AI 覆盖。
    ice_conflict = bool(can_enter and regime == "ice")
    return {
        "status": "participate" if can_enter else "blocked",
        "can_enter": can_enter,
        "final_can_enter": False if ice_conflict else can_enter,
        "ice_conflict": ice_conflict,
        "view": meta.get("ai_view"),
        "gap_zone": meta.get("zone"),
        "via": meta.get("via"),
    }


def _stock_record(
    candidate: dict[str, Any],
    bucket: str,
    *,
    recommendation_date: str,
    trade_date: str | None,
    regime: str | None,
    quote: dict[str, Any] | None,
    ai_decision: dict[str, Any] | None,
    pool_view: dict[str, Any] | None,
    ledger_item: dict[str, Any] | None,
) -> dict[str, Any]:
    code = str(candidate.get("code") or "").strip()
    mechanical = _mechanical_result(candidate, quote, regime)
    ai = _ai_result(ai_decision, pool_view, candidate, quote, regime)
    stock = (ledger_item or {}).get("stock") or {}
    ledger = (ledger_item or {}).get("ledger") or {}
    entered = bool(ledger_item)
    reasons: list[str] = []
    if quote is None:
        reasons.append("竞价快照缺失")
    if ai_decision is None:
        reasons.append("AI盘后预案缺失，无法由冻结件确认AI口径")
    if entered and stock.get("return_pct") is None:
        reasons.append("账户已记成交但收盘收益缺失")
    if entered and trade_date and str(ledger.get("recommendation_date") or "") != recommendation_date:
        reasons.append("账户账本推荐日与冻结候选不一致")
    if entered and ai_decision is not None and ai.get("final_can_enter") is False:
        reasons.append("账户已记成交，但 AI/9:25 池内裁决未参与")
    if ai.get("ice_conflict"):
        reasons.append("冰点期由AI放行，账户硬闸已阻止入场")
    return {
        "code": code,
        "name": str(candidate.get("name") or ""),
        "bucket": bucket,
        "recommendation_date": recommendation_date,
        "trade_date": trade_date,
        "candidate_source": "wealth_code_candidates_v1",
        "candidate_rank": candidate.get("relay_rank") or candidate.get("watch_rank"),
        "mechanical": mechanical,
        "ai": ai,
        "pool_call": {"present": pool_view is not None, "view": (pool_view or {}).get("view") if isinstance(pool_view, dict) else None},
        "quote": {
            "present": quote is not None,
            "time": (quote or {}).get("time") if isinstance(quote, dict) else None,
            "auction_price": (quote or {}).get("auction_price") if isinstance(quote, dict) else None,
            "auction_amount_yi": (quote or {}).get("auction_amount_yi") if isinstance(quote, dict) else None,
        },
        "execution": {
            "entered": entered,
            "caliber": ledger.get("caliber") if entered else None,
            "gross_return_label": GROSS_RETURN_LABEL,
            "gross_return_pct": stock.get("return_pct") if entered else None,
            "close_price": None,  # 既有账本未冻结收盘价；审计不得由收益倒推价格。
        },
        "integrity": {"complete": not reasons, "issues": reasons},
    }


def build_execution_audit(paths: AuditPaths) -> dict[str, Any]:
    """构建只读执行审计报告；所有缺口保留为缺口，不使用猜测日期或价格。"""
    calendar = completed_trade_days(paths)
    decisions = _index_decisions(paths)
    nav = _index_nav(paths)
    batches: list[dict[str, Any]] = []
    stock_rows: list[dict[str, Any]] = []

    for rec_date, path in _candidate_files(paths):
        doc = _load_json(path, {})
        if not isinstance(doc, dict):
            continue
        trade_date = next_completed_trade_day(calendar, rec_date)
        quotes_path = paths.wealth_cache / f"auction_quotes-{trade_date}.json" if trade_date else None
        quotes_doc = _load_json(quotes_path, {}) if quotes_path else {}
        quotes = quotes_doc if isinstance(quotes_doc, dict) else {}
        views, pool_present = _pool_views(paths, trade_date) if trade_date else ({}, False)
        regime = str((doc.get("environment") or {}).get("regime") or doc.get("regime") or "") or None
        candidates = [("relay", item) for item in (doc.get("relay") or [])]
        candidates += [("watch", item) for item in (doc.get("watch") or [])]
        batch_issues: list[str] = []
        if not trade_date:
            batch_issues.append("执行日无法由已完成交易日历证明")
        if trade_date and not quotes_path.exists():
            batch_issues.append("9:25竞价冻结件缺失")
        if trade_date and not pool_present:
            batch_issues.append("池内裁决冻结件缺失")
        if regime == "ice":
            batch_issues.append("冰点批次：需核验AI是否放行实际入场")
        batch_rows: list[dict[str, Any]] = []
        for bucket, candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            code = str(candidate.get("code") or "").strip()
            row = _stock_record(
                candidate, bucket,
                recommendation_date=rec_date, trade_date=trade_date, regime=regime,
                quote=quotes.get(code) if code else None,
                ai_decision=decisions.get((rec_date, code)),
                pool_view=views.get(code) if code in views else None,
                ledger_item=nav.get((trade_date, code)) if trade_date and code else None,
            )
            batch_rows.append(row)
            stock_rows.append(row)
        entered = [row for row in batch_rows if row["execution"]["entered"]]
        actual_calibers = sorted({str(row["execution"]["caliber"]) for row in entered if row["execution"]["caliber"]})
        batches.append({
            "recommendation_date": rec_date,
            "trade_date": trade_date,
            "candidate_file": path.name,
            "candidate_generated_at": doc.get("generated_at_bj"),
            "regime": regime,
            "source": {
                "candidate_frozen": True,
                "auction_frozen": bool(quotes_path and quotes_path.exists()),
                "ai_plan_count": sum(1 for key in decisions if key[0] == rec_date),
                "pool_call_frozen": pool_present,
                "account_entries": len(entered),
            },
            "caliber": {
                "expected": "ai" if any(key[0] == rec_date for key in decisions) else "unverified_without_frozen_ai_plan",
                "actual": actual_calibers,
                # 历史机械批次与当前 AI 缺预案的回退不是同一事实，只从账户行留存值判定。
                "mechanical_fallback": "mechanical_fallback" in actual_calibers,
            },
            "integrity": {"complete": not batch_issues, "issues": batch_issues},
            "stocks": batch_rows,
        })

    entered_rows = [row for row in stock_rows if row["execution"]["entered"]]
    completed_rows = [row for row in entered_rows if row["execution"]["gross_return_pct"] is not None]
    ice_ai_entries = [row for row in stock_rows if row["ai"].get("ice_conflict")]
    fallback_rows = [row for row in entered_rows if row["execution"]["caliber"] == "mechanical_fallback"]
    ai_rejected_entries = [row for row in entered_rows if row["ai"].get("final_can_enter") is False]
    missing_plan_rows = [row for row in stock_rows if "AI盘后预案缺失" in " ".join(row["integrity"]["issues"])]
    return {
        "schema": "execution_audit_v1",
        "generated_at_bj": datetime.now().astimezone().isoformat(timespec="seconds"),
        "main_metric": {
            "label": GROSS_RETURN_LABEL,
            "formula": "(当日收盘价 - 9:25竞价成交价) / 9:25竞价成交价 * 100",
            "includes_costs": False,
            "includes_slippage": False,
        },
        "calendar": {"source": "local_completed_trade_evidence", "days": calendar, "count": len(calendar)},
        "summary": {
            "batches": len(batches),
            "stocks": len(stock_rows),
            "entered": len(entered_rows),
            "covered_returns": len(completed_rows),
            "mechanical_fallback_entries": len(fallback_rows),
            "ai_rejected_entries": len(ai_rejected_entries),
            "ai_plan_missing_candidates": len(missing_plan_rows),
            "ice_ai_entries": len(ice_ai_entries),
            "incomplete_batches": sum(1 for batch in batches if not batch["integrity"]["complete"]),
        },
        "batches": batches,
    }


def risk_register(report: dict[str, Any]) -> list[dict[str, str]]:
    """报告中的固定风险排序；只报告已证实的缺口，不修改任何策略参数。"""
    risks = [
        {"level": "P0", "code": "calendar_evidence", "message": "执行日必须由实际交易日证据确认；不可只按周末推算。"},
        {"level": "P0", "code": "frozen_evidence", "message": "候选、竞价、AI预案和池内裁决缺任一冻结件时，不得宣称该批可复现。"},
        {"level": "P1", "code": "ice_ai_override", "message": "冰点期 AI 原始放行与账户硬闸结果必须同时冻结，按最终禁止入场结算。"},
        {"level": "P1", "code": "caliber_fallback", "message": "AI预案缺失必须标记机械回退，不能统计为AI空仓。"},
        {"level": "P2", "code": "gross_return_only", "message": "当前账户收益未计手续费和滑点，只能标注为竞价至收盘毛收益。"},
    ]
    return risks
