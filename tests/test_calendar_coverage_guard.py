#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易日历覆盖度守卫（2026-10-14 审计 R1）回归测试。

**风险 R1 原文**：`config/a_share_calendar.json` 只覆盖 2026 年（`closed_dates` 全为 2026），
文件 note 自陈「每年公布安排后维护下一年度」⇒ **2027-01-01 起元旦/春节会被判为交易日**
→ 幽灵交易日 + 幽灵归档（与 2026-09-25 中秋漏列事故同型，但这次是整年级别）。

**处置口径（本轮落地）**：国办 2027 年安排截至 2026-10-14 **尚未公布**（通常 11 月发布），
故**不能补录**（编造就违反「数据即事实禁编造」）。改为：
  ① `common/trading_calendar` 暴露覆盖度查询（`covered_years` / `is_year_covered` / `uncovered_years`）；
  ② `guard_eod_chain` 在**距年末 45 天**起检查「当年 + 次年」，未覆盖 → 推测试群提醒卡，
     **同一年只推一次**（状态标记），不刷屏；
  ③ `capture_daily._guard_trading_day` 每次跑**日志级**告警。
⛔ 三条都不改「交易日判定」本身 —— 把「未覆盖」并入休市判定会连续拦掉整年真实交易日。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))

import guard_eod_chain as g          # noqa: E402
from common import trading_calendar as tc   # noqa: E402


# ────────────────── 1. 真实日历文件的覆盖度现状 ──────────────────
def test_real_calendar_covers_2026_only():
    """⛔ 现状断言：真实日历只覆盖 2026 年。**若本测试开始失败，说明已补录次年 → 请更新 R1 状态。**"""
    years = tc.covered_years()
    assert 2026 in years, "2026 必须已覆盖"
    assert 2027 not in years, (
        "2027 已被覆盖 —— 说明已补录次年日历，R1 已闭环；"
        "请同步更新 架构/数据血缘与行情口径审计_2026-10-14.md 的风险表与本节测试"
    )


def test_uncovered_year_would_be_treated_as_trading_day():
    """幽灵日的**机制证明**：未覆盖年度的工作日会被判成交易日。

    用 2027-01-01（周五·元旦）验证 —— 若日历已补录该年，此断言会失败（说明风险已消除）。
    """
    if tc.is_year_covered(2027):        # pragma: no cover - 已补录时不适用
        pytest.skip("2027 已补录，机制证明不再适用")
    assert datetime(2027, 1, 1).weekday() == 4, "2027-01-01 应为周五（工作日）"
    assert tc.is_a_share_trading_day("2027-01-01") is True, (
        "未覆盖时元旦会被判为交易日 —— 这正是幽灵归档的成因"
    )


# ────────────────── 2. 覆盖度查询本身 ──────────────────
def _write_cal(tmp_path: Path, years) -> Path:
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({
        "schema": "a_share_calendar_v1",
        "closed_dates": [f"{y}-01-01" for y in years],
        "open_dates": [],
    }, ensure_ascii=False), encoding="utf-8")
    return p


def test_covered_years_and_uncovered_years(tmp_path: Path) -> None:
    cal = _write_cal(tmp_path, [2025, 2026])
    assert tc.covered_years(calendar_path=cal) == {2025, 2026}
    assert tc.is_year_covered(2026, calendar_path=cal) is True
    assert tc.is_year_covered(2027, calendar_path=cal) is False
    assert tc.uncovered_years([2026, 2027, 2028], calendar_path=cal) == [2027, 2028]
    assert tc.uncovered_years([2027, 2025], calendar_path=cal) == [2027], "须去重且升序"


def test_coverage_is_report_only_not_a_gate(tmp_path: Path) -> None:
    """⛔ 核心口径：覆盖度**不参与**交易日判定。

    未覆盖年份的工作日仍判「交易日」（True）——这是**刻意**的：
    把「未覆盖」判成休市，会在日历维护滞后时连续拦掉整年真实交易日。
    """
    cal = _write_cal(tmp_path, [2026])
    # 2027-01-04（周一）未被覆盖 → 仍判交易日（只靠周末+空表）
    assert tc.is_a_share_trading_day("2027-01-04", calendar_path=cal) is True
    # 而 2027-01-02（周六）按周末硬规则休市 —— 与覆盖度无关
    assert tc.is_a_share_trading_day("2027-01-02", calendar_path=cal) is False
    # 同一天，覆盖了就是休市
    assert tc.is_a_share_trading_day("2026-01-01", calendar_path=cal) is False


def test_missing_calendar_file_yields_empty_coverage(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    assert tc.covered_years(calendar_path=missing) == set()
    assert tc.uncovered_years([2026], calendar_path=missing) == [2026]


def test_bad_values_ignored(tmp_path: Path) -> None:
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({"closed_dates": ["2026-01-01", "abcd-ef-gh", 20260101, None]}),
                 encoding="utf-8")
    assert tc.covered_years(calendar_path=p) == {2026}


# ────────────────── 3. 守卫的「关注年度」窗口 ──────────────────
def test_years_in_scope_includes_next_year_near_boundary() -> None:
    """距年末 ≤45 天 → 当年 + 次年；否则只查当年。"""
    assert g._years_in_scope("2026-06-30") == [2026], "年中不提前查次年"
    assert g._years_in_scope("2026-11-16") == [2026, 2027], "距年末 45 天应开始查次年"
    assert g._years_in_scope("2026-12-31") == [2026, 2027]
    assert g._years_in_scope("2027-01-05") == [2027], "跨年后只查当年（次年尚远）"
    assert g._years_in_scope("bad-date") == []


def test_year_lookahead_boundary_is_exactly_45_days() -> None:
    """边界必须是 ≤45（11-16 是 12-31 前 45 天；11-17 是 44 天）。"""
    assert (datetime(2026, 12, 31).date() - datetime(2026, 11, 16).date()).days == 45
    assert 2027 in g._years_in_scope("2026-11-16")
    assert (datetime(2026, 12, 31).date() - datetime(2026, 11, 17).date()).days == 44
    assert 2027 in g._years_in_scope("2026-11-17"), "44 天同样 ≤45，仍应查次年"
    # 46 天前则不查
    assert (datetime(2026, 12, 31).date() - datetime(2026, 11, 15).date()).days == 46
    assert 2027 not in g._years_in_scope("2026-11-15")


# ────────────────── 4. 告警去重（同一年只推一次） ──────────────────
def test_calendar_gap_years_dedupes_per_year(tmp_path: Path, monkeypatch) -> None:
    cal = _write_cal(tmp_path, [2026])
    monkeypatch.setattr(tc, "CALENDAR_PATH", cal)
    monkeypatch.setattr(g, "CALENDAR_GAP_STATE", tmp_path / ".warned")

    assert g.calendar_gap_years("2026-11-16") == [2027], "首次应报次年"
    g._mark_calendar_warned([2027])
    assert g.calendar_gap_years("2026-11-16") == [], "已告警过同一年不得重复"
    assert g.calendar_gap_years("2027-01-05") == [], "跨年后仍算已告警"
    # 一年后：2027 已在状态里（抑制），2028 是新缺口年 → 只报 2028
    # （去重按**年**，不是「推过一次就永久闭嘴」，否则日历可以一直不维护）
    assert g.calendar_gap_years("2027-11-20") == [2028]
    # 状态文件丢失（重装/清理）→ 允许重新报一次，属预期（宁可多报一次）
    monkeypatch.setattr(g, "CALENDAR_GAP_STATE", tmp_path / ".warned-lost")
    assert g.calendar_gap_years("2027-11-20") == [2027, 2028]


def test_calendar_gap_years_empty_when_covered(tmp_path: Path, monkeypatch) -> None:
    cal = _write_cal(tmp_path, [2026, 2027])
    monkeypatch.setattr(tc, "CALENDAR_PATH", cal)
    monkeypatch.setattr(g, "CALENDAR_GAP_STATE", tmp_path / ".warned")
    assert g.calendar_gap_years("2026-11-16") == [], "次年已覆盖 → 不得告警"


def test_calendar_guard_kill_switch(tmp_path: Path, monkeypatch) -> None:
    """可逆开关：`CALENDAR_GUARD_ENABLED = False` → 完全不查（回滚路径）。"""
    cal = _write_cal(tmp_path, [2026])
    monkeypatch.setattr(tc, "CALENDAR_PATH", cal)
    monkeypatch.setattr(g, "CALENDAR_GAP_STATE", tmp_path / ".warned")
    monkeypatch.setattr(g, "CALENDAR_GUARD_ENABLED", False)
    assert g.calendar_gap_years("2026-12-01") == []


def test_warned_state_roundtrip_ignores_garbage(tmp_path: Path, monkeypatch) -> None:
    st = tmp_path / ".warned"
    monkeypatch.setattr(g, "CALENDAR_GAP_STATE", st)
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_text("2027\nnot-a-year\n\n2026\n", encoding="utf-8")
    assert g._calendar_warned_years() == {2026, 2027}
    g._mark_calendar_warned([2028])
    assert g._calendar_warned_years() == {2026, 2027, 2028}


# ────────────────── 5. 卡片与接线 ──────────────────
def test_calendar_card_is_plain_and_goes_to_test_group() -> None:
    """卡面必须人话（含「幽灵」机制解释 + 具体怎么补），且内部验证卡走测试群。"""
    card = g._build_calendar_card("2026-11-16", [2027])
    assert "2027" in card["title"]
    flat = json.dumps(card, ensure_ascii=False)
    assert "幽灵" in flat, "必须解释会造成幽灵交易日/幽灵归档"
    assert "closed_dates" in flat, "必须给出具体补录位置"
    assert "每年只推一次" in flat
    src = (BASE / "scripts" / "guard_eod_chain.py").read_text(encoding="utf-8")
    # 日历卡与其推卡函数必须显式走测试群（shared_rules 选群铁律）
    seg = src[src.index("def _push_calendar_card("):src.index("def _build_card(")]
    assert "feishu_webhook_test" in seg
    assert "feishu_webhook_prod" not in seg


def test_calendar_check_runs_before_all_green_early_return() -> None:
    """⛔ 接线铁律：日历守卫必须排在「全绿提前 return」**之前**。

    否则链子跑得顺的那些天会直接 return，永远走不到日历检查 ⇒ 守卫等于没接。
    """
    src = (BASE / "scripts" / "guard_eod_chain.py").read_text(encoding="utf-8")
    i_cal = src.index("calendar_gap_years(today)")
    i_green = src.index("if not missing:")
    assert i_cal < i_green, "日历守卫被放在全绿 return 之后 → 永远不触发"


def test_capture_daily_warns_but_does_not_block() -> None:
    """`capture_daily` 侧只做**日志级**告警，不得据此跳过归档（不得 return False）。"""
    src = (BASE / "scripts" / "capture_daily.py").read_text(encoding="utf-8")
    body = src[src.index("def _guard_trading_day("):src.index("def capture_eruption(")]
    assert "uncovered_years" in body, "capture_daily 应打印覆盖度告警"
    assert "⚠️ 交易日历未覆盖" in body
    # 覆盖度分支里不得出现 return False（否则等于把未覆盖当休市）
    seg = body[body.index("uncovered_years"):body.index("if not is_a_share_trading_day")]
    assert "return False" not in seg, "覆盖度告警不得阻断归档"
