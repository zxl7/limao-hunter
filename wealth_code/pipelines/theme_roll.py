#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
5日滚动题材盘点 —— 财富密码·数据沉淀（一）

数据源：wealth_code/data/zt_history/zt_YYYY-MM-DD.json
（由 scripts/record_zt_snapshot.py 盘后归档，复用同一份涨停池数据，零额外请求）

输出：wealth_code/data/theme_roll_5d.json
  - theme_breadth   : 题材（行业 sector）5日广度，按「出现天数→涨停总数」排序
  - ladder_trend    : 连板高度逐日趋势（max_boards + 当日最高板个股）
  - dragon_continuation : 最高板龙头逐日延续性（跨日是否存在、连板是否抬升）

设计原则（诚实）：
- 全部来自真实涨停数据；题材归属用快照自带的 sector 字段（行业口径）
- 不主观臆造「主线」，只呈广度/高度/延续三维客观事实，供 AI 期望层与盘中猎手判断
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
# 涨停池历史单一源（2026-09-12 N1 修复）：统一到项目 cache/zt_history，见 pipeline_eod.ZT_HIST_DIR
HISTORY_DIR = ROOT.parent / "cache" / "zt_history"
OUT_PATH = DATA_DIR / "theme_roll_5d.json"
DEFAULT_DAYS = 5

BJ_TZ = timezone(timedelta(hours=8))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_recent_snapshots(days: int) -> list[dict[str, Any]]:
    if not HISTORY_DIR.exists():
        return []
    files = sorted(HISTORY_DIR.glob("zt_*.json"))
    picked = files[-days:] if days > 0 else files
    out: list[dict[str, Any]] = []
    for f in picked:
        snap = _read_json(f)
        if isinstance(snap, dict) and snap.get("date"):
            out.append(snap)
    return out


def _theme_breadth(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    按 sector 聚合 5 日广度。
    返回：[{sector, total_zt, days_present, day_counts:{date:count}, pct_of_days}]
    """
    agg: dict[str, dict[str, Any]] = {}
    for snap in snapshots:
        date = str(snap.get("date") or "")
        for item in snap.get("zt_list") or []:
            sector = str(item.get("sector") or "未知").strip() or "未知"
            entry = agg.setdefault(
                sector,
                {"sector": sector, "total_zt": 0, "day_counts": {}},
            )
            entry["total_zt"] += 1
            entry["day_counts"][date] = int(entry["day_counts"].get(date, 0)) + 1

    out: list[dict[str, Any]] = []
    total_days = len(snapshots)
    for sector, entry in agg.items():
        days_present = len(entry["day_counts"])
        out.append(
            {
                "sector": sector,
                "total_zt": entry["total_zt"],
                "days_present": days_present,
                "pct_of_days": round(days_present * 100.0 / total_days, 1) if total_days else 0.0,
                "day_counts": entry["day_counts"],
            }
        )
    # 排序：出现天数降序 → 涨停总数降序 → 名称
    out.sort(key=lambda x: (-x["days_present"], -x["total_zt"], x["sector"]))
    return out


def _ladder_trend(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    连板高度逐日趋势。
    返回：[{date, max_boards, top_names:[...], zt_count}]
    """
    out: list[dict[str, Any]] = []
    for snap in snapshots:
        date = str(snap.get("date") or "")
        max_b = int(snap.get("max_boards") or 0)
        ladder = snap.get("ladder") or {}
        top_names: list[str] = []
        for b in sorted((int(k) for k in ladder.keys()), reverse=True):
            if b == max_b:
                top_names.extend(ladder.get(str(b)) or ladder.get(b) or [])
        out.append(
            {
                "date": date,
                "max_boards": max_b,
                "top_names": top_names,
                "zt_count": int(snap.get("zt_count") or len(snap.get("zt_list") or [])),
            }
        )
    return out


def _dragon_continuation(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    最高板龙头逐日延续性。
    每天选出「最高板个股」（boards==max_boards，按 zt_list 的 boards 字段）。
    对每只龙头：检查其 code 是否出现在上一日 zt_list（延续），以及连板数是否抬升。
    """
    out: list[dict[str, Any]] = []
    prev_codes: dict[str, dict[str, Any]] = {}
    prev_snap = None
    for snap in snapshots:
        date = str(snap.get("date") or "")
        max_b = int(snap.get("max_boards") or 0)
        zt_list = snap.get("zt_list") or []
        # 当日最高板个股
        dragons = [z for z in zt_list if int(z.get("boards") or 0) == max_b]
        dragon_names: list[str] = []
        for d in dragons:
            code = str(d.get("code") or "")
            name = str(d.get("name") or "")
            dragon_names.append(f"{name}({code})")
            # 延续性判定
            continued = code in prev_codes
            boards_prev = int(prev_codes.get(code, {}).get("boards") or 0)
            out.append(
                {
                    "date": date,
                    "name": name,
                    "code": code,
                    "boards": max_b,
                    "continued_from_prev": continued,
                    "boards_prev": boards_prev if continued else None,
                    "boards_delta": (max_b - boards_prev) if continued else None,
                    "note": (
                        f"较上一日{'连板抬升' if max_b > boards_prev else '高度持平'}（+{max_b - boards_prev}板）"
                        if continued
                        else "新晋最高板龙头"
                    ),
                }
            )
        # 记录当日 code->zt 用于次日延续判定
        prev_codes = {str(z.get("code") or ""): z for z in zt_list}
        prev_snap = snap
    return out


def _ensure_today_archive() -> str:
    """收盘后自愈当日涨停池归档（2026-09-18·治「双路径分裂」的消费侧兜底）。

    事故：15:30 财富码 eod 跑本模块时，`cache/zt_history/zt_{今日}.json` 可能还是
    盘中时间轴建的**空壳**（有 intraday_points、无 zt_list）→ 当日 zt_count=0，
    题材广度静默失真。处置：读之前先让 scripts/zt_archive 自愈（≥15:00 才抓、
    只写归档、不碰 yesterday_zt.json、失败不阻断）。⛔ 不重复实现抓取逻辑。
    """
    try:
        import sys
        scripts = str(ROOT.parent / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import zt_archive
        act = zt_archive.ensure_today_archive()
        if act != "ok":
            print(f"[theme_roll] 🔧 当日涨停池归档自愈：{act}")
        return act
    except Exception as e:  # noqa: BLE001 —— 自愈失败只告警，不阻断盘点
        print(f"[theme_roll] ⚠️ 归档自愈异常（不阻断）: {e}")
        return f"error({e})"


def run_theme_roll(days: int = DEFAULT_DAYS, *, out_path: Path | None = None) -> dict[str, Any]:
    _ensure_today_archive()
    snapshots = _load_recent_snapshots(days)
    out_path = out_path or OUT_PATH

    theme_breadth = _theme_breadth(snapshots)
    ladder_trend = _ladder_trend(snapshots)
    dragon_continuation = _dragon_continuation(snapshots)

    dates = [str(s.get("date") or "") for s in snapshots]
    # 题材延续性摘要：仅在可用天数≥3 且出现率≥60% 时，才判定为「持续题材」，
    # 避免单日/双日数据把所有板块误标为「持续」（单日无法判定持续性，必须诚实）。
    persistent_themes = (
        [t["sector"] for t in theme_breadth if t["pct_of_days"] >= 60.0]
        if len(snapshots) >= 3
        else []
    )
    # 龙头延续摘要：最后一只（最新日）最高板龙头是否延续
    latest_dragon = dragon_continuation[-1] if dragon_continuation else None

    payload = {
        "schema": "theme_roll_5d_v1",
        "generated_at_bj": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "days_requested": days,
        "days_available": len(snapshots),
        "dates": dates,
        "theme_breadth": theme_breadth,
        "ladder_trend": ladder_trend,
        "dragon_continuation": dragon_continuation,
        "summary": {
            "persistent_themes": persistent_themes,
            "latest_max_boards": ladder_trend[-1]["max_boards"] if ladder_trend else 0,
            "latest_dragon": (
                f"{latest_dragon['name']}({latest_dragon['code']}) "
                f"{latest_dragon['boards']}板"
                if latest_dragon
                else "无"
            ),
            "latest_dragon_continued": (
                bool(latest_dragon["continued_from_prev"]) if latest_dragon else False
            ),
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="5日滚动题材盘点")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="滚动天数（默认5）")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="输出 JSON 路径")
    args = ap.parse_args()

    payload = run_theme_roll(days=args.days, out_path=args.out)
    print(str(args.out))
    print(f"days={payload['days_available']}/{payload['days_requested']} "
          f"dates={payload['dates']}")
    print(f"持续题材={payload['summary']['persistent_themes']}")
    print(f"最新最高板={payload['summary']['latest_dragon']} "
          f"延续={payload['summary']['latest_dragon_continued']}")


if __name__ == "__main__":
    main()
