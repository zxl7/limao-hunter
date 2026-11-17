#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价卡 · 观察池弱转强段（2026-11-09 用户「弱转强写进竞价卡里面」）。

## 用户原话
「竞价的时候，昨天涨停，今天开盘竞价跌停，然后慢慢的居然红开了。我靠，这种竞价弱转强，
  我也是非常想要买的呀」
「我们只需要看我们这边昨天准备好的那些票就好了呀。不需要看场外的那些票，
  只关注我们当天收盘锁定的那些票。」
「根据时间戳来更新，记录一次时间再记录一次时间，对比价格变化就能看出来趋势变化了呀。
  哪怕没数据也没关系，我们这边做好数据的兼容就好。」

## 这份测试在锁什么
① 数据兼容四态：有反转 / 无反转但有未确认 / 全没抓到 / 整段缺字段（旧 payload）。
② 措辞诚实：`pending` 必须写成「终态未确认」，⛔ 不许写成「没有弱转强」；
   `no_data` 必须写成「没抓到」，⛔ 不许写成「没反转」。
③ 边界：只描述路径，⛔ 不进名额 / 仓位 / 买入；开关可整段回退。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE))

from pipelines import build_cards as bc            # noqa: E402
from pipelines import pipeline_verify as pv        # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────
def _rev(**kw):
    base = {"available": True, "rounds": 11, "pool_size": 15,
            "source": "heartbeat", "window": ["09:15", "09:30"], "deep_pct": -5.0,
            "items": [], "counts": {"reversal": 0, "flat": 0, "pending": 0, "no_data": 0}}
    base.update(kw)
    return base


def _item(code="002088", name="鲁阳节能", state="reversal", **kw):
    row = {"code": code, "name": name, "state": state}
    row.update(kw)
    return row


def _card(rev=None, *, drop=False):
    rec = {"code": "600261", "name": "阳光照明", "theme": "照明设备",
           "performance": {"open_check": {
               "ai_view": "participate", "decision_status": "buy",
               "gap_pct": -1.48, "auction_amount_yi": 0.53,
               "auction_amount_need_yi": 0.51,
               "pool_reason": "符合逐票预期", "can_enter": True}}}
    payload = {"records": [rec],
               "environment": {"regime": "risk_on", "regime_trusted": True,
                               "regime_score": 7.9, "regime_label": "偏强"}}
    if not drop:
        payload["watch_reversal"] = rev
    res = {"summary": {"trade_date10": "2026-11-09", "total_candidates": 1,
                       "buy_signals": 1, "hit_signals": 0, "covered": 24,
                       "caliber": "ai",
                       "pool_call": {"participate": 1, "pool_size": 15,
                                     "effective_quota": 1}},
           "payload": payload}
    return bc.build_verify_card(res)


def _txt(card) -> str:
    return str(card)


# ── ① 卡面四态 ────────────────────────────────────────────────────────
def test_reversal_renders_path_with_timestamps():
    """有弱转强：必须把「最低→末条」两端时点都摆出来（用户要的就是趋势）。"""
    card = _card(_rev(
        items=[_item(low_pct=-9.82, low_ts="09:16", last_pct=1.35,
                     last_ts="09:26", span_pct=11.17)],
        counts={"reversal": 1, "flat": 10, "pending": 0, "no_data": 0}))
    txt = _txt(card) + card["title"]
    assert "竞价弱转强" in txt
    assert "鲁阳节能" in txt and "002088" in txt
    assert "-9.82%" in txt and "09:16" in txt       # 低点 + 时点
    assert "+1.35%" in txt and "09:26" in txt       # 末条 + 时点
    assert "11.17pp" in txt                          # 拉升幅度
    assert "11 轮" in txt                            # 采样轮数 = 趋势可信度


def test_no_reversal_lists_pending_and_no_data():
    """没有反转但有未确认/没抓到：必须分开报，⛔ 不得笼统说「没反转」。"""
    card = _card(_rev(
        items=[_item(state="pending", reason="末条 09:22 早于 09:25")],
        counts={"reversal": 0, "flat": 0, "pending": 1, "no_data": 14}))
    txt = _txt(card)
    assert "深水转红" in txt
    assert "1 只终态未确认" in txt
    assert "14 只没抓到" in txt
    assert "没有弱转强" not in txt          # ⛔ 不知道 ≠ 没有


def test_unavailable_degrades_to_one_grey_line():
    """取不到就明说取不到 —— 段不静默消失（看不见比看见更危险）。"""
    card = _card({"available": False,
                  "reason": "本轮没抓到观察池行情（快照未覆盖竞价窗口）",
                  "pool_size": 15, "rounds": 0})
    txt = _txt(card)
    assert "竞价弱转强" in txt
    assert "未取到接力池竞价路径" in txt
    assert "快照未覆盖竞价窗口" in txt


def test_missing_field_keeps_old_payload_rendering():
    """旧 payload 无 watch_reversal → 整段不渲染（向后兼容，不报错）。"""
    card = _card(drop=True)
    assert "竞价弱转强" not in _txt(card)


def test_hide_switch_rolls_back(monkeypatch):
    """`VERIFY_CARD_HIDE=reversal` 一键回退整段。"""
    monkeypatch.setenv(bc._HIDE_BLOCK_ENV, "reversal")
    card = _card(_rev(items=[_item(low_pct=-9.8, low_ts="09:16",
                                   last_pct=1.3, last_ts="09:26", span_pct=11.1)],
                      counts={"reversal": 1, "flat": 0, "pending": 0, "no_data": 0}))
    assert "竞价弱转强" not in _txt(card)


def test_section_carries_no_decision_boundary():
    """边界锁：段落自陈「不进名额不进仓位」，且不改买入判定。"""
    card = _card(_rev(items=[_item(low_pct=-6.0, low_ts="09:16",
                                   last_pct=0.8, last_ts="09:26", span_pct=6.8)],
                      counts={"reversal": 1, "flat": 0, "pending": 0, "no_data": 0}))
    txt = _txt(card)
    assert "不进名额不进仓位" in txt
    assert card["title"].endswith("AI 只买1只·精选")     # 标题口径未受影响


def test_registry_fallback_is_labeled(monkeypatch):
    """走观测表兜底时如实标注「更保守」，不冒充心跳。"""
    card = _card(_rev(source="registry",
                      items=[_item(low_pct=-6.0, low_ts="09:16",
                                   last_pct=0.8, last_ts="09:26", span_pct=6.8)],
                      counts={"reversal": 1, "flat": 0, "pending": 0, "no_data": 0}))
    # 2026-11-17：文案改为「观测表补充」（来源仍是 registry 兜底表，语义不变）
    assert "观测表补充" in _txt(card)


# ── ② payload 投影 ───────────────────────────────────────────────────
def test_payload_disabled_switch(monkeypatch):
    """ENABLE_WATCH_POOL_SNAPSHOT=0 → available=False 并说明原因。"""
    from common import watch_pool as wp
    monkeypatch.setattr(wp, "ENABLE_WATCH_POOL", False)
    out = pv._watch_reversal_payload("2026-11-09")
    assert out["available"] is False
    assert "关闭" in out["reason"]


def test_payload_all_no_data_is_unavailable(monkeypatch):
    """一条都没抓到 → available=False（⛔ 不当「没有反转」）。"""
    from common import watch_pool as wp
    monkeypatch.setattr(wp, "auction_paths", lambda **k: {
        "items": [{"code": "600261", "state": "no_data"},
                  {"code": "002088", "state": "no_data"}],
        "counts": {"reversal": 0, "flat": 0, "pending": 0, "no_data": 2},
        "rounds": 0, "source": "heartbeat"})
    out = pv._watch_reversal_payload("2026-11-09")
    assert out["available"] is False
    assert "没抓到" in out["reason"]


def test_payload_empty_pool(monkeypatch):
    """观察池为空 → available=False，reason 明说池空。"""
    from common import watch_pool as wp
    monkeypatch.setattr(wp, "auction_paths", lambda **k: {
        "items": [], "counts": {}, "rounds": 0, "source": "heartbeat"})
    out = pv._watch_reversal_payload("2026-11-09")
    assert out["available"] is False
    assert "接力池为空" in out["reason"]


def test_payload_exception_does_not_crash_card(monkeypatch):
    """读取抛异常 → 降级为 available=False，⛔ 不许拖垮整张竞价卡。"""
    from common import watch_pool as wp

    def _boom(**k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(wp, "auction_paths", _boom)
    out = pv._watch_reversal_payload("2026-11-09")
    assert out["available"] is False
    assert "RuntimeError" in out["reason"]


def test_payload_passes_through_counts(monkeypatch):
    """正常路径：counts / rounds / pool_size 原样透传给卡面。"""
    from common import watch_pool as wp
    monkeypatch.setattr(wp, "auction_paths", lambda **k: {
        "schema": "watch_pool_v1", "date": "2026-11-09", "source": "heartbeat",
        "rounds": 11, "global_last_ts": "09:26",
        "items": [{"code": "600261", "state": "flat"}],
        "counts": {"reversal": 0, "flat": 1, "pending": 0, "no_data": 0},
        "window": ["09:15", "09:30"], "deep_pct": -5.0})
    out = pv._watch_reversal_payload("2026-11-09")
    assert out["available"] is True
    assert out["pool_size"] == 1
    assert out["rounds"] == 11
    assert out["counts"]["flat"] == 1
