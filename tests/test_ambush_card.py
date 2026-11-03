# -*- coding: utf-8 -*-
"""尾盘潜伏卡的涨停启动排除回归锁。"""
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WC = ROOT / "wealth_code"
spec = importlib.util.spec_from_file_location("build_cards_ambush", WC / "pipelines" / "build_cards.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)


def test_pool_today_status_uses_full_limit_up_list():
    """题材摘要漏股时，仍须由全量涨停池挡住已启动的潜伏题材。"""
    pool = [{"code": "000011.SZ", "name": "深物业A"}]
    live = {
        "leaders": [],
        "limit_up_stocks": [
            {"code": "000011.SZ", "name": "深物业A", "pct": 9.97},
        ],
    }

    assert bc._pool_today_status(pool, live) == {"000011": 9.97}


def test_pullback_block_reads_fixed_snapshot_only(tmp_path):
    """阴线回踩＝本卡唯一选材（2026-10-09 用户「都改为那套逻辑」）：只读 14:30 冻结快照。"""
    snapshot = {
        "strategy": "ambush_pullback_ma10_v1",
        "trade_date": "2026-10-08",
        "selection_time": "2026-10-08 14:30",
        "candidates": [{
            "code": "000001", "name": "测试股份", "sector": "测试题材",
            "hot_date": "2026-09-25", "sector_zt_count": 3, "max_boards": 2,
            "close": 9.55, "ma10": 9.52, "ma10_gap_pct": 0.32,
            "volume_ratio": 0.70, "score": 42.5, "pct": -2.04,
        }],
    }
    (tmp_path / "ambush_pullback_2026-10-08.json").write_text(
        __import__("json").dumps(snapshot, ensure_ascii=False), encoding="utf-8"
    )

    blocks = bc._ambush_pullback_block(tmp_path, "2026-10-08")
    text = "\n".join(x["text"]["content"] for x in blocks if x.get("tag") == "div")

    # 块标题只报「这是什么块 + 什么时候冻的」：本块＝机械扫描证据池（⛔ 不带「买入」字样，
    # 2026-10-15 用户「尾盘买入标的和定调好像冲突了，只留一个」）；形态口径＝紧跟其后的定调行。
    assert "📋 今日候选" in text and "今日尾盘买入候选" not in text
    # 标题行本身不复读口径（口径由紧随其后的 📌 定调 说一次，见下）
    _head = next(ln for ln in text.split("\n") if "📋 今日候选" in ln)
    assert "回踩" not in _head and "反包" not in _head
    assert "测试股份(000001)" in text
    assert "距十日线+0.3%" in text
    assert "量比0.70" in text
    # 时点口径已移到「✅ 今日买入」块（全卡唯一买入出口），本块不得再挂买入指令
    assert bc.AMBUSH_BUY_NOTE not in text
    assert "📌 定调" in text                                     # 定调并入本块
    # 当日涨跌幅必须上卡（2026-10-09 用户「不然我不知道能不能买入」）：阴线按 A 股口径走绿
    assert "<text_tag color='green'>-2.04%</text_tag>" in text
    assert bc._ambush_pullback_codes(tmp_path, "2026-10-08") == {"000001"}


def test_pullback_block_fails_loud_when_snapshot_missing(tmp_path):
    """主选材块不允许静默：快照缺失＝链路异常，必须显式告知，不能空卡。"""
    blocks = bc._ambush_pullback_block(tmp_path, "2026-10-08")
    text = "\n".join(x["text"]["content"] for x in blocks if x.get("tag") == "div")
    assert "未产出候选快照" in text


def test_pullback_block_reports_empty_candidates(tmp_path):
    """快照存在但 0 候选 → 明确写「今日无符合条件」；与「快照缺失」区分开。"""
    (tmp_path / "ambush_pullback_2026-10-08.json").write_text(
        '{"trade_date":"2026-10-08","candidates":[],"data_ok":true}', encoding="utf-8"
    )
    blocks = bc._ambush_pullback_block(tmp_path, "2026-10-08")
    text = "\n".join(x["text"]["content"] for x in blocks if x.get("tag") == "div")
    assert "今日无符合条件的候选" in text
    assert "数据源异常" not in text


def test_pullback_block_flags_data_source_failure(tmp_path):
    """数据源异常时「0 候选」不得当成「今日无符合条件」（2026-10-09 腾讯停用 host 血案）。"""
    (tmp_path / "ambush_pullback_2026-10-08.json").write_text(
        '{"trade_date":"2026-10-08","candidates":[],"data_ok":false,'
        '"fetch_failed":141,"attempted":141}', encoding="utf-8"
    )
    blocks = bc._ambush_pullback_block(tmp_path, "2026-10-08")
    text = "\n".join(x["text"]["content"] for x in blocks if x.get("tag") == "div")
    assert "日K数据源异常" in text
    assert "141/141" in text


def test_ambush_validation_rejects_picks_outside_pullback_pool():
    """推票只能出自阴线回踩池（池外即拒绝推送）。"""
    card = _min_valid_ambush_card()
    picks = [{"theme": "测试", "stocks": [{"name": "池外票", "code": "300999", "pct": 3.0}]}]
    ok, issues = bc.validate_ambush_card(card, picks=picks, pool_codes={"000001"})
    assert not ok
    assert any("推票越界" in i for i in issues)
    ok2, _ = bc.validate_ambush_card(card, picks=[{"theme": "测试", "stocks": [
        {"name": "池内票", "code": "000001", "pct": -2.0}]}], pool_codes={"000001"})
    assert ok2


def test_live_confirm_does_not_treat_hotspot_as_fund_inflow():
    hot_only = {
        "sector_risk": {"inflow_top": [], "outflow_top": []},
        "sector_delta": {"ongoing": []},
        "hot_spots": [{"sector": "测试题材"}],
    }
    text, tone = bc._live_confirm("测试题材", hot_only)
    assert tone == "neutral"
    assert "仅代表热度" in text
    assert "资金净流入" not in text


def test_live_confirm_marks_real_fund_inflow_separately():
    live = {
        "sector_risk": {"inflow_top": [{"name": "测试题材"}], "outflow_top": []},
        "sector_delta": {"ongoing": []},
        "hot_spots": [{"sector": "测试题材"}],
    }
    text, tone = bc._live_confirm("测试题材", live)
    assert tone == "red"
    assert "资金净流入榜" in text


def test_picks_stocks_line_carries_pct_with_ashare_colors():
    """今日尾盘买入标的必须带当日涨幅（2026-10-09 用户「不然我不知道能不能买入」）：涨红跌绿。"""
    line = bc._picks_stocks_line([
        {"name": "无线传媒", "code": "301551", "pct": 13.09},
        {"name": "某某股份", "code": "000001", "pct": -3.2},
    ])
    assert "无线传媒(301551)" in line
    assert "+13.09%" in line
    assert "<text_tag color='red'>+13.09%</text_tag>" in line
    assert "<text_tag color='green'>-3.20%</text_tag>" in line
    assert "｜" in line


def test_picks_stocks_line_pct_missing_not_fabricated():
    """缺涨幅就不显示，不补零、不编造。"""
    line = bc._picks_stocks_line([{"name": "某某股份", "code": "000001"}])
    assert "某某股份(000001)" in line
    assert "%" not in line


def test_picks_stocks_line_keeps_legacy_string():
    """旧数据的纯字符串写法必须原样兼容（不破历史卡）。"""
    assert bc._picks_stocks_line("依顿电子(603328)｜金禄电子(301282)") == "依顿电子(603328)｜金禄电子(301282)"
    assert bc._picks_stocks_line(None) == ""


def test_pct_tag_uses_ashare_colors_and_never_fabricates():
    """涨跌幅标签统一出口（2026-10-09 用户「所有个股带上涨跌幅」）：涨红跌绿，非数值不编造。"""
    assert bc._pct_tag(3.5) == " <text_tag color='red'>+3.50%</text_tag>"
    assert bc._pct_tag(-1.2) == " <text_tag color='green'>-1.20%</text_tag>"
    assert bc._pct_tag(0) == " <text_tag color='red'>+0.00%</text_tag>"
    assert bc._pct_tag(9.99, 0) == " <text_tag color='red'>+10%</text_tag>"
    assert bc._pct_tag(9.99, 0, lead_space=False) == "<text_tag color='red'>+10%</text_tag>"
    assert bc._pct_tag(None) == ""
    assert bc._pct_tag("3.5") == ""
    assert bc._pct_tag(True) == ""


def test_pool_line_carries_today_pct():
    """题材回流·相关个股同样必带当日涨跌幅（不是只有推票带）。"""
    live = bc._pool_line({"name": "昭衍新药", "code": "603127.SH", "cap_yi": 369.4}, 1.04)
    assert "昭衍新药(603127.SH)" in live
    assert "<text_tag color='red'>+1.04%</text_tag>" in live
    snap = bc._pool_line({"name": "康辰药业", "code": "603590.SH", "live_pct": -0.67})
    assert "<text_tag color='green'>-0.67%</text_tag>" in snap
    # 无任何实时涨幅 → 不显示，不拿历史涨停 pct 冒充
    bare = bc._pool_line({"name": "无数据", "code": "000002.SZ", "pct": 10.0})
    assert "%" not in bare


def test_mainline_leader_carries_pct():
    """背景·今日新主线的龙头股也必须带当日涨幅。"""
    live = {
        "top5": [{"name": "测试主线", "zt_count": 11, "desc": "测试催化",
                  "leader": {"name": "龙头股", "code": "600000.SS", "pct": 9.99, "boards": "2天2板"}}],
        "hot_spots": [],
        "early": [],
    }
    blocks = bc._ambush_mainline_block(live)
    text = "\n".join(x["text"]["content"] for x in blocks if x.get("tag") == "div")
    assert "龙头股(600000)" in text
    assert "<text_tag color='red'>+9.99%</text_tag>" in text


def test_sector_flow_block_hidden_by_default_and_reversible(monkeypatch):
    """2026-10-09 用户「不显示今日板块资金流」：默认整块不渲染，开关可逆恢复。"""
    live = {"sector_risk": {"inflow_top": [{"name": "测试流入", "flow_yi": 3.2}],
                            "outflow_top": [{"name": "测试流出", "flow_yi": -2.1}]}}
    monkeypatch.setattr(bc, "_load_ambush_data", lambda: ({}, live))
    monkeypatch.setattr(bc, "_ambush_pullback_block", lambda *a, **k: [])
    monkeypatch.setattr(bc, "_ambush_mainline_block", lambda *a, **k: [])
    monkeypatch.setattr(bc, "_ambush_reflow_block", lambda *a, **k: [])
    assert bc.AMBUSH_SHOW_SECTOR_FLOW is False
    assert "今日板块资金流" not in json.dumps(bc.build_ambush_card(None), ensure_ascii=False)
    monkeypatch.setattr(bc, "AMBUSH_SHOW_SECTOR_FLOW", True)
    assert "今日板块资金流" in json.dumps(bc.build_ambush_card(None), ensure_ascii=False)


def test_validation_rejects_picks_missing_pct(monkeypatch):
    """每只推票必带当日涨跌幅（2026-10-09 用户「所有个股带上涨跌幅」）。"""
    monkeypatch.setattr(bc, "_ambush_reflow_block", lambda *a, **k: [])  # 杜绝测试期写生产缓存
    card = _min_valid_ambush_card()
    bad = [{"theme": "测试", "stocks": [{"name": "池内票", "code": "000001"}]}]
    ok, issues = bc.validate_ambush_card(card, picks=bad, pool_codes={"000001"})
    assert not ok
    assert any("涨跌幅缺失" in i for i in issues)
    good = [{"theme": "测试", "stocks": [{"name": "池内票", "code": "000001", "pct": -2.0}]}]
    ok2, _ = bc.validate_ambush_card(card, picks=good, pool_codes={"000001"})
    assert ok2


def _stub_ambush_sources(monkeypatch, live=None):
    monkeypatch.setattr(bc, "_load_ambush_data", lambda: ({}, live or {}))
    monkeypatch.setattr(bc, "_ambush_pullback_block", lambda *a, **k: [])
    monkeypatch.setattr(bc, "_ambush_mainline_block", lambda *a, **k: [])
    monkeypatch.setattr(bc, "_ambush_reflow_block", lambda *a, **k: [])


def _min_valid_ambush_card() -> dict:
    """一张**骨架完整**的最小尾盘卡（只满足 `validate_ambush_card` 的 presence 检查）。

    ⛔ 为什么不再用 `build_ambush_card(None)` 当夹具：它读**当天**的 14:30 冻结快照
      （`cache/ambush_pullback_{今天}.json`），而当日快照要到 14:30 才生成
      → **早上跑全量必然缺「定调」块**，`validate_ambush_card` 报「饱满度缺失定调模块」，
      两个与该检查无关的测试（推票越界 / 涨跌幅缺失）跟着红。实测 2026-10-22 09:0x 复现。
      ⛔ 测试不该依赖「今天恰好跑过尾盘扫描」——单测 `validate_ambush_card` 就该只喂校验需要的东西。
    """
    return {
        "title": "🎯 尾盘潜伏·增量",
        "color": "purple",
        "elements": [
            bc._div("<text_tag color='purple'>📌 定调</text_tag> 尾盘只挑一种形态：前段爆发过"),
            bc._div("🔮 次日有望回流的题材：无明确回流候选"),
            bc._hr(),
        ],
    }


def test_picks_block_at_card_head_and_reversible(monkeypatch):
    """2026-10-14 用户「我说的这一项直接放到整个卡片的头部！」：推票块置顶，开关可逆。"""
    _stub_ambush_sources(monkeypatch)
    ai = {
        "ai_flow": "资金口径说明",
        "stock_scan": [],
        "picks": [{"theme": "测试方向", "stocks": [
            {"name": "池内票", "code": "000001", "pct": -2.0}], "reason": "理由A", "risk": "风险A"}],
        "ai_verdict": "综合结论Y",
    }
    assert bc.AMBUSH_PICKS_AT_TOP is True
    card = bc.build_ambush_card(ai)
    divs = [e["text"]["content"] for e in card["elements"] if e.get("tag") == "div"]
    assert "✅ 今日买入" in divs[0], divs[0]
    blob = json.dumps(card, ensure_ascii=False)
    # 置顶：在综合研判之前；综合研判仍恒置底
    assert -1 < blob.find("✅ 今日买入") < blob.find("🎯 综合研判")
    # 可逆：关闭开关即回落到「AI 研判层·置底」原位置（资金流之前不再有推票块）
    monkeypatch.setattr(bc, "AMBUSH_PICKS_AT_TOP", False)
    card2 = bc.build_ambush_card(ai)
    divs2 = [e["text"]["content"] for e in card2["elements"] if e.get("tag") == "div"]
    assert "✅ 今日买入" not in divs2[0], divs2[0]
    blob2 = json.dumps(card2, ensure_ascii=False)
    assert blob2.find("🧠 AI研判") < blob2.find("✅ 今日买入") < blob2.find("🎯 综合研判")


def test_no_picks_keeps_card_head_unchanged(monkeypatch):
    """无推票时置顶模块不得留空占位（⛔ 不留「✅ 今日买入」空壳）。"""
    _stub_ambush_sources(monkeypatch)
    assert bc.AMBUSH_PICKS_AT_TOP is True
    card = bc.build_ambush_card({"ai_flow": "资金口径说明", "picks": [], "ai_verdict": "综合结论Y"})
    assert "✅ 今日买入" not in json.dumps(card, ensure_ascii=False)
    assert bc.AMBUSH_BUY_NOTE not in json.dumps(card, ensure_ascii=False)   # 无票即无买入指令


def _stub_snapshot(monkeypatch, snap):
    monkeypatch.setattr(bc, "_ambush_pullback_snapshot", lambda *a, **k: snap)


def test_picks_block_carries_market_risk_line_from_frozen_snapshot(monkeypatch):
    """2026-10-14 用户「加上一句风险跟在第二部分」：推票收尾带一句、数据全部来自当日冻结快照。"""
    _stub_ambush_sources(monkeypatch)
    _stub_snapshot(monkeypatch, {
        "trade_date": "2026-10-14",
        "candidates": [],
        "env": {"level": "weak",
                "facts": {"up": 953, "down": 4535, "total_zt": 34, "broken_rate": 43.3}},
        "advice": {"max_picks": 2, "position_pct_each": 50},
    })
    ai = {"picks": [{"theme": "测试方向", "stocks": [
        {"name": "池内票", "code": "000001", "pct": -2.0}], "reason": "理由A", "risk": "风险A"}]}
    assert bc.AMBUSH_PICKS_RISK is True
    card = bc.build_ambush_card(ai)
    divs = [e["text"]["content"] for e in card["elements"] if e.get("tag") == "div"]
    risk_divs = [d for d in divs if "⚠️ 风险：" in d]
    assert len(risk_divs) == 1, risk_divs
    line = risk_divs[0]
    assert "今天市场偏弱" in line
    assert "4535家跌对953家涨" in line and "涨停34家" in line and "炸板率43%" in line
    assert "最多2只" in line and "不超过50%" in line
    assert "<text_tag color='green'>" in line
    # 位置：推票标题之后、买入口径行之前（＝紧跟「✅ 今日买入」本块）
    blob = json.dumps(card, ensure_ascii=False)
    assert -1 < blob.find("✅ 今日买入") < blob.find("⚠️ 风险：") < blob.find(bc.AMBUSH_BUY_NOTE)
    # 可逆：关掉开关即整句不渲染
    monkeypatch.setattr(bc, "AMBUSH_PICKS_RISK", False)
    card2 = bc.build_ambush_card(ai)
    assert "⚠️ 风险：" not in json.dumps(card2, ensure_ascii=False)


def test_picks_risk_line_omitted_when_snapshot_missing(monkeypatch):
    """快照缺失 → 风险句整句省略（缺数据 ≠ 风险为零），⛔ 不编造盘面数字。"""
    _stub_ambush_sources(monkeypatch)
    _stub_snapshot(monkeypatch, None)
    card = bc.build_ambush_card({"picks": [{"theme": "测试方向", "stocks": [
        {"name": "池内票", "code": "000001", "pct": -2.0}]}]})
    assert "⚠️ 风险：" not in json.dumps(card, ensure_ascii=False)


def test_picks_risk_text_no_fabrication_without_facts(monkeypatch):
    """快照里既无盘面事实又无强弱结论 → 返回空串（宁可不说，也不编）。"""
    _stub_snapshot(monkeypatch, {"trade_date": "2026-10-14", "candidates": [],
                                "env": {}, "advice": {}})
    assert bc._ambush_picks_risk_text(ROOT / "cache", "2026-10-14") == ""
    # 只有仓位建议、没有盘面事实 → 仍给一句（不含任何编造的数字）
    _stub_snapshot(monkeypatch, {"trade_date": "2026-10-14", "candidates": [],
                                "env": {"level": "weak"}, "advice": {"max_picks": 2,
                                                                     "position_pct_each": 50}})
    txt = bc._ambush_picks_risk_text(ROOT / "cache", "2026-10-14")
    assert "今天市场偏弱" in txt and "最多2只" in txt


# ── 2026-10-15 用户「杂七杂八的话说得太多了 / 推票理由描述得不清不楚」 ──────────────
def _card_divs(card):
    return [e["text"]["content"] for e in card["elements"] if e.get("tag") == "div"]


def test_picks_reason_rendered_as_named_segments(monkeypatch):
    """推票理由必须分段（段名在段首·风险转绿），⛔ 不得把方向/资金/形态糊成一整行。"""
    _stub_ambush_sources(monkeypatch)
    ai = {"picks": [{"theme": "测试方向",
                     "stocks": [{"name": "池内票", "code": "000001", "pct": -2.0}],
                     "points": [{"k": "方向", "v": "钱在这条线"},
                                {"k": "资金", "v": "今天净流入"},
                                {"k": "形态", "v": "洗盘后反包"}],
                     "risk": "仓位减半"}]}
    line = next(d for d in _card_divs(bc.build_ambush_card(ai)) if "▶ 测试方向" in d)
    for k, v in (("方向", "钱在这条线"), ("资金", "今天净流入"), ("形态", "洗盘后反包")):
        assert f"\n　**{k}**：{v}" in line
    assert "\n<text_tag color='green'>风险：仓位减半</text_tag>" in line
    assert "理由：" not in line            # 旧「理由：整段」写法已废
    assert line.count("**") == 6           # 三个段名各一对，⛔ 风险段不加粗（标签内禁 **）


def test_picks_reason_legacy_single_paragraph_still_renders(monkeypatch):
    """旧 data（单段 reason）走同一出口：一个字都不能丢。"""
    _stub_ambush_sources(monkeypatch)
    ai = {"picks": [{"theme": "测试方向",
                     "stocks": [{"name": "池内票", "code": "000001", "pct": -2.0}],
                     "reason": "理由A"}]}
    line = next(d for d in _card_divs(bc.build_ambush_card(ai)) if "▶ 测试方向" in d)
    assert "\n　理由A" in line


def test_lean_layout_drops_repeated_template_and_member_wall(monkeypatch):
    """精简布局：通用模板行（再次启动信号）与相关个股名单不再逐题材刷屏；开关可逆。"""
    _stub_ambush_sources(monkeypatch)
    tr = {"candidates": [{"theme": "测试题材", "cooled_days": 2, "phase": "watch",
                          "trigger": "明日竞价高开 / 板块资金转流入即确认回流",
                          "boom_day": "2026-10-13",
                          "watch_pool": [{"code": "000002.SZ", "name": "甲票"}]}]}
    monkeypatch.setattr(bc, "_load_ambush_data", lambda: (tr, {}))
    assert bc.AMBUSH_LEAN_LAYOUT is True
    blob = json.dumps(bc.build_ambush_card({"picks": []}), ensure_ascii=False)
    # 第二轮收敛：无资金确认的方向不再各占一行，聚合进一行（仍保留方向名 + 回调天数）
    assert "测试题材（回调2天）" in blob
    assert "其余 1 个方向今日资金未回流" in blob
    assert "再次启动信号" not in blob and "📦 相关个股" not in blob
    # 可逆：关掉开关 → 旧布局逐字回来（模板行 + 相关个股名单）
    monkeypatch.setattr(bc, "AMBUSH_LEAN_LAYOUT", False)
    blob2 = json.dumps(bc.build_ambush_card({"picks": []}), ensure_ascii=False)
    assert "再次启动信号" in blob2 and "📦 相关个股" in blob2


def test_lean_layout_no_zero_info_placeholder_repeated(monkeypatch):
    """「今日实时榜未直接命中（以系统资金趋势为准）」零信息占位句不得逐题材重复刷屏。

    2026-10-15 第二轮收敛：连「今日资金未回流」本身也不许逐题材刷 N 遍——4 个方向都是
    同一个结论，聚合进一行；方向名与回调天数一个不丢。
    """
    _stub_ambush_sources(monkeypatch)
    tr = {"candidates": [{"theme": f"题材{i}", "cooled_days": i + 1, "phase": "watch"}
                         for i in range(4)]}
    monkeypatch.setattr(bc, "_load_ambush_data", lambda: (tr, {}))
    blob = json.dumps(bc.build_ambush_card({"picks": []}), ensure_ascii=False)
    assert "未直接命中" not in blob
    assert blob.count("今日资金未回流") == 1          # 同一结论只说一次
    assert "其余 4 个方向今日资金未回流" in blob
    for i in range(4):
        assert f"题材{i}（回调{i + 1}天）" in blob   # 方向名 + 回调天数一个不丢


def test_reflow_rows_capped_and_reversible(monkeypatch):
    """断板回流块行数上限（旧行为无上限→11 行成墙）；置 None 即回退全量。"""
    cands = [{"name": f"票{i}", "code": f"00000{i}", "sector": "测试",
              "tag": "昨日涨停·今日断板回流", "today_pct": 1.0 + i, "max_boards": 2}
             for i in range(1, 12)]
    monkeypatch.setattr(bc, "_ambush_reflow_pool", lambda *a, **k: {"candidates": cands})
    rows = [b for b in bc._ambush_reflow_block(ROOT / "cache")
            if b.get("tag") == "div" and " · 测试" in b["text"]["content"]]
    assert bc.AMBUSH_REFLOW_MAX == 3 and len(rows) == 3
    monkeypatch.setattr(bc, "AMBUSH_REFLOW_MAX", None)
    rows2 = [b for b in bc._ambush_reflow_block(ROOT / "cache")
             if b.get("tag") == "div" and " · 测试" in b["text"]["content"]]
    assert len(rows2) == 11


def test_caliber_wording_follows_strategy_mode(tmp_path, monkeypatch):
    """口径跟当日策略走（⛔ 卡头不得再写已废止的形态名）；定调与卡名共用单一出口。"""
    (tmp_path / "ambush_pullback_2026-10-15.json").write_text(
        json.dumps({"trade_date": "2026-10-15", "candidates": [],
                    "params": {"strategy_mode": "strong_trend"}}, ensure_ascii=False),
        encoding="utf-8")
    assert "分歧洗盘后放量反包" in bc._ambush_caliber(tmp_path, "2026-10-15")
    assert "阴线回踩十日线" not in bc._ambush_caliber(tmp_path, "2026-10-15")
    # 旧快照（无 strategy_mode）→ 回落旧口径，历史卡片渲染不变
    (tmp_path / "ambush_pullback_2026-10-14.json").write_text(
        json.dumps({"trade_date": "2026-10-14", "candidates": []}, ensure_ascii=False),
        encoding="utf-8")
    assert "收阴回踩十日线" in bc._ambush_caliber(tmp_path, "2026-10-14")
    # 卡名跟着同一口径走（卡名 / 定调 / 候选块标题禁止各自硬编码）
    _stub_ambush_sources(monkeypatch)
    _stub_snapshot(monkeypatch, {"trade_date": "2026-10-15", "candidates": [],
                                 "params": {"strategy_mode": "strong_trend"}})
    assert "强势趋势核心" in bc.build_ambush_card({"picks": []})["title"]
    _stub_snapshot(monkeypatch, {"trade_date": "2026-10-15", "candidates": []})
    assert "阴线回踩十日线" in bc.build_ambush_card({"picks": []})["title"]


def test_sector_zt_label_is_cumulative_not_daily(tmp_path):
    """`sector_zt_count` 是近5日累计同行业涨停家数，⛔ 不得写成「当日同方向 N 家」。"""
    snapshot = {"strategy": "ambush_pullback_ma10_v1", "trade_date": "2026-10-15",
                "params": {"strategy_mode": "strong_trend"},
                "candidates": [{"code": "300821", "name": "东岳硅材", "sector": "化学制品",
                                "hot_date": "2026-10-09", "sector_zt_count": 17,
                                "max_boards": 1, "close": 18.3, "ma10": 16.39,
                                "ma10_gap_pct": 11.64, "volume_ratio": 2.47,
                                "score": 22.4, "pct": 1.61}]}
    (tmp_path / "ambush_pullback_2026-10-15.json").write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
    )
    text = "\n".join(b["text"]["content"] for b in bc._ambush_pullback_block(tmp_path, "2026-10-15")
                     if b.get("tag") == "div")
    assert "近5日同方向17家涨停" in text and "当日同方向" not in text


# ── 2026-10-15 第二轮收敛（用户「还是觉得有点多余！继续收敛！」）─────────────────
def test_excluded_themes_rendered_as_one_line_name_only(monkeypatch):
    """今日已排除：一行只列方向名。

    旧版給每个题材复读「（板块内 N 只今天已经涨停·已经启动）」——8 个题材 8 遍同一句；
    且「涨停家数」在 🧠 涨停与催化段已按当日归因报过，两处口径不同并排像互相打脸。
    """
    _stub_ambush_sources(monkeypatch)
    tr = {"candidates": [
        {"theme": "方向A", "cooled_days": 2, "phase": "watch",
         "watch_pool": [{"code": "000011.SZ", "name": "甲票"}]},
        {"theme": "方向B", "cooled_days": 3, "phase": "watch",
         "watch_pool": [{"code": "000012.SZ", "name": "乙票"}]},
    ]}
    live = {"leaders": [], "limit_up_stocks": [
        {"code": "000011.SZ", "name": "甲票", "pct": 9.97},
        {"code": "000012.SZ", "name": "乙票", "pct": 10.0},
    ]}
    monkeypatch.setattr(bc, "_load_ambush_data", lambda: (tr, live))
    divs = _card_divs(bc.build_ambush_card({"picks": []}))
    excluded = [d for d in divs if "今日已排除" in d]
    assert len(excluded) == 1, excluded
    assert "方向A / 方向B" in excluded[0]
    assert "板块内" not in excluded[0] and "只今天已经涨停" not in excluded[0]


def test_reflow_warning_merged_into_header_and_reversible(monkeypatch):
    """断板回流的「性质不是新低位埋伏」并进标题，⛔ 不再末尾重复一句；开关可逆。"""
    cands = [{"name": "测票", "code": "000001", "sector": "测试",
              "tag": "昨日涨停·今日断板回流", "today_pct": 3.0, "max_boards": 2}]
    monkeypatch.setattr(bc, "_ambush_reflow_pool", lambda *a, **k: {"candidates": cands})
    blob = json.dumps(bc._ambush_reflow_block(ROOT / "cache"), ensure_ascii=False)
    assert blob.count("新低位埋伏") == 1                       # 只说一次
    assert "｜性质不是新低位埋伏" in blob                       # 位置：并进标题
    monkeypatch.setattr(bc, "AMBUSH_LEAN_LAYOUT", False)
    blob2 = json.dumps(bc._ambush_reflow_block(ROOT / "cache"), ensure_ascii=False)
    assert "上面是「涨停后休整过、今天钱又回来」的票" in blob2   # 旧布局：末尾句逐字回来
    assert "今日资金回流" in blob2                                # 旧标题也回来


def test_scan_block_hidden_by_default_and_reversible(monkeypatch):
    """参照个股块默认不渲染（2026-10-15 用户「参照个股·资金/地位/人气 不需要这个模块」）。

    置 AMBUSH_SHOW_SCAN=True 回落旧行为：渲染名单外票，并按 AMBUSH_SCAN_DEDUP 与
    「当日候选池 / 推票」去重（同一票全卡只出现一次）。
    """
    _stub_ambush_sources(monkeypatch)
    monkeypatch.setattr(bc, "_ambush_pullback_codes", lambda *a, **k: {"000002"})
    ai = {
        "picks": [{"theme": "测试方向",
                   "stocks": [{"name": "推票", "code": "000003", "pct": 1.0}]}],
        "stock_scan": [
            {"name": "池内票", "code": "000002", "role": "名单内", "pct": 2.0},
            {"name": "推票", "code": "000003", "role": "名单内", "pct": 1.0},
            {"name": "名单外票", "code": "000009", "role": "方向参照", "pct": 3.0},
        ],
    }
    assert bc.AMBUSH_SHOW_SCAN is False
    blob = "\n".join(_card_divs(bc.build_ambush_card(ai)))
    assert "参照个股" not in blob and "名单外票" not in blob     # 整块不渲染
    monkeypatch.setattr(bc, "AMBUSH_SHOW_SCAN", True)
    blob2 = "\n".join(_card_divs(bc.build_ambush_card(ai)))
    assert "参照个股" in blob2 and "名单外票" in blob2           # 回退旧行为
    assert "池内票" not in blob2                                 # 去重仍生效（池内票不重复出现）


def test_pullback_metrics_merge_pullback_day_no_template(tmp_path):
    """候选名单：洗盘日并进指标行，⛔ 不再每只票复读一行「→ 次日反包转一致」（那是准入条件）。"""
    snapshot = {
        "trade_date": "2026-10-15",
        "params": {"strategy_mode": "strong_trend"},
        "candidates": [{
            "code": "000001", "name": "测试股份", "sector": "测试题材",
            "close": 22.5, "ma10": 20.63, "ma10_gap_pct": 9.0,
            "volume_ratio": 1.7, "score": 24.9, "pct": -2.17,
            "pullback_date": "2026-10-13", "pullback_drop_pct": -4.31,
        }],
    }
    (tmp_path / "ambush_pullback_2026-10-15.json").write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    blocks = bc._ambush_pullback_block(tmp_path, "2026-10-15")
    text = "\n".join(b["text"]["content"] for b in blocks if b.get("tag") == "div")
    assert "洗盘10-13 -4.3%" in text
    assert "次日反包转一致" not in text                          # 模板句不得复读
    # 洗盘事实与指标同处一行（不另起一行）
    row = next(b["text"]["content"] for b in blocks
               if b.get("tag") == "div" and "测试股份" in b["text"]["content"])
    assert len(row.split("\n")) == 2                            # 标题行 + 一行指标（含洗盘）


# ── 2026-10-15 第三轮：买入出口唯一化（用户「尾盘买入标的和下面这个定调好像冲突了，只留一个」）──
def test_buy_instruction_has_single_exit(monkeypatch):
    """「买入」全卡只准一个出口＝块⓪「✅ 今日买入」；⛔ 旧标题与候选块都不得再下买入指令。"""
    _stub_ambush_sources(monkeypatch)
    ai = {"picks": [{"theme": "测试方向",
                     "stocks": [{"name": "推票", "code": "000001", "pct": 1.0}],
                     "reason": "理由A", "risk": "风险A"}]}
    divs = _card_divs(bc.build_ambush_card(ai))
    blob = "\n".join(divs)
    assert blob.count(bc.AMBUSH_BUY_NOTE) == 1                 # 买入口径行全卡只出现一次
    assert "✅ 今日买入" in blob
    assert "今日尾盘买入标的" not in blob                        # 旧标题（与候选块撞名）不得再现
    i_buy = next(i for i, d in enumerate(divs) if "✅ 今日买入" in d)
    i_note = next(i for i, d in enumerate(divs) if bc.AMBUSH_BUY_NOTE in d)
    assert i_buy < i_note                                      # 口径行挂在本块内


def test_candidate_block_title_drops_buy_and_inlines_caliber(tmp_path):
    """候选块＝机械扫描证据池：标题不带「买入」、紧跟定调行，且定调点破时点。"""
    (tmp_path / "ambush_pullback_2026-10-15.json").write_text(
        json.dumps({"trade_date": "2026-10-15", "generated_at": "2026-10-15T14:30:00+08:00",
                    "params": {"strategy_mode": "strong_trend"}, "data_ok": True,
                    "candidates": [{"code": "000001", "name": "测试股份", "pct": -2.0,
                                    "close": 10.0, "ma10": 9.5, "ma10_gap_pct": 5.0,
                                    "volume_ratio": 1.5, "score": 20.0}]},
                   ensure_ascii=False), encoding="utf-8")
    divs = [b["text"]["content"] for b in bc._ambush_pullback_block(tmp_path, "2026-10-15")
            if b.get("tag") == "div"]
    assert divs[0].startswith("<text_tag color='purple'>📋 今日候选")
    assert "买入" not in divs[0] and "1 只 · 机械扫描" in divs[0]
    assert "📌 定调" in divs[1]                                 # 定调紧邻标题（同块，不隔 hr）
    assert bc.AMBUSH_BUY_NOTE not in "\n".join(divs)             # 本块不下买入指令
    # 时点点破：洗盘/反包是前一两日的事 —— 否则「定调要反包、推的票今天收阴」看着自相矛盾
    assert "前一两日" in divs[1] and "今天只等它回踩" in divs[1]


def test_caliber_block_inlining_reversible(tmp_path, monkeypatch):
    """定调并入候选块（默认）；`AMBUSH_SHOW_CALIBER_AS_BLOCK=True` 回退独立块旧布局。"""
    (tmp_path / "ambush_pullback_2026-10-15.json").write_text(
        json.dumps({"trade_date": "2026-10-15", "params": {"strategy_mode": "strong_trend"},
                    "candidates": [{"code": "000001", "name": "测试股份", "pct": -2.0,
                                    "close": 10.0, "ma10": 9.5, "ma10_gap_pct": 5.0,
                                    "volume_ratio": 1.5, "score": 20.0}]},
                   ensure_ascii=False), encoding="utf-8")
    assert bc.AMBUSH_SHOW_CALIBER_AS_BLOCK is False
    divs = [b["text"]["content"] for b in bc._ambush_pullback_block(tmp_path, "2026-10-15")
            if b.get("tag") == "div"]
    assert "📌 定调" in divs[1]
    monkeypatch.setattr(bc, "AMBUSH_SHOW_CALIBER_AS_BLOCK", True)
    divs2 = [b["text"]["content"] for b in bc._ambush_pullback_block(tmp_path, "2026-10-15")
             if b.get("tag") == "div"]
    assert "定调" not in divs2[1]                               # 回退：候选块内不再带定调
