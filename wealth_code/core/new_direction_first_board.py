"""🌱 新方向首板槽（2026-11-11 用户拍板·「这条线必须打开，优秀的票明早能交易」）

## 用户原话（11-11）

「新方向首板观察槽这个也要的啊！像今天这种非常优秀的、晋级率比较高的票，
  也都要拿出来放在我们的观察池里面，**明天早上可以进入交易的**。」

## 治什么（11-11 实证）

当日 光通信 **10 家全部首板**（整条线零连板）、成交额 **222.6 亿**（全市场第二大），
`PCB板` 5 家全首板、成交额 **255.8 亿**（最大）。这两条线是当天真金白银最多、
方向最新鲜的，但：

  · 接力池 `1进2` 档要 `raw≥72 / quality≥80 / capacity≥68`，一条线只能漏进 1 只
    （11-11 只有 天洋新材 从光通信漏进来，PCB 一只都没有）；
  · 池准入 `pool_admit` 每线只留 3 只、且按龙一身位排，尾盘板反而占位；
  · 旧「新方向首板槽」判据是 `题材家数 ≤ 3`，而光通信有 **10 家** —— 家数越多越
    进不来，与「这条线强不强」恰好相反，且它被 `not two_pool.enabled()` 硬关，
    生产环境（`ENABLE_TWO_POOL_CONTRACT=1`）里**从来没跑过**。

## 判据（用户拍板口径，全部当日收盘可得，无未来函数）

「新方向」= **整条线零连板**（线内最高板 == 1）。不是「家数少」：
  一条线 10 家全是首板，说明方向刚起步、还没有身位，这正是「新方向」；
  而家数只有 2 家的孤军是「没人跟」，不是新方向。

线级门槛（三个都要）：
  ① 家数 ≥ `min_members()`（默认 3）—— 1~2 家不成线，接孤军不做；
  ② 线内最高板 ≤ 1（默认）—— 一旦出现 2 板，这条线已进接力池的常规通道；
  ③ 线成交额 ≥ `min_line_amount_yi()`（默认 10 亿）—— 有真金，不是几只小票自嗨。
  ⛔ 只用 `theme_source == "snapshot"` 的行算线（行业名兜底的行不算真题材）。

个股门槛（「优秀」= **先复用当日过筛，再加两条形态线**）：
  ⓿ **必须已通过当日过筛**（`core/daily_screen`）—— 股性（用户三核心第一项）、
     题材线、封板形态、带动全都由过筛判，本槽**不另造一套**。
     实证 11-11：剑桥科技（近一年 13 次涨停·晋级 **0%**）、华脉科技（晋级 14.3%）
     都在过筛里被判「股性难连板」——它们封板最早，但正是用户最不该接的那种票；
     槽若绕过过筛直接按封板时间排，就会把 0% 晋级的票排到第一位。
  ① 非一字（一字买不到，**事实权限**，竞价再强也买不进）；
  ② 排除 688 / ST（无权限/不可交易）；
  ③ 封板时间 ≤ `max_fbt_min()`（默认 10:00）—— 早盘主动封板，不是尾盘偷袭；
  ④ 炸板 ≤ `max_zbc()`（默认 2 次）；
  ⑤ 成交额 ≥ `min_cje_yi()`（默认 1 亿，只做数据防呆 —— 缩量锁筹不是缺陷）；
  ⑥ 同线身位 < `drop_backrow_rank()`（默认 5）—— 与单池后排过滤同口径，
     不然槽里列出来、合池时又被剔掉，卡面与池子对不上。

每条线最多 `per_line()`（默认 3，与 `POOL_ADMIT_PER_LINE` 同值＝用户 11-11 拍板
「可以放宽到三个身位」），全槽最多 `slots()`（默认 6）。排序：封板时间早 → 成交额大。

## 身份与边界

· 入池行打 `participation_eligibility="conditional"` + `buyable=True`，
  **能过 `_merge_pools` 的资格线**（`POOL_QUALIFIED_ONLY`），09:25 走完整裁决链
  （DEV / 量能带 / 顶部空间硬闸 / quota / 仓位）。「可交易」= 能进裁决，
  **不是**保证买入 —— 买不买仍由竞价现场说了算。
· ⛔ 不改任何买入判据：DEV、量能带、顶部空间、quota、仓位公式一律原样。
· ⛔ 不占 relay 名额，不改 `relay_rank` / `dragon_rank` / 数组顺序。
· 回退：`POOL_NEW_DIRECTION_FIRST_BOARD=0` → 整块关闭，逐字回到 11-11 之前。
"""

from __future__ import annotations

import os
from typing import Any

SCHEMA = "new_direction_first_board_v1"

# ── 开关 ────────────────────────────────────────────────────────────────────
ENABLED_ENV = "POOL_NEW_DIRECTION_FIRST_BOARD"
MIN_MEMBERS_ENV = "POOL_ND_MIN_MEMBERS"
MIN_LINE_AMOUNT_ENV = "POOL_ND_MIN_LINE_AMOUNT_YI"
MAX_LINE_BOARDS_ENV = "POOL_ND_MAX_LINE_BOARDS"
MAX_FBT_ENV = "POOL_ND_MAX_FBT"
MAX_ZBC_ENV = "POOL_ND_MAX_ZBC"
MIN_CJE_ENV = "POOL_ND_MIN_CJE_YI"
PER_LINE_ENV = "POOL_ND_PER_LINE"
SLOTS_ENV = "POOL_ND_SLOTS"

DEFAULT_MIN_MEMBERS = 3
DEFAULT_MIN_LINE_AMOUNT_YI = 10.0
DEFAULT_MAX_LINE_BOARDS = 1
DEFAULT_MAX_FBT = 600          # 10:00 = 600 分钟
DEFAULT_MAX_ZBC = 2
DEFAULT_MIN_CJE_YI = 1.0
DEFAULT_PER_LINE = 3
DEFAULT_SLOTS = 6

_INVALID_LINES = {"", "—", "-", "其他", "无", "未知", "题材缺失"}


def enabled() -> bool:
    return str(os.environ.get(ENABLED_ENV, "1")).strip() not in {"0", "false", "off", ""}


def _num_env(name: str, default):
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return type(default)(str(raw).strip()) if not isinstance(default, float) \
            else float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def min_members() -> int:
    return _num_env(MIN_MEMBERS_ENV, DEFAULT_MIN_MEMBERS)


def min_line_amount_yi() -> float:
    return float(_num_env(MIN_LINE_AMOUNT_ENV, DEFAULT_MIN_LINE_AMOUNT_YI))


def max_line_boards() -> int:
    return int(_num_env(MAX_LINE_BOARDS_ENV, DEFAULT_MAX_LINE_BOARDS))


def max_fbt_min() -> int:
    return int(_num_env(MAX_FBT_ENV, DEFAULT_MAX_FBT))


def max_zbc() -> float:
    return float(_num_env(MAX_ZBC_ENV, DEFAULT_MAX_ZBC))


def min_cje_yi() -> float:
    return float(_num_env(MIN_CJE_ENV, DEFAULT_MIN_CJE_YI))


def per_line() -> int:
    return max(0, int(_num_env(PER_LINE_ENV, DEFAULT_PER_LINE)))


def slots() -> int:
    return max(0, int(_num_env(SLOTS_ENV, DEFAULT_SLOTS)))


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _code(r: dict[str, Any]) -> str:
    return str(r.get("code") or r.get("dm") or "").strip()


def _f(v: Any, d: float | None = None) -> float | None:
    try:
        if v is None or (isinstance(v, str) and not v.strip()):
            return d
        return float(v)
    except (TypeError, ValueError):
        return d


def _i(v: Any, d: int = 0) -> int:
    x = _f(v)
    return d if x is None else int(x)


def _boards(r: dict[str, Any]) -> int:
    for k in ("boards", "lbc", "continuous_boards", "lianban"):
        v = r.get(k)
        if v not in (None, ""):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                pass
    return 1


def fbt_min(raw: Any) -> int | None:
    """封板时间 → 当日分钟数；缺/非法返回 None（诚实不编造）。"""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) < 4:
        return None
    try:
        return int(digits[:2]) * 60 + int(digits[2:4])
    except (TypeError, ValueError):
        return None


def _hhmm(m: int | None) -> str:
    return f"{m // 60:02d}:{m % 60:02d}" if isinstance(m, int) else "—"


def _amount(r: dict[str, Any]) -> float:
    for k in ("cje_yi", "amount_yi", "cje", "amount"):
        x = _f(r.get(k))
        if x is not None:
            return x
    return 0.0


def line_of(r: dict[str, Any]) -> str:
    """涨停池行 → **细化线名**（`theme_base` 归并名优先，再 core_theme）。

    ⛔ 只认 `theme_source == "snapshot"`（真题材）；行业名兜底的行不算线 ——
       `theme_source` 缺失时按「未知来源」，仍用 core_theme（向后兼容老归档）。
    """
    src = str(r.get("theme_source") or "").strip()
    if src == "sector":
        return ""
    name = str(r.get("theme_base") or r.get("core_theme") or r.get("theme") or "").strip()
    if not name or name in _INVALID_LINES:
        return ""
    # 归一化：把细分叫法收进同一条线（光通信/CPO/光模块 → 光通信 等）
    try:
        from core.sector_support import THEME_FINE_BRIDGE, EM_THEME_BRIDGE, SECTOR_THEME_MAP
        return (THEME_FINE_BRIDGE.get(name)
                or EM_THEME_BRIDGE.get(name)
                or SECTOR_THEME_MAP.get(name, name))
    except Exception:                                    # pragma: no cover
        return name


def line_stats(market_rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """按细化线聚合**完整同日涨停池**：家数 / 最高板 / 成交额 / 最早封板。

    ⛔ 必须传完整涨停池（含 688/ST/一字/低成交额），否则「这条线今天有几家」
       会退化成「过筛后剩几家」，光通信 10 家会变成 1 家。
    """
    stats: dict[str, dict[str, Any]] = {}
    for raw in (market_rows or []):
        if not isinstance(raw, dict):
            continue
        line = line_of(raw)
        if not line:
            continue
        s = stats.setdefault(line, {
            "line": line, "members": 0, "max_boards": 0,
            "amount_yi": 0.0, "first_fbt_min": None, "codes": [],
        })
        s["members"] += 1
        s["max_boards"] = max(s["max_boards"], _boards(raw))
        s["amount_yi"] = round(s["amount_yi"] + _amount(raw), 2)
        f = fbt_min(raw.get("fbt"))
        if f is not None and (s["first_fbt_min"] is None or f < s["first_fbt_min"]):
            s["first_fbt_min"] = f
        c = _code(raw)
        if c:
            s["codes"].append(c)
    return stats


def new_direction_lines(market_rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """筛出**够格的新方向线**：家数够 ∧ 整条线零连板 ∧ 成交额够。"""
    out: dict[str, dict[str, Any]] = {}
    lo_members, lo_boards = min_members(), max_line_boards()
    lo_amt = min_line_amount_yi()
    for line, s in line_stats(market_rows).items():
        if s["members"] < lo_members:
            continue
        if s["max_boards"] > lo_boards:
            continue
        if s["amount_yi"] < lo_amt:
            continue
        s = dict(s)
        s["first_seal"] = _hhmm(s.get("first_fbt_min"))
        out[line] = s
    return out


def build(
    candidates: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
    *,
    exclude_codes: set[str] | None = None,
    screened_codes: set[str] | None = None,
    drop_backrow_rank: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """生成「新方向首板」槽。

    `screened_codes` = 已通过当日过筛（`core/daily_screen`：题材线 / **股性** / 封板形态 /
    带动）的代码集。传了它就**只从过筛通过的票里挑** —— 本槽只负责回答「哪条线是新方向、
    线上哪几只算优秀」，⛔ 不自己另造一套质量判据（尤其不重造「股性」：用户三核心第一项，
    过筛早已实现）。缺省 None = 不做该过滤（向后兼容纯函数调用）。

    返回 `(rows, diag)`：
      · `rows`  = 可交易的入池行（浅拷贝，已打好 bucket / 资格字段）；
      · `diag`  = 线级统计 + 入选明细 + 被排除明细（**留痕**，卡面/审计可查）。
    ⛔ 本函数纯计算，不写任何全局状态、不改入参。
    """
    diag: dict[str, Any] = {
        "schema": SCHEMA, "enabled": enabled(),
        "min_members": min_members(), "max_line_boards": max_line_boards(),
        "min_line_amount_yi": min_line_amount_yi(), "max_fbt": _hhmm(max_fbt_min()),
        "max_zbc": max_zbc(), "min_cje_yi": min_cje_yi(),
        "per_line": per_line(), "slots": slots(),
        "require_screened": screened_codes is not None,
        "lines": [], "picked": [], "skipped": [],
    }
    if not enabled():
        return [], diag

    lines = new_direction_lines(market_rows)
    diag["lines"] = sorted(
        ({"line": s["line"], "members": s["members"], "max_boards": s["max_boards"],
          "amount_yi": s["amount_yi"], "first_seal": s.get("first_seal")}
         for s in lines.values()),
        key=lambda x: -x["amount_yi"])

    ex = {str(c) for c in (exclude_codes or set())}
    ok = {str(c) for c in screened_codes} if screened_codes is not None else None
    cap_fbt, cap_zbc, cap_cje = max_fbt_min(), max_zbc(), min_cje_yi()
    cap_row = int(drop_backrow_rank or 0)

    by_line: dict[str, list[dict[str, Any]]] = {}
    for cand in (candidates or []):
        if not isinstance(cand, dict):
            continue
        code = _code(cand)
        name = str(cand.get("name") or "")
        if not code or not name:
            continue
        line = line_of(cand)
        if line not in lines:
            # 不在够格的新方向线里 —— 只在「线够格但票不合格」时留痕，避免噪声
            continue
        why = []
        if code in ex:
            why.append("已在池")
        if ok is not None and code not in ok:
            why.append("没过当日过筛（股性/题材线/封板形态）")
        if code.startswith("688") or "ST" in name.upper():
            why.append("688/ST")
        if _boards(cand) != 1:
            why.append(f"{_boards(cand)}板（不是首板）")
        if cand.get("is_one_word") is True:
            why.append("一字买不到")
        f = fbt_min(cand.get("fbt"))
        if f is None:
            why.append("封板时间缺失")
        elif f > cap_fbt:
            why.append(f"{_hhmm(f)} 封板（晚于 {_hhmm(cap_fbt)}）")
        z = _f(cand.get("zbc"))
        if z is not None and z > cap_zbc:
            why.append(f"炸板 {int(z)} 次")
        cje = _amount(cand)
        if cje < cap_cje:
            why.append(f"成交额 {cje:.2f} 亿")
        if cap_row > 0:
            dr = _f(cand.get("dragon_rank"))
            if dr is not None and dr >= cap_row:
                why.append(f"同线身位第 {int(dr)}")
        if why:
            diag["skipped"].append({"code": code, "name": name, "line": line,
                                    "reason": "；".join(why)})
            continue
        by_line.setdefault(line, []).append({"_cand": cand, "_fbt": f, "_cje": cje})

    picked: list[dict[str, Any]] = []
    for line, arr in by_line.items():
        # 排序：封板早 → 成交额大（都是当日收盘可得的事前信息）
        arr.sort(key=lambda x: (x["_fbt"], -x["_cje"]))
        for x in arr[:per_line()]:
            c = x["_cand"]
            row = dict(c)
            row.update(
                bucket="new_direction", bucket_label="新方向首板",
                # ⛔ 必须用 `relay_candidate`，**不能用 `conditional`**：
                #    `pool_call.py:1993` 把 `conditional` 当作「历史修复条件资格」，
                #    要求 `historical_repair.eligibility=="conditional"` 且 quality/asof 齐全，
                #    否则 09:25 一句「条件资格缺失或过期，不参与」直接短路 ——
                #    那就等于票在池里、明早却买不了（正好违反用户「明早能进入交易」）。
                participation_eligibility="relay_candidate", buyable=True,
                watch_only=False,
                new_direction_line=line,
                new_direction_fbt=_hhmm(x["_fbt"]),
                new_direction_line_members=lines[line]["members"],
                new_direction_line_amount_yi=lines[line]["amount_yi"],
                watch_group="新方向首板",
                watch_reason=(f"{line} 整条线 {lines[line]['members']} 家全首板"
                              f"（最高 1 板·成交额 {lines[line]['amount_yi']:.0f} 亿），"
                              f"本票 {_hhmm(x['_fbt'])} 封板、已过当日过筛"
                              f" —— 新方向首板，能不能接由明早竞价定"),
            )
            picked.append(row)

    # 全局名额：按线成交额（线越硬越先）→ 线内封板时间
    _line_order = {s["line"]: -s["amount_yi"] for s in lines.values()}
    picked.sort(key=lambda r: (_line_order.get(r.get("new_direction_line") or "", 0),
                               fbt_min(r.get("fbt")) or 9999))
    picked = picked[:slots()]
    for i, r in enumerate(picked, 1):
        r["new_direction_rank"] = i
        diag["picked"].append({
            "code": r.get("code"), "name": r.get("name"),
            "line": r.get("new_direction_line"), "fbt": r.get("new_direction_fbt"),
            "rank": i,
        })
    diag["picked_count"] = len(picked)
    diag["skipped_count"] = len(diag["skipped"])
    return picked, diag
