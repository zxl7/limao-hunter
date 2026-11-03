#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_prev_boards.py — 效应路径「昨日板数」与日线事实对账（纯只读审计 · 2026-10-27）

背景（为什么要有它）：
  `cache/effect_paths_{date}.json` 的每一只票都带 `prev_boards`（昨日连板数），
  它直接决定分桶：`prev_boards >= HIGH_BOARDS` 才会进「前排龙头 / 高度衰减」，
  否则进「低位失败 / 杂毛」。**板数错 → 归因错 → 赚钱效应结论错。**

  2026-10-26 实测（本脚本跑出来的）：83 只带板数的票里 3 只与日线不符，其中
  百洋股份 002696 标 7 板、实际 0 板（10-23 收 +4.49% 未涨停），被塞进
  「前排龙头·高位继续封板」。7 这个数从上游 feedback_matrix 快照里来，
  但对应日期的昨日池里根本没有它 —— 疑为跨波累加，**根因尚未定位**。

定位：只核对、只报告、**不修数据、不改归因**。
      根因没查清之前，任何自动「纠正」都是拿假数据换假账。

口径（唯一真源）：
  板数 = 截至 asof 日收盘、**连续**涨幅达涨停的交易日数，断板即归零。
  用 cache/kline_qfq/{code}.json（前复权日线）自算，不依赖任何上游 boards 字段。
  ⛔ 涨停阈值按板块：688/300/301 为 20cm（≈19.5%），其余 10cm（≈9.7%）。

用法：
  python3 scripts/audit_prev_boards.py                    # 审计最近一份 effect_paths
  python3 scripts/audit_prev_boards.py --date 2026-10-26  # 指定日期
  python3 scripts/audit_prev_boards.py --json             # 机器可读
"""
import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
CACHE = ROOT / "cache"
KLINE = CACHE / "kline_qfq"


def _prev_trading_day(d: str) -> str:
    """d 的前一交易日（唯一真源 common.trading_calendar；失败才退回工作日近似）。"""
    try:
        from datetime import date as _d, timedelta as _td
        from common.trading_calendar import prev_a_share_trading_day
        return prev_a_share_trading_day(_d.fromisoformat(d)).isoformat()
    except Exception:
        # trading-day-fallback: 仅日历不可用时兜底
        from datetime import date as _d, timedelta as _td
        x = _d.fromisoformat(d) - _td(days=1)
        while x.weekday() >= 5:
            x -= _td(days=1)
        return x.isoformat()


def limit_pct(code: str) -> float:
    """该票的涨停阈值（%）。20cm 板块按 19.5 兜住四舍五入误差。"""
    c = str(code).split(".")[0]
    if c.startswith(("688", "300", "301", "689")):
        return 19.5
    return 9.7


def true_boards(code: str, asof: str) -> int | None:
    """截至 asof 收盘的连续涨停天数；无K线/无该日 → None（不猜）。"""
    f = KLINE / f"{str(code).split('.')[0]}.json"
    if not f.exists():
        return None
    try:
        bars = json.loads(f.read_text(encoding="utf-8")).get("bars") or []
    except Exception:
        return None
    idx = next((i for i, b in enumerate(bars) if b.get("date") == asof), None)
    if idx is None or idx == 0:
        return None
    th = limit_pct(code)
    n = 0
    i = idx
    while i > 0:
        prev_c = bars[i - 1].get("close")
        cur_c = bars[i].get("close")
        if not prev_c:
            break
        if (cur_c / prev_c - 1) * 100 >= th:
            n += 1
            i -= 1
        else:
            break
    return n


def audit(date: str | None = None, as_json: bool = False) -> dict:
    if not date:
        files = sorted(glob.glob(str(CACHE / "effect_paths_*.json")))
        if not files:
            return {"error": "无 effect_paths 产物"}
        date = Path(files[-1]).stem.replace("effect_paths_", "")

    p = CACHE / f"effect_paths_{date}.json"
    if not p.exists():
        return {"error": f"缺 {p.name}"}
    ep = json.loads(p.read_text(encoding="utf-8"))
    raw_prev = ep.get("prev_date") or ""

    # ⛔ 2026-10-27 实测：10-19～10-26 的产物 **prev_date 全部等于 date**（字段被上游污染），
    #    只有 10-27 是对的。板数基准日不能信这个字段 → 一律用交易日历自算。
    base = _prev_trading_day(date)
    prev_date_warn = None
    if raw_prev and raw_prev >= date:
        prev_date_warn = f"产物 prev_date={raw_prev} 与 date 同日（字段错位），已按交易日历改用 {base}"

    rows, ok, miss = [], 0, 0
    for b in ep.get("buckets") or []:
        for s in b.get("stocks") or []:
            marked = s.get("prev_boards")
            if marked is None:
                continue
            tb = true_boards(s.get("code"), base)
            if tb is None:
                miss += 1
                continue
            if int(marked) == tb:
                ok += 1
            else:
                rows.append({
                    "bucket": b.get("label"), "code": s.get("code"), "name": s.get("name"),
                    "marked": int(marked), "actual": tb, "delta": int(marked) - tb,
                })

    out = {
        "date": date, "base_date": base, "prev_date_field": raw_prev,
        "prev_date_warn": prev_date_warn,
        "checked": ok + len(rows), "match": ok, "mismatch": len(rows), "no_kline": miss,
        "match_rate": round(ok / (ok + len(rows)) * 100, 1) if (ok + len(rows)) else None,
        "rows": rows,
    }
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return out

    print(f"[audit_prev_boards] {date}（板数基准日 {base}，按交易日历自算）")
    if prev_date_warn:
        print(f"  ⚠️ {prev_date_warn}")
    print(f"  核对 {out['checked']} 只｜一致 {ok}｜**不符 {len(rows)}**｜无日线 {miss}"
          + (f"｜一致率 {out['match_rate']}%" if out["match_rate"] is not None else ""))
    if rows:
        print("  不符明细：")
        for r in rows:
            print(f"    {r['code']} {r['name']:6s} 标记{r['marked']}板 → 实际{r['actual']}板"
                  f"（差{r['delta']:+d}）[{r['bucket']}]")
        print("  ⚠️ 板数决定分桶，分桶错则赚钱效应归因错 → 需定位上游 feedback_matrix 快照")
    return out


def main():
    ap = argparse.ArgumentParser(description="效应路径板数与日线对账（只读）")
    ap.add_argument("--date", help="效应路径日期 YYYY-MM-DD（缺省=最新一份）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()
    r = audit(a.date, a.json)
    if r.get("error"):
        print(f"[audit_prev_boards] {r['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
