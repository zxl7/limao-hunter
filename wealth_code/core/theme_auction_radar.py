#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧭 板块竞价雷达（2026-11-12 用户「看见这边开一字了，要看这个题材里开得怎么样」）

## 它回答什么

不回答「买哪只」，只回答「**这条线今天竞价强不强、强在哪一段**」：
把昨日涨停票按**封板时间**分成「早封（≤10:00）/ 晚封」两组，分别看：

  · 红开率（gap > 0 占比）
  · 中位竞价涨幅
  · 一字只数
  · 覆盖只数（有多少只拿到竞价）

## 为什么要分层（2026-11-12 实测）

光通信 10 只全首板，整条线中位竞价只有 +1.49%，看着很普通；
但拆开看——**早封的 4 只全部 +7.5%~+10%**（剑桥 +7.82 / 青山 +10.06 /
华脉 +7.47 / 天洋 +8.26），**晚封的 6 只 -0.7%~+2%**。
⇒ 资金只认早封的，不是全板块普涨。用「中位数」会把这条线的真实结构抹平。

## 边界

- ⛔ 只作观察输出（写进 `pool_call.meta.theme_auction` / 卡面），**不参与任何买入判据**；
- ⛔ 不改阈值、不改名额、不改观察身份；
- 缺竞价数据的票不计入分母，但会记 `covered`（覆盖不足时 `level=unknown`）。
"""
from __future__ import annotations

from statistics import median
from typing import Any, Iterable, Mapping

SCHEMA = "theme_auction_radar_v1"
EARLY_FBT = "100000"          # ≤10:00 视为早封
MIN_COVERED = 2               # 少于 2 只有竞价 → 不判


def _gap(row: Mapping[str, Any], quotes: Mapping[str, Mapping[str, Any]]) -> float | None:
    q = quotes.get(str(row.get("code") or "")) or {}
    pc, ap = q.get("prev_close"), q.get("auction_price")
    try:
        pc_f, ap_f = float(pc), float(ap)
    except (TypeError, ValueError):
        return None
    if pc_f <= 0 or ap_f <= 0:
        return None
    return round((ap_f / pc_f - 1) * 100, 2)


def _stat(gaps: list[float]) -> dict[str, Any]:
    if not gaps:
        return {"n": 0, "red": None, "median": None, "one_word": 0}
    return {
        "n": len(gaps),
        "red": round(sum(1 for g in gaps if g > 0) / len(gaps), 3),
        "median": round(median(gaps), 2),
        "one_word": sum(1 for g in gaps if g >= 9.8),
    }


def build(zt_rows: Iterable[Mapping[str, Any]],
          quotes: Mapping[str, Mapping[str, Any]],
          theme_of) -> list[dict[str, Any]]:
    """返回每条线的竞价雷达（按「早封组强度」降序）。"""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for r in zt_rows or []:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        theme = str(theme_of(code, r) or "").strip()
        if not theme:
            continue
        groups.setdefault(theme, []).append(r)

    out: list[dict[str, Any]] = []
    for theme, arr in groups.items():
        early, late, gaps_all = [], [], []
        for r in arr:
            g = _gap(r, quotes)
            if g is None:
                continue
            gaps_all.append(g)
            fbt = str(r.get("fbt") or "").strip()[:6]
            (early if fbt and fbt <= EARLY_FBT else late).append(g)
        if len(gaps_all) < MIN_COVERED:
            continue
        e, l = _stat(early), _stat(late)
        level = "unknown"
        if e["n"] >= 2 and e["median"] is not None:
            if e["median"] >= 5 or e["one_word"] >= 1:
                level = "strong_early"
            elif e["median"] >= 2:
                level = "mild"
        if e["n"] >= 2 and l["n"] >= 2 and e["median"] is not None and l["median"] is not None:
            if e["median"] - l["median"] >= 4:
                level = "early_only"       # 资金只认早封
            elif e["median"] >= 4 and l["median"] >= 3:
                level = "broad"            # 全板块普涨
        out.append({
            "theme": theme, "members": len(arr), "covered": len(gaps_all),
            "early": e, "late": l, "level": level,
        })
    out.sort(key=lambda x: -(x["early"]["median"] if x["early"]["median"] is not None else -99))
    return out


def verdict(item: Mapping[str, Any]) -> str:
    """一句话判断（给卡面用）。"""
    e, l = item.get("early") or {}, item.get("late") or {}
    if item.get("level") == "broad":
        return f"全板块普涨（早封 {e.get('median')}% / 晚封 {l.get('median')}%）→ 真主线特征"
    if item.get("level") == "early_only":
        return (f"资金只认早封（早封 {e.get('median')}% vs 晚封 {l.get('median')}%）"
                "→ 方向有效但不普涨，只跟早封核心")
    if item.get("level") == "strong_early":
        return f"早封组强（{e.get('median')}%·一字 {e.get('one_word')} 只）→ 方向要留意"
    if item.get("level") == "mild":
        return f"早封组小幅高开（{e.get('median')}%）→ 观察"
    return "竞价无结论"
