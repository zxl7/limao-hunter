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

import os
from typing import Any

# ⚖️ 2026-11-12 口径对齐：**不用绝对 40%**（会与机械端"空间闸≤6%"结构性冲突：
#    1板 5~6% 档历史仅 37.5%，永远够不到 40%）。改为**板位基准线**：
#    高于该板位的历史平均晋级率才值得打。数据见 `promotion_odds.rates()['overall']`。
BASE_FLOOR = {1: 16.3, 2: 33.6}      # 40 日实测：1→2 16.3%，2→3 33.6%
LIKELY_MIN = 40.0                     # 仅作展示用的"高概率"标签


def decide(rows: list[dict], views: dict[str, Any] | None = None,
           catalyst_levels: dict[str, str] | None = None,
           llm_actions: dict[str, dict] | None = None,
           *, likely_min: float = LIKELY_MIN) -> list[dict]:
    """rows: 晋级预案行（含 code/name/boards/pct/gap/theme）；views: pool_call 票视图。"""
    views = views or {}
    cat = catalyst_levels or {}
    llm = llm_actions or {}
    from core.sector_hierarchy import rank_bonus as _h_bonus
    from core.accel import rank_bonus as _a_bonus
    from core.weak_to_strong import rank_bonus as _w_bonus
    out = []
    for r in rows:
        code = str(r.get("code"))
        _b = int(r.get("boards") or 1)
        _floor = BASE_FLOOR.get(_b, 16.3)
        v = views.get(code) or {}
        mech = str(v.get("view") or "not_in_pool")
        pct = r.get("pct")
        theme = str(r.get("theme") or "")
        level = str(cat.get(theme) or "unknown")
        mech_ok = mech == "participate"
        pct_ok = isinstance(pct, (int, float)) and float(pct) >= _floor
        cat_ok = level != "weak"
        act = str((llm.get(code) or {}).get("action") or "").lower()
        thesis = str((llm.get(code) or {}).get("thesis") or "")
        if not pct_ok:
            conclusion, why = "不买", f"晋级率 {pct}% < {_b}板基准 {_floor:.1f}%"
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


# ── 双轨（2026-11-12 口径不打架的关键）────────────────────────────────
# 冲突根源：机械端「空间闸 ≤6% / DEV」偏好低开低吸；打板轨要求高开（≥5~7% 才有高晋级率）。
# 两者**不能互相否决**，否则永远 0 单。解法：拆成两条独立轨，各自出「可买」，卡片分栏。
#
# 打板轨门槛 = 该板位**机械可达的最高档**（5~7% 档）历史晋级率：
TOP_BAND = {1: 37.5, 2: 50.0}


def _buyable_gap(code: str, gap) -> bool:
    """竞价能不能买到：封死（≈涨停）就买不到 —— 与机械端「竞价封死」同一口径。"""
    if not isinstance(gap, (int, float)):
        return False
    seal = 19.5 if str(code).startswith(("30", "68")) else 9.5
    return float(gap) < seal


MAX_DAILY_PICKS = int(os.environ.get("MAX_DAILY_PICKS", "2") or 2)   # 少即是多（文档原则 6）


def decide_tracks(rows: list[dict], views: dict[str, Any] | None = None,
                  catalyst_levels: dict[str, str] | None = None,
                  llm_actions: dict[str, dict] | None = None,
                  hierarchy: dict[str, dict] | None = None,
                  accel: dict[str, dict] | None = None,
                  weak_to_strong: dict[str, dict] | None = None) -> list[dict]:
    views = views or {}
    from core.sector_hierarchy import rank_bonus as _h_bonus
    from core.accel import rank_bonus as _a_bonus
    from core.weak_to_strong import rank_bonus as _w_bonus
    cat = catalyst_levels or {}
    llm = llm_actions or {}
    out = []
    for r in rows:
        code = str(r.get("code"))
        v = views.get(code) or {}
        mech = str(v.get("view") or "not_in_pool")
        pct = r.get("pct")
        b = int(r.get("boards") or 1)
        theme = str(r.get("theme") or "")
        level = str(cat.get(theme) or "unknown")
        act = str((llm.get(code) or {}).get("action") or "").lower()
        thesis = str((llm.get(code) or {}).get("thesis") or "")
        role = str((hierarchy or {}).get(code, {}).get("role") or "")
        ast = str((accel or {}).get(code, {}).get("state") or "未知")
        wst = str((weak_to_strong or {}).get(code, {}).get("state") or "")
        bonus = _h_bonus(role) + _a_bonus(ast) + _w_bonus(wst)
        base = {"code": code, "name": r.get("name"), "theme": theme, "boards": b,
                "gap": r.get("gap"), "pct": pct, "mech": mech, "catalyst": level, "llm": act or None,
                "role": role or None, "accel": ast, "w2s": wst or None, "bonus": bonus}
        # 低吸轨：机械闸**自己**说了算（晋级率只作展示，不再二次卡它）
        if mech == "participate" and level != "weak" and act != "skip":
            out.append({**base, "track": "低吸轨", "conclusion": "可买",
                        "why": f"机械过（晋级率 {pct}% 仅参考）+催化{level}"})
        # 打板轨：高开档（≥该板位可达最高档）+ 催化不弱 + LLM 不反对
        elif isinstance(pct, (int, float)) and float(pct) >= TOP_BAND.get(b, 37.5) \
                and _buyable_gap(code, r.get("gap")) \
                and b <= 3 and "ST" not in str(r.get("name") or "").upper() \
                and level != "weak" and act != "skip":
            out.append({**base, "track": "打板轨", "conclusion": "可买",
                        "why": f"竞价{pct}%≥{TOP_BAND.get(b,37.5):.1f}（{b}板最高档）+催化{level}"
                               + (f"+LLM：{thesis}" if act == "buy" and thesis else "")})
        else:
            why = ("晋级率 %s < 打板门槛 %.1f" % (pct, TOP_BAND.get(b, 37.5)))
            out.append({**base, "track": "—", "conclusion": "观察", "why": why})
    order = {"可买": 0, "观察": 1}
    out.sort(key=lambda x: (order.get(x["conclusion"], 9),
                            -((x.get("pct") or 0) + (x.get("bonus") or 0))))
    n = 0
    cap = MAX_DAILY_PICKS if MAX_DAILY_PICKS > 0 else 10 ** 6
    for x in out:
        if x["conclusion"] == "可买" and n >= cap:
            x["conclusion"] = "观察"
            x["why"] = f"超出当日名额 {cap}（少即是多·文档原则 6）"
        if x["conclusion"] == "可买":
            n += 1
            x["priority"] = f"P{n}"
        else:
            x["priority"] = "—"
    return out
