#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材基座（common/theme_base.py）回归。

2026-11-03 用户「以选股宝的题材分类为核心基座，东财只调一点资金看一看」。
本文件守四件事：
  ① 属性标签不得冒充题材（业绩增长/ST股 不是板块，聚合了就是假板块）
  ② 族归并只做「心智对齐」，不替代基座家数
  ③ 完整性守卫五个状态各自可复现（missing/empty/stale/ok/not_ok）
  ④ 守卫在「不知道」时不出假结论（不假装 100% 覆盖）
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import theme_base as TB


# ── ① 属性标签过滤 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("plate,expected", [
    ("短剧/互动影游", "短剧/互动影游"),
    ("业绩增长", None),
    ("ST股", None),
    ("其他", None),
    ("资产重组", None),
    ("高送转", None),
])
def test_theme_of_plates_basic(plate, expected):
    assert TB.theme_of_plates([plate]) == expected


def test_theme_of_plates_skips_attr_to_next():
    """首项是属性标签 → 取下一个真题材（08-11 艾华集团实证：['业绩增长','被动元件']）。"""
    assert TB.theme_of_plates(["业绩增长", "被动元件"]) == "被动元件"


def test_theme_of_plates_all_attr_is_none():
    """⛔ 全是属性标签 → None，**不许**退化成「其他」凑一个板块。"""
    assert TB.theme_of_plates(["业绩增长", "ST股"]) is None


@pytest.mark.parametrize("plates", [None, [], [""], [None, "  "]])
def test_theme_of_plates_empty_inputs(plates):
    assert TB.theme_of_plates(plates) is None


def test_is_attr_plate():
    assert TB.is_attr_plate("业绩增长") is True
    assert TB.is_attr_plate("短剧/互动影游") is False
    assert TB.is_attr_plate(None) is False


# ── ② 族归并 ────────────────────────────────────────────────────────────────
def test_family_media_group():
    """11-03 传媒实证：短剧/互动影游、传媒、游戏 是选股宝三个独立板块，用户看成一个。"""
    for t in ("短剧/互动影游", "传媒", "游戏", "出版"):
        assert TB.family_of(t) == "传媒族"


def test_family_unknown_returns_none():
    assert TB.family_of("谁也没归过的题材") is None
    assert TB.family_of("") is None


def test_family_switch_off(monkeypatch):
    monkeypatch.setattr(TB, "ENABLE_THEME_FAMILY", False)
    assert TB.family_of("短剧/互动影游") is None


def test_display_name():
    assert TB.display_name("短剧/互动影游") == "短剧/互动影游"
    assert TB.display_name("短剧/互动影游", prefer_family=True) == "传媒族"
    assert TB.display_name("") == "—"
    # 没有族的票，prefer_family 也照原样显示（⛔ 不编一个族出来）
    assert TB.display_name("谁也没归过的题材", prefer_family=True) == "谁也没归过的题材"


def test_themes_of_family_roundtrip():
    members = TB.themes_of_family("传媒族")
    assert "短剧/互动影游" in members
    for m in members:
        assert TB.family_of(m) == "传媒族"


# ── ③ 家数聚合 ──────────────────────────────────────────────────────────────
def test_count_by_theme():
    rows = [{"theme": "短剧/互动影游"}, {"theme": "短剧/互动影游"}, {"theme": "游戏"}]
    assert TB.count_by_theme(rows) == {"短剧/互动影游": 2, "游戏": 1}


def test_count_by_theme_unclassified():
    """无题材的票单独计数，⛔ 不塞进任何一个题材里。"""
    rows = [{"theme": "游戏"}, {"theme": ""}, {"theme": "—"}, {"theme": "其他"}, {}]
    c = TB.count_by_theme(rows)
    assert c["游戏"] == 1
    assert c["__unclassified__"] == 4


def test_count_by_family_is_separate_from_theme():
    """⛔ 族家数 ≠ 基座家数：族只是同一批票的另一种读法，不能替代基座。"""
    rows = [{"theme": "短剧/互动影游"}, {"theme": "游戏"}, {"theme": "传媒"},
            {"theme": "AI大模型/智能体"}]
    base = TB.count_by_theme(rows)
    fam = TB.count_by_family(rows)
    assert base["短剧/互动影游"] == 1 and base["游戏"] == 1
    assert fam["传媒族"] == 3
    assert "AI大模型/智能体" not in fam          # 无族的票不进族统计


@pytest.mark.parametrize("raw,want", [
    (0, 0), ("3", 3), (3.0, 3), (None, None), (True, None),
    (-1, None), (1.5, None), ("nan", None), ("inf", None), ("", None),
])
def test_parse_zt_count_strict(raw, want):
    assert TB.parse_zt_count(raw) == want


def test_live_counts_keep_source_and_unknown_without_family_merge():
    lt = {"ts": "2026-11-03T10:00:00+08:00", "top5": [
        {"name": "传媒", "zt_count": "3", "zt_count_src": "xgb"},
        {"name": "游戏", "zt_count": None},
        {"name": "业绩增长", "zt_count": 10},
    ]}
    facts = TB.live_theme_counts(lt)
    assert set(facts) == {"传媒", "游戏"}
    assert facts["传媒"]["zt_count"] == 3
    assert facts["传媒"]["zt_count_src"] == "xgb"
    assert facts["传媒"]["date"] == "2026-11-03"
    assert facts["游戏"]["zt_count"] is None
    assert TB.live_theme_counts({"top5": "bad"}) == {}


def test_duplicate_conflicting_counts_are_unknown():
    facts = TB.live_theme_counts({"top5": [
        {"name": "游戏", "zt_count": 2}, {"name": "游戏", "zt_count": 3},
        {"name": "游戏", "zt_count": 3},
    ]})
    assert facts["游戏"]["zt_count"] is None


def test_snapshot_members_and_intersection_are_deduplicated():
    members = TB.snapshot_theme_members({
        "600001.SS": {"plates": ["业绩增长", "游戏"]},
        "600001": {"plates": ["游戏"]},
        "600002": {"plates": ["ST股"]},
        "600003": {"plates": "游戏"},
    })
    assert members == {"游戏": {"600001"}}
    assert TB.count_member_limitups(members["游戏"], ["600001.SS", "600001"]) == 1
    assert TB.count_member_limitups(None, []) is None
    assert TB.count_member_limitups([], None) is None
    assert TB.count_member_limitups([], []) == 0


# ── ④ 完整性守卫 ────────────────────────────────────────────────────────────
def _write_snap(tmp_path, date, mapping, snap_date=None):
    d = Path(tmp_path) / "theme_snapshot"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "theme_map_snapshot_v1",
        "date": snap_date if snap_date is not None else date,
        "generated_at": "2026-11-02 15:31:24",
        "source": "live",
        "count": len(mapping),
        "fingerprint": "x" * 12,
        "mapping": mapping,
    }
    (d / f"theme_{date}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_guard_missing(tmp_path):
    g = TB.guard_snapshot("2026-11-03", cache_dir=tmp_path)
    assert g["ok"] is False and g["status"] == "missing"
    assert "不可回补" in g["why"]
    assert TB.guard_line(g).startswith("⚠️")


def test_guard_empty_mapping(tmp_path):
    _write_snap(tmp_path, "2026-11-03", {})
    g = TB.guard_snapshot("2026-11-03", cache_dir=tmp_path)
    assert g["status"] == "empty" and g["ok"] is False


def test_guard_all_attr_plates_is_empty(tmp_path):
    """⛔ 全是属性标签 = 等于没有题材：不能因为 mapping 非空就判 ok。"""
    _write_snap(tmp_path, "2026-11-03", {
        "600001": {"plates": ["业绩增长"]},
        "600002": {"plates": ["ST股"]},
    })
    g = TB.guard_snapshot("2026-11-03", cache_dir=tmp_path)
    assert g["status"] == "empty"


def test_guard_stale_date(tmp_path):
    """⛔ 快照日期与目标日期不符 → stale，绝不当今天用。"""
    _write_snap(tmp_path, "2026-11-03", {"600001": {"plates": ["游戏"]}},
                snap_date="2026-11-02")
    g = TB.guard_snapshot("2026-11-03", cache_dir=tmp_path)
    assert g["status"] == "stale" and g["ok"] is False


def test_guard_ok_with_full_coverage(tmp_path):
    _write_snap(tmp_path, "2026-11-03", {
        "600001": {"plates": ["游戏"]},
        "600002": {"plates": ["短剧/互动影游"]},
    })
    g = TB.guard_snapshot("2026-11-03", cache_dir=tmp_path,
                          zt_codes=["600001", "600002"])
    assert g["ok"] is True and g["status"] == "ok"
    assert g["coverage"] == 1.0 and g["missing"] == []
    assert TB.guard_line(g) == ""          # ok 时不占版面


def test_guard_not_ok_low_coverage(tmp_path):
    _write_snap(tmp_path, "2026-11-03", {"600001": {"plates": ["游戏"]}})
    g = TB.guard_snapshot("2026-11-03", cache_dir=tmp_path,
                          zt_codes=["600001", "600002", "600003", "600004"])
    assert g["status"] == "not_ok" and g["ok"] is False
    assert g["coverage"] == 0.25
    assert set(g["missing"]) == {"600002", "600003", "600004"}
    assert "⚠️" in TB.guard_line(g)


def test_guard_without_zt_codes_does_not_claim_100(tmp_path):
    """⛔ 没给涨停池 → coverage 必须是 None，不许假装 100%。"""
    _write_snap(tmp_path, "2026-11-03", {"600001": {"plates": ["游戏"]}})
    g = TB.guard_snapshot("2026-11-03", cache_dir=tmp_path)
    assert g["ok"] is True                 # 只做了非空/非陈旧校验
    assert g["coverage"] is None
    assert "未计算" in g["why"]


def test_guard_empty_zt_pool_is_ok(tmp_path):
    """非交易日涨停池为空 → 只校验快照本身，不判失败。"""
    _write_snap(tmp_path, "2026-11-03", {"600001": {"plates": ["游戏"]}})
    g = TB.guard_snapshot("2026-11-03", cache_dir=tmp_path, zt_codes=[])
    assert g["ok"] is True and g["coverage"] is None


def test_guard_attr_plates_not_counted_as_covered(tmp_path):
    """属性标签票不算「已归类」——它们没有板块，算进去会把覆盖率虚高。"""
    _write_snap(tmp_path, "2026-11-03", {
        "600001": {"plates": ["游戏"]},
        "600002": {"plates": ["业绩增长"]},
    })
    g = TB.guard_snapshot("2026-11-03", cache_dir=tmp_path,
                          zt_codes=["600001", "600002"])
    assert g["coverage"] == 0.5
    assert g["missing"] == ["600002"]


def test_guard_sidecar_written(tmp_path):
    _write_snap(tmp_path, "2026-11-03", {"600001": {"plates": ["游戏"]}})
    p = TB.guard_sidecar("2026-11-03", cache_dir=tmp_path, zt_codes=["600001"])
    assert p is not None and Path(p).exists()
    d = json.loads(Path(p).read_text(encoding="utf-8"))
    assert d["ok"] is True and "generated_at" in d


# ── ⑤ 真实数据回归（11-02 传媒实证）────────────────────────────────────────
def test_real_1120_media_family_is_51(tmp_path):
    """11-02 涨停池：短剧/互动影游 40 + 游戏 7 + 传媒 4 → 传媒族 51。

    这是「系统报 4 家、用户看到大爆发」的定量证据：不是没识别，是口径拆碎了。
    ⚠️ 依赖真实快照文件；不在则跳过（CI/新机不阻塞）。
    """
    root = Path(__file__).resolve().parents[1]
    snap = root / "cache" / "theme_snapshot" / "theme_2026-11-02.json"
    if not snap.exists():
        pytest.skip("无 11-02 真实快照")
    raw = json.loads(snap.read_text(encoding="utf-8"))
    rows = [{"theme": TB.theme_of_plates((v or {}).get("plates")) or ""}
            for v in (raw.get("mapping") or {}).values()]
    base = TB.count_by_theme(rows)
    fam = TB.count_by_family(rows)
    assert base.get("短剧/互动影游") == 40
    assert base.get("游戏") == 7
    assert base.get("传媒") == 4
    assert fam.get("传媒族") == 51
