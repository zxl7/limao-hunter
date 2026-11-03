#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周期阶段上卡面（P1）契约锁 —— 2026-10-21。

P1 目标：把 `core/cycle_phase` 的结论（在哪个阶段 / 该用什么手法 / 仓位档）送进卡面。
锁四件事：
  1. **不与 📡 大盘环境重复**：卡面用 `shift`（只讲方向），不用 `why`（含当期读数）；
  2. **基准日标注**：周期是收盘后才成立的判断，卡与基准日不同时必须标出来；
  3. **诚实降级**：位置不明 / 读不到数据 → 整块不渲染（⛔ 不占位、不硬凑）；
  4. **详略分档**：盘后卡三行（含方向证据），竞价卡两行（早上时间紧）。
"""
import pytest

from core import cycle_phase as cp
from pipelines import build_cards as bc


def _text(blocks):
    return "\n".join(b["text"]["content"] for b in blocks if isinstance(b.get("text"), dict))


@pytest.fixture
def fb_bottom(monkeypatch):
    """低位震荡（今天 10-21 的真实形态：晋级率 28% + 溢价从 -2.13% 修上来）。"""
    monkeypatch.setattr(cp, "_load_fb", lambda root=None: {
        "date": "2026-10-21", "promote_rate": 27.8, "avg_pct": 3.66, "up_rate": 69.2,
        "now_zt_count": 48, "now_max_boards": 4, "prev_max_boards": 4,
        "nuke_count": 2, "broken_rate": 29.4, "break_count": 1,
        "trend": {"history": [{"date": "2026-10-15", "pr": 16.7, "ap": -2.13},
                              {"date": "2026-10-16", "pr": 16.7, "ap": -1.64},
                              {"date": "2026-10-19", "pr": 18.0, "ap": 0.53}]},
    })


def test_block_renders_phase_and_play(fb_bottom):
    txt = _text(bc._cycle_phase_block(asof="2026-10-21"))
    assert "行情位置" in txt and "低位震荡" in txt
    assert "该这么打" in txt and "龙头种子" in txt
    assert "小仓位但积极试" in txt          # 仓位档必须出现（P1 明确要求）


def test_block_does_not_repeat_current_readings(fb_bottom):
    """⛔ 卡面用 shift：不许复述「晋级率 28%」「溢价 +3.66%」——📡 块已讲。"""
    txt = _text(bc._cycle_phase_block(asof="2026-10-21"))
    assert "晋级率" not in txt
    assert "+3.66%" not in txt and "3.66" not in txt


def test_block_marks_basis_date_when_different(fb_bottom):
    """卡是 10-20 的、周期用的是 10-21 收盘 → 必须标出基准日。"""
    txt = _text(bc._cycle_phase_block(asof="2026-10-20"))
    assert "10-21 收盘盘面" in txt


def test_block_silent_when_same_date(fb_bottom):
    txt = _text(bc._cycle_phase_block(asof="2026-10-21"))
    assert "收盘盘面" not in txt            # 同日不标（向后兼容·不添噪）


def test_block_hidden_when_unknown(monkeypatch):
    """位置不明 → 整块不渲染（⛔ 不占位、不硬凑一句「位置不明」）。"""
    monkeypatch.setattr(cp, "_load_fb", lambda root=None: {})
    assert bc._cycle_phase_block(asof="2026-10-21") == []


def test_block_never_breaks_card(monkeypatch):
    """周期判断炸了也不许影响整张卡（返回空 list）。"""
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(cp, "phase_of", _boom)
    assert bc._cycle_phase_block(asof="2026-10-21") == []


def test_verbose_false_drops_evidence_line(fb_bottom):
    """竞价卡（verbose=False）：两行——位置 + 手法，不展开方向证据。"""
    full = _text(bc._cycle_phase_block(asof="2026-10-21", verbose=True))
    brief = _text(bc._cycle_phase_block(asof="2026-10-21", verbose=False))
    assert "修复" in full and "修复" not in brief
    assert "行情位置" in brief and "该这么打" in brief
    assert len(brief) < len(full)


def test_no_internal_marks_on_card(fb_bottom):
    """⛔ 内部标记（⛔/regime=/phase=）一律不许上卡面。"""
    txt = _text(bc._cycle_phase_block(asof="2026-10-21"))
    for bad in ("⛔", "regime=", "phase=", "PHASE_", "eod", "pending"):
        assert bad not in txt


def test_play_text_has_no_forbidden_mark():
    """PHASE_PLAY 文案本身不得含内部标记（源头治理，不靠渲染层补救）。"""
    for k, v in cp.PHASE_PLAY.items():
        assert "⛔" not in v, f"{k} 的 play 文案含内部标记：{v}"


def test_eod_card_contains_cycle_block(monkeypatch, fb_bottom):
    """盘后卡实际渲染：🌊 块要在 📡 之后、「连板天梯」之前。"""
    monkeypatch.setattr(bc, "_load_metrics", lambda: None)
    monkeypatch.setattr(bc, "_load_zt_snapshot", lambda: {
        "zt_count": 54, "max_boards": 4, "ladder": {}})
    card = bc.build_eod_card({"date": "2026-10-21", "regime": "neutral",
                              "relay": [], "watch": [], "diagnostics": {},
                              "ai_plan": {}, "environment": {"regime": "neutral"}})
    txt = _text(card["elements"])
    assert "🌊 **行情位置**" in txt
    assert txt.index("📡") < txt.index("🌊")      # 先「几度」后「走到哪了」
