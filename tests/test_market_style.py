#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🌊 指数环境·风格切换层回归锁（2026-10-19 用户「学习上海 7.10 聚会心得」）。

锁死六条铁律：
1. 主升必须**四个条件全满足**（三指数皆红 / idx_avg≥0.8 / 量能≥2.5万亿 / 情绪≥7）——缺一不成立
2. 三指数**不同向**即判分歧，不看平均涨幅（一红一绿平均值可能还是正的）
3. 缺数据 → neutral + data_gap，⛔ 不猜、⛔ 不拿 0 冒充
4. 标注**只加标记**：不改 raw_score、不改顺序、不塞池
5. 分歧期「高位 + 封单/流通<2%」优先判**高位薄封**，不得因是龙一就放行
6. 无趋势提示只在"真的没方向"时给——走弱趋势（promote_downtrend/avg_pct_weak）不算没方向
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))

from core import market_style as mst  # noqa: E402
from pipelines import build_cards as bc  # noqa: E402


def _idx(*pcts):
    names = ["上证指数", "深证成指", "创业板指"]
    return [{"name": n, "pct": p} for n, p in zip(names, pcts)]


def _fb(**over):
    d = {"sentiment_score": 7.5, "market_amount_yi": 26000.0, "trend": {}}
    d.update(over)
    return d


# ── detect_style ──────────────────────────────────────────


def test_attack_all_conditions():
    s = mst.detect_style(_idx(1.2, 1.0, 0.9), _fb())
    assert s["style"] == mst.STYLE_ATTACK
    assert s["label"] == "主升"
    assert "最强" in s["trading_rule"]


def test_attack_requires_volume():
    """量能不够 → 不是主升（放量是主升的定义之一）。"""
    s = mst.detect_style(_idx(1.2, 1.0, 0.9), _fb(market_amount_yi=15000.0))
    assert s["style"] != mst.STYLE_ATTACK


def test_attack_requires_sentiment():
    s = mst.detect_style(_idx(1.2, 1.0, 0.9), _fb(sentiment_score=5.0))
    assert s["style"] != mst.STYLE_ATTACK


def test_attack_requires_all_up():
    """一绿即不成立（创业板 -0.3 时 avg 仍可能 >0.8，但已经不是三指数皆红）。"""
    s = mst.detect_style(_idx(1.5, 1.2, -0.3), _fb())
    assert s["style"] == mst.STYLE_SHIFT


def test_mixed_is_shift():
    s = mst.detect_style(_idx(0.52, 0.25, -0.8), _fb(sentiment_score=1.0))
    assert s["style"] == mst.STYLE_SHIFT
    assert s["label"] == "分歧"
    assert "逆势" in s["trading_rule"]
    assert s["ice"] is True


def test_all_down_is_shift():
    s = mst.detect_style(_idx(-1.0, -1.2, -1.5), _fb(sentiment_score=3.0))
    assert s["style"] == mst.STYLE_SHIFT


def test_small_aligned_up_is_neutral():
    s = mst.detect_style(_idx(0.3, 0.2, 0.1), _fb(sentiment_score=5.0))
    assert s["style"] == mst.STYLE_NEUTRAL


def test_missing_index_is_neutral_with_gap():
    s = mst.detect_style([], _fb())
    assert s["style"] == mst.STYLE_NEUTRAL
    assert "index" in s["data_gap"]


def test_missing_pct_rows_skipped():
    s = mst.detect_style([{"name": "上证指数"}, {"name": "深证成指", "pct": 1.1},
                          {"name": "创业板指", "pct": 1.0}], _fb())
    assert len(s["evidence"]["index"]) == 2
    assert s["style"] == mst.STYLE_ATTACK


def test_disabled_returns_neutral(monkeypatch):
    monkeypatch.setattr(mst, "ENABLE_MARKET_STYLE", False)
    s = mst.detect_style(_idx(1.2, 1.0, 0.9), _fb())
    assert s["style"] == mst.STYLE_NEUTRAL
    assert "关闭" in s["rule"]


def test_ice_note_forbids_action():
    """⛔ 语义矛盾门禁：冰点档不得只剩"只做逆势"这种可做暗示。"""
    s = mst.detect_style(_idx(0.5, 0.2, -0.8), _fb(sentiment_score=1.0))
    assert s["ice"] is True
    assert "空仓" in (s["ice_note"] or "")
    assert "只看不买" in (s["ice_note"] or "")


def test_not_ice_no_note():
    s = mst.detect_style(_idx(0.3, 0.2, 0.1), _fb(sentiment_score=5.0))
    assert s["ice"] is False
    assert s["ice_note"] is None


# ── seal_ratio_pct ────────────────────────────────────────


def test_seal_ratio():
    assert mst.seal_ratio_pct({"seal_amt": 1.5, "lt_mktcap": 75.0}) == 2.0


def test_seal_ratio_missing_is_none():
    assert mst.seal_ratio_pct({"seal_amt": 1.5}) is None
    assert mst.seal_ratio_pct({"seal_amt": None, "lt_mktcap": 0}) is None


# ── annotate_candidates ───────────────────────────────────


def _row(**over):
    d = {
        "code": "600000", "name": "测试", "boards": 2, "pct": 10.0,
        "seal_amt": 2.0, "lt_mktcap": 50.0, "sector_mates_cnt": 1,
        "is_sector_dragon": False, "is_capacity_core": False,
        "reason": {"lead": {"themeFirst": "094500"}}, "fbt": "094500",
        "score": 50.0, "raw_score": 50.0,
    }
    d.update(over)
    return d


def test_attack_top_board_is_core():
    rows = [_row(boards=4), _row(code="600001", boards=1)]
    st = mst.annotate_candidates(rows, {"style": mst.STYLE_ATTACK})
    assert rows[0]["style_bucket"] == "core"
    assert rows[0]["style_flag"] == "🎯最强"
    assert rows[1]["style_bucket"] == "follow"
    assert "跟风" in rows[1]["style_flag"]


def test_attack_dragon_with_sector_effect_is_core():
    rows = [_row(boards=2, is_sector_dragon=True, sector_mates_cnt=3)]
    mst.annotate_candidates(rows, {"style": mst.STYLE_ATTACK}, max_boards=5)
    assert rows[0]["style_bucket"] == "core"


def test_attack_capacity_core_must_be_moving():
    rows = [_row(pct=2.0, is_capacity_core=True)]
    mst.annotate_candidates(rows, {"style": mst.STYLE_ATTACK}, max_boards=5)
    assert rows[0]["style_bucket"] == "follow"
    rows = [_row(pct=6.0, is_capacity_core=True)]
    mst.annotate_candidates(rows, {"style": mst.STYLE_ATTACK}, max_boards=5)
    assert rows[0]["style_bucket"] == "core"


def test_shift_hedge_first_in_sector():
    rows = [_row(is_sector_dragon=True, sector_mates_cnt=3, boards=2)]
    mst.annotate_candidates(rows, {"style": mst.STYLE_SHIFT})
    assert rows[0]["style_bucket"] == "hedge"
    assert "逆势" in rows[0]["style_flag"]


def test_shift_not_first_in_sector_is_not_hedge():
    rows = [_row(is_sector_dragon=True, sector_mates_cnt=3, boards=2,
                 reason={"lead": {"themeFirst": "093000"}})]
    mst.annotate_candidates(rows, {"style": mst.STYLE_SHIFT})
    assert rows[0]["style_bucket"] is None


def test_shift_high_thin_seal_is_caution():
    """≥4 板且封单/流通<2% → 高位薄封（即使不是龙一）。"""
    rows = [_row(boards=5, seal_amt=0.5, lt_mktcap=60.0)]  # 0.83%
    mst.annotate_candidates(rows, {"style": mst.STYLE_SHIFT})
    assert rows[0]["style_bucket"] == "caution"
    assert "封单薄" in rows[0]["style_flag"]


def test_shift_high_thin_seal_beats_hedge():
    """⛔ 铁律 5：龙一身份不能放过薄封高位票。"""
    rows = [_row(boards=5, is_sector_dragon=True, sector_mates_cnt=4,
                 seal_amt=0.5, lt_mktcap=60.0)]
    mst.annotate_candidates(rows, {"style": mst.STYLE_SHIFT})
    assert rows[0]["style_bucket"] == "caution"


def test_shift_low_board_thin_seal_not_caution():
    """低位薄封不算派发（环境系数：低位筹码没兑现压力）。"""
    rows = [_row(boards=1, seal_amt=0.2, lt_mktcap=50.0)]
    mst.annotate_candidates(rows, {"style": mst.STYLE_SHIFT})
    assert rows[0]["style_bucket"] is None


def test_neutral_no_flags():
    rows = [_row(boards=4, is_sector_dragon=True, sector_mates_cnt=5)]
    mst.annotate_candidates(rows, {"style": mst.STYLE_NEUTRAL})
    assert rows[0]["style_flag"] is None


def test_annotate_does_not_change_score_or_order():
    rows = [_row(code="A", boards=1, raw_score=40.0),
            _row(code="B", boards=4, raw_score=60.0)]
    before = [r["raw_score"] for r in rows]
    mst.annotate_candidates(rows, {"style": mst.STYLE_ATTACK})
    assert [r["raw_score"] for r in rows] == before
    assert [r["code"] for r in rows] == ["A", "B"]


def test_annotate_writes_seal_ratio():
    rows = [_row(seal_amt=1.0, lt_mktcap=40.0)]
    mst.annotate_candidates(rows, {"style": mst.STYLE_NEUTRAL})
    assert rows[0]["seal_ratio_pct"] == 2.5


# ── no_trend_hint ─────────────────────────────────────────


def test_no_trend_hint_flat():
    fb = {"trend": {"history": [
        {"date": "1", "pr": 20.0, "ap": 0.5},
        {"date": "2", "pr": 20.8, "ap": 0.6},
        {"date": "3", "pr": 21.2, "ap": 0.4},
    ]}}
    assert "看不出趋势" in (mst.no_trend_hint(fb) or "")


def test_no_trend_hint_none_when_downtrend():
    fb = {"trend": {"promote_downtrend": True, "history": [
        {"date": "1", "pr": 20.0, "ap": 0.5},
        {"date": "2", "pr": 20.5, "ap": 0.5},
        {"date": "3", "pr": 20.6, "ap": 0.5},
    ]}}
    assert mst.no_trend_hint(fb) is None


def test_no_trend_hint_none_when_short_history():
    assert mst.no_trend_hint({"trend": {"history": [{"pr": 20, "ap": 0}]}}) is None


# ── 卡面渲染 ──────────────────────────────────────────────


def _texts(blocks):
    out = []
    for b in blocks:
        t = b.get("text")
        out.append(t.get("content") if isinstance(t, dict) else "")
    return out


def test_card_renders_style_block():
    payload = {"market_style": {
        "style": "shift", "label": "分歧", "rule": "三指数不同向（有红有绿）",
        "trading_rule": mst.TRADING_RULE["shift"],
        "evidence": {"index": _idx(0.5, 0.3, -0.8), "amount_yi": 16874.0},
        "no_trend_hint": None,
    }}
    txt = "\n".join(_texts(bc._post_pool_blocks(payload)))
    assert "今天的市场环境" in txt
    assert "分歧" in txt
    assert "逆势" in txt
    assert "1.7万亿" in txt


def test_card_style_block_absent_when_missing():
    txt = "\n".join(_texts(bc._post_pool_blocks({})))
    assert "今天的市场环境" not in txt


def test_card_no_trend_line():
    payload = {"market_style": {
        "style": "neutral", "label": "没方向", "rule": "指数没有明确方向",
        "trading_rule": mst.TRADING_RULE["neutral"],
        "evidence": {"index": _idx(0.1, 0.1, 0.1)},
        "no_trend_hint": "看不出趋势 → 少出手",
    }}
    txt = "\n".join(_texts(bc._post_pool_blocks(payload)))
    assert "看不出趋势" in txt


def test_card_ice_note_rendered():
    payload = {"market_style": {
        "style": "shift", "label": "分歧", "rule": "三指数不同向（有红有绿）",
        "trading_rule": mst.TRADING_RULE["shift"],
        "evidence": {"index": _idx(0.5, 0.2, -0.8)},
        "ice_note": "冰点档（情绪 1.0/10）→ 空仓优先，下面的标注只看不买",
    }}
    txt = "\n".join(_texts(bc._post_pool_blocks(payload)))
    assert "空仓优先" in txt
    assert "只看不买" in txt


def test_card_seal_tag_uses_ratio():
    """封单标签只认归一化口径（占流通%），不再单吹"X 亿"。"""
    row = _row(seal_amt=2.0, lt_mktcap=50.0, boards=2)
    row["dim_panel"] = {}
    joined = " ".join(t for t, _ in bc._candidate_tags(row))
    assert "封单占流通" in joined


def test_card_high_thin_seal_colored_green():
    """高位(≥4板)薄封 → 绿色（恶化），与 defense_gate 派发特征同口径。"""
    row = _row(boards=5, seal_amt=0.5, lt_mktcap=60.0)
    row["dim_panel"] = {}
    pairs = dict(bc._candidate_tags(row))
    tag = next(k for k in pairs if k.startswith("封单占流通"))
    assert pairs[tag] == "green"


def test_card_style_flag_rendered():
    row = _row(boards=4)
    row["dim_panel"] = {}
    row["style_bucket"] = "core"
    row["style_flag"] = "🎯最强"
    joined = " ".join(t for t, _ in bc._candidate_tags(row))
    assert "🎯最强" in joined
