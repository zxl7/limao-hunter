#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回流确认事件研究（只读 · 与 decision_audit / reflow_backtest 双基准口径同源）

====================================================================
回测对象 = 系统的「盲区」信号
--------------------------------------------------------------------
既有 `reflow_backtest.py` 只回测「🔔 回流前兆」（题材冷却中、预判即将回流）。
但系统对「冷却后**已经**重新爆发」的题材（回流确认）完全没有识别——这也是
2026-09-30 医药回流没被任何卡片预警的根因。本脚本就测这一类信号：

    题材 S 在 D 日重新爆发（涨停数 ≥ BOOM_ZT），且此前有过爆发日 D0，
    D0 与 D 之间经历了一段冷却（涨停数 ≤1 的交易日 ≥ COOLDOWN_MIN），
    D0 与 D 间隔 ≥ SEP_MIN 个交易日，且中间没有再次爆发。

→ 在 D 日收盘判定信号，D+1 开盘买入该题材**全部涨停票**（等权），
  持有 N 个交易日（N=3 主口径，对齐用户「3 天无改善离场」纪律；附 N=1），
  D+N 收盘卖出。

回答的问题：「今天医药这种回流，是有迹可循、可提前埋伏，还是纯随机？」
→ 用历史样本测：冷却后重新爆发的题材，其涨停票次日买入后 N 日平均收益，
  是否显著跑赢「同日随便买一只涨停票」（base_zt 基准）。无超额即该信号无信息量。

====================================================================
口径（单一源 · 与既有系统同源 · ⛔ 禁另造）
--------------------------------------------------------------------
  · 涨停权威 = cache/zt_history/zt_<date>.json 的 zt_list
              （intraday_only 盘中壳除外，无完整涨停池）
  · 日K      = decision_audit 共享缓存层（audit_close_cache.json）+ 腾讯 fqkline
  · 交易日历 = zt_history 实际归档日（已核对：本窗口每个交易日都有归档，
              缺失的 09-12/09-13/09-19/09/20 均为周末，非交易日）
  · 执行价   = 回流日 D 收盘判定 → D+1 开盘买入 → D+N 收盘卖出（T+1·防 look-ahead）
  · 缺失     = None（⛔ 不填 0、不借相邻日、不用前收外推）
  · 剔除     = 科创板(688)/北交所(8,4)/B股(9) + ST（无权限 + 用户铁律不看 ST）

====================================================================
产物（事件研究 · 不调 export_results）
--------------------------------------------------------------------
  cache/reflow_confirm_backtest/reflow_confirm_trades.csv
  cache/reflow_confirm_backtest/reflow_confirm_summary.json
  cache/reflow_confirm_backtest/reflow_confirm_equity.csv  （虚拟累计事件PnL曲线）
  cache/reflow_confirm_backtest/index.html                （仪表盘）

用法：
  python3 scripts/reflow_confirm_backtest.py            # 联网补日K缺口
  python3 scripts/reflow_confirm_backtest.py --no-fetch # 离线（只用已缓存日K）
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE / "common"))

from decision_audit import (  # noqa: E402
    load_close_cache, save_close_cache, fetch_bars, kline_calendar, nth_next,
    close_of, open_of,
)

CACHE = BASE / "cache"
ZT_HIST = CACHE / "zt_history"
OUTDIR = CACHE / "reflow_confirm_backtest"
REF_PLUGIN = Path(
    "/Users/zxl/.workbuddy/plugins/marketplaces/experts/plugins/strategy-backtest-expert"
    "/skills/quant-backtest-lab/reference"
)

BOOM_ZT = 3          # 爆发阈值（与 theme_rotation 同源）
COOLDOWN_MIN = 2     # D0 与 D 之间「涨停数 ≤1」的交易日下限
SEP_MIN = 3          # D0 与 D 至少间隔交易日
HOLDS = [1, 3]       # 主口径 N=3（用户纪律），附 N=1
PRIMARY = 3
LOW_SAMPLE = 20      # 样本不足门槛
EXCLUDE_PREFIX = ("688", "8", "4", "9")  # 科创板/北交所/B股（无权限+非主板创业板）


def is_excluded(code: str, name: str | None) -> bool:
    if code.startswith(EXCLUDE_PREFIX):
        return True
    n = (name or "").upper()
    if "ST" in n or n.startswith("*"):
        return True
    return False


def load_zt() -> dict[str, list[dict]]:
    """date -> [{code,name,sector}]；剔除盘中壳与缺字段行。"""
    by_date: dict[str, list[dict]] = {}
    for f in sorted(ZT_HIST.glob("zt_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("intraday_only"):
            continue
        date = d.get("date")
        if not date:
            continue
        rows = []
        for z in (d.get("zt_list") or []):
            code = z.get("code")
            name = z.get("name")
            sector = z.get("sector")
            if not code or not sector:
                continue
            rows.append({"code": code, "name": name, "sector": sector})
        by_date[date] = rows
    return by_date


def build_sector(dates, by_date):
    sec_count: dict[tuple[str, str], int] = {}
    sec_codes: dict[tuple[str, str], list[str]] = {}
    for date in dates:
        cnt: dict[str, int] = {}
        codes: dict[str, list[str]] = {}
        for r in by_date[date]:
            s = r["sector"]
            cnt[s] = cnt.get(s, 0) + 1
            codes.setdefault(s, []).append(r["code"])
        for s, n in cnt.items():
            sec_count[(s, date)] = n
            sec_codes[(s, date)] = codes[s]
    return sec_count, sec_codes


def detect_events(dates, by_date, sec_count, sec_codes) -> list[dict]:
    """检出所有「回流确认」事件（结构判定；冷却日已被完整观测见下面说明）。"""
    events: list[dict] = []
    secs = sorted({s for (s, _) in sec_count})
    for s in secs:
        dlist = [d for d in dates if (s, d) in sec_count]
        if len(dlist) < 2:
            continue
        booms = [d for d in dlist if sec_count[(s, d)] >= BOOM_ZT]
        for D in booms:
            prior = [d0 for d0 in booms if d0 < D]
            if not prior:
                continue  # 首次爆发，无法确认是否为「回流」
            D0 = max(prior)
            gap = dates.index(D) - dates.index(D0)
            if gap < SEP_MIN:
                continue
            between = [d for d in dlist if D0 < d < D]
            cooled = [d for d in between if sec_count[(s, d)] <= 1]
            if len(cooled) < COOLDOWN_MIN:
                continue
            if any(sec_count[(s, d)] >= BOOM_ZT for d in between):
                continue  # 中间又爆过 → 连续主升，不是回流
            # 冷却完整性：D0~D 之间每个交易日必须已归档（本窗口全部交易日都有归档）
            mkt_between = [d for d in dates if D0 < d < D]
            if any(d not in by_date for d in mkt_between):
                continue  # 有未观测交易日 → 证据不足，剔除
            events.append({
                "sector": s,
                "reflow_date": D,
                "prior_boom": D0,
                "cooled_days": len(cooled),
                "gap_days": gap,
                "boom_n": sec_count[(s, D)],
                "codes": sec_codes[(s, D)],
            })
    events.sort(key=lambda e: e["reflow_date"])
    return events


def horizon_return(cache, cal, code, D, H):
    """D+1 开盘买入，D+H 收盘卖出 → 收益率%；缺价返回 None。"""
    d1 = nth_next(cal, D, 1)
    dH = nth_next(cal, D, H)
    if not d1 or not dH:
        return None
    o = open_of(cache, code, d1)
    c = close_of(cache, code, dH)
    if o is None or c is None or o == 0:
        return None
    return round((c / o - 1) * 100, 2)


def metrics(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "median": round(statistics.median(vals), 2),
        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
        "best": round(max(vals), 2),
        "worst": round(min(vals), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="离线（只用已缓存日K）")
    args = ap.parse_args()

    by_date = load_zt()
    dates = sorted(by_date.keys())
    sec_count, sec_codes = build_sector(dates, by_date)
    events = detect_events(dates, by_date, sec_count, sec_codes)

    print(f"[回流确认] 归档交易日 {len(dates)} 个；检出回流确认事件 {len(events)} 个")
    for e in events:
        print(f"  · {e['reflow_date']} {e['sector']:>8} | 前次爆发 {e['prior_boom']} "
              f"| 冷却 {e['cooled_days']}日 | 今日涨停 {e['boom_n']}只")

    # 收集所有需要补日K的代码与日期
    cache = load_close_cache()
    cal = kline_calendar(cache)
    need_codes: set[str] = set()
    need_dates: set[str] = set()
    # base_zt：每个事件日 D 的全部涨停票（跨题材）
    base_codes_by_date: dict[str, set[str]] = {}
    for e in events:
        D = e["reflow_date"]
        for H in HOLDS:
            d1 = nth_next(cal, D, 1)
            dH = nth_next(cal, D, H)
            for d in (d1, dH):
                if d:
                    need_dates.add(d)
        for c in e["codes"]:
            if not is_excluded(c, None):
                need_codes.add(c)
        # base_zt 池
        pool = set()
        for r in by_date.get(D, []):
            if not is_excluded(r["code"], r["name"]):
                pool.add(r["code"])
                need_codes.add(r["code"])
        base_codes_by_date[D] = pool

    if not args.no_fetch and need_codes:
        cache, failed = fetch_bars(sorted(need_codes), cache, True, need_dates=need_dates)
        save_close_cache(cache)
        if failed:
            print(f"[warn] 日K拉取失败 {len(failed)} 只：{failed[:10]}")
    cal = kline_calendar(cache)  # 刷新后重取日历

    # 计算每只票各周期收益
    run_today = datetime.now().strftime("%Y%m%d")
    trades: list[dict] = []
    excluded_recent = 0
    base_by_date: dict[str, list[float]] = {D: [] for D in base_codes_by_date}
    for e in events:
        D = e["reflow_date"]
        s = e["sector"]
        D1 = nth_next(cal, D, 1)
        if D1 is None or D1 >= run_today:
            excluded_recent += 1  # 入口落在今日或未来 → 前向窗口未闭合，不计入回测
            continue
        for c in e["codes"]:
            if is_excluded(c, None):
                continue
            name = next((r["name"] for r in by_date[D] if r["code"] == c), c)
            row = {
                "label": f"{s} 回流确认 {D}",
                "symbol": c,
                "symbol_name": name,
                "sector": s,
                "reflow_date": D,
                "entry_date": nth_next(cal, D, 1),
                "holding_bars": PRIMARY,
            }
            valid_primary = True
            for H in HOLDS:
                ret = horizon_return(cache, cal, c, D, H)
                row[f"ret_{H}d"] = ret
                if H == PRIMARY and ret is None:
                    valid_primary = False
            # 主口径（N=3）入场/出场价
            d1 = nth_next(cal, D, 1)
            d3 = nth_next(cal, D, PRIMARY)
            row["entry_price"] = open_of(cache, c, d1)
            row["exit_price"] = close_of(cache, c, d3)
            row["exit_date"] = d3
            row["pnl_pct"] = row.get(f"ret_{PRIMARY}d")
            # 该票是否主口径可交易（入场/出场价齐备）
            if valid_primary and row["entry_price"] and row["exit_price"]:
                trades.append(row)
            else:
                # 主口径缺价 → 仍保留但 pnl 留空（不计入主口径统计）
                row["pnl_pct"] = None
                trades.append(row)
        # base_zt 池各票主口径收益
        for c in base_codes_by_date[D]:
            r = horizon_return(cache, cal, c, D, PRIMARY)
            if r is not None:
                base_by_date[D].append(r)

    # 汇总
    reflow_primary = [t["pnl_pct"] for t in trades if t.get("pnl_pct") is not None]
    reflow_1d = [t.get("ret_1d") for t in trades if t.get("ret_1d") is not None]
    base_primary = [r for vals in base_by_date.values() for r in vals]
    base_1d = [horizon_return(cache, cal, c, D, 1)
               for D, cs in base_codes_by_date.items() for c in cs]
    base_1d = [r for r in base_1d if r is not None]

    m_reflow_p = metrics(reflow_primary)
    m_reflow_1 = metrics(reflow_1d)
    m_base_p = metrics(base_primary)
    m_base_1 = metrics(base_1d)

    excess_p = (round(m_reflow_p["mean"] - m_base_p["mean"], 2)
                if m_reflow_p and m_base_p else None)
    excess_1 = (round(m_reflow_1["mean"] - m_base_1["mean"], 2)
                if m_reflow_1 and m_base_1 else None)

    # 虚拟累计事件PnL曲线（主口径·按卖出日排序·等权累计）
    curve_rows = sorted(
        [t for t in trades if t.get("pnl_pct") is not None],
        key=lambda t: (t["exit_date"] or "9999"),
    )
    cum = 0.0
    equity = []
    for t in curve_rows:
        cum += t["pnl_pct"]
        equity.append({"date": t["exit_date"], "value": round(cum, 2)})

    # 写文件
    OUTDIR.mkdir(parents=True, exist_ok=True)
    # trades.csv
    tcols = ["label", "symbol", "symbol_name", "sector", "reflow_date",
             "entry_date", "exit_date", "holding_bars", "entry_price",
             "exit_price", "ret_1d", "ret_3d", "pnl_pct"]
    with open(OUTDIR / "reflow_confirm_trades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=tcols)
        w.writeheader()
        for t in trades:
            w.writerow({k: t.get(k) for k in tcols})
    # equity.csv
    with open(OUTDIR / "reflow_confirm_equity.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "value"])
        for p in equity:
            w.writerow([p["date"], p["value"]])
    # summary.json
    meta = {
        "strategy_name": "回流确认事件研究（冷却后重新爆发）",
        "market": "china_a",
        "language": "zh",
        "report_kind": "event_study",
        "start": events[0]["reflow_date"] if events else None,
        "end": events[-1]["reflow_date"] if events else None,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "boom_zt": BOOM_ZT, "cooldown_min": COOLDOWN_MIN, "sep_min": SEP_MIN,
        "holds": HOLDS, "primary_hold": PRIMARY, "low_sample": LOW_SAMPLE,
        "universe": "主板+创业板涨停票（剔除科创板/北交所/ST）",
        "execution": "回流日收盘判定 → 次日开盘买入 → 持有N日收盘卖出（T+1）",
        "data_note": "zt_history 仅覆盖 2026-08-31~09-29；样本为小样本方向性参考，非统计结论",
    }
    summary = {
        "n_events": len(events),
        "n_events_backtested": len(events) - excluded_recent,
        "n_events_recent_excluded": excluded_recent,
        "n_trades_primary": len(reflow_primary),
        "sample_adequacy": ("充足" if len(reflow_primary) >= LOW_SAMPLE
                            else "不足（仅呈现不裁决）"),
        "reflow_primary_3d": m_reflow_p,
        "reflow_1d": m_reflow_1,
        "base_zt_primary_3d": m_base_p,
        "base_zt_1d": m_base_1,
        "excess_3d_vs_base_zt": excess_p,
        "excess_1d_vs_base_zt": excess_1,
        "live_case": "2026-09-30 医药：09-21 爆发→09-22~09-29 冷却→09-30 重新爆发"
                     "（进行中·无前向收益·未计入统计，但形态与检出模式完全一致）",
    }
    payload = {"meta": meta, "summary": summary}
    (OUTDIR / "reflow_confirm_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 结果 ===")
    print(f"回流确认票（N=3 主口径）平均收益: {m_reflow_p}")
    print(f"base_zt 基准（同日任意涨停票 N=3）: {m_base_p}")
    print(f"超额(回流-基准): {excess_p}")
    print(f"样本充分性: {summary['sample_adequacy']}")

    # 渲染仪表盘
    render_dashboard(trades, equity, meta, summary, excess_p, excess_1,
                     m_reflow_p, m_base_p, m_reflow_1, m_base_1)


def render_dashboard(trades, equity, meta, summary, excess_p, excess_1,
                     m_reflow_p, m_base_p, m_reflow_1, m_base_1):
    # 复制渲染资源到 OUTDIR（自包含）
    for fn in ("render_dashboard.py", "dashboard_locales.py", "dashboard_template.html"):
        src = REF_PLUGIN / fn
        if src.exists():
            shutil.copy(src, OUTDIR / fn)
    sys.path.insert(0, str(OUTDIR))
    from render_dashboard import build_dashboard_data, render_dashboard as _rd

    conclusion = (
        f"● 结论：窗口内共检出 {summary['n_events']} 次「冷却后重新爆发」事件"
        f"（其中 {summary['n_events_recent_excluded']} 次落在最近两日、前向窗口未闭合，不计入统计）；"
        f"实际回测 {summary['n_events_backtested']} 次、{summary['n_trades_primary']} 只可交易涨停票（N=3 主口径）。\n"
        f"● 回流确认票 3 日平均收益 {m_reflow_p['mean'] if m_reflow_p else 'N/A'}%，"
        f"胜率 {m_reflow_p['win_rate'] if m_reflow_p else 'N/A'}%；"
        f"同日任意涨停票基准 3 日平均 {m_base_p['mean'] if m_base_p else 'N/A'}%。\n"
        f"● 超额 = {excess_p}%（正=回流票跑赢基准，负=无信息量）。\n"
        f"● 样本 {summary['sample_adequacy']}，本结果仅作方向性参考，不构成统计结论。"
    )
    assumptions = (
        "● 信号：题材涨停数 ≥3 为爆发；此前有过爆发、间隔≥3日、中间经历≥2个冷却日（涨停≤1）"
        "且未再爆发，方判定为「回流确认」。\n"
        "● 执行：回流日收盘判定，次日开盘买入该题材全部涨停票（等权），持有3日收盘卖出（T+1，防前视）。\n"
        "● 基准 base_zt：同一天全市场所有涨停票（同口径剔除）的次日买入3日收益均值，"
        "用于回答「回流票是否比随便买只涨停票更有价值」。\n"
        "● 剔除科创板/北交所/ST；缺失价格记为无效不参与统计。"
    )
    limitations = (
        "● 数据极薄：zt_history 仅覆盖 2026-08-31~09-29（约 3.5 周），样本远小于统计显著所需。\n"
        "● 事件研究非组合回测：无仓位/费率/滑点建模；每只票等权单位累计，非真实净值曲线。\n"
        "● 涨停票次日常高开甚至一字，实际可成交量受限；日K无法还原盘中顺序。\n"
        "● 回流确认本是系统盲区（theme_rotation 把「重新爆发」一律标 boom、被回流监测排除），"
        "故历史无系统信号日志，本回测从涨停归档**反推**该模式，非复现系统信号。"
    )
    optimization = (
        "● 若样本扩充后超额稳定为正：在 theme_rotation 增加「冷却后回流 boom」标记"
        "（is_reentry=True），并新增「回流确认卡」实时预警（当前系统缺这一环）。\n"
        "● 可加入资金面/板块强度过滤（避免弱势题材假回流），并分题材/分市场状态分层回测。\n"
        "● 延长数据窗口（接入更长涨停史或外部题材库）后再做显著性检验。"
    )

    extra_modules = [
        {"type": "text", "tab": "overview", "title": "结论", "text": conclusion},
        {"type": "text", "tab": "overview", "title": "关键假设与口径", "text": assumptions},
        {"type": "text", "tab": "overview", "title": "局限与偏差", "text": limitations},
        {"type": "text", "tab": "overview", "title": "优化方向", "text": optimization},
    ]

    report_data = build_dashboard_data(
        trade_history=trades,
        equity_curve=equity,
        summary=summary,
        meta=meta,
        language="zh",
        market="china_a",
        event_overview_mode="stats",
        extra_modules=extra_modules,
    )
    out = _rd(report_data, output_path=OUTDIR / "index.html")
    print(f"\n✅ 仪表盘已生成：{out}")


if __name__ == "__main__":
    main()
