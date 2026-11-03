# -*- coding: utf-8 -*-
"""点评语义分段排版（2026-09-11 用户「点评不要一大堆数据堆在一起·排版/换行/语义/加粗」）。

病灶回顾：一篇 300~500 字的点评挤在**一个 div 一行**里（名称+风险标签+走势+题材+股性连写），
移动端就是一堵墙；风险句还被提到冒号后置首，语义顺序颠倒。

本文件锁死四条不可回退的性质：
  1. **不再一坨**：点评渲染结果必为多行（段名独占一行）；单行字数有上限。
  2. **语义分段**：走势 / 题材催化 / 历史股性 / ⚠️风险 四段齐备，次序固定，风险置尾。
  3. **只排版不改写**：原文标点保留（不吞句号）、只在标点处折行（绝不切词）、不编造风险。
  4. **渲染合规**：段名加粗 ⛔ 不在 text_tag 内；风险段用 turquoise；无机器黑话。
"""
import importlib.util
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("build_cards", WC / "pipelines" / "build_cards.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)

# 存量单段点评（2026-09-11 真实盘后卡文本·未分段的历史格式）
_LEGACY = (
    "电力方向的领头票，今天全池分数最高（龙头分84.8），09:25一开盘就封住了，封板后半小时里同方向又冒出3家涨停，"
    "是它在带节奏；今天电力一共4家涨停（杭州热电、江苏新能、拓日新能），背后是算电协同这条线——10月26日上海要开"
    "国际算电协同产业大会，券商测算到2030年算力用电会占到全社会用电的5%到6%。历史股性：近一年涨停11次、历史上"
    "最长连过5板、涨停后能继续涨停的比例约三成，但首板次日平均只高开0.08%。⚠️风险：今天全市场做多气氛只有3分"
    "（满分10）、昨天涨停的票只有两成今天还能继续涨停，开盘价和量不给力就别硬上。"
)
# 新格式（agent 显式分段）
_EXPLICIT = (
    "今日走势：09:25 秒板，封板后半小时同方向又冒出 3 家涨停，是它在带节奏。\n"
    "题材催化：算电协同产业大会 10 月 26 日落地，北京刚发文。\n"
    "历史股性：近一年涨停 11 次、最长连过 5 板，但首板次日平均平开。\n"
    "⚠️风险：位置已经 3 板，开太高就别追。"
)



# ── 分层隔离：本文件只测「点评语义分段渲染（_narr_render 层）」────────────
# ⛔ 2026-10-21：`VERIFY_CARD_LEAN`（整段取舍）是另一层；档位 2 会把整段点评从卡里撤掉，
#    本文件的「点评必须分段」断言就测不到了 → 假红。lean 自己的契约在
#    `tests/test_verify_card_lean.py`。
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
    #    点评语义分段，与「机械口径放哪」无关 —— 不隔离会让断言随融合默认值假红。
    #    融合契约在 `tests/test_verify_card_lean.py`。
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "0")


def _lines(txt: str) -> list[str]:
    return [ln for ln in str(txt).split("\n") if ln.strip()]


# ── 1. 不再一坨 ────────────────────────────────────────────────
def test_legacy_blob_is_no_longer_one_line():
    """存量单段点评 → 必须切成多行（本次用户诉求的根因回归锁）。"""
    out = bc._narr_render(_LEGACY)
    assert len(_lines(out)) >= 4, f"点评仍是一坨：{out!r}"


def test_every_line_has_length_cap():
    """单行正文长度上限 —— 不允许再出现 100+ 字的墙（排版标记不计入）。"""
    bodies = [body for _, body in bc._narr_segments(_LEGACY)]
    longest = max(len(b) for b in bodies)
    assert longest <= 80, f"存在超长正文行（{longest} 字）：{[b for b in bodies if len(b) > 80]}"


# ── 2. 语义分段 ───────────────────────────────────────────────
def test_legacy_split_into_four_semantic_segments():
    """走势 / 题材催化 / 历史股性 / ⚠️风险 四段齐备且次序固定。"""
    out = bc._narr_render(_LEGACY)
    order = [lab for lab, _ in bc._narr_segments(_LEGACY) if lab]
    assert order == ["今日走势", "题材催化", "历史股性", "⚠️ 风险"], order
    for lab in ("今日走势", "题材催化", "历史股性"):
        assert f"**{lab}**" in out, f"缺段名加粗：{lab}"
    assert "<text_tag color='turquoise'>⚠️ 风险</text_tag>" in out


def test_risk_segment_is_last_line():
    """风险段必须收尾（2026-08-16 铁律「点评末尾写风险」的渲染层兑现）。"""
    segs = [(lab, body) for lab, body in bc._narr_segments(_LEGACY) if lab]
    assert segs[-1][0] == "⚠️ 风险", segs


def test_explicit_segments_respected_in_order():
    """agent 显式分段（\\n + 段名）→ 原序保留，不重排。"""
    segs = [lab for lab, _ in bc._narr_segments(_EXPLICIT) if lab]
    assert segs == ["今日走势", "题材催化", "历史股性", "⚠️ 风险"], segs


def test_label_alias_accepted():
    """段名别名（题材/股性/走势）归一化到规范段名。"""
    segs = bc._narr_segments("走势：a。\n题材：b。\n股性：c。\n风险：d。")
    assert [lab for lab, _ in segs if lab] == ["今日走势", "题材催化", "历史股性", "⚠️ 风险"]


# ── 3. 只排版不改写 ───────────────────────────────────────────
def test_punctuation_preserved():
    """切段不吞标点：原文句号不得丢失（曾因 strip 吃掉句尾）。"""
    out = bc._narr_render(_LEGACY)
    assert "5%到6%。" in out, out
    assert out.rstrip().endswith("。"), out


def test_wrap_only_at_punctuation():
    """折行只发生在标点处，绝不切词（每行首字符不得是汉字中段）。"""
    out = bc._narr_render(_LEGACY)
    for ln in _lines(out)[1:]:
        prev_ok = ln.startswith("**") or ln.startswith("<text_tag") or ln.startswith("　　")
        assert prev_ok, f"续行未按标点断开：{ln!r}"
    # 行尾要么是标点，要么是段名行/续行前的完整分句
    for ln in _lines(out)[:-1]:
        body = ln.strip()
        if body.startswith("**") or body.startswith("<text_tag"):
            continue
        assert body[-1] in "。；，、）%", f"折行点不是标点：{body[-8:]!r}"


def test_no_fabricated_risk_when_absent():
    """原文没有风险句 → 不得凭空补 turquoise 风险行（诚实原则）。"""
    out = bc._narr_render("今日走势：09:25 秒板，成交 6.7 亿。")
    assert "turquoise" not in out, out


def test_empty_narrative_renders_empty():
    for v in (None, "", "   ", "\n\n"):
        assert bc._narr_render(v) == ""


# ── 4. 渲染合规 ───────────────────────────────────────────────
def test_bold_never_inside_text_tag():
    """⛔ text_tag 内禁 **（shared_rules【标题层级规范】）。"""
    for txt in (_LEGACY, _EXPLICIT):
        out = bc._narr_render(txt)
        for chunk in out.split("<text_tag"):
            head, _, tail = chunk.partition("</text_tag>")
            if ">" in head:
                assert "**" not in head.split(">", 1)[1], f"text_tag 内出现加粗：{chunk[:60]}"


def test_render_has_no_jargon():
    """排版结果不得引入机器黑话（复用禁项清单·用户「不要推专业术语」）。"""
    bad = ["regime=", "expected", "reject", "pending", "承接", "孤军", "弱转强",
           "容量核心", "梯队", "体系", "维度", "研判", "标的", "卡位", "溢价率"]
    out = bc._narr_render(_LEGACY) + bc._narr_render(_EXPLICIT)
    hit = [b for b in bad if b in out]
    assert hit == [], f"点评渲染残留黑话 {hit}"


def test_label_of_rejects_unknown_prefix():
    """非白名单前缀不得被当成段名（防误切「今天市场是普跌：…」这类正文）。"""
    lab, body = bc._narr_label_of("今天市场是普跌：上证跌1.21%")
    assert lab is None and body == "今天市场是普跌：上证跌1.21%"


# ── 5. 端到端：卡内点评块结构（本次改动的最终回归锁）────────────────
_IT = {
    "code": "000993", "name": "闽东电力", "score": 84.8, "boards": 3,
    "core_theme": "电力", "industry_sector": "电力", "narrative": _EXPLICIT,
    "expectation": {"expected_range": ["0.0", "1.4"]},
}


def _blocks(card):
    return [e["text"]["content"] for e in card["elements"] if e.get("text")]


def test_eod_card_narrative_is_multiline_block():
    """eod 卡：名称独占一行，点评按语义分段 —— 不再「名称：风险+正文」一坨。

    `ai_plan={}`：显式声明本卡无 AI 预案（2026-10-14 起盘后卡会渲染「🤖 AI 明日买入预案」，
    其理由同样可能含「今日走势」这类段名）→ 隔离真实 cache，只锁点评块本身。
    """
    card = bc.build_eod_card({"date": "2026-09-11", "regime": "neutral",
                              "relay": [dict(_IT)], "watch": [], "diagnostics": {},
                              "ai_plan": {}})
    narr = [b for b in _blocks(card) if "今日走势" in b]
    assert len(narr) == 1, narr
    lines = _lines(narr[0])
    assert len(lines) == 5, lines                      # 名称 + 4 段
    assert lines[0].startswith("　·")                   # 名称独占一行
    assert "：⚠️" not in lines[0]                       # 风险不再插在名称冒号后
    assert lines[-1].startswith("　　<text_tag color='turquoise'>")  # 风险收尾


def test_verify_card_risk_not_inline_and_narrative_multiline(monkeypatch):
    """竞价卡：标题行不再挂内联风险标签；点评走同一套语义分段。

    2026-10-21 简洁模式（用户「早盘内容过于冗余·简洁一点但核心数据不要少」）改契约：
    买入票点评 = 走势 + 题材 + 风险 **3 段**，`历史股性` 段与上方 `📈 历史基因` 行是同一批
    数字的两次表述 → 只留 gene 行一处。⛔ 断言同时锁住「段没丢完」与「回退开关」：
    `VERIFY_CARD_COMPACT=0` 时恢复 4 段（旧版全量）。
    """
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    rec = {"code": "000993", "name": "闽东电力", "bucket_label": "高标/题材核心",
           "theme": "电力", "industry_sector": "电力", "narrative": _EXPLICIT,
           "performance": {"open_check": {"decision_status": "buy", "can_enter": True,
                                          "gap_pct": 0.4, "auction_amount_yi": 0.9,
                                          "auction_amount_need_yi": 0.6,
                                          "signal_label": "符合预期"}}}
    vr = {"payload": {"records": [rec]},
          "summary": {"total_candidates": 1, "buy_signals": 1,
                      "hit_signals": 0, "covered": 0,
                      "nav_latest": 1.0, "trade_date10": "2026-09-11"}}

    def _render():
        card = bc.build_verify_card(vr)
        blocks = _blocks(card)
        heads = [b for b in blocks if b.startswith("✅")]
        assert heads and all("⚠️" not in h for h in heads), heads   # 标题行无内联风险
        narr = [b for b in blocks if "昨日走势" in b]
        assert narr, blocks
        return _lines(narr[0])

    lines = _render()
    assert len(lines) == 3, lines                     # 走势 + 题材 + 风险（股性并入 gene 行）
    assert lines[-1].startswith("　　<text_tag color='turquoise'>")
    gene = [b for b in _blocks(bc.build_verify_card(vr)) if "历史基因" in b]
    assert gene, "gene 行必须仍在（数据不丢）"

    monkeypatch.setenv("VERIFY_CARD_COMPACT", "0")    # 回退开关：恢复旧全量 4 段
    assert len(_render()) == 4


# ── 6. 🗣️ 全局旁白的「盘面基准日」标注（2026-09-14）──────────────────
# 病灶：竞价卡的 🗣️ 透传上一交易日候选文件里的旁白，正文原话「今天的市场是普跌…」，
# 在次日卡上被读成当日盘面（实测 09-14 卡引用的全是 09-11 周五收盘数据）。
# 用户口径：用上一交易日的即可（开盘前当日盘面尚不存在），但必须让读者知道是哪一天。
_GN = "账户：14个交易日6赚8亏。\n盘面：今天的市场是普跌，40家涨停。\n操作：控制仓位。"


def test_narr_basis_label_unit_same_day_is_silent():
    """基准日与当次交易日一致 → 不标注（盘后卡原样渲染·向后兼容）。"""
    assert bc._narr_basis_label("2026-09-11", "2026-09-11") == ""
    assert bc._narr_basis_label(None, "2026-09-11") == ""      # 缺基准日 → 不编造
    assert bc._narr_basis_label("", "") == ""


def test_narr_basis_label_unit_prev_session():
    """上一交易日 → 标出「MM-DD 周X收盘盘面」（含星期，读者一眼看清是哪天）。

    当次交易日未知时仍标注：标注陈述的是「旁白写于哪天」，该事实与交易日无关，标出更安全。
    """
    assert bc._narr_basis_label("2026-09-11", "2026-09-14") == "（09-11 周五收盘盘面）"
    assert bc._narr_basis_label("2026-09-11", None) == "（09-11 周五收盘盘面）"
    # 跨月/周中同样成立（09-15 周二 → 09-16 周三）
    assert bc._narr_basis_label("2026-09-15", "2026-09-16") == "（09-15 周二收盘盘面）"


def test_narr_basis_label_unit_bad_date_not_fabricated():
    """非法日期不得崩，也不得编造星期。"""
    assert bc._narr_basis_label("bad-date", "2026-09-14") == "（bad-date 收盘盘面）"


def test_verify_card_labels_prev_session_narrative(monkeypatch):
    """竞价卡：旁白基准日（date10）≠ 当次交易日（trade_date10）→ 必须带日期标注。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    card = bc.build_verify_card({
        "payload": {"records": []},
        "summary": {"date10": "2026-09-11", "trade_date10": "2026-09-14",
                    "total_candidates": 0, "buy_signals": 0, "hit_signals": 0,
                    "covered": 0, "nav_latest": 1.0},
        "global_narrative": _GN,
    })
    gn = [b for b in _blocks(card) if b.startswith("🗣️")]
    assert len(gn) == 1, gn
    assert gn[0].startswith("🗣️ **全局**（09-11 周五收盘盘面）"), gn[0][:40]
    assert "盘面" in gn[0]                                     # 正文仍完整渲染


def test_verify_card_no_label_when_same_session(monkeypatch):
    """同日（无 date10 或与交易日相同）→ 保持原样，不画蛇添足。"""
    monkeypatch.setattr(bc, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(bc, "_nav_drawdown_block", lambda m: None)
    card = bc.build_verify_card({
        "payload": {"records": []},
        "summary": {"date10": "2026-09-14", "trade_date10": "2026-09-14",
                    "nav_latest": 1.0},
        "global_narrative": _GN,
    })
    gn = [b for b in _blocks(card) if b.startswith("🗣️")]
    assert gn and gn[0].startswith("🗣️ **全局**\n"), gn


def test_eod_card_labels_explicitly_stale_narrative():
    """盘后卡：上游显式声明 narrative_date 早于当日 → 标注（防陈旧旁白被读成当日）。

    注：eod 卡的 🗣️ 与点评块同分支（靠 relay/watch 的 narrative 才进入），故须给一只候选。
    """
    card = bc.build_eod_card({"date": "2026-09-14", "regime": "neutral",
                              "relay": [dict(_IT)], "watch": [], "diagnostics": {},
                              "global_narrative": _GN,
                              "narrative_date": "2026-09-11"})
    gn = [b for b in _blocks(card) if b.startswith("🗣️")]
    assert gn and "（09-11 周五收盘盘面）" in gn[0], gn

    card2 = bc.build_eod_card({"date": "2026-09-14", "regime": "neutral",
                               "relay": [dict(_IT)], "watch": [], "diagnostics": {},
                               "global_narrative": _GN})
    gn2 = [b for b in _blocks(card2) if b.startswith("🗣️")]
    assert gn2 and gn2[0].startswith("🗣️ **全局**\n"), gn2        # 默认不标注


# ── 依据→上涨逻辑 / 操作→操作思路（2026-10-14 用户图学习·三段式迭代）────────────────
def test_reason_segments_alias_growth_logic():
    """AI 决策理由第1段「依据：」→ 渲染为「上涨逻辑」段名（用户图左列语义·不带时点词防平移）。"""
    out = bc._narr_render("依据：9点49分封板、炸开3次，成交11.16亿。\n⚠️风险：位置很高。", prev=True)
    assert "**上涨逻辑**：" in out, out
    assert "⚠️ 风险" in out and "turquoise" in out
    # 「上涨逻辑」无时点词 → 跨日平移不得改写段名（禁变成「前日上涨逻辑」）
    assert "前日" not in out, out


def test_reason_segments_operation_branch():
    """第2段「操作：」→ violet 标签「🎯 操作思路」（条件推演·关键执行信息）。"""
    reason = ("依据：9点30分秒板，封单2.1亿。\n"
              "操作：若一字封死买不进，不追，看同题材龙二的承接；若竞价高开5%内有量，按仓位建议小仓。\n"
              "⚠️风险：换手不足，分歧会很大。")
    out = bc._narr_render(reason, prev=True)
    assert "🎯 操作思路" in out and "violet" in out, out
    # 正文条件分支保留原样（只排版不改写）
    assert "若一字封死买不进" in out, out


def test_ai_plan_block_no_double_prefix():
    """池内实判路径：理由已是「依据：」格式 → 段名渲染为「上涨逻辑」，禁再叠加「昨日逻辑」双重前缀。"""
    it = {"code": "600851", "name": "海欣股份",
          "performance": {"open_check": {"pool_reason": "竞价+2.1%·量能达标"}}}
    ai_view = {"600851": {"view": None, "bias": "focus",
                          "reason": "依据：公开征集转让10%股份，近一年涨停8次。\n⚠️风险：炸开3次。"}}
    line = bc._ai_view_line(it, ai_view)
    assert "上涨逻辑" in line, line
    assert line.count("昨日逻辑") == 0, line      # 双重段名 = 渲染 bug
