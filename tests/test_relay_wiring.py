# -*- coding: utf-8 -*-
"""早盘接力·动态仓位「执行链路接线」回归锁（2026-10-13）。

relay_position 只出方案；方案必须真正作用到买入链路，本文件锁「边界」：
- clamp_position(p, cap)：单票上限取 min(50%, 动态 cap)，只收紧不放大。
- refine_effective_quota：名额向下裁断（can_buy=False → 0）。
- apply_total_cap：一组单票计划仓位等比缩放到 ≤ 总仓上限，不偏袒、不编造。
- 软线（5%~10%）只减半、不禁买 → 弱档名额 1 也不被四舍五入抹成 0。
"""
import pytest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rp = _load("relay_position", "wealth_code/core/relay_position.py")
ac = _load("ai_caliber", "wealth_code/core/ai_caliber.py")


# ── ① 单票上限（ai_caliber.clamp_position 动态 cap）────────────────────────────
def test_clamp_position_dynamic_cap_tightens_only():
    assert ac.clamp_position(0.8, 0.3) == 0.3        # 动态 cap 更小 → 收紧
    assert ac.clamp_position(0.2, 0.3) == 0.2        # 本就小 → 不动
    assert ac.clamp_position(0.8, 0.9) == 0.5        # cap 放得比 50% 还宽 → 仍守 50%
    assert ac.clamp_position(0.8) == 0.5             # 无 cap → 原 50% 口径
    assert ac.clamp_position(0.8, None) == 0.5
    assert ac.clamp_position(0.8, 0) == 0.5          # 非法 cap → 忽略
    assert ac.clamp_position(-0.1, 0.3) is None      # 非正 → 诚实留空
    assert ac.clamp_position("x", 0.3) is None


# ── ② 名额向下裁断 ────────────────────────────────────────────────────────────
def test_refine_quota_never_enlarges():
    strong = {"enabled": True, "can_buy": True, "quota": 3}
    assert rp.refine_effective_quota(1, strong) == 1     # 只取 min
    assert rp.refine_effective_quota(5, strong) == 3
    weak = {"enabled": True, "can_buy": True, "quota": 1}
    assert rp.refine_effective_quota(5, weak) == 1       # 转弱 → 收紧
    assert rp.refine_effective_quota(3, {"enabled": True, "can_buy": False}) == 0
    assert rp.refine_effective_quota(3, None) == 3


# ── ③ 总仓上限等比裁剪 ────────────────────────────────────────────────────────
def test_apply_total_cap_equal_scaling():
    plan = {"enabled": True, "can_buy": True, "per_pick_cap": 0.5, "total_cap": 0.6}
    out = rp.apply_total_cap([0.4, 0.4, 0.4], plan)      # 和 1.2 > 0.6
    assert abs(sum(out) - 0.6) < 1e-6                    # 等比缩到 0.6
    assert abs(out[0] - out[1]) < 1e-9 and abs(out[1] - out[2]) < 1e-9  # 不偏袒
    # 未超上限 → 原样（仅按 per_pick 夹紧）
    out2 = rp.apply_total_cap([0.2, 0.1], plan)
    assert out2 == [0.2, 0.1]


def test_apply_total_cap_clamps_per_pick_first():
    plan = {"enabled": True, "can_buy": True, "per_pick_cap": 0.3, "total_cap": 1.0}
    out = rp.apply_total_cap([0.9, 0.1], plan)           # 单票先各自夹到 0.3
    assert out[0] == 0.3 and out[1] == 0.1


def test_apply_total_cap_honest_degrade():
    # 方案未启用 / 缺失 → 原样返回（回退旧口径·⛔ 不得归零误伤）
    off = {"enabled": False, "can_buy": True, "total_cap": 0.6}
    assert rp.apply_total_cap([0.4, None], off) == [0.4, None]
    assert rp.apply_total_cap([0.4, 0.3], None) == [0.4, 0.3]
    # 方案启用但禁买 → 全部归 0（None 仍 None）
    blocked = {"enabled": True, "can_buy": False, "total_cap": 0.6}
    assert rp.apply_total_cap([0.4, 0.3], blocked) == [0.0, 0.0]
    assert rp.apply_total_cap([0.4, None], blocked) == [0.0, None]
    # 上限缺失/为 0 → 原样返回（不编造）
    assert rp.apply_total_cap([0.4, 0.3], {"enabled": True, "can_buy": True}) == [0.4, 0.3]
    assert rp.apply_total_cap([0.4, 0.3],
                              {"enabled": True, "can_buy": True, "total_cap": 0}) == [0.4, 0.3]


# ── ④ 回撤＝动态压仓 ≠ 禁买（2026-10-13 用户口径 B）────────────────────────────
def test_drawdown_always_buyable(monkeypatch):
    """回撤再深也不禁买：只压仓位（需显式开 `ENABLE_DRAWDOWN_SCALING`，11-05 起默认关）。"""
    monkeypatch.setattr(rp, "ENABLE_DRAWDOWN_SCALING", True)
    p_soft = rp.build_plan(strength=0.2, tier="weak", dd=6.0)      # 起降段
    assert p_soft["can_buy"] is True
    assert p_soft["quota"] == 1                                    # 保底 1，不被抹成 0
    p_deep = rp.build_plan(strength=0.2, tier="weak", dd=18.0)     # 原硬线区间
    assert p_deep["can_buy"] is True                               # ⛔ 不再禁买
    assert p_deep["quota"] >= 1
    assert 0 < p_deep["per_pick_cap"] < rp.PER_PICK_WEAK
    p_floor = rp.build_plan(strength=0.2, tier="weak", dd=99.0)    # 触底
    assert p_floor["can_buy"] is True and p_floor["per_pick_cap"] > 0


# ── ⑤ 规划口径（T-1）与执行口径（09:25）分离 ──────────────────────────────────
def test_tier_caps_are_dd_independent():
    """tier_caps 是规划口径：只由强弱档位决定，不含回撤裁断。"""
    for t in ("strong", "neutral", "weak"):
        q, per, tot = rp.tier_caps(t)
        assert q >= 1 and per > 0 and tot > 0
    assert rp.tier_caps("strong") == (rp.QUOTA_STRONG, rp.PER_PICK_STRONG, rp.TOTAL_STRONG)
    assert rp.tier_caps("weak") == (rp.QUOTA_WEAK, rp.PER_PICK_WEAK, rp.TOTAL_WEAK)
    assert rp.tier_caps("不存在") == rp.tier_caps("neutral")     # 未知档 → 中性兜底
