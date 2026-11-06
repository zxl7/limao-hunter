#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当日涨停强度 + 次日锚点接力链契约锁（2026-10-21 用户口述体系）。

锁四件事：
  1. **涨停时间**是强度第一判据（权重最高），尾盘封板不能靠板数补回来；
  2. 大将军＝**最先封**（时间优先于带动/板数）——不能用「尾盘封了带几家」顶掉真先锋；
  3. 锚点五档判读只认 09:25 竞价，⛔ 不猜盘中；一字/大高开＝买不到，不是「更强所以能买」；
  4. 判断顺序固定：破预期→放弃 ‖ 一字/大高开→打隔壁 ‖ 强开→打核心 ‖ 平开/低开→只看。
"""
import pytest

from core import anchor_call as ac


# ── ① 节点分档 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("fbt,node", [
    ("092500", ac.NODE_AUCTION), ("093000", ac.NODE_AUCTION),   # 竞价顶板（含 09:30 整）
    ("093001", ac.NODE_DAWN), ("094500", ac.NODE_DAWN),
    ("094501", ac.NODE_MAIN), ("103000", ac.NODE_MAIN),
    ("103001", ac.NODE_FOLLOW), ("133000", ac.NODE_FOLLOW),
    ("133001", ac.NODE_TAIL), ("145700", ac.NODE_TAIL),
    ("", ac.NODE_UNKNOWN), (None, ac.NODE_UNKNOWN), ("abc", ac.NODE_UNKNOWN),
])
def test_node_of(fbt, node):
    assert ac.node_of(fbt) == node


def test_strength_time_is_first_order():
    """同一只票，只改封板时间：09:31 封 > 11:00 封 > 14:30 封。强度必须单调下降。"""
    base = {"boards": 2, "carry_cnt": 4, "zbc": 0, "seal_ratio_pct": 2.0, "theme_mates_cnt": 5}
    dawn = ac.strength_of({**base, "fbt": "093100"})["strength"]
    noon = ac.strength_of({**base, "fbt": "110000"})["strength"]
    tail = ac.strength_of({**base, "fbt": "143000"})["strength"]
    assert dawn > noon > tail


def test_strength_survives_missing_fields():
    """缺数据不给 0 分也不编造：只用已有项算，并标明缺了多少权重。"""
    s = ac.strength_of({"fbt": "093100"})
    assert s["strength"] is not None and s["missing"]
    assert ac.strength_of({})["strength"] is None      # 全缺 → None（不猜）


# ── ② 大将军 ──────────────────────────────────────────────────────────
def test_general_is_earliest_seal_not_most_carry():
    """⛔ 时间优先：尾盘封板带 9 家的票，不能顶掉 09:31 封板带 3 家的真先锋。"""
    early = {"name": "先锋", "fbt": "093100", "carry_cnt": 3, "boards": 2}
    late = {"name": "尾盘", "fbt": "142000", "carry_cnt": 9, "boards": 4}
    assert ac.pick_general([late, early])["name"] == "先锋"


def test_general_tiebreak_by_carry():
    a = {"name": "甲", "fbt": "093500", "carry_cnt": 2, "boards": 3}
    b = {"name": "乙", "fbt": "093500", "carry_cnt": 6, "boards": 2}
    assert ac.pick_general([a, b])["name"] == "乙"


def test_general_none_when_no_fbt():
    assert ac.pick_general([{"name": "无时间"}]) is None
    assert ac.pick_general([]) is None


# ── ③ 锚点五档 ─────────────────────────────────────────────────────────
def _q(prev, gap):
    return {"prev_close": prev, "auction_price": round(prev * (1 + gap / 100.0), 3)}


def test_anchor_yizi_10cm():
    r = ac.anchor_read(_q(10.0, 10.0), code="600001")
    assert r["attitude"] == ac.ATT_YIZI
    assert "买不到" in r["note"]


def test_anchor_yizi_20cm():
    """20cm 票 +10% 只是半板，不能判一字（否则把强开误判成买不到）。"""
    assert ac.anchor_read(_q(10.0, 10.0), code="300001")["attitude"] != ac.ATT_YIZI
    assert ac.anchor_read(_q(10.0, 20.0), code="300001")["attitude"] == ac.ATT_YIZI


def test_anchor_big_gap_above_hard_line():
    """高开 6%（超旧 5% 线）→ 仍是「大高开·压仓」档，⛔ 但不许再说买不到。"""
    r = ac.anchor_read(_q(10.0, 6.0), code="600001")
    assert r["attitude"] == ac.ATT_BIG_GAP
    assert "买不到" not in r["note"], "11-05 删线后 5% 已不是买不到"
    assert "压" in r["note"] or "下调" in r["note"]


def test_anchor_strong_is_only_actionable_window():
    r = ac.anchor_read(_q(10.0, 3.0), code="600001")
    assert r["attitude"] == ac.ATT_STRONG


def test_anchor_break_and_weak_split():
    assert ac.anchor_read(_q(10.0, -0.5), code="600001")["attitude"] == ac.ATT_WEAK
    assert ac.anchor_read(_q(10.0, -3.0), code="600001")["attitude"] == ac.ATT_BREAK


def test_anchor_below_expected_range_downgrades_to_break():
    """开了红盘但低于昨天给的预期下沿 → 仍算破预期（预期是盘后定死的承诺）。"""
    r = ac.anchor_read(_q(10.0, 1.0), code="600001", expected_range=[2.0, 4.0])
    assert r["attitude"] == ac.ATT_BREAK and r.get("below_expected") is True


def test_anchor_unknown_when_quote_missing():
    assert ac.anchor_read(None)["attitude"] == ac.ATT_UNKNOWN
    assert ac.anchor_read({"prev_close": 0})["attitude"] == ac.ATT_UNKNOWN


# ── ④ 线路结论与判断顺序 ────────────────────────────────────────────────
@pytest.mark.parametrize("att,is_anchor,want", [
    (ac.ATT_BREAK, False, ac.LINE_ABANDON),
    (ac.ATT_BREAK, True, ac.LINE_ABANDON),      # 锚点自己破预期也不豁免
    (ac.ATT_WEAK, False, ac.LINE_WATCH),
    (ac.ATT_YIZI, False, ac.LINE_BUY_NEIGHBOR),  # 买不到 → 打隔壁
    (ac.ATT_YIZI, True, ac.LINE_WATCH),          # 自己一字 → 自己买不到
    # ⚠️ 11-06：5% 线已废 → 大高开不等于买不到，仍打核心（只按 gap_weight 压仓）
    (ac.ATT_BIG_GAP, False, ac.LINE_BUY_CORE),
    (ac.ATT_STRONG, False, ac.LINE_BUY_CORE),
    (ac.ATT_STRONG, True, ac.LINE_BUY_CORE),
    (ac.ATT_FLAT, True, ac.LINE_WATCH),
    (ac.ATT_UNKNOWN, False, ac.LINE_WATCH),
])
def test_line_verdict_order(att, is_anchor, want):
    assert ac.line_verdict(att, is_anchor=is_anchor) == want


def test_market_verdict_counts():
    assert ac.market_verdict([])["level"] == "unknown"
    assert ac.market_verdict([ac.ATT_BREAK, ac.ATT_BREAK])["level"] == "off"
    assert ac.market_verdict([ac.ATT_BREAK, ac.ATT_FLAT])["level"] == "single"
    assert ac.market_verdict([ac.ATT_STRONG, ac.ATT_BREAK])["level"] == "normal"


# ── ⑤ 接进 9:25 裁决（锚点闸）──────────────────────────────────────────
def _cand(code, name, boards=3, sector="同一线"):
    return {"code": code, "name": name, "boards": boards, "sector": sector,
            "bucket_label": "接力池",
            "expectation": {"expected_range": [0.0, 3.0], "auction_amount_min_yi": 0.1}}


def _pool_call(quotes, cores):
    from core import pool_call
    return pool_call.call_pool({"relay": [_cand("600002", "线内票")], "watch": []},
                               quotes, plan={"stance": "defend", "views": {}},
                               line_cores=cores)


def test_anchor_gate_blocks_line_when_anchor_breaks():
    cores = {"同一线": {"code": "600001", "name": "锚点票", "boards": 3, "broken": False}}
    res = _pool_call({"600002": _q(10.0, 1.0), "600001": _q(10.0, -4.0)}, cores)
    v = res["views"]["600002"]
    assert v["view"] == "watch" and v.get("anchor_gate") is True
    assert "锚点票" in v["reason"] and "先别碰" in v["reason"]
    assert res["meta"]["defense"]["anchor"]["blocked"] == ["600002"]


def test_anchor_gate_flat_only_flags_not_blocks():
    """平开太常见 → 只留痕，⛔ 不拦（否则系统会被打成常年空仓）。"""
    cores = {"同一线": {"code": "600001", "name": "锚点票", "boards": 3, "broken": False}}
    res = _pool_call({"600002": _q(10.0, 1.0), "600001": _q(10.0, 1.0)}, cores)
    v = res["views"]["600002"]
    assert v["view"] == "participate" and v.get("anchor_flat") is True


def test_anchor_gate_allows_neighbor_when_anchor_yizi():
    """锚点一字（买不到）→ 线内第二只可以打（买隔壁），不被锚点闸拦。"""
    cores = {"同一线": {"code": "600001", "name": "锚点票", "boards": 3, "broken": False}}
    res = _pool_call({"600002": _q(10.0, 2.5), "600001": _q(10.0, 10.0)}, cores)
    assert res["views"]["600002"]["view"] == "participate"
    assert res["views"]["600002"].get("anchor_gate") is None


def test_anchor_gate_skips_broken_core():
    """核心已断板 → 今天不是涨停股，不能当锚点（⛔ 不裁决，防误杀）。

    ⚠️ 取「差 1 板」：差 ≥2 会先被跟风闸（⑦）拦下，那不是锚点闸的活。
    """
    cores = {"同一线": {"code": "600001", "name": "锚点票", "boards": 4, "broken": True}}
    res = _pool_call({"600002": _q(10.0, 1.0), "600001": _q(10.0, -6.0)}, cores)
    assert res["views"]["600002"]["view"] == "participate"
    assert res["views"]["600002"].get("anchor_gate") is None


def test_anchor_gate_switch_off(monkeypatch):
    from core import pool_call
    monkeypatch.setattr(pool_call, "POOL_CALL_ANCHOR_GATE", False)
    cores = {"同一线": {"code": "600001", "name": "锚点票", "boards": 3, "broken": False}}
    res = _pool_call({"600002": _q(10.0, 1.0), "600001": _q(10.0, -4.0)}, cores)
    assert res["views"]["600002"]["view"] == "participate"
    assert res["views"]["600002"].get("anchor_gate") is None
