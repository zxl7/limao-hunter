#!/usr/bin/env python3
"""玄学族的归纳与寿命追踪（东方系 / 羊年 …）。

## 这个脚本回答什么

用户 2026-11-10 定调：**「玄学可以归纳，但是不加权重，他只是特殊时期的产物」**。
拆成三件事，全在这里：

1. **可以归纳** —— 按族统计每日涨停家数、族内最高板、题材分散度（不同 sector 数 / 家数）。
   题材分散度高（如 8 只分属 6 个 sector）＝题材解释不了，确实是命名族在炒；
   分散度低＝其实还是题材带动，只是名字碰巧像。**这个数比名字本身可信。**
2. **不加权重** —— 本脚本**只读不写**，不改任何判据、不产生任何信号、不参与仓位。
   族定义里 `weight` 由 `common.manual_theme` 强制为 0（文件写别的值也不认）。
3. **特殊时期的产物** —— 每族按家数自动判寿命，过期即归档：

   ```
   count>=3 且 此前无族      → emerging  刚成族
   count>=3 且 >= 前日       → active    还在
   count>=3 但 <  前日       → fading    在退
   1<=count<3                → weak      只剩零星
   连续 2 日 count<=1        → expired   已瓦解
   ```

   ⚠️ 这不是「预测明天还炒不炒」，只是**给这条线记一个生卒年**。
   羊年 11-03 峰值 11 只 → 11-04 掉到 2 只，一天瓦解 —— 这类东西不能当长期规则。

## 用法

    python3 scripts/theme_family_trace.py                    # 各族摘要
    python3 scripts/theme_family_trace.py --family 东方系    # 逐日明细
    python3 scripts/theme_family_trace.py --days 15          # 只看最近 15 个交易日
    python3 scripts/theme_family_trace.py --json             # 机器可读

⛔ 本脚本不写任何文件、不改任何状态、不上卡。要改族定义请用 `scripts/theme_manual.py`。
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common import manual_theme as mt          # noqa: E402

ZT_DIR = ROOT / "cache" / "zt_history"

# ── 寿命判定阈值（单一源）──────────────────────────────────────────────────
# ⚠️ 这三个数字是「描述口径」不是「交易参数」：它们只决定打印 emerging/active/…
#    之类的标签，不参与任何买卖判断。调它们不会让系统多买或少买一只票。

def _dw(s: str) -> int:
    """终端显示宽度：中文/全角算 2 列。⛔ 别用 len()，中文表必然错位。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(s))


def _pad(s: str, width: int, *, right: bool = False) -> str:
    fill = " " * max(0, width - _dw(s))
    return fill + str(s) if right else str(s) + fill


FAMILY_MIN = 3        # >= 此家数才算「成族」
EXPIRE_RUN = 2        # 连续这么多个交易日 count <= 1 → expired


def zt_days() -> list[tuple[str, list[dict]]]:
    """全部涨停归档日，按日期升序。`[(date, rows), …]`"""
    out: list[tuple[str, list[dict]]] = []
    for p in sorted(ZT_DIR.glob("zt_*.json")):
        day = p.stem.replace("zt_", "")
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                  # noqa: BLE001 —— 坏归档跳过，不阻断
            continue
        rows = d.get("list") or d.get("zt_list") or d.get("rows") or []
        if isinstance(rows, list) and rows:
            out.append((day, rows))
    return out


def _row_name(r: dict) -> str:
    return str(r.get("name") or r.get("mc") or "").strip()


def _row_code(r: dict) -> str:
    return str(r.get("code") or r.get("dm") or "").strip()


def _row_boards(r: dict) -> int:
    try:
        return int(r.get("boards") or r.get("lbc") or 0)
    except (TypeError, ValueError):
        return 0


def _row_sector(r: dict) -> str:
    """题材用的板块名。⛔ 缺就记空串，不用行业名凑（那正是本项目的旧病）。"""
    return str(r.get("sector") or r.get("hy") or "").strip()


def trace(family: str, days: list[tuple[str, list[dict]]], path: Path | None = None) -> list[dict]:
    """逐日算一族的曝光度。返回 `[{date, count, max_boards, spread, members, state}]`。"""
    fams = mt.load_families(path)
    spec = fams.get(family)
    if not spec:
        return []

    series: list[dict] = []
    prev_count: int | None = None
    low_run = 0                     # 连续 count<=1 的日数

    for day, rows in days:
        hit = [r for r in rows
               if family in mt.match_families(_row_name(r), _row_code(r), path=path)]

        count = len(hit)
        sectors = {s for s in (_row_sector(r) for r in hit) if s}
        max_boards = max((_row_boards(r) for r in hit), default=0)

        if count <= 1:
            low_run += 1
        else:
            low_run = 0

        # 状态机（只用来打标签）
        if count <= 1:
            state = "expired" if low_run >= EXPIRE_RUN else "weak"
        elif count < FAMILY_MIN:
            state = "weak"
        elif prev_count is None or prev_count <= 1:
            state = "emerging"
        elif count >= prev_count:
            state = "active"
        else:
            state = "fading"

        series.append({
            "date": day,
            "count": count,
            "max_boards": max_boards,
            "spread": f"{len(sectors)}/{count}" if count else "0/0",
            "sector_n": len(sectors),
            "members": [
                {"code": _row_code(r), "name": _row_name(r),
                 "boards": _row_boards(r), "sector": _row_sector(r)}
                for r in sorted(hit, key=lambda x: (-_row_boards(x), _row_name(x)))
            ],
            "state": state,
        })
        prev_count = count

    return series


def summary(series: list[dict]) -> dict:
    """一族的生卒摘要。⛔ 只描述历史，不预测未来。"""
    if not series:
        return {}
    peak = max(series, key=lambda x: (x["count"], x["date"]))
    # 峰值日的题材分散度：`10/11` 表示 11 只票分属 10 个板块 ⇒ 板块解释不了，是名字在炒。
    peak_row = peak
    alive = [s for s in series if s["count"] >= FAMILY_MIN]
    last = series[-1]
    # 最近一段连续在族内的天数（从末尾往前数 count>=FAMILY_MIN）
    streak = 0
    for s in reversed(series):
        if s["count"] >= FAMILY_MIN:
            streak += 1
        else:
            break
    return {
        "first_seen": alive[0]["date"] if alive else "",
        "peak_date": peak["date"],
        "peak_count": peak["count"],
        "peak_spread": peak_row["spread"],
        "last_date": last["date"],
        "last_count": last["count"],
        "last_state": last["state"],
        "alive_days": len(alive),
        "total_days": len(series),
        "streak": streak,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", help="只看这一族的逐日明细")
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 个交易日（0=全部）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    fams = mt.load_families()
    if not fams:
        print("⛔ `cache/manual_theme.json` 里没有 families（族定义缺失）")
        return 3

    days = zt_days()
    if not days:
        print(f"⛔ 无涨停归档可读：{ZT_DIR}")
        return 3
    if a.days > 0:
        days = days[-a.days:]

    if a.family and a.family not in fams:
        print(f"⛔ 没有这个族：{a.family}（已有：{'、'.join(fams)}）")
        return 2

    targets = [a.family] if a.family else list(fams)
    payload: dict = {"window": [days[0][0], days[-1][0]], "days": len(days), "families": {}}

    for fname in targets:
        series = trace(fname, days)
        summ = summary(series)
        payload["families"][fname] = {
            "kind": fams[fname]["kind"],
            "weight": fams[fname]["weight"],
            "summary": summ,
            "series": series,
        }

    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"窗口 {days[0][0]} ~ {days[-1][0]}（{len(days)} 个交易日）"
          f" · 权重恒 0 · 只观察不裁决\n")

    if a.family:
        series = payload["families"][a.family]["series"]
        s = payload["families"][a.family]["summary"]
        print(f"【{a.family}】{fams[a.family]['kind']}｜峰值 {s['peak_count']} 只"
              f"（{s['peak_date']}）｜最近 {s['last_count']} 只/{s['last_state']}"
              f"｜在族 {s['alive_days']}/{s['total_days']} 天\n")
        print(f"{_pad('日期', 12)}{_pad('家数', 6, right=True)}{_pad('最高板', 8, right=True)}"
              f"{_pad('题材分散', 10, right=True)}  {_pad('状态', 9)}名单")
        print("-" * 104)
        for row in series:
            if row["count"] == 0:
                continue
            names = "、".join(f"{m['name']}({m['boards']})" for m in row["members"][:6])
            if len(row["members"]) > 6:
                names += f" 等 {len(row['members'])} 只"
            print(f"{_pad(row['date'], 12)}{_pad(row['count'], 6, right=True)}"
                  f"{_pad(row['max_boards'], 8, right=True)}{_pad(row['spread'], 10, right=True)}  "
                  f"{_pad(row['state'], 9)}{names}")
        gaps = [r for r in series if r["count"] == 0]
        if gaps:
            print(f"\n（另有 {len(gaps)} 个交易日族内零涨停，未列出）")
        return 0

    print(f"{_pad('族', 10)}{_pad('类', 10)}{_pad('峰值', 6, right=True)}  "
          f"{_pad('峰值日', 12)}{_pad('分散', 8, right=True)}  "
          f"{_pad('最近', 6, right=True)}  {_pad('状态', 9)}  在族天数")
    print("-" * 82)
    for fname in targets:
        d = payload["families"][fname]
        s = d["summary"]
        print(f"{_pad(fname, 10)}{_pad(d['kind'], 10)}{_pad(s['peak_count'], 6, right=True)}  "
              f"{_pad(s['peak_date'], 12)}{_pad(s['peak_spread'], 8, right=True)}  "
              f"{_pad(s['last_count'], 6, right=True)}  "
              f"{_pad(s['last_state'], 9)}  {s['alive_days']}/{s['total_days']} 天")
    print("\n⛔ weight 恒 0（代码强制，文件写别的值也不认）；不进判据、不上卡、不参与仓位。"
          "\n⚠️ expired 表示这条线已瓦解，归档留痕；⛔ 不要拿它当长期规则复用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
