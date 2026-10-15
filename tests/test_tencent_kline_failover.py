# -*- coding: utf-8 -*-
"""腾讯日K host 兜底回归锁。

2026-10-09 血案：`ifzq.gtimg.cn` 被腾讯停用（全线 HTTP 501），单一 host 导致
阴线回踩扫描 141 只全部判「日K不足」→ 候选 0 只＝假阴性。本锁保证：
① 首个 host 失败自动换下一个；② 命中的 host 提到队首，后续票不再重复试错；
③ 全部 host 失败才返回空（调用方据此判 data_ok=False，不得当成「无候选」）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import tencent_kline as tk  # noqa: E402


def _payload(sec: str, rows: list) -> dict:
    return {"data": {sec: {"qfqday": rows}}}


def test_falls_back_to_next_host_and_promotes_it(monkeypatch):
    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        if "ifzq.gtimg.cn/appstock" in url and "web.ifzq" not in url:
            return None            # 模拟 501：http_client 失败返回 None
        return _payload("sh600577", [["2026-10-09", "8.80", "8.66", "8.84", "8.52", "787928"]])

    monkeypatch.setattr(tk, "http_get", fake_get)
    monkeypatch.setattr(tk, "_GOOD_URLS", list(tk.KLINE_URLS[:2]))

    rows = tk._raw_klines("600577", n=15)
    assert rows and rows[-1][0] == "2026-10-09"
    assert len(calls) == 2                       # 先试坏的，再兜底到好的
    assert "web.ifzq.gtimg.cn" in tk._GOOD_URLS[0]   # 命中 host 提到队首

    calls.clear()
    tk._raw_klines("600577", n=15)
    assert len(calls) == 1                       # 后续票不再重复试错


def test_returns_empty_only_when_all_hosts_fail(monkeypatch):
    monkeypatch.setattr(tk, "http_get", lambda url, **kw: None)
    monkeypatch.setattr(tk, "_GOOD_URLS", list(tk.KLINE_URLS))
    assert tk._raw_klines("600577", n=15) == []


def test_fetch_kline_ohlc_keeps_volume_field(monkeypatch):
    """缩量判定依赖成交量字段；缺失必须保留 None，不得臆造。"""
    monkeypatch.setattr(tk, "http_get", lambda url, **kw: _payload(
        "sh600577", [["2026-10-09", "8.80", "8.66", "8.84", "8.52", "787928"]]))
    monkeypatch.setattr(tk, "_GOOD_URLS", list(tk.KLINE_URLS))
    bar = tk.fetch_kline_ohlc("600577", n=15)[-1]
    assert bar["volume"] == 787928.0
    assert bar["open"] == 8.80 and bar["close"] == 8.66
