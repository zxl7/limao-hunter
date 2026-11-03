#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机构热议榜脚本回归（inst_hot_board.py）

⛔ 落盘隔离：build_json / compute 会写 cache/inst_hot_board_<date>.json，
   所有用例统一 monkeypatch CACHE / BLUEBOOK_DIR 到 tmp_path，
   绝不触碰生产 cache（参照 2026-10-14 market_memory 污染事故教训）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import inst_hot_board as ihb  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """把产线目录全部指向临时目录。"""
    cache = tmp_path / "cache"
    board = cache / "inst_hot_board"
    blue = cache / "alphapai_bluebook"
    for d in (cache, board, blue):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ihb, "CACHE", cache)
    monkeypatch.setattr(ihb, "BOARD_DIR", board)
    monkeypatch.setattr(ihb, "BLUEBOOK_DIR", blue)
    return tmp_path


def _board(days: dict, names: dict | None = None) -> dict:
    """days = {day: {symbol: {inst: rank}}} → 接口原始结构。

    names 用于模拟 A/H 同名（真实数据里中际旭创 A 股与 H 股 name 完全一致）。
    """
    names = names or {}
    out = []
    for day, syms in days.items():
        blocks = {}
        for sym, insts in syms.items():
            for inst, rank in insts.items():
                blocks.setdefault(inst, []).append({
                    "comboSymbol": sym,
                    "name": names.get(sym, sym.split(".")[0]),
                    "rank": rank,
                    "hotCount": 10,
                })
        out.append({
            "tradingDay": day,
            "instBoardList": [
                {"instType": k, "itemList": v} for k, v in blocks.items()
            ],
        })
    return {"code": 200000, "message": "success", "data": out}


def _write_board(payload: dict, path: Path | None = None) -> Path:
    p = path or (ihb.BOARD_DIR / "board_test.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ── 板块过滤 ───────────────────────────────────────────────────────────────
def test_board_of_prefixes():
    assert ihb.board_of("600000.SH") == "沪主板"
    assert ihb.board_of("002475.SZ") == "深主板"
    assert ihb.board_of("300750.SZ") == "创业板"
    assert ihb.board_of("688507.SH") == "科创板"
    assert ihb.board_of("430047.BJ") is None
    assert ihb.board_of("MU.US") is None


def test_tradable_rejects_star_bj_and_overseas():
    """无科创板权限 → 688 与港美股一律不进池。"""
    assert ihb.is_tradable("300308.SZ") is True
    assert ihb.is_tradable("601088.SH") is True
    assert ihb.is_tradable("688041.SH") is False
    assert ihb.is_tradable("MU.US") is False
    assert ihb.is_tradable("03308.HK") is False


# ── 连榜与共振 ─────────────────────────────────────────────────────────────
def test_days_on_board_and_inst_split():
    payload = _board({
        "2026-10-26": {"300750.SZ": {"公募": 3}},
        "2026-10-27": {"300750.SZ": {"公募": 2, "私募": 5}},
        "2026-10-28": {"300750.SZ": {"公募": 1, "私募": 4}},
    })
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    s = next(x for x in hot["stocks"] if x["code"] == "300750.SZ")
    assert s["days_on_board"] == 3
    assert s["inst"]["公募"]["days"] == 3
    assert s["inst"]["私募"]["days"] == 2
    assert s["inst"]["公募"]["best_rank"] == 1


def test_resonance_now_true_only_when_latest_day_has_three():
    """最新一日三类都在榜才算「仍在共振」，否则只算曾经共振。"""
    payload = _board({
        "2026-10-26": {"300308.SZ": {"公募": 1, "私募": 2, "保险": 3}},
        "2026-10-27": {"300308.SZ": {"公募": 1, "私募": 2, "保险": 3}},
        "2026-10-28": {"300308.SZ": {"私募": 2}},
    })
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    s = next(x for x in hot["stocks"] if x["code"] == "300308.SZ")
    assert s["resonance_days"] == 2
    assert s["resonance_last"] == "2026-10-27"
    assert s["resonance_now"] is False
    assert s["resonance_gap"] == 1


def test_resonance_now_true_when_latest_complete():
    payload = _board({
        "2026-10-27": {"300750.SZ": {"公募": 3}},
        "2026-10-28": {"300750.SZ": {"公募": 1, "私募": 2, "保险": 3}},
    })
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    s = next(x for x in hot["stocks"] if x["code"] == "300750.SZ")
    assert s["resonance_now"] is True
    assert s["resonance_last"] == "2026-10-28"


# ── 排名动量 ───────────────────────────────────────────────────────────────
def test_rank_move_direction_positive_means_forward():
    """move>0 = 排名前进（rank 数值变小）。"""
    payload = _board({
        "2026-10-27": {"300308.SZ": {"保险": 11}},
        "2026-10-28": {"300308.SZ": {"保险": 1}},
    })
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    s = next(x for x in hot["stocks"] if x["code"] == "300308.SZ")
    assert s["delta"]["保险"]["move"] == 10
    assert "↑" in ihb._delta_brief(s)


def test_rank_move_ignores_small_jitter():
    """小于 RANK_MOVE_MIN 的抖动不算升温/降温。"""
    payload = _board({
        "2026-10-27": {"300308.SZ": {"保险": 5}},
        "2026-10-28": {"300308.SZ": {"保险": 6}},
    })
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    s = next(x for x in hot["stocks"] if x["code"] == "300308.SZ")
    assert s["delta"] == {}


def test_rank_drop_marked_down():
    payload = _board({
        "2026-10-27": {"002001.SZ": {"公募": 7}},
        "2026-10-28": {"002001.SZ": {"公募": 18}},
    })
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    s = next(x for x in hot["stocks"] if x["code"] == "002001.SZ")
    assert s["delta"]["公募"]["move"] == -11
    assert "↓" in ihb._delta_brief(s)


# ── A/H 双上市 ─────────────────────────────────────────────────────────────
def test_ah_dual_recorded_not_dropped():
    """同名 A/H（中际旭创 300308.SZ + 03308.HK）按 symbol 各留，H 股记入 ah_dual。"""
    payload = _board({
        "2026-10-28": {
            "300308.SZ": {"私募": 2},
            "03308.HK": {"私募": 13},
        },
    }, names={"300308.SZ": "中际旭创", "03308.HK": "中际旭创"})
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    codes = [x["code"] for x in hot["stocks"]]
    assert "300308.SZ" in codes
    assert "03308.HK" not in codes          # 港股不入池
    a = next(x for x in hot["stocks"] if x["code"] == "300308.SZ")
    assert "03308.HK" in a["ah_dual"]       # 但作为跨市场共振被记录


def test_board_depth_warning_when_exceeds_expected():
    payload = _board({"2026-10-28": {"300750.SZ": {"公募": 21}}})
    _, _, warns = ihb.load_board(_write_board(payload))
    assert any("榜单深度" in w for w in warns)


# ── 与晨报叠加 ─────────────────────────────────────────────────────────────
def _bb(topic_pairs: list[tuple[str, list[tuple[str, str]]]]) -> dict:
    rows = []
    for topic, stocks in topic_pairs:
        rows.append({
            "topicName": topic,
            "heatLevel": 9,
            "stockTag": [{"code": c, "name": n} for n, c in stocks],
        })
    return {"code": 200000, "data": {"data": rows}}


def test_overlay_splits_three_ways():
    payload = _board({
        "2026-10-28": {
            "300750.SZ": {"公募": 1},   # 两边都有
            "601088.SH": {"保险": 2},   # 只有买方
        },
    })
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    bb = {
        "300750.SZ": {"name": "宁德时代", "topics": [{"topic": "锂电排产", "heat": 9}]},
        "002594.SZ": {"name": "比亚迪", "topics": [{"topic": "新能源车", "heat": 8}]},
    }
    ov = ihb.overlay(hot, bb)
    assert [x["code"] for x in ov["both"]] == ["300750.SZ"]
    assert [x["code"] for x in ov["buy_only"]] == ["601088.SH"]
    assert [x["code"] for x in ov["sell_only"]] == ["002594.SZ"]


def test_overlay_sell_only_excludes_untradable():
    """晨报里的科创板票不能出现在「只有卖方在讲」——用户无权限。"""
    payload = _board({"2026-10-28": {"300750.SZ": {"公募": 1}}})
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    bb = {"688041.SH": {"name": "海光信息", "topics": [{"topic": "散热", "heat": 9}]}}
    ov = ihb.overlay(hot, bb)
    assert ov["sell_only"] == []


def test_both_sorted_by_days_not_by_news_heat():
    """块一必须按在榜天数排：只上榜 1 天的不能排到 7 天前面。"""
    payload = _board({
        "2026-10-22": {"300750.SZ": {"公募": 5}, "002475.SZ": {"公募": 5}},
        "2026-10-23": {"300750.SZ": {"公募": 5}},
        "2026-10-24": {"300750.SZ": {"公募": 5}},
        "2026-10-28": {"002475.SZ": {"公募": 5}},
    })
    by_day, meta, _ = ihb.load_board(_write_board(payload))
    hot = ihb.compute(by_day, meta)
    bb = {
        "300750.SZ": {"name": "宁德时代", "topics": [{"topic": "低热度", "heat": 3}]},
        "002475.SZ": {"name": "立讯精密", "topics": [{"topic": "高热度", "heat": 10}]},
    }
    ov = ihb.overlay(hot, bb)
    assert ov["both"][0]["code"] == "300750.SZ"


# ── 落盘与卡片 ─────────────────────────────────────────────────────────────
def test_build_json_writes_to_isolated_cache(tmp_path):
    payload = _board({"2026-10-28": {"300750.SZ": {"公募": 1, "私募": 2, "保险": 3}}})
    board_path = _write_board(payload)
    out = ihb.build_json("2026-10-29", board_path, "morning")
    written = ihb.CACHE / "inst_hot_board_2026-10-29.json"
    assert written.exists()
    assert written.parent == tmp_path / "cache"
    assert out["schema"] == ihb.SCHEMA
    assert out["counts"]["stocks_tradable"] == 1
    assert any("缺当日晨报缓存" in w for w in out["warnings"])


def test_build_card_passes_required_structure():
    payload = _board({"2026-10-28": {"300750.SZ": {"公募": 1, "私募": 2, "保险": 3}}})
    board_path = _write_board(payload)
    out = ihb.build_json("2026-10-29", board_path, "morning")
    card = ihb.build_card(out, "2026-10-29", top=6)
    assert card["title"].startswith("机构在看什么")
    note = card["elements"][-1]
    assert note["tag"] == "note"
    assert isinstance(note["elements"][0]["content"], str)
    # 禁嵌套 text_tag
    blob = json.dumps(card, ensure_ascii=False)
    assert "text_tag" not in blob
    assert "column_set" not in blob


def test_topics_brief_dedups_and_caps():
    s = {"bb_topics": [{"topic": "A"}, {"topic": "A"}, {"topic": "B"}, {"topic": "C"}]}
    got = ihb._topics_brief(s, n=2)
    assert got == "A　B"
