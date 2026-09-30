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
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from core.leader import (
    _build_theme_stats,
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
from core.dimension_score import build_dimension_panel
from core.quant_score import select_relay_watch
from core.historical_relay import annotate_historical_relay

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
# 成交额门控（用户纪律 2026-08-13）：成交额<5亿 的票流动性/辨识度不足，一律不进池。
# 须在板块梯队计算(_attach_sector_ladder)之前过滤，避免低流动性票污染梯队/龙一归属。
MIN_TURNOVER_YI = 5.0
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
    cap_total = _clamp(
        cje_target_score * 0.44 + vol_tier_score * 0.24 + cap_score * 0.17 + liquidity_band_score * 0.15
    )
    fbt = str(fbt or "")
    is_yizi = bool(hs_pct > 0 and hs_pct < 2 and cje_yi < 3 and zbc == 0 and fbt.startswith("0925"))
    is_shrink = bool(hs_pct > 0 and hs_pct < 3 and cje_yi < 5 and zbc == 0)
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
            "seal_amt": seal_amt, "lt_mktcap": lt_mktcap,  # 透传封单金额(亿)/流通市值(亿)
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


def _attach_sector_ladder(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    板块梯队因子接入：按题材聚合涨停池，调用 core/leader.build_sector_ladder 计算
    每个题材的梯队健康度，并把结果回写到每只候选：
      - ladder_health     : 0~10（核心驱动分量）
      - ladder_has_complete: 是否完整梯队（龙头+龙二+龙三+≥3补涨）
      - is_sector_dragon  : 是否是该题材封板结构顶端（龙一）
    纯数据驱动，无任何个股硬编码。
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
    return scored


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

    # ⛔ 2026-08-25 三修（用户「黄河旋风明明是钻石·你把它定到机器人里去了·以题材的涨停
    # 为准不要以板块的涨停」）：**完整涨停池(ztgc)回填 core_theme（炒作题材）**，且在
    # _score_all 之前——候选行继承 core_theme 后，annotate_lone 的 _theme_key 优先用
    # 炒作题材聚合（黄河旋风=金刚石/培育钻石 归钻石，不被行业 通用设备→机器人 误导）。
    # 旧实现只在 annotate_lone 之后对 relay+watch 候选拉题材，pool 行只有静态行业
    # → 候选与 pool 失配，题材层聚合到「机器人」错误桶（实证：黄河旋风 theme_name=机器人
    # ·theme_mates_cnt=6 误判）。ThemeFetcher 带缓存（fetch_all 幂等），后续候选回填复用。
    try:
        from common.stock_theme import ThemeFetcher
        _theme_map_all = ThemeFetcher().fetch_all()
        for _z in (zt.get("ztgc") or []):
            _zc = str(_z.get("dm") or _z.get("code") or "")
            _ti = _theme_map_all.get(_zc) if _zc else None
            if _ti and _ti.get("theme"):
                _z["core_theme"] = _ti.get("theme")
            else:
                _z.setdefault("core_theme", "")
    except Exception:
        pass  # 题材接口失败 → pool 行无 core_theme，回退行业映射（不编造）

    scored = _score_all(zt["ztgc"], zt["code2themes"], zt["hot_sectors"])

    # 成交额门控（用户纪律 2026-08-13）：成交额< MIN_TURNOVER_YI 亿的票不进池。
    # 须在板块梯队计算之前过滤，避免低流动性票污染梯队/龙一归属。
    n_before_filter = len(scored)
    scored = [it for it in scored if (it.get("cje_yi") or 0.0) >= MIN_TURNOVER_YI]
    n_excluded_turnover = n_before_filter - len(scored)

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
    _attach_sector_ladder(scored)

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

    # 历史连板晋级因子：必须在实际生效的 select_relay_watch 之前回填，避免只展示不参与排序。
    # 统计同一股票/同一高度的真实次日晋级后验，再按当前晋级率、连板晋级率、情绪分小幅校准。
    history_diag = annotate_historical_relay(
        scored, ZT_HIST_DIR, zt.get("date") or _today_d, env
    )

    # faithful §4.4 四档接力/观察选择（spec 十一维 raw_score 驱动 + 真实环境 + AI）
    relay, watch, diagnostics, sel_mode = select_relay_watch(scored, env, pulse, ai_env)
    diagnostics["historical_relay"] = history_diag

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
        from common.stock_theme import ThemeFetcher, annotate_candidate
        _tf = ThemeFetcher()
        _theme_map = _tf.for_codes([str(it.get("dm") or it.get("code") or "") for it in relay + watch])
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
    diagnostics["excluded_688"] = n_excluded_688
    diagnostics["selection_mode"] = sel_mode
    diagnostics["data_source"] = zt.get("source", "xuangubao")
    diagnostics["pulse_ts"] = pulse.get("ts")

    payload = {
        "schema": "wealth_code_candidates_v1",
        "date": zt["date"],
        "generated_at_bj": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "regime": env.get("regime"),
        "environment": env,                  # feedback_matrix 情绪（真实）
        "real_env": pulse,                  # pulse 大盘+板块环境（真实）
        "ai_env": ai_env or {},             # AI 研判输入
        "data_source": zt.get("source", "xuangubao"),
        "relay": relay,
        "watch": watch,
        # P0-2 孤岛打通（2026-08-17）：盘中早鸟埋伏信号透传盘后卡。
        # early = 未封板强势拉升(uplimit=false) + newsflash 点名股（涨停前可买窗口），
        # 独立于 relay/watch（它们基于涨停池），供卡片「早鸟埋伏」区与 AI 点评引用——
        # 情绪传递：盘中爆发前夜 → 次日候选伏笔。
        "early_ambush": (pulse.get("early") or [])[:6],
        "diagnostics": diagnostics,
        "global_narrative": global_narrative,
    }
    from core.leader_cycle import enrich_payload
    enrich_payload(payload, ROOT.parent)
    out_path = out_path or (CACHE_DIR / f"candidates-{zt['date'] or 'latest'}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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
