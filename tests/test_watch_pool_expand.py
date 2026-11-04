#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观察池扩容 / 占位 / 一字龙头（2026-11-04 用户三条要求）。

## 用户原话

> 「扩容+b占位，都要迭代！而且观察池 把一字的龙头也放进来一下 方便观察，现在都是准龙头」

## 三条各自的病灶（均实测，不是推测）

① **扩容**：`quant_score` 原为硬编码 `watch_sorted[:8]`。
   11-04 涨停 119 只，龙版传媒（3板·短剧6家·成交额达标）排 `selection_rank=11` 被截掉。

② **占位**：观察池每天重算，昨天在池、今天连板的票会被新票挤出去 ——
   与「观察」二字矛盾。11-03 观察池 3 只 2 板（龙版/紫竹/大晟）**11-04 全部晋级 3 板**。

③ **一字龙头**：`watch_kind` 的一字分支要求 `seal_ratio_pct >= 5%`，
   而一字板结构上就是「封死、量小」，封单数据经常拿不到 →
   11-04 全天 4 只一字板（广聚/大连友谊/高争民爆3板/大晟文化3板）**全部因缺数据被丢弃**。

## 根因还有一层（最关键）

`select_relay_watch` 里 `watch_cands` 用 `m["_cand"]` 判 `watch_kind`，
**但此时 `leader_role` 还没注入**（注入在后面的 `_restore`，从 `entries` 取）
→ `core` 判据恒 False → 几乎所有候选被误杀。
这是「观察池里几乎只剩一字/容量两类」的真正原因。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wealth_code"))

import core.quant_score as qs  # noqa: E402
import core.two_pool as tp  # noqa: E402


# ── ① 扩容 ────────────────────────────────────────────────────────────
def test_观察池容量默认12(monkeypatch):
    monkeypatch.delenv("WATCH_POOL_CAP", raising=False)
    assert qs._watch_cap() == 12


def test_容量可回退到8(monkeypatch):
    monkeypatch.setenv("WATCH_POOL_CAP", "8")
    assert qs._watch_cap() == 8


def test_容量非法值不崩(monkeypatch):
    monkeypatch.setenv("WATCH_POOL_CAP", "abc")
    assert qs._watch_cap() == 12, "非法值应回默认 12，不能抛异常"


# ── ② 占位 ────────────────────────────────────────────────────────────
def _m(code, boards):
    return {"code": code, "_cand": {"code": code, "boards": boards}, "lbc": boards}


def test_昨日在池且晋级才算占位():
    prev = {"600892": 2}
    assert qs._is_carryover_promoted(_m("600892", 3), prev) is True


def test_昨日在池但未晋级不占位():
    prev = {"600892": 3}
    assert qs._is_carryover_promoted(_m("600892", 3), prev) is False, "板数没涨不算晋级"
    assert qs._is_carryover_promoted(_m("600892", 2), prev) is False, "降板不算晋级"


def test_昨日不在池不占位():
    assert qs._is_carryover_promoted(_m("600999", 5), {"600892": 2}) is False


def test_板数缺失不编造():
    prev = {"600892": 2}
    assert qs._is_carryover_promoted({"code": "600892", "_cand": {"code": "600892"}}, prev) is False


@pytest.mark.parametrize("contract", ["0", "1"])
def test_普通观察理由不改变名单分数或资格(monkeypatch, contract):
    from copy import deepcopy

    monkeypatch.setenv("ENABLE_TWO_POOL_CONTRACT", contract)
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "1")
    monkeypatch.setattr(qs, "_WATCH_ALL_MULTI", True)
    monkeypatch.setattr(qs, "_WATCH_WIDEN", True)
    monkeypatch.setattr(qs, "_WATCH_CARRYOVER", True)
    monkeypatch.setattr(qs, "ICE_FREEZE_RELAY", True)
    monkeypatch.setattr(qs, "compute_raw_score", lambda c, *args: {"raw_score": c["score"]})
    monkeypatch.setattr(qs, "map_tactics_fields", lambda c, env, pulse, ai, raw: {
        "code": c["code"], "name": c["name"], "lbc": c["boards"],
        "_raw": raw["raw_score"], "_cand": c,
    })
    rows = [
        {"code": "600001", "name": "多板", "boards": 3, "score": 60},
        {"code": "600002", "name": "容量", "boards": 2, "score": 70, "style_tag": "容量"},
        {"code": "600003", "name": "准龙头", "boards": 2, "score": 80, "leader_role": "准龙头"},
        {"code": "600004", "name": "已有原因", "boards": 3, "score": 65,
         "watch_reason": "保留原始观察依据"},
        {"code": "600005", "name": "二板", "boards": 2, "score": 50, "watch_reason": ""},
    ]
    original = deepcopy(rows)
    args = ({"regime": "ice"}, None, None)
    relay, watch, diag, mode = qs.select_relay_watch(rows, *args, prev_pool_boards={"600001": 2})
    by_code = {r["code"]: r for r in watch}
    assert relay == []
    assert [r["code"] for r in watch] == ["600001", "600004", "600003", "600002", "600005"]
    assert by_code["600001"]["watch_reason"] == "多板持续观察，跟踪连板延续与题材强弱；不参与"
    assert by_code["600002"]["watch_reason"] == "容量观察，跟踪板块资金态度；不参与"
    assert by_code["600003"]["watch_reason"] == "准龙头补充观察，跟踪题材地位变化；不参与"
    assert by_code["600004"]["watch_reason"] == "保留原始观察依据"
    assert by_code["600005"]["watch_reason"] == "补充观察，跟踪后续表现；不参与"
    assert by_code["600001"]["carryover_promoted"] is True
    assert "_carryover_promoted" not in by_code["600001"]
    assert rows == original
    if contract == "1":
        assert all(r["participation_eligibility"] == "observe_only" and r["buyable"] is False for r in watch)
    # 用已有相同理由的输入对照：理由补齐不能改变任何其他输出。
    for row in rows:
        row["watch_reason"] = by_code[row["code"]]["watch_reason"]
    assert qs.select_relay_watch(rows, *args, prev_pool_boards={"600001": 2}) == (relay, watch, diag, mode)


# ── ③ 一字龙头 ────────────────────────────────────────────────────────
def _one_word_row(**patch):
    row = {
        "code": "600001", "name": "测试一字", "core_theme": "锂电池",
        "is_one_word": True, "leader_role": "准龙头",
        "is_sector_dragon": True, "dragon_rank": 1,
    }
    row.update(patch)
    return row


def test_一字龙头_封单数据缺失仍入观察池(monkeypatch):
    """⛔ 核心回归：封单拿不到 ≠ 封得不厚。用 unknown 当 false 是本次修的 bug。"""
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "1")
    assert tp.watch_kind(_one_word_row()) == "一字龙头"


def test_一字龙头_可回退(monkeypatch):
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "0")
    assert tp.watch_kind(_one_word_row()) is None, "关开关应回到「丢弃」的旧行为"


def test_一字龙头不可买(monkeypatch):
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "1")
    row = tp.mark_watch(_one_word_row(), "一字龙头")
    assert row["buyable"] is False, "一字封死，只能看不能买"
    assert row["watch_only"] is True
    assert row["watch_group"] == "一字龙头"
    assert row["watch_reason"], "必须写明为什么不参与"


def test_非龙头一字不进池(monkeypatch):
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "1")
    row = _one_word_row(leader_role=None, is_sector_dragon=False, dragon_rank=None)
    assert tp.watch_kind(row) is None, "一字但没龙头身份 → 不是「一字龙头」，不该入池"


def test_688与ST仍排除(monkeypatch):
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "1")
    assert tp.watch_kind(_one_word_row(code="688001")) is None
    assert tp.watch_kind(_one_word_row(name="ST测试")) is None


# ── ④ 上游已归入观察的票必须留住（finalize）──────────────────────────
def test_上游已分组为观察则保留(monkeypatch):
    """`高标/题材核心`、`补充观察` 两类不在 `watch_kind` 白名单内，
    但上游已经定了它们是观察票 → finalize 不得按「不符合两类观察资格」删掉。"""
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "1")
    row = {"code": "605577", "name": "龙版传媒", "boards": 3, "core_theme": "短剧/互动影游",
           "bucket": "watch", "watch_group": "高标/题材核心", "leader_role": "准龙头"}
    relay, watch, diag = tp.finalize([], [row], stage="post_risk")
    assert [w["name"] for w in watch] == ["龙版传媒"], f"被丢弃：{diag['excluded']}"
    assert watch[0]["buyable"] is False
    assert len(relay) == 0, "铁律：绝不从观察反提"


def test_没有分组的票仍被剔除(monkeypatch):
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "1")
    row = {"code": "600002", "name": "无分组", "boards": 1, "core_theme": "题材",
           "bucket": "watch"}          # 有 bucket 但没 watch_group
    relay, watch, diag = tp.finalize([], [row], stage="post_risk")
    assert len(watch) == 0
    assert [e["name"] for e in diag["excluded"]] == ["无分组"]


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 2 板及以上全部进观察池（2026-11-04 用户）
# ══════════════════════════════════════════════════════════════════════════
# 用户原话：「从 2 进 3 开始仔细观察，相当于 2 进 3 以上的票全部都要进观察池…
#            首板这边的票，有选择性地进观察池。」
# ⛔ 首板看不出东西（用户原话），2 进 3 才是要盯的位置。
def test_多板开关默认开启():
    """⛔ 绝不 `importlib.reload` 去验默认值：reload 会替换模块对象，
    其他模块已持有的引用仍指向旧版本（2026-11-04 实测污染 `test_snapshot_guard` 4 例）。
    默认值直接读模块属性即可（`_WATCH_ALL_MULTI` 在 import 时按环境变量求值）。"""
    import os
    assert "ENABLE_WATCH_ALL_MULTI_BOARD" not in os.environ, \
        "本用例要求环境未覆盖该开关（否则测的不是默认值）"
    assert qs._WATCH_ALL_MULTI is True


def test_硬性排除只挡688与ST():
    assert qs._watch_hard_excluded({"code": "688001", "name": "科创"}) is True
    assert qs._watch_hard_excluded({"code": "600001", "name": "ST测试"}) is True
    assert qs._watch_hard_excluded({"code": "600001", "name": "正常股"}) is False
    # ⛔ 没题材、非龙头、低成交额都**不**属于硬性排除 —— 它们只影响接力资格，不影响能不能看
    assert qs._watch_hard_excluded({"code": "600001", "name": "甲", "core_theme": ""}) is False


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 竞价溢价榜（2026-11-04 用户）
# ══════════════════════════════════════════════════════════════════════════
# 用户原话：「大于 0 至少是有溢价的，那就非常不错；如果没有溢价，那就要谨慎。」
def _zt(code, name, boards, theme="测试"):
    return {"code": code, "name": name, "boards": boards, "sector": theme}


def _q(prev, price, amount=1.0):
    return {"prev_close": prev, "auction_price": price, "auction_amount_yi": amount}


def test_溢价榜只收2板以上():
    from core import auction_premium as ap

    rows = [_zt("600001", "首板甲", 1), _zt("600002", "二板乙", 2), _zt("600003", "三板丙", 3)]
    quotes = {"600001": _q(10, 11), "600002": _q(10, 11), "600003": _q(10, 9)}
    res = ap.build_premium_board(rows, quotes)
    assert res["count"] == 2, "首板不入榜"
    assert {r["name"] for r in res["rows"]} == {"二板乙", "三板丙"}


def test_溢价判定_大于0为有溢价_小于0为谨慎():
    from core import auction_premium as ap

    rows = [_zt("600002", "溢价", 2), _zt("600003", "折价", 2), _zt("600004", "平开", 2)]
    quotes = {"600002": _q(10, 10.5), "600003": _q(10, 9.8), "600004": _q(10, 10)}
    res = ap.build_premium_board(rows, quotes)
    by = {r["name"]: r for r in res["rows"]}
    assert by["溢价"]["verdict"] == ap.VERDICT_PREMIUM
    assert by["折价"]["verdict"] == ap.VERDICT_DISCOUNT
    assert by["平开"]["verdict"] == ap.VERDICT_FLAT
    assert res["premium_count"] == 1
    assert res["discount_count"] == 1


def test_报价缺失不当平开():
    """⛔ 拿不到报价 ≠ 平开。缺数据的票进 `no_quote`，不参与溢价统计。"""
    from core import auction_premium as ap

    rows = [_zt("600002", "有价", 2), _zt("600003", "无价", 2)]
    res = ap.build_premium_board(rows, {"600002": _q(10, 11)})
    assert res["count"] == 1
    assert res["no_quote"] == ["无价"]


def test_热度等于同题材昨日涨停家数():
    from core import auction_premium as ap

    rows = [_zt("600002", "甲", 2, "芯片"), _zt("600003", "乙", 2, "芯片"),
            _zt("600004", "丙", 1, "芯片"), _zt("600005", "丁", 3, "黄金")]
    quotes = {c: _q(10, 10.5) for c in ("600002", "600003", "600004", "600005")}
    res = ap.build_premium_board(rows, quotes)
    by = {r["name"]: r for r in res["rows"]}
    assert by["甲"]["theme_hot"] == 3, "含首板：线宽本身就是强度"
    assert by["丁"]["theme_hot"] == 1


def test_按板数降序排():
    from core import auction_premium as ap

    rows = [_zt("600002", "二板", 2), _zt("600003", "三板", 3), _zt("600004", "四板", 4)]
    quotes = {c: _q(10, 10.5) for c in ("600002", "600003", "600004")}
    res = ap.build_premium_board(rows, quotes)
    assert [r["boards"] for r in res["rows"]] == [4, 3, 2]


def test_按板数分档统计():
    from core import auction_premium as ap

    rows = [_zt("600002", "二板甲", 2), _zt("600003", "二板乙", 2), _zt("600004", "三板", 3)]
    quotes = {"600002": _q(10, 10.5), "600003": _q(10, 10.2), "600004": _q(10, 9.5)}
    res = ap.build_premium_board(rows, quotes)
    assert res["by_boards"]["2"]["count"] == 2
    assert res["by_boards"]["2"]["premium_count"] == 2
    assert res["by_boards"]["3"]["premium_count"] == 0
