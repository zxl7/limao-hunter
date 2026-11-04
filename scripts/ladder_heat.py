#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""梯次热度：验证「前一天哪个梯次好，今天多做那个梯次」是否成立。

## 由来（2026-11-03 · 用户转述 NIKA 原话）

> 「一进二 才该进去 / 二进三 / 三进四 还要回测」
> 「比如前一天一进二好，多做一进二；前一天二进三全是天地、大面，提示少做」

这是**梯次轮动**的想法：不同晋级阶段的赚钱效应按天轮换，据此决定今天主打哪一档。

## 为什么必须先验证再落地

「昨天的 X 好 → 今天多做 X」这个推论隐含一个前提：**梯次表现存在日间自相关**。
如果相关系数≈0，那它和抛硬币没区别，照做只会增加出手次数 —— 而 §三·C 的回放已经证明
「多抓机会」把日均从 +2.79% 打到 +0.84%。

所以本脚本只做一件事：**算出每个梯次逐日表现，然后算「昨日 → 今日」的秩相关**。
⛔ 不自带推送、不改裁决、不写任何生产文件（只写 `cache/ladder_heat/latest.json`）。

## 口径（与 relay_review_30d 同源，避免又造第三套）

- 样本：`cache/zt_history/zt_*.json`（归档涨停池）× `cache/kline_qfq`（前复权日线）
- 梯次 = 当日 `boards`：1→「1进2」、2→「2进3」、3→「3进4」、≥4→「高位」
- 一日游收益 = **开盘买 → 收盘卖**（可执行价；一字板剔除）
- **可成交口径**：只统计 `0 < gap ≤ 5%`（`USER_ENTRY_MAX_GAP_PCT`）的样本
  —— 与 §三·B 铁律一致：混入高开 >5% 会把「封板质量好」算成亏钱

用法：
    python3 scripts/ladder_heat.py              # 报告：各梯次日表现 + 自相关
    python3 scripts/ladder_heat.py --json       # 机读
    python3 scripts/ladder_heat.py --today      # 只输出「今天主打哪档」建议
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE = ROOT / "cache"
ZT_DIR = CACHE / "zt_history"
KL_DIR = CACHE / "kline_qfq"
OUT_DIR = CACHE / "ladder_heat"
BJ_TZ = timezone(timedelta(hours=8))

ENTRY_MAX_GAP = 5.0          # 与 core/ai_caliber.USER_ENTRY_MAX_GAP_PCT 同值
_LADDER_NAMES = {1: "1进2", 2: "2进3", 3: "3进4"}
ORDER = ["1进2", "2进3", "3进4", "高位"]


def _tier_of(boards) -> str | None:
    try:
        b = int(boards)
    except (TypeError, ValueError):
        return None
    if b < 1:
        return None
    return _LADDER_NAMES.get(b, "高位")


def _load_days():
    """[(date, {code: row})]，按日期升序。"""
    days = []
    for f in sorted(ZT_DIR.glob("zt_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        date = str(d.get("date") or "")[:10]
        if not date:
            continue
        pool = {}
        for r in d.get("zt_list") or []:
            code = str(r.get("code") or "").zfill(6)
            if code and code != "000000":
                pool[code] = r
        if pool:
            days.append((date, pool))
    days.sort()
    return days


_KL: dict[str, dict[str, dict]] = {}


def _kline(code: str) -> dict[str, dict]:
    if code in _KL:
        return _KL[code]
    out: dict[str, dict] = {}
    try:
        d = json.loads((KL_DIR / f"{code}.json").read_text(encoding="utf-8"))
        for b in d.get("bars") or []:
            k = str(b.get("date") or "")[:10]
            if k:
                out[k] = b
    except Exception:
        pass
    _KL[code] = out
    return out


def _day_stat(code_pool: dict, trade_date: str, tier: str) -> dict:
    """某梯次在某个交易日的表现（可成交口径）。"""
    n_total = 0            # 该梯次今天有多少只（= 昨天的候选）
    n_fill = 0             # 今天可成交（0<gap≤5，非一字）
    rets: list[float] = []
    promoted = 0           # 今天是否还涨停（晋级）
    for code, row in code_pool.items():
        if _tier_of(row.get("boards")) != tier:
            continue
        n_total += 1
        b = _kline(code).get(trade_date)
        if not b:
            continue
        try:
            o, c, hi, lo = float(b["open"]), float(b["close"]), float(b["high"]), float(b["low"])
        except Exception:
            continue
        if o <= 0:
            continue
        prev = row.get("price") or row.get("close")
        gap = None
        if prev:
            try:
                gap = (o - float(prev)) / float(prev) * 100.0
            except Exception:
                gap = None
        # 晋级 = 今日收盘涨停（近似：涨幅≥9.8% 或 收盘=最高且涨幅超阈值）
        try:
            chg = (c - float(prev)) / float(prev) * 100.0 if prev else None
        except Exception:
            chg = None
        if chg is not None and chg >= (19.5 if row.get("is_20cm") else 9.8):
            promoted += 1
        if hi == lo:                       # 一字板：买不到
            continue
        if gap is None or not (0 < gap <= ENTRY_MAX_GAP):
            continue
        n_fill += 1
        rets.append((c - o) / o * 100.0)
    avg = round(sum(rets) / len(rets), 3) if rets else None
    win = round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1) if rets else None
    return {
        "tier": tier, "date": trade_date, "n_total": n_total, "n_fill": n_fill,
        "avg_ret": avg, "win_rate": win,
        "promo_rate": round(promoted / n_total * 100, 1) if n_total else None,
    }


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """秩相关（不用 scipy）。样本 <5 返回 None。"""
    n = len(xs)
    if n < 5:
        return None

    def rank(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 3)


def build() -> dict:
    days = _load_days()
    if not days:
        return {"ok": False, "reason": "无 zt_history 归档"}
    # 每个交易日：用**前一日**的池子（那才是「昨天涨停、今天要晋级」的票）去算今天表现
    series: list[dict] = []
    for i in range(1, len(days)):
        prev_date, prev_pool = days[i - 1]
        cur_date, _ = days[i]
        for tier in ORDER:
            st = _day_stat(prev_pool, cur_date, tier)
            if st["n_total"]:
                series.append(st)

    # 各梯次汇总 + 自相关
    by_tier: dict[str, dict] = {}
    for tier in ORDER:
        rows = [s for s in series if s["tier"] == tier]
        filled = [s for s in rows if s["n_fill"]]
        all_rets_n = sum(s["n_fill"] for s in filled)
        by_tier[tier] = {
            "days": len(rows),
            "avg_ret_days": round(sum(s["avg_ret"] for s in filled) / len(filled), 3) if filled else None,
            "win_rate_days": round(sum(s["win_rate"] for s in filled) / len(filled), 1) if filled else None,
            "promo_rate": round(sum(s["promo_rate"] or 0 for s in rows) / len(rows), 1) if rows else None,
            "n_fill_total": all_rets_n,
        }

    # 「昨天该梯次表现 → 今天同梯次表现」的秩相关（核心验证）
    autocorr: dict[str, dict] = {}
    for tier in ORDER:
        rows = [s for s in series if s["tier"] == tier and s["avg_ret"] is not None]
        xs = [s["avg_ret"] for s in rows[:-1]]
        ys = [s["avg_ret"] for s in rows[1:]]
        # 必须严格按日配对（相邻两个交易日）
        pairs = []
        for a, b in zip(rows, rows[1:]):
            pairs.append((a["avg_ret"], b["avg_ret"]))
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        autocorr[tier] = {"n": len(pairs), "spearman": _spearman(xs, ys)}
    return {"ok": True, "days": len(days), "series": series,
            "by_tier": by_tier, "autocorr": autocorr}


def latest_suggestion(payload: dict) -> dict:
    """给出「今天主打哪档」的建议。

    ⛔ 判定必须**同时**满足三件事，只看自相关会得出荒谬结论：
      ① 自相关显著（ρ > 0.15 且 n ≥ 10）—— 「昨天好」才能预示「今天好」
      ② 该档**本身**日均 > 0 —— 否则跟着做也是亏（2进3 ρ=0.19 但日均 −1.48%）
      ③ 可成交样本 ≥ 30 —— n<30 的 +2.46%/75% 是噪声，不是结论
    三条缺一，就不能按「昨日排名」做轮动。
    """
    ac = payload.get("autocorr") or {}
    bt = payload.get("by_tier") or {}
    usable, rejected = [], []
    for t in ORDER:
        rho = ac.get(t, {}).get("spearman")
        n_ac = ac.get(t, {}).get("n") or 0
        avg = (bt.get(t) or {}).get("avg_ret_days")
        fills = (bt.get(t) or {}).get("n_fill_total") or 0
        why = []
        if rho is None or rho <= 0.15:
            why.append(f"自相关不足(ρ={rho})")
        if n_ac < 10:
            why.append(f"日配对仅{n_ac}")
        if avg is None or avg <= 0:
            why.append(f"该档日均{(avg if avg is not None else 0):.2f}%")
        if fills < 30:
            why.append(f"可成交仅{fills}")
        (usable if not why else rejected).append({"tier": t, "why": why})
    series = payload.get("series") or []
    if not series:
        return {"ok": False, "reason": "无序列"}
    last = max(s["date"] for s in series)
    yday = {s["tier"]: s for s in series if s["date"] == last}
    ranked = sorted((s for s in yday.values() if s["avg_ret"] is not None),
                    key=lambda s: -(s["avg_ret"] or -999))
    return {
        "ok": True,
        "as_of": last,
        "yesterday": {t: {"avg_ret": v.get("avg_ret"), "win_rate": v.get("win_rate"),
                          "promo_rate": v.get("promo_rate"), "n_fill": v.get("n_fill")}
                      for t, v in yday.items()},
        "ranked": [s["tier"] for s in ranked],
        "rotatable": [u["tier"] for u in usable],
        "not_rotatable": rejected,
        "verdict": ("可按昨日排名轮动：" + "、".join(u["tier"] for u in usable)) if usable
                   else "⛔ 不做梯次轮动 —— 没有一档同时满足「昨日可预示今日 ∧ 本身赚钱 ∧ 样本足够」",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--today", action="store_true")
    args = ap.parse_args()

    payload = build()
    if not payload.get("ok"):
        print(payload.get("reason"))
        return 1
    payload["ts"] = datetime.now(BJ_TZ).isoformat()
    payload["suggestion"] = latest_suggestion(payload)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    sug = payload["suggestion"]
    if args.today:
        print(f"基准日 {sug['as_of']}  归档 {payload['days']} 天")
        for t in ORDER:
            v = sug["yesterday"].get(t) or {}
            if v:
                print(f"  {t:5s} 均{v['avg_ret']}% 胜{v['win_rate']}% 晋级{v['promo_rate']}%"
                      f"（可成交 {v['n_fill']}）")
        print(f"\n昨日排名: {' > '.join(sug['ranked'])}")
        print(f"结论: {sug['verdict']}")
        return 0

    print(f"══ 梯次热度（归档 {payload['days']} 天）\n")
    print(f"{'梯次':6s} {'天数':>4s} {'可成交':>5s} {'日均':>7s} {'日胜率':>7s} {'晋级率':>7s}")
    for t in ORDER:
        v = payload["by_tier"][t]
        print(f"{t:6s} {v['days']:>4d} {v['n_fill_total']:>5d} "
              f"{(v['avg_ret_days'] if v['avg_ret_days'] is not None else 0):>7.2f} "
              f"{(v['win_rate_days'] if v['win_rate_days'] is not None else 0):>7.1f} "
              f"{(v['promo_rate'] if v['promo_rate'] is not None else 0):>7.1f}")
    print(f"\n══ 日间自相关（昨日表现 → 今日表现·Spearman）")
    for t in ORDER:
        a = payload["autocorr"][t]
        s = a["spearman"]
        tag = "—" if s is None else ("正相关" if s > 0.15 else ("负相关" if s < -0.15 else "≈无关"))
        print(f"  {t:6s} n={a['n']:>3d}  ρ={('%.3f' % s) if s is not None else 'None':>6s}  {tag}")
    print(f"\n昨日排名: {' > '.join(sug['ranked'])}")
    print("不可按昨日排名轮动的档（逐项列原因）:")
    for r in sug["not_rotatable"]:
        print(f"  {r['tier']:6s} {' / '.join(r['why'])}")
    print(f"\n结论: {sug['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
