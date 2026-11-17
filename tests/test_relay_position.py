# -*- coding: utf-8 -*-
"""早盘接力·动态仓位管理 回归锁（2026-10-13）。

锁定：
- 强弱分合成（昨日六维 / 今晨竞价）的取值区间与诚实降级。
- 三档 → 名额 / 单票 / 总仓 映射。
- 回撤软/硬线降档（衡量＝净值峰值→当前）。
- 题材沉淀置信 + 候选 known 占比逻辑。
- build_plan_from_files 编排 + refine_effective_quota 向下裁断（不越权放大）。
"""
import pytest
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "relay_position", ROOT / "wealth_code" / "core" / "relay_position.py")
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)


# ── ① 昨日强势 ──────────────────────────────────────────────────────────────
def test_strength_yesterday_full():
    fb = {"sentiment_score": 10.0, "promote_rate": 30.0, "nuke_count": 0,
          "broken_rate": 0.0, "avg_pct": 4.0, "up_rate": 70.0}
    s, parts = rp.strength_yesterday(fb)
    assert 0.0 <= s <= 1.0
    # 全满分输入应接近 1
    assert s > 0.95
    assert parts["score"] == round(s, 4)


def test_strength_yesterday_missing_degrades_neutral():
    s, parts = rp.strength_yesterday(None)
    assert s == 0.5                       # 全缺 → 中性
    assert parts["sentiment_missing"] is True
    assert parts["promote_missing"] is True


def test_strength_yesterday_weak():
    fb = {"sentiment_score": 2.0, "promote_rate": 5.0, "nuke_count": 10,
          "broken_rate": 40.0, "avg_pct": -2.0, "up_rate": 30.0}
    s, _ = rp.strength_yesterday(fb)
    assert s < 0.2                         # 全弱


# ── ② 今晨竞价 ──────────────────────────────────────────────────────────────
def test_strength_morning_full():
    mo = {"ready": True, "red_open_ratio": 0.8, "leader_red_open_ratio": 0.9,
          "leader_volume_pass_ratio": 0.85}
    cands = {"relay": [{"code": "600693", "theme_name": "X"},
                       {"code": "002343", "theme_name": "X"}]}
    quotes = {"600693": {"auction_price": 12.3, "prev_close": 11.9},   # +3.4% 红开未超线
              "002343": {"auction_price": 6.9, "prev_close": 6.69}}    # +3.1%
    s, parts = rp.strength_morning(mo, cands, quotes)
    assert 0.0 <= s <= 1.0
    assert parts["cand_red_ok"] == 2
    assert parts["cand_over"] == 0


def test_strength_morning_over_gap_penalized():
    mo = {"red_open_ratio": 0.5, "leader_red_open_ratio": 0.5, "leader_volume_pass_ratio": 0.5}
    cands = {"relay": [{"code": "600693", "theme_name": "X"}, {"code": "002343", "theme_name": "X"}]}
    quotes = {"600693": {"auction_price": 12.0, "prev_close": 10.0},   # +20% 超线→透支
              "002343": {"auction_price": 10.5, "prev_close": 10.0}}   # +5% 临界
    s, parts = rp.strength_morning(mo, cands, quotes)
    assert parts["cand_over"] == 1
    assert s < 0.6                         # 透支票拉低竞价分


def test_strength_morning_missing_neutral():
    s, parts = rp.strength_morning(None, None, None)
    assert s == 0.5
    assert parts["cand_missing"] is True


# ── ③ 三档与回撤降档 ─────────────────────────────────────────────────────────
def test_tier_of_boundaries():
    assert rp.tier_of(0.80) == "strong"
    assert rp.tier_of(0.75) == "strong"
    assert rp.tier_of(0.74) == "neutral"
    assert rp.tier_of(0.45) == "neutral"
    assert rp.tier_of(0.44) == "weak"
    assert rp.tier_of(0.0) == "weak"


def test_drawdown_multiplier_bands(monkeypatch):
    """2026-10-13 口径 B：回撤**连续压仓·不禁买**（乘数恒 >0）。

    ⛔ 2026-11-05 用户「回撤不需要压仓」→ `ENABLE_DRAWDOWN_SCALING` 默认 0。
       本用例测的是**开关打开后**的曲线，所以显式开启；默认关闭的行为见下一个用例。
    """
    monkeypatch.setattr(rp, "ENABLE_DRAWDOWN_SCALING", True)
    assert rp.drawdown_multiplier(None) == (1.0, None)
    assert rp.drawdown_multiplier(3.0) == (1.0, "normal")          # 未到起降线
    # 5%~10% 线性下降到 0.5
    assert rp.drawdown_multiplier(7.5) == (0.75, "soft")
    assert rp.drawdown_multiplier(10.0) == (0.5, "heavy")          # 警戒线 = 半仓
    # ≥10% 继续压，但 ⛔ 不归零（最低 0.15）
    _m18, _lv18 = rp.drawdown_multiplier(18.0)
    assert _lv18 == "heavy" and 0.15 < _m18 < 0.5
    assert rp.drawdown_multiplier(25.0) == (rp.DRAWDOWN_MIN_MULT, "heavy")
    assert rp.drawdown_multiplier(40.0)[0] == rp.DRAWDOWN_MIN_MULT  # 触底不归零
    assert all(rp.drawdown_multiplier(d)[0] > 0 for d in (0, 5, 10, 18, 30, 99))


def test_build_plan_tiers():
    strong = rp.build_plan(strength=0.9, tier="strong")
    assert strong["quota"] == 3 and strong["per_pick_cap"] == 0.5 and strong["total_cap"] == 1.0
    neutral = rp.build_plan(strength=0.6, tier="neutral")
    assert neutral["quota"] == 2 and neutral["per_pick_cap"] == 0.4 and neutral["total_cap"] == 0.6
    weak = rp.build_plan(strength=0.3, tier="weak")
    assert weak["quota"] == 1 and weak["per_pick_cap"] == 0.3 and weak["total_cap"] == 0.3


def test_build_plan_deep_drawdown_shrinks_not_blocks(monkeypatch):
    """2026-10-13 用户口径 B：深回撤（原硬线）**不禁止买**，只把仓位压小（需开开关）。"""
    monkeypatch.setattr(rp, "ENABLE_DRAWDOWN_SCALING", True)
    p = rp.build_plan(strength=0.9, tier="strong", dd=18.0)
    assert p["can_buy"] is True                    # ⛔ 不再禁买
    assert p["quota"] >= 1                         # 名额保底 1
    assert 0 < p["per_pick_cap"] < rp.PER_PICK_STRONG   # 单票被压小
    assert p["total_cap"] < rp.TOTAL_STRONG
    assert any("动态控制" in h for h in p["hints"])


def test_build_plan_drawdown_soft_compresses(monkeypatch):
    monkeypatch.setattr(rp, "ENABLE_DRAWDOWN_SCALING", True)
    p = rp.build_plan(strength=0.6, tier="neutral", dd=10.0)
    assert p["can_buy"] is True
    assert p["quota"] == 1                        # 2 × 0.5 → 1
    assert p["per_pick_cap"] == 0.2               # 0.4 × 0.5
    assert p["total_cap"] == 0.3                  # 0.6 × 0.5
    assert any("动态控制" in h for h in p["hints"])


# ── ④ 题材沉淀置信 + known 标记 ───────────────────────────────────────────────
def test_theme_confidence():
    assert rp.theme_accumulation_confidence({"day_count": 60, "theme_count": 100})[0] == 1.0
    assert rp.theme_accumulation_confidence({"day_count": 0})[0] == 0.0
    c, info = rp.theme_accumulation_confidence({"day_count": 30})
    assert info["day_count"] == 30


def test_known_ratio_marks_themes():
    theme_index = {"themes": {"PCB": {"days": 10}, "未知线": {"days": 1}}}
    ratio, marked = rp._candidate_theme_known_ratio(
        {"relay": [{"code": "1", "theme_name": "PCB"},
                   {"code": "2", "theme_name": "PCB"},
                   {"code": "3", "theme_name": "未知线"}]}, theme_index)
    assert round(ratio, 3) == 0.667
    assert marked[0]["known_theme"] is True and marked[0]["theme_tracked_days"] == 10
    assert marked[2]["known_theme"] is False


# ── ⑤ 编排 + 向下裁断 ────────────────────────────────────────────────────────
def test_build_plan_from_files_smoke(tmp_path, monkeypatch):
    # 隔离文件依赖：用 tmp 目录伪造 feedback / nav / theme_index
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "feedback_matrix.json").write_text(json.dumps(
        {"sentiment_score": 6.0, "promote_rate": 28.0, "nuke_count": 2,
         "broken_rate": 15.0, "avg_pct": 2.0, "up_rate": 60.0}), encoding="utf-8")
    (cache / "account_nav_history.jsonl").write_text(
        "\n".join([json.dumps({"nav": 1.05}), json.dumps({"nav": 1.04})]), encoding="utf-8")
    th = tmp_path / "cache" / "top5_history"
    th.mkdir()
    (th / "theme_index.json").write_text(json.dumps(
        {"day_count": 60, "theme_count": 100, "themes": {"PCB": {"days": 10}}}), encoding="utf-8")
    # 把 ROOT/HUNTER_ROOT 指向 tmp_path
    monkeypatch.setattr(rp, "ROOT", tmp_path / "wealth_code" if (tmp_path / "wealth_code").exists() else tmp_path)
    monkeypatch.setattr(rp, "HUNTER_ROOT", tmp_path)
    # nav_drawdown 硬读 ROOT/cache —— 用 tmp 的 cache
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    p = rp.build_plan_from_files(
        tmp_path,
        market_open={"ready": True, "red_open_ratio": 0.7, "leader_red_open_ratio": 0.8,
                     "leader_volume_pass_ratio": 0.8},
        candidates={"relay": [{"code": "1", "theme_name": "PCB"}]},
        quotes={"1": {"auction_price": 10.3, "prev_close": 10.0}})
    assert 0.0 <= p["strength"] <= 1.0
    assert p["tier"] in ("strong", "neutral", "weak")
    assert isinstance(p["quota"], int) and p["quota"] >= 0
    assert p["can_buy"] is True
    assert p["drawdown_pct"] is not None and p["drawdown_pct"] < 1.0   # 1.04/1.05 回撤极小


def test_refine_effective_quota_down_only():
    full = {"enabled": True, "can_buy": True, "quota": 2}
    assert rp.refine_effective_quota(5, full) == 2          # 取 min，不放大
    assert rp.refine_effective_quota(1, full) == 1
    blocked = {"enabled": True, "can_buy": False, "quota": 0}
    assert rp.refine_effective_quota(3, blocked) == 0
    assert rp.refine_effective_quota(3, None) == 3          # 无方案→不动
    disabled = {"enabled": False, "can_buy": True, "quota": 0}
    assert rp.refine_effective_quota(3, disabled) == 3      # 开关关→不动


def test_drawdown_scaling_default_off():
    """⛔ 2026-11-05 用户「回撤不需要压仓」：默认不参与任何仓位计算（读数只作展示）。"""
    assert rp.ENABLE_DRAWDOWN_SCALING is False
    assert rp.drawdown_multiplier(18.0) == (1.0, None)
    plan = rp.build_plan(strength=0.9, tier="strong", dd=18.0)
    assert plan["per_pick_cap"] == rp.PER_PICK_STRONG
    assert plan["total_cap"] == rp.TOTAL_STRONG
