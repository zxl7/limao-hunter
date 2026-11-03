#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股生涯（stock_arc）—— 一只票反复出现时，跟踪它的「变现能力」和「进化路线」。

## 解决的问题（2026-10-23 用户「一个股连续出现我们就应该跟踪他的可连续变现和进化路线」）

`judgement_trail` 已经把每天的判断留痕，但它是**按天**的：看单只票要自己把 8 行串起来，
而且分不清「这只票是越走越强，还是反复给你假机会」。本模块把它们折成两级：

- **一波（episode）**：连续涨停的一串日子。相邻两次涨停之间没涨停的交易日 ≤2 → 同一波；
  ≥3 → 新一波（与 2026-10-22 用户口径「断板≥3 天 ∧ 上一波 ≥3 板 = 高位二波」同源）。
- **生涯（arc）**：一只票所有波的集合 + 三级「发现延迟」+ 真实变现成绩。

## 三层，⛔ 不混

1. **事实层**：哪天涨停、几板、封板时刻、成交额 → 全部来自 `judgement_trail`，不重算。
2. **可执行层**：T 日涨停、T+1 日**能不能买到**。判据用真实日线：
   `high==low` = 一字（买不到）；有成交、且次日开盘高开落在 **−3%~+5%** = 我们 09:25 的准入窗口。
3. **可变现层**：账户同口径 `equal_weight_next_day_close` = **次日开盘买、次日收盘卖**
   （`pipeline_verify.py:350` 实证口径）。T+1 与 T+2 两档都给，T+2 只作「多拿一天」参考。

## 铁律

- **⛔ 不把「没数据」当「没赚」**：T+1 日线缺失 / 当日尚未收盘 → `pending=True`，收益留 `None`，
  绝不按 0 或昨收顶替。
- **⛔ 收益不是胜率**：`ret_t1` 是那一天真金白银的价差，`hit_rate` 是这些价差里为正的比例，
  两者分开报，且样本数必须同时给。
- **⛔ 不给买入建议**：`patrol()` 只输出「这只票以前反复出现、每次是这么成交的」，
  下一板要不要接仍归 09:25 裁决，本模块不参与。
- **rebuild 幂等**：从 judgement_trail + 日线全量重算覆盖，不追加累计。

## 用法

```bash
python3 wealth_code/scripts/stock_arc.py rebuild          # 全量重算（日线有缓存后很快）
python3 wealth_code/scripts/stock_arc.py show 603278      # 单票生涯
python3 wealth_code/scripts/stock_arc.py patrol           # 今日继续跟踪清单
python3 wealth_code/scripts/stock_arc.py stats            # 真实收益分组
```
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # 让 `common.*` 可导入（脚本模式与 pipeline 模式都能用）
    sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / "cache"
TRAIL_DIR = CACHE_DIR / "judgement_trail"
OUT_DIR = CACHE_DIR / "stock_arc"

SCHEMA = "stock_arc_v1"

# 一波的边界：两次涨停之间「没有涨停的交易日」达到 3 天即视为新一波
GAP_NEW_WAVE = 3
# 09:25 准入窗口（与池内裁决硬伤同源：开盘超 5% 不买、低开破预期不接）
GAP_HI = 5.0
GAP_LO = -3.0
# 生涯分级门槛
REPEAT_MIN = 3          # 近 20 个交易日出现 ≥3 次才算「反复出现」
PATROL_WINDOW = 20      # 观察窗口（交易日）
PATROL_ALIVE = 3        # 最后一次涨停距今 ≤3 个交易日仍算「活着」


# ── 小工具 ──────────────────────────────────────────────
def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001 — 坏行跳过，不让一行毁掉整库
            continue
    return rows


_CAL_CACHE: dict[tuple[str, str], list[str]] = {}
_TD_MEMO: dict[str, bool] = {}


def _is_td(day: str) -> bool:
    """交易日判定（进程内记忆）。

    ⚠️ 必须记忆：`common.trading_calendar.is_a_share_trading_day` 每次调用都读日历文件，
    2026-10-23 实测逐日调用 2777 次 ≈ 50 秒，把 rebuild 从 1 秒拖成 1 分钟。记忆后降到 1 秒级。
    """
    if day not in _TD_MEMO:
        from common.trading_calendar import is_a_share_trading_day  # noqa: PLC0415

        _TD_MEMO[day] = bool(is_a_share_trading_day(day))
    return _TD_MEMO[day]


def trading_days(start: str, end: str) -> list[str]:
    """[start, end] 区间内的 A 股交易日（唯一真源 `common.trading_calendar`）。

    ⛔ 不自己写 weekday 规则；导入失败时退回「区间内出现过的涨停日」，
    此时按交易日计的间隔会偏小，但不会凭空多算日子（缺失优于瞎猜）。
    """
    key = (start, end)
    if key in _CAL_CACHE:
        return _CAL_CACHE[key]
    days: list[str] = []
    try:
        from datetime import date, timedelta

        d = date.fromisoformat(start)
        stop = date.fromisoformat(end)
        while d <= stop:
            ds = d.isoformat()
            if _is_td(ds):
                days.append(ds)
            d += timedelta(days=1)
    except Exception:  # noqa: BLE001 — 交易日历不可用：退回空，由调用方用轨迹日期兜底
        days = []
    _CAL_CACHE[key] = days
    return days


def gap_trading_days(d1: str, d2: str, fallback_dates: Iterable[str] = ()) -> int:
    """d1 与 d2 之间**没有涨停/没有交易**的交易日数（严格在两者之间）。"""
    cal = trading_days(d1, d2)
    if cal:
        return max(0, len([x for x in cal if d1 < x < d2]))
    fb = sorted(set(fallback_dates))
    return max(0, len([x for x in fb if d1 < x < d2]))


# ── 取轨迹 ──────────────────────────────────────────────
def load_trail(*, trail_dir: Path | None = None,
               trading_day_only: bool = True) -> list[dict[str, Any]]:
    """读判断轨迹。

    `trading_day_only=True` 时丢弃**非交易日**的行：2026-10-23 实测 `cache/decision_log/2026-09-25`
    目录存在（中秋休市），内容是 09-24 的整份复制 → 轨迹里多出一批幽灵行，会把同一笔交易
    算成两次（大业 09-24/09-25 两天完全同额 1.56 亿、次日价同一个）。⛔ 宁可按日历剔掉，
    也不保留「看着像多出现一天」的假信号。日历不可用时**不剔**（不能凭猜测删数据）。
    """
    trail_dir = trail_dir or TRAIL_DIR
    rows: list[dict[str, Any]] = []
    for p in sorted(trail_dir.glob("*.jsonl")):
        rows.extend(_read_jsonl(p))
    if trading_day_only:
        try:
            rows = [r for r in rows if _is_td(str(r.get("date")))]
        except Exception:  # noqa: BLE001 — 日历不可用：原样保留，由调用方看 index 里的告警
            pass
    rows.sort(key=lambda r: (r.get("code", ""), r.get("date", "")))
    return rows


def phantom_dates(*, trail_dir: Path | None = None) -> list[str]:
    """轨迹里出现、但按交易日历不是交易日的日期（数据质量告警用，⛔ 不参与计算）。"""
    trail_dir = trail_dir or TRAIL_DIR
    dates = {str(r.get("date")) for p in sorted(trail_dir.glob("*.jsonl")) for r in _read_jsonl(p)}
    try:
        return sorted(d for d in dates if d and not _is_td(d))
    except Exception:  # noqa: BLE001
        return []


# ── 可执行层：一天腿 ────────────────────────────────────
def build_leg(row: dict[str, Any], bars: list[dict[str, Any]], *,
              today: str, closed_today: bool = False) -> dict[str, Any]:
    """给「T 日涨停」这条事实补上 T+1 的可执行信息与 T+1/T+2 收益。"""
    from core.kline_store import bar_at, next_bar  # noqa: PLC0415 — 避免循环导入

    date = str(row.get("date"))
    leg: dict[str, Any] = {
        "date": date,
        "boards": int(row.get("boards") or 1),
        "gate": row.get("gate"),
        "pool": row.get("pool"),
        "score": row.get("score"),
        "verdict": row.get("verdict"),
        "cje_yi": row.get("cje_yi"),
        "vol_state": row.get("vol_state"),
        "mech": list(row.get("mech") or []),
        "next_date": None,
        "prev_close": None,
        "entry": None,
        "gap_pct": None,
        "zone": "unknown",
        "fillable": None,
        "entry_ok": None,
        "ret_t1": None,
        "ret_t2": None,
        "pending": True,
        "note": "",
    }
    base = bar_at(bars, date)
    if base:
        leg["prev_close"] = base.get("close")
    nb = next_bar(bars, date, 1)
    if not nb:
        leg["note"] = "次一交易日无行情"
        return leg
    leg["next_date"] = nb["date"]
    # 次日尚未来到、或次日是「今天但还没收盘」→ 结果未定，⛔ 不留半截数字。
    # `closed_today=True` 只允许在「当日收盘后」调用（盘后 eod 传 True，盘中一律 False）。
    if nb["date"] > today or (nb["date"] == today and not closed_today):
        leg["note"] = "次日未收盘"
        return leg
    leg["pending"] = False

    o, c, h, low = nb.get("open"), nb.get("close"), nb.get("high"), nb.get("low")
    vol = nb.get("vol")
    leg["entry"] = o
    if not o or o <= 0 or not c or c <= 0:
        leg["note"] = "次日价格缺失"
        return leg

    one_word = bool(h is not None and low is not None and abs(float(h) - float(low)) < 1e-6)
    leg["fillable"] = (not one_word) and (vol is None or float(vol) > 0)
    if one_word:
        leg["note"] = "次日一字，买不到"
    elif vol is not None and float(vol) <= 0:
        leg["note"] = "次日停牌"

    prev_close = leg["prev_close"]
    if prev_close:
        gap = (float(o) - float(prev_close)) / float(prev_close) * 100.0
        leg["gap_pct"] = round(gap, 2)
        if gap > GAP_HI:
            leg["zone"] = "超上限"
        elif gap < GAP_LO:
            leg["zone"] = "破预期"
        else:
            leg["zone"] = "区间内"
    leg["entry_ok"] = bool(leg["fillable"] and leg["zone"] == "区间内")

    # 账户口径：次日开盘买 → 次日收盘卖
    leg["ret_t1"] = round((float(c) - float(o)) / float(o) * 100.0, 2)
    nb2 = next_bar(bars, date, 2)
    if nb2 and (nb2["date"] < today or (nb2["date"] == today and closed_today)) and nb2.get("close"):
        leg["ret_t2"] = round((float(nb2["close"]) - float(o)) / float(o) * 100.0, 2)
    return leg


# ── 一波 ────────────────────────────────────────────────
def build_episodes(code_rows: list[dict[str, Any]], bars: list[dict[str, Any]], *,
                   today: str, closed_today: bool = False) -> list[dict[str, Any]]:
    """按「断板 ≥3 个交易日」切波，并给每一波算可进场与变现。"""
    if not code_rows:
        return []
    rows = sorted(code_rows, key=lambda r: str(r.get("date")))
    dates = [str(r["date"]) for r in rows]

    groups: list[list[dict[str, Any]]] = [[rows[0]]]
    for prev, cur in zip(rows, rows[1:]):
        gap = gap_trading_days(str(prev["date"]), str(cur["date"]), fallback_dates=dates)
        if gap >= GAP_NEW_WAVE:
            groups.append([cur])
        else:
            groups[-1].append(cur)

    episodes: list[dict[str, Any]] = []
    prev_end: str | None = None
    for g in groups:
        legs = [build_leg(r, bars, today=today, closed_today=closed_today) for r in g]
        heights = [lg["boards"] for lg in legs]
        entry_legs = [lg for lg in legs if lg.get("entry_ok")]
        rets = [lg["ret_t1"] for lg in entry_legs if lg.get("ret_t1") is not None]
        first_pool = next((lg for lg in legs if lg.get("pool")), None)
        first_entry = entry_legs[0] if entry_legs else None

        ep: dict[str, Any] = {
            "schema": SCHEMA,
            "code": str(rows[0].get("code")),
            "name": str(rows[0].get("name") or "").replace(" ", ""),
            "sector": str(rows[0].get("sector") or ""),
            "start_date": legs[0]["date"],
            "end_date": legs[-1]["date"],
            "days": len(legs),
            "heights": heights,
            "first_boards": heights[0],
            "max_boards": max(heights),
            "last_boards": heights[-1],
            "gap_before": (gap_trading_days(prev_end, legs[0]["date"]) if prev_end else 0),
            "pool_days": sum(1 for lg in legs if lg.get("pool")),
            "first_pool_date": first_pool["date"] if first_pool else None,
            "first_pool_boards": first_pool["boards"] if first_pool else None,
            "first_pool_score": first_pool["score"] if first_pool else None,
            "entry_days": len(entry_legs),
            "first_entry_date": first_entry["date"] if first_entry else None,
            "first_entry_boards": first_entry["boards"] if first_entry else None,
            "avg_ret": round(sum(rets) / len(rets), 2) if rets else None,
            "best_ret": round(max(rets), 2) if rets else None,
            "worst_ret": round(min(rets), 2) if rets else None,
            "hit_rate": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1) if rets else None,
            "ret_samples": len(rets),
            "one_word_days": sum(1 for lg in legs if "一字封" in (lg.get("mech") or [])),
            "legs": legs,
        }
        ep["verdict"] = _episode_verdict(ep)
        episodes.append(ep)
        prev_end = legs[-1]["date"]
    return episodes


def _episode_verdict(ep: dict[str, Any]) -> str:
    if ep["entry_days"] == 0:
        if ep["one_word_days"] >= max(1, ep["days"] - 1):
            return "整波一字，买不到"
        return "没给进场点"
    if (ep["avg_ret"] or 0) > 0:
        return "可连续变现" if ep["entry_days"] >= 2 else "单点可赚"
    return "进场就亏"


# ── 生涯 ────────────────────────────────────────────────
def build_profile(code: str, episodes: list[dict[str, Any]], *, today: str) -> dict[str, Any]:
    eps = sorted(episodes, key=lambda e: e["start_date"])
    heights = [e["max_boards"] for e in eps]
    entry_eps = [e for e in eps if e["entry_days"] > 0]
    rets = [lg["ret_t1"] for e in entry_eps for lg in e["legs"]
            if lg.get("entry_ok") and lg.get("ret_t1") is not None]
    first_entry = next((lg for e in eps for lg in e["legs"] if lg.get("entry_ok")), None)
    first_pool = next((lg for e in eps for lg in e["legs"] if lg.get("pool")), None)
    last_leg = eps[-1]["legs"][-1] if eps else {}

    half = max(1, len(heights) // 2)
    early = max(heights[:half]) if heights[:half] else 0
    late = max(heights[half:]) if heights[half:] else 0
    evolve = "走高" if late > early else ("走低" if late < early else "持平")

    avg = (sum(rets) / len(rets)) if rets else None
    if not entry_eps or not rets:
        # 一次都没进得去（全程一字/无行情）＝看得见吃不到
        level = "看得见吃不到"
    elif avg is None or avg <= 0:
        level = "进场就亏"
    elif len(entry_eps) >= 2:
        level = "可连续变现"
    else:
        level = "单次可赚"

    return {
        "schema": SCHEMA,
        "code": code,
        "name": eps[0].get("name") if eps else "",
        "sector": eps[0].get("sector") if eps else "",
        "appearances": sum(e["days"] for e in eps),
        "episodes": len(eps),
        "heights": heights,
        "max_boards": max(heights) if heights else 0,
        "first_date": eps[0]["start_date"] if eps else None,
        "first_boards": eps[0]["first_boards"] if eps else None,
        "last_date": last_leg.get("date"),
        "last_boards": last_leg.get("boards"),
        "first_pool_date": first_pool["date"] if first_pool else None,
        "first_pool_boards": first_pool["boards"] if first_pool else None,
        "first_entry_date": first_entry["date"] if first_entry else None,
        "first_entry_boards": first_entry["boards"] if first_entry else None,
        "entry_days": len(rets),
        "hit_rate": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1) if rets else None,
        "avg_ret": round(sum(rets) / len(rets), 2) if rets else None,
        "best_ret": round(max(rets), 2) if rets else None,
        "worst_ret": round(min(rets), 2) if rets else None,
        "sum_ret": round(sum(rets), 2) if rets else None,
        "ret_samples": len(rets),
        "evolve": evolve,
        "level": level,
        "waves": [{"start": e["start_date"], "end": e["end_date"], "max_boards": e["max_boards"],
                   "entry_days": e["entry_days"], "avg_ret": e["avg_ret"],
                   "verdict": e["verdict"]} for e in eps],
    }


# ── 全量重建 ────────────────────────────────────────────
def rebuild(*, trail_dir: Path | None = None, out_dir: Path | None = None,
            today: str | None = None, fetch_kline: bool = True,
            bars_map: dict[str, list[dict[str, Any]]] | None = None,
            closed_today: bool = False) -> dict[str, Any]:
    """全量重算。`bars_map` 显式传入时不再触网（测试用；生产走 kline_store 缓存）。"""
    from datetime import date as _date

    today = today or _date.today().isoformat()
    out_dir = out_dir or OUT_DIR
    rows = load_trail(trail_dir=trail_dir)
    if not rows:
        return {"schema": SCHEMA, "error": "judgement_trail 为空，先跑 judgement_trail rebuild"}

    by_code: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_code.setdefault(str(r["code"]), []).append(r)

    if bars_map is None:
        bars_map = {}
        if fetch_kline:
            try:
                from core.kline_store import get_many  # noqa: PLC0415

                # 只有「最近 3 个涨停日里还出现过」的票需要补最新行情；其余读缓存即可
                recent_dates = sorted({r["date"] for r in rows})[-3:]
                hot = {c for c, crs in by_code.items()
                       if recent_dates and max(str(x["date"]) for x in crs) >= recent_dates[0]}
                bars_map = get_many(by_code.keys(), end=today, allow_fetch_codes=hot)
            except Exception as exc:  # noqa: BLE001 — 日线取不到就只出事实层，收益留空
                print(f"[ARC] 日线批量获取降级: {exc}")

    all_eps: list[dict[str, Any]] = []
    profiles: dict[str, Any] = {}
    for code, crows in by_code.items():
        eps = build_episodes(crows, bars_map.get(code) or [], today=today,
                             closed_today=closed_today)
        if not eps:
            continue
        all_eps.extend(eps)
        profiles[code] = build_profile(code, eps, today=today)

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "episodes.jsonl").open("w", encoding="utf-8") as fh:
        for ep in sorted(all_eps, key=lambda e: (e["start_date"], e["code"])):
            fh.write(json.dumps(ep, ensure_ascii=False) + "\n")
    (out_dir / "arcs.json").write_text(
        json.dumps(profiles, ensure_ascii=False, indent=1), encoding="utf-8")

    with_kline = sum(1 for c in by_code if bars_map.get(c))
    phantoms = phantom_dates(trail_dir=trail_dir)
    index = {
        "schema": SCHEMA,
        "generated_at": today,
        "codes": len(profiles),
        "episodes": len(all_eps),
        "appearances": len(rows),
        "codes_with_kline": with_kline,
        "codes_without_kline": len(profiles) - with_kline,
        "repeat_codes": sum(1 for p in profiles.values() if p["appearances"] >= REPEAT_MIN),
        "levels": _count_by(profiles, "level"),
        "pending_legs": sum(1 for e in all_eps for lg in e["legs"] if lg.get("pending")),
        "phantom_dates": phantoms,
        "range": [rows[0]["date"], rows[-1]["date"]],
    }
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    return index


def _count_by(profiles: dict[str, Any], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in profiles.values():
        out[str(p.get(key))] = out.get(str(p.get(key)), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def load_arcs(*, out_dir: Path | None = None) -> dict[str, Any]:
    out_dir = out_dir or OUT_DIR
    p = out_dir / "arcs.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def load_episodes(*, out_dir: Path | None = None) -> list[dict[str, Any]]:
    out_dir = out_dir or OUT_DIR
    return _read_jsonl(out_dir / "episodes.jsonl")


def query(code: str, *, out_dir: Path | None = None) -> dict[str, Any]:
    """单票生涯：档案 + 每一波的每一天。"""
    out_dir = out_dir or OUT_DIR
    code = str(code).strip().zfill(6)
    arcs = load_arcs(out_dir=out_dir)
    eps = [e for e in load_episodes(out_dir=out_dir) if e.get("code") == code]
    return {"profile": arcs.get(code), "episodes": eps}


# ── 今日继续跟踪（只观察，不给建议）──────────────────────
def patrol(asof: str, *, arcs: dict[str, Any] | None = None,
           episodes: list[dict[str, Any]] | None = None, window: int = PATROL_WINDOW,
           alive: int = PATROL_ALIVE) -> list[dict[str, Any]]:
    arcs = arcs if arcs is not None else load_arcs()
    episodes = episodes if episodes is not None else load_episodes()
    if not arcs:
        return []

    all_dates = sorted({lg["date"] for e in episodes for lg in e["legs"]})
    recent = [d for d in all_dates if d <= asof][-window:]
    if not recent:
        return []
    recent_set = set(recent)
    alive_dates = [d for d in all_dates if d <= asof][-alive:]

    out: list[dict[str, Any]] = []
    for code, p in arcs.items():
        eps = [e for e in episodes if e.get("code") == code]
        days = [lg for e in eps for lg in e["legs"] if lg["date"] in recent_set]
        if len(days) < REPEAT_MIN:
            continue
        if p.get("last_date") not in alive_dates:
            continue
        out.append({
            "code": code,
            "name": p.get("name"),
            "sector": p.get("sector"),
            "appearances": p.get("appearances"),
            "in_window": len(days),
            "heights": p.get("heights"),
            "last_date": p.get("last_date"),
            "last_boards": p.get("last_boards"),
            "in_pool_now": bool(days[-1].get("pool")),
            "last_gate": days[-1].get("gate"),
            "entry_days": p.get("entry_days"),
            "hit_rate": p.get("hit_rate"),
            "avg_ret": p.get("avg_ret"),
            "level": p.get("level"),
            "evolve": p.get("evolve"),
            "first_entry_boards": p.get("first_entry_boards"),
            "first_pool_boards": p.get("first_pool_boards"),
        })
    out.sort(key=lambda r: (r["level"] == "可连续变现", r["entry_days"] or 0,
                            r["appearances"] or 0), reverse=True)
    return out


# ── 真实收益分组（替代「晋级率」看分离度）────────────────
def update_daily(asof: str | None = None, *, out_dir: Path | None = None,
                 closed_today: bool = False) -> dict[str, Any]:
    """盘后一次做完：重算生涯库 + 冻结当日「继续跟踪清单」。

    产物 `cache/stock_arc/patrol-<date>.json` 供卡面/终端直接读（纯展示）。
    ⛔ 不产生买卖结论，不占接力池名额。
    """
    from datetime import date as _date

    asof = asof or _date.today().isoformat()
    out_dir = out_dir or OUT_DIR
    idx = rebuild(today=asof, out_dir=out_dir, closed_today=closed_today)
    if idx.get("error"):
        return idx
    rows = patrol(asof, arcs=load_arcs(out_dir=out_dir),
                  episodes=load_episodes(out_dir=out_dir))
    payload = {
        "schema": SCHEMA,
        "date": asof,
        "note": "反复出现且仍活着的票，只观察；买不买归 09:25 裁决",
        "count": len(rows),
        "items": rows,
    }
    (out_dir / f"patrol-{asof}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"index": idx, "patrol_count": len(rows), "patrol_path": str(out_dir / f"patrol-{asof}.json")}


def load_patrol(day: str, *, out_dir: Path | None = None) -> dict[str, Any]:
    out_dir = out_dir or OUT_DIR
    p = out_dir / f"patrol-{day}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _median(xs: list[float]) -> float:
    ys = sorted(xs)
    n = len(ys)
    if not n:
        return 0.0
    mid = n // 2
    return ys[mid] if n % 2 else (ys[mid - 1] + ys[mid]) / 2.0


def _prior_bucket(i: int) -> str:
    if i <= 0:
        return "0次"
    if i == 1:
        return "1次"
    if i == 2:
        return "2次"
    if i <= 5:
        return "3-5次"
    return "6次+"


def by_prior_appearances(episodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按「这只票**在此之前**涨停过几次」分组（⛔ 无未来函数）。

    2026-10-23 教训：先用 48 天总次数分组，得到「10 次+ 平均 +2.36%/命中 68%」的漂亮单调曲线；
    换成点位口径（只数当天之前的次数）后塌成 -0.25%/52.5% —— 前者是幸存者偏差，
    因为「总共出现 10 次」本身包含了未来信息。任何分组都必须按腿当日之前的信息切。
    """
    by_code: dict[str, list[dict[str, Any]]] = {}
    for ep in episodes:
        by_code.setdefault(str(ep.get("code")), []).extend(ep["legs"])
    out: dict[str, list[dict[str, Any]]] = {}
    for legs in by_code.values():
        legs.sort(key=lambda x: x["date"])
        for i, lg in enumerate(legs):
            if lg.get("entry_ok"):
                out.setdefault(_prior_bucket(i), []).append(lg)
    return {k: _agg(v, fillable_only=False) for k, v in sorted(out.items())}


def _agg(sel: list[dict[str, Any]], *, fillable_only: bool = False) -> dict[str, Any]:
    rows = [x for x in sel if x.get("ret_t1") is not None]
    if fillable_only:
        rows = [x for x in rows if x.get("fillable")]
    rets = [x["ret_t1"] for x in rows]
    if not rets:
        return {"n": 0}
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    return {
        "n": len(rets),
        "avg": round(sum(rets) / len(rets), 2),
        "median": round(_median(rets), 2),
        "hit": round(len(wins) / len(rets) * 100, 1),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "best": round(max(rets), 2),
        "worst": round(min(rets), 2),
    }


def stats(*, episodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """按「真实价差」分组（不是晋级率）。

    ⚠️ 读结论前先看 `median`：均值容易被少数大赚拉起来，中位数才是「随便做一笔」的典型结果。
    """
    episodes = episodes if episodes is not None else load_episodes()
    legs = [lg for e in episodes for lg in e["legs"]]
    priced = [lg for lg in legs if lg.get("ret_t1") is not None]

    def agg(sel: list[dict[str, Any]]) -> dict[str, Any]:
        return _agg(sel)

    def by(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        buckets: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            buckets.setdefault(str(r.get(key)), []).append(r)
        return {k: agg(v) for k, v in sorted(buckets.items(), key=lambda kv: -len(kv[1]))}

    fillable = [x for x in priced if x.get("fillable")]
    return {
        "priced_legs": len(priced),
        "all_legs": len(legs),
        "pending": sum(1 for x in legs if x.get("pending")),
        "by_gate": by("gate", priced),
        "by_pool": by("pool", priced),
        "by_vol": by("vol_state", [x for x in priced if x.get("boards", 1) >= 2]),
        "by_zone": by("zone", priced),
        "by_boards": by("boards", priced),
        # 点位口径：这只票此前涨停过几次（⛔ 不含未来信息）
        "by_prior_appearances": by_prior_appearances(episodes),
        "fillable_only": {
            "by_gate": by("gate", fillable),
            "by_pool": by("pool", fillable),
            "by_vol": by("vol_state", [x for x in fillable if x.get("boards", 1) >= 2]),
        },
    }
