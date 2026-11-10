#!/usr/bin/env python3
"""账户净值轨补结算（`wealth_code/cache/account_nav_history.jsonl`）。

## 为什么需要它（2026-11-09 查出的 P3）

账户净值轨最后一行停在 **2026-10-21**，之后 10-22 ~ 11-09 约 12 个交易日**一条新行都没有**，
但同期 AI 轨（`ai_nav_history.jsonl`）是正常更新的。

根因不在本脚本要修的地方（在 `run_wealth_code._finalize_prev_batch`）：它只结算「最近一个批次」，
写行还要求 `next_day.status == "covered"`（需 `can_enter ∧ next_close 抓到 ∧ quote 存在`）。
只要那天抓价失败 → `pending` → `nav_ledger` 跳过 → **第二天批次就换成新的，旧的永不重试**。
断一天 = 永久断。

本脚本是**事后补档工具**：拿已经落盘的台账（当日收盘价已定稿），把缺的那些行补回去。
⛔ 它不修那个「不重试」的根因（那需要改结算链，另立一轮）。

## 口径已验证（2026-11-09）

`account_nav` 的 `avg_return_pct` == 台账 `summary.auction_buy_close_sell.avg`，
即「09:25 竞价价买 → **当日**收盘卖、可成交票等权」。三天逐位复核：

| 日期 | 票 | 竞价价 | 收盘 | 我算 | account_nav |
|---|---|---|---|---|---|
| 10-12 | 601086 | 15.30 | 13.45 | −12.09% | −12.09% ✅ |
| 10-14 | 600693 | 12.40 | 12.19 | −1.69% | −1.69% ✅ |
| 10-21 | 603636 | 8.95 | 9.78 | +9.27% | +9.27% ✅ |

所以补档**直接取台账数字**，⛔ 不重新抓价（历史日抓「昨收」拿到的是今天的昨收，会算错）。

## 三条硬约束

1. ⛔ **已存在的 `trade_date` 一律不覆盖** —— 不改历史已定稿数字。
2. ⛔ **只允许补在末尾**：若缺口夹在两个既有行中间，重算中间行的 `nav` 会连带改掉后面既有行的净值
   ⇒ 直接报错退出，让人工判断，不偷偷改。
3. ⛔ **不编造 `caliber`**：补出来的行写空字符串 + `source=pool_ledger_backfill` 留痕，
   不冒充 `ai` / `mechanical`。

用法：
    python3 scripts/backfill_account_nav.py --check     # 只打印将要补什么
    python3 scripts/backfill_account_nav.py            # 落盘
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NAV = ROOT / "wealth_code" / "cache" / "account_nav_history.jsonl"
LEDGER_DIR = ROOT / "cache" / "pool_ledger"

# 沿用既有行的 strategy 值（⛔ 不要改：改了会让序列出现两种 strategy，
# 下游按 strategy 分组时会把这批行单独分出去）。
# ⚠️ 这个名字历史遗留、与实现不符：它实际记的是「竞价买→当日收盘」，
#    不是名字暗示的「次日收盘」。与本文件口径验证表一致，勿按名字理解。
STRATEGY = "equal_weight_next_day_close"


def _prev_trading_day(date10: str) -> str:
    """上一交易日（单一源 `common.trading_calendar`；取不到就留空，⛔ 不猜）。"""
    try:
        from common.trading_calendar import prev_a_share_trading_day
        return str(prev_a_share_trading_day(date10) or "")
    except Exception:                                    # noqa: BLE001
        return ""


def _read_nav() -> list[dict]:
    if not NAV.exists():
        return []
    out = []
    for line in NAV.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:                                # noqa: BLE001
            continue
    return out


def _read_ledgers() -> dict[str, dict]:
    """交易日 → 台账 summary 里的主口径（只取有可成交票的）。"""
    out: dict[str, dict] = {}
    for fp in sorted(LEDGER_DIR.glob("*.json")):
        try:
            led = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            continue
        date = str(led.get("date") or fp.stem)
        agg = ((led.get("summary") or {}).get("auction_buy_close_sell") or {})
        n = agg.get("n") or 0
        avg = agg.get("avg")
        if n <= 0 or avg is None:
            continue                                     # 空仓日不记行（与 nav_ledger 同语义）
        rows = [r for r in (led.get("rows") or [])
                if isinstance(r, dict) and r.get("tradable") and r.get("au_ret") is not None]
        out[date] = {
            "n": int(n),
            "avg": round(float(avg), 4),
            "stocks": [{"code": str(r.get("code") or ""),
                        "name": str(r.get("name") or ""),
                        "return_pct": round(float(r["au_ret"]), 4)} for r in rows],
        }
    return out


def plan(nav_rows: list[dict], ledgers: dict[str, dict]) -> tuple[list[dict], str | None]:
    """算出要补的行（含 nav 复利）。返回 (新行, 错误信息)。"""
    if not nav_rows:
        return [], "净值文件为空或不存在 —— 无基准 nav，⛔ 不凭空起一条新序列"

    have = {str(r.get("trade_date")) for r in nav_rows}
    last_date = max(have)
    missing = sorted(d for d in ledgers if d not in have)
    if not missing:
        return [], None

    # ⛔ 只允许补末尾：缺口夹在中间会连带改掉后面既有行的 nav
    early = [d for d in missing if d < last_date]
    if early:
        return [], (f"缺口不在末尾：{early[:5]} 早于最后一行 {last_date}。"
                    f"补这些行会连带重算后面既有行的净值 —— ⛔ 拒绝执行，需人工判断。")

    try:
        base_nav = float(nav_rows[-1].get("nav"))
    except (TypeError, ValueError):
        return [], "最后一行 nav 不是数字 —— 无基准，⛔ 不推断"

    new_rows: list[dict] = []
    nav = base_nav
    for d in missing:
        item = ledgers[d]
        nav = nav * (1 + item["avg"] / 100.0)
        new_rows.append({
            "trade_date": d,
            "recommendation_date": _prev_trading_day(d),
            "strategy": STRATEGY,
            # ⛔ 不冒充 ai / mechanical：这是事后从台账补的
            "caliber": "",
            "note": "",
            "stock_count": item["n"],
            "avg_return_pct": item["avg"],
            "nav": round(nav, 6),
            "codes": [s["code"] for s in item["stocks"]],
            "names": [s["name"] for s in item["stocks"]],
            "stocks": item["stocks"],
            # 留痕：这行不是结算链写的，是补档补的
            "source": "pool_ledger_backfill",
        })
    return new_rows, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只打印，不写文件")
    a = ap.parse_args()

    nav_rows = _read_nav()
    ledgers = _read_ledgers()
    new_rows, err = plan(nav_rows, ledgers)

    if err:
        print(f"⛔ {err}")
        return 2

    if not new_rows:
        print(f"✅ 无需补行（净值 {len(nav_rows)} 行，台账 {len(ledgers)} 个交易日，无缺口）")
        return 0

    base = float(nav_rows[-1]["nav"])
    print(f"基准：{nav_rows[-1]['trade_date']} nav={base:.6f}")
    print(f"{'日期':<12}{'只数':>5}{'当日':>9}{'补后nav':>11}  {'来源台账'}")
    print("-" * 56)
    prev = base
    for r in new_rows:
        print(f"{r['trade_date']:<12}{r['stock_count']:>5}{r['avg_return_pct']:>+9.2f}"
              f"{r['nav']:>11.6f}  pool_ledger")
        prev = r["nav"]

    peak = max([float(x.get("nav") or 0) for x in nav_rows] + [r["nav"] for r in new_rows])
    print(f"\n补 {len(new_rows)} 行 → nav {base:.6f} → {new_rows[-1]['nav']:.6f}"
          f"（{((new_rows[-1]['nav'] / base) - 1) * 100:+.2f}%）")
    print(f"峰值 {peak:.6f} → 补后回撤 {(1 - new_rows[-1]['nav'] / peak) * 100:.2f}%"
          f"（补前口径回撤 {(1 - base / peak) * 100:.2f}%）")

    if a.check:
        print("\n（--check 未落盘）")
        return 0

    with NAV.open("a", encoding="utf-8") as f:
        for r in new_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    try:
        shown = NAV.relative_to(ROOT)
    except ValueError:                                   # 测试会把 NAV 指到 tmp
        shown = NAV
    print(f"\n✅ 已追加 {len(new_rows)} 行 → {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
