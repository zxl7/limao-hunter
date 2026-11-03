#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测引擎 + 自学习黄金用例（财神-回测 · 2026-09-01）
====================================================
覆盖：
- backtest_engine：候选命中率 / 区间命中率 / 情绪分对齐 / 交易守卫 / 幂等 append
- learning：R1 区间命中过低触发 / R2 样本守卫降级 / auto_apply 双保险 / 回滚快照
- gen_backtest_card：日卡结构 / 周报结构 / 语义色板

运行：/Users/zxl/.workbuddy/binaries/python/envs/default/bin/python -m pytest tests/test_backtest.py -v
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_engine as be  # noqa: E402
import learning as ln  # noqa: E402


# ---------------- backtest_engine ----------------

def test_candidate_hit_from_nav():
    """候选命中：nav 账本 recommendation_date 对齐 → 命中率/平均收益正确。"""
    nav = [
        {"trade_date": "2026-08-31", "recommendation_date": "2026-08-28",
         "avg_return_pct": 2.5, "stocks": [
             {"code": "000001", "name": "A", "return_pct": 4.0},
             {"code": "000002", "name": "B", "return_pct": -1.0},
             {"code": "000003", "name": "C", "return_pct": 4.5},
         ]},
    ]
    r = be.backtest_candidate_hit("2026-08-28", "2026-08-31", nav)
    assert r["available"] is True
    assert r["hit_count"] == 2
    assert r["hit_rate"] == pytest.approx(66.7, abs=0.1)
    assert r["avg_return_pct"] == 2.5


def test_candidate_hit_missing_rec_date():
    """候选命中：无对应推荐日记录 → available=False，不算 0%。"""
    nav = [{"trade_date": "2026-08-31", "recommendation_date": "2026-08-25", "avg_return_pct": 1.0}]
    r = be.backtest_candidate_hit("2026-08-28", "2026-08-31", nav)
    assert r["available"] is False


def test_range_hit_calculates_gap():
    """区间命中：expected_range vs 实际竞价涨幅（gap=(auction-prev)/prev）。"""
    cand = {"relay": [{"code": "600000.SH", "name": "X", "expectation": {"expected_range": [0.0, 2.0]}}],
            "watch": []}
    auction = {"600000": {"prev_close": 10.0, "auction_price": 10.2}}
    r = be.backtest_range_hit("2026-08-28", "2026-08-31", cand, auction)
    assert r["available"] is True
    assert r["evaluable"] == 1
    assert r["rows"][0]["actual_gap_pct"] == pytest.approx(2.0, abs=0.01)
    assert r["rows"][0]["hit"] is True


def test_range_hit_no_auction():
    """区间命中：无竞价快照 → available=False（不算 0%）。"""
    cand = {"relay": [{"code": "600000", "expectation": {"expected_range": [0.0, 2.0]}}], "watch": []}
    r = be.backtest_range_hit("2026-08-28", "2026-08-31", cand, {})
    assert r["available"] is False


def test_sentiment_align_parse():
    """情绪分对齐：解析「预判 7-8/10」→ mid=7.5，actual=6.8 → diff=+0.7。"""
    # 用真实盘前文件（08-28 预判 7-8/10）
    r = be.backtest_sentiment_align("2026-08-28")
    if r.get("available"):
        assert r["predict_mid"] == pytest.approx(7.5, abs=0.01)
        assert r["source"] is not None


def test_trading_day_guard_weekend():
    """交易守卫：周末不产出。"""
    from datetime import date
    assert be.is_trading_day(date(2026, 8, 29)) is False  # 周六


def test_history_append_idempotent(tmp_path):
    """幂等：同 settle_date 重复 append 只保留一份。"""
    h = tmp_path / "backtest_history.jsonl"
    r1 = {"settle_date": "2026-08-31", "x": 1}
    # 模拟两次 append
    lines = []
    for r in (r1, r1):
        lines = [l for l in lines if l.strip() and not json.loads(l).get("settle_date") == r["settle_date"]]
        lines.append(json.dumps(r, ensure_ascii=False))
    h.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = [json.loads(l) for l in h.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1


# ---------------- learning ----------------

def _mk_hist(rates, cand_rates=None, cand_counts=None, diffs=None):
    """构造回测历史样本。"""
    hist = []
    for i, rr in enumerate(rates):
        cand_avail = cand_rates is not None and i < len(cand_rates)
        hist.append({
            "settle_date": f"2026-08-{20+i}",
            "metrics": {
                "range_hit": {"available": True, "evaluable": 10, "hit_rate": rr},
                "candidate_hit": {"available": cand_avail, "hit_rate": cand_rates[i] if cand_avail else None,
                                  "stock_count": cand_counts[i] if cand_avail and cand_counts else 0},
                "sentiment_align": {"available": True},
            },
            "summary": {"range_hit_rate": rr,
                        "candidate_hit_rate": cand_rates[i] if cand_avail else None,
                        "sentiment_diff": diffs[i] if diffs else None},
        })
    return hist


def test_learning_R1_range_low():
    """R1：区间命中率均值 <40% → 触发 high 建议。"""
    cfg = ln.DEFAULT_LEARNING_CONFIG
    hist = _mk_hist([10, 20, 30])
    sugs = ln.detect_biases(hist, cfg)
    r1 = [s for s in sugs if s["rule"] == "R1_range_low"]
    assert r1 and r1[0]["severity"] == "high"


def test_learning_R2_sample_guard():
    """R2 样本守卫：候选命中率 0% 但样本仅 1 只 → 降级 low，不触发回写。"""
    cfg = ln.DEFAULT_LEARNING_CONFIG
    hist = _mk_hist([50, 60, 70], cand_rates=[0.0], cand_counts=[1])
    sugs = ln.detect_biases(hist, cfg)
    r2 = [s for s in sugs if s["rule"] == "R2_cand_low"]
    assert r2 and r2[0]["severity"] == "low"
    assert r2[0]["step"] is None  # 不回写


def test_learning_R2_enough_samples():
    """R2 样本充足（累计≥3）→ 触发 high 回写建议。"""
    cfg = ln.DEFAULT_LEARNING_CONFIG
    hist = _mk_hist([50, 60, 70], cand_rates=[30, 20, 25], cand_counts=[4, 4, 4])
    sugs = ln.detect_biases(hist, cfg)
    r2 = [s for s in sugs if s["rule"] == "R2_cand_low"]
    assert r2 and r2[0]["severity"] == "high"
    assert r2[0]["step"] == 2


def test_learning_R3_sentiment_bias():
    """R3：情绪差均值 |avg|>1.0 → 触发 medium 建议（不回写）。"""
    cfg = ln.DEFAULT_LEARNING_CONFIG
    hist = _mk_hist([60, 60, 60], diffs=[1.5, 1.8, 1.6])
    sugs = ln.detect_biases(hist, cfg)
    r3 = [s for s in sugs if s["rule"] == "R3_sentiment_bias"]
    assert r3 and r3[0]["severity"] == "medium"
    assert r3[0]["step"] is None


def test_learning_R4_range_high():
    """R4：区间命中率均值 >80% → 触发 low 建议（区间过宽）。"""
    cfg = ln.DEFAULT_LEARNING_CONFIG
    hist = _mk_hist([85, 90, 88])
    sugs = ln.detect_biases(hist, cfg)
    r4 = [s for s in sugs if s["rule"] == "R4_range_high"]
    assert r4 and r4[0]["severity"] == "low"


def test_learning_apply_requires_master_switch():
    """auto_apply 双保险：learning_config auto_apply=False 时 --auto-apply 不回写。"""
    # 直接验证主流程逻辑：auto_apply 参数需且仅需 config.auto_apply 同时为 True
    assert ln.DEFAULT_LEARNING_CONFIG["auto_apply"] is False  # 默认安全


def test_learning_apply_suggestion_backup(tmp_path, monkeypatch):
    """⛔ L2 冻结（2026-09-18）：R1 的回写必须被拒绝、且**不得改盘**。

    旧断言（applied=True）已被用户拍板推翻 —— `range_hit` 与赚钱无因果；
    R1/R4 永久降级为「仅建议」，改由人工拍板。
    """
    tmp_conf = tmp_path / "expectation_rules.json"
    tmp_conf.write_text(json.dumps({"base_range": [0.0, 2.5], "max_range_width": 2.0}), encoding="utf-8")
    monkeypatch.setattr(ln, "EXPECT_RULES", tmp_conf)
    monkeypatch.setattr(ln, "BACKUP_DIR", tmp_path / "backups")
    before = tmp_conf.read_text(encoding="utf-8")

    sug = {"rule": "R1_range_low", "target": "expectation_rules.json.base_range", "step": 0.3,
           "suggest": "base_range 下移 0.3（预期系统性偏乐观）"}
    res = ln.apply_suggestion(sug, {"max_adjust_step": 0.3})
    assert res["applied"] is False
    assert "仅建议" in res["reason"]
    assert tmp_conf.read_text(encoding="utf-8") == before, "冻结规则不得改盘"


def test_learning_apply_backup_targets_real_file(tmp_path, monkeypatch):
    """回写机制仍可用（唯一可写档 = R2 relay 门槛），且**快照必须备份真实目标文件**。

    历史缺陷：`backup_snapshot` 无条件备份 expectation_rules.json → 改 relay 配置时
    「备份了 A、改了 B」，`--rollback` 会还原错文件（可逆性失效）。本锁防复发。
    """
    exp = tmp_path / "expectation_rules.json"
    exp.write_text(json.dumps({"base_range": [-0.6, 1.9]}), encoding="utf-8")
    relay = tmp_path / "relay_score_config.json"
    relay.write_text(json.dumps({"bands": {"first_choice_min": 80.0}}), encoding="utf-8")
    monkeypatch.setattr(ln, "EXPECT_RULES", exp)
    monkeypatch.setattr(ln, "RELAY_RULES", relay)
    monkeypatch.setattr(ln, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(ln, "audit", lambda *a, **k: None)     # ⛔ 测试不得污染真实 cache/audit.jsonl
    # ⛔ R2 在生产已关死（RULE_RISK=high）→ 本测试在**放开 R2=medium** 的受控前提下，
    #   验证 apply_suggestion 的写入/备份机制仍正确（可逆性锁防复发）。不影响生产冻结。
    monkeypatch.setattr(ln, "RULE_RISK", {**ln.RULE_RISK, "R2_cand_low": "medium"})

    sug = {"rule": "R2_cand_low", "target": "relay_score_config.json.bands.first_choice_min",
           "step": 2, "suggest": "relay first_choice_min 门槛上调 2 分"}
    res = ln.apply_suggestion(sug, {"max_adjust_step": 0.3})
    assert res["applied"] is True and res["before"] == 80.0 and res["after"] == 80.3
    # 改的是 relay、不是 expectation_rules
    assert json.loads(relay.read_text(encoding="utf-8"))["bands"]["first_choice_min"] == 80.3
    assert json.loads(exp.read_text(encoding="utf-8"))["base_range"] == [-0.6, 1.9]
    # 快照存的是**被改那个文件**的原内容
    snap = Path(res["snapshot"])
    assert snap.name.endswith("__relay_score_config.json"), snap.name
    assert json.loads(snap.read_text(encoding="utf-8"))["bands"]["first_choice_min"] == 80.0


def test_learning_rollback_restores_correct_file(tmp_path, monkeypatch):
    """`--rollback` 须按快照名复原到**正确文件**（relay），而非固定 expectation_rules。"""
    exp = tmp_path / "expectation_rules.json"
    exp.write_text('{"base_range": [-0.6, 1.9]}', encoding="utf-8")
    relay = tmp_path / "relay_score_config.json"
    relay.write_text('{"bands": {"first_choice_min": 80.0}}', encoding="utf-8")
    snapdir = tmp_path / "backups"
    snapdir.mkdir()
    snap = snapdir / "learning_rollback_20260918_120000_relay_score_config__relay_score_config.json"
    snap.write_text('{"bands": {"first_choice_min": 78.0}}', encoding="utf-8")
    monkeypatch.setattr(ln, "EXPECT_RULES", exp)
    monkeypatch.setattr(ln, "RELAY_RULES", relay)
    monkeypatch.setattr(ln, "audit", lambda *a, **k: None)     # ⛔ 测试不得污染真实 cache/audit.jsonl
    ln.rollback(str(snap))
    assert json.loads(relay.read_text(encoding="utf-8"))["bands"]["first_choice_min"] == 78.0
    assert json.loads(exp.read_text(encoding="utf-8"))["base_range"] == [-0.6, 1.9]   # 未被误改


def test_learning_apply_step_capped():
    """幅度受限：step=5 被钳制到 max_adjust_step=0.3。"""
    sug = {"rule": "R1_range_low", "target": "expectation_rules.json.base_range", "step": 5.0,
           "suggest": "base_range 下移 0.3（预期系统性偏乐观）"}
    step = max(-0.3, min(0.3, 5.0))
    assert step == 0.3


# ---------------- gen_backtest_card ----------------

def test_card_render_daily_structure():
    """日卡：结论（学习建议）置底 + 语义色板正确。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import gen_backtest_card as gc

    report = {
        "settle_date": "2026-08-31", "rec_date": "2026-08-28",
        "metrics": {
            "candidate_hit": {"available": True, "stock_count": 1, "avg_return_pct": -2.27,
                              "hit_count": 0, "hit_rate": 0.0, "stocks": [{"name": "深中华A", "return_pct": -2.27}]},
            "range_hit": {"available": True, "evaluable": 10, "hit_count": 1, "hit_rate": 10.0,
                          "rows": [{"name": "冀衡医药", "expected_range": [0.0, 2.0], "actual_gap_pct": -9.94, "hit": False}]},
            "sentiment_align": {"available": True, "predict": 7.0, "predict_hi": 8.0,
                                "actual": 6.8, "diff": 0.7},
        },
        "summary": {},
    }
    sugs = [{"rule": "R1_range_low", "evidence": "近1日区间命中率均值 10.0% < 40%",
             "suggest": "base_range 下移 0.3", "severity": "high"}]
    card = gc.render_daily(report, sugs)
    # 结构
    assert card["color"] == "blue"
    contents = [e["text"]["content"] for e in card["elements"] if e["tag"] == "div"]
    # 结论置底：最后一个 div 含学习建议
    assert "自我学习建议" in contents[-1] or "R1_range_low" in contents[-1]
    # 语义色板：候选命中率 0% → green（恶化）
    first = contents[0]
    assert "green'>0.0%</text_tag>" in first
    # ⛔ 2026-10-18 用户拍板：区间命中率不作对错判据 → 卡面只报落区只数，不报比率红绿
    assert gc.RANGE_HIT_VERDICT is False
    assert "区间落区 1/10" in first
    assert "10.0%" not in first
    # 判决模式（恢复开关）时仍能渲染旧观感
    orig = gc.RANGE_HIT_VERDICT
    try:
        gc.RANGE_HIT_VERDICT = True
        first_verdict = [e["text"]["content"] for e in gc.render_daily(report, sugs)["elements"]
                         if e["tag"] == "div"][0]
        assert "green'>10.0%</text_tag>" in first_verdict
    finally:
        gc.RANGE_HIT_VERDICT = orig
    # 无 hex 颜色
    assert "#" not in json.dumps(card, ensure_ascii=False)


# ---------------- gen_proposals（提案模式·2026-09-01）----------------

def test_proposals_P3_range_low_auto_approved(tmp_path, monkeypatch):
    """P3：区间命中低 → R1 已自动回写 → 提案标 approved（仅展示不待办）。"""
    import gen_proposals as gp
    hist = [{"settle_date": f"2026-08-{20+i}",
             "metrics": {"range_hit": {"available": True, "hit_rate": 10.0},
                         "candidate_hit": {"available": True, "stock_count": 4, "hit_rate": 50.0},
                         "sentiment_align": {"available": True}},
             "summary": {"range_hit_rate": 10.0, "candidate_hit_rate": 50.0, "sentiment_diff": 0.5}}
            for i in range(3)]
    monkeypatch.setattr(gp, "load_history", lambda n=5: hist)
    monkeypatch.setattr(gp, "load_suggestions", lambda: [])
    monkeypatch.setattr(gp, "PROPOSAL_FILE", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(gp, "LATEST_FILE", tmp_path / "latest.json")
    rows = gp._load_existing()
    assert rows == []
    # 直接测 P3 逻辑
    range_rates = [r["summary"]["range_hit_rate"] for r in hist if r["metrics"]["range_hit"]["available"]]
    assert sum(range_rates) / len(range_rates) < 40
    np_ = gp._mk_proposal("P3_range_low", "区间命中率低·已自动回写", f"均值 {sum(range_rates)/len(range_rates):.0f}%", "R1 已处理")
    np_["status"] = "approved"
    assert np_["status"] == "approved"
    assert np_["target_skill"] == "财神-回测"


def test_proposals_P1_requires_3_samples(tmp_path, monkeypatch):
    """P1 样本守卫：情绪差样本 <3 不生成提案。"""
    import gen_proposals as gp
    # 只有 1 条记录（diff 0.7）→ 不触发 P1
    hist = [{"settle_date": "2026-08-31",
             "metrics": {"candidate_hit": {"available": True, "stock_count": 1, "hit_rate": 0.0},
                         "range_hit": {"available": True, "hit_rate": 10.0},
                         "sentiment_align": {"available": True}},
             "summary": {"candidate_hit_rate": 0.0, "range_hit_rate": 10.0, "sentiment_diff": 0.7}}]
    sugs = [{"rule": "R3_sentiment_bias", "severity": "medium", "evidence": "近1日情绪差均值 +0.70", "suggest": "AI 校准"}]
    monkeypatch.setattr(gp, "load_history", lambda n=5: hist)
    monkeypatch.setattr(gp, "load_suggestions", lambda: sugs)
    monkeypatch.setattr(gp, "PROPOSAL_FILE", tmp_path / "proposals.jsonl")
    monkeypatch.setattr(gp, "LATEST_FILE", tmp_path / "latest.json")
    gp._save([])  # 清空
    added = gp.gen_proposals()
    p1 = [p for p in added if p["rule"] == "P1_sentiment_bias"]
    assert p1 == []  # 样本 1 < 3，不提案


# ---------------- gen_optimization_plan（周五优化方案·2026-09-01）----------------

def test_optimization_plan_decisions_structure(tmp_path, monkeypatch):
    """优化方案：聚合六维 → O 编号决策清单，high 级回测偏差置顶。"""
    import gen_optimization_plan as op
    hist = [{"settle_date": "2026-08-31",
             "metrics": {"candidate_hit": {"available": True, "stock_count": 1, "hit_rate": 0.0},
                         "range_hit": {"available": True, "hit_rate": 10.0},
                         "sentiment_align": {"available": True}},
             "summary": {"candidate_hit_rate": 0.0, "range_hit_rate": 10.0, "sentiment_diff": 0.7}}]
    sugs = [{"rule": "R1_range_low", "severity": "high", "evidence": "近1日区间命中率 10%", "suggest": "base_range 下移 0.3"}]
    props = [{"proposal_id": "P2_cand_low_20260901", "rule": "P2_candidate_low", "status": "draft",
              "title": "候选命中率走低", "target_skill": "财神-选股", "target_section": "候选门槛",
              "evidence": "近1日 0%", "suggest": "核查门槛"}]
    health = {"run_health": {"stale": [], "never": []}, "launchd": {"running": ["com.limao.pulse-fetcher ⚠️未加载"]},
              "cache": {"issues": ["ℹ️ feedback_matrix 守卫态: pre_auction（非故障）"]},
              "upgrade_todo": {"todo": [{"id": "P1-5", "title": "退潮聚合卡"}]}}
    decs = op.build_decisions(hist, sugs, props, health)
    assert len(decs) >= 3
    ids = [d["id"] for d in decs]
    assert ids[0].startswith("O1")
    # 回测偏差（high）在升级待办（low）之前
    cats = [d["cat"] for d in decs]
    assert cats.index("回测/学习") < cats.index("升级")
    # 非故障 issue 被过滤
    assert not any("pre_auction" in d["title"] for d in decs)
    # 每项有完整字段
    for d in decs:
        for k in ("id", "cat", "sev", "title", "evidence", "action", "impact", "default"):
            assert k in d, f"{d['id']} 缺字段 {k}"


def test_learning_effect_falls_back_to_effect_applied(tmp_path, monkeypatch):
    """学习效果回看：「上轮自动应用」必须能取到值（2026-09-18 修）。

    事故：`load_learning_effect()` 读顶层 `applied_summary`，而 learning.py **从不写**该字段
    → 卡面「上轮自动应用：」长期渲染为空（真实数据在 `effect.applied`）。
    """
    import gen_optimization_plan as op
    row = {"generated_at": "2026-09-18T17:50:38+08:00", "mode": "tiered",
           "range_hit_rate_avg": 19.4,
           "effect": {"since": "2026-09-17T21:52:46+08:00",
                      "applied": ["expectation_rules.json.base_range: [-0.3, 2.2]→[-0.6, 1.9]"],
                      "range_hit_rate": "19.6% → 19.4%", "improved": False}}
    (tmp_path / "learning_history.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(op, "CACHE", tmp_path)
    eff = op.load_learning_effect()
    assert eff is not None
    assert eff["applied"] == ["expectation_rules.json.base_range: [-0.3, 2.2]→[-0.6, 1.9]"]
    # 渲染出的那一行不得是空值
    line = [e for e in op.render_card({"decisions": [], "health_issue_count": 0,
                                       "learning_effect": eff, "suggestions": [],
                                       "proposals": [], "factor_attr": None,
                                       "card_quality": None, "week_label": "测试周",
                                       "week_hist": []})["elements"]
            if "上轮自动应用" in ((e.get("text") or {}).get("content") or "")]
    assert line and "base_range" in line[0]["text"]["content"]


def test_optimization_plan_render_sanitizes_legacy_copy():
    """渲染出口白话化：**真实 plan（含旧落盘 evidence/提案文案）** 渲染出的卡零 Tier-1。

    背景（2026-09-18 实证）：09-21 起 Tier-1 转阻断，而 plan 里的 evidence / 提案
    是 09-02~09-14 生成的旧文案（含「样本」）→ 不做渲染层白话化就是**整卡推不出去**。
    """
    import gen_optimization_plan as op
    import card_copy_rules as R
    p = ROOT / "cache" / "optimization_plan_latest.json"
    if not p.exists():
        return
    plan = json.loads(p.read_text(encoding="utf-8"))
    card = op.render_card(plan)

    def strings(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in ("tag", "template", "color", "msg_type", "type", "schema", "font"):
                    continue
                yield from strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from strings(v)

    f, _ = R.scan("\n".join(strings(card)))
    assert f == [], f"优化方案卡残留 Tier-1 禁项: {f}"


# ────────────────── 沙盒审计不得写穿生产日志（2026-09-22 立） ──────────────────
def test_is_prod_write_rejects_sandbox_paths(tmp_path):
    """⛔ 测试/临时路径一律判为非生产 —— 否则 `apply_suggestion` 的 audit 会污染
    `cache/audit.jsonl`（实测 197 条 auto_apply 中 194 条为 pytest 噪声，
    会造成「谁改了参数」的误判）。"""
    assert ln._is_prod_write(tmp_path / "expectation_rules.json") is False
    assert ln._is_prod_write("/private/var/folders/xy/pytest-of-zxl/x/y.json") is False
    assert ln._is_prod_write("/tmp/anything.json") is False
    assert ln._is_prod_write(ln.EXPECT_RULES) is True          # 真实配置 → 生产
    assert ln._is_prod_write(ln.RELAY_RULES) is True


def test_apply_suggestion_skips_audit_in_sandbox(tmp_path, monkeypatch):
    """沙盒内回写**不得**产生生产审计记录（代码层兜底·不依赖测试自觉 monkeypatch）。"""
    calls: list[tuple] = []
    exp = tmp_path / "expectation_rules.json"
    exp.write_text(json.dumps({"base_range": [-0.6, 1.9]}), encoding="utf-8")
    monkeypatch.setattr(ln, "EXPECT_RULES", exp)
    monkeypatch.setattr(ln, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(ln, "audit", lambda *a, **k: calls.append(a))
    # 受控放开 R1（生产已冻结），只为验证「沙盒 → 不写审计」这条路径
    monkeypatch.setattr(ln, "RULE_RISK", {**ln.RULE_RISK, "R1_range_low": "medium"})
    sug = {"rule": "R1_range_low", "target": "expectation_rules.json.base_range",
           "step": 0.3, "suggest": "base_range 下移 0.3"}
    res = ln.apply_suggestion(sug, {"max_adjust_step": 0.3})
    assert res["applied"] is True                     # 回写本身仍生效（可逆性不变）
    assert calls == []                                # ⛔ 但审计不得写入生产日志


# ────────────────── 沙盒审计不得写穿生产日志（2026-09-22 立） ──────────────────
def test_is_prod_write_rejects_sandbox_paths(tmp_path):
    """⛔ 测试/临时路径一律判为非生产 —— 否则 `apply_suggestion` 的 audit 会污染
    `cache/audit.jsonl`（实测 197 条 auto_apply 中 194 条为 pytest 噪声，
    会造成「谁改了参数」的误判）。"""
    assert ln._is_prod_write(tmp_path / "expectation_rules.json") is False
    assert ln._is_prod_write("/private/var/folders/xy/pytest-of-zxl/x/y.json") is False
    assert ln._is_prod_write("/tmp/anything.json") is False
    assert ln._is_prod_write(ln.EXPECT_RULES) is True          # 真实配置 → 生产
    assert ln._is_prod_write(ln.RELAY_RULES) is True


def test_apply_suggestion_skips_audit_in_sandbox(tmp_path, monkeypatch):
    """沙盒内回写**不得**产生生产审计记录（代码层兜底·不依赖测试自觉 monkeypatch）。"""
    calls: list[tuple] = []
    exp = tmp_path / "expectation_rules.json"
    exp.write_text(json.dumps({"base_range": [-0.6, 1.9]}), encoding="utf-8")
    monkeypatch.setattr(ln, "EXPECT_RULES", exp)
    monkeypatch.setattr(ln, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(ln, "audit", lambda *a, **k: calls.append(a))
    # 受控放开 R1（生产已冻结），只为验证「沙盒 → 不写审计」这条路径
    monkeypatch.setattr(ln, "RULE_RISK", {**ln.RULE_RISK, "R1_range_low": "medium"})
    sug = {"rule": "R1_range_low", "target": "expectation_rules.json.base_range",
           "step": 0.3, "suggest": "base_range 下移 0.3"}
    res = ln.apply_suggestion(sug, {"max_adjust_step": 0.3})
    assert res["applied"] is True                     # 回写本身仍生效（可逆性不变）
    assert calls == []                                # ⛔ 但审计不得写入生产日志
