"""近月 Wilder RSI 研究证据；只读、无交易裁决，不追加历史库。"""
from __future__ import annotations

from datetime import date
import math
from numbers import Real
from typing import Any

PERIODS = (6, 12, 24)


def rsi_wilder(closes, period: int) -> list[float | None]:
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError("RSI period must be a positive integer")
    values = [None] * len(closes)
    previous = None
    count = 0
    gain_avg = loss_avg = 0.0
    for index, close in enumerate(closes):
        if isinstance(close, bool) or not isinstance(close, Real) or not math.isfinite(close):
            previous = None
            count = 0
            gain_avg = loss_avg = 0.0
            continue
        if previous is None:
            previous = close
            continue
        change = close - previous
        previous = close
        if not math.isfinite(change):
            count = 0
            gain_avg = loss_avg = 0.0
            continue
        gain, loss = max(change, 0), max(-change, 0)
        if count < period:
            gain_avg += gain / period
            loss_avg += loss / period
            count += 1
            if count < period:
                continue
        else:
            gain_avg = gain_avg * ((period - 1) / period) + gain / period
            loss_avg = loss_avg * ((period - 1) / period) + loss / period
        values[index] = (50.0 if gain_avg == loss_avg == 0 else
                         100.0 if loss_avg == 0 else
                         0.0 if gain_avg == 0 else
                         100 - 100 / (1 + gain_avg / loss_avg))
    return values


def build_evidence(code: str, bars: list[dict], *, asof: str) -> dict[str, Any]:
    """asof 为明确的收盘业务日；不证明数据在历史决策时已经被采集。"""
    cutoff = date.fromisoformat(asof).isoformat()
    rows = sorted((r for r in bars if str(r['trade_date']) <= cutoff),
                  key=lambda r: str(r['trade_date']))[-20:]
    dates = [str(r['trade_date']) for r in rows]
    invalid_rows = len(set(dates)) != len(dates) or any(r.get('code') != code for r in rows)
    closes = [r.get('close') for r in rows]
    if invalid_rows:
        closes = [None] * len(rows)
    readings = {}
    for period in PERIODS:
        series = rsi_wilder(closes, period)
        value = series[-1] if series else None
        previous = series[-2] if len(series) > 1 else None
        readings[str(period)] = {
            'period': period, 'value': value, 'previous': previous,
            'change': value - previous if value is not None and previous is not None else None,
            'status': 'ready' if value is not None else 'insufficient',
            'series': series,
        }
    latest = dates[-1] if dates else None
    status = ('invalid' if invalid_rows else 'missing' if not rows else
              'stale' if latest != cutoff else 'ready' if readings['6']['value'] is not None else 'insufficient')
    return {
        'schema': 'rsi_evidence_v1', 'code': code, 'asof': cutoff, 'bar_date': latest,
        'method': 'wilder', 'adjustment': 'none', 'sample_count': len(rows),
        'source': sorted({str(r.get('source') or 'unknown') for r in rows}),
        'dates': dates, 'quality': status, 'readings': readings,
        'corporate_actions': 'unchecked', 'historical_availability': 'not_verified',
        'warnings': ['近20日重新预热，不等于全历史递推值',
                     '未复权且未核验公司行为，跳变不能直接解释为动能变化',
                     '按交易日截断，不证明历史决策时已知；禁止回填冻结裁决'],
        'usage': 'research_only',
    }


def load_evidence(code: str, *, asof: str, db_path=None) -> dict[str, Any]:
    from common.market_daily_store import recent_bars
    try:
        bars = recent_bars(code, asof=asof, limit=20, db_path=db_path)
    except (FileNotFoundError, RuntimeError):
        bars = []
    return build_evidence(code, bars, asof=asof)


def annotate_candidates(rows: list[dict], *, asof: str, db_path=None) -> None:
    """仅追加研究字段；异常留缺口，保留原候选及顺序。"""
    for row in rows:
        try:
            row['rsi_evidence'] = load_evidence(str(row.get('code') or ''), asof=asof, db_path=db_path)
        except Exception:
            row['rsi_evidence'] = {**build_evidence(str(row.get('code') or ''), [], asof=asof),
                                   'quality': 'unavailable'}
