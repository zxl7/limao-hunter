# -*- coding: utf-8 -*-
"""情绪评估窗口分层单测（2026-09-10 用户拍板·窗口实测校准）。

锁定 gen_sentiment_summary.ma3 / turn_signal（纯函数）：
当日=执行 / 3日=拐点确认 / 5日=纪律 / 10日=归纳。含背离修正（假修复/修复前兆）。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("gen_sentiment_summary", SCRIPTS / "gen_sentiment_summary.py")
gs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gs)


def test_ma3_rolling_and_none_safe():
    seq = [1.0, 2.0, 3.0, 4.0]
    assert gs.ma3(seq) == [1.0, 1.5, 2.0, 3.0]
    # i=3 窗口含 idx1..3（2.0/None/4.0）→ 均值 3.0（None 跳过）
    assert gs.ma3([None, 2.0, None, 4.0]) == [None, 2.0, 2.0, 3.0]


def test_turn_repair_confirmed():
    """3 日窗连续上行 → repair（且当日不弱于窗均值）。"""
    t = gs.turn_signal([1.0, 1.5, 2.0, 3.0, 4.0])
    assert t["dir"] == "repair"


def test_turn_ebb_confirmed():
    """3 日窗连续下行 → ebb。"""
    t = gs.turn_signal([6.0, 5.0, 4.0, 3.0, 2.0])
    assert t["dir"] == "ebb"


def test_turn_ebb_warn_on_fake_repair():
    """⛔ 假修复识别（09-09 实证）：ma3 名义上行(4.17→5.37→5.7) 但当日 2.0 显著转弱 → ebb_warn。"""
    t = gs.turn_signal([1.5, 1.0, 10.0, 5.1, 2.0])
    assert t["dir"] == "ebb_warn"
    assert "假修复" in t["label"]
    assert t["divergence"] and "弱于" in t["divergence"]


def test_turn_repair_warn_on_panic_rebound():
    """3 日窗名义下行但当日显著转强 → repair_warn（修复前兆·待确认）。"""
    # 构造成立条件：ma3 名义下行(b=6>c=5) 且当日 7 高于 ma3 5 达 +2.0
    t = gs.turn_signal([6.0, 10.0, 4.0, 4.0, 7.0])
    assert t["dir"] == "repair_warn"


def test_turn_flat_and_insufficient():
    """震荡 → flat；样本不足 → unknown（诚实降级）。"""
    t = gs.turn_signal([5.0, 6.0, 5.5, 6.0, 5.5])
    assert t["dir"] == "flat"
    t2 = gs.turn_signal([3.0, 4.0])
    assert t2["dir"] == "unknown"


def test_model_change_is_not_a_market_turn():
    days = [{"sentiment_score": 1}, {"sentiment_score": 10},
            {"sentiment_score": 6, "sentiment_model": "continuous_v3"}]
    assert gs.comparable_sentiment(days) == [None, None, 6]
    assert gs._cycle_why(days, [], None) is None
    assert gs.turn_signal([6])["dir"] == "unknown"
