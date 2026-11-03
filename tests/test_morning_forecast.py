#!/usr/bin/env python3
"""次日推测模型 + 周度准确性归纳的纯函数测试（2026-11-02 立，同日迁到 core）。

⛔ 模型只有一份实现 = `core/relay_forecast.py`（选股卡与竞价卡共用）。
   `scripts/morning_forecast.py` 只是手动诊断的薄封装，不再单独测它的模型逻辑。

只测「口径与边界」，不测网络/文件 IO：
  · factor_count 缺失字段**不算命中**（2026-11-02 修的真 bug：缺 zbc 曾被白送一条）
  · band_of / pos_key 的边界
  · build_model(cutoff) 真的只用 cutoff 之前的样本（⛔ 回测不许看未来）
  · forecast_one 的三档判定（后排→放弃、6板崖位→只观察、可等条件）
  · 选股卡接入：annotate / line_for / summary_line 的输出契约
  · 竞价卡接入：band_win_for 用实测开盘查历史档；无 forecast 时不得抛错
  · weekly_accuracy.summarize 的分档统计与「买不到」校准
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "wealth_code"), str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import relay_forecast as mf     # noqa: E402
import weekly_accuracy as wa              # noqa: E402


# ── 封板四因子 ──────────────────────────────────────────────────────
def test_factor_count_all_missing_is_zero():
    """全缺 → 0 条。⛔ 历史 bug：缺 zbc 时被当「零炸板」白送一条。"""
    assert mf.factor_count({}) == 0


def test_factor_count_missing_zbc_not_clean():
    """有早封+厚封+低换手，但 zbc 缺失 → 只算 3 条（不许把缺失当零炸板）。"""
    row = {"fbt_min": 560, "seal_ratio": 5.0, "hs": 3.0}
    assert mf.factor_count(row) == 3


def test_factor_count_full_house():
    row = {"fbt_min": 560, "seal_ratio": 5.0, "hs": 3.0, "zbc": 0}
    assert mf.factor_count(row) == 4


def test_factor_count_seal_ratio_pct_alias():
    """台账字段叫 seal_ratio_pct，回测样本叫 seal_ratio，两个都要认。"""
    assert mf.factor_count({"seal_ratio_pct": 4.0}) == 1


# ── 分档边界 ────────────────────────────────────────────────────────
def test_band_of_edges():
    assert mf.band_of(-3.0) == "低开>3%"        # 含边界
    assert mf.band_of(-3.01) == "低开>3%"
    assert mf.band_of(-1.0) == "低开0~3%"
    assert mf.band_of(0.0) == "低开0~3%"        # 0 不算红开
    assert mf.band_of(0.01) == "高开0~3%"
    assert mf.band_of(3.0) == "高开0~3%"
    assert mf.band_of(3.01) == "高开3~5%"
    assert mf.band_of(5.0) == "高开3~5%"        # 5% 仍可买
    assert mf.band_of(5.01) == "高开>5%"        # 超 5% 买不到
    assert mf.band_of(None) is None


def test_pos_key():
    assert mf.pos_key(0) == "最高板"
    assert mf.pos_key(1) == "差1板"
    assert mf.pos_key(2) == "差≥2板"
    assert mf.pos_key(9) == "差≥2板"


# ── 模型不能看未来 ──────────────────────────────────────────────────
def test_build_model_cutoff_excludes_future():
    """cutoff 之后的日子不许进模型（⛔ 回测的最大陷阱）。"""
    full = mf.build_model()
    cut = mf.build_model(cutoff="2026-10-01")
    assert full["meta"]["sample_n"] > cut["meta"]["sample_n"], "cutoff 没生效"


def test_build_model_groups_have_required_fields():
    m = mf.build_model()
    assert m["groups"], "模型分组为空"
    g = next(iter(m["groups"].values()))
    for k in ("n", "gap_median", "bands", "promo_rate", "buy_n"):
        assert k in g, f"分组缺字段 {k}"


# ── 三档判定 ────────────────────────────────────────────────────────
def _row(boards, **kw):
    d = {"code": "000001", "name": "甲", "boards": boards,
         "fbt_min": 560, "seal_ratio_pct": 5.0, "hs": 3.0, "zbc": 0,
         "factors": {"count": 4}, "track": {"days_in_track": boards}}
    d.update(kw)
    return d


def test_forecast_backrow_always_give_up():
    """身位差 ≥2 板 → 一律放弃（与封板质量无关，⛔ 质量救不了后排）。"""
    m = mf.build_model()
    for boards in (2, 3, 4):
        r = mf.forecast_one(_row(boards), 6, m)
        assert r["gap_to_top"] >= 2
        assert r["verdict"] == "放弃", (boards, r["verdict"])


def test_forecast_top_board_cliff_is_watch_only():
    """最高板但 6 板崖位（晋级率 <25%）→ 只观察，不许判「可等」。"""
    m = mf.build_model()
    r = mf.forecast_one(_row(6), 6, m)
    assert r["gap_to_top"] == 0
    assert r["verdict"] == "只观察", r["verdict"]
    assert any("崖位" in x for x in r["reasons"])


def test_forecast_verdict_labels_are_fixed_set():
    m = mf.build_model()
    for boards in (2, 3, 5, 6, 7):
        r = mf.forecast_one(_row(boards), 6, m)
        assert r["verdict"] in ("可等", "只观察", "放弃")


def test_forecast_reasons_not_empty():
    """每只票都必须给理由（禁止只给结论）。"""
    m = mf.build_model()
    r = mf.forecast_one(_row(2), 6, m)
    assert r["reasons"], "理由为空"


# ── 选股卡接入：annotate / line_for / summary_line ──────────────────
def test_factor_count_accepts_fbt_hhmmss():
    """候选文件里 `fbt` 是 HHMMSS 字符串（'093221'），台账里是 `fbt_min` 分钟数。
    ⛔ 两个都要认，否则选股卡算出来的因子数会比回测少一条（口径分裂）。"""
    assert mf.fbt_hm({"fbt": "092500"}) == 925
    assert mf.fbt_hm({"fbt": "093221"}) == 932
    assert mf.fbt_hm({"fbt_min": 570}) == 930
    assert mf.fbt_hm({}) is None
    # 09:32 不算早封（>09:30），09:25 算
    assert mf.factor_count({"fbt": "093221", "seal_ratio_pct": 5.0, "hs": 3.0, "zbc": 0}) == 3
    assert mf.factor_count({"fbt": "092500", "seal_ratio_pct": 5.0, "hs": 3.0, "zbc": 0}) == 4


def test_seal_ratio_prefers_percent_fields():
    """三个来源量纲必须统一成 %。字段缺失才现算，⛔ 不把 0.02 当 2%。"""
    assert mf.seal_ratio_pct({"seal_ratio": 3.5}) == 3.5
    assert mf.seal_ratio_pct({"seal_ratio_pct": 1.73}) == 1.73
    assert mf.seal_ratio_pct({"seal_amt": 0.72, "lt_mktcap": 41.7}) == pytest.approx(1.73, abs=0.01)
    assert mf.seal_ratio_pct({}) is None


def test_annotate_attaches_forecast_and_keeps_order():
    m = mf.build_model()
    rows = [{"code": "000001", "name": "甲", "boards": 2},
            {"code": "000002", "name": "乙", "boards": 6}]
    mf.annotate(rows, 6, model=m)
    assert [r["code"] for r in rows] == ["000001", "000002"], "annotate 不许改顺序"
    assert rows[0]["forecast"]["verdict"] == "放弃"          # 2 板在 6 板市场=后排
    assert rows[1]["forecast"]["verdict"] == "只观察"        # 6 板崖位


def test_annotate_without_model_skips_silently():
    """模型样本缺失（groups 空）→ 不挂字段、不抛错（⛔ 不许因此打断选股主链）。"""
    rows = [{"code": "000001", "boards": 2}]
    mf.annotate(rows, 6, model={"groups": {}, "by_boards": {}})
    assert "forecast" not in rows[0]


def test_line_for_and_summary_line():
    m = mf.build_model()
    rows = [{"code": "000001", "name": "甲", "boards": 6}]
    mf.annotate(rows, 6, model=m)
    v, txt = mf.line_for(rows[0])
    assert v == "只观察"
    assert "明早大概率开在" in txt and "买不到" in txt
    s = mf.summary_line(rows)
    assert "可等 0 只" in s and "只观察 1 只" in s


def test_line_for_missing_forecast_returns_empty():
    assert mf.line_for({}) == ("", "")
    assert mf.line_for({"forecast": {"error": "x"}}) == ("", "")


# ── 竞价卡接入：用实测开盘查历史档 ──────────────────────────────────
def test_band_win_for_uses_actual_gap():
    cand = {"forecast": {"by_band": {"高开0~3%": {"n": 20, "win": 62.0, "avg": 1.85}}}}
    hit = mf.band_win_for(cand, 2.0)
    assert hit["band"] == "高开0~3%"
    assert hit["win"] == 62.0


def test_band_win_for_missing_band_returns_none():
    """该档没样本 → None（⛔ 不许拿别档的数字顶替）。"""
    cand = {"forecast": {"by_band": {"高开0~3%": {"n": 20, "win": 62.0}}}}
    assert mf.band_win_for(cand, 4.0) is None
    assert mf.band_win_for({}, 2.0) is None
    assert mf.band_win_for(cand, None) is None


# ── 周度准确性：聚合与校准 ──────────────────────────────────────────
def _acc_row(verdict, gap, ret, promo=False, pred_over5=50, pred_promo=40.0,
             median=3.0, p25=-2.0, p75=8.0):
    return {"date": "2026-11-03", "code": "000001", "name": "甲", "boards": 2,
            "verdict": verdict, "group": "差≥2板·封板弱(≤2)",
            "pred_median": median, "pred_p25": p25, "pred_p75": p75,
            "pred_p_over5": pred_over5, "pred_promo": pred_promo,
            "actual_gap": gap, "actual_ret": ret, "one_word": False,
            "actual_promoted": promo, "next_boards": 0,
            "in_band": p25 <= gap <= p75, "over5_hit": gap > 5}


def test_summarize_empty():
    assert wa.summarize([]) == {"n": 0}


def test_summarize_by_verdict_counts_only_tradable():
    """「红开可买」只统计 0<gap≤5 且非一字板的样本——这才是纪律下真能下单的子集。"""
    rows = [
        _acc_row("放弃", 2.0, -1.0),      # 可买、亏
        _acc_row("放弃", 7.0, -3.0),      # 高开>5，买不到 → 不计入 bought
        _acc_row("放弃", -4.0, 5.0),      # 低开 → 不计入 bought
        _acc_row("放弃", 1.0, 2.0),       # 可买、赚
    ]
    s = wa.summarize(rows)
    b = s["by_verdict"]["放弃"]
    assert b["n"] == 4
    assert b["bought_n"] == 2
    assert b["bought_win"] == 50.0
    assert b["bought_avg"] == 0.5


def test_summarize_over5_calibration():
    rows = [_acc_row("放弃", 8.0, None, pred_over5=60),
            _acc_row("放弃", 9.0, None, pred_over5=70),
            _acc_row("放弃", 2.0, 1.0, pred_over5=10)]
    s = wa.summarize(rows)
    assert s["over5_call_n"] == 2
    assert s["over5_call_hit"] == 100.0
    assert s["easy_call_n"] == 1
    assert s["easy_call_hit"] == 100.0


def test_summarize_promo_actual():
    rows = [_acc_row("可等", 2.0, 1.0, promo=True),
            _acc_row("可等", 2.0, -1.0, promo=False)]
    s = wa.summarize(rows)
    assert s["promo_actual"] == 50.0
