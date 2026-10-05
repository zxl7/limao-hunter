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


def test_history_supply_covers_demand():
    """retain（供给）必须 ≥ 各消费方回看窗口（需求）。"""
    retain = _const("scripts/capture_daily.py", "RETAIN_DAYS")
    need = _const("scripts/live_signal.py", "N_SHAPE_HIST_DAYS")
    assert retain >= need, (
        f"涨停池保留 {retain} 交易日 < 需求 {need} 交易日 → 形态检测将缺料。"
        f"改回看窗口时必须同步 capture_daily.RETAIN_DAYS"
    )
    assert retain >= need + 3, f"保留期 {retain} 对需求 {need} 缓冲不足（建议 ≥ 需求+3）"


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
