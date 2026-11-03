#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quant-review 打分镜像 · 回归（旁路必须真的只是旁路）

守三件事：
1. **算法对得上**：六维/接力动力的阈值与原实现一致（改阈值必须改测试）。
2. **口径不打架**：低成交额惩罚、20cm 加分、688 无权限三处冲突必须显式暴露。
3. **旁路是旁路**：annotate 不改顺序、不改分数、不产出名额/仓位字段。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wealth_code.core import qr_mirror as Q  # noqa: E402


def _zt(code="600118", name="中国卫星", boards=1, cje=31.0, seal=0.8,
        hs=4.8, lt=200.0, fbt="103000", zbc=0, sector="航天", is_20cm=False):
    return {"code": code, "name": name, "boards": boards, "cje_yi": cje,
            "seal": seal, "hs": hs, "lt_yi": lt, "fbt": fbt, "zbc": zbc,
            "sector": sector, "is_20cm": is_20cm}


# ── 六维：逐条对齐原实现阈值 ─────────────────────────────────────────────────
@pytest.mark.parametrize("conf,boards,expected", [
    (0.6, 2, 20), (0.6, 1, 16), (0.4, 2, 14), (0.4, 1, 10), (0.1, 1, 6), (0.0, 1, 0),
])
def test_mainline_fit(conf, boards, expected):
    assert Q._mainline_fit(conf, boards) == expected


def test_mainline_fit_missing_is_none():
    """缺题材置信度 → None（不按 0 分算，否则等于编造"没主线"）。"""
    assert Q._mainline_fit(None, 2) is None


@pytest.mark.parametrize("boards,cje,expected", [
    (3, 5.0, 17), (2, 5.0, 14), (1, 120.0, 16), (1, 35.0, 10), (1, 10.0, 6),
])
def test_tier_position(boards, cje, expected):
    assert Q._tier_position(boards, cje) == expected


@pytest.mark.parametrize("cje,expected", [
    (120.0, 15), (60.0, 12), (35.0, 9), (12.0, 5), (4.0, 2), (1.0, 0),
])
def test_volume_score(cje, expected):
    assert Q._volume_score(cje) == expected


@pytest.mark.parametrize("n,expected", [(9, 10), (5, 7), (3, 4), (1, 1)])
def test_sector_resonance(n, expected):
    assert Q._sector_resonance(n) == expected


@pytest.mark.parametrize("hs,expected", [
    (5.0, 10), (15.0, 10), (20.0, 6), (1.5, 3), (40.0, 2), (0.0, 0),
])
def test_turnover_health(hs, expected):
    assert Q._turnover_health(hs) == expected


# ── 封板质量：代理实现必须标 proxy，不能冒充原字段 ────────────────────────────
def test_seal_quality_proxy_high():
    # 封单 12 亿 / 流通 200 亿 = 6%，且 10:00 前首封 → 满分
    assert Q._seal_quality(12.0, 200.0, "093000") == 15


def test_seal_quality_proxy_low_ratio():
    # 封单 0.9 亿 / 流通 200 亿 = 0.45% → 不给分（原实现对 ≥3 亿才兜底 6 分）
    assert Q._seal_quality(0.9, 200.0, "103000") == 0


def test_seal_quality_fallback_by_amount():
    """无流通市值 → 退化为封单绝对额（≥3 亿给 6 分）。"""
    assert Q._seal_quality(5.0, None, None) == 6


def test_seal_quality_all_missing():
    assert Q._seal_quality(None, None, None) is None


# ── 接力动力：低成交额惩罚与「我们口径」 ─────────────────────────────────────
def test_relay_power_low_amount_penalty_exposed():
    """成交额 <5 亿 → 原式 −3；score_ours 撤掉这 3 分（用户：缩量封板是筹码锁定）。"""
    rp = Q.relay_power(boards=4, cje_yi=0.5, seal_yi=1.65, turnover=1.6,
                       zbc=0, sector_zt_count=3, theme_conf=0.0)
    assert rp["low_amount_penalty"] == -3.0
    assert rp["score_ours"] == rp["score"] + 3
    assert rp["score"] <= 100


def test_relay_power_no_penalty_when_amount_ok():
    rp = Q.relay_power(boards=2, cje_yi=20.0, seal_yi=2.0, turnover=10.0,
                       zbc=0, sector_zt_count=5, theme_conf=0.7)
    assert rp["low_amount_penalty"] == 0.0
    assert rp["score_ours"] == rp["score"]


def test_relay_power_break_penalty():
    """炸板次数 ≥5 → −10（原式口径）。"""
    clean = Q.relay_power(2, 20.0, 2.0, 10.0, 0, 5, 0.7)["score"]
    broken = Q.relay_power(2, 20.0, 2.0, 10.0, 5, 5, 0.7)["score"]
    assert clean - broken == 10


def test_relay_power_missing_factors_listed():
    """我们没有的 zt_* 因子必须写进 missing，而不是当 0 分扣掉。"""
    rp = Q.relay_power(2, 20.0, 2.0, 10.0, 0, 5, 0.7)
    assert "zt_break_risk" in rp["missing"]
    assert "zt_relay_factor" in rp["missing"]


# ── 无权限：688 / 北交所 不出分 ──────────────────────────────────────────────
@pytest.mark.parametrize("code", ["688507", "830799", "430047", "920001"])
def test_no_permission_codes_not_applicable(code):
    m = Q.mirror_one(_zt(code=code, name="某科创"))
    assert m["applicable"] is False
    assert "权限" in m["reason"]


# ── 缺维 → 不给分档（避免"全部只观察"的假结论）───────────────────────────────
def test_missing_dim_no_band():
    m = Q.mirror_one(_zt(), 3)         # 给了板块家数，但不传 theme_conf → 只缺主线维
    assert m["six_dim"]["total_scaled"] is None
    assert m["band"] is None
    assert m["six_dim"]["coverage"] == "5/6"
    assert m["six_dim"]["known_full"] == 70


def test_full_dims_gives_band():
    m = Q.mirror_one(_zt(boards=2, cje=20.0, seal=12.0, hs=8.0), 6, 0.7)
    assert m["six_dim"]["coverage"] == "6/6"
    assert m["six_dim"]["total_scaled"] is not None
    assert m["band"] in {"强候选", "重点候选", "条件触发", "只观察"}


# ── 冲突必须显式暴露 ────────────────────────────────────────────────────────
def test_conflict_low_amount_registered():
    m = Q.mirror_one(_zt(boards=4, cje=0.5, seal=1.6, hs=1.5, fbt="092500"), 3, 0.7)
    ids = [c["id"] for c in m["conflicts"]]
    assert "low_amount_penalty" in ids


def test_conflict_20cm_registered():
    m = Q.mirror_one(_zt(code="300308", name="中际旭创", is_20cm=True), 4, 0.7)
    ids = [c["id"] for c in m["conflicts"]]
    assert "board_bonus_20cm" in ids


def test_conflict_entries_have_both_sides():
    """每条冲突必须同时写清「原实现」和「我们口径」——只写"冲突"等于没说。"""
    for c in Q.CONFLICTS:
        assert c["rule"] and c["ours"] and c["handling"]


# ── 主线置信度：我们的口径映射，来源必须标清 ──────────────────────────────────
@pytest.mark.parametrize("tier,heat,expected", [
    ("front", "H", 0.7), ("front", "M", 0.5), ("front", "", 0.4), ("second", "H", 0.3),
])
def test_theme_conf_mapping(tier, heat, expected):
    conf, src = Q._theme_conf_from({"line_tier_outpool": tier, "core_theme_heat": heat})
    assert conf == pytest.approx(expected)
    assert src.startswith("ours(")


def test_theme_conf_missing_is_none():
    """没有身位字段 → None（⛔ 不猜「不是主线」）"""
    conf, src = Q._theme_conf_from({})
    assert conf is None and src == "missing"


def test_mainline_source_labelled():
    """用我们的映射时必须标注来源，不能让人误以为就是原 in_ml_top_conf。"""
    m = Q.mirror_one({"code": "600118", "name": "A", "boards": 2, "cje_yi": 20.0,
                      "seal_amt": 2.0, "lt_mktcap": 100.0, "hs": 8.0, "fbt": "100000",
                      "line_tier_outpool": "front", "core_theme_heat": "H"}, 5)
    assert m["sources"]["mainline_fit"].startswith("ours(")
    assert m["six_dim"]["coverage"] == "6/6"


# ── 字段别名：盘后候选与涨停池两套命名都要吃 ──────────────────────────────────
def test_alias_seal_amt_and_lt_mktcap():
    """candidates-*.json 用 seal_amt / lt_mktcap，不能因字段名不同就判「无数据」。"""
    m = Q.mirror_one({"code": "600118", "boards": 1, "cje_yi": 30.0,
                      "seal_amt": 12.0, "lt_mktcap": 200.0, "hs": 5.0, "fbt": "093000"}, 4, 0.7)
    assert m["six_dim"]["parts"]["seal_quality"] == 15
    assert m["six_dim"]["missing"] == []


def test_pool_zt_preferred_over_sector_count():
    """候选自带题材池涨停家数时，优先用它（我们的既有口径）。"""
    m = Q.mirror_one({"code": "600118", "boards": 1, "cje_yi": 30.0, "seal": 1.0,
                      "hs": 5.0, "theme_pool_zt_count": 9}, 3, 0.7)
    assert m["six_dim"]["parts"]["sector_resonance"] == 10
    assert "theme_pool_zt_count" in m["sources"]["sector_zt_count"]


def test_annotate_keeps_order_and_scores():
    cands = [
        {"code": "600118", "name": "A", "raw_score": 91.5, "boards": 1, "cje_yi": 31.0,
         "seal": 0.8, "hs": 4.8, "lt_yi": 200.0, "fbt": "103000", "sector": "航天"},
        {"code": "002943", "name": "B", "raw_score": 88.0, "boards": 2, "cje_yi": 9.0,
         "seal": 0.8, "hs": 18.1, "lt_yi": 50.0, "fbt": "100000", "sector": "机械"},
        {"code": "688507", "name": "C", "raw_score": 99.0, "boards": 1, "cje_yi": 1.0,
         "seal": 0.1, "hs": 2.0, "lt_yi": 30.0, "fbt": "140000", "sector": "软件"},
    ]
    zt = [dict(c) for c in cands]
    before = [(c["code"], c["raw_score"]) for c in cands]
    out = Q.annotate(cands, zt)
    assert [(c["code"], c["raw_score"]) for c in out] == before     # 顺序与分数都不动
    assert all("qr_mirror" in c for c in out)


def test_annotate_never_emits_position_or_quota():
    """镜像不得产出名额/仓位类字段——它不参与裁决。"""
    out = Q.annotate([_zt()], [_zt()])
    m = out[0]["qr_mirror"]
    for forbidden in ("position", "quota", "per_pick_cap", "total_cap", "can_buy"):
        assert forbidden not in m


def test_annotate_infers_sector_from_pool():
    """候选行没有 `sector` 时，用涨停池 code→板块 反查补齐（⛔ 不猜、不按名字相似匹配）。"""
    zt = [
        {"code": "600118", "name": "中国卫星", "sector": "航天", "boards": 1},
        {"code": "600879", "name": "航天电子", "sector": "航天", "boards": 1},
        {"code": "600877", "name": "电科芯片", "sector": "航天", "boards": 1},
    ]
    cand = {"code": "600118", "name": "中国卫星", "boards": 1, "cje_yi": 31.0,
            "seal": 0.8, "hs": 4.8, "lt_yi": 200.0, "fbt": "103000"}
    out = Q.annotate([cand], zt, {"600118": 0.7})
    m = out[0]["qr_mirror"]
    assert m["six_dim"]["parts"]["sector_resonance"] == 4      # 航天 3 家 → 4 分
    assert m["six_dim"]["coverage"] == "6/6"                  # 补上板块维后六维齐全


def test_annotate_unknown_code_leaves_sector_unknown():
    """池子里查不到的票 → 板块维缺失，⛔ 不硬塞一个板块。"""
    cand = {"code": "999999", "name": "池外票", "boards": 1, "cje_yi": 10.0,
            "seal": 1.0, "hs": 5.0, "lt_yi": 50.0, "fbt": "100000"}
    out = Q.annotate([cand], [{"code": "600118", "sector": "航天"}], {"999999": 0.7})
    assert out[0]["qr_mirror"]["six_dim"]["parts"]["sector_resonance"] is None


def test_annotate_survives_bad_input():
    """脏候选不能让镜像抛异常打断主链。"""
    out = Q.annotate([{"code": "600118", "boards": "abc", "cje_yi": "x"}], [])
    assert out[0]["qr_mirror"]["schema"] == Q.SCHEMA


def test_switch_off_returns_untouched(monkeypatch):
    monkeypatch.setattr(Q, "ENABLE_QR_MIRROR", False)
    cands = [_zt()]
    out = Q.annotate(cands, [_zt()])
    assert "qr_mirror" not in out[0]


# ═══════════════════════════════════════════════════════════════════════════
# 2026-10-30 两处字段口径修正（拿真实候选跑才发现的坑）
#   ① 原来 `_theme_conf_from` 只认 `line_tier_outpool`，而普通 relay/watch 行
#     根本没这个字段（那是「池外高辨识」P1 才打的标）→ 全池 11/11 缺主线维 →
#     六维永远凑不齐，分档永远出不来。
#   ② 别名表把「申万行业同门家数」排在「题材同门家数」前面 → 板块维系统性算低。
# ═══════════════════════════════════════════════════════════════════════════
def _cand(code="600001", name="票", role=None, heat=None, **kw):
    c = {"code": code, "name": name}
    if role is not None:
        c["leader_role"] = role
    if heat is not None:
        c["core_theme_heat"] = heat
    c.update(kw)
    return c


# ── 入口②：leader_role 映射主线置信度 ─────────────────────────────────────
@pytest.mark.parametrize("role,heat,expected", [
    ("准龙头", "H", 0.7),
    ("准龙头", "M", 0.5),
    ("准龙头", None, 0.4),
    ("总龙头", "H", 0.7),
    ("跟风", "H", 0.3),      # 跟风不吃热度加成
    ("补涨", None, 0.3),
    ("中军", "H", 0.3),
])
def test_theme_conf_from_leader_role(role, heat, expected):
    conf, src = Q._theme_conf_from(_cand(role=role, heat=heat))
    assert conf == expected
    assert "leader_role" in src


def test_theme_conf_from_prefers_line_tier():
    """两个入口都在时，`line_tier_outpool`（前沿口径）优先。"""
    c = _cand(role="跟风", heat="H", line_tier_outpool="front")
    conf, src = Q._theme_conf_from(c)
    assert conf == 0.7 and "line_tier" in src


def test_theme_conf_from_unknown_role_is_missing():
    """认不出的角色 → None，⛔ 不猜。"""
    conf, src = Q._theme_conf_from(_cand(role="看过"))
    assert conf is None and src == "missing"


# ── 板块家数：题材同门优先于行业同门 ────────────────────────────────────────
def test_pool_zt_prefers_theme_over_industry():
    """中新赛克实证：AI算力题材 10 家、行业「计算机设」1 家。
    题材 10 家 → 板块维 10/10；行业 1 家 → 1/10。⛔ 不许拿行业家数冒充板块家数。"""
    c = _cand(theme="AI算力", sector="计算机设", theme_mates_cnt=10, sector_mates_cnt=1)
    m = Q.mirror_one(c)
    assert m["six_dim"]["parts"]["sector_resonance"] == 10
    assert m["sources"]["sector_zt_count"] == "ours(theme_mates_cnt)"


def test_pool_zt_falls_back_to_industry():
    c = _cand(sector="计算机设", sector_mates_cnt=1)
    m = Q.mirror_one(c)
    assert m["six_dim"]["parts"]["sector_resonance"] == 1
    assert m["sources"]["sector_zt_count"] == "ours(sector_mates_cnt)"


def test_pool_zt_top_source_wins():
    """`theme_pool_zt_count`（盘后精选池自带）排最前。"""
    c = _cand(theme_pool_zt_count=9, theme_mates_cnt=1, sector_mates_cnt=1)
    m = Q.mirror_one(c)
    assert m["six_dim"]["parts"]["sector_resonance"] == 10
    assert m["sources"]["sector_zt_count"] == "ours(theme_pool_zt_count)"
