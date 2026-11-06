# -*- coding: utf-8 -*-
"""竞价快照双守卫（2026-11-06）。

背景：`verify --fetch` 原先无条件覆写 `auction_quotes-<今日>.json`。launchd 09:26
用干净 09:25 报价冻结了 `pool_call`、发了卡、写了账本之后，任何再跑一次 fetch
（本任务兜底 / 人工复跑 / 09:30 后抓到累计额污染）都会覆写裁决、卡面、账本的
**共同输入**，破坏「三方同源」铁律。自 10-23 连续挂账，11-06 落地。

锁五条行为：
  ① 已有今日非空快照 → 复用、不覆写（默认）
  ② `VERIFY_FETCH_FORCE=1` → 强制覆写
  ③ 本次抓空 + 有旧快照 → 保留旧快照，绝不写空文件
  ④ 本次抓空 + 无旧快照 → 不落空文件
  ⑤ `VERIFY_FETCH_GUARD=0` → 逐字回退旧行为（无条件写）
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WC = ROOT / "wealth_code"
for p in (str(WC), str(WC / "scripts"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _mod():
    return importlib.import_module("scripts.run_wealth_code")


GOOD = {
    "002578": {"prev_close": 4.55, "auction_price": 5.01,
               "auction_amount_yi": 2.05, "time": "2026-11-06 09:25:00"},
    "601599": {"prev_close": 10.0, "auction_price": 11.0,
               "auction_amount_yi": 1.2, "time": "2026-11-06 09:25:02"},
}
NEWER = {
    "002578": {"prev_close": 4.55, "auction_price": 5.01,
               "auction_amount_yi": 9.99, "time": "2026-11-06 09:31:00"},
}


def _seed(tmp_path: Path, snap: dict | None) -> Path:
    p = tmp_path / "auction_quotes-2026-11-06.json"
    if snap is not None:
        p.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return p


# ── ① 已有今日快照 → 复用不覆写（核心：保护已冻结裁决的同源输入）──────────────
def test_existing_snapshot_reused_not_overwritten(tmp_path):
    p = _seed(tmp_path, GOOD)
    before = p.read_text(encoding="utf-8")
    got, act = _mod()._guard_auction_snapshot(p, NEWER)
    assert act == "reuse_existing"
    assert got == GOOD, "必须复用已冻结的今日快照"
    assert p.read_text(encoding="utf-8") == before, "⛔ 不得覆写已冻结快照"


# ── ② FORCE → 强制覆写 ────────────────────────────────────────────────────
def test_force_overwrites(tmp_path):
    p = _seed(tmp_path, GOOD)
    got, act = _mod()._guard_auction_snapshot(p, NEWER, force=True)
    assert act == "write"
    assert got == NEWER
    assert json.loads(p.read_text(encoding="utf-8")) == NEWER


# ── ③ 抓空 + 有旧快照 → 保留旧数据，不写空文件 ─────────────────────────────
def test_empty_fetch_keeps_previous_snapshot(tmp_path):
    p = _seed(tmp_path, GOOD)
    before = p.read_text(encoding="utf-8")
    got, act = _mod()._guard_auction_snapshot(p, {})
    assert act == "keep_on_empty"
    assert got == GOOD
    assert p.read_text(encoding="utf-8") == before, "⛔ 抓空不得写空文件覆盖"


# ── ③-b force 抓空也不许清空已有数据 ───────────────────────────────────────
def test_force_empty_still_keeps_previous(tmp_path):
    p = _seed(tmp_path, GOOD)
    before = p.read_text(encoding="utf-8")
    got, act = _mod()._guard_auction_snapshot(p, {}, force=True)
    assert act == "keep_on_empty"
    assert got == GOOD
    assert p.read_text(encoding="utf-8") == before


# ── ④ 首次运行 + 抓空 → 不落空文件 ─────────────────────────────────────────
def test_first_run_empty_writes_nothing(tmp_path):
    p = _seed(tmp_path, None)
    got, act = _mod()._guard_auction_snapshot(p, {})
    assert act == "write_empty_blocked"
    assert got == {}
    assert not p.exists(), "无历史快照时也不该落一个空文件"


# ── 首次运行 + 有数据 → 正常落盘 ───────────────────────────────────────────
def test_first_run_with_data_writes(tmp_path):
    p = _seed(tmp_path, None)
    got, act = _mod()._guard_auction_snapshot(p, GOOD)
    assert act == "write"
    assert got == GOOD
    assert json.loads(p.read_text(encoding="utf-8")) == GOOD


# ── ⑤ 守卫关闭 → 逐字回退旧行为（无条件写，含写空）────────────────────────
def test_guard_disabled_legacy_behaviour(tmp_path):
    p = _seed(tmp_path, GOOD)
    got, act = _mod()._guard_auction_snapshot(p, NEWER, guard=False)
    assert act == "write_legacy"
    assert got == NEWER
    assert json.loads(p.read_text(encoding="utf-8")) == NEWER


def test_guard_disabled_writes_empty_like_before(tmp_path):
    """旧行为确实会写空文件 —— 这是回退开关的语义，必须如实保留。"""
    p = _seed(tmp_path, GOOD)
    got, act = _mod()._guard_auction_snapshot(p, {}, guard=False)
    assert act == "write_legacy"
    assert got == {}
    assert json.loads(p.read_text(encoding="utf-8")) == {}


# ── 开关读取 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("val,expect", [
    (None, True), ("1", True), ("0", False), ("false", False), ("", False),
])
def test_guard_enabled_env(monkeypatch, val, expect):
    if val is None:
        monkeypatch.delenv("VERIFY_FETCH_GUARD", raising=False)
    else:
        monkeypatch.setenv("VERIFY_FETCH_GUARD", val)
    assert _mod()._fetch_guard_enabled() is expect


# ── 损坏/空文件视为「无快照」，不应被当成有效数据复用 ──────────────────────
def test_corrupt_snapshot_treated_as_absent(tmp_path):
    p = tmp_path / "auction_quotes-2026-11-06.json"
    p.write_text("{not json", encoding="utf-8")
    assert _mod()._read_auction_snapshot(p) == {}
    got, act = _mod()._guard_auction_snapshot(p, GOOD)
    assert act == "write"
    assert got == GOOD
