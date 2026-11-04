"""名字炒作集群 + 独苗高开派发闸 测试（2026-11-04 用户定）。

用户原话：
① 「名字炒作维度可以做」
② 「独苗高开 + 同板块集体绿开」不是强势信号，是派发信号……独苗高开可以立马设，适合跑路

守的边界：
1. 共字集群客观可复现；⛔ 不做谐音扩展（需人工表，易成事后编规则）
2. 通用字必须剔除，否则「新」「中」会伪造巨型假簇
3. 独苗闸：自己高开 ≥3% ∧ 同族其余票红开率 <40% → watch，⛔ 不占名额
4. 同族 = 题材族 ∪ 名字簇，取**最差**那一族判
5. 缺数据一律不裁决（不猜）
6. 真实案例：2026-11-04 东方海洋（名字「洋」簇其余 2 只 0% 红开）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "wealth_code") not in sys.path:
    sys.path.insert(0, str(ROOT / "wealth_code"))

from core import name_cluster as NC  # noqa: E402
from core import pool_call as PC  # noqa: E402


def row(code: str, name: str, **kw):
    return {"code": code, "name": name, "boards": 2, **kw}


# ── ① 名字集群识别 ─────────────────────────────────────────────────────────
def test_common_char_cluster_found():
    rows = [row("600261", "阳光照明"), row("001318", "阳光乳业"),
            row("000608", "阳光股份"), row("002086", "东方海洋")]
    cl = NC.build_clusters(rows)
    assert set(cl["阳"]) == {"600261", "001318", "000608"}
    assert "洋" not in cl          # 只有 1 只，不成簇


def test_generic_chars_are_excluded():
    """⛔ 通用字不能造簇：「科技」「股份」「中国」这类任何一天都能撞一大把。"""
    rows = [row("600001", "科技A"), row("600002", "科技B"), row("600003", "科技C")]
    cl = NC.build_clusters(rows)
    for ch in "科技股份中国新材":
        assert ch not in cl, ch


def test_min_size_threshold():
    rows = [row("600001", "甲洋"), row("600002", "乙洋")]
    assert NC.build_clusters(rows, min_size=3) == {}
    assert set(NC.build_clusters(rows, min_size=2)["洋"]) == {"600001", "600002"}


def test_non_six_digit_codes_ignored():
    rows = [row("600001", "阳光A"), row("600002", "阳光B"), row("000300", "阳光C"),
            row("SH000001", "阳光D")]
    cl = NC.build_clusters(rows)
    assert "SH000001" not in cl.get("阳", [])


def test_st_excluded():
    rows = [row("600001", "ST阳光"), row("600002", "阳光A"), row("600003", "阳光B")]
    assert "600001" not in NC.build_clusters(rows).get("阳", [])


def test_cluster_map_multi_membership():
    """一只票可以同时在多个簇里（阳光照明 = 阳 + 光）。"""
    rows = [row("600261", "阳光照明"), row("001318", "阳光乳业"),
            row("000608", "阳光股份")]
    cm = NC.cluster_map(rows)
    assert set(cm["600261"]) == {"阳", "光"}


def test_annotate_writes_field_only():
    rows = [row("600261", "阳光照明"), row("001318", "阳光乳业"), row("000608", "阳光股份")]
    before = [dict(r) for r in rows]
    info = NC.annotate(rows)
    assert info["cluster_count"] >= 1
    assert all("name_cluster" in r for r in rows)
    # ⛔ 不改其它字段
    for a, b in zip(rows, before):
        assert {k: v for k, v in a.items() if k != "name_cluster"} == b


# ── ② 独苗高开闸 ───────────────────────────────────────────────────────────
def _cand(code, name, boards=2, theme="大消费"):
    return {"code": code, "name": name, "boards": boards, "theme": theme,
            "core_theme": theme, "bucket": "watch"}


def _call(cands, quotes, zt_rows):
    return PC.call_pool({"date": "2026-11-03", "relay": [], "watch": cands,
                         "environment": {"regime": "neutral"}},
                        quotes, theme_open_rows=zt_rows, stance="attack")


def test_lone_gap_gate_blocks_dongfang_real_case(monkeypatch):
    """真实案例 2026-11-04 东方海洋：名字「洋」簇其余 2 只全绿开、自己 +4.68%。"""
    monkeypatch.setattr(PC, "POOL_CALL_HIGH_WIN_GATE", False)   # 排除情绪闸干扰
    monkeypatch.setattr(PC, "POOL_CALL_LEADER_ONLY", False)
    monkeypatch.setattr(PC, "POOL_CALL_DRAGON_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_BACKROW_GATE", False)
    zt = [row("002086", "东方海洋", sector="农产品加"),
          row("600558", "大西洋", sector="化学制品"),
          row("002696", "百洋股份", sector="一般零售")]
    quotes = {
        "002086": {"prev_close": 3.42, "auction_price": 3.58},   # +4.68%
        "600558": {"prev_close": 10.0, "auction_price": 9.90},   # −1.0%
        "002696": {"prev_close": 10.0, "auction_price": 9.30},   # −7.0%
    }
    res = _call([_cand("002086", "东方海洋")], quotes, zt)
    v = res["views"]["002086"]
    assert v["view"] == "watch"
    assert v.get("lone_gap_gate") is True
    assert v.get("lone_peer_src") == "名字「洋」"
    assert "派发" in v["reason"]


def test_lone_gap_gate_not_triggered_when_peers_strong(monkeypatch):
    """同族其余票大多红开 → 不是独苗，不拦。"""
    monkeypatch.setattr(PC, "POOL_CALL_HIGH_WIN_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_LEADER_ONLY", False)
    monkeypatch.setattr(PC, "POOL_CALL_DRAGON_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_BACKROW_GATE", False)
    zt = [row("002086", "东方海洋"), row("600558", "大西洋"), row("002696", "百洋股份")]
    quotes = {
        "002086": {"prev_close": 3.42, "auction_price": 3.58},
        "600558": {"prev_close": 10.0, "auction_price": 10.50},  # +5%
        "002696": {"prev_close": 10.0, "auction_price": 10.40},  # +4%
    }
    res = _call([_cand("002086", "东方海洋")], quotes, zt)
    assert res["views"]["002086"].get("lone_gap_gate") is None


def test_lone_gap_gate_requires_own_gap_above_3pct(monkeypatch):
    """自己不涨（≤3%）就不叫「独苗高开」，不拦。"""
    monkeypatch.setattr(PC, "POOL_CALL_HIGH_WIN_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_LEADER_ONLY", False)
    monkeypatch.setattr(PC, "POOL_CALL_DRAGON_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_BACKROW_GATE", False)
    zt = [row("002086", "东方海洋"), row("600558", "大西洋"), row("002696", "百洋股份")]
    quotes = {
        "002086": {"prev_close": 10.0, "auction_price": 10.20},  # +2%
        "600558": {"prev_close": 10.0, "auction_price": 9.00},
        "002696": {"prev_close": 10.0, "auction_price": 9.00},
    }
    res = _call([_cand("002086", "东方海洋")], quotes, zt)
    assert res["views"]["002086"].get("lone_gap_gate") is None


def test_lone_gap_gate_switch_off(monkeypatch):
    monkeypatch.setattr(PC, "POOL_CALL_HIGH_WIN_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_LEADER_ONLY", False)
    monkeypatch.setattr(PC, "POOL_CALL_DRAGON_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_BACKROW_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_LONE_GAP_GATE", False)
    zt = [row("002086", "东方海洋"), row("600558", "大西洋"), row("002696", "百洋股份")]
    quotes = {
        "002086": {"prev_close": 3.42, "auction_price": 3.58},
        "600558": {"prev_close": 10.0, "auction_price": 9.90},
        "002696": {"prev_close": 10.0, "auction_price": 9.30},
    }
    res = _call([_cand("002086", "东方海洋")], quotes, zt)
    assert res["views"]["002086"].get("lone_gap_gate") is None


def test_lone_gap_gate_skips_when_cluster_too_small(monkeypatch):
    """同族只有自己（无参照）→ 不裁决，⛔ 不能凭空判派发。"""
    monkeypatch.setattr(PC, "POOL_CALL_HIGH_WIN_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_LEADER_ONLY", False)
    monkeypatch.setattr(PC, "POOL_CALL_DRAGON_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_BACKROW_GATE", False)
    zt = [row("002086", "东方海洋", sector="农产品加")]
    quotes = {"002086": {"prev_close": 3.42, "auction_price": 3.58}}
    res = _call([_cand("002086", "东方海洋")], quotes, zt)
    assert res["views"]["002086"].get("lone_gap_gate") is None


def test_lone_gap_gate_skips_when_no_quotes(monkeypatch):
    """同族票没有竞价数据 → 进不了分母，不裁决（缺数据不是证据）。"""
    monkeypatch.setattr(PC, "POOL_CALL_HIGH_WIN_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_LEADER_ONLY", False)
    monkeypatch.setattr(PC, "POOL_CALL_DRAGON_GATE", False)
    monkeypatch.setattr(PC, "POOL_CALL_BACKROW_GATE", False)
    zt = [row("002086", "东方海洋"), row("600558", "大西洋"), row("002696", "百洋股份")]
    quotes = {"002086": {"prev_close": 3.42, "auction_price": 3.58}}   # 同族无报价
    res = _call([_cand("002086", "东方海洋")], quotes, zt)
    assert res["views"]["002086"].get("lone_gap_gate") is None
