#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境防守腿 · 三条阈值的样本验证（2026-11-06 用户「阈值可以降低门槛」）。

问题：`core/defense_gate.env_verdict` 用三条开盘/昨收读数判「弱」，命中 ≥3 条即当日不买：
  · ENV_PROMOTE_MIN     = 25.0  ← 昨收晋级率 < 25% 算弱
  · ENV_RED_OPEN_MIN    = 0.40  ← 今开昨涨停红开比例 ≤ 40% 算弱
  · ENV_LEADER_RED_OPEN_MIN = 0.35 ← 今开昨连板红开比例 ≤ 35% 算弱
今天(11-06)三条全中 → level=block → effective_quota=0，全池空仓。
用户明确「阈值可以降低门槛」。**但降多少不能拍脑袋**，本脚本用样本投票。

口径（严格复现 09:25 时点可得信息）：
  · promote_rate(T)      = |zt(T-1) ∩ zt(T-2)| / |zt(T-2)|      ← T-1 盘后归档可算
  · red_open_ratio(T)    = zt(T-1) 池中 T 日竞价 gap>0 的比例    ← 09:25 实况
  · leader_red_ratio(T)  = 其中 boards>=2 的子集 gap>0 的比例
标的收益（账本口径）：
  · 买入 = T 日开盘价（≈09:25 竞价价，1556 组比对 94.9% 完全相等）
  · 卖出 = T+1 日收盘价
  · 只算「当日可成交」＝竞价未封死（gap < 涨停限制 − 0.2pct）
  · 为对齐真实入场纪律，另给一档「红开可买 0<gap≤5」的子集

输出：每条读数的分档收益表 + 阈值扫描（每个候选阈值下 保留批 vs 剔除批 的差）。
⛔ 只读研究，不写生产文件、不改任何开关。
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

WC = ROOT / "wealth_code"
ZT = ROOT / "cache" / "zt_history"
KLINE = ROOT / "cache" / "kline_qfq"
OUT = ROOT / "cache" / "research" / "defense_env_bt.json"


def _jl(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _zt_rows(day: str) -> list[dict]:
    d = _jl(ZT / f"zt_{day}.json") or {}
    return [r for r in (d.get("zt_list") or []) if isinstance(r, dict)]


def _boards(r: dict) -> int:
    for k in ("boards", "lbc", "lianban"):
        v = r.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return 0


def _codes(rows: list[dict]) -> set[str]:
    return {str(r.get("code") or "").strip() for r in rows} - {""}


_BARS: dict[str, dict[str, dict]] = {}


def _bars(code: str) -> dict[str, dict]:
    if code in _BARS:
        return _BARS[code]
    d = _jl(KLINE / f"{code}.json") or {}
    out = {str(b.get("date"))[:10]: b for b in (d.get("bars") or []) if b.get("date")}
    _BARS[code] = out
    return out


def _gap(q: dict) -> float | None:
    """竞价快照 → 开幅%。逐字对齐 `core.market_open._gap`：
    有 gap_pct 直接用，否则 auction_price / prev_close − 1。"""
    for k in ("gap_pct", "open_gap", "gap"):
        v = q.get(k)
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == f:
            return f
    try:
        p, pc = float(q.get("auction_price")), float(q.get("prev_close"))
    except (TypeError, ValueError):
        return None
    if not pc or pc <= 0 or p != p:
        return None
    return (p / pc - 1) * 100.0


def trading_days() -> list[str]:
    """有涨停归档、且当期非空的交易日（按文件名排序）。"""
    days = []
    for f in sorted(ZT.glob("zt_*.json")):
        day = f.stem[3:]
        if len(day) != 10:
            continue
        if _zt_rows(day):
            days.append(day)
    return days


def build() -> dict[str, Any]:
    days = trading_days()
    idx = {d: i for i, d in enumerate(days)}
    quotes_by_day: dict[str, dict] = {}
    for d in days:
        q = _jl(WC / "cache" / f"auction_quotes-{d}.json")
        if isinstance(q, dict):
            quotes_by_day[d] = q

    day_rows: list[dict] = []
    stock_rows: list[dict] = []

    for d in days:
        i = idx[d]
        if i < 2:
            continue
        prev, prev2 = days[i - 1], days[i - 2]
        q = quotes_by_day.get(d)
        if not q:
            continue
        pool = _zt_rows(prev)
        if not pool:
            continue
        pcodes = _codes(pool)
        # ── ① 昨收晋级率（T-1 盘后可得）────────────────────────────────
        base = _codes(_zt_rows(prev2))
        promo = (len(_codes(_zt_rows(prev)) & base) / len(base) * 100.0) if base else None
        # ── ②③ 今开红开比例（09:25 实况）──────────────────────────────
        gaps: list[tuple[dict, float]] = []
        for r in pool:
            c = str(r.get("code") or "").strip()
            g = _gap(q.get(c) or {})
            if g is not None:
                gaps.append((r, g))
        if not gaps:
            continue
        red = sum(1 for _, g in gaps if g > 0)
        red_ratio = red / len(gaps)
        leaders = [(r, g) for r, g in gaps if _boards(r) >= 2]
        lead_ratio = (sum(1 for _, g in leaders if g > 0) / len(leaders)) if leaders else None
        day_rows.append({
            "date": d, "prev": prev, "pool_n": len(gaps),
            "promote_rate": round(promo, 1) if promo is not None else None,
            "red_open_ratio": round(red_ratio, 4),
            "leader_red_open_ratio": round(lead_ratio, 4) if lead_ratio is not None else None,
            "leader_n": len(leaders),
        })
        # ── 每票：T 日可成交性 + T+1 收盘收益 ─────────────────────────
        for r, g in gaps:
            c = str(r.get("code") or "").strip()
            b = _boards(r)
            lim = 20.0 if c.startswith(("300", "301", "688")) else 10.0
            sealed = g >= lim - 0.2
            bars = _bars(c)
            bt, bt1 = bars.get(d), bars.get(days[i + 1]) if i + 1 < len(days) else None
            ret = None
            if bt and bt1:
                try:
                    o, c1 = float(bt["open"]), float(bt1["close"])
                    if o > 0:
                        ret = (c1 / o - 1) * 100.0
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    ret = None
            stock_rows.append({
                "date": d, "code": c, "boards": b, "gap": round(g, 2),
                "sealed": bool(sealed), "ret_t1": round(ret, 2) if ret is not None else None,
                "promote_rate": round(promo, 1) if promo is not None else None,
                "red_open_ratio": round(red_ratio, 4),
                "leader_red_open_ratio": round(lead_ratio, 4) if lead_ratio is not None else None,
                "red_small": bool(0 < g <= 5),
            })
    return {"days": day_rows, "stocks": stock_rows}


def agg(rs: list[dict]) -> dict[str, Any]:
    vals = [r["ret_t1"] for r in rs if r.get("ret_t1") is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "avg": round(sum(vals) / len(vals), 2),
            "med": round(st.median(vals), 2),
            "win": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
            "days": len({r["date"] for r in rs})}


def bands(rows: list[dict], key: str, edges: list[float], rev: bool = False) -> dict:
    """按阈值切档（rev=True 表示「≥阈值算好」，默认「≤阈值算弱」）。"""
    out = {}
    lo = None
    for e in edges:
        sel = [r for r in rows if r.get(key) is not None and (lo is None or r[key] > lo)
               and r[key] <= e]
        out[f"({lo if lo is not None else '-inf'}, {e}]"] = agg(sel)
        lo = e
    sel = [r for r in rows if r.get(key) is not None and (lo is None or r[key] > lo)]
    out[f"({lo if lo is not None else '-inf'}, +inf)"] = agg(sel)
    return out


def scan(rows: list[dict], key: str, edges: list[float], *, low_is_weak: bool) -> list[dict]:
    """阈值扫描：低/高两侧分别看「保留批 - 剔除批」的差。"""
    res = []
    for thr in edges:
        if low_is_weak:      # 阈值以下算弱 → 剔除
            keep = [r for r in rows if r.get(key) is None or r[key] > thr]
            cut = [r for r in rows if r.get(key) is not None and r[key] <= thr]
        else:                # 阈值以上算弱 → 剔除
            keep = [r for r in rows if r.get(key) is None or r[key] < thr]
            cut = [r for r in rows if r.get(key) is not None and r[key] >= thr]
        a, b = agg(keep), agg(cut)
        if not a.get("n") or not b.get("n"):
            continue
        res.append({"thr": thr, "keep": a, "cut": b,
                    "edge": round(a["avg"] - b["avg"], 2)})
    return res


def main() -> None:
    data = build()
    days, stocks = data["days"], data["stocks"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"交易日 {len(days)} 天｜票样本 {len(stocks)} 条｜"
          f"有收益 {sum(1 for r in stocks if r.get('ret_t1') is not None)} 条")
    print(f"区间 {days[0]['date']} ~ {days[-1]['date']}\n")

    for label, sel in (("全部可成交（竞价未封死）", [r for r in stocks if not r["sealed"]]),
                       ("红开可买 0<gap≤5", [r for r in stocks if not r["sealed"] and r["red_small"]])):
        print(f"===== {label}  n={agg(sel).get('n')}  基准 {agg(sel)} =====")
        for key, edges, low_weak, name in (
            ("promote_rate", [15, 18, 20, 22, 25, 28, 30], True, "昨收晋级率(%)"),
            ("red_open_ratio", [0.20, 0.25, 0.30, 0.35, 0.40, 0.45], True, "今开红开比例"),
            ("leader_red_open_ratio", [0.20, 0.25, 0.30, 0.35, 0.40], True, "今开连板红开比例"),
        ):
            print(f"\n-- {name} 分档 --")
            for k, v in bands(sel, key, edges).items():
                if v.get("n"):
                    print(f"   {k:<16} n={v['n']:<4} 平均{v['avg']:+6.2f}%  "
                          f"中位{v['med']:+6.2f}%  胜{v['win']:5.1f}%  天数{v['days']}")
                else:
                    print(f"   {k:<16} n=0")
            print(f"-- {name} 阈值扫描（保留 − 剔除，越大越好）--")
            for s in scan(sel, key, edges, low_is_weak=low_weak):
                print(f"   阈值{s['thr']:<6} 保留 n={s['keep']['n']:<4} {s['keep']['avg']:+6.2f}%/"
                      f"胜{s['keep']['win']:5.1f}% ｜ 剔除 n={s['cut']['n']:<4} "
                      f"{s['cut']['avg']:+6.2f}%/胜{s['cut']['win']:5.1f}% ｜ 差 {s['edge']:+.2f}pct")
        print()

    # 逐日明细（便于看单日主导）
    print("===== 逐日三条读数（按晋级率升序）=====")
    print(f"{'日期':<12}{'晋级率':>7}{'红开':>8}{'连板红开':>9}{'样本':>6}{'收益均值':>9}{'胜率':>7}")
    for d in sorted(days, key=lambda x: (x["promote_rate"] is None, x["promote_rate"] or 0)):
        rs = [r for r in stocks if r["date"] == d["date"] and not r["sealed"]]
        a = agg(rs)
        pr = f"{d['promote_rate']:.1f}" if d["promote_rate"] is not None else "-"
        lr = f"{d['leader_red_open_ratio'] * 100:.0f}%" if d["leader_red_open_ratio"] is not None else "-"
        print(f"{d['date']:<12}{pr:>7}{d['red_open_ratio'] * 100:>7.0f}%{lr:>9}"
              f"{a.get('n', 0):>6}{a.get('avg', 0):>+8.2f}%{a.get('win', 0):>6.1f}%")


if __name__ == "__main__":
    main()
