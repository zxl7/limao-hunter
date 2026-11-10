#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观察池行情快照（common/watch_pool.py）回归。

覆盖三件事：
1. 池子来源单一入口：pool_call ∪ candidates（并集去重、非法代码过滤、截断）；
2. 采集只写行情事实：心跳每轮落一行、观测带独立 source、陈旧行情不进事实、
   ⛔ 不写 limit_up_now / limit_up_touched（不污染涨停闩锁）；
3. 弱转强读法四态：reversal / pending / flat / no_data，
   ⛔ 「终态未到」不得当 reversal。
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


def _candidates(root: Path, date: str, relay=None, watch=None) -> None:
    _write(root / wp.POOL_DIR / f"candidates-{date}.json",
           {"date": date, "relay": relay or [], "watch": watch or []})


def _quote(pct, price=10.0, name="票", ts="2026-11-09T09:24:00+08:00"):
    return {"name": name, "pct": pct, "price": price, "source_ts_iso": ts}


@pytest.fixture()
def fake_quotes(monkeypatch):
    """把行情层换成可控字典；默认返回空表（各用例自己填）。

    同时把陈旧阈值放宽到极大 —— 用例里的行情时间戳都是写死的，
    若不放宽会被当成「旧价」全部过滤；专门测陈旧的用例自己改回真阈值。
    """
    state = {"data": {}}

    def _fake(codes):
        return {c: state["data"][c] for c in codes if c in state["data"]}

    import common.em_flow as em
    monkeypatch.setattr(em, "fetch_quotes", _fake)
    monkeypatch.setattr(em, "em_flow_enabled", lambda: True)
    monkeypatch.setattr(wp, "STALE_SECONDS", 10 ** 9)
    return state


@pytest.fixture()
def no_registry_write(monkeypatch):
    """拦截 registry 写入，只留痕不落库。"""
    calls: list[dict] = []
    import common.stock_registry as reg

    def _fake(observations, *, trade_date, asof_ts, source, **kw):
        rows = list(observations)
        calls.append({"rows": rows, "trade_date": trade_date,
                      "asof_ts": asof_ts, "source": source})
        return {"ok": True, "inserted": len(rows), "updated": 0, "unchanged": 0}

    monkeypatch.setattr(reg, "upsert_many", _fake)
    return calls


# ── 池子来源 ──────────────────────────────────────────────────────────
def test_pool_union_of_call_and_candidates(tmp_path):
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _candidates(tmp_path, "2026-11-06",
                watch=[{"code": "002149", "name": "西部材料"},
                       {"code": "600261", "name": "阳光照明"}])
    pool = wp.watch_codes(root=tmp_path, date="2026-11-09")
    assert pool["codes"] == ["600261", "002149"]      # 并集且去重、顺序稳定
    assert pool["names"]["002149"] == "西部材料"
    assert any(s.startswith("candidates:") for s in pool["sources"])


def test_pool_call_only_before_candidates_exist(tmp_path):
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    pool = wp.watch_codes(root=tmp_path, date="2026-11-09")
    assert pool["codes"] == ["600261"]
    assert pool["sources"] == ["pool_call"]


def test_candidates_only_before_call_frozen(tmp_path):
    """09:25 前 pool_call 还没冻结 → 只用昨日盘后预案池。"""
    _candidates(tmp_path, "2026-11-06", relay=[{"code": "002088", "name": "鲁阳节能"}])
    pool = wp.watch_codes(root=tmp_path, date="2026-11-09")
    assert pool["codes"] == ["002088"]


def test_candidates_must_not_be_future(tmp_path):
    """未来日期的候选不得当成今日观察池。"""
    _candidates(tmp_path, "2026-11-10", watch=[{"code": "600261", "name": "阳光照明"}])
    pool = wp.watch_codes(root=tmp_path, date="2026-11-09")
    assert pool["codes"] == []
    assert "为空" in pool["reason"]


def test_pool_filters_bad_codes_and_truncates(tmp_path):
    bad = {"abc": {"name": "坏"}, "12345": {"name": "五位"},
           "60026X": {"name": "带字母"}}
    good = {f"60{i:04d}": {"name": f"票{i}"} for i in range(5)}
    _pool_call(tmp_path, "2026-11-09", {**bad, **good})
    pool = wp.watch_codes(root=tmp_path, date="2026-11-09")
    assert set(pool["codes"]) == set(good)
    assert pool["truncated"] == 0


def test_pool_truncation_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "MAX_CODES", 3)
    _pool_call(tmp_path, "2026-11-09",
               {f"60{i:04d}": {"name": f"票{i}"} for i in range(6)})
    pool = wp.watch_codes(root=tmp_path, date="2026-11-09")
    assert len(pool["codes"]) == 3
    assert pool["truncated"] == 3


# ── 采集 ──────────────────────────────────────────────────────────────
def test_snapshot_writes_heartbeat_and_registry(tmp_path, fake_quotes, no_registry_write):
    _pool_call(tmp_path, "2026-11-09",
               {"600261": {"name": "阳光照明"}, "002149": {"name": "西部材料"}})
    fake_quotes["data"] = {
        "600261": _quote(-9.8, 5.0, "阳光照明"),
        "002149": _quote(9.99, 41.5, "西部材料"),
    }
    res = wp.snapshot(root=tmp_path, trade_date="2026-11-09",
                      asof_ts="2026-11-09T09:20:00+08:00")
    assert res["ok"] is True and res["quoted"] == 2

    heart = (tmp_path / wp.SNAP_DIR / "2026-11-09.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(heart) == 1
    row = json.loads(heart[0])
    assert row["ts"] == "2026-11-09T09:20:00+08:00"
    assert {r["code"] for r in row["rows"]} == {"600261", "002149"}

    assert len(no_registry_write) == 1
    call = no_registry_write[0]
    assert call["source"] == wp.REGISTRY_SOURCE
    assert {r["code"] for r in call["rows"]} == {"600261", "002149"}


def test_snapshot_never_touches_limit_up_latch(tmp_path, fake_quotes, no_registry_write):
    """⛔ 行情快照不得写涨停闩锁——那是选股宝的强证据。"""
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    fake_quotes["data"] = {"600261": _quote(9.99, 5.7)}
    wp.snapshot(root=tmp_path, trade_date="2026-11-09",
                asof_ts="2026-11-09T09:20:00+08:00")
    row = no_registry_write[0]["rows"][0]
    assert "limit_up_now" not in row
    assert "limit_up_touched" not in row
    assert row["pct"] == 9.99


def test_snapshot_skips_stale_quotes(tmp_path, fake_quotes, no_registry_write, monkeypatch):
    """行情时间戳落后 → 不进事实、不进心跳（不拿旧价冒充当前）。"""
    monkeypatch.setattr(wp, "STALE_SECONDS", 180)
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    fake_quotes["data"] = {"600261": _quote(-3.0, 5.0, ts="2026-11-09T08:00:00+08:00")}
    res = wp.snapshot(root=tmp_path, trade_date="2026-11-09",
                      asof_ts="2026-11-09T13:00:00+08:00")
    assert res["stale"] == ["600261"]
    assert res["quoted"] == 0
    assert no_registry_write == []
    assert not (tmp_path / wp.SNAP_DIR / "2026-11-09.jsonl").exists()


def test_snapshot_records_missing_codes(tmp_path, fake_quotes, no_registry_write):
    _pool_call(tmp_path, "2026-11-09",
               {"600261": {"name": "阳光照明"}, "002149": {"name": "西部材料"}})
    fake_quotes["data"] = {"600261": _quote(1.0)}
    res = wp.snapshot(root=tmp_path, trade_date="2026-11-09",
                      asof_ts="2026-11-09T09:20:00+08:00")
    assert res["missing"] == ["002149"]
    assert res["quoted"] == 1


def test_snapshot_empty_pool_is_reported(tmp_path, fake_quotes):
    res = wp.snapshot(root=tmp_path, trade_date="2026-11-09",
                      asof_ts="2026-11-09T09:20:00+08:00")
    assert res["ok"] is False
    assert "为空" in res["reason"]


def test_snapshot_switch_off(tmp_path, monkeypatch, fake_quotes):
    monkeypatch.setattr(wp, "ENABLE_WATCH_POOL", False)
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    res = wp.snapshot(root=tmp_path, trade_date="2026-11-09")
    assert res["ok"] is False
    assert "ENABLE_WATCH_POOL_SNAPSHOT" in res["reason"]
    assert not (tmp_path / wp.SNAP_DIR).exists()


def test_stale_limit_wider_in_auction_window():
    """竞价段放宽陈旧阈值：09:15 的价到 09:24 也已 9 分钟，不能判成旧价。"""
    from datetime import datetime as _dt
    in_auction = _dt(2026, 11, 9, 9, 20, tzinfo=wp.BJ)
    off_auction = _dt(2026, 11, 9, 13, 20, tzinfo=wp.BJ)
    assert wp._stale_limit(in_auction) == wp.AUCTION_STALE_SECONDS
    assert wp._stale_limit(off_auction) == wp.STALE_SECONDS
    assert wp.AUCTION_STALE_SECONDS > wp.STALE_SECONDS


def test_snapshot_dry_run_writes_nothing(tmp_path, fake_quotes, no_registry_write):
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    fake_quotes["data"] = {"600261": _quote(-2.0)}
    res = wp.snapshot(root=tmp_path, trade_date="2026-11-09",
                      asof_ts="2026-11-09T09:20:00+08:00", persist=False)
    assert res["quoted"] == 1
    assert not (tmp_path / wp.SNAP_DIR / "2026-11-09.jsonl").exists()
    assert no_registry_write == []


# ── 弱转强读法 ────────────────────────────────────────────────────────
def _heart(root: Path, date: str, rounds: list[tuple[str, list[dict]]]) -> None:
    path = root / wp.SNAP_DIR / f"{date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ts, rows in rounds:
            fh.write(json.dumps({"ts": ts, "rows": rows}, ensure_ascii=False) + "\n")


def _row(code, pct, price=10.0, name="票"):
    return {"code": code, "name": name, "pct": pct, "price": price}


def test_paths_detects_reversal(tmp_path):
    """跌停 → 红开：这就是用户要看的形态。"""
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:15:05+08:00", [_row("600261", -9.98)]),
        ("2026-11-09T09:20:05+08:00", [_row("600261", -6.20)]),
        ("2026-11-09T09:24:05+08:00", [_row("600261", 1.80)]),
        ("2026-11-09T09:26:05+08:00", [_row("600261", 2.40)]),
    ])
    res = wp.auction_paths(root=tmp_path, date="2026-11-09")
    item = res["items"][0]
    assert item["state"] == "reversal"
    assert item["low_pct"] == -9.98
    assert item["last_pct"] == 2.40
    assert item["span_pct"] == pytest.approx(12.38)
    assert res["counts"]["reversal"] == 1


def test_paths_pending_when_last_round_before_0925(tmp_path):
    """末条早于 09:25 → 不知道，⛔ 不许当弱转强。"""
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:15:05+08:00", [_row("600261", -9.98)]),
        ("2026-11-09T09:22:05+08:00", [_row("600261", 1.50)]),
    ])
    item = wp.auction_paths(root=tmp_path, date="2026-11-09")["items"][0]
    assert item["state"] == "pending"
    assert "终态未到" in item["reason"]


def test_paths_flat_when_no_deep_dip(tmp_path):
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:15:05+08:00", [_row("600261", 9.98)]),
        ("2026-11-09T09:26:05+08:00", [_row("600261", 5.04)]),
    ])
    item = wp.auction_paths(root=tmp_path, date="2026-11-09")["items"][0]
    assert item["state"] == "flat"


def test_paths_deep_but_still_red_requires_positive_last(tmp_path):
    """深水后只是少亏、仍绿 → 不是弱转强。"""
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:15:05+08:00", [_row("600261", -9.98)]),
        ("2026-11-09T09:26:05+08:00", [_row("600261", -0.20)]),
    ])
    item = wp.auction_paths(root=tmp_path, date="2026-11-09")["items"][0]
    assert item["state"] == "flat"


def test_paths_no_data_is_not_flat(tmp_path, monkeypatch):
    # ⚠️ 心跳窗口内无该票 → 会回落 registry；测试里断开回落，避免读到真实库
    monkeypatch.setattr(wp, "_rounds_from_registry", lambda *a, **k: [])
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:15:05+08:00", [_row("002149", -9.98)]),
        ("2026-11-09T09:26:05+08:00", [_row("002149", 3.0)]),
    ])
    res = wp.auction_paths(root=tmp_path, date="2026-11-09")
    item = res["items"][0]
    assert item["code"] == "600261"
    assert item["state"] == "no_data"
    assert res["counts"]["no_data"] == 1


def test_paths_outside_window_ignored(tmp_path, monkeypatch):
    """窗口外的轮次不算竞价路径（13:00 的行情不能当竞价）。"""
    monkeypatch.setattr(wp, "_rounds_from_registry", lambda *a, **k: [])
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T10:30:00+08:00", [_row("600261", -9.98)]),
        ("2026-11-09T13:00:00+08:00", [_row("600261", 3.0)]),
    ])
    item = wp.auction_paths(root=tmp_path, date="2026-11-09")["items"][0]
    assert item["state"] == "no_data"


def test_paths_falls_back_to_registry_when_heartbeat_empty_in_window(tmp_path, monkeypatch):
    """当天心跳只在盘中产生（快照中午上线）→ 窗口内为空时可回落观测表，但必须标来源。"""
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T13:00:00+08:00", [_row("600261", 1.0)]),
    ])
    fake = [{"ts": "2026-11-09T09:15:09+08:00", "rows": [_row("600261", -9.0)]},
            {"ts": "2026-11-09T09:26:06+08:00", "rows": [_row("600261", 2.0)]}]
    monkeypatch.setattr(wp, "_rounds_from_registry", lambda *a, **k: fake)
    res = wp.auction_paths(root=tmp_path, date="2026-11-09")
    assert res["source"] == "registry"
    assert res["items"][0]["state"] == "reversal"


def test_paths_heartbeat_preferred_over_registry(tmp_path, monkeypatch):
    """心跳存在时不得回落到 registry 兜底。"""
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:26:05+08:00", [_row("600261", 1.0)]),
    ])
    monkeypatch.setattr(wp, "_rounds_from_registry",
                        lambda *a, **k: pytest.fail("不应回落"))
    res = wp.auction_paths(root=tmp_path, date="2026-11-09")
    assert res["source"] == "heartbeat"


def test_paths_custom_deep_threshold(tmp_path):
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:15:05+08:00", [_row("600261", -3.2)]),
        ("2026-11-09T09:26:05+08:00", [_row("600261", 1.0)]),
    ])
    default = wp.auction_paths(root=tmp_path, date="2026-11-09")["items"][0]
    loose = wp.auction_paths(root=tmp_path, date="2026-11-09", deep_pct=-3.0)["items"][0]
    assert default["state"] == "flat"
    assert loose["state"] == "reversal"


# ── 卡面文案 ──────────────────────────────────────────────────────────
def test_card_lines_prefix_and_disclaimer(tmp_path):
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:15:05+08:00", [_row("600261", -9.98, name="阳光照明")]),
        ("2026-11-09T09:26:05+08:00", [_row("600261", 2.0, name="阳光照明")]),
    ])
    res = wp.auction_paths(root=tmp_path, date="2026-11-09")
    text = "\n".join(wp.card_lines(res))
    assert "阳光照明" in text
    assert "拉升" in text
    assert "不进名额" in text


def test_card_lines_no_reversal_wording(tmp_path):
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:15:05+08:00", [_row("600261", -9.98)]),
    ])
    res = wp.auction_paths(root=tmp_path, date="2026-11-09")
    text = "\n".join(wp.card_lines(res))
    assert "没有出现" in text
    assert "1 只终态未确认" in text


# ── 接线锁（源码级，防「写了模块但没接进采集链」）──────────────────────
def test_intraday_pulse_wires_watch_pool():
    """盘中采集必须每轮调用观察池快照，且放在底库写入之后、推送之前。"""
    src = (ROOT / "scripts" / "intraday_pulse.py").read_text(encoding="utf-8")
    assert "_snapshot_watch_pool()" in src
    assert "from common import watch_pool" in src

    i_reg = src.index("_ingest_stock_registry(stocks, pre_stocks, rising")
    i_watch = src.index("    _snapshot_watch_pool()")
    i_push = src.index('if mode == "intra":', i_reg)
    assert i_reg < i_watch < i_push, "观察池快照应在底库写入后、推送前"


def test_intraday_pulse_watch_pool_failure_is_non_fatal():
    """观察池快照异常不得中断快照与推送（非致命边界）。"""
    src = (ROOT / "scripts" / "intraday_pulse.py").read_text(encoding="utf-8")
    start = src.index("def _snapshot_watch_pool():")
    end = src.index("def run_mode(", start)
    body = src[start:end]
    assert "try:" in body and "except Exception" in body
    assert "不影响主流程" in body


# ── 兜底来源的确认判据（2026-11-09 实测踩到）────────────────────────────
def test_registry_source_confirms_by_global_last(tmp_path, monkeypatch):
    """观测表兜底：票级末条 = 最后一次**变化**，确认要看**全局最后采样**。

    事故形态：一只票 09:16 之后价格没动过 → 观测表里只有 09:16 一条
    （同源同值不重复写）→ 若沿用「票级末条 ≥09:25」判据，它会被判成 pending，
    即使那一轮确实采到了、价格确实没变。今日实测 15 只里多数踩这个坑。
    """
    _pool_call(tmp_path, "2026-11-09",
               {"600261": {"name": "阳光照明"}, "002088": {"name": "鲁阳节能"}})
    rounds = [
        {"ts": "2026-11-09T09:16:00+08:00",
         "rows": [_row("600261", -9.8, name="阳光照明")]},
        {"ts": "2026-11-09T09:26:00+08:00",
         "rows": [_row("600261", 1.2, name="阳光照明"),
                  _row("002088", 0.5, name="鲁阳节能")]},
    ]
    monkeypatch.setattr(wp, "_rounds_from_heartbeat", lambda root_p, d: [])
    monkeypatch.setattr(wp, "_rounds_from_registry", lambda root_p, d: rounds)

    res = wp.auction_paths(root=tmp_path, date="2026-11-09")
    assert res["source"] == "registry"
    assert res["global_last_ts"] == "09:26"
    by = {i["code"]: i for i in res["items"]}
    assert by["600261"]["state"] == "reversal"
    assert by["002088"]["state"] == "flat"     # 09:26 才首现，也照样算确认


def test_registry_source_still_pending_when_global_last_early(tmp_path, monkeypatch):
    """全局最后采样早于 09:25 → 仍然待确认（⛔ 放宽来源不等于放宽判据）。"""
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    rounds = [{"ts": "2026-11-09T09:18:00+08:00",
               "rows": [_row("600261", -9.8, name="阳光照明")]}]
    monkeypatch.setattr(wp, "_rounds_from_heartbeat", lambda root_p, d: [])
    monkeypatch.setattr(wp, "_rounds_from_registry", lambda root_p, d: rounds)

    res = wp.auction_paths(root=tmp_path, date="2026-11-09")
    assert res["items"][0]["state"] == "pending"
    assert "全局最后采样" in res["items"][0]["reason"]


def test_heartbeat_source_keeps_ticket_level_judgement(tmp_path):
    """心跳来源每轮全量写 → 仍按票级末条判（两套判据不可互换）。"""
    _pool_call(tmp_path, "2026-11-09", {"600261": {"name": "阳光照明"}})
    _heart(tmp_path, "2026-11-09", [
        ("2026-11-09T09:15:05+08:00", [_row("600261", -9.98)]),
        ("2026-11-09T09:24:05+08:00", [_row("600261", 1.0)]),
    ])
    res = wp.auction_paths(root=tmp_path, date="2026-11-09")
    assert res["source"] == "heartbeat"
    assert res["items"][0]["state"] == "pending"
