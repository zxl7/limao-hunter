"""`sentiment_predict` 结构化字段的回归锁（2026-11-09 立）。

## 它解决的是什么

回测指标 `sentiment_align`（情绪对齐）要算「**盘前预判情绪分** − 当日实际情绪分」。
实测 2026-10-27 ~ 11-06 共 8 期 `summary.sentiment_diff` **全部 null**，状态 `contract_missing`，
根因不是解析器坏了，而是**盘前数据里根本没有「明日预判分」**：卡片正文写的是
「退潮确认：情绪1.0/10」这类**昨日实际分**（回顾语境）。

2026-10-27 的结论是「宁可少算，不可错算」——不放宽正则去凑数，改为让盘前落结构化字段。
本次（11-09）落地：
  · 契约 `"sentiment_predict": {lo, mid, hi, basis}`，`mid` 必填且 0 < mid ≤ 10；
  · `find_prediction_text` 改为**结构化优先**，文本正则降为兜底（历史日期用）；
  · 越界值一律拒绝并写明原因 —— 一个 15/10 的分数进偏差曲线会污染整条 R3/P1。

## 本文件锁死五条

1. 合法字段被读到，`parse_mode=structured`。
2. 越界（0 / >10 / mid 不在 lo~hi 内 / 缺 mid）→ 不采用，且给出可读原因。
3. 结构化**优先于**文本正则（同一份数据里两者并存时，不许取文本）。
4. 无字段 → 回落旧的正则识别（向后兼容），且「情绪X/10」仍是**拒绝**状态，不得当预判。
5. `backtest_sentiment_align` 在有字段 + 有实际分时真的算出 `diff`（不是又一个 null）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def be(monkeypatch, tmp_path):
    """加载 backtest_engine 并把数据目录指向 tmp（⛔ 不碰真实 cache）。"""
    spec = importlib.util.spec_from_file_location("backtest_engine", ROOT / "scripts" / "backtest_engine.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                          # type: ignore[union-attr]
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(mod, "CACHE", cache)
    monkeypatch.setattr(mod, "WC_CACHE", tmp_path / "wc_cache")
    return mod


def _write(be, date: str, payload: dict) -> None:
    name = f"panqian_data_{date.replace('-', '')}.json"
    (be.CACHE / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_valid_structured_field_is_used(be):
    _write(be, "2026-11-09", {"sentiment_predict": {"lo": 5.0, "mid": 5.5, "hi": 6.0, "basis": "旧高度未死"}})
    r = be.find_prediction_text("2026-11-09")
    assert r["predict_mid"] == 5.5
    assert r["predict_lo"] == 5.0 and r["predict_hi"] == 6.0
    assert r["parse_mode"] == "structured"
    assert r["basis"] == "旧高度未死"


def test_lo_hi_default_to_mid_when_absent(be):
    """只给 mid 也合法（区间可选）。"""
    _write(be, "2026-11-09", {"sentiment_predict": {"mid": 4.2}})
    r = be.find_prediction_text("2026-11-09")
    assert (r["predict_lo"], r["predict_mid"], r["predict_hi"]) == (4.2, 4.2, 4.2)


@pytest.mark.parametrize("payload", [
    {"sentiment_predict": {"mid": 0}},            # 下界不含
    {"sentiment_predict": {"mid": -1}},
    {"sentiment_predict": {"mid": 15}},           # 上界 10
    {"sentiment_predict": {"lo": 7, "mid": 3, "hi": 6}},   # 区间不成立
    {"sentiment_predict": {"lo": 5, "hi": 6}},    # 缺 mid（必填）
])
def test_invalid_values_are_rejected_with_reason(be, payload):
    _write(be, "2026-11-09", payload)
    r = be.find_prediction_text("2026-11-09")
    assert r.get("predict_mid") is None, "非法值不得被采用"
    inv = r.get("invalid_structured") or []
    assert inv and any("sentiment_predict" in x for x in inv), "必须留下可读的拒绝原因"


def test_structured_wins_over_text_regex(be):
    """同一份数据里既有结构化字段、又有「预判 8/10」文本 → 必须取结构化。"""
    (be.CACHE / "panqian_data_20261109.json").write_text(
        json.dumps({"sentiment_predict": {"mid": 3.0}, "mindset": "预判 8/10 偏热 情绪2.0/10"},
                   ensure_ascii=False), encoding="utf-8")
    r = be.find_prediction_text("2026-11-09")
    assert r["predict_mid"] == 3.0
    assert r["parse_mode"] == "structured"


def test_without_field_falls_back_and_still_rejects_retro_value(be):
    """无结构化字段 → 回落正则；但「情绪X/10」仍只标记不采用（不可当预判）。"""
    (be.CACHE / "panqian_data_20261106.json").write_text(
        json.dumps({"mindset": "退潮确认：情绪1.0/10冰点"}, ensure_ascii=False), encoding="utf-8")
    r = be.find_prediction_text("2026-11-06")
    assert r.get("predict_mid") is None
    assert r.get("rejected_loose_value") == 1.0
    assert "昨日实际分" in (r.get("reject_reason") or "")


def test_sentiment_align_computes_diff(be, monkeypatch):
    """有预判分 + 有实际分 → 真的算出偏差（而不是又一个 null）。"""
    _write(be, "2026-11-09", {"sentiment_predict": {"mid": 5.5}})
    monkeypatch.setattr(be, "actual_sentiment", lambda d: 1.8)
    out = be.backtest_sentiment_align("2026-11-09")
    assert out["available"] is True
    assert out["predict_mid"] == 5.5 and out["actual"] == 1.8
    assert out["diff"] == 3.7                     # 5.5 − 1.8
    assert out["aligned"] is False                # |3.7| > 1.0 容忍带


def test_sentiment_align_contract_missing_points_to_the_field(be, monkeypatch):
    """缺字段 → contract_missing，且 reason 必须点名 `sentiment_predict`（可指导修复）。"""
    _write(be, "2026-11-09", {"mindset": "情绪1.0/10"})
    monkeypatch.setattr(be, "actual_sentiment", lambda d: 1.8)
    out = be.backtest_sentiment_align("2026-11-09")
    assert out["available"] is False
    assert out["status"] == "contract_missing"
    assert "sentiment_predict" in out["reason"]


def test_sentiment_align_actual_missing_is_distinguished(be, monkeypatch):
    """实际分缺 ≠ 契约缺：两种不可用必须能分辨（否则永远不知道该修哪边）。"""
    _write(be, "2026-11-09", {"sentiment_predict": {"mid": 5.5}})
    monkeypatch.setattr(be, "actual_sentiment", lambda d: None)
    out = be.backtest_sentiment_align("2026-11-09")
    assert out["available"] is False
    assert out["status"] == "actual_missing"
