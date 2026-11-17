#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冰点冻结 / 09:25 回暖解冻（2026-10-28 用户「冰点日若开盘后情绪转好 可以出手」）。

## 这份测试在锁什么

四条件闸里的「情绪」是**全市场级**条件，其余三条（题材龙 / 量能 / 梯队）是**个股级**。
所以冰点日被移出接力池的票不是判死刑，而是**冷冻**：

  · 只缺「情绪」→ 留解冻资格，等 09:25 竞价回暖可放行
  · 缺任何个股级条件 → ⛔ **永不解冻**，市场回暖救不了一只有硬伤的票

以及一条容易漏的：`stance=hibernate` 时名额 0，`open_relief` 本已放宽到 1，
但紧随其后的环境防守腿 `level=block` 又会压回 0 —— 回暖信号被自己的防守腿掐死
（2026-10-08 实证：`market_open_relief=True`、defense 5 条命中 block → `effective_quota=0`）。
`POOL_CALL_OPEN_RELIEF_OVERRIDE` 让回暖在 block 时保底 1 只。

## 回测依据（51 个归档交易日 × relay 票 · 次日开盘买→收盘卖 · 剔一字与竞价超窗）

| regime | 竞价回暖 | n | 均收益 | 胜率 | 中位 |
|---|---|---|---|---|---|
| ice   | 是 |  4 | +1.37% | 50%  | +0.68% |
| ice   | 否 | 11 | -3.97% | 27%  | -5.43% |
| risk_on  | 是 / 否 | 17 / 4 | +2.07% / -1.07% | 71% / 25% | — |
| neutral  | 是 / 否 | 22 / 28 | +1.50% / -1.78% | 55% / 36% | — |

三种环境**同向**，故不是只在冰点上偶然成立。⚠️ ice 组仅 4 条，幅度不可当真。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE))

from core import market_open, pool_call          # noqa: E402
from pipelines import pipeline_eod as pe         # noqa: E402


# ────────────────────────── 夹具 ──────────────────────────
def _row(**kw):
    """一个「四条件齐备」的接力票；用例只破坏想测的那一条。"""
    base = {
        "code": "603528", "name": "多伦科技", "boards": 4, "cje_yi": 8.1,
        "dragon_rank": 1, "is_sector_dragon": True, "sector_has_mates": True,
        "theme_mates_cnt": 17, "sector_is_lone": False, "theme_is_lone": False,
        "ladder_health": 10.0, "ladder_has_complete": True,
        "core_theme": "机器人", "bucket": "relay", "bucket_label": "接力池",
        "relay_rank": 1,
    }
    base.update(kw)
    return base


def _ice_env():
    """真冰点日环境：5 条全部命中（2026-10-27 实盘形态）。"""
    return {"regime": "ice", "sentiment_score": 1.0, "promote_rate": 19.2,
            "nuke_count": 11, "break_count": 3, "broken_rate": 44.7,
            "relay_feedback": {"pressure": False}}


def _zt_rows(n_leader=3, n_total=6):
    """昨涨停池：3 只连板 + 3 只首板（连板数要过 market_open.MIN_LEADERS）。"""
    return [{"code": f"00000{i}", "name": f"票{i}",
             "boards": 3 if i <= n_leader else 1, "cje_yi": 10.0}
            for i in range(1, n_total + 1)]


def _quotes_warm(rows):
    return {r["code"]: {"prev_close": 10.0, "auction_price": 10.2,
                        "auction_amount_yi": 0.2} for r in rows}


def _quotes_cold(rows):
    return {r["code"]: {"prev_close": 10.0, "auction_price": 9.7,
                        "auction_amount_yi": 0.02} for r in rows}


@pytest.fixture
def warm_read():
    rows = _zt_rows()
    read = market_open.build_market_open(rows, _quotes_warm(rows))
    assert read["ready"] is True, "夹具前提：竞价必须真的回暖"
    return read


@pytest.fixture
def cold_read():
    rows = _zt_rows()
    read = market_open.build_market_open(rows, _quotes_cold(rows))
    assert read["ready"] is False, "夹具前提：竞价必须真的没回暖"
    return read


def _run_pool(code, hw, *, read, stance="hibernate", frozen=True):
    """把一张「已冻结」的票塞进观察池，跑 09:25 裁决。"""
    c = _row(code=code, name=f"候选{code}", high_win_gate=list(hw), watch_only=True)
    if frozen:
        c["frozen"] = True
        c["unfreeze_on"] = "market_open_ready"
    cands = {"relay": [], "watch": [c], "environment": _ice_env(), "date": "2026-10-08"}
    rows = _zt_rows()
    qs = _quotes_warm(rows) if read.get("ready") else _quotes_cold(rows)
    res = pool_call.call_pool(cands, qs, plan={"stance": stance, "views": {}},
                              market_open=read, line_cores={}, sector_flow={},
                              zt_series={}, zt_days=[])
    view = res["views"].get(code) or {}
    env = ((res["meta"].get("defense") or {}).get("env") or {})
    return res, view, env


# ──────────────── 1. 盘后：冻结资格的分层（个股级永不解冻）────────────────
def test_only_emotion_missing_is_frozen() -> None:
    """只缺情绪 → 打 frozen 标 + 解冻条件。"""
    relay, watch, diag = pe.apply_high_win_gate([_row()], [], _ice_env())
    assert relay == []
    f = watch[0]
    assert f["frozen"] is True
    assert f["unfreeze_on"] == "market_open_ready"
    assert f["high_win_gate"] == ["情绪"]
    assert diag["frozen_count"] == 1
    assert diag["frozen"][0]["name"] == "多伦科技"
    assert diag["moved"][0]["frozen"] is True


def test_emotion_plus_stock_level_is_not_frozen() -> None:
    """⛔ 只缺情绪才能冻。掺个股级硬伤（题材龙）→ 不给解冻资格。

    ⛔ 2026-11-17 换样本：原来用「量能不足」当硬伤，但换手板 5 亿门槛被
       `leader_turnover_floor` 用 min() 稀释（已另记为疑似缺陷），1.0 亿不再判缺量能。
       改用「题材龙」（dragon_rank=3）这个稳定的个股级硬伤。
    """
    _, watch, diag = pe.apply_high_win_gate([_row(cje_yi=1.0, dragon_rank=3)], [], _ice_env())
    f = watch[0]
    assert "frozen" not in f and "unfreeze_on" not in f
    assert "题材龙" in f["high_win_gate"]
    assert diag["frozen_count"] == 0
    assert diag["moved"][0]["frozen"] is False


def test_lone_dragon_is_not_frozen() -> None:
    """孤军（题材只有自己）也是个股级问题，不能靠大盘回暖救。"""
    _, watch, _ = pe.apply_high_win_gate(
        [_row(sector_is_lone=True, theme_mates_cnt=1)], [], _ice_env())
    assert "frozen" not in watch[0]


def test_unfreeze_switch_off_freezes_nothing(monkeypatch) -> None:
    """`ENABLE_ICE_UNFREEZE=0` → 回退「冰点一票不出」，连冻结标都不打。"""
    monkeypatch.setenv(pe.HIGH_WIN_UNFREEZE_ENV, "0")
    _, watch, diag = pe.apply_high_win_gate([_row()], [], _ice_env())
    assert "frozen" not in watch[0]
    assert diag["frozen_count"] == 0
    assert diag["unfreeze_enabled"] is False


def test_market_level_missing_pure_helper() -> None:
    assert pe.high_win_market_level_missing(["情绪"]) is True
    assert pe.high_win_market_level_missing(["情绪", "量能"]) is False
    assert pe.high_win_market_level_missing(["梯队"]) is False
    assert pe.high_win_market_level_missing([]) is False
    assert pe.high_win_market_level_missing(None) is False


def test_qualified_row_keeps_no_frozen_flag() -> None:
    """四条件齐备的票不该带任何冻结痕迹（防脏字段串进卡面）。"""
    r = _row(frozen=True, unfreeze_on="market_open_ready", high_win_gate=["情绪"])
    relay, _, _ = pe.apply_high_win_gate([r], [], {"regime": "risk_on"})
    assert len(relay) == 1
    assert "frozen" not in r and "unfreeze_on" not in r and "high_win_gate" not in r


# ──────────────── 2. 09:25 裁决：解冻 / 不解冻 ────────────────
def test_frozen_ticket_unfrozen_when_market_warms(warm_read) -> None:
    """只缺情绪 + 竞价回暖 → 解冻放行。"""
    res, view, env = _run_pool("000001", ["情绪"], read=warm_read)
    assert view["view"] == "participate"
    assert view["unfrozen"] == "market_open"
    assert view["unfrozen_from"] == ["情绪"]
    assert env["level"] == "block", "昨收环境差是事实，level 不粉饰"
    assert env["relief_override"] is True


def test_stock_level_defect_stays_frozen_even_when_warm(warm_read) -> None:
    """⛔ 核心锁：缺情绪+量能，即便大盘回暖也**不放行**。"""
    res, view, _ = _run_pool("000002", ["情绪", "量能"], read=warm_read)
    assert view["view"] == "watch"
    assert "unfrozen" not in view
    assert "四条件缺" in str(view["reason"])


def test_frozen_ticket_stays_frozen_when_market_cold(cold_read) -> None:
    """回暖未成立 → 保持冻结。"""
    res, view, env = _run_pool("000003", ["情绪"], read=cold_read)
    assert view["view"] == "watch"
    assert "unfrozen" not in view
    assert env.get("relief_override") is None


def test_unfreeze_switch_off_keeps_ticket_blocked(warm_read, monkeypatch) -> None:
    """`POOL_CALL_ICE_UNFREEZE=False` → 即便回暖也不解冻（可逆开关）。"""
    monkeypatch.setattr(pool_call, "POOL_CALL_ICE_UNFREEZE", False)
    _, view, _ = _run_pool("000004", ["情绪"], read=warm_read)
    assert view["view"] == "watch"
    assert "unfrozen" not in view


# ──────────────── 3. 名额：回暖不能被防守腿掐死 ────────────────
def test_warm_market_keeps_one_slot_despite_block(warm_read) -> None:
    """10-08 现场复现：block 会压名额到 0，回暖时保底 1 只。"""
    res, _, env = _run_pool("000005", ["情绪"], read=warm_read, stance="hibernate")
    # 2026-11-17：回暖保底名额 1 → 2（冰点连续两日后放宽，见 shared_rules）
    assert res["meta"]["effective_quota"] == 2
    assert env["level"] == "block" and env["relief_override"] is True


def test_cold_market_still_zero_slot(cold_read) -> None:
    """没回暖 → 名额照旧归零（不放水）。"""
    res, _, _ = _run_pool("000005", ["情绪"], read=cold_read, stance="hibernate")
    assert res["meta"]["effective_quota"] == 0


def test_relief_override_switch_off_restores_zero(warm_read, monkeypatch) -> None:
    """`POOL_CALL_OPEN_RELIEF_OVERRIDE=False` → 回到「block 即归零」。"""
    monkeypatch.setattr(pool_call, "POOL_CALL_OPEN_RELIEF_OVERRIDE", False)
    res, _, env = _run_pool("000005", ["情绪"], read=warm_read, stance="hibernate")
    assert res["meta"]["effective_quota"] == 0
    assert env.get("relief_override") is None


def test_defend_stance_also_gets_relief(warm_read) -> None:
    """冰点日的 stance 常是 defend（名额 1），block 压到 0，回暖同样要保底。"""
    res, view, env = _run_pool("000006", ["情绪"], read=warm_read, stance="defend")
    assert res["meta"]["effective_quota"] == 2
    assert view["view"] == "participate"
    assert env["relief_override"] is True


# ──────────────── 4. 诚实降级：没有回暖读数不误放行 ────────────────
def test_no_market_open_read_never_unfreezes() -> None:
    """缺 market_open 读数（数据没到）→ 不判回暖，保持冻结。"""
    c = _row(code="000007", high_win_gate=["情绪"], watch_only=True,
             frozen=True, unfreeze_on="market_open_ready")
    cands = {"relay": [], "watch": [c], "environment": _ice_env(), "date": "2026-10-08"}
    res = pool_call.call_pool(cands, {}, plan={"stance": "hibernate", "views": {}},
                              market_open=None, line_cores={}, sector_flow={},
                              zt_series={}, zt_days=[])
    assert (res["views"].get("000007") or {}).get("view") == "watch"
    assert res["meta"]["effective_quota"] == 0


def test_unfreeze_does_not_write_watch_reason() -> None:
    """用户「不用给」：解冻与冻结都不写 `watch_reason`（卡面用 dragon_note 等自有字段）。"""
    _, watch, _ = pe.apply_high_win_gate([_row()], [], _ice_env())
    assert not watch[0].get("watch_reason")
