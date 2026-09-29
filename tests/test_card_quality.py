#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡面质量自检回归锁（2026-09-18 学习层 B 项）。

锁定三类检测 + 两类误报防护：
  · 重复长文案 → 报（实证 09-16 `⚠️ 风险：20cm波动大要盯紧止损` x2）
  · 多票同名短标签（`🤖 AI 判断：观察` x13）→ **不报**（阈值过滤·属正常）
  · 结构字段值（`{"tag":"plain_text"}`）→ **不当文案**（曾误报）
  · 黑话禁项 → 报
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import card_quality_check as cq  # noqa: E402


def _write(tmp_path, card, name="card_20260918_120000.json"):
    p = tmp_path / name
    p.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return p


def _div(content):
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def test_repeat_detected(tmp_path):
    """同一长句出现 2 次 → 报重复（含句内容）。"""
    txt = "⚠️ 风险：20cm波动大要盯紧止损；明天高开容易回落别追高"
    p = _write(tmp_path, {"title": "T", "elements": [_div(txt), _div(txt)]})
    r = cq.check_card(p)
    assert r["repeat"], r
    assert "20cm波动大要盯紧止损" in r["repeat"][0]["sentence"]


def test_short_label_not_counted_as_repeat(tmp_path):
    """多票同名**短标签**（每票一行）不算重复——阈值 MIN_SENT_LEN 过滤。"""
    p = _write(tmp_path, {"title": "T",
                          "elements": [_div("🤖 AI 判断：观察"), _div("🤖 AI 判断：观察")]})
    r = cq.check_card(p)
    assert not r["repeat"], r


def test_per_stock_pool_call_label_not_counted_as_repeat(tmp_path):
    """9:25 逐票裁决标签可重复，但每行承载不同个股的独立结论，不应误报。"""
    line = "🤖 今日9:25判：放弃 （昨日预案：一般）"
    p = _write(tmp_path, {"title": "T", "elements": [_div(line), _div(line)]})
    r = cq.check_card(p)
    assert not r["repeat"], r


def test_struct_values_not_treated_as_text(tmp_path):
    """结构字段值（`plain_text`）不得被当文案（曾误报 `plain_text x2`）。"""
    p = _write(tmp_path, {"title": "T", "elements": [{"tag": "plain_text", "text_size": "normal"}]})
    r = cq.check_card(p)
    assert not r["repeat"], r


def test_jargon_detected(tmp_path):
    """黑话检测分级（2026-09-18 词表单一源 + 两级化）：
    Tier-1 机器黑话 → `jargon`（真问题）；Tier-2 行业术语 → `jargon_warn`（提示）；
    情绪周期体系词（亢奋/退潮/冰点）→ 均不报（用户体系语汇·保留）。"""
    p = _write(tmp_path, {"title": "T", "elements": [
        _div("今天市场很亢奋，梯队完整，承接不错"),
        _div("盘面状态 regime=ice"),
    ]})
    r = cq.check_card(p)
    assert "regime=" in r["jargon"]                            # Tier-1 真问题
    assert {"梯队", "承接"} <= set(r["jargon_warn"])            # Tier-2 提示
    assert "亢奋" not in r["jargon"] and "亢奋" not in r["jargon_warn"]


def test_module_label_exempt_body_word_caught(tmp_path):
    """模块名 `🧠 AI综合研判` 豁免；正文裸 `研判` 仍计入 Tier-2。"""
    p = _write(tmp_path, {"title": "T", "elements": [
        _div("**🧠 AI综合研判**"), _div("盘面状态｜研判 1/10")]})
    r = cq.check_card(p)
    assert "研判" in r["jargon_warn"]


def test_clean_card_no_issue(tmp_path):
    """干净卡 → 三项均无。"""
    p = _write(tmp_path, {"title": "T", "elements": [
        _div("**盘后复盘 09-18**"),
        _div("今天涨停 42 家，做多气氛 5 分，市场不冷不热"),
    ]})
    r = cq.check_card(p)
    assert not r["repeat"] and not r["jargon"] and not r["struct"], r


def test_unparsable_card_reported(tmp_path):
    """不可解析 → 结构异常（不崩）。"""
    p = tmp_path / "card_20260918_130000.json"
    p.write_text("{not json", encoding="utf-8")
    r = cq.check_card(p)
    assert r["struct"], r
