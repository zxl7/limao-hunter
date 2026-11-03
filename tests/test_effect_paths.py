# -*- coding: utf-8 -*-
"""赚钱/亏钱路径归因（scripts/effect_paths.py）回归锁。

锁三件事：
1. 九条路径各自能被真实字段正确命中（含 §3.1 盲区「高度衰减」）
2. **互斥且穷尽**：每票只落一桶，各桶家数之和 == 输入票数
3. 题材家数口径：top5 优先 / 涨停池兜底 / 泛词不判 solo —— 防再次误判「独走」
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "effect_paths",
    Path(__file__).resolve().parent.parent / "scripts" / "effect_paths.py",
)
ep = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ep)


# ── 构造工具 ──────────────────────────────────────────────────────────────
def _fb(quotes: dict, **kw) -> dict:
    base = {"date": "2026-10-19", "prev_date": "2026-10-16", "prev_zt_count": len(quotes), "quotes": quotes}
    base.update(kw)
    return base


def _q(name: str, pct: float | None, boards: int, sealed: bool = False) -> dict:
    return {"name": name, "pct": pct, "prev_boards": boards, "sealed": sealed}


def _latest(zt: list | None = None, top5: list | None = None, limit_down: list | None = None, ts: str = "2026-10-19T13:30:00+08:00") -> dict:
    return {
        "ts": ts,
        "limit_up_stocks": zt or [],
        "top5": top5 or [],
        "limit_down": {"count": len(limit_down or []), "names": limit_down or []},
    }


def _theme(name: str, zt_count: int, zt_prev: int | None, leader: str = "") -> dict:
    return {
        "name": name,
        "zt_count": zt_count,
        "zt_prev": zt_prev,
        "leader": {"name": leader} if leader else {},
    }


def _find(result: dict, kind: str) -> dict:
    for b in result["buckets"]:
        if b["kind"] == kind:
            return b
    return {}


def _path_of(result: dict, name: str) -> str:
    for b in result["buckets"]:
        for s in b["stocks"]:
            if s["name"] == name:
                return b["kind"]
    raise AssertionError(f"{name} 未出现在任何桶")


# ── 1. 赚钱侧五条路径 ─────────────────────────────────────────────────────
def test_ditian_reversal_classified():
    """当日曾跌停又拉上涨停 → 地天反核（最强路径）。"""
    fb = _fb({"600001": _q("反核甲", 10.0, 1, sealed=True)})
    lt = _latest(limit_down=["反核甲"])
    r = ep.build(fb, lt)
    assert _path_of(r, "反核甲") == "ditian"
    assert _find(r, "ditian")["side"] == "profit"


def test_front_leader_requires_high_boards_and_sealed():
    """昨 ≥3 板仍封住 → 前排龙头；同样 3 板但没封住 → 不算。"""
    fb = _fb({
        "600001": _q("高位封住", 10.0, 4, sealed=True),
        "600002": _q("高位炸了", -2.0, 4, sealed=False),
    })
    r = ep.build(fb, _latest())
    assert _path_of(r, "高位封住") == "front_leader"
    assert _path_of(r, "高位炸了") == "high_fade"


def test_new_theme_dragon_requires_leader():
    """新题材（昨日≤1家）且是 top5 leader → 板块龙；同题材非龙一 → 人气接力。"""
    fb = _fb({
        "600001": _q("龙一甲", 10.0, 1, sealed=True),
        "600002": _q("跟风甲", 10.0, 1, sealed=True),
    })
    lt = _latest(top5=[_theme("新题材", zt_count=2, zt_prev=1, leader="龙一甲")])
    # 两只票的题材来自今日涨停池 sector
    lt["limit_up_stocks"] = [
        {"code": "600001.SS", "name": "龙一甲", "pct": 10.0, "sector": "新题材", "boards": "2天2板"},
        {"code": "600002.SS", "name": "跟风甲", "pct": 10.0, "sector": "新题材", "boards": "2天2板"},
    ]
    r = ep.build(fb, lt)
    assert _path_of(r, "龙一甲") == "new_theme_dragon"
    assert _path_of(r, "跟风甲") == "non_core_relay"


def test_solo_when_old_theme_has_single_sealed():
    """老题材（昨日≥2家）今日只剩它封住 → 独走辨识度。"""
    fb = _fb({"600001": _q("独走甲", 10.0, 1, sealed=True)})
    lt = _latest(top5=[_theme("老题材", zt_count=1, zt_prev=5)])
    lt["limit_up_stocks"] = [
        {"code": "600001.SS", "name": "独走甲", "pct": 10.0, "sector": "老题材", "boards": "2天2板"},
    ]
    r = ep.build(fb, lt)
    assert _path_of(r, "独走甲") == "solo"


def test_generic_theme_never_solo():
    """「其他/综合」等泛词不判独走（题材归并铁律·禁泛词）。"""
    fb = _fb({"600001": _q("泛词甲", 10.0, 1, sealed=True)})
    lt = _latest()
    lt["limit_up_stocks"] = [
        {"code": "600001.SS", "name": "泛词甲", "pct": 10.0, "sector": "其他", "boards": "2天2板"},
    ]
    r = ep.build(fb, lt)
    assert _path_of(r, "泛词甲") == "non_core_relay"


def test_theme_count_falls_back_to_limit_up_pool():
    """题材不在 top5 时，用今日涨停池实分组家数兜底 —— ⛔ 不得当成 0 家而误判独走。"""
    fb = _fb({
        "600001": _q("甲", 10.0, 1, sealed=True),
        "600002": _q("乙", 10.0, 1, sealed=True),
    })
    lt = _latest()  # top5 空
    lt["limit_up_stocks"] = [
        {"code": "600001.SS", "name": "甲", "pct": 10.0, "sector": "板外题材", "boards": "2天2板"},
        {"code": "600002.SS", "name": "乙", "pct": 10.0, "sector": "板外题材", "boards": "2天2板"},
    ]
    r = ep.build(fb, lt)
    assert _path_of(r, "甲") == "non_core_relay", "题材内 2 家封板 → 不能判独走"
    assert _path_of(r, "乙") == "non_core_relay"


# ── 2. 亏钱侧四条路径 ─────────────────────────────────────────────────────
def test_high_nuke_vs_high_fade_is_the_blind_spot():
    """高位 −5% 以下=被核；高位但只是温和下杀=高度衰减（§3.1 阈值抓不到的盲区）。"""
    fb = _fb({
        "600001": _q("被核甲", -9.0, 4),
        "600002": _q("衰减甲", 1.5, 11),
        "600003": _q("衰减乙", -1.6, 4),
    })
    r = ep.build(fb, _latest())
    assert _path_of(r, "被核甲") == "high_nuke"
    assert _path_of(r, "衰减甲") == "high_fade"
    assert _path_of(r, "衰减乙") == "high_fade"


def test_low_fail_and_mojito():
    """低位 ≤−5% = 失败出货；低位小跌且题材无跟随 = 杂毛无催化。"""
    fb = _fb({
        "600001": _q("崩了甲", -7.0, 1),
        "600002": _q("阴跌甲", -1.2, 1),
    })
    r = ep.build(fb, _latest())
    assert _path_of(r, "崩了甲") == "low_fail"
    assert _path_of(r, "阴跌甲") == "mojito"


def test_strong_unsealed_is_profit_weak_not_profit():
    """涨 ≥3% 但没封住 → 强但未封，归 profit_weak（钱还在但没锁）。"""
    fb = _fb({"600001": _q("强未封", 6.8, 1)})
    r = ep.build(fb, _latest())
    assert _path_of(r, "强未封") == "strong_unsealed"
    assert _find(r, "strong_unsealed")["side"] == "profit_weak"
    assert r["summary"]["profit_count"] == 0
    assert r["summary"]["profit_weak_count"] == 1


# ── 3. 互斥 / 穷尽 / 健壮性 ───────────────────────────────────────────────
def test_mutually_exclusive_and_exhaustive():
    """每票只落一桶，各桶家数之和 == 输入票数（不重不漏）。"""
    fb = _fb({
        "600001": _q("A", 10.0, 4, sealed=True),
        "600002": _q("B", -8.0, 3),
        "600003": _q("C", 0.5, 1),
        "600004": _q("D", -2.0, 1),
        "600005": _q("E", 5.0, 2),
        "600006": _q("F", -6.0, 1),
        "600007": _q("G", 1.0, 2),
    })
    r = ep.build(fb, _latest())
    total = sum(b["count"] for b in r["buckets"])
    assert total == len(fb["quotes"]) == 7
    seen = [s["name"] for b in r["buckets"] for s in b["stocks"]]
    assert len(seen) == len(set(seen)), "同一票不得出现在多个桶"


def test_missing_pct_goes_neutral_not_guessed():
    """涨幅缺失 → 中性 + 记 data_gaps，⛔ 不臆造涨跌。"""
    fb = _fb({"600001": _q("无价甲", None, 2)})
    r = ep.build(fb, _latest())
    assert _path_of(r, "无价甲") == "neutral"
    assert any("无价甲" in g for g in r["data_gaps"])


def test_today_new_limit_up_included():
    """今日新涨停（不在昨池）必须入池，且板数按「今日板数−1」还原昨日板数。"""
    fb = _fb({})  # 昨池空
    lt = _latest(zt=[{"code": "600009.SS", "name": "新首板", "pct": 10.0, "sector": "低空经济", "boards": "1天1板"}])
    r = ep.build(fb, lt)
    assert r["total"] == 1
    assert r["coverage"]["today_new"] == 1
    assert _path_of(r, "新首板") in ("non_core_relay", "new_theme_dragon", "solo")


def test_dominant_path_is_max_count_not_first():
    """主导路径必须按家数取最大，⛔ 不能按定义顺序取第一条。"""
    fb = _fb({
        "600001": _q("崩1", -8.0, 1),
        "600002": _q("崩2", -7.0, 1),
        "600003": _q("崩3", -6.0, 1),
        "600004": _q("核1", -9.0, 4),
    })
    r = ep.build(fb, _latest())
    assert r["summary"]["dominant_loss_path"] == "低位失败出货"
    assert r["summary"]["dominant_loss_count"] == 3


def test_empty_input_no_crash():
    """空输入不崩，返回零值结构（守护链安全）。"""
    r = ep.build({}, {}, {})
    assert r["total"] == 0
    assert r["buckets"] == []
    assert r["summary"]["net_bias"] == 0
    assert r["blind_spots"], "盲区说明必须常驻"


def test_switch_off_returns_zero(monkeypatch):
    monkeypatch.setattr(ep, "ENABLE_EFFECT_PATHS", False)
    assert ep.main() == 0


# ── 4. 纯函数工具 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,want", [
    ("603197.SS", "603197"),
    ("002085.SZ", "002085"),
    ("000573", "000573"),
    (None, ""),
])
def test_normalize_code(raw, want):
    assert ep.normalize_code(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("2天2板", 2),
    ("3板", 3),
    ("首板", None),
    (4, 4),
    (None, None),
])
def test_parse_boards(raw, want):
    assert ep.parse_boards(raw) == want
