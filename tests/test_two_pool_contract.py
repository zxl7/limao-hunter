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
