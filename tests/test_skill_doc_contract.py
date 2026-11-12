#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能文档契约（2026-11-12 重构后锁死）。

防的是**回潮**：以后谁再往 SKILL.md 里追加旧口径（5% 硬线/三档建议/回撤压仓），
这条测试就红。文档在用户主目录，CI 无此目录时跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = Path.home() / ".workbuddy" / "skills" / "龙头战法交易员"
SKILL = SKILL_DIR / "SKILL.md"
ARCHIVE = SKILL_DIR / "references" / "archive_2026-11.md"

pytestmark = pytest.mark.skipif(not SKILL.exists(), reason="本机无技能目录（CI）")

REQUIRED = ("§0", "§1", "§2", "§3", "§4", "§5", "§6", "§7", "§8", "§9")
BANNED = ("5% 是硬线", "三档建议", "可等 / 只观察 / 放弃", "32 个归档日",
          "ai_tilt_v2", "成交额分阶段", "回撤只压仓")


def test_sections_present_and_short():
    text = SKILL.read_text(encoding="utf-8")
    for sec in REQUIRED:
        assert sec in text, f"缺少 {sec}"
    assert len(text.splitlines()) <= 400, "主文档超过 400 行，研究内容应进存档"


def test_no_stale_wording():
    text = SKILL.read_text(encoding="utf-8")
    hits = [w for w in BANNED if w in text]
    assert not hits, f"主文档出现旧口径：{hits}"


def test_symbols_referenced_exist():
    """文档里点名的开关必须在代码里真实存在。"""
    sys.path.insert(0, str(ROOT / "wealth_code"))
    from core import pool_call as pc
    for name in ("POOL_CALL_HIGH_GAP_REJECT", "POOL_CALL_FIRST_BOARD_BUY",
                 "POOL_CALL_REQUIRE_THEME", "POOL_CALL_CORE_WEAK_SECOND",
                 "POOL_CALL_ONE_WORD_DISCOUNT", "POOL_CALL_OLD_STOCK_OBSERVE",
                 "CATALYST_POSITION_WEIGHT", "CATALYST_SIZE"):
        assert hasattr(pc, name), f"文档引用的 {name} 在 pool_call 里不存在"


def test_archive_markers():
    assert ARCHIVE.exists(), "缺少历史存档"
    bad = [ln for ln in ARCHIVE.read_text(encoding="utf-8").splitlines()
           if ln.startswith("## ") and not ln.startswith("## ❄️")]
    assert not bad, f"存档有未标记章节：{bad[:3]}"


def test_no_duplicate_headings():
    heads = [ln.strip() for ln in SKILL.read_text(encoding="utf-8").splitlines() if ln.startswith("#")]
    dup = {h for h in heads if heads.count(h) > 1}
    assert not dup, f"重复标题：{sorted(dup)[:5]}"
