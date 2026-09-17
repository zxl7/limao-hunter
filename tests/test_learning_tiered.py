#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测自我学习「分级自动 + 防过度」回归锁（2026-09-17 用户「全方位自动学习+迭代」）。

背景：此前 19 次学习全「建议 N 条 / 应用 0 条」（auto_apply=False + 只认 high）→ 闭环断裂。
本锁覆盖：模式解析 / 风险分级与严重度解耦 / 防过度三闸 / R5 AI 质量数据源。
⛔ 纯离线：不触真实配置、不落盘、不外呼。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import learning as L  # noqa: E402


# ── 模式解析（含向后兼容）────────────────────────────────────────
def test_resolve_mode():
    assert L._resolve_mode({"auto_apply_mode": "tiered"}) == "tiered"
    assert L._resolve_mode({"auto_apply_mode": "off"}) == "off"
    assert L._resolve_mode({"auto_apply_mode": "high_only"}) == "high_only"
    assert L._resolve_mode({}) == "off"                       # 无配置 → 最保守
    assert L._resolve_mode({"auto_apply": True}) == "high_only"   # 向后兼容旧开关
    assert L._resolve_mode({"auto_apply_mode": "bogus"}) == "off"


# ── 分级判据：RULE_RISK（改动风险）≠ severity（偏差严重度）──────────
def test_rule_risk_is_decoupled_from_severity():
    """⛔ 核心：R1 的 severity=high（偏差严重）但 risk=low（数值可自动）——两者必须解耦。"""
    assert L.RULE_RISK["R1_range_low"] == "low"
    assert L.RULE_RISK["R4_range_high"] == "low"
    assert L.RULE_RISK["R2_cand_low"] == "medium"
    assert L.RULE_RISK["R3_sentiment_bias"] == "high"      # 语义类 → 仅建议
    assert L.RULE_RISK["R5_ai_quality"] == "high"          # AI 判定质量 → 仅建议
    # 默认策略：低/中自动、高仅建议
    assert L.DEFAULT_LEARNING_CONFIG["tier_policy"] == {"low": True, "medium": True, "high": False}


# ── 防过度三闸 + PDCA 开关存在 ─────────────────────────────────
def test_default_config_switches():
    c = L.DEFAULT_LEARNING_CONFIG
    assert c["auto_apply_mode"] == "tiered"          # 默认分级自动
    assert c["max_apply_per_run"] == 2               # 单次条数上限
    assert c["apply_cooldown_days"] == 3             # 同规则冷却
    assert c["max_adjust_step"] == 0.3               # 单次幅度
    assert c["track_effect"] is True                 # PDCA 效果追踪


def test_rule_risk_covers_all_default_rules():
    """新增规则必须登记 RULE_RISK（否则按 high 处理 → 不自动改·安全默认）。"""
    for name in L.DEFAULT_LEARNING_CONFIG["rules"]:
        assert name in L.RULE_RISK, f"{name} 未登记 RULE_RISK"
    # 未登记规则的安全默认 = high（仅建议）
    assert L.RULE_RISK.get("R_UNKNOWN", "high") == "high"


# ── R5 数据源：缺料/样本不足 → None（不编造）─────────────────────
def test_ai_quality_stats_degrades_without_data(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "CACHE", tmp_path)
    assert L._ai_quality_stats() is None


def test_ai_quality_stats_counts_hits(tmp_path, monkeypatch):
    """构造 3 票决策 + 次日涨停池 → participate 命中率可算。"""
    wc = tmp_path / "wealth_code" / "cache"
    wc.mkdir(parents=True)
    (wc / "ai_decisions.jsonl").write_text(
        "\n".join([
            '{"decision_date":"2026-09-10","code":"000001","view":"participate"}',
            '{"decision_date":"2026-09-10","code":"000002","view":"watch"}',
            '{"decision_date":"2026-09-10","code":"000003","view":"skip"}',
        ]) + "\n", encoding="utf-8")
    zt = tmp_path / "cache" / "zt_history"
    zt.mkdir(parents=True)
    (zt / "zt_2026-09-11.json").write_text(
        '{"date":"2026-09-11","zt_list":[{"code":"000001"},{"code":"000002"}]}',
        encoding="utf-8")
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "CACHE", tmp_path / "cache")
    q = L._ai_quality_stats()
    assert q is not None and q["_total"] == 3
    assert q["participate"]["hit"] == 1 and q["participate"]["rate"] == 100.0
    assert q["watch"]["hit"] == 1 and q["watch"]["rate"] == 100.0
    assert q["skip"]["hit"] == 0 and q["skip"]["rate"] == 0.0
