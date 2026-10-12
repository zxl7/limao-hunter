# -*- coding: utf-8 -*-
"""题材归并巡检「哨兵」（机制4·2026-10-12 传媒事故防御层）回归锁。

⚠️ 设计要点（实测教训）：简单「core_theme 归线 ≠ sector 归线」布尔告警**误报 62%**
（core_theme=炒作题材 与 sector=行业 天然可不同线）。故只报**高置信跨大类错配**
（如 传媒票被归进「AI算力」），并产出归并快照供排查。

锁定：
  ① 真异常（传媒行业票被 core_theme 误标 AI算力）→ 报；
  ② legit 父子题材（固态电池 vs 新能源）→ 不报；
  ③ 同线（AI算力 + 半导体）→ 不报；
  ④ snapshot 按线族归组。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "wealth_code")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

spec = importlib.util.spec_from_file_location("tla", ROOT / "scripts" / "theme_line_audit.py")
tla = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tla)


def _c(code, name, ct, sec):
    return {"code": code, "name": name, "core_theme": ct, "sector": sec}


def test_high_risk_media_misclassified_as_ai():
    """① 10-12 事故形态：core_theme 误标「AI算力」但 sector=数字媒体(→传媒) → 报。"""
    r = tla.find_high_risk([_c("300364", "中文在线", "AI算力", "数字媒体")])
    assert len(r) == 1 and r[0]["code"] == "300364"
    assert r[0]["by_theme"] == "AI算力" and r[0]["by_sector"] == "传媒"


def test_legit_parent_child_not_reported():
    """② legit：core_theme 是 sector 的细分题材（固态电池 ⊆ 新能源）→ 不报。"""
    assert tla.find_high_risk([_c("002805", "丰元股份", "固态电池", "电池")]) == []


def test_legit_same_line_not_reported():
    """③ core_theme 与 sector 归到同线（AI算力 + 半导体）→ 不报。"""
    assert tla.find_high_risk([_c("X", "真算力", "AI算力", "半导体")]) == []


def test_missing_fields_skipped():
    """空 core_theme / 空 sector / 「—」→ 跳过（诚实降级）。"""
    assert tla.find_high_risk([_c("1", "A", "", "数字媒体"),
                               _c("2", "B", "—", "数字媒体"),
                               _c("3", "C", "AI算力", "")]) == []


def test_snapshot_groups_by_line():
    """④ 快照按线族归组（数字媒体/出版 都→ 传媒）。"""
    snap = tla.snapshot([_c("1", "A", "", "数字媒体"), _c("2", "B", "", "出版")])
    assert "传媒" in snap and len(snap["传媒"]) == 2
