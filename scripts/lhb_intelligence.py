#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙虎榜全量席位信号沉淀与溢价统计。

输入：cache/hithink_lhb_YYYYMMDD.json（同花顺全量龙虎榜/游资明细）
输出：cache/lhb_intelligence.json（全量信号、已成熟溢价、席位统计、当日重点）
       cache/lhb_kline_cache.json（腾讯前复权日K缓存）

口径：信号日收盘为起点；统计 T+1 开盘、T+1 收盘、T+3 收盘收益。只将游资席位
单票净买入>0、机构单票净买入>0 视作“买入信号”；卖出并不反向伪造成做空收益。
样本不足 MIN_SAMPLES 时只展示记录，不给“赚钱效应好/差”的定性。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from common.tencent_kline import fetch_kline_ohlc  # noqa: E402

MIN_SAMPLES = 5
HORIZONS = {"t1_open": (1, "open"), "t1_close": (1, "close"), "t3_close": (3, "close")}


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _date8(value: str) -> str:
    return str(value or "").replace("-", "")[:8]


def _date10(value: str) -> str:
    s = _date8(value)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else ""


def _eligible(code: str) -> bool:
    """仅保留用户可交易的沪深主板与创业板；排除科创、北交所、转债。"""
    return len(code) == 6 and code.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605"))


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return 0.0


def _signals(docs: list[dict]) -> list[dict]:
    """把机构、游资的全量“单票净买”归一化。"""
    seen: set[tuple[str, str, str, str]] = set()
    out: list[dict] = []
    for doc in docs:
        day = _date8(doc.get("date") or "")
        if len(day) != 8:
            continue
        for seat in doc.get("hotmoney") or []:
            who = str(seat.get("name") or "").strip()
            for row in seat.get("rows") or []:
                code = str(row.get("ticker") or "").split(".")[0]
                amount = _num(row.get("hot_money_item_net_value"))
                key = (day, "hotmoney", who, code)
                if not who or not _eligible(code) or amount <= 0 or key in seen:
                    continue
                seen.add(key)
                out.append({"trade_date": day, "actor_type": "hotmoney", "actor": who,
                            "code": code, "name": row.get("name") or "", "net_buy_yi": round(amount / 1e8, 4),
                            "concepts": [x.get("name") for x in (row.get("concept_list") or [])[:3] if x.get("name")]})
        for row in doc.get("stocks") or []:
            code = str(row.get("ticker") or "").split(".")[0]
            amount = _num(row.get("org_net_value"))
            key = (day, "institution", "机构", code)
            if not _eligible(code) or amount <= 0 or key in seen:
                continue
            seen.add(key)
            out.append({"trade_date": day, "actor_type": "institution", "actor": "机构",
                        "code": code, "name": row.get("name") or "", "net_buy_yi": round(amount / 1e8, 4),
                        "concepts": [x.get("name") for x in (row.get("concept_list") or [])[:3] if x.get("name")]})
    return sorted(out, key=lambda x: (x["trade_date"], x["actor_type"], x["actor"], x["code"]))


def _bars(codes: set[str], cache: dict, fetch: bool) -> tuple[dict, list[str]]:
    failures: list[str] = []
    for code in sorted(codes):
        if fetch:
            rows = fetch_kline_ohlc(code, n=80)
            if rows:
                cache[code] = {str(x["date"]).replace("-", ""): {"open": x.get("open"), "close": x.get("close")} for x in rows}
            elif code not in cache:
                failures.append(code)
    return cache, failures


def _returns(sig: dict, bars: dict, calendar: list[str]) -> dict:
    result = {key: None for key in HORIZONS}
    series = bars.get(sig["code"]) or {}
    t0 = sig["trade_date"]
    base = (series.get(t0) or {}).get("close")
    if not base or t0 not in calendar:
        return result
    i = calendar.index(t0)
    for key, (offset, price_key) in HORIZONS.items():
        if i + offset >= len(calendar):
            continue
        px = (series.get(calendar[i + offset]) or {}).get(price_key)
        if px is not None:
            result[key] = round((float(px) / float(base) - 1) * 100, 2)
    return result


def _today_observations(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """生成当天游资全量观察：席位聚合及同票多席位共同净买。"""
    by_actor: dict[str, list[dict]] = defaultdict(list)
    by_stock: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row["actor_type"] != "hotmoney":
            continue
        by_actor[row["actor"]].append(row)
        by_stock[(row["code"], row["name"])].append(row)

    hotmoney = []
    for actor, items in by_actor.items():
        stocks = sorted(items, key=lambda x: -x["net_buy_yi"])
        hotmoney.append({
            "actor": actor,
            "net_buy_yi": round(sum(x["net_buy_yi"] for x in items), 4),
            "stocks": [{"code": x["code"], "name": x["name"], "net_buy_yi": x["net_buy_yi"]} for x in stocks],
        })
    hotmoney.sort(key=lambda x: -x["net_buy_yi"])

    co_buys = []
    for (code, name), items in by_stock.items():
        actors = sorted({x["actor"] for x in items})
        if len(actors) < 2:
            continue
        co_buys.append({
            "code": code, "name": name, "seat_count": len(actors), "actors": actors,
            "net_buy_yi": round(sum(x["net_buy_yi"] for x in items), 4),
        })
    co_buys.sort(key=lambda x: (-x["net_buy_yi"], -x["seat_count"]))
    return hotmoney, co_buys


def _stat(rows: list[dict], actor: str, actor_type: str) -> dict:
    ret = [x["returns"]["t1_open"] for x in rows if x["returns"].get("t1_open") is not None]
    ret_close = [x["returns"]["t1_close"] for x in rows if x["returns"].get("t1_close") is not None]
    ret3 = [x["returns"]["t3_close"] for x in rows if x["returns"].get("t3_close") is not None]
    mature = len(ret)
    def _avg(v: list[float]) -> float | None:
        return round(mean(v), 2) if v else None
    return {
        "actor": actor, "actor_type": actor_type, "signals": len(rows), "mature_t1": mature,
        "t1_open_avg_pct": _avg(ret), "t1_open_win_rate": round(sum(v > 0 for v in ret) / mature * 100, 1) if mature else None,
        "t1_close_avg_pct": _avg(ret_close), "t3_close_avg_pct": _avg(ret3),
        "qualified": mature >= MIN_SAMPLES,
        # 仅均值略正但胜率不足一半，不得包装成“溢价较好”。
        "label": "样本不足，暂不评价" if mature < MIN_SAMPLES else (
            "次日溢价较好" if (_avg(ret) is not None and _avg(ret) >= 1.0 and sum(v > 0 for v in ret) / mature >= 0.5)
            else "次日溢价偏弱"
        ),
    }


def build(as_of: str | None = None, fetch: bool = True) -> dict:
    paths = sorted(CACHE.glob("hithink_lhb_????????.json"))
    docs = [_load(p, {}) for p in paths]
    docs = [x for x in docs if isinstance(x, dict)]
    signals = _signals(docs)
    if as_of:
        cutoff = _date8(as_of)
        signals = [x for x in signals if x["trade_date"] <= cutoff]
    kcache_path = CACHE / "lhb_kline_cache.json"
    bars, failures = _bars({x["code"] for x in signals}, _load(kcache_path, {}), fetch)
    _save(kcache_path, bars)
    calendar = sorted({d for series in bars.values() if isinstance(series, dict) for d in series})
    for row in signals:
        row["returns"] = _returns(row, bars, calendar)
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in signals:
        grouped[(row["actor_type"], row["actor"])].append(row)
    actor_stats = [_stat(rows, actor, actor_type) for (actor_type, actor), rows in grouped.items()]
    actor_stats.sort(key=lambda x: (not x["qualified"], -(x["t1_open_avg_pct"] or -999), -x["mature_t1"]))
    latest = max((x["trade_date"] for x in signals), default="")
    today = [x for x in signals if x["trade_date"] == latest]
    today.sort(key=lambda x: -x["net_buy_yi"])
    stats_by_actor = {(x["actor_type"], x["actor"]): x for x in actor_stats}
    focus = []
    for row in today:
        stat = stats_by_actor[(row["actor_type"], row["actor"])]
        focus.append({**row, "actor_history": {k: stat[k] for k in ("mature_t1", "t1_open_avg_pct", "t1_open_win_rate", "qualified", "label")}})
    today_hotmoney, today_co_buys = _today_observations(today)
    for item in today_hotmoney:
        stat = stats_by_actor.get(("hotmoney", item["actor"]), {})
        item["actor_history"] = {k: stat.get(k) for k in ("mature_t1", "t1_open_avg_pct", "t1_open_win_rate", "qualified", "label")}
    return {
        "as_of": _date10(as_of or latest), "generated_ts": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "method": {"entry": "信号日收盘", "horizons": "T+1开盘、T+1收盘、T+3收盘", "min_samples": MIN_SAMPLES,
                   "scope": "同花顺龙虎榜全量单票净买；仅沪深主板及创业板；不将卖出反推为做空"},
        "source_files": len(paths), "signal_count": len(signals), "kline_failures": failures,
        "actor_stats": actor_stats, "today_focus": focus,
        "today_hotmoney": today_hotmoney, "today_co_buys": today_co_buys,
        "signals": signals,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="统计截止日 YYYY-MM-DD；默认最新龙虎榜日期")
    p.add_argument("--no-fetch", action="store_true", help="不刷新腾讯日K，仅使用既有缓存")
    args = p.parse_args()
    result = build(args.date, fetch=not args.no_fetch)
    out = CACHE / "lhb_intelligence.json"
    _save(out, result)
    qualified = [x for x in result["actor_stats"] if x["qualified"]]
    print(f"[lhb_intelligence] {result['as_of']} 全量信号{result['signal_count']}条/席位统计{len(result['actor_stats'])}个/可评价{len(qualified)}个 → {out}")
    if result["kline_failures"]:
        print(f"[lhb_intelligence] 日K缺失: {','.join(result['kline_failures'][:12])}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
