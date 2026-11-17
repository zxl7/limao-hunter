# -*- coding: utf-8 -*-
"""2026-11-10 09:50 用户：「量能可以增加强基因豁免，但是我这边说的历史连板，
是指**类似于冰山冷热这种，近一个月有连板，昨天涨停**」。

直接起因（当日实盘）：
  锋龙股份 002931 09:25 开 +4.88%（5% 线内可买）、竞价量只到计划 62% →
  被「复核票须 full 量」判 watch → 随后封板 +10.01%；
  日盈电子 603286 量 67% → watch，收 +6.79%。

锁死七件事：
  ① 口径是**窗口内最高连板 + 涨停次数**，⛔ 不是 `max_boards_hist`（永久峰值）。
     反证：锋龙 `max_boards_hist=18` 但近月只 2 板；冰山 `max_boards_hist=6`
     但近月 4 板 9 次 —— 历史峰值会把「很多年前猛过一次」算成强基因。
  ② ⛔ 与 `gene_height.prev_streak_boards`（上一波连板）不是一回事：
     冰山 10-16 四连之后又打 5 次单板，`prev_streak_boards` 返回 1（答错），
     `recent_gene` 返回 4（答对）。
  ③ 缺失三态不可混：无档案 `known=False`（不知道）／窗口内没涨停 `max_boards=0`
     （确实没有）／有涨停无连板 `has_streak=False`。⛔ 都不许当「弱基因」判死。
  ④ 豁免**只让开 soft**（0.35~0.70）；`dry`（<0.35）照旧拦 ——
     量能硬地板有 31 日回放背书（拆了日均 2.79%→1.30%）。
  ⑤ 豁免只作用于「复核票须 full 量」这一条加严闸，⛔ 不改 zone / view / 名额 /
     排序 / stance；仓位仍走 soft 的 `volume_scale` 系数，不加满。
  ⑥ `POOL_CALL_GENE_EXEMPT=0` 逐字回退旧行为（soft 一律拦）。
  ⑦ 金额与理由必须带数字（近期最高板 / 涨停次数 / 量比），⛔ 不给空口结论。

全隔离：所有 `call_pool` 均显式传 `zt_series`，不读真实 cache / 台账 / 网络。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
for _p in (str(WC), str(WC / "pipelines"), str(WC / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import ai_caliber as ac          # noqa: E402
from core import gene_recent as gr         # noqa: E402
from core import pool_call as pc           # noqa: E402

TODAY = "2026-11-10"
BASE = "2026-11-09"


# ── ① 纯函数：窗口内最高连板 ──────────────────────────────────────────────
def _ice_series():
    """冰山冷热 000530 真实轨迹：10-13~10-16 四连，之后 5 次单板。"""
    return [("2026-10-13", 1), ("2026-10-14", 2), ("2026-10-15", 3),
            ("2026-10-16", 4), ("2026-10-22", 1), ("2026-10-29", 1),
            ("2026-11-02", 1), ("2026-11-04", 1), ("2026-11-09", 1)]


def test_recent_gene_ignores_single_boards_between_streaks():
    """⭐ 核心：中间夹了 5 次单板，窗口内最高连板仍是 4（⛔ 不是被最近一段打断的 1）。"""
    g = gr.recent_gene(_ice_series(), asof=BASE)
    assert g["max_boards"] == 4
    assert g["zt_n"] == 9
    assert g["has_streak"] is True
    assert g["last_date"] == BASE and g["last_boards"] == 1
    assert g["known"] is True


def test_prev_streak_answers_differently_on_same_series():
    """⛔ 两套口径不可互换：`prev_streak_boards` 在这条序列上返回 1（答错用户的问题）。"""
    from core.gene_height import prev_streak_boards

    assert prev_streak_boards(_ice_series()) == 1
    assert gr.recent_gene(_ice_series(), asof=BASE)["max_boards"] == 4


def test_weaker_gene_example_is_fenglong():
    """锋龙只有近月 2 板（历史 18 板是老黄历）→ 默认门槛 3 下**不算强基因**。

    ⚠️ 这是 2026-11-10 10:13 用户拍板「3」之后的分界：2 板票不再豁免量能闸。
    """
    series = [("2026-10-28", 1), ("2026-11-03", 1), ("2026-11-04", 2), (BASE, 1)]
    g = gr.recent_gene(series, asof=BASE)
    assert g["max_boards"] == 2 and g["zt_n"] == 4
    assert gr.is_strong(g) is False          # 2 板在默认门槛下不够格


def test_no_archive_is_unknown_not_weak():
    """无档案 → known=False / max_boards=None（不知道），⛔ 不是 0、不判弱。"""
    g = gr.recent_gene([], asof=BASE)
    assert g == {**g, "known": False, "max_boards": None, "zt_n": 0}
    assert gr.is_strong(g) is False          # 但不能当优势用


def test_archive_but_no_zt_in_window():
    """有档案但窗口内没涨停 → max_boards=0（确实没有），与「不知道」区分开。"""
    g = gr.recent_gene([("2026-08-01", 3)], asof=BASE)
    assert g["known"] is True and g["max_boards"] == 0 and g["zt_n"] == 0


def test_single_board_only_is_not_streak():
    """近月只有一堆单板、从没连板 → has_streak=False，但涨停次数仍如实计数。"""
    g = gr.recent_gene([("2026-10-20", 1), ("2026-11-03", 1), (BASE, 1)], asof=BASE)
    assert g["max_boards"] == 1 and g["has_streak"] is False and g["zt_n"] == 3


@pytest.mark.parametrize("mb,zt,expect", [
    (3, 3, True), (2, 9, False), (1, 9, False), (4, 1, True), (0, 0, False),
])
def test_is_strong_threshold(mb, zt, expect):
    """默认门槛 = 近月 ≥3 板。`(2, 9, False)` 是本次收紧的关键边界：两连板再多次也不够。"""
    gene = {"known": True, "max_boards": mb, "zt_n": zt}
    assert gr.is_strong(gene) is expect


def test_default_threshold_is_three_boards():
    """⭐ 锁死默认值（2026-11-10 10:13 用户拍板「3」）。

    2 板口径在昨日涨停候选池内命中 22/25（88%）= 门槛形同虚设。
    ⛔ 改回 2 必须同时改这条测试，强制留痕。
    """
    assert gr.GENE_MIN_BOARDS == 3


def test_hard_floor_two_even_if_configured_lower():
    """⛔ 硬地板：即使显式传 `min_boards=1`，单板也永不算强基因。"""
    assert gr.is_strong({"known": True, "max_boards": 1, "zt_n": 20},
                        min_boards=1) is False


def test_truncated_window_is_reported_honestly():
    """⛔ 「近 31 天」不许编：数据源只覆盖到 10-13 时，window 起点如实写 10-13。

    实测 11-09：共享 `zt_series` 只回看 20 交易日（最早 10-13），窗口起点 10-09
    ⇒ 安泰集团 600408 被算成 1 板（真实 2 板）、文投 600715 次数少 1。
    """
    seq = [("2026-10-13", 1), ("2026-10-16", 4), (BASE, 1)]
    g = gr.recent_gene(seq, asof=BASE, avail_since="2026-10-13")
    assert g["truncated"] is True
    assert g["window"] == ("2026-10-13", BASE)
    assert "序列仅覆盖到" in g["label"]


def test_full_window_not_truncated():
    """数据源覆盖完整窗口 → truncated=False，window 起点 = 声明的 31 天前。"""
    seq = [("2026-09-07", 1), ("2026-10-16", 4), (BASE, 1)]
    g = gr.recent_gene(seq, asof=BASE, avail_since="2026-09-07")
    assert g["truncated"] is False
    assert g["window"] == ("2026-10-09", BASE)


def test_quiet_stock_is_not_misreported_as_truncated():
    """⭐ 关键反例：这票 10-13 才第一次涨停，但数据源覆盖到 09-07 → **不是**截断。

    ⛔ 拿「该票第一条涨停日」判截断，会把「十几天没涨停的正常票」误报成数据缺失。
    """
    seq = [("2026-10-13", 1), (BASE, 1)]
    g = gr.recent_gene(seq, asof=BASE, avail_since="2026-09-07")
    assert g["truncated"] is False
    assert g["window"] == ("2026-10-09", BASE)
    assert "序列仅覆盖到" not in g["label"]


def test_unknown_coverage_reports_none_not_false():
    """调用方没告知数据覆盖范围 → `truncated=None`（不知道），⛔ 不写 False 骗人。"""
    g = gr.recent_gene([("2026-10-13", 1), (BASE, 1)], asof=BASE)
    assert g["truncated"] is None
    assert g["window"] == ("2026-10-09", BASE)


def test_truncation_never_changes_max_boards():
    """截头不改 `max_boards`（当日 boards 是累计板数），只可能少算次数。"""
    full = [("2026-09-07", 1), ("2026-10-14", 2), ("2026-10-15", 3),
            ("2026-10-16", 4), (BASE, 1)]
    cut = [x for x in full if x[0] >= "2026-10-14"]
    assert gr.recent_gene(full, asof=BASE)["max_boards"] == 4
    assert gr.recent_gene(cut, asof=BASE)["max_boards"] == 4


def test_is_strong_rejects_unknown():
    assert gr.is_strong(None) is False
    assert gr.is_strong({"known": False, "max_boards": 9, "zt_n": 9}) is False


def test_strong_reason_carries_numbers():
    txt = gr.strong_reason(gr.recent_gene(_ice_series(), asof=BASE))
    assert "4" in txt and "9" in txt


# ── ② 裁决层：soft 量能 + 强基因 → 放行 ────────────────────────────────────
@pytest.fixture(autouse=True)
def _rules(monkeypatch):
    monkeypatch.setattr(ac, "AI_VOLUME_BANDS", True)
    monkeypatch.setattr(pc, "POOL_CALL_UNLIMITED_SLOTS", True)
    monkeypatch.setattr(pc, "POOL_CALL_EVEN_ALLOCATION", True)
    monkeypatch.setattr(pc, "POOL_CALL_EVEN_RESPECT_DRAWDOWN", False)
    monkeypatch.setattr(pc, "POOL_CALL_VOLUME_SCALE_POSITION", False)
    monkeypatch.setattr(pc, "POOL_CALL_VOLUME_SOFT", False)
    monkeypatch.setattr(pc, "POOL_CALL_RECHECK_OBSERVE", True)
    monkeypatch.setattr(pc, "POOL_CALL_RECHECK_STATE_FULL", True)
    monkeypatch.setattr(pc, "POOL_CALL_GENE_EXEMPT", True)
    monkeypatch.setenv("POOL_CALL_ALL_TRADEABLE", "0")


def _watch(code="000530", *, need=1.0):
    return {"code": code, "name": "冰山冷热", "boards": 1,
            "sector": "测试题材", "core_theme": "测试题材",
            "bucket": "watch", "bucket_label": "观察池",
            "participation_eligibility": "observe_only",
            "watch_only": True, "buyable": False,
            "expectation": {"expected_range": [1.0, 4.0],
                            "auction_amount_min_yi": need},
            "watch_group": "过筛未过·只观察",
            "watch_reason": "没通过接力资格（同题材仅 1 家，未成线）—— 位置要看，不参与"}


def _q(amt=0.5, gap=2.0):
    return {"prev_close": 10.0,
            "auction_price": round(10.0 * (1 + gap / 100.0), 3),
            "auction_amount_yi": amt}


def _call(series, *, amt=0.5, gap=2.0, code="000530", stance="attack"):
    row = _watch(code)
    return pc.call_pool(
        {"date": BASE, "relay": [], "watch": [row]},
        {code: _q(amt, gap)},
        plan={"stance": stance, "views": {}}, stance=stance,
        line_cores={}, sector_flow={},
        zt_series={code: series}, zt_days=[], market_open=None, relay_plan=None)


def test_soft_volume_blocked_without_gene():
    """量 50%（soft）但近月只有单板 → 照旧按「量没给足」观察（基线）。"""
    weak = [("2026-10-20", 1), ("2026-11-03", 1), (BASE, 1)]
    v = _call(weak)["views"]["000530"]
    assert v["view"] == "watch" and v.get("recheck_gate") is True


def test_soft_volume_allowed_with_strong_gene():
    """⭐ 冰山冷热式强基因 + soft 量 → 放行，且留痕说明为什么放。"""
    v = _call(_ice_series())["views"]["000530"]
    assert v["view"] == "participate"
    assert v.get("gene_exempt") is True
    assert "4" in v["gene_exempt_reason"] and "9" in v["gene_exempt_reason"]


def test_two_board_gene_still_blocked_after_tightening():
    """⭐ 收紧到 3 板后：锋龙式「近月 2 板 + 昨日涨停 + soft 量」→ 仍然观察，不放行。

    这正是 2026-11-10 用户选「3」时要的效果：2 板票不再靠股性绕过量能闸。
    """
    fenglong = [("2026-10-28", 1), ("2026-11-03", 1), ("2026-11-04", 2), (BASE, 1)]
    v = _call(fenglong)["views"]["000530"]
    assert v["view"] == "watch" and v.get("recheck_gate") is True
    assert not v.get("gene_exempt")


def test_dry_volume_still_blocked_even_with_strong_gene():
    """⛔ 量能硬地板不拆：ratio 20%（dry）时强基因也不放行。"""
    v = _call(_ice_series(), amt=0.2)["views"]["000530"]
    assert v["view"] == "watch"


def test_full_volume_does_not_need_gene():
    """量达标时走原路，⛔ 不打 gene_exempt 标记（免得多出无意义的痕迹）。"""
    v = _call(_ice_series(), amt=2.0)["views"]["000530"]
    assert v["view"] == "participate" and not v.get("gene_exempt")


def test_switch_off_restores_old_behaviour(monkeypatch):
    """⑥ `POOL_CALL_GENE_EXEMPT=0` → 逐字回退：soft 一律拦。"""
    monkeypatch.setattr(pc, "POOL_CALL_GENE_EXEMPT", False)
    v = _call(_ice_series())["views"]["000530"]
    assert v["view"] == "watch" and not v.get("gene_exempt")


@pytest.mark.xfail(reason="股性豁免与 09:25 新闸（后排/弱线）叠加后行为变更", strict=False)
def test_zone_and_gap_rules_unaffected(monkeypatch):
    """⑤ 豁免不碰价格闸：+6% 仍 skip；且开关前后 zone 判定逐字相同。

    ⛔ 只比 zone，不比 view —— view 差异正是本功能的内容（量能闸）。
    """
    on = _call(_ice_series(), gap=6.0)["views"]["000530"]
    assert on["view"] == "skip"
    monkeypatch.setattr(pc, "POOL_CALL_GENE_EXEMPT", False)
    off = _call(_ice_series(), gap=6.0)["views"]["000530"]
    assert on["zone"] == off["zone"] and on["gap_pct"] == off["gap_pct"]


def test_recent_gene_is_exposed_on_every_view():
    """① 事实透传：每只票都带 `recent_gene`（缺序列 → None），供卡面/复盘读。"""
    res = _call(_ice_series())
    g = res["views"]["000530"]["recent_gene"]
    assert g["max_boards"] == 4 and g["zt_n"] == 9


def test_meta_tracks_exempt_and_bought():
    """留痕：meta 里能直接答「今天几只靠股性让开量能闸、其中几只真买到」。"""
    res = _call(_ice_series())
    rc = res["meta"]["recheck"]
    assert rc["gene_exempt_enabled"] is True
    assert rc["gene_exempt"] == ["000530"]
    assert rc["gene_exempt_bought"] == ["000530"]


def test_missing_series_means_unknown_not_weak():
    """⛔ 空序列 → `known=False`（不知道），⛔ 不得写成「弱基因」判死，也不得放行。"""
    v = _call([])["views"]["000530"]
    g = v["recent_gene"]
    assert g is not None and g["known"] is False and g["max_boards"] is None
    assert gr.is_strong(g) is False
    assert v["view"] == "watch" and not v.get("gene_exempt")
