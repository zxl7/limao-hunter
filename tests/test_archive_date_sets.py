#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归档日集合差对账（`capture_daily.audit_archive_date_sets`）回归测试。

立此测试的审计依据（2026-10-14 数据血缘与行情口径审计 · P1-④）：

`cache/sector_flow_history/` 在 09-01~10-14 的 26 个交易日里缺 12 天，去掉目录诞生前的
09-01~09-04，**中后段仍缺 8 天**（09-21/23/24/29、10-08/09/12/10-14）；而同日 `zt_history`
（镜像侧）**一天不缺**。成因 = `sector_flow` 只在 `capture_daily` step 2.5 一条路径产出，
而 `zt` 另有 15:30（`record_zt_snapshot`）与一条 ~23:5x 的独立写入 ⇒ 盘后链没跑到 2.5 时，
**zt 有、flow 没有**的单侧缺口静默产生，无人知晓。

⛔ 处置口径 = **只告警、不回补**（契约 §1/§12「缺失不裁决、不以邻近日期代替」；
且 `cache/em_stock_sectors.json` 仅能反推 184/357 ≈ 52% 板块的名称↔BK 映射，
部分回补会让「5 日累计净流入排行」静默扭曲）。故本测试只锁「能不能把缺日说出来」。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("cdmod_ads", BASE / "scripts" / "capture_daily.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mk(d: Path, pref: str, dates) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for ds in dates:
        (d / f"{pref}{ds}.json").write_text("{}", encoding="utf-8")


def _dirs(tmp_path, monkeypatch):
    m = _load()
    zt = tmp_path / "cache" / "zt_history"
    flow = tmp_path / "cache" / "sector_flow_history"
    erupt = tmp_path / "cache" / "eruption_history"
    monkeypatch.setattr(m, "ZT_DIR", zt)
    monkeypatch.setattr(m, "FLOW_DIR", flow)
    monkeypatch.setattr(m, "ERUPT_DIR", erupt)
    return m, zt, flow, erupt


def test_symmetric_reports_ok(tmp_path, monkeypatch):
    """三侧同日齐备 → 判对称（不得报缺口）。"""
    m, zt, flow, erupt = _dirs(tmp_path, monkeypatch)
    days = ["2026-10-08", "2026-10-09", "2026-10-13"]
    _mk(zt, "zt_", days)
    _mk(flow, "sector_flow_", days)
    _mk(erupt, "eruption_", days)
    msg = m.audit_archive_date_sets()
    assert msg.startswith("archive_audit=✓对称"), msg
    assert "3日" in msg


def test_one_sided_gap_is_reported_with_dates(tmp_path, monkeypatch):
    """事故形态复现：zt 全有、flow/eruption 缺 2 天 → 必须**指名道姓**列出缺哪几天。"""
    m, zt, flow, erupt = _dirs(tmp_path, monkeypatch)
    days = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
    _mk(zt, "zt_", days)
    _mk(flow, "sector_flow_", ["2026-10-13"])
    _mk(erupt, "eruption_", ["2026-10-13"])
    msg = m.audit_archive_date_sets()
    assert msg.startswith("archive_audit=⚠️单侧缺口"), msg
    assert "flow缺3日" in msg and "eruption缺3日" in msg
    # 只显示 MM-DD、且截断时带 +N
    assert "10-08" in msg and "10-09" in msg and "10-12" in msg
    assert "只告警不回补" in msg, "口径必须写在输出里，防后来者改成回补"


def test_only_flow_side_reported_as_zt_missing(tmp_path, monkeypatch):
    """反向单侧缺口（flow 有、zt 无）同样要报（09-07~09-09 的真实形态）。"""
    m, zt, flow, erupt = _dirs(tmp_path, monkeypatch)
    _mk(zt, "zt_", ["2026-09-10"])
    _mk(flow, "sector_flow_", ["2026-09-10", "2026-09-07"])
    _mk(erupt, "eruption_", ["2026-09-10", "2026-09-07"])
    msg = m.audit_archive_date_sets()
    assert "zt缺1日[09-07]" in msg, msg


def test_window_limits_reporting(tmp_path, monkeypatch):
    """窗口 = 最近 N 个参考日：远古缺日不得天天重播（否则告警变噪声）。"""
    m, zt, flow, erupt = _dirs(tmp_path, monkeypatch)
    old = ["2026-09-%02d" % d for d in (10, 11, 14, 15, 16)]
    recent = ["2026-10-%02d" % d for d in (8, 9, 12, 13)]
    # zt 全有；flow 只补了 recent（old 全缺）
    _mk(zt, "zt_", old + recent)
    _mk(flow, "sector_flow_", recent)
    _mk(erupt, "eruption_", recent)
    msg = m.audit_archive_date_sets(window=4)
    assert "对称" in msg, f"窗口内应判对称，实得 {msg}"
    msg_all = m.audit_archive_date_sets(window=99)
    assert "flow缺5日" in msg_all, msg_all


def test_empty_dirs_do_not_crash(tmp_path, monkeypatch):
    """三目录全空 → 返回可读提示，不得抛异常（首日部署形态）。"""
    m, _zt, _flow, _erupt = _dirs(tmp_path, monkeypatch)
    assert "空" in m.audit_archive_date_sets()


def test_bad_filenames_ignored(tmp_path, monkeypatch):
    """非法命名（非 YYYY-MM-DD）不得混进对账，也不得导致崩溃。"""
    m, zt, flow, erupt = _dirs(tmp_path, monkeypatch)
    _mk(zt, "zt_", ["2026-10-13"])
    _mk(flow, "sector_flow_", ["2026-10-13"])
    _mk(erupt, "eruption_", ["2026-10-13"])
    (zt / "zt_backup.json").write_text("{}", encoding="utf-8")
    (flow / "sector_flow_2026-10-13.json.bak").write_text("{}", encoding="utf-8")
    assert "对称" in m.audit_archive_date_sets()


def test_audit_is_wired_into_main_summary():
    """本对账必须真的挂进盘后链汇总行（否则等于写了没人调的死代码）。"""
    src = (BASE / "scripts" / "capture_daily.py").read_text(encoding="utf-8")
    assert "audit_archive_date_sets()" in src
    assert "{arch_msg}" in src, "对账结果必须出现在 main() 的汇总 print 里"
    # 且必须排在 flow 写入步骤之后
    assert src.index("capture_sector_flow()") < src.index("audit_archive_date_sets()")


def test_sector_flow_empty_pool_is_not_silent():
    """`capture_sector_flow` 的全空分支原先**静默 return 0**（缺口长期不可见的成因之一），
    必须留下显式告警文案。"""
    src = (BASE / "scripts" / "capture_daily.py").read_text(encoding="utf-8")
    body_start = src.index("def capture_sector_flow(")
    body = src[body_start:body_start + 2000]
    assert "build_sector_flow_snapshot" in body
    assert "⚠️ 板块资金流快照为空" in body, "全空分支必须打印告警（禁止静默 return 0）"
