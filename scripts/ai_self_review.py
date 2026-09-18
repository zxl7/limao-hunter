#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 操盘手自评（胜率 × 落地率 × 错杀）· 2026-09-18 立项（用户「自我研究目前胜率、操作复盘」）

回答三个问题（**只读**，不改任何阈值/账本/推送）：
  A) 我到底赚没赚？—— 账本口径战绩（账户账本 account_nav_history.jsonl / AI 轨 ai_nav_history.jsonl）
     老口径（等权铺 2-5 只）与新口径（AI 单票精选）分段对比。
  B) 我判「参与」的票，真的买到了吗？—— 决策落地率（participate → 成交 / 未成交原因）
  C) 我判「观察/放弃」的票，事后证明是错的吗？—— 错杀盘点（watch/skip 的执行日收益）

数据源（全真实落盘·禁编造）：
  wealth_code/cache/ai_decisions.jsonl      AI 逐票决策（decision_date/view/reason）
  wealth_code/cache/auction_quotes-*.json   各执行日竞价（prev_close/auction_price/auction_amount_yi）
  wealth_code/cache/ai_nav_history.jsonl    AI 轨净值
  wealth_code/cache/account_nav_history.jsonl 账户账本（含 caliber 标记）
  cache/audit_close_cache.json              收盘价缓存（decision_audit.py 同源）
  cache/decision_audit.json                 反事实审计摘要（可选，用于交叉核对）

口径与既有系统同源（禁另造）：
  gap  = (竞价 − 前收)/前收×100               ← core/ai_caliber.decide
  ret  = (T收 − T竞价)/T竞价×100              ← wealth_code/scripts/ai_ledger.py
  硬线 = core.ai_caliber.USER_ENTRY_MAX_GAP_PCT

用法：
  python3 scripts/ai_self_review.py                # 全量
  python3 scripts/ai_self_review.py --json         # 只出 JSON
  python3 scripts/ai_self_review.py --recent 10    # 只看最近 10 个决策日明细
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WC = BASE / "wealth_code"
for p in (BASE, WC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

DECISIONS = WC / "cache" / "ai_decisions.jsonl"
AI_NAV = WC / "cache" / "ai_nav_history.jsonl"
ACC_NAV = WC / "cache" / "account_nav_history.jsonl"
CLOSE_CACHE = BASE / "cache" / "audit_close_cache.json"
OUT_JSON = BASE / "cache" / "ai_self_review.json"
BJ_TZ = timezone(timedelta(hours=8))

try:
    from core.ai_caliber import USER_ENTRY_MAX_GAP_PCT as HARD_LINE
except Exception:
    HARD_LINE = 5.0


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _trade_days() -> list[str]:
    """执行日序列 = auction_quotes-*.json 文件名日期（与 ai_ledger 同源）。"""
    days = sorted(p.stem.split("-", 1)[1] for p in (WC / "cache").glob("auction_quotes-*.json"))
    return days


def _next_day(decision_date: str, days: list[str]) -> str | None:
    for d in days:
        if d > decision_date:
            return d
    return None


def _num(v):
    try:
        f = float(v)
        return f
    except Exception:
        return None


# ─────────────────── A) 账本口径战绩 ───────────────────
def ledger_stats() -> dict:
    rows = _jsonl(ACC_NAV)
    ai_nav = _jsonl(AI_NAV)
    old = [r for r in rows if r.get("caliber") != "ai"]
    new = [r for r in rows if r.get("caliber") == "ai"]

    def _seg(rs: list[dict]) -> dict:
        stk = [s["return_pct"] for r in rs for s in (r.get("stocks") or [])]
        g = [x for x in stk if x > 0]
        l = [x for x in stk if x < 0]
        return {
            "days": len(rs),
            "from": rs[0]["trade_date"] if rs else None,
            "to": rs[-1]["trade_date"] if rs else None,
            "day_win_rate": round(sum(1 for r in rs if r["avg_return_pct"] > 0) / len(rs) * 100, 1) if rs else None,
            "stock_n": len(stk),
            "stock_win_rate": round(len(g) / len(stk) * 100, 1) if stk else None,
            "avg_day_pct": round(statistics.mean(r["avg_return_pct"] for r in rs), 3) if rs else None,
            "avg_stock_pct": round(statistics.mean(stk), 3) if stk else None,
            "nav_from": rs[0]["nav"] if rs else None,
            "nav_to": rs[-1]["nav"] if rs else None,
            "win_avg": round(statistics.mean(g), 2) if g else None,
            "loss_avg": round(statistics.mean(l), 2) if l else None,
            "pl_ratio": round(abs(statistics.mean(g) / statistics.mean(l)), 2) if g and l else None,
        }

    # 最大回撤（合并曲线，按行顺序）
    navs = [(r["trade_date"], r["nav"]) for r in rows]
    peak, peakd, maxdd, maxdd_d = navs[0][1], navs[0][0], 0.0, ""
    for d, v in navs:
        if v > peak:
            peak, peakd = v, d
        dd = (v / peak - 1) * 100
        if dd < maxdd:
            maxdd, maxdd_d = dd, f"{peakd}→{d}"
    return {
        "old_caliber": _seg(old),
        "ai_caliber": _seg(new),
        "ai_track": {
            "trades": len(ai_nav),
            "nav_from": 1.0,
            "nav_to": ai_nav[-1]["nav"] if ai_nav else None,
            "win": sum(1 for r in ai_nav for s in r["stocks"] if s["return_pct"] > 0),
            "n": sum(len(r["stocks"]) for r in ai_nav),
            "detail": [
                {"trade_date": r["trade_date"], "code": s["code"], "name": s["name"],
                 "auction": s["auction_price"], "close": s["close"], "ret": s["return_pct"]}
                for r in ai_nav for s in r["stocks"]
            ],
        },
        "drawdown": {"peak": peak, "peak_date": peakd, "now": navs[-1][1], "now_date": navs[-1][0],
                     "cur_dd": round((navs[-1][1] / peak - 1) * 100, 2), "max_dd": round(maxdd, 2),
                     "max_dd_window": maxdd_d},
    }


# ─────────────────── B/C) 决策落地与错杀 ───────────────────
def decision_outcome() -> dict:
    rows = _jsonl(DECISIONS)
    days = _trade_days()
    close_cache = _json(CLOSE_CACHE, {}) or {}
    quotes_by_day: dict[str, dict] = {}
    for d in days:
        quotes_by_day[d] = _json(WC / "cache" / f"auction_quotes-{d}.json", {}) or {}

    out_rows = []
    for r in rows:
        dd = str(r["decision_date"])
        ed = _next_day(dd, days)
        rec = {"decision_date": dd, "exec_date": ed, "code": r["code"], "name": r.get("name"),
               "view": r.get("view"), "position": r.get("position"),
               "gap": None, "ret": None, "auction": None, "close": None, "zone": None}
        if ed:
            q = (quotes_by_day.get(ed) or {}).get(str(r["code"])) or {}
            ap, pc = _num(q.get("auction_price")), _num(q.get("prev_close"))
            if ap and pc:
                rec["gap"] = round((ap - pc) / pc * 100, 2)
                rec["auction"] = ap
            if ap:
                cl = _num((close_cache.get(str(r["code"])) or {}).get(ed.replace("-", "")))
                if cl:
                    rec["close"] = cl
                    rec["ret"] = round((cl - ap) / ap * 100, 2)
        out_rows.append(rec)

    def _agg(rs: list[dict]) -> dict:
        ok = [x for x in rs if x["ret"] is not None]
        exe = [x for x in ok if x["gap"] is not None and x["gap"] <= HARD_LINE]
        return {
            "n": len(rs), "covered": len(ok),
            "executable": len(exe),
            "exec_rate": round(len(exe) / len(ok) * 100, 1) if ok else None,
            "ret_all": round(statistics.mean([x["ret"] for x in ok]), 3) if ok else None,
            "ret_exe": round(statistics.mean([x["ret"] for x in exe]), 3) if exe else None,
            "win_rate_exe": round(sum(1 for x in exe if x["ret"] > 0) / len(exe) * 100, 1) if exe else None,
        }

    by_tier = {t: _agg([x for x in out_rows if x["view"] == t]) for t in ("participate", "watch", "skip")}
    # 错杀：判 watch/skip 却在执行日上涨的
    mis = sorted([x for x in out_rows if x["view"] in ("watch", "skip") and (x["ret"] or 0) >= 3],
                 key=lambda x: -(x["ret"] or 0))
    # 计划参与但未成交
    unfilled = [x for x in out_rows if x["view"] == "participate"
                and (x["gap"] is None or x["gap"] > HARD_LINE or x["ret"] is None)]
    # 按执行日聚合（空仓日/成交日）
    by_day: dict[str, dict] = OrderedDict()
    for x in out_rows:
        if not x["exec_date"]:
            continue
        d = by_day.setdefault(x["exec_date"], {"exec_date": x["exec_date"], "participate": 0,
                                               "watch": 0, "skip": 0, "rets": [],
                                               "part_rets": [], "part_filled": 0, "inband": []})
        d[x["view"]] = d.get(x["view"], 0) + 1
        if x["ret"] is not None:
            d["rets"].append(x["ret"])
            # 「硬线内红开」口径（2026-09-18 加）：与 hibernate 逃生阀同一准入
            # （0 < 竞价 ≤ 5%）→ 回答「空仓日开阀到底能买到什么」，比全池均值诚实。
            if x["gap"] is not None and 0 < x["gap"] <= HARD_LINE:
                d["inband"].append(x["ret"])
            if x["view"] == "participate":
                d["part_rets"].append(x["ret"])
                if x["gap"] is not None and x["gap"] <= HARD_LINE:
                    d["part_filled"] += 1
    for d in by_day.values():
        d["cand_avg_ret"] = round(statistics.mean(d.pop("rets")), 3) if d.get("rets") else None
        d["part_ret"] = round(statistics.mean(d.pop("part_rets")), 3) if d.get("part_rets") else None
        _ib = d.pop("inband")
        d["inband_n"] = len(_ib)
        d["inband_avg_ret"] = round(statistics.mean(_ib), 3) if _ib else None
    return {"rows": out_rows, "by_tier": by_tier, "missed": mis, "unfilled": unfilled,
            "by_day": list(by_day.values()), "hard_line": HARD_LINE}


def stance_review() -> dict:
    """定调层复盘：每日 AI 定调（stance）× 执行日候选池结果 × 出手数。

    stance 来自 cache/ai_view_{执行日}.json（AI 在**依据日盘后**自定·不看执行日盘面）。
    机会成本口径：hibernate 日若按池均收益计（AI 本该一只不买 → 差额即错过）。
    """
    days = _trade_days()
    out = []
    for d in days:
        p = BASE / "cache" / f"ai_view_{d}.json"
        if not p.exists():
            continue
        v = _json(p, {}) or {}
        views = v.get("views") or {}
        c = Counter(str((x or {}).get("view") or "") for x in views.values())
        out.append({
            "exec_date": d, "decision_date": v.get("decision_date"),
            "stance": v.get("stance"), "label": v.get("stance_label"),
            "direction": v.get("direction"),
            "participate": c.get("participate", 0), "watch": c.get("watch", 0), "skip": c.get("skip", 0),
            "note": (v.get("note") or "")[:160],
        })
    return {"days": out,
            "hibernate_days": [x["exec_date"] for x in out if x["stance"] == "hibernate"],
            "participate_days": [x["exec_date"] for x in out if x["participate"] > 0]}


def overcaution_attr() -> dict:
    """过虑归因：watch/skip 的 reason 按「顾虑类型」分类，看哪类顾虑真有效。

    判定逻辑：某类顾虑的「当日收益均值 / 胜率 / ≥3% 占比」若 ≥ 全体 watch+skip 基准、
    则该顾虑**不构成否决理由**（只应影响仓位）——对齐 skill 的「否决=硬伤制」。
    """
    import re
    pat = {
        "封板质量(开板/砸开/封单小)": r"开板|砸开|炸开|封板质量|封单|封得晚|封板时间|被砸",
        "位置高(历史极限/高位)": r"位置高|位置不低|太高|高位|历史最高|历史最长|追平|极限|已经\d+板|到.{0,4}板位置",
        "方向独苗(没同伴/只有它)": r"独苗|就它一?家|只有它|没有同伴|没有同方向|孤军|单打独斗|没跟上",
        "基本面瑕疵(澄清/业绩)": r"澄清|业绩|利润|净利|下滑|亏损|风险提示|公告",
        "纯跟风/传闻": r"纯跟|传闻|没有硬消息|辟谣",
        "市场冷/情绪差": r"市场很冷|情绪.{0,3}冷|冰点|退潮|不接力|市场冷",
    }
    rows = decision_outcome()["rows"]
    dec = {(r["decision_date"], r["code"]): r for r in _jsonl(DECISIONS)}
    buckets: dict[str, list[float]] = defaultdict(list)
    base: list[float] = []
    for k, o in dec.items():
        if str(o.get("view")) not in ("watch", "skip"):
            continue
        row = next((x for x in rows if x["decision_date"] == k[0] and x["code"] == k[1]), None)
        if not row or row["ret"] is None:
            continue
        base.append(row["ret"])
        txt = str(o.get("reason") or "")
        for name, p in pat.items():
            if re.search(p, txt):
                buckets[name].append(row["ret"])

    def _s(v: list[float]) -> dict:
        return {"n": len(v), "avg": round(statistics.mean(v), 2) if v else None,
                "win": round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else None,
                "ge3": round(sum(1 for x in v if x >= 3) / len(v) * 100, 1) if v else None}
    items = [{"concern": k, **_s(v)} for k, v in sorted(buckets.items(),
             key=lambda x: -(statistics.mean(x[1]) if x[1] else -99))]
    return {"base": _s(base), "items": items}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--recent", type=int, default=0, help="明细只显示最近 N 个决策日")
    args = ap.parse_args()

    led = ledger_stats()
    dec = decision_outcome()
    st = stance_review()
    oc = overcaution_attr()
    payload = {"schema": "ai_self_review_v1",
               "generated_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
               "ledger": led, "decision": {k: v for k, v in dec.items() if k != "rows"},
               "stance": st, "overcaution": oc, "rows": dec["rows"]}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    hl = led
    o, a = hl["old_caliber"], hl["ai_caliber"]
    print("=" * 74)
    print("A) 账本口径战绩（真实成交）")
    print("=" * 74)
    print(f"老口径 {o['from']}~{o['to']} | {o['days']}日/{o['stock_n']}票 | 日胜率 {o['day_win_rate']}% "
          f"票胜率 {o['stock_win_rate']}% | 日均 {o['avg_day_pct']:+.3f}% | 净值 {o['nav_from']:.6f}→{o['nav_to']:.6f} "
          f"({(o['nav_to']/o['nav_from']-1)*100:+.2f}%) | 盈亏比 {o['pl_ratio']}")
    if a["days"]:
        print(f"AI口径 {a['from']}~{a['to']} | {a['days']}日/{a['stock_n']}票 | 日胜率 {a['day_win_rate']}% "
              f"票胜率 {a['stock_win_rate']}% | 日均 {a['avg_day_pct']:+.3f}% | 净值 {a['nav_from']:.6f}→{a['nav_to']:.6f} "
              f"({(a['nav_to']/a['nav_from']-1)*100:+.2f}%)")
    t = hl["ai_track"]
    print(f"AI轨独立净值 1.0→{t['nav_to']:.6f} ({(t['nav_to']-1)*100:+.2f}%) | {t['win']}/{t['n']} 笔盈利")
    for d in t["detail"]:
        print(f"    {d['trade_date']} {d['name']}({d['code']}) 竞价{d['auction']}→收{d['close']} {d['ret']:+.2f}%")
    dd = hl["drawdown"]
    print(f"回撤：峰值 {dd['peak']:.6f}({dd['peak_date']}) 当前 {dd['now']:.6f} → 当前回撤 {dd['cur_dd']:+.2f}% | "
          f"历史最大 {dd['max_dd']:.2f}% ({dd['max_dd_window']})")

    print()
    print("=" * 74)
    print(f"B) 决策落地（AI 三档 × 执行日结果·硬线 {dec['hard_line']:g}%）")
    print("=" * 74)
    print(f"{'档位':<12}{'样本':<6}{'覆盖':<6}{'可执行':<8}{'可执行率':<10}{'可执行收益':<12}{'可执行胜率':<10}{'全样本收益'}")
    for k, v in dec["by_tier"].items():
        print(f"{k:<12}{v['n']:<6}{v['covered']:<6}{v['executable']:<8}"
              f"{(str(v['exec_rate'])+'%') if v['exec_rate'] is not None else '-':<10}"
              f"{(str(v['ret_exe'])+'%') if v['ret_exe'] is not None else '-':<12}"
              f"{(str(v['win_rate_exe'])+'%') if v['win_rate_exe'] is not None else '-':<10}"
              f"{v['ret_all']}%")
    print("\n-- 判「参与」未成交（纪律外/无数据）--")
    for x in dec["unfilled"]:
        print(f"    {x['exec_date']} {x['name']}({x['code']}) 竞价{x['gap']}% → {'超硬线不追' if (x['gap'] or 0) > dec['hard_line'] else '缺竞价/收盘数据'}")
    print("\n-- 错杀盘点：判「观察/放弃」却涨≥3% --")
    for x in dec["missed"][:15]:
        print(f"    {x['exec_date']} {x['name']}({x['code']}) {x['view']:<5} 竞价{x['gap']}% → 当日{x['ret']:+.2f}%")

    print()
    print("=" * 74)
    print("C) 逐执行日（AI 出手 vs 候选池平均）")
    print("=" * 74)
    print(f"{'执行日':<12}{'参与':<6}{'成交':<6}{'观察':<6}{'放弃':<6}{'参票收益':<12}{'候选池均值'}")
    for d in dec["by_day"]:
        print(f"{d['exec_date']:<12}{d.get('participate',0):<6}{d['part_filled']:<6}{d.get('watch',0):<6}"
              f"{d.get('skip',0):<6}"
              f"{(str(d['part_ret'])+'%') if d['part_ret'] is not None else '-':<12}{d['cand_avg_ret']}%")

    print()
    print("=" * 74)
    print("D) 定调层复盘（AI 定调 × 执行日池均 × 出手数）")
    print("=" * 74)
    day_map = {d["exec_date"]: d for d in dec["by_day"]}
    print(f"{'执行日':<12}{'定调':<12}{'参与':<6}{'全池均值':<11}{'红开≤5%均值':<14}{'参票收益'}")
    opp_all, opp_in = 0.0, 0.0
    n_hib = 0
    for s in st["days"]:
        dm = day_map.get(s["exec_date"]) or {}
        cavg, iavg = dm.get("cand_avg_ret"), dm.get("inband_avg_ret")
        inb = dm.get("inband_n")
        pr = dm.get("part_ret")
        flag = ""
        if s["stance"] == "hibernate" and s["participate"] == 0 and cavg is not None and cavg > 0:
            flag = "  ← 空仓（全池正·但看红开列）"
            opp_all += cavg
            n_hib += 1
            opp_in += (iavg or 0.0)
        print(f"{s['exec_date']:<12}{str(s['stance']):<12}{s['participate']:<6}"
              f"{(str(cavg)+'%') if cavg is not None else '-':<11}"
              f"{((str(iavg)+'% (n='+str(inb)+')') if iavg is not None else '-'):<14}"
              f"{(str(pr)+'%') if pr is not None else '-':<8}{flag}")
    print(f"空仓日机会成本（{n_hib} 次**合计**）：「全池均」口径 {opp_all:+.2f}%｜"
          f"「红开≤5% 可执行」口径 {opp_in:+.2f}%  ← 后者才是「开阀真能买到什么」")
    print("  ⚠️ 两口径差距来自「低开反转票」（竞价绿开、当日大涨）——逃生阀只买红开，吃不到那部分。")

    print()
    print("=" * 74)
    print("E) 过虑归因（判「观察/放弃」的顾虑类型 × 事后结果）")
    print("=" * 74)
    b = oc["base"]
    print(f"基准·全部 watch+skip：n={b['n']} 均值 {b['avg']:+.2f}% 胜率 {b['win']}% ≥3%占比 {b['ge3']}%")
    print(f"{'顾虑类型':<26}{'样本':<6}{'收益均值':<11}{'胜率':<9}{'≥3%占比':<9}{'结论'}")
    for it in oc["items"]:
        dlt = (it["avg"] - b["avg"]) if (it["avg"] is not None and b["avg"] is not None) else 0.0
        if dlt >= 0.3:
            verdict = "❌ 过虑·误杀（这类顾虑反而更赚）"
        elif dlt >= -0.5:
            verdict = "⚪ 微效·不足以否决（最多影响仓位）"
        else:
            verdict = "✅ 有效·可保留否决"
        if it["n"] < 10:
            verdict += "（样本不足）"
        print(f"{it['concern']:<26}{it['n']:<6}{it['avg']:+.2f}%{'':<6}{(str(it['win'])+'%'):<9}{(str(it['ge3'])+'%'):<9}{verdict}")

    print("\n-- 明细（最近 N 决策日）--")
    show = dec["rows"]
    if args.recent:
        kd = sorted({x["decision_date"] for x in show})[-args.recent:]
        show = [x for x in show if x["decision_date"] in kd]
    cur = None
    for x in show:
        if x["decision_date"] != cur:
            cur = x["decision_date"]
            print(f"  [{cur}]")
        g = f"{x['gap']:+.2f}%" if x["gap"] is not None else "  -  "
        rt = f"{x['ret']:+.2f}%" if x["ret"] is not None else "  -  "
        print(f"    {x['exec_date'] or '     -    '} {x['name']}({x['code']}) {x['view']:<11} 竞价{g:>8} → {rt:>8}")
    print(f"\n[ai_self_review] 已写 {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
