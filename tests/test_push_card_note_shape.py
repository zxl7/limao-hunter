#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送卡片结构回归（2026-11-16 全天推送失败事故）

## 事故
`/tmp/limao_fts_card.json`（09:26 一进二卡）连续 22 次被飞书拒绝：
```
code 11246 / 200621 parse card json err
mismatched type with value ... at index 311~723
```
根因：拼卡脚本把 `note` 写成了**结构体**
```python
"note": {"tag": "note", "elements": [...]}
```
而 `push_card.normalize()` 当时无条件把 `note` 当成字符串塞进
`plain_text.content` → content 变成对象 → 飞书报类型不匹配。

## 本测试锁两件事
1. `note` 传字符串 / 传结构体，都要产出**合法卡片**（content 全部是字符串）；
2. 生成的 payload 里不允许出现非字符串的 `content`（这是 200621 的直接原因）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("push_card", ROOT / "scripts" / "push_card.py")
push_card = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(push_card)


def _contents(obj, acc=None):
    acc = [] if acc is None else acc
    if isinstance(obj, dict):
        if "content" in obj:
            acc.append(obj["content"])
        for v in obj.values():
            _contents(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _contents(v, acc)
    return acc


BASE = {"title": "📡 一进二机会 | 09:26", "color": "purple",
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**🚀 接近涨停**"}}]}


def test_note_as_string_is_wrapped_as_plain_text():
    card = push_card.normalize({**BASE, "note": "📡 狸猫猎手 | 09:26"})
    last = card["card"]["elements"][-1]
    assert last["tag"] == "note"
    assert last["elements"][0] == {"tag": "plain_text", "content": "📡 狸猫猎手 | 09:26"}


def test_note_as_element_is_appended_untouched():
    note = {"tag": "note", "elements": [{"tag": "lark_md", "content": "📡 狸猫猎手 | 09:26"}]}
    card = push_card.normalize({**BASE, "note": note})
    assert card["card"]["elements"][-1] == note


def test_all_content_fields_are_strings():
    """飞书 200621 的直接判据：content 必须是字符串。"""
    for note in ("📡 x", {"tag": "note", "elements": [{"tag": "lark_md", "content": "y"}]}):
        card = push_card.normalize({**BASE, "note": note})
        bad = [c for c in _contents(card) if not isinstance(c, str)]
        assert not bad, f"note={note!r} 产出了非字符串 content: {bad[:3]}"


def test_payload_is_json_serializable():
    card = push_card.normalize({**BASE, "note": {"tag": "note", "elements": []}})
    json.dumps(card, ensure_ascii=False)   # 不抛异常即通过
