#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当日涨停强度 + 次日锚点接力链（2026-10-21 用户口述体系·`core/anchor_call.py`）。

## 体系来源（用户原话要点）
「以个股在当天的涨停时间与所处节点作为判断其地位的核心依据；次日只需做好锚点的对比与
 观察，重点看前排个股是否大高开或一字、是否以一字表明态度，据此判断是否需要出手；
 在前排强势确认后，再选择容量合适的核心标的进行接力。」

## 三个环节（顺序不可换）
① **定强度**（收盘后·`strength_of`）：涨停时间 → 节点档；叠板位/带动/封板质量 → 强度分。
   每条线选出**大将军**（`pick_general`）＝最先封 + 带动最多 + 封得最硬的那只。
② **读锚点**（次日 09:25·`anchor_read`）：只看昨天的大将军（和龙头）今天怎么开——
   一字 / 大高开 / 强开 / 平开 / 破预期 五档。这是「态度」，不猜后续。
③ **接力**（`line_verdict`）：锚点态度决定这条线今天**能不能出手**、以及**出手打谁**。

## 龙头 vs 前排大将军
- **龙头**：空间高度 + 全市场认同（跨线唯一性）→ 决定「今天敢不敢做」的大方向。
- **大将军**：单条线里最先封板、带动最多、封单最硬的那只 → 决定「这条线今天能不能打」。
- 关系：龙头必须有大将军属性；**大将军可以不是龙头**。龙头断板后由大将军接位，
  该线降级为「无龙头接力」——只允许在锚点买不到时打隔壁，不允许当核心打。

## ⛔ 边界
- 纯计算、只读字段；缺失一律 `unknown`，绝不猜（不编造竞价、不编造强度）。
- 本模块不改任何既有阈值；是否生效由调用方（`pool_call`）的开关决定。
"""
from __future__ import annotations

import os

from typing import Any

# ── 节点档（涨停时间）————————————————————————————————————————————
# ⛔ 用「时间窗」而不是单一时刻：集合竞价顶板/开盘抢筹/上午主攻/午后跟随/尾盘偷袭，
#   这五档的**资金性质完全不同**——早盘封的是主动抢，尾盘封的是被动跟/偷袭。
NODE_AUCTION = "auction"     # 09:25–09:30 竞价顶板（一字或竞价即封）
NODE_DAWN = "dawn"           # 09:30–09:45 开盘抢筹（最强）
NODE_MAIN = "main"           # 09:45–10:30 上午主攻（确认）
NODE_FOLLOW = "follow"       # 10:30–13:30 午后跟随（被动）
NODE_TAIL = "tail"           # 13:30 之后 尾盘偷袭（最弱）
NODE_UNKNOWN = "unknown"

NODE_LABEL = {
    NODE_AUCTION: "竞价顶板", NODE_DAWN: "开盘抢筹", NODE_MAIN: "上午主攻",
    NODE_FOLLOW: "午后跟随", NODE_TAIL: "尾盘偷袭", NODE_UNKNOWN: "时间未知",
}
# 强度权重：竞价顶板最强（但买不到），尾盘最弱。
NODE_WEIGHT = {NODE_AUCTION: 1.00, NODE_DAWN: 0.92, NODE_MAIN: 0.72,
               NODE_FOLLOW: 0.45, NODE_TAIL: 0.20, NODE_UNKNOWN: 0.50}

# ── 板位档 ————————————————————————————————————————————————————
_STAGE_LABEL = {1: "首板", 2: "一进二", 3: "加速板", 4: "加速板"}


def fbt_minutes(fbt: Any) -> int | None:
    """封板时间 HHMMSS → 当日分钟数（展示用）；非法/缺失 → None（不猜）。"""
    s = "".join(ch for ch in str(fbt or "") if ch.isdigit())
    if len(s) < 4:
        return None
    try:
        h, m = int(s[:2]), int(s[2:4])
    except ValueError:
        return None
    if not (9 <= h <= 15 and 0 <= m < 60):
        return None
    return h * 60 + m


def _fbt_seconds(fbt: Any) -> int | None:
    """封板时间 → 当日秒数。节点分档用秒级：09:30:00 属竞价，09:30:01 已进入抢筹段。"""
    s = "".join(ch for ch in str(fbt or "") if ch.isdigit())
    if len(s) < 4:
        return None
    try:
        h, m = int(s[:2]), int(s[2:4])
        sec = int(s[4:6]) if len(s) >= 6 else 0
    except ValueError:
        return None
    if not (9 <= h <= 15 and 0 <= m < 60 and 0 <= sec < 60):
        return None
    return h * 3600 + m * 60 + sec


def node_of(fbt: Any) -> str:
    """涨停时间 → 节点档。09:30:00 及以前（含竞价）= auction；其余按时间窗。"""
    t = _fbt_seconds(fbt)
    if t is None:
        return NODE_UNKNOWN
    if t <= 9 * 3600 + 30 * 60:
        return NODE_AUCTION
    if t <= 9 * 3600 + 45 * 60:
        return NODE_DAWN
    if t <= 10 * 3600 + 30 * 60:
        return NODE_MAIN
    if t <= 13 * 3600 + 30 * 60:
        return NODE_FOLLOW
    return NODE_TAIL


def stage_of(boards: Any) -> str:
    try:
        b = int(boards)
    except (TypeError, ValueError):
        return "板位未知"
    return _STAGE_LABEL.get(b, "高位分歧" if b >= 5 else "板位未知")


def _num(v: Any) -> float | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def strength_of(it: dict[str, Any]) -> dict[str, Any]:
    """① 当日涨停强度（0~100）+ 节点/板位/证据。

    构成（缺项不给分、也不给零分——用**已有项**的均值，诚实降级）：
      · 时间 45%：越早越强（核心依据·用户「涨停时间与节点是地位的核心依据」）
      · 带动 25%：封板后带起几家同线涨停
      · 封板质量 20%：炸板次数越少越硬 + 封单/流通越厚越硬
      · 板块成片 10%：同题材涨停家数
    ⛔ 不含「涨幅」「板数」——板数已由身位/高度另算，混进来会把高位票无脑顶到前面。
    """
    node = node_of(it.get("fbt"))
    parts: list[tuple[float, float]] = []

    if node != NODE_UNKNOWN:
        parts.append((NODE_WEIGHT[node] * 100.0, 0.45))

    carry = _num(it.get("carry_cnt"))
    if carry is not None:
        # 带起 ≥5 家＝真带队；0 家＝独苗（在接力里几乎没价值）
        parts.append((min(100.0, max(0.0, carry / 5.0 * 100.0)), 0.25))

    zbc = _num(it.get("zbc"))
    ratio = _num(it.get("seal_ratio_pct"))
    seal_parts: list[float] = []
    if zbc is not None:
        seal_parts.append(max(0.0, 100.0 - zbc * 20.0))     # 炸 5 次 → 0
    if ratio is not None:
        seal_parts.append(min(100.0, ratio / 3.0 * 100.0))   # 封单/流通 3% → 满分
    if seal_parts:
        parts.append((sum(seal_parts) / len(seal_parts), 0.20))

    mates = _num(it.get("theme_mates_cnt"))
    if mates is None:
        mates = _num(it.get("sector_mates_cnt"))
    if mates is not None:
        parts.append((min(100.0, max(0.0, mates / 6.0 * 100.0)), 0.10))

    if not parts:
        return {"strength": None, "node": node, "node_label": NODE_LABEL[node],
                "stage": stage_of(it.get("boards")), "facts": {}, "missing": "all"}

    wsum = sum(w for _, w in parts)
    score = sum(v * w for v, w in parts) / wsum
    return {
        "strength": round(score, 1),
        "node": node,
        "node_label": NODE_LABEL[node],
        "stage": stage_of(it.get("boards")),
        "facts": {
            "fbt": it.get("fbt"), "carry_cnt": carry, "zbc": zbc,
            "seal_ratio_pct": ratio, "mates": mates, "boards": it.get("boards"),
        },
        "missing": "" if wsum >= 0.999 else f"缺{round((1 - wsum) * 100)}%权重数据",
    }


# ── 大将军 / 龙头 ————————————————————————————————————————————————

def _general_key(it: dict[str, Any]) -> tuple:
    """大将军排序：**最先封板**第一，再带动、再板数、再封单厚。

    ⛔ 时间必须排第一——用户的核心判据就是「涨停时间与节点」；把带动放前面会让
      「尾盘拉起来带了几家」的票压过「开盘一分钟就封死」的真先锋。
    """
    t = fbt_minutes(it.get("fbt"))
    carry = _num(it.get("carry_cnt")) or 0.0
    boards = _num(it.get("boards")) or 0.0
    ratio = _num(it.get("seal_ratio_pct")) or 0.0
    return (-(t if t is not None else 9999), carry, boards, ratio)


def pick_general(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """一条线里的「前排大将军」＝最先封 + 带动最多 + 封得最硬的那只。无数据 → None。"""
    cands = [r for r in (rows or []) if isinstance(r, dict) and fbt_minutes(r.get("fbt")) is not None]
    if not cands:
        return None
    return sorted(cands, key=_general_key, reverse=True)[0]


def general_note(it: dict[str, Any] | None) -> str:
    """大将军一句话：时间 + 节点 + 带动（卡面/复盘用）。"""
    if not isinstance(it, dict):
        return ""
    node = node_of(it.get("fbt"))
    t = fbt_minutes(it.get("fbt"))
    hhmm = f"{t // 60:02d}:{t % 60:02d}" if t is not None else "—"
    carry = _num(it.get("carry_cnt"))
    tail = f"，带起 {int(carry)} 家" if carry else ""
    return f"{it.get('name') or ''} {hhmm} 封板（{NODE_LABEL[node]}）{tail}"


# ── 龙头确认两把尺（2026-10-21 学 92科比 完整框架后补·文档 §18.1）———————
# 文档原话：「涨停时间越走越早：三板的封板时间早于二板，四板早于三板」
#          「先爆量检验、再缩量加速。没有爆量检验的股票，不能确认为龙头。」
# ⛔ 这两条是**确认龙头的加分尺**，不是否决闸 —— 没有通过的票仍可做普通接力，
#    只是不许在卡面/叙事里把它叫「龙头」（避免把跟风票包装成核心）。

def fbt_acceleration(series: list[tuple[Any, Any]]) -> dict[str, Any]:
    """涨停时间是否「越走越早」。series = [(日期, fbt), ...] 按时间正序。

    返回 {trend: accelerating / decelerating / flat / unknown, detail, deltas}
      · accelerating（越走越早）＝龙头在加速，最强的形态
      · decelerating（越走越晚）＝走弱前兆，接力要打折
    ⛔ 只有 ≥2 个时间点才判；不足 → unknown（不猜）。
    """
    pts: list[tuple[str, int]] = []
    for d, f in (series or []):
        t = _fbt_seconds(f)
        if t is not None:
            pts.append((str(d)[:10], t))
    if len(pts) < 2:
        return {"trend": "unknown", "detail": "涨停时间点不足 2 个", "deltas": []}
    deltas = [pts[i + 1][1] - pts[i][1] for i in range(len(pts) - 1)]
    # 分隔线：单次提前/推迟 ≥10 分钟才算变化（3~5 分钟属噪声）
    thr = 10 * 60
    ups = sum(1 for d in deltas if d <= -thr)
    downs = sum(1 for d in deltas if d >= thr)
    if ups and not downs:
        trend, txt = "accelerating", "封板时间一次比一次早 → 在加速"
    elif downs and not ups:
        trend, txt = "decelerating", "封板时间一次比一次晚 → 在走弱"
    else:
        trend, txt = "flat", "封板时间没有明显提前"
    return {"trend": trend, "detail": txt,
            "deltas": [round(d / 60.0, 1) for d in deltas],
            "points": [{"date": d, "fbt": f"{t // 3600:02d}{(t % 3600) // 60:02d}"}
                       for d, t in pts]}


def volume_check(series: list[tuple[Any, Any]]) -> dict[str, Any]:
    """是否经历「爆量检验 → 缩量加速」。series = [(日期, 成交额亿), ...] 正序。

    文档：「先爆量检验、再缩量加速。没有爆量检验的股票，不能确认为龙头。」
    判据（缺一不算通过）：
      ① 连板期间至少有一天 成交额 ≥ 前一日 × 1.5（爆量分歧）
      ② 爆量之后出现过 成交额 ≤ 爆量日 × 0.7（缩量加速）
    ⛔ 数据不足 3 天 → unknown（不猜）。
    """
    xs: list[float] = []
    for _d, v in (series or []):
        x = _num(v)
        if x is not None and x > 0:
            xs.append(x)
    if len(xs) < 3:
        return {"passed": None, "detail": "成交额序列不足 3 天，判不出有没有爆量检验"}
    peak_i, peak_r = -1, 0.0
    for i in range(1, len(xs)):
        r = xs[i] / xs[i - 1] if xs[i - 1] > 0 else 0.0
        if r > peak_r:
            peak_i, peak_r = i, r
    if peak_i <= 0 or peak_r < 1.5:
        return {"passed": False, "boom_ratio": round(peak_r, 2),
                "detail": f"没有经历过爆量分歧（最大放量只有前一天的 {peak_r:.1f} 倍）"
                          f"→ 不能算确认过的龙头"}
    after = xs[peak_i:]
    cooled = any(x <= xs[peak_i] * 0.7 for x in after[1:]) if len(after) > 1 else False
    return {"passed": bool(cooled), "boom_ratio": round(peak_r, 2),
            "cooled": cooled,
            "detail": (f"爆量 {peak_r:.1f} 倍后缩量加速 → 符合龙头量能特征" if cooled
                       else f"爆量 {peak_r:.1f} 倍但还没缩量加速 → 检验只做了一半")}


def weak_to_strong(prev: dict[str, Any] | None, quote: dict[str, Any] | None, *,
                   code: Any = None) -> dict[str, Any]:
    """弱转强真伪（文档 §6.2：竞价高开是做盘嫌疑，真弱转强是被真金白银买上去的）。

    ⛔ 09:25 只能给「嫌疑」判断，**不能定真伪**——真伪要看开盘后有没有资金往上买。
    返回 confidence ∈ high_bait(高开·做盘嫌疑) / watch(平开·等盘中) / likely(低开·符合真弱转强形态)。
    """
    prev = prev if isinstance(prev, dict) else {}
    oc = 0
    try:
        oc = int(prev.get("zbc") or 0)
    except (TypeError, ValueError):
        oc = 0
    node = node_of(prev.get("fbt"))
    was_weak = oc >= 2 or node in (NODE_FOLLOW, NODE_TAIL)
    if not was_weak:
        return {"applies": False, "detail": "昨天封得不弱，不属于弱转强场景", "confidence": ""}
    if not isinstance(quote, dict):
        return {"applies": True, "confidence": "watch",
                "detail": "昨天封得弱，但今天竞价数据没拿到 → 等开盘看有没有资金往上买"}
    prev_close = _num(quote.get("prev_close"))
    px = _num(quote.get("auction_price"))
    if not prev_close or not px or prev_close <= 0:
        return {"applies": True, "confidence": "watch", "detail": "竞价数据不完整"}
    gap = (px - prev_close) / prev_close * 100.0
    if gap >= 3.0:
        return {"applies": True, "confidence": "high_bait", "gap": round(gap, 2),
                "detail": (f"昨天封得弱、今天却高开 {gap:+.2f}% —— 92科比：竞价高开是做盘嫌疑，"
                           f"真正的弱转强是被真金白银买上去的。今天开盘后要盯着看有没有人接，"
                           f"不接就是假的")}
    if gap <= 0.5:
        return {"applies": True, "confidence": "likely", "gap": round(gap, 2),
                "detail": (f"昨天封得弱、今天平开/低开 {gap:+.2f}% —— 符合真弱转强的形态，"
                           f"但还要等开盘后资金往上买才算数")}
    return {"applies": True, "confidence": "watch", "gap": round(gap, 2),
            "detail": f"昨天封得弱、今天高开 {gap:+.2f}%，不算高开也不算平开 → 等盘中确认"}


# ── ② 锚点态度（次日 09:25）—————————————————————————————————————

ATT_YIZI = "yizi"            # 一字/竞价顶板 → 态度最强但买不到
# ⚠️ 2026-11-06 口径修正：「高开超 5% ＝ 买不到」这条**已废**（用户 11-05：
#    「有些票今天很强势明天它就可以高开，有些票今天会弱势那么它低开一样能拉起来」）。
#    今天「买不到」的唯一事实是**竞价封死**（`gap ≥ 涨停幅度 − 0.2pct`，已由 ATT_YIZI 覆盖）。
#    这档保留下来只表示「开得比 5% 高 → 按 `ai_caliber.gap_weight` 要压仓位」，
#    ⛔ 不再作为「打不了核心、只能打隔壁」的依据。
ATT_BIG_GAP = "big_gap"      # 高开超 5%（旧硬线已废）→ 仍可打，但按仓位权重下调
ATT_STRONG = "strong"        # 强开 2%~5% → 可打窗口
ATT_FLAT = "flat"            # 平开 0~2%
ATT_WEAK = "weak"            # 低开 -1%~0
ATT_BREAK = "break"          # 低开超 1% 或低于预期 → 破预期
ATT_UNKNOWN = "unknown"

# ⭐ 2026-11-10 口径统一（用户「先 A 再 B，直接按新策略执行，全局文案同步统一」）：
#    态度判定从**绝对开幅**改为 **DEV = 实际开幅 − 该票自己的质量预期**，
#    与 `ai_caliber.gap_zone`（09:25 唯一准入判据）**同一套口径**。
#
#    ⛔ 为什么必须改：11-10 东方创业(600278) 预期 −0.17%、实际 −0.89%、DEV −0.72
#       → 池内裁决判 `ok` 并买入，但风向标按「gap < 0 就叫弱」写成「低开转弱 → 别碰」，
#       同一张卡自相矛盾。
#    🔬 35 日实测：62 笔买入里 23 笔低开，其中「自己就是这条线锚点且低开」13 笔
#       **9 赚 4 亏、均值 +3.17%**，好于全样本 +1.73% ⇒ 旧那句「别碰」本身是错的。
ATT_DEEP_LOW = "deep_low"    # 深低开·错杀档（DEV ≤ 深低开线，与 gap_zone 豁免同源）
ATT_LOW = "low"              # 低于自身预期但没砸穿（破预期线 ≤ DEV < 0）→ 可打

# ⭐ 2026-11-10 晚 用户定稿文案（两轮口述，逐字落地）：
#    「高于预期就是**超预期，观察接力**；低开错杀＝**错杀，可低吸**；
#      一字＝**一字不买，看跟风**；略低于预期＝**观察下杀力度，低吸**；
#      符合预期，就是符合预期；未达预期，放弃。」
#
#    落地方式：**状态（label）+ 动作（action）各一段**——
#      · `ATT_LABEL` 只答「开成什么样」，⛔ 不含结论；
#      · `ATT_ACTION` 只答「怎么办」，⛔ 不重复状态。
#    卡面拼成「状态 开幅 → 动作」，例如「错杀 −6.20% → 可低吸」。
#    ⛔ 两段都由本文件单一源给，卡面/闸/回放一律引用 —— 11-10 早上
#       「卡面说别碰、裁决却买」就是两处各写一套文案造成的。
#
#    ⛔ **动作必须等于系统真实行为**（用户 11-10 19:36「只想要文案和行为一致」）：
#       写「放弃」的档，⑥b 就必须真拦（目前只有 `break` 拦）；写「可低吸/观察接力/看量能」
#       的档，就真的放行交给后面的闸。⛔ 不许出现「卡面说放弃、系统照买」。
ATT_LABEL = {
    ATT_YIZI: "一字", ATT_BIG_GAP: "大高开", ATT_STRONG: "超预期",
    ATT_FLAT: "符合预期", ATT_LOW: "略低于预期", ATT_DEEP_LOW: "错杀",
    # ⚠️ `weak` = 拿不到该票预期（无 DEV 可比），只是「低开 + 缺基准」，⛔ 不叫「低于预期」
    ATT_WEAK: "低开·无预期", ATT_BREAK: "未达预期", ATT_UNKNOWN: "未拿到报价",
}
# 动作＝「这条线今天怎么办」。空串表示**不给额外动作**（符合预期就是符合预期）。
# ⛔ 每一个"放弃"都必须能在 `pool_call` ⑥b 找到对应的硬拦，否则就是文案撒谎。
ATT_ACTION = {
    ATT_YIZI: "不买，看跟风", ATT_BIG_GAP: "压仓位", ATT_STRONG: "观察接力",
    ATT_FLAT: "", ATT_LOW: "观察下杀力度，低吸", ATT_DEEP_LOW: "可低吸",
    # ⚠️ weak 判据**不拦**（缺数据不裁决，只留痕 `anchor_weak_no_expect`）
    #    ⇒ 动作只能写「看量能」（交给量能/身位/名额），⛔ 不能写「放弃」。
    ATT_WEAK: "看量能", ATT_BREAK: "放弃", ATT_UNKNOWN: "",
}
# 动作里出现这几个词 = 系统必须否决这条线。`test_anchor_copy_consistency` 会扫。
ACTION_REJECT = "放弃"


def attitude_action(att: Any) -> str:
    """态度 → 动作（卡面/闸/回放共用的**唯一**文案源）。未知档返回空串。"""
    return ATT_ACTION.get(str(att or ""), "")


def attitude_label(att: Any, default: str = "") -> str:
    """态度 → 状态词。⛔ 不读冻结件里的旧 label —— 旧存档存的是当时的文案
    （「深低开错杀」「一字顶板」），照读会让卡面出现两代文案混排。"""
    return ATT_LABEL.get(str(att or ""), default)


def attitude_text(att: Any) -> str:
    """态度 → 「状态·动作」一行字（无动作时只给状态）。日志/回放用。"""
    a = str(att or "")
    label = ATT_LABEL.get(a, "")
    act = ATT_ACTION.get(a, "")
    return f"{label}·{act}" if (label and act) else (label or act)
# 态度对「这条线今天能不能打」的结论权重（只用于排序/解释，不直接下单）
# ⚠️ `ATT_DEEP_LOW` 排到 3：它是 v1.1 最赚钱的一档（深低开豁免），⛔ 不是「弱」。
_ATT_RANK = {ATT_STRONG: 4, ATT_DEEP_LOW: 3, ATT_YIZI: 3, ATT_BIG_GAP: 3, ATT_FLAT: 2,
             ATT_LOW: 2, ATT_WEAK: 1, ATT_BREAK: 0, ATT_UNKNOWN: 2}

# ⭐ 2026-11-10 口径开关：态度是否按 DEV（相对自身预期）判。
#    `ANCHOR_ATTITUDE_DEV=0` → 逐字回到「绝对开幅」旧口径（gap<0 就叫弱）。
#    ⚠️ 留它只为 A/B 与回滚，⛔ 不要长期关着——关着就是「卡面说别碰、裁决却买」那套。
ANCHOR_ATTITUDE_DEV = os.environ.get("ANCHOR_ATTITUDE_DEV", "1") != "0"

LIMIT_PCT_MAIN = 10.0
LIMIT_PCT_20CM = 20.0
BIG_GAP_PCT = 5.0        # 用户 5% 硬线（与 ai_caliber 同值·此处只读用于态度分档）
BREAK_GAP_PCT = -1.0


def is_20cm(code: Any) -> bool:
    c = str(code or "")
    return c.startswith(("300", "301", "302", "688", "689", "8", "4"))



def dev_lines() -> tuple[float, float]:
    """(破预期线, 深低开线) —— ⛔ 单一源，不在本文件硬编码。

    破预期线取 `ai_caliber.EXPECTED_GAP_DEV_MIN`（gap_zone 同一个常量，环境变量可调）；
    深低开线取 `pool_call.POOL_CALL_DEEP_LOW_DEV`，且豁免开关关掉时**没有这一档**。
    """
    brk, deep = -2.0, -2.0
    try:
        from core import ai_caliber as _ac
        brk = float(_ac.EXPECTED_GAP_DEV_MIN)
    except Exception:                                    # noqa: BLE001
        pass
    deep = brk
    try:
        from core import pool_call as _pc
        if getattr(_pc, "POOL_CALL_DEEP_LOW_RESCUE", True):
            # ⚠️ 常量名是 `DEEP_LOW_OPEN_DEV`（不是 POOL_CALL_DEEP_LOW_DEV，那是环境变量名）
            deep = float(getattr(_pc, "DEEP_LOW_OPEN_DEV", brk))
    except Exception:                                    # noqa: BLE001
        pass
    return brk, deep


def _attitude_by_dev(gap: float, expected_gap: float) -> dict[str, Any]:
    """按 **DEV = 实际开幅 − 该票自己的质量预期** 定态度（与 `gap_zone` 同阈值）。

    ⛔ 这是 2026-11-10 起的**唯一**态度口径。绝对开幅只在「拿不到预期」时才兜底。
    """
    brk, deep = dev_lines()
    dev = round(gap - expected_gap, 2)
    base = {"gap": round(gap, 2), "dev": dev, "expected_gap": round(expected_gap, 2)}
    if dev <= deep:
        return {**base, "attitude": ATT_DEEP_LOW, "label": ATT_LABEL[ATT_DEEP_LOW],
                "note": (f"开在 {gap:+.2f}%，比它该开的 {expected_gap:+.2f}% 低 {abs(dev):.2f}pct"
                         f" —— 被砸下来的错杀档（≤{deep:g} 豁免线），可低吸")}
    if dev < brk:
        return {**base, "attitude": ATT_BREAK, "label": ATT_LABEL[ATT_BREAK],
                "note": (f"开在 {gap:+.2f}%，明显低于它该开的 {expected_gap:+.2f}%"
                         f"（DEV {dev:+.2f} < {brk:g}）→ 未达预期，这条线今天放弃")}
    if dev < 0:
        return {**base, "attitude": ATT_LOW, "label": ATT_LABEL[ATT_LOW],
                "note": (f"开在 {gap:+.2f}%，略低于它该开的 {expected_gap:+.2f}%"
                         f"（DEV {dev:+.2f}，在容忍带内）→ 先看下杀力度，缩量不破就低吸")}
    if dev < 3.0:
        return {**base, "attitude": ATT_FLAT, "label": ATT_LABEL[ATT_FLAT],
                "note": (f"开在 {gap:+.2f}%，符合它该开的 {expected_gap:+.2f}%"
                         f"（DEV {dev:+.2f}）")}
    return {**base, "attitude": ATT_STRONG, "label": ATT_LABEL[ATT_STRONG],
            "note": (f"开在 {gap:+.2f}%，高于它该开的 {expected_gap:+.2f}%"
                     f"（DEV {dev:+.2f}）→ 超预期，观察它能不能带出接力")}


def anchor_read(quote: dict[str, Any] | None, *, code: Any = None,
                expected_range: Any = None,
                expected_gap: Any = None) -> dict[str, Any]:
    """② 锚点态度：只看今天 09:25 怎么开，五档 + 一句原因。

    ⛔ 不预测后续、不看盘中。锚点是「态度」，不是「走势预判」。
    """
    if not isinstance(quote, dict):
        return {"attitude": ATT_UNKNOWN, "label": ATT_LABEL[ATT_UNKNOWN], "gap": None,
                "note": "没拿到竞价报价"}
    prev = _num(quote.get("prev_close"))
    px = _num(quote.get("auction_price"))
    if not prev or not px or prev <= 0:
        return {"attitude": ATT_UNKNOWN, "label": ATT_LABEL[ATT_UNKNOWN], "gap": None,
                "note": "竞价报价不完整"}
    gap = (px - prev) / prev * 100.0
    limit = LIMIT_PCT_20CM if is_20cm(code) else LIMIT_PCT_MAIN
    # 一字：开在涨停价（留 0.15pct 容差防浮点）
    if gap >= limit - 0.15:
        return {"attitude": ATT_YIZI, "label": ATT_LABEL[ATT_YIZI], "gap": round(gap, 2),
                "note": "直接一字顶板：今天不买，看它把跟风带到谁身上"}
    # ⭐ 2026-11-10：有逐票质量预期 → 一律按 DEV 判（与 gap_zone 同一套阈值）。
    _eg = _num(expected_gap)
    if _eg is not None and ANCHOR_ATTITUDE_DEV:
        return _attitude_by_dev(gap, _eg)
    if gap > BIG_GAP_PCT:
        # ⛔ 不再写「买不到」：5% 线 11-05 已废，买不到＝竞价封死（上面 ATT_YIZI 已挡）。
        return {"attitude": ATT_BIG_GAP, "label": ATT_LABEL[ATT_BIG_GAP], "gap": round(gap, 2),
                "note": (f"高开 {gap:+.2f}%（超 {BIG_GAP_PCT:g}%）：这档仍可打，"
                         f"只是压仓位 → 别打满")}
    if gap < BREAK_GAP_PCT:
        return {"attitude": ATT_BREAK, "label": ATT_LABEL[ATT_BREAK], "gap": round(gap, 2),
                "note": f"低开 {gap:+.2f}%，未达预期：这条线今天放弃"}
    if gap < 0:
        return {"attitude": ATT_WEAK, "label": ATT_LABEL[ATT_WEAK], "gap": round(gap, 2),
                "note": f"低开 {gap:+.2f}%，但拿不到它该开的水平可比 → 不裁决，交给量能/身位"}
    if gap >= 2.0:
        out = {"attitude": ATT_STRONG, "label": ATT_LABEL[ATT_STRONG], "gap": round(gap, 2),
               "note": f"强开 {gap:+.2f}%：超预期，观察接力"}
    else:
        out = {"attitude": ATT_FLAT, "label": ATT_LABEL[ATT_FLAT], "gap": round(gap, 2),
               "note": f"平开 {gap:+.2f}%：符合预期"}
    # 盘后预期区间（若有）：低于下沿＝跌破预期，降一档
    exp = expected_range if isinstance(expected_range, (list, tuple)) and len(expected_range) == 2 else None
    if exp:
        try:
            lo = float(exp[0])
        except (TypeError, ValueError):
            lo = None
        if lo is not None and gap < lo:
            out = {**out, "attitude": ATT_BREAK, "label": ATT_LABEL[ATT_BREAK],
                   "below_expected": True,
                   "note": f"开在 {gap:+.2f}%，低于昨天的预期 {lo:g}% 起：未达预期，这条线今天放弃"}
    return out


# ── ③ 线路结论 ——————————————————————————————————————————————————

LINE_BUY_CORE = "buy_core"          # 打本线核心
LINE_BUY_NEIGHBOR = "buy_neighbor"  # 锚点买不到 → 打隔壁
LINE_WATCH = "watch"                # 只看
LINE_ABANDON = "abandon"            # 放弃这条线

LINE_LABEL = {LINE_BUY_CORE: "可打核心", LINE_BUY_NEIGHBOR: "锚点买不到·打隔壁",
              LINE_WATCH: "只看不动", LINE_ABANDON: "今天放弃这条线"}


def line_verdict(attitude: str, *, is_anchor: bool) -> str:
    """锚点态度 → 这条线今天怎么处理。**判断顺序固定**（先放弃、再看能不能买、最后才挑票）：

    ① 破预期 → 整条线放弃（最强否决，不再看第二个指标）
    ② 一字（竞价封死）→ 线强势但锚点真买不到 → 只允许打隔壁
    ②b 大高开（>5%，旧硬线已废）→ **仍打核心**，只是按 `gap_weight` 压仓
    ③ 强开 → 可打核心
    ④ 平开 → 只看，不给出手
    ⑤ 低开未破 → 只看
    ⛔ 锚点本人（is_anchor）在 ② / ③ 里可直接打；④⑤ 一律只看，不给自己开口子。
    """
    if attitude == ATT_BREAK:
        return LINE_ABANDON
    if attitude == ATT_YIZI:
        return LINE_WATCH if is_anchor else LINE_BUY_NEIGHBOR
    if attitude in (ATT_BIG_GAP, ATT_STRONG):
        return LINE_BUY_CORE
    return LINE_WATCH


def market_verdict(attitudes: list[str]) -> dict[str, Any]:
    """全局：把各线锚点态度汇总成「今天整体什么氛围」。

    ⛔ 只用锚点计数，不用指数/情绪分——锚点就是市场自己的态度，指数是滞后的。
    ⛔ **`note` 只陈述事实，`advice` 才是建议**：这一层目前**不进任何裁决**
      （只在 `meta.defense.anchor.market` 留痕），写成「今天整体空仓」会变成
      一句系统并不执行的承诺（2026-11-10 用户「文案和行为一致」）。
    规则：
      · 未达预期 ≥2 条线 → 建议空仓（多点同时崩＝情绪断点）
      · 未达预期 1 条 + 无超预期 → 建议只做最强那条线
      · 有超预期 → 正常，按线各自判断
    """
    a = [x for x in (attitudes or []) if x]
    brk = sum(1 for x in a if x == ATT_BREAK)
    strong = sum(1 for x in a if x == ATT_STRONG)
    yizi = sum(1 for x in a if x == ATT_YIZI)
    if not a:
        return {"level": "unknown", "note": "锚点报价没拿到", "advice": "今天不主动出手",
                "break": 0, "strong": 0, "yizi": 0}
    if brk >= 2:
        return {"level": "off", "note": f"{brk} 条线的锚点未达预期",
                "advice": "情绪断点，建议整体空仓（逐线裁决，系统不强制）",
                "break": brk, "strong": strong, "yizi": yizi}
    if brk == 1 and strong == 0:
        return {"level": "single", "note": "1 条锚点未达预期、没有超预期",
                "advice": "建议只做最强的那一条线",
                "break": brk, "strong": strong, "yizi": yizi}
    return {"level": "normal", "note": f"{strong} 条线超预期、{yizi} 条一字",
            "advice": "按各线自己判断",
            "break": brk, "strong": strong, "yizi": yizi}


# ── 失效条件（明确写死，供卡面/复盘引用）———————————————————————————

FAILURE_RULES = (
    "锚点低开未达预期 → 整条线当日作废（不看第二个指标）",
    "锚点一字/大高开，但线内没有第二只跟（孤军）→ 不买隔壁，空仓",
    "锚点大高开后炸板且 30 分钟内回不去 → 该线当日作废",
    "前排大高开、后排全线低开（分歧）→ 只做锚点本身（若买得到），不做接力",
    "大将军是尾盘才封的（13:30 后）→ 它的次日锚点可信度打折，不做核心",
    "接力标的容量不达标（成交额<5亿 / 封单/流通<0.5% / 换手>25%）→ 不接",
    "位置太高（30 日超额>50% 且断板 ≥3 日后重启）→ 二波反抽，不做",
    "多条线锚点同时未达预期 → 当日整体空仓（情绪断点）",
)
