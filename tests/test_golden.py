#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黄金数据冒烟测试（P0-2 · 2026-08-17）
======================================
用 08-13~17 真实数据 + 构造样例覆盖关键路径，防止回归：
- card_lint：正例通过 / 负例（hex/column_set/缺标题标注）阻断
- push_card.normalize：简写结构→飞书 interactive 包装、缺 title 兜底
- validate_schema：真实产物全过 / 缺字段负例报错
运行：/Users/zxl/.workbuddy/binaries/python/envs/default/bin/python -m pytest tests/ -v
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from card_lint import lint_card  # noqa: E402
from push_card import normalize, fix_newlines  # noqa: E402
import validate_schema as vs  # noqa: E402


# ────────────────────────── Fixtures ──────────────────────────

@pytest.fixture(scope="session")
def real_cards():
    """真实推送卡（代码生成）—— **最近 4 张**（最新 eod 卡 + 竞价卡）。

    ⚠️ 2026-09-18 修正：原实现 glob `wealth_code/cache/card_*.json` **全量 103 张历史卡**，
    混入 08-13 旧格式卡（`[炸板回封×1] 可信度35%`）与 `*_debug_*` / `*preview*` 调试卡
    → 与新政（白话门禁 Tier-1 阻断）冲突，且与 fixture 注释「最新一张」的**原意不符**。
    本测试目的是「防 lint 误伤**当前**生产卡」，故只取最近产物。
    """
    files = []
    for pat in ("card_*.json", "card_2026*.json"):
        files += [f for f in (ROOT / "wealth_code" / "cache").glob(pat)
                  if "debug" not in f.name and "preview" not in f.name]
    files = sorted({p.resolve() for p in files}, key=lambda p: -p.stat().st_mtime)[:4]
    cards = []
    for f in files:
        try:
            cards.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return cards


@pytest.fixture(scope="session")
def real_products():
    """真实数据产物：latest / feedback_matrix / candidates / auction_quotes。"""
    out = {}
    p1 = ROOT / "cache" / "pulse_snapshots" / "latest.json"
    if p1.exists():
        out["latest"] = json.loads(p1.read_text(encoding="utf-8"))
    p2 = ROOT / "cache" / "feedback_matrix.json"
    if p2.exists():
        out["fb"] = json.loads(p2.read_text(encoding="utf-8"))
    cands = sorted((ROOT / "wealth_code" / "cache").glob("candidates-*.json"))
    if cands:
        out["candidates"] = json.loads(cands[-1].read_text(encoding="utf-8"))
    auct = sorted((ROOT / "wealth_code" / "cache").glob("auction_quotes-*.json"))
    if auct:
        out["auction"] = json.loads(auct[-1].read_text(encoding="utf-8"))
    return out


def _good_card():
    return {
        "title": "盘中猎手·增量 | 11:15 | 光通信·沃格光电+10% | 涨停78",
        "color": "red",
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
                                    "content": "<text_tag color='red'>沃格光电(603773)</text_tag> +10.0%"}},
            {"tag": "div", "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": "**🔴 净流入**"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": "**🟢 净流出**"}},
            ]},
        ],
    }


# ────────────────────────── card_lint 正例 ──────────────────────────

class TestCardLint:
    def test_good_card_passes(self):
        ok, errs, warns = lint_card(_good_card())
        assert ok, f"正例卡应通过 lint: {errs}"

    def test_hex_color_blocked(self):
        card = _good_card()
        card["elements"][0]["text"]["content"] = "<font color='#ee2500'>红</font>"
        ok, errs, _ = lint_card(card)
        assert not ok
        assert any("hex" in e["msg"] for e in errs)

    def test_column_set_blocked(self):
        card = _good_card()
        card["elements"].append({"tag": "column_set", "columns": []})
        ok, errs, _ = lint_card(card)
        assert not ok
        assert any("column_set" in e["msg"] for e in errs)

    def test_title_missing_tag_blocked(self):
        card = _good_card()
        card["title"] = "盘中脉搏 13:30 | 测试"  # 无 ·增量/·全量
        ok, errs, _ = lint_card(card)
        assert not ok
        assert any("增量/全量" in e["msg"] for e in errs)

    def test_title_missing_blocked(self):
        card = _good_card()
        del card["title"]
        ok, errs, _ = lint_card(card)
        assert not ok
        assert any("title" in e["msg"] for e in errs)

    def test_real_cards_pass(self, real_cards):
        """真实代码生成卡必须全过 lint（防 lint 误伤生产卡）。"""
        assert real_cards, "未找到真实卡片 fixture"
        for c in real_cards:
            ok, errs, warns = lint_card(c)
            assert ok, f"真实卡 lint 应通过: {c.get('title','')[:40]} -> {errs}"

    def test_literal_newline_warns(self):
        card = _good_card()
        card["elements"][0]["text"]["content"] = "第一行\\n第二行"
        ok, _, warns = lint_card(card)
        assert ok  # 字面 \\n 只 WARN 不阻断
        assert any("\\\\n" in w["msg"] for w in warns)


# ────────────────────────── push_card.normalize ──────────────────────────

class TestNormalize:
    def test_short_form_wraps(self):
        card = _good_card()
        out = normalize(card)
        assert out["msg_type"] == "interactive"
        assert out["card"]["header"]["title"]["content"] == card["title"]
        assert out["card"]["config"]["wide_screen_mode"] is True

    def test_missing_title_fallback(self):
        card = _good_card()
        del card["title"]
        out = normalize(card)
        assert out["card"]["header"]["title"]["content"] == "📊 盘面播报"

    def test_fix_newlines_recursive(self):
        data = {"a": "x\\ny", "b": ["z\\n"], "c": {"d": "真\n换行"}}
        fixed = fix_newlines(data)
        assert fixed["a"] == "x\ny"
        assert fixed["b"][0] == "z\n"
        assert fixed["c"]["d"] == "真\n换行"

    def test_note_appended(self):
        card = _good_card()
        card["note"] = "📡 来源 | 12:00 | AI盘中诊断"
        out = normalize(card)
        assert any(e.get("tag") == "note" for e in out["card"]["elements"])


# ────────────────────────── validate_schema ──────────────────────────

class TestSchema:
    def test_real_products_pass(self, real_products):
        """真实产物必须全过 schema（防误伤）。"""
        assert real_products, "未找到真实产物 fixture"
        for name, data in real_products.items():
            p = {"latest": "latest.json", "fb": "feedback_matrix.json",
                 "candidates": "candidates-2026-08-14.json",
                 "auction": "auction_quotes-2026-08-17.json"}[name]
            tmp = ROOT / "cache" / f"_schema_test_{name}.json"
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            try:
                ok, errs = vs.validate_one(tmp)
                assert ok, f"{name} schema 应通过: {errs}"
            finally:
                tmp.unlink(missing_ok=True)

    def test_missing_field_detected(self):
        """缺必填字段 → 报错。文件名需命中契约（latest.json）。"""
        tmp = Path("/tmp/latest.json")
        tmp.write_text(json.dumps({"total_zt": 88}, ensure_ascii=False), encoding="utf-8")
        try:
            ok, errs = vs.validate_one(tmp)
            assert not ok
            assert any("ts" in e or "sentiment" in e for e in errs)
        finally:
            tmp.unlink(missing_ok=True)

    def test_invalid_json_detected(self):
        tmp = ROOT / "cache" / "_schema_test_broken.json"
        tmp.write_text("{bad json", encoding="utf-8")
        try:
            ok, errs = vs.validate_one(tmp)
            assert not ok
            assert any("解析失败" in e for e in errs)
        finally:
            tmp.unlink(missing_ok=True)


# ──────────────────── P1-⑦ mtime 缓存 ────────────────────

def test_mtime_cache_hit(tmp_path):
    """输出比输入新 → 缓存命中（不重算）。"""
    import sys as _s
    _s.path.insert(0, str(ROOT / "common"))
    from mtime_cache import needs_recompute, skip_msg
    src = tmp_path / "in.json"; out = tmp_path / "out.json"
    src.write_text("{}"); out.write_text("{}")
    assert needs_recompute([src], [out]) is False
    assert "缓存命中" in skip_msg("test")


def test_mtime_cache_input_newer(tmp_path):
    """输入比输出新 → 需重算。"""
    import sys as _s
    _s.path.insert(0, str(ROOT / "common"))
    from mtime_cache import needs_recompute
    src = tmp_path / "in.json"; out = tmp_path / "out.json"
    out.write_text("{}")
    import time
    time.sleep(0.05)
    src.write_text("{}")
    assert needs_recompute([src], [out]) is True


def test_mtime_cache_missing_output(tmp_path):
    """输出缺失 → 需重算。"""
    import sys as _s
    _s.path.insert(0, str(ROOT / "common"))
    from mtime_cache import needs_recompute
    src = tmp_path / "in.json"; src.write_text("{}")
    assert needs_recompute([src], [tmp_path / "nope.json"]) is True


def test_mtime_cache_force(tmp_path):
    """force=True → 恒重算。"""
    import sys as _s
    _s.path.insert(0, str(ROOT / "common"))
    from mtime_cache import needs_recompute
    src = tmp_path / "in.json"; out = tmp_path / "out.json"
    src.write_text("{}"); out.write_text("{}")
    assert needs_recompute([src], [out], force=True) is True


def test_validate_config_ok():
    """配置完整性校验：settings 键齐全（.env 存在性按环境容忍）。"""
    import validate_config as vc
    res = vc.validate(verbose=False)
    # settings.json 的 webhook 键必须全在（errors 为空即通过）
    assert not res["errors"]


# ──────────────────── 打分系统重构（2026-08-17 用户定）────────────────────

def _load_fb_score():
    """加载 feedback_matrix.sentiment_score（脚本模块，无副作用）。"""
    import sys as _s
    import importlib.util as _ilu
    _s.path.insert(0, str(ROOT / "scripts"))
    spec = _ilu.spec_from_file_location("fb_score", str(ROOT / "scripts" / "feedback_matrix.py"))
    m = _ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.sentiment_score


def test_sentiment_score_strong_market():
    """强普涨行情（08-17 实况：涨停63→110、三指数+1.4~3.1%、核按钮2）→ ≥9 亢奋。"""
    fn = _load_fb_score()
    s, l = fn(promote=0.238, lb_promote=0.455, avg_pct=3.57, nuke=2, broken_rate=0.106,
              max_trend=0, zt_expand=round(110 / 63 - 1, 2), up_rate=64.5,
              idx_avg=round((1.41 + 2.44 + 3.14) / 3, 2), break_count=0)
    assert s >= 9.0 and l == "亢奋", f"强普涨应≥9分, 实得 {s}/{l}"


def test_sentiment_score_crash_market():
    """冰点退潮（涨停腰斩+广度崩+指数大跌+核按钮爆）→ ≤2.5 退潮。"""
    fn = _load_fb_score()
    s, l = fn(promote=0.12, lb_promote=0.20, avg_pct=-4.2, nuke=8, broken_rate=0.55,
              max_trend=-1, zt_expand=-0.40, up_rate=28, idx_avg=-2.8, break_count=4)
    assert s <= 2.5 and l == "退潮", f"冰点应≤2.5分, 实得 {s}/{l}"


def test_sentiment_score_zt_expansion_not_penalized():
    """晋级率<25% 不再一票扣2（涨停基数膨胀稀释保护）：涨停翻倍+晋级率23% 不判退潮。"""
    fn = _load_fb_score()
    s, l = fn(promote=0.23, lb_promote=0.45, avg_pct=3.0, nuke=2, broken_rate=0.12,
              max_trend=0, zt_expand=0.75, up_rate=62, idx_avg=2.3, break_count=0)
    assert s >= 8.0, f"涨停翻倍行情晋级率低不构成退潮, 实得 {s}"


def test_sentiment_score_breadth_penalizes():
    """P0-量能广度（2026-08-17）：全市场普跌（400:900）广度修正应拉低情绪分（放量也救不回）。"""
    fn = _load_fb_score()
    base = dict(promote=0.238, lb_promote=0.455, avg_pct=3.57, nuke=2, broken_rate=0.106,
                max_trend=1, zt_expand=0.75, up_rate=64.5, idx_avg=2.33, break_count=0)
    # 放量+普涨 vs 放量+普跌：普跌广度必须显著更低
    s_up, _ = fn(market_amount_yi=30190.8, breadth_up=1777, breadth_down=491, **base)
    s_down, l_down = fn(market_amount_yi=20000, breadth_up=400, breadth_down=900, **base)
    assert s_up - s_down >= 0.8, f"普涨普跌区分度不足: {s_up} vs {s_down}"
    assert l_down == "偏强", f"放量但普跌不应亢奋, 实得 {l_down}"


def test_sentiment_score_amount_differentiates():
    """P0-量能广度（2026-08-17）：同基础下放量 vs 缩量应有可感知区分（≥0.3 分）。"""
    fn = _load_fb_score()
    base = dict(promote=0.238, lb_promote=0.455, avg_pct=3.57, nuke=2, broken_rate=0.106,
                max_trend=1, zt_expand=0.75, up_rate=64.5, idx_avg=2.33, break_count=0)
    s_hi, _ = fn(market_amount_yi=30190.8, breadth_up=1777, breadth_down=491, **base)
    s_lo, _ = fn(market_amount_yi=11000, breadth_up=1777, breadth_down=491, **base)
    assert s_hi - s_lo >= 0.3, f"放量缩量区分度不足: {s_hi} vs {s_lo}"


def test_expectation_range_width_capped():
    """区间宽度钳制（2026-08-17 用户「敞口过大·压缩至1~2个点」）：
    无论相位/形态/溢价怎么叠加，expected_range 宽度 ≤ max_range_width(2.0)，
    且 hi 不超过涨停阈值（主板 9.9 / 20cm 19.9）。"""
    import sys, json, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wealth_code"))
    from core.expectation_ai import build_ai_expectation
    rules = json.load(open(os.path.join(os.path.dirname(__file__), "..",
                                         "wealth_code", "config", "expectation_rules.json")))
    max_w = float(rules.get("max_range_width") or 2.0)
    # 亢奋相位 + 真龙头容量溢价 的极端叠加样本（最容易超宽）
    cand = {
        "code": "603186", "mc": "华正新材", "boards": 2, "theme": "元件",
        "cje_yi": 20.0, "is_20cm": False, "is_sector_dragon": True,
        "is_capacity_core": True, "cap_score": 85,
        "zbc": 0, "hs": 10.0, "fbt": "0930",
    }
    env = {"regime": "risk_on", "sentiment_score": 9.0, "sentiment_label": "亢奋",
           "promote_rate": 0.4, "nuke_count": 1, "broken_rate": 0.1,
           "up_rate": 70, "avg_pct": 3.5, "em_expect": []}
    exp = build_ai_expectation(cand, env, None)
    rng = exp.get("expected_range") or []
    assert len(rng) == 2, f"应产出区间, 实得 {rng}"
    w = rng[1] - rng[0]
    assert w <= max_w + 0.01, f"区间宽度超限: {rng} 宽{w} > {max_w}"
    assert rng[1] <= 9.9, f"10cm 预期上沿超涨停阈值: {rng}"
    # 20cm 样本
    cand20 = dict(cand); cand20["code"] = "300570"; cand20["is_20cm"] = True
    exp20 = build_ai_expectation(cand20, env, None)
    rng20 = exp20.get("expected_range") or []
    assert rng20[1] <= 19.9, f"20cm 预期上沿超涨停阈值: {rng20}"
    assert rng20[1] - rng20[0] <= max_w + 0.01, f"20cm 宽度超限: {rng20}"


def test_signal_above_range_is_super_not_excluded():
    """用户铁律（2026-08-17）：竞价涨幅**大于预期区间上沿**的一律判超预期 buy，
    不得反被排除（reject）。区间收窄后贴上限宽容同步收窄（旧固定0.5pp 会把真超预期
    误标为符合预期·2026-08-17 修正为区间宽10%且≤0.3pp）。"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wealth_code"))
    from core.signal_eval import evaluate_realtime_signal
    cand = {
        "code": "600353", "name": "旭光电子",
        "expectation": {"expected_range": [1.5, 3.5], "super_gap_min": 4.5,
                        "auction_amount_min_yi": 0.5, "auction_amount_super_yi": 1.0,
                        "expected_text": "符合预期", "super_text": "超预期", "low_text": "低于预期"},
    }
    q0 = {"prev_close": 10.0}
    # ① 大于上沿·量足（+4.0%）→ super buy
    r1 = evaluate_realtime_signal(cand, dict(q0, auction_price=10.40, auction_amount_yi=0.6))
    assert r1["decision_status"] == "buy" and r1["signal_status"] == "super", f"大于上沿量足应超预期buy: {r1['decision_status']}/{r1['signal_status']}"
    # ② 大于上沿·量不足（+4.0%）→ super buy（量弱不阻断·用户「不要大于之后被排除」）
    r2 = evaluate_realtime_signal(cand, dict(q0, auction_price=10.40, auction_amount_yi=0.3))
    assert r2["decision_status"] == "buy" and r2["signal_status"] == "super", f"大于上沿量弱应超预期buy: {r2['decision_status']}/{r2['signal_status']}"
    # ③ 达超线·量不足（+5.0%）→ super buy
    r3 = evaluate_realtime_signal(cand, dict(q0, auction_price=10.50, auction_amount_yi=0.3))
    assert r3["decision_status"] == "buy" and r3["signal_status"] == "super", f"达超线量弱应超预期buy: {r3['decision_status']}/{r3['signal_status']}"
    # ④ 超宽容段（+3.8% > 上沿+0.2pp）→ super 而非 expected（宽容收窄生效）
    r4 = evaluate_realtime_signal(cand, dict(q0, auction_price=10.38, auction_amount_yi=0.6))
    assert r4["signal_status"] == "super", f"超宽容段应标超预期: {r4['signal_status']}"
    # ⑤ 落区间内（+3.0%）→ expected buy（不受影响）
    r5 = evaluate_realtime_signal(cand, dict(q0, auction_price=10.30, auction_amount_yi=0.6))
    assert r5["decision_status"] == "buy" and r5["signal_status"] == "expected", f"落区间应符合预期: {r5['signal_status']}"


def test_verify_card_theme_dual_tag():
    """verify 卡开仓决策显示题材双字段（2026-08-17 用户）：records 透传 core_theme/industry_sector，
    _theme_dual_tag 渲染「🔥 炒作题材｜🏷 行业」（题材前置·不带行业/炒作字样·无热度）。"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wealth_code"))
    from pipelines.build_cards import build_verify_card, _theme_dual_tag
    # ① 双字段透传渲染
    r = {"name": "黄河旋风", "code": "600172", "bucket": "relay", "bucket_label": "接力",
         "industry_sector": "通用设备", "core_theme": "金刚石/培育钻石",
         "expectation": {"expected_range": [1.5, 3.5]},
         "performance": {"open_check": {"can_enter": True, "decision_status": "buy",
                                        "signal_status": "super", "signal_label": "超预期",
                                        "gap_pct": 4.0, "auction_amount_yi": 1.5,
                                        "auction_amount_need_yi": 1.0, "decision_label": "直接买入"}}}
    tag = _theme_dual_tag(r)
    assert "金刚石" in tag and "通用设备" in tag, f"题材双标签缺失: {tag}"
    assert "行业" not in tag and "炒作" not in tag and "H热" not in tag, f"不应含字样/热度: {tag}"
    # 题材在行业前面
    assert tag.index("金刚石") < tag.index("通用设备"), f"题材应前置: {tag}"
    # ② verify 卡整体渲染不崩 + 含双标签
    vr = {"payload": {"records": [r], "environment": {"sentiment_score": 9.0}},
          "summary": {"total_candidates": 1, "buy_signals": 1, "covered": 0, "nav_latest": 0.98}}
    card = build_verify_card(vr)
    joined = json.dumps(card, ensure_ascii=False)
    assert "金刚石/培育钻石" in joined and "通用设备" in joined, "verify 卡未渲染题材双字段"


def test_ice_ban_hit_not_buy():
    """P1-6 冰点禁买·命中照算（2026-08-19 用户澄清）：regime=ice 时竞价达标照常判 buy/super，
    由 pipeline_verify._build_record 打标 hit（can_enter=False 不进场不写持仓），
    卡片仍展示命中票；非 ice 不受影响。"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wealth_code"))
    from core.signal_eval import evaluate_realtime_signal
    from pipelines.pipeline_verify import _build_record

    cand = {
        "code": "600353", "name": "旭光电子", "date10": "2026-08-19",
        "expectation": {"expected_range": [1.5, 3.5], "super_gap_min": 4.5,
                        "auction_amount_min_yi": 0.5, "auction_amount_super_yi": 1.0,
                        "expected_text": "符合预期", "super_text": "超预期", "low_text": "低于预期"},
    }
    q = {"prev_close": 10.0, "auction_price": 10.40, "auction_amount_yi": 0.6, "time": "09:25"}
    # ① signal_eval 层：ice 不提前拦截，正常判 super buy
    sig = evaluate_realtime_signal(cand, q, regime="ice")
    assert sig["decision_status"] == "buy" and sig["signal_status"] == "super", \
        f"ice 命中判定应照算 buy/super: {sig['decision_status']}/{sig['signal_status']}"
    # ② _build_record 层：ice → hit（can_enter=False），档位 super 保留
    rec = _build_record(cand, q, None, date10="2026-08-19", regime="ice")
    oc = rec["performance"]["open_check"]
    assert oc["decision_status"] == "hit" and oc["can_enter"] is False, \
        f"ice 应打标 hit 且不进场: {oc['decision_status']}/{oc['can_enter']}"
    assert oc["status"] == "super", f"命中档位应保留 super: {oc['status']}"
    assert oc["note"] and "冰点禁买" in oc["note"], "note 应含冰点禁买标注"
    # ③ 非 ice（risk_off）不受影响：AI 口径下「AI 说买 + 竞价落预期区间」→ 可进场。
    # ⚠️ 2026-09-29 修断言（门禁漂移，非代码缺陷）：原断言用 `ai_decisions=None`（不传）
    #    期望 buy/can_enter=True。但 2026-09-14 用户拍板「全量以 AI 为唯一口径！」后，
    #    `_build_record` 在 `ai_caliber.AI_CALIBER_ACCOUNT` 下遇 `ai_decisions is None`
    #    即判 `ai_missing_no_trade` → can_enter=False（**账户不交易**，机械结果仅供展示/审计）。
    #    ⇒「该执行日没有 AI 决策行就不交易」是**现行正确行为**，测试断言过时。
    #    改法：显式给一条 focus（=participate）决策行，并把竞价挪进预期区间 [1.5,3.5]。
    _ai = {"600353": {"code": "600353", "bias": "focus", "entry_max_gap": 5.0}}
    q3 = {"prev_close": 10.0, "auction_price": 10.20, "auction_amount_yi": 0.6, "time": "09:25"}  # +2.0%
    rec2 = _build_record(cand, q3, None, date10="2026-08-19", regime="risk_off", ai_decisions=_ai)
    oc2 = rec2["performance"]["open_check"]
    assert oc2["decision_status"] == "buy" and oc2["can_enter"] is True, \
        f"risk_off + AI 说买 + 竞价达标 → 应可进场: {oc2['decision_status']}/{oc2['can_enter']}"
    # ③-b 同一组输入换 ice：冰点硬闸优先于 AI 口径，照样不许进场（但保留 AI 判买留痕）
    rec_ice = _build_record(cand, q3, None, date10="2026-08-19", regime="ice", ai_decisions=_ai)
    oc_ice = rec_ice["performance"]["open_check"]
    assert oc_ice["can_enter"] is False and oc_ice["ice_ban"] is True, \
        f"ice 硬闸应拦下 AI 的买入: can_enter={oc_ice['can_enter']}/ice_ban={oc_ice['ice_ban']}"
    # ③-c 无 AI 决策行（口径缺失）→ 账户不交易（诚实降级，不是静默空仓）
    rec_na = _build_record(cand, q3, None, date10="2026-08-19", regime="risk_off")
    oc_na = rec_na["performance"]["open_check"]
    assert oc_na["can_enter"] is False and oc_na["caliber"] == "ai_missing_no_trade", \
        f"缺 AI 决策应不交易且留痕: {oc_na['can_enter']}/{oc_na['caliber']}"
    # ④ verify 卡渲染：ice 场景展示命中票 + 禁买标注，标题含"冰点禁买"
    from pipelines.build_cards import build_verify_card
    vr = {
        "payload": {"records": [rec], "environment": {"regime": "ice", "sentiment_score": 1.0}},
        "summary": {"total_candidates": 1, "buy_signals": 0, "hit_signals": 1,
                    "covered": 0, "nav_latest": 0.96},
    }
    card = build_verify_card(vr)
    joined = json.dumps(card, ensure_ascii=False)
    assert "市场很冷" in joined and "不买" in joined, "verify 卡应展示「市场很冷·不买」（2026-09-10 白话化）"
    assert "旭光电子" in joined, "命中票应展示"
    assert "✅ 达标买入信号" not in joined, "冰点期不应出现达标买入信号块"


def test_sentiment_regulatory_pressure_factor():
    """P1-8 监管打击因子（2026-08-19 用户定）：regulatory_pressure 只扣不奖——
    监管红线触发（严重异常波动被点名/停牌）压制情绪分；无监管事件（0）不影响打分。"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from scripts.feedback_matrix import sentiment_score
    base = dict(promote=0.5, lb_promote=0.6, avg_pct=3.5, nuke=1, broken_rate=0.1,
                max_trend=1, zt_expand=0.6, up_rate=70, idx_avg=2.5, break_count=0,
                market_amount_yi=26000, breadth_up=3000, breadth_down=1000)
    s0, _ = sentiment_score(**base)
    s1, _ = sentiment_score(**base, regulatory_pressure=2.5)
    s2, _ = sentiment_score(**base, regulatory_pressure=7.0)
    assert s0 >= s1 > s2, f"监管压力应压制情绪分: 无={s0} 压力2.5={s1} 压力7.0={s2}"
    # 无监管事件不改变原分（修正系数=1.0）
    s3, _ = sentiment_score(**base, regulatory_pressure=0.0)
    assert s3 == s0, f"监管压力=0 不应改变分数: {s3} vs {s0}"


# ============================================================================
# 板块竞价承接力（2026-08-24 用户「孤军高开·板块无承接被砸」思想·金健米业实证）
# ============================================================================

def _lone_cand():
    """金健米业式孤军候选：8/20 4板·炸板9次·同板块唯一涨停。"""
    return {
        "code": "600127", "name": "金健米业", "date10": "2026-08-20", "boards": 4,
        "zbc": 9, "hs": 43.4, "cje_yi": 23.57, "sector": "农产品加", "theme": "农产品加",
        "sector_is_lone": True, "sector_mates": [], "bucket_label": "观察",
        "expectation": {"expected_range": [0.0, 0.5], "super_gap_min": 2.5,
                        "auction_amount_min_yi": 0.94, "auction_amount_super_yi": 1.89,
                        "expected_text": "预期0~0.5%", "super_text": "≥2.5%超预期", "low_text": "未达标"},
    }


def test_lone_high_open_blocked():
    """孤军高开 + 板块0红开 → buy 降级 pending「防被砸」（金健米业 8/21 实证：
    竞价+9.19% 板块零助攻 → 收-4.65%，振幅15点；旧逻辑 3~5% 高开区间会直接 buy）。"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wealth_code"))
    from core.sector_support import compute_support
    from core.signal_eval import evaluate_realtime_signal
    cand = _lone_cand()
    support = compute_support(cand, {})  # 孤军 0 报价
    assert support["support_level"] == "lone_no_support"
    # ① 极端高开 +9.19%（金健米业真实竞价）
    q = {"prev_close": 8.60, "auction_price": 9.39, "auction_amount_yi": 3.0, "time": "09:25:00"}
    r = evaluate_realtime_signal(cand, q, sector_support=support)
    assert r["decision_status"] == "pending" and "防被砸" in r["signal_label"], r
    # ② 3~5% 高开（旧盲区：量足会直接 buy）→ 必须拦截
    q2 = {"prev_close": 8.60, "auction_price": 8.95, "auction_amount_yi": 2.0, "time": "09:25:00"}  # +4.07%
    r2 = evaluate_realtime_signal(cand, q2, sector_support=support)
    assert r2["decision_status"] == "pending" and "防被砸" in r2["signal_label"], r2
    # ③ 板块有红开助攻 → 保持 buy + 标注（不误伤真起飞）
    cand3 = dict(cand); cand3["sector_mates"] = ["600598"]
    sup3 = compute_support(cand3, {"600598": {"prev_close": 10.0, "auction_price": 10.30,
                                              "auction_amount_yi": 0.5, "time": "09:25:00"}})
    assert sup3["support_level"] == "support_ok"
    r3 = evaluate_realtime_signal(cand, q2, sector_support=sup3)
    assert r3["decision_status"] == "buy" and "板块助攻" in r3["note"], r3


def test_lone_low_open_not_triggered():
    """孤军但未高开（gap<3%）→ 不触发防被砸门（低开/平开无被砸逻辑）。"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wealth_code"))
    from core.sector_support import compute_support
    from core.signal_eval import evaluate_realtime_signal
    cand = _lone_cand()
    support = compute_support(cand, {})
    q = {"prev_close": 8.60, "auction_price": 8.75, "auction_amount_yi": 1.5, "time": "09:25:00"}  # +1.74%
    r = evaluate_realtime_signal(cand, q, sector_support=support)
    assert "防被砸" not in r["signal_label"], r


def test_no_sector_data_no_regression():
    """无板块承接数据（老候选文件/抓取失败）→ 保持原行为不误伤（诚实降级，不裁决）。"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wealth_code"))
    from core.signal_eval import evaluate_realtime_signal
    cand = _lone_cand()
    q = {"prev_close": 8.60, "auction_price": 8.95, "auction_amount_yi": 2.0, "time": "09:25:00"}
    r = evaluate_realtime_signal(cand, q, sector_support=None)
    assert r["decision_status"] == "buy", f"无板块数据应保持旧行为: {r['decision_status']}"


def test_annotate_lone_and_verify_card_tag():
    """① annotate_lone 正确标孤军/成员；② verify 卡渲染 🧲 标注（孤军红标/助攻紫标）。"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wealth_code"))
    from core.sector_support import annotate_lone
    from pipelines.build_cards import build_verify_card
    cands = [
        {"code": "600127", "name": "金健米业", "sector": "农产品加"},
        {"code": "600598", "name": "北大荒", "sector": "农产品加"},
        {"code": "000001", "name": "平安银行", "sector": "银行"},
    ]
    out = annotate_lone(cands)
    assert out[0]["sector_is_lone"] is False and out[0]["sector_mates"] == ["600598"]
    assert out[2]["sector_is_lone"] is True and out[2]["sector_mates"] == []
    # verify 卡渲染 🧲
    r = {"name": "金健米业", "code": "600127", "bucket": "relay", "bucket_label": "接力",
         "industry_sector": "农产品加", "core_theme": "粮食",
         "expectation": {"expected_range": [0.0, 0.5]},
         "sector_support": {"support_level": "lone_no_support", "red_open": 0, "label": "🧲孤军·板块0红开"},
         "performance": {"open_check": {"can_enter": False, "decision_status": "pending",
                                        "signal_status": "pending", "signal_label": "孤军高开·防被砸",
                                        "gap_pct": 9.19, "auction_amount_yi": 3.0,
                                        "auction_amount_need_yi": 0.94, "decision_label": "观察"}}}
    card = build_verify_card({"payload": {"records": [r], "environment": {"sentiment_score": 5.0}},
                              "summary": {"total_candidates": 1, "buy_signals": 0, "covered": 0,
                                          "nav_latest": 1.0}})
    joined = json.dumps(card, ensure_ascii=False)
    assert "孤军" in joined and "防被砸" in joined, "verify 卡应渲染孤军警示"


# ────────────────── 短线精灵配色铁律（2026-08-25 用户抓包「又忘了红涨绿跌」） ──────────────────

def test_short_spirit_color_rules():
    """短线精灵 build_card 配色铁律：禁 <font> / 禁 turquoise 给上涨 / 涨幅红涨绿跌 / 个股名类型色。
    回归锚：2026-08-25 用户抓包——4 只上涨票全部渲染 turquoise（退潮色=视觉"涨显绿"）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("short_spirit", str(ROOT / "scripts" / "short_spirit.py"))
    ss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ss)
    signals = [
        {"name": "英维克", "code": "002837", "pct": 3.58, "sector": "专用设备",
         "reason": "共振", "type": "快速拉升", "strength": 0.5},
        {"name": "深中华A", "code": "000017", "pct": 9.97, "sector": "黄金",
         "reason": "冲板", "type": "接近涨停", "strength": 0.9},
        {"name": "汉森制药", "code": "002412", "pct": 10.01, "sector": "中药",
         "reason": "涨停", "type": "涨停", "strength": 1.0},
        {"name": "测试跌票", "code": "600000", "pct": -1.23, "sector": "测试",
         "reason": "负涨", "type": "快速拉升", "strength": 0.1},
    ]
    card = ss.build_card(signals, "morning", __import__("datetime").datetime(2026, 8, 25, 9, 35))
    s = json.dumps(card, ensure_ascii=False)
    # 1) 禁 <font>（飞书不渲染·shared_rules 实测）
    assert "<font" not in s
    # 2) 禁 turquoise 出现在上涨语义（退潮/防守专用色）
    assert "turquoise" not in s
    # 3) 涨幅红涨绿跌
    assert "<text_tag color='red'>+9.97%</text_tag>" in s
    assert "<text_tag color='red'>+10.01%</text_tag>" in s
    assert "<text_tag color='green'>-1.23%</text_tag>" in s
    # 4) 个股名与涨幅同色号（2026-08-25 用户「红色颜色不对·统一色号」）：涨停/拉升=red（与涨幅一致）、冲板=purple
    assert "<text_tag color='red'>汉森制药(002412)</text_tag>" in s
    assert "<text_tag color='purple'>深中华A(000017)</text_tag>" in s
    assert "<text_tag color='red'>英维克(002837)</text_tag>" in s
    assert "orange" not in s  # 快速拉升禁 orange（渲染偏橘红≠涨幅正红）


# ────────────────── 板块加强（2026-08-25 用户「早盘爆发+盘中续聚=叠加加强」） ──────────────────

def test_sector_boost_semantics(monkeypatch, tmp_path):
    """个股归集命中当日 sector_pushed（早盘已板块级爆发）→ 升级「板块加强」，替代「非板块爆发」。
    断层背景：早盘医药板块级爆发(9家涨停)推过板块强势卡(sector_pushed 记录)，但窗口聚合层不知情，
    盘中个股续聚被误标「无板块爆发·个股归集（非板块爆发）」→ 用户要求叠加为「医药加强」。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pulse_aggregator", str(ROOT / "scripts" / "pulse_aggregator.py"))
    pa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pa)

    # 1) is_boost 纯函数：命中/粒度差异/未爆发/空值
    sp = ["大农业", "光通信", "机器人", "医药", "大消费"]
    assert pa.is_boost("医药", sp) is True
    assert pa.is_boost("液冷服务器", sp) is False       # 今日未板块级爆发
    assert pa.is_boost("—", sp) is False
    assert pa.is_boost("", sp) is False

    # 2) 构造 10:15 窗口真实场景（医药个股归集·focus 卡）→ 渲染「板块加强」
    #    注入空 latest（无资金榜）→ 不显示资金标注；隔离 boost_state 防跨用例污染
    monkeypatch.setattr(pa, "load_sector_pushed", lambda: sp)
    monkeypatch.setattr(pa, "_load_latest", lambda: {"top5": [], "sector_risk": {}})
    monkeypatch.setattr(pa, "_load_boost_state", lambda: {"date": "2026-08-25", "sectors": {}})
    monkeypatch.setattr(pa, "_save_boost_state", lambda s: None)
    summary = {
        "zt": [{"type": "zt", "code": "002437", "name": "誉衡药业", "sector": "化学制药", "pct": 9.9, "boards": ""},
               {"type": "zt", "code": "000070", "name": "特发信息", "sector": "光通信", "pct": 9.99, "boards": ""}],
        "strong": [{"type": "strong", "code": "301218", "name": "华是科技", "sector": "航天", "pct": 11.41, "is_20cm": True}],
        "charge": [{"type": "charge", "code": "301211", "name": "亨迪药业", "sector": "医药", "pct": 15.49}],
        "surge": [], "nf": [], "sector": [], "sectors": [], "new_sectors": [],
        "zt_total": 2, "main_line": "医药",
    }
    card = pa.build_window_card(summary, "20260825_1015", {}, "10:20", focus=True)
    s = json.dumps(card, ensure_ascii=False)
    assert "板块加强【医药】" in s, "命中已爆发板块应渲染板块加强"
    assert "非板块爆发" not in s, "板块加强时禁再标「非板块爆发」"
    assert "·板块加强【医药】" in card.get("title", ""), "标题应带板块加强"
    assert "第1次" in s, "首次加强应标注第1次"

    # 3) 未爆发板块（如液冷服务器）→ 维持「个股归集·非板块爆发」不回归
    summary2 = dict(summary, main_line="液冷服务器")
    card2 = pa.build_window_card(summary2, "20260825_1000", {}, "10:05", focus=True)
    s2 = json.dumps(card2, ensure_ascii=False)
    assert "个股归集【液冷服务器】" in s2 and "板块加强" not in s2, "未爆发板块维持个股归集口径"


def test_sector_boost_flow_and_repeat(monkeypatch):
    """板块加强二迭代（2026-08-25）：①资金验证（主力流入=真加强/流出=资金背离/无榜不标）
    ②首/续区分（首次=板块加强·第N次，持续=板块持续·防5min刷屏）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pulse_aggregator", str(ROOT / "scripts" / "pulse_aggregator.py"))
    pa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pa)

    sp = ["医药"]
    monkeypatch.setattr(pa, "load_sector_pushed", lambda: sp)
    summary = {
        "zt": [{"type": "zt", "code": "002437", "name": "誉衡药业", "sector": "化学制药", "pct": 9.9, "boards": ""}],
        "strong": [], "charge": [], "surge": [], "nf": [], "sector": [],
        "sectors": [], "new_sectors": [], "zt_total": 1, "main_line": "医药",
    }
    # ① 资金验证：流入 → 💰主力净流入；流出 → ⚠️资金背离；无榜 → 不标注
    monkeypatch.setattr(pa, "_load_latest", lambda: {
        "top5": [{"name": "医药", "zt_count": 9}],
        "sector_risk": {"inflow_top": [{"name": "医药", "flow_yi": 2.3}], "outflow_top": []}})
    monkeypatch.setattr(pa, "_load_boost_state", lambda: {"date": "2026-08-25", "sectors": {}})
    monkeypatch.setattr(pa, "_save_boost_state", lambda s: None)
    c_in = pa.build_window_card(summary, "20260825_1015", {}, "10:20", focus=True)
    assert "💰主力净流入 +2.3亿" in json.dumps(c_in, ensure_ascii=False), "流入应标真加强"

    monkeypatch.setattr(pa, "_load_latest", lambda: {
        "top5": [{"name": "医药", "zt_count": 9}],
        "sector_risk": {"inflow_top": [], "outflow_top": [{"name": "医药", "flow_yi": 3.7}]}})
    c_out = pa.build_window_card(summary, "20260825_1015", {}, "10:20", focus=True)
    assert "⚠️资金背离·主力净流出 -3.7亿" in json.dumps(c_out, ensure_ascii=False), "流出应标资金背离"

    monkeypatch.setattr(pa, "_load_latest", lambda: {"top5": [{"name": "医药", "zt_count": 9}], "sector_risk": {}})
    c_none = pa.build_window_card(summary, "20260825_1015", {}, "10:20", focus=True)
    assert "主力净" not in json.dumps(c_none, ensure_ascii=False), "无资金榜不编造标注"

    # ② 首/续区分：注入状态计数=2（续加强）→ 标题「板块持续」，不刷「板块加强」
    monkeypatch.setattr(pa, "_load_boost_state", lambda: {"date": "2026-08-25",
                                                          "sectors": {"医药": {"count": 2, "peak_zt": 9}}})
    c_repeat = pa.build_window_card(summary, "20260825_1020", {}, "10:25", focus=True)
    s2 = json.dumps(c_repeat, ensure_ascii=False)
    assert "·板块持续【医药】" in c_repeat.get("title", ""), "续加强标题应降为板块持续"
    assert "第3次" in s2, "续加强应累计次数"


# ────────────────── content 数组防线（2026-08-25 事故：猎手卡 200621 parse card json err） ──────────────────

def test_lint_rejects_content_array():
    """lint 必须拦截 content 数组（飞书 200621：'with value array at index 5943: mismatched type'）。
    事故现场：13:00 猎手卡 AI 拼卡把多行 content 写成数组 → lint 未查类型漏网 → 飞书解析失败。"""
    from card_lint import lint_card
    bad = {"title": "📊 盘中猎手·增量 | 13:00 | 测试", "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content": ["📡 选股宝", "第二行"]}}]}
    ok, errs, warns = lint_card(bad)
    assert not ok, "content 数组必须被拦截"
    assert any("content 应为字符串" in e["msg"] for e in errs), "错误信息应指明 content 类型"
    # 正常字符串 content 不误伤
    good = {"title": "📊 盘中猎手·增量 | 13:00 | 测试", "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content": "📡 选股宝\n第二行"}}]}
    ok2, e2, w2 = lint_card(good)
    assert ok2, "字符串 content 不应误伤"


def test_push_fixes_content_array():
    """push 兜底：content 数组 join 成 \\n 字符串（--skip-lint 时的最后防线·三层：正文/note/fields深层）。"""
    from push_card import normalize, _fix_content_array
    # 正文 div
    bad = {"title": "📊 盘中猎手·增量 | 13:00 | 测试", "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content": ["📡 选股宝", "第二行"]}}]}
    fixed = _fix_content_array(normalize(bad))
    assert fixed["card"]["elements"][0]["text"]["content"] == "📡 选股宝\n第二行"
    # note 数组
    bad2 = {"title": "📊 盘中猎手·增量 | 13:00 | 测试", "elements": [], "note": ["脚注1", "脚注2"]}
    f2 = _fix_content_array(normalize(bad2))
    assert f2["card"]["elements"][-1]["elements"][0]["content"] == "脚注1\n脚注2"
    # fields 深层
    bad3 = {"title": "📊 盘中猎手·增量 | 13:00 | 测试", "elements": [
        {"tag": "div", "fields": [{"is_short": True, "text": {"tag": "lark_md", "content": ["流入", "+2.3亿"]}}]}]}
    f3 = _fix_content_array(normalize(bad3))
    assert f3["card"]["elements"][0]["fields"][0]["text"]["content"] == "流入\n+2.3亿"


# ────────────────── 题材板块效应（2026-08-25 用户「孤军判定要全局化·医药还有其它涨停」） ──────────────────

def test_lone_theme_sector_effect():
    """行业孤军但题材层有板块效应 → 不判孤军（2026-08-25 用户抓包：凯莱英 医疗服务行业孤军、
    医药题材8家涨停被误判孤军；汉森 中药Ⅱ 行业2家+题材医药8家=有板块效应）。
    全局化眼光：孤军判定叠加题材层聚合（行业→题材归并 + core_theme 归一）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sector_support", str(ROOT / "wealth_code" / "core" / "sector_support.py"))
    ss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ss)

    # 完整涨停池（模拟医药系分散行业 + 大农业）
    pool = [
        {"dm": "600001", "mc": "卫光生物", "hy": "生物制品", "sector": "生物制品"},
        {"dm": "600002", "mc": "冀衡医药", "hy": "化学制药", "sector": "化学制药"},
        {"dm": "600003", "mc": "汉森制药", "hy": "中药Ⅱ", "sector": "中药Ⅱ"},
        {"dm": "600004", "mc": "誉衡药业", "hy": "化学制药", "sector": "化学制药"},
        {"dm": "600005", "mc": "西点药业", "hy": "医疗器械", "sector": "医疗器械"},
        {"dm": "600006", "mc": "凯莱英", "hy": "医疗服务", "sector": "医疗服务"},
        {"dm": "600007", "mc": "益诺思", "hy": "医药商业", "sector": "医药商业"},
        {"dm": "600008", "mc": "荣昌生物", "hy": "生物制品", "sector": "生物制品"},
        {"dm": "600009", "mc": "金健米业", "hy": "农产品加工", "sector": "农产品加工"},
        {"dm": "600010", "mc": "红四方", "hy": "农化制品", "sector": "农化制品"},
    ]
    cands = [
        {"dm": "600006", "mc": "凯莱英", "hy": "医疗服务", "sector": "医疗服务", "core_theme": "医药"},
        {"dm": "600003", "mc": "汉森制药", "hy": "中药Ⅱ", "sector": "中药Ⅱ", "core_theme": "中药"},
        {"dm": "600009", "mc": "金健米业", "hy": "农产品加工", "sector": "农产品加工", "core_theme": "粮食"},
    ]
    r = ss.annotate_lone(cands, pool=pool, em_sector_zt={})  # 空东财 → 走涨停池聚合估算
    by_name = {c["mc"]: c for c in r}
    # 凯莱英：行业孤军（医疗服务仅1家）但题材医药8家 → 非孤军 + 题材效应
    assert by_name["凯莱英"]["sector_mates_cnt"] == 1, "行业层医疗服务应仅1家"
    assert by_name["凯莱英"]["theme_name"] == "医药", "题材层应归并为医药"
    assert by_name["凯莱英"]["theme_mates_cnt"] == 8, "题材医药应有8家涨停"
    assert by_name["凯莱英"]["theme_has_sector_effect"] is True
    assert by_name["凯莱英"]["sector_is_lone"] is False, "题材有板块效应不得判孤军"
    # 汉森：中药Ⅱ 2家 + 题材医药8家（core_theme=中药 归一→医药）→ 非孤军
    assert by_name["汉森制药"]["sector_is_lone"] is False
    assert by_name["汉森制药"]["theme_name"] == "医药", "core_theme 中药应归一为医药"
    # 金健米业：农产品加工 1家 + 题材大农业 2家（金健+红四方）→ 非孤军（题材有梯队）
    assert by_name["金健米业"]["theme_name"] == "大农业", "粮食/农产品加工应归并为大农业"
    assert by_name["金健米业"]["theme_mates_cnt"] == 2
    assert by_name["金健米业"]["sector_is_lone"] is False, "大农业2家涨停=题材有板块效应"

    # 真孤军：行业+题材都只有自己 → 仍判孤军（承接力风险警示保留）
    pool2 = pool + [{"dm": "600099", "mc": "真孤军", "hy": "某细分", "sector": "某细分"}]
    c2 = [{"dm": "600099", "mc": "真孤军", "hy": "某细分", "sector": "某细分"}]
    r2 = ss.annotate_lone(c2, pool=pool2, em_sector_zt={})
    assert r2[0]["sector_is_lone"] is True, "行业+题材都孤军应保留孤军判定"


def test_lone_em_sector_calibration():
    """东财接口校准（2026-08-25 用户「题材归纳参考东财接口」）：传 em_sector_zt（东财 top5
    收盘快照·医药 zt=9）→ 题材涨停数用东财权威口径（≥聚合估算时取东财值），孤军判定同步。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sector_support", str(ROOT / "wealth_code" / "core" / "sector_support.py"))
    ss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ss)

    pool = [{"dm": "600001", "mc": "卫光生物", "hy": "生物制品", "sector": "生物制品"},
            {"dm": "600002", "mc": "冀衡医药", "hy": "化学制药", "sector": "化学制药"},
            {"dm": "600003", "mc": "汉森制药", "hy": "中药Ⅱ", "sector": "中药Ⅱ"}]
    cands = [{"dm": "600001", "mc": "卫光生物", "hy": "生物制品", "sector": "生物制品", "core_theme": "医药"}]
    em = {"医药": 9, "大消费": 6, "机器人": 4}  # 东财收盘快照：医药 9 家涨停
    r = ss.annotate_lone(cands, pool=pool, em_sector_zt=em)
    c = r[0]
    assert c["theme_name"] == "医药"
    assert c["theme_mates_cnt"] == 9, "东财口径医药 9 家应覆盖聚合估算 3 家"
    assert c["theme_em_name"] == "医药", "应记录匹配到的东财板块名"
    assert c["theme_has_sector_effect"] is True
    assert c["sector_is_lone"] is False, "东财口径医药 9 家涨停=强板块效应，不得判孤军"
    # 无东财数据 → 降级聚合估算（不编造）
    r2 = ss.annotate_lone(cands, pool=pool, em_sector_zt={})
    assert r2[0]["theme_mates_cnt"] == 3, "无东财数据时用涨停池聚合估算"


# ────────────────── 标题定调·止跌修正（2026-08-25 用户「情绪不算退潮·有止跌感觉」） ──────────────────

def test_regime_label_stabilize(monkeypatch):
    """risk_off 但跌不动了（情绪分≥3.5 + 溢价转正 + 核按钮≤10）→ 标题禁硬标「退潮」。
    推断多维核验铁律（2026-08-25 中枢审查）：止跌须趋势确认——当日快照满足 +
    downtrend=False + defense==0 → 「可能要转好」；defense≥1（趋势防守档）→ 「仍防守」
    （语义矛盾门禁：标题"止跌"不得与防守档裸并存）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("card_style", str(ROOT / "wealth_code" / "pipelines" / "card_style.py"))
    cs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cs)
    # 8/25 真实值：当日止跌信号但 defense=1（趋势防守档）→ 标「仍防守」
    monkeypatch.setattr(cs, "_load_emotion_signal", lambda: {
        "sentiment": 4.0, "avg_pct": 1.03, "nuke": 7,
        "lb_promote": 40.0, "downtrend": False, "defense": 1})
    assert cs._regime_label("risk_off") == "跌不动了·但还得防", "defense≥1 应标还得防（趋势约束优先·2026-09-10 白话化）"
    # 趋势确认（defense=0 + 无 downtrend）→ 「可能要转好」
    monkeypatch.setattr(cs, "_load_emotion_signal", lambda: {
        "sentiment": 4.0, "avg_pct": 1.03, "nuke": 7,
        "lb_promote": 40.0, "downtrend": False, "defense": 0})
    assert cs._regime_label("risk_off") == "跌不动了·可能要转好", "趋势确认才标可能要转好"
    # 当日信号满足但 downtrend=True → 仍防守（趋势走弱否决）
    monkeypatch.setattr(cs, "_load_emotion_signal", lambda: {
        "sentiment": 4.0, "avg_pct": 1.03, "nuke": 7,
        "lb_promote": 40.0, "downtrend": True, "defense": 0})
    assert cs._regime_label("risk_off") == "跌不动了·但还得防", "downtrend 否决可能要转好"
    # 真退潮（情绪分低 + 溢价负 + 核按钮多）→ 维持「退潮」
    monkeypatch.setattr(cs, "_load_emotion_signal", lambda: {
        "sentiment": 1.5, "avg_pct": -2.0, "nuke": 20,
        "lb_promote": 10.0, "downtrend": True, "defense": 2})
    assert cs._regime_label("risk_off") == "市场转冷·防守为主", "真退潮信号应维持退潮定调"
    # 无数据/非当日 → 向后兼容旧映射
    monkeypatch.setattr(cs, "_load_emotion_signal", lambda: {})
    assert cs._regime_label("risk_off") == "市场转冷·防守为主", "无数据维持旧行为"
    # 其他 regime 不受影响
    assert cs._regime_label("risk_on") == "市场转暖·可以积极"
    assert cs._regime_label("neutral") == "市场平稳·挑着做"


# ────────────────── P3 板块加强与次日竞价助攻池打通（2026-08-25 中枢审查） ──────────────────

def test_mates_theme_fallback(monkeypatch):
    """题材板块效应票（行业孤军·题材9家）次日竞价用题材成员算承接力——P3 打通。
    盘中「板块加强」=早盘板块级爆发+盘中续聚 → 题材成员进 theme_mates → verify 抓竞价
    算「🧲板块助攻N家红开」；真孤军（行业+题材都只有自己）维持无承接。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sector_support", str(ROOT / "wealth_code" / "core" / "sector_support.py"))
    ss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ss)
    # ① 题材板块效应票：sector_mates 空 → 回退 theme_mates 算承接
    cand = {"sector_is_lone": False, "sector_mates": [], "theme_mates": ["600001", "600002", "600003"],
            "theme_has_sector_effect": True, "theme_name": "医药", "theme_mates_cnt": 4}
    mq = {"600001": {"prev_close": 10, "auction_price": 10.5},
          "600002": {"prev_close": 10, "auction_price": 10.1},
          "600003": {"prev_close": 10, "auction_price": 9.9}}
    r = ss.compute_support(cand, mq)
    assert r["support_level"] == "support_ok", "题材成员有红开应判承接确认"
    assert "板块助攻" in r["label"], "应显示题材助攻"
    # ② 真孤军：行业+题材都空 → 维持无承接
    gj = {"sector_is_lone": True, "sector_mates": [], "theme_mates": [], "theme_has_sector_effect": False}
    r2 = ss.compute_support(gj, {})
    assert r2["support_level"] == "lone_no_support", "真孤军维持无承接"
    # ③ collect_mates_codes 合并行业+题材去重
    codes = ss.collect_mates_codes([{"sector_mates": ["600001", "600002"], "theme_mates": ["600002", "600003"]}])
    assert sorted(codes) == ["600001", "600002", "600003"], "行业+题材合并去重"
    # ④ annotate_lone 产出 theme_mates（同题材其他涨停）
    pool = [{"dm": "600001", "mc": "卫光生物", "hy": "生物制品", "sector": "生物制品"},
            {"dm": "600002", "mc": "冀衡医药", "hy": "化学制药", "sector": "化学制药"},
            {"dm": "600006", "mc": "凯莱英", "hy": "医疗服务", "sector": "医疗服务", "core_theme": "医药"}]
    r3 = ss.annotate_lone([{"dm": "600006", "mc": "凯莱英", "hy": "医疗服务", "sector": "医疗服务", "core_theme": "医药"}],
                          pool=pool, em_sector_zt={})
    assert sorted(r3[0]["theme_mates"]) == ["600001", "600002"], "theme_mates 应含同题材其他涨停"


# ────────────────── 题材定位·以题材涨停为准（2026-08-25 用户「黄河旋风钻石被定机器人」） ──────────────────

def test_theme_pool_core_theme(monkeypatch):
    """完整涨停池(pool)回填 core_theme 后，候选与 pool 同口径聚合——黄河旋风
    金刚石/培育钻石 不被行业 通用设备→机器人 误导（2026-08-25 用户抓包：
    「黄河旋风明明是钻石·你把它定到机器人里去了·以题材的涨停为准不要以板块的涨停」）。
    修复前：pool 行只有行业 → 候选 core_theme 与 pool 失配 → 聚合到机器人错误桶。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sector_support", str(ROOT / "wealth_code" / "core" / "sector_support.py"))
    ss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ss)
    # 完整涨停池：黄河旋风(通用设备·core_theme=金刚石) + 机器人票 + 其他钻石票
    pool = [
        {"dm": "600172", "mc": "黄河旋风", "hy": "通用设备", "sector": "通用设备", "core_theme": "金刚石/培育钻石"},
        {"dm": "600001", "mc": "机器人A", "hy": "通用设备", "sector": "通用设备", "core_theme": "机器人"},
        {"dm": "600002", "mc": "钻石B", "hy": "非金属材料", "sector": "非金属材料", "core_theme": "金刚石/培育钻石"},
    ]
    cand = [{"dm": "600172", "mc": "黄河旋风", "hy": "通用设备", "sector": "通用设备", "core_theme": "金刚石/培育钻石"}]
    r = ss.annotate_lone(cand, pool=pool, em_sector_zt={})
    c = r[0]
    assert c["theme_name"] == "金刚石/培育钻石", "黄河旋风应归钻石题材（以题材涨停为准）"
    assert c["theme_mates_cnt"] == 2, "钻石题材 2 家涨停（黄河旋风+钻石B）"
    assert c["theme_has_sector_effect"] is True, "钻石题材 2 家=有板块效应"
    # 无 core_theme 时回退行业映射（向后兼容·通用设备→机器人）
    cand2 = [{"dm": "600001", "mc": "机器人A", "hy": "通用设备", "sector": "通用设备"}]
    r2 = ss.annotate_lone(cand2, pool=[p for p in pool if p.get("dm") != "600172"], em_sector_zt={})
    assert r2[0]["theme_name"] == "机器人", "无 core_theme 回退行业映射"


# ────────────────── 小周期载体三件套（2026-08-25 用户「载体思路·全部接入」） ──────────────────

def test_relay_fbt_factor():
    """fbt 封板时间因子：早封加分（09:30-10:00 秒板=主动走强最高）、尾盘弱封惩罚。
    实证 8/25：汉森 09:45 秒板(5板) vs 尾盘票——同分基础下早封分高。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("relay_score", str(ROOT / "wealth_code" / "core" / "relay_score.py"))
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    cfg = rs._load_config()
    s = cfg["leadership"]["fbt"]
    # 早封秒板（09:45 → 585min ≤600）最高档
    early = {"is_sector_dragon": False, "boards": 1, "lead_score": 0, "fbt": "094536", "tags": []}
    late = {"is_sector_dragon": False, "boards": 1, "lead_score": 0, "fbt": "144500", "tags": []}
    no_fbt = {"is_sector_dragon": False, "boards": 1, "lead_score": 0, "fbt": "", "tags": []}
    sc_early = rs._score_leadership(early, cfg)
    sc_late = rs._score_leadership(late, cfg)
    sc_none = rs._score_leadership(no_fbt, cfg)
    assert sc_early > sc_late, "早封应高于尾盘弱封"
    assert sc_early > sc_none, "早封应高于无fbt"
    assert s["before_1000"] >= s["before_0930"], "秒板档 ≥ 竞价封死档（秒板=主动走强更健康）"


def test_carry_timing():
    """带动性时序：按 fbt 算「封板后 30min 内同题材新增涨停数」。
    实证 8/25：汉森 09:45 带动华森/海南海药 2 家（carry=2·行业映射兜底海南海药 化学制药→医药）；
    深中华 09:25 竞价封死 0 家跟进（孤军）。题材接口细分（液冷散热/半导体散热→AI算力）桥接后
    液冷系可聚合（英维克 10:05 带动恒铭达等）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sector_support", str(ROOT / "wealth_code" / "core" / "sector_support.py"))
    ss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ss)
    pool = [
        {"dm": "600001", "mc": "华森制药", "hy": "中药Ⅱ", "sector": "中药Ⅱ", "fbt": "094906", "core_theme": "医药"},
        {"dm": "600002", "mc": "海南海药", "hy": "化学制药", "sector": "化学制药", "fbt": "095403", "core_theme": "脑机接口"},
        {"dm": "600003", "mc": "冀衡医药", "hy": "化学制药", "sector": "化学制药", "fbt": "094306", "core_theme": "医药"},
        {"dm": "600004", "mc": "卫光生物", "hy": "生物制品", "sector": "生物制品", "fbt": "093012", "core_theme": "医药"},
    ]
    cand = [{"dm": "600005", "mc": "汉森制药", "hy": "中药Ⅱ", "sector": "中药Ⅱ", "fbt": "094536", "core_theme": "中药"}]
    r = ss.annotate_carry(cand, pool=pool)
    c = r[0]
    # 华森(主题桶·4min) + 海南海药(行业映射兜底·9min) = 2 家；冀衡/卫光在汉森前不计
    assert c["carry_cnt"] == 2, f"汉森应带动 2 家（华森+海南海药），实际 {c['carry_cnt']}"
    assert c["carry_score"] == round(min(2 / 3.0, 1.0), 2)
    assert c["carry_time"] == "09:45"
    # 孤军：深中华 竞价封死 09:25 → 同题材无其他票（真孤军）带动 0
    pool2 = [{"dm": "600010", "mc": "完全不相关票", "hy": "服装家纺", "sector": "服装家纺", "fbt": "093000", "core_theme": "服装"}]
    cand2 = [{"dm": "600011", "mc": "深中华A", "hy": "饰品", "sector": "饰品", "fbt": "092500", "core_theme": "饰品"}]
    r2 = ss.annotate_carry(cand2, pool=pool2)
    assert r2[0]["carry_cnt"] == 0, "孤军竞价封死应带动 0 家"
    # 液冷系桥接：液冷散热/半导体散热 同归 AI算力
    assert ss._theme_key({"core_theme": "液冷散热"}) == "AI算力"
    assert ss._theme_key({"core_theme": "半导体散热"}) == "AI算力"


def test_carrier_track(monkeypatch, tmp_path):
    """载体证伪追踪：昨日载体今日断板=证伪迁移。实证 8/25：汉森(昨4板)今日 5 板确认，
    哈森股份/天洋新材 今日断板证伪。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("carrier_track", str(ROOT / "scripts" / "carrier_track.py"))
    ct = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ct)
    # monkeypatch CACHE 指向 tmp 构造的两日数据
    cache = tmp_path / "cache"
    (cache / "zt_history").mkdir(parents=True)
    # 昨日(8/24)：汉森4板 + 哈森3板 + 天洋2板
    (cache / "zt_history" / "zt_2026-08-24.json").write_text(json.dumps({
        "date": "2026-08-24", "zt_list": [
            {"code": "002412", "name": "汉森制药", "boards": 4, "zbc": 0, "sector": "中药Ⅱ"},
            {"code": "600001", "name": "哈森股份", "boards": 3, "zbc": 3, "sector": "服装家纺"},
            {"code": "600002", "name": "天洋新材", "boards": 2, "zbc": 7, "sector": "塑料"},
        ]}, ensure_ascii=False), encoding="utf-8")
    # 今日(8/25)：汉森5板在池，哈森/天洋断板
    (cache / "yesterday_zt.json").write_text(json.dumps({
        "date": "2026-08-25", "zt_list": [
            {"code": "002412", "name": "汉森制药", "boards": 5, "zbc": 0, "sector": "中药Ⅱ"},
            {"code": "600010", "name": "新华百货", "boards": 3, "zbc": 1, "sector": "一般零售"},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ct, "CACHE", cache)
    monkeypatch.setattr(ct, "OUT_PATH", cache / "carrier_state.json")
    out = ct.run()
    assert out["date"] == "2026-08-25" and out["prev_date"] == "2026-08-24"
    today_names = {c["name"] for c in out["carriers_today"]}
    assert "汉森制药" in today_names and "新华百货" in today_names
    dead_names = {c["name"] for c in out["carriers_dead"]}
    assert "哈森股份" in dead_names and "天洋新材" in dead_names, "昨日载体今日断板应证伪"
    assert "汉森制药" not in dead_names, "今日在板载体不得判证伪"
    assert "证伪" in out["carrier_migration"]


# ────────────────── 腾讯自选股量能/广度兜底（2026-08-25 用户「腾讯自选股接口呢」） ──────────────────

def test_tx_selfchanged_parse():
    """腾讯自选股 changedist 解析：量能+广度全补（东财挂时的终极兜底）。
    2026-08-25 实证：东财 push2 全天 73 次请求全失败 → changedist 补上 上涨4234/下跌1246/平70 +
    两市成交额。口径：量能取 qt 三指数（changedist 漏创业板独立成交·仅上证+深证主板）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("intraday_pulse", str(ROOT / "scripts" / "intraday_pulse.py"))
    ip = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ip)
    md = """### 市场涨跌分布（沪深A股）

| 上涨 | 下跌 | 平盘 | 涨停 | 跌停 | 停牌 | 上涨占比 |
| --- | --- | --- | --- | --- | --- | --- |
| 4234 | 1246 | 70 | 70 | 4 | 4 | 76% |

两市成交额：18318.44亿（较上日 -1756.12亿）

> 当前市场上涨家数占比全市场76%，市场情绪偏高。"""
    r = ip.parse_tx_selfchanged(md)
    assert r["up"] == 4234 and r["down"] == 1246 and r["flat"] == 70, "涨跌家数应解析"
    assert r["total_amount_yi"] == 18318.4, "成交额应解析"
    # 空输入降级
    assert ip.parse_tx_selfchanged("") == {}
    assert ip.parse_tx_selfchanged(None) == {}


def test_fetch_market_three_tier(monkeypatch):
    """fetch_market 三级降级：东财→qt(量能)→changedist(广度)全链路。
    东财挂 + qt 通 → 量能=qt三指数 + 广度=changedist（2026-08-25 真实场景）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("intraday_pulse", str(ROOT / "scripts" / "intraday_pulse.py"))
    ip = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ip)
    # 东财挂（无 em_market）+ qt 通 + changedist 通
    tx_raw = 'v_sh000001="1~上证指数~000001~3889.44~3882.01~3863.37~464117264~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~20260825161402~7.43~0.19~3896.21~3850.86~3889.44/464117264/858874228922~464117264~85887423~0.96~17.71~3896.21~3850.86~1.17~608388.09";v_sz399001="1~深证成指~399001~13745.87~13794.29~13683.91~579779582~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~20260825161418~-48.42~-0.35~13848.40~13568.51~13745.87/579779582/972970230987~579779582~97297023~0.96~17.71~13848.40~13568.51~1.17~608388.09";v_sz399006="1~创业板指~399006~3397.52~3432.08~3384.16~170664183~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~20260825161418~-34.56~-1.0~3432.08~3358.40~3397.52/170664183/463976734895~170664183~46397673~0.96~17.71~3432.08~3358.40~1.17~608388.09";'
    cd_md = "| 4234 | 1246 | 70 | 70 | 4 | 4 | 76% |\n\n两市成交额：18318.44亿"
    monkeypatch.setattr(ip, "_fetch_tx_selfchanged", lambda: cd_md)
    r = ip.fetch_market({"tx_index": tx_raw})
    # 量能 = qt 三指数口径（8589+9730+4640≈22959亿·changedist 漏创业板不作为量能）
    assert r["total_amount_yi"] > 20000, f"量能应为 qt 三指数口径≥2万亿, 实际 {r['total_amount_yi']}"
    assert r["up"] == 4234 and r["down"] == 1246, "广度应来自 changedist"
    # 东财正常 → 东财优先
    em = {"em_market": {"data": {"diff": [
        {"f14": "上证指数", "f6": 1110000000000, "f104": 2000, "f105": 350, "f106": 30},
        {"f14": "深证成指", "f6": 1270000000000, "f104": 2300, "f105": 500, "f106": 40}]}}}
    r2 = ip.fetch_market(em)
    assert r2["up"] == 4300, "东财优先含涨跌家数"
    # 全挂 → 空 dict
    monkeypatch.setattr(ip, "_fetch_tx_selfchanged", lambda: None)
    assert ip.fetch_market({}) == {}


# ────────────────── 腾讯自选股资金/筹码 + 龙虎榜（2026-08-25 用户「查询用腾讯自选股·龙虎榜」） ──────────────────

def test_tx_query_parse():
    """腾讯自选股 fund flow / chip 解析（候选资金筹码回填·2026-08-25 用户 #1）。
    fund flow 批量宽表（code 带 sz 前缀·负数亿）；chip 宽表每列带 code 前缀分桶。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tx_query", str(ROOT / "wealth_code" / "scripts" / "tx_query.py"))
    tq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tq)
    # fund flow markdown
    ff = """| symbol | code | name | MainNetFlow | MainNetFlow5D | MainNetFlow10D | MainNetFlow20D |
| --- | --- | --- | --- | --- | --- | --- |
| sz002412 | sz002412 | 汉森制药 | -149223034 | 108360513 | 94766902 | 5282335 |
| sz002837 | sz002837 | 英维克 | 1630365701 | -540507967 | -178288669 | -1046490825 |"""
    # 注入 _run 返回
    import subprocess as _sp
    calls = {"fund": ff, "chip": ""}
    tq._run = lambda args, timeout=30: calls.get(args[0], "")
    r = tq.fund_flow(["002412", "002837"])
    assert r["002412"]["main_flow_yi"] == -1.49, f"汉森主力应-1.49亿, 实际{r['002412']}"
    assert r["002412"]["main_flow_5d_yi"] == 1.08
    assert r["002837"]["main_flow_yi"] == 16.3, f"英维克主力应+16.3亿"
    assert r["002837"]["main_flow_5d_yi"] == -5.41
    # chip 宽表（带前缀）
    chip = """| sz002412.code | sz002412.chipProfitRate | sz002412.chipAvgCost | sz002412.chipConcentration90 |
| --- | --- | --- | --- |
| sz002412 | 100.0 | 7.33 | 30.09 |"""
    tq._run = lambda args, timeout=30: chip
    c = tq.chip(["002412"])
    assert c["002412"]["chip_profit_rate"] == 100.0, f"获利盘应100, 实际{c}"
    assert c["002412"]["chip_avg_cost"] == 7.33
    # 空降级
    tq._run = lambda args, timeout=30: None
    assert tq.fund_flow(["002412"]) == {}
    assert tq.chip(["002412"]) == {}


def test_lhb_parse():
    """龙虎榜抓取解析（2026-08-25 用户「改卡片时间满足龙虎榜刷新」·盘后18:00+披露）。
    机构榜 35 只（机构买入额/净买入）；游资席位 128 家（活跃席位表·5列）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("lhb_fetch", str(ROOT / "scripts" / "lhb_fetch.py"))
    lf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lf)
    inst_md = """**机构榜** (35只)
| 排名 | 代码 | 名称 | 上榜天数 | 机构买入席位 | 机构买入额 | 买入占比 | 总买入额 | 净买入额 | 净占比 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | sh600487 | 亨通光电 | 1 | 1 | 6.56亿 | 6.9% | 6.56亿 | 0.00 | 10.6% |
| 1 | sz002837 | 英维克 | 1 | 4 | 1.37亿 | 10.01% | 3.83亿 | 2.47亿 | 8.51% |"""
    r = lf._parse_institution(inst_md)
    by_name = {x["name"]: x for x in r}
    assert by_name["亨通光电"]["inst_buy_yi"] == 6.56, "机构买入额应解析亿"
    assert by_name["英维克"]["inst_buy_yi"] == 1.37
    assert by_name["英维克"]["net_yi"] == 2.47
    hot_md = """**活跃席位** (128个)
| 席位ID | 席位名称 | 股票代码 | 股票名称 | 买入金额 |
| --- | --- | --- | --- | --- |
| T000029774 | 开源证券西安西大街 | sz002837;sh600127;sz002081 | 英维克;金健米业;金螳螂 | 3.25亿 |"""
    h = lf._parse_hotmoney(hot_md)
    assert h and h[0]["seat"] == "开源证券西安西大街", f"席位名应解析, 实际{h}"
    assert h[0]["amount_yi"] == 3.25
    names = [s["name"] for s in h[0]["stocks"]]
    assert "英维克" in names and "金螳螂" in names, "成分股应拆解"


# ────────────────── 资金场内切换（2026-08-26 用户实盘教学·盘中分钟级） ──────────────────

def test_fund_switch_detect():
    """旧主线龙头开板出货 + 同刻新方向涨停潮+资金霸榜 → 检测「资金场内切换」。
    五步时序（shared_rules【资金场内切换·盘中分钟级】）：主线拉升→后排掉队→他线加强→
    龙头炸板出货→新龙拉起。实证：08-26 医药汉森开板(+4.06%换手20%)，同刻白银有色/江西铜业涨停。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("live_signal", str(ROOT / "scripts" / "live_signal.py"))
    ls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ls)

    # 1) 生命周期：09:30 汉森封板入 core_high（prev leaders 含 board≥2/涨幅≥9.5）
    sig = {"sector_pushed": ["医药"], "fund_switch_pushed": []}
    prev1 = {"leaders": [
        {"code": "002412", "name": "汉森制药", "sector": "医药", "pct": 10.0,
         "buyable": False, "boards": "5天5板"},
        {"code": "600362", "name": "江西铜业", "sector": "有色金属", "pct": 9.5,
         "buyable": True, "boards": ""},
    ]}
    ls._track_core_high(sig, prev1)
    assert sig["core_high"]["002412"]["sealed"] is True, "封板票应入 core_high 且 sealed=True"

    # 2) 10:20 汉森开板回落+4.06%，有色涨停潮+资金流入 → 命中切换
    latest = {
        "leaders": [
            {"code": "002412", "name": "汉森制药", "sector": "医药", "pct": 4.06, "buyable": True},
            {"code": "601212", "name": "白银有色", "sector": "黄金", "pct": 10.03, "buyable": False},
            {"code": "600362", "name": "江西铜业", "sector": "有色金属", "pct": 10.01, "buyable": False},
        ],
        "top5": [{"name": "有色金属", "zt_count": 3,
                  "leader": {"name": "江西铜业", "pct": 10.01, "buyable": False}}],
        "sector_risk": {"inflow_top": [{"name": "有色金属", "flow_yi": 70.0}]},
    }
    sw = ls.detect_fund_switch(latest, {"leaders": [], "top5": []}, sig, "10:20")
    assert sw and sw["old"] == "医药" and sw["new"] == "有色金属", f"应命中医药→有色, 实际{sw}"
    assert "汉森制药" in sw["old_names"]
    assert sw["new_zt"] == 3 and sw["new_leader"] == "江西铜业"

    # 3) 同组合当天去重
    assert ls.detect_fund_switch(latest, {"leaders": [], "top5": []}, sig, "10:25") is None

    # 4) 边界：旧线票未封板历史（sealed=False）不触发
    sig2 = {"sector_pushed": ["医药"], "fund_switch_pushed": [],
            "core_high": {"002412": {"name": "汉森制药", "sector": "医药", "pct": 10.0, "sealed": False}}}
    assert ls.detect_fund_switch(latest, {"leaders": [], "top5": []}, sig2, "10:30") is None

    # 5) 边界：新线无资金流入不触发（无钱=假突破，不标切换）
    sig3 = {"sector_pushed": ["医药"], "fund_switch_pushed": [],
            "core_high": {"002412": {"name": "汉森制药", "sector": "医药", "pct": 10.0, "sealed": True}}}
    latest_noflow = dict(latest, sector_risk={"inflow_top": [{"name": "通信技术", "flow_yi": 5.0}]})
    assert ls.detect_fund_switch(latest_noflow, {"leaders": [], "top5": []}, sig3, "10:35") is None

    # 6) 卡渲染：标题/语义色/新旧线证据齐全
    card = ls.build_fund_switch_card(sw, "10:20")
    s = json.dumps(card, ensure_ascii=False)
    assert "资金切换·医药→有色金属" in card.get("title", "")
    assert "旧线退潮" in s and "新线承接" in s
    assert "跟随新方向承接者" in s


def test_st_filter_rising_and_card():
    """⛔ 铁律④ 不看ST（2026-08-26 用户抓包 ST海龙(000677) 进 10:55 脉冲卡）。
    双层防线：①源头 parse_em_rising 过滤 ST/*ST ②渲染 build_window_card 兜底过滤（防缓冲残留）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("intraday_pulse", str(ROOT / "scripts" / "intraday_pulse.py"))
    ip = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ip)
    raw = {"data": {"diff": [
        {"f12": "000677", "f14": "ST海龙", "f3": 9.88, "f2": 1.89, "f100": "化学纤维"},
        {"f12": "002412", "f14": "*ST汉森", "f3": 9.5, "f2": 12.0, "f100": "医药"},
        {"f12": "600362", "f14": "江西铜业", "f3": 10.01, "f2": 48.57, "f100": "有色金属"},
    ]}}
    out = ip.parse_em_rising(raw)
    assert len(out) == 1 and out[0]["name"] == "江西铜业", f"源头应滤 ST/*ST, 实际{out}"

    spec2 = importlib.util.spec_from_file_location("pulse_aggregator", str(ROOT / "scripts" / "pulse_aggregator.py"))
    pa = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(pa)
    summary = {
        "zt": [{"name": "ST海龙", "code": "000677", "pct": 9.88, "sector": "化学纤维"}],
        "strong": [{"name": "正常票", "code": "600001", "pct": 12.0}],
        "surge": [], "nf": [], "sector": [],
        "charge": [{"name": "*ST海龙", "code": "000677", "pct": 9.88, "sector": "化学纤维"}],
        "main_line": "化学纤维",
    }
    card = pa.build_window_card(summary, "20260826_1055", None, "10:55", focus=True)
    s = json.dumps(card, ensure_ascii=False)
    assert "ST海龙" not in s and "*ST" not in s, "渲染层兜底应滤 ST"
    assert "正常票" in s, "正常票应保留"


def test_flow_5d_trend_and_board():
    """5日资金监管升级（2026-08-26 用户「扩大为5日流入流出板块监管」）。
    FLOW_LOOKBACK=5：五日累计+流入天数双轨；空洞容忍（≥3有效点）；转流入/持续流入/全负收敛放大；
    五日资金榜（Top流入/流出·监管总览）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("theme_rotation", str(ROOT / "scripts" / "theme_rotation.py"))
    tr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tr)
    assert tr.FLOW_LOOKBACK == 5, "资金窗口应升级为5日"
    assert tr.FLOW_MIN_VALID == 3

    # 构造 5 日资金序列（带 1 天空洞验证容忍）
    flow_series = [
        ("2026-08-19", {"钨": {"flow_yi": -5.0}}),
        ("2026-08-20", {"钨": {"flow_yi": -3.0}}),
        ("2026-08-21", {"钨": {"flow_yi": 4.0}}),
        ("2026-08-24", {}),                       # 空洞：当日无该板块
        ("2026-08-25", {"钨": {"flow_yi": 6.0}}),
    ]
    # 转流入：末点净流入 + 前段曾流出 + Σ5日=+2>0 → inflow_rising
    # （3日版 [-3,4,空洞] 会因空洞 neutral；5日版能跨空洞看到资金掉头）
    trend, vals, tx10, tx5 = tr._flow_trend("六氟化钨", flow_series)
    assert trend == "inflow_rising", f"5日应识别转流入, 实际{trend}"
    assert len(vals) == 5, "5日窗口应返回5个点"
    assert tx10 is None, "10日总览应为单值(无腾讯数据时None)"

    # 空洞过重（<3 有效点）→ neutral 不硬判
    sparse = [("a", {"X": {"flow_yi": 1.0}}), ("b", {}), ("c", {}), ("d", {}), ("e", {})]
    trend2, _, _, _ = tr._flow_trend("X", sparse)
    assert trend2 == "neutral", "有效点<3 应 neutral"

    # 全负收敛 vs 放大
    shrink = [("a", {"Y": {"flow_yi": -10.0}}), ("b", {"Y": {"flow_yi": -8.0}}),
              ("c", {"Y": {"flow_yi": -5.0}}), ("d", {"Y": {"flow_yi": -3.0}}), ("e", {"Y": {"flow_yi": -1.0}})]
    assert tr._flow_trend("Y", shrink)[0] == "outflow_shrinking"
    widen = [("a", {"Y": {"flow_yi": -1.0}}), ("b", {"Y": {"flow_yi": -3.0}}),
             ("c", {"Y": {"flow_yi": -5.0}}), ("d", {"Y": {"flow_yi": -8.0}}), ("e", {"Y": {"flow_yi": -10.0}})]
    assert tr._flow_trend("Y", widen)[0] == "outflow_widening"
