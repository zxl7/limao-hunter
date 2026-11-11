#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选池**源头准入闸**契约锁（2026-10-21 用户定）。

## 用户原话（两轮）

> 「从源头上解决问题，我觉得票太多了，后排的票其实不需要到这里面来，
>  我们接力的票必须是龙头票，龙头票才能做接力，趋势票做容量观察用，
>  前排的须是龙头前排，后排的票就不要推到这里面来了！！！」
>
> 「每个板块尽量留两只活口，除非实在没有」

## 本文件锁死的性质

1. **同线只留最强 N 只**（N 默认 2）：机器人线 5 只 → 2 只。
2. **最强判据 = 板数 → 带动 → 封板早 → 综合分**（⛔ 分数不许把低位票顶到龙头前面）。
3. **组内第 1 → 接力池 / 第 2 → 观察池**（前排做接力、次排做容量观察）。
4. **题材缺失不淘汰**（缺数据 ≠ 它是杂毛，⛔ 不拿缺失当不利证据）。
5. **小题材（< min_mates 家）不淘汰**（1 家的"线"没有后排可言）。
6. **开关 `POOL_ADMIT_ENABLED=0` → 逐字节回到旧池**。
7. **幂等**：同一输入多次调用结果一致。
8. **⛔ 不碰 9:25 裁决**：关掉准入闸，`pool_call` 三道闸照旧工作。
"""
import copy
import json

import pytest

import core.pool_admit as pa


@pytest.fixture(autouse=True)
def legacy_pool_contract(monkeypatch):
    # 历史分桶回退锁；新观察禁反提由 test_two_pool_contract 覆盖。
    monkeypatch.setenv("ENABLE_TWO_POOL_CONTRACT", "0")


def _row(code, name, boards, carry, fbt="093000", theme="机器人", mates=10,
         score=60.0, theme_em=None, **kw):
    r = {"code": code, "name": name, "boards": boards, "carry_cnt": carry,
         "fbt": fbt, "core_theme": theme, "theme_mates_cnt": mates,
         "sector_mates_cnt": mates, "score": score, "theme_em_name": theme_em}
    r.update(kw)
    return r


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in (pa.ENABLED_ENV, pa.PER_LINE_ENV, pa.MIN_MATES_ENV,
              pa.FIRST_BOARD_MIN_CARRY_ENV):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("interim", [False, True])
def test_multiday_repair_requires_complete_history(tmp_path, interim):
    from pipelines.pipeline_eod import _build_second_leg_map
    dates = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03"]
    for i, day in enumerate(dates):
        boards = i + 1 if i < 3 else (1 if interim and i == 4 else 0)
        rows = [{"code": "600001", "boards": boards}] if boards else []
        (tmp_path / f"zt_{day}.json").write_text(json.dumps({
            "date": day, "generated_ts": day + "T17:00:00+08:00",
            "zt_count": len(rows), "zt_list": rows}))
    rows = [{"code": "600001", "boards": 1}, {"code": "600002", "boards": 1}]
    result = _build_second_leg_map(tmp_path, "2026-11-04", rows)
    assert set(result) == {"600001"}
    assert result["600001"]["adjustment_sessions"] == 4
    assert result["600001"]["restart_dates"] == ["2026-11-04"]
    assert result["600001"]["interim_limitup_dates"] == (["2026-10-30"] if interim else [])
    (tmp_path / "zt_2026-10-30.json").write_text("{}")
    assert not _build_second_leg_map(tmp_path, "2026-11-04", rows)


def test_historical_repair_retained_without_promoting_plain_first_board(monkeypatch):
    row = _row("600001", "修复样本", 1, 0)
    assert not pa.first_board_worthy(row)
    row["n_plus_n"] = {"quality": "ok", "prior_boards": 3,
                       "prior_dates": ["2026-10-29", "2026-10-30", "2026-11-02"],
                       "restart_dates": ["2026-11-04"]}
    assert pa.first_board_worthy(row)
    assert not pa.relay_worth(row)
    monkeypatch.setenv("ENABLE_HISTORICAL_REPAIR", "0")
    assert not pa.first_board_worthy(row)
    row["n_plus_n"]["quality"] = "missing"
    monkeypatch.setenv("ENABLE_HISTORICAL_REPAIR", "1")
    assert not pa.first_board_worthy(row)


# ── ① 默认值 ───────────────────────────────────────────────────────────
def test_default_per_line_is_three():
    """2026-11-11 用户「C 放宽到三个身位」→ 默认 3（每线留前 3）。

    回退口径：`POOL_ADMIT_PER_LINE=0` 不限；`=2` 回到「龙头+次排」。
    """
    assert pa.DEFAULT_PER_LINE == 3
    assert pa.per_line() == 3
    import os
    os.environ[pa.PER_LINE_ENV] = "2"
    try:
        assert pa.per_line() == 2
    finally:
        os.environ.pop(pa.PER_LINE_ENV, None)


def test_per_line_zero_means_unlimited_not_empty():
    """⛔ 0 是「不限」，不是「一只都不留」——整条线全留，不砍光。"""
    import os
    os.environ[pa.PER_LINE_ENV] = "0"
    try:
        assert pa.per_line() == 0
        relay = [_row("600001", "机器人龙", 3, 5, "093000"),
                 _row("600002", "机器人二", 2, 7, "093445"),
                 _row("600003", "机器人三", 2, 6, "094409")]
        r, w, diag = pa.admit(relay, [])
        assert {x["name"] for x in r + w} == {"机器人龙", "机器人二", "机器人三"}
        assert diag["dropped"] == 0
        assert any(g.get("note") == "per_line_unlimited" for g in diag["groups"])
    finally:
        os.environ.pop(pa.PER_LINE_ENV, None)


def test_per_line_negative_falls_back_to_unlimited():
    """负数同样按「不限」处理（不是砍光，也不是退回 1）。"""
    import os
    os.environ[pa.PER_LINE_ENV] = "-3"
    try:
        assert pa.per_line() == 0
    finally:
        os.environ.pop(pa.PER_LINE_ENV, None)


# ── ② 核心：同线只留最强 N 只（默认已解除；此处显式指定 2 验证旧口径可回退）──
def test_same_line_keeps_top_two_only():
    relay = [_row("600001", "机器人龙", 3, 5, "093000"),
             _row("600002", "机器人二", 2, 7, "093445"),
             _row("600003", "机器人三", 2, 6, "094409"),
             _row("600004", "机器人四", 2, 7, "095109"),
             _row("600005", "机器人五", 2, 7, "100302")]
    r, w, diag = pa.admit(relay, [], per_line_cap=2)
    kept = {x["name"] for x in r + w}
    assert kept == {"机器人龙", "机器人二"}, kept
    assert diag["dropped"] == 3
    assert set(diag["dropped_names"]) == {"机器人三", "机器人四", "机器人五"}


def test_same_line_keeps_all_when_unlimited():
    """显式 `POOL_ADMIT_PER_LINE=0` 时同线 5 只全留（0 的语义是「不限」）。"""
    import os
    os.environ[pa.PER_LINE_ENV] = "0"
    try:
        relay = [_row("600001", "机器人龙", 3, 5, "093000"),
                 _row("600002", "机器人二", 2, 7, "093445"),
                 _row("600003", "机器人三", 2, 6, "094409"),
                 _row("600004", "机器人四", 2, 7, "095109"),
                 _row("600005", "机器人五", 2, 7, "100302")]
        r, w, diag = pa.admit(relay, [])
        assert len({x["name"] for x in r + w}) == 5, [x["name"] for x in r + w]
        assert diag["dropped"] == 0
    finally:
        os.environ.pop(pa.PER_LINE_ENV, None)


def test_strength_order_boards_first_then_carry():
    """⛔ 板数排第一：3 板带动少，也要压过 2 板带动多的。"""
    relay = [_row("600001", "两板高带动", 2, 9),
             _row("600002", "三板低带动", 3, 1)]
    r, w, _ = pa.admit(relay, [])
    assert [x["name"] for x in r] == ["三板低带动"]
    assert [x["name"] for x in w] == ["两板高带动"]


def test_same_boards_earlier_seal_wins():
    """板数、带动都相同时 → 封板早的排前。"""
    relay = [_row("600001", "晚封", 2, 5, "140000"),
             _row("600002", "早封", 2, 5, "093000")]
    r, w, _ = pa.admit(relay, [])
    assert [x["name"] for x in r] == ["早封"]
    assert [x["name"] for x in w] == ["晚封"]


# ── ③ 分桶：第 1 → 接力 / 第 2 → 观察 ──────────────────────────────────
def test_first_goes_relay_second_goes_watch():
    """前排龙头做接力、次排做容量观察（用户「趋势票做容量观察用」）。"""
    relay = [_row("600001", "线最强", 3, 5), _row("600002", "线次排", 2, 4)]
    r, w, _ = pa.admit(relay, [])
    assert [x["name"] for x in r] == ["线最强"]
    assert [x["name"] for x in w] == ["线次排"]


def test_front_slot_kept_even_if_it_was_watch_before():
    """⛔ 原在 watch 的最强票要升进接力（否则龙头留在观察池、后排占接力名额）。"""
    relay = [_row("600002", "原接力后排", 2, 3)]
    watch = [_row("600001", "原观察龙头", 3, 6)]
    r, w, _ = pa.admit(relay, watch)
    assert [x["name"] for x in r] == ["原观察龙头"]
    assert [x["name"] for x in w] == ["原接力后排"]


def test_first_board_without_carry_dropped(monkeypatch):
    """⛔ 2026-11-03 改口径（用户转述 NIKA「首板就不该进来」）：首板零带动**不进池**。

    旧口径是「进观察池不占接力名额」。实测 11-03：竞价卡 11 只里 7 只是 1 板，
    4 只跟风首板（国金证券带动1 / 统一股份2 / 华瓷股份0 / 三峡新材1）与唯一
    6 板龙头并排显示，且按综合分排在龙头前面 → 卡面读起来像在推杂毛。
    """
    relay = []
    watch = [_row("600001", "首板独走", 1, 0, theme="杂线", mates=5)]
    r, w, diag = pa.admit(relay, watch)
    assert not r and not w, "首板零带动连观察池都不该进"
    assert diag["first_board_dropped"] == ["首板独走"]


def test_first_board_gate_rollback(monkeypatch):
    """`POOL_ADMIT_FIRST_BOARD_MIN_CARRY=0` → 逐字节回到旧口径（首板进观察池）。"""
    monkeypatch.setenv(pa.FIRST_BOARD_MIN_CARRY_ENV, "0")
    r, w, _ = pa.admit([], [_row("600001", "首板独走", 1, 0, theme="杂线", mates=5)])
    assert not r and [x["name"] for x in w] == ["首板独走"]


def test_first_board_with_carry_still_goes_watch():
    """首板即使带动了板块，也不进接力池（2026-10-27 用户「推的全是杂毛」）。

    实证：文投控股 10-27 首板、带动 3 家、09:41 封、炸 5 次、封单占流通 0.12%、梯队 D，
    旧口径「首板 + carry>0 就算接力格」把它从观察池提拔成**接力池第 2 位**。
    接力 = 连板晋级，首板没有晋级可接。⛔ 例外：`was_relay=True`（四档 1进2 已判过格）。
    """
    relay = []
    watch = [_row("600715", "文投控股", 1, 3, fbt="094125", theme="大消费", mates=11)]
    r, w, _ = pa.admit(relay, watch)
    assert not r
    assert [x["name"] for x in w] == ["文投控股"]
    # 四档已判过格的 1进2 首板仍可留在接力
    assert pa.relay_worth(_row("600715", "文投控股", 1, 3), was_relay=True) is True


# ── ④ 两条豁免：缺数据 / 小题材 ─────────────────────────────────────────
def test_theme_missing_group_is_not_eliminated():
    """⛔ 题材取不到 = 数据缺失，不是「它是杂毛」→ 全留，不许拿缺失当证据砍票。"""
    relay = [_row("600001", "无题材甲", 2, 3, theme=""),
             _row("600002", "无题材乙", 2, 2, theme=""),
             _row("600003", "无题材丙", 1, 1, theme="")]

    for r0 in relay:
        r0["core_theme"] = ""
        r0["theme_em_name"] = None
        r0["theme_name"] = None
    r, w, diag = pa.admit(relay, [])
    assert len(r) + len(w) == 3
    assert diag["theme_missing"] == 3
    assert diag["dropped"] == 0


def test_small_line_is_not_eliminated():
    """涨停池只有 1~2 家的「线」没有后排可言 → 不淘汰。"""
    relay = [_row("600001", "小题材甲", 2, 1, theme="小题材", mates=1),
             _row("600002", "小题材乙", 2, 0, theme="小题材", mates=1)]
    r, w, diag = pa.admit(relay, [])
    assert len(r) + len(w) == 2
    assert diag["dropped"] == 0
    assert any(g.get("note") == "small_line" for g in diag["groups"])


# ── ⑤ 开关与幂等 ───────────────────────────────────────────────────────
def test_switch_off_restores_old_pool(monkeypatch):
    """⛔ 一键回退：`POOL_ADMIT_ENABLED=0` → 池子逐字节不变。"""
    monkeypatch.setenv(pa.ENABLED_ENV, "0")
    relay = [_row(f"60000{i}", f"票{i}", 2, i) for i in range(1, 6)]
    watch = [_row(f"60001{i}", f"观{i}", 2, i) for i in range(1, 4)]
    r, w, diag = pa.admit(relay, watch)
    assert len(r) == len(relay) and len(w) == len(watch)
    assert diag["enabled"] is False


def test_idempotent():
    """同一输入多次调用，结果完全一致（纯函数）。"""
    relay = [_row(f"60000{i}", f"票{i}", 2, i) for i in range(1, 6)]
    a1, b1, _ = pa.admit(relay, [])
    a2, b2, _ = pa.admit(copy.deepcopy(relay), [])
    assert [x["code"] for x in a1] == [x["code"] for x in a2]
    assert [x["code"] for x in b1] == [x["code"] for x in b2]


def test_empty_pool_is_safe():
    r, w, diag = pa.admit([], [])
    assert r == [] and w == []
    assert diag["dropped"] == 0


def test_theme_grouping_uses_em_name_not_industry():
    """⛔ 分组必须用炒作题材（theme_em_name），⛔ 不能用申万行业。

    实测病根：机器人线跨 通用设备/消费电子/汽车零部/照明设备/数字媒体 5 个行业，
    按行业分组 → 每只都是"行业龙一" → 11 只候选 9 只 `is_sector_dragon=True` → 全进池。
    """
    relay = [
        # 同一炒作题材、不同行业 → 必须同组、只留 1 只
        _row("600001", "机器人龙头", 3, 5, industry_sector="照明设备"),
        _row("600002", "机器人跟风", 2, 7, industry_sector="通用设备"),
        _row("600003", "机器人跟风2", 2, 6, industry_sector="消费电子"),
    ]
    r, w, diag = pa.admit(relay, [], per_line_cap=2)
    assert len(r) + len(w) == 2, "同题材跨行业必须只留 2 只"
    assert diag["groups"][0]["theme"] == "机器人"


def test_theme_grouping_still_one_group_when_unlimited():
    """解除只数限制后，跨行业同题材仍归**同一组**（分组口径没被放宽）。"""
    relay = [
        _row("600001", "机器人龙头", 3, 5, industry_sector="照明设备"),
        _row("600002", "机器人跟风", 2, 7, industry_sector="通用设备"),
        _row("600003", "机器人跟风2", 2, 6, industry_sector="消费电子"),
    ]
    r, w, diag = pa.admit(relay, [])
    assert len(r) + len(w) == 3, "不限只数 → 三只全留"
    assert len(diag["groups"]) == 1 and diag["groups"][0]["theme"] == "机器人"


# ── ⑥ 回归锚：真实数据形状 ─────────────────────────────────────────────
def test_real_shape_2026_10_20():
    """用 10-20 真实结构（机器人 5 只 + 各线 1 只）验证：11 → 8。"""
    rows = [
        _row("301313", "凡拓数创", 2, 7, "095109", "机器人", 24, 62.6),
        _row("002164", "宁波东力", 2, 7, "093445", "机器人", 24, 75.83),
        _row("603626", "科森科技", 2, 6, "094409", "机器人", 24, 74.35),
        _row("603119", "浙江荣泰", 2, 7, "100302", "机器人", 24, 40.07),
        _row("002724", "海洋王", 3, 5, "093018", "机器人", 24, 76.16),
        _row("603068", "博通集成", 1, 2, "103340", "端侧AI", 4, 65.25),
        _row("603636", "南威软件", 1, 2, "141333", "影视传媒", 3, 62.04),
        _row("003029", "吉大正元", 4, 3, "092500", "低空经济", 2, 58.21),
        _row("600148", "长春一东", 3, 0, "093010", "军工", 3, 61.46),
        _row("600267", "海正药业", 2, 0, "093031", "医药", 3, 81.09),
        _row("002929", "润建股份", 2, 0, "142000", "AI算力", 4, 37.09),
    ]
    relay, watch = rows[:3], rows[3:]
    r, w, diag = pa.admit(relay, watch, per_line_cap=2)
    # ⚠️ 2026-11-03：首板闸上线后 8 → 6。博通集成/南威软件都是 1 板且带动仅 2 家，
    #    按「首板要有带动才算 1进2 候选」被剔；要回到 8 只请设
    #    POOL_ADMIT_FIRST_BOARD_MIN_CARRY=0。
    # ⚠️ 2026-11-04：默认已解除每线两只 → 本用例显式传 per_line_cap=2 保留旧口径回归。
    assert len(r) + len(w) == 6, f"应为 6 只，实际 {len(r) + len(w)}"
    assert set(diag["first_board_dropped"]) == {"博通集成", "南威软件"}
    kept = {x["name"] for x in r + w}
    # 机器人线：3 板海洋王 + 2 板带动最多且最早封的宁波东力
    assert "海洋王" in kept and "宁波东力" in kept
    # 后排三只必须被挡
    assert not ({"凡拓数创", "浙江荣泰", "科森科技"} & kept)


def test_buy_decision_unaffected_by_admit():
    """⛔ 准入闸只筛「进不进池」，不碰 9:25 裁决（关掉它，裁决逻辑照旧）。"""
    import os
    # 本测试只断言「开关存在且不引发异常」，裁决逻辑的契约在
    # tests/test_defense_gate.py / test_pool_call*.py 里锁。
    os.environ[pa.ENABLED_ENV] = "0"
    try:
        r, w, diag = pa.admit([_row("600001", "票", 3, 5)], [])
        assert diag["enabled"] is False
        assert len(r) + len(w) == 1
    finally:
        os.environ.pop(pa.ENABLED_ENV, None)


# ── ⑦ 桶标签必须跟着改 ────────────────────────────────────────────────
def test_bucket_fields_follow_new_assignment():
    """⛔ 分桶改了，`bucket`/`bucket_label` 必须同步。

    实测踩坑：宁波东力被改判进观察池，卡面却仍显示「接力池」——同一行自相矛盾。
    """
    relay = [_row("600001", "原接力后来降级", 2, 3)]
    watch = [_row("600002", "原观察升上来了", 3, 6)]
    r, w, _ = pa.admit(relay, watch)
    assert r[0]["bucket"] == "relay" and r[0]["bucket_label"] == "接力池"
    assert w[0]["bucket"] == "watch" and w[0]["bucket_label"] == "观察池"


# ══════════════════════════════════════════════════════════════════════
# ⑧ 池外龙头校验（2026-10-21 用户「可以加一层池外龙头校验」）
# ══════════════════════════════════════════════════════════════════════
def _zt(*rows):
    """涨停池（zt_history 格式）。"""
    return [{"code": c, "name": n, "boards": b} for c, n, b in rows]


def test_outpool_front_when_self_is_line_top():
    pool = [_row("600001", "线龙头", 3, 5, mates=5)]
    res = pa.outpool_dragon_check(pool, _zt(("600001", "线龙头", 3), ("600002", "跟风", 2)))
    assert res["rows"]["600001"]["tier"] == "front"


def test_outpool_second_when_top_is_in_pool():
    """自己不是最高，但池内有最高板兜着 → second（允许的第二席活口）。"""
    pool = [_row("600001", "线龙头", 3, 5), _row("600002", "次排", 2, 4)]
    res = pa.outpool_dragon_check(pool, _zt(("600001", "线龙头", 3), ("600002", "次排", 2)))
    assert res["rows"]["600002"]["tier"] == "second"
    assert "线龙头" in res["rows"]["600002"]["why"]


def test_outpool_back_when_line_top_is_outside_pool():
    """⛔ 核心场景：这条线最强的在池外 → 池内这只是硬凑的后排。"""
    pool = [_row("600002", "池内后排", 2, 4)]
    # 同题材的 3 板龙头（600009）不在池内
    pool[0]["theme_mates"] = ["600002", "600009"]
    res = pa.outpool_dragon_check(
        pool, _zt(("600009", "池外真龙", 3), ("600002", "池内后排", 2)))
    assert res["rows"]["600002"]["tier"] == "back"
    assert res["back"] == ["600002"]
    # 卡面要用的字段
    assert res["rows"]["600002"]["line_top"] == 3
    assert res["rows"]["600002"]["line_tops"] == ["池外真龙"]


def test_outpool_unknown_when_zt_missing():
    """⛔ 涨停池缺失 → 全部 unknown，⛔ 不许猜「它是后排」。"""
    pool = [_row("600001", "票甲", 2, 3), _row("600002", "票乙", 2, 2)]
    res = pa.outpool_dragon_check(pool, None)
    assert res["summary"]["unknown"] == 2
    assert res["back"] == []
    for v in res["rows"].values():
        assert "涨停池缺失" in v["why"]


def test_outpool_unknown_when_theme_missing():
    """⛔ 题材取不到 → 没法比 → unknown（缺数据不作不利证据）。"""
    pool = [_row("600001", "无题材", 2, 3, theme="")]
    pool[0]["core_theme"] = ""
    pool[0]["theme_em_name"] = None
    pool[0]["theme_name"] = None
    res = pa.outpool_dragon_check(pool, _zt(("600001", "无题材", 2)))
    assert res["rows"]["600001"]["tier"] == "unknown"
    assert res["back"] == []


def test_outpool_front_when_only_one_in_line():
    """⛔ 这条线只有它 1 家 → 池外当然没有更强的 → front，⛔ 不是 lone。

    校验要回答的是「池外有没有更强的」，本场景答案是没有。
    """
    pool = [_row("600001", "独苗", 2, 0, theme="独苗题材", mates=1)]
    res = pa.outpool_dragon_check(pool, _zt(("600001", "独苗", 2)))
    assert res["rows"]["600001"]["tier"] == "front"


def test_outpool_accepts_ztgc_format():
    """⛔ 必须吃下东财原始格式（dm/mc/lbc）——否则「涨停池 54 只却全匹配不上」静默失效。"""
    pool = [_row("600001", "线龙头", 3, 5)]
    ztgc = [{"dm": "600001", "mc": "线龙头", "lbc": 3}]
    res = pa.outpool_dragon_check(pool, ztgc)
    assert res["zt_rows"] == 1
    assert res["rows"]["600001"]["tier"] == "front"


def test_outpool_switch_off():
    import os
    os.environ[pa.OUTPOOL_ENV] = "0"
    try:
        res = pa.outpool_dragon_check([_row("600001", "票", 2, 3)], [])
        assert res["enabled"] is False and res["rows"] == {}
    finally:
        os.environ.pop(pa.OUTPOOL_ENV, None)


def test_outpool_drop_is_on_by_default():
    """默认**真砍**后排（2026-10-27 改）；`POOL_ADMIT_OUTPOOL_DROP=0` 可一键回滚。

    演进：10-21 用户「我来看看对不对」→ 只标记不砍；10-27 实证判对了（文投控股被判
    「这条线最高 5 板是上海九百，池内没有 → 后排」），但因为没砍，它照样被推进接力池 →
    用户当场骂「全是杂毛」。判断已验证，故默认改为真砍。
    """
    import os
    assert pa.outpool_drop_enabled() is True
    os.environ[pa.OUTPOOL_DROP_ENV] = "0"
    try:
        assert pa.outpool_drop_enabled() is False
    finally:
        os.environ.pop(pa.OUTPOOL_DROP_ENV, None)


# ── ⑨ 卡面胶囊（只在「后排」时打）──────────────────────────────────────
def test_card_badge_only_for_back():
    from pipelines import build_cards as bc
    assert bc._outpool_badge({"line_tier_outpool": "front"}) == ""
    assert bc._outpool_badge({"line_tier_outpool": "second"}) == ""
    assert bc._outpool_badge({"line_tier_outpool": "unknown"}) == ""
    assert bc._outpool_badge({}) == ""
    got = bc._outpool_badge({"line_tier_outpool": "back",
                             "line_top": 3, "line_tops": ["池外真龙"]})
    assert "这条线最强 3 板在池外" in got and "池外真龙" in got


def test_card_badge_does_not_explain_mechanism():
    """⛔ 胶囊只讲事实，不许出现「所以它是后排」这类机制解释。"""
    from pipelines import build_cards as bc
    got = bc._outpool_badge({"line_tier_outpool": "back",
                             "line_top": 3, "line_tops": ["甲", "乙"]})
    for banned in ("它是后排", "池内没有", "准入", "校验"):
        assert banned not in got, banned


def test_zbc_over_limit_dropped():
    """2026-11-11 用户 A：炸板 **>5** 才丢；5 次仍保留。"""
    relay = [_row("600001", "炸九次", 3, 5, "093000"),
             _row("600002", "炸六次", 2, 5, "093100"),
             _row("600003", "炸五次", 2, 5, "093200"),
             _row("600004", "没炸", 2, 5, "093300")]
    relay[0]["zbc"] = 9
    relay[1]["zbc"] = 6
    relay[2]["zbc"] = 5
    relay[3]["zbc"] = 0
    r, w, diag = pa.admit(relay, [])
    kept = {x["name"] for x in r + w}
    assert "炸九次" not in kept and "炸六次" not in kept
    assert "炸五次" in kept and "没炸" in kept
    assert set(diag["zbc_dropped"]) == {"炸九次", "炸六次"}


def test_zbc_gate_can_be_disabled():
    """回退开关：POOL_ADMIT_MAX_ZBC=99 → 闸关闭。"""
    import os
    os.environ[pa.MAX_ZBC_ENV] = "99"
    try:
        relay = [_row("600001", "炸十次", 3, 5, "093000")]
        relay[0]["zbc"] = 10
        r, w, diag = pa.admit(relay, [])
        assert {x["name"] for x in r + w} == {"炸十次"}
    finally:
        os.environ.pop(pa.MAX_ZBC_ENV, None)
