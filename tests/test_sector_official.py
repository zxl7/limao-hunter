#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选股宝板块 → 东财官方板块 映射层回归（2026-10-08）

用户原话：「系统自造聚合名改为东财官方板块名！！」

规则（本测试锁死）：
  ① **不靠纯名字猜测**：必须「名字证据 ∧ 成分股交集」双证；纯交集需 ≥3 只成员且覆盖 ≥80%；
  ② 名称匹配**不对称**：选股宝名 ⊂ 东财名（≥2字）→ 0.9；东财名 ⊂ 选股宝名**须东财名 ≥3 字** → 0.8
     （⛔ 挡「电子 ⊂ 电子布」假命中，同 09-15 回流卡铁律）；
  ③ **精确同名豁免覆盖率门槛**（MIN_COVERAGE_EXACT=0.15）：实证「锂电池」与东财「锂电池 BK1303」
     字面一致但成分覆盖仅 ~0.44，被 0.5 门槛挡掉会错配到「锂电池概念」；
  ④ 排序 = **名字相似度优先**（精确同名必须排第一），覆盖率只做内部次序；
  ⑤ 噪声板块（地域/统计标签）必须过滤，⛔ 不得让「深股通」「高市净率」进映射；
  ⑥ 未映射 → `boards=[]` / `method="none"`（诚实，不编造官方对应）；
  ⑦ `ENABLE_SECTOR_OFFICIAL=False` → lookup 恒 None（回到改动前行为）。

隔离约定：ROOT 全部指向 tmp_path，**不读写生产 cache**、不联网（fetch 被替换）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import sector_official as so  # noqa: E402


# ── 夹具 ────────────────────────────────────────────────────────────
@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离 ROOT + 屏蔽网络。"""
    monkeypatch.setattr(so, "ROOT", tmp_path)
    monkeypatch.setattr(so, "SLIST_SLEEP", 0)
    monkeypatch.setattr(so, "fetch_em_sectors", lambda code: [])
    return tmp_path


def _cache(pairs: dict[str, list[tuple[str, str]]]) -> dict:
    """构造个股 → 东财板块缓存。pairs: code -> [(bk, name), ...]"""
    return {c: {"boards": [[bk, nm] for bk, nm in bs], "ts": 0} for c, bs in pairs.items()}


# 锂电池板块 5 只成员（含噪声板块）
LIT = _cache({
    "300530": [("BK1033", "电池"), ("BK1303", "锂电池"), ("BK0988", "钠离子电池"),
               ("BK0804", "深股通"), ("BK0158", "湖南板块"), ("BK1722", "高市净率")],
    "002058": [("BK1033", "电池"), ("BK1302", "电池化学品"), ("BK1722", "高市净率"),
               ("BK1051", "昨日连板_含一字"), ("BK0145", "上海板块")],
    "600241": [("BK1033", "电池"), ("BK1303", "锂电池"), ("BK0574", "锂电池概念"),
               ("BK1638", "最近多板"), ("BK0161", "辽宁板块")],
    "002733": [("BK1033", "电池"), ("BK1303", "锂电池"), ("BK1648", "电池技术")],
    "002805": [("BK1033", "电池"), ("BK1303", "锂电池"), ("BK0968", "固态电池")],
})


# ── ① 噪声过滤 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("nm", [
    "深股通", "沪股通", "高市净率", "QFII重仓", "昨日连板_含一字", "最近多板",
    "题材股", "东方财富热股", "破增发价股", "近期新高", "百日新高", "融资融券",
    "湖南板块", "上海板块", "辽宁板块", "长江三角", "ST股", "次新股", "2026中报预减",
])
def test_noise_filtered(nm):
    assert so._is_noise_board(nm) is True, f"{nm} 属统计标签/地域，必须过滤"


@pytest.mark.parametrize("nm", ["电池", "锂电池", "锂电池概念", "固态电池", "人形机器人",
                                "航运", "医药生物", "房地产", "航运港口", "电池化学品"])
def test_real_boards_kept(nm):
    assert so._is_noise_board(nm) is False, f"{nm} 是真题材/行业，不得误杀"


# ── ② 名称相似度（不对称规则）──────────────────────────────────────
def test_name_score_exact():
    assert so._name_score("锂电池", "锂电池") == 1.0


def test_name_score_xgb_is_abbrev():
    """选股宝名 ⊂ 东财名（≥2字）→ 0.9：医药 ⊂ 医药生物"""
    assert so._name_score("医药", "医药生物") == 0.9
    assert so._name_score("机器人", "机器人概念") == 0.9


def test_name_score_em_is_suffix():
    """东财名 ⊂ 选股宝名 且东财名 ≥3 字 → 0.8（参数序：候选/选股宝名, 东财名）"""
    assert so._name_score("固态电池概念", "固态电池") == 0.8
    assert so._name_score("锂电池概念", "锂电池") == 0.8
    # 边界：东财名仅 2 字 → 不认（见下一例）


def test_name_score_blocks_two_char_reverse():
    """⛔ 2 字东财名禁止反向包含 —— 挡 电子⊄电子布 / 电网⊄智能电网 假命中"""
    assert so._name_score("电子布", "电子") == 0.0
    assert so._name_score("智能电网", "电网") == 0.0


def test_name_score_unrelated():
    assert so._name_score("锂电池", "白酒") == 0.0
    assert so._name_score("", "电池") == 0.0


# ── ③ secid 映射 ────────────────────────────────────────────────────
@pytest.mark.parametrize("code,expect", [("600241", "1.600241"), ("300530", "0.300530"),
                                         ("002058", "0.002058"), ("688196", "1.688196")])
def test_secid(code, expect):
    assert so._secid(code) == expect


# ── ④ resolve_sector ────────────────────────────────────────────────
def test_resolve_exact_name_first():
    """精确同名「锂电池(BK1303)」必须排第一 —— 哪怕「锂电池概念」成分覆盖更高。"""
    codes = ["300530", "600241", "002733", "002805"]
    r = so.resolve_sector("锂电池", codes, cache=LIT)
    assert r["method"] == "name+members"
    assert r["boards"][0]["name"] == "锂电池" and r["boards"][0]["bk"] == "BK1303"
    assert r["boards"][0]["score"] == 1.0


def test_resolve_exact_exempt_from_coverage_gate():
    """精确同名的覆盖率门槛放宽到 0.15 —— 覆盖率 0.25（低于常规门槛 0.5）仍须入选。

    实证依据：选股宝「锂电池」下只有部分成员属东财「锂电池 BK1303」
    （另一些属「电池 BK1033」），覆盖率天然偏低；若卡 0.5 会错配到「锂电池概念」。
    """
    cache = dict(LIT)
    for c in ("600001", "600002", "600003"):      # 只属「电池」，不属「锂电池」
        cache[c] = {"boards": [["BK1033", "电池"]], "ts": 0}
    codes = ["300530", "600001", "600002", "600003"]   # 锂电池覆盖 = 1/4 = 0.25
    r = so.resolve_sector("锂电池", codes, cache=cache)
    hit = [b for b in r["boards"] if b["name"] == "锂电池"]
    assert hit, "精确同名即便覆盖率低于常规门槛也必须入选"
    assert hit[0]["coverage"] == pytest.approx(0.25, abs=0.01)
    assert r["boards"][0]["name"] == "锂电池", "精确同名须排第一"


def test_resolve_filters_noise():
    """噪声板块不得出现在结果里。"""
    r = so.resolve_sector("锂电池", ["300530", "002058", "600241"], cache=LIT)
    names = " ".join(b["name"] for b in r["boards"])
    for bad in ("深股通", "高市净率", "湖南板块", "昨日连板", "最近多板", "上海板块"):
        assert bad not in names, f"{bad} 是噪声，不得进映射"


def test_resolve_alias_fallback():
    """东财无同名板块的聚合名 → 走 ALIAS。"""
    r = so.resolve_sector("大消费", ["002242"], cache=LIT)
    assert r["method"] == "alias"
    assert "食品饮料" in [b["name"] for b in r["boards"]]


def test_resolve_none_when_no_evidence():
    """既无名字证据、成员又不足 → none（诚实，不编造）。"""
    r = so.resolve_sector("某个新题材", ["300530"], cache=LIT)
    assert r["method"] == "none" and r["boards"] == []


def test_resolve_members_only_needs_3_and_strong_coverage():
    """纯交集兜底：成员 ≥3 且覆盖 ≥80% 才认。"""
    cache = _cache({f"60000{i}": [("BK9999", "某某概念")] for i in range(3)})
    r = so.resolve_sector("无对应名的板块", ["600000", "600001", "600002"], cache=cache)
    assert r["method"] == "members" and r["boards"][0]["name"] == "某某概念"


def test_resolve_members_only_rejected_when_few_members():
    """只有 2 只成员 → 纯交集不稳，不得入选。"""
    cache = _cache({f"60000{i}": [("BK9999", "某某概念")] for i in range(2)})
    r = so.resolve_sector("无对应名的板块", ["600000", "600001"], cache=cache)
    assert r["method"] == "none"


def test_resolve_no_members():
    r = so.resolve_sector("锂电池", [], cache=LIT)
    assert r["method"] == "none" and r["members"] == 0


# ── ⑤ 成员票收集 ────────────────────────────────────────────────────
def test_member_codes_of_collects_leader_followers_zt():
    sec = {
        "leader": {"code": "002058.SZ"},
        "followers": [{"name": "领湃科技", "pct": 19.98}, {"name": "x", "pct": 1}],
        "zt_stocks": [{"name": "时代万恒", "code": "600241.SS"}],
    }
    # followers 无 code → 只收 leader + zt_stocks
    assert so.member_codes_of(sec) == ["002058", "600241"]


def test_member_codes_dedup():
    sec = {"leader": {"code": "002058"}, "zt_stocks": [{"code": "002058"}, {"code": "600241"}]}
    assert so.member_codes_of(sec) == ["002058", "600241"]


# ── ⑥ build_map / lookup / 开关 ─────────────────────────────────────
def test_build_map_and_lookup(iso, monkeypatch):
    monkeypatch.setattr(so, "load_stock_cache", lambda: LIT)
    top5 = [{"name": "锂电池", "leader": {"code": "002058"},
             "followers": [], "zt_stocks": [{"code": "600241"}]}]
    m = so.build_map(top5, cache=LIT)
    so.save_map(m)
    assert m["锂电池"]["boards"][0]["name"] == "锂电池"

    r = so.lookup("锂电池")
    assert r and r["boards"][0]["bk"] == "BK1303"


def test_build_map_does_not_downgrade(iso):
    """已有 name+members 结果不得被后续的 alias/none 覆盖。"""
    prior = {"锂电池": {"boards": [{"bk": "BK1303", "name": "锂电池"}],
                       "method": "name+members", "members": 5}}
    top5 = [{"name": "锂电池", "leader": {"code": "002058"}, "followers": [], "zt_stocks": []}]
    m = so.build_map(top5, cache={}, prior=prior)
    assert m["锂电池"]["method"] == "name+members"
    assert m["锂电池"]["boards"][0]["bk"] == "BK1303"


def test_build_map_keeps_history(iso):
    """新板块并入时，历史条目必须保留（只增不减）。"""
    prior = {"旧板块": {"boards": [{"bk": "BK0001", "name": "旧"}], "method": "name+members", "members": 3}}
    top5 = [{"name": "新板块", "leader": {"code": "002058"}, "followers": [], "zt_stocks": []}]
    m = so.build_map(top5, cache={}, prior=prior)
    assert "旧板块" in m and "新板块" in m


def test_lookup_none_for_unmapped(iso):
    so.save_map({"锂电池": {"boards": [{"bk": "BK1303", "name": "锂电池"}], "method": "name+members"}})
    assert so.lookup("电子树脂") is None
    assert so.lookup("") is None


def test_official_display(iso):
    so.save_map({"大消费": {"boards": [{"bk": "BK0438", "name": "食品饮料"},
                                     {"bk": "", "name": "商贸零售"}], "method": "alias"}})
    assert so.official_display("大消费") == "食品饮料/商贸零售"
    assert so.official_display("大消费", sep="+") == "食品饮料+商贸零售"
    assert so.official_display("未映射的") == ""


def test_switch_off_returns_none(monkeypatch, iso):
    so.save_map({"锂电池": {"boards": [{"bk": "BK1303", "name": "锂电池"}], "method": "name+members"}})
    monkeypatch.setattr(so, "ENABLE_SECTOR_OFFICIAL", False)
    assert so.lookup("锂电池") is None, "关开关必须回到改动前行为"


def test_production_default_is_on():
    src = (SCRIPTS / "sector_official.py").read_text(encoding="utf-8")
    assert "ENABLE_SECTOR_OFFICIAL = True" in src


# ── ⑦ 隔离：不得写生产 cache ────────────────────────────────────────
def test_paths_follow_root(iso):
    assert str(so.stock_cache_path()).startswith(str(iso))
    assert str(so.map_path()).startswith(str(iso))


def test_save_and_load_roundtrip(iso):
    so.save_map({"A": {"boards": [], "method": "none"}})
    assert (iso / "cache" / "sector_official_map.json").exists()
    assert so.load_map()["A"]["method"] == "none"


def test_ensure_codes_skips_known_and_disabled(iso, monkeypatch):
    """已知 code 不重复联网；总开关关闭时不联网。"""
    calls = []
    monkeypatch.setattr(so, "fetch_em_sectors", lambda c: (calls.append(c), [["BK1", "X"]])[1])
    cache = {"300530": {"boards": [["BK1033", "电池"]], "ts": 0}}
    so.ensure_codes(["300530"], cache=cache)
    assert calls == [], "已知 code 不得重复请求"

    monkeypatch.setattr(so, "ENABLE_SECTOR_OFFICIAL", False)
    so.ensure_codes(["002058"], cache=cache)
    assert calls == [], "总开关关闭时不得联网"


def test_intraday_pulse_wires_official_field():
    """intraday_pulse 的 top5 必须挂 official 字段，且 name 不被改动。"""
    src = (SCRIPTS / "intraday_pulse.py").read_text(encoding="utf-8")
    assert '"official": _official_lookup(sec["name"])' in src
    assert "from sector_official import lookup as _official_lookup" in src
