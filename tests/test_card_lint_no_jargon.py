#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡片文案闸：推送卡里不得出现工程词（2026-11-16 用户「做！」）

## 为什么用 AST 而不是 grep
注释和 docstring 里出现「快照/兜底」是正常的（那是写给我们自己看的）。
grep 会把这些算进去 → 天天误报。AST 只取**字符串字面量**，并且跳过 docstring，
正好等于「会出现在卡片上的字」。

## 规则
禁用词：快照 / 落盘 / 兜底 / 降级 / 阈值 / 产物 / 字段 / 接口 / 路由
（来自 `架构/界面用语.md`；交易术语如涨停/连板/晋级率**不在**禁用范围）

## 例外
统计口径的历史说明允许保留在**注释**里；卡片正文不允许。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BANNED = ("快照", "落盘", "兜底", "降级", "阈值", "产物", "字段", "接口", "路由")

# 只扫「会生成卡片文案」的文件；日志脚本（short_spirit 等）打印的是 stderr，
# 不是给用户看的卡片，故不在本闸范围。
CARD_FILES = (
    ROOT / "wealth_code" / "pipelines" / "build_cards.py",
    ROOT / "scripts" / "first_to_second_alert.py",
)


def _docstring_ids(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _hits(path: Path) -> list[tuple[int, str, str]]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = _docstring_ids(tree)
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in doc:
            continue
        for word in BANNED:
            if word in node.value:
                hits.append((node.lineno, word, node.value[:80].replace("\n", " ")))
    return hits


def test_card_text_has_no_engineering_jargon():
    problems: list[str] = []
    for f in CARD_FILES:
        for lineno, word, snippet in _hits(f):
            problems.append(f"{f.relative_to(ROOT)}:{lineno} 用了「{word}」→ {snippet}")
    assert not problems, "卡片里出现工程词（改成人话，或写进注释）:\n" + "\n".join(problems)


def test_banned_list_matches_wording_doc():
    """禁用词必须和 `架构/界面用语.md` 对得上，避免两处各说各话。"""
    doc = (ROOT / "架构" / "界面用语.md").read_text(encoding="utf-8")
    missing = [w for w in BANNED if w not in doc]
    assert not missing, f"这些禁用词没写进用语规范: {missing}"
