# -*- coding: utf-8 -*-
"""2026-11-06 用户三条裁定：关盘后预案否决 / 低开按当日整体偏移修正 / 龙一认股性。

对应实现：
  ① `pool_call.POOL_CALL_BIAS_AVOID_VETO`（默认 False）—— 盘后 `bias=avoid` 不再否决
  ② `ai_caliber.gap_zone(env_offset=…)` + `pool_call.POOL_CALL_DEV_ENV_OFFSET`
  ③ `core/gene_height.py` + `sector_leader.leader_key` 第二序 + `pool_call.POOL_CALL_DRAGON_GENE`

全隔离：不读真实 cache、不落盘、不触网。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

from core import ai_caliber as ac          # noqa: E402
from core import gene_height as gh         # noqa: E402
from core import pool_call as pc           # noqa: E402
from core import sector_leader as sl       # noqa: E402


# ── 夹具 ────────────────────────────────────────────────────────────
def _cand(code, name=None, boards=1, sector="测试题材", need=1.0):
    name = name or code
    return {"code": code, "name": name, "boards": boards, "sector": sector,
            "bucket_label": "接力池",
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
    return pc.call_pool(cands, quotes, **kw)


# ── ① 盘后「回避」不再否决 ───────────────────────────────────────────
def test_bias_avoid_does_not_veto_by_default(monkeypatch):
    # 2026-11-06 起 `POOL_CALL_EOD_PLAN=0` 默认不消费预案；要验「回避只留痕」得先打开它，
    # 否则连 bias 都读不到，本用例会变成在验「根本没读预案」。
    monkeypatch.setattr(pc, "POOL_CALL_EOD_PLAN", True)
    c = _cand("000001", "甲票")
    res = _call({"relay": [c], "watch": []}, {"000001": _q(10.0, 2.0)},
                plan={"stance": "attack", "views": {"000001": {"bias": "avoid"}}},
                stance="attack")
    v = res["views"]["000001"]
    assert v["view"] == "participate"
    assert v.get("bias_avoided") is True


def test_bias_avoid_veto_switch_reverts(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_EOD_PLAN", True)
    monkeypatch.setattr(pc, "POOL_CALL_BIAS_AVOID_VETO", True)
    c = _cand("000001", "甲票")
    res = _call({"relay": [c], "watch": []}, {"000001": _q(10.0, 2.0)},
                plan={"stance": "attack", "views": {"000001": {"bias": "avoid"}}},
                stance="attack")
    assert res["views"]["000001"]["view"] == "skip"


# ── ② 当日整体开盘偏移 ───────────────────────────────────────────────
def test_gap_zone_env_offset_moves_dev_threshold():
    """DEV = gap − E − offset。gap −1.5 / E +1.0 → 原 DEV −2.5（below）。"""
    assert ac.gap_zone(-1.5, None, expected_gap_pct=1.0) == "below"
    assert ac.gap_zone(-1.5, None, expected_gap_pct=1.0, env_offset=-1.5) == "ok"  # −2.5+1.5=−1.0


def test_gap_zone_env_offset_does_not_touch_unbuyable():
    """竞价封死是物理事实，⛔ 偏移不得把它「修」回来。"""
    assert ac.gap_zone(9.9, None, expected_gap_pct=0.0, env_offset=-3.0) == "above"


def test_pool_median_low_open_applies_offset(monkeypatch):
    """池内 9 只中位开幅 −1.2% → 扣掉环境偏移后，−1.5% 开的那只不再被判「低于预期」。

    ⚠️ 2026-11-06 实测后本开关**默认关**（35 日 A/B 只新增 2 笔、均 −3.43%），
       故测试须显式打开才验行为；默认关由下一个用例锁住。
    """
    monkeypatch.setattr(pc, "POOL_CALL_DEV_ENV_OFFSET", True)
    codes = [f"00000{i}" for i in range(1, 10)]
    gaps = [-3.0, -2.5, -2.0, -1.5, -1.2, -1.0, -0.9, -0.5, 2.0]
    cands = {"relay": [_cand(c) for c in codes], "watch": []}
    quotes = {c: _q(10.0, g) for c, g in zip(codes, gaps)}
    exps = {c: 1.0 for c in codes}
    res = _call(cands, quotes, stance="attack", expected_gaps=exps)

    info = res["meta"]["dev_env_offset"]
    assert info.get("applied") is True
    assert info["median_gap"] == pytest.approx(-1.2)
    assert info["offset"] == pytest.approx(-1.2)
    assert res["views"]["000004"]["zone"] == "ok"        # −1.5 − 1.0 + 1.2 = −1.3


def test_pool_median_offset_off_when_market_normal(monkeypatch):
    """整体没明显低开 → 偏移恒 0（零回归）。"""
    monkeypatch.setattr(pc, "POOL_CALL_DEV_ENV_OFFSET", True)
    codes = [f"00000{i}" for i in range(1, 10)]
    gaps = [1.0, 1.2, 1.5, 0.5, 0.8, 0.6, 1.8, 2.0, 0.9]
    cands = {"relay": [_cand(c) for c in codes], "watch": []}
    quotes = {c: _q(10.0, g) for c, g in zip(codes, gaps)}
    res = _call(cands, quotes, stance="attack",
                expected_gaps={c: 0.0 for c in codes})
    assert res["meta"]["dev_env_offset"]["offset"] == 0.0


def test_dev_env_offset_switch_reverts(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_DEV_ENV_OFFSET", False)
    codes = [f"00000{i}" for i in range(1, 10)]
    gaps = [-3.0, -2.5, -2.0, -1.5, -1.2, -1.0, -0.9, -0.5, 2.0]
    res = _call({"relay": [_cand(c) for c in codes], "watch": []},
                {c: _q(10.0, g) for c, g in zip(codes, gaps)},
                stance="attack", expected_gaps={c: 1.0 for c in codes})
    assert res["meta"]["dev_env_offset"]["offset"] == 0.0
    assert res["views"]["000004"]["zone"] == "below"


# ── ③ 上一波连板高度（股性） ─────────────────────────────────────────
# 真实序列（cache/zt_history）：东方海洋 10-29 起 1→2→3→4 板，11-04 断，11-05 首板；
# 皇台酒业 10-30 起 1→2→3 板，11-04 断，11-05 首板。
_SERIES = {
    "002086": [("2026-10-29", 1), ("2026-10-30", 2), ("2026-11-02", 3),
               ("2026-11-03", 4), ("2026-11-05", 1)],
    "000995": [("2026-10-30", 1), ("2026-11-02", 2), ("2026-11-03", 3),
               ("2026-11-05", 1)],
}


def test_prev_streak_matches_real_history():
    assert gh.prev_streak_of(_SERIES, "002086") == 4
    assert gh.prev_streak_of(_SERIES, "000995") == 3


def test_prev_streak_none_when_no_history():
    assert gh.prev_streak_of({}, "002086") is None
    assert gh.prev_streak_of({"002086": [("2026-11-05", 1)]}, "002086") is None


def test_prev_streak_for_fills_today():
    """盘后 zt_history 还没落当天 → 用 prev_streak_for 把今天这一板补进去。"""
    series = {"000995": [("2026-10-30", 1), ("2026-11-02", 2),
                         ("2026-11-03", 3), ("2026-11-05", 1)]}
    # 11-06 若它今天是 2 板，当前段=2，上一波仍是 3
    assert gh.prev_streak_for(series, "000995", "2026-11-06", 2) == 3


def test_gene_stronger_requires_both_sides():
    assert gh.gene_stronger(4, 3) is True
    assert gh.gene_stronger(3, 4) is False
    assert gh.gene_stronger(None, 3) is False
    assert gh.gene_stronger(4, None) is False


def test_sector_leader_prefers_higher_prev_streak_over_earlier_fbt():
    """同 1 板：封得早的皇台 vs 上一波 4 板的东方海洋 → 后者当龙一。"""
    rows = [
        {"code": "000995", "name": "皇台酒业", "boards": 1, "fbt": "093500",
         "core_theme": "大消费", "prev_streak_boards": 3},
        {"code": "002086", "name": "东方海洋", "boards": 1, "fbt": "095800",
         "core_theme": "大消费", "prev_streak_boards": 4},
    ]
    res = sl.compute(rows, None)
    assert res["by_code"]["002086"]["dragon_rank"] == 1
    assert res["by_code"]["000995"]["dragon_rank"] == 2


def test_sector_leader_gene_tiebreak_switch_reverts(monkeypatch):
    monkeypatch.setattr(sl, "_GENE_TIEBREAK", False)
    rows = [
        {"code": "000995", "name": "皇台酒业", "boards": 1, "fbt": "093500",
         "core_theme": "大消费", "prev_streak_boards": 3},
        {"code": "002086", "name": "东方海洋", "boards": 1, "fbt": "095800",
         "core_theme": "大消费", "prev_streak_boards": 4},
    ]
    res = sl.compute(rows, None)
    assert res["by_code"]["000995"]["dragon_rank"] == 1


def _dragon_cand(code, name, rank, leader_code, leader_boards=1):
    c = _cand(code, name)
    c.update({"dragon_rank": rank, "sector_has_mates": True,
              "dragon_leader_code": leader_code,
              "dragon_leader_name": "皇台酒业",
              "dragon_leader_boards": leader_boards})
    return c


def test_pool_call_gene_override_lets_stronger_gene_through():
    """dragon_rank=3 但上一波 4 板 > 龙一 3 板 → 不按「跟风」否决。"""
    c = _dragon_cand("002086", "东方海洋", 3, "000995")
    res = _call({"relay": [c], "watch": []}, {"002086": _q(10.0, 2.17)},
                stance="attack", zt_series=_SERIES,
                zt_days=["2026-11-03", "2026-11-05"])
    v = res["views"]["002086"]
    assert v["view"] == "participate"
    assert v["gene_leader_override"]["mine_prev_streak"] == 4


def test_pool_call_gene_override_keeps_weaker_gene_blocked():
    """龙一上一波更硬时，跟风票照样拦。"""
    c = _dragon_cand("000995", "皇台酒业", 3, "002086")
    c["dragon_leader_name"] = "东方海洋"
    res = _call({"relay": [c], "watch": []}, {"000995": _q(10.0, 2.17)},
                stance="attack", zt_series=_SERIES,
                zt_days=["2026-11-03", "2026-11-05"])
    v = res["views"]["000995"]
    assert v["view"] == "watch"
    assert v.get("dragon_gate") == "deputy"


def test_pool_call_gene_override_switch_reverts(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_DRAGON_GENE", False)
    c = _dragon_cand("002086", "东方海洋", 3, "000995")
    res = _call({"relay": [c], "watch": []}, {"002086": _q(10.0, 2.17)},
                stance="attack", zt_series=_SERIES,
                zt_days=["2026-11-03", "2026-11-05"])
    assert res["views"]["002086"]["view"] == "watch"
