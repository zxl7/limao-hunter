# -*- coding: utf-8 -*-
"""主导决策机械层单测（2026-09-09 用户「吸收决策进系统」）。

锁定 decision_outlook.build_decision 五档门控 + 破线硬禁买 + 数据缺失降级。
"""
import importlib.util
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("decision_outlook", WC / "core" / "decision_outlook.py")
do = importlib.util.module_from_spec(spec)
spec.loader.exec_module(do)


def test_ice_wait_no_buy():
    """冰点(≤2)·晋级率走弱 → wait_ice：不买·仓位0·带修复触发。"""
    d = do.build_decision(regime="ice", sentiment=2.0, promote_rate=20.5,
                          nuke_count=11, avg_pct=1.23, promote_downtrend=True,
                          defense_level=1, nav_drawdown_pct=9.34)
    assert d["verdict"] == "wait_ice"
    assert d["rules"]["position"] == 0
    assert any("≥4.5" in t for t in d["triggers"])


def test_hard_block_when_drawdown_over10():
    """净值回撤≥10% → hard_block（3日禁买）——最高优先级，无视情绪分。"""
    d = do.build_decision(regime="risk_on", sentiment=8.0, promote_rate=40.0,
                          nuke_count=2, nav_drawdown_pct=11.2)
    assert d["verdict"] == "hard_block"
    assert "禁买" in d["conclusion"]


def test_wait_on_defense_and_risk_off():
    """退潮/risk_off/趋势防守 → wait（观望·触发式·可试错仓位上限）。"""
    d = do.build_decision(regime="risk_off", sentiment=3.5, promote_rate=18.0,
                          nuke_count=8, avg_pct=0.4, defense_level=1)
    assert d["verdict"] == "wait"
    assert d["rules"]["position"].startswith("≤")


def test_selective_on_repair():
    """修复档(4.5-6.9) → selective（可做·≤50%·高开≤5%参与/＞5%放弃）。"""
    d = do.build_decision(regime="neutral", sentiment=5.5, promote_rate=32.0,
                          nuke_count=3, avg_pct=2.1)
    assert d["verdict"] == "selective"
    assert "3%" in d["rules"]["entry"] and "5%" in d["rules"]["entry"]
    assert d["rules"]["position"] == "≤50% 单票·≤1-2 只"


def test_buy_on_risk_on():
    """亢奋档(≥7) → buy（接力池正常执行）。"""
    d = do.build_decision(regime="risk_on", sentiment=7.5, promote_rate=40.0,
                          nuke_count=1, avg_pct=3.5)
    assert d["verdict"] == "buy"


def test_unknown_when_sentiment_missing():
    """情绪缺失 → unknown（不决策·诚实降级不编造）。"""
    d = do.build_decision(regime=None, sentiment=None)
    assert d["verdict"] == "unknown"
