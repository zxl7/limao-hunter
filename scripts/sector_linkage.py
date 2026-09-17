# -*- coding: utf-8 -*-
"""跨板块联动检测内核（2026-09-17 A2）—— 纯函数、可单测、零副作用。

## 口径（用户 2026-09-17 拍板「保持」）
**同刻 ≥ 2 个板块，各 ≥ 3 只封板** = 联动爆发。
常量刻意与前端 `狸猫终端/src/features/analytics/lib/timeline.ts` 的
`LINKAGE_MIN_SECTORS` / `BURST_MIN_STOCKS` **同值**，避免前后端口径漂移。

## ⛔⛔ 本模块**尚未接入** `live_signal.py`
用户明确「保证盘中异动那边高频不要变化」。故本模块只提供**已实现、已测试的内核**；
接入（命中轮出「深色联动卡」）须：① 收盘后；② 默认关的开关；③ 次日盘中验证。

## 为什么必须抽成纯函数
联动判定错了**不报错**：只是把「多板块同时爆发」画成普通卡（漏掉爆发），
是典型的**静默错值**，只有不变量测试能发现。
"""
from collections import defaultdict

# ⚠️ 与前端 timeline.ts 同值（改一处必须同步另一处）
LINKAGE_MIN_SECTORS = 2
BURST_MIN_STOCKS = 3


def fbt_min(v) -> int | None:
    """`'092500'`（首封时刻）→ 自 00:00 起算的分钟；非法返回 None。

    与前端 `timeline.ts::fbtMin` 同逻辑（取前 4 位 HHMM）。
    """
    if not v:
        return None
    d = "".join(ch for ch in str(v) if ch.isdigit())
    if len(d) < 4:
        return None
    h, m = int(d[:2]), int(d[2:4])
    if h > 23 or m > 59:
        return None
    return h * 60 + m


def min_to_clock(v: int) -> str:
    return f"{v // 60:02d}:{v % 60:02d}"


def detect_linkage(rows, *, min_sectors: int = LINKAGE_MIN_SECTORS,
                   min_stocks: int = BURST_MIN_STOCKS) -> list:
    """按分钟聚合，判定跨板块联动。

    :param rows: `[{"code","name","sector","fbt"}]`（`fbt` 缺省/非法的行被忽略）
    :return: `[{"at_min", "at", "sector_count", "sectors":[{"sector","count","stocks"}]}]`
             —— 按时刻升序；**无联动返回 `[]`**。

    判定：同一分钟内，**达到 `min_stocks` 只封板的板块数 ≥ `min_sectors`**。
    ⛔ 同一板块跨分钟累计**不算**联动（必须**同刻**）。
    """
    by_min_sector: dict = defaultdict(lambda: defaultdict(list))
    for r in rows or []:
        t = fbt_min(r.get("fbt"))
        if t is None:
            continue
        sector = (r.get("sector") or "").strip() or "未分类"
        by_min_sector[t][sector].append(r)

    out = []
    for t in sorted(by_min_sector):
        hit = [
            {"sector": sec, "count": len(stks), "stocks": stks}
            for sec, stks in sorted(by_min_sector[t].items())
            if len(stks) >= min_stocks
        ]
        if len(hit) >= min_sectors:
            out.append({"at_min": t, "at": min_to_clock(t),
                        "sector_count": len(hit), "sectors": hit})
    return out


def summarize(linkages) -> str:
    """给卡片标题用的一句话摘要（无联动返回空串）。"""
    if not linkages:
        return ""
    lk = linkages[0]
    names = " / ".join(s["sector"] for s in lk["sectors"])
    return f"🚀 板块联动 {lk['sector_count']} 个（{lk['at']}）：{names}"
