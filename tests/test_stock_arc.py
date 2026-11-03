#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stock_arc 单测：切波边界 / 能不能买到 / 收益口径 / 分级 / 幂等。

全部离线（合成日线），⛔ 不触网。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "wealth_code")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import stock_arc  # noqa: E402


def _row(date: str, boards: int, *, gate: str = "pass", pool=None, cje: float = 5.0) -> dict:
    r = {
        "schema": "judgement_trail_v1", "date": date, "code": "600000", "name": "测试股",
        "boards": boards, "fbt": "093000", "zbc": 0, "seal_yi": 1.0, "hs_pct": 5.0,
        "cje_yi": cje, "sector": "测试", "is_20cm": False, "floor_yi": 3.0,
        "gate": gate, "pool": pool, "score": 70.0 if pool else None,
        "bucket_label": None, "verdict": None, "verdict_reason": None, "src": "archive",
    }
    r["mech"] = stock_arc_mech(r)
    return r


def stock_arc_mech(r: dict) -> list[str]:
    from core.judgement_trail import mech_tags

    return mech_tags(r)


def _bar(date: str, o: float, c: float, h: float | None = None, low: float | None = None,
         vol: float = 1000.0) -> dict:
    return {"date": date, "open": o, "close": c, "high": h if h is not None else max(o, c),
            "low": low if low is not None else min(o, c), "vol": vol}


def test_gap3_splits_new_wave():
    """断板 ≥3 个交易日 → 新一波；断 1 天 → 同一波。"""
    rows = [_row("2026-09-01", 1), _row("2026-09-03", 2), _row("2026-09-09", 3)]
    eps = stock_arc.build_episodes(rows, [], today="2026-10-23")
    # 09-01→09-03 中间隔 09-02 一天 → 同波；09-03→09-09 中间 09-04/07/08 三天 → 新波
    assert len(eps) == 2
    assert eps[0]["days"] == 2 and eps[0]["heights"] == [1, 2]
    assert eps[1]["days"] == 1 and eps[1]["gap_before"] >= 3


def test_one_word_next_day_not_fillable():
    """次日一字（high==low）→ 买不到，不算可进场，但收益仍按开收盘算出（这是事实）。"""
    rows = [_row("2026-09-01", 1)]
    bars = [_bar("2026-09-01", 10, 11), _bar("2026-09-02", 12.1, 12.1, h=12.1, low=12.1, vol=500)]
    leg = stock_arc.build_episodes(rows, bars, today="2026-10-23")[0]["legs"][0]
    assert leg["fillable"] is False
    assert leg["entry_ok"] is False
    assert "一字" in leg["note"]
    assert leg["ret_t1"] == 0.0          # 开盘=收盘，价差为 0（事实），不代表赚


def test_gap_over_limit_blocks_entry_but_keeps_return():
    """次日高开 8% → 超出 5% 准入窗口：不算可进场，但真实收益照样记录（不能抹掉）。"""
    rows = [_row("2026-09-01", 1)]
    bars = [_bar("2026-09-01", 10, 11), _bar("2026-09-02", 11.88, 12.5)]  # 11.88/11 = +8%
    leg = stock_arc.build_episodes(rows, bars, today="2026-10-23")[0]["legs"][0]
    assert leg["zone"] == "超上限"
    assert leg["entry_ok"] is False
    assert leg["gap_pct"] == pytest.approx(8.0, abs=0.01)
    assert leg["ret_t1"] == pytest.approx(round((12.5 - 11.88) / 11.88 * 100, 2))


def test_return_caliber_is_buy_open_sell_close():
    """账户口径：次日开盘买、次日收盘卖。"""
    rows = [_row("2026-09-01", 1)]
    bars = [_bar("2026-09-01", 10, 11), _bar("2026-09-02", 11.0, 12.1)]
    leg = stock_arc.build_episodes(rows, bars, today="2026-10-23")[0]["legs"][0]
    assert leg["entry"] == 11.0
    assert leg["ret_t1"] == pytest.approx(10.0, abs=0.01)


def test_pending_when_next_day_not_closed():
    """次日就是今天（盘中）→ 结果未定，收益必须留空，⛔ 不许写半截数字。"""
    rows = [_row("2026-09-01", 1)]
    bars = [_bar("2026-09-01", 10, 11), _bar("2026-10-23", 11.5, 12.0)]
    leg = stock_arc.build_episodes(rows, bars, today="2026-10-23")[0]["legs"][0]
    assert leg["pending"] is True
    assert leg["ret_t1"] is None and leg["ret_t2"] is None
    assert leg["note"] == "次日未收盘"


def test_profile_level_continuous_when_two_profitable_entries():
    rows = [_row("2026-09-01", 1), _row("2026-09-11", 2)]
    bars = [
        _bar("2026-09-01", 10, 11), _bar("2026-09-02", 11.2, 12.0),   # +7.1%
        _bar("2026-09-11", 10, 11), _bar("2026-09-14", 11.2, 11.8),   # +5.4%
    ]
    eps = stock_arc.build_episodes(rows, bars, today="2026-10-23")
    p = stock_arc.build_profile("600000", eps, today="2026-10-23")
    assert p["entry_days"] == 2
    assert p["level"] == "可连续变现"
    assert p["hit_rate"] == 100.0
    assert p["evolve"] == "走高"


def test_profile_level_blind_when_all_one_word():
    rows = [_row("2026-09-01", 1)]
    bars = [_bar("2026-09-01", 10, 11), _bar("2026-09-02", 12.1, 12.1, h=12.1, low=12.1)]
    eps = stock_arc.build_episodes(rows, bars, today="2026-10-23")
    p = stock_arc.build_profile("600000", eps, today="2026-10-23")
    assert p["level"] == "看得见吃不到"
    assert p["entry_days"] == 0


def _write_trail(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    with (d / "2026-09.jsonl").open("w", encoding="utf-8") as fh:
        for r in (_row("2026-09-01", 1), _row("2026-09-02", 2)):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_rebuild_idempotent_with_injected_bars(tmp_path):
    trail = tmp_path / "trail"
    out = tmp_path / "arc"
    _write_trail(trail)
    bars = {"600000": [_bar("2026-09-01", 10, 11), _bar("2026-09-02", 11.0, 12.1),
                       _bar("2026-09-03", 12.2, 13.0)]}
    a = stock_arc.rebuild(trail_dir=trail, out_dir=out, today="2026-10-23", bars_map=bars)
    first = (out / "arcs.json").read_text(encoding="utf-8")
    b = stock_arc.rebuild(trail_dir=trail, out_dir=out, today="2026-10-23", bars_map=bars)
    second = (out / "arcs.json").read_text(encoding="utf-8")
    assert a["episodes"] == b["episodes"] == 1
    assert first == second           # 同输入必得同产物
    assert a["codes_with_kline"] == 1


def test_patrol_needs_repeat_and_alive(tmp_path):
    out = tmp_path / "arc"
    arcs = {
        "600000": {"name": "反复股", "last_date": "2026-09-03", "appearances": 6,
                   "heights": [2, 2], "entry_days": 3, "hit_rate": 66.0, "avg_ret": 2.0,
                   "level": "可连续变现", "evolve": "持平", "last_boards": 2,
                   "sector": "测试"},
        "600001": {"name": "久未动", "last_date": "2026-08-20", "appearances": 6,
                   "heights": [2], "entry_days": 2, "hit_rate": 50.0, "avg_ret": 1.0,
                   "level": "单次可赚", "evolve": "持平", "last_boards": 2, "sector": "测试"},
        "600002": {"name": "只出现一次", "last_date": "2026-09-03", "appearances": 1,
                   "heights": [1], "entry_days": 0, "hit_rate": None, "avg_ret": None,
                   "level": "看得见吃不到", "evolve": "持平", "last_boards": 1, "sector": "测试"},
    }
    eps = []
    for code in ("600000", "600001", "600002"):
        for d in ("2026-09-01", "2026-09-02", "2026-09-03"):
            eps.append({"code": code, "start_date": d, "end_date": d, "days": 1,
                        "heights": [1], "max_boards": 1,
                        "legs": [{"date": d, "boards": 1, "gate": "pass", "pool": None}]})
    # 600002 只留一天，其余两只各有 3 天
    eps = [e for e in eps if not (e["code"] == "600002" and e["legs"][0]["date"] != "2026-09-03")]
    got = stock_arc.patrol("2026-09-03", arcs=arcs, episodes=eps)
    codes = {r["code"] for r in got}
    assert "600000" in codes
    assert "600001" not in codes      # 太久没出现
    assert "600002" not in codes      # 窗内不足 3 次
    assert got[0]["code"] == "600000"  # 可连续变现排最前


def test_phantom_nontrading_day_is_dropped(tmp_path):
    """2026-09-25 是中秋休市，但 decision_log 有归档（整份复制 09-24）→ 必须按日历剔掉。"""
    d = tmp_path / "trail"
    d.mkdir(parents=True)
    rows = [_row("2026-09-24", 1), _row("2026-09-25", 1), _row("2026-09-28", 2)]
    with (d / "2026-09.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    kept = stock_arc.load_trail(trail_dir=d)
    assert [r["date"] for r in kept] == ["2026-09-24", "2026-09-28"]
    assert stock_arc.phantom_dates(trail_dir=d) == ["2026-09-25"]


def test_prior_appearances_has_no_lookahead():
    """分组只数「当天之前」的涨停次数：第 1 次算 0 次，第 3 次算 2 次。"""
    eps = []
    for i, day in enumerate(("2026-09-01", "2026-09-08", "2026-09-15"), 1):
        eps.append({"code": "600000", "start_date": day, "end_date": day, "days": 1,
                    "heights": [i], "max_boards": i,
                    "legs": [{"date": day, "boards": i, "gate": "pass", "pool": None,
                              "fillable": True, "entry_ok": True, "ret_t1": 1.0, "pending": False}]})
    got = stock_arc.by_prior_appearances(eps)
    assert got["0次"]["n"] == 1
    assert got["1次"]["n"] == 1
    assert got["2次"]["n"] == 1
    assert "6次+" not in got
