#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封单趋势：验证「封单比昨天还大」到底是好信号还是坏信号。

## 由来（2026-11-03 · 用户转述 NIKA 对青岛食品的质疑）

> 「这个不可能开一个点的」「这么大热度加封单」「**封单比昨天还大**」

事实核验（`cache/zt_history`）：青岛食品 001219

| 日期 | 板 | 封单额 | 流通市值 | 封单/流通 |
|---|---|---|---|---|
| 10-30 | 5 | 0.57 亿 | 37.93 亿 | 1.50% |
| 11-02 | 6 | **0.72 亿** | 41.71 亿 | **1.73%** |

→ **NIKA 说对了：封单确实比昨天大**（额 +26%，占比 +0.23pp）。
   但 1.73% 仍低于 `DIST_SEAL_RATIO_MAX=2%`，故被 `thin_seal_top` 判「最高板+薄封单」→ watch。

## 那么「封单变大」该不该救它？—— 用数据回答

口径与 `relay_review_30d.py` 完全一致，避免又造第四套：
- 样本：`cache/zt_history` × `cache/kline_qfq`，只取**前一日 ≥2 板**的票
- 收益：**次日开盘买 → 次日收盘卖**（一日游·可执行）
- 可成交：`0 < gap ≤ 5%` ∧ 非一字（`high != low`）
- `seal` = 封单额（亿元），`seal/lt_yi` = 封单占流通 %（⛔ `seal` 不是百分比）

## 实测结论（2026-11-03·78 个可成交样本）

| 分组 | n | 均 | 中位 | 胜率 |
|---|---|---|---|---|
| 全部 ≥2板 | 78 | −0.77% | −1.49% | 44.9% |
| **封单额缩小 >20%** | 28 | **+0.71%** | +2.17% | **53.6%** |
| 封单额 ±20% 内 | 12 | −1.48% | −0.62% | 50.0% |
| **封单额放大 >20%** | 37 | **−1.65%** | −3.29% | **37.8%** |
| 薄<2% ∧ 占比变厚 | 15 | **−2.19%** | −4.03% | 40.0% |

**封单变大反而更差**，与用户既有心法「连板缩量 = 锁筹」（地量→首板放量→连板缩量）
方向一致：封单越堆越厚，说明是**越堆越多人排队接货**，而不是没人愿意卖。

⛔ 本脚本只输出事实，不改任何生产闸门。要改 `thin_seal_top` / `DIST_SEAL_RATIO_MAX`，
   必须先看这里的分组，并说明改的是哪一档。

用法：
    python3 scripts/seal_trend.py            # 报告
    python3 scripts/seal_trend.py --json
    python3 scripts/seal_trend.py --code 001219   # 单票封单轨迹
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics as st
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
ZT_DIR = CACHE / "zt_history"
KL_DIR = CACHE / "kline_qfq"
OUT_DIR = CACHE / "seal_trend"
BJ_TZ = timezone(timedelta(hours=8))

ENTRY_MAX_GAP = 5.0          # 与 core/ai_caliber.USER_ENTRY_MAX_GAP_PCT 同值
MIN_BOARDS = 2               # 本岗只看 ≥2 板
THICK_THRESHOLD = 2.0        # 与 defense_gate.DIST_SEAL_RATIO_MAX 同值


# ── 数据 ────────────────────────────────────────────────────────────────
_KL: dict[str, dict] = {}


def _bars(code: str) -> dict[str, dict]:
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


def load_days() -> list[tuple[str, dict[str, dict]]]:
    days = []
    for f in sorted(glob.glob(str(ZT_DIR / "zt_*.json"))):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        date = str(d.get("date") or "")[:10]
        if not date:
            continue
        pool = {str(r.get("code") or "").zfill(6): r for r in d.get("zt_list") or []
                if r.get("code")}
        if pool:
            days.append((date, pool))
    days.sort()
    return days


def _f(row: dict, key: str) -> float | None:
    try:
        v = float(row.get(key))
        return v
    except (TypeError, ValueError):
        return None


def seal_ratio_pct(row: dict) -> float | None:
    """封单占流通 %。⛔ `seal` 是**封单额（亿元）**，不是百分比。"""
    sa, lt = _f(row, "seal"), _f(row, "lt_yi")
    if sa is None or lt is None or lt <= 0:
        return None
    return sa / lt * 100.0


def build() -> dict:
    days = load_days()
    recs: list[dict] = []
    for i in range(len(days) - 1):
        D, pool = days[i]
        N = days[i + 1][0]
        prev_pool = days[i - 1][1] if i > 0 else {}
        for code, r in pool.items():
            boards = _f(r, "boards")
            if boards is None or int(boards) < MIN_BOARDS:
                continue
            b = _bars(code).get(N)
            if not b:
                continue
            try:
                o, c = float(b["open"]), float(b["close"])
                hi, lo = float(b["high"]), float(b["low"])
            except Exception:
                continue
            pc = _f(r, "price")
            if not o or not pc:
                continue
            gap = (o - pc) / pc * 100.0
            if not (0 < gap <= ENTRY_MAX_GAP) or hi == lo:
                continue                     # 买不到 / 一字
            prev = prev_pool.get(code) or {}
            sa, pa = _f(r, "seal"), _f(prev, "seal")
            cur_sr, prev_sr = seal_ratio_pct(r), seal_ratio_pct(prev)
            recs.append({
                "board_date": D, "trade_date": N, "code": code,
                "name": str(r.get("name") or ""), "boards": int(boards),
                "seal_yi": sa, "prev_seal_yi": pa,
                "amt_chg_pct": round((sa / pa - 1) * 100, 1) if (sa and pa and pa > 0) else None,
                "seal_ratio_pct": round(cur_sr, 2) if cur_sr is not None else None,
                "ratio_chg_pp": round(cur_sr - prev_sr, 2)
                if (cur_sr is not None and prev_sr is not None) else None,
                "gap_pct": round(gap, 2),
                "ret_pct": round((c - o) / o * 100, 2),
            })
    return {"samples": recs, "days": len(days)}


def _stat(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "avg": None, "median": None, "win": None}
    return {"n": len(xs), "avg": round(st.mean(xs), 2),
            "median": round(st.median(xs), 2),
            "win": round(sum(1 for v in xs if v > 0) / len(xs) * 100, 1)}


def group_stats(recs: list[dict]) -> dict:
    def g(sel):
        return _stat([r["ret_pct"] for r in recs if sel(r)])

    return {
        "all": _stat([r["ret_pct"] for r in recs]),
        # 封单额变化（NIKA 的原话口径）
        "amt_shrink": g(lambda r: r["amt_chg_pct"] is not None and r["amt_chg_pct"] < -20),
        "amt_flat": g(lambda r: r["amt_chg_pct"] is not None and abs(r["amt_chg_pct"]) <= 20),
        "amt_grow": g(lambda r: r["amt_chg_pct"] is not None and r["amt_chg_pct"] > 20),
        "amt_grow_big": g(lambda r: r["amt_chg_pct"] is not None and r["amt_chg_pct"] > 50),
        # 占比绝对值
        "thin_lt1": g(lambda r: r["seal_ratio_pct"] is not None and r["seal_ratio_pct"] < 1),
        "mid_1_2": g(lambda r: r["seal_ratio_pct"] is not None and 1 <= r["seal_ratio_pct"] < 2),
        "thick_2_5": g(lambda r: r["seal_ratio_pct"] is not None and 2 <= r["seal_ratio_pct"] < 5),
        "thick_ge5": g(lambda r: r["seal_ratio_pct"] is not None and r["seal_ratio_pct"] >= 5),
        # 交叉：青岛食品那一类（薄但变厚）
        "thin_growing": g(lambda r: r["seal_ratio_pct"] is not None and r["seal_ratio_pct"] < THICK_THRESHOLD
                          and r["ratio_chg_pp"] is not None and r["ratio_chg_pp"] > 0.3),
        "thin_shrinking": g(lambda r: r["seal_ratio_pct"] is not None and r["seal_ratio_pct"] < THICK_THRESHOLD
                            and (r["ratio_chg_pp"] is None or r["ratio_chg_pp"] <= 0.3)),
    }


def trace(code: str) -> list[dict]:
    """单票封单轨迹（逐涨停日：板数/封单额/流通/占比）。"""
    out = []
    for date, pool in load_days():
        r = pool.get(code)
        if not r:
            continue
        sr = seal_ratio_pct(r)
        out.append({"date": date, "boards": int(_f(r, "boards") or 0),
                    "seal_yi": _f(r, "seal"), "lt_yi": _f(r, "lt_yi"),
                    "seal_ratio_pct": round(sr, 2) if sr is not None else None,
                    "hs": _f(r, "hs"), "zbc": _f(r, "zbc")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--code", help="单票封单轨迹")
    args = ap.parse_args()

    if args.code:
        rows = trace(args.code.zfill(6))
        if not rows:
            print(f"{args.code} 无涨停记录")
            return 1
        print(f"{'日期':12s} {'板':>2s} {'封单亿':>7s} {'流通亿':>8s} {'占比%':>6s} {'换手%':>6s} {'炸板':>3s}")
        for r in rows:
            print(f"{r['date']:12s} {r['boards']:>2d} "
                  f"{(r['seal_yi'] or 0):>7.2f} {(r['lt_yi'] or 0):>8.2f} "
                  f"{(r['seal_ratio_pct'] or 0):>6.2f} {(r['hs'] or 0):>6.2f} {int(r['zbc'] or 0):>3d}")
        return 0

    raw = build()
    stats = group_stats(raw["samples"])
    payload = {"ts": datetime.now(BJ_TZ).isoformat(), "days": raw["days"],
               "n_samples": len(raw["samples"]), "stats": stats,
               "samples": raw["samples"]}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    def line(label: str, k: str) -> None:
        v = stats[k]
        if not v["n"]:
            print(f"  {label:22s} n=  0")
            return
        print(f"  {label:22s} n={v['n']:>3d} 均{v['avg']:+6.2f}% 中位{v['median']:+6.2f}% 胜{v['win']:5.1f}%")

    print(f"══ 封单趋势（{raw['days']} 归档日 · {len(raw['samples'])} 个可成交样本）\n")
    line("全部 ≥2板", "all")
    print("\n按【封单额】环比（NIKA 说「比昨天还大」）：")
    line("缩小 >20%", "amt_shrink")
    line("±20% 内", "amt_flat")
    line("放大 >20%", "amt_grow")
    line("放大 >50%", "amt_grow_big")
    print("\n按【封单/流通】绝对值：")
    line("薄 <1%", "thin_lt1")
    line("中 1~2%", "mid_1_2")
    line("厚 2~5%", "thick_2_5")
    line("很厚 ≥5%", "thick_ge5")
    print(f"\n交叉（阈值 {THICK_THRESHOLD:g}%，青岛食品属左栏）：")
    line("薄 ∧ 占比变厚", "thin_growing")
    line("薄 ∧ 不变/变薄", "thin_shrinking")
    print("\n⛔ 只读结论：封单变大不是买入理由（数据方向相反）；本脚本不改任何生产闸门。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
