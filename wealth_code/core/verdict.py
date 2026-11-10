#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价「是否符合预期」的**语义单一源**（2026-11-10 抽离）

## 为什么要有这一层

改一个词要动四处：判定（`ai_caliber.gap_zone`）、渲染（`build_cards._STATUS_PLAIN`）、
颜色判断（`"低于预期" in verdict`）、测试断言（30 处写死字面量）。
漏一处**不报错**：颜色会静默变红、或者「机械没达标该摘掉✅」失效。

根因不是分层错，是**同一个概念有多个事实来源**：中文文案被当成逻辑键用。
本模块把「文案 → 语义键」反过来：**逻辑只认 `Verdict`，中文只在 `label()/tag()` 里出现一次**。

## 用法

    from core.verdict import Verdict, label, tag, is_negative, from_text

    tag(Verdict.BELOW)            # '⚠️低于预期'
    is_negative('⚠️明显低于预期')   # True（兼容旧存档里的中文）
    from_text('🔺超预期')          # Verdict.SUPER

⛔ 新增/修改文案只改 `LABEL`；**禁止**在业务代码里再写 `"低于预期" in x` 这类子串判断
（有它就会漏，`scripts/check_verdict_semantics.py` 会扫）。
"""
from __future__ import annotations

from enum import Enum


class Verdict(str, Enum):
    """竞价相对预期的判定档（值 = 数据层 `signal_status` 的英文键）。"""

    SUPER = "super"            # 强于预期（绝对口径遗留；相对口径下少见）
    ABOVE = "above"            # 高于预期（相对口径：开了但买不进/空间不足的场景另说）
    OK = "ok"                  # 符合预期
    BELOW = "below"            # 低于预期
    DEEP_BELOW = "deep_below"  # 明显低于预期（DEV 深低开）
    SEALED = "sealed"          # 竞价封死买不进（物理事实，不是预期问题）
    UNKNOWN = "unknown"        # 没有预期数据 / 没有竞价数据
    PENDING = "pending"        # 待观察
    REJECT = "reject"          # 没达标（机械/资格）
    MISS = "miss"              # 没达标（同义键）


# ── 文案单一源（改这里，全系统跟着变）────────────────────────────────────
LABEL: dict[Verdict, str] = {
    Verdict.SUPER: "超预期",
    Verdict.ABOVE: "高于预期",
    Verdict.OK: "符合预期",
    Verdict.BELOW: "低于预期",
    Verdict.DEEP_BELOW: "明显低于预期",
    Verdict.SEALED: "竞价封死买不进",
    Verdict.UNKNOWN: "",
    Verdict.PENDING: "待观察",
    Verdict.REJECT: "没达标",
    Verdict.MISS: "没达标",
}

# 前缀图标（卡面用）
EMOJI: dict[Verdict, str] = {
    Verdict.SUPER: "🔺",
    Verdict.ABOVE: "🔺",
    Verdict.OK: "✅",
    Verdict.BELOW: "⚠️",
    Verdict.DEEP_BELOW: "⚠️",
    Verdict.SEALED: "⛔",
}

# 哪些档属于「负向」（决定颜色/是否摘除）
NEGATIVE: frozenset[Verdict] = frozenset({Verdict.BELOW, Verdict.DEEP_BELOW,
                                          Verdict.REJECT, Verdict.MISS})

# 旧存档里的中文（含图标、含后缀）→ 语义键；⛔ 只用于兼容读取，不用于新写入
_LEGACY: dict[str, Verdict] = {
    "超预期": Verdict.SUPER,
    "高于预期": Verdict.ABOVE,
    "符合预期": Verdict.OK,
    "低于预期": Verdict.BELOW,
    "明显低于预期": Verdict.DEEP_BELOW,
    "竞价封死买不进": Verdict.SEALED,
    "待观察": Verdict.PENDING,
    "没达标": Verdict.REJECT,
    "未达标": Verdict.REJECT,
}


def label(v: Verdict | str | None) -> str:
    """语义键 → 中文（不带图标）。未知值原样返回（⛔ 不吞数据）。"""
    vv = v if isinstance(v, Verdict) else _coerce(v)
    if vv is None:
        return str(v or "")
    return LABEL.get(vv, str(v or ""))


def tag(v: Verdict | str | None) -> str:
    """语义键 → 卡面标签（图标 + 中文）。空档返回空串。"""
    vv = v if isinstance(v, Verdict) else _coerce(v)
    if vv is None:
        return str(v or "")
    return f"{EMOJI.get(vv, '')}{LABEL.get(vv, '')}"


def is_negative(v: Verdict | str | None) -> bool:
    """是否负向（颜色用中性灰）。兼容旧中文文本（含 '·辨真假高开' 后缀）。"""
    vv = v if isinstance(v, Verdict) else _coerce(v)
    return vv in NEGATIVE if vv is not None else False


def is_ok(v: Verdict | str | None) -> bool:
    vv = v if isinstance(v, Verdict) else _coerce(v)
    return vv is Verdict.OK


def from_text(text: str | None) -> Verdict | None:
    """中文/带图标/带后缀的旧文本 → 语义键。取不到返回 None。"""
    s = str(text or "").strip()
    if not s:
        return None
    for key, v in _LEGACY.items():
        if key in s:
            # 「明显低于预期」优先于「低于预期」
            if key == "低于预期" and "明显低于预期" in s:
                continue
            return v
    return None


def from_status(raw: str | None) -> Verdict | None:
    """数据层英文键 → 语义键（`signal_status`）。"""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return Verdict(s)
    except ValueError:
        return from_text(s)


def _coerce(value: str | None) -> Verdict | None:
    """内部：英文键优先，其次中文。"""
    if value is None:
        return None
    if isinstance(value, Verdict):
        return value
    return from_status(str(value))


# ── 数据层信号键（`signal_status`）→ 语义键 ─────────────────────────────
STATUS_MAP: dict[str, Verdict] = {
    "super": Verdict.SUPER,
    "expected": Verdict.OK,
    "weak": Verdict.BELOW,
    "pending": Verdict.PENDING,
    "reject": Verdict.REJECT,
    "miss": Verdict.MISS,
}


def status_plain(raw: str | None) -> str:
    """`signal_status` 英文档位 → 中文白话（未收录原样返回，⛔ 不吞数据）。"""
    s = str(raw or "").strip()
    v = STATUS_MAP.get(s)
    return LABEL[v] if v is not None else s
