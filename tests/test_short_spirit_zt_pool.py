# -*- coding: utf-8 -*-
"""全量涨停池并入候选池（2026-11-03 用户「立即处理！人工决断可改生产链路」）回归锁。

【病灶】11-03 整个上午精灵手里只有 7~16 只，真实涨停 49 只。根因链条：
  fetch_pool（东财涨幅榜）盘中反复 RemoteDisconnected → 降级 _leaders_as_pool
  （latest.json 的连板龙头 7~16 只）→ **兜底池天然不含「今天新封的首板/一字」**。
  当天竞价即封 11 只、传媒线 4 只 9:25 一字（大晟10分/华阳9/浙文9/龙版8）全在池外。

【修法】涨停池（getTopicZTPool）作为「已封板事实源」无条件并入候选池，
  且涨停池票**豁免成交额门槛**（一字锁死的成交额小是特征，不是流动性缺陷）。

⛔ 本文件锁三条边界：
  1. fetch_zt_pool 必须源头剔除 688（用户无权限）/ 北交所 / ST；
  2. 抓取失败 → []（= 不知道），⛔ 不许返回伪空结构冒充「今天没涨停」；
  3. 只有 `_zt_fact` 标记的票豁免成交额门槛，涨幅榜里未封板的小额票照旧被挡。
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("short_spirit", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


@pytest.fixture(autouse=True)
def _clear_zt_cache(tmp_path, monkeypatch):
    """模块级缓存跨用例污染 → 每个用例前后都清。

    ⚠️ 同时把 `ROUND_PATH`/`ROUND_DOWN_PATH`（涨停/跌停当下性硬门的**轮快照**）
    重定向到 tmp_path —— `build_signals` 会 `save_json(ROUND_PATH, ...)` 落盘，
    不隔离就会**覆盖生产 cache/short_spirit_round.json**。
    （2026-11-03 实测踩到：用真实票号 600892/601599 跑端到端，被生产快照判成「存量涨停」
      而整条 signals 变空 —— 既是污染源也是误判源。）
    """
    ss._ZT_POOL_CACHE.clear()
    ss.GROUP_VIEW_CACHE["view"] = None
    monkeypatch.setattr(ss, "ROUND_PATH", tmp_path / "round.json")
    monkeypatch.setattr(ss, "ROUND_DOWN_PATH", tmp_path / "round_down.json")
    yield
    ss._ZT_POOL_CACHE.clear()
    ss.GROUP_VIEW_CACHE["view"] = None


# ── 11-03 真实涨停池样本（字段名与东财 getTopicZTPool 一致）───────────────────
def _raw(code, name, lbc=2, amount_yi=0.26, fund_yi=4.55, ltsz_yi=27.0,
         fbt=92500, zbc=0, hybk="游戏Ⅱ", zdp=10.0, px=10000):
    return {
        "c": code, "n": name, "m": 0, "p": px, "zdp": zdp,
        "amount": int(amount_yi * 1e8), "ltsz": ltsz_yi * 1e8, "tshare": ltsz_yi * 1e8,
        "hs": 1.4, "lbc": lbc, "fbt": fbt, "lbt": fbt, "zbc": zbc,
        "fund": int(fund_yi * 1e8), "hybk": hybk, "zttj": {"days": 7, "ct": lbc},
    }


def _mock_http(rows, monkeypatch):
    monkeypatch.setattr(ss, "http_get",
                        lambda *a, **k: {"data": {"tc": len(rows), "pool": rows}})


def _cfg(**over):
    base = {
        "min_pct": 3.0, "min_market_cap_yi": 100, "amount_morning_yi": 5.0,
        "amount_afternoon_yi": 10.0, "max_new_stock_days": 365,
        "top_per_sector": 2, "max_signals": 10,
        "weight": {"pct": 0.4, "amount": 0.2, "resonance": 0.3, "flow": 0.1},
    }
    base.update(over)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# ① fetch_zt_pool：解析 / 剔除 / 失败语义
# ══════════════════════════════════════════════════════════════════════════════
def test_zt_pool_parses_full_fields(monkeypatch):
    """大晟文化 11-03 实录：0.26 亿成交 / 4.55 亿封单 / 流通 27 亿 / 9:25 一字 / 2 板。"""
    _mock_http([_raw("600892", "大晟文化")], monkeypatch)
    rows = ss.fetch_zt_pool()
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "600892" and r["name"] == "大晟文化"
    assert r["amount_yi"] == 0.26
    assert r["float_cap_yi"] == 27.0
    assert r["industry"] == "游戏Ⅱ"
    assert r[ss.ZT_FACT_KEY] is True
    rich = r["_zt_rich"]
    assert rich["seal_amt"] == 4.55
    assert rich["fbt"] == 92500
    assert rich["boards"] == 2
    assert rich["lt_mktcap"] == 27.0


def test_zt_pool_prev_close_backs_out_limit_price(monkeypatch):
    """prev_close 反推必须让 signal_type 判「涨停」，不是「炸板·回落」。"""
    _mock_http([_raw("600892", "大晟文化", px=10000, zdp=10.0)], monkeypatch)
    r = ss.fetch_zt_pool()[0]
    assert abs(r["prev_close"] - 10000 / 1000 / 1.1) < 0.01
    assert ss.signal_type(r)[0] == "涨停"


@pytest.mark.parametrize("code,name,why", [
    ("688001", "华兴源创", "科创板无权限"),
    ("830799", "艾融软件", "北交所"),
    ("430047", "诺思兰德", "北交所"),
    ("920001", "万达轴承", "北交所新号段"),
    ("002001", "*ST某某", "ST股"),
    ("002002", "某某退", "退市"),
])
def test_zt_pool_drops_forbidden_at_source(monkeypatch, code, name, why):
    """⛔ 源头剔除，不吃下游过滤的运气（用户纪律：无 688 权限 / 不看 ST）。"""
    _mock_http([_raw(code, name)], monkeypatch)
    assert ss.fetch_zt_pool() == [], why


def test_zt_pool_failure_returns_empty_not_fake(monkeypatch):
    """抓取失败 = 「不知道」→ []。⛔ 绝不返回伪空结构冒充「今天没涨停」。"""
    def _boom(*a, **k):
        raise OSError("RemoteDisconnected")
    monkeypatch.setattr(ss, "http_get", _boom)
    assert ss.fetch_zt_pool() == []
    assert ss.FETCH_STATUS["zt_pool"]["ok"] is False
    assert "RemoteDisconnected" in ss.FETCH_STATUS["zt_pool"]["err"]


def test_zt_pool_switch_off_returns_empty(monkeypatch):
    """可逆开关：ENABLE_SPIRIT_ZT_POOL=0 → 完全不联网、返回空（回到改动前行为）。"""
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return {"data": {"pool": [_raw("600892", "大晟文化")]}}
    monkeypatch.setattr(ss, "http_get", _spy)
    assert ss.fetch_zt_pool(enabled=False) == []
    assert called["n"] == 0, "开关关闭时不得发起请求"


def test_zt_pool_cache_avoids_duplicate_requests(monkeypatch):
    """盘中每轮都调 → 必须命中 TTL 缓存，否则等于对东财压测（11-03 已被封过一次）。"""
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return {"data": {"pool": [_raw("600892", "大晟文化")]}}
    monkeypatch.setattr(ss, "http_get", _spy)
    ss.fetch_zt_pool()
    ss.fetch_zt_pool()
    ss.fetch_zt_pool()
    assert called["n"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# ② _merge_zt_pool：去重 + 富字段
# ══════════════════════════════════════════════════════════════════════════════
def test_merge_keeps_single_row_per_code(monkeypatch):
    """同一只票在涨幅榜与涨停池各出现一次 → 合并后只能有一条（防重复推送）。"""
    _mock_http([_raw("600892", "大晟文化")], monkeypatch)
    zt = ss.fetch_zt_pool()
    pool = [{"code": "600892", "name": "大晟文化", "pct": 10.0, "amount_yi": 0.26,
             "market_cap_yi": 27.0, "float_cap_yi": 27.0, "list_date": None,
             "prev_close": 9.09, "main_flow_yi": -0.3, "industry": "游戏Ⅱ",
             "vol_ratio": 1.1, "turnover": 1.4}]
    merged = ss._merge_zt_pool(pool, zt)
    assert len(merged) == 1
    assert merged[0][ss.ZT_FACT_KEY] is True          # 打上事实标记
    assert merged[0]["main_flow_yi"] == -0.3          # ⛔ 涨幅榜的主力净额不被覆盖


def test_merge_appends_new_sealed_board(monkeypatch):
    """涨幅榜里没有、只在涨停池里的票（今天新封的一字）必须被补进池 —— 这就是本次修复的核心。"""
    _mock_http([_raw("600892", "大晟文化")], monkeypatch)
    zt = ss.fetch_zt_pool()
    merged = ss._merge_zt_pool([], zt)
    assert [r["code"] for r in merged] == ["600892"]
    assert merged[0][ss.ZT_FACT_KEY] is True


def test_merge_empty_zt_is_noop():
    """涨停池空（抓取失败）→ 原池原样返回，行为与改动前一致。"""
    pool = [{"code": "600000"}]
    assert ss._merge_zt_pool(pool, []) is pool


# ══════════════════════════════════════════════════════════════════════════════
# ③ noise_filter：成交额豁免只给「已确认封板」的票
# ══════════════════════════════════════════════════════════════════════════════
def _row(code="600892", name="大晟文化", pct=10.0, amt=0.26, mc=27.0, **kw):
    r = {"code": code, "name": name, "pct": pct, "amount_yi": amt,
         "market_cap_yi": mc, "float_cap_yi": mc, "list_date": None,
         "prev_close": 9.09, "price": 10.0, "main_flow_yi": 0.0,
         "industry": "游戏Ⅱ", "vol_ratio": 1.0, "turnover": 1.4}
    r.update(kw)
    return r


def test_sealed_fact_exempt_from_turnover_floor():
    """涨停池事实票：成交 0.26 亿 < 5 亿门槛，但已确认封板 → 必须保留。

    11-03 实证：大晟文化正是这样被挡在池外的（0.26 亿 / 封单占流通 16.9%）。
    """
    row = _row()
    row[ss.ZT_FACT_KEY] = True
    keep = ss.noise_filter([row], _cfg(), __import__("datetime").datetime.now(),
                           boards_map={"600892": 2})
    assert [r["code"] for r in keep] == ["600892"]


def test_unsealed_small_amount_still_dropped():
    """⛔ 反向断言：没有事实标记的同样数字 → 照旧被成交额门槛挡（不许借豁免开后门）。"""
    row = _row()
    keep = ss.noise_filter([row], _cfg(), __import__("datetime").datetime.now(),
                           boards_map={"600892": 2})
    assert keep == []


def test_sealed_fact_exempt_switch_off():
    """ENABLE_SPIRIT_ZT_AMT_EXEMPT=False → 涨停池票也回成交额门槛（可逆）。"""
    row = _row()
    row[ss.ZT_FACT_KEY] = True
    old = ss.ENABLE_SPIRIT_ZT_AMT_EXEMPT
    ss.ENABLE_SPIRIT_ZT_AMT_EXEMPT = False
    try:
        keep = ss.noise_filter([row], _cfg(), __import__("datetime").datetime.now(),
                               boards_map={"600892": 2})
        assert keep == []
    finally:
        ss.ENABLE_SPIRIT_ZT_AMT_EXEMPT = old


def test_sealed_fact_still_subject_to_st_and_pct_floor():
    """豁免只针对成交额：ST / 涨幅不足 仍被挡（边界不许扩大）。"""
    st_row = _row(name="*ST大晟")
    st_row[ss.ZT_FACT_KEY] = True
    weak = _row(code="600893", name="某票", pct=1.2)
    weak[ss.ZT_FACT_KEY] = True
    keep = ss.noise_filter([st_row, weak], _cfg(), __import__("datetime").datetime.now(),
                           boards_map={"600892": 2, "600893": 1})
    assert keep == []


# ══════════════════════════════════════════════════════════════════════════════
# ④ 端到端：涨幅榜挂掉的那一轮，涨停池必须顶上
# ══════════════════════════════════════════════════════════════════════════════
def test_build_signals_survives_quote_board_outage(monkeypatch):
    """11-03 场景重现：涨幅榜全挂 + leaders 兜底只有老龙头，
    传媒一字（涨停池里）仍必须进池 —— 这是「早上没发现传媒爆发」的直接修复。"""
    monkeypatch.setattr(ss, "fetch_pool", lambda cfg: [])
    monkeypatch.setattr(ss, "_leaders_as_pool", lambda d: [])
    _mock_http([
        _raw("600892", "大晟文化", lbc=2, amount_yi=0.26, fund_yi=4.55, ltsz_yi=27.0),
        _raw("601599", "浙文影业", lbc=2, amount_yi=0.79, fund_yi=2.59, ltsz_yi=44.0,
             hybk="纺织制造"),
    ], monkeypatch)
    d = {"top5": [], "leaders": [], "sector_risk": {}}
    signals, _sess, _nkeep, npool = ss.build_signals(
        d, __import__("datetime").datetime.now(), _cfg())
    codes = {s["code"] for s in signals}
    assert {"600892", "601599"} <= codes
    assert npool >= 2
    # 全池视图：供板块联动聚合，⛔ 不受 max_signals 截断影响
    gv = ss.GROUP_VIEW_CACHE.get("view")
    assert gv is not None and len(gv) >= len(signals)


def test_group_view_bypasses_top_per_sector_cap(monkeypatch):
    """2026-11-03 13:07 实录：锂电 8 家，卡面「🔥 板块联动」只显示「电池 2只」。

    根因：`top_per_sector=2`（**推送名额**规则，防一个板块刷屏）被用在了
    板块联动的数据源上 —— 一条 8 家的线被砍成 2 家，在卡面上等于不存在。
    修法：全池视图取 `keep`（未做每行业截断），推送仍走 `core`。

    ⛔ 本用例锁死两侧：推送口径**不许**因此放宽（同行业仍 ≤2 只），
       家数口径**不许**再被名额规则影响（视图里 5 只全在）。
    """
    rows = [_raw(f"60000{i}", f"锂电{i}", lbc=1, amount_yi=2.0, fund_yi=1.0,
                 ltsz_yi=40.0, fbt=94000 + i, hybk="电池") for i in range(5)]
    monkeypatch.setattr(ss, "fetch_pool", lambda cfg: [])
    monkeypatch.setattr(ss, "_leaders_as_pool", lambda d: [])
    _mock_http(rows, monkeypatch)

    d = {"top5": [], "leaders": [], "sector_risk": {}}
    signals, _sess, nkeep, _npool = ss.build_signals(
        d, __import__("datetime").datetime.now(), _cfg())

    assert nkeep == 5                                    # 过滤后确有 5 只
    assert len([s for s in signals if s["sector"] == "电池"]) <= 2   # 推送名额未放宽
    gv = ss.GROUP_VIEW_CACHE.get("view") or []
    assert len([s for s in gv if s["sector"] == "电池"]) == 5        # 家数看得全
    # 聚合出来必须是「5 只」而非「2 只」
    top = ss._sector_group_summary(gv)
    assert top[0][0] == "锂电池" and top[0][1] == 5 and top[0][2] == 5

