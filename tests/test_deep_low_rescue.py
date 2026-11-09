# -*- coding: utf-8 -*-
"""🕳️ 深低开豁免（2026-11-06 用户「今天低开是可以理解的」的正式落地）。

## 为什么不是「关掉 DEV 闸」

35 日 · 366 票次实测，DEV 的收益曲线是 **U 形**：

    DEV < −6      n=40  均值 +2.69%  胜 60%   ← 砸出来的错杀
    −6 ~ −4       n=24  均值 −0.48%  胜 38%
    −4 ~ −2       n=65  均值 −0.92%  胜 35%   ← 最差
    −2 ~ 0        n=83  均值 +0.04%  胜 48%
    0 ~ +3        n=87  均值 −0.21%  胜 45%
    ≥ +3          n=67  均值 −2.87%  胜 30%

旧规则「DEV < −2 一律放弃」把最差档和最好档一起砍。故改成**带豁免的带**：

    DEV ≤ DEEP_LOW_OPEN_DEV(−6)         → 放行（`deep_low_rescue`），不按走弱处理
    DEEP_LOW_OPEN_DEV < DEV < −2        → 仍放弃（那才是真走弱）
    DEV ≥ −2                            → 照旧

全隔离：不读真实 cache、不落盘、不触网。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

from core import ai_caliber as ac          # noqa: E402
from core import pool_call as pc           # noqa: E402


def _cand(code, name=None, boards=1, need=1.0):
    return {"code": code, "name": name or code, "boards": boards,
            "sector": "测试题材", "bucket_label": "接力池",
            "expectation": {"expected_range": [1.0, 3.0],
                            "auction_amount_min_yi": need}}


def _q(prev, gap_pct, amt=20.0):
    return {"prev_close": prev,
            "auction_price": round(prev * (1 + gap_pct / 100.0), 3),
            "auction_amount_yi": amt}


def _call(cands, quotes, **kw):
    kw.setdefault("line_cores", {})
    kw.setdefault("sector_flow", {})
    kw.setdefault("market_open", None)
    kw.setdefault("relay_plan", None)
    kw.setdefault("zt_series", {})
    kw.setdefault("zt_days", [])
    kw.setdefault("stance", "attack")
    return pc.call_pool(cands, quotes, **kw)


# ── ① 豁免生效 ───────────────────────────────────────────────────────
def test_deep_low_open_is_rescued(monkeypatch):
    """开 −8%、该开 +1% → DEV −9（深低开）→ 放行，且留痕。"""
    monkeypatch.setattr(pc, "POOL_CALL_DEEP_LOW_RESCUE", True)
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, -8.0)},
                expected_gaps={"000001": 1.0})
    v = res["views"]["000001"]
    assert v["view"] == "participate"
    assert v["zone"] == "below"                 # 原始判定不改（诚实留痕）
    r = v["deep_low_rescue"]
    assert r["dev"] == pytest.approx(-9.0)
    assert r["threshold"] == pytest.approx(-6.0)


def test_rescued_reason_does_not_claim_meets_expectation(monkeypatch):
    """⛔ 文案不能说「符合预期」——它明显低于预期，必须说清是错杀。"""
    monkeypatch.setattr(pc, "POOL_CALL_DEEP_LOW_RESCUE", True)
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, -8.0)},
                expected_gaps={"000001": 1.0})
    reason = res["views"]["000001"]["reason"]
    assert "错杀" in reason
    assert "符合这只票的预期" not in reason


# ── ② 中间档仍然放弃 ─────────────────────────────────────────────────
def test_middle_band_still_rejected():
    """开 −3%、该开 +1% → DEV −4（−6 ~ −2 之间）→ 仍放弃。"""
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, -3.0)},
                expected_gaps={"000001": 1.0})
    v = res["views"]["000001"]
    assert v["view"] == "skip"
    assert v["zone"] == "below"
    assert "deep_low_rescue" not in v


def test_boundary_exactly_at_threshold_is_rescued(monkeypatch):
    """DEV 恰好 = −6 → 用 `≤`，放行（边界不模糊）。"""
    monkeypatch.setattr(pc, "POOL_CALL_DEEP_LOW_RESCUE", True)
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, -5.0)},
                expected_gaps={"000001": 1.0})
    v = res["views"]["000001"]
    assert v["deep_low_rescue"]["dev"] == pytest.approx(-6.0)
    assert v["view"] == "participate"


def test_just_inside_middle_band_not_rescued(monkeypatch):
    """DEV = −5.9 落在中间档 → 仍放弃（阈值不能有毛边）。"""
    monkeypatch.setattr(pc, "POOL_CALL_DEEP_LOW_RESCUE", True)
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, -4.9)},
                expected_gaps={"000001": 1.0})
    assert res["views"]["000001"]["view"] == "skip"


# ── ③ 可逆开关 ───────────────────────────────────────────────────────
def test_switch_off_reverts_to_hard_reject(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_DEEP_LOW_RESCUE", False)
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, -8.0)},
                expected_gaps={"000001": 1.0})
    v = res["views"]["000001"]
    assert v["view"] == "skip"
    assert "deep_low_rescue" not in v


def test_threshold_is_tunable(monkeypatch):
    """`DEEP_LOW_OPEN_DEV` 调到 −8 时，DEV −6 就回到中间档（放弃）。"""
    monkeypatch.setattr(pc, "POOL_CALL_DEEP_LOW_RESCUE", True)
    monkeypatch.setattr(pc, "DEEP_LOW_OPEN_DEV", -8.0)
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, -5.0)},
                expected_gaps={"000001": 1.0})
    assert res["views"]["000001"]["view"] == "skip"


# ── ④ 不越权：物理买不进 / absolute 回退 / 缺数据 ─────────────────────
def test_unbuyable_is_not_rescued(monkeypatch):
    """竞价封死是物理事实，⛔ 深低开豁免不得把它「修」进来。"""
    monkeypatch.setattr(pc, "POOL_CALL_DEEP_LOW_RESCUE", True)
    monkeypatch.setattr(pc, "DEEP_LOW_OPEN_DEV", 50.0)   # 极端放大，仍不该放行
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, 9.9)},
                expected_gaps={"000001": 0.0})
    v = res["views"]["000001"]
    assert v["zone"] == "above"
    assert v["view"] != "participate"


def test_absolute_mode_ignores_rescue(monkeypatch):
    """`AI_EXPECTED_GAP_MODE=absolute` 一键回退时，深低开豁免不参与。"""
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "absolute")
    monkeypatch.setattr(pc, "POOL_CALL_DEEP_LOW_RESCUE", True)
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, -8.0)},
                expected_gaps={"000001": 1.0})
    assert "deep_low_rescue" not in res["views"]["000001"]


def test_missing_expected_gap_is_unknown_not_rescued(monkeypatch):
    """缺质量预期 → 'unknown'，既不否决也不豁免（缺数据不裁决）。"""
    monkeypatch.setattr(pc, "POOL_CALL_DEEP_LOW_RESCUE", True)
    res = _call({"relay": [_cand("000001", "甲票")], "watch": []},
                {"000001": _q(10.0, -8.0)},
                expected_gaps={})
    v = res["views"]["000001"]
    assert v["zone"] == "unknown"
    assert "deep_low_rescue" not in v


# ── ⑤ gap_zone 本体不受影响（判据唯一源不分裂）──────────────────────────
def test_gap_zone_itself_unchanged():
    """豁免是 pool_call 的**准入**决定，⛔ 不改 `gap_zone` 的判定结果。"""
    assert ac.gap_zone(-8.0, None, expected_gap_pct=1.0) == "below"
    assert ac.gap_zone(-3.0, None, expected_gap_pct=1.0) == "below"
    assert ac.gap_zone(-0.5, None, expected_gap_pct=1.0) == "ok"
