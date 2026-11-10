#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡片数字自洽自查回归锁（2026-11-09 · 11-09 复盘卡连推 3 次事故）。

锁的是「改稿后方言残留」这一类错误：主题计数在加粗处改了、
句子里的另一处没改，卡就这么发出去了（13/12、11/10 各漏一处）。
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("card_selfcheck", ROOT / "scripts" / "card_selfcheck.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def _card(*lines: str) -> dict:
    return {"title": "t", "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]}


def _write(tmp_path, card) -> str:
    p = tmp_path / "review_2026-11-09.json"
    p.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ── 文本抽取 ──────────────────────────────────────────────────────────────
def test_extract_text_no_duplicate():
    """⛔ 事故前科：walk 既抽 content 又递归进 content 字符串 → 同一段出现两次。"""
    text = cs.extract_text(_card("机器人 12 只"))
    assert text.count("机器人 12 只") == 1


def test_extract_text_strips_text_tag():
    text = cs.extract_text(_card("<text_tag color='red'>机器人 12 只</text_tag>"))
    assert "<text_tag" not in text and "机器人 12 只" in text


# ── C1 主题计数唯一 ───────────────────────────────────────────────────────
def test_c1_catches_residual_old_count():
    """真实事故形态：加粗处已改 12，后面同一主题仍写着 13。"""
    text = cs.extract_text(_card(
        "**机器人 12 只**（全是首板·概念资金 +50.4 亿）",
        "**接棒候选在首板**　机器人 13 只首板里最先上 2 板的",
    ))
    fails = cs.check_theme_counts(text)
    assert fails and fails[0]["code"] == "C1"
    assert "13" in fails[0]["msg"] and "12" in fails[0]["msg"]


def test_c1_blind_spot_is_documented():
    """⛔ 能力边界（明确写下，别指望它兜住）：主题名被省略、只剩「翻到 13 只」
    这类句子片段时，C1 抓不到 —— 这正是 11-09 第三次推送漏掉的那两处。
    这类残留只能靠 `--verbose` 的全卡清单人工扫，故闸/清单缺一不可。
    """
    text = cs.extract_text(_card("**机器人 12 只**", "昨天这条线只有 5 只，今天翻到 13 只，但 13 只全是首板"))
    assert cs.check_theme_counts(text) == []          # 抓不到（已知边界）
    assert "13 只" in "\n".join(cs.dump_counts(text))  # 但清单里一定有，人工能看见


def test_c1_passes_when_consistent():
    text = cs.extract_text(_card(
        "**机器人 12 只**（全是首板）",
        "⚠️ 12 只里 0 只连板，今天翻到 12 只",
        "接棒候选在首板　机器人 12 只",
    ))
    assert cs.check_theme_counts(text) == []


def test_c1_ignores_sentence_fragments():
    """加粗片段是句子（含标点）时不得当主题，否则会造一堆假 FAIL。"""
    text = "**　拆开看是两件事：昨首板 21 只**（中位 +8.30%）"
    assert cs.check_theme_counts(text) == []


def test_c1_distinguishes_similar_names():
    """商业航天与卫星 / 冰雪与寒潮 各自独立，不互相污染。"""
    text = cs.extract_text(_card(
        "**商业航天与卫星 10 只**（资金 +49.7 亿）",
        "**冰雪与寒潮 7 只**（季节性）",
        "商业航天与卫星 10 只里没有连板",
        "冰雪与寒潮 7 只第一次成规模",
    ))
    assert cs.check_theme_counts(text) == []


# ── C2/C3 与当日涨停池对账 ────────────────────────────────────────────────
@pytest.fixture
def zt_tmp(tmp_path, monkeypatch):
    """造一个只含 3 只涨停、最高 2 板的当日池，并把 ROOT 指到 tmp。"""
    (tmp_path / "cache" / "zt_history").mkdir(parents=True)
    (tmp_path / "cache" / "zt_history" / "zt_2026-11-09.json").write_text(json.dumps({
        "date": "2026-11-09",
        "zt_count": 3,
        "max_boards": 2,
        "zt_list": [{"name": "闽发铝业", "code": "002578", "boards": 2},
                    {"name": "龙江交通", "code": "601188", "boards": 1},
                    {"name": "润贝航科", "code": "001316", "boards": 1}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cs, "ROOT", tmp_path)
    return tmp_path


def test_c2_catches_wrong_zt_total(zt_tmp):
    text = cs.extract_text(_card("涨停 54 只"))
    fails, _ = cs.check_pool_names(text, "2026-11-09")
    assert any(f["code"] == "C2" for f in fails)


def test_c2_passes_on_match(zt_tmp):
    text = cs.extract_text(_card("涨停 3 只"))
    fails, _ = cs.check_pool_names(text, "2026-11-09")
    assert not any(f["code"] == "C2" for f in fails)


def test_c2_lower_number_is_warn_not_fail(zt_tmp):
    """卡内只引用昨天涨停数（小于当日）→ 提示不阻断，避免误杀正常表述。"""
    text = cs.extract_text(_card("涨停 2 只（昨）"))
    fails, warns = cs.check_pool_names(text, "2026-11-09")
    assert not any(f["code"] == "C2" for f in fails)
    assert any(w["code"] == "C2" for w in warns)


def test_c3_catches_board_above_max(zt_tmp):
    text = cs.extract_text(_card("最高板 6"))
    fails, _ = cs.check_pool_names(text, "2026-11-09")
    assert any(f["code"] == "C3" for f in fails)


def test_c3_allows_board_within_max(zt_tmp):
    text = cs.extract_text(_card("最高板 2"))
    fails, _ = cs.check_pool_names(text, "2026-11-09")
    assert not any(f["code"] == "C3" for f in fails)


def test_missing_source_is_warn_not_fail(zt_tmp):
    fails, warns = cs.check_pool_names("涨停 3 只", "2026-01-01")
    assert fails == []
    assert any(w["code"] == "C0" for w in warns)


# ── 端到端 ────────────────────────────────────────────────────────────────
def test_selfcheck_end_to_end_fail(zt_tmp):
    p = _write(zt_tmp, _card("**机器人 12 只**", "接棒候选　机器人 13 只", "涨停 54 只"))
    ok, fails, _warns, _text = cs.selfcheck(p)
    codes = {f["code"] for f in fails}
    assert ok is False and {"C1", "C2"} <= codes


def test_selfcheck_end_to_end_pass(zt_tmp):
    p = _write(zt_tmp, _card("**机器人 3 只**", "今天翻到 3 只", "涨停 3 只", "最高板 2"))
    ok, fails, _warns, _text = cs.selfcheck(p)
    assert ok is True and fails == []


def test_date_from_filename():
    assert cs._guess_date("outputs/review_2026-11-09.json", None) == "2026-11-09"
    assert cs._guess_date("whatever.json", "2026-11-06") == "2026-11-06"
    assert cs._guess_date("whatever.json") is None
