#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选池准入闸 · 只留龙头前排（2026-10-21 用户定·源头治理）。

## 用户原话

> 「从源头上解决问题，我觉得票太多了，后排的票其实不需要到这里面来，
>  我们接力的票必须是龙头票，龙头票才能做接力，趋势票做容量观察用，
>  前排的须是龙头前排，后排的票就不要推到这里面来了！！！」

## 问题的真根

不是 9:25 裁判太松——**票在盘后就已经进池了**：

1. `select_relay_watch` 四档严格资格空 → 降级级联（relaxed → broad → emergency），
   门槛一路降到「lbc 1~6 且 raw≥55」（`_relay_broad_ok`），后排票从这里灌进来。
2. `watch_mapped = watch_sorted[:8]` 硬塞 8 只观察票。
3. `is_sector_dragon` 按**申万二级行业**判（`_attach_sector_ladder` 用 `theme` 字段）——
   而机器人这条大线跨了通用设备 / 消费电子 / 汽车零部 / 照明设备 / 数字媒体 5 个行业，
   每个行业只有 1 只涨停 → **每只都成了「行业龙一」** → 全都有资格进池。
   实测 10-20：11 只候选里 9 只 `is_sector_dragon=True`，label 全是「准龙头」。

盘后池子 11 只 → 9:25 只能在这 11 只里裁 → 卡上必然热闹。**收口必须在池子生成时**。

## 本模块做什么

按**炒作题材**（`theme_em_name` 优先，退 `core_theme`）分组，**每组只留最强的 N 只**：
    排序键 = 板数 → 带动家数 → 封板时间早 → 综合分
其余同线票 = 后排 → **直接不进池**（⛔ 不是标观察，是不出现）。

⛔ 「无题材」组（`theme_em_name` / `core_theme` 都取不到）**不做组内淘汰**：
   取不到题材是**数据缺失**，不是「它是杂毛」。拿缺失当证据砍票＝凭空消失，
   违反「缺数据≠不利证据」。这类票保留，并在 `admit_note` 里标 `theme_missing`。

## 开关（全部可逆）

- `POOL_ADMIT_ENABLED`（默认开）：关 → 逐字节回到旧池（不做任何准入淘汰）。
- `POOL_ADMIT_PER_LINE`（**默认 2**）：每条线最多留几只。
  ⚠️ 2026-10-21 17:25 用户「每个板块尽量留两只活口，除非实在没有」→ 从 1 改 2：
  留一只的风险是——唯一那只是后排（如某线最高板是池外一字板买不到），整条线等于没有入口；
  留两只＝龙头 + 次排，既守住「不做纯后排」，又不至于把可做的那条线整条封死。
- `POOL_ADMIT_MIN_MATES`（默认 2）：涨停池家数 ≥ 此值才算「一条线」；
  不足（1~2 家）的小题材按「无组」处理 → 不淘汰（防止因小事砍票）。

## 与既有闸的关系（⛔ 不重复职责）

| 闸 | 层 | 职责 |
|---|---|---|
| 本模块 `pool_admit` | **盘后·建池** | 谁有资格进池（前排/后排） |
| `pool_call` 后排闸 | 9:25·裁决 | 池内这只能不能买（核心还在板就不买小弟） |
| `pool_call` 中位股闸 | 9:25·裁决 | 高度分布位置（H≥5 时 3~H-2 板不碰） |

本模块只做「进不进池」，**不碰任何 9:25 裁决逻辑**；关掉它，9:25 那三道闸照旧工作。
"""
from __future__ import annotations

import os
from typing import Any

ENABLED_ENV = "POOL_ADMIT_ENABLED"
PER_LINE_ENV = "POOL_ADMIT_PER_LINE"
MIN_MATES_ENV = "POOL_ADMIT_MIN_MATES"
MAX_ZBC_ENV = "POOL_ADMIT_MAX_ZBC"

DEFAULT_PER_LINE = 3
# ↳ 2026-11-11 用户「C 这个可以放宽到三个身位」→ 默认 3（每题材留前 3）。
#   历史：11-04「解除每线两只」成 0=不限；11-11 收紧到 3（仍比最初的 2 宽）。
#   回退：`POOL_ADMIT_PER_LINE=0`（不限）或 `=2`（旧口径：龙头+次排）。
# ⛔ 0 仍表示**不限**（不是「一只都不留」）。
#   ⛔ 解除的是「进池只数」，不是「买入资格」——后排仍由 pool_call 的身位闸/龙一闸拦。
# 涨停池同题材 < 2 家 → 当作「没有成线」，不参与组内淘汰（1 家的"线"没有后排可言）
DEFAULT_MIN_MATES = 2
# 🎯 2026-11-11 用户：「A 炸板 >5 确实可以丢出去」——炸板次数 > 5 的票不进池。
#    ⛔ 是「>5」不是「≥5」：5 次仍保留。回退 `POOL_ADMIT_MAX_ZBC=99`。
DEFAULT_MAX_ZBC = 5

# ⛔ 无题材的占位值（东财/选股宝抓不到时会出现）
_THEME_EMPTY = {"", "—", "-", "其他", "无", "未知", "none", "null"}


def _flag(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() not in (
        "0", "false", "no", "off")


def enabled() -> bool:
    return _flag(ENABLED_ENV)


def per_line() -> int:
    """每条线最多留几只。`0` = **不限**（2026-11-04 用户「解除每一个题材保留两只的限制」）。

    `POOL_ADMIT_PER_LINE=2` 可逐字回退旧口径（每线留龙头+次排两只）。
    ⛔ 0 不是「一只都不留」——`admit()` 里 0 走 `unlimited` 分支全留，
       真正的淘汰仍由后面的龙一闸/身位闸/薄封闸/四条件/名额完成。
    """
    try:
        n = int(str(os.environ.get(PER_LINE_ENV, DEFAULT_PER_LINE)).strip())
    except (TypeError, ValueError):
        return DEFAULT_PER_LINE
    return n if n >= 0 else 0


def max_zbc() -> float:
    """炸板次数上限（**严格大于**才剔除）。99 = 关闭该闸。"""
    try:
        return float(str(os.environ.get(MAX_ZBC_ENV, DEFAULT_MAX_ZBC)).strip())
    except (TypeError, ValueError):
        return float(DEFAULT_MAX_ZBC)


def min_mates() -> int:
    try:
        return int(str(os.environ.get(MIN_MATES_ENV, DEFAULT_MIN_MATES)).strip())
    except (TypeError, ValueError):
        return DEFAULT_MIN_MATES


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _fbt_min(fbt: Any) -> float:
    """封板时间 HHMMSS → 分钟数；越早越小。非法 → 9999（排最后，不当作最早）。"""
    s = str(fbt or "").strip()
    if len(s) < 5 or not s.isdigit():
        return 9999.0
    try:
        h, m = int(s[:2]), int(s[2:4])
        return h * 60 + m + (int(s[4:6]) / 60 if len(s) >= 6 else 0)
    except (TypeError, ValueError):
        return 9999.0


def theme_of(row: dict[str, Any]) -> str:
    """该票所属的**炒作题材**（分组键）。

    ⛔ 优先 `theme_em_name`（东财概念·真实的"这条线"），退 `core_theme`；
    ⛔ **不用** `industry_sector`/`theme`（申万二级）：机器人线跨 5 个行业，
       用行业分组等于每只都是行业龙一 → 正是票太多的病根。
    """
    for k in ("theme_em_name", "core_theme", "theme_name"):
        v = str((row or {}).get(k) or "").strip()
        if v and v.lower() not in _THEME_EMPTY:
            return v
    return ""


def mates_of(row: dict[str, Any]) -> int:
    """该票所在题材的涨停池家数（用于判断「这是不是一条线」）。"""
    n = max(_num((row or {}).get("theme_mates_cnt")),
            _num((row or {}).get("sector_mates_cnt")))
    return int(n)


def strength_key(row: dict[str, Any], *, band: int = 0) -> tuple:
    """组内「谁最强」——**单一源转发到 `core.sector_leader.leader_key`**。

    判据（从强到弱）：板数 → 封板时段档（±5 分钟同档）→ 封单/流通% → 带动家数
    → 封板时间 → 代码。

    ⛔ 为什么板数排第一：前排的定义就是「这条线走到最高/最靠前的那只」。
      用分数排会把低位高分票顶到 3 板龙头前面（与 `_leader_preference_key` 同一条理）。
    ⛔ 2026-10-27 改为单一源：旧实现在这里用 (板数, 带动, 封板早, 综合分)，而
      `_attach_sector_ladder` 另用 (板数, 涨幅, 成交额) 判 `is_sector_dragon` ——
      两套口径给出两个"龙一"（多伦 vs 赛福天），建池与闸门各认一套，推出去的票自然打架。
      现在「谁是这条线最强」只有一处定义（`core/sector_leader.py`），本函数只做转发。
    """
    from core import sector_leader
    return sector_leader.leader_key(row, band=band)


# ── 「够不够格做接力」判据（⛔ 只判接力价值，不重复四档资格）────────────────
# 用户原话「我们接力的票必须是龙头票，龙头票才能做接力，趋势票做容量观察用」：
#   · 接力价值 = 连板（≥2 板，有晋级预期）**或** 带动板块（carry_cnt>0）
#   · 首板且零带动 = 没被市场检验过，放观察不占接力名额
# ⛔ 原 relay 身份也算一条：四档资格（strict/relaxed…）已经筛过一遍，
#    不能被本闸推翻（否则关闸前后的 relay 语义会漂）。
RELAY_MIN_BOARDS = 2


def relay_worth(row: dict[str, Any], *, was_relay: bool = False) -> bool:
    """该票是否够格进**接力池**（而非容量观察池）。

    ⛔ 2026-10-27 收紧（用户「推的全是杂毛…既不是龙一也不是高度第一」）：
    旧口径里「首板 + 带动>0」也算接力格，结果**首板后排被提拔进接力池**——
    实证 10-27 文投控股（大消费线龙一上海九百被成交额门槛剔走后，它成了组内最强）：
    09:41 才封、炸 5 次、封单占流通 0.12%、梯队 D，却被推成接力池第 2 位。
    「接力」的语义是**连板晋级**，首板没有晋级可接；首板该进观察池或新方向首板槽。
    ⛔ 唯一例外：`was_relay=True`（quant_score 四档里的「1进2」档已判过格）——
      那是系统明确定的接力资格，本闸不推翻。
    """
    if was_relay:
        return True                                   # 四档已判过格，不推翻
    return _num((row or {}).get("boards")) >= RELAY_MIN_BOARDS


# ── 首板「无辨识度」闸（2026-11-03 用户转述 NIKA 点评：「首板就不该进来」）──────
# 症状（11-03 实测）：09:25 竞价卡 11 只里 **7 只是 1 板**，其中 4 只跟风首板
#   （国金证券 carry=1 / 统一股份 2 / 华瓷股份 0 / 三峡新材 1）与唯一 6 板龙头
#   青岛食品并排陈列，卡面按综合分排，1 板稿排在 6 板前面。
# 判据：**首板没有晋级可接，只有它把这条线带起来了才配做 1进2 候选**。
#   带动家数 = 封板后同题材跟封数（`carry_cnt`，`core.sector_leader` 已在用），
#   ≥3 说明这条线因它点火；<3 就是单打独斗的杂毛。
# ⛔ 不动 `relay_worth`（那是「进接力池」的资格）；本闸更靠前，决定**进不进池**。
# ⛔ 可回滚：`POOL_ADMIT_FIRST_BOARD_MIN_CARRY=0` 恢复旧口径（首板全进观察池）。
FIRST_BOARD_MIN_CARRY_ENV = "POOL_ADMIT_FIRST_BOARD_MIN_CARRY"
FIRST_BOARD_MIN_CARRY_DEFAULT = 3


def first_board_min_carry() -> int:
    try:
        return max(0, int(os.environ.get(FIRST_BOARD_MIN_CARRY_ENV, FIRST_BOARD_MIN_CARRY_DEFAULT)))
    except (TypeError, ValueError):
        return FIRST_BOARD_MIN_CARRY_DEFAULT


def first_board_worthy(row: dict[str, Any], *, was_relay: bool = False,
                       min_carry: int | None = None) -> bool:
    """首板是否够格进池。非首板一律 True（本闸只管 1 板）。"""
    if was_relay:
        return True                                   # 四档已判过「1进2」，不推翻
    if _num((row or {}).get("boards")) >= 2:
        return True
    evidence = (row or {}).get("n_plus_n") or {}
    if (os.environ.get("ENABLE_HISTORICAL_REPAIR", "1") != "0"
            and evidence.get("quality") == "ok" and evidence.get("prior_boards", 0) >= 2
            and evidence.get("prior_dates") and evidence.get("restart_dates")):
        return True  # 历史修复保留观察，不代表接力准入。
    need = first_board_min_carry() if min_carry is None else min_carry
    if need <= 0:
        return True                                   # 闸关
    return _num((row or {}).get("carry_cnt")) >= need


# ══════════════════════════════════════════════════════════════════════════
# 🔍 池外龙头校验（2026-10-21 用户「可以加一层池外龙头校验」）
# ══════════════════════════════════════════════════════════════════════════
# ⛔ 为什么准入闸不够：准入闸只能在**池内**比强弱。若某条线真正的龙头
#   因为分数不够/被别的闸挡掉而没进池，那"池内第一"也可能只是后排。
#   实证（10-20）：机器人线 3 板有 海洋王/大业股份/京城股份 三只，
#   准入闸留了海洋王（在池）+ 宁波东力（2板），但如果海洋王被挡，
#   池内最高就只剩 2 板 → 整条线变成"没有龙头"的后排池，卡面却毫无提示。
#
# 判据（用 `theme_mates` 找同题材全部成员，再对照当日涨停池的板数）：
#   ① 自己就是同题材最高板 → front（前排）
#   ② 自己不是最高，但**池内有**最高板 → second（次排·有前排兜着）
#   ③ 自己不是最高，且**最高板全在池外** → back（真后排·该线没有入口）
#   ④ 同题材只有自己 1 家 → lone（没法比，不裁决）
#   ⑤ 取不到题材 / 取不到涨停池 → unknown（诚实降级，⛔ 不猜）
#
# ⛔ 日期铁律：涨停池必须读**与候选池同一交易日**（`candidates.date`），
#   ⛔ 不是"今天"。10-20 盘后候选要配 `zt_2026-10-20`，用 10-21 的池子
#   等于拿明天的数据判今天的票。
OUTPOOL_ENV = "POOL_ADMIT_OUTPOOL_CHECK"


OUTPOOL_DROP_ENV = "POOL_ADMIT_OUTPOOL_DROP"


def outpool_enabled() -> bool:
    return _flag(OUTPOOL_ENV)


def outpool_drop_enabled() -> bool:
    """是否**砍掉**被判定为后排的票。默认开（2026-10-27 起·`POOL_ADMIT_OUTPOOL_DROP=0` 可回滚）。

    演进：2026-10-21 用户说「可以加一层池外龙头校验……我来看看对不对」→ 先只标记不砍。
    2026-10-27 实证校验判定**是对的**：文投控股被明确判为
      「这条线最高 5 板是 上海九百，池内没有 → 它是后排」，
      但因为没有砍，它照样进了接力池第 2 位并推到群里 —— 用户当场骂「全是杂毛」。
    判断已经验证过、且不砍会直接违背「后排不要推过来」，故默认改为真砍。
    ⛔ 砍的只是**池外有更高板兜不住的后排**；有前排在池内的「次排」不砍（降观察池）。
    """
    return _flag(OUTPOOL_DROP_ENV, "1")


def outpool_dragon_check(pool: list[dict[str, Any]],
                         zt_rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """池外龙头校验：判断池内每只票在**自己那条线**里到底排第几。

    `pool`    = 准入后的候选（relay + watch）
    `zt_rows` = **同一交易日**的涨停池（`zt_history/zt_{date}.json` 的 `zt_list`）；
                缺失/为空 → 全部 unknown（⛔ 不裁决、不猜）

    返回 `{"enabled":.., "asof":.., "rows": {code: {...}}, "back": [...], "summary": {...}}`
    ⛔ 只做判断与留痕，**不自行砍票**——砍不砍由调用方决定（见 `OUTPOOL_DROP`）。
    """
    if not outpool_enabled():
        return {"enabled": False, "rows": {}, "back": []}

    # ⛔ 涨停池有**两种字段格式**，函数必须都吃得下：
    #   · `zt_history/zt_{date}.json` 的 `zt_list`：`{code, name, boards}`
    #   · `load_zt_pool()` 的 `ztgc`（东财原始）：`{dm, mc, lbc}`
    #   不归一化就会出现「涨停池读到 54 只、却全部匹配不上」的静默失效。
    def _norm(r: dict[str, Any]) -> dict[str, Any] | None:
        r = r or {}
        code = str(r.get("code") or r.get("dm") or "").strip()
        if not code:
            return None
        return {"code": code,
                "name": str(r.get("name") or r.get("mc") or ""),
                "boards": int(_num(r.get("boards") if r.get("boards") is not None
                                   else r.get("lbc")))}

    zmap: dict[str, dict[str, Any]] = {}
    for r in (zt_rows or []):
        n = _norm(r)
        if n:
            zmap[n["code"]] = n

    by_code = {str((x or {}).get("code") or ""): x for x in (pool or []) if (x or {}).get("code")}
    # 同题材索引（池内）：题材 → [code]
    line_of: dict[str, list[str]] = {}
    for c, x in by_code.items():
        th = theme_of(x)
        if th:
            line_of.setdefault(th, []).append(c)

    rows: dict[str, dict[str, Any]] = {}
    back: list[str] = []
    for code, x in by_code.items():
        th = theme_of(x)
        name = str(x.get("name") or "")
        my_b = int(_num(x.get("boards")))
        if not th:
            rows[code] = {"tier": "unknown", "why": "题材取不到，没法比",
                          "theme": "", "boards": my_b}
            continue
        if not zmap:
            rows[code] = {"tier": "unknown", "why": "当日涨停池缺失，不裁决",
                          "theme": th, "boards": my_b}
            continue
        # 同题材成员 = theme_mates ∪ 自己 ∪ 池内同题材
        mates = {str(m) for m in (x.get("theme_mates") or []) if str(m).strip()}
        mates.add(code)
        mates.update(line_of.get(th) or [])
        sib: list[dict[str, Any]] = []
        for c in mates:
            if c in zmap:
                sib.append({"code": c, "name": str(zmap[c].get("name") or ""),
                            "boards": int(_num(zmap[c].get("boards")))})
            elif c in by_code:
                sib.append({"code": c, "name": str(by_code[c].get("name") or ""),
                            "boards": int(_num(by_code[c].get("boards")))})
        if len(sib) <= 1:
            # ⛔ 这条线只有它 1 家 → 池外当然没有更强的 → 判 front，⛔ 不是 lone。
            #    `lone` 只在"别处另有更高板、但那只跟本线无关"时才需要，本校验不产生。
            #    校验要回答的是「池外有没有更强的」，答案是没有。
            rows[code] = {"tier": "front", "why": "这条线只有它 1 家",
                          "theme": th, "boards": my_b, "line_top": my_b}
            continue
        top = max(s["boards"] for s in sib)
        tops = [s for s in sib if s["boards"] == top]
        top_in_pool = [s for s in tops if s["code"] in by_code]
        top_out = [s for s in tops if s["code"] not in by_code]
        if my_b >= top:
            rows[code] = {"tier": "front", "why": f"它就是这条线最高（{top} 板）",
                          "theme": th, "boards": my_b, "line_top": top}
        elif top_in_pool:
            rows[code] = {"tier": "second",
                          "why": f"这条线最高 {top} 板、池内有"
                                 f"{('、'.join(s['name'] for s in top_in_pool))}兜着",
                          "theme": th, "boards": my_b, "line_top": top,
                          "line_tops": [s["name"] for s in tops]}
        else:
            names = "、".join(s["name"] for s in top_out)
            rows[code] = {"tier": "back",
                          "why": f"这条线最高 {top} 板是 {names}，池内没有 → 它是后排",
                          "theme": th, "boards": my_b, "line_top": top,
                          "line_tops": [s["name"] for s in tops]}
            back.append(code)

    return {
        "enabled": True,
        "zt_rows": len(zmap),
        "rows": rows,
        "back": back,
        "summary": {
            "front": sum(1 for v in rows.values() if v["tier"] == "front"),
            "second": sum(1 for v in rows.values() if v["tier"] == "second"),
            "back": len(back),
            "lone": sum(1 for v in rows.values() if v["tier"] == "lone"),
            "unknown": sum(1 for v in rows.values() if v["tier"] == "unknown"),
        },
    }


def admit(relay: list[dict[str, Any]],
          watch: list[dict[str, Any]],
          *,
          per_line_cap: int | None = None,
          min_mates_floor: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """源头准入：同线只留前排，后排不进池。

    返回 `(relay, watch, diag)`；`diag` 供 `candidates.diagnostics.pool_admit` 留痕
    （谁进了、谁被挡、为什么）。

    ⛔ 保序：输出沿用「原池次序」= 先 relay 后 watch，组内按 `strength_key` 降序。
    ⛔ 幂等：同一输入多次调用结果一致（纯函数，无副作用）。
    """
    if not enabled():
        return relay, watch, {"enabled": False, "reason": "POOL_ADMIT_ENABLED=0"}

    cap = per_line_cap if isinstance(per_line_cap, int) and per_line_cap >= 0 else per_line()
    floor = (min_mates_floor if isinstance(min_mates_floor, int) else min_mates())
    unlimited = int(cap) <= 0          # 0 = 不限（2026-11-04 用户解除每线两只限制）

    # 原 bucket 归属（relay 优先），用于收敛后还原
    bucket: dict[str, str] = {}
    order: dict[str, int] = {}
    pool: list[dict[str, Any]] = []
    for i, r in enumerate([*relay, *watch]):
        code = str((r or {}).get("code") or "")
        if not code or code in bucket:
            continue
        bucket[code] = "relay" if i < len(relay) else "watch"
        order[code] = i
        pool.append(r)

    # 🎯 炸板闸（2026-11-11 用户 A）：炸板次数 > 上限 → 直接不进池。
    _zbc_cap = max_zbc()
    zbc_dropped: list[str] = []
    _kept: list[dict[str, Any]] = []
    for r in pool:
        try:
            _z = float((r or {}).get("zbc"))
        except (TypeError, ValueError):
            _z = 0.0
        if _z > _zbc_cap:
            zbc_dropped.append(str((r or {}).get("name") or (r or {}).get("code") or ""))
            continue
        _kept.append(r)
    pool = _kept

    groups: dict[str, list[dict[str, Any]]] = {}
    theme_missing: list[str] = []
    for r in pool:
        th = theme_of(r)
        if not th:
            # ⛔ 题材取不到 = 数据缺失，不是「它是杂毛」→ 单独成组、不做淘汰
            theme_missing.append(str(r.get("code") or ""))
            th = "__theme_missing__"
        groups.setdefault(th, []).append(r)

    keep: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    group_diag: list[dict[str, Any]] = []
    line_rank: dict[str, int] = {}      # code → 组内名次（0 = 这条线最强那只）
    from core import sector_leader
    for th, arr in groups.items():
        # 组内排序 = `sector_leader.leader_key`（**升序：越小越强**）
        # ⛔ 2026-10-27：旧实现是 `sorted(..., key=strength_key, reverse=True)`（越大越强）；
        #    改单源后键的含义反了，必须同步去掉 reverse，否则「组内最弱」会被当龙一留下。
        _bands = sector_leader.time_bands(arr)
        ranked = sorted(arr, key=lambda r: strength_key(
            r, band=_bands.get(str((r or {}).get("code") or ""), 999)))
        for _i, _r in enumerate(ranked):
            line_rank[str(_r.get("code") or "")] = _i
        if th == "__theme_missing__":
            # 数据缺失 → 全留（缺数据不作不利证据）
            keep.extend(ranked)
            group_diag.append({"theme": "(题材缺失)", "kept": len(ranked),
                               "dropped": 0, "note": "theme_missing"})
            continue
        mates = max((mates_of(r) for r in ranked), default=0)
        if mates and mates < floor:
            # 小题材（< min_mates 家）→ 没有「线下后排」这回事，全留
            keep.extend(ranked)
            group_diag.append({"theme": th, "kept": len(ranked), "dropped": 0,
                               "mates": mates, "note": "small_line"})
            continue
        if unlimited:
            # 2026-11-04 用户解除每线两只限制：整条线全留（不再有「线下后排」这一刀）。
            keep.extend(ranked)
            group_diag.append({
                "theme": th, "mates": mates, "kept": len(ranked), "dropped": 0,
                "kept_names": [str(r.get("name") or "") for r in ranked],
                "dropped_names": [], "note": "per_line_unlimited",
            })
            continue
        keep.extend(ranked[:cap])
        dropped.extend(ranked[cap:])
        group_diag.append({
            "theme": th, "mates": mates, "kept": min(cap, len(ranked)),
            "dropped": max(0, len(ranked) - cap),
            "kept_names": [str(r.get("name") or "") for r in ranked[:cap]],
            "dropped_names": [str(r.get("name") or "") for r in ranked[cap:]],
        })

    # ── 收敛后重新分桶（⛔ 不再沿用原 bucket）─────────────────────────────
    # ⛔ 原写法「沿用原 bucket」有个致命问题：机器人线原 relay 是 3 只 2 板后排，
    #    收敛后最强的海洋王（3 板·原在 watch）被留下 → relay 变成 0 只、龙头反而在观察池，
    #    与用户「接力票必须是龙头票」正好相反。
    #
    # ✅ 现行口径直接对应用户原话（2026-10-21）：
    #    「我们接力的票必须是龙头票，龙头票才能做接力，趋势票做容量观察用，
    #      前排的须是龙头前排」
    #    → **每条线最强那只 = 前排龙头 → 接力池**（且还要过 `relay_worth`）
    #    → **同线第 2 只 = 活口 → 观察池**（`POOL_ADMIT_PER_LINE=2` 的第二席，
    #       用户 17:25「每个板块尽量留两只活口」——留一只活口若恰是后排，整条线就没了入口）
    relay_codes = {str(r.get("code")) for r in relay}          # 原 relay 身份，作资格参考

    # ── 首板「无辨识度」闸（2026-11-03）─────────────────────────────────
    # ⛔ 位置必须在**重新分桶之前**：否则杂毛首板会先被判成「次排活口」进观察池，
    #    再想剔就得从桶里捞，容易漏（实测 11-03 观察池 7 只里 4 只是这种）。
    _fb_dropped: list[dict[str, Any]] = []
    _kept: list[dict[str, Any]] = []
    for r in keep:
        if first_board_worthy(r, was_relay=str(r.get("code")) in relay_codes):
            _kept.append(r)
        else:
            _fb_dropped.append(r)
    keep = _kept
    for r in _fb_dropped:
        r["_pool_admit_dropped"] = True
        r["_pool_admit_drop_reason"] = f"1板且带动仅 {int(_num(r.get('carry_cnt')))} 家（< {first_board_min_carry()}）"

    out_relay: list[dict[str, Any]] = []
    out_watch: list[dict[str, Any]] = []
    for r in keep:
        code = str(r.get("code") or "")
        is_front = line_rank.get(code, 0) == 0
        from core import two_pool
        if (is_front and relay_worth(r, was_relay=code in relay_codes)
                and (not two_pool.enabled() or code in relay_codes)
                and not two_pool.observation_only(r)):
            out_relay.append(r)                 # 前排龙头 → 接力
        else:
            out_watch.append(r)                 # 次排活口 → 容量观察

    def _final_key(r: dict[str, Any]) -> tuple:
        return (0 if str(r.get("code")) in relay_codes else 1,
                order.get(str(r.get("code")), 999))

    new_relay = sorted(out_relay, key=_final_key)
    new_watch = sorted(out_watch, key=_final_key)
    # ⛔ 桶归属改了，`bucket`/`bucket_label` 必须同步 —— 否则卡面会出现
    #    「在观察段、标签却写接力池」的自相矛盾（实测宁波东力）。
    for _r in new_relay:
        _r["bucket"] = "relay"
        _r["bucket_label"] = "接力池"
    for _r in new_watch:
        _r["bucket"] = "watch"
        _r["bucket_label"] = "观察池"

    # ⛔ 序号必须按**准入后的新次序**重排（2026-10-22 卡面实测「None、」）：
    #    本闸会把「每条线最强那只」从观察池提进接力池，而这些行原本没有
    #    `relay_rank`（只有 relay 行才有）→ 卡面渲染 `it.get('relay_rank')` 打出
    #    `None、`；留下来的 relay 行则保留重排前的旧序号（出现「2、」开头但排第一位）。
    #    ⛔ 只重排序号，不动池子构成/次序/分数。
    for _i, _r in enumerate(new_relay, start=1):
        _r["relay_rank"] = _i
        _r.pop("watch_rank", None)
    for _i, _r in enumerate(new_watch, start=1):
        _r["watch_rank"] = _i
        _r.pop("relay_rank", None)

    for r in dropped:
        # 留痕在票上（不落盘到池，落到 diag 即可；这里只是给审计用）
        r["_pool_admit_dropped"] = True

    diag = {
        "enabled": True,
        "per_line": cap,
        "min_mates": floor,
        "before": {"relay": len(relay), "watch": len(watch)},
        "after": {"relay": len(new_relay), "watch": len(new_watch)},
        "dropped": len(dropped) + len(_fb_dropped),
        "dropped_names": [str(r.get("name") or "") for r in dropped],
        "first_board_dropped": [str(r.get("name") or "") for r in _fb_dropped],
        "first_board_min_carry": first_board_min_carry(),
        "groups": group_diag,
        "theme_missing": len(theme_missing),
        # 🎯 2026-11-11 用户 A：炸板 >5 不进池
        "max_zbc": _zbc_cap,
        "zbc_dropped": zbc_dropped,
    }
    return new_relay, new_watch, diag
