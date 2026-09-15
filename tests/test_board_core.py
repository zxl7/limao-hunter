# -*- coding: utf-8 -*-
"""板块核心股性票识别单测（2026-09-09 用户立项 P3）。

锁定 board_core.core_rank（纯函数·不触网）：四维打分/带动反推/最高板领先/排序。
"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("board_core", SCRIPTS / "board_core.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)


def _px(code, name, boards, seal_min=92500, fund=1.0, broken=0):
    return {"code": code, "name": name, "zt_boards": boards, "industry": "测试板",
            "seal_min": seal_min, "broken": broken, "seal_fund_yi": fund}


def _pools():
    # 板A板块 3 个涨停日：X 总是先封且常是最高板；Y 总跟封
    return {
        "20260904": [_px("X", "老X", 2, seal_min=92500, fund=5.0),
                     _px("Y", "小Y", 1, seal_min=100000)],
        "20260907": [_px("X", "老X", 3, seal_min=93000, fund=5.0),
                     _px("Y", "小Y", 1, seal_min=100500)],
        "20260908": [_px("X", "老X", 4, seal_min=93000, fund=5.0),
                     _px("Y", "小Y", 2, seal_min=101000)],
    }


def test_core_rank_picks_first_sealer():
    rows = bc.core_rank("测试板", _pools(), days=5)
    by = {r["code"]: r for r in rows}
    assert len(rows) == 2
    assert by["X"]["core_score"] > by["Y"]["core_score"]   # 先封+最高板持有者=核心
    assert by["X"]["avg_order"] == 1.0                      # 均首封序位 1
    assert by["Y"]["avg_order"] == 2.0
    assert by["X"]["lead_score"] == 100.0                   # 每次都是板块最高板
    assert rows[0]["code"] == "X"                           # 降序第一位


def test_core_rank_drive_measured_by_followers():
    rows = bc.core_rank("测试板", _pools(), days=5)
    by = {r["code"]: r for r in rows}
    # X 每次身后都有 Y 跟封（当日 2 票·X 序位1 → 身后 1 票）
    assert by["X"]["drive_score"] > 0
    # 参与率：X/Y 都 3/3
    assert by["X"]["join_rate"] == 100.0
