#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""涨停股全量过筛（2026-11-04 用户定：每天把涨停股全部过一遍）。

## 用户原话

> 「我们其实每天要把涨停的个股全部过一遍，尽量过二连板以上的票。首板其实也能过，
>   但我们尽量还是看二板以上的票权重稍微高一点，看的时候就过一下这些股性还有逻辑。
>   基于你这个逻辑的话，这个"紫竹"根本就不会进入我们系统的范围里面，直接就 pass 掉了。」

## 为什么需要这一层（当前的病）

现有链路是「**先算分选优，再用风控兜底**」：

```
涨停池 48 → _score_all 45 → 四档资格 → 准入分线 → seal_gate → 各闸 → 最终 7 只
```

问题有三：

1. **没有任何地方输出「这 48 只各自的结论」**。紫竹进没进池，取决于它算出来 66 分
   排在中游、恰好落进 `relaxed` 兜底档 —— 而不是「过筛时被看穿」。
2. 一旦候选文件冻结，09:25 **只读不重算**：11-03 15:36 用旧门槛生成的候选，
   里面紫竹 `seal_gate.source=enough / floor_used=1.5` 是**死快照**，
   后来改代码改不动它。当天纯靠「情绪」闸碰巧拦下；**换成情绪好的日子，它就占真名额了**。
3. 「股性」字段在候选里**大面积为 None**（实测 11-03 紫竹 `promo_rate_hist`/`zt_250`/
   `zt_total` 全空），而 `cache/zt_genes/hist_002058.json` 里明明有 —— 数据没回填。

## 本模块做什么

**不选优、不排名、不给分**。对全量涨停股逐票给一个**可核查的结论 + 理由链**：

| verdict | 含义 |
|---|---|
| `pass` | 股性、逻辑、量能、身位都没硬伤 → 可进后续候选流程 |
| `watch_only` | 只能看不能买（一字厚封买不到 / 首板无带动 / 缺关键数据） |
| `reject` | 有明确硬伤 → **直接 pass，不进池** |

**板数只决定「看多细」，不直接决定 pass/reject**（用户「二板以上权重高一点」）：
≥2 板逐条核查全部维度；1 板额外要求「带动 ≥3 或成线」才算过。

## 判据全部复用单一源（⛔ 不重造）

| 维度 | 来源 |
|---|---|
| 封板形态 / 成交额分档 | `core.seal_gate.board_style / style_floor / seal_profile` |
| 股性 | `core.zt_gene.gene_facts / gene_grade` |
| 题材成线 | 本模块按 `core_theme` 计数（与 `pool_admit` 同口径） |

⛔ **缺数据不判**：任一关键字段缺失 → 该条理由不下结论（写进 `unknown`），
   既不没收也不放宽。⛔ 绝不用「评分低」当 reject 理由。

## 边界

- **只读**：不写候选、不改排序、不进裁决、不推送。产物落 `cache/daily_screen/`。
- 本模块**不替代** `pool_admit`/`pool_call`；它回答「这 48 只各是什么结论」，
  那两层回答「今天买哪只」。两者可以并存，也可以让它前置收口（待用户拍板）。
- `ENABLE_DAILY_SCREEN=0` 关闭。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "daily_screen_v1"

ENABLED_ENV = "ENABLE_DAILY_SCREEN"

# 首板要求的最低带动家数（与 `pool_admit.FIRST_BOARD_MIN_CARRY_DEFAULT` 同值）
FIRST_BOARD_MIN_CARRY = 3
# 同题材至少几只才算「成线」
MIN_LINE_MEMBERS = 3
# 封板质量分低于此值视为「封得很差」（seal_gate 满分 10）
WEAK_SEAL_SCORE = 2

_THEME_EMPTY = {"", "—", "-", "其他", "无", "未知", "none", "null"}

# ⛔ 字段名映射：涨停池有**三种**来源格式，不归一化会静默失效。
#    `pool_admit._norm` 的注释已经警告过同一个坑（「涨停池读到 54 只、却全部匹配不上」），
#    2026-11-04 我又踩了一次：`pipeline_eod` 传的是东财原始 `ztgc`（`{dm, mc, lbc}`），
#    而本模块按 `{code, name, boards}` 读 → `zt_count` 恒为 0，前置闸**上线即空转**。
#
#    · `zt_history/zt_{date}.json` 的 `zt_list`：`{code, name, boards, seal, lt_yi}`
#    · `load_zt_pool()` 的 `ztgc`（东财原始）：`{dm, mc, lbc, zj, lt, cje, hy}`
#      单位：`zj`(封单额) / `lt`(流通市值) / `cje`(成交额) **全部为亿**，
#      与 `pipeline_eod._score_all` 的用法一致（`cje_yi=_to_float(s.get("cje"))` 未再除 1e8）。
_FIELD_ALIASES = {
    "code": ("dm",),
    "name": ("mc",),
    "boards": ("lbc",),
    "cje_yi": ("cje",),
    "seal_amt": ("seal_amt", "zj", "seal"),
    "lt_mktcap": ("lt_mktcap", "lt", "lt_yi"),
    "sector": ("hy",),
}


def _coerce_fields(row: dict[str, Any]) -> dict[str, Any]:
    """把三种来源形状补齐成本模块认的形状（⛔ 不覆盖已有值，不改原 dict）。"""
    out = dict(row)
    for dst, srcs in _FIELD_ALIASES.items():
        if out.get(dst) is not None:
            continue
        for s in srcs:
            if out.get(s) is not None:
                out[dst] = out[s]
                break
    return out


def _is_one_word_from_kline(code: str, date: str, root: Path | None = None) -> bool | None:
    """用前复权日K严格判一字板：当日 开=高=低=收（全天无波动）。

    ⛔ `zt_history` **没有** `is_one_word` 字段，只能从日K反推。
    ⛔ 缺K线 / 缺当日 / 四价缺失 → None（不猜，不拿「竞价即封」当一字——
       竞价封也可能开盘后被砸开）。
    """
    base = Path(root) if root else Path(__file__).resolve().parents[2]
    p = base / "cache" / "kline_qfq" / f"{code}.json"
    if not p.exists():
        return None
    try:
        bars = json.loads(p.read_text(encoding="utf-8")).get("bars") or []
    except Exception:
        return None
    for b in bars:
        if str(b.get("date") or "")[:10] != date[:10]:
            continue
        o, h, l, c = (b.get("open"), b.get("high"), b.get("low"), b.get("close"))
        if None in (o, h, l, c):
            return None
        return abs(h - l) < 1e-9 and abs(o - c) < 1e-9
    return None



def enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "1") not in ("0", "false", "False", "")


# 📏 2026-11-06 用户 A：身位从「过筛硬伤」降级为「09:25 降权」。
#   默认 1（只写事实、不 reject）；`=0` 逐字回退到旧硬拒（全市场最高板口径）。
POS_SCREEN_FACT_ONLY = os.environ.get("ENABLE_SCREEN_POSITION_GAP", "1") != "0"

def _num(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _code(row: dict) -> str:
    return str((row or {}).get("code") or "").strip()


def _theme(row: dict) -> str:
    for key in ("core_theme", "theme", "sector"):
        v = str((row or {}).get(key) or "").strip()
        if v and v.lower() not in _THEME_EMPTY:
            return v
    return ""


def screen_one(row: dict[str, Any], *, market_max_boards: int | None,
               theme_members: dict[str, int], gene_cache: Path | None = None,
               trade_date: str = "", root: Path | None = None,
               theme_max_boards: dict[str, int] | None = None) -> dict[str, Any]:
    """单票过筛 → verdict + 理由链。

    ⛔ 顺序有意义：先硬伤（reject），再「只能看」（watch_only），最后才是 pass。
       「能看但不能买」优先于「有硬伤」——因为一字厚封的票不是坏票，只是买不到。
    """
    from core import seal_gate as _sg
    from core import zt_gene as _gene

    row = _coerce_fields(row)          # ⛔ zt_history 字段名 → seal_gate 口径
    code = _code(row)
    name = str(row.get("name") or "")
    boards = int(_num(row.get("boards")) or 0)
    theme = _theme(row)
    mates = theme_members.get(theme, 0) if theme else 0

    # 一字形态：zt_history 无此字段 → 日K反推（缺K线则留 None，走 unknown）
    if row.get("is_one_word") is None and trade_date:
        yz = _is_one_word_from_kline(code, trade_date, root)
        if yz is not None:
            row["is_one_word"] = yz

    rejects: list[str] = []
    watch: list[str] = []
    supports: list[str] = []
    unknown: list[str] = []

    # ── 0) 板块与权限（硬性，与既有口径一致）──────────────────────────────
    if code.startswith("688"):
        rejects.append("科创板无权限")
    if "ST" in name.upper():
        rejects.append("ST 标的")

    # ── 1) 封板形态与成交额（2026-11-04 用户「换手板成交额低=做局」）────────
    style = _sg.board_style(row)
    prof = _sg.seal_profile(row)
    grade = prof.get("grade")
    if style == "unknown":
        unknown.append("封板形态缺失（is_one_word 非布尔）")
    if grade == "unknown":
        unknown.append("封板质量字段缺失：" + ",".join(prof.get("missing") or []))

    # 门槛按形态分档：一字板原门槛（量小是封死的结构必然），换手板 1~2 板提到 5 亿
    # 🔴 2026-11-11 用户「5 亿的限制全系统清除」→ 成交额不再产生 reject。
    #    保留 `_sg.style_floor` 调用只为拿到「本档生效门槛」用于留痕；
    #    成交额偏小一律降级为**提示**（supports/unknown），⛔ 不再写进 rejects。
    base_floor = 1.5 if boards <= 2 else 5.0
    floor = _sg.style_floor(row, base_floor)
    cje = _num(row.get("cje_yi"))
    if cje is None:
        unknown.append("成交额缺失")
    elif cje < base_floor:
        # 只记事实：量小本身不是缺陷（缩量封板＝筹码锁死），⛔ 不复核、不否决
        supports.append(f"成交额 {cje:.2f}亿（{'换手板' if style == 'turnover' else '一字/未知'}·"
                        f"量小不构成缺陷，已不作门槛）")

    # ── 2) 封板质量 ────────────────────────────────────────────────────────
    # ⛔ 2026-11-04 重大修正：**封板弱不能当 reject 理由**。
    #   实测（32 配对日）按拒因看可成交口径：
    #     封板弱  n=242 开收 **+0.15%** 胜率 **49.6%**  ← 比被拒整体（41.4%）高 8pp
    #     换手板量能 n=366 开收 −0.90% 胜率 37.2%
    #   原因正是老结论：**封板强 → 高开多 → 买不到；封板弱 → 能买到，买入后反而有溢价。**
    #   把封板弱当硬伤，等于系统性地把「买得到、且赚」的那批扔掉。
    #   → 降级为**提示**（写进 facts 与 supports/notes），不参与 reject。
    score = prof.get("score")
    if isinstance(score, int):
        if score >= 6:
            supports.append(f"封板强（{score}/10：" + "·".join(prof.get("reasons") or []) + "）")
        elif score <= WEAK_SEAL_SCORE:
            fbt = str(row.get("fbt") or "")
            hm = f"{fbt[:2]}:{fbt[2:4]}" if len(fbt) >= 4 else "—"
            # 只记事实，不判死
            unknown.append(f"封板偏弱（{score}/10）：首封 {hm}、封单占流通 "
                           f"{prof.get('seal_ratio_pct')}%、炸板 {row.get('zbc')} 次"
                           f"（不据此否决——封板弱通常更好买到）")

    # ── 3) 板块效应 ────────────────────────────────────────────────────────
    if not theme:
        unknown.append("题材缺失（不判定是否成线）")
    elif mates < MIN_LINE_MEMBERS:
        if boards >= 4:
            # ⛔ 2026-11-04 修正：原写「独苗高标按独立龙头看待」= 直接放行，
            #    实测（32 配对日 / ≥3 板 142 样本，可成交口径）证明这是错的：
            #      · 独苗 ≥4 板  n=32  可成交 5  开收 −1.17%  胜率 **20.0%**
            #      · 独苗 3 板   n=52  可成交 8  开收 +1.31%  胜率 62.5%
            #      · 成线 ≥3 家  n=58  可成交 12 开收 +1.26%  胜率 **75.0%**
            #    ⇒ 同题材一家都没有的 ≥4 板，是「没有板块支撑的高位孤军」，
            #      次日高开过 5% 的占 50%（开盘溢价被吃掉），能买到的多半是砸盘位。
            #      ⛔ 但也不删：3 板独苗胜率 62.5%，是「高」而不是「独苗」不行。
            #      ⇒ 只把 ≥4 板卡在「只能看」，让它不占名额、进不了 relay。
            watch.append(f"独苗高标（{boards} 板·同题材 {mates} 家）"
                         f"—— 高位无板块支撑，可成交样本胜率仅 20%，只观察不接")
        elif boards == 3:
            supports.append(f"独苗高标（3 板），该档可成交胜率 62.5%，按独立龙头看待")
        else:
            rejects.append(f"同题材仅 {mates} 家，未成线（低板不接孤军）")
    else:
        supports.append(f"同题材 {mates} 家，成线")

    # ── 4) 身位（2026-11-06 用户 A 重构：分母改同题材 + 不再当硬伤）──────────────
    # 用户原话：「身位差我好像没有强制要求百分之百限制吧……你传媒板块你已经涨了五板龙头了，
    #   但是光伏板块才一个板，这种身位差其实是没有影响的呀，它是走一个新的逻辑，
    #   甚至说可以做一个资金的承接方向，你这种身位差估计只能在同一个板块里面适用，
    #   不同板块之间它是没有关系的」
    #
    # ⛔ 旧口径（全市场最高板 − 本票板数 ≥2 → reject）有三个问题：
    #    ① 分母错：跨题材没有板数传导关系；
    #    ② 依据过期：拒因写「次日高开>5% 买不到」，而 11-05 用户已删掉 5% 那条线，
    #       且该依据的样本本身就是按 `0<gap≤5%` 划出来的（用已废的线定义样本）；
    #    ③ **职责错位**：身位回答的是「该买多重」，不是「值不值得看」。放进过筛
    #       就会把票在盘后就判死，09:25 的降权逻辑永远没机会生效 → 双轨。
    #    35 日 717 真题材可买票次实测：旧闸保留批 −1.05%/胜42.4%，
    #    剔除批 −0.93%/胜41.8%（**剔除的反而更好**），零区分度。
    #
    # ⇒ 现在只写**事实**（basis / line_gap / market_gap），由 09:25
    #    `ai_caliber.position_weight` 统一降权。`ENABLE_SCREEN_POSITION_GAP=0`
    #    回退旧硬拒（逐字恢复 11-06 之前行为）。
    _p_facts: dict[str, Any] = {}
    if POS_SCREEN_FACT_ONLY:
        _p_tmax = (theme_max_boards or {}).get(theme) if theme else None
        _p_facts = {
            "market_max_boards": market_max_boards,
            "theme_max_boards": _p_tmax,
            "line_gap": (int(_p_tmax) - boards) if _p_tmax and boards else None,
            "market_gap": (int(market_max_boards) - boards)
                          if market_max_boards and boards else None,
        }
        if _p_facts["line_gap"] is not None:
            _p_facts["basis"] = "line"
            _lg = _p_facts["line_gap"]
            if _lg == 0:
                supports.append(f"同题材里它就是最高板（{boards} 板）——自己当锚，"
                                f"该档可成交 T+1 反而偏弱，买则轻仓")
            elif _lg == 1:
                supports.append(f"同题材身位差 1 板（最快的一档：T+1 +0.52%/胜 53.4%）")
            else:
                unknown.append(f"同题材身位差 {_lg} 板（该档 T+1 −1.19%~−1.89%、"
                               f"胜率 35~40%）—— 不据此否决，09:25 只降仓")
        else:
            _p_facts["basis"] = "unknown"
            unknown.append("题材最高板缺失（不判身位，不据此否决）")
    elif boards >= 2:
        if market_max_boards and boards:
            gap = int(market_max_boards) - boards
            if gap >= 2:
                # ⚠️ 措辞修正（2026-11-04）：实测这一档**晋级率 50.0%（n=52）**，
                #    是全组最高，不能说它「弱」。真正的问题是**开盘溢价被吃掉**：
                #    可成交仅 6 只、高开>5% 占 53.8%、能成交的开收 −3.19%。
                # ⚠️ 2026-11-06 再修：「买不到」这个归因**是错的**——5% 线 11-05 已废，
                #    买不到只剩竞价封死。而且这条拒因本身已经整体停用
                #    （`POS_SCREEN_FACT_ONLY=1` 走上面的事实层），这里只保留回退路径。
                rejects.append(f"身位差 {gap} 板（全市场最高 {market_max_boards} 板）"
                               f"—— 晋级率不低，但次日高开过 5% 的占一半以上、开盘溢价被吃掉")
            else:
                supports.append(f"身位在前排（差 {gap} 板）")
        else:
            unknown.append("市场最高板缺失（不判身位）")

    # ── 5) 股性（用户「过一下这些股性」）──────────────────────────────────
    gf: dict[str, Any] = {}
    try:
        gf = _gene.gene_facts(code, gene_cache) or {}
    except Exception:
        gf = {}
    g_grade = str(gf.get("grade") or "")
    if not gf:
        unknown.append("股性缓存缺失")
    elif not g_grade:
        unknown.append("股性晋级率缺失")
    else:
        pr = gf.get("promo_rate_hist")
        zt250 = gf.get("zt_250")
        if g_grade.startswith("连板龙"):
            supports.append(f"股性 {g_grade}（晋级 {pr}%·近一年 {zt250} 次）")
        elif g_grade == "难连板":
            rejects.append(f"股性难连板（晋级 {pr}%·近一年 {zt250} 次）")
        else:
            supports.append(f"股性 {g_grade}（晋级 {pr}%·近一年 {zt250} 次）")

    # ── 6) 首板额外要求（用户「首板也能过，但权重低」）────────────────────
    carry = int(_num(row.get("carry_cnt")) or 0)
    if boards <= 1 and not code.startswith("688"):
        if carry < FIRST_BOARD_MIN_CARRY and mates < MIN_LINE_MEMBERS:
            watch.append(f"首板带动 {carry} 家 <{FIRST_BOARD_MIN_CARRY} 且未成线，只看不买")

    # ── 7) 一字厚封：买不到，但可作为情绪参照 ──────────────────────────────
    if style == "yizi":
        ratio = prof.get("seal_ratio_pct")
        if isinstance(ratio, (int, float)) and ratio >= _sg.THICK_SEAL_EXEMPT_PCT:
            watch.append(f"一字封死、封单占流通 {ratio:.1f}%，买不到（作情绪参照）")

    # ── 结论 ──────────────────────────────────────────────────────────────
    if rejects:
        verdict = "reject"
    elif watch:
        verdict = "watch_only"
    else:
        verdict = "pass"

    return {
        "code": code, "name": name, "boards": boards, "theme": theme,
        "theme_members": mates, "verdict": verdict,
        "rejects": rejects, "watch_reasons": watch,
        "supports": supports, "unknown": unknown,
        # 板数只决定「看多细」：≥2 板是主看对象
        "priority": 2 if boards >= 2 else 1,
        "facts": {
            "style": style, "seal_grade": grade, "seal_score": score,
            "seal_ratio_pct": prof.get("seal_ratio_pct"),
            "cje_yi": cje, "floor_yi": floor, "hs": _num(row.get("hs")),
            "fbt": row.get("fbt"), "zbc": row.get("zbc"),
            "carry_cnt": carry, "gene_grade": g_grade or None,
            "promo_rate_hist": gf.get("promo_rate_hist"),
            "zt_250": gf.get("zt_250"),
            # 📏 身位事实（09:25 `ai_caliber.position_weight` 消费；⛔ 不是裁决）
            "position_gap": (_p_facts if POS_SCREEN_FACT_ONLY else None),
        },
    }


def screen_pool(zt_rows: Iterable[dict[str, Any]], *,
                gene_cache: Path | None = None,
                trade_date: str = "", root: Path | None = None) -> dict[str, Any]:
    """全量过筛。输入＝当日**完整**涨停池（`zt_history/zt_{date}.json` 的 `zt_list`）。

    ⛔ 必须是全量，不是候选池 —— 候选池只剩 7 只，过筛就失去意义。
    ⛔ 入口先做字段归一化：调用方可能传 `zt_list`（code/name/boards）
       也可能传东财原始 `ztgc`（dm/mc/lbc）。不归一化时 `_code()` 全空 →
       `zt_count=0` → 调用方把所有候选当作「不在涨停池」放行，闸门静默失效。
    """
    rows: list[dict[str, Any]] = []
    for _r in (zt_rows or []):
        if not isinstance(_r, dict):
            continue
        _c = _coerce_fields(_r)
        if len(_code(_c)) == 6:
            rows.append(_c)
    theme_members: dict[str, int] = {}
    for r in rows:
        t = _theme(r)
        if t:
            theme_members[t] = theme_members.get(t, 0) + 1
    # 📏 同题材最高板（身位的新分母·11-06 用户 A）：只认炒作题材，行业兜底也照算但
    #    `position_weight` 在 pool_call 层会用同一口径复核；此处只提供事实。
    theme_max_boards: dict[str, int] = {}
    for r in rows:
        t = _theme(r)
        b = int(_num(r.get("boards")) or 0)
        if t and b > theme_max_boards.get(t, 0):
            theme_max_boards[t] = b
    boards_all = [int(_num(r.get("boards")) or 0) for r in rows]
    max_boards = max(boards_all) if boards_all else None

    screened = [screen_one(r, market_max_boards=max_boards,
                           theme_members=theme_members, gene_cache=gene_cache,
                           trade_date=trade_date, root=root,
                           theme_max_boards=theme_max_boards)
                for r in rows]
    # 排序：先按 verdict（reject 沉底），再按板数，再按代码（稳定）
    order = {"pass": 0, "watch_only": 1, "reject": 2}
    screened.sort(key=lambda x: (order.get(x["verdict"], 9), -x["boards"], x["code"]))

    counts = {"pass": 0, "watch_only": 0, "reject": 0}
    for s in screened:
        counts[s["verdict"]] = counts.get(s["verdict"], 0) + 1
    reasons: dict[str, int] = {}
    for s in screened:
        for r in s["rejects"]:
            key = r.split("（")[0].split(" <")[0][:24]
            reasons[key] = reasons.get(key, 0) + 1

    return {
        "schema": SCHEMA,
        # ⛔ 必须带日期：`persist` 用 `result["date"]` 拼文件名，
        #    缺失时会写出 `screen_None.json`（前端按 `screen_<date>` 匹配 → 永远读不到）。
        "date": str(trade_date or "")[:10],
        "zt_count": len(rows),
        "market_max_boards": max_boards,
        "counts": counts,
        "reject_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "screened": screened,
    }


def build_from_files(root: Path, date: str, *, gene_cache: Path | None = None) -> dict[str, Any] | None:
    """从 `cache/zt_history/zt_<date>.json` 读全量涨停池并过筛。缺失 → None（不编造）。"""
    p = Path(root) / "cache" / "zt_history" / f"zt_{date}.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    rows = raw.get("zt_list") or []
    if not rows:
        return None                      # 节假日占位 / 未归档 → 不产结论
    d10 = str(raw.get("date") or date)[:10]
    out = screen_pool(rows, gene_cache=gene_cache or (Path(root) / "cache" / "zt_genes"),
                      trade_date=d10, root=Path(root))
    out["date"] = d10
    return out


def persist(root: Path, result: dict[str, Any]) -> Path:
    """落 `cache/daily_screen/screen_<date>.json`。⛔ 只写自己目录。

    ⛔ 没有 `date` 时**拒绝落盘**并抛错 —— 宁可让调用方看到异常，
       也不能写出 `screen_None.json` 这种永远匹配不上的幽灵文件。
    """
    date = str((result or {}).get("date") or "")[:10]
    if not date:
        raise ValueError("daily_screen.persist: result 缺 date，拒绝写出无法匹配的文件")
    d = Path(root) / "cache" / "daily_screen"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"screen_{date}.json"
    p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# ══════════════════════════════════════════════════════════════════════════
# 🔁 跨日追踪：今天被拒、明天又涨停 —— 「总会被我们发现」
# ══════════════════════════════════════════════════════════════════════════
# 用户原话：「今天哪怕它离开了，没有进入我们的池子，但是明天如果它还涨停了，
#   我们就会对它进行进一步的观察和判断，总会被我们发现的。」
#
# 这是本套逻辑**成立的最后一环**：过筛是「今天不选」，不是「永久封杀」。
#   被拒的票明天若再涨停 → 说明它仍在晋级，必须重新评估，不能被昨天的结论连坐。
# ⛔ 本函数只回答「昨天被我拒掉的那些，今天怎么样了」，不生成买卖判断。
def reject_followup(root: Path, date: str, *,
                    prev_date: str | None = None,
                    gene_cache: Path | None = None) -> dict[str, Any]:
    """对上一份过筛里 `reject` 的票，回看今日是否仍涨停 / 是否晋级。

    返回 `{prev_date, date, watched: [...], still_zt: [...], promoted: [...]}`。
    ⛔ 缺上一日产物 → 返回 `{"available": False}`，不编造。
    """
    base = Path(root) / "cache" / "daily_screen"
    prevs = sorted(base.glob("screen_*.json")) if base.exists() else []
    prevs = [p for p in prevs if p.stem[7:] < date[:10]]
    if prev_date:
        prevs = [p for p in prevs if p.stem[7:] == prev_date[:10]]
    if not prevs:
        return {"available": False, "reason": "无上一日过筛产物"}
    prev_path = prevs[-1]
    try:
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "reason": "上一日过筛产物不可读"}

    rejected = [s for s in (prev.get("screened") or []) if s.get("verdict") == "reject"]
    if not rejected:
        return {"available": True, "prev_date": prev.get("date"), "date": date,
                "rejected_count": 0, "still_zt": [], "promoted": []}

    # 今日涨停池
    today_p = Path(root) / "cache" / "zt_history" / f"zt_{date[:10]}.json"
    if not today_p.exists():
        return {"available": False, "reason": f"今日涨停池缺失（{date}）"}
    try:
        today_rows = json.loads(today_p.read_text(encoding="utf-8")).get("zt_list") or []
    except Exception:
        return {"available": False, "reason": "今日涨停池不可读"}
    today_by = {str(r.get("code") or "").strip(): r for r in today_rows}

    still, promoted = [], []
    for s in rejected:
        code = str(s.get("code") or "")
        now = today_by.get(code)
        if not now:
            continue
        now_boards = int(_num(now.get("boards")) or 0)
        item = {"code": code, "name": s.get("name"),
                "prev_boards": s.get("boards"), "now_boards": now_boards,
                "prev_rejects": list(s.get("rejects") or [])}
        still.append(item)
        if now_boards > int(s.get("boards") or 0):
            promoted.append(item)

    return {
        "available": True,
        "prev_date": prev.get("date"), "date": date[:10],
        "rejected_count": len(rejected),
        "still_zt": still,
        "promoted": promoted,
        # ⛔ 一句人话，说明「它没被永久封杀」
        "note": (f"昨日拒掉 {len(rejected)} 只，今日仍有 {len(still)} 只涨停"
                 f"（其中 {len(promoted)} 只晋级）—— 已被重新纳入今日过筛，不是黑名单"
                 if still else f"昨日拒掉 {len(rejected)} 只，今日无一涨停"),
    }

