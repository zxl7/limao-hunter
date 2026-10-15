#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""股性基因数据层（2026-09-09 用户「股性很重要·连板/涨停溢价/竞价基因全做」）。

三类基因全部由「东财涨停池逐日原始 + 腾讯日K」派生，不依赖第三方"基因标签"：
  涨停基因 = 涨停频率 / 最高连板 / 平均首封时间 / 炸板率 / 连板晋级成功率（纯 pool 序列）
  溢价基因 = 涨停后次日 open 高开% / 次日收盘溢价%（pool + 次日腾讯日K open/high/close）
  竞价基因 = 高开涨停(竞价抢筹) / 低开涨停(分歧转一致) 占比（同日 open vs prev_close 日K）

用法:
  python3 scripts/zt_genes.py fetch  --days 60             # 回溯涨停池 → cache/zt_genes/pool_YYYYMMDD.json
  python3 scripts/zt_genes.py gene   --scope today          # 今日涨停∪top5成分∪候选 → gene_{code}.json
  python3 scripts/zt_genes.py gene   --codes 600127,002702  # 指定票
  python3 scripts/zt_genes.py summary                       # 今日涨停池股性榜 → cache/zt_genes/summary.json
盘后链（capture_daily 结尾）增量跑 fetch --days 1 即可维持逐日积累。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BJ_TZ = timezone(timedelta(hours=8))   # 同花顺 date_ms → 北京时间（2026-09-09 双源）
ROOT = HERE.parent
CACHE = ROOT / "cache"
GENES = CACHE / "zt_genes"

sys.path.insert(0, str(ROOT))
from common.http_client import http_get  # noqa: E402

ZT_URL = ("https://push2ex.eastmoney.com/getTopicZTPool"
          "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=400"
          "&sort=fbt%3Aasc&date={date}&_={ts}")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      "Referer": "https://quote.eastmoney.com/ztb/"}
WINDOW_DAYS = 60          # 股性统计窗口（交易日）
GENE_MAX = 3.0            # 炸板率显示阈值（超此封顶防单日灾难值）
KLINE_N = 64              # 腾讯日K回补根数（≈3个月交易日·覆盖窗口+次日；腾讯连发会限流勿调大）
KLINE_TTL_DAYS = 1        # 单票日K磁盘缓存有效期（天）——80票连拉必被腾讯限流(实测 501)，必须缓存


# ── 抓取层 ────────────────────────────────────────────────
def _trade_days(n: int) -> list[str]:
    """最近 n 个交易日（截至今天）YYYYMMDD 升序。akshare 新浪日历；失败降级纯工作日过滤。"""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import akshare as ak
        cal = ak.tool_trade_date_hist_sina()
        vals = list(cal["trade_date"])
        today = datetime.now().date()
        ds = []
        for v in vals:
            d = v.date() if hasattr(v, "date") and type(v).__name__ == "datetime" else v
            ds.append(d)
        ds = [d for d in ds if d <= today]
        out = [d.strftime("%Y%m%d") for d in ds[-n:]]
        if out:
            return out
    except Exception as e:
        print(f"[zt_genes] 交易日历失败({e})，退出手算工作日")
    # 兜底：近 7n/5 个自然日过滤周末（法定假日会漏抓→抓取端空数据自动跳过）
    out = []
    d = datetime.now().date()
    while len(out) < n:
        if d.weekday() < 5:  # trading-day-fallback: 唯一真源不可用时的周末兜底
            out.append(d.strftime("%Y%m%d"))
        d -= __import__("datetime").timedelta(days=1)
    return sorted(out)


def fetch_day(date_ymd: str, tries: int = 3) -> list[dict]:
    """东财 getTopicZTPool 单日涨停池 → 规范化 list。空/失败 → []。"""
    url = ZT_URL.format(date=date_ymd, ts=int(time.time() * 1000))
    for i in range(tries):
        try:
            r = http_get(url, t=15, headers=UA, retries=0)
            pool = ((r or {}).get("data") or {}).get("pool") or []
            return [_norm(x) for x in pool if x.get("c")]
        except Exception as e:
            if i == tries - 1:
                print(f"[zt_genes] {date_ymd} 抓取失败: {e}")
            time.sleep(0.4)
    return []


def _norm(x: dict) -> dict:
    """东财原字段 → 精简语义字段（涨停基因计算只需这些）。"""
    tt = x.get("zttj") or {}
    try:
        fund_yi = round((float(x.get("fund") or 0) or 0) / 1e8, 2)
    except (TypeError, ValueError):
        fund_yi = 0.0
    return {
        "code": str(x.get("c") or ""),
        "name": str(x.get("n") or ""),
        "zt_boards": int(x.get("lbc") or 0),          # 连板数
        "zt_days": int(tt.get("days") or 0),          # N天
        "zt_times": int(tt.get("ct") or 0),           # N板(近N天涨停次数)
        "seal_min": _hhmm_to_min(x.get("fbt")),       # 首封时间(分钟·越小越早)
        "last_seal_min": _hhmm_to_min(x.get("lbt")),  # 最后封板
        "broken": int(x.get("zbc") or 0),             # 炸板次数
        "seal_fund_yi": fund_yi,                      # 封单资金(亿)
        "pct": round(float(x.get("zdp") or 0), 2),
        "hs": round(float(x.get("hs") or 0), 2),
        "industry": str(x.get("hybk") or ""),
    }


def _hhmm_to_min(v) -> int | None:
    try:
        s = str(int(v)).zfill(6)      # 092500 → 9h25m
        return int(s[:2]) * 60 + int(s[2:4])
    except (TypeError, ValueError):
        return None


def cmd_fetch(days: int) -> None:
    GENES.mkdir(parents=True, exist_ok=True)
    want = [d for d in _trade_days(days + 1) if d < datetime.now().strftime("%Y%m%d")][-days:]
    have = {p.stem[5:] for p in GENES.glob("pool_*.json")}
    todo = [d for d in want if d not in have]
    print(f"[zt_genes] 窗口 {len(want)} 交易日 | 已有 {len(have)} | 待抓 {len(todo)}")
    for d in todo:
        pool = fetch_day(d)
        if pool:
            out = {"date": d, "pool": pool, "ts": datetime.now().isoformat()}
            (GENES / f"pool_{d}.json").write_text(
                json.dumps(out, ensure_ascii=False), encoding="utf-8")
            print(f"  {d}: {len(pool)} 只涨停 ✓")
        else:
            print(f"  {d}: 空（可能非交易日/接口限流）")
        time.sleep(0.25)


# ── 基因构建层 ──────────────────────────────────────────────
def _load_pools() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in sorted(GENES.glob("pool_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("pool"):
                out[d["date"]] = d["pool"]
        except (OSError, ValueError):
            continue
    return out


def _kline_map(code: str) -> dict[str, dict]:
    """日K：date(YYYYMMDD) → {open,close,high,low,prev_close}。双源回退：腾讯(缓存优先)→新浪。

    腾讯 ifzq 对连续请求限流(实测 501)——新浪 money.finance.sina.com.cn 稳定无 UA 限制；
    磁盘缓存 cache/zt_genes/kl_{code}.json（当日有效）避免重复拉。全失败 → {}（溢价基因为 None 优雅降级）。
    """
    cache_p = GENES / f"kl_{code}.json"
    try:
        if cache_p.exists():
            c = json.loads(cache_p.read_text(encoding="utf-8"))
            age = (datetime.now() - datetime.fromisoformat(c.get("ts", ""))).days if c.get("ts") else 99
            if age < KLINE_TTL_DAYS and isinstance(c.get("rows"), list):
                return {r["d"]: r for r in c["rows"]}
    except Exception:
        pass
    rows = _tencent_rows(code) or _sina_rows(code)
    if not rows:
        return {}
    out = {r["d"]: r for r in rows}
    try:
        cache_p.write_text(json.dumps({"ts": datetime.now().isoformat(), "rows": rows}),
                           encoding="utf-8")
    except Exception:
        pass
    return out


def _tencent_rows(code: str) -> list[dict] | None:
    """腾讯 ifzq 前复权日K → rows；限流/失败返回 None（交给新浪回退）。"""
    import common.tencent_kline as tk
    sec = f"{tk.tk_market(code)}{code}"
    url = tk.KLINE_URL.format(sec=sec, n=KLINE_N)
    try:
        r = http_get(url, t=8, headers={"User-Agent": "Mozilla/5.0"}, retries=0)
        d = ((r or {}).get("data") or {}).get(sec) or {}
        kl = d.get("qfqday") or d.get("day") or []
    except Exception:
        return None
    if not kl:
        return None
    rows, prev_close = [], None
    for row in kl:
        try:
            dt, o, c, hi, lo = str(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])
        except (IndexError, TypeError, ValueError):
            continue
        rows.append({"d": dt.replace("-", ""), "open": o, "close": c,
                     "high": hi, "low": lo, "prev_close": prev_close})
        prev_close = c
    return rows or None


def _sina_rows(code: str) -> list[dict] | None:
    """新浪日K（不复权·未做除权平滑，60日窗口内偏差可忽略）→ rows。"""
    import common.tencent_kline as tk
    pre = tk.tk_market(code)
    if pre == "bj":          # 新浪北交所日K不支持 → 放弃（北交所池股性暂缺）
        return None
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={pre}{code}&scale=240&ma=no&datalen={KLINE_N + 10}")
    try:
        raw = http_get(url, t=8, headers={"User-Agent": "Mozilla/5.0"}, retries=1, parse_json=False)
    except Exception:
        return None
    if not raw or not raw.strip() or raw.strip().startswith("<"):
        return None
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    rows, prev_close = [], None
    for it in d or []:
        try:
            o, c, hi, lo = (float(it["open"]), float(it["close"]),
                            float(it["high"]), float(it["low"]))
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({"d": str(it["day"]).replace("-", ""), "open": o, "close": c,
                     "high": hi, "low": lo, "prev_close": prev_close})
        prev_close = c
    return rows or None


def build_gene(code: str, pools: dict[str, list[dict]], kl: dict | None = None,
               with_kline: bool = True) -> dict | None:
    """单票股性档案（见 docstring 三类基因）。无任何涨停记录 → None。
    with_kline=False（批量/全量场景）跳过腾讯日K→溢价/竞价基因为 None，防限流。"""
    dates = sorted(pools)
    klines = kl if kl is not None else (_kline_map(code) if with_kline else {})
    recs: list[tuple[str, dict]] = []
    for dt in dates:
        for x in pools[dt]:
            if x.get("code") == code:
                recs.append((dt, x))
                break
    if not recs:
        return None
    # ── 涨停基因 ──
    zt_n = len(recs)
    max_lbc = max((r[1].get("zt_boards") or 0) for r in recs)
    seal_ok = [r[1]["seal_min"] for r in recs if r[1].get("seal_min") is not None]
    seal_avg = (sum(seal_ok) / len(seal_ok)) if seal_ok else None
    broken_sum = sum(r[1].get("broken") or 0 for r in recs)
    broken_rate = round(min(broken_sum / max(1, zt_n), GENE_MAX), 2)
    # 晋级成功率：d 日涨停 → d+1（下一 pool 日）仍涨停=晋级成功
    promo_ok = promo_ttl = 0
    zt_codes_by_day = {}
    for dt in dates:
        zt_codes_by_day[dt] = {x["code"] for x in pools[dt]}
    for i, (dt, x) in enumerate(recs):
        if i + 1 < len(dates):
            nxt = dates[i + 1]
            promo_ttl += 1
            if code in zt_codes_by_day[nxt]:
                promo_ok += 1
    promo_rate = round(promo_ok / promo_ttl * 100, 1) if promo_ttl else None
    # ── 溢价/竞价基因（腾讯日K join；涨停日收盘=涨停价）──
    prem_open, prem_close, hi_open_zt, low_open_zt = [], [], 0, 0
    for dt, x in recs:
        k = klines.get(dt)
        if not k or not k.get("prev_close") or k["prev_close"] <= 0:
            continue
        gap = (k["open"] / k["prev_close"] - 1) * 100
        if gap >= 3.0:
            hi_open_zt += 1
        elif gap <= -1.0:
            low_open_zt += 1
        # 次日溢价：取下一 pool 日（下一交易日）open/收盘 相对本涨停日收盘
        idx = dates.index(dt)
        if idx + 1 < len(dates):
            nk = klines.get(dates[idx + 1])
            if nk and k["close"] > 0:
                prem_open.append((nk["open"] / k["close"] - 1) * 100)
                prem_close.append((nk["close"] / k["close"] - 1) * 100)
    prem_open_avg = round(sum(prem_open) / len(prem_open), 2) if prem_open else None
    prem_close_avg = round(sum(prem_close) / len(prem_close), 2) if prem_close else None
    hi_zt_ratio = round(hi_open_zt / zt_n * 100, 1) if zt_n else 0.0
    low_zt_ratio = round(low_open_zt / zt_n * 100, 1) if zt_n else 0.0
    # ── 股性分（0-100·权重可调·初版经验值）──
    win = max(1, len(dates))                       # 实际窗口交易日（东财接口仅保留近1月→自适应）
    f_zt = min(zt_n / max(1, win // 3), 1.0) * 30        # 涨停频率（窗口1/3 内满频=满分30）
    f_lbc = min(max_lbc / 10.0, 1.0) * 20                        # 最高连板（10板封顶=满分20）
    f_seal = (max(0.0, 1.0 - (seal_avg - 540) / 180) if seal_avg else 0.0) * 15  # 首封 09:00-12:00 早封满分
    f_broken = max(0.0, 1.0 - broken_rate) * 10                  # 炸板率 0=满分
    f_promo = (promo_rate / 100.0 if promo_rate is not None else 0.3) * 15  # 晋级率 100%=满分
    f_prem = max(-0.5, min(1.0, (prem_close_avg or 0) / 5.0)) * 10  # 次日均溢价 -2.5%..+5% → -5..10
    score = round(max(0.0, f_zt + f_lbc + f_seal + f_broken + f_promo + f_prem), 1)
    return {
        "code": code, "name": recs[-1][1].get("name") or "", "industry": recs[-1][1].get("industry") or "",
        "zt_n": zt_n, "max_lbc": max_lbc, "seal_avg_min": round(seal_avg, 1) if seal_avg else None,
        "broken_rate": broken_rate, "promo_rate": promo_rate,
        "prem_open_avg": prem_open_avg, "prem_close_avg": prem_close_avg,
        "hi_open_zt_ratio": hi_zt_ratio, "low_open_zt_ratio": low_zt_ratio,
        "gene_score": score,
    }


def cmd_gene(scope: str | None = None, codes: list[str] | None = None,
             with_kline: bool = True) -> None:
    pools = _load_pools()
    if not pools:
        print("[zt_genes] 无 pool 数据，先跑 fetch --days 60")
        return
    targets: set[str] = set(codes or [])
    if scope == "today":
        latest = sorted(pools)[-1]
        for x in pools[latest]:
            targets.add(x["code"])
        # 并入 top5 成分（候选常用票）
        try:
            lj = json.loads((CACHE / "pulse_snapshots" / "latest.json").read_text(encoding="utf-8"))
            for s in (lj.get("top5") or []):
                for z in (s.get("zt_stocks") or []):
                    if z.get("code"):
                        targets.add(str(z["code"])[:6])
        except Exception:
            pass
    if not targets:
        print("[zt_genes] 未指定目标（--scope today 或 --codes）")
        return
    GENES.mkdir(parents=True, exist_ok=True)
    rows = []
    for code in sorted(targets):
        g = build_gene(code, pools, with_kline=with_kline)
        if not g:
            print(f"  {code}: 窗口内无涨停记录")
            continue
        (GENES / f"gene_{code}.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append(g)
        print(f"  {code} {g['name'] or ''}: 股性分{g['gene_score']} | 涨停{g['zt_n']}次 最高{g['max_lbc']}板 "
              f"晋级{g['promo_rate']}% 溢价{g['prem_close_avg']}% 炸板率{g['broken_rate']}")
    print(f"[zt_genes] 股性档案 {len(rows)} 只 → {GENES}")


def cmd_summary() -> None:
    pools = _load_pools()
    if not pools:
        return
    latest_dt = sorted(pools)[-1]
    latest = pools[latest_dt]
    rows = []
    for x in latest:
        # pool-only 快路径（盘后 70+ 票连拉日K必被腾讯限流；复盘只需涨停/晋级基因+分数）
        g = build_gene(x["code"], pools, with_kline=False)
        if g:
            rows.append(g)
    rows.sort(key=lambda r: -(r.get("gene_score") or 0))
    out = {"date": latest_dt, "count": len(rows),
           "top": [{k: r[k] for k in ("code", "name", "industry", "zt_boards") if k in r} | {
               "gene_score": r["gene_score"], "zt_n": r["zt_n"], "max_lbc": r["max_lbc"],
               "promo_rate": r["promo_rate"], "prem_close_avg": r["prem_close_avg"],
               "broken_rate": r["broken_rate"]} for r in rows[:30]]}
    # zt_boards 需从 pool 补（build_gene 档案没带当日连板）→ 直接回填
    for t in out["top"]:
        for x in latest:
            if x["code"] == t["code"]:
                t["zt_boards"] = x["zt_boards"]
                t["seal_min"] = x["seal_min"]
                break
    (CACHE / "zt_genes" / "summary.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[zt_genes] summary 已生成：{latest_dt} 涨停 {len(latest)} 只 | 股性榜前10：")
    for r in rows[:10]:
        print(f"  {r['code']} {r['name']}: {r['gene_score']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="股性基因数据层")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--days", type=int, default=WINDOW_DAYS)
    g = sub.add_parser("gene"); g.add_argument("--scope", choices=["today"], default=None)
    g.add_argument("--codes", default=None, help="逗号分隔 6 位代码")
    g.add_argument("--no-kline", action="store_true", help="跳过腾讯日K(仅涨停/晋级基因·批量快跑)")
    s = sub.add_parser("summary")
    h = sub.add_parser("hist"); h.add_argument("--codes", required=True, help="逗号分隔 6 位代码")
    a = ap.parse_args()
    if a.cmd == "fetch":
        cmd_fetch(a.days)
    elif a.cmd == "gene":
        # 全量(scope today)默认不拉日K防腾讯限流(80票连拉必501)；codes 小集合默认拉(有缓存)
        cmd_gene(a.scope, [c.strip() for c in (a.codes or "").split(",") if c.strip()],
                 with_kline=not (a.no_kline or a.scope == "today"))
    elif a.cmd == "summary":
        cmd_summary()
    elif a.cmd == "hist":
        cmd_hist([c.strip() for c in (a.codes or "").split(",") if c.strip()])





# ── 历史涨停基因（2026-09-09 用户「要的是历史涨停基因」）──────────────
# zt_pool 仅存近 1 个月 → 非"历史"。长历史 = 新浪**不复权**日K(datalen 大) 按涨停价
# 精确判定（close≈涨停价），自算涨停/连板/晋级/首板次日溢价——最多 8 年可回溯。
HIST_KLINE_N = 1500     # 历史基因回溯根数（≈6年·新浪 datalen 上限 2000+）


def _is_zt_row(row: dict) -> bool:
    """不复权日K涨停判定：主板 +10%（沪60/深00/000/002…）、创业板300/301/科创688 +20%。
    判定=收盘价贴住涨停价（round(昨收×1.1,2) 容差 0.005），ST 5% 自然排除。"""
    c = float(row["close"]); pc = float(row.get("prev_close") or 0)
    if pc <= 0:
        return False
    code = str(row.get("code") or "")
    lim = 1.2 if code[:3] in ("300", "301", "688") else 1.1
    # 容差 0.006：close/涨停价浮点化误差 ≤0.005（round 银行家舍入边界），真实 2 位小数价差几乎为 0
    return abs(c - round(pc * lim, 2)) <= 0.006


def _limit_rows(rows: list[dict], code: str) -> list[dict]:
    """给 rows 补 code 并前向填 prev_close（新浪行无昨收→用上一行 close 即昨收）。"""
    out, pc = [], None
    for r in rows:
        rr = dict(r); rr["code"] = code
        if pc is not None:
            rr["prev_close"] = pc
        else:
            rr["prev_close"] = 0.0
        out.append(rr)
        pc = float(r["close"])
    return out


def build_hist_gene(code: str, rows: list[dict] | None = None) -> dict | None:
    """长历史涨停基因（不复权日K·纯函数供单测·数据源=同花顺 REST 优先→新浪回退）。

    涨停定义=收盘贴涨停价(_is_zt_row)。**统计窗口统一 = 近250交易日**（2026-09-09
    双源验证修复：旧版涨停次数按250日窗、但晋级/溢价按全历史6年 → 口径混用失真，
    实证 603308 应流股份 全历史首板高开 1.35% vs 250日窗 0.63%（同花顺确认））。
    输出（均 250 日窗口，除标注全历史）：
      zt_250 / zt_500          近250/500 交易日涨停次数（频率）
      max_boards_hist          **全历史**最长连板（「历史最长X板」语义·卡面用）
      board_seq                近20段历史连板分布
      promo_rate_hist          250日窗 连板晋级成功率（涨停次日再涨停）
      prem_open_hist / prem_close_hist   250日窗 涨停次日 open/close 溢价均值%
      first_prem_open_hist     250日窗 「首板」(前一交易日未涨停)次日开盘溢价均值%
    """
    rows = rows or _fetch_hist_rows(code)
    if not rows:
        return None
    data = _limit_rows(rows, code)
    n = len(data)
    zt_idx = [i for i in range(n) if _is_zt_row(data[i])]
    if not zt_idx:
        return None
    win0 = n - 250                      # 250 日窗口起点（含涨停判定用其前一根=首板判断）
    zt_250 = sum(1 for i in zt_idx if i >= win0)
    zt_500 = sum(1 for i in zt_idx if i >= n - 500)
    # 连板序列（全历史·供 max_boards 与分布）：连续 zt_idx 间隔==1 天累计
    seqs, cur = [], 1
    for a, b in zip(zt_idx, zt_idx[1:]):
        cur = cur + 1 if b - a == 1 else (seqs.append(cur) or 1)
    seqs.append(cur)
    # 250日窗内 晋级 + 次日 open/close 溢价 + 首板溢价（样本行须在窗口内且有次日行）
    promo_ok = promo_ttl = 0
    prem_open, prem_close = [], []
    first_prem = []
    for i in zt_idx:
        if i < win0 or i + 1 >= n:      # 只统计 250 日窗内（末日无次日样本跳过）
            continue
        nxt = data[i + 1]
        pc_close = float(data[i]["close"])
        if pc_close <= 0:
            continue
        prem_open.append((float(nxt["open"]) / pc_close - 1) * 100)
        prem_close.append((float(nxt["close"]) / pc_close - 1) * 100)
        promo_ttl += 1
        if _is_zt_row(nxt):
            promo_ok += 1
        if i == 0 or not _is_zt_row(data[i - 1]):     # 首板（前一交易日非涨停）
            first_prem.append((float(nxt["open"]) / pc_close - 1) * 100)
    return {
        "code": code,
        "first_day": data[0]["d"], "last_day": data[-1]["d"],
        "zt_total": len(zt_idx), "zt_250": zt_250, "zt_500": zt_500,
        "max_boards_hist": max(seqs) if seqs else 0,
        "board_seq": seqs[-20:],
        "promo_rate_hist": round(promo_ok / promo_ttl * 100, 1) if promo_ttl else None,
        "prem_open_hist": round(sum(prem_open) / len(prem_open), 2) if prem_open else None,
        "prem_close_hist": round(sum(prem_close) / len(prem_close), 2) if prem_close else None,
        "first_prem_open_hist": round(sum(first_prem) / len(first_prem), 2) if first_prem else None,
        "sample_n": n,
    }


def _ths_key() -> str | None:
    """同花顺 REST 统一 API Key（用户级 credentials.env），找不到返回 None（走新浪回退）。"""
    import os
    for cand in (os.path.expanduser("~/Library/Application Support/hithink-finance/credentials.env"),
                 os.path.expanduser("~/.config/hithink-finance/credentials.env"),
                 os.path.expanduser("~/.hithink-finance/credentials.env")):
        try:
            for line in open(cand):
                m = re.match(r'\s*(?:export\s+)?HITHINK_FINANCE_API_KEY\s*=\s*["\']?([^"\'\n]+)', line)
                if m:
                    return m.group(1).strip()
        except Exception:
            continue
    return os.environ.get("HITHINK_FINANCE_API_KEY")


def _fetch_ths_kline(code: str) -> list[dict] | None:
    """同花顺历史日K（REST /api/a-share/prices/historical·adjust=none 不复权·6年窗）→
    统一 {d(YYYYMMDD),open,close,high,low} 行。失败返回 None（供新浪回退）。"""
    import common.tencent_kline as tk
    key = _ths_key()
    if not key:
        return None
    pre = tk.tk_market(code)
    if pre == "bj":
        return None
    sfx = ".SH" if pre == "sh" else ".SZ"
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(6.2 * 365.25 * 86400 * 1000)     # ≈6 年窗（覆盖 250 交易日+首板判定余量+历史最长板）
    url = ("https://fuyao.aicubes.cn/api/a-share/prices/historical"
           f"?thscode={code}{sfx}&interval=1d&start={start_ms}&end={now_ms}&adjust=none")
    try:
        d = json.loads(http_get(url, t=15, headers={"X-api-key": key, "User-Agent": "Mozilla/5.0"},
                                retries=2, parse_json=True))
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("code") != 0:
        return None
    items = (d.get("data") or {}).get("item") or []
    rows = []
    for it in items:
        try:
            rows.append({"d": datetime.fromtimestamp(it["date_ms"] / 1000, BJ_TZ).strftime("%Y%m%d"),
                         "open": float(it["open_price"]), "close": float(it["close_price"]),
                         "high": float(it["high_price"]), "low": float(it["low_price"])})
        except (KeyError, TypeError, ValueError):
            continue
    return rows or None


def _fetch_hist_rows(code: str) -> list[dict] | None:
    """长历史不复权日K（**同花顺 REST 优先 → 新浪回退**·2026-09-09 用户「用同花顺接口双重验证」），
    带**当日**磁盘缓存（任一源命中即复用·缓存行结构同构无需分源）。

    ⛔ 2026-09-15 修（数据准确性·实证）：原实现读缓存时**不看日期、永久复用**（写盘只存 rows/src），
    导致「反复进候选的票」其历史基因被冻结在首次抓取日——09-15 实测 5 只候选的 K 线停在
    09-08/09-10/09-11，近一年涨停次数/晋级率/首板次日高开全部按旧窗口算（000993 晋级率 30.0%
    实际已 46.2%）。现改为**跨日即失效重抓**（与 kl_ 短缓存 KLINE_TTL_DAYS=1 同语义）；
    重抓失败时**回退旧缓存**（优雅降级，不让基因行整体消失）。文档自述「当日磁盘缓存」本就如此。
    """
    cache_p = GENES / f"kl_hist_{code}.json"
    cached: list[dict] | None = None
    try:
        if cache_p.exists():
            c = json.loads(cache_p.read_text(encoding="utf-8"))
            if c.get("rows"):
                cached = c["rows"]
                if str(c.get("date") or "") == datetime.now().strftime("%Y%m%d"):
                    return cached          # 当日缓存命中
    except Exception:
        pass
    rows = _fetch_ths_kline(code)
    src = "ths"
    if not rows:
        rows = _fetch_sina_kline(code)      # 回退新浪
        src = "sina"
    if rows:
        try:
            cache_p.write_text(json.dumps({"rows": rows, "src": src,
                                           "date": datetime.now().strftime("%Y%m%d")}),
                               encoding="utf-8")
        except Exception:
            pass
    return rows or cached


def _fetch_sina_kline(code: str) -> list[dict] | None:
    """新浪长历史不复权日K（datalen=HIST_KLINE_N）——历史基因回退源（同花顺不可用/无 key 时）。"""
    import common.tencent_kline as tk
    pre = tk.tk_market(code)
    if pre == "bj":
        return None
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={pre}{code}&scale=240&ma=no&datalen={HIST_KLINE_N}")
    try:
        raw = http_get(url, t=12, headers={"User-Agent": "Mozilla/5.0"}, retries=1, parse_json=False)
    except Exception:
        return None
    if not raw or raw.strip().startswith("<"):
        return None
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    rows = [{"d": str(it["day"]).replace("-", ""), "open": float(it["open"]),
             "close": float(it["close"]), "high": float(it["high"]), "low": float(it["low"])}
            for it in d or [] if it.get("day")]
    return rows or None


def cmd_hist(codes: list[str]) -> None:
    GENES.mkdir(parents=True, exist_ok=True)
    for code in codes:
        g = build_hist_gene(code)
        if not g:
            print(f"  {code}: 无数据/无涨停记录")
            continue
        (GENES / f"hist_{code}.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {code} 历史涨停基因: 近250日涨停{g['zt_250']}次/500日{g['zt_500']}次 | "
              f"最长连板{g['max_boards_hist']}板(序列{g['board_seq']}) | 晋级{g['promo_rate_hist']}% | "
              f"首板次日高开均{g['first_prem_open_hist']}% | 次日收盘溢价均{g['prem_close_hist']}%")


if __name__ == "__main__":
    main()
