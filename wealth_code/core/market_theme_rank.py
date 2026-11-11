"""🧭 市场级题材强度聚合（2026-11-11 用户拍板 B）。

用户 11-10 晚原话：「我们要在最强的板块，或者说前三强的板块里面，去寻找能接力的个股。
然后根据它们的涨停时间、涨幅，还有带领性这些因子……再来推谁是龙头，我们再来接力谁。
这一套逻辑我现在没有感觉到，你看到了昨天的强度题材，但没有看到你对昨天整个市场强题材的思考。」

本模块只回答**盘后第一问**：昨天整个市场，哪些线真强、强在哪；并把
「板块强度 → 板块内票的强度」变成一条可解释的**展示层排序** `theme_order`。

⛔ 铁律（2026-11-11 落地时修正，别踩）：
  · `relay_rank` / `dragon_rank` 是 09:25 **判据字段**：`pool_call.py:900` 用
    `relay_rank == 1` 裁决、`expectation_ai.py:128` 用 `relay_rank <= 2` 定期望、
    `pool_admit` 龙一闸读 `dragon_rank`。**本模块一个都不改**。
  · 不改 `relay` 数组顺序（09:25 遍历顺序 / quota 分配敏感）。
  · 不进任何准入闸，不改 DEV / 量能带 / 顶部空间 / quota / 仓位 P0。
  · 只用当日收盘可得的事前信息（涨停池 fbt / 封单 / 流通 / 板数），无未来函数。
  · 开关 `POOL_CALL_MARKET_THEME_RANK=0` 完全回退：不算、不写字段、卡面不显示。

「强」的口径 = 用户 11-10 拍板：①有催化/有逻辑（系统无可靠数据 ⇒ **缺数据不裁决**，
只留题材名）；②高度与梯队（最高板、连板≥2 家数）；③承接质量（最早封板、
封单/流通、带动家数）。**家数只做弱辅助，不做主序** —— 与用户 7-27
「广度≠核心：板块涨停数量多不代表核心主线」一致。

⛔ 已证伪背景：`core/theme_score.py` 四维等权打分分档不单调、方向相反；
   唯一单调的家数（n≥6 才 +0.69%）明确「不进判据」。本模块**不是**它的翻版：
   不产出进权重的总分，只做可解释的结构复盘 + 展示排序，分数仅供排序与留痕。
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

SCHEMA = "market_theme_rank_v1"
_BREADTH_MIN_CNT = 8          # 家数达到多少才判「铺广度」候选
_BREADTH_MAX_BOARDS = 2       # 且最高板不超过这个数
_EARLY_SEAL_MIN = 600         # 10:00 = 600 分钟
_CARRY_WINDOW_MIN = 30        # 与 core/sector_support.annotate_carry 同口径


def enabled() -> bool:
    """总开关；`POOL_CALL_MARKET_THEME_RANK=0` 完全回退到 11-10 旧行为。"""
    return str(os.environ.get("POOL_CALL_MARKET_THEME_RANK", "1")).strip() not in {
        "0", "false", "off", ""
    }


# ── 字段兼容：ztgc 用 dm/mc/lbc，zt_history 用 code/name/boards ──────────────
def _code(r: dict[str, Any]) -> str:
    return str(r.get("code") or r.get("dm") or "").strip()


def _boards(r: dict[str, Any]) -> int:
    for k in ("boards", "lbc", "continuous_boards", "lianban"):
        v = r.get(k)
        if v not in (None, ""):
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return 1


def _fbt_min(r: dict[str, Any]) -> int | None:
    """封板时间 → 当日分钟数。缺/非法返回 None（诚实不编造）。"""
    raw = r.get("fbt") or r.get("first_seal_time") or r.get("fbt_time") or ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) < 4:
        return None
    try:
        return int(digits[:2]) * 60 + int(digits[2:4])
    except (TypeError, ValueError):
        return None


def _seal_ratio(r: dict[str, Any]) -> float | None:
    """封单/流通市值（%）。单位与 core/sector_leader 一致，缺数据返回 None。"""
    v = r.get("seal_ratio_pct")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), 2)
    seal = r.get("seal") or r.get("seal_amt") or r.get("seal_amount")
    lt = r.get("lt_yi") or r.get("lt") or r.get("ltsz_yi") or r.get("float_mv_yi")
    try:
        if float(lt) <= 0:
            return None
        return round(100.0 * float(seal) / float(lt), 2)
    except (TypeError, ValueError):
        return None


def _amount(r: dict[str, Any]) -> float:
    for k in ("cje_yi", "amount_yi", "cje", "amount"):
        v = r.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _theme_of(r: dict[str, Any], theme_map: dict[str, Any] | None) -> str:
    """题材单一源 = 当日冻结的 theme_map；缺失时回落行内 core_theme/theme。

    ⚠️ 不用 `sector`（东财行业）兜底 —— 用户已明确「行业≠题材」。缺就归到
       「题材缺失」并**不参与强度排序**（缺数据不裁决）。
    """
    c = _code(r)
    v = (theme_map or {}).get(c) if isinstance(theme_map, dict) else None
    if isinstance(v, dict):
        t = v.get("theme") or v.get("core_theme")
    elif isinstance(v, (list, tuple)):
        t = v[0] if v else None
    else:
        t = v
    t = str(t or r.get("core_theme") or r.get("theme") or "").strip()
    return t if t and t not in {"—", "-", "其他", "无"} else "题材缺失"


def _hhmm(m: int | None) -> str:
    return f"{m // 60:02d}:{m % 60:02d}" if m is not None else "—"


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[len(s) // 2], 2)


def rank(zt_rows: list[dict[str, Any]] | None,
         theme_map: dict[str, Any] | None = None) -> dict[str, Any]:
    """把**完整同日涨停池**聚合成市场级题材强度表。

    ⛔ 必须传完整池（含 688/ST/一字/低成交额全部涨停股），不能传过滤后的候选残池 ——
       否则「全市场最强线」会退化成「池内最强线」，正是用户指出的问题。

    返回 `by_code`：每只票的板块名次与档内因子，供 `attach_row_metrics` /
    `order_rows` 复用，避免一处算聚合、一处重算个股。
    """
    rows: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_code: dict[str, dict[str, Any]] = {}

    for raw in (zt_rows or []):
        if not isinstance(raw, dict):
            continue
        r = dict(raw)                       # 不修改调用方输入
        c = _code(r)
        if not c:
            continue
        t = _theme_of(r, theme_map)
        b = _boards(r)
        f = _fbt_min(r)
        sp = _seal_ratio(r)
        r.update(_code=c, _theme=t, _boards=b, _fbt=f, _seal=sp, _amount=_amount(r),
                 _carry=0)
        rows.append(r)
        by_code[c] = {
            "theme": t, "boards": b, "fbt_min": f, "fbt": r.get("fbt"),
            "seal_ratio_pct": sp, "amount_yi": r["_amount"],
        }
        if t != "题材缺失":
            groups[t].append(r)

    # 带动性：封板后 30min 内同题材新增涨停家数（与 annotate_carry 同口径的时序因果）
    for _t, arr in groups.items():
        for r in arr:
            mine = r["_fbt"]
            if mine is None:
                continue
            cnt = sum(1 for y in arr if y["_fbt"] is not None
                      and mine < y["_fbt"] <= mine + _CARRY_WINDOW_MIN)
            r["_carry"] = cnt
            by_code[r["_code"]]["carry_cnt"] = cnt

    themes: list[dict[str, Any]] = []
    for t, arr in groups.items():
        cnt = len(arr)
        bs = [r["_boards"] for r in arr]
        ge2 = sum(1 for b in bs if b >= 2)
        ge3 = sum(1 for b in bs if b >= 3)
        maxb = max(bs, default=0)
        fbts = [r["_fbt"] for r in arr if r["_fbt"] is not None]
        early = sum(1 for f in fbts if f <= _EARLY_SEAL_MIN)
        late = sum(1 for f in fbts if f >= 780)      # 13:00 后
        sp_med = _median([r["_seal"] for r in arr if r["_seal"] is not None])
        carry_total = sum(r["_carry"] for r in arr)
        early_ratio = round(early / cnt, 3) if cnt else 0.0

        # 「铺广度」＝家数多但没有高度、封一整天。用户 7-27 定的「量化铺广度·持续性弱」。
        breadth = bool(cnt >= _BREADTH_MIN_CNT and maxb <= _BREADTH_MAX_BOARDS
                       and ge3 == 0)

        # 排序分**不进任何判据**，只用于板块分层展示：高度/梯队为主，承接质量次之，
        # 家数是弱辅助（≤8 分），铺广度扣分。
        height = min(maxb, 8) * 12 + min(ge2, 10) * 4
        quality = (min(early_ratio * 30, 30)
                   + min((sp_med or 0.0) * 8, 20)
                   + min(carry_total / 2.0, 15))
        bonus = min(cnt * 0.5, 8)
        penalty = -25 if breadth else 0
        score = round(height + quality + bonus + penalty, 2)

        themes.append({
            "theme": t, "zt_count": cnt, "boards_ge2": ge2, "boards_ge3": ge3,
            "max_boards": maxb, "first_seal": _hhmm(min(fbts)) if fbts else "—",
            "early_seal_cnt": early, "late_seal_cnt": late,
            "early_ratio": early_ratio,
            "seal_ratio_med": sp_med, "carry_total": carry_total,
            "amount_yi": round(sum(r["_amount"] for r in arr), 1),
            "breadth_flag": breadth, "score": score,
        })

    themes.sort(key=lambda x: (-x["score"], -x["max_boards"], x["theme"]))

    def _brief(x: dict[str, Any]) -> dict[str, Any]:
        return {k: x[k] for k in ("theme", "zt_count", "max_boards", "boards_ge2",
                                  "first_seal", "seal_ratio_med", "carry_total",
                                  "breadth_flag", "score")}

    top3 = [_brief(x) for x in themes[:3]]
    breadth_top3 = [_brief(x) for x in
                    sorted(themes, key=lambda x: (-x["zt_count"], x["theme"]))[:3]]
    divergence = (top3[0]["theme"] != breadth_top3[0]["theme"]) if top3 else False

    for i, x in enumerate(themes, 1):
        for r in groups[x["theme"]]:
            by_code[r["_code"]].update(theme_rank=i, theme_breadth_flag=x["breadth_flag"])

    return {
        "schema": SCHEMA,
        "enabled": True,
        "zt_count": len(rows),
        "theme_count": len(groups),
        "caliber": "高度与梯队 + 承接质量；家数仅弱辅助（用户 2026-11-11 拍板·广度≠核心）",
        "themes": themes,
        "top3": top3,
        "breadth_top3": breadth_top3,
        "divergence": divergence,
        "by_code": by_code,
    }


def ticket_score(m: dict[str, Any]) -> float:
    """板块内「票的强度」分 —— 用户点名的四因子，全部当日收盘可得。

    ⛔ 只用于**展示排序**，不进任何判据 / 权重 / 收益预测（区别于已证伪的 theme_score）。
       · 连板高度（用户说的「涨幅」在涨停池里全是 10/20%，真正有区分度的是连板高度）
       · 封板时间（09:30 满分 30，越晚越低；13:00 约 9 分，14:30 归零）
       · 封单/流通（承接质量，最高 30）
       · 带动家数（封板后 30min 同题材新增涨停，最高 18）
    """
    boards = int(m.get("boards") or 0)
    seal = float(m.get("seal_ratio_pct") or 0.0)
    carry = int(m.get("carry_cnt") or 0)
    fbt = m.get("fbt_min")
    early = (0.0 if not isinstance(fbt, int)
             else max(0.0, 30.0 - max(0, fbt - 570) / 10.0))
    return round(min(boards, 8) * 8 + min(max(seal, 0.0), 10) * 3
                 + early + min(max(carry, 0), 6) * 3, 2)


def line_bonus(rank_no: int | None) -> float:
    """板块档加成：第 1 档 30 分，每降一档减 6，第 6 档起 0。

    ⛔ 刻意小于票强度分上限（~100）：强板块里的烂票不能整体压过次强板块的龙头。
       这是 11-11 回放抓到的硬伤（光伏永臻股份 13:09 封·炸4次曾被排到板块序 #2）。
    """
    i = rank_no if isinstance(rank_no, int) else 999
    return max(0.0, 30.0 - (i - 1) * 6.0)


def attach_to_payload(diagnostics: dict[str, Any], relay: list[dict[str, Any]],
                      watch: list[dict[str, Any]], zt_rows: list[dict[str, Any]],
                      theme_map: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """`pipeline_eod` 的**唯一入口**：算聚合 → 写 diagnostics → 给候选补展示因子。

    ⛔ 只写 diagnostics["market_theme_rank"] 与候选的 theme_rank/theme_metrics；
       不动 relay_rank / dragon_rank / 数组顺序 / 任何准入闸。
    ⛔ `POOL_CALL_MARKET_THEME_RANK=0` 时返回 None 且**一个字段都不写**。
    """
    if not enabled():
        return None
    ctx = rank(zt_rows, theme_map)
    diagnostics["market_theme_rank"] = ctx
    attach_row_metrics(list(relay or []) + list(watch or []), ctx)
    return ctx


def attach_row_metrics(rows: list[dict[str, Any]], ctx: dict[str, Any] | None) -> list[dict[str, Any]]:
    """给候选补**展示因子**（不覆盖 `dragon_rank` / `carry_cnt` 等判据字段）。

    原地写 `theme_rank` / `theme_breadth_flag` / `theme_metrics`，不改顺序、不改资格。
    低成交额放行票 / 补票路径没有 `dragon_rank` 时，因子从完整涨停池重算，
    保证「板块内谁更硬」这批因子**每只票都有**（11-10 实证 31/31 覆盖）。
    """
    if not enabled() or not ctx or not ctx.get("enabled"):
        return rows
    by_code = ctx.get("by_code") or {}
    for r in rows:
        c = str(r.get("code") or "")
        m = by_code.get(c)
        if not m:
            continue
        r["theme_rank"] = m.get("theme_rank")
        r["theme_breadth_flag"] = bool(m.get("theme_breadth_flag"))
        r["theme_metrics"] = {
            "boards": m.get("boards"),
            "fbt": m.get("fbt"),
            "seal_ratio_pct": m.get("seal_ratio_pct"),
            "carry_cnt": m.get("carry_cnt", 0),
            "theme_order_score": round(line_bonus(m.get("theme_rank")) + ticket_score(m), 2),
        }
    return rows


def order_rows(rows: list[dict[str, Any]], ctx: dict[str, Any] | None) -> list[dict[str, Any]]:
    """板块分层展示排序：板块档加成 + 票自身强度（高度/封板/封单/带动）混合。

    ⛔ 返回**新列表**（浅拷贝），不改传入数组顺序、不改 `relay_rank`。
       新字段 `theme_order` 是卡面编号，仅代表「昨日板块与票的强度顺序」，
       **不等于 09:25 的买入顺序** —— 买不买仍由竞价裁决（文案=行为）。
    """
    if not rows:
        return []
    if not enabled() or not ctx or not ctx.get("enabled"):
        return list(rows)
    by_code = ctx.get("by_code") or {}

    def _key(r: dict[str, Any]):
        m = by_code.get(str(r.get("code") or "")) or {}
        tr = m.get("theme_rank")
        tr_i = tr if isinstance(tr, int) else 999
        total = line_bonus(tr) + ticket_score(m)
        fbt = m.get("fbt_min")
        return (-round(total, 2), tr_i,
                fbt if isinstance(fbt, int) else 9999,
                str(r.get("code") or ""))

    out: list[dict[str, Any]] = []
    for i, r in enumerate(sorted(rows, key=_key), 1):
        n = dict(r)
        n["theme_order"] = i
        out.append(n)
    return out
