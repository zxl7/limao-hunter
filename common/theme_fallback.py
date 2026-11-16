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

# 属性标签/占位词：不是题材（选股宝常把无题材票标成「其他」）
NON_THEME = {"其他", "其它", "—", "-", "", "ST股", "业绩增长", "重组", "次新股", "融资融券",
             "深股通", "沪股通", "参股银行", "参股保险", "参股券商", "破净股", "高送转"}


def _load(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def resolve(code: str, day: str | None = None, *, sector: str | None = None,
            reason: str | None = None) -> dict:
    """返回 {"theme": str, "source": "snapshot|library|hithink|industry|none", "note": str}

    `reason`（可选）= 当日涨停原因文本（同花顺）。给了就**优先匹配原因里出现的概念**，
    因为「这只票今天因什么涨停」比「它属于哪个最小概念」更接近真实炒作方向。
    """
    code = str(code or "").strip().zfill(6)
    if not code:
        return {"theme": "", "source": "none", "note": "无代码"}

    # 0) 🥇 同花顺涨停原因（2026-11-16 用户「所有涨停票都走同花顺+涨停原因」）
    #    原因文本是「今天为什么涨停」，比任何静态归属都准；命中概念即采用。
    if reason:
        _hl = _load(CACHE / "theme_library_hithink.json", {})
        _cons = ((_hl.get("entries") or {}).get(code) or {}).get("concepts") or []
        _clean = [c for c in _cons if c and c not in NON_THEME]
        # 原因文本用「+」拼接，概念名可能带斜杠/括号 → 先切词再比对
        import re as _re
        _tokens = [t for t in _re.split(r"[+＋、/,，\s]+", str(reason)) if len(t) >= 2]
        # 命中排序：**原因里越靠前越主**（「短剧+互动影游」→ 短剧是主因），
        # 同位置再按「概念越具体越好」。
        _hit = []
        for c in _clean:
            _pos = next((i for i, t in enumerate(_tokens) if t == c or t in c or c in t), None)
            if _pos is not None:
                _hit.append((_pos, c))
        if _hit:
            # 若当天快照已经给了题材、且该题材也出现在原因里 → 用快照（选股宝口径更稳）
            if day:
                _snap = _load(CACHE / "theme_snapshot" / f"theme_{day}.json", {})
                _t = str(((_snap.get("mapping") or {}).get(code) or {}).get("theme_base")
                         or ((_snap.get("mapping") or {}).get(code) or {}).get("theme") or "").strip()
                # 题材名可能带斜杠（「短剧/互动影游」），原因用「+」拆开 → 用词级判断
                _agree = bool(_t) and (_t in str(reason)
                                       or any(t in _t or _t in t for t in _tokens))
                if _t and _t not in NON_THEME and _agree:
                    return {"theme": _t, "source": "snapshot",
                            "note": f"与涨停原因吻合（{reason[:24]}）"}
            _sizes = _concept_sizes(_hl)
            _best = min(_hit, key=lambda pc: (pc[0], _sizes.get(pc[1], 10 ** 9)))[1]
            return {"theme": _best, "source": "hithink_reason",
                    "note": f"涨停原因命中「{_best}」（{reason[:26]}）"}

    # 1) 当日快照
    if day:
        snap = _load(CACHE / "theme_snapshot" / f"theme_{day}.json", {})
        row = (snap.get("mapping") or {}).get(code) or {}
        t = str(row.get("theme_base") or row.get("theme") or "").strip()
        if t and t not in NON_THEME:
            # 快照名也出现在涨停原因里 → 两条证据一致，保留快照（更贴近选股宝口径）
            if reason and t in str(reason):
                return {"theme": t, "source": "snapshot",
                        "note": f"与涨停原因吻合（{reason[:24]}）"}
            return {"theme": t, "source": "snapshot", "note": ""}

    # 2) 选股宝历史库
    lib = _load(CACHE / "theme_library.json", {})
    e = (lib.get("entries") or {}).get(code) or {}
    t = str(e.get("theme_base_latest") or e.get("theme_latest") or "").strip()
    if t and t not in NON_THEME:
        return {"theme": t, "source": "library", "note": f"取自题材库（末见 {e.get('last_day')}）"}

    # 3) 同花顺概念（挑最具体的一条）
    hl = _load(CACHE / "theme_library_hithink.json", {})
    cons = ((hl.get("entries") or {}).get(code) or {}).get("concepts") or []
    if cons:
        sizes = _concept_sizes(hl)
        clean = [c for c in cons if c not in NON_THEME]
        if reason:
            hit = [c for c in clean if c and c in str(reason)]
            if hit:
                best = min(hit, key=lambda c: sizes.get(c, 10 ** 9))
                return {"theme": best, "source": "hithink",
                        "note": f"涨停原因命中（{reason[:24]}）"}
        cands = [c for c in clean if sizes.get(c, 10 ** 9) <= MAX_HITHINK_MEMBERS] or clean or cons
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
