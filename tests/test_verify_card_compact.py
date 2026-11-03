#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价卡「简洁模式」契约锁（2026-10-21 用户「早盘推送内容过于冗余·简洁一点但核心数据不要少」）。

三档详略（判据 = 今天要不要动手，见 `build_cards._compact_drops`）：
  · 买入票      → 只丢 `历史股性`（与 `📈 历史基因` 行同数据）
  · 观察票      → 再丢 `题材催化`
  · 放弃/没达标 → 再丢 `昨日走势`，只留 `⚠️ 风险`
⛔ 不变量：竞价 gap / 额·需要额 / 预期区间 / 板块 / 板数 / 历史基因 / 9:25 判定 + 理由
   一律照旧 —— 这几个字段有一条被吞，本文件必须红。
⛔ 可逆：`VERIFY_CARD_COMPACT=0` → 逐字节回到旧全量渲染。
"""
import pipelines.build_cards as bc



# ── 分层隔离：本文件只测「点评段三档详略（compact 层）」────────────────────────────────────
# ⛔ 2026-10-21：`VERIFY_CARD_LEAN`（收紧段取舍）是**另一层**开关，
#    与本文所测维度无关。不隔离会让本文件的断言随 lean 默认值漂移
#    （实测：lean 默认开启后有 5 例因「整段不再渲染」而假红）。
#    lean 自己的契约在 `tests/test_verify_card_lean.py`。
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _isolate_lean(monkeypatch):
    monkeypatch.setenv("VERIFY_CARD_LEAN", "0")
    # ⛔ 2026-10-21：`VERIFY_CARD_UNIFY_MECH`（机械口径并入 AI 判定）是**第四层**，
    #    会把机械段从「逐票完整展示」改成「一行口径 + 标题行胶囊」。本文件测的是
    #    点评段三档详略，与「机械口径放哪」无关 —— 不隔离会让断言随融合默认值假红。
    #    融合契约在 `tests/test_verify_card_lean.py`。
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "0")


def _blocks(card):
    return [e["text"]["content"] for e in card["elements"] if e.get("text")]


def _text(card):
    return "\n".join(_blocks(card))


def _rec(code, name, status, gap, narrative, *, ai_view=None, boards=2):
    oc = {"decision_status": status, "gap_pct": gap,
          "can_enter": status == "buy", "mech_can_enter": status == "buy",
          "auction_amount_yi": 0.52, "auction_amount_need_yi": 0.89,
          "signal_label": "符合预期", "decision_label": "符合预期"}
    if ai_view:
        oc["ai_view"] = ai_view
        oc["pool_reason"] = "开在 +2.00% 达标，名额内 → 买入"
    return {"code": code, "name": name, "boards": boards, "bucket_label": "接力池",
            "core_theme": "机器人", "industry_sector": "通用设备",
            "narrative": narrative,
            "expectation": {"expected_range": ["0.0", "2.4"]},
            "performance": {"open_check": oc}}


_NARR = ("今日走势：9 点 34 分封板，被砸开 3 次又回封，带起 7 家。\n"
         "题材催化：做谐波减速器，小批量供货，控股股东增持。\n"
         "历史股性：近一年 7 次涨停，历史最长 4 板。\n"
         "风险：封单只占流通盘 0.67%，很薄，高开容易回落。")


def _card(recs):
    return bc.build_verify_card({"payload": {"records": recs},
                                 "summary": {"total_candidates": len(recs),
                                             "buy_signals": 1, "hit_signals": 0,
                                             "covered": 0, "nav_latest": 1.0,
                                             "trade_date10": "2026-10-21"}})


def _strip(s):
    import re
    return re.sub(r"<[^>]+>", "", s)


def test_buy_row_keeps_all_core_data_and_drops_only_gene(monkeypatch):
    monkeypatch.delenv("VERIFY_CARD_COMPACT", raising=False)
    card = _card([_rec("603636", "南威软件", "buy", 0.67, _NARR, ai_view="participate")])
    s = _text(card)
    plain = _strip(s)
    # 核心数据一个不少
    assert "+0.67%" in plain                     # 竞价
    assert "0.52/0.89" in plain                  # 额 / 需要额
    assert "预期0.0~2.4%" in plain or "0.0~2.4%" in plain
    assert "接力池" in plain and "机器人" in plain  # 池 + 题材
    assert "历史基因" in plain                   # 股性（唯一一处）
    assert "今日9:25判：买入" in plain           # 9:25 判定
    # 点评：走势 + 题材 + 风险 3 段，股性已并入 gene 行
    assert "**昨日走势**" in s and "**题材催化**" in s
    assert "**历史股性**" not in s
    assert "⚠️ 风险" in plain


def test_watch_row_drops_theme_keeps_walk_and_risk(monkeypatch):
    monkeypatch.delenv("VERIFY_CARD_COMPACT", raising=False)
    card = _card([_rec("002164", "宁波东力", "pending", 1.07, _NARR, ai_view="watch")])
    s = _text(card)
    assert "**昨日走势**" in s            # 观察票还要动手 → 走势保留
    assert "**题材催化**" not in s        # 讲故事砍掉
    assert "**历史股性**" not in s
    assert "⚠️ 风险" in _strip(s)


def test_reject_row_keeps_only_metrics_and_verdict(monkeypatch):
    "没达标的票：只留「指标 + 历史基因 + 判定」，点评整段不上卡（本就不买）。"
    monkeypatch.delenv("VERIFY_CARD_COMPACT", raising=False)
    card = _card([_rec("301313", "凡拓数创", "reject", 6.56, _NARR, ai_view="skip")])
    s = _text(card)
    assert "**昨日走势**" not in s and "**题材催化**" not in s
    assert "+6.56%" in _strip(s)          # 竞价数据仍在
    assert "历史基因" in s                 # 股性仍在


def test_switch_off_restores_full_render(monkeypatch):
    "回退开关：VERIFY_CARD_COMPACT=0 → 点评恢复 4 段（含历史股性）。"
    recs = [_rec("002164", "宁波东力", "pending", 1.07, _NARR, ai_view="watch")]
    monkeypatch.setenv("VERIFY_CARD_COMPACT", "0")
    off = _text(_card(recs))
    assert "**历史股性**" in off and "**题材催化**" in off

    monkeypatch.delenv("VERIFY_CARD_COMPACT")
    on = _text(_card(recs))
    assert len(_strip(on)) < len(_strip(off))


def test_no_buy_row_never_dropped_even_when_ai_view_missing(monkeypatch):
    "旧数据无 ai_view 字段：decision_status=buy 仍按「要动手」给全量，⛔ 不误压成一行。"
    monkeypatch.delenv("VERIFY_CARD_COMPACT", raising=False)
    card = _card([_rec("000993", "闽东电力", "buy", 1.2, _NARR)])
    s = _text(card)
    assert "**昨日走势**" in s and "**题材催化**" in s


def test_linked_theme_card_uses_complete_pool_fact_not_legacy_max_count():
    "完整涨停池有3家、外部旧计数报8家时，卡面必须展示3家，不能把两源混成8家。"
    row = _rec("000001", "题材梯队", "buy", 1.2, _NARR, ai_view="participate")
    row.update({
        "theme_name": "机器人",
        "theme_mates_cnt": 8,
        "theme_has_sector_effect": True,
        "linkage_facts": {
            "schema": "linkage_facts_v1", "theme": "机器人",
            "eod_status": "linked", "theme_pool_zt_count": 3,
            "theme_reported_zt_count": 8,
        },
    })
    plain = _strip(_text(_card([row])))
    assert "机器人3家涨停" in plain
    assert "机器人8家涨停" not in plain


def test_standalone_missing_mate_quote_is_shown_as_data_gap_not_no_support():
    "竞价成员报价缺失只能说数据不足，⛔ 不得写成今早没人高开或板块没人跟。"
    row = _rec("000002", "独苗数据缺口", "buy", 1.2, _NARR, ai_view="participate")
    row.update({
        "linkage_facts": {
            "schema": "linkage_facts_v1", "theme": "机器人",
            "eod_status": "standalone", "theme_pool_zt_count": 1,
        },
        "linkage_discipline": {"auction_status": "unknown"},
    })
    plain = _strip(_text(_card([row])))
    assert "昨日同题材仅它涨停·今早同题材数据不足" in plain
    assert "今早没人高开" not in plain


def test_linkage_gate_keeps_watch_row_and_plain_reason(monkeypatch):
    "低板独苗有有效同题材报价但全未红开：票仍显示，且白话说明今天先观察。"
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "1")
    row = _rec("000003", "独苗待确认", "pending", 1.2, _NARR, ai_view="watch")
    row["performance"]["open_check"]["mech_can_enter"] = True
    row.update({
        "linkage_facts": {
            "schema": "linkage_facts_v1", "theme": "机器人",
            "eod_status": "standalone", "theme_pool_zt_count": 1,
        },
        "linkage_discipline": {"auction_status": "no_support"},
    })
    row["performance"]["open_check"].update({
        "linkage_gate": True,
        "pool_reason": "开在 +1.20% 达标，但昨日同题材仅本股涨停，今早同方向2家报价均未红开 → 今天先看，不买",
    })
    plain = _strip(_text(_card([row])))
    assert "独苗待确认" in plain
    assert "同题材没跟上" in plain
    # 精简卡标题胶囊负责说明“今天为何不动”，昨日独苗事实在同一行的题材结构标签展示。
    assert "昨日同题材仅它涨停" in plain


def test_sidecar_stage_hides_new_linkage_facts_from_verify_card(monkeypatch):
    "sidecar_record 只留痕：新联动事实不能改变竞价卡的可见文案。"
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "sidecar_record")
    row = _rec("000004", "仅留痕", "buy", 1.2, _NARR, ai_view="participate")
    row.update({
        "linkage_facts": {
            "schema": "linkage_facts_v1", "theme": "机器人",
            "eod_status": "standalone", "theme_pool_zt_count": 1,
        },
        "linkage_discipline": {"auction_status": "unknown"},
    })
    plain = _strip(_text(_card([row])))
    assert "昨日同题材仅它涨停" not in plain
    assert "今早同题材数据不足" not in plain


def test_card_explain_stage_shows_unknown_as_data_gap(monkeypatch):
    "开放卡面后仍须把无成员报价表述为数据不足。"
    monkeypatch.setenv("THEME_LINKAGE_ROLLOUT_STAGE", "card_explain")
    row = _rec("000005", "数据不足", "buy", 1.2, _NARR, ai_view="participate")
    row.update({
        "linkage_facts": {
            "schema": "linkage_facts_v1", "theme": "机器人",
            "eod_status": "standalone", "theme_pool_zt_count": 1,
        },
        "linkage_discipline": {"auction_status": "unknown"},
    })
    plain = _strip(_text(_card([row])))
    assert "昨日同题材仅它涨停·今早同题材数据不足" in plain
    assert "今早没人高开" not in plain
