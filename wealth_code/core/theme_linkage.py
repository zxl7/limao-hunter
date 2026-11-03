#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材联动事实与交易纪律（纯函数、无 IO）。

用户纪律：个股强不等于可交易；低位接力优先参与有题材广度、梯队与竞价承接的方向，
独立强势只能观察或显著降级。模块刻意拆成两层：

* ``linkage_facts_v1``：昨日冻结的客观结构（题材涨停家数、来源与数据质量）；
* ``linkage_discipline_v1``：消费事实后给出的可逆交易纪律，不把判断反写成事实。

数据缺失绝不写成“没有板块支持”：只有完整涨停池明确显示该题材仅 1 只涨停时，
才认定为 ``standalone``；当天竞价没有可比成员时标 ``unknown``，不伪造“0 红开”。
"""
from __future__ import annotations

import os
from typing import Any

_INVALID_THEMES = {"", "其他", "—", "-", "无", "未知"}
VALID_MODES = {"off", "annotate", "soft", "strict"}
VALID_ROLLOUT_STAGES = {
    "sidecar_record",
    "card_explain",
    "candidate_prefer",
    "auction_gate",
    "strict",
}
_ROLLOUT_RANK = {
    "sidecar_record": 0,
    "card_explain": 1,
    "candidate_prefer": 2,
    "auction_gate": 3,
    "strict": 4,
}
LOW_BOARD_MAX = 2


def linkage_mode() -> str:
    """返回当前交易纪律模式；非法值诚实回到 soft。"""
    mode = str(os.environ.get("THEME_LINKAGE_CANDIDATE_MODE", "soft") or "soft").strip().lower()
    return mode if mode in VALID_MODES else "soft"


def linkage_rollout_stage() -> str:
    """返回联动纪律的工程发布阶段。

    发布阶段只界定事实在哪些产物可见、以及竞价门是否接管终态；交易纪律的
    实际强弱仍只由 ``THEME_LINKAGE_CANDIDATE_MODE`` 决定。默认 auction_gate
    是为兼容已上线的 soft 竞价门，不会因引入阶段字段改变既有裁决。
    """
    stage = str(os.environ.get("THEME_LINKAGE_ROLLOUT_STAGE", "auction_gate") or "auction_gate").strip().lower()
    return stage if stage in VALID_ROLLOUT_STAGES else "auction_gate"


def rollout_allows_card_explain() -> bool:
    """阶段是否允许在卡面展示联动白话。"""
    return _ROLLOUT_RANK[linkage_rollout_stage()] >= _ROLLOUT_RANK["card_explain"]


def rollout_allows_auction_gate() -> bool:
    """阶段是否允许联动纪律改变 09:25 交易终态。"""
    return _ROLLOUT_RANK[linkage_rollout_stage()] >= _ROLLOUT_RANK["auction_gate"]


def rollout_allows_candidate_prefer() -> bool:
    """阶段是否允许输出展示/预案优先级；不代表可改 raw_score 或分池。"""
    return _ROLLOUT_RANK[linkage_rollout_stage()] >= _ROLLOUT_RANK["candidate_prefer"]


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_eod_facts(candidate: dict[str, Any]) -> dict[str, Any]:
    """基于盘后完整涨停池的同题材结构，冻结一条可追溯事实。

    ``theme_pool_zt_count`` 是完整涨停池按冻结题材映射聚合的数量，属于是否独苗的
    主判据；东财板块计数仅作交叉校验，不能以单一较大外部数值凭空把独苗改成联动。
    """
    theme = str(candidate.get("theme_name") or candidate.get("core_theme") or "").strip()
    pool_count = _to_int(candidate.get("theme_pool_zt_count"))
    reported_count = _to_int(candidate.get("theme_reported_zt_count"))
    fallback_count = _to_int(candidate.get("theme_mates_cnt"))

    if theme in _INVALID_THEMES:
        status = "unknown"
        quality = "missing_theme"
    elif pool_count is None:
        # 兼容旧候选文件，但不把旧的 max(pool, external) 当作可确认的独苗证据。
        status = "unknown"
        quality = "legacy_count_only" if fallback_count is not None else "missing_pool_count"
    elif pool_count <= 0:
        status = "unknown"
        quality = "invalid_pool_count"
    elif pool_count == 1:
        status = "standalone"
        quality = "complete_pool"
    else:
        status = "linked"
        quality = "complete_pool"

    # 外部板块数据仅说明是否与完整涨停池口径相符，不改主事实。
    source_conflict = (
        pool_count is not None and reported_count is not None
        and reported_count > 0 and pool_count != reported_count
    )
    return {
        "schema": "linkage_facts_v1",
        "theme": theme or None,
        "eod_status": status,                 # linked / standalone / unknown
        "theme_pool_zt_count": pool_count,
        "theme_reported_zt_count": reported_count,
        "theme_effective_zt_count": fallback_count,
        "source_conflict": source_conflict,
        "data_quality": quality,
        "mapping_fingerprint": candidate.get("theme_mapping_fingerprint"),
    }


def annotate_eod(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为候选写入联动事实及盘后提示；不改分、不删池、不改变排序。"""
    mode = linkage_mode()
    stage = linkage_rollout_stage()
    for candidate in candidates:
        # 盘后只冻结可追溯事实和展示级提示；不得把竞价纪律预写成交易终态。
        facts = build_eod_facts(candidate)
        boards = _to_int(candidate.get("boards")) or 0
        status = facts["eod_status"]
        if mode == "off":
            action, note = "neutral", "板块联动纪律已关闭"
        elif status == "linked":
            action, note = "prefer", "同题材有涨停梯队"
        elif status == "standalone" and boards <= LOW_BOARD_MAX:
            action, note = "caution", "昨日同题材仅本股涨停，低板独立强势只观察"
        elif status == "standalone":
            action, note = "caution", "昨日同题材仅本股涨停，高标保留但次日须看承接"
        else:
            action, note = "neutral", "题材联动数据不足，不作孤军结论"
        candidate["linkage_facts"] = facts
        # 盘后仅留展示/预案提示；竞价 discipline 由 signal_eval / pool_call 在 09:25 生成并冻结。
        candidate["linkage_eod_hint"] = {
            "schema": "linkage_eod_hint_v1",
            "mode": mode,
            "rollout_stage": stage,
            "action": action,
            "note": note,
        }
        # candidate_prefer 仅提供给卡片/AI预案作为展示优先级，严禁影响分数、分池与排序。
        if rollout_allows_candidate_prefer():
            candidate["linkage_prefer"] = action == "prefer"
    return candidates


def _facts_from(candidate: dict[str, Any]) -> dict[str, Any]:
    facts = candidate.get("linkage_facts")
    if isinstance(facts, dict) and facts.get("schema") == "linkage_facts_v1":
        return facts
    # 对旧候选按旧字段只生成 unknown，禁止历史文件被误判独苗。
    return build_eod_facts(candidate)


def apply_auction_gate(
    candidate: dict[str, Any],
    sector_support: dict[str, Any] | None,
) -> dict[str, Any]:
    """合成“昨日结构 × 今早承接”的竞价纪律。

    soft：仅拦 1~2 板的确认独苗。当天没有同题材成员报价时，理由是“昨日独苗”，
    而不是错误声称“今天板块 0 红开”。strict：相同条件下所有板数均须得到题材红开
    助攻；annotate/off 不改变买入裁决。
    """
    mode = linkage_mode()
    stage = linkage_rollout_stage()
    facts = _facts_from(candidate)
    boards = _to_int(candidate.get("boards")) or 0
    support = sector_support or {}
    quoted = _to_int(support.get("mates_quoted")) or 0
    red_open = _to_int(support.get("red_open")) or 0
    if quoted <= 0:
        auction_status = "unknown"
    elif red_open > 0:
        auction_status = "support"
    else:
        auction_status = "no_support"

    standalone = facts.get("eod_status") == "standalone"
    should_block = False
    # 缺竞价成员报价 = 数据不足，不可偷换成“板块没有承接”。严格模式只扩板数，
    # 不突破这条数据质量边界。
    if rollout_allows_auction_gate() and mode == "soft":
        should_block = standalone and boards <= LOW_BOARD_MAX and auction_status == "no_support"
    elif rollout_allows_auction_gate() and mode == "strict":
        should_block = standalone and auction_status == "no_support"

    if should_block:
        if auction_status == "no_support":
            note = f"昨日同题材仅本股涨停，今早同方向{quoted}家报价均未红开"
        else:
            note = "昨日同题材仅本股涨停，今早没有可核验的同方向竞价助攻"
        action = "block"
    elif facts.get("eod_status") == "linked" and auction_status == "support":
        action, note = "prefer", f"昨日有题材梯队，今早{red_open}家同方向红开"
    elif facts.get("eod_status") == "unknown":
        action, note = "neutral", "题材联动数据不足，不作独立股裁决"
    else:
        action, note = "neutral", "题材联动未形成额外买入限制"

    return {
        "schema": "linkage_discipline_v1",
        "mode": mode,
        "rollout_stage": stage,
        "action": action,
        "block": should_block,
        "eod_status": facts.get("eod_status"),
        "auction_status": auction_status,
        "facts": facts,
        "note": note,
    }
