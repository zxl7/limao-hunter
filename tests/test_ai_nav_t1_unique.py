#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 轨 T+1 净值账本的不变量（2026-11-18 事故回归锁）。

事故：一轮重放把历史交易日反复结算，`ai_nav_history_t1.jsonl` 从 15 行涨到 339 行
（2026-09-14 被写 61 次），nav 一路复利到 461（起点 1.0），前端折线被压成贴地直线。

锁两条不变量：
  ① 每个 `trade_date` 只能有一行 —— 重复追加＝重复计收益；
  ② nav 必须落在可解释区间（0 < nav < 100）—— 461 这种值是链式复利的指纹。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "wealth_code" / "cache" / "ai_nav_history_t1.jsonl"


def _rows() -> list[dict]:
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_t1_ledger_has_one_row_per_trade_date():
    rows = _rows()
    assert rows, "T+1 账本为空"
    dup = {d: n for d, n in Counter(str(r.get("trade_date")) for r in rows).items() if n > 1}
    assert not dup, f"同一交易日出现多行（重复计收益）：{dup}"


def test_t1_ledger_nav_is_sane():
    rows = sorted(_rows(), key=lambda r: str(r.get("trade_date")))
    navs = [float(r["nav"]) for r in rows]
    assert all(0 < v < 100 for v in navs), f"nav 越界（疑似重复复利）：{min(navs)}~{max(navs)}"
