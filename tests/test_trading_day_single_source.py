#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""⛔ 交易日判定「唯一真源」守卫（2026-10-09 用户「交易日的判定一定要唯一」· 立）。

铁律：**「某一天是不是A股交易日」只允许 `common/trading_calendar` 回答。**
任何模块不得再用 `weekday() < 5` / `weekday() >= 5` 之类**自行判定交易日**。

## 事故链（本测试的由来）
1. `config/a_share_calendar.json` 的 `open_dates` 把 6 个**周末**调休补班日当开市日
   （含 2026-10-10 周六）→ 盘后 AI 预案视图被写成 `ai_view_2026-10-10.json`，
   而周一竞价卡只读 `ai_view_2026-10-12.json` ⇒ **AI 判断整段消失**。
2. `closed_dates` 漏列 2026-09-25（中秋·周五休市）→ 而 `common/market_time`、
   `feedback_matrix`、`system_health`、`backtest_engine` 等 **5+ 处各自用 `weekday()`**
   判断 → 全部判错 → 接口回吐 09-24 的完整池 → 归档出与 09-24 **逐字段相同**的幽灵日，
   并派生 `market_memory_2026-10-05/06/07`、`dragon_locks_2026-10-01/02/05` 等长假幽灵产物。

## 两条守卫
- **A. 静态扫描**：全仓 `.py` 里凡「`.weekday()` + 比较运算符」必须带
  `trading-day-fallback` 标记（仅限 `except` 降级兜底），否则 FAIL。白名单见 `ALLOWED_FILES`。
- **B. 具名函数委托锁**：下列函数必须在其函数体内出现 `is_a_share_trading_day`——
  防止有人把已经收敛好的判定再退回 `weekday()`。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SKIP_DIRS = {"cache", ".git", "node_modules", "outputs", ".workbuddy",
             "venv", ".venv", "__pycache__", "dist", "build"}

# `.weekday()` + 比较运算符 —— 唯一允许存在的白名单（文件级，须写明理由）
ALLOWED_FILES = {
    # 唯一真源内部：周末硬规则就是这里实现的，此处**必须**有 weekday 比较
    "common/trading_calendar.py": "唯一真源本体：周末恒休市的硬规则实现处",
    # 美股隔夜收盘的周末回退（_us 是美东日期，**不是A股交易日判定**）
    "wealth_code/pipelines/build_cards.py": "外盘联动取美东隔夜收盘，非A股交易日判定",
}

COMPARE_RE = re.compile(r"\.weekday\(\)\s*(<|<=|>|>=|==|!=)")
MARKER = "trading-day-fallback"

# ── B. 必须委托唯一真源的具名函数（函数体须出现 is_a_share_trading_day）──────────
DELEGATING = [
    ("common/market_time.py", "is_trading_day"),
    ("scripts/short_spirit.py", "is_trading_day"),
    ("scripts/live_signal.py", "in_early_realtime_window"),
    ("scripts/live_signal.py", "_load_zt_history"),
    ("scripts/schedule_sentinel.py", "in_trading"),
    ("scripts/intraday_beat.py", "in_trading"),
    ("scripts/newsflash_express.py", "in_trading"),
    ("scripts/intraday_pulse.py", "in_trading"),
    ("scripts/feedback_matrix.py", "is_trading_day"),
    ("scripts/capture_daily.py", "_guard_trading_day"),
    ("scripts/link_guard.py", "_in_session"),
    ("scripts/backtest_engine.py", "is_trading_day"),
    ("scripts/realtime_analytics.py", "_session_open"),
    ("scripts/system_health.py", "_is_trading_day"),
    ("scripts/push_card.py", "resolve_webhook_key"),
    ("scripts/record_zt_snapshot.py", "main"),
    ("scripts/theme_rotation.py", "_load_zt_pool_series"),
    ("wealth_code/pipelines/theme_roll.py", "_load_recent_snapshots"),
    ("wealth_code/core/leader_cycle.py", "_is_trading"),
    ("common/theme_series.py", "record_intraday_point"),
]


def _py_files() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*.py")):
        if set(p.parts) & SKIP_DIRS:
            continue
        if "tests" in p.parts:      # 测试自身允许构造任意日期
            continue
        out.append(p)
    return out


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _func_body(src: str, name: str) -> str:
    """截取模块级（或缩进）函数 `name` 的函数体；找不到返回空串。"""
    m = re.search(rf"^[ \t]*def {re.escape(name)}\(", src, re.M)
    if not m:
        return ""
    tail = src[m.end():]
    nxt = re.search(r"^(?:def |class |@)", tail, re.M)
    return src[m.start(): m.end() + (nxt.start() if nxt else len(tail))]


# ── A. 静态扫描 ──────────────────────────────────────────────────────────
def test_no_ad_hoc_trading_day_judgment():
    """全仓不得出现白名单外的「`.weekday()` + 比较」自行交易日判定。"""
    offenders = []
    for p in _py_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel in ALLOWED_FILES:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if COMPARE_RE.search(line) and MARKER not in line:
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, (
        "⛔ 检出自行实现交易日判定的代码（必须改为调用 "
        "common.trading_calendar.is_a_share_trading_day；"
        "若确属 except 降级兜底，请标注 `# trading-day-fallback`）：\n  "
        + "\n  ".join(offenders)
    )


def test_fallback_marker_is_only_used_in_except_branches():
    """`trading-day-fallback` 标记只允许出现在 `except` 降级分支里（防滥用逃逸）。

    只审 **真正的 `weekday()` 比较代码行**——文档/注释里提及标记名不算（否则会误伤
    `common/trading_calendar.py` 的模块说明）。
    """
    bad = []
    for p in _py_files():
        rel = p.relative_to(ROOT).as_posix()
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            if MARKER not in line or not COMPARE_RE.search(line):
                continue        # 非 weekday 比较行（文档/注释）不参与本判据
            # 向上 7 行内必须能找到 except
            window = "\n".join(lines[max(0, i - 8):i])
            if "except" not in window and "except" not in line:
                bad.append(f"{rel}:{i}: {line.strip()}")
    assert not bad, (
        "⛔ `trading-day-fallback` 只能用于 `except` 降级兜底，以下位置不在 except 分支：\n  "
        + "\n  ".join(bad)
    )


# ── B. 具名函数委托锁 ────────────────────────────────────────────────────
@pytest.mark.parametrize("rel,name", DELEGATING)
def test_function_delegates_to_single_source(rel: str, name: str):
    """`name` 必须**直接**调用唯一真源，或**委托本模块的薄封装**（一层）。

    允许薄封装的理由：`is_trading_day()` / `_is_trading_day()` 这类本地 helper
    承载了 `except` 降级兜底，形态更清晰。但**兜底之外的判定**最终必须落到
    `is_a_share_trading_day`——否则就是「把自实现藏进 helper」的逃逸，判 FAIL。
    """
    src = _src(rel)
    body = _func_body(src, name)
    assert body, f"{rel} 未找到函数 {name}"
    if "is_a_share_trading_day" in body:
        return
    for callee in set(re.findall(r"\b(_?[A-Za-z_][A-Za-z0-9_]*)\s*\(", body)):
        helper = _func_body(src, callee)
        if helper and "is_a_share_trading_day" in helper:
            return
    raise AssertionError(
        f"⛔ {rel}::{name} 未（直接或经本地薄封装）委托唯一真源 is_a_share_trading_day——"
        f"交易日判定退回自实现会让「工作日休市/调休补班日」重新漏网。"
    )


def test_market_time_is_a_thin_wrapper():
    """`common/market_time` 只能转发，不得自带「周一到周五」式实现。"""
    body = _func_body(_src("common/market_time.py"), "is_trading_day")
    assert "trading_calendar" in body, "market_time.is_trading_day 未转发唯一真源"
    # 主路径不得出现裸的 weekday 判定（降级兜底行带 marker 才放行）
    for i, line in enumerate(body.splitlines(), 1):
        if COMPARE_RE.search(line):
            assert MARKER in line, f"market_time.is_trading_day 主路径含自实现判定：{line.strip()}"
    it_body = _func_body(_src("common/market_time.py"), "in_trading")
    assert "is_trading_day" in it_body, "market_time.in_trading 未复用交易日判定"


def test_no_module_reads_calendar_config_directly():
    """`config/a_share_calendar.json` 只允许唯一真源读（防再长出第二套日历解析）。"""
    offenders = []
    for p in _py_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel == "common/trading_calendar.py":
            continue
        if "a_share_calendar" in p.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert not offenders, (
        "⛔ 以下模块直接读日历配置，应改为调用 common.trading_calendar：\n  "
        + "\n  ".join(offenders)
    )


# ── C. 配置纪律 + 行为锁 ────────────────────────────────────────────────
def test_calendar_config_has_no_open_dates():
    """`open_dates` 必须为空（周末恒休市，不接受「调休补班开市」）。"""
    doc = json.loads((ROOT / "config" / "a_share_calendar.json").read_text(encoding="utf-8"))
    assert not doc.get("open_dates"), (
        "⛔ open_dates 非空 —— 它会把周末调休补班日当A股开市日（2026-10-10 事故根因）"
    )


def test_calendar_config_closed_dates_include_mid_autumn():
    """`closed_dates` 必须含 2026-09-25（中秋·周五休市，幽灵日事故的另一半）。"""
    doc = json.loads((ROOT / "config" / "a_share_calendar.json").read_text(encoding="utf-8"))
    assert "2026-09-25" in (doc.get("closed_dates") or [])


def test_behavior_weekend_and_holiday_are_not_trading_days():
    """行为锁：调休补班周末与工作日休市一律非交易日；market_time 同判。"""
    from common.market_time import is_trading_day as mt
    from common.trading_calendar import is_a_share_trading_day as cal

    cases = [
        ("2026-10-10", False),   # 周六·调休补班（A股仍休市）
        ("2026-09-20", False),   # 周日·调休补班
        ("2026-09-25", False),   # 周五·中秋休市
        ("2026-10-05", False),   # 周一·国庆休市
        ("2026-10-09", True),    # 周五·正常交易日
        ("2026-10-12", True),    # 周一·正常交易日
    ]
    for d, expect in cases:
        assert cal(d) is expect, f"trading_calendar 判 {d} 应为 {expect}"
        assert mt(datetime.strptime(d, "%Y-%m-%d").replace(hour=10)) is expect, \
            f"market_time 判 {d} 应为 {expect}（委托失效？）"
