#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
账户净值账本（equal_weight_next_day_close）——财富密码闭环·忠实移植版

来源：quant-review/scripts/build_account_nav_ledger.py（main 分支）
移植：狸猫猎手/wealth_code/core/nav_ledger.py
说明：纯算法层，仅依赖标准库。从 backtest_payload（已由 pipeline_verify 构建）
      中抽取“可入场 + 隔日 covered”的样本，按交易日等权平均收益，复利滚动出净值曲线。

与原版差异（层级维护）：
- 移除对 build_stock_research_backtest.build_stock_research_backtest_payload 的懒加载
  （该函数在 quant-review 依赖 biying 实时数据；狸猫猎手改为由 pipeline_verify 显式传入 payload）
- 账本读写路径指向狸猫猎手/wealth_code/cache
- 其余核算逻辑（_implied_base_nav / _merge_ledger_rows / 复利）逐字保留
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 模块根（wealth_code/）
ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
LEDGER_PATH = CACHE_DIR / "account_nav_history.jsonl"
DEFAULT_STRATEGY = "equal_weight_next_day_close"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if payload:
        payload += "\n"
    path.write_text(payload, encoding="utf-8")


def _load_existing_ledger() -> list[dict[str, Any]]:
    # 以本模块 cache 账本为准
    return _read_jsonl(LEDGER_PATH)


def _load_backtest_from_market_data(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    backtest = payload.get("stockResearchBacktest") if isinstance(payload, dict) else None
    records = backtest.get("records") if isinstance(backtest, dict) else None
    if not isinstance(backtest, dict) or not isinstance(records, list) or not records:
        raise ValueError(f"invalid stockResearchBacktest in {path}")
    return backtest


def _implied_base_nav(rows: list[dict[str, Any]], *, default_base: float) -> float:
    if not rows:
        return default_base if default_base > 0 else 1.0
    first = rows[0]
    try:
        first_nav = float(first.get("nav") or 0)
        first_pct = float(first.get("avg_return_pct") or 0)
    except Exception:
        return default_base if default_base > 0 else 1.0
    divisor = 1.0 + first_pct / 100.0
    if first_nav > 0 and divisor > 0:
        return round(first_nav / divisor, 6)
    return default_base if default_base > 0 else 1.0


def _ledger_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("trade_date") or "").strip(),
        str(row.get("strategy") or DEFAULT_STRATEGY).strip(),
    )


def _normalize_existing_row(row: dict[str, Any]) -> dict[str, Any] | None:
    trade_date = str(row.get("trade_date") or "").strip()
    if not trade_date:
        return None
    strategy = str(row.get("strategy") or DEFAULT_STRATEGY).strip() or DEFAULT_STRATEGY
    try:
        avg_return_pct = round(float(row.get("avg_return_pct") or 0.0), 4)
    except Exception:
        avg_return_pct = 0.0
    return {
        "trade_date": trade_date,
        "recommendation_date": str(row.get("recommendation_date") or "").strip(),
        "strategy": strategy,
        # 🤖 口径留痕（2026-09-14 用户「全量以 AI 为唯一口径」）：ai / mechanical / mechanical_fallback
        #     --normalize 显式构造 dict，新增字段必须在此透传，否则会被静默丢弃。
        "caliber": str(row.get("caliber") or "").strip(),
        # 📝 行级留痕（2026-09-14 补记 000993 引入）：非自动结算的行必须写明由来
        #    （如「规则修正补记」），否则后续审计无法区分「系统自动成交」与「人工补记」。
        "note": str(row.get("note") or "").strip(),
        "stock_count": int(float(row.get("stock_count") or 0)),
        "avg_return_pct": avg_return_pct,
        "nav": 0.0,
        "codes": [str(x).strip() for x in (row.get("codes") or []) if str(x).strip()],
        "names": [str(x).strip() for x in (row.get("names") or []) if str(x).strip()],
        "stocks": [
            {
                "code": str(s.get("code") or "").strip(),
                "name": str(s.get("name") or "").strip(),
                "return_pct": round(float(s.get("return_pct") or 0.0), 4),
            }
            for s in (row.get("stocks") or [])
            if isinstance(s, dict)
        ],
    }


def _build_incremental_rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = payload.get("records") if isinstance(payload, dict) else []
    grouped: dict[str, dict[str, Any]] = {}

    for row in records if isinstance(records, list) else []:
        if not isinstance(row, dict):
            continue
        performance = row.get("performance") if isinstance(row.get("performance"), dict) else {}
        open_check = performance.get("open_check") if isinstance(performance.get("open_check"), dict) else {}
        next_day = performance.get("next_day") if isinstance(performance.get("next_day"), dict) else {}
        if not bool(open_check.get("can_enter")):
            continue
        if str(next_day.get("status") or "") != "covered":
            continue
        trade_date = str(next_day.get("entry_date") or next_day.get("exit_date") or "").strip()
        if not trade_date:
            continue
        item = grouped.setdefault(
            trade_date,
            {
                "trade_date": trade_date,
                "recommendation_date": str(row.get("date10") or "").strip(),
                "strategy": DEFAULT_STRATEGY,
                # 🤖 口径留痕（2026-09-14）：本批结算按 AI 口径还是机械口径
                "caliber": str(open_check.get("caliber") or "").strip(),
                "returns": [],
                "codes": [],
                "names": [],
                "stocks": [],  # per-stock 明细（2026-08-14：交易战绩 tab 用）
            },
        )
        try:
            ret = float(next_day.get("return_pct"))
        except Exception:
            continue
        item["returns"].append(ret)
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if code:
            item["codes"].append(code)
        if name:
            item["names"].append(name)
        item["stocks"].append(
            {"code": code, "name": name, "return_pct": round(ret, 4)}
        )

    out: list[dict[str, Any]] = []
    for trade_date in sorted(grouped.keys()):
        item = grouped[trade_date]
        returns = item.get("returns") if isinstance(item.get("returns"), list) else []
        if not returns:
            continue
        avg_return_pct = round(sum(float(x) for x in returns) / len(returns), 4)
        out.append(
            {
                "trade_date": trade_date,
                "recommendation_date": item.get("recommendation_date") or "",
                "strategy": DEFAULT_STRATEGY,
                "caliber": item.get("caliber") or "",
                "stock_count": len(returns),
                "avg_return_pct": avg_return_pct,
                "nav": 0.0,
                "codes": item.get("codes") or [],
                "names": item.get("names") or [],
                "stocks": item.get("stocks") or [],
            }
        )
    return out


def _merge_ledger_rows(
    existing_rows: list[dict[str, Any]],
    incremental_rows: list[dict[str, Any]],
    *,
    base_nav: float,
) -> list[dict[str, Any]]:
    normalized_existing = [row for row in (_normalize_existing_row(row) for row in existing_rows) if row]
    normalized_incremental = [row for row in (_normalize_existing_row(row) for row in incremental_rows) if row]
    implied_base = _implied_base_nav(sorted(normalized_existing, key=_ledger_sort_key), default_base=base_nav)

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in normalized_existing:
        by_key[_ledger_sort_key(row)] = row
    for row in normalized_incremental:
        by_key[_ledger_sort_key(row)] = row

    nav = implied_base
    out: list[dict[str, Any]] = []
    for row in sorted(by_key.values(), key=_ledger_sort_key):
        avg_return_pct = float(row.get("avg_return_pct") or 0.0)
        nav = round(nav * (1 + avg_return_pct / 100.0), 6)
        row = dict(row)
        row["nav"] = nav
        out.append(row)
    return out


def build_account_nav_ledger(
    *,
    base_nav: float = 1.0,
    backtest_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    构建净值账本。

    入参：
    - base_nav: 起始净值（默认 1.0）
    - backtest_payload: 由 pipeline_verify 构建的回测 payload（必填，不再懒加载 biying 数据）
    """
    payload = backtest_payload
    if payload is None:
        raise ValueError("backtest_payload 为必填（狸猫猎手不再依赖 quant-review 的 biying 数据链）")
    existing_rows = _load_existing_ledger()
    incremental_rows = _build_incremental_rows_from_payload(payload if isinstance(payload, dict) else {})
    return _merge_ledger_rows(existing_rows, incremental_rows, base_nav=base_nav)


def sync_account_nav_ledger(
    *,
    base_nav: float = 1.0,
    backtest_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows = build_account_nav_ledger(base_nav=base_nav, backtest_payload=backtest_payload)
    _write_jsonl(LEDGER_PATH, rows)
    return rows


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--market-data", type=Path)
    args = parser.parse_args()
    backtest_payload = _load_backtest_from_market_data(args.market_data) if args.market_data else None
    rows = sync_account_nav_ledger(backtest_payload=backtest_payload)
    print(str(LEDGER_PATH))
    print(f"rows={len(rows)}")


if __name__ == "__main__":
    main()
