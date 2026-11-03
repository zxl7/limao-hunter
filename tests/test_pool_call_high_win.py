# -*- coding: utf-8 -*-
"""高胜率优先三开关回归锁 —— 2026-11-02 用户「打开！高胜率优先！可以自己迭代，有序优化」。

锁定（依据 `scripts/relay_winrate.py`：31 个交易日 / 218 个可执行样本）：
  ① `gap_to_top` 计算：全市场最高板 = 0；拿不到 market_max_boards → None（⛔ 不猜）
  ② 🎯 身位优先排序：最高板排在小弟前面，即便小弟板数相同/预案更靠前
     · 关闭 `POOL_CALL_TOP_BOARD_PRIORITY` → 回到旧顺序（板数优先），行为可逆
  ③ 🎯 低开例外：**只对最高板**放行，且必须跌超 3%、仓位上限 0.5
     · 非最高板低开 → 仍不买（⛔ 该档在回测验证段只有 29.2%，属过拟合）
     · 关闭 `POOL_CALL_LOW_OPEN_TOP_EXCEPTION` → 最高板低开也回观察
  ④ 🎯 量能软化：量能不足但未破地板 → 降半仓放行；破地板 → 仍否决
     · 关闭 `POOL_CALL_VOLUME_SOFT` → 恢复「量不够即观察」

全隔离：不读真实 cache / 不联网。
"""
import sys
from contextlib import contextmanager
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

from core import pool_call  # noqa: E402


@contextmanager
def flags(*, top=None, low=None, vol=None, leader=None):
    """临时改四个开关（⛔ 测试不依赖默认值：默认值会随实盘证据变，断言必须自己说清开的是哪套）。"""
    saved = (pool_call.POOL_CALL_TOP_BOARD_PRIORITY,
             pool_call.POOL_CALL_LOW_OPEN_TOP_EXCEPTION,
             pool_call.POOL_CALL_VOLUME_SOFT,
             pool_call.POOL_CALL_LEADER_ONLY)
    try:
        if top is not None:
            pool_call.POOL_CALL_TOP_BOARD_PRIORITY = top
        if low is not None:
            pool_call.POOL_CALL_LOW_OPEN_TOP_EXCEPTION = low
        if vol is not None:
            pool_call.POOL_CALL_VOLUME_SOFT = vol
        if leader is not None:
            pool_call.POOL_CALL_LEADER_ONLY = leader
        yield
    finally:
        (pool_call.POOL_CALL_TOP_BOARD_PRIORITY,
         pool_call.POOL_CALL_LOW_OPEN_TOP_EXCEPTION,
         pool_call.POOL_CALL_VOLUME_SOFT,
         pool_call.POOL_CALL_LEADER_ONLY) = saved


# ── 夹具 ────────────────────────────────────────────────────────────
def _cand(code, name, boards=2, sector="测试题材", exp=(1.0, 3.0), need=1.0, **kw):
    d = {"code": code, "name": name, "boards": boards, "sector": sector,
         "bucket_label": "接力池",
         "expectation": {"expected_range": list(exp),
                         "auction_amount_min_yi": need}}
    d.update(kw)
    return d


def _q(prev, gap_pct, amt=20.0):
    return {"prev_close": prev,
            "auction_price": round(prev * (1 + gap_pct / 100.0), 3),
            "auction_amount_yi": amt}


def _run(relay, quotes, *, mmb=None, **kw):
    stance = kw.pop("stance", "attack")
    return pool_call.call_pool(
        {"date": "2026-11-02", "relay": relay, "watch": []},
        quotes,
        stance=stance,
        line_cores={},          # ⛔ 不读盘：显式空表 → 不做同线核心识别
        zt_rows=[],
        zt_series={},
        zt_days=[],
        sector_flow={},
        market_max_boards=mmb,
        **kw)


# ── ① gap_to_top ────────────────────────────────────────────────────
def test_default_flags_locked():
    """⛔ 默认值锁定：只有身位优先生效，低开例外/量能软化默认关闭。

    依据 `scripts/pool_call_replay.py`（31 个交易日真实链路回放）：
      A 旧口径     日均 +2.79% / 日胜率 63.6%
      C 低开例外   日均 +2.16%（只触发 1 次：10-22 东安动力 −4.73%）
      D 量能软化   日均 +1.30%（10-21 把南威 +9.27% 顶成科森 −2.04%）
    → 改默认值前先重跑回放，别凭纸面统计开会亏钱的闸。
    """
    assert pool_call.POOL_CALL_TOP_BOARD_PRIORITY is True
    assert pool_call.POOL_CALL_LOW_OPEN_TOP_EXCEPTION is False
    assert pool_call.POOL_CALL_VOLUME_SOFT is False


def test_gap_to_top_is_zero_for_top_board():
    """全市场最高板（6 板）→ gap_to_top=0；3 板小弟 → 差 3 板。"""
    res = _run([_cand("000001", "龙头", boards=6), _cand("000002", "小弟", boards=3)],
               {"000001": _q(10.0, 1.0), "000002": _q(10.0, 1.0)}, mmb=6)
    assert res["views"]["000001"]["gap_to_top"] == 0
    assert res["views"]["000002"]["gap_to_top"] == 3
    assert res["views"]["000001"]["market_max_boards"] == 6


def test_gap_to_top_none_when_mmb_unknown():
    """拿不到市场最高板 → gap_to_top=None（⛔ 不猜成「最高板」）。"""
    res = _run([_cand("000001", "甲票", boards=6)],
               {"000001": _q(10.0, 1.0)}, mmb=None)
    assert res["views"]["000001"]["gap_to_top"] is None


# ── ② 身位优先排序 ───────────────────────────────────────────────────
def test_top_board_ranked_before_follower():
    """名额只有 1 只时，最高板买到手，小弟只能看着。"""
    res = _run([_cand("000001", "小弟", boards=3), _cand("000002", "龙头", boards=6)],
               {"000001": _q(10.0, 1.0), "000002": _q(10.0, 1.0)},
               mmb=6, stance="defend")            # defend → 名额 1
    v_top = res["views"]["000002"]
    v_small = res["views"]["000001"]
    assert v_top["view"] == "participate", v_top
    assert v_small["view"] == "watch", v_small
    assert v_top["quota_rank"] == 1


def test_top_board_priority_switch_reversible():
    """关掉身位优先 → 名额让给「预案重点看」的小弟（旧口径可逐字回退）。

    ⚠️ 小弟用 2 板：3~4 板会被既有「中位股闸」先拦（两头不靠、不做），
       与本开关无关，别把两道闸混在一个断言里。
    """
    plan = {"stance": "defend", "views": {"000001": {"bias": "focus"}}}
    relay = [_cand("000001", "小弟", boards=2), _cand("000002", "龙头", boards=6)]
    qs = {"000001": _q(10.0, 1.0), "000002": _q(10.0, 1.0)}

    # ⛔ 身位闸显式关掉：本用例只测「排序键」这一个变量，
    #    开着会把小弟（身位差 4 板）提前拦掉，排序层就没得比了。
    with flags(leader=False):
        on = _run(relay, qs, mmb=6, plan=plan, stance="defend")
        assert on["views"]["000002"]["view"] == "participate"

        with flags(top=False):
            off = _run(relay, qs, mmb=6, plan=plan, stance="defend")
        assert off["views"]["000001"]["view"] == "participate", off["views"]["000001"]

# ── ⑤ 身位闸（后排一律不做）────────────────────────────────────────
def test_leader_only_blocks_backrow():
    """身位差 ≥2 板 → 不买、不占名额，理由要讲人话（含历史数字）。

    依据：红开可买口径下后排 n=58 胜率 39.7%/均 −1.37%，且封板质量救不了后排。
    """
    with flags(leader=True):
        res = _run([_cand("000001", "后排", boards=2)], {"000001": _q(10.0, 1.0)},
                   mmb=6, stance="attack")
    v = res["views"]["000001"]
    assert v["view"] == "watch", v
    assert v.get("leader_only_gate") is True
    assert "后排不做" in v["reason"]


def test_leader_only_allows_top_and_near_top():
    """最高板 / 差 1 板 → 照常参与（不误杀）。"""
    with flags(leader=True):
        res = _run([_cand("000001", "龙头", boards=6), _cand("000002", "次高", boards=5)],
                   {"000001": _q(10.0, 1.0), "000002": _q(10.0, 1.0)},
                   mmb=6, stance="attack")
    assert res["views"]["000001"]["view"] == "participate"
    assert res["views"]["000002"]["view"] == "participate"


def test_leader_only_empty_pool_is_legal():
    """全是后排 → 整池空仓（用户「没有就空」，⛔ 不许为了出手降门槛）。"""
    with flags(leader=True):
        res = _run([_cand("000001", "甲", boards=2), _cand("000002", "乙", boards=3)],
                   {"000001": _q(10.0, 1.0), "000002": _q(10.0, 1.0)},
                   mmb=6, stance="attack")
    assert all(v["view"] != "participate" for v in res["views"].values())


def test_leader_only_switch_reversible():
    """关掉 → 后排恢复参与（可逐字回退）。"""
    relay = [_cand("000001", "后排", boards=2)]
    qs = {"000001": _q(10.0, 1.0)}
    with flags(leader=False):
        assert _run(relay, qs, mmb=6)["views"]["000001"]["view"] == "participate"
    with flags(leader=True):
        assert _run(relay, qs, mmb=6)["views"]["000001"]["view"] == "watch"


def test_leader_only_no_market_max_no_verdict():
    """拿不到全市场最高板 → 不裁决（缺数据≠不利证据，禁猜）。"""
    with flags(leader=True):
        res = _run([_cand("000001", "甲", boards=2)], {"000001": _q(10.0, 1.0)}, mmb=None)
    assert res["views"]["000001"]["view"] == "participate"
    assert not res["views"]["000001"].get("leader_only_gate")


# ── ③ 低开例外 ──────────────────────────────────────────────────────
def test_top_board_low_open_allowed_half_position():
    """最高板跌超 3% 开盘 → 放行，但只给半仓，且理由要说人话。

    ⚠️ 该开关**默认关闭**（真实链路回放只触发 1 次且亏），本测试锁定的是「打开后」的行为。
    """
    with flags(top=True, low=True, vol=False):
        res = _run([_cand("000001", "龙头", boards=6)],
                   {"000001": _q(10.0, -4.0)}, mmb=6, stance="attack")
    v = res["views"]["000001"]
    assert v["view"] == "participate", v
    assert v["low_open_top"] is True
    assert v["position_cap"] == pool_call.LOW_OPEN_TOP_POSITION
    assert "半仓" in v["reason"]


def test_follower_low_open_still_rejected():
    """⛔ 非最高板低开 → 不买（该档验证段只有 29.2%，属过拟合，永不放开）。"""
    with flags(top=True, low=True, vol=False):
        res = _run([_cand("000001", "小弟", boards=3)],
                   {"000001": _q(10.0, -4.0)}, mmb=6, stance="attack")
        v = res["views"]["000001"]
        assert v["view"] in ("watch", "skip"), v
        assert not v.get("low_open_top")


def test_top_board_shallow_low_open_not_exempt():
    """最高板只低开 2%（未跌超 3%）→ 不构成例外，仍不买。"""
    with flags(top=True, low=True, vol=False):
        res = _run([_cand("000001", "龙头", boards=6)],
                   {"000001": _q(10.0, -2.0)}, mmb=6, stance="attack")
        v = res["views"]["000001"]
        assert v["view"] in ("watch", "skip"), v
        assert not v.get("low_open_top")


def test_low_open_exception_switch_reversible():
    """关掉低开例外 → 最高板低开也回到观察（默认就是关）。"""
    qs = {"000001": _q(10.0, -4.0)}
    relay = [_cand("000001", "龙头", boards=6)]
    with flags(low=True, vol=False):
        assert _run(relay, qs, mmb=6)["views"]["000001"]["view"] == "participate"
    with flags(low=False, vol=False):
        assert _run(relay, qs, mmb=6)["views"]["000001"]["view"] in ("watch", "skip")


# ── ④ 量能软化 ──────────────────────────────────────────────────────
def test_weak_volume_above_floor_goes_half_position():
    """量能只到计划的一半（未破 35% 地板）→ 半仓放行，不再直接观察。

    ⚠️ 该开关**默认关闭**（回放把 10-21 南威 +9.27% 换成科森 −2.04%），此处锁「打开后」行为。
    """
    with flags(low=False, vol=True):
        res = _run([_cand("000001", "甲票", boards=2, need=4.0)],
                   {"000001": _q(10.0, 1.0, amt=2.0)})       # 2.0/4.0 = 50% < 70%
    v = res["views"]["000001"]
    assert v["view"] == "participate", v
    assert v["volume_soft"] is True
    assert v["position_cap"] == pool_call.VOLUME_SOFT_POSITION
    assert "半仓" in v["reason"]


def test_volume_below_floor_still_rejected():
    """量能跌破地板（20% < 35%）→ 仍按「像诱多」否决。"""
    with flags(low=False, vol=True):
        res = _run([_cand("000001", "甲票", boards=2, need=4.0)],
                   {"000001": _q(10.0, 1.0, amt=0.8)})       # 0.8/4.0 = 20%
        v = res["views"]["000001"]
        assert v["view"] == "watch", v
        assert not v.get("volume_soft")


def test_volume_soft_switch_reversible():
    """关掉量能软化 → 恢复「量不够即观察」（默认就是关）。"""
    relay = [_cand("000001", "甲票", boards=2, need=4.0)]
    qs = {"000001": _q(10.0, 1.0, amt=2.0)}
    with flags(vol=True):
        assert _run(relay, qs)["views"]["000001"]["view"] == "participate"
    with flags(vol=False):
        assert _run(relay, qs)["views"]["000001"]["view"] == "watch"


def test_low_open_and_weak_volume_caps_at_half():
    """低开例外 ∧ 量能偏弱 同时命中 → 仓位上限取更严的那个，不叠加放大。"""
    with flags(top=True, low=True, vol=True):
        res = _run([_cand("000001", "龙头", boards=6, need=4.0)],
                   {"000001": _q(10.0, -4.0, amt=2.0)}, mmb=6)
    v = res["views"]["000001"]
    assert v["view"] == "participate", v
    assert v["position_cap"] <= 0.5


# ── ⑤ 选股卡 forecast 接进竞价裁决（2026-11-02 用户「两张卡结合」）────────────
def _fc(verdict="只观察", boards=6, promo=12.5, band="高开0~3%", win=62.0, n=20):
    """造一份和 `core.relay_forecast` 同形的 forecast。"""
    return {"gap_median": 2.5, "p_over5": 29, "verdict": verdict,
            "boards": boards, "promo_board": promo,
            "by_band": ({band: {"n": n, "win": win, "win_lb": 40.0, "avg": 1.85}}
                        if band else {})}


def test_forecast_band_note_uses_actual_gap():
    """竞价卡必须把「开在这个幅度、历史上赚不赚」写进买入理由（两张卡共用一套数字）。"""
    res = _run([_cand("000001", "龙头", boards=6, forecast=_fc())],
               {"000001": _q(10.0, 2.0)}, mmb=6)
    v = res["views"]["000001"]
    assert v["view"] == "participate", v
    assert "历史上" in v["reason"] and "62%" in v["reason"]
    assert v["forecast_band"]["band"] == "高开0~3%"


def test_forecast_band_note_absent_for_old_candidates():
    """旧候选文件没有 forecast → 理由照旧、不报错（⛔ 不许因此改变裁决）。"""
    res = _run([_cand("000001", "甲票", boards=2)], {"000001": _q(10.0, 2.0)})
    v = res["views"]["000001"]
    assert v["view"] == "participate", v
    assert "历史上" not in v["reason"]
    assert not v.get("forecast_band")


def test_forecast_gate_default_off():
    """⑪c 崖位闸默认关：6 板 + 晋级率 12.5% 仍按常规参与（只留痕，不改裁决）。"""
    assert pool_call.POOL_CALL_FORECAST_GATE is False
    res = _run([_cand("000001", "龙头", boards=6, forecast=_fc())],
               {"000001": _q(10.0, 2.0)}, mmb=6)
    assert res["views"]["000001"]["view"] == "participate"


def test_forecast_gate_blocks_cliff_when_on():
    """打开崖位闸 → 6 板且该档晋级率 <25% 只观察，理由说人话。"""
    old = pool_call.POOL_CALL_FORECAST_GATE
    try:
        pool_call.POOL_CALL_FORECAST_GATE = True
        res = _run([_cand("000001", "龙头", boards=6, forecast=_fc())],
                   {"000001": _q(10.0, 2.0)}, mmb=6)
        v = res["views"]["000001"]
        assert v["view"] == "watch", v
        assert v.get("forecast_gate") is True
        assert "晋级" in v["reason"]
    finally:
        pool_call.POOL_CALL_FORECAST_GATE = old


def test_forecast_gate_only_handles_cliff_not_backrow():
    """崖位闸只管「≥6板且晋级率低」；后排是身位闸的事，⛔ 同一件事不判两次。"""
    old = pool_call.POOL_CALL_FORECAST_GATE
    try:
        pool_call.POOL_CALL_FORECAST_GATE = True
        with flags(leader=False):        # 关掉身位闸，单看崖位闸是否越权
            res = _run([_cand("000001", "小弟", boards=2,
                              forecast=_fc(verdict="放弃", boards=2, promo=10.0))],
                       {"000001": _q(10.0, 2.0)}, mmb=6)
        v = res["views"]["000001"]
        assert v["view"] == "participate", v
        assert not v.get("forecast_gate")
    finally:
        pool_call.POOL_CALL_FORECAST_GATE = old


def test_forecast_gate_missing_data_never_blocks():
    """forecast 缺失 / 带 error → 闸门一律放行（⛔ 不拿「不知道」当「不行」）。"""
    old = pool_call.POOL_CALL_FORECAST_GATE
    try:
        pool_call.POOL_CALL_FORECAST_GATE = True
        for fc in (None, {"error": "boom"}, {"boards": 6, "promo_board": None}):
            res = _run([_cand("000001", "龙头", boards=6, **({"forecast": fc} if fc else {}))],
                       {"000001": _q(10.0, 2.0)}, mmb=6)
            assert res["views"]["000001"]["view"] == "participate", fc
    finally:
        pool_call.POOL_CALL_FORECAST_GATE = old
