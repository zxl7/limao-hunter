#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账本订正工具（净值递推可复算，别手改数字）。

## 为什么需要它

净值是**递推**的（nav_t = nav_{t-1} × (1 + avg_t/100)），所以任何一天算错，
后面每一天的净值都跟着错。手工改一行会漏改后续，且没人能复核。

本工具做的事只有一件：**用日K把「当日收益」重算一遍，再整条重推净值**。

## 事故背景（2026-11-03）

`auction_quotes-2026-09-30.json` 里 603533 掌阅科技的报价其实是 **09-29 的竞价**
（`time: "2026-09-29 15:00:01"`，`prev_close 21.5` = 09-28 收盘、`auction_price 21.76` = 09-29 开盘）。

```
旧： (09-30 收盘 23.88 − 09-29 竞价 21.76) / 21.76 = +9.74%   ← 虚增 9.8pp
新： (09-30 收盘 23.88 − 09-30 开盘 23.90) / 23.90 = −0.08%
```

`ai_ledger` 侧已加校验（跨日报价 → 改用日K开盘价），本工具负责把**已经写坏的历史**订正回来。

## 用法

```
python3 wealth_code/scripts/ledger_repair.py            # dry-run，只报告改什么
python3 wealth_code/scripts/ledger_repair.py --apply    # 落盘（先备份到 ledger_repairs/<ts>/）
```

⛔ 只订正「能用日K复算出来」的收益；K线缺失的行**原样保留**并在报告里标 `no_kline`，
   不做任何推断。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
BASE = ROOT.parent                                      # 狸猫猎手/
CACHE = ROOT / "cache"
KLINE_DIR = BASE / "cache" / "kline_qfq"
AI_LEDGER = CACHE / "ai_nav_history.jsonl"
PY_LEDGER = CACHE / "account_nav_history.jsonl"
BACKUP_DIR = CACHE / "ledger_repairs"
BASE_NAV = 1.0
BJ_TZ = timezone(timedelta(hours=8))

# 竞价价与当日开盘价的容差（%）。A股集合竞价成交价**就是**开盘价，正常逐分一致。
DEV_TOL = 0.3


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _bars(code: str) -> dict[str, dict]:
    p = KLINE_DIR / f"{code}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {str(b["date"]).replace("-", ""): b for b in d.get("bars") or [] if b.get("date")}
    except Exception:
        return {}


def _truth(code: str, trade_date: str) -> tuple[float, float] | None:
    """→ (开盘价, 收盘价)；K线缺失/字段不全 → None（⛔ 不猜）。"""
    b = _bars(code).get(str(trade_date).replace("-", ""))
    if not b:
        return None
    try:
        return float(b["open"]), float(b["close"])
    except Exception:
        return None


def audit(rows: list[dict], label: str) -> tuple[list[dict], list[str]]:
    """逐行核对并重算；返回 (新行列表, 问题清单)。不改动入参对象。"""
    issues: list[str] = []
    out: list[dict] = []
    for r in rows:
        td = str(r.get("trade_date") or "")
        r2 = json.loads(json.dumps(r, ensure_ascii=False))       # 深拷贝，避免污染原数据
        rets: list[float] = []
        for s in r2.get("stocks") or []:
            truth = _truth(str(s.get("code") or ""), td)
            if truth is None:
                # 无K线：保留原值，但明确标注（不推断、不填充）
                issues.append(f"{label} {td} {s.get('code')} {s.get('name')} · no_kline → 原样保留")
                if s.get("return_pct") is not None:
                    rets.append(float(s["return_pct"]))
                continue
            op, cl = truth
            real_ap = op                        # 竞价成交价 ≡ 开盘价
            old_ap = s.get("auction_price")
            old_ret = s.get("return_pct")
            new_ret = round((cl - real_ap) / real_ap * 100, 2)
            if old_ap is not None and abs(float(old_ap) - real_ap) / real_ap * 100 > DEV_TOL:
                issues.append(
                    f"{label} {td} {s.get('code')} {s.get('name')} · 竞价价 {old_ap} ≠ 开盘价 {real_ap}"
                    f"（偏差 {(float(old_ap) - real_ap) / real_ap * 100:+.2f}%）"
                    f" → 收益 {old_ret}% 修为 {new_ret}%")
                s["auction_price"] = real_ap
                s["price_src"] = "kline_open"
                s["repair_note"] = f"原竞价价 {old_ap} 非当日（2026-11-03 订正）"
            elif old_ret is not None and abs(float(old_ret) - new_ret) > 0.02:
                issues.append(f"{label} {td} {s.get('code')} {s.get('name')} · 收益 {old_ret}% → {new_ret}%")
            s["close"] = cl if "close" in s else s.get("close")
            if "return_pct" in s or s.get("return_pct") is None:
                s["return_pct"] = new_ret
            rets.append(new_ret)
        if rets:
            r2["stock_count"] = len(rets)
            r2["avg_return_pct"] = round(sum(rets) / len(rets), 4)
        out.append(r2)
    return out, issues


def rebuild_nav(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """按 avg_return_pct 整条重推净值（起点 1.0），返回 (新行, 与旧值的差异说明)。"""
    diffs: list[str] = []
    nav = BASE_NAV
    for r in rows:
        avg = float(r.get("avg_return_pct") or 0.0)
        nav = round(nav * (1 + avg / 100.0), 6)
        old = r.get("nav")
        if old is None or abs(float(old) - nav) > 1e-9:
            diffs.append(f"  {r.get('trade_date')}  nav {old} → {nav}（avg {avg}%）")
        r["nav"] = nav
    return rows, diffs


def main() -> int:
    ap_ = argparse.ArgumentParser(description="用日K复算收益并重推净值（默认 dry-run）")
    ap_.add_argument("--apply", action="store_true", help="落盘（先备份）")
    ap_.add_argument("--ledger", choices=["ai", "account", "both"], default="both")
    args = ap_.parse_args()

    targets = []
    if args.ledger in ("ai", "both"):
        targets.append(("AI轨", AI_LEDGER))
    if args.ledger in ("account", "both"):
        targets.append(("账户", PY_LEDGER))

    stamp = datetime.now(BJ_TZ).strftime("%Y%m%dT%H%M%S%z")
    backup = BACKUP_DIR / stamp
    changed = False

    for label, path in targets:
        rows = _read(path)
        if not rows:
            print(f"[{label}] {path.name} 为空或不存在，跳过")
            continue
        fixed, issues = audit(rows, label)
        fixed, navdiff = rebuild_nav(fixed)
        print(f"\n══ {label} {path.name}（{len(rows)} 行）")
        if issues:
            print("  收益订正:")
            for i in issues:
                print("   ", i)
        else:
            print("  收益: 全部与日K一致 ✓")
        if navdiff:
            print("  净值重推:")
            for d in navdiff:
                print(d)
        else:
            print("  净值: 无需变更 ✓")
        if issues or navdiff:
            changed = True
            if args.apply:
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup / path.name)
                _write(path, fixed)
                print(f"  ✅ 已写入（备份 → {backup / path.name}）")
            else:
                print("  （dry-run：未写入，加 --apply 落盘）")

    if not changed:
        print("\n结论：两条账本都与日K一致，无需订正。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
