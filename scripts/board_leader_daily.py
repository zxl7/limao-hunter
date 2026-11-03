#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连板日观察台账（2026-10-27 用户「我们要做好每一个连板日的观察」）。

## 为什么单独落一份台账

`candidates-<date>.json` 只记「今天推谁」，**不记这条线昨天是谁、明天换没换人**；
`theme_series.json` 的 `dates` 是**当日 5 个盘中时点**（不是跨日），
`board_core` 是跨 20 日的历史核心票（不是当日身位）。

所以「每个连板日」的观察数据此前**没有载体**：昨天机器人线龙一是谁、走到第几板、
今天还是不是它、它今天断没断 —— 全靠人记。

本台账按交易日逐日落一份，形成**板块 × 日期**的连续序列，终端一页看完整周期。

## 判据

龙一判定**完全复用** `core/sector_leader.compute`（单一源），⛔ 本脚本不另立口径。
分组题材 = `cache/theme_snapshot/theme_<date>.json` 的当日冻结映射（与选股同源，
保证「台账里的龙一」与「当天推的票」是同一个答案）。
快照缺失 → 回退该日涨停池自带的 `core_theme`/`sector`（不编造，`theme_source` 留痕）。

## 逐日变化（`change`）

对每个板块，比较「今日龙一」与「上一交易日同板块龙一」：

| kind | 含义 |
|---|---|
| `same` | 还是同一只（看 `boards_step` 是否晋级） |
| `switch` | 换人了（`prev` 里带昨日那只今天还在不在涨停池 → `prev_today`） |
| `new` | 昨日该板块没有龙一记录（新方向/新题材） |
| `gone` | 昨日有这个板块、今日该板块一家都没涨停（整条线熄火） |

⛔ 「昨日龙一今天还在不在涨停池」是本台账最有价值的一列 —— 它直接回答
「这条线是延续还是断了」，而不是靠印象。

## 用法

```bash
python3 scripts/board_leader_daily.py build                 # 今日（最近交易日）落盘
python3 scripts/board_leader_daily.py build --date 2026-10-24
python3 scripts/board_leader_daily.py backfill --days 30    # 回填最近 30 个交易日
python3 scripts/board_leader_daily.py show --date 2026-10-27
python3 scripts/board_leader_daily.py series --theme 机器人 --days 10
```

产物：`cache/board_leader_daily/leader_<YYYY-MM-DD>.json`（按日落盘·幂等覆盖）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                     # 项目根（狸猫猎手）
CACHE = ROOT / "cache"
ZT_DIR = CACHE / "zt_history"
THEME_DIR = CACHE / "theme_snapshot"
OUT_DIR = CACHE / "board_leader_daily"

WEALTH = ROOT / "wealth_code"
if str(WEALTH) not in sys.path:
    sys.path.insert(0, str(WEALTH))

from core import sector_leader as sl   # noqa: E402

BJ_TZ = timezone(timedelta(hours=8))
SCHEMA = "board_leader_daily.v1"


def _load_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def trading_dates_available() -> list[str]:
    """涨停池归档日序列（有归档 = 有数据，⛔ 不自己按 calendar 猜）。"""
    out = []
    for p in sorted(ZT_DIR.glob("zt_*.json")):
        d = p.stem[3:]
        if len(d) == 10 and d[4] == "-":
            out.append(d)
    return out


def _prev_date(date: str) -> str:
    ds = [d for d in trading_dates_available() if d < date]
    return ds[-1] if ds else ""


def _theme_map(date: str, rows: list[dict] | None = None) -> tuple[dict, str]:
    """当日题材冻结映射；缺失 → **降级为申万行业**（`sector_fallback`，明确标注）。

    ⛔ 不实时抓题材（会漂移，台账要可复现）。
    ⛔ 降级不是"编造"：行业口径与题材口径不同（行业内 ≥2 家涨停才算一条线），
      所以产物里 `theme_source` / `degraded` 必须写出来，终端据此标「行业口径」。
      没有这条降级，题材快照上线前的 20 个交易日就全是空白台账 —— 序列直接断掉。
    """
    p = THEME_DIR / f"theme_{date}.json"
    d = _load_json(p)
    m = d.get("mapping")
    if isinstance(m, dict) and m:
        return m, "snapshot"
    if rows:
        fb = {}
        for r in rows:
            code = str(r.get("code") or "")
            sec = str(r.get("sector") or "").strip()
            if code and sec:
                fb[code] = {"theme": sec}
        if fb:
            return fb, "sector_fallback"
    return {}, "missing"


def build_for_date(date: str) -> dict:
    """算某一天的板块龙一（不含 change 对比）。

    ⚠️ `dragon_ranks` **只留前 3 个身位**（龙一/龙二/龙三）：
    整条线 40+ 只成员的逐票身位会把单日文件从 ~5KB 撑到 ~19KB
    （28 天 = 531KB），而终端与 `series` 只消费龙一。
    要看完整梯队 → `zt_history/zt_<date>.json` + 当日题材快照即可复算（compute 是纯函数）。
    """
    zp = ZT_DIR / f"zt_{date}.json"
    zt = _load_json(zp)
    rows = zt.get("zt_list") or []
    tmap, tsrc = _theme_map(date, rows)
    res = sl.compute(rows, tmap)
    return {
        "schema": SCHEMA,
        "date": date,
        "generated_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "theme_source": tsrc,
        "degraded": tsrc != "snapshot",
        "zt_count": len(rows),
        "max_boards": max((sl.boards_of(r) for r in rows), default=0),
        "window_min": res["window_min"],
        "mates_min": res["mates_min"],
        "boards": [
            {
                "theme": b["theme"],
                "mates": b["mates"],
                "leader": {
                    "code": b["leader_code"], "name": b["leader_name"],
                    "boards": b["leader_boards"], "fbt": b["leader_fbt"],
                    "seal_ratio_pct": b["leader_seal_ratio_pct"],
                    "block": b["leader_block"],
                },
                "dragon_ranks": b["dragon_ranks"][:3],
            }
            for b in res["boards"] if b["has_sector"]
        ],
    }


def annotate_change(today: dict, prev: dict | None) -> dict:
    """给今日台账补「相对上一交易日的变化」。⛔ 缺上一日 → 全部标 new（不猜）。"""
    if not prev:
        for b in today["boards"]:
            b["change"] = {"kind": "new", "prev": None, "note": "没有上一交易日的记录"}
        return today

    # ⛔ 只拿「昨天真有龙一」的板块做对比：
    #    昨日那些 `mates=0 / leader 空` 的条目本身就是「更早一天的熄火记录」，
    #    让它们参与对比会把同一个熄火板块**逐日重复追加**——实测 7 条线一天
    #    膨胀成 81 条、单日文件 45KB（真正有涨停的只有 5 条）。
    prev_by_theme = {b["theme"]: b
                     for b in (prev.get("boards") or [])
                     if (b.get("leader") or {}).get("name")}
    # 今日涨停池代码集（判「昨日龙一今天还在不在」）
    z = _load_json(ZT_DIR / f"zt_{today['date']}.json")
    today_codes = {str(r.get("code") or ""): sl.boards_of(r) for r in (z.get("zt_list") or [])}

    seen: set[str] = set()
    for b in today["boards"]:
        th = b["theme"]
        seen.add(th)
        p = prev_by_theme.get(th)
        cur_code = b["leader"]["code"]
        if not p:
            b["change"] = {"kind": "new", "prev": None,
                           "note": f"{prev.get('date')} 这条线还没有龙一记录"}
            continue
        pc = p.get("leader") or {}
        if str(pc.get("code")) == cur_code:
            step = int(b["leader"]["boards"]) - int(pc.get("boards") or 0)
            b["change"] = {"kind": "same", "prev": pc, "boards_step": step,
                           "note": ("晋级" if step > 0 else "板数未增")}
        else:
            prev_today = today_codes.get(str(pc.get("code") or ""))
            b["change"] = {
                "kind": "switch", "prev": pc,
                "prev_today": ({"boards": prev_today} if prev_today else None),
                "note": ("换人（昨日龙一今天也还在涨停池）" if prev_today
                         else "换人（昨日龙一今天已断板）"),
            }

    # 昨日有、今日整条线消失的板块
    for th, p in prev_by_theme.items():
        if th in seen:
            continue
        pc = p.get("leader") or {}
        today["boards"].append({
            "theme": th, "mates": 0,
            "leader": {"code": "", "name": "", "boards": 0, "fbt": "",
                       "seal_ratio_pct": None, "block": ""},
            "dragon_ranks": [],
            "change": {"kind": "gone", "prev": pc,
                       "note": f"{prev.get('date')} 的龙一 {pc.get('name')} 今天整条线没涨停"},
        })
    return today


def out_path(date: str) -> Path:
    return OUT_DIR / f"leader_{date}.json"


def cmd_build(date: str | None = None, *, write: bool = True) -> dict:
    d = date or (trading_dates_available()[-1] if trading_dates_available() else "")
    if not d:
        raise SystemExit("❌ 没有可用的涨停池归档（cache/zt_history/zt_*.json）")
    if not (ZT_DIR / f"zt_{d}.json").exists():
        raise SystemExit(f"❌ {d} 没有涨停池归档，无法生成台账")
    today = build_for_date(d)
    pd = _prev_date(d)
    prev = None
    if pd:
        pp = out_path(pd)
        prev = _load_json(pp) if pp.exists() else (build_for_date(pd) if (ZT_DIR / f"zt_{pd}.json").exists() else None)
    today = annotate_change(today, prev)
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path(d).write_text(json.dumps(today, ensure_ascii=False, indent=2), encoding="utf-8")
    return today


def cmd_backfill(days: int = 30) -> list[str]:
    ds = trading_dates_available()[-max(1, days):]
    done: list[str] = []
    for d in ds:                       # 升序：保证 change 能读到前一日
        cmd_build(d)
        done.append(d)
    return done


def _fmt(x) -> str:
    return "—" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))


def cmd_show(date: str | None = None) -> None:
    d = date or (trading_dates_available()[-1] if trading_dates_available() else "")
    p = out_path(d)
    data = _load_json(p) if p.exists() else cmd_build(d, write=False)
    print(f"📅 {data.get('date')} · 涨停 {data.get('zt_count')} 只 · 最高 {data.get('max_boards')} 板"
          f" · 题材源 {data.get('theme_source')}")
    for b in sorted(data.get("boards") or [], key=lambda x: -x.get("mates", 0)):
        lead = b.get("leader") or {}
        ch = b.get("change") or {}
        kind = {"same": "连续", "switch": "换人", "new": "新线", "gone": "熄火"}.get(ch.get("kind"), "")
        print(f"  {b['theme']:<8} {b['mates']:>2}家 → 龙一 {lead.get('name') or '—'}"
              f"({lead.get('boards')}板 封{_fmt(lead.get('seal_ratio_pct'))}%  {lead.get('fbt') or '—'})"
              f"  [{kind}] {ch.get('note') or ''}")


def cmd_series(theme: str, days: int = 10) -> None:
    ds = trading_dates_available()[-max(1, days):]
    print(f"🐉 {theme} 连板日序列")
    for d in ds:
        p = out_path(d)
        data = _load_json(p) if p.exists() else cmd_build(d, write=False)
        b = next((x for x in (data.get("boards") or []) if x.get("theme") == theme), None)
        if not b:
            print(f"  {d}  —")
            continue
        lead = b.get("leader") or {}
        ch = b.get("change") or {}
        print(f"  {d}  {b['mates']:>2}家  龙一 {lead.get('name')}({lead.get('boards')}板)"
              f"  {ch.get('kind') or ''}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="连板日观察台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pb = sub.add_parser("build", help="生成某一交易日的板块龙一台账")
    pb.add_argument("--date")
    pf = sub.add_parser("backfill", help="回填最近 N 个交易日")
    pf.add_argument("--days", type=int, default=30)
    ps = sub.add_parser("show", help="人读某日台账")
    ps.add_argument("--date")
    pq = sub.add_parser("series", help="某板块的连板日序列")
    pq.add_argument("--theme", required=True)
    pq.add_argument("--days", type=int, default=10)
    a = ap.parse_args(argv)

    if a.cmd == "build":
        d = cmd_build(a.date)
        print(f"✅ {d['date']} 台账 → {out_path(d['date'])}（{len(d['boards'])} 条板块线）")
    elif a.cmd == "backfill":
        ds = cmd_backfill(a.days)
        print(f"✅ 回填 {len(ds)} 个交易日：{ds[0]} → {ds[-1]}")
    elif a.cmd == "show":
        cmd_show(a.date)
    elif a.cmd == "series":
        cmd_series(a.theme, a.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
