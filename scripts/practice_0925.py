#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🎯 09:25 多层合一练习（机械 × 晋级率 × LLM × skill 口径）

用户 2026-11-12：「继续训练，按今早 9:25 的数据做预案练习，
务必 skill + LLM + 机械端 多层合一，口径不打架」。

对每个交易日重放：
1. 机械：`pool_call`（当日候选 + 当日竞价）
2. 晋级率：`promotion_odds`（昨日涨停 → 今日竞价档）
3. LLM：`ai_trader_judge`（可关，`--no-llm`）
4. 融合：`final_verdict` → 可买/观察/不买
5. 校验：当日是否真涨停 + 竞价买→收盘收益

输出：逐日表 + 汇总（命中率 / 平均收益 / 各层拦截数）。
⛔ 只做研究回放，不改判据。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))
sys.path.insert(0, str(ROOT / "scripts"))


def _mdays() -> list[str]:
    return sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))


def _close(code: str, day: str):
    try:
        bars = json.loads((ROOT / "cache" / "kline_qfq" / f"{code}.json").read_text(encoding="utf-8"))["bars"]
        hit = [b for b in bars if str(b.get("date"))[:10] == day]
        return float(hit[-1]["close"]) if hit else None
    except Exception:  # noqa: BLE001
        return None


def run_day(day: str, *, use_llm: bool) -> dict:
    from core import pool_call as pc, final_verdict as fv, promotion_odds as po
    import promotion_plan as pp
    import pool_call_replay as R

    days = R.trading_days()
    rd = R.replay_day(day, days)
    if not rd:
        return {"day": day, "error": "无可回放数据"}
    # 🧹 数据校验：竞价文件里的 time 日期必须等于当日（09-30 曾是 09-29 的数据）
    _ts = {str(v.get("time"))[:10] for v in (rd["quotes"] or {}).values()
           if isinstance(v, dict) and v.get("time")}
    if _ts and _ts != {day}:
        return {"day": day, "error": f"竞价文件日期错配 {sorted(_ts)}"}
    from core import second_wave as sw
    M = {"", "-", "—", "其他", "无", "未知", "none", "null"}
    pool = list(rd["cands"].get("relay") or []) + list(rd["cands"].get("watch") or [])
    kept = []
    for r in pool:
        if str(r.get("core_theme") or "").strip().lower() in M:
            continue
        b = int(r.get("boards") or 0)
        hit = sw.second_wave(b, str(r.get("code")))
        if hit:
            r.update(participation_eligibility="observe_only", buyable=False, watch_only=True)
        elif b == 1 and not r.get("new_direction_line"):
            r.update(participation_eligibility="observe_only", buyable=False, watch_only=True)
        kept.append(r)
    cands = {**rd["cands"], "relay": kept, "watch": []}
    res = pc.call_pool(cands, rd["quotes"], **rd["kw"])
    views = res.get("views") or {}

    plan = pp.build(day)
    lv = {}
    catp = ROOT / "cache" / f"ai_advice_{day}.json"
    if catp.exists():
        lv = {k: (v or {}).get("level") for k, v in
              (json.loads(catp.read_text(encoding="utf-8")).get("themes") or {}).items()}
    llm = {}
    if use_llm:
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "ai_trader_judge.py"),
                        "--date", day], check=False, timeout=180)
        tp = ROOT / "cache" / f"ai_trade_{day}.json"
        if tp.exists():
            llm = {str(x.get("code")): x for x in
                   (json.loads(tp.read_text(encoding="utf-8")).get("picks") or [])}
    # 🏛⚡🔄 三层
    from core import sector_hierarchy as sh2, accel as ac2, weak_to_strong as ws2
    # ⛔ 防未来函数：09:25 只可知**昨日**涨停池；用今日池会偷看收盘（2026-11-12 修）
    _prev_day = ws2._prev_zt(day)
    _prev_days = [d for d in _mdays() if d < day]
    _pday = _prev_days[-1] if _prev_days else day
    zt_rows = json.loads((ROOT / "cache" / "zt_history" / f"zt_{_pday}.json").read_text(encoding="utf-8"))["zt_list"]
    snap = {}
    try:
        snap = json.loads((ROOT / "cache" / "theme_snapshot" / f"theme_{_pday}.json").read_text(encoding="utf-8")).get("mapping") or {}
    except Exception:  # noqa: BLE001
        pass
    def _th(r):
        m = snap.get(str(r.get("code"))) or {}
        return str(m.get("theme_base") or m.get("theme") or r.get("core_theme") or r.get("sector") or "—")
    hier = sh2.classify(zt_rows, _th)   # 昨日池 → 无未来函数
    prev = ws2._prev_zt(day)
    acc = {}
    w2s = {}
    for _r in (plan.get("rows") or []):
        _c = str(_r.get("code"))
        acc[_c] = ac2.accel_of(_c, _pday)
        ap = (rd["quotes"].get(_c) or {}).get("auction_price")
        pc = (rd["quotes"].get(_c) or {}).get("prev_close")
        g = round((ap / pc - 1) * 100, 2) if ap and pc else None
        w2s[_c] = ws2.judge(_c, day, g, prev=prev)
    vs = fv.decide_tracks(plan.get("rows") or [], views, lv, llm, hier, acc, w2s)
    buys = [v for v in vs if v["conclusion"] == "可买"]
    zt = {str(r.get("code")) for r in (json.loads(
        (ROOT / "cache" / "zt_history" / f"zt_{day}.json").read_text(encoding="utf-8"))["zt_list"])}
    out = []
    for v in vs[:8]:
        ap = (rd["quotes"].get(str(v["code"])) or {}).get("auction_price")
        cl = _close(str(v["code"]), day)
        out.append({**{k: v.get(k) for k in ("code", "name", "pct", "conclusion", "mech", "catalyst", "llm", "track", "role", "accel", "w2s")},
                    "hit_zt": str(v["code"]) in zt,
                    "ret": round((cl / ap - 1) * 100, 2) if ap and cl else None})
    return {"day": day, "views": len(views), "participate": sum(1 for v in views.values() if v.get("view") == "participate"),
            "plan": len(plan.get("rows") or []), "buys": buys, "rows": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--from", dest="start")
    a = ap.parse_args(argv)
    days = [d for d in _mdays() if d < "2026-11-12"]
    if a.start:
        days = [d for d in days if d >= a.start]
    days = days[-a.days:]
    print(f"练习 {len(days)} 天 · LLM={'off' if a.no_llm else 'on'}")
    tot = []
    by_track: dict[str, list[float]] = {}
    for d in days:
        r = run_day(d, use_llm=not a.no_llm)
        if r.get("error"):
            print(f"{d} {r['error']}")
            continue
        b = "、".join(f"{x['name']}({x['pct']:.0f}%)" for x in r["buys"]) or "—"
        print(f"\n{d} 机械参与{r['participate']} · 预案{r['plan']} · 可买: {b}")
        for x in r["rows"]:
            if x["conclusion"] == "可买" or x["ret"] is not None:
                print(f"   {x['conclusion']:<3} {x['name']:<7}{x['pct']:>5.1f}% {str(x.get('track') or '-'):<4}"
                      f" 机械{x['mech'][:9]:<9} {'涨停' if x['hit_zt'] else '未涨停'} "
                      f"{'' if x['ret'] is None else f'{x['ret']:+.2f}%'}")
            if x["ret"] is not None and x["conclusion"] == "可买":
                tot.append(x["ret"])
                by_track.setdefault(str(x.get("track") or "—"), []).append(x["ret"])
    (ROOT / "cache" / f"practice_{len(days)}d.json").write_text(
        json.dumps({"days": days, "trades": [t for t in globals().get("_all_trades", [])]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    if tot:
        print(f"\n（仅统计最终「可买」）")
        print(f"\n样本 {len(tot)} 笔 · 均 {sum(tot)/len(tot):+.2f}% · 胜率 "
              f"{100*sum(1 for x in tot if x>0)/len(tot):.0f}%")
        import statistics as _st
        _wins = [x for x in tot if x > 0]
        _loss = [x for x in tot if x < 0]
        _ratio = ((sum(_wins)/len(_wins)) / abs(sum(_loss)/len(_loss))) if _wins and _loss else None
        print(f"  中位 {_st.median(tot):+.2f}% · 盈亏比 {_ratio:.2f}" if _ratio else f"  中位 {_st.median(tot):+.2f}%")
        print(f"  最好 {max(tot):+.2f}% · 最差 {min(tot):+.2f}%")
        for k, v in sorted(by_track.items()):
            print(f"  {k}：{len(v)} 笔 · 均 {sum(v)/len(v):+.2f}% · 胜率 {100*sum(1 for x in v if x>0)/len(v):.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
