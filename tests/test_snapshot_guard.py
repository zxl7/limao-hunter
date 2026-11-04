#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据源聚合层 · 守门 / 读侧门面 / 卡面纯注入与路由隐私回归。

用户原话：「开始按计划优化，数据单一原则等设计模式 灵活搭配」。

## 锁什么

1. **读写分离**：读侧统一经 `read_with_fallback`（缺档回退、行为逐字不变）。
2. **一致性守门**：validate 能抓「终裁买池外票」（硬矛盾）与「题材键两套口径」（软提示）；
   `write_health` 落 health section，⛔ 只报不改数。
3. **日常接线**：`snapshot_build --guard` 在输入输出均隔离的 root 下写 health。
4. **卡面纯注入**：仅渲染冻结注入，缺失不上卡，显式空不回填，不自行读盘。
5. **只读聚合 API**：不代表所有卡面已迁移；SHARE_PRIVATE=0 时整路由不可访问。
6. **死维度如实标注**：coverage 带 dead/unused；serve 路由标 retired。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE / "wealth_code" / "pipelines"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))

from core import snapshot as snap  # noqa: E402

D = "2026-11-04"


# ── ① 读侧统一门面（Adapter 回退）─────────────────────────────────────────
def test_read_fallback_prefers_snapshot(tmp_path):
    """快照有 → 用快照，⛔ 不去动 legacy loader（同一事实只读一处）。"""
    snap.put_section(D, "market", {"sentiment": 6.5}, root=tmp_path)
    called = []

    def legacy():
        called.append(1)
        return {"sentiment": 1.0}

    v, src = snap.read_with_fallback(D, "market", legacy, root=tmp_path)
    assert src == "snapshot" and v == {"sentiment": 6.5}
    assert called == []


def test_read_fallback_uses_legacy_when_missing(tmp_path):
    """快照缺档 → 回退原产物，行为逐字不变（渐进迁移的前提）。"""
    v, src = snap.read_with_fallback(D, "market", lambda: {"sentiment": 1.0}, root=tmp_path)
    assert src == "legacy" and v == {"sentiment": 1.0}


def test_read_fallback_missing_when_both_absent(tmp_path):
    """两边都没有 → missing + None，⛔ 不填默认值冒充有数据。"""
    v, src = snap.read_with_fallback(D, "market", root=tmp_path)
    assert src == "missing" and v is None


def test_read_fallback_legacy_exception_is_missing(tmp_path):
    """legacy loader 抛错 → 诚实降级，⛔ 不带崩读侧。"""
    def boom():
        raise OSError("no legacy file")

    v, src = snap.read_with_fallback(D, "market", boom, root=tmp_path)
    assert src == "missing" and v is None


# ── ② 守门留痕 health ─────────────────────────────────────────────────────
def test_write_health_persists_and_is_readable(tmp_path):
    snap.put_section(D, "candidates", {
        "stocks": {"600892": {"name": "大晟文化", "boards": 2}}}, root=tmp_path)
    snap.put_section(D, "verdict", {
        "views": {"600892": {"name": "大晟文化", "boards": 2}},
        "participate": []}, root=tmp_path)
    h = snap.write_health(D, root=tmp_path)
    assert h["ok"] is True
    assert h["coverage"]["present"] == 2
    assert h["coverage"]["total"] == len(snap.OWNERS)
    assert "lhb" in h["dimensions"]["dead"]
    assert "effect_paths" in h["dimensions"]["unused"]
    got = snap.health(D, root=tmp_path)
    assert got and got["checked_at"] == h["checked_at"]


def test_health_missing_is_none(tmp_path):
    """没跑过守门 → None，⛔ 不编造「已校验」。"""
    assert snap.health(D, root=tmp_path) is None


# ── ③ validate 增强（提升准确性，⛔ 不自动改数）────────────────────────────
def test_validate_catches_participate_not_in_candidates(tmp_path):
    """终裁买了池外票 = 硬矛盾（凭空开仓/串日串池），必须 ok=False。"""
    snap.put_section(D, "candidates", {
        "stocks": {"600892": {"name": "大晟文化", "boards": 2}}}, root=tmp_path)
    snap.put_section(D, "verdict", {"views": {}, "participate": ["999999"]}, root=tmp_path)
    r = snap.validate(D, root=tmp_path)
    assert r["ok"] is False
    assert any(i["kind"] == "participate_not_in_candidates" for i in r["issues"])


def test_validate_theme_key_mismatch_is_warning_not_failure(tmp_path):
    """11-04 传媒血案根因（候选按题材、风向标按行业）能被发现；
    但候选/lines 常跨交易日，故只作软提示，⛔ 不误伤 ok。"""
    snap.put_section(D, "candidates", {
        "stocks": {"600892": {"core_theme": "短剧/互动影游"}}}, root=tmp_path)
    snap.put_section(D, "lines", {"申万-游戏Ⅱ": {"code": "600892"}}, root=tmp_path)
    r = snap.validate(D, root=tmp_path)
    assert r["ok"] is True
    assert any(w["kind"] == "theme_key_mismatch" for w in r["warnings"])


def test_validate_no_warning_when_theme_keys_align(tmp_path):
    snap.put_section(D, "candidates", {
        "stocks": {"600892": {"core_theme": "短剧/互动影游"}}}, root=tmp_path)
    snap.put_section(D, "lines", {"短剧/互动影游": {"code": "600892"}}, root=tmp_path)
    r = snap.validate(D, root=tmp_path)
    assert r["warnings"] == []


def test_validate_consistent_has_no_new_issue(tmp_path):
    """正常一致数据不被新增检查误伤。"""
    snap.put_section(D, "candidates", {
        "stocks": {"600892": {"name": "大晟文化", "boards": 2,
                              "core_theme": "短剧/互动影游"}}}, root=tmp_path)
    snap.put_section(D, "verdict", {
        "views": {"600892": {"name": "大晟文化", "boards": 2}},
        "participate": ["600892"]}, root=tmp_path)
    snap.put_section(D, "lines", {"短剧/互动影游": {
        "code": "600892", "in_pool": True, "beacon": {"code": "600892"}}}, root=tmp_path)
    r = snap.validate(D, root=tmp_path)
    assert r["ok"] is True, r["issues"]


# ── ④ coverage：health 不进未知 section，维度状态可见 ──────────────────────
def test_coverage_excludes_health_from_extra_and_exposes_dimensions(tmp_path):
    snap.write_health(D, root=tmp_path)
    c = snap.coverage(D, root=tmp_path)
    assert "health" not in c["extra_sections"]
    assert isinstance(c["health"], dict)
    assert {"lhb", "board_core"} <= set(c["dimensions"]["dead"])
    assert "effect_paths" in c["dimensions"]["unused"]
    assert c["total"] == len(snap.OWNERS)      # health 不挤进 8/8 分母


# ── ⑤ 开关可逆 ───────────────────────────────────────────────────────────
def test_guard_switch_off(monkeypatch):
    monkeypatch.setenv(snap.GUARD_ENV, "0")
    assert snap.guard_enabled() is False


def test_guard_switch_default_on(monkeypatch):
    monkeypatch.delenv(snap.GUARD_ENV, raising=False)
    assert snap.guard_enabled() is True


# ── ⑥ 日常接线：snapshot_build --guard（隔离 root，不碰生产快照）────────────
@pytest.fixture
def isolated_snapshot_input(tmp_path, monkeypatch):
    import snapshot_build as sb

    # main 会临时覆写模块 ROOT；测试结束后恢复，避免污染后续用例。
    monkeypatch.setattr(sb, "ROOT", sb.ROOT)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "feedback_matrix.json").write_text(
        json.dumps({"date": D, "sentiment_score": 6.5}), encoding="utf-8")
    return tmp_path


def test_snapshot_build_guard_writes_health_in_isolated_root(isolated_snapshot_input):
    import snapshot_build as sb
    tmp_path = isolated_snapshot_input
    rc = sb.main(["--date", D, "--only", "market", "--guard", "--root", str(tmp_path)])
    assert rc == 0
    p = tmp_path / "cache" / "snapshot" / f"{D}.json"
    assert p.exists()
    d = json.loads(p.read_text(encoding="utf-8"))
    secs = d.get("sections") or {}
    assert set(secs) == {"market", "health"}
    assert secs["market"]["payload"]["sentiment_score"] == 6.5
    assert secs["health"]["payload"]["schema"] == snap.SCHEMA


def test_snapshot_build_check_does_not_write(isolated_snapshot_input, capsys):
    """dry-run 使用临时输入且不落盘。"""
    import snapshot_build as sb
    tmp_path = isolated_snapshot_input
    rc = sb.main(["--date", D, "--only", "market", "--check", "--root", str(tmp_path)])
    assert "market" in capsys.readouterr().out
    assert rc == 0
    assert not (tmp_path / "cache" / "snapshot" / f"{D}.json").exists()


# ── ⑦ 卡面纯注入（缺失不显示，显式空不回填）─────────────────────────────
@pytest.fixture
def beacon_without_disk(tmp_path, monkeypatch):
    import builtins
    import build_cards as bc

    snap.put_section(D, "verdict", {"anchor_lines": {
        "新能源": {"name": "不应回填的票", "boards": 2, "attitude": "weak"},
    }}, root=tmp_path)
    monkeypatch.setattr(snap, "ROOT", tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("风向标渲染不得读取磁盘或快照")

    monkeypatch.setattr(snap, "read_with_fallback", forbidden)
    monkeypatch.setattr(snap, "read_section", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    return bc


def test_beacon_only_renders_injection(beacon_without_disk):
    injected = {"defense": {"anchor": {"lines": {
        "大消费": {"name": "东方海洋", "boards": 4, "attitude": "strong",
                   "label": "强开", "gap": 4.68},
    }}}}
    txt = json.dumps(beacon_without_disk._beacon_block(injected), ensure_ascii=False)
    assert "东方海洋" in txt and "不应回填的票" not in txt


@pytest.mark.parametrize("pool_meta", [
    None, {}, {"defense": {}}, {"defense": {"anchor": {}}},
])
def test_beacon_missing_injection_does_not_read_snapshot(beacon_without_disk, pool_meta):
    assert beacon_without_disk._beacon_block(pool_meta) == []


@pytest.mark.parametrize("lines", [{}, None, []])
def test_beacon_explicit_empty_does_not_refill(beacon_without_disk, lines):
    injected = {"defense": {"anchor": {"lines": lines}}}
    assert beacon_without_disk._beacon_block(injected) == []


def test_beacon_no_data_no_block():
    import build_cards as bc
    assert bc._beacon_block(None) == []


# ── ⑧ 展现层单一入口 + 死维度如实标注 ────────────────────────────────────
def test_serve_routes_expose_snapshot_and_mark_dead_dims():
    import serve
    defn = serve.PUBLIC_ROUTES.get("/api/data/market-snapshot")
    assert defn and defn["dir"] == "cache/snapshot"
    for r in ("/api/board/core", "/api/lhb/tencent", "/api/lhb/hithink"):
        v = serve.PUBLIC_ROUTES[r]
        assert v.get("retired") is True, r
        assert v.get("cadence") == "manual", r


@pytest.mark.parametrize("suffix", ["", "?t=valid-token", "?date=2026-11-04"])
def test_snapshot_private_off_rejects_before_reading(monkeypatch, suffix):
    import serve

    route = "/api/data/market-snapshot"
    assert route.startswith(serve.PRIVATE_PREFIXES)
    monkeypatch.setattr(serve, "SHARE_PRIVATE", False)
    monkeypatch.setattr(serve, "write_access", lambda *args: None)

    def forbidden(*args, **kwargs):
        pytest.fail("隐私关闭时不得查询数据、读取快照或返回缓存命中")

    monkeypatch.setattr(serve, "read_data_query", forbidden)
    monkeypatch.setattr(serve, "read_route", forbidden)
    handler = object.__new__(serve.Handler)
    handler.path = route + suffix
    handler.client_address = ("127.0.0.1", 12345)
    handler.headers = {"If-None-Match": '"cached-snapshot"'}
    monkeypatch.setattr(handler, "_auth_ok", lambda query: (True, True))
    monkeypatch.setattr(handler, "_route_etag", forbidden)
    responses = []
    monkeypatch.setattr(handler, "_send_json", lambda status, payload, **kw: responses.append((status, payload)))
    handler.do_GET()
    assert len(responses) == 1
    status, payload = responses[0]
    assert status == 404
    assert payload["error"] == "not_found"
    assert "sections" not in payload
    assert "verdict" not in json.dumps(payload)


def test_snapshot_private_on_reads_aggregate_api(tmp_path, monkeypatch):
    import serve

    snap.put_section(D, "verdict", {"participate": ["600892"]}, root=tmp_path)
    monkeypatch.setattr(serve, "SHARE_PRIVATE", True)
    monkeypatch.setattr(serve, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(serve, "write_access", lambda *args: None)
    handler = object.__new__(serve.Handler)
    handler.path = "/api/data/market-snapshot"
    handler.client_address = ("127.0.0.1", 12345)
    handler.headers = {}
    monkeypatch.setattr(handler, "_auth_ok", lambda query: (True, True))
    monkeypatch.setattr(handler, "_route_etag", lambda defn: None)
    responses = []
    monkeypatch.setattr(handler, "_send_json", lambda status, payload, **kw: responses.append((status, payload)))
    handler.do_GET()
    assert len(responses) == 1
    status, payload = responses[0]
    assert status == 200
    assert payload["sections"]["verdict"]["payload"]["participate"] == ["600892"]
