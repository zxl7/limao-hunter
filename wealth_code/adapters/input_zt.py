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


def _registry_read_enabled(cache_dir: Path) -> bool:
    """只有生产缓存或显式注入 DB 时才启用底库主读，避免夹具串到生产 DB。"""
    raw = os.getenv("WEALTH_USE_STOCK_REGISTRY", "1").strip().lower()
    if raw in ("0", "false", "no", "off", "n"):
        return False
    try:
        if cache_dir.resolve() == DEFAULT_CACHE_DIR.resolve():
            return True
    except OSError:
        pass
    return bool(os.getenv("LIMAO_REGISTRY_DB", "").strip())


def _load_registry_xuangubao(raw: dict[str, Any], cache_dir: Path) -> dict[str, Any] | None:
    """以底库为个股事实主读，旧快照仅补充尚未结构化的涨停字段。"""
    if not _registry_read_enabled(cache_dir):
        return None
    date10 = str(raw.get("date") or "").strip()[:10]
    if not date10:
        return None
    try:
        from .stock_registry_reader import get_daily_facts, get_stock

        rows = get_daily_facts(date10, date10, only_limit_up=True)
    except Exception:
        return None
    if not rows:
        return None

    supplements = {
        str(item.get("code") or "").strip(): item
        for item in (raw.get("zt_list") or [])
        if isinstance(item, dict) and str(item.get("code") or "").strip()
    }
    ztgc: list[dict[str, Any]] = []
    code2themes: dict[str, list[str]] = {}
    sector_counter: dict[str, int] = {}
    for item in rows:
        code = str(item.get("code") or "").strip()
        if len(code) != 6:
            continue
        old = supplements.get(code, {})
        stock = get_stock(code) or {}
        tags = stock.get("tags") if isinstance(stock.get("tags"), dict) else {}
        themes = []
        for key in ("theme", "concept", "sector", "board"):
            values = tags.get(key) or []
            if isinstance(values, str):
                values = [values]
            themes.extend(str(value).strip() for value in values if str(value).strip())
        legacy_sector = str(old.get("sector") or "").strip()
        if legacy_sector:
            themes.append(legacy_sector)
        themes = sorted(set(themes))
        industry = str(stock.get("industry") or item.get("industry") or legacy_sector or "").strip()
        row = {
            "dm": code,
            "mc": str(stock.get("name") or item.get("name") or old.get("name") or "").strip(),
            "zf": float(item.get("pct") if item.get("pct") is not None else old.get("pct") or 0.0),
            "lbc": int(item.get("boards") if item.get("boards") is not None else old.get("boards") or 1),
            "fbt": str(old.get("fbt") or "").strip() or None,
            "zbc": int(old.get("zbc") or 0),
            "hs": float(item.get("hs") if item.get("hs") is not None else old.get("hs") or 0.0),
            "hy": industry or "其他",
            "is_20cm": bool(stock.get("is_20cm") if stock.get("is_20cm") is not None else old.get("is_20cm")),
            "zj": float(item.get("seal_yi") if item.get("seal_yi") is not None else old.get("seal") or 0.0) or None,
            "lbt": None,
            "price": float(item.get("price") if item.get("price") is not None else old.get("price") or 0.0) or None,
            "lt": float(old.get("lt_yi") or old.get("lt") or 0.0) or None,
            "zsz": float(old.get("zsz_yi") or old.get("zsz") or 0.0) or None,
            "cje": float(old.get("cje_yi") or old.get("cje") or 0.0) or None,
        }
        # 封单与流通市值必须来自同一份快照，不能将底库旧封单拼接到新分母。
        # 底库仍作为身份/逐日事实主读；此开关仅控制动态封单对的读取。
        if os.getenv("ENABLE_COHERENT_SEAL_PAIR", "1") != "0":
            row["zj"] = (float(old["seal"]) if old.get("seal") is not None else None)
            row["seal_fact"] = {
                "source": "yesterday_zt", "date": date10,
                "ingested_at": raw.get("generated_ts"),
                "seal_yi": row["zj"], "lt_yi": row["lt"],
                "registry_seal_yi": item.get("seal_yi"),
                "different_from_registry": (
                    item.get("seal_yi") is not None and row["zj"] is not None
                    and float(item["seal_yi"]) != row["zj"]),
            }
        row["is_st"] = _is_st(row["mc"], code)
        ztgc.append(row)
        code2themes[code] = themes
        for theme in themes:
            if theme != "其他":
                sector_counter[theme] = sector_counter.get(theme, 0) + 1

    if not ztgc:
        return None
    return {
        "date": date10,
        "ztgc": ztgc,
        "code2themes": code2themes,
        "hot_sectors": [
            {"name": name, "count": count}
            for name, count in sorted(sector_counter.items(), key=lambda pair: pair[1], reverse=True)
        ],
        "raw": raw,
        "registry_primary": True,
    }


def _load_xuangubao(cache_dir: Path | None = None) -> dict[str, Any]:
    """底库主读个股事实，选股宝快照保留为补充字段与原始审计。"""
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    raw = _load_json(cache_dir / "yesterday_zt.json")
    if not raw:
        return {"date": "", "ztgc": [], "code2themes": {}, "hot_sectors": [], "raw": {}}

    registry_result = _load_registry_xuangubao(raw, cache_dir)
    if registry_result is not None:
        return registry_result

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
    if not fb or fb.get("error"):
        return {
            "sentiment_score": None, "sentiment_label": "未知",
            "sentiment_quality": "unavailable",
            "promote_rate": 0.0, "nuke_count": 0, "broken_rate": 0.0,
            "up_rate": 0.0, "avg_pct": 0.0, "now_zt_count": 0, "regime": "neutral",
        }

    import math
    raw_score = fb.get("sentiment_score")
    sentiment_score = (float(raw_score) if isinstance(raw_score, (int, float))
                       and not isinstance(raw_score, bool) and math.isfinite(raw_score)
                       and 1 <= raw_score <= 10 else None)
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
    if sentiment_score is None:
        regime = "risk_off" if break_count >= 1 else "neutral"
    elif sentiment_score <= 2.0:
        regime = "ice"
    elif (sentiment_score >= 6.5 and nuke_count <= 2 and broken_rate < 30
          and fb.get("nuke_count") is not None and fb.get("broken_rate") is not None
          and not {"nuke", "broken_rate"}.intersection(fb.get("sentiment_inputs_missing") or [])):
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
        "sentiment_model": fb.get("sentiment_model") or "legacy_v2",
        "sentiment_quality": (fb.get("sentiment_quality") or "unspecified") if sentiment_score is not None else "unavailable",
        "sentiment_inputs_missing": fb.get("sentiment_inputs_missing") or [],
        "sentiment_source_at": fb.get("source_snapshot_ts"),
        "promote_rate": promote_rate,
        "nuke_count": nuke_count,
        "broken_rate": broken_rate,
        "break_count": break_count,
        "up_rate": float(fb.get("up_rate") or 0.0),
        "avg_pct": float(fb.get("avg_pct") or 0.0),
        "now_zt_count": int(fb.get("now_zt_count") or 0),
        # 市场最高板（2026-10-21 · 92科比「要么做高要么做低」的参照系）：
        # 纯透传 feedback_matrix 既有字段，⛔ 不新抓、不改任何既有判定。
        # 中位区由 `core/cycle_phase.mid_band()` 按它算；缺失则中位股闸不裁决。
        "max_boards": (int(fb["now_max_boards"])
                       if fb.get("now_max_boards") is not None else None),
        "regime": regime,
        # P0 孤岛打通（2026-08-17）：透传东财次日预期题材（此前仅 load_pulse 有、
        # expectation 层读不到导致纯孤岛）。数据源 = pulse_snapshots/latest.json em_expect。
        "em_expect": (lambda _p: _p.get("em_expect") or [] if _p else [])(
            _load_json(cache_dir / "pulse_snapshots" / "latest.json")
        ),
    }
