#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书卡片通用样式组件（2026-08-17 架构重构 P1 · 复用优先：从 build_cards 抽出）

职责：所有卡片（eod/verify/panqian/盘中）共用的**纯视觉构件**——div/hr/note/text_tag
背景胶囊/资金流向双列/页脚/时间。零业务依赖（不读数据、不含选股逻辑），可独立测试。

被 build_cards / card_lint 等卡片处理模块引用，改样式只动本文件。
配色铁律：text_tag 枚举 neutral/blue/turquoise/lime/orange/violet/indigo/wathet/
green/yellow/red/purple/carmine（禁 hex、禁 grey 之外的自由色）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

BJ_TZ = timezone(timedelta(hours=8))

# 飞书 text_tag 合法枚举（禁 hex/grey，卡片统一）
TEXT_TAG_COLORS = {
    "neutral", "blue", "turquoise", "lime", "orange", "violet", "indigo",
    "wathet", "green", "yellow", "red", "purple", "carmine",
}


def _md(t: str) -> dict:
    return {"tag": "lark_md", "content": t}


def _div(t: str) -> dict:
    return {"tag": "div", "text": _md(t)}


def _hr() -> dict:
    return {"tag": "hr"}


def _note(t: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": t}]}


def _now_bj() -> datetime:
    return datetime.now(BJ_TZ)


FOOTER1 = "📡 选股宝+脉冲快照+情绪矩阵 | {hhmm} | 财富密码"
FOOTER2 = "⚠️ 本报告基于公开信息分析，投资需谨慎"


def _footer(hhmm: str) -> list[dict]:
    return [_note(FOOTER1.format(hhmm=hhmm)), _note(FOOTER2)]


def _load_emotion_signal() -> dict:
    """读 feedback_matrix.json 的当日情绪方向信号（标题定调用·2026-08-25 用户
    「情绪止跌·不算退潮」修正）：sentiment_score / avg_pct / nuke_count / lb_promote_rate /
    promote_downtrend / defense_level。无数据/非当日 → 返回 {}（不编造，维持旧行为）。"""
    try:
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent.parent / "cache" / "feedback_matrix.json"
        fb = json.loads(p.read_text(encoding="utf-8"))
        d = fb.get("date") or (fb.get("generated_ts") or "")[:10]
        if not d or d != _now_bj().strftime("%Y-%m-%d"):
            return {}
        trend = fb.get("trend") or {}
        return {
            "sentiment": fb.get("sentiment_score"),
            "avg_pct": fb.get("avg_pct"),
            "nuke": fb.get("nuke_count"),
            "lb_promote": fb.get("lb_promote_rate"),
            "downtrend": bool(trend.get("promote_downtrend")),
            "defense": int(trend.get("defense_level") or 0),
        }
    except Exception:
        return {}


def _regime_label(regime: str | None) -> str:
    """标题定调（2026-08-25 修正·用户「情绪止跌不算退潮」+ 中枢审查沉淀·推断多维核验铁律）：
    regime 只反映门控档位（risk_off 因 break_count≥1 触发），**不反映情绪方向**。
    推断引用维度清单（铁律①三维覆盖）：
      - 当日数据：sentiment / avg_pct / nuke（单快照）
      - 历史趋势：trend.promote_downtrend / trend.defense_level / 5日 avg_pct 轨迹
      - 跨链路约束：净值回撤线 / 趋势防守档（铁律③语义矛盾门禁）
    止跌判据（铁律②单快照禁止·趋势确认优先）：
      risk_off + 当日(情绪分≥3.5 ∧ 溢价>0 ∧ 核按钮≤10) → 再查趋势：
        - downtrend=False 且 defense==0 → 「情绪止跌·修复前夜」（真止跌·趋势确认）
        - 否则（downtrend 或 defense≥1 或 溢价仅单日转正）→ 「情绪止跌·仍防守」
          （趋势防守档/回撤线约束优先，禁裸"止跌"与防守矛盾并存）
    无数据/非当日 → 维持旧映射（向后兼容）。"""
    # 定调白话（2026-09-10 用户「话术上删除专业术语」）：情绪升温/退潮/中性/冰点 → 市场转暖/转冷/平稳/很冷
    base = {"risk_on": "市场转暖·可以积极", "risk_off": "市场转冷·防守为主",
            "neutral": "市场平稳·挑着做",
            "ice": "市场很冷·多看少动"}.get(regime or "", "市场平稳·挑着做")  # ice 补键 2026-09-09
    if regime != "risk_off":
        return base
    sig = _load_emotion_signal()
    if not sig or sig.get("sentiment") is None:
        return base
    st = float(sig["sentiment"])
    ap = sig.get("avg_pct")
    nuke = sig.get("nuke")
    # 当日快照满足（情绪分≥3.5 ∧ 溢价为正 ∧ 核按钮≤10）
    if st >= 3.5 and ap is not None and float(ap) > 0 and (nuke is None or int(nuke) <= 10):
        # 趋势维度核验（铁律②：单快照不足以下"止跌"结论）
        downtrend = bool(sig.get("downtrend"))
        defense = int(sig.get("defense") or 0)
        if not downtrend and defense == 0:
            return "跌不动了·可能要转好"   # 当日+趋势双确认（2026-09-10 白话化）
        return "跌不动了·但还得防"         # 趋势防守档约束优先（铁律③语义矛盾门禁·2026-09-10 白话化）
    return base                          # 当日快照不满足 → 维持退潮/防守定调


def _fbt_le(a: str, b: str) -> bool:
    """首封时间 a 是否早于等于 b（HHMMSS / HH:MM:SS 均可）。"""
    try:
        return a.replace(":", "") <= b.replace(":", "")
    except Exception:
        return False


def _hl(txt: str, color: str = "purple") -> str:
    """
    个股名称背景标签（飞书 lark_md `<text_tag color='x'>` 带背景色标签，官方支持的"背景小卡"语法，
    2026-08-12 修正：`<highlight>` 标签飞书不支持，渲染会回退成灰色，勿用）。
    color 必须是单词枚举（勿用 RGB）：neutral/blue/turquoise/lime/orange/violet/indigo/
    wathet/green/yellow/red/purple/carmine；无 grey → 天梯/未达标简列用 neutral。
    """
    return f"<text_tag color='{color}'>{txt}</text_tag>"


def _stock_hl(it: dict, color: str = "purple") -> str:
    """个股名称+代码(+龙头分) → 背景标签：`<text_tag color='purple'>京投发展(600683) 92.9</text_tag>`
    （接力紫/观察黄，用户2026-08-13定：龙头分直接放进小卡，只显示数值不显示文案）。"""
    score = it.get('score')
    score_txt = f" {score:.1f}" if isinstance(score, (int, float)) else ""
    return _hl(f"{it.get('name', '')}({it.get('code', '')}){score_txt}", color)


def _amount_txt(it: dict) -> str:
    """
    竞价量能预测（由昨日成交额直接算出的具体竞价金额，亿）：竞价额0.68~1.35亿。
    用户指定口径：不显示「昨量百分比」、不显示亿级量能下限，直接给出
    昨日成交额 cje_yi × 量能比例 计算出的预期竞价金额区间（2026-08-13 去「昨X亿」基数）。
    """
    exp = it.get("expectation") or {}
    amt_rng = exp.get("auction_amount_range")  # [lo_pct, hi_pct] 百分比整数
    cje = float(it.get("cje_yi") or 0.0)
    if not (isinstance(amt_rng, (list, tuple)) and len(amt_rng) == 2) or cje <= 0:
        return ""
    lo_yi = round(cje * amt_rng[0] / 100.0, 2)
    hi_yi = round(cje * amt_rng[1] / 100.0, 2)
    return f"竞价额{lo_yi}~{hi_yi}亿"


def _flow_columns(in_top, out_top, *, col_l_title: str = "净流入", col_r_title: str = "净流出") -> dict:
    """💰 板块资金流向左右两列（2026-08-14 用户定：刚好两列）。
    左列=净流入 Top5（红胶囊），右列=净流出 Top5（绿胶囊）；每列内每板块一行，is_focus 加 ⭐。

    ⚠️ 2026-08-14 13:33 实测：飞书 column_set 在本环境不渲染（整块空白，用户反馈），
    已改回 fields is_short 交替双列（外盘 2×2 同款、100% 渲染）：field 两两并排 →
    列头|列头 / 流入1|流出1 / 流入2|流出2 …，视觉即「左红右绿」两列。
    ⛔ 2026-08-18 修正：表头默认值去掉 🔴/🟢 圆点（违反【统一配色规范】禁圆点铁律），
    红绿胶囊本身即左右列语义；复盘卡已同规则（不渲染表头）。"""
    def _field(txt):
        return {"is_short": True, "text": {"tag": "lark_md", "content": txt}}

    def _col_items(items: Iterable[dict], color: str) -> list[str]:
        els = []
        for s in (list(items) or [])[:5]:
            nm = (s.get("name") or "").strip()
            f = s.get("flow_yi")
            if not isinstance(f, (int, float)):
                continue
            star = " ⭐" if s.get("is_focus") else ""
            sign = "+" if f >= 0 else "-"
            els.append(f"<text_tag color='{color}'>{nm}{star} {sign}{abs(f):.1f}亿</text_tag>")
        return els

    in_items = [f"**{col_l_title}**"] + _col_items(in_top, "red")
    out_items = [f"**{col_r_title}**"] + _col_items(out_top, "green")
    fields = []
    n = max(len(in_items), len(out_items))
    for i in range(n):
        if i < len(in_items):
            fields.append(_field(in_items[i]))
        if i < len(out_items):
            fields.append(_field(out_items[i]))
    return {"tag": "div", "fields": fields}


def validate_color(color: str) -> bool:
    """校验颜色是否为合法 text_tag 枚举（card_lint 用）。"""
    return color in TEXT_TAG_COLORS
