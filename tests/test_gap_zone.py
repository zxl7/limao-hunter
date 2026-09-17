#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价三档判据（方案 A + A2 修订 · 2026-09-17）回归锁。

口径演进：
  · 方案 A：卡面三档下沉为账本执行条件（卡面＝账本，判据唯一源）。
  · **A2 修订（2026-09-17）**：「高开太多」判据从「超**预期区间上沿**」改为
    **绝对线 gap > 5%（用户硬线）**——原判据受 `max_range_width=2.0` 钳制，
    「超预期」门槛实际落在 2~4%，把 3.6% 这类 ≤5% 合理高开误杀（实证误杀率 14%）。
    区间只保留**下沿**用于「低于预期 → 放弃」。

核心断言：判据唯一源 + 卡面/账本一致。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE / "wealth_code" / "pipelines"))

from core import ai_caliber  # noqa: E402
from build_cards import _ai_dyn_verdict  # noqa: E402


# ── 1) above ＝ 绝对 5% 线（A2 核心）───────────────────────────────
def test_above_is_absolute_5pct():
    assert ai_caliber.gap_zone(7.33, [1.4, 3.4]) == "above"
    assert ai_caliber.gap_zone(5.5, [0.0, 2.0]) == "above"
    assert ai_caliber.gap_zone(5.0, [0.0, 2.0]) == "ok"       # 边界：恰好 5% 仍在线上
    # ⛔ A2 核心回归点：≤5% 的合理高开不再因「超区间上沿」被误杀
    assert ai_caliber.gap_zone(3.81, [0.0, 2.0]) == "ok"      # 北自科技实证
    assert ai_caliber.gap_zone(3.59, [0.9, 2.9]) == "ok"      # 万向德农实证
    assert ai_caliber.gap_zone(4.2, [1.0, 3.0]) == "ok"


# ── 2) below ＝ 预期区间下沿（含容差）──────────────────────────────
def test_below_uses_lower_bound():
    tol = ai_caliber.EXPECTED_ZONE_TOL
    assert tol == 0.5
    assert ai_caliber.gap_zone(-9.96, [0.0, 2.0]) == "below"
    assert ai_caliber.gap_zone(0.4, [1.0, 3.0]) == "below"
    assert ai_caliber.gap_zone(0.6, [1.0, 3.0]) == "ok"       # 容差内仍算符合


# ── 3) unknown 仅当无竞价数据（无区间不再 unknown）─────────────────
def test_unknown_only_when_no_gap():
    assert ai_caliber.gap_zone(None, [1.0, 3.0]) == "unknown"
    assert ai_caliber.gap_zone("x", [1.0, 3.0]) == "unknown"
    # 无区间仍可判（above 用绝对线·其余 ok）——向后兼容旧候选
    assert ai_caliber.gap_zone(2.0, None) == "ok"
    assert ai_caliber.gap_zone(6.0, None) == "above"


# ── 4) 账本裁决 ──────────────────────────────────────────────────
def test_decide_zone_gates():
    led = {"view": "participate", "entry_max_gap": 5.0}
    # A2：≤5% 不再因"超区间"被拦
    can, _, meta = ai_caliber.decide(led, 3.81, expected_range=[0.0, 2.0])
    assert can is True and meta["zone"] == "ok"
    # 高开太多（>5%）
    can, note, meta = ai_caliber.decide(led, 7.33, expected_range=[1.4, 3.4])
    assert can is False and meta["zone"] == "above" and "超过 5%" in note
    # 不及预期（低于下沿）
    can, note, meta = ai_caliber.decide(led, -9.96, expected_range=[0.0, 2.0])
    assert can is False and meta["zone"] == "below" and "低于预期区间" in note


def test_decide_without_range_falls_back():
    """无预期区间 → 只按 5% 硬线（above 走绝对线·不再返回 unknown）。"""
    led = {"view": "participate", "entry_max_gap": 2.0}
    can, _, meta = ai_caliber.decide(led, 4.54, expected_range=None)
    assert can is True and meta["zone"] == "ok"               # AI 自定 2% 不拦截
    can2, _, meta2 = ai_caliber.decide(led, 6.5, expected_range=None)
    assert can2 is False and meta2["zone"] == "above"


def test_volume_gate():
    """量能门（2026-09-17 用户「带量高开＝转强」）：红开但量能 < 目标×0.7 → 降级观察。

    ⛔ 只拦「红开无量」；绿开/平开不受此门；无量能数据 → 不判（宽容·不误伤）。
    """
    led = {"view": "participate", "entry_max_gap": 5.0}
    # 红开 + 量能充足 → 买
    assert ai_caliber.decide(led, 2.0, expected_range=[1.0, 3.0],
                             auction_amount_yi=5.0, need_yi=5.0)[0] is True
    # 红开 + 无量（<0.7×目标）→ 观察
    can, note, meta = ai_caliber.decide(led, 2.0, expected_range=[1.0, 3.0],
                                        auction_amount_yi=1.0, need_yi=5.0)
    assert can is False and meta.get("volume_weak") is True and "无量的高开" in note
    # 无量能数据 → 不判（宽容）
    assert ai_caliber.decide(led, 2.0, expected_range=[1.0, 3.0])[0] is True
    # 低开 + 无量 → 由 zone 判（below），非量能门
    can2, note2, meta2 = ai_caliber.decide(led, -1.0, expected_range=[1.0, 3.0],
                                           auction_amount_yi=0.1, need_yi=5.0)
    assert can2 is False and meta2.get("zone") == "below" and "volume_weak" not in meta2


def test_decide_view_and_data_gates_unchanged():
    led = {"view": "watch", "entry_max_gap": 5.0}
    assert ai_caliber.decide(led, 2.0, expected_range=[1.0, 3.0])[0] is False
    assert ai_caliber.decide(None, 2.0)[0] is False
    assert ai_caliber.decide({"view": "participate"}, None)[0] is False


# ── 5) ⛔ 卡面与账本同向 ──────────────────────────────────────────
def test_card_and_ledger_agree():
    cases = [
        # (竞价, 预期区间, 账本应买?, 卡面关键字)
        (7.33, [1.4, 3.4], False, "继续观察"),   # 西陇科学（高开太多 >5%）
        (3.81, [0.0, 2.0], True, "符合预期"),    # A2：≤5% 可买（不再误杀）
        (2.00, [1.0, 3.0], True, "符合预期"),
        (-9.96, [0.0, 2.0], False, "放弃"),      # 闽东电力（大幅低开）
    ]
    for gap, rng, expect_buy, kw in cases:
        can, note, meta = ai_caliber.decide(
            {"view": "participate", "entry_max_gap": 5.0}, gap, expected_range=rng)
        it = {"code": "x", "expectation": {"expected_range": rng},
              "performance": {"open_check": {"gap_pct": gap}}}
        dyn = _ai_dyn_verdict(it)
        assert can is expect_buy, (gap, rng, can, note)
        assert dyn is not None and kw in dyn[0], (gap, rng, dyn)
