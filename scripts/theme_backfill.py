#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材历史回补（东财 getFryTomorrowList · 覆盖 400+ 交易日）—— 2026-10-13 用户
「你替我直接回补30个交易日的数据，我们可以通过接口修改接口时间，直接获取！！」

**为什么能回补**：选股宝的 live 接口**不行**（实测 `_ts` 换任何时间返回逐字节相同，纯缓存戳）；
但东财「题材投资」接口 `getFryTomorrowList` 的请求体里有 **`lastTradeDate`** 参数，
实测**语义有效**——且一次请求（pageSize 拉大）即返回 **~3800 条 / 400+ 个交易日**的题材催化，
每条自带 `themeName` + `title` + `summary` + **`stockList`（相关个股·带 code）** + `f3` + `cumulateF3`。

**产出**：写入 `cache/top5_history/top5_<日期>.json`（与 `scripts/top5_history.py` **同构**）
→ 直接喂给题材库索引 `theme_index.json`，与选股宝来源的归档**共存**（`source` 字段可辨）。

涨停家数：以「该题材相关个股 ∩ 当日涨停池（东财 getTopicZTPool·date 参数同日实测可用）」计算，
**不编造**；取不到涨停池 → `zt_count=None`（诚实标空）。

⛔ 幂等：目标文件已存在即跳过（不覆盖选股宝来源的既有归档）。
⛔ 幽灵产物闸：写盘前过 `common.trading_calendar`。
⛔ 只回补历史，不再抓实时；不推送、不改策略。
"""
from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

BJ_TZ = timezone(timedelta(hours=8))
THEME_URL = "https://emcfgdata.eastmoney.com/api/themeInvest/getFryTomorrowList"
ZT_URL = ("https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
          "&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt%3Aasc&date={ymd}")
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
DEFAULT_DAYS = 30
STOCKLIST_URL = "https://emcfgdata.eastmoney.com/api/themeInvest/getStockList"
MEMBERS_CACHE = "theme_members.json"


def _now() -> str:
    return datetime.now(BJ_TZ).isoformat()


def _is_trading_day(day: str) -> bool:
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


def _curl(url: str, *, body: dict | None = None, referer: str = "https://emcfgdata.eastmoney.com/") -> str:
    import subprocess
    cmd = ["curl", "-s", "-m", "25", url, "-H", f"User-Agent: {UA}", "-H", f"Referer: {referer}"]
    if body is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json",
                "-d", json.dumps(body, ensure_ascii=False)]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def _random_code() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(35))


def fetch_theme_items(last_trade_date: str = "", *, page_size: int = 2000) -> list[dict[str, Any]]:
    """拉题材催化列表（一次即覆盖 400+ 交易日）。失败 → []（诚实降级）。"""
    body = {"args": {"pageSize": page_size, "lastTradeDate": str(last_trade_date or "")},
            "client": "wap", "clientType": "cfw", "clientVersion": "9001",
            "randomCode": _random_code(), "timestamp": int(time.time() * 1000)}
    try:
        raw = _curl(THEME_URL, body=body)
        data = (json.loads(raw) or {}).get("data") or []
    except Exception:
        return []
    return [x for x in data if isinstance(x, dict)]


def group_by_trade_date(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        day = str(it.get("tradeDate") or "").strip()[:10]
        if len(day) == 10:
            out.setdefault(day, []).append(it)
    return out


def fetch_members(theme_code: str) -> list[dict[str, Any]]:
    """`getStockList` → 该题材**全成分股**（pageSize 1000·按涨幅倒序）。失败 → []。

    ⚠️ 本列表是**当前**成分（题材成分变动慢，用作历史近似）；接口的 `stockList`
       只有 2 只代表股 —— 直接拿它算涨停家数会系统性偏低（实测几乎全 0~2）。
    """
    body = {"args": {"themeCode": str(theme_code), "pageSize": 1000, "pageNum": 1,
                     "sort": -1, "sortField": "f3"},
            "client": "web", "clientVersion": "8.3", "clientType": "cfw",
            "randomCode": _random_code(), "timestamp": int(time.time() * 1000)}
    try:
        raw = _curl(STOCKLIST_URL, body=body)
        data = (json.loads(raw) or {}).get("data")
    except Exception:
        return []
    # 实测返回结构：data.stockList（list[dict]），个股代码字段 = securityCode
    if isinstance(data, dict):
        data = data.get("stockList") or data.get("list") or data.get("items") or []
    return [x for x in (data or []) if isinstance(x, dict)]


def _load_members(cache_dir: Path) -> dict[str, list[str]]:
    try:
        d = json.loads((Path(cache_dir) / "top5_history" / MEMBERS_CACHE).read_text(encoding="utf-8"))
        return {str(k): [str(c) for c in v] for k, v in (d or {}).items() if isinstance(v, list)}
    except Exception:
        return {}


def _save_members(cache_dir: Path, members: dict[str, list[str]]) -> None:
    p = Path(cache_dir) / "top5_history" / MEMBERS_CACHE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(members, ensure_ascii=False), encoding="utf-8")


def fetch_zt_map(day: str) -> dict[str, int]:
    """当日涨停池 → {code: 连板数}（东财 getTopicZTPool·date 参数）。失败 → {}。"""
    try:
        raw = _curl(ZT_URL.format(ymd=day.replace("-", "")), referer="https://quote.eastmoney.com/")
        pool = ((json.loads(raw) or {}).get("data") or {}).get("pool") or []
    except Exception:
        return {}
    out: dict[str, int] = {}
    for r in pool:
        c = str(r.get("c") or "").strip()
        if len(c) == 6:
            try:
                out[c] = int(r.get("lbc") or 0)
            except (TypeError, ValueError):
                out[c] = 0
    return out


def build_themes(items: list[dict[str, Any]], zt_map: dict[str, int],
                 members: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """把某日题材催化 items → 与 top5_history 同构的主题条目。

    `codes` 优先取**全成分股**（`getStockList` 缓存）；缺失时退回接口自带的 2 只代表股。
    `zt_count` = 该题材成分 ∩ 当日涨停池的家数（取不到涨停池 → None，不编造）。
    `leader` 取成分中当日连板数最高者（并列取首个）。
    """
    members = members or {}
    themes = []
    for it in items:
        name = str(it.get("themeName") or "").strip()
        theme_code = str(it.get("themeCode") or "").strip()
        stocks = [s for s in (it.get("stockList") or []) if isinstance(s, dict)]
        rep_codes = [str(s.get("code") or "").split(".")[0].strip() for s in stocks]
        rep_codes = [c for c in rep_codes if len(c) == 6]
        codes = list(members.get(theme_code) or rep_codes)
        if not name:
            continue
        zt_in = [c for c in codes if c in zt_map]
        zt_count = len(zt_in) if zt_map else None
        leader, leader_code = "", ""
        if zt_in:
            leader_code = max(zt_in, key=lambda c: zt_map.get(c, 0))
            leader = next((str(s.get("name") or "") for s in stocks
                           if str(s.get("code") or "").split(".")[0].strip() == leader_code), "")
        cum = it.get("cumulateF3")
        themes.append({
            "name": name, "theme_code": theme_code,
            "zt_count": zt_count, "leader": leader, "leader_code": leader_code,
            "codes": sorted(set(codes)),
            "stocks": [{"code": str(s.get("code") or "").split(".")[0].strip(),
                        "name": s.get("name")} for s in stocks
                       if len(str(s.get("code") or "").split(".")[0].strip()) == 6],
            "official": None, "title": str(it.get("title") or "")[:120],
            "pct": it.get("f3"), "cum_pct": round(float(cum), 2) if isinstance(cum, (int, float)) else None,
            "is_hot": bool(it.get("isHot")), "sort_num": it.get("sortNum"),
        })
    themes.sort(key=lambda t: (t["sort_num"] if isinstance(t["sort_num"], int) else 999))
    return themes


def backfill(cache_dir: Path, *, days: int = DEFAULT_DAYS, last_trade_date: str = "",
             out_dir: Path | None = None, pages: int = 2000) -> dict[str, Any]:
    """回补最近 `days` 个交易日的题材 → `cache/top5_history/top5_<日期>.json`（幂等）。"""
    cache_dir = Path(cache_dir)
    d = Path(out_dir) if out_dir else cache_dir / "top5_history"
    items = fetch_theme_items(last_trade_date, page_size=pages)
    if not items:
        return {"status": "no_source", "days": 0, "written": 0, "skipped": 0, "not_trading_day": []}
    by_day = group_by_trade_date(items)
    if last_trade_date:
        by_day = {k: v for k, v in by_day.items() if k <= last_trade_date}
    targets = sorted(by_day)[-int(days):]
    written, skipped, bad_days = 0, 0, []
    d.mkdir(parents=True, exist_ok=True)
    # 成分股缓存（按 themeCode·跨日复用）——⛔ 只用代表股 2 只会让涨停家数系统性偏低
    members = _load_members(cache_dir)
    need = sorted({str(it.get("themeCode") or "").strip() for day in targets
                   for it in by_day[day]} - set(members) - {""})
    for code in need:
        got = fetch_members(code)
        members[code] = [c for c in (str(s.get("securityCode") or s.get("code") or "").split(".")[0].strip()
                                     for s in got) if len(c) == 6]
    if need:
        _save_members(cache_dir, members)
    for day in targets:
        if not _is_trading_day(day):
            bad_days.append(day)
            continue
        target = d / f"top5_{day}.json"
        if target.exists():
            skipped += 1
            continue
        themes = build_themes(by_day[day], fetch_zt_map(day), members)
        target.write_text(json.dumps(
            {"date": day, "source": "eastmoney getFryTomorrowList + getTopicZTPool",
             "archived_at": _now(), "themes": themes}, ensure_ascii=False, indent=1), encoding="utf-8")
        written += 1
    return {"status": "ok", "days": len(targets), "written": written, "skipped": skipped,
            "not_trading_day": bad_days, "range": [targets[0], targets[-1]] if targets else []}


def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="题材历史回补（东财题材接口·date 参数）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--last-trade-date", default="", help="缺省＝接口当前最新")
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "cache")
    ap.add_argument("--no-index", action="store_true", help="回补后不重建题材库索引")
    a = ap.parse_args(argv)
    r = backfill(a.cache_dir, days=a.days, last_trade_date=a.last_trade_date)
    print(f"[theme_backfill] {r['status']} | 目标 {r.get('days')} 日 → 新写 {r.get('written')}"
          f" / 跳过 {r.get('skipped')} | 区间 {r.get('range')}")
    if r.get("not_trading_day"):
        print(f"[theme_backfill] ⛔ 非交易日已拦下: {r['not_trading_day']}")
    if r.get("written") and not a.no_index:
        import top5_history as th
        ix = th.rebuild_index(a.cache_dir)
        print(f"[theme_backfill] 题材库：{ix['day_count']} 日 / {ix['theme_count']} 个题材")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
