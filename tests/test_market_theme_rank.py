"""🧭 市场级题材强度（方案 B）契约锁 · 2026-11-11。

用户 11-10 晚质疑「没看到对昨天整个市场强题材的思考」，拍板 B：
新增市场级题材聚合 + 卡面首段 + 板块优先序展示排序。

⛔ 本文件锁的是**边界**，不是收益：
  · `relay_rank` / `dragon_rank` / `participation_eligibility` / relay 数组顺序
    逐字节不动 —— 它们是 09:25 判据字段（`pool_call.py:900`、
    `expectation_ai.py:128`、`pool_admit` 龙一闸、`quant_score` 龙5过滤）。
  · 聚合基准必须是**完整同日涨停池**，不能拿过滤后残池冒充全市场。
  · `POOL_CALL_MARKET_THEME_RANK=0` 全链路回退（含卡面渲染）。
  · 「强」的口径 = 高度与梯队 + 承接质量，家数只做弱辅助（用户「广度≠核心」）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import market_theme_rank as m

ROOT = Path(__file__).resolve().parent.parent


def _row(code, theme, boards, fbt, seal_pct, *, cje=5.0):
    """`seal` 单位与 lt_yi 一致：seal_pct = 100 * seal / lt。"""
    return {
        "code": code, "name": code, "theme": theme, "core_theme": theme,
        "boards": boards, "fbt": fbt,
        "seal": seal_pct, "lt_yi": 100.0, "cje_yi": cje,
    }


def _ctx(rows):
    tm = {str(r["code"]): r["theme"] for r in rows}
    return m.rank(rows, tm)


@pytest.fixture(autouse=True)
def _switch_on(monkeypatch):
    monkeypatch.delenv("POOL_CALL_MARKET_THEME_RANK", raising=False)


# ── 口径：广度≠核心 ────────────────────────────────────────────────────────
def test_breadth_line_is_flagged_and_height_line_ranks_first():
    rows = [_row(f"00{i:04d}", "广度线", 1, f"{9 + i // 6:02d}{30 + i % 6:02d}00", 1.0)
            for i in range(10)]
    rows += [
        _row("900001", "高度线", 7, "092500", 8.0),
        _row("900002", "高度线", 3, "092501", 4.0),
    ]
    ctx = _ctx(rows)
    by_name = {x["theme"]: x for x in ctx["themes"]}

    assert by_name["广度线"]["breadth_flag"] is True          # 10 家 · 2 板封顶 · 无 3 板
    assert by_name["高度线"]["breadth_flag"] is False
    assert ctx["top3"][0]["theme"] == "高度线"               # 高度·承接榜第一
    assert ctx["breadth_top3"][0]["theme"] == "广度线"       # 广度榜第一
    assert ctx["divergence"] is True                         # 两榜不一致要显式留痕


def test_missing_theme_does_not_become_a_line():
    rows = [_row("000001", "—", 5, "092500", 2.0),
            _row("000002", "其他", 3, "092501", 2.0),
            _row("000003", "真题材", 2, "093000", 2.0)]
    ctx = _ctx(rows)
    assert [x["theme"] for x in ctx["themes"]] == ["真题材"]
    assert ctx["zt_count"] == 3                              # 全市场只数仍含缺题材票


def test_rank_does_not_mutate_input():
    rows = [_row("000001", "A线", 2, "093000", 2.0)]
    snapshot = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    _ctx(rows)
    assert json.dumps(rows, ensure_ascii=False, sort_keys=True) == snapshot


# ── 票的强度：强板块烂票不能靠板块躺赢 ──────────────────────────────────────
def test_strong_line_weak_ticket_sinks_below_other_line_leader():
    rows = [
        _row("100001", "高度线", 7, "092500", 8.0),
        _row("100002", "高度线", 1, "143000", 0.05),          # 14:30 封 · 极薄封单
        _row("200001", "次强线", 3, "092501", 4.0),           # 09:25 封 · 厚封单
    ]
    ctx = _ctx(rows)
    cands = [{"code": "100002"}, {"code": "200001"}]
    ordered = m.order_rows(cands, ctx)
    assert ordered[0]["code"] == "200001"                    # 次强线龙头在前
    assert ordered[1]["code"] == "100002"                    # 强线烂票沉底
    assert [x["theme_order"] for x in ordered] == [1, 2]


def test_ticket_score_rewards_early_seal_seal_quality_and_carry():
    strong = {"boards": 2, "seal_ratio_pct": 2.15, "fbt_min": 570, "carry_cnt": 10}
    weak = {"boards": 1, "seal_ratio_pct": 0.54, "fbt_min": 881, "carry_cnt": 1}
    assert m.ticket_score(strong) > m.ticket_score(weak)
    assert m.line_bonus(1) > m.line_bonus(3) >= m.line_bonus(9) == 0


# ── 铁律：一个判据字段都不碰 ────────────────────────────────────────────────
def test_attach_to_payload_never_touches_judgement_fields():
    rows = [_row("000001", "A线", 2, "093000", 2.0),
            _row("000002", "A线", 1, "093100", 1.0)]
    relay = [
        {**rows[0], "relay_rank": 1, "dragon_rank": 1,
         "participation_eligibility": "relay_candidate"},
        {**rows[1], "relay_rank": 2, "dragon_rank": None,   # 补票路径缺身位
         "participation_eligibility": "observe_only"},
    ]
    watch = [{"code": "999999", "watch_rank": 1, "height_observe": True}]
    diag = {"relay_count": 2}
    before = json.dumps(relay, ensure_ascii=False, sort_keys=True)

    ctx = m.attach_to_payload(diag, relay, watch, rows,
                              {r["code"]: r["theme"] for r in rows})
    assert ctx is not None and diag["market_theme_rank"]["schema"] == m.SCHEMA
    # 只新增 theme_* 展示字段，判据字段与数组顺序原样
    for key in ("relay_rank", "dragon_rank", "participation_eligibility", "code"):
        assert [x.get(key) for x in relay] == [
            json.loads(before)[i].get(key) for i in range(len(relay))]
    assert [x.get("code") for x in relay] == ["000001", "000002"]
    assert all(x.get("theme_metrics") for x in relay)        # 缺 dragon_rank 的票也有因子
    assert relay[1].get("dragon_rank") is None               # 不擅自补判据字段


def test_pool_call_stays_free_of_theme_rank():
    """静态锁：新机制不得被接进 09:25 买入裁决链。"""
    src = (ROOT / "wealth_code/core/pool_call.py").read_text(encoding="utf-8")
    assert "market_theme_rank" not in src
    assert "theme_order" not in src


# ── 可逆开关 ───────────────────────────────────────────────────────────────
def test_switch_off_rolls_back_everything(monkeypatch):
    rows = [_row("000001", "A线", 2, "093000", 2.0)]
    ctx = _ctx(rows)
    relay = [{"code": "000001", "relay_rank": 1}]

    monkeypatch.setenv("POOL_CALL_MARKET_THEME_RANK", "0")
    assert m.enabled() is False
    diag = {}
    assert m.attach_to_payload(diag, relay, [], rows, {}) is None
    assert diag == {}                                        # 一个字段都不写
    assert "theme_metrics" not in relay[0]
    assert [x.get("code") for x in m.order_rows(relay, ctx)] == ["000001"]
    assert "theme_order" not in m.order_rows(relay, ctx)[0]


# ── 11-10 真实归档回放 ─────────────────────────────────────────────────────
def _archive():
    p = ROOT / "wealth_code/cache/candidates-2026-11-10.json"
    z = ROOT / "cache/zt_history/zt_2026-11-10.json"
    t = ROOT / "cache/theme_snapshot/theme_2026-11-10.json"
    if not (p.exists() and z.exists() and t.exists()):
        return None
    cand = json.loads(p.read_text(encoding="utf-8"))
    zt = json.loads(z.read_text(encoding="utf-8"))
    snap = json.loads(t.read_text(encoding="utf-8")).get("mapping") or {}
    tm = {c: (v.get("theme") if isinstance(v, dict) else v) for c, v in snap.items()}
    return cand, (zt.get("ztgc") or zt.get("zt_list") or []), tm


@pytest.mark.skipif(_archive() is None, reason="11-10 归档不存在")
def test_real_1110_height_line_beats_breadth_and_leader_comes_first():
    cand, rows, tm = _archive()
    diag = dict(cand.get("diagnostics") or {})
    relay = cand["relay"]
    before_rank = [x.get("relay_rank") for x in relay]
    before_order = [x.get("code") for x in relay]

    ctx = m.attach_to_payload(diag, relay, cand["watch"], rows, tm)
    lines = {x["theme"]: x for x in ctx["themes"]}
    assert lines["机器人"]["breadth_flag"] is True           # 29 家却 2 板封顶
    assert ctx["top3"][0]["theme"] == "光伏"                 # 唯一 7 板线排第一
    assert ctx["divergence"] is True

    ordered = m.order_rows(relay, ctx)
    assert ordered[0]["code"] == "603366"                   # 日出东方 09:25 封/流通 8.82%
    assert "002708" in [x["code"] for x in ordered[:5]]     # 光洋股份带动 10 家
    assert sum(1 for x in relay if x.get("theme_metrics")) == len(relay)   # 31/31
    assert [x.get("relay_rank") for x in relay] == before_rank
    assert [x.get("code") for x in relay] == before_order


@pytest.mark.skipif(_archive() is None, reason="11-10 归档不存在")
def test_real_1110_card_shows_market_context_and_switch_off_restores_old():
    from pipelines import build_cards

    cand, rows, tm = _archive()
    diag = dict(cand.get("diagnostics") or {})
    m.attach_to_payload(diag, cand["relay"], cand["watch"], rows, tm)
    payload = {**cand, "diagnostics": diag}

    new = json.dumps(build_cards.build_eod_card(payload), ensure_ascii=False)
    for phrase in ("昨日市场强题材", "广度榜", "高度·承接榜", "铺广度",
                   "按昨日板块强度排序", "编号不等于买入顺序"):
        assert phrase in new

    old = json.dumps(build_cards.build_eod_card(
        {**cand, "diagnostics": {k: v for k, v in (cand.get("diagnostics") or {}).items()
                                 if k != "market_theme_rank"}}), ensure_ascii=False)
    assert new != old

    import os
    os.environ["POOL_CALL_MARKET_THEME_RANK"] = "0"
    try:
        off = json.dumps(build_cards.build_eod_card(payload), ensure_ascii=False)
        assert off == old                                    # 冻结件重放时开关仍生效
    finally:
        os.environ.pop("POOL_CALL_MARKET_THEME_RANK", None)
