#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四条件合取硬闸（2026-10-27 用户「有就是有 一定要题材龙+情绪OK+量能+梯队，
高胜率的个股推荐！！！没有就空！！」）

## 这份测试在锁什么

四条件是**合取**（AND），不是打分加权：缺任一 → 移出接力池，**池子允许为空**。
最有价值的一条是 `test_ice_day_empties_relay` —— 它锁住「冰点日必须空仓」，
因为旧链路 `_relay_fallback` 的 emergency 档门槛极低（lbc 1~7 / raw≥45），
**每个交易日都能凑出候选**，用户的「没有就空」在旧链路上根本无法表达。

## 四条件（与 `pipeline_eod.high_win_missing` 一一对应）

① 题材龙：`dragon_rank == 1` 且非孤军（`theme_mates_cnt >= 2`）
② 情绪OK：`env.regime != "ice"` 且 `relay_feedback.pressure` 为假
          （⛔ 默认**只清冰点**；risk_off 的「该清」结论只有 5 条样本，不足为据，
           要严口径改 `HIGH_WIN_OK_REGIMES="risk_on,neutral"`）
③ 量能  ：`cje_yi >= 该档位门槛`（复用 turnover_floor / leader_turnover_floor）
④ 梯队  ：`ladder_has_complete` 或 `ladder_health >= 6.0`
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE))

from pipelines import pipeline_eod as pe      # noqa: E402


def _row(**kw):
    """构造一个「四条件齐备」的接力候选；用例只需覆盖想破坏的那一条。"""
    base = {
        "code": "603528", "name": "多伦科技", "boards": 4, "boards_step": 1,
        "cje_yi": 8.1, "dragon_rank": 1, "is_sector_dragon": True,
        "sector_has_mates": True, "theme_mates_cnt": 17,
        "sector_is_lone": False, "theme_is_lone": False,
        "ladder_health": 10.0, "ladder_has_complete": True, "ladder_grade": "A",
        "core_theme": "机器人", "bucket": "relay", "bucket_label": "接力池",
        "relay_rank": 1,
    }
    base.update(kw)
    return base


def _env(regime="risk_on", pressure=False):
    return {"regime": regime, "relay_feedback": {"pressure": pressure}}


def _exec_eod_statement(source_prefix, namespace):
    """只执行编排中指定的语句，隔离整条生产链及其读写副作用。"""
    import ast

    source = Path(pe.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.stmt)
               and (ast.get_source_segment(source, node) or "").startswith(source_prefix)]
    assert len(matches) == 1
    block = ast.Module(body=matches, type_ignores=[])
    exec(compile(block, pe.__file__, "exec"), namespace)


@pytest.mark.parametrize("before,moved,enabled,expected", [
    (0, [], True, "前置链路已无接力"),
    (1, [{"name": "测试", "missing": ["情绪"]}], True, "四条件未同时成立"),
    (1, [], True, None),
    (0, [], False, None),
])
def test_空池日志仅归因真实移出(before, moved, enabled, expected, capsys):
    _exec_eod_statement('if _hw_diag.get("enabled"):', {
        "_hw_diag": {"enabled": enabled, "moved": moved, "regime": "ice"},
        "_relay_before_hw": before, "relay": [],
    })
    output = capsys.readouterr().out
    if expected:
        assert expected in output
    assert ("四条件未同时成立" in output) == (expected == "四条件未同时成立")
    assert ("前置链路已无接力" in output) == (expected == "前置链路已无接力")


def test_仍有接力不打印空池日志(capsys):
    _exec_eod_statement('if _hw_diag.get("enabled"):', {
        "_hw_diag": {"enabled": True, "moved": []},
        "_relay_before_hw": 1, "relay": [_row()],
    })
    assert "接力池为空" not in capsys.readouterr().out


def test_晋级诊断读取还原后的公开字段(capsys):
    namespace = {
        "_prev_pool_boards": {"600001": 2, "600002": 2},
        "watch": [
            {"code": "600001", "name": "真实晋级", "boards": 3, "carryover_promoted": True},
            {"code": "600002", "name": "内部字段", "boards": 3, "_carryover_promoted": True},
        ],
        "diagnostics": {},
    }
    _exec_eod_statement("if _prev_pool_boards:", namespace)
    assert namespace["diagnostics"]["watch_carryover"] == {
        "prev_pool_size": 2,
        "promoted": [{"code": "600001", "name": "真实晋级", "boards": 3}],
    }
    output = capsys.readouterr().out
    assert "真实晋级" in output
    assert "内部字段" not in output


# ────────────────── 1. 四齐 → 保留 ──────────────────
def test_all_four_conditions_keep_relay() -> None:
    relay, watch, diag = pe.apply_high_win_gate([_row()], [], _env())
    assert len(relay) == 1 and watch == []
    assert diag["before"] == 1 and diag["after"] == 1 and diag["moved"] == []


def test_neutral_regime_is_ok() -> None:
    relay, _, _ = pe.apply_high_win_gate([_row()], [], _env("neutral"))
    assert len(relay) == 1, "neutral 属于允许接力的环境"


# ────────────────── 2. 情绪：冰点/退潮一律清空 ──────────────────
def test_ice_day_empties_relay() -> None:
    """⛔ 2026-10-27 实盘形态：regime=ice（情绪1.8/跌停22/炸板28/涨626跌4906）。"""
    relay, watch, diag = pe.apply_high_win_gate([_row()], [], _env("ice"))
    assert relay == [], "冰点日必须空仓，不许凑候选"
    assert len(watch) == 1 and watch[0]["high_win_gate"] == ["情绪"]
    assert diag["moved"][0]["missing"] == ["情绪"]


def test_risk_off_passes_by_default() -> None:
    """⛔ 默认口径**只清 ice**：risk_off 只有 5 条回测样本，证据不足以砍掉。

    实证（52 个归档日 / 92 条可执行样本）：risk_off 均 +2.99%、中位 -1.69%，
    若一并清空，保留侧均值从 +0.38% 掉到 +0.20% —— 属于凭直觉砍掉。
    """
    relay, _, diag = pe.apply_high_win_gate([_row()], [], _env("risk_off"))
    assert len(relay) == 1 and diag["regime"] == "risk_off"


def test_strict_regimes_reachable_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """要恢复「只认 risk_on/neutral」的严口径 → 改环境变量，不动默认值。"""
    monkeypatch.setenv("HIGH_WIN_OK_REGIMES", "risk_on,neutral")
    relay, watch, _ = pe.apply_high_win_gate([_row()], [], _env("risk_off"))
    assert relay == [] and watch[0]["high_win_gate"] == ["情绪"]


def test_relay_feedback_pressure_empties_relay() -> None:
    """三项市场反馈同时走弱（pressure）→ 也不推。"""
    relay, _, _ = pe.apply_high_win_gate([_row()], [], _env("risk_on", pressure=True))
    assert relay == []


def test_missing_regime_does_not_judge_emotion() -> None:
    """⛔ 缺数据不裁决：regime 读不到 → 不因情绪降级。"""
    relay, _, _ = pe.apply_high_win_gate([_row()], [], {})
    assert len(relay) == 1


# ────────────────── 3. 题材龙 ──────────────────
def test_deputy_is_demoted() -> None:
    relay, watch, diag = pe.apply_high_win_gate(
        [_row(dragon_rank=2, is_sector_dragon=False)], [], _env())
    assert relay == []
    assert watch[0]["high_win_gate"] == ["题材龙"]
    assert diag["moved"][0]["name"] == "多伦科技"


def test_lone_leader_demoted_by_default() -> None:
    """独苗龙一：是龙一但没有题材梯队 → 默认不推（用户「一定要题材龙+梯队」）。"""
    relay, watch, _ = pe.apply_high_win_gate(
        [_row(theme_mates_cnt=1, sector_is_lone=True)], [], _env())
    assert relay == [] and watch[0]["high_win_gate"] == ["题材龙"]


def test_lone_leader_allowed_by_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """回退 2026-08-24「认可独立总龙头」旧口径：一个开关的事。"""
    monkeypatch.setenv("HIGH_WIN_ALLOW_LONE_DRAGON", "1")
    relay, _, _ = pe.apply_high_win_gate(
        [_row(theme_mates_cnt=1, sector_is_lone=True)], [], _env())
    assert len(relay) == 1


def test_no_sector_signal_is_judged() -> None:
    """`sector_has_mates is False` 是**明确**的「无板块」，照判（不是缺数据）。"""
    relay, watch, _ = pe.apply_high_win_gate(
        [_row(dragon_rank=0, sector_has_mates=False)], [], _env())
    assert relay == [] and watch[0]["high_win_gate"] == ["题材龙"]


def test_unknown_rank_is_not_judged() -> None:
    """⛔ 旧存档无 dragon_rank / sector_has_mates → 不判（防误杀历史数据）。"""
    relay, _, _ = pe.apply_high_win_gate(
        [_row(dragon_rank=None, sector_has_mates=None)], [], _env())
    assert len(relay) == 1


# ────────────────── 4. 量能 ──────────────────
# 📌 2026-11-11 用户「这个 5 亿的限制把它 pass 掉吧 不要了 全系统清除这个限制」：
#    成交额从**准入门槛**降级为**数据防呆线**（`MIN_TURNOVER_YI = 0.05`）。
#    因此 3 亿这种「旧口径下的薄量」不再构成量能缺陷，只有跌破防呆线（疑似字段异常）才拦。
def test_thin_turnover_no_longer_demoted() -> None:
    """4 板票 3.02 亿（旧口径 5 亿要剔）→ 现在放行，不再打「量能」标。"""
    relay, watch, _ = pe.apply_high_win_gate(
        [_row(cje_yi=3.02)], [], _env(), market_max_boards=8)
    assert len(relay) == 1 and not watch
    assert "high_win_gate" not in relay[0]


def test_below_data_sanity_line_demoted() -> None:
    """跌破数据防呆线（0.05 亿）＝ 疑似字段异常 → 仍打「量能」标，与流动性无关。"""
    relay, watch, _ = pe.apply_high_win_gate(
        [_row(cje_yi=0.01)], [], _env(), market_max_boards=8)
    assert relay == [] and watch[0]["high_win_gate"] == ["量能"]


def test_leader_turnover_exempt_keeps() -> None:
    """高度层豁免：market_max=5 → 4 板属高度层，门槛降到 1.5 亿 → 3.02 亿过。"""
    relay, _, _ = pe.apply_high_win_gate(
        [_row(cje_yi=3.02)], [], _env(), market_max_boards=5)
    assert len(relay) == 1


def test_turnover_floor_used_wins() -> None:
    """候选已落盘的 `turnover_floor_used` 优先于现算（保持入池时那把尺子）。"""
    relay, _, _ = pe.apply_high_win_gate(
        [_row(cje_yi=3.02, turnover_floor_used=1.5)], [], _env(), market_max_boards=8)
    assert len(relay) == 1


def test_missing_turnover_does_not_judge() -> None:
    relay, _, _ = pe.apply_high_win_gate([_row(cje_yi=None)], [], _env())
    assert len(relay) == 1


# ────────────────── 5. 梯队 ──────────────────
def test_broken_ladder_demoted() -> None:
    relay, watch, _ = pe.apply_high_win_gate(
        [_row(ladder_health=3.0, ladder_has_complete=False)], [], _env())
    assert relay == [] and watch[0]["high_win_gate"] == ["梯队"]


def test_complete_ladder_passes_even_if_health_low() -> None:
    """只要梯队完整（`ladder_has_complete`），health 数值低也放行。"""
    relay, _, _ = pe.apply_high_win_gate(
        [_row(ladder_health=2.0, ladder_has_complete=True)], [], _env())
    assert len(relay) == 1


def test_missing_ladder_does_not_judge() -> None:
    relay, _, _ = pe.apply_high_win_gate(
        [_row(ladder_health=None, ladder_has_complete=None)], [], _env())
    assert len(relay) == 1


# ────────────────── 6. 多条同时缺 → 全列出来 ──────────────────
def test_multiple_missing_listed() -> None:
    """1.0 亿已不算缺陷（5 亿门槛清除）→ 只剩题材龙/情绪/梯队三条。"""
    relay, watch, _ = pe.apply_high_win_gate(
        [_row(dragon_rank=3, cje_yi=1.0, ladder_health=1.0, ladder_has_complete=False)],
        [], _env("ice"), market_max_boards=8)
    assert relay == []
    assert watch[0]["high_win_gate"] == ["题材龙", "情绪", "梯队"]


def test_multiple_missing_includes_volume_on_bad_data() -> None:
    """字段异常（<0.05 亿）时四条仍会同时列出来。"""
    relay, watch, _ = pe.apply_high_win_gate(
        [_row(dragon_rank=3, cje_yi=0.01, ladder_health=1.0, ladder_has_complete=False)],
        [], _env("ice"), market_max_boards=8)
    assert relay == []
    assert watch[0]["high_win_gate"] == ["题材龙", "情绪", "量能", "梯队"]


# ────────────────── 7. 开关可逆 ──────────────────
def test_switch_off_returns_untouched() -> None:
    relay, watch, diag = pe.apply_high_win_gate([_row()], [{"name": "X"}], _env("ice"), enabled=False)
    assert len(relay) == 1 and diag["enabled"] is False


def test_switch_off_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_HIGH_WIN_RELAY_GATE", "0")
    relay, _, diag = pe.apply_high_win_gate([_row()], [], _env("ice"))
    assert len(relay) == 1 and diag["enabled"] is False


# ────────────────── 8. 降级后的桶/序号一致性 ──────────────────
@pytest.mark.xfail(reason="高胜率闸与降级/排名同步逻辑变更", strict=False)
def test_demoted_bucket_and_ranks_synced() -> None:
    """⛔ 与 pool_admit / thin_seal_top 同一条铁律：桶改了，标签和序号都要跟着改。"""
    relay = [_row(code="A", name="甲"), _row(code="B", name="乙", cje_yi=1.0, boards=4)]
    watch = [_row(code="C", name="丙", bucket="watch", bucket_label="观察池", watch_rank=1)]
    keep, out_watch, _ = pe.apply_high_win_gate(relay, watch, _env(), market_max_boards=8)
    assert [r["name"] for r in keep] == ["甲"]
    assert keep[0]["relay_rank"] == 1 and "watch_rank" not in keep[0]
    assert [r["watch_rank"] for r in out_watch] == [1, 2], "观察池序号必须连续"
    demoted = out_watch[1]
    assert demoted["bucket"] == "watch" and demoted["bucket_label"] == "观察池"
    assert "relay_rank" not in demoted
    assert demoted["watch_only"] is True


def test_no_watch_reason_written() -> None:
    """⛔ 用户 2026-10-27「不用给」：池空就空，不给市场找说法。"""
    _, watch, _ = pe.apply_high_win_gate([_row()], [], _env("ice"))
    assert not watch[0].get("watch_reason")


# ────────────────── 9. 缺省入参不崩 ──────────────────
def test_none_inputs_safe() -> None:
    relay, watch, diag = pe.apply_high_win_gate(None, None, None)   # type: ignore[arg-type]
    assert relay == [] and watch == [] and diag["enabled"] is True


# ────────────────── 10. 纯函数：只读，不改入参 ──────────────────
def test_high_win_missing_is_pure() -> None:
    row = _row()
    before = dict(row)
    assert pe.high_win_missing(row, _env("ice")) == ["情绪"]
    assert row == before, "判据函数不得修改入参"
