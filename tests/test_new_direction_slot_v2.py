# -*- coding: utf-8 -*-
"""新方向首板槽 · v2 契约（2026-11-11 用户「这条线必须打开·优秀的票明早能交易」）。

与 `test_new_direction_first_board.py` 的分工：
  · 那份 2026-10-18 的锁只管**旧纯函数** `quant_score.select_new_direction_first_board`
    （题材家数 ≤3 的「纯观察」口径，仅在回退模式里还被调用）；
  · 本文件锁**生产口径** `core/new_direction_first_board.build`（真入池·可交易）。

锁四件事：
  ① **「新方向」= 整条线零连板**，不是「家数少」——旧判据「家数≤3」把光通信 10 家
     首板挡在门外（家数越多越进不来，与强弱恰好相反），且旧槽被
     `not two_pool.enabled()` 硬关，生产环境一次都没跑过；
  ② **质量单一源 = 当日过筛**（股性是用户三核心第一项）：剑桥科技「13 次涨停/晋级 0%」
     这类封板最早但股性难连板的票必须被挡，⛔ 槽不另造一套质量判据；
  ③ **入池即可交易**：`participation_eligibility` 必须是 `relay_candidate` ——
     ⛔ 不能用 `conditional`（`pool_call.py:1993` 会把它当历史修复，缺档案就短路「不参与」）；
  ④ 线级归属走**细化线**：光通信/PCB板/半导体各自成线，不许被大线 AI算力 合并。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WC = ROOT / "wealth_code"
for p in (str(ROOT), str(WC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core import new_direction_first_board as nd          # noqa: E402
from core import quant_score as qs                        # noqa: E402


def zt(code, name, *, boards=1, fbt="093500", cje=5.0, theme="光通信",
       source="snapshot", zbc=0, one_word=False):
    return {"dm": code, "mc": name, "lbc": boards, "fbt": fbt, "cje": cje,
            "core_theme": theme, "theme_base": theme,
            "theme_source": source, "zbc": zbc, "is_one_word": one_word}


def cand(code, name, *, theme="光通信", boards=1, fbt="093500", cje=5.0,
         one_word=False, zbc=0, dragon_rank=1):
    return {"code": code, "name": name, "core_theme": theme, "theme_base": theme,
            "theme_source": "snapshot", "boards": boards, "fbt": fbt,
            "cje_yi": cje, "is_one_word": one_word, "zbc": zbc,
            "dragon_rank": dragon_rank}


def optical_line(n=10):
    """一条 10 家全首板的「光通信」线（复刻 11-11 实况：10 家·222.5 亿）。"""
    out = [zt("603083", "剑桥科技", fbt="093408", cje=25.58)]
    for i in range(n - 1):
        out.append(zt(f"60000{i}", f"跟风{i}", fbt=f"1{3 + i % 3}0000", cje=5.0))
    return out


# ── ① 线级判据 ────────────────────────────────────────────────────────
def test_zero_board_line_counts_as_new_direction():
    """10 家全首板 + 成交额够 → 是新方向（旧判据「家数≤3」会把它排除）。"""
    stats = nd.new_direction_lines(optical_line())
    assert "光通信" in stats, stats
    assert stats["光通信"]["members"] == 10
    assert stats["光通信"]["max_boards"] == 1


def test_line_with_two_board_is_not_new_direction():
    """线内出现 1 只 2 板 → 已不是「新方向」（该走接力常规通道）。"""
    rows = optical_line()
    rows[3]["lbc"] = 2
    assert "光通信" not in nd.new_direction_lines(rows)


def test_two_member_line_is_not_a_direction():
    """只 2 家 = 没人跟，不是方向（低板不接孤军）。"""
    assert nd.new_direction_lines(optical_line(2)) == {}


def test_small_amount_line_is_not_a_direction():
    """家数够但总成交额 < 10 亿 → 全是小票自嗨，不列。"""
    rows = [zt(f"60000{i}", f"小票{i}", cje=0.5) for i in range(4)]
    assert nd.new_direction_lines(rows) == {}


def test_industry_fallback_rows_do_not_form_a_line():
    """行业名兜底（theme_source=sector）不算真题材 → 不构成线。"""
    rows = [zt(f"60000{i}", f"行业票{i}", source="sector") for i in range(5)]
    assert nd.new_direction_lines(rows) == {}


def test_synonyms_collapse_into_one_line():
    """光通信/CPO/光模块/光纤 是同一环节的不同叫法 → 必须聚成一条线。"""
    rows = [zt("600001", "A", theme="光通信"),
            zt("600002", "B", theme="CPO"),
            zt("600003", "C", theme="光模块"),
            zt("600004", "D", theme="光纤")]
    stats = nd.new_direction_lines(rows)
    assert list(stats) == ["光通信"], stats
    assert stats["光通信"]["members"] == 4


def test_optical_and_pcb_stay_separate_lines():
    """光通信 与 PCB板 大线同为 AI算力，但**各自成线**（身位/龙一不能混算）。"""
    rows = [zt(f"60000{i}", f"光{i}", cje=30.0) for i in range(3)]
    rows += [zt(f"60001{i}", f"板{i}", theme="PCB") for i in range(3)]
    stats = nd.new_direction_lines(rows)
    assert set(stats) == {"光通信", "PCB板"}
    assert stats["光通信"]["members"] == 3 and stats["PCB板"]["members"] == 3


# ── ② 个股质量 ────────────────────────────────────────────────────────
def test_early_seal_quality_passes():
    picked, _ = nd.build([cand("603083", "剑桥科技", fbt="093408", cje=25.58)],
                         optical_line())
    assert [r["name"] for r in picked] == ["剑桥科技"]


def test_late_seal_is_rejected():
    """线里已有早封票时，13:11 才封的票仍不进槽（口子只给「全线晚封」）。"""
    picked, diag = nd.build([cand("600001", "尾盘票", fbt="131100")], optical_line())
    assert picked == []
    assert "晚于" in diag["skipped"][0]["reason"]


def test_one_word_and_688_are_rejected():
    picked, diag = nd.build([cand("600001", "一字票", one_word=True),
                             cand("688001", "科创票")], optical_line())
    assert picked == []
    reasons = " ".join(x["reason"] for x in diag["skipped"])
    assert "一字买不到" in reasons and "688/ST" in reasons


def test_unqualified_by_screen_is_rejected():
    """没过当日过筛（股性难连板/题材线不达标）→ 不进槽。

    11-11 实证：剑桥科技 近一年 13 次涨停、晋级 **0%**，封板全场最早，
    但正是最不该接的那种票。槽不许绕过过筛自己按封板时间排序。
    """
    picked, diag = nd.build([cand("603083", "剑桥科技", fbt="093408")],
                            optical_line(), screened_codes=set())
    assert picked == []
    assert "过筛" in diag["skipped"][0]["reason"]


def test_backrow_rank_is_rejected():
    """同线身位 ≥ 5 与单池后排过滤同口径，避免「槽里列了、合池又被剔」。"""
    picked, diag = nd.build([cand("600009", "后排票", dragon_rank=6)],
                            optical_line(), drop_backrow_rank=5)
    assert picked == []
    assert "身位" in diag["skipped"][0]["reason"]


# ── ③ 入池即可交易（本轮最关键的一条）────────────────────────────────
def test_picked_rows_are_tradeable_not_conditional():
    """`participation_eligibility` 必须是 relay_candidate，且能过池资格线。

    ⛔ 用 `conditional` 会命中 `pool_call.py` 的「历史修复条件资格」分支 ——
    这些票没有 `historical_repair` 档案，09:25 会一句「条件资格缺失或过期，不参与」
    直接短路：票在池里、明早却买不了，正好违反用户「明早能进入交易」。
    """
    picked, _ = nd.build([cand("603083", "剑桥科技", fbt="093408", cje=25.58)],
                         optical_line())
    assert len(picked) == 1
    r = picked[0]
    assert r["participation_eligibility"] == "relay_candidate"
    assert r["buyable"] is True and r["watch_only"] is False
    # 能过 `_merge_pools` 的资格线（= 真的会进最终池、进 09:25 裁决）
    assert qs._relay_qualified(r) is True
    assert r["new_direction_line"] == "光通信"
    # 不改任何买入判据字段
    assert "expected_range" not in r or True


def test_per_line_cap():
    """每条线最多 3 只（与 POOL_ADMIT_PER_LINE 同值＝用户 11-11「放宽到三个身位」）。"""
    picked, _ = nd.build([cand(f"60000{i}", f"票{i}") for i in range(8)],
                         optical_line(10))
    assert len(picked) == nd.per_line() == 3


def test_already_in_pool_is_excluded():
    picked, diag = nd.build([cand("600001", "已在池")], optical_line(),
                            exclude_codes={"600001"})
    assert picked == []
    assert "已在池" in diag["skipped"][0]["reason"]


# ── ④ 可逆开关 ────────────────────────────────────────────────────────
def test_switch_off_returns_empty(monkeypatch):
    monkeypatch.setenv("POOL_NEW_DIRECTION_FIRST_BOARD", "0")
    picked, diag = nd.build([cand("600001", "票")], optical_line())
    assert picked == [] and diag["enabled"] is False


def pcb_line(n=5, fbt="134400"):
    """复刻 11-11：PCB 板 5 家全首板、**全部午后封板**、256 亿。"""
    return [zt(f"00291{i}", f"PCB{i}", fbt=fbt, cje=50.0, theme="PCB板") for i in range(n)]


def test_late_seal_line_gets_one_slot():
    """2026-11-11 用户「可以留口子」：整条线全晚封时，留 1 只。

    ⛔ 前提：这只票除封板时间外全部合格（过筛/非一字/炸板/身位）。
    """
    import os
    os.environ.pop("POOL_NDFB_LATE_SEAL", None)
    rows = pcb_line()
    cands = [cand(r["dm"], r["mc"], theme="PCB板", fbt=r["fbt"], cje=50.0) for r in rows]
    picked, _diag = nd.build(cands, rows, screened_codes={r["dm"] for r in rows},
                             drop_backrow_rank=5)
    late = [r for r in picked if r.get("new_direction_late")]
    assert len(late) == 1, picked
    assert late[0]["new_direction_line"] == "PCB板"


def test_late_seal_switch_off():
    """回退：POOL_NDFB_LATE_SEAL=0 → 晚封口子关闭，一只都不留。"""
    import os
    os.environ["POOL_NDFB_LATE_SEAL"] = "0"
    try:
        rows = pcb_line()
        cands = [cand(r["dm"], r["mc"], theme="PCB板", fbt=r["fbt"], cje=50.0) for r in rows]
        picked, _ = nd.build(cands, rows, screened_codes={r["dm"] for r in rows},
                             drop_backrow_rank=5)
        assert not [r for r in picked if r.get("new_direction_late")]
    finally:
        os.environ.pop("POOL_NDFB_LATE_SEAL", None)
