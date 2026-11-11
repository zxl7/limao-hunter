#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连板票生命周期台账（2026-11-02 用户「连板数>2板的个股长期跟踪，直到它断板为止」）。

## 为什么再落一份台账（与 board_leader_daily 的分工）

| 台账 | 视角 | 回答的问题 |
|---|---|---|
| `board_leader_daily/leader_<date>.json` | **板块** × 交易日 | 这条线今天还是不是同一个人在带？换没换人？ |
| `board_track/track_<date>.json`（本文件） | **个股** × 交易日 | 这只票从几板起、走到第几板、今天断没断、明天能不能接？ |

两者互补，⛔ 不互相替代：板块台账告诉你「土壤」，个股台账告诉你「这棵树还活着吗」。

## 口径（全部只读本地归档，禁编造）

- 涨停池序列 = `cache/zt_history/zt_<YYYY-MM-DD>.json`（仅有效完整收盘池，非文件存在即有数据）。
- 题材/身位 = `cache/theme_snapshot/theme_<date>.json` 的当日冻结映射 → `core.sector_leader.compute`
  （单一源，与选股/板块台账同一答案）。快照缺失 → 标 `theme_source=sector_fallback`。
- **在册** = 当日涨停且 `boards >= 2`。**断板** = 昨日在册、今日不在涨停池。
- **首板日** 用序列回溯：逐日 `boards` 递减 1 且相邻交易日连续 → 一直退到 1 板那天。
  回溯不连续（中间断过）→ `streak_clean=False`，说明这是断板后重启的一段（N 字二波）。
- **晋级率证据**只用 `date < 当日` 的历史事件，⛔ 禁未来函数（同日样本不进分母）。

## 因子档（4 条·来自 2026-08-20~11-02 的 293 个 2 板样本实测区分度）

| 档 | 判据（前一交易日收盘即可知） | 实测 2进3 晋级率 |
|---|---|---|
| 早封 | 首封 `fbt` ≤ 09:30 | 47%（vs 晚于 10:00 的 23%） |
| 厚封 | 封单 ≥ 1 亿 | 44%（vs <0.3 亿 20%） |
| 低换手 | 换手 < 10% | 40%（vs ≥20% 的 16%） |
| 零炸板 | `zbc == 0` | 39%（vs ≥2 次 20%） |

命中 3~4 条 = 39 个样本 56%，命中 0~1 条 ≈ 25%。**这是条件概率，不是打分**——
输出里必须带 `n`，样本 <15 标 `weak`（诚实降级，不许当结论喊）。

## 用法

```bash
python3 scripts/board_track.py build                  # 最近交易日
python3 scripts/board_track.py build --date 2026-11-02
python3 scripts/board_track.py backfill --days 30     # 回填序列
python3 scripts/board_track.py show [--date]
python3 scripts/board_track.py lifecycle --code 001219
python3 scripts/board_track.py stats --stage 2        # 条件晋级率表
```

产物：`cache/board_track/track_<YYYY-MM-DD>.json`（幂等覆盖·不写生产链）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / "cache"
ZT_DIR = CACHE / "zt_history"
THEME_DIR = CACHE / "theme_snapshot"
OUT_DIR = CACHE / "board_track"

WEALTH = ROOT / "wealth_code"
if str(WEALTH) not in sys.path:
    sys.path.insert(0, str(WEALTH))

from core import sector_leader as sl   # noqa: E402
from core.pool_call import _read_zt, _zt_rows   # noqa: E402

BJ_TZ = timezone(timedelta(hours=8))
SCHEMA = "board_track.v1"
MIN_BOARDS = 2          # 入册门槛（用户口径：2 板以上才跟）
WEAK_N = 15             # 样本数低于此 → weak

# 因子档阈值（与 skills/龙头战法交易员 的口径表同源）
EARLY_SEAL_MAX_MIN = 570        # 09:30
FUND_THICK_YI = 1.0
HS_LOW_PCT = 10.0
ZBC_CLEAN = 0


def _load_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def trade_dates() -> list[str]:
    """有效完整收盘池日期；盘中统计、缺名单和损坏归档不计入。"""
    out = []
    for p in sorted(ZT_DIR.glob("zt_*.json")):
        d = p.stem[3:]
        if _read_zt(p) is not None:
            out.append(d)
    return out


def _rows(date: str) -> list[dict]:
    pool = _read_zt(ZT_DIR / f"zt_{date}.json")
    if pool is None:
        raise ValueError(f"{date} 缺少有效完整收盘涨停池，不能认定断板")
    rows = _zt_rows(pool)
    # 兼容旧归档别名，后续统计仍使用统一的 code / boards 字段。
    return [{**r, "code": str(r.get("code") or r.get("dm") or "").strip(),
             "boards": next((r[k] for k in ("boards", "lbc", "lianban") if k in r), 0),
             "name": r.get("name") or r.get("mc")} for r in rows]


def _theme_map(date: str) -> tuple[dict, str]:
    d = _load_json(THEME_DIR / f"theme_{date}.json")
    m = d.get("mapping")
    if isinstance(m, dict) and m:
        return m, "snapshot"
    return {}, "missing"


def _fbt_min(fbt: str) -> int | None:
    """'092500' → 565（当日分钟数）。解析失败 → None（不填 0）。"""
    s = str(fbt or "").strip()
    if len(s) != 6 or not s.isdigit():
        return None
    return int(s[:2]) * 60 + int(s[2:4])


def factor_hits(row: dict) -> dict:
    """4 条因子档命中情况（只用前一交易日收盘已知字段）。"""
    fmin = _fbt_min(row.get("fbt"))
    seal = row.get("seal")
    hs = row.get("hs")
    zbc = row.get("zbc")
    hits = {
        "early_seal": bool(fmin is not None and fmin <= EARLY_SEAL_MAX_MIN),
        "thick_seal": bool(isinstance(seal, (int, float)) and seal >= FUND_THICK_YI),
        "low_turnover": bool(isinstance(hs, (int, float)) and 0 <= hs < HS_LOW_PCT),
        "clean_seal": bool(zbc == ZBC_CLEAN),
    }
    return {**hits, "count": sum(1 for v in hits.values() if v)}


# ── 事件层：全序列的 N进N+1 结果（用于条件晋级率，禁未来函数）─────────────────
def build_events(dates: list[str]) -> list[dict]:
    """相邻交易日事件表：每条 = (prev_date, code, prev_boards, ok)。"""
    evs: list[dict] = []
    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        nxt = {str(r.get("code")): r for r in _rows(d1)}
        for r in _rows(d0):
            code = str(r.get("code") or "")
            b = int(r.get("boards") or 0)
            if not code or b < 1:
                continue
            nb = int((nxt.get(code) or {}).get("boards") or 0)
            evs.append({
                "date": d0, "next_date": d1, "code": code,
                "name": r.get("name"), "prev_boards": b, "boards": nb,
                "ok": nb > b, "row": r,
            })
    return evs


def cond_rate(stage: int, hits: int, before: str, evs: list[dict]) -> dict:
    """同梯次 × 同命中数 的历史晋级率（**只用 before 之前的事件**）。"""
    sample = [bool(e["ok"]) for e in evs
              if int(e["prev_boards"]) == stage and e["date"] < before
              and factor_hits(e["row"])["count"] == hits]
    n = len(sample)
    if not n:
        return {"n": 0, "rate": None, "quality": "no_sample"}
    return {"n": n, "ok": sum(sample), "rate": round(sum(sample) / n * 100, 1),
            "quality": "ok" if n >= WEAK_N else "weak"}


def stage_base_rate(stage: int, before: str, evs: list[dict]) -> dict:
    sample = [bool(e["ok"]) for e in evs
              if int(e["prev_boards"]) == stage and e["date"] < before]
    n = len(sample)
    if not n:
        return {"n": 0, "rate": None, "quality": "no_sample"}
    return {"n": n, "rate": round(sum(sample) / n * 100, 1),
            "quality": "ok" if n >= WEAK_N else "weak"}


# ── 生命周期：从首板日回溯 ─────────────────────────────────────────────────
def lifecycle(dates: list[str], code: str) -> list[dict]:
    """该票的连板段序列（连续涨停链，遇到断档即断开）。"""
    seg: list[dict] = []
    for d in dates:
        row = next((r for r in _rows(d) if str(r.get("code")) == code), None)
        if not row:
            continue
        seg.append({"date": d, "boards": int(row.get("boards") or 0),
                    "fbt": row.get("fbt"), "zbc": row.get("zbc"),
                    "seal": row.get("seal"), "hs": row.get("hs"),
                    "pct": row.get("pct")})
    # 切成若干连续段：板数从 1 起、逐日 +1，否则断开
    out: list[list[dict]] = []
    cur: list[dict] = []
    prev = None
    order = {d: i for i, d in enumerate(dates)}
    for x in seg:
        contiguous = (prev is not None and order.get(x["date"], -99) - order.get(prev["date"], -99) == 1
                      and x["boards"] == prev["boards"] + 1)
        if prev is not None and not contiguous:
            out.append(cur)
            cur = []
        cur.append(x)
        prev = x
    if cur:
        out.append(cur)
    return out


def _track_meta(dates: list[str], date: str, code: str) -> dict:
    """在册天数 = 连续涨停链里到今日的天数（含今日）。

    `truncated=True` 表示这段链的起点是**归档首日**（池里第一天就已经不是 1 板），
    真实首板日早于数据边界 → `days_in_track` 只是「至少这么多天」，⛔ 不许当准确值引用。
    """
    for seg in lifecycle(dates, code):
        if seg and seg[-1]["date"] == date:
            first_boards = seg[0]["boards"]
            return {"first_date": seg[0]["date"], "first_boards": first_boards,
                    "days_in_track": len(seg), "streak_clean": first_boards == 1,
                    "truncated": first_boards != 1,
                    "seg_len": len(seg)}
    return {"first_date": date, "first_boards": None, "days_in_track": 1,
            "streak_clean": False, "truncated": True, "seg_len": 1}


def build_for_date(date: str, dates: list[str] | None = None,
                   evs: list[dict] | None = None) -> dict:
    dates = dates or trade_dates()
    evs = evs if evs is not None else build_events(dates)
    rows = _rows(date)
    tmap, tsrc = _theme_map(date)
    res = sl.compute(rows, tmap or None)
    by_code = res.get("by_code") or {}
    today_codes = {str(r.get("code")) for r in rows}

    tracked: list[dict] = []
    for r in rows:
        b = int(r.get("boards") or 0)
        if b < MIN_BOARDS:
            continue
        code = str(r.get("code") or "")
        pos = by_code.get(code) or {}
        hits = factor_hits(r)
        meta = _track_meta(dates, date, code)
        tracked.append({
            "code": code, "name": r.get("name"), "boards": b,
            "theme": pos.get("theme") or r.get("sector") or "",
            "is_sector_dragon": bool(pos.get("is_sector_dragon")),
            "dragon_rank": pos.get("dragon_rank"),
            "mates": pos.get("mates"),
            "fbt": r.get("fbt"), "fbt_min": _fbt_min(r.get("fbt")),
            "seal_yi": r.get("seal"), "seal_ratio_pct": pos.get("seal_ratio_pct"),
            "zbc": r.get("zbc"), "hs": r.get("hs"),
            "cje_yi": r.get("cje_yi"), "lt_yi": r.get("lt_yi"),
            "is_20cm": bool(r.get("is_20cm")),
            "track": meta,
            "factors": hits,
            "hist": {
                "stage": f"{b}to{b + 1}",
                "cond": cond_rate(b, hits["count"], date, evs),
                "base": stage_base_rate(b, date, evs),
            },
        })

    tracked.sort(key=lambda x: (-x["boards"], -(x["factors"]["count"] or 0)))

    # 断板：昨日在册、今日不在涨停池
    prev_date = next((d for d in reversed(dates) if d < date), "")
    broken: list[dict] = []
    if prev_date:
        for r in _rows(prev_date):
            pb = int(r.get("boards") or 0)
            code = str(r.get("code") or "")
            if pb < MIN_BOARDS or code in today_codes:
                continue
            segs = [s for s in lifecycle(dates, code) if s[-1]["date"] == prev_date]
            broken.append({
                "code": code, "name": r.get("name"), "prev_boards": pb,
                "last_date": prev_date,
                "days_in_track": len(segs[0]) if segs else None,
                "first_date": segs[0][0]["date"] if segs else None,
                "sector": r.get("sector"),
            })

    max_b = max((int(r.get("boards") or 0) for r in rows), default=0)
    return {
        "schema": SCHEMA,
        "date": date,
        "prev_date": prev_date,
        "generated_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "theme_source": tsrc,
        "degraded": tsrc != "snapshot",
        "market": {"zt_count": len(rows), "max_boards": max_b,
                   "tracked_count": len(tracked), "broken_count": len(broken),
                   "history_days": len({e["date"] for e in evs})},
        "tracked": tracked,
        "broken": broken,
    }


def out_path(date: str) -> Path:
    return OUT_DIR / f"track_{date}.json"


def cmd_build(date: str | None = None, write: bool = True) -> dict:
    dates = trade_dates()
    if not dates:
        raise SystemExit("❌ 没有涨停池归档（cache/zt_history/zt_*.json）")
    date = date or dates[-1]
    if date not in dates:
        raise SystemExit(f"❌ {date} 没有涨停池归档（最近：{dates[-1]}）")
    data = build_for_date(date, dates)
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path(date).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def cmd_show(date: str | None = None) -> None:
    dates = trade_dates()
    if not dates or (date is not None and date not in dates):
        requested = date or "当前"
        print(f"{requested} 缺少有效完整收盘涨停池（缺失、盘中或畸形），断板未知。")
        if dates:
            print(f"最近有效完整收盘池：{dates[-1]}")
        return
    d = cmd_build(date, write=False)
    m = d["market"]
    print(f"\n=== {d['date']} 连板在册台账（≥{MIN_BOARDS}板）===")
    print(f"涨停 {m['zt_count']} 只 · 最高 {m['max_boards']} 板 · 在册 {m['tracked_count']} 只"
          f" · 今日断板 {m['broken_count']} 只 · 题材源 {d['theme_source']}")
    print(f"{'代码':<8}{'名称':<10}{'板':>3} {'在册':>3} {'首板日':<12}{'题材':<10}{'身位':>4}"
          f"{'命中':>4} {'同档历史晋级':>12}")
    for t in d["tracked"]:
        h = t["hist"]["cond"]
        rate = (f"{h['rate']}%({h['n']}{'弱' if h['quality'] == 'weak' else ''})"
                if h.get("rate") is not None else f"无样本({h['n']})")
        rk = t["dragon_rank"] if t["is_sector_dragon"] else "-"
        days = str(t["track"]["days_in_track"]) + ("?" if t["track"]["truncated"] else "")
        print(f"{t['code']:<8}{(t['name'] or '')[:9]:<10}{t['boards']:>3} "
              f"{days:>3} {t['track']['first_date']:<12}"
              f"{(t['theme'] or '')[:9]:<10}{str(rk):>4}{t['factors']['count']:>4} {rate:>12}")
    if d["broken"]:
        print("\n--- 今日断板（昨日在册·今日不在涨停池）---")
        for b in d["broken"]:
            print(f"  {b['code']} {b['name']} 昨 {b['prev_boards']}板 · 连了 {b['days_in_track']} 天"
                  f"（{b['first_date']} 起）")


def cmd_stats(stage: int = 2) -> None:
    dates = trade_dates()
    evs = build_events(dates)
    print(f"\n=== {stage}进{stage + 1} 条件晋级率（{dates[0]} ~ {dates[-1]}·{len(dates)} 个归档日）===")
    base = [bool(e["ok"]) for e in evs if int(e["prev_boards"]) == stage]
    if not base:
        print("无样本"); return
    print(f"基线：n={len(base)} 晋级率={sum(base) / len(base) * 100:.1f}%")
    print(f"{'命中档':<8}{'n':>5}{'晋级':>5}{'率':>7}  质量")
    for k in range(5):
        s = [bool(e["ok"]) for e in evs
             if int(e["prev_boards"]) == stage and factor_hits(e["row"])["count"] == k]
        if not s:
            continue
        q = "ok" if len(s) >= WEAK_N else "weak(样本不足)"
        print(f"{str(k) + ' 条':<8}{len(s):>5}{sum(s):>5}{sum(s) / len(s) * 100:>6.0f}%  {q}")


def cmd_lifecycle(code: str) -> None:
    dates = trade_dates()
    segs = lifecycle(dates, code)
    if not segs:
        print(f"❌ {code} 不在归档涨停池里")
        return
    print(f"\n=== {code} 连板生命周期（{len(segs)} 段）===")
    for i, seg in enumerate(segs, 1):
        print(f"\n段{i}：{seg[0]['date']} 起 1→{seg[-1]['boards']} 板（{len(seg)} 个交易日）")
        for x in seg:
            print(f"   {x['date']}  {x['boards']:>2}板  封 {x['fbt']}  炸{x['zbc']}  "
                  f"封单{x['seal']}亿 换手{x['hs']}%  涨{x['pct']}%")
        nxt = "已断板" if i < len(segs) else "仍在册"
        print(f"   → {nxt}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="连板票生命周期台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pb = sub.add_parser("build"); pb.add_argument("--date")
    pf = sub.add_parser("backfill"); pf.add_argument("--days", type=int, default=30)
    ps = sub.add_parser("show"); ps.add_argument("--date")
    pl = sub.add_parser("lifecycle"); pl.add_argument("--code", required=True)
    pst = sub.add_parser("stats"); pst.add_argument("--stage", type=int, default=2)
    a = ap.parse_args(argv)

    if a.cmd == "build":
        d = cmd_build(a.date)
        print(f"✅ {d['date']} 台账 → {out_path(d['date'])}"
              f"（在册 {d['market']['tracked_count']} · 断板 {d['market']['broken_count']}）")
    elif a.cmd == "backfill":
        dates = trade_dates()[-a.days:]
        for d0 in dates:
            cmd_build(d0, write=True)
        print(f"✅ 回填 {len(dates)} 个交易日：{dates[0]} → {dates[-1]}")
    elif a.cmd == "show":
        cmd_show(a.date)
    elif a.cmd == "lifecycle":
        cmd_lifecycle(a.code)
    elif a.cmd == "stats":
        cmd_stats(a.stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
