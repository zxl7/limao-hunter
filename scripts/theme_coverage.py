#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材归位体检（2026-11-11 · 只读）

用户：「每天选到持稳的票，**一定要把题材稳住**，属于哪个题材，这真的很重要。」

本脚本回答一件事：候选里的「题材」到底是不是真题材。
- `snapshot`：题材快照/题材库命中（真题材）
- `sector`  ：只能拿到行业名兜底（⛔ 行业 ≠ 题材，卡面必须标「行业兜底」）
- `empty`   ：两者都没有（⛔ 必须显式报出，不许静默为空）

历史候选没有 `theme_source` 字段 → 用「theme 是否等于 sector/industry_sector」推断，
并在输出里标 `inferred=true`，⛔ 不假装是原始记录。

用法：
    python3 scripts/theme_coverage.py                 # 最近一期候选
    python3 scripts/theme_coverage.py --date 2026-11-10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAND = ROOT / "wealth_code" / "cache"
OUT = ROOT / "cache"


def _pick(row: dict) -> tuple[str, str, bool]:
    theme = str(row.get("core_theme") or row.get("theme") or "").strip()
    # ⛔ 只用 `sector`（真行业，如 游戏Ⅱ / 通用设备 / 汽车零部）做兜底判据。
    #    `industry_sector` 这个字段名有误导：它在候选里常被写成**题材**（机器人/短剧），
    #    拿它比会把所有票都误判成「行业兜底」（2026-11-11 第一次跑就踩了这个坑）。
    sector = str(row.get("sector") or "").strip()
    src = str(row.get("theme_source") or "").strip()
    if src in ("snapshot", "sector"):
        return theme, src, False
    if not theme:
        return "", "empty", False
    # 历史行没有 theme_source：题材名 == 行业名 → 判为行业兜底
    return theme, ("sector" if theme == sector else "snapshot"), True


def build(day: str) -> dict | None:
    p = CAND / f"candidates-{day}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    rows = [r for b in ("relay", "watch") for r in (d.get(b) or []) if isinstance(r, dict)]
    if not rows:
        return None
    counts = {"snapshot": 0, "sector": 0, "empty": 0}
    inferred = False
    fallback, empty = [], []
    for r in rows:
        theme, src, inf = _pick(r)
        inferred = inferred or inf
        counts[src] = counts.get(src, 0) + 1
        item = {"code": str(r.get("code") or ""), "name": str(r.get("name") or ""),
                "theme": theme, "sector": str(r.get("sector") or "")}
        if src == "sector":
            fallback.append(item)
        elif src == "empty":
            empty.append(item)
    total = len(rows)
    return {
        "schema": "theme_coverage_v1",
        "date": day,
        "source_file": p.name,
        "total": total,
        "counts": counts,
        "snapshot_ratio": round(counts["snapshot"] / total, 3),
        "inferred": inferred,
        "fallback": fallback,
        "empty": empty,
        "note": "snapshot=真题材 / sector=行业兜底（卡面须标）/ empty=未归",
    }


def flips(days: int = 12) -> dict:
    """近 N 个候选日里，同一只票的题材是否变过（区分「细名换了」与「base 也换了」）。

    ⛔ 只有 `theme_base` 变了才算**真漂移**（如 塑料→铜）；
       细名变化（医药→创新药）属同义调整，⛔ 不当错误报。
    """
    hist: dict[tuple[str, str], dict[str, set[str]]] = {}
    paths = sorted(CAND.glob("candidates-*.json"))[-days:]
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        day = p.stem.replace("candidates-", "")
        for b in ("relay", "watch"):
            for r in d.get(b) or []:
                if not isinstance(r, dict):
                    continue
                key = (str(r.get("code") or ""), str(r.get("name") or ""))
                th = str(r.get("core_theme") or r.get("theme") or "").strip()
                base = str(r.get("theme_base") or th).strip()
                if th:
                    hist.setdefault(key, {"theme": set(), "base": set()})["theme"].add(th)
                if base:
                    hist.setdefault(key, {"theme": set(), "base": set()})["base"].add(base)
    real, alias = [], []
    for (code, name), v in hist.items():
        if len(v["base"]) > 1:
            real.append({"code": code, "name": name, "bases": sorted(v["base"]),
                         "themes": sorted(v["theme"])})
        elif len(v["theme"]) > 1:
            alias.append({"code": code, "name": name, "themes": sorted(v["theme"])})
    return {"window_days": len(paths), "real_drift": real, "alias_ok": alias}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="题材归位体检（只读）")
    ap.add_argument("--date", help="候选日 YYYY-MM-DD；默认最近一期")
    args = ap.parse_args(argv)
    days = sorted(p.stem.replace("candidates-", "") for p in CAND.glob("candidates-*.json"))
    day = args.date or (days[-1] if days else "")
    if not day:
        print("[theme_coverage] 没有候选文件")
        return 1
    data = build(day)
    if not data:
        print(f"[theme_coverage] {day} 候选不可用")
        return 1
    (OUT / f"theme_coverage_{day}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    c = data["counts"]
    print(f"[theme_coverage] {day} · {data['total']} 只｜真题材 {c['snapshot']}"
          f"｜行业兜底 {c['sector']}｜未归 {c['empty']}｜真题材占比 {data['snapshot_ratio']:.0%}"
          + ("（历史行按名称推断）" if data["inferred"] else ""))
    if data["fallback"]:
        names = "、".join(f"{x['name']}({x['theme']})" for x in data["fallback"][:8])
        print(f"  行业兜底：{names}")

    f = flips()
    data["flips"] = f
    (OUT / f"theme_coverage_{day}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[theme_flip] 近 {f['window_days']} 个候选日：真漂移 {len(f['real_drift'])} 只"
          f"｜同义换名 {len(f['alias_ok'])} 只（不算错）")
    for x in f["real_drift"][:5]:
        print(f"  真漂移 {x['name']}({x['code']}): {' → '.join(x['bases'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
