#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🏛 板块结构分层（2026-11-12 两篇方法论并入 · 用户在 plan 批准）

文档依据：
- 《我所理解的短线的道》P95-107：**地位理论**——只做有地位的，总龙 > 阶段龙 > 补涨 > 小弟；
  "有第一就不做第二"（排他性）。
- 《短线从入门到精通》P237-264：总龙 / 阶段龙 / 补涨龙 / 新龙 / 小弟 的分层。

## 判定（只用已有字段，⛔ 不新抓数据）
| 角色 | 条件（按顺序） |
|---|---|
| 总龙 | 全市场板数最高 ∧ carry_cnt≥3 ∧ 该线家数≥5 |
| 阶段龙 | 该线板数最高 ∧ 封板最早（同板数）∧ carry_cnt≥2 |
| 补涨龙 | 该线第二档（板数比该线最高少 1~2）∧ fbt 不晚于该线中位 |
| 跟风 | 其余 |
| 未知 | 字段缺失（⛔ 不猜） |

⛔ 只输出角色与依据，不改买卖；`HIERARCHY_GATE=shadow` 默认只记录。
"""
from __future__ import annotations

import os
from typing import Any

HIERARCHY_GATE = os.environ.get("HIERARCHY_GATE", "shadow").strip().lower()

ROLE_ORDER = {"总龙": 4, "阶段龙": 3, "补涨龙": 2, "跟风": 1, "未知": 0}


def _boards(r: dict) -> int:
    try:
        return int(r.get("boards") or 0)
    except (TypeError, ValueError):
        return 0


def _fbt(r: dict) -> int:
    s = str(r.get("fbt") or "").strip()
    if len(s) >= 6 and s.isdigit():
        return int(s[:2]) * 60 + int(s[2:4])
    return 15 * 60


def _carry(r: dict) -> int:
    for k in ("carry_cnt", "carry"):
        v = r.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return int((r.get("expectation") or {}).get("carry_cnt") or 0)


def classify(rows: list[dict], theme_of) -> dict[str, dict[str, Any]]:
    """rows = 当日涨停池；theme_of(row) -> 题材名。返回 {code: {role, why}}。"""
    if not rows:
        return {}
    market_max = max((_boards(r) for r in rows), default=0)
    by_theme: dict[str, list[dict]] = {}
    for r in rows:
        by_theme.setdefault(theme_of(r), []).append(r)
    out: dict[str, dict[str, Any]] = {}
    for theme, arr in by_theme.items():
        arr = sorted(arr, key=lambda r: (-_boards(r), _fbt(r)))
        top = arr[0]
        top_boards = _boards(top)
        fbts = sorted(_fbt(r) for r in arr)
        med = fbts[len(fbts) // 2]
        for r in arr:
            code = str(r.get("code") or "")
            b, carry = _boards(r), _carry(r)
            if not code:
                continue
            if len(arr) < 2:
                out[code] = {"role": "未知", "why": f"孤军（该线仅{len(arr)}家）"}
            elif b == market_max and b >= 3 and len(arr) >= 5:
                out[code] = {"role": "总龙", "why": f"全市场最高{b}板·线{len(arr)}家"}
            elif b == top_boards:
                out[code] = {"role": "阶段龙", "why": f"该线最高{b}板·封{str(r.get('fbt'))[:4]}·线{len(arr)}家"}
            elif top_boards - b in (1, 2) and _fbt(r) <= med:
                out[code] = {"role": "补涨龙", "why": f"该线第二档{b}板·封板不晚于中位"}
            else:
                out[code] = {"role": "跟风", "why": f"{b}板·封板晚/地位不足"}
    return out


def rank_bonus(role: str) -> int:
    """排序加成：总龙 +3、阶段龙 +2、补涨 +1、跟风 0。"""
    return {"总龙": 3, "阶段龙": 2, "补涨龙": 1}.get(role, 0)
