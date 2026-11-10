# -*- coding: utf-8 -*-
"""2026-11-10 用户「逻辑错误！这些票全部都要有可交易的权限」回归锁

旧口径（2026-11-03 两池契约）：观察池 = `observe_only / buyable=False`，
09:25 只有 `state` 类能翻案，`restricted` / `hard` 一律「今天只看不买」。
等于**盘后分组一票否决今早竞价**——用户判定这是逻辑错误。

新口径（`POOL_CALL_ALL_TRADEABLE` 默认 1）：
候选池里每一只都具备交易资格，**买不买只由 09:25 竞价现场的闸门决定**
（价格/量能/顶部空间/封板质量/stance 名额）；盘后分组不再预先剥夺。

锁定：
  ① 默认开；`POOL_CALL_ALL_TRADEABLE=0` 逐字回退旧口径（可逆，旧断言见
     `test_observe_recheck.py` / `test_recheck_unlock_downstream.py`）；
  ② `rechecked_entry` 不再要求 `recheck_class == "state"`；
  ③ restricted 票确实进入完整裁决链（带 `auction_recheck`、`rechecked_from_observe`）；
  ④ **物理买不进仍不买**：一字封死不会因为「全池可交易」就变成可成交。
"""
import sys
from pathlib import Path

import pytest

WC = Path(__file__).resolve().parents[1] / "wealth_code"
for _p in (str(WC), str(WC / "pipelines"), str(WC / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import pool_call as pc           # noqa: E402
from core import two_pool as tp            # noqa: E402


@pytest.fixture(autouse=True)
def _default_flag(monkeypatch):
    """默认口径（不设 env）——本文件锁新行为。"""
    monkeypatch.delenv("POOL_CALL_ALL_TRADEABLE", raising=False)
    monkeypatch.setenv("POOL_CALL_RECHECK_UNLOCK", "1")
    monkeypatch.setattr(pc, "POOL_CALL_RECHECK_OBSERVE", True)


def _watch(code="000001", *, one_word=False, **kw):
    r = {
        "code": code, "name": f"票{code}", "boards": 2,
        "sector": "测试题材", "core_theme": "测试题材",
        "bucket": "watch", "bucket_label": "观察池",
        "participation_eligibility": "observe_only",
        "watch_only": True, "buyable": False,
        "watch_group": "补充观察",
        "watch_reason": "前置准入未过（昨日同线核心已断板）—— 只观察，不参与",
        "is_one_word": one_word,
        "expectation": {"expected_range": [1.0, 4.0], "auction_amount_min_yi": 1.0},
    }
    r.update(kw)
    return r


def _q(amt=2.0, gap=3.0):
    return {"prev_close": 10.0,
            "auction_price": round(10.0 * (1 + gap / 100.0), 3),
            "auction_amount_yi": amt}


def _call(rows, *, amt=2.0, gap=3.0, stance="attack"):
    return pc.call_pool(
        {"relay": [], "watch": rows},
        {str(r["code"]): _q(amt, gap) for r in rows},
        plan={"stance": stance, "views": {}}, stance=stance,
        line_cores={}, sector_flow={}, zt_series={}, zt_days=[],
        market_open=None, relay_plan=None)


def test_flag_default_on():
    assert tp.all_tradeable_enabled() is True


def test_flag_off_restores_old_contract(monkeypatch):
    monkeypatch.setenv("POOL_CALL_ALL_TRADEABLE", "0")
    assert tp.all_tradeable_enabled() is False


def test_rechecked_entry_no_longer_requires_state():
    row = {"view": "participate", "rechecked_from_observe": True,
           "recheck_class": "restricted"}
    assert tp.rechecked_entry(row) is True, "非 state 类也应能取得入场资格"


def test_restricted_watch_enters_full_chain():
    r = _watch()
    res = _call([r])
    v = res["views"]["000001"]
    assert v.get("rechecked_from_observe") is True
    assert (v.get("auction_recheck") or {}).get("class") == "restricted"
    assert "今天只看不买" not in str(v.get("reason") or ""), "不得再用盘后一句话否掉竞价"


def test_old_contract_reason_when_flag_off(monkeypatch):
    monkeypatch.setenv("POOL_CALL_ALL_TRADEABLE", "0")
    v = _call([_watch()])["views"]["000001"]
    assert v["view"] == "watch"
    assert "今天只看不买" in str(v.get("reason") or "")


def test_one_word_still_not_buyable():
    """全池可交易 ≠ 一字能成交：物理买不进仍然是买不进。"""
    # 一字板 = 竞价直接顶到涨停（gap ≈ 涨停幅度）→ zone=above → 物理买不进
    v = _call([_watch("000002", one_word=True)], gap=10.0)["views"]["000002"]
    assert v["view"] == "skip", "封死买不进必须 skip，不能因为全池可交易就标记 participate"
