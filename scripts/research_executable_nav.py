#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可执行净值回测：把「等权日复利」的乐观代理换成**真金账户模拟**（2026-11-09）

## 为什么要有这个脚本（承接 2026-11-06「T+1 可执行口径」那次讨论）

`research_t1_caliber_ab.py` 已经把 D0 / T1C / T1O 三种口径摊开了，但它的 `summ()`
用的是 **「每日等权平均收益 → 日复利」** 这个老模型，它隐含了三个不成立的假设：

1. **资金无限**：D 日买入的票要 D+1 收盘才卖，D+1 开盘又满仓买新票 → 实际需要 2 倍资金；
2. **成交完美**：开盘价想买就买（一字板除外）、收盘价想卖就卖（跌停也能卖）；
3. **零成本**：佣金、印花税、过户费、冲击成本全无。

三者叠加会把 T1C 的乐观上限再抬高一大截。本脚本用**逐日资金账本**重算同一条策略：

    现金 + 持仓市值（逐日 mark-to-market）→ 真实净值曲线

## 约束（全部可开关，用于归因）

| 开关 | 含义 |
|---|---|
| `--capital` | 资金上限（默认 100 万）；不足时按可用现金等权摊，买不起就少买 |
| `--max-positions` | 单日最多持仓数（默认 5）；当日候选多于该数时按裁决顺序取前 N |
| `--fill` | 成交约束：开盘涨停/一字 → **买不到**；收盘或开盘跌停/停牌 → **卖不出**，顺延 |
| `--cost` | 费用：佣金万 2.5（最低 5 元）+ 印花税卖出 0.05% + 过户费 0.001%（双向） |

## 归因（四段式，差额相加＝与乐观模型的全部差距）

    乐观代理（旧 summ） → ① 加资金约束 → ② 加成交约束 → ③ 加费用 = 可执行净值

⚠️ 已知近似（**不藏**）：
  · K 线是前复权（`cache/kline_qfq`），除权日算出的涨跌停价会偏移 → 只影响边缘日；
  · 涨跌停比例按代码推断（主板 10% / 创业板科创板 20% / 北交所 30% / ST 5%），未取官方价；
  · 印花税/佣金按 2026 年常见档位，真实费率以券商为准（可用 `--cost-zero` 关闭对比）。

用法：
    python3 scripts/research_executable_nav.py                    # 默认 L4_v11 · T1C · 近 35 个交易日
    python3 scripts/research_executable_nav.py --sell T1O         # 卖在次日开盘（最保守）
    python3 scripts/research_executable_nav.py --json             # → cache/research/executable_nav_bt.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "wealth_code"))

KLINE_DIR = ROOT / "cache" / "kline_qfq"
OUT = ROOT / "cache" / "research" / "executable_nav_bt.json"

# 费率（可被 --cost-zero 关掉）
COMMISSION_RATE = 0.00025      # 佣金 万 2.5
COMMISSION_MIN = 5.0           # 单笔最低 5 元
STAMP_RATE = 0.0005            # 印花税（仅卖出）
TRANSFER_RATE = 0.00001        # 过户费（双向）

# ── K 线读取（按代码缓存整个 bars 序列，供「前收盘 / 涨跌停」使用）────────────
_BARS: dict[str, list[dict]] = {}
_IDX: dict[str, dict[str, int]] = {}


def bars(code: str) -> list[dict]:
    if code not in _BARS:
        p = KLINE_DIR / f"{code}.json"
        rows: list[dict] = []
        if p.exists():
            try:
                rows = [b for b in (json.loads(p.read_text()).get("bars") or [])
                        if b.get("date") and b.get("close")]
            except Exception:                              # noqa: BLE001
                rows = []
        rows.sort(key=lambda b: str(b["date"]))
        _BARS[code] = rows
        _IDX[code] = {str(b["date"])[:10]: i for i, b in enumerate(rows)}
    return _BARS[code]


def bar(code: str, date: str) -> dict | None:
    i = _IDX.get(code, {}).get(date) if code in _IDX else None
    if i is None:
        bars(code)
        i = _IDX.get(code, {}).get(date)
    return None if i is None else _BARS[code][i]


def prev_close(code: str, date: str) -> float | None:
    i = _IDX.get(code, {}).get(date)
    if i is None or i == 0:
        return None
    c = float(_BARS[code][i - 1].get("close") or 0)
    return c if c > 0 else None


def limit_ratio(code: str, name: str | None) -> float:
    """涨跌停比例（近似口径，见文件头「已知近似」）。"""
    n = (name or "").upper()
    if "ST" in n:
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "9")) and not code.startswith(("83", "87")):  # 北交所 4/8/9 段
        return 0.30
    return 0.10


def _fee_buy(amount: float) -> float:
    return max(COMMISSION_MIN, amount * COMMISSION_RATE) + amount * TRANSFER_RATE


def _fee_sell(amount: float) -> float:
    return max(COMMISSION_MIN, amount * COMMISSION_RATE) + amount * (STAMP_RATE + TRANSFER_RATE)


class Account:
    """逐日资金账本。所有价格均为当日真实 OHLC，不补 0、不猜。"""

    def __init__(self, capital: float, *, max_positions: int, use_fill: bool,
                 use_cost: bool, slippage: float, hold_days_cap: int = 5):
        self.cash = capital
        self.start = capital
        self.max_positions = max_positions
        self.use_fill = use_fill
        self.use_cost = use_cost
        self.slip = slippage
        self.hold_cap = hold_days_cap
        self.pos: dict[str, dict] = {}
        self.pending: dict[str, int] = {}      # code → 已顺延天数
        self.nav: list[tuple[str, float]] = []
        self.trades = 0
        self.amt_traded = 0.0
        self.fees = 0.0
        self.buy_blocked = 0
        self.sell_blocked = 0
        self.positions_hist: list[int] = []

    # ── 成交与费用 ────────────────────────────────────────────────
    def _buy(self, code: str, name: str | None, date: str, budget: float) -> bool:
        b = bar(code, date)
        if not b:
            return False
        o = float(b.get("open") or 0)
        if o <= 0 or budget <= 0:
            return False
        if self.use_fill:
            pc = prev_close(code, date)
            if pc:
                up = round(pc * (1 + limit_ratio(code, name)), 2)
                if o >= up - 0.001:                      # 开盘即涨停（含一字）→ 买不到
                    self.buy_blocked += 1
                    return False
            if float(b.get("high") or 0) == float(b.get("low") or 0):   # 一字板
                self.buy_blocked += 1
                return False
        px = o * (1 + self.slip)
        shares = math.floor(budget / px / 100) * 100
        # ⛔ 费用使「刚好买满」的预算超出现金：按手数回退，而不是整只放弃
        #    （整只放弃会让结果对「买不买得起最后一只」过敏，净值路径失真）
        if self.use_cost:
            while shares >= 100:
                amt = shares * px
                if amt + _fee_buy(amt) <= self.cash + 1e-6:
                    break
                shares -= 100
        if shares < 100:
            return False
        gross = shares * px
        fee = _fee_buy(gross) if self.use_cost else 0.0
        if gross + fee > self.cash + 1e-6:
            return False
        self.cash -= gross + fee
        self.fees += fee
        self.amt_traded += gross
        self.trades += 1
        self.pos[code] = {"shares": shares, "px": px, "buy": date, "name": name}
        return True

    def _sell(self, code: str, date: str, when: str) -> bool:
        """when: 'open' | 'close'。返回是否成交。"""
        p = self.pos.get(code)
        b = bar(code, date)
        if not p or not b:
            return False
        raw = float(b.get(when) or 0)
        if raw <= 0:
            return False
        if self.use_fill:
            pc = prev_close(code, date)
            if pc:
                dn = round(pc * (1 - limit_ratio(code, p.get("name"))), 2)
                if raw <= dn + 0.001:                    # 跌停（无论开收）→ 卖不出
                    self.sell_blocked += 1
                    return False
        px = raw * (1 - self.slip)
        gross = p["shares"] * px
        fee = _fee_sell(gross) if self.use_cost else 0.0
        self.cash += gross - fee
        self.fees += fee
        self.amt_traded += gross
        self.trades += 1
        self.pos.pop(code, None)
        self.pending.pop(code, None)
        return True

    def _try_pending(self, date: str, when: str) -> None:
        for code in list(self.pending.keys()):
            if self._sell(code, date, when):
                continue
            self.pending[code] += 1
            if self.pending[code] >= self.hold_cap:      # 顺延上限：强制按当刻价出（记一次「强制」）
                p = self.pos.get(code)
                b = bar(code, date)
                if p and b and float(b.get(when) or 0) > 0:
                    gross = p["shares"] * float(b[when])
                    fee = _fee_sell(gross) if self.use_cost else 0.0
                    self.cash += gross - fee
                    self.fees += fee
                    self.amt_traded += gross
                    self.trades += 1
                    self.pos.pop(code, None)
                    self.pending.pop(code, None)

    def equity(self, date: str) -> float:
        v = self.cash
        for c, p in self.pos.items():
            b = bar(c, date)
            px = float(b.get("close") or 0) if b else p["px"]
            v += p["shares"] * (px if px > 0 else p["px"])
        return v

    def mark(self, date: str) -> None:
        self.nav.append((date, self.equity(date)))
        self.positions_hist.append(len(self.pos))


def simulate(picks_by_day: dict[str, list[dict]], days: list[str], *, sell: str,
             capital: float, max_positions: int, use_fill: bool, use_cost: bool,
             slippage: float) -> dict:
    """逐日推进：开盘先卖（T1O）/ 先买，收盘再卖（T1C）/ 记账。"""
    acct = Account(capital, max_positions=max_positions, use_fill=use_fill,
                   use_cost=use_cost, slippage=slippage)
    days = sorted(days)
    prev: str | None = None
    for d in days:
        # ① 开盘：T1O 到期卖出 + 顺延单（先腾现金，再买）
        if sell == "T1O":
            for c in [c for c, p in acct.pos.items() if p["buy"] == prev]:
                if not acct._sell(c, d, "open"):
                    acct.pending[c] = 1
            if acct.pending:
                acct._try_pending(d, "open")

        # ② 开盘：买当日名单（已有持仓的不重复买，且受总持仓上限约束）
        slots = max(0, max_positions - len(acct.pos))
        codes: list[str] = []
        for r in picks_by_day.get(d, []):
            c = r.get("code")
            if c and c not in acct.pos and c not in codes:
                codes.append(c)
        codes = codes[:slots]
        if codes:
            budget = acct.cash / len(codes)
            for c in codes:
                nm = next((r.get("name") for r in picks_by_day[d] if r.get("code") == c), None)
                acct._buy(c, nm, d, budget)

        # ③ 收盘：T1C 到期卖出 + 顺延单（⛔ 收盘价成交，不得挪到开盘用）
        if sell == "T1C":
            for c in [c for c, p in acct.pos.items() if p["buy"] == prev]:
                if not acct._sell(c, d, "close"):
                    acct.pending[c] = 1
            if acct.pending:
                acct._try_pending(d, "close")

        acct.mark(d)
        prev = d

    # 期末强制清仓（按最后一日收盘）
    last = days[-1]
    for c in list(acct.pos.keys()):
        acct._sell(c, last, "close")
    nav = acct.nav[-1][1] if acct.nav else acct.start
    curve = [v for _, v in acct.nav] + [acct.equity(last)]
    return _stats(curve, acct, nav)


def _stats(curve: list[float], acct: Account, end_equity: float) -> dict:
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0]
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    n = len(rets)
    ann = ((end_equity / acct.start) ** (252 / n) - 1) * 100 if n and end_equity > 0 else 0.0
    return {
        "end_nav": round(end_equity / acct.start, 4),
        "total": round((end_equity / acct.start - 1) * 100, 2),
        "days": n,
        "day_win": round(100 * sum(1 for r in rets if r > 0) / n, 1) if n else 0.0,
        "day_avg": round(st.mean(rets) * 100, 2) if n else 0.0,
        "mdd": round(mdd * 100, 2),
        "annual": round(ann, 2),
        "trades": acct.trades,
        "turnover_x": round(acct.amt_traded / acct.start, 2),
        "fees": round(acct.fees, 2),
        "fee_drag_pct": round(acct.fees / acct.start * 100, 2),
        "buy_blocked": acct.buy_blocked,
        "sell_blocked": acct.sell_blocked,
        "avg_positions": round(st.mean(acct.positions_hist), 2) if acct.positions_hist else 0.0,
    }


def _picks(cfg: str, days: list[str]) -> tuple[dict[str, list[dict]], list[dict]]:
    """复用 research_t1_caliber_ab 的选票逻辑（同一套 CONFIGS / 同一套 stance 处理）。"""
    spec = importlib.util.spec_from_file_location(
        "t1ab", ROOT / "scripts" / "research_t1_caliber_ab.py")
    m = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
    spec.loader.exec_module(m)                          # type: ignore[union-attr]
    rows = m.run(cfg, days)
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    return by_day, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="L4_v11")
    ap.add_argument("--sell", default="T1C", choices=["T1C", "T1O"])
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--max-positions", type=int, default=5)
    ap.add_argument("--slippage", type=float, default=0.001)
    ap.add_argument("--no-fill", action="store_true", help="关闭成交约束（不检查涨跌停）")
    ap.add_argument("--cost-zero", action="store_true", help="关闭费用")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    spec = importlib.util.spec_from_file_location(
        "pcr", ROOT / "scripts" / "pool_call_replay.py")
    R = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
    spec.loader.exec_module(R)                          # type: ignore[union-attr]
    days = R.trading_days()[-a.days:]

    by_day, _rows = _picks(a.config, days)
    print(f"窗口：{days[0]} ~ {days[-1]}（{len(days)} 个交易日）· 配置 {a.config} · 卖出 {a.sell}")
    print(f"资金 {a.capital:,.0f} 元 · 最多 {a.max_positions} 只 · 滑点 {a.slippage * 100:.2f}%"
          f" · 成交约束 {'开' if not a.no_fill else '关'} · 费用 {'开' if not a.cost_zero else '关'}\n")

    # 四段归因：乐观 → 资金 → 成交 → 费用（费用=最终）
    base = dict(sell=a.sell, capital=a.capital, max_positions=a.max_positions, slippage=0.0)
    s1 = simulate(by_day, days, use_fill=False, use_cost=False, **base)
    s2 = simulate(by_day, days, use_fill=not a.no_fill, use_cost=False, **base)
    s3 = simulate(by_day, days, use_fill=not a.no_fill, use_cost=not a.cost_zero,
                  sell=a.sell, capital=a.capital, max_positions=a.max_positions,
                  slippage=a.slippage)

    # 乐观参照：旧 summ（等权日复利 · 无资金/成交/费用约束）
    t1 = importlib.util.spec_from_file_location(
        "t1", ROOT / "scripts" / "research_t1_caliber_ab.py")
    T = importlib.util.module_from_spec(t1)             # type: ignore[arg-type]
    t1.loader.exec_module(T)                            # type: ignore[union-attr]
    opt = T.summ(_rows, "T1C" if a.sell == "T1C" else "T1O")

    hdr = (f"{'阶段':<26}{'总收益':>10}{'回撤':>9}{'日均':>8}{'日胜':>7}"
           f"{'换手':>7}{'均仓':>6}{'费用':>9}{'买不到':>7}{'卖不出':>7}")
    print(hdr)
    print("-" * len(hdr))
    rows_out = [
        ("乐观代理（旧 summ）", {"total": opt.get("compound"), "mdd": opt.get("mdd"),
                              "day_avg": opt.get("day_avg"), "day_win": opt.get("day_win"),
                              "turnover_x": None, "avg_positions": None, "fees": None,
                              "buy_blocked": None, "sell_blocked": None}),
        ("① 加资金约束", s1),
        ("② 加成交约束", s2),
        ("③ 加费用＝可执行", s3),
    ]
    def col(v, fmt, suffix=""):
        return "-".rjust(9) if v is None else f"{fmt.format(v)}{suffix}"

    for label, s in rows_out:
        print(f"{label:<24}"
              f"{col(s.get('total'), '{:>8.2f}', '%'):>10}"
              f"{col(s.get('mdd'), '{:>8.2f}', '%'):>9}"
              f"{col(s.get('day_avg'), '{:>7.2f}', '%'):>8}"
              f"{col(s.get('day_win'), '{:>6.1f}', '%'):>7}"
              f"{col(s.get('turnover_x'), '{:>6.1f}', 'x'):>7}"
              f"{col(s.get('avg_positions'), '{:>6.2f}'):>6}"
              f"{col(s.get('fees'), '{:>9,.0f}'):>9}"
              f"{col(s.get('buy_blocked'), '{:>7d}'):>7}"
              f"{col(s.get('sell_blocked'), '{:>7d}'):>7}")
    print()
    gap = (opt.get("compound") or 0) - s3["total"]
    print(f"乐观 → 可执行 总差距：{gap:+.2f} 个百分点")
    if opt.get("compound") is not None:
        print(f"  其中 资金占用 {s1['total'] - (opt.get('compound') or 0):+.2f}pp"
              f" · 成交约束 {s2['total'] - s1['total']:+.2f}pp"
              f" · 费用+滑点 {s3['total'] - s2['total']:+.2f}pp")

    if a.json:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"window": [days[0], days[-1]], "config": a.config, "sell": a.sell,
                   "capital": a.capital, "max_positions": a.max_positions,
                   "slippage": a.slippage,
                   "optimistic": opt, "capital_only": s1, "with_fill": s2, "executable": s3},
                  open(OUT, "w"), ensure_ascii=False, indent=1)
        print(f"已落盘：{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
