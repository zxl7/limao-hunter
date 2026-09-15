#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真实环境适配层：把盘中脉冲快照（pulse_snapshots/latest.json）与隔日情绪反馈
（feedback_matrix.json）合并成「当前大盘 + 板块环境」标准结构。

这是 spec 「环境/板块/风险/潮汐」四维的**真实数据来源**（spec 原版依赖必盈上游因子
管线，本系统用选股宝/pulse/feedback 真实派生，守数据诚实：标注「真实派生」）。

输出 real_env：
{
  "ts": "...",                       # 快照时点
  "indices": [{"name","pct","price"}...],   # 三大指数（真实）
  "avg_index_pct": float,            # 指数均值涨跌幅（真实，喂 environment_score）
  "sentiment": str,                  # 情绪标签（修复/分歧/退潮...）
  "sentiment_score": float,          # 情绪分（0-10，真实）
  "total_zt": int,                   # 涨停总数（真实）
  "limit_down": {"count","names"},   # 跌停（真实）
  "broken": {"count","names"},       # 炸板（真实，亏钱效应）
  "sector_risk": {...},              # 板块风险（下跌/净流入/流出，真实）
  "weak_sectors": {...},             # 弱板块（破板聚焦，真实）
  "hot_spots": [...],                # 强势题材热点（真实）
  "em_expect": [...],                # 东方财富题材预期（真实）
  "feedback": {...},                 # 透传 feedback_matrix 关键字段（核按钮/破位/晋级率）
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent  # .../狸猫猎手
DEFAULT_CACHE_DIR = ROOT / "cache"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_pulse(cache_dir: Path | None = None) -> dict[str, Any]:
    """
    加载 pulse_snapshots/latest.json → 真实环境结构。
    缺失时返回含空字段的结构（下游优雅降级，不中断选股）。
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    d = _load_json(cache_dir / "pulse_snapshots" / "latest.json")
    if not d:
        return {
            "ts": "", "indices": [], "avg_index_pct": 0.0, "sentiment": "未知",
            "sentiment_score": 0.0, "total_zt": 0, "limit_down": {"count": 0, "names": []},
            "broken": {"count": 0, "names": []}, "sector_risk": {}, "weak_sectors": {},
            "hot_spots": [], "em_expect": [], "feedback": {},
            "early": [],
        }

    idxs = d.get("index") or []
    avg_idx = (sum(float(i.get("pct", 0.0)) for i in idxs) / len(idxs)) if idxs else 0.0
    sd = d.get("sentiment_detail") or {}
    sentiment = str(d.get("sentiment") or sd.get("label") or "未知")
    sent_score = _to_float(sd.get("score")) if sd.get("score") is not None else 0.0

    # 透传 feedback_matrix 关键情绪字段（核按钮/破位/晋级率）——与 pulse 并列真实源
    fb = _load_json(cache_dir / "feedback_matrix.json")
    feedback = {
        "sentiment_score": _to_float(fb.get("sentiment_score")),
        "nuke_count": int(fb.get("nuke_count") or 0),
        "break_count": int(fb.get("break_count") or 0),
        "broken_rate": _to_float(fb.get("broken_rate")),
        "promote_rate": _to_float(fb.get("promote_rate")),
        "up_rate": _to_float(fb.get("up_rate")),
        "avg_pct": _to_float(fb.get("avg_pct")),
    }

    return {
        "ts": str(d.get("ts") or ""),
        "indices": [{"name": i.get("name"), "pct": _to_float(i.get("pct")), "price": _to_float(i.get("price"))} for i in idxs],
        "avg_index_pct": round(avg_idx, 3),
        "sentiment": sentiment,
        "sentiment_score": sent_score,
        "total_zt": int(d.get("total_zt") or 0),
        "limit_down": {"count": int((d.get("limit_down") or {}).get("count", 0)),
                       "names": (d.get("limit_down") or {}).get("names") or []},
        "broken": {"count": int((d.get("broken") or {}).get("count", 0)),
                   "names": (d.get("broken") or {}).get("names") or []},
        "sector_risk": d.get("sector_risk") or {},
        "weak_sectors": d.get("weak_sectors") or {},
        "hot_spots": d.get("hot_spots") or [],
        "em_expect": d.get("em_expect") or [],
        # P0-量能广度（2026-08-17）：两市成交额 + 全市场涨跌家数（intraday_pulse 已落盘）——
        # 供卡片「量能广度」行展示 + 情绪分量能/广度因子消费
        "market": d.get("market"),
        "breadth": d.get("breadth") or {},
        # P0-2 孤岛打通（2026-08-17）：透传盘中早鸟埋伏信号（uplimit=false 未封板强势拉升 +
        # newsflash 点名股），供 eod 选股回标「盘中强势」——情绪从盘中传递到盘后候选。
        "early": d.get("early") or [],
        "feedback": feedback,
    }


def _to_float(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return d
