#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一进二打板候选（2026-11-12 用户「一进二打板·二进三定心」）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import first_to_second_watch as w  # noqa: E402


def _latest_day_with_boards() -> str:
    """取**最近一个确有涨停**的归档日。

    ⛔ 2026-11-18：原来直接取最新文件，而盘中快照日可能 `涨停 0 只`
    （当天 09:xx 刚写的空池）→ 用例必然红，且与逻辑无关。
    """
    import json
    for f in sorted((ROOT / "cache" / "zt_history").glob("zt_*.json"), reverse=True):
        try:
            if (json.loads(f.read_text(encoding="utf-8")).get("zt_list") or []):
                return f.stem.replace("zt_", "")
        except Exception:
            continue
    raise AssertionError("zt_history 里没有任何含涨停的归档日")



def test_only_first_boards_and_filters():
    day = _latest_day_with_boards()
    res = w.build(day)
    picks = res["picks"]
    assert picks, "应至少有一进二候选"
    for p in picks:
        assert not p["code"].startswith(("688", "689"))
        assert p["zbc"] <= 2
        assert "ST" not in p["name"].upper()
        # 一字（09:25 封 + 零炸）必须被排除
        assert not (p["fbt"][:4] == "0925" and p["zbc"] == 0)


def test_sorted_by_score():
    day = _latest_day_with_boards()
    picks = w.build(day)["picks"]
    assert picks == sorted(picks, key=lambda x: -x["score"])
