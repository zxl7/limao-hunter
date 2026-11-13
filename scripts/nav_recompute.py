#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""💰 账本重算：按仓位加权（2026-11-13 用户「收益要按仓位算」）

旧账本 `avg_return_pct` = 个股收益**等权平均**，忽略 `position_cap`：
- 只买 1 只、仓位 37.5%、跌 8.73% → 记为 −8.73%，实际账户只跌 **−3.27%**。

本脚本：
1. 对**有冻结件**的日子，用 `position_cap` 重算 `account_return_pct`；
2. 无冻结件的日子保留等权（标 `weight=equal_fallback`）；
3. 默认只打印对比（`--dry-run` 行为），`--write` 才覆盖（先备份）。

    python3 scripts/nav_recompute.py            # 只看
    python3 scripts/nav_recompute.py --write    # 写入（备份 .bak）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import nav_ledger as nl  # noqa: E402

LEDGER = ROOT / "wealth_code" / "cache" / "ai_nav_history.jsonl"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    old_nav = new_nav = 1.0
    changed = 0
    print(f"{'日期':<12}{'旧':>9}{'新':>9}{'仓位合计':>8}")
    for r in rows:
        caps = nl._load_position_caps(str(r.get("trade_date")))
        stocks = r.get("stocks") or []
        ok = bool(caps) and bool(stocks) and all(str(s.get("code")) in caps for s in stocks)
        old = float(r.get("avg_return_pct") or 0.0)
        new = round(sum(caps[str(s["code"])] * float(s.get("return_pct") or 0) for s in stocks), 4) if ok else old
        old_nav *= 1 + old / 100
        new_nav *= 1 + new / 100
        if ok:
            changed += 1
            r["account_return_pct"] = new
            r["weight"] = "position"
            print(f"{r['trade_date']:<12}{old:>8.2f}%{new:>8.2f}%{sum(caps.values()):>8.2f}")
        else:
            r["account_return_pct"] = old
            r["weight"] = str(r.get("weight") or "equal_fallback")
    print(f"\n可重算 {changed}/{len(rows)} 天")
    print(f"净值：旧口径 {old_nav:.4f} → 仓位口径 {new_nav:.4f}")
    if a.write:
        bak = LEDGER.with_suffix(".jsonl.bak")
        shutil.copy2(LEDGER, bak)
        LEDGER.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
        print(f"已写入（备份 {bak.name}）")
    else:
        print("（只读；加 --write 才写入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
