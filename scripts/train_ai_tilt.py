#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""训练智能评分层（2026-11-11 计划 P0/P1）

用法：
    python3 scripts/train_ai_tilt.py --dataset-only   # 只建样本、打印分布（不写模型）
    python3 scripts/train_ai_tilt.py                  # 训练 + walk-forward + 落模型（达标才落）

## 样本口径

- 行：`wealth_code/cache/pool_call-<D>.json` 的每只票（含买/不买，反事实也算样本）
- 标签：`au_ret` = 09:25 竞价价买 → **当日收盘**卖（策略口径，与账本一致）
  · 优先读 `cache/pool_ledger/<D>.json`（已结算）
  · 缺失则用 `auction_quotes-<D>.json` 的 `auction_price` + `kline_qfq` 当日收盘现算
- 训练/验证：**按日期 walk-forward**，最后 5 个交易日只做验证，⛔ 不参与拟合

## 门槛（不达标就不产出模型）

- 样本 ≥ 120；验证 AUC ≥ 0.52；否则保留旧模型、不覆盖。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core.ai_tilt import FEATURES, MODEL_DIR, features  # noqa: E402

WC = ROOT / "wealth_code" / "cache"
CACHE = ROOT / "cache"
MIN_SAMPLES = 120
MIN_AUC = 0.52
VALID_DAYS = 5


def _bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _kline_close(code: str, day: str) -> float | None:
    try:
        d = json.loads((CACHE / "kline_qfq" / f"{code}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for b in d.get("bars") or []:
        if str(b.get("date"))[:10] == day and b.get("close") is not None:
            return float(b["close"])
    return None


def _auction_price(code: str, day: str) -> float | None:
    try:
        d = json.loads((WC / f"auction_quotes-{day}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    q = d.get(code) if isinstance(d, dict) else None
    if isinstance(q, dict) and q.get("auction_price") is not None:
        try:
            return float(q["auction_price"])
        except (TypeError, ValueError):
            return None
    return None


def _ledger_ret(day: str, code: str) -> float | None:
    try:
        d = json.loads((CACHE / "pool_ledger" / f"{day}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for r in d.get("rows") or []:
        if str(r.get("code")) == code and r.get("au_ret") is not None:
            try:
                return float(r["au_ret"])
            except (TypeError, ValueError):
                return None
    return None


def build_dataset() -> list[dict]:
    out: list[dict] = []
    for p in sorted(WC.glob("pool_call-*.json")):
        day = p.stem.replace("pool_call-", "")
        try:
            views = (json.loads(p.read_text(encoding="utf-8")).get("views") or {})
        except Exception:  # noqa: BLE001
            continue
        for code, row in views.items():
            if not isinstance(row, dict):
                continue
            ret = _ledger_ret(day, code)
            src = "ledger"
            if ret is None:
                ap = _auction_price(code, day)
                close = _kline_close(code, day)
                if ap and close and ap > 0:
                    ret = round((close / ap - 1) * 100, 3)
                    src = "kline"
            if ret is None:
                continue
            out.append({
                "date": day, "code": code,
                "x": features(row),
                "y": 1 if ret > 0 else 0,
                "ret": ret,
                "label_src": src,
                "view": row.get("view"),
            })
    return out


def _sigmoid(z: float) -> float:
    z = max(min(z, 30.0), -30.0)
    return 1.0 / (1.0 + math.exp(-z))


def _auc(pairs: list[tuple[float, int]]) -> float:
    """秩和 AUC（不依赖 numpy/sklearn）。"""
    pos = [s for s, y in pairs if y == 1]
    neg = [s for s, y in pairs if y == 0]
    if not pos or not neg:
        return 0.5
    wins = 0.0
    for a in pos:
        for b in neg:
            wins += 1.0 if a > b else 0.5 if a == b else 0.0
    return wins / (len(pos) * len(neg))


def train(rows: list[dict], *, epochs: int = 400, lr: float = 0.08, l2: float = 0.02):
    """L2 逻辑回归（全批量梯度下降）。返回 (weights, bias, mean, std)。"""
    n = len(rows)
    d = len(FEATURES)
    mean = [sum(r["x"][j] for r in rows) / n for j in range(d)]
    var = [sum((r["x"][j] - mean[j]) ** 2 for r in rows) / n for j in range(d)]
    std = [math.sqrt(v) or 1.0 for v in var]
    xs = [[(r["x"][j] - mean[j]) / std[j] for j in range(d)] for r in rows]
    ys = [r["y"] for r in rows]
    w = [0.0] * d
    b = 0.0
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for x, y in zip(xs, ys):
            p = _sigmoid(sum(wi * xi for wi, xi in zip(w, x)) + b)
            err = p - y
            for j in range(d):
                gw[j] += err * x[j]
            gb += err
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
        b -= lr * (gb / n)
    return w, b, mean, std


def evaluate(rows: list[dict], w, b, mean, std) -> dict:
    pairs = []
    for r in rows:
        x = [(r["x"][j] - mean[j]) / std[j] for j in range(len(FEATURES))]
        p = _sigmoid(sum(wi * xi for wi, xi in zip(w, x)) + b)
        pairs.append((p, r["y"]))
    auc = _auc(pairs)
    acc = sum(1 for p, y in pairs if (p >= 0.5) == bool(y)) / max(len(pairs), 1)
    return {"auc": round(auc, 4), "acc": round(acc, 4), "n": len(pairs),
            "pos_rate": round(sum(1 for _, y in pairs if y) / max(len(pairs), 1), 3)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="训练智能评分层（只读样本，落模型）")
    ap.add_argument("--dataset-only", action="store_true", help="只建样本并打印分布")
    args = ap.parse_args(argv)

    rows = build_dataset()
    if not rows:
        print("[ai_tilt] 没有可用样本（需要 pool_call + 账本或行情）")
        return 1
    days = sorted({r["date"] for r in rows})
    pos = sum(r["y"] for r in rows)
    print(f"[ai_tilt] 样本 {len(rows)} 票日 · {len(days)} 个交易日 · 正样本 {pos} "
          f"({pos / len(rows):.0%}) · 标签来源 "
          f"{ {s: sum(1 for r in rows if r['label_src'] == s) for s in ('ledger', 'kline')} }")
    if args.dataset_only:
        for d in days[-5:]:
            xs = [r for r in rows if r["date"] == d]
            print(f"  {d}: {len(xs)} 票 · 正 {sum(r['y'] for r in xs)} · "
                  f"均收益 {sum(r['ret'] for r in xs) / len(xs):+.2f}%")
        return 0

    if len(rows) < MIN_SAMPLES:
        print(f"[ai_tilt] 样本 {len(rows)} < {MIN_SAMPLES}，⛔ 不训练（保持纯机械）")
        return 1

    valid_days = set(days[-VALID_DAYS:])
    train_rows = [r for r in rows if r["date"] not in valid_days]
    valid_rows = [r for r in rows if r["date"] in valid_days]
    w, b, mean, std = train(train_rows)
    tr = evaluate(train_rows, w, b, mean, std)
    va = evaluate(valid_rows, w, b, mean, std)
    print(f"[ai_tilt] 训练 {tr} | 验证 {va}")

    if va["auc"] < MIN_AUC:
        print(f"[ai_tilt] 验证 AUC {va['auc']} < {MIN_AUC} → ⛔ 不产出模型（保持旧版/纯机械）")
        return 1

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(MODEL_DIR.glob("ai_tilt_v*.json"))
    ver = len(files) + 1
    model = {
        "version": f"ai_tilt_v{ver}",
        "trained_at": _bj().strftime("%Y-%m-%d %H:%M:%S"),
        "window": [days[0], days[-1]],
        "samples": len(train_rows),
        "features": list(FEATURES),
        "mean": [round(v, 6) for v in mean],
        "std": [round(v, 6) for v in std],
        "weights": [round(v, 6) for v in w],
        "bias": round(b, 6),
        "valid": {"auc": va["auc"], "acc": va["acc"], "n": va["n"],
                  "days": sorted(valid_days)},
        "guard": {"min_auc": MIN_AUC, "min_samples": MIN_SAMPLES},
    }
    (MODEL_DIR / f"{model['version']}.json").write_text(
        json.dumps(model, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[ai_tilt] 已写入 config/models/{model['version']}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
