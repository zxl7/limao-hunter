#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧩 最终结论：机械闸 × 晋级率 × LLM 催化（2026-11-12 用户定）

用户原话：「竞价推票也是要买晋级率高的票；并且结合我们的机械判断，然后 LLM
给出能买的结果！」

## 三层各管什么（⛔ 不越权）
| 层 | 作用 | 能否否决 |
|---|---|---|
| **机械闸**（pool_call） | 能不能买、买多重 | ✅ 一票否决（skip 即不买） |
| **晋级率**（promotion_odds） | 值不值得打板 | ✅ <40% 不买 |
| **LLM 催化**（ai_advice） | 这条线今天有没有由头 | ⚠️ weak → 降为观察；unknown 不罚 |

## 结论只有三种
- `可买`：机械过 ∧ 晋级率 ≥40% ∧ 催化 ≠ weak
- `观察`：缺一层（机械未过 / 催化 weak）
- `不买`：机械 skip 或 晋级率 <40%

⛔ 输出带三层依据；⛔ 不改任何一层的内部判据。
"""
from __future__ import annotations

from typing import Any

LIKELY_MIN = 40.0


def decide(rows: list[dict], views: dict[str, Any] | None = None,
           catalyst_levels: dict[str, str] | None = None,
           llm_actions: dict[str, dict] | None = None,
           *, likely_min: float = LIKELY_MIN) -> list[dict]:
    """rows: 晋级预案行（含 code/name/boards/pct/gap/theme）；views: pool_call 票视图。"""
    views = views or {}
    cat = catalyst_levels or {}
    llm = llm_actions or {}
    out = []
    for r in rows:
        code = str(r.get("code"))
        v = views.get(code) or {}
        mech = str(v.get("view") or "not_in_pool")
        pct = r.get("pct")
        theme = str(r.get("theme") or "")
        level = str(cat.get(theme) or "unknown")
        mech_ok = mech == "participate"
        pct_ok = isinstance(pct, (int, float)) and float(pct) >= likely_min
        cat_ok = level != "weak"
        act = str((llm.get(code) or {}).get("action") or "").lower()
        thesis = str((llm.get(code) or {}).get("thesis") or "")
        if not pct_ok:
            conclusion, why = "不买", f"晋级率 {pct}% < {likely_min:.0f}%"
        elif mech == "skip":
            conclusion, why = "不买", f"机械否决：{v.get('reason') or 'skip'}"
        elif act == "skip":
            conclusion, why = "不买", f"LLM 放弃：{thesis or '看空'}"
        elif act == "watch":
            conclusion, why = "观察", f"LLM 观察：{thesis or '再看'}"
        elif mech_ok and cat_ok:
            conclusion, why = "可买", (f"机械过 + 晋级率 {pct}% + 催化 {level}"
                                       + (f" + LLM：{thesis}" if act == "buy" and thesis else ""))
        else:
            conclusion = "观察"
            bits = []
            if not mech_ok:
                bits.append(f"机械未过（{mech}）")
            if not cat_ok:
                bits.append("催化 weak")
            why = "；".join(bits) or "未达可买"
        out.append({"code": code, "name": r.get("name"), "theme": theme,
                    "boards": r.get("boards"), "gap": r.get("gap"), "pct": pct,
                    "mech": mech, "catalyst": level, "llm": act or None,
                    "conclusion": conclusion, "why": why})
    order = {"可买": 0, "观察": 1, "不买": 2}
    out.sort(key=lambda x: (order.get(x["conclusion"], 9), -(x.get("pct") or 0)))
    for i, x in enumerate(out, 1):
        x["priority"] = f"P{i}" if x["conclusion"] == "可买" else "—"
    return out
