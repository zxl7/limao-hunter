#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
必盈（biyingapi）市场池适配 —— spec §3 主候选全集 + 竞价 + 历史。

职责：把必盈原始返回适配成财富密码算法层标准结构（与 adapters/input_zt.py
选股宝适配同构），实现 spec 「两者相结合」的必盈主池位。

字段映射假设（必盈 ztgc 典型字段，与选股宝 yesterday_zt.json 同构；
缺失字段优雅降级为 0/None，绝不编造）：
  dm=代码 mc=名称 zf=涨幅(%) lbc=连板数 fbt=首封时间 zbc=开板次数
  hs=换手率(%) hy=行业 cje=成交额(元) lt=流通市值(元) zsz=总市值(元)
  zj=封单额(元) price=现价

数据源切换（保守默认）：
- 默认用选股宝 yesterday_zt.json（真实、稳定）。
- 设 WEALTH_USE_BIYING=1 且 BIYING_TOKEN 可用时，改用必盈 ztgc 主池，缺失自动降级选股宝。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .biying_client import build_client

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CACHE_DIR = ROOT / "cache"


def _is_st(name: str, code: str) -> bool:
    return "ST" in str(name or "").upper() or str(code or "").startswith(("4", "8", "9"))


def _to_float(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return d


def _to_int(v: Any, d: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return d


def fetch_ztgc(date: str) -> dict[str, Any]:
    """
    拉取必盈当日涨停池（spec §3.1 pool_name=ztgc），适配为标准结构。

    返回与 input_zt.load_zt_pool 同构的 dict：
      {"date", "ztgc":[{dm,mc,zf,lbc,fbt,zbc,hs,hy,is_20cm,zj,price,lt,zsz,cje,is_st}],
       "code2themes", "hot_sectors", "raw"}
    失败（token 缺失/网络/404）时抛异常，由调用方捕获降级选股宝。
    """
    client = build_client()
    raw = client.api(f"hslt/ztgc/{date}", quiet_404=True)
    if not raw:
        raise RuntimeError(f"必盈 ztgc 返回空（date={date}）")
    # 兼容必盈可能的外层包装 {data: [...]} 或直接 [...]
    rows = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        rows = []

    ztgc: list[dict[str, Any]] = []
    code2themes: dict[str, list[str]] = {}
    sector_counter: dict[str, int] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        code = str(item.get("dm") or item.get("code") or "").strip()
        name = str(item.get("mc") or item.get("name") or "").strip()
        if not code or not name:
            continue
        sector = str(item.get("hy") or "").strip() or "其他"
        # 20cm 判定：创业板/科创板且涨幅≥19%（biying 通常直接给 is_20cm/zf）
        is_20cm = bool(item.get("is_20cm")) or (str(code).startswith(("30", "688")) and _to_float(item.get("zf")) >= 19)
        cje_yuan = _to_float(item.get("cje"))
        lt_yuan = _to_float(item.get("lt"))
        zsz_yuan = _to_float(item.get("zsz"))
        row = {
            "dm": code, "mc": name,
            "zf": _to_float(item.get("zf")),
            "lbc": _to_int(item.get("lbc"), 1),
            "fbt": str(item.get("fbt") or "").strip() or None,
            "zbc": _to_int(item.get("zbc"), 0),
            "hs": _to_float(item.get("hs")),
            "hy": sector,
            "is_20cm": is_20cm,
            "zj": _to_float(item.get("zj")) / 1e8 if item.get("zj") else None,  # 元→亿
            "price": _to_float(item.get("price")) or None,
            "lt": lt_yuan / 1e8 if lt_yuan else None,
            "zsz": zsz_yuan / 1e8 if zsz_yuan else None,
            "cje": cje_yuan / 1e8 if cje_yuan else None,
        }
        row["is_st"] = _is_st(name, code)
        ztgc.append(row)
        code2themes[code] = [sector] if sector and sector != "其他" else []
        if sector and sector != "其他":
            sector_counter[sector] = sector_counter.get(sector, 0) + 1

    hot_sectors = [{"name": s, "count": c} for s, c in sorted(sector_counter.items(), key=lambda kv: kv[1], reverse=True)]
    return {"date": date, "ztgc": ztgc, "code2themes": code2themes, "hot_sectors": hot_sectors, "raw": raw}


def fetch_auction(codes: list[str]) -> dict[str, dict[str, Any]]:
    """
    批量读取 09:25 实时竞价（spec §3.2 hsrl/ssjy_more，每批最多 20 只）。
    返回 {code: {prev_close, auction_price, auction_amount_yi, time}}（归一化）。
    """
    client = build_client()
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(codes), 20):
        batch = codes[i:i + 20]
        url = f"hsrl/ssjy_more/{client.token}?stock_codes=" + ",".join(batch)
        # 注意：ssjy_more 路径中已含 token，用 get_json 直接拼完整 URL
        full = f"{client.base_url}/{url}"
        try:
            data = client.get_json(full)
        except Exception:
            continue
        rows = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            rows = []
        for r in rows:
            code = str(r.get("dm") or r.get("code") or r.get("symbol") or "").strip()
            if not code:
                continue
            prev = _to_float(r.get("yc"))
            price = _to_float(r.get("o")) or _to_float(r.get("p") or r.get("c"))
            amt_yuan = _to_float(r.get("cje"))
            out[code] = {
                "prev_close": prev,
                "auction_price": price,
                "auction_amount_yi": amt_yuan / 1e8 if amt_yuan else 0.0,
                "time": str(r.get("t") or ""),
            }
    return out


def fetch_history_k(code: str, st: str, et: str, period: str = "d", adjust: str = "f") -> list[dict[str, Any]]:
    """
    历史日 K（spec §3.3 hsstock/history），用于后验收益，不参与选股。
    返回归一化日 K 列表（含 close/date）。
    """
    client = build_client()
    path = f"hsstock/history/{code}/{period}/{adjust}/{client.token}?st={st}&et={et}"
    data = client.api(path, quiet_404=True)
    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        out.append({
            "date": str(r.get("date") or r.get("t") or ""),
            "close": _to_float(r.get("c") or r.get("close") or r.get("p")),
            "open": _to_float(r.get("o") or r.get("open")),
            "high": _to_float(r.get("h") or r.get("high")),
            "low": _to_float(r.get("l") or r.get("low")),
            "volume": _to_float(r.get("v") or r.get("volume")),
        })
    return out


def biying_available() -> bool:
    """是否启用必盈主池：环境变量开关 + token 存在。"""
    return os.getenv("WEALTH_USE_BIYING", "").strip() in ("1", "true", "yes") and bool(os.getenv("BIYING_TOKEN"))
