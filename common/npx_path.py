#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""npx 定位工具（2026-09-12 建立 · 根治硬编码版本路径）。

背景（实证事故）：WorkBuddy 托管 node 运行时目录会随升级迁移
（2026-09-12 实测 `22.22.2-2` → `22.22.2-3`），而三处脚本硬编码了完整路径
（tx_query / lhb_fetch / intraday_pulse）→ 运行时迁移当日，
P2-19 风险归档 8 只**全部 unavailable**、龙虎榜数据源同时失效，且 `_run` 吞掉 stderr
导致排障困难（本地复现耗时 0s 才发现 `No such file or directory`）。

铁律：⛔ **禁止在任何脚本硬编码 node 版本路径**；一律 `from common.npx_path import find_npx`。

查找优先级：
  1. 环境变量 `LIMAO_NPX`（显式覆盖·应急）
  2. `~/.workbuddy/binaries/node/versions/*/bin/npx`（**优先 22.x 最高版本**，其次任意可用）
  3. `PATH` 中的 npx（shutil.which）
返回 None 表示不可用（调用方须记录并降级，不静默）。
"""
from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

_VERSIONS_DIR = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"


def _version_key(name: str):
    """22.22.2-3 → (22, 22, 2, 3) 便于排序取最高版本。"""
    parts = []
    for seg in str(name).replace("-", ".").split("."):
        parts.append(int(seg) if seg.isdigit() else 0)
    return tuple(parts)


@lru_cache(maxsize=1)
def find_npx() -> str | None:
    """定位可用的 npx 可执行文件（结果进程内缓存）。不可用返回 None。"""
    env = os.environ.get("LIMAO_NPX")
    if env and Path(env).exists():
        return env
    if _VERSIONS_DIR.exists():
        cands = [p for p in _VERSIONS_DIR.iterdir() if (p / "bin" / "npx").exists()]
        if cands:
            preferred = [p for p in cands if p.name.startswith("22.")] or cands
            best = max(preferred, key=lambda p: _version_key(p.name))
            return str(best / "bin" / "npx")
    return shutil.which("npx")


def npx_available() -> bool:
    return bool(find_npx())


def describe() -> str:
    """诊断用：当前解析到的 npx 路径。"""
    p = find_npx()
    return p or "❌ 未找到可用 npx"
