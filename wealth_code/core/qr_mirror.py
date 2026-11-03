#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quant-review 打分镜像 · 纯旁路（2026-10-30 用户「吸收它的算法，不要影响我们的购买策略」）

## 这是在做什么

把 `quant-review`（github.com/zxl7/quant-review）的**选股打分算法**搬到我们这边，
但**只做旁路对照**：算出来的分只写进候选的展示字段 `qr_mirror`，
⛔ 不排序、⛔ 不改 `raw_score`、⛔ 不改名额、⛔ 不改仓位、⛔ 不进 `_compute_ready`。

一句话：**它是另一把尺子，不是新的裁决者。**

## 吸收的算法（逐条对齐 quant-review 原实现）

| 维度 | 原函数 | 满分 | 我们的字段 |
|---|---|---|---|
| 主线贴合 | `calc_mainline_fit(conf, lbc)` | 20 | 题材置信度（缺→按 0，不编）＋ `boards` |
| 梯队位置 | `calc_tier_position(lbc, leaders, cje)` | 20 | `boards` ＋ `cje_yi` |
| 量能量级 | `calc_volume_score(cje)` | 15 | `cje_yi` |
| 封板质量 | `calc_seal_quality(leaders, seal)` | 15 | `seal` ＋ `流通市值` ＋ `fbt`（代理，见下） |
| 板块强度 | `calc_sector_resonance(zt_count)` | 10 | `sector` 当日涨停家数 |
| 换手健康 | `calc_turnover_health(turnover)` | 10 | `hs` |
| 接力动力 | `calc_relay_power_score(...)` | 0-100 | 位阶/承接/量能/风险扣分 |

分档沿用原口径：≥80 强候选 / ≥70 重点候选 / ≥60 条件触发 / 其余 只观察。

## 三处必须改造，不能照抄（否则和用户纪律打架）

1. **低成交额惩罚** —— 原实现 `calc_volume_score`: <3 亿 = 0 分、
   `calc_relay_power_score`: <5 亿 −3 分。但用户 2026-10-22 明确：
   「前期成交额不高很正常，因为资金筹码没什么抛压，成交额很低但能封板」
   → **低位缩量能封板＝筹码锁定，不是流动性硬伤**。本镜像保留原分（便于对照），
   但把这条**显式标成冲突**、并在 `effective_total` 里给出「按我们口径」的分数。
2. **688 加分** —— 原 `calc_bonus` 对 `300/688` 一律 +3。用户无科创板权限，
   → 688/北交所直接 `applicable=False`（不出分），300 的 +3 也标冲突
   （用户 2026-08-13：「主板创业板按同一套对称参与，无板块属性加分」）。
3. **leaders_score 我们没有** —— 原实现用它算封板质量。我们只有 `seal`（封单亿）、
   `lt_yi`（流通市值）、`fbt`（首封时间）→ 用「封单/流通占比 + 首封时刻」做代理，
   并在 `sources.seal_quality = "proxy"` 明确标注。⛔ 绝不用别的东西冒充原字段。

## 边界
- 纯函数：无网络、无落盘、无全局状态；输入缺失 → 该维返回 `None`（不是 0）。
- 开关 `ENABLE_QR_MIRROR=0` → `annotate()` 原样返回，不写任何字段。
"""
from __future__ import annotations

import os

# ── 可逆开关（默认开：旁路只写展示字段，开着的风险=0，关掉=完全不介入）──────────
# 2026-10-30 用户「不要灰色小字，也不要这个表」→ 本模块**只写 JSON，不上任何卡面**。
# 六维/接力动力分留作离线核对与将来选股用的原料，⛔ 卡面零渲染。
ENABLE_QR_MIRROR = os.environ.get("ENABLE_QR_MIRROR", "1") != "0"

SCHEMA = "qr_mirror_v1"

# ── 分档阈值（与 quant-review 原口径一致，改这里只影响展示标签）────────────────
BAND_STRONG = 80
BAND_FOCUS = 70
BAND_COND = 60

# ── 换手舒适区（原 calc_turnover_health）────────────────────────────────────
TURN_SWEET_LO, TURN_SWEET_HI = 3.0, 15.0
TURN_HI = 30.0

# 无权限板块（用户无科创板/北交所权限 → 不出分，避免"镜像推荐了一只买不到的票"）
def _no_permission(code: str) -> bool:
    c = str(code or "")
    return c.startswith(("688", "8", "4")) or c.startswith("920")


# ─────────────────────────────────────────────────────────────────────────────
# 六维（逐条对齐原实现）
# ─────────────────────────────────────────────────────────────────────────────
def _mainline_fit(conf: float | None, boards: int | None) -> int | None:
    """主线贴合度 0-20（原 calc_mainline_fit）。conf 缺失 → None（不按 0 算）。"""
    if conf is None:
        return None
    b = int(boards or 0)
    if conf >= 0.6 and b >= 2:
        return 20
    if conf >= 0.6:
        return 16
    if conf >= 0.4 and b >= 2:
        return 14
    if conf >= 0.4:
        return 10
    if conf > 0:
        return 6
    return 0


def _tier_position(boards: int | None, cje_yi: float | None) -> int | None:
    """梯队位置 0-20（原 calc_tier_position，leaders_score 段已剥离）。"""
    if boards is None:
        return None
    b = int(boards)
    cje = float(cje_yi or 0.0)
    if b >= 3:
        return 17
    if b == 2:
        return 14
    if b == 1 and cje >= 100:
        return 16
    if b == 1 and cje >= 30:
        return 10
    return 6


def _volume_score(cje_yi: float | None) -> int | None:
    """量能量级 0-15（原 calc_volume_score·⚠️ 低成交额口径与用户纪律冲突，见 CONFLICTS）。"""
    if cje_yi is None:
        return None
    v = float(cje_yi)
    if v >= 100:
        return 15
    if v >= 50:
        return 12
    if v >= 30:
        return 9
    if v >= 10:
        return 5
    if v >= 3:
        return 2
    return 0


def _seal_quality(seal_yi: float | None, lt_yi: float | None, fbt: str | None) -> int | None:
    """封板质量 0-15（原 calc_seal_quality 的 **代理实现**）。

    原实现依赖 `leaders_score`（quant-review 内部的龙头/资金分），我们没有该字段。
    改用两个我们真有的、且方向一致的量：**封单/流通占比** ＋ **首封时刻**。
    ⛔ 这是 proxy，不是原字段；`sources.seal_quality="proxy"` 已标注。
    """
    if seal_yi is None and fbt is None:
        return None
    ratio = None
    if seal_yi is not None and lt_yi:
        try:
            if float(lt_yi) > 0:
                ratio = float(seal_yi) / float(lt_yi) * 100.0
        except (TypeError, ValueError):
            ratio = None
    early = str(fbt or "") <= "100000" and bool(str(fbt or ""))
    if ratio is not None:
        if ratio >= 6.0 and early:
            return 15
        if ratio >= 3.0 and early:
            return 11
        if ratio >= 3.0:
            return 8
        if ratio >= 1.0:
            return 4
    # 无流通市值时退化为封单绝对额（原实现对 seal_fund_yi ≥3 给 6 分）
    if seal_yi is not None and float(seal_yi) >= 3:
        return 6
    return 0


def _sector_resonance(zt_count: int | None) -> int | None:
    """板块强度 0-10（原 calc_sector_resonance）。"""
    if zt_count is None:
        return None
    n = int(zt_count)
    if n >= 8:
        return 10
    if n >= 5:
        return 7
    if n >= 3:
        return 4
    return 1


def _turnover_health(turnover: float | None) -> int | None:
    """换手健康度 0-10（原 calc_turnover_health）。"""
    if turnover is None:
        return None
    t = float(turnover)
    if TURN_SWEET_LO <= t <= TURN_SWEET_HI:
        return 10
    if TURN_SWEET_HI < t <= TURN_HI:
        return 6
    if 0 < t < TURN_SWEET_LO:
        return 3
    if t > TURN_HI:
        return 2
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# 接力动力分（原 calc_relay_power_score·100 分制）
# ─────────────────────────────────────────────────────────────────────────────
def relay_power(boards: int | None, cje_yi: float | None, seal_yi: float | None,
                turnover: float | None, zbc: int | None,
                sector_zt_count: int | None, theme_conf: float | None) -> dict:
    """接力动力分。原式的 `zt_*_factor` / `break_risk` 我们没有 → 跳过并记 missing。

    ⛔ 跳过 ≠ 记 0：缺失因子不进分母、也不扣分，只写进 `missing` 供读者判断可信度。
    """
    missing: list[str] = []
    if boards is None:
        missing.append("boards")
    if cje_yi is None:
        missing.append("cje_yi")
    if turnover is None:
        missing.append("hs")
    # 原式里我们没有的四项因子（非本系统字段，⛔ 不编造等价物）
    missing += ["zt_relay_factor", "zt_leader_factor", "zt_capacity_factor", "zt_break_risk"]

    score = 24.0

    # 1. 位阶
    if boards is not None:
        b = int(boards)
        if b >= 3:
            score += 16
        elif b == 2:
            score += 13
        elif b == 1:
            score += 5

    # 2. 承接与换手
    if turnover is not None:
        t = float(turnover)
        if 3 <= t <= 30:
            score += min(max(t, 0.0), 30.0) * 0.35
        elif 0 < t < 3:
            score += 1
        else:
            score -= 5
    if seal_yi is not None:
        s = float(seal_yi)
        if s >= 3:
            score += 8
        elif s >= 1:
            score += 5
        elif s >= 0.3:
            score += 2

    # 3. 量能舒适区（⚠️ 8~40 亿加分、<5 亿扣分 → 与用户「缩量封板=筹码锁定」冲突）
    low_amount_penalty = 0.0
    if cje_yi is not None:
        c = float(cje_yi)
        if 8 <= c <= 40:
            score += 6
        elif 40 < c <= 100:
            score += 4
        elif c > 100:
            score += 1
        elif c < 5:
            score -= 3
            low_amount_penalty = -3.0

    # 4. 主线加成
    if theme_conf is not None and float(theme_conf) >= 0.6:
        score += 3

    # 5. 风险扣分（原式的炸板次数段）
    if zbc is not None:
        z = int(zbc)
        if z >= 5:
            score -= 10
        elif z >= 3:
            score -= 6
        elif z >= 1:
            score -= 2
    if turnover is not None and float(turnover) > 35:
        score -= 6

    raw = max(0, min(100, round(score)))
    # 按我们口径重算：撤掉「低成交额扣分」这一条（用户纪律：缩量封板是筹码锁定）
    ours = max(0, min(100, round(score - low_amount_penalty)))
    return {
        "score": raw,
        "score_ours": ours,
        "low_amount_penalty": low_amount_penalty,
        "missing": missing,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 冲突登记（吸收时最该看清的部分）
# ─────────────────────────────────────────────────────────────────────────────
CONFLICTS: list[dict] = [
    {
        "id": "low_amount_penalty",
        "rule": "原实现：成交额 <3 亿 量能给 0 分、<5 亿 接力动力 −3 分",
        "ours": "用户 2026-10-22「前期成交额不高很正常，筹码没抛压，成交额很低但能封板」= 缩量封板是筹码锁定，不是流动性硬伤",
        "handling": "镜像照原样算（便于对照），另出 score_ours 剔除该扣分；⛔ 两者都不参与我们的排序",
    },
    {
        "id": "board_bonus_20cm",
        "rule": "原实现：代码 300/688 一律 +3 分（20cm 溢价）",
        "ours": "用户 2026-08-13「主板创业板按同一套对称参与」，无板块属性加分/减分；科创板 688 无权限",
        "handling": "688/北交所 applicable=False 不出分；300 的 +3 不采纳（镜像分只作对照）",
    },
    {
        "id": "leaders_score_proxy",
        "rule": "原实现的封板质量依赖内部 `leaders_score`（龙头/资金综合分）",
        "ours": "本系统无该字段",
        "handling": "改用 封单/流通占比 + 首封时刻 做代理，字段标 source=proxy；⛔ 不冒充原字段",
    },
]


# 候选字段别名（⛔ 跨命名体系必须逐个别名列出，禁止"看起来像就用"）
#   涨停池 `zt_list` 用 seal / lt_yi；盘后候选 candidates-*.json 用 seal_amt / lt_mktcap。
#   同一份镜像要能吃两种来源，否则接进主链后字段全取不到、静默降级成"无数据"。
_ALIAS = {
    "boards": ("boards", "lbc", "board_count"),
    "cje_yi": ("cje_yi", "amount_yi", "turnover_amount_yi"),
    "seal": ("seal", "seal_amt"),
    "lt": ("lt_yi", "lt_mktcap", "lt_mktcap_yi"),
    "hs": ("hs", "turnover", "turnover_rate"),
    "fbt": ("fbt", "first_board_time"),
    "zbc": ("zbc", "broken_times"),
    "sector": ("sector", "industry_sector", "theme_name"),
    # 「同板块涨停家数」的三条来源，按口径优先级排（2026-10-30 修）：
    #   ① `theme_pool_zt_count` 盘后精选池自带（最贴原意）
    #   ② `theme_mates_cnt` **同题材**涨停家数 ← 用户口径是题材优先、行业兜底
    #   ③ `sector_mates_cnt` **申万行业**同门家数（最粗，只在①②都缺时用）
    # ⛔ 原来只写了 ①② 之外还把 ③ 放在第 2 位 —— 中新赛克 AI算力题材 10 家，
    #    却按行业「计算机设」1 家算成 sector_resonance=1 分，这是「看起来像就用」的典型错。
    "pool_zt": ("theme_pool_zt_count", "theme_mates_cnt", "sector_mates_cnt"),
}


def _pick_with_key(cand: dict, key: str) -> tuple:
    """同 `_pick`，但连**取自哪个字段**一起返回（sources 要如实标注）。"""
    for name in _ALIAS.get(key, (key,)):
        v = cand.get(name)
        if v is None or v == "":
            continue
        return v, name
    return None, None


def _pick(cand: dict, key: str):
    """按别名取第一个非空值；全空 → None（⛔ 不填 0）。"""
    for name in _ALIAS.get(key, (key,)):
        v = cand.get(name)
        if v is None or v == "":
            continue
        return v
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 对外：单只 / 批量
# ─────────────────────────────────────────────────────────────────────────────
def _theme_conf_from(cand: dict) -> tuple[float | None, str]:
    """把我们的题材字段映射成「主线置信度」（原算法要的 `in_ml_top_conf`）。

    ⛔ 这不是原字段的等价物，是我们口径的代理，返回的 source 必须跟着上卡。

    **两个入口，按优先级取**（2026-10-30 修：原来只认第一个，而普通候选里根本没有它
    ——`line_tier_outpool` 是「池外高辨识」那套 P1 才打的标，relay/watch 行永远是 None
    → 全池 11/11 缺主线维 → 六维永远凑不齐 6/6 → 分档永远出不来，等于白接）：

      入口① `line_tier_outpool`（前沿口径，有就用）
        front + H → 0.7 ｜ front + M → 0.5 ｜ front → 0.4 ｜ second → 0.3
      入口② `leader_role`（我们既有的龙头身份口径，普通候选都有）
        含「龙头」（总龙头/准龙头/龙一）+ H → 0.7 ｜ + M → 0.5 ｜ 无热度 → 0.4
        「跟风/补涨/中军」 → 0.3
      两者都没有 → None（⛔ 不猜，宁可缺维）
    """
    tier = str(cand.get("line_tier_outpool") or "").strip().lower()
    heat = str(cand.get("core_theme_heat") or "").strip().upper()
    if tier in {"front", "second"}:
        if tier == "front":
            if heat == "H":
                return 0.7, "ours(line_tier=front+heat=H)"
            if heat == "M":
                return 0.5, "ours(line_tier=front+heat=M)"
            return 0.4, "ours(line_tier=front)"
        return 0.3, "ours(line_tier=second)"
    role = str(cand.get("leader_role") or "").strip()
    if role:
        if "龙头" in role:
            if heat == "H":
                return 0.7, f"ours(leader_role={role}+heat=H)"
            if heat == "M":
                return 0.5, f"ours(leader_role={role}+heat=M)"
            return 0.4, f"ours(leader_role={role})"
        if role in {"跟风", "补涨", "中军"}:
            return 0.3, f"ours(leader_role={role})"
    return None, "missing"


def mirror_one(cand: dict, sector_zt_count: int | None = None,
               theme_conf: float | None = None) -> dict:
    """单只候选的镜像评分（纯函数）。`cand` 用我们 zt 池或盘后候选的字段名。"""
    code = str(cand.get("code") or "")
    if _no_permission(code):
        return {
            "schema": SCHEMA, "applicable": False,
            "reason": "无科创板/北交所权限（用户纪律）",
        }
    boards = _pick(cand, "boards")
    cje = _pick(cand, "cje_yi")
    seal = _pick(cand, "seal")
    lt = _pick(cand, "lt")
    fbt = _pick(cand, "fbt")
    hs = _pick(cand, "hs")
    zbc = _pick(cand, "zbc")
    # 板块强度：候选自带的「同题材涨停家数」优先（我们的既有口径：题材 > 行业），
    # 其次用调用方给的「同板块涨停家数」，都没有 → None（不编）。
    # 同时记住取自哪个字段，sources 要如实写出来（⛔ 不能笼统写「同板块涨停家数」）。
    pool_zt, pool_zt_key = _pick_with_key(cand, "pool_zt")
    if pool_zt is not None:
        try:
            sector_zt_count = int(pool_zt)
        except (TypeError, ValueError):
            pass

    # 主线置信度：调用方显式给了就用；没给则按我们的题材字段映射（并标注来源）
    conf_src = "caller"
    if theme_conf is None:
        theme_conf, conf_src = _theme_conf_from(cand)

    parts = {
        "mainline_fit": _mainline_fit(theme_conf, boards),
        "tier_position": _tier_position(boards, cje),
        "volume": _volume_score(cje),
        "seal_quality": _seal_quality(seal, lt, fbt),
        "sector_resonance": _sector_resonance(sector_zt_count),
        "turnover_health": _turnover_health(hs),
    }
    known = {k: v for k, v in parts.items() if v is not None}
    total = sum(known.values()) if known else None
    known_full = sum(_FULL_MARKS[k] for k in known)
    coverage = f"{len(known)}/{len(_FULL_MARKS)}"
    # ⛔ 只有**六维齐全**才折算成 0-100 并给分档。
    #   缺维折算会系统性压低分数（实测 10-29 全池 39 只全落「只观察」——
    #   因为缺主线维 20 分 + 2 板拿不到先锋龙 20 分），那是假结论，不是真排序。
    #   缺维时只报「已知维实得分 / 已知维满分」，让读者知道这分数覆盖了多少。
    scaled = round(total / known_full * 100) if (total is not None and len(known) == len(_FULL_MARKS)) else None

    rp = relay_power(boards, cje, seal, hs, zbc, sector_zt_count, theme_conf)

    if scaled is None:
        band = None
    elif scaled >= BAND_STRONG:
        band = "强候选"
    elif scaled >= BAND_FOCUS:
        band = "重点候选"
    elif scaled >= BAND_COND:
        band = "条件触发"
    else:
        band = "只观察"

    hits = []
    if rp["low_amount_penalty"]:
        hits.append("low_amount_penalty")
    if cand.get("is_20cm") or code.startswith("300"):
        hits.append("board_bonus_20cm")

    return {
        "schema": SCHEMA,
        "applicable": True,
        "code": code,
        "name": cand.get("name"),
        "six_dim": {
            "total": total,
            "known_full": known_full,
            "coverage": coverage,          # "6/6" 才给分档；否则只报实得/满分
            "total_scaled": scaled,
            "parts": parts,
            "missing": [k for k, v in parts.items() if v is None],
        },
        "relay_power": rp,
        "band": band,
        "sources": {
            "seal_quality": "proxy（封单/流通＋首封时刻，非原 leaders_score）",
            "mainline_fit": conf_src,
            # 如实写取自哪个字段：题材同门 ≠ 行业同门，两者数值差好几倍
            "sector_zt_count": (f"ours({pool_zt_key})" if pool_zt_key
                                else "ours(调用方传入)"),
        },
        "conflicts": [c for c in CONFLICTS if c["id"] in hits],
        "note": "旁路镜像：分档只作对照，⛔ 不参与本系统排序/名额/仓位",
    }


# 缺维时按已知维满分折算到 100，避免"缺数据"被读成"分数低"
_FULL_MARKS = {"mainline_fit": 20, "tier_position": 20, "volume": 15,
               "seal_quality": 15, "sector_resonance": 10, "turnover_health": 10}
_DIM_LABEL = {"mainline_fit": "主线", "tier_position": "梯队", "volume": "量能",
              "seal_quality": "封板", "sector_resonance": "板块", "turnover_health": "换手"}


def _sector_index(zt_list: list[dict] | None) -> tuple[dict[str, int], dict[str, str]]:
    """当日各板块涨停家数 ＋ code→板块 反查表（我们的 `sector` 字段口径）。

    候选里常没有 `sector`（relay/watch 行未必带），所以用涨停池反查补——
    ⛔ 不猜、不按名称相似匹配，查不到就是查不到（该维返回 None）。
    """
    counts: dict[str, int] = {}
    code2sector: dict[str, str] = {}
    for z in zt_list or []:
        if not isinstance(z, dict):
            continue
        s = str(z.get("sector") or "").strip()
        if not s:
            continue
        counts[s] = counts.get(s, 0) + 1
        c = str(z.get("code") or "").strip()
        if c:
            code2sector[c] = s
    return counts, code2sector


def annotate(candidates: list[dict], zt_list: list[dict] | None = None,
             theme_conf_map: dict[str, float] | None = None) -> list[dict]:
    """给每只候选挂 `qr_mirror` 字段 —— **原地写入、保持原顺序、不动任何其他字段**。

    ⛔ 三条硬边界：
      1. 不排序：返回列表与入参顺序/身份完全一致（调用方无需换序）。
      2. 不改分：只新增 `qr_mirror` 键，`raw_score`/`score` 等一概不碰。
      3. 不改名额/仓位：本模块根本不产出这两类字段。
    """
    if not ENABLE_QR_MIRROR:
        return candidates
    counts, code2sector = _sector_index(zt_list)
    for c in candidates:
        if not isinstance(c, dict):
            continue
        try:
            code = str(c.get("code") or "")
            sector = str(c.get("sector") or "").strip() or code2sector.get(code, "")
            conf = None
            if theme_conf_map:
                conf = theme_conf_map.get(code)
            c["qr_mirror"] = mirror_one(c, counts.get(sector) if sector else None, conf)
        except Exception as e:  # noqa: BLE001
            # 镜像失败绝不影响主链：降级为「不可用」，不抛给调用方
            c["qr_mirror"] = {"schema": SCHEMA, "applicable": False, "error": repr(e)}
    return candidates
