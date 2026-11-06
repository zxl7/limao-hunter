#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价三环影子 · 逐日留痕对照表（只读研究，⛔ 不参与 09:25 裁决）。

用途
────
把 09:25 已落地的**生产影子字段**（`core/sector_gap_context.py`）与**事后收益**按
交易日对账，回答一个问题：这套三环判据如果真开成硬闸，历史上每天会砍掉谁、
砍对了没有。

与 `research_sector_gap_backtest.py` 的分工
──────────────────────────────────────────
  回测脚本  = 找判据、调阈值、分层统计（横截面）
  本脚本    = 判据已定稿后，逐日留痕 + **T+1 结算口径**对账（时间序列）

收益口径（两套并列，⛔ 不可混用）
────────────────────────────────
  same_close : 竞价价 → 当日收盘。系统决策评估代理，**不是** A 股 T+1 可执行收益。
  t1_close   : 竞价价 → **次一交易日收盘**。对齐 `ai_ledger.STRATEGY =
               ai_judgement_next_day_close`，是账本结算口径的可执行代理。
               无成交回报、无费用滑点，⛔ 仍不是真实券商战绩。
  次一交易日由该股 `cache/kline_qfq/<code>.json` 中 date > T 的第一根日 K 决定
  （数据驱动，不用日历推断；停牌跨日会显式记 t1_date 供核对）。

排除
────
  竞价封死买不进（`ai_caliber.unbuyable_gap`）/ 688 / ST / 缺当日收盘 /
  竞价覆盖率不足的交易日（`--min-coverage`，防早期快照只覆盖候选子集导致板块分母失真）。

⛔ 边界
────
  影子 `linked_would_cut` 只是「若开 LC 守卫会被砍」的平行标记，恒不改 view/zone/
  quota/仓位；本脚本不写冻结件、不写账本、不写结算、不推群、不抓行情。
  每天重算近 N 日窗口，天然幂等，无 append 状态。

用法
────
  python3 scripts/sector_gap_daily_trace.py                        # 近 20 日 → 默认产物
  python3 scripts/sector_gap_daily_trace.py --days 40 --json      # 扩窗口 + 打印 JSON
  python3 scripts/sector_gap_daily_trace.py --out /tmp/trace.json # 指定输出
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))
os.environ.setdefault("AI_EXPECTED_GAP_MODE", "relative")

from common.trading_calendar import prev_a_share_trading_day  # noqa: E402
from core import ai_caliber as ac                              # noqa: E402
from core import expected_gap as eg                            # noqa: E402
from core import sector_gap_context as sgc                    # noqa: E402

SCHEMA = "sector_gap_daily_trace_v1"
DEFAULT_OUT = ROOT / "cache" / "sector_gap_trace" / "daily_trace.json"
_BARS: dict[str, list[dict]] = {}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def bars_of(code: str) -> list[dict]:
    """全序列日 K（含 T、T+1）——⛔ 只在事后结算用，绝不喂给影子上下文。"""
    if code not in _BARS:
        d = _load(ROOT / "cache" / "kline_qfq" / f"{code}.json", {})
        v = d.get("bars") if isinstance(d, dict) else None
        _BARS[code] = [b for b in (v or []) if isinstance(b, dict) and b.get("date")]
        _BARS[code].sort(key=lambda b: str(b["date"]))
    return _BARS[code]


def trading_days(end: str, n: int) -> list[str]:
    days = [end]
    for _ in range(n - 1):
        days.append(prev_a_share_trading_day(days[-1]))
    return sorted(days)


def stat(rows: list[dict], key: str) -> dict:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": len(rows), "covered": 0, "mean": None, "median": None,
                "win_rate": None, "zt": sum(1 for r in rows if r.get("zt_today"))}
    return {
        "n": len(rows),
        "covered": len(vals),
        "mean": round(st.mean(vals), 2),
        "median": round(st.median(vals), 2),
        "win_rate": round(sum(1 for x in vals if x > 0) / len(vals) * 100, 1),
        "zt": sum(1 for r in rows if r.get("zt_today")),
    }


def build_day(d: str, prev: str) -> tuple[dict, list[dict]]:
    q = _load(ROOT / "wealth_code" / "cache" / f"auction_quotes-{d}.json", {}) or {}
    yz = _load(ROOT / "cache" / "zt_history" / f"zt_{prev}.json", {}) or {}
    tz = _load(ROOT / "cache" / "zt_history" / f"zt_{d}.json", {}) or {}
    th = _load(ROOT / "cache" / "theme_snapshot" / f"theme_{prev}.json", {}) or {}
    z4 = {str(r.get("code")): r for r in (yz.get("zt_list") or [])}
    z5 = {str(r.get("code")): r for r in (tz.get("zt_list") or [])}
    mapping = th.get("mapping") or {}
    if not isinstance(mapping, dict):
        mapping = {}

    # ── 生产影子上下文（与 09:25 主链 / --production 回测逐字同源）──────────
    ctx = sgc.build_context(
        quotes=q, zt_rows=list(z4.values()), plates_map=mapping,
        bars_by_code={c: [b for b in bars_of(c) if str(b["date"]) <= prev]
                      for c in q},
        prev_date=prev,
        expected_gaps={c: eg.expected_gap_pct(z4.get(c)) for c in q},
    )

    rows: list[dict] = []
    price_mismatch = 0
    for c, v in q.items():
        pc, ap = v.get("prev_close"), v.get("auction_price")
        if not (isinstance(pc, (int, float)) and isinstance(ap, (int, float))
                and pc > 0 and ap > 0):
            continue
        y = z4.get(c) or {}
        name = (z5.get(c) or y).get("name") or (mapping.get(c) or {}).get("name") or ""
        is20 = bool(y.get("is_20cm"))
        gap = round((ap / pc - 1) * 100, 2)
        if ac.unbuyable_gap(gap, is20) or c.startswith("688") or "ST" in str(name).upper():
            continue

        bars = bars_of(c)
        b_prev = next((b for b in reversed(bars) if str(b["date"]) <= prev), None)
        b_today = next((b for b in bars if str(b["date"]) == d), None)
        b_next = next((b for b in bars if str(b["date"]) > d), None)
        # 前复权价与实际竞价价不一致（除权/数据错配）→ 跳过，不猜收益
        if b_prev and abs(float(b_prev["close"]) - pc) / pc > 0.01:
            price_mismatch += 1
            continue
        close = (b_today or {}).get("close")
        if not isinstance(close, (int, float)) or close <= 0:
            continue
        t1_close = (b_next or {}).get("close")
        t1_date = (b_next or {}).get("date")
        e = eg.expected_gap_pct(y)
        x = ctx.get(c) or {}
        cr = x.get("credits") or {}
        rows.append({
            "date": d, "code": c, "name": name,
            "gap": gap, "E": e,
            "DEV": round(gap - e, 2) if e is not None else None,
            "zone": ac.gap_zone(gap, None, expected_gap_pct=e, is_20cm=is20),
            "theme": x.get("linked_theme"),
            "theme_src": x.get("linked_theme_src"),
            "gap_vs_theme": x.get("gap_vs_theme"),
            "theme_gap_med": x.get("theme_gap_med"),
            "upper_gap_med": x.get("upper_gap_med"),
            "credits_n": cr.get("n"), "credits_known": cr.get("known"),
            "veto": bool(cr.get("veto")),
            "status": x.get("linked_status"),
            "isolated": bool(x.get("linked_isolated_high_open")),
            "would_cut": bool(x.get("linked_would_cut")),
            "R_pct": x.get("linked_auction_r_pct"),
            "ret_same_close": round((close / ap - 1) * 100, 2),
            "ret_t1_close": (round((t1_close / ap - 1) * 100, 2)
                             if isinstance(t1_close, (int, float)) and t1_close > 0 else None),
            "t1_date": t1_date,
            "zt_today": c in z5,
        })

    v0 = [r for r in rows if r["zone"] == "ok"]
    cut = [r for r in v0 if r["would_cut"]]
    keep = [r for r in v0 if not r["would_cut"]]
    veto = [r for r in v0 if r["veto"]]
    # ⚠️ 两组不能混：`status=volume_veto` 是「三环齐全、只被量能证伪」的干净组；
    #    `veto=True` 全组还含 15 只 `abstain_missing_ring`（A 档「缺任一环不裁决」
    #    优先级更高），把它们算进量能证伪会显著稀释信号。
    veto_strict = [r for r in v0 if r["status"] == "volume_veto"]
    abstain = [r for r in v0 if str(r["status"] or "").startswith("abstain")]
    day = {
        "date": d, "prev": prev,
        "n_quotes": len(q), "n_prev_zt": len(z4),
        "coverage": round(len(q) / max(1, len(z4)), 3),
        "theme_src": "plates" if mapping else "sector_fallback",
        "n_tradable": len(rows), "n_v0": len(v0),
        "price_mismatch": price_mismatch,
        "cut_codes": [r["code"] for r in cut],
        "v0": {"same_close": stat(v0, "ret_same_close"), "t1_close": stat(v0, "ret_t1_close")},
        "cut": {"same_close": stat(cut, "ret_same_close"), "t1_close": stat(cut, "ret_t1_close")},
        "keep": {"same_close": stat(keep, "ret_same_close"), "t1_close": stat(keep, "ret_t1_close")},
        "veto": {"same_close": stat(veto, "ret_same_close"), "t1_close": stat(veto, "ret_t1_close")},
        "veto_strict": {"same_close": stat(veto_strict, "ret_same_close"),
                        "t1_close": stat(veto_strict, "ret_t1_close")},
        "abstain": {"same_close": stat(abstain, "ret_same_close"), "t1_close": stat(abstain, "ret_t1_close")},
    }
    a, b = day["v0"]["t1_close"]["mean"], day["keep"]["t1_close"]["mean"]
    day["delta_t1_pct"] = round(b - a, 2) if a is not None and b is not None else None
    return day, rows


def main() -> int:
    ap = argparse.ArgumentParser(description="竞价三环影子逐日留痕对照表（只读）")
    ap.add_argument("--days", type=int, default=20, help="名义窗口交易日数（默认 20）")
    ap.add_argument("--end", default=None, help="窗口末日 YYYY-MM-DD，默认今天")
    ap.add_argument("--min-quotes", type=int, default=10, help="竞价报价下限（默认 10）")
    ap.add_argument("--min-coverage", type=float, default=0.6,
                    help="竞价数/昨涨停池数下限，防早期只覆盖候选子集（默认 0.6）")
    ap.add_argument("--detail-days", type=int, default=5,
                    help="输出逐票明细的最近交易日数（默认 5，其余只留日汇总）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true", help="把完整 JSON 打到 stdout")
    a = ap.parse_args()

    end = a.end or datetime.now().strftime("%Y-%m-%d")
    days = trading_days(end, a.days)
    detail_set = set(days[-a.detail_days:]) if a.detail_days > 0 else set()

    out_days, all_rows, rows, dropped = [], [], [], []
    for i, d in enumerate(days):
        prev = days[i - 1] if i > 0 else prev_a_share_trading_day(d)
        qp = ROOT / "wealth_code" / "cache" / f"auction_quotes-{d}.json"
        if not qp.exists():
            dropped.append((d, "no_quotes"))
            continue
        q = _load(qp, {}) or {}
        yz = _load(ROOT / "cache" / "zt_history" / f"zt_{prev}.json", {}) or {}
        n_prev = len(yz.get("zt_list") or [])
        cov = len(q) / max(1, n_prev)
        if len(q) < a.min_quotes or cov < a.min_coverage:
            dropped.append((d, f"coverage {len(q)}/{n_prev}={cov:.2f}"))
            continue
        day, rs = build_day(d, prev)
        out_days.append(day)
        all_rows.extend(rs)
        if d in detail_set:
            rows.extend(rs)

    v0 = [r for r in all_rows if r["zone"] == "ok"]
    cut = [r for r in v0 if r["would_cut"]]
    keep = [r for r in v0 if not r["would_cut"]]
    veto = [r for r in v0 if r["veto"]]
    veto_strict = [r for r in v0 if r["status"] == "volume_veto"]
    abstain = [r for r in v0 if str(r["status"] or "").startswith("abstain")]

    def pair(rs):
        return {"same_close": stat(rs, "ret_same_close"),
                "t1_close": stat(rs, "ret_t1_close")}

    better = [x for x in out_days
              if x["cut"]["t1_close"]["mean"] is not None
              and x["keep"]["t1_close"]["mean"] is not None
              and x["cut"]["t1_close"]["mean"] < x["keep"]["t1_close"]["mean"]]
    worse = [x for x in out_days
             if x["cut"]["t1_close"]["mean"] is not None
             and x["keep"]["t1_close"]["mean"] is not None
             and x["cut"]["t1_close"]["mean"] > x["keep"]["t1_close"]["mean"]]

    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": {
            "start": out_days[0]["date"] if out_days else None,
            "end": out_days[-1]["date"] if out_days else None,
            "nominal_days": len(days), "active_days": len(out_days),
            "dropped": [{"date": d, "reason": r} for d, r in dropped],
        },
        "caliber": {
            "strategy": "ai_judgement_next_day_close",
            "same_close": "竞价价→当日收盘（系统决策评估代理，非 T+1 可执行）",
            "t1_close": "竞价价→次一交易日收盘（账本结算口径代理；无成交回报/费用/滑点）",
            "excluded": "竞价封死买不进 / 688 / ST / 缺收盘 / 覆盖不足日 / 前复权价错配",
            "warning": "晋级率 ≠ 买入胜率 ≠ 可执行收益；影子 only 留痕，恒不改裁决。",
            "shadow": "linked_would_cut = 若开 LC 守卫会被砍的平行标记，不是已执行动作",
        },
        "summary": {
            "n_tradable": len(all_rows), "n_v0": len(v0),
            "n_cut": len(cut), "n_keep": len(keep),
            "n_veto": len(veto), "n_veto_strict": len(veto_strict),
            "n_abstain": len(abstain),
            "v0": pair(v0), "cut": pair(cut), "keep": pair(keep),
            "veto": pair(veto), "veto_strict": pair(veto_strict),
            "abstain": pair(abstain),
            "cut_better_days": len(better), "cut_worse_days": len(worse),
            "cut_better_dates": [x["date"] for x in better],
            "cut_worse_dates": [x["date"] for x in worse],
        },
        "days": out_days,
        "rows": rows,
    }

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if a.json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    s = payload["summary"]
    print(f"窗口 {payload['window']['start']} ~ {payload['window']['end']} · "
          f"名义 {len(days)} 日 / 有效 {len(out_days)} 日 · 剔除 "
          f"{len(dropped)} 日（{', '.join(d for d, _ in dropped) or '无'}）")
    print(f"可买 {s['n_tradable']} 票次｜现判据 {s['n_v0']}｜影子砍 {s['n_cut']}｜保留 {s['n_keep']}｜量能证伪 {s['n_veto']}｜缺环不裁决 {s['n_abstain']}")
    print("\n【累计 · T+1 结算口径】")
    for lb, g in [("现判据 V0", s["v0"]), ("影子砍掉", s["cut"]),
                  ("影子保留", s["keep"]), ("量能证伪·干净组", s["veto_strict"]),
                  ("veto标记全组", s["veto"]), ("缺环不裁决", s["abstain"])]:
        t = g["t1_close"]
        print(f"  {lb:<12} n={t['n']:>4} 有价={t['covered']:>4} 均值={str(t['mean']):>7}% "
              f"中位={str(t['median']):>7}% 胜率={str(t['win_rate']):>6}% 晋级={t['zt']:>3}")
    print("\n【累计 · 当日收盘评估代理（对照）】")
    for lb, g in [("现判据 V0", s["v0"]), ("影子砍掉", s["cut"]), ("影子保留", s["keep"])]:
        t = g["same_close"]
        print(f"  {lb:<12} n={t['n']:>4} 均值={str(t['mean']):>7}% 胜率={str(t['win_rate']):>6}%")
    print(f"\n逐日：砍掉组 T+1 更差 {len(better)} 日 / 更好 {len(worse)} 日 / 其余无对照")
    print("\n【逐日留痕】日期       题材源  可买  V0  砍  保留  砍组T+1  保留T+1   ΔT+1")
    for x in out_days:
        c, k = x["cut"]["t1_close"], x["keep"]["t1_close"]
        print(f"  {x['date']}  {x['theme_src']:<10} {x['n_tradable']:>4} {x['n_v0']:>4} "
              f"{len(x['cut_codes']):>3} {x['n_v0']-len(x['cut_codes']):>5} "
              f"{str(c['mean']):>8} {str(k['mean']):>9} {str(x['delta_t1_pct']):>7}")
    print(f"\n产物 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
