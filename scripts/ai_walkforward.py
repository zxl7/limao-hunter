#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向前模拟（walk-forward）：用「当天之前」的数据训练，逐日模拟智能层的表现。

## 为什么必须这样做

`train_ai_tilt.py` 只留最后 5 天做验证 —— 那是**一次**切分，样本太小。
本脚本做**扩展窗口 walk-forward**：对最近 N 个交易日里的每一天 D：

1. 只用 `< D` 的样本训练模型（⛔ 绝不看当天及以后）；
2. 用当天 09:25 冻结件里的机械参与票 + 当日上下文，算 `p_win`；
3. 记录三套结果：
   - **M**：机械全买（等权，基准）
   - **MA_top1**：按 `p_win` 在参与票里选一只
   - **MA_drop**：丢掉 `p_win` 最低的若干只（弃权变体）
4. 收益一律用 `au_ret`（竞价买 → 当日收盘，与账本同口径）。

## 输出

逐日明细 + 汇总（均值/胜率/Δ/样本量）。⛔ N < 20 天只记录、不下结论。

用法：
    python3 scripts/ai_walkforward.py             # 最近 30 个交易日
    python3 scripts/ai_walkforward.py --days 40
    python3 scripts/ai_walkforward.py --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core.ai_tilt import FEATURES, au_ret, build_day_context, features  # noqa: E402

WC = ROOT / "wealth_code" / "cache"
MIN_TRAIN = 40          # 少于这个样本不训练（宁可不出结论）
EPOCHS = 250
LR = 0.35
L2 = 0.05


def load_dataset() -> list[dict]:
    """所有可训练票日（含标签）；与 train_ai_tilt 同口径。"""
    out: list[dict] = []
    for p in sorted(WC.glob("pool_call-*.json")):
        day = p.stem.replace("pool_call-", "")
        ctx = build_day_context(day)
        try:
            views = json.loads(p.read_text(encoding="utf-8")).get("views") or {}
        except Exception:  # noqa: BLE001
            continue
        for code, row in views.items():
            if not isinstance(row, dict):
                continue
            ret = au_ret(day, code)
            if ret is None:
                continue
            out.append({"date": day, "code": code, "x": features(row, ctx),
                        "y": 1 if ret > 0 else 0, "ret": ret,
                        "view": row.get("view")})
    return out


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit(X: np.ndarray, y: np.ndarray) -> dict:
    n, d = X.shape
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    w = np.zeros(d)
    b = 0.0
    for _ in range(EPOCHS):
        p = _sigmoid(Z @ w + b)
        err = p - y
        w -= LR * (Z.T @ err / n + L2 * w)
        b -= LR * float(err.mean())
    return {"mu": mu, "sd": sd, "w": w, "b": b}


def predict(model: dict, x: list[float]) -> float:
    z = (np.asarray(x, dtype=float) - model["mu"]) / model["sd"]
    return float(_sigmoid(float(z @ model["w"] + model["b"])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="智能层 walk-forward 向前模拟（只读）")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--drop-k", type=int, default=1, help="弃权变体：每丢弃多少只")
    args = ap.parse_args(argv)

    data = load_dataset()
    if not data:
        print("[walkforward] 没有样本")
        return 1
    all_days = sorted({r["date"] for r in data})
    targets = all_days[-args.days:]

    rows = []
    for day in targets:
        train = [r for r in data if r["date"] < day]
        today = [r for r in data if r["date"] == day]
        parts = [r for r in today if r["view"] == "participate"]
        if len(train) < MIN_TRAIN:
            continue
        X = np.asarray([r["x"] for r in train], dtype=float)
        y = np.asarray([r["y"] for r in train], dtype=float)
        model = fit(X, y)
        for r in today:
            r["p"] = predict(model, r["x"])
        # 全池排序质量（含未买入的票）：Spearman IC + 首尾十分位价差
        # ⛔ 这不代表能赚多少，只说明「模型把好票排前面没有」。
        ps = [r["p"] for r in today]
        rs = [r["ret"] for r in today]
        ic = None
        if len(ps) >= 6:
            rank = lambda xs: [sorted(xs).index(v) for v in xs]
            rp, rr = rank(ps), rank(rs)
            mp, mr = sum(rp) / len(rp), sum(rr) / len(rr)
            num = sum((a - mp) * (b - mr) for a, b in zip(rp, rr))
            den = math.sqrt(sum((a - mp) ** 2 for a in rp) * sum((b - mr) ** 2 for b in rr))
            ic = round(num / den, 3) if den else None
        decile = None
        if len(today) >= 10:
            order = sorted(today, key=lambda r: r["p"], reverse=True)
            k = max(1, len(order) // 10)
            decile = round(sum(r["ret"] for r in order[:k]) / k
                           - sum(r["ret"] for r in order[-k:]) / k, 2)
        m_ret = best_ret = drop_ret = None
        if parts:
            m_ret = sum(r["ret"] for r in parts) / len(parts)
            best = max(parts, key=lambda r: r["p"])
            best_ret = best["ret"]
            # 弃权变体：丢掉 p 最低的 k 只（至少留 1 只）
            k = min(args.drop_k, max(len(parts) - 1, 0))
            drop = set(id(r) for r in sorted(parts, key=lambda r: r["p"])[:k])
            kept = [r for r in parts if id(r) not in drop]
            drop_ret = sum(r["ret"] for r in kept) / len(kept)
        rows.append({
            "date": day, "n": len(parts),
            "ret_M": None if m_ret is None else round(m_ret, 3),
            "ret_top1": None if best_ret is None else round(best_ret, 3),
            "ret_drop": None if drop_ret is None else round(drop_ret, 3),
            "p_top": round(max(r["p"] for r in parts), 4) if parts else None,
            "p_min": round(min(r["p"] for r in parts), 4) if parts else None,
            "ic": ic,
            "decile_spread": decile,
            "rows": len(today),
        })

    if not rows:
        print("[walkforward] 没有可模拟的日子（历史样本不足 %d）" % MIN_TRAIN)
        return 1

    def avg(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    traded = [r for r in rows if r.get("ret_M") is not None]

    out = {
        "schema": "ai_walkforward_v1",
        "days": len(rows),
        "window": [rows[0]["date"], rows[-1]["date"]],
        "avg_M": avg("ret_M"),
        "avg_top1": avg("ret_top1"),
        "avg_drop": avg("ret_drop"),
        "delta_top1": round(avg("ret_top1") - avg("ret_M"), 3),
        "delta_drop": round(avg("ret_drop") - avg("ret_M"), 3),
        "traded_days": len(traded),
        "win_M": round(100 * sum(1 for r in traded if r["ret_M"] > 0) / max(len(traded), 1), 1),
        "win_top1": round(100 * sum(1 for r in traded if r["ret_top1"] > 0) / max(len(traded), 1), 1),
        "ic_mean": round(sum(r["ic"] for r in rows if r.get("ic") is not None)
                         / max(1, sum(1 for r in rows if r.get("ic") is not None)), 3),
        "decile_mean": round(sum(r["decile_spread"] for r in rows
                                 if r.get("decile_spread") is not None)
                             / max(1, sum(1 for r in rows if r.get("decile_spread") is not None)), 2),
        "rows": rows,
        "note": "扩展窗口 walk-forward：每天只用更早的数据训练；收益=竞价买→当日收盘",
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print(f"[walkforward] {out['days']} 天（{out['window'][0]} ~ {out['window'][1]}）"
          f"，其中有买入的 {out['traded_days']} 天")
    print(f"  机械全买   {out['avg_M']:+.2f}%  (胜率 {out['win_M']}%)")
    print(f"  智能选第一  {out['avg_top1']:+.2f}%  (胜率 {out['win_top1']}%)  Δ {out['delta_top1']:+.2f}%")
    print(f"  弃权丢{k}只   {out['avg_drop']:+.2f}%  Δ {out['delta_drop']:+.2f}%")
    print(f"  全池排序：IC {out['ic_mean']:+.3f}｜首尾十分位价差 {out['decile_mean']:+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
