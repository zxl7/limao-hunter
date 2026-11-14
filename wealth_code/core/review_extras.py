#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""👑 高标处境 + ⚠️ 明日跌停预判（2026-11-14 用户「吸收截图里对高标和明日跌停的理解」）

两块内容，供盘后复盘卡（LLM）直接引用：
1. `high_board`：今日最高板是谁、封得怎么样、是不是第二波、同板位历史晋级率、
   明日「封住=晋级 / 断板=退潮信号」的关键线；
2. `dump_risk`：明日会不会成批跌停 —— 用跌停数/炸板率/核按钮/晋级率/最高板/指数
   六条信号打分，并给出「昨日涨停今日跌停」的实证数。

数据：`pulse_snapshots/latest.json`、`feedback_matrix`、`zt_history`、`kline_qfq`、
`promotion_odds`、`second_wave`。
⛔ 只读汇总，不改判据。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import promotion_odds as po   # noqa: E402
from core import second_wave as sw      # noqa: E402
from core import review_panel as rp     # noqa: E402

# 高位板的历史晋级率（40 日实测：3→4 43.6%、4→5 44.2%）。
# `promotion_odds` 只覆盖 1/2 板（那两档有竞价分层），3 板以上用这个总体值。
NEXT_ODDS = {2: 33.6, 3: 43.6, 4: 44.2}


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def _days() -> list[str]:
    return sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))


def high_board(day: str, zt: list[dict], pct: dict[str, float]) -> dict:
    mx = max((int(r.get("boards") or 0) for r in zt), default=0)
    holders = [r for r in zt if int(r.get("boards") or 0) == mx]
    rows = []
    for r in holders:
        code = str(r.get("code"))
        boards = int(r.get("boards") or 0)
        sw_hit = sw.second_wave(boards, code)
        nxt = NEXT_ODDS.get(boards) or po.odds(boards, None).get("pct")
        rows.append({
            "name": r.get("name"), "code": code, "boards": boards,
            "pct": pct.get(code), "zbc": int(r.get("zbc") or 0),
            "fbt": str(r.get("fbt") or "")[:4],
            "second_wave": bool(sw_hit), "prior_zt": (sw_hit or {}).get("prior"),
            "next_odds": nxt,
            "key": (f"明日封住 → {boards + 1} 板（同板位历史 {nxt}%）；"
                    f"断板 → 高位退潮信号，这条线当日不接力"),
        })
    return {"max_boards": mx, "count": len(holders), "rows": rows,
            "note": (f"今日最高 {mx} 板 · {len(holders)} 只持有"
                     + ("（含第二波，位置偏晚）" if any(x["second_wave"] for x in rows) else ""))}


def dump_risk(day: str, pulse: dict, fb: dict, zt_prev: list[dict], zt_today: list[dict],
              pct: dict[str, float]) -> dict:
    risk = rp.risk_block(pulse, fb, zt_prev, zt_today)
    ld_names = set((pulse.get("limit_down") or {}).get("names") or [])
    prev_names = {str(r.get("name")) for r in zt_prev}
    killed = sorted(ld_names & prev_names)          # 昨日涨停 → 今日跌停（最凶的亏钱效应）
    high = [r for r in zt_prev if int(r.get("boards") or 0) >= 3]
    high_pct = [pct[str(r.get("code"))] for r in high if str(r.get("code")) in pct]
    extra = {
        "prev_zt_to_limit_down": killed[:8],
        "prev_zt_to_limit_down_count": len(killed),
        "high_board_avg_pct": round(sum(high_pct) / len(high_pct), 2) if high_pct else None,
        "high_board_n": len(high),
    }
    # 高标补一条：昨日高位股今天平均跌超 5% → 直接升一档
    if extra["high_board_avg_pct"] is not None and extra["high_board_avg_pct"] <= -5 and risk["level"] != "高":
        risk["level"] = "中" if risk["level"] == "低" else "高"
        risk["signals"].append({"name": "高位股集体大面", "hit": True,
                                "value": f"昨日≥3板 均 {extra['high_board_avg_pct']}%"})
    risk.update(extra)
    risk["verdict"] = (
        "明天大概率还有成批跌停，先躲高位" if risk["level"] == "高"
        else "局部跌停风险，避高位、只做低位确认" if risk["level"] == "中"
        else "成批跌停概率低，可按正常节奏做")
    return risk


def build(day: str | None = None) -> dict:
    ds = _days()
    day = day or (ds[-1] if ds else "")
    prev = [d for d in ds if d < day]
    prev_day = prev[-1] if prev else None
    zt_today = _load(ROOT / "cache" / "zt_history" / f"zt_{day}.json", {}).get("zt_list") or []
    zt_prev = (_load(ROOT / "cache" / "zt_history" / f"zt_{prev_day}.json", {}).get("zt_list") or []) if prev_day else []
    pulse = _load(ROOT / "cache" / "pulse_snapshots" / "latest.json", {})
    fb = _load(ROOT / "cache" / "feedback_matrix.json", {})
    pct = rp._pct_of(day)
    return {"date": day, "prev": prev_day,
            "high_board": high_board(day, zt_today, pct),
            "dump_risk": dump_risk(day, pulse, fb, zt_prev, zt_today, pct)}
