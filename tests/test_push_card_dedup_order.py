# -*- coding: utf-8 -*-
"""push_card 去重时序回归锁（2026-09-17 修复 A4）。

病根：`dedup_check()` 原在**发送前**就写 hash → 发送失败时同内容在窗口内**无法重试**
（被自己的去重误拦）。修法：拆成
  · `dedup_check()` = **只读**检查（不写盘）
  · `dedup_mark()`   = **仅发送成功后**登记 hash

隔离：去重库路径 monkeypatch 到 tmp；不读 config/settings.json、不联网、不推送。
"""
import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("push_card_dd", SCRIPTS / "push_card.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)


def _card(title):
    return {"msg_type": "interactive", "card": {"header": {"title": {"content": title}}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": title}}]}


def _point_dedup(tmp_path, monkeypatch):
    f = tmp_path / "push_dedup.json"
    monkeypatch.setattr(pc, "_dedup_path", lambda: f)
    return f


def test_dedup_check_is_read_only(tmp_path, monkeypatch):
    """只读检查：不得创建/写入去重文件。"""
    f = _point_dedup(tmp_path, monkeypatch)
    dup, _ = pc.dedup_check(_card("A"))
    assert dup is False, "首次应为不重复"
    assert not f.exists(), "dedup_check 不得写盘（原缺陷就是发送前写盘）"


def test_dedup_mark_records(tmp_path, monkeypatch):
    """登记后才写盘，且随后检查命中重复。"""
    f = _point_dedup(tmp_path, monkeypatch)
    pc.dedup_mark(_card("A"))
    assert f.exists(), "dedup_mark 应写盘"
    dup, msg = pc.dedup_check(_card("A"))
    assert dup is True, "登记后同内容应判重复"
    assert "已推送" in msg


def test_failure_path_allows_retry(tmp_path, monkeypatch):
    """核心修复：检查→(发送失败，未登记)→再检查仍不重复 → 允许重试。"""
    _point_dedup(tmp_path, monkeypatch)
    assert pc.dedup_check(_card("B"))[0] is False
    # 模拟发送失败：不调用 dedup_mark
    assert pc.dedup_check(_card("B"))[0] is False, "失败未登记 → 必须允许重试"


def test_different_content_not_blocked(tmp_path, monkeypatch):
    _point_dedup(tmp_path, monkeypatch)
    pc.dedup_mark(_card("C"))
    assert pc.dedup_check(_card("D"))[0] is False, "不同内容不应被误拦"


def test_mark_ignores_volatile_fields(tmp_path, monkeypatch):
    """hash 忽略 timestamp/ts 等易变字段（登记与检查需一致）。"""
    f = _point_dedup(tmp_path, monkeypatch)
    c1 = _card("E")
    pc.dedup_mark(c1)
    c2 = _card("E")
    c2["elements"] = [{"tag": "div", "text": {"tag": "lark_md", "content": "E"}, "timestamp": 123}]
    assert pc.dedup_check(c2)[0] is True, "时间戳变化不应导致去重失效"
    assert json.loads(f.read_text(encoding="utf-8")), "去重库应含记录"
