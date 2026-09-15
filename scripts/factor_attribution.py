#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""因子归因（N3 · 2026-09-11 用户拍板「做一批 N1+N2+N3」）

用途：用**已有的**真实交易记录回答一个问题——「亏的那些单，入场时哪个因子是红的？」

数据源（全部为已有文件·零新增抓取）：
  - wealth_code/cache/account_nav_history.jsonl   真实逐票收益（净值账本）
  - wealth_code/cache/candidates-<date>.json      入场日的候选快照（71 字段/票）

口径：
  - 以 recommendation_date 对齐：candidates-<recommendation_date>.json 提供「入场日因子」，
    账本该行的 stocks[].return_pct 提供「次日实现收益」。
  - 胜单 = return_pct > 0；败单 = return_pct < 0；=0 归入中性（不参与对比）。
  - 每个数值因子输出：胜单均值 / 败单均值 / 差值(胜-败) / 覆盖数。
    ⚠️ 差值方向解读：正 = 该因子高时更容易赚；负 = 该因子高时更容易亏（拖后腿）。

⛔ 诚实边界：样本极小（约 2 周 / 数十只），本脚本输出的是**观察**不是**统计结论**，
   不得据此直接改阈值（宪法：数据真实 + 不得用小样本过拟合）。仅供人工看方向。

用法：
  python3 scripts/factor_attribution.py            # 全量
  python3 scripts/factor_attribution.py --json     # 机器可读
  python3 scripts/factor_attribution.py --min-cov 0.6
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "wealth_code" / "cache"
NAV = CACHE / "account_nav_history.jsonl"

# 参与归因的数值因子（字段名 → 展示名）。缺字段自动跳过。
FACTORS: list[tuple[str, str]] = [
    ("score", "综合分"),
    ("raw_score", "原始分"),
    ("boards", "连板数"),
    ("ign_score", "点火分"),
    ("exp_score", "爆发分"),
    ("lead_score", "龙头分"),
    ("cap_score", "容量分"),
    ("themeTier", "题材档位"),
    ("cje_yi", "成交额(亿)"),
    ("zsz_yi", "总市值(亿)"),
    ("hs", "换手率(%)"),
    ("zbc", "炸板次数"),
    ("ladder_health", "梯队健康度"),
    ("sector_mates_cnt", "同板块涨停家数"),
    ("theme_mates_cnt", "同题材家数"),
    ("history_relay_prob", "历史晋级概率"),
    ("history_relay_samples", "历史样本数"),
    ("chip_profit_rate", "筹码获利比例(%)"),
    ("chip_conc90", "筹码集中度90"),
    ("main_flow_yi", "当日主力净流入(亿)"),
    ("main_flow_5d_yi", "5日主力净流入(亿)"),
    ("main_flow_10d_yi", "10日主力净流入(亿)"),
    ("pct", "入场日涨幅(%)"),
]

BOOL_FACTORS: list[tuple[str, str]] = [
    ("is_capacity_core", "容量核心"),
    ("is_sector_dragon", "板块龙头"),
    ("is_20cm", "创业板20cm"),
    ("ladder_has_complete", "梯队完整"),
    ("theme_is_lone", "题材孤军"),
    ("sector_is_lone", "板块孤军"),
]


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_pairs() -> tuple[list[dict], list[str]]:
    """→ (样本列表, 告警列表)。每样本 = {date, code, name, ret, bucket, factor...}"""
    warn: list[str] = []
    if not NAV.exists():
        return [], [f"缺净值账本: {NAV}"]
    rows = []
    for line in NAV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            warn.append("净值账本存在无法解析的行，已跳过")

    samples: list[dict] = []
    for r in rows:
        rec_date = r.get("recommendation_date")
        if not rec_date:
            continue
        cand_p = CACHE / f"candidates-{rec_date}.json"
        if not cand_p.exists():
            warn.append(f"{rec_date}: 缺候选快照 candidates-{rec_date}.json → 该日无法归因")
            continue
        cand = json.loads(cand_p.read_text(encoding="utf-8"))
        pool: dict[str, dict] = {}
        for bucket in ("relay", "watch", "early_ambush"):
            for it in (cand.get(bucket) or []):
                c = str(it.get("code") or "").strip()
                if c:
                    pool[c] = {**it, "_bucket": bucket}
        for s in (r.get("stocks") or []):
            c = str(s.get("code") or "").strip()
            ret = _num(s.get("return_pct"))
            if not c or ret is None:
                continue
            if c not in pool:
                warn.append(f"{rec_date} {c}: 账本有、候选快照无（无法归因）")
                continue
            samples.append({"date": rec_date, "code": c, "name": s.get("name") or pool[c].get("name"),
                            "ret": ret, "_bucket": pool[c].get("_bucket"), **pool[c]})
    return samples, warn


def nav_curve(subset: list[dict], date_map: dict[str, str]) -> list[tuple]:
    """按 trade_date 等权复利 → [(trade_date, n, avg_ret, nav)]。"""
    by: dict[str, list[float]] = {}
    for s in subset:
        td = date_map.get(s["date"])
        if td:
            by.setdefault(td, []).append(s["ret"])
    nav, seq = 1.0, []
    for d in sorted(by):
        r = sum(by[d]) / len(by[d])
        nav *= (1 + r / 100)
        seq.append((d, len(by[d]), round(r, 2), round(nav, 4)))
    return seq


def load_date_map() -> dict[str, str]:
    """recommendation_date → trade_date（来自净值账本）。"""
    out: dict[str, str] = {}
    if not NAV.exists():
        return out
    for line in NAV.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("recommendation_date"):
            out[r["recommendation_date"]] = r.get("trade_date") or ""
    return out


def attribute(samples: list[dict], min_cov: float = 0.5) -> dict:
    win = [s for s in samples if s["ret"] > 0]
    lose = [s for s in samples if s["ret"] < 0]
    flat = [s for s in samples if s["ret"] == 0]

    out = {"n_all": len(samples), "n_win": len(win), "n_lose": len(lose), "n_flat": len(flat),
           "win_rate_pct": round(len(win) / len(samples) * 100, 1) if samples else None,
           "avg_win": round(sum(s["ret"] for s in win) / len(win), 2) if win else None,
           "avg_lose": round(sum(s["ret"] for s in lose) / len(lose), 2) if lose else None,
           "numeric": [], "boolean": [], "by_bucket": {}}

    for key, label in FACTORS:
        wv = [v for s in win if (v := _num(s.get(key))) is not None]
        lv = [v for s in lose if (v := _num(s.get(key))) is not None]
        cov = (len(wv) + len(lv)) / len(samples) if samples else 0
        if not wv or not lv or cov < min_cov:
            continue
        mw, ml = sum(wv) / len(wv), sum(lv) / len(lv)
        out["numeric"].append({
            "key": key, "label": label, "win_mean": round(mw, 3), "lose_mean": round(ml, 3),
            "diff": round(mw - ml, 3), "n_win": len(wv), "n_lose": len(lv),
        })
    out["numeric"].sort(key=lambda d: -abs(d["diff"]))

    for key, label in BOOL_FACTORS:
        wv = [bool(s.get(key)) for s in win if s.get(key) is not None]
        lv = [bool(s.get(key)) for s in lose if s.get(key) is not None]
        if not wv or not lv:
            continue
        rw, rl = sum(wv) / len(wv) * 100, sum(lv) / len(lv) * 100
        out["boolean"].append({"key": key, "label": label, "win_pct": round(rw, 1),
                               "lose_pct": round(rl, 1), "diff_pp": round(rw - rl, 1),
                               "n_win": len(wv), "n_lose": len(lv)})
    out["boolean"].sort(key=lambda d: -abs(d["diff_pp"]))

    for b in ("relay", "watch", "early_ambush"):
        sub = [s for s in samples if s.get("_bucket") == b]
        if sub:
            w = [s for s in sub if s["ret"] > 0]
            out["by_bucket"][b] = {
                "n": len(sub), "win_rate_pct": round(len(w) / len(sub) * 100, 1),
                "avg_ret": round(sum(s["ret"] for s in sub) / len(sub), 2),
            }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--min-cov", type=float, default=0.5, help="因子最小覆盖率")
    ap.add_argument("--show-samples", action="store_true", help="列出逐票明细")
    a = ap.parse_args()

    samples, warn = load_pairs()
    if not samples:
        print("无可归因样本（候选快照与净值账本未对齐）", file=sys.stderr)
        for w in warn[:5]:
            print("  ⚠️", w, file=sys.stderr)
        return 1

    res = attribute(samples, a.min_cov)
    dmap = load_date_map()
    pools = {
        "全池(现状口径 relay+watch)": samples,
        "只做接力池 relay": [s for s in samples if s.get("_bucket") == "relay"],
        "只做观察池 watch": [s for s in samples if s.get("_bucket") == "watch"],
    }
    navs = {k: nav_curve(v, dmap) for k, v in pools.items()}

    if a.json:
        print(json.dumps({**res, "nav_curves": navs, "warnings": warn}, ensure_ascii=False, indent=1))
        return 0

    print(f"样本 {res['n_all']} 笔 | 胜 {res['n_win']} · 败 {res['n_lose']} · 平 {res['n_flat']}"
          f" | 胜率 {res['win_rate_pct']}%")
    print(f"平均盈利 {res['avg_win']}% | 平均亏损 {res['avg_lose']}%")
    if res["by_bucket"]:
        print("\n=== 分池 ===")
        for b, d in res["by_bucket"].items():
            print(f"  {b:<14} n={d['n']:<3} 胜率{d['win_rate_pct']:>5}%  平均{d['avg_ret']:>6}%")

    print("\n=== 数值因子（按区分度排序·差值=胜单均值−败单均值）===")
    print(f"{'因子':<16}{'胜单':>9}{'败单':>9}{'差值':>9}   样本")
    for d in res["numeric"]:
        flag = "  ← 反向" if d["diff"] < 0 else ""
        print(f"{d['label']:<16}{d['win_mean']:>9}{d['lose_mean']:>9}{d['diff']:>+9}{flag}   {d['n_win']}/{d['n_lose']}")

    print("\n=== 布尔因子（胜单/败单中出现比例）===")
    for d in res["boolean"]:
        print(f"{d['label']:<14} 胜{d['win_pct']:>5}%  败{d['lose_pct']:>5}%  差{d['diff_pp']:>+6}pp")

    print("\n=== 分池净值对照（等权复利·非官方账本）===")
    for k, seq in navs.items():
        if not seq:
            print(f"  {k}: 无样本")
            continue
        print(f"  {k:<24} 期末 {seq[-1][3]:.4f} ({(seq[-1][3]-1)*100:+.2f}%) · {len(seq)} 个交易日")
    print("  ⚠️ 本区为**重建**（覆盖账本 12/14 天·用 1.0 起算），与官方 nav 不同源同法，只用于**池间相对比较**。")

    if a.show_samples:
        print("\n=== 逐票明细 ===")
        for s in sorted(samples, key=lambda x: x["ret"]):
            print(f"  {s['date']} {s['code']} {str(s['name'])[:6]:<7}{s['ret']:>+7}%  [{s.get('_bucket')}]")

    if warn:
        print(f"\n⚠️ 告警 {len(warn)} 条（前 8）:")
        for w in warn[:8]:
            print("  -", w)
    print("\n⛔ 样本极小（约 2 周），以上为观察非统计结论；不得据此直接调阈值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
