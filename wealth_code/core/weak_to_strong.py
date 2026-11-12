#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔄 弱转强（分歧转一致）（2026-11-12 两篇方法论并入）

文档依据：《短线从入门到精通》P150-155
- 「左侧分歧下降通道不建议抄底；分歧企稳且量能适合的情况下，有分歧转一致的预期，
  建议企稳右侧的时候介入」
- 「弱转强」是买入点，「强转弱」是卖点。

## 判定（只用昨日涨停池 + 今日竞价，⛔ 不抓分时）
前置：昨日**炸板**（`zbc>0`）或**断板**（昨日不在涨停池但前日涨停）。
今日竞价 gap：
- `[+3%, +8%)` 且未封死 → **弱转强**（右侧企稳）
- `≥+8%` → **过强**（可能封死买不到）
- `<+3%` → **未转强**（继续观察）
- 无竞价 → 未知

⛔ 只给打板轨排序 +1（`WEAK_TO_STRONG_BOOST=1`），不单独构成买入。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOST = os.environ.get("WEAK_TO_STRONG_BOOST", "1") != "0"
LOW, HIGH = 3.0, 8.0


def _prev_zt(day: str) -> dict:
    days = sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))
    prev = [d for d in days if d < day]
    if not prev:
        return {}
    try:
        zt = json.loads((ROOT / "cache" / "zt_history" / f"zt_{prev[-1]}.json").read_text(encoding="utf-8"))["zt_list"]
    except Exception:  # noqa: BLE001
        return {}
    return {str(r.get("code")): r for r in zt}


def judge(code: str, day: str, gap: float | None, *, prev: dict | None = None) -> dict:
    p = (prev or _prev_zt(day)).get(str(code))
    if p is None:
        yesterday = "断板"
    elif int(p.get("zbc") or 0) > 0:
        yesterday = f"炸{p.get('zbc')}次"
    else:
        return {"state": "未知", "why": "昨日在板且未炸，不适用弱转强"}
    if gap is None:
        return {"state": "未知", "yesterday": yesterday, "why": "无竞价数据"}
    if gap < LOW:
        return {"state": "未转强", "yesterday": yesterday, "why": f"昨日{yesterday}，今日 {gap:+.1f}% < +3%"}
    if gap >= HIGH:
        return {"state": "过强", "yesterday": yesterday, "why": f"昨日{yesterday}，今日 {gap:+.1f}% 过高"}
    return {"state": "弱转强", "yesterday": yesterday,
            "why": f"昨日{yesterday}，今日 {gap:+.1f}% 企稳（右侧）"}


def rank_bonus(state: str) -> int:
    return 1 if BOOST and state == "弱转强" else 0
