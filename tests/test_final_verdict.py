#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最终结论：机械闸 × 晋级率 × LLM 催化。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import final_verdict as fv  # noqa: E402


def _rows():
    return [{"code": "A", "name": "甲", "pct": 59.3, "theme": "机器人", "boards": 1},
            {"code": "B", "name": "乙", "pct": 64.5, "theme": "光通信", "boards": 1},
            {"code": "C", "name": "丙", "pct": 10.0, "theme": "机器人", "boards": 1},
            {"code": "D", "name": "丁", "pct": 50.0, "theme": "PCB板", "boards": 1}]


def test_three_way_rule():
    views = {"A": {"view": "participate", "reason": "AI 买入"},
             "B": {"view": "watch", "reason": "盘后没过接力准入"},
             "C": {"view": "participate", "reason": "AI 买入"},
             "D": {"view": "skip", "reason": "空间不够"}}
    cat = {"机器人": "strong", "光通信": "weak", "PCB板": "strong"}
    out = {x["code"]: x for x in fv.decide(_rows(), views, cat)}
    assert out["A"]["conclusion"] == "可买"
    assert out["B"]["conclusion"] == "观察"      # 机械未过
    assert out["C"]["conclusion"] == "不买"      # 低于 1 板基准 16.3%
    assert out["D"]["conclusion"] == "不买"      # 机械 skip


def test_weak_catalyst_downgrades():
    out = fv.decide([{"code": "A", "name": "甲", "pct": 59.3, "theme": "机器人"}],
                    {"A": {"view": "participate"}}, {"机器人": "weak"})
    assert out[0]["conclusion"] == "观察"


def test_unknown_catalyst_not_penalised():
    out = fv.decide([{"code": "A", "name": "甲", "pct": 59.3, "theme": "机器人"}],
                    {"A": {"view": "participate"}}, {})
    assert out[0]["conclusion"] == "可买"


def test_priority_only_for_buyable_sorted():
    rows = [{"code": "A", "name": "甲", "pct": 50.0}, {"code": "B", "name": "乙", "pct": 64.5}]
    views = {c: {"view": "participate"} for c in ("A", "B")}
    out = fv.decide(rows, views, {})
    assert [x["code"] for x in out] == ["B", "A"] and out[0]["priority"] == "P1"


def test_llm_actions_override():
    rows = [{"code": "A", "name": "甲", "pct": 59.3, "theme": "机器人"},
            {"code": "B", "name": "乙", "pct": 64.5, "theme": "机器人"},
            {"code": "C", "name": "丙", "pct": 50.0, "theme": "机器人"}]
    views = {c: {"view": "participate"} for c in "ABC"}
    llm = {"A": {"action": "buy", "thesis": "机器人7家+竞价8%"}, "B": {"action": "skip", "thesis": "身位高"},
           "C": {"action": "watch", "thesis": "量能弱"}}
    out = {x["code"]: x for x in fv.decide(rows, views, {"机器人": "strong"}, llm)}
    assert out["A"]["conclusion"] == "可买" and "LLM" in out["A"]["why"]
    assert out["B"]["conclusion"] == "不买" and "LLM 放弃" in out["B"]["why"]
    assert out["C"]["conclusion"] == "观察" and "LLM 观察" in out["C"]["why"]


def test_tracks_split_conflict():
    """低吸轨=机械自决；打板轨=高开未封死；互不否决。"""
    rows = [{"code": "600001", "name": "低吸票", "pct": 21.9, "boards": 1, "gap": 3.6},
            {"code": "600002", "name": "打板票", "pct": 59.3, "boards": 1, "gap": 8.0},
            {"code": "600003", "name": "封死票", "pct": 64.5, "boards": 2, "gap": 10.0}]
    views = {"600001": {"view": "participate"}, "600002": {"view": "skip"},
             "600003": {"view": "skip"}}
    out = {x["code"]: x for x in fv.decide_tracks(rows, views, {})}
    assert out["600001"]["track"] == "低吸轨" and out["600001"]["conclusion"] == "可买"
    assert out["600002"]["track"] == "打板轨" and out["600002"]["conclusion"] == "可买"
    # 封死（gap 10 ≥ 9.5）买不到 → 不进打板轨
    assert out["600003"]["conclusion"] == "观察"


def test_global_daily_cap(monkeypatch):
    monkeypatch.setattr(fv, "MAX_DAILY_PICKS", 2, raising=False)
    rows = [{"code": str(i), "name": f"票{i}", "pct": 50 + i, "boards": 1, "gap": 5.0} for i in range(4)]
    views = {str(i): {"view": "participate"} for i in range(4)}
    out = fv.decide_tracks(rows, views, {})
    assert sum(1 for x in out if x["conclusion"] == "可买") == 2
    assert [x["priority"] for x in out if x["conclusion"] == "可买"] == ["P1", "P2"]


def test_rank_bonus_can_promote():
    rows = [{"code": "A", "name": "总龙", "pct": 54, "boards": 1, "gap": 5.0},
            {"code": "B", "name": "普通", "pct": 55, "boards": 1, "gap": 5.0}]
    views = {c: {"view": "participate"} for c in "AB"}
    out = fv.decide_tracks(rows, views, {}, None, {"A": {"role": "总龙"}})
    assert out[0]["code"] == "A" and out[0]["bonus"] >= 3
