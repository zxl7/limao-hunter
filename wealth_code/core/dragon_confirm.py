#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙头资格认定（2026-10-21 P2·打通「身份」与「确认尺」）。

## 为什么要有这个模块（问题在哪）
系统里关于「谁是龙头」有三套彼此独立的东西，从来没串起来过：

| 来源 | 判什么 | 输出 |
|---|---|---|
| `expectation_ai.dragon_three_diagnosis` | 带领性 / 突破性 / 唯一性 三要素打分 | 真龙头(主升) / 准龙头 / 跟风 |
| `leader_cycle.annotate` | 五维证据（竞争/板块跟随/分歧/启动/催化）+ 连续性 | 龙头 / 准龙头 / 跟风 + confirmed/pending |
| `anchor_call.fbt_acceleration` / `volume_check` | 92科比两把尺（时间越走越早 / 先爆量再缩量） | accelerating / passed |

结果：卡面上一只票到底算不算龙头，取决于「哪套先跑」；两把尺算出来却没人用。

## 本模块做什么
把它合成**一个认定结论 + 逐项证据 + 一句人话**，供 `leader_cycle` 与卡面共用。

## ⛔ 铁律：两把尺是「叙述层」，不是「资格层」
用户 2026-09-24 已拍板「地位优先 + 五维确认」＝龙头资格的**唯一判据**。本模块：

- 五维 confirmed **但两把尺都没过** → **保持龙头身份，不降级**，只标注
  「没经历过爆量考验，这种容易一波流」。理由：降级会推翻用户已拍板的规则；
  而只标注不降级，信息照样传达到卡面。
- 五维 pending **但两把尺都过** → **仍不升龙头**，只标注
  「量能形态像龙头，就差最后一步确认」。理由：两把尺只是形态，不构成身份。
- 两把尺只在**叙述**里改变措辞，绝不改变 `identity` 字段本身。

## 数据来源
`series` 由调用方提供（`leader_cycle.annotate` 从 `load_history` 里取），
本模块**不读盘**：纯计算、可离线复现、可单测。
"""
from __future__ import annotations

from typing import Any

from core import anchor_call as _ac

# 认定等级（叙述强度，从强到弱）
LEVEL_CONFIRMED = "confirmed"      # 五维确认 + 两把尺都过 → 最硬的龙头
LEVEL_CONFIRMED_THIN = "confirmed_thin"  # 五维确认但两把尺没全过 → 是龙头，但没被检验过
LEVEL_PARTIAL = "partial"          # 五维未确认，仅形态像（两把尺过）
LEVEL_PENDING = "pending"          # 五维未确认、形态也没过
LEVEL_NOT = "not_dragon"           # 数据不足或明确不是

LEVEL_LABEL = {
    LEVEL_CONFIRMED: "龙头",
    LEVEL_CONFIRMED_THIN: "龙头（未经验证）",
    LEVEL_PARTIAL: "准龙头（形态像）",
    LEVEL_PENDING: "准龙头",
    LEVEL_NOT: "跟风",
}

ENABLED = True

# ── 「只差催化」的替代确认路径（2026-10-21 用户拍板开启·A 方案）─────────────────
# 背景（实测）：五维确认的 `catalyst == "sustained_verified"` 要求
#   `kind ∈ (policy/order/industry_event/technology)` **且** `verified == true`，
# 但 `cache/newsflash_history/daily_*.json` 目前**全部 `kind=None`、`verified=None`**
#   → catalyst 维永远是 unknown → `confirmed` 实际不可达 → 卡面「龙头」标签是死代码。
#
# 用户 2026-10-21 14:59 拍板 **A 方案**：开启替代路径，同时把「补新闻源 kind/verified」
# 排进计划（治本）。本常量即该开关：
#   · 只差催化时也算 confirmed，但措辞必须写「只差硬催化没证实」；
#   · 环境变量 `DRAGON_CONFIRM_NO_CATALYST=0` 可临时关掉（自动化不设环境变量，故默认开）。
#
# ⛔ 条件设计（2026-10-21 修正过一次，必须记住）：
#   判据**严格等于** `leader_cycle.annotate` 里原文的 confirmed 五条，只把其中
#   `catalyst == "sustained_verified"` 换成 `catalyst == "unknown"`。
#   ⚠️ 首版我额外加了「competition ∈ (market_only, theme_winner)」和「两把尺全过」，
#      实测**永远 0 只命中**——原文 confirmed 根本不看 competition（那是外层提 role 的另一条），
#      而两把尺是叙述层。多加条件＝把这条路径写成死代码，还会让人误以为「放宽了」。
#   ⛔ 明确「催化有但是不够」（single_verified / unverified_news）**不算 unknown**，不放行。
ALLOW_NO_CATALYST = True
ALLOW_NO_CATALYST_ENV = "DRAGON_CONFIRM_NO_CATALYST"


def _allow_no_catalyst() -> bool:
    """替代路径是否生效：模块开关 ∧ 环境变量未显式关闭。"""
    import os
    v = str(os.environ.get(ALLOW_NO_CATALYST_ENV, "")).strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return bool(ALLOW_NO_CATALYST)


def _num(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def series_of(history: list[dict[str, Any]], code: str, asof: str, *,
              is_adjacent: Any = None) -> list[dict[str, Any]]:
    """取某只票**本轮连续涨停**的日序列（从 asof 往前，遇到没涨停即停）。

    ⚠️ 必须「连续」：不连续说明中间断过板，那是另一波行情，
    把断板前的数据混进来会让「时间加速」和「爆量检验」全部失真。

    ⚠️ **缺文件也要算断**（2026-10-21 实测踩坑）：`zt_history` 可能整天缺失
    （采集失败/节假日），此时不能因为「那天不在 history 里」就跳过去当成连续。
    `is_adjacent(a, b)` 注入交易日相邻判定；缺省走 `common.trading_calendar`。

    返回 [{date, fbt, cje_yi, boards, zbc}]，按日期**正序**（早→晚），最新一天在最后。
    """
    code = str(code or "").strip()
    if not code or not asof:
        return []
    adj = is_adjacent or _default_adjacent
    days = [d for d in (history or []) if isinstance(d, dict) and str(d.get("date") or "") <= asof]
    days.sort(key=lambda d: str(d.get("date") or ""))
    out: list[dict[str, Any]] = []
    prev_day: str | None = None
    for d in reversed(days):
        day = str(d.get("date") or "")
        if prev_day is not None and not adj(day, prev_day):
            break                      # 中间隔着交易日却没数据 → 视为断板（不猜）
        hit = None
        for r in (d.get("rows") or []):
            if str(r.get("code") or "").strip() == code:
                hit = r
                break
        if hit is None:
            break                      # 断了 → 本轮结束
        out.append({"date": day, "fbt": hit.get("fbt"), "cje_yi": hit.get("cje_yi"),
                    "boards": hit.get("boards") or hit.get("lbc"), "zbc": hit.get("zbc")})
        prev_day = day
    out.reverse()
    return out


def _default_adjacent(a: str, b: str) -> bool:
    """a、b 之间**没有夹着交易日**（＝相邻，可以是同一日）。

    ⛔ 走 `common.trading_calendar` 唯一真源：用 weekday 判会把「工作日休市」
       （中秋/国庆调休）错当成交易日，隔着假期的两次涨停会被误连成一波。
    """
    a, b = str(a or "")[:10], str(b or "")[:10]
    if not a or not b:
        return False
    if a == b:
        return True
    lo, hi = (a, b) if a < b else (b, a)
    try:
        import datetime as _dt
        import sys
        from pathlib import Path as _P
        _root = str(_P(__file__).resolve().parents[2])
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from common.trading_calendar import is_a_share_trading_day
        cur = _dt.date.fromisoformat(lo) + _dt.timedelta(days=1)
        end = _dt.date.fromisoformat(hi)
        while cur < end:
            if is_a_share_trading_day(cur.isoformat()):
                return False           # 中间夹着交易日 → 不相邻
            cur += _dt.timedelta(days=1)
        return True
    except Exception:                  # 日历不可用 → 保守判「不相邻」（宁可少算一波）
        return False


def confirm(row: dict[str, Any], series: list[dict[str, Any]] | None = None, *,
            enabled: bool | None = None) -> dict[str, Any]:
    """龙头资格认定。

    row: 候选行（需 `leader_evidence.confirmation` / `carry_cnt` / `boards`）
    series: `series_of()` 的结果（缺则两把尺判 unknown，⛔ 不猜）
    """
    on = ENABLED if enabled is None else bool(enabled)
    if not on:
        return {"enabled": False, "level": "", "label": "",
                "identity_role": "", "checks": [], "narrative": ""}

    ev = row.get("leader_evidence") or {}
    five = str(ev.get("confirmation") or "").strip()      # confirmed / pending / ""
    carry = _num(row.get("carry_cnt")) or 0.0
    boards = _num(row.get("boards")) or 0.0
    seq = list(series or [])

    # ── 两把尺 ──────────────────────────────────────────────────
    accel = _ac.fbt_acceleration([(x.get("date"), x.get("fbt")) for x in seq])
    vol = _ac.volume_check([(x.get("date"), x.get("cje_yi")) for x in seq])

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool | None, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    # ① 五维确认（唯一资格判据）
    if five == "confirmed":
        add("五维确认", True, "竞争、板块跟随、分歧、启动、催化五项都过")
    elif five == "pending":
        add("五维确认", False, "五项证据还没凑齐")
    else:
        add("五维确认", None, "没有逐日证据，判不了")

    # ② 带动性
    add("带动", carry > 0,
        f"封板后带起 {int(carry)} 家同向涨停" if carry > 0 else "封板后没有带起同向涨停")

    # ③ 连续性（≥3 天才够格，这是既有规则）
    cd = _num(ev.get("continuous_days")) or 0.0
    add("连续性", cd >= 3 if cd else None,
        f"连续 {int(cd)} 个交易日涨停" if cd else "连续性数据缺失")

    # ④ 时间加速（92科比第一把尺）
    add("涨停时间越走越早", (accel["trend"] == "accelerating") if accel["trend"] != "unknown" else None,
        accel.get("detail") or "")

    # ⑤ 爆量检验（92科比第二把尺）
    add("爆量检验", vol.get("passed"), vol.get("detail") or "")

    # ── 合成等级 ────────────────────────────────────────────────
    scale_ok = (accel["trend"] == "accelerating") and (vol.get("passed") is True)
    scale_any = (accel["trend"] == "accelerating") or (vol.get("passed") is True)

    # 「只差催化」：数据缺口补偿（⛔ 判据严格等于原文 confirmed，只换催化一条）
    only_catalyst_missing = _only_catalyst_missing(row, ev, scale_ok, carry, cd)
    via_relief = False
    if five != "confirmed" and only_catalyst_missing:
        five = "confirmed"
        via_relief = True
        checks[0] = {"name": "五维确认", "passed": True,
                     "detail": "带动、连续、板块跟随、分歧、启动都过；催化这一项目前拿不到证据"
                               "（新闻源还没有分类/核实字段）"}

    if five == "confirmed":
        level = LEVEL_CONFIRMED if scale_ok else LEVEL_CONFIRMED_THIN
    elif five == "pending":
        if scale_ok:
            level = LEVEL_PARTIAL          # 形态全对，只差五维 → 仍不升龙头
        else:
            level = LEVEL_PENDING
    else:
        # 无逐日证据：回落到三要素身份（既有 identity 字段），本模块不越权认定
        ident = str(((row.get("expectation") or {}).get("dragon_diagnosis") or {})
                    .get("identity") or "")
        if ident.startswith("真龙头"):
            level = LEVEL_CONFIRMED_THIN
        elif ident == "准龙头":
            level = LEVEL_PENDING
        else:
            level = LEVEL_NOT

    # ── 一句人话（卡面/理由用）────────────────────────────────────
    # ⛔ 两把尺的信息**必须进叙述**：否则「时间在加速但爆量没消化」和
    #   「量能符合但时间没提前」会写出同一句话，读者拿不到任何可用信息
    #   （2026-10-21 真实数据实测发现：吉大正元 vs 大业股份 结论完全同形，
    #    但一个在加速、一个量能合格，是两种完全不同的票）。
    name = str(row.get("name") or "").strip()
    acc_txt = _scale_phrase(accel, vol)

    if level == LEVEL_CONFIRMED:
        narr = f"{name} 是这条线的龙头：五项证据齐，{'、'.join(acc_txt)}"
    elif level == LEVEL_CONFIRMED_THIN:
        gaps = _scale_gaps(accel, vol)
        narr = (f"{name} 是这条线的龙头，但{'、'.join(gaps)} —— "
                f"这种龙头容易被一波流带走，接力要盯紧")
    elif level == LEVEL_PARTIAL:
        narr = (f"{name} 的形态很像龙头（{'、'.join(acc_txt)}），"
                f"但五项证据还没凑齐，暂时只按准龙头看")
    elif level == LEVEL_PENDING:
        miss = "五项证据没凑齐" if five == "pending" else "逐日证据不足"
        if scale_any:
            narr = f"{name} 按准龙头看：{miss}；形态上{'、'.join(acc_txt)}，但还不够叫龙头"
        elif vol.get("passed") is False or accel["trend"] in ("flat", "decelerating"):
            narr = f"{name} 按准龙头看：{miss}，量能和封板时间也都不支持它当龙头"
        else:
            narr = f"{name} 按准龙头看：{miss}，还没到能叫龙头的程度"
    else:
        narr = ""

    # ⛔ 走了替代路径必须**两档都说明**（2026-10-21 实测漏过）：读者有权知道这个「龙头」
    #    有一项证据是拿不到，而不是没查。首版只写在 confirmed 分支，
    #    confirmed_thin（尺也没过）那条叙述里完全没有催化信息 → 隐瞒了关键前提。
    if via_relief and narr:
        narr += "；催化这一项目前拿不到证据（新闻源还没给分类），先按已确认处理"

    _ROLE_OF_LEVEL = {
        LEVEL_CONFIRMED: "龙头", LEVEL_CONFIRMED_THIN: "龙头",
        LEVEL_PARTIAL: "准龙头", LEVEL_PENDING: "准龙头",
        LEVEL_NOT: "跟风",
    }
    return {
        "enabled": True,
        "level": level,
        "label": LEVEL_LABEL[level],
        "identity_role": _ROLE_OF_LEVEL[level],
        "five_dim": five or None,
        "scale_ok": scale_ok,          # 两把尺是否都过（叙述用，⛔ 不改身份）
        "scale_any": scale_any,
        "acceleration": accel,
        "volume": vol,
        "checks": checks,
        "narrative": narr,
        "series_len": len(seq),
    }


def _only_catalyst_missing(row: dict[str, Any], ev: dict[str, Any], scale_ok: bool,
                          carry: float, continuous: float) -> bool:
    """是否「只差催化」——**严格等于** `leader_cycle.annotate` 原文的 confirmed 五条，
    只把 `catalyst == "sustained_verified"` 换成 `catalyst == "unknown"`。

    ⛔ 不额外加 competition / 五维其它条件：原文 confirmed 本来就不看 competition
      （那是外层 `if confirmed and (competitive or ...)` 的另一条），多加会让命中恒为 0。
    ⛔ `scale_ok` 参数保留但**不进判据**：两把尺是叙述层，不进资格层——
      它只决定「是 confirmed 还是 confirmed_thin」的措辞，不决定能不能确认。
    """
    if not _allow_no_catalyst():
        return False
    dims = (ev.get("dimensions") or {})
    if not dims:
        return False
    catalyst = str((dims.get("catalyst") or {}).get("status") or "")
    if catalyst != "unknown":
        return False            # 催化有但不够（single_verified 等）→ 不放行
    return bool(carry > 0 and continuous >= 3
                and str((dims.get("sector_follow") or {}).get("status") or "") == "sustained"
                and str((dims.get("divergence") or {}).get("status") or "") == "recovery_with_carry"
                and str((dims.get("launch") or {}).get("status") or "")
                in ("new_direction_candidate", "existing_direction"))


def _scale_phrase(accel: dict[str, Any], vol: dict[str, Any]) -> list[str]:
    """两把尺的**正面**表述（有哪些支持它的形态证据）。空 list → 都没过。"""
    out: list[str] = []
    if accel.get("trend") == "accelerating":
        out.append("涨停时间越走越早")
    if vol.get("passed") is True:
        out.append("做过爆量检验")
    return out


def _scale_gaps(accel: dict[str, Any], vol: dict[str, Any]) -> list[str]:
    """两把尺**缺什么**（供「是龙头但不硬」的叙述）。⛔ 数据不足要说「不足」不说过没过。"""
    gaps: list[str] = []
    if accel.get("trend") == "accelerating":
        pass
    elif accel.get("trend") == "unknown":
        gaps.append("涨停时间天数不够看不出节奏")
    else:
        gaps.append("涨停时间没越走越早")
    if vol.get("passed") is True:
        pass
    elif vol.get("passed") is False:
        gaps.append("没做过爆量检验")
    else:
        gaps.append("量能天数不够看不出检验")
    return gaps


def tag_text(result: dict[str, Any]) -> str | None:
    """卡面极短身份标签（⛔ 只给前两档，跟风不标——标了只会添噪）。

    与「降权≠不显示」一致：真龙头用红、未验证的龙头用橙（说明有保留）。
    """
    if not isinstance(result, dict) or not result.get("enabled"):
        return None
    lv = result.get("level")
    if lv == LEVEL_CONFIRMED:
        return "<text_tag color='red'>龙头</text_tag>"
    if lv == LEVEL_CONFIRMED_THIN:
        return "<text_tag color='orange'>龙头·未验证</text_tag>"
    return None
