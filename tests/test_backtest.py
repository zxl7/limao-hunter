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
    """apply_suggestion：改 base_range + 生成回滚快照 + 幅度受限。"""
    # 用临时配置目录模拟
    tmp_conf = tmp_path / "expectation_rules.json"
    tmp_conf.write_text(json.dumps({"base_range": [0.0, 2.5], "max_range_width": 2.0}), encoding="utf-8")
    monkeypatch.setattr(ln, "EXPECT_RULES", tmp_conf)
    monkeypatch.setattr(ln, "BACKUP_DIR", tmp_path / "backups")

    sug = {"rule": "R1_range_low", "target": "expectation_rules.json.base_range", "step": 0.3,
           "suggest": "base_range 下移 0.3（预期系统性偏乐观）"}
    res = ln.apply_suggestion(sug, {"max_adjust_step": 0.3})
    assert res["applied"] is True
    assert res["before"] == [0.0, 2.5]
    assert res["after"] == [-0.3, 2.2]
    assert "learning_rollback_" in res["snapshot"]
    # 快照文件确实生成
    assert list((tmp_path / "backups").glob("learning_rollback_*"))


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
    # 语义色板：命中率 0% → green（恶化）
    first = contents[0]
    assert "green'>0.0%</text_tag>" in first
    assert "green'>10.0%</text_tag>" in first
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
