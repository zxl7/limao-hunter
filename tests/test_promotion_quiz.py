#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""晋级三问 · 回归（高胜率 / 可晋级 / 板块地位高）

守四件事：
1. **阈值对得上**：40% 晋级率、8 次涨停样本门槛、1~2 板、3 家题材（改阈值必须改测试）。
2. **「不知道」≠「不行」**：档案缺失时该问为 None，⛔ 不能按不通过算（否则次新股被判死）。
3. **最容易骗人的两格**：4 次涨停蒙对 3 次（67%）、题材只有自己 1 家的孤票。
4. **纯判定**：annotate 不改顺序、不改分数、不产名额/仓位。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wealth_code.core import promotion_quiz as P  # noqa: E402


def _cand(code="600001", name="票", boards=2, role="准龙头", theme=10):
    return {"code": code, "name": name, "boards": boards,
            "leader_role": role, "theme_mates_cnt": theme}


def _gene(pr=50.0, zt=15, mx=5):
    g = {}
    if pr is not None:
        g["promo_rate_hist"] = pr
    if zt is not None:
        g["zt_250"] = zt
    if mx is not None:
        g["max_boards_hist"] = mx
    return g


# ── 全过 ────────────────────────────────────────────────────────────────────
def test_all_three_pass():
    q = P.quiz(_cand(), _gene(pr=40.0, zt=11, mx=5))
    assert q["passed"] == 3
    assert q["label"] == "三高"
    assert q["blocked_by"] == []


# ── ① 高胜率 ────────────────────────────────────────────────────────────────
def test_win_rate_below_threshold():
    q = P.quiz(_cand(), _gene(pr=33.3))
    assert q["items"]["win_rate"]["ok"] is False
    assert "win_rate" in q["blocked_by"]


def test_win_rate_exactly_at_threshold():
    """40% 正好达标（与 zt_gene.GENE_HABITUAL_MIN 同值，不是 >40）。"""
    q = P.quiz(_cand(), _gene(pr=40.0))
    assert q["items"]["win_rate"]["ok"] is True


def test_win_rate_high_but_sample_thin():
    """善水科技实证：晋级率 67%，但近一年只涨停 4 次 → 不算高胜率。
    ⛔ 4 次里蒙对 3 次 = 75%，这个数字撑不起「高胜率」。"""
    q = P.quiz(_cand(), _gene(pr=66.7, zt=4))
    assert q["items"]["win_rate"]["ok"] is False
    assert "样本不足" in q["items"]["win_rate"]["why"]


def test_win_rate_sample_exactly_at_min():
    q = P.quiz(_cand(), _gene(pr=50.0, zt=P.WIN_SAMPLE_MIN))
    assert q["items"]["win_rate"]["ok"] is True


# ── ② 可晋级 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("boards,ok", [(1, True), (2, True), (3, False), (5, False)])
def test_promote_board_window(boards, ok):
    """只做 1~2 板（用户纪律：第 1~2 板发现，4 板是另一笔交易）。"""
    q = P.quiz(_cand(boards=boards), _gene(mx=9))
    assert q["items"]["promote"]["ok"] is ok


def test_promote_needs_higher_hist():
    """历史最高板没超过当前板数 → 没证明它走过更高。"""
    q = P.quiz(_cand(boards=2), _gene(mx=2))
    assert q["items"]["promote"]["ok"] is False
    assert "没走过比现在更高" in q["items"]["promote"]["why"]


# ── ③ 板块地位 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("role", ["跟风", "补涨", "中军"])
def test_status_follower_rejected(role):
    q = P.quiz(_cand(role=role, theme=10), _gene())
    assert q["items"]["status"]["ok"] is False
    assert "不是前排" in q["items"]["status"]["why"]


def test_status_lone_stock_rejected():
    """津药药业实证：准龙头第 3、晋级 42%、历史最长 7 板，但题材只有自己 1 家
    → 孤票，没有板块效应，不叫「板块地位高」。"""
    q = P.quiz(_cand(role="准龙头", theme=1), _gene(pr=42.1, zt=20, mx=7))
    assert q["items"]["status"]["ok"] is False
    assert "孤票" in q["items"]["status"]["why"]
    assert q["passed"] == 2          # 其余两问都过，只差这一条


def test_status_theme_exactly_at_min():
    q = P.quiz(_cand(theme=P.STATUS_MIN_THEME), _gene())
    assert q["items"]["status"]["ok"] is True


def test_status_unknown_role_is_unknown():
    """认不出的身位 → None（不知道），⛔ 不按不通过算。"""
    q = P.quiz(_cand(role="看过"), _gene())
    assert q["items"]["status"]["ok"] is None


# ── 「不知道」≠「不行」 ─────────────────────────────────────────────────────
def test_missing_gene_is_unknown_not_fail():
    q = P.quiz(_cand(), {})
    assert q["items"]["win_rate"]["ok"] is None
    assert q["items"]["promote"]["ok"] is None
    assert "win_rate" not in q["blocked_by"]      # ⛔ 不算被否
    assert "win_rate" in q["unknown"]
    assert q["label"] == ""                        # 也不给「三高」称号


def test_missing_boards_is_unknown():
    c = _cand()
    c.pop("boards")
    q = P.quiz(c, _gene())
    assert q["items"]["promote"]["ok"] is None


# ── annotate 边界 ───────────────────────────────────────────────────────────
def test_annotate_does_not_reorder_or_rescore():
    rows = [_cand("600001", "甲", boards=1), _cand("600002", "乙", boards=2)]
    for r in rows:
        r["raw_score"] = 50.0
    before = [id(r) for r in rows]
    P.annotate(rows, {"600001": _gene(pr=10), "600002": _gene(pr=60)})
    assert [id(r) for r in rows] == before
    assert [r["name"] for r in rows] == ["甲", "乙"]
    assert [r["raw_score"] for r in rows] == [50.0, 50.0]


def test_annotate_never_emits_position_or_quota():
    rows = [_cand()]
    P.annotate(rows, {})
    q = rows[0]["promotion_quiz"]
    for bad in ("position", "quota", "pct", "shares"):
        assert bad not in q


def test_annotate_survives_bad_input():
    out = P.annotate([{"code": "600001", "boards": "abc"}], None)
    assert out[0]["promotion_quiz"]["schema"] == P.SCHEMA


# ── 汇总行 ──────────────────────────────────────────────────────────────────
def test_summary_when_three_pass():
    rows = [_cand("600001", "中新赛克")]
    P.annotate(rows, {"600001": _gene(pr=40, zt=11, mx=5)})
    assert "三问全过 1 只" in P.summary_line(rows)
    assert "中新赛克" in P.summary_line(rows)


def test_summary_when_none_pass():
    """一只全过都没有 → 直说没有，⛔ 不粉饰。"""
    rows = [_cand("600001", "甲", role="跟风")]
    P.annotate(rows, {"600001": _gene(pr=10)})
    line = P.summary_line(rows)
    assert "三问全过 0 只" in line


def test_summary_empty_without_quiz():
    assert P.summary_line([]) == ""
    assert P.summary_line([{"code": "600001"}]) == ""


# ── 与真实数据同型的两只票，判定必须能分开 ──────────────────────────────────
def test_real_world_pair_distinguished():
    """10-29 实证：中新赛克 3/3，津药药业 2/3（卡在孤票）。
    同样都是「准龙头 + 2 板 + 晋级率 ≥40%」，必须能分出高下。"""
    zx = P.quiz(_cand("002912", "中新赛克", theme=10), _gene(pr=40.0, zt=11, mx=5))
    jy = P.quiz(_cand("600488", "津药药业", theme=1), _gene(pr=42.1, zt=20, mx=7))
    assert zx["passed"] == 3
    assert jy["passed"] == 2
    assert jy["blocked_by"] == ["status"]
