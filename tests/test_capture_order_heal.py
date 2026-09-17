#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘后链时序缺陷根治守卫（2026-09-15 立）。

事故（09-04 / 09-07 / 09-11 三日实证，09-14 复核）：
  盘后链里 `capture_daily` 若早于 `record_zt_snapshot` 执行，
  `cache/yesterday_zt.json` 仍是**前一交易日**的 → 双重损坏：
    ① 当日 `zt_history/zt_{今日}.json` 归档缺失 → 形态/N字/回流检测**永久少一天料**；
    ② 反而把**前一交易日**的归档用旧数据重写。

根治（脚本级·与 automation 调用次序解耦）：`capture_daily.capture_zt()` 写盘前先调
`_ensure_pool_fresh()`——发现口径日 ≠ 今日即主动跑一次上游 `record_zt_snapshot`（幂等）。

本测试锁死四个行为（全部离线·不打网络）：
  · 口径日已是今日 → "ok"（顺序正确时零开销、不调上游）；
  · **收盘前（<15:00）不主动补齐**（原本这就是正常态，误跑会抓盘中不完整池=污染）；
  · 收盘后口径日陈旧 → 调上游一次；成功回读到今日 → "filled"；
  · 上游失败 → "fill-failed" **不抛异常**（主流程继续，由 _heal_residual_archives 兜底）。
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("cdmod", ROOT / "scripts" / "capture_daily.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()


def _seed(tmp_path, date, monkeypatch):
    """把模块的 CACHE 指向临时目录并写入一份 yesterday_zt.json，返回该路径。"""
    monkeypatch.setattr(M, "CACHE", tmp_path)
    p = tmp_path / "yesterday_zt.json"
    p.write_text(json.dumps({"date": date, "zt_list": [{"code": "000001.SZ"}],
                             "zt_count": 1}), encoding="utf-8")
    return p


# ── ① 顺序正确：口径日已是今日 → 零开销放行 ──────────────────────────────
def test_ok_when_pool_already_today(tmp_path, monkeypatch):
    _seed(tmp_path, "2026-09-15", monkeypatch)
    called = []
    monkeypatch.setattr(M.subprocess, "run", lambda *a, **k: called.append(a))
    assert M._ensure_pool_fresh("2026-09-15", now_hour=17) == "ok"
    assert called == [], "口径日正确时不应调用上游（防无谓抓取）"


# ── ② 收盘前不主动补齐（盘中口径陈旧=正常态·禁止污染）──────────────────
def test_before_close_never_calls_upstream(tmp_path, monkeypatch):
    _seed(tmp_path, "2026-09-14", monkeypatch)
    called = []
    monkeypatch.setattr(M.subprocess, "run", lambda *a, **k: called.append(a))
    for h in (9, 10, 11, 13, 14):
        r = M._ensure_pool_fresh("2026-09-15", now_hour=h)
        assert r.startswith("stale-before-close"), (h, r)
    assert called == [], "收盘前绝不可调上游（会抓到盘中不完整涨停池）"


# ── ③ 收盘后口径陈旧 → 调上游一次并回读确认 ─────────────────────────────
def test_after_close_fills_from_upstream(tmp_path, monkeypatch):
    p = _seed(tmp_path, "2026-09-14", monkeypatch)
    calls = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*a, **k):
        calls.append(a)
        # 模拟上游 record_zt_snapshot 补数成功：口径日推进到今日
        p.write_text(json.dumps({"date": "2026-09-15", "zt_list": [{"code": "000001.SZ"}]}),
                     encoding="utf-8")
        return _R()

    monkeypatch.setattr(M.subprocess, "run", fake_run)
    r = M._ensure_pool_fresh("2026-09-15", now_hour=17)
    assert r.startswith("filled(cur=2026-09-14→2026-09-15)"), r
    assert len(calls) == 1, "幂等：只补一次"


# ── ④ 上游失败必须被降级为描述串（不抛异常·不阻断主流程）────────────────
def test_upstream_failure_is_reported_not_raised(tmp_path, monkeypatch):
    _seed(tmp_path, "2026-09-14", monkeypatch)

    class _R:
        returncode = 1
        stdout = "boom"
        stderr = "Error: biying token missing"

    monkeypatch.setattr(M.subprocess, "run", lambda *a, **k: _R())
    r = M._ensure_pool_fresh("2026-09-15", now_hour=17)
    assert r.startswith("fill-failed"), r
    assert "rc=1" in r


def test_upstream_exception_is_swallowed(tmp_path, monkeypatch):
    _seed(tmp_path, "2026-09-14", monkeypatch)

    def boom(*a, **k):
        raise TimeoutError("180s timeout")

    monkeypatch.setattr(M.subprocess, "run", boom)
    r = M._ensure_pool_fresh("2026-09-15", now_hour=17)
    assert r.startswith("fill-err"), r


def test_missing_upstream_script_is_reported(tmp_path, monkeypatch):
    _seed(tmp_path, "2026-09-14", monkeypatch)
    monkeypatch.setattr(M, "ROOT", tmp_path)          # 临时无 scripts/record_zt_snapshot.py
    r = M._ensure_pool_fresh("2026-09-15", now_hour=17)
    assert r.startswith("skip-no-upstream"), r


# ── ⑤ 接线断言：判定必须被 capture_zt 真的调用（防"定义了却没用"）────────
def test_wired_into_capture_zt():
    src = (ROOT / "scripts" / "capture_daily.py").read_text(encoding="utf-8")
    assert "_fill = _ensure_pool_fresh(_today())" in src
    # ⛔ 15:00 门槛必须留在生产代码里（既有保护不可被误删）
    assert "if hour < 15:" in src
    # 盘后仍未拿到今日池时必须显式告警（勿静默让归档缺料）
    assert "盘后未拿到今日涨停池" in src
