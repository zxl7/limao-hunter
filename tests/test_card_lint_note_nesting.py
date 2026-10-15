#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""card_lint · note 结构与 text_tag 嵌套熔断（2026-10-14）。

⛔ 事故：2026-10-14 盘中猎手 v5 预览卡推到测试群后用户反馈「**卡片里面没有样式**」。
根因 = 预览脚本手搓「完整飞书消息体」时把 note 写成

    {"tag": "note", "content": "📡 选股宝+东财 | 13:53 | AI盘中诊断"}

**缺 `elements` 数组**（飞书合法格式必须是
`{"tag":"note","elements":[{"tag":"lark_md","content":...}]}`），导致整卡被飞书降级渲染。
同一脚本还把「🏛️ 机构对账行」写成 text_tag 套 text_tag（宪法【突发题材榜 UI 标准模板】5 明确禁嵌套）。

旧 lint 的 check_elements 只判 `div`，note 完全不查 → 非法结构一路放行到群里。
本测试锁定两条熔断，防同类问题再进正式群。

对应源码：scripts/card_lint.py → check_note / 5.6 嵌套检查
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import card_lint as L  # noqa: E402


def _card(elements, title="盘中猎手·增量 | 10:00 | 测试 | 涨停1"):
    return {"msg_type": "interactive", "card": {
        "config": {"wide_screen_mode": True},
        "header": {"template": "red", "title": {"tag": "plain_text", "content": title}},
        "elements": elements}}


def _msgs(errors):
    return " | ".join(e["msg"] for e in errors)


# ── A. note 结构 ────────────────────────────────────────────────

def test_note_bare_content_fails():
    """裸 content 的 note（缺 elements）必须 FAIL —— 事故原形。"""
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": "正文 ok"}},
        {"tag": "note", "content": "📡 选股宝+东财 | 13:53 | AI盘中诊断"},
    ]))
    assert not ok
    assert "note 裸 content 非法" in _msgs(errs)
    assert "note 缺 elements 数组" in _msgs(errs)


def test_note_missing_elements_fails():
    """note 有别的键但没 elements（如只有 icon）同样 FAIL。"""
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": "正文 ok"}},
        {"tag": "note", "icon": {"tag": "standard_emoji", "key": "OK"}},
    ]))
    assert not ok
    assert "note 缺 elements 数组" in _msgs(errs)


def test_note_empty_elements_fails():
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": "正文 ok"}},
        {"tag": "note", "elements": []},
    ]))
    assert not ok
    assert "note 缺 elements 数组" in _msgs(errs)


def test_note_element_subitem_needs_content():
    """elements 里子项必须有非空 content。"""
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": "正文 ok"}},
        {"tag": "note", "elements": [{"tag": "lark_md", "content": "   "}]},
    ]))
    assert not ok
    assert "note.elements[0] 须为" in _msgs(errs)


def test_note_proper_structure_passes():
    """合法 note（lark_md 子项）必须放行 —— 生产卡实际写法。"""
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": "正文 ok"}},
        {"tag": "note", "elements": [{"tag": "lark_md", "content": "📡 选股宝+东财 | 13:45"}]},
    ]))
    assert ok, _msgs(errs)


def test_simple_format_string_note_passes():
    """简版结构 {"note": "字符串"} 由 push_card.normalize() 包装，lint 不得误伤。"""
    ok, errs, _ = L.lint_card({
        "title": "盘中猎手·增量 | 10:00 | 测试 | 涨停1",
        "color": "red",
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "正文 ok"}}],
        "note": "📡 选股宝+东财+猎手 | 10:00 | AI盘中诊断\n⚠️ 本报告基于公开信息分析，投资需谨慎",
    })
    assert ok, _msgs(errs)


# ── B. text_tag / font 嵌套 ─────────────────────────────────────

def test_nested_text_tag_fails():
    """text_tag 套 text_tag（机构对账行事故原形）必须 FAIL。"""
    bad = ("<text_tag color='violet'>🏛️ 机构今日点名：Top："
           "慈文传媒(002343) <text_tag color='red'>+10.05%</text_tag></text_tag>")
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": bad}},
    ]))
    assert not ok
    assert "嵌套" in _msgs(errs)


def test_split_tags_not_nested_passes():
    """平铺多标签（生产卡写法）不得误伤。"""
    good = ("🏛️ 机构今日点名·盘面：涨停 2 只｜拉升中 0 只｜Top："
            "<text_tag color='violet'>冰山冷热(000530) +9.95%</text_tag>·"
            "<text_tag color='violet'>慈文传媒(002343) +10.05%</text_tag>")
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": good}},
    ]))
    assert ok, _msgs(errs)


def test_font_inside_text_tag_fails():
    """font 嵌在 text_tag 内 → FAIL（宪法 ⓪-10 禁任一嵌套·2026-10-14 由 WARN 升级）。"""
    bad = "<text_tag color='red'>涨停 <font color='yellow'>9</font> 只</text_tag>"
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": bad}},
    ]))
    assert not ok
    assert "嵌套" in _msgs(errs)


def test_text_tag_inside_font_fails():
    """旧生产 verify 卡写法：<font color='grey'>…<text_tag color='blue'>缩量</text_tag>…</font>
    → 2026-10-14 起 **FAIL 硬阻断**（build_cards.py 4 处已清理为 `</font>` 之后）。"""
    line = ("　　<font color='grey'>预计0.0~1.2%高开 预期可信51%｜额0.33/0.46亿"
            " <text_tag color='blue'>缩量</text_tag>｜观察池｜🔥 机器人｜🏷 数字媒体</font>")
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": line}},
    ]))
    assert not ok
    assert "嵌套" in _msgs(errs)


def test_clean_verify_line_after_refactor_passes():
    """清理后的写法（缩量胶囊贴在 </font> 之后·拆两段 font）必须放行。"""
    line = ("　　<font color='grey'>预计0.0~1.2%高开 预期可信51%｜额0.33/0.46亿</font>"
            "<text_tag color='blue'>缩量</text_tag>"
            "<font color='grey'>｜观察池｜🔥 机器人｜🏷 数字媒体</font>")
    ok, errs, warns = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": line}},
    ]))
    assert ok, _msgs(errs)
    assert not any("嵌套" in w["msg"] for w in warns)


def test_many_sequential_tags_passes():
    """连续 10 个不嵌套的 text_tag（资金双列写法）不得误伤。"""
    good = "\n".join(f"<text_tag color='red'>板块{i} +{i}.0亿</text_tag>" for i in range(10))
    ok, errs, _ = L.lint_card(_card([
        {"tag": "div", "fields": [{"is_short": True, "text": {"tag": "lark_md", "content": good}}]},
    ]))
    assert ok, _msgs(errs)


def test_sibling_tags_after_close_pass():
    """先开后关再开（平级）不得被误判为嵌套。"""
    good = ("<text_tag color='red'>短剧</text_tag>｜"
            "<text_tag color='green'>转差</text_tag>｜"
            "<text_tag color='purple'>亚香股份(301220)</text_tag>")
    ok, errs, warns = L.lint_card(_card([
        {"tag": "div", "text": {"tag": "lark_md", "content": good}},
    ]))
    assert ok, _msgs(errs)
    assert not any("嵌套" in w["msg"] for w in warns), _msgs(errs)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
