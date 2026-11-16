#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘后流水线：涨停池 → 龙头分 → 接力/观察池候选（轻量门控版）

这是财富密码接入狸猫猎手的“生成候选”一端。与 quant-review 的 zt_analysis
（2619 行、耦合 xuangubao/eastmoney 数据源）不同，这里用一只干净的轻量实现：
- 龙头分：直接复用 core/leader.py 的纯算法（点火+爆发+先发+题材加分）
- 接力/观察门控：用 feedback_matrix 情绪代理 + 连板高度 + 主板倾斜 做轻量排序
  （取代原版写死的潮汐魔法数字，便于后续用 AI 语义层替换）

产出：cache/candidates-YYYY-MM-DD.json
{
  "date": ..., "regime": ..., "relay": [...], "watch": [...], "diagnostics": {...}
}
每个候选含：code/name/sector/price/pct/boards/leader_score/is_20cm/relay_rank/
watch_rank/expectation/{gap/super/amount}/tags
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from core.leader import (    _build_theme_stats,
    _quantile,
    _theme_priority,
    _time_hhmmss_to_sec,
    _to_float,
    _to_int,
    build_sector_ladder,
    pick_primary_theme,
    score_explosion,
    score_ignition,
    score_leadership,
)
from adapters.input_zt import load_environment, load_zt_pool
from adapters.input_env import load_pulse
from adapters.daily_verdict import digest_daily_verdict, match_candidate
from core.expectation_ai import build_ai_expectation
from core.dimension_score import build_dimension_panel, relay_selection_mode_for
from core.quant_score import select_relay_watch
from core import pool_admit                     # 源头准入闸（2026-10-21 用户「后排的票不要推到这里面来」）
from core import zt_gene as _zt_gene            # 股性判据单一真源（2026-10-22 与卡面共用）
from core import defense_gate as _defense_gate  # 位置判据（雷达与 09:25 防守闸同一套口径）
# 封板强度闸（2026-11-03 用户「重新定量」）：成交额从硬门槛降级为四因素之一。
# 强封（一字/竞价封+厚封单）与极热题材（同题材≥8家）可在低成交额下放行。
# ⛔ 只决定候选准入，不改评分/排序/名额/仓位；`ENABLE_SEAL_GATE=0` 一键回旧行为。
from core import seal_gate as _seal_gate
# 全量过筛（2026-11-04 用户「每天把涨停股全部过一遍」）：逐票 pass/watch_only/reject，
# 本闸只做减法。`ENABLE_DAILY_SCREEN=0` 一键回旧链路。
from core import daily_screen as _daily_screen
from core.historical_relay import annotate_historical_relay

# ⛔ `common/` 在**项目根**、不在 wealth_code/ 下；以脚本/CLI 方式运行时 cwd 不入 sys.path，
#    调用方只把 wealth_code 导进 PYTHONPATH 就会在此行抛 ModuleNotFoundError。
#    2026-10-01 实证：launchd 竞价卡整轮 import 期即崩（verify RC=1，当日无卡）。
#    与 core/ai_caliber.py、adapters/input_auction.py 同法：本模块自插项目根。
_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))
from common.trading_calendar import next_a_share_trading_day  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
DATA_DIR = ROOT / "data"
# 涨停池历史**单一源**（2026-09-12 N1 修复）：
# ⛔ 曾分裂为两份 —— wealth_code/data/zt_history（本文件第 663 行 + theme_roll 读）
#    与 项目 cache/zt_history（live_signal/theme_rotation/capture_daily/theme_data 用）。
#    两者内容同源同数据（差异仅 intraday_points），但窗口与剪枝策略不同，
#    且 648 行曾误读不存在的 wealth_code/cache/zt_history → 「弱转强」标注静默失效。
#    现统一读项目 cache/zt_history（capture_daily 写 zt_list + theme_data 写 intraday_points）。
ZT_HIST_DIR = ROOT.parent / "cache" / "zt_history"

BJ_TZ = timezone(timedelta(hours=8))

# ============================================================================
# 选股排序权重配置（纯模型择优；全仓无任何股票代码级硬编码 / 持仓加权）
# 三大核心驱动：龙头算法因子(leader_score) / 板块题材(theme) / 板块梯队(ladder)
# 次要驱动：连板高度(height) / 容量(capacity, QR对齐)
# 元覆盖：日报 verdict（用户明确"相信日报判断"，属外部定性覆盖，非硬编码，可一键关）
# ============================================================================
# 创业板开放（2026-08-13 反转 08-04「主板倾斜」纪律）：主板/创业板对称参与，
# 不再对主板排序加分；MAINBOARD_BONUS 保留为 0 仅作历史兼容，不再使用。
MAINBOARD_BONUS = 0.0
# 反吃日报 verdict 的板块效应权重（用户纪律：板块无效应→降权，确认主线→加权）
# avoid_breadth=量化铺广度/避险轮动(无板块效应) → 大幅降权；observe=回流未确认 → 小幅降权；
# main=唯一穿越主线(板块效应最强) → 加权
SECTOR_VERDICT_BONUS = {
    "avoid_breadth": -20.0,
    "observe": -8.0,
    "main": 8.0,
}
# 接力池 / 观察池容量
RELAY_TOPN = 8
WATCH_TOPN = 10
# 容量因子：向量化(quant-review)靠拢——其容量评分权重 0.05，但因财富密码模型更精简(无其余 11 项成分)，
# 权重提到 0.10 使其影响可比。仅作为排序分内的加权项参与纯排名，不再"绕过排名强行塞入"观察池。
CAPACITY_WEIGHT = 0.10
# 板块梯队（用户指定三大核心要素之一）：梯队健康度(0~10)线性映射到排序分，上限 LADDER_WEIGHT；
# 完整梯队(龙头+龙二+龙三+≥3补涨)的 sector 龙头额外加成 LADDER_DRAGON_BONUS。
# 这一项直接惩罚"孤龙/纯广度无梯队"的题材，强化"真主线 vs 量化铺广度"的判别。
LADDER_WEIGHT = 8.0
LADDER_DRAGON_BONUS = 3.0
# 🚫 成交额硬门槛已全系统清除（2026-11-11 用户「这个 5 亿的限制把它 pass 掉吧，
#    不要了，全系统清除这个限制」）。
#
# 为什么拆：用户原话「前期成交额不高很正常，资金筹码没什么抛压，成交额很低但能封板
#   是很正常的」。11-11 实证——光通信 10 只首板里质量最好的两只正是被这条线砍掉的：
#     青山纸业 600103（72.6 亿流通·换手仅 4.6%·成交 3.29 亿·9:35 封板·炸 1 次）
#     华脉科技 603042（28 亿流通·换手 5.7%·成交 1.55 亿·9:36 封板·零炸板）
#   两者的「量小」是**筹码锁死**，不是没人接。用绝对成交额一刀切，等于系统性惩罚
#   「封得好、卖盘少」的票，与「低位缩量封板＝筹码锁定」的既定原则直接矛盾。
#   晋级率侧也一直反向：≤1.5亿 27.5% > 1.5~3亿 16.0% > 3~5亿 13.1% > 5~10亿 13.9%
#   > ≥10亿 8.1%（33 日 ≈3000 个首板样本）——门槛方向本来就是反的。
#
# ⛔ 只保留一根**数据防呆线**（0.05 亿 = 500 万）：那不是流动性判据，
#   只挡 amount 字段异常（把 0.000 或字符串当成交额）。⛔ 不许再把它当门槛往上加。
# ⛔ 回退：`ENABLE_TURNOVER_FLOOR=1` + `MIN_TURNOVER_YI` 恢复旧口径。
MIN_TURNOVER_YI = 0.05            # 数据防呆线（原 5.0 已清除）
MIN_TURNOVER_YI_LOW_BOARD = 0.05  # 同上（原 1.5 已清除）
MIN_TURNOVER_YI_LEGACY = 5.0           # 仅 ENABLE_TURNOVER_FLOOR=1 回退时生效
MIN_TURNOVER_YI_LOW_BOARD_LEGACY = 1.5  # 同上
LOW_BOARD_CEIL = 2                # 「低位」上界（含）＝首板 / 2 板
ENABLE_TURNOVER_BY_STAGE = "0"    # 分阶段门槛已停用（0 = 两档都走防呆线）
ENABLE_TURNOVER_FLOOR = "0"       # 0 = 成交额不作准入判据（默认）；1 = 回退旧门槛


def turnover_floor(boards: Any) -> float:
    """该板数档位下的成交额**防呆线**（亿）。

    📌 2026-11-11：成交额已从准入门槛降级为数据防呆 —— 恒返回 `MIN_TURNOVER_YI`。
    保留 `boards` 形参是为了不改动全部调用方签名；⛔ 不再按板数分档。
    """
    if os.environ.get("ENABLE_TURNOVER_FLOOR", ENABLE_TURNOVER_FLOOR) not in ("1", "true", "True"):
        return MIN_TURNOVER_YI          # 默认：只做数据防呆，不判成交额
    # ── 以下仅回退模式（ENABLE_TURNOVER_FLOOR=1）才会走到 ──
    if os.environ.get("ENABLE_TURNOVER_BY_STAGE", ENABLE_TURNOVER_BY_STAGE) in ("0", "false", "False", ""):
        return MIN_TURNOVER_YI_LEGACY
    try:
        b = int(boards)
    except (TypeError, ValueError):
        return MIN_TURNOVER_YI_LEGACY
    if b <= 0:
        return MIN_TURNOVER_YI_LEGACY
    return (MIN_TURNOVER_YI_LOW_BOARD_LEGACY if b <= LOW_BOARD_CEIL
            else MIN_TURNOVER_YI_LEGACY)


# ══════════════════════════════════════════════════════════════════════════
# 👑 龙头门槛豁免（2026-10-27 用户「推的全是杂毛，既不是板块龙一，也不是高度第一」）
# ══════════════════════════════════════════════════════════════════════════
# 病灶（当日实证）：
#   ① 上海九百 5 板（全市场唯一高度）成交 3.94 亿 < 5 亿 → 被剔出池，只进「只看不买」名单；
#   ② 赛福天 4 板 09:25:02 秒板 3.02 亿 → 同样被剔；
#   ③ 大消费线龙头被剔后，组内最高板变成**首板**的文投控股 → 它成了「这条线的前排」，
#      还被推成接力池第 2 位（09:41 封、炸 5 次、封单占流通 0.12%、梯队 D）。
#   ⇒ **先砍掉老大，再从残骸里挑前排** —— 池子必然由二线构成。
# 修法：高度层（全市场最高板 / 次高板层，且 ≥LEADER_EXEMPT_MIN_BOARDS 板）的成交额门槛
#   降到 MIN_TURNOVER_YI_LEADER。⛔ 只放宽「进场门槛」，不改排序、不改任何裁决逻辑；
#   ⛔ 不是无门槛（1.5 亿以下仍剔，防极端失真）。
# 为什么按「板数层」而不是按「题材分组」判龙头：题材映射存在跨线误归（实证 line_family
#   把多伦科技归到「AI算力」），拿它定龙头会把豁免发错人；板数层是当日客观事实，无映射风险。
MIN_TURNOVER_YI_LEADER = MIN_TURNOVER_YI   # 高度层也是防呆线（原 1.5 已清除·2026-11-11）
LEADER_EXEMPT_MIN_BOARDS = 3       # 豁免只给 ≥3 板的高度层（1~2 板交给 gene_radar / 低池）
ENABLE_LEADER_TURNOVER_EXEMPT = "1"


def leader_exempt_enabled() -> bool:
    """龙头门槛豁免开关（默认开，`ENABLE_LEADER_TURNOVER_EXEMPT=0` 一键回旧行为）。"""
    return os.environ.get(
        "ENABLE_LEADER_TURNOVER_EXEMPT", ENABLE_LEADER_TURNOVER_EXEMPT
    ) not in ("0", "false", "False", "")


def leader_turnover_floor(
    boards: Any,
    market_max_boards: Any,
    *,
    enabled: bool | None = None,
) -> float | None:
    """高度层票的豁免门槛（亿）；**不该豁免则返回 None**（调用方继续用常规门槛）。

    豁免范围：`boards >= market_max - 1` 且 `boards >= LEADER_EXEMPT_MIN_BOARDS`。
      · market_max=5 → 5 板、4 板都算「高度层」
      · market_max=6 → 6 板、5 板豁免；4 板不豁免（那是中位股，非高度层）
    板数/最高板缺失 → None（⛔ 不猜、不开后门）。
    """
    if enabled is None:
        enabled = leader_exempt_enabled()
    if not enabled:
        return None
    try:
        b = int(boards)
        m = int(market_max_boards)
    except (TypeError, ValueError):
        return None
    if b <= 0 or m <= 0:
        return None
    if b < LEADER_EXEMPT_MIN_BOARDS or b < m - 1:
        return None
    return MIN_TURNOVER_YI_LEADER


# ⛔ P1（2026-10-18 用户「按顺序处理」·治「选股宇宙被 5 亿门槛砍掉 3/4」）：
# 实证 10-16：涨停 61 只 → 成交额<5亿 剔掉 45 只(74%) → 只剩 16 只进评分；
# 复盘点名的新华传媒(2.47亿)、金盾股份(3.35亿) 就死在这一刀上，**根本没进视野**。
# 此处单列「池外高辨识度」名单：连板≥3 或 全市场唯一高度。
# ⛔ 铁律：**只展示、不参与排序、不占名额、标 buyable=False**——看得到 ≠ 买得到，
#    成交额门槛作为用户纪律不放松，本名单只解决"复盘说得头头是道、选股里却查无此票"。
# 开关 ENABLE_OFFPOOL_HIGH_IDENT=0 可一键回旧行为。
OFFPOOL_MIN_BOARDS = 3      # 池外票进名单的最低连板高度
OFFPOOL_TOPN = 8            # 名单上限（防刷屏）


def offpool_high_ident_enabled() -> bool:
    """池外高辨识度名单开关（默认开，环境变量可回滚）。"""
    return os.environ.get("ENABLE_OFFPOOL_HIGH_IDENT", "1") not in ("0", "false", "False", "")
# 最近强势板块加权（用户纪律 2026-08-13：聚焦题材/主线/强势板块）
# 数据源=聚合最近N天 candidates.theme(申万二级)——即每天记录的题材炒作路径里爆发过的板块；
# 排除当天文件避免自举，跨日持续(≥min_persist_days)的板块=持续主线→加权，历史单日→小加权，
# 最近窗口完全未出现→温和降权（保留新题材冒头空间，但体现"不聚焦非强势题材"）。
STRONG_SECTOR_N_DAYS = 7              # 历史窗口（排除当天后取最近 N 个 candidates 文件）
STRONG_SECTOR_MIN_PERSIST_DAYS = 2    # 跨日命中≥此天数=持续主线
STRONG_SECTOR_PERSIST_BONUS = 6.0     # 持续主线加权
STRONG_SECTOR_RECENT_BONUS = 2.0      # 历史仅单日出现
STRONG_SECTOR_OUTSIDE_PENALTY = -3.0  # 最近窗口未出现→温和降权


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# 📌 缩量封板补偿（2026-11-03 用户「重新定量」）
#   容量公式对 cje≤10 亿线性给分，缩量一字票（如 2.44 亿）只拿 8.8/36，
#   再叠 liquidity_band 48 分 → 最强形态被判最差。回测结论相反，
#   故对「一字/缩量封板」给保底（不给超额，防小票反超真容量核心）。
SHRINK_SEAL_CIE_FLOOR = 30.0     # 成交额维保底分（满分 36）
SHRINK_SEAL_LIQ_FLOOR = 66.0     # 流动性档保底分（对齐 40-90 亿档的 66）


def _capacity_score(cje_yi: float, zsz_yi: float, hs_pct: float, zbc: int, fbt: str | None) -> dict[str, Any]:
    """
    容量因子（忠实在 quant-review/daily_review/metrics/zt_analysis.py 的 capacity_score 公式）。
    输入：成交额(亿) / 总市值(亿) / 换手率(%) / 炸板次数 / 首封时间。
    返回 cap_score(0-100) + 是否容量核 + 风格标签(容量/龙头)。
    缺数据（cje/zsz 为 0）时优雅退化：容量分=55、非容量核、风格=龙头。
    """
    cap_score = _clamp(100 - max(0.0, zsz_yi - 150) * 0.55) if zsz_yi else 55.0
    cje_threshold = 10.0  # 简化：不依赖 theme_net 的 relaxed 分支
    if cje_yi and cje_yi > 0:
        if cje_yi <= cje_threshold:
            cje_target_score = _clamp(36.0 * (cje_yi / cje_threshold), 0.0, 36.0)
        else:
            cje_target_score = _clamp(
                92.0 + min(cje_yi - 10, 25) * 0.46 - max(0.0, cje_yi - 40) * 0.55, 76.0, 100.0
            )
    else:
        cje_target_score = 0.0
    vol_tier_score = _clamp(100.0 - abs(hs_pct - 8.0) * 6.5) if hs_pct else 55.0
    liquidity_band_score = (
        86.0 if 10.0 <= cje_yi <= 40.0 else
        72.0 if 5.0 <= cje_yi < 10.0 else
        66.0 if 40.0 < cje_yi <= 90.0 else
        48.0 if cje_yi > 0 else 34.0
    )
    fbt = str(fbt or "")
    # 一字：竞价封 + 极低换手 + 炸板 0（原判据不动）
    is_yizi = bool(hs_pct > 0 and hs_pct < 2 and cje_yi < 3 and zbc == 0 and fbt.startswith("0925"))
    is_shrink = bool(hs_pct > 0 and hs_pct < 3 and cje_yi < 5 and zbc == 0)
    # 📌 2026-11-03 用户「重新定量」——缩量封板补偿。
    #   原式在 cje≤10 时线性给分（2.44 亿 → 8.8/36），再叠 liquidity_band 48 分，
    #   等于把「一字封死·没换手」这种最强形态按「流动性缺陷」双重扣分。
    #   回测（33 日/约 3000 首板样本）结论相反：≤1.5 亿次日晋级 27.5% > ≥3 亿 13.1%。
    #   ⛔ 只给**保底**、不给超额：30/36 ≈ 8.3 亿票的水平，防小票反超真容量核心。
    #   开关 ENABLE_SHRINK_SEAL_CAPACITY=0 回旧算法。
    if (os.environ.get("ENABLE_SHRINK_SEAL_CAPACITY", "1").lower() not in {"0", "false", "off"}) \
            and (is_yizi or is_shrink):
        cje_target_score = max(cje_target_score, SHRINK_SEAL_CIE_FLOOR)
        liquidity_band_score = max(liquidity_band_score, SHRINK_SEAL_LIQ_FLOOR)
    cap_total = _clamp(
        cje_target_score * 0.44 + vol_tier_score * 0.24 + cap_score * 0.17 + liquidity_band_score * 0.15
    )
    is_capacity_core = (
        cje_yi >= 10.0 and zsz_yi >= 50.0 and hs_pct <= 15.0 and not is_yizi and not is_shrink
    )
    # 风格标签（对齐 quant-review：容量核心 = 成交额≥50亿 或 容量分≥60）
    style_tag = "容量" if (cje_yi >= 50.0 or cap_total >= 60.0) else "龙头"
    return {
        "cap_score": round(cap_total, 2),
        "cje_yi": round(cje_yi, 2),
        "zsz_yi": round(zsz_yi, 2),
        "is_capacity_core": bool(is_capacity_core),
        "style_tag": style_tag,
    }


# 🎯 低位连板龙雷达（2026-10-22 用户拍板「需要做，雷达叠加观察的意思」）──────────
# 要解决的问题（用户原话）：大业股份这种票「尽量在第一个板或者第二个板的时候，必须要发现它
#   被推出来了」。但首板/2 板往往成交额只有 1~3 亿，会被 5 亿/3 亿的进场门槛挡在池外，
#   于是「复盘说得头头是道、选股里却查无此票」。
# 设计（与 `offpool_high_ident` 同一套护栏）：
#   ① **叠加观察**：只展示，⛔ 不占 relay/watch 名额、⛔ 不参与排序、⛔ 不进 09:25 池内裁决；
#   ② 门槛：1~2 板 ∧ 成交额 ≥ GENE_RADAR_MIN_TURNOVER_YI 亿 ∧ 股性档＝**连板龙**（严格档，
#      「连板龙·次数少/历史短」不进——样本不够就不是「它一直能连」的证据）；
#   ③ 排除已在池内的票（池里已有就别重复占版面）、688（无权限）、ST（铁律不看）。
GENE_RADAR_MIN_TURNOVER_YI = 0.05  # 仅数据防呆（原 1.0 已清除·2026-11-11 全系统拆成交额门槛）
GENE_RADAR_MAX_BOARDS = 2          # 只盯低位（首板 / 2 板）
GENE_RADAR_TOPN = 6                # 名单上限（防刷屏）


def gene_radar_enabled() -> bool:
    """低位连板龙雷达开关（默认开，`ENABLE_GENE_RADAR=0` 一键回旧行为）。"""
    return os.environ.get("ENABLE_GENE_RADAR", "1") not in ("0", "false", "False", "")


# 🐣 低位池（2026-10-27 用户「还不如推低位！」）：
#   雷达此前「只展示不买」，用户的诉求是「这种低位的才想买」→ 升级为可买第三桶。
#   ⛔ 名额与 relay/watch **共享**（不另开），否则等于悄悄放大仓位（违反回撤期单票/总仓纪律）。
LOW_POOL_ENABLED_ENV = "ENABLE_LOW_POOL"
LOW_POOL_TOPN = 3                 # 最多 3 只（防刷屏；真正能买的只有名额内的 1 只）
# ⛔ 封板质量护栏（2026-10-27 当天实证补上）：低池 = 「低位起步」，不是「低位的垃圾」。
#   首例反例：海洋王(002724) 首板、20.9 亿、股性连板龙（晋级54%/最长6板）→ 机械上完全符合，
#   但换手 28.9%、一天炸开 6 次、上一波已经到过 6 板 —— 这是「大资金借涨停倒货」，
#   用户口径里恰好是最差的那种：地量→首板放量→连板缩量才是筹码锁定，巨量换手就是出货。
#   故低池额外要求：换手 ≤20% ∧ 炸板 ≤2 次。⛔ 只卡低池（可买），雷达展示层不卡（信息不丢）。
LOW_POOL_MAX_HS_PCT = 20.0
LOW_POOL_MAX_ZBC = 2


def low_pool_quality_ok(row: dict[str, Any]) -> bool:
    """低池封板质量门槛：换手不过度、炸板不多。缺数据 → 放行（⛔ 缺数据≠不利证据）。"""
    try:
        hs = float((row or {}).get("hs") or 0.0)
    except (TypeError, ValueError):
        hs = 0.0
    try:
        zbc = int((row or {}).get("zbc") or 0)
    except (TypeError, ValueError):
        zbc = 0
    if hs > LOW_POOL_MAX_HS_PCT:
        return False
    return zbc <= LOW_POOL_MAX_ZBC


def low_pool_enabled() -> bool:
    """低位池可买开关（默认开，`ENABLE_LOW_POOL=0` → 退回「只看不买」）。"""
    return os.environ.get(LOW_POOL_ENABLED_ENV, "1") not in ("0", "false", "False", "")


# 🧊 2026-10-27 用户定调（原话「只观察，行情差没有必要强行接力」）：
#    **全市场最高板 + 封单极薄 → 默认只当观察（能看不能买）**。
#    背景实证：上海九百(600838) 5 板是全市场唯一高度，但封单只占流通 0.24%
#    （1,000 万级别的封单量级 vs 十几亿的成交）—— 最高板却没人愿意把封单堆厚，
#    这不是「强」，是「没人接」。缩量 + 情绪 1.8 分的日子再强行接力这类票，
#    赔率极差（用户口径：没必要强行接力）。
#    阈值直接复用防守闸已校准的「封单薄」口径（`defense_gate.DIST_SEAL_RATIO_MAX`
#    = 2%），⛔ 不另造一套阈值，否则卡面说「封单薄」、这里却按别的线判，两套口径打架。
#    可逆开关 `ENABLE_THIN_SEAL_TOP_WATCH=0` 逐字回退。
THIN_SEAL_TOP_RATIO_MAX = 2.0
THIN_SEAL_TOP_MIN_BOARDS = 3          # 最高板不到 3 板 → 谈「高度」无意义，不启用
THIN_SEAL_TOP_ENABLED_ENV = "ENABLE_THIN_SEAL_TOP_WATCH"


def _load_prev_pool_boards(today: str, cache_dir: Path | None = None) -> dict[str, int]:
    """读**上一交易日**候选文件的 relay+watch → `{code: 当日板数}`。

    用途：观察池「昨日在池 ∧ 今日晋级 → 优先占位」（2026-11-04 用户「b 占位」）。

    ⛔ 上一交易日必须走 `common.trading_calendar.prev_a_share_trading_day`，
      **不得用 `today - 1 天`** —— 跨周末/长假会去读一个根本不存在的文件，
      占位功能静默失效（且看起来像「昨天没票」，是最难查的一类静默错误）。
    ⛔ 文件缺失/损坏一律返回空 dict（调用方据此退回旧行为），⛔ 不猜、不编造板数。
    """
    from common.trading_calendar import prev_a_share_trading_day
    from common.json_io import load_json

    try:
        prev_day = prev_a_share_trading_day(today)
    except Exception:  # noqa: BLE001
        return {}
    if not prev_day or prev_day == today:
        return {}
    # ⛔ `ROOT` 在本模块**已经指向 wealth_code/**（不是项目根）——
    #    写成 `ROOT/"wealth_code"/"cache"` 会拼出 `wealth_code/wealth_code/cache`
    #    这种不存在路径，函数静默返回空 dict（实证踩过：占位功能完全不生效）。
    base = Path(cache_dir) if cache_dir else (ROOT / "cache")
    data = load_json(base / f"candidates-{prev_day}.json")
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for bucket in ("relay", "watch"):
        for row in data.get(bucket) or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "")
            if not code:
                continue
            try:
                out[code] = max(out.get(code, 0), int(row.get("boards") or 0))
            except (TypeError, ValueError):
                continue
    return out


def thin_seal_top_enabled() -> bool:
    """最高板极薄封单降级开关（默认开，`ENABLE_THIN_SEAL_TOP_WATCH=0` 回滚）。"""
    return os.environ.get(THIN_SEAL_TOP_ENABLED_ENV, "1") not in ("0", "false", "False", "")


def demote_thin_seal_top(
    relay: list[dict[str, Any]],
    watch: list[dict[str, Any]],
    ztgc: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    """把「全市场最高板 ∧ 封单极薄」的票从接力池降为观察（只看不买）。

    返回 `(relay, watch, moved, market_max)`；`moved` 供 diagnostics/打印留痕。

    判据（两条同时满足才动，缺数据一律不动）：
      ① `boards >= 全市场最高板`（严格最高，最高板-1 不管 —— 用户说的是「最高板」）
      ② `seal_ratio_pct < THIN_SEAL_TOP_RATIO_MAX`（封单/流通 百分比）
    ⛔ 缺 `seal_ratio_pct`（拿不到流通市值）→ 不裁决，留在接力池（不编造）。
    ⛔ 只在接力池里降级；本来就是观察池的票只打标（`watch_only`），不改归属。
    """
    if not thin_seal_top_enabled():
        return relay, watch, [], 0
    boards_all: list[int] = []
    for _s in (ztgc or []):
        try:
            boards_all.append(int(_s.get("lbc") or _s.get("boards") or 1))
        except (TypeError, ValueError):
            continue
    market_max = max(boards_all or [0])
    if market_max < THIN_SEAL_TOP_MIN_BOARDS:
        return relay, watch, [], market_max

    def _hit(r: dict[str, Any]) -> str | None:
        """返回白话理由；不该降级返回 None。"""
        b = _to_int(r.get("boards"), 1)
        if b < market_max:
            return None
        ratio = r.get("seal_ratio_pct")
        if not isinstance(ratio, (int, float)):
            return None                     # 缺数据 → 不裁决
        if float(ratio) >= THIN_SEAL_TOP_RATIO_MAX:
            return None
        return (f"它是今天全市场最高 {b} 板，但封单只占流通盘的 {float(ratio):.2f}%"
                f"（没有资金愿意把封单堆厚）→ 位置最高、封得最薄，只看不买")

    moved: list[dict[str, Any]] = []
    keep: list[dict[str, Any]] = []
    for r in (relay or []):
        why = _hit(r)
        if why:
            r["thin_seal_top"] = True
            r["watch_only"] = True
            r["watch_reason"] = why
            r["watch_group"] = "最高板封单过薄"
            moved.append(r)
            watch.append(r)
        else:
            keep.append(r)
    # 已经在观察池里的最高板也打标（防裁决层把它捡回去买）
    for r in (watch or []):
        why = _hit(r)
        if why and not r.get("watch_only"):
            r["thin_seal_top"] = True
            r["watch_only"] = True
            r["watch_reason"] = why
            if not r.get("watch_group"):
                r["watch_group"] = "最高板封单过薄"
    # 降级后接力池序号重排，避免卡面出现「2、3」起头的断档编号
    for i, r in enumerate(keep, start=1):
        r["relay_rank"] = i
    # ⛔ 观察池序号也必须重排（2026-10-27 实测卡面 `None、`）：
    #    走 append 路径进观察池的票**本来没有 `watch_rank`**（只有 relay 行有 `relay_rank`），
    #    卡面 `it.get('watch_rank')` 直接渲染出 `None、`。
    #    与 `pool_admit` 的同一条铁律：桶归属改了，序号与标签都要跟着改。
    for i, r in enumerate(watch or [], start=1):
        r["watch_rank"] = i
        r.pop("relay_rank", None)
        r["bucket"] = "watch"
        r["bucket_label"] = "观察池"
    return keep, watch, moved, market_max



def select_gene_radar(
    rows: list[dict[str, Any]],
    *,
    in_pool_codes: set[str] | None = None,
    excluded_turnover_codes: set[str] | None = None,
    series: dict[str, list[tuple[str, int]]] | None = None,
    trading_days: list[str] | None = None,
    topn: int = GENE_RADAR_TOPN,
) -> list[dict[str, Any]]:
    """从**评分全量**里挑「低位 + 连板龙 + 有量」的票（纯展示·不可买）。

    `rows` 必须是成交额过滤**之前**的评分全量（否则被门槛剔掉的票根本不在里面，
    雷达就只剩「本来就能进池」的票，等于什么都没补）。

    ⛔ **位置过滤不能省**（2026-10-22 当天实测抓到）：冰山冷热 000530 首板、成交 25.5 亿、
       基因「连板龙」（晋级 57%/最长 6 板），单看这三项完美符合，但它 10-21 之前已经涨了
       45%~64%、上一波最高 4 板、断板 3 天后重启 —— 是「高位二波」，不是「刚起步」。
       用户的核心口径是「往期接力强 × **启动位置低**」，位置这一维漏了，雷达就会变成
       又一堆「别人已经赚完的票」。故此处直接复判 `defense_gate.position_wave`（本地
       zt 序列，⛔ 不外呼网络）：**断板 ≥3 天 ∧ 上一波最高 ≥3 板 → 不进雷达**，
       与 09:25 的防守闸同一套判据，避免「卡上说低位、系统却另判高位」两套口径打架。

    返回每项带 `buyable=False` + `radar_why`（为什么没进池）+ 基因原始数字（不加工）。
    """
    pool = {str(c) for c in (in_pool_codes or set())}
    dropped_by_turnover = {str(c) for c in (excluded_turnover_codes or set())}
    picked: list[dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code") or "").strip()
        name = str(r.get("name") or "").strip()
        if not code or not name:
            continue
        if code in pool or code.startswith("688"):
            continue
        if "ST" in name.upper() or bool(r.get("is_st")):
            continue
        if bool(r.get("is_one_word")):        # 一字板买不到，看了只会误导
            continue
        boards = _to_int(r.get("boards"), 0)
        if boards < 1 or boards > GENE_RADAR_MAX_BOARDS:
            continue
        cje = _to_float(r.get("cje_yi"), 0.0)
        if cje < GENE_RADAR_MIN_TURNOVER_YI:
            continue
        # 位置：高位二波（断板多日 + 上一波已经到过 3 板以上）→ 不是低位，不进雷达
        wave = _defense_gate.position_wave(code, series, trading_days)
        if (wave.get("known") and wave.get("is_rebound")
                and _to_int(wave.get("prior_max_board"), 0) >= _defense_gate.POSITION_PRIOR_BOARD_MIN):
            continue
        d10, d30 = r.get("dev_10d_pct"), r.get("dev_30d_pct")
        d10 = d10 if d10 is not None else r.get("ret_10d_pct")
        d30 = d30 if d30 is not None else r.get("ret_30d_pct")
        if ((isinstance(d10, (int, float)) and float(d10) >= _defense_gate.POSITION_DEV10_MIN)
                or (isinstance(d30, (int, float)) and float(d30) >= _defense_gate.POSITION_DEV30_MIN)):
            continue
        facts = _zt_gene.gene_facts(code)
        if facts.get("grade") != "连板龙":     # 严格档：次数少/历史短不进雷达
            continue
        picked.append({
            "code": code,
            "name": name,
            "boards": boards,
            "theme": str(r.get("core_theme") or r.get("theme") or r.get("sector") or ""),
            "pct": _to_float(r.get("pct"), 0.0),
            "cje_yi": round(cje, 2),
            "fbt": str(r.get("fbt") or ""),
            "zbc": _to_int(r.get("zbc"), 0),
            "is_20cm": bool(r.get("is_20cm")),
            "gene": facts,
            # 2026-10-27 升级为可买桶后，裁决层的闸（派发/位置/身位）要有原始读数才能判，
            # 故把这几项一并带出（此前只展示，缺了也不影响）。
            "price": _to_float(r.get("price"), 0.0),
            "hs": _to_float(r.get("hs"), 0.0),
            "seal_ratio_pct": r.get("seal_ratio_pct"),
            "seal_yi": r.get("seal_yi"),
            "zsz_yi": r.get("zsz_yi"),
            "lt_yi": r.get("lt_yi"),
            "carry_cnt": _to_int(r.get("carry_cnt"), 0),
            "sector": str(r.get("sector") or ""),
            "dev_10d_pct": r.get("dev_10d_pct"),
            "dev_30d_pct": r.get("dev_30d_pct"),
            # ⛔ 纪律护栏：渲染层必须保留 buyable=False 语义
            "buyable": False,
            "radar_why": (
                f"成交额{cje:.2f}亿＜{turnover_floor(boards):g}亿进池门槛"
                if code in dropped_by_turnover else "评分/名额没排上"
            ),
        })
    picked.sort(key=lambda x: (
        _to_float((x.get("gene") or {}).get("promo_rate_hist"), 0.0),
        _to_int((x.get("gene") or {}).get("zt_250"), 0),
        _to_float(x.get("cje_yi"), 0.0),
    ), reverse=True)
    return picked[:topn]


def select_offpool_high_ident(
    excluded: list[dict[str, Any]],
    *,
    market_max_boards: int = 0,
    market_max_count: int = 0,
    min_boards: int = OFFPOOL_MIN_BOARDS,
    topn: int = OFFPOOL_TOPN,
) -> list[dict[str, Any]]:
    """从「成交额被剔除」的票里挑出**高辨识度**名单（P1 · 纯展示·不可买）。

    入选判据（满足其一）：
      ① 连板高度 ≥ min_boards（默认 3 板）
      ② 该票是**全市场唯一高度**（boards == market_max_boards 且全市场仅 market_max_count=1
         只达到该高度，且 ≥2 板）——`market_max_*` 必须按**全池**统计、不是按本名单统计，
         否则池内同高度的票不会被算进去，会把"两 5 板"误判成"唯一高度"。

    硬排除：688（无权限）/ ST（铁律不看）/ 无 code 无 name。

    返回按 (boards desc, cje_yi desc) 排序、截 topn；每项带：
      - buyable=False          ⛔ 本名单永不作为买入依据
      - offpool_reason         被池外剔除的真实原因（成交额不足 X 亿）
      - ident_reason           为什么仍值得看（N 板 / 唯一高度）
    """
    rows = [r for r in (excluded or []) if isinstance(r, dict)]
    if not rows:
        return []
    uniq_height = market_max_boards >= 2 and market_max_count == 1

    picked: list[dict[str, Any]] = []
    for r in rows:
        code = str(r.get("code") or r.get("dm") or "").strip()
        name = str(r.get("name") or r.get("mc") or "").strip()
        if not code or not name:
            continue
        if code.startswith("688"):
            continue
        if "ST" in name.upper():
            continue
        boards = _to_int(r.get("boards"), 0)
        is_uniq = bool(uniq_height and boards == market_max_boards)
        if boards < min_boards and not is_uniq:
            continue
        cje = _to_float(r.get("cje_yi"), 0.0)
        picked.append({
            "code": code,
            "name": name,
            "sector": str(r.get("sector") or ""),
            "core_theme": str(r.get("core_theme") or ""),
            "boards": boards,
            "pct": _to_float(r.get("pct"), 0.0),
            "cje_yi": round(cje, 2),
            "is_20cm": bool(r.get("is_20cm")),
            "fbt": str(r.get("fbt") or ""),
            "raw_score": round(_to_float(r.get("raw_score"), 0.0), 1),
            # ⛔ 下面两个字段是纪律护栏，渲染层必须保留
            "buyable": False,
            "offpool_reason": f"成交额{cje:.2f}亿＜{turnover_floor(boards):g}亿门槛",
            "ident_reason": ("全市场唯一高度" if is_uniq else f"{boards}连板"),
        })
    picked.sort(key=lambda x: (_to_int(x.get("boards"), 0), _to_float(x.get("cje_yi"), 0.0)), reverse=True)
    return picked[:topn]


def _score_all(ztgc: list[dict[str, Any]], code2themes: dict[str, list[str]], hot_sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    对全部涨停股计算 leader_score（复用 core/leader.py 的纯因子）。
    返回 list[{code,name,theme,score,reason,tags,is_20cm,price,pct,boards,...}]
    """
    rows = list(ztgc or [])
    seal_amt_max = max([_to_float(s.get("zj"), 0.0) for s in rows] or [0.0])
    hs_p10 = _quantile([_to_float(s.get("hs"), 0.0) for s in rows if _to_float(s.get("hs"), 0.0) > 0], 0.10, 1.2)

    theme_of: dict[str, str] = {}
    theme_first_fbt: dict[str, str] = {}
    for s in rows:
        code = str(s.get("dm") or "").strip()
        if not code:
            continue
        # ⛔ 2026-10-27 确定性修复（用户「处理问题一」·治「同一天跑两次分数不同」）：
        #    **题材必须优先取当日题材快照回填的 core_theme**，而不是只认 code2themes。
        #    实证（10-27 同池两次跑）：15:59 那次 code2themes 尚未齐 → 赛福天/多伦科技
        #    fallback 到行业「通用设备 / 软件开发」，16:13 那次已齐 → 「机器人」。
        #    题材桶一变 → `_attach_sector_ladder` 分组变（C→A、health 4.6→10）
        #    → `score_explosion` 的题材统计变（exp 2.75→38.67）→ 最终分 56.1→92.03。
        #    即：**同一份涨停池算出两套分，差的不是随机数，而是旁路数据到没到**。
        #    现统一走 core_theme（= cache/theme_snapshot/theme_<date>.json 的当日冻结映射，
        #    `theme_mapping_fingerprint` 同日恒定）→ 早跑晚跑同分；
        #    快照缺失时仍回退旧路径（不编造题材）。
        theme = str(s.get("core_theme") or "").strip()
        if not theme:
            theme = pick_primary_theme(
                code=code, code2themes=code2themes, hot_sectors=hot_sectors,
                fallback_industry=str(s.get("hy") or ""),
            )
        theme_of[code] = theme
        fbt = str(s.get("fbt") or "").strip()
        if not fbt:
            continue
        prev = theme_first_fbt.get(theme)
        if prev is None or _time_hhmmss_to_sec(fbt) < _time_hhmmss_to_sec(prev):
            theme_first_fbt[theme] = fbt

    theme_stats = _build_theme_stats(rows=rows, theme_of=theme_of)
    out: list[dict[str, Any]] = []
    for s in rows:
        code = str(s.get("dm") or "").strip()
        name = str(s.get("mc") or "").strip()
        if not code or not name:
            continue
        hs = _to_float(s.get("hs"), 0.0)
        zbc = _to_int(s.get("zbc"), 0)
        fbt = str(s.get("fbt") or "").strip() or None
        lbt = str(s.get("lbt") or "").strip() or None
        seal_amt = _to_float(s.get("zj"), 0.0)
        zf = _to_float(s.get("zf"), 0.0)
        lbc = _to_int(s.get("lbc"), 1)
        lt_mktcap = _to_float(s.get("lt"), 0.0)
        cje = _to_float(s.get("cje"), 0.0)
        theme = theme_of.get(code) or "其他"
        ign, ign_r = score_ignition(
            code=code, hs=hs, zbc=zbc, fbt=fbt, lbt=lbt, zf=zf, lbc=lbc,
            seal_amt=seal_amt, seal_amt_max=seal_amt_max, lt_mktcap=lt_mktcap, cje=cje,
            ohlc=None, hs_p10=hs_p10,
        )
        exp, exp_r = score_explosion(theme=theme, theme_stats=theme_stats, leader_fbt=fbt)
        lead, lead_r = score_leadership(fbt=fbt, theme_first_fbt=theme_first_fbt.get(theme))
        # 容量因子（向量化靠拢）：成交额(亿)/总市值(亿)/换手率
        cap = _capacity_score(
            cje_yi=_to_float(s.get("cje"), 0.0),
            zsz_yi=_to_float(s.get("zsz"), 0.0),
            hs_pct=hs, zbc=zbc, fbt=fbt,
        )
        pr = _theme_priority(theme)
        theme_bonus = 4.0 if pr == 1 else (2.5 if pr <= 4 else 0.0)
        score = ign + exp + lead + theme_bonus
        tags: list[str] = []
        if ign_r.get("strictOneWord"):
            tags.append("一字板")
        elif ign_r.get("oneWord"):
            tags.append("强封")
        if exp_r.get("hit") and _to_int(exp_r.get("follow"), 0) >= 2:
            tags.append("带动板块")
        if lead >= 15:
            tags.append("先发")
        if _to_float(ign_r.get("spaceScore"), 0.0) >= 6:
            tags.append("空间")
        if theme_bonus > 0:
            tags.append("一级题材")
        out.append({
            "code": code, "name": name, "theme": theme, "score": round(score, 2),
            # 龙头三要素组件分（供 AI 期望层 dragon_three_diagnosis 使用）
            "ign_score": round(ign, 2), "exp_score": round(exp, 2),
            "lead_score": round(lead, 2), "themeTier": 1 if pr <= 4 else 2,
            "reason": {"ignition": ign_r, "explosion": exp_r, "lead": lead_r,
                       "themeBonus": theme_bonus, "themeTier": 1 if pr <= 4 else 2},
            "tags": tags,
            # 容量因子输出（向量化靠拢）
            "cap_score": cap["cap_score"],
            "cje_yi": cap["cje_yi"],
            "zsz_yi": cap["zsz_yi"],
            "is_capacity_core": cap["is_capacity_core"],
            "style_tag": cap["style_tag"],
            "is_20cm": bool(s.get("is_20cm")),
            "is_st": bool(s.get("is_st")),
            "is_one_word": bool(ign_r.get("oneWord")) or bool(ign_r.get("strictOneWord")),
            # price 缺失时置 None（不得用涨幅顶替，数据即事实）
            "price": _to_float(s.get("price")) or None,
            "pct": zf, "boards": lbc, "hs": hs, "zbc": zbc, "fbt": fbt,
            "seal_amt": (seal_amt if s.get("zj") is not None else None),
            "lt_mktcap": (lt_mktcap if s.get("lt") is not None else None),
            "seal_fact": s.get("seal_fact"),  # 同批封单事实与读取来源（金额单位亿）
            "sector": str(s.get("hy") or ""),
            # 炒作题材透传（2026-08-25 三修·用户「以题材的涨停为准」）：ztgc 已在
            # annotate_lone 前回填 core_theme（题材接口），候选行继承——annotate_lone 的
            # _theme_key 优先用 core_theme（黄河旋风=金刚石/培育钻石 归钻石，不被行业
            # 通用设备→机器人误导）；645 行 annotate_candidate 再精化（desc/plates）。
            "core_theme": str(s.get("core_theme") or ""),
        })
    return out


def _environment_component(env: dict[str, Any]) -> float:
    """情绪潮汐代理 -> 排序加分（替代原版写死的潮汐魔法数字）。"""
    regime = env.get("regime")
    if regime == "risk_on":
        base = 6.0
    elif regime == "risk_off":
        base = -8.0
    else:
        base = 0.0
    nuke = int(env.get("nuke_count") or 0)
    base -= max(0, nuke - 2) * 1.5  # 核按钮过多额外扣分
    return base


def _height_component(boards: int) -> float:
    if boards >= 5:
        return 10.0
    if boards >= 3:
        return 6.0
    if boards >= 2:
        return 3.0
    return 0.0


def _build_expectation(boards: int, is_20cm: bool) -> dict[str, Any]:  # pragma: no cover
    """
    [已废弃] 旧版裸启发式预期（仅按板数给区间），已被 core/expectation_ai.py 的
    AI 期望层完全取代，不再被任何路径调用。保留签名仅为兼容旧引用，不计入选股逻辑。
    """
    if boards >= 4:
        lo, hi = 5.0, 9.0
    elif boards == 3:
        lo, hi = 4.0, 8.0
    elif boards == 2:
        lo, hi = 3.0, 7.0
    else:
        lo, hi = 2.0, 6.0
    return {"expected_range": [lo, hi], "super_gap_min": hi + 1.0}


def _attach_sector_ladder(scored: list[dict[str, Any]], *,
                         zt_rows: list[dict[str, Any]] | None = None,
                         theme_map: dict[str, Any] | None = None,
                         asof: str | None = None
                         ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    板块梯队因子接入：按题材聚合涨停池，调用 core/leader.build_sector_ladder 计算
    每个题材的梯队健康度，并把结果回写到每只候选：
      - ladder_health     : 0~10（核心驱动分量）
      - ladder_has_complete: 是否完整梯队（龙头+龙二+龙三+≥3补涨）

    🐉 龙一判定（2026-10-27 用户「推的票必须是板块龙一」）：
    传 `zt_rows` 时，`is_sector_dragon` 改由 **`core.sector_leader` 单一源**判定，
    ⛔ 不再用 `build_sector_ladder` 的 (连板数, 涨幅, **成交额**) 排序 —— 那个口径把
    10-27 成交额 8.1 亿的多伦科技判成龙一、而卡面推的赛福天被判「非龙一」，
    同一系统给出两个答案（详见 `core/sector_leader.py` 模块 docstring）。
    不传 `zt_rows` = 旧行为（保持向后兼容，测试与旧调用不受影响）。
    """
    by_theme: dict[str, list[dict[str, Any]]] = {}
    for it in scored:
        by_theme.setdefault(it.get("theme") or "其他", []).append(it)

    for theme, arr in by_theme.items():
        lad = build_sector_ladder(arr)
        dragon_code = lad.get("dragon_code", "")
        for it in arr:
            it["ladder_health"] = lad["health_score"]
            it["ladder_has_complete"] = lad["has_complete"]
            it["ladder_grade"] = lad["health_grade"]
            it["is_sector_dragon"] = (it.get("code") == dragon_code)

    if not zt_rows:
        return scored, None

    # ── 龙一：单一源覆盖（sector_leader 的分层判据）────────────────────────
    from core import sector_leader
    # 🧬 上一波连板高度（2026-11-06 用户「东方海洋上次四连板，皇台只有三连板」）：
    #    `sector_leader.leader_key` 的**第二序**（同板数内才起作用）。缺序列/失败 →
    #    字段不写，leader_key 用中性 0，与旧行为逐字一致。`EOD_GENE_HEIGHT=0` 回退。
    if str(os.environ.get("EOD_GENE_HEIGHT", "1")).strip() not in {"0", "false", "off", ""}:
        try:
            from core import defense_gate as _dg_gene
            from core import gene_height as _gh
            _series, _ = _dg_gene.load_zt_series(upto=asof or None)
            for it in scored:
                _c = str(it.get("code") or "")
                it["prev_streak_boards"] = (
                    _gh.prev_streak_for(_series, _c, asof, it.get("boards")) if asof
                    else _gh.prev_streak_of(_series, _c)
                )
            # ⛔ 必须连**完整涨停池**一起补：龙一是在 zt_rows 内比的，只补候选会让
            #    池外强票的基因变成 0（中性）→ 反而更容易被候选顶掉龙一。
            for _r in (zt_rows or []):
                _c = str(_r.get("code") or _r.get("dm") or "")
                if not _c or _r.get("prev_streak_boards") is not None:
                    continue
                _b = _r.get("boards") if _r.get("boards") is not None else _r.get("lbc")
                _r["prev_streak_boards"] = (
                    _gh.prev_streak_for(_series, _c, asof, _b) if asof
                    else _gh.prev_streak_of(_series, _c)
                )
        except Exception:                                       # noqa: BLE001
            pass
    extra = {str(it.get("code") or ""): it for it in scored if it.get("code")}
    res = sector_leader.compute(zt_rows, theme_map, extra_by_code=extra)
    for it in scored:
        v = res["by_code"].get(str(it.get("code") or ""))
        if not v:
            continue
        it["is_sector_dragon"] = bool(v["is_sector_dragon"])
        it["dragon_rank"] = int(v["dragon_rank"])
        it["sector_has_mates"] = bool(v["has_sector"])
        it["dragon_leader_code"] = v["leader_code"]
        it["dragon_leader_name"] = v["leader_name"]
        it["dragon_leader_boards"] = v["leader_boards"]
        it["dragon_leader_block"] = v["leader_block"]
        if v["seal_ratio_pct"] is not None:
            it.setdefault("seal_ratio_pct", v["seal_ratio_pct"])
        # 板块家数取「完整涨停池口径」与既有标注的最大值（不因口径差异缩小板块）
        try:
            it["sector_mates_cnt"] = max(int(it.get("sector_mates_cnt") or 0), int(v["mates"]))
        except (TypeError, ValueError):
            it["sector_mates_cnt"] = int(v["mates"])
        if not it.get("theme_mates_cnt"):
            it["theme_mates_cnt"] = int(v["mates"])

    summary = {
        "enabled": True,
        "window_min": res["window_min"],
        "mates_min": res["mates_min"],
        "asof_count": res["asof_count"],
        "with_sector": sum(1 for b in res["boards"] if b["has_sector"]),
        # ⛔ 只落龙一清单（完整梯队见 `cache/board_leader_daily/` 台账）——候选文件是卡面/前端消费，
        #    把 42 只逐票梯队塞进 diagnostics 会让 candidates 文件翻倍且无人消费。
        "leaders": [
            {"theme": b["theme"], "mates": b["mates"],
             "code": b["leader_code"], "name": b["leader_name"],
             "boards": b["leader_boards"], "fbt": b["leader_fbt"],
             "seal_ratio_pct": b["leader_seal_ratio_pct"],
             "block": b["leader_block"]}
            for b in res["boards"] if b["has_sector"]
        ],
    }
    return scored, summary


# ══════════════════════════════════════════════════════════════════════════
# 🐉 板块龙一准入闸（2026-10-27 用户「推的票必须是板块龙一」）
# ══════════════════════════════════════════════════════════════════════════
DRAGON_GATE_ENV = "ENABLE_SECTOR_DRAGON_ONLY"
# 🚪 隔壁通道（2026-11-04 用户 BBB「今天龙一买不到，必须考虑龙二能不能买」）
# 只在「龙一自己买不进（一字/ST）」时，放**仅第 2 身位**进接力池，仓位减半、龙一开板即走。
NEXT_TO_LEADER_ENV = "ENABLE_NEXT_TO_LEADER"


def next_to_leader_enabled() -> bool:
    return str(os.environ.get(NEXT_TO_LEADER_ENV, "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _theme_key_of(r: dict[str, Any]) -> str:
    """归并后的炒作题材键（与 `pool_admit` 分组同口径·单一源）。缺失 → ""。"""
    try:
        return str(pool_admit.theme_of(r) or "").strip()
    except Exception:
        return str(r.get("theme") or r.get("core_theme") or "").strip()


def dragon_gate_enabled() -> bool:
    return str(os.environ.get(DRAGON_GATE_ENV, "1")).strip().lower() not in (
        "0", "false", "no", "off")


def apply_dragon_gate(relay: list[dict[str, Any]],
                      watch: list[dict[str, Any]],
                      *, enabled: bool | None = None
                      ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """接力池只放**板块龙一**；龙二/跟风一律降观察（不可买）。
    🚪 例外（2026-11-04 用户 BBB）：龙一**自己买不进**时，第 2 身位可顶上来。

    判据来源 = `core.sector_leader.compute` 回填的 `dragon_rank`（单一源），
    ⛔ 本函数不自己重算强弱，只按 `dragon_rank` 分桶。

    两条规则：
      ① `dragon_rank != 1` 且有板块 → 移出接力池（`dragon_gate="deputy"`）
         「龙二就是龙二，不因为龙一买不到就把它顶上来」。
      ② 龙一本身买不进（一字板 / ST）→ 移出接力池（`dragon_gate="leader_unbuyable"`），
         **且不拿同线跟风顶替** —— 同线票在 ① 里已被同一条规则挡掉。
      ⛔ 无板块（题材缺失 / 涨停独苗，`has_sector=False`）→ **不适用本闸**，
        沿用原有孤军/高标逻辑（缺数据不当不利证据）。
    """
    on = dragon_gate_enabled() if enabled is None else bool(enabled)
    if not on:
        return relay, watch, {"enabled": False, "reason": f"{DRAGON_GATE_ENV}=0"}

    def _rank(r: dict[str, Any]) -> int:
        try:
            return int(r.get("dragon_rank") or 0)
        except (TypeError, ValueError):
            return 0

    keep: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    proxies: list[dict[str, Any]] = []
    _ntl = bool(next_to_leader_enabled()) and on
    for r in relay:
        # has_sector 缺省 True：只有明确标 False 才豁免（老候选文件无此字段时按有板块处理）
        if r.get("sector_has_mates") is False or _rank(r) == 0:
            keep.append(r)
            continue
        # 龙一自己买不进（一字 / ST）→ 本线唯一入口是「隔壁」（2026-11-04 用户 BBB）
        _lb = str(r.get("dragon_leader_block") or "")
        if _rank(r) != 1:
            if _ntl and _rank(r) == 2 and _lb:
                # ⛔ 只放第 2 身位，绝不放龙三及以后；仓位减半、龙一开板即走由 pool_call/卡面承接
                r["dragon_gate"] = "next_to_leader"
                r["next_to_leader"] = True
                r["leader_block"] = _lb
                r["dragon_note"] = (f"这条线龙一{r.get('dragon_leader_name') or '别人'}今天买不进"
                                    f"（{_lb}）→ 它是唯一入口，半仓参与，龙一开板即走")
                proxies.append(r)
                keep.append(r)
                continue
            r["dragon_gate"] = "deputy"
            r["dragon_note"] = (f"这条线龙一是 {r.get('dragon_leader_name') or '别人'}"
                                f"（{r.get('dragon_leader_boards') or '?'} 板）")
            moved.append(r)
            continue
        if _lb:
            r["dragon_gate"] = "leader_unbuyable"
            r["dragon_note"] = "它自己是龙一，但今天买不进（一字/ST）"
            moved.append(r)
            continue
        keep.append(r)

    # ── 🚪 隔壁补位（2026-11-04 用户 BBB）─────────────────────────────────
    # 场景（11-04 传媒线实证）：龙一大晟文化一字封死 +9.98% → 它被移出接力池，
    # 龙二龙版传媒（+4.06% 可成交、同题材 6 家涨停）原本只是「次排活口」待在观察池，
    # 结果这条当日最强的线在接力池里彻底没有入口。
    # 规则：**仅当**本线龙一确实买不进（`dragon_leader_block` 非空）时，
    #       把该线第 2 身位从 watch 提回接力池；⛔ 绝不放第 3 名及以后。
    # ⛔ 这是对「只降不升」的唯一例外，边界死守三条：
    #    ① 只因「龙一自己买不进」触发，不因龙一被其它风控否决而触发；
    #    ② 只提 rank==2；③ 仍需自己过 5%/量能/预期区间/身位/四条件/名额。
    blocked_themes: dict[str, dict[str, Any]] = {}
    if _ntl:
        # ⛔ 必须扫**全池**（relay + watch 原始入参），不能只看 `moved`：
        #    11-04 实证——大晟文化当天本来就在观察池（前序闸已降级），relay 是空的，
        #    只看 moved 会让隔壁通道永远不触发。
        for r in list(relay or []) + list(watch or []):
            if _rank(r) != 1 or not str(r.get("dragon_leader_block") or ""):
                continue
            if r.get("sector_has_mates") is False:
                continue
            th = _theme_key_of(r)
            if th:
                blocked_themes[th] = r
        # relay 里被判 leader_unbuyable 的也补进来（上面已覆盖，这里兜底其它路径打标）
        for r in moved:
            if str(r.get("dragon_gate") or "") != "leader_unbuyable":
                continue
            th = _theme_key_of(r)
            if th:
                blocked_themes.setdefault(th, r)
    if blocked_themes:
        stay_watch: list[dict[str, Any]] = []
        for r in (watch or []):
            th = _theme_key_of(r)
            if (th in blocked_themes and _rank(r) == 2
                    and str(r.get("dragon_leader_block") or "")
                    and not r.get("watch_only") and not r.get("frozen")):
                rr = dict(r)
                rr["dragon_gate"] = "next_to_leader"
                rr["next_to_leader"] = True
                rr["leader_block"] = str(r.get("dragon_leader_block") or "")
                rr["dragon_note"] = (
                    f"这条线龙一{r.get('dragon_leader_name') or '别人'}今天买不进"
                    f"（{rr['leader_block']}）→ 它是唯一入口，半仓参与，龙一开板即走")
                keep.append(rr)
                proxies.append(rr)
                continue
            stay_watch.append(r)
        watch = stay_watch

    diag = {
        "enabled": True,
        "before": len(relay),
        "after": len(keep),
        "moved": [{"code": str(r.get("code") or ""), "name": str(r.get("name") or ""),
                   "theme": str(r.get("theme") or ""),
                   "dragon_rank": _rank(r),
                   "leader": str(r.get("dragon_leader_name") or ""),
                   "gate": r.get("dragon_gate")} for r in moved],
        "moved_names": [str(r.get("name") or "") for r in moved],
        "next_to_leader": [{"code": str(r.get("code") or ""), "name": str(r.get("name") or ""),
                            "theme": str(r.get("theme") or ""),
                            "rank": _rank(r), "leader": str(r.get("dragon_leader_name") or ""),
                            "leader_block": str(r.get("leader_block") or "")} for r in proxies],
        "next_to_leader_enabled": _ntl,
    }
    if not moved:
        return keep, watch, diag

    # 降级进观察池：同步 bucket 标签 + 重排序号（⛔ 与 pool_admit 同一条铁律：
    # 桶归属改了标签不改 → 卡面出现「在观察段、标签写接力池」的自相矛盾）
    out_watch = list(watch) + moved
    for _r in moved:
        _r["bucket"] = "watch"
        _r["bucket_label"] = "观察池"
    for _i, _r in enumerate(keep, start=1):
        _r["relay_rank"] = _i
        _r.pop("watch_rank", None)
    for _i, _r in enumerate(out_watch, start=1):
        _r["watch_rank"] = _i
        _r.pop("relay_rank", None)
    return keep, out_watch, diag


# ══════════════════════════════════════════════════════════════════════════
# 🎯 四条件合取硬闸
#    2026-10-27 用户原话：「有就是有 一定要题材龙+情绪OK+量能+梯队，
#    高胜率的个股推荐！！！没有就空！！」
# ══════════════════════════════════════════════════════════════════════════
# ## 为什么必须单独一道闸（旧链路「永远不会空」）
#
# `select_relay_watch` 在 strict 四档全空时会走 `_relay_fallback` 的
# relaxed → broad → emergency 级联，而 emergency 门槛极低
# （lbc 1~7 / open<15 / breakRisk<98 / raw≥45）—— 等于**每个交易日都能凑出候选**。
# 用户要的「没有就空」在旧链路上根本无法表达，只能靠 AI 事后嘴硬。
#
# ## 与 `apply_dragon_gate` 的分工（⛔ 不重复判、也不互相替代）
#   · `apply_dragon_gate` 只判**第 ① 条**（是不是题材龙一），且同样只降不升；
#   · 本闸把用户点名的**四条做成合取**：缺任一 → 移出接力池，**池子允许为空**。
#   两者叠加不冲突：龙一闸先滤一遍，本闸再按另外三条补刀。
#
# ## 四条判据（全部 AND，缺一即降观察）
#   ① **题材龙**：`dragon_rank == 1`（单一源 `core/sector_leader.compute`），
#      且不是孤军（`sector_is_lone`/`theme_is_lone` 均为假、`theme_mates_cnt >= 2`）。
#      ⚠️ 这一条覆盖了 2026-08-24「认可独立总龙头」的旧口径 —— 用户本次明确要求
#      「一定要题材龙 + 梯队」，孤军天然没有梯队。旧口径用
#      `HIGH_WIN_ALLOW_LONE_DRAGON=1` 一键回退。
#   ② **情绪OK**：`env.regime` 不在「冰点」且 `relay_feedback.pressure` 为假。
#      ⛔ `ice` 一律不过 —— 这是全市场级判据，一旦不成立**整个池子清空**，
#      正是用户说的「没有就空」。允许的 regime 用 `HIGH_WIN_OK_REGIMES` 可调。
#
#      ⚠️ 为什么默认**只清 `ice`、不清 `risk_off`**（2026-10-27 回测实证，勿凭直觉改）：
#        对 52 个归档交易日、176 条接力票做次日回测（剔除一字/竞价超窗后 92 条可执行）：
#          risk_on   n=21  均 +1.47%  胜率 61.9%  中位 +1.59%
#          neutral   n=50  均 -0.34%  胜率 44.0%  中位 -1.22%
#          risk_off  n= 5  均 +2.99%  胜率 40.0%  中位 -1.69%   ← 样本太少，**证据不足**
#          ice       n=15  均 -2.54%  胜率 33.3%  中位 -4.52%   ← 唯一明确该清的环境
#        只清 ice：保留侧均 +0.38% / 胜率 48.7%，被清侧 -2.54% / 33.3% → 分离度 +2.92pp
#        连 risk_off 一起清：保留侧降到 +0.20%，且依据只有 5 条样本。
#        ⇒ 默认 `HIGH_WIN_OK_REGIMES="risk_on,neutral,risk_off"`（= 只清冰点）。
#          若日后样本变大证明 risk_off 该清，改环境变量即可，⛔ 不要改这里的默认值口头约定。
#   ③ **量能**：`cje_yi >= 该票档位门槛`，复用入池阶段同一把尺子
#      （`turnover_floor` + `leader_turnover_floor` 的龙头豁免），⛔ 不另造阈值。
#      优先读候选已落盘的 `turnover_floor_used`，缺失才现算。
#   ④ **梯队**：`ladder_has_complete` 为真，或 `ladder_health >= HIGH_WIN_LADDER_MIN`。
#
# ## 诚实降级
#   ⛔ 缺数据不裁决：`dragon_rank` 缺失（旧存档 / 题材映射缺失）→ 该条**不判**，
#      绝不因为「读不到」就把票降级。同理 `ladder_*` 缺失、`cje_yi` 缺失均不硬判。
#      （例外：`sector_has_mates is False` 是**明确的**「无板块」信号，照判。）
#
# ## 开关
#   `ENABLE_HIGH_WIN_RELAY_GATE=0` → 一键回旧行为（relay 永不因本闸为空）。
HIGH_WIN_GATE_ENV = "ENABLE_HIGH_WIN_RELAY_GATE"
# ⛔ 只清 `ice`。理由见上方回测表：risk_off 的「该清」结论只有 5 条样本支撑，不足以砍掉。
# 想恢复「只认 risk_on/neutral」的严口径：`HIGH_WIN_OK_REGIMES="risk_on,neutral"`。
HIGH_WIN_OK_REGIMES = ("risk_on", "neutral", "risk_off")
HIGH_WIN_LADDER_MIN = 6.0          # ladder_health 0~10；6 = 梯队中上（有完整梯队者不看此值）
HIGH_WIN_THEME_MATES_MIN = 2       # 题材内至少 2 家（独苗 = 没有题材梯队）
HIGH_WIN_ALLOW_LONE_DRAGON_ENV = "HIGH_WIN_ALLOW_LONE_DRAGON"
HIGH_WIN_CONDITIONS = ("题材龙", "情绪", "量能", "梯队")

# ## 🪜 梯队门槛按连板数分级（2026-11-06 用户选 A「先验证再定口径」·验证后落地）
#   起因：用户质疑「小题材龙一永远过不了梯队这条」（`calc_ladder_health` 对 2 家题材上限
#         5.8 < 6，结构上不可能过）。把全窗口 relay 票拉出来分档验证后，**假设的形态被推翻**：
#         `ladder_health` 是量化值，5.0 直接跳到 6.4，中间无取值 ⇒ `>=6` 实际等价于
#         「该题材昨日不止 1 家涨停（有同伴/联动）」，不是「题材大小歧视」。
#   真发现的交互（179 个 relay 票次 / 45 个交易日 / 09:25 买入→次日收盘，账本口径）：
#     1~2 板 · 有梯队   n=56  T+1 −2.52%  胜 37.5%
#     1~2 板 · 无梯队   n=51  T+1 −0.07%  胜 41.2%   ← 比「有梯队」更好
#     3 板+  · 有梯队   n=29  T+1 +3.74%  胜 55.2%   ← 唯一稳健正期望格
#     3 板+  · 无梯队   n=39  T+1 −1.82%  胜 46.2%
#     置换检验：3 板+ Δ=+5.56pct p=0.017（显著，8~9 月/10~11 月两段同向）；
#               1~2 板 Δ=−2.45pct p=0.908（不显著，但两段同向）
#   ⇒ **梯队只在 3 板以上有区分力；1~2 板用它筛，反而选中更差的那一半。**
#      与用户既定认知一致：「广度 ≠ 核心」，低板有梯队常是资金铺广度的一日游；
#      而用户「必须在第 1~2 板发现它」的发现时点纪律，正落在这个被误伤的口子上。
#   ⛔ 不是把 6.0 整体下调（那会同时放过 3 板+ 孤军，历史 −1.82%），
#      只对 1~2 板**豁免这一条**，其余三条（题材龙/情绪/量能）照判，个股硬伤一条不放。
#   ⛔ `ENABLE_HIGH_WIN_LADDER_BY_BOARDS=0` 一键回退旧口径（全板数统一 6.0）。
HIGH_WIN_LADDER_BY_BOARDS_ENV = "ENABLE_HIGH_WIN_LADDER_BY_BOARDS"
HIGH_WIN_LADDER_WAIVE_MAX_BOARDS = 2   # ≤2 板豁免梯队；3 板+ 仍守 HIGH_WIN_LADDER_MIN

# ## 💧 量能判据按封板形态分档（2026-11-04 用户「必须补齐」）
#   用户原话：「要考虑它是不是一字涨停。昨天龙版一字涨停，这个时候缩量是很正常的，
#             除非你是换手，换手的话量能太低就肯定说不过去，比如说今天的紫竹。」
#
#   一字板封死 → 成交额天然小，是**结构必然**，不是量能缺陷；
#   换手板成交额低 → 少数资金之间对倒，容易被做局 → 1~2 板也守 5 亿。
#   与进场闸共用同一套形态判据（`seal_gate.board_style` / `style_floor`），
#   ⛔ 但**不复用进场闸的豁免结论**（强封/厚封/热题材豁免的理由不是「成交额够」）。
HIGH_WIN_VOLUME_BY_STYLE_ENV = "ENABLE_HIGH_WIN_VOLUME_BY_STYLE"

# ## 🧊 冰点解冻（2026-10-28 用户「冰点日若开盘后情绪转好 可以出手」）
#   冰点日被本闸移出接力池的票**不是判死刑，是冻结**：保留身份与解冻条件，
#   等 09:25 竞价回暖（`core.market_open.build_market_open` 的 `ready`）再放行。
#
#   ⛔ 分层铁律：**只有市场级条件可解冻**。
#     · 情绪            = 全市场的事。今晨资金集体回暖是同一层的新证据，可推翻昨收判断。
#     · 题材龙/量能/梯队 = 票自己的硬伤。市场回暖救不了一只量能不足、或本来就不是龙一的票。
#   故 `HIGH_WIN_MARKET_LEVEL` 只放「情绪」，⛔ 不要往里加个股级条件。
#
#   回测依据（51 个归档交易日 × relay 票 · 次日开盘买→收盘卖 · 剔一字与竞价超窗）：
#     ice   + 竞价回暖  n=4   均 +1.37%  胜率 50%  中位 +0.68%
#     ice   + 未回暖    n=11  均 -3.97%  胜率 27%  中位 -5.43%
#     且同一判据在 risk_on（+2.07% / -1.07%）、neutral（+1.50% / -1.78%）**同样单调有效**
#     → 跨三种环境同向，不是只在冰点上偶然成立。
#     ⚠️ ice 组样本仅 4 条，**幅度不可当真**；方向性由「三环境同向」支撑。
HIGH_WIN_UNFREEZE_ENV = "ENABLE_ICE_UNFREEZE"
HIGH_WIN_MARKET_LEVEL = ("情绪",)


def high_win_unfreeze_enabled() -> bool:
    """冰点解冻开关（默认开）。`ENABLE_ICE_UNFREEZE=0` → 回退「冰点一票不出」。"""
    return os.environ.get(HIGH_WIN_UNFREEZE_ENV, "1") not in ("0", "false", "False", "")


def high_win_ladder_by_boards() -> bool:
    """梯队门槛是否按连板数分级（默认开）。`ENABLE_HIGH_WIN_LADDER_BY_BOARDS=0` → 回退统一 6.0。"""
    return os.environ.get(HIGH_WIN_LADDER_BY_BOARDS_ENV, "1") not in ("0", "false", "False", "")


def high_win_ladder_waived(c: dict[str, Any]) -> bool:
    """该票是否适用「1~2 板免判梯队」。**纯读**，不改入参。

    ⛔ 只在连板数**明确可读且 ≤2** 时豁免；连板数缺失 → 不豁免（走原判据），
      避免「读不到板数」被当成低板放行。
    """
    if not high_win_ladder_by_boards():
        return False
    b = _to_int(c.get("boards"), 0)
    return 0 < b <= HIGH_WIN_LADDER_WAIVE_MAX_BOARDS


def high_win_market_level_missing(miss: list[str] | None) -> bool:
    """缺失项是否**全部**属市场级（= 可被 09:25 竞价回暖解冻）。

    空列表（无缺失）返回 False —— 无缺失的票本来就不该进冻结流程。
    """
    items = [str(x) for x in (miss or [])]
    if not items:
        return False
    return all(x in HIGH_WIN_MARKET_LEVEL for x in items)


def high_win_gate_enabled() -> bool:
    """四条件合取闸开关（默认开，`ENABLE_HIGH_WIN_RELAY_GATE=0` 回滚）。"""
    return os.environ.get(HIGH_WIN_GATE_ENV, "1") not in ("0", "false", "False", "")


def high_win_allow_lone_dragon() -> bool:
    """是否放行「高标独立总龙头」（2026-08-24 旧口径）。默认 0 = 不放行。"""
    return os.environ.get(HIGH_WIN_ALLOW_LONE_DRAGON_ENV, "0") not in ("0", "false", "False", "")


def high_win_volume_by_style() -> bool:
    """量能这条是否按封板形态分档（默认开，`ENABLE_HIGH_WIN_VOLUME_BY_STYLE=0` 回退纯数值）。

    开：一字板只守数据防呆线 / 换手板 1~2 板守 5 亿 / 形态未知走原门槛。
    关：逐字回到「`cje_yi < 档位门槛`」的旧口径（一字缩量会被判缺量能）。
    """
    return os.environ.get(HIGH_WIN_VOLUME_BY_STYLE_ENV, "1") not in (
        "0", "false", "False", "")


def high_win_ok_regimes() -> tuple[str, ...]:
    raw = os.environ.get("HIGH_WIN_OK_REGIMES", "")
    if not raw.strip():
        return HIGH_WIN_OK_REGIMES
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def high_win_missing(c: dict[str, Any], env: dict[str, Any] | None,
                     market_max: Any = None) -> list[str]:
    """返回该票**缺失**的条件名列表；`[]` = 四条件齐备（可直接推）。

    ⛔ 纯函数、只读，不改入参。缺数据一律不算「缺失」（诚实降级）。
    """
    miss: list[str] = []
    e = env or {}

    # ① 题材龙 ------------------------------------------------------------
    _rank_raw = c.get("dragon_rank")
    _rank = _to_int(_rank_raw, 0)
    _mates = _to_int(c.get("theme_mates_cnt"), 0)
    _has_sector = c.get("sector_has_mates")
    _lone = bool(c.get("sector_is_lone")) or bool(c.get("theme_is_lone"))
    if _rank_raw is None and _has_sector is None:
        pass                                  # 完全无身位信息 → 不裁决（旧存档）
    elif _rank != 1:
        # 🚪 隔壁（2026-11-04 用户 BBB）：龙一自己买不进时，第 2 身位就是本线唯一入口，
        #    「题材龙」这一条按「本线当前可参与的前排」认定，不再重复判它「不是龙一」。
        #    ⛔ 只豁免这一条；情绪 / 量能 / 梯队三条照旧全判——个股级硬伤一条都不放。
        _ntl_pass = bool(
            os.environ.get("HIGH_WIN_NEXT_TO_LEADER_PASS", "1") not in ("0", "false", "False", "")
            and c.get("next_to_leader") and _rank == 2)
        if not _ntl_pass:
            miss.append("题材龙")              # 有信息但不是龙一（含 rank=0「无板块」）
    elif _lone or (_mates and _mates < HIGH_WIN_THEME_MATES_MIN):
        if not high_win_allow_lone_dragon():
            miss.append("题材龙")              # 是龙一但孤军 → 没有题材梯队

    # ② 情绪OK（全市场级）--------------------------------------------------
    _regime = str(e.get("regime") or "")
    if _regime:                               # regime 缺失 → 不裁决
        if _regime not in high_win_ok_regimes():
            miss.append("情绪")
        elif bool((e.get("relay_feedback") or {}).get("pressure")):
            miss.append("情绪")

    # ③ 量能（2026-11-04 用户「必须补齐」·按封板形态分档）──────────────────
    # 用户原话：「要考虑它是不是一字涨停。昨天龙版一字涨停，这个时候缩量是很正常的，
    #          除非你是换手，换手的话量能太低就肯定说不过去，比如说今天的紫竹。」
    #
    # 病根：本闸原来只做 `cje < 1.5亿` 的裸数值比较，不看封板形态。结果：
    #   一字板（封死→成交额天然小）被误判「量能不足」，与进场闸的「强封放行」自相矛盾。
    #   11-04 实测：大晟文化(一字0.34亿)、龙版传媒(一字0.54亿) 都进不了接力池。
    #
    # 修法：**与进场闸共用同一套形态判据**（`seal_gate.board_style` / `style_floor`），
    #   但⛔ 不复用进场闸的「豁免结论」——强封/厚封/热题材豁免的理由是封板质量与题材家数，
    #   不是「成交额够」；拿它当量能达标，会让「换手板缩量」从后门溜进来。
    #
    # 分档：
    #   · 一字板  → 成交额低是结构必然，**不构成量能缺陷**；只守数据防呆线
    #   · 换手板  → 量能低 = 少数资金之间对倒（易被做局）→ 1~2 板也守 5 亿
    #   · 形态未知 → 原门槛（⛔ 缺数据既不没收也不放宽）
    _cje_v = _to_float(c.get("cje_yi"), None)
    if high_win_volume_by_style():
        _style_v = _seal_gate.board_style(c)
        if _style_v == "yizi":
            # ⛔ 不是「无门槛」：跌破防呆线仍要拦（挡 amount 字段异常，不是流动性判据）
            if _cje_v is not None and float(_cje_v) < float(_seal_gate.DATA_SANITY_MIN_YI):
                miss.append("量能")
        elif _style_v == "turnover":
            _floor_v = _seal_gate.style_floor(c, turnover_floor(c.get("boards")))
            _lf_v = leader_turnover_floor(c.get("boards"), market_max)
            if _lf_v is not None:
                _floor_v = min(float(_floor_v), float(_lf_v))
            if _cje_v is not None and float(_cje_v) < float(_floor_v):
                miss.append("量能")
        else:
            _floor_v = _to_float(c.get("turnover_floor_used"), None)
            if _floor_v is None:
                _floor_v = leader_turnover_floor(c.get("boards"), market_max)
                if _floor_v is None:
                    _floor_v = turnover_floor(c.get("boards"))
            if _cje_v is not None and _floor_v is not None and float(_cje_v) < float(_floor_v):
                miss.append("量能")
    else:
        # 回退旧口径（`ENABLE_HIGH_WIN_VOLUME_BY_STYLE=0`）：纯数值，不看形态
        _floor_v = _to_float(c.get("turnover_floor_used"), None)
        if _floor_v is None:
            _floor_v = leader_turnover_floor(c.get("boards"), market_max)
            if _floor_v is None:
                _floor_v = turnover_floor(c.get("boards"))
        if _cje_v is not None and _floor_v is not None and float(_cje_v) < float(_floor_v):
            miss.append("量能")

    # ④ 梯队 --------------------------------------------------------------
    #   🪜 1~2 板免判（2026-11-06）：见 HIGH_WIN_LADDER_BY_BOARDS_ENV 上方回测表——
    #      该档「有梯队」比「无梯队」更差（−2.52% vs −0.07%），用它筛是反向选择。
    #      ⛔ 只跳过这一条；3 板+ 仍守 HIGH_WIN_LADDER_MIN，缺板数也照旧判。
    _has_complete = c.get("ladder_has_complete")
    _lh = _to_float(c.get("ladder_health"), None)
    if high_win_ladder_waived(c):
        pass                                  # 1~2 板：梯队不作硬条件（留痕在 apply_high_win_gate）
    elif _has_complete is None and _lh is None:
        pass                                  # 无梯队信息 → 不裁决
    elif not _has_complete and (_lh is None or float(_lh) < HIGH_WIN_LADDER_MIN):
        miss.append("梯队")

    return miss


def apply_high_win_gate(
    relay: list[dict[str, Any]],
    watch: list[dict[str, Any]],
    env: dict[str, Any] | None = None,
    *,
    market_max_boards: Any = None,
    enabled: bool | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """四条件合取：缺任一 → 移出接力池进观察池（**接力池允许为空**）。

    返回 `(relay, watch, diagnostics)`。降级票打标：
      · `high_win_gate`   = 缺失条件名列表（如 `["情绪"]`），供 diagnostics / 复盘追溯
      · `watch_only`      = True（防裁决层把它捡回去）
    ⛔ 刻意**不写** `watch_reason`：用户 2026-10-27「不用给」——
       池子空就空，不需要给市场找说法。
    """
    on = high_win_gate_enabled() if enabled is None else bool(enabled)
    if not on:
        return list(relay or []), list(watch or []), {
            "enabled": False, "reason": f"{HIGH_WIN_GATE_ENV}=0",
        }

    keep: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    _unfreeze_on = high_win_unfreeze_enabled()
    for r in (relay or []):
        miss = high_win_missing(r, env, market_max_boards)
        _waived = high_win_ladder_waived(r)
        if miss:
            r["high_win_gate"] = miss
            r["watch_only"] = True
            # 🧊 冻结：只缺市场级条件（情绪）→ 留解冻资格，等 09:25 竞价回暖放行。
            #   缺个股级硬伤（题材龙/量能/梯队）→ ⛔ 不给资格，市场回暖救不了它。
            if _unfreeze_on and high_win_market_level_missing(miss):
                r["frozen"] = True
                r["unfreeze_on"] = "market_open_ready"
            else:
                r.pop("frozen", None)
                r.pop("unfreeze_on", None)
            moved.append(r)
        else:
            r.pop("high_win_gate", None)
            r.pop("frozen", None)
            r.pop("unfreeze_on", None)
            # 🪜 留痕：这只票是靠「1~2 板免判梯队」进池的，复盘能一眼看出原因。
            if _waived:
                r["ladder_waived"] = f"boards<={HIGH_WIN_LADDER_WAIVE_MAX_BOARDS}"
            else:
                r.pop("ladder_waived", None)
            keep.append(r)

    _frozen = [r for r in moved if r.get("frozen")]
    diag: dict[str, Any] = {
        "enabled": True,
        "before": len(relay or []),
        "after": len(keep),
        "conditions": list(HIGH_WIN_CONDITIONS),
        "regime": str((env or {}).get("regime") or ""),
        "ok_regimes": list(high_win_ok_regimes()),
        "unfreeze_enabled": _unfreeze_on,
        "ladder_by_boards": high_win_ladder_by_boards(),
        "ladder_waive_max_boards": HIGH_WIN_LADDER_WAIVE_MAX_BOARDS,
        "ladder_waived": [str(r.get("code") or "") for r in keep if r.get("ladder_waived")],
        "frozen_count": len(_frozen),
        "frozen": [{"code": str(r.get("code") or ""), "name": str(r.get("name") or ""),
                    "unfreeze_on": str(r.get("unfreeze_on") or ""),
                    "missing": list(r.get("high_win_gate") or [])} for r in _frozen],
        "moved": [{"code": str(r.get("code") or ""), "name": str(r.get("name") or ""),
                   "theme": str(r.get("core_theme") or r.get("theme") or ""),
                   "missing": list(r.get("high_win_gate") or []),
                   "frozen": bool(r.get("frozen"))} for r in moved],
        "moved_names": [str(r.get("name") or "") for r in moved],
    }
    if not moved:
        return keep, list(watch or []), diag

    out_watch = list(watch or []) + moved
    for _r in moved:
        _r["bucket"] = "watch"
        _r["bucket_label"] = "观察池"
    # 桶归属改了，序号与标签必须跟着改（与 pool_admit / thin_seal_top 同一条铁律）
    for _i, _r in enumerate(keep, start=1):
        _r["relay_rank"] = _i
        _r.pop("watch_rank", None)
    for _i, _r in enumerate(out_watch, start=1):
        _r["watch_rank"] = _i
        _r.pop("relay_rank", None)
    return keep, out_watch, diag


def _build_early_code_map(early: list[dict[str, Any]]) -> dict[str, str]:
    """从 pulse.early（盘中早鸟埋伏信号）构建 {code: signal_type} 映射。

    early 元素结构（intraday_pulse.build_early_signals 产出）：
        {"type": "爆发前夜", "sector": "...", "buy": {"code": "300862", "pct": 15.82, ...},
         "count": 1, "members": [{"name": "蓝盾光电", "pct": 15.82}]}
    buy 为主信号（必含 code），members 为同题材跟风（可能无 code，按 name 忽略）。
    返回 code→type 映射；buy 缺失/无 code 的 early 条目跳过（诚实降级，不编造）。
    """
    m: dict[str, str] = {}
    for ea in early or []:
        sig_type = str(ea.get("type") or "盘中强势")
        buy = ea.get("buy") or {}
        if isinstance(buy, dict):
            c = str(buy.get("code") or "").strip()
            if c:
                m[c] = sig_type
        elif isinstance(buy, list):
            for b in buy:
                c = str(b.get("code") or "").strip() if isinstance(b, dict) else ""
                if c:
                    m[c] = sig_type
    return m


def _build_weak2strong_map(zt_history_dir: Path, today: str, yesterday: str,
                           zt_list_today: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """弱转强识别（2026-08-20 用户「神奇制药竞价从地板拉起·经典弱转强」）：
    断板回封 = 昨日(T-1)未涨停但 T-2 曾涨停（含连板史）→ 今日回封涨停。

    识别规则（读 cache/zt_history/zt_YYYY-MM-DD.json 真实历史，禁编造）：
      - 今日涨停池每只票，查 T-1 与 T-2 两日 zt_history：
      - T-1 不在涨停池（断板）且 T-2 在涨停池（曾涨停/连板）→ 断板回封 = 弱转强
      - 附加分歧信息：T-2 当日 zbc（炸板次数）、hs（换手）> 全池中位 → 标记分歧→一致
    返回 {code: {"weak2strong": true, "w2s_type": "断板回封", "prev_boards": N, "prev_date": "..."}}
    """
    m: dict[str, dict[str, Any]] = {}

    def _day_codes(date: str) -> set[str]:
        p = zt_history_dir / f"zt_{date}.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return {str(it.get("code") or "") for it in (d.get("zt_list") or [])}
        except Exception:
            return set()

    def _day_meta(date: str, code: str) -> dict[str, Any]:
        p = zt_history_dir / f"zt_{date}.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            for it in (d.get("zt_list") or []):
                if str(it.get("code") or "") == code:
                    return it
        except Exception:
            pass
        return {}

    def _prev_date(d: str) -> str:
        try:
            files = sorted([p.stem[3:] for p in zt_history_dir.glob("zt_*.json")])
            if d in files:
                files = [f for f in files if f < d]
            return files[-1] if files else d
        except Exception:
            return d

    t1_date = _prev_date(today)              # T-1（昨日·最近交易日）
    t2_date = _prev_date(t1_date)            # T-2（前日）
    t1 = _day_codes(t1_date)
    for it in zt_list_today:
        code = str(it.get("dm") or it.get("code") or "")
        if not code:
            continue
        if code in t1:
            continue  # 昨日仍涨停 = 连板/晋级，不是断板回封
        meta2 = _day_meta(t2_date, code)
        if meta2:
            m[code] = {
                "weak2strong": True,
                "w2s_type": "断板回封",
                "prev_boards": int(meta2.get("boards") or 0),
                "prev_zbc": int(meta2.get("zbc") or 0),
                "prev_hs": meta2.get("hs"),
                "prev_date": t2_date,
            }
    return m


def _build_second_leg_map(zt_history_dir: Path, today: str,
                          scored: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """完整收盘序列确认前段连板及调整后重启；零散首板不抹掉前段历史。"""
    days: list[tuple[str, dict[str, dict[str, Any]]]] = []
    history_files = [path for path in sorted(zt_history_dir.glob("zt_*.json")) if path.stem[3:] < today][-40:]
    for path in history_files:
        date = path.stem[3:]
        try:
            day = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(day, dict):
                continue
            rows = day.get("zt_list")
            stamp = str(day.get("generated_ts") or "")
            # 断板只能从完整收盘池确认；任何缺日都会在交易日相邻检查处中止。
            if (day.get("date") != date or not isinstance(rows, list)
                    or day.get("zt_count") != len(rows) or stamp[:10] != date or stamp[11:16] < "15:00"):
                continue
            days.append((date, {str(row["code"]): row for row in rows}))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    result: dict[str, dict[str, Any]] = {}
    for cand in scored:
        code = str(cand.get("code") or "")
        boards = int(cand.get("boards") or 0)
        if not code or boards < 1 or len(days) < boards + 1:
            continue
        restart_index = len(days) - boards + 1
        adjustment_end = restart_index - 1
        if adjustment_end < 0 or code in days[adjustment_end][1]:
            continue
        # 找最近一轮真实连板；调整期的零散首板仅留事实，不冒充连续连板。
        prior_index = next((i for i in range(adjustment_end - 1, -1, -1)
                            if int((days[i][1].get(code) or {}).get("boards") or 0) >= 2), None)
        if prior_index is None:
            continue
        break_index = prior_index + 1
        prior_date, prior_rows = days[prior_index]
        break_date, break_rows = days[break_index]
        prior_boards = int(prior_rows[code]["boards"])
        if code in break_rows or break_index < prior_boards:
            continue
        window_dates = [d for d, _ in days[break_index - prior_boards:]] + [today]
        if any(next_a_share_trading_day(a) != b for a, b in zip(window_dates, window_dates[1:])):
            continue
        # 前段 N 板也逐日核实；只凭断板前一日的板数会把缺日历史误认成 N+N。
        prior_valid = True
        for prior_board in range(1, prior_boards + 1):
            prior_index = break_index - prior_boards + prior_board - 1
            day_date, day_rows = days[prior_index]
            if int((day_rows.get(code) or {}).get("boards") or 0) != prior_board:
                prior_valid = False
                break
            if prior_board > 1 and next_a_share_trading_day(days[prior_index - 1][0]) != day_date:
                prior_valid = False
                break
        if not prior_valid:
            continue
        previous = days[adjustment_end][0]
        valid = True
        # 第二段必须从 1 板逐日递增到今日 M 板，缺完整交易日不推断连续性。
        for current_board in range(1, boards):
            day_date, day_rows = days[restart_index + current_board - 1]
            if (next_a_share_trading_day(previous) != day_date
                    or int((day_rows.get(code) or {}).get("boards") or 0) != current_board):
                valid = False
                break
            previous = day_date
        if not valid or next_a_share_trading_day(previous) != today:
            continue
        result[code] = {"prior_boards": prior_boards, "second_boards": boards,
                        "break_date": break_date, "n_plus_n_confirmed": boards >= prior_boards,
                        "quality": "ok", "asof": today,
                        "source": "cache/zt_history 完整收盘池逐日核验",
                        "prior_dates": [days[i][0] for i in range(break_index - prior_boards, break_index)],
                        "adjustment_dates": [days[i][0] for i in range(break_index, restart_index)],
                        "adjustment_sessions": restart_index - break_index,
                        "interim_limitup_dates": [days[i][0] for i in range(break_index, restart_index)
                                                  if code in days[i][1]],
                        "restart_dates": [days[i][0] for i in range(restart_index, len(days))] + [today],
                        "technical_status": "unknown"}
    return result


def _load_theme_ctx() -> dict[str, Any] | None:
    """
    加载题材动量上下文（来自 theme_roll 的 5 日题材强度表，可选）。
    用于给「处于 boom / 回流候选」的题材在期望层额外加分。文件不存在时返回 None。
    """
    p = DATA_DIR / "theme_roll_5d.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    ctx: dict[str, Any] = {}
    for t in (d.get("themes") or []):
        nm = t.get("theme")
        if nm:
            ctx[nm] = {
                "phase": t.get("phase"),
                "confidence": t.get("confidence", 0.0),
                "cooled_days": t.get("cooled_days"),
            }
    return ctx or None


def _rank_and_split(
    scored: list[dict[str, Any]],
    env: dict[str, Any],
    theme_ctx: dict[str, Any] | None = None,
    strong_sectors: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """
    ⛔⛔ 2026-09-29 标注：**本函数是废弃死代码，从未被调用**。

    eod 的实际排序/拆池入口 = `core.quant_score.select_relay_watch`
    （见 `run_eod()` 内 `relay, watch, diagnostics, sel_mode = select_relay_watch(...)`）。
    因此本函数体内的 `STRONG_SECTOR_*` 加权、`_load_strong_sectors()` 调用
    **对候选结果零影响**；`diagnostics["strong_sector_persistent"]` 等键也从不产出
    （实证：2026-09-29 的 candidates diagnostics 无此键）。

    ⚠️ 为什么要写这段（踩过的坑）：2026-09-23 同日重跑 eod 出现 relay 1→3、
    个别 score 变动，当时把根因归到「`_load_strong_sectors()` 未传 `exclude_date`
    导致当时候选自举加分」并写进了记忆 —— **该归因是错的**（该函数不在生效路径上）。
    2026-09-29 实测：候选文件存在时重跑 eod，relay/watch 与逐票 score **完全一致**。
    真实差异只可能来自**输入文件在两次跑之间被别的任务更新**
    （`feedback_matrix.json` / `pulse_snapshots/latest.json` / 涨停池），属「输入新鲜度」问题，
    不是自举。**排查同日重跑不一致，请看输入文件 mtime，不要来改本函数。**

    原始意图（仅供理解历史设计，勿据此推理现行为）：
    排序分 = 龙头算法分(点火+爆发+先发+题材) + 连板高度 + 板块梯队(核心) + 情绪代理 + 主板倾斜
            + 容量加权(次要,QR对齐) + 日报verdict(元覆盖,用户指定) + 最近强势板块加权(用户:聚焦题材)。
    板块梯队：题材内梯队健康度(0~10)线性映射 LADDER_WEIGHT，完整梯队 sector 龙头额外加成。
    """
    if strong_sectors is None:
        strong_sectors = _load_strong_sectors()
    env_c = _environment_component(env)
    for it in scored:
        if it.get("is_st"):
            it["_rank_score"] = -999.0
            continue
        # 一字板：当日封死，竞价无法买入（无参与机会），原版 quant-review 选股规范
        # 明确定义为「换手<3% = 无参与机会」。排除出可交易接力/观察池（同 ST 处理）。
        if it.get("is_one_word"):
            it["_rank_score"] = -999.0
            it["excluded_reason"] = "一字板·无参与机会"
            continue
        rs = it["score"] + _height_component(it.get("boards", 1)) + env_c
        # 主板倾斜已撤销（2026-08-13 创业板开放）；MAINBOARD_BONUS=0 不再影响排序
        if not it.get("is_20cm"):
            rs += MAINBOARD_BONUS
        # 板块梯队（核心驱动）：题材梯队健康度越高，该股所处主线越扎实
        lad = (it.get("ladder_health") or 0.0) / 10.0 * LADDER_WEIGHT
        rs += lad
        if it.get("is_sector_dragon") and it.get("ladder_has_complete"):
            rs += LADDER_DRAGON_BONUS  # 完整梯队 sector 龙头额外加成
        # 反吃日报 verdict：板块无效应→降权，确认主线→加权（用户：板块无效应就降权）
        dv = it.get("daily_verdict")
        if dv:
            for f in dv.get("flags", []):
                rs += SECTOR_VERDICT_BONUS.get(f, 0.0)
        # 最近强势板块加权（用户纪律 2026-08-13：聚焦题材/主线/强势板块）
        # 数据源=聚合最近N天 candidates.theme；持续主线加权、历史单日小加权、清单外温和降权
        ss = strong_sectors.get(it.get("theme"))
        if ss:
            if ss["persistent"]:
                rs += STRONG_SECTOR_PERSIST_BONUS
                it["strong_sector_bonus"] = STRONG_SECTOR_PERSIST_BONUS
            else:
                rs += STRONG_SECTOR_RECENT_BONUS
                it["strong_sector_bonus"] = STRONG_SECTOR_RECENT_BONUS
        else:
            rs += STRONG_SECTOR_OUTSIDE_PENALTY
            it["strong_sector_bonus"] = STRONG_SECTOR_OUTSIDE_PENALTY
        # 容量因子加权（向量化靠拢：仅作为排序分内加权项，参与纯排名，不绕过排名）
        rs += (it.get("cap_score") or 0.0) * CAPACITY_WEIGHT
        it["_rank_score"] = round(rs, 2)

    ranked = sorted(
        [it for it in scored if it["_rank_score"] > -999.0],
        key=lambda x: x["_rank_score"], reverse=True,
    )

    # 情绪差时收缩接力池
    relay_cap = RELAY_TOPN
    if env.get("regime") == "risk_off":
        relay_cap = 4
    elif env.get("regime") == "neutral":
        relay_cap = 6

    relay = ranked[:relay_cap]
    relay_names = {it["code"] for it in relay}
    watch = [it for it in ranked if it["code"] not in relay_names][:WATCH_TOPN]

    # 注：已移除原"容量风格预留位绕过排名强行塞入观察池"的逻辑（CAPACITY_WATCH_SLOTS），
    #     容量票现在只凭 cap_score*CAPACITY_WEIGHT 在上面的纯排名中竞争，保证选股纯模型驱动。

    # 接力选择模式（对齐 quant 级联：strict→relaxed→broad→emergency，用 env 周期替代原版 tide gate）
    sel_mode = relay_selection_mode_for(env, len(relay))
    is_broad_fallback = sel_mode in ("broad", "emergency")

    for idx, it in enumerate(relay, start=1):
        it["relay_rank"] = idx
        it["relay_selection_mode"] = sel_mode
        it["is_broad_fallback"] = is_broad_fallback
        it["dim_panel"] = build_dimension_panel(it, env, theme_ctx)
        it["expectation"] = build_ai_expectation(it, env, theme_ctx)
        it["bucket"] = "relay"
        it["bucket_label"] = "接力池"
    for idx, it in enumerate(watch, start=1):
        it["watch_rank"] = idx
        it["dim_panel"] = build_dimension_panel(it, env, theme_ctx)
        it["expectation"] = build_ai_expectation(it, env, theme_ctx)
        it["bucket"] = "watch"
        it["bucket_label"] = "观察池"
        # 观察池分组：容量核心(风格标签) / 高标(≥3板) / 补充观察，便于卡片分区
        if it.get("style_tag") == "容量":
            it["watch_group"] = "容量核心"
        else:
            it["watch_group"] = "高标/题材核心" if it.get("boards", 1) >= 3 else "补充观察"

    # 🏷 题材兜底（2026-11-16 用户「有些票连题材都没有，必须把题材找出来」）：
    #    当日快照没覆盖到的票 → 题材库 → 同花顺概念 → 行业兜底，并留 `theme_source` 痕迹。
    try:
        from common.theme_fallback import resolve as _resolve_theme
        _filled = 0
        for _bucket in (relay, watch):
            for _r in _bucket:
                _t = str(_r.get("core_theme") or _r.get("theme_name") or "").strip()
                if _t and _t != "—":
                    continue
                _rs = _resolve_theme(str(_r.get("code") or ""), str(zt.get("date") or ""),
                                     sector=_r.get("sector"),
                                     reason=_hy_reason.get(str(_r.get("code") or "").zfill(6)))
                if _rs.get("theme"):
                    _r["core_theme"] = _rs["theme"]
                    _r["theme_name"] = _rs["theme"]
                    _r["theme_source"] = _rs["source"]
                    _r["theme_note"] = _rs.get("note") or ""
                    _filled += 1
        if _filled:
            print(f"[EOD] 🏷 题材兜底补全 {_filled} 只")
        # 🏷 把同花顺涨停原因挂到候选行（终端卡片「原因」行直接读它）
        _n_reason = 0
        for _bucket in (relay, watch):
            for _r in _bucket:
                _rc = _hy_reason.get(str(_r.get("code") or "").zfill(6))
                if _rc:
                    _r["limit_up_reason"] = _rc
                    _n_reason += 1
        if _n_reason:
            print(f"[EOD] 🏷 涨停原因挂载 {_n_reason} 只")
        # 🧲 收盘共振分（0~3）：成线 / 高度 / 早封，三项各 1 分
        #    ⛔ 与 09:25 的竞价共振不是一回事：这里是**收盘可得的事实**，
        #    用于盘后预案排序与卡面标注；竞价共振仍在 09:25 现算。
        from common.theme_fallback import resolve as _r2  # noqa: F401  (保持导入语义一致)
        _ladder_by_theme: dict[str, dict] = {}
        for _r in (zt.get("ztgc") or []):
            _t = str(_r.get("core_theme") or "").strip()
            if not _t:
                continue
            _b = int(_r.get("boards") or 0)
            _fbt = str(_r.get("fbt") or "999999")
            _cur = _ladder_by_theme.setdefault(_t, {"mates": 0, "max_boards": 0, "earliest": "999999"})
            _cur["mates"] += 1
            _cur["max_boards"] = max(_cur["max_boards"], _b)
            _cur["earliest"] = min(_cur["earliest"], _fbt)
        _n_res = 0
        for _bucket in (relay, watch):
            for _r in _bucket:
                _t = str(_r.get("core_theme") or _r.get("theme_name") or "").strip()
                _st = _ladder_by_theme.get(_t)
                if not _st:
                    continue
                _f1 = 1 if _st["mates"] >= 3 else 0
                _f2 = 1 if _st["max_boards"] >= 2 else 0
                _f3 = 1 if _st["earliest"] <= "093500" else 0
                _r["close_resonance"] = {
                    "score": _f1 + _f2 + _f3, "line": _t,
                    "mates": _st["mates"], "max_boards": _st["max_boards"],
                    "earliest": _st["earliest"],
                }
                _n_res += 1
        if _n_res:
            print(f"[EOD] 🧲 收盘共振分挂载 {_n_res} 只")
    except Exception as _e:  # noqa: BLE001
        print(f"[EOD] 题材兜底降级（不影响主链）：{_e}")

    diagnostics = {
        "scored": len(scored),
        "excluded_st": sum(1 for it in scored if it.get("is_st")),
        "excluded_one_word": sum(1 for it in scored if it.get("is_one_word")),
        "relay_count": len(relay),
        "watch_count": len(watch),
        "regime": env.get("regime"),
        "env_component": round(env_c, 2),
        "strong_sector_persistent": sorted(t for t, v in strong_sectors.items() if v["persistent"]),
        "strong_sector_outside_penalized": sum(1 for it in ranked if it.get("strong_sector_bonus", 0) < 0),
    }
    return relay, watch, diagnostics


# 🇨🇳 首板观察判据（2026-11-11 用户「股性强的票，首板就要关注；二板才看就危险了」）
# 两条任一即可：
#   ① 封得干脆：封板 ≤10:00（09:30-10:00 都算）且零炸板；
#   ② 股性强：近一年涨停 ≥ N 次 ∧ 历史最高连板 ≥2（老妖股的首板才是关注点）。
# ⛔ 只决定「进不进观察池」，不看买入；⛔ 二板及以上不走这条。
def first_board_observe_reason(row: dict, gene: dict | None, *,
                               gene_min_zt: int = 6) -> str | None:
    if str(row.get("boards")) not in ("1", "1.0"):
        try:
            if int(row.get("boards")) != 1:
                return None
        except (TypeError, ValueError):
            return None
    if row.get("is_one_word") is True:
        return None
    fbt = str(row.get("fbt") or "").strip()[:6]
    try:
        zbc = float(row.get("zbc") or 0)
    except (TypeError, ValueError):
        zbc = 0.0
    if fbt and fbt <= "100000" and zbc == 0:
        return f"首板 {fbt[:2]}:{fbt[2:4]} 封板、零炸板"
    if isinstance(gene, dict):
        try:
            zt_n = int(gene.get("zt_n") or 0)
            max_lbc = int(gene.get("max_lbc") or 0)
        except (TypeError, ValueError):
            zt_n, max_lbc = 0, 0
        if zt_n >= gene_min_zt and max_lbc >= 2:
            return f"首板·股性强（近一年涨停 {zt_n} 次 / 最高 {max_lbc} 连板）"
    return None


def run_eod(
    cache_dir: Path | None = None,
    *,
    out_path: Path | None = None,
    narratives: dict[str, str] | None = None,
    global_narrative: str | None = None,
    ai_env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    收盘选股主流程（增强版：spec §4.3 十一维 raw_score + 真实环境 + AI 研判）。

    - 评分严格按 spec §4.3（环境/板块/接力/龙头/容量/风险/机会 + identity/height/market_gate/tide）。
      其中 环境/板块/风险/潮汐 四+一维由 pulse_snapshots + feedback_matrix **真实驱动**；
      AI 研判（ai_env.json）作为这些 spec 自有维度的**输入**折叠，不另立打分维度。
    - 接力/观察选择严格按 spec §4.4 四档（高度突破/高标龙头/核心接力/1进2）+ strict→级联。
    - narratives / global_narrative：agent 逐只点评 + 全局综合研判（AI 文案层）。
    - 铁律保留：ST 排除、一字板排除、成交额<5亿 剔除、溢价门控（expectation 层）。
    """
    zt = load_zt_pool(cache_dir)
    env = load_environment(cache_dir)
    pulse = load_pulse(cache_dir)            # 真实大盘+板块环境（pulse 快照）
    from common.json_io import load_json
    effect = load_json((cache_dir or ROOT.parent / "cache") / "selection_effect.json")
    # 盘后路径判断只认候选当日的收盘后快照；盘中暂态不改变既有分池。
    if (isinstance(effect, dict) and effect.get("date") == zt["date"]
            and str(effect.get("generated_ts") or "")[:10] == zt["date"]
            and str(effect.get("generated_ts") or "")[11:16] >= "15:00"
            and isinstance(effect.get("market"), dict)):
        env["relay_feedback"] = effect["market"]

    # ⛔ 2026-08-25 三修（用户「黄河旋风明明是钻石·你把它定到机器人里去了·以题材的涨停
    # 为准不要以板块的涨停」）：**完整涨停池(ztgc)回填 core_theme（炒作题材）**，且在
    # _score_all 之前——候选行继承 core_theme 后，annotate_lone 的 _theme_key 优先用
    # 炒作题材聚合（黄河旋风=金刚石/培育钻石 归钻石，不被行业 通用设备→机器人 误导）。
    # 旧实现只在 annotate_lone 之后对 relay+watch 候选拉题材，pool 行只有静态行业
    # → 候选与 pool 失配，题材层聚合到「机器人」错误桶（实证：黄河旋风 theme_name=机器人
    # ·theme_mates_cnt=6 误判）。ThemeFetcher 带缓存（fetch_all 幂等），后续候选回填复用。
    # ⛔ 2026-10-18 P0（用户「按顺序处理」·治「选股不可复现」）：**题材映射必须走当日快照**。
    # 旧实现直连 `ThemeFetcher().fetch_all()`，映射随抓取时刻漂移 → core_theme/板块梯队变
    # → 同一涨停池算出两套 relay/watch（实证 10-16：长城军工 54.51→58.28、接力池
    # [晨曦航空,尖峰集团]→[冰山冷热,新华文轩]）。现由 `get_theme_map` 统一：
    # 首跑抓接口并落 cache/theme_snapshot/theme_<date>.json，同日重跑/回放只读快照。
    # 开关：THEME_SNAPSHOT_ENABLED=0 回旧行为；THEME_SNAPSHOT_FORCE_REFRESH=1 强制刷新。
    # 🥇 2026-11-16 用户「所有涨停票都走同花顺+涨停原因」：
    #    选股前先把当天同花顺涨停池（带 limit_up_reason）抓到本地，缺失就现抓一次。
    #    ⛔ 失败只降级（回退选股宝快照），不阻断选池。
    _hy_reason: dict[str, str] = {}
    try:
        import subprocess as _sp_hy
        from datetime import datetime as _dt_hy
        from zoneinfo import ZoneInfo as _ZI_hy
        _d8 = _dt_hy.now(_ZI_hy("Asia/Shanghai")).strftime("%Y%m%d")
        _hp = ROOT.parent / "cache" / f"hithink_zt_{_d8}.json"
        if not _hp.exists():
            _sp_hy.run([sys.executable, str(ROOT.parent / "scripts" / "hithink_fetch.py"), "limitup"],
                       check=False, timeout=120, stdout=_sp_hy.DEVNULL, stderr=_sp_hy.DEVNULL)
        if _hp.exists():
            _hd = json.loads(_hp.read_text(encoding="utf-8"))
            for _it in (_hd.get("items") or []):
                _c = str(_it.get("code") or "").zfill(6)
                _r = str(_it.get("limit_up_reason") or _it.get("reason") or "").strip()
                if _c and _r:
                    _hy_reason[_c] = _r
            print(f"[EOD] 🥇 同花顺涨停原因 {len(_hy_reason)} 条（题材归因优先用它）")
    except Exception as _e:  # noqa: BLE001
        print(f"[EOD] 同花顺涨停原因不可用（回退选股宝快照）：{_e}")

    _theme_map_all: dict[str, Any] = {}
    _theme_meta: dict[str, Any] = {"source": "empty", "count": 0}
    try:
        from common.stock_theme import get_theme_map
        # cache_dir 透传：run_eod 指定了自定义缓存目录时，快照要落在同一处（默认→项目 cache）
        _theme_map_all, _theme_meta = get_theme_map(zt["date"], cache_dir=cache_dir)
        for _z in (zt.get("ztgc") or []):
            _zc = str(_z.get("dm") or _z.get("code") or "")
            _ti = _theme_map_all.get(_zc) if _zc else None
            if _ti and _ti.get("theme"):
                _z["core_theme"] = _ti.get("theme")
                # 🏷 归并名（2026-11-11 用户「题材一定要稳住」）：`theme` 是细名
                #    （创新药/医疗服务/医药…），`theme_base` 是同义归并后的稳定名。
                #    判「同题材身位/同线关系」时应优先用 base，避免同义换名造成假漂移。
                _z["theme_base"] = str(_ti.get("theme_base") or _ti.get("theme") or "")
                # 🏷 题材来源留痕（2026-11-11 用户「题材一定要稳住」）：
                #    snapshot = 题材库/快照命中（真题材）；sector = 只能拿到行业名兜底。
                #    卡面必须把「行业兜底」标出来，⛔ 不能让行业名冒充题材。
                _z["theme_source"] = "snapshot"
            else:
                # 🏷 2026-11-16：快照没有（选股宝把它标成「其他」）→ 先用同花顺概念兜底，
                #    否则这只票会被当「无题材」直接剔出候选池（实证 002205 国统股份 3 板被删）。
                _fb = {}
                try:
                    from common.theme_fallback import resolve as _resolve_theme
                    _fb = _resolve_theme(_zc, str(zt.get("date") or ""), sector=_z.get("sector"),
                                         reason=_hy_reason.get(_zc))
                except Exception:  # noqa: BLE001
                    _fb = {}
                if _fb.get("theme"):
                    _z["core_theme"] = _fb["theme"]
                    _z["theme_base"] = _fb["theme"]
                    _z["theme_source"] = _fb.get("source") or "fallback"
                    _z["theme_note"] = _fb.get("note") or ""
                else:
                    _z.setdefault("core_theme", "")
                    _z["theme_source"] = "sector"
    except Exception as _e:
        _theme_meta = {"source": "error", "count": 0, "error": str(_e)}
        pass  # 题材接口失败 → pool 行无 core_theme，回退行业映射（不编造）

    # 🛡️ 题材基座完整性守卫（2026-11-03 用户「以选股宝为基座」的前置条件）
    #   ⛔ 为什么必须有：选股宝 `surge_stock` 接口**盘中活·盘后关**，当天没抓到就是
    #   永久空洞、没有历史可回补。把分类基座压在这样一个源上，必须先能回答「今天抓全了吗」。
    #   ⛔ 边界：**只判定 + 留痕，不改任何数据、不阻断主链**（拦截要等有明确证据再开）。
    _theme_guard: dict[str, Any] = {}
    if os.environ.get("ENABLE_THEME_GUARD", "1") != "0":
        try:
            from common.theme_base import guard_snapshot, guard_line
            _zt_codes_for_guard = [
                str(_z.get("dm") or _z.get("code") or "")[:6]
                for _z in (zt.get("ztgc") or [])
            ]
            _theme_guard = guard_snapshot(
                zt["date"], cache_dir=cache_dir,
                zt_codes=[c for c in _zt_codes_for_guard if c],
            )
            _theme_guard["line"] = guard_line(_theme_guard)
        except Exception as _e:  # noqa: BLE001
            _theme_guard = {"schema": "theme_base_v1", "ok": False,
                            "status": "error", "error": type(_e).__name__}

    scored = _score_all(zt["ztgc"], zt["code2themes"], zt["hot_sectors"])

    # P3（2026-10-18 用户「按顺序处理」·打通「复盘 → 选股」）：读**上一交易日**复盘推演，
    # 给候选打 `review_flag`（height/new_direction/caution）+ `review_bonus`（±1.2 排序倾斜）。
    # ⛔ 边界：不塞池、不改 raw_score、不改入池资格；只影响 relay/watch 同层展示顺序。
    # 读不到文件（昨日没写推演）→ 静默跳过，不编造"复盘说了什么"。
    review_directions: dict[str, Any] | None = None
    review_date = ""
    review_stats: dict[str, Any] = {"available": False}
    try:
        from scripts.review_directions import annotate_candidates, load_for_eod
        review_directions, review_date = load_for_eod(zt["date"], cache_dir=cache_dir)
        if review_directions:
            review_stats = annotate_candidates(scored, review_directions)
            review_stats["review_date"] = review_date
            # 推演兑现核对：复盘点名、但今日仍未涨停的票（= 复盘说对了没走出来 / 说错了没封板）
            # ⛔ 只统计不评价——"为什么没走出来"要 AI 结合盘面判断，脚本不下结论。
            _pool_names = {str(s.get("mc") or "").strip() for s in (zt.get("ztgc") or [])}
            _pool_codes = {str(s.get("dm") or "").strip() for s in (zt.get("ztgc") or [])}
            _missed: list[dict[str, Any]] = []
            for _kind, _field in (("height", "height"),
                                  ("new_direction", "new_directions"),
                                  ("caution", "cautions")):
                for _it in (review_directions.get(_field) or []):
                    _nm = str(_it.get("name") or "").strip()
                    _cd = str(_it.get("code") or "").strip()
                    if (_cd and _cd in _pool_codes) or (_nm and _nm in _pool_names):
                        continue
                    _missed.append({"kind": _kind, "name": _nm, "code": _cd})
            review_stats["missed"] = _missed
    except Exception as _e:
        review_stats = {"available": False, "error": str(_e)}

    # 成交额门控（用户纪律 2026-08-13 → 2026-10-22 按板数分阶段）：低于该板数档位的
    # 成交额门槛的票不进池。须在板块梯队计算之前过滤，避免低流动性票污染梯队/龙一归属。
    # 📌 先留一份**过滤前全量**：低位连板龙雷达要在这里面挑「被门槛挡掉但股性很强」的票，
    #    否则雷达只剩本来就能进池的票，等于没补（2026-10-22）。
    _scored_all: list[dict[str, Any]] = list(scored)
    _scored_kept: list[dict[str, Any]] = []
    _excluded_turnover: list[dict[str, Any]] = []
    # 👑 高度层门槛豁免（2026-10-27）：先把「全市场最高板」定出来，再判每只票的门槛。
    #    ⛔ 顺序即病根——门槛若跑在「谁是高度第一」之前，龙头会被 5 亿门槛先杀掉，
    #      准入闸只能在残骸里挑前排（实证：上海九百 5 板 3.94 亿被剔 → 文投控股首板顶上）。
    _pool_boards_all = [_to_int(_s.get("lbc"), 1) for _s in (zt.get("ztgc") or [])]
    _market_max_boards = max(_pool_boards_all or [0])
    if not _market_max_boards:                      # 涨停池缺板数字段 → 退回评分全量
        _market_max_boards = max((_to_int(_s.get("boards"), 0) for _s in scored), default=0)
    _leader_exempt_rows: list[dict[str, Any]] = []
    _seal_exempt_rows: list[dict[str, Any]] = []
    # 同题材涨停家数（**选股宝题材口径**·与 scored 的 `theme` 字段同源）。
    # ⛔ 不许用 `sector`（东财行业）：传媒系被拆成 游戏Ⅱ/出版/广告营销/纺织制造/工程咨询
    #    六个行业，按行业算永远凑不到 8 家 → 热题材豁免会变成一行死代码。
    #    （2026-11-03 实证：短剧/互动影游 27 家同门，东财口径最大只到 5 家。）
    _theme_mates_cnt: dict[str, int] = {}
    if _seal_gate.enabled():
        for _it in scored:
            _t = str(_it.get("theme") or "").strip()
            if _t and _t != "其他":
                _theme_mates_cnt[_t] = _theme_mates_cnt.get(_t, 0) + 1
    for _it in scored:
        _floor_normal = turnover_floor(_it.get("boards"))
        _floor_leader = leader_turnover_floor(_it.get("boards"), _market_max_boards)
        _t = str(_it.get("theme") or "").strip()
        _mates = _theme_mates_cnt.get(_t) if (_t and _t != "其他") else None
        # 成交额准入 → core/seal_gate（2026-11-03）：成交额从「硬门槛」降级为四因素之一，
        # 强封（一字/竞价封+厚封单）与极热题材（≥8 家）可低量放行。
        # ⛔ 只决定「进不进候选池」，不改评分/排序/名额/仓位/09:25 裁决。
        _verdict = _seal_gate.admit(_it, _floor_normal, _floor_leader, theme_mates=_mates)
        _it["seal_gate"] = {
            "schema": _seal_gate.SCHEMA, "grade": _verdict["grade"],
            "score": _verdict["score"], "floor_used": _verdict["floor_used"],
            "theme_mates": _mates, "source": _verdict["source"],
            "reason": _verdict["reason"],
            # 封板形态（2026-11-04）：一字/换手 → 换手板 1~2 板门槛提到 5 亿。
            "style": _verdict.get("style"),
        }
        if _verdict["keep"]:
            _scored_kept.append(_it)
            if _verdict["exempt"]:
                _it["turnover_exempt"] = True
                _it["turnover_exempt_kind"] = _verdict["source"]
                _it["turnover_floor_used"] = _verdict["floor_used"]
                _it["turnover_floor_normal"] = _floor_normal
                # 按**来源标签**分流，⛔ 不再 reason text contains 猜类型
                if _verdict["source"] == "leader":
                    _it["leader_turnover_exempt"] = True
                    _leader_exempt_rows.append(_it)
                else:
                    _seal_exempt_rows.append(_it)
        else:
            _excluded_turnover.append(_it)

    # ══════════════════════════════════════════════════════════════════════
    # 🧭 全量过筛前置入口（2026-11-04 用户「可以处理前置入口！！这是我们优化的关键」）
    # ══════════════════════════════════════════════════════════════════════
    # 用户原话：「我们其实每天要把涨停的个股全部过一遍……基于你这个逻辑的话，
    #   这个"紫竹"根本就不会进入我们系统的范围里面，直接就 pass 掉了。」
    #   「今天哪怕它离开了，没有进入我们的池子，但是明天如果它还涨停了，
    #     我们就会对它进行进一步的观察和判断，总会被我们发现的。」
    #
    # 与既有闸的关系：⛔ **本闸只做减法，不加分、不改排序、不放宽任何门槛**。
    #   `seal_gate` 在上一步已按形态/封板强度决定过「成交额够不够」；本闸在此之上
    #   再问一句「这只票逐维过筛的结论是什么」——被 reject 的移出池并逐个留痕。
    #   ⛔ 之所以放在 `seal_gate` **之后**：这样能留下「seal_gate 放过了、过筛拦住了」
    #      的双层痕迹，才看得出本闸的增量价值（否则混在一起无法归因）。
    #
    # ⛔ 过筛是**每天重跑**的，不是黑名单：被拒的票明天若再涨停，会重新算一遍结论。
    #    连续观察由 `core.daily_screen` 的跨日追踪负责（见 `reject_followup`）。
    _screen_info: dict[str, Any] = {"enabled": False}
    if _daily_screen.enabled():
        try:
            _screen_res = _daily_screen.screen_pool(
                zt.get("ztgc") or [], trade_date=str(zt.get("date") or "")[:10], root=ROOT.parent)
            _by_code = {s["code"]: s for s in _screen_res.get("screened") or []}
            _keep_after, _excluded_screen = [], []
            for _it in _scored_kept:
                _sc = _by_code.get(str(_it.get("code") or ""))
                if _sc is None:
                    # ⛔ 涨停池里没有这只票（候选来自其它上游）→ 不裁，标 unknown
                    _it["daily_screen"] = {"verdict": "unknown", "rejects": [],
                                           "unknown": ["不在当日涨停池，未过筛"]}
                    _keep_after.append(_it)
                elif _sc["verdict"] == "reject":
                    _it["daily_screen"] = {
                        "verdict": "reject", "rejects": list(_sc["rejects"]),
                        "supports": list(_sc["supports"]), "unknown": list(_sc["unknown"]),
                    }
                    _it["screen_reject"] = list(_sc["rejects"])
                    _excluded_screen.append(_it)
                else:
                    _it["daily_screen"] = {
                        "verdict": _sc["verdict"], "rejects": [],
                        "watch_reasons": list(_sc["watch_reasons"]),
                        "supports": list(_sc["supports"]), "unknown": list(_sc["unknown"]),
                    }
                    # 🚫 2026-11-04：「只能看」≠「能买」。
                    #    原实现把 watch_only 与 pass 一起放行，于是「一字封死买不到」
                    #    「首板无带动」「独苗高位无板块支撑」这些票照样能进接力池占名额。
                    #    映射到既有 `observe_only` 身份（`two_pool` 单一源）：
                    #      · `pool_admit` 第 498 行 `not observation_only(r)` → 落观察池
                    #      · ⛔ 不设这个字段，独苗 4 板（line_rank 0 + boards≥2）会直接进 relay
                    #      · ⛔ 同时不伪造 `watch_reason`：原因已由 `watch_reasons` 保存
                    if _sc["verdict"] == "watch_only":
                        _it["participation_eligibility"] = "observe_only"
                        _it["watch_only"] = True
                        _it["buyable"] = False
                        _it["daily_screen_watch_reasons"] = list(_sc["watch_reasons"])
                    _keep_after.append(_it)
            _scored_kept = _keep_after
            _screen_info = {
                "enabled": True,
                "zt_count": _screen_res.get("zt_count"),
                "counts": _screen_res.get("counts"),
                "excluded": [{"code": _x.get("code"), "name": _x.get("name"),
                              "rejects": _x.get("screen_reject")} for _x in _excluded_screen],
            }
            # 📝 落盘逐票结论（2026-11-04 补）：此前只算不存 → 前端「今日决策」的
            #    漏斗永远读不到当日数据，只能显示手工产物。
            #    ⛔ 写失败不得打断选股（与上面同一条降级原则）。
            try:
                _screen_info["path"] = str(_daily_screen.persist(ROOT.parent, _screen_res))
            except Exception as _pe:
                _screen_info["persist_error"] = str(_pe)
        except Exception as _e:            # ⛔ 过筛失败不得打断选股（诚实降级）
            _screen_info = {"enabled": True, "error": str(_e)}
    # ⛔ 2026-11-04 修：`diagnostics` 到这里还没创建（它在下面 `select_relay_watch`
    #    的返回值里才出现）→ 这里只能暂存，落 diagnostics 的动作挪到 2071 行之后。
    #    原写法 `diagnostics["daily_screen"] = ...` 直接 UnboundLocalError，eod 整轮崩。
    scored = _scored_kept
    n_excluded_turnover = len(_excluded_turnover)
    if _seal_exempt_rows:
        _kind_lbl = {"strong": "强封", "thick_seal": "厚封单",
                     "theme_hot": "热题材", "leader": "高度层"}
        _kinds: dict[str, int] = {}
        for _r in _seal_exempt_rows:
            _k = str((_r.get("seal_gate") or {}).get("source") or "?")
            _kinds[_k] = _kinds.get(_k, 0) + 1
        _ktxt = "/".join(f"{_kind_lbl.get(k, k)}{v}只" for k, v in
                         sorted(_kinds.items(), key=lambda x: -x[1]))
        print(f"[EOD] ⚡ 封板强度放行：{len(_seal_exempt_rows)} 只低成交额票进池"
              f"（{_ktxt}）"
              f" → {'、'.join(str(_r.get('name') or '') for _r in _seal_exempt_rows[:8])}")
    if _leader_exempt_rows:
        print(f"[EOD] 👑 龙头门槛豁免：{len(_leader_exempt_rows)} 只高度层票按 "
              f"{MIN_TURNOVER_YI_LEADER:g} 亿放行（全市场最高 {_market_max_boards} 板）"
              f" → {'、'.join(str(_r.get('name') or '') for _r in _leader_exempt_rows)}")

    # P1（2026-10-18 用户「按顺序处理」）：**池外高辨识度名单**——被成交额门槛剔掉、
    # 但连板高度仍值得看的票（新华传媒 2.47亿 这种复盘会点名、选股却查无此票的）。
    # ⛔ 纯展示：不参与排序、不占名额、每项 buyable=False；成交额门槛纪律不变。
    offpool_high_ident: list[dict[str, Any]] = []
    if offpool_high_ident_enabled() and _excluded_turnover:
        offpool_high_ident = select_offpool_high_ident(
            _excluded_turnover,
            market_max_boards=_market_max_boards,
            market_max_count=sum(1 for _b in _pool_boards_all if _b == _market_max_boards),
        )

    # 科创板硬排除（用户无权限 2026-08-13 落铁律）：688 开头一律不进池。
    # 此前仅靠 20cm 惩罚压底，属隐患；创业板(300/301)开放后必须显式补齐。
    n_before_688 = len(scored)
    scored = [it for it in scored if not str(it.get("code", "")).startswith("688")]
    n_excluded_688 = n_before_688 - len(scored)

    # 反吃「短期情绪日报」板块 verdict（板块主线/回避判定，喂进 sector_sentiment/risk 维度）
    verdict = digest_daily_verdict(zt["date"], str(ROOT.parent))
    for it in scored:
        dv = match_candidate(it.get("name", ""), it.get("sector", ""), verdict)
        if dv:
            it["daily_verdict"] = dv

    # 板块梯队因子：按题材聚合计算梯队健康度，回写每只候选（核心驱动之一）
    # 🐉 同时用 `core.sector_leader`（单一源·分层判据）覆盖龙一归属：
    #    分组基准 = **完整涨停池 ztgc**（含被门槛剔掉的票，否则"龙一"会变成"池内最强"）。
    scored, _dragon_summary = _attach_sector_ladder(
        scored, zt_rows=zt.get("ztgc") or [], theme_map=_theme_map_all,
        asof=zt.get("date"))

    # 板块孤军标注（2026-08-24 用户「板块掉队只剩一人·次日高开无承接被砸」思想）：
    # 同 sector 涨停池成员数==1 → sector_is_lone=True + sector_mates=[]（供次日 09:25
    # verify 抓同板块竞价算「板块承接力」——孤军高开 + 板块 0 红开 → 买→观察降级）。
    # 纯标注不塞池不降排名：高标孤军仍是辨识度候选（情绪核心战法认可独立总龙头），
    # 真正裁决在次日竞价承接力（core/sector_support.py + signal_eval 门控）。
    # ⛔ 2026-08-25 修复：聚合基准 = 完整涨停池 zt["ztgc"]（含 ST/一字/688/低成交额全部
    # 涨停股），不是候选子集——旧实现（只传 scored）导致候选池内同行业仅 1 只即全员
    # 误判孤军（实证 08-24 贵金属 5+ 家涨停被标孤军，用户抓包「全打上孤军」）。
    # ⛔ 2026-08-25 二修：题材归纳/板块效应参考东财接口（latest.json top5 收盘快照·
    # 用户「题材这一块参考东财·实时+涨跌停池用必盈」）——em_sector_zt 校准题材层涨停数。
    # ⛔ 2026-08-25 三修：pool 已回填 core_theme → _theme_key 优先题材（钻石归钻石），
    # 不再被行业映射误导（黄河旋风 通用设备→机器人 错误桶）。
    from core.sector_support import annotate_lone, load_em_sector_zt
    scored = annotate_lone(scored, pool=zt.get("ztgc") or scored,
                           em_sector_zt=load_em_sector_zt())

    # 带动性时序标注（2026-08-25 用户「小周期载体」思路·#2·载体三标准「带动同属性/反推板块」）：
    # 按 fbt 排序统计「封板后 30min 内同题材新增涨停数」（carry_cnt/carry_score），
    # 供卡片「带动N家」标签 + relay_score 带动性加分 + AI 点评引用。纯标注不改排序硬门槛。
    from core.sector_support import annotate_carry
    scored = annotate_carry(scored, pool=zt.get("ztgc") or scored)

    # 板块联动事实层（2026-10-22 用户纪律）：事实与交易纪律分离。
    # 此处仅冻结完整涨停池结构、题材映射版本与可逆标签；不改分、不删候选。
    # 09:25 再用同题材竞价助攻验证，避免“昨天独苗”被误读成今天必然不可做。
    try:
        from core.theme_linkage import annotate_eod
        for _it in scored:
            _it["theme_mapping_fingerprint"] = _theme_meta.get("fingerprint")
        scored = annotate_eod(scored)
    except Exception as _e:  # noqa: BLE001
        print(f"[EOD] 板块联动事实层降级: {_e}")


    # P0-2 孤岛打通（2026-08-17）：盘中强势信号 → 盘后候选回标。
    # 情绪传递链：盘中未封板强势拉升(uplimit=false) + newsflash 点名股 → 次日候选。
    # 早鸟埋伏层 = 涨停前可买信号源（用户核心诉求），此处把 early 信号 code 集合
    # 回标到涨停池候选上（early_buy_signal=True），供卡片「盘中强势」标签与 AI 点评引用。
    # 不回标不改变排序（纯标记，不塞池不绕硬门槛）；diagnostics 记录命中数。
    early_map = _build_early_code_map(pulse.get("early") or [])
    early_hits = 0
    for it in scored:
        # ztgc 标准字段：dm=代码 mc=名称（非 code/name）
        it_code = str(it.get("dm") or it.get("code") or "")
        if it_code in early_map:
            it["early_buy_signal"] = True
            it["early_signal_type"] = early_map[it_code]
            early_hits += 1

    # 弱转强识别（2026-08-20 用户「神奇制药竞价从地板拉起·经典弱转强」）：
    # 断板回封 = 昨日未涨停 + 前日曾涨停（含连板史）→ 今日回封涨停。
    # 情绪核心战法：弱转强是情绪核心最经典的形成路径，识别后回标 w2s 标签，
    # 不改变排序硬门槛（纯标记），供卡片「弱转强」标签 + AI 点评 + 次日 verify 竞价验证。
    _today_d = datetime.now().strftime("%Y-%m-%d")
    w2s_map = _build_weak2strong_map(
        ZT_HIST_DIR, _today_d,
        "", zt.get("ztgc") or [])
    w2s_hits = 0
    for it in scored:
        it_code = str(it.get("dm") or it.get("code") or "")
        if it_code in w2s_map:
            it["weak2strong"] = True
            it["w2s_type"] = w2s_map[it_code]["w2s_type"]
            it["w2s_prev_boards"] = w2s_map[it_code]["prev_boards"]
            it["w2s_prev_zbc"] = w2s_map[it_code].get("prev_zbc", 0)
            w2s_hits += 1

    second_legs = _build_second_leg_map(ZT_HIST_DIR, zt["date"], _scored_all)
    stage_feedback = (env.get("relay_feedback") or {}).get("stages") or {}
    for it in scored:
        code = str(it.get("code") or "")
        boards = int(it.get("boards") or 0)
        second_leg = second_legs.get(code)
        # N+N 二波与普通 1进2/2进3 分开判断；身份仍由已有龙头证据决定。
        stage_key = ("n_plus_n" if second_leg else "one_to_two" if boards == 1
                     else "two_to_three" if boards == 2 else "high_relay")
        stage = stage_feedback.get(stage_key) if isinstance(stage_feedback, dict) else None
        it["relay_stage"] = stage_key
        it["relay_mood"] = (stage.get("mood") if isinstance(stage, dict) else None) or "insufficient"
        it["relay_mood_evidence"] = stage if isinstance(stage, dict) else None
        if second_leg:
            it["n_plus_n"] = second_leg

    # 历史连板晋级因子：必须在实际生效的 select_relay_watch 之前回填，避免只展示不参与排序。
    # 统计同一股票/同一高度的真实次日晋级后验，再按当前晋级率、连板晋级率、情绪分小幅校准。
    history_diag = annotate_historical_relay(
        scored, ZT_HIST_DIR, zt.get("date") or _today_d, env
    )

    # faithful §4.4 四档接力/观察选择（spec 十一维 raw_score 驱动 + 真实环境 + AI）
    from core import leader_cycle
    comparison = None
    if leader_cycle.ENABLE_PRESELECT_STATUS:
        comparison = leader_cycle.build_preselect_context(
            scored, zt.get("ztgc") or [],
            leader_cycle.load_history(ROOT.parent, zt["date"], before_only=True),
            asof=zt["date"], cutoff=datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            themes=_theme_map_all)
    # 🌊（2026-10-19 用户「学习上海 7.10 聚会心得」）：**指数形态 → 选股风格**。
    # 情绪分只回答「能不能做」，指数形态回答「用哪种方式做」（主升只做最强／分歧只做逆势）。
    # ⛔ 纯标记：不排序、不改 raw_score、不塞池、不改入池资格；权威=`core/market_style.py`+宪法 🌊 章。
    # 量能/趋势取原始 feedback_matrix（load_environment 只给规范化字段，缺 amount/trend）。
    # ⚠️ 整段兜底：风格层是**附加信息**，任何异常都不得连累候选产出（降级为中性 + 记 error）。
    try:
        from core.market_style import annotate_candidates, detect_style, no_trend_hint
        _style_fb = load_json((cache_dir or ROOT.parent / "cache") / "feedback_matrix.json") or {}
        if not isinstance(_style_fb, dict):
            _style_fb = {}
        _style_fb.setdefault("sentiment_score", env.get("sentiment_score"))
        market_style = detect_style((pulse or {}).get("index"), _style_fb)
        market_style["stats"] = annotate_candidates(scored, market_style)
        market_style["no_trend_hint"] = no_trend_hint(_style_fb)
    except Exception as _e:  # noqa: BLE001
        market_style = {
            "style": "neutral", "label": "没方向",
            "trading_rule": "挑着做：不硬贴风格，按原分数排序",
            "rule": "风格层降级（计算异常）", "error": str(_e),
            "ice_note": None, "no_trend_hint": None,
            "evidence": {}, "data_gap": ["exception"], "stats": {},
        }
        print(f"[EOD] 指数风格层降级: {_e}")

    from core import two_pool
    if two_pool.enabled():
        # 复用既有同梯次×封板条件接口；历史股性不冒充条件晋级预测。
        from scripts import board_track
        _events = board_track.build_events([d for d in board_track.trade_dates() if d <= zt["date"]])
        for _r in scored:
            _factor_row = {**_r, "seal": _r.get("seal_amt")}
            if any(_factor_row.get(k) is None for k in ("fbt", "seal", "hs", "zbc")):
                _r["conditional_promotion"] = {"quality": "missing", "rate": None,
                                               "asof": zt["date"], "source": "scripts.board_track.cond_rate"}
                continue
            _r["conditional_promotion"] = {
                **board_track.cond_rate(int(_r.get("boards") or 1),
                                       board_track.factor_hits(_factor_row)["count"], zt["date"], _events),
                "asof": zt["date"], "source": "scripts.board_track.cond_rate",
                "window": "归档历史、事件完成日不晚于asof", "usage": "同地位排序证据，非个股保证概率",
            }

    # 🔁 昨日在池且今日晋级 → 观察池优先占位（2026-11-04 用户「b 占位」）：
    #    读**上一交易日**候选（relay+watch）→ `{code: board}`。
    #    ⛔ 读不到（首日/断档/文件损坏）就传空 dict，退回旧行为，不编造。
    _prev_pool_boards: dict[str, int] = {}
    try:
        _prev_pool_boards = _load_prev_pool_boards(zt["date"], cache_dir)
    except Exception as _e:  # noqa: BLE001
        print(f"[EOD] 昨日池读取失败（占位功能跳过）: {_e}")

    # 旧第三槽仅供回退模式。
    new_direction_first_board: list[dict[str, Any]] = []
    relay, watch, diagnostics, sel_mode = select_relay_watch(
        scored, env, pulse, ai_env, comparison=comparison,
        first_board_out=new_direction_first_board,
        prev_pool_boards=_prev_pool_boards, apply_pool_policy=False)
    if _prev_pool_boards:
        _carry = [r for r in watch if r.get("carryover_promoted")]
        diagnostics["watch_carryover"] = {
            "prev_pool_size": len(_prev_pool_boards),
            "promoted": [{"code": r.get("code"), "name": r.get("name"), "boards": r.get("boards")}
                         for r in _carry],
        }
        if _carry:
            print("[EOD] 🔁 昨日在池今日晋级·优先占位：" +
                  "、".join(f"{r.get('name')}({r.get('boards')}板)" for r in _carry))
    diagnostics["historical_relay"] = history_diag
    # 🧭 全量过筛留痕（2026-11-04）：`_screen_info` 在上游筛票时已算好，此处才拿到 diagnostics。
    diagnostics["daily_screen"] = _screen_info

    # 🚪 源头准入闸（2026-10-21 用户「后排的票不要推到这里面来了」·`core/pool_admit`）：
    #    「我们接力的票必须是龙头票，龙头票才能做接力，趋势票做容量观察用，
    #      前排的须是龙头前排」→ **按炒作题材分组，每组只留最强 N 只**（默认 2 只活口）。
    #    ⛔ 位置极其关键：必须在 `select_relay_watch` **之后、任何回填/展示之前** ——
    #      这里已经是「谁进池」的最终出口；放到 9:25 去拦只能把票标成观察，
    #      票还在池里就还在卡上（用户三次要求瘦身都没根治，根就在这）。
    #    ⛔ 只动 relay/watch 两个桶；`new_direction_first_board` / `offpool_*` 一律不碰。
    _relay_before, _watch_before = len(relay), len(watch)
    relay, watch, _admit_diag = pool_admit.admit(relay, watch)
    diagnostics["pool_admit"] = _admit_diag
    if _admit_diag.get("enabled"):
        print(f"[EOD] 🚪 准入闸：{_relay_before}+{_watch_before}={_relay_before + _watch_before} 只"
              f" → {len(relay)}+{len(watch)}={len(relay) + len(watch)} 只"
              f"（每条线留 {_admit_diag.get('per_line')} 只·挡掉 {_admit_diag.get('dropped')} 只）")
        if _admit_diag.get("dropped_names"):
            print(f"[EOD]    挡掉：{'、'.join(_admit_diag['dropped_names'])}")

    # 🔍 池外龙头校验（2026-10-21 用户「可以加一层池外龙头校验」·`core/pool_admit`）：
    #    准入闸只能在**池内**比强弱；若某条线真正的龙头没进池（分数不够/被别的闸挡），
    #    "池内第一"也可能只是后排 —— 这一层就是拿**当日涨停池**回头核一遍。
    #    ⛔ 日期铁律：`zt["ztgc"]` 与 `zt["date"]` 就是本次候选的同一交易日，
    #      天然同源；⛔ 不要另读别的日期的池子。
    #    ⛔ 默认**只标记不砍**（`POOL_ADMIT_OUTPOOL_DROP=0`）：用户要「我来看看对不对」，
    #      先让判断露在 `diagnostics.outpool_check` + 卡面，确认无误再决定是否落刀。
    _outpool = pool_admit.outpool_dragon_check(relay + watch, zt.get("ztgc") or [])
    diagnostics["outpool_check"] = _outpool
    for _r in relay + watch:
        _v = (_outpool.get("rows") or {}).get(str(_r.get("code") or "")) or {}
        if _v:
            _r["line_tier_outpool"] = _v.get("tier")
            _r["line_tier_why"] = _v.get("why")
            _r["line_top"] = _v.get("line_top")
            _r["line_tops"] = _v.get("line_tops")
    if _outpool.get("enabled"):
        _s = _outpool.get("summary") or {}
        print(f"[EOD] 🔍 池外龙头校验（配置 {zt.get('date')} 涨停池 {_outpool.get('zt_rows')} 只）："
              f"前排 {_s.get('front', 0)} · 次排 {_s.get('second', 0)} · "
              f"后排 {_s.get('back', 0)} · 独苗 {_s.get('lone', 0)} · 不明 {_s.get('unknown', 0)}")
        for _c in (_outpool.get("back") or []):
            _v = _outpool["rows"][_c]
            print(f"[EOD]    ⛔ 后排：{_v.get('theme')} → {_v.get('why')}")
        if pool_admit.outpool_drop_enabled() and _outpool.get("back"):
            _drop = set(_outpool["back"])
            relay = [r for r in relay if str(r.get("code")) not in _drop]
            watch = [r for r in watch if str(r.get("code")) not in _drop]
            diagnostics["outpool_check"]["dropped"] = sorted(_drop)
            print(f"[EOD]    🗑 已砍后排 {len(_drop)} 只（POOL_ADMIT_OUTPOOL_DROP=1）")

    # 🧊 最高板 + 极薄封单 → 只当观察（2026-10-27 用户「只观察，行情差没有必要强行接力」）：
    #    放在准入闸/池外校验**之后**：先定「谁有资格进池」，再判「进来的这只该不该给买单资格」。
    #    ⛔ 只降级、不删除 —— 票还在卡面上（用户能看见「最高板但封单太薄」这个事实）。
    relay, watch, _thin_seal_moved, _market_max_boards = demote_thin_seal_top(
        relay, watch, zt.get("ztgc") or [])
    diagnostics["thin_seal_top"] = {
        "enabled": thin_seal_top_enabled(),
        "market_max_boards": _market_max_boards,
        "ratio_max_pct": THIN_SEAL_TOP_RATIO_MAX,
        "moved": [{"code": str(r.get("code") or ""), "name": str(r.get("name") or ""),
                   "boards": _to_int(r.get("boards"), 1),
                   "seal_ratio_pct": r.get("seal_ratio_pct")} for r in _thin_seal_moved],
    }
    if _thin_seal_moved:
        _ts_desc = "、".join(
            "{}({}板/封单{}%)".format(r.get("name"), r.get("boards"), r.get("seal_ratio_pct"))
            for r in _thin_seal_moved)
        print(f"[EOD] 🧊 最高板封单过薄 → 降为观察（只看不买·不占名额）：{_ts_desc}")

    # 🐉 板块龙一准入闸（2026-10-27 用户「推的票必须是板块龙一」）：
    #    「接力的票必须是龙头票」的前一层 —— 先认准谁是这条线的龙一（`core/sector_leader`），
    #    再要求**接力池里只放龙一**：龙二/跟风一律降观察、不给买单资格。
    #    ⛔ 位置必须在 `thin_seal_top` 之后：龙一若因「最高板+极薄封单」被降级，
    #      同线副手必须跟着不可买（不拿跟风顶替）——这正是用户「行情差没必要强行接力」。
    #    ⛔ 无板块（题材缺失/独涨停）不受本闸约束，沿用孤军/高标逻辑。
    _relay_before_dragon = len(relay)
    relay, watch, _dragon_gate_diag = apply_dragon_gate(relay, watch)
    diagnostics["dragon_gate"] = _dragon_gate_diag
    if _dragon_gate_diag.get("enabled"):
        print(f"[EOD] 🐉 龙一闸：接力 {_relay_before_dragon} 只 → {len(relay)} 只"
              f"（降观察 {len(_dragon_gate_diag.get('moved') or [])} 只）")
        for _m in (_dragon_gate_diag.get("moved") or []):
            print(f"[EOD]    ↓ {_m.get('name')}（{_m.get('theme') or '无题材'}·"
                  f"第{_m.get('dragon_rank')}身位·龙一={_m.get('leader') or '—'}）")
    diagnostics["sector_dragon"] = _dragon_summary

    # 🎯 四条件合取硬闸（2026-10-27 用户「一定要题材龙+情绪OK+量能+梯队…没有就空！！」）
    #    ⛔ 位置必须在**所有降级闸之后**（pool_admit → thin_seal_top → 龙一闸 → 本闸）：
    #      前面各闸解决「这只票本身行不行」，本闸解决「今天这条线+这个环境该不该推」。
    #    ⛔ 接力池**允许为空** —— 这是用户要的效果，不是异常；卡面已有「今日无接力候选」分支。
    _relay_before_hw = len(relay)
    # 高度层豁免需要「全市场最高板」：thin_seal_top 关闭时它不回传（返回 0），
    # 此处独立兜底算一次，⛔ 不让开关状态影响量能豁免口径。
    _hw_market_max = _market_max_boards or max(
        [_to_int((_s or {}).get("lbc") or (_s or {}).get("boards"), 1)
         for _s in (zt.get("ztgc") or [])] or [0]
    )
    relay, watch, _hw_diag = apply_high_win_gate(
        relay, watch, env, market_max_boards=_hw_market_max)
    diagnostics["high_win_gate"] = _hw_diag
    if _hw_diag.get("enabled"):
        _hw_missing_text = "、".join(_hw_diag.get("conditions") or [])
        print(f"[EOD] 🎯 四条件闸（{_hw_missing_text}）：接力 {_relay_before_hw} 只 → {len(relay)} 只"
              f"（regime={_hw_diag.get('regime') or '—'}）")
        for _m in (_hw_diag.get("moved") or []):
            print(f"[EOD]    ↓ {_m.get('name')}（缺少：{'/'.join(_m.get('missing') or [])}）")
        if not relay:
            if _relay_before_hw == 0:
                print("[EOD] 接力池为空 —— 前置链路已无接力，本闸未移出候选")
            elif _hw_diag.get("moved"):
                print("[EOD] 接力池为空 —— 四条件未同时成立，今日不推（用户纪律「没有就空」）")

    # 🎯 低位连板龙雷达（2026-10-22 用户「需要做，雷达叠加观察的意思」）：
    #    「大业股份这种票，第一个板或第二个板就必须发现」。首板/2 板常只有 1~3 亿成交额，
    #    会被进场门槛挡在池外 —— 本名单把「低位 + 股性连板龙 + 还有量」的票单独列出来，
    #    ⛔ 只展示、不占 relay/watch 名额、不进 09:25 池内裁决（`buyable=False`）。
    #    ⛔ 必须在 `pool_admit` 定稿之后算：排除集要用最终池子，否则会把刚被闸挡掉的票
    #      又当成"没进池"重复列一遍（同一条线的后排已经在池外校验里说过一次了）。
    gene_radar: list[dict[str, Any]] = []
    low_pool: list[dict[str, Any]] = []
    if gene_radar_enabled() and not two_pool.enabled():
        try:
            _rs, _rd = _defense_gate.load_zt_series(upto=str(zt.get("date") or ""))
        except Exception as _e:      # 数据缺料 → 位置判据降级为「不裁决」，不硬猜
            _rs, _rd = {}, []
            print(f"[EOD] 🎯 雷达位置判据缺料（{_e}），本批不做位置过滤")
        gene_radar = select_gene_radar(
            _scored_all,
            in_pool_codes={str(r.get("code") or "") for r in (relay + watch)},
            excluded_turnover_codes={str(r.get("code") or "") for r in _excluded_turnover},
            series=_rs, trading_days=_rd,
        )
        if gene_radar:
            print(f"[EOD] 🎯 低位连板龙雷达：{len(gene_radar)} 只（只看不买）"
                  f" → {'、'.join(x.get('name', '') for x in gene_radar)}")

    # 🐣 低位池（2026-10-27 用户「还不如推低位！」· C 档）：
    #    把上面雷达里**股性最强**的前几只从「只看不买」升级为**真可买**，进 09:25 池内裁决。
    #    理由：接力池全被高标占满时（今天最高只剩 4 板、还都是成交额勉强够线的），
    #    用户更愿意在「低位 + 连板龙基因 + 位置不高」的票上试仓，而不是接中位股。
    #    ⛔ 三条护栏不变：①1~2 板 ②严格股性「连板龙」③位置不高（高位二波已在上游剔除）。
    #    ⛔ 与 relay/watch **共享当日名额**（不另开独立名额），否则等于偷偷放大仓位。
    #    ⛔ 排序仍按板数 → 低池天然排在接力池之后：它是「名额空出来时的入口」，
    #      不抢前排；若前排全被防守闸拦掉，它才轮到（这正是想要的行为）。
    if low_pool_enabled() and gene_radar:
        _clean = [r for r in gene_radar if low_pool_quality_ok(r)]
        _dirty = [r for r in gene_radar if not low_pool_quality_ok(r)]
        low_pool = [
            {**r, "buyable": True, "bucket": "low_pool", "bucket_label": "低位池",
             "low_rank": i}
            for i, r in enumerate(_clean[:LOW_POOL_TOPN], start=1)
        ]
        if _dirty:
            print(f"[EOD] 🐣 封板质量不够、不进低池："
                  f"{'、'.join(str(x.get('name') or '') for x in _dirty)}")
        print(f"[EOD] 🐣 低位池（可买·与接力池共享名额）：{len(low_pool)} 只"
              f" → {'、'.join(x.get('name', '') for x in low_pool) or '无'}")

    if two_pool.enabled():
        # 后置风控只降不升；被拒绝者不得因身份重新进入接力。
        relay, watch, _two_diag = two_pool.finalize(relay, watch, stage="post_risk")
        # 一字核心从完整评分池补观察，复用同日完整池龙一判定；不绕入可交易池。
        _anchors, _ = _attach_sector_ladder(
            [dict(r) for r in _scored_all if r.get("is_one_word")],
            zt_rows=zt.get("ztgc") or [], theme_map=_theme_map_all,
            asof=zt.get("date"))
        _used = {str(r.get("code")) for r in relay + watch}
        _one_word_kinds = {"一字核心", "一字龙头"}
        for _r in _anchors:
            _kind = two_pool.watch_kind(_r)
            # ⛔ 2026-11-04：原来只认 `一字核心`（厚封单）。一字板封单数据缺失时
            #    `watch_kind` 返回「一字龙头」，这里不跟着收就会**依旧一个都进不来**。
            if _kind in _one_word_kinds and str(_r.get("code")) not in _used:
                watch.append(two_pool.mark_watch(_r, _kind))
                _used.add(str(_r.get("code")))
        # 🎯 2 板以上「过筛未过」的票补进观察池（2026-11-04 用户）。
        #    ⛔ 只写 watch、`buyable=False`、分组「过筛未过·只观察」；
        #      **绝不进 relay**（过筛 reject 已否认它的接力资格）。
        #    ⛔ 已在池的不重复加（`_used` 去重）。
        # ⛔ 判据用**全量 − 已进池**的差集，而不是只看过筛 reject：
        #    2 板以上的票可能在更上游就被挡住（成交额门槛 `_excluded_turnover` 26 只、
        #    过筛 reject、准入闸后排…）。只盯某一条路径会漏
        #    （实证 11-04：鲁阳节能/宝明科技/永臻股份 3 只被成交额门槛挡在过筛之前）。
        _kept_codes = {str(x.get("code") or "") for x in _scored_kept}
        _mr_candidates = [
            x for x in _scored_all
            if str(x.get("code") or "") not in _kept_codes
            and _to_int(x.get("boards") or x.get("lbc"), 0) >= 2
            and not str(x.get("code") or "").startswith("688")
            and "ST" not in str(x.get("name") or "").upper()
        ]
        _mr_added: list[str] = []
        for _r in _mr_candidates:
            _c = str(_r.get("code") or "")
            if not _c or _c in _used:
                continue
            _row = dict(_r)
            _row.update(
                bucket="watch", bucket_label="观察池",
                participation_eligibility="observe_only",
                watch_only=True, buyable=False,
                watch_group="过筛未过·只观察",
                watch_reason=("没通过接力资格（" + "；".join(
                    [str(x) for x in (_r.get("screen_reject") or [])]
                    or ["成交额/题材门槛未过"]) + "）"
                    "—— 但它已是 " + str(_to_int(_r.get("boards"), 1)) + " 板，位置要看，不参与"),
            )
            watch.append(_row)
            _used.add(_c)
            _mr_added.append(str(_r.get("name") or _c))
        if _mr_added:
            print(f"[EOD] 👀 未进接力但仍入观察（2板以上·只看不买）：{'、'.join(_mr_added)}")

        # 历史修复与交易准入分离：硬伤只收观察；通过既有个股门才给条件资格。
        _repair_diag = []
        if os.environ.get("ENABLE_HISTORICAL_REPAIR", "1") != "0":
            _final_by_code = {str(r.get("code")): r for r in relay + watch}
            for _src in _scored_all:
                _code = str(_src.get("code") or "")
                _ev = second_legs.get(_code)
                if not _ev or _code.startswith("688") or "ST" in str(_src.get("name") or "").upper():
                    continue
                _existing = _final_by_code.get(_code)
                _row = _existing if _existing is not None else dict(_src)
                _row["n_plus_n"] = _ev
                _miss = high_win_missing(_row, env, _hw_market_max)
                _blocks = [x for x in _miss if x != "情绪"]
                if _code not in _kept_codes or _row.get("screen_reject"):
                    _blocks.append("前置准入")
                if _row.get("dragon_rank") != 1 or not _row.get("is_sector_dragon"):
                    _blocks.append("题材龙")
                if (_row.get("is_one_word") or _row.get("watch_only")
                        or two_pool.watch_kind(_row) or _row.get("dragon_gate")
                        or _code in set(_outpool.get("back") or [])):
                    _blocks.append("既有风控/观察身份")
                _conditional = not _blocks
                _row["historical_repair"] = {"evidence": _ev, "blocked_by": sorted(set(_blocks)),
                                              "eligibility": "conditional" if _conditional else "observe_only"}
                if _existing is None or _conditional:
                    _row.update(bucket="watch", bucket_label="观察池", watch_group="历史连板修复",
                                participation_eligibility="conditional" if _conditional else "observe_only",
                                buyable=_conditional, watch_only=not _conditional,
                                watch_reason="逐日历史连板后断板再涨停；非价量N形态确认")
                    if _conditional:
                        _row["high_win_gate"] = _miss
                        if _miss == ["情绪"]:
                            _row.update(frozen=True, unfreeze_on="market_open_ready")
                    if _existing is None:
                        watch.append(_row)
                _repair_diag.append({"code": _code, "name": _row.get("name"),
                                     **_row["historical_repair"]})
        diagnostics["historical_repair"] = _repair_diag
        for _i, _r in enumerate(watch, 1):
            _r["watch_rank"] = _i
        diagnostics["two_pool_final"] = _two_diag
        diagnostics["screen_reject_multi_added"] = _mr_added
        diagnostics.update(relay_count=len(relay), watch_count=len(watch))
        offpool_high_ident = []
        low_pool = []
        new_direction_first_board = []

    # ══════════════════════════════════════════════════════════════════════
    # 🌱 新方向首板槽（2026-11-11 用户「这条线必须打开·优秀的票明早能交易」）
    # ══════════════════════════════════════════════════════════════════════
    # 用户原话：「新方向首板观察槽这个也要的啊！像今天这种非常优秀的、晋级率比较高的票，
    #   也都要拿出来放在我们的观察池里面，明天早上可以进入交易的。」
    #
    # 为什么必须新写一块：旧槽判据是 `题材家数 ≤ 3`，光通信有 10 家 → 家数越多越进不来，
    #   与「这条线强不强」恰好相反；且旧槽被 `not two_pool.enabled()` 硬关，生产环境
    #   （ENABLE_TWO_POOL_CONTRACT=1）里**一次都没跑过**。11-11 实证：光通信 10 家全首板、
    #   成交额 222.6 亿（全市场第二），接力池只漏进 1 只；PCB板 5 家全首板、255.8 亿，
    #   一只都没进池。
    #
    # 判据见 `core/new_direction_first_board.py` 文件头（「新方向」= **整条线零连板**，
    #   不是「家数少」）。⛔ 位置必须在所有降级闸与 finalize 之后、`_merge_pools` 之前：
    #   · 之前 —— 它就绕过了后排过滤/龙一闸/四条件闸（那几闸是给接力票设计的）；
    #   · `_merge_pools` 之前 —— 只有并进 watch 才能被资格线校验并进入最终池，
    #     09:25 才真的会拿它去裁决（「可交易」= 能进裁决，不是保证买）。
    try:
        from core import new_direction_first_board as _ndfb
        from core.quant_score import drop_backrow_rank as _dbr
        _nd_rows, _nd_diag = _ndfb.build(
            _scored_all, zt.get("ztgc") or [],
            exclude_codes={str(r.get("code") or "") for r in (relay + watch)},
            # 质量单一源 = 当日过筛（股性/题材线/封板形态）；槽只补「线是新方向 + 早盘封板」
            screened_codes={str(r.get("code") or "") for r in scored if r.get("code")},
            drop_backrow_rank=_dbr(),
        )
        diagnostics["new_direction"] = _nd_diag
        if _nd_rows:
            watch.extend(_nd_rows)
            new_direction_first_board = _nd_rows
            _by_line: dict[str, list[str]] = {}
            for _r in _nd_rows:
                _by_line.setdefault(str(_r.get("new_direction_line") or ""), []).append(
                    str(_r.get("name") or ""))
            print("[EOD] 🌱 新方向首板入池（整条线零连板·可进 09:25 裁决）：" +
                  "；".join(f"{_l} {len(_n)} 只（{'、'.join(_n)}）"
                            for _l, _n in _by_line.items()))
        else:
            new_direction_first_board = []
        for _l in (_nd_diag.get("lines") or []):
            print(f"[EOD] 🌱 新方向线：{_l['line']} {_l['members']} 家全首板 · "
                  f"{_l['amount_yi']:.0f} 亿 · 最早 {_l['first_seal']}")
        if _nd_diag.get("skipped"):
            print("[EOD] 🌱 线内未入池：" + "；".join(
                f"{x['name']}（{x['reason']}）" for x in _nd_diag["skipped"][:8]))
    except Exception as _e:  # noqa: BLE001
        print(f"[EOD] 🌱 新方向首板槽降级（不影响主链）：{_e}")
        new_direction_first_board = []

    # ══════════════════════════════════════════════════════════════════════
    # 👀 首板观察（2026-11-11 用户「首板可以进观察池；尽量还是从 2 板开始看」）
    # ══════════════════════════════════════════════════════════════════════
    # 用户原话：「没问题，首板不解决，首板可以进观察池。尽量还是从 2 版开始看，因为 1 版看不太懂。」
    #
    # 两件事（都只动观察身份，⛔ 不碰任何买入判据）：
    #   ① 普通首板（非新方向槽）→ 一律 observe_only：**能看、不能买**；
    #   ② 首板里「封得早（≤09:30）且零炸板」但没过既有资格的 → 补进观察池（最多 6 只）。
    # ⛔ 2 板及以上不受影响。回退：`POOL_FIRST_BOARD_OBSERVE=0`。
    if os.environ.get("POOL_FIRST_BOARD_OBSERVE", "1") != "0":
        _in_pool = {str(r.get("code") or "") for r in (relay + watch)}
        _fb_obs: list[dict[str, Any]] = []
        for _it in (_scored_all or []):
            _c = str(_it.get("code") or "")
            if not _c or _c in _in_pool:
                continue
            try:
                _b = int(_it.get("boards"))
            except (TypeError, ValueError):
                continue
            if _b != 1 or _it.get("is_one_word") is True:
                continue
            if _c.startswith("688") or "ST" in str(_it.get("name") or "").upper():
                continue
            # 🚫 缺题材归属不配进观察池（2026-11-12 用户「三钢闽光连观察池都不配」）
            _ct = str(_it.get("core_theme") or "").strip().lower()
            if _ct in {"", "-", "—", "其他", "无", "未知", "none", "null"}:
                continue
            try:
                _g = json.loads((ROOT / "cache" / "zt_genes"
                                 / f"gene_{_c}.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                _g = None
            _why = first_board_observe_reason(_it, _g)
            if not _why:
                continue
            _fb_obs.append({**_it, "_observe_why": _why})
        def _gene_rank(r: dict) -> tuple:
            try:
                g = json.loads((ROOT / "cache" / "zt_genes"
                                / f"gene_{r.get('code')}.json").read_text(encoding="utf-8"))
                return (-int(g.get("zt_n") or 0), str(r.get("fbt") or "999999"))
            except Exception:  # noqa: BLE001
                return (0, str(r.get("fbt") or "999999"))

        _fb_cap = int(os.environ.get("POOL_FIRST_BOARD_OBSERVE_MAX", "8") or 8)
        _fb_obs.sort(key=_gene_rank)
        for _it in _fb_obs[:_fb_cap]:
            _row = dict(_it)
            _row.update(bucket="watch", bucket_label="首板观察",
                        participation_eligibility="observe_only", buyable=False,
                        watch_only=True, watch_group="首板观察",
                        watch_reason=(f"{_it.get('_observe_why')}；"
                                      "按用户口径「首板只看不买」→ 放观察池，明早竞价再判"))
            watch.append(_row)
        if _fb_obs[:_fb_cap]:
            print("[EOD] 👀 首板观察入池：" + "、".join(
                str(r.get("name")) for r in _fb_obs[:_fb_cap]))
        # 普通首板（非新方向槽）一律降为观察
        for _r in (relay + watch):
            if (str(_r.get("pool_source_bucket") or "") == "new_direction"
                    or str(_r.get("bucket") or "") == "new_direction"
                    or _r.get("new_direction_line")):
                continue
            try:
                _b = int(_r.get("boards"))
            except (TypeError, ValueError):
                continue
            if _b != 1:
                continue
            _r.update(participation_eligibility="observe_only", buyable=False,
                      watch_only=True, watch_group=_r.get("watch_group") or "首板观察",
                      watch_reason=_r.get("watch_reason")
                      or "首板只看不买（用户 2026-11-11）")

    # 所有降级与补票完成后才迁移展示身份；不把 observe_only 改成 relay_candidate。
    # ⚠️ 位置必须在「准入闸 → 各降级闸 → 历史修复/补票 → two_pool.finalize」之后，
    #    否则后补的票会绕过「龙5及以后不进池」；仍早于所有纯回填（不动交易资格）。
    from core.quant_score import _merge_pools, drop_backrow_rank, single_pool_enabled, pool_policy_diag
    if drop_backrow_rank() > 0 or single_pool_enabled():
        diagnostics["pre_single_pool"] = {"relay": len(relay), "watch": len(watch)}
        relay, watch, _dropped, _height = _merge_pools(
            relay, watch, market_rows=zt.get("ztgc") or [])
        diagnostics.update(pool_policy_diag(_dropped, _height),
                           relay_count=len(relay), watch_count=len(watch))
        _unq = diagnostics.get("unqualified_dropped") or []
        if _unq:
            print(f"[EOD] 🚮 未获接力资格·不进池 {len(_unq)} 只"
                  f"（接力池剩 {len(relay)} 只）："
                  + "、".join(str(x.get("name")) for x in _unq[:12])
                  + ("…" if len(_unq) > 12 else ""))

    # ══════════════════════════════════════════════════════════════════════
    # 🥈 第二波高位节点（2026-11-12 用户「最近一个月第一波走完了，第二波三板危险」）
    # ══════════════════════════════════════════════════════════════════════
    # 前移到建池层：卡面与 09:25 裁决必须是同一个身份，⛔ 不允许「卡面可买、裁决只观察」。
    # 判据见 `core/second_wave.py`；⛔ 只改身份/展示，不改任何买入判据本身。
    try:
        from core import second_wave as _sw
        _sw_hit = []
        for _r in (relay + watch):
            _hit = _sw.second_wave(_r.get("boards"), str(_r.get("code") or ""))
            if not _hit:
                continue
            _r.update(participation_eligibility="observe_only", buyable=False,
                      watch_only=True, watch_group="第二波节点",
                      watch_reason=_hit["note"],
                      second_wave_node=True, prior_wave_zt=_hit["prior"])
            _sw_hit.append(str(_r.get("name") or ""))
        if _sw_hit:
            print("[EOD] 🥈 第二波高位节点·只观察：" + "、".join(_sw_hit))
    except Exception as _e:  # noqa: BLE001
        print(f"[EOD] 🥈 第二波节点判定降级（不影响主链）：{_e}")

    # 🏛 板块核心观察（2026-11-13 用户「题材与情绪核心那张卡选得准，每天并进池子」）
    #    ⛔ 只观察不可买：核心票是「这条线的旗手」，用来对照板块异动真伪，不是买点。
    try:
        from core import board_core_watch as _bc
        # 先重建核心票榜（0.3s·只读 zt_history），保证用当日数据
        try:
            import subprocess as _sp
            _sp.run([sys.executable, str(ROOT.parent / "scripts" / "board_core.py"),
                     "build", "--days", "20"], check=False, timeout=90,
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception:  # noqa: BLE001
            pass
        _exist = {str(r.get("code")) for r in (relay + watch)}
        _cores = _bc.picks(_exist)
        # ⛔ 2026-11-16 修数据错乱：核心观察票的 `boards` 是**上次涨停时的高度**，
        #    不等于今天还在板上（实证 603366 日出东方：11-12 五板后断板，11-16 仍以
        #    boards=5 混进观察池，页面像「当前 5 板高标」，pct/fbt/seal 全是空）。
        #    这里按「今天是否在涨停池」重写高度：不在 → boards=0 + 明确标注上次涨停日。
        _today_boards = {str(r.get("dm") or r.get("code")): int(r.get("boards") or 0)
                         for r in (zt.get("ztgc") or [])}
        for _c in _cores:
            _tday = str(_c.get("last_zt_date") or "")
            _b_today = _today_boards.get(str(_c["code"]), 0)
            # ⛔ 2026-11-16 用户：「断板的核心票显示在这里没有意义」。
            #    只保留**今天仍在涨停**的核心票，且身份改成正常候选（可参与 09:25 判定）——
            #    不再是"只看不买"，因为所有票都要给买入预期判断。
            if not _b_today:
                continue
            watch.append({
                "code": _c["code"], "name": _c["name"], "boards": _b_today,
                "boards_note": "",
                "is_today_zt": True,
                "core_theme": _c.get("sector"), "sector": _c.get("sector"),
                "participation_eligibility": "relay_candidate", "buyable": True,
                "watch_only": False, "watch_group": "板块核心观察",
                "watch_reason": (f"这条线的核心（{_c.get('sector')}·core {_c.get('core_score')}"
                                 f"·参与{_c.get('join_rate')}%）：只看它动不动，不买"),
                "board_core_score": _c.get("core_score"),
                "board_core_join": _c.get("join_rate"),
            })
        if _cores:
            print("[EOD] 🏛 板块核心观察：" + "、".join(
                f"{x['name']}({x.get('sector')}·{x.get('core_score')})" for x in _cores))
    except Exception as _e:  # noqa: BLE001
        print(f"[EOD] 🏛 板块核心观察降级（不影响主链）：{_e}")

    # 🧭 市场级题材强度（2026-11-11 用户拍板 B）：回答「昨天整个市场哪几条线真强」。
    #    ⛔ 只写 diagnostics + 展示字段 theme_rank/theme_metrics；不改 relay_rank、
    #       dragon_rank、relay 数组顺序、任何准入闸 / DEV / 量能 / quota / 仓位 P0。
    #    ⛔ 基准必须是完整同日涨停池 ztgc，不能拿过滤后的候选残池冒充全市场。
    #    ⛔ 可逆：POOL_CALL_MARKET_THEME_RANK=0 → 不算、不写、卡面整段不渲染。
    try:
        from core import market_theme_rank as _mtr
        _mtr.attach_to_payload(diagnostics, relay, watch,
                               zt.get("ztgc") or [],
                               locals().get("_theme_map_all") or {})
    except Exception as _e:  # noqa: BLE001
        print(f"[EOD] 市场级题材强度降级（不影响候选）: {_e}")

    # early 回标统计（diagnostics 来自 select_relay_watch 返回值，须在其后写入）
    diagnostics["early_buy_hits"] = early_hits
    diagnostics["early_buy_signals"] = [
        {"code": c, "type": t} for c, t in list(early_map.items())[:10]
    ]
    diagnostics["weak2strong_hits"] = w2s_hits
    diagnostics["weak2strong_codes"] = list(w2s_map.keys())[:10]

    # 每只候选回填：八维面板 + AI 期望 + 旁白 + 核心炒作题材（2026-08-17 用户）
    theme_ctx = _load_theme_ctx()
    # 核心炒作题材识别（2026-08-17 用户「识别市场真正炒作的核心题材·非静态行业」）：
    # surge_stock/stocks 接口对涨停股返回 description(题材逻辑)+plates(概念板块)，
    # 而 ztgc 只有静态行业(hy)。抓取后为每只候选回填 industry_sector(行业归属) +
    # core_theme(当前炒作题材) + core_theme_logic(逻辑) + core_theme_heat(热度H/M/L)。
    # 盘后接口关闭/抓取失败 → 降级空标注（不编造）；题材热度按所在板块涨停数评级。
    # ⚠️ 2026-09-18 修：`_sector_zt` 原**赋值在 try 内**——一旦题材抓取失败
    # （网络异常 / `common.stock_theme` 导入失败）→ except 只补 `_theme_map`，
    # `_sector_zt` 保持未绑定 → 下一行 `UnboundLocalError` **整条 eod 链崩溃**，
    # 与注释「抓取失败 → 降级空标注」的降级意图相反。它本身是**纯本地计算**
    # （只依赖 relay+watch 的 ladder_health，不触网），故上提到 try 之外。
    _sector_zt = {str(it.get("theme") or ""): int(it.get("ladder_health") or 0) for it in relay + watch}
    try:
        from common.stock_theme import annotate_candidate
        # P0（2026-10-18）：**一律复用 `_theme_map_all`（=当日快照）**，不再二次实时抓。
        # 旧实现在 comparison is None 时另起一次 for_codes 抓取，
        # 与前面 pool 回填用的映射可能不同源 → 同一文件里 topic 前后不一致。
        _theme_map = {
            _c: _theme_map_all[_c]
            for _c in (str(it.get("dm") or it.get("code") or "") for it in relay + watch)
            if _c and _c in _theme_map_all
        }
    except Exception:
        _theme_map = {}
    for it in relay + watch:
        it["dim_panel"] = build_dimension_panel(it, env, theme_ctx)
        it["expectation"] = build_ai_expectation(it, env, theme_ctx)
        _code = str(it.get("dm") or it.get("code") or "")
        _ti = _theme_map.get(_code)
        _zt_cnt = _sector_zt.get(str(it.get("theme") or ""), 0)
        annotate_candidate(it, _ti, sector_zt_count=_zt_cnt)

    # AI 竞价预期覆盖（用户指定：AI 逐只读取涨停强度推断合理区间，覆盖确定性引擎基线）
    exp_overrides = (ai_env or {}).get("expectations") or {}
    for _it in relay + watch:
        _code = str(_it.get("code") or "")
        ov = exp_overrides.get(_code)
        if not ov:
            continue
        ov_rng = ov.get("expected_range")
        if not (isinstance(ov_rng, (list, tuple)) and len(ov_rng) == 2):
            continue
        lo, hi = float(ov_rng[0]), float(ov_rng[1])
        hi = max(hi, lo + 0.5)
        exp = _it.get("expectation") or {}
        exp["expected_range"] = [round(lo, 1), round(hi, 1)]
        exp["super_gap_min"] = round(hi + 1.0, 1)
        exp["ai_override"] = True
        _it["expectation"] = exp
        _it["ai_expectation_reason"] = str(ov.get("reason", ""))

    if narratives:
        for _it in relay + watch:
            _code = str(_it.get("code") or "")
            if _code in narratives:
                _it["narrative"] = narratives[_code]

    # 🐣 低位池补强（2026-10-27）：低池要进 09:25 裁决，就必须带上期望区间 + 面板，
    #   否则裁决层读不到预期区间 → 只能按「缺数据不裁决」放行，等于少一道闸。
    #   ⛔ 独立 try：低池补强失败**不得**影响 relay/watch 主链（宁可低池退化，不炸整条 eod）。
    if low_pool:
        for _it in low_pool:
            try:
                _it["dim_panel"] = build_dimension_panel(_it, env, theme_ctx)
                _it["expectation"] = build_ai_expectation(_it, env, theme_ctx)
                _code = str(_it.get("code") or "")
                annotate_candidate(_it, _theme_map.get(_code),
                                   sector_zt_count=_sector_zt.get(str(_it.get("theme") or ""), 0))
                if narratives and _code in narratives:
                    _it["narrative"] = narratives[_code]
            except Exception as _e:      # noqa: BLE001
                print(f"[EOD] 🐣 低位池补强降级 {_it.get('name')}: {_e}")
        try:
            from scripts.tx_query import enrich_candidates as _enrich_cands
            _enrich_cands(low_pool)
        except Exception as _e:          # noqa: BLE001
            print(f"[EOD] 🐣 低位池资金/筹码补强跳过: {_e}")

    # 个股资金/筹码补强（2026-08-25 用户「查询用腾讯自选股」·#1）：候选批量回填
    #   main_flow_yi(当日主力净流入亿)/main_flow_5d_yi/chip_profit_rate(获利盘%)/chip_avg_cost
    # 资金验证层：板块加强需个股资金确认（主力流入=真加强/流出=背离）；筹码获利盘高=抛压警示。
    # ⚠️ 腾讯自选股 npx 调用 ~1-3s，失败静默降级（不中断选股）；盘后查询·非实时推送。
    try:
        from scripts.tx_query import enrich_candidates
        _n = enrich_candidates(relay + watch)
        diagnostics["tx_enriched"] = _n
        if _n:
            print(f"[EOD] 腾讯自选股资金/筹码回填 {_n} 字段（候选 {len(relay)+len(watch)} 只）")
    except Exception as _e:
        diagnostics["tx_enriched"] = 0
        print(f"[EOD] 腾讯自选股补强跳过: {_e}")

    # A股监管异动线（2026-08-27 用户「达到监管标准的个股会被监管」）：候选批量回填
    #   ret_10d_pct(10日累计涨幅%)/ret_30d_pct(30日累计涨幅%)/kline_asof(数据截止日)
    # 监管口径：10日累计涨幅≥100% 或 30日累计涨幅≥200% → 沪深交易所严重异常波动认定线
    #   （重点监控/停牌核查·连板预期打折），渲染层红胶囊警示。
    # 数据源=腾讯前复权日K(n=35·common.tencent_kline，与 ma5_check/feedback_matrix 同源)；
    # ⚠️ 拉取 ~1-3s/批，失败静默降级（不中断选股）；无数据不编造。
    try:
        from scripts.regulatory_zone import enrich_regulatory
        _rn = enrich_regulatory(relay + watch)
        diagnostics["regulatory_enriched"] = _rn
        if _rn:
            print(f"[EOD] 监管异动线回填 {_rn} 字段（候选 {len(relay)+len(watch)} 只）")
    except Exception as _e:
        diagnostics["regulatory_enriched"] = 0
        print(f"[EOD] 监管线补强跳过: {_e}")

    if verdict.get("available"):
        diagnostics["daily_verdict_source"] = verdict["source"]
        diagnostics["daily_verdict_matched"] = sum(
            1 for it in relay + watch if it.get("daily_verdict")
        )
    diagnostics["excluded_low_turnover"] = n_excluded_turnover
    # ⚡ 封板强度豁免留痕（2026-11-03「A 拆掉下限」）：低成交额票**凭哪一条**进池。
    #    by_source 的键 = seal_gate.admit 的 source：
    #      strong(强封)/thick_seal(封单/流通≥5%)/theme_hot(同题材≥8家)
    #    ⛔ 只留痕，不参与任何裁决；缺失=0 表示今天没有低量票靠豁免进池。
    _seal_kinds: dict[str, int] = {}
    for _r in _seal_exempt_rows:
        _sk = str((_r.get("seal_gate") or {}).get("source") or "?")
        _seal_kinds[_sk] = _seal_kinds.get(_sk, 0) + 1
    diagnostics["seal_gate_exempt"] = {
        "enabled": _seal_gate.enabled(),
        "data_sanity_floor_yi": _seal_gate.DATA_SANITY_MIN_YI,
        "thick_seal_pct": _seal_gate.THICK_SEAL_EXEMPT_PCT,
        "strong_floor_yi": _seal_gate.STRONG_FLOOR_YI,
        "theme_hot_mates": _seal_gate.THEME_HOT_MATES,
        "count": len(_seal_exempt_rows),
        "by_source": _seal_kinds,
        "names": [str(_r.get("name") or "") for _r in _seal_exempt_rows],
        "codes": [str(_r.get("code") or "") for _r in _seal_exempt_rows],
    }
    # 👑 高度层门槛豁免留痕（2026-10-27）：0 = 今天没有龙头被门槛挡住（不是出错）
    diagnostics["leader_turnover_exempt"] = {
        "enabled": leader_exempt_enabled(),
        "floor_yi": MIN_TURNOVER_YI_LEADER,
        "market_max_boards": _market_max_boards,
        "count": len(_leader_exempt_rows),
        "names": [str(_r.get("name") or "") for _r in _leader_exempt_rows],
        "codes": [str(_r.get("code") or "") for _r in _leader_exempt_rows],
    }
    # P1（2026-10-18）：池外高辨识度名单条数（可 0；0 不代表被砍，只代表今天没有高板被剔）
    diagnostics["offpool_high_ident_count"] = len(offpool_high_ident)
    # 🎯 低位连板龙雷达条数（可 0；0 = 今天没有「低位+连板龙+有量」的票，不是出错）
    diagnostics["gene_radar_count"] = len(gene_radar)
    # 🐣 低位池条数（可买·与接力池共享名额）；enabled=False 表示开关关着
    diagnostics["low_pool"] = {
        "enabled": low_pool_enabled(),
        "count": len(low_pool),
        "names": [str(x.get("name") or "") for x in low_pool],
        "codes": [str(x.get("code") or "") for x in low_pool],
    }
    diagnostics["excluded_688"] = n_excluded_688
    diagnostics["selection_mode"] = sel_mode
    # 🌊（2026-10-19）：指数形态 + 标注命中数（available=False = 开关关闭，不是出错）
    diagnostics["market_style"] = {
        "style": market_style.get("style"),
        "label": market_style.get("label"),
        "rule": market_style.get("rule"),
        "idx_avg": (market_style.get("evidence") or {}).get("idx_avg"),
        "amount_yi": (market_style.get("evidence") or {}).get("amount_yi"),
        "counts": (market_style.get("stats") or {}).get("counts"),
        "no_trend_hint": market_style.get("no_trend_hint"),
        "data_gap": market_style.get("data_gap"),
    }
    diagnostics["data_source"] = zt.get("source", "xuangubao")
    diagnostics["pulse_ts"] = pulse.get("ts")
    # P0（2026-10-18）：题材映射来源留痕——审计/重跑时可凭 fingerprint 确认用的是哪份映射。
    diagnostics["theme_snapshot"] = dict(_theme_meta)
    # 🛡️ 2026-11-03：题材基座完整性（覆盖了多少涨停票 / 缺哪些 / 是否陈旧）。
    #   `ok=False` 时 `line` 是给人看的告警句；⛔ 只留痕，不阻断本次 eod。
    if _theme_guard:
        diagnostics["theme_guard"] = dict(_theme_guard)
    # P3（2026-10-18）：复盘推演读取/命中情况（available=False = 昨日没写推演，不是出错）
    diagnostics["review_directions"] = {
        "review_date": review_date,
        "available": bool(review_directions),
        "height_hit": review_stats.get("height_hit", 0),
        "new_direction_hit": review_stats.get("new_direction_hit", 0),
        "caution_hit": review_stats.get("caution_hit", 0),
        "missed_count": len(review_stats.get("missed") or []),
    }

    payload = {
        "schema": "wealth_code_candidates_v1",
        "date": zt["date"],
        "generated_at_bj": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "regime": env.get("regime"),
        "environment": env,                  # feedback_matrix 情绪（真实）
        "real_env": pulse,                  # pulse 大盘+板块环境（真实）
        "ai_env": ai_env or {},             # AI 研判输入
        "data_source": zt.get("source", "xuangubao"),
        # P0（2026-10-18 可复现）：当日题材映射来源快照（source/fingerprint/generated_at）。
        # 审计口径：同 date 的 candidates 若 fingerprint 相同，说明用的同一份映射，结果应当一致。
        "theme_snapshot": dict(_theme_meta),
        "relay": relay,
        "watch": watch,
        # P0-2 孤岛打通（2026-08-17）：盘中早鸟埋伏信号透传盘后卡。
        # early = 未封板强势拉升(uplimit=false) + newsflash 点名股（涨停前可买窗口），
        # 独立于 relay/watch（它们基于涨停池），供卡片「早鸟埋伏」区与 AI 点评引用——
        # 情绪传递：盘中爆发前夜 → 次日候选伏笔。
        "early_ambush": (pulse.get("early") or [])[:6],
        # P1（2026-10-18）：池外高辨识度名单——被成交额门槛剔除但连板高度仍高的票。
        # ⛔ 渲染层必须保留 buyable=False 语义：只解释"为什么不进池"，不得当买入标的。
        "offpool_high_ident": offpool_high_ident,
        # 🎯（2026-10-22）：低位连板龙雷达——1~2 板 + 连板龙股性 + 成交额≥1 亿，但没进池的票。
        # ⛔ 纯展示：不占 relay/watch 名额、不进 09:25 裁决；渲染层保留 buyable=False。
        "gene_radar": gene_radar,
        # 🐣（2026-10-27 用户「还不如推低位！」）：低位池——**真可买**的第三桶。
        # 来源 = gene_radar 里最强的几只（1~2 板 + 连板龙股性 + 位置不高 + 成交额≥1 亿）。
        # ⚠️ 与 relay/watch **共享当日名额**（不额外加仓），在 09:25 池内裁决里同台竞争；
        #    板数天然最低 → 排后面，只有前排被防守闸拦掉/名额空出来时才轮到。
        # ⛔ 关 `ENABLE_LOW_POOL=0` 即退回旧行为（这些票只剩 gene_radar 的「只看不买」）。
        "low_pool": low_pool,
        # 🌱（2026-11-11 改口径）：新方向首板槽 —— **已并进 watch 并过资格线**，
        #    09:25 走完整裁决（不是「只看不买」）。卡面只用它做线级摘要。
        #    判据/边界见 `core/new_direction_first_board.py` 文件头。
        "new_direction_first_board": new_direction_first_board,
        # P3（2026-10-18）：复盘推演（原文 + 今日命中/落空核对）。
        # 卡面用它解释"复盘说的高度/新方向，今天兑现了没有"，⛔ 不作买入依据。
        "review_directions": {
            "review_date": review_date,
            "available": bool(review_directions),
            "stats": review_stats,
            "directions": review_directions or {},
        },
        "diagnostics": diagnostics,
        # 🧊（2026-10-27）：全市场最高板——给 09:25 裁决层判「最高板 + 极薄封单」用。
        # ⛔ 只在盘后算一次并落盘（当日恒定），裁决层读它，不自己另算，避免两套口径。
        "market_max_boards": _market_max_boards,
        # 🌊（2026-10-19）：指数形态（主升/分歧/没方向）+ 明日该用哪种方式做。
        # ⛔ 只解释"为什么这样挑"，不作买入依据；买不买仍由 decision_outlook 判。
        "market_style": market_style,
        "global_narrative": global_narrative,
    }
    if two_pool.enabled():
        # ⛔ 2026-11-11：`new_direction_first_board` 不再随两池契约被清空 ——
        #    它已改成**真入池**（并进 watch → 过 `_merge_pools` 资格线），
        #    卡面靠它做「新方向哪几条线、入池了谁」的线级摘要。其余四桶照旧清掉。
        for _key in ("offpool_high_ident", "gene_radar", "low_pool", "early_ambush"):
            payload.pop(_key, None)
        _final_codes = {str(r.get("code")) for r in relay + watch}
        _selected_diag = {str(r.get("code")): r for r in diagnostics.get("two_pool_selection", {}).get("excluded", [])}
        _post_diag = {str(r.get("code")): r for r in diagnostics["two_pool_final"]["excluded"]}
        diagnostics["two_pool_final"]["all_excluded"] = [
            {"code": r.get("code"), "name": r.get("name"),
             "reason": (_post_diag.get(str(r.get("code"))) or _selected_diag.get(str(r.get("code"))) or
                        {"reason": "既有准入/个股/情绪风控拒绝，且无两类观察资格"})["reason"],
             "seal_gate": r.get("seal_gate")}
            for r in _scored_all if str(r.get("code")) not in _final_codes]
    from core.leader_cycle import enrich_payload
    if comparison is None:
        enrich_payload(payload, ROOT.parent)
    else:
        for rank, row in enumerate(sorted(relay + watch, key=lambda r: comparison["entries"].get(r["code"], {}).get("key", ()), reverse=True), 1):
            row["status_rank"] = rank
    # 必须在两条龙头证据分支及所有补强之后留痕，不假定板块资金/独立技术已接通。
    # 此层只标注最终池对象，不改变评分、顺序、名额或次日冻结裁决。
    try:
        from core.theme_resonance import annotate_eod as annotate_theme_resonance
        _resonance_rows = payload["relay"] + payload["watch"]
        annotate_theme_resonance(_resonance_rows, asof=zt["date"])
        diagnostics["theme_resonance"] = {
            "total": len(_resonance_rows),
            "flow_observed": sum(r["theme_resonance_facts"]["flow_status"] != "unknown" for r in _resonance_rows),
            "technical_observed": sum(r["theme_resonance_facts"]["technical_status"] != "unknown" for r in _resonance_rows),
        }
    except Exception as _e:  # noqa: BLE001
        print(f"[EOD] 板块趋势共振事实层降级: {_e}")
    # 最终候选的个股动能研究证据，不参与评分/分池或09:25裁决。
    if os.environ.get("ENABLE_RSI_EVIDENCE", "1").lower() not in {"0", "false", "off"}:
        try:
            from common.rsi_evidence import annotate_candidates
            annotate_candidates(payload["relay"] + payload["watch"], asof=zt["date"])
        except Exception as exc:
            diagnostics["rsi_evidence_error"] = type(exc).__name__
    # 对照镜像（2026-10-30 用户「吸收 quant-review 算法，不影响我们的购买策略」）：
    # 只写展示字段 qr_mirror，⛔ 不参与评分/分池/名额/仓位/09:25 裁决；失败只降级不留痕主链。
    if os.environ.get("ENABLE_QR_MIRROR", "1").lower() not in {"0", "false", "off"}:
        try:
            from core.qr_mirror import annotate as annotate_qr_mirror
            _qr_rows = payload["relay"] + payload["watch"]
            annotate_qr_mirror(_qr_rows, zt.get("zt_list"))
            diagnostics["qr_mirror"] = {
                "total": len(_qr_rows),
                "scored": sum(1 for r in _qr_rows
                              if (r.get("qr_mirror") or {}).get("applicable")),
            }
        except Exception as exc:  # noqa: BLE001
            diagnostics["qr_mirror_error"] = type(exc).__name__
    # 晋级三问（2026-10-30 用户「优化推送高胜率可晋级板块地位高的个股」）：
    # 高胜率（个股自身晋级率≥40% 且样本够）／可晋级（1~2 板且历史走过更高）／
    # 板块地位（前排身位且题材有梯队）。只写展示字段 `promotion_quiz`，
    # ⛔ 不参与评分/分池/名额/仓位/09:25 裁决；失败只降级不留痕主链。
    if os.environ.get("ENABLE_PROMOTION_QUIZ", "1").lower() not in {"0", "false", "off"}:
        try:
            from core.promotion_quiz import annotate as annotate_promotion_quiz
            _pq_rows = payload["relay"] + payload["watch"]
            annotate_promotion_quiz(_pq_rows)
            diagnostics["promotion_quiz"] = {
                "total": len(_pq_rows),
                "three": sum(1 for r in _pq_rows
                             if (r.get("promotion_quiz") or {}).get("passed") == 3),
                "two": sum(1 for r in _pq_rows
                           if (r.get("promotion_quiz") or {}).get("passed") == 2),
            }
        except Exception as exc:  # noqa: BLE001
            diagnostics["promotion_quiz_error"] = type(exc).__name__
    # 🔮 次日开盘推测（2026-11-02 用户「要和原有的选股卡与竞价卡结合，不是新开链路」）：
    # 给每只候选挂 `forecast`＝明早大概率开在几个点／几成概率买不到／这档真买到时的历史胜率。
    # ⛔ 只写展示字段，不改评分/分池/名额/仓位；09:25 裁决层读它换算「实测落在哪档」。
    # ⛔ 模型读 cache/relay_review/latest.json（32 日归档样本），样本缺失就整体跳过、不硬编数字。
    if os.environ.get("ENABLE_RELAY_FORECAST", "1").lower() not in {"0", "false", "off"}:
        try:
            from core.relay_forecast import annotate as annotate_relay_forecast
            _fc_rows = payload["relay"] + payload["watch"]
            # 📐 同题材身位（11-06 用户：「身位差只能在同一个板块里面适用，不同板块之间它是
            #    没有关系的」）→ 先按**选股宝 plates**归出 {代码: 题材} 与 {题材: 昨日最高板}。
            #    ⛔ 题材源与 09:25 的 `code_theme` 必须同源（plates > theme > ⛔ sector 不用）：
            #       拿申万行业当归线，一个行业里板数天然一致 → 身位差恒 0 → 口径静默失效。
            #    ⛔ 显式传 `theme_of`，不读候选行自带的 `theme`（那是东财题材，可能不同源）。
            _fc_theme_of: dict[str, str] = {}
            _fc_theme_max: dict[str, int] = {}
            try:
                from core import sector_gap_context as _sgc_fc
                _fc_day = str(zt.get("date") or "")[:10]
                _fc_plates = _sgc_fc.load_plates_map(_fc_day) or {}
                for _z in (zt.get("ztgc") or []):
                    _zcd = str(_z.get("dm") or "").strip()
                    if not _zcd:
                        continue
                    _zth, _zsrc = _sgc_fc.main_theme(_zcd, _z, _fc_plates)
                    if not _zth or _zth in ("其他", "未分类", "—", "-") or _zsrc not in ("plates", "theme"):
                        continue
                    _fc_theme_of[_zcd] = _zth
                    _zb = _to_int(_z.get("lbc"), 1)
                    if _zb > _fc_theme_max.get(_zth, 0):
                        _fc_theme_max[_zth] = _zb
            except Exception:  # noqa: BLE001
                _fc_theme_of, _fc_theme_max = {}, {}
            annotate_relay_forecast(_fc_rows, _market_max_boards,
                                    theme_max_boards=_fc_theme_max,
                                    theme_of=_fc_theme_of)
            _fc = [r.get("forecast") or {} for r in _fc_rows]
            diagnostics["relay_forecast"] = {
                "total": len(_fc_rows),
                "scored": sum(1 for f in _fc if f.get("gap_median") is not None),
                "wait": sum(1 for f in _fc if f.get("verdict") == "可等"),
                "watch": sum(1 for f in _fc if f.get("verdict") == "只观察"),
                "drop": sum(1 for f in _fc if f.get("verdict") == "放弃"),
                # 身位口径留痕：line=按同题材判、market=缺题材未裁决
                "pos_line": sum(1 for f in _fc if f.get("position_basis") == "line"),
                "pos_unknown": sum(1 for f in _fc if f.get("position_basis") != "line"),
                "themes": len(_fc_theme_max),
            }
        except Exception as exc:  # noqa: BLE001
            diagnostics["relay_forecast_error"] = type(exc).__name__
    out_path = out_path or (CACHE_DIR / f"candidates-{zt['date'] or 'latest'}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 数据源聚合：盘后候选与闸门写独立 eod 分区，不覆盖早盘竞价输入。
    # eod_candidates/eod_gates 不属于八个业务维度的覆盖分母。
    #    ⛔ 只搬运已算好的结论（119 字段压到 31），不重算、不改任何闸门判定；
    #    ⛔ 写盘失败不影响盘后主链（聚合是增强，不是依赖）。
    try:
        from core import snapshot as _snap
        _candidate_section = _snap.build_candidate_section(payload)
        _candidate_section["candidate_date"] = payload.get("date")
        _snap.put_section(str(zt["date"] or ""), "eod_candidates", _candidate_section)
        _gates = {k: ({kk: v.get(kk) for kk in
                       ("enabled", "before", "after", "dropped", "moved_names",
                        "dropped_names", "count", "names", "regime") if kk in v}
                      if isinstance(v, dict) else v)
                  for k, v in (diagnostics or {}).items()
                  if k in ("pool_admit", "outpool_check", "thin_seal_top", "dragon_gate",
                           "high_win_gate", "seal_gate_exempt", "leader_turnover_exempt")}
        if _gates:
            _gates["candidate_date"] = payload.get("date")
            _snap.put_section(str(zt["date"] or ""), "eod_gates", _gates)
    except Exception as _se:                       # noqa: BLE001
        print(f"[EOD] 快照聚合跳过：{_se}")
    return payload


def _load_strong_sectors(
    cache_dir: Path | None = None,
    n_days: int = STRONG_SECTOR_N_DAYS,
    min_persist_days: int = STRONG_SECTOR_MIN_PERSIST_DAYS,
    exclude_date: str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    ⚠️ 2026-09-29 标注：**唯一调用方是废弃死代码 `_rank_and_split`**（run_eod 不调用它），
    故本函数（含 `exclude_date` 语义）**不影响任何候选结果**。排查「同日重跑 eod 结果不同」
    时不要从这里入手（见 `_rank_and_split` docstring 的说明）。

    聚合最近 n_days 天 candidates-*.json 的 theme(申万二级)字段，
    生成「最近强势板块清单」——即用户定义的"每天记录的题材炒作路径里爆发过的板块"。

    返回 {theme: {"days":跨日命中天数, "count":累计候选命中数, "persistent": bool(≥min_persist_days)}}。
    exclude_date：排除当天文件，避免用今天的候选给今天的候选自举加分。
    """
    base = Path(cache_dir) if cache_dir else CACHE_DIR
    files = sorted(base.glob("candidates-*.json"), reverse=True)[: n_days + 1]
    day_themes: dict[str, set[str]] = {}
    theme_count: dict[str, int] = {}
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        date = d.get("date")
        if date == exclude_date or date in day_themes:
            continue  # 排除当天 + 同日多文件只计一次
        pool = d.get("relay", []) + d.get("watch", [])
        themes_today: set[str] = set()
        for it in pool:
            t = it.get("theme")
            if t:
                themes_today.add(t)
                theme_count[t] = theme_count.get(t, 0) + 1
        day_themes[date or f.name] = themes_today
    result: dict[str, dict[str, Any]] = {}
    for t, cnt in theme_count.items():
        days_hit = sum(1 for s in day_themes.values() if t in s)
        result[t] = {
            "days": days_hit,
            "count": cnt,
            "persistent": days_hit >= min_persist_days,
        }
    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    p = run_eod()
    print(f"date={p['date']} regime={p['regime']} relay={len(p['relay'])} watch={len(p['watch'])}")
