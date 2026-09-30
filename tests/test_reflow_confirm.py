# -*- coding: utf-8 -*-
"""回流确认功能回归测试（2026-09-30）。

覆盖：
1. theme_rotation._classify_reentry：新题材 / 电风扇 / 真回流 / 间隔不足 / 今日未真实爆发 五场景。
2. live_signal.build_reflow_confirm_card：大白话卡片结构正确、无机器黑话。
"""
import importlib
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "scripts"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import theme_rotation as tr  # noqa: E402

_LS_ERR = None
try:
    import live_signal as ls  # noqa: E402
    _HAVE_LS = True
except Exception as _e:  # live_signal 顶层依赖较重，缺失时仅跳过卡片测试
    _HAVE_LS = False
    _LS_ERR = _e


def _day(zt=None, pct=None):
    if zt is None and pct is None:
        return None
    return {"zt": zt, "pct": pct or 0.0}


def test_classify_reentry_new_theme():
    # 新题材首次爆发：只有最后一天 boom，之前无 boom
    per_day = [_day(), _day(), _day(), _day(zt=5)]
    ok, gap = tr._classify_reentry(per_day, boom_idx=3)
    assert ok is False, "新题材首次爆发不应判回流"
    assert gap == 0


def test_classify_reentry_everyday_boom():
    # 电风扇：每天 boom，间隔 0
    per_day = [_day(zt=5), _day(zt=4), _day(zt=6)]
    ok, gap = tr._classify_reentry(per_day, boom_idx=2)
    assert ok is False, "电风扇每天 boom 不应判回流"


def test_classify_reentry_real():
    # 真回流：第0天 boom，中间冷却2天，最后一天 boom（间隔2 ≥ 阈值2）
    per_day = [_day(zt=5), _day(), _day(), _day(zt=4)]
    ok, gap = tr._classify_reentry(per_day, boom_idx=3)
    assert ok is True, "冷却后重新爆发应判回流"
    assert gap == 2


def test_classify_reentry_gap_too_small():
    # 间隔不足：连续两日 boom（间隔0 < 阈值2）
    per_day = [_day(zt=5), _day(zt=4)]
    ok, gap = tr._classify_reentry(per_day, boom_idx=1)
    assert ok is False, "连续两日爆发间隔不足不应判回流"


def test_classify_reentry_today_not_boom():
    # 今日未真实爆发（仅历史上爆发过）：P1 修正边界，phase 被改 boom 但今日本身非 boom
    per_day = [_day(zt=5), _day(), _day(zt=4), _day()]
    ok, gap = tr._classify_reentry(per_day, boom_idx=2)
    assert ok is False, "今日未真实爆发不应判回流确认"


@pytest.mark.skipif(not _HAVE_LS, reason=f"live_signal 导入失败: {_LS_ERR}")
def test_build_reflow_confirm_card():
    cands = [{
        "theme": "医药", "boom_zt": 8, "reentry_gap_days": 6,
        "watch_pool": [{"name": "贝瑞基因"}, {"name": "南华生物"}],
    }]
    card = ls.build_reflow_confirm_card(cands, "14:30")
    assert card["title"] == "⚡短线精灵·回流确认 14:30"
    assert card["color"] == "red"
    import json
    txt = json.dumps(card, ensure_ascii=False)
    assert "医药" in txt
    assert "回流确认" in txt
    assert "贝瑞基因" in txt
    # 大白话铁律：卡面不得出现机器黑话
    assert "reflow" not in txt.lower()
    assert "is_reentry" not in txt


# ── 回流确认监测纯函数（_detect_reflow_confirm）测试 ──
def _c(theme, is_reentry, zt, pool=None):
    return {"theme": theme, "is_reentry": is_reentry, "boom_zt": zt,
            "watch_pool": [{"code": c} for c in (pool or [])]}


@pytest.mark.skipif(not _HAVE_LS, reason=f"live_signal 导入失败: {_LS_ERR}")
def test_detect_basic_trigger():
    out, skip, cool = ls._detect_reflow_confirm([_c("医药", True, 8, ["000710"])], {}, {}, datetime.now(), 1800)
    assert len(out) == 1 and out[0]["theme"] == "医药"


@pytest.mark.skipif(not _HAVE_LS, reason=f"live_signal 导入失败: {_LS_ERR}")
def test_detect_filter_not_reentry():
    out, skip, cool = ls._detect_reflow_confirm([_c("电风扇", False, 8)], {}, {}, datetime.now(), 1800)
    assert len(out) == 0 and skip == 1


@pytest.mark.skipif(not _HAVE_LS, reason=f"live_signal 导入失败: {_LS_ERR}")
def test_detect_filter_low_zt():
    out, skip, cool = ls._detect_reflow_confirm([_c("弱回流", True, 2)], {}, {}, datetime.now(), 1800)
    assert len(out) == 0


@pytest.mark.skipif(not _HAVE_LS, reason=f"live_signal 导入失败: {_LS_ERR}")
def test_detect_cooldown_blocks():
    now = datetime.now()
    pushed = {"医药": (now - timedelta(seconds=10)).isoformat()}
    out, skip, cool = ls._detect_reflow_confirm([_c("医药", True, 8, ["000710"])], pushed, {}, now, 1800)
    assert len(out) == 0 and cool == 1  # 冷却内不推


@pytest.mark.skipif(not _HAVE_LS, reason=f"live_signal 导入失败: {_LS_ERR}")
def test_detect_fingerprint_change_after_cooldown():
    now = datetime.now()
    pushed = {"医药": (now - timedelta(seconds=2000)).isoformat()}  # 冷却到期
    sig = {"医药": "5#000504"}  # 旧指纹（跟踪池不同）
    out, skip, cool = ls._detect_reflow_confirm([_c("医药", True, 8, ["000710"])], pushed, sig, now, 1800)
    assert len(out) == 1  # 冷却到期 + 指纹变化 → 可重推


@pytest.mark.skipif(not _HAVE_LS, reason=f"live_signal 导入失败: {_LS_ERR}")
def test_build_card_zt_integer_display():
    # boom_zt 可能来自引擎是 float（6.0），卡面应显示整数"6只"而非"6.0只"
    cands = [{"theme": "医药", "boom_zt": 6.0, "reentry_gap_days": 4,
             "watch_pool": [{"name": "贝瑞基因"}]}]
    card = ls.build_reflow_confirm_card(cands, "14:30")
    import json
    txt = json.dumps(card, ensure_ascii=False)
    assert "今日涨停6只" in txt, "float 涨停数应显示为整数"
    assert "6.0只" not in txt


@pytest.mark.skipif(not _HAVE_LS, reason=f"live_signal 导入失败: {_LS_ERR}")
def test_main_emits_reflow_confirm_card(monkeypatch, capsys):
    """回归（2026-09-30 真实 bug）：_detect_reflow_confirm 若误置于 for 循环体内，
    会因每轮改写 reflow_confirm_pushed 冷却时间戳，导致循环末次调用恒返回空 →
    回流确认卡在生产中永不触发。本测试跑通真实 main() 推送路径，断言卡片被打印。"""
    synth = {"candidates": [{
        "theme": "回归测试回流", "phase": "boom", "is_reentry": True,
        "reentry_gap_days": 3, "boom_zt": 6.0,
        "watch_pool": [{"name": "测试A", "code": "000001"}], "zt_filled": True,
    }]}
    _orig_load = ls.load

    def _fake_load(p):
        if str(p).endswith("theme_rotation.json"):
            return synth
        return _orig_load(p)

    monkeypatch.setattr(ls, "load", _fake_load)
    monkeypatch.setattr(ls, "in_trading", lambda: True)
    monkeypatch.setattr(ls, "save_sig", lambda s: None)  # 防污染生产 pushed_signals_*.json
    monkeypatch.setattr(ls, "load_sig", lambda: {})
    monkeypatch.setattr(sys, "argv", ["live_signal.py", "--dry-run"])
    ls.main()
    out = capsys.readouterr().out
    assert "回流确认卡" in out, "回流确认卡应被打印（循环外只调一次的修复必须生效）"
    assert "回归测试回流" in out
    assert "回流确认" in out

