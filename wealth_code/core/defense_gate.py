#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买入前防守闸门 —— 2026-10-08 大幅回撤的归因经验固化。

背景（用户 2026-10-08 盘后提问）：
  「结合行情逻辑、个股在市场中的板块地位与资金地位、以及当日推送该股的触发原因，
   分析今天推送的这两只票为何出现如此大幅度的回撤，总结回撤的主要成因，
   并总结可复用的判断经验。」

实证（唯一依据＝`wealth_code/cache/ai_nav_history.jsonl` 2026-10-08 行 + 当日卡）：
  当天只买两只，竞价价 → 收盘价：
    · 襄阳轴承(000678)  竞价 +0.85% → 收盘 −5.85%
    · 南华生物(000504)  竞价 +0.40% → 收盘 −8.18%
  平均 −7.02%，净值 1.058172 → 0.983941。

成因（四层，本模块负责其中三层）：

  ① 环境 —— 长假后第一个交易日：昨日 52 只涨停里只有 11 只今天还在板（晋级率 19.2%），
     核按钮 11 只、炸板率 44.7%，情绪 1.0/10。这种日子买「昨天涨停的高位票」胜率只有两成。
     ⚠️ **诚实边界**：当日情绪分要盘中数据才能合成，09:25 时点**不可得**。本模块只能用
     「昨收环境 + 今日开盘读数」，据此把名额降档。**10-08 这一天它不会触发**
     （昨收 4.5 分修复、今开昨涨停 56.9% 红开，都不算差）——这条是给真正冰点日预备的
     第二道防线，不是为了好看。

  ② 方向 —— 襄阳挂靠的「机器人」昨日板块资金净流出 44.31 亿（今日再 −108.7 亿）；
     同日真正的主线是电池（+32.8 亿、8 家涨停）。方向本身在失血，个股再强也是逆风。

  ③ 个股 —— 两只票在**前一天收盘**就同时具备「换手 > 15% + 封单/流通 < 2% + 10:00 后才封板」
     三条特征：高位、筹码在换手、没有资金愿意把封单堆厚、封得还晚 ＝ **派发特征**。
     同日真正走出来的票对照（09-30 候选原始数据）：
       时代万恒 换手 2.61% / 封单 4.96% / 09:25 封板 → 命中 0 条
       紫竹高科 换手 1.77% / 封单 2.38% / 09:25 封板 → 命中 0 条
       龙蟠科技 换手 6.57% / 封单 0.39% / 10:11 封板 → 命中 2 条（未达 3 条门槛）
       襄阳轴承 换手 19.65% / 封单 1.13% / 10:03 封板 → **命中 3 条**
       南华生物 换手 22.51% / 封单 1.52% / 10:22 封板 → **命中 3 条**
     「三条全中」在本例中**精确命中两只亏损票、零误伤**。

  ④ 位置 —— 南华生物实为**第二波反抽**：`cache/zt_history/` 实录 09-21(3板) → 09-22(4板)
     → 断板 4 个交易日（09-25 中秋休市，原按幽灵日误计为 5）→ 09-30 重新涨停（计数被重置成「首板」）。系统的位置维度原本缺失
     ——「首板」这两个字把「这是一只已经涨过一大轮的票」整个盖住了。
     **本轮把它补成独立硬闸**（`position_verdict`）：近 10 日相对大盘超额 ≥45%（或近 30 日
     ≥50%）＝已经涨了一大截，**并且**涨停序列显示「断板 ≥3 个交易日后重新涨停、上一波最高板
     ≥3」＝第二波 → 不占当日名额。
     ⚠️ **与「断板回封是加分项」不矛盾**（⛔ 别把这条读成禁止 N 字）：断 1~2 天的 N 字回封、
       且位置不高 → 仍是加分项；只有「**断了好多天 + 已经涨了一大截**」两条腿同时具备才算接盘。
     数据源＝`cache/zt_history/zt_<交易日>.json`（与本目录的板块资金表同属盘后归档，
     09:25 时点可得；文件缺失/该票序列不足 2 条 → **不裁决**，诚实降级）。

定位（⛔ 边界，不可违反）：
  · 本模块**纯只读**：只把「昨收/今开事实 + 个股封板质量 + 涨停序列」翻译成判据，**不产生 view**。
    裁决唯一源仍是 `core.pool_call.call_pool`（卡面＝账本＝决策三方同源）。
  · 判据四处全部**可逆**（环境 / 方向 / 派发 / 位置），各自一个子开关；总开关
    `ENABLE_DEFENSE_GATE=False` 逐字回退 10-08 前行为。
  · 阈值集中在文件顶部，⛔ 改阈值须同时改宪法【🛡️ 买入前防守闸门】。
  · 数据缺失一律**诚实降级**（不裁决、不编造）：板块资金文件缺失/方向匹配不到 → 不拦；
    涨停序列缺失/不足 → 位置闸不拦。
"""
from __future__ import annotations

import json
import sys
from datetime import date as _date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── 总开关与子开关（架构改动必留可逆开关）────────────────────────────────────
# 总开关：False → pool_call 完全不调用本模块（逐字回退到 2026-10-08 之前的裁决行为）
ENABLE_DEFENSE_GATE = True
# ① 环境名额降档
DEFENSE_ENV_ENABLED = True
# ② 方向资金失血
DEFENSE_SECTOR_DRAIN_ENABLED = True
# ③ 派发特征
DEFENSE_DISTRIBUTION_ENABLED = True
# ③b 派发·组合升级（2026-10-12 收紧）：{封单薄 + 封得晚}（不含大换手）∧ 位置已很高 → 硬闸
DEFENSE_DIST_THIN_LATE_ENABLED = True
# ④ 位置（高位二波）
DEFENSE_POSITION_ENABLED = True
# ⑤ 老龙复燃（炒过的老票沉寂很久后又拉首板）
DEFENSE_OLD_DRAGON_ENABLED = True

# ── ② 方向资金失血 ───────────────────────────────────────────────────────────
# 数据源 = `cache/sector_flow_history/sector_flow_<上一交易日>.json`（盘后 19 点生成，
# 09:25 时点可得）。该文件按日归档且**存在缺口**（实测 09-23/24/25/29 缺）→ 缺失不裁决。
SECTOR_FLOW_DIR = Path(ROOT).parent / "cache" / "sector_flow_history"
SECTOR_DRAIN_MIN_YI = 30.0        # 该方向（同名/含名板块**合计**）净流出 ≥ 30 亿 → 失血
SECTOR_MATCH_MIN_LEN = 2          # 题材名 ≥2 字才参与匹配（防单字泛名乱命中）
SECTOR_MATCH_MAX_BOARDS = 8       # 匹配到的板块数 > 该值 → 题材名过泛（如「其他」命中 16 个）
                                  #   → 不裁决（⛔ 防用泛名把一堆不相关板块的资金加总当方向）
# ⛔ 门槛 30 亿是**校准过**的（不是随手写的）：10-08 案例里
#   · 襄阳 core_theme=机器人 → 命中「机器人概念 −44.31 + 机器人执行器 +2.06」= −42.25 → 拦 ✓
#   · 龙蟠 core_theme=锂电池 → 命中「锂电池 +1.8 + 锂电池概念 −14.68」= −12.88 → **不拦** ✓
#     （若改用聚合族名 theme_name=新能源 会命中「新能源车 −36.4」→ 把当日真主线误杀）
#   即：题材取 core_theme（当日真实炒作题材）优先，门槛 30 亿只抓「显著失血」。

# ── ③ 派发特征（数据源＝候选自身字段，全部取自**前一交易日收盘**）─────────────
DIST_HS_MIN = 15.0                # 换手率（%）≥ 该值
DIST_SEAL_RATIO_MAX = 2.0         # 封单 / 流通市值（%）< 该值
DIST_FBT_LATE_HM = 1000           # 封板时间（HHMM）≥ 10:00 算「封得晚」
DIST_BLOCK_HITS = 3               # 三条全中 → 硬闸（不占名额）
DIST_WARN_HITS = 2                # 两条 + 10 日位置偏高 → 只留痕警示
DIST_HIGH_POSITION_DEV10 = 40.0   # ⚠️ 旧口径：只认近 10 日相对指数超额（仅用于 warn 留痕）
# ③b 组合升级（2026-10-12 收紧·用户「如果是指标问题，收一收」·国芳集团漏网案例）：
#   「封单薄 + 封得晚」这两条（**不含**大换手）＋ 位置**已经很高** → 与三条全中同等危险，
#   直接不占名额。⛔ 这里的「位置高」改用 ④ 位置闸的**同一口径**（dev10 ≥45 或 dev30 ≥50）
#   —— 原实现只认 `dev_10d_pct ≥40`，而国芳集团 10 日只涨 3.9%、30 日已多涨 97.8%
#   → 旧口径判「不高」→ 连 warn 都没有；次日 10-12 竞价 +2.41% 买入、收盘 −12.09%。
#   ⛔ 只收紧这一对组合：实测「换手大 + 封单薄」那条 55 个可成交样本里含闽东电力 +7.32%、
#   传艺科技 +10.92% 等大赢家 → 一并收紧会砍掉真龙头（新增 41 条拦截，已被数据否证）。

# ── ① 环境名额降档 ───────────────────────────────────────────────────────────
ENV_SENTIMENT_MIN = 2.0           # 昨收情绪分 ≤ 该值算弱
ENV_PROMOTE_MIN = 25.0            # 昨收晋级率（%）< 该值算弱
ENV_NUKE_MAX = 8                  # 昨收核按钮（家）> 该值算弱
ENV_BREAK_MIN = 2                 # 昨收真破位（家）≥ 该值算弱
ENV_BROKEN_RATE_MAX = 40.0        # 昨收炸板率（%）≥ 该值算弱
ENV_RED_OPEN_MIN = 0.40           # 今日开盘：昨涨停整体红开比例 ≤ 该值算弱
ENV_LEADER_RED_OPEN_MIN = 0.35    # 今日开盘：昨连板红开比例 ≤ 该值算弱
ENV_MIN_COVERAGE = 0.70           # 今日开盘读数样本覆盖 < 该值 → 不参与判定
ENV_DEFENSIVE_HITS = 2            # 命中 ≥2 条 → 名额压到 1 只
ENV_BLOCK_HITS = 3                # 命中 ≥3 条 → 当日不主动买（全置观察）
DEFENSIVE_QUOTA_CAP = 1
POST_HOLIDAY_GAP_DAYS = 4         # 上一交易日距今 ≥4 个自然日 → 长假后首日（只提示）

# ── ④ 位置（高位二波）────────────────────────────────────────────────────────
# 数据源 = `cache/zt_history/zt_<交易日>.json`（日频归档·盘后写·09:25 时点可得）。
# 只取 ≤ 依据日（T-1）的文件；空文件（节假日占位·zt_list=[]）不计入交易日历。
ZT_HISTORY_DIR = Path(ROOT).parent / "cache" / "zt_history"
POSITION_DEV10_MIN = 45.0         # 近 10 日相对大盘超额（%）≥ 该值算「已经涨了一大截」
POSITION_DEV30_MIN = 50.0         # 近 30 日相对大盘超额（%）≥ 该值算「涨幅已经很大」（与上条取或）
POSITION_PRIOR_BOARD_MIN = 3      # 上一波最高板 ≥ 该值才算「上一波是连板主线」
POSITION_GAP_MIN_DAYS = 3         # 断板间隔 ≥ 该值（交易日）算「断板多日后重启（二波）」
POSITION_LOOKBACK_DAYS = 20       # 涨停序列回看窗口（交易日）
# ⛔ 门槛校准（10-08 实测，零误伤）：
#   南华生物 000504  dev10=50.3 → 高位 ✓；序列 09-21(3板)→09-22(4板)→断板 4 天→09-30(1板)
#     → prior_max=4 ≥3 ✓、gap=5 ≥3 ✓ → **拦**（当日 −8.18%）
#   襄阳轴承 000678  dev10=51.1 → 高位 ✓；序列 09-24~09-30 连续 → gap=0 → **位置闸不拦**
#     （它由派发闸拦下；两条闸各管一段，不重复计数）
#   同日走出来的票（龙蟠 603906 dev10=6.3、时代万恒、紫竹高科）→ 位置都不高 → **零误伤**

# ── ⑤ 老龙复燃（数据源同 ④＝`cache/zt_history/`）────────────────────────────────
# 国芳集团案例（2026-10-12 用户抓包）：9 月已炒过 2 连板、断板 7 个交易日后
# 10-09 又拉首板，被当成「低位新方向首板」买入 → 次日 −8.5%。同期另一只老龙
# 哈药股份当日跌停（那批老票**整体**已退潮的写照）。
OLD_DRAGON_MIN_BOARD = 2          # 上一波最高板 ≥ 该值 ＝ 前期老龙（炒过一轮）
OLD_DRAGON_GAP_MIN = 5            # 断板 ≥ 该值（交易日）＝ 沉寂够久，**不是** N 字快速回封
OLD_DRAGON_REBOUND_MAX_BOARD = 1  # 复燃首日板数 ≤ 该值（＝刚从 0 到首板）
# ⛔ 门槛校准（2026-10-12 实测·全历史仅 7 次触发，拦掉它避开的损失 > 拦错的收益）：
#   国芳集团 601086  上一波 2 板@09-21、断 7 日、10-09 首板 → 拦 ✓（次日 −8.5%）
#   超声电子 000823  09-29 触发 → 之后连续阴跌 −16% ✓
#   众泰汽车 000980  09-28 触发 → 阴跌 −4.5% ✓
#   万向德农 600371  10-09 触发 → −2.5% ✓
#   金健米业 600127  10-09 触发 → +1.8%（唯一小赚）
#   启明信息 002232 / 英联股份 002846  触发后震荡（拦错，代价可控）


# ─────────────────────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────────────────────
def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None                       # 挡 NaN


def _fbt_hm(v: Any) -> int | None:
    """封板时间 → HHMM 整数（'093129' → 931、'10:03' → 1003）。缺失/异常 → None。"""
    digits = "".join(ch for ch in str(v or "") if ch.isdigit())
    if len(digits) < 4:
        return None
    try:
        hh, mm = int(digits[:2]), int(digits[2:4])
    except ValueError:
        return None
    if hh > 23 or mm > 59:
        return None
    return hh * 100 + mm


def _fmt_hm(hm: int | None) -> str:
    return f"{hm // 100:02d}:{hm % 100:02d}" if isinstance(hm, int) else "—"


def _pos10(it: dict[str, Any]) -> float | None:
    """位置读数：近 10 日相对指数超额（优先），兜底近 10 日绝对涨幅。"""
    for k in ("dev_10d_pct", "ret_10d_pct"):
        v = _num(it.get(k))
        if v is not None:
            return v
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ③ 派发特征 —— 「高位 + 大换手 + 薄封单 + 晚封板」
# ─────────────────────────────────────────────────────────────────────────────
def seal_quality(it: dict[str, Any] | None) -> dict[str, Any]:
    """个股封板质量读数（纯计算·不外呼）。字段缺失 → None，不编造。"""
    it = it if isinstance(it, dict) else {}
    hs = _num(it.get("hs"))
    seal_amt, lt_mktcap = _num(it.get("seal_amt")), _num(it.get("lt_mktcap"))
    seal_ratio = (seal_amt / lt_mktcap * 100.0
                  if seal_amt is not None and lt_mktcap and lt_mktcap > 0 else None)
    return {
        "hs_pct": hs,
        "seal_ratio_pct": round(seal_ratio, 2) if seal_ratio is not None else None,
        "fbt_hm": _fbt_hm(it.get("fbt")),
        "pos10_pct": _pos10(it),
    }


def distribution_verdict(it: dict[str, Any] | None) -> dict[str, Any]:
    """派发特征判据（**只读**）。

    level：
      · "block" → ① 三条全中（换手大 ∧ 封单薄 ∧ 封得晚）＝大资金借涨停往外倒货 → 不占名额
                  ② 「封单薄 + 封得晚」两条 ＋ **位置已经很高**（2026-10-12 收紧）
      · "warn"  → 中两条且 10 日位置偏高（旧口径）→ 卡面留痕警示，不否决
      · "none"  → 其余 / 数据不足

    `kind` ∈ `triple` / `thin_late_high` / `warn` / `""` —— 两种脏法的话术不同，
    裁决链据此落不同的卡面标签（`pool_call` → `defense_gate` 码）。

    ⛔ 2026-10-12 收紧（用户「如果是指标问题，收一收」）：原实现的「位置高」只认
      `_pos10`（＝`dev_10d_pct`，门槛 40），而 ④ 位置闸认 `dev10 ≥45 或 dev30 ≥50`
      —— **同一个「位置高」两套口径**。国芳集团(601086) 10-09 正是从这个缝漏过去的：
      换手仅 10.6%（第一条不中）、封单只占流通 0.60%（中）、10:16 才封板（中），
      10 日只涨 3.9% → 旧口径判「不高」→ 连 warn 都没有；但它 30 日相对大盘已多涨 97.8%。
      次日 10-12 竞价 +2.41% 被买入 → 收盘 **−12.09%**。
      收紧后「封单薄 + 封得晚 + 位置高」直接 block，且位置口径与 ④ 位置闸统一。
    ⛔ 校准（469 条候选行 · 只算**真会成交**的样本＝竞价红开且 ≤5%，竞价买→收盘）：
      新增拦截 7 条，其中可成交的 3 条 ＝ 威尔高 −9.48% / 国芳集团 −12.09% / 会稽山 +2.60%
      （均值 −6.32%）。**不同步收紧**「换手大 + 封单薄」那条（含闽东电力等大赢家）。
    """
    it = it if isinstance(it, dict) else {}
    q = seal_quality(it)
    hs, ratio, fbt = q["hs_pct"], q["seal_ratio_pct"], q["fbt_hm"]
    facts: dict[str, Any] = {}
    hits: list[str] = []

    big_turn = hs is not None and hs >= DIST_HS_MIN
    thin_seal = ratio is not None and ratio < DIST_SEAL_RATIO_MAX
    late_seal = fbt is not None and fbt >= DIST_FBT_LATE_HM

    if hs is not None:
        facts["hs_pct"] = round(hs, 2)
        if big_turn:
            hits.append(f"换手 {hs:.1f}%（超过 {DIST_HS_MIN:.0f}% 就是筹码在大换手）")
    if ratio is not None:
        facts["seal_ratio_pct"] = ratio
        if thin_seal:
            hits.append(f"封单只占流通盘的 {ratio:.2f}%（没有资金愿意把封单堆厚）")
    if fbt is not None:
        facts["fbt_hm"] = fbt
        if late_seal:
            hits.append(f"{_fmt_hm(fbt)} 才封板（封得晚，是被动跟出来的）")
    pos = q["pos10_pct"]
    high_warn = pos is not None and pos >= DIST_HIGH_POSITION_DEV10
    if pos is not None:
        facts["pos10_pct"] = round(pos, 1)

    # 高位读数与 ④ 位置闸同口径（dev10 ≥45 或 dev30 ≥50）——2026-10-12 统一
    high_strong, pos_facts = _high_position(it)
    facts["high_dev10_pct"] = pos_facts.get("dev10_pct")
    facts["high_dev30_pct"] = pos_facts.get("dev30_pct")

    n = len(hits)
    # ③b「封单薄 + 封得晚」这一对（不含大换手）＋ 位置已经很高
    thin_late = bool(DEFENSE_DIST_THIN_LATE_ENABLED and thin_seal and late_seal
                     and not big_turn)
    kind = ""
    if n >= DIST_BLOCK_HITS:
        level, kind = "block", "triple"
    elif thin_late and high_strong:
        level, kind = "block", "thin_late_high"
    elif n >= DIST_WARN_HITS and high_warn:
        level, kind = "warn", "warn"
    else:
        level = "none"

    note = ""
    if kind == "triple":
        note = ("昨天它 " + "、".join(hits) +
                "——三样凑一起就是大资金在借涨停往外倒货")
        if high_warn:
            note += f"，而且 10 天已经涨了 {pos:.0f}%，位置也高了"
    elif kind == "thin_late_high":
        note = ("昨天它 " + "、".join(hits) +
                "——封得又晚、封单又薄，钱不愿意往上做")
        _d10, _d30 = pos_facts.get("dev10_pct"), pos_facts.get("dev30_pct")
        # 说**真正触发**的那条腿：10 日够高就说 10 日，否则说 30 日
        # （国芳集团就是 30 日的账：10 日只涨 3.9%、30 日已多涨 97.8%）
        if _d10 is not None and _d10 >= POSITION_DEV10_MIN:
            _pos_txt = f"10 天已经涨了 {_d10:.0f}%"
        elif _d30 is not None:
            _pos_txt = f"30 天已经涨了 {_d30:.0f}%"
        else:
            _pos_txt = "股价已经拉得很高"
        note += f"，而且它{_pos_txt}，人在高位，接力等于给前面的人抬轿子"
    elif kind == "warn":
        note = ("昨天它 " + "、".join(hits) +
                f"，加上 10 天已经涨了 {pos:.0f}%，位置偏高，留神派发")
    return {"level": level, "kind": kind, "hits": hits, "hit_count": n, "facts": facts,
            "high_position": bool(high_warn), "high_position_strong": bool(high_strong),
            "note": note}


# ─────────────────────────────────────────────────────────────────────────────
# ② 方向资金失血
# ─────────────────────────────────────────────────────────────────────────────
def load_sector_flow(date10: str, *, root: Path | None = None) -> dict[str, dict[str, Any]]:
    """读 `sector_flow_<date10>.json` 的 `boards` 表。缺失/异常 → {}（诚实降级）。"""
    d = Path(root) if root else SECTOR_FLOW_DIR
    p = d / f"sector_flow_{str(date10 or '').strip()}.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    boards = (data or {}).get("boards")
    return {str(k): v for k, v in (boards or {}).items() if isinstance(v, dict)}


def sector_flow_sum(theme_name: Any, boards: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """该方向的板块资金合计（亿元）。

    匹配＝**同名或含名**（题材名 ≥2 字）：个股的题材名是炒作名（「机器人」），
    资金表的 key 是官方板块名（「机器人概念」/「机器人执行器」），故按包含关系聚合**合计**
    ——⛔ 不取单条最负值，否则「医药」会被「医药商业 −1.07 亿」这种零头误判成失血
    （族内合计其实净流入 98.7 亿）。

    ⛔ 过泛保护：同名/含名命中 **> `SECTOR_MATCH_MAX_BOARDS`** 个板块 → 说明这个题材名
    是泛名（「其他」能命中 16 个「其他XX」板块）→ 返回 `sum_yi=None`（不裁决），
    否则等于把一堆不相关板块的资金加总当方向。
    """
    name = str(theme_name or "").strip()
    if len(name) < SECTOR_MATCH_MIN_LEN or not isinstance(boards, dict) or not boards:
        return {"sum_yi": None, "matched": [], "theme": name, "too_broad": False}
    matched: list[tuple[str, float]] = []
    for k, v in boards.items():
        if name == k or name in k:
            f = _num(v.get("flow_yi"))
            if f is not None:
                matched.append((k, f))
    if not matched:
        return {"sum_yi": None, "matched": [], "theme": name, "too_broad": False}
    detail = [{"board": k, "flow_yi": round(f, 2)} for k, f in matched]
    if len(matched) > SECTOR_MATCH_MAX_BOARDS:
        return {"sum_yi": None, "matched": detail, "theme": name, "too_broad": True}
    return {
        "sum_yi": round(sum(f for _, f in matched), 2),
        "matched": detail,
        "theme": name,
        "too_broad": False,
    }


def sector_drain_verdict(it: dict[str, Any] | None,
                         boards: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """方向资金失血判据（**只读**）。方向数据缺失/匹配不到/题材名过泛 → 不裁决（诚实降级）。

    ⛔ 题材取 `core_theme`（当日真实炒作题材）优先，兜底 `theme_name`（聚合族名）→ `sector`。
    用族名会出错：龙蟠科技 core_theme=锂电池（−12.9 亿，不拦），而 theme_name=新能源
    （命中「新能源车 −36.4 亿」→ 会把当日真主线误杀）。
    """
    it = it if isinstance(it, dict) else {}
    theme = None
    for k in ("core_theme", "theme_name", "sector"):
        v = str(it.get(k) or "").strip()
        if len(v) >= SECTOR_MATCH_MIN_LEN and v != "—":
            theme = v
            break
    agg = sector_flow_sum(theme, boards)
    total = agg["sum_yi"]
    base = {"drained": False, "known": False, "theme": agg["theme"],
            "sum_yi": None, "matched": agg["matched"], "too_broad": agg["too_broad"],
            "note": ""}
    if total is None:
        return base
    drained = total <= -abs(SECTOR_DRAIN_MIN_YI)
    note = ""
    if drained:
        note = (f"它这条线（{agg['theme']}）昨天板块资金净流出 {abs(total):.1f} 亿，"
                f"方向本身在失血")
    return {**base, "drained": drained, "known": True, "sum_yi": total, "note": note}


def sector_drain_verdict_from_rows(it: dict[str, Any] | None,
                                   date10: str) -> dict[str, Any]:
    """按日期读文件再判（调用方没预载 `boards` 时的兜底入口）。"""
    return sector_drain_verdict(it, load_sector_flow(date10))


# ─────────────────────────────────────────────────────────────────────────────
# ④ 位置（高位二波）—— 连板计数会被断板重置，系统看不见「高位」
# ─────────────────────────────────────────────────────────────────────────────
def _boards_of_row(r: dict[str, Any]) -> int:
    for k in ("boards", "lbc", "lianban", "lbc_num"):
        v = r.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return 0


def _load_zt_series_legacy(*, days: int = POSITION_LOOKBACK_DAYS,
                            upto: str | None = None,
                            root: Path | None = None) -> tuple[dict[str, list[tuple[str, int]]], list[str]]:
    """旧归档读取实现；显式传 root 时只走这里，供回放和测试隔离。"""
    d = Path(root) if root else ZT_HISTORY_DIR
    if not d.exists():
        return {}, []
    try:
        files = sorted(d.glob("zt_*.json"))
    except Exception:
        return {}, []
    cutoff = str(upto).strip()[:10] if upto else None
    days_list: list[str] = []
    series: dict[str, list[tuple[str, int]]] = {}
    for f in files:
        day = f.stem[3:]
        if len(day) != 10:
            continue
        if cutoff and day > cutoff:
            continue
        try:
            z = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = [r for r in ((z or {}).get("zt_list") or []) if isinstance(r, dict)]
        if not rows:
            continue                                  # 空文件 → 非交易日占位，不进日历
        days_list.append(day)
        for r in rows:
            code = str(r.get("code") or r.get("dm") or "").strip()
            if len(code) != 6:
                continue
            series.setdefault(code, []).append((day, _boards_of_row(r)))
    if len(days_list) > days:
        days_list = days_list[-days:]
        keep = set(days_list)
        series = {c: [(d0, b) for d0, b in v if d0 in keep] for c, v in series.items()}
    series = {c: sorted(v, key=lambda x: x[0]) for c, v in series.items() if v}
    return series, days_list


def load_zt_series(*, days: int = POSITION_LOOKBACK_DAYS,
                   upto: str | None = None,
                   root: Path | None = None) -> tuple[dict[str, list[tuple[str, int]]], list[str]]:
    """读取统一个股底库的收盘涨停序列，旧归档仅作显式回放/缺料降级。

    ``root`` 是测试/历史回放注入点，传入时保持旧 JSON 读取语义；生产默认
    优先读 ``stock_registry.stock_daily``，避免业务层继续把 zt_history 当事实主源。
    """
    if root is not None:
        return _load_zt_series_legacy(days=days, upto=upto, root=root)
    try:
        from adapters.stock_registry_reader import load_zt_series_from_registry

        series, trading_days = load_zt_series_from_registry(days=days, upto=upto)
        if series and trading_days:
            return series, trading_days
    except Exception:
        pass
    # 读取灰度/底库冷启动缺料时，保留旧归档作为可逆降级，不改变裁决结果形状。
    return _load_zt_series_legacy(days=days, upto=upto)


def _high_position(it: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """高位读数（相对大盘超额优先·绝对涨幅兜底）。数据缺 → (False, 事实表)。"""
    dev10 = _num(it.get("dev_10d_pct"))
    if dev10 is None:
        dev10 = _num(it.get("ret_10d_pct"))
    dev30 = _num(it.get("dev_30d_pct"))
    if dev30 is None:
        dev30 = _num(it.get("ret_30d_pct"))
    high = bool((dev10 is not None and dev10 >= POSITION_DEV10_MIN)
                or (dev30 is not None and dev30 >= POSITION_DEV30_MIN))
    facts = {
        "dev10_pct": round(dev10, 1) if dev10 is not None else None,
        "dev30_pct": round(dev30, 1) if dev30 is not None else None,
        "dev10_min": POSITION_DEV10_MIN, "dev30_min": POSITION_DEV30_MIN,
    }
    return high, facts


def position_wave(code: str,
                  series: dict[str, list[tuple[str, int]]] | None = None,
                  trading_days: list[str] | None = None) -> dict[str, Any]:
    """该票的「第几波」读数（纯计算·不外呼）。

    断板间隔 = 交易日历里「最近两次涨停之间空了几个交易日」。
    `is_rebound` = 间隔 ≥ `POSITION_GAP_MIN_DAYS`（＝断板多日后重启，第二波）。
    数据不足（该票序列 <2 条 / 交易日历缺失）→ `known=False`（调用方据此不裁决）。
    """
    ser = (series or {}).get(str(code or "").strip()) or []
    days = list(trading_days or [])
    base = {"known": False, "is_rebound": False, "gap_days": None,
            "prior_max_board": None, "last_board": None,
            "prev_date": None, "last_date": None}
    if len(ser) < 2 or not days:
        return base
    idx = {d: i for i, d in enumerate(days)}
    last_date, last_board = ser[-1]
    prev_date, prev_board = ser[-2]
    i_last, i_prev = idx.get(last_date), idx.get(prev_date)
    if i_last is None or i_prev is None:
        return base
    gap = i_last - i_prev - 1
    prior_max = max([b for _, b in ser[:-1]] or [0])
    return {"known": True, "is_rebound": gap >= POSITION_GAP_MIN_DAYS,
            "gap_days": gap, "prior_max_board": prior_max,
            "last_board": last_board, "prev_board": prev_board,
            "prev_date": prev_date, "last_date": last_date}


def position_verdict(it: dict[str, Any] | None,
                     series: dict[str, list[tuple[str, int]]] | None = None,
                     trading_days: list[str] | None = None) -> dict[str, Any]:
    """位置判据（**只读**）。

    level：
      · "block" → 高位 ∧ 二波（断板 ≥3 个交易日后重启 ∧ 上一波最高板 ≥3）＝接盘位 → 不占名额
      · "warn"  → 只是位置高（非二波）→ 卡面留痕，不否决
      · "none"  → 其余 / 数据不足
    ⛔ 「断板回封（N 字二波）」本身是加分项；只有**同时**满足「断了好多天 + 已经涨了一大截」
    才拦——所以 `gap_days` 门槛取 3（断 1~2 天的快速 N 字不拦）。
    """
    it = it if isinstance(it, dict) else {}
    code = str(it.get("code") or "").strip()
    high, facts = _high_position(it)
    wave = position_wave(code, series, trading_days)
    prior_ok = (wave.get("prior_max_board") or 0) >= POSITION_PRIOR_BOARD_MIN
    is_rebound = bool(wave.get("is_rebound"))

    if high and wave.get("known") and is_rebound and prior_ok:
        level = "block"
    elif high:
        level = "warn"
    else:
        level = "none"

    note = ""
    if level == "block":
        pos_txt = (f"10 天涨了 {facts['dev10_pct']:.0f}%" if facts.get("dev10_pct") is not None
                   else f"30 天涨了 {facts['dev30_pct']:.0f}%")
        note = (f"它 {wave.get('prev_date')} 那波最高到 {wave.get('prior_max_board')} 板，"
                f"断板 {wave.get('gap_days')} 个交易日后又重新涨停——这是第二波反抽，"
                f"而且 {pos_txt}，位置已经很高，接力等于给前面的人抬轿子")
    elif level == "warn":
        if facts.get("dev10_pct") is not None:
            note = (f"它 10 天已经涨了 {facts['dev10_pct']:.0f}%（相对大盘超额），位置偏高，"
                    f"再接力性价比会差")
        elif facts.get("dev30_pct") is not None:
            note = (f"它 30 天已经涨了 {facts['dev30_pct']:.0f}%（相对大盘超额），位置偏高，"
                    f"再接力性价比会差")
    return {"level": level, "high_position": bool(high), "facts": facts,
            "wave": wave, "prior_board_ok": bool(prior_ok), "note": note}


def position_verdict_from_disk(it: dict[str, Any] | None, date10: str) -> dict[str, Any]:
    """按依据日读 zt_history 再判（调用方没预载序列时的兜底入口）。"""
    series, days = load_zt_series(upto=date10)
    return position_verdict(it, series, days)


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ 老龙复燃 —— 「炒过的老票沉寂很久后又拉首板」≠「低位新方向首板」
# ─────────────────────────────────────────────────────────────────────────────
def old_dragon_verdict(it: dict[str, Any] | None,
                       series: dict[str, list[tuple[str, int]]] | None = None,
                       trading_days: list[str] | None = None) -> dict[str, Any]:
    """老龙复燃判据（**只读**）。

    与 ④ 位置闸同源（`cache/zt_history/`），但管的是**另一段**：
      · 位置闸管「**已经涨了一大截** + 断板后重启」＝ 高位接盘；
      · 本闸管「**上一波已经炒过** + 沉寂很久 + 复燃首日只有首板」＝ 老票重新拉板。
    国芳集团上一波只到 2 板、位置也不高 → 位置闸漏（要求上一波 ≥3 板），本闸补上。

    level：
      · "block" → 前期老龙（回看窗口内曾 ≥`OLD_DRAGON_MIN_BOARD` 连板）
                  × 断板 ≥`OLD_DRAGON_GAP_MIN` 交易日 × 复燃首日仅首板 → 不占名额
      · "none"  → 其余 / 数据不足（诚实降级，不裁决）
    ⛔ 断 1~2 天的 N 字快速回封（情绪核心的经典形成路径·加分项）**不受影响**——
      `OLD_DRAGON_GAP_MIN=5` 把它们排除在外。
    """
    it = it if isinstance(it, dict) else {}
    code = str(it.get("code") or "").strip()
    wave = position_wave(code, series, trading_days)
    facts = {
        "prior_max_board": wave.get("prior_max_board"),
        "gap_days": wave.get("gap_days"),
        "last_board": wave.get("last_board"),
        "prev_date": wave.get("prev_date"),
        "last_date": wave.get("last_date"),
    }
    base = {"level": "none", "note": "", "wave": wave, "facts": facts,
            "known": bool(wave.get("known"))}
    if not wave.get("known"):
        return base
    prior_max, gap, last_b = (wave.get("prior_max_board"), wave.get("gap_days"),
                              wave.get("last_board"))
    if (prior_max is None or gap is None or last_b is None
            or prior_max < OLD_DRAGON_MIN_BOARD or gap < OLD_DRAGON_GAP_MIN
            or last_b > OLD_DRAGON_REBOUND_MAX_BOARD):
        return base
    note = (f"它 {wave.get('prev_date')} 那波最高到 {prior_max} 板，"
            f"之后断了 {gap} 个交易日，{wave.get('last_date')} 才又封了个首板——"
            f"这是炒过的老票重新拉板，不是新方向的首板，容易一日游")
    return {**base, "level": "block", "note": note}


def old_dragon_verdict_from_disk(it: dict[str, Any] | None, date10: str) -> dict[str, Any]:
    """按依据日读 zt_history 再判（调用方没预载序列时的兜底入口）。"""
    series, days = load_zt_series(upto=date10)
    return old_dragon_verdict(it, series, days)


# ─────────────────────────────────────────────────────────────────────────────
# ① 环境名额降档
# ─────────────────────────────────────────────────────────────────────────────
def _days_since(prev_date10: Any) -> int | None:
    try:
        d = _date.fromisoformat(str(prev_date10)[:10])
    except Exception:
        return None
    return (_date.today() - d).days


def env_verdict(environment: dict[str, Any] | None,
                market_open: dict[str, Any] | None,
                *,
                prev_date10: str | None = None) -> dict[str, Any]:
    """环境判据（**只读**）→ 名额档位。

    两条腿：
      · 昨收环境（`candidates.environment`，盘后 feedback_matrix 快照）—— 既定事实
      · 今日开盘（`core.market_open.build_market_open` 的读数）—— 09:25 实况
    level：normal / defensive（名额压到 1 只）/ block（当日不主动买）。
    ⛔ 长假后首日只写进 `notes` 提示，不改变 level（日历效应不是硬证据）。
    """
    env = environment if isinstance(environment, dict) else {}
    mo = market_open if isinstance(market_open, dict) else {}
    weak: list[str] = []
    notes: list[str] = []

    s = _num(env.get("sentiment_score"))
    if s is not None and s <= ENV_SENTIMENT_MIN:
        weak.append(f"昨天收盘做多气氛只有 {s:.1f} 分")
    pr = _num(env.get("promote_rate"))
    if pr is not None and pr < ENV_PROMOTE_MIN:
        weak.append(f"昨天涨停的票今天还在板的只有 {pr:.1f}%")
    nk = _num(env.get("nuke_count"))
    if nk is not None and nk > ENV_NUKE_MAX:
        weak.append(f"昨天涨停今天大跌的有 {nk:.0f} 家")
    bk = _num(env.get("break_count"))
    if bk is not None and bk >= ENV_BREAK_MIN:
        weak.append(f"真跌破五日线十日线的有 {bk:.0f} 家")
    br = _num(env.get("broken_rate"))
    if br is not None and br >= ENV_BROKEN_RATE_MAX:
        weak.append(f"炸板率 {br:.1f}%")

    # 今日开盘腿（样本覆盖不足不判）
    cov = _num(mo.get("coverage"))
    if (mo.get("enabled") and cov is not None and cov >= ENV_MIN_COVERAGE):
        rr = _num(mo.get("red_open_ratio"))
        if rr is not None and rr <= ENV_RED_OPEN_MIN:
            weak.append(f"今天开盘昨天涨停的票只有 {rr * 100:.0f}% 红开")
        lr = _num(mo.get("leader_red_open_ratio"))
        if lr is not None and lr <= ENV_LEADER_RED_OPEN_MIN:
            weak.append(f"其中连板的只有 {lr * 100:.0f}% 红开")

    gap = _days_since(prev_date10) if prev_date10 else None
    if gap is not None and gap >= POST_HOLIDAY_GAP_DAYS:
        notes.append(f"距上个交易日已经 {gap} 天（长假后第一个交易日），"
                     f"这种日子高开回落的居多，出手更要挑")

    n = len(weak)
    level = "block" if n >= ENV_BLOCK_HITS else ("defensive" if n >= ENV_DEFENSIVE_HITS else "normal")
    return {"level": level, "hits": weak, "hit_count": n, "notes": notes}
