#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""涨停池归档单一写入源回归锁（2026-09-18 治「双路径分裂」）。

事故：`record_zt_snapshot.py` 只写 `wealth_code/data/zt_history/`，`capture_daily.py`
只写 `cache/zt_history/`，而消费方（theme_roll / live_signal / decision_audit）读 cache
→ 15:30 财富码 eod 读到的是**盘中空壳**（有 intraday_points、无 zt_list）→
theme_roll 当日 `zt_count=0`（题材广度静默失真）。

本测试锁定：
  ① 双目录**同写**（一份快照 → 两个目录内容一致）；
  ② `cache` 侧**合并保留** `intraday_points`（只能补，不能把盘中产物覆盖掉）；
  ③ 自愈 `ensure_today_archive` 四道守卫：已有料 ok / 15:00 前不动 / 非今日不补 /
     **自交守卫**（与前一日完全同池 = 节假日返回上一交易日池 → 拒绝写入）；
  ④ 契约：盘后两处写盘必须走 `zt_archive.write_archives`（防第三种分裂形态复发）。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import zt_archive as ZA          # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")


def _snap(date10: str, codes, extra=None):
    d = {"date": date10, "zt_count": len(codes), "tc": len(codes),
         "zt_list": [{"code": c, "name": f"票{c}", "boards": 1, "sector": "半导体"} for c in codes]}
    if extra:
        d.update(extra)
    return d


def _use_tmp_dirs(monkeypatch, tmp_path):
    a = tmp_path / "cache_zt"
    b = tmp_path / "wc_zt"
    monkeypatch.setattr(ZA, "ZT_DIRS", (a, b))
    return a, b


# ── ① 双目录同写 ────────────────────────────────────────────────────────

def test_write_archives_writes_both_dirs(monkeypatch, tmp_path):
    a, b = _use_tmp_dirs(monkeypatch, tmp_path)
    paths = ZA.write_archives(_snap("2026-09-18", ["000001", "600000"]))
    assert len(paths) == 2, paths
    assert {p.parent for p in paths} == {a, b}
    da = json.loads((a / "zt_2026-09-18.json").read_text(encoding="utf-8"))
    db = json.loads((b / "zt_2026-09-18.json").read_text(encoding="utf-8"))
    assert da == db
    assert len(da["zt_list"]) == 2


def test_write_archives_rejects_snapshot_without_date(monkeypatch, tmp_path):
    a, b = _use_tmp_dirs(monkeypatch, tmp_path)
    try:
        ZA.write_archives({"zt_list": [{"code": "000001"}]})
    except ValueError:
        return
    raise AssertionError("缺 date 的快照必须拒绝归档（否则文件名无从确定）")


# ── ② 合并保留 intraday_points ──────────────────────────────────────────

def test_write_archives_preserves_intraday_points(monkeypatch, tmp_path):
    """盘后补 zt_list 时，盘中时间轴写的 intraday_points **不得被覆盖**。"""
    a, b = _use_tmp_dirs(monkeypatch, tmp_path)
    a.mkdir(parents=True, exist_ok=True)
    (a / "zt_2026-09-18.json").write_text(json.dumps(
        {"date": "2026-09-18", "intraday_points": [{"t": "10:00"}], "zt_count": 0}),
        encoding="utf-8")
    ZA.write_archives(_snap("2026-09-18", ["000001"]))
    got = json.loads((a / "zt_2026-09-18.json").read_text(encoding="utf-8"))
    assert got["intraday_points"] == [{"t": "10:00"}], got
    assert len(got["zt_list"]) == 1


def test_write_archives_does_not_merge_dates_from_other_day(monkeypatch, tmp_path):
    """只有**同一天**的文件才合并；跨日文件不得被牵连。"""
    a, b = _use_tmp_dirs(monkeypatch, tmp_path)
    a.mkdir(parents=True, exist_ok=True)
    (a / "zt_2026-09-17.json").write_text(json.dumps(
        {"date": "2026-09-17", "intraday_points": [{"t": "09:35"}]}), encoding="utf-8")
    ZA.write_archives(_snap("2026-09-18", ["000001"]))
    got = json.loads((a / "zt_2026-09-18.json").read_text(encoding="utf-8"))
    assert "intraday_points" not in got


# ── ③ 自愈守卫 ──────────────────────────────────────────────────────────

def test_ensure_ok_when_pool_already_present(monkeypatch, tmp_path):
    """当日归档已有 zt_list → 直接 ok，**不得触网**。"""
    a, b = _use_tmp_dirs(monkeypatch, tmp_path)
    a.mkdir(parents=True, exist_ok=True)
    (a / f"zt_{TODAY}.json").write_text(json.dumps(_snap(TODAY, ["000001"])), encoding="utf-8")
    import record_zt_snapshot as rz
    monkeypatch.setattr(rz, "build_snapshot", lambda d: (_ for _ in ()).throw(
        AssertionError("已有料时不该抓取")))
    assert ZA.ensure_today_archive(now_hour=16) == "ok"


def test_ensure_skips_before_close(monkeypatch, tmp_path):
    """15:00 前一律不动（盘中池不完整，抓了就是污染）。"""
    _use_tmp_dirs(monkeypatch, tmp_path)
    assert ZA.ensure_today_archive(now_hour=10) == "before-close"


def test_ensure_skips_non_today_without_backfill(monkeypatch, tmp_path):
    """默认只补**今天**：显式传历史日期（未开 allow_backfill）必须拒绝。"""
    _use_tmp_dirs(monkeypatch, tmp_path)
    assert ZA.ensure_today_archive(today="2026-01-01", now_hour=16) == "skip-non-today"


def test_ensure_selfsame_guard_blocks_holiday_fake_pool(monkeypatch, tmp_path):
    """自交守卫：新抓池与前一个归档**代码集完全相同** → 拒绝写入（节假日假数据）。"""
    import record_zt_snapshot as rz
    a, b = _use_tmp_dirs(monkeypatch, tmp_path)
    a.mkdir(parents=True, exist_ok=True)
    (a / "zt_2026-09-17.json").write_text(json.dumps(_snap("2026-09-17", ["000001", "600000"])),
                                         encoding="utf-8")
    monkeypatch.setattr(rz, "build_snapshot", lambda d: _snap(d, ["000001", "600000"]))
    act = ZA.ensure_today_archive(now_hour=16)
    assert act.startswith("skip-selfsame"), act
    assert not (a / f"zt_{TODAY}.json").exists(), "同池不得写入归档"


def test_ensure_fills_and_writes_both_dirs(monkeypatch, tmp_path):
    """缺料 + 收盘后 + 与前日不同池 → 抓取并**双目录**写入。"""
    import record_zt_snapshot as rz
    a, b = _use_tmp_dirs(monkeypatch, tmp_path)
    a.mkdir(parents=True, exist_ok=True)
    (a / "zt_2026-09-17.json").write_text(json.dumps(_snap("2026-09-17", ["000001"])),
                                         encoding="utf-8")
    monkeypatch.setattr(rz, "build_snapshot", lambda d: _snap(d, ["000001", "300001", "600000"]))
    act = ZA.ensure_today_archive(now_hour=16)
    assert act == "filled(3)", act
    for d in (a, b):
        assert (d / f"zt_{TODAY}.json").exists(), d


def test_ensure_failure_is_non_fatal(monkeypatch, tmp_path):
    """抓取异常不得抛出（避免拖垮 eod 主链）→ 返回 fetch-failed 描述。"""
    import record_zt_snapshot as rz
    _use_tmp_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(rz, "build_snapshot", lambda d: (_ for _ in ()).throw(RuntimeError("net")))
    act = ZA.ensure_today_archive(now_hour=16)
    assert act.startswith("fetch-failed"), act


# ── ④ 单一写入源契约 ────────────────────────────────────────────────────

def test_zt_writers_go_through_archive_module():
    """盘后两处写盘必须 import zt_archive（防第三种分裂形态复发）。"""
    for rel in ("scripts/record_zt_snapshot.py", "scripts/capture_daily.py"):
        txt = (ROOT / rel).read_text(encoding="utf-8")
        assert "zt_archive" in txt and "write_archives" in txt, rel


# ── ⑤ 写穿防线（2026-09-18 血案）────────────────────────────────────────

def test_guard_blocks_production_write_under_pytest():
    """⛔ 测试期未注入 `dirs=` 时，写生产归档必须**当场报错**，不得静默写穿。

    血案：`_heal_residual_archives` 的测试把 `cd.ZT_DIR` 注入 tmp，但自愈改走本模块后
    仍走模块默认目录 → 夹具里的 1 只假票被写进真实 `cache/zt_history/zt_2026-09-11.json`
    （真 40 只数据被覆盖，只能靠数据源回补 + 晋级归档交叉验证才恢复）。
    """
    try:
        ZA.write_archives({"date": "2026-09-11", "zt_list": [{"code": "000001"}]})
    except RuntimeError as e:
        assert "禁止写生产归档" in str(e), e
        return
    raise AssertionError("未注入 dirs 时写生产归档未被拦截 —— 防线失效")


def test_guard_allows_injected_dirs(tmp_path):
    """显式注入 dirs → 正常写入（防线不误伤测试自身）。"""
    p = ZA.write_archives({"date": "2026-09-11", "zt_list": [{"code": "000001"}]},
                          dirs=(tmp_path,))
    assert p and p[0].exists()
