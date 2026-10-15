#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cache/pulse_windows 保留期回归（P2-⑪ · 2026-10-14 数据血缘审计）。

背景：`cache/pulse_windows/` 长期「无清理」式增长（实测 232 个 `window_*.json` +
450 个 `.closed_*` 防重标记 = 682 项），被审计列为「僵尸目录」。

⚠️ 但审计的「无消费者」判断**不成立**（该目录的消费方在前端仓库，不在狸猫猎手仓）：
  · 狸猫终端 `OpsPage` 经 `routes.ts` `/api/pulse/windows` 读取 `window_*.json` 存档（limit 40）；
  · `pulse_fetcher.sh` 仍在该目录落 `.closed_*` 防重标记。
⇒ 故策略是 **加保留期、不删目录**（目录与 `close_window_step` 函数都保留，
   后者是「取消注释即回滚」的可逆路径，见 pulse_fetcher.sh:140）。
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_cd():
    spec = importlib.util.spec_from_file_location(
        "cdmod_pulse", ROOT / "scripts" / "capture_daily.py")
    cd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cd)
    return cd


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """把 prune_history 涉及的全部目录注入 tmp → 绝不触碰真实 cache。"""
    cd = _load_cd()
    pdir = tmp_path / "pulse_windows"
    pdir.mkdir()
    for attr, sub in (("ZT_DIR", "zt"), ("ZT_MIRROR_DIR", "zt_mirror"),
                      ("ERUPT_DIR", "erupt"), ("FLOW_DIR", "flow"), ("DAILY_DIR", "daily")):
        monkeypatch.setattr(cd, attr, tmp_path / sub)
    monkeypatch.setattr(cd, "PULSE_WINDOW_DIR", pdir)
    monkeypatch.setattr(cd, "PULSE_WINDOW_RETAIN", 3)
    monkeypatch.setattr(cd, "PULSE_MARK_RETAIN_DAYS", 10)
    return cd, pdir


def _mk_windows(d, keys):
    for k in keys:                       # k 形如 '20260903_1455'
        (d / f"window_{k}.json").write_text("{}", encoding="utf-8")


def _mk_marks(d, dates, suffix=""):
    out = []
    for day in dates:
        p = d / f".closed_{day}_1455{suffix}"
        p.write_text("", encoding="utf-8")
        out.append(p)
    return out


def test_retention_keeps_newest_n_windows(iso):
    cd, d = iso
    keys = [f"2026090{i}_1455" for i in range(1, 8)]      # 20260901..20260907（7 份）
    _mk_windows(d, keys)
    cd.prune_history()
    kept = sorted(p.name for p in d.glob("window_*.json"))
    assert kept == [f"window_2026{i}_1455.json" for i in
                    ("0901", "0902", "0903")] or len(kept) == 3, kept
    # 保留的必须是**最新 3 份**（字典序即时间序）
    assert kept[-1].endswith("20260907_1455.json"), kept


def test_retention_removes_old_markers_and_keeps_fresh(iso, monkeypatch):
    cd, d = iso
    from datetime import datetime, timedelta
    today = datetime.now()
    old = (today - timedelta(days=40)).strftime("%Y%m%d")
    fresh = (today - timedelta(days=1)).strftime("%Y%m%d")
    _mk_marks(d, [old])
    _mk_marks(d, [fresh])
    cd.prune_history()
    names = sorted(p.name for p in d.glob(".closed_*"))
    assert names == [f".closed_{fresh}_1455"], names


def test_marker_date_boundary_is_inclusive(iso):
    """正好 PULSE_MARK_RETAIN_DAYS 天的标记保留（cutoff 用 `<` 比较）。"""
    cd, d = iso
    from datetime import datetime, timedelta
    edge = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
    _mk_marks(d, [edge])
    cd.prune_history()
    assert [p.name for p in d.glob(".closed_*")] == [f".closed_{edge}_1455"]


def test_s15_marker_suffix_also_pruned(iso):
    cd, d = iso
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    _mk_marks(d, [old], suffix="_s15")
    cd.prune_history()
    assert not list(d.glob(".closed_*"))


def test_malformed_marker_name_is_skipped_not_deleted(iso):
    """非法命名不得误删（保守放过）。"""
    cd, d = iso
    weird = d / ".closed_weird_name"
    weird.write_text("", encoding="utf-8")
    short = d / ".closed_2026090"
    short.write_text("", encoding="utf-8")
    cd.prune_history()
    assert weird.exists(), "非法命名被误删"
    assert short.exists(), "长度不足的日期段被误删"


def test_directory_is_never_removed(iso):
    """⛔ 目录本身必须保留（前端读取 + pulse_fetcher 落标记）。"""
    cd, d = iso
    cd.prune_history()
    assert d.is_dir()


def test_missing_directory_does_not_raise(monkeypatch, tmp_path):
    cd = _load_cd()
    for attr, sub in (("ZT_DIR", "zt"), ("ZT_MIRROR_DIR", "zm"), ("ERUPT_DIR", "e"),
                      ("FLOW_DIR", "f"), ("DAILY_DIR", "dl")):
        monkeypatch.setattr(cd, attr, tmp_path / sub)
    monkeypatch.setattr(cd, "PULSE_WINDOW_DIR", tmp_path / "nope")
    assert cd.prune_history() == []


def test_idempotent(iso):
    cd, d = iso
    _mk_windows(d, [f"2026090{i}_1455" for i in range(1, 8)])
    cd.prune_history()
    assert cd.prune_history() == []


def test_retain_is_at_least_frontend_route_limit():
    """保留份数必须 ≥ 前端路由 limit（routes.ts `/api/pulse/windows` = 40）。

    若哪天调低本常量到 40 以下，OpsPage 的「最新 N 份」会出现空洞 —— 这条锁提醒同步。
    """
    cd = _load_cd()
    assert cd.PULSE_WINDOW_RETAIN >= 40, (
        f"PULSE_WINDOW_RETAIN={cd.PULSE_WINDOW_RETAIN} < 前端路由 limit 40")


def test_rule_is_wired_into_prune_history():
    """契约锁：prune_history 源码里必须真的出现 pulse_windows 裁剪段。"""
    txt = (ROOT / "scripts" / "capture_daily.py").read_text(encoding="utf-8")
    assert "PULSE_WINDOW_DIR" in txt and 'glob("window_*.json")' in txt
    assert ".closed_*" in txt


def test_audit_claim_corrected_in_report():
    """审计报告 §九 的「无消费者」结论已就地更正（否则本清理会误导后来人）。

    断言「更正痕迹」存在而非「原词消失」——勘误需**引用原句**才可追溯，故原文必然保留。
    """
    txt = (ROOT / "架构" / "数据血缘与行情口径审计_2026-10-14.md").read_text(encoding="utf-8")
    sec9 = txt.split("## 九、僵尸产物与目录卫生")[1].split("## 十、")[0]
    assert "pulse_windows" in sec9
    assert "勘误" in sec9, "§九 未标注勘误"
    assert "该判断不成立" in sec9, "§九 未明示「无消费者」判断已被证伪"
    assert "OpsPage" in sec9, "§九 未指名真实消费者（前端 OpsPage）"
