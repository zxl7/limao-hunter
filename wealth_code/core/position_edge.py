#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""⚖️ 仓位与暴露控制（2026-11-12 吸收原版 quant-review 的四条优点）

用户 2026-11-12：「可以把这些优点都吸收一下，更细化。」

## 吸收了什么（来源：zxl7/quant-review）

1. **赢面五档**（`v3_position.py`）：`edge = 胜率 × 赔率` →
   ≥0.90 满仓 / ≥0.80 重仓 / ≥0.70 正常 / ≥0.60 试错 / <0.60 空仓。
2. **连续走弱降档**（今天在 105 天账本上 walk-forward 验证过）：
   连续 2 日单日 ≤ −1.5% → 次日空仓；验证段收益 +12.0%→+18.9%、回撤 −17.0%→−10.9%。
3. **满仓三条件共振**（`v3_fullposition.py`）：指数趋势 + 板块同步 + 个股人气，
   缺任一 → 不允许满仓（这里指数数据缺失时**默认不满足**，⛔ 不猜）。
4. **崩盘预警**（`v3_dujie.py` / `v3_collapse.py` 的思路）：
   炸板率 / 核按钮数 / 晋级率同时恶化 → 降暴露。

## 边界

- 本模块只输出**暴露系数**（0~1）与理由，⛔ 不改任何买卖判据、不改名额；
- 由 `pipeline_verify` 决定是否应用（`EXPOSURE_MODE=off|shadow|live`，默认 shadow）；
- 全部输入都来自本机缓存，缺数据时**保守但不猜**：缺 → 该条不参与，不当作利空。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

# 赢面五档（照抄原版 v3_position，按 edge 分档）
TIERS: tuple[tuple[float, float, str], ...] = (
    (0.90, 1.00, "满仓区"),
    (0.80, 0.80, "重仓区"),
    (0.70, 0.50, "正常区"),
    (0.60, 0.20, "试错区"),
    (0.00, 0.00, "空仓区"),
)

DEFAULT_ODDS = 1.5          # 赔率缺失时的中性值（原版默认 1.5）


def edge_tier(win_rate: float | None, odds: float | None = None) -> dict[str, Any]:
    """`edge = 胜率 × 赔率` → 五档仓位上限。缺胜率 → 正常区（不猜）。"""
    if win_rate is None:
        return {"edge": None, "tier": "未知区", "max_position": 0.50,
                "why": "缺胜率样本 → 按正常区（不猜）"}
    p = max(0.0, min(1.0, float(win_rate)))
    o = float(odds) if isinstance(odds, (int, float)) and odds > 0 else DEFAULT_ODDS
    edge = round(p * o, 3)
    for min_edge, pos, tier in TIERS:
        if edge >= min_edge:
            return {"edge": edge, "tier": tier, "max_position": pos,
                    "why": f"赢面 {edge}（胜率 {p:.0%} × 赔率 {o:g}）→ {tier}"}
    return {"edge": edge, "tier": "空仓区", "max_position": 0.0, "why": f"赢面 {edge} → 空仓区"}


def streak_coeff(returns: list[float], *, th: float = 0.015,
                 exp: float = 0.0, days: int = 2) -> dict[str, Any]:
    """连续 `days` 日单日收益 ≤ −th → 暴露降为 `exp`（默认空仓）。"""
    if len(returns) < days:
        return {"coeff": 1.0, "why": "样本不足，不降档"}
    tail = returns[-days:]
    if all(float(r) <= -abs(th) for r in tail):
        return {"coeff": exp,
                "why": f"连续 {days} 日 ≤ −{abs(th):.1%}（{', '.join(f'{r:+.2%}' for r in tail)}）→ 暴露 {exp:g}"}
    return {"coeff": 1.0, "why": "未连续走弱"}


def crash_cap(fb: dict[str, Any] | None) -> dict[str, Any]:
    """崩盘预警：炸板率 ≥60% 或 核按钮 ≥30 家 → 暴露减半；两者同时 → 空仓。"""
    if not isinstance(fb, dict):
        return {"cap": 1.0, "level": "unknown", "why": "无反馈矩阵"}
    try:
        broken = float(fb.get("broken_rate") or 0)
        nuke = float(fb.get("nuke_count") or 0)
        # ⛔ 晋级率缺失时**不参与**判断（⛔ 不拿缺失当利空）
        promote = float(fb["promote_rate"]) if fb.get("promote_rate") is not None else None
    except (TypeError, ValueError):
        return {"cap": 1.0, "level": "unknown", "why": "字段异常"}
    hits = []
    if broken >= 60:
        hits.append(f"炸板率 {broken:.0f}%")
    if nuke >= 30:
        hits.append(f"核按钮 {nuke:.0f} 家")
    if promote is not None and promote <= 15:
        hits.append(f"晋级率 {promote:.1f}%")
    if len(hits) >= 2:
        return {"cap": 0.0, "level": "danger", "why": "、".join(hits) + " → 空仓"}
    if hits:
        return {"cap": 0.5, "level": "warn", "why": "、".join(hits) + " → 减半"}
    return {"cap": 1.0, "level": "normal", "why": "情绪指标正常"}


def fullpos_cap(*, index_ok: bool | None = None,
                sector_ok: bool | None = None,
                stock_ok: bool | None = None) -> dict[str, Any]:
    """满仓三条件；任一不满足 → 上限 0.8（原版：不允许满仓）。"""
    checks = {"index": index_ok, "sector": sector_ok, "stock": stock_ok}
    if any(v is None for v in checks.values()):
        return {"cap": 0.8, "passed": False,
                "why": "三条件数据不全（指数/板块/个股）→ 不允许满仓（0.8 上限）"}
    if all(checks.values()):
        return {"cap": 1.0, "passed": True, "why": "三条件共振"}
    return {"cap": 0.8, "passed": False, "why": "三条件未全过 → 0.8 上限"}


def combine(*, win_rate: float | None = None, odds: float | None = None,
            returns: list[float] | None = None,
            fb: dict[str, Any] | None = None,
            index_ok: bool | None = None, sector_ok: bool | None = None,
            stock_ok: bool | None = None) -> dict[str, Any]:
    """四条的**取小**（不叠乘，避免过度收缩）。"""
    parts = {
        "edge": edge_tier(win_rate, odds),
        "streak": streak_coeff(list(returns or [])),
        "crash": crash_cap(fb),
        "fullpos": fullpos_cap(index_ok=index_ok, sector_ok=sector_ok, stock_ok=stock_ok),
    }
    coeffs = {"edge": parts["edge"]["max_position"],
              "streak": parts["streak"]["coeff"],
              "crash": parts["crash"]["cap"],
              "fullpos": parts["fullpos"]["cap"]}
    exposure = min(coeffs.values())
    driver = min(coeffs, key=lambda k: coeffs[k])
    return {"exposure": round(exposure, 3), "driver": driver,
            "coeffs": coeffs, "parts": parts,
            "why": parts[driver].get("why")}


def load_returns(n: int = 20) -> list[float]:
    """近 n 日策略口径单日收益（`wealth_code/cache/ai_nav_history.jsonl`）。"""
    p = ROOT / "wealth_code" / "cache" / "ai_nav_history.jsonl"
    try:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:  # noqa: BLE001
        return []
    vals = [float(r["avg_return_pct"]) / 100 for r in rows[-n:]
            if isinstance(r.get("avg_return_pct"), (int, float))]
    return vals


def rolling_edge(n: int = 20) -> dict[str, Any]:
    """近 n 日账本 → 胜率 / 赔率（赔率 = 平均盈利 / |平均亏损|）。"""
    p = ROOT / "wealth_code" / "cache" / "ai_nav_history.jsonl"
    try:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:  # noqa: BLE001
        return {"win_rate": None, "odds": None, "n": 0}
    rets: list[float] = []
    for r in rows[-n:]:
        for s in (r.get("stocks") or []):
            if isinstance(s.get("return_pct"), (int, float)):
                rets.append(float(s["return_pct"]))
    if len(rets) < 10:
        return {"win_rate": None, "odds": None, "n": len(rets)}
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x < 0]
    wr = len(wins) / len(rets)
    odds = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else None
    return {"win_rate": round(wr, 3), "odds": round(odds, 2) if odds else None, "n": len(rets)}


def snapshot(fb_path: Path | None = None) -> dict[str, Any]:
    """一站式计算（供 09:25 / 15:30 调用）。"""
    fb = None
    try:
        fb = json.loads((fb_path or (ROOT / "cache" / "feedback_matrix.json")).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        fb = None
    edge = rolling_edge()
    return combine(win_rate=edge["win_rate"], odds=edge["odds"],
                   returns=load_returns(), fb=fb)
