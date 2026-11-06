#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""身位差「分母」验证：全市场最高板 vs 同题材最高板（2026-11-06 用户拍板 A）

## 用户原话（问题来源）

「我觉得可以先按A执行一下，因为身位差，我好像没有强制要求百分之百限制吧……
  有时候龙头，比如说板块之间非板块之间的切换，比如说你传媒板块你已经涨了五板龙头了，
  但是光伏板块才一个板，这种身位差其实是没有影响的呀，它是走一个新的逻辑，
  甚至说可以做一个资金的承接方向，你这种身位差估计只能在同一个板块里面适用，
  不同板块之间它是没有关系的」

⇒ 拆成两个可验证的命题：
   P1「跨题材身位差无意义」：`gap = 全市场最高板 − 本票板数 ≥ 2` 的票里，
      若 `同题材身位差 ≤ 1`（即它在本题材里就是前排/最高板），收益是否确实不差？
   P2「同题材内身位差才有效」：`同题材身位差 ≥ 2` 是否比 `≤1` 明显更差？

## 口径（与既有研究逐字同源，⛔ 不另造）

  · 买入价 = 当日 09:25 竞价价（`auction_quotes-<D>.json`）。
      已校验：开盘价 = 09:25 集合竞价成交价，94.9% 完全相等、P95 差 0.043%。
  · 可买 = 未竞价封死（gap < 涨停幅度 − 0.2pct）。封死票不进收益样本（买不进）。
  · 收益 T+1 = 次一交易日收盘 / 竞价价 − 1（`ai_judgement_next_day_close` 账本口径）。
  · 晋级率 = 次一交易日是否收盘涨停（`zt_history/zt_<D+1>.json`）。
  · 题材源：`sector_gap_context.main_theme` —— 选股宝 plates[0] > theme > 昨归档 sector。
      ⛔ 只有 plates/theme 算「题材」；落到 sector 兜底的票**单独成组**，不与题材组混读。
  · 同题材最高板 = **昨日完整涨停池**内同题材的 max(boards)（⛔ 不是候选池，否则被选股结果高估）。

⛔ 纯研究：不改判据、不写冻结件、不写账本、不产卡。
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from common.trading_calendar import (  # noqa: E402
    prev_a_share_trading_day, next_a_share_trading_day,
)

CACHE = ROOT / "wealth_code" / "cache"
ZT = ROOT / "cache" / "zt_history"
OUT = ROOT / "cache" / "research" / "position_gap_bt.json"

MARKET_GAP_MAX = 2          # 现行硬闸：全市场身位差 ≥2 → 后排不做
LINE_GAP_MAX = 2            # 候选口径：同题材身位差 ≥2 → 后排不做


def _load(p: Path, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _bars(code: str) -> list[dict]:
    d = _load(ROOT / "cache" / "kline_qfq" / f"{code}.json", {}) or {}
    return [b for b in (d.get("bars") or []) if b.get("date")]


def _close_on(code: str, day: str) -> float | None:
    for b in _bars(code):
        if str(b.get("date"))[:10] == day:
            return _num(b.get("close"))
    return None


def _limit_pct(code: str, yrow: dict) -> float:
    if yrow.get("is_20cm") is True:
        return 20.0
    if code.startswith(("300", "301", "688")):
        return 20.0
    return 10.0


def _auction_days() -> list[str]:
    days = []
    for p in CACHE.glob("auction_quotes-*.json"):
        d = p.stem.replace("auction_quotes-", "")
        if (ZT / f"zt_{d}.json").exists():
            days.append(d)
    return sorted(days)


def _med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _agg(rows: list[dict]) -> dict:
    rets = [r["ret_t1"] for r in rows if r.get("ret_t1") is not None]
    if not rets:
        return {"n": 0}
    return {
        "n": len(rets),
        "mean": round(_mean(rets), 2),
        "med": round(_med(rets), 2),
        "win": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1),
        "promote": (round(sum(1 for r in rows if r.get("promoted")) / len(rows) * 100, 1)
                    if rows else None),
    }


def build_rows() -> list[dict]:
    """逐「票日」重建：题材身位差 / 市场身位差 / T+1 收益。"""
    try:
        from core import expected_gap as eg
        from core import sector_gap_context as sgc
    except ImportError:                                        # pragma: no cover
        from wealth_code.core import expected_gap as eg        # type: ignore
        from wealth_code.core import sector_gap_context as sgc  # type: ignore

    rows: list[dict] = []
    for day in _auction_days():
        prev = prev_a_share_trading_day(day)
        zt = _load(ZT / f"zt_{prev}.json", {}) or {}
        zlist = zt.get("zt_list") or []
        if not zlist:
            continue
        quotes = _load(CACHE / f"auction_quotes-{day}.json", {}) or {}
        if not quotes:
            continue
        mp = sgc.load_plates_map(prev) or {}

        yr = {str(r.get("code") or ""): r for r in zlist if isinstance(r, dict)}
        boards_of = {c: int(_num(r.get("boards")) or 0) for c, r in yr.items()}
        market_max = max(boards_of.values())

        # 题材归线（昨日**完整涨停池**，非候选池）
        theme_of: dict[str, str] = {}
        src_of: dict[str, str] = {}
        for c, r in yr.items():
            theme_of[c], src_of[c] = sgc.main_theme(c, r, mp)
        groups: dict[str, list[str]] = defaultdict(list)
        for c, t in theme_of.items():
            if t and t not in ("其他", "未分类"):
                groups[t].append(c)
        theme_max = {t: max(boards_of[c] for c in cs) for t, cs in groups.items()}

        nxt = next_a_share_trading_day(day)
        zt_next = _load(ZT / f"zt_{nxt}.json", {}) or {}
        promoted = {str(r.get("code") or "") for r in (zt_next.get("zt_list") or [])
                    if isinstance(r, dict)}

        for code, y in yr.items():
            q = quotes.get(code)
            if not isinstance(q, dict):
                continue
            pc, ap = _num(q.get("prev_close")), _num(q.get("auction_price"))
            if not pc or not ap or pc <= 0 or ap <= 0:
                continue
            gap = round((ap / pc - 1) * 100, 2)
            lim = _limit_pct(code, y)
            if gap >= lim - 0.2:                    # 竞价封死，买不进 → 不入收益样本
                continue
            entry = ap
            c_d = _close_on(code, day)
            c_n = _close_on(code, nxt)
            ret_t1 = round((c_n / entry - 1) * 100, 2) if c_n else None

            b = boards_of.get(code) or 0
            if not b:
                continue
            t = theme_of.get(code) or ""
            valid_theme = bool(t) and t not in ("其他", "未分类")
            tmax = theme_max.get(t) if valid_theme else None
            E = eg.expected_gap_pct(y)

            rows.append({
                "date": day, "code": code, "name": y.get("name"),
                "boards": b, "gap": gap,
                "theme": t, "theme_src": src_of.get(code),
                "market_max": market_max, "theme_max": tmax,
                "market_gap": market_max - b,
                "line_gap": (tmax - b) if tmax else None,
                "E": round(E, 2) if E is not None else None,
                "dev": round(gap - E, 2) if E is not None else None,
                "ret_auction_to_close": round((c_d / entry - 1) * 100, 2) if c_d else None,
                "ret_t1": ret_t1,
                "promoted": code in promoted,
            })
    return rows


def _split(rows: list[dict], key: str, thr: int) -> dict:
    back = [r for r in rows if r.get(key) is not None and r[key] >= thr]
    front = [r for r in rows if r.get(key) is not None and r[key] < thr]
    return {"后排(>=": _agg(back), "前排": _agg(front), "后排_n": len(back), "前排_n": len(front)}


# ── C 段：选股卡 forecast 现用的「三桶」结构（最高板 / 差1板 / 差≥2板）──────────
# 为什么要单独算这段：`core/relay_forecast.forecast_one` 的 `VERDICT_DROP`（放弃）
# 直接由「gap_to_top ≥ 2」触发，用的是**全市场**分母。若这个分母本身没有区分度，
# 那这枚「放弃」标签就是无依据的——与本轮身位口径修正同源，故在此一并验证。
def _three_bucket(rows: list[dict], key: str) -> dict:
    """按 relay_forecast 的三桶切法聚合（0=最高板 / 1=差1板 / ≥2=后排）。"""
    def front(r):
        return r.get(key) == 0
    top = [r for r in rows if front(r)]
    one = [r for r in rows if r.get(key) == 1]
    back = [r for r in rows if r.get(key) is not None and r[key] >= 2]
    return {"最高板": _agg(top), "最高板_n": len(top),
            "差1板": _agg(one), "差1板_n": len(one),
            "差>=2板": _agg(back), "差>=2板_n": len(back)}


def main() -> int:
    rows = build_rows()
    themed = [r for r in rows if r.get("theme_src") in ("plates", "theme") and r.get("line_gap") is not None]
    sector_fb = [r for r in rows if r.get("theme_src") == "sector" and r.get("line_gap") is not None]

    print(f"样本：全部可买票日 {len(rows)}｜真题材口径 {len(themed)}｜行业兜底 {len(sector_fb)}")
    print(f"日期覆盖：{len({r['date'] for r in rows})} 天\n")

    report: dict = {"generated": "2026-11-06", "n_all": len(rows),
                    "n_themed": len(themed), "n_sector_fallback": len(sector_fb)}

    for label, pool in (("A. 真题材口径（plates/theme）", themed),
                        ("B. 行业兜底口径（sector）", sector_fb)):
        print("=" * 78)
        print(label, f"n={len(pool)}")
        print("=" * 78)
        cur = _split(pool, "market_gap", MARKET_GAP_MAX)
        lin = _split(pool, "line_gap", LINE_GAP_MAX)
        print(f"  【现行】全市场身位差 ≥{MARKET_GAP_MAX} 判后排：")
        print(f"     保留(前排) {cur['前排']}")
        print(f"     剔除(后排) {cur['后排(>=']}")
        print(f"  【用户】同题材身位差 ≥{LINE_GAP_MAX} 判后排：")
        print(f"     保留(前排) {lin['前排']}")
        print(f"     剔除(后排) {lin['后排(>=']}")

        # 核心：现行闸判后排、但本题材里其实是前排 → 被错杀的那批
        misfire = [r for r in pool
                   if r.get("market_gap") is not None and r["market_gap"] >= MARKET_GAP_MAX
                   and r.get("line_gap") is not None and r["line_gap"] < LINE_GAP_MAX]
        keep_cur = [r for r in pool if r.get("market_gap") is not None
                    and r["market_gap"] < MARKET_GAP_MAX]
        print(f"  【错杀检查】现行闸判后排、但自己在本题材是前排：n={len(misfire)}")
        print(f"     被错杀批 {_agg(misfire)}")
        print(f"     现行保留批 {_agg(keep_cur)}")
        if misfire:
            by_b = Counter(r["boards"] for r in misfire)
            print(f"     板数分布 {dict(sorted(by_b.items()))}")
            by_t = Counter(r["theme"] for r in misfire).most_common(6)
            print(f"     题材 TOP {by_t}")

        # 同题材内后排是否真的差（P2）
        inner = [r for r in pool if r.get("line_gap") is not None]
        same_line_back = [r for r in inner if r["line_gap"] >= LINE_GAP_MAX]
        print(f"  【同题材内后排】line_gap≥{LINE_GAP_MAX} n={len(same_line_back)} {_agg(same_line_back)}")
        print()

        report[label] = {
            "n": len(pool),
            "market_gap_split": cur,
            "line_gap_split": lin,
            "misfire": _agg(misfire), "misfire_n": len(misfire),
            "keep_current": _agg(keep_cur), "keep_current_n": len(keep_cur),
            "line_back": _agg(same_line_back), "line_back_n": len(same_line_back),
        }

    # ── C 段：选股卡 forecast 的「放弃」标签用的三桶，换分母后还成立吗 ──────────
    print("=" * 78)
    print("C. 选股卡 forecast 三桶（`gap_to_top>=2 → 放弃`）的分母对照")
    print("=" * 78)
    _red5 = [r for r in themed if r.get("gap") is not None and 0 < r["gap"] <= 5]
    c_sec: dict = {}
    for label, pool in (("全部未封死可买（现行 11-05 口径）", themed),
                        (f"旧可买口径 0<gap<=5（n={len(_red5)}）", _red5)):
        mg = _three_bucket(pool, "market_gap")
        lg = _three_bucket(pool, "line_gap")
        # 区分度：被标「放弃」的桶 vs 不被标的两桶（加权平均）
        def _spread(b, drop_key="差>=2板"):
            kept = [x for x in (b.values()) if isinstance(x, dict) and x.get("n")]
            d = b.get(drop_key) or {}
            k = [b.get("最高板") or {}, b.get("差1板") or {}]
            kn = sum(x.get("n", 0) for x in k)
            kw = (sum(x.get("win", 0) * x.get("n", 0) for x in k) / kn) if kn else None
            return {"drop_win": d.get("win"), "drop_n": d.get("n"),
                    "keep_win": round(kw, 1) if kw is not None else None, "keep_n": kn,
                    "gap_pct": (round(kw - (d.get("win") or 0), 1)
                                if kw is not None and d.get("win") is not None else None)}
        print(f"  【{label}】")
        print(f"     市场分母 {mg}")
        print(f"     题材分母 {lg}")
        print(f"     市场分母区分度(保留-放弃 胜率差) {_spread(mg)}")
        print(f"     题材分母区分度(保留-放弃 胜率差) {_spread(lg)}")
        c_sec[label] = {"market_bucket": mg, "line_bucket": lg,
                        "market_spread": _spread(mg), "line_spread": _spread(lg)}
    report["C. relay 三桶分母对照"] = c_sec

    # 逐日方向（防被单日主导）
    if themed:
        misfire_days: dict[str, list] = defaultdict(list)
        keep_days: dict[str, list] = defaultdict(list)
        for r in themed:
            if r.get("ret_t1") is None:
                continue
            if (r.get("market_gap") or 0) >= MARKET_GAP_MAX and (r.get("line_gap") or 99) < LINE_GAP_MAX:
                misfire_days[r["date"]].append(r["ret_t1"])
            elif (r.get("market_gap") is not None and r["market_gap"] < MARKET_GAP_MAX):
                keep_days[r["date"]].append(r["ret_t1"])
        both = sorted(set(misfire_days) & set(keep_days))
        better = sum(1 for d in both
                     if _mean(misfire_days[d]) is not None and _mean(keep_days[d]) is not None
                     and _mean(misfire_days[d]) > _mean(keep_days[d]))
        print(f"逐日对照：有双方数据的 {len(both)} 天，「错杀批」优于「现行保留批」{better} 天")
        report["daily_compare"] = {"days": len(both), "misfire_better": better}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
