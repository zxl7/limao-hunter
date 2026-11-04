#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观察区「按晋级档分组」的行为锁（2026-11-03）。

## 用户原话（转述 NIKA）

> 「一进二 才该进去 / 二进三 / 三进四 还要回测」
> 「你看青岛 和百大 都不是一个热度」

现场：11-03 竞价卡观察区按综合分平铺，1 板稿（文投 60.7、国金 73.4）排在
6 板龙头（青岛食品 58.6）**前面**。分数不是身位，混排之后卡面读不出梯队。

本文件锁三件事：
1. 档名用「几进几」（用户按晋级阶段思考，不是按板数）；
2. 档内排序**身位第一**（6 板永远在首板前，⛔ 不被综合分翻转）；
3. 开关可回滚（`VERIFY_CARD_TIER_GROUPS=0` 回到平铺）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "wealth_code"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from pipelines import build_cards as bc  # noqa: E402


def test_tier_label_uses_stage_not_boards():
    """⛔ 「几进几」而不是「几板」—— 用户是按晋级阶段想事情的。"""
    assert bc._tier_label({"boards": 1}) == "1进2"
    assert bc._tier_label({"boards": 2}) == "2进3"
    assert bc._tier_label({"boards": 3}) == "3进4"
    assert bc._tier_label({"boards": 6}) == "高位 6板"
    # 缺板数 = 不知道，⛔ 不塞进 1进2 冒充首板
    assert bc._tier_label({}) == "身位未标"
    assert bc._tier_label({"boards": 0}) == "身位未标"


def test_order_boards_first_then_carry():
    """身位第一：6 板哪怕综合分最低，也必须排在最前（NIKA「不是一个热度」）。"""
    rows = [
        {"code": "600109", "boards": 1, "score": 73.4, "carry_cnt": 1},
        {"code": "600715", "boards": 1, "score": 60.7, "carry_cnt": 3},
        {"code": "001219", "boards": 6, "score": 58.6, "carry_cnt": 5},
        {"code": "002086", "boards": 3, "score": 88.5, "carry_cnt": 8},
    ]
    got = [r["code"] for r in sorted(rows, key=bc._tier_order_key)]
    assert got[0] == "001219", "6 板龙头必须第一"
    assert got[1] == "002086", "其次 3 板"
    # 同为 1 板时，带动多的在前（文投 3 > 国金 1）
    assert got[2:] == ["600715", "600109"]


def test_order_falls_back_to_score_within_same_seat():
    """同板同带动时，才轮到封单/综合分 —— 分是末位键，⛔ 不能越位。"""
    rows = [
        {"code": "A", "boards": 2, "carry_cnt": 0, "score": 40.0},
        {"code": "B", "boards": 2, "carry_cnt": 0, "score": 80.0},
    ]
    assert [r["code"] for r in sorted(rows, key=bc._tier_order_key)] == ["B", "A"]


def test_switch_off_restores_flat(monkeypatch):
    """`VERIFY_CARD_TIER_GROUPS=0` → 关掉分档（可直接回滚，无需改代码）。"""
    monkeypatch.delenv(bc.TIER_GROUPS_ENV, raising=False)
    assert bc._tier_groups_on() is True
    monkeypatch.setenv(bc.TIER_GROUPS_ENV, "0")
    assert bc._tier_groups_on() is False


def test_missing_boards_sorts_last():
    """无板数据排最后，⛔ 不当 0 板混进 1进2 里冒充首板。"""
    rows = [{"code": "X", "score": 99.0}, {"code": "Y", "boards": 1, "score": 10.0}]
    assert [r["code"] for r in sorted(rows, key=bc._tier_order_key)] == ["Y", "X"]
