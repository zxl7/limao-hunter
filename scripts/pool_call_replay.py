#!/usr/bin/env python3
"""9:25 池内裁决「真实链路回放」—— 排序/开关改动的最终验收（2026-11-02 立）。

与 `relay_winrate.py` 的分工：
  relay_winrate   = 用已归档样本做**统计**（快，但候选池是理想化的全量样本）
  pool_call_replay = 把真实盘后候选 + 真实竞价快照重新喂进 `call_pool`，
                     两套配置各跑一遍，比**实际买入名单**和**第二天真金白银的收益**
                     ⛔ 这才是验收开关该不该开的地方：模拟再好，穿不透生产门控都是假的

回放口径（务必一致，否则 A/B 不可比）：
  · 候选 = `candidates-<T-1>.json`（盘后 T-1 生成，供 T 日执行）
  · 竞价 = `auction_quotes-<T>.json`
  · 预案 = `cache/ai_view_<T>.json`（T-1 盘后写，T 日执行）
  · 环境读数 = 当日 `pool_call-<T>.json` 快照里的 `meta.market_open / meta.relay_position`
    ⛔ 不重新跑环境判定：那会引入「今天的数据判昨天」的前视偏差
  · 收益 = **T 日开盘买 → T 日收盘卖**（`relay_return` 同一日游口径）；一字板/停牌剔除

用法：
  python3 scripts/pool_call_replay.py            # 全量对比
  python3 scripts/pool_call_replay.py --json     # 只打 JSON
  python3 scripts/pool_call_replay.py --verbose  # 逐日列名单
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WC = ROOT / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

CAND_DIR = WC / "cache"
QUOTE_DIR = WC / "cache"
PC_DIR = WC / "cache"
VIEW_DIR = ROOT / "cache"
ZT_DIR = ROOT / "cache/zt_history"
KLINE_DIR = ROOT / "cache/kline_qfq"

from core import pool_call  # noqa: E402


# ── 数据 ───────────────────────────────────────────────────

def _jload(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def trading_days() -> list[str]:
    """用 zt_history 的归档日当交易日序列（与回测同一日历，不自己数星期几）。"""
    return sorted(p.stem.replace("zt_", "") for p in ZT_DIR.glob("zt_*.json"))


def prev_day(days: list[str], d: str) -> str | None:
    try:
        i = days.index(d)
    except ValueError:
        return None
    return days[i - 1] if i > 0 else None


_KL: dict[str, dict[str, dict]] = {}


def bar(code: str, date: str) -> dict | None:
    if code not in _KL:
        m: dict[str, dict] = {}
        p = KLINE_DIR / f"{code}.json"
        if p.exists():
            try:
                for b in _jload(p).get("bars") or []:
                    m[str(b["date"])[:10]] = b
            except Exception:
                m = {}
        _KL[code] = m
    return _KL[code].get(date)


def day_return(code: str, date: str) -> tuple[float | None, str]:
    b = bar(code, date)
    if not b:
        return None, "no_kline"
    o, c = float(b["open"]), float(b["close"])
    if o <= 0:
        return None, "bad_price"
    if float(b["high"]) == float(b["low"]):
        return None, "one_word"
    return round((c - o) / o * 100.0, 2), "ok"


# ── 单日回放 ───────────────────────────────────────────────

def replay_day(date: str, days: list[str]) -> dict | None:
    pv = prev_day(days, date)
    if not pv:
        return None
    cands = _jload(CAND_DIR / f"candidates-{pv}.json")
    quotes = _jload(QUOTE_DIR / f"auction_quotes-{date}.json")
    snap = _jload(PC_DIR / f"pool_call-{date}.json")
    plan = _jload(VIEW_DIR / f"ai_view_{date}.json") or {}
    if not cands or not quotes:
        return None
    meta = (snap or {}).get("meta") or {}
    market_open = meta.get("market_open")
    relay_plan = meta.get("relay_position")
    # ⛔ stance 必须取**当日快照里实际算出的那个**（生产走 `ai_caliber.ai_stance(T)`），
    #    不能取 plan 里的：写预案那天的基调 ≠ 今天开盘的基调（10-09 实证：plan 与实判不同，
    #    取错会让名额凭空多出来，回放结果整体失真）。
    stance = (snap or {}).get("stance") or plan.get("stance")

    # 🌊 题材动量：按**买入日**读预生成快照（内容严格早于该日，无未来函数）。
    #    缺失返回 {} → views 里 theme_momentum=None，不裁决。
    try:
        from core import pool_call as _pc
        _mom = _pc.load_theme_momentum(date)
    except Exception:                                  # noqa: BLE001
        _mom = {}
    kw = dict(plan=plan, stance=stance, market_open=market_open, relay_plan=relay_plan,
              theme_momentum=_mom)
    return {"date": date, "cands": cands, "quotes": quotes, "kw": kw,
            "snap_meta": meta}


def _views_of(res: dict | None) -> dict[str, dict]:
    return (res or {}).get("views") or {}


def run_config(day: dict, cfg: str) -> dict:
    """cfg:
      'old'      旧口径（全部关）
      'new'      只开身位优先（排序键）
      'low'      身位优先 + 低开例外
      'vol'      身位优先 + 量能软化
      'leader'   身位优先 + **身位闸（后排一律不买）**
      'new_all'  三项全开
    """
    saved = (pool_call.POOL_CALL_TOP_BOARD_PRIORITY,
             pool_call.POOL_CALL_LOW_OPEN_TOP_EXCEPTION,
             pool_call.POOL_CALL_VOLUME_SOFT,
             pool_call.POOL_CALL_LEADER_ONLY)
    flags = {
        "old":     (False, False, False, False),
        "new":     (True,  False, False, False),
        "low":     (True,  True,  False, False),
        "vol":     (True,  False, True,  False),
        "leader":  (True,  False, False, True),
        "new_all": (True,  True,  True,  True),
    }[cfg]
    try:
        (pool_call.POOL_CALL_TOP_BOARD_PRIORITY,
         pool_call.POOL_CALL_LOW_OPEN_TOP_EXCEPTION,
         pool_call.POOL_CALL_VOLUME_SOFT,
         pool_call.POOL_CALL_LEADER_ONLY) = flags
        res = pool_call.call_pool(day["cands"], day["quotes"], **day["kw"])
    except Exception as e:                                       # noqa: BLE001
        return {"error": str(e), "picks": []}
    finally:
        (pool_call.POOL_CALL_TOP_BOARD_PRIORITY,
         pool_call.POOL_CALL_LOW_OPEN_TOP_EXCEPTION,
         pool_call.POOL_CALL_VOLUME_SOFT,
         pool_call.POOL_CALL_LEADER_ONLY) = saved

    views = _views_of(res)
    picks = []
    for code, v in views.items():
        if v.get("view") != "participate":
            continue
        ret, note = day_return(code, day["date"])
        picks.append({"code": code, "name": v.get("name"),
                      "boards": v.get("boards"), "gap_pct": v.get("gap_pct"),
                      "gap_to_top": v.get("gap_to_top"),
                      "position_cap": v.get("position_cap"),
                      "ret": ret, "note": note})
    return {"picks": picks, "quota": (res.get("meta") or {}).get("effective_quota"),
            "pool_size": (res.get("meta") or {}).get("pool_size")}


def summarize(picks_per_day: list[list[dict]]) -> dict:
    rets, days_with = [], 0
    for picks in picks_per_day:
        vals = [p["ret"] for p in picks if p["ret"] is not None]
        if not vals:
            continue
        days_with += 1
        rets.append(sum(vals) / len(vals))
    n_pick = sum(len([p for p in picks if p["ret"] is not None]) for picks in picks_per_day)
    if not rets:
        return {"days": 0, "picks": n_pick, "win_rate": None, "avg": None, "median": None}
    return {
        "days": days_with, "picks": n_pick,
        "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
        "avg": round(statistics.mean(rets), 2),
        "median": round(statistics.median(rets), 2),
        "total": round(sum(rets), 2),
    }


def analyze(verbose: bool = False) -> dict:
    days = trading_days()
    rows = []
    for d in days:
        day = replay_day(d, days)
        if not day:
            continue
        rows.append({"date": d,
                     "old": run_config(day, "old"),
                     "new": run_config(day, "new"),
                     "low": run_config(day, "low"),
                     "vol": run_config(day, "vol"),
                     "leader": run_config(day, "leader"),
                     "new_all": run_config(day, "new_all")})

    def names(cfg): return [{p["code"]: p["name"] for p in r[cfg]["picks"]} for r in rows]

    base_names = names("old")
    diff = {}
    for cfg in ("new", "low", "vol", "leader", "new_all"):
        diff[cfg] = [r["date"] for r, a, b in zip(rows, base_names, names(cfg)) if a != b]

    out = {
        "days_replayed": len(rows),
        "window": [rows[0]["date"], rows[-1]["date"]] if rows else None,
        "old": summarize([r["old"]["picks"] for r in rows]),
        "new": summarize([r["new"]["picks"] for r in rows]),
        "low": summarize([r["low"]["picks"] for r in rows]),
        "vol": summarize([r["vol"]["picks"] for r in rows]),
        "leader": summarize([r["leader"]["picks"] for r in rows]),
        "new_all": summarize([r["new_all"]["picks"] for r in rows]),
        "diff_days": diff,
        "rows": rows,
    }
    if verbose:
        for r in rows:
            def fmt(cfg):
                return ", ".join(
                    f"{p['name']}({p['boards']}板{p['gap_pct']:+.1f}%→{p['ret']:+.2f}%)"
                    if p["ret"] is not None else f"{p['name']}(无K线)"
                    for p in r[cfg]["picks"]) or "—"
            mark = "≠" if r["old"]["picks"] != r["leader"]["picks"] else " "
            out.setdefault("lines", []).append(
                f"  {r['date']}{mark} 旧|{fmt('old')}  ‖ 身位闸|{fmt('leader')}"
                f"  ‖ 全开|{fmt('new_all')}")
    return out


def render(a: dict) -> None:
    print(f"\n{'='*104}")
    print(f"池内裁决·真实链路回放   窗口 {a['window'][0]} ~ {a['window'][1]}"
          f"（{a['days_replayed']} 个交易日）")
    print(f"{'='*104}")
    print(f"  {'配置':<30}{'出手天数':>8}{'笔数':>6}{'日胜率':>8}{'日均':>9}{'中位':>8}{'累计':>9}")
    for key, label in (("old", "A 旧口径（现状）"),
                       ("new", "B 只开身位优先"),
                       ("low", "C 身位优先 + 低开例外"),
                       ("vol", "D 身位优先 + 量能软化"),
                       ("leader", "F 身位优先 + 身位闸（后排不做）"),
                       ("new_all", "E 三项全开")):
        st = a[key]
        if not st.get("days"):
            print(f"  {label:<30}无成交")
            continue
        print(f"  {label:<30}{st['days']:>8}{st['picks']:>6}"
              f"{st['win_rate']:>7}%{st['avg']:>8}%{st['median']:>7}%{st['total']:>8}%")
    d = a["diff_days"]
    print(f"\n  名单发生变化（相对 A）：身位优先 {len(d['new'])} 天"
          f" / 低开例外 {len(d['low'])} 天 / 量能软化 {len(d['vol'])} 天"
          f" / 身位闸 {len(d['leader'])} 天 / 三项全开 {len(d['new_all'])} 天")
    if a.get("lines"):
        print("\n【逐日明细】（≠ 表示旧口径与三项全开名单不同）")
        for line in a["lines"]:
            print(line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    a = analyze(verbose=args.verbose or not args.json)
    if args.json:
        print(json.dumps({k: v for k, v in a.items() if k != "rows"},
                         ensure_ascii=False, indent=1))
    else:
        render(a)
    # 落盘时去掉逐日 rows 里的巨量 view 细节，只留 picks
    slim = {**a, "rows": [{k: {"picks": v.get("picks"), "quota": v.get("quota")}
                           for k, v in r.items() if k != "date"} | {"date": r["date"]}
                          for r in a["rows"]]}
    out = ROOT / "cache/relay_review/replay.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
