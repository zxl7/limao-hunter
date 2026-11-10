#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""池内票每日台账：09:25 竞价价 → 当日收盘，赚没赚一目了然（2026-11-06 立项）。

## 为什么要有它（真实教训）

15:19 定 v1.0 基线时，11-06 的日线还没落盘，皇台酒业那笔 `ret=null` 被**静默跳过**，
D 组被算成 +13.22%；15:32 数据补齐后实际只有 **+2.56%**，最大回撤也从 −15.8% 变成 −22.9%。
缺的恰好是"当天唯一那笔"，足以把结论翻转。本脚本就是堵这个洞：

- **缺当日 K 线就报错退出**，不写半截文件、不静默跳过（`--allow-missing` 才放宽）；
- 把池内每只票的「竞价价 / 开盘 / 最高 / 最低 / 收盘 / 裁决 / 是否封板」写成一行，复盘不用再 join 三处。

⛔ 只读聚合：不改任何裁决、不重算阈值、不覆盖原始产物。同一输入重复跑结果一致。

## 用法

```bash
python3 scripts/pool_ledger.py --date 2026-11-06        # 单日，写 cache/pool_ledger-<date>.json
python3 scripts/pool_ledger.py --days 5                 # 近 5 个有冻结件的交易日
python3 scripts/pool_ledger.py --date 2026-11-06 --check  # 只校验+打印，不落盘
```

退出码：0 正常；2 当日 K 线未落盘（等收盘数据同步后重跑）；3 无冻结件。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WC = ROOT / "wealth_code"
POOL = WC / "cache"
KLINE = ROOT / "cache" / "kline_qfq"
ZT = ROOT / "cache" / "zt_history"
OUT = ROOT / "cache" / "pool_ledger"


def jl(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        return {}


def f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def agg(sel: list[float]) -> dict:
    """一组收益的汇总（n / 均值 / 中位 / 胜率 / 极值）。空列表 → 只有 n=0。

    2026-11-09 从 `build()` 的闭包提到模块级：`--backfill-t1` 也要用它重算 T+1 汇总，
    两处各留一份就是两套口径。⛔ 语义未改，`build()` 内调用点逐字不变。
    """
    if not sel:
        return {"n": 0}
    return {"n": len(sel),
            "avg": round(statistics.mean(sel), 2),
            "med": round(statistics.median(sel), 2),
            "win": round(sum(1 for x in sel if x > 0) / len(sel) * 100, 1),
            "max": round(max(sel), 2), "min": round(min(sel), 2)}


def bars(code: str) -> dict[str, dict]:
    d = jl(KLINE / f"{code}.json")
    return {str(b.get("date")): b for b in (d.get("bars") or []) if b.get("date")}


def net_days() -> list[str]:
    """有冻结件的交易日（与账本/回放同一口径，不自己数星期几）。"""
    return sorted(p.stem.replace("pool_call-", "") for p in POOL.glob("pool_call-*.json"))


def _next_bar(cur: dict, code: str, date: str) -> dict | None:
    """该票**下一个**交易日的 K 线（T+1 口径用）。缺 → None（⛔ 不当日收盘顶）。"""
    allb = bars(code)
    later = sorted(d for d in allb if d > date)
    return allb[later[0]] if later else None


def build(date: str, allow_missing: bool = False) -> tuple[dict | None, int]:
    snap = jl(POOL / f"pool_call-{date}.json")
    if not snap:
        print(f"⛔ {date} 无冻结件 pool_call-{date}.json")
        return None, 3
    views = {c: v for c, v in (snap.get("views") or {}).items() if isinstance(v, dict)}
    quotes = jl(POOL / f"auction_quotes-{date}.json")
    zt = {str(r.get("code") or "").strip()
          for r in ((jl(ZT / f"zt_{date}.json") or {}).get("zt_list") or [])}

    rows, miss_q, miss_k = [], [], []
    for code, v in views.items():
        q = quotes.get(code) or {}
        au, pc = f(q.get("auction_price")), f(q.get("prev_close"))
        if not au or not pc:
            miss_q.append(code)
        b = bars(code).get(date)
        if not b:
            miss_k.append(code)
            continue
        o, c, hi, lo = f(b.get("open")), f(b.get("close")), f(b.get("high")), f(b.get("low"))
        lim = 20.0 if v.get("is_20cm") else 10.0
        gap = f(v.get("gap_pct"))
        if gap is None and au and pc:
            gap = (au / pc - 1) * 100
        one_word = bool(hi and lo and hi == lo)
        # 竞价封死 = 竞价价已在涨停价（留 0.2pct 容差，与 pool_call 同口径）
        sealed_auction = bool(au and pc and (au / pc - 1) * 100 >= lim - 0.2)
        # 📏 T+1 口径（2026-11-06 用户「另一个记录计算 t+1」）：竞价买 → **次日收盘**卖。
        #    ⛔ 次日 K 线未落盘 → None（不填 0、不拿当日收盘顶）；⛔ 一字板当日买不进时不参与。
        nb = _next_bar(b, code, date)
        nclose = f((nb or {}).get("close"))
        rows.append({
            "code": code, "name": v.get("name"),
            "boards": v.get("boards"), "limit_up_pct": lim,
            "gap_pct": round(gap, 2) if gap is not None else None,
            # 用户口径：09:25 竞价价买 → 当日收盘卖
            "auction_price": au, "prev_close": pc,
            "open": o, "high": hi, "low": lo, "close": c,
            "amount_yi": f(v.get("amount_yi")),
            "need_yi": f(v.get("need_yi")),
            "theme": v.get("theme_name"),
            "view": v.get("view"), "recheck_class": v.get("recheck_class"),
            "reason": v.get("reason"),
            "chg_pct": round((c / pc - 1) * 100, 2) if (c and pc) else None,
            "au_ret": round((c / au - 1) * 100, 2) if (c and au) else None,
            "oc_ret": round((c / o - 1) * 100, 2) if (c and o) else None,
            # 📏 T+1 另立口径：竞价买 → 次日收盘卖（⛔ 与 au_ret 并列展示，不互相替代）
            "t1_ret": round((nclose / au - 1) * 100, 2) if (nclose and au) else None,
            "t1_date": (nb or {}).get("date"),
            # 🕳️ DEV = 今开 − 该票自己的质量预期（来自 09:25 冻结件）。
            #    ⛔ 只作诊断维度，不参与本脚本任何汇总口径。
            "dev": f(v.get("gap_vs_expected")),
            "one_word": one_word,
            "sealed_auction": sealed_auction,
            "sealed": code in zt,
            # 可成交 = 非一字（买不进）且竞价未封死
            "tradable": (not one_word) and (not sealed_auction) and bool(c),
            "price_mismatch": round((o / au - 1) * 100, 3) if (o and au) else None,
        })

    if miss_k and not allow_missing:
        print(f"⛔ {date} 有 {len(miss_k)} 只票缺当日 K 线（收盘数据未落盘？）："
              f"{'、'.join(miss_k[:8])}")
        print("   等收盘数据同步后重跑；确需忽略请加 --allow-missing")
        return None, 2

    tr = [r for r in rows if r["tradable"] and r["au_ret"] is not None]
    rets = [r["au_ret"] for r in tr]
    # `agg` 已提到模块级（2026-11-09）：`--backfill-t1` 也要用它重算 T+1 汇总。

    by_view: dict[str, dict] = {}
    for vname in ("participate", "watch", "skip"):
        sub = [r["au_ret"] for r in tr if r["view"] == vname]
        if sub:
            by_view[vname] = agg(sub)

    bands = [("≤−5%", None, -5), ("−5~−2%", -5, -2), ("−2~0%", -2, 0),
             ("0~2%", 0, 2), ("2~5%", 2, 5), ("≥5%", 5, None)]
    by_band: dict[str, dict] = {}
    for lab, lo, hi in bands:
        sub = [r["au_ret"] for r in tr
               if r["gap_pct"] is not None
               and (lo is None or r["gap_pct"] > lo) and (hi is None or r["gap_pct"] <= hi)]
        if sub:
            by_band[lab] = agg(sub)

    # 🕳️ 按 DEV 分档（2026-11-06 新增维度）：今开分档只说「开了多少」，
    #    DEV 说「比它自己该开的差多少」—— 后者才是 `gap_zone` 真正用的判据，
    #    且实测是 U 形（<−6 最好、−4~−2 最差），今开分档看不出这个结构。
    dev_bands = [("≤−6", None, -6), ("−6~−4", -6, -4), ("−4~−2", -4, -2),
                 ("−2~0", -2, 0), ("0~3", 0, 3), ("≥3", 3, None)]
    by_dev: dict[str, dict] = {}
    for lab, lo, hi in dev_bands:
        sub = [r["au_ret"] for r in tr
               if r["dev"] is not None
               and (lo is None or r["dev"] > lo) and (hi is None or r["dev"] <= hi)]
        if sub:
            by_dev[lab] = agg(sub)

    # 📏 T+1 汇总（独立口径，⛔ 不与 au_ret 混算）
    tr_t1 = [r for r in tr if r["t1_ret"] is not None]
    t1_agg = agg([r["t1_ret"] for r in tr_t1])

    ledger = {
        "date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": {"pool_call": f"wealth_code/cache/pool_call-{date}.json",
                   "quotes": f"wealth_code/cache/auction_quotes-{date}.json",
                   "kline": "cache/kline_qfq/<code>.json", "zt": f"cache/zt_history/zt_{date}.json"},
        "coverage": {"pool": len(views), "quoted": len(quotes),
                     "kline": len(rows), "missing_quotes": miss_q, "missing_kline": miss_k},
        "stance": snap.get("stance"), "quota": snap.get("quota"),
        "effective_quota": snap.get("effective_quota"),
        "summary": {"pool": len(rows), "tradable": len(tr),
                    "one_word": sum(1 for r in rows if r["one_word"]),
                    "sealed_auction": sum(1 for r in rows if r["sealed_auction"]),
                    "sealed": sum(1 for r in rows if r["sealed"]),
                    "auction_buy_close_sell": agg(rets),
                    "auction_buy_next_close_sell": t1_agg,
                    "by_view": by_view, "by_gap_band": by_band,
                    "by_dev_band": by_dev},
        "rows": sorted(rows, key=lambda r: (r["gap_pct"] if r["gap_pct"] is not None else -99)),
    }
    return ledger, 0


def _w(s: str, width: int) -> str:
    """按显示宽度补空格（中文/全角算 2 格）。"""
    s = str(s)
    cur = sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)
    return s + " " * max(0, width - cur)


def _l(s: str, width: int) -> str:
    return _w(s, width)


def _r(s: str, width: int) -> str:
    s = str(s)
    cur = sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)
    return " " * max(0, width - cur) + s


def show(led: dict) -> None:
    s = led["summary"]
    a = s["auction_buy_close_sell"]
    print(f"\n{'='*96}")
    print(f"池内台账 {led['date']}｜池 {s['pool']} 只 → 可成交 {s['tradable']} 只"
          f"（一字 {s['one_word']}、竞价封死 {s['sealed_auction']}）"
          f"｜当日封板 {s['sealed']} 只｜{led['stance']} / 名额 {led['effective_quota']}")
    print(f"{'='*96}")
    if a.get("n"):
        print(f"  ⭐ 09:25 竞价价买 → 当日收盘卖：{a['n']} 只  平均 {a['avg']:+.2f}%"
              f"／中位 {a['med']:+.2f}%／胜率 {a['win']:.1f}%"
              f"（最好 {a['max']:+.2f}%，最差 {a['min']:+.2f}%）")
    print()
    print("  " + _l("代码", 8) + _l("名称", 12) + _r("板", 3) + _r("今开", 8)
          + _r("竞价额", 9) + _l("存", 3) + _l("裁决", 12) + _r("收盘", 9)
          + _r("竞价→收盘", 11) + _r("当日", 9) + _r("封", 3))
    print("  " + "─" * 84)
    for r in led["rows"]:
        gap = "—" if r["gap_pct"] is None else f"{r['gap_pct']:+.2f}%"
        amt = "—" if r["amount_yi"] is None else f"{r['amount_yi']:.2f}"
        au = "—" if r["au_ret"] is None else f"{r['au_ret']:+.2f}%"
        chg = "—" if r["chg_pct"] is None else f"{r['chg_pct']:+.2f}%"
        cl = "—" if r["close"] is None else f"{r['close']:.2f}"
        mark = "★" if r["tradable"] else ("═" if r["one_word"] else "锁")
        seal = "✓" if r["sealed"] else ""
        print("  " + _l(r["code"], 8) + _l(r["name"] or "", 12) + _r(r["boards"] or 0, 3)
              + _r(gap, 8) + _r(amt, 8) + _l(mark, 3) + _l(r["view"] or "", 12)
              + _r(cl, 9) + _r(au, 11) + _r(chg, 9) + _r(seal, 3))
    print(f"\n  图例：★=可成交（计入统计） ═=一字板买不进 锁=竞价已封死"
          f"｜竞价→收盘 = 09:25 竞价价买、当日收盘卖")

    if s["by_view"]:
        print("\n  按裁决分组（只看可成交）：")
        for k, v in s["by_view"].items():
            print(f"    {k:<12} n={v['n']:<3} 平均 {v['avg']:+.2f}%／胜 {v['win']:.1f}%")
    if s["by_gap_band"]:
        print("\n  按今开幅度分组（只看可成交）：")
        for k, v in s["by_gap_band"].items():
            print(f"    {k:<8} n={v['n']:<3} 平均 {v['avg']:+.2f}%／胜 {v['win']:.1f}%")
    cov = led["coverage"]
    if cov["missing_quotes"]:
        print(f"\n  ⚠️ 竞价漏采 {len(cov['missing_quotes'])} 只：{'、'.join(cov['missing_quotes'][:8])}")
    bad = [r for r in led["rows"] if r.get("price_mismatch") is not None
           and abs(r["price_mismatch"]) > 0.5]
    if bad:
        print(f"  ⚠️ 竞价价与开盘价偏离 >0.5% 的 {len(bad)} 只（复核一下竞价快照）："
              f"{'、'.join(r['code'] for r in bad[:6])}")


def backfill_t1(lookback: int = 5, write: bool = True) -> int:
    """回补最近 `lookback` 个交易日的 `t1_ret`（竞价买 → **次日**收盘卖）。

    ## 为什么需要它（2026-11-09 查出的 P1）

    16:00 的自动化跑的是「当日」台账，而 D 日的 `t1_ret` 要等 **D+1 的收盘价**才成立。
    写 D 日台账那一刻 D+1 还没开盘 ⇒ 最新一天必然是 `n=0`，而且**永远不会自动补上**
    （16:00 只跑当日，不会回头重算昨天）。实测 11-06 一直 `T+1 n=0`，其余天都有人工跑过。

    ## 只填空，不覆盖

    ⛔ 已有数值的 `t1_ret` 一律不动 —— 这是「不改历史已定稿数字」的铁律。
    本函数**只重算 `t1_ret` / `t1_date` 两个字段和它的汇总**，`au_ret`（主口径）、
    `by_dev_band`、`by_view` 等一概不碰，避免一次回补把历史口径也带走。

    ## 缺次日 K 线怎么办

    `t1_ret` 留 `None`（⛔ 不填 0、不拿当日收盘顶），并把该日计入「待补」清单打印出来。
    """
    days = net_days()
    if not days:
        print("⛔ 无冻结件可读")
        return 3

    targets = days[-lookback:] if lookback > 0 else days
    filled = 0
    touched = 0
    pending: list[str] = []
    for d in targets:
        fp = OUT / f"{d}.json"
        if not fp.exists():
            continue                      # 该日台账还没生成过，交给正常 build 去写
        led = jl(fp)
        rows = led.get("rows") or []
        hit = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            if r.get("t1_ret") is not None:
                continue                  # ⛔ 不覆盖已有值
            au = f(r.get("auction_price"))
            code = str(r.get("code") or "")
            if not (au and code):
                continue
            nb = _next_bar({}, code, d)
            nclose = f((nb or {}).get("close"))
            if not nclose:
                continue                  # 次日 K 线未落盘 → 保持 None，计入待补
            r["t1_ret"] = round((nclose / au - 1) * 100, 2)
            r["t1_date"] = (nb or {}).get("date")
            hit += 1

        # 重算 T+1 汇总（只统计可成交票，与 build() 同口径）
        tr_t1 = [x["t1_ret"] for x in rows
                 if isinstance(x, dict) and x.get("tradable") and x.get("t1_ret") is not None]
        led.setdefault("summary", {})["auction_buy_next_close_sell"] = agg(tr_t1)
        still = [x.get("code") for x in rows
                 if isinstance(x, dict) and x.get("tradable") and x.get("t1_ret") is None]
        if still:
            pending.append(f"{d}（{len(still)} 只待次日 K 线）")

        if hit:
            filled += hit
            touched += 1
            led["t1_backfilled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if write:
                fp.write_text(json.dumps(led, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {d}  补 {hit} 笔 · T+1 n={led['summary']['auction_buy_next_close_sell'].get('n')}"
              f"{'  ⏳ ' + ', '.join(still[:3]) + ('…' if len(still) > 3 else '') if still else ''}")

    print(f"\n  回补完成：{touched} 个交易日 / {filled} 笔"
          f"{'（--check 未落盘）' if not write else ''}")
    if pending:
        print(f"  ⏳ 仍待补：{'；'.join(pending)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--check", action="store_true", help="只校验/打印，不写文件")
    ap.add_argument("--allow-missing", action="store_true", help="允许缺当日 K 线（不推荐）")
    # 📏 T+1 回补（2026-11-09）：不带值 = 回补最近 5 个交易日
    ap.add_argument("--backfill-t1", nargs="?", type=int, const=5, default=0, metavar="N",
                    help="回补最近 N 个交易日的 t1_ret（默认 5；只填空不覆盖）")
    a = ap.parse_args()

    days = net_days()
    targets = days[-a.days:] if a.days else ([a.date] if a.date else days[-1:])
    if not targets:
        print("⛔ 无冻结件可读")
        return 3

    rc = 0
    seq: list[dict] = []
    for d in targets:
        led, code = build(d, allow_missing=a.allow_missing)
        if not led:
            rc = code or 2
            continue
        show(led)
        if not a.check:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / f"{d}.json").write_text(json.dumps(led, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
            print(f"\n  已落盘 → cache/pool_ledger/{d}.json")
        seq.append({"date": d, **(led["summary"]["auction_buy_close_sell"] or {})})

    if len(seq) > 1:
        print(f"\n{'='*96}")
        print("多日汇总（只算可成交票，09:25 竞价价买 → 当日收盘卖）")
        print(f"{'='*96}")
        print(f"  {'日期':<12}{'只数':>5}{'平均':>9}{'中位':>9}{'胜率':>8}")
        allr: list[float] = []
        for s in seq:
            if not s.get("n"):
                print(f"  {s['date']:<12}{0:>5}      无可成交")
                continue
            print(f"  {s['date']:<12}{s['n']:>5}{s['avg']:>+9.2f}{s['med']:>+9.2f}{s['win']:>7.1f}%")
            allr.append(s["avg"])
        if allr:
            wr = sum(1 for s in seq if (s.get("win") or 0) > 50) / len(allr) * 100
            print(f"\n  {len(allr)} 个交易日：日均 {statistics.mean(allr):+.2f}%"
                  f"／日胜率 {wr:.1f}%（日收益>0 的天数占比）"
                  f"／累计 {sum(allr):+.2f}%（算术）")
    # 📏 T+1 回补放在最后：先把当日台账 build+落盘（缺当日 K 线会 exit 2，不写半截），
    #    再回头补历史那些「次日 K 线今天才落盘」的日子。两步互不依赖，失败也不互相污染。
    if a.backfill_t1:
        print(f"\n{'='*96}")
        print(f"T+1 回补（最近 {a.backfill_t1} 个交易日 · 只填空不覆盖）")
        print(f"{'='*96}")
        bf = backfill_t1(a.backfill_t1, write=not a.check)
        rc = rc or bf

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
