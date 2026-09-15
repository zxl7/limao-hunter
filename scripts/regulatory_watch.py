#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监管红线监测 · 情绪拐点信号（2026-08-19 用户定·P1-8）

背景：8/18 晚上交所出手监管爱丽家居（13天12板+214.8%·暂停账户交易）、一鸣食品
（28日偏离累计+202.8%·严重异常波动）、蓝盾光电（7天+197.2%·停牌核查）→ 8/19 高标
集体一字跌停/核按钮，构成短线情绪反转的核心驱动。原系统无此维度，盘前卡未能预警。

功能：
1. 扫描今日涨停池（zt_history 最新），对每只算 10 日累计涨幅 / 30 日累计涨幅（腾讯日K）
2. 命中监管红线（10日≥100% 或 30日≥200%·交易所严重异常波动标准）→ 记录监管风险事件
3. 追加沉淀 cache/regulatory_events.json（按日 events 数组，含标的/规则/时间点）
4. 输出当日监管压力分 regulatory_pressure（0-10），供 feedback_matrix 情绪打分联动

用法：
  python3 scripts/regulatory_watch.py            # 扫描今日涨停池并沉淀
  python3 scripts/regulatory_watch.py --show     # 只读最近沉淀
  python3 scripts/regulatory_watch.py --limit 5  # 只扫涨停池前 N 只（调试降速）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.tencent_kline import fetch_kline  # noqa: E402

BJ_TZ = timezone(timedelta(hours=8))
CACHE_DIR = ROOT / "cache"

# 监管红线（交易所严重异常波动口径）
CUM10_THRESHOLD = 100.0   # 10 个交易日累计涨幅 ≥100%
CUM30_THRESHOLD = 200.0   # 30 个交易日累计涨幅 ≥200%
KLINE_DAYS = 60           # 拉取日K根数（覆盖 30 交易日前收盘 + 停牌缓冲）


def _load_events() -> dict:
    p = CACHE_DIR / "regulatory_events.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"schema": "regulatory_events_v1", "days": []}


def _save_events(ev: dict) -> None:
    (CACHE_DIR / "regulatory_events.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def load_today_zt(date10: str) -> list[dict]:
    """读 zt_history 当日涨停池（与 feedback_matrix 同源）。"""
    p = CACHE_DIR / "zt_history" / f"zt_{date10}.json"
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    lst = d.get("zt_list") or []
    return [x for x in lst if isinstance(x, dict) and x.get("code")]


def _cum_pct(closes: list[float], n: int) -> float | None:
    """n 个交易日前收盘 → 今收的累计涨幅 %。"""
    if len(closes) < n + 1:
        return None
    base = closes[-(n + 1)]
    if not base:
        return None
    return (closes[-1] / base - 1.0) * 100.0


def scan(date10: str, codes: list[str], limit: int | None = None) -> list[dict]:
    """扫描并返回监管命中事件（不写盘，由 main 决定沉淀）。"""
    events: list[dict] = []
    targets = codes if limit is None else codes[:limit]
    for code in targets:
        try:
            k = fetch_kline(code, n=KLINE_DAYS, timeout=8)
        except Exception:
            continue
        if not k:
            continue
        # ⚠️ 按扫描日截断（2026-08-19 修复）：回扫历史日时 K 线含未来数据（如 8/19 收盘
        # 会低估 8/18 的累计涨幅），必须只取 date<=date10 的收盘序列再算。
        k = [(d, c) for d, c in k if d <= date10]
        if len(k) < 11:
            continue
        closes = [float(c) for _, c in k]
        cum10 = _cum_pct(closes, 10)
        cum30 = _cum_pct(closes, 30)
        hit10 = cum10 is not None and cum10 >= CUM10_THRESHOLD
        hit30 = cum30 is not None and cum30 >= CUM30_THRESHOLD
        if hit10 or hit30:
            rules = []
            if hit10:
                rules.append(f"10日累计{cum10:+.1f}%≥100%")
            if hit30:
                rules.append(f"30日累计{cum30:+.1f}%≥200%")
            events.append({
                "code": code,
                "cum10_pct": round(cum10, 1) if cum10 is not None else None,
                "cum30_pct": round(cum30, 1) if cum30 is not None else None,
                "rules": rules,
                "severity": 2 if (hit10 and hit30) else 1,  # 双红线=2（更严重）
            })
        time.sleep(0.05)
    return events


def pressure_score(events: list[dict]) -> float:
    """监管打击强度 → 短线资金出逃意愿（0-10）。双红线 2 分/单红线 1 分，3 只以上递增。"""
    if not events:
        return 0.0
    raw = sum(e.get("severity", 1) for e in events)
    # 非线性：1只双红=2 → 2.5；2只=4 → 5.0；3只=6 → 7.5（封顶 10）
    return round(min(10.0, raw * 1.25), 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="监管红线监测·情绪拐点信号")
    ap.add_argument("--show", action="store_true", help="只读最近沉淀不扫描")
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 只（调试降速）")
    ap.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD（默认今日）")
    args = ap.parse_args()

    ev = _load_events()
    if args.show:
        for day in ev.get("days", [])[-5:]:
            print(f"== {day['date']} | 压力 {day.get('pressure')}/10 | 命中 {len(day.get('events', []))} 只")
            for e in day.get("events", []):
                print(f"   - {e.get('name')} ({e.get('code')}) {e.get('rules')}")
        return 0

    date10 = args.date or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    zt = load_today_zt(date10)
    if not zt:
        print(f"[regulatory_watch] {date10} 无涨停池快照（zt_history 缺失/非交易日），跳过")
        return 0
    codes = [str(x["code"]).strip() for x in zt]
    events = scan(date10, codes, args.limit)
    # 回填名称
    name_map = {str(x["code"]).strip(): x.get("name") or "" for x in zt}
    for e in events:
        e["name"] = name_map.get(e["code"], "")
        e["trigger_date"] = date10

    # 去重：按 date 覆盖旧记录（2026-08-19 修复：原只看 days[-1]，手动回扫同日期会重复追加）
    days = [d for d in ev.get("days", []) if d.get("date") != date10]
    days.append({"date": date10, "pressure": pressure_score(events), "events": events})
    ev["days"] = days
    _save_events(ev)

    print(f"[regulatory_watch] {date10} 扫描 {len(codes)} 只涨停，命中监管红线 {len(events)} 只 | 压力 {pressure_score(events)}/10")
    for e in events:
        print(f"   - {e.get('name')} ({e['code']}) {e['rules']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
