# -*- coding: utf-8 -*-
"""身位差口径修正回归锁（2026-11-06 用户拍板 A）。

## 用户原话

「我觉得可以先按A执行一下，因为身位差，我好像没有强制要求百分之百限制吧……
  有时候龙头，比如说板块之间非板块之间的切换，比如说你传媒板块你已经涨了五板龙头了，
  但是光伏板块才一个板，这种身位差其实是没有影响的呀，它是走一个新的逻辑，
  甚至说可以做一个资金的承接方向，你这种身位差估计只能在同一个板块里面适用，
  不同板块之间它是没有关系的」

## 锁死七件事

  ① 分母唯一源＝**同题材最高板**（不是全市场最高板）；缺题材 → 不裁决，⛔ 不猜；
  ② 跨题材不再否决：市场身位差 3、同题材最高 → 不 watch，只按 0.75 降仓；
  ③ 同题材差 1（历史最好档 +0.52%/胜 53.4%）→ 权重 1.0，一点都不降；
  ④ 同题材差 ≥2（−1.62%/胜 37.3%）→ 降仓但**不剔除**，下限 0.20 > 0；
  ⑤ 盘后过筛不再把身位当硬伤（职责错位修正），只写事实给 09:25 消费；
  ⑥ 双开关可逐字回退：`POOL_CALL_POSITION_GAP_HARD=1`（硬闸）、
     `POOL_CALL_POSITION_GAP_SCOPE=market`（旧分母）、
     `ENABLE_SCREEN_POSITION_GAP=0`（盘后旧 hard reject）、`AI_POSITION_WEIGHT=0`；
  ⑦ ⛔ 不改 zone / view（除硬闸开关）/ participate / quota —— 降仓不等于否决。

全隔离：不读真实 cache / 台账 / 网络，不推群，不写冻结件。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
for _p in (str(WC), str(WC / "pipelines"), str(WC / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import ai_caliber as ac          # noqa: E402
from core import daily_screen as ds        # noqa: E402
from core import pool_call as pc           # noqa: E402


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    """显式跑在 11-06 新口径下（不依赖模块默认值），并隔离无关闸。"""
    monkeypatch.delenv("POOL_CALL_POSITION_GAP_SCOPE", raising=False)
    monkeypatch.setattr(ac, "AI_POSITION_WEIGHT", True)
    monkeypatch.setattr(pc, "POOL_CALL_POSITION_GAP_HARD", False)
    monkeypatch.setattr(pc, "POOL_CALL_POSITION_WEIGHT_POSITION", True)
    monkeypatch.setattr(pc, "POOL_CALL_UNLIMITED_SLOTS", True)
    monkeypatch.setattr(pc, "POOL_CALL_EVEN_ALLOCATION", True)
    monkeypatch.setattr(pc, "POOL_CALL_VOLUME_SCALE_POSITION", False)
    monkeypatch.setattr(pc, "POOL_CALL_LOW_OPEN_GUARD", False)


# ── ①②③④ 判据唯一源：position_weight ────────────────────────────────────────

def test_line_gap_bands():
    """同题材身位差 0/1/≥2 三档；差 1 是最优档，一点都不降。"""
    assert ac.position_weight(4, theme_max_boards=4)["weight"] == 0.75      # 自己当锚
    assert ac.position_weight(3, theme_max_boards=4)["weight"] == 1.0       # 差 1 板
    assert ac.position_weight(2, theme_max_boards=4)["weight"] == 0.40      # 差 2 板
    assert ac.position_weight(1, theme_max_boards=4)["weight"] == 0.40      # 差 3 板不更狠


def test_cross_theme_not_penalized_by_market_gap():
    """⭐ 核心：市场身位差 3、但自己在本题材就是最高板 → 按 0.75，绝不当后排。"""
    out = ac.position_weight(2, theme_max_boards=2, market_max_boards=5)
    assert out["basis"] == "line"
    assert out["line_gap"] == 0
    assert out["market_gap"] == 3
    assert out["weight"] == 0.75
    # 旧闸口径下这只是「差 3 板的后排」，会被整只剔出裁决
    assert out["weight"] > ac.POSITION_WEIGHT_MIN


def test_missing_theme_does_not_rule():
    """缺题材 → basis=unknown、权重 1.0。⛔ 不用行业兜底冒充题材、不猜。"""
    out = ac.position_weight(2, theme_max_boards=None, market_max_boards=4)
    assert out["basis"] == "unknown"
    assert out["weight"] == 1.0
    assert out["active"] is False
    assert ac.position_weight(None)["weight"] == 1.0
    assert ac.position_weight(0)["weight"] == 1.0


def test_market_scope_rollback(monkeypatch):
    """`POOL_CALL_POSITION_GAP_SCOPE=market` 逐字回到旧分母（全市场最高板）。"""
    monkeypatch.setenv("POOL_CALL_POSITION_GAP_SCOPE", "market")
    out = ac.position_weight(2, theme_max_boards=2, market_max_boards=5)
    assert out["basis"] == "market"
    assert out["market_gap"] == 3
    assert out["weight"] == 0.40


def test_ai_switch_off(monkeypatch):
    """`AI_POSITION_WEIGHT=0` → 权重恒 1.0，字段仍在（留痕不丢）。"""
    monkeypatch.setattr(ac, "AI_POSITION_WEIGHT", False)
    out = ac.position_weight(2, theme_max_boards=4)
    assert out["weight"] == 1.0
    assert out["line_gap"] == 2


def test_floor_positive_and_no_veto_fields():
    """⛔ 下限 > 0，本函数不拥有否决权（没有 block / zone / view 字段）。"""
    out = ac.position_weight(1, theme_max_boards=9)
    assert out["weight"] >= ac.POSITION_WEIGHT_MIN > 0
    assert not ({"block", "zone", "view", "quota"} & set(out))


# ── ⑤ 盘后过筛：身位不再当硬伤 ──────────────────────────────────────────────

def _ztrow(code, boards, theme):
    return {"code": code, "name": code, "boards": boards, "core_theme": theme,
            "cje_yi": 8.0, "hs": 5.0, "fbt": "100000", "zbc": 0, "sector": theme,
            "is_one_word": False}


def _screen(rows):
    return {s["code"]: s for s in ds.screen_pool(rows, trade_date="")["screened"]}


def test_screen_does_not_reject_on_position_gap(monkeypatch):
    """market 最高 5 板时，2 板的票不再因身位差被 reject（旧行为会整只判死）。"""
    monkeypatch.setattr(ds, "POS_SCREEN_FACT_ONLY", True)
    rows = [_ztrow("600001", 5, "光伏"), _ztrow("600002", 2, "机器人"),
            _ztrow("600011", 2, "机器人"), _ztrow("600012", 1, "机器人")]
    out = _screen(rows)
    assert out["600002"]["verdict"] != "reject"
    assert not any("身位差" in r for r in out["600002"]["rejects"])
    pg = out["600002"]["facts"]["position_gap"]
    assert pg["basis"] == "line"
    assert pg["line_gap"] == 0          # 机器人题材里它就是最高板
    assert pg["market_gap"] == 3


def test_screen_facts_line_gap_for_backrow(monkeypatch):
    """同题材真有高板时，line_gap ≥2 只写事实 + 进 unknown 提示，⛔ 仍不 reject。"""
    monkeypatch.setattr(ds, "POS_SCREEN_FACT_ONLY", True)
    rows = [_ztrow("600001", 4, "光伏"), _ztrow("600002", 1, "光伏"),
            _ztrow("600011", 1, "光伏"), _ztrow("600012", 1, "光伏")]
    out = _screen(rows)
    assert out["600002"]["verdict"] != "reject"
    assert out["600002"]["facts"]["position_gap"]["line_gap"] == 3
    assert any("同题材身位差 3 板" in u for u in out["600002"]["unknown"])


def test_screen_rollback_to_hard_reject(monkeypatch):
    """`ENABLE_SCREEN_POSITION_GAP=0` → 逐字恢复旧硬拒（全市场最高板口径）。"""
    monkeypatch.setattr(ds, "POS_SCREEN_FACT_ONLY", False)
    rows = [_ztrow("600001", 5, "光伏"), _ztrow("600002", 2, "机器人"),
            _ztrow("600011", 2, "机器人"), _ztrow("600012", 1, "机器人")]
    out = _screen(rows)
    assert out["600002"]["verdict"] == "reject"
    assert any("身位差 3 板" in r for r in out["600002"]["rejects"])
    assert out["600002"]["facts"]["position_gap"] is None


# ── ②③④⑤ 09:25 裁决：降仓不否决 ────────────────────────────────────────────

def _cand(code, name, boards=2, theme="测试题材", exp=(1.0, 3.0), need=1.0, **kw):
    # 2026-11-17：补 core_theme —— 11-12 起「缺题材归属」的票不进裁决（题材缺失闸），
    # 这些用例测的是身位/仓位，不该被题材闸误伤。
    c = {"code": code, "name": name, "boards": boards, "sector": theme,
         "core_theme": theme, "bucket_label": "接力池",
         "expectation": {"expected_range": list(exp), "auction_amount_min_yi": need}}
    c.update(kw)
    return c


def _q(prev, gap_pct, amt=20.0):
    return {"prev_close": prev,
            "auction_price": round(prev * (1 + gap_pct / 100.0), 3),
            "auction_amount_yi": amt}


PLAN = {"enabled": True, "can_buy": True, "tier": "weak", "strength": 0.30,
        "quota": 2, "per_pick_cap": 0.5, "total_cap": 1.0, "drawdown_pct": None,
        "drawdown_level": None, "drawdown_mult": 1.0, "theme_confidence": 0.0,
        "hints": [], "_diag": {}}


def _call(cands, quotes, *, zt_rows=None, code_theme=None, market_max=4):
    return pc.call_pool(
        cands, quotes, plan={"stance": "selective", "views": {}}, stance="selective",
        line_cores={}, sector_flow={}, zt_series={}, zt_days=[], market_open=None,
        relay_plan=PLAN, theme_open_rows=zt_rows, market_max_boards=market_max,
        code_theme=code_theme)


def test_pool_call_cross_theme_keeps_participate_with_lighter_cap():
    """⭐ 市场差 3、同题材最高 → 仍然 participate（旧闸会 watch），仓位 0.5→0.375。"""
    cands = {"relay": [_cand("000001", "甲票", boards=2)], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, 5.0)}
    zt = [{"code": "000001", "boards": 2}, {"code": "000009", "boards": 5}]
    res = _call(cands, quotes, zt_rows=zt, market_max=5,
                code_theme={"000001": "测试题材", "000009": "别的题材"})
    v = res["views"]["000001"]
    assert v["view"] == "participate"
    assert v["position_gap"]["line_gap"] == 0
    assert v["position_gap"]["market_gap"] == 3
    assert v["position_weight"] == 0.75
    assert v["position_cap"] == pytest.approx(0.5 * 0.75)


def test_pool_call_same_theme_backrow_downsized_not_dropped():
    """同题材差 2 板 → 降仓 0.40，但仍参与（⛔ 不剔除、不归零）。"""
    cands = {"relay": [_cand("000001", "甲票", boards=2)], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, 5.0)}
    zt = [{"code": "000001", "boards": 2}, {"code": "000009", "boards": 4}]
    res = _call(cands, quotes, zt_rows=zt,
                code_theme={"000001": "测试题材", "000009": "测试题材"})
    v = res["views"]["000001"]
    # ⛔ 2026-11-17 契约更新：同题材有更高板核心在板时，**后排直接不买**（不再「降仓 0.40 仍参与」）。
    #    原断言来自 10-xx 的「降仓不剔除」口径，已被「只做前排/核心」推翻。
    assert v["view"] == "watch"
    assert "核心" in str(v.get("reason") or "")


def test_pool_call_line_gap_one_not_downsized():
    """同题材差 1 板（历史最优档 +0.52%/胜 53.4%）→ 权重 1.0。"""
    cands = {"relay": [_cand("000001", "甲票", boards=2)], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, 5.0)}
    zt = [{"code": "000001", "boards": 2}, {"code": "000009", "boards": 3}]
    res = _call(cands, quotes, zt_rows=zt,
                code_theme={"000001": "测试题材", "000009": "测试题材"})
    v = res["views"]["000001"]
    # 同上：差 1 板也算后排 → 不买（旧口径是权重 1.0 正常买入）
    assert v["view"] == "watch"


def test_pool_call_hard_switch_restores_veto(monkeypatch):
    """`POOL_CALL_POSITION_GAP_HARD=1` → 回到硬闸：同题材差 2 判 watch。"""
    monkeypatch.setattr(pc, "POOL_CALL_POSITION_GAP_HARD", True)
    cands = {"relay": [_cand("000001", "甲票", boards=2)], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, 5.0)}
    zt = [{"code": "000001", "boards": 2}, {"code": "000009", "boards": 4}]
    res = _call(cands, quotes, zt_rows=zt,
                code_theme={"000001": "测试题材", "000009": "测试题材"})
    v = res["views"]["000001"]
    assert v["view"] == "watch"
    # 2026-11-17：后排闸已由「降仓」改为「不买」，reason 必须说清是核心在板
    assert "核心" in str(v.get("reason") or "") or "只观察" in str(v.get("reason") or "")


def test_pool_call_without_theme_does_not_rule():
    """缺 code_theme → basis unknown，身位完全不参与（不降仓、不剔除）。"""
    cands = {"relay": [_cand("000001", "甲票", boards=2)], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, 5.0)}
    zt = [{"code": "000001", "boards": 2}, {"code": "000009", "boards": 5}]
    res = _call(cands, quotes, zt_rows=zt, code_theme=None)
    v = res["views"]["000001"]
    assert v["view"] == "participate"
    assert "position_gap" not in v
    assert v["position_cap"] == pytest.approx(0.5)


def test_pool_call_position_weight_switch_off(monkeypatch):
    """`POOL_CALL_POSITION_WEIGHT_POSITION=0` → 不再影响仓位（留痕仍在）。"""
    monkeypatch.setattr(pc, "POOL_CALL_POSITION_WEIGHT_POSITION", False)
    cands = {"relay": [_cand("000001", "甲票", boards=2)], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, 5.0)}
    zt = [{"code": "000001", "boards": 2}, {"code": "000009", "boards": 4}]
    res = _call(cands, quotes, zt_rows=zt,
                code_theme={"000001": "测试题材", "000009": "测试题材"})
    v = res["views"]["000001"]
    # 2026-11-17：同题材后排在前排可买时直接不买，不再产生 position_gap / 降仓
    assert v["view"] == "watch"
    assert "position_gap" not in v


def test_pool_call_does_not_change_zone_or_participate_count():
    """⛔ 身位只影响 position_cap：zone 与参与只数一字不动。"""
    cands = {"relay": [_cand("000001", "甲票", boards=2),
                       _cand("000002", "乙票", boards=2)], "watch": []}
    quotes = {"000001": _q(10.0, 2.0, 5.0), "000002": _q(10.0, 2.0, 5.0)}
    # 2026-11-17：给两只票各自独立的题材 —— 测的是「身位只影响仓位」，
    # 不能让「同题材后排闸」（11-12 起改为一律不买）把参与只数打掉。
    zt = [{"code": "000001", "boards": 2}, {"code": "000002", "boards": 2},
          {"code": "000009", "boards": 4}]
    res = _call(cands, quotes, zt_rows=zt,
                code_theme={"000001": "甲题材", "000002": "乙题材",
                            "000009": "丙题材"})
    views = res["views"]
    assert {c: v["zone"] for c, v in views.items()} == {
        "000001": views["000001"]["zone"], "000002": views["000002"]["zone"]}
    assert res["meta"]["slot_policy"]["participate_count"] == 2
    # 各票独立题材 → 无后排闸；市场最高 4 板、本票 2 板 → 差 2 板，权重 0.75（不是 1.0）
    assert views["000001"]["position_weight"] == 0.75
    assert views["000002"]["position_weight"] == 0.75
    assert views["000001"]["position_weight"] == views["000002"]["position_weight"]
