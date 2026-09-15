#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一 JSON 读写（2026-08-17 架构重构 P0-2 · 复用优先：合并 9 文件各自实现）

背景：build_cards/card_lint/gen_sentiment_summary/input_env/input_zt/push_card/
relay_score/theme_data/theme_rotation 各自 json.load(open(...))/Path.read_text，
容错/编码/异常处理不一致（空文件崩溃行为各异、缺中文乱码隐患）。

设计：
- load_json(path) → dict/list | None：文件缺失/损坏/非 JSON 返回 None（调用方防御）
- save_json(path, data)：ensure_ascii=False + indent=2 + 原子写（tmp+rename，防写一半）
- load_jsonl / append_jsonl：账本 jsonl 统一读写（account_nav_history 同款）
- 全部走 common.logging_setup（P0-3 统一日志）

铁律：任何脚本读写 JSON/JSONL，一律 import 本模块；禁止再自写 json.load(open())。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator, Optional


def _log(msg: str) -> None:
    try:
        from common.logging_setup import get_logger

        get_logger("json_io").warning(msg)
    except Exception:
        print(f"[json_io] {msg}")


def load_json(path: str | Path) -> Any:
    """读 JSON 文件 → dict/list；文件缺失/损坏/非 JSON 返回 None（不抛异常）。"""
    p = Path(path)
    try:
        if not p.exists():
            return None
        raw = p.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _log(f"JSON 读取失败 {p}: {e}")
        return None


def save_json(path: str | Path, data: Any, *, indent: int = 2) -> bool:
    """写 JSON 文件：ensure_ascii=False + 原子写（tmp→rename，防进程中断写一半）。
    成功返回 True；失败返回 False 并记日志。"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=indent)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except (OSError, TypeError) as e:
        _log(f"JSON 写入失败 {p}: {e}")
        return False


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """读 JSONL（每行一个 JSON 对象）→ list[dict]；坏行跳过并记日志。"""
    p = Path(path)
    out: list[dict[str, Any]] = []
    try:
        if not p.exists():
            return out
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except json.JSONDecodeError as e:
                _log(f"JSONL 坏行 {p}:{i} 跳过: {e}")
    except OSError as e:
        _log(f"JSONL 读取失败 {p}: {e}")
    return out


def append_jsonl(path: str | Path, record: dict[str, Any]) -> bool:
    """追加一行 JSON 到 JSONL（原子：单行写入）。成功 True。"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        return True
    except (OSError, TypeError) as e:
        _log(f"JSONL 追加失败 {p}: {e}")
        return False


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    """惰性迭代 JSONL（大文件省内存）。"""
    p = Path(path)
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj
            except json.JSONDecodeError:
                continue
    except OSError:
        return
