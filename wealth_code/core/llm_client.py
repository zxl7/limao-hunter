#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 LLM 客户端（2026-11-13 · 竞价链 LLM 接入）

## 为什么要有这个模块
竞价链上原本有三处 LLM 相关代码互相不通：
- `scripts/ai_catalyst.py`：自有 `_resolve_model()`，只服务题材催化
- `scripts/ai_trader_judge.py`：`from ai_catalyst import _resolve_model`（跨脚本 import）
- `wealth_code/core/*`：**一处都没有** ⇒ 主链无法调模型

本模块把「取配置 + 发请求 + 抽 JSON」收敛到 `wealth_code/core/`，主链可直接 import。

## 配置来源（优先级从高到低）
1. 环境变量 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`
2. `~/.workbuddy/models.json`（WorkBuddy 已登录的模型列表，含 apiKey + url）

⛔ key 只在运行时读取，不写日志、不落盘、不 print。
⛔ 缺配置返回 `None`，由调用方降级；⛔ 绝不假装成功。

## 竞价窗口的三个硬约束
- **不重试**：`retries=0`。http_client 默认重试 2 次（指数退避），09:25-09:30 窗口内
  重试会把 25s 拖成 75s，直接毁掉推卡时点。
- **硬超时**：默认 25s，由调用方按剩余窗口传值。
- **失败静默**：返回 `None` 而非抛异常 —— 推卡不能因为 AI 挂了而中断。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_BASE = "https://api.deepseek.com"


def _join_url(base: str) -> str:
    """拼 chat/completions。

    ⚠️ `models.json` 里的 url 有两种形态：
      - `https://api.galaxydata.pro/v1`              → 需补 `/chat/completions`
      - `https://api.deepseek.com/chat/completions`  → 已含完整路径，⛔ 不能再拼
    直接 `${base}/chat/completions` 会拼出 `.../chat/completions/chat/completions`（404）。
    """
    b = (base or "").strip().rstrip("/") or DEFAULT_BASE
    return b if b.endswith("/chat/completions") else f"{b}/chat/completions"


def resolve_model(want: str | None = None) -> dict[str, str] | None:
    """返回 {model, base, key}；缺任一字段即返回 None。"""
    want = want or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    key = os.environ.get("LLM_API_KEY")
    if key:
        return {"model": want,
                "base": os.environ.get("LLM_BASE_URL") or DEFAULT_BASE,
                "key": key}
    p = Path.home() / ".workbuddy" / "models.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if isinstance(rows, dict):  # 兼容 {"models": [...]} 形态
        rows = rows.get("models") or rows.get("rows") or []
    if not isinstance(rows, list):
        return None
    for m in rows:
        if not isinstance(m, dict):
            continue
        if str(m.get("id")) != want and str(m.get("name")) != want:
            continue
        k, u = str(m.get("apiKey") or ""), str(m.get("url") or "")
        if not k or not u:
            return None
        return {"model": str(m.get("id") or want), "base": u, "key": k}
    return None


def chat(prompt: str, *, system: str | None = None, timeout: float = 25.0,
         max_tokens: int = 900, temperature: float = 0.2,
         want_model: str | None = None) -> str | None:
    """单轮调用，返回文本；任何失败返回 None（⛔ 不抛异常、不重试）。"""
    cfg = resolve_model(want_model)
    if not cfg:
        return None
    msgs: list[dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    try:
        from common.http_client import http_post
        res = http_post(
            _join_url(cfg["base"]),
            {"model": cfg["model"], "temperature": temperature,
             "max_tokens": max_tokens, "messages": msgs},
            timeout=timeout, retries=0,
            headers={"Authorization": f"Bearer {cfg['key']}",
                     "Content-Type": "application/json"},
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(res, dict):
        return None
    try:
        return str(res["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError):
        return None


def extract_json(text: str | None) -> Any:
    """从可能带 ```json 围栏的文本里抽第一个完整 JSON 对象。"""
    if not text:
        return None
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except Exception:  # noqa: BLE001
        return None


def chat_json(prompt: str, **kw) -> Any:
    return extract_json(chat(prompt, **kw))
