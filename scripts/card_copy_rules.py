#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
卡片文案白话词表 —— **唯一权威源**（2026-09-18 建）
====================================================
背景：此前禁项词表散落 **5 处且互不一致**——`tests/test_verify_card_ai.py` 的
`BAD` / `_EOD_BAD` / `_TAG_BAD` / `_PLAN_BAD` 四套 + `scripts/card_quality_check.py`
的 `JARGON` 一套。实证差异：`承接` 在 BAD/TAG/PLAN 被禁、EOD 未禁；`梯队` 只在 TAG
被禁（而 eod 卡早因「梯队」白话化过）；`亢奋/退潮/冰点` 只在 EOD/PLAN 被禁。
→ 同一门禁多份词表 = 必然漂移；且**LLM 拼的复盘卡长期无任何词表约束**（09-17 实测
  复盘卡残留 研判/容量核心/梯队/承接/亢奋 共 18 处）。

职责：本模块是**判据单一源**。渲染层（card_lint 推送门禁 / card_quality_check 每日
自检 / 测试）一律 import 本模块，⛔ 不得再各自维护词表。

分级（判据 = 词的性质，不是出现位置）：
  · PLAIN_FAIL —— **机器黑话 / 内部字段 / 英文档位 / 自造指标**：上卡即违规、无语境
    可言 → 推送前**阻断**（card_lint FAIL）。
  · PLAIN_WARN —— **行业圈内术语 / 情绪档位原词**：在叙述里可能勉强可读 → 推送前
    **提示**（WARN），不阻断（⛔ 硬阻断会因 LLM 拼卡偶发用词把整张复盘卡卡死、
    推送丢失，代价远大于措辞瑕疵）。
  · PLAIN_KEEP —— 显式**保留**（A股通用交易词·用户日常词汇）：判据 = 「A股通用交易
    词保留，系统内部造词/字段名/英文档位一律白话化」（宪法【🗣️ 卡片话术铁律】）。

权威规则：`config/shared_rules.md【🗣️ 卡片话术铁律 · 全局白话】`。
"""
from __future__ import annotations
import re

# ── Tier-1 阻断：机器黑话 / 内部字段 / 英文档位 / 自造指标 ────────────────
# 判据：这些词「上卡即违规」——它们是系统内部实现细节，任何语境都不该出现在人看的卡上。
PLAIN_FAIL = [
    # 英文档位 / 字段名
    "expected", "regime=", "门控=", "defense=", "buy/", "pending", "reject",
    "feedback_matrix", "反馈矩阵", "MA5", "spec §", "health ",
    # 自造指标 / 内部口径
    "pp）", "[放量", "可信度", "已结算", "样本", "严格主升", "冰点档", "冰点禁买",
    "分歧/死亡", "情绪升温", "情绪退潮", "情绪中性",
    # 圈内自造术语（照搬数据键/分组键）
    "题材板块效应",
]

# ── Tier-2 提示：行业/圈内术语 · 情绪档位原词 ────────────────────────────
# 判据：属「过度行业术语或圈内造词」，卡面白话化更好，但特定叙述语境下勉强可读。
PLAIN_WARN = [
    "研判", "维度", "标的", "卡位", "兑现", "埋伏", "打板", "身位", "杂毛",
    "孤军", "弱转强", "容量核心", "承接", "梯队", "溢价率", "命门",
]

# ── 显式保留：A股通用交易词 + 用户体系标准词（非系统内部造词）────────────
# 判据：宪法【🗣️ 卡片话术铁律】——「A股通用交易词保留，系统内部造词/字段名/英文档位一律白话化」。
# ⚠️ 情绪周期词（冰点/修复/亢奋/退潮/分歧）属**用户体系标准语汇**（`a-stock-sentiment`
#    四阶段 + `龙头战法-交易体系` 五阶段 + 用户日常高频「退潮日」）→ 全卡叙述中合法、保留。
#    ⛔ 但**档位标签原值直传卡面**仍是违规（那是内部字段泄漏）——该场景由 `_gate_plain`
#    显示层映射 + `tests/test_verify_card_ai.py::test_gate_block_no_jargon` 单独锁死，
#    与本全卡词表**分层各司其职**（全卡扫描不重复管标签位）。
PLAIN_KEEP = [
    "涨停", "跌停", "连板", "首板", "断板", "炸板", "接力池", "观察池", "竞价",
    "高开", "低开", "放量", "缩量", "晋级率", "核按钮", "换手", "封单",
    "五日线", "龙头", "主线", "题材", "板块", "分歧", "回流", "套牢",
    # 情绪周期体系标准词（用户体系语汇·非内部档位泄漏）
    "冰点", "修复", "亢奋", "退潮",
]

# 白话替换建议（宪法映射表镜像·供违规报错提示 AI 怎么改）
PLAIN_HINT = {
    "研判": "判断", "维度": "方面", "标的": "票", "卡位": "占住位置",
    "兑现": "涨到位/落袋", "埋伏": "提前买", "打板": "追涨停", "身位": "位置",
    "容量核心": "板块大票", "承接": "有资金接", "梯队": "同方向的票",
    "孤军": "同方向只它1家", "弱转强": "重新走强", "溢价率": "第二天还能涨多少",
    "亢奋": "市场很热", "退潮": "市场转冷", "冰点": "市场很冷",
    "题材板块效应": "同板块一起涨", "严格主升": "只做强票", "冰点档": "市场很冷",
    "反馈矩阵": "情绪反馈数据", "可信度": "预期可信度", "样本": "已记录",
    "已结算": "已记录", "情绪升温": "市场转暖", "情绪退潮": "市场转冷",
    "情绪中性": "市场平稳",
}

# ── 模块名豁免：系统级固定模块名（全卡统一 UI 约定·⛔ 不属黑话）──────────
# 判据：这些是全系统 20+ 处引用、且用户长期看惯的模块名（见宪法【🧠 AI综合研判渲染模板】等）。
# 扫描前先剔除这些标签串 —— 故正文里的裸 `研判` 仍会被抓到，只有模块名被豁免。
# ⛔ 若要改模块名，须用户单独拍板并全系统同步；不得由词表自动"修"。
PLAIN_KEEP_LABELS = [
    "AI综合研判", "AI 综合研判", "🧠 AI综合研判", "🧠AI综合研判", "🧠 AI 综合研判",
    "情绪核心", "连板天梯", "板块资金流向", "突发题材榜", "龙头定性", "延续观察",
    "出手研判", "信号摘要", "大盘概览", "盘中催化速览", "昨日涨停反馈",
    # 复盘骨架模块名（含"演化史/轮动复盘"等书面但用户已验收的固定名）
    "核心演化史", "轮动复盘", "主线复盘", "真实情绪总览", "情绪演变",
]


def strip_labels(text: str) -> str:
    """剔除系统固定模块名（豁免标签），防模块名里的词被判黑话。"""
    for lb in PLAIN_KEEP_LABELS:
        text = text.replace(lb, " ")
    return text


# 英文词需词边界（防 `pending` 命中 `pendingX`、`expected` 命中 `unexpected`）
_WORD_BOUNDED = re.compile(r"^[A-Za-z_]+$")


def _hit(term: str, text: str) -> bool:
    if _WORD_BOUNDED.match(term):
        return re.search(rf"(?<![A-Za-z_]){re.escape(term)}(?![A-Za-z_])", text) is not None
    return term in text


def scan(text: str) -> tuple[list[str], list[str]]:
    """扫描文本 → (fail_hits, warn_hits)，各去重保序。模块名先豁免。"""
    text = strip_labels(text)
    fails = [t for t in PLAIN_FAIL if _hit(t, text)]
    warns = [t for t in PLAIN_WARN if _hit(t, text)]
    return fails, warns


def hint(term: str) -> str:
    """违规词 → 白话建议（无建议则空串）。"""
    return PLAIN_HINT.get(term, "")


def describe(term: str) -> str:
    h = hint(term)
    return f"{term}（建议改「{h}」）" if h else term


# ── 自检：词表三组互斥（KEEP 不得同时出现在 FAIL/WARN，防自相矛盾）────────
def _self_check() -> list[str]:
    errs = []
    for k in PLAIN_KEEP:
        if k in PLAIN_FAIL:
            errs.append(f"保留词 `{k}` 同时被列为阻断禁项")
        if k in PLAIN_WARN:
            errs.append(f"保留词 `{k}` 同时被列为提示禁项")
    dup = set(PLAIN_FAIL) & set(PLAIN_WARN)
    if dup:
        errs.append(f"词同时属 FAIL 与 WARN: {sorted(dup)}")
    return errs


SELF_CHECK_ERRORS = _self_check()

if __name__ == "__main__":
    import sys
    if SELF_CHECK_ERRORS:
        for e in SELF_CHECK_ERRORS:
            print(f"❌ {e}")
        sys.exit(1)
    print(f"✅ 词表自检通过 | 阻断 {len(PLAIN_FAIL)} · 提示 {len(PLAIN_WARN)} · 保留 {len(PLAIN_KEEP)}")
    sys.exit(0)
