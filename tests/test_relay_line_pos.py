#!/usr/bin/env python3
"""选股卡 forecast 的「放弃」标签分母 = 同题材（2026-11-06 用户拍板）

## 用户原话（问题来源）

「有时候龙头，比如说板块之间非板块之间的切换，比如说你传媒板块你已经涨了五板龙头了，
  但是光伏板块才一个板，这种身位差其实是没有影响的呀，它是走一个新的逻辑，
  甚至说可以做一个资金的承接方向，你这种身位差估计只能在同一个板块里面适用适用，
  不同板块之间它是没有关系的」

## 为什么这枚标签必须改

`forecast_one` 原本 `gap_to_top >= 2 → 放弃`，而 `market_max` 传的是**全市场最高板**。
11-05 候选 31 只里 28 只被标「放弃」，其中 **18 只（64%）在本题材是最高板或只差 1 板**。

35 日 717 真题材可买票次（`cache/research/position_gap_bt.json` C 段）：

| 三桶 | 市场分母 | 题材分母 |
|---|---|---|
| 最高板 | n=13 +1.77% / 53.8% | n=289 −1.10% / 40.1% |
| 差1板  | n=20 −2.89% / 35.0% | n=103 +0.52% / 53.4% |
| 差≥2板 | n=512 −0.93% / 41.8% | n=153 −1.62% / 37.3% |
| 区分度 | **0.6pct**（≈抛硬币） | **6.3pct** |

## 本文件锁定什么

1. `line_gap_of` 取数优先级：`row.line_gap` > `row.theme_max_boards` > 入参；⛔ 缺题材返回 None
2. verdict 三档：同题材差 1→可等、差 0→只观察、≥2→放弃
3. 缺题材 → `position_basis=market` 且**不否决**（⛔ 不许拿全市场差判死）
4. `annotate` 显式 `theme_of` 优先于候选行自带的 `theme`（东财题材与选股宝不同源）
5. 开关 `RELAY_FORECAST_LINE_POS` / `line_pos=False` 逐字回退旧行为
6. 文案里不许再出现「买不到」（11-05 删线后只剩竞价封死一个事实）
7. ⛔ 统计模型分组不动（`group` 仍是市场分母），只改 verdict 判据
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "wealth_code"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import relay_forecast as rf     # noqa: E402


def _row(boards=2, **kw):
    """一只「封板一般」的票：早封/厚封/低换手/零炸板 都不满足 → 弱封组。"""
    base = {"code": "600000", "name": "测试", "boards": boards,
            "fbt_min": 600, "seal_ratio": 1.0, "hs": 25.0, "zbc": 2}
    base.update(kw)
    return base


@pytest.fixture(scope="module")
def model():
    m = rf.build_model()
    if not m.get("groups"):
        pytest.skip("归档样本缺失（cache/relay_review/latest.json）→ 模型无法建，跳过")
    return m


# ── 1. 取数优先级 ───────────────────────────────────────────────────
def test_line_gap_prefers_row_field():
    assert rf.line_gap_of({"boards": 2, "line_gap": 3}) == (3, None)


def test_line_gap_falls_back_to_row_theme_max():
    assert rf.line_gap_of({"boards": 2, "theme_max_boards": 5}) == (3, 5)


def test_line_gap_falls_back_to_argument():
    assert rf.line_gap_of({"boards": 3}, 5) == (2, 5)


def test_line_gap_missing_is_none():
    """⛔ 缺题材 → None（不裁决）。不拿行业 sector 顶替。"""
    assert rf.line_gap_of({"boards": 2}) == (None, None)
    assert rf.line_gap_of({"boards": 2, "sector": "光伏设备"}) == (None, None)


def test_line_gap_never_negative():
    """数据异常（题材最高 < 本票板数）→ 不返回负数，按算不处理。"""
    assert rf.line_gap_of({"boards": 5, "theme_max_boards": 3}) == (None, None)


# ── 2. verdict 三档（同题材）──────────────────────────────────────────
def test_line_gap_one_is_wait(model):
    f = rf.forecast_one(_row(3), 6, model, theme_max_boards=4)
    assert f["line_gap"] == 1 and f["position_basis"] == "line"
    assert f["verdict"] == rf.VERDICT_WAIT


def test_line_gap_zero_is_watch(model):
    """自己是这条线的锚 → 只观察（不是可等，也不是放弃）。"""
    f = rf.forecast_one(_row(2), 6, model, theme_max_boards=2)
    assert f["line_gap"] == 0
    assert f["verdict"] == rf.VERDICT_WATCH


def test_line_gap_two_or_more_is_drop(model):
    for tm, b in ((4, 2), (5, 2), (6, 3)):
        f = rf.forecast_one(_row(b), 6, model, theme_max_boards=tm)
        assert f["line_gap"] >= 2
        assert f["verdict"] == rf.VERDICT_DROP, (tm, b, f["verdict"])


# ── 3. 跨题材不罚（用户的核心论断）────────────────────────────────────
def test_cross_theme_backrow_not_dropped(model):
    """全市场最高 8 板、它只有 2 板，但它在本题材就是最高板 → ⛔ 不许放弃。"""
    f = rf.forecast_one(_row(2), 8, model, theme_max_boards=2)
    assert f["gap_to_top"] == 6          # 全市场差 6 板是事实
    assert f["line_gap"] == 0            # 但本题材它就是最高
    assert f["verdict"] != rf.VERDICT_DROP


def test_missing_theme_never_drops(model):
    """缺题材（行业兜底）→ 不裁决，⛔ 不拿全市场差一票否决。"""
    f = rf.forecast_one(_row(1), 6, model)
    assert f["position_basis"] == "market"
    assert f["line_gap"] is None
    assert f["verdict"] != rf.VERDICT_DROP
    assert any("不裁决" in x or "不据此否决" in x for x in f["reasons"])


# ── 4. annotate 注入口径 ────────────────────────────────────────────
def test_annotate_theme_of_beats_row_theme(model):
    """显式 `theme_of`（选股宝）优先于候选行自带的 `theme`（东财），否则口径分裂。"""
    rows = [_row(2, code="600001", theme="东财题材")]
    rf.annotate(rows, 6, model=model, theme_max_boards={"机器人": 4},
                theme_of={"600001": "机器人"})
    f = rows[0]["forecast"]
    assert f["theme_max_boards"] == 4 and f["line_gap"] == 2
    assert f["position_basis"] == "line"


def test_annotate_unknown_theme_when_map_misses_code(model):
    rows = [_row(2, code="600002", theme="机器人")]
    rf.annotate(rows, 6, model=model, theme_max_boards={"机器人": 4}, theme_of={})
    # theme_of 空 → 退回行内 theme → 仍能算出题材
    assert rows[0]["forecast"]["line_gap"] == 2


def test_annotate_does_not_change_order_or_other_fields(model):
    rows = [_row(2, code="600003"), _row(3, code="600004")]
    before = [r["code"] for r in rows]
    rf.annotate(rows, 6, model=model, theme_max_boards={}, theme_of={})
    assert [r["code"] for r in rows] == before
    assert all("forecast" in r for r in rows)


# ── 5. 开关回退 ─────────────────────────────────────────────────────
def test_switch_off_restores_market_denominator(model):
    """`line_pos=False` → 逐字回退「市场差 ≥2 就放弃」。"""
    f = rf.forecast_one(_row(2), 6, model, theme_max_boards=2, line_pos=False)
    assert f["position_basis"] == "market"
    assert f["verdict"] == rf.VERDICT_DROP


def test_env_switch_off(monkeypatch, model):
    monkeypatch.setenv("RELAY_FORECAST_LINE_POS", "0")
    import importlib
    mod = importlib.reload(rf)
    try:
        assert mod.RELAY_FORECAST_LINE_POS is False
        f = mod.forecast_one(_row(2), 6, model, theme_max_boards=2)
        assert f["verdict"] == mod.VERDICT_DROP
    finally:
        monkeypatch.delenv("RELAY_FORECAST_LINE_POS", raising=False)
        importlib.reload(rf)


# ── 6. 文案口径 ─────────────────────────────────────────────────────
def test_line_for_never_says_unbuyable(model):
    rows = [_row(3, code="600005")]
    rf.annotate(rows, 6, model=model, theme_max_boards={"机器人": 4},
                theme_of={"600005": "机器人"})
    _, txt = rf.line_for(rows[0])
    assert "买不到" not in txt
    assert "高开过 5%" in txt


def test_summary_line_never_says_unbuyable(model):
    rows = [_row(3, code="600006")]
    rf.annotate(rows, 6, model=model, theme_max_boards={"机器人": 4},
                theme_of={"600006": "机器人"})
    s = rf.summary_line(rows)
    assert "买不到" not in s
    assert "可等" in s and "只观察" in s and "放弃" in s


def test_line_for_hides_tiny_sample_winrate(model):
    """样本不足不报胜率：「100.0%（3 笔）」这种读数会误导（MIN_GROUP_N 同源纪律）。"""
    cand = {"forecast": {"gap_median": 2.5, "p_over5": 40, "buy_win": 100.0,
                         "buy_n": rf.MIN_GROUP_N - 1, "verdict": rf.VERDICT_WAIT}}
    _, txt = rf.line_for(cand)
    assert "胜率" not in txt


# ── 7. 统计模型不受影响（只改判据，不改数字）────────────────────────────
def test_group_key_still_market_based(model):
    f = rf.forecast_one(_row(2), 6, model, theme_max_boards=2)
    assert f["group"].startswith(("最高板", "差1板", "差≥2板"))
    assert f["group"] == f"{rf.pos_key(f['gap_to_top'])}·封板弱(≤2)"
