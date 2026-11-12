#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🥇 09:25 晋级预案（一进二 / 二进三）· 只读

用户 2026-11-12：「竞价这里也需要 1进2 和 2进3 大概率成功的个股预案」。

数据：
- 候选：上一交易日首板（`cache/first_to_second_<date>.json`）+ 上一交易日 2 板（zt_history）
- 竞价：`wealth_code/cache/auction_quotes-<今日>.json`
- 概率：`core/promotion_odds.py`（40 日分层频率）

输出：控制台 + `cache/promotion_plan_<今日>.json`
⛔ 只做预案，不改任何买卖判据。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import promotion_odds as po  # noqa: E402


def _prev_day(day: str) -> str | None:
    days = sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))
    prev = [d for d in days if d < day]
    return prev[-1] if prev else None


def build(day: str) -> dict:
    prev = _prev_day(day)
    if not prev:
        return {"date": day, "error": "无上一交易日涨停池"}
    zt = json.loads((ROOT / "cache" / "zt_history" / f"zt_{prev}.json").read_text(encoding="utf-8"))["zt_list"]
    try:
        snap = json.loads((ROOT / "cache" / "theme_snapshot" / f"theme_{prev}.json").read_text(encoding="utf-8"))["mapping"]
    except Exception:  # noqa: BLE001
        snap = {}
    def theme(code: str, r: dict) -> str:
        m = snap.get(str(code)) or {}
        return str(m.get("theme_base") or m.get("theme") or r.get("sector") or "—")
    # 一进二：优先用收盘后打板候选（已过 ST/688/一字/炸板过滤）
    items = []
    fp = ROOT / "cache" / f"first_to_second_{prev}.json"
    if fp.exists():
        for p in (json.loads(fp.read_text(encoding="utf-8")).get("picks") or []):
            items.append({"code": p["code"], "name": p["name"], "boards": 1,
                          "theme": p.get("theme"), "score": p.get("score")})
    else:
        for r in zt:
            if int(r.get("boards") or 0) == 1:
                items.append({"code": str(r["code"]), "name": r["name"], "boards": 1, "theme": theme(str(r["code"]), r)})
    # 二进三：上一交易日 2 板
    for r in zt:
        if int(r.get("boards") or 0) == 2:
            items.append({"code": str(r["code"]), "name": r["name"], "boards": 2,
                          "theme": theme(str(r["code"]), r)})
    qp = ROOT / "wealth_code" / "cache" / f"auction_quotes-{day}.json"
    quotes = json.loads(qp.read_text(encoding="utf-8")) if qp.exists() else {}
    rows = po.plan(items, quotes)
    return {"date": day, "prev": prev, "count": len(rows), "rows": rows,
            "likely": [r for r in rows if r["likely"]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    day = a.date or sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))[-1]
    res = build(day)
    (ROOT / "cache" / f"promotion_plan_{day}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    print(f"[晋级预案] {day}（基于 {res.get('prev')}）· {res.get('count', 0)} 只")
    for r in res.get("rows") or []:
        flag = "🎯大概率" if r["likely"] else "  "
        g = "—" if r.get("gap") is None else f"{r['gap']:+.2f}%"
        print(f"  {flag} {r['name']:<7}{r['boards']}板 竞价{g:>8} 晋级 {r.get('pct')}%  [{r.get('band')}] {r.get('theme') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
