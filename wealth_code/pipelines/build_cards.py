#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财富密码卡片构建 + 推送（飞书单入口 push_card.py）。

四张卡（财富密码模块）：
- build_eod_card():  📊 财富密码·盘后候选 —— 收盘后由 pipeline_eod 产出候选时推送，
                      含连板天梯 + 接力池(龙头) + 观察池 + 强势个股概览（晚间计划）。
- build_verify_card(): ⚡ 财富密码·竞价验证 —— 次日 09:25 由 pipeline_verify 竞价验证后
                      推送，含达标买入信号(super/expected) + 竞价涨幅/额 + 净值/策略指标
                      （次日执行）。
- build_panqian_card(): 📊 盘前预测·全量 —— 06:06 由技能组装 data 后推送（隔夜消息+今日主线）。
- build_ambush_card(): 🎯 尾盘潜伏·次日回流预期 —— 14:50 定时，读 theme_rotation 回流引擎
                      ＋ latest 实时板块资金，机械筛选次日回流候选（不硬编码任何板块规律）。

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
USER_ENTRY_MAX_GAP_PCT = _ai_caliber.USER_ENTRY_MAX_GAP_PCT
CACHE_DIR = ROOT / "cache"
LIMAO_SCRIPTS = ROOT.parent / "scripts"   # 狸猫猎手/scripts/push_card.py 单入口


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
#         并括注「计划 N 只」（AI 判参与）与「机械达标 M 只」；✅ 段按 mech_can_enter 筛选
#         （段名「✅ 竞价达标·机械参考」——与账本口径解耦，机械信息不丢）。
# False = 一键回退旧显示行为（买入信号 = 机械 can_enter 只数）。
# ⛔ 显示层只读不写：不改 can_enter（由 core/ai_caliber 决定）、不改 signal_eval。
AI_CALIBER_BUY_SIGNAL = True


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
    # 题材板块效应（2026-08-25 用户「孤军判定不准确·要全局化看板块效应」）：
    # 行业孤军但题材层有涨停梯队 → 紫胶囊「题材板块·N家涨停」替代孤军警示（避免误伤
    # 板块效应票——实证：凯莱英 医疗服务行业孤军、医药题材8家涨停）。真孤军
    # （行业+题材都只有自己）→ 红胶囊「板块孤军」警示（承接力风险·2026-08-24 用户）。
    if it.get("theme_has_sector_effect"):
        th = str(it.get("theme_name") or "")
        n = int(it.get("theme_mates_cnt") or 0)
        if th and th != "—":
            tags.append((f"题材板块·{th}{n}家涨停", "purple"))
    elif it.get("sector_is_lone"):
        # 白话化（2026-09-11·禁项扫描）：原「板块孤军」属圈内造词 → 改直白描述
        tags.append(("同方向只它1家", "red"))
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
    # 封单金额（透传自 pipeline_eod seal_amt，亿）
    seal = it.get("seal_amt")
    if isinstance(seal, (int, float)) and seal > 0:
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
    if it.get("is_sector_dragon"):
        tags.append(("题材龙一", "neutral"))
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
_STATUS_PLAIN = {
    "expected": "符合预期",
    "super": "超预期",
    "weak": "低于预期",
    "pending": "待观察",
    "reject": "没达标",
    "miss": "没达标",
}


# 观察池分组 → 白话（仅显示层别名·数据键 watch_group 不动）
_WATCH_GROUP_PLAIN = {
    "容量核心": "板块大票（体量大）",
    "高标/题材核心": "高连板 / 题材领头",
    "补充观察": "补充观察",
}


def _status_plain(raw) -> str:
    """signal_status 英文档位 → 中文白话；未收录原样返回（不吞数据）。"""
    s = str(raw or "").strip()
    return _STATUS_PLAIN.get(s, s)


def _gap_strength_short(it: dict) -> str:
    """高开强度短标签（预期区间旁点睛）：超预期 / 高开 / 低开。
    三档对齐双维度规则引擎 gap_strength：strong=超预期、mid=高开、weak=低开。
    颜色由调用处统一包红（与预期同步），此处只给纯文本（用户2026-08-13定）。"""
    s = (it.get("expectation") or {}).get("gap_strength")
    return {"strong": "超预期", "mid": "高开", "weak": "低开"}.get(s, "")

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
        # 三档判定 = `ai_caliber.gap_zone`（2026-09-17「A 计划」·与账本成交同源同容差，
        #   防「标题行说高于预期 / AI 判断行说符合预期 / 账本却在买」三方脱节）
        _zone = _ai_caliber.gap_zone(gap, erng)
        if super_min is not None and gap >= float(super_min):
            verdict = "🔺超预期"
        elif _zone == "above":
            verdict = "🔺高于预期"
        elif _zone == "ok":
            verdict = "✅符合预期"
        else:
            verdict = "⚠️低于预期"
        # 炸板类（炸板回封/放量烂板/任何炸板）次日红开：超预期信号，但需辨真假高开。
        # 2026-08-18 修：仅当判为非「低于预期」才追加——微红近平开（如 +0.3%）根本谈不上
        # "高开"，不能辨真假（曾把 苏州天脉/贵研铂业 的"低于预期"错配成"·辨真假高开"）。
        form = (exp.get("seal_form") or "")
        zbc = int(exp.get("seal_zbc") or 0)
        if (form in ("炸板回封", "放量烂板") or zbc >= 1) and gap > 0 and "低于预期" not in verdict:
            verdict = verdict + "·辨真假高开"
    else:
        verdict = ""
    return exp_ref, verdict


def _verdict_tag(verdict: str) -> str:
    """预期判定标签（2026-08-18 修颜色）：超预期/符合预期=红（机会色），低于预期=中性灰（未达标）。
    原先所有 verdict 一律红色，苏州天脉/贵研铂业「低于预期」被标红，与灰化语义不符。"""
    if not verdict:
        return ""
    color = "neutral" if "低于预期" in verdict else "red"
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
    if dd >= NAV_DRAWDOWN_WARN_PCT:
        return [_div(
            f"🚨 净值回撤线 · <text_tag color='green'>-{dd:.2f}%</text_tag> 跌破{NAV_DRAWDOWN_WARN_PCT:.0f}%线\n"
            f"高点{peak_txt}→当前{cur_txt}，回撤线之下·降档防守（退学炒股心法：回撤50%需100%盈利才回本，先控回撤再谈进攻）"
        )]
    if dd >= NAV_DRAWDOWN_HINT_PCT:
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


def _narr_segments(narrative) -> list[tuple[str, str]]:
    """点评 → [(段名, 正文)]（段名 == "" 表示续行/无标题行）。"""
    s = str(narrative or "").strip()
    if not s:
        return []
    rows = [c.strip() for c in s.split("\n") if c.strip()]
    if len(rows) <= 1:
        # 单段文本：先按「段名：正文」认白名单段名（2026-09-18）——全局旁白若被写成单段
        # 「盘面：今天市场…」，也必须认成**盘面段**，否则会退化成「今日走势」而无法随跨日平移
        # 成「昨日盘面」；认不出段名才走锚点启发式（存量点评 blob 走这条）。
        lab, body = _narr_label_of(s)
        if lab and body:
            return [(lab if i == 0 else "", ln) for i, ln in enumerate(_narr_wrap(body))]
        return _narr_heuristic(s)          # 单段存量文本 → 锚点启发式
    out: list[tuple[str, str]] = []
    for c in rows:                          # agent 已显式分段：逐行取「段名：正文」
        lab, body = _narr_label_of(c)
        body = str(body or "").strip()
        if not body:
            continue
        for i, ln in enumerate(_narr_wrap(body)):
            out.append(((lab or "") if i == 0 else "", ln))
    return out


_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


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


def _narr_render(narrative, indent: str = "　　", *, prev: bool = False) -> str:
    """点评 → 语义分段的 lark_md 多行文本（段名加粗 / 风险段 turquoise 标签置尾）。

    prev: 文案**撰写日 ≠ 读卡日**（跨日复用 —— 竞价卡的点评/旁白/AI 理由都写于上一交易日
          盘后）→ 段名与正文统一 +1 天平移（今日走势→昨日走势、今天→昨天），
          使「上一交易日的结论」在次日卡上读作「昨日」。⛔ 当日卡（盘后 eod / 接力卡）保持
          默认 False，文案原样上卡。
    返回 "" 表示无点评（诚实省略，不占位不编造）。
    """
    segs = _narr_segments(narrative)
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
        f"　　<font color='grey'>情绪={sent}｜涨停{total_zt}只｜跌停{ld}｜炸板{broken}（真实脉冲快照）</font>"
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
        return " ｜ ⚠️兜底档（市场偏冷·仅供参考·风险高）"
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


def _stock_hl_boards(it: dict, color: str = "purple") -> str:
    """竞价验证卡专用：个股标签追加**当前连板数**。

    格式 `中百集团(000759) 82.5 3板`（接力紫/观察黄）；首板标「首板」；无板数据不标（诚实省略）。
    ⛔ 不动共享的 _stock_hl（eod 卡模板已验收），仅 verify 卡使用。
    """
    score = it.get("score")
    score_txt = f" {score:.1f}" if isinstance(score, (int, float)) else ""
    return _hl(f"{it.get('name', '')}({it.get('code', '')}){score_txt}{_boards_txt(it)}", color)


# ── 股性档阈值（2026-09-17 用户「股性定选谁」落地·可逆常量）──────────────
GENE_HABITUAL_MIN = 40.0   # 历史晋级率 ≥40% → 惯犯（接力强·优先）
GENE_WEAK_MAX = 15.0       # 历史晋级率 <15%  → 偶犯（接力弱·降级）


def _load_hist_gene(code: str) -> dict:
    """读该票历史涨停基因档案（cache/zt_genes/hist_{code}.json）；缺失 → {}。"""
    if len(str(code)) != 6:
        return {}
    try:
        return json.loads((ROOT.parent / "cache" / "zt_genes" / f"hist_{code}.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return {}


def _gene_grade(g: dict) -> tuple[str, str]:
    """股性档（惯犯 / 普通 / 偶犯）—— 2026-09-17「股性定选谁」从**只展示**升级为**可判定因子**。

    判据主看历史晋级率 `promo_rate_hist`（涨停次日再涨停 %）：
      ≥40% → 惯犯（接力强·同方向内优先、可给足仓位）
      15~40% → 普通
      <15%  → 偶犯（接力弱·降级：仓位减半；环境弱时直接跳过）
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
        return "惯犯", "red"
    if v < GENE_WEAK_MAX:
        return "偶犯", "green"
    return "普通", "neutral"


def _hist_gene_line(it: dict) -> str:
    """历史涨停基因行（2026-09-09 用户「历史涨停因子很重要·要加进卡里」·eod/verify 卡常驻渲染）：
    读 cache/zt_genes/hist_{code}.json（新浪不复权日K自算·6年窗·scripts/zt_genes.py hist 产出）。
    档案存在 → 返回灰字基因行（**股性档**·250日涨停次数·历史最长板·晋级率·首板次日高开）；
    档案缺失（未预生成/抓取失败）→ 返回 "" 诚实省略，不硬凑不报错。
    ⚠️ 2026-09-17：新增「股性档（惯犯/普通/偶犯）」，供 AI 判定与人工速读（`_gene_grade`）。"""
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
    """次一交易日（跳周末）——与 pipeline_verify._next_day / ai_caliber.next_trade_day 同口径。
    本地复制以免渲染层反向依赖 core（build_cards 可被独立 importlib 加载·测试沙箱无 sys.path）。"""
    from datetime import datetime as _dt, timedelta as _td
    try:
        d = _dt.strptime(str(date10), "%Y-%m-%d") + _td(days=1)
        while d.weekday() >= 5:
            d += _td(days=1)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return str(date10)


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


def _ai_participate_count(raw: dict) -> int:
    """AI 判「参与」的只数 —— 买入口径唯一基准（2026-09-14 用户「我想统一为AI口径」）。

    与 `_ai_plan_block` 的买入名单同源同判：只数 = views 里 view=="participate" 的条目数。
    无视图/空视图 → 0（调用方须先判 views 是否存在，缺失时回退机械口径，不编造）。
    """
    views = raw.get("views") or {}
    return sum(1 for v in views.values()
               if str((v or {}).get("view") or "") == "participate")


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
    _zone = _ai_caliber.gap_zone(gap, _exp)
    if _zone == "unknown":
        return None
    g = f"{gap:+.2f}%"
    if _zone == "below":
        return (f"→ 今日竞价{g}·不及预期 → 放弃", "green")
    if _zone == "above":
        # 2026-09-17 A2：above ＝ **绝对高开太多**（gap > 5% 用户硬线），非「超模型区间」。
        return (f"→ 今日竞价{g}·高开太多（超5%上限）→ 继续观察", "yellow")
    return (f"→ 今日竞价{g}·符合预期", "red")


def _ai_view_line(it: dict, ai_view: dict) -> str:
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
    view = str(v.get("view") or "")
    label = {"participate": "参与", "skip": "放弃", "watch": "观察"}.get(view, view or "—")
    color = "red" if view == "participate" else ("green" if view == "skip" else "purple")
    # 🚪 hibernate 逃生阀（2026-09-18 用户拍板 A）：AI 判「观察」但当日带量转强 → 已小仓买入。
    #    ⛔ 必须改写标签，否则个股段仍显示「观察」而账本/持仓里有成交 → 卡面自相矛盾。
    if view != "participate" and ((it.get("performance") or {}).get("open_check") or {}).get("hibernate_valve"):
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


def _ai_plan_block(raw: dict, records: list[dict]) -> list[dict]:
    """🤖 AI 昨日买入计划（2026-09-11 立项·2026-09-18 时点口径修正）。

    用户原话（2026-09-11）：「竞价卡要告诉我今天买什么，并统计 AI 自己的收益」。
    ⛔ 时点（2026-09-18）：「毕竟是上一个交易日的结论」——标题写**昨日**买入计划，
    段内所有时点词按「撰写日→读卡日」+1 天平移（今天→昨天），
    只保留「今日竞价」指代当日实况读数（见 `_shift_day_words`）。

    数据源 cache/ai_view_{trade_date}.json —— AI 在**上一交易日盘后**自定（不看今日盘面，
    故不存在用未来信息作弊）：
      · stance/stance_label/direction/note → 攻守定调与方向
      · views[code].view → participate 买入 / watch 观察 / skip 放弃 + reason + entry_max_gap
    成交判定：取当日竞价 gap_pct 与**用户 5% 硬线**比较 → 买到 / 没买到（高开超上限·诚实标注）。
    收益：AI 独立轨 ai_nav_history.jsonl 聚合；⛔ 不写 holdings、与真人持仓无关（观点层）。
    """
    views = raw.get("views") or {}
    if not views:
        return []
    by_code: dict[str, dict] = {}
    for r in records:
        by_code[str(r.get("code") or "")] = r
    _mg = raw.get("entry_max_gap")
    max_gap = float(_mg) if isinstance(_mg, (int, float)) else 3.0
    # 逐票上限兜底（2026-09-14）：views 缺字段时回查 AI 决策台账，保证卡面判定与账本同值
    limits = _ai_decision_limits(str(raw.get("date") or ""))
    dd = str(raw.get("decision_date") or "")
    mmdd = dd[5:] if len(dd) >= 10 else dd
    stance_label = (str(raw.get("stance_label") or "")
                    or _AI_STANCE_FALLBACK.get(str(raw.get("stance") or ""), ""))
    direction = str(raw.get("direction") or "")
    note = str(raw.get("note") or "").strip()

    buys: list[tuple[str, dict]] = []
    others: list[str] = []
    # 🚪 hibernate 竞价逃生阀（2026-09-18 用户拍板「A」）：AI 判「观察」但因
    #    「带量转强 ∧ 方向内板数最高」被**当日唯一**放行小仓试错的票 —— 必须与普通买入
    #    **同列展示**，否则卡面会出现「AI 昨日没有要买的」却在持仓/账本里真有成交
    #    （自相矛盾·2026-09-18 用户刚批评过同类「观察又说放弃」打架）。
    valve_codes = {str(r.get("code") or "") for r in records
                   if ((r.get("performance") or {}).get("open_check") or {}).get("hibernate_valve")}
    for _code, _v in views.items():
        _v = _v or {}
        _view = str(_v.get("view") or "")
        _code = str(_code)
        if _view == "participate" or _code in valve_codes:
            buys.append((_code, _v))
        else:
            _r = by_code.get(_code) or {}
            others.append(str(_r.get("name") or _v.get("name") or _code))

    blocks: list[dict] = [_div(
        # ⛔ 时点（2026-09-18 用户「毕竟是上一个交易日的结论·不要用今日·改成昨日」）：
        #    这份计划由**上一交易日盘后**定死，⛔ 标题禁写「AI 今日买入计划」；
        #    括注同时交代两个时点：昨日（盘后定）↔ 今日（竞价定成交）。
        f"**🤖 AI 昨日买入计划**<font color='grey'>（{mmdd} 盘后定 · 今日竞价定成交）</font>"
    )]
    head = []
    if stance_label:
        head.append(f"AI 昨日判断：{stance_label}")   # ⛔ 禁写「AI 判断」（无时点会被读成当日）
    if direction:
        head.append(f"方向：{direction}")
    if head:
        blocks.append(_div("｜".join(head)))
    if note:
        # note 由上一交易日盘后撰写 → 时点词 +1 天平移（今天→昨天），否则次日读成当日盘面。
        blocks.append(_div(f"<font color='grey'>{_shift_day_words(note)}</font>"))

    if valve_codes and buys:
        blocks.append(_div("　　<font color='grey'>⚠️ 昨日收盘定的是空仓——只有下面这一只是今日竞价破例小仓试错"
                           "（它带了量、是方向里板数最高的）</font>"))
    if not buys:
        blocks.append(_div("<font color='grey'>昨日没有选出要买的（AI 自己空仓）</font>"))
    for code, v in buys:
        r = by_code.get(code) or {}
        oc = (r.get("performance") or {}).get("open_check") or {}
        gap = oc.get("gap_pct")
        _valve = bool(oc.get("hibernate_valve"))
        # ⛔ 生效上限 = 用户 5% 硬线（2026-09-14）——⛔ 不再读 AI 自定 entry_max_gap，
        #    否则卡面会与账本/结算打架（AI 自定值仅留痕于 ai_view，不上卡）。
        limit = USER_ENTRY_MAX_GAP_PCT
        name_txt = _hl(f"{r.get('name') or v.get('name') or code}({code})", "violet")
        if isinstance(gap, (int, float)):
            gap_txt = f"竞价{gap:+.2f}%"
            if _valve:
                # 逃生阀票：AI 盘后判「观察」→ 竞价带量转强后小仓试错（诚实标注来源差异）
                # ⛔ 仓位文案换算：0.2 → 「2 成」（×10），不是 ×100（否则显示「20 成」）。
                _pc = int(round(_ai_caliber.HIBERNATE_VALVE_POSITION * 10))
                tag = (f"<text_tag color='orange'>小仓试错</text_tag>"
                       f"<font color='grey'>（空仓日破例·只买 {_pc} 成）</font>"
                       if gap <= limit else
                       f"<text_tag color='green'>没买到</text_tag>（高开超{limit:g}%）")
            else:
                tag = (f"<text_tag color='red'>买入</text_tag>（高开{limit:g}%以内）"
                       if gap <= limit else
                       f"<text_tag color='green'>没买到</text_tag>（高开超{limit:g}%）")
        else:
            gap_txt, tag = "竞价数据未到", "<font color='grey'>待竞价</font>"
        blocks.append(_div(f"{name_txt}{_boards_txt(r)} {gap_txt} → {tag}"))
        reason = str(v.get("reason") or "").strip()
        if reason:
            # ⛔ 去重（2026-09-15 用户「个股下的话术重复了」）：同源 `views[code].reason` 已由
            #    下方个股段 `_ai_view_line` 渲染为「🤖 AI 判断：X + 理由」——计划块再整段渲染即
            #    逐字重复（09-15 实证：超声电子同一段在计划块与达标段各出现一次）。
            #    仅当下方**不会**渲染该票 AI 判断行时才补出，保证信息不丢：
            #      · 该票无对应 record（计划块票不在候选里）；或
            #      · 报价缺失（unavailable 段只出行、不渲染 🤖 AI 判断行）。
            _oc_r = (r.get("performance") or {}).get("open_check") or {}
            _rendered_below = bool(r) and _oc_r.get("decision_status") != "unavailable"
            if not _rendered_below:
                # prev=True：理由写于上一交易日盘后 → 时点词平移（今天→昨天）。
                blocks.append(_div(_narr_render(reason, prev=True)))  # 语义分段（风险独立末行）

    if others:
        shown = "、".join(others[:8]) + ("等" if len(others) > 8 else "")
        # 名单来自**上一交易日盘后**的选票 → 时点必须是「昨日」，⛔ 禁写「今天不买」。
        blocks.append(_div(f"　　<font color='grey'>其余 {len(others)} 只昨日没有安排买：{shown}</font>"))

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

def build_eod_card(payload: dict[str, Any]) -> dict:
    """
    payload = pipeline_eod.run_eod() 的输出（含 relay / watch / diagnostics / regime）。
    标题规范：显式携带日期 + 定调。
    """
    hhmm = _now_bj().strftime("%H:%M")
    date = str(payload.get("date") or "")
    mmdd = date[5:] if len(date) >= 10 else date
    regime = payload.get("regime")
    relay = payload.get("relay") or []
    watch = payload.get("watch") or []
    diag = payload.get("diagnostics") or {}
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
    # 📡 大盘门控（2026-08-18 用户「重溢价·轻大盘」：只作环境开关一行 + 资金双列，不铺开指数/量能/板块风险）
    # 情绪统一 feedback 10 分制（payload.environment 为 feedback_matrix 派生·2026-08-16）
    blocks += _gate_block(payload.get("real_env"), payload.get("environment"))
    # 数据源诚实标注（两者相结合：必盈主池优先，缺失降级选股宝）
    ds = payload.get("data_source") or "xuangubao"
    if ds == "xuangubao":
        blocks.append(_div(
            "<font color='grey'>数据源：选股宝涨停池（备用通道）</font>"
        ))
    else:
        blocks.append(_div("<font color='grey'>数据源：涨停池</font>"))
    # 策略战绩（收盘结算口径）：盘后结算后即可看到上一批次收益（2026-08-12 加；2026-08-13 改整句标红+当前净值）
    m = _load_metrics()
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
        line = f"📈 已记录{cov_txt}次，胜率{wr_txt}，当前收益{ar_txt}，当前净值{nav_txt}"
        if ar is not None and ar < 0:
            blocks.append(_div(f"<font color='green'>{line}</font>"))
        elif ar is not None and ar >= 0:
            blocks.append(_div(f"<font color='red'>{line}</font>"))
        else:
            blocks.append(_div(line))
        _cn = _caliber_note(m)          # 口径分界标注（2026-09-14 起 AI 口径）
        if _cn:
            blocks.append(_div(f"<font color='grey'>{_cn}</font>"))
    # 🚨 净值回撤线（退学炒股心法·语录62·2026-08-24）：回撤≥10% 硬警示 / ≥5% 软提示
    dd_blocks = _nav_drawdown_block(m)
    if dd_blocks:
        blocks.extend(dd_blocks)
    blocks.append(_hr())

    # 连板天梯（用户关切：能否拿到连板天梯 —— 答案：yesterday_zt.json 现成可用）
    ladder = (snap or {}).get("ladder") or {}
    if ladder:
        blocks.append(_div("**🔝 连板天梯**"))
        for k in sorted(ladder.keys(), key=lambda x: int(x), reverse=True):
            names = ladder[k]
            hl_names = "、".join(_hl(n, "red") for n in names)
            blocks.append(_div(f"  **{k}板**：{hl_names}"))
        blocks.append(_hr())

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
            blocks.append(_div(f"　　<text_tag color='red'>⛔ 今日无接力候选{_empty_note}</text_tag> 全部转观察池"))
    else:
        _ice_flag = " ｜ ⚠️市场很冷·候选仅供参考·买不买自决" if regime == "ice" else ""
        blocks.append(_div(f"**🔥 接力池 TOP{len(relay)}（核心候选）**{_relay_mode_note(mode)}{_ice_flag}"))
    weak_flagged = 0
    relay_narr: list[str] = []
    for it in relay:
        exp = it.get("expectation") or {}
        rng = exp.get("expected_range") or ["—", "—"]
        # 日报 verdict（板块被判弱/观察级时记 weak_flagged；重要风险提示保留在独立行）
        dv = it.get("daily_verdict")
        if dv and ("avoid_breadth" in dv.get("flags", []) or "observe" in dv.get("flags", [])):
            weak_flagged += 1
        amt_txt = _amount_txt(it)
        conf = _conf_tag(it)
        cloud = _render_tag_cloud(_candidate_tags(it, suppress_band=uniform_band))
        # 新布局（2026-08-13）：行1=紫色小卡(名称+分数)+tag云紧跟；
        # 行2=现价｜预期%｜高开｜竞价额｜板块｜可信度（预期+高开同红，竞价额去昨基数）
        blocks.append(_div(
            f"<font color='red'>{it.get('relay_rank')}、</font> {_stock_hl(it, 'purple')}"
            + (f" {cloud}" if cloud else "")
        ))
        price = it.get("price")
        price_seg = f"现价{price:.2f}｜" if isinstance(price, (int, float)) else ""
        ai_tag = ""  # AI 竞价区间覆盖仍生效（data.expectation.ai_override），按用户要求不显示 🤖AI 标记
        blocks.append(_div(
            f"　　<font color='grey'>{price_seg}</font>"
            f"<font color='red'>预期{rng[0]}~{rng[1]}%｜{_gap_strength_short(it)}｜</font>"
            f"<font color='grey'>{amt_txt}｜{_theme_dual_tag(it)}｜{conf}{ai_tag}</font>"
        ))
        _gene = _hist_gene_line(it)          # 2026-09-09 历史涨停基因常驻行（有档案才渲染）
        if _gene:
            blocks.append(_div(_gene))
        # 中位承接（2026-08-17 修正：_mid_carry 改为健康度分级 充足/一般/不足——
        # 旧 has_complete 布尔口径在涨停分散日恒 False 导致全部候选刷同一句。
        # 仅真·梯队薄弱（C/D 级·health<6）才提示；B 级「一般」不提示（信息价值低）。）
        if it.get("dim_panel", {}).get("_mid_carry") == "不足":
            _lh = it.get("ladder_health") or 0
            # 白话化（2026-09-14）：去「梯队」圈内造词
            blocks.append(_div("　　⚠️ 中间位置没人跟（等中间的票跟上再考虑接力）"))
        # 反吃日报 verdict：板块被日报判弱时，加「仅龙头独立强势」注记（以日报判断为准）
        if dv and dv.get("tag"):
            blocks.append(_div(f"　　{dv.get('tag')} {dv.get('note')}"))
        # 点评收集到底部集中展示，不与个股混排（名称独占一行；点评按语义分段·2026-09-11）
        if it.get("narrative"):
            relay_narr.append(f"　· {_stock_hl(it, 'purple')}\n{_narr_render(it['narrative'])}")
    if weak_flagged:
        blocks.append(_div(
            f"📋 其中 {weak_flagged} 只已被日报判板块弱/观察级，按纪律仅做龙头、不追板块"
        ))
    blocks.append(_hr())

    # 观察池（按 watch_group 分区展示：容量核心 / 高标题材核心 / 补充观察）
    watch_narr: list[str] = []
    if watch:
        blocks.append(_div(f"**👀 观察池 TOP{len(watch)}**"))
        # 分组兜底：无 watch_group 字段（旧数据）按板数归入合理组
        groups: list[tuple[str, list[dict]]] = []
        for grp in ("容量核心", "高标/题材核心", "补充观察"):
            items = [
                it for it in watch
                if (it.get("watch_group") or ("高标/题材核心" if it.get("boards", 1) >= 3 else "补充观察")) == grp
            ]
            if items:
                groups.append((grp, items))
        for grp, items in groups:
            # 分组名白话化（2026-09-10 用户「不要推专业术语」）——仅显示层别名，数据键不动
            blocks.append(_div(f"  ▸ **{_WATCH_GROUP_PLAIN.get(grp, grp)}**"))
            for it in items:
                exp = it.get("expectation") or {}
                rng = exp.get("expected_range") or ["—", "—"]
                amt_txt = _amount_txt(it)
                conf = _conf_tag(it)
                cloud = _render_tag_cloud(_candidate_tags(it, suppress_band=uniform_band))
                # 新布局（同接力池）：行1=黄色小卡(名称+分数)+tag云紧跟；
                # 行2=现价｜预期%｜高开｜竞价额｜板块｜可信度（预期+高开同红，竞价额去昨基数）
                blocks.append(_div(
                    f"<font color='red'>  {it.get('watch_rank')}、</font> {_stock_hl(it, 'yellow')}"
                    + (f" {cloud}" if cloud else "")
                ))
                price = it.get("price")
                price_seg = f"现价{price:.2f}｜" if isinstance(price, (int, float)) else ""
                ai_tag = ""  # 同接力池：AI 竞价覆盖生效，按用户要求不显示 🤖AI 标记
                blocks.append(_div(
                    f"　　<font color='grey'>{price_seg}</font>"
                    f"<font color='red'>预期{rng[0]}~{rng[1]}%｜{_gap_strength_short(it)}｜</font>"
                    f"<font color='grey'>{amt_txt}｜{_theme_dual_tag(it)}｜{conf}{ai_tag}</font>"
                ))
                _gene = _hist_gene_line(it)          # 2026-09-09 历史涨停基因常驻行（同接力池）
                if _gene:
                    blocks.append(_div(_gene))
                if it.get("narrative"):
                    watch_narr.append(f"　· {_stock_hl(it, 'yellow')}\n{_narr_render(it['narrative'])}")
        blocks.append(_hr())

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

    # 点评集中区：不区分接力/观察分区，按顺序（接力在前、观察在后）连续列出（2026-08-12 用户要求）
    if relay_narr or watch_narr:
        if not watch:
            blocks.append(_hr())  # 无观察池列表时补分隔（有 watch 时观察区尾部已加 hr，避免双 HR）
        blocks.append(_div("**点评**"))
        for line in relay_narr + watch_narr:
            blocks.append(_div(line))
        # 全局拟人旁白（agent 产出，随 payload 传入；此前盘后卡漏渲染，2026-08-12 修复）
        # 可读性重写（2026-08-13 拆行 / 2026-09-11 语义分段）：标题独占一行，正文段名加粗分行。
        gn = payload.get("global_narrative")
        if gn:
            _gn_body = _narr_render(gn).strip()
            if _gn_body:
                # 盘面基准日标注（2026-09-14）：默认与 payload 当日一致 → 不标注；上游若显式给出
                # narrative_date（旁白写于更早的交易日）则标出，防止陈旧旁白被读成当日盘面。
                _lbl = _narr_basis_label(payload.get("narrative_date") or payload.get("date"),
                                         payload.get("date"))
                blocks.append(_div(f"🗣️ **全局**{_lbl}\n{_gn_body}"))

    # 🎯 明日执行判定（2026-09-09 主导决策机械层·decision_outlook.py·读真实文件·禁编数字）
    try:
        from core.decision_outlook import decision_from_files as _decision_from_files
        _dec = _decision_from_files(ROOT.parent)
    except Exception:
        _dec = {}
    if _dec.get("verdict"):
        blocks.append(_hr())
        blocks.append(_div(f"**🎯 明日执行判定（系统建议）** {_dec.get('tone') or ''}"))
        blocks.append(_div(_dec.get("conclusion") or ""))
        _b = _dec.get("basis") or {}
        if _b:
            _b_txt = "｜".join(_decision_fields(_b, _DECISION_BASIS_PLAIN))
            if _b_txt:
                blocks.append(_div(f"　依据：{_b_txt}"))
        for _tr in (_dec.get("triggers") or [])[:2]:
            blocks.append(_div(f"　触发：{_tr}"))
        _r = _dec.get("rules") or {}
        if _r:
            _r_txt = "｜".join(_decision_fields(_r, _DECISION_RULE_PLAIN))
            if _r_txt:
                blocks.append(_div(f"　纪律：{_r_txt}"))
            if _r.get("note"):
                blocks.append(_div(f"　　　{_r.get('note')}"))
        for _rk in (_dec.get("risks") or [])[:2]:
            blocks.append(_div(f"　风险：{_rk}"))

    blocks += _footer(hhmm)

    return {"title": title, "color": "violet", "elements": blocks}


# ── 竞价验证卡 ──────────────────────────────────────────

def _vol_weak_flag(oc: dict) -> str:
    """验证卡唯一量能增量（用户2026-08-13定）：实际竞价成交额 < 预期量能线 → 缩量（蓝），其余不标。
    聚焦竞价指标本身，不做爆量/放量/平量/地量多档判断。"""
    amt = oc.get("auction_amount_yi")
    need = oc.get("auction_amount_need_yi")
    if isinstance(amt, (int, float)) and isinstance(need, (int, float)) and amt < need:
        return " <text_tag color='blue'>缩量</text_tag>"
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
    # 题材板块效应优先于孤军警示（全局化眼光·2026-08-25 用户）
    if r.get("theme_has_sector_effect"):
        n = int(r.get("theme_mates_cnt") or 0)
        th = str(r.get("theme_name") or "")
        if th and th != "—":
            return f"<text_tag color='purple'>🧲同板块一起涨·{th} {n}家涨停</text_tag>"
    ss = r.get("sector_support") or {}
    level = str(ss.get("support_level") or "")
    if level == "lone_no_support":
        if int(ss.get("mates_quoted") or 0) == 0:
            return "<text_tag color='red'>🧲同板块没人跟·高开有风险</text_tag>"
        return "<text_tag color='red'>🧲同板块没人跟·0家高开</text_tag>"
    if level == "support_ok":
        n = int(ss.get("red_open") or 0)
        return f"<text_tag color='purple'>🧲同板块 {n}家高开助攻</text_tag>"
    return ""


def build_verify_card(verify_result: dict[str, Any]) -> dict:
    """
    verify_result = pipeline_verify.run_verify(...) 的输出（含 payload.records / summary）。
    """
    hhmm = _now_bj().strftime("%H:%M")
    summary = verify_result.get("summary") or {}
    records = (verify_result.get("payload") or {}).get("records") or []
    # feedback 10 分制情绪（规则层风险标记用·2026-08-16；pipeline_verify 已透传）
    fb_env = (verify_result.get("payload") or {}).get("environment")

    # 🤖 AI 判断视图（2026-09-10 用户「AI 思考合并进竞价验证」·盘后写入次日卡展示）
    _ai_view = _load_ai_view(str(summary.get("trade_date10") or ""))
    # AI 决策原文（含 stance/direction/note/views）——卡头口径与「🤖 今日买入计划」段共用同一份
    _ai_raw = _load_ai_view_raw(str(summary.get("trade_date10") or ""))
    total = summary.get("total_candidates", len(records))
    buys = summary.get("buy_signals", 0)
    hits = summary.get("hit_signals", 0)
    covered = summary.get("covered", 0)
    nav = summary.get("nav_latest")
    # 冰点 regime（2026-08-19 用户定）：命中照算·禁买不进场·卡片展示命中票
    regime = str(((verify_result.get("payload") or {}).get("environment") or {}).get("regime") or "").strip()
    ice_ban = regime == "ice"

    # ── 买入口径统一为 AI（2026-09-14 用户「我想统一为AI口径」→「全量以 AI 为唯一口径！」）──
    # AI 有判断 → 卡头/定调以 **AI 口径实际能成交的只数**（= 账本口径 = summary.buy_signals）为准，
    #   并括注「计划 N 只」（AI 判参与）与「机械达标 M 只」给出全信息；
    # AI 无判断（视图缺失）或旧 summary（无 caliber 字段）→ 回退上一版行为（参与只数 / 机械口径）。
    # 采用「实买」而非「计划」的理由：计划数会把「AI 要买但竞价开太高没买到」渲染成有信号，
    # 与账本（0 成交）自相矛盾——口径统一后卡头必须与账本同源。
    ai_plan = _ai_participate_count(_ai_raw)
    caliber = str(summary.get("caliber") or "")
    ai_caliber = bool(AI_CALIBER_BUY_SIGNAL and (_ai_raw.get("views") or {}))
    ai_filled = buys if (ai_caliber and caliber == "ai") else ai_plan
    mech_buys = summary.get("mech_buy_signals")
    if mech_buys is None:
        mech_buys = buys if caliber == "" else None   # 旧 summary 回退；新链路缺字段则如实省略

    if ice_ban and not (ai_caliber and ai_filled):
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
        if ai_filled != ai_plan:
            _refs.append(f"计划 {ai_plan} 只")
        if mech_buys is not None:
            _refs.append(f"机械达标 {mech_buys} 只")
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
    blocks.append(_div(
        f"**⚡ 财富密码·竞价验证** | 候选{total}只 {buy_txt} "
        f"已记录{covered}天 | 最新净值{nav_txt}"
    ))
    blocks.append(_hr())

    # 🚨 净值回撤线（退学炒股心法·语录62·2026-08-24）：回撤≥10% 硬警示 / ≥5% 软提示
    dd_blocks = _nav_drawdown_block(_load_metrics())
    if dd_blocks:
        blocks.extend(dd_blocks)
        blocks.append(_hr())

    # 🤖 AI 昨日买入计划（2026-09-11 立项「竞价卡要告诉我今天买什么 + 统计 AI 自己的收益」；
    # 2026-09-18 时点口径修正：计划由上一交易日盘后定 → 标题与段内时点词统一为「昨日」）
    # 数据源 = 上一交易日盘后 AI 自定的 ai_view_{今日}.json；AI 决策先于竞价 → 无非公开信息
    blocks.extend(_ai_plan_block(_ai_raw, records))

    # 👑 核心弱转强（昨日连板核心·今日竞价·2026-08-14 用户定·情绪核心战法）
    core_blocks = _core_reversal_block()
    if core_blocks:
        blocks.extend(core_blocks)
        blocks.append(_hr())

    # 买入信号（super / expected）——名称按接力紫/观察黄分色，竞价指标为核心
    # ⚠️ 口径：本段是**机械判定**（竞价量价达标即列），不等同于 AI 要买的名单（2026-09-14 口径统一）。
    # 🤖 2026-09-14「全量以 AI 为唯一口径」后 `can_enter` = AI 口径（决定账本），本段改按
    #    `mech_can_enter`（机械口径结论）筛选，保持「机械参考」语义不变、与账本互不干扰。
    #    旧 record 无 mech_can_enter 字段 → 回退 can_enter（向后兼容）。
    def _is_mech_pass(_r: dict) -> bool:
        _oc = (_r.get("performance") or {}).get("open_check") or {}
        return bool(_oc["mech_can_enter"]) if "mech_can_enter" in _oc else bool(_oc.get("can_enter"))

    buy_rows = [r for r in records if _is_mech_pass(r)]
    if buy_rows:
        blocks.append(_div("**✅ 竞价达标·机械参考**" if ai_caliber else "**✅ 达标买入信号**"))
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
            hl_color = "purple" if "接力" in bucket else "yellow"
            exp_ref, verdict = _exp_verdict(r, oc)
            dv_tag = r.get("daily_verdict", {}).get("tag", "")
            # 风险提示：2026-09-11 起不再内联在标题行（与下方点评的「⚠️ 风险」段重复且撑长标题行），
            # 统一由 _narr_render 在点评末行以 turquoise 标签呈现（语义顺序：走势→题材→股性→风险）。
            blocks.append(_div(
                f"✅ {_stock_hl_boards(r, hl_color)} {sig} {_gap_tag(gap)}"
                + _verdict_tag(verdict)
                + _sector_support_tag(r)
            ))
            if exp_ref:
                blocks.append(_div(
                    f"　　<font color='grey'>{exp_ref}｜额{amt_txt}/{need_txt}亿{_vol_weak_flag(oc)}｜{bucket}｜"
                    f"{_theme_dual_tag(r)}｜"
                    ""
                    + (f" {dv_tag}" if dv_tag else "")
                    + "</font>"
                ))
            else:
                blocks.append(_div(
                    f"　　<font color='grey'>竞价{gap_txt} 额{amt_txt}/{need_txt}亿{_vol_weak_flag(oc)}｜{bucket}｜"
                    f"{_theme_dual_tag(r)}｜"
                    ""
                    + (f" {dv_tag}" if dv_tag else "")
                    + "</font>"
                ))
            _gene = _hist_gene_line(r)               # 📈 历史股性（2026-09-10 用户要求补·verify 卡）
            if _gene:
                blocks.append(_div(_gene))
            _ai = _ai_view_line(r, _ai_view)         # 🤖 AI 判断（合并进竞价验证）
            if _ai:
                blocks.append(_div(_ai))
            if r.get("narrative"):
                blocks.append(_div(_narr_render(r["narrative"], prev=True)))   # 语义分段·跨日平移（09-18）
        blocks.append(_hr())
    else:
        # 机械参考无达标票。若 AI 口径另有成交，卡头 🤖 AI 计划段已逐票给出
        # （2026-09-14 口径统一后本段只陈述机械面，避免与 AI 段互相打脸）。
        blocks.append(_div(
            "⚪ 机械参考：今日没有票达标（买不买看上面的 🤖 AI 计划）。" if ai_caliber
            else "⚠️ 今日没有能买的信号——空仓，或者只看不动。"))
        blocks.append(_hr())

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
            hl_color = "purple" if "接力" in bucket else "yellow"
            blocks.append(_div(
                f"⛔ {_stock_hl_boards(r, hl_color)} {sig} {_gap_tag(gap)} <text_tag color='red'>不买</text_tag> "
                + _sector_support_tag(r)
            ))
            blocks.append(_div(
                f"　　<font color='grey'>竞价{gap_txt} 额{amt_txt}/{need_txt}亿{_vol_weak_flag(oc)}｜{bucket}｜"
                f"{_theme_dual_tag(r)}</font>"
            ))
            _gene = _hist_gene_line(r)               # 📈 历史股性
            if _gene:
                blocks.append(_div(_gene))
            _ai = _ai_view_line(r, _ai_view)         # 🤖 AI 判断
            if _ai:
                blocks.append(_div(_ai))
        blocks.append(_hr())

    # 观察区（pending：涨幅量能达标但 gap>5% / 高于预期未达超预期夹缝区 / 微红偏弱 → 黄色，等承接确认）
    pending_rows = [r for r in records
                    if (r.get("performance") or {}).get("open_check", {}).get("decision_status") == "pending"]
    if pending_rows:
        blocks.append(_div("**🟡 观察（还没到能买的程度·再看一会儿）**"))
        for r in pending_rows:
            oc = (r.get("performance") or {}).get("open_check") or {}
            gap = oc.get("gap_pct")
            amt = oc.get("auction_amount_yi")
            need = oc.get("auction_amount_need_yi")
            sig = oc.get("signal_label") or oc.get("decision_label") or ""
            gap_txt = f"{gap:+.2f}%" if isinstance(gap, (int, float)) else "—"
            amt_txt = f"{amt:.2f}" if isinstance(amt, (int, float)) else "—"
            need_txt = f"{need:.2f}" if isinstance(need, (int, float)) else "—"
            bucket = str(r.get("bucket_label") or "")
            hl_color = "purple" if "接力" in bucket else "yellow"
            exp_ref, verdict = _exp_verdict(r, oc)
            dv_tag = r.get("daily_verdict", {}).get("tag", "")
            # 风险提示统一由下方点评「⚠️ 风险」段呈现（2026-09-11·同买入段口径）
            blocks.append(_div(
                f"🟡 {_stock_hl_boards(r, hl_color)} {sig} {_gap_tag(gap)}"
                + _verdict_tag(verdict)
                + _sector_support_tag(r)
            ))
            if exp_ref:
                blocks.append(_div(
                    f"　　<font color='grey'>{exp_ref}｜额{amt_txt}/{need_txt}亿{_vol_weak_flag(oc)}｜{bucket}｜"
                    f"{_theme_dual_tag(r)}｜"
                    ""
                    + (f" {dv_tag}" if dv_tag else "")
                    + "</font>"
                ))
            _gene = _hist_gene_line(r)               # 📈 历史股性（verify 卡观察区）
            if _gene:
                blocks.append(_div(_gene))
            _ai = _ai_view_line(r, _ai_view)         # 🤖 AI 判断（合并进竞价验证）
            if _ai:
                blocks.append(_div(_ai))
            if r.get("narrative"):
                blocks.append(_div(_narr_render(r["narrative"], prev=True)))   # 语义分段·跨日平移（09-18）
        blocks.append(_hr())

    # 其余未达标（reject：涨幅或量能不达标 → 灰色简列）
    # 其余：真·未达标（reject）与 报价缺失（unavailable）**分开**——2026-08-14 审计：
    # 原实现把 unavailable 混入"未达标"（语义错误：没报价≠不达标），且 [:12] 静默截断、decision_label 被吞。
    other = [r for r in records
             if (r.get("performance") or {}).get("open_check", {}).get("decision_status") == "reject"]
    missing = [r for r in records
               if (r.get("performance") or {}).get("open_check", {}).get("decision_status") == "unavailable"]

    def _row_div(prefix, r, lbl_pri="decision"):
        oc = (r.get("performance") or {}).get("open_check") or {}
        gap = oc.get("gap_pct")
        if lbl_pri == "decision":
            lbl = oc.get("decision_label") or oc.get("signal_label") or "—"
        else:
            lbl = oc.get("signal_label") or oc.get("decision_label") or "—"
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
        )

    if other:
        blocks.append(_div(f"**⚪ 其余没达标**（{len(other)} 只·高开红/低开绿）"))
        for r in other:
            blocks.append(_row_div("⚪", r, lbl_pri="decision"))
            _gene = _hist_gene_line(r)               # 📈 历史股性（2026-09-10 用户「竞价验证这里没有历史股性也要加上」）
            if _gene:
                blocks.append(_div(_gene))
            _ai = _ai_view_line(r, _ai_view)         # 🤖 AI 判断（合并进竞价验证）
            if _ai:
                blocks.append(_div(_ai))
    if missing:
        blocks.append(_div(f"**⚪ 报价缺失·未判定**（{len(missing)} 只·未拿到 9:25 报价，不构成判断）"))
        for r in missing:
            blocks.append(_row_div("⚪", r, lbl_pri="signal"))

    # 策略指标（最新）
    m = _load_metrics()
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
    gn = verify_result.get("global_narrative")
    if gn:
        _sum = verify_result.get("summary") or {}
        # 撰写日：优先 narrative_date（当日 --narratives 注入时由 run_wealth_code 显式写），
        # 否则回落到候选文件日期（= 上一交易日盘后撰写）。
        _narr_d = verify_result.get("narrative_date") or _sum.get("date10")
        _lbl = _narr_basis_label(_narr_d, _sum.get("trade_date10"))
        _gn_body = _narr_render(gn, prev=bool(_lbl)).strip()
        if _gn_body:
            blocks.append(_div(f"🗣️ **全局**{_lbl}\n{_gn_body}"))

    # 未达标个股点评：⛔ 2026-08-14 用户要求底部不显示「点评」块，已删除（其余未达标行已含简列）

    blocks += _footer(hhmm)
    return {"title": title, "color": "orange", "elements": blocks}


# ── 推送（复用狸猫猎手 push_card.py 单入口）──────────────

def push_card_file(card: dict, *, webhook: str | None = None, dry_run: bool = False) -> tuple[bool, str]:
    """
    写 cache 卡片 JSON，调用狸猫猎手 scripts/push_card.py 单入口推送。
    返回 (ok, output)。推送失败不影响本地数据（已落盘）。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ts = _now_bj().strftime("%Y%m%d_%H%M%S")
    path = CACHE_DIR / f"card_{ts}.json"
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
    blocks.append(_hr())

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
    """宽松匹配候选 theme 与今日实时板块榜，给「今日走势」确认标签。
    命中净流入/进行中/热点 → 承接(red)；命中净流出/走弱 → 降档(green)；未命中 → 中性(以引擎为准)。"""
    names_in: set[str] = set()
    names_out: set[str] = set()
    sr = (live or {}).get("sector_risk") or {}
    for s in (sr.get("inflow_top") or []):
        names_in.add(str(s.get("name") or ""))
    for s in (sr.get("outflow_top") or []):
        names_out.add(str(s.get("name") or ""))
    sd = (live or {}).get("sector_delta") or {}
    for x in (sd.get("ongoing") or []):
        names_in.add(str(x).split("(")[0].strip())
    for hs in ((live or {}).get("hot_spots") or []):
        names_in.add(str(hs.get("sector") or ""))
    t = theme or ""

    def hit(names: set[str]) -> bool:
        for n in names:
            if not n:
                continue
            if t and (t in n or n in t):
                return True
        return False

    if hit(names_out):
        return ("⚠️ 今日列入净流出/走弱榜·回流降档待确认", "green")
    if hit(names_in):
        return ("✅ 今天资金和热度都还在·钱回来的机会更大", "red")
    return ("— 今日实时榜未直接命中（以系统资金趋势为准）", "neutral")


def _pool_line(s: dict) -> str:
    nm = _stock_hl(s)
    cap = s.get("cap_yi")
    cap_txt = f" ·市值{cap:.0f}亿" if isinstance(cap, (int, float)) and cap > 0 else ""
    risk = s.get("risk") or ""
    risk_txt = f" ·{risk}" if risk else ""
    return f"{nm}{cap_txt}{risk_txt}"


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

    for it in (lv.get("leaders") or []):          # 今日涨停池
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
                    f"{st.get('name')}({str(st.get('code')).split('.')[0]})+{float(st.get('pct') or 0):.0f}%")
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
                f"{(' · ' + lboards) if lboards else ''}\n　催化：{desc}")
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
                bits.append(f"{e.get('sector')}·{bb.get('name')}({str(bb.get('code') or '').split('.')[0]})+{float(bb.get('pct') or 0):.0f}%")
            line += "\n　🌱 爆发前夜（可跟踪·竞价确认）：" + "、".join(bits)
        out.append(_div(line))
    out.append(_div("⛔ 以上都是今天盘中真实走强的（涨停潮/还没封板/爆发前夜），不是低位埋伏；明天能不能接力、会不会扩散，要看竞价买盘，别闭着眼追"))
    out.append(_hr())
    return out


# ===== 方案1（2026-09-17 用户「中京电子涨停之后的曲线…你就没有命中呀」拍板）=====
# 个股「断板回流」机械通道：补上「涨停后休整→资金回流再走强」的**个股维度**盲区。
# 与上面的题材相位回流（主题材通道）并列不互斥；口径/开关全部托管 core/reflow_scan.py。
_REFLOW_POOL_FRESH_SEC = 600   # 落盘池复用窗口（同一轮 automation 的 dry-run 与推送不重复扫描）


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
    out: list[dict] = [_div(
        "<text_tag color='purple'>🔁 个股断板回流（涨停后休整·今日资金回流）</text_tag>"
    )]
    for r in cands:
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
    out.append(_div(
        "⛔ 上面是「涨停后休整过、今天钱又回来」的票，性质不是新低位埋伏；"
        "明天能不能接上要看竞价买盘，别闭着眼追"
    ))
    out.append(_hr())
    return out


def build_ambush_card(data: dict | None = None) -> dict:
    """🎯 尾盘潜伏·次日回流预期（14:30 定时）。详见 shared_rules【潜伏卡骨架】。

    双层结构（2026-08-28 用户「除了数据，需要 AI 介入和思考」升级）：
    - 机械数据层：theme_rotation 回流候选 + 今日实时板块资金流（不硬编码规律·完全以实时数据为准）
      ＋🔥 今日新主线·次日接力锚（方案A）+ 🔁 个股断板回流（方案1·core/reflow_scan.py）
    - AI 研判层（data 注入）：🧠 板块资金流向分析 / 🔥 个股资金·地位·人气 / 🎯 次日重点标的推票
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

    title = f"🎯 尾盘潜伏·次日回流预期 {mmdd} | 实时数据驱动"
    blocks: list[dict] = []

    # 模块① 定调（分行·UI 优化铁律：不堆一行）
    blocks.append(_div(
        "<text_tag color='purple'>📌 定调</text_tag> 尾盘依据「前几日爆发 + 今日实时走势」自动筛选次日有望回流的题材\n"
        "🧬 数据来源：近期涨停和资金流向记录 + 盘中实时板块资金\n"
        "⛔ 不主观预判任何板块，完全以实时数据为准"
    ))
    blocks.append(_hr())

    # 模块①.5 今日新主线·次日接力锚（方案A 2026-09-07 用户拍板·爆发日主通道）
    blocks += _ambush_mainline_block(live)

    # 模块② 次日回流候选（核心）
    if core:
        blocks.append(_div("<text_tag color='purple'>🔮 次日有望回流的题材（按近期涨停与资金流向自动筛选）</text_tag>"))
        # 2026-08-28 P0：今日已涨停排除——池内个股今日涨停 → 题材已启动·非潜伏（剔除）；
        # 池内个股今日 ≥5% → 标注「今日已启动·不潜伏」。实证：和远气体今日涨停仍被推潜伏（用户质疑）。
        zt_excluded: list[str] = []
        hot_pending: list[str] = []
        for c in core:
            pool = c.get("watch_pool") or []
            hits = _pool_today_status(pool, live)
            zt_codes = {k for k, v in hits.items() if v >= 9.8}
            hot_codes = {k for k, v in hits.items() if 5.0 <= v < 9.8}
            if zt_codes:
                zt_excluded.append(f"{c.get('theme')}（板块内 {len(zt_codes)} 只今天已经涨停·已经启动）")
                continue
            if hot_codes:
                hot_pending.append(c.get("theme"))
            nm = str(c.get("theme") or "")
            cd = c.get("cooled_days")
            conf = c.get("confidence")
            ft = c.get("flow_trend") or "—"
            live_tag, live_color = _live_confirm(nm, live)
            if hot_codes:
                live_tag = f"⚠️ 池内个股今日已启动({', '.join(sorted(hot_codes))} +{max(hits.values()):.0f}%)·不潜伏 | {live_tag}"
                live_color = "green"
            boom_day = c.get("boom_day") or "—"
            blocks.append(_div(
                f"<text_tag color='purple'>▶ {nm}</text_tag> 已回调 {cd} 天\n"
                f"今日：<text_tag color='{live_color}'>{live_tag}</text_tag>\n"
                f"再次启动信号：{c.get('trigger') or '—'}"
            ))
            if pool:
                lines = "\n".join(_pool_line(s) for s in pool[:5])
                blocks.append(_div(f"📦 相关个股（为爆发日 {boom_day} 的持仓快照，未确认今日走势前不追）：\n{lines}"))
            blocks.append(_hr())
        if zt_excluded:
            blocks.append(_div(f"<text_tag color='green'>⛔ 今日已排除（这些题材今天已经涨停启动，不适合再潜伏）：\n{'；'.join(zt_excluded)}</text_tag>"))
            blocks.append(_hr())
    else:
        blocks.append(_div("今日无有望回流的题材（待回流方向与退潮观察均为空），等板块回调满 2 天且资金重新流入再说"))
        blocks.append(_hr())

    # 模块②.5 个股断板回流（方案1 2026-09-17 用户拍板·个股维度通道，与题材回流并列不互斥）
    blocks += _ambush_reflow_block(ROOT.parent / "cache")

    # 模块③ 今日板块资金流（实时·左红右绿双列）
    sr = (live or {}).get("sector_risk") or {}
    if sr.get("inflow_top") or sr.get("outflow_top"):
        blocks.append(_div("<text_tag color='neutral'>💰 今日板块资金流（实时·左红右绿）</text_tag>"))
        blocks.append(_flow_columns(sr.get("inflow_top"), sr.get("outflow_top")))
        blocks.append(_hr())

    # ── AI 研判层（2026-08-28 用户「除了数据，需要 AI 介入和思考」）──
    ai = data or {}
    ai_flow = (ai.get("ai_flow") or "").strip()
    stock_scan = ai.get("stock_scan") or []
    picks = ai.get("picks") or []
    ai_verdict = (ai.get("ai_verdict") or "").strip()
    if ai_flow or stock_scan or picks or ai_verdict:
        # 模块④ 最近的板块资金流向（AI 研判）
        if ai_flow:
            blocks.append(_div("<text_tag color='purple'>🧠 AI研判·最近板块资金流向</text_tag>"))
            for line in ai_flow.split("\n"):
                line = line.strip()
                if line:
                    blocks.append(_div(line))
            blocks.append(_hr())
        # 模块⑤ 个股资金·地位·人气（AI 研判）
        if stock_scan:
            blocks.append(_div("<text_tag color='purple'>🔥 个股资金·地位·人气（AI 研判）</text_tag>"))
            for s in stock_scan:
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
        # 模块⑥ 🎯 次日重点标的（AI 推票·置底）
        if picks:
            blocks.append(_div("<text_tag color='purple'>🎯 次日重点标的（AI 推票·实时数据核验）</text_tag>"))
            for p in picks:
                theme = str(p.get("theme") or "")
                stocks = str(p.get("stocks") or "")
                reason = str(p.get("reason") or "")
                risk = str(p.get("risk") or "")
                line = f"<text_tag color='red'>▶ {theme}</text_tag>"
                if stocks:
                    line += f"\n{stocks}"
                if reason:
                    line += f"\n理由：{reason}"
                if risk:
                    line += f"\n<text_tag color='green'>风险：{risk}</text_tag>"
                blocks.append(_div(line))
            blocks.append(_hr())
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


def validate_ambush_card(card: dict) -> tuple[bool, list[str]]:
    issues: list[str] = []
    txt = json.dumps(card, ensure_ascii=False)
    if "定调" not in txt:
        issues.append("饱满度缺失定调模块")
    if "次日回流候选" not in txt and "无明确回流候选" not in txt and "次日有望回流的题材" not in txt and "无有望回流的题材" not in txt:
        issues.append("饱满度缺失回流候选模块")
    if "font color=" in txt:
        issues.append("配色：卡片含 <font>（飞书不渲染，须改 <text_tag>）")
    # 🎯 结论置底铁律（2026-08-27 用户纠错）：综合研判/推票必须在资金流之后（卡片末尾）
    if "🎯 综合研判" in txt or "🎯 次日重点标的" in txt:
        if "💰 今日板块资金流" in txt:
            pos_flow = txt.find("💰 今日板块资金流")
            pos_pick = txt.find("🎯 次日重点标的")
            pos_verdict = txt.find("🎯 综合研判")
            tail_ok = all(p >= 0 and p > pos_flow for p in (pos_pick, pos_verdict) if p >= 0)
            if not tail_ok:
                issues.append("结论置底违规：🎯推票/综合研判 必须在 💰资金流 之后")
    # 文案语义化铁律（2026-09-04 补：AI 注入层首推漏网「机械引擎」→ 推送前强制拦截）
    hits = _ambush_banned_terms(txt)
    if hits:
        issues.append(f"文案语义化违规：含内部术语 {hits}，须大白话改写后再推（09-01 铁律）")
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
        ok, issues = validate_ambush_card(card)
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
