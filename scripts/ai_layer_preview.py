#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看一眼「某一天 09:25 的 AI 层结果」（只读，不落盘、不推卡）

用户 11-11：「把那个任务调出来再执行一下，看看结果是什么样子」——
⛔ 不要真的重跑 09:25 自动化（收盘后它会把「下一天」当执行日、且没有竞价数据）。
用本脚本回放指定执行日，打印 AI 层实际会输出什么：

    python3 scripts/ai_layer_preview.py --date 2026-11-11

输出：机械买入名单 / p_win 分数 / M 与 MA 两套顺序 / 催化等级 / 是否改序。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "wealth_code"))

import pool_call_replay as R  # noqa: E402
from core import ai_advice as aa  # noqa: E402
from core import ai_tilt as at  # noqa: E402
from core import pool_call as pc  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 层结果预览（只读）")
    ap.add_argument("--date", required=True, help="执行日 YYYY-MM-DD")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    days = R.trading_days()
    day = R.replay_day(args.date, days)
    if not day:
        print(f"[preview] {args.date} 没有完整的候选+竞价，无法回放")
        return 1
    model = at.load_model()
    ctx = at.build_day_context(args.date)
    pool = [*(day["cands"].get("relay") or []), *(day["cands"].get("watch") or [])]
    adv = at.advice_payload(model, pool, mode="shadow", ctx=ctx)
    cat = aa.load(args.date)
    res = pc.call_pool(day["cands"], day["quotes"], advice=adv, catalyst=cat, **day["kw"])
    ai = (res.get("meta") or {}).get("ai_layer") or {}
    buys = [c for c, v in res["views"].items() if v.get("view") == "participate"]
    names = {c: v.get("name") for c, v in res["views"].items()}
    out = {
        "date": args.date,
        "model": (model or {}).get("version"),
        "buys": [{"code": c, "name": names.get(c)} for c in buys],
        "mode": ai.get("mode"),
        "changed": ai.get("changed"),
        "arms": ai.get("arms"),
        "scores": ai.get("scores"),
        "abstained": ai.get("abstained"),
        "catalyst": (res.get("meta") or {}).get("catalyst"),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print(f"[preview] {args.date} · 模型 {out['model']} · 模式 {out['mode']}")
    print(f"  机械买入 {len(buys)} 只：" + "、".join(
        f"{names.get(c)}({c})" for c in buys) if buys else "  机械买入 0 只")
    sc = out["scores"] or {}
    for c in buys:
        print(f"    {names.get(c)}({c})  p_win={sc.get(c)}")
    arms = out["arms"] or {}
    print(f"  M 顺序  {arms.get('M')}")
    print(f"  MA 顺序 {arms.get('MA')}")
    print(f"  是否改序：{'是' if out['changed'] else '否'}"
          + (f"｜弃权 {out['abstained']}" if out.get("abstained") else ""))
    cat = out["catalyst"] or {}
    lv = cat.get("levels") or {}
    if lv:
        top = sorted(lv.items(), key=lambda kv: str(kv[1]))[:5]
        print(f"  题材催化（{len(lv)} 条）：" + "、".join(f"{k}={v}" for k, v in top))
    else:
        print("  题材催化：未落盘（等于没有 LLM 意见，⛔ 不是「催化弱」）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
