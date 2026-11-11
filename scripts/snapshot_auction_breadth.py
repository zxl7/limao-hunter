#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""落盘 09:25 竞价广度（2026-11-11 · 只读）

用户口径：「前五开得都还不错，但后面整体大面积水下，这是很有风险的事情」。
本脚本把「昨日该线全部涨停票的竞价」聚合成一条线的广度，落 `cache/auction_breadth_<date>.json`。

数据源（全部本地已有，⛔ 不联网）：
- 昨日涨停明细：`cache/zt_history/zt_<prev>.json`（必须是收盘完整档）
- 题材归线：`cache/theme_snapshot/theme_<prev>.json`（无快照回落 `sector`）
- 身位前五：`cache/ladder_rank_<prev>.json` 的 `rank_proposed`
- 当日竞价：`cache/snapshot/<date>.json` → `sections.quotes.payload`

用法：
    python3 scripts/snapshot_auction_breadth.py                # 今天
    python3 scripts/snapshot_auction_breadth.py --date 2026-11-11
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core.auction_breadth import build_breadth  # noqa: E402
from wealth_code.core.theme_strength import theme_of_factory  # noqa: E402

CACHE = ROOT / "cache"
ZT_DIR = CACHE / "zt_history"
FRONT_N = 5


def archive_days() -> list[str]:
    return sorted(p.stem.replace("zt_", "") for p in ZT_DIR.glob("zt_*.json"))


def load_zt(day: str) -> list[dict] | None:
    try:
        d = json.loads((ZT_DIR / f"zt_{day}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if d.get("intraday_only") or "zt_list" not in d:
        return None
    return [r for r in (d.get("zt_list") or []) if isinstance(r, dict)]


def load_quotes(day: str) -> dict[str, dict]:
    try:
        d = json.loads((CACHE / "snapshot" / f"{day}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    sec = ((d.get("sections") or {}).get("quotes") or {}).get("payload") or {}
    return sec if isinstance(sec, dict) else {}


def load_fronts(prev: str) -> dict[str, list[str]]:
    try:
        d = json.loads((CACHE / f"ladder_rank_{prev}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, list[str]] = {}
    for t in d.get("themes") or []:
        members = sorted(t.get("members") or [], key=lambda m: m.get("rank_proposed") or 999)
        out[str(t.get("theme"))] = [str(m.get("code")) for m in members[:FRONT_N]]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="落盘 09:25 竞价广度（只读）")
    ap.add_argument("--date", help="交易日 YYYY-MM-DD；默认取最近一个有竞价快照的归档日")
    args = ap.parse_args(argv)

    days = archive_days()
    day = args.date
    if not day:
        day = days[-1] if days else ""
    prevs = [d for d in days if d < day]
    if not prevs:
        print(f"[auction_breadth] {day} 没有前一交易日归档，跳过")
        return 1
    prev = prevs[-1]
    rows = load_zt(prev)
    if not rows:
        print(f"[auction_breadth] {prev} 不是完整收盘档，跳过")
        return 1
    quotes = load_quotes(day)
    if not quotes:
        print(f"[auction_breadth] {day} 没有 09:25 竞价快照，跳过")
        return 1

    theme_of, source = theme_of_factory(prev)
    themes: dict[str, list[dict]] = {}
    for r in rows:
        code = str(r.get("code") or "").strip()
        if code:
            themes.setdefault(theme_of(code, r), []).append(r)

    data = build_breadth(day, themes, quotes, load_fronts(prev))
    data["prev_date"] = prev
    data["theme_source"] = source
    data["quote_covered"] = len(quotes)
    out = CACHE / f"auction_breadth_{day}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    big = [t for t in data["themes"] if t["applicable"]]
    weak = [t["theme"] for t in big if t["level"] in ("divergent", "weak")]
    print(f"[auction_breadth] {day}（基准 {prev}）· {data['theme_count']} 线 / {data['zt_total']} 只"
          f"｜判定广度 {len(big)} 线｜分化/弱：{'、'.join(weak) if weak else '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
