#!/usr/bin/env python3
"""单票判断轨迹 CLI（judgement_trail）。

⛔ 自插项目根 + wealth_code：脚本模式下 cwd 不入 sys.path，`from pipelines…` 会
   ModuleNotFoundError（本项目已踩 3 次，见 config/shared_rules.md）。

用法：
    python3 wealth_code/scripts/judgement_trail.py rebuild [--since YYYY-MM-DD]
    python3 wealth_code/scripts/judgement_trail.py show 603278 [--window 90]
    python3 wealth_code/scripts/judgement_trail.py stats
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "wealth_code")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import judgement_trail as jt  # noqa: E402


def cmd_rebuild(args) -> int:
    idx = jt.rebuild(since=args.since)
    print(f"[TRAIL] 重建完成：{idx['generated_dates']} 个交易日 / {idx['rows']} 行 / "
          f"{idx['codes']} 只票 · 区间 {idx.get('range')}")
    print(f"[TRAIL] 进池 {idx['in_pool']} 行 · 被成交额门槛挡下 {idx['gate_below_floor']} 行")
    if idx["days_without_pool"]:
        print(f"[TRAIL] 缺涨停池快照：{idx['days_without_pool']}")
    print(f"[TRAIL] 产物：{jt.OUT_DIR}")
    return 0


def cmd_show(args) -> int:
    rows = jt.query(args.code)
    if not rows:
        print(f"[TRAIL] {args.code} 在轨迹里没有任何记录（不在任何一天的涨停池）")
        return 1
    rows = rows[-args.window:]
    name = next((r["name"] for r in rows if r.get("name")), args.code)
    print(f"\n{name} {args.code} · 涨停轨迹 {len(rows)} 天（{rows[0]['date']} → {rows[-1]['date']}）\n")
    head = f"{'日期':<11}{'板':>3} {'封板':>8} {'封单亿':>7} {'换手%':>7} {'成交亿':>7} {'门槛':>5} {'池':<6}{'分':>6}  标签 / 结论"
    print(head)
    print("-" * len(head.encode("gbk", errors="ignore")))
    for r in rows:
        pool = {"relay": "接力", "watch": "观察"}.get(r.get("pool") or "", "池外")
        if r.get("gate") == "below_floor":
            gate = "✗"
        elif r.get("gate") == "pass":
            gate = "✓"
        else:
            gate = "?"
        score = f"{r['score']:.1f}" if r.get("score") is not None else "—"
        tags = "·".join(r.get("mech") or [])
        verdict = r.get("verdict")
        tail = f"{tags}" + (f" → {verdict}" if verdict else "")
        print(f"{r['date']:<11}{r['boards']:>3} {str(r.get('fbt') or '—'):>8} "
              f"{r['seal_yi']:>7.2f} {r['hs_pct']:>7.2f} {r['cje_yi']:>7.2f} {gate:>5} "
              f"{pool:<6}{score:>6}  {tail}")

    hit = [r for r in rows if r.get("pool")]
    print(f"\n进池 {len(hit)}/{len(rows)} 天"
          + ("；" + "、".join(f"{r['date']} {r['boards']}板 {r['score']:.1f}分→{r.get('verdict') or r.get('pool')}" for r in hit)
             if hit else "（从未进池）"))
    blocked = [r for r in rows if r.get("gate") == "below_floor"]
    if blocked:
        print("被成交额门槛挡下：" + "、".join(
            f"{r['date']} {r['boards']}板 {r['cje_yi']:.2f}亿<{r['floor_yi']}亿" for r in blocked))
    return 0


def cmd_stats(args) -> int:
    idx = json.loads((jt.OUT_DIR / "index.json").read_text(encoding="utf-8"))
    print(json.dumps({k: v for k, v in idx.items() if k != "per_day"}, ensure_ascii=False, indent=1))
    print("\n逐日：")
    for d in idx.get("per_day") or []:
        print(f"  {d['date']}  {d.get('rows', 0):>3} 行  {d.get('note', '')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="单票判断轨迹")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rebuild", help="从归档全量重建轨迹（幂等）")
    p.add_argument("--since", help="只输出该日期之后的轨迹")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("show", help="看某只票的轨迹")
    p.add_argument("code")
    p.add_argument("--window", type=int, default=90)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("stats", help="看轨迹库概况")
    p.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
