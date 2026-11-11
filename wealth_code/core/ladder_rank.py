#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同题材身位（龙一~龙五）· P0 只读快照（2026-11-11）

## 用户口径

> 「同题材地位……板块之间的差距没有同一个题材那么大，只有同一题材才会形成身位上的压制。」
> 「封板时间很重要，但可能不是核心，还是要看最近一个月或最近 5 天的累计涨幅。」

⇒ 身位只在**同一板块内**比较；跨板块比高低没有意义。

## 本模块只做两件事

1. 把每个板块的成员按**两套键**各排一次，**都把排名落盘**：
   - `rank_current`：现行口径（板数 → 首封 → 封单 → 代码），≈ `sector_leader.leader_key` 的简化；
   - `rank_proposed`：11-11 候选口径（板数 → 20 日涨幅 → 5 日涨幅 → 封板质量 → 首封 → 代码）。
2. 把每个因子原值一并落盘（板数/首封/炸板/封单/5日/20日涨幅），

⛔ **不替换生产排序**。半年后回测要拿的就是「两套键在同一批样本上的分歧」，
   所以两套排名必须同时存在，缺一不可。

## 缺数据怎么办

- K 线缺失 / 不足 21 根 → `ret_5d_pct` / `ret_20d_pct` 给 `null`，排序按 **0（不奖不罚）**；
  `ret_missing=true` 让人一眼看出这票的排名是「缺料下算出来的」。
- 板块名取不到 → 归「未分类」，不编造。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .theme_strength import theme_of_factory

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
KLINE_DIR = CACHE / "kline_qfq"

SCHEMA = "ladder_rank_v1"


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _opt(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _boards(row: Mapping[str, Any]) -> int:
    return int(_num(row.get("boards"), 0))


def _fbt(row: Mapping[str, Any]) -> int:
    raw = str(row.get("fbt") or "").strip()
    return int(raw) if raw.isdigit() else 10**9


class KlineReturns:
    """按日期取「截至该日」的 N 日累计涨幅。只读 `cache/kline_qfq`，带内存缓存。"""

    def __init__(self, kline_dir: Path = KLINE_DIR) -> None:
        self.dir = kline_dir
        self._cache: dict[str, tuple[dict[str, int], list[float]] | None] = {}

    def _load(self, code: str):
        if code not in self._cache:
            p = self.dir / f"{code}.json"
            item = None
            try:
                bars = [b for b in (json.loads(p.read_text(encoding="utf-8")).get("bars") or [])
                        if b.get("close") is not None]
                if bars:
                    idx = {str(b.get("date"))[:10]: i for i, b in enumerate(bars)}
                    item = (idx, [float(b["close"]) for b in bars])
            except Exception:  # noqa: BLE001
                item = None
            self._cache[code] = item
        return self._cache[code]

    def ret(self, code: str, day: str, n: int) -> float | None:
        item = self._load(code)
        if not item:
            return None
        idx, closes = item
        i = idx.get(day)
        if i is None or i < n:
            return None
        base = closes[i - n]
        if not base:
            return None
        return round((closes[i] / base - 1) * 100, 2)


def _seal(row: Mapping[str, Any]) -> float:
    """封单（亿）：涨停行用 `seal`，快照成员用 `seal_yi`（两个结构都要能读）。"""
    return _num(row.get("seal_yi", row.get("seal")))


def _quality(row: Mapping[str, Any]) -> tuple:
    """封板质量（越小越好）：炸板次数少 → 封单厚 → 首封早。

    ⛔ 回封时间目前**没有数据源**（只有首封 `fbt` 与炸板次数 `zbc`），
       所以这里只能用「炸板次数 + 封单 + 首封」近似；半年后若有回封时间再补一序。
    """
    return (_num(row.get("zbc"), 0.0), -_seal(row), _fbt(row))


def rank_current_key(row: Mapping[str, Any]) -> tuple:
    """现行口径（板数 → 首封 → 封单厚 → 代码），用作对照基准。"""
    return (-_boards(row), _fbt(row), -_seal(row), str(row.get("code")))


def rank_momentum_key(row: Mapping[str, Any], ret5: float | None, ret20: float | None) -> tuple:
    """用户原话的字面口径：**累计涨幅主序**（20 日 → 5 日 → 板数 → 首封 → 代码）。

    2026-11-11 研究结论：分歧样本里「板数第一」次日 +2.78% / 「动量第一」+1.84%，
    所以这条**暂不进生产**；但必须落盘 —— 半年后要验证的正是这两套谁对。
    """
    return (
        -(ret20 if ret20 is not None else 0.0),
        -(ret5 if ret5 is not None else 0.0),
        -_boards(row),
        _fbt(row),
        str(row.get("code")),
    )


def rank_proposed_key(row: Mapping[str, Any], ret5: float | None, ret20: float | None) -> tuple:
    """11-11 候选口径：板数 → 20 日涨幅 → 5 日涨幅 → 封板质量 → 首封 → 代码。

    缺涨幅按 0（不奖不罚），不把「没数据」当成「涨得少」。
    """
    return (
        -_boards(row),
        -(ret20 if ret20 is not None else 0.0),
        -(ret5 if ret5 is not None else 0.0),
        *_quality(row),
        str(row.get("code")),
    )


def build_ladder(day: str, zt_rows: list[Mapping[str, Any]],
                 klines: KlineReturns | None = None) -> dict[str, Any]:
    """把一日涨停池按板块分组，落「成员因子 + 两套身位排名」。"""
    klines = klines or KlineReturns()
    theme_of, source = theme_of_factory(day)
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for r in zt_rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        groups.setdefault(theme_of(code, r), []).append(r)

    themes: list[dict[str, Any]] = []
    for name, rows in groups.items():
        members = []
        for r in rows:
            code = str(r.get("code") or "")
            ret5 = klines.ret(code, day, 5)
            ret20 = klines.ret(code, day, 20)
            members.append({
                "code": code,
                "name": str(r.get("name") or ""),
                "boards": _boards(r),
                "fbt": str(r.get("fbt") or ""),
                "zbc": int(_num(r.get("zbc"), 0)),
                "seal_yi": round(_num(r.get("seal")), 3),
                "cje_yi": round(_num(r.get("cje_yi")), 3),
                "ret_5d_pct": ret5,
                "ret_20d_pct": ret20,
                "ret_missing": ret5 is None or ret20 is None,
            })
        cur = sorted(members, key=lambda m: rank_current_key(m))
        mom = sorted(members, key=lambda m: rank_momentum_key(
            m, _opt(m["ret_5d_pct"]), _opt(m["ret_20d_pct"])))
        prop = sorted(members, key=lambda m: rank_proposed_key(
            m, _opt(m["ret_5d_pct"]), _opt(m["ret_20d_pct"])))
        for i, m in enumerate(cur, start=1):
            m["rank_current"] = i
        for i, m in enumerate(prop, start=1):
            m["rank_proposed"] = i
        # 用户字面口径（累计涨幅主序）——只落盘，供半年后回测对照
        for i, m in enumerate(mom, start=1):
            m["rank_momentum"] = i
        # 输出按「候选口径」排，`rank_current` 留在字段里做对照
        members = sorted(members, key=lambda m: m["rank_proposed"])
        themes.append({"theme": name, "mate_count": len(members), "members": members})

    themes.sort(key=lambda t: (-t["mate_count"], t["theme"]))
    return {
        "schema": SCHEMA,
        "date": day,
        "generated_at": _bj_now().isoformat(timespec="seconds"),
        "theme_source": source,
        "theme_count": len(themes),
        # ⛔ 口径仍处研究期：只落排名与因子，不参与任何买入判据
        "note": "P0 snapshot only · rank_proposed / rank_momentum 均未接入生产排序",
        "themes": themes,
    }
