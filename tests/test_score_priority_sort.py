#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排序口径契约锁（2026-11-04 用户「按照打分来排序」）。

## 用户原话

> 「平起平坐没问题，但是在排序的时候尽量做好打分这块，按照打分来排序」

## 改前的真实病灶（不是猜的）

11-03 落盘候选的展示顺序：

| # | 名称 | 板 | raw |
|---|---|---|---|
| 1 | 圣阳股份 | 1 | **63.7（全池最低）** |
| 7 | 东方海洋 | **4** | **79.0（全池最高）** |

根因：`priority_key` 在 `core_active` 下走 `selection_key`，其键为
`(角色, 是否龙一, 带动, 连续天数, 历史最高板, 联动, 竞争, -线内排名, 晋级率, raw)`
—— **板数不在有效位、综合分排最后**，实际决定先后的是「带动 + 连续天数」。
`_leader_preference_key`（非 active 分支）虽然含板数，但综合分同样在最后一位。

## 本文件锁死的四条

1. **龙头优先不动**：板块龙 / 复盘倾斜仍在前两位（「接力票必须是龙头」是铁律）。
2. **同层级内按打分**：板数之后先比综合分，再比带动/题材广度。
3. **口径说明与实际一致**：`leader_preference_note` 必须反映真实排序键，不得写 A 做 B。
4. **可逆**：`ENABLE_SCORE_PRIORITY_SORT=0` 回到旧键。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wealth_code"))

import core.quant_score as qs  # noqa: E402


def _cand(boards, raw, *, dragon=False, carry=0, mates=5, score=None):
    """构造一只候选（`_cand` 是 quant_score 内部读取的原始行）。"""
    return {
        "code": f"6000{boards}{int(raw)}",
        "lbc": boards,
        "_raw": raw,
        "leaderFactorScore": 50.0,
        "_cand": {
            "code": f"6000{boards}{int(raw)}",
            "boards": boards,
            "is_sector_dragon": dragon,
            "carry_cnt": carry,
            "theme_mates_cnt": mates,
            "review_bonus": 0.0,
            "score": score if score is not None else raw,
        },
    }


def _pref_key(row):
    """复刻 `_leader_preference_key`（内部闭包，无法直接调用）。"""
    cand = row["_cand"]
    order = [
        float(bool(cand.get("is_sector_dragon"))),
        cand.get("review_bonus") or 0.0,
        row.get("lbc") or 0,
    ]
    if qs._SCORE_SORT:
        order += [row.get("_raw") or 0.0,
                  cand.get("carry_cnt") or 0,
                  cand.get("theme_mates_cnt") or 0,
                  row.get("leaderFactorScore") or 0]
    else:
        order += [cand.get("carry_cnt") or 0,
                  cand.get("theme_mates_cnt") or 0,
                  row.get("leaderFactorScore") or 0,
                  row.get("_raw") or 0.0]
    return tuple(order)


def test_score_排序_默认开启():
    assert qs._SCORE_SORT is True, "默认必须是开启状态（用户 2026-11-04 明确要求）"


def test_同板数内按综合分排序():
    """⛔ 改前：同板数内先比带动 → 低分高带动排前面。改后：先比分数。"""
    low_score_high_carry = _cand(2, 55.0, carry=8)
    high_score_low_carry = _cand(2, 90.0, carry=1)
    ranked = sorted([low_score_high_carry, high_score_low_carry], key=_pref_key, reverse=True)
    assert ranked[0] is high_score_low_carry, "同板数内应让分数高的在前，而不是带动家数多的"


def test_板数优先于综合分():
    """⛔ 低位高分票不得顶到高板票前面（这条铁律没变）。"""
    high_board_low_score = _cand(4, 50.0)
    low_board_high_score = _cand(1, 100.0)
    ranked = sorted([low_board_high_score, high_board_low_score], key=_pref_key, reverse=True)
    assert ranked[0] is high_board_low_score, "板数必须优先于综合分"


def test_板块龙优先于板数与分数():
    """「接力票必须是龙头」铁律：龙一压过更高板/更高分的非龙一。"""
    dragon_1board = _cand(1, 40.0, dragon=True)
    plain_5board = _cand(5, 95.0)
    ranked = sorted([plain_5board, dragon_1board], key=_pref_key, reverse=True)
    assert ranked[0] is dragon_1board, "板块龙一必须排最前，不得被高板/高分顶掉"


def test_关掉开关回到旧键(monkeypatch):
    """`ENABLE_SCORE_PRIORITY_SORT=0` → 同板数内回到「先带动后分数」。"""
    monkeypatch.setattr(qs, "_SCORE_SORT", False)
    low_score_high_carry = _cand(2, 55.0, carry=8)
    high_score_low_carry = _cand(2, 90.0, carry=1)
    ranked = sorted([low_score_high_carry, high_score_low_carry], key=_pref_key, reverse=True)
    assert ranked[0] is low_score_high_carry, "旧键下应回到按带动家数排序"


def test_口径说明与排序键一致():
    """⛔ 页面写着 A、代码做着 B 是硬伤：note 必须反映真实键。"""
    assert qs.sort_note() == "板块龙→板数→综合分→带动性→题材广度"


def test_口径说明随开关切换(monkeypatch):
    """关掉开关时文案也要跟着回退，不能留旧说明配新行为。

    ⛔ 用 `setattr` 切开关、**绝不 `importlib.reload`** —— reload 会替换模块对象，
      其他模块已持有的 `from core.quant_score import ...` 引用仍指向旧版本，
      实测污染 `test_snapshot_guard` 4 例（TypeError）。这也是 `sort_note()`
      被做成函数而非模块级常量的原因。
    """
    monkeypatch.setattr(qs, "_SCORE_SORT", False)
    assert qs.sort_note() == "板块龙→连板高度→带动性→题材广度→综合分"
    monkeypatch.setattr(qs, "_SCORE_SORT", True)
    assert qs.sort_note() == "板块龙→板数→综合分→带动性→题材广度"


def test_真实_11_03_排序不再把4板排到末位():
    """用真实归档复现：新键下 4 板东方海洋必须在 1 板票之前。

    ⛔ 只断言「相对位置」，不断言具体名次 —— 名次会随规则演进变化，
      但「4 板不该排第 7」是本轮要根治的性质。
    """
    import json

    p = Path(__file__).resolve().parents[1] / "wealth_code" / "cache" / "candidates-2026-11-03.json"
    if not p.exists():
        pytest.skip("归档不存在")
    data = json.loads(p.read_text(encoding="utf-8"))
    pool = (data.get("relay") or []) + (data.get("watch") or [])
    if not pool:
        pytest.skip("该期无候选")

    rows = []
    for s in pool:
        rows.append({
            "code": s["code"], "name": s["name"], "lbc": s["boards"],
            "_raw": s.get("raw_score") or 0.0,
            "leaderFactorScore": s.get("lead_score") or 0.0,
            "_cand": {
                "is_sector_dragon": s.get("is_sector_dragon"),
                "carry_cnt": s.get("carry_cnt"),
                "theme_mates_cnt": s.get("theme_mates_cnt"),
                "review_bonus": 0.0,
            },
        })
    ranked = sorted(rows, key=_pref_key, reverse=True)
    names = [r["name"] for r in ranked]
    pos_4b = next((i for i, r in enumerate(ranked) if r["lbc"] >= 4), None)
    pos_1b = [i for i, r in enumerate(ranked) if r["lbc"] == 1]
    if pos_4b is None or not pos_1b:
        pytest.skip("该期不含 4 板或 1 板")
    assert pos_4b < min(pos_1b), f"4 板仍被 1 板压在后面：{names}"


# ══════════════════════════════════════════════════════════════════════════
# ⓵ 符号方向护栏（2026-11-04 实际踩坑）
# ══════════════════════════════════════════════════════════════════════════
# `selection_key` 被 `sorted(..., reverse=True)` 使用 → **key 越大越优先**。
# 第一版把板数与综合分写成 `-boards` / `-raw`（沿用了 `leader_key` 的「越小越强」直觉），
# 结果变成「板数低的、分数低的优先」——raw 最低的 000013 反而被顶进接力池，
# 被 `test_preselect_status_priority::test_unconfirmed_or_conflicting_rank_not_preferred`
# 当场抓出（7 个参数全红）。
# ⛔ 这条护栏直接读源码：只要 `_SCORE_SORT` 分支里出现 `-_to_num`，立即失败。
def test_selection_key_不得使用负号():
    import inspect

    src = inspect.getsource(qs.select_relay_watch)
    start = src.index("def selection_key")
    end = src.index("raw_key = ", start)
    block = src[start:end]
    assert "-_to_num" not in block, (
        "selection_key 被 reverse=True 排序，板数/分数必须取正值。"
        "出现 -_to_num 意味着把「低板/低分优先」，是 2026-11-04 踩过的坑。"
    )
    assert "reverse=True" in src, "前提变了：排序不再是 reverse=True，请重新核对符号方向"


def test_两个排序键的分数方向一致():
    """`_leader_preference_key`（非 active）与 `selection_key`（active）必须同向。

    ⛔ 两套键给出相反的先后，是最难查的一类 bug —— 同为「按打分排序」，
      一条链路正着排、另一条反着排，落盘结果还都「看起来有分数」。
    """
    import inspect

    pref_src = inspect.getsource(qs.select_relay_watch)
    # 非 active 键里综合分必须是正值（reverse=True → 大在前）
    assert "_to_num(row.get(\"_raw\"))," in pref_src or '_to_num(row.get("_raw")),' in pref_src, (
        "_leader_preference_key 里的综合分应为正值"
    )
    # active 键同理
    assert '_to_num(m.get("_raw")),' in pref_src, "selection_key 里的综合分应为正值"
