#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
账户策略指标（胜率 / 平均收益 / 按开盘状态拆分）——财富密码闭环·忠实移植版

来源：quant-review/scripts/build_account_strategy_metrics.py（main 分支）
移植：狸猫猎手/wealth_code/core/strategy_metrics.py
说明：纯算法层，仅依赖标准库。消费 backtest_payload，按推荐日聚合并对
      next_day / hold_2d / hold_3d 三条持有周期分别汇总覆盖度、胜率、收益。

与原版差异（层级维护）：
- 移除对 build_stock_research_backtest.build_stock_research_backtest_payload 的懒加载
- 指标读写路径指向狸猫猎手/wealth_code/cache
- 其余汇总逻辑（_summarize_scope / _pct / _avg）逐字保留
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
METRICS_PATH = CACHE_DIR / "account_strategy_metrics.json"

STRATEGY_DEFS = (
    ("next_day", "尾盘收益"),
    ("hold_2d", "2日收益"),
    ("hold_3d", "3日收益"),
)


def _load_backtest_from_market_data(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    backtest = payload.get("stockResearchBacktest") if isinstance(payload, dict) else None
    records = backtest.get("records") if isinstance(backtest, dict) else None
    if not isinstance(backtest, dict) or not isinstance(records, list) or not records:
        raise ValueError(f"invalid stockResearchBacktest in {path}")
    return backtest


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100.0 / denominator, 1)


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _summarize_scope(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    covered_rows = [r for r in rows if str(((r.get("performance") or {}).get(key) or {}).get("status") or "") == "covered"]
    pending_rows = [r for r in rows if str(((r.get("performance") or {}).get(key) or {}).get("status") or "") == "pending"]
    skipped_rows = [r for r in rows if str(((r.get("performance") or {}).get(key) or {}).get("status") or "") == "skipped"]
    missing_rows = [r for r in rows if str(((r.get("performance") or {}).get(key) or {}).get("status") or "") == "missing"]
    returns = [float(((r.get("performance") or {}).get(key) or {}).get("return_pct") or 0.0) for r in covered_rows]
    wins = [val for val in returns if val > 0]
    flats = [val for val in returns if val == 0]
    losses = [val for val in returns if val < 0]

    by_open_status: dict[str, Any] = {}
    for open_status in ("super", "expected", "reject"):
        status_rows = [
            r for r in covered_rows
            if str((((r.get("performance") or {}).get("open_check") or {}).get("status") or "")) == open_status
        ]
        status_returns = [float(((r.get("performance") or {}).get(key) or {}).get("return_pct") or 0.0) for r in status_rows]
        by_open_status[open_status] = {
            "covered": len(status_rows),
            "win_rate": _pct(sum(1 for value in status_returns if value > 0), len(status_rows)),
            "avg_return": _avg(status_returns),
        }

    return {
        "covered": len(covered_rows),
        "eligible": len(rows),
        "coverage": _pct(len(covered_rows), len(rows)),
        "pending": len(pending_rows),
        "missing": len(missing_rows),
        "skipped": len(skipped_rows),
        "win_count": len(wins),
        "flat_count": len(flats),
        "loss_count": len(losses),
        "win_rate": _pct(len(wins), len(covered_rows)),
        "avg_return": _avg(returns),
        "avg_win_return": _avg(wins),
        "avg_loss_return": _avg(losses),
        "by_open_status": by_open_status,
    }


def build_account_strategy_metrics(
    *,
    backtest_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    构建策略指标汇总。

    入参：
    - backtest_payload: 由 pipeline_verify 构建的回测 payload（必填）
    """
    payload = backtest_payload
    if payload is None:
        raise ValueError("backtest_payload 为必填（狸猫猎手不再依赖 quant-review 的 biying 数据链）")
    records = payload.get("records") if isinstance(payload, dict) else []
    valid_records = [row for row in records if isinstance(row, dict)]
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in valid_records:
        recommendation_date = str(row.get("date10") or "").strip()
        if not recommendation_date:
            continue
        by_date.setdefault(recommendation_date, []).append(row)

    daily_records: list[dict[str, Any]] = []
    for recommendation_date in sorted(by_date.keys()):
        rows = by_date[recommendation_date]
        tradable_rows = [
            row for row in rows
            if bool((((row.get("performance") or {}).get("open_check") or {}).get("can_enter")))
        ]
        metrics: dict[str, Any] = {}
        for key, label in STRATEGY_DEFS:
            metrics[key] = {
                "key": key,
                "label": label,
                "tradable": _summarize_scope(tradable_rows, key),
                "all": _summarize_scope(rows, key),
            }
        daily_records.append(
            {
                "recommendation_date": recommendation_date,
                "trade_date": str(rows[0].get("trade_date10") or "").strip() if rows else "",
                "sample_count": len(rows),
                "tradable_count": len(tradable_rows),
                "metrics": metrics,
            }
        )

    latest = daily_records[-1] if daily_records else {}
    return {
        "schema": "account_strategy_metrics_v1",
        "generated_at_bj": str(((payload.get("meta") or {}).get("generated_at_bj")) or ""),
        "records": daily_records,
        "latest_recommendation_date": str(latest.get("recommendation_date") or ""),
        "latest_trade_date": str(latest.get("trade_date") or ""),
    }


def _read_existing_metrics_records() -> dict[str, dict[str, Any]]:
    """读取现有指标文件，按 recommendation_date 建索引（用于合并累加，避免覆盖历史）。"""
    if not METRICS_PATH.exists():
        return {}
    try:
        data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in (data.get("records") or []):
        if isinstance(r, dict) and r.get("recommendation_date"):
            out[str(r["recommendation_date"])] = r
    return out


def sync_account_strategy_metrics(
    *,
    backtest_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    构建并落盘策略指标。

    合并语义（2026-08-13 修复根因）：不再全量覆盖，而是按 recommendation_date
    与现有指标文件合并累加——同一推荐日覆盖、不同推荐日追加。与 nav_ledger
    的合并口径一致，防止「每日 verify 只留最新一天、丢失历史盈利/亏损日」的 bug。
    """
    payload = build_account_strategy_metrics(backtest_payload=backtest_payload)
    existing = _read_existing_metrics_records()
    for rec in payload.get("records", []):
        rdate = str(rec.get("recommendation_date") or "").strip()
        if rdate:
            existing[rdate] = rec  # 同推荐日覆盖，异推荐日追加
    merged_records = [existing[k] for k in sorted(existing.keys())]
    latest = merged_records[-1] if merged_records else {}
    merged = {
        "schema": "account_strategy_metrics_v1",
        "generated_at_bj": str(((payload.get("meta") or {}).get("generated_at_bj")) or ""),
        "records": merged_records,
        "latest_recommendation_date": str(latest.get("recommendation_date") or ""),
        "latest_trade_date": str(latest.get("trade_date") or ""),
    }
    _write_json(METRICS_PATH, merged)
    return merged


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--market-data", type=Path)
    args = parser.parse_args()
    backtest_payload = _load_backtest_from_market_data(args.market_data) if args.market_data else None
    payload = sync_account_strategy_metrics(backtest_payload=backtest_payload)
    print(str(METRICS_PATH))
    print(f"rows={len(payload.get('records') or [])}")


if __name__ == "__main__":
    main()
