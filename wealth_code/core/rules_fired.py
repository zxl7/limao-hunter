#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据命中日报（2026-11-11 · 只读）

## 为什么需要

规则多了会变黑盒，根因不是「算不出来」，而是**没人知道哪条规则今天真的在干活**。
本模块只做一件事：把 09:25 冻结件（`pool_call-<date>.json`）里每只票的终态，
归因到**登记表里的判据 id**（`config/rules_registry.json`），数出每条的命中数。

## ⛔ 边界

- 只读冻结件，不改任何结论；
- 归因优先读**结构化字段**（`high_gap_reject`/`defense_gate`/…），
  字段缺失才回退到理由文案关键词（旧冻结件没有新字段）；
- 归不到任何判据的票进 `unattributed`，⛔ 不硬塞给某条规则。

## 产物

`cache/rules_fired_<date>.json`：
```json
{"schema": "rules_fired_v1", "date": "...", "total_views": 32,
 "fired": [{"id": "gate.gap_room", "name": "顶部空间", "hits": 5, "codes": [...]}],
 "zero_hit": ["gate.backrow", ...],          // 今天一次都没命中的判据（⚠️ 待删候选）
 "by_state": {"A": 4, "B": 0, "C": 0, "": 28},
 "substitutes": [...]}
```
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
WC_CACHE = ROOT / "wealth_code" / "cache"
REGISTRY = ROOT / "config" / "rules_registry.json"

SCHEMA = "rules_fired_v1"

# 判据 id → 归因方式。结构字段优先，文案关键词兜底（旧冻结件没有新字段）。
# ⛔ 这里的 id 必须都能在 `config/rules_registry.json` 里找到（有测试锁）。
RULE_SIGNALS: dict[str, dict[str, Any]] = {
    "gate.gap_room": {"field": "high_gap_reject", "kw": ("上方空间不够",)},
    "gate.seal_auction": {"kw": ("竞价就封死", "封在涨停附近")},
    "gate.volume": {"kw": ("量没给足", "真没量", "竞价只到计划")},
    "gate.expectation": {"kw": ("低于预期",)},
    "gate.core_first": {"field": "core_first_gate"},
    "gate.backrow": {"field": "backrow_gate", "kw": ("后排", "身位")},
    "gate.anchor": {"field": "anchor_gate", "kw": ("锚点", "前排态度坏了")},
    "gate.midboard": {"field": "midboard_gate", "kw": ("两头不靠",)},
    "gate.defense": {"field": "defense_gate"},
    "gate.dragon": {"field": "dragon_gate", "kw": ("只做龙一", "龙一")},
    "gate.thin_seal": {"kw": ("封单只占流通", "封单太薄")},
}


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def registry_names(path: Path = REGISTRY) -> dict[str, str]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {r["id"]: r.get("name", r["id"])
            for g in d.get("groups") or [] for r in (g.get("rules") or []) if r.get("id")}


def registry_ids(path: Path = REGISTRY) -> set[str]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    return {r.get("id") for g in d.get("groups") or [] for r in (g.get("rules") or [])
            if r.get("id")}


def load_views(day: str, wc_cache: Path = WC_CACHE) -> dict[str, dict] | None:
    p = wc_cache / f"pool_call-{day}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    views = d.get("views")
    return views if isinstance(views, dict) else None


def _hit(v: Mapping[str, Any], sig: Mapping[str, Any]) -> bool:
    f = sig.get("field")
    if f and v.get(f):
        return True
    reason = str(v.get("reason") or "")
    return any(k in reason for k in (sig.get("kw") or ()))


def attribute(v: Mapping[str, Any]) -> list[str]:
    """一只票命中了哪些判据（可能多条：闸是串联的，先命中的先拦）。"""
    return [rid for rid, sig in RULE_SIGNALS.items() if sig and _hit(v, sig)]


def build(day: str, *, names: Mapping[str, str] | None = None,
          wc_cache: Path = WC_CACHE) -> dict[str, Any] | None:
    views = load_views(day, wc_cache)
    if views is None:
        return None
    names = names or {}
    hits: dict[str, list[str]] = {}
    unattributed: list[str] = []
    states: dict[str, int] = {}
    subs: list[str] = []
    for code, v in views.items():
        if not isinstance(v, dict):
            continue
        st = str(v.get("trade_state") or "")
        states[st] = states.get(st, 0) + 1
        if v.get("substitute"):
            subs.append(code)
        if str(v.get("view")) == "participate":
            continue                      # 买到的票不算「被拦」
        ids = attribute(v)
        if not ids:
            unattributed.append(code)
        for rid in ids:
            hits.setdefault(rid, []).append(code)

    fired = [{"id": rid, "name": names.get(rid, rid), "hits": len(codes),
              "codes": sorted(codes)} for rid, codes in
             sorted(hits.items(), key=lambda kv: -len(kv[1]))]
    # 只对「有归因方式」的判据报零命中（没有信号映射的判据不参与，避免噪声）
    zero = sorted(rid for rid in RULE_SIGNALS if rid not in hits)
    return {
        "schema": SCHEMA,
        "date": day,
        "generated_at": _bj_now().isoformat(timespec="seconds"),
        "total_views": len(views),
        "bought": sum(1 for v in views.values() if str(v.get("view")) == "participate"),
        "fired": fired,
        "zero_hit": zero,
        "unattributed": sorted(unattributed),
        "by_state": states,
        "substitutes": sorted(subs),
        "note": "只读：把每只未买票归因到登记表里的判据；zero_hit 是待删候选，不是错误",
    }
