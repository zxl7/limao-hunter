# -*- coding: utf-8 -*-
"""AI 口径账户层单测（2026-09-14 用户「全量以 AI 为唯一口径！」）。

锁定：账户账本 / 持仓 / 战绩口径 = AI 决策
      成交 = AI 判 participate ∧ 竞价涨幅 ≤ AI 自定 entry_max_gap ∧ 有竞价数据
      机械 signal_eval 结果降级为参考信号（mech_can_enter / decision_status 照常写入）
      AI 决策缺失（该执行日无决策行）→ 诚实回退机械口径（caliber=mechanical_fallback）
      开关 AI_CALIBER_ACCOUNT=False → 一键回退旧行为

全隔离 tmp_path：不触真实台账、不触网。
"""
import importlib.util
import json
import sys
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
if str(WC) not in sys.path:
    sys.path.insert(0, str(WC))

from core import ai_caliber, nav_ledger, strategy_metrics  # noqa: E402
from pipelines import pipeline_verify as pv  # noqa: E402


# ── 夹具 ────────────────────────────────────────────────────────────
def _cand(code, name="测试票", exp=(1.0, 3.0), super_gap=4.0):
    return {"code": code, "name": name, "boards": 1, "bucket_label": "接力池",
            "theme": "测试题材",
            "expectation": {"expected_range": list(exp), "super_gap_min": super_gap}}


def _q(prev, gap_pct, amt=20.0):
    ap = round(prev * (1 + gap_pct / 100.0), 3)
    return {"prev_close": prev, "auction_price": ap, "auction_amount_yi": amt}


def _dec(view="participate", limit=3.0):
    return {"view": view, "entry_max_gap": limit, "name": "测试票", "reason": "单测理由"}


def _isolate_ledgers(monkeypatch, tmp_path):
    monkeypatch.setattr(nav_ledger, "LEDGER_PATH", tmp_path / "nav.jsonl")
    monkeypatch.setattr(strategy_metrics, "METRICS_PATH", tmp_path / "metrics.json")


# ── 1) 裁决函数（纯逻辑）─────────────────────────────────────────────
def test_decide_participate_within_limit():
    """AI 判参与 ∧ 竞价在 5% 硬线内 → 成交（⛔ 2026-09-14 起用用户硬线，非 AI 自定值）。"""
    ok, note, meta = ai_caliber.decide(_dec("participate", 3.0), 2.10)
    assert ok is True
    assert "买入" in note and "5%" in note
    assert meta["ai_view"] == "participate"
    assert meta["ai_gap_limit"] == ai_caliber.USER_ENTRY_MAX_GAP_PCT == 5.0
    assert meta["ai_declared_limit"] == 3.0          # AI 自定值仅留痕


def test_decide_ignores_ai_declared_limit_below_user_floor():
    """⛔ 用户纪律核心回归点：AI 自定 2% **不得**否决 ≤5% 的竞价。

    实证（09-14 闽东电力 000993）：AI 判 participate + 自定 2%，竞价 +4.54% 被旧逻辑拒掉，
    该票当日涨停 +10.01%，账户错过 +5.24%。修复后 4.54% ≤ 5% → 必须成交。
    """
    ok, note, meta = ai_caliber.decide(_dec("participate", 2.0), 4.54)
    assert ok is True, note
    assert "买入" in note
    assert meta["ai_gap_limit"] == 5.0 and meta["ai_declared_limit"] == 2.0


def test_decide_gap_over_user_line_gives_up():
    """AI 判参与但竞价高开 >5% → 不追（用户纪律：高开超 5% 的不买）。"""
    ok, note, meta = ai_caliber.decide(_dec("participate", 2.0), 6.00)
    assert ok is False
    assert "超过 5%" in note
    assert meta["ai_gap_limit"] == 5.0


def test_decide_watch_skip_and_missing():
    """AI 判观察/放弃 → 不买；AI 没判断过这只票 → 不买（不擅自替 AI 决定）。"""
    assert ai_caliber.decide(_dec("watch"), 1.0)[0] is False
    assert ai_caliber.decide(_dec("skip"), 1.0)[0] is False
    ok, note, meta = ai_caliber.decide(None, 1.0)
    assert ok is False and "未对这只票下判断" in note and meta["ai_view"] is None


def test_decide_missing_quote_is_honest():
    """无竞价数据 → 不买（诚实跳过，不假设成交）。"""
    ok, note, _ = ai_caliber.decide(_dec("participate", 3.0), None)
    assert ok is False and "无竞价数据" in note


# ── 2) 决策加载（trade_date 匹配 + 缺失语义）──────────────────────────
def test_load_ai_decisions_matches_trade_date(monkeypatch, tmp_path):
    """按执行日（= decision_date 次一交易日）匹配；历史行无 trade_date 时推导。"""
    monkeypatch.setattr(ai_caliber, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir()
    rows = [
        {"decision_date": "2026-09-11", "code": "000993", "name": "闽东电力",
         "view": "participate", "entry_max_gap": 2.0},          # 周五 → 执行日 09-14(周一)
        {"decision_date": "2026-09-10", "code": "002585", "name": "双星新材",
         "view": "watch", "entry_max_gap": 3.0},                # → 执行日 09-11
        {"decision_date": "2026-09-11", "code": "002201", "name": "九鼎新材",
         "view": "skip", "trade_date": "2026-09-14", "entry_max_gap": 3.0},  # 显式 trade_date
    ]
    (tmp_path / "cache" / "ai_decisions.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    d14 = ai_caliber.load_ai_decisions("2026-09-14")
    assert set(d14) == {"000993", "002201"}
    assert d14["000993"]["entry_max_gap"] == 2.0
    d11 = ai_caliber.load_ai_decisions("2026-09-11")
    assert set(d11) == {"002585"}


def test_load_ai_decisions_missing_is_none(monkeypatch, tmp_path):
    """该执行日无任何决策行 → None（= 缺失·非「AI 决定空仓」，调用方须回退机械口径）。"""
    monkeypatch.setattr(ai_caliber, "ROOT", tmp_path)
    assert ai_caliber.load_ai_decisions("2026-09-14") is None
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "ai_decisions.jsonl").write_text("", encoding="utf-8")
    assert ai_caliber.load_ai_decisions("2026-09-14") is None


# ── 3) _build_record：AI 口径接管 can_enter ───────────────────────────
def test_record_ai_buy_within_limit():
    """AI 参与 ∧ 竞价 2% ≤ 用户 5% 硬线 → 成交（can_enter=True·caliber=ai）。"""
    rec = pv._build_record(_cand("000001"), _q(10.0, 2.0), 10.5, date10="2026-09-11",
                           ai_decisions={"000001": _dec("participate", 3.0)})
    oc = rec["performance"]["open_check"]
    assert oc["can_enter"] is True and oc["caliber"] == "ai"
    assert oc["ai_view"] == "participate" and oc["ai_gap_limit"] == 5.0
    assert oc["ai_declared_limit"] == 3.0                # AI 自定值仅留痕
    assert oc["mech_can_enter"] is True                 # 机械本就达标 → 口径未改变结论
    assert oc["caliber_changed"] is False
    assert rec["performance"]["next_day"]["status"] == "covered"
    assert rec["performance"]["next_day"]["return_pct"] == 2.94   # (10.5/10.2-1)*100


def test_record_ai_gives_up_on_high_open():
    """AI 参与但竞价高开 >5% → 不成交（skipped·不进账本），机械结论照留。"""
    rec = pv._build_record(_cand("000993"), _q(10.0, 6.0), 10.5, date10="2026-09-11",
                           ai_decisions={"000993": _dec("participate", 2.0)})
    oc = rec["performance"]["open_check"]
    assert oc["can_enter"] is False and oc["caliber"] == "ai"
    assert oc["ai_gap_limit"] == 5.0 and "超过 5%" in oc["ai_note"]
    assert oc["ai_declared_limit"] == 2.0
    assert rec["performance"]["next_day"]["status"] == "skipped"
    assert rec["performance"]["next_day"]["return_pct"] is None


def test_record_ai_kk993_no_longer_gives_up_at_454():
    """⛔ 09-14 事故回归点：AI 自定 2% **不得**否决 ≤5% 的竞价（AI 自定上限仅留痕）。

    09-14 旧实现按 AI 自定 2% 判 skipped，当日该票涨停 +10.01%（14.51 → 15.27）。
    ⚠️ 2026-09-17「A 计划」后成交还须**竞价落在预期区间内**——故此处区间取 [1.0, 5.0]
    （4.54% 属可接受高开）；超预期拦截见 test_record_ai_zone_above_blocks_super_gap。
    """
    rec = pv._build_record(_cand("000993", exp=(1.0, 5.0)), _q(13.88, 4.54), 15.27,
                           date10="2026-09-11",
                           ai_decisions={"000993": _dec("participate", 2.0)})
    oc = rec["performance"]["open_check"]
    assert oc["can_enter"] is True and oc["caliber"] == "ai"
    assert oc["ai_gap_limit"] == 5.0 and oc["ai_declared_limit"] == 2.0
    assert oc["zone"] == "ok"                                     # 方案 A：落预期区间 → 成交
    assert rec["performance"]["next_day"]["status"] == "covered"
    assert rec["performance"]["next_day"]["return_pct"] == 5.24   # (15.27/14.51-1)*100


def test_record_ai_zone_above_blocks_super_gap():
    """方案 A（2026-09-17 用户「A 计划」）：竞价**高于预期区间**（高开太多）→ 不买。

    与卡面「🤖 AI 判断：观察 → 高开超预期·高开太多 → 继续观察」同源（`gap_zone`）。
    实证 09-17 西陇科学(002584) 预期[1.4,3.4] 竞价+7.33% → 当日收 -0.31%（高开低走）。
    """
    rec = pv._build_record(_cand("000993", exp=(1.0, 3.0)), _q(13.88, 4.54), 15.27,
                           date10="2026-09-11",
                           ai_decisions={"000993": _dec("participate", 2.0)})
    oc = rec["performance"]["open_check"]
    assert oc["can_enter"] is False                     # 超预期 → 不买（观察）
    assert oc["zone"] == "above"
    assert "高于预期区间" in oc["ai_note"]
    assert rec["performance"]["next_day"]["status"] == "skipped"


def test_record_ai_vetoes_mechanical_buy():
    """AI 判放弃 → 即使机械竞价达标也不买（AI 是唯一口径）。"""
    rec = pv._build_record(_cand("002585"), _q(10.0, 2.0), 10.5, date10="2026-09-11",
                           ai_decisions={"002585": _dec("watch", 3.0)})
    oc = rec["performance"]["open_check"]
    assert oc["mech_can_enter"] is True                  # 机械原本达标
    assert oc["can_enter"] is False and oc["caliber_changed"] is True
    assert "观察" in oc["ai_note"]
    assert rec["performance"]["next_day"]["status"] == "skipped"


def test_record_ai_zone_below_gives_up():
    """方案 A（2026-09-17）：竞价**低于预期区间**（不及预期）→ 放弃 —— 与机械口径同向（不再覆盖）。

    与卡面「🤖 AI 判断：观察 → 今日竞价X%·不及预期 → 放弃」同源（`gap_zone`）。
    """
    rec = pv._build_record(_cand("600410", exp=(4.0, 6.0)), _q(10.0, 1.8), 10.5,
                           date10="2026-09-11",
                           ai_decisions={"600410": _dec("participate", 3.0)})
    oc = rec["performance"]["open_check"]
    assert oc["mech_can_enter"] is False                 # 机械：低于预期下沿 → 不达标
    assert oc["can_enter"] is False                      # AI 也放弃（区间门·口径一致）
    assert oc["zone"] == "below"
    assert "低于预期区间" in oc["ai_note"]
    assert rec["performance"]["next_day"]["status"] == "skipped"


def test_record_ai_zone_ok_lets_ai_buy():
    """方案 A：AI 判参与 ∧ 竞价落预期区间 ∧ ≤5% → 成交（AI 唯一口径仍成立）。"""
    rec = pv._build_record(_cand("600410", exp=(0.5, 3.0)), _q(10.0, 1.8), 10.5,
                           date10="2026-09-11",
                           ai_decisions={"600410": _dec("participate", 3.0)})
    oc = rec["performance"]["open_check"]
    assert oc["zone"] == "ok"
    assert oc["can_enter"] is True
    assert rec["performance"]["next_day"]["status"] == "covered"


def test_record_falls_back_when_ai_decisions_missing():
    """该执行日无 AI 决策行 → 诚实回退机械口径（caliber=mechanical_fallback）。"""
    rec = pv._build_record(_cand("000001"), _q(10.0, 2.0), 10.5, date10="2026-09-11",
                           ai_decisions=None)
    oc = rec["performance"]["open_check"]
    assert oc["caliber"] == "mechanical_fallback"
    assert oc["can_enter"] is True                       # 机械口径结论保留
    assert "回退机械口径" in oc["caliber_note"]


def test_record_switch_off_restores_mechanical(monkeypatch):
    """开关关闭 → 即使有 AI 决策也走机械口径（一键回退）。"""
    monkeypatch.setattr(ai_caliber, "AI_CALIBER_ACCOUNT", False)
    rec = pv._build_record(_cand("002585"), _q(10.0, 2.0), 10.5, date10="2026-09-11",
                           ai_decisions={"002585": _dec("watch", 3.0)})
    oc = rec["performance"]["open_check"]
    assert oc["caliber"] == "mechanical"
    assert oc["can_enter"] is True                       # 回退后 AI 的「观察」不再拦截


# ── 4) run_verify + 净值账本：只记 AI 实际买到的票 ─────────────────────
def test_ledger_only_counts_ai_filled(monkeypatch, tmp_path):
    """账户账本 = AI 口径：机械达标但 AI 放弃的票不进账本，行内含 caliber 留痕。"""
    _isolate_ledgers(monkeypatch, tmp_path)
    cand = {"date": "2026-09-11",
            "relay": [_cand("000001", "甲票"), _cand("000002", "乙票")],
            "watch": [_cand("000003", "丙票"), _cand("000004", "丁票")],
            "environment": {"regime": "neutral"}}
    quotes = {"000001": _q(10.0, 2.0),      # 机械 buy；AI 参与 → 成交
              "000002": _q(10.0, 2.0),      # 机械 buy；AI 放弃 → 不成交
              "000003": _q(10.0, 6.0),      # 机械 pending；AI 参与但高开 6% > 5% 硬线 → 不成交
              "000004": _q(10.0, 2.0)}      # 机械 buy；AI 只观察 → 不成交
    closes = {"000001": 10.5, "000002": 10.5, "000003": 10.5, "000004": 10.5}
    decs = {"000001": _dec("participate", 3.0), "000002": _dec("skip", 3.0),
            "000003": _dec("participate", 2.0), "000004": _dec("watch", 3.0)}

    # run_verify 内部按 trade_date 读 ai_decisions.jsonl → 用 tmp 台账喂真实链路
    monkeypatch.setattr(ai_caliber, "ROOT", tmp_path)
    (tmp_path / "cache").mkdir(exist_ok=True)
    rows = [{"decision_date": "2026-09-11", "trade_date": "2026-09-14", "code": c,
             "name": d["name"], "view": d["view"], "entry_max_gap": d["entry_max_gap"],
             "reason": "单测"} for c, d in decs.items()]
    (tmp_path / "cache" / "ai_decisions.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    res = pv.run_verify(candidates=cand, quotes=quotes, next_close_map=closes)
    s = res["summary"]
    assert s["caliber"] == "ai" and s["ai_decisions"] == 4
    assert s["buy_signals"] == 1          # 只有 000001 实际成交
    assert s["mech_buy_signals"] == 3     # 机械参考仍 3 只（000003 高开 6% 机械同样 pending）
    assert s["caliber_switched"] == 2     # 000002 AI 否决、000004 AI 只观察

    led = [json.loads(x) for x in (tmp_path / "nav.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(led) == 1 and led[0]["trade_date"] == "2026-09-14"
    assert led[0]["codes"] == ["000001"] and led[0]["stock_count"] == 1
    assert led[0]["caliber"] == "ai"      # 口径留痕
    assert led[0]["avg_return_pct"] == 2.94   # (10.5/10.2-1)*100


def test_run_verify_falls_back_without_ai(monkeypatch, tmp_path):
    """无 AI 决策文件 → summary 标 mechanical_fallback（不静默按空仓结算）。"""
    _isolate_ledgers(monkeypatch, tmp_path)
    monkeypatch.setattr(ai_caliber, "ROOT", tmp_path)
    cand = {"date": "2026-09-11", "relay": [_cand("000001", "甲票")], "watch": [],
            "environment": {"regime": "neutral"}}
    quotes = {"000001": _q(10.0, 2.0)}
    res = pv.run_verify(candidates=cand, quotes=quotes, next_close_map={"000001": 10.5})
    assert res["summary"]["caliber"] == "mechanical_fallback"
    assert res["summary"]["buy_signals"] == 1     # 机械口径仍在结算
    assert res["payload"]["caliber"] == "mechanical_fallback"


# ── 5) 卡面口径：卡头 = 实买（与账本同源），计划/机械降为括注 ─────────────
def _load_bc():
    spec = importlib.util.spec_from_file_location("build_cards", WC / "pipelines" / "build_cards.py")
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    return bc


def _card_text(card):
    return "\n".join(e.get("text", {}).get("content", "") for e in card["elements"] if e.get("text"))


def test_verify_card_head_uses_filled_count(monkeypatch, tmp_path):
    """AI 计划 1 只但竞价高开 >5% 没买到 → 卡头显示实买 0（与账本一致），并括注计划/机械。"""
    bc = _load_bc()
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ai_view_2099-03-02.json").write_text(json.dumps({
        "date": "2099-03-02", "decision_date": "2099-02-27", "entry_max_gap": 5.0,
        "stance": "defend", "stance_label": "只观察·最多试一只",
        "views": {"000993": {"view": "participate", "reason": "3板·带动3家",
                             "entry_max_gap": 2.0}}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    rec = {"code": "000993", "name": "闽东电力", "boards": 3, "bucket_label": "接力池",
           "performance": {"open_check": {"can_enter": False, "mech_can_enter": True,
                                          "caliber": "ai", "ai_view": "participate",
                                          "ai_gap_limit": 5.0, "decision_status": "buy",
                                          "gap_pct": 6.5}}}
    card = bc.build_verify_card({
        "payload": {"records": [rec], "environment": {"regime": "neutral"}},
        "summary": {"total_candidates": 1, "buy_signals": 0, "mech_buy_signals": 5,
                    "caliber": "ai", "hit_signals": 0, "covered": 14,
                    "nav_latest": 0.932762, "trade_date10": "2099-03-02"}})
    s = _card_text(card)
    assert "AI 买入信号" in s and "计划 1 只" in s and "机械达标 5 只" in s
    # 定调同口径：实买 0 → 空仓观望（不得写「AI 买1只」）
    assert "AI 空仓·观望" in card["title"]


def test_verify_card_mech_section_uses_mech_flag(monkeypatch, tmp_path):
    """「✅ 竞价达标·机械参考」段按 mech_can_enter 筛选——AI 放弃的机械达标票仍可见。"""
    bc = _load_bc()
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ai_view_2099-03-03.json").write_text(json.dumps({
        "date": "2099-03-03", "decision_date": "2099-02-28", "entry_max_gap": 3.0,
        "views": {"002585": {"view": "watch", "reason": "只观察", "entry_max_gap": 3.0}}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    rec = {"code": "002585", "name": "双星新材", "boards": 2, "bucket_label": "接力池",
           "performance": {"open_check": {"can_enter": False, "mech_can_enter": True,
                                          "caliber": "ai", "ai_view": "watch",
                                          "decision_status": "buy", "gap_pct": 2.1,
                                          "auction_amount_yi": 1.0,
                                          "auction_amount_need_yi": 0.5}}}
    card = bc.build_verify_card({
        "payload": {"records": [rec], "environment": {"regime": "neutral"}},
        "summary": {"total_candidates": 1, "buy_signals": 0, "mech_buy_signals": 1,
                    "caliber": "ai", "hit_signals": 0, "covered": 14,
                    "nav_latest": 1.0, "trade_date10": "2099-03-03"}})
    s = _card_text(card)
    assert "✅ 竞价达标·机械参考" in s and "双星新材" in s
    # 机械参考段与账本口径解耦：该票 can_enter=False（AI 只观察），但机械达标照常列出
    assert "🤖 AI 判断" not in s or "观察" in s


def test_plan_block_uses_user_gap_line(monkeypatch, tmp_path):
    """卡面「买入/没买到」用**用户 5% 硬线**（2026-09-14），⛔ 不读 AI 自定 entry_max_gap。

    事故背景：闽东电力竞价 +4.54%、AI 自定 2% → 旧卡面渲染「没买到」；新规则 4.54% ≤ 5%
    → 必须渲染「买入」（与账本/结算同值）。
    """
    bc = _load_bc()
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ai_view_2099-03-04.json").write_text(json.dumps({
        "date": "2099-03-04", "decision_date": "2099-03-01", "entry_max_gap": 2.0,
        "views": {"000993": {"view": "participate", "reason": "3板·带动3家",
                             "entry_max_gap": 2.0}}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")

    rec = {"code": "000993", "name": "闽东电力", "boards": 3, "bucket_label": "接力池",
           "performance": {"open_check": {"can_enter": True, "mech_can_enter": True,
                                          "caliber": "ai", "ai_view": "participate",
                                          "decision_status": "buy", "gap_pct": 4.54}}}
    card = bc.build_verify_card({
        "payload": {"records": [rec], "environment": {"regime": "neutral"}},
        "summary": {"total_candidates": 1, "buy_signals": 1, "mech_buy_signals": 1,
                    "caliber": "ai", "hit_signals": 0, "covered": 14,
                    "nav_latest": 1.0, "trade_date10": "2099-03-04"}})
    s = _card_text(card)
    assert "买入" in s and "高开5%以内" in s          # 4.54% ≤ 5% → 买到
    assert "上限+2%" not in s and "没买到" not in s    # ⛔ AI 自定 2% 不再上卡


# ── 6) 口径分界标注：账本跨口径必须在卡上标出 ─────────────────────────
def test_caliber_note_marks_switch():
    """纯机械历史（无 AI 行）→ 静默；账本出现 AI 口径行 → 标出分界日。"""
    bc = _load_bc()
    assert bc._caliber_note(None) == ""
    assert bc._caliber_note({"caliber": ""}) == ""
    assert bc._caliber_note({"caliber": "mechanical"}) == ""
    note = bc._caliber_note({"caliber": "ai", "caliber_from": "2026-09-14"})
    assert "09-14 起" in note and "AI" in note


def test_verify_card_renders_caliber_note(monkeypatch, tmp_path):
    """竞价验证卡战绩行下方渲染口径分界（净值曲线跨口径，不标会被误读）。"""
    bc = _load_bc()
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    monkeypatch.setattr(bc, "_load_metrics", lambda: {
        "nav_latest": 1.05, "caliber": "ai", "caliber_from": "2026-09-14",
        "metrics": {"next_day": {"tradable": {"covered": 15, "win_rate": 46.7,
                                              "avg_return": 0.31}}}})
    card = bc.build_verify_card({
        "payload": {"records": [], "environment": {"regime": "neutral"}},
        "summary": {"total_candidates": 0, "buy_signals": 0, "hit_signals": 0,
                    "covered": 0, "nav_latest": 1.05, "trade_date10": "2099-03-05"}})
    s = _card_text(card)
    assert "09-14 起" in s and "买什么由 AI 定" in s


def test_ledger_row_keeps_backfill_note():
    """账本行 note 留痕（2026-09-14 补记 000993 引入）——人工/规则修正补记必须可追溯。

    ⛔ 该字段曾被 _normalize_existing_row 静默丢弃（显式构造 dict 未透传），
    导致「系统自动成交」与「人工补记」在账本里无法区分。回归锁防止再次丢失。
    """
    rows = nav_ledger._merge_ledger_rows([], [{
        "trade_date": "2026-09-14", "recommendation_date": "2026-09-11",
        "caliber": "ai", "avg_return_pct": 5.24,
        "note": "规则修正补记：AI 自定 2% 上限已废止",
        "codes": ["000993"], "names": ["闽东电力"],
        "stocks": [{"code": "000993", "name": "闽东电力", "return_pct": 5.24}],
    }], base_nav=1.0)
    assert rows[-1]["note"] == "规则修正补记：AI 自定 2% 上限已废止"
    assert rows[-1]["nav"] == 1.0524
    # 历史无 note 行保持空串（不往老行塞噪声）
    rows2 = nav_ledger._merge_ledger_rows(
        [{"trade_date": "2026-09-09", "avg_return_pct": -3.37}], [], base_nav=1.0)
    assert rows2[0]["note"] == ""
