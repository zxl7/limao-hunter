#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一缓存路径（数据基座 · 工件 D）

双物理根（保留，不做破坏性合并）：
  - CACHE_ROOT         = <repo>/cache                顶层数据（脉冲/快照/题材/基因…）
  - WEALTH_CACHE_ROOT  = <repo>/wealth_code/cache    财富密码账本/决策/AI 台账

数据基座契约：所有脚本/服务经本模块取缓存路径，禁止在业务代码里硬编码
`'cache/xxx'` 字符串。serve.py 的 PUBLIC_ROUTES 仍保留相对路径作为运行源，
其解析经 resolve()/try_resolve() 统一归口，避免散落路径漂移。

提供：
  - cache_path(*sub)          顶层缓存根下的绝对路径
  - wealth_cache_path(*sub)   财富密码缓存根下的绝对路径
  - resolve(rel)              把 serve.py 风格相对路径映射到绝对路径
  - try_resolve(rel)          宽松版，非 cache/wealth_code 前缀返回 None（防越界）
  - ALL_ROOTS                 供 serve.py ALLOWED_ROOTS 复用
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = REPO_ROOT / "cache"
WEALTH_CACHE_ROOT = REPO_ROOT / "wealth_code" / "cache"
ALL_ROOTS = (str(CACHE_ROOT), str(WEALTH_CACHE_ROOT), str(REPO_ROOT))


def cache_path(*sub: str) -> Path:
    """顶层缓存根（cache/）下的绝对路径。"""
    return CACHE_ROOT.joinpath(*sub)


def wealth_cache_path(*sub: str) -> Path:
    """财富密码缓存根（wealth_code/cache/）下的绝对路径。"""
    return WEALTH_CACHE_ROOT.joinpath(*sub)


def resolve(rel: str) -> Path:
    """serve.py 风格相对路径 → 绝对路径。

    支持前缀：cache/...、wealth_code/cache/...、wealth_code/...，
    其余一律落到 REPO_ROOT（用于 config/ 等顶层资源）。
    """
    p = Path(rel)
    parts = p.parts
    if parts[:2] == ("wealth_code", "cache"):
        return WEALTH_CACHE_ROOT.joinpath(*parts[2:])
    if parts[:1] == ("cache",):
        return CACHE_ROOT.joinpath(*parts[1:])
    if parts[:1] == ("wealth_code",):
        return (REPO_ROOT / "wealth_code").joinpath(*parts[1:])
    return REPO_ROOT / rel


def try_resolve(rel: str) -> Path | None:
    """resolve 的越界保护版：仅允许 cache/ 与 wealth_code/cache/ 前缀，
    其余返回 None（防止业务代码越权访问仓库任意路径）。"""
    parts = Path(rel).parts
    if parts[:1] == ("cache",) or parts[:2] == ("wealth_code", "cache"):
        return resolve(rel)
    return None
