# -*- coding: utf-8 -*-
"""`scripts/relay_winrate.py` 统计口径回归锁（2026-11-02 用户「高胜率优先」）。

锁的是**口径**，不是数字：
  ① Wilson 下界必须惩罚小样本（3 中 3 不能等同于 300 中 300）
  ② 胜率定义：严格 >0 才算赢，0 平局不算赢（防「持平也算对」的自欺）
  ③ **可执行口径必须排除高开>5%** —— 这是本模块唯一的口径铁律：
     混进买不到的样本会把「封板质量好」算成亏钱，得出「封板越差越好」的荒谬结论。
  ④ 晋级因子四条与 `board_track.py` 同阈值（早封≤570 / 封厚≥3 / 换手<10 / 零炸板）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "wealth_code", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import relay_winrate as rw  # noqa: E402


# ── ① Wilson 下界 ────────────────────────────────────────────────────
def test_wilson_penalises_small_sample():
    """3 中 3 的下界必须远低于 300 中 300 —— 小样本不许吹 100%。"""
    small = rw.wilson_lower(3, 3)
    big = rw.wilson_lower(300, 300)
    assert small is not None and big is not None
    assert small < 45, f"3/3 下界应被压得很低，实际 {small}"
    assert big > 95, f"300/300 下界应接近 100%，实际 {big}"
    assert small < big


def test_wilson_edge_cases():
    assert rw.wilson_lower(0, 0) is None
    assert rw.wilson_lower(0, 10) == 0.0
    # 一半一半 → 下界应显著低于 50
    half = rw.wilson_lower(50, 100)
    assert half is not None and 30 < half < 45


# ── ② 胜率定义 ──────────────────────────────────────────────────────
def test_summarize_zero_is_not_a_win():
    """0% 平局不算赢（宁可保守，也别把「不涨不跌」当成功）。"""
    st = rw.summarize([2.0, 0.0, -1.0, 0.0])
    assert st["n"] == 4
    assert st["win"] == 1, st
    assert st["win_rate"] == 25.0


def test_summarize_empty():
    st = rw.summarize([])
    assert st["n"] == 0 and st["win_rate"] is None


# ── ③ 可执行口径（本模块最重要的口径） ─────────────────────────────
def _s(code, gap, ret):
    return {"code": code, "open_gap": gap, "relay_return": ret,
            "relay_note": "ok"}


def test_executable_excludes_high_open():
    """高开 >5% 按纪律买不到 → 必须被剔除，否则统计会得出反向结论。"""
    rows = [_s("000001", 1.0, 1.0), _s("000002", 6.0, -5.0), _s("000003", 5.0, 2.0)]
    out = rw.executable(rows)
    codes = {x["code"] for x in out}
    assert codes == {"000001", "000003"}, "5.0% 恰在上限内应保留，6% 必须剔除"


def test_executable_requires_gap():
    """没有开盘幅度 → 无法判断是否超线，按缺失剔除（⛔ 不猜）。"""
    rows = [{"code": "000001", "open_gap": None, "relay_return": 1.0,
             "relay_note": "ok"}]
    assert rw.executable(rows) == []


def test_tradable_excludes_one_word():
    """一字板/停牌（no_kline）不进可成交池。"""
    rows = [
        {"code": "000001", "relay_note": "one_word", "relay_return": None},
        {"code": "000002", "relay_note": "no_kline", "relay_return": None},
        {"code": "000003", "relay_note": "ok", "relay_return": 1.5},
    ]
    out = rw.tradable(rows)
    assert [x["code"] for x in out] == ["000003"]


# ── ④ 晋级因子阈值（与 board_track 同源） ───────────────────────────
def test_factor_count_thresholds():
    good = {"fbt_min": 565, "seal_ratio": 3.5, "hs": 8.0, "zbc": 0}
    assert rw.factor_count(good) == 4

    bad = {"fbt_min": 601, "seal_ratio": 0.9, "hs": 22.0, "zbc": 2}
    assert rw.factor_count(bad) == 0

    # 边界：09:30 整（570）算早封；封单 3.0 整算厚；换手 10.0 不算低
    edge = {"fbt_min": 570, "seal_ratio": 3.0, "hs": 10.0, "zbc": 0}
    assert rw.factor_count(edge) == 3


def test_factor_count_missing_fields_not_counted():
    """缺字段不猜成「达标」（宁可少算一条，不可白送一条）。"""
    assert rw.factor_count({}) == 0
    assert rw.factor_count({"fbt_min": None, "seal_ratio": None,
                            "hs": None, "zbc": None}) == 0
