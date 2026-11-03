#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价三问体检（只读诊断层·2026-11-03 立）

目的：把 09:25 竞价落地后的判断，固化成**顺序不可颠倒的三问**，并在终端一次打全。
      ① 情绪（市场层：总龙头开在哪、昨日涨停今天红开几成）
      ② 板块（这条线活不活：同题材昨日涨停票今早红开家数 / 中位开幅）
      ③ 个股（能不能买：0<gap≤5%、竞价额达标、身位差≤2）

⛔ 边界：本脚本**只读、只打印**，不写账本、不改 `pool_call` 裁决、不推送。
   `pool_call` 的结论仍由 `wealth_code/cache/pool_call-<date>.json` 冻结；本脚本是给人看的体检表，
   不一致时以 pool_call 为准，并把差异当成「待回放的优化线索」，⛔ 不许据此手工改裁决。

用法：
  python3 scripts/auction_three_questions.py                # 今日（前一交易日候选 → 今日竞价）
  python3 scripts/auction_three_questions.py --date 2026-11-03
  python3 scripts/auction_three_questions.py --date 2026-11-03 --json   # 机读

判据来源（全部既有口径·不新造）：
  · 红开 = gap > 0；低开 = gap < 0
  · 板块活：红开比例 ≥ 0.5 且中位 gap > 0；板块死：红开比例 < 0.3 或 中位 gap < -2
  · 个股可买：0 < gap ≤ 5（`USER_ENTRY_MAX_GAP_PCT`）∧ 竞价额 ≥ 昨成交额×0.7 ∧ 身位差 ≤ 2
  · 身位差 = 全市场最高板 − 本票板数（`POOL_CALL_LEADER_ONLY` 同源）
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

WC_CACHE = ROOT / "wealth_code" / "cache"
ZT_DIR = ROOT / "cache" / "zt_history"
TRACK_DIR = ROOT / "cache" / "board_track"

GAP_MAX = 5.0             # 用户硬线：>5% 买不到
VOL_RATIO_MIN = 0.7       # ai_caliber 量能门
POS_MAX_GAP = 2           # 身位差上限（POOL_CALL_LEADER_ONLY_MAX_GAP）
TOP_DRAGON_KILL = -3.0    # 总龙头跌破该开幅 → 全池降级（诊断提示，不裁决）
SECTOR_ALIVE_RED = 0.5
SECTOR_DEAD_RED = 0.3
SECTOR_DEAD_MED = -2.0


# ── io ──────────────────────────────────────────────────────────────────────
def _jd(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def latest_candidates(target_date: str) -> tuple[str, dict]:
    """取 target_date 之前最近的候选文件（候选是「D 日盘后 → D+1 用」，故必须早于 target）。"""
    best = ("", {})
    for p in sorted(WC_CACHE.glob("candidates-????-??-??.json")):
        d = p.stem.replace("candidates-", "")
        if d < target_date and d > best[0]:
            best = (d, _jd(p))
    return best


def load_quotes(target_date: str) -> dict[str, dict]:
    return _jd(WC_CACHE / f"auction_quotes-{target_date}.json")


def _gap(q: dict | None) -> float | None:
    if not isinstance(q, dict):
        return None
    px, pre = q.get("auction_price"), q.get("prev_close")
    try:
        px, pre = float(px), float(pre)
    except Exception:
        return None
    if px <= 0 or pre <= 0:
        return None
    return round((px / pre - 1.0) * 100.0, 2)


def _med(vals: list[float]) -> float | None:
    return round(statistics.median(vals), 2) if vals else None


# ── Q1 情绪 ─────────────────────────────────────────────────────────────────
def q1_mood(prev: dict, zt_rows: list[dict], quotes: dict[str, dict],
            pool_meta: dict | None = None) -> dict:
    env = prev.get("environment") or {}
    mo = (pool_meta or {}).get("market_open") or {}
    gaps = [g for g in (_gap(quotes.get(str(r.get("code")))) for r in zt_rows) if g is not None]
    red = [g for g in gaps if g > 0]
    high = [r for r in zt_rows if int(r.get("boards") or 0) >= 3]
    hg = [g for g in (_gap(quotes.get(str(r.get("code")))) for r in high) if g is not None]
    hr = [g for g in hg if g > 0]
    # 市场层红开率优先取 pool_call 已冻结口径（与裁决同源），缺失才自算
    if mo.get("red_open_ratio") is not None:
        ratio = round(float(mo["red_open_ratio"]), 3)
        red_n, quoted_n = int(mo.get("red_open_count") or 0), int(mo.get("quoted") or 0)
        basis = "pool_call.market_open"
    else:
        ratio = round(len(red) / len(gaps), 3) if gaps else None
        red_n, quoted_n = len(red), len(gaps)
        basis = "self"
    level = "未知"
    if ratio is not None:
        level = "强" if ratio >= SECTOR_ALIVE_RED else ("中" if ratio >= SECTOR_DEAD_RED else "弱")
    return {
        "prev_zt_count": env.get("now_zt_count"),
        "prev_sentiment": env.get("sentiment_score"),
        "prev_sentiment_label": env.get("sentiment_label"),
        "prev_promote_rate": env.get("promote_rate"),
        "prev_max_boards": env.get("max_boards"),
        "quoted": quoted_n,
        "red_open": red_n,
        "red_ratio": ratio,
        "gap_median": _med(gaps),
        "high_board_quoted": len(hg),
        "high_board_red": len(hr),
        "leader_red_ratio": mo.get("leader_red_open_ratio"),
        "market_ready": mo.get("ready"),
        "basis": basis,
        "level": level,
    }


# ── Q2 板块 ─────────────────────────────────────────────────────────────────
def theme_of(prev: dict, tmap: dict[str, str], code: str, row: dict | None) -> tuple[str, str]:
    """题材口径：⛔ 优先炒作题材快照（`theme_snapshot.mapping[code].theme`），
    兜底候选 `core_theme`，最后才用申万行业并**标注降级**（记忆铁律：分组不用申万）。"""
    t = tmap.get(code)
    if t:
        return str(t), "snapshot"
    if row and row.get("core_theme"):
        return str(row["core_theme"]), "core_theme"
    if row and row.get("sector"):
        return str(row["sector"]), "sector_fallback"
    return "未分类", "missing"


def load_theme_map(prev: dict) -> dict[str, str]:
    ts = prev.get("theme_snapshot") or {}
    p = ts.get("snapshot_path")
    if not p:
        return {}
    try:
        s = json.loads(Path(p).read_text(encoding="utf-8"))
        m = s.get("mapping") or {}
        if isinstance(m, dict) and m:
            return {str(k): str((v or {}).get("theme") or "") for k, v in m.items()}
    except Exception:
        pass
    return {}


def q2_sectors(prev: dict, zt_rows: list[dict], quotes: dict[str, dict],
               tmap: dict[str, str] | None = None) -> list[dict]:
    tmap = tmap or {}
    buckets: dict[str, list[dict]] = {}
    src_stat: dict[str, int] = {}
    for r in zt_rows:
        code = str(r.get("code") or "")
        if not code:
            continue
        th, src = theme_of(prev, tmap, code, r)
        src_stat[src] = src_stat.get(src, 0) + 1
        g = _gap(quotes.get(code))
        buckets.setdefault(th, []).append({
            "code": code, "name": r.get("name"), "boards": int(r.get("boards") or 0),
            "gap": g, "amt": (quotes.get(code) or {}).get("auction_amount_yi"),
        })
    out = []
    for th, rows in buckets.items():
        if len(rows) < 2:
            continue
        gs = [r["gap"] for r in rows if r["gap"] is not None]
        red = [g for g in gs if g > 0]
        ratio = round(len(red) / len(gs), 3) if gs else None
        med = _med(gs)
        if ratio is None:
            state = "无报价"
        elif ratio >= SECTOR_ALIVE_RED and (med or 0) > 0:
            state = "活"
        elif ratio < SECTOR_DEAD_RED or (med is not None and med < SECTOR_DEAD_MED):
            state = "死"
        else:
            state = "中性"
        rows_sorted = sorted(rows, key=lambda r: (-(r["boards"] or 0), -(r["gap"] or -99)))
        out.append({
            "theme": th, "mates": len(rows), "quoted": len(gs), "red_open": len(red),
            "red_ratio": ratio, "gap_median": med, "state": state,
            "leader": rows_sorted[0] if rows_sorted else None,
            "rows": rows_sorted,
        })
    out.sort(key=lambda x: (-(x["mates"] or 0), str(x["theme"])))
    return out, src_stat


# ── Q3 个股 ─────────────────────────────────────────────────────────────────
def q3_stocks(prev: dict, zt_rows: list[dict], quotes: dict[str, dict],
              pc_views: dict | None = None) -> dict:
    """量能门一律取 `pool_call` 冻结的 `amount_yi / need_yi`（与裁决同源·`VOLUME_WEAK_RATIO`=0.7），
    ⛔ 不用「竞价额 ÷ 昨成交额」自造口径——那个比值天然只有 3~15%，会把全部票误判无量。"""
    mm = int(prev.get("market_max_boards") or 0)
    by_code = {str(r.get("code")): r for r in zt_rows}
    pv = pc_views if isinstance(pc_views, dict) else {}
    rows = []
    for pool in ("relay", "watch", "low_pool"):
        for c in prev.get(pool) or []:
            code = str(c.get("code") or "")
            if not code:
                continue
            q = quotes.get(code) or {}
            g = _gap(q)
            boards = int(c.get("boards") or 0)
            pos_gap = (mm - boards) if mm and boards else None
            fc = c.get("forecast") or {}
            pv_it = pv.get(code) or {}
            amt = pv_it.get("amount_yi", q.get("auction_amount_yi"))
            need = pv_it.get("need_yi")
            vol_ok = None
            if isinstance(amt, (int, float)) and isinstance(need, (int, float)) and need > 0:
                vol_ok = float(amt) >= float(need) * VOL_RATIO_MIN
            if g is None:
                verdict, why = "无报价", "竞价缺失"
            elif g <= 0:
                verdict, why = "观察", f"绿开/平开 {g:+.2f}%"
            elif g > GAP_MAX:
                verdict, why = "买不到", f"高开 {g:+.2f}% > {GAP_MAX}%"
            elif pos_gap is not None and pos_gap > POS_MAX_GAP:
                verdict, why = "放弃", f"身位差 {pos_gap} 板（后排）"
            elif vol_ok is False:
                verdict, why = "观察", f"竞价 {float(amt):.2f}亿 < 目标 {float(need):.2f}亿×{VOL_RATIO_MIN:g}"
            else:
                vol_txt = (f"量 {float(amt):.2f}/{float(need):.2f}亿达标" if vol_ok
                           else "量能未知（未裁决）")
                verdict, why = "可买", f"开 {g:+.2f}% · {vol_txt}"
            rows.append({
                "pool": pool, "code": code, "name": c.get("name"), "boards": boards,
                "theme": c.get("core_theme") or c.get("theme"), "gap": g,
                "pos_gap": pos_gap, "amount_yi": amt, "need_yi": need, "vol_ok": vol_ok,
                "forecast": fc.get("verdict"), "verdict": verdict, "why": why,
                "pool_view": pv_it.get("view"),
                "dragon_rank": c.get("dragon_rank"),
            })
    # 总龙头体检
    top = None
    if mm:
        cands = [r for r in zt_rows if int(r.get("boards") or 0) >= mm]
        if cands:
            t = cands[0]
            top = {"code": t.get("code"), "name": t.get("name"), "boards": mm,
                   "gap": _gap(quotes.get(str(t.get("code"))))}
    kill = bool(top and top.get("gap") is not None and top["gap"] < TOP_DRAGON_KILL)
    return {"stocks": rows, "top_dragon": top, "top_dragon_kill": kill}


# ── main ────────────────────────────────────────────────────────────────────
def run(target_date: str) -> dict:
    prev_date, prev = latest_candidates(target_date)
    zt = _jd(ZT_DIR / f"zt_{prev_date}.json") if prev_date else {}
    zt_rows = zt.get("zt_list") or []
    quotes = load_quotes(target_date)
    pc = _jd(WC_CACHE / f"pool_call-{target_date}.json")
    pc_views = pc.get("views") if isinstance(pc.get("views"), dict) else {}
    pc_meta = pc.get("meta") if isinstance(pc.get("meta"), dict) else {}
    tmap = load_theme_map(prev)
    q2, src_stat = q2_sectors(prev, zt_rows, quotes, tmap)
    res = {
        "target_date": target_date,
        "prev_date": prev_date,
        "quotes_n": len(quotes),
        "zt_prev_n": len(zt_rows),
        "pool_call_frozen": bool(pc_views),
        "theme_source": src_stat,
        "q1": q1_mood(prev, zt_rows, quotes, pc_meta),
        "q2": q2,
        "q3": q3_stocks(prev, zt_rows, quotes, pc_views),
    }
    return res


def render(res: dict) -> str:
    L: list[str] = []
    q1, q2, q3 = res["q1"], res["q2"], res["q3"]
    L.append(f"=== 竞价三问体检 · {res['target_date']}（昨日候选 {res['prev_date']}·昨涨停 {res['zt_prev_n']} 只·今早报价 {res['quotes_n']} 只）===")
    L.append("")
    L.append(f"【Q1 情绪】昨：涨停 {q1['prev_zt_count']} 家 · 气氛 {q1['prev_sentiment']}（{q1['prev_sentiment_label']}）"
             f" · 晋级率 {q1['prev_promote_rate']}% · 最高 {q1['prev_max_boards']} 板")
    if q1["quoted"]:
        L.append(f"        今早：红开 {q1['red_open']}/{q1['quoted']} = {q1['red_ratio']:.0%}"
                 f" · 中位 {q1['gap_median']:+.2f}% · ≥3板红开 {q1['high_board_red']}/{q1['high_board_quoted']}"
                 f" → 情绪档【{q1['level']}】")
    else:
        L.append("        今早：无报价（竞价未落地）")
    L.append("")
    dead, alive = [s for s in q2 if s["state"] == "死"], [s for s in q2 if s["state"] == "活"]
    L.append("【Q2 板块】" + (f"活线 {len(alive)} 条 · 死线 {len(dead)} 条" if q2 else "无 ≥2 家题材")
             + ("" if (res.get("theme_source") or {}).get("snapshot")
                else f"  ⚠️题材降级：{res.get('theme_source')}"))
    for s in q2[:12]:
        lead = s["leader"] or {}
        L.append(f"        {s['state']}  {s['theme']}({s['mates']}家) 红开 {s['red_open']}/{s['quoted']}"
                 f"={('%.0f%%' % (s['red_ratio']*100)) if s['red_ratio'] is not None else '—'}"
                 f" · 中位 {('%.2f' % s['gap_median']) if s['gap_median'] is not None else '—'}%"
                 f" · 龙一 {lead.get('name','—')}({lead.get('boards',0)}板 {('%.2f' % lead['gap']) if lead.get('gap') is not None else '—'}%)")
    if dead:
        L.append(f"        ⛔ 整块弃：{'、'.join(s['theme'] for s in dead)}")
    L.append("")
    rows = q3["stocks"]
    order = {"可买": 0, "观察": 1, "放弃": 2, "买不到": 3, "无报价": 4}
    rows_sorted = sorted(rows, key=lambda r: (order.get(r["verdict"], 9), -(r["boards"] or 0)))
    L.append("【Q3 个股】" + f"{len(rows)} 只 · 可买 {sum(1 for r in rows if r['verdict']=='可买')} 只"
             + ("" if res.get("pool_call_frozen") else "  ⚠️今日 pool_call 未冻结：量能口径未知"))
    for r in rows_sorted:
        _amt, _need = r.get("amount_yi"), r.get("need_yi")
        _vol = (f"{float(_amt):.2f}/{float(_need):.2f}亿" if isinstance(_amt, (int, float)) and _need
                else "—")
        _pv = r.get("pool_view") or "—"
        _pv_buy = _pv in ("buy", "participate")
        _conflict = ("  ⚠️以终裁为准" if (r["verdict"] == "可买" and not _pv_buy)
                     or (_pv_buy and r["verdict"] != "可买") else "")
        L.append(f"        {r['verdict']:<4}{r['code']} {r['name']}({r['boards']}板 {r['pool']})"
                 f" 开 {('%.2f' % r['gap']) if r['gap'] is not None else '—'}%"
                 f" · 身位差 {r['pos_gap'] if r['pos_gap'] is not None else '—'}"
                 f" · 量 {_vol}"
                 f" · 模型:{r['forecast'] or '—'} · 终裁:{_pv} → {r['why']}{_conflict}")
    td = q3["top_dragon"]
    L.append("")
    if td:
        L.append(f"【总龙头】{td['code']} {td['name']} {td['boards']}板 · 今开 "
                 f"{('%.2f' % td['gap']) if td['gap'] is not None else '—'}%"
                 + ("  ⛔ 跌破 -3%：全池降级（只看不做）" if q3["top_dragon_kill"] else "  ✅ 未破 -3%"))
    else:
        L.append("【总龙头】无（最高板数据缺失）")
    L.append("")
    L.append("说明：本表只读诊断，不产裁决；买哪只以 pool_call-<date>.json 冻结结论为准。")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not a.date:
        sys.path.insert(0, str(ROOT))
        try:
            from common.trading_calendar import today_or_last_trading_day  # type: ignore
            a.date = today_or_last_trading_day()
        except Exception:
            import datetime
            a.date = datetime.date.today().isoformat()
    res = run(str(a.date))
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(render(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
