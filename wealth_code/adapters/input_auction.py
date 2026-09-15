#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
输入适配层：次日 9:25 竞价快照接入。

线上接入方式（默认真实路径）：
- fetch_realtime_auction(codes) 通过必盈 API（biyingapi）hsrl/ssjy_more 抓取竞价价/昨收/
  竞价额（token 仅从环境变量或本机技能配置读取，详见 common/biying_client.py）。

兼容文件方式：
- 狸猫猎手实时链路若已在 09:25 落一份 auction_quotes-YYYYMMDD.json
  （{code: {prev_close, auction_price, auction_amount_yi, time}}），load_auction_quotes() 可直接读取。

测试 / 演示：
- synthesize_quotes_for_test() 用盘后候选的 price/pct 反推昨收，再按确定性偏移生成
  竞价价与隔日收盘价，使整条闭环可在无实时数据下端到端跑通（仅验证算法与口径，非收益预期）。
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BJ_TZ = timezone(timedelta(hours=8))

# 竞价接入数据源：必盈 API（biyingapi）hsrl/ssjy_more —— 稳定、无需 web 爬虫。
# 字段：o=今开(集合竞价成交价=竞价价) / yc=昨收 / cje=成交额(元，09:25≈竞价额) / t=时间。
# ⚠️ Token 仅从环境变量 BIYING_TOKEN 或本机技能配置读取，绝不硬编码（详见 common/biying_client.py）。
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))
from common import biying_client  # noqa: E402


def _limit_pct(is_20cm: bool) -> float:
    return 0.2 if is_20cm else 0.1


def load_auction_quotes(path: Path) -> dict[str, dict[str, Any]]:
    """
    从 JSON 文件加载竞价快照。

    支持两种形态：
    - dict: {code: {prev_close, auction_price, auction_amount_yi, time}}
    - list: [{code, prev_close, auction_price, auction_amount_yi, time}]
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    if isinstance(data, dict):
        for code, q in data.items():
            if isinstance(q, dict):
                out[str(code).strip()] = q
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("code"):
                out[str(item["code"]).strip()] = item
    return out


def _deterministic_gap(code: str, lo: float, hi: float) -> float:
    """用 code 哈希生成确定性 [lo,hi] 偏移，保证测试可复现。"""
    h = int(hashlib.md5(code.encode("utf-8")).hexdigest(), 16)
    frac = (h % 1000) / 1000.0
    return round(lo + (hi - lo) * frac, 2)


def synthesize_quotes_for_test(
    candidates: list[dict[str, Any]],
    *,
    gap_lo: float = 1.0,
    gap_hi: float = 6.0,
    amount_yi: float = 1.5,
    next_day_change_lo: float = -3.0,
    next_day_change_hi: float = 4.0,
) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    """
    为 dry-run 合成竞价快照与隔日收盘价。

    入参 candidates：盘后候选（需含 code/price/pct/is_20cm）。
    返回 (quotes, next_close_map)：
    - quotes[code] = {prev_close, auction_price, auction_amount_yi, time}
    - next_close_map[code] = 隔日收盘价（用于计算 next_day return）
    """
    quotes: dict[str, dict[str, Any]] = {}
    next_close: dict[str, float] = {}
    for c in candidates:
        code = str(c.get("code") or "").strip()
        if not code:
            continue
        price = float(c.get("price") or 0.0)
        pct = float(c.get("pct") or 0.0)
        is_20cm = bool(c.get("is_20cm"))
        lp = _limit_pct(is_20cm)
        # 反推昨收（涨停价 = 昨收 * (1+limit)）
        prev_close = round(price / (1.0 + lp), 2) if price > 0 else 0.0
        gap = _deterministic_gap(code, gap_lo, gap_hi)
        auction_price = round(prev_close * (1.0 + gap / 100.0), 2) if prev_close > 0 else price
        quotes[code] = {
            "prev_close": prev_close,
            "auction_price": auction_price,
            "auction_amount_yi": amount_yi,
            "time": "09:25:00",
        }
        # 隔日收盘价：在竞价价基础上再确定性偏移
        ndc = _deterministic_gap(code + "next", next_day_change_lo, next_day_change_hi)
        next_close[code] = round(auction_price * (1.0 + ndc / 100.0), 2)
    return quotes, next_close


def fetch_realtime_closes(
    codes: list[str],
    *,
    retries: int = 3,
    sleep: float = 2.0,
) -> dict[str, float]:
    """
    实时抓取「当前价」（收盘后即收盘价）→ {code: close_price}，用于盘后收益结算
    （2026-08-12 新增：原结算链路只在次日 09:25 用昨收，收盘后无法立即结算当日收益）。

    数据源同 fetch_realtime_auction（必盈 hsrl/ssjy_more），取 p=当前价字段。
    """
    out: dict[str, float] = {}
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        return out
    for _ in range(1, retries + 1):
        pending = [c for c in codes if c not in out]
        if not pending:
            break
        quotes = biying_client.fetch_realtime_quotes(pending)
        for code, it in quotes.items():
            px = float(it.get("p") or 0.0)
            if px > 0:
                out[code] = round(px, 2)
        if len(out) >= len(codes):
            break
        time.sleep(sleep)
    return out


def fetch_realtime_auction(
    codes: list[str],
    *,
    retries: int = 8,
    sleep: float = 3.0,
) -> dict[str, dict[str, Any]]:
    """
    次日 9:25 实时抓取竞价快照（狸猫猎手·竞价接入，真实数据路径，必盈 API）。

    返回 {code: {prev_close, auction_price, auction_amount_yi, time}}，
    与 load_auction_quotes() 的 schema 完全一致，可直接喂给 pipeline_verify。

    映射（biyingapi hsrl/ssjy_more）：
      o  -> auction_price          （今开 = 集合竞价成交价）
      yc -> prev_close             （昨收）
      cje/1e8 -> auction_amount_yi （成交额元 → 亿元；09:25 时≈竞价成交额）
      t  -> time                   （更新时间）

    重试：09:25 刚开盘时 o(今开) 可能尚未落库（=0），故循环等待直到 o>0 且 yc>0
    或重试耗尽（默认 8 次 × 3s ≈ 24s，足以覆盖 09:25:00 后的数据落地）。
    即便偶发滑到 09:30 连续竞价，o 仍为集合竞价成交价，不受影响。
    """
    out: dict[str, dict[str, Any]] = {}
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        return out
    now_str = datetime.now(BJ_TZ).strftime("%H:%M:%S")
    for _ in range(1, retries + 1):
        pending = [c for c in codes if c not in out]
        if not pending:
            break
        quotes = biying_client.fetch_realtime_quotes(pending)
        for code, it in quotes.items():
            open_px = float(it.get("o") or 0.0)   # 今开 = 竞价价
            prev = float(it.get("yc") or 0.0)     # 昨收
            cje = float(it.get("cje") or 0.0)     # 成交额(元)
            if open_px > 0 and prev > 0:
                out[code] = {
                    "prev_close": round(prev, 2),
                    "auction_price": round(open_px, 2),
                    "auction_amount_yi": round(cje / 1e8, 2),
                    "time": str(it.get("t") or now_str),
                }
        if len(out) >= len(codes):
            break
        time.sleep(sleep)
    return out
