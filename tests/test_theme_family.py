"""玄学族归纳（`families` + `scripts/theme_family_trace.py`）的回归锁。

## 用户定调（2026-11-10）

> 「玄学可以归纳，但是不加权重，他只是特殊时期的产物」

拆成三条，本文件各锁一条：

| 原话 | 锁什么 |
|---|---|
| **可以归纳** | 按名字规则归族、算每日家数 / 最高板 / 题材分散度，能跑出时间序列 |
| **不加权重** | `weight` **读出来恒为 0**（文件写 5 也没用）；判据文件一个都不准 import 族归纳 |
| **特殊时期的产物** | 状态机会走到 `expired`；不能因为「归了纳」就当成长期规则 |

⛔ 特别锁一条：`weight` 不是「默认 0、可配置」，而是**强制 0**。
   允许配置就等于允许某天有人在 JSON 里写 0.3，而 0.3 的权重在 8 只票的族里
   足够挤掉一个真题材票 —— 这条不能靠人记性守。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from common import manual_theme as mt

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tr():
    """加载 `scripts/theme_family_trace.py`（scripts 不在包路径里）。"""
    spec = importlib.util.spec_from_file_location(
        "theme_family_trace", ROOT / "scripts" / "theme_family_trace.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _write(p: Path, payload) -> None:
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
                 encoding="utf-8")


def _fam(name_any, *, kind="naming", exclude=None, weight=0) -> dict:
    return {"kind": kind, "name_any": name_any, "exclude_codes": exclude or [], "weight": weight}


# ── ① 可以归纳：族定义能被读出来 ────────────────────────────────────────────
def test_family_is_readable(tmp_path):
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"])}})
    fams = mt.load_families(f, refresh=True)
    assert list(fams) == ["东方系"]
    assert fams["东方系"]["name_any"] == ["东方"]
    assert fams["东方系"]["kind"] == "naming"


def test_illegal_kind_falls_back_to_other(tmp_path):
    f = tmp_path / "mt.json"
    _write(f, {"families": {"X": _fam(["东方"], kind="乱写的类型")}})
    assert mt.load_families(f, refresh=True)["X"]["kind"] == "other"


# ── ② 不加权重：weight 强制 0 ───────────────────────────────────────────────
@pytest.mark.parametrize("written", [1, 5, 0.3, -1, "0.5", None, True])
def test_weight_is_forced_to_zero(tmp_path, written):
    """⛔ 文件里写多少都没用 —— 读出来必须是 0。

    允许配置权重，等于允许「哪天线被炒起来了，把它权重调高一点」。
    那一刻起，玄学就进了判据。
    """
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"], weight=written)}})
    assert mt.load_families(f, refresh=True)["东方系"]["weight"] == 0


def test_real_file_weights_are_all_zero():
    """生产文件的每一族 weight 都必须是 0（手改也不许）。"""
    real = ROOT / "cache" / "manual_theme.json"
    if not real.exists():
        pytest.skip("尚无标注文件")
    for name, fam in mt.load_families(real, refresh=True).items():
        assert fam["weight"] == 0, f"{name} 的 weight 不是 0 —— 玄学不进权重"


# ── ③ 名字匹配（含排除表）──────────────────────────────────────────────────
def test_match_by_name(tmp_path):
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"]), "羊年": _fam(["阳", "羊", "洋", "扬"], kind="zodiac")}})
    assert mt.match_families("东方新能", "002310", path=f) == ["东方系"]
    assert mt.match_families("澳洋健康", "002172", path=f) == ["羊年"]
    assert mt.match_families("平安银行", "000001", path=f) == []
    # 同时命中两族也要如实返回两个（调用方自己决定要不要都看）
    assert set(mt.match_families("东方海洋", "002086", path=f)) == {"东方系", "羊年"}


def test_exclude_codes_suppress_false_positive(tmp_path):
    """东方财富名字带东方，但不是族内炒作 —— 靠排除表挡掉，不靠语义猜。"""
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"], exclude=["300059"])}})
    assert mt.match_families("东方财富", "300059", path=f) == []
    assert mt.match_families("东方新能", "002310", path=f) == ["东方系"]


def test_empty_name_matches_nothing(tmp_path):
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"])}})
    assert mt.match_families("", "600278", path=f) == []


# ── ④ 坏数据不炸、不编造 ────────────────────────────────────────────────────
@pytest.mark.parametrize("payload", [
    {"families": ["列表不是字典"]},
    {"families": {"空规则": {"kind": "naming", "name_any": []}}},      # 没匹配规则
    {"families": {"字符串": "不是对象"}},
    {"families": {}},
    {},
])
def test_bad_family_payloads_yield_empty(tmp_path, payload):
    f = tmp_path / "mt.json"
    _write(f, payload)
    assert mt.load_families(f, refresh=True) == {}


def test_broken_json_does_not_raise(tmp_path):
    f = tmp_path / "mt.json"
    _write(f, "{ 坏 json")
    assert mt.load_families(f, refresh=True) == {}


def test_missing_file_returns_empty(tmp_path):
    assert mt.load_families(tmp_path / "nope.json", refresh=True) == {}


# ── ⑤ 特殊时期的产物：状态机会走到 expired ──────────────────────────────────
def _row(name: str, boards: int = 1, sector: str = "") -> dict:
    return {"code": f"C{name}", "name": name, "boards": boards, "sector": sector}


def test_lifecycle_emerging_active_weak_expired(tmp_path, tr):
    """一条族的完整一生：成族 → 走强 → 还剩零星 → 连续两天近乎归零 → 死。

    ⛔ 关键是最后一步必须**能死**。如果状态机永远 active，那「特殊时期的产物」
       就变成了「永久规则」，与用户定调相反。
    """
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"])}})
    days = [
        ("2026-01-01", [_row("东方A"), _row("东方B"), _row("东方C")]),                    # emerging
        ("2026-01-02", [_row("东方A"), _row("东方B"), _row("东方C"), _row("东方D")]),      # active
        ("2026-01-03", [_row("东方A"), _row("东方B")]),                                   # weak
        ("2026-01-04", [_row("东方A")]),                                                  # weak
        ("2026-01-05", [_row("东方A")]),                                                  # expired
        ("2026-01-06", [_row("东方A"), _row("东方B"), _row("东方C")]),                    # emerging（复活）
    ]
    states = [r["state"] for r in tr.trace("东方系", days, path=f)]
    assert states == ["emerging", "active", "weak", "weak", "expired", "emerging"]


def test_revival_is_emerging_not_active(tmp_path, tr):
    """死过一次再成族，算「重新起」，不算「还在」—— 这两件事的赔率完全不同。"""
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"])}})
    days = [
        ("2026-01-01", [_row("东方A")]),
        ("2026-01-02", [_row("东方A")]),
        ("2026-01-03", [_row("东方A"), _row("东方B"), _row("东方C")]),
    ]
    series = tr.trace("东方系", days, path=f)
    assert series[2]["state"] == "emerging"
    assert tr.summary(series)["first_seen"] == "2026-01-03", "first_seen 只算「成族」的日子"


def test_spread_counts_distinct_sectors(tmp_path, tr):
    """题材分散度 = 不同板块数 / 家数。这是「名字炒作 vs 题材带动」的客观分界。"""
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"])}})
    days = [("2026-01-01", [
        _row("东方A", sector="光伏"), _row("东方B", sector="大消费"),
        _row("东方C", sector="国产芯片"), _row("东方D", sector="光伏"),
    ])]
    row = tr.trace("东方系", days, path=f)[0]
    assert row["count"] == 4
    assert row["sector_n"] == 3
    assert row["spread"] == "3/4"


def test_max_boards_is_family_high(tmp_path, tr):
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"])}})
    days = [("2026-01-01", [_row("东方A", boards=2), _row("东方B", boards=5)])]
    assert tr.trace("东方系", days, path=f)[0]["max_boards"] == 5


def test_no_hit_day_is_counted_as_zero(tmp_path, tr):
    """空仓日要如实记 0（不是跳过）—— 寿命算的是连续天数，跳过一天寿命就偏长。"""
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"])}})
    days = [("2026-01-01", [_row("东方A")]), ("2026-01-02", [_row("平安银行")])]
    series = tr.trace("东方系", days, path=f)
    assert [r["count"] for r in series] == [1, 0]
    assert series[1]["members"] == []


def test_unknown_family_returns_empty(tmp_path, tr):
    f = tmp_path / "mt.json"
    _write(f, {"families": {"东方系": _fam(["东方"])}})
    assert tr.trace("不存在的族", [("2026-01-01", [_row("东方A")])], path=f) == []


# ── ⑥ 铁律：族归纳一个判据都不准读 ──────────────────────────────────────────
def test_never_used_by_decision_paths():
    """⛔ 「不加权重」的最终防线：准入判据里连这个模块都不许出现。

    查的是 `manual_theme` / `match_families` / `load_families` 三个名字任一出场。
    哪天有人图方便写 `if "东方" in name: 加权`，这条会直接红。
    """
    forbidden = [
        ROOT / "wealth_code" / "core" / "pool_call.py",
        ROOT / "wealth_code" / "core" / "sector_leader.py",
        ROOT / "wealth_code" / "core" / "relay_position.py",
        ROOT / "wealth_code" / "core" / "ai_caliber.py",
        ROOT / "wealth_code" / "core" / "daily_screen.py",
        ROOT / "wealth_code" / "core" / "relay_forecast.py",
    ]
    needles = ("manual_theme", "match_families", "load_families")
    offenders = []
    for p in forbidden:
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        hit = [n for n in needles if n in txt]
        if hit:
            offenders.append(f"{p.name}:{hit}")
    assert not offenders, (
        "准入判据里出现了玄学族归纳 —— 用户定调「可以归纳，但是不加权重」，"
        f"归了纳就必须停在观察层：{offenders}"
    )


def test_real_families_are_well_formed():
    """生产文件的族必须结构合法（防止手改写坏导致第二天 trace 静默归零）。"""
    real = ROOT / "cache" / "manual_theme.json"
    if not real.exists():
        pytest.skip("尚无标注文件")
    obj = json.loads(real.read_text(encoding="utf-8"))
    fams = obj.get("families") or {}
    assert isinstance(fams, dict)
    for name, item in fams.items():
        assert isinstance(item, dict), f"{name} 不是对象"
        assert item.get("name_any"), f"{name} 缺 name_any"
        assert all(str(k).strip() for k in item["name_any"]), f"{name} 有明显空的匹配词"
        assert item.get("kind") in mt.FAMILY_KINDS, f"{name} 的 kind 非法：{item.get('kind')}"
