#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影子排序记录（2026-11-11 · 只读，不参与任何买入）

## 为什么需要

用户方案（先选强板块 → 板块内按身位推龙一）与现行排序（全池一个序列）**分歧在哪、
分歧的那几只后来涨没涨**——必须每天记下来，半年后才有的回测。
本脚本不改变任何东西：只把「现行顺序」与「候选顺序」并排写进
`cache/shadow_rank_<date>.json`。

## 两种顺序

| 顺序 | 键 |
|---|---|
| `current` | 候选文件里的原始顺序（`relay_rank` / `watch_rank`，即现行生产结果） |
| `proposed` | 同题材内：板数 ↓ → 20 日涨幅 ↓ → 5 日涨幅 ↓ → 身位 → 代码 |

⛔ 缺数据（旧候选没有 `ret_20d_pct`）→ 该票 `momentum_missing=true`，
   排序按 0 中性；文件顶部 `coverage` 写明有多少票缺料，⛔ 不假装有数据。

用法：
    python3 scripts/shadow_rank_diff.py               # 最近一个有候选的交易日
    python3 scripts/shadow_rank_diff.py --date 2026-11-10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "cache"
CAND_DIR = ROOT / "wealth_code" / "cache"


def candidate_days() -> list[str]:
    return sorted(p.stem.replace("candidates-", "") for p in CAND_DIR.glob("candidates-*.json"))


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _theme(row: dict) -> str:
    return str(row.get("core_theme") or row.get("theme") or row.get("sector") or "未分类")


def _boards(row: dict) -> int:
    try:
        return int(row.get("boards") or 0)
    except (TypeError, ValueError):
        return 0


def _proposed_key(row: dict) -> tuple:
    r20 = _num(row.get("ret_20d_pct"), 0.0) or 0.0
    r5 = _num(row.get("ret_5d_pct"), 0.0) or 0.0
    dr = _num(row.get("dragon_rank"), 999) or 999
    return (-_boards(row), -r20, -r5, dr, str(row.get("code") or ""))


def build(day: str) -> dict | None:
    p = CAND_DIR / f"candidates-{day}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    rows = []
    for bucket in ("relay", "watch"):
        for i, r in enumerate(d.get(bucket) or []):
            if not isinstance(r, dict):
                continue
            rows.append({**r, "_bucket": bucket,
                         "_cur": r.get("relay_rank") if bucket == "relay" else r.get("watch_rank")})
    if not rows:
        return None

    # 当前顺序：优先用文件里的 rank，缺失按出现顺序
    for i, r in enumerate(rows):
        if r.get("_cur") is None:
            r["_cur"] = i + 1

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_theme(r), []).append(r)

    out_rows = []
    only_cur, only_prop, both = [], [], []
    for th, arr in groups.items():
        cur = sorted(arr, key=lambda r: (r.get("_cur") if r.get("_cur") is not None else 999))
        prop = sorted(arr, key=lambda r: _proposed_key(r))
        for i, r in enumerate(prop, start=1):
            r["_prop"] = i
        for r in arr:
            out_rows.append({
                "code": str(r.get("code") or ""),
                "name": str(r.get("name") or ""),
                "theme": th,
                "boards": _boards(r),
                "ret_20d_pct": _num(r.get("ret_20d_pct")),
                "ret_5d_pct": _num(r.get("ret_5d_pct")),
                "momentum_missing": _num(r.get("ret_20d_pct")) is None,
                "dragon_rank": _num(r.get("dragon_rank")),
                "bucket": r.get("_bucket"),
                "rank_current": r.get("_cur"),
                "rank_proposed": r.get("_prop"),
            })
        c0 = str(cur[0].get("code") or "") if cur else ""
        p0 = str(prop[0].get("code") or "") if prop else ""
        if c0 and p0:
            (both if c0 == p0 else only_cur).append(
                {"theme": th, "current": c0, "current_name": str(cur[0].get("name") or ""),
                 "proposed": p0, "proposed_name": str(prop[0].get("name") or "")}
                if c0 != p0 else
                {"theme": th, "code": c0, "name": str(cur[0].get("name") or "")})
    missing = sum(1 for r in out_rows if r["momentum_missing"])
    return {
        "schema": "shadow_rank_v1",
        "date": day,
        "source": p.name,
        "note": "只读影子记录：current 与 proposed 两套顺序并排，⛔ 不参与任何买入",
        "coverage": {"stocks": len(out_rows), "momentum_missing": missing,
                     "themes": len(groups)},
        "agreements": both,
        "disagreements": only_cur,
        "stocks": sorted(out_rows, key=lambda r: (r["theme"], r["rank_proposed"] or 999)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="影子排序记录（只读）")
    ap.add_argument("--date", help="交易日；默认最近一个有候选文件的日期")
    args = ap.parse_args(argv)
    days = candidate_days()
    day = args.date or (days[-1] if days else "")
    if not day:
        print("[shadow_rank] 没有候选文件，跳过")
        return 1
    data = build(day)
    if not data:
        print(f"[shadow_rank] {day} 候选文件不可用，跳过")
        return 1
    out = CACHE / f"shadow_rank_{day}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    cov = data["coverage"]
    print(f"[shadow_rank] {day} · {cov['themes']} 线 / {cov['stocks']} 只"
          f"｜龙一不一致 {len(data['disagreements'])} 条｜缺涨幅 {cov['momentum_missing']} 只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
