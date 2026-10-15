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
# 2026-10-09 再修：腾讯**又把 host 换回去了**——实测 15:56 起 ifzq.gtimg.cn 全线 501、
#   而 web.ifzq 恢复 200。单一 host 会让全量日K静默变空（当日阴线回踩扫描 141 只全部
#   被判「日K不足」→ 候选 0 只＝假阴性）。改为**按序兜底**，任一 host 拿到非空即用。
KLINE_URLS = (
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={sec},day,,,{n},qfq",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sec},day,,,{n},qfq",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get?param={sec},day,,,{n},qfq",
)
KLINE_URL = KLINE_URLS[0]  # 兼容既有引用；实际请求走 KLINE_URLS 全序列

# 进程内记住可用 host 并提到队首：否则每只票都要先吃一次 501（141 只 ≈ 多花 4 分钟）。
_GOOD_URLS: list[str] = list(KLINE_URLS)


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
    2026-10-09：host 会被腾讯单方面停用（当日实测 ifzq 501 / web.ifzq 200），
    故按 `KLINE_URLS` 顺序兜底，拿到非空即返回；全部失败才返回空列表。
    """
    sec = f"{tk_market(code)}{code}"
    for tmpl in list(_GOOD_URLS):
        url = tmpl.format(sec=sec, n=n)
        try:
            r = http_get(url, t=timeout, headers={"User-Agent": "Mozilla/5.0"}, retries=1)
            if not isinstance(r, dict):
                continue
            d = (r.get("data") or {}).get(sec) or {}
            if not isinstance(d, dict):
                continue
            kl = d.get("qfqday") or d.get("day") or []
            if isinstance(kl, list) and kl:
                if _GOOD_URLS[0] != tmpl:      # 命中即把该 host 提到队首（后续票不再试错）
                    _GOOD_URLS.remove(tmpl)
                    _GOOD_URLS.insert(0, tmpl)
                return kl
        except Exception:
            continue
    return []


def fetch_kline(code: str, n: int = 8, timeout: int = 10) -> list[tuple[str, float]]:
    """拉最近 n 根腾讯日K（前复权），返回 [(date, close), ...] 正序。
    失败/异常返回空列表（调用方防御，不抛错）。"""
    try:
        return [(x[0], float(x[2])) for x in _raw_klines(code, n, timeout)]  # [0]=日期 [2]=收盘
    except Exception:
        return []


def fetch_kline_ohlc(code: str, n: int = 8, timeout: int = 10) -> list[dict]:
    """拉最近 n 根腾讯日K（前复权）**含开盘价与成交量**，返回
    [{date, open, close, high, low, volume}, ...] 正序。

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

        # 腾讯 qfqday 第 6 列为成交量；缺失时保留 None，调用方不得臆造缩量。
        out.append({"date": date, "open": _f(1), "close": close, "high": _f(3), "low": _f(4), "volume": _f(5)})
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
