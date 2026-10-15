# -*- coding: utf-8 -*-
"""盘中情绪时间轴记录器（2026-09-17）回归锁。

核心不变量：**同一事件只产出一次**（连跑两次第二次为空）；
**追加写不覆盖**（历史不可丢）；**任何异常不抛出**（不得影响守护链）。
隔离：全部落 tmp_path，不读生产 latest、不推送。
"""
import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("intraday_timeline", SCRIPTS / "intraday_timeline.py")
it = importlib.util.module_from_spec(spec)
spec.loader.exec_module(it)


def _snap(total=30, top5=None, leaders=None, broken=None, ld=None, decl=None, limit_up_stocks=None):
    snap = {
        "total_zt": total,
        "top5": top5 or [],
        "leaders": leaders or [],
        "broken": {"names": broken or []},
        "limit_down": {"names": ld or []},
        "sector_risk": {"declining": decl or []},
    }
    if limit_up_stocks is not None:
        snap["limit_up_stocks"] = limit_up_stocks
    return snap


def test_first_round_is_baseline_seed():
    """⛔ 缺口③根治：当天首轮只播种基线，不把开盘快照当盘中事件广播（防 00:01 假 spike）。"""
    snap = _snap(
        total=1,
        top5=[{"name": "机器人", "zt_count": 6, "leader": "科森科技"}],
        leaders=[{"code": "603626", "name": "科森科技", "sector": "机器人", "pct": 10.03, "is_20cm": False}],
        broken=["中材科技"], ld=["海南发展"],
        decl=[{"name": "白银", "pct": -6.49, "main_inflow": -6.38e8}],
        limit_up_stocks=[{"code": "603626", "name": "科森科技", "sector": "机器人", "pct": 10.03}],
    )
    evs, st = it.collect(snap, {})
    # 首轮只能有一条 baseline，绝不能有 sector_up/stock_up/broken_new/limit_down_new/sector_down
    assert len(evs) == 1 and evs[0]["kind"] == "baseline", "首轮必须只产出 baseline"
    assert st["baseline_seeded"] is True
    # 基线已把开盘已存在的票/板块种入 seen，后续不会误报"新增"
    assert "603626" in st["seen_zt"]
    assert "机器人" in st["sector_up"]
    assert st["total"] == 1
    # 第二论：相同快照不再产出任何事件（种子已覆盖）
    evs2, _ = it.collect(snap, st)
    assert evs2 == [], "基线后相同状态不得重复产出事件"


def test_same_snapshot_twice_yields_nothing():
    """⛔ 不重复：同一快照连跑两次，第二轮必须为空。"""
    snap = _snap(total=33, top5=[{"name": "机器人", "zt_count": 6}],
                 leaders=[{"code": "603626", "pct": 10.03}], broken=["A"], ld=["B"],
                 decl=[{"name": "白银", "pct": -6.49}])
    _, st = it.collect(snap, {})
    evs2, _ = it.collect(snap, st)
    assert evs2 == [], "同一状态不得重复产出事件"


def test_incremental_new_events_only():
    """增量：只有**新**涨停/新跌停/新炸板/家数上升才产出。"""
    snap = _snap(total=30, top5=[{"name": "机器人", "zt_count": 5}], leaders=[], broken=[], ld=[], decl=[])
    _, st = it.collect(snap, {})
    snap2 = _snap(total=33, top5=[{"name": "机器人", "zt_count": 6}],
                  leaders=[{"code": "000001", "pct": 10.0}], broken=["新炸"], ld=["新跌"],
                  decl=[{"name": "黄金", "pct": -5.9}])
    evs, _ = it.collect(snap2, st)
    kinds = sorted(e["kind"] for e in evs)
    assert kinds == ["broken_new", "limit_down_new", "sector_down", "sector_up", "stock_up"]
    su = next(e for e in evs if e["kind"] == "sector_up")
    assert su["delta"] == 1, "家数从 5→6 应记 delta=1"


def test_zt_total_only_when_full_sets_prove_added_stocks():
    first, state = it.collect(_snap(total=2, limit_up_stocks=[
        {"code": "A", "name": "甲"}, {"code": "B", "name": "乙"},
    ]), {})
    assert not [e for e in first if e["kind"] == "zt_total"]
    evs, _ = it.collect(_snap(total=3, limit_up_stocks=[
        {"code": "A", "name": "甲"}, {"code": "B", "name": "乙"}, {"code": "C", "name": "丙"},
    ]), state)
    total = next(e for e in evs if e["kind"] == "zt_total")
    assert total["delta"] == 1 and [s["code"] for s in total["added"]] == ["C"] and total["removed"] == []


def test_zt_total_only_when_full_sets_prove_removed_stocks():
    _, state = it.collect(_snap(total=3, limit_up_stocks=[
        {"code": "A", "name": "甲"}, {"code": "B", "name": "乙"}, {"code": "C", "name": "丙"},
    ]), {})
    evs, _ = it.collect(_snap(total=2, limit_up_stocks=[
        {"code": "A", "name": "甲"}, {"code": "C", "name": "丙"},
    ]), state)
    total = next(e for e in evs if e["kind"] == "zt_total")
    assert total["delta"] == -1 and total["added"] == [] and [s["code"] for s in total["removed"]] == ["B"]


def test_zt_total_skips_missing_or_inconsistent_sets():
    _, state = it.collect(_snap(total=2, limit_up_stocks=[
        {"code": "A", "name": "甲"}, {"code": "B", "name": "乙"},
    ]), {})
    missing, state = it.collect(_snap(total=3), state)
    assert not [e for e in missing if e["kind"] == "zt_total"]
    inconsistent, _ = it.collect(_snap(total=3, limit_up_stocks=[
        {"code": "A", "name": "甲"}, {"code": "C", "name": "丙"}, {"code": "D", "name": "丁"},
    ]), state)
    assert not [e for e in inconsistent if e["kind"] == "zt_total"]


def test_20cm_threshold_respected():
    """20cm 标的不按 9.9% 误判为涨停（+10% 的 300 票不算涨停）。

    ⚠️ 首轮已改为基线播种，个股涨停判定须在"已播种"的状态上跑差分轮。
    """
    _, st = it.collect(_snap(limit_up_stocks=[{"code": "X", "name": "基线"}]), {})
    snap = _snap(leaders=[
        {"code": "300741", "pct": 10.0, "is_20cm": True},   # 未达 19.9 → 不算
        {"code": "300939", "pct": 19.95, "is_20cm": True},  # 达标 → 算
    ])
    evs, _ = it.collect(snap, st)
    codes = [e["code"] for e in evs if e["kind"] == "stock_up"]
    assert codes == ["300939"]


def test_append_is_append_only(tmp_path, monkeypatch):
    """⛔ 追加写不得覆盖历史。"""
    monkeypatch.setattr(it, "timeline_path", lambda: tmp_path / "tl.jsonl")
    it.append_events([{"kind": "zt_total", "total": 30}], at="2026-09-17 09:31:00")
    it.append_events([{"kind": "zt_total", "total": 33}], at="2026-09-17 09:32:00")
    lines = (tmp_path / "tl.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "追加写应保留两轮历史"
    assert [json.loads(x)["at"] for x in lines] == ["2026-09-17 09:31:00", "2026-09-17 09:32:00"]


def test_append_empty_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(it, "timeline_path", lambda: tmp_path / "tl.jsonl")
    assert it.append_events([]) == 0
    assert not (tmp_path / "tl.jsonl").exists()


def test_st_filtered_out():
    """⛔ 铁律④ 不看 ST：炸板/跌停名单里的 ST/*ST 在差分轮仍被过滤，只报新增非ST。"""
    _, st = it.collect(_snap(broken=["*ST四通", "正常炸板"], ld=["ST海龙", "正常跌停"]), {})
    snap = _snap(broken=["*ST四通", "正常炸板", "新炸板"], ld=["ST海龙", "正常跌停", "新跌停"])
    evs, _ = it.collect(snap, st)
    names = sorted(e["name"] for e in evs if e["kind"] in ("broken_new", "limit_down_new"))
    assert names == ["新炸板", "新跌停"], "ST/*ST 必须剔除，且只有新增的非ST才上报"


def test_main_never_raises_on_bad_input(tmp_path, monkeypatch, capsys):
    """⛔ 守护链安全：latest 损坏也不得抛异常。"""
    bad = tmp_path / "latest.json"
    bad.write_text("{ 这不是 json", encoding="utf-8")
    monkeypatch.setattr(it, "LATEST", bad)
    assert it.main() == 0
