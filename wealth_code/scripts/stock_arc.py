#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股生涯 CLI（stock_arc）。

```bash
python3 wealth_code/scripts/stock_arc.py rebuild            # 全量重算（首次会拉日线，之后走缓存）
python3 wealth_code/scripts/stock_arc.py show 603278        # 单票生涯 + 每一波每日明细
python3 wealth_code/scripts/stock_arc.py patrol 2026-10-23  # 今日继续跟踪清单（只观察）
python3 wealth_code/scripts/stock_arc.py stats              # 真实收益分组
```
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "wealth_code")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import stock_arc  # noqa: E402


def _fmt(v, unit: str = "", plus: bool = False) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:+.2f}{unit}" if plus else f"{v:.2f}{unit}"
    return f"{v}{unit}"


def cmd_rebuild(args: argparse.Namespace) -> int:
    idx = stock_arc.rebuild(today=args.today, fetch_kline=not args.no_fetch)
    if idx.get("error"):
        print("✗", idx["error"])
        return 1
    print(f"✓ 生涯库重建：{idx['codes']} 只 / {idx['episodes']} 波 / {idx['appearances']} 条涨停日")
    print(f"  日线覆盖 {idx['codes_with_kline']} 只（缺 {idx['codes_without_kline']} 只）"
          f"  反复出现(≥3天) {idx['repeat_codes']} 只  待结算腿 {idx['pending_legs']}")
    print(f"  分级：{idx['levels']}")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    from datetime import date as _date

    asof = args.today or _date.today().isoformat()
    res = stock_arc.update_daily(asof, closed_today=args.closed)
    if res.get("error"):
        print("✗", res["error"])
        return 1
    idx = res["index"]
    print(f"✓ {asof} 生涯库：{idx['codes']} 只 / {idx['episodes']} 波 | "
          f"跟踪清单 {res['patrol_count']} 只 → {res['patrol_path']}")
    if idx.get("phantom_dates"):
        print(f"  ⚠️ 轨迹含非交易日归档：{idx['phantom_dates']}（已剔除，不参与计算）")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    res = stock_arc.query(args.code)
    p = res.get("profile")
    if not p:
        print(f"✗ {args.code} 不在生涯库里（先 rebuild，或这只票从未进过涨停池）")
        return 1
    print(f"══ {p['name']} {p['code']} {p['sector']} ══")
    print(f"涨停 {p['appearances']} 天 / {p['episodes']} 波 | 最高 {p['max_boards']} 板 | "
          f"高度 {p['heights']} | 走势{p['evolve']}")
    first_boards = p.get("first_boards")
    print(f"首次出现 {p['first_date']}({_fmt(first_boards)}板) "
          f"→ 首次进池 {p['first_pool_date']}({_fmt(p['first_pool_boards'])}板) "
          f"→ 首次可进场 {p['first_entry_date']}({_fmt(p['first_entry_boards'])}板)")
    print(f"可变现：可进场 {p['entry_days']} 次 / 命中 {_fmt(p['hit_rate'],'%')} / "
          f"平均 {_fmt(p['avg_ret'],'%',True)} / 累计 {_fmt(p['sum_ret'],'%',True)} "
          f"| 等级 {p['level']}")
    for i, ep in enumerate(res["episodes"], 1):
        print(f"\n── 第{i}波 {ep['start_date']}~{ep['end_date']} "
              f"{ep['days']}天 高度{ep['heights']} | {ep['verdict']}"
              f"（可进场{ep['entry_days']}次 均{_fmt(ep['avg_ret'],'%',True)}）")
        for lg in ep["legs"]:
            print(f"   {lg['date']} {lg['boards']}板 {''.join(lg['mech'])[:14]:<14}"
                  f" 额{_fmt(lg['cje_yi'])}亿 门槛{lg['gate']:<11} 池{_fmt(lg['pool'])}"
                  f" | 次日 {_fmt(lg['next_date'])} 开{_fmt(lg['entry'])} "
                  f"{_fmt(lg['gap_pct'],'%',True)} {lg['zone']:<4}"
                  f" 收{_fmt(lg['ret_t1'],'%',True)} T+2 {_fmt(lg['ret_t2'],'%',True)}"
                  f" {lg['note']}")
    return 0


def cmd_patrol(args: argparse.Namespace) -> int:
    from datetime import date as _date

    asof = args.date or _date.today().isoformat()
    rows = stock_arc.patrol(asof)
    if not rows:
        print(f"{asof} 无反复出现且仍活着的票")
        return 0
    print(f"══ {asof} 继续跟踪清单（{len(rows)} 只·只观察不构成买入）══")
    print(f"{'代码':<7}{'名称':<9}{'窗内':<5}{'波高':<14}{'末次':<12}{'末板':<5}"
          f"{'进场次数':<9}{'命中':<7}{'均收益':<8}{'等级':<8}{'走势':<5}")
    for r in rows:
        print(f"{r['code']:<7}{str(r['name'])[:8]:<9}{r['in_window']:<5}"
              f"{str(r['heights'])[:13]:<14}{r['last_date']:<12}{r['last_boards']:<5}"
              f"{_fmt(r['entry_days']):<9}{_fmt(r['hit_rate'],'%'):<7}"
              f"{_fmt(r['avg_ret'],'%',True):<8}{r['level']:<8}{r['evolve']:<5}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    s = stock_arc.stats()
    print(f"有价格的腿 {s['priced_legs']} / 全部 {s['all_legs']}（待结算 {s['pending']}）")
    print("口径：次日开盘买、次日收盘卖（账户同口径）；n=样本，命中=正收益占比")
    for title, key in (("按成交额门槛", "by_gate"), ("按是否进池", "by_pool"),
                       ("按竞价位置（开盘即知）", "by_zone"),
                       ("按这只票此前涨停过几次（无未来函数）", "by_prior_appearances"),
                       ("连板按量能", "by_vol"), ("按板数", "by_boards")):
        print(f"\n── {title}")
        for k, v in (s.get(key) or {}).items():
            if not v.get("n"):
                continue
            print(f"   {k:<10} n={v['n']:>4}  均{v['avg']:>+7.2f}%  中位{v['median']:>+7.2f}%"
                  f"  命中{v['hit']:>5.1f}%  赚时{_fmt(v.get('avg_win'),'%',True):>7}"
                  f"  亏时{_fmt(v.get('avg_loss'),'%',True):>7}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="个股生涯（反复出现的票怎么变现）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("rebuild")
    r.add_argument("--today", default=None)
    r.add_argument("--no-fetch", action="store_true", help="不拉日线（只出事实层）")
    r.set_defaults(func=cmd_rebuild)

    dl = sub.add_parser("daily", help="盘后一次做完：重算 + 冻结当日跟踪清单")
    dl.add_argument("--today", default=None)
    dl.add_argument("--closed", action="store_true",
                    help="当日已收盘（可结算今天这一档）；盘中切勿加")
    dl.set_defaults(func=cmd_daily)

    sh = sub.add_parser("show")
    sh.add_argument("code")
    sh.set_defaults(func=cmd_show)

    pt = sub.add_parser("patrol")
    pt.add_argument("date", nargs="?", default=None)
    pt.set_defaults(func=cmd_patrol)

    st = sub.add_parser("stats")
    st.set_defaults(func=cmd_stats)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
