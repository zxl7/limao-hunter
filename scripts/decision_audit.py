#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""决策质量审计（反事实账本）· 2026-09-17 立项（用户拍板「按你设计的来·继续迭代」）

背景（为什么需要这个脚本）：
  `account_nav_history.jsonl` **只在有成交时才写行** → 2026-09-14 起每日 stock_count=1~2；
  而候选池/候选决策每日 10~14 只。⇒ 学习层（learning.py R1-R4）以 1/N 的信息量在学，
  只能在 `expected_range` 这类「无需成交也有样本」的代理指标上打转，
  学出的 R1「区间命中率低 → base_range 下移」连续 8 日方向都不对（见 automations/27c81420 memory）。

本脚本做一件事：**不管成不成交，都按决策回算「若按纪律执行会怎样」**。
  · 样本量：1~2 只/日 → 10~15 只/日（覆盖全候选池）
  · 学习对象：从「预测精度（区间命中率）」→「决策质量（判断有没有区分度）」

⛔ 边界（硬约束，勿越）：
  · **只读**：不改任何阈值 / 不写 nav 账本 / 不推送 / 不碰自动化 / 不动 skill
  · 产物只有三个：cache/decision_audit.json / .md（报告）+ cache/audit_close_cache.json（收盘价缓存）
  · 口径与既有系统同源（禁另造口径）：
      return_pct = (T 收盘 − T 竞价成交价) / T 竞价成交价 × 100   ← 同 wealth_code/scripts/ai_ledger.py
      入场硬线   = core.ai_caliber.USER_ENTRY_MAX_GAP_PCT（5%）  ← 单一源
      日K        = common.tencent_kline.fetch_kline（同 ai_ledger._close_of）
  · 样本 < 30 的分组一律标注「样本不足」，只呈现不裁决

三个口径并列（互不混淆）：
  A) 池档口径  relay / watch / early_ambush × 实际结果   —— 样本大，回答「哪些档位值得买」
  B) AI 口径   participate / watch / skip  × 实际结果   —— 样本小（09-14 起），回答「我的判断有没有效」
  C) 门槛成本  按竞价涨幅分档（≤硬线 / >硬线 / 一字板）—— 回答「5% 硬线到底在保护还是在挡钱」

用法：
  python3 scripts/decision_audit.py                 # 默认最近 30 个交易日
  python3 scripts/decision_audit.py --days 15
  python3 scripts/decision_audit.py --no-fetch      # 只用已缓存的收盘价（离线）
  python3 scripts/decision_audit.py --json          # 只出 JSON 摘要
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent           # 狸猫猎手/
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
WC = BASE / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

CACHE = BASE / "cache"
WCCACHE = WC / "cache"
ZT_HIST = CACHE / "zt_history"
OUT_JSON = CACHE / "decision_audit.json"
OUT_MD = CACHE / "decision_audit.md"
CLOSE_CACHE = CACHE / "audit_close_cache.json"

BJ_TZ = timezone(timedelta(hours=8))
POOLS = ("relay", "watch", "early_ambush")
AI_TIERS = ("participate", "watch", "skip")
LOW_SAMPLE = 30            # 分组样本 < 此值 → 只呈现不裁决

# 入场硬线：单一源 = core.ai_caliber.USER_ENTRY_MAX_GAP_PCT（2026-09-14 用户纪律）
try:
    from core.ai_caliber import USER_ENTRY_MAX_GAP_PCT as ENTRY_GAP_MAX
except Exception:                                       # 独立运行兜底
    ENTRY_GAP_MAX = 5.0


# ────────────────────────── 基础 IO ──────────────────────────
def _load(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None                     # 拒 NaN
    except (TypeError, ValueError):
        return None


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


# ────────────────────────── 交易日历（仓内自证·不依赖外部） ──────────────────────────
def trading_days() -> list[str]:
    """已归档完整涨停池的交易日（升序）。zt_history 是仓内唯一交易日证据。"""
    out = []
    for f in sorted(ZT_HIST.glob("zt_*.json")):
        d = _load(f, {}) or {}
        if d.get("zt_list"):
            out.append(str(d.get("date") or f.stem.replace("zt_", "")))
    return sorted(set(out))


def _next_day(days: list[str], d: str) -> str | None:
    try:
        i = days.index(d)
    except ValueError:
        return None
    return days[i + 1] if i + 1 < len(days) else None


# ────────────────────────── 收盘价（带缓存·网络只补缺口） ──────────────────────────
def load_close_cache() -> dict:
    c = _load(CLOSE_CACHE, {}) or {}
    return c if isinstance(c, dict) else {}


def save_close_cache(c: dict) -> None:
    CLOSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CLOSE_CACHE.write_text(json.dumps(c, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def fetch_closes(codes: list[str], cache: dict, allow_fetch: bool) -> tuple[dict, list[str]]:
    """补全 codes 的日K收盘（{code: {YYYY-MM-DD: close}}）。返回 (cache, 失败列表)。"""
    todo = [c for c in codes if c not in cache]
    if not todo or not allow_fetch:
        return cache, (todo if not allow_fetch else [])
    failed: list[str] = []
    try:
        import common.tencent_kline as tk
    except Exception as e:                                # pragma: no cover
        print(f"[decision_audit] ⚠️ 日K模块不可用（{e}）→ 收盘价全部缺失，仅出涨停口径", file=sys.stderr)
        return cache, todo

    def one(code: str) -> tuple[str, dict | None]:
        try:
            rows = tk.fetch_kline(code, n=80)             # 80 根足够覆盖回看窗口
            if not rows:
                return code, None
            return code, {str(d)[:10].replace("-", ""): float(c) for d, c in rows}
        except Exception:
            return code, None

    with ThreadPoolExecutor(max_workers=6) as ex:
        for code, got in ex.map(one, todo):
            if got is None:
                failed.append(code)
            else:
                cache[code] = got                          # {YYYYMMDD: close}
    return cache, failed


def close_of(cache: dict, code: str, date10: str) -> float | None:
    m = cache.get(code) or {}
    return _num(m.get(str(date10).replace("-", "")))


# ────────────────────────── 组装样本 ──────────────────────────
def build_rows(days: list[str], cache: dict) -> tuple[list[dict], list[str]]:
    """逐执行日组装 (候选池档 + AI 档) × 结果。"""
    rows: list[dict] = []
    codes: set[str] = set()
    zt_map = {d: {s.get("code"): s for s in ((_load(ZT_HIST / f"zt_{d}.json", {}) or {}).get("zt_list") or [])}
              for d in days}

    for t in days:
        rec = _prev(days, t)
        if not rec:
            continue
        cand = _load(WCCACHE / f"candidates-{rec}.json", {}) or {}
        quotes = _load(WCCACHE / f"auction_quotes-{t}.json", {}) or {}
        if not isinstance(quotes, dict):
            quotes = {}
        if not cand.get("relay") and not cand.get("watch"):
            continue

        nxt = _next_day(days, t)

        # AI 判断（执行日 = t 的视图；ai_view_{t}.json）
        ai_doc = _load(CACHE / f"ai_view_{t}.json", {}) or {}
        ai_views = {} if ai_doc.get("debug") else (ai_doc.get("views") or {})

        for pool in POOLS:
            for c in cand.get(pool) or []:
                code = str(c.get("code") or "")
                if not code:
                    continue
                codes.add(code)
                rows.append(_mk_row(t, rec, code, c.get("name") or "", pool, None, c,
                                    quotes.get(code) or {}, zt_map, nxt))
        # AI 档：只对候选池内出现的票（保证池字段可得）
        pool_of = {str(c.get("code")): pool for pool in POOLS for c in (cand.get(pool) or [])}
        cand_of = {str(c.get("code")): c for pool in POOLS for c in (cand.get(pool) or [])}
        for code, v in ai_views.items():
            code = str(code)
            c = cand_of.get(code)
            if c is None:
                continue                                  # 非本批候选 → 不入样本（防口径混入）
            codes.add(code)
            v = v if isinstance(v, dict) else {}
            rows.append(_mk_row(t, rec, code, c.get("name") or v.get("name") or "",
                                pool_of.get(code), str(v.get("view") or ""), c,
                                quotes.get(code) or {}, zt_map, nxt,
                                note=str(v.get("reason") or "")[:120]))
    return rows, sorted(codes)


def _prev(days: list[str], d: str) -> str | None:
    try:
        i = days.index(d)
    except ValueError:
        return None
    return days[i - 1] if i > 0 else None


def _mk_row(t: str, rec: str, code: str, name: str, pool: str | None, ai_view: str | None,
            cand: dict, q: dict, zt_map: dict, nxt: str | None, note: str = "") -> dict:
    ap = _num(q.get("auction_price"))
    pc = _num(q.get("prev_close"))
    is20 = bool(cand.get("is_20cm"))
    limit = 20.0 if is20 else 10.0
    gap = round((ap - pc) / pc * 100, 2) if (ap and pc) else None
    zt_t = code in (zt_map.get(t) or {})
    zt_n = code in (zt_map.get(nxt) or {}) if nxt else None
    row = {
        "trade_date": t, "rec_date": rec, "code": code, "name": name,
        "pool": pool, "ai_view": ai_view, "note": note,
        "boards": cand.get("boards"), "is_20cm": is20,
        "auction_price": ap, "prev_close": pc, "gap_pct": gap,
        "gone_limit_open": bool(gap is not None and gap >= limit - 0.1),   # 开盘即涨停（结构性买不到）
        # ⛔ 可买 = 竞价涨幅 ≤ 用户硬线。一字板（≥ 涨停线−0.1）必然 > 硬线，
        #    故此处**不需要**重复判涨停（2026-09-17 变异验证发现该 clause 为死逻辑已删）；
        #    该蕴含关系由 tests/test_decision_audit.py::test_limit_open_implies_unbuyable 锁死。
        "buyable": bool(gap is not None and gap <= ENTRY_GAP_MAX),
        "zt_on_t": zt_t, "zt_on_next": zt_n,
    }
    return row


# ────────────────────────── 统计 ──────────────────────────
def _group(rows: list[dict], key: str) -> dict[str, dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        k = r.get(key)
        if k:
            buckets[str(k)].append(r)
    return {k: _stats(v) for k, v in buckets.items()}


def _stats(rs: list[dict]) -> dict:
    n = len(rs)
    buyable = [r for r in rs if r.get("buyable")]
    execable = [r for r in buyable if r.get("ret_pct") is not None]
    rets = [r["ret_pct"] for r in execable]
    all_rets = [r["ret_pct"] for r in rs if r.get("ret_pct") is not None]
    zt = [r for r in rs if r.get("zt_on_t")]
    ztn = [r for r in rs if r.get("zt_on_next")]
    ztn_base = [r for r in rs if r.get("zt_on_next") is not None]
    return {
        "n": n,
        "exec_n": len(buyable),
        "exec_rate": round(100 * len(buyable) / n, 1) if n else None,   # 可执行率与收盘价可得性无关
        "ret_exec": _avg(rets),                       # 可执行样本的实际收益（同 nav 口径）
        "ret_all": _avg(all_rets),                    # 全样本覆盖收益（含被硬线挡掉的）
        "win_rate": round(100 * sum(1 for x in rets if x > 0) / len(rets), 1) if rets else None,
        "zt_rate": round(100 * len(zt) / n, 1) if n else None,
        "zt_next_rate": round(100 * len(ztn) / len(ztn_base), 1) if ztn_base else None,
        "small_sample": n < LOW_SAMPLE,
    }


def _gate_cost(rows: list[dict]) -> dict:
    """门槛成本：按竞价涨幅分档看结果。正 = 门槛挡掉了收益（代价）。"""
    b = {"可买（≤硬线）": [], "超硬线（>硬线）": [], "一字板（开盘涨停）": []}
    for r in rows:
        if r.get("gap_pct") is None:
            continue
        key = ("一字板（开盘涨停）" if r.get("gone_limit_open")
               else "可买（≤硬线）" if r.get("buyable") else "超硬线（>硬线）")
        b[key].append(r)
    return {k: _stats(v) for k, v in b.items()}


def _drift(rows: list[dict]) -> list[dict]:
    """判断漂移：同一 code 在连续执行日的 AI 档位变化（participate↔watch 摆动）。"""
    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("ai_view"):
            by_code[r["code"]].append(r)
    out = []
    for code, rs in by_code.items():
        rs.sort(key=lambda x: x["trade_date"])
        seq = [(r["trade_date"], r["ai_view"]) for r in rs]
        switches = sum(1 for a, b in zip(seq, seq[1:]) if a[1] != b[1])
        if switches >= 1:
            out.append({"code": code, "name": rs[0]["name"], "n_days": len(seq),
                        "switches": switches, "seq": [f"{d[5:]}·{v}" for d, v in seq]})
    return sorted(out, key=lambda x: (-x["switches"], -x["n_days"]))[:10]


def _missed(raw: list[dict], cap: int = 20) -> list[dict]:
    """被门槛挡掉、但当日涨停的标的（同一 执行日+标的 去重·池档与AI档只留一条）。"""
    seen: dict[tuple, dict] = {}
    for r in raw:
        if not (r.get("zt_on_t") and not r.get("buyable")):
            continue
        k = (r["trade_date"], r["code"])
        if k in seen:
            continue
        seen[k] = {"trade_date": r["trade_date"], "code": r["code"], "name": r["name"],
                   "gap_pct": r["gap_pct"], "zt_on_t": r["zt_on_t"], "ret_pct": r.get("ret_pct")}
    return sorted(seen.values(), key=lambda x: x["trade_date"], reverse=True)[:cap]


def _ai_matrix(rows: list[dict]) -> dict:
    """AI 三档 × 结果（含「档位单调性」判定）。"""
    m = _group([r for r in rows if r.get("ai_view")], "ai_view")
    order = [k for k in AI_TIERS if k in m]
    zt = [(k, m[k]["zt_rate"]) for k in order if m[k]["zt_rate"] is not None]
    mono = all(zt[i][1] >= zt[i + 1][1] for i in range(len(zt) - 1)) if len(zt) >= 2 else None
    return {"by_tier": m, "monotonic": mono,
            "mono_note": ("档位单调有效（参与 > 观察 > 放弃）" if mono else
                          "档位非单调 → 判断区分度存疑（但样本可能不足）" if mono is not None else "样本不足")}


# ────────────────────────── 输出 ──────────────────────────
def _fmt(v: Any, unit: str = "", na: str = "—") -> str:
    if v is None:
        return na
    return f"{v}{unit}" if isinstance(v, str) else f"{v:g}{unit}"


def render_md(res: dict) -> str:
    L: list[str] = []
    w = L.append
    w(f"# 决策质量审计（反事实账本）· {res['generated_at'][:10]}")
    w("")
    w(f"- 覆盖：**{res['window']['days']}** 个执行日（{res['window']['from']} → {res['window']['to']}）"
      f"｜样本 **{res['window']['rows']}** 条｜去重标的 **{res['window']['codes']}** 只")
    w(f"- 入场硬线：竞价涨幅 ≤ **{res['entry_gap_max']:g}%**（同 `ai_caliber.USER_ENTRY_MAX_GAP_PCT`）"
      f"｜收益口径：竞价成交价 → 当日收盘（同 `ai_ledger`）")
    w(f"- 样本 < {LOW_SAMPLE} 的分组标 ⚠️**样本不足**，只呈现不裁决")
    if res.get("close_failed"):
        w(f"- ⚠️ 收盘价缺失 {len(res['close_failed'])} 只（网络/停牌）→ 该部分仅出涨停口径")
    w("")

    w("## A) 池档口径（候选池 × 实际结果）")
    w("")
    w("| 池档 | 样本 | 可执行率 | 可执行收益均值 | 全样本收益均值 | 胜率 | 当日涨停率 | 次日涨停率 |")
    w("|---|---|---|---|---|---|---|---|")
    for k in POOLS:
        s = (res["pool_matrix"].get(k) or {})
        if not s:
            continue
        w(f"| {k}{' ⚠️' if s.get('small_sample') else ''} | {s['n']} | {_fmt(s['exec_rate'],'%')} | "
          f"{_fmt(s['ret_exec'],'%')} | {_fmt(s['ret_all'],'%')} | {_fmt(s['win_rate'],'%')} | "
          f"{_fmt(s['zt_rate'],'%')} | {_fmt(s['zt_next_rate'],'%')} |")
    w("")

    w("## B) AI 三档口径（判断有没有区分度）")
    w("")
    w("| AI 判断 | 样本 | 可执行率 | 可执行收益均值 | 胜率 | 当日涨停率 | 次日涨停率 |")
    w("|---|---|---|---|---|---|---|")
    for k in AI_TIERS:
        s = (res["ai_matrix"]["by_tier"].get(k) or {})
        if not s:
            continue
        w(f"| {k}{' ⚠️' if s.get('small_sample') else ''} | {s['n']} | {_fmt(s['exec_rate'],'%')} | "
          f"{_fmt(s['ret_exec'],'%')} | {_fmt(s['win_rate'],'%')} | {_fmt(s['zt_rate'],'%')} | "
          f"{_fmt(s['zt_next_rate'],'%')} |")
    w("")
    w(f"**判定**：{res['ai_matrix']['mono_note']}")
    w("")

    w("## C) 门槛成本（5% 硬线在保护还是在挡钱）")
    w("")
    w("| 分档 | 样本 | 可执行收益均值 | 全样本收益均值 | 胜率 | 当日涨停率 |")
    w("|---|---|---|---|---|---|")
    for k, s in res["gate"].items():
        if not s:
            continue
        w(f"| {k}{' ⚠️' if s.get('small_sample') else ''} | {s['n']} | {_fmt(s['ret_exec'],'%')} | "
          f"{_fmt(s['ret_all'],'%')} | {_fmt(s['win_rate'],'%')} | {_fmt(s['zt_rate'],'%')} |")
    w("")

    if res["drift"]:
        w("## D) 判断漂移（同票 AI 档位摆动 Top）")
        w("")
        w("| 标的 | 出现天数 | 档位变化次数 | 序列 |")
        w("|---|---|---|---|")
        for d in res["drift"]:
            w(f"| {d['name']}({d['code']}) | {d['n_days']} | {d['switches']} | {' → '.join(d['seq'])} |")
        w("")

    w("## E) 被挡掉的可交易机会（超硬线 / 一字板，当日涨停）")
    w("")
    lst = res.get("missed") or []
    if lst:
        w("| 执行日 | 标的 | 竞价涨幅 | 当日涨停 | 可执行收益 |")
        w("|---|---|---|---|---|")
        for r in lst[:20]:
            w(f"| {r['trade_date'][5:]} | {r['name']}({r['code']}) | {_fmt(r['gap_pct'],'%')} | "
              f"{'是' if r['zt_on_t'] else '否'} | {_fmt(r.get('ret_pct'),'%')} |")
    else:
        w("（窗口内无）")
    w("")
    w("---")
    w("> 本审计**只读**：不改阈值、不写净值账本、不推送、不动自动化。")
    w("> 归因请在样本 ≥ 30/分组时再下结论；当前分组标注已给出是否满足。")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="决策质量审计（反事实账本）")
    ap.add_argument("--days", type=int, default=30, help="回看交易日数（默认 30）")
    ap.add_argument("--no-fetch", action="store_true", help="不联网，只用已缓存的收盘价")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--out", default=None, help="额外写出 JSON 到指定路径")
    args = ap.parse_args()

    days_all = trading_days()
    if not days_all:
        print("[decision_audit] 无可用交易日（cache/zt_history 为空）", file=sys.stderr)
        return 1
    days = days_all[-args.days:]

    raw, codes = build_rows(days, {})
    if not raw:
        print("[decision_audit] 窗口内无可审计样本（缺 candidates/auction_quotes）", file=sys.stderr)
        return 1

    cache = load_close_cache()
    cache, failed = fetch_closes(codes, cache, allow_fetch=not args.no_fetch)
    if not args.no_fetch:
        save_close_cache(cache)

    for r in raw:
        px = close_of(cache, r["code"], r["trade_date"])
        ap_ = r.get("auction_price")
        r["close"] = px
        r["ret_pct"] = round((px - ap_) / ap_ * 100, 2) if (px and ap_) else None

    used = [d for d in days if any(x["trade_date"] == d for x in raw)]
    res = {
        "schema": "decision_audit_v1",
        "generated_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        "entry_gap_max": ENTRY_GAP_MAX,
        "window": {"days": len(used), "from": used[0] if used else None,
                   "to": used[-1] if used else None, "rows": len(raw), "codes": len(codes)},
        "close_failed": failed,
        "pool_matrix": _group(raw, "pool"),
        "ai_matrix": _ai_matrix(raw),
        "gate": _gate_cost(raw),
        "drift": _drift(raw),
        "missed": _missed(raw),
    }
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_MD.write_text(render_md(res), encoding="utf-8")
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    print(render_md(res))
    print(f"[decision_audit] 已写 {OUT_JSON} / {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
