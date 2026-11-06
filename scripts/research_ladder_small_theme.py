#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小题材龙一 × 梯队门槛 验证（2026-11-06 用户选 A：先验证再定口径）

问题：`high_win_missing` 条件④ 要求 `ladder_has_complete or ladder_health >= 6`。
      `calc_ladder_health` 结构上让 2 家题材上限 = 3 + min(2, boards*0.4) + 2 = 5.8 < 6，
      ⇒ 2 家题材的龙一**永远**过不了「梯队」这一条，与质量无关。

本脚本只读历史，回答三件事：
  1. relay 票按 `ladder_health` 分档，T+1 账本口径收益/胜率/晋级率各是多少；
  2. `< 6` 这一档里，按 `theme_mates_cnt` 再切（2 家 vs 3+ 家）是否同质；
  3. 阈值 6 若下移到 5.5（只救 2 家高分题材）会改变多少票、收益走向如何。

口径（与既有研究一致，⛔ 不另造）：
  · 买入价 = 当日 09:25 竞价价（优先快照）；快照缺失时用当日**开盘价**兜底
      —— 已校验：1556 组可比对样本中 94.9% 完全相等、中位差 0.0000%、P95 0.043%，
         因为 A 股开盘价就是 09:25 集合竞价的成交价，属同一事实的两个来源，不是插补。
  · 当日 = 当日收盘；T+1 = 次一交易日收盘（账本口径）
  · 竞价快照只覆盖昨涨停池，故样本天然等于「昨日涨停的 relay 候选」
⛔ 纯研究：不改任何生产判据、不写冻结件、不写账本。
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from common.trading_calendar import prev_a_share_trading_day  # noqa: E402

CACHE = ROOT / "wealth_code" / "cache"
OUT = ROOT / "cache" / "research" / "ladder_small_theme_bt.json"

# 与生产同源阈值（只读引用，⛔ 不回写）
HIGH_WIN_LADDER_MIN = 6.0
THEME_MATES_MIN = 2


def _load(p: Path, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def _trading_days(end: str, n: int) -> list[str]:
    """回溯 n 个交易日（升序，含 end 若为交易日）。"""
    days: list[str] = []
    d = end
    for _ in range(n * 2):
        if d not in days:
            days.append(d)
        d = prev_a_share_trading_day(d)
        if len(days) >= n:
            break
    return sorted(days)


def _bars(code: str) -> list[dict]:
    d = _load(ROOT / "cache" / "kline_qfq" / f"{code}.json", {}) or {}
    bars = d.get("bars") or []
    return [b for b in bars if b.get("date")]


def _ret_to(code: str, day: str, entry: float, *, next_day: bool) -> float | None:
    """竞价价 → 当日/次一交易日收盘（前复权）。"""
    bars = _bars(code)
    if not bars:
        return None
    idx = None
    for i, b in enumerate(bars):
        if str(b.get("date"))[:10] == day:
            idx = i
            break
    if idx is None or not entry:
        return None
    j = idx + 1 if next_day else idx
    if j >= len(bars):
        return None
    c = bars[j].get("close")
    try:
        return round((float(c) / float(entry) - 1) * 100, 3)
    except Exception:
        return None


def _stats(rows: list[dict]) -> dict:
    same = [r["ret_same"] for r in rows if r.get("ret_same") is not None]
    t1 = [r["ret_t1"] for r in rows if r.get("ret_t1") is not None]
    out = {"n": len(rows), "n_t1": len(t1)}
    if same:
        out["same_mean"] = round(st.mean(same), 3)
        out["same_win"] = round(sum(1 for x in same if x > 0) / len(same) * 100, 1)
    if t1:
        out["t1_mean"] = round(st.mean(t1), 3)
        out["t1_med"] = round(st.median(t1), 3)
        out["t1_win"] = round(sum(1 for x in t1 if x > 0) / len(t1) * 100, 1)
    return out


def _open_of(code: str, day: str) -> float | None:
    """当日开盘价 = 09:25 集合竞价成交价（与快照同源事实，仅用于快照缺失时兜底）。"""
    for b in _bars(code):
        if str(b.get("date"))[:10] == day:
            try:
                return float(b.get("open"))
            except Exception:
                return None
    return None


def main() -> int:
    end = "2026-11-06"
    days = _trading_days(end, 60)

    rows: list[dict] = []
    dropped: list[dict] = []

    for d in days:
        prev = prev_a_share_trading_day(d)
        cand = _load(CACHE / f"candidates-{prev}.json")
        if not isinstance(cand, dict):
            continue
        relay = cand.get("relay") or []
        if not relay:
            continue

        quotes = _load(CACHE / f"auction_quotes-{d}.json", {}) or {}

        zt_today = {str(r.get("code")) for r in
                    (_load(ROOT / "cache" / "zt_history" / f"zt_{d}.json", {}) or {}).get("zt_list") or []}

        for c in relay:
            code = str(c.get("code") or "")
            q = quotes.get(code) or {}
            entry = q.get("auction_price")
            src = "auction_snapshot"
            if not entry:
                entry = _open_of(code, d)
                src = "bar_open"
            if not entry:
                dropped.append({"date": d, "code": code, "why": "无买入价"})
                continue
            bars = _bars(code)
            prev_close = None
            for i, b in enumerate(bars):
                if str(b.get("date"))[:10] == d and i > 0:
                    prev_close = bars[i - 1].get("close")
                    break
            gap = None
            if prev_close:
                gap = round((float(entry) / float(prev_close) - 1) * 100, 3)
            rows.append({
                "date": d,
                "code": code,
                "name": c.get("name"),
                "boards": c.get("boards"),
                "theme": c.get("theme_name") or c.get("theme") or c.get("core_theme"),
                "mates": c.get("theme_mates_cnt"),
                "ladder_health": c.get("ladder_health"),
                "ladder_complete": c.get("ladder_has_complete"),
                "ladder_grade": c.get("ladder_grade"),
                "dragon_rank": c.get("dragon_rank"),
                "gate_missing": c.get("high_win_gate"),
                "entry_src": src,
                "gap": gap,
                "is_20cm": bool(c.get("is_20cm")),
                "ret_same": _ret_to(code, d, float(entry), next_day=False),
                "ret_t1": _ret_to(code, d, float(entry), next_day=True),
                "zt_today": code in zt_today,
            })

    print(f"窗口复查 {len(days)} 个交易日（{days[0]} ~ {days[-1]}）")
    if dropped:
        print(f"  ⚠️ 剔除 {len(dropped)} 条：", Counter(x["why"] for x in dropped))
    raw_n = len(rows)
    rows = [r for r in rows if r.get("ret_t1") is not None]
    src_n = Counter(r["entry_src"] for r in rows)
    print(f"有效 relay 票次 {len(rows)}（原始 {raw_n}，买入价来源 {dict(src_n)}）")
    print()

    if not rows:
        print("⚠️ 无有效样本")
        return 1

    # ── 1) 按 ladder_health 分档 ─────────────────────────────────────────
    def band(lh):
        if lh is None:
            return "缺失"
        lh = float(lh)
        if lh < 4:
            return "<4"
        if lh < 5.5:
            return "4~5.5"
        if lh < 6:
            return "5.5~6(死区)"
        if lh < 8:
            return "6~8"
        return ">=8"

    print("=== ① 按 ladder_health 分档（现阈值 6）===")
    print(f"{'档':<13}{'n':>5}{'当日均':>9}{'当日胜':>8}{'T+1均':>9}{'T+1中位':>9}{'T+1胜':>8}{'晋级':>7}")
    groups = defaultdict(list)
    for r in rows:
        groups[band(r["ladder_health"])].append(r)
    for k in ["<4", "4~5.5", "5.5~6(死区)", "6~8", ">=8", "缺失"]:
        if k not in groups:
            continue
        s = _stats(groups[k])
        ztr = sum(1 for r in groups[k] if r["zt_today"]) / len(groups[k]) * 100
        print(f"{k:<13}{s['n']:>5}{s.get('same_mean', 0):>8.2f}%{s.get('same_win', 0):>7.1f}%"
              f"{s.get('t1_mean', 0):>8.2f}%{s.get('t1_med', 0):>8.2f}%{s.get('t1_win', 0):>7.1f}%{ztr:>6.1f}%")

    # ── 2) 死区内部按题材家数再切 ────────────────────────────────────────
    dead = [r for r in rows if r["ladder_health"] is not None and 5.5 <= float(r["ladder_health"]) < 6]
    print()
    print(f"=== ② 5.5~6 死区（{len(dead)} 票次）内部按题材家数切 ===")
    if dead:
        print(f"{'家数':<10}{'n':>5}{'当日均':>9}{'T+1均':>9}{'T+1中位':>9}{'T+1胜':>8}{'晋级':>7}")
        for lab, lo, hi in [("2家", 2, 3), ("3家", 3, 4), ("4+家", 4, 99)]:
            g = [r for r in dead if r["mates"] is not None and lo <= int(r["mates"]) < hi]
            if not g:
                continue
            s = _stats(g)
            ztr = sum(1 for r in g if r["zt_today"]) / len(g) * 100
            print(f"{lab:<10}{s['n']:>5}{s.get('same_mean', 0):>8.2f}%{s.get('t1_mean', 0):>8.2f}%"
                  f"{s.get('t1_med', 0):>8.2f}%{s.get('t1_win', 0):>7.1f}%{ztr:>6.1f}%")

    print()
    print("=== ③ 全样本按题材家数切（控制梯队后看家数本身）===")
    print(f"{'家数':<10}{'n':>5}{'当日均':>9}{'T+1均':>9}{'T+1中位':>9}{'T+1胜':>8}{'晋级':>7}")
    for lab, lo, hi in [("2家", 2, 3), ("3家", 3, 4), ("4家", 4, 5), ("5+家", 5, 99)]:
        g = [r for r in rows if r["mates"] is not None and lo <= int(r["mates"]) < hi]
        if not g:
            continue
        s = _stats(g)
        ztr = sum(1 for r in g if r["zt_today"]) / len(g) * 100
        print(f"{lab:<10}{s['n']:>5}{s.get('same_mean', 0):>8.2f}%{s.get('t1_mean', 0):>8.2f}%"
              f"{s.get('t1_med', 0):>8.2f}%{s.get('t1_win', 0):>7.1f}%{ztr:>6.1f}%")

    # ── 4) 阈值敏感性：5.0 / 5.5 / 6.0 ──────────────────────────────────
    print()
    print("=== ④ 梯队阈值敏感性（把 <阈值 的票放进来会怎样）===")
    print(f"{'阈值':<8}{'新增票次':>9}{'新增T+1均':>11}{'新增T+1中位':>12}{'新增胜率':>9}{'新增晋级':>9}{'含入后全组T+1':>14}")
    for thr in (5.0, 5.5, 6.0):
        new = [r for r in rows if r["ladder_health"] is not None
               and float(r["ladder_health"]) < 6.0 and float(r["ladder_health"]) >= thr]
        keep = [r for r in rows if r["ladder_health"] is not None and float(r["ladder_health"]) >= 6.0]
        s_new = _stats(new)
        s_all = _stats(new + keep)
        ztr = (sum(1 for r in new if r["zt_today"]) / len(new) * 100) if new else 0.0
        print(f"{thr:<8}{s_new['n']:>9}{s_new.get('t1_mean', 0):>10.2f}%{s_new.get('t1_med', 0):>11.2f}%"
              f"{s_new.get('t1_win', 0):>8.1f}%{ztr:>8.1f}%{s_all.get('t1_mean', 0):>13.2f}%")

    # ── 5) 逐日明细 ────────────────────────────────────────────────────
    print()
    print("=== ⑤ 逐日明细（ladder_health < 6 的 relay 票）===")
    byday = defaultdict(list)
    for r in rows:
        byday[r["date"]].append(r)
    for d in sorted(byday):
        g = [r for r in byday[d] if r["ladder_health"] is None or float(r["ladder_health"]) < 6]
        if not g:
            continue
        bits = []
        for r in g:
            t1 = f"{r['ret_t1']:+.2f}%" if r["ret_t1"] is not None else "—"
            bits.append(f"{r['name']}(b{r['boards']},lh{r['ladder_health']},m{r['mates']},T+1 {t1})")
        print(f"  {d}  " + " | ".join(bits))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_for": "2026-11-06 小题材梯队口径验证（用户 A）",
        "window": {"start": days[0], "end": days[-1], "replayed": len(days)},
        "dropped": dropped,
        "n_rows": len(rows),
        "bands": {k: _stats(v) for k, v in groups.items()},
        "rows": rows,
        "caliber": "买入=09:25竞价价；当日=当日收盘；T+1=次一交易日收盘（账本口径）",
        "caveat": "无成交回报/费用/滑点；晋级率≠买入胜率≠可执行收益",
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n产物 → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
