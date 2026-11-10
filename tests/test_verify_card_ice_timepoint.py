#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价验证卡·冰点时效闸门（2026-11-09 用户「我要买入的那一套逻辑，不要禁用的这一套逻辑」）。

## 这份测试在锁什么

事故现场（2026-11-09 09:26 卡）：
  标题 `⚡ 财富密码·竞价验证·增量 09:26 | 市场很冷·不买·空仓`
  正文 `🤖 今日买入 → 阳光照明 2板 竞价-1.48% → AI 买入 · 每只 50%`
  同一张卡，四个地方两种结论 —— 用户直接问「这是谁推的」。

根因不是文案，是**时点**：`candidates.environment.regime` 是 11-06 收盘按 1.8 分打的
`ice` 标签，被 11-09 09:25 原样沿用去禁今天的买；而 11-09 09:29 情绪已回到 6.3 分。

三条锁：
  ① `_resolve_regime`：文件日期 == 今日 → trusted；否则只标注不禁买；开关可回退。
  ② 当日冰点（trusted）：**整卡只有一个结论**——空仓 + 命中·禁买，
     ⛔ 不许再出现「今日买入 / AI 买入 / 每只 50%」。
  ③ 昨收冰点·今晨修复：标题按 AI 实判，正文照常写买入，并给一行时点交代。
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


# ── ① 时点裁决 ───────────────────────────────────────────────────────────

def _write_fb(root: Path, *, date: str, score: float, label: str = "") -> Path:
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "feedback_matrix.json").write_text(
        '{"date": "%s", "sentiment_score": %s, "sentiment_label": "%s",'
        ' "nuke_count": 0, "broken_rate": 0.0, "break_count": 0}'
        % (date, score, label), encoding="utf-8")
    return cache


@pytest.fixture()
def fake_root(tmp_path, monkeypatch):
    """隔离 cache：⛔ 不读真实 feedback_matrix.json（会随盘中秒级变化）。"""
    monkeypatch.setattr(pv, "ROOT", tmp_path / "wealth_code")
    (tmp_path / "wealth_code").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VERIFY_REGIME_LIVE", "1")
    return tmp_path


def test_today_fresh_reading_is_trusted(fake_root, monkeypatch):
    """文件日期 == 今日 → 用当日读数重判，trusted=True（禁买与否由今天的温度决定）。"""
    _write_fb(fake_root, date="2026-11-09", score=7.9, label="偏强")
    monkeypatch.setattr(
        "adapters.input_zt.load_environment",
        lambda *a, **k: {"regime": "risk_on", "sentiment_score": 7.9, "sentiment_label": "偏强"})
    out = pv._resolve_regime("2026-11-09", "ice")
    assert out["source"] == "live_today"
    assert out["trusted"] is True
    assert out["regime"] == "risk_on"       # 昨收 ice 被当日读数覆盖
    assert out["eod_regime"] == "ice"       # 昨收标签仍留痕
    assert out["score"] == 7.9


def test_stale_file_is_not_trusted(fake_root):
    """文件还停在上一个交易日 → 昨收标签，trusted=False：⛔ 不许禁掉今天的买。"""
    _write_fb(fake_root, date="2026-11-06", score=1.8, label="退潮")
    out = pv._resolve_regime("2026-11-09", "ice")
    assert out["source"] == "eod_prev"
    assert out["trusted"] is False
    assert out["regime"] == "ice"           # 标签保留（只当背景）
    assert out["score"] is None             # ⛔ 不把昨收分冒充今日读数


def test_switch_rolls_back_to_old_behavior(fake_root, monkeypatch):
    """VERIFY_REGIME_LIVE=0 → 无条件信任昨收标签（旧行为，可一键回退）。"""
    _write_fb(fake_root, date="2026-11-09", score=7.9)
    monkeypatch.setenv("VERIFY_REGIME_LIVE", "0")
    out = pv._resolve_regime("2026-11-09", "ice")
    assert out["source"] == "eod_legacy"
    assert out["trusted"] is True
    assert out["regime"] == "ice"


def test_missing_file_degrades_honestly(fake_root):
    """没有 feedback_matrix → 不当 0、不当今日读数，仍按昨收标签且不禁买。"""
    out = pv._resolve_regime("2026-11-09", "ice")
    assert out["trusted"] is False
    assert out["score"] is None


# ── ② / ③ 卡面只能有一个结论 ──────────────────────────────────────────────

def _card(env: dict, *, status: str):
    # ⚠️ 冰点日真实形态：`ai_view` **仍是 participate**（AI 原判），只有
    #    `decision_status` 被闸门改成 hit。⛔ 别把 ai_view 也改掉——那就测不到卡面对撞。
    rec = {"code": "600261", "name": "阳光照明", "theme": "照明设备",
           "performance": {"open_check": {
               "ai_view": "participate",
               "decision_status": status,
               "gap_pct": -1.48, "auction_amount_yi": 0.53,
               "auction_amount_need_yi": 0.51,
               "pool_reason": "符合逐票预期",
               "can_enter": status == "buy"}}}
    res = {
        "summary": {
            "trade_date10": "2026-11-09", "total_candidates": 1,
            "buy_signals": 1 if status == "buy" else 0,
            "hit_signals": 0 if status == "buy" else 1,
            "covered": 24, "caliber": "ai",
            "pool_call": {"participate": 1, "pool_size": 15, "effective_quota": 1,
                          "slot_policy": {"single_pick": True, "single_pick_cap": 0.5,
                                          "unlimited_slots": True,
                                          "allocation_count": 1, "even_cap": 0.5}},
        },
        "payload": {"records": [rec], "environment": env},
    }
    return bc.build_verify_card(res)


def test_ice_today_has_single_conclusion(monkeypatch):
    """当日冰点：整卡只有一个结论 —— 空仓 + 命中·禁买，⛔ 不许出现买入字样。"""
    monkeypatch.setattr(bc, "_load_ai_view_raw", lambda *a, **k: {"views": {"600261": {}}})
    monkeypatch.setattr(bc, "_load_ai_view", lambda *a, **k: {"views": {"600261": {}}})
    card = _card({"regime": "ice", "regime_trusted": True, "regime_score": 1.5,
                  "regime_label": "退潮", "regime_eod": "ice",
                  "regime_eod_score": 1.8}, status="hit")
    txt = str(card)
    assert "不买·空仓" in card["title"]
    assert "今日命中" in txt
    assert "命中·禁买" in txt
    # 三条硬禁止：同一张卡不许再出现「买入 / 每只 50%」
    assert "今日买入" not in txt
    assert "AI 买入" not in txt
    assert "每只 50%" not in txt


def test_ice_cleared_puts_back_the_buying_logic(monkeypatch):
    """昨收冰点·今晨已修复：标题按 AI 实判，正文照常写买入，并给一行时点交代。"""
    monkeypatch.setattr(bc, "_load_ai_view_raw", lambda *a, **k: {"views": {"600261": {}}})
    monkeypatch.setattr(bc, "_load_ai_view", lambda *a, **k: {"views": {"600261": {}}})
    card = _card({"regime": "risk_on", "regime_trusted": True, "regime_score": 7.9,
                  "regime_label": "偏强", "regime_eod": "ice",
                  "regime_eod_score": 1.8}, status="buy")
    txt = str(card)
    assert "AI 只买1只·精选" in card["title"]
    assert "AI 买入" in txt
    # 时点必须讲清楚：昨收 1.8 → 今晨 7.9，冰点已解除
    assert "昨日收盘 1.8 分" in txt and "今晨 7.9 分" in txt
    assert "昨日冰点已解除" in txt


def test_old_payload_without_trusted_keeps_old_behavior(monkeypatch):
    """旧 payload（无 regime_trusted）→ 维持旧行为（信任 ice），⛔ 不悄悄改成放行。"""
    monkeypatch.setattr(bc, "_load_ai_view_raw", lambda *a, **k: {"views": {"600261": {}}})
    monkeypatch.setattr(bc, "_load_ai_view", lambda *a, **k: {"views": {"600261": {}}})
    card = _card({"regime": "ice"}, status="hit")
    assert "不买·空仓" in card["title"]
