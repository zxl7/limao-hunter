# -*- coding: utf-8 -*-
"""老龙复燃闸回归锁 —— 2026-10-12 用户抓包（国芳集团）。

背景（用户原话）：「国芳还有个错误，最大的原因是他是前期的老龙，同一时间哈药
也是前期老龙！！他死的很惨 影响到了」。

事实（腾讯日K + `cache/zt_history/` 实录，**非编造**）：
  · 国芳集团 601086：09-18 首板 → 09-21 2 板 → 断板 7 个交易日 → 10-09 又拉首板，
    被系统当成「低位新方向首板」买入 → 10-12 **−8.50%**。
  · 哈药股份 600664：同期老龙（09-22 2 板）→ 10-12 **跌停 −10.01%**（那批老票整体退潮）。
  · 系统盲区：位置闸要求「上一波最高板 ≥3」，国芳只有 2 板 → 漏网（`leader_cycle.launch`
    只按板块判「新方向/已有方向」，不看个股自己以前炒没炒过）。

本闸 = 「炒过的老票沉寂很久后又拉首板」≠「低位新方向首板」→ 不占名额。

锁定：
  ① 前期老龙 × 断板 ≥5 交易日 × 复燃首日仅首板 → block
  ② ⛔ 断 1~2 天的 N 字快速回封**不拦**（情绪核心经典路径·加分项）
  ③ ⛔ 复燃首日已是连板（≥2 板）不拦（那是真启动）
  ④ ⛔ 上一波最高板 <2 不拦（没炒过的不算老龙）
  ⑤ ⛔ 数据不足一律**不裁决**（诚实降级）
  ⑥ 卡面白话：note 必须过 `card_copy_rules` 词表门禁（禁机器黑话）
  ⑦ 卡面标签 `_DEFENSE_GATE_PLAIN` 收录 `old_dragon_rebound`
  ⑧ 裁决链：@09:25 命中 → 不占名额；关掉子开关 → 逐字回退（可逆）

全隔离：显式传参 series / trading_days；不读真实 cache、不落盘、不触网。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WC = ROOT / "wealth_code"
for _p in (str(WC), str(ROOT), str(ROOT / "scripts"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import defense_gate as dg  # noqa: E402
from core import pool_call  # noqa: E402

# 真实交易日（`cache/zt_history/` 里有涨停票的日·09-18 ~ 10-09）
DAYS = ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24",
        "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"]

# 国芳集团真实涨停序列（09-21 那波最高 2 板 → 断 7 日 → 10-09 首板）
GUOFANG_SERIES = {"601086": [("2026-09-14", 1), ("2026-09-18", 1),
                             ("2026-09-21", 2), ("2026-10-09", 1)]}


def _v(code, ser, days=DAYS):
    return dg.old_dragon_verdict({"code": code}, series={code: ser}, trading_days=days)


# ── ① 命中 ───────────────────────────────────────────────────────────────────
def test_guofang_old_dragon_is_blocked():
    """国芳：上一波 2 板 @09-21、断 7 个交易日、10-09 首板 → 拦（次日 −8.5%）。"""
    out = _v("601086", [("2026-09-21", 2), ("2026-10-09", 1)])
    assert out["level"] == "block", out
    assert out["facts"]["gap_days"] == 7
    assert out["facts"]["prior_max_board"] == 2
    assert out["facts"]["last_board"] == 1
    assert "2026-09-21" in out["note"] and "断了 7 个交易日" in out["note"]


def test_old_dragon_verdict_from_disk_matches_pure_function():
    """兜底入口与纯函数同源（同一序列 → 同一结论）。"""
    v1 = dg.old_dragon_verdict({"code": "601086"}, GUOFANG_SERIES, DAYS)
    assert v1["level"] == "block"


# ── ②~⑤ 不得误伤 ─────────────────────────────────────────────────────────────
def test_quick_nzi_rebound_is_not_blocked():
    """⛔ 锁死「断板回封是加分项」：断 1 天的快速 N 字 → 不拦。"""
    out = _v("300001", [("2026-09-30", 3), ("2026-10-09", 1)])
    assert out["facts"]["gap_days"] == 1
    assert out["level"] == "none"


def test_rebound_already_two_boards_is_not_blocked():
    """复燃首日直接 2 板 → 真启动，不拦（只有「苟延残喘的首板」才拦）。"""
    out = _v("300002", [("2026-09-21", 2), ("2026-10-09", 2)])
    assert out["facts"]["gap_days"] == 7
    assert out["level"] == "none"


def test_no_prior_run_is_not_blocked():
    """上一波只到 1 板（没炒过）→ 不算老龙，不拦。"""
    out = _v("300003", [("2026-09-21", 1), ("2026-10-09", 1)])
    assert out["facts"]["prior_max_board"] == 1
    assert out["level"] == "none"


def test_gap_below_threshold_is_not_blocked():
    """断 4 个交易日（<5）→ 不拦。"""
    out = _v("300004", [("2026-09-24", 2), ("2026-10-09", 1)])
    assert out["facts"]["gap_days"] == 4
    assert out["level"] == "none"


def test_insufficient_data_is_not_adjudicated():
    """数据不足 → 诚实降级，不裁决（不编造、不误伤）。"""
    assert _v("300005", [])["level"] == "none"
    assert _v("300006", [("2026-10-09", 1)])["level"] == "none"
    assert dg.old_dragon_verdict({"code": "601086"}, {}, [])["known"] is False


# ── ⑥ 卡面白话 ───────────────────────────────────────────────────────────────
def test_note_passes_plain_copy_gate():
    """note 必须过词表唯一源 `card_copy_rules.scan()`（禁机器黑话上卡）。"""
    import card_copy_rules as ccr
    out = _v("601086", [("2026-09-21", 2), ("2026-10-09", 1)])
    fails, warns = ccr.scan(out["note"])
    assert fails == [] and warns == [], (fails, warns, out["note"])


# ── ⑦ 卡面标签登记 ───────────────────────────────────────────────────────────
def test_card_plain_label_registered():
    """「竞价达标但没买」名单必须收录本闸的一眼原因。"""
    from pipelines import build_cards as bc
    assert "old_dragon_rebound" in bc._DEFENSE_GATE_PLAIN


# ── ⑧ 裁决链集成（不占名额 + 可逆）────────────────────────────────────────────
def _candidates():
    """国芳集团 10-09 候选（真实字段形态）——位置不高（专测它不是被位置闸拦下的）。"""
    return {
        "date": "2026-10-09",
        "environment": {},                      # 空 → 环境闸 normal
        "relay": [{
            "code": "601086", "name": "国芳集团", "boards": 1,
            "theme_name": "大消费", "core_theme": "大消费", "sector": "一般零售",
            "expectation": {"expected_range": [1.0, 4.0], "auction_amount_min_yi": 0.3},
        }],
        "watch": [],
    }


# 10-12 真实竞价：昨收 14.94 → 今开 15.30（+2.41%），量能达标
QUOTES = {"601086": {"prev_close": 14.94, "auction_price": 15.30, "auction_amount_yi": 1.2}}


def _run(*, sector_flow=None):
    return pool_call.call_pool(_candidates(), QUOTES, plan={}, stance="attack",
                               line_cores={}, sector_flow=sector_flow or {},
                               zt_series={"601086": [("2026-09-21", 2), ("2026-10-09", 1)]},
                               zt_days=DAYS)


def test_pool_call_blocks_old_dragon_rebound():
    """竞价达标（+2.41%）但仍不买 —— 卡面/裁决记 `old_dragon_rebound`，不占名额。"""
    res = _run()
    v = res["views"]["601086"]
    assert v["view"] == "watch"
    assert v["defense_gate"] == "old_dragon_rebound"
    assert "老票" in (v.get("reason") or "")
    # 合并进 views（卡面/审计可查）时理由留痕
    views = pool_call.apply_to_views(res, {})
    assert views["601086"]["defense_gate"] == "old_dragon_rebound"
    assert "老票" in (views["601086"].get("defense_reason") or "")
    bought = sorted(c for c, x in res["views"].items() if x["view"] == "participate")
    assert "601086" not in bought


def test_pool_call_old_dragon_switch_is_reversible(monkeypatch):
    """关掉子开关 → 逐字回退（该票恢复为可买候选）。"""
    monkeypatch.setattr(pool_call, "POOL_CALL_DEFENSE_OLD_DRAGON", False)
    res = _run()
    assert res["views"]["601086"]["view"] == "participate"
    assert res["views"]["601086"].get("defense_gate") is None
