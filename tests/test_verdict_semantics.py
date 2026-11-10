# -*- coding: utf-8 -*-
"""竞价预期语义层（`core/verdict.py`）回归锁 —— 2026-11-10 抽离

背景：改一个词要动四处（判定 / 渲染 / 颜色判断 / 30 处测试字面量），漏一处**不报错**——
颜色静默变红，或「机械没达标该摘✅」失效。本文件锁住三件事：

  ① **文案单一源**：卡面标签必须等于 `verdict.tag()`，不得在本文件之外另写一份；
  ② **语义判负**：负向档 = 低于预期 / 明显低于预期 / 没达标（含旧存档里的带图标文本）；
  ③ **兼容旧数据**：`from_text` 能从带图标、带 `·辨真假高开` 后缀的旧文本里取到语义键。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT / "wealth_code"), str(ROOT / "wealth_code" / "pipelines")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import verdict as v          # noqa: E402


def test_labels_are_canonical():
    """文案与线上卡面一致（改文案 = 改这里，⛔ 不许在别处再写一份）。"""
    assert v.tag(v.Verdict.SUPER) == "🔺超预期"
    assert v.tag(v.Verdict.ABOVE) == "🔺高于预期"
    assert v.tag(v.Verdict.OK) == "✅符合预期"
    assert v.tag(v.Verdict.BELOW) == "⚠️低于预期"
    assert v.tag(v.Verdict.DEEP_BELOW) == "⚠️明显低于预期"
    assert v.tag(v.Verdict.SEALED) == "⛔竞价封死买不进"
    assert v.tag(v.Verdict.UNKNOWN) == ""
    assert v.label(v.Verdict.PENDING) == "待观察"


def test_negative_set():
    assert v.is_negative(v.Verdict.BELOW)
    assert v.is_negative(v.Verdict.DEEP_BELOW)
    assert v.is_negative(v.Verdict.REJECT)
    assert not v.is_negative(v.Verdict.OK)
    assert not v.is_negative(v.Verdict.SUPER)
    assert not v.is_negative(v.Verdict.SEALED), "封死是物理事实，不是『低于预期』"


def test_from_text_handles_legacy_decorations():
    assert v.from_text("⚠️低于预期") is v.Verdict.BELOW
    assert v.from_text("⚠️明显低于预期") is v.Verdict.DEEP_BELOW
    assert v.from_text("⚠️低于预期·辨真假高开") is v.Verdict.BELOW
    assert v.from_text("⛔竞价封死买不进") is v.Verdict.SEALED
    assert v.from_text("") is None
    assert v.from_text("完全无关的话") is None


def test_status_plain_is_single_source():
    assert v.status_plain("expected") == "符合预期"
    assert v.status_plain("weak") == "低于预期"
    assert v.status_plain("super") == "超预期"
    assert v.status_plain("没收录的键") == "没收录的键", "⛔ 未知键原样返回，不吞数据"


def test_build_cards_derives_from_module():
    """卡面文案表必须由 `core.verdict` 派生（⛔ 不许在 build_cards 里再定义一份）。"""
    import build_cards as bc
    assert bc._STATUS_PLAIN == {k: v.label(x) for k, x in v.STATUS_MAP.items()}
    assert bc._status_plain("weak") == v.tag(v.Verdict.BELOW).replace("⚠️", "", 1)


def test_no_chinese_substring_logic_in_business_code():
    """业务代码里不得再用中文子串做逻辑判断（有它就会漏，交 `scripts/check_verdict_semantics.py` 长期看守）。"""
    bad = []
    for p in (ROOT / "wealth_code").rglob("*.py"):
        if p.name == "verdict.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if any(k in line for k in ('"低于预期" in', '"符合预期" in', '"超预期" in')):
                bad.append(f"{p.relative_to(ROOT)}:{i}")
    assert not bad, f"发现中文子串逻辑：{bad}"
