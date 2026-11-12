#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🩺 判据链体检（2026-11-12 用户「看不出今天到底用了哪些闸」）

只读：把一次 09:25 裁决里**哪几条闸真的开了、哪几条没数据/没启用**汇总成一行，
写进 `pool_call.meta.chain`，并在 09:25 打印。

⛔ 不参与任何买卖判断；⛔ 不改 reason / view / 名额 / 仓位。
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

SCHEMA = "chain_health_v1"

# (键, 展示名, 判据：在 reason 里出现即算命中)
GATES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("space", "空间闸", ("上方空间",)),
    ("volume", "量能闸", ("量没给足", "量能")),
    ("core", "核心闸", ("不是核心", "同真实题材核心")),
    ("second_wave", "第二波", ("第二波",)),
    ("first_board", "首板观察", ("首板只看不买", "首板观察")),
    ("theme_missing", "缺题材", ("缺题材",)),
    ("one_word", "一字", ("一字",)),
    ("low_open", "低开", ("低开",)),
    ("repair", "历史修复", ("历史连板修复", "非价量N形态")),
    ("backrow", "后排闸", ("后排",)),
    ("dragon", "龙一/身位", ("身位", "龙一")),
    ("forecast", "预测档", ("崖位", "晋级")),
)


def build(pool_res: Mapping[str, Any], *, catalyst: bool = False,
          radar: int = 0, ai_layer: bool = False) -> dict[str, Any]:
    """从一次裁决结果里抽取「开了哪些闸」。"""
    views = (pool_res.get("views") or {})
    reasons = [str((v or {}).get("reason") or "") for v in views.values()]
    hit: Counter[str] = Counter()
    for key, _label, marks in GATES:
        for r in reasons:
            if any(m in r for m in marks):
                hit[key] += 1

    meta = pool_res.get("meta") or {}
    states = Counter((v or {}).get("trade_state") for v in views.values()
                     if (v or {}).get("trade_state"))
    by_view = Counter((v or {}).get("view") for v in views.values())

    # 数据可用性（决定「这条闸今天有没有条件跑」）
    data = {
        "catalyst": bool(catalyst),
        "radar_lines": int(radar or 0),
        "ai_layer": bool(ai_layer),
        "breadth": bool((meta.get("defense") or {}).get("breadth")),
        "sector_flow": bool(((meta.get("defense") or {}).get("sector_flow_boards")) or 0),
        "recheck": bool(meta.get("recheck")),
    }

    fired = [label for key, label, _ in GATES if hit.get(key)]
    dormant = [label for key, label, _ in GATES if not hit.get(key)]
    line = (f"09:25 判据链：参与 {by_view.get('participate', 0)} / 观察 "
            f"{by_view.get('watch', 0)} / 放弃 {by_view.get('skip', 0)}"
            f"｜命中 {'·'.join(fired) if fired else '无'}"
            f"｜未触发 {'·'.join(dormant) if dormant else '无'}")
    missing = [k for k, ok in data.items() if not ok]
    if missing:
        line += f"｜缺数据 {','.join(missing)}"
    if states:
        line += "｜三态 " + "/".join(f"{k}{v}" for k, v in states.items())

    return {"schema": SCHEMA, "line": line, "fired": {k: hit[k] for k in hit},
            "dormant": dormant, "data": data, "states": dict(states),
            "views": dict(by_view)}
