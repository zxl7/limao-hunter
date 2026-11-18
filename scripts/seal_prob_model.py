#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封板概率模型（2026-11-18 用户口径：「我们要的是今天能封住涨停的票」）。

## 为什么换目标函数

早前一版用「竞价买 → 当日收盘卖」做回放，结论是「分数反向」。
那是**错的目标**：本系统的逻辑是「今天封住 → 明天有溢价」，收益来自**封板**，不是当日波动。

本脚本用同一批账本数据回答三件事：
1. 封住 vs 没封住，钱差多少；
2. 用已有字段（开幅/盘后分/同线家数/炸板）能不能预测「封板」；
3. 按「期望收益 = P(封板)×封板溢价 − (1−P)×未封损失」排序，和现状比谁好。

## 模型

`P(封板) = sigmoid(w·[gap, eod_score, mates, zbc] + b)`，逻辑回归，纯 numpy 手写梯度下降。
⛔ 样本只有 ~250 条，**不做复杂模型**；评估用「按日留一」交叉验证（防同日数据泄漏）。

## 用法

    python3 scripts/seal_prob_model.py           # 训练 + 评估 + 排序对比（只读）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

FEATURES = ("gap", "eod", "mates", "zbc")


def _j(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _cand_for(day: str) -> dict[str, dict]:
    files = sorted((ROOT / "wealth_code/cache").glob("candidates-*.json"))
    prev = [f for f in files if f.stem.split("-", 1)[1] < day]
    if not prev:
        return {}
    c = _j(prev[-1]) or {}
    return {str(r.get("code")): r for r in (c.get("relay") or []) + (c.get("watch") or [])}


def load_rows() -> list[dict]:
    rows: list[dict] = []
    for f in sorted((ROOT / "cache/pool_ledger").glob("*.json")):
        day = f.stem
        led = _j(f) or {}
        cands = _cand_for(day)
        for r in led.get("rows") or []:
            chg, lim = r.get("chg_pct"), r.get("limit_up_pct") or 10.0
            if not isinstance(chg, (int, float)):
                continue
            c = cands.get(str(r.get("code"))) or {}
            gaps = r.get("gap_pct")
            eod = c.get("score")
            if not isinstance(gaps, (int, float)) or not isinstance(eod, (int, float)):
                continue
            rows.append({
                "day": day,
                "code": str(r.get("code")),
                "gap": float(gaps),
                "eod": float(eod),
                "mates": float(c.get("theme_mates_cnt") or 0.0),
                "zbc": float(c.get("zbc") or 0.0),
                "sealed": 1.0 if float(chg) >= float(lim) - 0.2 else 0.0,
                "au_ret": r.get("au_ret"),
                "t1_ret": r.get("t1_ret"),
            })
    return rows


def _x(row: dict) -> np.ndarray:
    # 标准化在 fit 内做（这里只做量纲压缩，避免梯度爆炸）
    return np.array([row["gap"] / 10.0, row["eod"] / 100.0, row["mates"] / 10.0, row["zbc"] / 10.0])


def fit(rows: list[dict], epochs: int = 4000, lr: float = 0.35) -> np.ndarray:
    X = np.vstack([_x(r) for r in rows])
    y = np.array([r["sealed"] for r in rows])
    w = np.zeros(X.shape[1] + 1)          # 最后一维 = bias
    Xb = np.hstack([X, np.ones((len(X), 1))])
    for _ in range(epochs):
        p = 1.0 / (1.0 + np.exp(-Xb @ w))
        grad = Xb.T @ (p - y) / len(y)
        w -= lr * grad
    return w


def predict(w: np.ndarray, row: dict) -> float:
    z = float(np.dot(np.append(_x(row), 1.0), w))
    return 1.0 / (1.0 + np.exp(-z))


def auc(scores: list[float], labels: list[float]) -> float:
    pos = [s for s, l in zip(scores, labels) if l > 0.5]
    neg = [s for s, l in zip(scores, labels) if l <= 0.5]
    if not pos or not neg:
        return float("nan")
    wins = sum(1.0 for a in pos for b in neg if a > b) + 0.5 * sum(1.0 for a in pos for b in neg if a == b)
    return wins / (len(pos) * len(neg))


def main() -> int:
    rows = load_rows()
    if len(rows) < 30:
        print("⛔ 样本不足")
        return 2
    sealed = [r for r in rows if r["sealed"] > 0.5]
    unsealed = [r for r in rows if r["sealed"] <= 0.5]

    def _avg(v, k):
        xs = [x[k] for x in v if isinstance(x.get(k), (int, float))]
        return (sum(xs) / len(xs)) if xs else float("nan")

    print(f"样本 {len(rows)} 条（{len(set(r['day'] for r in rows))} 日）｜整体封板率 "
          f"{100 * len(sealed) / len(rows):.1f}%")
    print("\n== 1. 封住 vs 没封住，钱差多少 ==")
    print(f"封住  {len(sealed):3d} 笔 | 当日 {_avg(sealed, 'au_ret'):+.2f}% | T+1 {_avg(sealed, 't1_ret'):+.2f}%")
    print(f"没封  {len(unsealed):3d} 笔 | 当日 {_avg(unsealed, 'au_ret'):+.2f}% | T+1 {_avg(unsealed, 't1_ret'):+.2f}%")

    print("\n== 2. 按日留一交叉验证（防同日泄漏）==")
    days = sorted(set(r["day"] for r in rows))
    scores, labels = [], []
    for d in days:
        train = [r for r in rows if r["day"] != d]
        test = [r for r in rows if r["day"] == d]
        if len(train) < 40 or not test:
            continue
        w = fit(train)
        for r in test:
            scores.append(predict(w, r))
            labels.append(r["sealed"])
    print(f"留一 AUC = {auc(scores, labels):.3f}（0.5=随机，>0.65 可用）；样本 {len(scores)}")

    w = fit(rows)
    print("\n== 3. 全样本权重（量纲已压缩，仅供看方向）==")
    for name, coef in zip(FEATURES + ("bias",), w):
        print(f"  {name:6s} {coef:+.3f}")

    print("\n== 4. 按「期望收益」排序 vs 现状 ==")
    sealed_t1 = _avg(sealed, "t1_ret")
    unsealed_t1 = _avg(unsealed, "t1_ret")
    for r in rows:
        p = predict(w, r)
        r["p_seal"] = p
        r["ev"] = p * sealed_t1 + (1 - p) * unsealed_t1
    # 每日取 EV 前 2 vs 每日现状参与（用 pool_call verdict）
    ev_top, cur = [], []
    for d in days:
        seg = [r for r in rows if r["day"] == d]
        if not seg:
            continue
        seg.sort(key=lambda x: -x["ev"])
        ev_top.extend([x["t1_ret"] for x in seg[:2] if isinstance(x.get("t1_ret"), (int, float))])
        call = _j(ROOT / "wealth_code/cache" / f"pool_call-{d}.json") or {}
        part = {str(c) for c, v in (call.get("views") or {}).items() if str(v.get("view")) == "participate"}
        cur.extend([x["t1_ret"] for x in seg if x["code"] in part and isinstance(x.get("t1_ret"), (int, float))])
    def _m(v):
        return f"{len(v):3d} 笔 | 均值 {sum(v) / len(v):+.2f}%" if v else "0 笔"
    print(f"现状（闸门参与）  {_m(cur)}")
    print(f"EV 前 2 名        {_m(ev_top)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
