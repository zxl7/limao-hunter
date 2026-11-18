#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财富密码卡片构建 + 推送（飞书单入口 push_card.py）。

四张卡（财富密码模块）：
- build_eod_card():  📊 财富密码·盘后候选 —— 收盘后由 pipeline_eod 产出候选时推送，
                      含连板天梯 + 接力池（2026-11-17 起为唯一候选池，无其他池）。
- build_verify_card(): ⚡ 财富密码·竞价验证 —— 次日 09:25 由 pipeline_verify 竞价验证后
                      推送，含达标买入信号(super/expected) + 竞价涨幅/额 + 净值/策略指标
                      （次日执行）。
- build_panqian_card(): 📊 盘前预测·全量 —— 06:06 由技能组装 data 后推送（隔夜消息+今日主线）。
- build_ambush_card(): 🎯 尾盘潜伏·阴线回踩十日线 —— 14:30 定时。选材＝「前段爆发过的板块中，
                      今日收阴 + 十日线连续上行 + 收盘贴着十日线 + 缩量」的个股
                      （由 core/ambush_pullback.py 在 14:30 冻结，卡片只读快照）。
                      新主线/题材回流/断板回流三块降为背景参考，不参与次日推票。

配色遵循 shared_rules：飞书 lark_md 只用命名色(red=涨/核心, purple=机会, turquoise=防御)，
禁用 hex；语义 emoji 替代彩色圆点；页脚两行（数据源 + 免责）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 架构重构 P1：通用组件抽出（pipelines 包）。直接在脚本目录外运行时必须把 wealth_code
# 加入 sys.path，否则 `from pipelines.card_style import` 会找不到包（提前到导入之前）。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
import os
import re as _re
import subprocess
import time
from datetime import datetime, timedelta

from pipelines.card_style import (  # noqa: E402
    _md, _div, _hr, _note, _now_bj, _footer, _regime_label, _fbt_le,
    _hl, _stock_hl, _amount_txt, _flow_columns, BJ_TZ,
)
from typing import Any

# ── 用户 5% 硬线单一源（2026-09-14 用户「AI 决定要买的，低于 5% 直接买」）──────
# 卡面「买入 / 没买到」的判定必须与账户账本（core/ai_caliber.decide）同值，
# 否则会出现「卡面说没买到、账本记买到」或反之的三方打架（09-14 闽东电力实证）。
from core import ai_caliber as _ai_caliber  # noqa: E402
# 锚点态度文案单一源（2026-11-10 晚）：状态/动作都从 `core.anchor_call` 取，⛔ 不再本地另写。
from core import anchor_call as _anchor_call  # noqa: E402
# 竞价预期语义单一源（2026-11-10 抽离）：中文文案只在 core/verdict 出现一次。
from core import verdict as _vs  # noqa: E402
# 情绪周期四阶段（2026-10-21 P1·`regime` 判「今天几度」，本模块判「走到哪了」）。
from core import cycle_phase as _cycle_phase  # noqa: E402
# 龙头身份认定（2026-10-21 P2）：卡面只借 `tag_text` 拿一个极短标签，不重算判定。
from core import dragon_confirm as _dc  # noqa: E402
from core import theme_linkage as _theme_linkage  # noqa: E402
from core import zt_gene as _zt_gene  # noqa: E402  股性判据单一真源（2026-10-22 抽出共用）
# 晋级三问（2026-10-30 用户「优化推送高胜率可晋级板块地位高的个股」）：
# 只做判定 + 一行块级汇总，⛔ 不改评分/顺序/名额/仓位。
from core import promotion_quiz as _pquiz  # noqa: E402
USER_ENTRY_MAX_GAP_PCT = _ai_caliber.USER_ENTRY_MAX_GAP_PCT
CACHE_DIR = ROOT / "cache"
LIMAO_SCRIPTS = ROOT.parent / "scripts"   # 狸猫猎手/scripts/push_card.py 单入口

# ── 竞价卡「简洁模式」开关（2026-10-21 用户「早盘推送内容过于冗余·简洁一点但核心数据不要少」）──
# 病灶（实测 10-21 卡 13,328 字 / 83 个 block）：同一只票的同一件事实被讲 2~3 遍 ——
#   ① `📈 历史基因` 行 与 点评里的 `历史股性` 段重复；
#   ② `🤖 今日9:25判` 里的「昨日逻辑」与点评里的 `题材催化` 段近乎逐字重复（同一篇 AI 写作两处引用）；
#   ③ 观察区 / 没达标区的票**不买**，却照样展开完整四段点评（走势+题材+股性+风险）。
# 处置（⛔ 只删重复表述，不删数据）：买入/机械达标票保留全量但去重；不买的票压成
#   「指标 + 历史基因 + 判定」一行，只保留风险一句。
# 回退：`VERIFY_CARD_COMPACT=0` → 恢复旧的全量展开渲染（一行环境变量即可，无需改代码）。
_COMPACT_ENV = "VERIFY_CARD_COMPACT"


def _compact_mode() -> str:
    """竞价卡详略档位：off（旧全量）/ normal（默认·简洁）/ max（更狠·不买的票只留风险）。

    `VERIFY_CARD_COMPACT`：0/false/off → off；2/max → max；其余 → normal。
    """
    v = str(os.environ.get(_COMPACT_ENV, "1")).strip().lower()
    if v in ("0", "false", "no", "off"):
        return "off"
    if v in ("2", "max", "strong"):
        return "max"
    return "normal"


def _compact_on() -> bool:
    """竞价卡简洁模式是否开启（默认开；`VERIFY_CARD_COMPACT=0` 回退旧全量渲染）。"""
    return _compact_mode() != "off"


def _ai_view_dir() -> Path:
    """AI 判断视图目录（2026-09-11）。

    AI 决策落盘于「狸猫猎手/cache」（与 zt_genes/yesterday_zt 等真实专有数据同处），
    **不是** wealth_code/cache。写入方 scripts/ai_ledger.AI_VIEW_DIR 必须与此一致——
    接缝由 tests/test_ai_ledger.py::test_ai_view_dir_seam 锁死
    （09-10 曾因两侧不一致导致「写了也读不到」静默失效）。
    跟随 ROOT 计算（非导入期快照），以便测试 monkeypatch ROOT 隔离。
    """
    return ROOT.parent / "cache"


# 🚨 净值回撤线（退学炒股心法·语录62「回撤线机制」2026-08-24 吸收）：
# 资金回撤线之下=降档防守。净值从历史高点回撤 ≥ WARN 触发硬警示（红块）、≥ HINT 软提示。
NAV_DRAWDOWN_WARN_PCT = 10.0
NAV_DRAWDOWN_HINT_PCT = 5.0
# 监管异动线渲染档位已抽离 → wealth_code/config/regulatory_rules.json（render 节·改规则不动代码）

# ── 买入口径开关（2026-09-14 用户「我想统一为AI口径」→「全量以 AI 为唯一口径！」）──
# 系统分两层，均以 AI 决策为准：
#   ①账本层 `core/ai_caliber.py`（开关 AI_CALIBER_ACCOUNT）：净值 / 持仓 / 战绩统计
#     = AI 实际买到的票（AI 判 participate ∧ 竞价 ≤ AI 自定 entry_max_gap）。
#   ②显示层（本开关）：卡头 / 标题定调按 AI 口径，机械达标降为灰字参考。
# True  = 卡头「AI 买入信号」= AI 口径**实际能成交**的只数（= 账本口径 = summary.buy_signals），
#         并括注「重点看 N 只」（AI 昨日预案）与「机械量价参考 M 只」；📊 段按 mech_can_enter 筛选
#         （段名明确为「机械量价参考」，与 AI 买入和账本口径严格区分）。
# False = 一键回退旧显示行为（买入信号 = 机械 can_enter 只数）。
# ⛔ 显示层只读不写：不改 can_enter（由 core/ai_caliber 决定）、不改 signal_eval。
AI_CALIBER_BUY_SIGNAL = True


# ── 🪶 竞价卡精简（2026-10-21 用户「如果我不想显示以下数据」+ 逐项确认）──────────
# 用户列的 5 项诉求 → 本轮处理方式（⛔ 每一项都独立可逆）：
#   ① 净值回撤线    → **保留但压缩**：去掉括号里的心法解释，只留「回撤 X% · 降档防守」
#                      （只占 121 字/1.2%，砍掉省不了多少，却是唯一每天提醒账户还在亏的东西）
#   ② 机械量价参考  → **只留差集**：只列「机械达标但 AI 没买」的票
#                      实测 6 只里 4 只已在「今日买入」→ 同一只票两套完整展示＝真冗余来源
#   ③ 观察          → **只留判定行**：去掉点评/基因/理由三块，只留标题行 + 指标行
#                      且剔除已在「今日买入」「机械差集」出现的票（再消一层重复）
#   ④ 其余未达标    → **整段不渲染**（当天没有机会的票，列出来只添噪）
#   ⑤ 全局          → **压缩成只剩「操作」段**：账户/盘面两段分别已有 📈战绩行 与 📡/🌊 两块
#   ⑥ 池外/未采     → 用户没提但顺手砍（803 字/8.2%，对决策零价值）
# ⛔ 不变量（⛔ 删数据不算精简）：竞价涨跌幅、额/需要额、预期区间、池别、题材行业、
#    板数、历史基因、9:25 判定与理由、风险一句 —— 一个都不许少。
# ⛔ 主开关 `VERIFY_LEAN_MODE=False` → 逐字回退到 2026-10-21 之前的全量渲染。
VERIFY_LEAN_MODE = True
_LEAN_ENV = "VERIFY_CARD_LEAN"


def _lean_on() -> bool:
    """精简模式是否开启（默认开；`VERIFY_CARD_LEAN=0` 一键回退全量）。"""
    v = str(os.environ.get(_LEAN_ENV, "1")).strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return bool(VERIFY_LEAN_MODE)


def _nav_drawdown_dense() -> bool:
    """① 回撤块是否压缩（精简模式下压；⛔ 只删解释、不删警示本身）。"""
    return _lean_on()


def _mech_diff_only() -> bool:
    """② 机械量价段是否只留差集（机械达标 ∧ AI 未买）。"""
    return _lean_on()


def _watch_brief() -> bool:
    """③ 观察区是否只留判定行。"""
    return _lean_on()


def _show_reject_rows() -> bool:
    """④ 其余没达标的票是否渲染。

    ⛔ **2026-10-21 改为恒 True**：原来精简模式整段不渲染，结果科森科技、长春一东
    这类票从卡上彻底消失，而上方还写着「逐票见下方分段」——自相矛盾；这也正是
    用户当场质问「AI 的放弃判断被你抹掉了？？」的实际案例（AI 对它们判了 观察/放弃）。
    精简的是**版面**（每票两行：指标行 + AI 判定行），⛔ 不是「这只票存不存在」。
    """
    return True


def _show_pool_out() -> bool:
    """⑥ 池外/未采是否渲染（精简模式：不渲染）。"""
    return not _lean_on()


def _narrative_brief() -> bool:
    """⑤ 全局旁白是否压缩成只剩「操作」段。"""
    return _lean_on()


def _lean_level() -> int:
    """🪶 精简档位：0=全量 / 1=第一轮（去重+段级取舍）/ 2=极限（每票两行·2026-10-21 第二轮）。

    `VERIFY_CARD_LEAN`：0/off → 0；1 → 1；其余（默认）→ 2。
    用户 2026-10-21「继续压！再推一次」→ 默认档位提到 2。
    """
    v = str(os.environ.get(_LEAN_ENV, "2")).strip().lower()
    if v in ("0", "false", "no", "off"):
        return 0
    if v == "1":
        return 1
    return 2 if VERIFY_LEAN_MODE else 0


def _two_line_rows() -> bool:
    """档位 2：机械段/观察段每只票只留「标题行 + 指标行」。

    ⛔ 不丢的东西：竞价 gap、额/需要额、预期区间、池别、题材、板数、
      9:25 判定（在标题行的 sig 里）、判定理由（并入指标行末）。
    ⛔ 丢的东西：历史基因行、AI 判断行单独成行、点评（走势/题材/股性/风险）。
      「为什么没买」＝判定理由，它仍在——只是不再独占两行。
    """
    return _lean_level() >= 2


def _compress_position_block() -> bool:
    """档位 2：动态仓位块压成一行（档位基数那行只在真压过仓时才留）。"""
    return _lean_level() >= 2


def _drop_ai_stats() -> bool:
    """档位 2：砍「AI 自己的成绩」块（卡头已有 📈 战绩行 · 同一件事）. """
    return _lean_level() >= 2


_UNIFY_MECH_ENV = "VERIFY_CARD_UNIFY_MECH"


def _unify_mech() -> bool:
    """机械口径是否**并入** AI 判定（不再单列「📊 机械量价参考」一段）。

    2026-10-21 用户「机械量价参考 想办法 和AI判断融合在一起」。
    ⛔ 背景：原先同一只票在 🤖 今日买入段出现一次、在 📊 机械段再完整出现一次
      （标题行 + 指标行 + 判定行三块）→ 实测 6 只里 4 只重复，是全卡最大冗杂来源。
    **融合后：一只票只出现一处**，机械口径压成标题行一个胶囊（`📊 机械达标`），
    AI 判定仍是独立的第三行 —— 两套口径并排，读者能直接对拍。
    ⛔ 不丢：机械全量达标只数在段标题交代；机械结论（符合预期/超预期）在 gap 胶囊。
    开关 `VERIFY_CARD_UNIFY_MECH=0` → 逐字节回到「两段分列」的旧行为。
    """
    return str(os.environ.get(_UNIFY_MECH_ENV, "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _mech_pass(r: dict | None) -> bool:
    """该票机械口径是否达标（旧 record 无 `mech_can_enter` → 回退 `can_enter`）。"""
    oc = _oc_of(r)
    return bool(oc["mech_can_enter"]) if "mech_can_enter" in oc else bool(oc.get("can_enter"))


# ── 🎨 卡面颜色分层（2026-10-21 用户「观察这边颜色不要搞得这么花哨，重点突出要买的」）──
# 判据：**颜色预算只给要买的票**。其余一律降权，让读者的眼睛自己找到该看的那一行。
#   买入   → `red` 底白字（全卡最重的一处，也是唯一用红色标的的地方）
#   观察   → `wathet`（淡蓝·"先看着"的语义，比灰多一点存在感但不抢眼）
#   放弃   → `neutral`（灰·已经否决了，最低权重）
#   跟风/量价等提示胶囊 → 一律 `neutral` 灰字，⛔ 不再用 orange/yellow/purple 抢色
# ⛔ 飞书枚举无 `grey`：灰字标签用 `neutral`；正文灰字用 `<font color='grey'>`。
# ⛔ 2026-10-21 二次调色（用户「还是不是很好看，无法命中的颜色选一个合适的」）：
#    原方案 `wathet`（淡蓝）做观察底色 —— 飞书 wathet 是 #e1eaff 极浅蓝，
#    在深色模式几乎看不见、在浅色模式跟红底对比又太跳，整卡显得"浮"。
#    ✅ 改为**全灰阶 + 单点红**：颜色预算只给买入票，其余靠浓淡区分，不靠色相。
#      买入 → red 底（全卡唯一彩色块）
#      观察 → neutral 灰底（有块、但灰度低）
#      放弃 → **无底色**，只用灰字加粗（连块都不给）
#      提示胶囊 → neutral 灰底
#    ⛔ 这样一张卡最多出现 3 种"颜色形态"：红块 / 灰块 / 灰字。
C_BUY = "red"
C_WATCH = "neutral"
C_SKIP = ""          # 空 = 不上 text_tag，渲染层走灰字路径
C_HINT = "neutral"


def _tier_color(bucket_label: str, *, bought: bool = False) -> str:
    """按「今天买不买」给票名配底色。⛔ 不看池别（接力/观察）——那是内部结构，
    读者只关心"这只要不要动手"。"""
    return C_BUY if bought else C_WATCH


def _sp(txt: str) -> str:
    """给非空胶囊补一个前导空格（空串原样返回，不留多余空格）。"""
    t = str(txt or "")
    return f" {t}" if t else ""


def _v_tag_for(r: dict, verdict: str) -> str:
    """🧩 机械没达标时摘掉 `✅符合预期`，保留 `🔺超预期` / `⚠️低于预期`。

    ⛔ 只摘「符合预期」的理由：它与「机械没达标」直接对撞（一个说行、一个说不行）。
      `🔺超预期` / `⚠️低于预期` 描述的是 gap 偏离预期区间**这个事实**，
      与「量能够不够买」不冲突，删了反而丢信息（长春一东 -3.30% 低于预期要留）。
    """
    v = str(verdict or "")
    if _unify_mech() and not _mech_pass(r) and _vs.is_ok(v):
        return ""
    return v


def _mech_badge(r: dict | None) -> str:
    """量价判定胶囊：`量价够格` / `量价不足`（挂在票标题行）。

    ⛔ 2026-10-21 用户「这是商业性项目，不要显示解释性质的语录」：
      原名 `📊 机械达标/机械没达标` —— 「机械」是系统内部对「竞价量价判定」的称呼，
      对外读者看不懂，且 📊 图标本身在提示「这是另一套系统」。改为用户视角的词：
        · `量价够格`（黄）= 竞价成交额与涨幅都够，量价上可以动手
        · `量价不足`（灰）= 涨得达标但量不够 / 板块没跟上 → 量价上不行
    ⛔ 只在**融合模式**且有对照价值时打：AI 判「买入」的票不用打（它当然够格），
      以免每只票都挂一个胶囊＝等于没挂。
    """
    oc = _oc_of(r)
    if not _unify_mech():
        return ""
    if str(oc.get("ai_view") or "") == "participate":
        return ""
    if "mech_can_enter" not in oc and "can_enter" not in oc:
        return ""                      # 无机械结论 → 不猜（诚实留白）
    # ⛔ 前导空格是设计的一部分：挂在 `+0.19%` 后面不加空格会粘成 `+0.19%量价够格`
    # 🎨 2026-10-21 降权：量价判定是**辅助信息**，⛔ 不给彩色（原 yellow 会跟买入红抢眼）
    if _mech_pass(r):
        return f" <text_tag color='{C_HINT}'>量价够格</text_tag>"
    return " <text_tag color='neutral'>量价不足</text_tag>"


# ⛔ 2026-10-21 用户「防守篮下可以改为跟风」：
#    「防守拦下」是内部闸门名（防的是什么？读者不知道）；这两种拦下**本质是同一件事**——
#    它今天只是跟着涨、不是这条线的主角。统一叫「跟风」，一个词说清。
_GATE_BADGE = {
    "follower": ("跟风", C_HINT),
    "defense": ("跟风", C_HINT),
}


def _outpool_badge(r: dict | None) -> str:
    """🔍 池外龙头校验胶囊：**只在判定为「后排」时打**。

    2026-10-21 用户「可以加一层池外龙头校验」。
    ⛔ 为什么不标前排/次排：每只票都挂一个名次＝等于没挂（与龙头标签同一条理）。
      只有「这条线最强的在池外、池内这只是在硬凑」才是读者需要知道的危险信号。
    ⛔ 与 `🐣 跟风拦下` 的分工：那个是 **9:25 裁决**说「有前排可买就不买小弟」，
      这个是 **盘后建池**说「这条线根本没龙头进池」——两种不同的病，措辞必须区分开。
    """
    tier = str((r or {}).get("line_tier_outpool") or "")
    if tier != "back":
        return ""
    # ⛔ 只讲事实（这条线最强几板、在池外是谁），⛔ 不写「所以它是后排」——
    #    标签本身已经在说这件事，再解释一遍就是机制陈述（用户 2026-10-21 明确否掉）。
    tops = [str(x).strip() for x in ((r or {}).get("line_tops") or []) if str(x).strip()]
    top = (r or {}).get("line_top")
    if not tops or not isinstance(top, int):
        return " <text_tag color='red'>⚠️ 这条线最强的票不在池里</text_tag>"
    return (f" <text_tag color='red'>⚠️ 这条线最强 {top} 板在池外</text_tag>"
            f"<font color='grey'>（{'、'.join(tops[:3])}）</font>")


def _gate_badge(oc: dict) -> str:
    """竞价裁决提示胶囊（融合后挂在票标题行）。"""
    if not _unify_mech():
        return ""
    # 昨日独苗 × 今早有效同题材报价均未红开：量价够格也只观察。
    # 不在数据缺失时显示——unknown 不能被说成“同板块没人跟”。
    if (oc or {}).get("linkage_gate") and _theme_linkage.rollout_allows_card_explain():
        return f" <text_tag color='{C_HINT}'>同题材没跟上</text_tag>"
    if (oc or {}).get("follower_gate"):
        txt, color = _GATE_BADGE["follower"]
        return f" <text_tag color='{color}'>{txt}</text_tag>"
    if (oc or {}).get("defense_gate"):
        txt, color = _GATE_BADGE["defense"]
        why = _DEFENSE_GATE_PLAIN.get(str(oc.get("defense_gate") or ""), "")
        return (f" <text_tag color='{color}'>{txt}</text_tag>"
                + (f"<font color='grey'>（{why}）</font>" if why else ""))
    return ""


def _short_others_list() -> bool:
    """档位 2：「其余 N 只今日没有买入安排」只报只数，不列名字（名字在下方各段都有）. """
    return _lean_level() >= 2


# ── 🚫 竞价卡「整块不要」清单（2026-10-21 用户连发两张截图逐块点名）────────────
# 用户截图圈掉的四块：①净值回撤线 ②行情位置 ③📈 已记录战绩行 ④🗣️ 全局旁白。
# ⛔ 这不是「信息删除」，是「位置迁移」——四块的事实都还在别处：
#   · 回撤 → 🎯 动态仓位块「账户回撤 -X% → 实际压到…」（这个块用户明确要留）
#   · 行情位置 → 已落进选股与 9:25 裁决，卡面不再复述方法论
#   · 战绩 → `cache/metrics.json` + 复盘卡；卡头「已记录N天 | 最新净值」仍在
#   · 全局 → 盘后卡仍完整保留（盘后是复盘场景，早盘不需要读作文）
# 恢复方式：`VERIFY_CARD_HIDE=dd,phase`（只隐藏前两块）；`VERIFY_CARD_HIDE=`（清空=全恢复）。
_HIDE_BLOCK_ENV = "VERIFY_CARD_HIDE"
# `head` = 卡头汇总行「⚡ 财富密码·竞价验证 | 候选N只 AI 买入信号X（机械量价参考M只）
#          已记录K天 | 最新净值V」（2026-10-21 16:39 用户点名「删除文案」）。
#    ⛔ 删它不丢信息：卡片标题仍有「⚡ 财富密码·竞价验证·增量 HH:MM | AI 只买1只·精选」；
#      「候选N只 / 买1只以内」在 🤖 今日买入段首句；「机械M只达标」在 📊 机械段标题；
#      净值/战绩在 metrics.json 与复盘卡。
_HIDE_BLOCK_DEFAULT = "head,dd,phase,pnl,global"
_HIDE_ALLOWED = ("head", "dd", "phase", "pnl", "global", "track")


def _hide_block(tag: str) -> bool:
    """该整块是否隐藏。`tag` ∈ dd（回撤）/ phase（行情位置）/ pnl（战绩行）/ global（全局）. """
    raw = os.environ.get(_HIDE_BLOCK_ENV)
    src = _HIDE_BLOCK_DEFAULT if raw is None else str(raw)
    items = {x.strip().lower() for x in src.split(",") if x.strip()}
    return tag in items


# ── 飞书卡片基础构件 ────────────────────────────────────

def _candidate_tags(it: dict, *, suppress_band: bool = False) -> list[tuple[str, str]]:
    """生成候选胶囊 tag 云（飞书 text_tag 彩色胶囊），贴近截图：
    封板质量 / 首封时间 / 封单额 / 换手 / 成交额 / 流通市值 / 低价 / 20cm / 领涨 / 容量 /
    梯队 / 连板进阶 / 断板风险 / 综合档位。返回 [(文本, 飞书命名色)]，_render_tag_cloud 拼装。
    飞书 text_tag 仅支持命名色 neutral/blue/red/orange/violet/indigo/lime/wathet/green/yellow/
    purple/carmine/turquoise，禁用 hex（守 shared_rules）。

    配色（用户2026-08-13定·去花）：绝大多数 tag 为中性事实数据，统一 neutral（灰）最干净；
    仅保留「断板风险高」红色——它是唯一有价差的预警信号（与行3 🧭 风险代 呼应），
    盘中一眼可见。若后续要彻底素净，把 risk 的 "red" 也改 "neutral" 即可。"""
    tags: list[tuple[str, str]] = []
    # 🐉 龙头身份（2026-10-21 P2 补齐盘后卡）：**放在最前**——身份比封板质量/换手这些
    #    盘口细节更硬，排第一位才合理。
    #    ⛔ 只标前两档（真龙头红 / 龙头·未验证橙）；准龙头与跟风不标，
    #      否则每只票都挂身份＝等于没身份（与验证卡 `_dragon_tag` 同一口径）。
    _dc_tag = _dragon_tag(it)
    if _dc_tag:
        _lv = (it.get("dragon_confirm") or {}).get("level")
        # 🎨 2026-10-21：真龙头红（身份够硬），未验证龙头降为中性黄（原 orange 太抢）
        tags.insert(0, ("🐉 龙头" if _lv == "confirmed" else "🐉 龙头·未验证",
                        "red" if _lv == "confirmed" else "yellow"))
    # 🎯 晋级三问全过（2026-10-30 用户「优化推送高胜率可晋级板块地位高的个股」）：
    #   三问 = 高胜率（个股晋级率≥40% 且 样本够）＋ 可晋级（1~2 板且历史走过更高）＋
    #   板块地位（前排身位且题材有梯队）。放最前：它是「明天凭什么晋级」的直接回答。
    #   ⛔ 只有 3/3 才挂——「两高」也挂上去，就等于把「差一条」和「都占」混为一谈。
    _pq = it.get("promotion_quiz") or {}
    if (_pq.get("passed") or 0) == 3:
        tags.insert(0, ("🎯 三高", "red"))
    # A股监管异动线（2026-08-27 用户·偏离值口径·pipeline_eod 回填 regulatory_zone）：
    # 监管口径=偏离值（个股累计涨幅 − 对应指数累计涨幅）：10日偏离≥100% / 30日偏离≥200%
    # → 沪深交易所严重异常波动认定线（重点监控/停牌核查·连板预期打折）red 警示；
    # 接近(≥70%/≥140%) orange 提示。并给出「明日触发」预警（指数平盘假设）：
    # 明日涨 trig_gap_pct%（价 trig_price）即触监管线——一个涨停板内可触→orange 强提示。
    # 无字段（eod 未回填/日K不足/北交所无对应指数）→ 不标（诚实降级·数据即事实）。
    d10 = it.get("dev_10d_pct")
    d30 = it.get("dev_30d_pct")
    tg = it.get("trig_gap_pct")
    tp = it.get("trig_price")
    hit10 = hit30 = False
    if isinstance(d10, (int, float)) or isinstance(d30, (int, float)):
        hit10 = isinstance(d10, (int, float)) and d10 >= 100.0
        hit30 = isinstance(d30, (int, float)) and d30 >= 200.0
        near10 = isinstance(d10, (int, float)) and 70.0 <= d10 < 100.0
        near30 = isinstance(d30, (int, float)) and 140.0 <= d30 < 200.0
        d10t = f"10日偏离{'+' if d10 > 0 else ''}{d10:.0f}%" if isinstance(d10, (int, float)) else ""
        d30t = f"30日偏离{'+' if d30 > 0 else ''}{d30:.0f}%" if isinstance(d30, (int, float)) else ""
        seg = "·".join(x for x in (d10t, d30t) if x)
        if hit10 or hit30:
            tags.append((f"⚠️监管线·{seg}", "red"))
        elif near10 or near30:
            tags.append((f"近监管线·{seg}", "orange"))
    # 明日触发预警：已触线(hit)的票不显示（无意义·已过线）
    if isinstance(tg, (int, float)) and tg > 0 and not (hit10 or hit30):
        # 渲染档位来自规则文件 config/regulatory_rules.json（render 节·改规则不动代码）：
        # limit_board_pct 一板内=橙标强提示；neutral_mult 3 板内=灰标；>3 板不显示。
        try:
            from scripts.regulatory_zone import load_rules
            _rn_r = (load_rules().get("render") or {})
            _lim = float((_rn_r.get("limit_board_pct") or {}).get(
                "gem" if it.get("is_20cm") else "main", 10.0))
            _mult = float(_rn_r.get("neutral_mult", 3.0))
        except Exception:
            _lim, _mult = 10.0, 3.0
        tp_txt = f"·{tp:.2f}元" if isinstance(tp, (int, float)) else ""
        if tg <= _lim:                                # 明日一板内即触监管 → 强提示
            tags.append((f"⚠️明日+{tg:.1f}%触监管{tp_txt}", "orange"))
        elif tg <= _lim * _mult:                      # 3 板内可触 → 中性数据
            tags.append((f"明日+{tg:.1f}%触线{tp_txt}", "neutral"))
    # 联动事实须等卡面阶段开放才可展示：sidecar_record 只留痕，不能静默改变旧卡。
    # 旧候选没有 linkage_facts 时保留历史标签，避免发布开关把历史卡渲染回退成空。
    _lf = it.get("linkage_facts") or {}
    _lf_ok = isinstance(_lf, dict) and _lf.get("schema") == "linkage_facts_v1"
    _show_linkage = _theme_linkage.rollout_allows_card_explain()
    _lf_status = str(_lf.get("eod_status") or "") if _lf_ok else ""
    _lf_theme = str(_lf.get("theme") or it.get("theme_name") or "")
    _lf_count = _lf.get("theme_pool_zt_count") if _lf_ok else None
    if _show_linkage and _lf_status == "linked" and _lf_theme and _lf_theme != "—" and isinstance(_lf_count, int):
        tags.append((f"同题材·{_lf_theme}{_lf_count}家涨停", "purple"))
    elif _show_linkage and _lf_status == "standalone":
        # 只陈述昨日完整池事实；是否可买由次日竞价红开助攻再裁决。
        tags.append(("同题材仅它涨停", "red"))
    elif not _lf_ok and it.get("theme_has_sector_effect"):
        th = str(it.get("theme_name") or "")
        n = int(it.get("theme_mates_cnt") or 0)
        if th and th != "—":
            tags.append((f"题材板块·{th}{n}家涨停", "purple"))
    elif not _lf_ok and it.get("sector_is_lone"):
        # 白话化（2026-09-11·禁项扫描）：原「板块孤军」属圈内造词 → 改直白描述
        tags.append(("同方向只它1家", "red"))
    # 共振标签独立于旧联动兼容链；旧版 confirmed 缺独立技术证据，不能继续宣称确认。
    _tr = it.get("theme_resonance_facts") or {}
    if _show_linkage and isinstance(_tr, dict) and _tr.get("schema") == "theme_resonance_facts_v1":
        _tr_status = str(_tr.get("status") or "")
        if _tr_status == "confirmed":
            tags.append(("板块共振待复核", "neutral"))
        elif _tr_status == "contradicted":
            tags.append(("板块本次净流出", "green"))
        elif _tr_status == "developing":
            tags.append(("板块共振待确认", "neutral"))
    # 带动性时序标签（2026-08-25 用户「小周期载体·反推板块」·annotate_carry 回填）：
    # carry_cnt=封板后 30min 内同题材新增涨停数 → 带动≥1 家紫胶囊「带动N家」（载体三标准
    # 「带动同属性」的机械证据）；0 家不标（真孤军走上方孤军/板块效应判定，避免噪音）。
    if int(it.get("carry_cnt") or 0) >= 1:
        tags.append((f"带动{int(it['carry_cnt'])}家", "purple"))
    # 个股资金/筹码标签（2026-08-25 用户「查询用腾讯自选股」·tx_query 回填）：
    #   main_flow_yi 当日主力净流入(亿)：正=资金进场(red)/负=流出背离(green·呼应板块加强资金验证)
    #   chip_profit_rate 获利盘%：≥90 高位获利盘重·抛压警示(green·接高位分歧纪律)
    # 数据即事实：无回填不标（诚实降级）；正负语义接 A 股色板（流入红/流出绿）。
    mf = it.get("main_flow_yi")
    if isinstance(mf, (int, float)) and abs(mf) >= 0.3:  # 0.3亿以上才有信息量
        mf_c = "red" if mf > 0 else "green"
        mf_txt = f"主力{'+' if mf > 0 else ''}{mf:.1f}亿"
        tags.append((mf_txt, mf_c))
    pr = it.get("chip_profit_rate")
    if isinstance(pr, (int, float)) and pr >= 90:
        tags.append((f"获利盘{pr:.0f}%·抛压重", "green"))
    # 断板后回封标签（2026-08-20 用户「神奇制药竞价从地板拉起」·情绪核心经典买点）：
    # 断板回封=昨日未涨停+前日曾涨停→今日回封；violet 强调置顶。
    # ⚠️ 白话化（2026-09-14）：原「弱转强·N板后回封」的「弱转强」属圈内造词（禁项）→ 直白描述。
    if it.get("weak2strong"):
        w2s_txt = "断板后回封"
        if it.get("w2s_prev_boards") and it.get("w2s_prev_boards") >= 2:
            w2s_txt = f"前面{it['w2s_prev_boards']}板·断板后回封"
        tags.append((w2s_txt, "violet"))
    # 连板进阶（N进N+1）
    b = int(it.get("boards") or 1)
    if b >= 2:
        tags.append((f"{b}进{b+1}", "neutral"))
    # 封板质量
    zbc = int(it.get("zbc") or 0)
    if zbc == 0:
        tags.append(("封板稳", "neutral"))
    else:
        tags.append((f"开板{zbc}次", "neutral"))
    # 首封时间
    fbt = str(it.get("fbt") or "")
    if fbt.startswith("0925"):
        tags.append(("首封09:25", "neutral"))
    elif fbt and _fbt_le(fbt, "09:31:00"):
        tags.append(("早封", "neutral"))
    # 封单（2026-10-19 🌊 章「大单不是唯一的」）：**只认归一化口径**，绝对额仅辅助。
    # 封单/流通 <2% 与 defense_gate 派发特征同口径；小盘 5000 万可能比大盘 5 亿更厚。
    seal = it.get("seal_amt")
    ratio = it.get("seal_ratio_pct")
    if ratio is None and isinstance(seal, (int, float)) and seal > 0:
        _lt = it.get("lt_mktcap")
        if isinstance(_lt, (int, float)) and _lt > 0:
            ratio = round(seal / _lt * 100.0, 2)
    if isinstance(ratio, (int, float)) and ratio > 0:
        thin_high = b >= 4 and ratio < 2.0
        tags.append((f"封单占流通{ratio:.1f}%", "green" if thin_high else "neutral"))
    elif isinstance(seal, (int, float)) and seal > 0:
        tags.append((f"封单{seal:.2f}亿", "neutral"))
    # 换手率
    hs = it.get("hs") or 0.0
    if hs >= 15:
        # 白话化（2026-09-11·禁项扫描）：原「·高承接」属圈内造词 → 改直白描述
        tags.append((f"换手{hs:.1f}%·换手大", "neutral"))
    elif hs >= 8:
        tags.append((f"换手{hs:.1f}%", "neutral"))
    # 成交额 / 流通市值
    cje = it.get("cje_yi")
    if isinstance(cje, (int, float)):
        tags.append((f"成交{cje:.1f}亿", "neutral"))
    lt = it.get("lt_mktcap")
    if isinstance(lt, (int, float)) and lt > 0:
        tags.append((f"流通{lt:.0f}亿", "neutral"))
    # 价格（低价标签）
    price = it.get("price")
    if isinstance(price, (int, float)) and price > 0:
        if price < 5:
            tags.append(("超低价", "neutral"))
        elif price < 10:
            tags.append(("低价", "neutral"))
    # 20cm 弹性（创业板/科创板）
    if it.get("is_20cm"):
        tags.append(("20cm弹性", "neutral"))
    # 领涨 / 容量 / 梯队（真实因子语义，非代理）
    # 🐉 2026-10-27 用户「推的票必须是板块龙一」：
    #    同一系统里「谁是龙一」现在只有一处定义（`core/sector_leader`），卡面必须把它写明白——
    #    龙一标红（优先），非龙一标绿（回避·不推），⛔ 不要再出现"看着像候选、其实是跟风"。
    if it.get("is_sector_dragon"):
        _th = str(it.get("theme") or "").strip()
        _mt = it.get("theme_mates_cnt") or it.get("sector_mates_cnt")
        _lab = f"龙一·{_th}" if _th else "题材龙一"
        if isinstance(_mt, (int, float)) and _mt:
            _lab += f"{int(_mt)}家"
        tags.append((_lab, "red"))
    elif it.get("sector_has_mates") is not False:
        try:
            _dr = int(it.get("dragon_rank") or 0)
        except (TypeError, ValueError):
            _dr = 0
        # 🐉 2026-11-04 用户 BBB「龙一买不进 → 第 2 身位顶上」：
        #    `next_to_leader` 的票是这条线**唯一能买到的入口**，⛔ 不能再打「跟风不推」
        #    （否则同一张卡上「接力池第 1」与「跟风不推」自相矛盾）。
        _lead = str(it.get("dragon_leader_name") or "")
        if it.get("next_to_leader") and _dr == 2:
            tags.append((f"龙2·{_lead or '龙一'}买不进·接隔壁", "purple"))
        elif _dr > 1:
            tags.append((f"龙{_dr}·跟风不推", "green"))
    if it.get("is_capacity_core"):
        tags.append(("板块大票", "neutral"))
    if it.get("ladder_has_complete"):
        # 白话化（2026-09-14）：原「梯队完整」的「梯队」属圈内造词（禁项）→ 直白描述
        tags.append(("同方向各档都有", "neutral"))
    # 带动板块（已有业务标签）
    if "带动板块" in (it.get("tags") or []):
        tags.append(("带动板块", "neutral"))
    # 综合档位（与八维结论行文本「强候选·可接力」互补，此处作胶囊）
    # suppress_band=True 时抑制（全卡候选档位统一，如全员「重点候选」→ 不重复显示，用户2026-08-13定）
    if not suppress_band:
        band = (it.get("dim_panel") or {}).get("_band")
        if band == "强候选":
            tags.append(("强候选", "neutral"))
        elif band == "重点候选":
            tags.append(("重点候选", "neutral"))
    # 断板风险（高标龙一 + 风险代理维中/高）：唯一的红色预警胶囊（与行3 🧭 风险代 呼应），
    # 代理维标「代」不冒充真实因子，仅作风险胶囊
    risk_l = (it.get("dim_panel") or {}).get("风险", {}).get("label")
    if it.get("is_sector_dragon") and b >= 4 and risk_l in ("中", "高"):
        tags.append(("断板风险高", "red"))
    # 🌊 指数形态标注（2026-10-19）：主升标最强/跟风、分歧标逆势领涨/高位薄封。
    # ⛔ 只解释"这种环境该挑哪种票"，不是买入建议；语义色：改善红、恶化绿。
    _sf = it.get("style_flag")
    if _sf:
        _bucket = it.get("style_bucket")
        _c = {"core": "red", "hedge": "violet"}.get(str(_bucket or ""), "green")
        tags.append((str(_sf), _c))
    return tags


def _eod_candidate_tags(it: dict, *, suppress_band: bool = False) -> list[tuple[str, str]]:
    """只去除盘后冗余事实胶囊，风险/未知说明与晋级档不按颜色粗删。"""
    noise = ("封板稳", "首封09:25", "早封", "超低价", "低价", "20cm弹性",
             "板块大票", "同方向各档都有", "带动板块", "强候选", "重点候选")
    tags = [(text, color) for text, color in _candidate_tags(it, suppress_band=suppress_band)
            if not (color == "neutral" and (text in noise or text.startswith(
                ("成交", "流通", "封单"))))]
    tags = [(text, color) for text, color in tags if text != "🎯 三高"]
    if it.get("boards") == 1:
        tags.append(("1进2", "neutral"))
    return tags


def _render_tag_cloud(tags: list[tuple[str, str]]) -> str:
    """把 [(文本, 色)] 拼成飞书 lark_md 彩色胶囊串（单行，容器窄时自动折行）。"""
    if not tags:
        return ""
    return " ".join(f"<text_tag color='{c}'>{t}</text_tag>" for t, c in tags)


def _ladder_tag(it: dict) -> str:
    """板块梯队标签：如「梯队A(完整)」「梯队C(不完整)」；无数据返回空。"""
    g = it.get("ladder_grade")
    if not g:
        return ""
    comp = "完整" if it.get("ladder_has_complete") else "不完整"
    return f"梯队{comp}·{g}级"

# 涨停形态 → 白话（2026-09-10 用户「话术上删除专业术语」）
_SEAL_FORM_PLAIN = {
    "一字板": "一字板",
    "炸板回封": "砸开后回封",
    "放量烂板": "放量、封板不稳",
    "快速板": "封板很快",
    "放量板": "放量涨停",
    "温和放量": "量能温和",
    "缩量板": "缩量涨停",
    "缩量": "量能缩了",
}


def _seal_form_tag(it: dict) -> str:
    """涨停形态标签（白话）：一字板 / 砸开后回封 / 放量、封板不稳 …；无则空。
    2026-09-10 用户「话术上删除专业术语」：原输出 `[放量烂板×1]` 方括号+黑话 → 改白话括号。"""
    exp = it.get("expectation") or {}
    form = exp.get("seal_form") or ""
    if not form:
        return ""
    zbc = int(exp.get("seal_zbc") or 0)
    txt = _SEAL_FORM_PLAIN.get(form, form)
    if zbc >= 1:
        txt += f"·砸开{zbc}次"
    return f"（{txt}）"

# 大盘门控档位 → 白话（2026-09-10 用户「不要推专业术语」：去 门控=ice 这类字段名）
_GATE_PLAIN = {
    "ice": "市场很冷",
    "risk_on": "市场转暖",
    "risk_off": "市场转冷",
    "neutral": "市场平稳",
}

# 情绪标签 → 白话（2026-09-10 用户「不要推专业术语」：feedback_matrix.sentiment_label
# 原样透传会把「退潮/冰点/亢奋」等圈内定调词带上卡面·禁项）。
# ⛔ 仅显示层映射，feedback_matrix 原始字段不动；未知标签原样返回（诚实透传）。
_SENTIMENT_LABEL_PLAIN = {
    "退潮": "跌得多、人气差",
    "冰点": "很冷",
    "修复": "在转好",
    "分歧": "不冷不热",
    "亢奋": "很热",
    "升温": "在转热",
    "中性": "不冷不热",
}


def _sentiment_label_plain(raw) -> str:
    return _SENTIMENT_LABEL_PLAIN.get(str(raw or "").strip(), str(raw or ""))


# 🎯 明日执行判定 字段 → 白话（2026-09-14 漏网修复）：原实现 `f"{k}={v}"` 直接把
# basis/rules 的**内部字段名**（sentiment=/promote_rate=/nuke_count=/avg_pct=/
# defense_level=/position=/count=/entry=/stop=）推上卡面 → 属禁项。
# ⚠️ 教训：`test_decision_outlook_copy_no_jargon` 当年显式「排除 basis」（理由=basis 不上卡），
# 而卡面正是渲染 basis/rules → 扫描与渲染错位。现由 `test_eod_decision_block_no_jargon`
# 对**整卡渲染结果**做扫描，堵死这类「测试排除项 = 实际渲染项」的漏网。
_DECISION_BASIS_PLAIN = (
    ("sentiment", "做多气氛{}/10"),
    ("promote_rate", "晋级率{}%"),
    ("nuke_count", "核按钮{}只"),
    ("avg_pct", "涨停后次日平均涨{}%"),
    ("defense_level", "趋势走弱{}档"),
    ("regime", "市场状态{}"),
)
_DECISION_RULE_PLAIN = (
    ("position", "仓位：{}"),
    ("count", "同时持有：{}"),
    ("entry", "参与条件：{}"),
    ("stop", "止损：{}"),
)


def _decision_fields(src, spec) -> list[str]:
    """🎯 明日执行判定字段 → 白话短语（仅显示层；键名/阈值/verdict 一律不动）。"""
    out: list[str] = []
    for key, tpl in spec:
        v = (src or {}).get(key)
        if v is None:
            continue
        if key == "defense_level":
            try:
                if not float(v or 0):
                    continue          # 0 档不渲染（无信息量）
            except (TypeError, ValueError):
                continue
        if key == "regime":
            v = _GATE_PLAIN.get(str(v), v)
        out.append(tpl.format(v))
    return out


def _gate_plain(raw) -> str:
    return _GATE_PLAIN.get(str(raw or "").strip(), str(raw or ""))


# 竞价信号状态 → 白话（2026-09-10 用户「不要推专业术语」：修 hit 行泄漏英文 `expected`）
# ⛔ 2026-11-10：文案不再在本文件定义，统一由 `core.verdict` 派生（改词只改那一处）。
_STATUS_PLAIN = {k: _vs.label(v) for k, v in _vs.STATUS_MAP.items()}


# 观察池分组 → 白话（仅显示层别名·数据键 watch_group 不动）
_WATCH_GROUP_PLAIN = {
    # 👀 2026-11-10 晚：单池合并后，全市场唯一最高板单独作「高度观察」（只看不买）。
    #    ⛔ 放在第一位：它是标尺，不该埋在 25 只后面。
    "高度观察": "👀 高度观察·全市场唯一高度（只看不买）",
    "容量核心": "板块大票（体量大）",
    "高标/题材核心": "高连板 / 题材领头",
    "最高板封单过薄": "封单太薄·只看不买",   # 2026-10-27 用户「只观察」
    "补充观察": "补充观察",
    # ⛔ 2026-11-04 用户「观察池把一字的龙头也放进来一下」：
    #    分组是**白名单**，新组名不登记 → 该票从卡面消失（数据在、人看不到）。
    "一字核心": "一字封死·厚封单",           # 封单/流通 ≥5%（买不进，只看）
    "一字龙头": "一字封死·买不进",           # 封单数据缺失/不厚，仍是龙头 → 只观察
    # ⛔ 2026-11-04 用户「2 进 3 以上全部进观察池」：过筛 reject 的多板票也会进观察池，
    #    新分组不登记 → 该票从卡面消失（数据在、人看不到），与本轮改动初衷相反。
    "过筛未过·只观察": "接力资格没过·只看",
    "历史连板修复": "历史连板修复·资格逐票核验",
    # ⛔ 2026-11-13：新方向首板槽 / 板块核心观察池都是当轮新增的 watch_group，
    #    不登记 → 该票从卡面消失（11-13 实证：横店影视＝当日唯一可买 + 6 只板块核心
    #    全部没上卡，只剩 5 只）。新增 watch_group 必须同步登记本节。
    "新方向首板": "🌱 新方向首板·明早竞价定能不能接",
    "板块核心观察": "🏛 板块核心观察·只看不买",
}


# 🛡️ 前置防守闸门 → 白话短标签（2026-10-08 回撤经验·`core/defense_gate.py`）
# 只用于「竞价达标但没买」名单的一眼原因；理由正文由个股段 `_ai_view_line` 渲染（禁重复）。
_DEFENSE_GATE_PLAIN = {
    "distribution": "昨天封单又薄、换手又大，像是在往外倒货",
    "distribution_thin_late": "昨天封得又晚、封单又薄，位置还高",
    "sector_drain": "它这条线昨天资金一直在往外走",
    "high_position": "位置太高了，是断板之后的第二波反抽",
    "old_dragon_rebound": "是炒过的老票，断了很久才又拉首板，不是新方向",
}


def _pool_reason(txt: Any, *, observing: bool) -> str:
    """接力池简列区的「为什么它在这」——⛔ 不许再说「不参与」。

    2026-11-10 晚：`POOL_CALL_ALL_TRADEABLE=1` 之后，原观察票 09:25 **真能买**
    （11-10 买的东方创业就来自观察池）。盘后候选里那套「不参与 / 只看不买」
    是旧口径留下的字，留着就是「卡面说不能买、系统照买」。
    唯一例外：`高度观察`（全市场唯一最高板）确实只看不买，原样保留。
    """
    s = str(txt or "")
    if observing or not s:
        return s
    for a, b in (
        ("位置要看，不参与", "位置要看·明早竞价判"),
        ("仅参照，不参与", "仅作参照"),
        ("；不参与", ""), ("，不参与", ""),
        ("只看不买", "明早竞价判"),
    ):
        s = s.replace(a, b)
    return s


def _status_plain(raw) -> str:
    """signal_status 英文档位 → 中文白话；未收录原样返回（不吞数据）。

    ⛔ 文案单一源 = `core.verdict.STATUS_MAP/LABEL`；本函数只是薄包装。"""
    return _vs.status_plain(raw)


def _gap_strength_short(it: dict) -> str:
    """高开强度短标签（预期区间旁点睛）：超预期 / 高开 / 低开。
    三档对齐双维度规则引擎 gap_strength：strong=超预期、mid=高开、weak=低开。
    颜色由调用处统一包红（与预期同步），此处只给纯文本（用户2026-08-13定）。"""
    s = (it.get("expectation") or {}).get("gap_strength")
    return {"strong": "超预期", "mid": "高开", "weak": "低开", "unknown": "情绪待确认"}.get(s, "")

def _conf_tag(it: dict) -> str:
    """研判可信度标签（非盈利概率）：可信度46%（≥80% 高可信）。

    注意：confidence 是「预期区间本身可不可信」的加权评分（情绪0.3+龙头0.3+形态0.2+板块0.2+调节项），
    不是「涨/赚的概率」。按用户口径显示为 可信度XX%，避免被误读为盈利概率。"""
    c = (it.get("expectation") or {}).get("confidence")
    if not isinstance(c, (int, float)):
        return ""
    return f"预期可信{int(round(c * 100))}%"


def _theme_dual_tag(it: dict) -> str:
    """当前炒作题材 vs 行业归属 双标签（2026-08-17 用户「识别核心炒作题材·非静态行业」）。

    黄河旋风示例：🔥 金刚石/培育钻石｜🏷 通用设备（题材前置·不带「行业/炒作」字样·不显示热度）。
    无 core_theme 数据（旧候选/抓取失败）→ 只显示行业（诚实降级）。"""
    ind = str(it.get("industry_sector") or it.get("theme") or it.get("sector") or "")
    ct = str(it.get("core_theme") or "")
    if not ct or ct == "—" or ct == ind:
        return f"🏷 {ind}" if ind else ""
    return f"🔥 {ct}｜🏷 {ind}"


def _exp_verdict(r: dict, oc: dict) -> tuple[str, str]:
    """
    竞价验证卡专用：给出「盘后预期参考」与「实际竞价 vs 预期」判定。
    - exp_ref：盘后给的预期区间 + 高开强度 + 形态 + 置信（双维度规则引擎输出）
    - verdict：实际竞价涨幅 相对 预期区间 的判定（✅符合预期 / 🔺超预期 / ⚠️低于预期）
    无预期数据时返回 ("", "") 以便优雅降级（旧数据不显示新字段）。
    """
    exp = r.get("expectation") or {}
    erng = exp.get("expected_range")
    if not (isinstance(erng, (list, tuple)) and len(erng) == 2):
        return "", ""
    lo, hi = float(erng[0]), float(erng[1])
    exp_ref = f"预计{lo}~{hi}%{_gap_strength_short(r)} {_seal_form_tag(r)} {_conf_tag(r)}".strip()
    gap = oc.get("gap_pct")
    super_min = exp.get("super_gap_min")
    if isinstance(gap, (int, float)):
        # 三档判定 = `ai_caliber.gap_zone`（唯一判据源，与账本成交同源，
        #   防「标题行说高于预期 / AI 判断行说符合预期 / 账本却在买」三方脱节）
        _zone = _ai_caliber.gap_zone(
            gap, erng, expected_gap_pct=r.get("expected_gap_pct"),
            is_20cm=r.get("is_20cm"))
        # ⛔ 11-05 删 5%/红开：旧 `super_gap_min` 是盘后主观绝对线，relative 模式下
        #    不再抢在 zone 前把高开票标成「超预期」——强票高开本就是符合它自己的预期。
        if (not _ai_caliber.relative_mode() and super_min is not None
                and gap >= float(super_min)):
            verdict = _vs.tag(_vs.Verdict.SUPER)
        elif _zone == "above":
            verdict = _vs.tag(_vs.Verdict.SEALED) if _ai_caliber.relative_mode() else _vs.tag(_vs.Verdict.ABOVE)
        elif _zone == "ok":
            verdict = _vs.tag(_vs.Verdict.OK)
        elif _zone == "unknown":
            verdict = ""
        else:
            verdict = _vs.tag(_vs.Verdict.DEEP_BELOW if _ai_caliber.relative_mode()
                              else _vs.Verdict.BELOW)
        # 炸板类（炸板回封/放量烂板/任何炸板）次日红开：超预期信号，但需辨真假高开。
        # 2026-08-18 修：仅当判为非「低于预期」才追加——微红近平开（如 +0.3%）根本谈不上
        # "高开"，不能辨真假（曾把 苏州天脉/贵研铂业 的"低于预期"错配成"·辨真假高开"）。
        form = (exp.get("seal_form") or "")
        zbc = int(exp.get("seal_zbc") or 0)
        if (form in ("炸板回封", "放量烂板") or zbc >= 1) and gap > 0 and not _vs.is_negative(verdict):
            verdict = verdict + "·辨真假高开"
    else:
        verdict = ""
    return exp_ref, verdict


def _verdict_tag(verdict: str, *, loud: bool = False) -> str:
    """预期判定标签。

    ⛔ 2026-10-21 用户「观察这边颜色不要这么花哨，重点突出要买的」：
      **红色只留给买入票**（`loud=True`）。观察/放弃票的「✅符合预期」降为灰——
      它本来就不是行动项，给红色会跟真正要买的那只抢眼。
      `⚠️低于预期` 一律中性灰（2026-08-18 原规则，未达标不是机会）。
    """
    if not verdict:
        return ""
    if _vs.is_negative(verdict):
        color = "neutral"
    else:
        color = C_BUY if loud else C_HINT
    return f" <text_tag color='{color}'>{verdict}</text_tag>"


def _today_open_read(rows: list[dict]) -> str:
    """今日开盘实时读数（2026-09-18 用户「这是昨天的判断·今天集合竞价开出来应该不是这个东西吧」）。

    卡的定调（regime/做多气氛）来自**前一日收盘**，而卡本身是今晨 09:26 推的 ——
    两个时点必须拆开交代：标题给「昨天收盘定的」，本行给「今天开盘实际长什么样」。

    ⛔ 只陈述**竞价事实**（高开/低开家数·平均涨幅），**禁给「今日做多气氛X分」**：
    情绪分由晋级率/炸板率/核按钮合成，三者都要盘中数据，09:25 算不出（禁编造）。
    ⛔ 样本是「本批候选」而非全市场 —— 候选已过滤，读数不代表全市场温度，文案须写明只数。
    返回 ""（无报价）→ 调用方不渲染本行。
    """
    gaps: list[float] = []
    for r in rows:
        oc = (r.get("performance") or {}).get("open_check") or {}
        g = oc.get("gap_pct")
        if isinstance(g, (int, float)):
            gaps.append(float(g))
    if not gaps:
        return ""
    n = len(gaps)
    up = sum(1 for g in gaps if g > 0.05)
    dn = sum(1 for g in gaps if g < -0.05)
    avg = sum(gaps) / n
    # 定性标签走固定阈值（可解释·非黑盒）：2/3 以上同向 → 偏强/偏弱，否则分化
    if up / n >= 2 / 3 and avg > 0:
        tag = "开盘偏强"
    elif dn / n >= 2 / 3 or avg < -1:
        tag = "开盘偏弱"
    else:
        tag = "开盘分化"
    return f"今日开盘：这 {n} 只里 {up} 只高开、{dn} 只低开，平均 {avg:+.2f}%（{tag}）"


def _load_zt_snapshot() -> dict | None:
    """读昨日涨停快照，拿连板天梯 / 涨停数（eod 卡用）。"""
    p = ROOT.parent / "cache" / "yesterday_zt.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _load_metrics() -> dict | None:
    """收益指标：以真实净值账本 account_nav_history.jsonl 为单一真相源（已结算战绩）。

    - 账本非空：聚合出 样本(累计交易次数=账本行数)/胜率/平均收益/最新净值，返回与
      account_strategy_metrics.json 同结构的 {metrics.next_day.tradable.{covered,win_rate,avg_return}}
      + nav_latest，供 eod/verify 卡共用。避免验证卡只显示当批 pending 的 0%。
    - 账本为空：回退读 account_strategy_metrics.json（当批候选状态），兼容无结算场景。
    """
    ledger = CACHE_DIR / "account_nav_history.jsonl"
    rows: list[dict] = []
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    if rows:
        n = len(rows)
        rets = [float(r.get("avg_return_pct") or 0.0) for r in rows]
        wins = sum(1 for x in rets if x > 0)
        win_rate = round(wins * 100.0 / n, 1) if n else 0.0
        avg_return = round(sum(rets) / n, 2) if n else 0.0
        nav_latest = float(rows[-1].get("nav") or 0.0)
        # 回撤线（语录62）：历史高点 vs 当前，回撤率
        nav_peak = max(float(r.get("nav") or 0.0) for r in rows if r.get("nav"))
        nav_drawdown_pct = round((nav_peak - nav_latest) / nav_peak * 100, 2) if nav_peak > 0 else None
        # 口径分界（2026-09-14 用户「全量以 AI 为唯一口径」）：账本 09-14 前是脚本机械口径，
        # 之后是 AI 口径——净值曲线跨口径，必须在卡上标出分界日，否则读数会被误读为同一口径。
        first_ai = next((str(r.get("trade_date") or "") for r in rows
                         if str(r.get("caliber") or "") == "ai"), "")
        return {
            "schema": "nav_ledger_summary",
            "trade_date": str(rows[-1].get("trade_date") or ""),
            "caliber": str(rows[-1].get("caliber") or ""),
            "caliber_from": first_ai,
            "nav_latest": nav_latest,
            "nav_peak": nav_peak,
            "nav_drawdown_pct": nav_drawdown_pct,
            "metrics": {
                "next_day": {
                "tradable": {
                    "covered": n,
                    "win_rate": win_rate,
                    "avg_return": avg_return,
                }
                }
            },
        }
    # 回退：账本空时读当批候选指标（与原逻辑一致）
    p = CACHE_DIR / "account_strategy_metrics.json"
    if not p.exists():
        return None
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
        recs = m.get("records") or []
        return recs[-1] if recs else None
    except Exception:
        return None


def _caliber_note(m: dict | None) -> str:
    """账户战绩口径分界标注（2026-09-14 用户「全量以 AI 为唯一口径」）。

    账本 append-only：09-14 之前的 14 个交易日是**脚本机械口径**（signal_eval 达标即买），
    09-14 起换成 **AI 口径**（AI 判参与 ∧ 竞价不超自定上限）。曲线跨口径 → 卡上标分界日，
    避免把混合口径的胜率/净值读成单一策略成绩。账本尚无 AI 口径行 → 不渲染（诚实）。
    """
    if not m or str(m.get("caliber") or "") != "ai":
        return ""
    frm = str(m.get("caliber_from") or "")
    mmdd = frm[5:] if len(frm) >= 10 else ""
    return (f"（{mmdd} 起：买什么由 AI 定；此前按系统规则）" if mmdd
            else "（买什么由 AI 定）")


def _nav_drawdown_block(m: dict | None) -> list[dict] | None:
    """净值回撤线警示块（退学炒股心法·语录62 吸收·2026-08-24）。

    - 回撤 ≥ NAV_DRAWDOWN_WARN_PCT(10%) → 硬警示：回撤线之下·降档防守
    - 回撤 ≥ NAV_DRAWDOWN_HINT_PCT(5%) → 软提示：接近回撤线·控制仓位
    - 语义配色：回撤=恶化/利空 → green（A股语义色铁律，禁红）
    - 无账本/无数据 → None（不渲染，诚实降级）
    """
    if not m:
        return None
    dd = m.get("nav_drawdown_pct")
    if dd is None:
        return None
    peak = m.get("nav_peak")
    cur = m.get("nav_latest")
    peak_txt = f"{peak:.4f}" if peak is not None else "?"
    cur_txt = f"{cur:.4f}" if cur is not None else "?"
    # 🪶 精简模式（2026-10-21）：只留「数字 + 该怎么做」，删掉括号里的心法解释。
    #    ⛔ 不删警示本身——它是卡里唯一每天提醒「账户还在亏」的东西。
    _dense = _nav_drawdown_dense()
    if dd >= NAV_DRAWDOWN_WARN_PCT:
        if _dense:
            return [_div(
                f"🚨 净值回撤 <text_tag color='green'>-{dd:.2f}%</text_tag>"
                f"（高点{peak_txt}→现{cur_txt}）· 已跌破{NAV_DRAWDOWN_WARN_PCT:.0f}%线 → 降档防守"
            )]
        return [_div(
            f"🚨 净值回撤线 · <text_tag color='green'>-{dd:.2f}%</text_tag> 跌破{NAV_DRAWDOWN_WARN_PCT:.0f}%线\n"
            f"高点{peak_txt}→当前{cur_txt}，回撤线之下·降档防守（退学炒股心法：回撤50%需100%盈利才回本，先控回撤再谈进攻）"
        )]
    if dd >= NAV_DRAWDOWN_HINT_PCT:
        if _dense:
            return [_div(
                f"⚠️ 净值接近回撤线 <text_tag color='green'>-{dd:.2f}%</text_tag>"
                f"（距{NAV_DRAWDOWN_WARN_PCT:.0f}%线还差{NAV_DRAWDOWN_WARN_PCT - dd:.2f}个点）· 控制仓位"
            )]
        return [_div(
            f"⚠️ 净值接近回撤线 · <text_tag color='green'>-{dd:.2f}%</text_tag>（距{NAV_DRAWDOWN_WARN_PCT:.0f}%警戒线还差{NAV_DRAWDOWN_WARN_PCT - dd:.2f}个百分点）\n"
            f"高点{peak_txt}→当前{cur_txt}，控制仓位·防止继续回撤（退学炒股心法·语录62回撤线）"
        )]
    return None


def _sent_tag(v) -> str:
    """情绪分字符串（"2/10·退潮"）→ 语义色板（2026-08-16 用户定）：≥6 红(改善) / ≤2 绿(恶化) / 中间 neutral。"""
    try:
        num = float(str(v).split("/")[0])
    except Exception:
        return f"<text_tag color='neutral'>情绪 {v}</text_tag>"
    c = "red" if num >= 6 else ("green" if num <= 2 else "neutral")
    return f"<text_tag color='{c}'>情绪 {v}</text_tag>"


_PROMO_GATE_CACHE: dict = {"val": None, "loaded": False}


def _promo_gate() -> float:
    """晋级率动态阀门（2026-09-08 用户定）：feedback_matrix.json trend.promotion_gate_10d
    （前10交易日去极值截尾均值·不含当日）；文件缺失/字段 None（样本不足或开关关）→ 回退固定 25。
    模块级缓存：进程内只读一次，渲染多卡不重复 IO。"""
    if not _PROMO_GATE_CACHE["loaded"]:
        _PROMO_GATE_CACHE["loaded"] = True
        try:
            fb = json.loads((CACHE_DIR / "feedback_matrix.json").read_text(encoding="utf-8"))
            _PROMO_GATE_CACHE["val"] = (fb.get("trend") or {}).get("promotion_gate_10d")
        except Exception:
            _PROMO_GATE_CACHE["val"] = None
    return _PROMO_GATE_CACHE["val"] or 25.0


def _promo_tag(v) -> str:
    """晋级率字符串（"22.8%"）→ 语义色板：≥40 红(改善) / <动态阀门(前10日截尾均值·09-08 用户定) 绿(恶化) / 中间 neutral。"""
    try:
        num = float(str(v).replace("%", "").strip())
    except Exception:
        return f"<text_tag color='neutral'>晋级率 {v}</text_tag>"
    c = "red" if num >= 40 else ("green" if num < _promo_gate() else "neutral")
    return f"<text_tag color='{c}'>晋级率 {v}</text_tag>"


def _narrative_risk_tag(narrative) -> str:
    """AI 风险提示标签（2026-08-16 用户定·风险标记由 AI 打标，机器指标太死板）。

    机器规则（退潮+高开+首板）曾硬套「防一日游」，与 AI 判断打架（8/14 AI 称电力"避风港"
    但机器标一日游）。改为：风险提示由 AI 在 narrative 中书写，本函数仅做**格式提取**（不做
    "要不要标"的判断），提取 AI 已写的风险短句渲染为 turquoise 标签。
    提取优先级：① 显式 `风险：xxx` ② `⚠️xxx` ③ `【弃·风险】xxx` 首分句。无 → 不标（尊重 AI 判断）。
    """
    if not narrative:
        return ""
    s = str(narrative)
    m = _re.search(r"风险[:：]\s*([^。；;，,\n]+)", s)
    if m:
        return f" <text_tag color='turquoise'>⚠️ {m.group(1).strip()}</text_tag>"
    m = _re.search(r"⚠️\s*([^。；;，,\n:：]+)", s)
    if m:
        return f" <text_tag color='turquoise'>⚠️ {m.group(1).strip()}</text_tag>"
    m = _re.search(r"【弃·风险】\s*([^。；;，,\n:：]+)", s)
    if m:
        return f" <text_tag color='turquoise'>⚠️ {m.group(1).strip()}</text_tag>"
    return ""


# ── 点评语义分段（2026-09-11 用户「点评不要一大堆数据堆在一起·排版/换行/语义/加粗」）──────
# 用户原话：「这张卡的点评全部数据堆叠在一起了，建议把语文的段落语义分隔换行学习一下」。
# 病灶：一篇 300~500 字的点评挤在**一个 div 一行**里（名称+风险标签+走势+题材+股性全连写），
#       移动端就是一堵墙；风险句还被提到冒号后置首，语义顺序颠倒。
# 设计（只排版、不改写、不吞数据、不编造）：
#   ① **首选 agent 显式分段**：narrative 内用 \n 分行、行首写「段名：正文」；
#   ② **存量单段文本走锚点启发式**：按 走势 / 题材催化 / 历史股性 / ⚠️风险 四段切分，
#      从句读标点折行——保证任何文本都能排出层次，旧数据不倒灌。
#   ③ 渲染：每段独立一行（同一 div 内真实换行），段名加粗；**风险段置尾**且用 turquoise 标签
#      （唯一警示单元·语义顺序恢复为「走势→题材→股性→风险」，不再前置插在名称后面）。
#   ④ 铁律：⛔ text_tag 内禁 **（段名加粗只对普通文本）；⛔ 只在标点处折行，绝不切词。
_NARR_LABEL_ALIAS = {
    "今日走势": "今日走势", "走势": "今日走势", "今日": "今日走势",
    "题材催化": "题材催化", "题材": "题材催化", "催化": "题材催化",
    "历史股性": "历史股性", "股性": "历史股性", "涨停基因": "历史股性",
    "风险": "⚠️ 风险", "⚠️ 风险": "⚠️ 风险", "⚠️风险": "⚠️ 风险",
    "账户": "账户战绩", "战绩": "账户战绩", "账户战绩": "账户战绩",
    "盘面": "今日盘面", "盘面环境": "今日盘面", "今日盘面": "今日盘面", "环境": "今日盘面",
    "市场": "今日盘面", "大盘": "今日盘面",
    "操作": "操作思路", "操作思路": "操作思路",
    "昨日逻辑": "昨日逻辑",
    # 依据 = 昨日实时盘面事实（上涨逻辑·2026-10-14 用户图学习）：段名用「上涨逻辑」
    # （不带时点词 → 跨日平移安全，不会被 _shift_day_words 改成「前日…」）。
    "依据": "上涨逻辑", "上涨逻辑": "上涨逻辑",
    "推演": "操作思路", "预案": "操作思路", "操作建议": "操作思路",
}
_NARR_LINE_TARGET = 56          # 单行目标字数（折行阈值 = 56×1.35 ≈ 76 字）
_NARR_SEG_ORDER = ("今日走势", "题材催化", "历史股性", "⚠️ 风险")

# ── 时点词平移（2026-09-18 用户「毕竟是上一个交易日的结论·不要用『今日』·改成『昨日』」）──
# 病灶（实测 09-18 09:26 竞价卡）：卡片把上一交易日盘后写的文案（个股点评 / AI 判断理由 /
#   全局旁白 / AI 买入选票）原样搬到次日读——文案里的「今天」指撰写当天，读者却读成当天。
#   最刺眼的一处：🗣️ 已标「（09-17 周四收盘盘面）」，正文却写「**今日盘面**：今天市场明显转冷」，
#   而 09-18 实际是转暖日（78 家涨停）→ 同卡自相矛盾。
# 口径（**唯一规则**）：
#   · 产出端一律按「撰写当日」视角写相对时间词（今天＝撰写当天、明天＝撰写次一交易日）；
#   · 渲染端在「撰写日 ≠ 读卡日」（跨日复用）时统一 +1 天平移 —— 今天→昨天、昨天→前天、
#     明天→今天。当日卡（盘后 eod / 接力卡）不平移，保持原样。
#   · ⛔ 单次扫描替换，禁级联（否则「今天→昨天」会被二次替换成「前天」）。
#   · 「昨日/今日」为**时点标签**用词（AI 结论段 + 段名），当日实况读数一律「今日」。
_DAY_SHIFT_PAIRS = (("今天", "昨天"), ("今日", "昨日"),
                    ("明天", "今天"), ("明日", "今日"),
                    ("昨天", "前天"), ("昨日", "前日"))
_DAY_SHIFT_MAP = dict(_DAY_SHIFT_PAIRS)
_DAY_SHIFT_RE = _re.compile("|".join(_re.escape(_k) for _k, _ in _DAY_SHIFT_PAIRS))


def _shift_day_words(text: str) -> str:
    """相对时间词 +1 天平移（跨日复用文案：上一交易日撰写 → 次日读卡）。

    无时点词 → 原样返回（零副作用）。⚠️ 只用于**渲染出口**，⛔ 禁用于写盘/落库。
    """
    s = str(text or "")
    if not s:
        return s
    return _DAY_SHIFT_RE.sub(lambda m: _DAY_SHIFT_MAP[m.group(0)], s)


def _narr_label_of(text: str) -> tuple[str | None, str]:
    """解析「段名：正文」→ (规范段名, 正文)；段名不在白名单 → (None, 原文)。"""
    s = _re.sub(r"\*\*", "", str(text or "")).strip()
    m = _re.match(r"^(?:⚠️\s*)?(.{1,6}?)\s*[:：｜|]\s*", s)
    if m:
        lab = _NARR_LABEL_ALIAS.get(m.group(1).strip())
        if lab:
            return lab, s[m.end():].strip()
    return None, s


def _narr_wrap(body: str, target: int = _NARR_LINE_TARGET) -> list[str]:
    """长句折行：只在中英分句标点（；。！？，）处断，累积到 ≤target×1.35 字——绝不切词。"""
    body = str(body or "").strip()
    if not body:
        return []
    limit = target * 1.35
    out: list[str] = []
    cur = ""
    for a in _re.split(r"(?<=[；;。！？!?，,])", body):
        if not a:
            continue
        if cur and len(cur) + len(a) > limit:
            out.append(cur.strip())
            cur = a
        else:
            cur += a
    if cur.strip():
        out.append(cur.strip())
    return out


def _narr_heuristic(s: str) -> list[tuple[str, str]]:
    """存量单段点评 → 四段语义切分（锚点词；次序固定 走势→题材→股性→风险）。"""
    rest = str(s or "").strip()
    if not rest:
        return []

    def _cut(pattern: str) -> str:
        """按锚点切出「锚点之后」的文字，锚点之前的留在 rest 里。

        ⚠️ 锚点只吃自己（不吞前一句的句号/分号）——保留原文标点，不改写、不丢字。
        """
        nonlocal rest
        m = _re.search(pattern, rest)
        if not m:
            return ""
        seg = rest[m.end():].lstrip("，,。；;、 \t")
        rest = rest[:m.start()].rstrip()
        return seg

    risk = _cut(r"(?:⚠️\s*)?风险\s*[:：]\s*") or _cut(r"⚠️\s*")
    gene = _cut(r"历史股性\s*[:：]?\s*")
    theme = _cut(r"(?:背后是|催化是|逻辑是|原因是|催化来自)\s*")

    segs: list[tuple[str, str]] = []
    for lab, body in (("今日走势", rest), ("题材催化", theme),
                      ("历史股性", gene), ("⚠️ 风险", risk)):
        body = str(body or "").strip()
        if not body:
            continue
        for i, ln in enumerate(_narr_wrap(body)):
            segs.append((lab if i == 0 else "", ln))
    return segs


def _narr_segments(narrative, drop_labels: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """点评 → [(段名, 正文)]（段名 == "" 表示续行/无标题行）。

    `drop_labels`：**在折行之前**按段名整段丢弃（2026-10-21 修）。
      ⛔ 为什么必须在这里丢，不能在折行后丢：`_narr_wrap` 会把一个长段折成多行，
        只有第一行带段名、后续行 lab="" —— 事后按 lab 过滤会把续行留下来，
        卡面出现「用在 AI 眼镜、AI 玩具上。」这种**半句残段**（10-21 博通集成实测）。
    """
    s = str(narrative or "").strip()
    if not s:
        return []
    drop = set(drop_labels or ())
    rows = [c.strip() for c in s.split("\n") if c.strip()]
    if len(rows) <= 1:
        # 单段文本：先按「段名：正文」认白名单段名（2026-09-18）——全局旁白若被写成单段
        # 「盘面：今天市场…」，也必须认成**盘面段**，否则会退化成「今日走势」而无法随跨日平移
        # 成「昨日盘面」；认不出段名才走锚点启发式（存量点评 blob 走这条）。
        lab, body = _narr_label_of(s)
        if lab and body:
            if lab in drop:
                return []                  # 整段被丢 → 不留半句
            return [(lab if i == 0 else "", ln) for i, ln in enumerate(_narr_wrap(body))]
        return _narr_heuristic(s)          # 单段存量文本 → 锚点启发式
    out: list[tuple[str, str]] = []
    for c in rows:                          # agent 已显式分段：逐行取「段名：正文」
        lab, body = _narr_label_of(c)
        body = str(body or "").strip()
        if not body:
            continue
        if lab and lab in drop:
            continue                       # 整段丢（连同它折出的所有行）
        for i, ln in enumerate(_narr_wrap(body)):
            out.append(((lab or "") if i == 0 else "", ln))
    return out


_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _gn_compact(gn: str) -> str:
    """🪶 全局旁白压缩（2026-10-21 精简模式）：只留「操作思路」段。

    ⛔ 为什么砍另两段（不是随手删，是去重）：
      · 「账户战绩」段 → 卡头 `📈 已记录 N 次·胜率·净值` 一行已说（同一件事实只说一次）
      · 「今日盘面」段 → `📡 大盘环境`（今天几度）+ `🌊 行情位置`（走到哪了）两块已说
      · 「操作思路」段 = 唯一**只在这里出现**的内容（今天该怎么做），必须留。
    ⛔ 兜底：认不出段名 / 没有操作段 → **原文返回**，不硬删（宁可长一点也不丢信息）。
    """
    segs = _narr_segments(gn)
    if not segs:
        return str(gn or "")
    keep = [(lab, body) for lab, body in segs if lab == "操作思路"]
    if not keep:
        return str(gn or "")
    # 保留原始段名标记（_narr_render 会按白名单重新渲染成「🎯 操作思路」）
    return "\n".join(f"操作思路：{body}" if i == 0 else body
                     for i, (lab, body) in enumerate(keep))


def _narr_basis_label(narr_date: str | None, session_date: str | None) -> str:
    """🗣️ 全局旁白的「盘面基准日」标注（2026-09-14 用户拍板）。

    背景：竞价验证卡的 🗣️ 按设计透传 **上一交易日** 候选文件里的旁白，该旁白写的是上一
    交易日收盘盘面（正文原话"今天的市场是普跌…"）。原样渲染会被读成当日盘面——实测
    09-14 的卡引用的是 09-11（周五）的行情。

    用户口径（2026-09-14）：**用上一交易日的盘面即可**。开盘前当日盘面尚不存在，上一交
    易日代表"既定发生的事实"；不必为拿当日数据而等待/重取（会很慢）。

    故仅在「旁白基准日 ≠ 卡片当次交易日」时显式标出（例「（09-11 周五收盘盘面）」）；
    日期缺失或一致时返回 ""，保持原有渲染（向后兼容·不编造）。
    """
    nd = str(narr_date or "").strip()[:10]
    sd = str(session_date or "").strip()[:10]
    if not nd or nd == sd:
        return ""
    try:
        d = datetime.strptime(nd, "%Y-%m-%d")
    except ValueError:
        return f"（{nd} 收盘盘面）"
    return f"（{d.strftime('%m-%d')} {_WEEKDAY_CN[d.weekday()]}收盘盘面）"


def _narr_has_label(narrative, label: str) -> bool:
    """点评里是否已有某个段（用于「同一事实只说一次」去重判断）。

    例：`_narr_has_label(r["narrative"], "题材催化")` 为真时，AI 理由里的「上涨逻辑」段
    就是同一件事的第二次表述 → 不再叠加（2026-10-21 简洁模式）。
    """
    try:
        return any(str(lab) == str(label) for lab, _body in _narr_segments(narrative))
    except Exception:
        return False


def _narr_render(narrative, indent: str = "　　", *, prev: bool = False,
                 drop_labels: tuple[str, ...] = ()) -> str:
    """点评 → 语义分段的 lark_md 多行文本（段名加粗 / 风险段 turquoise 标签置尾）。

    prev: 文案**撰写日 ≠ 读卡日**（跨日复用 —— 竞价卡的点评/旁白/AI 理由都写于上一交易日
          盘后）→ 段名与正文统一 +1 天平移（今日走势→昨日走势、今天→昨天），
          使「上一交易日的结论」在次日卡上读作「昨日」。⛔ 当日卡（盘后 eod / 接力卡）保持
          默认 False，文案原样上卡。
    drop_labels: 只在**简洁模式**（2026-10-21）下由竞价卡传入，按段名丢弃重复/低价值段：
          · "历史股性"——同一批数字已在 `📈 历史基因` 行给过；
          · "今日走势"/"题材催化"——当天不动手的票不必展开讲故事。
          ⛔ 缺省空 tuple ＝ 完全沿用旧渲染（eod / 潜伏 / 其余卡零影响、可逆）。
    返回 "" 表示无点评（诚实省略，不占位不编造）。
    """
    # ⛔ drop 必须传进 `_narr_segments`（折行**之前**按整段丢）——
    #    事后按 lab 过滤会把折出的续行（lab=""）留下来，卡面出现半句残段。
    segs = _narr_segments(narrative, drop_labels=drop_labels)
    lines: list[str] = []
    for lab, body in segs:
        body = str(body or "").strip()
        if not body:
            continue
        if prev:                                   # 跨日复用：时点词 +1 天（段名 + 正文）
            lab = _shift_day_words(lab)
            body = _shift_day_words(body)
        if lab == "⚠️ 风险":
            lines.append(f"{indent}<text_tag color='turquoise'>⚠️ 风险</text_tag>：{body}")
        elif lab == "操作思路":
            # 操作思路 = 今日条件推演（若…则…）·关键执行信息 → violet 标签醒目
            # （2026-10-14 用户图学习：左「上涨逻辑」=昨日事实，右「操作建议」=条件分支，
            #   两者必须分层呈现；violet 不占红绿语义，攻防两种动作都适用）。
            lines.append(f"{indent}<text_tag color='violet'>🎯 操作思路</text_tag>：{body}")
        elif lab:
            lines.append(f"{indent}**{lab}**：{body}")
        else:
            lines.append(f"{indent}{body}")
    # 兜底：文本里没有「风险」字眼却带 ⚠️ 告警句 → 仍给一行 turquoise 标签（不编造·仅提取原文）
    if lines and not any(lab == "⚠️ 风险" for lab, _ in segs):
        tag = _narrative_risk_tag(narrative).strip()
        if tag:
            lines.append(f"{indent}{tag}")
    return "\n".join(lines)


def _real_env_block(pulse: dict | None, fb_env: dict | None = None) -> list[dict]:
    """📡 真实环境块：pulse 三大指数 + 情绪 + 板块风险 + 弱板块 + 强势板块（真实数据）。

    情绪口径（2026-08-16 统一）：优先 feedback_matrix 10 分制（sentiment_score/sentiment_label，
    唯一权威——曾踩坑：8/14 eod 卡用 pulse 5 级粗分"偏强" vs feedback 1/10 冰点，判读相反）。
    pulse.sentiment（latest.json 5 级粗分）仅作 fb_env 缺失时的兜底。
    """
    if not pulse:
        return []
    blocks: list[dict] = []
    idxs = pulse.get("index") or []
    if idxs:
        parts = []
        for i in idxs:
            pct = i.get("pct")
            arrow = "🔴" if (pct or 0) < 0 else "🟢"
            parts.append(f"{arrow}{i.get('name')}{pct:+.2f}%")
        blocks.append(_div("📡 **真实环境·大盘**：" + " ｜ ".join(parts)))
    # 情绪 + 涨停总数（情绪统一 feedback 10 分制，pulse 粗分兜底）
    if (fb_env or {}).get("sentiment_score") is not None:
        sent = f"{fb_env['sentiment_score']}/10·{fb_env.get('sentiment_label')}"
    else:
        sent = pulse.get("sentiment") or "未知"
    total_zt = pulse.get("total_zt") or 0
    ld = (pulse.get("limit_down") or {}).get("count", 0)
    broken = (pulse.get("broken") or {}).get("count", 0)
    blocks.append(_div(
        f"　　<font color='grey'>情绪={sent}｜涨停{total_zt}只｜跌停{ld}｜炸板{broken}（盘中实测）</font>"
    ))
    # P0-量能广度（2026-08-17）：两市成交额 + 全市场涨跌家数——盘面广度直接可见
    # 语义色板：量能/广度数据本身中性灰；广度普涨红、普跌绿（改善红/恶化绿）
    mkt = pulse.get("market")
    brd = pulse.get("breadth") or {}
    bu, bd = brd.get("up"), brd.get("down")
    parts = []
    if mkt:
        amount_txt = f"{mkt/10000:.2f}万亿" if mkt >= 10000 else f"{mkt}亿"
        vol_tag = "🔴放量" if mkt >= 25000 else ("🟢缩量" if mkt < 12000 else "平量")
        parts.append(f"{amount_txt}({vol_tag})")
    if bu is not None and bd is not None:
        ratio = bu / max(1, bd)
        color = "red" if ratio >= 2.0 else ("green" if ratio <= 0.8 else "grey")
        parts.append(f"<text_tag color='{color}'>涨{bu}/跌{bd}</text_tag>")
    if parts:
        blocks.append(_div(f"　　<font color='grey'>量能广度：{' ｜ '.join(parts)}</font>"))
    # 数据口径标注（2026-08-17 用户「标注统计口径与截止时间」）：涨跌家数沪深全市场、量能两市合计
    dscope = pulse.get("data_scope") or {}
    if dscope.get("asof"):
        blocks.append(_div(
            f"　　<font color='grey'>口径：{dscope.get('breadth','沪深全市场')} ｜ 量能{dscope.get('amount','沪深两市')} ｜ 截止 {dscope['asof']}</font>"
        ))
    # 板块风险：下跌板块（取前 3）+ 净流入/流出 top（Top5 全量·2026-08-14 用户铁律：监控5个渲染5个）
    sr = pulse.get("sector_risk") or {}
    declining = sr.get("declining") or []
    inflow = sr.get("inflow_top") or []
    outflow = sr.get("outflow_top") or []
    if declining:
        dparts = []
        for s in declining[:3]:
            dparts.append(f"{s.get('name')}{s.get('pct'):.2f}%")
        blocks.append(_div(f"　　<text_tag color='green'>板块风险(跌)</text_tag> { '、'.join(dparts) }"))
    if inflow or outflow:
        blocks.append(_flow_columns(inflow, outflow))
    # 弱板块聚焦（破板）
    ws = pulse.get("weak_sectors") or {}
    fh = ws.get("focus_hits") or []
    if fh:
        wparts = []
        for w in fh[:3]:
            sec = w.get("sector") or ""
            brk = w.get("broken") or []
            wparts.append(f"{sec}(破:{','.join(brk[:2])})" if brk else sec)
        blocks.append(_div(f"　　<font color='green'>弱板块预警：{ '、'.join(wparts) }</font>"))
    # 强势热点
    hs = pulse.get("hot_spots") or []
    if hs:
        hparts = [str(h.get("sector") or "") for h in hs[:6]]
        blocks.append(_div(f"　　<font color='grey'>强势热点：{ '、'.join(hparts) }</font>"))
    return blocks


def _gate_block(pulse: dict | None, fb_env: dict | None = None) -> list[dict]:
    """📡 大盘门控（2026-08-18 用户「重溢价·轻大盘」·eod 卡专属）：

    大盘环境只作门控开关一行，不铺开指数/量能/板块风险/弱板块/强势热点
    （那些属大盘情绪，不主导个股接力判断——个股层看自身强度+板块氛围+次日溢价）。
    数据唯一权威 = feedback_matrix（regime/晋级率/核按钮/情绪分），无则跳过；
    板块资金流向双列保留（2026-08-18 用户「重溢价」：资金流入方向→次日溢价概率）。
    """
    blocks: list[dict] = []
    if fb_env:
        parts: list[str] = []
        reg = fb_env.get("regime")
        sent = fb_env.get("sentiment_score")
        label = fb_env.get("sentiment_label")
        pr = fb_env.get("promote_rate")
        nk = fb_env.get("nuke_count")
        bc = fb_env.get("break_count")
        if reg:
            parts.append(_gate_plain(reg))
        if sent is not None:
            parts.append(f"做多气氛{sent}/10·{_sentiment_label_plain(label)}")
        if pr is not None:
            c = "red" if pr < 25 else ("green" if pr >= 40 else "grey")
            parts.append(f"晋级率<text_tag color='{c}'>{pr}%</text_tag>")
        if nk is not None:
            parts.append(f"核按钮<text_tag color='red'>{nk}只</text_tag>" + (f"(真破位{bc})" if bc else ""))
        if parts:
            blocks.append(_div("📡 **大盘环境**（只用来定今天能不能做）：" + " ｜ ".join(parts)))
    # 板块资金流向（保留·重溢价：流入方向→次日溢价概率）
    sr = (pulse or {}).get("sector_risk") or {}
    inflow = sr.get("inflow_top") or []
    outflow = sr.get("outflow_top") or []
    if inflow or outflow:
        blocks.append(_flow_columns(inflow, outflow))
    return blocks


def _cycle_phase_block(*, asof: str | None = None, verbose: bool = True,
                       persist: bool = True) -> list[dict]:
    """🌊 行情位置（情绪周期四阶段）—— 2026-10-21 P1：把 `core/cycle_phase` 的结论上卡面。

    **为什么单独一块、而不是并进 📡 大盘环境**：
    📡 那块讲的是「今天几度」（做多气氛 / 晋级率 / 核按钮），本块讲的是
    「**这段行情走到哪了 + 这一段该怎么打**」。两者是不同维度，且 92科比原话
    「周期位置不对，题材再好也是毒药」——位置是上游判断，必须肉眼可见。

    ⛔ 「同一件事实全卡只说一次」：所以本块**不重复**晋级率/情绪分这些数字，
      只给「位置 + 该用什么手法 + 仓位档」这三件 📡 那块没有的信息。
      证据句只说**方向**（从哪修上来的 / 在往坏走），不复述当期读数。

    `asof`：卡片对应的交易日。与周期基准日不一致时自动加基准日标注
      （verify 卡在次日 09:26 读到的仍是昨日收盘的矩阵，不标就会被读成今天）。
    `persist`：是否把「连续第几天」落盘。生产渲染=True；离线重渲/测试传 False
      （⛔ 否则同一天多渲几次会把生产状态文件的性质搞乱，虽说有幂等保护，
        但离线推演本就该零副作用）。
    """
    try:
        ph = _cycle_phase.phase_of()
        # P3（2026-10-21）：补「连续第几天」。⛔ 幂等——同一基准日多次渲染不重复计数。
        if ph and ph.get("phase") not in (None, "", _cycle_phase.PHASE_UNKNOWN):
            ph = _cycle_phase.track_state(ph, persist=persist)
    except Exception:
        return []                                   # ⛔ 周期判断失败不得影响整张卡
    if not ph or ph.get("phase") in (None, "", _cycle_phase.PHASE_UNKNOWN):
        return []                                   # 位置不明 → 诚实不占位（不硬凑）
    label = str(ph.get("label") or "")
    # ⛔ 用 `shift`（只讲方向）而不是 `why`（含当期读数）：晋级率/溢价等数字已由
    #    📡 大盘环境块讲过，再写一遍违反「同一件事实全卡只说一次」（2026-10-21 实测发现）。
    why = str(ph.get("shift") or ph.get("why") or "").strip()
    play = str(ph.get("play") or "").strip()
    pos = ph.get("position") or {}
    pos_label = str(pos.get("label") or "")

    # 基准日标注（与 🗣️ 旁白同法：只在不同日时才标，同日/缺失则静默）
    basis = ""
    _fd = str(ph.get("date") or "").strip()[:10]
    _as = str(asof or "").strip()[:10]
    if _fd and _as and _fd != _as:
        basis = f"<font color='grey'>（{_fd[5:]} 收盘盘面）</font>"

    blocks: list[dict] = []
    blocks.append(_div(f"🌊 **行情位置**{basis}：{label}"))
    # 证据句：只讲方向。`verbose=False`（竞价卡·早上时间紧）省略——位置 + 手法两行就够决策。
    if why and verbose:
        blocks.append(_div(f"　　<font color='grey'>{why}</font>"))
    # ⏱️ 第几天（P3·2026-10-21）：只在主跌/主升这类**有先后纪律**的阶段出现。
    #    ⛔ 低位震荡/高位震荡不给天数——那两段没有「第几天该做什么」的节奏，硬给会误导。
    try:
        _sn = _cycle_phase.streak_note(ph)
    except Exception:
        _sn = ""
    if _sn:
        blocks.append(_div(f"　　<font color='grey'>⏱ {_sn}</font>"))
    # 手法 + 仓位档：这一段「该干什么」，是本块最可执行的一行
    if play:
        _tail = f" · {pos_label}" if pos_label else ""
        blocks.append(_div(f"　　<text_tag color='violet'>🎯 这一段该这么打</text_tag>：{play}{_tail}"))
    return blocks


def _ai_block(ai: dict | None) -> list[dict]:
    """🧠 AI综合研判块：ai_env.json 的全局研判 + 市场体制 + 板块优先级/回避 + AI 笔记。"""
    if not ai:
        return []
    blocks: list[dict] = []
    gn = ai.get("global_narrative") or ai.get("notes")
    regime = ai.get("market_regime")
    score = ai.get("ai_env_score")
    pri = ai.get("sector_priority") or []
    avo = ai.get("sector_avoid") or []
    header = "<text_tag color='neutral'>🧠 AI综合研判</text_tag>"
    if regime:
        header += f" 体制：{regime}"
    if score is not None:
        header += f" ｜ 研判分 {score}/10"
    blocks.append(_div(header))
    if gn:
        blocks.append(_div(f"　　{gn}"))
    if pri:
        # 配色铁律（2026-08-14）：优先=红（利好/看多）、回避=绿（利空/看空），遵循涨红跌绿
        blocks.append(_div(f"　　<text_tag color='red'>板块优先</text_tag> { '、'.join(pri) }"))
    if avo:
        blocks.append(_div(f"　　<text_tag color='green'>板块回避</text_tag> { '、'.join(avo) }"))
    return blocks


def _relay_mode_note(mode: str | None) -> str:
    if mode in ("broad", "emergency"):
        return " ｜ ⚠️保守档（市场偏冷·仅供参考·风险高）"
    if mode == "relaxed":
        return " ｜ 常规接力"
    if mode == "strict":
        return " ｜ 只做强票"
    if mode == "freeze":
        return " ｜ ⛔市场很冷·只观察(做多气氛≤2分)"
    return ""


def _boards_txt(it: dict) -> str:
    """连板数标注片段（2026-09-10 用户「竞价要显示几板·方便确认接力」）：
    ` 3板` / ` 首板` / `""`（无板数据诚实省略）。"""
    b = it.get("boards")
    if not isinstance(b, (int, float)) or int(b) < 1:
        return ""
    return f" {int(b)}板" if int(b) >= 2 else " 首板"


# ── 观察区「按晋级档分组」（2026-11-03 用户转述 NIKA）────────────────────────
# 用户原话：「一进二 才该进去 / 二进三 / 三进四 还要回测」。
# 现场问题：11-03 竞价卡观察区 11 只按综合分平铺，1 板稿（文投 60.7、国金 73.4）
#   排在 6 板龙头（青岛食品 58.6）前面 —— 分数不是身位，混排之后**卡面读不出梯队**。
# 改法：分 1进2 / 2进3 / 3进4 / 高位 四档，档内按「身位 → 带动 → 封单 → 分」排。
# ⛔ 纯展示：不动任何 view 判定（买/观察/放弃 仍由 pool_call 决定）。
# 回滚：`VERIFY_CARD_TIER_GROUPS=0`。
TIER_GROUPS_ENV = "VERIFY_CARD_TIER_GROUPS"


def _tier_groups_on() -> bool:
    return str(os.environ.get(TIER_GROUPS_ENV, "1")).strip() not in ("0", "false", "False", "")


def _tier_label(it: dict) -> str:
    """晋级档名。⛔ 用「几进几」而不是「几板」——用户是按晋级阶段思考的。"""
    b = it.get("boards")
    if not isinstance(b, (int, float)) or int(b) < 1:
        return "身位未标"
    b = int(b)
    if b >= 4:
        return f"高位 {b}板"
    return {3: "3进4", 2: "2进3", 1: "1进2"}[b]


def _tier_order_key(it: dict) -> tuple:
    """档内排序：**身位（板数）→ 带动家数 → 封单/流通 → 综合分 → 代码**。

    ⛔ 身位必须第一：NIKA「青岛和百大不是一个热度」说的就是这个 ——
      6 板龙头与首板不能因为综合分高低而互换位置。
    """
    def _num(v) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    boards = it.get("boards")
    b = int(boards) if isinstance(boards, (int, float)) else 0
    seal = it.get("seal_ratio_pct")
    if seal is None:
        seal = it.get("seal_ratio")
    return (-b, -_num(it.get("carry_cnt")), -_num(seal), -_num(it.get("score")),
            str(it.get("code") or ""))


def _stock_hl_boards(it: dict, color: str = "purple") -> str:
    """竞价验证卡专用：个股标签追加**当前连板数**。

    格式 `中百集团(000759) 82.5 3板`；首板标「首板」；无板数据不标（诚实省略）。
    ⛔ 不动共享的 _stock_hl（eod 卡模板已验收），仅 verify 卡使用。

    `color=""` → **不上底色，只给灰字加粗**（2026-10-21 放弃票专用）。
    ⛔ 飞书 `<text_tag color=''>` 是非法值（会渲染异常），空色值必须走另一条路径。
    """
    score = it.get("score")
    score_txt = f" {score:.1f}" if isinstance(score, (int, float)) else ""
    body = f"{it.get('name', '')}({it.get('code', '')}){score_txt}{_boards_txt(it)}"
    if not str(color or "").strip():
        return f"<font color='grey'>{body}</font>"
    return _hl(body, color)


# ── 股性档阈值（2026-09-17 用户「股性定选谁」落地·可逆常量）──────────────
GENE_HABITUAL_MIN = 40.0   # 历史晋级率 ≥40% → 连板龙（接力强·优先）
GENE_WEAK_MAX = 15.0       # 历史晋级率 <15%  → 难连板（接力弱·降级）
GENE_MIN_HIST_YEARS = 1.0  # 历史窗口不足 1 年 → 年均失真，单独标「历史短」（2026-10-22 用户定）


def _load_hist_gene(code: str) -> dict:
    """读该票历史涨停基因档案（cache/zt_genes/hist_{code}.json）；缺失 → {}。

    2026-10-22 起实现下沉到 `core/zt_gene.py`（与选股侧的低位连板龙雷达共用同一份判据）。
    ⛔ 目录仍按本模块 `ROOT.parent/cache/zt_genes` 传下去——测试会 monkeypatch `ROOT` 指向
      tmp 目录注入假档案，若改成模块内写死绝对路径，这类测试就会读到真实底库而假失败。
    """
    return _zt_gene.load_hist_gene(code, ROOT.parent / "cache" / "zt_genes")


def _hist_annual_avg(g: dict) -> float | None:
    """历史年均涨停次数 = 全历史涨停次数 ÷ 历史年数（`first_day`→`last_day`）。

    ⛔ 用途＝给「连板龙」加**样本量门槛**时的基准（2026-10-22 用户定）：
       判据是「**近一年涨停次数 > 该股自身历史年均**」，⛔ 不是固定值（如 ≥8 次）。
       理由（用户原话）：「你不能说今年才刚开始两天你就找大于8的，肯定找不出来呀」——
       固定值在次新股 / 窗口起始段必然误杀；用自身年均＝**自适应**，且语义正是
       「它现在是不是处在自己的活跃期」——这才是「连板龙」该有的意思。

    返回 None 的情形：日期缺失/格式异常、或历史窗口 < `GENE_MIN_HIST_YEARS`
    （不足 1 年时年均会被极小分母放大，宁可诚实标「历史短」也不给假门槛）。
    """
    return _zt_gene.hist_annual_avg(g)


def _gene_grade(g: dict) -> tuple[str, str]:
    """股性档（连板龙 / 普通 / 难连板）—— 2026-09-17「股性定选谁」从**只展示**升级为**可判定因子**。

    ⛔ 术语（2026-10-22 用户定）：「惯犯/偶犯」是司法用语，⛔ 不许上卡、不许在对话里用；
       统一叫「连板龙」（接力强·能连）/「难连板」（一年涨停下来基本不连）。

    判据主看历史晋级率 `promo_rate_hist`（涨停次日再涨停 %），再叠加样本量门槛：
      ≥40% 且 近一年涨停次数 **> 该股历史年均** → 连板龙（接力强·同方向内优先、可给足仓位）
      ≥40% 但 次数未超年均 → 连板龙·次数少（能力像，样本不够·⛔ 不当同档给足仓位）
      ≥40% 但 历史不足 1 年 → 连板龙·历史短（年均失真·只作参考）
      15~40% → 普通
      <15%  → 难连板（接力弱·降级：仓位减半；环境弱时直接跳过）
    返回 (档名, 颜色)；数据缺失 → ("", "")（诚实省略·不误判）。
    """
    pr = g.get("promo_rate_hist")
    if pr is None:
        return "", ""
    try:
        v = float(pr)
    except (TypeError, ValueError):
        return "", ""
    if v >= GENE_HABITUAL_MIN:
        # 样本量门槛（2026-10-22 用户定）＝近一年涨停次数 > 该股历史年均，⛔ 不用固定值。
        # 实证：星光股份/哈空调近一年都只涨停 4 次（< 各自年均 5.31/4.52），晋级率 67%
        #   只有 3 次样本撑 → 必须与「大业 23 次/晋级 59%」区分开，否则「连板龙」被滥用。
        return _zt_gene.gene_grade(g)
    if v < GENE_WEAK_MAX:
        return "难连板", "green"
    return "普通", "neutral"


def _hist_gene_line(it: dict) -> str:
    """历史涨停基因行（2026-09-09 用户「历史涨停因子很重要·要加进卡里」·eod/verify 卡常驻渲染）：
    读 cache/zt_genes/hist_{code}.json（新浪不复权日K自算·6年窗·scripts/zt_genes.py hist 产出）。
    档案存在 → 返回灰字基因行（**股性档**·250日涨停次数·历史最长板·晋级率·首板次日高开）；
    档案缺失（未预生成/抓取失败）→ 返回 "" 诚实省略，不硬凑不报错。
    ⚠️ 2026-09-17：新增「股性档（连板龙/普通/难连板）」，供 AI 判定与人工速读（`_gene_grade`）。"""
    g = _load_hist_gene(str(it.get("code") or ""))
    zt = g.get("zt_250")
    if zt is None:
        return ""
    mb = g.get("max_boards_hist") or 0
    pr = g.get("promo_rate_hist")
    fpo = g.get("first_prem_open_hist")
    prs = "—" if pr is None else f"{float(pr):.0f}%"
    fpos = "—" if fpo is None else f"{float(fpo):+.2f}%"
    grade, _color = _gene_grade(g)
    gt = f"（{grade}）" if grade else ""
    # 口径（2026-09-09 双源验证统一）：涨停次数/晋级/首板高开=近250交易日窗；最长板=全历史
    return (f"　　<font color='grey'>📈 历史基因{gt}｜近一年{zt}次涨停·晋级{prs}·"
            f"首板高开{fpos}｜历史最长{mb}板</font>")


def _load_ai_view_raw(trade_date: str | None) -> dict:
    """AI 判断视图全文（2026-09-10 用户「AI 思考合并进竞价验证」）。

    读「狸猫猎手/cache/ai_view_{trade_date}.json」——AI 在盘后（T）对候选给出的判断，
    次日 verify 卡渲染「🤖 AI 今日买入计划」段 + 每票「🤖 AI 判断」行。
    文件缺失 → {}（诚实省略不编造）。
    """
    if not trade_date:
        return {}
    try:
        p = _ai_view_dir() / f"ai_view_{trade_date}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _load_ai_view(trade_date: str | None) -> dict:
    """AI 逐票判断映射（views）——缺失 → {}。"""
    return _load_ai_view_raw(trade_date).get("views") or {}


def _next_trade_day_inline(date10: str) -> str:
    """卡面读取AI视图时与执行链共用A股法定休市日历。"""
    from common.trading_calendar import next_a_share_trading_day
    return next_a_share_trading_day(date10) or str(date10)


def _ai_decision_limits(trade_date: str | None) -> dict[str, float]:
    """逐票入场上限兜底表（2026-09-14「三处同值」铁律的读取侧兜底）。

    AI 决策台账 `wealth_code/cache/ai_decisions.jsonl` 按 trade_date 匹配，给出
    {code: entry_max_gap}。用途：`ai_view_{trade_date}.json` 由**旧版本** ai_ledger 写入时
    缺逐票 `entry_max_gap`（该 bug 2026-09-14 修），渲染层会回落到顶层默认 3.0 →
    卡面显示「上限+3%」而账本按 decisions 的 2.0 结算，自相矛盾且误导。
    读取侧兜底使「卡面判定 = 账本结算」恒定同值，无需回填历史文件。
    文件缺失/解析失败 → {}（诚实降级：仍旧回落顶层默认，不编造）。
    """
    if not trade_date:
        return {}
    p = ROOT / "cache" / "ai_decisions.jsonl"
    out: dict[str, float] = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip()
            if len(code) != 6 or row.get("entry_max_gap") is None:
                continue
            td = (str(row.get("trade_date") or "").strip()
                  or _next_trade_day_inline(str(row.get("decision_date") or "")))
            if td == str(trade_date):
                try:
                    out[code] = float(row["entry_max_gap"])
                except Exception:
                    continue
    except Exception:
        return {}
    return out


_BIAS_LABEL = {"focus": "重点看", "normal": "一般", "avoid": "回避"}
_VIEW_LABEL = {"participate": "重点看", "watch": "一般", "skip": "回避"}


def _bias_of(v: dict | None) -> str:
    """取预案档 bias（重点看/一般/回避），缺省空串。"""
    return str((v or {}).get("bias") or "").strip()


def _plan_label(v: dict | None) -> str:
    """预案档标签：bias 优先，旧口径 view 兜底（participate→重点看 / watch→一般 / skip→回避）。"""
    b = _bias_of(v)
    if b in _BIAS_LABEL:
        return _BIAS_LABEL[b]
    return _VIEW_LABEL.get(str((v or {}).get("view") or "").strip(),
                           str((v or {}).get("view") or "") or "—")


def _plan_color(v: dict | None) -> str:
    """预案档配色：focus→红(优先) / avoid→绿(回避) / normal→紫(一般)；缺 bias 回退 view 配色。"""
    b = _bias_of(v)
    if b == "focus":
        return "red"
    if b == "avoid":
        return "green"
    if b == "normal":
        return "purple"
    _view = str((v or {}).get("view") or "").strip()
    return ("red" if _view == "participate" else
            "green" if _view == "skip" else "purple")


def _is_focus(v: dict | None) -> bool:
    """预案档＝重点看（focus）；旧口径 view=participate 兜底。买入口径「计划只数」基准。"""
    b = _bias_of(v)
    if b:
        return b == "focus"
    return str((v or {}).get("view") or "").strip() == "participate"


def _ai_focus_count(raw: dict) -> int:
    """AI 判「重点看」(bias=focus) 的只数 —— 买入口径「计划只数」基准（2026-09-14 用户「统一为AI口径」）。

    与 `_ai_plan_block` 的买入名单同源同判：只数 = views 里 bias=="focus" 的条目数
    （旧口径 view=="participate" 兜底·兼容真·历史数据）。⛔ 2026-09-22「彻底不派」：
    盘后**不派生** view，故不再数 view，直接数预案档 focus = 昨日重点看的票。
    无视图/空视图 → 0（调用方须先判 views 是否存在，缺失时回退机械口径，不编造）。
    """
    views = raw.get("views") or {}
    return sum(1 for v in views.values() if _is_focus(v))


def _load_ai_stats() -> dict | None:
    """AI 独立轨成绩（wealth_code/cache/ai_nav_history.jsonl·由 ai_ledger settle 写入）。

    与真人持仓完全无关：AI 自己定买入、自己按 T+1 竞价价成交、T+1 收盘结算。
    无记录 → None（卡上诚实写「还没开始记账」）。

    ⚠️ 路径**跟随 ROOT 动态计算**（非导入期快照）——修复 2026-09-11 测试隔离缺陷：
    原用模块级 `CACHE_DIR`（import 时求值），monkeypatch ROOT 不生效 → 单测读真实账本
    （渲染 14 次/42.9% 而非 mock 的 2 次/50%）→ 质量门红灯。与 `_ai_view_dir()` 同款模式。
    """
    p = ROOT / "cache" / "ai_nav_history.jsonl"
    if not p.exists():
        return None
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if not rows:
        return None
    wins = sum(1 for r in rows if float(r.get("avg_return_pct") or 0) > 0)
    avg = sum(float(r.get("avg_return_pct") or 0) for r in rows) / len(rows)
    return {"days": len(rows), "win_rate": round(wins / len(rows) * 100, 1),
            "avg": round(avg, 3), "nav": rows[-1].get("nav")}


_AI_STANCE_FALLBACK = {
    "attack": "环境允许·正常做",
    "selective": "只做最强的一两只",
    "defend": "只观察·不主动出手",
    # ⛔ 时点（2026-09-18）：定调写于上一交易日盘后 → 标签里**不写「今天」**（时点由
    #    「AI 昨日判断：」前缀承担），否则次日卡上「今天不买」会被读成当日结论。
    "hibernate": "空仓待命·不买",
}


def _ai_dyn_verdict(it: dict) -> tuple[str, str] | None:
    """今日竞价动态结论（2026-09-17 用户「不要写死板·竞价那一瞬间才能感知」）。

    背景：AI 盘后 view 是**盘后一次性判定**——盘后无法预知次日竞价强弱，故盘后只能保守
    给「观察」；用户要求「观察要有依据、要动态」：明显不及预期的票应归**放弃**，
    带量高开转强的票保留**观察**（超 5% 上限不追），达标票才叫**可参与**。

    口径：按**当日竞价实况**（gap_pct vs 盘后预期区间，复用 `_exp_verdict` 三档结论）
    给动态执行结论。⛔ 只影响卡面呈现，**不改账本/成绩口径**（AI 决策时序铁律：决策
    须竞价前定死，否则成绩失真）。
    返回 (文案, 颜色)；无竞价数据 → None（优雅降级·不加行）。
    """
    oc = (it.get("performance") or {}).get("open_check") or {}
    gap = oc.get("gap_pct")
    if not isinstance(gap, (int, float)):
        return None
    # 判据唯一源 = ai_caliber.gap_zone（2026-09-17「A 计划」·与账本成交同源·⛔ 禁另写一套）
    _exp = (it.get("expectation") or {}).get("expected_range")
    _zone = _ai_caliber.gap_zone(
        gap, _exp, expected_gap_pct=it.get("expected_gap_pct"),
        is_20cm=it.get("is_20cm"))
    if _zone == "unknown":
        return None
    g = f"{gap:+.2f}%"
    if _zone == "below":
        if _ai_caliber.relative_mode():
            return (f"→ 今日竞价{g}·明显低于这只票的预期 → 放弃", "green")
        return (f"→ 今日竞价{g}·不及预期 → 放弃", "green")   # absolute 回退逐字保留旧文案
    if _zone == "above":
        if _ai_caliber.relative_mode():
            # 11-05 删 5% 线：above 只剩「竞价封死买不进」这一物理事实。
            return (f"→ 今日竞价{g}·封在涨停附近买不进 → 只观察", "yellow")
        return (f"→ 今日竞价{g}·高开太多（超5%上限）→ 继续观察", "yellow")
    return (f"→ 今日竞价{g}·符合预期", "red")


def _wants_full_detail(oc: dict) -> bool:
    """该票今日是否**要动手**（决定卡面详略，2026-10-21 简洁模式）。

    要动 → 走势/题材/风险全给；不动 → 只留判定 + 风险一句。
    判据：9:25 实判 `ai_view == participate`；旧数据无该字段时回落 `decision_status`
    ∈ (buy, hit)——⛔ 宁可多给信息，也不把要买的票压成一行。
    """
    av = str(oc.get("ai_view") or "").strip()
    if av:
        return av == "participate"
    return str(oc.get("decision_status") or "").strip() in ("buy", "hit")


def _dragon_tag(oc: dict) -> str:
    """🐉 龙头身份标签（2026-10-21 P2）。

    ⛔ 只标前两档（真龙头 / 龙头·未验证），准龙头与跟风**不标**——
    每只票都挂一个身份＝等于没身份，违反卡片减噪铁律。
    与「降权≠不显示」一致：真龙头红、未验证龙头橙（橙色本身就在说「有保留」）。
    """
    dc = oc.get("dragon_confirm") if isinstance(oc.get("dragon_confirm"), dict) else None
    if not dc or not dc.get("enabled"):
        return ""
    return _dc.tag_text(dc) or ""


def _tier_tag(oc: dict) -> str:
    """同线身位标签（2026-10-21 用户「后排接力严格要谨慎！很有可能没有溢价」）。

    front（前排核心）不加标签——它就是要买的那只，标签只会添噪；
    second/back 在标题行给一个短标签，让读者一眼看到「它排第几」。
    """
    tier = str(oc.get("line_tier") or "")
    if oc.get("backrow_note"):
        # 已经是「买隔壁」的票：前排情况由买入行那句话交代，⛔ 不再叠一个「后排·没溢价」
        # （买了却挂「没溢价」标签会自相矛盾）。
        return ""
    if tier == "second":
        return "<text_tag color='yellow'>次排·溢价打折</text_tag>"
    if tier == "back":
        return "<text_tag color='green'>后排·没溢价</text_tag>"
    return ""


def _compact_drops(oc: dict, has_gene_line: bool, *, ai_skipped: bool = False) -> tuple[str, ...]:
    """竞价卡简洁模式：该票点评里应当丢弃哪些段（2026-10-21 用户「早盘内容过于冗余」）。

    三档详略，**按「今天要不要动手」定**：
      · 要买（9:25 判 participate / buy、hit）→ 只丢 `历史股性`（与 `📈 历史基因` 行同数据）；
      · 观察（pending，今天可能还要动手）→ 再丢 `题材催化`（讲故事），保留走势 + 风险；
      · 放弃 / 没达标 → 走势、题材、股性三段的讲故事部分全丢，只留 `⚠️ 风险` 一句。
    `ai_skipped=True`（🪶 2026-10-21 精简模式）：机械达标但 **AI 今天没买** 的票，
      再丢 `今日走势`——它的 decision_status 是 buy（机械口径），但今天不动手，
      讲「昨天怎么封的」属叙事；**`⚠️ 风险` 仍保留**（纪律信息，不是冗余）。
    ⛔ 任何档都不丢：竞价 gap、额/需要额、预期区间、板块、板数、历史基因、9:25 判定与理由。
    返回空 tuple 时渲染零变化（`VERIFY_CARD_COMPACT=0` 即整体回退）。
    """
    if not _compact_on():
        return ()
    drop: list[str] = []
    if has_gene_line:
        drop.append("历史股性")           # 与 📈 历史基因 行重复（同一批数字）
    if not _wants_full_detail(oc):
        drop.append("题材催化")
    if (str(oc.get("decision_status") or "") in ("reject", "unavailable")
            or (_compact_mode() == "max" and not _wants_full_detail(oc))):
        drop.append("今日走势")           # max 档：不买的票连走势也不展开
        # 2026-10-21 用户「文案可以 pass 放弃的个股」：放弃/没达标的票**整段点评不上卡**，
        # 判定理由已在 9:25 判那行说清；⚠️ 风险留给还在考虑要不要买的票。
        drop.append("⚠️ 风险")
    elif ai_skipped and _lean_on():
        drop.append("今日走势")           # 精简模式：AI 没买 → 走势是叙事，风险仍留
    return tuple(drop)


def _ai_view_line(it: dict, ai_view: dict, *, compact: bool = False) -> str:
    """AI 判断行（参与/放弃/观察）+ 今日竞价动态结论 + 理由（去重）。无 AI 观点 → ""（不占位）。

    ⛔ **理由去重（2026-09-17 用户「风险提示了两遍」）**：该票若已有**点评**
    （`record.narrative`·4 段含 `⚠️风险`），则**不再渲染 AI 理由正文**——reason（依据+⚠️风险）
    与点评内容高度重叠，两套同时上卡 = 同一票出现两条 `⚠️风险`（实测成对出现在相邻 block）。
    仅在**无点评**时以 reason 兜底（保信息不丢）。
    """
    code = str(it.get("code") or "")
    v = (ai_view or {}).get(code)
    if not v:
        return ""
    _oc = (it.get("performance") or {}).get("open_check") or {}
    view = str(v.get("view") or "")
    # ⛔ 2026-09-22「彻底不派」：盘后只写 bias（重点看/一般/回避），view 不再派生。
    #    若无 9:25 实判（旧口径回退），以**预案档** label/color 呈现（bias 优先·view 兜底）。
    label = _plan_label(v)
    color = _plan_color(v)
    # 🎨 2026-10-21：预案档颜色同样降权（紫/绿 → 灰），只有买入允许红
    if color in ("purple", "violet", "green", "orange", "yellow"):
        color = C_HINT

    # ── 🎯 9:25 池内实判（2026-09-21 用户「接力池＝提前缩范围，不是提前拍板」）──────────
    # 该票有今日 9:25 结论 → 单层渲染：**今日实判**（label 即 9:25 判的，不再有"两个时间层
    # 互斥终态同框"的问题）+ 昨日预案档 + 实判理由。⛔ 不再叠加 `_ai_dyn_verdict`（同一判断
    # 的第二次表述＝重复），也不再走旧口径的「终态去重」抑制（已无冲突源）。
    if _oc.get("pool_reason"):
        # ⛔ 标签必须取 **9:25 实判**（`open_check.ai_view`），⛔ 不是 ai_view 文件里的
        #    盘后预案档——否则会出现「判：观察」+ 理由「→ 买入」自相矛盾（2026-09-21 实测）。
        view = str(_oc.get("ai_view") or view or "")
        _label = {"participate": "买入", "watch": "观察", "skip": "放弃"}.get(view, label)
        # ⛔ 2026-10-21 颜色分层：买入=红（唯一行动项）/ 观察=淡蓝 / 放弃=灰
        #    ⛔ 放弃不再用绿色——绿是"跌"的语义（A股铁律），用它表达"不买"会与跌幅混淆。
        color = C_BUY if view == "participate" else (C_SKIP if view == "skip" else C_WATCH)
        # 昨夜的预案必须展示“为什么”，不能把每只票压缩成“重点看/一般/回避”三个
        # 标签。空仓只是当日名额结论，不抹去逐票的强弱、带动与风险信息。
        # 优先读盘后 AI 原因；缺失时诚实省略，不用档位名占位。
        _plan_reason = str(v.get("reason") or "").strip()
        # ⛔ 空色值（放弃票）→ 不上底色，只灰字（飞书 `<text_tag color=''>` 非法）
        _tags = [(_hl(f"🤖 今日9:25判：{_label}", color) if str(color or "").strip()
                  else f"<font color='grey'>🤖 今日9:25判：{_label}</font>")]
        txt = "　　" + " ".join(_tags)
        # 🚦 三态 C（2026-11-11）：这条线的核心/广度已失效 → 明写「今天不做」，
        #    ⛔ 不写「再看看」这种模糊话，也不发替代资格。
        if str(v.get("trade_state") or "") == "C" and v.get("trade_state_note"):
            txt += f" <font color='grey'>· {v['trade_state_note']}</font>"
        txt += "\n" + f"　　<font color='grey'>{_oc.get('pool_reason')}</font>"
        # ⛔ 「同一件事实全卡只说一次」（2026-10-21 用户「早盘内容过于冗余」）：
        #    该票若已有点评且点评含「题材催化」段，则 AI 理由里的依据/上涨逻辑与风险段
        #    就是同一件事的第二次表述（实测 10-21 宁波东力：昨日逻辑 349 字与点评题材催化
        #    近乎逐字相同）→ 不再叠加。⛔ 不是丢弃 AI 判断：9:25 判 + 实判理由仍完整在上两行。
        _dup_reason = (it.get("narrative")
                       and _narr_has_label(it.get("narrative"),
                                           "题材催化" if _compact_on() else "__never__"))
        # ⛔ 简洁模式（2026-10-21）：compact 只在**该票已有点评**（下方会渲染判定+风险）时
        #    才丢弃 AI 理由正文；没有点评的票仍要保留理由，否则读者看不到任何「为什么」。
        if _plan_reason and not _dup_reason and not (compact and it.get("narrative")):
            _plan_text = _narr_render(_plan_reason, indent="　　", prev=True)
            # 「依据」段名已被 alias 识别为「**上涨逻辑**」（2026-10-14）→ 不再叠加手动前缀；
            #  仅当理由是旧格式（无段名正文）时才兜底加「昨日逻辑」。
            if _plan_text:
                if "**上涨逻辑**" not in _plan_text:
                    _plan_text = _plan_text.replace("　　", "　　**昨日逻辑**：", 1)
                txt += "\n" + _plan_text
        return txt

    # 🚪 hibernate 逃生阀（2026-09-18 用户拍板 A）：AI 判「观察」但当日带量转强 → 已小仓买入。
    #    ⛔ 必须改写标签，否则个股段仍显示「观察」而账本/持仓里有成交 → 卡面自相矛盾。
    if view != "participate" and _oc.get("hibernate_valve"):
        label = f"{label} → 破例小仓买"
        color = "red"
    reason = str(v.get("reason") or "").strip()
    _dyn = _ai_dyn_verdict(it)      # 今日竞价实况动态结论（2026-09-17·盘后静态判断 → 今日动态）
    # ⛔ 终态去重（2026-09-18 用户「明明说观察又说放弃·这两个自己打架了」）：
    #   盘后 label 与今日竞价结论分属**两个时间层**；当动态结论已给出终态「放弃」时，
    #   再并列一个「观察」= 两个互斥终态同框（实测 ⚪其余没达标段 4 只全中）。
    #   · label ∈ (观察, 放弃) 且 动态结论含「放弃」 → 抑制 label，只留动态结论；
    #   · label == 参与 → **禁抑制**（AI 说买 / 机械不买 = 真分歧，必须展示）；
    #   · ⛔段（结论=符合预期）/ 🟡段（结论=继续观察）与「观察」语义一致 → 不受影响。
    _dyn_txt = _dyn[0] if _dyn else ""
    if _dyn and view != "participate" and "放弃" in _dyn_txt:
        txt = f"　　🤖 <text_tag color='{_dyn[1]}'>{_dyn_txt.lstrip('→ ').strip()}</text_tag>"
    else:
        # 时点标签（2026-09-18）：AI 的 view/reason 写于**上一交易日盘后** → 显式标「昨日判断」，
        # 与紧随其后的「→ 今日竞价…」动态结论形成两个时点，⛔ 禁写「AI 今日判断」。
        txt = f"　　<text_tag color='{color}'>🤖 AI 昨日判断：{label}</text_tag>"
        if _dyn:
            txt += f" <text_tag color='{_dyn[1]}'>{_dyn[0]}</text_tag>"
    if reason and not it.get("narrative"):
        # 理由语义分段（2026-09-11）：依据一行、⚠️风险独立末行（不再一整段连写）
        # prev=True：理由写于上一交易日盘后 → 时点词平移（今天→昨天），见 `_shift_day_words`。
        txt += "\n" + _narr_render(reason, prev=True)
    return txt


def _j25_line(oc: dict, *, why_limit: int = 70) -> str:
    """压缩版「AI 9:25 判定」单行：`🤖 今日9:25判：放弃 · 一句话理由`。

    ⛔ 2026-10-21 用户当场质问「AI 的放弃判断被你抹掉了？？」——精简档位曾只留
    `pool_reason` 灰字，把「AI 判它放弃」这个**结论**抹掉了：读者只看到一段理由，
    却不知道 AI 到底判放弃、观察还是买入。**结论是判断，理由只是支撑，⛔ 结论永不省。**
    `why_limit`：理由截断长度（档位 2 = 70 字，够说明白又不占版面）。
    """
    oc = oc or {}
    v = str(oc.get("ai_view") or "")
    label = {"participate": "买入", "watch": "观察", "skip": "放弃"}.get(v, "")
    why = str(oc.get("pool_reason") or "").strip()
    if why_limit and len(why) > why_limit:
        why = why[:why_limit] + "…"
    if not label:
        return f"　　<font color='grey'>{why}</font>" if why else ""
    # 🎨 2026-10-21 颜色分层：买入=红（唯一行动项）/ 观察=淡蓝 / 放弃=灰
    #    ⛔ 放弃原用绿色 → 与「跌」的语义冲突（A股绿色=跌），改为中性灰。
    color = C_BUY if v == "participate" else (C_SKIP if v == "skip" else C_WATCH)
    # ⛔ 空色值（放弃票）→ 不上底色，只灰字
    out = ("　　" + (_hl(f"🤖 今日9:25判：{label}", color) if str(color or "").strip()
                    else f"<font color='grey'>🤖 今日9:25判：{label}</font>"))
    if why:
        out += f" <font color='grey'>· {why}</font>"
    return out


def _trading_days_between(d1: str, d2: str) -> int:
    """d1 → d2 之间隔了几个**交易日**（不含 d1、不含 d2）。

    ⛔ 必须走 `common.trading_calendar`，⛔ 不许用 `weekday()` 数自然日——
       周五断板到周一，自然日差 3、交易日只差 1，数错就说不清"断了几天"。
    ⛔ 任一日期解析失败 → 0（不显示天数，不猜）。
    """
    a, b = str(d1 or "").strip(), str(d2 or "").strip()
    if len(a) != 10 or len(b) != 10 or a >= b:
        return 0
    try:
        from common.trading_calendar import prev_a_share_trading_day as _prev
        n, cur, guard = 0, b, 0
        # 从 d2 往前逐交易日回退，直到碰到 d1；⛔ guard 防死循环（最多 30 步）
        while cur > a and guard < 30:
            nxt = _prev(cur)
            if not nxt or nxt >= cur:
                break
            cur = nxt
            guard += 1
            if cur > a:
                n += 1
        return max(0, n)
    except Exception:
        return 0


def _oc_of(r: dict | None) -> dict:
    """取 record 的 open_check（缺省 {}）。"""
    return ((r or {}).get("performance") or {}).get("open_check") or {}


def _bought_codes(records: list[dict], views: dict | None = None) -> set[str]:
    """9:25 实判「买入」的票（＝账本口径）——机械/观察两段做**差集**用。

    🪶 2026-10-21：精简模式下机械段与观察段都只列「今天买入名单之外」的票，
    避免同一只票在卡里出现两套完整展示（实测 6 只里 4 只重复，是冗杂的真正来源）。

    口径与 `_ai_plan_block` 的买入名单严格同源：
      · 有 9:25 结论 → `open_check.ai_view == "participate"`
      · 旧口径（无 ai_view）→ 回落 `_is_focus(view)`（预案档 focus）
    ⛔ 差集只影响**展示**，不改任何裁决/账本。
    """
    out: set[str] = set()
    recs = [r for r in (records or []) if isinstance(r, dict)]
    has_pool = any(str((_oc_of(r) or {}).get("ai_view") or "").strip() for r in recs)
    if has_pool:
        for r in recs:
            if str((_oc_of(r) or {}).get("ai_view") or "") == "participate":
                out.add(str(r.get("code") or ""))
        return out
    for code, v in (views or {}).items():
        if _is_focus(v or {}):
            out.add(str(code))
    return out


def _is_ai_skipped(rec: dict, bought: set[str]) -> bool:
    """该票是否「AI 有结论但没买它」——机械段/观察段做 ai_skipped 判据。

    ⛔ **没有 AI 结论 ≠ AI 没买**（2026-10-21 实测踩坑）：
      旧候选文件 / `POOL_CALL_ENABLED=False` 时 record 无 `ai_view` 字段，
      此时它既没被买、也没被否——按「AI 没买」处理会把它的走势段无辜砍掉
      （`test_narrative_layout` 那条 buy 票就是这样红的）。
      只有 `ai_view` 明确是 watch/skip 才算「AI 看过但没买」。
    """
    code = str(rec.get("code") or "")
    av = str((_oc_of(rec) or {}).get("ai_view") or "").strip()
    if not av:
        return False          # 没结论 → 不按「没买」处理
    return av != "participate" and code not in bought


# ══════════════════════════════════════════════════════════════════════
# 🎨 指标权重分级（2026-10-21 22:33 用户「胶囊的颜色应该体现在权重上」）
# ══════════════════════════════════════════════════════════════════════
# 判据来自**短线接力实战**：买不买 → 强不强 → 参考。
#
#   W1 决定买不买（颜色最重，达标红 / 不达标绿＝警示）
#     · 竞价额 vs 需要额 —— 9:25 有没有资金接，唯一的第一判据
#     · 带起几家 —— 是不是这条线的核心（接力只做核心，不做跟风）
#   W2 决定强度（达标橙 / 其余灰）
#     · 封单占流通比 —— 封得牢不牢（薄封单一炸就没）
#     · 梯队等级 —— 板块有没有跟上（孤军走不远）
#     · 主力净流入 —— 资金是进还是出
#     · 炸板次数 —— 封板质量（0 次才算干净）
#   W3 参考（一律灰，不抢眼）
#     · 昨日成交 / 换手 / 流通市值 / 同题材家数
#
# ⛔ 颜色只有 4 种：red / green / orange / neutral。⛔ 不许再引 purple/yellow/blue
#    （2026-10-21 已因「太花哨」被否过一次）。
# ⛔ W1 的"不达标"用 **green**：A 股里绿＝不利，正好表达"这项没够"。
C_BAD = "green"


def _w1_tag(txt: str, *, ok: bool) -> str:
    """W1 指标胶囊：达标红（最重要且够）、不达标绿（最重要但没够）。"""
    return _hl(txt, C_BUY if ok else C_BAD)


def _w2_tag(txt: str, *, ok: bool) -> str:
    """W2 指标胶囊：达标橙、其余灰。"""
    return _hl(txt, "orange" if ok else C_HINT)


def _w3_tag(txt: str) -> str:
    """W3 参考指标胶囊：一律灰（不抢眼）。"""
    return _hl(txt, C_HINT)


def _vd_tag(label: str) -> str:
    """维度名胶囊（灰·五个维度统一）。"""
    return _hl(str(label), C_HINT)


def _buy_indicator_lines(r: dict, oc: dict, v: dict) -> list[str]:
    """🎯 买入票「指标打满」：五维铺开，**胶囊颜色＝指标权重**（2026-10-21 用户）。

    用户原话：
      「买入的这个标的是哪个板块、什么龙一、放量多少、占昨天的百分比，这些指标全部打上去。」
      「这些 tab 节点都要给我加上对应的 tag，不要平铺文字。」
      「胶囊的颜色应该体现在权重上。在短线接力的情况下，哪些是更重要的指标，
        那么这个胶囊的颜色就要更突出，跟在对应的推荐个股后面。」

    ## 结构（五行，每行一个维度）
      板块 / 身份 / 量能 / 带动 / 底子 —— 维度名走灰胶囊，后面跟**彩色指标胶囊**

    ## 配色＝权重（见文件头 W1/W2/W3 定义）
      W1 竞价额够不够、带起几家 → 红（够）/ 绿（没够）  ← 最突出
      W2 封单占比、梯队、主力资金、炸板 → 橙（好）/ 灰
      W3 昨日成交、换手、流通、题材家数 → 灰

    ⛔ **零网络**：全部取自已抓好的竞价快照 + 候选文件，不额外发请求
      （用户 22:28「那就不抓取吧，拿到的竞价金额就够，拿不到就算了」）。
    ⛔ 缺数据的项**整段跳过**，⛔ 不留「—」「0」占位（缺数据≠没放量/没炸板）。
    """
    out: list[str] = []

    def _line(label: str, parts: list[str]) -> None:
        items = [x for x in parts if x]
        if items:
            out.append(_vd_tag(label) + " " + "".join(items))

    # ── 板块：题材为主，行业/家数作补充 ──────────────────────────────────
    theme = str(r.get("core_theme") or r.get("theme_em_name") or r.get("theme_name") or "").strip()
    # ⛔ 2026-11-10 用户「就 pass 掉这个玄学方案吧，只是记录一下」：
    #    人工归因（东方系/羊年等）**只存档、不上卡、不进判据**。
    #    记录在 `cache/manual_theme.json`，查：`python3 scripts/theme_manual.py --list`。
    #    所以这里**不读** manual_theme —— 卡面题材名保持系统原值（哪怕它是行业兜底）。
    #    真要改，先过 A/B；在那之前别在这里加 hook。
    logic = str(r.get("core_theme_logic") or "").strip()
    ind = str(r.get("industry_sector") or r.get("sector") or "").strip()
    mates = r.get("theme_mates_cnt")
    p1: list[str] = []
    # 🏷 2026-11-11 用户「题材一定要稳住」：`theme_source=sector` 表示这只票的「题材」
    #    其实是**行业兜底**（题材库没覆盖到）。必须标出来，⛔ 不能让它冒充题材——
    #    否则读卡的人会以为「焦炭Ⅱ」是一条炒作线（它只是行业）。
    _theme_fallback = str(r.get("theme_source") or "") == "sector"
    if theme and theme not in ("—", "其他"):
        _t = theme + ("（按行业归类·未归题材）" if _theme_fallback else "")
        _t += (f"（{logic}）" if logic and logic not in ("—", theme) else "")
        # 题材是分组前提，不是权重项 → 灰；但值本身要醒目，用加粗
        p1.append(f" <text_tag color='{C_HINT}'>{_t}</text_tag>")
    if ind and ind != theme:
        p1.append(f" <text_tag color='{C_HINT}'>{ind}</text_tag>")
    if isinstance(mates, (int, float)) and mates:
        # ⛔ 同题材家数 = W2：家数多说明这条线成气候（≥5 家才算真成片）
        _ok = mates >= 5
        p1.append(f" {_w2_tag(f'同题材 {int(mates)} 家', ok=_ok)}")
    _line("板块", p1)

    # ── 身份：行业龙一 / 龙头角色 / 梯队 ────────────────────────────────
    p2: list[str] = []
    if r.get("is_sector_dragon"):
        # 行业龙一是接力资格的核心，W1
        p2.append(f" {_w1_tag(f'{ind or '本行业'}龙一', ok=True)}")
    role = str(r.get("leader_role") or "").strip()
    if role:
        # 真龙头 W1 红；准龙头 W2 橙；其余灰
        if role == "龙头":
            p2.append(f" {_w1_tag('龙头', ok=True)}")
        elif role == "准龙头":
            p2.append(f" {_w2_tag('准龙头', ok=True)}")
        else:
            p2.append(f" {_w3_tag(role)}")
    grade = str(r.get("ladder_grade") or "").strip()
    if grade:
        _health = r.get("ladder_health")
        _plain = {"A": "成型", "B": "较全", "C": "勉强", "D": "不成型"}.get(grade, "")
        _g = f"梯队 {grade}"
        if _plain:
            _g += f"·{_plain}"
        if isinstance(_health, (int, float)):
            _g += f" {_health:.1f}/10"
        # ⛔ 梯队是 W2：A/B 算好（橙），C/D 灰
        p2.append(f" {_w2_tag(_g, ok=grade in ('A', 'B'))}")
        if r.get("ladder_has_complete") is False:
            p2.append(f" <text_tag color='{C_HINT}'>没有龙二龙三</text_tag>")
    _line("身份", p2)

    # ── 量能：竞价额(W1) / 昨日成交(W3) / 换手(W3) / 封单占比(W2) / 炸板(W2) ──
    p3: list[str] = []
    _amt = oc.get("auction_amount_yi")
    _need = oc.get("auction_amount_need_yi")
    if isinstance(_amt, (int, float)) and isinstance(_need, (int, float)):
        _ok = _amt >= _need
        _v = "够" if _ok else f"差 {_need - _amt:.2f} 亿"
        # ⛔ W1 且排第一：9:25 判断能不能买，看的就是这一项
        p3.append(f" {_w1_tag(f'竞价额 {_amt:.2f}/{_need:.2f} 亿·{_v}', ok=_ok)}")
    elif isinstance(_amt, (int, float)):
        p3.append(f" {_w3_tag(f'竞价额 {_amt:.2f} 亿')}")
    cje = r.get("cje_yi")
    if isinstance(cje, (int, float)):
        p3.append(f" {_w3_tag(f'昨日成交 {cje:.2f} 亿')}")
    hs = r.get("hs")
    if isinstance(hs, (int, float)):
        p3.append(f" {_w3_tag(f'换手 {hs:.2f}%')}")
    seal = r.get("seal_amt")
    seal_r = r.get("seal_ratio_pct")
    if isinstance(seal, (int, float)):
        _sl = f"封单 {seal:.2f} 亿"
        _ok2 = False
        if isinstance(seal_r, (int, float)):
            _sl += f"·占 {seal_r:.2f}%"
            # ⛔ 占流通 ≥2% 算封得厚（薄封单＜1% 一炸就没）
            _ok2 = seal_r >= 2.0
        p3.append(f" {_w2_tag(_sl, ok=_ok2)}")
    zbc = r.get("zbc")
    if isinstance(zbc, (int, float)):
        # ⛔ 炸板 0 次算干净（W2 橙），炸过就是灰
        p3.append(f" {_w2_tag(f'炸板 {int(zbc)} 次', ok=int(zbc) == 0)}")
    _line("量能", p3)

    # ── 带动：带起几家(W1) / 封板时间(W3) / 主力资金(W2) / 流通(W3) ──────
    p4: list[str] = []
    carry = r.get("carry_cnt")
    if isinstance(carry, (int, float)) and carry:
        # ⛔ W1：带动 ≥3 家算真带动（只带 1~2 家是跟着涨，不是领涨）
        p4.append(f" {_w1_tag(f'带起 {int(carry)} 家', ok=carry >= 3)}")
    fbt = str(r.get("fbt") or "").strip()
    if len(fbt) >= 4 and fbt.isdigit():
        p4.append(f" {_w3_tag(f'{fbt[:2]}:{fbt[2:4]} 封板')}")
    flow = r.get("main_flow_yi")
    if isinstance(flow, (int, float)) and flow:
        # ⛔ W2：净流入为正算资金站在这边
        p4.append(f" {_w2_tag(f'主力净流入 {flow:+.2f} 亿', ok=flow > 0)}")
    lt = r.get("lt_mktcap")
    if isinstance(lt, (int, float)) and lt:
        p4.append(f" {_w3_tag(f'流通 {lt:.1f} 亿')}")
    _line("带动", p4)

    # ── 底子：股性档（W3 参考）────────────────────────────────────────────
    _gene = _hist_gene_line(r)
    if _gene:
        import re as _re
        _g = _re.sub(r"<[^>]+>", "", _gene).replace("📈", "").strip()
        for _pre in ("历史基因", "历史股性"):
            if _g.startswith(_pre):
                _g = _g[len(_pre):]
        _g = _g.strip(" ：｜")
        if _g.startswith("（") and "）" in _g:
            _g = "股性" + _g
        if _g:
            out.append(_vd_tag("底子") + f" {_w3_tag(_g)}")
    return out


def _ai_review_block(rev: dict[str, Any] | None) -> list[dict]:
    """🧠 09:26 池内复核（LLM 层 · 2026-11-13 接入主链）。

    与 `_ai_plan_block` 的分工（⛔ 不重复同一件事）：
    - `_ai_plan_block`：**机械裁决**结果（`pool_call` 17 道闸在池内判生死）+ 账本口径；
    - 本块：模型在**同一批池内票**上复核，只能降不能升（`core/ai_review` 强制）。

    渲染原则：结论优先、只带关键数字，最多 4 行。
    ⛔ 无数据（模块关闭 / 超时 / 池内 0 只）→ 整块不出现，
      ⛔ 不写「AI 未就绪」之类占位（占位等于告诉读者系统坏了，比不显示更差）。
    """
    if not isinstance(rev, dict):
        return []
    picks = [p for p in (rev.get("picks") or []) if isinstance(p, dict)]
    market = str(rev.get("market") or "").strip()
    if not picks and not market:
        return []
    buys = [p for p in picks if p.get("action") == "buy"]
    skips = [p for p in picks if p.get("action") == "skip"]
    watches = [p for p in picks if p.get("action") == "watch"]
    verdict = str(rev.get("verdict") or (f"可买 {len(buys)} 只" if buys else "空仓"))
    head = ("**🧠 AI 复核**｜" +
            (f"<text_tag color='red'>{verdict}</text_tag>" if buys
             else f"<text_tag color='neutral'>{verdict}</text_tag>"))
    out = [_div(head)]
    if market:
        out.append(_div(f"　<font color='grey'>{market}</font>"))
    for p in buys[:2]:
        why = f" · {p['why']}" if str(p.get("why") or "").strip() else ""
        out.append(_div(
            f"　🔥 <text_tag color='red'>{p.get('name') or p.get('code')}</text_tag>{why}"))
    if skips:
        names = "、".join(str(p.get("name") or p.get("code")) for p in skips[:3])
        tail = f" 等 {len(skips)} 只" if len(skips) > 3 else ""
        out.append(_div(f"　<font color='grey'>放弃：{names}{tail}</font>"))
    if watches and not buys:
        out.append(_div(f"　<font color='grey'>其余 {len(watches)} 只先看着，不动手</font>"))
    return out


def _ai_plan_block(raw: dict, records: list[dict],
                   pool_summary: dict | None = None,
                   ice_ban: bool = False) -> list[dict]:
    """🤖 今日买入（**9:25 竞价落地后定**）＋ AI 独立成绩。

    ⏱️ 两个时点（2026-09-21 用户「接力池＝提前缩范围，不是提前拍板」）：
      · **昨日预案**（上一交易日盘后写·`cache/ai_view_<今日>.json`）：攻守基调 + 方向 + 逐票
        `bias`（重点看 / 一般 / 回避）——⛔ **不含"买哪只"**。
      · **今日实判**（今日 09:25 竞价落地后·`core/pool_call` 在池内判命中）：买哪只在**今天**
        才定，结论随 record 的 `open_check.ai_view` 上来（与账本/结算同源）。
    ⛔ 时点词：实判段一律写「今日」，预案段一律写「昨日」；note 由昨日撰写 → `_shift_day_words` 平移。
    ⛔ 数据源优先级：record 的 9:25 结论 → 无（旧数据 / 开关回退）时退回旧口径（读 views 终态）。
    """
    views = raw.get("views") or {}
    _by_code: dict[str, dict] = {str(r.get("code") or ""): r for r in records}
    # 走 9:25 口径的判据：summary 有池内结论，或任一 record 带 9:25 实判理由
    _has_pool = bool((pool_summary or {}).get("participate") is not None) or any(
        _oc_of(r).get("pool_reason") for r in records)
    if not views and not _has_pool:
        return []
    dd = str(raw.get("decision_date") or "")
    mmdd = dd[5:] if len(dd) >= 10 else dd
    stance_label = (str(raw.get("stance_label") or "")
                    or _AI_STANCE_FALLBACK.get(str(raw.get("stance") or ""), ""))
    # 🚫 2026-11-06 用户「盘后预案全系统关闭」：`pool_call` 已不再消费预案的 stance/bias。
    #    卡面若继续写「昨日预案：防守」，读者会以为它还是今天的名额依据 → **必须停显**。
    #    判据用裁决自带留痕 `meta.stance_source.eod_plan`，⛔ 不读环境变量（防卡与裁决不同源）。
    _stance_src = (pool_summary or {}).get("stance_source")
    _plan_live = not (isinstance(_stance_src, dict)
                      and _stance_src and _stance_src.get("eod_plan") is False)
    if not _plan_live:
        stance_label = ""
    direction = str(raw.get("direction") or "")
    note = str(raw.get("note") or "").strip()

    blocks: list[dict] = []
    if _has_pool:
        # 🎯 口径同源（2026-11-04）：显示**最终生效名额**（含名额放宽到 2 的结果），
        #    ⛔ 不读原 stance 名额——否则出现「卡说 2 只、账本/裁决只有 1 只」的三方打架。
        _q = (pool_summary or {}).get("effective_quota")
        if _q is None:
            _q = (pool_summary or {}).get("quota")
        _n = (pool_summary or {}).get("pool_size")
        blocks.append(_div(
            # ⛔ 2026-11-16 修：盘后预案已关闭（`POOL_CALL_EOD_PLAN=0`）时，
            #    「昨天只出预案」是**不存在的事实**（11-15 无 ai_view 文件），
            #    会让人以为昨天给过买入预案。按 `_plan_live` 分两种措辞。
            ("**🤖 今日买入**<font color='grey'>（9:25 竞价开出来后定的；昨天只出预案）</font>"
             if _plan_live else
             "**🤖 今日买入**<font color='grey'>（9:25 竞价开出来后定的；盘后不预设买哪只）</font>")
            if not ice_ban else
            "**🤖 今日命中**<font color='grey'>（今日冰点·一律禁买，只记录不进场）</font>"))
        head = []
        if stance_label:
            head.append(f"昨日预案：{stance_label}")
        if direction:
            head.append(f"方向：{direction}")
        if head:
            blocks.append(_div("｜".join(head)))
        # 🪶 档位 2：昨日预案 note 不再整段搬（150 字）——「在什么位置」🌊 已说、
        #    「今天几度」📡 已说、「买几只」本段标题已说。⛔ 不丢信息：方向行仍在上两行。
        #    🚫 预案关闭时（`_plan_live=False`）note 也一并停显：它是盘后预案的正文，
        #       继续上卡会被读成"今天还是这么看"（时点口径铁律）。
        if note and _plan_live and not _two_line_rows():
            blocks.append(_div(f"<font color='grey'>{_shift_day_words(note)}</font>"))
        if isinstance(_n, (int, float)):
            _slot_h = (pool_summary or {}).get("slot_policy") or {}
            _even_n_h = _slot_h.get("allocation_count")
            _even_cap_h = _slot_h.get("even_cap")
            # ⛔ 2026-11-18：机械口径行必须服从**最终裁决**。
            #    11-17 事故：同一张卡上并联「竞价定下买 1 只·每只 50%」与
            #    「今天池内没有一只够条件买（AI 空仓）」——读者不知道该信哪句。
            #    现在只要最终 buys 为空，这里就不再喊「买 N 只」。
            _buys_now = [r for r in records if str(_oc_of(r).get("ai_view") or "") == "participate"]
            # 计划层参与票（pool_call.meta.participate）也算「有结论」——
            # 竞价卡渲染时 records 可能还没带上逐票 view，不能因此误判成空仓。
            # ⚠️ `participate` 在这套 payload 里**两种形态都出现过**：名单（list）与数量（int）。
            _p_now = (pool_summary or {}).get("participate")
            if isinstance(_p_now, (int, float)):
                _planned_now = [1] if _p_now > 0 else []
            elif isinstance(_p_now, (list, tuple, set)):
                _planned_now = [str(c) for c in _p_now]
            else:
                _planned_now = []
            if not _buys_now and not _planned_now:
                blocks.append(_div(
                    f"　　<font color='grey'>今天池内 {int(_n)} 只，开盘竞价定下："
                    f"<b>不买</b>（逐票原因见下）</font>"))
                _slot_h = {}
                _even_n_h = None
                _even_cap_h = None
            if ice_ban:
                # 冰点：⛔ 不写「买几只」——它和卡头「不买·空仓」直接对撞（2026-11-09 的事故）
                blocks.append(_div(
                    f"　　<font color='grey'>今天池内 {int(_n)} 只，今日冰点·全部不进场</font>"))
            elif (_slot_h.get("unlimited_slots") and isinstance(_even_n_h, int)
                    and isinstance(_even_cap_h, (int, float)) and _even_cap_h > 0):
                blocks.append(_div(
                    f"　　<font color='grey'>今天池内 {int(_n)} 只，竞价定下买 "
                    f"{_even_n_h} 只 · 每只 {float(_even_cap_h) * 100:.0f}%</font>"))
            else:
                blocks.append(_div(
                    f"　　<font color='grey'>今天池内 {int(_n)} 只，按开盘竞价实况定下买 "
                    f"{0 if _q is None else int(_q)} 只以内</font>"))
        # 买入名单 = 9:25 实判 participate（= 账本口径）
        buys = [r for r in records if str(_oc_of(r).get("ai_view") or "") == "participate"]
        others = [str(r.get("name") or r.get("code") or "") for r in records
                  if str(_oc_of(r).get("ai_view") or "") != "participate"]
    else:
        # ── 旧口径回退（无 9:25 结论：历史数据 / POOL_CALL_ENABLED=False）──────────
        # ⛔ 2026-09-22「彻底不派」：盘后只出预案（重点看/一般/回避），⛔ 不提前拍板「买哪只」。
        blocks.append(_div(
            f"**🤖 AI 昨日预案**<font color='grey'>（{mmdd} 盘后定 · 重点看/一般/回避 · 买哪只今日竞价定）</font>"))
        head = []
        if stance_label:
            head.append(f"AI 昨日判断：{stance_label}")
        if direction:
            head.append(f"方向：{direction}")
        if head:
            blocks.append(_div("｜".join(head)))
        if note:
            blocks.append(_div(f"<font color='grey'>{_shift_day_words(note)}</font>"))
        valve_codes = {str(r.get("code") or "") for r in records
                       if _oc_of(r).get("hibernate_valve")}
        buys = []
        others = []
        for _code, _v in views.items():
            _v = _v or {}
            _code = str(_code)
            # ⛔ 2026-09-22：旧口径回退时，买哪只由**预案档 bias=focus** 决定（view 不再派生）；
            #    真·历史数据（只有 view=participate）走 _is_focus 兜底。
            if _is_focus(_v) or _code in valve_codes:
                buys.append(_by_code.get(_code) or {"code": _code, "name": _v.get("name")})
            else:
                _r = _by_code.get(_code) or {}
                others.append(str(_r.get("name") or _v.get("name") or _code))

    _valve_codes = {str(r.get("code") or "") for r in records if _oc_of(r).get("hibernate_valve")}
    if _valve_codes and buys:
        blocks.append(_div("　　<font color='grey'>⚠️ 昨日收盘定的是空仓——只有下面这一只是今日竞价破例小仓试错"
                           "（它带了量、是方向里板数最高的）</font>"))
    if not buys:
        blocks.append(_div("<font color='grey'>今天池内没有一只够条件买（AI 空仓）</font>"
                           if _has_pool else
                           "<font color='grey'>昨日没有选出要买的（AI 自己空仓）</font>"))
    # 🎨 2026-10-21 用户「重点突出要买的」：买入票前后各加一条分隔线，
    #    让它在整卡里视觉上"独立成一区"，读者的眼睛直接落在这里。
    if buys:
        # ⛔ 用 `_hr()` 不用空 div：飞书 lint 要求 `div.text.content` 非空，
        #    放一个全角空格会被判 "content 须为非空字符串"（实测推送被阻断）。
        blocks.append(_hr())
    for _i, r in enumerate(buys):
        code = str(r.get("code") or "")
        oc = _oc_of(r)
        gap = oc.get("gap_pct")
        _valve = bool(oc.get("hibernate_valve"))
        # ⛔ 11-05 用户删 5% 线：relative 模式下 limit 改指「物理封死线」（涨停−0.2pct），
        #    文案不再说「高开5%以内/超5%」；absolute 回退时逐字保留旧文案。
        _rel = _ai_caliber.relative_mode()
        limit = (_ai_caliber.limit_up_pct(r.get("is_20cm")) - _ai_caliber.PHYSICAL_LIMIT_TOL
                 if _rel else USER_ENTRY_MAX_GAP_PCT)
        _no_tag = ("<text_tag color='{C_HINT}'>没买到</text_tag>（竞价封死买不进）"
                   if _rel else
                   f"<text_tag color='{C_HINT}'>没买到</text_tag>（高开超{limit:g}%）")
        # ⛔ 冰点日：同一只票在卡上只能有一个结论——「命中·禁买」，⛔ 不许再出现「AI 买入」
        #    （2026-11-09：标题写空仓、这一行写 AI 买入，用户直接问「这是谁推的」）
        _ok_tag = ("<text_tag color='red'>命中·禁买</text_tag>（今日冰点一律不进场）"
                   if ice_ban else
                   ("<text_tag color='{C_BUY}'>AI 买入</text_tag>（符合逐票预期）"
                    if _rel else
                    f"<text_tag color='{C_BUY}'>AI 买入</text_tag>（高开{limit:g}%以内）"))
        v = views.get(code) or {}
        # 🧾 替补声明（2026-11-11 用户：「其他票必须有独立成立依据，
        #    不能因为核心太贵就获得替代资格」）。⛔ 只标注，不改买入。
        #    ⚠️ 必须放在 `v` 赋值之后——此前放到前面，导致 UnboundLocalError。
        _sub = (v.get("substitute") or {}) if isinstance(v, dict) else {}
        if _sub:
            _ok_tag += (f"<font color='grey'>（这条线核心 {_sub.get('core_name') or ''} 今天没买"
                        f" → 它是替补；依据：{_sub.get('basis')}）</font>"
                        if _sub.get("independent") else
                        f"<font color='orange'>（⚠️ 替补：核心 {_sub.get('core_name') or ''} 今天没买，"
                        f"它本身不是这条线的龙一 —— 必须自己成立才买）</font>")
        # 🎨 2026-10-21 用户「主要把要买的这个推得花哨一点，指标打满」：
        #    买入票是全卡唯一用**红底**的票名（`C_BUY`），其余一律降权。
        name_txt = _hl(f"{r.get('name') or v.get('name') or code}({code})", C_BUY)
        if isinstance(gap, (int, float)):
            gap_txt = f"竞价{gap:+.2f}%"
            if _valve:
                _pc = int(round(_ai_caliber.HIBERNATE_VALVE_POSITION * 10))
                # ⛔ 同上：破例小仓也是 AI 的决定，标签必须带 AI
                tag = (f"<text_tag color='{C_BUY}'>AI 小仓试错</text_tag>"
                       f"<font color='grey'>（空仓日破例·只买 {_pc} 成）</font>"
                       if gap <= limit else _no_tag)
            else:
                # ⛔ 2026-10-21 22:29 用户「买入的这个票要写 AI 买入吧？我们现在是 AI 买入」：
                #    必须点明**是谁买的**——这是 AI 口径决定的，不是系统机械信号。
                #    与卡头「AI 买入信号 N」、段标题「🤖 今日买入」同一口径，⛔ 不写裸「买入」。
                tag = _ok_tag if gap <= limit else _no_tag
        else:
            gap_txt, tag = "竞价数据未到", "<font color='grey'>待竞价</font>"
        _core_tag = ""
        _bn = oc.get("backrow_note")
        if oc.get("line_is_core"):
            _core_tag = "<text_tag color='red'>这条线最高板</text_tag>"
        elif _bn:
            # 🚧 买的是「隔壁」（2026-10-21 92科比 口径）：前排今天买不到 → 只能买第二靠前那只。
            #   ⛔ 一句话讲完（不与上面的「最高板是 X·已断板」重复）。
            #
            # ⛔⛔ 2026-10-21 22:45 用户「这个推论好像有点不对吧」——**确实错了**：
            #   原文案一律写「前排 X 已断板 → 买的是隔壁·**它开板就走**」，
            #   但「已断板」和「它开板」是互斥的：
            #     新华传媒 11 板，10-16 最后涨停，10-20 已不在涨停池 → **断板已发生**。
            #     断板之后不会再"开板"——拿一个**不会发生的事件**当离场依据，读者照着做等于没有纪律。
            #   ✅ 拆成两种真实场景，各给对应的离场信号：
            #     · 核心**已断板**（`core_broken`）→ 这条线进入"断板后接力"阶段，
            #       唯一的离场依据是**自己**：跌破买入价 / 次日不高开就走。
            #       ⛔ 不许再提「核心开板」——它已经开过了。
            #     · 核心**今天还在板但买不到**（一字/超5%）→ 这才是真正的"买隔壁"，
            #       离场依据＝核心开板（用户 10-09 定的「开板即走」）。
            _lc = oc.get("line_core") or {}
            _lc_name = _lc.get("name") or "核心"
            if _bn == "core_broken":
                # ⛔ 措辞规范（2026-10-21 22:45 用户质疑后定）：
                #    ① ⛔ 不称它「前排」——那暗示"跟你同一梯队、是你的上一层"。
                #       实测南威(短剧·互动影游) 与 新华传媒(出版) 只靠「传媒」这个
                #       粗粒度线族归并到一起，业务不是一回事，读者会误以为同一条赛道。
                #    ② **必须给断板天数**：断 1 天（刚断、可能反包）和断 5 天（凉了）是两件事。
                #    ③ 离场依据回到**自己**身上——已断板的核心不会再"开板"。
                _bd = ""
                _ld = str(_lc.get("last_date") or "")
                # ⛔ `date10` 在 **record 顶层**（不是 open_check 里）——取错层级
                #    会让「已断 N 个交易日」静默消失（实测第一次就是这样）。
                _ref = str(r.get("date10") or r.get("trade_date10")
                           or oc.get("date10") or oc.get("trade_date10") or "")
                _n = _trading_days_between(_ld, _ref)
                if _n:
                    _bd = f"（{_ld[5:]} 最后涨停，已断 {_n} 个交易日）"
                _core_tag = (f"<font color='grey'>（这条线原来最高是 {_lc_name} "
                             f"{_lc.get('boards')} 板{_bd}——这是它断板后新冒出来的票，"
                             f"盯它自己：跌破买入价或明天不高开就走）</font>")
            else:
                _core_tag = (f"<font color='grey'>（{_lc_name} 今天还在板但买不到"
                             f" → 买的是隔壁·{_lc_name}一开板就走）</font>")
        elif isinstance(oc.get("line_core"), dict) and (oc["line_core"] or {}).get("name"):
            _lc = oc["line_core"]
            # ⛔ 2026-11-11 文案：`broken` 只表示「这条线上一波更高的核心已断」，
            #    **不表示今天在不在板**。写成「已断板」会被读成「今天也断了」
            #    （11-11 光洋 09:44 就封上 7 板，卡面却写它已断板）。
            # ⛔ 2026-11-11 用户：「不要说什么光洋断板这种错误的消息」。
            #    `in_pool=True` 说明它**今天就在涨停池里**，此时一个字都不该提「断」；
            #    只有 `in_pool` 明确为 False 时才提示「上一波已断」。
            # ⛔ 只认 `core_broken_self`（命名的这只自己断没断），⛔ 不用 `broken`——
            #    `broken` 是「上一波更高核心已断」，可能是**另一只票**的事，
            #    拿它写在这只票名字后面，就会出现「光洋·已断板」而光洋今天正封 7 板。
            _self_broken = _lc.get("core_broken_self")
            if _self_broken is None:            # 旧冻结件没有该字段 → 退回 in_pool 判断
                _self_broken = _lc.get("in_pool") is False and _lc.get("broken")
            _broken_txt = "·上一波已断（今天不在板）" if _self_broken else ""
            _core_tag = (f"<font color='grey'>（这条线最高板是 {_lc.get('name')}"
                         f"{_broken_txt}）</font>")
        blocks.append(_div(
            f"**{name_txt}{_boards_txt(r)}** {gap_txt} → {tag}{_core_tag}"))
        # 🎯 指标打满（2026-10-21 用户「指标打满」）：买入票是全卡唯一的行动项，
        #    读者要在这里一次性看到**所有能支撑下单的数字**，而不是往下翻找。
        #    ⛔ 只打「与下单有关」的：竞价额/需要额、预期区间+可信度、题材板块、历史股性、量价。
        #    ⛔ 不再重复下方分段已有的段（理由正文仍由 `_ai_view_line` 唯一渲染）。
        _full = _buy_indicator_lines(r, oc, v)
        for _ln in _full:
            blocks.append(_div(_ln))
        # ⛔ 理由去重（2026-09-15 用户「个股下的话术重复了」）：理由正文由下方个股段
        #    `_ai_view_line` 唯一渲染（9:25 实判理由 / 旧口径的昨日理由）——计划块再整段渲染
        #    即逐字重复。仅当**下方不会渲染**该票 AI 行时（无 record / 报价缺失段）才在此补出。
        _rendered_below = bool(r.get("code")) and oc.get("decision_status") != "unavailable"
        if not _rendered_below:
            _reason = str(oc.get("pool_reason") or v.get("reason") or "").strip()
            if _reason:
                blocks.append(_div(
                    f"　　<font color='grey'>{_reason}</font>" if _has_pool
                    else _narr_render(_reason, prev=True)))

    if buys:
        blocks.append(_hr())

    # 🐣 跟风闸：达标但没买（当日被"线核心"判断拦下）——必须显式点名，否则读者会问
    #    「它竞价明明达标，为什么没买」。⛔ 理由正文仍由下方个股段 `_ai_view_line` 渲染
    #    （禁两处重复），本块只给一眼名单。
    _gated = [r for r in records
              if _oc_of(r).get("follower_gate")
              and str(_oc_of(r).get("ai_view") or "") != "participate"]
    # 🧩 融合模式（2026-10-21 用户「机械量价参考 想办法 和AI判断融合在一起」）：
    #    🐣/🛡️ 两张点名名单**不再单列**——这些票在下方统一分段里各有一处完整展示，
    #    拦下原因已压成标题行胶囊（`🐣 跟风拦下` / `🛡️ 防守拦下`），理由在判定行。
    #    ⛔ 原写法让同一只票在卡上出现三次（点名名单 + 机械段 + 观察段），是最大冗杂源。
    if _gated and not _unify_mech():
        blocks.append(_div("<font color='grey'>🐣 竞价达标但没买（是跟风，不是这条线的核心）：</font>"))
        for r in (_gated[:5] if not _unify_mech() else []):
            oc = _oc_of(r)
            gap = oc.get("gap_pct")
            gtxt = f"竞价{gap:+.2f}%" if isinstance(gap, (int, float)) else "竞价数据未到"
            _lc = oc.get("line_core") or {}
            blocks.append(_div(
                f"　　{_hl(str(r.get('name') or '') + '(' + str(r.get('code') or '') + ')', 'yellow')}"
                f"{_boards_txt(r)} {gtxt}"
                + (f"<font color='grey'>（最高板是 {_lc.get('name')}）</font>"
                   if _lc.get("name") else "")))

    # 🛡️ 前置防守闸门（2026-10-08 回撤经验）：竞价明明达标、却被拦下没买——必须点名，
    #   否则读者会问「它竞价达标了，为什么没买」。⛔ 理由正文仍由个股段 `_ai_view_line`
    #   渲染（`pool_reason`·禁两处重复），本块只给一眼名单 + 一句原因标签。
    _defended = [r for r in records
                 if _oc_of(r).get("defense_gate")
                 and str(_oc_of(r).get("ai_view") or "") != "participate"]
    if _defended and not _unify_mech():
        blocks.append(_div("<font color='grey'>🛡️ 竞价达标但没买（昨天这票 / 这条线就有问题）：</font>"))
        for r in (_defended[:5] if not _unify_mech() else []):
            oc = _oc_of(r)
            gap = oc.get("gap_pct")
            gtxt = f"竞价{gap:+.2f}%" if isinstance(gap, (int, float)) else "竞价数据未到"
            _why = _DEFENSE_GATE_PLAIN.get(str(oc.get("defense_gate") or ""), "有风险")
            blocks.append(_div(
                f"　　{_hl(str(r.get('name') or '') + '(' + str(r.get('code') or '') + ')', 'yellow')}"
                f"{_boards_txt(r)} {gtxt}"
                f"<font color='grey'>（{_why}）</font>"))

    if others:
        shown = "、".join([o for o in others if o][:8]) + ("等" if len(others) > 8 else "")
        # ⏱️ 时点（2026-09-18/21）：9:25 口径 = 名单是**今日**判出来的；旧口径 = 由**昨日**盘后定。
        if _short_others_list():
            # 🪶 档位 2：只报只数——名字在下方各段都有，这行只是索引（不再列第二遍）
            _tail = (f"其余 {len(others)} 只今日没有买入安排（逐票见下方分段）" if _has_pool
                     else f"其余 {len(others)} 只昨日没有安排买（逐票见下方分段）")
        else:
            _tail = (f"其余 {len(others)} 只今日没有买入安排：{shown}" if _has_pool
                     else f"其余 {len(others)} 只昨日没有安排买：{shown}")
        blocks.append(_div(f"　　<font color='grey'>{_tail}</font>"))

    # ⛔ 2026-10-21 修 bug：原写法 `st = None if _drop_ai_stats() else _load_ai_stats()`
    #    让 st 变成 None 后掉进 else 分支，**反而渲染出「还没开始记账（今天第一批）」**——
    #    想删却多显示一行废话（用户实测截图可见）。要删就整块不渲染。
    if _drop_ai_stats():
        pass
    else:
        st = _load_ai_stats()
        if st:
            avg_c = "red" if float(st["avg"] or 0) >= 0 else "green"
            blocks.append(_div(
                f"📈 AI 自己的成绩：记录 <text_tag color='blue'>{st['days']}</text_tag> 天"
                f"｜胜率 <text_tag color='blue'>{st['win_rate']}%</text_tag>"
                f"｜日均 <text_tag color='{avg_c}'>{float(st['avg']):+.2f}%</text_tag>"
                f"｜净值 <text_tag color='blue'>{st['nav']}</text_tag>"
            ))
        else:
            blocks.append(_div("<font color='grey'>📈 AI 自己的成绩：还没开始记账（今天第一批）</font>"))
        blocks.append(_hr())
    return blocks


# ── 盘后候选卡 ──────────────────────────────────────────

def _post_pool_blocks(payload: dict[str, Any], *, eod: bool = False) -> list[dict]:
    """P1/P2/P3 三块新增信息（2026-10-18 用户「按顺序处理」）。

    顺序：🧩 新方向首板（P2）→ 🔎 复盘兑现核对（P3）→ 📎 没进池的高度（P1）。
    ⛔ 全部为**观察信息**，不构成买入依据；P1 明确标「不可买」。
    ⛔ 数据缺失 → 整块不渲染（诚实缺席，不写"暂无"占位挤占信息密度）。
    """
    out: list[dict] = []

    # 🌊 指数形态（2026-10-19 用户「学习上海 7.10 聚会心得」）：先答"这种环境该挑哪种票"。
    # ⛔ 只解释打法，不作买入依据（买不买看 decision_outlook）；数据缺失整块不渲染。
    ms = payload.get("market_style") or {}
    if not eod and isinstance(ms, dict) and ms.get("style"):
        ev = ms.get("evidence") or {}
        idx_rows = [r for r in (ev.get("index") or []) if isinstance(r, dict) and r.get("name")]

        def _idx_short(nm: str) -> str:
            for _suf in ("指数", "成指", "指"):
                if nm.endswith(_suf) and len(nm) > len(_suf):
                    return nm[: -len(_suf)]
            return nm

        idx_txt = "／".join(
            f"{_idx_short(str(r['name']))} {float(r['pct']):+.1f}%" for r in idx_rows
        )
        amt = ev.get("amount_yi")
        amt_txt = f" · 成交{amt / 10000:.1f}万亿" if isinstance(amt, (int, float)) and amt > 0 else ""
        _label = str(ms.get("label") or "")
        _rule = str(ms.get("rule") or "")
        _trade = str(ms.get("trading_rule") or "")
        _color = {"attack": "red", "shift": "green"}.get(str(ms.get("style")), "grey")
        head = f"{_label}：{_rule}{amt_txt}" + (f"（{idx_txt}）" if idx_txt else "")
        out.append(_div("**🌊 今天的市场环境（指数说了算）**"))
        out.append(_div(f"　· <font color='{_color}'>{head}</font> → 明天{_trade}"))
        ice_note = ms.get("ice_note")
        if ice_note:
            # ⛔ 语义矛盾门禁：冰点档不许出现"可做"暗示，标注一律只看不买
            out.append(_div(f"　· <font color='green'>⚠️ {ice_note}</font>"))
        hint = ms.get("no_trend_hint")
        if hint:
            out.append(_div(f"　· <font color='grey'>{hint}</font>"))
        out.append(_hr())

    # 🧩 低位池（回退模式的第三桶）—— eod 卡上的逐票清单。
    # ⛔ 2026-11-11：新方向首板已从这里摘出去（它现在**真入池**，逐票清单会在接力池
    #    那段出现一次；这里再列一遍就是同票两处、且旧文案「只看不买」与行为不符）。
    fb = payload.get("new_direction_first_board") or []
    low_pool = payload.get("low_pool") or []
    if eod and low_pool:
        out.append(_div("**低位（逐票保留原资格）**"))
        seen: set[str] = set()
        for it in low_pool:
            code = str(it.get("code") or "")
            if code and code in seen:
                continue
            if code:
                seen.add(code)
            qualified = it.get("buyable") is True
            qualification = ("原低位池资格·与接力池共用名额·待竞价裁决" if qualified
                             else "只看不买·不占接力名额")
            theme = str(it.get("core_theme") or it.get("theme") or it.get("sector") or "")
            out.append(_div(f"　· {_stock_hl(it, 'neutral')} "
                            f"{it.get('boards') or 1}板｜{theme}｜{qualification}"))
            g = it.get("gene") or {}
            promo = g.get("promo_rate_hist")
            fpo = g.get("first_prem_open_hist")
            promo_txt = f"{float(promo):.0f}%" if promo is not None else "—"
            fpo_txt = f"{float(fpo):+.1f}%" if fpo is not None else "—"
            out.append(_div(f"　　成交{it.get('cje_yi')}亿｜近一年{g.get('zt_250')}次涨停｜"
                            f"晋级{promo_txt}｜历史最长{g.get('max_boards_hist') or 0}板｜"
                            f"首板次日高开{fpo_txt}"))
            for key in ("watch_reason", "dragon_note", "radar_why", "offpool_reason"):
                if it.get(key):
                    out.append(_div(f"　　{it[key]}"))
        out.append(_hr())

    # 🌱 新方向（2026-11-11 用户「这条线必须打开·优秀的票明早能交易」）
    #    只报**线级事实 + 入池了谁**：这些票已经在上面接力池逐票列过一遍了，
    #    再逐票列一遍就是同票两处。⛔ 文案必须与行为一致 —— 它们有买入资格
    #    （09:25 竞价裁决），不许再写「只看不买」。
    if fb:
        _nd_lines: dict[str, list[str]] = {}
        for _it in fb:
            _nd_lines.setdefault(str(_it.get("new_direction_line") or ""), []).append(
                str(_it.get("name") or ""))
        out.append(_div("**🌱 新方向（整条线零连板·已进池·明早竞价定）**"))
        for _line, _names in list(_nd_lines.items())[:2]:
            _line = _line or "新方向"     # 旧候选没有 new_direction_line → 不渲染空红字
            _m = next((x for x in fb
                       if str(x.get("new_direction_line") or "") == _line), {})
            _mem = _m.get("new_direction_line_members")
            _amt = _m.get("new_direction_line_amount_yi")
            _facts = []
            if _mem:
                _facts.append(f"{_mem}家全是首板")
            if isinstance(_amt, (int, float)):
                _facts.append(f"成交{_amt:.0f}亿")
            out.append(_div(f"　· <font color='red'>{_line}</font>"
                            f"<font color='grey'>｜{'·'.join(_facts)}</font>"))
            out.append(_div(f"　　<font color='grey'>入池：{'、'.join(_names)}"
                            f" —— 能不能接，看明早竞价</font>"))
        out.append(_hr())

    # 🐣 低位池（2026-10-27 用户「还不如推低位！」）：从雷达升级为**真可买**的第三桶。
    #    ⛔ 与 relay/watch **共用当日名额**（不额外加仓）→ 卡面必须写清「共用名额」，
    #      不能让人误以为是加仓额度。⛔ 板数最低 → 天然排在接力池后面。
    low_pool = payload.get("low_pool") or []
    _low_codes = {str(x.get("code") or "") for x in low_pool}
    if low_pool and not eod:
        out.append(_div("**🐣 低位池（1-2 板·可买·与接力池共用名额）**"))
        for it in low_pool[:3]:
            g = it.get("gene") or {}
            theme = str(it.get("theme") or "")
            theme_txt = f"｜{theme}" if theme else ""
            promo = g.get("promo_rate_hist")
            promo_txt = f"{float(promo):.0f}%" if promo is not None else "—"
            mb = g.get("max_boards_hist") or 0
            fpo = g.get("first_prem_open_hist")
            fpo_txt = f"{float(fpo):+.1f}%" if fpo is not None else "—"
            out.append(_div(
                f"　· {_stock_hl(it, 'red')} "
                f"<font color='grey'>{it.get('boards')}板{theme_txt}｜"
                f"成交{it.get('cje_yi')}亿｜近一年{g.get('zt_250')}次涨停·晋级{promo_txt}·"
                f"最长{mb}板·首板次日高开{fpo_txt}</font>"
            ))
        out.append(_div("　<font color='grey'>名额就这么几个，前排买得到就先做前排；"
                        "前排被环境/位置拦掉，这里才有机会轮到。</font>"))
        out.append(_hr())
    # 🎯 低位连板龙雷达（2026-10-22 用户「需要做，雷达叠加观察的意思」）
    #    治「大业股份这种票第 1-2 板没被看见」：首板/2 板成交额小 → 被进池门槛挡掉 →
    #    复盘说得头头是道、卡上却查无此票。此块把「低位 + 股性连板龙 + 还有量」单列出来，
    #    ⛔ 只展示、不占接力/观察名额、不进 09:25 池内裁决（每项 buyable=False）。
    #    ⚠️ 已被低位池收录的票不再重复列（2026-10-27：低池默认开，雷达通常自动清空）。
    radar = [r for r in (payload.get("gene_radar") or [])
             if str(r.get("code") or "") not in _low_codes]
    if radar:
        out.append(_div("**🎯 低位连板龙雷达（1-2 板·只看不买·不占名额）**"))
        for it in radar[:6]:
            g = it.get("gene") or {}
            theme = str(it.get("theme") or "")
            theme_txt = f"｜{theme}" if theme else ""
            promo = g.get("promo_rate_hist")
            promo_txt = f"{float(promo):.0f}%" if promo is not None else "—"
            mb = g.get("max_boards_hist") or 0
            fpo = g.get("first_prem_open_hist")
            fpo_txt = f"{float(fpo):+.1f}%" if fpo is not None else "—"
            out.append(_div(
                f"　· {_stock_hl(it, 'red')} "
                f"<font color='grey'>{it.get('boards')}板{theme_txt}｜"
                f"成交{it.get('cje_yi')}亿｜近一年{g.get('zt_250')}次涨停·晋级{promo_txt}·"
                f"最长{mb}板·首板次日高开{fpo_txt}</font>"
            ))
            if it.get("radar_why"):
                out.append(_div(f"　　<font color='grey'>{it.get('radar_why')}</font>"))
        out.append(_hr())

    # 🔎 P3：复盘推演兑现核对
    rv = payload.get("review_directions") or {}
    if not eod and rv.get("available"):
        stats = rv.get("stats") or {}
        rv_date = str(rv.get("review_date") or "")
        mmdd = rv_date[5:] if len(rv_date) >= 10 else rv_date
        hits = [h for h in (stats.get("hits") or []) if isinstance(h, dict)]
        missed = [m for m in (stats.get("missed") or []) if isinstance(m, dict)]

        def _names(kind: str, src: list[dict]) -> list[str]:
            return [str(x.get("name") or "") for x in src
                    if x.get("kind") == kind and str(x.get("name") or "")]

        lines: list[str] = []
        for kind, label in (("height", "高度"), ("new_direction", "新方向"), ("caution", "警示")):
            ok_names = _names(kind, hits)
            no_names = _names(kind, missed)
            if not ok_names and not no_names:
                continue
            seg: list[str] = []
            if kind == "caution":
                # 警示票语义相反：今天还在涨停 = 风险仍在（红）；没涨停 = 风险缓解（绿）
                if ok_names:
                    seg.append(f"<font color='red'>{'、'.join(ok_names)} 今天还在涨停·留意停牌核查</font>")
                if no_names:
                    seg.append(f"<font color='green'>{'、'.join(no_names)} 今天没涨停·风险缓解</font>")
            else:
                if ok_names:
                    seg.append(f"<font color='red'>{'、'.join(ok_names)} 今日涨停</font>")
                if no_names:
                    seg.append(f"<font color='grey'>{'、'.join(no_names)} 今日没涨停</font>")
            lines.append(f"　· {label}：" + " ／ ".join(seg))
        if lines:
            out.append(_div(f"**🔎 复盘说的（{mmdd}）今天对上了没**"))
            out.extend(_div(x) for x in lines)
            out.append(_hr())

    # 📎 P1：成交额不够没进池、但连板高度还在的票
    off = payload.get("offpool_high_ident") or []
    if off:
        out.append(_div("**📎 成交额不够没进池（高度还在·不可买）**"))
        for it in off[:8]:
            sec = str(it.get("core_theme") or it.get("sector") or "")
            sec_txt = f"｜{sec}" if sec else ""
            out.append(_div(
                f"　· {_stock_hl(it, 'grey')} "
                f"<font color='grey'>{it.get('boards')}板{sec_txt}｜"
                f"{it.get('offpool_reason') or ''}</font>"
            ))
        out.append(_hr())
    return out


def _reason_line(it: dict) -> str:
    """🏷 涨停原因行（2026-11-16 用户「所有涨停票都走同花顺+涨停原因」）。

    ⛔ 这一行在 11-16 晚写好、但字段挂在**零调用点的死函数**里 → 11-17 之前一直没上卡。
       2026-11-17 用户「最新的改动覆盖原来的逻辑」：字段已搬到主链，卡面同步接上。
    ⛔ 只显示事实（同花顺给的归因原文），不解读、不推结论。
    """
    _r = str((it or {}).get("limit_up_reason") or "").strip()
    if not _r:
        return ""
    if len(_r) > 42:                 # 卡面字数纪律：超长截断，全文留在 JSON 里
        _r = _r[:41] + "…"
    return f"<font color='grey'>📰 {_r}</font>"


def _score_badge(it: dict) -> str:
    """🧮 接力终评分（2026-11-18）：盘后基础分 × 竞价系数 → 分 + 档位。

    ⛔ 只展示，不改任何判据；缺字段就不写（旧候选文件零感知）。
    """
    sc = (it or {}).get("relay_score")
    if not isinstance(sc, dict):
        return ""
    try:
        final = float(sc.get("final"))
    except (TypeError, ValueError):
        return ""
    grade = str(sc.get("grade") or "")
    color = {"A": "red", "B": "orange", "C": "grey", "D": "grey"}.get(grade, "grey")
    tail = f"（{grade} 档）" if grade else ""
    return f"<font color='{color}'>🧮 {final:.0f} 分{tail}</font>"


def _resonance_line(it: dict) -> str:
    """🧲 收盘共振分（2026-11-17 用户「补一个共振得分」）。

    数据来自 pipeline_eod 主链写入的 `close_resonance`（成线/高度/早封各 1 分）。
    ⛔ 与 09:25 竞价共振不是一回事：这里说的是**今天收盘就能看到的事实**，
       不预测明早；也不参与排名，只让读者知道「这条线是真线还是独苗」。
    """
    cr = (it or {}).get("close_resonance")
    if not isinstance(cr, dict):
        return ""
    try:
        score = int(cr.get("score"))
    except (TypeError, ValueError):
        return ""
    line = str(cr.get("line") or "").strip()
    mates = cr.get("mates")
    hb = cr.get("max_boards")
    color = "red" if score >= 3 else ("orange" if score == 2 else "grey")
    tail = f"（{mates} 家·最高 {hb} 板）" if mates is not None and hb is not None else ""
    return (f"<font color='{color}'>🧲 {line or '题材'} 共振 {score}/3{tail}</font>")


def _rfc_line(it: dict) -> tuple[str, str]:
    """选股卡逐票「明早怎么开」一行 → (建议档, 文本)。失败/无数据 → ("", "")。

    ⛔ 只读候选里已挂好的 `forecast` 字段（盘后 pipeline_eod 写入），此处不重算模型，
       避免卡面与落盘口径两套数字。
    """
    # 预测研究字段不再渲染为行动三档或可成交性判断。
    return "", ""


def _eod_brief_comment(narrative) -> str:
    """盘后只保留简短点评与风险，不展示历史股性统计。"""
    parts = []
    for label, body in _narr_segments(narrative, drop_labels=("历史股性", "账户战绩")):
        # 旧预案的历史统计可能混在无标签正文中，按句移除而不删风险段。
        sentences = _re.split(r"(?<=[。；;\n])", str(body or ""))
        text = "".join(s for s in sentences if not any(
            word in s for word in ("近一年", "历史上", "胜率", "晋级率", "涨停后第二天", "净值", "回撤", "已记录")))
        if text.strip():
            parts.append(f"{label}：{text.strip()}" if label else text.strip())
    return _narr_render("\n".join(parts))


def build_eod_card(payload: dict[str, Any]) -> dict:
    """
    payload = pipeline_eod.run_eod() 的输出（含 relay / watch / diagnostics / regime）。
    标题规范：显式携带日期 + 定调。
    """
    # 2026-11-10 晚：单池模式下原观察票仍走 09:25 完整裁决（ALL_TRADEABLE=1），
    # 卡面不能再一律按「只看不买」渲染；唯一例外＝高度观察 / 一字封死 / 全池可交易关着。
    from core.two_pool import all_tradeable_enabled, height_observation, observation_only
    hhmm = _now_bj().strftime("%H:%M")
    date = str(payload.get("date") or "")
    mmdd = date[5:] if len(date) >= 10 else date
    regime = payload.get("regime")
    relay = payload.get("relay") or []
    watch = payload.get("watch") or []
    # 只读已有预案；显式空对象禁止回退读盘，不把池外旧计划渲染上卡。
    raw_plan = (payload.get("ai_plan") if "ai_plan" in payload
                else _load_ai_view_raw(_next_trade_day_inline(date))) or {}
    plan_views = raw_plan.get("views") or {}
    pool_codes = {str(it.get("code") or "") for it in relay + watch}
    outside_terms = {code for code in plan_views if code not in pool_codes}
    outside_terms.update(str(v.get("name")) for code, v in plan_views.items()
                         if code not in pool_codes and v.get("name"))
    for key in ("low_pool", "new_direction_first_board", "offpool_high_ident"):
        for row in payload.get(key) or []:
            if str(row.get("code") or "") not in pool_codes:
                outside_terms.update(str(row[k]) for k in ("code", "name") if row.get(k))

    def pool_only_text(text):
        if not isinstance(text, str):
            return text
        return "".join(sentence for sentence in _re.split(r"(?<=[。；;\n])", text)
                       if not any(term in sentence for term in outside_terms)
                       and not any(code not in pool_codes for code in
                                   _re.findall(r"(?<!\d)\d{6}(?!\d)", sentence)))

    def _obs_badge(it) -> str:
        """只看不买的票在行内补一句「为什么买不进」。

        ⛔ 2026-11-17 用户「只有接力池，没有其他池」后，原来的分组标题
        （「👀 高度观察·全市场唯一高度（只看不买）」／「一字封死·买不进」）随分组一起消失。
        风控事实不能跟着标题一起丢——否则读者会以为这只也能买。
        """
        if height_observation(it):
            return " <text_tag color='grey'>全市场唯一高度·只看不买</text_tag>"
        if it.get("is_one_word"):
            return " <text_tag color='grey'>一字封死·买不进</text_tag>"
        return ""

    def plan_marker(it, *, observation=False):
        view = plan_views.get(str(it.get("code") or "")) or {}
        bias = _bias_of(view)
        if bias == "focus":
            label = "明天重点参照·不可买" if observation else "明天重点看·待竞价核验"
            return f" <text_tag color='purple'>{label}</text_tag>"
        if bias == "avoid":
            return " <text_tag color='green'>预案回避</text_tag>"
        return ""

    # 全卡候选档位是否统一：统一则抑制档位胶囊（如全员「重点候选」→ 不重复显示，用户2026-08-13定）
    _band_vals = [(c.get("dim_panel") or {}).get("_band") for c in (relay + watch)]
    _nonempty = [b for b in _band_vals if b]
    uniform_band = bool(_nonempty) and len(set(_nonempty)) == 1 and len(_nonempty) == len(_band_vals)

    title = f"📊 财富密码·盘后候选·全量 {mmdd} | {_regime_label(regime)}"
    blocks: list[dict] = []

    snap = _load_zt_snapshot()
    zt_count = (snap or {}).get("zt_count", "—")
    max_boards = (snap or {}).get("max_boards", "—")
    blocks.append(_div(
        f"**📊 财富密码·盘后候选** | 涨停{zt_count}只 最高{max_boards}板 | "
        f"{_regime_label(regime)}"
    ))
    # 盘后只展示候选与预案；环境和周期仍由原决策链消费，竞价卡不变。
    # 数据源诚实标注（两者相结合：必盈主池优先，缺失降级选股宝）
    ds = payload.get("data_source") or "xuangubao"
    if ds == "xuangubao":
        blocks.append(_div(
            "<font color='grey'>数据源：选股宝涨停池（备用通道）</font>"
        ))
    else:
        blocks.append(_div("<font color='grey'>数据源：涨停池</font>"))
    blocks.append(_hr())

    # 🧭 市场级题材强度（2026-11-11 用户拍板 B）：先回答「昨天整个市场哪几条线真强」，
    #    再进入逐票明细。⛔ 纯展示：不进判据、不改 relay_rank / 数组顺序；=0 整段不渲染。
    _mtr_ctx = (payload.get("diagnostics") or {}).get("market_theme_rank") or {}
    # ⛔ 全局开关也要过：payload 是盘后冻结件，重放时 env=0 必须能关掉整段渲染。
    if _mtr_ctx.get("enabled"):
        from core.market_theme_rank import enabled as _mtr_enabled
        _mtr_on = bool(_mtr_enabled())
    else:
        _mtr_on = False
    if _mtr_on:
        def _theme_brief(x):
            return (f"{x.get('theme')}({x.get('zt_count')}家·{x.get('max_boards')}板·"
                    f"最早{x.get('first_seal')})")
        _b3 = _mtr_ctx.get("breadth_top3") or []
        _t3 = _mtr_ctx.get("top3") or []
        blocks.append(_div(
            f"**🧭 昨日市场强题材** | 全市场{_mtr_ctx.get('zt_count', '—')}只涨停·"
            f"{_mtr_ctx.get('theme_count', '—')}条线 ｜ 📊 广度榜 "
            + "｜".join(_theme_brief(x) for x in _b3)))
        blocks.append(_div("🎯 高度·承接榜｜" + "｜".join(_theme_brief(x) for x in _t3)))
        _bd = next((x for x in _b3 if x.get("breadth_flag")), None)
        if _bd and _mtr_ctx.get("divergence"):
            blocks.append(_div(
                f"<font color='grey'>⚠️ {_bd.get('theme')}{_bd.get('zt_count')}家却"
                f"{_bd.get('max_boards')}板封顶＝铺广度，不算主线（广度不等于核心）；"
                f"接力池改按板块强度加封板、封单、带动排序，"
                f"买卖仍由明早09:25裁决</font>"))
        blocks.append(_hr())

    # 连板天梯（用户关切：能否拿到连板天梯 —— 答案：yesterday_zt.json 现成可用）
    ladder = (snap or {}).get("ladder") or {}
    if ladder:
        blocks.append(_div("**🔝 连板天梯**"))
        for k in sorted(ladder.keys(), key=lambda x: int(x), reverse=True):
            names = ladder[k]
            hl_names = "、".join(_hl(n, "red") for n in names)
            blocks.append(_div(f"  **{k}板**：{hl_names}"))
    if ladder:
        blocks.append(_hr())

    # 📋 在册连板段已于 2026-11-17 按用户「删除这个模块」整段移除（函数一并删除）。
    #   台账本身仍由 `scripts/board_track.py` 每日落盘供后台查，但**不再进卡**。

    # 🔎 单池标志（2026-11-17 用户「只有接力池，没有其他池」）：
    #    池子可能几十只 → **前 3 只详述、其余接在后面连续列**，避免每只两屏长文把卡撑爆。
    #    ⛔ 只影响展示；relay/watch 数组、菜单与 09:25 裁决完全不动。
    _single_pool = bool((payload.get("diagnostics") or {}).get("single_pool"))

    # 接力池（核心，全部标红）
    # 2026-09-09 用户「冰点也要推接力池·买不买由人决定」：ice 解冻产池后 relay 有货 →
    # 正常渲染 + 冰点档提示；relay 真为空 → 中性提示（选不出硬票·非系统禁买）。
    mode = (relay[0].get("relay_selection_mode") if relay else None)
    if mode == "freeze" or not relay:
        if mode == "freeze":          # 仅 ICE_FREEZE_RELAY=True 回退旧行为出现
            blocks.append(_div("**🔥 接力池（⛔市场很冷·只观察）**"))
        else:
            blocks.append(_div(f"**🔥 接力池 TOP{len(relay)}（核心候选）**"))
        if not relay:
            _empty_note = "（市场很冷·暂无硬票）" if regime == "ice" else ""
            from core.two_pool import enabled as _two_pool_enabled
            _empty_tail = (" 不符合接力条件的票不进池" if _two_pool_enabled()
                           else " 全部转观察池")
            blocks.append(_div(f"　　<text_tag color='red'>⛔ 今日无接力候选{_empty_note}</text_tag>{_empty_tail}"))
            # 🧊 冰点冻结（2026-10-28 用户「冰点日若开盘后情绪转好 可以出手」）：
            #    relay 空 ≠ 没有票在等。只差「情绪」这一条市场级条件的票被冷冻，
            #    明早 09:25 竞价转强即解冻放行（个股级硬伤不会被解冻）。
            #    ⛔ 只报**状态 + 解冻条件**，不展开理由（用户 2026-10-27「不用给」）。
            from core.two_pool import observation_only
            _fz = [it for it in (watch or []) if it.get("frozen") and not observation_only(it)]
            if _fz:
                _fz_names = "、".join(_hl(str(it.get("name") or ""), "neutral") for it in _fz[:6])
                _fz_more = f" 等 {len(_fz)} 只" if len(_fz) > 6 else ""
                # ⛔ 标签禁嵌套（宪法 ⓪-10）：`_hl()` 产出 `<text_tag>`，
                #    不能被 `<font>` 包住（外层吞内层 → 整行退化纯文本 / lint 硬阻断）。
                #    故票名标签在前、灰色说明文字单独包 font。
                blocks.append(_div(
                    f"　　🧊 冰点冻结 {_fz_names}{_fz_more}"
                    f"<font color='grey'> —— 只差市场情绪这一条，明早 09:25 竞价转强才解冻</font>"))
    else:
        _ice_flag = " ｜ ⚠️市场很冷·候选仅供参考·买不买自决" if regime == "ice" else ""
        # 🎯 2026-11-10 晚：不再有「接力池 / 观察池」两套 —— 候选只有**一个接力池**。
        # ⛔ 标题里的数字必须是池子总只数（含下面的接续列表），否则「TOP3」会骗人。
        #    2026-11-17 单池：原 watch 里的票也在这个池里一起列 → 计数必须并进来，
        #    否则标题写 5 只、下面列 6 只（用户看到的第一个不一致就从这里开始）。
        _total = len(relay) + (len(watch) if _single_pool else 0)
        _sort_note = " ｜ 按昨日板块强度排序·编号不等于买入顺序" if _mtr_on else ""
        blocks.append(_div(f"**🔥 接力池（{_total} 只）**{_relay_mode_note(mode)}{_ice_flag}{_sort_note}"))
        # 🚮 2026-11-11 用户「垃圾票不该进池」：资格线剔掉的票只报数量+原因分布，
        #    ⛔ 不列名单（列了就等于又推一遍）、不给任何买入暗示。开关同 `POOL_QUALIFIED_ONLY`。
        _unq = (payload.get("diagnostics") or {}).get("unqualified_dropped") or []
        if _unq:
            _cnt: dict[str, int] = {}
            for _it in _unq:
                _g = str(_it.get("watch_group") or "").strip()
                _key = ("一字买不到" if "一字" in _g else
                        "容量/封单太薄" if ("容量" in _g or "封单" in _g) else
                        "成交额或成线不达标" if "过筛" in _g else
                        "高标只作参照" if "高标" in _g else
                        "历史连板没确认")
                _cnt[_key] = _cnt.get(_key, 0) + 1
            _parts = " · ".join(f"{k} {v}" for k, v in sorted(_cnt.items(), key=lambda x: -x[1]))
            blocks.append(_div(f"　　🚮 <font color='grey'>另有 {len(_unq)} 只未获接力资格，不进池"
                               f"（{_parts}）</font>"))
    # 🎯 晋级三问汇总（2026-10-30 用户「优化推送高胜率可晋级板块地位高的个股」）：
    #   只出现一次、放在接力池标题之下——**不是逐票灰字**（用户明确不要灰字行）。
    #   作用：一句话回答「今天到底有没有值得重仓的票」，没有就直说没有。
    # 晋级统计仅供后台研究，盘后卡不再汇总展示。
    # 🔮 明早开盘推测（2026-11-02）：一句话答「明天值得等几只、几只根本买不到」。
    # ⛔ 只在接力池汇总一次（逐票重复＝挤占信息密度）；逐票明细在下面每只票的第二行给出。
    # 撤销明早可等/观察/放弃预测汇总；两池身份不由预测行动档定义。
    weak_flagged = 0
    # 同一市场结构提示只在接力池汇总一次：逐票重复会挤占信息密度，且无法增加决策信息。
    mid_carry_weak = 0
    relay_narr: list[str] = []

    def _render_as_observation(it: dict[str, Any], *, from_relay: bool = False) -> bool:
        """这行该不该按「只看不买」渲染 —— 只管显示层，不改任何资格/裁决。"""
        if height_observation(it) or it.get("is_one_word"):
            return True                                  # 高度观察 / 一字封死确实买不进
        if not _single_pool:
            return not from_relay                        # 旧双池：relay 详述可买、watch 只观察
        # 全池可交易关着时，原 observe_only 仍不可买；开着时统一由 09:25 判。
        return not all_tradeable_enabled() and observation_only(it)

    _RELAY_DETAIL_N = 3 if _single_pool else len(relay)
    # ⛔ 只排「展示顺序」：relay 数组与 relay_rank 逐字节不动（09:25 判据 / quota 顺序敏感）。
    if _mtr_on:
        from core.market_theme_rank import order_rows as _mtr_order_rows
        _relay_show = _mtr_order_rows(relay, _mtr_ctx)
        _watch_show = _mtr_order_rows(watch, _mtr_ctx)
    else:
        _relay_show, _watch_show = relay, watch
    _relay_head = _relay_show[:_RELAY_DETAIL_N]
    _relay_rest = _relay_show[_RELAY_DETAIL_N:]
    for _head_i, it in enumerate(_relay_head, start=1):
        exp = it.get("expectation") or {}
        rng = exp.get("expected_range") or ["—", "—"]
        # 日报 verdict（板块被判弱/观察级时记 weak_flagged；重要风险提示保留在独立行）
        dv = it.get("daily_verdict")
        if dv and ("avoid_breadth" in dv.get("flags", []) or "observe" in dv.get("flags", [])):
            weak_flagged += 1
        amt_txt = _amount_txt(it)
        conf = _conf_tag(it)
        cloud = _render_tag_cloud(_eod_candidate_tags(it, suppress_band=uniform_band))
        _head_observing = _render_as_observation(it, from_relay=True)
        # 新布局（2026-08-13）：行1=紫色小卡(名称+分数)+tag云紧跟；
        # 行2=现价｜预期%｜高开｜竞价额｜板块｜可信度（预期+高开同红，竞价额去昨基数）
        blocks.append(_div(
            f"<font color='red'>{_head_i if _single_pool else (it.get('theme_order') if _mtr_on else it.get('relay_rank'))}、</font> {_stock_hl(it, 'purple')}"
            + (f" {cloud}" if cloud else "") + plan_marker(it, observation=_head_observing)
            + (_obs_badge(it) if _head_observing else "")
        ))
        price = it.get("price")
        price_seg = f"现价{price:.2f}｜" if isinstance(price, (int, float)) else ""
        ai_tag = ""  # AI 竞价区间覆盖仍生效（data.expectation.ai_override），按用户要求不显示 🤖AI 标记
        if _head_observing:
            # 只看不买的票不挂交易预期（一字 / 高度观察 / 全池可交易关闭时的原观察票）。
            _facts = [f"现价{price:.2f}" if isinstance(price, (int, float)) else "",
                      _theme_dual_tag(it)]
            blocks.append(_div("　　" + "｜".join(f for f in _facts if f)))
        else:
            blocks.append(_div(
                f"　　<font color='grey'>{price_seg}</font>"
                f"<font color='red'>预期{rng[0]}~{rng[1]}%｜{_gap_strength_short(it)}｜</font>"
                f"<font color='grey'>{amt_txt}｜{_theme_dual_tag(it)}｜{conf}{ai_tag}</font>"
            ))
        _rl = _reason_line(it)
        if _rl:
            blocks.append(_div("　　" + _rl))
        _rln = _resonance_line(it)
        if _rln:
            blocks.append(_div("　　" + _rln))
        _scb = _score_badge(it)
        if _scb:
            blocks.append(_div("　　" + _scb))
        # 历史基因不在盘后卡展示，后台证据保持不动。
        # 🔮 明早怎么开（2026-11-02）：选股卡的核心新增——把「明天大概率开在几个点、
        #    几成概率买不到」提前说清。⛔ 不是买入指令；买不买由 09:25 竞价卡裁决。
        _fv, _ftxt = _rfc_line(it)
        if _ftxt and not _head_observing:
            # ⛔ 建议档必须**永远可见**：`C_SKIP=""` 意思是「不上色」，不是「不显示」——
            #    早期写法用 `if _fcolor` 兜底，会把「放弃」两个字直接吞掉（实测只渲染出半句）。
            _fcolor = (C_BUY if _fv == "可等" else C_WATCH if _fv == "只观察" else C_SKIP)
            if _fcolor:
                blocks.append(_div(f"　　<font color='grey'>🔮 {_ftxt}</font>"
                                   f" <text_tag color='{_fcolor}'>{_fv}</text_tag>"))
            else:
                blocks.append(_div(f"　　<font color='grey'>🔮 {_ftxt} → {_fv}</font>"))
        # 中间层偏弱是全市场结构信息，不对每一只候选重复展示。
        if it.get("dim_panel", {}).get("_mid_carry") == "不足":
            mid_carry_weak += 1
        # 反吃日报 verdict：板块被日报判弱时，加「仅龙头独立强势」注记（以日报判断为准）
        if dv and dv.get("tag"):
            blocks.append(_div(f"　　{dv.get('tag')} {dv.get('note')}"))
        # 🚪 隔壁（2026-11-04 用户 BBB）：龙一买不进 → 本线唯一入口，半仓、龙一开板即走。
        #    ⛔ 只写事实与执行条件，不写机制解释。
        if it.get("next_to_leader"):
            _ln = str(it.get("dragon_leader_name") or "龙一")
            _cap = it.get("position_cap")
            _cap_txt = ""
            if isinstance(_cap, (int, float)) and _cap > 0:
                _n100 = float(_cap) * 100
                _cap_txt = f"·上限{_n100:.0f}%" if _n100 >= 10 else f"·上限{_n100:.1f}%"
            blocks.append(_div(
                f"　　<font color='red'>🐉 {_ln}今天买不进 → 接隔壁{_cap_txt}｜{_ln}开板即走</font>"))
        # 点评收集到底部集中展示，不与个股混排（名称独占一行；点评按语义分段·2026-09-11）
        comment = _eod_brief_comment(pool_only_text(it.get("narrative") or
                                     (plan_views.get(str(it.get("code") or "")) or {}).get("reason")))
        if comment:
            blocks.append(_div(comment))
    if mid_carry_weak:
        blocks.append(_div(
            f"⚠️ 接力池 {mid_carry_weak} 只缺中间跟随票：等中间票跟上再考虑接力"
        ))
    if weak_flagged:
        blocks.append(_div(
            f"📋 其中 {weak_flagged} 只已被日报判板块弱/观察级，按纪律仅做龙头、不追板块"
        ))
    blocks.append(_hr())

    # 池内其余票 + 唯一高度观察（按 watch_group 分区展示）
    # ⛔ 2026-11-10 晚：这里原来是「👀 观察池 TOP39（仅作板块参照·不可买）」。
    #    ① 池子已合并 → 标题不再叫观察池；
    #    ② 「不可买」这句在 `POOL_CALL_ALL_TRADEABLE=1` 之后就是假的（观察票 09:25
    #       真能买，11-10 买的东方创业就来自观察池）→ 改成「能不能买由明早竞价判」。
    watch_narr: list[str] = []
    _rest_rows = [
        {**it, "watch_rank": (it.get("theme_order") if _mtr_on else it.get("relay_rank"))}
        for it in _relay_rest
    ]
    _render_watch = ((list(_rest_rows) + list(_watch_show or [])) if _single_pool
                     else (list(_watch_show or []) + _rest_rows))
    if _render_watch:
        if any(it.get("participation_eligibility") == "conditional" for it in _render_watch):
            watch_note = "条件资格须竞价核验"
        elif _single_pool:
            # 单池 + 全池可交易开：原观察票 09:25 会真判，不能再说「不可买」。
            watch_note = "同一池塘·买不买由明早竞价定"
        else:
            # ⛔ 旧双池回退态逐字保留，既有渲染契约锁着这句。
            watch_note = "仅作板块参照·不可买"
        # 🎯 2026-11-17 用户「只有接力池，没有其他池」：单池模式下这里只是**同一个接力池的
        #    接续部分**——不再起第二级池名、不按 watch_group 分组，编号接着详述区连续往下排。
        #    ⛔ 数据层（relay / watch / watch_group / participation_eligibility）一字节不动，
        #       09:25 裁决、名额、仓位全不受影响；只有回退双池时才渲染旧的「观察池 TOP」。
        groups: list[tuple[str, list[dict]]] = []
        if _single_pool:
            groups = [("", _render_watch)]
        else:
            blocks.append(_div(f"**观察池 TOP{len(watch)}（{watch_note}）**"))
            # 分组兜底：无 watch_group 字段（旧数据）按板数归入合理组
            for grp in _WATCH_GROUP_PLAIN:
                items = [
                    it for it in _render_watch
                    if (it.get("watch_group") or ("高标/题材核心" if it.get("boards", 1) >= 3 else "补充观察")) == grp
                ]
                if items:
                    groups.append((grp, items))
        _seq = len(_relay_head)
        for grp, items in groups:
            if grp:
                # 分组名白话化（2026-09-10 用户「不要推专业术语」）——仅显示层别名，数据键不动
                blocks.append(_div(f"  ▸ **{_WATCH_GROUP_PLAIN.get(grp, grp)}**"))
            for it in items:
                exp = it.get("expectation") or {}
                rng = exp.get("expected_range") or ["—", "—"]
                amt_txt = _amount_txt(it)
                conf = _conf_tag(it)
                cloud = _render_tag_cloud(_eod_candidate_tags(it, suppress_band=uniform_band))
                # 新布局（同接力池）：行1=黄色小卡(名称+分数)+tag云紧跟；
                # 行2=现价｜预期%｜高开｜竞价额｜板块｜可信度（预期+高开同红，竞价额去昨基数）
                _observing = _render_as_observation(it)
                # ⛔ 2026-11-13：板块核心观察等新分组没有 relay_rank/theme_order，
                #    原写法直出 → 卡面出现「None、」。没有编号就不编号，不拿 None 充数。
                if _single_pool:
                    # 单池：编号接着详述区连续往下，一个池只应该有一条号列。
                    # 缩进与配色也跟详述区一致 —— 读者不应该从排版里看出「里面还分了两块」。
                    _seq += 1
                    _wr = _seq
                else:
                    _wr = it.get("watch_rank")
                _pad = "" if _single_pool else "  "
                _rank_txt = f"<font color='red'>{_pad}{_wr}、</font> " if _wr not in (None, "") else _pad
                blocks.append(_div(
                    _rank_txt + _stock_hl(it, 'purple' if _single_pool else 'yellow')
                    + (f" {cloud}" if cloud else "") + plan_marker(it, observation=_observing)
                    + (_obs_badge(it) if _observing else "")
                ))
                price = it.get("price")
                price_seg = f"现价{price:.2f}｜" if isinstance(price, (int, float)) else ""
                if _observing:
                    # 真正只看不买的票不展示交易预期；缺失不以占位或零值充数。
                    facts = [f"现价{price:.2f}" if isinstance(price, (int, float)) else "",
                             _theme_dual_tag(it)]
                    blocks.append(_div("　　" + "｜".join(f for f in facts if f)))
                else:
                    blocks.append(_div(
                        f"　　<font color='grey'>{price_seg}</font>"
                        f"<font color='red'>预期{rng[0]}~{rng[1]}%｜{_gap_strength_short(it)}｜</font>"
                        f"<font color='grey'>{amt_txt}｜{_theme_dual_tag(it)}｜{conf}</font>"
                    ))
                _rl = _reason_line(it)
                if _rl:
                    blocks.append(_div("　　" + _rl))
                _rln = _resonance_line(it)
                if _rln:
                    blocks.append(_div("　　" + _rln))
                _scb = _score_badge(it)
                if _scb:
                    blocks.append(_div("　　" + _scb))
                # 不读取历史基因档案。
                # 2026-10-27：降级原因必须写出来（否则读者只看到"它掉进观察池"，不知道为啥）
                if it.get("participation_eligibility") == "conditional":
                    blocks.append(_div("　　条件资格：竞价核验全部风控后方可参与，非买入确认"))
                if it.get("watch_reason"):
                    reason = _pool_reason(it["watch_reason"], observing=_observing)
                    blocks.append(_div(f"　　<font color='grey'>{reason}</font>"))
                # 🐉 龙一闸降级原因（非龙一 / 龙一买不进）—— 同样是"为什么它不能买"
                if it.get("dragon_note") and not it.get("watch_reason"):
                    blocks.append(_div(f"　　<font color='grey'>{it['dragon_note']}</font>"))
                comment = _eod_brief_comment(pool_only_text(it.get("narrative") or
                                             (plan_views.get(str(it.get("code") or "")) or {}).get("reason")))
                if comment:
                    blocks.append(_div(comment))
        blocks.append(_hr())

    # P1/P2/P3（2026-10-18 用户「按顺序处理」）：观察池之后补三块——
    # 🧩 新方向首板（不占接力名额）→ 🔎 复盘兑现核对 → 📎 没进池的高度（不可买）。
    # 池外对象及旧低位入口不渲染。

    # 早鸟埋伏区：2026-08-18 用户决定「几乎没用」→ 从 eod 盘后卡移除（可回滚）。
    # 理由：①数据=盘中未封板强势(latest.json early·surge_stock 盘后接口关闭=过期快照)，
    # ②盘后推"埋伏"无操作窗口（当日已涨 12~16%），③无次日竞价预期无法转化操作，
    # ④盘中价值已由 短线精灵/live_signal 承担。candidates.early_ambush 数据保留不删，
    # 仅停止渲染；如需恢复，把下方 blocks.append 循环接回即可。
    # ambush = payload.get("early_ambush") or []
    # if ambush:
    #     blocks.append(_div("**🚀 早鸟埋伏（盘中强势·未封板）**"))
    #     for ea in ambush[:4]:
    #         b = ea.get("buy") or {}
    #         nm = str(b.get("name") or "")
    #         code = str(b.get("code") or "")
    #         pct = b.get("pct")
    #         typ = str(ea.get("type") or "盘中强势")
    #         sec = str(ea.get("sector") or "")
    #         is20 = "20cm" if b.get("is_20cm") else ""
    #         pct_txt = f"{pct:+.1f}%" if pct is not None else ""
    #         warn = "⚠️20cm波动大·紧止损" if is20 else ""
    #         blocks.append(_div(
    #             f"　· <text_tag color='orange'>{nm}({code})</text_tag> "
    #             f"{typ}｜{sec}｜{pct_txt}｜{is20}{warn}"
    #         ))
    #     blocks.append(_hr())

    # 现有输入只有普通direction/复盘新方向和旧低位股票列表，
    # 无明确低位题材事实来源；省略低位方向段，不猜字段或恢复第三池。

    # 执行判断仅由次日竞价链产生，盘后不提前展示结论。

    blocks += _footer(hhmm)

    return {"title": title, "color": "violet", "elements": blocks}


# ── 竞价验证卡 ──────────────────────────────────────────

def _vol_weak_flag(oc: dict) -> str:
    """验证卡唯一量能增量（用户2026-08-13定）：实际竞价成交额 < 预期量能线 → 缩量（蓝），其余不标。
    聚焦竞价指标本身，不做爆量/放量/平量/地量多档判断。

    ⛔ **调用约束（宪法 ⓪-10 · 2026-10-14 立）**：本函数返回 `<text_tag>`，
    调用方**禁止把它插进 `<font>…</font>` 之间**（外层吞内层·整卡降级为无样式）。
    正确写法＝贴在 `</font>` 之后，或把该行拆成「font 段 + 本函数 + font 段」。
    `scripts/card_lint.py` 已把 font↔text_tag 嵌套升为 **FAIL 硬阻断**。"""
    amt = oc.get("auction_amount_yi")
    need = oc.get("auction_amount_need_yi")
    if isinstance(amt, (int, float)) and isinstance(need, (int, float)) and amt < need:
        # ⛔ 2026-10-21 降权：缩量是**辅助判断**，⛔ 不给蓝色（与买入红抢眼）；
        #    灰色胶囊 + 「量能缩了」的白话（原「缩量」是行话）。
        return f" <text_tag color='{C_HINT}'>量能缩了</text_tag>"
    return ""


def _gap_tag(gap) -> str:
    """竞价格差红绿胶囊（2026-08-14 用户定·涨红跌绿 A股惯例）：>0 红、<0 绿、=0 灰。"""
    if not isinstance(gap, (int, float)):
        return ""
    c = "red" if gap > 0 else ("green" if gap < 0 else "grey")
    return f"<text_tag color='{c}'>{gap:+.2f}%</text_tag>"


def _core_reversal_block() -> list[dict]:
    """👑 核心弱转强：昨日连板核心（≥3板）今日竞价强度（2026-08-14 用户定·情绪核心战法）。

    数据源：狸猫猎手/cache/yesterday_zt.json（昨日涨停池·boards=连板数）+
    必盈实时竞价 fetch_realtime_auction（09:25 后今开仍=集合竞价成交价，可现抓）。
    判定（机械·禁编造）：竞价 gap≥+3% → 弱转强✓（核心买点信号，红）；
    0≤gap<+3% → 平开观察（neutral）；gap<0 → 低开·分歧/死亡（绿）。
    降级：无 ≥3 板核心 或 全部抓取失败 → 返回空列表（不阻塞卡片）。"""
    try:
        from adapters.input_auction import fetch_realtime_auction
    except Exception:
        return []
    p = ROOT.parent / "cache" / "yesterday_zt.json"
    if not p.exists():
        return []
    try:
        zt = json.loads(p.read_text(encoding="utf-8")).get("zt_list") or []
    except Exception:
        return []
    cores = [x for x in zt if isinstance(x, dict) and isinstance(x.get("boards"), int) and x["boards"] >= 3]
    cores.sort(key=lambda x: -x["boards"])
    cores = cores[:3]
    if not cores:
        return []
    try:
        q = fetch_realtime_auction([str(c["code"]).strip() for c in cores], retries=3, sleep=1.5)
    except Exception:
        q = {}
    rows = []
    for c in cores:
        name = str(c.get("name") or "?")
        code = str(c["code"]).strip()
        nb = c["boards"]
        it = q.get(code)
        if not it or not it.get("auction_price") or not it.get("prev_close"):
            rows.append(f"{name}({code}) {nb}板｜竞价未采集")
            continue
        gap = (it["auction_price"] / it["prev_close"] - 1) * 100
        if gap >= 3:
            tag = "<text_tag color='red'>重新走强 ✓</text_tag>"
        elif gap >= 0:
            tag = "<text_tag color='neutral'>平开观察</text_tag>"
        else:
            tag = "<text_tag color='green'>低开·走弱</text_tag>"
        rows.append(f"{name}({code}) {nb}板｜竞价 {gap:+.2f}%｜{tag}")
    if not rows:
        return []
    # 🪶 档位 2：全是「竞价未采集」时整块不渲染——没有读数就没有信息，
    #    只留一个标题加一串「未采集」纯占版面（2026-10-21 实测 118 字零信息）。
    if _two_line_rows() and all("未采集" in r for r in rows):
        return []
    return [_div("<text_tag color='neutral'>👑 昨日连板的票 · 今日竞价表现</text_tag>"),
            _div("\n".join(rows))]


def _sector_support_tag(r: dict) -> str:
    """
    板块竞价承接力标注（2026-08-24 用户「孤军高开·板块无承接被砸」思想落地）：
    - lone_no_support + 无成员报价（真孤军·mates 为空/报价缺失）→ 红胶囊「🧲孤军·板块无助攻」
    - lone_no_support + 有报价但 0 红开 → 红胶囊「🧲孤军·板块0红开」（被砸风险警示）
    - support_ok（红开≥1 或 高开≥2%≥1）→ 紫胶囊「🧲板块助攻N家红开」（承接确认）
    - 其余（no_data / 无字段）→ 空串（诚实不渲染，不编造）
    ⛔ 2026-08-25 修复：真孤军（同板块仅本股涨停、无成员可抓）不得渲染「板块0红开」
    误导——0 家报价 ≠ 0 家红开，文案区分「无助攻」与「0红开」。
    ⛔ 2026-08-25 二修（用户「孤军判定不准确·要结合题材接口看板块效应·全局化眼光」）：
    行业孤军但题材层有板块效应（theme_has_sector_effect=True·如医药8家涨停）→ 禁渲染
    「🧲孤军」，改渲染「🧲题材板块效应·N家涨停」紫胶囊——个股行业分散≠板块无承接，
    题材整体有涨停梯队即有板块效应（实证：凯莱英 医疗服务行业孤军、医药题材8家涨停）。
    """
    # 盘后冻结事实优先：完整涨停池与外部计数分列，不能沿用 max(pool, external)
    # 将确认独苗错画成“板块效应”。sidecar_record 只留痕，不能改变既有卡面。
    facts = r.get("linkage_facts") or {}
    facts_ok = isinstance(facts, dict) and facts.get("schema") == "linkage_facts_v1"
    facts_status = str(facts.get("eod_status") or "") if facts_ok else ""
    facts_theme = str(facts.get("theme") or r.get("theme_name") or "")
    facts_count = facts.get("theme_pool_zt_count") if facts_ok else None
    link = r.get("linkage_discipline") or {}
    if _theme_linkage.rollout_allows_card_explain():
        if facts_status == "linked" and facts_theme and facts_theme != "—" and isinstance(facts_count, int):
            if str(link.get("action") or "") == "prefer":
                return f" <text_tag color='{C_HINT}'>🧲同题材继续走强·{facts_theme}{facts_count}家涨停</text_tag>"
            return f" <text_tag color='{C_HINT}'>🧲同题材有梯队·{facts_theme}{facts_count}家涨停</text_tag>"
        if facts_status == "standalone":
            if str(link.get("auction_status") or "") == "no_support":
                return " <text_tag color='neutral'>昨日同题材仅它涨停·今早没人高开</text_tag>"
            if str(link.get("auction_status") or "") == "unknown":
                return " <text_tag color='neutral'>昨日同题材仅它涨停·今早同题材数据不足</text_tag>"
            return " <text_tag color='neutral'>昨日同题材仅它涨停</text_tag>"
    # 题材板块效应优先于孤军警示（旧候选兼容回退·2026-08-25 用户）
    if not facts_ok and r.get("theme_has_sector_effect"):
        n = int(r.get("theme_mates_cnt") or 0)
        th = str(r.get("theme_name") or "")
        if th and th != "—":
            return f" <text_tag color='{C_HINT}'>🧲同板块一起涨·{th} {n}家涨停</text_tag>"
    ss = r.get("sector_support") or {}
    level = str(ss.get("support_level") or "")
    if level == "lone_no_support":
        if int(ss.get("mates_quoted") or 0) == 0:
            return " <text_tag color='red'>🧲同板块没人跟·高开有风险</text_tag>"
        return " <text_tag color='red'>🧲同板块没人跟·0家高开</text_tag>"
    if level == "support_ok":
        n = int(ss.get("red_open") or 0)
        return f" <text_tag color='{C_HINT}'>🧲同板块 {n}家高开助攻</text_tag>"
    return ""


# 风向标仅渲染冻结注入；聚合数据读取由只读 API 承接，不在卡面自行读盘。
def _beacon_block(pool_meta: dict | None) -> list[dict]:
    """🧭 风向标：每条线一个核心 + 它今天的竞价态度。

    2026-11-04 用户：「这样子我们知道这个风向标它到底是一个什么情况，基于这个风向标，
    就像我们上一次做新华传媒和新华文轩一样联动！」

    ⛔ 只呈现结论与数据（谁、几板、开在哪、这条线能不能碰），不写机制解释；
       同一条线的风向标全卡只说这一次，逐票不再重复。
    只接收 09:25 冻结注入 `pool_call.meta.defense.anchor.lines`；
    注入缺失或显式为空均不上卡，不读取磁盘补位。
    """
    meta = pool_meta if isinstance(pool_meta, dict) else {}
    defense = meta.get("defense") if isinstance(meta.get("defense"), dict) else {}
    anchor = defense.get("anchor") if isinstance(defense.get("anchor"), dict) else {}
    # 🧮 竞价广度：这条线**昨日全部涨停票**今天开在哪（2026-11-11 用户
    #    「前五好看但后排大面积水下，是风险」）。⛔ 只读注入，不读盘、不裁决。
    _breadth = ((defense.get("breadth") or {}).get("lines") or {})
    _bmin = int((defense.get("breadth") or {}).get("min_size") or 8)
    lines = anchor.get("lines") if isinstance(anchor.get("lines"), dict) else {}
    if not lines:
        return []

    # 态度 → 动作（⭐ 2026-11-10 晚 文案统一：动作**不再在本文件另写一份**，
    #   一律取 `core.anchor_call.ATT_ACTION` 单一源）。
    #   ⛔ 为什么必须同源：11-10 早上「卡面说别碰、裁决却买」就是 anchor_call 一套、
    #      本文件 `_verdict` 另一套，各写各的。文案再分家就会重演。
    #   · 状态（超预期/错杀/一字…）由 `label` 给；
    #   · 动作（观察接力/可低吸/不买，看跟风…）由 `attitude_action` 给；
    #   · `flat`（符合预期）动作是空串 → 只显示状态，⛔ 不硬凑一句（用户「就是符合预期」）。
    out: list[dict] = [_div("**🧭 风向标**")]
    rows = []
    for fam, v in lines.items():
        if not isinstance(v, dict):
            continue
        name = str(v.get("name") or "")
        if not name:
            continue
        att = str(v.get("attitude") or "")
        if att == "unknown":
            continue                              # 没拿到报价 = 没有信息，不占版面
        gap = v.get("gap")
        # ⛔ 状态/动作都从单一源取，**不读冻结件里存的 label**：旧存档是旧文案
        #    （深低开错杀/一字顶板），照读会让卡面两代文案混排。
        label = _anchor_call.attitude_label(att, str(v.get("label") or ""))
        act = _anchor_call.attitude_action(att) or str(v.get("action") or "")
        boards = v.get("boards")
        bd = f"{int(boards)}板" if isinstance(boards, (int, float)) and boards else ""
        gap_txt = f"{float(gap):+.2f}%" if isinstance(gap, (int, float)) else "—"
        bnum = int(boards) if isinstance(boards, (int, float)) and boards else 0
        line = f"　{name}（{bd}·{fam}） {label} {gap_txt}"
        if act:
            line += f" → {act}"
        # 🧮 广度只挂在这条线的第一行（家数够才算；不够不写百分比，⛔ 不拿 1 只票吓人）
        _b = _breadth.get(fam) if isinstance(_breadth, dict) else None
        if isinstance(_b, dict) and _b.get("applicable"):
            _u = int(_b.get("underwater") or 0)
            _r = float(_b.get("under_ratio") or 0) * 100
            _med = _b.get("median_gap")
            _tail = ("⚠️分化" if _b.get("level") == "divergent"
                     else "⚠️偏弱" if _b.get("level") == "weak" else "")
            line += (f"｜广度 {_b.get('n')} 只·水下 {_u}（{_r:.0f}%）"
                     f"·中位 {float(_med):+.1f}%" if isinstance(_med, (int, float))
                     else f"｜广度 {_b.get('n')} 只·水下 {_u}（{_r:.0f}%）")
            if _tail:
                line += f" {_tail}"
        rows.append((att, bnum, line))
    # 排序：**高度优先**（4板/2板的线才是真风向标，1板新线排后）→ 同高度看态度。
    # ⚠️ `_ATT_RANK` 里 `deep_low` 已排 3（可打），⛔ 别让它按旧 rank 掉到「弱」那堆。
    # 最多 5 条防长卡；没态度的（unknown）上面已剔除，不占版面。
    # ⚠️ 必须覆盖所有档位：缺键会走默认 9 → 该档永远排最后（11-10 实测 `deep_low`
    #    这种最赚钱的档差点被排到「破预期」后面）。
    _order = {"strong": 0, "deep_low": 1, "yizi": 1, "big_gap": 2, "low": 2,
              "flat": 3, "weak": 4, "break": 5}
    rows.sort(key=lambda x: (-x[1], _order.get(x[0], 9)))
    if not rows:
        return []
    for _, _, txt in rows[:5]:
        out.append(_div(txt))
    return out


def _auction_seal_block(seal: dict | None) -> list[dict]:
    """🧲 竞价一字 → 方向线索（2026-11-09 用户「先看见」）。

    用户原话：「竞价的时候如果有一字涨停，需要留意他的炒作方向，
              比如今天的西部材料带动商业航天逻辑！！」
    拍板：「可以 没问题！！先看见」→ **只做解释层**。

    ⛔ 不进名额、不进仓位、不改买入判定。一字板的隔壁票⛔ 不因此成为买入理由：
       11-06 实测同题材龙头一字封死时，隔壁票 T+1 −1.49%、胜率 40.4%（虹吸不是外溢）。
    ⛔ 取不到就写「未取到」，**不写「无一字」** —— 抓不到 ≠ 没有。
    ⛔ 题材一律取标签（board/theme），⛔ 不读行业名：
       002149 行业=小金属、题材=商业航天，读行业名会把方向整个丢掉。
    """
    if _hide_block("seal"):
        return []
    data = seal if isinstance(seal, dict) else {}
    if not data:
        return []

    if not data.get("available"):
        reason = str(data.get("reason") or "未取到").strip()
        return [
            _div("**🧲 竞价一字 → 方向**"),
            _div(f"<font color='grey'>未取到竞价封板数据（{reason}）</font>"),
        ]

    items = [it for it in (data.get("items") or []) if isinstance(it, dict)]
    states = data.get("states") if isinstance(data.get("states"), dict) else {}
    themes = [t for t in (data.get("themes") or [])
              if isinstance(t, dict) and t.get("theme")]

    out: list[dict] = [_div("**🧲 竞价一字 → 方向**")]
    if items:
        head = "、".join(f"{t['theme']}{int(t.get('n') or 0)}只" for t in themes[:4])
        out.append(_div(
            f"竞价一字 <text_tag color='red'>{len(items)}</text_tag> 只"
            + (f" → 方向：{head}" if head else "")
        ))
        for it in items[:6]:
            pct = it.get("pct")
            pct_txt = f"{float(pct):+.2f}%" if isinstance(pct, (int, float)) else "—"
            th = str(it.get("theme") or "题材未标注")
            out.append(_div(
                f"　{it.get('name') or '—'}（{it.get('code') or '—'}）"
                f"{pct_txt} → **{th}**"
            ))
        if len(items) > 6:
            out.append(_div(f"<font color='grey'>　…另有 {len(items) - 6} 只</font>"))
    elif (states.get("unconfirmed") or 0) > 0:
        # ⏱️ 时序诚实：卡片可能生成在 09:25 之后、09:26 那份快照写入之前。
        #    此时「没有 sealed」= **还没等到确认**，不是"市场没有一字"。
        #    ⛔ 不许写「无一字」——抓不到 ≠ 没有。
        out.append(_div(
            f"<font color='grey'>{int(states.get('unconfirmed') or 0)} 只顶过板，"
            f"但最后一条数据早于 09:25 → 一字尚未确认（不是没有）</font>"
        ))
    else:
        out.append(_div("<font color='grey'>竞价没有顶到涨停的票</font>"))

    tail: list[str] = []
    wob = states.get("wobbled") or 0
    unc = states.get("unconfirmed") or 0
    if wob:
        tail.append(f"顶板后撤单 {wob} 只已排除")
    if unc and items:
        tail.append(f"{unc} 只仅 09:20 前出现或未到确认时点")
    tail.append("只做方向线索·不进名额不进仓位")
    out.append(_div("<font color='grey'>" + "；".join(tail) + "</font>"))
    return out


def _watch_reversal_block(rev: dict | None) -> list[dict]:
    """📉→📈 接力池竞价弱转强（2026-11-09 用户「弱转强写进竞价卡里面」）。

    用户原话：「竞价的时候，昨天涨停，今天开盘竞价跌停，然后慢慢的居然红开了。
              我靠，这种竞价弱转强，我也是非常想要买的呀」
              「只关注我们当天收盘锁定的那些票」
              「根据时间戳来更新，记录一次时间再记录一次时间，对比价格变化看趋势」

    ⛔ 只做解释层：不进名额、不进仓位、不改买入判定。
    ⛔ `pending`（末条早于 09:25）写成「终态未确认」，**不许写成「没有弱转强」**。
    ⛔ `no_data` 写成「没抓到」，**不许写成「没反转」**——抓不到 ≠ 没有。
    ⛔ 取不到数据时整段只留一行灰字说明，不静默消失（看不见比看见更危险）。
    """
    if _hide_block("reversal"):
        return []
    data = rev if isinstance(rev, dict) else {}
    if not data:
        return []

    if not data.get("available"):
        reason = str(data.get("reason") or "未取到").strip()
        return [
            _div("**📉→📈 竞价弱转强（接力池）**"),
            _div(f"<font color='grey'>未取到接力池竞价路径（{reason}）</font>"),
        ]

    items = [it for it in (data.get("items") or []) if isinstance(it, dict)]
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    rounds = data.get("rounds")
    pool_n = data.get("pool_size") or len(items)
    revs = [it for it in items if it.get("state") == "reversal"]
    revs.sort(key=lambda x: -(x.get("span_pct") or 0))

    out: list[dict] = [_div("**📉→📈 竞价弱转强（接力池）**")]

    head = f"接力池 <text_tag color='grey'>{int(pool_n)}</text_tag> 只"
    src = str(data.get("source") or "")
    # ⚠️ `rounds` 在两套来源里含义不同：心跳=轮次；观测表兜底=**记录条数**（去重后
    #    一只票可能只有 1 条）。⛔ 兜底时不许写「N 轮」——740 条记录不是 740 轮采样。
    if src == "heartbeat" and isinstance(rounds, int) and rounds > 0:
        head += f" · 竞价采样 {rounds} 轮"
    elif src == "registry":
        last = str(data.get("global_last_ts") or "")
        head += f" · 观测表补充{f'（最后采样 {last}）' if last else ''}"
    out.append(_div(head))

    if revs:
        for it in revs[:4]:
            first = it.get("first_pct")
            low = it.get("low_pct")
            last = it.get("last_pct")
            span = it.get("span_pct")
            def _p(v):
                return f"{float(v):+.2f}%" if isinstance(v, (int, float)) else "—"
            span_txt = f"{float(span):+.2f}pp" if isinstance(span, (int, float)) else "—"
            # 三个时点摆出来 = 用户要的「记录一次再一次，对比看趋势」：
            # 首笔（起点）→ 最低（砸到哪）→ 末条（爬回哪）。只给两端看不出过程。
            out.append(_div(
                f"　{it.get('name') or '—'}（{it.get('code') or '—'}）"
                f"首 {_p(first)}（{it.get('first_ts') or '—'}）→ "
                f"低 <text_tag color='green'>{_p(low)}</text_tag>"
                f"（{it.get('low_ts') or '—'}）→ 末 "
                f"<text_tag color='red'>{_p(last)}</text_tag>"
                f"（{it.get('last_ts') or '—'}）· 拉升 {span_txt}"
            ))
        if len(revs) > 4:
            out.append(_div(f"<font color='grey'>　…另有 {len(revs) - 4} 只</font>"))
    else:
        bits = ["接力池竞价没有「深水转红」"]
        pend = int(counts.get("pending") or 0)
        nod = int(counts.get("no_data") or 0)
        if pend:
            bits.append(f"{pend} 只终态未确认")
        if nod:
            bits.append(f"{nod} 只没抓到")
        out.append(_div("<font color='grey'>" + "；".join(bits) + "</font>"))

    tail: list[str] = []
    pend = int(counts.get("pending") or 0)
    nod = int(counts.get("no_data") or 0)
    if revs and pend:
        tail.append(f"{pend} 只终态未确认")
    if revs and nod:
        tail.append(f"{nod} 只没抓到")
    tail.append("只描述竞价路径·不进名额不进仓位")
    out.append(_div("<font color='grey'>" + "；".join(tail) + "</font>"))
    return out


def _relay_position_block(plan: dict | None, *, day_word: str = "今天",
                          title: str = "今日接力仓位建议（动态）",
                          pool_meta: dict | None = None,
                          ice_ban: bool = False) -> list[dict] | None:
    """早盘接力·动态仓位建议块（2026-10-13 用户需求）。

    把 relay_position 的方案渲染成**大白话**块（过 card_lint 白话门禁）：行情强弱定调 +
    同时持有只数 + 单票/总仓上限 + 题材沉淀置信 + 回撤降档提示。失败/禁用 → None（不渲染）。

    ⏱️ 时点（2026-10-14）：盘后候选卡渲染的是**次日**方案 → 传 `day_word="明天"` /
    `title="明日接力仓位建议（动态）"`；09:25 竞价卡渲染当日 → 用默认「今天 / 今日」。
    🎯 动态两段式（同日用户「动态仓位必须让人看见」）：只要回撤压过仓，就同时给出
    「按行情档位能给到」与「实际压到」两行——只给最终一个数字，读者看不出"动态"。

    🎯 单票/名额口径（2026-11-04 用户「每日尽量推两个」「只能推一只时仓位控死50%」）：
    `pool_meta` = 09:25 冻结的 `pool_call` meta。有它就以**真实参与票数**为准：
      · `single_pick=True`  → 显示「今天只买 1 只 · 单票上限 50%」（× 回撤乘数），
        ⛔ 不再显示弱档 30% / 中档 40%（那会把单票压到 30%，与账本打架）；
      · 两票及以上 → 名额显示 `effective_quota`（名额放宽到 2 时不能还写 1），
        单票/总仓仍用档位 `per_pick_cap` / `total_cap`（不放大总仓）。
    无 `pool_meta`（盘后卡 / 隔离渲染）→ 逐字沿用旧渲染。
    """
    if not isinstance(plan, dict):
        return None
    if not plan.get("enabled", True):
        return None
    tier = plan.get("tier_label") or ""
    quota = plan.get("quota")
    per = plan.get("per_pick_cap")
    total = plan.get("total_cap")
    can_buy = plan.get("can_buy", True)
    ti = (plan.get("_diag") or {}).get("theme_info") or {}
    day_count = ti.get("day_count")
    theme_count = ti.get("theme_count")

    # 09:25 真实参与数（缺 → 全部为 None/旧行为）
    _slot = (pool_meta or {}).get("slot_policy") or {}
    single_pick = bool(_slot.get("single_pick"))
    single_cap = _slot.get("single_pick_cap")
    shown_quota = _slot.get("effective_quota")
    if not isinstance(shown_quota, int):
        shown_quota = quota

    # 🎯 2026-11-05「不限制只数 · 单票最多50% · 平均分配仓位」：
    #   名额不再按档位截断 → 卡面读**真实参与只数**；仓位读均分上限 even_cap，
    #   ⛔ 不再显示弱档 30% / 中档 40% 的档位数字（那是旧口径，会与账本打架）。
    _even = bool(_slot.get("even_allocation"))
    _even_cap = _slot.get("even_cap")
    _even_n = _slot.get("allocation_count")
    try:
        from core.risk_limits import MAX_POSITION_PER_STOCK as _HARD_CAP_LOCAL
    except ImportError:  # pragma: no cover
        from wealth_code.core.risk_limits import MAX_POSITION_PER_STOCK as _HARD_CAP_LOCAL  # type: ignore[no-redef]
    if _even and isinstance(_even_cap, (int, float)) and _even_cap > 0 \
            and isinstance(_even_n, int) and _even_n >= 1:
        shown_quota = _even_n
        single_cap = float(_even_cap)

    lines: list[str] = []

    def _pct(v) -> str:
        """仓位百分比显示：<10% 保留 1 位小数（避免 4.5% 被 .0f 舍成 4%）。"""
        n = v * 100
        return f"{n:.1f}%" if n < 10 else f"{n:.0f}%"

    _single_ok = (single_pick and isinstance(single_cap, (int, float))
                  and float(single_cap) > 0)

    if ice_ban:
        # ⛔ 冰点日不许再喊「今天只买 N 只 · 每只 X%」——与卡头「不买·空仓」直接对撞
        #    （2026-11-09 事故：同一张卡上「空仓」与「买1只50%」并存）。
        _dd_ice = plan.get("drawdown_pct")
        _dd_txt = f"（账户回撤 -{_dd_ice:.2f}% 的压仓乘数照常留痕）" if isinstance(_dd_ice, (int, float)) else ""
        lines.append(f"🛡️ {day_word}冰点不买 · 仓位归零{_dd_txt}")
        lines.append("　　等情绪分走出冰点区再恢复名额（⛔ 竞价转强不翻多）")
    elif not can_buy:
        # 防御分支：本系统（relay_position）2026-10-13 起**不再产生 can_buy=False**
        # （回撤改为动态压仓·不禁买）；此处仅为外部注入 plan 的兜底渲染。
        dd_v = plan.get("drawdown_pct")
        reason = f"账户回撤 -{dd_v:.2f}%" if isinstance(dd_v, (int, float)) else "行情过弱"
        lines.append(f"🛡️ {day_word}不参与（{reason} → 仓位建议不生效）")
    elif _single_ok:
        # 🎯 单票场景：单票上限 = 50% × 回撤乘数（relay_position.single_pick_cap 单一源）。
        #    ⛔ 不再写「每只不超过 30%」——行情弱档不该把唯一的票压到三成。
        lines.append(f"行情强弱：{tier}")
        dd = plan.get("drawdown_pct")
        mult = plan.get("drawdown_mult")
        _pressed = (plan.get("drawdown_level") in ("soft", "heavy")
                    and isinstance(dd, (int, float))
                    and isinstance(mult, (int, float)) and mult < 0.999)
        if _pressed:
            try:
                from core.risk_limits import MAX_POSITION_PER_STOCK as _HARD_CAP
                lines.append(f"行情好的话能给到：最多 1 只 · 每只不超过 {_pct(_HARD_CAP)}")
            except Exception:
                pass
            lines.append(
                f"账户回撤 -{dd:.2f}% → 实际压到：{day_word}只买 1 只 · "
                f"单票上限 {_pct(float(single_cap))}"
                f"（因为前面亏了 {dd:.1f}%，按比例收着做）")
        else:
            lines.append(f"{day_word}只买 1 只 · 单票上限 {_pct(float(single_cap))}")
    elif (_even and isinstance(_even_cap, (int, float)) and _even_cap > 0
          and isinstance(_even_n, int) and _even_n >= 2):
        # 🎯 均分口径：只报「买几只 / 每只多少」，⛔ 不写机制解释、不写旧档位。
        lines.append(f"行情强弱：{tier}")
        lines.append(f"{day_word}买 {_even_n} 只 · 每只 {_pct(float(_even_cap))} · "
                     f"单票上限 {_pct(_HARD_CAP_LOCAL)}")
    elif isinstance(per, (int, float)) and isinstance(total, (int, float)) and isinstance(shown_quota, int):
        # ⛔ 「行情强弱」是仓位块的**结论档位**，⛔ 任何档位都必须留——
        #    删了它就只剩一个数字，读者不知道这个数字是「中·正常」压出来的还是「强」压出来的。
        lines.append(f"行情强弱：{tier}")
        dd = plan.get("drawdown_pct")
        mult = plan.get("drawdown_mult")
        # 回撤实际压过仓才算「动态」：此时先给档位基数、再给压后实际（2026-10-14）。
        _pressed = (plan.get("drawdown_level") in ("soft", "heavy")
                    and isinstance(dd, (int, float))
                    and isinstance(mult, (int, float)) and mult < 0.999)
        if _pressed:
            # ⛔ 两段式**任何档位都不许压成一行**（用户 2026-10-14「动态仓位必须让人看见」）：
            #    只给最终一个数字，读者看不出「这个数字是被回撤压下来的」还是「本来就这么小」。
            #    我曾试过压成一行（2026-10-21 第二轮），test_verify_card_relay_position 立刻红。
            try:
                from core.relay_position import tier_caps as _tier_caps
                _bq, _bp, _bt = _tier_caps(str(plan.get("tier") or ""))
                # ⛔ 「档位」是内部词 → 对外说「行情好的时候」
                lines.append(f"行情好的话能给到：最多 {_bq} 只 · 每只不超过 {_pct(_bp)} · "
                             f"总仓位不超过 {_pct(_bt)}")
            except Exception:
                pass
            lines.append(
                f"账户回撤 -{dd:.2f}% → 实际压到：{day_word}最多同时持有 {shown_quota} 只 · "
                f"每只不超过 {_pct(per)} · 总仓位不超过 {_pct(total)}"
                f"（因为前面亏了 {dd:.1f}%，按比例收着做）")
        else:
            lines.append(f"{day_word}最多同时持有 {shown_quota} 只 · 每只不超过 {_pct(per)} · "
                         f"总仓位不超过 {_pct(total)}")
    # 🎯 11-05 21:45 高开降权：上面的「每只 X%」是**均分标准上限**，而接近涨停的票
    #    逐票 position_cap 会更低（冻结件/账本同值）。⛔ 不写这一行就会出现
    #    「卡面说 50%、账本记 10%」——违反卡面/验证/账本同源。
    #    只报事实与最低那档，⛔ 不解释机制、不露「降权系数」。
    if can_buy and _slot.get("gap_weight_enabled"):
        _gw_caps = _slot.get("gap_weight_caps") or {}
        _gw_vals = [v for v in _gw_caps.values() if isinstance(v, (int, float)) and v > 0] \
            if isinstance(_gw_caps, dict) else []
        if _gw_vals:
            lines.append(f"其中 {len(_gw_vals)} 只开得接近涨停，"
                         f"上限收紧到最低 {_pct(min(_gw_vals))}")
    # 题材库沉淀 = 客观事实（判断交给 hints，避免同一件事说两遍）
    if isinstance(day_count, int) and isinstance(theme_count, int) and day_count:
        # 只 20 字，且是「题材库成熟度」这条唯一口径 —— ⛔ 不值得为省它破坏契约
        lines.append(f"题材库已沉淀 {day_count} 天（共 {theme_count} 个题材）")
    # 提示：回撤已在上一行直说 → 跳过重复；「可正常给仓位」在不参与时是误导 → 跳过
    for h in (plan.get("hints") or []):
        if "账户回撤" in h:
            continue
        if not can_buy and ("禁买" in h or "可正常给仓位" in h):
            continue
        lines.append(f"　{h}")
    if not lines:
        return None
    return [_div(f"🎯 **{title}**\n" + "\n".join(lines))]


def attach_eod_ai_plan(payload: dict) -> dict:
    """给盘后候选 payload 注入次日 AI 预案视图（`cache/ai_view_<次日>.json`）。

    由 `run_wealth_code.cmd_eod` 在推卡前调用——**注入式**而非渲染层隐式读盘：
    渲染函数拿到确定输入，单测可注入 `{"ai_plan": {}}` 隔离真实 cache（防跨日误读）。
    ⛔ 注入失败 → 空 dict（诚实省略该块，不阻断出卡）。
    """
    if not isinstance(payload, dict) or "ai_plan" in payload:
        return payload
    try:
        payload["ai_plan"] = _load_ai_view_raw(
            _next_trade_day_inline(str(payload.get("date") or "")))
    except Exception:
        payload["ai_plan"] = {}
    return payload


def _ai_plan_eod_block(raw: dict, cand_by_code: dict,
                       session_date: str | None = None, *,
                       relay_codes: set[str] | None = None) -> list[dict]:
    """🤖 AI 明日买入预案（2026-10-14 用户「文案要体现动态仓位和买入这块内容」）。

    盘后候选卡的**预案段**：读 `cache/ai_view_<次日>.json`（由 `ai_ledger record` 写，
    在 eod 推卡前一步落盘），只渲染「定调 + 方向 + 逐票预案档」。
    ⛔ 不含「买哪只」——那由**次日 09:25** 池内裁决产出（`_ai_plan_block`，竞价卡渲染）；
      时序大前提＝盘后只缩范围、不拍板（2026-09-21 用户定）。
    ⛔ 理由只在此处渲染一次（盘后卡别处不渲染 AI 理由），走 `_narr_render` 分段排版。
    视图缺失（未跑 record / 非交易日）→ []（诚实省略，不编造）。

    ⛔⛔ **2026-11-06 用户拍板「独立明日预案不展示」→ 本函数保持不接入 `build_eod_card`。**
       预案数据仍照常落盘（`cache/ai_view_<次日>.json`，次日 09:25 池内裁决与竞价卡消费），
       只是盘后卡不渲染这一块。⛔ 后续勿擅自接线，否则等于推翻用户决定。
    """
    views = raw.get("views") or {}
    if not views:
        return []
    _dd = str(raw.get("decision_date") or session_date or "")
    mmdd = _dd[5:] if len(_dd) >= 10 else _dd
    stance_label = (str(raw.get("stance_label") or "")
                    or _AI_STANCE_FALLBACK.get(str(raw.get("stance") or ""), ""))
    direction = str(raw.get("direction") or "").strip()
    note = str(raw.get("note") or "").strip()

    blocks: list[dict] = [_div(
        f"**🤖 AI 明日买入预案**<font color='grey'>（{mmdd} 盘后定 · 重点看 / 一般 / 回避；"
        "买哪只次日 9:25 竞价开出来后定）</font>")]
    head = []
    if stance_label:
        head.append(f"定调：{stance_label}")
    if direction:
        head.append(f"方向：{direction}")
    if head:
        blocks.append(_div("｜".join(head)))
    if note:
        blocks.append(_div(f"<font color='grey'>{note}</font>"))

    for code, v in views.items():
        it = cand_by_code.get(str(code)) or {}
        core = it.get("is_sector_dragon") is True and it.get("dragon_rank") in (None, 1)
        status = "已标注题材龙一" if core else "未确认题材核心"
        if not it:
            boundary = "未匹配本卡候选·不新增交易资格"
        elif str(code) not in (relay_codes or set()) or it.get("buyable") is False:
            boundary = "保留原资格·不自动转可买"
        else:
            boundary = "仍待原竞价量价、风险与名额裁决"
        blocks.append(_div(f"　{it.get('name') or v.get('name') or code}({code})：{status}｜{boundary}"))
        # 一般/回避票同样保留核心依据和风险，避免只有重点票能看见理由。
        if not _is_focus(v) and v.get("reason"):
            blocks.append(_div(_narr_render(str(v["reason"]))))

    def _nm(code: str, v: dict) -> str:
        it = cand_by_code.get(str(code)) or {}
        return str(it.get("name") or v.get("name") or code)

    focus = [(str(c), v) for c, v in views.items() if _is_focus(v)]
    if focus:
        blocks.append(_div(f"　<text_tag color='red'>重点看（{len(focus)} 只·仅预案，待竞价核验）</text_tag>："))
        for code, v in focus:
            it = cand_by_code.get(code) or {}
            blocks.append(_div(f"　　{_hl(_nm(code, v) + '(' + code + ')', 'red')}{_boards_txt(it)}"))
            _rs = str((v or {}).get("reason") or "").strip()
            if _rs:
                blocks.append(_div(_narr_render(_rs)))
    else:
        blocks.append(_div("<font color='grey'>　明日没有给「重点看」的票（都按一般或回避处理）</font>"))
    # ⛔ 其余必须按预案档分开写（2026-10-30 修）：「回避」与「只做观察」是两回事，
    #    合成一句「其余 N 只只做观察」会把明确回避的票说成观察票（口径打架）。
    _normal = [_nm(str(c), v) for c, v in views.items()
               if not _is_focus(v) and _plan_label(v) == _BIAS_LABEL["normal"]]
    _avoid = [_nm(str(c), v) for c, v in views.items()
              if not _is_focus(v) and _plan_label(v) == _BIAS_LABEL["avoid"]]
    _rest = [_nm(str(c), v) for c, v in views.items()
             if not _is_focus(v) and _plan_label(v) not in
             (_BIAS_LABEL["normal"], _BIAS_LABEL["avoid"])]
    if _normal:
        shown = "、".join([o for o in _normal if o][:8]) + ("等" if len(_normal) > 8 else "")
        blocks.append(_div(f"　　一般预案 {len(_normal)} 只（保留原资格·非买入许可）：{shown}"))
    if _avoid:
        shown = "、".join([o for o in _avoid if o][:8]) + ("等" if len(_avoid) > 8 else "")
        blocks.append(_div(f"　　回避 {len(_avoid)} 只：{shown}"))
    if _rest:
        shown = "、".join([o for o in _rest if o][:8]) + ("等" if len(_rest) > 8 else "")
        blocks.append(_div(f"　　其余 {len(_rest)} 只只做观察：{shown}"))
    return blocks


def _mech_fold_blocks(records: list[dict]) -> list[dict]:
    """🧩 机械口径**并入**逐票（2026-10-21 用户「机械量价参考 想办法 和AI判断融合在一起」）。

    ⛔ 为什么不再逐票列：
      同一只票原先在 🤖 今日买入段出现一次、在 📊 机械段再完整出现一次（三块正文），
      实测 10-21 六只达标里四只重复 —— 这是全卡最大冗杂来源，也是用户三次要求瘦身的根因。
    **现在一只票只出现一处**，机械口径压成标题行胶囊（`📊 机械达标` / `📊 机械没达标`），
    AI 判定仍是独立第三行 —— 两套口径**并排**，读者能直接对拍「机械看上、AI 没买」。

    ⛔ 这里只做两件事：
      ① 一行口径总量（机械达标 N 只 / AI 买入 M 只）——总量信息不丢，但不逐票重复；
      ② **兜底**：机械达标却没有任何 AI 判定的票（旧候选 / POOL_CALL 关闭）——
         它们不会在下面任何分段出现，此处必须完整列出，否则会从卡上消失。
    """
    _mech = [r for r in records if _mech_pass(r)]
    if not _mech:
        return []                     # ⛔ 无达标票 → 不占版面（不写「今日没有票达标」废话）
    _ai_yes = [r for r in _mech if str(_oc_of(r).get("ai_view") or "") == "participate"]
    # 兜底判据：机械达标 ∧ AI 无判定 ∧ 不是被明确放弃/没报价/命中
    _orphan = [r for r in _mech
               if not str(_oc_of(r).get("ai_view") or "")
               and str(_oc_of(r).get("decision_status") or "") in ("buy", "pending", "")]
    # ⛔ 2026-10-21 用户「这是商业性项目，不要显示解释性质的语录」：
    #    原句「📊 机械口径：达标 N 只 · AI 买入 M 只 —— 已并到下面每只票（标…），不另列一遍」
    #    整句是**系统机制解释**，对外产品不该出现。删掉后信息仍在：
    #    每只票的量价判定写在标题行（`量价够格`）、AI 判定写在第三行。
    blocks: list[dict] = []
    for r in _orphan:
        oc = _oc_of(r)
        blocks.extend(_fold_row_blocks(r, oc))
    blocks.append(_hr())
    return blocks


def _fold_row_blocks(r: dict, oc: dict) -> list[dict]:
    """无 AI 判定的兜底票：标题行 + 指标行（⛔ 无判定就不编判定）。"""
    gap = oc.get("gap_pct")
    amt = oc.get("auction_amount_yi")
    need = oc.get("auction_amount_need_yi")
    gap_txt = f"{gap:+.2f}%" if isinstance(gap, (int, float)) else "—"
    amt_txt = f"{amt:.2f}" if isinstance(amt, (int, float)) else "—"
    need_txt = f"{need:.2f}" if isinstance(need, (int, float)) else "—"
    bucket = str(r.get("bucket_label") or "")
    hl_color = C_WATCH   # 🎨 非买入票统一淡蓝（2026-10-21 降权）
    exp_ref, verdict = _exp_verdict(r, oc)
    dv_tag = (r.get("daily_verdict") or {}).get("tag", "")
    out = [_div(
        f"📊 {_stock_hl_boards(r, hl_color)} "
        f"{oc.get('signal_label') or oc.get('decision_label') or ''} {_gap_tag(gap)}"
        + _dragon_tag(oc) + _tier_tag(oc) + _verdict_tag(verdict) + _sector_support_tag(r)
        + "<font color='grey'>（AI 未给结论）</font>")]
    if exp_ref:
        out.append(_div(
            f"　　<font color='grey'>{exp_ref}｜额{amt_txt}/{need_txt}亿</font>"
            + _vol_weak_flag(oc)
            + f"<font color='grey'>｜{bucket}｜{_theme_dual_tag(r)}｜"
            + (f" {dv_tag}" if dv_tag else "") + "</font>"))
    else:
        out.append(_div(
            f"　　<font color='grey'>竞价{gap_txt} 额{amt_txt}/{need_txt}亿</font>"
            + _vol_weak_flag(oc)
            + f"<font color='grey'>｜{bucket}｜{_theme_dual_tag(r)}｜"
            + (f" {dv_tag}" if dv_tag else "") + "</font>"))
    return out


def _mech_reference_blocks(records: list[dict], ai_view: dict, ai_caliber: bool) -> list[dict]:
    """📊 机械量价参考段（**旧版·两段分列**）。⛔ 仅在 `VERIFY_CARD_UNIFY_MECH=0` 时使用。

    2026-10-21 用户「机械量价参考 想办法 和AI判断融合在一起」→ 默认走
    `_mech_fold_blocks()`：一只票只出现一处，机械口径压成标题行胶囊。
    本函数逐字节保留旧行为，供一键回退。
    """
    # ⛔ 抽出成函数后必须自己初始化 `blocks`（原来用的是 build_verify_card 里的同名局部变量）
    blocks: list[dict] = []
    # 买入信号（super / expected）——名称按接力紫/观察黄分色，竞价指标为核心
    # ⚠️ 口径：本段是**机械判定**（竞价量价达标即列），不等同于 AI 要买的名单（2026-09-14 口径统一）。
    # 🤖 2026-09-14「全量以 AI 为唯一口径」后 `can_enter` = AI 口径（决定账本），本段改按
    #    `mech_can_enter`（机械口径结论）筛选，保持「机械参考」语义不变、与账本互不干扰。
    #    旧 record 无 mech_can_enter 字段 → 回退 can_enter（向后兼容）。
    def _is_mech_pass(_r: dict) -> bool:
        _oc = (_r.get("performance") or {}).get("open_check") or {}
        return bool(_oc["mech_can_enter"]) if "mech_can_enter" in _oc else bool(_oc.get("can_enter"))

    # 🪶 差集（2026-10-21）：机械段只列「机械达标 ∧ AI 今天没买」的票——
    #    已在「🤖 今日买入」逐票展示过的，不再重复一遍（实测 6 只里 4 只重复）。
    #    ⛔ 总数不丢：卡头「机械量价参考 N 只」仍是全量，差集只影响正文列谁。
    _bought = _bought_codes(records, ai_view.get("views") if isinstance(ai_view, dict) else None)
    _mech_all = [r for r in records if _is_mech_pass(r)]
    buy_rows = ([r for r in _mech_all if str(r.get("code") or "") not in _bought]
                if _mech_diff_only() else _mech_all)
    _mech_dup = len(_mech_all) - len(buy_rows) if _mech_diff_only() else 0
    if buy_rows:
        # AI 裁决启用后，机械达标只用于核对量价，不得被误读为当日买入安排。
        _head = ("**📊 量价够格的票（仅作参考，不等同于今天的买入安排）**" if ai_caliber
                 else "**✅ 量价够格的票**")
        if _mech_dup:
            # 差集模式下必须交代「另外几只去哪了」，否则读者会以为机械只达标这几只
            _head += (f"<font color='grey'>（量价够格共 {len(_mech_all)} 只，"
                      f"其中 {_mech_dup} 只已在上方买入名单 → 这里只列没买的）</font>")
        blocks.append(_div(_head))
        for r in buy_rows:
            oc = (r.get("performance") or {}).get("open_check") or {}
            gap = oc.get("gap_pct")
            amt = oc.get("auction_amount_yi")
            need = oc.get("auction_amount_need_yi")
            sig = oc.get("signal_label") or oc.get("decision_label") or ""
            gap_txt = f"{gap:+.2f}%" if isinstance(gap, (int, float)) else "—"
            amt_txt = f"{amt:.2f}" if isinstance(amt, (int, float)) else "—"
            need_txt = f"{need:.2f}" if isinstance(need, (int, float)) else "—"
            bucket = str(r.get("bucket_label") or "")
            # 🎨 2026-10-21：这里不是要买的票 → 统一淡蓝，
            #    ⛔ 不再按"接力池紫、观察池黄"上色：池别是内部结构，读者不关心。
            #    ⚠️ 本段（旧版机械参考·仅 `VERIFY_CARD_UNIFY_MECH=0` 时走）没有 icon 变量，
            #       ⛔ 不能引用它 —— 实测 NameError，会让整张卡渲染失败。
            hl_color = C_WATCH
            exp_ref, verdict = _exp_verdict(r, oc)
            dv_tag = r.get("daily_verdict", {}).get("tag", "")
            # 风险提示：2026-09-11 起不再内联在标题行（与下方点评的「⚠️ 风险」段重复且撑长标题行），
            # 统一由 _narr_render 在点评末行以 turquoise 标签呈现（语义顺序：走势→题材→股性→风险）。
            blocks.append(_div(
                f"{'📊' if ai_caliber else '✅'} {_stock_hl_boards(r, hl_color)} {sig} {_gap_tag(gap)}"
                + _dragon_tag(oc)
                + _tier_tag(oc)
                + _verdict_tag(verdict)
                + _sector_support_tag(r)
            ))
            if exp_ref:
                blocks.append(_div(
                    # ⛔ 缩量胶囊必须贴在 </font> 之外（宪法 ⓪-10：禁 font 套 text_tag）
                    f"　　<font color='grey'>{exp_ref}｜额{amt_txt}/{need_txt}亿</font>"
                    + _vol_weak_flag(oc)
                    + f"<font color='grey'>｜{bucket}｜{_theme_dual_tag(r)}｜"
                    + (f" {dv_tag}" if dv_tag else "")
                    + "</font>"
                ))
            else:
                blocks.append(_div(
                    # ⛔ 缩量胶囊必须贴在 </font> 之外（宪法 ⓪-10：禁 font 套 text_tag）
                    f"　　<font color='grey'>竞价{gap_txt} 额{amt_txt}/{need_txt}亿</font>"
                    + _vol_weak_flag(oc)
                    + f"<font color='grey'>｜{bucket}｜{_theme_dual_tag(r)}｜"
                    + (f" {dv_tag}" if dv_tag else "")
                    + "</font>"
                ))
            # 🪶 档位 2（2026-10-21 第二轮）：每只票**只留这两行**。
            #    ⛔ 不丢：判定理由（为什么没买）——它并进指标行末，不再独占两行。
            #    ⛔ 丢：历史基因行 / AI 判断单独成行 / 点评四段。
            #      基因与点评的数字在候选 JSON 里都在，卡面不再重复第二遍。
            if _two_line_rows():
                # ⛔ 只砍理由长度，不砍「AI 判什么」（用户 2026-10-21 质问点）
                _j25 = _j25_line(oc)
                if _j25:
                    blocks.append(_div(_j25))
                continue
            _gene = _hist_gene_line(r)               # 📈 历史股性（2026-09-10 用户要求补·verify 卡）
            if _gene:
                blocks.append(_div(_gene))
            _ai = _ai_view_line(r, ai_view,
                                compact=_compact_on() and not _wants_full_detail(oc))
            if _ai:
                blocks.append(_div(_ai))
            if r.get("narrative"):
                # 简洁模式（2026-10-21）：这一段是**机械量价参考**，只有 AI 今日 9:25 判「买入」
                # 的那只才需要完整叙事；其余达标但没买的票（跟风/防守闸/名额用完）压成
                # 「判定 + 风险一句」——走势/题材属于讲故事，不影响今天动不动手。
                # ⛔ 数据不丢：竞价、额/需要额、预期区间、历史基因、判定理由全在。
                # 🪶 精简模式（2026-10-21）：ai_skipped=True → 这批票本来就在「AI 没买」
                #    名单里，它的 9:25 判定与理由已在上一行说清，点评只留风险一句。
                _nb = _narr_render(
                    r["narrative"], prev=True,
                    drop_labels=_compact_drops(oc, bool(_gene),
                                               ai_skipped=_is_ai_skipped(r, _bought)))
                if _nb:
                    blocks.append(_div(_nb))
        blocks.append(_hr())
    elif _mech_all:
        # 差集把票全滤掉了（机械达标的都被 AI 买了）→ ⛔ 不能说「今日没有票达标」，
        # 那是另一件事。如实说明即可，不占版面。
        blocks.append(_div(
            f"<font color='grey'>📊 量价够格的 {len(_mech_all)} 只已在上方买入名单里，不重复列出。</font>"))
        blocks.append(_hr())
    else:
        # 机械参考无达标票。若 AI 口径另有成交，卡头 🤖 AI 计划段已逐票给出
        # （2026-09-14 口径统一后本段只陈述机械面，避免与 AI 段互相打脸）。
        blocks.append(_div(
            "⚪ 今天没有量价同时够格的票（买不买看上面的 🤖 今日买入）。" if ai_caliber
            else "⚠️ 今日没有能买的信号——空仓，或者只看不动。"))
        blocks.append(_hr())
    return blocks


def build_verify_card(verify_result: dict[str, Any]) -> dict:
    """
    verify_result = pipeline_verify.run_verify(...) 的输出（含 payload.records / summary）。
    """
    hhmm = _now_bj().strftime("%H:%M")
    summary = verify_result.get("summary") or {}
    records = (verify_result.get("payload") or {}).get("records") or []
    # feedback 10 分制情绪（规则层风险标记用·2026-08-16；pipeline_verify 已透传）
    fb_env = (verify_result.get("payload") or {}).get("environment")
    # 🧲 竞价一字 → 方向线索（2026-11-09 用户「先看见」；⛔ 纯解释层）
    _seal = (verify_result.get("payload") or {}).get("auction_seal")
    # 📉→📈 观察池竞价弱转强（2026-11-09 用户「弱转强写进竞价卡里面」；⛔ 纯解释层）
    _rev = (verify_result.get("payload") or {}).get("watch_reversal")

    # 🤖 AI 判断视图（2026-09-10 用户「AI 思考合并进竞价验证」·盘后写入次日卡展示）
    _ai_view = _load_ai_view(str(summary.get("trade_date10") or ""))
    # AI 决策原文（含 stance/direction/note/views）——卡头口径与「🤖 今日买入/昨日预案」段共用同一份
    _ai_raw = _load_ai_view_raw(str(summary.get("trade_date10") or ""))
    total = summary.get("total_candidates", len(records))
    buys = summary.get("buy_signals", 0)
    hits = summary.get("hit_signals", 0)
    covered = summary.get("covered", 0)
    nav = summary.get("nav_latest")
    # 冰点 regime（2026-08-19 用户定）：命中照算·禁买不进场·卡片展示命中票
    # ⏱️ 2026-11-09 用户「我要买入的那一套逻辑，不要禁用的这一套逻辑」：
    #    `regime` 可能是**上一交易日盘后**的标签（pipeline_verify 会带 `regime_trusted`
    #    标明它是不是当日实时读数）。⛔ 不 trusted 的 ice 只当背景，不得渲染成禁买/空仓——
    #    实测 2026-11-09：昨收 1.8 分 ice → 今晨 6.3 分修复，标题却仍写「不买·空仓」，
    #    正文 AI 实判了买入，一卡两结论。缺字段（旧 payload）→ 维持旧行为（信任）。
    _env = (verify_result.get("payload") or {}).get("environment") or {}
    regime = str(_env.get("regime") or "").strip()
    regime_trusted = bool(_env.get("regime_trusted", True))
    ice_ban = bool(regime == "ice" and regime_trusted)
    # 说明用：昨收冰点但当日已重判 → 给一行时点交代，⛔ 不改结论
    ice_stale = bool(_env.get("regime_eod") == "ice" and not ice_ban)

    # ── 买入口径统一为 AI（2026-09-14 用户「我想统一为AI口径」→「全量以 AI 为唯一口径！」）──
    # AI 有判断 → 卡头/定调以 **AI 口径实际能成交的只数**（= 账本口径 = summary.buy_signals）为准，
    #   并括注「重点看 N 只」（AI 昨日预案）与「机械量价参考 M 只」给出全信息；
    # AI 无判断（视图缺失）或旧 summary（无 caliber 字段）→ 回退上一版行为（参与只数 / 机械口径）。
    # 采用「实买」而非「计划」的理由：计划数会把「AI 要买但竞价开太高没买到」渲染成有信号，
    # 与账本（0 成交）自相矛盾——口径统一后卡头必须与账本同源。
    ai_focus = _ai_focus_count(_ai_raw)
    caliber = str(summary.get("caliber") or "")
    ai_caliber = bool(AI_CALIBER_BUY_SIGNAL and (_ai_raw.get("views") or {}))
    # 🎯 口径同源（2026-11-09）：标题的「买几只」**必须**与正文「🤖 今日买入」段同一个表达式。
    #    踩过的坑：标题读账本 `buy_signals`（会被冰点闸门降级成 0），正文读
    #    `record.open_check.ai_view == "participate"`（AI 原判，1 只）→ 同一张卡
    #    标题写「空仓」、正文写「AI 买入阳光照明」，读者以为系统坏了。
    _pool_has = bool((summary.get("pool_call") or {}).get("participate") is not None) or any(
        _oc_of(r).get("pool_reason") for r in records)
    _pool_buy_n = sum(1 for r in records if str(_oc_of(r).get("ai_view") or "") == "participate")
    ai_filled = (_pool_buy_n if (ai_caliber and _pool_has)
                 else (buys if (ai_caliber and caliber == "ai") else ai_focus))
    mech_buys = summary.get("mech_buy_signals")
    if mech_buys is None:
        mech_buys = buys if caliber == "" else None   # 旧 summary 回退；新链路缺字段则如实省略

    # ⛔ 冰点优先且**唯一**：当日实时冰点时，不管 AI 判了几只，卡头一律空仓（与正文的
    #    「命中·禁买」段同结论）。非 trusted 的昨收冰点已在 ice_ban 处排除。
    if ice_ban:
        tone = "市场很冷·不买·空仓"
    elif ai_caliber:
        tone = ("AI 空仓·观望" if ai_filled == 0 else
                "AI 只买1只·精选" if ai_filled == 1 else f"AI 买{ai_filled}只")
    elif buys >= 3:
        tone = "多只达标·可跟"
    elif buys >= 1:
        tone = "个别达标·精选"
    else:
        tone = "无达标·空仓观望"

    title = f"⚡ 财富密码·竞价验证·增量 {hhmm} | {tone}"
    blocks: list[dict] = []

    nav_txt = f"{nav}" if nav is not None else "—"
    if ai_caliber:
        _refs = []
        if ai_filled != ai_focus:
            _refs.append(f"重点看 {ai_focus} 只")
        if mech_buys is not None:
            _refs.append(f"量价够格 {mech_buys} 只")
        buy_txt = (f"AI 买入信号<text_tag color='red'>{ai_filled}</text_tag>"
                   + (f"<font color='grey'>（{'·'.join(_refs)}）</font>" if _refs else ""))
        if ice_ban:
            buy_txt += f" 命中<text_tag color='purple'>{hits}</text_tag>"
    else:
        buy_txt = (
            f"买入信号<text_tag color='red'>{buys}</text_tag> 命中<text_tag color='purple'>{hits}</text_tag>"
            if ice_ban else
            f"买入信号<text_tag color='red'>{buys}</text_tag>"
        )
    # 🚫 2026-10-21 16:39 用户「删除文案」点名这一行 → 默认不渲染；`VERIFY_CARD_HIDE=`
    #    清空即恢复。⛔ 保留 `buy_txt` 的计算（回退时立刻可用），只是不拼进卡面。
    if not _hide_block("head"):
        blocks.append(_div(
            f"**⚡ 财富密码·竞价验证** | 候选{total}只 {buy_txt} "
            f"已记录{covered}天 | 最新净值{nav_txt}"
        ))
        blocks.append(_hr())

    # 🚨 净值回撤线（退学炒股心法·语录62·2026-08-24）：回撤≥10% 硬警示 / ≥5% 软提示
    # 🚫 2026-10-21 用户截图点名不要 → 竞价卡不渲染。⛔ 回撤事实仍在底部 🎯 动态仓位块
    #    （「账户回撤 -X% → 实际压到：最多 N 只」），不丢；盘后卡照常保留。
    dd_blocks = None if _hide_block("dd") else _nav_drawdown_block(_load_metrics())
    if dd_blocks:
        blocks.extend(dd_blocks)
        blocks.append(_hr())

    # 🌊 行情位置（2026-10-21 P1）：放在「账户风险」之后、「今天买什么」之前——
    #    读者先知道自己在什么位置、这一段该用什么手法，再看具体买哪只。
    #    ⛔ 与底部 🎯 动态仓位不重复：本块给**方法论档位**（重仓/轻仓/空仓），
    #      底部给**今天实际能上几只、每只多少百分比**——一个是打法，一个是数量。
    # 🚫 2026-10-21 用户截图点名不要 → 竞价卡不渲染。⛔ 周期判断仍在跑（决策留痕
    #    `pool_call.meta.cycle_phase` + 盘后卡仍显示），只是早盘不再占两行；位置判断
    #    本来就已经落进选股与 9:25 裁决，卡面复述一遍是「同一件事实说两遍」。
    _cyc = ([] if _hide_block("phase")
            else _cycle_phase_block(asof=str(summary.get("trade_date10") or ""), verbose=False))
    if _cyc:
        blocks.extend(_cyc)
        blocks.append(_hr())

    # 🧭 风向标（2026-11-04 用户「基于这个风向标做联动」）：
    #    「这样子我们知道这个风向标它到底是一个什么情况，基于这个风向标，
    #      就像我们上一次做新华传媒和新华文轩一样联动！」
    # 每条线一个核心 + 它今天的竞价态度 → 直接回答「这条线今天能不能碰、能碰就看谁」。
    # ⛔ 只给结论与数据，不解释机制；逐票不再重复同一条线的风向标（同一事实只说一次）。
    _beacon = _beacon_block(summary.get("pool_call") or None)
    if _beacon:
        blocks.extend(_beacon)
        blocks.append(_hr())

    # 🧲 竞价一字 → 方向线索（2026-11-09）：放在「买什么」之前——
    #    先看今天哪条线在竞价就被点亮，再看自己那一只是不是在这条线上。
    _seal_blocks = _auction_seal_block(_seal)
    if _seal_blocks:
        blocks.extend(_seal_blocks)
        blocks.append(_hr())

    # 📉→📈 观察池竞价弱转强（2026-11-09）：紧接一字之后——
    #    一字答「今天哪条线被点亮」，弱转强答「我自己锁定的票里，谁在竞价被砸后爬回来」。
    #    ⛔ 纯解释层，不进名额 / 仓位 / 买入判定。
    _rev_blocks = _watch_reversal_block(_rev)
    if _rev_blocks:
        blocks.extend(_rev_blocks)
        blocks.append(_hr())

    # 🤖 今日买入（2026-09-11 立项「竞价卡要告诉我今天买什么 + 统计 AI 自己的收益」；
    # 2026-09-21 时点改造：买哪只改在**今日 09:25 竞价落地后**由 `core/pool_call` 在池内判，
    # 昨日盘后只出「预案」（基调/方向/bias）——「接力池＝提前缩范围，不是提前拍板」。
    blocks.extend(_ai_plan_block(_ai_raw, records, pool_summary=summary.get("pool_call"),
                                 ice_ban=ice_ban))

    # 🧠 AI 复核（2026-11-13 接入）：紧跟「今日买入」——同一个问题的两个视角。
    #    顺序不能反：机械裁决先定生死，模型只在这批池内票上降级。若把模型放到前面，
    #    读者会读成「模型先拍板、机械再复核」，因果颠倒。
    _arv = _ai_review_block(verify_result.get("ai_review"))
    if _arv:
        blocks.extend(_arv)

    # ⏱️ 昨收冰点 · 今晨已重判（2026-11-09）：把两个时点摆出来，防读者把昨收的冰点
    #    读成今天的结论。⛔ 只交代时点，不改任何结论。
    if ice_stale:
        _t_parts = []
        if isinstance(_env.get("regime_eod_score"), (int, float)):
            _t_parts.append(f"昨日收盘 {float(_env['regime_eod_score']):g} 分")
        if isinstance(_env.get("regime_score"), (int, float)):
            _t_parts.append(f"今晨 {float(_env['regime_score']):g} 分")
        _t_lbl = str(_env.get("regime_label") or "")
        if _t_parts:
            blocks.append(_div(
                f"<font color='grey'>⏱️ {' → '.join(_t_parts)}"
                f"{('（' + _t_lbl + '）') if _t_lbl else ''}：昨日冰点已解除，今天按当日读数执行</font>"))

    # 👑 核心弱转强（昨日连板核心·今日竞价·2026-08-14 用户定·情绪核心战法）
    core_blocks = _core_reversal_block()
    if core_blocks:
        blocks.extend(core_blocks)
        blocks.append(_hr())

    # 买入名单（＝账本口径）+ 机械达标全集：观察段去重与机械融合都要用，先算好。
    _bought = _bought_codes(records, _ai_view.get("views") if isinstance(_ai_view, dict) else None)
    _mech_all = [r for r in records if _mech_pass(r)]
    # ⛔ 融合模式下 `buy_rows` 只用于**旧版机械段**（分列）；观察段去重不能再用它，
    #    否则机械达标的观察票会被当成「已展示过」而整只消失（踩过的坑）。
    buy_rows = ([r for r in _mech_all if str(r.get("code") or "") not in _bought]
                if _mech_diff_only() else _mech_all)

    # 🧩 机械口径：融合 or 分列（2026-10-21 用户「机械量价参考 想办法 和AI判断融合在一起」）
    if _unify_mech():
        blocks.extend(_mech_fold_blocks(records))
    else:
        blocks.extend(_mech_reference_blocks(records, _ai_view, ai_caliber))

    # ⛔ 冰点命中票（2026-08-19 用户定·P1-6 语义澄清）：regime=ice 时 buy→hit，
    # 命中判定照算照推（super/expected 档位保留），但一律禁买不进场。
    hit_rows = [r for r in records
                if (r.get("performance") or {}).get("open_check", {}).get("decision_status") == "hit"]
    if hit_rows:
        # ── 昨收定调 + 今日开盘实况（2026-09-18 修·用户「这是昨天的判断·这块是不是也要动态一点」）──
        # 原句「⛔ 市场很冷·今天不买（做多气氛≤2分，一律空仓）」两处毛病：
        #   ① 「≤2分」是 ice **阈值**、不是实际值 —— 以阈值冒充今日事实（连昨日实际 1.0 分都没写）；
        #   ② 无时点标注 —— 昨日收盘档位被读成今日结论（今日实盘：09:38 1.9 分 → 11:00 已 7.5 分）。
        # 改为「结论 + 昨收实际值 + 今晨竞价读数」三段式：**结论不变**（AI 盘后决策时序铁律，
        # ⛔ 不因竞价转强就翻成买入），只把**时点**与**当日证据**交代清楚。
        _ys = fb_env.get("sentiment_score") if isinstance(fb_env, dict) else None
        _ys_txt = (f"做多气氛{float(_ys):.0f}分" if isinstance(_ys, (int, float))
                   else "做多气氛≤2分")
        blocks.append(_div(
            f"<text_tag color='red'>⛔ 空仓待命（昨日收盘定的：{_ys_txt}·市场很冷）</text_tag>"))
        _open_read = _today_open_read(records)
        if _open_read:
            blocks.append(_div(f"　　<font color='grey'>{_open_read}</font>"))
        for r in hit_rows:
            oc = (r.get("performance") or {}).get("open_check") or {}
            gap = oc.get("gap_pct")
            amt = oc.get("auction_amount_yi")
            need = oc.get("auction_amount_need_yi")
            sig = (oc.get("signal_label") or oc.get("decision_label")
                   or _status_plain(oc.get("status")))
            gap_txt = f"{gap:+.2f}%" if isinstance(gap, (int, float)) else "—"
            amt_txt = f"{amt:.2f}" if isinstance(amt, (int, float)) else "—"
            need_txt = f"{need:.2f}" if isinstance(need, (int, float)) else "—"
            bucket = str(r.get("bucket_label") or "")
            hl_color = C_WATCH   # 🎨 非买入票统一淡蓝（2026-10-21 降权）
            blocks.append(_div(
                f"⛔ {_stock_hl_boards(r, hl_color)} {sig} {_gap_tag(gap)} <text_tag color='red'>不买</text_tag> "
                + _sector_support_tag(r)
            ))
            blocks.append(_div(
                # ⛔ 缩量胶囊必须贴在 </font> 之外（宪法 ⓪-10：禁 font 套 text_tag）
                f"　　<font color='grey'>竞价{gap_txt} 额{amt_txt}/{need_txt}亿</font>"
                + _vol_weak_flag(oc)
                + f"<font color='grey'>｜{bucket}｜{_theme_dual_tag(r)}</font>"
            ))
            _gene = _hist_gene_line(r)               # 📈 历史股性
            if _gene:
                blocks.append(_div(_gene))
            _ai = _ai_view_line(r, _ai_view)         # 🤖 AI 判断
            if _ai:
                blocks.append(_div(_ai))
        blocks.append(_hr())

    # 观察区（pending：涨幅量能达标但 gap>5% / 高于预期未达超预期夹缝区 / 微红偏弱 → 黄色，等承接确认）
    pending_all = [r for r in records
                   if (r.get("performance") or {}).get("open_check", {}).get("decision_status") == "pending"]
    if _unify_mech():
        # 🧩 融合模式的分组依据 = **AI 判定**，不是机械 decision_status。
        # ⛔ 踩过的坑：宁波东力这类「机械达标（status=buy）+ AI 判观察」的票，
        #    原来只在 📊 机械段出现；机械段一撤，按 `pending` 筛就一只都筛不到 → 整只消失。
        #    三组口径：买入=participate（在 🤖 段）／观察=watch／放弃=skip。
        _av_of = lambda _r: str(_oc_of(_r).get("ai_view") or "")
        pending_all = [r for r in records if _av_of(r) in ("watch", "skip")]
    # 🪶 观察区差集（2026-10-21）：已在上方「今日买入」或「机械差集」出现过的票不再列一次。
    #    ⛔ 但「降权≠不显示」铁律仍守：观察区**整段不消失**，只去重 + 压成判定行。
    # ⛔ 融合模式：机械段已不再逐票展示 → 去重只能排除「已买入」那一批。
    #    用 `buy_rows`（机械达标但没买）去重会把宁波东力这类票**整只删掉**（本次重构踩过）。
    _shown = (_bought if _unify_mech()
              else _bought | {str(r.get("code") or "") for r in buy_rows})
    pending_rows = ([r for r in pending_all if str(r.get("code") or "") not in _shown]
                    if _watch_brief() else pending_all)
    _watch_dup = len(pending_all) - len(pending_rows) if _watch_brief() else 0
    if pending_rows:
        # ⛔ 段名必须与票的判定一致（2026-09-18 用户「明明说观察又说放弃·这两个自己打架了」）：
        #    原来整段叫「🟡 观察」，里面却挂着 AI 判「放弃」的票 → 段名与逐票判定自相矛盾，
        #    读者看到「观察」段里的票写着「放弃」会以为系统没想清楚。
        #    现按 AI 终态拆两段：先「观察」后「放弃」——票数不变、一只不少，只改归属。
        _skip_rows = [r for r in pending_rows
                      if str(_oc_of(r).get("ai_view") or "") == "skip"]
        _watch_rows = [r for r in pending_rows
                       if str(_oc_of(r).get("ai_view") or "") != "skip"]
        _dup_txt = (f"<font color='grey'>（另有 {_watch_dup} 只已在上方出现过，不重复列）</font>"
                    if _watch_dup else "")

        def _render_pending(rows: list[dict], head: str, icon: str, *,
                            group: bool = False) -> None:
            if not rows:
                return
            blocks.append(_div(head + _dup_txt))
            # 按晋级档分组（可选）：seq = [(档名或 None, 行)]
            _cnt: dict[str, int] = {}
            if group and _tier_groups_on():
                ordered = sorted(rows, key=_tier_order_key)
                for _x in ordered:
                    _t = _tier_label(_x)
                    _cnt[_t] = _cnt.get(_t, 0) + 1
                seq: list[tuple[str | None, dict]] = []
                _prev: str | None = None
                for _x in ordered:
                    _t = _tier_label(_x)
                    seq.append((_t if _t != _prev else None, _x))
                    _prev = _t
            else:
                seq = [(None, x) for x in rows]
            for _lbl, r in seq:
                if _lbl is not None:
                    blocks.append(_div(
                        f"<font color='grey'>── {_lbl} · {_cnt.get(_lbl, 0)} 只</font>"))
                oc = (r.get("performance") or {}).get("open_check") or {}
                gap = oc.get("gap_pct")
                amt = oc.get("auction_amount_yi")
                need = oc.get("auction_amount_need_yi")
                sig = oc.get("signal_label") or oc.get("decision_label") or ""
                gap_txt = f"{gap:+.2f}%" if isinstance(gap, (int, float)) else "—"
                amt_txt = f"{amt:.2f}" if isinstance(amt, (int, float)) else "—"
                need_txt = f"{need:.2f}" if isinstance(need, (int, float)) else "—"
                bucket = str(r.get("bucket_label") or "")
                hl_color = C_WATCH   # 🎨 非买入票统一淡蓝（2026-10-21 降权）
                exp_ref, verdict = _exp_verdict(r, oc)
                dv_tag = r.get("daily_verdict", {}).get("tag", "")
                # 风险提示统一由下方点评「⚠️ 风险」段呈现（2026-09-11·同买入段口径）
                blocks.append(_div(
                    f"{icon} {_stock_hl_boards(r, hl_color)} {sig} {_gap_tag(gap)}"
                    # 🧩 融合（2026-10-21）：机械口径与 AI 判定**并排**在一行——
                    #    `📊 机械达标` 说机械怎么看、第三行的 `🤖 今日9:25判：观察` 说 AI 怎么定，
                    #    中间夹 `🐣 跟风拦下` / `🛡️ 防守拦下` 解释「机械达标为什么没买」。
                    + _mech_badge(r)
                    + _gate_badge(oc)
                    + _outpool_badge(r)
                    + _sp(_dragon_tag(oc))
                    + _sp(_tier_tag(oc))
                    # ⛔ `✅符合预期` 是「gap 对预期」的判定，与「机械能不能买」不是一回事；
                    #    机械没达标时两者并排会读成自相矛盾（科森科技实证）→ 只在机械达标时给。
                    #    `_verdict_tag` 自带前导空格，⛔ 不要再套 `_sp`（会出两个空格）。
                    + _verdict_tag(_v_tag_for(r, verdict))
                    + _sector_support_tag(r)
                ))
                if exp_ref:
                    blocks.append(_div(
                        # ⛔ 缩量胶囊必须贴在 </font> 之外（宪法 ⓪-10：禁 font 套 text_tag）
                        f"　　<font color='grey'>{exp_ref}｜额{amt_txt}/{need_txt}亿</font>"
                        + _vol_weak_flag(oc)
                        + f"<font color='grey'>｜{bucket}｜{_theme_dual_tag(r)}｜"
                        + (f" {dv_tag}" if dv_tag else "")
                        + "</font>"
                    ))
                # 🪶 精简模式（2026-10-21）：观察＝今天不动手的票 → **只留标题行 + 指标行**，
                #    历史基因 / AI 理由 / 点评三段全部不渲染（走势+题材+股性属讲故事）。
                #    ⛔ 判定理由已经写在标题行的 sig 里（如「超预期·辨真假高开」「高开超5%」），
                #      所以「为什么没买」这个信息没丢。
                if _watch_brief():
                    # ⛔ 档位 1/2 都必须带「AI 判什么」——精简的是理由长度，不是判断本身
                    #    （2026-10-21 用户「AI 的放弃判断被你抹掉了」）。档位 1 给完整理由。
                    _j25 = _j25_line(oc, why_limit=70 if _two_line_rows() else 0)
                    if _j25:
                        blocks.append(_div(_j25))
                    continue
                _gene = _hist_gene_line(r)               # 📈 历史股性（verify 卡观察区）
                if _gene:
                    blocks.append(_div(_gene))
                # 简洁模式（2026-10-21）：观察区＝今天不动手的票 → 只留「判定 + 风险一句」，
                # 走势/题材/股性三段不再展开（股性已由上一行 gene 覆盖）。VERIFY_CARD_COMPACT=0 回退。
                _cmp = _compact_on()
                _ai = _ai_view_line(r, _ai_view, compact=_cmp)         # 🤖 AI 判断（合并进竞价验证）
                if _ai:
                    blocks.append(_div(_ai))
                if r.get("narrative"):
                    _nb = _narr_render(r["narrative"], prev=True,
                                       drop_labels=_compact_drops(oc, bool(_gene)))
                    if _nb:
                        blocks.append(_div(_nb))

        _render_pending(_watch_rows, "**🟡 观察（还没到能买的程度·再看一会儿）**", "🟡",
                        group=True)
        # 🟢 A股语义＝跌/回避。AI 判「放弃」的票单独成段，⛔ 不混进「观察」段自相矛盾。
        # ⛔ 段标题 emoji 也要去绿：🟢 在 A 股语境里读作"跌/退潮"，
        #    用「⚪ 放弃」表达"这里不用动手"，与配色的灰阶一致。
        _render_pending(_skip_rows, "**⚪ 放弃（AI 判不接·逐票理由在下一行）**", "⚪")
        blocks.append(_hr())

    # 其余未达标（reject：涨幅或量能不达标 → 灰色简列）
    # 其余：真·未达标（reject）与 报价缺失（unavailable）**分开**——2026-08-14 审计：
    # 原实现把 unavailable 混入"未达标"（语义错误：没报价≠不达标），且 [:12] 静默截断、decision_label 被吞。
    other = [r for r in records
             if (r.get("performance") or {}).get("open_check", {}).get("decision_status") == "reject"]
    if _unify_mech():
        # 🧩 融合模式的分组必须**全覆盖**（⛔ 漏一只就是卡面事故，实测踩过两次）：
        #    · participate → 🤖 今日买入
        #    · watch / skip → 🟡 观察 / 🟢 放弃
        #    · unavailable → ⚪ 报价缺失
        #    · hit → ⛔ 空仓待命段
        #    · **其余（含 pending / buy / reject 但 AI 没给结论的）→ 本段兜底**
        #  原写法 `decision_status == "reject"` 会让「pending 但 AI 无结论」的票凭空消失
        #  （实测 test_golden 金健米业：pending + 无 ai_view → 卡上一片空白）。
        _grouped = {id(r) for r in records
                    if str(_oc_of(r).get("ai_view") or "") in ("participate", "watch", "skip")}
        other = [r for r in records
                 if id(r) not in _grouped
                 and str(_oc_of(r).get("decision_status") or "") not in ("unavailable", "hit")]
    missing = [r for r in records
               if (r.get("performance") or {}).get("open_check", {}).get("decision_status") == "unavailable"]

    def _row_div(prefix, r, lbl_pri="decision"):
        oc = (r.get("performance") or {}).get("open_check") or {}
        gap = oc.get("gap_pct")
        if lbl_pri == "decision":
            lbl = oc.get("decision_label") or oc.get("signal_label") or "—"
        else:
            lbl = oc.get("signal_label") or oc.get("decision_label") or "—"
        if _unify_mech():
            # 🧩 融合后本段是「AI 未给结论」兜底段，读者要看的是**竞价信号本身**
            #    （如「孤军高开·防被砸」），而不是「观察/放弃」这种结论词——
            #    有 signal_label 时优先它（实测金健米业：只显示「观察」会丢掉孤军警示）。
            lbl = oc.get("signal_label") or lbl
        amt = oc.get("auction_amount_yi")
        need = oc.get("auction_amount_need_yi")
        amt_txt = f"{amt:.2f}" if isinstance(amt, (int, float)) else "—"
        need_txt = f"{need:.2f}" if isinstance(need, (int, float)) else "—"
        exp = r.get("expectation") or {}
        erng = exp.get("expected_range")
        exp_txt = f"预期{erng[0]}~{erng[1]}%" if isinstance(erng, (list, tuple)) and len(erng) == 2 else ""
        return _div(
            f"{prefix} {_hl(r.get('name',''), 'neutral')}{_boards_txt(r)} {lbl} {_gap_tag(gap)}"
            f" 额{amt_txt}/{need_txt}亿{_vol_weak_flag(oc)}"
            + (f"（{exp_txt}）" if exp_txt else "")
            + _mech_badge(r)   # 🧩 融合：这一组基本都是「量价也没达标」→ 与 AI 判定互相印证
            + _outpool_badge(r)  # 🔍 池外龙头校验（仅「后排」时显示）
            + _sector_support_tag(r)   # 🧲 孤军/助攻（自带前导空格）
        )

    if other and _show_reject_rows():
        _ohead = (f"**⚪ 其余（AI 未给结论）**（{len(other)} 只·高开红/低开绿·只列竞价读数）"
                  if _unify_mech() else
                  f"**⚪ 其余没达标**（{len(other)} 只·高开红/低开绿·AI 判断照列）")
        blocks.append(_div(_ohead))
        for r in other:
            blocks.append(_row_div("⚪", r, lbl_pri="decision"))
            if _lean_on():
                # 🪶 精简档：只加「AI 判什么 + 一句理由」——⛔ 不再铺历史基因与长叙述，
                #    但也**不许**只显示指标就把 AI 判断吞掉（用户 2026-10-21 质问点）。
                _j25 = _j25_line(_oc_of(r), why_limit=50 if _two_line_rows() else 0)
                if _j25:
                    blocks.append(_div(_j25))
                continue
            _gene = _hist_gene_line(r)               # 📈 历史股性（2026-09-10 用户「竞价验证这里没有历史股性也要加上」）
            if _gene:
                blocks.append(_div(_gene))
            _ai = _ai_view_line(r, _ai_view, compact=_compact_on())   # 🤖 AI 判断（没达标的票只留判定）
            if _ai:
                blocks.append(_div(_ai))
    if missing and _show_pool_out():
        blocks.append(_div(f"**⚪ 报价缺失·未判定**（{len(missing)} 只·未拿到 9:25 报价，不构成判断）"))
        for r in missing:
            blocks.append(_row_div("⚪", r, lbl_pri="signal"))

    # 策略指标（最新）
    # 🚫 2026-10-21 用户截图点名不要「📈 已记录24次，胜率41.7%…」→ 竞价卡整行不渲染。
    #    ⛔ 卡头「已记录N天 | 最新净值」仍在（那是覆盖度不是战绩）；完整战绩在复盘卡。
    m = None if _hide_block("pnl") else _load_metrics()
    if m:
        nd = (m.get("metrics") or {}).get("next_day", {}).get("tradable") or {}
        wr = nd.get("win_rate")
        ar = nd.get("avg_return")
        cov = nd.get("covered")
        nav_latest = m.get("nav_latest")
        wr_txt = f"{wr}%" if wr is not None else "—"
        ar_txt = f"{ar:+.2f}%" if ar is not None else "—"
        cov_txt = str(cov) if cov is not None else "—"
        nav_txt = f"{nav_latest}" if nav_latest is not None else "—"
        # 语义色板（2026-08-16 用户定）：战绩收益正红负绿，负收益不再整行红（恶化禁红）
        blocks.append(_hr())
        line = f"📈 已记录{cov_txt}次，胜率{wr_txt}，当前收益{ar_txt}，当前净值{nav_txt}"
        if ar is not None and ar < 0:
            blocks.append(_div(f"<text_tag color='green'>{line}</text_tag>"))
        elif ar is not None and ar >= 0:
            blocks.append(_div(f"<text_tag color='red'>{line}</text_tag>"))
        else:
            blocks.append(_div(line))
        _cn = _caliber_note(m)          # 口径分界标注（2026-09-14 起 AI 口径）
        if _cn:
            blocks.append(_div(f"<font color='grey'>{_cn}</font>"))

    # 全局拟人旁白（随 verify_result 传入；2026-08-13 拆行 → 2026-09-11 语义分段，与盘后卡一致）
    # 盘面基准日标注（2026-09-14）：旁白透传自上一交易日的候选文件 → 显式标明基准日，防止把
    # 上一交易日收盘盘面读成当日盘面（用户口径：用上一交易日的即可，不必重取当日数据）。
    # ⏱️ 时点平移（2026-09-18）：旁白撰写日 ≠ 读卡日时，正文时点词也要跟着平移 —— 否则出现
    #    「（09-17 周四收盘盘面）… **今日盘面**：今天市场明显转冷」这种**同卡自相矛盾**
    #    （09-18 实证：那天其实转暖、78 家涨停）。判据 = 基准日标注是否非空（非空 ⇒ 跨日）。
    # 🚫 2026-10-21 用户截图点名不要「🗣️ 全局 … 🎯 操作思路」→ 竞价卡不渲染。
    #    ⛔ 操作总纲没丢：底部 🎯 动态仓位块给出「行情强弱 + 今天最多几只 + 每只多少」，
    #      比一段散文更可执行；盘后卡仍完整保留全局旁白（复盘场景需要）。
    gn = None if _hide_block("global") else verify_result.get("global_narrative")
    if gn:
        _sum = verify_result.get("summary") or {}
        # 撰写日：优先 narrative_date（当日 --narratives 注入时由 run_wealth_code 显式写），
        # 否则回落到候选文件日期（= 上一交易日盘后撰写）。
        _narr_d = verify_result.get("narrative_date") or _sum.get("date10")
        _lbl = _narr_basis_label(_narr_d, _sum.get("trade_date10"))
        # 🪶 精简模式（2026-10-21）：只留「操作思路」段——账户段与盘面段已分别由
        #    卡头 📈 战绩行、📡 大盘环境 + 🌊 行情位置 讲过（同一件事实只说一次）。
        _gn_src = _gn_compact(gn) if _narrative_brief() else gn
        _gn_body = _narr_render(_gn_src, prev=bool(_lbl)).strip()
        if _gn_body:
            blocks.append(_div(f"🗣️ **全局**{_lbl}\n{_gn_body}"))

    # 未达标个股点评：⛔ 2026-08-14 用户要求底部不显示「点评」块，已删除（其余未达标行已含简列）

    # 🎯 今日接力仓位建议（2026-10-13 用户需求·动态仓位·relay_position 唯一判据源）
    # 用竞价验证卡的真实候选 + 09:25 竞价涨幅合成「今晨竞价」分量；昨日强势 / 题材沉淀 /
    # 净值回撤由 relay_position 读本系统真实文件。失败 → 诚实省略（不阻断出卡）。
    try:
        from core.relay_position import build_plan_from_files as _build_relay_plan

        def _to_float(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        _recs = (verify_result.get("payload") or {}).get("records") or []
        _cands = {"relay": [
            {"code": str(r.get("code") or "").strip(),
             "name": str(r.get("name") or ""),
             "theme_name": str(r.get("theme") or r.get("theme_name") or "").strip()}
            for r in _recs if str(r.get("code") or "").strip()]}
        _quotes: dict[str, dict] = {}
        for r in _recs:
            _code = str(r.get("code") or "").strip()
            if len(_code) != 6:
                continue
            _oc = (r.get("performance") or {}).get("open_check") or {}
            _gp = _to_float(_oc.get("gap_pct"))
            if _gp is None:
                continue
            _quotes[_code] = {"prev_close": 100.0, "auction_price": round(100.0 * (1.0 + _gp / 100.0), 4)}
        _rp_plan = _build_relay_plan(ROOT.parent, candidates=_cands, quotes=_quotes)
        # 🎯 2026-11-04：把 09:25 冻结的 pool_call meta 交给渲染层 —— 单票场景的单票上限、
        #    名额放宽到 2 的实际只数都取自这份**真实参与数**（⛔ 渲染层不自己猜名额）。
        _rp_block = _relay_position_block(_rp_plan, pool_meta=(summary.get("pool_call") or None),
                                          ice_ban=ice_ban)
        if _rp_block:
            blocks.append(_hr())
            blocks.extend(_rp_block)
    except Exception as _e:                       # 诚实降级·绝不阻断出卡
        print(f"[verify_card] 动态仓位块跳过：{_e}")

    # 🕘 时点声明（2026-11-11 用户：「不能把竞价的强势结论原封不动带到下跌后」）
    #    ⛔ 只声明边界，不加功能：系统确实是 09:25 一次裁决，盘中机会交人工。
    blocks.append(_note("🕘 以上判定只对 09:25 有效；开盘后的回落/走强是另一套判断，"
                        "系统不做盘中二次买入 · 交人工"))
    blocks += _footer(hhmm)
    # 🎨 卡头颜色（2026-10-22 08:59 用户「竞价卡的标题颜色变为紫色！」）：
    #   ⛔ 这是**整卡标题条**的颜色（飞书 card header template），与正文里
    #     `text_tag` 的配色是两套东西 —— 正文仍守「红块/灰块/灰字」三形态，
    #     ⛔ 不要因为改卡头就去动 `C_BUY/C_WATCH/C_HINT`。
    #   `purple` 是飞书 header 合法模板色（template 枚举与 text_tag 同名不必一致）。
    return {"title": title, "color": "purple", "elements": blocks}


# ── 推送（复用狸猫猎手 push_card.py 单入口）──────────────

def sanitize_card(card: dict) -> dict:
    """递归清洗卡面自由文本（单一源：`scripts/card_copy_rules`）。

    ⚠️ 为什么必须在**渲染出口**兜这一道（2026-11-13 实锤）：
    - 卡面大量文案不是模板写的，而是从**冻结件**透传的（`pool_call-<day>.json` 的 `reason`、
      `ai_advice` 的 note…）。冻结件按铁律 ⛔ 不许改。
    - 于是「源码已经改好的措辞」对**当天**无效：11-13 上午 `口径：` 命中
      `card_copy_rules.PLAIN_FAIL` → `push_card` 硬阻断 → 一条 HTTP 没发出 → 全天无卡。
    - 修源码只对次日新冻结件生效；渲染层清洗才对当天生效，两者都需要。

    只替换 `PLAIN_HINT` 里有白话映射的词；洗不掉的仍由最终 lint 阻断（⛔ 不放松门禁）。
    """
    try:
        if str(LIMAO_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(LIMAO_SCRIPTS))
        import card_copy_rules as CCR
    except Exception:  # noqa: BLE001
        return card

    def _walk(o):
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_walk(v) for v in o]
        if isinstance(o, str):
            return CCR.sanitize(o)
        return o

    try:
        return _walk(card)
    except Exception:  # noqa: BLE001
        return card


def push_card_file(card: dict, *, webhook: str | None = None, dry_run: bool = False) -> tuple[bool, str]:
    """
    写 cache 卡片 JSON，调用狸猫猎手 scripts/push_card.py 单入口推送。
    返回 (ok, output)。推送失败不影响本地数据（已落盘）。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ts = _now_bj().strftime("%Y%m%d_%H%M%S")
    path = CACHE_DIR / f"card_{ts}.json"
    card = sanitize_card(card)          # 黑话清洗（冻结件透传的旧措辞只在此处可救）
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")

    cmd = [sys.executable, str(LIMAO_SCRIPTS / "push_card.py"), str(path)]
    if webhook:
        cmd += ["--webhook", webhook]
    if dry_run:
        cmd += ["--dry-run"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return False, f"推送异常: {e}"


# ── 盘前预测卡（固定模板 · 06:06 自动化调用）──────────────
# 设计：LLM 经 财神-选股 + 财神-复盘 + 股票题材猎手 技能抓取/核验数据后，
# 把「动态内容」(外盘/催化/主线/切换日/持仓/风险/心法) 组装进 data dict，
# 本函数 FIXED 渲染 7 模块结构 + 读本地专有数据(yesterday_zt/theme_rotation) + 自查校验。
#
# 铁律（2026-08-14 固化）：
# ① 配色：推送卡颜色一律 <text_tag> 胶囊（禁 <font>，实测 font 在飞书不渲染）；
#    减色：个股涨跌幅数字纯文本，仅语义标签用胶囊。
# ② 数据真实性：回流模块读 cache/theme_rotation.json 真实引擎输出（无数据则红标"无板块资金流数据"，
#    不编置信度）；昨日反馈读 cache/yesterday_zt.json 真实快照；晋级率/核按钮来自 theme_rotation
#    的 market_context（仅 post-9:26 可用，盘前若 null 诚实标"待9:26更新"，严禁编造）。
# ③ 隐私：群推卡禁止读取 cache/holdings.json、禁止在 card 任何位置出现个股持仓/仓位。
# ④ 语言一致性：模块顺序/标题/页脚固定，保证每次输出结构一致。
_STOCK_CODE_RE = _re.compile(r"\d{6}")


def _panqian_yesterday_zt() -> dict | None:
    p = ROOT.parent / "cache" / "yesterday_zt.json"   # 真实专有数据在 狸猫猎手/cache
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _panqian_theme_rotation() -> dict | None:
    p = ROOT.parent / "cache" / "theme_rotation.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _flow_label(fv) -> tuple[str, str | None]:
    """把近3日板块资金流 list 渲染成带语义标签的文本；全 null → 红标无数据。"""
    if not fv or all(v is None for v in fv):
        return "资金：<text_tag color='red'>无板块资金流数据</text_tag>", "无数据"
    nums = [v for v in fv if v is not None]
    s = " / ".join(("%.1f" % v if v is not None else "—") for v in fv)
    if nums and all(v < 0 for v in nums):
        tag = "<text_tag color='green'>持续净流出</text_tag>"
    elif nums and all(v > 0 for v in nums):
        tag = "<text_tag color='red'>持续净流入</text_tag>"
    else:
        tag = "<text_tag color='neutral'>流入分化</text_tag>"
    return f"资金(近3日·亿)：{s} ｜ {tag}", "有数据"


def build_panqian_card(data: dict[str, Any]) -> dict:
    """生成盘前预测飞书长卡。data 由 06:06 自动化经技能组装，结构见 财神-选股·盘前预测章节。

    data 字段：
      date(str) 切换日/外盘/催化/主线 等动态内容 + switch_day + holdings_note + risks + mindset
    内部读取（专有数据，保证真实）：
      cache/yesterday_zt.json（昨日涨停反馈真实快照）
      cache/theme_rotation.json（题材回流真实引擎输出）
    """
    hhmm = _now_bj().strftime("%H:%M")
    date = str(data.get("date") or "")
    mmdd = date[5:10] if len(date) >= 10 else (date or "M-D")
    sd = data.get("switch_day") or {}
    gm = data.get("global_market") or {}
    cats = data.get("catalysts") or []
    lines = data.get("main_lines") or []
    anchor = data.get("switch_anchor") or ""
    holdings = str(data.get("holdings_note") or "当前无 tracked 持仓，板块级应对。")
    risks = data.get("risks") or []
    mindset = data.get("mindset") or ""

    tone = data.get("title_tone") or sd.get("note") or "盘前预判"
    title = f"📊 盘前预测·全量 {mmdd} | {tone[:24]}"

    blocks: list[dict] = []

    # 模块① 切换日 / 盘前定调（分行布局：标签行→状态 chips→旧龙/新龙各一行→结论；不挤一行）
    # ⛔ note 语义段强制拆行（2026-08-27 UI 优化）：note 契约用「｜」「；」分隔语义段
    # （承接/情绪演变/轨迹/基调），渲染层一律换行；「——」不拆（防括号内转折误伤）。
    _note_fmt = lambda s: _re.sub(r"\n{2,}", "\n", str(s).replace("｜", "\n").replace("；", "\n")).strip()
    if sd.get("is_switch"):
        s1 = (
            f"<text_tag color='orange'>🔀 切换日</text_tag> 旧龙死·新龙生 = 新方向试错\n\n"
            f"<text_tag color='turquoise'>{sd.get('regime','')}</text_tag> "
            f"{_sent_tag(sd.get('sentiment',''))} "
            f"{_promo_tag(sd.get('promo_rate',''))}\n\n"
            f"旧龙死：{sd.get('old_dragon','')}\n"
            f"新龙生：{sd.get('new_dir','')}\n\n"
            f"{_note_fmt(sd.get('note',''))}"
        )
    else:
        s1 = (
            f"<text_tag color='neutral'>📊 盘前定调</text_tag> {_note_fmt(sd.get('note') or tone)}\n\n"
            f"<text_tag color='turquoise'>{sd.get('regime','')}</text_tag> "
            f"{_sent_tag(sd.get('sentiment',''))}"
        )
    blocks.append(_div(s1))
    # 🌊 行情位置（2026-10-21 P1）：盘前只给两行——「在什么位置」+「这一段该干什么」。
    #    ⛔ 不复述 regime/情绪/晋级率：模块① 的切换日段已经讲过（同一件事实只说一次）。
    #    ⛔ 不占模块编号：模块①~⑦ 的结构是铁律（语言一致性），本块挂在定调之后作补充。
    _cyc = _cycle_phase_block(asof=date, verbose=False)
    if _cyc:
        blocks.extend(_cyc)
    blocks.append(_hr())

    # 🌡️ 今日预判情绪分（2026-11-09 新增）——「情绪对齐」回测的唯一数据源
    #
    # 为什么必须在卡面写出来：`backtest_engine.backtest_sentiment_align` 要用
    # 「盘前预判情绪分 − 当日实际情绪分」，而过去 8 期全 null，卡在**根本没有预判分**。
    # 卡片上原有的「情绪 X/10」描述的是**昨日实际**（如「退潮确认：情绪1.0/10」），
    # 拿它当预判等于用昨天的实际减今天的实际，会凭空造出一条偏差曲线。
    # 所以这里只认 `sentiment_predict` 结构化字段，⛔ 不从正文猜。
    # ⛔ 不占模块编号（模块①~⑦ 是铁律），挂在定调之后作补充。
    _sp = data.get("sentiment_predict") or {}
    _sp_mid = _sp.get("mid") if isinstance(_sp, dict) else None
    if isinstance(_sp_mid, (int, float)) and 0 < float(_sp_mid) <= 10:
        _lo, _hi = _sp.get("lo"), _sp.get("hi")
        _rng = ""
        if isinstance(_lo, (int, float)) and isinstance(_hi, (int, float)) and float(_lo) != float(_hi):
            _rng = f"（区间 {float(_lo):g}~{float(_hi):g}）"
        _basis = str(_sp.get("basis") or "").strip()
        _line = f"<text_tag color='neutral'>🌡️ 今日预判情绪 {float(_sp_mid):g}/10{_rng}</text_tag>"
        if _basis:
            _line += f"\n{_basis[:80]}"
        blocks.append(_div(_line))
        blocks.append(_hr())
    else:
        # ⛔ 不阻断推送（早上没卡比少一个指标严重得多），但要留痕：
        #    当天回测的「情绪对齐」会记 contract_missing。
        print("[panqian] ⚠️ 缺 sentiment_predict（今日预判情绪分）→ 回测『情绪对齐』本期不可算",
              file=sys.stderr)

    # 模块② 外盘联动（2×2 网格 + 各题材独立块；个股数字纯文本减色；⛔ 题材内一行一票，禁 ｜ 连排）
    # ⛔ 外盘日期铁律（2026-08-19 事故）：最新可用收盘 = 美东(BJ-1天)·夏令时差12h；
    # 北京当日凌晨04:00已收美东前一日盘，故动态取 BJ-1 计算美东交易日，杜绝硬编码旧日期。
    _bj = _now_bj()
    _us = _bj - timedelta(days=1)
    # ⛔ 周末回退铁律（2026-08-31 周一执行补丁）：美股周六/周日休市——北京周一凌晨执行时
    # _us 会落在周日，标题错误显示"周日收盘"；须回退到最近周五（最新可用收盘=美东T-1交易日）。
    while _us.weekday() >= 5:
        _us -= timedelta(days=1)
    _wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][_us.weekday()]
    _us_label = f"{_us.month}/{_us.day} {_wd}"
    blocks.append(_div(f"<text_tag color='neutral'>🌐 外盘联动（隔夜美股 {_us_label} 收盘·实时核验）</text_tag>"))
    idx = gm.get("indices") or []
    if idx:
        cells = [f"**{i['name']}**\n{i['pct']:+.2f}%" + (f" · {i['note']}" if i.get('note') else "")
                 for i in idx[:4]]
        blocks.append({"tag": "div", "fields": [{"is_short": True, "text": _md(c)} for c in cells]})
    if gm.get("macro_note"):
        # 2026-08-27 UI 优化：宏观概要按分句拆行（；。→换行），避免一整段堆叠；· 保留不拆（防误伤个股名）
        macro_txt = gm["macro_note"].replace("；", "\n").replace("。", "\n").strip()
        blocks.append(_div(macro_txt))
    for b in gm.get("blocks") or []:
        # 一行一票（2026-08-27 UI 优化：原 ｜ 连排 9 只挤一行→每只独立换行）
        lines_txt = "\n".join(
            f"{l['name']} {l['pct']:+.2f}%" + (f" {l['map']}" if l.get('map') else "")
            for l in b.get("lines", []))
        bnote = f"（{b['note']}）" if b.get("note") else ""
        blocks.append(_div(
            f"<text_tag color='{b.get('color','neutral')}'>{b['tag']}</text_tag>\n"
            f"{bnote}\n{lines_txt}"))
    blocks.append(_hr())

    # 模块③ 催化速览（事件→产业链→个股，经 股票题材猎手 实时核验；紧邻今日主线→映射即结论）
    if cats:
        blocks.append(_div("<text_tag color='neutral'>🔍 催化速览（事件→A股映射·实时核验）</text_tag>"))
        for c in cats:
            # 2026-08-27 UI 优化：map 长文按转折(——)/分句(；)拆行，避免整段堆叠
            m = _re.sub(r"\n{2,}", "\n", str(c.get("map") or "").replace("——", "\n").replace("；", "\n")).strip()
            blocks.append(_div(
                f"<text_tag color='{c.get('color','neutral')}'>{c.get('emoji','')} {c['title']}</text_tag>\n{m}"))
        blocks.append(_hr())

    # 模块④ 今日预判主线（⭐ 标题加粗 + 理由换行分行；2026-08-27 UI 优化：原标题与长文同段）
    if lines:
        blocks.append(_div("<text_tag color='neutral'>🔥 今日预判主线</text_tag>"))
        for ml in lines:
            stars = "⭐" * int(ml.get("stars", 3))
            r = _re.sub(r"\n{2,}", "\n", str(ml.get("reason") or "").replace("——", "\n").replace("；", "\n")).strip()
            blocks.append(_div(f"{stars} **{ml['name']}**\n{r}"))
        if anchor:
            blocks.append(_div(f"<text_tag color='neutral'>📌 切换日验证锚</text_tag> {anchor}"))
        blocks.append(_hr())

    # 模块⑤ 持仓应对（隐私铁律：不读 holdings.json，不点名）
    blocks.append(_div("<text_tag color='neutral'>💼 持仓应对</text_tag>"))
    blocks.append(_div(holdings))
    blocks.append(_hr())

    # 模块⑥ 风险提示（带序号，一条一行）
    if risks:
        blocks.append(_div("<text_tag color='neutral'>📎 风险提示</text_tag>"))
        for i, r in enumerate(risks, 1):
            blocks.append(_div(f"{i}. {r}"))
        blocks.append(_hr())

    # 页脚三行（盘前长卡：来源 + 免责 + 心法）
    # 心法置于免责声明**之后**、样式与免责一致（note 灰色小字）——用户 2026-08-14 定。
    blocks.append(_note("📡 选股宝+东财+猎手 | " + hhmm + " | 盘前预测"))
    blocks.append(_note("⚠️ 本报告基于公开信息分析，投资需谨慎"))
    if mindset:
        blocks.append(_note(mindset))

    return {"title": title, "color": "indigo", "elements": blocks}


def validate_panqian_card(card: dict, data: dict) -> tuple[bool, list[str]]:
    """自查校验：数据精准性 / 饱满度 / 逻辑严密性 / 隐私 / 配色。
    返回 (ok, issues)；任一 issue 即不应推送。"""
    issues: list[str] = []
    txt = json.dumps(card, ensure_ascii=False)

    # 饱满度：核心模块齐全（2026-08-27 精简版：去掉昨日涨停反馈/题材回流/资金流向三模块；
    # 早盘只保留 隔夜消息(外盘+催化)+今日主线+持仓 五模块——用户「更关注隔夜消息和今日主线」）
    core = ["外盘联动", "今日预判主线", "持仓应对"]
    for m in core:
        if m not in txt:
            issues.append(f"饱满度缺失核心模块：{m}")
    if "切换日" not in txt and "盘前定调" not in txt:
        issues.append("饱满度缺失定调模块（切换日/盘前定调）")

    # 精准性：外盘指数/个股必须带数值
    gm = data.get("global_market") or {}
    for i in gm.get("indices") or []:
        if not isinstance(i.get("pct"), (int, float)):
            issues.append(f"精准性：外盘指数 {i.get('name')} 缺涨跌幅数值")
    for b in gm.get("blocks") or []:
        for l in b.get("lines") or []:
            if not isinstance(l.get("pct"), (int, float)):
                issues.append(f"精准性：外盘 {b.get('tag')}/{l.get('name')} 缺涨跌幅数值")

    # 逻辑严密性①：高星主线（≥4星）必须能在 催化/外盘/切换日 证据链中找到关键词
    # （防"无证据却给高星"——主线结论必须有事件/资金/切换支撑）
    lines = data.get("main_lines") or []
    if lines:
        evidence_parts: list[str] = []
        for c in data.get("catalysts") or []:
            evidence_parts.append(str(c.get("title") or "") + str(c.get("map") or ""))
        for b in gm.get("blocks") or []:
            evidence_parts.append(str(b.get("tag") or "") + str(b.get("map") or ""))
            for l in b.get("lines") or []:
                evidence_parts.append(str(l.get("name") or "") + str(l.get("map") or ""))
        sd2 = data.get("switch_day") or {}
        evidence_parts.append(str(sd2.get("old_dragon") or "") + str(sd2.get("new_dir") or ""))
        evidence_blob = " ".join(evidence_parts)
        for ml in lines:
            if int(ml.get("stars", 3)) < 4:
                continue
            name = str(ml.get("name") or "")
            kws = [k.strip() for k in _re.split(r"[/·、\s]+", name) if len(k.strip()) >= 2]
            if not any(k in evidence_blob for k in kws):
                issues.append(f"逻辑：主线「{name}」{ml.get('stars')}星但催化/外盘/切换日无对应证据（高星须有支撑）")

    # 逻辑严密性②：外盘看空(tone=bear)板块 不得同时给高星主线（防模块②与模块⑥自相矛盾，
    # 曾踩坑：模块②写"CPO承压"、模块⑥却给光模块⭐⭐⭐⭐）
    for b in gm.get("blocks") or []:
        if b.get("tone") != "bear":
            continue
        btxt = str(b.get("tag") or "") + str(b.get("map") or "")
        for ml in lines:
            if int(ml.get("stars", 3)) < 4:
                continue
            name = str(ml.get("name") or "")
            kws = [k.strip() for k in _re.split(r"[/·、\s]+", name) if len(k.strip()) >= 2]
            if any(k in btxt for k in kws):
                issues.append(f"逻辑矛盾：外盘看空「{b.get('tag','')}」但主线「{name}」仍给{ml.get('stars')}星")

    # 隐私：持仓应对不得含 6 位个股代码
    holdings = str(data.get("holdings_note") or "")
    if _STOCK_CODE_RE.search(holdings):
        issues.append("隐私红线：持仓应对含 6 位个股代码（群推卡禁止点名持仓）")

    # 配色：禁 <font>（实测飞书不渲染）
    if "font color=" in txt:
        issues.append("配色：卡片含 <font> 文字着色（飞书不渲染，须改 <text_tag>）")

    return (len(issues) == 0, issues)


# ── 尾盘潜伏卡（财富密码第4张·14:50 定时）──────────────────────
# 设计（2026-08-27 用户定）：以实时数据为准、不硬编码任何板块规律。
# 数据源：cache/theme_rotation.json（回流引擎·真实涨停/资金流机械识别 boom→cooling→re-entry）
#        cache/pulse_snapshots/latest.json（今日实时板块资金流/热点·确认休整/破位）
# 价值：与盘前回流模块(06:06 预测今日)错开——潜伏卡在看过今日真实走势后预判明日回流。

def _load_ambush_data() -> tuple[dict | None, dict | None]:
    tr_p = ROOT.parent / "cache" / "theme_rotation.json"
    lv_p = ROOT.parent / "cache" / "pulse_snapshots" / "latest.json"
    tr = json.loads(tr_p.read_text(encoding="utf-8")) if tr_p.exists() else None
    try:
        lv = json.loads(lv_p.read_text(encoding="utf-8")) if lv_p.exists() else None
    except Exception:
        lv = None
    return tr, lv


def _live_confirm(theme: str, live: dict | None) -> tuple[str, str]:
    """按资金、持续活跃、热点三类独立事实确认候选题材。

    热点只能说明市场关注，不能冒充资金净流入；净流出优先于其他正面信号。
    """
    names_in: set[str] = set()
    names_out: set[str] = set()
    names_ongoing: set[str] = set()
    names_hot: set[str] = set()
    from common.em_flow import sector_risk_for_decision
    sr = sector_risk_for_decision((live or {}).get("sector_risk"))
    for s in (sr.get("inflow_top") or []):
        names_in.add(str(s.get("name") or ""))
    for s in (sr.get("outflow_top") or []):
        names_out.add(str(s.get("name") or ""))
    sd = (live or {}).get("sector_delta") or {}
    for x in (sd.get("ongoing") or []):
        names_ongoing.add(str(x).split("(")[0].strip())
    for hs in ((live or {}).get("hot_spots") or []):
        names_hot.add(str(hs.get("sector") or ""))
    t = theme or ""

    def hit(names: set[str]) -> bool:
        return any(n and t and (t in n or n in t) for n in names)

    if hit(names_out):
        return ("⚠️ 今日列入净流出/走弱榜·回流降档待确认", "green")
    if hit(names_in):
        return ("✅ 今日资金净流入榜有确认·仍有回流观察价值", "red")
    if hit(names_ongoing):
        return ("⚪ 今日仍在活跃方向中·资金流向未单独确认", "neutral")
    if hit(names_hot):
        return ("⚪ 今日热点榜命中·仅代表热度，资金流向未确认", "neutral")
    return ("— 今日实时榜未直接命中（以系统资金趋势为准）", "neutral")


def _pct_tag(pct: Any, digits: int = 2, *, lead_space: bool = True) -> str:
    """涨跌幅标签（A股口径：涨红跌绿）。非数值一律返回空串——不补零、不编造。

    2026-10-09 用户「所有个股带上涨跌幅」：卡面出现的每一只个股都必须带当日涨跌幅，
    统一走本函数着色，避免各处各写一套（口径/精度/颜色不一致）。
    """
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        return ""
    tone = "red" if pct >= 0 else "green"
    body = f"<text_tag color='{tone}'>{pct:+.{digits}f}%</text_tag>"
    return (" " + body) if lead_space else body


def _pool_line(s: dict, live_pct: float | None = None) -> str:
    """题材回流·相关个股行。2026-10-09 用户「所有个股带上涨跌幅」→ 必带今日实时涨跌。

    优先用调用方传入的今日实时涨幅（`_pool_today_status` 实测命中），其次取池内
    `live_pct`（盘中刷新写入）；两者都缺→不显示，不拿涨停日的历史 pct 冒充今日。
    """
    nm = _stock_hl(s)
    pct = live_pct if isinstance(live_pct, (int, float)) else s.get("live_pct")
    pct_txt = _pct_tag(pct, 2)
    cap = s.get("cap_yi")
    cap_txt = f" ·市值{cap:.0f}亿" if isinstance(cap, (int, float)) and cap > 0 else ""
    risk = s.get("risk") or ""
    risk_txt = f" ·{risk}" if risk else ""
    return f"{nm}{pct_txt}{cap_txt}{risk_txt}"


def _pool_today_status(pool: list[dict], live: dict | None) -> dict[str, float]:
    """查跟踪池个股今日实时涨幅（latest.json leaders/early/rising/hot_spots/top5 code 匹配）。

    2026-08-28 P0 修复：潜伏卡此前只按板块名匹配实时榜（_live_confirm），不校验跟踪池
    个股今日实时状态——实证：和远气体今日涨停（国产芯片题材）仍被列为「六氟化钨·次日潜伏」
    龙头推送，语义反向。本函数返回 {code: 今日最高涨幅}，供「今日已涨停/已启动」硬门槛剔除。
    """
    if not live or not pool:
        return {}
    want = {str(s.get("code") or "").split(".")[0] for s in pool if s.get("code")}
    if not want:
        return {}
    hits: dict[str, float] = {}
    lv = live

    def _upd(code, pct):
        c = str(code or "").split(".")[0]
        if c in want:
            p = float(pct or 0)
            hits[c] = max(hits.get(c, 0), p)

    # `leaders` 是题材摘要，不保证覆盖全量涨停股；潜伏排除必须以
    # `limit_up_stocks` 全量池为准。否则池内股票已经涨停却不在 leaders 时，
    # 题材会被错误保留为潜伏候选（2026-09-30 深物业A）。
    for it in (lv.get("limit_up_stocks") or []):   # 全量今日涨停池（硬排除依据）
        _upd(it.get("code"), it.get("pct"))
    for it in (lv.get("leaders") or []):          # 题材摘要，补充兼容旧快照
        _upd(it.get("code"), it.get("pct"))
    for it in (lv.get("early") or []):            # 盘中强势（buy 主票）
        _upd((it.get("buy") or {}).get("code"), (it.get("buy") or {}).get("pct"))
    for it in (lv.get("rising") or []):           # 上涨榜
        _upd(it.get("code"), it.get("pct"))
    for hs in (lv.get("hot_spots") or []):        # 热点板块成分
        for st in (hs.get("stocks") or []):
            _upd(st.get("code"), st.get("pct"))
    for tp in (lv.get("top5") or []):             # Top5 板块龙头/跟风
        _upd((tp.get("leader") or {}).get("code"), (tp.get("leader") or {}).get("pct"))
        for fw in (tp.get("followers") or []):
            _upd(fw.get("code"), fw.get("pct"))
    return hits


# ===== 方案A（2026-09-07 用户「怎么还是这些个股？没有新的方向吗」拍板）：潜伏卡新主线通道 =====
AMBUSH_MAINLINE_MIN_ZT = 10   # 今日涨停数 ≥10 判定为爆发主线；置 None 即整体禁用回滚
AMBUSH_MAINLINE_MAX = 3       # 新主线块最多列几条
# 东财板块口径与 top5 板块名的同义桥（early/hot_spots 挂靠主线用·防「光电共封装CPO」匹配不上「光通信」）
_AMBUSH_MAIN_SYN = {
    "光通信": ("CPO", "光电共封装", "光模块", "通信设备", "5G", "通信"),
    "机器人": ("人形机器人", "具身智能", "减速器", "伺服"),
    "PCB板": ("覆铜板", "印制电路", "PCB"),
    "液冷服务器": ("液冷", "数据中心", "算力"),
    "国产芯片": ("半导体", "算力芯片"),
    "大农业": ("粮食", "种业", "养殖", "猪肉"),
    "短剧/互动影游": ("短剧", "AI漫剧", "影视"),
}


def _ambush_syn_match(sec: str, main: str) -> bool:
    if not sec or not main:
        return False
    if main in sec or sec in main:
        return True
    for kw in _AMBUSH_MAIN_SYN.get(main, ()):
        if kw and kw in sec:
            return True
    return False


def _ambush_mainline_block(live: dict | None) -> list[dict]:
    """方案A 爆发日主通道：存在涨停≥MIN 的主线时，机械层插入「🔥 今日新主线·次日接力锚」块。
    只列实时涨停池可确认事实（top5 涨停数/龙头/催化 + hot_spots 盘中未封板 buyable +
    early 爆发前夜·同义桥挂靠），不预测不评级；非爆发日返回 [] 行为不变。"""
    if not live or AMBUSH_MAINLINE_MIN_ZT is None:
        return []
    strong = [b for b in (live.get("top5") or [])
              if (b.get("zt_count") or 0) >= AMBUSH_MAINLINE_MIN_ZT][:AMBUSH_MAINLINE_MAX]
    if not strong:
        return []
    mains = [str(b.get("name") or "") for b in strong]
    buy_by_sector: dict[str, list[str]] = {}
    for h in (live.get("hot_spots") or []):
        sec = str(h.get("sector") or "")
        if not sec:
            continue
        for st in (h.get("stocks") or []):
            if st.get("buyable") and st.get("name") and st.get("code"):
                buy_by_sector.setdefault(sec, []).append(
                    f"{st.get('name')}({str(st.get('code')).split('.')[0]})"
                    f"{_pct_tag(st.get('pct'), 0, lead_space=False)}")
    early_by_main: dict[str, list[dict]] = {}
    for e in (live.get("early") or []):
        sec = str(e.get("sector") or "")
        for m in mains:
            if _ambush_syn_match(sec, m):
                early_by_main.setdefault(m, []).append(e)
                break

    out: list[dict] = []
    out.append(_div("<text_tag color='purple'>🔥 今日新主线·次日接力锚（爆发日自动识别）</text_tag>"))
    for b in strong:
        nm = str(b.get("name") or "")
        zt = b.get("zt_count") or 0
        L = b.get("leader") or {}
        lcode = str(L.get("code") or "").split(".")[0]
        lboards = str(L.get("boards") or "")
        desc = str(b.get("desc") or "")
        if len(desc) > 48:
            desc = desc[:48] + "…"
        line = (f"▶ <text_tag color='red'>{nm}</text_tag> 今日 {zt} 家涨停｜龙头 {L.get('name')}({lcode})"
                f"{(' · ' + lboards) if lboards else ''}{_pct_tag(L.get('pct'), 2)}\n　催化：{desc}")
        adds: list[str] = []
        for sec, buys in buy_by_sector.items():
            if buys and _ambush_syn_match(sec, nm):
                adds.extend(buys)
        if adds:
            line += "\n　盘中未封板（半路·非低位）：" + "、".join(dict.fromkeys(adds))[:150]
        eb = early_by_main.get(nm)
        if eb:
            bits = []
            for e in eb[:2]:
                bb = e.get("buy") or {}
                bits.append(f"{e.get('sector')}·{bb.get('name')}({str(bb.get('code') or '').split('.')[0]})"
                            f"{_pct_tag(bb.get('pct'), 0, lead_space=False)}")
            line += "\n　🌱 爆发前夜（可跟踪·竞价确认）：" + "、".join(bits)
        out.append(_div(line))
    out.append(_div("⛔ 以上都是今天盘中真实走强的（涨停潮/还没封板/爆发前夜），不是低位埋伏；明天能不能接力、会不会扩散，要看竞价买盘，别闭着眼追"))
    out.append(_hr())
    return out


# ===== 方案1（2026-09-17 用户「中京电子涨停之后的曲线…你就没有命中呀」拍板）=====
# 个股「断板回流」机械通道：补上「涨停后休整→资金回流再走强」的**个股维度**盲区。
# 与上面的题材相位回流（主题材通道）并列不互斥；口径/开关全部托管 core/reflow_scan.py。
_REFLOW_POOL_FRESH_SEC = 600   # 落盘池复用窗口（同一轮 automation 的 dry-run 与推送不重复扫描）

# ===== 阴线回踩十日线（2026-10-08 用户拍板；2026-10-09 升为本卡唯一选材）=====
# 独立研究策略：候选/台账由 core/ambush_pullback.py 在 14:30 冻结，卡片只读快照渲染。
# ⛔ 本卡「今日尾盘买入标的」只能出自该池（PICKS_POOL_ENFORCE）——2026-10-09 用户
#    「我们尾盘潜伏卡现在都改为那套逻辑」。
_PULLBACK_SNAPSHOT_PREFIX = "ambush_pullback_"
_PULLBACK_MAX = 8            # 卡面展示上限（全量候选通常 ≤3，不硬凑）
PICKS_POOL_ENFORCE = True    # False 即回退为「池外也可推票」的旧行为（可逆开关）
# 机械层「💰 今日板块资金流（实时·左红右绿）」两列块——2026-10-09 用户「不显示今日板块资金流」
# 置 False；改回 True 即恢复该块（可逆开关，勿删代码）。
AMBUSH_SHOW_SECTOR_FLOW = False
# 🎯「今日尾盘买入标的」块位置——2026-10-14 用户「我说的这一项直接放到整个卡片的头部！」
# 置 True ⇒ 渲染在整个卡片最前面（定调之前）；置 False ⇒ 回落到原「AI 研判层·置底」位置。
# （可逆开关，勿删代码。开启后 `validate_ambush_card` 不再要求推票位于资金流之后。）
AMBUSH_PICKS_AT_TOP = True
# 🎯 推票块收尾「⚠️ 风险」一句——2026-10-14 用户「加上一句风险跟在第二部分」。
# 内容全部取自当日 14:30 冻结快照的 `env`（真实盘面家数/涨停/炸板率）与 `advice`（系统给的
# 仓位建议），⛔ 绝不编数字：快照缺失/字段不全时整句不渲染（缺数据 ≠ 风险为零）。
# 置 False 即整句不渲染（可逆开关，勿删代码）。
AMBUSH_PICKS_RISK = True
# 市场强弱 → 白话措辞（快照 env.level 可能值：weak / neutral / strong；未知值不套词）
_MARKET_TONE_WORD = {"weak": "偏弱", "neutral": "一般", "strong": "偏强"}
# ── 精简布局 + 推票理由分段（2026-10-15 用户「杂七杂八的话说得太多了，删除一些你觉得没有
#    意义的话和模块」「推票的理由也描述得不清不楚，好好重新梳理一下」）──
# 开关独立可逆（2026-10-15 第二轮收敛「还是觉得有点多余」）：
#  ① AMBUSH_LEAN_LAYOUT=False → 逐字回退旧布局（背景参考恢复「相关个股」名单 + 每题材
#    一行通用模板「再次启动信号」；断板回流恢复全量；回流方向恢复逐题材一行）。
#  ② AMBUSH_BG_MEMBER_ROWS：背景参考每个题材最多渲染几只相关个股（None=沿用旧上限 5 只；
#     0=不渲染该名单）。
#  ③ AMBUSH_REFLOW_MAX：断板回流块最多渲染几只（None=旧行为，全量）。
#  ④ AMBUSH_SCAN_DEDUP=False → 参照个股块不再与「当日候选池 / 推票」去重（回退旧行为）。
#  ⑤ AMBUSH_SHOW_SCAN=False（2026-10-15 用户「参照个股·资金/地位/人气 不需要这个模块」）
#     → 「🔥 参照个股」整块不渲染（默认）；置 True 回落旧行为（渲染，并按 ④ 去重）。
#  ⑥ AMBUSH_SHOW_CALIBER_AS_BLOCK=False → 定调并入「今日候选」块（规则与结果同处，
#     不再独立成块）；置 True 恢复独立的 📌 定调块（旧布局）。
AMBUSH_LEAN_LAYOUT = True
AMBUSH_BG_MEMBER_ROWS = None
AMBUSH_REFLOW_MAX = 3
AMBUSH_SCAN_DEDUP = True
AMBUSH_SHOW_SCAN = False
AMBUSH_SHOW_CALIBER_AS_BLOCK = False
# 买入口径（⛔ 唯一出口·挂在「✅ 今日买入（AI 精选）」块末尾；测试与调用方引用本常量）。
AMBUSH_BUY_NOTE = ("买入：今日推送后即按实时价买入，计入战绩；"
                   "卖出：下一交易日10:00准时卖出，计入收益。")
# 推票「理由」段名（AI data 可用 points 或带换行/段名的 reason；两者都按段渲染）
_PICK_POINT_KEYS = ("方向", "资金", "形态", "买点", "风险")


def _ambush_picks_risk_text(cache_dir: Path, day: str) -> str:
    """推票收尾的风险句（数据驱动；无数据 → 空串，调用方整句省略）。"""
    snap = _ambush_pullback_snapshot(cache_dir, day)
    if not snap:
        return ""
    env = snap.get("env") or {}
    advice = snap.get("advice") or {}
    facts = env.get("facts") or {}
    bits: list[str] = []
    up, down = facts.get("up"), facts.get("down")
    if isinstance(up, (int, float)) and isinstance(down, (int, float)):
        bits.append(f"{int(down)}家跌对{int(up)}家涨")
    zt = facts.get("total_zt")
    if isinstance(zt, (int, float)):
        bits.append(f"涨停{int(zt)}家")
    br = facts.get("broken_rate")
    if isinstance(br, (int, float)):
        bits.append(f"炸板率{br:.0f}%")
    tone = _MARKET_TONE_WORD.get(str(env.get("level") or ""), "")
    head = f"今天市场{tone}" if tone else "今天盘面"
    if not bits and not tone:
        return ""
    part1 = f"{head}（{' · '.join(bits)}）" if bits else head
    n, per = advice.get("max_picks"), advice.get("position_pct_each")
    part2 = ""
    if isinstance(n, (int, float)) and n > 0 and isinstance(per, (int, float)) and per > 0:
        part2 = f"——买入票最多{int(n)}只、每只仓位不超过{per:.0f}%"
    return f"⚠️ 风险：{part1}{part2}。"


def _ambush_reflow_pool(cache_dir: Path) -> dict | None:
    """取断板回流候选池：10 分钟内已落盘则复用，否则实时扫描并写 cache/reflow_pool.json。

    失败（网络/数据缺失）返回 None，调用方静默跳过该块——绝不让卡片因此报错。
    """
    try:
        from core import reflow_scan as _rs
    except Exception:
        return None
    pool_p = cache_dir / "reflow_pool.json"
    if pool_p.exists():
        try:
            d = json.loads(pool_p.read_text(encoding="utf-8"))
            if time.time() - float(d.get("pool_ts_epoch") or 0) < _REFLOW_POOL_FRESH_SEC:
                return d
        except Exception:
            pass
    try:
        res = _rs.scan_reflow(cache_dir)
    except Exception:
        return None
    if not res:
        return None
    res["pool_ts_epoch"] = time.time()
    try:
        _rs.write_pool(cache_dir, res)
    except Exception:
        pass
    return res


def _ambush_reflow_block(cache_dir: Path) -> list[dict]:
    """🔁 个股断板回流块（机械层·只列客观事实，不预测）。无候选返回 [] 行为不变。"""
    res = _ambush_reflow_pool(cache_dir)
    cands = (res or {}).get("candidates") or []
    if not cands:
        return []
    # ⚠️ 性质声明并进标题（2026-10-15 第二轮收敛）：原来标题讲一遍「涨停后休整·今日资金
    #    回流」，末尾再单起一行讲一遍「不是新低位埋伏…别闭着眼追」——同一件事说两遍。
    #    精简态合成一行；AMBUSH_LEAN_LAYOUT=False 时回退旧的两段式。
    out: list[dict] = [_div(
        "<text_tag color='purple'>🔁 个股断板回流（涨停后休整·今天钱又回来｜"
        "性质不是新低位埋伏，别闭眼追）</text_tag>"
        if AMBUSH_LEAN_LAYOUT else
        "<text_tag color='purple'>🔁 个股断板回流（涨停后休整·今日资金回流）</text_tag>"
    )]
    # 行数上限（2026-10-15 用户「杂七杂八的话说得太多了」）：旧行为无上限，实测 11 行成墙；
    # 置 AMBUSH_REFLOW_MAX=None 即回退全量（可逆）。
    rows = cands if AMBUSH_REFLOW_MAX is None else cands[:AMBUSH_REFLOW_MAX]
    for r in rows:
        nm = str(r.get("name") or "")
        code = str(r.get("code") or "")
        sec = str(r.get("sector") or "")
        tag = str(r.get("tag") or "")
        pct = float(r.get("today_pct") or 0)
        boards = int(r.get("max_boards") or 0)
        line = f"{_stock_hl({'name': nm, 'code': code})} <text_tag color='red'>{pct:+.1f}%</text_tag>"
        bits = [b for b in (sec, tag, (f"近期曾{boards}板" if boards >= 2 else "")) if b]
        if bits:
            line += " · " + " · ".join(bits)
        out.append(_div(line))
    if not AMBUSH_LEAN_LAYOUT:
        out.append(_div(
            "⛔ 上面是「涨停后休整过、今天钱又回来」的票，性质不是新低位埋伏；"
            "明天能不能接上要看竞价买盘，别闭着眼追"
        ))
    out.append(_hr())
    return out


def _ambush_pullback_snapshot(cache_dir: Path, day: str) -> dict | None:
    """只读 14:30 冻结快照；卡片构建不得扫描、记账、结算或触发交易。"""
    path = cache_dir / f"{_PULLBACK_SNAPSHOT_PREFIX}{day}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("trade_date") != day:
        return None
    if not isinstance(data.get("candidates"), list):
        return None
    return data


def _ambush_strategy_mode(cache_dir: Path, day: str) -> str:
    """当日冻结快照的选材口径：strong_trend / ma10_pullback（缺快照按旧口径渲染）。

    2026-10-15 用户「删除旧逻辑」后，现行口径恒为 strong_trend；本函数保留旧值分支只为
    渲染历史卡片（旧快照无 `params.strategy_mode` 字段）。
    """
    snap = _ambush_pullback_snapshot(cache_dir, day) or {}
    return str((snap.get("params") or {}).get("strategy_mode") or "ma10_pullback")


def _ambush_caliber(cache_dir: Path, day: str) -> str:
    """当日形态口径的一句话（「只挑这一种形态」）。

    ⛔ 单一出口：卡名、候选块标题、定调行**共用本函数**，禁止各自硬编码——
    2026-10-15 之前同一句话在卡名 / 块标题 / 定调里各写一遍，改口径必漏一处。

    ⛔ 时点点破（2026-10-15 用户「尾盘买入标的和定调好像冲突了」）：strong_trend 的
    「分歧洗盘 → 反包」是**前一两日已发生**的准入事实，而买入发生在今天（反包次日的回踩）。
    旧文案只写「分歧洗盘后放量反包」，读者按「今天」去读，就会觉得「怎么推了只今天收阴的」，
    故补一句「（洗盘与反包是前一两日的事，今天只等它回踩）」消歧义。
    """
    if _ambush_strategy_mode(cache_dir, day) == "strong_trend":
        return ("前段爆发过 · 均线多头 · 站在十日线上方 · 分歧洗盘后放量反包"
                "（洗盘与反包是前一两日的事，今天只等它回踩）")
    return "前段爆发过 · 今日收阴回踩十日线 · 十日线连续上行且缩量"


def _ambush_pullback_codes(cache_dir: Path, day: str) -> set[str]:
    """当日冻结池内的代码集合（供「推票只能出自本池」校验）。"""
    snap = _ambush_pullback_snapshot(cache_dir, day)
    return {str(r.get("code") or "") for r in (snap or {}).get("candidates") or []
            if str(r.get("code") or "")}


def _ambush_pullback_block(cache_dir: Path, day: str) -> list[dict]:
    """🎯 主选材块：尾盘潜伏买入候选（2026-10-09 升为本卡唯一选材）。

    渲染 14:30 冻结快照；现行策略为「强势趋势核心·分歧转一致」，历史快照可能仍是
    旧的「阴线回踩十日线」形态——按 `params.strategy_mode` 区分标题与话术，保证历史卡片兼容。
    主模块**不静默**：快照缺失＝链路未产出（区别于「今日无候选」），必须显式告知。
    """
    if _ambush_pullback_snapshot(cache_dir, day) is None:
        return [
            _div("<text_tag color='green'>🎯 今日尾盘买入候选：今日扫描没有产出候选（链路异常）</text_tag>"),
            _hr(),
        ]
    snap = _ambush_pullback_snapshot(cache_dir, day)
    cands = snap.get("candidates") or []
    # strategy_mode：strong_trend=2026-10-15 启动的「强势趋势核心·分歧转一致」；
    # 缺省/旧快照按 ma10_pullback（阴线回踩十日线）渲染，保证历史卡片兼容。
    is_strong = _ambush_strategy_mode(cache_dir, day) == "strong_trend"
    # 冻结时刻：优先用扫描真实生成时间（补跑时不能冒充 14:30），缺省回落到计划时点。
    _frozen = str(snap.get("generated_at") or "")
    _hhmm = ""
    if len(_frozen) >= 16 and _frozen[10] in ("T", " "):
        _hhmm = _frozen[11:16]
    # 标题去「买入」（2026-10-15 用户「尾盘买入标的和下面这个定调好像冲突了，是不是只留一个」）：
    # 本块＝**机械选材证据池**，不是买入指令——「买入」字样与买入口径行只挂在置顶的
    # 「✅ 今日买入（AI 精选）」块。旧标题「🎯 今日尾盘买入候选」与置顶块同带 🎯 且都含
    # 「买入」，一个 4 只一个 2 只，读者根本判断不出该买几只。
    out = [_div("<text_tag color='purple'>📋 今日候选</text_tag>"
                f"（{len(cands)} 只 · 机械扫描 · "
                f"{'今日' + _hhmm + ' 冻结' if _hhmm else '今日14:30 冻结'}）")]
    # 定调并入本块（规则与结果同处＝同一个方向只讲一次）：口径与卡名共用 `_ambush_caliber()`，
    # ⛔ 不再独立成块、也不再逐票复读。`AMBUSH_SHOW_CALIBER_AS_BLOCK=True` 回退独立块布局。
    if not AMBUSH_SHOW_CALIBER_AS_BLOCK:
        out.append(_div(f"<text_tag color='purple'>📌 定调</text_tag> 尾盘只挑一种形态："
                        f"{_ambush_caliber(cache_dir, day)}"))
    # ⛔ 数据源异常必须显式声明：抓不到日K时「候选 0 只」是假阴性，不是「今日无符合条件」
    if snap.get("data_ok") is False:
        out.append(_div(f"<text_tag color='green'>⚠️ 日K数据源异常（{snap.get('fetch_failed')}"
                        f"/{snap.get('attempted')} 只取不到历史K线），今日候选不可信</text_tag>"))
    if not cands:
        if is_strong:
            # ⛔ 文案铁律：此处曾用「均线多头(MA5≥MA10≥MA20) / 价在MA10上方」——
            #    `MA5` 是白话门禁黑话词表成员，零候选日必被 lint 拦死（2026-10-27 实拦）。
            #    入选条件对读者只讲人话：5日线/10日线/20日线怎么排。
            out.append(_div("今日无符合条件的候选（须同时满足：前段爆发过的方向 + 均线多头排列"
                            "（5日线在10日线上、10日线在20日线上）+ 股价站在十日线上方"
                            " + 近5日与20日量比都≥1.2 + 近15日内有过放量启动 + 分歧洗盘后放量反包）"))
        else:
            out.append(_div("今日无符合条件的候选（须同时满足：前段爆发板块 + 今日收阴 + 十日线连续上行"
                            " + 收盘贴着十日线 + 缩量）"))
        out.append(_hr())
        return out
    for row in cands[:_PULLBACK_MAX]:
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        sector = str(row.get("sector") or "")
        themes = row.get("themes") or []
        close = row.get("close")
        ma10 = row.get("ma10")
        gap = row.get("ma10_gap_pct")
        volume_ratio = row.get("volume_ratio")
        score = row.get("score")
        hot_date = str(row.get("hot_date") or "")
        sector_zt = row.get("sector_zt_count")
        max_boards = row.get("max_boards")
        line = f"{_stock_hl({'name': name, 'code': code}, 'purple')}"
        if sector:
            line += f" ·{sector}"
        if themes:
            line += f" ·题材{'/'.join(str(t) for t in themes[:3])}"
        pct = row.get("pct")
        if isinstance(pct, (int, float)):
            tone = "red" if pct >= 0 else "green"
            line += f" <text_tag color='{tone}'>{pct:+.2f}%</text_tag>"
        hot_bits = []
        if hot_date:
            hot_bits.append(f"曾在{hot_date}活跃")
        if isinstance(sector_zt, (int, float)) and sector_zt >= 3:
            # ⚠️ 口径纠正（2026-10-15）：该数字来自快照 `sector_zt_count` = **近5个交易日累计**
            # 同行业涨停家数（core/ambush_pullback._historical_universe 逐日累加），旧文案写成
            # 「当日同方向…」＝把 5 日累计当单日，与当日全市场涨停数（约 30 家）自相矛盾。
            hot_bits.append(f"近5日同方向{int(sector_zt)}家涨停")
        if isinstance(max_boards, (int, float)) and max_boards >= 2:
            hot_bits.append(f"曾{int(max_boards)}板")
        if hot_bits:
            line += "\n　历史热度：" + " · ".join(hot_bits)
        metrics = []
        if isinstance(close, (int, float)):
            # ⛔ 2026-11-11：这个 `close` 是 **14:30 快照价**，不是收盘价
            #    （选股时还没收盘）。写成「收盘」会让人高估形态强度。
            metrics.append(f"14:30 现价{close:.2f}")
        if isinstance(ma10, (int, float)):
            metrics.append(f"十日线{ma10:.2f}")
        if isinstance(gap, (int, float)):
            metrics.append(f"距十日线{gap:+.1f}%")
        if isinstance(volume_ratio, (int, float)):
            metrics.append(f"量比{volume_ratio:.2f}")
        if isinstance(score, (int, float)):
            # 🧾 2026-11-11：分数必须带分量（旧写法只有「形态分9.8」，像接近满分）
            _sp = row.get("score_parts")
            if isinstance(_sp, dict):
                metrics.append(
                    f"形态分{score:.1f}（热度{_sp.get('hot')}·板数{_sp.get('boards')}"
                    f"·量能{_sp.get('volume')}·位置{_sp.get('position')}）")
            else:
                metrics.append(f"形态分{score:.1f}")
        # 分歧洗盘（2026-10-15 第二轮收敛）：旧版每只票单起一行「分歧洗盘：X 日 -Y% → 次日
        # 反包转一致」——可「次日反包转一致」**本身就是入选条件**（不反包的票根本进不了池），
        # 4 只票复读 4 遍同一句准入规则。只保留可核事实「哪一天洗的、洗多狠」，并进上一行。
        pullback_date = row.get("pullback_date")
        pullback_drop = row.get("pullback_drop_pct")
        if pullback_date and isinstance(pullback_drop, (int, float)):
            _pd = str(pullback_date)
            metrics.append(f"洗盘{_pd[5:] if len(_pd) >= 10 else _pd} {pullback_drop:+.1f}%")
        if metrics:
            line += "\n　" + " · ".join(metrics)
        out.append(_div(line))
    # ⛔ 买入口径行⛔ 不在此处（2026-10-15 用户「尾盘买入标的和定调好像冲突了，只留一个」）：
    #    原先挂在本块末行、紧跟在 4 只候选后面，读起来像「这 4 只都要买」——已移到置顶的
    #    「✅ 今日买入（AI 精选）」块末尾，全卡只在那一处下买入指令。
    out.append(_hr())
    return out


def _pick_seg_line(text: str) -> str:
    """推票理由的**一段** → 渲染行：段名加粗；「风险」段整行转 green（A股语义·恶化=绿）。

    ⛔ 段名加粗走 markdown `**`（在标签之外），⛔ 不得把 `**` 塞进 text_tag 内
    （宪法【视觉样式管控⓪】：text_tag 内禁 `**`）。
    """
    ln = str(text or "").strip()
    if not ln:
        return ""
    head, sep, body = ln.partition("：")
    if sep and head and len(head) <= 5:
        if head == "风险":
            return f"\n　<text_tag color='green'>风险：{body.strip()}</text_tag>"
        return f"\n　**{head}**：{body.strip()}"
    return f"\n　{ln}"


def _picks_reason_lines(p: dict) -> list[str]:
    """推票理由 → 分段行（2026-10-15 用户「推票的理由也描述得不清不楚，好好重新梳理」）。

    铁律（宪法【✍️ 长文语义分段规范】）：理由必须分段，段名在段首、每段 1~2 句，
    ⛔ 禁把方向/资金/形态/风险糊成一行 200+ 字。两种入参都吃：
      ① 结构化 `points: [{"k": "方向", "v": "…"}, …]`（推荐：段名由 writing 端显式给出）；
      ② 字符串 `reason`——含换行则按行拆；行首带「XX：」即视作段名。
    """
    parts: list[str] = []
    pts = p.get("points")
    if isinstance(pts, list):
        for it in pts:
            if not isinstance(it, dict):
                continue
            k = str(it.get("k") or it.get("label") or "").strip()
            v = str(it.get("v") or it.get("text") or "").strip()
            if v:
                parts.append(f"{k}：{v}" if k else v)
    if not parts:
        for ln in str(p.get("reason") or "").split("\n"):
            if ln.strip():
                parts.append(ln.strip())
    return [x for x in (_pick_seg_line(t) for t in parts) if x]


def _picks_stocks_line(stocks: Any) -> str:
    """今日尾盘买入标的的个股行（2026-10-09 用户「标的带上当时的涨幅，不然我不知道能不能买入」）。

    支持两种写法：
    ① 结构化 [{"name","code","pct"}] → 「名(代码) +X.XX%」，涨幅按 A 股口径着色（涨红跌绿）；
    ② 纯字符串（旧数据/机械层兼容）→ 原样输出。
    涨幅必须来自当日实时快照，缺失就不显示，不补零、不编造。
    """
    if isinstance(stocks, str):
        return stocks.strip()
    if not isinstance(stocks, list):
        return ""
    parts: list[str] = []
    for it in stocks:
        if isinstance(it, dict):
            seg = _stock_hl({"name": it.get("name") or "", "code": it.get("code") or ""}, "purple")
            pct = it.get("pct")
            if isinstance(pct, (int, float)):
                c = "red" if pct >= 0 else "green"
                seg += f" <text_tag color='{c}'>{pct:+.2f}%</text_tag>"
            parts.append(seg)
        elif str(it).strip():
            parts.append(str(it).strip())
    return "｜".join(parts)


def build_ambush_card(data: dict | None = None) -> dict:
    """🎯 尾盘潜伏·次日回流预期（14:30 定时）。详见 shared_rules【潜伏卡骨架】。

    双层结构（2026-08-28 用户「除了数据，需要 AI 介入和思考」升级）：
    - 机械数据层：theme_rotation 回流候选 + 今日实时板块资金流（不硬编码规律·完全以实时数据为准）
      ＋🔥 今日新主线·次日接力锚（方案A）+ 🔁 个股断板回流（方案1·core/reflow_scan.py）
    - AI 研判层（data 注入）：🧠 板块资金流向分析 / 🔥 个股资金·地位·人气 / 🎯 今日尾盘买入标的推票
      data 契约：{ai_flow: 多行文本, stock_scan: [{name,code,role,pct,fund,hot,note}],
                 picks: [{theme,stocks,reason,risk}], ai_verdict: 置底综合研判}
    """
    hhmm = _now_bj().strftime("%H:%M")
    now = _now_bj()
    mmdd = f"{now.month:02d}-{now.day:02d}"
    tr, live = _load_ambush_data()

    cands = (tr or {}).get("candidates") or []
    # ⛔ 2026-09-07 用户「题材回流预测·板块数量放大至少10个」：修复 phase 口径错配——
    #    引擎相位是 re-entry-candidate/boom/watch（不产"分歧/cooling"字面），旧代码找
    #    phase in (分歧,cooling) → 恒空 → core=reentry+[] → 卡上只剩 2 个，watch 观察档 6 个被藏。
    #    改为：candidates 全量按 相位优先级(re-entry>watch>boom) + 置信度排序，取前 12
    #    （保底 ≥10 需求；候选不足 12 时全量如实展示，不硬凑无信号板块）。
    def _reflow_rank(c):
        p = str(c.get("phase") or "")
        return 0 if p == "re-entry-candidate" else (1 if p == "watch" else 2)
    core = sorted(cands, key=lambda c: (_reflow_rank(c), -(c.get("confidence") or 0)))
    core = core[:12]

    # 卡名随当日选材口径走（2026-10-15 用户「删除旧逻辑」后旧口径已废止，⛔ 卡头不得再写
    # 已停用的形态名）；旧快照/无快照仍回落「阴线回踩十日线」，历史卡片渲染不变。
    _title_caliber = ("强势趋势核心"
                      if _ambush_strategy_mode(ROOT.parent / "cache", now.strftime("%Y-%m-%d"))
                      == "strong_trend" else "阴线回踩十日线")
    title = f"🎯 尾盘潜伏·{_title_caliber} {mmdd} | 实时数据驱动"
    blocks: list[dict] = []

    # ── AI 层入参（提前解析：推票块可能被置顶，必须先于 blocks 组装就绪）──
    ai = data or {}
    ai_flow = (ai.get("ai_flow") or "").strip()
    stock_scan = ai.get("stock_scan") or []
    picks = ai.get("picks") or []
    ai_verdict = (ai.get("ai_verdict") or "").strip()

    def _picks_block() -> list[dict]:
        """🎯 今日尾盘买入标的（AI 推票）——2026-10-14 用户「直接放到整个卡片的头部」。

        单一出口：置顶（AMBUSH_PICKS_AT_TOP=True）与回落置底（False）共用本函数，
        ⛔ 不得复制第二份渲染逻辑（两份必然漂移）。
        """
        if not picks:
            return []
        # 全卡唯一买入出口（2026-10-15 用户「尾盘买入标的和下面这个定调好像冲突了，只留一个」）：
        # 标题明确「今日买入」并与「📋 今日候选（机械扫描）」区分；买入口径行也挂在本块末尾，
        # ⛔ 不再挂候选块（否则 4 只候选后面跟一句「买入…」＝像要买 4 只）。
        _n = sum(len(p.get("stocks") or []) for p in picks)
        out: list[dict] = [_div(
            "<text_tag color='purple'>✅ 今日买入</text_tag>"
            # ⛔ 2026-11-11：这 1–4 只是 `ambush_pullback` **机械规则**筛的，
            #    AI 只写点评 —— 标「AI 精选」属于夸大（用户 11-10 审计发现）。
            f"（规则筛选 {_n} 只 · 实时数据核验）"
            # ⛔ 仓位口径单一源：个股点评是 AI 写的，曾出现「不超三成」与系统 50% 打架。
            #    卡面在这里钉死优先级，⛔ 不复述数字（数字只在「风险」段出现一次）。
            # ⛔ 配色铁律：飞书不渲染 <font>（本卡 self-check 直接拒推），灰字用 text_tag/neutral。
            "\n<text_tag color='neutral'>仓位只以本卡「风险」段为准；个股点评里出现的仓位百分比不生效。</text_tag>"
        )]
        for p in picks:
            theme = str(p.get("theme") or "")
            risk = str(p.get("risk") or "")
            line = f"<text_tag color='red'>▶ {theme}</text_tag>"
            stock_line = _picks_stocks_line(p.get("stocks"))
            if stock_line:
                line += f"\n{stock_line}"
            # 理由按段渲染（2026-10-15 用户「理由描述得不清不楚」→ 段名 + 每段一句，
            # 段名加粗、风险段转绿；旧单段 reason 走同一出口，不再糊成一整行）。
            for seg in _picks_reason_lines(p):
                line += seg
            if risk:
                line += f"\n<text_tag color='green'>风险：{risk}</text_tag>"
            out.append(_div(line))
        # 收尾「⚠️ 风险」一句（2026-10-14 用户「加上一句风险跟在第二部分」）：
        # 跟在两条推票的「风险」段之后，内容取自当日冻结快照，无数据则整句省略。
        if AMBUSH_PICKS_RISK:
            risk_line = _ambush_picks_risk_text(ROOT.parent / "cache", now.strftime("%Y-%m-%d"))
            if risk_line:
                out.append(_div(f"<text_tag color='green'>{risk_line}</text_tag>"))
        # 买入口径（全卡唯一出口·2026-10-15 用户「只留一个」）：紧跟在本块（实际要买的票）之后。
        out.append(_div(AMBUSH_BUY_NOTE))
        out.append(_hr())
        return out

    # 模块⓪ 🎯 今日尾盘买入标的（置顶·2026-10-14 用户「直接放到整个卡片的头部」）
    if AMBUSH_PICKS_AT_TOP:
        blocks += _picks_block()

    # 模块① 定调（2026-10-15 用户「尾盘买入标的和下面这个定调好像冲突了，只留一个」）：
    #  **默认并入**「📋 今日候选」块（见 `_ambush_pullback_block`）——形态口径与它筛出的结果
    #  同处一块，读者不用在两个 hr 之间来回对照。`AMBUSH_SHOW_CALIBER_AS_BLOCK=True` 回退
    #  独立块旧布局。口径恒由 `_ambush_caliber()` 单一出口产出。
    if AMBUSH_SHOW_CALIBER_AS_BLOCK:
        blocks.append(_div(
            f"<text_tag color='purple'>📌 定调</text_tag> 尾盘只挑一种形态："
            f"{_ambush_caliber(ROOT.parent / 'cache', now.strftime('%Y-%m-%d'))}"
        ))
        blocks.append(_hr())

    # 模块①.5 主选材：阴线回踩十日线候选（2026-10-09 用户「尾盘潜伏卡都改为那套逻辑」）
    # ⛔ 本块＝卡的唯一选材与次日推票来源；只读 14:30 冻结快照，渲染期不扫描、不记账。
    blocks += _ambush_pullback_block(ROOT.parent / "cache", now.strftime("%Y-%m-%d"))

    # 模块② 背景参考（当日盘面事实·不参与次日推票）
    bg: list[dict] = []
    bg += _ambush_mainline_block(live)

    #  背景·题材相位回流候选
    if core:
        bg.append(_div("<text_tag color='purple'>🔮 次日有望回流的题材（按近期涨停与资金流向自动筛选）</text_tag>"))
        # 2026-08-28 P0：今日已涨停排除——池内个股今日涨停 → 题材已启动·非潜伏（剔除）；
        # 池内个股今日 ≥5% → 标注「今日已启动·不潜伏」。实证：和远气体今日涨停仍被推潜伏（用户质疑）。
        zt_excluded: list[str] = []  # 今日已涨停启动的方向名（只列名，只数交给 🧠 涨停与催化段）
        pending: list[str] = []      # 今日资金无确认的待回流方向（聚合一行）
        for c in core:
            pool = c.get("watch_pool") or []
            hits = _pool_today_status(pool, live)
            zt_codes = {k for k, v in hits.items() if v >= 9.8}
            hot_codes = {k for k, v in hits.items() if 5.0 <= v < 9.8}
            if zt_codes:
                # 2026-10-15 第二轮收敛：旧版给每个题材复读一遍「（板块内 N 只今天已经涨停·
                # 已经启动）」，8 个题材就是 8 遍同样的句子 → 只收方向名，只数交给
                # 🧠 涨停与催化段说一次（两处口径本就不同，并排放着反而像互相打脸）。
                zt_excluded.append(str(c.get("theme") or ""))
                continue
            nm = str(c.get("theme") or "")
            cd = c.get("cooled_days")
            live_tag, live_color = _live_confirm(nm, live)
            if hot_codes:
                # ⛔ 不再拼尾部那句「| — 今日实时榜未直接命中（以系统资金趋势为准）」
                # （2026-10-15 第三轮收敛）：已启动本身就是结论，后面再接一句「没命中」
                # 既零信息又自相矛盾（今日实测渲染成「…·不潜伏 | — 今日实时榜未直接命中…」）。
                live_tag = (f"⚠️ 池内个股今日已启动({', '.join(sorted(hot_codes))} "
                            f"+{max(hits.values()):.0f}%)·不潜伏")
                live_color = "green"
            boom_day = c.get("boom_day") or "—"
            if AMBUSH_LEAN_LAYOUT:
                # 精简态（2026-10-15 用户「杂七杂八的话说得太多了」）：一个题材一行——
                # 题材名 + 已回调天数 + 今日资金实况；⛔ 不再复读每题材一行通用模板
                # 「再次启动信号：明日竞价高开/资金转流入即确认回流」（4 个题材 4 遍同样的话），
                # 也⛔ 不再把「今日实时榜未直接命中（以系统资金趋势为准）」这种零信息占位句
                # 逐题材刷 4 遍 —— 没命中就说「今日资金未回流」，一句结论。
                # 第二轮收敛（用户「还是觉得有点多余」）：**没资金确认的方向不再各占一行**
                # （全写「今日资金未回流」＝同一个结论刷 N 遍），聚合进末尾一行；只有真的
                # 出现资金确认（或池内个股已启动）的方向才单独成行——那才是有信息量的那一行。
                if live_tag.startswith("—") and not hot_codes:
                    pending.append(f"{nm}（回调{cd}天）")
                    continue
                bg.append(_div(
                    f"<text_tag color='purple'>▶ {nm}</text_tag>（已回调 {cd} 天）｜"
                    f"<text_tag color='{live_color}'>{live_tag}</text_tag>"
                ))
                continue
            bg.append(_div(
                f"<text_tag color='purple'>▶ {nm}</text_tag> 已回调 {cd} 天\n"
                f"今日：<text_tag color='{live_color}'>{live_tag}</text_tag>\n"
                f"再次启动信号：{c.get('trigger') or '—'}"
            ))
            _member_rows = 5 if AMBUSH_BG_MEMBER_ROWS is None else int(AMBUSH_BG_MEMBER_ROWS)
            if pool and _member_rows > 0:
                lines = "\n".join(_pool_line(s, hits.get(str(s.get("code") or "").split(".")[0]))
                                  for s in pool[:_member_rows])
                bg.append(_div(f"📦 相关个股（爆发日 {boom_day} 的持仓记录·附今日实时涨跌）：\n{lines}"))
            bg.append(_hr())
        # 待回流方向聚合（2026-10-15 第二轮收敛）：逐条各占一行、每行都只写「今日资金未回流」
        # ＝同一个结论刷 N 遍，零信息。改成一行汇总，只保留「哪些方向 + 回调几天」。
        if pending:
            bg.append(_div(
                f"<text_tag color='neutral'>▶ 其余 {len(pending)} 个方向今日资金未回流（只等钱进来）："
                + " · ".join(pending) + "</text_tag>"))
        # 今日已排除：压缩成一行**只列方向名**（2026-10-15 第二轮收敛）。两个原因：
        # ① 旧版给每个题材复读一遍「（板块内 N 只今天已经涨停·已经启动）」，8 个题材 8 遍同句；
        # ② 「涨停家数」在下面 🧠 涨停与催化段已经按当日归因报过一次，两处口径还不同（一个数
        #    当日池内 ≥9.8% 的只数、一个数涨停归因家数），并排放着像互相打脸 —— 只留名字，
        #    只数交给那段说一次。
        if zt_excluded:
            bg.append(_div("<text_tag color='green'>⛔ 今日已排除（今天已涨停启动·不再潜伏）："
                           + " / ".join(zt_excluded) + "</text_tag>"))
            bg.append(_hr())
    else:
        bg.append(_div("今日无有望回流的题材（待回流方向与退潮观察均为空），等板块回调满 2 天且资金重新流入再说"))
        bg.append(_hr())

    #  背景·个股断板回流（方案1 2026-09-17 用户拍板·个股维度通道）
    bg += _ambush_reflow_block(ROOT.parent / "cache")

    if bg:
        blocks.append(_div("<text_tag color='neutral'>🔎 背景参考（当日盘面事实·不参与今日推票）</text_tag>"))
        blocks.append(_hr())
        blocks += bg

    # 模块③ 今日板块资金流（实时·左红右绿双列）
    # 2026-10-09 用户「不显示今日板块资金流」→ 默认整块不渲染（AMBUSH_SHOW_SECTOR_FLOW=False）。
    sr = (live or {}).get("sector_risk") or {}
    if AMBUSH_SHOW_SECTOR_FLOW and (sr.get("inflow_top") or sr.get("outflow_top")):
        blocks.append(_div("<text_tag color='neutral'>💰 今日板块资金流（实时·左红右绿）</text_tag>"))
        blocks.append(_flow_columns(sr.get("inflow_top"), sr.get("outflow_top")))
        blocks.append(_hr())

    # ── AI 研判层（2026-08-28 用户「除了数据，需要 AI 介入和思考」）──
    if ai_flow or stock_scan or picks or ai_verdict:
        # 模块④ 最近的板块资金流向（AI 研判）
        if ai_flow:
            blocks.append(_div("<text_tag color='purple'>🧠 AI研判·最近板块资金流向</text_tag>"))
            for line in ai_flow.split("\n"):
                line = line.strip()
                if line:
                    blocks.append(_div(line))
            blocks.append(_hr())
        # 模块⑤ 个股资金·地位·人气（AI 研判）——2026-10-15 用户「参照个股·资金/地位/人气
        # 不需要这个模块」→ 默认**整块不渲染**（`AMBUSH_SHOW_SCAN=False`）。
        # 置 True 回退旧行为：渲染名单外票，并按 `AMBUSH_SCAN_DEDUP` 与「当日候选池 / 推票」
        # 去重（候选名单已把收盘/十日线/量比/资金讲全，同一票不复读第二遍）。
        if AMBUSH_SHOW_SCAN and stock_scan:
            _scan_rows = stock_scan
            if AMBUSH_SCAN_DEDUP:
                _seen = _ambush_pullback_codes(ROOT.parent / "cache", now.strftime("%Y-%m-%d"))
                for p in picks:
                    for s in (p.get("stocks") or []):
                        if isinstance(s, dict) and s.get("code"):
                            _seen.add(str(s["code"]))
                _scan_rows = [s for s in stock_scan
                              if str((s or {}).get("code") or "") not in _seen]
            if _scan_rows:
                blocks.append(_div(
                    "<text_tag color='purple'>🔥 参照个股·资金/地位/人气（名单外·只做方向参照）</text_tag>"))
                for s in _scan_rows:
                    nm = str(s.get("name") or "")
                    code = str(s.get("code") or "")
                    role = str(s.get("role") or "")
                    pct = s.get("pct")
                    fund = s.get("fund") or ""
                    hot = s.get("hot") or ""
                    note = s.get("note") or ""
                    line = f"{_stock_hl({'name': nm, 'code': code}, 'purple')} {role}"
                    if isinstance(pct, (int, float)):
                        c = "red" if pct >= 0 else "green"
                        line += f" <text_tag color='{c}'>{pct:+.1f}%</text_tag>"
                    if fund:
                        line += f" ·{fund}"
                    if hot:
                        line += f" ·人气:{hot}"
                    if note:
                        line += f"\n　{note}"
                    blocks.append(_div(line))
                blocks.append(_hr())
        # 模块⑥ 🎯 今日尾盘买入标的（AI 推票）——默认已置顶（模块⓪），
        # 仅当 AMBUSH_PICKS_AT_TOP=False 时回落到此处（原「置底」位置）。
        if picks and not AMBUSH_PICKS_AT_TOP:
            blocks += _picks_block()
        # 🎯 综合研判（AI·置底）
        if ai_verdict:
            blocks.append(_div(f"<text_tag color='purple'>🎯 综合研判（AI）</text_tag>\n{ai_verdict}"))
            blocks.append(_hr())

    # 页脚（来源 + 免责）
    blocks += _footer(hhmm)
    return {"title": title, "color": "purple", "elements": blocks}


def _ambush_banned_terms(txt: str) -> list[str]:
    """文案语义化铁律黑名单（09-01 用户纠错 + shared_rules）：
    内部术语/字段名一律不得上卡，须大白话改写（re-entry→待回流方向、watch→观察阶段、
    cooled→已回调、引擎→系统、置信度/字段名 不上卡）。命中→推送前拦截。
    注意：不含已语义化合法词（观察阶段/已回调/再次启动信号/相关个股）。"""
    banned = [
        "re-entry", "reentry", "watch_pool", "跟踪池", "cooled", "冷却",
        "已歇", "回流引擎", "引擎", "置信", "confidence", "theme_rotation",
        "candidate", "验证锚", "flow_5d", "flow_trend", "boom_day",
        "cooled_days", "confidence_factors", "watch",
    ]
    low = txt.lower()
    return [t for t in banned if t.lower() in low]


def validate_ambush_card(card: dict, picks: list | None = None,
                         pool_codes: set[str] | None = None) -> tuple[bool, list[str]]:
    issues: list[str] = []
    txt = json.dumps(card, ensure_ascii=False)
    if "定调" not in txt:
        issues.append("饱满度缺失定调模块")
    if "次日回流候选" not in txt and "无明确回流候选" not in txt and "次日有望回流的题材" not in txt and "无有望回流的题材" not in txt:
        issues.append("饱满度缺失回流候选模块")
    if "font color=" in txt:
        issues.append("配色：卡片含 <font>（飞书不渲染，须改 <text_tag>）")
    # 🎯 结论置底铁律（2026-08-27 用户纠错）：综合研判/推票必须在资金流之后（卡片末尾）
    # ⚠️ 2026-10-14 用户「买入标的直接放到整个卡片的头部」→ 置顶模式下推票不再受「置底」约束，
    #    综合研判仍恒置底（两者不同模块，考的是「结论要看得见」，别把新规则套到老模块上）。
    # 推票块标题（含历史卡兼容·2026-10-15 改名）：✅ 今日买入（现行）→ 🎯 今日尾盘买入标的（旧）
    # → 🎯 次日重点标的（更早）。注意用 bool 判定，⛔ 不可写成 `"" in txt`（恒真会误触发置底校验）。
    _pick_head = next((h for h in ("✅ 今日买入", "🎯 今日尾盘买入标的", "🎯 次日重点标的")
                       if h in txt), "")
    if "🎯 综合研判" in txt or _pick_head:
        if "💰 今日板块资金流" in txt:
            pos_flow = txt.find("💰 今日板块资金流")
            pos_pick = -1 if AMBUSH_PICKS_AT_TOP else (txt.find(_pick_head) if _pick_head else -1)
            pos_verdict = txt.find("🎯 综合研判")
            tail_ok = all(p >= 0 and p > pos_flow for p in (pos_pick, pos_verdict) if p >= 0)
            if not tail_ok:
                issues.append("结论置底违规：🎯综合研判 必须在 💰资金流 之后"
                              "（推票置顶模式下不再参与该约束）")
    # 文案语义化铁律（2026-09-04 补：AI 注入层首推漏网「机械引擎」→ 推送前强制拦截）
    hits = _ambush_banned_terms(txt)
    if hits:
        issues.append(f"文案语义化违规：含内部术语 {hits}，须大白话改写后再推（09-01 铁律）")
    # 🎯 推票只能出自阴线回踩池（2026-10-09 用户「尾盘潜伏卡都改为那套逻辑」）
    if PICKS_POOL_ENFORCE and pool_codes is not None and picks:
        outside: list[str] = []
        for p in picks:
            stocks = (p or {}).get("stocks") if isinstance(p, dict) else None
            if not isinstance(stocks, list):
                continue
            for s in stocks:
                if not isinstance(s, dict):
                    continue
                code = str(s.get("code") or "")
                if code and code not in pool_codes:
                    outside.append(f"{s.get('name') or ''}({code})")
        if outside:
            issues.append(f"推票越界：今日买入标的不在「📋 今日候选」池内 {outside}"
                          "（2026-10-09 用户「都改为那套逻辑」，池外的不得进推票）")
    # 📈 每只推票必带当日涨跌幅（2026-10-09 用户「所有个股带上涨跌幅」）
    missing_pct: list[str] = []
    for p in picks or []:
        stocks = (p or {}).get("stocks") if isinstance(p, dict) else None
        if not isinstance(stocks, list):
            continue
        for s in stocks:
            if isinstance(s, dict) and not isinstance(s.get("pct"), (int, float)):
                missing_pct.append(f"{s.get('name') or ''}({s.get('code') or ''})")
    if missing_pct:
        issues.append(f"涨跌幅缺失：{missing_pct} 未带当日涨跌幅，补齐后再推"
                      "（2026-10-09 用户「所有个股带上涨跌幅」）")
    return (len(issues) == 0, issues)


def _cli_ambush(argv: list[str]) -> int:
    """CLI：python build_cards.py ambush [--data ai_data.json] [--webhook X] [--self-check] [--dry-run]

    --data 可选：AI 研判层注入（ai_flow/stock_scan/picks/ai_verdict），缺省纯机械数据卡。
    """
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--webhook")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    ai_data = None
    if args.data:
        ai_data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    card = build_ambush_card(ai_data)
    if args.self_check:
        picks = (ai_data or {}).get("picks") or []
        pool_codes = None
        if PICKS_POOL_ENFORCE:
            pool_codes = _ambush_pullback_codes(ROOT.parent / "cache", _now_bj().strftime("%Y-%m-%d"))
        ok, issues = validate_ambush_card(card, picks=picks, pool_codes=pool_codes)
        if not ok:
            print("❌ 自查校验未通过，拒绝推送：")
            for i in issues:
                print("  -", i)
            return 2
        print("✅ 自查校验通过")
    path = CACHE_DIR / f"ambush_card_{_now_bj().strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    print("生成卡片:", path, "元素数:", len(card["elements"]))
    cmd = [sys.executable, str(LIMAO_SCRIPTS / "push_card.py"), str(path)]
    if args.webhook:
        cmd += ["--webhook", args.webhook]
    if args.dry_run:
        cmd += ["--dry-run"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    print((r.stdout + r.stderr).strip())
    return r.returncode


def _cli_panqian(argv: list[str]) -> int:
    """CLI：python build_cards.py panqian --data <json> [--webhook X] [--self-check] [--dry-run]"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--webhook")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    card = build_panqian_card(data)
    if args.self_check:
        ok, issues = validate_panqian_card(card, data)
        if not ok:
            print("❌ 自查校验未通过，拒绝推送：")
            for i in issues:
                print("  -", i)
            return 2
        print("✅ 自查校验通过")
    path = CACHE_DIR / f"panqian_card_{_now_bj().strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    print("生成卡片:", path, "元素数:", len(card["elements"]))
    cmd = [sys.executable, str(LIMAO_SCRIPTS / "push_card.py"), str(path)]
    if args.webhook:
        cmd += ["--webhook", args.webhook]
    if args.dry_run:
        cmd += ["--dry-run"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    print((r.stdout + r.stderr).strip())
    return r.returncode


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "panqian":
        sys.exit(_cli_panqian(sys.argv[2:]))
    elif len(sys.argv) > 1 and sys.argv[1] == "ambush":
        sys.exit(_cli_ambush(sys.argv[2:]))
    else:
        print("usage: build_cards.py [panqian|ambush] [--dry-run] [--self-check] [--webhook KEY]")
        sys.exit(1)
