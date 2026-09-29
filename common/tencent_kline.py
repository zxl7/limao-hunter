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
    """腾讯市场前缀：沪 6/9→sh，深 0/3→sz，北 8/4/920→bj

    ⚠️ 2026-09-22 修死代码：原实现把 `startswith(("8","4","920"))` 放在
       `code[0] in ("6","9") → "sh"` **之后** —— 北交所 `920xxx` 首位是 "9"，
       于是被前面的沪市分支捕获，**永远返回 "sh"**，该分支从未生效。
       后果：`920xxx` 请求 `sh920xxx` → 0 行 → 日K静默为空（实测 920298/920427/920478/920526
       四只全部取不到），连带 ma5_check / feedback_matrix 核按钮破位 / decision_audit /
       reflow_backtest 的该票口径静默变 None。⇒ 北交所判定**必须先于**单位数分支。
    """
    if code.startswith("920"):          # 北交所新代码段：须先判，否则被 "9"→sh 遮蔽
        return "bj"
    if code[0] in ("6", "9"):
        return "sh"
    if code.startswith(("8", "4")):
        return "bj"
    return "sz"


def _raw_klines(code: str, n: int = 8, timeout: int = 10) -> list[list]:
    """拉最近 n 根腾讯日K**原始行**（前复权）。失败/异常一律返回空列表（调用方防御，不抛错）。

    行格式（腾讯 fqkline 标准）：[日期, 开, 收, 高, 低, 量]；后 5 项为字符串，可能为 ""。
    2026-09-18 抽出：`fetch_kline`（只要收盘）与 `fetch_kline_ohlc`（要开盘）共用同一 host/URL，
    避免为「多周期收益口径」新开第二个数据源（同源原则）。
    """
    sec = f"{tk_market(code)}{code}"
    url = KLINE_URL.format(sec=sec, n=n)
    try:
        r = http_get(url, t=timeout, headers={"User-Agent": "Mozilla/5.0"}, retries=1)
        if not isinstance(r, dict):
            return []
        d = (r.get("data") or {}).get(sec) or {}
        if not isinstance(d, dict):
            return []
        kl = d.get("qfqday") or d.get("day") or []
        return kl if isinstance(kl, list) else []
    except Exception:
        return []


def fetch_kline(code: str, n: int = 8, timeout: int = 10) -> list[tuple[str, float]]:
    """拉最近 n 根腾讯日K（前复权），返回 [(date, close), ...] 正序。
    失败/异常返回空列表（调用方防御，不抛错）。"""
    try:
        return [(x[0], float(x[2])) for x in _raw_klines(code, n, timeout)]  # [0]=日期 [2]=收盘
    except Exception:
        return []


def fetch_kline_ohlc(code: str, n: int = 8, timeout: int = 10) -> list[dict]:
    """拉最近 n 根腾讯日K（前复权）**含开盘价**，返回
    [{date, open, close, high, low}, ...] 正序（2026-09-18 新增）。

    用途：决策质量审计（`scripts/decision_audit.py`）的 L4 多周期收益口径需要 t+1 开盘价
    （「次日开盘就走」的可执行价）。与 `fetch_kline` **同源**（同一个 `_raw_klines`）。

    容错：单行字段位不足/非数值 → 该行 open/high/low 记 None；**close 取不到即丢该行**
    （收盘价是主口径，宁可缺行也不编造）。整体失败仍返回 []。
    """
    out: list[dict] = []
    for x in _raw_klines(code, n, timeout):
        try:
            date, close = str(x[0]), float(x[2])
        except Exception:
            continue

        def _f(i: int, _x=x):
            try:
                return float(_x[i])
            except Exception:
                return None

        out.append({"date": date, "open": _f(1), "close": close, "high": _f(3), "low": _f(4)})
    return out


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
