"""前置地位比较隔离回归；所有历史文件均注入 tmp_path。"""
from copy import deepcopy
import json
import hashlib
import socket

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


@pytest.fixture(autouse=True)
def offline_defaults(monkeypatch):
    # 本文件锁旧排序回退；两池新契约另由专项测试覆盖。
    monkeypatch.setenv("ENABLE_TWO_POOL_CONTRACT", "0")
    monkeypatch.delenv("ENABLE_CORE_BEFORE_CUTOFF", raising=False)
    monkeypatch.setattr(lc, "ENABLE_PRESELECT_STATUS", True)
    # ⛔ 本文件锁的是**旧排序基线**（`OLD_DIGESTS` 逐字节比对）。
    #    2026-11-04 用户要求「按打分排序」后，`_leader_preference_key` 与
    #    `selection_key` 的综合分位置都变了 → digest 必然不同。
    #    ⇒ 本文件显式进入旧排序模式，继续保护「关掉开关能逐字节回到旧行为」这条可逆性；
    #      新排序另由 `tests/test_score_priority_sort.py` 覆盖。
    #    ⛔ 绝不为了让测试通过而改 `OLD_DIGESTS` —— 那等于把契约测试变成橡皮图章。
    #    ⛔ 用 setattr 而非 setenv：`_SCORE_SORT` 是模块级常量，import 时已求值，
    #      setenv 对它无效（会静默失效，测试看似通过实际没测到旧序）。
    #    ⛔ `sort_note()` 是函数、自动跟随 `_SCORE_SORT`，**无需单独打补丁**
    #      （早先版本打过 `_SORT_NOTE` 常量补丁，改成函数后那行会 AttributeError）。
    monkeypatch.setattr(qs, "_SCORE_SORT", False)
    # ⛔ 同理：观察池准入/占位/扩容都是 2026-11-04 的新行为，旧基线里没有。
    monkeypatch.setattr(qs, "_WATCH_WIDEN", False)
    monkeypatch.setattr(qs, "_WATCH_CARRYOVER", False)
    # 观察池容量 8→12 也会改变 digest（`_watch_cap()` 运行时读环境变量，故 setenv 有效）
    monkeypatch.setenv("WATCH_POOL_CAP", "8")
    monkeypatch.setenv("ENABLE_TWO_POOL_WIDEN", "0")

    def no_network(*args, **kwargs):
        raise AssertionError("隔离测试禁止触网")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)


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
    # ⛔ 2026-11-04：调用改为多行（新增 `prev_pool_boards=` 参数），
    #    断言从 `"select_relay_watch(scored"` 放宽为 `"select_relay_watch("` ——
    #    原意是「先建预选上下文再选池」，与换行无关。
    assert src.index("build_preselect_context(") < src.index("select_relay_watch(")
    assert "comparison=comparison" in src
    # P0（2026-10-18 用户「按顺序处理」）：题材映射**一律复用当日快照** `_theme_map_all`。
    # 旧断言锁的是「comparison 非空才复用」，新实现更强：无论是否预选，候选回填都从
    # 同一份快照取 —— 否则同一文件里 pool 与候选题材不同源（10-16 实证结果漂移）。
    assert "_theme_map_all[_c]" in src
    assert "ThemeFetcher().for_codes(" not in src, "⛔ 禁再二次实时抓（会与 pool 不同源）"
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


CUTOFF_STAGES = [("one_to_two", 3, {}), ("core", 8, {}),
                 ("core", 3, {"relay_feedback": {"pressure": True}}),
                 ("relaxed", 3, {}), ("broad", 3, {}), ("emergency", 3, {}),
                 ("watch", 8, {})]


def cutoff_case(mapped, stage):
    rows = [stock(i) for i in range(1, 14)]
    rows[-1].update(is_sector_dragon=True, dragon_rank=1)
    ctx = context(rows, [day("2026-09-23", [stock(12, 5)])])
    if stage != "watch":
        mapped.setattr(qs, f"_relay_{stage}_ok", lambda m: True)
    return rows, ctx


@pytest.mark.parametrize("stage,cap,env", CUTOFF_STAGES)
def test_confirmed_core_before_cutoff(mapped, stage, cap, env):
    rows, ctx = cutoff_case(mapped, stage)
    assert len({e["key"][0] for e in ctx["entries"].values()}) == 1
    original = deepcopy((rows, ctx, env))
    mapped.setenv("ENABLE_CORE_BEFORE_CUTOFF", "0")
    old = qs.select_relay_watch(rows, env, {}, {}, comparison=ctx)
    mapped.delenv("ENABLE_CORE_BEFORE_CUTOFF")
    new = qs.select_relay_watch(rows, env, {}, {}, comparison=ctx)
    bucket = 1 if stage == "watch" else 0
    assert len(new[bucket]) == cap
    assert rows[-1]["code"] not in {r["code"] for r in old[bucket]}
    assert new[bucket][0]["code"] == rows[-1]["code"]
    assert new[bucket][0]["raw_score"] == rows[-1]["raw"]
    assert new[3] == old[3]
    for r in new[2]["preselect_status"]["rows"]:
        before = next(x for x in old[2]["preselect_status"]["rows"] if x["code"] == r["code"])
        assert (r["raw"], r["eligible"]) == (before["raw"], before["eligible"])
    assert new[2]["core_before_cutoff"] == {
        "enabled": True, "active": True,
        "priority_rule": "旧key[0]角色→已确认题材龙一→旧key[1:]→raw"}
    assert (rows, ctx, env) == original


# 改动前完整返回值的规范 JSON 摘要；仅剔除此次新增的聚合诊断。
OLD_DIGESTS = [
    ("62f9d98ab40317c7a2d0d6f8500bcc10258a300b4aadff791f184c5fb4db8ffe", "a94370839a4f76e5fb3ee8d98744c99f770d480922ebc33f841f4ac67a71097b"),
    ("d56fe242d131a9dbd9cd05aa2f50ccedb3f2c3212348d5053d842137b5815b83", "12887460d88033a1343506670fd0e1cf95801dfb30df8f8151023029a94d4704"),
    ("2179765690102bf2960ce54e8d1fc09bafd0335411596e3f2b76a57b1c97ba72", "10f2443ca5809d480cf3b717877de301a6401f3c9abf74399f90fa290165f900"),
    ("2e9742062551f54573743a1fa5567744e5f2b6652fc22fed2819a82d69acb14f", "a7a900ef22317476c4c61578ef8d0e7a6ca647e5b7e2a8485d7e98e0142dfca9"),
    ("6e7b7336ea6f585c75768ae0df08256d51cf5d13f2b9c9d19ad5972d12f2cbbc", "220159559861d660db9058b92bf75ee77206e5de87e0006440bae9024b6903bb"),
    ("ffccc4bcc38537be6053e013e8de5107eb1b1db9b29de5c04678a34f82f1a9df", "600400197af33b6059721211a9e4cbd963efb04f7d7dede424f8dcf6ace13c6b"),
    ("dba50a3555998580b4ae4e7cd067a489492b50408a91ab7b2940328e35eb5853", "714f35435993c747f4af0343f5b892b7fd398bc28673a3ce11dc0a92efd2d9c7"),
]


def legacy_digest(result):
    result = deepcopy(result)
    result[2].pop("core_before_cutoff")
    # 普通观察新增解释字段不属于旧评分/名单快照；其文案由观察池回归单独锁定。
    for row in result[1]:
        row.pop("watch_reason", None)
    return hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("case,digests", list(zip(CUTOFF_STAGES, OLD_DIGESTS)))
@pytest.mark.parametrize("disabled", ["0", "false", "False", ""])
def test_core_switch_exact_legacy_result(mapped, case, digests, disabled):
    stage, _, env = case
    rows, ctx = cutoff_case(mapped, stage)
    mapped.setenv("ENABLE_CORE_BEFORE_CUTOFF", disabled)
    result = qs.select_relay_watch(rows, env, {}, {}, comparison=ctx)
    assert legacy_digest(result) == digests[0]
    assert result[2]["core_before_cutoff"] == {
        "enabled": False, "active": False, "priority_rule": "沿用旧排序"}


@pytest.mark.parametrize("case,digests", list(zip(CUTOFF_STAGES, OLD_DIGESTS)))
def test_core_without_context_exact_legacy_result(mapped, case, digests):
    stage, _, env = case
    rows, ctx = cutoff_case(mapped, stage)
    partial = deepcopy(ctx)
    partial["entries"].pop(rows[0]["code"])
    for comparison in (None, {}, partial):
        result = qs.select_relay_watch(rows, env, {}, {}, comparison=comparison)
        assert legacy_digest(result) == digests[1]
        assert not result[2]["core_before_cutoff"]["active"]
    mapped.setattr(lc, "ENABLE_PRESELECT_STATUS", False)
    assert legacy_digest(qs.select_relay_watch(rows, env, {}, {}, comparison=ctx)) == digests[1]


@pytest.mark.parametrize("flag,rank", [(True, 2), (True, None), (True, ""),
                                      (True, True), ("true", 1), (1, 1), (False, 1)])
def test_unconfirmed_or_conflicting_rank_not_preferred(mapped, flag, rank):
    rows, ctx = cutoff_case(mapped, "one_to_two")
    rows[-1].update(is_sector_dragon=flag, dragon_rank=rank)
    result = qs.select_relay_watch(rows, {}, {}, {}, comparison=ctx)
    assert rows[-1]["code"] not in {r["code"] for r in result[0]}


@pytest.mark.parametrize("rank", ["absent", 1, "1"])
def test_confirmed_rank_and_role_boundary(mapped, rank):
    rows, ctx = cutoff_case(mapped, "one_to_two")
    if rank == "absent":
        rows[-1].pop("dragon_rank")
    else:
        rows[-1]["dragon_rank"] = rank
    result = qs.select_relay_watch(rows, {}, {}, {}, comparison=ctx)
    assert result[0][0]["code"] == rows[-1]["code"]
    old_key = ctx["entries"][rows[-1]["code"]]["key"]
    ctx["entries"][rows[-1]["code"]]["key"] = (old_key[0] - 1, *old_key[1:])
    result = qs.select_relay_watch(rows, {}, {}, {}, comparison=ctx)
    assert rows[-1]["code"] not in {r["code"] for r in result[0]}


def test_ineligible_core_stays_out_of_relay(mapped):
    rows, ctx = cutoff_case(mapped, "watch")
    result = qs.select_relay_watch(rows, {}, {}, {}, comparison=ctx)
    assert result[0] == []
    assert result[1][0]["code"] == rows[-1]["code"]
    assert not any(result[2]["preselect_status"]["rows"][-1]["eligible"].values())


def test_core_priority_does_not_bypass_real_qualification():
    rows = [dict(stock(i), is_sector_dragon=True, dragon_rank=1, is_yizi=True,
                 ign_score=0, lead_score=0, cap_score=0, zbc=20) for i in range(1, 4)]
    original = deepcopy(rows)
    result = qs.select_relay_watch(rows, {"regime": "neutral"}, {}, {}, comparison=context(rows))
    assert result[0] == []
    assert all(not any(r["eligible"].values()) for r in result[2]["preselect_status"]["rows"])
    assert rows == original
