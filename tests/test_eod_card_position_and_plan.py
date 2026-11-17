#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘后候选卡「动态仓位 + 买入预案」回归锁（2026-10-14 用户需求）。

背景（用户原话）：「我说过动态仓位了，你这边并没有引入动态仓位，而且文案显示上并没有
体现动态仓位和买入这块内容。」——10-13 落地的动态仓位（core/relay_position）当时只接到了
**09:25 竞价卡**，盘后候选卡既不显示仓位、也不显示 AI 预案。

本文件锁死三件事：
· 盘后卡渲染「🎯 明日接力仓位建议（动态）」，且**回撤压过仓时两个数都在**
  （档位基数 + 压后实际）——只给一个数看不出"动态"。
· 盘后卡渲染「🤖 AI 明日买入预案」（由 payload 注入 ai_view），只出预案档，
  ⛔ 不出现「买哪只」的结论行（时序大前提：盘后缩范围、不拍板）。
· 全块过大白话门禁（与推送前 card_lint 同源词表），且单票上限与 decision_outlook 同值。

⛔ 2026-11-06 用户拍板（本文件随之更新，别按老契约回改）：
· 「独立明日预案**不展示**」→ 盘后卡仍**不渲染**「🤖 AI 明日买入预案」/ 动态仓位 / 全局旁白，
  预案只落盘供次日 09:25 消费。本文件保留这些「不出现」断言。
· 「📋 在册连板」→ **2026-11-17 用户「删除这个模块」**：该段与 `_board_track_block`
  一并从 `build_eod_card` 移除。本文件改为断言「任何情况都不出现」，⛔ 别再按老契约接回来。
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "wealth_code"))
sys.path.insert(0, str(BASE / "wealth_code" / "pipelines"))
sys.path.insert(0, str(BASE / "scripts"))

import card_copy_rules as ccr  # noqa: E402
import build_cards as bc  # noqa: E402
from core import relay_position as rp  # noqa: E402

_RELAY = {
    "code": "603096", "name": "新经典", "boards": 4, "score": 83.4,
    "core_theme": "短剧/互动影游", "theme_name": "短剧/互动影游",
    "expectation": {"expected_range": ["0.0", "0.5"]},
}
_WATCH = {
    "code": "000823", "name": "超声电子", "boards": 2, "score": 74.7,
    "core_theme": "元件", "theme_name": "元件",
    "expectation": {"expected_range": ["0.0", "0.5"]},
}
_AI_PLAN = {
    "schema": "ai_view/1", "decision_date": "2026-10-14", "stance": "defend",
    "stance_label": "可小仓·回撤过线压着做",
    "direction": "AI 短剧 / 传媒出版（今天唯一逆势的方向）",
    "note": "今天 36 只涨停、做多气氛只有 1 分；明天最多试一只，仓位压着做。",
    "entry_max_gap": 5.0,
    "views": {
        "603096": {"name": "新经典", "bias": "focus",
                   "reason": "第一行依据：4 板、全场最高，短剧这条线今天 5 家涨停。\n"
                             "⚠️风险：封单很薄、换手 22%，位置也高，追高不划算。"},
        "000823": {"name": "超声电子", "bias": "normal",
                   "reason": "第二行依据：开盘就封、换手只有 5%。\n"
                             "⚠️风险：同方向今天整体在跌。"},
    },
}


def _texts(card):
    out = []

    def walk(o):
        if isinstance(o, dict):
            t = o.get("text")
            if isinstance(t, dict) and isinstance(t.get("content"), str):
                out.append(t["content"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(card)
    return out


def _build(monkeypatch, plan: dict | None, ai_plan: dict | None = None):
    """构造盘后卡：动态仓位方案用注入值（不依赖真实账本回撤），AI 预案用显式注入。"""
    if plan is not None:
        monkeypatch.setattr(rp, "build_plan_from_files", lambda *a, **k: plan)
    monkeypatch.setattr(bc, "_cycle_phase_block", lambda **kw: [])
    # ⛔ 2026-11-04 隔离修复：`build_eod_card` 会调 `_load_zt_snapshot()` 读**当天真实**
    #    涨停池来渲染连板天梯。不 patch 的话，测试结果随当日缓存漂移
    #    （实证：11-04 归档变成 119 只/最高 3 板后，本用例凭空失败，
    #     而卡面逻辑一个字没改 —— 属于环境依赖，不是回归）。
    #    ⛔ 而且原断言里的 `"4板"` 其实是**靠天梯里恰好出现「4板」**才通过的
    #    （relay 行渲染的是 `4进5`），属巧合依赖 —— 天梯一旦不出现就假失败。
    #    ✅ 这里给一份**自洽的模拟快照**：天梯 4 板就是被测的那只票，断言不再靠巧合。
    monkeypatch.setattr(bc, "_load_zt_snapshot", lambda *a, **k: {
        "date": "2026-10-14", "zt_count": 36, "max_boards": 4,
        "ladder": {"4": ["新经典"], "2": ["超声电子"]},
    })
    return bc.build_eod_card({
        "date": "2026-10-14", "regime": "neutral",
        "relay": [dict(_RELAY)], "watch": [dict(_WATCH)],
        "diagnostics": {}, "ai_plan": (ai_plan if ai_plan is not None else {}),
    })


# ── ① 动态仓位块：回撤压过仓 → 档位基数与实际两个数都在 ──────────────────
# ⛔ 旧口径回归锁（11-05 用户「回撤不需要压仓」）：显式开 ENABLE_DRAWDOWN_SCALING=1，
#    验证旧文案与压仓数字逐字可复现；新默认由 test_drawdown_narrative_freeze.py 锁。
def test_eod_card_shows_dynamic_position_two_numbers(monkeypatch):
    monkeypatch.setattr(rp, "ENABLE_DRAWDOWN_SCALING", True)
    plan = rp.build_plan(strength=0.40, tier="weak", dd=19.64,
                         theme_info={"day_count": 31, "theme_count": 164})
    card = _build(monkeypatch, plan)
    hits = [t for t in _texts(card) if "明日接力仓位建议" in t]
    assert not hits, "盘后隐藏明日仓位"
    body = bc._relay_position_block(plan, day_word="明天")[0]["text"]["content"]
    # 弱档基数：1 只 / 每只 ≤30% / 总仓 ≤30%；回撤 -19.64% → ×0.2751 → 实际 8.2%
    assert "行情好的话能给到：最多 1 只 · 每只不超过 30% · 总仓位不超过 30%" in body
    assert "实际压到：明天最多同时持有 1 只" in body
    assert "每只不超过 8.2%" in body
    # ⚠️ 2026-10-21：原「·动态控制不禁买」是系统注释词 → 改「按比例收着做」（语义等价）
    assert "按比例收着做" in body
    fails, _ = ccr.scan(body)
    assert fails == [], f"白话门禁命中机器黑话: {fails}"


def test_eod_card_position_block_uncompressed_when_no_drawdown(monkeypatch):
    """回撤未起降（<5%）→ 不堆两行，直接给一档（避免冗余）。"""
    plan = rp.build_plan(strength=0.80, tier="strong", dd=3.0,
                         theme_info={"day_count": 31, "theme_count": 164})
    card = _build(monkeypatch, plan)
    hits = [t for t in _texts(card) if "明日接力仓位建议" in t]
    assert not hits
    body = bc._relay_position_block(plan, day_word="明天")[0]["text"]["content"]
    assert "明天最多同时持有 3 只 · 每只不超过 50% · 总仓位不超过 100%" in body
    assert "实际压到" not in body
    assert "行情好的话能给到" not in body


def test_eod_card_position_block_uses_relay_single_source(monkeypatch):
    """⛔ 渲染层不得自算仓位：卡内数字必须来自 relay_position 方案。"""
    plan = rp.build_plan(strength=0.40, tier="weak", dd=19.64)
    seen = {}

    def _fake(*a, **k):
        seen["called"] = True
        return plan

    monkeypatch.setattr(rp, "build_plan_from_files", _fake)
    bc.build_eod_card({"date": "2026-10-14", "regime": "neutral",
                       "relay": [dict(_RELAY)], "watch": [], "diagnostics": {},
                       "ai_plan": {}})
    assert not seen.get("called"), "隐藏展示后不应额外读取账户计算明日仓位"


# ── ② AI 买入预案块：只出预案档，⛔ 不拍板「买哪只」 ────────────────────
def test_eod_card_shows_ai_plan_with_bias_only(monkeypatch):
    card = _build(monkeypatch, None, ai_plan=_AI_PLAN)
    hits = [t for t in _texts(card) if "AI 明日买入预案" in t]
    assert not hits, "盘后删除独立预案块，重点标记回到池内"
    joined = "\n".join(_texts(card))
    assert "定调：" not in joined
    assert "AI 短剧 / 传媒出版" not in joined
    assert "明天重点看·待竞价核验" in joined
    assert "新经典(603096)" in joined and "4板" in joined
    # 风险段由 `_narr_render` 转成 turquoise 标签并收尾（段名带冒号被剥掉）
    assert "turquoise'>⚠️ 风险</text_tag>：封单很薄、换手 22%" in joined
    assert "超声电子" in joined and "仅作板块参照·不可买" in joined
    assert "一般预案 1 只" not in joined
    # ⛔ 盘后不拍板：不得出现「买入」结论行
    assert ">买入</text_tag>" not in joined
    assert "今日买入" not in joined
    fails, _ = ccr.scan(joined)
    assert fails == [], f"白话门禁命中机器黑话: {fails}"


def test_eod_card_plan_block_separates_avoid_from_normal(monkeypatch):
    """「回避」与「一般」必须分开写（2026-10-30 修）。

    ⛔ 回归背景：旧实现把所有非 focus 的票合成一句「其余 N 只只做观察」，
    明确判「回避」的票会被说成观察票 —— 卡面口径自相矛盾。
    """
    plan = dict(_AI_PLAN)
    plan["views"] = dict(_AI_PLAN["views"])
    plan["views"]["603200"] = {"name": "洗霸股份", "bias": "avoid",
                               "reason": "封单只剩 4 万，等于没封。\n⚠️风险：同方向只有它一家。"}
    card = _build(monkeypatch, None, ai_plan=plan)
    joined = "\n".join(_texts(card))
    assert "洗霸股份" not in joined and "603200" not in joined
    assert "一般预案" not in joined
    fails, _ = ccr.scan(joined)
    assert fails == [], f"白话门禁命中机器黑话: {fails}"


def test_eod_plan_checks_core_without_promoting_observation():
    import copy
    raw = copy.deepcopy(_AI_PLAN)
    rows = {"603096": {**_RELAY, "is_sector_dragon": True, "dragon_rank": 2},
            "000823": {**_WATCH, "is_sector_dragon": True, "dragon_rank": 1}}
    before = copy.deepcopy((raw, rows))
    txt = "\n".join(_texts({"elements": bc._ai_plan_eod_block(raw, rows, relay_codes=set())}))
    assert "未确认题材核心" in txt and "已标注题材龙一" in txt
    assert "保留原资格·不自动转可买" in txt
    assert "仅预案，待竞价核验" in txt
    assert "不超 5% 才买" not in txt
    assert "同方向今天整体在跌" in txt
    assert (raw, rows) == before


def test_eod_card_plan_block_absent_when_no_view(monkeypatch):
    """视图缺失（未跑 record / 非交易日）→ 整块省略，不编造。"""
    card = _build(monkeypatch, None, ai_plan={})
    assert not [t for t in _texts(card) if "AI 明日买入预案" in t]


def test_attach_eod_ai_plan_injects_next_day(monkeypatch, tmp_path):
    """注入助手：读次日视图落 payload；已存在键则不动（幂等·可显式置空）。"""
    payload = {"date": "2026-10-14"}
    monkeypatch.setattr(bc, "_load_ai_view_raw", lambda d: {"views": {}, "_d": d})
    bc.attach_eod_ai_plan(payload)
    assert payload["ai_plan"]["_d"] == "2026-10-15"
    payload2 = {"date": "2026-10-14", "ai_plan": {}}
    bc.attach_eod_ai_plan(payload2)
    assert payload2["ai_plan"] == {}


def test_eod_render_is_read_only_without_metrics_or_lifecycle(monkeypatch, tmp_path):
    import copy

    def forbidden(*args, **kwargs):
        raise AssertionError("纯渲染不得调用统计、周期、生命周期或补算")

    for name in ("_load_metrics", "_nav_drawdown_block", "_cycle_phase_block",
                 "_hist_gene_line", "_post_pool_blocks",
                 "_ai_plan_eod_block"):
        monkeypatch.setattr(bc, name, forbidden)
    monkeypatch.setattr(bc._pquiz, "annotate", forbidden)
    monkeypatch.setattr(bc._cycle_phase, "track_state", forbidden)
    # 📋 在册连板段已于 2026-11-17 删除（见文件头），此处只断言它不再出现。
    monkeypatch.setattr(bc, "_load_zt_snapshot", lambda: {"ladder": {"2": ["测试甲"]}})
    payload = {"date": "2026-11-03", "relay": [dict(_RELAY)],
               "watch": [dict(_WATCH)], "ai_plan": {}}
    before = copy.deepcopy(payload)
    joined = "\n".join(_texts(bc.build_eod_card(payload)))
    assert payload == before
    import card_lint
    ok, errors, warnings = card_lint.lint_card(bc.build_eod_card(payload))
    assert ok and not errors
    # ⛔ 台账缺失 → 不出现段名（不占位、不写「暂无数据」）
    assert "在册连板" not in joined
    for term in ("历史基因", "已记录", "胜率", "净值", "回撤", "战绩",
                 "明日买入预案", "低位方向"):
        assert term not in joined


def test_eod_focus_is_explicit_and_observation_never_buy(monkeypatch):
    import copy
    plan = copy.deepcopy(_AI_PLAN)
    plan["views"]["603096"] = {"view": "participate"}
    plan["views"]["000823"]["bias"] = "focus"
    joined = "\n".join(_texts(_build(monkeypatch, None, plan)))
    assert "明天重点看·待竞价核验" not in joined
    assert joined.count("明天重点参照·不可买") == 1
    assert "AI 明日买入预案" not in joined


def test_eod_direction_is_optional_theme_comment_not_third_pool(monkeypatch):
    payload = {"date": "2026-11-03", "relay": [], "watch": [], "ai_plan": {},
               "low_pool": [{"code": "999999", "name": "池外甲"}]}
    assert "低位方向" not in "\n".join(_texts(bc.build_eod_card(payload)))
    payload["direction"] = "题材甲仍待验证。池外甲(999999)重点买。"
    joined = "\n".join(_texts(bc.build_eod_card(payload)))
    assert "题材甲仍待验证" not in joined and "非低位确认" not in joined
    assert "低位方向" not in joined
    assert "999999" not in joined and "池外甲" not in joined
    assert "低位池" not in joined


def test_plain_ai_plan_direction_is_not_low_direction(monkeypatch):
    joined = "\n".join(_texts(_build(monkeypatch, None, _AI_PLAN)))
    assert "低位方向" not in joined and "非低位确认" not in joined
    assert _AI_PLAN["direction"] not in joined
    assert "封单很薄、换手 22%" in joined
    assert "同方向今天整体在跌" in joined


def test_one_word_observation_omits_invalid_trading_expectations(monkeypatch):
    import copy
    payload = {"date": "2026-11-03", "relay": [], "watch": [{
        **_WATCH, "is_one_word": True, "participation_eligibility": "observe_only",
        "price": 4.81, "watch_group": "一字核心",
        "expectation": {"expected_range": [None, None], "confidence": 0,
                        "auction_amount_range": [0, 0]},
        "narrative": "风险：开板后有回落风险。"}], "ai_plan": {}}
    before = copy.deepcopy(payload)
    joined = "\n".join(_texts(bc.build_eod_card(payload)))
    assert payload == before
    assert "仅作板块参照·不可买" in joined
    assert "现价4.81" in joined and "开板后有回落风险" in joined
    assert "预期" not in joined and "竞价额" not in joined and "可信" not in joined
    assert "仅作重点参照" not in joined
    payload["watch"][0]["expectation"] = {}
    joined = "\n".join(_texts(bc.build_eod_card(payload)))
    assert "预期" not in joined and "竞价额" not in joined and "可信" not in joined


# ── ③ 与 decision_outlook 同值：同一张卡不得出现两个单票上限 ─────────────
def _do():
    spec = importlib.util.spec_from_file_location(
        "decision_outlook", BASE / "wealth_code" / "core" / "decision_outlook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_deleverage_cap_follows_relay_plan():
    do = _do()
    plan_cap = 0.0825
    d = do.build_decision(regime="ice", sentiment=1.0, nav_drawdown_pct=19.64,
                          relay_per_pick_cap=plan_cap)
    assert d["verdict"] == "deleverage"
    _cap = float(re.search(r"≤([\d.]+)%", d["rules"]["position"]).group(1))
    # 与卡面动态仓位块同值（同一浮点 → 同一字符串）
    assert d["rules"]["position"].startswith("≤" + f"{plan_cap * 100:.1f}%")
    assert abs(_cap - plan_cap * 100) < 0.06      # 显示取 1 位小数（8.25 → 8.2）


def test_deleverage_falls_back_without_plan():
    """旧调用方（不注入方案）→ 回落 POSITION_MAX × 回撤乘数，行为不变。"""
    do = _do()
    d = do.build_decision(regime="ice", sentiment=1.0, nav_drawdown_pct=11.2)
    assert d["verdict"] == "deleverage"
    assert d["rules"]["position"].startswith("≤")
    cap = float(re.search(r"≤([\d.]+)%", d["rules"]["position"]).group(1))
    assert 0 < cap <= 50
