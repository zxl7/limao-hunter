#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日市场快照 · 数据源聚合层回归（2026-11-04 用户架构要求）。

用户原话：「数据的展现是可以分散的，但数据源一定是聚合的。
          这样的话，我们对市场的判断才会更清晰、更准确。」

## 锁什么

1. **分区写入不互相覆盖** —— 各链路能并行写同一文件的前提。
   这是整个架构的命门：若 put_section 变成整文件覆盖，
   后写的链路会把先写的维度全抹掉，比不做聚合更危险。
2. **原子写** —— 不留半截 JSON（下游永远读不到截断文件）。
3. **单票聚合视图** —— 把散在多处的事实合成一条，且标注来源。
4. **一致性校验能真的抓到矛盾** —— 这是「提升准确性」的落点。
5. **开关可逆** —— `ENABLE_MARKET_SNAPSHOT=0` 时写盘变 no-op，全系统回旧行为。
6. **诚实降级** —— 缺就是缺，⛔ 不填默认值冒充有数据。
"""
from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE))

from core import snapshot as snap  # noqa: E402

D = "2026-11-04"


# ── ① 分区写入：命门 ─────────────────────────────────────────────────────
def test_sections_do_not_overwrite_each_other(tmp_path):
    """⛔ 架构命门：写 A 再写 B，A 必须还在。"""
    assert snap.put_section(D, "market", {"sentiment": 6.5}, root=tmp_path)
    assert snap.put_section(D, "lines", {"短剧": {"name": "大晟文化"}}, root=tmp_path)
    assert snap.read_section(D, "market", root=tmp_path) == {"sentiment": 6.5}
    assert snap.read_section(D, "lines", root=tmp_path) == {"短剧": {"name": "大晟文化"}}


def test_same_section_upsert_replaces_payload(tmp_path):
    """同一 section 重写 → 覆盖自己，不动别人。"""
    snap.put_section(D, "market", {"v": 1}, root=tmp_path)
    snap.put_section(D, "gates", {"g": 1}, root=tmp_path)
    snap.put_section(D, "market", {"v": 2}, root=tmp_path)
    assert snap.read_section(D, "market", root=tmp_path) == {"v": 2}
    assert snap.read_section(D, "gates", root=tmp_path) == {"g": 1}


def test_section_carries_provenance(tmp_path):
    """每个 section 必须能回答「谁写的、几点写的」——可追溯是聚合的前提。"""
    snap.put_section(D, "verdict", {"participate": ["605577"]}, root=tmp_path)
    meta = snap.read_meta(D, "verdict", root=tmp_path)
    assert meta and meta["ingested_at"] and meta["owner"]
    assert meta["as_of"] is None
    assert meta["source_date"] == D


# ── ② 原子写 ─────────────────────────────────────────────────────────────
def test_write_leaves_no_partial_file(tmp_path):
    """写完后目录里只有正式文件，不留 .tmp_ 残片。"""
    snap.put_section(D, "market", {"sentiment": 1.6}, root=tmp_path)
    d = tmp_path / "cache" / "snapshot"
    files = [p.name for p in d.iterdir()]
    assert set(files) == {f"{D}.json", f"{D}.lock"}, files
    # 内容必须能正常解析
    json.loads((d / f"{D}.json").read_text(encoding="utf-8"))


# ── ③ 诚实降级 ───────────────────────────────────────────────────────────
def test_missing_section_returns_none(tmp_path):
    """⛔ 没写过就是 None，不返回 {} 或默认值冒充有数据。"""
    assert snap.read_section(D, "market", root=tmp_path) is None
    assert snap.read_meta(D, "market", root=tmp_path) is None
    assert snap.stock_view(D, "600892", root=tmp_path) is None


def test_corrupt_file_degrades_to_empty(tmp_path):
    """文件损坏 → 聚合失效但不崩（聚合是增强，不能带崩主链）。"""
    d = tmp_path / "cache" / "snapshot"
    d.mkdir(parents=True)
    (d / f"{D}.json").write_text("{不是合法json", encoding="utf-8")
    assert snap.read_section(D, "market", root=tmp_path) is None
    assert snap.put_section(D, "market", {"v": 1}, root=tmp_path) is True
    assert snap.read_section(D, "market", root=tmp_path) == {"v": 1}


# ── ④ 开关可逆 ───────────────────────────────────────────────────────────
def test_switch_off_makes_write_noop(tmp_path, monkeypatch):
    monkeypatch.setenv(snap.ENABLED_ENV, "0")
    assert snap.put_section(D, "market", {"v": 1}, root=tmp_path) is False
    assert not (tmp_path / "cache" / "snapshot" / f"{D}.json").exists()


def test_switch_default_on():
    assert snap.enabled() is True


# ── ⑤ 单票聚合视图 ───────────────────────────────────────────────────────
def _seed_full(tmp_path):
    snap.put_section(D, "candidates", {"stocks": {
        "600892": {"name": "大晟文化", "boards": 2, "theme": "短剧/互动影游",
                   "is_one_word": True, "dragon_rank": 1},
    }}, root=tmp_path)
    snap.put_section(D, "lines", {
        "短剧/互动影游": {"code": "600892", "name": "大晟文化", "boards": 2,
                          "in_pool": True, "beacon": {"code": "600892"}},
    }, root=tmp_path)
    snap.put_section(D, "verdict", {"views": {
        "600892": {"view": "skip", "reason": "高开 9.98% 超 5% 上限"},
    }}, root=tmp_path)
    snap.put_section(D, "quotes", {"600892": {"auction_price": 5.29,
                                              "prev_close": 4.81}}, root=tmp_path)


def test_stock_view_merges_all_sources(tmp_path):
    """一条命令读全：候选 + 风向标身份 + 裁决 + 竞价。"""
    _seed_full(tmp_path)
    v = snap.stock_view(D, "600892", root=tmp_path)
    assert v is not None
    assert set(v["sources"]) == {"candidate", "beacon_of", "verdict", "auction"}
    assert v["parts"]["candidate"]["boards"] == 2
    assert v["parts"]["beacon_of"]["theme"] == "短剧/互动影游"
    assert v["parts"]["verdict"]["view"] == "skip"
    assert v["parts"]["auction"]["auction_price"] == 5.29


def test_stock_view_candidate_only(tmp_path):
    """只有候选、没有其它 section → 仍返回已有部分（⛔ 不因缺块就返回 None）。"""
    snap.put_section(D, "candidates", {"stocks": {"600892": {"name": "大晟文化"}}},
                     root=tmp_path)
    v = snap.stock_view(D, "600892", root=tmp_path)
    assert v["sources"] == ["candidate"]


# ── ⑥ 一致性校验：真正的准确性抓手 ────────────────────────────────────────
def test_validate_catches_fact_mismatch(tmp_path):
    """同一事实两处不同值 → 必须报出来。11-04 传媒线就是死在没有这种检查。"""
    snap.put_section(D, "candidates", {"stocks": {
        "600892": {"name": "大晟文化", "boards": 2}}}, root=tmp_path)
    snap.put_section(D, "verdict", {"views": {
        "600892": {"name": "大晟文化", "boards": 3}}}, root=tmp_path)
    r = snap.validate(D, root=tmp_path)
    assert r["ok"] is False
    kinds = {i["kind"] for i in r["issues"]}
    assert "fact_mismatch" in kinds
    bad = [i for i in r["issues"] if i["kind"] == "fact_mismatch"][0]
    assert bad["field"] == "boards" and bad["candidates"] == 2 and bad["verdict"] == 3


def test_validate_passes_when_consistent(tmp_path):
    _seed_full(tmp_path)
    r = snap.validate(D, root=tmp_path)
    assert r["ok"] is True, r["issues"]


def test_validate_catches_broken_beacon(tmp_path):
    """标了 in_pool 却没有 beacon → 风向标指针断裂，必须报。"""
    snap.put_section(D, "lines", {
        "短剧/互动影游": {"code": "600892", "in_pool": True, "beacon": None},
    }, root=tmp_path)
    r = snap.validate(D, root=tmp_path)
    assert any(i["kind"] == "beacon_missing" for i in r["issues"])


def test_validate_flags_stale_section(tmp_path):
    """section 的 as_of 不是当日 → 报陈旧（防止拿昨天的裁决当今天）。"""
    d = tmp_path / "cache" / "snapshot"
    d.mkdir(parents=True)
    (d / f"{D}.json").write_text(json.dumps({
        "schema": snap.SCHEMA, "date": D,
        "sections": {"verdict": {"as_of": "2026-11-03T09:25:00+08:00",
                                 "source": "test", "payload": {}}},
    }, ensure_ascii=False), encoding="utf-8")
    r = snap.validate(D, root=tmp_path)
    assert any(i["kind"] == "stale_section" for i in r["issues"])


# ── ⑦ 候选结论层压缩 ─────────────────────────────────────────────────────
def test_candidate_section_trims_to_conclusions():
    """119 字段砍到判断所需；⛔ 丢掉打分中间量与指纹。"""
    cand = {"date": D, "regime": "ice", "market_max_boards": 4,
            "relay": [], "watch": [{
                "code": "600892", "name": "大晟文化", "boards": 2,
                "theme": "短剧/互动影游", "is_one_word": True, "hs": 1.26,
                "zbc": 0, "seal_amt": 2.49, "dragon_rank": 1,
                "score": 75.4,
                # ↓ 这些应当被丢掉
                "theme_mapping_fingerprint": "abc123", "quant_dims": {"x": 1},
                "preselect_evidence": {"big": "blob"}, "qr_mirror": {"y": 2},
            }]}
    sec = snap.build_candidate_section(cand)
    row = sec["stocks"]["600892"]
    assert row["name"] == "大晟文化" and row["boards"] == 2
    assert row["is_one_word"] is True and row["pool"] == "watch"
    for junk in ("theme_mapping_fingerprint", "quant_dims",
                 "preselect_evidence", "qr_mirror"):
        assert junk not in row, f"{junk} 应被裁掉"


def test_candidate_section_skips_bad_codes():
    cand = {"watch": [{"code": "60089", "name": "半截代码"},
                      {"code": "", "name": "空"},
                      {"code": "600892", "name": "正常"}]}
    sec = snap.build_candidate_section(cand)
    assert list(sec["stocks"]) == ["600892"]


# ── ⑧ 覆盖度 ─────────────────────────────────────────────────────────────
def test_coverage_reports_missing_sections(tmp_path):
    snap.put_section(D, "market", {"v": 1}, root=tmp_path)
    c = snap.coverage(D, root=tmp_path)
    assert c["present"] == 1
    assert c["sections"]["market"]["present"] is True
    assert c["sections"]["verdict"]["present"] is False
    assert c["total"] == len(snap.OWNERS)


def _parallel_writer(root, index, barrier):
    original = snap._atomic_write

    def slow_write(path, data):
        # 扩大读后写前的竞争窗口，缺事务锁时必然丢失 section。
        import time
        time.sleep(0.02)
        return original(path, data)

    snap._atomic_write = slow_write
    barrier.wait(timeout=20)
    for turn in range(3):
        assert snap.put_section(D, f"worker_{index}", {"turn": turn}, root=Path(root))


def test_multiprocess_writes_preserve_all_sections_and_revision(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(6)
    workers = [ctx.Process(target=_parallel_writer, args=(str(tmp_path), i, barrier))
               for i in range(6)]
    for worker in workers:
        worker.start()
    try:
        for worker in workers:
            worker.join(timeout=30)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
    data = snap._read_all(D, tmp_path)
    assert data["revision"] == 18
    assert len(data["sections"]) == 6
    assert all(sec["payload"] == {"turn": 2} for sec in data["sections"].values())


def test_lock_file_inode_is_stable_across_replaces(tmp_path):
    assert snap.put_section(D, "market", {}, root=tmp_path)
    lock = snap.snapshot_path(D, tmp_path).with_suffix(".lock")
    inode = lock.stat().st_ino
    assert snap.put_section(D, "lines", {}, root=tmp_path)
    assert lock.stat().st_ino == inode


def test_health_becomes_stale_after_business_write(tmp_path):
    snap.put_section(D, "market", {}, root=tmp_path)
    first = snap.write_health(D, root=tmp_path)
    assert first["revision"] == 1 and first["ok"] is True
    assert snap.health(D, root=tmp_path)["stale"] is False
    snap.put_section(D, "quotes", {}, root=tmp_path)
    for result in (snap.health(D, root=tmp_path),
                   snap.coverage(D, root=tmp_path)["health"],
                   snap.read_section(D, "health", root=tmp_path)):
        assert result["stale"] is True
        assert result["ok"] is None
    refreshed = snap.write_health(D, root=tmp_path)
    assert refreshed["revision"] == 2
    assert refreshed["coverage"]["present"] == 2
    assert snap.health(D, root=tmp_path)["stale"] is False
    assert snap._read_all(D, tmp_path)["revision"] == 2


def test_legacy_health_without_revision_is_stale(tmp_path):
    snap.put_section(D, "health", {"ok": True}, root=tmp_path)
    assert snap.health(D, root=tmp_path)["stale"] is True
    assert snap.coverage(D, root=tmp_path)["health"]["ok"] is None


def test_write_health_reads_once_under_lock(tmp_path, monkeypatch):
    snap.put_section(D, "market", {}, root=tmp_path)
    original_read = snap._read_all
    original_lock = snap._write_lock
    locked = False
    reads = 0
    from contextlib import contextmanager

    @contextmanager
    def checked_lock(*args):
        nonlocal locked
        assert not locked, "禁止嵌套锁"
        with original_lock(*args):
            locked = True
            try:
                yield
            finally:
                locked = False

    def checked_read(*args):
        nonlocal reads
        assert locked
        reads += 1
        return original_read(*args)

    def forbidden_put(*args, **kwargs):
        pytest.fail("write_health 不应再次调用 put_section 获取锁")

    monkeypatch.setattr(snap, "_write_lock", checked_lock)
    monkeypatch.setattr(snap, "_read_all", checked_read)
    monkeypatch.setattr(snap, "put_section", forbidden_put)
    result = snap.write_health(D, root=tmp_path)
    assert reads == 1
    assert result["checked"]["sections_present"] == result["coverage"]["present"] == 1


@pytest.mark.parametrize("candidates", [None, {"stocks": {}}])
def test_participate_requires_candidates_even_when_empty(tmp_path, candidates):
    if candidates is not None:
        snap.put_section(D, "candidates", candidates, root=tmp_path)
    snap.put_section(D, "verdict", {"participate": ["600892"]}, root=tmp_path)
    result = snap.validate(D, root=tmp_path)
    assert result["ok"] is False
    assert any(i["kind"] == "participate_not_in_candidates" for i in result["issues"])


def test_historical_backfill_separates_source_and_ingestion(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "_now", lambda: "2026-11-04T15:00:00+08:00")
    historical = "2026-10-30"
    for section in ("quotes", "verdict"):
        assert snap.put_section(historical, section, {}, root=tmp_path,
                                source_date=historical,
                                as_of="2026-10-30T09:25:00+08:00",
                                ingested_at="2026-11-04T14:00:00+08:00")
    meta = snap.read_meta(historical, "quotes", root=tmp_path)
    assert meta["source_date"] == historical
    assert meta["as_of"].startswith(historical)
    assert meta["ingested_at"] == "2026-11-04T14:00:00+08:00"
    assert snap.validate(historical, root=tmp_path)["ok"] is True
    assert snap.put_section(historical, "quotes", {}, root=tmp_path)
    meta = snap.read_meta(historical, "quotes", root=tmp_path)
    assert meta["as_of"] is None
    assert meta["ingested_at"].startswith(D)
    assert snap.validate(historical, root=tmp_path)["ok"] is True


@pytest.mark.parametrize("source_date,as_of,stale", [
    (D, "2026-11-03T09:25:00+08:00", False),
    ("2026-11-03", f"{D}T09:25:00+08:00", True),
    (None, "2026-11-03T09:25:00+08:00", True),
])
def test_validate_prefers_explicit_source_date(tmp_path, source_date, as_of, stale):
    snap.put_section(D, "quotes", {}, root=tmp_path,
                     source_date=source_date, as_of=as_of)
    result = snap.validate(D, root=tmp_path)
    assert any(i["kind"] == "stale_section" for i in result["issues"]) is stale


def test_fallback_switch_and_missing_legacy(tmp_path, monkeypatch):
    snap.put_section(D, "market", {"new": True}, root=tmp_path)
    assert snap.read_with_fallback(D, "market", root=tmp_path)[1] == "snapshot"
    monkeypatch.setenv(snap.ENABLED_ENV, "0")
    assert snap.read_with_fallback(D, "market", lambda: {}, root=tmp_path) == ({}, "legacy")
    assert snap.read_with_fallback(D, "market", lambda: None, root=tmp_path) == (None, "missing")
    assert snap.read_with_fallback(D, "market", root=tmp_path) == (None, "missing")


def test_cli_coverage_displays_stale_health(tmp_path, monkeypatch, capsys):
    snap.write_health(D, root=tmp_path)
    snap.put_section(D, "market", {}, root=tmp_path)
    monkeypatch.setattr(snap, "HUNTER_ROOT", tmp_path)
    assert snap._cli(["coverage", "--date", D]) == 0
    output = capsys.readouterr().out
    assert "已过期，需重新校验" in output
    assert "守门 一致" not in output


def test_dimension_ledger_has_no_future_reference():
    assert "11-17" not in json.dumps(snap.DIMENSION_STATUS, ensure_ascii=False)
