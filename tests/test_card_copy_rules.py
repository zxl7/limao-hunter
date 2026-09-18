#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡片白话门禁回归锁（2026-09-18 复盘卡黑话修复）。

背景：禁项词表曾散落 **5 处且互不一致**（tests 四套 + card_quality_check 一套），
而 **LLM 拼的复盘卡长期无任何词表约束** → 09-17 复盘卡残留
`研判/容量核心/梯队/承接/亢奋/退潮` 18 处。

本测试锁定：
  ① 词表单一源（card_copy_rules）三组互斥、自检通过；
  ② card_lint 推送前门禁分级：Tier-1 机器黑话 → FAIL 阻断 / Tier-2 行业术语 → WARN 不阻断；
  ③ 模块名豁免：`🧠 AI综合研判` 不算黑话，但正文裸 `研判` 仍被抓；
  ④ 保留词（A股通用 + 情绪周期体系词）不误报；
  ⑤ 真实 09-17 复盘卡场景可复现（抓到 4 个真术语、放行 退潮/亢奋）。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import card_copy_rules as R          # noqa: E402
import card_lint as L               # noqa: E402
import card_quality_check as cq     # noqa: E402


def _card(text: str, title: str = "📊 测试卡·增量"):
    return {"title": title,
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}]}


def _lint(text: str):
    return L.lint_card(_card(text))


# ── ① 词表单一源 / 自洽性 ────────────────────────────────────────────────

def test_wordlist_self_check_clean():
    """词表自检：KEEP 不得与 FAIL/WARN 重叠，FAIL 与 WARN 不得重叠。"""
    assert R.SELF_CHECK_ERRORS == [], R.SELF_CHECK_ERRORS


def test_quality_check_uses_same_source():
    """card_quality_check 与 card_lint 必须引用**同一个**词表模块对象（防再次漂移）。"""
    assert cq._ccr is R
    assert not hasattr(cq, "JARGON"), "card_quality_check 不得再自维护 JARGON 词表"


def test_keep_list_excludes_tier1_tier2():
    """保留词（含情绪周期体系词）不得出现在任何禁项组里。"""
    for w in ("冰点", "修复", "亢奋", "退潮", "分歧", "龙头", "涨停"):
        assert w in R.PLAIN_KEEP, w
        assert w not in R.PLAIN_FAIL and w not in R.PLAIN_WARN, w


# ── ② 门禁分级 ──────────────────────────────────────────────────────────

def test_tier1_machine_jargon_blocks_push(monkeypatch):
    """Tier-1 机器黑话（内部字段/英文档位）→ **阻断期内** lint FAIL 阻断。"""
    monkeypatch.setattr(L, "PLAIN_GATE_ENFORCE_FROM", "1970-01-01")   # 立即生效
    for bad in ("盘面状态 regime=ice", "expected 2.5", "buy/pending", "MA5 跌破"):
        ok, errs, _ = _lint(bad)
        assert ok is False, bad
        assert any("白话门禁" in e["msg"] for e in errs), bad


def test_tier1_observation_period_only_warns(monkeypatch):
    """⛔ 首发观察期（生效日前）：Tier-1 也只 WARN·**不阻断**（防首发误阻断丢卡）。

    背景：门禁首次上线时脚本卡实测有命中（核按钮/回流卡 `MA5`），而 LLM 现场拼的卡
    （猎手/复盘/龙虎榜/机构研报）无法穷举 → 首日即硬阻断＝一旦命中就整卡推不出去。
    """
    monkeypatch.setattr(L, "PLAIN_GATE_ENFORCE_FROM", "2099-01-01")   # 未生效
    ok, errs, warns = _lint("盘面状态 regime=ice")
    assert ok is True and errs == []
    assert any("观察期" in w["msg"] for w in warns)


def test_tier2_industry_term_warns_not_blocks():
    """Tier-2 行业术语 → WARN 提示但**不阻断**（防 LLM 拼卡被卡死丢卡）。"""
    ok, errs, warns = _lint("🧠 AI综合研判：同方向的票一起涨")
    assert ok is True and errs == []
    ok2, errs2, warns2 = _lint("容量核心 众泰汽车 晋级")
    assert ok2 is True and errs2 == []
    assert any("白话门禁" in w["msg"] for w in warns2)
    assert any("板块大票" in w["msg"] for w in warns2)


def test_strict_switch_escalates_tier2(monkeypatch):
    """PLAIN_GATE_STRICT=True → Tier-2 升级为阻断（可逆开关）。"""
    monkeypatch.setattr(L, "PLAIN_GATE_STRICT", True)
    ok, errs, _ = _lint("容量核心 众泰汽车")
    assert ok is False and any("白话门禁" in e["msg"] for e in errs)


def test_plain_gate_switch_off(monkeypatch):
    """ENABLE_PLAIN_GATE=False → 一键回退（2026-09-18 前行为）。"""
    monkeypatch.setattr(L, "ENABLE_PLAIN_GATE", False)
    ok, errs, warns = _lint("盘面状态 regime=ice")
    assert ok is True
    assert not any("白话门禁" in w["msg"] for w in warns)


# ── ③ 模块名豁免 ────────────────────────────────────────────────────────

def test_module_label_exempt_but_body_word_caught():
    """`🧠 AI综合研判` 模块名豁免；正文裸 `研判` 仍判为禁项。"""
    f, w = R.scan("**🧠 AI综合研判**")
    assert w == [], f"模块名不该报禁项: {w}"
    f2, w2 = R.scan("盘面状态：亢奋隔日归零｜研判 1/10")
    assert "研判" in w2


def test_strip_labels_only_removes_labels():
    s = R.strip_labels("**🧠 AI综合研判** 研判 1/10")
    assert "AI综合研判" not in s and "研判" in s


# ── ④ 保留词 / 词边界 ──────────────────────────────────────────────────

def test_keep_words_not_flagged():
    f, w = R.scan("涨停 12 家·最高 5 板｜连板晋级 22%｜核按钮 3 只｜竞价高开 1.8%｜龙头 众泰汽车")
    assert f == [] and w == [], (f, w)


def test_english_word_boundary():
    """英文词需词边界：`pendingX` 不该命中 `pending`。"""
    assert R._hit("pending", "pendingX") is False
    assert R._hit("pending", "状态 pending") is True


# ── ⑤ 真实 09-17 复盘卡场景复现 ─────────────────────────────────────────

def test_real_review_card_20260917():
    """实测复现：09-17 复盘卡 → 抓到 4 真术语、放行 退潮/亢奋（体系词）。"""
    p = ROOT / "cache" / "card_2026-09-17.json"
    if not p.exists():
        return      # 卡被清理时跳过（非失败）
    raw = json.loads(p.read_text(encoding="utf-8"))
    card = raw.get("card") if isinstance(raw, dict) and "card" in raw else raw

    def strings(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for k, v in o.items():
                if k in ("tag", "template", "color", "msg_type", "type", "mode",
                         "size", "width", "wide_screen_mode", "schema", "font"):
                    continue
                yield from strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from strings(v)

    f, w = R.scan("\n".join(strings(card)))
    assert f == [], f"复盘卡不该有 Tier-1 阻断项: {f}"
    assert {"容量核心", "梯队", "承接", "研判"} <= set(w), w
    for keep in ("亢奋", "退潮", "冰点"):
        assert keep not in w, f"体系情绪词不该误报: {keep}"
