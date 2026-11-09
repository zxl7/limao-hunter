# -*- coding: utf-8 -*-
"""🚫 盘后预案全系统关闭 + 🌅 09:25 机械定调（2026-11-06 用户拍板）。

## 关的是什么

用户原话：「盘后预案这个功能关了，根本不需要，全网全系统关闭这个盘后预案！！」

关的是**整条消费链**，不只是「回避否决」：

| 项 | 关之前 | 关之后（`POOL_CALL_EOD_PLAN=0`） |
|---|---|---|
| `views[code].bias` | 决定「重点看/一般/回避」 | 完全不再读，`bias` 字段恒空 |
| `plan.stance` | 兜底今日名额 | 不再兜底，走 `DEFAULT_QUOTA`(2) |
| 名额来源 | 昨天盘后 LLM 拍的攻守基调 | 缺省 2 只；可选 09:25 机械定调 |

⛔ 真正的熔断**没有**被关掉：环境防守腿（`env level=block → effective_quota=0`）
仍然独立生效，与 stance 无关。

## 35 日 A/B（`scripts/research_cycle_ab.py`）

| 配置 | 天数 | 笔数 | 日胜 | 日均 | 复利 | 回撤 |
|---|---|---|---|---|---|---|
| P0 读预案 | 20 | 67 | 70.0% | +1.73% | +39.99% | −4.70% |
| **P1 关预案** | **25** | **83** | **72.0%** | **+1.97%** | **+61.05%** | −4.70% |
| P2 机械定调 | 24 | 81 | 70.8% | +1.96% | +57.73% | −4.70% |

新增 16 笔均 +1.70%，全部落在「盘后 LLM 判 hibernate（空仓）」的那 5 天
⇒ LLM 的「今天别做」在本窗口是负价值。机械定调反而略差（它的 hibernate 档
在重复同一个错误），故 `POOL_CALL_STANCE_INTRADAY` 默认关。

全隔离：不读真实 cache、不落盘、不触网。
"""
import json
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

from core import pool_call as pc       # noqa: E402
from core import stance_intraday as si  # noqa: E402


def _cand(code, name="甲票", boards=1, need=1.0):
    return {"code": code, "name": name, "boards": boards, "sector": "测试题材",
            "bucket_label": "接力池",
            "expectation": {"expected_range": [1.0, 3.0], "auction_amount_min_yi": need}}


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


PLAN = {"stance": "attack", "views": {"000001": {"bias": "avoid"}}}


# ── ① 默认不消费预案 ─────────────────────────────────────────────────
def test_plan_is_not_consumed_by_default():
    """盘后 say avoid + attack，默认两样都不读：照样买，且不留 avoid 痕迹。"""
    res = _call({"relay": [_cand("000001")], "watch": []},
                {"000001": _q(10.0, 2.0)}, plan=PLAN, stance=None)
    v = res["views"]["000001"]
    assert v["view"] == "participate"
    assert not v.get("bias_avoided")
    # `bias` 恒为宽容缺省 normal（不再从预案读 avoid / focus）
    assert v.get("bias") == pc.BIAS_NORMAL


def test_plan_stance_no_longer_sets_quota(monkeypatch):
    """hibernate（0 只）不再由预案给出 → 缺省名额 2。"""
    cands = {"relay": [_cand(f"00000{i}", f"票{i}", boards=4 - i) for i in (1, 2, 3)],
             "watch": []}
    quotes = {f"00000{i}": _q(10.0, 2.0) for i in (1, 2, 3)}
    res = _call(cands, quotes, plan={"stance": "hibernate", "views": {}}, stance=None)
    assert res["quota"] == pc.DEFAULT_QUOTA
    assert len([1 for v in res["views"].values() if v["view"] == "participate"]) >= 1


def test_switch_on_restores_plan(monkeypatch):
    """`POOL_CALL_EOD_PLAN=1` 逐字恢复：avoid 重新生效、hibernate 名额归 0。"""
    monkeypatch.setattr(pc, "POOL_CALL_EOD_PLAN", True)
    monkeypatch.setattr(pc, "POOL_CALL_BIAS_AVOID_VETO", True)
    res = _call({"relay": [_cand("000001")], "watch": []},
                {"000001": _q(10.0, 2.0)}, plan=PLAN, stance="attack")
    assert res["views"]["000001"]["view"] == "skip"

    res2 = _call({"relay": [_cand("000001")], "watch": []},
                 {"000001": _q(10.0, 2.0)},
                 plan={"stance": "hibernate", "views": {}}, stance=None)
    assert res2["quota"] == 0


def test_env_breaker_survives_plan_off():
    """⛔ 关预案不能把熔断一起关掉：环境防守腿仍独立可用（与 stance 解耦）。"""
    assert hasattr(pc, "POOL_CALL_DEFENSE_ENV")
    assert hasattr(pc, "POOL_CALL_DEFENSE_GATE")
    # 缺省名额只在「没人给定调」时生效；环境 block 由 refine_effective_quota 另行压到 0，
    # 不经过 stance —— 见 tests/test_defense_gate.py 的环境腿用例。
    assert pc.quota_for(None) == pc.DEFAULT_QUOTA
    assert pc.quota_for("hibernate") == 0


# ── ② 机械定调 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("red,lead,want", [
    (0.75, 0.80, "attack"),
    (0.62, 0.50, "selective"),   # 连板红开率没到 0.60 → 降档
    (0.50, 0.90, "selective"),
    (0.30, 0.10, "defend"),
    (0.29, 0.10, "hibernate"),
    (0.00, 0.00, "hibernate"),
])
def test_stance_levels(red, lead, want):
    st = si.stance_from_open({"red_open_ratio": red, "leader_red_open_ratio": lead,
                              "coverage": 1.0})
    assert st["stance"] == want
    assert st["reason"]


def test_stance_boundary_is_inclusive():
    """边界用 `≥`：0.60/0.60 恰好 attack，0.30 恰好 defend。"""
    assert si.stance_from_open({"red_open_ratio": 0.60, "leader_red_open_ratio": 0.60,
                                "coverage": 1.0})["stance"] == "attack"
    assert si.stance_from_open({"red_open_ratio": 0.30, "leader_red_open_ratio": 0.0,
                                "coverage": 1.0})["stance"] == "defend"


def test_stance_unknown_when_no_readings():
    """缺读数 / 覆盖率不足 → None（不裁决，不当成空仓）。"""
    assert si.stance_from_open(None)["stance"] is None
    assert si.stance_from_open({})["stance"] is None
    assert si.stance_from_open({"red_open_ratio": 0.9, "coverage": 0.2})["stance"] is None


def test_stance_missing_leader_ratio_blocks_attack():
    """连板红开率缺失时不给 attack（缺数据不猜，最多 selective）。"""
    st = si.stance_from_open({"red_open_ratio": 0.90, "coverage": 1.0})
    assert st["stance"] == "selective"


def test_stance_is_pure():
    """纯函数：不改入参。"""
    src = {"red_open_ratio": 0.70, "leader_red_open_ratio": 0.70, "coverage": 1.0}
    snapshot = dict(src)
    si.stance_from_open(src)
    assert src == snapshot


def test_switches_default_off(monkeypatch):
    """两个新开关默认关（35 日实测：P1 最优，机械定调略差）。"""
    import os
    monkeypatch.delenv("POOL_CALL_EOD_PLAN", raising=False)
    monkeypatch.delenv("POOL_CALL_STANCE_INTRADAY", raising=False)
    assert os.environ.get("POOL_CALL_EOD_PLAN") is None
    assert os.environ.get("POOL_CALL_STANCE_INTRADAY") is None
    assert pc.POOL_CALL_EOD_PLAN is False
    assert pc.POOL_CALL_STANCE_INTRADAY is False

    # 定调阈值单一源（改这里必须同步 shared_rules）
    assert (si.ATTACK_RED_OPEN, si.SELECTIVE_RED_OPEN, si.DEFEND_RED_OPEN) == (0.60, 0.45, 0.30)


# ── ③ 卡面必须跟着停显（⛔ 不能一边不消费、一边还写「昨日预案：防守」）──────
def _bc():
    from pipelines import build_cards
    return build_cards


def test_card_hides_eod_plan_when_off():
    raw = {"decision_date": "2026-11-06", "stance": "defend", "stance_label": "防守",
           "direction": "算力", "note": "今天市场很冷，只做最有把握的",
           "views": {"000001": {"bias": "focus"}}}
    recs = [{"code": "000001", "name": "甲票",
             "open_check": {"ai_view": "participate", "pool_reason": "开在 +2%"}}]
    pool = {"participate": ["000001"], "pool_size": 31, "effective_quota": 2,
            "stance_source": {"eod_plan": False, "intraday": False, "stance": None, "why": ""}}
    txt = json.dumps(_bc()._ai_plan_block(raw, recs, pool), ensure_ascii=False)
    assert "昨日预案" not in txt
    assert "防守" not in txt
    assert "市场很冷" not in txt          # note 同属预案正文，一并停显
    assert "今日买入" in txt              # ⛔ 实判段必须还在


def test_card_still_shows_eod_plan_when_switch_on():
    """回退口径（`POOL_CALL_EOD_PLAN=1`）时卡面照旧显示昨日预案 —— 证明是「跟着停」不是「删了」。"""
    raw = {"decision_date": "2026-11-06", "stance": "defend", "stance_label": "防守",
           "direction": "算力", "views": {"000001": {"bias": "focus"}}}
    recs = [{"code": "000001", "name": "甲票",
             "open_check": {"ai_view": "participate", "pool_reason": "开在 +2%"}}]
    pool = {"participate": ["000001"], "pool_size": 31, "effective_quota": 2,
            "stance_source": {"eod_plan": True, "intraday": False, "stance": "defend", "why": ""}}
    txt = json.dumps(_bc()._ai_plan_block(raw, recs, pool), ensure_ascii=False)
    assert "昨日预案" in txt and "防守" in txt


def test_card_without_stance_source_keeps_old_rendering():
    """缺 `stance_source`（历史快照）→ 保持旧渲染，不误删。"""
    raw = {"decision_date": "2026-11-06", "stance": "defend", "stance_label": "防守",
           "views": {"000001": {"bias": "focus"}}}
    recs = [{"code": "000001", "name": "甲票",
             "open_check": {"ai_view": "participate", "pool_reason": "开在 +2%"}}]
    pool = {"participate": ["000001"], "pool_size": 31, "effective_quota": 2}
    txt = json.dumps(_bc()._ai_plan_block(raw, recs, pool), ensure_ascii=False)
    assert "昨日预案" in txt
