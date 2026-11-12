#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""⚡ 加速 / 减速（2026-11-12 两篇方法论并入）

文档依据：《短线从入门到精通》P140-150
- 「涨幅增加是加速；涨停时间越来越早是加速」
- 「减速往往预示着分歧」

## 判定（只用 zt_history 的 fbt/boards，⛔ 不抓分时）
取该股最近 3 次涨停（含今日），比较封板时间：
- 最近一次比上一次 **早 ≥30 分钟** → `加速`
- **晚 ≥30 分钟** → `减速`
- 其余 → `平`
- 样本 <2 次 → `未知`（⛔ 不猜）

⛔ 只影响打板轨排序（`ACCEL_RANK=1`），不改买入阈值。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCEL_RANK = os.environ.get("ACCEL_RANK", "1") != "0"
STEP_MIN = 30


def _fbt(v) -> int | None:
    s = str(v or "").strip()
    if len(s) >= 6 and s.isdigit():
        return int(s[:2]) * 60 + int(s[2:4])
    return None


def history(code: str, asof: str | None = None, *, lookback: int = 12) -> list[dict]:
    """该股近 lookback 个交易日的涨停记录（日期升序）。"""
    code = str(code)
    days = sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))
    if asof:
        days = [d for d in days if d <= asof]
    rows = []
    for d in days[-lookback:]:
        try:
            zt = json.loads((ROOT / "cache" / "zt_history" / f"zt_{d}.json").read_text(encoding="utf-8"))["zt_list"]
        except Exception:  # noqa: BLE001
            continue
        hit = next((r for r in zt if str(r.get("code")) == code), None)
        if hit:
            rows.append({"date": d, "fbt": _fbt(hit.get("fbt")), "boards": int(hit.get("boards") or 0)})
    return rows


def accel_of(code: str, asof: str | None = None) -> dict:
    h = history(code, asof)
    if len(h) < 2 or h[-1]["fbt"] is None or h[-2]["fbt"] is None:
        return {"state": "未知", "delta_min": None, "why": f"样本 {len(h)} 次，不足"}
    delta = h[-1]["fbt"] - h[-2]["fbt"]
    if delta <= -STEP_MIN:
        st = "加速"
    elif delta >= STEP_MIN:
        st = "减速"
    else:
        st = "平"
    return {"state": st, "delta_min": int(delta),
            "why": f"今日封{h[-1]['fbt']//60:02d}:{h[-1]['fbt']%60:02d} vs 上次{h[-2]['fbt']//60:02d}:{h[-2]['fbt']%60:02d}（{delta:+d}分）"}


def rank_bonus(state: str) -> int:
    """排序加成：加速 +1，减速 −1（仅打板轨排序）。"""
    if not ACCEL_RANK:
        return 0
    return {"加速": 1, "减速": -1}.get(state, 0)
