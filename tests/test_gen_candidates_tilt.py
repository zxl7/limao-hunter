#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中候选「板块属性偏置」静态锁（2026-09-23）。

背景：`gen_candidates.py` 的评分里含**板块属性偏置**（20cm 涨幅按 10 封顶 + 非主板 ×0.85），
来源是 2026-08-04 的「主板倾斜」；而用户 2026-08-13 已撤销该倾斜（主板/创业板对称参与、
无板块属性加减分）。两者冲突，属**策略评分口径**，须用户拍板才能改 → 本轮只把参数抽成
显式常量并**保持旧行为**，用本测试锁住「不得被顺手改动」。

⛔ 本测试不判断哪种口径对，只保证：**改它必须是一次显式改动**（常量 + 本测试同步）。
改了常量而没改这里 → 测试红，提醒改的人确认这是有意为之。
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "scripts" / "gen_candidates.py"


def _text() -> str:
    return SRC.read_text(encoding="utf-8")


def test_tilt_constants_exist_and_are_explicit():
    t = _text()
    assert "MAIN_BOARD_TILT" in t, "板块属性偏置必须用具名常量，禁止裸数字"
    assert "MAIN_BOARD_TILT_FACTOR" in t
    assert "CAP_EXTRA_PCT_TO_10" in t
    m = re.search(r"^\s*MAIN_BOARD_TILT\s*=\s*(\w+)", t, re.M)
    f = re.search(r"^\s*MAIN_BOARD_TILT_FACTOR\s*=\s*([\d.]+)", t, re.M)
    c = re.search(r"^\s*CAP_EXTRA_PCT_TO_10\s*=\s*(\w+)", t, re.M)
    assert m and f and c, "常量定义格式变了，请同步本测试"


def test_tilt_defaults_equal_legacy_behavior():
    """⛔ 锁定“本轮未改行为”：默认值必须仍等于旧实现（封顶10 + 非主板×0.85）。"""
    t = _text()
    tilt = re.search(r"^\s*MAIN_BOARD_TILT\s*=\s*(\w+)", t, re.M).group(1)
    factor = float(re.search(r"^\s*MAIN_BOARD_TILT_FACTOR\s*=\s*([\d.]+)", t, re.M).group(1))
    cap = re.search(r"^\s*CAP_EXTRA_PCT_TO_10\s*=\s*(\w+)", t, re.M).group(1)
    assert tilt == "True", f"MAIN_BOARD_TILT 默认被改为 {tilt} —— 这是策略口径变更，须用户拍板"
    assert factor == 0.85, f"非主板系数被改为 {factor} —— 同上"
    assert cap == "True", f"CAP_EXTRA_PCT_TO_10 默认被改为 {cap} —— 同上"


def test_score_uses_constants_not_literals():
    """评分必须走常量分支，禁止再出现 `1.0 if is_main else 0.85` 这类裸写法。"""
    t = _text()
    assert "0.85)" not in t.replace("MAIN_BOARD_TILT_FACTOR = 0.85", ""), \
        "评分行仍在用裸 0.85，应改为 MAIN_BOARD_TILT_FACTOR"
    assert "eff_pct = min(pct, 10.0) if CAP_EXTRA_PCT_TO_10 else pct" in t


def test_tilt_conflict_documented_in_source():
    """冲突与来源必须写在源码旁（防下一个人把旧注释当依据）。"""
    t = _text()
    assert "2026-08-13" in t, "须记录用户撤销主板倾斜的日期与依据"
    assert "对称参与" in t
