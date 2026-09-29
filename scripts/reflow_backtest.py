#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中回流信号回测（只读）· 2026-09-22 立项（用户：「把这个回测专门用来作为『盘中回流』模块的回测计算，让盘中回流以后越来越准」）

## 回测对象
`scripts/live_signal.py` 的 **🔔 回流前兆**（卡名 limao_reflow_card.json）：
    · 主判据 = 该题材 `flow_trend` 资金趋势在允许档位（`_reflow_flow_ok`）
    · 盘中实时资金榜命中降级为佐证（`_reflow_name_hit`·09-15 已加长度门）
    · 每次触发落 `pushed_signals_<date>.json` 的 `reflow_pushed`(题材→触发时刻)
      + `reflow_sig_map`(题材→"命中板块名#代码|代码|…") —— **本脚本据此重建信号**

## 为什么不能只看「信号后涨没涨」
2026-09-22 预测精度审计的教训：**没有基准的命中率只是市场基准的镜像**（预期中枢 199/199 全为正，
方向净增益恒 +0.0pp）。故本回测**每条信号都强制带两个基准**：
    · `base_cand` = 同日 AI 候选池（relay+watch）的次日收益均值 → 回答「回流是否比常规候选更有价值」
    · `base_zt`   = 同日涨停池全部票的次日收益均值         → 回答「回流是否跑赢当日强势股群体」
    **超额 = 池内均值 − 基准**；无超额即该信号无信息量，不论绝对涨跌。

## 口径（与既有系统同源·⛔ 禁另造）
    · 日K        = `common.tencent_kline.fetch_kline_ohlc`（经 decision_audit 的缓存层复用）
    · 涨停判定   = `cache/zt_history/zt_<date>.json` 的 `zt_list`（仓内唯一涨停权威）
    · 交易日历   = 日K实际日期（`kline_calendar`）⛔ 不用 zt 归档日（缺一天会静默位移）
    · 次日收益   = (D+1 收盘 − D 收盘)/D 收盘 ×100；次日高开 = (D+1 开盘 − D 收盘)/D 收盘 ×100
    · 缺失一律 None（⛔ 不填 0、不借相邻日、不用前收外推）

## 三个分档（用来定位「哪类回流信号才有用」）
    ① 触发时段：盘前(≤09:25) / 早盘(09:25~11:30) / 午后(≥13:00)
       —— 盘前信号的当日涨跌幅是**干净前向**；盘中信号的当日涨幅已被信号自身时段污染 → 主看 D+1
    ② 是否重复：同题材近 3 个交易日内已触发过（钝化嫌疑）
    ③ 池内票数 / 是否命中当日资金榜（board 名非空）

## ⛔ 边界（硬约束·勿越）
    · **只读**：不改任何阈值 / 不改 `live_signal.py` / 不推送 / 不碰自动化 / 不动 skill
    · 产物只有：`cache/reflow_backtest.json` / `.md`（报告）
      + `cache/reflow_signal_log.jsonl`（**前向账本**·append-only·按 date+theme 幂等）
      + 共用 `cache/audit_close_cache.json`（日K缓存·与 decision_audit 同一文件，防两套口径）
    · **回填不可逆**：`pushed_signals_*` 只保留近数个交易日、`theme_rotation.json` 无历史快照
      ⇒ 更早的信号**无法重建**（audit.jsonl 有推送事件但无题材）。故必须靠本账本**前向累积**。
    · 分组样本 < `LOW_SAMPLE`(20) 一律标注「样本不足·只呈现不裁决」
    · ⛔ 建议清单**只呈现**，改 `live_signal.py` 必须用户拍板（对齐「skill/代码永不自动改」铁律）

用法：
    python3 scripts/reflow_backtest.py                # 全量回测（联网补日K缺口）
    python3 scripts/reflow_backtest.py --no-fetch     # 离线（只用已缓存日K）
    python3 scripts/reflow_backtest.py --days 20      # 只看最近 N 个交易日内的信号
    python3 scripts/reflow_backtest.py --json         # 只出 JSON 摘要
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
SCRIPTS = BASE / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# 复用 decision_audit 的日K层（单一源·禁另造口径）
from decision_audit import (          # noqa: E402
    load_close_cache, save_close_cache, fetch_bars, kline_calendar, nth_next,
    close_of, open_of, _load, _num, _avg,
)

CACHE = BASE / "cache"
ZT_HIST = CACHE / "zt_history"
CANDS = BASE / "wealth_code" / "cache"
OUT_JSON = CACHE / "reflow_backtest.json"
OUT_MD = CACHE / "reflow_backtest.md"
LEDGER = CACHE / "reflow_signal_log.jsonl"

BJ_TZ = timezone(timedelta(hours=8))
LOW_SAMPLE = 20                    # 分组样本门槛：低于此值只呈现不裁决
REPEAT_LOOKBACK_DAYS = 3           # 「重复触发」回看窗口（钝化嫌疑）

# 时段分档（HHMM 整数比较）· ⚠️ 早盘须覆盖 11:30（午盘收市那一刻的日志属早盘口径）
BUCKETS = (("pre", 0, 924, "盘前 ≤09:24"), ("am", 925, 1159, "早盘 09:25~11:59"), ("pm", 1300, 2400, "午后 ≥13:00"))


# ────────────────────────── 信号重建 ──────────────────────────
def _split_sig_map(v: str) -> tuple[str, list[str]]:
    """`reflow_sig_map` 值 → (命中板块名, 跟踪池代码)。格式 "光通信模块#000070.SZ|002281.SZ"。

    ⛔ 无 "#" 时 board 记空串（= 未命中当日资金榜），不是数据缺口。
    """
    s = str(v or "")
    if "#" not in s:
        return "", []
    board, _, codes = s.partition("#")
    out = []
    for c in codes.split("|"):
        c = c.strip().upper()
        for suf in (".SZ", ".SH", ".BJ"):
            if c.endswith(suf):
                c = c[: -len(suf)]
        if c:
            out.append(c)
    return board.strip(), out


def collect_signals() -> list[dict]:
    """从 `pushed_signals_*.json` 重建回流信号（按 date+theme 去重·同日同题材只算一条）。"""
    sigs: dict[tuple[str, str], dict] = {}
    for f in sorted(CACHE.glob("pushed_signals_*.json")):
        raw = f.stem.replace("pushed_signals_", "")
        if len(raw) != 8 or not raw.isdigit():
            continue
        d10 = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        blob = _load(f, {}) or {}
        pushed = blob.get("reflow_pushed") or {}
        sigmap = blob.get("reflow_sig_map") or {}
        if not isinstance(pushed, dict):
            continue
        for theme, ts in pushed.items():
            key = (d10, str(theme))
            if key in sigs:                       # 同日已有（含重复写）→ 保留最早时刻
                continue
            tstr = str(ts or "")
            hhmm = 0
            try:
                hhmm = int(tstr[11:13]) * 100 + int(tstr[14:16])
            except Exception:
                pass
            board, codes = _split_sig_map(sigmap.get(theme, ""))
            sigs[key] = {
                "signal_date": d10,
                "ts": tstr,
                "hhmm": hhmm,
                "theme": str(theme),
                "board": board,
                "codes": codes,
            }
    return sorted(sigs.values(), key=lambda x: (x["signal_date"], x["hhmm"]))


def append_ledger(sigs: list[dict], path: Path | None = None) -> list[dict]:
    """前向账本：把本次见到的信号追加进 `reflow_signal_log.jsonl`（date+theme 幂等）。

    ⛔ 存在的唯一理由：`pushed_signals_*` 会被轮转淘汰、`theme_rotation.json` 无历史
    ⇒ 不落账本则每次回测都只有最近几日的样本，永远长不出统计意义。

    ⚠️ `path` 可注入（默认 = 生产 `cache/reflow_signal_log.jsonl`）：测试**必须**显式注入
    临时路径，防夹具信号写穿生产账本（对齐 `zt_archive` 的注入铁律）。
    """
    target = Path(path) if path else LEDGER
    known: set[tuple[str, str]] = set()
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            known.add((str(r.get("signal_date")), str(r.get("theme"))))
    new = [s for s in sigs if (s["signal_date"], s["theme"]) not in known]
    if new and path is None:
        target.parent.mkdir(parents=True, exist_ok=True)
    if new:
        with open(target, "a", encoding="utf-8") as fh:
            for s in new:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    return sigs


def load_ledger(path: Path | None = None) -> list[dict]:
    target = Path(path) if path else LEDGER
    if not target.exists():
        return []
    out = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ────────────────────────── 真值与基准 ──────────────────────────
def load_zt_codes() -> dict[str, set[str]]:
    """各交易日涨停池代码集合（仓内唯一涨停权威）。

    ⚠️ **键统一为无横线 `YYYYMMDD`**：日K层的日期（`kline_calendar` / `nth_next`）是无横线格式，
    而 `zt_list` 归档的 `date` 字段是 `YYYY-MM-DD`。两者混用会让 `dp1 in zt` **恒为 False**
    ⇒ 次日涨停率静默全为空（2026-09-22 实测踩中，且当日涨停率因 d0 恰好同为带横线而"看起来正常"）。
    """
    out: dict[str, set[str]] = {}
    for f in sorted(ZT_HIST.glob("zt_*.json")):
        d = _load(f, {}) or {}
        zl = d.get("zt_list") or []
        date = str(d.get("date") or f.stem.replace("zt_", ""))[:10].replace("-", "")
        if zl:
            out[date] = {str(r.get("code")) for r in zl if r.get("code")}
    return out


def _d8(d: str | None) -> str | None:
    """日期归一化为无横线 `YYYYMMDD`（与日K层对齐）。None/空 → None。"""
    if not d:
        return None
    return str(d).replace("-", "")


def load_cand_codes() -> dict[str, list[str]]:
    """各推荐日 AI 候选池代码（relay + watch）——作为「常规候选」基准。键同归一化为无横线。"""
    out: dict[str, list[str]] = {}
    for f in sorted(CANDS.glob("candidates-*.json")):
        d = _load(f, {}) or {}
        date = _d8(str(d.get("date") or f.stem.replace("candidates-", ""))[:10])
        codes: list[str] = []
        for k in ("relay", "watch"):
            for it in (d.get(k) or []):
                if isinstance(it, dict) and it.get("code"):
                    codes.append(str(it["code"]))
        if codes and date:
            out[date] = sorted(set(codes))
    return out


def _pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / prev * 100, 2)


def attach_truth(row: dict, cache: dict, cal: list[str], zt: dict[str, set[str]]) -> dict:
    """给一条信号补齐池内个股的 D/D+1 表现 + 两个基准。缺失一律 None。"""
    d0 = row["signal_date"]
    k0, kp1 = _d8(d0), _d8(nth_next(cal, d0, 1))
    dm1, dp1 = nth_next(cal, d0, -1), nth_next(cal, d0, 1)
    codes = row.get("codes") or []

    per: list[dict] = []
    for c in codes:
        c0, cm1, cp1 = close_of(cache, c, d0), close_of(cache, c, dm1), close_of(cache, c, dp1)
        per.append({
            "code": c,
            "ret_d0": _pct(c0, cm1),                    # 信号当日涨跌幅
            "ret_d1": _pct(cp1, c0),                    # 次日收益（主口径）
            "gap_d1": _pct(open_of(cache, c, dp1), c0),  # 次日高开（竞价代理）
            # ⚠️ 三态：True 涨停 / False 未涨停 / **None 无法判定**（该涨停池归档缺失）
            "zt_d0": (c in zt[k0]) if k0 in zt else None,
            "zt_d1": (c in zt[kp1]) if (kp1 and kp1 in zt) else None,
        })
    row["pool"] = per
    row["n_pool"] = len(per)

    def _m(k: str) -> float | None:
        return _avg([p[k] for p in per if p.get(k) is not None])

    row["pool_ret_d0"] = _m("ret_d0")
    row["pool_ret_d1"] = _m("ret_d1")
    row["pool_gap_d1"] = _m("gap_d1")
    # ⛔ 涨停计数必须分「命中数」与「可判定数」两个字段：归档缺失 ≠ 未涨停，
    #    否则命中率会把缺数据当成负面证据（分母虚增）。
    for tag, key in (("d0", "zt_d0"), ("d1", "zt_d1")):
        known = [p[key] for p in per if p[key] is not None]
        row[f"pool_zt_{tag}_n"] = sum(1 for x in known if x)
        row[f"pool_zt_{tag}_known"] = len(known)

    # ── 基准①：同日 AI 候选池（relay+watch）次日收益
    b1 = []
    for c in (row.get("_cand_codes") or []):
        b1.append(_pct(close_of(cache, c, dp1), close_of(cache, c, d0)))
    row["base_cand_ret_d1"] = _avg([x for x in b1 if x is not None])
    row["base_cand_n"] = len([x for x in b1 if x is not None])

    # ── 基准②：同日涨停池群体次日收益
    b2 = []
    if k0 in zt:
        for c in sorted(zt[k0]):
            b2.append(_pct(close_of(cache, c, dp1), close_of(cache, c, d0)))
    row["base_zt_ret_d1"] = _avg([x for x in b2 if x is not None])
    row["base_zt_n"] = len([x for x in b2 if x is not None])

    # ── 超额（无基准 → None，不填 0）
    for tag, base in (("cand", row["base_cand_ret_d1"]), ("zt", row["base_zt_ret_d1"])):
        row[f"excess_{tag}_d1"] = (round(row["pool_ret_d1"] - base, 2)
                                   if (row["pool_ret_d1"] is not None and base is not None) else None)
    return row


def bucket_of(hhmm: int) -> str:
    for key, lo, hi, _lab in BUCKETS:
        if lo <= hhmm <= hi:
            return key
    return "am"


def bucket_label(key: str) -> str:
    for k, _lo, _hi, lab in BUCKETS:
        if k == key:
            return lab
    return key


def mark_repeat(rows: list[dict]) -> None:
    """标记钝化嫌疑：同题材在 `REPEAT_LOOKBACK_DAYS` 个交易日内已触发过。

    ⚠️ 2026-09-22 修：原实现写 `cur in _within_n(prior, cur, n)` —— `_within_n` 返回的是
    **prior 日期列表**，当前日期永不在其中 ⇒ `is_repeat` **恒为 False**（钝化检测完全失效）。
    正确写法 = 判 `_within_n(prior, cur, n)` **非空**。
    """
    by_theme: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_theme[r["theme"]].append(r["signal_date"])
    for r in rows:
        prior = sorted(d for d in by_theme[r["theme"]] if d < r["signal_date"])
        r["theme_prior_days"] = len(prior)
        r["is_repeat"] = bool(_within_n(prior, r["signal_date"], REPEAT_LOOKBACK_DAYS))
        r["theme_all_days"] = len(by_theme[r["theme"]])


def _within_n(prior_dates: list[str], cur: str, n: int) -> list[str]:
    """prior 中与 cur 相隔 ≤ n 个交易日的日期（用自然日近似·仅作钝化粗筛）。"""
    try:
        c = datetime.strptime(cur, "%Y-%m-%d")
    except Exception:
        return []
    out = []
    for d in prior_dates:
        try:
            delta = (c - datetime.strptime(d, "%Y-%m-%d")).days
        except Exception:
            continue
        if 0 < delta <= n + 2:          # 周末容差
            out.append(d)
    return out


# ────────────────────────── 汇总 ──────────────────────────
def _agg(rows: list[dict], key: str) -> float | None:
    return _avg([r[key] for r in rows if r.get(key) is not None])


def _rate(rows: list[dict], num: str, den: str) -> float | None:
    n = sum(r.get(num) or 0 for r in rows)
    d = sum(r.get(den) or 0 for r in rows)
    return round(n / d * 100, 1) if d else None


def group_stat(rows: list[dict], label: str) -> dict:
    return {
        "label": label,
        "n_signals": len(rows),
        "n_pool": sum(r.get("n_pool") or 0 for r in rows),
        "pool_ret_d0": _agg(rows, "pool_ret_d0"),
        "pool_ret_d1": _agg(rows, "pool_ret_d1"),
        "pool_gap_d1": _agg(rows, "pool_gap_d1"),
        "base_cand_ret_d1": _agg(rows, "base_cand_ret_d1"),
        "base_zt_ret_d1": _agg(rows, "base_zt_ret_d1"),
        "excess_cand_d1": _agg(rows, "excess_cand_d1"),
        "excess_zt_d1": _agg(rows, "excess_zt_d1"),
        "zt_d0_rate": _rate(rows, "pool_zt_d0_n", "pool_zt_d0_known"),
        "zt_d1_rate": _rate(rows, "pool_zt_d1_n", "pool_zt_d1_known"),
        "zt_d1_n": sum(r.get("pool_zt_d1_n") or 0 for r in rows),
        "zt_d1_known": sum(r.get("pool_zt_d1_known") or 0 for r in rows),
        "low_sample": len(rows) < LOW_SAMPLE,
    }


def build_report(rows: list[dict]) -> dict:
    windows = {
        "overall": group_stat(rows, "全窗口"),
    }
    for key, _lo, _hi, lab in BUCKETS:
        seg = [r for r in rows if bucket_of(r["hhmm"]) == key]
        if seg:
            windows[f"bucket_{key}"] = group_stat(seg, lab)
    first = [r for r in rows if not r.get("is_repeat")]
    rep = [r for r in rows if r.get("is_repeat")]
    if first:
        windows["first_trigger"] = group_stat(first, "首次触发")
    if rep:
        windows["repeat_trigger"] = group_stat(rep, f"重复触发（近{REPEAT_LOOKBACK_DAYS}交易日内已发）")
    hit = [r for r in rows if r.get("board")]
    miss = [r for r in rows if not r.get("board")]
    if hit:
        windows["board_hit"] = group_stat(hit, "命中当日资金榜")
    if miss:
        windows["board_miss"] = group_stat(miss, "未命中当日资金榜")

    # 钝化候选：窗口内触发 ≥3 个交易日
    dull = [{"theme": t, "days": n} for t, n in
            Counter(r["theme"] for r in rows).items() if n >= 3]
    dull.sort(key=lambda x: -x["days"])

    return {
        "schema": "reflow_backtest_v1",
        "generated_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        "window": {
            "from": rows[0]["signal_date"] if rows else None,
            "to": rows[-1]["signal_date"] if rows else None,
            "days_with_signal": len({r["signal_date"] for r in rows}),
            "n_signals": len(rows),
        },
        "windows": windows,
        "dull_candidates": dull,
        "thresholds": {"LOW_SAMPLE": LOW_SAMPLE, "REPEAT_LOOKBACK_DAYS": REPEAT_LOOKBACK_DAYS},
        "signals": rows,
    }


# ────────────────────────── 渲染 ──────────────────────────
def _f(v: Any, p: int = 2, sign: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:+.{p}f}" if sign else f"{v:.{p}f}"


def render_md(res: dict) -> str:
    L: list[str] = []
    w = res["window"]
    L.append("# 盘中回流信号回测（🔔 回流前兆）")
    L.append("")
    L.append(f"窗口 **{w['from']} ~ {w['to']}**｜有信号交易日 **{w['days_with_signal']}** 天｜信号 **{w['n_signals']}** 条")
    L.append("")
    L.append("> 口径：次日收益 = (D+1收盘−D收盘)/D收盘。**超额 = 池内均值 − 基准**；")
    L.append("> 无超额即该信号无信息量，不论绝对涨跌。⛔ 分组样本 < 20 只呈现不裁决。")
    L.append("")
    L.append("## 一、分组表现")
    L.append("")
    L.append("| 分组 | 信号 | 池内票 | 当日% | **次日%** | 次日高开% | 基准:候选% | 基准:涨停池% | **超额vs候选** | 超额vs涨停池 | 当日涨停率 | 次日涨停率 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    order = ["overall", "bucket_pre", "bucket_am", "bucket_pm",
             "first_trigger", "repeat_trigger", "board_hit", "board_miss"]
    for k in order:
        s = res["windows"].get(k)
        if not s:
            continue
        mark = " ⚠️样本不足" if s["low_sample"] else ""
        zt1 = "—" if s.get("zt_d1_rate") is None else f"{s['zt_d1_rate']:.1f}%（可判 {s['zt_d1_known']}）"
        L.append(
            f"| {s['label']}{mark} | {s['n_signals']} | {s['n_pool']} | {_f(s['pool_ret_d0'])} | "
            f"**{_f(s['pool_ret_d1'])}** | {_f(s['pool_gap_d1'])} | {_f(s['base_cand_ret_d1'])} | "
            f"{_f(s['base_zt_ret_d1'])} | **{_f(s['excess_cand_d1'], 2, True)}** | "
            f"{_f(s['excess_zt_d1'], 2, True)} | {_f(s['zt_d0_rate'], 1)}% | {zt1} |"
        )
    L.append("")
    L.append("## 二、逐条明细")
    L.append("")
    L.append("| 日期 | 时刻 | 题材 | 命中板块 | 池 | 当日% | 次日% | 超额 | 备注 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in res["signals"]:
        note = []
        if r.get("is_repeat"):
            note.append("重复触发")
        if not r.get("board"):
            note.append("未上榜")
        if (r.get("pool_ret_d1") is None):
            note.append("日K缺失")
        L.append(
            f"| {r['signal_date']} | {r['hhmm']:04d} | {r['theme']} | {r.get('board') or '—'} | "
            f"{r.get('n_pool')} | {_f(r.get('pool_ret_d0'))} | {_f(r.get('pool_ret_d1'))} | "
            f"{_f(r.get('excess_cand_d1'), 2, True)} | {'、'.join(note) or ''} |"
        )
    L.append("")
    if res["dull_candidates"]:
        L.append("## 三、钝化候选（窗口内触发 ≥3 个交易日）")
        L.append("")
        L.append("| 题材 | 触发天数 |")
        L.append("|---|---|")
        for d in res["dull_candidates"]:
            L.append(f"| {d['theme']} | {d['days']} |")
        L.append("")
    L.append("## 四、待拍板的调整项（本脚本不自动应用）")
    L.append("")
    L.extend(_suggestions(res))
    L.append("")
    L.append("## 五、口径边界（不可测项·勿当缺失）")
    L.append("")
    L.append("- **漏报不可测**：无「应触发未触发」日志 ⇒ 只能评已发信号的质量，不能评召回率。")
    L.append("- **回填不可逆**：`pushed_signals_*` 只保留近数个交易日、`theme_rotation.json` 无历史快照")
    L.append("  ⇒ 更早信号无法重建（audit.jsonl 有推送事件但无题材），样本靠 `cache/reflow_signal_log.jsonl` 前向累积。")
    L.append("- **盘中信号的当日涨幅被时段污染**：如 14:03 触发的信号，其 D 日涨幅已含信号前的大半走势 ⇒ 盘中批次主看 D+1。")
    L.append("- **次日高开用开盘价代理竞价成交价**（分时/竞价快照无历史留存）。")
    L.append("- **题材级真值未做**：`zt_list.sector` 是行业名、信号 theme 是题材名，两者命名体系不同，硬匹配会重演「电子布⊂电子」类假命中 ⇒ 只用跟踪池代码作真值。")
    return "\n".join(L)


def _suggestions(res: dict) -> list[str]:
    out: list[str] = []
    W = res["windows"]
    ov = W.get("overall") or {}
    if not ov.get("n_signals"):
        return ["- 暂无信号样本 ⇒ 先让账本累积（每日盘后跑一次本脚本）。"]
    if ov.get("low_sample"):
        out.append(f"- ⚠️ 全窗口仅 {ov['n_signals']} 条信号，**任何阈值调整都不该基于此样本**；先累积到 ≥{LOW_SAMPLE} 条再谈。")
    ex_c, ex_z = ov.get("excess_cand_d1"), ov.get("excess_zt_d1")
    if ex_c is not None and ex_z is not None:
        if ex_c <= 0 and ex_z <= 0:
            out.append(f"- 🔴 两个基准**全部跑输**（vs 候选 {ex_c:+.2f}pt / vs 涨停池 {ex_z:+.2f}pt）⇒ 当前回流信号**无信息增量**，"
                       "优先查判据（`flow_trend` 档位是否过宽）而不是加卡。")
        elif ex_c > 0 or ex_z > 0:
            out.append(f"- ✅ 存在正超额（vs 候选 {ex_c:+.2f}pt / vs 涨停池 {ex_z:+.2f}pt）⇒ 信号方向有效，可继续观察。")
    pre, am, pm = W.get("bucket_pre"), W.get("bucket_am"), W.get("bucket_pm")
    if pre and pre.get("n_signals") and not pre.get("low_sample"):
        if (pre.get("excess_cand_d1") or 0) <= 0 and pre["n_signals"] >= LOW_SAMPLE:
            out.append("- ⚠️ **盘前批次**无超额 ⇒ 考虑盘前信号只入观察、不进卡（盘前 `flow_trend` 是盘后引擎残值，与当日资金脱钩）。")
    if pre and am and pre["n_signals"] >= 3 and am["n_signals"] >= 3:
        if (pre.get("pool_ret_d1") or 0) < (am.get("pool_ret_d1") or 0):
            out.append("- 盘前批次次日收益低于早盘批次 ⇒ 支持「盘前信号降级/静默」方向（待样本扩大复核）。")
    dull = res["dull_candidates"]
    if dull:
        names = "、".join(f"{d['theme']}×{d['days']}天" for d in dull[:5])
        out.append(f"- ⚠️ **钝化嫌疑题材**：{names} ⇒ 候选调整 = 同题材 N 个交易日内限频（当前指纹去重只作用在**当日**，跨日无闸）。")
    rep, fir = W.get("repeat_trigger"), W.get("first_trigger")
    if rep and fir and not rep.get("low_sample") and rep["n_signals"] >= LOW_SAMPLE:
        if (rep.get("excess_cand_d1") or 0) < (fir.get("excess_cand_d1") or 0):
            out.append("- 重复触发批次超额低于首次触发 ⇒ 支持上一条「跨日限频」。")
    if not out:
        out.append("- 样本或结构尚不足以给出可靠调整项，先累积账本。")
    out.append("- ⛔ 以上均为**候选调整项**，改 `live_signal.py` 必须用户拍板（本脚本不改任何代码/阈值）。")
    return out


# ────────────────────────── 主流程 ──────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="盘中回流信号回测（只读）")
    ap.add_argument("--days", type=int, default=None, help="只看最近 N 个交易日内的信号")
    ap.add_argument("--no-fetch", action="store_true", help="离线：只用已缓存日K")
    ap.add_argument("--json", action="store_true", help="只出 JSON 摘要")
    args = ap.parse_args()

    sigs = collect_signals()
    sigs = append_ledger(sigs)
    # 账本与重建结果并集（账本是权威累积）
    union: dict[tuple[str, str], dict] = {(s["signal_date"], s["theme"]): s for s in load_ledger()}
    for s in sigs:
        union.setdefault((s["signal_date"], s["theme"]), s)
    rows = sorted(union.values(), key=lambda x: (x["signal_date"], x.get("hhmm") or 0))

    if not rows:
        print("[reflow_backtest] 无回流信号可回测（pushed_signals_* 与账本均为空）", file=sys.stderr)
        return 0

    cal = kline_calendar(load_close_cache())
    if args.days and cal:
        keep = set(cal[-args.days:])
        rows = [r for r in rows if r["signal_date"].replace("-", "") in keep]
    if not rows:
        print(f"[reflow_backtest] 指定窗口内无信号（--days {args.days}）", file=sys.stderr)
        return 0

    zt = load_zt_codes()
    cand = load_cand_codes()
    for r in rows:
        r["_cand_codes"] = cand.get(_d8(r["signal_date"])) or []

    cache = load_close_cache()
    want = sorted({c for r in rows for c in (r.get("codes") or [])}
                  | {c for r in rows for c in (r.get("_cand_codes") or [])}
                  | {c for r in rows for c in zt.get(_d8(r["signal_date"]), set())})
    # ⚠️ 显式声明需要的日期（D / D±1）：否则「已入缓存但停在旧日期」的代码会被
    #    `fetch_bars` 判为已完成而永不刷新 → 次日口径静默变 None（decision_audit 同类缺陷）。
    _cal0 = kline_calendar(cache)
    _need: set[str] = set()
    for r in rows:
        d0 = str(r["signal_date"]).replace("-", "")
        _need.add(d0)
        for n in (-1, 1):
            nd = nth_next(_cal0, d0, n)
            if nd:
                _need.add(nd)
    cache, failed = fetch_bars(want, cache, allow_fetch=not args.no_fetch, need_dates=_need or None)
    if not args.no_fetch:
        save_close_cache(cache)
    cal = kline_calendar(cache)
    if failed:
        print(f"[reflow_backtest] ⚠️ 日K获取失败 {len(failed)} 只（相关口径记 None）", file=sys.stderr)

    for r in rows:
        attach_truth(r, cache, cal, zt)
        r.pop("_cand_codes", None)
    mark_repeat(rows)

    res = build_report(rows)
    OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    OUT_MD.write_text(render_md(res), encoding="utf-8")

    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != "signals"}, ensure_ascii=False, indent=1))
        return 0
    print(render_md(res))
    print(f"[reflow_backtest] 已写 {OUT_JSON} / {OUT_MD}｜账本 {LEDGER.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
