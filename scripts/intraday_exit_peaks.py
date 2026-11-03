#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中卖出侧证据采集：记录封单与板块涨停家数的当日峰值。

只做观测，不产生卖出信号、不改候选排序、不发卡。该文件为后续验证
「封单在减 / 板块家数腰斩」提供可审计的峰值基线。

数据边界：
- 个股封单来自 feedback_matrix.quotes，仅覆盖昨日涨停池；seal_yi=None 表示该次
  行情已观察到但当前未确认封住，不能把 None 编成 0 亿封单。
- 板块家数来自 latest.top5，仅覆盖当轮 Top5；未进入 Top5 不等于板块没有涨停。
- 同日状态写 cache/intraday_exit_peaks_<YYYYMMDD>.json；跨日绝不拼接。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
SNAP = CACHE / "pulse_snapshots"


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def state_path(date: str, *, cache_dir: Path = CACHE) -> Path:
    """按业务交易日分文件，避免隔日峰值污染。"""
    return cache_dir / f"intraday_exit_peaks_{date.replace('-', '')}.json"


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def update_peaks(
    *, latest: dict[str, Any], feedback: dict[str, Any], old: dict[str, Any] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """将同刻快照并入当日峰值状态（纯函数）。

    返回的 ``current_*`` 是最新一次已观察值； ``*_peak`` 是当日最大值及其时刻。
    缺字段保持缺失，绝不以 0 替代。仅当输入日期一致且 feedback 无 error 时才入库。
    """
    old = old or {}
    source_ts = str(latest.get("ts") or "")
    date = source_ts[:10]
    at = observed_at or source_ts
    if not date or not at or feedback.get("error"):
        return old
    feedback_date = str(feedback.get("date") or "")[:10]
    if feedback_date and feedback_date != date:
        return old
    if str(old.get("date") or "") not in ("", date):
        old = {}

    out: dict[str, Any] = {
        "schema": "intraday_exit_peaks_v1",
        "date": date,
        "updated_at": at,
        "stock_scope": "feedback_matrix.quotes（昨日涨停池）",
        "sector_scope": "latest.top5（当轮Top5）",
        "stocks": dict(old.get("stocks") or {}),
        "sectors": dict(old.get("sectors") or {}),
    }

    quotes = feedback.get("quotes") or {}
    if isinstance(quotes, dict):
        for code, quote in quotes.items():
            if not isinstance(quote, dict):
                continue
            code = str(code)[:6]
            if not code:
                continue
            seal = _num(quote.get("seal_yi"))
            rec = dict(out["stocks"].get(code) or {})
            rec.update({
                "name": str(quote.get("name") or rec.get("name") or code),
                "current_seal_yi": round(seal, 2) if seal is not None else None,
                "current_sealed": bool(seal is not None and seal > 0),
                "current_observed_at": at,
                "quote_at": quote.get("quote_at"),
            })
            peak = _num(rec.get("seal_yi_peak"))
            if seal is not None and seal > 0 and (peak is None or seal > peak):
                rec["seal_yi_peak"] = round(seal, 2)
                rec["seal_yi_peak_at"] = at
            out["stocks"][code] = rec

    top5 = latest.get("top5") or []
    if isinstance(top5, list):
        for sector in top5:
            if not isinstance(sector, dict):
                continue
            name = str(sector.get("name") or "").strip()
            zt = _num(sector.get("zt_count"))
            if not name or zt is None:
                continue
            zt_int = max(0, int(zt))
            rec = dict(out["sectors"].get(name) or {})
            rec.update({"current_zt_count": zt_int, "current_observed_at": at})
            peak = _num(rec.get("zt_count_peak"))
            if peak is None or zt_int > peak:
                rec["zt_count_peak"] = zt_int
                rec["zt_count_peak_at"] = at
            out["sectors"][name] = rec
    return out


def collect(*, cache_dir: Path = CACHE) -> tuple[bool, str, dict[str, Any] | None]:
    """读取本轮产物、写峰值状态。失败仅返回状态，不能影响主盘中链路。"""
    latest = _load(cache_dir / "pulse_snapshots" / "latest.json")
    feedback = _load(cache_dir / "feedback_matrix.json")
    date = str(latest.get("ts") or "")[:10]
    if not date:
        return False, "latest缺少业务时刻", None
    if feedback.get("error"):
        return False, f"feedback不可用:{feedback.get('error')}", None
    path = state_path(date, cache_dir=cache_dir)
    result = update_peaks(latest=latest, feedback=feedback, old=_load(path))
    if not result or result.get("date") != date:
        return False, "数据日期不一致或无可采集字段", None
    _atomic_write(path, result)
    return True, str(path), result


def main() -> int:
    ok, message, state = collect()
    if not ok:
        print(f"[intraday_exit_peaks] 跳过: {message}")
        return 0  # 旁路采集，不能阻断实时数据与推送
    print(f"[intraday_exit_peaks] ✅ stocks={len(state.get('stocks') or {})} sectors={len(state.get('sectors') or {})} → {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
