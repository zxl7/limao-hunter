# -*- coding: utf-8 -*-
"""卡面时点口径：**上一交易日的结论一律写「昨日」**（2026-09-18 用户拍板）。

用户原话：「毕竟是上一个交易日的结论，那么我们这边就不要用『今日』了，应该改成『昨日』。」

病灶（实测 09-18 09:26 竞价卡）：卡片把**上一交易日盘后**写的文案（AI 买入计划 / AI 判断 /
理由 / 个股点评 / 全局旁白）原样搬到次日读 —— 文案里的「今天」是撰写当天，读者却读成当天。
最刺眼的一处自相矛盾：🗣️ 已标「（09-17 周四收盘盘面）」，正文却写
「**今日盘面**：今天市场明显转冷」，而 09-18 实际是转暖日（78 家涨停、气氛 8 分）。

本文件锁死三条性质：
  1. **平移规则**：产出端按「撰写当日」视角写相对时间词；渲染端在**跨日复用**时 +1 天平移
     （今天→昨天 / 昨天→前天 / 明天→今天），⛔ 单次扫描替换、禁级联（否则今天→昨天→前天）。
  2. **AI 段一律「昨日」**：计划标题 / 攻守定调 / 选票名单 / 逐票 AI 判断都用「昨日」，
     只有**当日竞价实况**（g今日竞价 x%、今日开盘…）用「今日」。
  3. **当日卡不平移**：盘后 eod / 接力卡的点评仍写「今日走势」（文案撰写日 = 读卡日）。
"""
import importlib.util
import json
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("build_cards", WC / "pipelines" / "build_cards.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)


# ── 夹具 ──────────────────────────────────────────────────────────

# ── 分层隔离：本文件只测「跨日时点词平移」────────────────────────────────────
# ⛔ 2026-10-21：`VERIFY_CARD_LEAN`（收紧段取舍）是**另一层**开关，
#    与本文所测维度无关。不隔离会让本文件的断言随 lean 默认值漂移
#    （实测：lean 默认开启后有 1 例因「整段不再渲染」而假红）。
#    lean 自己的契约在 `tests/test_verify_card_lean.py`。
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _isolate_lean(monkeypatch):
    monkeypatch.setenv("VERIFY_CARD_LEAN", "0")
    # ⛔ 2026-10-21：`VERIFY_CARD_HIDE` 是**第三层**（整块不要：回撤/位置/战绩/全局）。
    #    本文件测的是时点词与分段语义，与「这块显示不显示」无关 —— 不隔离会让
    #    「🗣️ 全局」相关断言随隐藏默认值假红。隐藏契约在 test_verify_card_lean.py。
    monkeypatch.setenv("VERIFY_CARD_HIDE", "")
    # ⛔ 2026-10-21：`VERIFY_CARD_UNIFY_MECH`（机械口径并入 AI 判定）是**第四层**，
    #    会把机械段从「逐票完整展示」改成「一行口径 + 标题行胶囊」。本文件测的是
    #    跨日时点词平移，与「机械口径放哪」无关 —— 不隔离会让断言随融合默认值假红。
    #    融合契约在 `tests/test_verify_card_lean.py`。
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "0")


def _rec(code, name, status, gap, **extra):
    r = {"code": code, "name": name, "bucket_label": "高标/题材核心",
         "theme": "AI算力", "industry_sector": "消费电子",
         "performance": {"open_check": {"decision_status": status, "can_enter": status == "buy",
                                        "gap_pct": gap, "auction_amount_yi": 0.9,
                                        "auction_amount_need_yi": 0.6, "signal_label": "符合预期"}}}
    r.update(extra)
    return r


def _vr(trade_date, records, date10=None):
    return {"payload": {"records": records},
            "summary": {"total_candidates": len(records), "buy_signals": 0, "hit_signals": 0,
                        "covered": 0, "nav_latest": 1.0, "trade_date10": trade_date,
                        **({"date10": date10} if date10 else {})}}


def _text(card):
    return "\n".join(e.get("text", {}).get("content", "") for e in card["elements"] if e.get("text"))


def _ai_view(d, trade_date, views, **extra):
    payload = {"date": trade_date, "decision_date": "2026-09-17",
               "stance": "selective", "stance_label": "只做最强的一只",
               "direction": "AI算力", "note": "今天市场很冷，涨停从昨天的89家掉到47家",
               "entry_max_gap": 5.0, "views": views}
    payload.update(extra)
    (d / f"ai_view_{trade_date}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _quiet(monkeypatch):
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    # 🌊 周期块也要隔离（2026-10-21 P1）：它自带「（MM-DD 收盘盘面）」基准标注，
    # 不隔离会与本文件「旁白基准标注」的断言互相干扰，且会读到真实 feedback_matrix
    # → 测试结果随生产数据漂移。isolate 掉才能只锁旁白这一件事。
    monkeypatch.setattr(bc, "_cycle_phase_block", lambda **k: [])


# ── 1. 平移规则（单元）─────────────────────────────────────────────
def test_shift_day_words_pairs():
    """六个相对时间词各 +1 天：今天→昨天 / 今日→昨日 / 昨天→前天 / 昨日→前日 / 明天→今天 / 明日→今日。"""
    assert bc._shift_day_words("今天") == "昨天"
    assert bc._shift_day_words("今日") == "昨日"
    assert bc._shift_day_words("昨天") == "前天"
    assert bc._shift_day_words("昨日") == "前日"
    assert bc._shift_day_words("明天") == "今天"
    assert bc._shift_day_words("明日") == "今日"


def test_shift_day_words_single_pass_no_cascade():
    """⛔ 禁级联：一次扫描替换，否则「今天→昨天」会被二次替换成「前天」。"""
    out = bc._shift_day_words("今天涨停，昨天被砸")
    assert out == "昨天涨停，前天被砸", out
    assert "前天涨停" not in out


def test_shift_day_words_noop_when_no_time_word():
    """无时点词 / 空值 → 原样返回（零副作用，不编造）。"""
    assert bc._shift_day_words("竞价+2.10%、成交5.6亿") == "竞价+2.10%、成交5.6亿"
    assert bc._shift_day_words("") == ""
    assert bc._shift_day_words(None) == ""


def test_narr_render_prev_shifts_segment_label_and_body():
    """跨日复用（prev=True）：段名与正文一起平移 —— 今日走势→昨日走势、今天→昨天。"""
    txt = ("今日走势：今天开盘就顶在涨停价上。\n"
           "题材催化：算电协同大会 10 月 26 日落地。\n"
           "⚠️风险：今天全市场做多气氛只有 3 分。")
    out = bc._narr_render(txt, prev=True)
    assert "**昨日走势**" in out and "今日走势" not in out
    assert "昨天开盘就顶在涨停价上" in out
    assert "昨天全市场做多气氛只有 3 分" in out
    # 当日卡（prev 缺省）不动物时点词
    keep = bc._narr_render(txt)
    assert "**今日走势**" in keep and "今天开盘就顶在涨停价上" in keep


def test_narr_render_prev_shifts_panmian_label():
    """🗣️ 旁白的「盘面」段在次日卡上必须是「昨日盘面」——这正是本次用户抓包的那一处。"""
    out = bc._narr_render("盘面：今天市场明显转冷，做多气氛只有1分。", prev=True)
    assert "**昨日盘面**" in out and "**今日盘面**" not in out
    assert "昨天市场明显转冷" in out


# ── 2. AI 段 → 「昨日」（端到端）────────────────────────────────────
def test_verify_card_ai_plan_yesterday_wording(monkeypatch, tmp_path):
    """AI 买入计划段：标题/定调/空仓名单/理由 一律「昨日」，当日竞价实况仍「今日」。"""
    _quiet(monkeypatch)
    d = tmp_path / "cache"
    d.mkdir()
    _ai_view(d, "2026-09-18", {
        "002655": {"view": "participate", "reason": "3板·机器人今天7家涨停", "entry_max_gap": 5.0},
        "002584": {"view": "watch", "reason": "封板质量一般"},
    })
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    r1 = _rec("002655", "共达电声", "buy", 3.73)
    r2 = _rec("002584", "西陇科学", "reject", 1.2)
    s = _text(bc.build_verify_card(_vr("2026-09-18", [r1, r2], date10="2026-09-17")))

    assert "🤖 AI 昨日预案" in s and "AI 今日买入计划" not in s
    assert "AI 昨日判断：只做最强的一只" in s and "AI 判断：只做最强的一只" not in s
    assert "09-17 盘后定 · 重点看/一般/回避 · 买哪只今日竞价定" in s              # 两个时点同时交代
    # note（写于上一交易日盘后）→ 平移到读者视角
    assert "昨天市场很冷" in s and "今天市场很冷" not in s
    assert "涨停从前天的89家掉到47家" in s
    # 逐票判断 + 理由同源平移；但「今日竞价」保留（当日实况）
    assert "🤖 AI 昨日判断：重点看" in s
    assert "机器人昨天7家涨停" in s and "机器人今天7家涨停" not in s
    assert "今日竞价" in s
    # 其余名单：昨日没安排（⛔ 不能写「今天不买」）
    assert "其余 1 只昨日没有安排买" in s and "今天不买" not in s


def test_verify_card_hibernate_valve_yesterday_wording(monkeypatch, tmp_path):
    """逃生阀：定调是**昨日收盘**定的 → 「昨日收盘定的是空仓…今日竞价破例小仓试错」。"""
    _quiet(monkeypatch)
    d = tmp_path / "cache"
    d.mkdir()
    _ai_view(d, "2026-09-18", {"002655": {"view": "watch", "reason": "2板·方向内板数最高"}},
             stance="hibernate", stance_label="空仓待命·不买")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    r = _rec("002655", "共达电声", "pending", 3.73)
    r["performance"]["open_check"]["hibernate_valve"] = {"position": 0.2}
    s = _text(bc.build_verify_card(_vr("2026-09-18", [r], date10="2026-09-17")))

    assert "昨日收盘定的是空仓" in s and "今天本来是空仓日" not in s
    assert "今日竞价破例小仓试错" in s
    assert "🤖 AI 昨日判断：一般 → 破例小仓买" in s          # 卡面不与账本成交打架


def test_verify_card_empty_plan_says_yesterday(monkeypatch, tmp_path):
    """全空仓（无买入、无阀票）→ 「昨日没有选出要买的」，⛔ 禁「今天没有要买的」。"""
    _quiet(monkeypatch)
    d = tmp_path / "cache"
    d.mkdir()
    _ai_view(d, "2026-09-18", {"002584": {"view": "skip", "reason": "纯跟风"}},
             stance="hibernate", stance_label="空仓待命·不买")
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    s = _text(bc.build_verify_card(_vr("2026-09-18", [_rec("002584", "西陇科学", "reject", -4.6)],
                                       date10="2026-09-17")))
    assert "昨日没有选出要买的" in s and "今天没有要买的" not in s


# ── 3. 全局旁白（跨日才平移）──────────────────────────────────────
_GN = "账户：16个交易日8赚8亏。\n盘面：今天市场明显转冷，做多气氛只有1分。\n操作：控制仓位，明天高开超5%不追。"


def test_verify_card_global_narrative_shifted_when_prev_day(monkeypatch):
    """旁白透传自上一交易日（基准日 ≠ 交易当日）→ 加基准标注 + 正文时点词同步平移。"""
    _quiet(monkeypatch)
    vr = _vr("2026-09-18", [_rec("002584", "西陇科学", "reject", -4.6)], date10="2026-09-17")
    vr["global_narrative"] = _GN
    s = _text(bc.build_verify_card(vr))
    assert "（09-17 周四收盘盘面）" in s                   # 基准日标注（2026-09-14 铁律）
    assert "**昨日盘面**" in s and "**今日盘面**" not in s  # ⛔ 本次抓包点
    assert "昨天市场明显转冷" in s and "今天市场明显转冷" not in s
    assert "今天高开超5%不追" in s                          # 操作段顺移（明天→今天）


def test_verify_card_global_narrative_untouched_when_same_day(monkeypatch):
    """旁白由当日开盘前撰写（narrative_date = 当次交易日）→ 不标注、不平移。"""
    _quiet(monkeypatch)
    vr = _vr("2026-09-18", [_rec("002584", "西陇科学", "reject", -4.6)], date10="2026-09-17")
    vr["global_narrative"] = _GN
    vr["narrative_date"] = "2026-09-18"                     # 当日撰写留痕（run_wealth_code 写入）
    s = _text(bc.build_verify_card(vr))
    assert "收盘盘面）" not in s
    assert "今天市场明显转冷" in s and "**今日盘面**" in s


# ── 4. 当日卡不受影响 ─────────────────────────────────────────────
def test_eod_card_keeps_today_wording():
    """盘后 eod 卡：点评撰写日 = 读卡日 → 仍写「今日走势」（⛔ 不许被平移）。"""
    item = {"code": "000993", "name": "闽东电力", "score": 84.8, "boards": 3,
            "core_theme": "电力", "industry_sector": "电力",
            "expectation": {"expected_range": ["0.0", "1.4"]},
            "narrative": "今日走势：今天 09:25 秒板。\n⚠️风险：位置已经 3 板。"}
    card = bc.build_eod_card({"date": "2026-09-11", "regime": "neutral",
                              "relay": [item], "watch": [], "diagnostics": {}})
    s = _text(card)
    assert "**今日走势**" in s and "**昨日走势**" not in s
    assert "今天 09:25 秒板" in s


# ── 5. 全卡禁项（防漏网）──────────────────────────────────────────
_TODAY_ON_CONCLUSION = ("AI 今日买入计划", "AI 判断：", "今天没有要买的",
                        "只今天不买", "今天本来是空仓日", "今天没有票达标")


def test_verify_card_no_today_on_conclusion_lines(monkeypatch, tmp_path):
    """整卡扫描：AI 结论面不得再出现「今日/今天」（防新增模块漏网·同白话门禁思路）。"""
    _quiet(monkeypatch)
    d = tmp_path / "cache"
    d.mkdir()
    _ai_view(d, "2026-09-18", {
        "002655": {"view": "participate", "reason": "3板·机器人今天7家涨停"},
        "002584": {"view": "watch", "reason": "封板质量一般"},
        "000993": {"view": "skip", "reason": "纯跟风"},
    })
    monkeypatch.setattr(bc, "ROOT", tmp_path / "wealth_code")
    recs = [_rec("002655", "共达电声", "buy", 3.73),
            _rec("002584", "西陇科学", "pending", 4.6,
                 narrative="今日走势：今天开盘就顶在涨停价上。\n⚠️风险：今天气氛只有3分。"),
            _rec("000993", "闽东电力", "reject", -8.57)]
    s = _text(bc.build_verify_card(_vr("2026-09-18", recs, date10="2026-09-17")))
    hit = [k for k in _TODAY_ON_CONCLUSION if k in s]
    assert hit == [], f"AI 结论面残留「今日/今天」：{hit}"
    assert "**昨日走势**" in s                       # 跨日点评段名
    assert "昨天开盘就顶在涨停价上" in s
    assert "昨天气氛只有3分" in s
