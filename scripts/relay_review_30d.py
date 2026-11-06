#!/usr/bin/env python3
"""连板生命周期回溯（默认最近 32 个归档交易日）—— 龙头战法交易员 复盘口径。

只读分析脚本，不写生产数据（除 cache/relay_review/latest.json）。
数据源：
  cache/zt_history/zt_<date>.json   每日收盘涨停池（含 fbt/zbc/hs/seal/lt_yi）
  cache/kline_qfq/<code>.json       前复权日线（收益口径）
  wealth_code/cache/ai_nav_history.jsonl   AI 真实结算（推荐逻辑复盘）
  wealth_code/cache/pool_call-<date>.json  09:25 池内裁决

用法：
  python3 scripts/relay_review_30d.py            # 全量报告
  python3 scripts/relay_review_30d.py --json     # 只打 JSON
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZT_DIR = ROOT / "cache/zt_history"
KLINE_DIR = ROOT / "cache/kline_qfq"
AI_NAV = ROOT / "wealth_code/cache/ai_nav_history.jsonl"
POOL_CALL_DIR = ROOT / "wealth_code/cache"
OUT_DIR = ROOT / "cache/relay_review"

# 晋级因子阈值（与 board_track.py 同口径）
EARLY_SEAL_MIN = 570      # 09:30 前封板
THICK_SEAL_RATIO = 3.0    # 封单/流通 ≥3%
LOW_TURNOVER = 10.0       # 换手 <10%


# ── 数据加载 ────────────────────────────────────────────────

def load_days() -> list[tuple[str, dict[str, dict]]]:
    days = []
    for f in sorted(ZT_DIR.glob("zt_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        date = str(d.get("date") or "")[:10]
        if not date:
            continue
        pool = {}
        for r in d.get("zt_list") or []:
            code = str(r.get("code") or "").zfill(6)
            if not code:
                continue
            pool[code] = r
        days.append((date, pool))
    days.sort()
    return days


_KLINE: dict[str, dict[str, dict]] = {}


def kline(code: str) -> dict[str, dict]:
    """code → {date: bar}，缺失返回空。"""
    if code in _KLINE:
        return _KLINE[code]
    p = KLINE_DIR / f"{code}.json"
    out: dict[str, dict] = {}
    if p.exists():
        try:
            for b in json.loads(p.read_text(encoding="utf-8")).get("bars") or []:
                out[str(b["date"])[:10]] = b
        except Exception:
            out = {}
    _KLINE[code] = out
    return out


# ── 指标 ──────────────────────────────────────────────────

def seal_ratio(row: dict) -> float | None:
    """封单/流通 %。zt_history 的 seal 为封单亿元，lt_yi 为流通市值亿元。"""
    seal = row.get("seal")
    lt = row.get("lt_yi")
    try:
        if seal is None or lt is None or float(lt) <= 0:
            return None
        return float(seal) / float(lt) * 100.0
    except (TypeError, ValueError):
        return None


def fbt_min(row: dict) -> int | None:
    """首封时刻（09:30 = 570 分）。"""
    s = str(row.get("fbt") or "").strip()
    if len(s) < 5 or not s.isdigit():
        return None
    return int(s[:2]) * 60 + int(s[2:4])


def factors(row: dict) -> dict:
    fm = fbt_min(row)
    sr = seal_ratio(row)
    f = {
        "early_seal": fm is not None and fm <= EARLY_SEAL_MIN,
        "thick_seal": sr is not None and sr >= THICK_SEAL_RATIO,
        "low_turnover": (row.get("hs") or 999) < LOW_TURNOVER,
        "clean_seal": int(row.get("zbc") or 0) == 0,
    }
    f["count"] = sum(bool(v) for k, v in f.items())
    return f


def build_lifecycles(days: list[tuple[str, dict[str, dict]]]) -> dict[str, list[dict]]:
    """code → 涨停段列表，每段为该票连续涨停日中 boards≥1 的记录（按日期升序）。"""
    dates = [d for d, _ in days]
    idx = {d: i for i, d in enumerate(dates)}
    seqs: dict[str, list[dict]] = defaultdict(list)
    for date, pool in days:
        for code, row in pool.items():
            seqs[code].append({"date": date, "i": idx[date], "row": row})

    out: dict[str, list[dict]] = {}
    for code, rows in seqs.items():
        rows.sort(key=lambda x: x["i"])
        segs: list[list[dict]] = []
        cur: list[dict] = []
        for r in rows:
            if cur and r["i"] != cur[-1]["i"] + 1:
                segs.append(cur)
                cur = []
            cur.append(r)
        if cur:
            segs.append(cur)
        out[code] = segs
    return out


def next_date(dates: list[str], d: str) -> str | None:
    try:
        i = dates.index(d)
    except ValueError:
        return None
    return dates[i + 1] if i + 1 < len(dates) else None


def relay_return(code: str, trade_date: str, prev_close: float | None = None
                 ) -> tuple[float | None, float | None, str]:
    """T+1 一日游：开盘买 → 收盘卖。返回 (收益%, 开盘gap%, 说明)。"""
    bars = kline(code)
    b = bars.get(trade_date)
    if not b:
        return None, None, "no_kline"
    o, c = float(b["open"]), float(b["close"])
    if o <= 0:
        return None, None, "bad_price"
    gap = None
    if prev_close and prev_close > 0:
        gap = round((o - prev_close) / prev_close * 100.0, 2)
    if float(b["high"]) == float(b["low"]):
        return None, gap, "one_word"
    return round((c - o) / o * 100.0, 2), gap, "ok"


_GAP_BUCKETS = [(None, -3.0, "低开>3%"), (-3.0, 0.0, "低开0~3%"), (0.0, 3.0, "高开0~3%"),
                (3.0, 5.0, "高开3~5%"), (5.0, None, "高开>5%")]


def _gap_label(g: float) -> str:
    for lo, hi, label in _GAP_BUCKETS:
        if (lo is None or g >= lo) and (hi is None or g < hi):
            return label
    return "未知"


# ── 主分析 ─────────────────────────────────────────────────

def analyze() -> dict:
    days = load_days()
    dates = [d for d, _ in days]
    pools = dict(days)
    lifecycles = build_lifecycles(days)
    max_boards_by_date = {d: max((int(r.get("boards") or 0) for r in p.values()), default=0)
                          for d, p in days}

    # ── 在册样本：所有 boards≥2 的「票·日」
    samples = []
    for code, segs in lifecycles.items():
        for si, seg in enumerate(segs):
            for k, item in enumerate(seg):
                row = item["row"]
                b = int(row.get("boards") or 0)
                if b < 2:
                    continue
                d = item["date"]
                nd = next_date(dates, d)
                nxt = pools.get(nd, {}).get(code) if nd else None
                nb = int(nxt.get("boards") or 0) if nxt else 0
                f = factors(row)
                samples.append({
                    "date": d, "code": code, "name": row.get("name"),
                    "boards": b,
                    "promoted": bool(nxt and nb > b),
                    "next_date": nd,
                    "next_boards": nb,
                    "seg_first_date": seg[0]["date"],
                    "seg_first_boards": int(seg[0]["row"].get("boards") or 0),
                    "seg_len": len(seg),
                    "days_in_track": k + 1,
                    "gap_to_top": max_boards_by_date.get(d, b) - b,
                    "fbt_min": fbt_min(row),
                    "seal_ratio": (round(seal_ratio(row), 2) if seal_ratio(row) is not None else None),
                    "hs": row.get("hs"),
                    "zbc": int(row.get("zbc") or 0),
                    "cje_yi": row.get("cje_yi"),
                    "seal_yi": row.get("seal"),
                    "sector": row.get("sector"),
                    "is_20cm": bool(row.get("is_20cm")),
                    "factors": f,
                    "zt_count": len(pools.get(d, {})),
                    "max_boards_day": max_boards_by_date.get(d, b),
                })

    def rate(sel) -> dict:
        g = [s for s in samples if sel(s)]
        ok = sum(1 for s in g if s["promoted"])
        return {"n": len(g), "ok": ok, "rate": round(ok / len(g) * 100, 1) if g else None}

    by_stage = {}
    for stage in sorted({s["boards"] for s in samples}):
        by_stage[f"{stage}to{stage+1}"] = rate(lambda s, b=stage: s["boards"] == b)

    by_factor_count = {k: rate(lambda s, k=k: s["factors"]["count"] == k) for k in range(5)}
    by_single = {
        "早封≤09:30": rate(lambda s: s["factors"]["early_seal"]),
        "非早封": rate(lambda s: not s["factors"]["early_seal"]),
        "封单/流通≥3%": rate(lambda s: s["factors"]["thick_seal"]),
        "封单/流通<1%": rate(lambda s: s["seal_ratio"] is not None and s["seal_ratio"] < 1.0),
        "换手<10%": rate(lambda s: s["factors"]["low_turnover"]),
        "换手≥20%": rate(lambda s: (s["hs"] or 0) >= 20),
        "零炸板": rate(lambda s: s["factors"]["clean_seal"]),
        "炸板≥2次": rate(lambda s: s["zbc"] >= 2),
    }
    by_zone = {
        "身位=最高板": rate(lambda s: s["gap_to_top"] == 0),
        "身位差1板": rate(lambda s: s["gap_to_top"] == 1),
        "身位差≥2板": rate(lambda s: s["gap_to_top"] >= 2),
        "20cm": rate(lambda s: s["is_20cm"]),
        "主板10cm": rate(lambda s: not s["is_20cm"]),
    }
    by_market = {}
    for lo, hi, label in [(0, 60, "涨停<60家(冷)"), (60, 100, "涨停60~99家(平)"), (100, 999, "涨停≥100家(热)")]:
        by_market[label] = rate(lambda s, lo=lo, hi=hi: lo <= s["zt_count"] < hi)
    by_track_day = {}
    for lo, hi in [(1, 2), (2, 4), (4, 99)]:
        by_track_day[f"在册第{lo}~{hi-1}天" if hi < 99 else f"在册≥{lo}天"] = \
            rate(lambda s, lo=lo, hi=hi: lo <= s["days_in_track"] < hi)

    # 组合：4 条齐 / 0-3 条
    combo = {
        "四条齐(早封+厚封+低换手+零炸)": rate(lambda s: s["factors"]["count"] == 4),
        "早封+封厚≥3%": rate(lambda s: s["factors"]["early_seal"] and s["factors"]["thick_seal"]),
        "早封+封厚≥3%+零炸": rate(lambda s: s["factors"]["early_seal"] and s["factors"]["thick_seal"]
                              and s["factors"]["clean_seal"]),
        "最高板+早封": rate(lambda s: s["gap_to_top"] == 0 and s["factors"]["early_seal"]),
        "最高板+早封+封厚": rate(lambda s: s["gap_to_top"] == 0 and s["factors"]["early_seal"]
                            and s["factors"]["thick_seal"]),
    }

    # ── 跟踪收益：次日一日游
    ret_all, ret_ok = [], []
    for s in samples:
        if not s["next_date"]:
            continue
        prev_close = None
        pb = kline(s["code"]).get(s["date"])
        if pb:
            prev_close = float(pb["close"])
        else:
            try:
                prev_close = float(pools[s["date"]][s["code"]].get("price") or 0) or None
            except (KeyError, TypeError, ValueError):
                prev_close = None
        r, gap, why = relay_return(s["code"], s["next_date"], prev_close)
        s["relay_return"] = r
        s["open_gap"] = gap
        s["gap_band"] = _gap_label(gap) if gap is not None else "未知"
        s["relay_note"] = why
        if r is not None:
            ret_all.append(r)
            if s["promoted"]:
                ret_ok.append(r)

    def avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    # 收益分组
    def ret_stat(sel) -> dict:
        g = [s for s in samples if sel(s) and s.get("relay_return") is not None]
        xs = [s["relay_return"] for s in g]
        if not xs:
            return {"n": 0}
        win = sum(1 for x in xs if x > 0)
        return {"n": len(xs), "avg": avg(xs), "win_rate": round(win / len(xs) * 100, 1),
                "median": round(sorted(xs)[len(xs) // 2], 2)}

    ret_groups = {
        "全部在册票": ret_stat(lambda s: True),
        "次日晋级(=连板)": ret_stat(lambda s: s["promoted"]),
        "次日未晋级": ret_stat(lambda s: not s["promoted"]),
        "四条齐": ret_stat(lambda s: s["factors"]["count"] == 4),
        "最高板": ret_stat(lambda s: s["gap_to_top"] == 0),
        "身位差≥2板": ret_stat(lambda s: s["gap_to_top"] >= 2),
        "身位差≥2板且四条齐": ret_stat(lambda s: s["gap_to_top"] >= 2 and s["factors"]["count"] == 4),
    }

    # ── 按次日开盘幅度分组：唯一「09:25 可观测」的决策变量
    by_gap = {}
    for lo, hi, label in _GAP_BUCKETS:
        def sel(s, lo=lo, hi=hi):
            g = s.get("open_gap")
            if g is None:
                return False
            return (lo is None or g >= lo) and (hi is None or g < hi)
        n_gap = sum(1 for s in samples if sel(s))
        by_gap[label] = {
            "n": n_gap,
            "promoted": rate(sel),
            "ret": ret_stat(sel),
        }
    # 两条最常见的组合：四条齐 / 最高板，在不同 gap 下的收益
    cross = {}
    for name, cond in [("四条齐", lambda s: s["factors"]["count"] == 4),
                       ("最高板", lambda s: s["gap_to_top"] == 0)]:
        for lo, hi, label in _GAP_BUCKETS:
            def sel(s, lo=lo, hi=hi, cond=cond):
                g = s.get("open_gap")
                if g is None or not cond(s):
                    return False
                return (lo is None or g >= lo) and (hi is None or g < hi)
            v = ret_stat(sel)
            if v.get("n"):
                cross[f"{name}·{label}"] = v

    # ── AI 真实结算
    ai_rows = []
    if AI_NAV.exists():
        for line in AI_NAV.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    ai_rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    ai_pos = []
    ai_skipped = Counter()
    ai_skip_rows = []
    for r in ai_rows:
        for s in r.get("stocks") or []:
            ai_pos.append({
                "trade_date": r.get("trade_date"),
                "reco_date": r.get("recommendation_date"),
                "code": s.get("code"), "name": s.get("name"),
                "return_pct": s.get("return_pct"),
                "auction_price": s.get("auction_price"),
                "close": s.get("close"),
                "reason": (s.get("reason") or "").replace("\n", " "),
            })
        for s in r.get("skipped") or []:
            note = str(s.get("note") or "")
            label = "其它"
            for key, lab in [
                ("高开超过 5%", "高开超5%(旧硬线已废)"),
                ("低于昨天的预期", "低开/不及预期放弃"),
                ("量不够", "竞价量不够像诱多"),
                ("只买前", "名额已满排后面"),
                ("已经断板", "跟风小弟(核心已断)"),
                ("没红开", "没红开"),
                ("没拿到", "报价缺失"),
                ("封得最薄", "最高板+极薄封单"),
                ("没有同方向", "同方向无支持"),
                ("不是新方向", "老票重新拉板"),
            ]:
                if key in note:
                    label = lab
                    break
            ai_skipped[label] += 1
            ai_skip_rows.append({"trade_date": r.get("trade_date"), "code": s.get("code"),
                                 "reason": label, "note": note})

    # 被跳过票的机会成本：若当日开盘买入、收盘卖出会怎样
    for s in ai_skip_rows:
        bars = kline(str(s["code"]).zfill(6))
        b = bars.get(str(s["trade_date"])[:10])
        if not b:
            s["would_return"] = None
            continue
        o, c = float(b["open"]), float(b["close"])
        s["would_return"] = round((c - o) / o * 100.0, 2) if o > 0 else None

    def skip_stat(label: str | None) -> dict:
        g = [x for x in ai_skip_rows if x.get("would_return") is not None
             and (label is None or x["reason"] == label)]
        xs = [x["would_return"] for x in g]
        if not xs:
            return {"n": 0}
        win = sum(1 for x in xs if x > 0)
        return {"n": len(xs), "avg": round(sum(xs) / len(xs), 2),
                "win_rate": round(win / len(xs) * 100, 1),
                "median": round(sorted(xs)[len(xs) // 2], 2)}

    ai_skip_cost = {lab: skip_stat(lab) for lab in ai_skipped}
    ai_skip_cost["全部跳过"] = skip_stat(None)

    ai_summary = {
        "settled_days": len(ai_rows),
        "trades": len(ai_pos),
        "win": sum(1 for p in ai_pos if (p["return_pct"] or 0) > 0),
        "avg": avg([p["return_pct"] for p in ai_pos if p["return_pct"] is not None]),
        "nav": ai_rows[-1].get("nav") if ai_rows else None,
        "skipped_total": sum(ai_skipped.values()),
        "skipped_reasons": dict(ai_skipped.most_common()),
        "skipped_cost": ai_skip_cost,
    }

    # ── 池内裁决行为
    pc_rows = []
    zone_sum: Counter = Counter()
    for f in sorted(glob.glob(str(POOL_CALL_DIR / "pool_call-*.json"))):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        views = d.get("views") or {}
        zc = Counter(str(v.get("zone") or "unknown") for v in views.values())
        zone_sum.update(zc)
        pc_rows.append({
            "date": d.get("trade_date"),
            "stance": d.get("stance"),
            "quota": d.get("quota"),
            "n_views": len(views),
            "bought": [v.get("name") for v in views.values() if v.get("bias") == "focus"],
            "zone": dict(zc),
        })
    pc_summary = {
        "days": len(pc_rows),
        "stance": dict(Counter(r["stance"] for r in pc_rows)),
        "zero_quota_days": sum(1 for r in pc_rows if not r["quota"]),
        "bought_days": sum(1 for r in pc_rows if r["bought"]),
        "avg_views": round(sum(r["n_views"] for r in pc_rows) / len(pc_rows), 1) if pc_rows else None,
        "zone": dict(zone_sum.most_common()),
    }

    # ── 生命周期链（最长/最典型的几条）
    chains = []
    for code, segs in lifecycles.items():
        for seg in segs:
            boards = [int(x["row"].get("boards") or 0) for x in seg]
            if max(boards) < 2:
                continue
            chains.append({
                "code": code, "name": seg[0]["row"].get("name"),
                "start": seg[0]["date"], "end": seg[-1]["date"],
                "len": len(seg), "max_boards": max(boards), "boards": boards,
                "sector": seg[0]["row"].get("sector"),
            })
    chains.sort(key=lambda c: (-c["max_boards"], -c["len"]))

    return {
        "window": {"days": len(days), "start": dates[0] if dates else None,
                   "end": dates[-1] if dates else None},
        "universe": {
            "samples_ge2": len(samples),
            "stocks_ge2": len({s["code"] for s in samples}),
            "chains_ge2": len(chains),
            "max_boards": max((c["max_boards"] for c in chains), default=0),
        },
        "by_stage": by_stage,
        "by_factor_count": by_factor_count,
        "by_single_factor": by_single,
        "by_zone": by_zone,
        "by_market": by_market,
        "by_track_day": by_track_day,
        "combo": combo,
        "by_gap": by_gap,
        "gap_cross": cross,
        "returns": {"groups": ret_groups,
                    "coverage": {"with_kline": len(ret_all), "of": len(samples)}},
        "ai": {"summary": ai_summary, "positions": ai_pos},
        "pool_call": {"summary": pc_summary, "days": pc_rows},
        "chains": chains[:30],
        "samples": [{k: v for k, v in s.items() if k != "factors"} for s in samples],
    }


# ── 输出 ───────────────────────────────────────────────────

def fmt(r: dict) -> str:
    if not r.get("n"):
        return "无样本"
    s = f"{r['rate']}% ({r['ok']}/{r['n']})"
    if r.get("avg") is not None:
        s += f" 收益{s['avg'] if False else r['avg']}%"
    return s


def print_report(a: dict) -> None:
    w = a["window"]
    u = a["universe"]
    print("=" * 78)
    print(f"连板生命周期回溯  {w['start']} ~ {w['end']}  ({w['days']} 个交易日)")
    print(f"≥2板样本 {u['samples_ge2']} 个「票·日」 / {u['stocks_ge2']} 只票 / {u['chains_ge2']} 段连板链"
          f" · 期间最高 {u['max_boards']} 板")
    print("=" * 78)

    print("\n【一】分档晋级率（今天在册 → 明天还能涨停）")
    for k, v in a["by_stage"].items():
        print(f"  {k:<8} {fmt(v)}")

    print("\n【二】单因子")
    for k, v in a["by_single_factor"].items():
        print(f"  {k:<16} {fmt(v)}")

    print("\n【三】因子命中条数（阈值质变检验）")
    for k, v in a["by_factor_count"].items():
        print(f"  命中 {k} 条  {fmt(v)}")

    print("\n【四】组合条件")
    for k, v in a["combo"].items():
        print(f"  {k:<28} {fmt(v)}")

    print("\n【五】身位 / 板块属性")
    for k, v in a["by_zone"].items():
        print(f"  {k:<14} {fmt(v)}")

    print("\n【六】市场环境")
    for k, v in a["by_market"].items():
        print(f"  {k:<18} {fmt(v)}")

    print("\n【七】在册天数")
    for k, v in a["by_track_day"].items():
        print(f"  {k:<14} {fmt(v)}")

    print("\n【八】跟踪收益（次日开盘买→次日收盘卖，一日游口径）")
    c = a["returns"]["coverage"]
    print(f"  日线覆盖 {c['with_kline']}/{c['of']}")
    for k, v in a["returns"]["groups"].items():
        if not v.get("n"):
            print(f"  {k:<22} 无样本")
            continue
        print(f"  {k:<22} n={v['n']:<4} 均{v['avg']:>7}%  中位{v['median']:>7}%  胜率{v['win_rate']}%")

    print("\n【八·B】按次日开盘幅度（09:25 唯一可观测的决策变量）")
    for k, v in a["by_gap"].items():
        p, r = v["promoted"], v["ret"]
        pr = f"{p['rate']}%({p['ok']}/{p['n']})" if p.get("n") else "—"
        rr = (f"n={r['n']:<3} 均{r['avg']:>7}%  胜率{r['win_rate']}%" if r.get("n") else "无成交样本")
        print(f"  {k:<16} 次日晋级 {pr:<16} 当日收益 {rr}")
    print("  — 交叉（条件票在不同开盘幅度下）：")
    for k, v in a["gap_cross"].items():
        print(f"    {k:<26} n={v['n']:<3} 均{v['avg']:>7}%  胜率{v['win_rate']}%")

    ai = a["ai"]["summary"]
    print("\n【九】AI 真实成交（独立轨）")
    print(f"  结算 {ai['settled_days']} 日 / {ai['trades']} 笔 · 胜 {ai['win']} 笔 · 均 {ai['avg']}% · 净值 {ai['nav']}")
    print(f"  被跳过 {ai['skipped_total']} 次，原因分布（附「若当日开盘买」的机会成本）：")
    for k, v in ai["skipped_reasons"].items():
        cost = ai["skipped_cost"].get(k) or {}
        cs = (f"若买则均{cost['avg']:>6}% 胜率{cost['win_rate']}% (n={cost['n']})"
              if cost.get("n") else "无日线")
        print(f"    {k:<22}{v:>3} 次  {cs}")
    tot = ai["skipped_cost"].get("全部跳过") or {}
    if tot.get("n"):
        print(f"    {'合计':<22}{tot['n']:>3} 次  若全买均{tot['avg']}% 胜率{tot['win_rate']}%")

    pc = a["pool_call"]["summary"]
    print("\n【十】09:25 池内裁决")
    print(f"  覆盖 {pc['days']} 日 · 姿态 {pc['stance']} · 零名额 {pc['zero_quota_days']} 日"
          f" · 有买入 {pc['bought_days']} 日 · 平均候选 {pc['avg_views']} 只")
    print(f"  竞价区间分布：{pc['zone']}")

    print("\n【十一】最长连板链 TOP10")
    for c2 in a["chains"][:10]:
        print(f"  {c2['name']:<8}{c2['code']}  {c2['start']}→{c2['end']}  "
              f"{'→'.join(str(b) for b in c2['boards'])}板  {c2['sector']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--save", action="store_true", help="落盘 cache/relay_review/latest.json")
    args = ap.parse_args()

    a = analyze()
    if args.save or not args.json:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "latest.json").write_text(
            json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=1))
    else:
        print_report(a)
        print(f"\n已落盘 {OUT_DIR / 'latest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
