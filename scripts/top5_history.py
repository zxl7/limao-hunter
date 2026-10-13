#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选股宝题材 top5 · 长期归档 + 题材库沉淀（2026-10-13 用户立项）。

用户原话：「题材是可以积累和沉淀的毕竟有上限」。

**为什么需要它**：`cache/market_memory_<日期>.json` 只是"盘面前额叶"逐日覆盖的当日快照，
题材历史最多能看到它自己那几份（实测 10-05 起 7 天）——再往前就断了，
没法给「前期的热门题材」（尾盘潜伏口径 = 近 5 个交易日）做长窗验证与回测。

**数据源（只读）**：`cache/market_memory_<日期>.json` 的 `prev_top5`
＝ 选股宝 top5 的按日内容（`name` 题材名 / `zt_count` 涨停家数 / `leader` / `followers` /
`zt_stocks`——成分股**均带 code** / `official` 东财官方板块）。

**产出（两个层次）**：
  · `cache/top5_history/top5_<日期>.json` —— **逐日原始快照**（幂等·已存在不覆盖）
  · `cache/top5_history/theme_index.json` —— **题材库索引**（题材名 → 首见/最近/出现天数/
    最高与累计涨停家数/成分股数/龙头）→ 题材有上限，这份索引就是"沉淀下来的题材总表"

⛔ 按日命名产物写盘前**必过交易日判定**（宪法【交易日判定唯一真源】幽灵产物铁律）：
   休市日不落盘，防再造 `market_memory_2026-10-05` 那类幽灵产物。
⛔ 只读归档：不抓行情、不推送、不改任何策略；缺失一律诚实降级（不编造）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BJ_TZ = timezone(timedelta(hours=8))
HISTORY_DIR = "top5_history"
MEM_PREFIX = "market_memory_"
INDEX_NAME = "theme_index.json"
BACKUP_DIR = "backups/ghost_holiday_2026-10"
# 可逆开关：False → CLI 也不写盘（用于排查 / 回滚）
ENABLED = True


def _now() -> datetime:
    return datetime.now(BJ_TZ)


def _is_trading_day(day: str) -> bool:
    """交易日判定 —— 唯一真源 `common.trading_calendar`（宪法铁律）。"""
    try:
        from common.trading_calendar import is_a_share_trading_day
        from datetime import date as _d
        return bool(is_a_share_trading_day(_d.fromisoformat(day)))
    except Exception:  # pragma: no cover - 极端环境兜底
        try:
            from datetime import date as _d
            return _d.fromisoformat(day).weekday() < 5  # trading-day-fallback: 唯一真源不可用
        except Exception:
            return False


def _mem_path(cache_dir: Path, day: str) -> Path:
    return Path(cache_dir) / f"{MEM_PREFIX}{day}.json"


def read_top5(cache_dir: Path, day: str) -> list[dict[str, Any]]:
    """读某日 market_memory 的 `prev_top5`（选股宝 top5）。缺失/异常 → []。"""
    try:
        data = json.loads(_mem_path(cache_dir, day).read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = (data or {}).get("prev_top5")
    if not isinstance(rows, list):
        return []
    out = []
    for t in rows:
        if not isinstance(t, dict) or not str(t.get("name") or "").strip():
            continue
        out.append({
            "name": str(t.get("name")).strip(),
            "zt_count": int(t.get("zt_count") or 0),
            "leader": (t.get("leader") or {}).get("name") or "",
            "leader_code": (t.get("leader") or {}).get("code") or "",
            "codes": sorted({
                str(s.get("code") or "").strip()
                for s in (list(t.get("zt_stocks") or []) + list(t.get("followers") or []))
                if str(s.get("code") or "").strip()
            }),
            "stocks": [{"code": str(s.get("code") or "").strip(), "name": s.get("name")}
                       for s in list(t.get("zt_stocks") or []) if str(s.get("code") or "").strip()],
            "official": t.get("official"),
        })
    return out


def archive(cache_dir: Path, day: str, *, force: bool = False,
            out_dir: Path | None = None) -> dict[str, Any]:
    """把某日 top5 落成长期归档（幂等）。返回 {status, path, themes}。

    status: written / exists / not_trading_day / no_source / disabled
    """
    day = str(day or "").strip()[:10]
    base = {"date": day, "status": "no_source", "path": None, "themes": 0}
    if not ENABLED:
        return {**base, "status": "disabled"}
    if len(day) != 10 or not _is_trading_day(day):
        return {**base, "status": "not_trading_day"}       # ⛔ 幽灵产物闸
    themes = read_top5(cache_dir, day)
    if not themes:
        return base
    d = Path(out_dir) if out_dir else Path(cache_dir) / HISTORY_DIR
    target = d / f"top5_{day}.json"
    if target.exists() and not force:
        return {**base, "status": "exists", "path": str(target), "themes": len(themes)}
    payload = {"date": day, "source": "market_memory.prev_top5 (选股宝)",
               "archived_at": _now().isoformat(), "themes": themes}
    d.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**base, "status": "written", "path": str(target), "themes": len(themes)}


def archived_days(cache_dir: Path, out_dir: Path | None = None) -> list[str]:
    """已归档的日期（升序）。"""
    d = Path(out_dir) if out_dir else Path(cache_dir) / HISTORY_DIR
    if not d.exists():
        return []
    days = []
    for f in d.glob("top5_*.json"):
        day = f.stem[len("top5_"):]
        if len(day) == 10:
            days.append(day)
    return sorted(set(days))


def rebuild_index(cache_dir: Path, *, out_dir: Path | None = None) -> dict[str, Any]:
    """由全部逐日归档重建题材库索引（题材有上限 → 这份就是沉淀总表）。"""
    d = Path(out_dir) if out_dir else Path(cache_dir) / HISTORY_DIR
    themes: dict[str, dict[str, Any]] = {}
    days = archived_days(cache_dir, d)
    for day in days:
        try:
            rows = json.loads((d / f"top5_{day}.json").read_text(encoding="utf-8")).get("themes") or []
        except Exception:
            continue
        for t in rows:
            nm = str(t.get("name") or "").strip()
            if not nm:
                continue
            e = themes.setdefault(nm, {"first_seen": day, "last_seen": day, "days": 0,
                                       "max_zt": 0, "total_zt": 0, "codes": [],
                                       "last_leader": ""})
            e["days"] += 1
            e["last_seen"] = day
            e["max_zt"] = max(e["max_zt"], int(t.get("zt_count") or 0))
            e["total_zt"] += int(t.get("zt_count") or 0)
            e["codes"] = sorted(set(e["codes"]) | set(t.get("codes") or []))
            if t.get("leader"):
                e["last_leader"] = t["leader"]
    index = {"updated_at": _now().isoformat(), "days": days, "day_count": len(days),
             "theme_count": len(themes), "themes": themes}
    if ENABLED and days:
        d.mkdir(parents=True, exist_ok=True)
        (d / INDEX_NAME).write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return index


def backfill(cache_dir: Path, *, out_dir: Path | None = None) -> dict[str, Any]:
    """把现存的历史 market_memory（含长假备份目录）一次性补齐。幂等。"""
    cache_dir = Path(cache_dir)
    days: set[str] = set()
    for d in (cache_dir, cache_dir / BACKUP_DIR):
        if not d.exists():
            continue
        for f in d.glob(f"{MEM_PREFIX}*.json"):
            day = f.stem[len(MEM_PREFIX):]
            if len(day) == 10:
                days.add(day)
    result = {"scanned": len(days), "written": 0, "skipped": 0,
              "not_trading_day": sorted(d for d in days if not _is_trading_day(d)),
              "details": []}
    for day in sorted(days):
        r = archive(cache_dir, day, out_dir=out_dir)
        if r["status"] == "written":
            result["written"] += 1
        else:
            result["skipped"] += 1
        result["details"].append({"date": day, "status": r["status"], "themes": r["themes"]})
    if result["written"]:
        result["index"] = rebuild_index(cache_dir, out_dir=out_dir)
    return result


def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="选股宝题材 top5 长期归档 + 题材库沉淀")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ar = sub.add_parser("archive", help="归档某日 top5（默认今天·幂等）")
    ar.add_argument("--date", dest="day")
    ar.add_argument("--force", action="store_true")
    ar.add_argument("--cache-dir", type=Path, default=ROOT / "cache")
    bf = sub.add_parser("backfill", help="把现存 market_memory 全部补齐（幂等）")
    bf.add_argument("--cache-dir", type=Path, default=ROOT / "cache")
    ix = sub.add_parser("index", help="由逐日归档重建题材库索引")
    ix.add_argument("--cache-dir", type=Path, default=ROOT / "cache")
    sh = sub.add_parser("show", help="查看题材库索引（可选 --theme 看单条）")
    sh.add_argument("--theme")
    sh.add_argument("--cache-dir", type=Path, default=ROOT / "cache")
    args = ap.parse_args(argv)

    if args.cmd == "archive":
        day = args.day or _now().strftime("%Y-%m-%d")
        r = archive(args.cache_dir, day, force=args.force)
        if r["status"] == "written":
            rebuild_index(args.cache_dir)
        print(f"[top5_history] {r['status']} date={r['date']} themes={r['themes']}")
        return 0
    if args.cmd == "backfill":
        r = backfill(args.cache_dir)
        print(f"[top5_history] 扫描 {r['scanned']} 日 → 新写 {r['written']} / 跳过 {r['skipped']}"
              f" | 非交易日 {r['not_trading_day']}")
        if r.get("index"):
            print(f"[top5_history] 题材库：{r['index']['day_count']} 日 / {r['index']['theme_count']} 个题材")
        return 0
    if args.cmd == "index":
        ix = rebuild_index(args.cache_dir)
        print(f"[top5_history] 题材库索引：{ix['day_count']} 日 / {ix['theme_count']} 个题材")
        return 0
    try:
        ix = json.loads((Path(args.cache_dir) / HISTORY_DIR / INDEX_NAME).read_text(encoding="utf-8"))
    except Exception:
        print("[top5_history] 尚无索引，先跑 archive/index")
        return 0
    if args.theme:
        e = (ix.get("themes") or {}).get(args.theme)
        print(json.dumps(e, ensure_ascii=False, indent=1) if e else f"未收录题材：{args.theme}")
        return 0
    rows = sorted((ix.get("themes") or {}).items(), key=lambda kv: (-kv[1]["days"], -kv[1]["max_zt"]))
    for nm, e in rows:
        print(f"  {nm:<14} 出现{e['days']}天 最高{e['max_zt']}家 累计{e['total_zt']}家 "
              f"成分{len(e['codes'])}只 | {e['first_seen']}~{e['last_seen']} | 龙头 {e['last_leader']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
