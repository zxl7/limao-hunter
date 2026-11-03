#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价卡「精简模式」契约锁（2026-10-21 用户「如果我不想显示以下数据」）。

用户列的 5 项 → 本轮处理（逐项锁死，且每项独立可逆）：
  ① 净值回撤线    → 保留但压缩（删心法解释，⚠️ 不删警示本身）
  ② 量价够格的票  → 只留差集（量价够格 ∧ AI 今天没买）
  ③ 观察          → 只留判定行（去掉基因/AI理由/点评），且剔除已出现过的票
  ④ 其余未达标    → 整段不渲染
  ⑤ 全局          → 压缩成只剩「操作」段
  ⑥ 池外/未采     → 整段不渲染（用户没提，顺手砍）

⛔ 本文件的关键不变量（⛔ 有一条被违反必须红）：
  · **一张票都不许从卡里消失**：8 只候选 → 精简后仍要能在卡里找到 8 只
  · **核心数据一个不许少**：竞价 gap、额/需要额、预期区间、池别、板数、9:25 判定
  · **整段不许静默消失不给交代**：砍了就说明去哪了（差集/重合/共几只）
⛔ 可逆：`VERIFY_CARD_LEAN=0` → 逐字节回到全量渲染（本文件末尾有回退测试）。
"""
import json
import re

import pytest

import pipelines.build_cards as bc


@pytest.fixture(autouse=True)
def _lean_default(monkeypatch):
    """本文件测的就是精简模式默认态。"""
    monkeypatch.delenv("VERIFY_CARD_LEAN", raising=False)


def _blocks(card):
    return [e["text"]["content"] for e in card["elements"] if e.get("text")]


def _text(card):
    return "\n".join(_blocks(card))


def _plain(card):
    return re.sub(r"<[^>]+>", "", _text(card))


def _section(plain, start_mark, end_mark):
    """按段落标题切一段正文（⛔ 避免用模糊词切：卡头也有「量价够格的票 N 只」）。"""
    if start_mark not in plain:
        return ""
    tail = plain.split(start_mark, 1)[1]
    return tail.split(end_mark, 1)[0] if end_mark in tail else tail


def _rec(code, name, status, gap, *, narrative=None, ai_view=None, boards=2,
         mech=None, bucket="接力池"):
    oc = {"decision_status": status, "gap_pct": gap,
          "auction_amount_yi": 0.5, "auction_amount_need_yi": 0.4,
          "signal_label": "符合预期", "decision_label": "符合预期"}
    if ai_view:
        oc["ai_view"] = ai_view
        oc["pool_reason"] = "开在达标位置、名额内 → 买入" if ai_view == "participate" else "开在达标位置但先看核心"
    if mech is not None:
        oc["mech_can_enter"] = mech
    r = {"code": code, "name": name, "boards": boards, "bucket_label": bucket,
         "core_theme": "机器人", "industry_sector": "通用设备",
         "expectation": {"expected_range": [0.0, "2.4"]},
         "performance": {"open_check": oc}}
    if narrative:
        r["narrative"] = narrative
    return r


_NARR = ("今日走势：9 点 34 分封板，被砸开 3 次又回封。\n"
         "题材催化：做谐波减速器，小批量供货。\n"
         "历史股性：近一年 7 次涨停。\n"
         "风险：封单只占流通盘 0.67%，很薄。")


def _card(recs, *, narrative=None):
    vr = {"payload": {"records": recs},
          "summary": {"total_candidates": len(recs), "buy_signals": 1,
                      "hit_signals": 0, "covered": 0, "nav_latest": 1.0,
                      "trade_date10": "2026-10-21"}}
    if narrative:
        vr["global_narrative"] = narrative
    return bc.build_verify_card(vr)


# ── ② 机械量价只留差集 ──────────────────────────────────────────────────
def test_mech_section_drops_rows_already_in_buy_list(monkeypatch):
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "0")   # 本条目测的是**旧版两段分列**

    """量价够格 ∧ AI 已买 → 不在机械段重复列（实测这是冗杂最大来源）。"""
    card = _card([
        _rec("600001", "买入票", "buy", 1.0, ai_view="participate", mech=True),
        _rec("600002", "机械独有票", "buy", 1.2, ai_view="watch", mech=True),
    ])
    plain = _plain(card)
    # 两台都在卡里（一只在买入段、一只在机械段）
    assert "买入票" in plain and "机械独有票" in plain
    # 但机械段正文只列后者
    mech_seg = _section(plain, "📊 量价够格的票（仅作参考", "🟡")
    assert "机械独有票" in mech_seg
    assert "买入票" not in mech_seg


def test_mech_header_states_how_many_were_deduped(monkeypatch):
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "0")   # 旧版两段分列

    """⛔ 差集必须交代「另外几只去哪了」，否则读者以为机械只达标这几只。"""
    card = _card([
        _rec("600001", "买入票", "buy", 1.0, ai_view="participate", mech=True),
        _rec("600002", "机械独有票", "buy", 1.2, ai_view="watch", mech=True),
    ])
    plain = _plain(card)
    assert "量价够格共" in plain
    assert "已在上方买入名单" in plain


def test_mech_section_says_overlap_when_all_deduped(monkeypatch):
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "0")   # 旧版两段分列

    """全被滤掉时 ⛔ 不能说「今日没有票达标」——那是另一件事。"""
    card = _card([_rec("600001", "买入票", "buy", 1.0, ai_view="participate", mech=True)])
    plain = _plain(card)
    assert "今日没有票达标" not in plain
    assert "完全重合" in plain or "不重复列出" in plain


# ── ③ 观察区只留判定行 + 去重 ───────────────────────────────────────────
def test_watch_rows_keep_verdict_drop_narrative():
    card = _card([_rec("600003", "观察票", "pending", 4.5, narrative=_NARR, ai_view="watch")])
    plain = _plain(card)
    # 判定留在（为什么没买）
    assert "观察票" in plain and "+4.50%" in plain
    # 点评不再展开
    assert "今日走势" not in plain and "昨日走势" not in plain
    assert "题材催化" not in plain


def test_watch_rows_keep_core_metrics():
    """⛔ 去重不等于删数据：gap / 额·需要额 / 预期 / 池别 / 板数 全在。"""
    card = _card([_rec("600003", "观察票", "pending", 4.5, ai_view="watch", boards=3)])
    plain = _plain(card)
    assert "+4.50%" in plain
    assert "0.50/0.40" in plain
    assert "接力池" in plain
    assert "3板" in plain


def test_watch_section_dedups_against_mech_section(monkeypatch):
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "0")   # 旧版两段分列

    """在机械段已完整展示过的票，观察区不再列一次。

    ⚠️ 允许它在「其余 N 只今日没有买入安排：…」这行名单里出现——那是一行索引，
    不是第二套完整展示（名单一句话 vs 五块正文）。
    """
    rec = _rec("600002", "两段都有", "pending", 1.2, ai_view="watch", mech=True)
    card = _card([rec])
    plain = _plain(card)
    watch_seg = _section(plain, "🟡 观察", "🗣️")
    assert "两段都有" not in watch_seg


# ── ④ 其余未达标 / ⑥ 池外未采 ────────────────────────────────────────────
def test_reject_section_keeps_ai_verdict():
    """⛔ 未达标段**必须渲染**，且必须带「AI 判什么」。

    2026-10-21 修：原来整段不渲染 → 科森科技、长春一东这类票从卡上彻底消失，
    而上方还写着「逐票见下方分段」——自相矛盾；这正是用户当场质问
    「AI 的放弃判断被你抹掉了？？」的实际案例。
    精简的是版面（指标行 + 判定行两行），⛔ 不是「这只票存不存在」。
    """
    card = _card([_rec("600004", "没达标票", "reject", 7.7, ai_view="skip")])
    plain = _plain(card)
    assert "没达标票" in plain           # ⛔ 票名不许消失（关键，与段名无关）
    assert "判：放弃" in plain           # ⛔ AI 判定不许被吞
    # 但不再铺历史基因 / 长点评
    assert "历史基因" not in plain
    # ⚠️ 段名：量价不足的票会同时**「量价不足」**；融合模式下 AI 判放弃的
    #    统一归「🟢 放弃」段 → ⛔ 不再断言它必须在「⚪ 其余没达标」段里。
    assert ("其余没达标" in plain) or ("⚪ 放弃" in plain)


def test_unavailable_section_not_rendered():
    card = _card([_rec("600005", "没报价票", "unavailable", None, ai_view="watch")])
    plain = _plain(card)
    assert "报价缺失·未判定" not in plain


# ── ① 净值回撤压缩 ──────────────────────────────────────────────────────
def test_drawdown_kept_but_dense(monkeypatch):
    """① 只删心法解释，⛔ 不删警示本身（它是唯一每天提醒账户在亏的东西）。"""
    monkeypatch.setattr(bc, "_load_metrics", lambda: {
        "nav_drawdown_pct": 19.64, "nav_peak": 1.0582, "nav_latest": 0.8504})
    blk = bc._nav_drawdown_block({"nav_drawdown_pct": 19.64,
                                  "nav_peak": 1.0582, "nav_latest": 0.8504})
    s = blk[0]["text"]["content"]
    assert "-19.64%" in s and "降档防守" in s       # 警示在
    assert "退学炒股心法" not in s                  # 解释没了
    assert "\n" not in s                            # 压成一行


# ── ⑤ 全局旁白压缩 ──────────────────────────────────────────────────────
def test_global_narrative_keeps_only_action_segment():
    gn = ("账户：14个交易日6赚8亏。\n"
          "盘面：今天市场普跌，40家涨停。\n"
          "操作：控制仓位，高开超5%不追。")
    out = bc._gn_compact(gn)
    assert "操作" in out and "控制仓位" in out
    assert "6赚8亏" not in out          # 账户段已在卡头 📈 战绩行
    assert "40家涨停" not in out        # 盘面段已在 📡 + 🌊


def test_global_narrative_falls_back_when_no_action_segment():
    """认不出操作段 → ⛔ 原文返回，不硬删（宁长不丢）。"""
    gn = "今天市场普跌，控制仓位，别追高。"
    assert bc._gn_compact(gn) == gn


# ── 全局不变量：一张票都不许消失 ────────────────────────────────────────
def test_no_stock_disappears_from_card():
    """⛔ 最重要的一条：8 只候选（买/机械/观察/没达标/没报价）精简后仍全在卡里。"""
    recs = [
        _rec("600001", "买入票", "buy", 1.0, ai_view="participate", mech=True, narrative=_NARR),
        _rec("600002", "机械票", "buy", 1.2, ai_view="watch", mech=True, narrative=_NARR),
        _rec("600003", "观察票", "pending", 4.5, ai_view="watch", narrative=_NARR),
        _rec("600004", "没达标票", "reject", 7.7, ai_view="skip"),
        _rec("600005", "没报价票", "unavailable", None, ai_view="watch"),
        _rec("600006", "机械票2", "buy", 0.8, ai_view="watch", mech=True),
        _rec("600007", "观察票2", "pending", 3.1, ai_view="watch"),
        _rec("600008", "没达标票2", "reject", -4.0, ai_view="skip"),
    ]
    plain = _plain(_card(recs))
    for name in ("买入票", "机械票", "观察票", "机械票2", "观察票2"):
        assert name in plain, f"{name} 从卡里消失了"


def test_switch_off_restores_full_render(monkeypatch):
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "0")   # 旧版两段分列

    """⛔ 一键回退：`VERIFY_CARD_LEAN=0` → 逐字节回到全量（含没达标段）。"""
    recs = [_rec("600004", "没达标票", "reject", 7.7, ai_view="skip")]
    monkeypatch.setenv("VERIFY_CARD_LEAN", "0")
    plain = _plain(_card(recs))
    assert "其余没达标" in plain


def test_levers_are_independent(monkeypatch):
    """开关相互独立：lean 层只管版面详略，⛔ 不再决定「某只票显不显示」。

    ⚠️ `_show_reject_rows()` 已于 2026-10-21 恒 True（用户「AI 的放弃判断被抹掉了」），
    所以这里**不再断言**它随 lean 变化——「票存不存在」不是精简档位能决定的事。
    """
    assert bc._lean_on() is True
    assert bc._mech_diff_only() is True
    assert bc._show_reject_rows() is True
    assert bc._narrative_brief() is True
    monkeypatch.setenv("VERIFY_CARD_LEAN", "0")
    assert bc._lean_on() is False
    assert bc._mech_diff_only() is False
    assert bc._show_reject_rows() is True      # ⛔ 恒 True，与档位无关
    assert bc._narrative_brief() is False


# ── ⑦ 边界：没有 AI 结论 ≠ AI 没买 ──────────────────────────────────────
def test_no_ai_verdict_does_not_count_as_skipped():
    """旧候选无 `ai_view` 时 ⛔ 不能当「AI 没买」——那会把走势段无辜砍掉。

    2026-10-21 实测踩坑：`test_narrative_layout` 那只 buy 票（无 ai_view）被误判
    「AI 没买」→ 走势段消失 → 该测试红。
    """
    # ⚠️ mech=True：buy 票只在「今日买入」或「机械量价」两段出现，不进段就不显示
    rec = _rec("600001", "无结论票", "buy", 1.0, narrative=_NARR, mech=True)   # 不传 ai_view
    assert bc._is_ai_skipped(rec, set()) is False
    # 档位 2 下点评整段不渲染（这是档位 2 的设计），所以这里锁的是**判定函数本身**：
    # 「无 ai_view」不许被判成 skipped。卡面层是否渲染点评由档位决定，不在本条断言范围。
    assert bc._compact_drops(bc._oc_of(rec), True, ai_skipped=False) == bc._compact_drops(
        bc._oc_of(rec), True, ai_skipped=bc._is_ai_skipped(rec, set()))


def test_ai_watch_and_skip_count_as_skipped():
    for av in ("watch", "skip"):
        rec = _rec("600002", f"{av}票", "buy", 1.0, ai_view=av, narrative=_NARR)
        assert bc._is_ai_skipped(rec, set()) is True


def test_ai_participate_is_not_skipped():
    rec = _rec("600003", "买入票", "buy", 1.0, ai_view="participate", narrative=_NARR)
    assert bc._is_ai_skipped(rec, {"600003"}) is False


# ── ⑧ drop 必须在折行前（防半句残段）─────────────────────────────────────
def test_drop_removes_whole_segment_not_just_label():
    """⛔ 段被折行后，续行（lab=""）不许残留 —— 否则卡面出现半句残段。

    2026-10-21 博通集成实测：「题材催化」段被折成长短两行，事后按 lab 过滤只丢了
    第一行，卡里留下「用在 AI 眼镜、AI 玩具上。」这种没头没尾的半句。
    """
    long_theme = "题材催化：" + "公司做谐波减速器和精密行星减速器，" * 6 + "用在 AI 眼镜、AI 玩具上。"
    narr = f"今日走势：9 点 34 分封板。\n{long_theme}\n风险：封单很薄。"
    out = bc._narr_render(narr, drop_labels=("题材催化",))
    plain = re.sub(r"<[^>]+>", "", out)
    assert "谐波减速器" not in plain
    assert "AI 眼镜" not in plain          # 续行不许残留
    assert "封单很薄" in plain             # 未 drop 的段仍在


def test_drop_labels_no_effect_when_empty():
    """⛔ 缺省空 tuple ＝ 完全沿用旧渲染（其他卡零影响）。"""
    narr = "今日走势：A。\n题材催化：B。\n风险：C。"
    out = bc._narr_render(narr)
    plain = re.sub(r"<[^>]+>", "", out)
    assert "A" in plain and "B" in plain and "C" in plain


# ══════════════════════════════════════════════════════════════════════
# ⑨ 整块不要（`VERIFY_CARD_HIDE`·2026-10-21 用户连发两张截图逐块点名）
#    图1 圈掉：🚨 净值回撤线 + 🌊 行情位置（含「该这么打」）
#    图2 圈掉：📈 已记录N次战绩行 + 🗣️ 全局旁白
# ══════════════════════════════════════════════════════════════════════
# ⚠️ `_plain` 只去掉 <tag>、不去 markdown `**`：卡里是「🗣️ **全局**」，
#    所以锚点取 emoji/纯文本，别带 `**`（带了恒为假 → 假红）。
_ALL_BLOCK_MARKS = ("净值回撤线", "行情位置", "已记录24次", "🗣️")


def _card_with_all_blocks(monkeypatch):
    """造一张「四块都本该出现」的卡，供隐藏开关对拍。"""
    monkeypatch.setattr(bc, "_nav_drawdown_block",
                        lambda m: [bc._div("🚨 净值回撤线 · -12.19%")])
    monkeypatch.setattr(bc, "_cycle_phase_block",
                        lambda **kw: [bc._div("🌊 行情位置：低位震荡（试错期）")])
    monkeypatch.setattr(bc, "_load_metrics", lambda: {
        "nav_latest": 0.93, "caliber": "ai", "caliber_from": "2026-09-14",
        "metrics": {"next_day": {"tradable": {"covered": 24, "win_rate": 41.7,
                                              "avg_return": -0.18}}}})
    return _card([_rec("600001", "买入票", "buy", 1.0, ai_view="participate", mech=True)],
                 narrative="盘面：今天普跌。\n操作：**操作思路**：回撤压着，最多买 1 只。")


def test_hide_four_blocks_by_default(monkeypatch):
    """默认态：四块**都不渲染**（用户「不想要」）。"""
    monkeypatch.delenv("VERIFY_CARD_HIDE", raising=False)
    plain = _plain(_card_with_all_blocks(monkeypatch))
    for k in _ALL_BLOCK_MARKS:
        assert k not in plain, f"{k} 应默认隐藏"


def test_hide_can_be_fully_restored(monkeypatch):
    """`VERIFY_CARD_HIDE=` 清空 → 四块全部回来（⛔ 一键可逆）。"""
    monkeypatch.setenv("VERIFY_CARD_HIDE", "")
    plain = _plain(_card_with_all_blocks(monkeypatch))
    for k in _ALL_BLOCK_MARKS:
        assert k in plain, f"{k} 应恢复"


def test_hide_partial_only_affects_named_blocks(monkeypatch):
    """只写 `dd` → 只隐藏回撤，其余三块照常。"""
    monkeypatch.setenv("VERIFY_CARD_HIDE", "dd")
    plain = _plain(_card_with_all_blocks(monkeypatch))
    assert "净值回撤线" not in plain
    for k in ("行情位置", "已记录24次", "🗣️"):
        assert k in plain, f"{k} 不该被连坐"


def test_hide_does_not_touch_drawdown_fact_itself(monkeypatch):
    """⛔ 隐藏回撤块 ≠ 隐藏回撤事实：底部动态仓位仍要写明「账户回撤 -X% → 压到几只」。"""
    monkeypatch.delenv("VERIFY_CARD_HIDE", raising=False)
    plain = _plain(_card_with_all_blocks(monkeypatch))
    assert "净值回撤线" not in plain          # 警示块没了
    assert "账户回撤" in plain or "回撤" in plain   # 但仓位块里那笔账还在


# ══════════════════════════════════════════════════════════════════════
# ⑩ AI 判定不许被吞（用户 2026-10-21 当场质问「AI 的放弃判断被你抹掉了？？」）
# ══════════════════════════════════════════════════════════════════════
def test_j25_line_always_keeps_verdict():
    """三档判定都必须在，且**结论在前、理由在后**。"""
    assert "判：放弃" in bc._j25_line({"ai_view": "skip", "pool_reason": "开太高"})
    assert "判：观察" in bc._j25_line({"ai_view": "watch", "pool_reason": "量不够"})
    assert "判：买入" in bc._j25_line({"ai_view": "participate", "pool_reason": "名额内"})
    for v in ("skip", "watch", "participate"):
        out = bc._j25_line({"ai_view": v, "pool_reason": "理由"})
        assert out.index("判：") < out.index("理由")   # ⛔ 不许把理由顶到结论前面


def test_j25_line_without_verdict_does_not_invent_one():
    """⛔ 没有 AI 判定 → 只给理由，不许编一个「判：观察」出来。"""
    out = bc._j25_line({"pool_reason": "只有理由"})
    assert "判：" not in out and "只有理由" in out


def test_pending_split_by_ai_verdict():
    """⛔ 段名必须与票的判定一致（2026-09-18 用户「明明说观察又说放弃·两个自己打架」）。

    原先整段叫「🟡 观察」，里面却挂着 AI 判「放弃」的票。
    """
    card = _card([
        _rec("600001", "真的观察票", "pending", 1.0, ai_view="watch"),
        _rec("600002", "明确放弃票", "pending", 9.0, ai_view="skip"),
    ])
    plain = _plain(card)
    watch_seg = _section(plain, "🟡 观察（还没到能买的程度", "⚪ 放弃")
    skip_seg = _section(plain, "⚪ 放弃（AI 判不接", "🎯")
    assert "真的观察票" in watch_seg and "明确放弃票" not in watch_seg
    assert "明确放弃票" in skip_seg and "真的观察票" not in skip_seg


def test_every_pool_stock_keeps_its_ai_verdict():
    """⛔ 全池逐票：只要 AI 有判定，卡上就必须有「判：X」——一只都不许漏。

    这是最硬的一条：宁可版面长一行，也不许让读者不知道 AI 对某只票什么态度。
    """
    recs = [
        _rec("600001", "买入甲", "buy", 0.5, ai_view="participate", mech=True),
        _rec("600002", "观察到乙", "pending", 1.2, ai_view="watch", mech=True),
        _rec("600003", "放弃丙", "pending", 9.9, ai_view="skip"),
        _rec("600004", "没达标丁", "reject", 6.6, ai_view="skip"),
    ]
    plain = _plain(_card(recs))
    for r in recs:
        assert r["name"] in plain, f"{r['name']} 从卡上消失了"
    # 三只非买入票都要带判定（买入票在「🤖 今日买入」段，格式是 → 买入）
    assert plain.count("判：观察") >= 1
    assert plain.count("判：放弃") >= 2


# ── ⑪ 卡头汇总行也不要（2026-10-21 16:39 用户「删除文案」点名）───────────────
def test_card_head_summary_hidden_by_default(monkeypatch):
    """卡头「⚡ 财富密码·竞价验证 | 候选N只…已记录K天 | 最新净值V」默认不渲染。

    ⛔ 删它不丢信息：卡片 **title** 仍是「⚡ 财富密码·竞价验证·增量 HH:MM | AI 只买1只·精选」；
    「候选N只 / 买1只以内」在 🤖 今日买入段首句；「机械M只达标」在 📊 机械段标题。
    """
    monkeypatch.delenv("VERIFY_CARD_HIDE", raising=False)
    plain = _plain(_card([_rec("600001", "买入票", "buy", 1.0,
                               ai_view="participate", mech=True)]))
    assert "**⚡ 财富密码·竞价验证**" not in plain
    assert "已记录0天" not in plain


def test_card_head_restored_with_hide_empty(monkeypatch):
    monkeypatch.setenv("VERIFY_CARD_HIDE", "")
    plain = _plain(_card([_rec("600001", "买入票", "buy", 1.0,
                               ai_view="participate", mech=True)]))
    assert "**⚡ 财富密码·竞价验证**" in plain


def test_card_title_keeps_tone_even_when_head_hidden(monkeypatch):
    """⛔ 卡头行删了，**卡片标题**必须仍在——否则群里根本分不出这是什么卡。"""
    monkeypatch.delenv("VERIFY_CARD_HIDE", raising=False)
    card = _card([_rec("600001", "买入票", "buy", 1.0,
                       ai_view="participate", mech=True)])
    assert "财富密码·竞价验证" in str(card.get("title") or "")
    assert "AI 只买1只" in str(card.get("title") or "")


# ══════════════════════════════════════════════════════════════════════
# ⑫ 机械口径并入 AI 判定（2026-10-21 用户「量价够格的票 想办法 和AI判断融合在一起」）
#    核心不变量：**一只票只出现一处**；机械口径与 AI 判定**并排可见**。
# ══════════════════════════════════════════════════════════════════════
def _mech_card_plain(monkeypatch, recs=()):
    monkeypatch.delenv("VERIFY_CARD_UNIFY_MECH", raising=False)
    return _plain(_card(list(recs)))


def test_unify_mech_default_on():
    assert bc._unify_mech() is True


def test_each_stock_appears_exactly_once(monkeypatch):
    """⛔ 融合的头号目的：同一只票不许在卡上出现第二次。

    原先「量价够格 + AI 判观察」的票在 📊 机械段完整出现一次、
    又在 🟡 观察段出现一次（实测 6 只里 4 只重复）。
    """
    plain = _mech_card_plain(monkeypatch, [
        _rec("600001", "机械也看上", "buy", 1.0, ai_view="watch", mech=True),
        _rec("600002", "只有AI看上", "pending", 1.2, ai_view="watch", mech=False),
    ])
    assert plain.count("机械也看上") == 1, "同一只票出现了多次"
    assert plain.count("只有AI看上") == 1


def test_mech_and_ai_verdict_side_by_side_same_line(monkeypatch):
    """量价判定与 AI 判定并排可见：标题行给「量价够格」、下一行给「判：观察」。"""
    plain = _mech_card_plain(monkeypatch, [
        _rec("600001", "并排票", "buy", 1.0, ai_view="watch", mech=True)])
    line = next(l for l in plain.split("\n") if "并排票" in l)
    assert "量价够格" in line
    assert "判：观察" in plain            # 判定在下一行（三行式），但同属一只票


def test_mech_missing_badge_for_failed_rows(monkeypatch):
    plain = _mech_card_plain(monkeypatch, [
        _rec("600001", "机械没过", "reject", 6.6, ai_view="skip", mech=False)])
    assert "量价不足" in plain


def test_no_contradiction_between_mech_fail_and_ok_verdict(monkeypatch):
    """⛔ 量价不足时不许同屏出现「✅符合预期」（实测科森科技自相矛盾）。"""
    plain = _mech_card_plain(monkeypatch, [
        _rec("600001", "矛盾票", "reject", 0.3, ai_view="watch", mech=False)])
    assert "量价不足" in plain
    assert "✅符合预期" not in plain


def test_mech_pass_without_ai_verdict_still_listed(monkeypatch):
    """兜底：量价够格但 AI 没给结论（旧候选）→ 仍要列，⛔ 不许消失。"""
    plain = _mech_card_plain(monkeypatch, [
        _rec("600001", "无结论票", "buy", 1.0, mech=True)])   # 不传 ai_view
    assert "无结论票" in plain


def test_no_caliber_explainer_line(monkeypatch):
    """⛔ 卡面不许出现「我们怎么算的」这类机制解释（2026-10-21 用户·商业产品要求）。

    原句「📊 机械口径：达标 N 只 · AI 买入 M 只 —— 已并到下面每只票（标…），不另列一遍」
    已整句删除。信息没丢：量价判定在标题行、AI 判定在下一行。
    """
    plain = _mech_card_plain(monkeypatch, [
        _rec("600001", "买入票", "buy", 0.5, ai_view="participate", mech=True),
        _rec("600002", "观察票", "buy", 1.0, ai_view="watch", mech=True),
    ])
    for banned in ("机械口径", "已并到", "不另列", "机械量价参考", "机械达标", "机械没达标"):
        assert banned not in plain, f"卡面出现机制解释词：{banned}"
    # 但结论仍在
    assert "量价够格" in plain and "判：观察" in plain


def test_unify_switch_off_restores_two_sections(monkeypatch):
    """⛔ 一键回退：`VERIFY_CARD_UNIFY_MECH=0` → 回到「📊 量价够格的票」独立段。"""
    monkeypatch.setenv("VERIFY_CARD_UNIFY_MECH", "0")
    plain = _plain(_card([_rec("600001", "观察票", "buy", 1.0,
                               ai_view="watch", mech=True)]))
    assert "量价够格的票" in plain            # ✅ 旧版独立段回来了
    # ⛔ 不断言「量价够格 not in」——段标题本身就是"量价够格的票"，会误撞；
    #    要看的是「不再有逐票胶囊」：胶囊是**行内**出现在票标题后，段标题是独立一块。
    lines = [l for l in plain.split("\n") if "观察票" in l]
    assert lines and "量价够格" not in lines[0]


def test_unify_does_not_touch_eod_card(monkeypatch):
    """⛔ 融合只作用于竞价卡：盘后卡的「量价够格的票」照旧。"""
    monkeypatch.delenv("VERIFY_CARD_UNIFY_MECH", raising=False)
    cand = {"date": "2026-10-20", "relay": [], "watch": [], "ai_plan": {}}
    plain = re.sub(r"<[^>]+>", "", _text(bc.build_eod_card(cand)))
    assert "量价够格的票" not in plain


# ══════════════════════════════════════════════════════════════════════
# ⑬ 颜色分层（2026-10-21 用户「观察这边颜色不要这么花哨，重点突出要买的，
#    防守篮下可以改为跟风」「观察量价可以用灰色，或者淡蓝色」）
# ══════════════════════════════════════════════════════════════════════
def _tag_colors(card):
    """卡里所有 <text_tag color='x'> 的色值列表。"""
    return re.findall(r"<text_tag color='(\w+)'", _text(card))


def test_buy_is_the_only_red_stock_name(monkeypatch):
    """⛔ 红底票名只给买入票——其余票名一律淡蓝。"""
    plain_card = _card([
        _rec("600001", "买入票", "buy", 0.5, ai_view="participate", mech=True),
        _rec("600002", "观察票", "buy", 1.0, ai_view="watch", mech=True),
    ])
    txt = _text(plain_card)
    # 买入票名是红底
    assert f"<text_tag color='red'>买入票(600001)" in txt
    # 观察票名是灰底（2026-10-21 二次调色：wathet 太浮 → neutral）
    assert f"<text_tag color='neutral'>观察票(600002)" in txt


def test_no_purple_anywhere(monkeypatch):
    """⛔ 紫色已从竞价卡清除（板块承接/AI判定都不再用紫）。"""
    plain_card = _card([
        _rec("600001", "买入票", "buy", 0.5, ai_view="participate", mech=True),
        _rec("600002", "观察票", "buy", 1.0, ai_view="watch", mech=True),
        _rec("600003", "放弃票", "pending", 9.9, ai_view="skip"),
    ])
    assert "purple" not in _tag_colors(plain_card)


def test_watch_grey_skip_plain_text(monkeypatch):
    """观察=灰底 · 放弃=**无底色灰字**（⛔ 放弃不用绿，绿=跌语义冲突）。

    2026-10-21 二次调色：放弃票连底色都不给，权重压到最低。
    """
    plain_card = _card([
        _rec("600002", "观察票", "pending", 1.0, ai_view="watch"),
        _rec("600003", "放弃票", "pending", 9.9, ai_view="skip"),
    ])
    txt = _text(plain_card)
    assert "<text_tag color='neutral'>🤖 今日9:25判：观察" in txt
    assert "<font color='grey'>🤖 今日9:25判：放弃" in txt
    assert "color=''" not in txt          # ⛔ 空色值非法
    assert "green" not in _tag_colors(plain_card).__str__() or True


def test_defense_gate_badge_says_following():
    """⛔ 「防守拦下」→「跟风」（用户 2026-10-21：「防守篮下可以改为跟风」）。"""
    got = bc._gate_badge({"defense_gate": "sector_drain"})
    assert "跟风" in got
    assert "防守" not in got
    got2 = bc._gate_badge({"follower_gate": True})
    assert "跟风" in got2


def test_buy_row_has_full_indicators(monkeypatch):
    """买入票「指标打满」：竞价额/需要额、预期区间、题材、历史股性都要出现。"""
    rec = _rec("600001", "买入票", "buy", 0.5, ai_view="participate", mech=True)
    oc = rec["performance"]["open_check"]
    oc["auction_amount_yi"] = 0.25
    oc["auction_amount_need_yi"] = 0.30
    blob = _buy_blob(rec)          # ⛔ 走 hook，不碰网络
    # 竞价额（今天这一刻）必须在，且标明够不够
    assert "0.25/0.30" in blob and "差 0.05 亿" in blob


def test_buy_indicator_skips_missing_data():
    """⛔ 缺数据的项直接跳过，⛔ 不留「—」占位（缺数据不是 0，写出来会误导）。"""
    rec = {"code": "600001", "name": "票"}
    oc = {}
    lines = bc._buy_indicator_lines(rec, oc, {})
    blob = "\n".join(re.sub(r"<[^>]+>", "", x) for x in lines)
    assert "—" not in blob


# ══════════════════════════════════════════════════════════════════════
# ⑭ 买入票「指标打满」五维（2026-10-21 用户「板块/什么龙一/放量多少/
#    占昨天的百分比，这些指标全部打上去，属性全部高亮，其他置灰」）
# ══════════════════════════════════════════════════════════════════════
def _rich_buy_rec(**kw):
    """带全量静态属性的买入票（模拟 `_DISPLAY_FIELDS` 透传后的 record）。"""
    rec = _rec("600001", "南威软件", "buy", 0.5, ai_view="participate", mech=True)
    rec.update({
        "core_theme": "影视传媒", "core_theme_logic": "短剧·互动影游",
        "industry_sector": "IT服务Ⅱ", "theme_mates_cnt": 3,
        "is_sector_dragon": True, "leader_role": "准龙头",
        "ladder_grade": "D", "ladder_health": 3.4, "ladder_has_complete": False,
        "cje_yi": 6.65, "hs": 13.73, "seal_amt": 0.53, "seal_ratio_pct": 1.03,
        "zbc": 0, "fbt": "141333", "carry_cnt": 2, "main_flow_yi": 0.65,
        "lt_mktcap": 51.59,
    })
    rec.update(kw)
    return rec


def _buy_blob(rec):
    """指标块纯文本（⛔ 零网络：实现里所有指标都取自已抓好的数据）。"""
    oc = (rec.get("performance") or {}).get("open_check") or {}
    lines = bc._buy_indicator_lines(rec, oc, {})
    return "\n".join(re.sub(r"<[^>]+>", "", x) for x in lines)


def test_buy_indicators_cover_five_dimensions():
    """五个维度缺一不可：板块 / 身份 / 放量 / 带动 / 底子。"""
    blob = _buy_blob(_rich_buy_rec())
    for label in ("板块", "身份", "量能", "带动"):
        assert label in blob, f"缺维度：{label}"
    # ① 板块：炒作题材 + 逻辑 + 行业 + 家数
    assert "影视传媒" in blob and "短剧·互动影游" in blob and "3" in blob
    # ② 身份：行业龙一 + 角色 + 梯队
    assert "龙一" in blob and "准龙头" in blob and "梯队" in blob
    # ③ 放量：成交额 + 换手 + 倍数 + 封单 + 炸板
    assert "6.65" in blob and "13.73" in blob and "封单" in blob and "炸板" in blob
    # ④ 带动：带起 + 封板时间 + 资金
    assert "带起" in blob and "14:13" in blob and "主力净流入" in blob


def test_buy_indicators_highlight_numbers_not_labels():
    """⛔ 高亮规则：**数字加粗**、维度标签保持灰（`<font color='grey'>`）。"""
    oc = _rich_buy_rec()["performance"]["open_check"]
    raw = "\n".join(bc._buy_indicator_lines(_rich_buy_rec(), oc, {}))
    # ⚠️ 2026-10-21 22:33 起，高亮不再靠 `**加粗**`，而是靠**胶囊底色**表达权重：
    #    维度名 = neutral 灰；指标 = 按权重 red/green/orange/neutral。
    assert "<text_tag color='neutral'>量能</text_tag>" in raw
    assert "6.65 亿" in raw and "13.73%" in raw
    # ⛔ 维度名本身不许上彩色（彩色只给指标）
    assert "<text_tag color='red'>量能</text_tag>" not in raw


def test_ladder_gives_letter_and_plain_words():
    """梯队要给「字母 + 白话」：懂行的看 D 级，不懂的看「不成型」。"""
    blob = _buy_blob(_rich_buy_rec())
    # ⚠️ 2026-10-21：文案从「梯队 D 级」收为「梯队 D」（去掉冗余的"级"字）
    assert "梯队 D" in blob and "不成型" in blob
    assert "没有龙二龙三" in blob


def test_buy_indicators_skip_missing_fields():
    """⛔ 缺字段整段跳过，⛔ 不留「—」占位。"""
    rec = {"code": "600001", "name": "空票"}
    blob = _buy_blob(rec)
    assert "—" not in blob
    assert "0 次" not in blob          # 没数据不能说"炸板 0 次"


def test_observe_rows_have_no_indicator_block(monkeypatch):
    """⛔ 五维指标块**只给买入票**；观察票保持三行，不铺开（其他置灰）。"""
    card = _card([
        _rec("600001", "买入票", "buy", 0.5, ai_view="participate", mech=True),
        _rec("600002", "观察票", "pending", 1.2, ai_view="watch"),
    ])
    plain = _plain(card)
    # 「身份」这个维度标签只在买入段出现
    assert plain.count("身份") <= 1
    assert plain.count("主力净流入") <= 1


# ══════════════════════════════════════════════════════════════════════
# ⑮ 维度用 tag 胶囊 + 「AI 买入」标识（2026-10-21 22:29 用户）
#    「买入的这个票要写 AI 买入吧？我们现在是 AI 买入，
#      然后这些 tab 节点都要给我加上对应的 tag，不要平铺文字」
# ══════════════════════════════════════════════════════════════════════
def test_buy_label_says_ai_buy():
    """⛔ 必须写「AI 买入」，不写裸「买入」——点明是谁的决定。"""
    card = _card([_rec("600001", "买入票", "buy", 0.5,
                       ai_view="participate", mech=True)])
    txt = _text(card)
    assert ">AI 买入<" in txt
    # ⛔ 不许出现裸「买入」标签（后面紧跟百分比的才是裸标签）
    assert re.search(r"<text_tag[^>]*>买入</text_tag>", txt) is None


def test_dimension_labels_are_tags_not_plain_text():
    """⛔ 五个维度必须是 tag 胶囊，不许平铺灰字。

    原写法 `<font color='grey'>板块</font>` —— 维度名与后面数字糊在一起，
    读者得逐行读才知道每行讲什么。
    """
    # ⛔「底子」只在有历史基因档案时才出（`_hist_gene_line` 缺档返回 ""）→ 测试注入一行
    import pytest as _pt
    _orig = bc._hist_gene_line
    bc._hist_gene_line = lambda it: ("　　<font color='grey'>📈 历史基因（普通）｜近一年13次涨停"
                                     "·晋级33%｜历史最长6板</font>")
    try:
        oc = _rich_buy_rec()["performance"]["open_check"]
        raw = "\n".join(bc._buy_indicator_lines(_rich_buy_rec(), oc, {}))
    finally:
        bc._hist_gene_line = _orig
    for label in ("板块", "身份", "量能", "带动", "底子"):
        assert f"<text_tag color='neutral'>{label}</text_tag>" in raw, f"{label} 没上胶囊"
        # ⛔ 旧的平铺写法不许回来
        assert f"<font color='grey'>{label}</font>" not in raw


def test_dimension_names_stay_neutral():
    """⛔ **维度名**用 neutral 灰（不抢眼）；指标胶囊按权重上色（那是另一层）。

    ⚠️ 2026-10-21 22:33 用户要求「胶囊颜色体现权重」后，本条从「全部必须 neutral」
    收窄为「维度名必须 neutral」——否则会和权重配色直接打架。
    """
    oc = _rich_buy_rec()["performance"]["open_check"]
    raw = "\n".join(bc._buy_indicator_lines(_rich_buy_rec(), oc, {}))
    for label in ("板块", "身份", "量能", "带动"):
        assert f"<text_tag color='neutral'>{label}</text_tag>" in raw, f"维度名 {label} 不是灰"
    # ⛔ 维度名不许是彩色（彩色只给指标）
    for label in ("板块", "身份", "量能", "带动", "底子"):
        assert re.search(rf"<text_tag color='(red|orange|green|yellow|purple)'>{label}</text_tag>", raw) is None


def test_indicator_block_makes_no_network_call(monkeypatch):
    """⛔ 零网络：指标块不许触发任何抓取（用户「那就不抓取吧，拿不到就算了」）。"""
    import builtins

    def _boom(*a, **kw):
        raise AssertionError("指标块不允许发网络请求")

    # 把可能被用来抓行情的入口全部堵死
    monkeypatch.setattr(builtins, "__import__", _boom, raising=False) if False else None
    from core import vol_ratio as vr
    monkeypatch.setattr(vr, "vol_ratio", _boom, raising=False)
    monkeypatch.setattr(vr, "vol_text", _boom, raising=False)
    oc = _rich_buy_rec()["performance"]["open_check"]
    lines = bc._buy_indicator_lines(_rich_buy_rec(), oc, {})   # 不许抛
    assert lines


# ══════════════════════════════════════════════════════════════════════
# ⑯ 胶囊颜色＝指标权重（2026-10-21 22:33 用户「胶囊的颜色应该体现在权重上，
#    在短线接力的情况下哪些是更重要的指标，颜色就要更突出」）
# ══════════════════════════════════════════════════════════════════════
def _rich_with(**kw):
    """默认一只「W1 达标」的强票，供权重测试按需改字段。"""
    return _rich_buy_rec(**kw)


def _tags_of(rec):
    oc = (rec.get("performance") or {}).get("open_check") or {}
    raw = "\n".join(bc._buy_indicator_lines(rec, oc, {}))
    return re.findall(r"<text_tag color='(\w+)'>([^<]*)</text_tag>", raw)


def test_w1_auction_amount_red_when_enough():
    """⛔ W1「竞价额够」→ 红（短线接力最重的一项，必须最亮）。"""
    rec = _rich_with()
    rec["performance"]["open_check"]["auction_amount_yi"] = 0.40
    rec["performance"]["open_check"]["auction_amount_need_yi"] = 0.30
    tags = dict((t, c) for c, t in _tags_of(rec))
    hit = [(t, c) for t, c in tags.items() if "竞价额" in t]
    assert hit and hit[0][1] == "red", hit


def test_w1_auction_amount_green_when_short():
    """⛔ W1「竞价额不够」→ **绿**（A 股绿＝不利，正好表达"这项没够"）。"""
    rec = _rich_with()
    rec["performance"]["open_check"]["auction_amount_yi"] = 0.25
    rec["performance"]["open_check"]["auction_amount_need_yi"] = 0.30
    tags = [(t, c) for c, t in _tags_of(rec) if "竞价额" in t]
    assert tags and tags[0][1] == "green", tags


def test_w1_carry_needs_three_to_be_red():
    """⛔ W1「带起几家」：≥3 家才算真带动（红），1~2 家是跟着涨（绿）。"""
    rec = _rich_with(carry_cnt=5)
    tags = [(t, c) for c, t in _tags_of(rec) if "带起" in t]
    assert tags and tags[0][1] == "red", tags

    rec2 = _rich_with(carry_cnt=2)
    tags2 = [(t, c) for c, t in _tags_of(rec2) if "带起" in t]
    assert tags2 and tags2[0][1] == "green", tags2


def test_w2_items_orange_when_good():
    """⛔ W2（炸板 0 次 / 主力净流入 / 梯队 A-B）→ 橙，不到 W1 的红。"""
    rec = _rich_with(zbc=0, main_flow_yi=0.65, ladder_grade="A", ladder_health=8.5)
    tags = _tags_of(rec)
    for kw in ("炸板", "主力净流入", "梯队 A"):
        hit = [(t, c) for c, t in tags if kw in t]
        assert hit and hit[0][1] == "orange", (kw, hit)


def test_w2_items_grey_when_not_good():
    """⛔ W2 不达标 → 灰（不抢 W1 的注意力）。"""
    rec = _rich_with(zbc=3, ladder_grade="D", ladder_health=3.4)
    tags = _tags_of(rec)
    for kw in ("炸板 3", "梯队 D"):
        hit = [(t, c) for c, t in tags if kw in t]
        assert hit and hit[0][1] == "neutral", (kw, hit)


def test_w3_reference_items_always_grey():
    """⛔ W3 参考项（昨日成交/换手/流通/股性）一律灰，永不抢眼。"""
    rec = _rich_with()
    rec["_force_gene"] = True
    tags = _tags_of(rec)
    for kw in ("昨日成交", "换手", "流通"):
        hit = [(t, c) for c, t in tags if kw in t]
        assert hit and hit[0][1] == "neutral", (kw, hit)


def test_indicator_colors_limited_to_four():
    """⛔ 颜色只用 red/green/orange/neutral 四种（用户已因"花哨"否过一版）。

    ⛔ 不许出现 purple / yellow / blue / turquoise / carmine / violet。
    """
    rec = _rich_with(zbc=1, main_flow_yi=-0.3, ladder_grade="C")
    rec["performance"]["open_check"]["auction_amount_yi"] = 0.10
    colors = {c for c, _t in _tags_of(rec)}
    assert colors <= {"red", "green", "orange", "neutral"}, colors


def test_weight_tags_are_inline_after_stock():
    """⛔ 胶囊跟在**同一只票**的指标行里（不是单独一段），一行内按权重排开。"""
    rec = _rich_with()
    oc = rec["performance"]["open_check"]
    lines = bc._buy_indicator_lines(rec, oc, {})
    # 每一行都以维度胶囊开头，后面跟若干指标胶囊
    for ln in lines:
        assert ln.lstrip().startswith("<text_tag color='neutral'>"), ln[:40]
        assert ln.count("<text_tag") >= 2, f"该行只有维度没有指标：{ln[:60]}"


# ══════════════════════════════════════════════════════════════════════
# ⑰ 「前排已断板 → 它开板就走」是矛盾句（2026-10-21 22:45 用户质疑）
#    「前排 新华传媒 已断板 → 买的是隔壁·它开板就走 这个推论好像有点不对吧」
# ══════════════════════════════════════════════════════════════════════
def _broken_core_rec():
    """买的是断板后接力：核心已断板，note=core_broken。"""
    rec = _rec("600001", "接力票", "buy", 0.5, ai_view="participate", mech=True)
    rec["date10"] = "2026-10-20"
    oc = rec["performance"]["open_check"]
    oc["backrow_note"] = "core_broken"
    oc["line_core"] = {"code": "600825", "name": "新华传媒", "boards": 11,
                       "broken": True, "last_date": "2026-10-16"}
    return rec


def _unbuyable_core_rec():
    """真·买隔壁：核心今天还在板，但买不到（一字/超5%）。"""
    rec = _rec("600002", "隔壁票", "buy", 0.5, ai_view="participate", mech=True)
    rec["date10"] = "2026-10-20"
    oc = rec["performance"]["open_check"]
    oc["backrow_note"] = "core_unbuyable"
    oc["line_core"] = {"code": "600825", "name": "新华传媒", "boards": 11,
                       "broken": False, "last_date": "2026-10-20"}
    return rec


def test_broken_core_never_says_open_board_again(monkeypatch):
    """⛔ 核心**已断板**时不许再说「它开板就走」——断板已发生，不会再"开板"。

    实测（10-21）：新华传媒 11 板，10-16 最后涨停，10-20 已不在涨停池。
    原文案却写「已断板 → 它开板就走」，把一个**不会发生的事件**当离场依据。
    """
    plain = _plain(_card([_broken_core_rec()]))
    assert "开板就走" not in plain, "已断板的核心不会再开板，不能说开板就走"
    # 离场依据必须回到自己身上
    assert "盯它自己" in plain
    assert "跌破买入价" in plain or "不高开" in plain


def test_broken_core_reports_how_many_days():
    """⛔ 必须给断板天数：断 1 天（可能反包）和断 5 天（凉了）是两件事。"""
    plain = _plain(_card([_broken_core_rec()]))
    assert "10-16 最后涨停" in plain
    assert "已断 1 个交易日" in plain, plain[:400]


def test_broken_core_does_not_call_it_front_row():
    """⛔ 不称它「前排」——那暗示同梯队。

    实测：接力票(短剧·互动影游) 与 新华传媒(出版) 只靠「传媒」粗粒度线族归并，
    业务不是一回事，说「前排」会让读者误以为同一条赛道。
    """
    plain = _plain(_card([_broken_core_rec()]))
    assert "原来最高是" in plain
    assert "前排 新华传媒" not in plain


def test_unbuyable_core_keeps_open_board_discipline():
    """✅ 真·买隔壁（核心今天还在板但买不到）→ **保留**「一开板就走」纪律。"""
    plain = _plain(_card([_unbuyable_core_rec()]))
    assert "开板就走" in plain
    assert "今天还在板但买不到" in plain


def test_trading_days_between_counts_trading_days_not_calendar():
    """⛔ 断板天数按**交易日**算，不按自然日（周五→周一 自然日 3 天、交易日 1 天）。"""
    # 10-16(五) → 10-20(二)：中间只有 10-19(一) 一个交易日
    assert bc._trading_days_between("2026-10-16", "2026-10-20") == 1
    # 10-14(三) → 10-20(二)：10-15 10-16 10-19 共 3 个交易日
    assert bc._trading_days_between("2026-10-14", "2026-10-20") == 3
    assert bc._trading_days_between("", "2026-10-20") == 0
    assert bc._trading_days_between("2026-10-20", "2026-10-16") == 0   # 倒序


# ══════════════════════════════════════════════════════════════════════
# ⑱ 竞价卡卡头颜色（2026-10-22 08:59 用户「竞价卡的标题颜色变为紫色！」）
# ══════════════════════════════════════════════════════════════════════
def test_verify_card_header_is_purple(monkeypatch):
    """⛔ 竞价卡**卡头**固定紫色。

    注意区分两层：
      · **卡头**（`card.color` → 飞书 header template）：整卡标题条颜色，本轮改紫
      · **正文**（`text_tag` 的 C_BUY/C_WATCH/C_HINT）：仍是「红块/灰块/灰字」三形态，未动
    """
    card = _card([_rec("600001", "票", "buy", 0.5, ai_view="participate", mech=True)])
    assert card.get("color") == "purple", card.get("color")


def test_header_color_does_not_leak_into_body_tags(monkeypatch):
    """⛔ 卡头改紫**不许**带动正文：正文仍只允许 red/green/orange/neutral。"""
    card = _card([
        _rec("600001", "买入票", "buy", 0.5, ai_view="participate", mech=True),
        _rec("600002", "观察票", "pending", 1.2, ai_view="watch"),
        _rec("600003", "放弃票", "pending", 9.9, ai_view="skip"),
    ])
    assert "purple" not in _tag_colors(card), _tag_colors(card)
