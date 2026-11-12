#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-11-12 两道硬闸（用户：「明明写的是只观察，结果被买了」「缺题材直接 pass」）。

1. 首板只看不买（`watch_group=首板观察` 或 boards==1 非新方向）；
2. 缺题材归属（core_theme 空/占位）→ 不进裁决池、不买。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import pool_call as pc  # noqa: E402


def _cand(code="002110", name="三钢闽光", **kw):
    c = {"code": code, "name": name, "boards": 1, "fbt": "093736", "zbc": 0,
         "cje_yi": 3.0, "need_yi": 0.3, "core_theme": "普钢", "theme": "普钢",
         "gap_pct": 3.65, "line_rank": 1, "line_is_core": True}
    c.update(kw)
    return c


def _quotes(code="002110", gap=3.65):
    return {code: {"prev_close": 10.0, "auction_price": 10.0 * (1 + gap / 100),
                   "auction_amount_yi": 5.0}}


def test_first_board_observation_is_not_bought(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_FIRST_BOARD_BUY", False, raising=False)
    monkeypatch.setattr(pc, "POOL_CALL_REQUIRE_THEME", True, raising=False)
    c = _cand(watch_group="首板观察", participation_eligibility="observe_only")
    res = pc.call_pool({"relay": [c], "watch": []}, _quotes(), plan={}, stance="normal")
    v = res["views"]["002110"]
    # ⛔ 关键断言：**不能被买**；具体拦在哪一道闸可以是量能/首板，但绝不能是 participate
    assert v["view"] != "participate", v


def test_second_board_still_buyable(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_FIRST_BOARD_BUY", False, raising=False)
    monkeypatch.setattr(pc, "POOL_CALL_REQUIRE_THEME", True, raising=False)
    c = _cand(boards=2, line_rank=1, line_is_core=True)
    res = pc.call_pool({"relay": [c], "watch": []}, _quotes(), plan={}, stance="normal")
    assert res["views"]["002110"]["view"] == "participate"


def test_missing_theme_is_not_bought(monkeypatch):
    monkeypatch.setattr(pc, "POOL_CALL_FIRST_BOARD_BUY", True, raising=False)
    monkeypatch.setattr(pc, "POOL_CALL_REQUIRE_THEME", True, raising=False)
    c = _cand(boards=2, core_theme="—", theme="普钢")
    res = pc.call_pool({"relay": [c], "watch": []}, _quotes(), plan={}, stance="normal")
    v = res["views"]["002110"]
    assert v["view"] == "watch" and "缺题材" in v["reason"]


def test_new_direction_first_board_still_buyable(monkeypatch):
    """新方向首板槽是用户点名的口子，不受首板闸影响。"""
    monkeypatch.setattr(pc, "POOL_CALL_FIRST_BOARD_BUY", False, raising=False)
    monkeypatch.setattr(pc, "POOL_CALL_REQUIRE_THEME", True, raising=False)
    c = _cand(new_direction_line="光通信", core_theme="光通信")
    res = pc.call_pool({"relay": [c], "watch": []}, _quotes(), plan={}, stance="normal")
    assert res["views"]["002110"]["view"] == "participate"


def test_catalyst_position_weight(monkeypatch):
    """2026-11-12 用户选 A：催化分进仓位（strong 1.0 / normal 0.85 / weak 0.6）。"""
    monkeypatch.setattr(pc, "POOL_CALL_FIRST_BOARD_BUY", True, raising=False)
    monkeypatch.setattr(pc, "POOL_CALL_REQUIRE_THEME", True, raising=False)
    monkeypatch.setattr(pc, "CATALYST_POSITION_WEIGHT", True, raising=False)
    cat = {"mode": "live", "themes": {"机器人": {"level": "weak"}}}
    c = _cand(boards=2, core_theme="机器人", theme="机器人", line_rank=1, line_is_core=True)
    res = pc.call_pool({"relay": [c], "watch": []}, _quotes(), plan={}, stance="normal",
                       catalyst=cat)
    v = res["views"]["002110"]
    assert v["catalyst_level"] == "weak"
    assert v["position_cap"] <= 0.3 + 1e-6


def test_catalyst_sort_weight_only_breaks_ties(monkeypatch):
    """催化只做同分键，⛔ 不能把低板票顶到高板前面。"""
    monkeypatch.setattr(pc, "POOL_CALL_FIRST_BOARD_BUY", True, raising=False)
    monkeypatch.setattr(pc, "POOL_CALL_REQUIRE_THEME", True, raising=False)
    monkeypatch.setattr(pc, "CATALYST_SORT_WEIGHT", True, raising=False)
    cat = {"mode": "live", "themes": {"机器人": {"level": "strong"},
                                      "光通信": {"level": "weak"}}}
    a = _cand("002708", "同档弱催化", boards=2, core_theme="光通信", theme="光通信",
              line_rank=1, line_is_core=True)
    b = _cand("002709", "同档强催化", boards=2, core_theme="机器人", theme="机器人",
              line_rank=1, line_is_core=True)
    res = pc.call_pool({"relay": [a, b], "watch": []},
                       {**_quotes("002708", 3.0), **_quotes("002709", 3.0)},
                       plan={}, stance="normal", catalyst=cat)
    parts = [c for c, v in res["views"].items() if v.get("view") == "participate"]
    assert len(parts) == 2, parts
    # 同板数同身位 → 催化 strong 的先排（只做同分键）
    assert list(res["views"].keys())[0] in ("002709", "002708")
