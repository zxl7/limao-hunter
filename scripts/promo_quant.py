#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连板晋级统计与回测 · P1 晋级事件层（2026-09-09 用户立项·设计见 config/design_连板晋级量化分析.md）。

事件定义（口径见设计文档 1.1）：
  相邻两 pool 交易日 (D, D+1)，D 日连板 n 的票 → D+1 连板 n+1 = n进n+1 成功；
  D+1 不在涨停池/断板 = 失败。梯次 stage=1to2/2to3/3to4/4to5+（4进5及以上合并）。
  次日溢价（晋级成功后的收益）= 以晋级日 T(即 D+1) 收盘为基准 → T+1 open/close、T+2 close
  （由新浪不复权日K join·复用 zt_genes 缓存，失败留 None 优雅降级）。

用法:
  python3 scripts/promo_quant.py build --all            # 回填全部相邻 pool 对 → cache/promo/promo_{next}.json
  python3 scripts/promo_quant.py build                  # 仅最新一对（capture step7 每日增量·默认）
  python3 scripts/promo_quant.py enrich --date 20260908 # 补某日事件次日溢价（分批防限流）
  python3 scripts/promo_quant.py enrich --all
  python3 scripts/promo_quant.py stat                   # 梯次×成功率+次日溢价矩阵（全量）
  python3 scripts/promo_quant.py stat --stage 2to3 --board 食品加工 --start 20260820
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / "cache"
PROMO = CACHE / "promo"
sys.path.insert(0, str(HERE))
import zt_genes as zg  # noqa: E402   # 复用 pool 读/日K缓存/涨停判定


# stage 按「前日板数 pb」定梯次：pb=1 的票次日晋级目标=2 → 1进2；pb=4 及以上合并为高标晋级 4to5+（防高标样本过稀）
STAGE_OF = lambda b: ("1to2" if b == 1 else "2to3" if b == 2 else "3to4" if b == 3 else "4to5+")
STAGES = ("1to2", "2to3", "3to4", "4to5+")


# ── build：晋级事件表 ──────────────────────────────────────────────
def build_pair(prev_date: str, next_date: str) -> list[dict]:
    """相邻 pool 日 → 前日涨停票的今日结局事件表（纯 pool·无网络）。"""
    pools = zg._load_pools()
    if prev_date not in pools or next_date not in pools:
        return []
    prev_pool = {x["code"]: x for x in pools[prev_date]}
    next_pool = {x["code"]: x for x in pools[next_date]}
    events = []
    for code, px in prev_pool.items():
        pb = int(px.get("zt_boards") or 0)
        if pb < 1:
            continue
        nx = next_pool.get(code)
        nb = int(nx.get("zt_boards") or 0) if nx else 0
        events.append({
            "date": next_date, "prev_date": prev_date,
            "code": code, "name": px.get("name") or "",
            "prev_boards": pb, "boards": nb,
            "stage": STAGE_OF(pb),
            "ok": nb > pb,                       # 晋级成功=连板数继续 +1
            "sector": px.get("industry") or "",
            "seal_min": (nx or {}).get("seal_min"),        # 晋级日首封(成功时)
            "broken": (nx or {}).get("broken"),            # 晋级日炸板次数
            "seal_fund_yi": (nx or {}).get("seal_fund_yi"),
            "prem_open1": None, "prem_close1": None, "prem_close2": None,  # enrich 填充
            "prem_ok": False,
        })
    return events


def cmd_build(all_days: bool) -> None:
    pools = zg._load_pools()
    dates = sorted(pools)
    if len(dates) < 2:
        print("[promo] 需 ≥2 个 pool 日，先跑 zt_genes fetch")
        return
    pairs = [(a, b) for a, b in zip(dates, dates[1:])]
    if not all_days:
        pairs = pairs[-1:]
    PROMO.mkdir(parents=True, exist_ok=True)
    for a, b in pairs:
        out = PROMO / f"promo_{b}.json"
        if out.exists() and not all_days:
            print(f"[promo] {b} 已存在，跳过（--all 强制回填）")
            continue
        evs = build_pair(a, b)
        out.write_text(json.dumps({"date": b, "prev_date": a, "events": evs,
                                   "ts": datetime.now().isoformat()}, ensure_ascii=False), encoding="utf-8")
        ok = sum(1 for e in evs if e["ok"])
        print(f"[promo] {b}: {len(evs)} 只连板候选 | 晋级成功 {ok} | 写 {out.name}")


# ── enrich：次日溢价（新浪日K·分批防限流）──
def _premium_rows(code: str) -> dict[str, dict]:
    """code 的日K date→row（复用 zt_genes kl_hist 缓存/新浪拉取）。"""
    rows = zg._fetch_sina_all(code)
    if not rows:
        return {}
    return {r["d"]: r for r in rows}


def enrich_file(path: Path, max_age_days: int = 3) -> int:
    d = json.loads(path.read_text(encoding="utf-8"))
    evs = d.get("events") or []
    n_fill = 0
    for e in evs:
        if e.get("prem_ok"):
            continue
        kl = _premium_rows(str(e["code"]))
        if not kl:
            continue
        # 晋级日=T(date)·基准=当日 close → T+1 open/close（date 后第一个交易日）、T+2 close
        dates = sorted(kl)
        try:
            ti = dates.index(e["date"])
        except ValueError:
            continue
        base = float(kl[e["date"]]["close"])
        if base <= 0 or ti + 2 >= len(dates):
            continue
        t1, t2 = dates[ti + 1], dates[ti + 2]
        e["prem_open1"] = round((float(kl[t1]["open"]) / base - 1) * 100, 2)
        e["prem_close1"] = round((float(kl[t1]["close"]) / base - 1) * 100, 2)
        e["prem_close2"] = round((float(kl[t2]["close"]) / base - 1) * 100, 2)
        e["prem_ok"] = True
        n_fill += 1
        time.sleep(0.15)          # 新浪防限流节拍
    d["ts"] = datetime.now().isoformat()
    path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return n_fill


def cmd_enrich(only_date: str | None) -> None:
    files = sorted(PROMO.glob("promo_*.json"))
    if only_date:
        files = [p for p in files if p.stem[6:] == only_date]
    total = 0
    for p in files:
        try:
            total += enrich_file(p)
            print(f"[promo] enrich {p.stem}: +{total} 条溢价（累计）")
        except Exception as e:
            print(f"[promo] enrich {p.stem} ERR: {e}")
    print(f"[promo] 溢价补全完成 {total} 条")


# ── stat：成功率+次日溢价矩阵 ──────────────────────────────────────
def load_events(stage=None, board=None, start=None, end=None) -> list[dict]:
    out = []
    for p in sorted(PROMO.glob("promo_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        dt = d.get("date") or ""
        if start and dt < start:
            continue
        if end and dt > end:
            continue
        for e in d.get("events") or []:
            if stage and e.get("stage") != stage:
                continue
            if board and e.get("sector") != board:
                continue
            out.append(e)
    return out


def _pct(vals: list[float]) -> tuple:
    if not vals:
        return (None, None)
    s = sorted(vals)
    med = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
    q = s[len(s) // 4] if len(s) >= 4 else None
    return (round(sum(s) / len(s), 2), round(med, 2))


def cmd_stat(stage: str | None, board: str | None, start: str | None, end: str | None) -> None:
    evs = load_events(stage, board, start, end)
    if not evs:
        print("[promo] 无匹配事件（先 build --all / enrich）")
        return
    rows = []
    for st in (stage or STAGES):
        sub = [e for e in evs if e["stage"] == st]
        if not sub:
            continue
        n = len(sub)
        ok = sum(1 for e in sub if e["ok"])
        ok_r = round(ok / n * 100, 1)
        # 成功组次日溢价（有 prem 的样本）
        po = [e["prem_open1"] for e in sub if e.get("prem_ok") and e["ok"] and e["prem_open1"] is not None]
        pc = [e["prem_close1"] for e in sub if e.get("prem_ok") and e["ok"] and e["prem_close1"] is not None]
        # 失败代价（断板票次日 open 跳水 = 打板者亏损代理）
        fl = [e["prem_open1"] for e in sub if e.get("prem_ok") and not e["ok"] and e["prem_open1"] is not None]
        ao, mo = _pct(po)
        ac, mc = _pct(pc)
        af, mf = _pct(fl)
        rows.append({
            "stage": st, "n": n, "ok": ok, "fail": n - ok, "succ_rate": ok_r,
            "n_prem": len(po),
            "prem_open_avg": ao, "prem_open_med": mo,
            "prem_close_avg": ac, "prem_close_med": mc,
            "fail_open_avg": af, "fail_open_med": mf,
        })
    f = {"filter": {"stage": stage, "board": board, "start": start, "end": end},
         "date_range": f"{start or '最早'}~{end or '最新'}",
         "rows": rows, "ts": datetime.now().isoformat()}
    (PROMO / "promo_summary.json").write_text(json.dumps(f, ensure_ascii=False, indent=1), encoding="utf-8")
    hdr = (f"[promo] 连板晋级矩阵（样本日期: 最早~最新）\n"
           f"{'梯次':<6}{'样本':>5}{'成功':>5}{'失败':>5}{'成功率':>8}{'含溢价':>7}"
           f"{'成功组次日开均':>12}{'收盘均':>9}{'失败组次日开均':>12}")
    print(hdr)
    for r in rows:
        def f2(x, suf="%"):
            return f"{x:>11}" if x is None else f"{x:>10}{suf}"
        print(f"{r['stage']:<7}{r['n']:>5}{r['ok']:>5}{r['fail']:>5}{r['succ_rate']:>7}%"
              f"{r['n_prem']:>7}{f2(r['prem_open_avg'])}{f2(r['prem_close_avg'])}{f2(r['fail_open_avg'])}")
    print(f"[promo] summary 已写 cache/promo/promo_summary.json")


# ── P2 回测：板块/区间/梯次横向对比报告 ──────────────────────────
def _agg(events: list[dict]) -> dict:
    """事件集聚合行（纯函数·成功率/成功组溢价均·失败组开盘均）。"""
    n = len(events)
    if not n:
        return None
    ok = sum(1 for e in events if e["ok"])
    def _avg(key, ok_only=True):
        vals = [e[key] for e in events
                if e.get("prem_ok") and (not ok_only or e["ok"]) and e.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None
    fl = [e["prem_open1"] for e in events if e.get("prem_ok") and not e["ok"] and e.get("prem_open1") is not None]
    return {"n": n, "ok": ok, "fail": n - ok,
            "succ_rate": round(ok / n * 100, 1),
            "prem_open1": _avg("prem_open1"), "prem_close1": _avg("prem_close1"),
            "prem_close2": _avg("prem_close2"),
            "fail_open1": round(sum(fl) / len(fl), 2) if fl else None}


def backtest(stage=None, boards=None, group="board", start=None, end=None,
             min_n: int = 3) -> list[dict]:
    """回测聚合。boards=None 全市场；group=board|stage|day；min_n=分组最小样本(防小样本噪声)。"""
    evs = load_events(stage=stage, start=start, end=end)
    if boards:
        bs = set(boards)
        evs = [e for e in evs if (e.get("sector") or "") in bs]
    if group == "board":
        keys = sorted({(e.get("sector") or "未知") for e in evs})
        rows = [{"key": k, **_agg([e for e in evs if (e.get("sector") or "未知") == k])} for k in keys]
    elif group == "stage":
        keys = STAGES
        rows = [{"key": k, **_agg([e for e in evs if e["stage"] == k])} for k in keys if _agg([e for e in evs if e["stage"] == k])]
    else:  # day 时间序列
        keys = sorted({e["date"] for e in evs})
        rows = [{"key": k, **_agg([e for e in evs if e["date"] == k])} for k in keys]
    rows = [r for r in rows if r and r["n"] >= min_n]
    rows.sort(key=lambda r: -((r.get("succ_rate") or 0)))  # 成功率降序（day 不排）
    if group == "day":
        rows = sorted(rows, key=lambda r: r["key"])
    return rows


def _fmt_md(rows, title, f=None) -> str:
    f = f or {}
    lines = [f"# 连板晋级回测 · {title}", ""]
    lines.append(f"- 过滤: stage={f.get('stage') or '全部'} | board={','.join(f.get('boards') or []) or '全市场'} "
                 f"| 区间 {f.get('start') or '最早'}~{f.get('end') or '最新'}")
    lines.append("")
    lines.append("| 分组 | 样本 | 成功 | 失败 | 成功率 | 成功次日开均 | 收盘均 | 次日2均 | 失败组开盘均 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        def g(x):
            return "-" if x is None else f"{x}"
        lines.append(f"| {r['key']} | {r['n']} | {r['ok']} | {r['fail']} | {g(r['succ_rate'])}% "
                     f"| {g(r['prem_open1'])}% | {g(r['prem_close1'])}% | {g(r['prem_close2'])}% | {g(r['fail_open1'])}% |")
    lines.append("")
    lines.append("> 口径：成功率=晋级成功/候选；溢价以晋级日收盘为基准；失败组开盘=断板票次日开盘均（打板亏损代理）。冷启动样本(8/20起)偏小，趋势参考为主。")
    return "\n".join(lines)


def cmd_backtest(stage, boards_csv, group, start, end, tag=None, min_n=3) -> None:
    boards = [b.strip() for b in (boards_csv or "").split(",") if b.strip()] or None
    rows = backtest(stage=stage, boards=boards, group=group, start=start, end=end, min_n=min_n)
    if not rows:
        print("[promo] backtest 无匹配（先 build/enrich）")
        return
    title = f"{stage or '全梯次'}×{(','.join(boards) if boards else '全市场')} by {group}"
    print(f"[promo] 连板晋级回测 · {title}")
    print(f"{'分组':<10}{'样本':>6}{'成功':>5}{'失败':>5}{'成功率':>8}{'次日开均':>9}{'收盘均':>8}{'失败开均':>10}")
    for r in rows[:25]:
        def g(x, w=8, s="%"):
            return "-" if x is None else f"{x:>{w}}{s}"
        print(f"{str(r['key']):<11}{r['n']:>6}{r['ok']:>5}{r['fail']:>5}"
              f"{g(r['succ_rate'], 7)}{g(r['prem_open1'])}{g(r['prem_close1'])}{g(r['fail_open1'], 9)}")
    if len(rows) > 25:
        print(f"  …另有 {len(rows) - 25} 组未显示（看报告）")
    PROMO.mkdir(parents=True, exist_ok=True)
    tag = tag or f"{group or 'all'}_{stage or 'all'}_{datetime.now().strftime('%Y%m%d%H%M')}"
    md = _fmt_md(rows, title, {"stage": stage, "boards": boards, "start": start, "end": end})
    (PROMO / f"backtest_{tag}.md").write_text(md.replace("\\n", "\n"), encoding="utf-8")
    (PROMO / f"backtest_{tag}.json").write_text(
        json.dumps({"title": title, "rows": rows, "ts": datetime.now().isoformat()},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[promo] 报告已写 cache/promo/backtest_{tag}.md / .json")


def main() -> None:
    ap = argparse.ArgumentParser(description="连板晋级统计·P1 事件层")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--all", action="store_true")
    e = sub.add_parser("enrich"); e.add_argument("--date", default=None); e.add_argument("--all", action="store_true")
    bt = sub.add_parser("backtest")
    bt.add_argument("--stage", choices=list(STAGES), default=None)
    bt.add_argument("--board", default=None, help="逗号分隔行业（并集过滤）")
    bt.add_argument("--group", choices=["board", "stage", "day"], default="board")
    bt.add_argument("--start", default=None); bt.add_argument("--end", default=None)
    bt.add_argument("--tag", default=None)
    bt.add_argument("--min-n", type=int, default=3)
    s = sub.add_parser("stat")
    s.add_argument("--stage", choices=list(STAGES), default=None)
    s.add_argument("--board", default=None)
    s.add_argument("--start", default=None); s.add_argument("--end", default=None)
    a = ap.parse_args()
    if a.cmd == "build":
        cmd_build(a.all)
    elif a.cmd == "enrich":
        cmd_enrich(None if a.all else a.date)
    elif a.cmd == "stat":
        cmd_stat(a.stage, a.board, a.start, a.end)
    elif a.cmd == "backtest":
        cmd_backtest(a.stage, a.board, a.group, a.start, a.end, a.tag, a.min_n)


if __name__ == "__main__":
    main()
