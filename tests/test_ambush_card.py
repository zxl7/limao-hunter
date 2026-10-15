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

    assert "阴线回踩十日线·今日尾盘买入候选" in text
    assert "测试股份(000001)" in text
    assert "距十日线+0.3%" in text
    assert "量比0.70" in text
    # 时点口径（2026-10-09 用户「今天推出来之后就买入计入战绩，明天10点准时卖出」）
    assert "计入战绩" in text
    assert "计入收益" in text
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
    card = bc.build_ambush_card(None)
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
    card = bc.build_ambush_card(None)
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
    assert "🎯 今日尾盘买入标的" in divs[0], divs[0]
    blob = json.dumps(card, ensure_ascii=False)
    # 置顶：在定调之前，也在综合研判之前；综合研判仍恒置底
    assert -1 < blob.find("🎯 今日尾盘买入标的") < blob.find("📌 定调")
    assert blob.find("🎯 今日尾盘买入标的") < blob.find("🎯 综合研判")
    # 可逆：关闭开关即回落到「AI 研判层·置底」原位置（定调重新回到卡首）
    monkeypatch.setattr(bc, "AMBUSH_PICKS_AT_TOP", False)
    card2 = bc.build_ambush_card(ai)
    divs2 = [e["text"]["content"] for e in card2["elements"] if e.get("tag") == "div"]
    assert "📌 定调" in divs2[0] and "今日尾盘买入标的" not in divs2[0], divs2[0]
    blob2 = json.dumps(card2, ensure_ascii=False)
    assert blob2.find("📌 定调") < blob2.find("🎯 今日尾盘买入标的") < blob2.find("🎯 综合研判")


def test_no_picks_keeps_card_head_unchanged(monkeypatch):
    """无推票时置顶模块不得留空占位（卡首仍是定调）。"""
    _stub_ambush_sources(monkeypatch)
    assert bc.AMBUSH_PICKS_AT_TOP is True
    card = bc.build_ambush_card({"ai_flow": "资金口径说明", "picks": [], "ai_verdict": "综合结论Y"})
    divs = [e["text"]["content"] for e in card["elements"] if e.get("tag") == "div"]
    assert "📌 定调" in divs[0], divs[0]
    assert "🎯 今日尾盘买入标的" not in json.dumps(card, ensure_ascii=False)


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
    # 位置：推票标题之后、定调之前 —— 即「跟在（推票的）第二部分后面」
    blob = json.dumps(card, ensure_ascii=False)
    assert -1 < blob.find("🎯 今日尾盘买入标的") < blob.find("⚠️ 风险：") < blob.find("📌 定调")
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
