#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材兜底解析（2026-11-16 用户「有些票连题材都没有，必须把题材找出来打上去」）

## 顺序（⛔ 不编造，只做"找得到就用"）
1. **当日快照** `cache/theme_snapshot/theme_<date>.json`（选股宝 plates，主口径）
2. **题材库** `cache/theme_library.json`（选股宝历史沉淀：这只票过去属过什么）
3. **同花顺概念** `cache/theme_library_hithink.json`（取**最具体**的概念：全局成分最少的一个）
4. **行业** `sector`（东财行业，**标注为 industry**，⛔ 不当题材用）
5. 都没有 → 返回空（诚实留空，下游显示「—」）

## 为什么需要它
11-16 实证：瀛通通讯(002861) 在当日快照里没有，候选行 `core_theme` 空 → 卡面上「—」。
题材是短线判断的第一要素，空着等于这只票没法读。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"

# 过宽概念：全局成分股太多，不是"炒作题材"（如 新能源汽车 1000+ 只）
MAX_HITHINK_MEMBERS = 300


def _load(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def resolve(code: str, day: str | None = None, *, sector: str | None = None) -> dict:
    """返回 {"theme": str, "source": "snapshot|library|hithink|industry|none", "note": str}"""
    code = str(code or "").strip().zfill(6)
    if not code:
        return {"theme": "", "source": "none", "note": "无代码"}

    # 1) 当日快照
    if day:
        snap = _load(CACHE / "theme_snapshot" / f"theme_{day}.json", {})
        row = (snap.get("mapping") or {}).get(code) or {}
        t = str(row.get("theme_base") or row.get("theme") or "").strip()
        if t:
            return {"theme": t, "source": "snapshot", "note": ""}

    # 2) 选股宝历史库
    lib = _load(CACHE / "theme_library.json", {})
    e = (lib.get("entries") or {}).get(code) or {}
    t = str(e.get("theme_base_latest") or e.get("theme_latest") or "").strip()
    if t:
        return {"theme": t, "source": "library", "note": f"取自题材库（末见 {e.get('last_day')}）"}

    # 3) 同花顺概念（挑最具体的一条）
    hl = _load(CACHE / "theme_library_hithink.json", {})
    cons = ((hl.get("entries") or {}).get(code) or {}).get("concepts") or []
    if cons:
        sizes = _concept_sizes(hl)
        cands = [c for c in cons if sizes.get(c, 10 ** 9) <= MAX_HITHINK_MEMBERS] or cons
        best = min(cands, key=lambda c: sizes.get(c, 10 ** 9))
        return {"theme": best, "source": "hithink",
                "note": f"同花顺概念（成分 {sizes.get(best, '?')} 只）"}

    # 4) 行业（只作兜底展示，不等于题材）
    if sector:
        return {"theme": "", "source": "industry", "note": f"仅行业：{sector}"}

    return {"theme": "", "source": "none", "note": "题材库无记录"}


_SIZE_CACHE: dict[str, dict[str, int]] = {}


def _concept_sizes(lib: dict) -> dict[str, int]:
    key = "v1"
    if key in _SIZE_CACHE:
        return _SIZE_CACHE[key]
    sizes: dict[str, int] = {}
    for e in (lib.get("entries") or {}).values():
        for c in (e.get("concepts") or []):
            sizes[c] = sizes.get(c, 0) + 1
    _SIZE_CACHE[key] = sizes
    return sizes
