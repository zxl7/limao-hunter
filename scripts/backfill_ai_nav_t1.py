#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填 AI 轨 T+1 口径净值（2026-11-11）

从 `wealth_code/cache/ai_nav_history.jsonl`（策略口径，已结算）逐日算出
「同一批买入 → 次一交易日收盘卖」的收益，写入独立文件 `ai_nav_history_t1.jsonl`。
⛔ 不改策略口径文件；次日行情缺失的日子**跳过**（不写半截、不补 0）。

    python3 scripts/backfill_ai_nav_t1.py            # 全量重建
    python3 scripts/backfill_ai_nav_t1.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code" / "scripts"))

from ai_ledger import AI_LEDGER, AI_LEDGER_T1, BASE_NAV, STRATEGY_T1, _close_of, _next_trade_day  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="回填 AI 轨 T+1 净值")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    rows = [json.loads(l) for l in AI_LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    out, nav = [], BASE_NAV
    for r in sorted(rows, key=lambda x: str(x.get("trade_date"))):
        tdate = str(r.get("trade_date") or "")
        stocks = r.get("stocks") or []
        if not tdate or not stocks:
            continue
        nxt = _next_trade_day(tdate)
        t1 = []
        ok = True
        for s in stocks:
            code = str(s.get("code") or "")
            ap_price = s.get("auction_price")
            close = _close_of(code, nxt) if code else None
            if not ap_price or close is None:
                ok = False
                break
            t1.append({"code": code, "name": s.get("name"), "auction_price": float(ap_price),
                       "exit_date": nxt, "exit_close": float(close),
                       "return_pct": round((float(close) - float(ap_price)) / float(ap_price) * 100, 2)})
        if not ok or not t1:
            print(f"  跳过 {tdate}（{nxt} 行情未落盘）")
            continue
        avg = round(sum(x["return_pct"] for x in t1) / len(t1), 4)
        nav = round(nav * (1 + avg / 100.0), 6)
        out.append({"trade_date": tdate, "strategy": STRATEGY_T1,
                    "caliber": "竞价价买 → 次日收盘卖", "exit_date": nxt,
                    "stock_count": len(t1), "avg_return_pct": avg, "nav": nav,
                    "stocks": t1,
                    "ts": datetime.now(timezone(timedelta(hours=8))).isoformat()})

    print(f"[t1] 可回填 {len(out)} 个结算日")
    for x in out:
        print(f"  {x['trade_date']} → {x['exit_date']}  {x['stock_count']} 只  "
              f"均 {x['avg_return_pct']:+.2f}%  净值 {x['nav']:.4f}")
    if args.dry_run:
        return 0
    AI_LEDGER_T1.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out),
                            encoding="utf-8")
    print(f"[t1] 已写 {AI_LEDGER_T1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
