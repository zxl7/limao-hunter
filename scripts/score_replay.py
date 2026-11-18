#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接力终评分回放（2026-11-18 用户「试一试回放」）。

## 比什么

同一批**已经真实发生**的交易日里，比较三种「买谁」的规则：

| 方案 | 规则 |
|---|---|
| A 现状 | 09:25 裁决里 `participate` 的那些（闸门说了算） |
| B 盘后分排序 | 按 `quant_score.score` 取前 k |
| C 终评分排序 | 按 `quant_score.score × 竞价系数` 取前 k（本次新增） |

收益口径：**竞价价买 → 当日收盘卖**（`au_ret`，与账本同源，⛔ 不用 T+1 口径）。

## 输出

- 各方案的：参与天数、平均单笔、胜率、日均
- 排名信息量：Spearman IC（分数 vs 实际收益）
- 分档表现：A/B/C 档各自均值（验证"分数越高越好"是否成立）

## ⛔ 纪律

回放只读 `wealth_code/cache/` 与 `cache/`，不写任何文件（除 stdout）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import relay_score as rs  # noqa: E402


def _j(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _spearman(pairs: list[tuple[float, float]]) -> float | None:
    """秩相关（无第三方依赖）。样本 <5 不报。"""
    n = len(pairs)
    if n < 5:
        return None

    def _rank(vals: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    xs = _rank([p[0] for p in pairs])
    ys = _rank([p[1] for p in pairs])
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return round(num / (dx * dy), 3) if dx and dy else None


def _candidates_for(day: str) -> dict[str, dict[str, Any]]:
    """交易日 D 的候选来自 D-1 晚上建的文件（回退：前一天找不到就找更早的最近一期）。"""
    files = sorted((ROOT / "wealth_code/cache").glob("candidates-*.json"))
    prev = [f for f in files if f.stem.split("-", 1)[1] < day]
    if not prev:
        return {}
    c = _j(prev[-1]) or {}
    out = {}
    for r in (c.get("relay") or []) + (c.get("watch") or []):
        out[str(r.get("code"))] = r
    return out


def main() -> int:
    days = sorted(p.stem for p in (ROOT / "cache/pool_ledger").glob("*.json"))
    rows_by_day: dict[str, list[dict[str, Any]]] = {}
    for d in days:
        led = _j(ROOT / "cache/pool_ledger" / f"{d}.json") or {}
        rows = [r for r in (led.get("rows") or []) if isinstance(r.get("au_ret"), (int, float))]
        if rows:
            rows_by_day[d] = rows

    if not rows_by_day:
        print("⛔ 没有可回放的账本日")
        return 2

    lines: list[str] = []
    plan_a: list[float] = []
    plan_b: list[float] = []
    plan_c: list[float] = []
    ic_eod: list[tuple[float, float]] = []
    ic_fin: list[tuple[float, float]] = []
    band_bucket: dict[str, list[float]] = {"A": [], "B": [], "C": [], "D": []}

    for d, rows in rows_by_day.items():
        cands = _candidates_for(d)
        call = _j(ROOT / "wealth_code/cache" / f"pool_call-{d}.json") or {}
        views = call.get("views") or {}
        scored: list[tuple[float, float, str, float]] = []   # (final, eod, code, ret)
        for r in rows:
            code = str(r.get("code") or "")
            c = cands.get(code) or {}
            eod = c.get("score")
            v = views.get(code) or {}
            exp = v.get("expected_gap_pct")
            sc = rs.apply_auction_to_eod_score(
                eod, gap=r.get("gap_pct"), expected_gap=exp,
                amt_yi=r.get("amount_yi"), need_yi=r.get("need_yi"),
                limit_pct=float(r.get("limit_up_pct") or 10.0),
            )
            final = float(sc["final_score"])
            band = "A" if "首选" in sc["band"] else "B" if "次选" in sc["band"] else "C" if "观察" in sc["band"] else "D"
            band_bucket[band].append(float(r["au_ret"]))
            scored.append((final, float(sc["eod_score"]), code, float(r["au_ret"])))
            if isinstance(eod, (int, float)):
                ic_eod.append((float(eod), float(r["au_ret"])))
            ic_fin.append((final, float(r["au_ret"])))

        # A 现状：裁决里的 participate
        part = [str(c) for c, v in views.items() if str(v.get("view")) == "participate"]
        ret_map = {str(r.get("code")): float(r["au_ret"]) for r in rows}
        a_rets = [ret_map[c] for c in part if c in ret_map]
        if a_rets:
            plan_a.extend(a_rets)

        scored.sort(reverse=True)
        k = max(1, len(a_rets) or 2)
        plan_b.extend([s[1] for s in scored[:k]])   # B: 按盘后分取前 k（收益也取同位置票的收益）
        plan_c.extend([s[3] for s in scored[:k]])
        # 注意：B 的列表暂存 eod 分，下面统一换成收益
        lines.append(
            f"{d} | 账本 {len(rows):2d} 只 | 现状参与 {len(a_rets)} 只 均值 {_avg(a_rets):+.2f}% | "
            f"终评前{k} 均值 {_avg([s[3] for s in scored[:k]]):+.2f}%"
        )

    # B 方案需要按 eod 排序后的收益：重算一次（简单起见用同一批 scored 的 eod 排序）
    plan_b_ret: list[float] = []
    for d, rows in rows_by_day.items():
        cands = _candidates_for(d)
        call = _j(ROOT / "wealth_code/cache" / f"pool_call-{d}.json") or {}
        views = call.get("views") or {}
        tmp = []
        for r in rows:
            code = str(r.get("code") or "")
            eod = (cands.get(code) or {}).get("score")
            if isinstance(eod, (int, float)):
                tmp.append((float(eod), float(r["au_ret"])))
        tmp.sort(reverse=True)
        k = max(1, sum(1 for v in views.values() if str(v.get("view")) == "participate") or 2)
        plan_b_ret.extend([t[1] for t in tmp[:k]])

    print("\n".join(lines))
    print("\n== 三种买法对比（竞价买→当日收盘卖）==")
    for name, rets in (("A 现状（闸门）", plan_a), ("B 盘后分排序", plan_b_ret), ("C 终评分排序", plan_c)):
        if rets:
            print(f"{name:16s} 笔数 {len(rets):3d} | 均值 {_avg(rets):+.2f}% | 胜率 {_win(rets):.0f}%")

    print("\n== 排名信息量（Spearman IC）==")
    print("盘后分 vs 收益:", _spearman(ic_eod))
    print("终评分 vs 收益:", _spearman(ic_fin))

    print("\n== 分档表现（终评档位 → 实际收益）==")
    for b in ("A", "B", "C", "D"):
        v = band_bucket[b]
        if v:
            print(f"{b} 档：{len(v):3d} 笔 | 均值 {_avg(v):+.2f}% | 胜率 {_win(v):.0f}%")
    return 0


def _avg(v: list[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def _win(v: list[float]) -> float:
    return 100.0 * sum(1 for x in v if x > 0) / len(v) if v else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
