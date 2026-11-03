#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘推演落盘/读取回归锁（P3 · 2026-10-18 用户「按顺序处理」）。

背景：接力卡 T 日 15:30 跑、复盘 T 日 17:18 才出结论，两者**从来没通过话**
（eod 只读情绪日报 verdict，不读复盘卡）。推演写进 `review_directions_<date>.json`，
次日 eod 读取做标记 + 轻量排序倾斜。

铁律（本文件锁死）：
1. 落盘必须过 schema + date 双校验（脏数据不得传染次日选股）
2. 读不到 → 静默跳过，**不编造**"复盘说了什么"
3. 标记不塞池、不改 raw_score；`review_bonus` 只进展示优先级
4. 同一票多类命中 → height > new_direction > caution
5. 代码归一：`600825.SS` / `SZ000001` → 6 位数字
"""
import importlib.util
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "review_directions", BASE / "scripts" / "review_directions.py"
)
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ENABLE_REVIEW_DIRECTIONS", raising=False)
    yield


def _payload(d="2026-10-16"):
    return rd.build_payload(
        d,
        height=[{"name": "冰山冷热", "code": "000530.SZ", "boards": 4, "note": "4进5唯一高度"}],
        new_directions=[
            {"name": "长城军工", "code": "601606", "theme": "军工", "note": "首板接棒最强"},
            {"name": "万丰奥威", "code": "002085", "theme": "低空经济"},
        ],
        cautions=[{"name": "新华传媒", "code": "600825", "note": "再异常上涨可能停牌"}],
        themes=["军工", "低空经济"],
        summary="高度看冰山冷热4进5；新方向看军工/低空首板",
    )


# ---------------- 代码归一 ----------------

@pytest.mark.parametrize("raw,expected", [
    ("600825.SS", "600825"),
    ("600825.SH", "600825"),
    ("SZ000001", "000001"),
    ("000530.sz", "000530"),
    ("601606", "601606"),
    ("", ""),
    (None, ""),
    ("abc", ""),
])
def test_normalize_code(raw, expected):
    assert rd.normalize_code(raw) == expected


# ---------------- 落盘校验 ----------------

def test_build_payload_shape():
    p = _payload()
    assert p["schema"] == rd.SCHEMA
    assert p["date"] == "2026-10-16"
    assert [x["name"] for x in p["height"]] == ["冰山冷热"]
    assert p["height"][0]["code"] == "000530", "落盘前必须归一代码"
    assert len(p["new_directions"]) == 2
    assert p["cautions"][0]["kind"] == "caution"


def test_save_and_load_roundtrip(tmp_path):
    p = _payload()
    out = rd.save_review_directions("2026-10-16", p, cache_dir=tmp_path)
    assert out is not None and out.exists()
    loaded = rd.load_review_directions("2026-10-16", cache_dir=tmp_path)
    assert loaded is not None
    assert loaded["summary"] == p["summary"]


def test_save_rejects_wrong_schema(tmp_path):
    bad = _payload()
    bad["schema"] = "something_else"
    assert rd.save_review_directions("2026-10-16", bad, cache_dir=tmp_path) is None
    assert not rd.review_path("2026-10-16", tmp_path).exists()


def test_save_rejects_date_mismatch(tmp_path):
    """⛔ 日期不符的推演绝不能落盘——否则次日会读到"别人家的推演"。"""
    p = _payload("2026-10-15")
    assert rd.save_review_directions("2026-10-16", p, cache_dir=tmp_path) is None


def test_load_missing_returns_none(tmp_path):
    assert rd.load_review_directions("2026-10-16", cache_dir=tmp_path) is None


def test_load_broken_json_returns_none(tmp_path):
    p = rd.review_path("2026-10-16", tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ 坏掉了", encoding="utf-8")
    assert rd.load_review_directions("2026-10-16", cache_dir=tmp_path) is None


# ---------------- 索引优先级 ----------------

def test_index_priority_height_over_others():
    p = _payload()
    # 同一只票同时出现在 height 与 new_directions
    p["new_directions"].append({"name": "冰山冷热", "code": "000530", "kind": "new_direction"})
    idx = rd.build_index(p)
    assert idx["000530"]["kind"] == "height", "高度证据优先"


def test_index_name_fallback_without_code():
    p = rd.build_payload("2026-10-16", new_directions=[{"name": "无代码票"}])
    idx = rd.build_index(p)
    assert idx["name:无代码票"]["kind"] == "new_direction"


def test_index_empty_input():
    assert rd.build_index(None) == {}
    assert rd.build_index({}) == {}


# ---------------- 打标 ----------------

def test_annotate_marks_hits_and_bonus():
    rows = [
        {"code": "000530", "name": "冰山冷热", "raw_score": 60.0},
        {"code": "601606", "name": "长城军工", "raw_score": 55.0},
        {"code": "600825", "name": "新华传媒", "raw_score": 58.0},
        {"code": "999999", "name": "没被提到", "raw_score": 50.0},
    ]
    stats = rd.annotate_candidates(rows, _payload())
    assert stats["available"] is True
    assert stats["height_hit"] == 1
    assert stats["new_direction_hit"] == 1
    assert stats["caution_hit"] == 1
    assert rows[0]["review_flag"] == "height"
    assert rows[1]["review_flag"] == "new_direction"
    assert rows[2]["review_flag"] == "caution"
    assert rows[0]["review_bonus"] > 0
    assert rows[2]["review_bonus"] < 0, "警示票应轻微降权"
    assert "review_flag" not in rows[3], "未命中不得写空标记（防止前端把没提显示成否）"
    assert "feature" not in rows[3]


def test_annotate_does_not_touch_raw_score():
    rows = [{"code": "000530", "name": "冰山冷热", "raw_score": 60.0}]
    rd.annotate_candidates(rows, _payload())
    assert rows[0]["raw_score"] == 60.0, "⛔ 复盘倾斜不得改 raw_score"


def test_annotate_name_fallback_matches_without_code():
    rows = [{"code": "600000", "name": "万丰奥威", "raw_score": 50.0}]
    stats = rd.annotate_candidates(rows, _payload())
    assert stats["new_direction_hit"] == 1
    assert rows[0]["review_flag"] == "new_direction"


def test_annotate_note_truncated():
    p = rd.build_payload("2026-10-16", height=[{"name": "X", "code": "600001", "note": "长" * 500}])
    rows = [{"code": "600001", "name": "X"}]
    rd.annotate_candidates(rows, p)
    assert len(rows[0]["review_note"]) == 120


def test_annotate_without_directions_is_noop():
    rows = [{"code": "600001", "name": "A"}]
    stats = rd.annotate_candidates(rows, None)
    assert stats["available"] is False
    assert rows[0] == {"code": "600001", "name": "A"}


# ---------------- 读取时机 ----------------

def test_prev_review_date_is_previous_trading_day():
    """周一 eod 应读**上周五**的复盘（不是周日）。"""
    assert rd.prev_review_date("2026-10-19") == "2026-10-16"


def test_load_for_eod_reads_prev_day(monkeypatch, tmp_path):
    p = _payload("2026-10-16")
    rd.save_review_directions("2026-10-16", p, cache_dir=tmp_path)
    monkeypatch.setattr(rd, "prev_review_date", lambda d: "2026-10-16")
    dirs, review_date = rd.load_for_eod("2026-10-19", cache_dir=tmp_path)
    assert review_date == "2026-10-16"
    assert dirs is not None and dirs["height"][0]["name"] == "冰山冷热"


def test_load_for_eod_disabled_switch(monkeypatch, tmp_path):
    rd.save_review_directions("2026-10-16", _payload("2026-10-16"), cache_dir=tmp_path)
    monkeypatch.setattr(rd, "prev_review_date", lambda d: "2026-10-16")
    monkeypatch.setenv("ENABLE_REVIEW_DIRECTIONS", "0")
    dirs, review_date = rd.load_for_eod("2026-10-19", cache_dir=tmp_path)
    assert dirs is None and review_date == ""


def test_review_bonus_direction():
    assert rd.review_bonus("height") > 0
    assert rd.review_bonus("new_direction") > 0
    assert rd.review_bonus("caution") < 0
    assert rd.review_bonus("unknown") == 0.0
    # 倾斜幅度必须小：不得盖过量价证据
    assert abs(rd.REVIEW_BONUS_HEIGHT) <= 2.0
    assert abs(rd.REVIEW_PENALTY_CAUTION) <= 2.0


def test_cli_path_mode(tmp_path, capsys):
    assert rd.main(["--path", "2026-10-16"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("review_directions_2026-10-16.json")
