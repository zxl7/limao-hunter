#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🏛 板块核心观察池（2026-11-13 用户「题材与情绪核心那张卡选得很准，能不能每天并进股票池」）

## 只做一件事
把 `cache/board_core/summary.json`（四维核心票：时效/封板硬度/连板领先/带动参与）
top-N 并进候选池，身份 = **observe_only（只观察，不可买）**。

## 为什么只观察
核心票 ≠ 买点：它是「这条线的旗手」，用来对照「板块异动时核心动不动」，
和 `sector_hierarchy` / `board_core` 的定位一致（用户 2026-08-17：核心票不动＝假启动）。

## 过滤（缺一不可）
- `core_score ≥ MIN_SCORE`（默认 55）
- 最近 3 个交易日**有涨停**（否则是过气核心）
- 非 ST / 非 688/689
- 已在池里的不重复加入

开关：`POOL_BOARD_CORE=0` 关闭；`POOL_BOARD_CORE_N` 控制条数（默认 8）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get("POOL_BOARD_CORE", "1") != "0"
MAX_N = int(os.environ.get("POOL_BOARD_CORE_N", "8") or 8)
MIN_SCORE = float(os.environ.get("POOL_BOARD_CORE_MIN", "55") or 55)


def _recent_zt(days: int = 3) -> dict[str, dict]:
    """最近 N 个交易日里出现过涨停的票 → 最后一次的记录。"""
    ds = sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))[-days:]
    out: dict[str, dict] = {}
    for d in ds:
        try:
            zt = json.loads((ROOT / "cache" / "zt_history" / f"zt_{d}.json").read_text(encoding="utf-8"))["zt_list"]
        except Exception:  # noqa: BLE001
            continue
        for r in zt:
            out[str(r.get("code"))] = r        # 越新越覆盖
    return out


def picks(exclude: set[str] | None = None) -> list[dict]:
    if not ENABLED:
        return []
    try:
        summary = json.loads((ROOT / "cache" / "board_core" / "summary.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    recent = _recent_zt(3)
    ex = {str(x) for x in (exclude or set())}
    out: list[dict] = []
    for x in (summary.get("top") or []):
        code = str(x.get("code") or "")
        name = str(x.get("name") or "")
        if not code or code in ex:
            continue
        if "ST" in name.upper() or code.startswith(("688", "689")):
            continue
        if float(x.get("core_score") or 0) < MIN_SCORE:
            continue
        r = recent.get(code)
        if not r:
            continue
        out.append({
            "code": code, "name": name,
            "boards": int(r.get("boards") or 0),
            "sector": x.get("board") or r.get("sector") or "—",
            "core_score": round(float(x.get("core_score") or 0), 1),
            "join_rate": x.get("join_rate"),
            "drive_score": x.get("drive_score"),
        })
        if len(out) >= MAX_N:
            break
    return out
