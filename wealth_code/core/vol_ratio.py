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
    _MA5_CACHE.clear()
    _BARS_HOOK = None


# ══════════════════════════════════════════════════════════════════════════
# 📊 5 日均量比（2026-11-04 用户定）
# ══════════════════════════════════════════════════════════════════════════
# 用户原话：「量能判断要基于 5 日平均判断。首板你可以缩量，但是二板要稍微放一点量，
#   除非你是 1 字，对吧？」
#
# ## 为什么要它
#
# `vol_ratio` 只看「今日 ÷ 昨日」，一根异常日 K 就能把结论带偏（昨日缩量一半 →
# 今天看着像爆量）。**5 日均量是更稳的基准**：同样「放量 1.15 倍」，
# 相对 5 日平台比相对单日更有说服力。
#
# ## 实测结论（33 归档日·1773 个可算样本·源 `cache/zt_history` × `cache/kline_qfq`）
#
# ⚠️ **本函数只做诊断，不参与任何硬门槛** —— 数据不支持把它当闸：
#   二板换手板·可成交口径（n=51）三种方案对比：
#   ┌──────────────────────┬──────┬────────┬──────────┐
#   │ 方案                  │ n    │ 次日开收 │ 胜率     │
#   ├──────────────────────┼──────┼────────┼──────────┤
#   │ 全放行（现状）         │ 51   │ −1.51% │ 37.3%    │
#   │ 仅绝对额 ≥5 亿         │ 35   │ −0.35% │ **45.7%**│
#   │ 仅量比 ≥1.0x          │ 45   │ −1.27% │ 37.8%    │
#   │ ≥5 亿 AND ≥1.0x      │ 31   │ −0.10% │ 45.2%    │
#   └──────────────────────┴──────┴────────┴──────────┘
#   ⇒ **量比几乎不提升（37.3%→37.8%），绝对额才是主判据**。
#      量比叠加到绝对额上反而略降（45.7%→45.2%）。
#
#   但用户的直觉在**两处是对的**：
#   ① **首板可以缩量**：首板换手板可成交口径 n=580，缩量<1.0x 胜率 40.2%
#      vs 放量≥1.0x 41.8% —— 无实质差别，缩量不是首板的硬伤。
#   ② **二板缩量确实最差**：全样本二板换手板缩量<0.8x（n=14）胜率 **21.4%**、
#      开收 **−3.16%**；缩量<1.0x（n=19）胜率 26.3%，均低于放量组 36.7%。
#   ⇒ 结论：**缩量对二板是风险信号，但「不缩量」本身不足以构成买入理由**。
#      用于解释与复盘（为什么这只二板不能接），不用于独立否决。
#
# ## 口径
# `ratio = 当日成交量 ÷ 前 N 日（不含当日）均量`
# ⛔ 用**成交量（股数）**不用成交额：涨停日价格高，成交额天然虚高。
# ⛔ 前 N 日不足 N 根 / 当日非正 / 日期对不上 → 返回 None（缺数据不猜）。

MA5_DAYS = 5
_MA5_CACHE: dict[tuple[str, str, int], dict[str, Any] | None] = {}


def _local_bars(code: str) -> dict[str, dict[str, Any]]:
    """读本地前复权日 K（`cache/kline_qfq/`）。⛔ 缺失返回空 dict，不建目录不联网。"""
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "cache" / "kline_qfq" / f"{code}.json"
    try:
        import json
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        out: dict[str, dict[str, Any]] = {}
        for b in data.get("bars") or []:
            d = str(b.get("date") or "")[:10]
            v = b.get("vol", b.get("volume"))
            if d and isinstance(v, (int, float)) and float(v) > 0:
                out[d] = {"vol": float(v)}
        return out
    except Exception:
        return {}


def vol_ma5(code: str, asof: str, *, days: int = MA5_DAYS) -> dict[str, Any] | None:
    """当日量 ÷ 前 `days` 日均量。返回 `{"ratio", "today", "ma5", "today_date", "window"}`。

    `asof` = 涨停当日（YYYY-MM-DD），必须是本地 K 线里存在的那一根；
    ⛔ 对不上就返回 None —— 宁可没有这一项，也不拿邻近日期冒充。
    """
    c, a = str(code or "").strip(), str(asof or "").strip()[:10]
    if not c or len(a) != 10:
        return None
    hit = (c, a, days)
    if hit in _MA5_CACHE:
        return _MA5_CACHE[hit]
    out: dict[str, Any] | None = None
    bars = _local_bars(c)
    if bars and a in bars:
        ds = sorted(bars)
        i = ds.index(a)
        prev = [bars[d]["vol"] for d in ds[max(0, i - days):i]]
        if len(prev) == days:                       # ⛔ 不足 N 根不算（不拿 2 天当 5 天）
            ma = sum(prev) / days
            cur = bars[a]["vol"]
            if ma > 0 and cur > 0:
                out = {"ratio": round(cur / ma, 3), "today": cur, "ma5": round(ma, 1),
                       "today_date": a, "window": days}
    _MA5_CACHE[hit] = out
    return out


def vol_ma5_text(code: str, asof: str, *, days: int = MA5_DAYS) -> str:
    """诊断一句话：`较5日均量放量 1.15 倍` / `…缩量 0.72 倍` / `""`（缺数据）。"""
    r = vol_ma5(code, asof, days=days)
    if not r:
        return ""
    ratio = float(r.get("ratio") or 0)
    if ratio <= 0:
        return ""
    verb = "放量" if ratio >= 1 else "缩量"
    return f"较{days}日均量{verb} {ratio:.2f} 倍"
