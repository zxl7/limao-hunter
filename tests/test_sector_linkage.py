# -*- coding: utf-8 -*-
"""跨板块联动检测内核（A2）回归锁。

不变量：① 必须**同刻**（跨分钟累计不算）；② 板块数下限；③ 单板块家数下限；
        ④ 输出按时刻升序；⑤ 常量与前端 timeline.ts 同值（防前后端漂移）。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("sector_linkage", SCRIPTS / "sector_linkage.py")
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)


def _row(code, sector, fbt):
    return {"code": code, "name": f"股{code}", "sector": sector, "fbt": fbt}


def test_two_sectors_three_each_same_minute():
    rows = ([_row(f"A{i}", "PCB板", "093000") for i in range(3)]
            + [_row(f"B{i}", "光通信", "093000") for i in range(3)])
    out = sl.detect_linkage(rows)
    assert len(out) == 1
    assert out[0]["at"] == "09:30" and out[0]["sector_count"] == 2
    assert {s["sector"] for s in out[0]["sectors"]} == {"PCB板", "光通信"}


def test_single_sector_is_not_linkage():
    rows = [_row(f"A{i}", "PCB板", "093000") for i in range(5)]
    assert sl.detect_linkage(rows) == [], "只有一个板块不算联动"


def test_cross_minute_accumulation_is_not_linkage():
    """同板块跨分钟累计不算联动（必须同刻）—— 这是最容易错的一条。"""
    rows = ([_row(f"A{i}", "PCB板", "093000") for i in range(3)]
            + [_row(f"B{i}", "光通信", "093500") for i in range(3)])
    assert sl.detect_linkage(rows) == [], "不同分钟的板块不得判为联动"


def test_three_sectors_detected_and_sorted():
    rows = []
    for sec in ("PCB板", "光通信", "锂电"):
        rows += [_row(f"{sec}{i}", sec, "094500") for i in range(3)]
    rows += [_row("X", "化工", "091500") for _ in range(1)]
    out = sl.detect_linkage(rows)
    assert len(out) == 1 and out[0]["sector_count"] == 3
    assert out[0]["at"] == "09:45"


def test_boundary_min_stocks():
    """2 只不够门槛（需 3 只）。"""
    rows = ([_row(f"A{i}", "PCB板", "093000") for i in range(3)]
            + [_row(f"B{i}", "光通信", "093000") for i in range(2)])
    assert sl.detect_linkage(rows) == [], "板块家数不足 3 不得判为联动"


def test_bad_and_missing_fbt_ignored():
    rows = [_row("A", "PCB板", None), _row("B", "PCB板", "99"), _row("C", "PCB板", "abc")]
    assert sl.detect_linkage(rows) == []
    assert sl.fbt_min("092500") == 9 * 60 + 25
    assert sl.fbt_min("2560") is None      # 非法时/分
    assert sl.fbt_min(None) is None


def test_empty_input():
    assert sl.detect_linkage(None) == []
    assert sl.summarize([]) == ""


def test_constants_match_frontend():
    """⛔ 常量必须与前端 timeline.ts 同值：LINKAGE_MIN_SECTORS=2 / BURST_MIN_STOCKS=3。"""
    assert sl.LINKAGE_MIN_SECTORS == 2
    assert sl.BURST_MIN_STOCKS == 3
