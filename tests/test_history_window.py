#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""口径守卫测试（2026-09-12 N1 修复的回归锁）

锁三件事，防 N1 那类缺陷复发：
 1. **供给 ≥ 需求**：`capture_daily.RETAIN_DAYS`（涨停池历史保留交易日数）必须 ≥
    `live_signal.N_SHAPE_HIST_DAYS`（第一波/N字/二波回看窗口）。
    原缺陷：RETAIN_DAYS=5 < 需求 12 → 盘中形态检测长期缺 7 天料（静默降级）。
 2. **单一源**：`pipeline_eod` / `theme_roll` 不得再读 `DATA_DIR/"zt_history"`
    （第二份历史），统一读 `ZT_HIST_DIR` / 项目 `cache/zt_history`。
 3. **无幽灵路径**：不得再出现 `wealth_code/cache/zt_history`（该目录从不存在，
    曾导致「弱转强」标注读空目录静默失效）。

用源码文本解析而非 import —— 避免拉起 live_signal 的重依赖与副作用。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _const(rel: str, name: str) -> int:
    txt = (ROOT / rel).read_text(encoding="utf-8")
    m = re.search(rf"^{name}\s*=\s*(\d+)", txt, re.M)
    assert m, f"{rel} 中未找到常量 {name}"
    return int(m.group(1))


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _func_body(src: str, name: str, *, span: int = 2000) -> str:
    """取 `def name` 起的一段函数体文本（源码文本守卫用，不 import）。"""
    idx = src.find(f"def {name}")
    assert idx >= 0, f"未找到 def {name}"
    tail = src[idx:]
    end = tail.find("\n\ndef ")
    return tail[: end if end > 0 else min(len(tail), span)]


# ── 回看窗口消费方登记表（2026-10-14 审计 P1-⑤）────────────────────────────
# 为什么要登记：`RETAIN_DAYS` 是**供给**，各消费方的回看窗口是**需求**。供给低于需求时
# 消费方**不会报错**，只会静默少料（N1 事故：RETAIN_DAYS=5 < 需求 12 → 形态检测缺 7 天料）。
# 因此每新增一个「按交易日回看 cache/zt_history（或同保留期产物）」的消费方，**必须在此登记**。
#
# 变更史：12 → 30（leader_cycle 读满 30 日、RETAIN_DAYS 仅 20 → 静默少 10 天）。
WINDOW_CONSUMERS: list[tuple[str, str]] = [
    ("scripts/live_signal.py",              "N_SHAPE_HIST_DAYS"),        # 第一波/N字/二波
    ("scripts/theme_rotation.py",           "REENTRY_LOOKBACK_LONG"),    # 回流确认长回看
    ("scripts/theme_rotation.py",           "TRADING_LOOKBACK"),         # 5 日轮动窗
    ("wealth_code/core/leader_cycle.py",    "HISTORY_DAYS"),             # ← 当前最大需求
    ("wealth_code/core/defense_gate.py",    "POSITION_LOOKBACK_DAYS"),   # 位置闸（零缓冲）
    ("wealth_code/core/ambush_pullback.py", "LOOKBACK_DAYS"),            # 前期热门题材
    ("wealth_code/core/reflow_scan.py",     "REFLOW_LOOKBACK_DAYS"),     # 回流候选宇宙
    ("wealth_code/core/pool_call.py",       "LINE_CORE_LOOKBACK_DAYS"),  # 主线核心
]

# 供给相对最大需求的最小缓冲（防「刚好相等」在加一天休市/补一天数据时立刻破窗）
MIN_BUFFER = 3


def test_history_supply_covers_demand():
    """retain（供给）必须 ≥ **每个**消费方回看窗口（需求），并对最大需求留缓冲。"""
    retain = _const("scripts/capture_daily.py", "RETAIN_DAYS")
    need_max, who = 0, "—"
    for rel, name in WINDOW_CONSUMERS:
        need = _const(rel, name)
        assert retain >= need, (
            f"保留 {retain} 交易日 < {rel}::{name} 需求 {need} 交易日 → 该消费方将**静默少料**。"
            f"改回看窗口时必须同步 scripts/capture_daily.py 的 RETAIN_DAYS"
        )
        if need > need_max:
            need_max, who = need, f"{rel}::{name}"
    assert retain >= need_max + MIN_BUFFER, (
        f"保留期 {retain} 对最大需求 {need_max}（{who}）缓冲不足"
        f"（要求 ≥ 需求+{MIN_BUFFER}）"
    )


def test_prune_history_prunes_mirror_symmetrically(tmp_path, monkeypatch):
    """镜像目录必须与主目录**同一保留期**裁剪（2026-10-14 审计 P1-⑥）。

    反例（修复前 `pref_map` 只有主目录）：主目录被裁到 RETAIN_DAYS、镜像目录从不裁剪
    → 同一份数据并存两套保留期（实测主 19 文件 / 镜像 40 文件）⇒ 消费方读到哪份，
    取决于它读哪个目录 —— 属**隐式口径分裂**，且镜像会无限膨胀。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cdmod_prune", ROOT / "scripts" / "capture_daily.py")
    cd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cd)

    main, mirror, daily = tmp_path / "main", tmp_path / "mirror", tmp_path / "daily"
    main.mkdir(); mirror.mkdir(); daily.mkdir()
    monkeypatch.setattr(cd, "ZT_DIR", main)
    monkeypatch.setattr(cd, "ZT_MIRROR_DIR", mirror)
    monkeypatch.setattr(cd, "ERUPT_DIR", tmp_path / "erupt")   # 不存在 → glob 为空，跳过
    monkeypatch.setattr(cd, "FLOW_DIR", tmp_path / "flow")
    monkeypatch.setattr(cd, "DAILY_DIR", daily)
    monkeypatch.setattr(cd, "RETAIN_DAYS", 3)
    monkeypatch.setattr(cd, "SHORT_TERM_RETAIN", 3)
    # ⚠️ 2026-10-14 审计 P2-⑪：prune_history 新增 cache/pulse_windows 保留期规则，
    #    **必须一并注入 tmp 路径**，否则跑测试会去裁真实 cache（隔离泄漏）。
    monkeypatch.setattr(cd, "PULSE_WINDOW_DIR", tmp_path / "pulse_windows")

    days = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"]
    for d in days:
        (main / f"zt_{d}.json").write_text("{}", encoding="utf-8")
        (mirror / f"zt_{d}.json").write_text("{}", encoding="utf-8")

    removed = cd.prune_history()
    keep = {f"zt_{d}.json" for d in days[-3:]}
    assert {p.name for p in main.glob("zt_*.json")} == keep, "主目录保留期不符"
    assert {p.name for p in mirror.glob("zt_*.json")} == keep, (
        "镜像目录未被同步裁剪 → 主/镜像保留期分裂（审计 P1-⑥ 复发）"
    )
    assert len(removed) == 4, f"应各裁 2 天 × 2 目录 = 4（实际 {removed}）"


def test_zt_history_single_source():
    """pipeline_eod / theme_roll 必须读单一源，不得回到 DATA_DIR/zt_history。"""
    for rel in ("wealth_code/pipelines/pipeline_eod.py", "wealth_code/pipelines/theme_roll.py"):
        txt = (ROOT / rel).read_text(encoding="utf-8")
        bad = re.findall(r'DATA_DIR\s*/\s*"zt_history"', txt)
        assert not bad, f"{rel} 仍引用第二份历史 DATA_DIR/\"zt_history\"（应改用 ZT_HIST_DIR）"


def test_no_ghost_zt_history_path():
    """不得再引用从不存在的 wealth_code/cache/zt_history（曾使弱转强标注静默失效）。"""
    for rel in ("wealth_code/pipelines/pipeline_eod.py", "wealth_code/pipelines/theme_roll.py"):
        txt = (ROOT / rel).read_text(encoding="utf-8")
        for m in re.finditer(r'ROOT\s*/\s*"cache"\s*/\s*"zt_history"', txt):
            raise AssertionError(f"{rel} 出现幽灵路径 wealth_code/cache/zt_history: {m.group(0)}")
    # 正例：单一源常量必须存在且指向项目 cache
    eod = (ROOT / "wealth_code/pipelines/pipeline_eod.py").read_text(encoding="utf-8")
    assert "ZT_HIST_DIR" in eod and 'ROOT.parent / "cache" / "zt_history"' in eod, \
        "pipeline_eod 缺少单一源常量 ZT_HIST_DIR"


def test_zt_history_dir_exists_and_complete():
    """数据侧体检：cache/zt_history 若存在，则**过去交易日**文件必须有非空 zt_list。

    允许目录为空（新环境冷启动）；允许今日文件为盘中快照（intraday_only·盘后链未跑属正常）；
    但不允许**过去交易日**存在「有文件却无涨停数据」的残缺日（09-04/09-07/09-11 曾如此，
    会让下游形态/N字/回流检测静默少算一天料）。
    """
    import json
    from datetime import datetime
    from common.trading_calendar import is_a_share_trading_day
    today = datetime.now().strftime("%Y-%m-%d")
    d = ROOT / "cache" / "zt_history"
    if not d.exists():
        return
    bad = []
    for p in sorted(d.glob("zt_*.json")):
        try:
            z = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            bad.append((p.name, "无法解析"))
            continue
        if str(z.get("date") or "") >= today:
            continue            # 今日/未来盘中快照：盘后链跑完前无 zt_list 属正常
        # 休市日（法定节假日/周末）本就无涨停，空 zt_list 属正常，跳过
        fname_date = p.name[len("zt_"):-len(".json")]  # zt_YYYY-MM-DD.json
        if not is_a_share_trading_day(fname_date):
            continue
        if not (z.get("zt_list") or []):
            bad.append((p.name, "zt_list 为空"))
    assert not bad, f"cache/zt_history 存在残缺历史日（缺 zt_list）: {bad}"


def test_residual_archive_self_heal(tmp_path, monkeypatch):
    """残缺日自愈（2026-09-14）：口径日匹配 → 补写 zt_list；来源不明 → 只告警不改写。

    事故：09-11 归档被 theme_series 盘中时间轴创建成 intraday_only，此后永无来源补
    → 形态/N字/回流检测永久少一天料。
    """
    import importlib.util
    import json as _json
    spec = importlib.util.spec_from_file_location("cdmod", ROOT / "scripts" / "capture_daily.py")
    cd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cd)

    monkeypatch.setattr(cd, "ZT_DIR", tmp_path)
    # 归档改为双目录同写（2026-09-18）→ 镜像目录也必须注入 tmp，
    # 否则自愈会写穿到生产 wealth_code 归档（zt_archive._guard_test_write 会当场报错）
    monkeypatch.setattr(cd, "ZT_MIRROR_DIR", tmp_path / "mirror")
    pool = [{"code": "000001", "name": "平安银行", "boards": 1}]
    f_ok = tmp_path / "zt_2026-09-11.json"
    f_ok.write_text(_json.dumps({"date": "2026-09-11", "intraday_only": True,
                                 "intraday_points": [{"window": "15:00"}]}), encoding="utf-8")
    f_old = tmp_path / "zt_2026-08-28.json"
    f_old.write_text(_json.dumps({"date": "2026-08-28", "intraday_only": True}), encoding="utf-8")

    healed = cd._heal_residual_archives("2026-09-11", pool)
    assert healed == ["zt_2026-09-11.json"], healed
    z = _json.loads(f_ok.read_text(encoding="utf-8"))
    assert z["zt_list"] == pool and z["zt_count"] == 1, z
    assert z["intraday_points"], "自愈不得丢失盘中时间轴"
    # 来源不明的残缺日：不动（禁编造）
    assert not _json.loads(f_old.read_text(encoding="utf-8")).get("zt_list")


# ── 2026-10-09 补：日历感知守卫（防「非交易日混进历史窗口」复发）─────────────
# 事故链：closed_dates 漏列 2026-09-25（中秋·周五）→ feedback_matrix 只判 weekday 未识别
# 工作日休市 → 接口回吐 09-24 的池既非空又未触发自交守卫 → capture_daily 归档出与 09-24
# 逐字段相同的幽灵日；同时长假盘中空壳（intraday_only）按「文件个数」占满 5/12 日窗名额。
def test_recorder_blocks_non_trading_day():
    """record_zt_snapshot 写盘前必须有交易日门禁（并留 --force 逃生口）。"""
    body = _func_body(_src("scripts/record_zt_snapshot.py"), "main")
    assert "is_a_share_trading_day" in body, "record_zt_snapshot 缺少交易日门禁（幽灵日复发的风险）"
    assert "--force" in body, "交易日门禁应保留 --force 逃生口（仅限确知的手工回填）"


def test_feedback_matrix_delegates_trading_day_to_calendar():
    """feedback_matrix.is_trading_day 必须委托交易日历，不得只判 weekday。"""
    body = _func_body(_src("scripts/feedback_matrix.py"), "is_trading_day")
    assert "is_a_share_trading_day" in body, (
        "feedback_matrix.is_trading_day 退回 weekday 判定 → 工作日休市（如 2026-09-25 中秋）会漏网"
    )


def test_capture_daily_has_calendar_gate():
    """capture_daily._guard_trading_day 必须前置日历硬门禁（不能只依赖上游 error 标记）。"""
    body = _func_body(_src("scripts/capture_daily.py"), "_guard_trading_day")
    assert "is_a_share_trading_day" in body, "capture_daily 缺少日历硬门禁"


def test_window_loaders_are_trading_day_aware():
    """三个「按个数取文件」的窗口都必须**先按交易日过滤、再切片**。"""
    ls = _src("scripts/live_signal.py")
    assert "is_a_share_trading_day" in _func_body(ls, "_load_zt_history"), \
        "live_signal._load_zt_history 未按交易日过滤 → 长假空壳占满 12 日窗"
    tr = _src("wealth_code/pipelines/theme_roll.py")
    assert "is_a_share_trading_day" in _func_body(tr, "_load_recent_snapshots"), \
        "theme_roll._load_recent_snapshots 未按交易日过滤 → 5 日窗混入休市日"
    trot = _src("scripts/theme_rotation.py")
    assert "is_a_share_trading_day" in _func_body(trot, "_load_zt_pool_series"), \
        "theme_rotation._load_zt_pool_series 未按交易日过滤 → 5 日窗混入休市日"


def test_theme_roll_window_counts_trading_days_only(tmp_path, monkeypatch):
    """功能回归：长假空壳不得占 5 日窗名额。"""
    import json
    import sys

    wc = str(ROOT / "wealth_code")
    if wc not in sys.path:
        sys.path.insert(0, wc)
    import pipelines.theme_roll as tr

    def _write(day: str, rows: list) -> None:
        (tmp_path / f"zt_{day}.json").write_text(
            json.dumps({"date": day, "zt_list": rows}, ensure_ascii=False), encoding="utf-8")

    for d in ("2026-10-01", "2026-10-02", "2026-10-05"):        # 国庆长假空壳
        _write(d, [])
    for d in ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"):
        _write(d, [{"code": "000001", "name": "平安银行", "boards": 1, "sector": "银行"}])

    monkeypatch.setattr(tr, "HISTORY_DIR", tmp_path)
    got = [s["date"] for s in tr._load_recent_snapshots(5)]
    assert got == ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"], got
