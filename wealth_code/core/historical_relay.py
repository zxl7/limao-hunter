#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史连板晋级因子：历史后验 + 当前接力情绪的小幅融合。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ENABLED = True
HISTORY_BONUS_MAX = 7.0


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _boards(row: dict[str, Any]) -> int:
    return int(_num(row.get("boards"), 0))


def _code(row: dict[str, Any]) -> str:
    return str(row.get("code") or row.get("dm") or "").strip()


def _load_snapshots(history_dir: Path, trade_date: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for p in sorted(history_dir.glob("zt_*.json")):
        date = p.stem[3:]
        if trade_date and date >= trade_date:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("zt_list"), list):
                out.append((date, data))
        except (OSError, ValueError, TypeError):
            continue
    return out


def _emotion_adjust(env: dict[str, Any] | None) -> float:
    """当前接力环境校准，最大 +/-12 个百分点，避免覆盖历史事实。"""
    env = env or {}
    promote = _num(env.get("promote_rate"), 35.0)
    lb_promote = _num(env.get("lb_promote_rate"), 25.0)
    sentiment = _num(env.get("sentiment_score"), 5.0)
    delta = (promote - 35.0) * 0.0012 + (lb_promote - 25.0) * 0.0012 + (sentiment - 5.0) * 0.012
    return max(-0.12, min(0.12, delta))


def _next_day_map(snapshots: list[tuple[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    dates = [d for d, _ in snapshots]
    by_date = {d: s for d, s in snapshots}
    result = {}
    for date in dates[:-1]:
        next_date = next((d for d in dates if d > date), None)
        if next_date:
            result[date] = { _code(r): r for r in (by_date[next_date].get("zt_list") or []) if _code(r) }
    return result


def _calc_for_code(code: str, current_boards: int, snapshots: list[tuple[str, dict[str, Any]]], env: dict[str, Any] | None) -> dict[str, Any]:
    next_maps = _next_day_map(snapshots)
    code_samples: list[bool] = []
    level_samples: list[bool] = []
    for date, snap in snapshots:
        rows = snap.get("zt_list") or []
        same_level = [_code(r) for r in rows if _boards(r) == current_boards and _code(r)]
        if not same_level or date not in next_maps:
            continue
        next_rows = next_maps[date]
        # 市场同高度先验：下一交易日仍在涨停且板数抬升，才算晋级。
        level_samples.extend(_boards(next_rows.get(c, {})) > current_boards for c in same_level)
        if code in same_level:
            code_samples.append(_boards(next_rows.get(code, {})) > current_boards)

    if not level_samples:
        return {"prob": None, "samples": 0, "stock_samples": len(code_samples), "bonus": 0.0, "note": "历史同高度无后验样本"}
    level_p = sum(level_samples) / len(level_samples)
    stock_p = sum(code_samples) / len(code_samples) if code_samples else level_p
    # 个股历史样本越多权重越高，最多 70%；单次历史不会主导结果。
    stock_weight = min(0.70, 0.25 * len(code_samples)) if code_samples else 0.0
    emotion_delta = _emotion_adjust(env)
    prob = max(0.03, min(0.97, stock_p * stock_weight + level_p * (1 - stock_weight) + emotion_delta))
    bonus = max(-HISTORY_BONUS_MAX, min(HISTORY_BONUS_MAX, (prob - 0.35) * 20.0))
    return {
        "prob": round(prob, 4), "samples": len(level_samples), "stock_samples": len(code_samples),
        "level_rate": round(level_p, 4), "stock_rate": round(stock_p, 4), "bonus": round(bonus, 2),
        "note": f"同高度{len(level_samples)}次·个股{len(code_samples)}次·情绪校准{emotion_delta * 100:+.1f}pp",
    }


def annotate_historical_relay(candidates: list[dict[str, Any]], history_dir: Path, trade_date: str, env: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshots = _load_snapshots(history_dir, trade_date)
    with_history = 0
    for cand in candidates or []:
        code = _code(cand)
        boards = int(_num(cand.get("boards"), 0))
        result = _calc_for_code(code, boards, snapshots, env) if code and boards > 0 else {"prob": None, "samples": 0, "stock_samples": 0, "bonus": 0.0, "note": "缺少代码或连板数"}
        cand["history_relay_prob"] = result.get("prob")
        cand["history_relay_samples"] = result.get("samples", 0)
        cand["history_relay_stock_samples"] = result.get("stock_samples", 0)
        cand["history_relay_factor"] = result.get("bonus", 0.0)
        cand["history_relay_note"] = result.get("note", "")
        if result.get("prob") is not None:
            with_history += 1
    return {"enabled": ENABLED, "history_days": len(snapshots), "candidates_with_history": with_history}
