#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""放量倍数（今日量 ÷ 昨日量）· 卡面「占昨天的百分比」（2026-10-21 用户要求）。

## 为什么要单独一个模块

用户原话：「放量多少、**占昨天的百分比**，这些指标全部打上去」。

这个数不在任何已有产物里：
- `candidates-*.json` 只有**今日**成交额 `cje_yi` / 换手 `hs`，没有昨日量；
- `zt_history/zt_{date}.json` 只收**当日涨停票**，首板票昨天不在池里 → 取不到；
- `cache/zt_genes/kl_hist_*.json` 的日 K **只有 OHLC，没有 volume**（抓取时丢了）。

所以只能现抓。⛔ 但**只给买入票抓**（每天通常 1 只）——观察/放弃票不配花这个网络时间。

## 口径（⛔ 必须说清，避免误读）

`倍数 = 今日成交量 ÷ **上一交易日**成交量`
- 用**成交量**（股数）不用成交额：成交额会被价格波动污染（涨停日价格高，额自然大）。
- "上一交易日"由 K 线序列决定（跳过周末/假期），⛔ 不按自然日减 1，也不 `weekday()`。
- 倍数 **>1 = 放量**，**<1 = 缩量**；卡面统一写"较昨放量 N.NN 倍 / 较昨缩量 N.NN 倍"。

## 降级（⛔ 缺数据不猜）

K 线取不到、不足 2 根、或昨日 volume 非正 → 返回 `None`，**卡面不渲染这一项**。
⛔ 不许写成「0 倍」「—」——缺数据不是"没放量"。
"""
from __future__ import annotations

from typing import Any

# 缓存：同一进程内同一只票只抓一次（一张卡渲染会多次调 `_buy_indicator_lines`）
_CACHE: dict[str, dict[str, Any] | None] = {}

# 测试注入：`set_bars_hook(fn)` 后不再走网络（fn(code, n) → bars）
_BARS_HOOK: Any = None

# ⛔ 数据新鲜度阈值（自然日）：K 线最后一根离「候选交易日」超过这么多天 → 判为陈旧。
#    取 10 天是为了容忍国庆/春节长假；⛔ 不能只判"是否等于今天"——09:25 推送时
#    今日日 K 往往还没生成，最后一根是**昨天**，那是正常的。
STALE_DAYS = 10


def set_bars_hook(fn: Any) -> None:
    """测试用：注入假 K 线源，绕过网络。传 None 恢复正常。"""
    global _BARS_HOOK
    _BARS_HOOK = fn


def _parse_date(s: str) -> Any:
    """`2026-10-20` / `20261020` → date；解析失败 → None。"""
    from datetime import date as _d
    t = str(s or "").strip().replace("-", "")
    if len(t) != 8 or not t.isdigit():
        return None
    try:
        return _d(int(t[:4]), int(t[4:6]), int(t[6:]))
    except ValueError:
        return None


def _fresh(latest: str, asof: str) -> bool:
    """K 线最后一根是否够新。⛔ `asof` 缺失时**不校验**（不知道参照就不下结论）。"""
    if not asof:
        return True
    d_latest, d_asof = _parse_date(latest), _parse_date(asof)
    if not d_latest or not d_asof:
        return True                      # 日期解析不了 → 不因此否掉真实数据
    gap = (d_asof - d_latest).days
    # gap < 0 = K 线比候选日还新（盘后重渲时正常）；gap 过大 = 停牌/退市/接口返回旧数据
    return gap <= STALE_DAYS


def vol_ratio(code: str, *, bars: int = 6, asof: str = "") -> dict[str, Any] | None:
    """返回 `{"ratio": 1.16, "today": 92375033, "prev": 79694687, "prev_date": "2026-10-20"}`；
    取不到 → `None`（⛔ 不编造）。

    `bars=6` 多取几根：停牌/新股上市初期可能存在缺口，多取能保证"上一根"真实存在。
    `asof` = 候选交易日（YYYY-MM-DD）。用于**新鲜度校验**：
      ⛔ 实测踩坑：`600001`（邯郸钢铁·2009 年已退市）接口仍返回 2009-12-15 的 K 线，
        算出来「较昨放量 1.00 倍」——十几年前的量，挂在一个今天要买的票上是**假指标**。
      故最后一根离 asof 超过 `STALE_DAYS` 天 → 判为陈旧，返回 None。
      ⛔ 不传 asof 时不校验（不知道参照就不下结论）。
    """
    c = str(code or "").strip()
    if not c:
        return None
    if c in _CACHE:
        return _CACHE[c]
    out: dict[str, Any] | None = None
    try:
        if _BARS_HOOK is not None:
            rows = _BARS_HOOK(c, bars) or []
        else:
            from common.tencent_kline import fetch_kline_ohlc
            # ⛔ timeout 收短到 5s：这张卡 09:25 要推，一个网络慢请求不能拖住整次推送。
            #    宁可这一项不显示（降级），也不能让买入票晚到。
            rows = fetch_kline_ohlc(c, n=bars, timeout=5) or []
        # ⛔ 只保留 volume 有效的行；close-only 行没量，参与比较会算出假倍数
        valid = [r for r in rows
                 if isinstance(r.get("volume"), (int, float)) and float(r["volume"]) > 0]
        # ⛔ 先校验新鲜度：陈旧数据算出来的倍数一律不用（见 docstring 里的 600001 实证）
        _latest = str((valid[-1] or {}).get("date") or "") if valid else ""
        if len(valid) >= 2 and _fresh(_latest, asof):
            today = valid[-1]
            prev = valid[-2]
            pv = float(prev["volume"])
            if pv > 0:
                out = {"ratio": round(float(today["volume"]) / pv, 3),
                       "today": float(today["volume"]),
                       "prev": pv,
                       "prev_date": str(prev.get("date") or ""),
                       "today_date": str(today.get("date") or "")}
    except Exception:
        out = None                      # 网络/字段异常 → 静默降级（卡面不显示该项）
    _CACHE[c] = out
    return out


def vol_text(code: str, *, asof: str = "") -> str:
    """卡面用的一句话：`较昨放量 1.16 倍` / `较昨缩量 0.72 倍` / `""`（缺数据）。"""
    r = vol_ratio(code, asof=asof)
    if not r:
        return ""
    ratio = float(r.get("ratio") or 0)
    if ratio <= 0:
        return ""
    if ratio >= 1:
        return f"较昨放量 {ratio:.2f} 倍"
    return f"较昨缩量 {ratio:.2f} 倍"


def reset_cache() -> None:
    """测试用：清空进程内缓存与注入源。"""
    global _BARS_HOOK
    _CACHE.clear()
    _BARS_HOOK = None
