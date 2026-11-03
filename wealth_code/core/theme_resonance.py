#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块趋势共振事实层（纯函数、无 IO）。

这层把催化、板块资金、梯队与带动结构、独立技术证据分开留痕。当前生产未接
独立技术证据，因此不输出整体 confirmed，也不把题材描述当成已核实催化。
只输出可审计的盘后事实与展示提示：不改分数、分池或候选顺序，不替代竞价裁决。
"""
from __future__ import annotations

from math import isfinite
from typing import Any

from core.theme_linkage import rollout_allows_candidate_prefer

_SCHEMA = "theme_resonance_facts_v1"
_EVIDENCE_VERSION = "theme_resonance_evidence_v2"
_INVALID = {"", "—", "-", "其他", "未知", "无"}


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _linkage_status(candidate: dict[str, Any]) -> str:
    facts = candidate.get("linkage_facts")
    if isinstance(facts, dict) and facts.get("schema") == "linkage_facts_v1":
        value = str(facts.get("eod_status") or "").strip()
        if value in {"linked", "standalone", "unknown"}:
            return value
    return "unknown"


def _catalyst_status(candidate: dict[str, Any]) -> str:
    """催化按证据质量分层；有题材描述不偷换成已核实硬催化。"""
    evidence = candidate.get("leader_evidence")
    dimensions = evidence.get("dimensions") if isinstance(evidence, dict) else None
    catalyst = dimensions.get("catalyst") if isinstance(dimensions, dict) else None
    verified = str(catalyst.get("status") or "").strip() if isinstance(catalyst, dict) else ""
    if verified in {"sustained_verified", "single_verified"}:
        return "verified"
    if verified == "unverified_news":
        return "unverified"
    logic = str(candidate.get("core_theme_logic") or "").strip()
    return "described" if logic and logic not in _INVALID else "unknown"


def _flow_status(candidate: dict[str, Any]) -> str:
    """板块资金不是个股主力资金；缺值必须保留 unknown。"""
    flow = _num(candidate.get("sector_flow_yi"))
    if flow is None:
        return "unknown"
    if flow > 0:
        return "inflow"
    if flow < 0:
        return "outflow"
    return "flat"


def _structure_status(candidate: dict[str, Any]) -> str:
    """梯队与带动仅描述结构，不代理价格或量价技术确认。"""
    ladder = _num(candidate.get("ladder_health"))
    carry = _num(candidate.get("carry_cnt"))
    if ladder is None and carry is None:
        return "unknown"
    if (ladder is not None and ladder >= 6.0) or (carry is not None and carry >= 1):
        return "confirmed"
    if (ladder is not None and ladder < 4.0) and (carry is None or carry <= 0):
        return "weak"
    return "developing"


def build_eod_facts(candidate: dict[str, Any], *, asof: str | None = None) -> dict[str, Any]:
    """生成盘后共振事实；观测时点由调用方提供，不读取系统时间。"""
    linkage = _linkage_status(candidate)
    catalyst = _catalyst_status(candidate)
    flow = _flow_status(candidate)
    structure = _structure_status(candidate)
    technical = "unknown"

    missing_dimensions = {
        "technical": "当前生产未接独立技术证据；ladder_health/carry_cnt 仅描述结构",
    }
    if linkage == "unknown":
        missing_dimensions["linkage"] = "缺少有效 linkage_facts.eod_status"
    if catalyst != "verified":
        missing_dimensions["catalyst"] = "缺少已核实催化；题材描述或未核实消息不作确认"
    if flow == "unknown":
        missing_dimensions["flow"] = "sector_flow_yi 缺失或不是有效有限数值"
    if structure == "unknown":
        missing_dimensions["structure"] = "ladder_health/carry_cnt 均缺失或不是有效有限数值"

    # 观测冲突与独苗保留其事实状态；其余缺任一维优先 unknown，不能降为 developing。
    if linkage == "linked" and flow == "outflow":
        status, note = "contradicted", "有同题材扩散，但本次观测板块资金净流出，不代表持续退潮"
    elif linkage == "standalone":
        status, note = "unconfirmed", "个股走强尚未形成同题材扩散"
    elif missing_dimensions:
        status, note = "unknown", "共振证据不完整，不把缺数据当成趋势"
    else:
        status, note = "unconfirmed", "暂未形成多维共振"

    sources = {"linkage": [], "catalyst": [], "flow": [], "structure": [], "technical": []}
    linkage_facts = candidate.get("linkage_facts")
    if isinstance(linkage_facts, dict):
        sources["linkage"] = [
            f"linkage_facts.{field}" for field in ("schema", "eod_status") if field in linkage_facts
        ]
    evidence = candidate.get("leader_evidence")
    dimensions = evidence.get("dimensions") if isinstance(evidence, dict) else None
    catalyst_evidence = dimensions.get("catalyst") if isinstance(dimensions, dict) else None
    if isinstance(catalyst_evidence, dict) and "status" in catalyst_evidence:
        sources["catalyst"].append("leader_evidence.dimensions.catalyst.status")
    if catalyst not in {"verified", "unverified"} and "core_theme_logic" in candidate:
        sources["catalyst"].append("core_theme_logic")
    if "sector_flow_yi" in candidate:
        sources["flow"].append("sector_flow_yi")
    sources["structure"] = [field for field in ("ladder_health", "carry_cnt") if field in candidate]

    return {
        "schema": _SCHEMA,
        "evidence_version": _EVIDENCE_VERSION,
        "asof": asof,
        "theme": str(candidate.get("theme_name") or candidate.get("core_theme") or "").strip() or None,
        "status": status,
        "linkage_status": linkage,
        "catalyst_status": catalyst,
        "flow_status": flow,
        "structure_status": structure,
        "technical_status": technical,
        "missing_dimensions": missing_dimensions,
        "sources": sources,
        "sector_flow_yi": _num(candidate.get("sector_flow_yi")),
        "ladder_health": _num(candidate.get("ladder_health")),
        "carry_cnt": _num(candidate.get("carry_cnt")),
        "note": note,
    }


def annotate_eod(candidates: list[dict[str, Any]], *, asof: str | None = None) -> list[dict[str, Any]]:
    """给候选加共振事实与展示级提示，严格不触碰选股排序/分池。"""
    for candidate in candidates:
        facts = build_eod_facts(candidate, asof=asof)
        candidate["theme_resonance_facts"] = facts
        candidate["theme_resonance_hint"] = {
            "schema": "theme_resonance_hint_v1",
            "status": facts["status"],
            "note": facts["note"],
        }
        # 与板块联动同一灰度边界：只作为 AI 预案/卡面提示，绝不改真实候选资格。
        if rollout_allows_candidate_prefer():
            candidate["theme_resonance_prefer"] = facts["status"] == "confirmed"
        else:
            candidate.pop("theme_resonance_prefer", None)
    return candidates
