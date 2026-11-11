#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价广度（2026-11-11 用户口径 · 只读计算，不进买入判据）

## 为什么需要它

用户原话：

> 「我们昨天把参考的个股截止到了前五，所以有可能发现不了后排已经掉队了……
>   如果同一个板块（比如机器人），前五开得都还不错；但是后面整体竞价，
>   昨天涨停了 20 多个票，大量开在水下，那么这里就是很有风险的事情。」

⇒ 只看前五（身位）会漏掉**整条线的承接质量**。竞价广度回答的是：
   这条线昨天涨停的**全体**，今天早上开在哪。

## ⛔ 口径边界

- **只对规模够的线算广度**：`n < MIN_THEME_SIZE(8)` 时给 `applicable=false` ——
  1 只票的线「水下 100%」没有任何信息量，⛔ 不许拿百分比吓人。
- **只描述、不裁决**：本模块输出事实与分层（ok/divergent/weak），
  ⛔ 不改 view / 名额 / 仓位；是否据此不买由上层（三态）决定。
- **只用 09:25 可得信息**：昨日涨停池 + 当日竞价快照，无未来函数。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

MIN_THEME_SIZE = 8          # 少于这个家数不判广度（样本太小，百分比无意义）
DEEP_UNDER_PCT = -3.0       # 「深水」阈值
DIVERGENT_UNDER_RATIO = 0.40   # 水下占比 ≥40% → 分化
WEAK_UNDER_RATIO = 0.50        # 水下占比 ≥50% 或中位 < -1% → 弱
WEAK_MEDIAN_PCT = -1.0
FRONT_N = 5                 # 「前五」= 身位前 5


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 2)


def gap_pct(quote: Mapping[str, Any] | None) -> float | None:
    """竞价涨幅（%）＝ auction_price / prev_close − 1。缺价 → None（⛔ 不当 0）。"""
    if not isinstance(quote, Mapping):
        return None
    try:
        ap = float(quote.get("auction_price"))
        pc = float(quote.get("prev_close"))
    except (TypeError, ValueError):
        return None
    if ap <= 0 or pc <= 0:
        return None
    return round((ap / pc - 1) * 100, 2)


def theme_breadth(members: Iterable[Mapping[str, Any]],
                  quotes: Mapping[str, Mapping[str, Any]],
                  front_codes: Iterable[str] | None = None) -> dict[str, Any]:
    """单条线的竞价广度。

    `front_codes`：身位前 N 的代码（调用方按 `rank_proposed` 取）；缺省用成员顺序前 N。
    """
    codes = [str(m.get("code") or "") for m in members]
    codes = [c for c in codes if c]
    gaps = [g for g in (gap_pct(quotes.get(c)) for c in codes) if g is not None]
    n = len(codes)
    covered = len(gaps)
    out: dict[str, Any] = {
        "n": n,
        "covered": covered,
        "applicable": n >= MIN_THEME_SIZE and covered > 0,
    }
    if not gaps:
        out.update(underwater=0, deep_under=0, median_gap=None,
                   front_gap=None, divergence=None, level="n/a", note="无竞价数据")
        return out

    under = sum(1 for g in gaps if g < 0)
    deep = sum(1 for g in gaps if g <= DEEP_UNDER_PCT)
    med = _median(gaps)
    front = list(front_codes or [])[:FRONT_N] or codes[:FRONT_N]
    front_gaps = [g for g in (gap_pct(quotes.get(c)) for c in front) if g is not None]
    fgap = _median(front_gaps)

    if not out["applicable"]:
        level, note = "n/a", f"家数 {n} < {MIN_THEME_SIZE}，不判广度"
    elif (med is not None and med < WEAK_MEDIAN_PCT) or under / covered >= WEAK_UNDER_RATIO:
        level, note = "weak", "整条线竞价偏弱"
    elif under / covered >= DIVERGENT_UNDER_RATIO:
        level, note = "divergent", "前五还好、后排大面积水下（分化）"
    else:
        level, note = "ok", "承接正常"

    out.update(
        underwater=under,
        deep_under=deep,
        under_ratio=round(under / covered, 3),
        median_gap=med,
        front_gap=fgap,
        divergence=(round(fgap - med, 2) if (fgap is not None and med is not None) else None),
        level=level,
        note=note,
    )
    return out


def build_breadth(day: str, themes: Mapping[str, list[Mapping[str, Any]]],
                  quotes: Mapping[str, Mapping[str, Any]],
                  fronts: Mapping[str, list[str]] | None = None) -> dict[str, Any]:
    """把「昨日各线成员 + 今日竞价」聚合成一张广度表（⛔ 不读盘、不写盘）。"""
    fronts = fronts or {}
    rows: list[dict[str, Any]] = []
    for name, members in themes.items():
        b = theme_breadth(members, quotes, fronts.get(name))
        rows.append({"theme": name, **b})
    rows.sort(key=lambda r: (-r["n"], r["theme"]))
    return {
        "schema": "auction_breadth_v1",
        "date": day,
        "generated_at": _bj_now().isoformat(timespec="seconds"),
        "min_theme_size": MIN_THEME_SIZE,
        "theme_count": len(rows),
        "zt_total": sum(r["n"] for r in rows),
        "themes": rows,
    }
