#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日级发布闸回归锁（2026-11-09 · 11-09 复盘卡连推 3 次事故）。

被锁的行为：
1. 同一张卡（按文件名身份）当日首次推送 → 放行；
2. 已推过 + 内容改了 + 无 --republish → **拒绝**（这正是事故路径：改一版推一版）；
3. 已推过 + 显式 `--republish "原因"` → 放行，原因进台账；
4. `--force` 语义只跳同内容去重，**不得**跳日级闸；
5. 测试群/异动群不拦（高频日常卡不适用）；
6. 不同卡名互不影响；台账按 30 天滚动清理。
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("push_card_mod", ROOT / "scripts" / "push_card.py")
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

PROD = "feishu_webhook_prod"
CARD_A = "/tmp/review_2026-11-09.json"
CARD_B = "/tmp/review_2026-11-10.json"


@pytest.fixture(autouse=True)
def _isolate_ledger(tmp_path, monkeypatch):
    """台账隔离：绝不写生产 cache/publish_ledger.json。"""
    ledger = tmp_path / "publish_ledger.json"
    monkeypatch.setattr(pc, "_publish_ledger_path", lambda: str(ledger))
    return ledger


def _card(tag: str) -> dict:
    return {"title": f"复盘 {tag}", "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": tag}}]}


def test_first_push_allowed():
    ok, msg = pc.publish_gate(CARD_A, PROD)
    assert ok is True and msg == ""


def test_second_push_blocked_without_republish():
    pc.publish_mark(CARD_A, _card("v1"))
    ok, msg = pc.publish_gate(CARD_A, PROD)          # 内容换成 v2 也一样：身份不看内容
    assert ok is False
    assert "已推送 1 次" in msg
    assert "--republish" in msg


def test_force_cannot_bypass_gate():
    """--force 只跳同内容去重；闸函数本身不接收 force 参数 = 结构上不可绕。"""
    import inspect
    params = list(inspect.signature(pc.publish_gate).parameters)
    assert "force" not in params
    pc.publish_mark(CARD_A, _card("v1"))
    assert pc.publish_gate(CARD_A, PROD, None)[0] is False


def test_republish_with_reason_allowed_and_recorded():
    pc.publish_mark(CARD_A, _card("v1"))
    ok, msg = pc.publish_gate(CARD_A, PROD, "错字订正")
    assert ok is True and "错字订正" in msg
    pc.publish_mark(CARD_A, _card("v2"), "错字订正")
    db = json.loads(Path(pc._publish_ledger_path()).read_text(encoding="utf-8"))
    rec = db["review_2026-11-09"]
    assert rec["count"] == 2
    assert rec["republish_reasons"] == ["错字订正"]
    assert len(rec["hashes"]) == 2


def test_test_group_never_blocked():
    pc.publish_mark(CARD_A, _card("v1"))
    assert pc.publish_gate(CARD_A, "feishu_webhook_test")[0] is True
    assert pc.publish_gate(CARD_A, "feishu_webhook_intraday")[0] is True


def test_identity_isolated_by_card_name():
    pc.publish_mark(CARD_A, _card("v1"))
    assert pc.publish_gate(CARD_B, PROD)[0] is True


def test_intraday_hunter_card_not_gated():
    """⛔ 关键防误伤：盘中猎手 limao_card.json 一天推 5~6 次是设计行为，绝不能被日级闸拦。"""
    p = "/tmp/limao_card.json"
    pc.publish_mark(p, _card("v1"))
    for _ in range(6):
        assert pc.publish_gate(p, PROD)[0] is True
    assert pc.publish_gate("/tmp/card_20261109_153902.json", PROD)[0] is True
    assert pc.publish_gate(CARD_A, PROD)[0] is True      # review 类才受管


def test_only_review_cards_use_ledger():
    pc.publish_mark("/tmp/limao_card.json", _card("v1"))
    db = json.loads(Path(pc._publish_ledger_path()).read_text(encoding="utf-8")) \
        if Path(pc._publish_ledger_path()).exists() else {}
    assert db == {}


def test_identity_ignores_content_and_mtime():
    """同一文件名 + 完全不同内容 = 同一身份（事故根因：按内容 hash 判身份）。"""
    pc.publish_mark(CARD_A, _card("首版"))
    assert pc.publish_identity(CARD_A) == pc.publish_identity("/any/dir/review_2026-11-09.json")
    assert pc.publish_gate(CARD_A, PROD)[0] is False


def test_ledger_prunes_stale_entries(_isolate_ledger):
    _isolate_ledger.write_text(json.dumps({
        "old_card": {"count": 1, "last_ts": 0},
        "fresh_card": {"count": 1, "last_ts": __import__("time").time()},
    }), encoding="utf-8")
    pc.publish_mark(CARD_A, _card("v1"))
    db = json.loads(_isolate_ledger.read_text(encoding="utf-8"))
    assert "old_card" not in db
    assert "fresh_card" in db
    assert "review_2026-11-09" in db


def test_corrupt_ledger_degrades_to_allow(tmp_path, monkeypatch):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(pc, "_publish_ledger_path", lambda: str(bad))
    assert pc.publish_gate(CARD_A, PROD)[0] is True   # 读不了就不拦，绝不因台账坏了丢卡
