#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔒 竞价推票 → 持有 → 断板退出（2026-11-12 用户定）

用户原话：
「在竞价阶段筛选连板晋级率较高的候选票，竞价推票同样优先推荐并买入晋级率
排序靠前的股票；买入后持续持有并跟踪，期间不提前退出，直到出现断板
（连板终止）信号时再结束持有并移出推荐。」

## 规则（只有这几条，⛔ 不加别的）
1. **选票**：晋级率 ≥40%（`promotion_odds.LIKELY`）且板位 1/2；按晋级率排序；
2. **优先级**：晋级率 desc → 竞价涨幅 desc → 一进二优先于二进三（同率）→ 名称；
   取前 N（默认 2，与名额一致）；
3. **买入**：09:25 竞价价成交（**系统只登记，人工下单**）；
4. **持有**：买入后**不提前退出**——不看盘中波动、不看浮盈浮亏、不设止损；
5. **断板退出**：**当日收盘不在涨停池** → 次日移出推荐并结束持有。
   ⛔ 断板只有一个定义（§4 铁律）：收盘没涨停，不是竞价没开高。

## 输出字段（用户要求）
股票名称/代码、晋级率、推荐优先级、持有状态、断板退出提示。
"""
from __future__ import annotations

from typing import Any

MAX_OPEN = 2              # 同时持有上限（与 09:25 名额 2 一致）
LIKELY_MIN = 40.0         # 晋级率门槛（≥40% 才算"大概率"）


def rank_candidates(rows: list[dict]) -> list[dict]:
    """按晋级率排优先级；只留 ≥40% 的 1/2 板。"""
    cand = [r for r in rows
            if int(r.get("boards") or 0) in (1, 2)
            and float(r.get("pct") or 0) >= LIKELY_MIN]
    cand.sort(key=lambda r: (-float(r.get("pct") or 0),
                             -float(r.get("gap") if r.get("gap") is not None else -99),
                             int(r.get("boards") or 9),
                             str(r.get("name") or "")))
    for i, r in enumerate(cand, 1):
        r["priority"] = f"P{i}"
    return cand


def open_positions(holdings: dict, rows: list[dict], quotes: dict,
                   date: str, *, max_open: int = MAX_OPEN) -> tuple[dict, list[dict]]:
    """09:26 登记买入：优先级前 N，跳过已持有。"""
    opened_at = {(h.get("code")) for h in holdings.get("open") or []}
    slots = max(0, max_open - len(opened_at))
    opened: list[dict] = []
    for r in rank_candidates(rows):
        if slots <= 0:
            break
        code = str(r.get("code"))
        if code in opened_at:
            continue
        q = quotes.get(code) or {}
        px = q.get("auction_price")
        if not px:
            continue                       # 没成交价不登记（不猜）
        holdings.setdefault("open", []).append({
            "code": code, "name": r.get("name"), "theme": r.get("theme"),
            "entry_date": date, "entry_price": round(float(px), 3),
            "entry_pct": r.get("pct"), "priority": r.get("priority"),
            "boards_at_entry": int(r.get("boards") or 1),
            "status": "持有中",
        })
        opened.append(holdings["open"][-1])
        opened_at.add(code)
        slots -= 1
    return holdings, opened


def signal(holdings: dict, zt_rows: list[dict], date: str) -> tuple[dict, list[dict]]:
    """收盘**发出**断板信号：当日不在涨停池 → 标记「待退出」。

    ⛔ T+1 铁律（2026-11-12 用户「T+1 断板也只有第二天才可以出」）：
    买入当日不能卖，所以这里**只记信号**，真正卖出在下一交易日开盘（见 `execute`）。
    """
    zt = {str(r.get("code")) for r in zt_rows if r.get("code")}
    boards = {str(r.get("code")): int(r.get("boards") or 0) for r in zt_rows if r.get("code")}
    signaled: list[dict] = []
    for h in holdings.get("open") or []:
        code = str(h.get("code"))
        if code in zt:
            h["boards_now"] = boards.get(code) or h.get("boards_at_entry")
            h["last_check"] = date
            continue
        if h.get("exit_signal_date") and h.get("exit_signal_date") <= date:
            continue
        h["exit_signal_date"] = date
        h["status"] = "待退出（T+1）"
        h["exit_hint"] = (f"⚠️ {h.get('name')}({code}) {date} 断板 → "
                          f"下一交易日开盘卖出（T+1，当日不可卖）")
        signaled.append(h)
    return holdings, signaled


def execute(holdings: dict, date: str, opens: dict[str, float]) -> tuple[dict, list[dict]]:
    """下一交易日开盘执行卖出（只处理「待退出」）。"""
    closed: list[dict] = []
    keep: list[dict] = []
    for h in holdings.get("open") or []:
        if h.get("status") != "待退出（T+1）":
            keep.append(h)
            continue
        # ⛔ 信号当天不执行：必须等下一交易日（T+1）
        if str(h.get("exit_signal_date") or "") >= date:
            keep.append(h)
            continue
        code = str(h.get("code"))
        px = opens.get(code)
        h["status"] = "已退出"
        h["exit_date"] = date
        h["exit_reason"] = f"断板（{h.get('exit_signal_date')} 收盘未涨停）"
        if px:
            h["exit_price"] = round(float(px), 3)
            try:
                h["ret_pct"] = round((float(px) / float(h["entry_price"]) - 1) * 100, 2)
            except Exception:  # noqa: BLE001
                pass
        h["exit_hint"] = (f"⚠️ {h.get('name')}({code}) 断板 → 已按 T+1 于 {date} 开盘卖出"
                          + (f"（{h.get('ret_pct'):+.2f}%）" if h.get("ret_pct") is not None else ""))
        holdings.setdefault("closed", []).append(h)
        closed.append(h)
    holdings["open"] = keep
    return holdings, closed


def settle(holdings: dict, zt_rows: list[dict], date: str,
           closes: dict[str, float] | None = None) -> tuple[dict, list[dict]]:
    """兼容旧调用：收盘发信号 + 记录退出价（不卖）。"""
    return signal(holdings, zt_rows, date)


def report(holdings: dict) -> list[dict]:
    """用户要求的五列。"""
    out = []
    for h in (holdings.get("open") or []):
        out.append({
            "代码": h.get("code"), "名称": h.get("name"),
            "晋级率": h.get("entry_pct"), "推荐优先级": h.get("priority"),
            "持有状态": h.get("status", "持有中"),
            "断板退出提示": (h.get("exit_hint") if h.get("status") != "持有中"
                             else "—（仍在板内，继续持有）"),
        })
    for h in (holdings.get("closed") or [])[-10:]:
        out.append({
            "代码": h.get("code"), "名称": h.get("name"),
            "晋级率": h.get("entry_pct"), "推荐优先级": h.get("priority"),
            "持有状态": "已退出",
            "断板退出提示": h.get("exit_hint") or f"断板退出 @{h.get('exit_date')}",
        })
    return out
