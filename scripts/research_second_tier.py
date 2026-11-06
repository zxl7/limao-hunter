#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「本线有第二身位」能否替代 `ladder_health >= 6`（2026-11-06 用户 A 的原提法）

用户原话口径：「2~3 家题材改用『本线是否有第二身位在涨停附近』替代 `ladder_has_complete`」。

## 与 `research_ladder_small_theme.py` 的区别
  · 前一份按 `ladder_health` 数值分档，发现「1~2 板免判」有支持；
  · 本份直接检验**第二身位**这个结构判据：同线涨停家数 ≥2（即除自己外还有同伴在板），
    不依赖 `ladder_health` 的数值与联动加减分（那 -1 分正是把 2 家题材压到 5.2 的元凶）。

## 数据边界（诚实说明）
  · 「同线涨停家数」需要 `cache/theme_snapshot/theme_<T-1>.json` 的 plates，
    该快照**只覆盖 10-16 起的 15 天** ⇒ 样本必然小于上一份，结论只能看方向。
  · 买入价 = 09:25 竞价价（缺失用当日开盘价兜底，二者同源）；T+1 = 次一交易日收盘。

⛔ 纯研究，不改判据、不写冻结件、不写账本。
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from common.trading_calendar import prev_a_share_trading_day  # noqa: E402

CACHE = ROOT / "wealth_code" / "cache"
OUT = ROOT / "cache" / "research" / "second_tier_bt.json"


def _load(p: Path, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def _bars(code: str) -> list[dict]:
    return (_load(ROOT / "cache" / "kline_qfq" / f"{code}.json", {}) or {}).get("bars") or []


def _on(code: str, day: str) -> tuple[float | None, float | None, float | None]:
    """(开盘, 当日收盘, 次日收盘)。"""
    bars = [b for b in _bars(code) if b.get("date")]
    idx = None
    for i, b in enumerate(bars):
        if str(b.get("date"))[:10] == day:
            idx = i
            break
    if idx is None:
        return None, None, None
    try:
        op = float(bars[idx].get("open"))
        cl = float(bars[idx].get("close"))
        nx = float(bars[idx + 1].get("close")) if idx + 1 < len(bars) else None
    except Exception:
        return None, None, None
    return op, cl, nx


def _trading_days(end: str, n: int) -> list[str]:
    days, d = [], end
    for _ in range(n * 2):
        if d not in days:
            days.append(d)
        d = prev_a_share_trading_day(d)
        if len(days) >= n:
            break
    return sorted(days)


def main() -> int:
    days = _trading_days("2026-11-06", 60)
    rows: list[dict] = []
    skipped: list[str] = []

    for d in days:
        prev = prev_a_share_trading_day(d)
        cand = _load(CACHE / f"candidates-{prev}.json")
        snap = _load(ROOT / "cache" / "theme_snapshot" / f"theme_{prev}.json")
        if not isinstance(cand, dict) or not isinstance(snap, dict):
            continue
        relay = cand.get("relay") or []
        if not relay:
            continue
        mapping = snap.get("mapping") or {}
        zt_prev = (_load(ROOT / "cache" / "zt_history" / f"zt_{prev}.json", {}) or {}).get("zt_list") or []
        if not mapping or not zt_prev:
            skipped.append(d)
            continue

        # 同线涨停：按 plates 交集聚合（排除「次新/其他」等泛化标签）
        GENERIC = ("次新", "其他", " ST", "ST", "业绩", "并购重组", "国企改革", "参股")
        plate_zt: dict[str, list[dict]] = defaultdict(list)
        for z in zt_prev:
            zc = str(z.get("code"))
            for p in (mapping.get(zc) or {}).get("plates") or []:
                p = str(p)
                if any(g in p for g in GENERIC):
                    continue
                plate_zt[p].append(z)

        quotes = _load(CACHE / f"auction_quotes-{d}.json", {}) or {}
        zt_today = {str(r.get("code")) for r in
                    (_load(ROOT / "cache" / "zt_history" / f"zt_{d}.json", {}) or {}).get("zt_list") or []}

        for c in relay:
            code = str(c.get("code") or "")
            op, cl, nx = _on(code, d)
            q = quotes.get(code) or {}
            entry = q.get("auction_price") or op
            if not entry or cl is None:
                continue
            plates = (mapping.get(code) or {}).get("plates") or []
            mates = set()
            for p in plates:
                for z in plate_zt.get(str(p), []):
                    zc = str(z.get("code"))
                    if zc != code:
                        mates.add(zc)
            rows.append({
                "date": d, "code": code, "name": c.get("name"),
                "boards": c.get("boards"), "theme": c.get("theme_name") or c.get("core_theme"),
                "plates": plates,
                "lh": c.get("ladder_health"),
                "complete": c.get("ladder_has_complete"),
                "second_tier": len(mates) >= 1,      # 本线除自己外还有涨停 = 有第二身位
                "n_mates": len(mates),
                "ret_same": round((cl / float(entry) - 1) * 100, 3),
                "ret_t1": round((nx / float(entry) - 1) * 100, 3) if nx else None,
                "zt_today": code in zt_today,
            })

    print(f"窗口 {days[0]} ~ {days[-1]}；有效 relay 票次 {len(rows)}")
    if skipped:
        print(f"  ⚠️ 缺题材快照被跳过 {len(skipped)} 天：{skipped}")
    if not rows:
        print("无样本")
        return 1

    def stat(g: list[dict]) -> str:
        t1 = [r["ret_t1"] for r in g if r["ret_t1"] is not None]
        if not t1:
            return f"n={len(g)} —"
        return (f"n={len(g):3d} T+1均{st.mean(t1):+6.2f}% 中位{st.median(t1):+6.2f}% "
                f"胜{sum(1 for x in t1 if x>0)/len(t1)*100:4.1f}% 晋级{sum(1 for r in g if r['zt_today'])/len(g)*100:4.1f}%")

    hi = lambda r: r["lh"] is not None and float(r["lh"]) >= 6
    lo = lambda r: r["lh"] is not None and float(r["lh"]) < 6

    print()
    print("=== ① 现判据 vs 「有第二身位」 ===")
    print("  现判据(过)      ", stat([r for r in rows if hi(r)]))
    print("  现判据(拦):有第二身位", stat([r for r in rows if lo(r) and r["second_tier"]]))
    print("  现判据(拦):无第二身位", stat([r for r in rows if lo(r) and not r["second_tier"]]))

    print()
    print("=== ② 按连板数拆（3 板+ 是上一份研究的关键格）===")
    for lab, f in [("1~2板", lambda r: int(r["boards"] or 0) <= 2),
                   ("3板+ ", lambda r: int(r["boards"] or 0) >= 3)]:
        print(f"  {lab} 现判据(过)       ", stat([r for r in rows if f(r) and hi(r)]))
        print(f"  {lab} 拦·有第二身位    ", stat([r for r in rows if f(r) and lo(r) and r["second_tier"]]))
        print(f"  {lab} 拦·无第二身位    ", stat([r for r in rows if f(r) and lo(r) and not r["second_tier"]]))

    print()
    print("=== ③ 逐票（被现判据拦下的）===")
    for r in rows:
        if not lo(r):
            continue
        t1 = f"{r['ret_t1']:+.2f}%" if r["ret_t1"] is not None else "—"
        print(f"  {r['date']} {str(r['name'])[:7]:<9}b{r['boards']} lh{r['lh']} "
              f"同线涨停{r['n_mates']}只 {'有身位' if r['second_tier'] else '孤军  '} T+1 {t1:<9} {r['plates']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"window": {"start": days[0], "end": days[-1]},
               "skipped": skipped, "rows": rows,
               "caliber": "买入=09:25竞价（缺则开盘价）；T+1=次一交易日收盘"},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n产物 → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
