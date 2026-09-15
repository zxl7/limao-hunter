#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 判断平行台账（2026-09-10 用户「把我的思考记到真实台账·和 Python 脚本轨对比」）。

背景（2026-09-14 更新·用户「全量以 AI 为唯一口径！」）：
  · **账户账本** `account_nav_history.jsonl` 的买入口径已切到 AI 决策
    （见 `core/ai_caliber.py`：成交 = AI 判 participate ∧ 竞价涨幅 ≤ AI 自定 entry_max_gap
     ∧ 有竞价数据），机械 signal_eval 降级为**参考信号**（卡面照常渲染，不再决定买卖与战绩）。
  · 本轨 `ai_nav_history.jsonl` 因而成为 **AI 口径的独立计分本**：同口径、同算法
    （T+1 竞价价买入 → T+1 收盘价结算·等权·nav 递推），但起点独立 1.0——
    账户账本含 09-14 之前的机械口径历史（14 个交易日），无法拆出「AI 判断本身」的成绩，
    故保留本轨单列。⛔ 两轨每日成交集合同源，数字应逐日一致（仅起点不同）；不一致即为 bug。

文件（全部 append-only，禁覆盖）：
  wealth_code/cache/ai_decisions.jsonl   AI 每日决策（decision_date=T 盘后 · trade_date=T+1 执行）
  wealth_code/cache/ai_nav_history.jsonl AI 轨账本（字段与 Python 轨同构 + strategy 区分）

口径（与 pipeline_verify 同源）：
  return_pct = (T+1 收盘 − T+1 竞价成交价) / T+1 竞价成交价 × 100
  avg_return_pct = 当日实际成交票等权均值；nav_t = nav_{t-1} × (1 + avg/100)
  入场规则由 AI 决策自带（entry_max_gap，默认 ≤3% 才参与、超则放弃）；竞价价缺失→跳过（诚实）
  ⛔ 0 成交日**不写账本行**（与账户账本同语义：账本只记有实际买入的交易日）

用法：
  python3 wealth_code/scripts/ai_ledger.py record --decision-date 2026-09-10 --file /tmp/ai_picks.json
  python3 wealth_code/scripts/ai_ledger.py settle --trade-date 2026-09-11
  python3 wealth_code/scripts/ai_ledger.py status
  python3 wealth_code/scripts/ai_ledger.py compare --days 30
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
BASE = ROOT.parent                                      # 狸猫猎手/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BJ_TZ = timezone(timedelta(hours=8))
CACHE = ROOT / "cache"
DECISIONS = CACHE / "ai_decisions.jsonl"
AI_LEDGER = CACHE / "ai_nav_history.jsonl"
PY_LEDGER = CACHE / "account_nav_history.jsonl"
# ⛔ AI 判断视图目录 = 狸猫猎手/cache（**不是** wealth_code/cache）——2026-09-11 修：
#    消费方 build_cards._load_ai_view 读 ROOT.parent/cache，此前本脚本写 wealth_code/cache，
#    写进去也读不到（两侧测试各自 mock 自己那半 → 接缝零覆盖，09-10 上线至今未暴露）。
#    改动此常量必须同步 build_cards.AI_VIEW_DIR，并由 test_ai_view_dir_seam 锁死。
AI_VIEW_DIR = BASE / "cache"
BASE_NAV = 1.0                      # AI 轨起点净值（独立计）
DEFAULT_ENTRY_MAX_GAP = 3.0         # AI 自定上限的兜底值（仅留痕·⛔ 不参与裁决）
STRATEGY = "ai_judgement_next_day_close"

# ── 统一买入口径（2026-09-14 用户「AI 决定要买的，低于 5% 直接买」）────────────
# 单一源 = `core/ai_caliber.USER_ENTRY_MAX_GAP_PCT`；本轨结算必须与账户账本同值，
# 否则两轨在「同一票买没买到」上会打架（09-14 闽东电力实证：账户口径已修，
# AI 轨若漏改会继续按 AI 自定 2% 记 0 成交 → 与卡面/账户账本三方不一致）。
# 单票仓位上限 50% 同理（`MAX_POSITION_PER_STOCK`，写入台账时裁剪）。
try:
    from core import ai_caliber as _ai_caliber
except Exception:                                     # 独立运行兜底（sys.path 已含 ROOT）
    _ai_caliber = None

USER_ENTRY_MAX_GAP_PCT = getattr(_ai_caliber, "USER_ENTRY_MAX_GAP_PCT", 5.0)
MAX_POSITION_PER_STOCK = getattr(_ai_caliber, "MAX_POSITION_PER_STOCK", 0.5)


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _append_jsonl(p: Path, row: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _nav_now() -> float:
    rows = _read_jsonl(AI_LEDGER)
    return float(rows[-1]["nav"]) if rows else BASE_NAV


def _next_trade_day(date10: str) -> str:
    """次一交易日（跳周末·与 pipeline_verify._next_day 同口径）。"""
    from datetime import datetime as _dt, timedelta as _td
    try:
        d = _dt.strptime(date10, "%Y-%m-%d") + _td(days=1)
        while d.weekday() >= 5:
            d += _td(days=1)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return date10


def _write_ai_view(trade_date: str, decision_date: str, views: dict,
                   meta: dict | None = None) -> Path:
    """写 AI 判断视图（合并进竞价验证·build_verify_card 消费）。

    meta（2026-09-11 用户「竞价卡要告诉我今天买什么」）：stance/stance_label/direction/note
    → 竞价卡顶部「🤖 AI 今日买入计划」段的定调与方向；缺省则只渲染逐票 AI 判断行。
    幂等：同 trade_date 重复写按 code 合并（后写覆盖同 code），不丢已有 views。
    """
    p = AI_VIEW_DIR / f"ai_view_{trade_date}.json"
    prev: dict = {}
    try:
        prev = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    p.parent.mkdir(parents=True, exist_ok=True)
    merged_views = dict(prev.get("views") or {})
    merged_views.update(views)
    out = {k: v for k, v in prev.items() if k not in ("views", "date", "decision_date", "ts")}
    out.update({
        "date": trade_date,
        "decision_date": decision_date,
        "views": merged_views,
        "source": "AI",
        "ts": datetime.now(BJ_TZ).isoformat(),
    })
    for k in ("stance", "stance_label", "direction", "note", "decided_at", "entry_max_gap"):
        if meta and meta.get(k) is not None:
            out[k] = meta[k]
    out.setdefault("decided_at", out.get("ts"))
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# ── 1) 记录 AI 决策 ───────────────────────────────────────────────
def cmd_record(args) -> int:
    ddate = args.decision_date or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    picks: list[dict] = []
    meta: dict = {}
    if args.file:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            picks = raw
        else:
            picks = raw.get("picks") or []
            meta = {k: raw.get(k) for k in ("stance", "stance_label", "direction", "note", "entry_max_gap")}
            meta = {k: v for k, v in meta.items() if v is not None}
    if not picks:
        print("ERROR: 无决策内容（--file 需为 [{code,name,reason,entry_max_gap?,position?}]）", file=sys.stderr)
        return 2
    trade_date = str(getattr(args, "trade_date", None) or _next_trade_day(ddate))
    existing = {(r.get("decision_date"), r.get("code")) for r in _read_jsonl(DECISIONS)}
    view_map: dict[str, dict] = {}
    n = 0
    for pk in picks:
        code = str(pk.get("code") or "").strip()
        if len(code) != 6:
            print(f"  跳过非法代码: {pk}", file=sys.stderr)
            continue
        if (ddate, code) in existing:
            print(f"  跳过重复决策: {ddate} {code}")
            continue
        row = {
            "decision_date": ddate,
            "code": code,
            "name": pk.get("name") or "",
            "reason": (pk.get("reason") or "").strip(),
            "factors": pk.get("factors") or {},
            "entry_max_gap": float(pk.get("entry_max_gap", DEFAULT_ENTRY_MAX_GAP)),
            # 单票仓位上限 50%（2026-09-14 用户「买入仓位不超过 50%」）——AI 给超了就裁到这里
            "position": (_ai_caliber.clamp_position(pk.get("position")) if _ai_caliber
                         else pk.get("position")),
            "view": (pk.get("view") or None),      # participate/skip/watch（供竞价验证卡渲染）
            "source": "AI",
            "ts": datetime.now(BJ_TZ).isoformat(),
            "settled": False,
        }
        if not row["reason"]:
            print(f"  跳过无理由决策: {code}（reason 必填·数据即事实）", file=sys.stderr)
            continue
        _append_jsonl(DECISIONS, row)
        n += 1
        print(f"  ✓ 记录 AI 决策 {ddate} {row['name']}({code}) {row['reason'][:40]}")
        # 🤖 合并进竞价验证（2026-09-10 用户）：同步写 ai_view_{trade_date}.json
        # → 次日 build_verify_card 渲染「🤖 AI 判断」行，与机器信号同卡对照（不再另开平行台账查看）
        if row.get("view"):
            view = view_map.setdefault(code, {})
            # ⛔ entry_max_gap 必须带进 views（2026-09-14 修）：此前只 UPDATE view/reason/factors，
            #    导致 build_cards._ai_plan_block 取不到逐票上限 → 回落到顶层 meta 默认 3.0，
            #    卡面显示「高开超上限+3%」而 AI 理由里写「开超2%就不买」（闽东电力实证），
            #    且与结算口径不一致（cmd_settle 读决策行的 2.0）。三处必须同值。
            view.update({
                "view": row["view"],
                "reason": row["reason"],
                "factors": row["factors"],
                "entry_max_gap": row["entry_max_gap"],
            })
    if view_map:
        meta.setdefault("entry_max_gap", DEFAULT_ENTRY_MAX_GAP)
        p = _write_ai_view(trade_date, ddate, view_map, meta=meta)
        print(f"[ai_ledger] 🤖 AI 判断视图已写 {p}（{len(view_map)} 只·供竞价验证卡渲染"
              f"{'·含今日买入计划' if meta.get('stance_label') or meta.get('note') else ''}）")
    print(f"[ai_ledger] 记录 {n} 条 AI 决策 → {DECISIONS.name}")
    return 0


# ── 2) 结算（同口径：T+1 竞价买 → T+1 收盘卖）────────────────────────
def _load_quotes(trade_date: str) -> dict:
    p = CACHE / f"auction_quotes-{trade_date}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _close_of(code: str, trade_date: str) -> float | None:
    """T+1 收盘价（腾讯日K·前复权·ifzq host 2026-09-09 已修）。失败→None（诚实跳过）。"""
    try:
        import common.tencent_kline as tk
        rows = tk.fetch_kline(code, n=8)          # [(date, close), ...]
        key = str(trade_date).replace("-", "")
        for d, c in rows:
            if str(d).replace("-", "") == key:
                return float(c)
    except Exception:
        pass
    return None


def cmd_settle(args) -> int:
    tdate = args.trade_date
    if not tdate:
        print("ERROR: --trade-date 必填（AI 决策的执行日 T+1）", file=sys.stderr)
        return 2
    rows = _read_jsonl(DECISIONS)
    due = [r for r in rows if not r.get("settled") and str(r.get("decision_date") or "") < tdate]
    # ⛔ 只有 view=participate（AI 决定买）才计入 AI 收益；watch/skip 是 AI 看过但没买的，
    #    一并标记 settled 但不进收益（2026-09-11 修：原实现把所有待结算决策都当买入，
    #    会把「观察/放弃」的票算进 AI 成绩 → 收益虚高失真）。
    todo = [r for r in due if str(r.get("view") or "participate") == "participate"]
    others = [r for r in due if str(r.get("view") or "participate") != "participate"]
    if not due:
        print(f"[ai_ledger] {tdate} 无待结算 AI 决策")
        return 0
    quotes = _load_quotes(tdate)
    traded, skipped = [], []
    for r in others:
        skipped.append((str(r.get("code")), f"AI 本次没选它（{r.get('view')}）·不参与收益统计"))
    for r in todo:
        code = str(r["code"])
        q = quotes.get(code) or {}
        ap = q.get("auction_price")
        prev_close = q.get("prev_close")
        if ap is None:
            skipped.append((code, "无竞价数据（auction_quotes 缺失该票）"))
            continue
        gap = ((float(ap) - float(prev_close)) / float(prev_close) * 100) if prev_close else None
        # ⛔ 用**用户 5% 硬线**（2026-09-14 用户纪律），不再读 AI 自定的 entry_max_gap。
        if gap is not None and gap > USER_ENTRY_MAX_GAP_PCT:
            skipped.append((code, f"竞价高开 {gap:.2f}% 超过 {USER_ENTRY_MAX_GAP_PCT:g}% → 不追"))
            continue
        close = _close_of(code, tdate)
        if close is None:
            skipped.append((code, f"缺 T+1 收盘价（{tdate}）"))
            continue
        ret = round((float(close) - float(ap)) / float(ap) * 100, 2)
        traded.append({"code": code, "name": r.get("name") or "", "auction_price": float(ap),
                       "close": float(close), "return_pct": ret, "reason": r.get("reason")})
    # 账本：**只记有实际买入的交易日**（2026-09-14 用户「全量以 AI 为唯一口径」后与
    # 账户账本 account_nav_history.jsonl 同语义——那边由 nav_ledger 只收 can_enter ∧ covered
    # 的样本，0 成交不落行；此处若给空仓日写 0% 行，会让「记录天数」与胜率口径两轨打架）。
    avg = round(sum(t["return_pct"] for t in traded) / len(traded), 4) if traded else 0.0
    if traded:
        nav = round(_nav_now() * (1 + avg / 100.0), 6)
        _append_jsonl(AI_LEDGER, {
            "trade_date": tdate,
            "recommendation_date": sorted({str(r["decision_date"]) for r in due})[-1],
            "strategy": STRATEGY,
            "stock_count": len(traded),
            "avg_return_pct": avg,
            "nav": nav,
            "stocks": traded,
            "skipped": [{"code": c, "note": n} for c, n in skipped],
            "decided": len(todo),
            "not_picked": len(others),
            "ts": datetime.now(BJ_TZ).isoformat(),
        })
    else:
        nav = _nav_now()
        print(f"  AI 今日 0 成交（{len(skipped)} 只未买到/未选）→ 不写账本行（净值 {nav} 不变）")
    # 标记已结算（重写 decisions 文件·保留全部字段·due 全量=含未选中的票，防反复待结算）
    settled_keys = {(str(r["decision_date"]), str(r["code"])) for r in due}
    for r in rows:
        if (str(r.get("decision_date")), str(r.get("code"))) in settled_keys:
            r["settled"] = True
            r["settled_trade_date"] = tdate
    DECISIONS.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"[ai_ledger] {tdate} 结算：AI 选中 {len(todo)} 只 → 成交 {len(traded)} 只·跳过 {len(skipped)} 只 | "
          f"avg={avg}% nav={nav}")
    for t in traded:
        print(f"    ✓ {t['name']}({t['code']}) 竞价{t['auction_price']} → 收盘{t['close']} = {t['return_pct']}%")
    for c, n in skipped:
        print(f"    – {c}: {n}")
    return 0


# ── 3) 状态 / 对比 ────────────────────────────────────────────────
def _stats(rows: list[dict]) -> dict:
    if not rows:
        return {"days": 0, "win_rate": None, "avg_return_pct": None, "nav": None}
    wins = sum(1 for r in rows if float(r.get("avg_return_pct") or 0) > 0)
    avg = sum(float(r.get("avg_return_pct") or 0) for r in rows) / len(rows)
    return {"days": len(rows), "win_rate": round(wins / len(rows) * 100, 1),
            "avg_return_pct": round(avg, 3), "nav": float(rows[-1].get("nav") or 0)}


def cmd_status(args) -> int:
    ai = _stats(_read_jsonl(AI_LEDGER))
    print(f"[AI 轨] 交易日 {ai['days']} | 胜率 {ai['win_rate']}% | 日均 {ai['avg_return_pct']}% | 净值 {ai['nav']}")
    pend = [r for r in _read_jsonl(DECISIONS) if not r.get("settled")]
    for r in pend:
        print(f"  待结算: {r['decision_date']} {r['name']}({r['code']}) — {str(r.get('reason'))[:50]}")
    return 0


def cmd_compare(args) -> int:
    days = int(args.days or 30)
    ai_rows = _read_jsonl(AI_LEDGER)[-days:]
    py_rows = _read_jsonl(PY_LEDGER)[-days:]
    a, p = _stats(ai_rows), _stats(py_rows)

    def _f(v, suffix="%"):
        # ⛔ AI 轨尚无结算行时（首日/任务漏跑）胜率与净值均为 None，
        #    直接 f-string 格式化会 TypeError 崩掉（2026-09-14 实测）→ 诚实显示「—」。
        return f"{v}{suffix}" if v is not None else "—"

    print(f"== AI 判断轨 vs Python 脚本轨（近 {days} 个结算日）==")
    print(f"  {'轨':<14}{'交易日':>6}{'胜率':>10}{'日均收益':>12}{'净值':>12}")
    print(f"  {'AI 判断（我）':<14}{a['days']:>6}{_f(a['win_rate']):>10}{_f(a['avg_return_pct']):>12}{_f(a['nav'], ''):>12}")
    print(f"  {'Python 脚本':<14}{p['days']:>6}{_f(p['win_rate']):>10}{_f(p['avg_return_pct']):>12}{_f(p['nav'], ''):>12}")
    if a["days"] == 0:
        print("  ℹ️ AI 轨还没有结算记录（首日要等当日 15:30 接力卡任务 settle 落首行）")
    if a["nav"] and p["nav"]:
        print(f"  → 净值比 AI/Python = {a['nav'] / p['nav']:.3f}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="AI 判断平行台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record"); r.add_argument("--decision-date"); r.add_argument("--file", required=True)
    r.add_argument("--trade-date", help="执行日（默认=decision_date 次一交易日）")
    r.set_defaults(func=cmd_record)
    s = sub.add_parser("settle"); s.add_argument("--trade-date", required=True); s.set_defaults(func=cmd_settle)
    st = sub.add_parser("status"); st.set_defaults(func=cmd_status)
    c = sub.add_parser("compare"); c.add_argument("--days", type=int, default=30); c.set_defaults(func=cmd_compare)
    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
