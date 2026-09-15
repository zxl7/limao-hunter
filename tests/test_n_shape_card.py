#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N字二波卡渲染守卫（2026-09-14 立·治「?首板 → ? 0板连板」事故）。

事故根因：`build_n_shape_card(stock, hist, ...)` 内部 `hist.get(code)` 期望**全量表**，
而唯一调用方传的是**单票档案** → 解引用永远落空 → 卡面首板日/连板高度恒为「?」「0板」。
本测试锁死四条：
  ① 两种入参形态都必须渲染出真实首波链条（不再出现「?」与「0板连板」）；
  ② 第一波链条只认相邻交易日（中间缺料不得硬连成假连板）；
  ③ 缺数据时降级为「无首波记录」，绝不再吐「?」；
  ④ 首波起点落在窗口首日时必须标注「可能更早」（窗口截断诚实披露）。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("lsmod", ROOT / "scripts" / "live_signal.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()

# 交易日（含缺料日 09-04：该日归档为空，用于验证"不硬连"）
DAYS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
        "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"]

STOCK = {"code": "000980", "name": "众泰汽车", "pct": 9.84,
         "boards": "4天3板", "sector": "新能源汽车"}
SEC = {"zt_today": 1, "top": "·高标众泰汽车3板"}

# 09-08 首板 → 09-09 二连板 → 09-10 断板 → 今日(09-11) 再启动
PER_CODE = {"code": "000980", "name": "众泰汽车",
            "dates": ["2026-09-08", "2026-09-09"],
            "board_by_date": {"2026-09-08": 1, "2026-09-09": 2},
            "last_date": "2026-09-09", "last_boards": 2, "max_boards": 2,
            "last_sector": "汽车零部"}


def _body(card: dict) -> str:
    return card["elements"][0]["text"]["content"]


def test_full_map_input_renders_chain():
    """①全量表入参：应渲染真实首板日/连板高度/回调天数。"""
    body = _body(M.build_n_shape_card(STOCK, {"000980": PER_CODE}, SEC, "11:15", DAYS))
    assert "09-08首板" in body, body
    assert "09-09 2板连板" in body, body
    assert "断板回调1天" in body, body
    assert "?" not in body, body


def test_single_profile_input_renders_chain():
    """①单票档案入参（真实调用方形态）——事故回归点：必须与全量表等价。"""
    body = _body(M.build_n_shape_card(STOCK, PER_CODE, SEC, "11:15", DAYS))
    assert "09-08首板" in body, body
    assert "09-09 2板连板" in body, body
    assert "断板回调1天" in body, body
    assert "0板连板" not in body, body


def test_no_false_chain_across_gap():
    """②中间缺料(09-04)不得硬连：09-03 与 09-07 不连续 → 链条只取 09-07 起连通段。"""
    h = dict(PER_CODE)
    h["dates"] = ["2026-09-03", "2026-09-07", "2026-09-08"]
    h["board_by_date"] = {"2026-09-03": 2, "2026-09-07": 1, "2026-09-08": 2}
    chain = M._first_wave_chain(h, DAYS)
    assert chain["start"] == "2026-09-07", chain
    assert chain["end"] == "2026-09-08", chain
    assert chain["span"] == 2, chain
    assert chain["boards"] == 2, chain
    assert chain["gap"] == 2, chain          # 09-09、09-10 两个完整断板交易日


def test_chain_start_at_window_edge_flagged():
    """④起点=窗口首日（可能被截断）→ 诚实标注「可能更早」。"""
    h = dict(PER_CODE)
    h["dates"] = ["2026-09-01", "2026-09-02"]
    h["board_by_date"] = {"2026-09-01": 1, "2026-09-02": 2}
    chain = M._first_wave_chain(h, DAYS)
    assert chain["window_edge"] is True, chain
    body = _body(M.build_n_shape_card(STOCK, h, SEC, "11:15", DAYS))
    assert "可能更早" in body, body


def test_missing_data_degrades_without_question_mark():
    """③无数据 → 降级文案（同花顺天梯兜底，或明说窗口内无记录），绝不出现「?」。"""
    body = _body(M.build_n_shape_card(STOCK, {}, SEC, "11:15", DAYS))
    assert "?" not in body, body
    assert "0板连板" not in body, body
    assert ("无首波记录" in body or "首次启动" in body or "天梯" in body), body


def test_boards_never_zero_or_question_mark_when_chain_exists():
    """回归锁：只要有首波链条，板数位不得为 0（曾恒为「? 0板连板」）。"""
    body = _body(M.build_n_shape_card(STOCK, PER_CODE, SEC, "11:15", DAYS))
    assert " 0板" not in body, body
    assert "—" not in body.split("\n")[2], body


def test_boards_by_date_recorded_in_loader_shape():
    """链条高度取自逐日板数，而非全窗口 max_boards（防跨波段串味）。"""
    h = {"dates": ["2026-09-07", "2026-09-08"], "board_by_date": {"2026-09-07": 1, "2026-09-08": 2},
         "max_boards": 9}
    chain = M._first_wave_chain(h, DAYS)
    assert chain["boards"] == 2, chain       # 不得取到隔壁波段的 9 板
    assert chain["start_boards"] == 1, chain
