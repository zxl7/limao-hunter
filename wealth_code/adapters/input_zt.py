#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
输入适配层：把狸猫猎手现有涨停池 / 情绪数据 转换成财富密码算法层的标准结构。

对接数据源（只读）：
- 狸猫猎手/cache/yesterday_zt.json  —— 涨停池（code/name/pct/boards/fbt/zbc/hs/sector/is_20cm）
- 狸猫猎手/cache/feedback_matrix.json —— 隔日情绪反馈（sentiment_score/nuke_count/broken_rate/...）

输出（标准结构，供 core/leader.py 直接消费）：
- ztgc: list[dict]，字段约定 dm/mc/hs/zbc/fbt/lbt/zj/zf/lbc/lt/cje/hy
- code2themes: {code: [题材]}
- hot_sectors: [{name: 题材}]（按当日涨停池题材频率排序）
- environment: 情绪潮汐代理（供接力门控使用）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# 狸猫猎手根目录（wealth_code 的上一级）
ROOT = Path(__file__).resolve().parent.parent.parent  # .../狸猫猎手
DEFAULT_CACHE_DIR = ROOT / "cache"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _is_st(name: str, code: str) -> bool:
    """简单 ST 过滤（与狸猫猎手纪律一致：不看 ST）。"""
    return "ST" in str(name or "").upper() or str(code or "").startswith(("4", "8", "9"))


def load_zt_pool(cache_dir: Path | None = None) -> dict[str, Any]:
    """
    加载涨停池并适配为标准结构（数据源「两者相结合」）。

    优先必盈主池（spec §3.1 ztgc）：当 WEALTH_USE_BIYING=1 且 BIYING_TOKEN 可用时，
    调 biying_pools.fetch_ztgc(date) 取必盈当日涨停池；任何失败（token 缺失/网络/空）
    自动降级选股宝 yesterday_zt.json（真实、稳定）。返回结构新增 "source" 字段标注来源。

    返回：
    {
      "date": "2026-08-10",
      "ztgc": [...],            # leader.py 标准涨停池
      "code2themes": {...},     # code -> [题材]
      "hot_sectors": [...],     # [{name}]
      "raw": {...},             # 原始数据（备用）
      "source": "biying" | "xuangubao"
    }
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    # 两者相结合：必盈主池优先，缺失降级选股宝
    if os.getenv("WEALTH_USE_BIYING", "").strip() in ("1", "true", "yes") and os.getenv("BIYING_TOKEN"):
        try:
            from .biying_pools import fetch_ztgc
            date = _xuangubao_date(cache_dir)
            if date:
                res = fetch_ztgc(date)
                if res.get("ztgc"):
                    res["source"] = "biying"
                    return res
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[zt] 必盈主池获取失败，降级选股宝: {e}\n")
    res = _load_xuangubao(cache_dir)
    res["source"] = "xuangubao"
    return res


def _xuangubao_date(cache_dir: Path | None = None) -> str:
    """取选股宝文件中的日期（作为必盈查询的 trade_date 基准）。"""
    raw = _load_json((cache_dir or DEFAULT_CACHE_DIR) / "yesterday_zt.json")
    return str(raw.get("date") or "").strip()


def _load_xuangubao(cache_dir: Path | None = None) -> dict[str, Any]:
    """选股宝（yesterday_zt.json）涨停池适配（原 load_zt_pool 主体）。"""
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    raw = _load_json(cache_dir / "yesterday_zt.json")
    if not raw:
        return {"date": "", "ztgc": [], "code2themes": {}, "hot_sectors": [], "raw": {}}

    zt_list = raw.get("zt_list") or []
    ztgc: list[dict[str, Any]] = []
    code2themes: dict[str, list[str]] = {}
    sector_counter: dict[str, int] = {}

    for item in zt_list:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        if not code or not name:
            continue
        sector = str(item.get("sector") or "").strip() or "其他"
        is_20cm = bool(item.get("is_20cm"))
        # 适配 leader.py 字段约定
        row = {
            "dm": code,
            "mc": name,
            "zf": float(item.get("pct") or 0.0),
            "lbc": int(item.get("boards") or 1),
            "fbt": str(item.get("fbt") or "").strip() or None,
            "zbc": int(item.get("zbc") or 0),
            "hs": float(item.get("hs") or 0.0),
            "hy": sector,
            "is_20cm": is_20cm,
            # 封单金额(亿)：快照 seal 字段 = biying zj/1e8（此前漏传，导致点火分封单因子恒 0）
            "zj": float(item.get("seal") or 0.0) or None,
            "lbt": None,
            # 真实收盘价（此前漏传，导致候选 price 被涨幅顶替）
            "price": float(item.get("price") or 0.0) or None,
            # 容量因子数据源（亿）：成交额/总市值/流通市值（biyingapi 已提供，单位元→亿由上游转换）
            "lt": float(item.get("lt_yi") or item.get("lt") or 0.0) or None,
            "zsz": float(item.get("zsz_yi") or item.get("zsz") or 0.0) or None,
            "cje": float(item.get("cje_yi") or item.get("cje") or 0.0) or None,
        }
        # ST / 北交所 标记（供下游过滤）
        row["is_st"] = _is_st(name, code)
        ztgc.append(row)
        code2themes[code] = [sector] if sector and sector != "其他" else []
        if sector and sector != "其他":
            sector_counter[sector] = sector_counter.get(sector, 0) + 1

    # 热门题材：按当日涨停池题材频率排序（leader.py 用它做题材匹配加权）
    hot_sectors = [
        {"name": s, "count": c}
        for s, c in sorted(sector_counter.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {
        "date": str(raw.get("date") or "").strip(),
        "ztgc": ztgc,
        "code2themes": code2themes,
        "hot_sectors": hot_sectors,
        "raw": raw,
    }


def load_environment(cache_dir: Path | None = None) -> dict[str, Any]:
    """
    加载隔日情绪反馈，作为接力门控的“情绪潮汐”代理。

    返回标准化 environment：
    {
      "sentiment_score": float,   # 0-10
      "sentiment_label": str,
      "promote_rate": float,      # 晋级率 %
      "nuke_count": int,          # 核按钮数
      "broken_rate": float,       # 断板率 %
      "up_rate": float,           # 上涨率 %
      "avg_pct": float,           # 平均涨幅 %
      "now_zt_count": int,
      "regime": "risk_on" | "neutral" | "risk_off"
    }
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    fb = _load_json(cache_dir / "feedback_matrix.json")
    if not fb:
        return {
            "sentiment_score": 5.0, "sentiment_label": "未知",
            "promote_rate": 0.0, "nuke_count": 0, "broken_rate": 0.0,
            "up_rate": 0.0, "avg_pct": 0.0, "now_zt_count": 0, "regime": "neutral",
        }

    sentiment_score = float(fb.get("sentiment_score") or 5.0)
    nuke_count = int(fb.get("nuke_count") or 0)
    broken_rate = float(fb.get("broken_rate") or 0.0)
    promote_rate = float(fb.get("promote_rate") or 0.0)
    # 真破位（同时跌破 MA5+MA10）家数：核按钮分级核心判据（2026-08-07 用户标准）
    break_count = int(fb.get("break_count") or 0)

    # 情绪体制判定（供接力门控）
    # 核按钮分级铁律：break_count=0（无真破位，仅连板分歧回踩 MA5）属正常分歧，
    # 按用户标准"可接受的5日线回调，不报恶劣"——不得判 risk_off / 回避高位接力；
    # 仅当真破位出现（break_count>=1）才升级为 risk_off（高位破位杀跌，回避高位接力）。
    # P1-6 冰点档（2026-08-17 用户定）：情绪分≤2.0 判 ice（冰点）——8/14 情绪 1/10 冰点
    # 仍判 neutral 导致 relay 照推的防守漏洞；冰点档触发 buy 硬开关（signal_eval 侧）。
    if sentiment_score <= 2.0:
        regime = "ice"
    elif sentiment_score >= 6.5 and nuke_count <= 2 and broken_rate < 30:
        regime = "risk_on"
    elif break_count >= 1:
        regime = "risk_off"
    elif sentiment_score <= 4.0 or nuke_count >= 5 or broken_rate >= 45:
        # 无真破位，仅分歧偏弱 → 中性偏谨慎，不进最防守档
        regime = "neutral"
    else:
        regime = "neutral"

    return {
        "sentiment_score": sentiment_score,
        "sentiment_label": str(fb.get("sentiment_label") or ""),
        "promote_rate": promote_rate,
        "nuke_count": nuke_count,
        "broken_rate": broken_rate,
        "break_count": break_count,
        "up_rate": float(fb.get("up_rate") or 0.0),
        "avg_pct": float(fb.get("avg_pct") or 0.0),
        "now_zt_count": int(fb.get("now_zt_count") or 0),
        "regime": regime,
        # P0 孤岛打通（2026-08-17）：透传东财次日预期题材（此前仅 load_pulse 有、
        # expectation 层读不到导致纯孤岛）。数据源 = pulse_snapshots/latest.json em_expect。
        "em_expect": (lambda _p: _p.get("em_expect") or [] if _p else [])(
            _load_json(cache_dir / "pulse_snapshots" / "latest.json")
        ),
    }
