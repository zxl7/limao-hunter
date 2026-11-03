#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""晋级三问 —— 「高胜率 · 可晋级 · 板块地位高」（2026-10-30 用户定）

## 为什么要有这个模块

用户原话：「优化推送高胜率可晋级板块地位高 的个股」。

盘后候选现在推的票，是按综合分排的。综合分里塞了很多东西（容量、卡位、情绪、
盯盘价值……），**唯独没有一句话回答「这只票明天凭什么能晋级」**。
结果就是跟风票、孤票、没样本的票都能进接力池占版面。

本模块只回答三个是非题，三问全过才算「三高」：

| 问 | 判据 | 为什么这么定 |
|---|---|---|
| ① 高胜率 | `promo_rate_hist ≥ 40%` **且** `zt_250 ≥ 8` | 40% 直接复用连板龙阈值（不新造第二套标准）；样本门槛防「4 次涨停里蒙对 3 次 = 75%」的假强 |
| ② 可晋级 | `boards ≤ 2` **且** `max_boards_hist > boards` | 用户纪律「必须在第 1~2 板发现它」（第 4 板已是另一笔交易）；历史最长板证明它真走过更高 |
| ③ 板块地位 | `leader_role` 含「龙头」**且** `theme_mates_cnt ≥ 3` | 身位是龙一，但题材只有它自己 1 家 = 孤票，没有板块效应，不叫地位 |

## ⛔ 三条硬边界（用户多次重申，不要越线）

1. **不改评分**：`raw_score` / `score` / `dim_panel` 一概不碰，只新增 `promotion_quiz` 字段。
2. **不改顺序**：本模块不排序，也不产名额/仓位。要不要按「三高」重排，由调用方（人）决定。
3. **不编数据**：档案缺失 / 字段缺失 → 该问判 `None`（不知道），⛔ 不按「不通过」算。
   「不知道」和「不行」必须能分开——否则次新股永远被判死。

## ⚠️ 为什么不用 `history_relay_prob` 当胜率

实测（10-29 候选 11 只）：该字段的 159/957 个样本是**全市场同高度**统计，不是个股的。
个股自身样本常为 **0~3 次**，权重只有 0.25~0.70。结果善水科技和津药药业
prob 同为 0.37，而两只票「个股样本 0 次」——这个数字与它们无关，只是
「2 板全市场晋级率 34% + 情绪 +3pp」。拿它筛票，筛出来的是**历史上偶然晋级过一次的票**。
故胜率改用 `promo_rate_hist`（个股自身历史晋级率，见 `core/zt_gene.py`）。
"""
from __future__ import annotations

from typing import Any

from .zt_gene import load_hist_gene

SCHEMA = "promotion_quiz_v1"

# ── 阈值（⛔ 改这里等于改推送口径，必须同步改测试）────────────────────────────
WIN_RATE_MIN = 40.0    # 与 zt_gene.GENE_HABITUAL_MIN 同值：不新造第二套「强」标准
WIN_SAMPLE_MIN = 8     # 近一年涨停次数下限：样本不足的高晋级率不可信
PROMOTE_MAX_BOARDS = 2  # 只做到 1~2 板（用户纪律：第 1~2 板发现，4 板是另一笔交易）
STATUS_MIN_THEME = 3   # 题材至少 3 家：只有自己 1 家 = 孤票，不算板块地位

_LEADER_WORDS = ("龙头",)   # 含「龙头」即算前排身位（总龙头/准龙头/龙一）
_FOLLOW_WORDS = ("跟风", "补涨", "中军")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _gene(code: Any) -> dict:
    """读股性档案。测试注入走 `gene=` 参数，生产走 cache。"""
    return load_hist_gene(code)


def quiz(cand: dict, gene: dict | None = None) -> dict:
    """单只候选的三问判定（纯函数，不落盘不改入参）。"""
    code = str(cand.get("code") or "")
    g = gene if gene is not None else _gene(code)

    # ── ① 高胜率：个股自身历史晋级率 + 样本门槛 ─────────────────────────────
    pr = _num(g.get("promo_rate_hist"))
    zt = _num(g.get("zt_250"))
    if pr is None or zt is None:
        win = {"ok": None, "why": "无股性档案"}
    elif pr < WIN_RATE_MIN:
        win = {"ok": False, "why": f"晋级率{pr:.0f}%＜{WIN_RATE_MIN:.0f}%",
               "value": pr, "samples": int(zt)}
    elif zt < WIN_SAMPLE_MIN:
        # ⚠️ 这是最容易被骗的一格：4 次涨停蒙对 3 次 = 75%，但样本撑不起
        win = {"ok": False, "why": f"晋级率{pr:.0f}%但近一年仅{int(zt)}次涨停·样本不足",
               "value": pr, "samples": int(zt)}
    else:
        win = {"ok": True, "why": f"晋级率{pr:.0f}%·近一年{int(zt)}次涨停",
               "value": pr, "samples": int(zt)}

    # ── ② 可晋级：板数在空间内 + 历史证明走过更高 ────────────────────────────
    boards = _num(cand.get("boards"))
    mx = _num(g.get("max_boards_hist"))
    if boards is None:
        promote = {"ok": None, "why": "缺连板数"}
    elif boards > PROMOTE_MAX_BOARDS:
        promote = {"ok": False, "why": f"{int(boards)}板·不在1~2板发现窗口",
                   "boards": int(boards), "max_hist": None if mx is None else int(mx)}
    elif mx is None:
        promote = {"ok": None, "why": "无历史最高板"}
    elif mx <= boards:
        promote = {"ok": False, "why": f"历史最高{int(mx)}板·没走过比现在更高",
                   "boards": int(boards), "max_hist": int(mx)}
    else:
        promote = {"ok": True, "why": f"{int(boards)}板·历史最长{int(mx)}板",
                   "boards": int(boards), "max_hist": int(mx)}

    # ── ③ 板块地位：前排身位 + 题材有梯队（不是孤票）────────────────────────
    role = str(cand.get("leader_role") or "").strip()
    mates = _num(cand.get("theme_mates_cnt"))
    if mates is None:
        mates = _num(cand.get("sector_mates_cnt"))
    is_leader = any(w in role for w in _LEADER_WORDS)
    if not role:
        status = {"ok": None, "why": "无身位数据"}
    elif any(w in role for w in _FOLLOW_WORDS):
        status = {"ok": False, "why": f"身位={role}·不是前排", "role": role,
                  "theme_mates": None if mates is None else int(mates)}
    elif not is_leader:
        status = {"ok": None, "why": f"身位={role}·认不出前后排", "role": role,
                  "theme_mates": None if mates is None else int(mates)}
    elif mates is None:
        status = {"ok": None, "why": "前排身位·但不知题材家数", "role": role}
    elif mates < STATUS_MIN_THEME:
        # ⚠️ 津药药业实证：准龙头第 3、晋级 42%、历史最长 7 板，但题材只有自己 1 家
        status = {"ok": False, "why": f"{role}但题材仅{int(mates)}家·孤票无板块效应",
                  "role": role, "theme_mates": int(mates)}
    else:
        status = {"ok": True, "why": f"{role}·题材{int(mates)}家",
                  "role": role, "theme_mates": int(mates)}

    items = {"win_rate": win, "promote": promote, "status": status}
    passed = sum(1 for v in items.values() if v.get("ok") is True)
    unknown = [k for k, v in items.items() if v.get("ok") is None]
    # ⛔ 有「不知道」就不给档位称号。否则「1 条通过 + 2 条没数据」会被写成「一高」，
    #    读者会读成「三条里过了一条」——那是个确定的比例，而我们其实什么都不知道。
    if passed == len(items):
        label = "三高"
    elif unknown:
        label = ""
    elif passed == 2:
        label = "两高"
    elif passed == 1:
        label = "一高"
    else:
        label = ""
    return {
        "schema": SCHEMA,
        "passed": passed,
        "label": label,
        "items": items,
        "unknown": unknown,
        "blocked_by": [k for k, v in items.items() if v.get("ok") is False],
        "note": "三问只作判定，⛔ 不改评分/顺序/名额/仓位",
    }


def annotate(candidates: list[dict], gene_map: dict[str, dict] | None = None) -> list[dict]:
    """给每只候选挂 `promotion_quiz` —— 原地写入、**保持原顺序**、不动其他字段。"""
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        try:
            g = None
            if gene_map:
                g = gene_map.get(str(c.get("code") or ""))
            c["promotion_quiz"] = quiz(c, g)
        except Exception as e:  # noqa: BLE001
            # 判定失败绝不打断主链
            c["promotion_quiz"] = {"schema": SCHEMA, "passed": 0, "label": "",
                                   "error": repr(e)}
    return candidates


def summary_line(rows: list[dict]) -> str:
    """曲线标题用的一句话（给卡面顶部汇总，不是逐票灰字）。无可判定的票 → ""。"""
    judged = [r for r in rows or [] if isinstance(r, dict) and r.get("promotion_quiz")]
    if not judged:
        return ""
    full = [r for r in judged if (r["promotion_quiz"].get("passed") or 0) == 3]
    if full:
        names = "、".join(str(r.get("name") or "") for r in full[:3])
        more = f" 等{len(full)}只" if len(full) > 3 else ""
        return f"🎯 三问全过 {len(full)} 只：{names}{more}"
    two = [r for r in judged if (r["promotion_quiz"].get("passed") or 0) == 2]
    if two:
        names = "、".join(str(r.get("name") or "") for r in two[:3])
        return f"🎯 三问全过 0 只（最好的两只卡在一条上）：{names}"
    return "🎯 三问全过 0 只：今天没有高胜率+可晋级+板块地位都占的票"
