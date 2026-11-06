#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排序权重验证：`line_is_core` / `focus` 该不该排在「竞价 A 态」之前？

## 为什么查这个（2026-11-06 用户拍板，但要求先验证）

11-06 的反事实试算里，放开防守腿后系统会买：
    · 皇台酒业 000995（大消费唯一板，`line_is_core=1`）→ 当日 **−9.41%**
    · 德才股份 605287（短剧最高板，`line_is_core=1`）→ 当日 **−2.49%**
而竞价 A 态的东方海洋（+8.17%）、润建股份（+5.68%）被排到后面没进名额。
单日反例不足以定策（项目纪律），故本脚本用冻结件 + 竞价快照 + 真实日线重测。

## 数据口径（⛔ 全部为事前可得 + 当日真实成交价）

- 候选集：`wealth_code/cache/pool_call-<T>.json` 的 `views`（当��� 09:25 真实冻结件）
- 竞价读数：`wealth_code/cache/auction_quotes-<T>.json`（`auction_price` / `auction_amount_yi`）
- 昨收质量：`cache/zt_history/zt_<T-1>.json` 的 `zt_list`（fbt/zbc/hs/seal/cje_yi/boards/is_20cm）
- A 态：`core.auction_state.classify`，预期开幅由 `core.expected_gap.expected_gap_pct` 现算
- 收益：`cache/kline_qfq/<code>.json`
    · 当日口径 = T 日 `close/open − 1`（09:25 竞价成交 → 当日收盘）
    · T+1 口径 = (T+1 `close`) / (T `open`) − 1（与账本一致）
- ⛔ 剔除「竞价封死买不进」（gap ≥ 涨停幅度 − 0.2pct）与无行情票

## 输出

A. 四象限：A 态 × 同线最高板 → 谁的边际更值钱
B. 重排对比：现行 key vs 「A 态提到 core 之前」key，逐日 top-1/2/3 的收益差
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WC = ROOT / "wealth_code"
sys.path.insert(0, str(WC))
sys.path.insert(0, str(ROOT))

from core import ai_caliber as ac          # noqa: E402
from core import auction_state as ast     # noqa: E402
from core import expected_gap as eg       # noqa: E402

KLINE = ROOT / "cache/kline_qfq"
ZT = ROOT / "cache/zt_history"
POOL = WC / "cache"
OUT = ROOT / "cache/research/rank_priority_bt.json"


def jl(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _f(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def zt_days() -> list[str]:
    return sorted(p.stem[3:] for p in ZT.glob("zt_*.json") if len(p.stem) == 13)


def bars(code: str) -> dict[str, dict]:
    d = jl(KLINE / f"{code}.json") or {}
    return {str(b.get("date"))[:10]: b for b in (d.get("bars") or []) if b.get("date")}


def build_rows() -> tuple[list[dict], list[str]]:
    """逐「冻结票」重建的事实行 + 缺数据说明。"""
    days = zt_days()
    rows: list[dict] = []
    notes: list[str] = []
    for f in sorted(POOL.glob("pool_call-*.json")):
        day = f.stem.replace("pool_call-", "")
        prev = max((d for d in days if d < day), default=None)
        if prev is None:
            notes.append(f"{day}: 无前一交易日涨停归档")
            continue
        snap = jl(f) or {}
        views = snap.get("views") or {}
        quotes = jl(POOL / f"auction_quotes-{day}.json") or {}
        prev_rows = (jl(ZT / f"zt_{prev}.json") or {}).get("zt_list") or []
        if not prev_rows:
            notes.append(f"{day}: 昨池 {prev} 为空壳")
            continue
        by_code = {str(r.get("code") or "").strip(): r for r in prev_rows}
        mm = max((int(_f(r.get("boards")) or 0) for r in prev_rows), default=0)
        hit = miss = 0
        for code, v in views.items():
            if not isinstance(v, dict) or v.get("view") == "skip":
                continue          # skip = 盘后已否决，不会进排序
            q = quotes.get(code)
            pr = by_code.get(code)
            if not q or not pr:
                miss += 1
                continue
            au, pc = _f(q.get("auction_price")), _f(q.get("prev_close"))
            if not au or not pc:
                miss += 1
                continue
            gap = (au / pc - 1) * 100
            is20 = bool(pr.get("is_20cm"))
            lim = ac.limit_up_pct(is20)
            exp = eg.expected_gap_pct(pr)
            cls = ast.classify(gap_pct=gap, amount_yi=q.get("auction_amount_yi"),
                               prev_amount_yi=pr.get("cje_yi"),
                               expected_gap_pct=exp, max_gap_pct=lim)
            state = cls.get("auction_state")
            if gap >= lim - 0.2:
                miss += 1
                continue          # 竞价封死，买不进
            b = bars(code)
            bd = b.get(day)
            nxt = next((d for d in sorted(b) if d > day), None)
            ret_d = ret_n = None
            if bd:
                o, c = _f(bd.get("open")), _f(bd.get("close"))
                if o and c:
                    ret_d = (c / o - 1) * 100
            if bd and nxt:
                o, c2 = _f(bd.get("open")), _f(b.get(nxt, {}).get("close"))
                if o and c2:
                    ret_n = (c2 / o - 1) * 100
            if ret_d is None and ret_n is None:
                miss += 1
                continue
            boards = int(_f(v.get("boards")) or 0)
            gw = ac.gap_weight(gap, expected_gap_pct=exp, is_20cm=is20)
            rows.append({
                "date": day, "code": code, "name": v.get("name"),
                "boards": boards, "gap": round(gap, 2),
                "state": state, "dev": cls.get("gap_vs_expected"),
                "r": cls.get("auction_r"),
                "line_is_core": bool(v.get("line_is_core")),
                "bias_focus": str(v.get("bias") or "") == "focus",
                "mech": bool(v.get("mechanical_leader")),
                "relay_mood": v.get("relay_mood"),
                "line_rank": v.get("line_rank"),
                "gap_to_top": (mm - boards) if mm else None,
                "gap_weight": (gw.get("weight") if isinstance(gw, dict) else 1.0),
                "view": v.get("view"),
                "ret_d": round(ret_d, 2) if ret_d is not None else None,
                "ret_n": round(ret_n, 2) if ret_n is not None else None,
            })
            hit += 1
        notes.append(f"{day}: 昨池 {prev} n={len(prev_rows)} 市场最高 {mm} 板；"
                     f"入样 {hit} / 缺数据 {miss}")
    return rows, notes


def agg(rs: list[dict], key: str = "ret_d") -> dict:
    v = [r[key] for r in rs if r.get(key) is not None]
    if not v:
        return {"n": 0}
    return {"n": len(v), "avg": round(st.mean(v), 2), "med": round(st.median(v), 2),
            "win": round(sum(1 for x in v if x > 0) / len(v) * 100, 1)}


def _key_old(x: dict) -> tuple:
    """现行排序键（`core/pool_call._key` 的简化复刻，去掉需要 leader_evidence 的 priority）。"""
    return (
        1 if x.get("gap_to_top") == 0 else 0,          # is_top_board
        1 if x.get("line_is_core") else 0,             # ← 本次争点
        {"supportive": 1, "pressured": -1}.get(x.get("relay_mood"), 0),
        1 if (x.get("bias_focus") or x.get("mech")) else 0,   # ← 本次争点
        int(x.get("boards") or 0),
        -int(x.get("line_rank") or 99),
        1 if x.get("state") == "A" else 0,
        x.get("gap_weight") if isinstance(x.get("gap_weight"), (int, float)) else 1.0,
    )


def _key_new(x: dict) -> tuple:
    """把 line_is_core / focus 挪到「竞价 A 态」之后（保留前两档与板数身位）。"""
    return (
        1 if x.get("gap_to_top") == 0 else 0,
        1 if x.get("state") == "A" else 0,             # ← A 态提前
        1 if x.get("line_is_core") else 0,
        {"supportive": 1, "pressured": -1}.get(x.get("relay_mood"), 0),
        1 if (x.get("bias_focus") or x.get("mech")) else 0,
        int(x.get("boards") or 0),
        -int(x.get("line_rank") or 99),
        x.get("gap_weight") if isinstance(x.get("gap_weight"), (int, float)) else 1.0,
    )


def main() -> int:
    rows, notes = build_rows()
    print("=== 数据构建 ===")
    for n in notes:
        print("  ", n)
    print(f"\n入样票次 {len(rows)}｜交易日 {len({r['date'] for r in rows})}")
    if not rows:
        print("⛔ 无样本，终止")
        return 1

    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)

    print("\n=== A. 四象限：A 态 × 同线最高板（当日 close/open−1）===")
    quads = {
        "A态 & 同线最高板": [r for r in rows if r["state"] == "A" and r["line_is_core"]],
        "A态 & 非最高板": [r for r in rows if r["state"] == "A" and not r["line_is_core"]],
        "非A & 同线最高板": [r for r in rows if r["state"] != "A" and r["line_is_core"]],
        "非A & 非最高板": [r for r in rows if r["state"] != "A" and not r["line_is_core"]],
    }
    for k, rs in quads.items():
        a = agg(rs)
        print(f"  {k:<18} n={a['n']:<4} 平均 {a.get('avg', 0):+6.2f}%  中位 "
              f"{a.get('med', 0):+6.2f}%  胜率 {a.get('win', 0):5.1f}%")

    print("\n=== A2. 单因子边际（同日对照）===")
    for lab, key, val in (("A 态", "state", "A"), ("同线最高板", "line_is_core", True)):
        y = [r for r in rows if r[key] == val]
        n = [r for r in rows if r[key] != val]
        ay, an = agg(y), agg(n)
        if ay["n"] and an["n"]:
            print(f"  {lab:<10} 命中 n={ay['n']:<4} {ay['avg']:+6.2f}%/胜{ay['win']:5.1f}%"
                  f" ｜未命中 n={an['n']:<4} {an['avg']:+6.2f}%/胜{an['win']:5.1f}%"
                  f" ｜差 {ay['avg'] - an['avg']:+.2f}pct")

    print("\n=== B. 重排对比（逐日取 top-K，当日口径）===")
    out_days = []
    for day in sorted(by_day):
        rs = by_day[day]
        if len(rs) < 2:
            continue
        so = sorted(rs, key=_key_old, reverse=True)
        sn = sorted(rs, key=_key_new, reverse=True)
        rec = {"date": day, "n": len(rs)}
        for k in (1, 2, 3):
            ko = [r for r in so[:k] if r.get("ret_d") is not None]
            kn = [r for r in sn[:k] if r.get("ret_d") is not None]
            rec[f"old{k}"] = round(st.mean([r["ret_d"] for r in ko]), 2) if ko else None
            rec[f"new{k}"] = round(st.mean([r["ret_d"] for r in kn]), 2) if kn else None
            rec[f"old{k}_codes"] = [r["code"] for r in so[:k]]
            rec[f"new{k}_codes"] = [r["code"] for r in sn[:k]]
        rec["changed"] = rec["old1_codes"] != rec["new1_codes"]
        out_days.append(rec)

    for k in (1, 2, 3):
        o = [d[f"old{k}"] for d in out_days if d.get(f"old{k}") is not None]
        n = [d[f"new{k}"] for d in out_days if d.get(f"new{k}") is not None]
        if not o or not n:
            continue
        diff = [d[f"new{k}"] - d[f"old{k}"] for d in out_days
                if d.get(f"old{k}") is not None and d.get(f"new{k}") is not None]
        better = sum(1 for x in diff if x > 0)
        print(f"  top-{k}：现行 日均 {st.mean(o):+6.2f}%（日胜 {sum(1 for x in o if x > 0)/len(o)*100:4.1f}%）"
              f" ｜ 新序 {st.mean(n):+6.2f}%（日胜 {sum(1 for x in n if x > 0)/len(n)*100:4.1f}%）"
              f" ｜ 差 {st.mean(diff):+.2f}pct，新序更好 {better}/{len(diff)} 天")

    ch = [d for d in out_days if d["changed"]]
    print(f"\n  首名发生变化的交易日：{len(ch)}/{len(out_days)}")
    for d in ch:
        print(f"    {d['date']}  现行→ 新序 "
              f"{'/'.join(d['old1_codes'])} → {'/'.join(d['new1_codes'])}"
              f"  {d['old1']:+.2f}% → {d['new1']:+.2f}%")

    print("\n=== C. T+1 口径（与账本一致）===")
    for k in (1, 2):
        o, n = [], []
        for day in sorted(by_day):
            rs = by_day[day]
            if len(rs) < 2:
                continue
            for keyf, box in ((_key_old, o), (_key_new, n)):
                sel = [r for r in sorted(rs, key=keyf, reverse=True)[:k]
                       if r.get("ret_n") is not None]
                if sel:
                    box.append(st.mean([r["ret_n"] for r in sel]))
        if o and n:
            print(f"  top-{k}：现行 {st.mean(o):+6.2f}% ｜ 新序 {st.mean(n):+6.2f}%"
                  f" ｜ 差 {st.mean(n) - st.mean(o):+.2f}pct")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated": "scripts/research_rank_priority.py",
        "rows": rows, "notes": notes,
        "quads": {k: agg(v) for k, v in quads.items()},
        "days": out_days,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n产物 → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
