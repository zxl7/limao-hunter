#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风险硬限制 · 单一源（2026-10-30 用户「单股买入不能超过 50%！！」）

## 为什么单独一个模块

用户纪律「单票 ≤50%」自 2026-09-14 起就在跑，但**同一个 0.5 被写了四遍**：

| 模块 | 原写法 |
|---|---|
| `core/ai_caliber.py` | `MAX_POSITION_PER_STOCK = 0.5` |
| `core/decision_outlook.py` | `POSITION_MAX = 0.5` |
| `core/relay_position.py` | `PER_PICK_STRONG = 0.50` |
| `core/ambush_pullback.py` | `NORMAL_ENV_POSITION_PCT = 50.0`（百分数口径） |

四份独立常量 = 四个可以分别漂移的地方。改一处、忘三处，就会出现宪法 117 行
禁止的那种事故：「同一张卡出现两个单票上限」。本模块把上限收敛成**唯一一个数**，
其余模块一律 import，不再各自定义。

## 边界（三条）

1. **只定义上限，不定义方案**：名额/总仓/回撤乘数仍归 `core/relay_position.py`（唯一判据源）。
   本模块不参与任何强弱判断，⛔ 不产出仓位建议。
2. **只收紧、绝不放大**：`clamp_per_stock` 对传入的动态上限（`per_pick_cap`）取
   `min(动态上限, 硬上限)`——动态方案只有在行情转弱时收紧，不可能把 50% 抬上去。
3. **纯函数无副作用**：不读文件、不写盘、无网络，可被任何层安全引用（含测试）。
"""
from __future__ import annotations

import math

# ── 用户纪律：单票仓位硬顶（⛔ 改这一个数就够，别去改别处）──────────────────────
# 2026-09-14 用户原话：「落袋单票的话，买入仓位不超过 50%！」
# 2026-10-30 用户再次强调：「单股买入不能超过 50%！！」
MAX_POSITION_PER_STOCK: float = 0.50

# 单票最低有效仓位（低于此视为「没买」而不是「买了一点」，用于过滤脏输入）
MIN_POSITION_EPS: float = 1e-4


def pct_of(ratio: float | None) -> str:
    """0.5 → "50%"（卡面文案统一走这里，避免各模块各写一遍格式化）。"""
    if not isinstance(ratio, (int, float)) or ratio <= 0:
        return "—"
    v = float(ratio) * 100.0
    return f"{v:.1f}%" if v < 10 else f"{v:.0f}%"


def clamp_per_stock(p: object, cap: object = None) -> float | None:
    """把单票仓位夹紧到硬上限以内（唯一的裁剪函数）。

    参数
      p   : 待裁剪的单票仓位（占账户比例，0.5 = 50%）
      cap : 调用方给的动态单票上限（来自 `relay_position.per_pick_cap`）。
            ⛔ 只能更小，不能更大——`min(cap, MAX_POSITION_PER_STOCK)` 保证这一点。

    返回
      合法正数 → 夹紧后的仓位（4 位小数）
      非法/非正/缺失 → None（诚实留空，⛔ 不编造仓位，也不填 0 冒充"已裁决"）
    """
    try:
        v = float(p)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # ⛔ NaN/Inf 必须显式拦：`nan <= eps` 恒为 False，会一路漏到账本里污染复利
    if not math.isfinite(v):
        return None
    if v <= MIN_POSITION_EPS:
        return None
    limit = MAX_POSITION_PER_STOCK
    try:
        if cap is not None:
            c = float(cap)  # type: ignore[arg-type]
            if math.isfinite(c) and c > 0:
                limit = min(limit, c)
    except (TypeError, ValueError):
        pass
    return round(min(v, limit), 4)


def clamp_many(positions: list, cap: object = None) -> list:
    """批量裁剪（保留 None 位与长度，供 `apply_total_cap` 之类等长返回的调用方）。

    非正数（含 0）一律归 None：0 不是"买了一点"，是"没买"，混在仓位列表里
    会让下游误以为该票存在有效持仓。
    """
    out: list = []
    for p in positions:
        if p is None:
            out.append(None)
            continue
        if isinstance(p, (int, float)) and math.isfinite(float(p)) and float(p) > MIN_POSITION_EPS:
            out.append(clamp_per_stock(p, cap))
        elif isinstance(p, (int, float)):
            out.append(None)
        else:
            out.append(p)
    return out


def audit() -> dict:
    """自检：把四个历史出口的常量读回来，确认没有一个超过硬顶。

    供 `tests/test_risk_limits.py` 与人工排查用。任一出口 > 硬顶即说明有人
    绕过单一源自己写了个数——这是必须暴露的漂移，不是可容忍的重复。
    """
    import importlib

    spec: list[tuple[str, str, float]] = [
        ("wealth_code.core.ai_caliber", "MAX_POSITION_PER_STOCK", 1.0),
        ("wealth_code.core.decision_outlook", "POSITION_MAX", 1.0),
        ("wealth_code.core.relay_position", "PER_PICK_STRONG", 1.0),
        # 尾盘潜伏用百分数口径（50.0 = 50%）
        ("wealth_code.core.ambush_pullback", "NORMAL_ENV_POSITION_PCT", 100.0),
        ("wealth_code.core.ambush_pullback", "WEAK_ENV_POSITION_PCT", 100.0),
    ]
    rows: list[dict] = []
    for mod_name, attr, scale in spec:
        try:
            mod = importlib.import_module(mod_name)
            raw = getattr(mod, attr)
        except Exception as e:  # noqa: BLE001
            rows.append({"module": mod_name, "attr": attr, "value": None,
                         "ratio": None, "ok": False, "error": repr(e)})
            continue
        ratio = float(raw) / scale
        rows.append({
            "module": mod_name, "attr": attr, "value": raw, "ratio": round(ratio, 4),
            "ok": ratio <= MAX_POSITION_PER_STOCK + 1e-9,
            "error": None,
        })
    return {
        "hard_cap": MAX_POSITION_PER_STOCK,
        "ok": all(r["ok"] for r in rows),
        "exits": rows,
    }
