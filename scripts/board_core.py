#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块核心股性票识别 · P3（2026-09-09 用户立项·设计 config/design_连板晋级量化分析.md 主线②）。

"一拉即知"拆成 4 个可算维度（对每票×所属行业板块·pool 序列窗口内）：
  时效(0.25)     = 板块涨停日里该票首封序位越靠前越好（fbt 越早序位越小）
  封板硬度(0.20) = 平均封单资金(亿·封顶5亿)+低炸板率
  连板领先(0.25) = 该票达到「板块当日最高连板」的占比（板块内最高板持有者）
  带动+参与(0.30)= 带动=该票涨停后同板块其余票跟封数均值(fbt 时间序反推) +
                   参与=板块有涨停的日子该票也在列的比例（join_rate）
核心票分 core_score = 0.25*seal + 0.20*strength + 0.25*lead + 0.30*drive(含参与)

用法:
  python3 scripts/board_core.py build --days 20        # 全行业核心票 → cache/board_core/board_core_{board}.json
  python3 scripts/board_core.py show --board 食品加工
  python3 scripts/board_core.py top --n 20 --min-zt 3  # 全市场核心票榜(至少3次涨停日参与)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CORE = ROOT / "cache" / "board_core"
sys.path.insert(0, str(HERE))
import zt_genes as zg  # noqa: E402


def _board_days(pools: dict[str, list[dict]], board: str, days: int) -> list[tuple[str, list[dict]]]:
    """板块的涨停日序列（该行业当日涨停≥1 即算一日）。"""
    out = []
    for dt in sorted(pools)[-days:]:
        lst = [x for x in pools[dt] if (x.get("industry") or "") == board]
        if lst:
            out.append((dt, lst))
    return out


def _seal_order_rank(items: list[dict]) -> dict[str, int]:
    """板块日内按首封时间升序排名（最早封=1）。None 排尾。"""
    order = sorted(items, key=lambda x: (x.get("seal_min") if x.get("seal_min") is not None else 10**6))
    return {x["code"]: i + 1 for i, x in enumerate(order)}


def core_rank(board: str, pools: dict[str, list[dict]], days: int = 20) -> list[dict]:
    """单板块核心票评分（纯函数·供单测与 CLI）。返回按 core_score 降序列表。"""
    days_lst = _board_days(pools, board, days)
    if not days_lst:
        return []
    # 统计底座
    stats: dict[str, dict] = {}
    for dt, items in days_lst:
        ranks = _seal_order_rank(items)
        n = len(items)
        max_b = max((x.get("zt_boards") or 0) for x in items)
        for x in items:
            c = x["code"]
            st = stats.setdefault(c, {"code": c, "name": x.get("name") or "",
                                      "join_days": 0, "order_sum": 0, "order_cnt": 0,
                                      "lead_days": 0, "drive_sum": 0, "drive_cnt": 0,
                                      "fund_sum": 0.0, "broken_sum": 0})
            st["join_days"] += 1
            st["order_sum"] += ranks[c]
            st["order_cnt"] += 1
            st["fund_sum"] += float(x.get("seal_fund_yi") or 0)
            st["broken_sum"] += int(x.get("broken") or 0)
            if (x.get("zt_boards") or 0) == max_b and max_b > 1:
                st["lead_days"] += 1
            # 带动：身后(封得更晚)的板块内涨停票数
            st["drive_sum"] += n - ranks[c]
            st["drive_cnt"] += 1
    total_days = len(days_lst)
    rows = []
    for c, st in stats.items():
        if st["join_days"] < 1:
            continue
        join_rate = st["join_days"] / total_days
        avg_order = st["order_sum"] / st["order_cnt"]          # 平均首封序位(1=最先)
        seal = max(0.0, 1.0 - (avg_order - 1) / max(1, st["join_days"])) * 100
        fund_avg = st["fund_sum"] / st["join_days"]
        broken_avg = st["broken_sum"] / st["join_days"]
        strength = (min(fund_avg / 5.0, 1.0) * 60 + max(0.0, 1.0 - broken_avg) * 40)
        lead = st["lead_days"] / st["join_days"] * 100
        drive_avg = st["drive_sum"] / st["drive_cnt"] if st["drive_cnt"] else 0
        drive = min(drive_avg / 3.0, 1.0) * 60 + join_rate * 40          # 带动(身后跟封)+参与率
        score = round(0.25 * seal + 0.20 * strength + 0.25 * lead + 0.30 * drive, 1)
        rows.append({"code": c, "name": st["name"], "join_days": st["join_days"],
                     "total_days": total_days, "join_rate": round(join_rate * 100, 1),
                     "avg_order": round(avg_order, 2), "seal_score": round(seal, 1),
                     "strength_score": round(strength, 1), "fund_avg_yi": round(fund_avg, 2),
                     "broken_avg": round(broken_avg, 2),
                     "lead_score": round(lead, 1), "drive_score": round(drive, 1),
                     "core_score": score})
    rows.sort(key=lambda r: -r["core_score"])
    return rows


def cmd_build(days: int) -> None:
    pools = zg._load_pools()
    if not pools:
        print("[board_core] 无 pool 数据，先 zt_genes fetch")
        return
    boards = sorted({x.get("industry") or "" for lst in pools.values() for x in lst if x.get("industry")})
    CORE.mkdir(parents=True, exist_ok=True)
    n_done = 0
    for b in boards:
        rows = core_rank(b, pools, days)
        if not rows:
            continue
        safe = b.replace("/", "_").replace(" ", "")
        (CORE / f"board_core_{safe}.json").write_text(
            json.dumps({"board": b, "days": min(days, len(pools)), "top": rows[:10],
                        "ts": __import__("datetime").datetime.now().isoformat()},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        n_done += 1
    # 全市场 core summary（每板块 top1 + 全榜前 N）
    all_top = []
    for b in boards:
        rows = core_rank(b, pools, days)
        if rows:
            rows[0]["board"] = b
            all_top.append(rows[0])
    all_top.sort(key=lambda r: -r["core_score"])
    (CORE / "summary.json").write_text(json.dumps(
        {"days": min(days, len(pools)), "boards": n_done,
         "top": [{"board": r["board"], "code": r["code"], "name": r["name"],
                  "core_score": r["core_score"], "join_rate": r["join_rate"],
                  "avg_order": r["avg_order"], "drive_score": r["drive_score"]} for r in all_top[:40]]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[board_core] {n_done} 个板块核心票已生成 → {CORE}")
    print("全市场核心票 Top15：")
    for r in all_top[:15]:
        print(f"  {r['board']:<8} {r['code']} {r['name']:<6} core={r['core_score']} "
              f"参与{r['join_rate']}% 均序位{r['avg_order']} 带动分{r['drive_score']}")


def cmd_show(board: str, days: int) -> None:
    pools = zg._load_pools()
    rows = core_rank(board, pools, days)
    print(f"[board_core] {board} 核心票（窗口 {min(days, len(pools))} 交易日·Top10）")
    print(f"{'代码':<8}{'名称':<8}{'参与率':>7}{'均首封序':>8}{'核心分':>7}")
    for r in rows[:10]:
        print(f"{r['code']:<9}{r['name']:<8}{r['join_rate']:>6}%{r['avg_order']:>8}{r['core_score']:>7}")


def cmd_top(n: int, min_zt: int, days: int) -> None:
    pools = zg._load_pools()
    boards = sorted({x.get("industry") or "" for lst in pools.values() for x in lst if x.get("industry")})
    cands = []
    for b in boards:
        for r in core_rank(b, pools, days):
            if r["join_days"] >= min_zt:
                r["board"] = b
                cands.append(r)
    cands.sort(key=lambda r: -r["core_score"])
    print(f"[board_core] 全市场核心票 Top{n}（涨停日参与≥{min_zt}·窗口{min(days, len(pools))}日）")
    print(f"{'板块':<8}{'代码':<8}{'名称':<8}{'参与日':>6}{'核心分':>7}")
    for r in cands[:n]:
        print(f"{r['board']:<8}{r['code']:<9}{r['name']:<8}{r['join_days']:>6}{r['core_score']:>7}")


def main() -> None:
    ap = argparse.ArgumentParser(description="板块核心股性票识别 P3")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--days", type=int, default=20)
    s = sub.add_parser("show"); s.add_argument("--board", required=True); s.add_argument("--days", type=int, default=20)
    t = sub.add_parser("top"); t.add_argument("--n", type=int, default=20)
    t.add_argument("--min-zt", type=int, default=3); t.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "build":
        cmd_build(a.days)
    elif a.cmd == "show":
        cmd_show(a.board, a.days)
    elif a.cmd == "top":
        cmd_top(a.n, a.min_zt, a.days)


if __name__ == "__main__":
    main()
