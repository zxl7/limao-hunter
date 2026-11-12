import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wealth_code"))
from core import two_pool, pool_admit, pool_call
from pipelines import build_cards


def row(**kw):
    return {"code": "000001", "name": "测试核心", "boards": 3, "core_theme": "测试题材",
            "is_sector_dragon": True, "dragon_rank": 1, "seal_amt": 6., "lt_mktcap": 100.,
            "fbt": "092500", "zbc": 0, "hs": 1., "cje_yi": 12., **kw}


@pytest.mark.parametrize("kw,kind", [
    ({"is_capacity_core": True}, "容量核心"),
    ({"is_one_word": True}, "一字核心"),
    ({"boards": 6}, None),
    ({"style_tag": "容量"}, None),
    ({"is_capacity_core": True, "is_sector_dragon": False}, None),
    # ⛔ 2026-11-04 用户「观察池把一字的龙头也放进来一下」：
    #    一字板封单不厚 → 由「丢弃(None)」改为「入池，分组一字龙头，仍不可买」。
    #    理由：一字板结构上就是封死量小，封单数据经常拿不到，拿不到 ≠ 封得不厚。
    ({"is_one_word": True, "seal_amt": .2}, "一字龙头"),
    ({"is_one_word": True, "code": "688001"}, None),
    ({"is_one_word": True, "name": "ST测试"}, None),
    ({"is_one_word": True, "core_theme": ""}, None),
    ({"is_one_word": True, "lt_mktcap": None}, "一字龙头"),   # 封单数据缺失不再丢弃
])
def test_watch_qualification(kw, kind):
    assert two_pool.watch_kind(row(**kw)) == kind


def test_finalize_does_not_rescue_risk_rejected():
    source = [row(watch_only=True), row(code="000002", boards=1), row(code="000003", is_capacity_core=True)]
    before = copy.deepcopy(source)
    relay, watch, diag = two_pool.finalize([], source, stage="test")
    assert relay == []
    assert [x["code"] for x in watch] == ["000003"]
    assert watch[0]["buyable"] is False
    assert len(diag["excluded"]) == 2
    assert source == before


def test_switch_rollback(monkeypatch):
    monkeypatch.setenv("ENABLE_TWO_POOL_CONTRACT", "0")
    r, w = [row()], [row(code="000002")]
    assert two_pool.finalize(r, w, stage="test")[:2] == (r, w)


def test_admit_never_promotes_anchor():
    anchor = two_pool.mark_watch(row(), "容量核心")
    relay, watch, _ = pool_admit.admit([], [anchor])
    assert not relay
    assert watch[0]["participation_eligibility"] == "observe_only"


def test_observation_cannot_unfreeze():
    anchor = two_pool.mark_watch(row(high_win_gate=["情绪"]), "容量核心")
    result = pool_call.call_pool({"date": "2026-11-03", "watch": [anchor], "relay": [], "market_max_boards": 3},
        {"000001": {"auction_change_pct": 2., "auction_amount_yi": 10.}},
        plan={}, stance="attack", market_open={"ready": True}, zt_series={}, zt_days=[])
    assert not result["meta"]["participate"]


def test_forecast_action_render_removed():
    assert build_cards._rfc_line({"forecast": {"verdict": "可等", "gap_median": 8}}) == ("", "")


def test_account_guard_ignores_focus_and_frozen_participation():
    from core import ai_caliber
    decision = {"view": "participate", "bias": "focus", "participation_eligibility": "observe_only"}
    can_enter, _, meta = ai_caliber.decide(decision, 2., auction_amount_yi=100., need_yi=1.)
    assert not can_enter
    assert meta["ai_view"] == "watch"


def test_verify_without_ai_cannot_buy_observation(monkeypatch):
    from core import ai_caliber
    from pipelines import pipeline_verify
    anchor = two_pool.mark_watch(row(), "容量核心")
    monkeypatch.setattr(ai_caliber, "load_ai_decisions", lambda *a: None)
    result = pipeline_verify.run_verify(
        candidates={"date": "2026-11-03", "relay": [], "watch": [anchor], "environment": {}},
        quotes={"000001": {"auction_change_pct": 2., "auction_price": 10., "auction_amount_yi": 10.}},
        next_close_map={}, persist=False, out_nav=False, sector_support_map={})
    assert not result["payload"]["records"][0]["performance"]["open_check"]["can_enter"]


def test_eod_renders_one_word_anchor_without_trade_promises():
    anchor = two_pool.mark_watch(row(is_one_word=True, frozen=True), "一字核心")
    card = build_cards.build_eod_card({"date": "2026-11-03", "relay": [], "watch": [anchor],
                                      "diagnostics": {}, "ai_plan": {}})
    text = str(card)
    # ⛔ 2026-11-04：`一字核心` 已进 `_WATCH_GROUP_PLAIN` 白名单，
    #    显示层翻成白话「一字封死·厚封单」，字面词不再出现（白名单本就是这个用途）。
    #    断言改为白话，原意不变：必须写明「买不进、不参与」，且不得出现交易承诺。
    assert "测试核心" in text
    assert "一字封死" in text
    assert "仅作板块情绪参照，不参与" in text
    assert "全部转观察池" not in text
    assert "竞价转强才解冻" not in text


@pytest.mark.parametrize("group", list(build_cards._WATCH_GROUP_PLAIN))
def test_eod_renders_every_registered_watch_group(group):
    anchor = row(watch_group=group, participation_eligibility="observe_only", buyable=False)
    card = build_cards.build_eod_card({"date": "2026-11-04", "relay": [], "watch": [anchor],
                                      "diagnostics": {}, "ai_plan": {}})
    assert "测试核心" in str(card)


def test_eod_conditional_repair_not_labelled_unbuyable():
    anchor = row(watch_group="历史连板修复", participation_eligibility="conditional", buyable=True)
    card = build_cards.build_eod_card({"date": "2026-11-04", "relay": [], "watch": [anchor],
                                      "diagnostics": {}, "ai_plan": {}})
    text = str(card)
    assert "测试核心" in text
    assert "条件资格：竞价核验全部风控后方可参与，非买入确认" in text
    assert "观察池 TOP1（仅作板块参照·不可买）" not in text


def test_hist_gene_not_conditional_prediction():
    assert two_pool.promotion_rate(row(history_relay_prob=99)) == -1
    assert two_pool.promotion_rate(row(conditional_promotion={"rate": 99, "quality": "weak"})) == -1
    assert two_pool.promotion_rate(row(conditional_promotion={"rate": 45, "quality": "ok"})) == 45


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 被闸门显式降级的票**不得凭空消失**（2026-11-04 实证 bug）
# ══════════════════════════════════════════════════════════════════════════
# 事故链：`pipeline_eod.demote_thin_seal_top` 把「全市场最高板 + 封单极薄」的票
#   从接力池降到观察池，打标 `watch_only + watch_reason + watch_group`，
#   日志也照实打了「降为观察（只看不买）」。
#   但 `two_pool.finalize` 只认 `watch_kind()` 的两类锚点（一字核心/容量核心），
#   对这些票返回 None → 被判「不符合两类观察资格」**直接删除**。
#   实证 11-04：紫竹高科(3板/0.63%)、浙文影业(3板/0.14%)、闽发铝业(3板/1.51%)
#   全部消失，`watch=0`。用户看到「系统说降为观察，观察池却是空的」。
def test_demoted_watch_only_survives_finalize():
    import core.two_pool as tp

    demoted = [
        {"code": "002058", "name": "紫竹高科", "boards": 3, "core_theme": "锂电池",
         "leader_role": "准龙头", "is_sector_dragon": True, "dragon_rank": 1,
         "seal_ratio_pct": 0.63, "thin_seal_top": True, "watch_only": True,
         "watch_reason": "它是今天全市场最高 3 板，但封单只占流通盘的 0.63%",
         "watch_group": "最高板封单过薄"},
        {"code": "601599", "name": "浙文影业", "boards": 3, "core_theme": "短剧",
         "leader_role": "准龙头", "is_sector_dragon": True, "dragon_rank": 1,
         "seal_ratio_pct": 0.14, "thin_seal_top": True, "watch_only": True,
         "watch_reason": "封单只占流通盘的 0.14%", "watch_group": "最高板封单过薄"},
    ]
    # 前提：它们本来就不属于两类观察锚点（否则测不到这个 bug）
    assert all(tp.watch_kind(r) is None for r in demoted)

    relay, watch, diag = tp.finalize([], demoted, stage="post_risk")
    assert len(watch) == 2, f"降级票被丢弃了：excluded={[e['name'] for e in diag['excluded']]}"
    assert {w["name"] for w in watch} == {"紫竹高科", "浙文影业"}
    assert all(w["bucket"] == "watch" for w in watch)
    assert all(w["buyable"] is False for w in watch), "观察票不得获得买入资格"
    assert all(w.get("watch_group") for w in watch), "分组丢失会导致卡面白名单过滤掉它"
    # ⛔ 铁律：绝不从观察反提
    assert len(relay) == 0


def test_没有降级标记的票仍会被剔除():
    """⛔ 修 bug 不能把「真没资格」的票也放进来 —— 无 watch_reason 的一律照旧剔除。"""
    import core.two_pool as tp

    junk = {"code": "600001", "name": "杂毛", "boards": 1, "core_theme": "",
            "watch_only": True}          # 有 watch_only 但没 watch_reason
    relay, watch, diag = tp.finalize([], [junk], stage="post_risk")
    assert len(watch) == 0
    assert [e["name"] for e in diag["excluded"]] == ["杂毛"]


def test_一字龙头可回退(monkeypatch):
    """⛔ `ENABLE_TWO_POOL_WIDEN=0` → 逐字回到「一字板封单不厚即丢弃」的旧行为。"""
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "0")
    assert two_pool.watch_kind(row(is_one_word=True, seal_amt=.2)) is None


@pytest.mark.parametrize("rank,removed", [(4, False), (5, True), ("5", True), (None, False), (True, False)])
def test_single_pool_rank_not_boards(monkeypatch, rank, removed):
    from core.quant_score import _merge_pools
    monkeypatch.setenv("POOL_SINGLE_POOL", "1")
    # 2026-11-11 起池子只收已获接力资格的票 → 本用例测「身位过滤」，
    # 故用 relay_candidate 身份（observe_only 会先被资格线剔掉，另有用例覆盖）。
    source = row(boards=7, dragon_rank=rank, bucket="relay",
                 participation_eligibility="relay_candidate", watch_group="补充观察")
    before = copy.deepcopy(source)
    r, w, dropped, _ = _merge_pools([source], [], market_rows=[source, row(code="000002", boards=8)])
    assert bool(dropped) is removed
    assert len(r) == (0 if removed else 1)
    assert not w
    if r:
        assert r[0]["participation_eligibility"] == "relay_candidate"
        assert r[0]["pool_source_bucket"] == "relay"
    assert source == before


@pytest.mark.parametrize("boards,tied,observed", [
    (4, False, False), (5, False, False), (6, False, False),
    (7, False, True), (8, False, True), (7, True, False),
])
def test_single_pool_unique_height_threshold(monkeypatch, boards, tied, observed):
    """用户点的是「唯一高度 7 板」：⛔ 5/6 板最高票不能被顺手禁买，并列也不算。"""
    from core.quant_score import _merge_pools
    monkeypatch.setenv("POOL_SINGLE_POOL", "1")
    source = row(boards=boards, bucket="relay", participation_eligibility="relay_candidate")
    market = [source, row(code="000002", boards=boards if tied else 1)]
    r, w, _, h = _merge_pools([source], [source], market_rows=market)
    assert bool(h) is observed
    assert len(r) + len(w) == 1
    if observed:
        assert h["buyable"] is False
        assert h["watch_only"] is True
        assert h["participation_eligibility"] == "observe_only"
        assert two_pool.finalize(r, w, stage="test")[1][0]["height_observe"]


def test_single_pool_rollback(monkeypatch):
    from core.quant_score import _merge_pools
    monkeypatch.setenv("POOL_SINGLE_POOL", "0")
    monkeypatch.setenv("POOL_CALL_DROP_BACKROW_RANK", "0")
    r, w, dropped, h = _merge_pools([row()], [row(code="000002", dragon_rank=8)])
    assert len(r) == len(w) == 1
    assert not dropped and h is None


@pytest.mark.parametrize("all_tradeable", ["0", "1"])
@pytest.mark.parametrize("marker", [{"height_observe": True}, {"watch_group": "高度观察"}])
def test_height_never_unlocked(monkeypatch, all_tradeable, marker):
    monkeypatch.setenv("POOL_CALL_ALL_TRADEABLE", all_tradeable)
    anchor = row(**marker, participation_eligibility="relay_candidate",
                 expectation={"expected_range": [1, 3], "auction_amount_min_yi": 1})
    result = pool_call.call_pool({"date": "2026-11-10", "relay": [anchor], "watch": []},
        {"000001": {"auction_change_pct": 2., "auction_amount_yi": 10.}},
        line_cores={}, sector_flow={}, zt_series={}, zt_days=[], market_open={"ready": True})
    decision = result["views"]["000001"]
    assert decision["view"] == "watch"
    assert decision["height_observe"] is True
    assert not result["meta"]["participate"]
    assert not two_pool.rechecked_entry({**marker, "rechecked_from_observe": True, "view": "participate"})


def test_formal_selection_applies_policy_after_finalize(monkeypatch):
    from core import quant_score
    sources = [row(code="000001", boards=7, score=90, lead_score=90),
               row(code="000002", boards=2, score=90, lead_score=90),
               row(code="000003", boards=1, dragon_rank=5, score=90, lead_score=90)]
    monkeypatch.setenv("POOL_SINGLE_POOL", "1")
    r, w, diag, _ = quant_score.select_relay_watch(
        sources, {"regime": "neutral"}, None, None, apply_pool_policy=True)
    assert all(x["bucket"] == "relay" for x in r)
    assert all(x["height_observe"] for x in w)
    assert "000003" not in {x["code"] for x in r + w}
    assert diag["relay_count"] == len(r)
    assert diag["watch_count"] == len(w)
    assert diag["single_pool"] is True
    assert diag["backrow_dropped_count"] == 1
    assert diag["height_observe"]["code"] == "000001"


def test_single_pool_card_counts_height_separately():
    from core.quant_score import _merge_pools
    sources = [row(code=f"00000{i}", name=f"测试{i}", boards=7 if i == 1 else 2,
                   participation_eligibility="relay_candidate",
                   watch_group="补充观察") for i in range(1, 7)]
    r, w, _, _ = _merge_pools([], sources)
    text = str(build_cards.build_eod_card({"date": "2026-11-10", "relay": r, "watch": w,
               "diagnostics": {"single_pool": True}, "ai_plan": {}}))
    assert "接力池（5 只）" in text
    assert "高度观察 1 只" in text
    assert "观察池 TOP" not in text
    assert all(f"测试{i}" in text for i in range(1, 7))


def test_unqualified_rows_never_enter_pool(monkeypatch):
    """🚮 2026-11-11 用户「垃圾票不该进池」：没拿接力资格的一律不进池。

    覆盖 11-11 实测的三类：一字买不到、容量核心、过筛未过（孤军/小额换手板）。
    """
    from core.quant_score import _merge_pools
    monkeypatch.setenv("POOL_SINGLE_POOL", "1")
    monkeypatch.delenv("POOL_QUALIFIED_ONLY", raising=False)
    junk = [
        row(code="000011", name="一字票", is_one_word=True,
            participation_eligibility="observe_only", watch_group="一字核心"),
        row(code="000012", name="容量票", is_capacity_core=True,
            participation_eligibility="observe_only", watch_group="容量核心"),
        row(code="000013", name="孤军票",
            participation_eligibility="observe_only", watch_group="过筛未过·只观察",
            screen_reject=["同题材仅 1 家，未成线（低板不接孤军）"]),
    ]
    good = row(code="000014", name="真接力", participation_eligibility="conditional",
               buyable=True, watch_group="历史连板修复")
    # 🕯 2026-11-12 用户「一字板次日能低吸」→ 一字核心保留在池里（observe_only），
    #    所以本用例的期望从「只剩真接力」改成「一字票保留观察、容量/孤军仍不进」。
    r, w, dropped, _ = _merge_pools([], junk + [good])
    names = [x["name"] for x in r]
    assert "真接力" in names
    assert "一字票" in names, names
    assert "容量票" not in names and "孤军票" not in names, names
    one = next(x for x in r if x["name"] == "一字票")
    assert one.get("participation_eligibility") == "observe_only"
    assert not one.get("buyable")   # 缺省 None 与 False 同义：不可买
    assert not w
    # 一字票已改为「保留观察」，不再出现在 dropped 里
    assert {x["name"] for x in dropped} == {"容量票", "孤军票"}
    assert all(x["dropped_kind"] == "unqualified" for x in dropped)


def test_empty_pool_is_legal(monkeypatch):
    """合格 0 只 → 空池（不用观察票凑数）。"""
    from core.quant_score import _merge_pools
    monkeypatch.setenv("POOL_SINGLE_POOL", "1")
    monkeypatch.delenv("POOL_QUALIFIED_ONLY", raising=False)
    r, w, dropped, h = _merge_pools([], [row(participation_eligibility="observe_only",
                                            watch_group="容量核心", is_capacity_core=True)])
    assert r == [] and w == [] and h is None
    assert len(dropped) == 1


def test_qualified_only_switch_rolls_back(monkeypatch):
    """`POOL_QUALIFIED_ONLY=0` → 逐字回到「观察票也并入接力池」的旧口径。"""
    from core.quant_score import _merge_pools
    monkeypatch.setenv("POOL_SINGLE_POOL", "1")
    monkeypatch.setenv("POOL_QUALIFIED_ONLY", "0")
    junk = row(code="000012", name="容量票", is_capacity_core=True,
               participation_eligibility="observe_only", watch_group="容量核心")
    r, w, dropped, _ = _merge_pools([], [junk])
    assert [x["name"] for x in r] == ["容量票"]
    assert not dropped


@pytest.mark.parametrize("all_tradeable,tradeable_text", [("1", True), ("0", False)])
def test_single_pool_card_matches_real_eligibility(monkeypatch, all_tradeable, tradeable_text):
    """文案=行为：全池可交易开时原观察票按待竞价渲染，关时仍按只看不买渲染。

    ⚠️ 2026-11-11 起这是**回退口径**（`POOL_QUALIFIED_ONLY=0`）的契约：
    默认开资格线时，这些观察票根本不会进池（见 `test_unqualified_rows_never_enter_pool`）。
    """
    from core.quant_score import _merge_pools
    monkeypatch.setenv("POOL_SINGLE_POOL", "1")
    monkeypatch.setenv("POOL_QUALIFIED_ONLY", "0")   # 回退到「观察票并入接力池」旧口径
    monkeypatch.setenv("POOL_CALL_ALL_TRADEABLE", all_tradeable)
    sources = [
        row(code=f"00000{i}", name=f"测试{i}", boards=7 if i == 1 else 2,
            watch_group="补充观察", price=10.0,
            participation_eligibility="observe_only",
            watch_reason="补充观察，跟踪后续表现；不参与",
            expectation={"expected_range": [1, 3]})
        for i in range(1, 7)
    ]
    r, w, _, _ = _merge_pools([], sources)
    text = str(build_cards.build_eod_card({"date": "2026-11-10", "relay": r, "watch": w,
               "diagnostics": {"single_pool": True}, "ai_plan": {}}))
    assert "只看不买" in text                         # 唯一 7 板高度观察
    assert ("预期1~3%" in text) is tradeable_text
    assert ("补充观察，跟踪后续表现；不参与" in text) is (not tradeable_text)
