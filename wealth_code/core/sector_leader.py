#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板块龙一判定 · **单一源**（2026-10-27 用户「推的票必须是板块龙一」）。

## 用户原话与真问题

> 「推的票 必须是板块龙一」

系统里此前**同时存在两套龙一判定，且互相打架**（10-27 实盘证据）：

| 判定处 | 排序依据 | 当日结论 |
|---|---|---|
| `pipeline_eod._attach_sector_ladder` → `leader.build_sector_ladder` | (连板数, 涨幅, **成交额**) | 多伦科技=龙一（成交额 8.1 亿 > 赛福天 3.02 亿） |
| `pool_admit.strength_key` | (板数, **带动家数**, 封板早, 综合分) | 赛福天=最强（carry 8 > 多伦 7）→ 进接力池 |

结果：卡面推的赛福天 `is_sector_dragon=False`，而 `True` 的多伦在观察池 ——
**同一个系统对「谁是这条线的龙一」给了两个答案**。

## 判据（分层·可解释·不互相抵消）

组内排序键，**从强到弱依次比**：

1. **板数**（身位）—— 这条线走到最高/最靠前的那只
2. **封板时段档**（`LEADER_SEAL_WINDOW_MIN` 分钟内视为同档·以档内最早封板为锚）
   —— 早封＝主动走强；但**秒差不等于强弱**，故同档内不再比时间
3. **封单 / 流通 %**（厚者优先）—— 同档内比承接厚度（含金量，不用绝对"X 亿"）
4. **带动家数**（`carry_cnt`）—— 封板后同题材跟封数，反推它是否点火了这条线
5. **封板时间**（更早）→ **代码**（稳定排序，保证可复现）

### 为什么封板时间要按「档」而不是逐秒
10-27 机器人线三只 4 板：多伦 09:25:01 / 赛福天 09:25:02 / 襄阳 09:34:09。
多伦与赛福天差 **1 秒**，若逐秒比较则多伦恒胜 —— 而这 1 秒没有任何信息量
（同一批集合竞价秒板），真正该比的是两者封单厚度（0.24% vs 0.85%）。
按档处理后：赛福天（0.85%）> 多伦（0.24%）> 襄阳（晚一档）。

### ⛔ 缺数据不编造
- 封单比缺失 → 该维度**不参与比较**（排在有值者之后），并标 `seal_ratio_missing`
- 题材缺失 → 归「无题材」组，`has_sector=False`，**不参与龙一裁决**
  （取不到题材是数据缺失，不是「它是杂毛」）

## 与既有闸的分工（⛔ 不重复职责）

| 层 | 模块 | 职责 |
|---|---|---|
| **判龙（本模块）** | `core/sector_leader.py` | 谁是这条线的龙一 / 龙二 / 龙三 |
| 建池 | `core/pool_admit.py` | 每条线留几只（消费本模块的 `leader_key`） |
| 盘后降级 | `pipeline_eod.apply_dragon_gate` | 非龙一不进接力池、龙一被闸时不拿跟风顶替 |
| 9:25 裁决 | `core/pool_call.py` ⑩d | 池内再拦一次（防盘后漏标） |

开关：本模块纯函数、无开关；调用方各带可逆开关。
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Mapping, Sequence

# 同时段容差（分钟）：同板块同板数、封板时刻差 ≤ 此值 → 视为同一批封板，改比封单厚度
LEADER_SEAL_WINDOW_MIN = float(os.environ.get("SECTOR_LEADER_SEAL_WINDOW_MIN", "5") or 5)

# 板块最少涨停家数：不足 = 没有「板块」可言（孤军不算板块龙一）
LEADER_MIN_MATES = int(os.environ.get("SECTOR_LEADER_MIN_MATES", "2") or 2)

# ⛔ 无题材的占位值（东财/选股宝抓不到时会出现）
_THEME_EMPTY = {"", "—", "-", "其他", "无", "未知", "none", "null", "未归类/其他"}

# 龙一「买不进」的三种情形（不涉及封单厚度——那属最高板专用闸，见 thin_seal_top）
LEADER_BLOCK_ONE_WORD = "one_word"
LEADER_BLOCK_ST = "st"

# ⛔ 同层内是否用综合分决定先后（2026-11-04 用户「按照打分来排序」）。
#    0 = 回到旧序（同层按股票代码兜底），用于回滚。
_SCORE_TIEBREAK = (os.environ.get("SECTOR_LEADER_SCORE_TIEBREAK") or "1").strip().lower() not in {
    "0", "false", "off", "no", ""
}


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _opt_num(v: Any) -> float | None:
    """有值返回 float，缺/非法返回 None（⛔ 不用 0 冒充缺失）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def theme_of(row: Mapping[str, Any] | None) -> str:
    """该票所属的**炒作题材**（分组键）。

    ⛔ 优先 `theme_em_name`（东财概念·真实的"这条线"），退 `core_theme` / `theme_name`；
    ⛔ **不用** `sector`/`theme`（申万二级行业）：机器人线跨通用设备/消费电子/汽车零部
       等 5 个行业，用行业分组等于每只都是「行业龙一」—— 正是 10-20「11 只候选 9 只
       龙一」的病根。
    """
    r = row or {}
    for k in ("theme_em_name", "core_theme", "theme_name"):
        v = str(r.get(k) or "").strip()
        if v and v.lower() not in _THEME_EMPTY:
            return v
    return ""


def boards_of(row: Mapping[str, Any] | None) -> int:
    r = row or {}
    for k in ("boards", "lbc", "lianban"):
        v = _opt_num(r.get(k))
        if v is not None and v >= 1:
            return int(v)
    return 1


def name_of(row: Mapping[str, Any] | None) -> str:
    r = row or {}
    return str(r.get("name") or r.get("mc") or "")


def code_of(row: Mapping[str, Any] | None) -> str:
    r = row or {}
    return str(r.get("code") or r.get("dm") or "").strip()


def fbt_min(row: Mapping[str, Any] | None) -> float | None:
    """"HHMMSS"/"HH:MM:SS" → 当日分钟数；缺/非法 → None（⛔ 不用 9999 冒充，缺失要能区分）。"""
    s = str((row or {}).get("fbt") or "").strip()
    if not s:
        return None
    try:
        if ":" in s:
            hh, mm = s.split(":")[0], s.split(":")[1]
            return int(hh) * 60 + int(mm)
        s = s.zfill(6)
        if not s.isdigit():
            return None
        return int(s[0:2]) * 60 + int(s[2:4]) + int(s[4:6]) / 60.0
    except (TypeError, ValueError):
        return None


def seal_ratio_of(row: Mapping[str, Any] | None) -> float | None:
    """封单 / 流通市值（%）——**归一化口径**，绝对"X 亿"只在卡面做辅助。

    三种来源依次尝试：
      ① 候选行已算好的 `seal_ratio_pct`
      ② 涨停池原始 `seal`(亿) / `lt_yi`(亿)
      ③ `seal_amt`(元) / `lt_mktcap`(元)
    """
    r = row or {}
    v = _opt_num(r.get("seal_ratio_pct"))
    if v is not None:
        return v
    seal, lt = _opt_num(r.get("seal")), _opt_num(r.get("lt_yi"))
    if seal is not None and lt and lt > 0:
        return seal / lt * 100.0
    seal_amt, lt_cap = _opt_num(r.get("seal_amt")), _opt_num(r.get("lt_mktcap"))
    if seal_amt is not None and lt_cap and lt_cap > 0:
        return seal_amt / lt_cap * 100.0
    return None


def carry_of(row: Mapping[str, Any] | None) -> int:
    return int(_num((row or {}).get("carry_cnt")))


def is_st_row(row: Mapping[str, Any] | None) -> bool:
    r = row or {}
    if bool(r.get("is_st")):
        return True
    nm = name_of(r).upper().replace(" ", "")
    return "ST" in nm or "*" in nm


def is_one_word_row(row: Mapping[str, Any] | None) -> bool:
    r = row or {}
    if bool(r.get("is_one_word")):
        return True
    # 无显式标记时：换手 < 3% 且封板即一字（与 quant_score 旧口径一致，保守）
    hs, fbt = _opt_num(r.get("hs")), fbt_min(r)
    if hs is not None and hs < 3.0 and fbt is not None and fbt <= 9 * 60 + 30:
        return True
    return False


def time_bands(rows: Sequence[Mapping[str, Any]],
               window_min: float | None = None) -> dict[str, int]:
    """封板时刻分档（0 = 最早档）。**同档内不比时间**。

    以档内**最早封板时刻为锚**滚动切档（不是整除网格）—— 避免 09:29/09:31
    被硬切到两档、而 09:31/09:35 反而同档的边界荒谬。
    ⛔ 缺 fbt → 不给档位（调用方按「最晚」处理）。
    """
    win = LEADER_SEAL_WINDOW_MIN if window_min is None else float(window_min)
    timed = []
    for r in rows:
        t = fbt_min(r)
        if t is not None:
            timed.append((t, code_of(r)))
    timed.sort()
    out: dict[str, int] = {}
    band, anchor = -1, None
    for t, c in timed:
        if anchor is None or t - anchor > win:
            band += 1
            anchor = t
        out[c] = band
    return out


def leader_key(row: Mapping[str, Any], *, band: int = 0) -> tuple:
    """组内龙一排序键（**越小越强**）。

    ⛔ 分层比较，不做加权求和 —— 加权会让「成交额大」之类的单维优势抵消板数劣势，
      正是旧实现把 8.1 亿成交额的多伦顶到龙一的原因。

    ## 2026-11-04 用户「按照打分来排序」
    原第 7 序是 `code_of(row)` —— 同板数/同时段/同封单/同带动/同封板时刻的票
    **按股票代码排**，综合分完全不参与（只在 `select_relay_watch` 里当最后的 tiebreak）。
    实测 11-03：排序第 1 的圣阳股份 raw 63.7（全池最低），raw 最高的东方海洋 79.0 排第 7。
    → 在「更早封板」之后插入**综合分**（分高者优先），代码降为第 8 序纯兜底。
    ⛔ 主序（板数 → 时段档 → 封单 → 带动 → 封板时刻）一个字没动 ——
      「推的票必须是板块龙一」是铁律，打分只在**同层内**决定先后。
    ⛔ 缺分数用中性 0（不罚），保证涨停池等无分场景与旧行为完全一致。
    `SECTOR_LEADER_SCORE_TIEBREAK=0` 回退旧序（代码兜底）。
    """
    ratio = seal_ratio_of(row)
    order: list[Any] = [
        -boards_of(row),                                  # 1. 板数高
        band,                                             # 2. 封板时段档（早者优先）
        0 if ratio is not None else 1,                    # 3. 有封单数据者优先（缺 ≠ 不利，但不给优势）
        -(ratio if ratio is not None else 0.0),           # 4. 封单/流通厚者优先
        -carry_of(row),                                   # 5. 带动家数多者优先
        fbt_min(row) if fbt_min(row) is not None else 1e9,  # 6. 更早封板
    ]
    if _SCORE_TIEBREAK:
        # 7. 同层内综合分高者优先（缺分 → 0 中性，不影响主序与旧行为）
        order.append(-(_opt_num(row.get("score")) or 0.0))
    order.append(code_of(row))                            # 8. 稳定兜底（可复现）
    return tuple(order)


def leader_block_reason(row: Mapping[str, Any]) -> str:
    """龙一是否「买不进」。⛔ 只认确定性买不进，不在此判封单厚度。"""
    if is_st_row(row):
        return LEADER_BLOCK_ST
    if is_one_word_row(row):
        return LEADER_BLOCK_ONE_WORD
    return ""


def compute(rows: Iterable[Mapping[str, Any]],
            theme_map: Mapping[str, Any] | None = None,
            *,
            mates_min: int | None = None,
            window_min: float | None = None,
            extra_by_code: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """按炒作题材分组，算出每组龙一/龙二/龙三。

    `rows`      = 当日涨停池（`zt_list` 或 `ztgc`，两种字段格式都吃得下）
    `theme_map` = 题材快照映射，支持两种形态：
                  · `{code: {"theme": "机器人"}}`（`theme_snapshot.mapping`）
                  · `{code: ["机器人", "人形机器人"]}`（`code2themes`，取第一个）
    `extra_by_code` = 候选行补充（carry_cnt / is_st / is_one_word / seal_ratio_pct
                  涨停池没有这些，从候选并行里补）

    返回:
      {
        "window_min", "mates_min", "asof_count",
        "boards": [ {theme, mates, has_sector, leader{...}, deputies[...],
                     leader_block, ladder_rows[...]}, ... ],   # 按 mates 降序
        "by_code": {code: {dragon_rank, is_sector_dragon, theme, mates,
                           leader_code, leader_name, leader_boards,
                           has_sector, leader_block, seal_ratio_pct, fbt}},
      }
    ⛔ 纯函数：不读盘、不写盘、无副作用（便于单测与前后端复用）。
    """
    mm = int(LEADER_MIN_MATES if mates_min is None else mates_min)
    win = LEADER_SEAL_WINDOW_MIN if window_min is None else float(window_min)
    extra = extra_by_code or {}

    def _theme(code: str, row: Mapping[str, Any]) -> str:
        v = theme_of(row)
        if v:
            return v
        e = theme_map or {}
        tv = e.get(code)
        if isinstance(tv, dict):
            v = str(tv.get("theme") or tv.get("name") or "").strip()
        elif isinstance(tv, (list, tuple)) and tv:
            v = str(tv[0] or "").strip()
        elif isinstance(tv, str):
            v = tv.strip()
        v = str(v or "").strip()
        return "" if v.lower() in _THEME_EMPTY else v

    # ── 合并行（涨停池 + 候选补充）────────────────────────────────────────
    merged: dict[str, dict[str, Any]] = {}
    for r in rows:
        code = code_of(r)
        if not code:
            continue
        row = dict(r)
        e = extra.get(code) or {}
        for k, v in e.items():
            if row.get(k) is None and v is not None:
                row[k] = v
        row["code"] = code
        row["theme_key"] = _theme(code, row)
        merged[code] = row

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in merged.values():
        th = row.get("theme_key") or ""
        groups.setdefault(th or "__theme_missing__", []).append(row)

    by_code: dict[str, dict[str, Any]] = {}
    board_diag: list[dict[str, Any]] = []
    for th, arr in groups.items():
        has_sector = th != "__theme_missing__" and len(arr) >= mm
        bands = time_bands(arr, win)
        ranked = sorted(arr, key=lambda r: leader_key(r, band=bands.get(code_of(r), 999)))
        lead = ranked[0] if ranked else None
        block = leader_block_reason(lead) if (lead and has_sector) else ""
        lead_code = code_of(lead) if lead else ""
        lead_name = name_of(lead) if lead else ""
        for i, r in enumerate(ranked, start=1):
            c = code_of(r)
            # ⛔ 没有板块（题材缺失/独苗）→ 不判龙一（dragon_rank=0 表示「不适用」）
            rank = i if has_sector else 0
            by_code[c] = {
                "dragon_rank": rank,
                "is_sector_dragon": bool(has_sector and rank == 1),
                "theme": "" if th == "__theme_missing__" else th,
                "mates": len(arr),
                "has_sector": has_sector,
                "leader_code": lead_code if has_sector else "",
                "leader_name": lead_name if has_sector else "",
                "leader_boards": boards_of(lead) if (lead and has_sector) else 0,
                "leader_block": block,
                "seal_ratio_pct": (round(seal_ratio_of(r), 2)
                                   if seal_ratio_of(r) is not None else None),
                "seal_ratio_missing": seal_ratio_of(r) is None,
                "fbt": str(r.get("fbt") or ""),
                "boards": boards_of(r),
                "time_band": bands.get(c),
            }
        board_diag.append({
            "theme": "" if th == "__theme_missing__" else th,
            "mates": len(arr),
            "has_sector": has_sector,
            "leader_code": lead_code if has_sector else "",
            "leader_name": lead_name if has_sector else "",
            "leader_boards": boards_of(lead) if (lead and has_sector) else 0,
            "leader_fbt": str((lead or {}).get("fbt") or "") if has_sector else "",
            "leader_seal_ratio_pct": (round(seal_ratio_of(lead), 2)
                                      if (lead and has_sector and seal_ratio_of(lead) is not None)
                                      else None),
            "leader_block": block,
            "dragon_ranks": [
                {"code": code_of(r), "name": name_of(r), "boards": boards_of(r),
                 "fbt": str(r.get("fbt") or ""),
                 "seal_ratio_pct": (round(seal_ratio_of(r), 2)
                                    if seal_ratio_of(r) is not None else None),
                 "dragon_rank": by_code.get(code_of(r), {}).get("dragon_rank", 0)}
                for r in ranked
            ],
        })

    board_diag.sort(key=lambda b: (-b["mates"], b["theme"]))
    return {
        "window_min": win,
        "mates_min": mm,
        "asof_count": len(merged),
        "boards": board_diag,
        "by_code": by_code,
    }
