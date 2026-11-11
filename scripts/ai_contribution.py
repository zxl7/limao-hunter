#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智能层贡献报告（2026-11-11 计划 P3）

两套名单都在 09:25 冻结件里：
- `meta.ai_layer.arms.M`  —— 纯机械顺序（第一位＝机械首选）
- `meta.ai_layer.arms.MA` —— 智能层 tie-break 后的顺序

shadow 模式下两套**实际买入集合相同**，所以这里量的是**排序质量**：

    Δ_rank = 平均(MA 首选收益) − 平均(M 首选收益)

⛔ 不是「AI 也能赚钱」的证明，只是「如果按它的顺序先买，会更好还是更差」。
⛔ 样本 < 20 天只打印、不下结论（与 §三·L 的样本纪律一致）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core.ai_tilt import au_ret  # noqa: E402

WC = ROOT / "wealth_code" / "cache"
MIN_DAYS = 20


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def build() -> dict:
    rows = []
    for p in sorted(WC.glob("pool_call-*.json")):
        day = p.stem.replace("pool_call-", "")
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        ai = (d.get("meta") or {}).get("ai_layer") or {}
        arms = ai.get("arms") or {}
        m, ma = arms.get("M") or [], arms.get("MA") or []
        if not m or not ma:
            continue
        r_m, r_ma = au_ret(day, m[0]), au_ret(day, ma[0])
        if r_m is None or r_ma is None:
            continue
        rows.append({"date": day, "M": m[0], "MA": ma[0],
                     "ret_M": r_m, "ret_MA": r_ma,
                     "changed": m[0] != ma[0]})
    ret_m = [r["ret_M"] for r in rows]
    ret_ma = [r["ret_MA"] for r in rows]
    diff = [r["ret_MA"] - r["ret_M"] for r in rows]
    return {
        "schema": "ai_contribution_v1",
        "days": len(rows),
        "changed_days": sum(1 for r in rows if r["changed"]),
        "avg_ret_M": _mean(ret_m),
        "avg_ret_MA": _mean(ret_ma),
        "delta": _mean(diff),
        "win_rate_M": round(100 * sum(1 for x in ret_m if x > 0) / len(ret_m), 1) if ret_m else None,
        "win_rate_MA": round(100 * sum(1 for x in ret_ma if x > 0) / len(ret_ma), 1) if ret_ma else None,
        "conclusive": len(rows) >= MIN_DAYS,
        "min_days": MIN_DAYS,
        "rows": rows,
    }


def replay() -> dict:
    """回放模式：用历史候选+竞价重跑两套顺序，直接算排序质量（⛔ 不写生产冻结件）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "wealth_code"))
    import pool_call_replay as R
    from core import ai_tilt as at
    from core import pool_call as pc

    model = at.load_model()
    if not model:
        return {"days": 0, "error": "模型缺失"}
    days = R.trading_days()
    rows = []
    for d in days:
        day = R.replay_day(d, days)
        if not day:
            continue
        _pool = [*(day["cands"].get("relay") or []), *(day["cands"].get("watch") or [])]
        adv = at.advice_payload(model, _pool, mode="shadow")
        res = pc.call_pool(day["cands"], day["quotes"], advice=adv, **day["kw"])
        ai = (res.get("meta") or {}).get("ai_layer") or {}
        m = (ai.get("arms") or {}).get("M") or []
        ma = (ai.get("arms") or {}).get("MA") or []
        if not m or not ma:
            continue
        r_m, r_ma = at.au_ret(d, m[0]), at.au_ret(d, ma[0])
        if r_m is None or r_ma is None:
            continue
        rows.append({"date": d, "M": m[0], "MA": ma[0], "ret_M": r_m,
                     "ret_MA": r_ma, "changed": m[0] != ma[0]})
    ret_m = [r["ret_M"] for r in rows]
    ret_ma = [r["ret_MA"] for r in rows]
    diff = [r["ret_MA"] - r["ret_M"] for r in rows]
    return {
        "schema": "ai_contribution_replay_v1",
        "model": model.get("version"),
        "days": len(rows),
        "changed_days": sum(1 for r in rows if r["changed"]),
        "avg_ret_M": _mean(ret_m), "avg_ret_MA": _mean(ret_ma), "delta": _mean(diff),
        "win_rate_M": round(100 * sum(1 for x in ret_m if x > 0) / len(ret_m), 1) if ret_m else None,
        "win_rate_MA": round(100 * sum(1 for x in ret_ma if x > 0) / len(ret_ma), 1) if ret_ma else None,
        "conclusive": len(rows) >= MIN_DAYS,
        "min_days": MIN_DAYS,
        "rows": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="智能层贡献报告（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--replay", action="store_true", help="用历史数据重跑两套顺序")
    args = ap.parse_args(argv)
    d = replay() if args.replay else build()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    if not d["days"]:
        print("[ai_contrib] 没有带 ai_layer 的冻结件（需先跑 shadow）")
        return 1
    print(f"[ai_contrib] {d['days']} 天（改序 {d['changed_days']} 天）"
          f"｜机械首选均 {d['avg_ret_M']}% vs 智能首选均 {d['avg_ret_MA']}%"
          f"｜Δ {d['delta']}%")
    print(f"  胜率 M {d['win_rate_M']}% / MA {d['win_rate_MA']}%"
          + ("" if d["conclusive"] else f" · ⚠️ 样本 < {d['min_days']} 天，只记录不下结论"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
