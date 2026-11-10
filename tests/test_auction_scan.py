#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备选池竞价扫描（`watch_pool.scan`）回归。

2026-11-09 用户：「让备选池中的全部股票明天都按同一套竞价逻辑进行观察筛选……
对备选池内每只股票逐一应用该逻辑，并输出可供交易员参考的观察结果与判断依据，
便于交易员据此比较并识别其中表现最好的标的。竞价是基于原有选股逻辑上的进一步
维度优化」。

这里守四件事：
1. **同一套参数**：所有票走 `params()` 这一个源，⛔ 各模块再写一遍阈值；
2. **09:20 起算**：`fade` 的高点只能用不可撤单期（09:20 后）的高点，
   ⛔ 拿 09:15 首笔虚顶当高点（会把撤单误判成砸盘）；
3. **判断依据可复核**：每票 `basis` 必须带数字与时点，⛔ 空口结论；
4. **⛔ 只观察**：不落盘、不进名额/仓位/买入/评分/裁决，`pending`/`no_data` 不排名。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "wealth_code")):
    if p not in sys.path:
        sys.path.insert(0, p)

from common import watch_pool as wp  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────
def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _pool_call(root: Path, date: str, rows: dict) -> None:
    _write(root / wp.POOL_DIR / f"pool_call-{date}.json",
           {"trade_date": date, "ts": f"{date}T09:25:06+08:00", "views": rows})


def _heart(root: Path, date: str, rounds: list[tuple[str, list[dict]]]) -> None:
    path = root / wp.SNAP_DIR / f"{date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ts, rows in rounds:
            fh.write(json.dumps({"ts": ts, "rows": rows}, ensure_ascii=False) + "\n")


def _row(code: str, pct: float, name: str = "票") -> dict:
    return {"code": code, "name": name, "pct": pct, "price": 10.0}


def _ts(date: str, hhmm: str) -> str:
    return f"{date}T{hhmm}:05+08:00"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """⛔ 不许回落到真实观测表（否则测试会读生产库、结果不稳定）。"""
    monkeypatch.setattr(wp, "_rounds_from_registry", lambda *a, **k: [])


# ── 参数单一源 ────────────────────────────────────────────────────────
def test_params_defaults():
    p = wp.params()
    assert p["window_start"] == "09:15" and p["window_end"] == "09:30"
    assert p["confirm"] == "09:25"
    assert p["reversal_deep"] == -5.0 and p["reversal_red"] == 0.0
    assert p["fade_start"] == "09:20"
    assert p["fade_high"] == 3.0 and p["fade_drop"] == -3.0


def test_params_env_override(monkeypatch):
    monkeypatch.setenv("WP_FADE_DROP", "-5")
    monkeypatch.setenv("WP_FADE_HIGH", "4.5")
    monkeypatch.setenv("WP_FADE_START", "09:22")
    p = wp.params()
    assert p["fade_drop"] == -5.0 and p["fade_high"] == 4.5
    assert p["fade_start"] == "09:22"


def test_params_bad_env_falls_back(monkeypatch):
    """坏值回落默认值，⛔ 不静默变成 0（0 会让判据全放行）。"""
    monkeypatch.setenv("WP_FADE_DROP", "abc")
    monkeypatch.setenv("WP_REVERSAL_DEEP", "")
    p = wp.params()
    assert p["fade_drop"] == -3.0
    assert p["reversal_deep"] == -5.0


# ── fade：转弱判定 ────────────────────────────────────────────────────
def test_fade_detected_from_0920_high(tmp_path):
    """09:20 高 +9.93 → 末 +5.50：从不可撤单期高点回撤 4.43pp → fade。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"603381": {"name": "永臻股份"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:15"), [_row("603381", 9.98)]),
        (_ts(d, "09:22"), [_row("603381", 9.93)]),
        (_ts(d, "09:24"), [_row("603381", 5.50)]),
        (_ts(d, "09:26"), [_row("603381", 5.50)]),
    ])
    it = wp.auction_paths(root=tmp_path, date=d)["items"][0]
    assert it["state"] == "fade"
    assert it["high_pct"] == 9.93 and it["high_ts"] == "09:22"
    assert it["drop_pp"] == pytest.approx(-4.43)


def test_fade_ignores_0915_virtual_top(tmp_path):
    """关键回归：09:15 首笔 +19.81 是撤单虚顶，高点必须取 09:20 后的 +9.29。

    若把首笔当高点，回撤会算成 −13.53pp（假），实际只有 −3.01pp。
    """
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"301697": {"name": "贝特利"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:15"), [_row("301697", 19.81)]),
        (_ts(d, "09:20"), [_row("301697", 9.29)]),
        (_ts(d, "09:23"), [_row("301697", 6.28)]),
        (_ts(d, "09:26"), [_row("301697", 6.28)]),
    ])
    it = wp.auction_paths(root=tmp_path, date=d)["items"][0]
    assert it["high_pct"] == 9.29
    assert it["drop_pp"] == pytest.approx(-3.01)
    assert it["state"] == "fade"


def test_virtual_top_drop_is_not_fade(tmp_path):
    """09:15 虚顶掉了 4.44pp，但 09:20 后没再跌 → 不是 fade，标「虚顶回落」。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:15"), [_row("600261", 10.00)]),
        (_ts(d, "09:20"), [_row("600261", 5.56)]),
        (_ts(d, "09:26"), [_row("600261", 5.56)]),
    ])
    it = wp.auction_paths(root=tmp_path, date=d)["items"][0]
    assert it["state"] == "flat"
    assert it["drop_pp"] == pytest.approx(0.0)
    assert any("虚顶回落" in f for f in it["flags"])


def test_reversal_wins_over_fade(tmp_path):
    """深水转红优先：同一只票既满足深水转红、又从高点回落，先算 reversal。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:15"), [_row("600261", -9.80)]),
        (_ts(d, "09:20"), [_row("600261", 9.00)]),
        (_ts(d, "09:24"), [_row("600261", 2.00)]),
        (_ts(d, "09:26"), [_row("600261", 1.50)]),
    ])
    it = wp.auction_paths(root=tmp_path, date=d)["items"][0]
    assert it["state"] == "reversal"


def test_fade_threshold_env_can_disable(tmp_path, monkeypatch):
    """把转弱线放宽到 −6pp，同一只票就不再算 fade（参数真的生效）。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"603381": {"name": "永臻股份"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:22"), [_row("603381", 9.93)]),
        (_ts(d, "09:24"), [_row("603381", 5.50)]),
        (_ts(d, "09:26"), [_row("603381", 5.50)]),
    ])
    assert wp.auction_paths(root=tmp_path, date=d)["items"][0]["state"] == "fade"
    monkeypatch.setenv("WP_FADE_DROP", "-6")
    assert wp.auction_paths(root=tmp_path, date=d)["items"][0]["state"] == "flat"


def test_counts_include_fade(tmp_path):
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"603381": {"name": "永臻股份"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:22"), [_row("603381", 9.93)]),
        (_ts(d, "09:26"), [_row("603381", 5.50)]),
    ])
    counts = wp.auction_paths(root=tmp_path, date=d)["counts"]
    assert counts["fade"] == 1 and counts["reversal"] == 0


# ── 档位 / 排序 / 依据 ────────────────────────────────────────────────
def test_grade_order_and_rank(tmp_path):
    """S > A > B > C > D；同档按末条涨幅降序；N 不排名。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {
        "000001": {"name": "转红"}, "000002": {"name": "走强"},
        "000003": {"name": "平稳"}, "000004": {"name": "转弱"},
        "000005": {"name": "未抓到"},
    })
    _heart(tmp_path, d, [
        (_ts(d, "09:15"), [_row("000001", -9.9), _row("000002", 9.9),
                           _row("000003", 2.0), _row("000004", 9.9)]),
        (_ts(d, "09:20"), [_row("000001", -6.0), _row("000002", 9.8),
                           _row("000003", 2.0), _row("000004", 9.9)]),
        (_ts(d, "09:24"), [_row("000001", 2.5), _row("000002", 9.6),
                           _row("000003", 2.1), _row("000004", 5.0)]),
        (_ts(d, "09:26"), [_row("000001", 2.5), _row("000002", 9.6),
                           _row("000003", 2.1), _row("000004", 5.0)]),
    ])
    res = wp.scan(root=tmp_path, date=d)
    by = {i["code"]: i for i in res["items"]}
    assert by["000001"]["grade"] == "S"
    assert by["000002"]["grade"] == "A"
    assert by["000003"]["grade"] == "B"
    assert by["000004"]["grade"] == "C"
    assert by["000005"]["grade"] == "N"
    assert by["000005"]["rank"] is None
    assert [by[c]["rank"] for c in ("000001", "000002", "000003", "000004")] == [1, 2, 3, 4]
    assert res["items"][0]["code"] == "000001"
    assert res["best"]["code"] == "000001"


def test_deep_unrecovered_is_d(tmp_path):
    """深水没回正 → D（比转弱更差），即使末条不是最低。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:15"), [_row("600261", -9.90)]),
        (_ts(d, "09:24"), [_row("600261", -8.00)]),
        (_ts(d, "09:26"), [_row("600261", -8.00)]),
    ])
    res = wp.scan(root=tmp_path, date=d)
    it = res["items"][0]
    assert it["grade"] == "D"
    assert "深水未回" in it["flags"]


def test_pending_is_not_ranked(tmp_path):
    """终态未确认 → N，⛔ 不当转弱也不当走强，且不进 best。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:15"), [_row("600261", 9.90)]),
        (_ts(d, "09:22"), [_row("600261", 5.00)]),
    ])
    res = wp.scan(root=tmp_path, date=d)
    it = res["items"][0]
    assert it["state"] == "pending" and it["grade"] == "N"
    assert it["rank"] is None and res["best"] is None
    assert res["coverage"]["pending"] == 1


def test_basis_has_numbers_and_times(tmp_path):
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"603381": {"name": "永臻股份"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:22"), [_row("603381", 9.93)]),
        (_ts(d, "09:26"), [_row("603381", 5.50)]),
    ])
    basis = wp.scan(root=tmp_path, date=d)["items"][0]["basis"]
    assert "09.93%" in basis or "+9.93%" in basis
    assert "09:22" in basis and "09:26" in basis
    assert "pp" in basis


def test_scan_does_not_persist(tmp_path):
    """扫描是只读：跑完不许多出任何文件（用户没要落盘）。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, d, [
        (_ts(d, "09:24"), [_row("600261", 3.0)]),
        (_ts(d, "09:26"), [_row("600261", 3.0)]),
    ])
    before = sorted(p.name for p in tmp_path.rglob("*"))
    wp.scan(root=tmp_path, date=d)
    after = sorted(p.name for p in tmp_path.rglob("*"))
    assert before == after


def test_registry_source_high_falls_back_to_last(tmp_path, monkeypatch):
    """观测表兜底：09:20 后没有新记录 = 值没变 → 高点取末条值，回撤 0。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"605339": {"name": "南侨食品"}})
    monkeypatch.setattr(wp, "_rounds_from_heartbeat", lambda root_p, x: [])
    monkeypatch.setattr(wp, "_rounds_from_registry", lambda root_p, x: [
        {"ts": _ts(d, "09:16"), "rows": [_row("605339", 10.00)]},
        {"ts": _ts(d, "09:26"), "rows": [_row("605339", 10.00)]},
    ])
    it = wp.auction_paths(root=tmp_path, date=d)["items"][0]
    assert it["high_pct"] == 10.00
    assert it["drop_pp"] == pytest.approx(0.0)


def test_scan_result_declares_non_decision_boundary(tmp_path):
    """结果里必须自带「不进裁决」的声明，⛔ 让下游误用。"""
    d = "2026-11-09"
    _pool_call(tmp_path, d, {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, d, [(_ts(d, "09:26"), [_row("600261", 3.0)])])
    res = wp.scan(root=tmp_path, date=d)
    assert "不进名额" in res["note"]
    assert res["schema"] == "watch_pool_scan_v1"
    assert res["params"]["fade_start"] == "09:20"
