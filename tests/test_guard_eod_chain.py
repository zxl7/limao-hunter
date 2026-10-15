#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘后链落盘守护守卫（2026-09-22 立）

锁住「盘中口径 ≠ 盘后链跑过」这条判据 —— 它是本守护存在的理由：
原实现只查 `exists()` / `date` 字段，而 eruption / sector_flow / emotion / theme_rotation /
carrier_state **盘中也会被写**（盘中回流补跑），于是**盘后链整条未执行**时守护静默放行。
实测事故：2026-09-22 五处产物 mtime 全在 10:41~11:12，守护 17:50 零告警。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import guard_eod_chain as g          # noqa: E402


def _touch(p: Path, when: datetime) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}", encoding="utf-8")
    ts = when.timestamp()
    os.utime(p, (ts, ts))


TODAY = "2026-09-22"
DAY = datetime(2026, 9, 22, 11, 12)          # 盘中
EOD = datetime(2026, 9, 22, 17, 25)          # 盘后链
PREV = datetime(2026, 9, 21, 17, 25)         # 前一日盘后


# ────────────────── 1. _cover_state 判据 ──────────────────
def test_cover_state_missing(tmp_path: Path) -> None:
    ok, why = g._cover_state(tmp_path / "nope.json", TODAY)
    assert ok is False
    assert "不存在" in why


def test_cover_state_intraday_is_not_covered(tmp_path: Path) -> None:
    """⛔ 盘中写入必须判为「未覆盖」——这是本次修的核心。"""
    p = tmp_path / "x.json"
    _touch(p, DAY)
    ok, why = g._cover_state(p, TODAY)
    assert ok is False
    assert "盘中口径" in why
    assert "11:12" in why


def test_cover_state_eod_is_covered(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    _touch(p, EOD)
    ok, why = g._cover_state(p, TODAY)
    assert ok is True
    assert why == "17:25"


def test_cover_state_previous_day_is_not_covered(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    _touch(p, PREV)
    ok, why = g._cover_state(p, TODAY)
    assert ok is False
    assert "非当日" in why


def test_cover_state_boundary_at_threshold(tmp_path: Path) -> None:
    """恰在 16:00 整 → 视为已覆盖（≥ 判据，不是 >）。"""
    p = tmp_path / "x.json"
    _touch(p, datetime(2026, 9, 22, 16, 0))
    ok, _ = g._cover_state(p, TODAY)
    assert ok is True
    p2 = tmp_path / "y.json"
    _touch(p2, datetime(2026, 9, 22, 15, 59))
    ok2, why2 = g._cover_state(p2, TODAY)
    assert ok2 is False and "盘中口径" in why2


# ────────────────── 2. _missing_of 组合判据 ──────────────────
def _seed(cache: Path, *, at: datetime, with_latest: bool) -> None:
    _touch(cache / "eruption_history" / f"eruption_{TODAY}.json", at)
    _touch(cache / "sector_flow_history" / f"sector_flow_{TODAY}.json", at)
    _touch(cache / "short_term" / "daily" / f"emotion_{TODAY}.json", at)
    if with_latest:
        p = cache / "theme_rotation.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"date": TODAY}), encoding="utf-8")
        ts = at.timestamp()
        os.utime(p, (ts, ts))
        _touch(cache / "carrier_state.json", at)


def test_missing_of_all_green_when_eod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=EOD, with_latest=True)
    assert g._missing_of(TODAY, live=True) == []


def test_missing_of_flags_intraday_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """事故复现：文件全在但都是盘中写的 → 必须 5 项全报。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=DAY, with_latest=True)
    miss = g._missing_of(TODAY, live=True)
    names = {m[0] for m in miss}
    assert len(miss) == 5, f"应报 5 项盘中口径，实得 {miss}"
    assert names == {"题材爆发快照", "板块资金流", "情绪日快照", "题材回流引擎", "载体证伪"}
    assert all("盘中口径" in m[1] for m in miss)


def test_missing_of_live_false_skips_latest_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """历史查询不做「只保留最新快照」的两项的判定（否则必然误报）。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=EOD, with_latest=False)          # theme_rotation/carrier 缺失
    assert g._missing_of(TODAY, live=False) == []
    assert {m[0] for m in g._missing_of(TODAY, live=True)} == {"题材回流引擎", "载体证伪"}


def test_missing_of_flags_absent_dated_products(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """按日期命名的三件套完全缺失（09-21 事故形态）→ 历史查询也能查出。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    miss = g._missing_of("2026-09-21", live=False)
    assert len(miss) == 3
    assert all("不存在" in m[1] for m in miss)


def test_theme_rotation_date_mismatch_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """date 字段是昨日 → 报到「读取失败/日期不符」，且不因 mtime 正常而放过。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=EOD, with_latest=False)
    p = tmp_path / "theme_rotation.json"
    p.write_text(json.dumps({"date": "2026-09-21"}), encoding="utf-8")
    ts = EOD.timestamp()
    os.utime(p, (ts, ts))
    _touch(tmp_path / "carrier_state.json", EOD)
    miss = g._missing_of(TODAY, live=True)
    assert len(miss) == 1 and miss[0][0] == "题材回流引擎"
    assert "2026-09-21" in miss[0][1]


def test_theme_rotation_realtime_flag_is_not_eod_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ 第二类盲区（2026-09-29 实测）：盘中链 realtime_analytics 收盘后仍在写
    `theme_rotation.json` ⇒ `date==今日` + `mtime≥16:00` 双判据同时通过，
    盘后链没跑也判绿（当日实际缺 5 项、卡只报 4 项）。
    盘后链版不写 `realtime` 字段 ⇒ 读到 `realtime=true` 必须判为未覆盖。
    """
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=EOD, with_latest=False)
    p = tmp_path / "theme_rotation.json"
    p.write_text(
        json.dumps({"date": TODAY, "realtime": True, "source_ts": "2026-09-22T15:00:08+08:00"}),
        encoding="utf-8",
    )
    ts = datetime(2026, 9, 22, 17, 51).timestamp()        # 事故时刻：收盘后
    os.utime(p, (ts, ts))
    _touch(tmp_path / "carrier_state.json", EOD)
    miss = g._missing_of(TODAY, live=True)
    assert len(miss) == 1 and miss[0][0] == "题材回流引擎"
    assert "realtime=true" in miss[0][1]


def test_theme_rotation_without_realtime_flag_still_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """盘后链产出的 theme_rotation（**无** realtime 字段）不得被误伤。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=EOD, with_latest=True)
    p = tmp_path / "theme_rotation.json"
    p.write_text(json.dumps({"date": TODAY, "data_days": [TODAY]}), encoding="utf-8")
    ts = EOD.timestamp()
    os.utime(p, (ts, ts))
    assert g._missing_of(TODAY, live=True) == []


# ────────────────── 3. 推卡走测试群（内部验证卡铁律） ──────────────────
def test_alert_goes_to_test_webhook() -> None:
    src = (BASE / "scripts" / "guard_eod_chain.py").read_text(encoding="utf-8")
    assert "feishu_webhook_test" in src
    assert "feishu_webhook_prod" not in src


# ────────────────── 4. 双条件：内容 generated_ts（2026-10-14 审计 P2-⑨ 修 B3） ──────
# 审计冲突项 B3：契约 §1 写「文件 mtime 不能替代内容日期」，而 `_cover_state` **只用 mtime**。
# 升级为**声明制双条件**：mtime 达标之外，声明为 `collect` 的产出还要求内容
# `generated_ts` 同样达标；声明为 `source` 的（emotion = 15:00 源时刻）**必须豁免**，
# 否则每个交易日都会误报。

def _write_with_ts(p: Path, *, mtime: datetime, generated_ts: str) -> None:
    """写一个带 `generated_ts` 的产物，并把 mtime 钉到指定时刻。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"generated_ts": generated_ts}), encoding="utf-8")
    ts = mtime.timestamp()
    os.utime(p, (ts, ts))


def test_cover_state_flags_mtime_content_contradiction(tmp_path: Path) -> None:
    """⛔ B3 反例：mtime 在盘后（17:25）但内容 generated_ts 是盘中（15:00）→ 必须判未覆盖。

    这正是审计实测的形态（`zt_2026-10-13.json` 内容 17:18:51 / mtime 10-15 20:25，
    只信 mtime 会判绿）。「以内容为准」= 契约 §1 的要求。
    """
    p = tmp_path / "x.json"
    _write_with_ts(p, mtime=EOD, generated_ts="2026-09-22T15:00:12+08:00")
    ok, why = g._cover_state(p, TODAY, "collect")
    assert ok is False
    assert "内容 generated_ts=15:00" in why


def test_cover_state_flags_content_from_other_day(tmp_path: Path) -> None:
    """内容 `generated_ts` 是**别的日期**而 mtime 是今日 → 也必须报（静默半途而废形态）。"""
    p = tmp_path / "x.json"
    _write_with_ts(p, mtime=EOD, generated_ts="2026-09-21 17:18:51")
    ok, why = g._cover_state(p, TODAY, "collect")
    assert ok is False
    assert "内容日期非当日" in why


def test_cover_state_collect_passes_when_both_sides_eod(tmp_path: Path) -> None:
    """mtime 与内容时间**都**在盘后 → 通过（不得误伤正常产出）。"""
    p = tmp_path / "x.json"
    _write_with_ts(p, mtime=EOD, generated_ts="2026-09-22T17:25:03+08:00")
    ok, why = g._cover_state(p, TODAY, "collect")
    assert ok is True and why == "17:25"


def test_cover_state_source_semantic_exempts_content_check(tmp_path: Path) -> None:
    """⛔ emotion 形态：内容 `generated_ts` 恒为 15:00 源时刻，声明 `source` 必须豁免。

    若不豁免，`short_term/daily/emotion_*.json` 会**每个交易日误报**一次
    「未覆盖」——这是引入第二条判据时最容易踩的坑。
    """
    p = tmp_path / "emotion.json"
    _write_with_ts(p, mtime=EOD, generated_ts="2026-09-22T15:00:12+08:00")
    ok, why = g._cover_state(p, TODAY, "source")
    assert ok is True and why == "17:25"
    # 反向确认：同一份内容若被误标 collect，就会 FAIL（证明豁免是**必要的**而非多余）
    bad, why_bad = g._cover_state(p, TODAY, "collect")
    assert bad is False and "内容 generated_ts=15:00" in why_bad


def test_cover_state_falls_back_to_mtime_when_content_ts_unreadable(tmp_path: Path) -> None:
    """内容无 `generated_ts` / 格式不识别 → **只按 mtime**（诚实降级，不误伤存量格式）。"""
    p = tmp_path / "x.json"
    _touch(p, EOD)                                     # 内容 = "{}"
    ok, why = g._cover_state(p, TODAY, "collect")
    assert ok is True and why == "17:25"
    p2 = tmp_path / "y.json"
    _write_with_ts(p2, mtime=EOD, generated_ts="not-a-time")
    ok2, _ = g._cover_state(p2, TODAY, "collect")
    assert ok2 is True, "不可解析的内容时间不得触发误报"
    assert g._content_ts(p2) is None


def test_semantics_table_declares_every_check() -> None:
    """⛔ 每个受检产出都必须在 `TS_SEMANTICS` 里声明语义（漏登记=默认为较严的 collect）。

    本测试锁住「emotion 必须显式标 source」——它是最容易在重构中被漏掉、且一漏就每天误报的一条。
    """
    for _name, tmpl, _imp in g.CHECKS:
        assert tmpl in g.TS_SEMANTICS, f"CHECKS 产出未登记语义：{tmpl}"
    for _name, rel, _key, _imp in g.DATED_JSON:
        assert rel in g.TS_SEMANTICS, f"DATED_JSON 产出未登记语义：{rel}"
    for _name, rel, _h, _imp in g.FRESH_FILES:
        assert rel in g.TS_SEMANTICS, f"FRESH_FILES 产出未登记语义：{rel}"
    assert g.TS_SEMANTICS["short_term/daily/emotion_{}.json"] == "source", (
        "情绪日快照的 generated_ts 是 15:00 行情源时刻，必须声明 source 豁免内容判据"
    )
    for tmpl, sem in g.TS_SEMANTICS.items():
        assert sem in ("collect", "source"), f"{tmpl} 语义取值非法：{sem}"


def test_missing_of_uses_declared_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端：mtime 全在盘后、但 eruption 内容时间是盘中 → 只报「题材爆发快照」一项，
    emotion 同形态（源时刻）**不得**被报。"""
    monkeypatch.setattr(g, "CACHE", tmp_path)
    _seed(tmp_path, at=EOD, with_latest=True)          # 基线：全绿
    # 只把 eruption 的内容时间踩成盘中（模拟「mtime 被后续动作刷新、内容还是盘中口径」）
    _write_with_ts(tmp_path / "eruption_history" / f"eruption_{TODAY}.json",
                   mtime=EOD, generated_ts="2026-09-22T10:41:07+08:00")
    # emotion 同形态（源时刻 15:00）——声明 source，不该被报
    _write_with_ts(tmp_path / "short_term" / "daily" / f"emotion_{TODAY}.json",
                   mtime=EOD, generated_ts="2026-09-22T15:00:12+08:00")
    miss = g._missing_of(TODAY, live=True)
    assert [m[0] for m in miss] == ["题材爆发快照"], f"实得 {miss}"
    assert "内容 generated_ts=10:41" in miss[0][1]
