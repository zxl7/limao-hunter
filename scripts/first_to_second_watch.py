#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🥇 一进二打板候选（2026-11-12 用户「一进二我想打板，二进三我想定心」）

只读：从当日涨停池挑「首板」→ 给明日一进二打板排序，落
`cache/first_to_second_<date>.json`。⛔ 不改任何判据、不进候选池、不推送。

排序（先硬条件过滤，再打分）：
- 硬过滤：首板 / 非 ST / 非 688 / 非一字（09:25 封且 0 炸 视作一字，打不到）
- 炸板 ≤2（炸多的封不住）
- 打分：封板时间 + 炸板 + 题材家数 + 流通盘（30~50 亿最优）+ 股性

用法：
    python3 scripts/first_to_second_watch.py                 # 最新交易日
    python3 scripts/first_to_second_watch.py --date 2026-11-12
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fbt_min(v) -> int:
    s = str(v or "").strip()
    if len(s) >= 6 and s.isdigit():
        return int(s[:2]) * 60 + int(s[2:4])
    return 15 * 60


def _score(r: dict, mates: int, zt_n: int | None) -> tuple[int, list[str]]:
    why: list[str] = []
    t = _fbt_min(r.get("fbt"))
    if t <= 9 * 60 + 35:
        s1 = 30; why.append("早封")
    elif t <= 9 * 60 + 45:
        s1 = 20; why.append("次早封")
    elif t <= 10 * 60:
        s1 = 12
    else:
        s1 = 5
    zbc = int(r.get("zbc") or 0)
    s2 = {0: 25, 1: 18, 2: 10}.get(zbc, 0)
    if zbc == 0:
        why.append("零炸板")
    elif zbc == 1:
        why.append("炸1")
    s3 = 20 if mates >= 5 else 14 if mates >= 3 else 8 if mates == 2 else 2
    if mates >= 3:
        why.append(f"线{money(mates)}家")
    lt = float(r.get("lt_yi") or 0)
    if 30 <= lt <= 50:
        s4 = 15; why.append("30-50亿")
    elif 15 <= lt < 30:
        s4 = 10
    elif 50 < lt <= 100:
        s4 = 8
    else:
        s4 = 5
    if zt_n is None:
        s5 = 5
    elif 2 <= zt_n <= 6:
        s5 = 10; why.append(f"股性{zt_n}次")
    else:
        s5 = 5
    return s1 + s2 + s3 + s4 + s5, why


def money(n: int) -> int:
    return int(n)


def build(day: str) -> dict:
    zt = json.loads((ROOT / "cache" / "zt_history" / f"zt_{day}.json").read_text(encoding="utf-8"))
    rows = zt.get("zt_list") or []
    try:
        snap = json.loads((ROOT / "cache" / "theme_snapshot" / f"theme_{day}.json").read_text(encoding="utf-8"))["mapping"]
    except Exception:  # noqa: BLE001
        snap = {}
    def theme(r) -> str:
        m = snap.get(str(r.get("code"))) or {}
        return str(m.get("theme_base") or m.get("theme") or r.get("sector") or "—")
    mates: dict[str, int] = {}
    for r in rows:
        mates[theme(r)] = mates.get(theme(r), 0) + 1
    out = []
    for r in rows:
        if int(r.get("boards") or 0) != 1:
            continue
        code, name = str(r.get("code")), str(r.get("name") or "")
        if "ST" in name.upper() or code.startswith(("688", "689")):
            continue
        zbc = int(r.get("zbc") or 0)
        if zbc > 2:
            continue
        fbt = _fbt_min(r.get("fbt"))
        sealed_0925 = fbt <= 9 * 60 + 26
        if sealed_0925 and zbc == 0:
            continue                       # 一字/秒板：明天打不到
        try:
            g = json.loads((ROOT / "cache" / "zt_genes" / f"gene_{code}.json").read_text(encoding="utf-8"))
            zt_n = g.get("zt_n")
        except Exception:  # noqa: BLE001
            zt_n = None
        sc, why = _score(r, mates.get(theme(r), 1), zt_n)
        out.append({"code": code, "name": name, "theme": theme(r), "mates": mates.get(theme(r), 1),
                    "fbt": str(r.get("fbt") or ""), "zbc": zbc,
                    "cje_yi": r.get("cje_yi"), "lt_yi": r.get("lt_yi"),
                    "zt_n": zt_n, "score": sc, "why": why})
    out.sort(key=lambda x: -x["score"])
    return {"date": day, "count": len(out), "picks": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    a = ap.parse_args(argv)
    day = a.date
    if not day:
        files = sorted((ROOT / "cache" / "zt_history").glob("zt_*.json"))
        day = files[-1].stem.replace("zt_", "")
    res = build(day)
    p = ROOT / "cache" / f"first_to_second_{day}.json"
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[一进二] {day} 候选 {res['count']} 只 → {p.name}")
    for x in res["picks"][:10]:
        print(f"  {x['score']:>3} {x['name']:<7}{x['theme']:<10}{x['mates']}家 封{x['fbt'][:4]} 炸{x['zbc']} "
              f"{float(x['cje_yi'] or 0):.1f}亿 流通{float(x['lt_yi'] or 0):.0f}亿 | {'·'.join(x['why'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
