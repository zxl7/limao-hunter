# -*- coding: utf-8 -*-
"""2026-11-05 用户「把 5% 和红开这两条线删掉，按逐票预期管理」——回归锁。

用户原话：「有些票今天很强势，明天它就可以高开，有些票今天会弱势，
           那么它低开一样能拉起来！！」

锁死八件事：
  ① 默认 relative 模式；`AI_EXPECTED_GAP_MODE=absolute` 逐字回退旧三条线；
  ② 强票高开（>5%）、弱票低开（≤0）都判「符合预期」，⛔ 不再有幅度线；
  ③ 唯一保留的绝对线是「竞价封死买不进」，按 10cm / 20cm 分别取涨停幅度；
  ④ 明显低于逐票质量预期（DEV < −2.0）才放弃；缺 E → unknown 不裁决；
  ⑤ decide / pool_call / 卡面 / 账本结算四处同走 `ai_caliber.gap_zone`；
  ⑥ relative 模式下量能门覆盖低开（「低开拉起来」同样要有量）；
  ⑦ 低开反证只留痕不否决，`POOL_CALL_LOW_OPEN_GUARD=1` 才收紧；
  ⑧ 全隔离：不读真实 cache / 台账 / 行情，不推群、不落盘。

⚠️ 本文件不主张「新判据已提高胜率」——那是描述统计，见项目日志 11-05。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
for _p in (str(WC), str(WC / "pipelines"), str(WC / "scripts"), str(WC.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import ai_caliber as ac              # noqa: E402
from core import auction_state as ast          # noqa: E402
from core import pool_call as pc               # noqa: E402
from build_cards import _ai_dyn_verdict        # noqa: E402
import ai_ledger as ledger                     # noqa: E402

# 与其余闸无关的隔离：只留 ①~⑥ + ⑫ 主链，避免题材/防守类闸干扰竞价判据断言。
_DISTRACTING_GATES = (
    "POOL_CALL_LONE_GAP_GATE", "POOL_CALL_DEFENSE_GATE", "POOL_CALL_DRAGON_GATE",
    "POOL_CALL_HIGH_WIN_GATE", "POOL_CALL_BACKROW_GATE", "POOL_CALL_ANCHOR_GATE",
    "POOL_CALL_MIDBOARD_GATE", "POOL_CALL_CORE_GATE", "POOL_CALL_LINE_CONSISTENCY_GATE",
    "POOL_CALL_LEADER_ONLY", "POOL_CALL_THIN_SEAL_TOP", "POOL_CALL_FORECAST_GATE",
    # 🔁 2026-11-06：新增「顶部空间硬闸」后，+7% 的票会在 ②′ 被拒。
    #    ⛔ 本文件只锁 `gap_zone` 本身（DEV 判据），⛔ 不替高开闸做判断——
    #    高开闸有自己的回归文件 `tests/test_high_gap_room.py`（含默认开启下的断言）。
    "POOL_CALL_HIGH_GAP_REJECT",
)


@pytest.fixture(autouse=True)
def _relative(monkeypatch):
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "relative")
    monkeypatch.setenv("POOL_CALL_AUCTION_STATE", "1")
    monkeypatch.setenv("POOL_CALL_A_STATE_ONLY", "0")   # 硬闸影子，不干扰主链断言
    monkeypatch.setenv("POOL_CALL_EXPECTED_GAP", "1")
    monkeypatch.setenv("POOL_CALL_LOW_OPEN_GUARD", "0")
    monkeypatch.setattr(pc, "POOL_CALL_LOW_OPEN_GUARD", False)
    monkeypatch.delenv("POOL_CALL_EXPECTED_DEV_MIN", raising=False)
    for k in _DISTRACTING_GATES:
        monkeypatch.setattr(pc, k, False)


# ── ① 模式与回退 ─────────────────────────────────────────────────────────
def test_relative_is_default(monkeypatch):
    monkeypatch.delenv("AI_EXPECTED_GAP_MODE", raising=False)
    assert ac.relative_mode() is True


def test_absolute_mode_is_runtime_switch(monkeypatch):
    """运行时读环境变量，⛔ 不需要 reload 模块即可一键回退。"""
    assert ac.relative_mode() is True
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "absolute")
    assert ac.relative_mode() is False
    assert ac.gap_zone(7.0, [0, 2], expected_gap_pct=7.0) == "above"   # 旧 5% 线
    assert ac.gap_zone(-1.0, [0, 2], expected_gap_pct=-0.9) == "below"  # 旧下沿 + 容差
    assert ac.gap_zone(3.0, [0, 2], expected_gap_pct=7.0) == "ok"


def test_absolute_mode_keeps_ai_declared_limit_semantics(monkeypatch):
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "absolute")
    ok, _, _ = ac.decide({"view": "participate"}, 4.0)
    assert ok is True
    ok2, _, _ = ac.decide({"view": "participate"}, 6.0)
    assert ok2 is False


# ── ② 删掉两条绝对线 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("gap,expected,label", [
    (7.0, "ok", "强票高开 +7%（旧链被 5% 砍）"),
    (5.01, "ok", "刚过 5% 不再是拒绝线"),
    (9.0, "ok", "接近涨停但仍买得到"),
    (-1.0, "ok", "弱票低开 −1%（旧链被没红开砍）"),
    (0.0, "ok", "平开"),
])
def test_no_absolute_gap_or_red_open_lines(gap, expected, label):
    e = 7.0 if gap > 0 else -0.9
    assert ac.gap_zone(gap, [0, 2], expected_gap_pct=e) == expected, label


def test_high_open_is_not_above_anymore():
    """above 不再表示「高开太多」，只表示买不进。"""
    assert ac.gap_zone(7.0, [0, 2], expected_gap_pct=7.0) != "above"


# ── ③ 唯一绝对线：竞价封死买不进 ─────────────────────────────────────────
@pytest.mark.parametrize("gap,is20,expected", [
    (9.7, False, "ok"), (9.8, False, "above"), (10.5, False, "above"),
    (15.0, True, "ok"), (19.8, True, "above"), (20.5, True, "above"),
])
def test_physical_limit_uses_correct_board(gap, is20, expected):
    assert ac.gap_zone(gap, [0, 2], expected_gap_pct=7.0,
                       is_20cm=is20) == expected


def test_unbuyable_gap_helper():
    assert ac.limit_up_pct(False) == 10.0 and ac.limit_up_pct(True) == 20.0
    assert ac.unbuyable_gap(5.0) is False
    assert ac.unbuyable_gap(9.8) is True
    assert ac.unbuyable_gap(15.0, True) is False


# ── ④ 明显低于逐票预期 / 缺数据不裁决 ────────────────────────────────────
@pytest.mark.parametrize("gap,e,expected", [
    (2.0, 7.0, "below"),      # DEV −5
    (4.99, 7.0, "below"),     # DEV −2.01
    (5.0, 7.0, "ok"),         # DEV −2.0 边界含等号
    (7.0, 7.0, "ok"),
    (9.0, 7.0, "ok"),         # 超预期不是坏事
])
def test_dev_min_boundary(gap, e, expected):
    assert ac.gap_zone(gap, [0, 2], expected_gap_pct=e) == expected


def test_missing_expected_gap_is_unknown_not_veto():
    assert ac.gap_zone(3.0, [0, 2], expected_gap_pct=None) == "unknown"
    assert ac.gap_zone(3.0, [0, 2], expected_gap_pct="bad") == "unknown"
    assert ac.gap_zone(None, [0, 2], expected_gap_pct=2.0) == "unknown"
    ok, _, meta = ac.decide({"view": "participate"}, 3.0)
    assert ok is True and meta["zone"] == "unknown"   # 缺数据不裁决 ≠ 否决


def test_dev_min_env_override(monkeypatch):
    monkeypatch.setattr(ac, "EXPECTED_GAP_DEV_MIN", -1.0)
    assert ac.gap_zone(5.5, [0, 2], expected_gap_pct=7.0) == "below"


# ── ⑤ decide 全链路同源 ──────────────────────────────────────────────────
@pytest.mark.parametrize("gap,e,expected,note_kw", [
    (7.0, 7.0, True, "符合"),        # 强票高开 → 买
    (-1.0, -0.9, True, "符合"),      # 弱票低开 → 买
    (9.9, 7.0, False, "买不进"),     # 竞价封死 → 不买
    (2.0, 7.0, False, "低于预期"),   # 明显低于预期 → 不买
])
def test_decide_follows_expected_gap(gap, e, expected, note_kw):
    d = {"view": "participate", "expected_gap_pct": e}
    ok, note, meta = ac.decide(d, gap)
    assert ok is expected and note_kw in note
    assert meta["expected_gap_mode"] == "relative"


def test_decide_20cm_high_open_is_buyable():
    d = {"view": "participate", "expected_gap_pct": 7.0, "is_20cm": True}
    ok, note, _ = ac.decide(d, 15.0)
    assert ok is True and "符合" in note


def test_volume_gate_now_covers_low_open():
    """用户删红开线后，低开票也必须带量 —— 不能绕过量能举证。"""
    d = {"view": "participate", "expected_gap_pct": -0.9}
    ok, note, meta = ac.decide(d, -1.0, auction_amount_yi=0.1, need_yi=5.0)
    assert ok is False and meta.get("volume_weak") is True and "真没量" in note
    ok2, _, _ = ac.decide(d, -1.0, auction_amount_yi=5.0, need_yi=5.0)
    assert ok2 is True


# ── ⑥ 冻结主链：两条闸确实删除 ───────────────────────────────────────────
def _cand(e_quality=None):
    row = {"code": "600001", "name": "测试票", "boards": 2,
           "expectation": {"expected_range": [0, 2],
                           "auction_amount_min_yi": 0.1},
           "theme_name": "测试线", "leader_evidence": {},
           "fbt": "100000", "seal_amt": 0.2, "lt_mktcap": 40.0,
           "hs": 6.0, "zbc": 0, "is_20cm": False}
    if e_quality:
        row.update(e_quality)
    return {"date": "2026-11-04", "environment": {"max_boards": 3},
            "relay": [row], "watch": [], "low_pool": []}


def _run(price, *, expected_gap=2.0, cand=None, amount=0.5):
    rows = [{"code": "600001", "cje_yi": 10.0, "boards": 2}]
    quotes = {"600001": {"prev_close": 10.0, "auction_price": price,
                         "auction_amount_yi": amount,
                         "time": "2026-11-05 09:25:03"}}
    return pc.call_pool(
        cand or _cand(), quotes, plan={"stance": "attack"}, zt_rows=rows,
        theme_open_rows=rows, prev_amounts={"600001": 10.0},
        expected_gaps={"600001": expected_gap},
        line_cores={}, market_open={}, sector_flow={}, zt_series={},
        zt_days=[], market_max_boards=3)


def test_pool_call_strong_stock_high_open_not_blocked():
    """+7% 且 E=7 → 符合预期；旧链会在 ② 被 5% 砍成 skip。"""
    v = _run(10.7, expected_gap=7.0)["views"]["600001"]
    assert v["zone"] == "ok" and v["gap_pct"] == pytest.approx(7.0)
    assert v["view"] == "participate"
    assert "高开超过 5%" not in str(v.get("reason", ""))


def test_pool_call_weak_stock_low_open_not_blocked():
    """−1% 且 E=−0.9 → 符合预期；旧链会在 ⑤ 被「没红开」砍成 watch。"""
    r = _run(9.9, expected_gap=-0.9)
    v = r["views"]["600001"]
    assert v["zone"] == "ok"
    assert v["view"] == "participate"
    assert "没红开" not in str(v.get("reason", ""))
    assert v["low_open_expected"] is True          # 只留痕，⛔ 不否决
    assert r["meta"]["expected_gap"]["low_open_expected"] == 1


def test_pool_call_still_blocks_sealed_auction():
    v = _run(10.99, expected_gap=7.0)["views"]["600001"]
    assert v["zone"] == "above" and v["view"] == "skip"
    assert "封" in str(v.get("reason", ""))


def test_pool_call_blocks_clearly_below_expectation():
    v = _run(10.2, expected_gap=7.0)["views"]["600001"]
    assert v["zone"] == "below" and v["view"] == "skip"
    assert "低于预期" in str(v.get("reason", ""))


def test_pool_call_meta_records_new_caliber():
    m = _run(10.4, expected_gap=2.0)["meta"]["expected_gap"]
    assert m["mode"] == "relative" and m["dev_min"] == -2.0
    assert "无5%高开线" in m["definition"] and "无必须红开线" in m["definition"]
    assert m["zones"]["ok"] >= 1


def test_low_open_guard_is_opt_in(monkeypatch):
    assert _run(9.9, expected_gap=-0.9)["views"]["600001"]["view"] == "participate"
    monkeypatch.setattr(pc, "POOL_CALL_LOW_OPEN_GUARD", True)
    v = _run(9.9, expected_gap=-0.9)["views"]["600001"]
    assert v["view"] == "watch" and v["low_open_guard"] is True


def test_absolute_mode_restores_both_old_lines(monkeypatch):
    monkeypatch.setenv("AI_EXPECTED_GAP_MODE", "absolute")
    assert _run(10.7, expected_gap=7.0)["views"]["600001"]["view"] == "skip"   # ② 5%
    # 放宽盘后区间下沿，让 ④ 不拦，才能单独验证 ⑤「没红开」闸确实回来了。
    cand = _cand()
    cand["relay"][0]["expectation"]["expected_range"] = [-3, 4]
    assert _run(9.9, expected_gap=-0.9, cand=cand)["views"]["600001"]["view"] == "watch"
    v = _run(10.4, expected_gap=2.0)["views"]["600001"]
    # absolute 下 zone 走旧绝对线（gap=+4%、区间 [0,2] → ok），且不写低开留痕。
    assert v["zone"] == "ok" and "low_open_expected" not in v
    # ⛔ `expected_gap_pct` 可能由独立的三态标注写入；双开关全回退见
    #    test_auction_state.py::test_full_rollback_has_no_new_fields。


# ── ⑦ 三态分层同步（cap 现在是涨停幅度，不是 5%）─────────────────────────
def test_auction_state_cap_is_physical_limit():
    r = ast.classify(gap_pct=7.0, amount_yi=0.5, prev_amount_yi=10.0,
                     expected_gap_pct=4.0, max_gap_pct=10.0)
    assert r["auction_state"] == "A"
    r2 = ast.classify(gap_pct=15.0, amount_yi=0.5, prev_amount_yi=10.0,
                      expected_gap_pct=4.0, max_gap_pct=20.0)
    assert r2["auction_state"] == "A"     # 20cm 高开不是封死
    r3 = ast.classify(gap_pct=9.9, amount_yi=0.5, prev_amount_yi=10.0,
                      expected_gap_pct=4.0, max_gap_pct=10.0)
    assert r3["auction_state"] == "C"


# ── ⑧ 账本结算 / 卡面同源 ────────────────────────────────────────────────
def test_settle_decision_carries_expected_gap():
    d = ledger._settle_decision(
        {"view": "watch", "code": "600001"}, "participate",
        {"expected_gap_pct": 7.0, "is_20cm": True,
         "gap_vs_expected": 0.0, "view": "participate"})
    assert d["expected_gap_pct"] == 7.0 and d["is_20cm"] is True
    ok, _, _ = ac.decide(d, 15.0)
    assert ok is True          # 20cm +15% 不会被误当封死


def test_card_dynamic_verdict_uses_expected_gap():
    it = {"code": "600001", "is_20cm": False,
          "expectation": {"expected_range": [0, 2]},
          "expected_gap_pct": 7.0,
          "performance": {"open_check": {"gap_pct": 7.0}}}
    verdict, color = _ai_dyn_verdict(it)
    assert "符合预期" in verdict and color == "red"     # 旧链会写「高开太多」
    it["performance"]["open_check"]["gap_pct"] = 9.9
    verdict2, _ = _ai_dyn_verdict(it)
    assert "封" in verdict2


def test_card_verdict_and_decide_agree_on_low_open():
    it = {"code": "600001", "is_20cm": False,
          "expectation": {"expected_range": [0, 2]},
          "expected_gap_pct": -0.9,
          "performance": {"open_check": {"gap_pct": -1.0}}}
    verdict, _ = _ai_dyn_verdict(it)
    ok, _, _ = ac.decide({"view": "participate", "expected_gap_pct": -0.9}, -1.0)
    assert "符合预期" in verdict and ok is True
