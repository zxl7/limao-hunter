#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔄 弱转强反包候选（2026-11-13 用户「文投昨天断板今天反包」）

盘中扫描对象（只看昨日状态，⛔ 不用今日收盘）：
1. **断板**：前日涨停、昨日不在涨停池；
2. **炸板**：昨日在池但 `zbc > 0` 且未封住（zbc>0 即被砸开过）。

今日盘中若**封板**（≥9.5% / 20cm ≥19.5%）→ 推「弱转强反包」提醒（只提醒，不买）。

⛔ 排除：ST、688/689、昨日一字无换手（zbc=0 且封板在 09:25 前）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get("W2S_ALERT", "1") != "0"
MAX_PUSH = 5


def _days() -> list[str]:
    return sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))


def _pool(day: str) -> dict[str, dict]:
    try:
        zt = json.loads((ROOT / "cache" / "zt_history" / f"zt_{day}.json").read_text(encoding="utf-8"))["zt_list"]
    except Exception:  # noqa: BLE001
        return {}
    return {str(r.get("code")): r for r in zt}


def _theme(day: str, code: str) -> str:
    try:
        snap = json.loads((ROOT / "cache" / "theme_snapshot" / f"theme_{day}.json").read_text(encoding="utf-8"))["mapping"]
        m = snap.get(str(code)) or {}
        return str(m.get("theme_base") or m.get("theme") or "—")
    except Exception:  # noqa: BLE001
        return "—"


def candidates(today: str) -> list[dict]:
    """昨日断板 / 炸板的票（用于今日盘中反包检测）。"""
    if not ENABLED:
        return []
    ds = [d for d in _days() if d < today]
    if len(ds) < 2:
        return []
    prev, prev2 = ds[-1], ds[-2]
    y, y2 = _pool(prev), _pool(prev2)
    out: list[dict] = []
    for code, r in y.items():
        name = str(r.get("name") or "")
        if "ST" in name.upper() or code.startswith(("688", "689")):
            continue
        if int(r.get("zbc") or 0) > 0:
            out.append({"code": code, "name": name, "theme": _theme(prev, code),
                        "prev": f"昨日炸{r.get('zbc')}次", "boards": int(r.get("boards") or 0)})
    for code, r in y2.items():
        if code in y:
            continue
        name = str(r.get("name") or "")
        if "ST" in name.upper() or code.startswith(("688", "689")):
            continue
        out.append({"code": code, "name": name, "theme": _theme(prev2, code),
                    "prev": f"昨日断板（前日{r.get('boards')}板）", "boards": int(r.get("boards") or 0)})
    return out[:120]
