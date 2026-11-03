#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机构原话抽取回归（inst_hot_talk.py）

⛔ 全部走纯函数 + tmp 缓存，不发网络请求：
   _run_recall 被 monkeypatch 掉，fetch_one 的缓存目录也指到 tmp_path。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import inst_hot_talk as iht  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    talk = cache / "inst_hot_talk"
    talk.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(iht, "CACHE", cache)
    monkeypatch.setattr(iht, "TALK_DIR", talk)
    return tmp_path


def _row(title: str, chunks: list[str], inst: str = "", t: str = "2026-10-28 10:00:00"):
    return {"title": title, "chunks": chunks, "institution": inst, "time": t, "type": "comment"}


# ── 摘要抽取 ───────────────────────────────────────────────────────────────
def test_snippet_prefers_sentence_with_real_number():
    snip = iht._snippet("三环集团点评", [
        "公司经营稳健发展态势良好。"
        "26年前三季度营收115.03亿元，同比+76.74%。"
    ])
    assert "115.03" in snip


def test_snippet_drops_meta_sentences():
    """「证券研究报告」「业绩回顾」「维持评级」这类元信息不能当摘要。"""
    snip = iht._snippet("大金重工点评", [
        "看好4Q出海项目发运释放利润。证券研究报告。维持评级。"
        "1-3Q26实现营收43.63亿元，同比-5.05%。"
    ])
    assert "证券研究报告" not in snip
    assert "43.63" in snip


def test_snippet_drops_chart_boilerplate():
    snip = iht._snippet("大金重工", [
        "图表标题：图表3：大金重工PB-Bands；图表内容：该图展示估值带走势。"
        "海外订单在手超过40亿元，同比增长明显。"
    ])
    assert "图表标题" not in snip
    assert "40亿" in snip


def test_snippet_drops_retail_bragging():
    """散户炒股感言不是机构在聊的内容。"""
    snip = iht._snippet("初识某股", [
        "持股体验美滋滋，成本为负，年化收益率26%。"
        "25年海外收入占比总收入的85.22%。"
    ])
    assert "美滋滋" not in snip
    assert "85.22" in snip


def test_snippet_drops_conference_boilerplate():
    snip = iht._snippet("摩根士丹利周期论剑", [
        "各位上午好，欢迎来到在线直播。"
        "宁德时代2027年单位净利润约0.08元每瓦时。"
    ])
    assert "各位上午好" not in snip
    assert "0.08" in snip


def test_snippet_strips_title_prefix_and_keeps_punctuation():
    """正文开头重复标题要剥掉；剥完的句子之间不能黏连丢标点。"""
    snip = iht._snippet("中际旭创点评", [
        "中际旭创点评。2026H1营收417.78亿元，同比增长182.49%。归母净利润136.51亿元。"
    ])
    assert not snip.startswith("中际旭创点评")


# ── 反漂移 / 去重 / 排序 ───────────────────────────────────────────────────
def test_drift_row_without_name_is_rejected():
    """recall 语义检索会漂：正文夹带票名但实际在聊大盘，必须剔掉。"""
    rows = [_row("财通总量｜如何看待全球失衡风险", ["这两天市场没有强劲反弹，主要缺催化。"])]
    assert iht.pick_items(rows, "宁德时代") == []


def test_row_with_name_in_title_is_kept():
    rows = [_row("宁德时代：税收影响测算", ["宁德时代即便承担全部冲击，2027年单位净利约0.08元每瓦时。"])]
    got = iht.pick_items(rows, "宁德时代")
    assert len(got) == 1
    assert got[0]["snippet"]


def test_duplicate_repost_of_same_report_removed():
    """两个号转述同一份研报（数字高度重合）只留一条。"""
    rows = [
        _row("高盛：上调兆易创新目标价", ["兆易创新目标价从391元上调至413元，2026年净利润上调160%。"], inst="甲"),
        _row("高盛最新调研", ["兆易创新目标价391元上调至413元，净利润预测上调160%、61%。"], inst="乙"),
    ]
    assert len(iht.pick_items(rows, "兆易创新")) == 1


def test_rows_with_institution_rank_first():
    rows = [
        _row("某票调研纪要", ["某票海外收入占比85.22%，产能布局近30国。"], inst=""),
        _row("中金：某票三季报", ["某票1-3Q26营收43.63亿元，同比-5.05%。"], inst="中金公司"),
    ]
    got = iht.pick_items(rows, "某票")
    assert got[0]["institution"] == "中金公司"


def test_noise_titles_filtered():
    for title in ("日报20261028", "月度金股报告", "26Q3主动公募科技持仓速览"):
        rows = [_row(title, ["某票营收115.03亿元。"], inst="某券商")]
        assert iht.pick_items(rows, "某票") == [], title


def test_max_items_cap():
    rows = [
        _row(f"机构观点{i}", [f"某票订单{i}亿元，同比增长。"], inst=f"机构{i}")
        for i in range(6)
    ]
    assert len(iht.pick_items(rows, "某票")) <= iht.MAX_ITEMS


# ── 缓存 ───────────────────────────────────────────────────────────────────
def test_cache_stores_raw_and_reuses(monkeypatch, tmp_path):
    """缓存必须存原始召回结果，改抽规则时不必重新调接口。"""
    calls = {"n": 0}

    def fake_recall(name, start, end):
        calls["n"] += 1
        return [_row("某票三季报", ["某票营收115.03亿元，同比+76.74%。"], inst="开源证券")]

    monkeypatch.setattr(iht, "_run_recall", fake_recall)
    items1 = iht.fetch_one("300408.SZ", "某票", "2026-10-29", "2026-10-20", "2026-10-28")
    items2 = iht.fetch_one("300408.SZ", "某票", "2026-10-29", "2026-10-20", "2026-10-28")
    assert calls["n"] == 1                      # 第二次命中缓存，没再请求
    assert len(items1) == len(items2) == 1
    raw = json.loads((iht.TALK_DIR / "2026-10-29_300408.json").read_text(encoding="utf-8"))
    assert raw["raw"] and "items" not in raw    # 只存 raw，抽取在读取时做


def test_refresh_forces_new_call(monkeypatch):
    calls = {"n": 0}

    def fake_recall(name, start, end):
        calls["n"] += 1
        return [_row("某票三季报", ["某票营收115.03亿元。"], inst="开源证券")]

    monkeypatch.setattr(iht, "_run_recall", fake_recall)
    iht.fetch_one("300408.SZ", "某票", "2026-10-29", "2026-10-20", "2026-10-28")
    iht.fetch_one("300408.SZ", "某票", "2026-10-29", "2026-10-20", "2026-10-28", refresh=True)
    assert calls["n"] == 2


def test_build_writes_payload_with_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(iht, "_run_recall",
                        lambda n, s, e: [_row(f"{n}三季报", [f"{n}营收115.03亿元。"], inst="开源证券")])
    payload = iht.build("2026-10-29", [("300408.SZ", "三环集团")], "2026-10-20", "2026-10-28")
    out = iht.CACHE / "inst_hot_talk_2026-10-29.json"
    assert out.exists()
    assert out.parent == tmp_path / "cache"
    assert payload["schema"] == iht.SCHEMA
    assert payload["stocks"]["300408.SZ"]["items"]
