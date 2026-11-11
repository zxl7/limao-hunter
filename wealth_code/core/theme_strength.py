#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块强度（2026-11-11 用户方案 · P0 只读落盘，不参与任何买入判据）

## 为什么单独一个模块

用户 11-11 的口径：「先看昨天最强的板块，在前三强里找能接力的个股」。
研究里验证过（近 25 个交易日、129 个板块日）：

| 板块强度档 | 该板块龙一次日 | 胜率 |
|---|---:|---:|
| 第 1 | +5.63% | 83% |
| 第 2–3 | +3.79% | 69% |
| 第 4–6 | +1.48% | 52% |

⇒ 「前三强」不是口味问题，是 **+2.5pp / +17pp 胜率** 的差距。

## ⛔ 本模块的边界

- **只算、只落盘**：输出 `cache/theme_strength_<date>.json`，⛔ 不碰候选池、不碰 09:25 闸门。
- **排序键透明**：`zt_count`（主）→ `max_boards`（次）→ `boards_sum`（兜底）。
  ⛔ 不做加权求和 —— 加权会把「成交额大」之类的单维优势顶成最强板块（`leader_key`
  的历史事故就是这个）。三个原始值也一并落盘，日后要换口径不必重算。
- **板块口径单一源**：优先用当日题材快照 `mapping[code].theme_base`；快照缺失的日期
  回落到 `zt_list[].sector` 并在 `theme_source` 里写明，⛔ 不混用两套口径却不留痕。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
SNAPSHOT_DIR = CACHE / "theme_snapshot"

SCHEMA = "theme_strength_v1"

# ⛔ 板块强度口径必须单一源：11-11 隔壁已实现 `core/market_theme_rank.py`
#    （「高度与梯队 + 承接质量，家数仅弱辅助」，与用户 7-27「广度≠核心」一致）。
#    本模块只**引用**它，不另立一套；只有它不可用时才回落到家数主序，并在
#    `strength_basis` 里写明 —— 避免两套口径各自排出一个「前三强」。


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def theme_map_for(day: str) -> tuple[dict[str, str], str]:
    """当日 `code -> theme` 映射 + 口径来源。

    返回 `(mapping, source)`，`source ∈ {"theme_snapshot", "sector_fallback"}`。
    ⛔ 快照存在但缺某只票时，该票不在这里补猜 —— 由调用方回落到 `sector` 并留痕。
    """
    p = SNAPSHOT_DIR / f"theme_{day}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}, "sector_fallback"
    rows = d.get("mapping") or {}
    out: dict[str, str] = {}
    for code, v in rows.items():
        if not isinstance(v, Mapping):
            continue
        theme = str(v.get("theme_base") or v.get("theme") or "").strip()
        if theme:
            out[str(code)] = theme
    return out, ("theme_snapshot" if out else "sector_fallback")


def theme_of_factory(day: str) -> tuple[Callable[[str, Mapping[str, Any]], str], str]:
    """构造 `(code, 涨停行) -> 板块名` 的取用函数，并把口径来源带出来。"""
    mapping, source = theme_map_for(day)
    fallback_hits = {"n": 0}

    def theme_of(code: str, row: Mapping[str, Any]) -> str:
        theme = mapping.get(str(code))
        if theme:
            return theme
        fallback_hits["n"] += 1
        # ⛔ 只用涨停行自己的 sector 兜底；两套口径都缺 → 归「未分类」，不编造
        return str(row.get("sector") or "").strip() or "未分类"

    theme_of.source = source  # type: ignore[attr-defined]
    theme_of.fallback_hits = fallback_hits  # type: ignore[attr-defined]
    return theme_of, source


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _boards(row: Mapping[str, Any]) -> int:
    return int(_num(row.get("boards"), 0))


def _fbt(row: Mapping[str, Any]) -> int:
    """首封时刻（HHMMSS）→ 可比较整数；缺失给 1e9（排最后，⛔ 不当成 0 点封板）。"""
    raw = str(row.get("fbt") or "").strip()
    return int(raw) if raw.isdigit() else 10**9


def leader_of(rows: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """板块内的「高度代表」：板数最高 → 首封最早 → 代码兜底（纯确定，不含评分）。"""
    arr = list(rows)
    if not arr:
        return None
    return min(arr, key=lambda r: (-_boards(r), _fbt(r), str(r.get("code"))))


def _attach_leaders(day: str, zt_rows: list[Mapping[str, Any]], themes: list[dict[str, Any]]) -> None:
    """给每个板块补 leader（板数最高 → 首封最早 → 代码），不参与排序。"""
    theme_of, _src = theme_of_factory(day)
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for r in zt_rows:
        code = str(r.get("code") or "").strip()
        if code:
            groups.setdefault(theme_of(code, r), []).append(r)
    for t in themes:
        rows = groups.get(t["theme"]) or []
        lead = leader_of(rows)
        t["leader"] = {
            "code": str(lead.get("code") or "") if lead else "",
            "name": str(lead.get("name") or "") if lead else "",
            "boards": _boards(lead) if lead else 0,
        }
        t["seal_sum_yi"] = round(sum(_num(r.get("seal")) for r in rows), 3)
        t["cje_sum_yi"] = round(sum(_num(r.get("cje_yi")) for r in rows), 3)
        t["boards_sum"] = sum(_boards(r) for r in rows)


def build_theme_strength(day: str, zt_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """把一日涨停池按板块聚合成「强度 + 排名 + 是否前三」。

    ## 口径优先级（⛔ 单一源，别再加第三套）

    1. `core/market_theme_rank.rank()`（11-11 生产口径：高度与梯队 + 承接质量，
       家数弱辅助）→ `strength_basis="market_theme_rank_v1"`；
    2. 它不可用 / 开关关闭时，回落到**家数主序**（本模块）→
       `strength_basis="zt_count_fallback"`，并在顶层 `caliber` 里写明。

    两种口径都会落 `zt_count / max_boards / boards_sum` 原始值，日后换口径不必重算。
    """
    mapping, source = theme_map_for(day)
    themes: list[dict[str, Any]] = []
    basis = "zt_count_fallback"

    try:
        from . import market_theme_rank as _mtr  # 生产口径（可能尚未合入/被开关关闭）
        if _mtr.enabled():
            # 生产口径只认 theme/core_theme；老归档没有题材快照时用 sector 补齐，
            # ⛔ 不把整只票丢掉（丢了会让「全市场最强线」退化成「有快照的那几条线」）。
            eff_map = dict(mapping)
            for r in zt_rows:
                c = str(r.get("code") or "").strip()
                if c:
                    eff_map.setdefault(c, str(r.get("sector") or "").strip() or "未分类")
            res = _mtr.rank(list(zt_rows), eff_map)
            if res.get("themes"):
                for i, t in enumerate(res["themes"], start=1):
                    themes.append({
                        "theme": t["theme"],
                        "zt_count": t.get("zt_count", 0),
                        "max_boards": t.get("max_boards", 0),
                        "boards_sum": 0,          # 由 _attach_leaders 补齐
                        "score": t.get("score"),
                        "breadth_flag": bool(t.get("breadth_flag")),
                        "theme_rank": i,
                    })
                basis = "market_theme_rank_v1"
    except Exception:  # noqa: BLE001 —— 生产模块缺失/半成品都不能拖垮快照
        themes = []

    if not themes:
        theme_of, _src = theme_of_factory(day)
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for r in zt_rows:
            code = str(r.get("code") or "").strip()
            if code:
                groups.setdefault(theme_of(code, r), []).append(r)
        for name, rows in groups.items():
            themes.append({
                "theme": name,
                "zt_count": len(rows),
                "max_boards": max((_boards(r) for r in rows), default=0),
                "boards_sum": sum(_boards(r) for r in rows),
            })
        # 家数主序（回落口径，⛔ 与生产口径不同，顶层已标注 basis）
        themes.sort(key=lambda t: (-t["zt_count"], -t["max_boards"], -t["boards_sum"], t["theme"]))

    _attach_leaders(day, zt_rows, themes)
    for i, t in enumerate(themes, start=1):
        t["strength_rank"] = i
        t["top3"] = i <= 3

    return {
        "schema": SCHEMA,
        "date": day,
        "generated_at": _bj_now().isoformat(timespec="seconds"),
        "theme_source": source,
        "strength_basis": basis,
        "caliber": ("高度与梯队 + 承接质量；家数仅弱辅助"
                    if basis == "market_theme_rank_v1"
                    else "家数主序（回落口径）"),
        "theme_count": len(themes),
        "zt_total": sum(t["zt_count"] for t in themes),
        "themes": themes,
    }
