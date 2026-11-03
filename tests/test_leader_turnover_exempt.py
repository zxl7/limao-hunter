# -*- coding: utf-8 -*-
"""龙头成交额门槛豁免（`pipeline_eod.leader_turnover_floor`）契约锁。

## 背景（2026-10-27 用户「推的全是杂毛，既不是板块龙一，也不是高度第一」）

当天实证：
  · 上海九百 5 板（全市场唯一高度）成交 3.94 亿 < 5 亿 → 被剔出池，只进「只看不买」；
  · 赛福天 4 板 09:25:02 秒板 3.02 亿 → 同样被剔；
  · 大消费线龙头被剔后，组内最强变成**首板**的文投控股 → 被推成接力池第 2 位。

病根 = **成交额门槛跑在「谁是高度第一」之前**：先砍掉老大，准入闸再从残骸里挑前排，
池子必然由二线构成。修法 = 高度层（最高板 / 次高板层，且 ≥3 板）门槛降到 1.5 亿。

⛔ 锁死的性质：
  ① 只放宽「进场门槛」，不改排序、不改裁决；
  ② 不是无门槛（1.5 亿以下仍剔）；
  ③ 板数/最高板缺失 → 不豁免（不猜、不开后门）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "wealth_code"))

import pipelines.pipeline_eod as pz  # noqa: E402


def test_top_board_is_exempted():
    """全市场最高板 → 按 1.5 亿放行（上海九百 5 板 3.94 亿的场景）。"""
    assert pz.leader_turnover_floor(5, 5) == pz.MIN_TURNOVER_YI_LEADER


def test_second_height_layer_is_exempted():
    """次高板层也算高度层（赛福天 4 板 3.02 亿的场景）。"""
    assert pz.leader_turnover_floor(4, 5) == pz.MIN_TURNOVER_YI_LEADER


def test_middle_board_not_exempted():
    """6 板市场里 3~4 板是中位股，不享受豁免（⛔ 不能整层开闸）。"""
    assert pz.leader_turnover_floor(4, 6) is None
    assert pz.leader_turnover_floor(3, 6) is None


def test_low_board_never_exempted():
    """1~2 板不在此豁免范围（低位交给 gene_radar / 低位池）。"""
    assert pz.leader_turnover_floor(2, 5) is None
    assert pz.leader_turnover_floor(1, 5) is None


def test_missing_data_not_exempted():
    """板数/最高板缺失 → 不豁免（⛔ 缺数据不是开后门的理由）。"""
    assert pz.leader_turnover_floor(None, 5) is None
    assert pz.leader_turnover_floor(5, None) is None
    assert pz.leader_turnover_floor(0, 5) is None


def test_switch_off_restores_old_behavior():
    """`ENABLE_LEADER_TURNOVER_EXEMPT=0` → 一键回旧行为（全部不豁免）。"""
    import os
    os.environ["ENABLE_LEADER_TURNOVER_EXEMPT"] = "0"
    try:
        assert pz.leader_turnover_floor(5, 5) is None
    finally:
        os.environ.pop("ENABLE_LEADER_TURNOVER_EXEMPT", None)


def test_low_pool_quality_guard():
    """低位池封板质量护栏：巨量换手 + 反复炸板 = 倒货，不进可买池（海洋王场景）。"""
    assert pz.low_pool_quality_ok({"hs": 28.85, "zbc": 6}) is False
    assert pz.low_pool_quality_ok({"hs": 14.9, "zbc": 0}) is True
    # ⛔ 缺数据 → 放行（缺数据 ≠ 不利证据）
    assert pz.low_pool_quality_ok({}) is True
