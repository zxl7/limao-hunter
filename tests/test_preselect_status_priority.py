"""前置地位比较隔离回归；所有历史文件均注入 tmp_path。"""
from copy import deepcopy
import json

import pytest
from core import leader_cycle as lc, quant_score as qs
from core.expectation_ai import dragon_three_diagnosis

ASOF = "2026-09-29"
CUTOFF = ASOF + " 16:00:00"


def stock(i, boards=1, theme="传媒"):
    return {"code": f"{i:06d}", "name": f"测试{i}", "boards": boards,
            "core_theme": theme, "carry_cnt": 0, "lead_score": 0, "raw": 100-i}


def day(date, rows):
    return {"date": date, "source_ts": date + " 15:10:00", "complete": True, "rows": rows}


def context(rows, history=(), pool=None, themes=None):
    return lc.build_preselect_context(rows, pool or rows, history, asof=ASOF, cutoff=CUTOFF,
        themes=themes if themes is not None else {r["code"]: {"theme": r["core_theme"]} for r in (pool or rows)})


@pytest.fixture
def mapped(monkeypatch):
    monkeypatch.setattr(qs, "compute_raw_score", lambda c, *a: {"raw_score": c["raw"]})
    monkeypatch.setattr(qs, "map_tactics_fields", lambda c, e, p, a, r:
        {"code": c["code"], "name": c["name"], "lbc": c["boards"], "_raw": c["raw"], "_cand": c})
    for name in ("height_breakout", "high_mark", "core", "one_to_two", "relaxed", "broad", "emergency"):
        monkeypatch.setattr(qs, f"_relay_{name}_ok", lambda m: False)
    return monkeypatch


@pytest.mark.parametrize("stage,cap", [("one_to_two", 3), ("core", 8), ("relaxed", 3), ("broad", 3), ("emergency", 3), ("watch", 8)])
def test_before_every_cutoff_and_raw_unchanged(mapped, stage, cap):
    rows = [stock(i) for i in range(1, 14)]
    hist = [day("2026-09-23", [stock(13, 5), stock(12, 4)])]
    ctx = context(rows, hist)
    if stage != "watch":
        mapped.setattr(qs, f"_relay_{stage}_ok", lambda m: True)
    original = deepcopy((rows, hist, ctx))
    old = qs.select_relay_watch(rows, {}, {}, {})
    new = qs.select_relay_watch(rows, {}, {}, {}, comparison=ctx)
    bucket = 1 if stage == "watch" else 0
    assert len(new[bucket]) == cap
    assert new[bucket][0]["code"] == "000013"
    assert new[bucket][1]["code"] == "000012"
    assert "000013" not in {r["code"] for r in old[bucket]}
    assert new[bucket][0]["raw_score"] == rows[-1]["raw"]
    assert (rows, hist, ctx) == original
    assert len(new[2]["preselect_status"]["rows"]) == len(rows)


def test_switch_and_unavailable_context_exact_old_path(mapped):
    rows = [stock(i) for i in range(1, 14)]
    mapped.setattr(qs, "_relay_one_to_two_ok", lambda m: True)
    old = qs.select_relay_watch(rows, {}, {}, {})
    ctx = context(rows, [day("2026-09-23", [stock(13, 5)])])
    assert qs.select_relay_watch(rows, {}, {}, {}, comparison={}) == old
    partial = deepcopy(ctx)
    partial["entries"].pop("000001")
    assert qs.select_relay_watch(rows, {}, {}, {}, comparison=partial) == old
    mapped.setattr(lc, "ENABLE_PRESELECT_STATUS", False)
    assert qs.select_relay_watch(rows, {}, {}, {}, comparison=ctx) == old


def test_follow_history_does_not_promote_and_keeps_second_core():
    rows = [stock(1), stock(2), stock(3)]
    historical = [dict(stock(1, 6), leader_role="龙头"), dict(stock(2, 5), leader_role="跟风")]
    ctx = context(rows, [day("2026-09-23", historical)])
    second = ctx["entries"]["000002"]
    assert second["historical_boards"] == 5
    assert second["leader_role"] == "跟风"
    assert second["history"][0]["role"] == "跟风"
    assert any(r["code"] == "000001" for r in second["same_line"])


def test_different_themes_and_missing_theme_not_merged():
    rows = [stock(1, theme="传媒"), stock(2, theme="AI算力")]
    hist = [day("2026-09-23", [stock(1, 5), stock(2, 4, "AI算力")])]
    ctx = context(rows, hist)
    assert ctx["entries"]["000002"]["same_line"] == []
    assert context(rows, hist, themes={}) is None
    partial = context(rows, hist, themes={"000002": {"theme": "AI算力"}})
    assert partial["entries"]["000001"]["historical_boards"] == 0
    for r in hist[0]["rows"]:
        r["sector"] = "传媒"
        r.pop("core_theme")
    assert context(rows, hist)["entries"]["000001"]["historical_boards"] == 0


def test_duplicate_future_same_day_and_input_immutability():
    rows = [stock(1)]
    hist = [day("2026-09-24", [stock(1, 5)]), day("2026-09-25", [stock(1, 5)])]
    original = deepcopy((rows, hist))
    ctx = context(rows, hist)
    assert ctx["entries"]["000001"]["historical_boards"] == 0
    assert ctx["entries"]["000001"]["leader_evidence"]["continuous_days"] == 1
    assert ctx["quality"]
    assert context(rows, hist + [day(ASOF, [stock(1, 99)]), day("2026-09-30", [stock(1, 99)])]) == ctx
    assert (rows, hist) == original


def test_same_broad_family_does_not_merge_distinct_themes():
    rows = [stock(1, theme="创新药"), stock(2, theme="中药")]
    ctx = context(rows, [day("2026-09-23", [stock(1, 5, "创新药")])])
    assert ctx["entries"]["000002"]["same_line"] == []
    assert ctx["entries"]["000001"]["line_family"] == ctx["entries"]["000002"]["line_family"]


def test_missing_source_and_future_publication_are_excluded():
    rows = [stock(1)]
    for stamp in (None, "2026-09-30 15:10:00"):
        h = day("2026-09-23", [stock(1, 5)])
        h["source_ts"] = stamp
        assert context(rows, [h])["entries"]["000001"]["historical_boards"] == 0


def test_future_theme_not_used():
    rows = [stock(1)]
    assert context(rows, themes={"000001": {"theme": "传媒", "source_ts": "2026-09-30 10:00:00"}}) is None


def test_reset_board_does_not_join_continuity():
    rows = [stock(1)]
    h = [day("2026-09-28", [stock(1, 5)])]
    assert context(rows, h)["entries"]["000001"]["leader_evidence"]["continuous_days"] == 1


def test_diagnosis_ignores_rank_and_name_code():
    r = stock(1, 3)
    a = dragon_three_diagnosis(r, use_relay_rank=False)
    assert dragon_three_diagnosis(dict(r, relay_rank=1), use_relay_rank=False) == a
    a = context([r])["entries"][r["code"]]["key"]
    renamed = dict(r, code="900001", name="完全不同")
    assert context([renamed])["entries"]["900001"]["key"] == a


def test_loader_same_day_candidates_and_late_rewrite_excluded(tmp_path):
    zt = tmp_path / "cache/zt_history"
    candidates = tmp_path / "wealth_code/cache"
    zt.mkdir(parents=True)
    candidates.mkdir(parents=True)
    for date in ("2026-09-28", ASOF, "2026-09-30"):
        (zt / f"zt_{date}.json").write_text(json.dumps({"date": date,
            "generated_ts": date + " 15:10:00", "zt_count": 1, "zt_list": [stock(1)]}))
        (candidates / f"candidates-{date}.json").write_text(json.dumps({"date": date,
            "generated_at_bj": "2026-09-30 16:00:00", "relay": [dict(stock(1, 99), leader_role="龙头")]}))
    hist = lc.load_history(tmp_path, ASOF, before_only=True)
    assert len(hist) == 1
    assert hist[0]["rows"][0]["boards"] == 1
    assert hist[0]["rows"][0].get("leader_role") is None


def test_pipeline_wires_before_selection_and_reuses_frozen_themes():
    import inspect
    from pipelines import pipeline_eod
    src = inspect.getsource(pipeline_eod.run_eod)
    assert src.index("build_preselect_context(") < src.index("select_relay_watch(scored")
    assert "comparison=comparison" in src
    assert "_theme_map_all if comparison is not None" in src
    assert "if comparison is None:\n        enrich_payload" in src


def test_real_predicates_and_scores_unchanged():
    rows = [dict(stock(i), theme="传媒", ign_score=30, exp_score=20, lead_score=15,
                 cap_score=70, cje_yi=10, hs=5, seal_amt=1, ladder_health=5)
            for i in range(1, 14)]
    ctx = context(rows, [day("2026-09-23", [stock(13, 5)])])
    old = qs.select_relay_watch(rows, {"regime": "neutral"}, {}, {})
    new = qs.select_relay_watch(rows, {"regime": "neutral"}, {}, {}, comparison=ctx)
    for r in new[2]["preselect_status"]["rows"]:
        candidate = next(c for c in rows if c["code"] == r["code"])
        assert r["raw"] == qs.compute_raw_score(candidate, {"regime": "neutral"}, {}, {})["raw_score"]
    assert new[3] == old[3]
    assert [len(new[i]) for i in (0, 1)] == [len(old[i]) for i in (0, 1)]


def test_complete_pool_reference_keeps_nontradeable_core():
    row = stock(2)
    core = dict(stock(1, 6), is_yizi=True)
    ctx = context([row], [day("2026-09-23", [core])], pool=[core, row])
    assert [r["code"] for r in ctx["entries"][row["code"]]["same_line"]] == ["000001"]
    assert list(ctx["entries"]) == [row["code"]]
