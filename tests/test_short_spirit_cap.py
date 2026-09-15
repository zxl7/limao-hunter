# -*- coding: utf-8 -*-
"""短线精灵市值门槛豁免（2026-09-07 用户 A+B 方案）回归锁。

A=连板龙头(boards>=2)/板块核心票 豁免市值门槛
B=非豁免票门槛降至 30 亿
⛔ 两开关默认关=与改动前行为完全一致；本测试锁死：
  1. 默认关→中小盘龙头仍被 100 亿门槛挡（不破坏原行为）
  2. 开 A+板块核心票→豁免过
  3. 开 A+连板(boards>=2)→豁免过
  4. 开 B→非豁免 50 亿过 / 20 亿仍过滤
"""
import importlib.util
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("short_spirit", SCRIPTS / "short_spirit.py")
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)


def _sample(code="600103", name="青山纸业", mc=87.6, pct=10.0):
    return {
        "code": code, "name": name, "pct": pct,
        "market_cap_yi": mc, "amount_yi": 20.0, "list_date": "20100101",
        "industry": "造纸", "prev_close": 3.0, "price": 3.3,
        "main_flow_yi": 1.0,
    }


def _cfg():
    return ss.load_cfg()  # min_market_cap_yi=100


def test_default_blocks_small_cap(monkeypatch):
    """默认两开关关：87.6 亿 < 100 亿 → 仍被过滤（行为不变）。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_CAPEXEMPT", False)
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_LOWCAP", False)
    out = ss.noise_filter([_sample()], _cfg(), datetime.now())
    assert out == [], "默认关时中小盘龙头必须仍被 100 亿门槛挡"


def test_capexempt_core_pass(monkeypatch):
    """开 A + code 在板块核心票集 → 87.6 亿豁免过。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_CAPEXEMPT", True)
    out = ss.noise_filter([_sample()], _cfg(), datetime.now(),
                          exempt_codes={"600103"}, boards_map={})
    assert len(out) == 1, "板块核心票应豁免市值门槛"


def test_capexempt_boards_pass(monkeypatch):
    """开 A + boards>=2（连板龙头）→ 87.6 亿豁免过。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_CAPEXEMPT", True)
    out = ss.noise_filter([_sample()], _cfg(), datetime.now(),
                          exempt_codes=set(), boards_map={"600103": 5})
    assert len(out) == 1, "连板(boards>=2)龙头应豁免市值门槛"


def test_capexempt_off_boards_still_blocked(monkeypatch):
    """关 A + 仅 boards>=2（非核心票）→ 仍被 100 亿挡（开关没开不豁免）。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_CAPEXEMPT", False)
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_LOWCAP", False)  # 还原 100 亿门槛，测 A 关时确实挡
    out = ss.noise_filter([_sample()], _cfg(), datetime.now(),
                          exempt_codes=set(), boards_map={"600103": 5})
    assert out == [], "A 关时连板中小盘仍被挡"


def test_lowcap_threshold(monkeypatch):
    """开 B：非豁免 50 亿过 / 20 亿仍过滤。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_LOWCAP", True)
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_CAPEXEMPT", False)
    big = ss.noise_filter([_sample(mc=50.0)], _cfg(), datetime.now())
    small = ss.noise_filter([_sample(mc=20.0)], _cfg(), datetime.now())
    assert len(big) == 1, "B 开时 50 亿应过"
    assert small == [], "B 开时 20 亿仍过滤"


def test_lowcap_only_lowers_unexempt(monkeypatch):
    """关 B：非豁免 50 亿仍被原 100 亿门槛挡。"""
    monkeypatch.setattr(ss, "ENABLE_SPIRIT_LOWCAP", False)
    out = ss.noise_filter([_sample(mc=50.0)], _cfg(), datetime.now())
    assert out == [], "B 关时 50 亿仍被 100 亿挡"


def test_boards_map_extracts_leaders():
    """_boards_map 从 latest leaders 正确提取 code→连板数。"""
    d = {"leaders": [
        {"code": "600103", "name": "青山纸业", "boards": 5},
        {"code": "000001", "name": "平安银行", "boards": 0},
        {"code": "300189", "name": "神农种业"},  # 无 boards
    ]}
    m = ss._boards_map(d)
    assert m.get("600103") == 5
    assert m.get("000001") == 0
    assert "300189" not in m
