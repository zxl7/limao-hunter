#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前复权日线缓存（kline_store）—— 为「判断轨迹 → 真实可变现收益」提供价格底座。

## 为什么不直接用 `cache/market_daily/market_daily.duckdb`

2026-10-23 实测该库两个硬伤，拿来算钱会算错：

1. **只覆盖 2026-10-07~10-20 共 10 个交易日**（`DEFAULT_KEEP_DAYS=20`，且只导入过一次），
   而判断轨迹要回溯到 08-11，覆盖不到。
2. **日期整体偏移一天**：库里 `trade_date=2026-10-18`（周日）那行 OHLC 与腾讯
   `2026-10-19` 完全一致，`2026-10-15` 那行等于腾讯 `2026-10-16`。用它做 T+1 对齐全体错位一格。

## 数据源：复用 `common.tencent_kline`（⛔ 不另起第二套腾讯请求逻辑）

腾讯 fqkline 的 host 会被单方面停用（2026-09-09 501 / 2026-10-09 反向），
`common.tencent_kline` 已内建 host 按序兜底 + 命中即提队首，本模块只在其上补两件事：
**磁盘缓存**与**并发预热**。语义仍是「前复权、按日期升序、失败返回空」。

## 契约

- 一个代码一个文件：`cache/kline_qfq/<code>.json`。
- **增量**：缓存最末一根 >= 目标 end 的上一交易日 → 直接读盘，不触网；否则重拉（区间短，重拉比合并安全）。
- **⛔ 不编造**：拉取失败返回空列表 / 退回旧缓存，调用方必须把收益留空，不得按 0 或昨收顶替。
- 不做任何买卖判断，不写策略结论。

```bash
python3 wealth_code/scripts/kline_store.py fetch 603278 002076   # 预热
python3 wealth_code/scripts/kline_store.py show 603278
```
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "cache" / "kline_qfq"

SCHEMA = "kline_qfq_v1"
BARS = 320          # 腾讯「最近 N 根」上限足够覆盖 08-01 至今（约 55 个交易日）
_MAX_WORKERS = 6


def _ensure_root_on_path() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _fetch(code: str) -> list[dict[str, Any]]:
    """走公共腾讯模块（自带 host 兜底）。失败返回 []。"""
    _ensure_root_on_path()
    try:
        from common.tencent_kline import fetch_kline_ohlc  # noqa: PLC0415 — 惰性导入，避免影响纯离线场景

        raw = fetch_kline_ohlc(str(code).strip().zfill(6), n=BARS)
    except Exception:  # noqa: BLE001 — 网络/导入异常一律按「无数据」处理，由调用方留空
        return []
    out: list[dict[str, Any]] = []
    for b in raw or []:
        try:
            d = str(b.get("date") or "")
            c = float(b.get("close"))
        except (TypeError, ValueError):
            continue
        if not d or c <= 0:
            continue
        def _f(key: str) -> float | None:
            v = b.get(key)
            try:
                return round(float(v), 2) if v is not None else None
            except (TypeError, ValueError):
                return None
        out.append({"date": d, "open": _f("open"), "close": round(c, 2),
                    "high": _f("high"), "low": _f("low"), "vol": _f("volume")})
    out.sort(key=lambda x: x["date"])
    return out


def _path(code: str) -> Path:
    return CACHE_DIR / f"{str(code).strip().zfill(6)}.json"


def get_daily(code: str, *, end: str | None = None, refresh: bool = False,
              allow_fetch: bool = True) -> list[dict[str, Any]]:
    """读缓存（必要时重拉）。`end` 只用于判断缓存是否够新。

    `allow_fetch=False` = **只读盘**：老票的历史行情早已定型，每天重拉 1300 只会把
    盘后流程拖成两分钟；只有「最近还在涨停」的票才需要补最新几根。
    """
    end = end or _date.today().isoformat()
    p = _path(code)
    cached: dict[str, Any] | None = None
    if p.exists() and not refresh:
        try:
            cached = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏文件当没缓存
            cached = None
    if cached:
        bars = cached.get("bars") or []
        if bars and bars[-1]["date"] >= end:
            return bars
        if not allow_fetch:
            # 老票：历史已定型，最后几根够用到它最后一次涨停之后即可，不追最新
            return bars
    # ⚠️ 这里**不能**用「最后一根 >= 昨天 就算够新」的宽容判断：盘中抓到的当日半根 K
    #    会被缓存下来，次日就会被当成「已含最新」，行情从此永久停在前一天。
    #    宁可不允许 fetch 的票读到旧数据（它们的信号早已结算完，用不到新价格）。
    bars = _fetch(code)
    if not bars:
        return (cached or {}).get("bars") or []

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": SCHEMA, "code": str(code).zfill(6),
                             "fetched_at": _date.today().isoformat(), "bars": bars},
                            ensure_ascii=False), encoding="utf-8")
    return bars


def _prev_day(d: str) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(d) - timedelta(days=1)).isoformat()


def get_many(codes: Iterable[str], *, end: str | None = None, refresh: bool = False,
             workers: int = _MAX_WORKERS,
             allow_fetch_codes: Iterable[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    """批量。`allow_fetch_codes` 给定时，**只有这些代码允许补最新行情**（其余只读缓存）。

    用途：盘后每天都要更新生涯库，但真正会变的只有「最近还在涨停」的那几十只；
    不设这个闸，1300 只全量重拉会把盘后流程拖成两分钟。
    """
    uniq = list(dict.fromkeys(str(c).strip().zfill(6) for c in codes if str(c).strip()))
    hot = {str(c).strip().zfill(6) for c in (allow_fetch_codes or [])} or None
    out: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, _MAX_WORKERS))) as pool:
        futs = {
            code: pool.submit(get_daily, code, end=end, refresh=refresh,
                              allow_fetch=(hot is None or code in hot))
            for code in uniq
        }
        for code, fut in futs.items():
            try:
                out[code] = fut.result()
            except Exception:  # noqa: BLE001 — 单只失败不影响整批
                out[code] = []
    return out


# ── 按日期定位（找不到就 None，不猜）──────────────────────────────
def bar_at(bars: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    for b in bars:
        if b["date"] == day:
            return b
    return None


def next_bar(bars: list[dict[str, Any]], day: str, offset: int = 1) -> dict[str, Any] | None:
    """严格晚于 `day` 的第 `offset` 根。"""
    for i, b in enumerate(bars):
        if b["date"] > day:
            j = i + offset - 1
            return bars[j] if 0 <= j < len(bars) else None
    return None


def bar_or_next(bars: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    """当日有就用当日，没有就用其后第一根（停牌/缺行兜底，调用方需自己判是否同日）。"""
    return bar_at(bars, day) or next_bar(bars, _prev_day(day), 1)
