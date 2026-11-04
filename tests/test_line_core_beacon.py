#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风向标（线锚）口径修复（2026-11-04 用户「基于这个风向标做联动」）。

用户原话：「这样还是利于我们看盘和观察的，因为这样子我们知道这个风向标它到底是一个
什么情况，基于这个风向标，就像我们上一次做新华传媒和新华文轩一样联动！」

## 修的两个病

### 病 1：归组键错配 → 一条线被行业拆散，永远读不到自己的风向标

11-03 实证：「短剧/互动影游」一条线 6 只票被申万行业拆成 **4 个键**：

| 票 | 申万行业（旧锚点键） | 炒作题材 |
|---|---|---|
| 大晟文化 600892 | 游戏Ⅱ | 短剧/互动影游 |
| 龙版传媒 605577 | 出版 | 短剧/互动影游 |
| 浙文影业 601599 | 纺织制造 | 短剧/互动影游 |
| 奥飞娱乐 002292 | 文娱用品 | 短剧/互动影游 |

候选按**炒作题材**归组，锚点按**申万行业**归组 → 两套键对不上。
→ 改用 `theme_snapshot.mapping` 的炒作题材归组（`ENABLE_LINE_CORE_THEME_KEY=0` 回退）。

### 病 2：选核心只看「窗口内历史最高板」→ 拿上一波的高度当当前波段的风向标

11-03 实证：文投控股窗口内曾 3 板（10-29），但 11-02 已断板重启、11-03 只是 2 板
（10:13 才封、炸 10 次、换手 23%、封单 0.20%）——却仍被选成「传媒」风向标。
次日（11-04）它低开 −1.22% → 判「破预期·这条线今天先别碰」，
而同日真正核心大晟文化是 09:25:00 一字封死 **+9.98%**（封单 9.26%、零炸板）。

→ 两种场景分开判：
  · **场景 A**（上一波核心已断、今日无票接住其高度）→ 报 `broken=True`，⛔ 不拿跟风冒充核心
  · **场景 B**（多只同日仍在板）→ 按 `sector_leader.leader_key` 在**当日**这批里选最强
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE))

from core import pool_call as pc  # noqa: E402


# ── 造数据辅助 ───────────────────────────────────────────────────────────
def _zt_row(code, name, boards, fbt="100000", seal=1.0, lt=50.0, zbc=0,
            hs=5.0, sector="游戏Ⅱ"):
    return {"code": code, "name": name, "boards": boards, "fbt": fbt,
            "seal": seal, "lt_yi": lt, "zbc": zbc, "hs": hs,
            "cje_yi": 3.0, "sector": sector, "pct": 10.0}


def _seed_zt(root: Path, day: str, rows: list[dict]) -> None:
    d = root / "cache" / "zt_history"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"zt_{day}.json").write_text(
        json.dumps({"date": day, "zt_list": rows}, ensure_ascii=False), encoding="utf-8")


def _seed_theme(root: Path, day: str, mapping: dict[str, str]) -> None:
    d = root / "cache" / "theme_snapshot"
    d.mkdir(parents=True, exist_ok=True)
    mp = {c: {"name": c, "theme": th} for c, th in mapping.items()}
    (d / f"theme_{day}.json").write_text(
        json.dumps({"date": day, "mapping": mp}, ensure_ascii=False), encoding="utf-8")


# ── 病 1：归组键用炒作题材 ───────────────────────────────────────────────
def test_theme_key_merges_industries_into_one_line(tmp_path):
    """同题材跨 4 个行业 → 必须合成 1 条线，而不是 4 个孤立锚点。"""
    _seed_zt(tmp_path, "2026-11-03", [
        _zt_row("600892", "大晟文化", 2, "092500", seal=2.49, lt=26.9, sector="游戏Ⅱ"),
        _zt_row("605577", "龙版传媒", 2, "092502", seal=1.58, lt=63.56, sector="出版"),
        _zt_row("601599", "浙文影业", 2, "092500", seal=0.06, lt=44.33, sector="纺织制造"),
        _zt_row("002292", "奥飞娱乐", 2, "093009", seal=1.22, lt=97.51, zbc=1, sector="文娱用品"),
    ])
    _seed_theme(tmp_path, "2026-11-03",
                {c: "短剧/互动影游" for c in ("600892", "605577", "601599", "002292")})

    cores = pc.load_line_cores("2026-11-03", root=tmp_path)
    drama = [k for k in cores if "短剧" in k or "影游" in k]
    assert drama == ["短剧/互动影游"], cores.keys()
    # 行业键不应再出现独立条目
    for ind in ("游戏Ⅱ", "出版", "纺织制造", "文娱用品"):
        assert ind not in cores, f"{ind} 不该单独成线"


def test_theme_key_off_falls_back_to_industry(tmp_path, monkeypatch):
    """`=0` → 回退按申万行业归组（旧行为，一条线被拆散）。

    注：行业名本身还会过一层 `SECTOR_THEME_MAP` 归并（游戏Ⅱ/出版→传媒），
    所以旧口径下这条线被拆成「传媒 / 纺织制造 / 文娱用品」等多条。
    """
    _seed_zt(tmp_path, "2026-11-03", [
        _zt_row("600892", "大晟文化", 2, "092500", sector="游戏Ⅱ"),
        _zt_row("605577", "龙版传媒", 2, "092502", sector="出版"),
        _zt_row("601599", "浙文影业", 2, "092500", seal=0.06, lt=44.33, sector="纺织制造"),
    ])
    _seed_theme(tmp_path, "2026-11-03", {c: "短剧/互动影游" for c in ("600892", "605577", "601599")})
    monkeypatch.setenv(pc.LINE_CORE_THEME_KEY_ENV, "0")
    cores = pc.load_line_cores("2026-11-03", root=tmp_path)
    # 旧口径：传媒 / 纺织制造 各自成线，短剧题材键不存在
    assert "传媒" in cores and "纺织制造" in cores
    assert "短剧/互动影游" not in cores


def test_theme_snapshot_missing_degrades_honestly(tmp_path):
    """快照缺失 → 退回行业键，⛔ 不崩、不编造。"""
    _seed_zt(tmp_path, "2026-11-03", [_zt_row("600892", "大晟文化", 2, sector="游戏Ⅱ")])
    cores = pc.load_line_cores("2026-11-03", root=tmp_path)
    assert cores["传媒"]["name"] == "大晟文化"


# ── 病 2 场景 B：同日多只在板 → 选当日最强 ────────────────────────────────
def test_same_day_picks_strongest_not_historical_max(tmp_path):
    """文投控股窗口内曾 3 板→当前 2 板；大晟文化 2 板但一字厚封零炸板。

    ⛔ 风向标必须是大晟文化（当日最强），不是文投控股（历史最高板）。
    """
    _seed_zt(tmp_path, "2026-10-29", [
        _zt_row("600715", "文投控股", 3, "093011", seal=1.0, lt=70.0, zbc=6, sector="游戏Ⅱ"),
    ])
    _seed_zt(tmp_path, "2026-11-02", [
        _zt_row("600715", "文投控股", 1, "093028", seal=0.5, lt=70.0, sector="游戏Ⅱ"),
    ])
    _seed_zt(tmp_path, "2026-11-03", [
        _zt_row("600715", "文投控股", 2, "101309", seal=0.14, lt=70.41, zbc=10, hs=23.28, sector="游戏Ⅱ"),
        _zt_row("600892", "大晟文化", 2, "092500", seal=2.49, lt=26.9, zbc=0, hs=1.26, sector="游戏Ⅱ"),
    ])
    for d in ("2026-10-29", "2026-11-02", "2026-11-03"):
        _seed_theme(tmp_path, d, {"600715": "短剧/互动影游", "600892": "短剧/互动影游"})

    core = pc.load_line_cores("2026-11-03", root=tmp_path)["短剧/互动影游"]
    assert core["name"] == "大晟文化", core
    assert core["code"] == "600892"
    assert core["boards"] == 2                 # 当日板数
    assert core["broken"] is False


def test_same_day_tie_broken_by_seal_ratio(tmp_path):
    """同为 2 板、同档封板 → 封单厚的胜。"""
    _seed_zt(tmp_path, "2026-11-03", [
        _zt_row("600892", "大晟文化", 2, "092500", seal=2.49, lt=26.9, zbc=0),
        _zt_row("605577", "龙版传媒", 2, "092502", seal=1.58, lt=63.56, zbc=0),
    ])
    _seed_theme(tmp_path, "2026-11-03", {"600892": "短剧/互动影游", "605577": "短剧/互动影游"})
    core = pc.load_line_cores("2026-11-03", root=tmp_path)["短剧/互动影游"]
    assert core["name"] == "大晟文化"          # 9.26% > 2.49%


# ── 病 2 场景 A：上一波核心已断 → broken 保持 ─────────────────────────────
def test_prev_wave_core_broken_keeps_broken_flag(tmp_path):
    """⛔ 09-21 回归：闽东电力 6 板断板，⛔ 不得因同线 4 板仍在板而把 broken 刷掉。

    同时锁 11-04 的新语义：`code/boards` = 今日在板最强（风向标），
    `broken` = 上一波更高核心已断（风险标记），两者不互相覆盖。
    """
    _seed_zt(tmp_path, "2026-09-15", [
        _zt_row("000993", "闽东电力", 4, sector="绿电"),
        _zt_row("603248", "锡华科技", 2, sector="绿电"),
    ])
    _seed_zt(tmp_path, "2026-09-16", [
        _zt_row("000993", "闽东电力", 6, sector="绿电"),
        _zt_row("603248", "锡华科技", 3, sector="绿电"),
    ])
    _seed_zt(tmp_path, "2026-09-17", [_zt_row("603248", "锡华科技", 3, sector="绿电")])
    _seed_zt(tmp_path, "2026-09-18", [_zt_row("603248", "锡华科技", 4, sector="绿电")])
    core = pc.load_line_cores("2026-09-18", root=tmp_path)["绿电"]
    assert core["broken"] is True                     # 上波核心闽东已断
    assert core["code"] == "603248"                   # 风向标＝今日在板最强
    assert core["in_pool"] is True
    assert core["beacon"]["name"] == "锡华科技"


def test_line_dead_today_has_no_beacon(tmp_path):
    """整条线今日无人涨停 → beacon=None（没有当日竞价可读），broken=True。"""
    _seed_zt(tmp_path, "2026-11-02", [_zt_row("600892", "大晟文化", 3, sector="游戏Ⅱ")])
    _seed_zt(tmp_path, "2026-11-03", [_zt_row("000001", "别的票", 1, sector="其他")])
    core = pc.load_line_cores("2026-11-03", root=tmp_path)["传媒"]
    assert core["broken"] is True
    assert core["in_pool"] is False
    assert core["beacon"] is None
    assert core["code"] == "600892"                  # 回退历史核心


# ── 真实数据锚点（11-03 候选库）────────────────────────────────────────
def test_drama_line_anchor_is_dasheng_not_wentou():
    """真实数据：短剧线风向标必须是大晟文化（一字封死 +9.98%），不是文投控股。"""
    root = BASE
    if not (root / "cache" / "zt_history" / "zt_2026-11-03.json").exists():
        pytest.skip("缺 11-03 涨停池归档")
    cores = pc.load_line_cores("2026-11-03", root=root)
    core = cores.get("短剧/互动影游")
    if core is None:
        pytest.skip("题材快照缺失（诚实降级为行业键）")
    assert core["name"] == "大晟文化", core
    assert core["broken"] is False
