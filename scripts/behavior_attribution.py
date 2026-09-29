#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行为归因 · 用你自己的交割单体检「三条闸门」（2026-09-23 用户交割单实证立项）

为什么要有这个脚本
==================
系统的 factor_attribution.py 读的是**系统账本**（wealth_code/cache/account_nav_history.jsonl，
2026-09 起、十余行）——它检验的是「**系统的规则跑得怎么样**」。

本脚本读的是**你自己的真实成交**（券商交割单）——它检验的是「**你有没有守住规则**」。
这是系统里此前完全缺失的「纠错层」：没有它，你不是在「基于当前推断」，
而是在**重复同一个错误且没有能力察觉**。

2026-09-23 用 4 年交割单实跑（2022-06-10 → 2026-04-30 · 2861 笔平仓 · 18553 条成交）：
  · 胜率 33.8% × 均盈 +4.20% − 败率 66.2% × 均亏 −4.32% = 单笔期望 **−1.445%** → 4 年 **−63.6 万**
  · 病根不是「选股」也不是「方向」：**卖后年化 +4.09%（≈大盘），是「持有」把它做成了 −97.6%**
  · 结论：赔率结构（盈亏比 0.97）＋ 持有周期（中位 2.4 天）＋ 仓位与信心绑定 → 三条闸门由此而来

三条闸门（权威 = config/shared_rules.md【🛡️ 交易闸门三条】）
=========================================================
  ① 赔率闸门  ：进场前写死「目标涨幅 / 止损幅度 ≥ 2」，< 2 直接不做
  ② 周期闸门  ：主线核心票按周/月持（卖点看**板块周期阶段**），⛔ 禁用固定百分比止盈
  ③ 仓位脱钩  ：单笔仓位只与**赔率**挂钩、与**信心**无关；单票 ≤50%；分批建仓

数据源
======
券商交割单，三张表（表名可含前后缀，按关键词匹配）：
  · 持仓数据  —— 期末快照（可选，仅用于对账）
  · 已清仓    —— 逐笔平仓（必需：清仓日期/代码/名称/总盈亏/盈亏比/[清仓距今]/持仓天数/交易费用/建仓日期）
  · 交易记录  —— 逐笔成交（必需：成交日期/交易类别/成交数量/成交金额/费用）
同时支持 `.xlsx` / `.xlsm` / `.csv`；也可直接传**目录**（目录内放 `已清仓.csv` +
`交易记录.csv` 即可分别认表）。

用法
====
  python3 scripts/behavior_attribution.py --path ~/Downloads/交割单.xlsx
  python3 scripts/behavior_attribution.py --path ~/Downloads/交割单目录/    # 目录形态
  python3 scripts/behavior_attribution.py --path x.xlsx --since 2024-01-01     # 只看近段
  python3 scripts/behavior_attribution.py --path x.xlsx --json                # 机器可读
  python3 scripts/behavior_attribution.py --path x.xlsx --persist             # 落盘 cache

⛔ 默认**只读不落盘**（--persist 才写 wealth_code/cache/behavior_attribution.json）。
⛔ 本脚本不写 holdings / 不动账本 / 不发推送——纯诊断。
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "wealth_code" / "cache"
OUT_DEFAULT = CACHE / "behavior_attribution.json"
NAV = CACHE / "account_nav_history.jsonl"

# ── 闸门阈值（宪法【🛡️ 交易闸门三条】· 改这里必须同步改宪法与 skill）──
GATE_PAYOFF_MIN = 2.0        # ① 赔率闸门：均盈 / |均亏| ≥ 2
GATE_HOLD_MEDIAN_MIN = 5.0   # ② 周期闸门：平仓中位持仓 ≥ 5 天（现状 2.4 天 = 「2.4 天机器」）
GATE_TOP25_LOSS_MAX = 0.40   # ③ 仓位脱钩：最大 25% 仓位的亏损占总亏损 ≤ 40%（现状 66%）
GATE_FEE_RATIO_MAX = 0.05    # 辅助·摩擦：交易费用 / |已实现亏损| ≤ 5%（现状 11%）
GATE_FLY_RATIO_MAX = 1.0     # 辅助·卖飞：卖后 >+50% 笔数 / 卖后 <−50% 笔数 ≤ 1（现状 2:1）
LONG_HOLD_DAYS = 21          # 长持档位（当时唯一赚钱档）
FLY_HI, FLY_LO = 50.0, -50.0 # 卖飞 / 卖对 的分界（%）

# 需要的列（按关键词匹配，容忍前后缀）
NEED_CLOSED = ["清仓日期", "代码", "名称", "总盈亏", "盈亏比", "持仓天数", "交易费用", "建仓日期"]
OPT_CLOSED = ["清仓距今", "同期大盘", "跑赢大盘"]
NEED_TRADE = ["成交日期", "交易类别", "成交金额", "发生金额", "费用"]
OPT_TRADE = ["成交时间", "代码", "名称", "成交数量", "成交价格"]
# 百分比列（Excel 里存的是小数，需 ×100 归一到「百分数」量纲）
RATIO_COLS = ["盈亏比", "同期大盘", "跑赢大盘", "清仓距今"]


# ══════════════════════════ 基础工具 ══════════════════════════

def _f(v) -> float | None:
    """任意值 → float（百分数文本带 % 则原样保留百分数量纲）"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if s in ("", "-", "--", "None", "nan", "—"):
        return None
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    try:
        x = float(s)
    except ValueError:
        return None
    return x


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def _q(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = int(len(sorted_vals) * p)
    idx = max(0, min(idx, len(sorted_vals) - 1))
    return sorted_vals[idx]


def _pct(x, digits=2):
    return "—" if x is None else f"{x:+.{digits}f}%"


def _num(x, digits=0):
    return "—" if x is None else f"{x:,.{digits}f}"


# ══════════════════════════ 读取交割单 ══════════════════════════

def _pick_col(header, keys):
    """表头 → 实际列名（关键词包含匹配，取最先命中的）"""
    got = {}
    for k in keys:
        for h in header:
            if h and k in h:
                got[k] = h
                break
    return got


def _load_sheet(ws):
    """工作表 → (表头, 行dict列表, 百分比列集合)"""
    it = ws.iter_rows()
    try:
        first = next(it)
    except StopIteration:
        return [], [], set()
    header = [_s(c.value) for c in first]
    # 同一表头重复出现时（券商表偶见），保留首次并去重
    seen = set()
    for i, h in enumerate(header):
        if h in seen:
            header[i] = ""
        elif h:
            seen.add(h)
    rows, pct = [], set()
    for row in it:
        d = {}
        empty = True
        for i, c in enumerate(row):
            h = header[i] if i < len(header) else ""
            if not h:
                continue
            v = c.value
            d[h] = v
            if v is not None and str(v).strip() != "":
                empty = False
                if "%" in (getattr(c, "number_format", "") or ""):
                    pct.add(h)
        if not empty:
            rows.append(d)
    return header, rows, pct


def _csv_payload(f: Path):
    with f.open(encoding="utf-8-sig", newline="") as fh:
        rd = list(csv.reader(fh))
    if not rd:
        return None
    header = [h.strip() for h in rd[0]]
    rows = [dict(zip(header, r)) for r in rd[1:] if any(str(x).strip() for x in r)]
    return header, rows, set()


def _sheets_from(path: Path) -> dict:
    """文件/目录 → {表名: (表头, 行, 百分比列集合)}"""
    def _wb(px: Path) -> dict:
        try:
            import openpyxl
        except ImportError:
            sys.exit("读 Excel 需要 openpyxl：pip install openpyxl（或改用 csv）")
        wb = openpyxl.load_workbook(px, read_only=True, data_only=True)
        out = {n: _load_sheet(wb[n]) for n in wb.sheetnames}
        wb.close()
        return out

    if path.is_dir():
        sheets = {}
        for f in sorted(path.iterdir()):
            suf = f.suffix.lower()
            if suf in (".xlsx", ".xlsm"):
                sheets.update(_wb(f))
            elif suf == ".csv":
                pl = _csv_payload(f)
                if pl:
                    sheets[f.stem] = pl
        return sheets
    suf = path.suffix.lower()
    if suf in (".xlsx", ".xlsm"):
        return _wb(path)
    if suf == ".csv":
        pl = _csv_payload(path)
        if not pl:
            sys.exit(f"空文件：{path}")
        return {path.stem: pl}
    if suf == ".xls":
        sys.exit("旧版 .xls 读不了，请另存为 .xlsx 或 .csv 再跑")
    sys.exit(f"不支持的格式：{suf}（支持 .xlsx/.xlsm/.csv，或含这些文件的目录）")


def load_broker(path: Path):
    """读交割单 → {"closed": [...], "trades": [...], "holdings": [...], "meta": {...}}"""
    sheets = _sheets_from(path)

    # 按关键词认表
    def find(*keywords):
        for name, payload in sheets.items():
            if any(k in name for k in keywords):
                return name, payload
        return None, ([], [], set())

    cname, (ch, crows, cpct) = find("已清仓", "清仓")
    tname, (th, trows, _) = find("交易记录", "成交", "对账单")
    hname, (hh, hrows, _) = find("持仓数据", "持仓")

    if not crows and not trows:
        sys.exit(f"没认出交割单表：{list(sheets)}（需含「已清仓」与「交易记录」）")

    # 需要的列是否齐
    cmap = _pick_col(ch, NEED_CLOSED + OPT_CLOSED)
    tmap = _pick_col(th, NEED_TRADE + OPT_TRADE)
    if crows:
        miss = [k for k in NEED_CLOSED if k not in cmap]
        if miss:
            print(f"⚠️ 已清仓表缺列 {miss}，相关指标将跳过", file=sys.stderr)
    if trows:
        miss = [k for k in NEED_TRADE if k not in tmap]
        if miss:
            print(f"⚠️ 交易记录表缺列 {miss}，相关指标将跳过", file=sys.stderr)

    # 百分比列归一：Excel「0.00%」存小数 → ×100
    for r in crows:
        for k in RATIO_COLS:
            col = cmap.get(k)
            if not col:
                continue
            v = _f(r.get(col))
            if v is None:
                r[col] = None
                continue
            # 该列若被识别为百分比格式 → 原始为小数，需 ×100
            if col in cpct and isinstance(r.get(col), (int, float)):
                r[col] = v * 100.0
            else:
                r[col] = v

    # csv 没有数字格式信息 → 用「量级」兜底：比率列绝对值全 ≤1.5 视为存的是小数
    for k in RATIO_COLS:
        col = cmap.get(k)
        if not col or col in cpct:
            continue
        vals = [abs(v) for r in crows if (v := _f(r.get(col))) is not None]
        if vals and max(vals) <= 1.5:
            for r in crows:
                v = _f(r.get(col))
                r[col] = v * 100.0 if v is not None else None

    return {
        "closed": crows,
        "trades": trows,
        "holdings": hrows,
        "cmap": cmap,
        "tmap": tmap,
        "meta": {"file": str(path), "sheets": list(sheets), "closed_sheet": cname, "trade_sheet": tname,
                 "holdings_sheet": hname},
    }


def _row_closed(r, cmap, since=None):
    """已清仓行 → 规范化 dict"""
    g = lambda k: r.get(cmap.get(k)) if cmap.get(k) else None
    d = _s(g("清仓日期"))[:10]
    if len(d) < 10 or d < "2000-01-01":
        return None
    if since and d < since:
        return None
    return {
        "close": d,
        "open": _s(g("建仓日期"))[:10],
        "code": _s(g("代码")),
        "name": _s(g("名称")),
        "pnl": _f(g("总盈亏")),
        "ret": _f(g("盈亏比")),
        "after": _f(g("清仓距今")),
        "bench": _f(g("同期大盘")),
        "excess": _f(g("跑赢大盘")),
        "days": _f(g("持仓天数")),
        "fee": _f(g("交易费用")),
    }


def _row_trade(r, tmap, since=None):
    g = lambda k: r.get(tmap.get(k)) if tmap.get(k) else None
    d = _s(g("成交日期"))[:10]
    if len(d) < 10 or d < "2000-01-01":
        return None
    if since and d < since:
        return None
    return {
        "date": d,
        "time": _s(g("成交时间")),
        "code": _s(g("代码")),
        "name": _s(g("名称")),
        "kind": _s(g("交易类别")),
        "qty": _f(g("成交数量")),
        "px": _f(g("成交价格")),
        "amt": _f(g("发生金额")),
        "turnover": _f(g("成交金额")),
        "fee": _f(g("费用")),
    }


# ══════════════════════════ 指标计算 ══════════════════════════

def compute(broker, since=None):
    cmap, tmap = broker["cmap"], broker["tmap"]
    closed = [x for x in (_row_closed(r, cmap, since) for r in broker["closed"]) if x]
    trades = [x for x in (_row_trade(r, tmap, since) for r in broker["trades"]) if x]

    rets = [x["ret"] for x in closed if x["ret"] is not None]
    win = [x for x in closed if x["ret"] is not None and x["ret"] > 0]
    los = [x for x in closed if x["ret"] is not None and x["ret"] < 0]

    m = {"meta": broker["meta"], "since": since}
    m["n_closed"] = len(closed)
    m["n_trade"] = len(trades)

    # ── 核心赔率结构 ──
    aw, al = _mean([x["ret"] for x in win]), _mean([x["ret"] for x in los])
    wr = len(win) / max(1, len(win) + len(los))
    payoff = (aw / abs(al)) if (aw is not None and al) else None
    m.update({
        "win": len(win), "loss": len(los), "flat": len(closed) - len(win) - len(los),
        "win_rate_pct": wr * 100.0,
        "avg_win_pct": aw, "avg_loss_pct": al,
        "payoff": payoff,
        "expectancy_pct": (wr * aw + (1 - wr) * al) if (aw is not None and al is not None) else None,
        "median_ret_pct": _median(rets),
        "max_ret_pct": max(rets) if rets else None,
        "min_ret_pct": min(rets) if rets else None,
        "profit_factor": (sum(x["ret"] for x in win) / abs(sum(x["ret"] for x in los)))
                          if los and sum(x["ret"] for x in los) else None,
        "breakeven_win_rate_pct": (100.0 / (1.0 + payoff)) if payoff else None,
    })
    if payoff:
        m["needed_payoff_at_current_winrate"] = (1 - wr) / wr if wr else None

    # ── 资金对账 ──
    dep = sum((x["amt"] or 0) for x in trades if "转存" in x["kind"] or "转入" in x["kind"])
    wd = sum((x["amt"] or 0) for x in trades if "转取" in x["kind"] or "转出" in x["kind"])
    realized = sum((x["pnl"] or 0) for x in closed if x["pnl"] is not None)
    fee_c = sum((x["fee"] or 0) for x in closed if x["fee"] is not None)
    fee_b = sum((x["fee"] or 0) for x in trades if "买入" in x["kind"])
    fee_s = sum((x["fee"] or 0) for x in trades if "卖出" in x["kind"])
    fee_all = (fee_b + fee_s) or fee_c
    turnover = sum((x["turnover"] or 0) for x in trades if "买入" in x["kind"] or "卖出" in x["kind"])
    net_input = dep + wd
    m.update({
        "deposit": dep, "withdraw": wd, "net_input": net_input,
        "realized_pnl": realized, "realized_ret_pct": (realized / net_input * 100.0) if net_input else None,
        "fee_total": fee_all, "fee_buy": fee_b, "fee_sell": fee_s,
        "turnover": turnover,
        "turnover_x": (turnover / net_input / 2.0) if net_input else None,
        "fee_over_turnover_pct": (fee_all / turnover * 100.0) if turnover else None,
        "fee_over_netinput_pct": (fee_all / abs(net_input) * 100.0) if net_input else None,
        "fee_over_loss_pct": (fee_all / abs(realized) * 100.0) if realized else None,
    })
    if closed:
        ds = [x["close"] for x in closed]
        m["span"] = [min(ds), max(ds)]

    # ── 持有周期 ──
    dw = [x["days"] for x in win if x["days"] is not None]
    dl = [x["days"] for x in los if x["days"] is not None]
    da = [x["days"] for x in closed if x["days"] is not None]
    m.update({
        "hold_mean_win": _mean(dw), "hold_mean_loss": _mean(dl),
        "hold_median_win": _median(dw), "hold_median_loss": _median(dl),
        "hold_median_all": _median(da), "hold_mean_all": _mean(da),
        "hold_le3d_pct": (100.0 * sum(1 for d in da if d <= 3) / len(da)) if da else None,
    })
    buckets, long_pnl, long_n = [], 0.0, 0
    for lo, hi, lab in [(0, 1, "≤1天(隔日)"), (1, 3, "2-3天"), (3, 6, "4-6天"),
                        (6, 11, "7-11天"), (11, LONG_HOLD_DAYS, f"12-{LONG_HOLD_DAYS}天"),
                        (LONG_HOLD_DAYS, 1e9, f">{LONG_HOLD_DAYS}天")]:
        S = [x for x in closed if x["days"] is not None and (lo < x["days"] <= hi if lo else x["days"] <= hi)]
        pnl = sum((x["pnl"] or 0) for x in S)
        buckets.append({"label": lab, "n": len(S),
                        "share_pct": (100.0 * len(S) / len(da)) if da else None, "pnl": pnl})
        if lo >= LONG_HOLD_DAYS:
            long_pnl, long_n = pnl, len(S)
    m["hold_buckets"] = buckets
    m["long_hold"] = {"days_gt": LONG_HOLD_DAYS, "n": long_n, "pnl": long_pnl}

    # ── 仓位结构（仓位大小 = 本轮持仓期内「最大占用资金」峰值）──
    # 口径说明：用「窗口内 累计(买入−卖出) 的峰值」，而不是「买入总额」——
    # 买入总额会把同一只票的滚动加仓/做T 累加进去（2026-09-23 实测某债算出 359 万，
    # 而账户净投入只有 65 万，物理上不可能）；峰值即「这只票最多占用过多少钱」，
    # 才是真正的仓位大小，且天然被账户规模约束。
    flow = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for t in trades:
        if "转" in t["kind"]:
            continue
        if "买入" in t["kind"]:
            flow[t["code"]][t["date"]][0] += t["turnover"] or 0.0
        elif "卖出" in t["kind"]:
            flow[t["code"]][t["date"]][1] += t["turnover"] or 0.0
    prev_close = {}
    for x in sorted(closed, key=lambda z: z["close"]):
        days = flow.get(x["code"], {})
        # 窗口起点 = 建仓日期；缺失则退回「上一次清仓日」（防多轮持仓被合并）
        d0 = x["open"] or prev_close.get(x["code"], "")
        ds = sorted(d for d in days if d <= x["close"] and (not d0 or d >= d0))
        peak = net_cap = 0.0
        for d in ds:
            b_, s_ = days[d]
            net_cap += b_ - s_
            peak = max(peak, net_cap)
        x["pos"] = peak
        x["nbuy"] = sum(1 for d in ds if days[d][0] > 0)
        prev_close[x["code"]] = x["close"]
    sized = sorted([x for x in closed if x["pos"] > 0], key=lambda z: z["pos"])
    if sized:
        q1, q2, q3 = (_q([x["pos"] for x in sized], p) for p in (0.25, 0.5, 0.75))
        segs = [(0, q1, "最小 25%"), (q1, q2, "25-50%"), (q2, q3, "50-75%"), (q3, 1e18, "最大 25%")]
        gross_neg = sum((x["pnl"] or 0) for x in closed if (x["pnl"] or 0) < 0)
        # 分母口径：优先「净亏损」（用户最终看到的那个数），无净亏时退回毛亏损。
        # ⚠️ 用毛亏损当分母会把 66% 算成 27.6%（2026-09-23 实测踩过），口径必须写死。
        denom = abs(realized) if realized < 0 else abs(gross_neg)
        seg_out = []
        for lo, hi, lab in segs:
            S = [x for x in sized if lo <= x["pos"] < hi]
            if not S:
                continue
            w = [x for x in S if (x["ret"] or 0) > 0]
            pnl = sum((x["pnl"] or 0) for x in S)
            seg_out.append({"label": lab, "n": len(S), "amt_lo": lo, "amt_hi": None if hi > 1e17 else hi,
                            "win_rate_pct": 100.0 * len(w) / len(S),
                            "avg_ret_pct": _mean([x["ret"] for x in S]), "pnl": pnl,
                            "loss_share_pct": (100.0 * abs(pnl) / denom) if (denom and pnl < 0) else None})
        m["size_segments"] = seg_out
        m["pos_max"] = sized[-1]["pos"]
        m["pos_median"] = _median([x["pos"] for x in sized])
        m["pos_p90"] = _q([x["pos"] for x in sized], 0.90)
        fills = [t["turnover"] for t in trades
                 if "买入" in t["kind"] and "转" not in t["kind"] and t["turnover"]]
        m["fill_max"] = max(fills) if fills else None
        m["fill_median"] = _median(fills)
        m["fill_ge30k_pct"] = (100.0 * sum(1 for v in fills if v >= 30000) / len(fills)) if fills else None
        top = [x for x in sized if x["pos"] >= q3]
        m["top25_loss_share_pct"] = (100.0 * abs(sum((x["pnl"] or 0) for x in top)) / denom) \
            if denom else None
        m["top25_win_rate_pct"] = (100.0 * sum(1 for x in top if (x["ret"] or 0) > 0) / len(top)) if top else None
        m["top25_pnl"] = sum((x["pnl"] or 0) for x in top)
    else:
        m["size_segments"] = []
        m["top25_loss_share_pct"] = None

    # ── 加仓行为（盈利单 vs 亏损单的买入笔数）──
    m["nbuy_mean_win"] = _mean([x["nbuy"] for x in win])
    m["nbuy_mean_loss"] = _mean([x["nbuy"] for x in los])

    # ── 卖飞 / 卖对 ──
    A = [x for x in closed if x["after"] is not None]
    if A:
        af = [x["after"] for x in A]
        fly = [x for x in A if x["after"] > FLY_HI]
        good = [x for x in A if x["after"] < FLY_LO]
        m["after_exit"] = {
            "n": len(A), "mean_pct": _mean(af), "median_pct": _median(af),
            "up_pct": 100.0 * sum(1 for v in af if v > 0) / len(af),
            "flat_pct": 100.0 * sum(1 for v in af if v == 0) / len(af),
            "fly_n": len(fly), "fly_pct": 100.0 * len(fly) / len(A),
            "fly_pnl": sum((x["pnl"] or 0) for x in fly),
            "good_n": len(good), "good_pct": 100.0 * len(good) / len(A),
            "fly_ratio": (len(fly) / len(good)) if good else None,
        }
        m["after_exit"]["fly_top"] = [
            {"code": x["code"], "name": x["name"], "close": x["close"],
             "ret": x["ret"], "after": x["after"], "days": x["days"]}
            for x in sorted(A, key=lambda z: -z["after"])[:15]]
    else:
        m["after_exit"] = None

    # ── 年度趋势 ──
    yr = defaultdict(list)
    for x in closed:
        if x["ret"] is not None:
            yr[x["close"][:4]].append(x)
    ytab = []
    for y in sorted(yr):
        S = yr[y]
        w = [x for x in S if x["ret"] > 0]
        l = [x for x in S if x["ret"] < 0]
        ww = len(w) / max(1, len(w) + len(l))
        awy, aly = _mean([x["ret"] for x in w]), _mean([x["ret"] for x in l])
        ytab.append({"year": y, "n": len(S), "win_rate_pct": ww * 100.0,
                     "expectancy_pct": (ww * awy + (1 - ww) * aly) if (awy is not None and aly is not None) else None,
                     "pnl": sum((x["pnl"] or 0) for x in S),
                     "hold_median": _median([x["days"] for x in S if x["days"] is not None])})
    m["yearly"] = ytab

    # ── 换手频率 ──
    yb = Counter(x["date"][:4] for x in trades if "买入" in x["kind"] or "卖出" in x["kind"])
    m["trades_per_year"] = dict(sorted(yb.items()))
    mb = Counter(x["date"][:7] for x in trades if "买入" in x["kind"] or "卖出" in x["kind"])
    m["trades_per_month_last12"] = dict(sorted(mb.items())[-12:])

    # ── 集中度 / 反复进出 ──
    cc = Counter(x["code"] for x in closed)
    m["symbols"] = {"n": len(cc),
                    "repeat_ge5": sum(1 for c, n in cc.items() if n >= 5),
                    "repeat_ge5_pnl": sum((x["pnl"] or 0) for x in closed
                                          if cc[x["code"]] >= 5),
                    "top": [{"code": c, "name": next((x["name"] for x in closed if x["code"] == c), ""),
                             "n": n, "pnl": sum((x["pnl"] or 0) for x in closed if x["code"] == c)}
                            for c, n in cc.most_common(10)]}

    # ── 买入时点分布 ──
    hb = Counter()
    for t in trades:
        if "买入" in t["kind"] and "转" not in t["kind"]:
            hm = t["time"][:5]
            if ":" in hm:
                try:
                    hb[int(hm.split(":")[0])] += 1
                except ValueError:
                    pass
    m["buy_hours"] = dict(sorted(hb.items())) if hb else None
    tot = sum(hb.values()) if hb else 0
    early = 0
    for t in trades:
        if "买入" in t["kind"] and "转" not in t["kind"] and ":" in t["time"]:
            hm = t["time"][:5]
            if "09:25" <= hm <= "09:35":
                early += 1
    m["buy_auction_pct"] = (100.0 * early / tot) if tot else None
    m["closed_per_trading_day"] = (len(closed) / max(1, len({x["close"] for x in closed})))

    # ── 尾部 ──
    sr = sorted([x for x in closed if x["pnl"] is not None], key=lambda z: z["pnl"])
    m["worst10"] = [{"close": x["close"], "code": x["code"], "name": x["name"], "pnl": x["pnl"],
                     "ret": x["ret"], "days": x["days"]} for x in sr[:10]]
    m["best10"] = [{"close": x["close"], "code": x["code"], "name": x["name"], "pnl": x["pnl"],
                    "ret": x["ret"], "days": x["days"]} for x in sr[-10:][::-1]]
    m["_closed"] = closed
    return m


# ══════════════════════════ 闸门体检 ══════════════════════════

def gates(m):
    """→ [{key, name, target, actual, ok, note}]"""
    g = []

    p, need = m.get("payoff"), m.get("needed_payoff_at_current_winrate")
    g.append({
        "key": "payoff", "name": "① 赔率闸门",
        "target": f"均盈/|均亏| ≥ {GATE_PAYOFF_MIN:.1f}",
        "actual": "—" if p is None else f"{p:.2f}",
        "ok": (p is not None and p >= GATE_PAYOFF_MIN),
        "note": (f"当前胜率 {m['win_rate_pct']:.1f}% 下，要打平需要赔率 ≥ {need:.2f}"
                 if need else "样本不足"),
    })

    hm = m.get("hold_median_all")
    g.append({
        "key": "horizon", "name": "② 周期闸门",
        "target": f"平仓中位持仓 ≥ {GATE_HOLD_MEDIAN_MIN:.0f} 天",
        "actual": "—" if hm is None else f"{hm:.1f} 天",
        "ok": (hm is not None and hm >= GATE_HOLD_MEDIAN_MIN),
        "note": (f"≤3 天平仓占比 {m['hold_le3d_pct']:.1f}%" if m.get("hold_le3d_pct") is not None else ""),
    })

    ts = m.get("top25_loss_share_pct")
    g.append({
        "key": "size", "name": "③ 仓位脱钩",
        "target": f"最大 25% 仓位占净亏损 ≤ {GATE_TOP25_LOSS_MAX*100:.0f}%",
        "actual": "—" if ts is None else f"{ts:.1f}%",
        "ok": (ts is not None and ts <= GATE_TOP25_LOSS_MAX * 100),
        "note": (f"该档胜率 {m['top25_win_rate_pct']:.1f}%、盈亏 {m['top25_pnl']:+,.0f} 元"
                 if ts is not None and m.get("top25_win_rate_pct") is not None else ""),
    })

    fr = m.get("fee_over_loss_pct")
    g.append({
        "key": "friction", "name": "④ 摩擦（辅助）",
        "target": f"交易费用 / |已实现亏损| ≤ {GATE_FEE_RATIO_MAX*100:.0f}%",
        "actual": "—" if fr is None else f"{fr:.1f}%",
        "ok": (fr is not None and fr <= GATE_FEE_RATIO_MAX * 100),
        "note": f"费用合计 {m.get('fee_total', 0):,.0f} 元 · 周转 {m.get('turnover_x') or 0:.0f} 倍",
    })

    ae = m.get("after_exit")
    fr2 = ae.get("fly_ratio") if ae else None
    g.append({
        "key": "fly", "name": "⑤ 卖飞比（辅助）",
        "target": f"卖后 >+{FLY_HI:.0f}% 笔数 / 卖后 <{FLY_LO:.0f}% 笔数 ≤ {GATE_FLY_RATIO_MAX:.1f}",
        "actual": "—" if fr2 is None else f"{fr2:.2f}",
        "ok": (fr2 is not None and fr2 <= GATE_FLY_RATIO_MAX),
        "note": (f"卖飞 {ae['fly_n']} 笔（这些票上实际亏 {ae['fly_pnl']:+,.0f} 元）/ 卖对 {ae['good_n']} 笔"
                 if ae else "缺「清仓距今」列，跳过"),
    })
    return g


# ══════════════════════════ 渲染 ══════════════════════════

def render(m, gs):
    L = []
    A = L.append
    meta = m["meta"]
    A("# 📒 行为归因体检（用你自己的交割单）")
    A("")
    A(f"- 数据源：`{meta['file']}`")
    if m.get("span"):
        A(f"- 区间：**{m['span'][0]} → {m['span'][1]}**"
          + (f"（已按 `--since {m['since']}` 过滤）" if m.get("since") else ""))
    A(f"- 样本：**{m['n_closed']}** 笔平仓 / **{m['n_trade']}** 条成交")
    A("")

    A("## 一、闸门体检（红=未达标）")
    A("")
    A("| 闸门 | 目标 | 实际 | 结论 | 备注 |")
    A("|---|---|---|---|---|")
    for x in gs:
        A(f"| {x['name']} | {x['target']} | **{x['actual']}** | {'✅ 达标' if x['ok'] else '❌ 未达标'} | {x['note']} |")
    A("")

    A("## 二、赔率结构（决定生死的一组数字）")
    A("")
    A(f"- 胜率 **{m['win_rate_pct']:.1f}%**（{m['win']} 胜 / {m['loss']} 负 / {m['flat']} 平）"
      f" · 打平线需 **{m['breakeven_win_rate_pct']:.1f}%**" if m.get("breakeven_win_rate_pct") else "")
    A(f"- 平均盈利 **{_pct(m.get('avg_win_pct'))}** / 平均亏损 **{_pct(m.get('avg_loss_pct'))}**"
      f" → **盈亏比 {m['payoff']:.2f}**" if m.get("payoff") else "")
    A(f"- **单笔期望 {_pct(m.get('expectancy_pct'), 3)}** · profit factor {m.get('profit_factor') or 0:.3f}"
      f" · 收益中位 {_pct(m.get('median_ret_pct'))}")
    A(f"- 资金对账：入金 {_num(m['deposit'])} − 出金 {_num(abs(m['withdraw']))} = **净投入 {_num(m['net_input'])}**；"
      f"已实现盈亏 **{m['realized_pnl']:+,.0f} 元（{_pct(m.get('realized_ret_pct'))}）**")
    A("")

    A("## 三、持有周期（赔率结构的「因」）")
    A("")
    A(f"- 中位持仓 **{m.get('hold_median_all') or 0:.1f} 天** · 均值 {m.get('hold_mean_all') or 0:.1f} 天"
      f" · ≤3 天平仓占比 **{m.get('hold_le3d_pct') or 0:.1f}%**")
    A(f"- 盈利单中位 {m.get('hold_median_win') or 0:.1f} 天 / 亏损单中位 {m.get('hold_median_loss') or 0:.1f} 天")
    A("")
    A("| 持有档位 | 笔数 | 占比 | 盈亏(元) |")
    A("|---|---|---|---|")
    for b in m.get("hold_buckets", []):
        A(f"| {b['label']} | {b['n']} | {b['share_pct'] or 0:.1f}% | {b['pnl']:+,.0f} |")
    A("")

    if m.get("size_segments"):
        A("## 四、仓位结构（仓位越大是否亏越狠）")
        A("")
        A("| 仓位分位 | 笔数 | 胜率 | 平均单笔 | 盈亏(元) | 占净亏损 |")
        A("|---|---|---|---|---|---|")
        for s in m["size_segments"]:
            A(f"| {s['label']} | {s['n']} | {s['win_rate_pct']:.1f}% | {_pct(s['avg_ret_pct'])} | "
              f"{s['pnl']:+,.0f} | " + ("—" if s["loss_share_pct"] is None else f"{s['loss_share_pct']:.1f}%") + " |")
        A("")
        A(f"- **仓位大小**（本轮持仓内最大占用资金）中位 {_num(m.get('pos_median'))} / "
          f"P90 {_num(m.get('pos_p90'))} / 最大 **{_num(m.get('pos_max'))} 元**")
        A(f"- **单笔买入成交额** 中位 {_num(m.get('fill_median'))} / 最大 **{_num(m.get('fill_max'))} 元**"
          f" · ≥3 万的买入占 **{m.get('fill_ge30k_pct') or 0:.1f}%**")
        A(f"- 每笔平仓对应买入笔数：盈利单 {m.get('nbuy_mean_win') or 0:.2f} 笔 vs 亏损单 {m.get('nbuy_mean_loss') or 0:.2f} 笔")
        A("")

    A("## 五、频率与摩擦")
    A("")
    A(f"- 双边成交额 **{_num(m['turnover'])} 元** · 周转 **{m.get('turnover_x') or 0:.0f} 倍**"
      f"（净投入口径）")
    A(f"- 费用合计 **{_num(m['fee_total'])} 元** = 净投入的 **{m.get('fee_over_netinput_pct') or 0:.1f}%**"
      f" = 已实现亏损的 **{m.get('fee_over_loss_pct') or 0:.1f}%**"
      f" · 占成交额 {m.get('fee_over_turnover_pct') or 0:.4f}%")
    A(f"- 平均每交易日平仓 **{m.get('closed_per_trading_day') or 0:.1f} 笔**"
      + (f" · 买入落在 09:25-09:35 竞价后抢入 **{m['buy_auction_pct']:.1f}%**"
         if m.get("buy_auction_pct") is not None else ""))
    A("")

    if m.get("after_exit"):
        ae = m["after_exit"]
        A("## 六、卖飞率（卖出之后这些票涨了还是跌了）")
        A("")
        A(f"- 卖后均值 **{_pct(ae['mean_pct'])}** · 中位 **{_pct(ae['median_pct'])}** · 上涨占比 {ae['up_pct']:.1f}%")
        A(f"- **卖飞（>+{FLY_HI:.0f}%）{ae['fly_n']} 笔（{ae['fly_pct']:.1f}%）→ 这些票上你实际亏 "
          f"{ae['fly_pnl']:+,.0f} 元**；卖对（<{FLY_LO:.0f}%）{ae['good_n']} 笔")
        A(f"- **卖飞 : 卖对 = {ae['fly_ratio'] or 0:.2f} : 1**（阈值 ≤ {GATE_FLY_RATIO_MAX:.1f}）")
        A("")
        A("| 卖出日 | 代码 | 名称 | 本笔收益 | 卖后涨幅 | 持天数 |")
        A("|---|---|---|---|---|---|")
        for x in ae.get("fly_top", []):
            A(f"| {x['close']} | {x['code']} | {x['name']} | {_pct(x['ret'], 1)} | "
              f"**{_pct(x['after'], 1)}** | {x['days'] or 0:.0f} |")
        A("")

    A("## 七、年度趋势（是否在收敛）")
    A("")
    A("| 年份 | 笔数 | 胜率 | 单笔期望 | 盈亏(元) | 中位持仓 |")
    A("|---|---|---|---|---|---|")
    for y in m.get("yearly", []):
        A(f"| {y['year']} | {y['n']} | {y['win_rate_pct']:.1f}% | {_pct(y['expectancy_pct'], 3)} | "
          f"{y['pnl']:+,.0f} | {y['hold_median'] or 0:.1f} 天 |")
    A("")

    A("## 八、集中度与尾部")
    A("")
    sy = m.get("symbols", {})
    A(f"- 交易过的标的 **{sy.get('n')}** 只，其中平仓 ≥5 次的 **{sy.get('repeat_ge5')}** 只"
      f"（贡献 **{sy.get('repeat_ge5_pnl', 0):+,.0f} 元**）")
    A("")
    A("| 最惨 5 笔 | 盈亏(元) | | 最佳 5 笔 | 盈亏(元) |")
    A("|---|---|---|---|---|")
    for i in range(5):
        w = m["worst10"][i] if i < len(m["worst10"]) else None
        b = m["best10"][i] if i < len(m["best10"]) else None
        A(f"| {w['name']}({w['code']}) {_pct(w['ret'],1)} | {w['pnl']:+,.0f} | | "
          f"{b['name']}({b['code']}) {_pct(b['ret'],1)} | {b['pnl']:+,.0f} |"
          if w and b else "")
    A("")
    A("---")
    A("")
    A("⛔ 边界：本表是**事后行为统计**，不是预测，也不构成「当时就该那样做」的结论；")
    A("   样本口径 = 你导出的交割单，未覆盖区间外数据。改闸门阈值请同步 `config/shared_rules.md`。")
    return "\n".join(x for x in L if x is not None)


def system_compare():
    """系统账本（factor_attribution 侧）对照——可选，缺文件静默跳过"""
    if not NAV.exists():
        return None
    rows = []
    for line in NAV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rs = []
    for r in rows:
        for s in (r.get("stocks") or []):
            v = s.get("return_pct")
            if isinstance(v, (int, float)):
                rs.append(float(v))
    if not rs:
        return None
    w = [x for x in rs if x > 0]
    l = [x for x in rs if x < 0]
    wr = len(w) / max(1, len(w) + len(l))
    aw, al = _mean(w), _mean(l)
    return {"days": len(rows), "n": len(rs), "win_rate_pct": wr * 100.0,
            "expectancy_pct": (wr * aw + (1 - wr) * al) if (aw is not None and al is not None) else None,
            "avg_win_pct": aw, "avg_loss_pct": al}


def main():
    ap = argparse.ArgumentParser(description="行为归因体检（交割单 → 三条闸门）")
    ap.add_argument("--path", required=True, help="交割单路径（.xlsx/.xlsm/.csv）")
    ap.add_argument("--since", default=None, help="只看该日期之后的平仓（YYYY-MM-DD）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--persist", action="store_true",
                    help=f"落盘（默认只读不写）；目标 {OUT_DEFAULT.relative_to(ROOT)}")
    ap.add_argument("--out", default=None, help="落盘路径（隐含 --persist）")
    ap.add_argument("--no-system", action="store_true", help="不做系统账本对照")
    args = ap.parse_args()

    path = Path(args.path).expanduser()
    if not path.exists():
        sys.exit(f"文件不存在：{path}")

    broker = load_broker(path)
    m = compute(broker, since=args.since)
    gs = gates(m)

    if not args.no_system:
        sc = system_compare()
        if sc:
            m["system_ledger"] = sc

    m.pop("_closed", None)
    payload = {"generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
               "metrics": m, "gates": gs}
    if m.get("system_ledger"):
        payload["system_ledger"] = m["system_ledger"]

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(m, gs))
        if m.get("system_ledger"):
            sc = m["system_ledger"]
            print()
            print("## 附：系统账本对照（AI 口径独立轨，起点不同不可直接比大小）")
            print()
            print(f"- 记录 {sc['days']} 个交易日 / {sc['n']} 只 · 胜率 {sc['win_rate_pct']:.1f}%"
                  f" · 单笔期望 {_pct(sc['expectancy_pct'], 3)}")

    if args.persist or args.out:
        out = Path(args.out).expanduser() if args.out else OUT_DEFAULT
        # ⛔ 测试期禁止写生产路径（防夹具写穿）
        import os
        if os.environ.get("PYTEST_CURRENT_TEST") and "behavior_attribution_test" not in str(out):
            sys.exit("❌ 测试环境拒绝写生产路径（--out 请指向临时目录）")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.json:
            print()
            print(f"✅ 已落盘：{out.relative_to(ROOT) if str(out).startswith(str(ROOT)) else out}")


if __name__ == "__main__":
    main()
