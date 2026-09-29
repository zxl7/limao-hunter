#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测自我学习「分级自动 + 防过度」回归锁（2026-09-17 用户「全方位自动学习+迭代」）。

背景：此前 19 次学习全「建议 N 条 / 应用 0 条」（auto_apply=False + 只认 high）→ 闭环断裂。
本锁覆盖：模式解析 / 风险分级与严重度解耦 / 防过度三闸 / R5 AI 质量数据源。
⛔ 纯离线：不触真实配置、不落盘、不外呼。
"""
import json
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
    """⛔ 核心：severity=high（偏差严重）≠ risk（敢不敢自动改）——两者必须解耦。

    2026-09-18 晚「关死」后：**全部 7 条规则 RULE_RISK=high** → 任何 severity 都不自动回写。
    解耦的意义在于：即便某条偏差严重（severity=high），只要 risk=high 就只建议、不改盘；
    将来若有意放开某条（如 R2 改回 medium），仍须靠 `RULE_RISK` 一处开关，而非 severity。
    """
    # 2026-09-18 关死：R1/R4/R2 全部升 high → 永不回写
    assert L.RULE_RISK["R1_range_low"] == "high", "R1 必须冻结为仅建议（base_range 不再自动下移）"
    assert L.RULE_RISK["R4_range_high"] == "high", "R4 必须冻结为仅建议（max_range_width 不再自动收紧）"
    assert L.RULE_RISK["R2_cand_low"] == "high", "R2 必须关死为仅建议（relay 门槛不再自动调）"
    assert L.RULE_RISK["R3_sentiment_bias"] == "high"      # 语义类 → 仅建议
    assert L.RULE_RISK["R5_ai_quality"] == "high"          # AI 判定质量 → 仅建议
    # 默认策略：低/中自动、高仅建议（但当前无规则落在 low/medium → 实际 0 自动回写）
    assert L.DEFAULT_LEARNING_CONFIG["tier_policy"] == {"low": True, "medium": True, "high": False}


def test_frozen_rules_never_writable_in_any_mode():
    """⛔ L2 冻结的可执行语义：`_writable()` 必须挡住 R1/R4 —— 即使带 step、即使在 legacy 模式。

    背景（真实漏洞）：legacy `high_only` 分支按 `severity` 选，而 R1 的 severity 恰是 high
    → 若只在 tiered 分支过滤，`high_only` 仍能把 base_range 改掉。故判据必须是统一闸门。
    """
    frozen = {"rule": "R1_range_low", "target": "expectation_rules.json.base_range",
              "step": 0.3, "severity": "high", "suggest": "base_range 下移 0.3"}
    assert L._writable(frozen) is False
    assert L._writable({**frozen, "rule": "R4_range_high"}) is False
    # 语义类（step=None）在任何风险等级下都不可回写
    assert L._writable({"rule": "R3_sentiment_bias", "step": None}) is False
    # 未登记规则 → 安全默认 high
    assert L._writable({"rule": "R_UNKNOWN", "step": 1}) is False
    # 2026-09-18 关死后：原先唯一可自动的 R2（medium）也升 high → 无任何规则可回写
    assert L._writable({"rule": "R2_cand_low", "step": 2}) is False
    # 反证：即使把 tier_policy 的 medium 打开，RULE_RISK=high 仍是总闸
    assert all(L._writable({"rule": r, "step": 1}) is False
               for r in L.RULE_RISK), "RULE_RISK 任一规则为 high → 一律不可回写"


def test_apply_suggestion_refuses_frozen_rule(tmp_path, monkeypatch):
    """入口再拦一道：即使被误传，`apply_suggestion` 也必须拒绝且**不改盘**。"""
    cfg_file = tmp_path / "expectation_rules.json"
    cfg_file.write_text('{"base_range": [-0.6, 1.9]}', encoding="utf-8")
    monkeypatch.setattr(L, "EXPECT_RULES", cfg_file)
    monkeypatch.setattr(L, "BACKUP_DIR", tmp_path / "backups")
    before = cfg_file.read_text(encoding="utf-8")
    out = L.apply_suggestion(
        {"rule": "R1_range_low", "target": "expectation_rules.json.base_range",
         "step": 0.3, "suggest": "base_range 下移 0.3"}, {})
    assert out["applied"] is False and "仅建议" in out["reason"]
    assert cfg_file.read_text(encoding="utf-8") == before, "冻结规则不得写盘"
    # R2 关死后（2026-09-18 晚）：误传 R2 也必须拒绝且不改盘
    relay = tmp_path / "relay_score_config.json"
    relay.write_text(json.dumps({"bands": {"first_choice_min": 80.0}}), encoding="utf-8")
    monkeypatch.setattr(L, "RELAY_RULES", relay)
    out2 = L.apply_suggestion(
        {"rule": "R2_cand_low", "target": "relay_score_config.json.bands.first_choice_min",
         "step": 2, "suggest": "relay first_choice_min 门槛上调 2 分"}, {})
    assert out2["applied"] is False and "仅建议" in out2["reason"]
    assert json.loads(relay.read_text(encoding="utf-8"))["bands"]["first_choice_min"] == 80.0, "R2 冻结不得写盘"


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


# ── L1 反事实账本样本源（2026-09-18 用户拍板「先做 124」）─────────────
AUDIT_DOC = {
    "schema": "decision_audit_v1",
    "generated_at": "2026-09-18T19:00:00+08:00",
    "entry_gap_max": 5.0,
    "window": {"days": 20, "from": "2026-08-20", "to": "2026-09-18", "rows": 286, "codes": 159},
    "pool_matrix": {
        "relay": {"n": 87, "exec_n": 61, "exec_ret_n": 61, "exec_rate": 70.1,
                  "ret_exec": 1.77, "win_rate": 60.7, "ret_t3_close": 2.1, "mdd_t0_t3": -3.2},
        "watch": {"n": 199, "exec_n": 166, "exec_ret_n": 166, "exec_rate": 83.4,
                  "ret_exec": 0.2, "win_rate": 46.8},
        "tiny": {"n": 5, "exec_n": 5, "exec_ret_n": 2, "ret_exec": -1.0, "win_rate": 0.0},
    },
    "gate": {"可买（≤硬线）": {"n": 100, "ret_exec": 0.67, "win_rate": 50.0}},
}


def test_load_audit_window_gates_and_shapes(tmp_path):
    """账本 → 学习窗口：形状正确 + 采信门（收益条数 < 30 → usable=False，只呈现）。"""
    p = tmp_path / "decision_audit.json"
    p.write_text(json.dumps(AUDIT_DOC, ensure_ascii=False), encoding="utf-8")
    aw = L.load_audit_window(p)
    assert aw["rows"] == 286 and aw["days"] == 20 and aw["min_n"] == 30
    assert aw["pools"]["relay"]["usable"] is True          # 61 条 ≥ 30 且 EV 可得
    assert aw["pools"]["watch"]["usable"] is True
    assert aw["pools"]["tiny"]["usable"] is False          # 2 条 < 30 → 不采信
    assert aw["pools"]["relay"]["ev_t3_close"] == 2.1
    assert aw["gate"]["可买（≤硬线）"]["ev_pct"] == 0.67
    assert "relay 收益均值 +1.77%" in L._audit_ref(aw)


def test_load_audit_window_degrades_honestly(tmp_path):
    """账本缺失 / schema 不符 / 空窗口 → None（⛔ 不编造、不抛错）。"""
    assert L.load_audit_window(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema":"other","window":{"rows":10}}', encoding="utf-8")
    assert L.load_audit_window(bad) is None
    empty = tmp_path / "empty.json"
    empty.write_text('{"schema":"decision_audit_v1","window":{"rows":0}}', encoding="utf-8")
    assert L.load_audit_window(empty) is None
    assert L._audit_ref(None) == ""


def _hist(range_rate: float) -> list[dict]:
    return [{
        "settle_date": "2026-09-18",
        "summary": {"range_hit_rate": range_rate, "candidate_hit_rate": 10.0, "sentiment_diff": 0.0},
        "metrics": {"range_hit": {"available": True, "evaluable": 10},
                    "candidate_hit": {"available": True, "stock_count": 5},
                    "sentiment_align": {"available": False}},
    }]


def _cfg_r12_only() -> dict:
    """只开 R1/R2 的检测配置 —— 让测试纯离线（R5/R6/R7 会外呼子进程/读真实文件）。"""
    rules = {k: {**v, "enabled": k in ("R1_range_low", "R2_cand_low")}
             for k, v in L.DEFAULT_LEARNING_CONFIG["rules"].items()}
    return {"rules": rules, "min_samples": 3}


def test_detect_biases_uses_audit_ref_and_stays_card_safe(tmp_path):
    """L1：建议里带上**全候选池级收益旁证**，且文案必须过卡面 Tier-1 门禁。

    为什么测「门禁」：`learning_suggestions.json` 的 evidence/suggest 会流进飞书卡
    （`gen_backtest_card` / `gen_optimization_plan`）——09-21 起 Tier-1 词直接阻断推送。
    """
    from card_copy_rules import scan
    aw = L.load_audit_window(_write_audit(tmp_path))
    sugs = L.detect_biases(_hist(10.0), _cfg_r12_only(), audit=aw)
    by_rule = {s["rule"]: s for s in sugs}
    assert "R1_range_low" in by_rule and "R2_cand_low" in by_rule
    assert "反事实账本对照" in by_rule["R1_range_low"]["evidence"]
    assert "relay 收益均值 +1.77%" in by_rule["R2_cand_low"]["evidence"]
    # 无账本时不得凭空出现该旁证
    no_audit = L.detect_biases(_hist(10.0), _cfg_r12_only())
    assert all("反事实账本对照" not in s["evidence"] for s in no_audit)
    for s in sugs:
        fails, _warns = scan(f"{s.get('evidence','')} {s.get('suggest','')} {s.get('note','')}")
        assert not fails, f"{s['rule']} 文案命中卡面阻断词：{fails}"


def _write_audit(tmp_path) -> str:
    p = tmp_path / "decision_audit.json"
    p.write_text(json.dumps(AUDIT_DOC, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_frozen_rule_still_emits_suggestion_but_no_step_write():
    """冻结 ≠ 静默：R1 仍须产出建议（供人工拍板），只是不再有自动回写资格。"""
    sugs = L.detect_biases(_hist(10.0), _cfg_r12_only())
    r1 = next(s for s in sugs if s["rule"] == "R1_range_low")
    assert r1["severity"] == "high" and r1["step"] == 0.3     # 改动量仍可见（供拍板）
    assert L._writable(r1) is False                            # 但不可自动回写
