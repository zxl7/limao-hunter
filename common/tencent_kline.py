"""腾讯日K线公共拉取（统一 ifzq.gtimg.cn 数据源）。

2026-08-17 架构重构 P1：消除 ma5_check.fetch_kline 与 feedback_matrix.fetch_ma510
的重复实现（同源腾讯前复权日K，仅根数/用途不同）。
- ma5_check：8 根 → 算 MA5（持仓/观察池五日线纪律）
- feedback_matrix：12 根 → 算 MA5/MA10（核按钮真破位判定）
调用方各自保留语义封装，网络层统一走本模块 + common.http_client。
"""
from __future__ import annotations

from typing import Any

from common.http_client import http_get

# 2026-09-09 修：原 web.ifzq.gtimg.cn 现返回 HTTP 501（腾讯已禁该 host）；
# 实测 ifzq.gtimg.cn 对个股/指数日K 均 200（锚：002702/sh000002 各 9 行）
KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={sec},day,,,{n},qfq"


def tk_market(code: str) -> str:
    """腾讯市场前缀：沪 6/9→sh，深 0/3→sz，北 8/4/920→bj"""
    if code[0] in ("6", "9"):
        return "sh"
    if code.startswith(("8", "4", "920")):
        return "bj"
    return "sz"


def fetch_kline(code: str, n: int = 8, timeout: int = 10) -> list[tuple[str, float]]:
    """拉最近 n 根腾讯日K（前复权），返回 [(date, close), ...] 正序。
    失败/异常返回空列表（调用方防御，不抛错）。"""
    sec = f"{tk_market(code)}{code}"
    url = KLINE_URL.format(sec=sec, n=n)
    try:
        r = http_get(url, t=timeout, headers={"User-Agent": "Mozilla/5.0"}, retries=1)
    except Exception:
        return []
    if not isinstance(r, dict):
        return []
    d = (r.get("data") or {}).get(sec) or {}
    kl = d.get("qfqday") or d.get("day") or []
    try:
        return [(x[0], float(x[2])) for x in kl]  # [0]=日期 [2]=收盘
    except Exception:
        return []


def fetch_ma(code: str, n: int = 5, bars: int | None = None) -> float | None:
    """拉最近 bars 根 K 算 n 日均线（默认 MA5）。数据不足返回 None。"""
    bars = bars or max(n + 3, 8)
    closes = [c for _, c in fetch_kline(code, n=bars)]
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 3)


def fetch_ma510(code: str) -> tuple[float, float] | None:
    """腾讯日K算 MA5/MA10（核按钮真破位判定专用）。数据不足返回 None。"""
    closes = [c for _, c in fetch_kline(code, n=12)]
    if len(closes) < 10:
        return None
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    return round(ma5, 3), round(ma10, 3)
