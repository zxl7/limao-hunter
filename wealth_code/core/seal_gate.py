#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封板强度闸（2026-11-03 用户「重新定量」）
============================================================================
病灶（2026-11-03 实证·传媒线）：
  昨日传媒 9 只首板，5 只因成交额 <3 亿被剔出候选；今日这 5 只 **5/5 全封**，
  4 只 9:25 竞价一字。而被留下、成交额最大（8.74 亿）的文投控股，今日没封。

  这不是偶然。33 个交易日、约 3000 个首板样本的回测（见下）显示：
  **首板次日晋级率与成交额单调负相关**——门槛的方向是反的。

  ┌──────────────┬──────────┬──────────┬─────────┐
  │ 封板时点      │ ≤1.5亿    │ 1.5-3亿   │ ≥3亿     │
  ├──────────────┼──────────┼──────────┼─────────┤
  │ 竞价即封≤9:26 │ 56.0%    │ 22.2%     │ 20.0%    │
  │ 秒封9:26-9:31 │ 38.7%    │ 25.0%     │ 18.8%    │
  │ 早盘9:31-10:00│ 26.4%    │ 19.7%     │ 11.6%    │
  │ 10点后        │ 16.7%    │ 12.1%     │ 11.1%    │
  └──────────────┴──────────┴──────────┴─────────┘
  另两个单调变量：封单/流通（≥10%→66.7% / 5-10%→38.9% / 2-5%→22.8% / <2%→14.1%）、
  炸板次数（0次→17.3% / 1次→15.6% / ≥2次→12.3%）。

结论：成交额不是「流动性够不够」，而是「筹码交换充分不充分」。缩量能封板 = 锁死，
是**优点**不是缺点。故本模块把成交额从硬门槛降级为四因素之一，改判「封板强度」。

⛔ 边界：
  - 只回答「该不该进候选池」，不改评分/排序/名额/仓位/09:25 裁决；
  - **数据不全一律不放宽**（fbt/封单/流通 缺任一 → grade=unknown，走原门槛），
    ⛔ 不允许「因为不知道反而开后门」；
  - **成交额绝对下限已拆（2026-11-03 用户「A 拆掉下限」）**：原来的
    `ABS_MIN_TURNOVER_YI=0.5` 是最后一道硬门槛，11-03 实盘它正好拦掉当天最强的 3 只
    （大晟文化 0.26 亿 / 华阳国际 0.47 亿 / 龙版传媒 0.48 亿，全是 9:25 竞价一字）。
    一字板成交额小是**锁死**的结果不是缺陷 → 下限改由「封单/流通比」承接
    （`THICK_SEAL_EXEMPT_PCT`）。只保留一根极低的数据防呆线 `DATA_SANITY_MIN_YI`
    （0.05 亿 = 500 万，挡 amount 字段异常，⛔ 不是流动性判据）；
  - 可逆开关 `ENABLE_SEAL_GATE=0` 一键回旧行为（纯成交额门槛）。
"""
from __future__ import annotations

import os
from typing import Any

SCHEMA = "seal_gate_v1"

# ── 可逆开关 ────────────────────────────────────────────────────────────────
ENABLE_SEAL_GATE = "1"

# ── 成交额相关阈值 ──────────────────────────────────────────────────────────
# ⛔ 绝对下限已拆（2026-11-03 用户拍板 A「拆掉下限」）。
#   原 `ABS_MIN_TURNOVER_YI = 0.5` 是无条件硬拦，11-03 实盘它正好拦住当天最强的三只：
#   大晟文化 0.26 亿（封单/流通 16.9%）、华阳国际 0.47 亿（6.6%）、龙版传媒 0.48 亿（4.1%），
#   三只全是 9:25 竞价一字。一字板成交额小 = 筹码锁死，不是「没钱要」。
#   → 下限职能移交「封单/流通比」（见 THICK_SEAL_EXEMPT_PCT）。

# 数据防呆线（亿）：⛔ 不是流动性判据，只挡「成交额小到不像真实成交」的数据异常。
# 0.05 亿 = 500 万；正常 A 股一字板换手再低也在千万级，低于此几乎只能是字段异常。
DATA_SANITY_MIN_YI = 0.05

# 厚封单豁免线（%）= 封单额 / 流通市值。
# 这是拆掉成交额下限后的**承接判据**：成交额再小，只要封单压得住流通盘就放行。
# 依据（33 日回测·首板次日晋级率）：封单/流通 ≥10% → 66.7%｜5-10% → 38.9%｜
#   2-5% → 22.8%｜<2% → 14.1%，全场均值约 20%。取 5% = 2 倍于均值。
# ⚠️ 4.1% 的龙版传媒按此条仍不进（但它形态 8/10 strong，走 ③ 照样放行）——
#    这条线救的是「形态一般但封单厚」的票，不替代形态档。
THICK_SEAL_EXEMPT_PCT = 5.0

# 强封放行线（亿）。strong 档不看板数档位的常规门槛，按这条放行。
# 定 0.8 的依据：一字/竞价封的成交额天然小（筹码锁死，没换手），
# 这批票「看得见但买不进」，进池的作用是**指路**（带动板块 → 找隔壁），
# ⛔ 不是让你直接买它。0.8 亿足以过滤掉真正的僵尸股。
STRONG_FLOOR_YI = 0.8

# ══════════════════════════════════════════════════════════════════════════
# 🎭 一字板 vs 换手板：成交额的含义完全相反（2026-11-04 用户定）
# ══════════════════════════════════════════════════════════════════════════
# 用户原话：
# 「紫竹可以挡掉，但不是因为身位，而是因为它不是一字板，它是换手板。换手板的成交
#   不到 5 亿，这个其实可以不要的。我们之前只限制了成交额，忘了限制它是换手板还是
#   一字板了。一字板成交额低没问题，但是换手板成交额低其实是很有问题的，容易被资金做局。」
#
# ## 为什么必须分开判
#
# 一字板成交额低是**结构必然**——9:25 就封死，全天没有成交机会，想卖也卖不出量。
#   所以「成交额小」在这里不含任何信息，拿它当缺陷等于惩罚「封得太好」。
# 换手板成交额低是**主动结果**——盘中真的换手了（有买有卖），量却只有两三个亿，
#   说明换手只在少数资金之间对倒完成，没有真实的市场参与。这种票资金可以自己拉到涨停，
#   但次日没有外部承接 → 容易「晋级但买不到溢价」。
#
# ## 依据（2026-11-04 实测·`cache/zt_history` 33 个归档日 × `cache/kline_qfq`）
#
# 样本 370 个「≥2 板·票日」，一字板用日K严格判定（开=高=低=收，370/370 全部可判）：
#
# ┌────────────┬──────┬────────┬─────────┬───────┐
# │ 分组        │ n    │ 晋级率  │ 次日开收 │ 胜率  │
# ├────────────┼──────┼────────┼─────────┼───────┤
# │ 一字板 全部  │ 84   │ 63.1%  │ −0.93%  │ 30.1% │
# │ 一字 <1.5亿 │ 72   │ 65.3%  │ −1.10%  │ 29.2% │ ← 量小但晋级最高
# │ 换手 <3亿   │ 33   │ 39.4%  │ −3.48%  │  9.1% │ ← 最差
# │ 换手 3~5亿  │ 63   │ 42.9%  │ −0.68%  │ 33.3% │
# │ 换手 5~10亿 │ 106  │ 25.5%  │ −0.51%  │ 38.7% │
# │ 换手 ≥10亿  │ 72   │ 22.2%  │ +1.13%  │ 50.0% │ ← 最好
# └────────────┴──────┴────────┴─────────┴───────┘
#
# 可成交口径（`0 < 次日 gap ≤ 5%`，n=71）分界更干净：
#   <1.5亿 −1.49% / 1.5~3亿 −4.47% / 3~5亿 −3.25% ｜ 5~10亿 +0.47% / ≥10亿 +0.02%
#   ⇒ **5 亿以下全亏，5 亿以上才转正** —— 与用户「不到 5 亿可以不要」吻合。
#
# 2 板换手板（紫竹所在档）：
#   <1.5亿 n=11 胜率18.2% / 1.5~2亿 n=12 胜率25.0% /
#   **2~3亿 n=16 胜率 0.0%（16 只全亏）、开收 −4.06%** / 3~5亿 n=43 胜率34.9% /
#   5~10亿 n=65 胜率40.0% / ≥10亿 n=41 胜率 51.2%
#   ⇒ 紫竹 2 板·换手·2.54 亿，正落在**历史 16 只全亏**那一档。
#
# ## 晋级率与胜率再次反向（与 §三·B 同源）
# 1~2 板全样本 227 只按「换手板 ≥5 亿」切分：
#   保留 145 只 → 晋级 31.7% / 开收 −0.75% / 胜率 40.0%
#   剔除  82 只 → 晋级 **40.2%**（更高！）/ 开收 −1.50% / 胜率 **24.4%**
# ⛔ 被剔除的那批「更容易涨停，但买了更不赚钱」—— 这正是「做局」的量化形态。
#    ⚠️ 代价要说清：82 只里也有赢家（鼎信通讯 +17.94%、锡华科技 +14.21%、
#       博汇科技 +13.43%、世联行 +11.15%），本闸会一起放弃。净效果是胜率优先。
#
# ## 边界
# - `is_one_word` 缺失（None）→ **不判**，沿用原门槛（缺数据既不没收也不放宽）。
# - 只影响「进不进候选池」，不改评分/排序/名额/仓位/09:25 裁决。
# - 可逆：`ENABLE_TURNOVER_BOARD_FLOOR=0` 回到「一字换手不分」的旧口径。
ENABLE_TURNOVER_BOARD_FLOOR = "0"
# 🔴 2026-11-11 用户「这个 5 亿的限制把它 pass 掉吧，不要了，全系统清除这个限制」。
#    成交额绝对闸已全系统拆除，换手板也不再提到 5 亿 —— 门槛恒为数据防呆线。
#    ⛔ 背景（用户 11-04 原话＋实测）仍然成立：换手板成交额低确实更像少数资金对倒
#      （2~3 亿档 16 只历史全亏）。但那是**统计倾向**，不是可以一刀切的门槛：
#      11-11 光通信质量最好的两只（青山纸业 3.29 亿 / 换手 4.6%、华脉科技 1.55 亿 /
#      换手 5.7%）都是「流通盘大 + 换手极低」的锁筹型，被 5 亿线误杀。
#    ⇒ 判据改为**只留痕不否决**：`churn_risk`（小额高换手）写进诊断，供人工/复盘看，
#      ⛔ 不再产生任何一个「因为成交额小所以不进池/不买」的结论。
#    回退：`ENABLE_TURNOVER_BOARD_FLOOR=1` + `TURNOVER_BOARD_FLOOR_YI=5.0`。
TURNOVER_BOARD_FLOOR_YI = DATA_SANITY_MIN_YI   # 原 5.0（2026-11-11 清除）
# 进池阶段只对 1~2 板追加约束；≥3 板本来就是 5 亿，不必重复收紧。
TURNOVER_BOARD_FLOOR_MAX_BOARDS = 2


def board_style(row: dict) -> str:
    """封板形态：`yizi` 一字 / `turnover` 换手 / `unknown` 字段缺失。

    ⛔ 不猜：`is_one_word` 非布尔一律 unknown（字符串 "true"/1 也不认，
       因为历史文件里这字段出现过 null，用宽松转换会把缺失读成「非一字」）。
    """
    v = (row or {}).get("is_one_word")
    if v is True:
        return "yizi"
    if v is False:
        return "turnover"
    return "unknown"


def turnover_board_floor_enabled() -> bool:
    return os.environ.get(
        "ENABLE_TURNOVER_BOARD_FLOOR", ENABLE_TURNOVER_BOARD_FLOOR
    ) not in ("0", "false", "False", "")


def style_floor(row: dict, floor_normal: float) -> float:
    """按封板形态给 1~2 板的**实际生效**成交额门槛。

    · 一字板 / 形态未知 → 原门槛（一字板成交额天然小，不能拿它当缺陷）
    · 换手板 → 不得低于 `TURNOVER_BOARD_FLOOR_YI`（5 亿）
    """
    if not turnover_board_floor_enabled():
        return floor_normal
    if board_style(row) != "turnover":
        return floor_normal
    try:
        boards = int((row or {}).get("boards"))
    except (TypeError, ValueError):
        return floor_normal              # 板数缺失 → 不收紧（不猜）
    if boards < 1 or boards > TURNOVER_BOARD_FLOOR_MAX_BOARDS:
        return floor_normal              # ≥3 板本就是更严档，不重复改
    return max(floor_normal, TURNOVER_BOARD_FLOOR_YI)


# 极端热题材豁免：同题材涨停 ≥ THEME_HOT_MATES 家 → 门槛降到 THEME_HOT_FLOOR_YI。
# 依据（2026-11-03 短剧实证）：昨日传媒 9 只首板中 5 只成交额 1.5~2.8 亿被 3 亿门槛全剔，
# 今日 5/5 全封（4 只竞价一字）；而唯一留下的文投控股（8.74 亿）没封。
# 极热题材里的低成交额不是缺陷——27 家同门意味着分歧时有人接力。
# ⚠️ 阈值取 8 家：只有当日最热的 1~2 个题材能触发，⛔ 不让全市场都走后门。
# ⚠️ 2026-11-03：其成交额门槛随下限一起降到防呆线（原 1.0 亿会变成变相新下限）。
THEME_HOT_MATES = 8
THEME_HOT_FLOOR_YI = DATA_SANITY_MIN_YI

# 档位阈值（满分 10）
STRONG_MIN_SCORE = 6
NORMAL_MIN_SCORE = 3

# ── 权重（全部来自回测单调性，非拍脑袋）────────────────────────────────────
# 封板时点：越早越强（56.0% → 38.7% → 26.4% → 16.7%）
_TIMING_BANDS = ((9 * 60 + 26, 3), (9 * 60 + 31, 2), (10 * 60, 1))

# 封单/流通比：越厚越强（66.7% → 38.9% → 22.8% → 14.1%）
_SEAL_BANDS = ((10.0, 3), (5.0, 2), (2.0, 1))

# 炸板次数：越少越强（17.3% → 15.6% → 12.3%）
_ZBC_BANDS = {0: 2, 1: 1}

# 缩量（仅 1~2 板有意义；高位缩量是另一回事，⛔ 不在这里判）
_SHRINK_BANDS = ((1.5, 2), (3.0, 1))
SHRINK_MAX_BOARDS = 2


def enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get("ENABLE_SEAL_GATE", ENABLE_SEAL_GATE) not in ("0", "false", "False", "")


def _f(v: Any, default: float | None = None) -> float | None:
    """容错转浮点；NaN/空串/非法 → default（⛔ 不把 NaN 当 0 用）。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x:                      # NaN
        return default
    return x


def _fbt_minutes(fbt: Any) -> int | None:
    """首封时刻 → 当日分钟数。支持 '092500' / '92500' / 92500 / '09:25:00'。"""
    s = str(fbt or "").strip().replace(":", "")
    if not s or not s.isdigit():
        return None
    s = s.zfill(6)
    if len(s) != 6:
        return None
    h, m = int(s[:2]), int(s[2:4])
    if h > 23 or m > 59:
        return None
    return h * 60 + m


def seal_profile(row: dict) -> dict:
    """封板质量画像。**不改 row**，纯计算。

    返回 {score, grade, parts, missing, reasons}
      grade: strong / normal / weak / unknown
      unknown = 关键字段缺失，调用方须走原门槛（⛔ 不因缺数据放宽）
    """
    fbt_m = _fbt_minutes(row.get("fbt"))
    seal_amt = _f(row.get("seal_amt"))
    lt = _f(row.get("lt_mktcap"))
    zbc = _f(row.get("zbc"))
    cje = _f(row.get("cje_yi"))
    boards = _f(row.get("boards"))

    missing = [k for k, v in (("fbt", fbt_m), ("seal_amt", seal_amt),
                              ("lt_mktcap", lt), ("zbc", zbc)) if v is None]
    if lt is not None and lt <= 0:
        missing.append("lt_mktcap")
    if missing:
        return {"score": None, "grade": "unknown", "parts": {}, "missing": missing,
                "reasons": [f"缺 {','.join(missing)}·不放宽"]}

    parts: dict[str, int] = {}
    reasons: list[str] = []

    # ① 封板时点
    parts["timing"] = 0
    for limit, pt in _TIMING_BANDS:
        if fbt_m <= limit:
            parts["timing"] = pt
            break
    if parts["timing"] >= 3:
        reasons.append("竞价即封")
    elif parts["timing"] == 2:
        reasons.append("开盘秒封")

    # ② 封单/流通
    ratio = seal_amt / lt * 100.0 if lt else 0.0
    parts["seal"] = 0
    for limit, pt in _SEAL_BANDS:
        if ratio >= limit:
            parts["seal"] = pt
            break
    if parts["seal"] >= 2:
        reasons.append(f"封单{ratio:.1f}%厚")

    # ③ 炸板
    parts["zbc"] = _ZBC_BANDS.get(int(zbc), 0)
    if parts["zbc"] == 2:
        reasons.append("零炸板")

    # ④ 缩量（仅低位 1~2 板；高位缩量不在此判）
    #    ⛔ 2026-11-04 修正：**只有一字板**的缩量才算「锁死」。
    #      换手板缩量是「换手在少数资金之间完成」，不是筹码锁死 —— 给分等于奖励做局
    #      （紫竹 2 板换手 2.54 亿原本靠这条拿到 1 分）。换手板改由 `style_floor`
    #      的 5 亿门槛承接，这里不再重复给分也不额外扣分。
    parts["shrink"] = 0
    if (cje is not None and boards is not None
            and 1 <= boards <= SHRINK_MAX_BOARDS and board_style(row) == "yizi"):
        for limit, pt in _SHRINK_BANDS:
            if cje <= limit:
                parts["shrink"] = pt
                break
        if parts["shrink"] == 2:
            reasons.append(f"缩量{cje:.2f}亿")

    score = sum(parts.values())
    grade = ("strong" if score >= STRONG_MIN_SCORE
             else "normal" if score >= NORMAL_MIN_SCORE else "weak")
    return {"score": score, "grade": grade, "parts": parts,
            "missing": [], "seal_ratio_pct": round(ratio, 2), "reasons": reasons}


def admit(
    row: dict,
    floor_normal: float,
    floor_leader: float | None = None,
    *,
    theme_mates: int | None = None,
    enabled_: bool | None = None,
) -> dict:
    """准入判定（替代 pipeline_eod 里那段纯成交额比较）。

    theme_mates: 该票所属**同一炒作题材**当日涨停家数（选股宝题材口径）。
      来源必须与候选的 `theme` 字段同源，⛔ 不许拿东财行业家数冒充
      （传媒系被东财拆成 游戏Ⅱ/出版/广告营销/纺织制造/工程咨询 六个行业，
       按行业算永远凑不到 8 家，这条豁免就成了死代码）。

    返回 {keep, grade, score, floor_used, exempt, source, reason}
      · source 是**放行/拦截来源**的显式标签，供调用方分类统计
        （enough / strong / thick_seal / theme_hot / leader / below_sanity / none / off）。
        ⛔ 调用方不许再靠 reason 文本 contains 猜来源。
      · 常规门槛优先（成交额够 → 直接进，不看形态，保持旧行为不变）
        ⚠️ 2026-11-04 起 1~2 板的「门槛」按封板形态分档：换手板 5 亿、一字板原门槛。
      · 不够时依次看：强封档 → 厚封单豁免 → 极热题材豁免 → 高度层豁免 → 剔除
      · 2026-11-03「A 拆掉下限」后：成交额不再有 0.5 亿硬门槛，
        由 `THICK_SEAL_EXEMPT_PCT`（封单/流通 ≥5%）承接；仅保留数据防呆线。
    """
    cje = _f(row.get("cje_yi"), 0.0) or 0.0
    prof = seal_profile(row)

    if not enabled(enabled_):
        keep = cje >= floor_normal or (floor_leader is not None and cje >= floor_leader)
        return {"keep": keep, "grade": "off", "score": None, "source": "off",
                "floor_used": floor_normal, "exempt": False,
                "reason": "开关关闭·纯成交额门槛"}

    # ⓪ 封板形态修正门槛（2026-11-04）：换手板 1~2 板不得低于 TURNOVER_BOARD_FLOOR_YI。
    #    ⛔ 必须在「① 成交额够就直接进」**之前**——否则换手板会先凭 1.5 亿的低位门槛
    #      直通，后面的收紧变成死代码。
    _style = board_style(row)
    _floor_eff = style_floor(row, floor_normal)

    # ① 成交额本来就够 → 旧行为，不动
    if cje >= _floor_eff:
        _exempt_style = _floor_eff > floor_normal
        return {"keep": True, "grade": prof["grade"], "score": prof["score"],
                "source": "enough", "floor_used": _floor_eff, "exempt": False,
                "style": _style,
                "reason": ("成交额达标" if not _exempt_style
                           else f"成交额达标（换手板门槛{_floor_eff:g}亿）")}

    # ② 数据防呆线：⛔ 不是流动性判据，只挡字段异常（amount 缺失/错单位）
    if cje < DATA_SANITY_MIN_YI:
        return {"keep": False, "grade": prof["grade"], "score": prof["score"],
                "source": "below_sanity", "floor_used": DATA_SANITY_MIN_YI, "exempt": False,
                "reason": f"成交额{cje:.3f}亿<数据防呆线{DATA_SANITY_MIN_YI:g}亿·疑似字段异常"}

    # ③ 强封档 → 放行（一字/竞价封 + 厚封单 + 零炸板 + 缩量）
    if prof["grade"] == "strong":
        return {"keep": True, "grade": "strong", "score": prof["score"],
                "source": "strong", "floor_used": STRONG_FLOOR_YI, "exempt": True,
                "reason": "强封放行（" + "·".join(prof["reasons"]) + "）"}

    # ④ 厚封单豁免（拆掉成交额下限后的承接判据）：形态一般也可能封单极厚。
    #    ⚠️ profile 缺字段(unknown)时没有 seal_ratio_pct → 不产生豁免（缺数据不放宽）。
    _ratio = prof.get("seal_ratio_pct")
    if _ratio is not None and _ratio >= THICK_SEAL_EXEMPT_PCT:
        return {"keep": True, "grade": prof["grade"], "score": prof["score"],
                "source": "thick_seal", "floor_used": DATA_SANITY_MIN_YI, "exempt": True,
                "reason": f"厚封单豁免（封单/流通 {_ratio:.1f}% ≥ {THICK_SEAL_EXEMPT_PCT:g}%）"}

    # ⑤ 极热题材豁免：同题材 ≥8 家，缩量也能进（27 家同门的分歧有人接力）
    if theme_mates is not None and theme_mates >= THEME_HOT_MATES and cje >= THEME_HOT_FLOOR_YI:
        return {"keep": True, "grade": prof["grade"], "score": prof["score"],
                "source": "theme_hot", "floor_used": THEME_HOT_FLOOR_YI, "exempt": True,
                "reason": f"热题材豁免（同题材{theme_mates}家）"}

    # ⑥ 高度层龙头豁免（沿用旧规则）
    if floor_leader is not None and cje >= floor_leader:
        return {"keep": True, "grade": prof["grade"], "score": prof["score"],
                "source": "leader", "floor_used": floor_leader, "exempt": True,
                "reason": "高度层豁免"}

    mates_txt = f"·同题材{theme_mates}家" if theme_mates is not None else ""
    _style_txt = "·换手板" if _style == "turnover" else ""
    return {"keep": False, "grade": prof["grade"], "score": prof["score"],
            "source": "none", "floor_used": _floor_eff, "exempt": False, "style": _style,
            "reason": f"成交额{cje:.2f}亿<{_floor_eff:g}亿{_style_txt}{mates_txt}"
                      f"且封板强度不足（{prof['score']}/10）"}


def annotate(rows: list[dict], *, floor_of=None) -> dict:
    """给一批候选挂 `seal_gate` 画像字段（**只写字段，不改顺序/评分**）。

    floor_of: callable(row) -> (floor_normal, floor_leader)，缺省用 5.0/None。
    """
    n_strong = 0
    out_rows = rows or []
    for r in out_rows:
        if not isinstance(r, dict):
            continue
        prof = seal_profile(r)
        r["seal_gate"] = {"schema": SCHEMA, **prof}
        if prof["grade"] == "strong":
            n_strong += 1
    return {"schema": SCHEMA, "total": len(out_rows), "strong": n_strong}


def summary_line(rows: list[dict]) -> str:
    """一行汇总：今天有几只强封票被旧门槛挡住、现已放行。供诊断/卡面用（单行）。"""
    strong = [r for r in (rows or []) if isinstance(r, dict)
              and ((r.get("seal_gate") or {}).get("grade") == "strong")]
    if not strong:
        return ""
    names = "、".join(str(r.get("name") or "") for r in strong[:4])
    more = f" 等{len(strong)}只" if len(strong) > 4 else ""
    return f"⚡ 强封放行 {len(strong)} 只：{names}{more}"
