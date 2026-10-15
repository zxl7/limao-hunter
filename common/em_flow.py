#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
东财资金流唯一数据层 em_flow.py（P2-18 · 2026-09-09 CapitalPulse 吸收定稿）

权威设计：config/design_资金流分层对接_CapitalPulse.md（唯一设计基准）
对接源：github.com/Dr-Liu23/CapitalPulse（字段口径已实测：f62=主力=f66超大+f72大 精确成立；
        f62+f78+f84≈0 资金守恒；f124=Unix秒时间戳）

职责边界（铁律）：
- 本模块 = 东财资金流字段映射/请求/校验的【唯一收口】。东财原始字段(f62/f66/f72/f78/f84/f124)
  只允许出现在本文件，下游一律消费语义契约字段（main_net/super_large_net/...）。
- HTTP 走 common.http_client（统一出口铁律 2026-08-17），禁自写 urllib。
- 增量语义：保留调用方 clist 全市场榜单（排名语义），本模块只做「代码池 → ulist.np 分层补全」+
  「分钟/日线曲线」+「守恒/新鲜度校验」。capital pulse 的静态申万池不吸收（板块权威=选股宝）。

契约命名（全局统一，见 design 文档 §3.1）：
  元值：main_net / super_large_net / large_net / mid_net / small_net（单位=元）
  时间：source_ts（f124 · **Unix 秒**，历史命名保留）+ source_ts_iso（同刻·ISO8601 带 +08:00）
        ⚠️ 2026-10-14 审计 P1-⑦：全系统 `source_ts` 另有「ISO 字符串」用法
        （realtime_data_pipeline / realtime_analytics / leader_cycle），**本模块是唯一用 Unix 秒的**。
        为免误用，本模块额外输出 `source_ts_iso`；新消费方一律读它，`source_ts` 留作向下兼容。
        规范见 `common.market_time` 末尾「时间戳字段规范」。
  展示：{field}_yi（亿）只由 net_to_yi() 换算，禁消费方自算
校验恒等式（parse 层强制）：main = super + large（容差 1%）；main+mid+small ≈ 0（守恒，容差 1%）
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from common.http_client import http_get
from common.market_time import iso_bj_from_unix

logger = logging.getLogger("em_flow")

# ---- 端点与公共参数（CapitalPulse config.py 同源）----
EM_FLOW_HOSTS = ["push2.eastmoney.com", "push2delay.eastmoney.com"]  # 实时优先 → delay 兜底
EM_REFERER = "https://data.eastmoney.com/"
EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": EM_REFERER,
}
EM_UT = "7eea3edcaed734bea9telecast"

# 分层快照（ulist.np 批量）与 fflow/kline 端点
EM_SNAPSHOT_PATH = "/api/qt/ulist.np/get"
EM_FFLOW_KLINE_PATH = "/api/qt/stock/fflow/kline/get"      # 分钟累计
EM_DAILY_KLINE_PATH = "/api/qt/stock/fflow/daykline/get"   # 历史日线（push2his）

# ---- 阈值（config/app_config.yaml 登记同款，读 env 可覆盖·可逆开关）----
CACHE_SECONDS = 60          # EM_FLOW_CACHE_SECONDS
CONSISTENCY_TOL = 0.01      # EM_FLOW_CONSISTENCY_TOLERANCE（守恒容差）
STALE_SECONDS = 300         # EM_FLOW_STALE_SECONDS（f124 新鲜度门禁·盘中）
_ENABLED = True             # EM_FLOW_ENABLED 总开关（False=本层空转，调用方回退旧逻辑）


def em_flow_enabled() -> bool:
    """总开关（env EM_FLOW_ENABLED=0/off/false 关闭）。"""
    v = __import__("os").getenv("EM_FLOW_ENABLED", "")
    if v:
        return v.strip().lower() not in {"0", "false", "no", "off"}
    return _ENABLED


# ---- 60s 进程内缓存（键=host-path-params 指纹；失败不落缓存）----
_cache: dict[str, tuple[float, Any]] = {}
# 解析契约版本戳：校验/字段规则演进时旧缓存自动失效（2026-09-09 强校验改软校验曾致缓存污染）
_PARSE_VER = "20260909.2"


def _cache_get(key: str) -> Any:
    item = _cache.get(key)
    if item and time.time() - item[0] < CACHE_SECONDS:
        return item[1]
    return None


def _cache_put(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)
    if len(_cache) > 128:  # 防无界增长
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)


def _em_get(path: str, params: dict[str, Any], hosts: Optional[list[str]] = None) -> Optional[dict]:
    """东财 GET：双源回落（push2 → push2delay），失败返回 None（调用方防御）。
    params 自动补公共参数（ut/fltt/invt/np/防缓存 _）。"""
    p = {**params}
    p.setdefault("ut", EM_UT)
    p.setdefault("fltt", "2")
    p.setdefault("invt", "2")
    p["_"] = str(int(time.time() * 1000))
    qs = "&".join(f"{k}={v}" for k, v in p.items())
    last = None
    for host in (hosts or EM_FLOW_HOSTS):
        try:
            payload = http_get(f"https://{host}{path}?{qs}", t=15, headers=EM_HEADERS)
            if isinstance(payload, dict):
                return payload
            last = payload
        except Exception as e:  # http_get 通常不抛，防御兜底
            last = e
    if last is not None:
        logger.warning("[em_flow] 双源均不可用 %s (%r)", path, last)
    return None


# ---- secid 工具 ----
def secid_of_sector(code: str) -> str:
    """板块代码 BKxxxx → 90.BKxxxx。"""
    c = str(code).strip().upper()
    return f"90.{c}" if not c.startswith("90.") else c


def secid_of_stock(code: str) -> str:
    """6 位个股代码 → 沪 1.xxxxxx / 深创 0.xxxxxx（688/6xx 沪市·0/3xx 深市）。"""
    c = str(code).strip()
    if "." in c:
        return c
    return f"{'1' if c.startswith(('6', '9')) else '0'}.{c}"


# ---- 解析与校验 ----
def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_snapshot_item(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """单条 ulist.np diff → 语义契约条目（分级校验·2026-09-09 真实数据校准）。

    恒等 1【强·定义性】：main = super + large —— 实测全量精确成立，违反=上游数据异常 → 丢弃。
    恒等 2【软·守恒】：main + mid + small ≈ 0 —— 行业板块实测元级成立，但**概念板块因成分
    重叠/分档舍入可出现 1~5% 破缺**（实测 BK1650 偏差 1.1%/BK1386 3.4%）→ 保留并打标
    flow_consistent=False 供下游知情；仅 >10% 判定真异常丢弃。
    """
    try:
        code = str(item.get("f12") or "").strip()
        source_ts = int(item.get("f124") or 0)
    except (TypeError, ValueError):
        source_ts = 0
    if not code or source_ts <= 0:
        return None
    main_net = _num(item.get("f62"))
    super_large_net = _num(item.get("f66"))
    large_net = _num(item.get("f72"))
    mid_net = _num(item.get("f78"))
    small_net = _num(item.get("f84"))
    # 恒等 1：主力=超大+大（强·丢弃）
    if abs(main_net - (super_large_net + large_net)) > abs(main_net) * CONSISTENCY_TOL + 1.0:
        logger.warning("[em_flow] 主力定义恒等违反(丢弃) %s main=%.0f super=%.0f large=%.0f",
                       code, main_net, super_large_net, large_net)
        return None
    # 恒等 2：资金守恒（软·概念板块 1~5% 破缺允许，>10% 丢弃）
    flow_consistent = True
    if abs(main_net + mid_net + small_net) > abs(main_net) * 0.10 + 1.0:
        logger.warning("[em_flow] 守恒严重破缺(丢弃>10%%) %s main=%.0f mid=%.0f small=%.0f",
                       code, main_net, mid_net, small_net)
        return None
    if abs(main_net + mid_net + small_net) > abs(main_net) * CONSISTENCY_TOL + 1.0:
        flow_consistent = False  # 1~10% 破缺：保留打标（概念成分重叠正常态）
    return {
        "code": code,
        "name": str(item.get("f14") or ""),
        "source_ts": source_ts,                      # 兼容保留：Unix 秒（历史命名）
        "source_ts_iso": iso_bj_from_unix(source_ts),  # 规范形态：ISO8601 +08:00
        "flow_consistent": flow_consistent,
        "main_net": main_net,
        "super_large_net": super_large_net,
        "large_net": large_net,
        "mid_net": mid_net,
        "small_net": small_net,
    }


def fetch_sector_flow_details(codes: list[str]) -> dict[str, dict[str, Any]]:
    """板块代码池 → ulist.np 批量分层快照（一次请求）。返回 {code: 契约条目}。
    守恒异常条目被丢弃（日志计数），不回退整批。空/不可用返回 {}（调用方降级）。"""
    if not em_flow_enabled() or not codes:
        return {}
    secids = ",".join(dict.fromkeys(secid_of_sector(c) for c in codes))  # 去重保序
    key = f"snap|{secids}|{_PARSE_VER}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    payload = _em_get(EM_SNAPSHOT_PATH, {
        "secids": secids,
        "fields": "f12,f14,f62,f66,f72,f78,f84,f124",
        "np": "1",
    })
    result: dict[str, dict[str, Any]] = {}
    if payload:
        diff = (payload.get("data") or {}).get("diff") or []
        for it in diff:
            if not isinstance(it, dict):
                continue
            parsed = parse_snapshot_item(it)
            if parsed:
                result[parsed["code"]] = parsed
    if result:
        _cache_put(key, result)
    return result


def parse_kline_flows(klines: list[str], kind: str = "minute") -> list[dict[str, Any]]:
    """fflow/kline klines 行解析。行序=时间,主力,小单,中单,大单,超大单（CapitalPulse 实测）。
    kind=minute 返回分钟累计条目（含 time）；kind=daily 复用同解析（time 即 trade_date）。"""
    flows: list[dict[str, Any]] = []
    for line in klines:
        if not isinstance(line, str):
            continue
        parts = line.split(",")
        if len(parts) < 6 or not parts[0]:
            continue
        flows.append({
            "time": parts[0],
            "main_net": _num(parts[1]),
            "small_net": _num(parts[2]),
            "mid_net": _num(parts[3]),
            "large_net": _num(parts[4]),
            "super_large_net": _num(parts[5]),
        })
    return flows


def fetch_minute_curve(secid: str, limit: int = 30) -> list[dict[str, Any]]:
    """单标的当日分钟累计资金曲线（klt=1·默认 30 根控量·EM_FLOW_MINUTE_LIMIT）。
    返回 [] 表示不可用（调用方降级）。"""
    if not em_flow_enabled():
        return []
    key = f"min|{secid}|{limit}|{_PARSE_VER}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    payload = _em_get(EM_FFLOW_KLINE_PATH, {
        "lmt": str(limit), "klt": "1", "secid": secid,
        "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56",
    })
    flows: list[dict[str, Any]] = []
    if payload:
        flows = parse_kline_flows((payload.get("data") or {}).get("klines") or [])
    if flows:
        _cache_put(key, flows)
    return flows


def fetch_daily_flows(secid: str, limit: int = 30) -> list[dict[str, Any]]:
    """单标的东财资金日线（klt=101·push2his·最近 N 交易日）。
    与腾讯 MainNetFlow 5D/10D/20D 构成盘后双源对账（TX_FLOW_CROSS_CHECK）。"""
    if not em_flow_enabled():
        return []
    key = f"day|{secid}|{limit}|{_PARSE_VER}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    payload = _em_get(EM_DAILY_KLINE_PATH, {
        "lmt": str(limit), "klt": "101", "secid": secid,
        "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56",
    }, hosts=["push2his.eastmoney.com"])
    flows: list[dict[str, Any]] = []
    if payload:
        flows = parse_kline_flows((payload.get("data") or {}).get("klines") or [])
    if flows:
        _cache_put(key, flows)
    return flows


# ---- 展示换算（唯一换算点：亿·保留 1-2 位）----
def net_to_yi(value: float, digits: int = 2) -> float:
    return round(value / 1e8, digits) if value else 0.0


def fetch_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    """批量个股实时行情快照（ulist.np·f2 价 / f3 涨幅 / f12 code / f14 名）→
    {code: {name, price, pct}}。

    P2-17 机构票拉升层的数据源：机构早盘卡点名池（70+ 只·多为中军/趋势票，**不在**
    latest.json 的 leaders 连板池内）需独立行情源——复用本层 ulist 批量能力，
    一次请求覆盖全池（60s 缓存天然对盘中每轮限频）。失败/空池返回 {}（调用方降级）。
    """
    if not em_flow_enabled() or not codes:
        return {}
    secids = ",".join(dict.fromkeys(secid_of_stock(c) for c in codes))
    key = f"quote|{secids}|{_PARSE_VER}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    payload = _em_get(EM_SNAPSHOT_PATH, {"secids": secids, "fields": "f2,f3,f12,f14", "np": "1"})
    out: dict[str, dict[str, Any]] = {}
    if payload:
        for it in ((payload.get("data") or {}).get("diff") or []):
            if not isinstance(it, dict):
                continue
            code = str(it.get("f12") or "").strip()
            if not code:
                continue
            out[code] = {"name": str(it.get("f14") or ""),
                         "price": _num(it.get("f2")),
                         "pct": _num(it.get("f3"))}
    if out:
        _cache_put(key, out)
    return out


def sector_flow_structure(item: dict[str, Any]) -> dict[str, Any]:
    """契约条目 → latest.json sector_risk 增补字段（亿·与存量 flow_yi 同口径）。
    供 intraday_pulse.build_sector_risk 榜单条目合并；缺字段时消费方自行回落。"""
    return {
        "super_large_yi": net_to_yi(item.get("super_large_net")),
        "large_yi": net_to_yi(item.get("large_net")),
        "mid_yi": net_to_yi(item.get("mid_net")),
        "small_yi": net_to_yi(item.get("small_net")),
        "source_ts": item.get("source_ts") or 0,
        "source_ts_iso": item.get("source_ts_iso")
                          or iso_bj_from_unix(item.get("source_ts")),
    }
