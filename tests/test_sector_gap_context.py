# -*- coding: utf-8 -*-
"""2026-11-05 用户拍板 A 档：09:25 三环联动「影子留痕、不裁决」回归锁。

锁死七件事：
  ① 三条硬约束：缺 plates 不裁决、缺任一环不裁决、量能只证伪不进分子分母；
  ② `linked_would_cut` 只是守卫影子对照，⛔ 绝不改 view / zone / quota / 仓位；
  ③ `POOL_CALL_SECTOR_GAP_CONTEXT=0` → base/meta 一个字段都不写（零感知回退）；
  ④ 开关 0/1 下，除影子字段外 views / meta 逐字一致；
  ⑤ 竞价封死买不进的票不进影子对照；
  ⑥ 只用 09:25 前事实，T 日日线被显式裁掉（无未来函数）；
  ⑦ 判据与 scripts/research_sector_gap_backtest.py 同源，不另立口径。

⛔ A 档不是硬闸：20 日回测改善仅 0.3~0.5pct，样本不足以称提升胜率。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WC = ROOT / "wealth_code"
for _p in (str(WC), str(WC / "pipelines"), str(WC / "scripts"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import sector_gap_context as sgc             # noqa: E402
from core import pool_call as pc                       # noqa: E402

_DISTRACTING_GATES = (
    "POOL_CALL_LONE_GAP_GATE", "POOL_CALL_DEFENSE_GATE", "POOL_CALL_DRAGON_GATE",
    "POOL_CALL_HIGH_WIN_GATE", "POOL_CALL_BACKROW_GATE", "POOL_CALL_ANCHOR_GATE",
    "POOL_CALL_MIDBOARD_GATE", "POOL_CALL_CORE_GATE", "POOL_CALL_LINE_CONSISTENCY_GATE",
    "POOL_CALL_LEADER_ONLY", "POOL_CALL_THIN_SEAL_TOP", "POOL_CALL_FORECAST_GATE",
    # 🔁 2026-11-06 新增「顶部空间硬闸」：本文件 fixture 用的是 gap 7.0/6.8/6.2，
    #    正好落在它「剩余空间不足涨停一半」的范围内会被 skip。
    #    ⛔ 本文件只验「三环联动影子不改决策」，与高开闸无关 → 显式关掉它。
    "POOL_CALL_HIGH_GAP_REJECT",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "relative")
    monkeypatch.setenv("POOL_CALL_EXPECTED_GAP", "1")
    monkeypatch.setenv("POOL_CALL_AUCTION_STATE", "1")
    monkeypatch.setenv("POOL_CALL_A_STATE_ONLY", "0")
    monkeypatch.setenv("POOL_CALL_LOW_OPEN_GUARD", "0")
    monkeypatch.setenv("POOL_CALL_SECTOR_GAP_CONTEXT", "1")
    monkeypatch.setattr(pc, "POOL_CALL_LOW_OPEN_GUARD", False)
    monkeypatch.delenv("POOL_CALL_EXPECTED_DEV_MIN", raising=False)
    for k in _DISTRACTING_GATES:
        monkeypatch.setattr(pc, k, False)


# ── 构造：全内存，⛔ 不读生产 cache ─────────────────────────────────────────
def _quotes(gaps, *, amt=0.5):
    return {c: {"prev_close": 10.0, "auction_price": round(10.0 * (1 + g / 100), 4),
                "auction_amount_yi": amt, "time": "2026-11-05 09:25:03"}
            for c, g in gaps.items()}


def _zt(items):
    return [{"code": c, "boards": b, "cje_yi": a, "sector": "申万行业兜底",
             "is_20cm": False, "name": c} for c, b, a in items]


def _plates(codes, theme="测试线"):
    return {c: {"plates": [theme], "theme": "", "name": c} for c in codes}


def _ctx(gaps, zt, plates, *, expected=None, amt=None, bars=None):
    q = _quotes(gaps) if amt is None else _quotes(gaps, amt=amt)
    # 测试用归档不含 fbt/seal/hs，expected_gap 现算会缺核心项 → 显式注入 E=2.0
    e_map = expected if expected is not None else {c: 2.0 for c in gaps}
    return sgc.build_context(quotes=q, zt_rows=zt, plates_map=plates,
                             prev_date="2026-11-04", expected_gaps=e_map,
                             bars_by_code=bars or {})


# ── ① 三环硬约束 ──────────────────────────────────────────────────────────
def test_main_theme_source_priority():
    m = {"600001": {"plates": ["选股宝题材"], "theme": "旧字段"},
         "600002": {"theme": "旧字段"}}
    assert sgc.main_theme("600001", {"sector": "行业"}, m) == ("选股宝题材", "plates")
    assert sgc.main_theme("600002", {"sector": "行业"}, m) == ("旧字段", "theme")
    assert sgc.main_theme("600003", {"sector": "行业"}, m) == ("行业", "sector")


def test_three_rings_all_backed():
    """板块/梯队/预期三环齐备 → n=3, known=3, status=ok, 影子不砍。"""
    gaps = {"600001": 7.0, "600002": 6.8, "600003": 6.2}
    zt = _zt([("600001", 1, 10.0), ("600002", 2, 10.0), ("600003", 3, 10.0)])
    c = _ctx(gaps, zt, _plates(gaps))["600001"]
    assert c["credits"]["sector"] is True
    assert c["credits"]["ladder"] is True
    assert c["credits"]["expected"] is True
    assert (c["credits"]["n"], c["credits"]["known"]) == (3, 3)
    assert c["credits"]["volume"] is None          # 量能不进分子分母
    assert c["linked_status"] == "ok"
    assert c["linked_would_cut"] is False
    assert c["gap_vs_theme"] == 0.5


def test_line_top_does_not_get_free_ladder_credit():
    """20 日纠偏：线内最高板不白送一分，交板块/预期判，缺环即不裁决。"""
    gaps = {"600001": 7.0, "600002": 6.8, "600003": 6.2}
    zt = _zt([("600001", 3, 10.0), ("600002", 1, 10.0), ("600003", 1, 10.0)])
    c = _ctx(gaps, zt, _plates(gaps))["600001"]
    assert c["linked_is_line_top"] is True
    assert c["credits"]["ladder"] is None
    assert c["linked_status"] == "abstain_missing_ring"
    assert c["linked_would_cut"] is False


def test_missing_plates_abstains():
    gaps = {"600001": 9.0, "600002": 0.5, "600003": 1.0}
    zt = _zt([("600001", 1, 10.0), ("600002", 2, 10.0), ("600003", 3, 10.0)])
    c = _ctx(gaps, zt, {})["600001"]              # 只有行业兜底
    assert c["linked_theme_src"] == "sector"
    assert c["linked_status"] == "abstain_no_plates"
    assert c["linked_would_cut"] is False


def test_missing_ring_abstains_even_if_volume_veto():
    """缺任一环优先于量能否决：A 档硬约束，⛔ 不猜、不当通过。"""
    gaps = {"600001": 9.0, "600002": 0.5}
    zt = _zt([("600001", 1, 0.01), ("600002", 2, 10.0)])   # R 爆表 → veto
    c = _ctx(gaps, zt, _plates(gaps), amt=0.5)["600001"]
    assert c["credits"]["known"] < 3
    assert c["credits"]["veto"] is True
    assert c["linked_status"] == "abstain_missing_ring"
    assert c["linked_would_cut"] is False


def test_volume_is_veto_only_not_a_score():
    gaps = {"600001": 9.0, "600002": 1.0, "600003": 1.0}
    zt = _zt([("600001", 1, 0.2), ("600002", 2, 10.0), ("600003", 3, 10.0)])
    c = _ctx(gaps, zt, _plates(gaps), amt=0.5)["600001"]   # R=250% → extreme
    assert c["R_extreme"] is True
    assert c["credits"]["volume"] is False
    assert c["credits"]["max"] == 3                        # 量能不在分母
    assert c["credits"]["n"] == sum(1 for k in ("sector", "ladder", "expected")
                                    if c["credits"][k] is True)
    assert c["credits"]["veto"] is True


def test_isolated_high_open_is_shadow_only():
    gaps = {"600001": 9.0, "600002": 0.5, "600003": 1.0}
    zt = _zt([("600001", 1, 10.0), ("600002", 2, 10.0), ("600003", 3, 10.0)])
    c = _ctx(gaps, zt, _plates(gaps))["600001"]
    assert c["linked_isolated_high_open"] is True
    assert c["credits"]["known"] == 3
    assert c["linked_would_cut"] is True                   # 只是影子标记


def test_unbuyable_excluded_from_shadow_guard():
    gaps = {"600001": 9.9, "600002": 0.5, "600003": 1.0}
    zt = _zt([("600001", 1, 10.0), ("600002", 2, 10.0), ("600003", 3, 10.0)])
    c = _ctx(gaps, zt, _plates(gaps))["600001"]
    assert c["linked_unbuyable"] is True
    assert c["linked_would_cut"] is False


def test_no_future_bars(tmp_path):
    """load_prev_bars 显式裁掉 T 日及以后（⛔ 09:25 不能有未来函数）。"""
    kl = tmp_path / "cache" / "kline_qfq"
    kl.mkdir(parents=True)
    (kl / "600001.json").write_text(json.dumps({"bars": [
        {"date": "2026-11-03", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 10},
        {"date": "2026-11-04", "open": 2, "high": 2, "low": 2, "close": 2, "vol": 10},
        {"date": "2026-11-05", "open": 3, "high": 9, "low": 1, "close": 8, "vol": 10},
    ]}), encoding="utf-8")
    bars = sgc.load_prev_bars(["600001"], "2026-11-04", root=tmp_path)
    assert [b["date"] for b in bars["600001"]] == ["2026-11-03", "2026-11-04"]
    assert sgc._prev_yizi(bars["600001"], "2026-11-04") is True


def test_build_day_context_missing_plates_returns_empty(tmp_path):
    assert sgc.build_day_context(prev_date="2026-11-04", quotes=_quotes({"600001": 7.0}),
                                 zt_rows=_zt([("600001", 1, 10.0)]),
                                 root=tmp_path) == {}


# ── ②~④ 主链：写字段但绝不改裁决 ──────────────────────────────────────────
def _cand():
    row = {"code": "600001", "name": "测试票", "boards": 1,
           "expectation": {"expected_range": [0, 2], "auction_amount_min_yi": 0.1},
           "theme_name": "测试线", "leader_evidence": {},
           "fbt": "100000", "seal_amt": 0.2, "lt_mktcap": 40.0,
           "hs": 6.0, "zbc": 0, "is_20cm": False}
    return {"date": "2026-11-04", "environment": {"max_boards": 3},
            "relay": [row], "watch": [], "low_pool": []}


def _run(sector_gap, *, price=10.7):
    gaps = {"600001": 7.0, "600002": 6.8, "600003": 6.2}
    q = _quotes(gaps)
    q["600001"]["auction_price"] = price
    rows = _zt([("600001", 1, 10.0), ("600002", 2, 10.0), ("600003", 3, 10.0)])
    return pc.call_pool(
        _cand(), q, plan={"stance": "attack"}, zt_rows=rows,
        theme_open_rows=rows, prev_amounts={c: 10.0 for c in gaps},
        expected_gaps={c: 2.0 for c in gaps},
        line_cores={}, market_open={}, sector_flow={}, zt_series={},
        zt_days=[], market_max_boards=3, sector_gap=sector_gap)


def _real_ctx():
    gaps = {"600001": 7.0, "600002": 6.8, "600003": 6.2}
    zt = _zt([("600001", 1, 10.0), ("600002", 2, 10.0), ("600003", 3, 10.0)])
    return sgc.build_context(quotes=_quotes(gaps), zt_rows=zt,
                             plates_map=_plates(gaps), prev_date="2026-11-04",
                             expected_gaps={c: 2.0 for c in gaps})


def test_switch_off_writes_nothing(monkeypatch):
    monkeypatch.setenv("POOL_CALL_SECTOR_GAP_CONTEXT", "0")
    r = _run(_real_ctx())
    assert sgc.enabled() is False
    assert "sector_gap_context" not in r["meta"]
    for v in r["views"].values():
        assert not (set(sgc.FIELDS) & set(v))


def test_switch_on_writes_fields_and_meta():
    r = _run(_real_ctx())
    v = r["views"]["600001"]
    for k in ("theme_gap_med", "gap_vs_theme", "upper_gap_med", "gap_vs_upper",
              "credits", "R_extreme", "linked_status", "linked_would_cut"):
        assert k in v, k
    assert r["meta"]["sector_gap_context"]["schema"] == sgc.SCHEMA
    assert r["meta"]["sector_gap_context"]["candidates_covered"] == 1


def test_switch_does_not_change_any_decision(monkeypatch):
    """开关 0/1 下除影子字段外，views / meta 逐字一致（裁决零影响）。"""
    ctx = _real_ctx()
    on = _run(ctx)
    monkeypatch.setenv("POOL_CALL_SECTOR_GAP_CONTEXT", "0")
    off = _run(ctx)
    for code, v_on in on["views"].items():
        stripped = {k: v for k, v in v_on.items() if k not in sgc.FIELDS}
        assert stripped == off["views"][code], code
        for key in ("view", "zone", "gap_pct", "expected_gap_pct", "quota_rank"):
            assert v_on.get(key) == off["views"][code].get(key), (code, key)
    m_on = {k: v for k, v in on["meta"].items()
            if k not in ("sector_gap_context", "call_at")}
    m_off = {k: v for k, v in off["meta"].items() if k != "call_at"}
    assert m_on == m_off
    assert on["quota"] == off["quota"] and on["effective_quota"] == off["effective_quota"]
    assert on["views"]["600001"]["view"] == "participate"


def test_would_cut_never_changes_view():
    """影子判「守卫会砍」也照买：A 档只留痕，⛔ 不裁决。"""
    ctx = _real_ctx()
    row = dict(ctx["600001"])
    row["linked_would_cut"] = True
    row["linked_isolated_high_open"] = True
    r = _run({"600001": row})
    assert r["views"]["600001"]["linked_would_cut"] is True
    assert r["views"]["600001"]["view"] == "participate"
    assert r["meta"]["sector_gap_context"]["candidate_would_cut"] == ["600001"]


def test_summary_counts_are_readonly():
    ctx = _real_ctx()
    s = sgc.summary(ctx, {"600001"})
    assert s["quotes_covered"] == 3 and s["candidates_covered"] == 1
    assert s["source"] == "xuangubao_plates"
    assert "definition" in s
