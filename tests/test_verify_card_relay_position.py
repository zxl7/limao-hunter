#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价验证卡·动态仓位建议块（relay_position）回归锁（2026-10-13 用户需求）。

锁死三件事：
· `_relay_position_block` 把方案渲染成大白话（过 card_copy_rules 白话门禁·无机器黑话）。
· 禁买（回撤破硬线）→ 渲染「今日禁买」且**不**给仓位数字（诚实）。
· `build_verify_card` 确实把该块接进 09:25 竞价验证卡（而非误放到盘后 eod 卡）。

⛔ 白话门禁是推送前硬校验（card_lint），本测试直接复用 card_copy_rules.scan 同词表，
   与推送门禁同源——一旦块里出现「反馈矩阵/可信度/题材板块效应/冰点禁买」等黑话即 FAIL。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE / "wealth_code" / "pipelines"))
sys.path.insert(0, str(BASE / "scripts"))

import card_copy_rules as ccr  # noqa: E402
from build_cards import _relay_position_block, build_verify_card  # noqa: E402
from core import relay_position as rp  # noqa: E402


def _scan_block(block) -> tuple[list[str], list[str]]:
    texts = []

    def walk(o):
        if isinstance(o, dict):
            tx = o.get("text")
            if isinstance(tx, dict) and isinstance(tx.get("content"), str):
                texts.append(tx["content"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(block)
    joined = "\n".join(texts)
    return ccr.scan(joined)


def test_block_neutral_renders_plain_language():
    """中档方案 → 大白话：行情强弱 + 同时持有只数 + 单票/总仓上限 + 题材沉淀，无黑话。"""
    plan = rp.build_plan(
        strength=0.6, tier="neutral", theme_confidence=0.52, known_ratio=0.7,
        dd=3.0, theme_day_count=31,
        theme_info={"day_count": 31, "theme_count": 164, "confidence": 0.517},
    )
    block = _relay_position_block(plan)
    assert block is not None, "neutral 且可买 → 必须渲染块"
    content = block[0]["text"]["content"]
    assert "今日接力仓位建议" in content
    assert "行情强弱：中·正常" in content
    assert "今天最多同时持有 2 只" in content
    assert "每只不超过 40%" in content
    assert "总仓位不超过 60%" in content
    assert "题材库已沉淀 31 天（共 164 个题材）" in content
    fails, warns = _scan_block(block)
    assert fails == [], f"白话门禁命中机器黑话: {fails}"
    # Tier-2 行业术语提示也不应出现（用词已大白话化）
    assert warns == [], f"出现未白话化行业术语: {warns}"


def test_deep_drawdown_renders_shrunk_position_not_ban():
    """2026-10-13 口径 B：回撤过警戒线 → 渲染「已压小」而非「禁买」，且仍给仓位数字。"""
    plan = rp.build_plan(
        strength=0.5, tier="neutral", dd=12.0, theme_day_count=31,
        theme_info={"day_count": 31, "theme_count": 164, "confidence": 0.517},
    )
    assert plan["can_buy"] is True                       # ⛔ 不再禁买
    block = _relay_position_block(plan)
    assert block is not None
    content = block[0]["text"]["content"]
    assert "禁买" not in content.replace("不禁买", "")     # 没有"禁买"结论
    # ⚠️ 2026-10-21：原句「（按回撤压到原来的 45%·动态控制不禁买）」含系统注释词，
    #    改为「（因为前面亏了 X%，按比例收着做）」——语义等价、去掉内部术语。
    assert "压小" in content or "按比例收着做" in content
    assert "同时持有" in content                          # 仍给仓位数字（可买）
    fails, warns = _scan_block(block)
    assert fails == [], f"白话门禁命中机器黑话: {fails}"


def test_block_disabled_returns_none():
    """模块开关关闭 → 不渲染（回退旧行为·不强行塞块）。"""
    plan = rp.build_plan(strength=0.6, tier="neutral")
    plan["enabled"] = False
    assert _relay_position_block(plan) is None


def test_verify_card_includes_relay_block(monkeypatch):
    """集成锁：build_verify_card 把动态仓位块接进 09:25 竞价验证卡（非 eod 卡）。

    用 monkeypatch 注入一个**中性可买**方案，确认卡面出现中性文案而非真实账本的「禁买」，
    从而验证块确实由 verify 链路渲染、且口径与 relay_position 方案同源。
    """
    injected = rp.build_plan(
        strength=0.6, tier="neutral", theme_confidence=0.52, known_ratio=0.7,
        dd=3.0, theme_day_count=31,
        theme_info={"day_count": 31, "theme_count": 164, "confidence": 0.517},
    )

    def fake_plan(*a, **k):
        return injected

    monkeypatch.setattr(rp, "build_plan_from_files", fake_plan)

    verify_result = {
        "summary": {"trade_date10": "2026-10-13", "total_candidates": 2,
                    "buy_signals": 1, "hit_signals": 1, "covered": 31,
                    "nav_latest": 0.98, "caliber": "ai", "mech_buy_signals": 3},
        "payload": {"records": [
            {"code": "600693", "name": "测试A", "theme": "PCB",
             "performance": {"open_check": {"gap_pct": 3.2, "decision_status": "super",
                                            "auction_amount_yi": 6.0}}},
            {"code": "002343", "name": "测试B", "theme": "粮食概念",
             "performance": {"open_check": {"gap_pct": 1.1, "decision_status": "expected",
                                            "auction_amount_yi": 4.0}}},
        ]},
    }
    card = build_verify_card(verify_result)

    found = []

    def walk(o):
        if isinstance(o, dict):
            tx = o.get("text")
            if isinstance(tx, dict) and isinstance(tx.get("content"), str) \
                    and "今日接力仓位建议" in tx["content"]:
                found.append(tx["content"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(card)
    assert found, "竞价验证卡必须包含动态仓位块"
    assert "行情强弱：中·正常" in found[0]
    # 注入为可买 → 不应出现禁买文案
    assert "今日禁买" not in found[0]
    # 白话门禁
    fails, warns = ccr.scan(found[0])
    assert fails == [], f"白话门禁命中机器黑话: {fails}"
