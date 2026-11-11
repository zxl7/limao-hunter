#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材催化强度建议（LLM 层契约 · 2026-11-11）

## 为什么需要这一层

机械层能算「这只票今天强不强」，但算不出「这条线**为什么**今天会强」——
政策/产业事件/龙头公告这类**催化**。用户 11-11：「我们还是要挑着做题材催化强度这一块」。
催化只能由 LLM 读新闻判断，所以本模块只做两件事：

1. **契约**：定义 `cache/ai_advice_<date>.json` 的结构与校验；
2. **取用**：`pool_call` 读它做**影子记录**（默认不参与裁决）。

## ⛔ 边界

- LLM 只输出「每个题材的催化强度 + 依据」，**不输出买卖结论**；
- 任一字段缺失/越界 → 该题材视为 `unknown`（⛔ 不猜、不默认强）；
- 文件缺失 = 没有 LLM 意见（⛔ 不等于「催化弱」）；
- 本模块**不联网**：抓取与调用在 `scripts/ai_catalyst.py`。

## 文件结构

```json
{
  "schema": "ai_advice_v1",
  "date": "2026-11-12",
  "source": "llm|manual",
  "model": "deepseek-chat",
  "generated_at": "2026-11-11 20:10:00",
  "themes": {
    "机器人": {
      "catalyst": 0.82,          // 0~1 催化强度
      "level": "strong",         // strong|normal|weak|unknown
      "basis": "宇树科技上市在即 + 特斯拉 Optimus 量产指引",
      "sources": ["财联社 2026-11-11 09:12", "公司公告"]
    }
  },
  "stocks": {
    "002708": {"flag": "leader", "note": "这条线的核心，催化直接受益"}
  }
}
```
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
SCHEMA = "ai_advice_v1"
LEVELS = {"strong", "normal", "weak", "unknown"}


def path_for(day: str) -> Path:
    return CACHE / f"ai_advice_{day}.json"


def validate(d: Any) -> dict[str, Any]:
    """校验并归一化；非法字段降级为 unknown，⛔ 不抛错（09:25 不能被建议文件打断）。"""
    if not isinstance(d, Mapping):
        return {"schema": SCHEMA, "valid": False, "themes": {}, "stocks": {},
                "errors": ["root not object"]}
    errors: list[str] = []
    themes: dict[str, dict[str, Any]] = {}
    raw_themes = d.get("themes")
    if isinstance(raw_themes, Mapping):
        for name, v in raw_themes.items():
            if not isinstance(v, Mapping):
                continue
            cat = v.get("catalyst")
            try:
                cat_f = float(cat)
            except (TypeError, ValueError):
                cat_f = None
            level = str(v.get("level") or "").strip().lower()
            if level not in LEVELS:
                # 没给 level 就用分数推；分数也没有 → unknown
                if cat_f is None:
                    level = "unknown"
                else:
                    level = "strong" if cat_f >= 0.65 else "weak" if cat_f < 0.35 else "normal"
            if cat_f is not None and not (0.0 <= cat_f <= 1.0):
                errors.append(f"{name}: catalyst out of range")
                cat_f = None
            themes[str(name)] = {
                "catalyst": cat_f,
                "level": level if cat_f is not None or level == "weak" else "unknown",
                "basis": str(v.get("basis") or "")[:200],
                "sources": [str(x)[:120] for x in (v.get("sources") or [])][:5],
            }
    stocks: dict[str, dict[str, Any]] = {}
    raw_stocks = d.get("stocks")
    if isinstance(raw_stocks, Mapping):
        for code, v in raw_stocks.items():
            if isinstance(v, Mapping):
                stocks[str(code)] = {"flag": str(v.get("flag") or ""),
                                     "note": str(v.get("note") or "")[:160]}
    return {
        "schema": SCHEMA,
        "valid": bool(themes),
        "date": str(d.get("date") or ""),
        "source": str(d.get("source") or "unknown"),
        "model": str(d.get("model") or ""),
        "generated_at": str(d.get("generated_at") or ""),
        "themes": themes,
        "stocks": stocks,
        "errors": errors,
    }


def load(day: str) -> dict[str, Any] | None:
    p = path_for(day)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return validate(raw)


def level_of(advice: Mapping[str, Any] | None, theme: str) -> str:
    if not advice:
        return "unknown"
    return str(((advice.get("themes") or {}).get(theme) or {}).get("level") or "unknown")


def catalyst_of(advice: Mapping[str, Any] | None, theme: str) -> float | None:
    if not advice:
        return None
    v = ((advice.get("themes") or {}).get(theme) or {}).get("catalyst")
    return float(v) if isinstance(v, (int, float)) else None
