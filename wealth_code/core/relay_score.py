#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
明日接力候选权重评分系统（2026-08-17 设计 · v0.1）

目标：从当日涨停池中，量化识别「板块领涨 + 成交活跃（容量核心）」个股，
按五维加权输出「明日接力优先级」排序。对齐现有系统因子（leader/cap_score/
ladder/theme_rotation），不重复造轮子——复用 pipeline_eod 已算好的字段。

五维权重（config/relay_score_config.json 可调）：
  leadership(领涨地位,0.25) + capacity(容量规模,0.20) + sector_momentum(板块联动,0.15)
  + ladder_position(连板梯队,0.20) + capital_flow(资金净流入,0.20)
  → 总加权分 × 环境调制(regime/sentiment) → 分档(首选/次选/观察)

纯算法层，仅依赖标准库；输入=当日候选 dict（与 candidates-*.json 同构），
输出=score + band + 分项明细，供回测与调参。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# 配置加载：默认读 wealth_code/config/relay_score_config.json（可被覆盖）
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "relay_score_config.json"

# 系统数据根（cache/），用于统一取数入口
ROOT = Path(__file__).resolve().parent.parent.parent


def _load_daily_metrics(cache_dir: Path | str | None = None) -> dict[str, Any]:
    """
    【统一日级指标入口】委托 adapters.input_zt.load_environment（唯一真相源），
    返回晋级率/连板晋级率/情绪分/核按钮/真破位/炸板率/regime 等日级指标。
    ⛔ 口径铁律：晋级率/regime 等指标唯一真相源 = load_environment（feedback_matrix 守卫产物），
    任何模块不得自行重算 regime（曾因 else 分支默认 risk_on 与现有 neutral 口径冲突，2026-08-17 修正）；
    本函数是 relay_score 及所有下游的唯一取数入口（防重复计算/口径冲突）。
    """
    import sys
    from pathlib import Path as _P
    _wc = _P(__file__).resolve().parent.parent
    if str(_wc) not in sys.path:
        sys.path.insert(0, str(_wc))
    from adapters.input_zt import load_environment  # type: ignore

    base = Path(cache_dir) if cache_dir else (ROOT / "cache")
    env = load_environment(base)
    fb = {}
    try:
        fb = json.loads((base / "feedback_matrix.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    # 补充 load_environment 未返回但对接方案需要的字段（只读，不重算口径）
    return {
        "regime": env.get("regime") or "neutral",
        "sentiment_score": _sentiment_value(env.get("sentiment_score")),
        "sentiment_model": env.get("sentiment_model"),
        "sentiment_quality": env.get("sentiment_quality", "unavailable"),
        "sentiment_inputs_missing": env.get("sentiment_inputs_missing", []),
        "sentiment_source_at": env.get("sentiment_source_at"),
        "sentiment_label": str(env.get("sentiment_label") or ""),
        "promote_rate": _f(env.get("promote_rate")),
        "lb_promote_rate": _f(fb.get("lb_promote_rate")),
        "nuke_count": int(env.get("nuke_count") or 0),
        "break_count": int(env.get("break_count") or 0),
        "broken_rate": _f(env.get("broken_rate")),
        "zt_count": int(env.get("now_zt_count") or 0),
        "prev_zt_count": int(fb.get("prev_zt_count") or 0),
        "up_rate": _f(env.get("up_rate")),
        "avg_pct": _f(env.get("avg_pct")),
        "generated_ts": str(fb.get("generated_ts") or ""),
    }


def _build_news_index(newsflash_history_dir: Path | str | None = None) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """
    【消息面关联索引】读 cache/newsflash_history/daily_{date}.json（capture_daily 产出）。
    返回 (code_hits_map, theme_hits_map)：
      code_hits_map: {code: [titles]}  —— 消息提到该个股的标题列表（按 6 位代码正则抽取）
      theme_hits_map: {theme: [titles]} —— 消息涉及该题材的标题列表（按题材名匹配）
    消息面不参与评分计算（数据/AI 边界），仅作 AI 点评的 news_ref 引用。
    """
    base = Path(newsflash_history_dir) if newsflash_history_dir else (ROOT / "cache" / "newsflash_history")
    import re
    code_hits: dict[str, list[str]] = {}
    theme_hits: dict[str, list[str]] = {}
    if not base.is_dir():
        return code_hits, theme_hits
    for f in sorted(base.glob("daily_*.json"))[-1:]:  # 最近一日
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for nf in d.get("newsflash") or []:
            title = str(nf.get("title") or nf.get("content") or "").strip()
            if not title:
                continue
            for m in re.findall(r"\b(\d{6})\b", title):
                code_hits.setdefault(m, []).append(title[:60])
            themes = d.get("themes") or []
            for t in themes:
                nm = str(t.get("name") or "")
                if nm and nm in title:
                    theme_hits.setdefault(nm, []).append(title[:60])
    return code_hits, theme_hits


def _load_config(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _f(v: Any, d: float = 0.0) -> float:
    try:
        if v is None:
            return d
        n = float(v)
        return n if math.isfinite(n) else d
    except Exception:
        return d


def _pass_gates(cand: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, str]:
    """硬门槛（对齐 pipeline_eod 铁律）：ST/一字/688/成交额下限。"""
    g = cfg.get("gates", {})
    code = str(cand.get("code") or "")
    if g.get("exclude_st") and cand.get("is_st"):
        return False, "ST"
    if g.get("exclude_one_word") and cand.get("is_one_word"):
        return False, "一字板·无参与机会"
    if g.get("exclude_688") and code.startswith("688"):
        return False, "688无权限"
    if (_f(cand.get("cje_yi")) < _f(g.get("min_turnover_yi"), 5.0)):
        return False, "成交额<5亿·辨识度不足"
    return True, ""


def _score_leadership(cand: dict[str, Any], cfg: dict[str, Any]) -> float:
    """领涨地位：板块龙 + 高度辨识度 + 先发 + 非跟风。
    2026-08-25 加 fbt 封板时间因子（用户「小周期载体·逆环境主动走强」思路·#1）：
    早封=主动走强（秒板/竞价封死是板块反推的机械证据之一），尾盘弱封=被动/分歧。
    数据即事实：fbt 缺失记 0 不编造；一字板已由 _pass_gates 排除（无参与机会）。
    """
    s = cfg["leadership"]
    sc = 0.0
    if cand.get("is_sector_dragon"):
        sc += _f(s["is_dragon"])
    b = int(_f(cand.get("boards"), 1))
    if b >= 4:
        sc += _f(s["boards_ge4"])
    elif b == 3:
        sc += _f(s["boards_eq3"])
    sc += min(_f(cand.get("lead_score")), _f(s["lead_score_cap"]))
    # fbt 封板时间（HHMM 如 094536 → 945）：早封加分 / 尾盘弱封惩罚
    fbt_raw = str(cand.get("fbt") or "")
    if len(fbt_raw) >= 4:
        try:
            fbt_min = int(fbt_raw[:2]) * 60 + int(fbt_raw[2:4])
        except ValueError:
            fbt_min = 0
        if fbt_min > 0:
            fb = s.get("fbt") or {}
            if fbt_min <= 570:      # ≤09:30 竞价封死/T字（主动性最强·但多为通道一字·次高）
                sc += _f(fb.get("before_0930"))
            elif fbt_min <= 600:    # ≤10:00 早盘秒板（主动走强·最健康·载体确认核心窗口）
                sc += _f(fb.get("before_1000"))
            elif fbt_min <= 690:    # ≤11:30 上午封板（仍属主动·弱一档）
                sc += _f(fb.get("before_1130"))
            elif fbt_min <= 780:    # ≤14:00 午后封板（中性·不奖不罚）
                sc += _f(fb.get("before_1400"))
            else:                   # >14:00 尾盘弱封（被动/分歧·惩罚）
                sc += _f(fb.get("after_1400"))
    tags = cand.get("tags") or []
    if "跟风" in tags or "补涨" in str(cand.get("identity") or ""):
        sc += _f(s["follower_penalty"])
    return _clamp(sc)


def _score_capacity(cand: dict[str, Any], cfg: dict[str, Any]) -> float:
    """容量规模：成交额分段 + 市值黄金区间 + 换手活跃 + 封单强度。"""
    s = cfg["capacity"]
    sc = 0.0
    cje = _f(cand.get("cje_yi"))
    if cje >= 30:
        sc += _f(s["cje_yi_ge30"])
    elif cje >= 15:
        sc += _f(s["cje_yi_ge15"])
    elif cje >= 8:
        sc += _f(s["cje_yi_ge8"])
    elif cje >= 5:
        sc += _f(s["cje_yi_ge5"])
    lt = _f(cand.get("lt_mktcap"), _f(cand.get("zsz_yi")))
    if _f(s["mktcap_lo"]) <= lt <= _f(s["mktcap_hi"]):
        sc += _f(s["mktcap_bonus"])
    hs = _f(cand.get("hs"))
    if _f(s["hs_lo"]) <= hs <= _f(s["hs_hi"]):
        sc += _f(s["hs_bonus"])
    elif 0 < hs < 3:
        sc += _f(s["hs_shrink_penalty"])
    if _f(cand.get("seal_amt")) >= _f(s["seal_amt_min"]):
        sc += _f(s["seal_amt_bonus"])
    return _clamp(sc)


def _score_sector_momentum(cand: dict[str, Any], cfg: dict[str, Any]) -> float:
    """板块联动强度：题材涨停广度 + 梯队健康 + 板块资金净流入。"""
    s = cfg["sector_momentum"]
    sc = 0.0
    zt = int(_f(cand.get("sector_zt_count")))
    if zt >= 5:
        sc += _f(s["zt_count_ge5"])
    elif zt >= 3:
        sc += _f(s["zt_count_ge3"])
    elif zt >= 2:
        sc += _f(s["zt_count_ge2"])
    grade = str(cand.get("ladder_grade") or "")
    if grade == "A":
        sc += _f(s["ladder_grade_A"])
    elif grade == "B":
        sc += _f(s["ladder_grade_B"])
    else:
        sc += _f(s["ladder_grade_C"])
    if _f(cand.get("sector_flow_yi")) > 0:
        sc += _f(s["sector_flow_positive_bonus"])
    return _clamp(sc)


def _score_ladder_position(cand: dict[str, Any], cfg: dict[str, Any], env: dict[str, Any]) -> float:
    """连板梯队位置：板数高度 + 空间龙(全市场最高板) + 板块晋级率。"""
    s = cfg["ladder_position"]
    sc = 0.0
    b = int(_f(cand.get("boards"), 1))
    if b >= 5:
        sc += _f(s["boards_ge5"])
    elif b == 4:
        sc += _f(s["boards_eq4"])
    elif b == 3:
        sc += _f(s["boards_eq3"])
    elif b == 2:
        sc += _f(s["boards_eq2"])
    else:
        sc += _f(s["boards_eq1"])
    max_board = int(_f(env.get("max_boards")))
    if max_board and b >= max_board:
        sc += _f(s["space_dragon_bonus"])
    if _f(cand.get("sector_promote_rate")) >= 40:
        sc += _f(s["promote_rate_ge40"])
    return _clamp(sc)


def _score_capital_flow(cand: dict[str, Any], cfg: dict[str, Any]) -> float:
    """资金净流入：板块主力净流入分段 + 封单方向。"""
    s = cfg["capital_flow"]
    sc = 0.0
    fy = _f(cand.get("sector_flow_yi"))
    if fy >= 50:
        sc += _f(s["flow_yi_ge50"])
    elif fy >= 10:
        sc += _f(s["flow_yi_ge10"])
    elif fy > 0:
        sc += _f(s["flow_yi_positive"])
    else:
        sc += _f(s["flow_yi_negative"])
    if _f(cand.get("seal_amt")) > 0:
        sc += _f(s["seal_direction_bonus"])
    return _clamp(sc)


def _sentiment_value(value: Any) -> float | None:
    """评分仅接受真实的 1~10 有限数；缺失不构造中性或冰点观测。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 1 <= value <= 10 else None


def _env_multiplier(env: dict[str, Any], cfg: dict[str, Any]) -> tuple[float, float]:
    """环境调制：保留独立 regime；未知情绪不参与加罚。"""
    m = float(cfg["env_multiplier"].get(env.get("regime") or "neutral", 1.0))
    adj = 0.0
    ea = cfg.get("env_adjust", {})
    senti = _sentiment_value(env.get("sentiment_score"))
    if senti is None:
        return m, adj
    if senti >= _f(ea.get("sentiment_high_min"), 8.0):
        adj += _f(ea.get("sentiment_high_bonus"), 5.0)
    elif senti <= _f(ea.get("sentiment_low_max"), 3.0):
        adj += _f(ea.get("sentiment_low_penalty"), -10.0)
    return m, adj


def score_relay_candidate(
    cand: dict[str, Any],
    env: dict[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """单只候选评分。cand 需含 pipeline_eod 已算因子 + 板块聚合字段（见 rank_relay_candidates 补全）。"""
    cfg = cfg or _load_config()
    ok, reason = _pass_gates(cand, cfg)
    if not ok:
        return {"code": cand.get("code"), "name": cand.get("name"), "pass": False, "exclude_reason": reason, "score": 0.0, "band": "排除"}

    w = cfg["weights"]
    s_lead = _score_leadership(cand, cfg)
    s_cap = _score_capacity(cand, cfg)
    s_sec = _score_sector_momentum(cand, cfg)
    s_lad = _score_ladder_position(cand, cfg, env)
    s_flow = _score_capital_flow(cand, cfg)

    weighted = (
        _f(w["leadership"]) * s_lead
        + _f(w["capacity"]) * s_cap
        + _f(w["sector_momentum"]) * s_sec
        + _f(w["ladder_position"]) * s_lad
        + _f(w["capital_flow"]) * s_flow
    )
    m, adj = _env_multiplier(env, cfg)
    final = _clamp(weighted * m + adj)

    bands = cfg["bands"]
    if final >= _f(bands["first_choice_min"], 80):
        band = "⭐⭐⭐ 首选接力"
    elif final >= _f(bands["second_choice_min"], 65):
        band = "⭐⭐ 次选接力"
    elif final >= _f(bands["watch_min"], 50):
        band = "⭐ 观察"
    else:
        band = "不入池"

    return {
        "code": cand.get("code"),
        "name": cand.get("name"),
        "theme": cand.get("theme"),
        "boards": cand.get("boards"),
        "pass": True,
        "score": round(final, 2),
        "band": band,
        "env_mult": round(m, 2),
        "env_adj": round(adj, 2),
        "sentiment_adjustment_available": _sentiment_value(env.get("sentiment_score")) is not None,
        "sentiment_model": env.get("sentiment_model"),
        "sentiment_quality": env.get("sentiment_quality", "unavailable"),
        "sentiment_inputs_missing": env.get("sentiment_inputs_missing", []),
        "parts": {
            "leadership": round(s_lead, 1),
            "capacity": round(s_cap, 1),
            "sector_momentum": round(s_sec, 1),
            "ladder_position": round(s_lad, 1),
            "capital_flow": round(s_flow, 1),
        },
    }


def _load_sector_flow_map(cache_dir: Path | str | None = None) -> dict[str, float]:
    """
    全量板块资金映射 {板块名: flow_yi 亿}，来源优先级：
    ① cache/sector_flow_history/sector_flow_{date}.json 的 boards（东财全量板块资金·覆盖最全）
    ② 兜底 theme_rotation.market_context.inflow_top/outflow_top（Top5）
    返回空 dict 表示无数据（调用方诚实记 0，不编造）。
    """
    base = Path(cache_dir) if cache_dir else (ROOT / "cache")
    # ① sector_flow_history 全量（按 mtime 取最新）
    import glob as _g
    sf_dir = base / "sector_flow_history"
    files = sorted(_g.glob(str(sf_dir / "sector_flow_*.json")))
    if files:
        try:
            sf = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
            boards = sf.get("boards") or {}
            return {str(k): float(v.get("flow_yi") or 0.0) for k, v in boards.items()}
        except Exception:
            pass
    # ② market_context Top5 兜底
    try:
        tr = json.loads((base / "theme_rotation.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    from common.em_flow import sector_risk_for_decision
    mc = sector_risk_for_decision(tr.get("market_context"))
    flow_map: dict[str, float] = {}
    for it in mc.get("inflow_top") or []:
        flow_map[str(it.get("name") or "")] = float(it.get("flow_yi") or 0.0)
    for it in mc.get("outflow_top") or []:
        flow_map[str(it.get("name") or "")] = -abs(float(it.get("flow_yi") or 0.0))
    return flow_map


def build_sector_agg_from_theme_rotation(
    theme_rotation: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    zt_list: list[dict[str, Any]] | None = None,
    cache_dir: Path | str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    从 theme_rotation.json + candidates + 涨停池构建板块聚合
    {theme: {zt_count, flow_yi, promote_rate}}：
    - zt_count：**全市场涨停池**该题材涨停数（zt_list 按 sector 计数；未传则用候选池计数·诚实标注）
    - flow_yi：优先 sector_flow_history 全量板块资金（东财·亿），兜底 market_context Top5
      （2026-08-17 优化：market_context 仅 Top5 导致 15 板块仅 1 个有资金数据、其余 14 个失真记 0）
    - promote_rate：theme_rotation.candidates[].flow_vals 无晋级率字段 → 记 0（可后续接 feedback_matrix 板块晋级）
    无资金/晋级数据的题材 → 0（诚实降权，不编造）。与 pipeline_eod 的数据血缘一致。
    """
    agg: dict[str, dict[str, Any]] = {}
    tr = theme_rotation or {}
    from common.em_flow import sector_risk_for_decision
    mc = sector_risk_for_decision(tr.get("market_context"))
    # 板块资金映射：优先全量 sector_flow_history，兜底 market_context Top5
    flow_map = _load_sector_flow_map(cache_dir)
    if not flow_map:
        for it in mc.get("inflow_top") or []:
            flow_map[str(it.get("name") or "")] = float(it.get("flow_yi") or 0.0)
        for it in mc.get("outflow_top") or []:
            flow_map[str(it.get("name") or "")] = -abs(float(it.get("flow_yi") or 0.0))

    # 涨停广度：优先全市场 zt_list，否则候选池兜底
    zt_by_theme: dict[str, int] = {}
    src = zt_list if zt_list is not None else candidates
    for c in src:
        t = str(c.get("theme") or c.get("sector") or "")
        if t:
            zt_by_theme[t] = zt_by_theme.get(t, 0) + 1

    # 候选题材集合（relay+watch 涉及的所有题材都要有聚合，未在资金 Top5 的记 0）
    for c in candidates:
        t = str(c.get("theme") or "")
        if not t:
            continue
        agg[t] = {
            "zt_count": zt_by_theme.get(t, 1),
            "flow_yi": flow_map.get(t, 0.0),
            "promote_rate": 0.0,
        }
    return agg


def rank_relay_candidates(
    candidates: list[dict[str, Any]],
    env: dict[str, Any],
    sector_agg: dict[str, dict[str, Any]] | None = None,
    cfg: dict[str, Any] | None = None,
    news_index: tuple[dict[str, list[str]], dict[str, list[str]]] | None = None,
) -> list[dict[str, Any]]:
    """
    批量评分排序。candidates = relay+watch 候选（与 candidates-*.json 同构）。
    sector_agg: {theme: {zt_count, flow_yi, promote_rate, max_boards}}——板块聚合，
    未传则从 candidates 内聚合并补齐字段（无资金数据时 flow 分按 0 计，诚实降权）。
    news_index: (_build_news_index 产出) (code_hits, theme_hits)——回填 news_ref 供 AI 点评引用。
    """
    cfg = cfg or _load_config()
    if sector_agg is None:
        sector_agg = {}
        zt_by_theme: dict[str, int] = {}
        for c in candidates:
            t = str(c.get("theme") or "")
            zt_by_theme[t] = zt_by_theme.get(t, 0) + 1
        max_b = max((int(_f(c.get("boards"), 1)) for c in candidates), default=0)
        for t, n in zt_by_theme.items():
            sector_agg[t] = {"zt_count": n, "flow_yi": 0.0, "promote_rate": 0.0}
        env = dict(env)
        env.setdefault("max_boards", max_b)
    code_hits, theme_hits = news_index or ({}, {})
    # P0 孤岛打通（2026-08-17）：ma5_status（五日线纪律快照）首个代码层消费者。
    # 此前仅 backup_snapshot/system_health 引用（备份+体检），业务零消费。
    # 现在：候选命中 ma5_status 且跌破五日线 → 回填 ma5_broken=True 风险标注（供 AI 点评引用，
    # 不改评分权重——遵守「纯标记不塞池」铁律）。
    ma5_map: dict[str, dict[str, Any]] = {}
    try:
        _ma5 = json.loads((ROOT / "cache" / "ma5_status.json").read_text(encoding="utf-8"))
        for _it in (_ma5 or {}).get("items") or []:
            _c = str(_it.get("code") or "")
            if _c:
                ma5_map[_c] = _it
    except Exception:
        ma5_map = {}

    out = []
    for c in candidates:
        t = str(c.get("theme") or "")
        agg = sector_agg.get(t, {})
        cc = dict(c)
        cc["sector_zt_count"] = int(_f(agg.get("zt_count"), 1))
        cc["sector_flow_yi"] = _f(agg.get("flow_yi"))
        cc["sector_promote_rate"] = _f(agg.get("promote_rate"))
        r = score_relay_candidate(cc, env, cfg)
        # 关联字段回填（数据对接：sector_ref / news_ref）
        r["sector_ref"] = {
            "zt_count": int(_f(agg.get("zt_count"), 1)),
            "flow_yi": _f(agg.get("flow_yi")) or None,
            "ladder_grade": c.get("ladder_grade"),
        }
        hits = code_hits.get(str(c.get("code") or ""), [])
        r["news_ref"] = {"hit_count": len(hits), "top_titles": hits[:2]}
        # ma5 破位标注（五日线=生命线，跌破=风险提示；无快照数据不标注）
        _ma5_it = ma5_map.get(str(c.get("code") or ""))
        if _ma5_it is not None:
            r["ma5_broken"] = not bool(_ma5_it.get("above_ma5"))
            r["ma5_pct_vs"] = _f(_ma5_it.get("pct_vs_ma5"))
        out.append(r)
    out.sort(key=lambda r: (r["pass"], r["score"]), reverse=True)
    return out


# ============ 自检（真实数据验证 · 澳洋健康/共进） ============
if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pipelines.pipeline_eod import load_environment  # type: ignore

    env = load_environment()
    env["max_boards"] = 4  # 08-17 市场最高板（蓝盾断板后 4 板为空间锚）

    # 用 candidates-2026-08-17 真实字段构造（板块聚合字段来自 theme_rotation/板块聚合）
    samples = [
        {"code": "603118", "name": "共进股份", "theme": "通信设备", "boards": 3, "zbc": 11, "fbt": "093022",
         "hs": 13.31, "cje_yi": 20.22, "zsz_yi": 152.1, "lt_mktcap": 152.1, "seal_amt": 1.53,
         "cap_score": 87.97, "is_sector_dragon": True, "ladder_health": 8.2, "ladder_grade": "A",
         "is_capacity_core": True, "is_one_word": False, "is_st": False, "pct": 10.02, "lead_score": 20.0,
         "tags": ["带动板块", "先发"], "sector_zt_count": 3, "sector_flow_yi": 194.0, "sector_promote_rate": 30.0},
        {"code": "002172", "name": "澳洋健康", "theme": "医疗服务", "boards": 4, "zbc": 0, "fbt": "094915",
         "hs": 26.86, "cje_yi": 9.8, "zsz_yi": 38.21, "lt_mktcap": 38.18, "seal_amt": 0.54,
         "cap_score": 43.32, "is_sector_dragon": True, "ladder_health": 4.6, "ladder_grade": "C",
         "is_capacity_core": False, "is_one_word": False, "is_st": False, "pct": 9.91, "lead_score": 20.0,
         "tags": [], "sector_zt_count": 12, "sector_flow_yi": 30.0, "sector_promote_rate": 45.0},
        {"code": "002081", "name": "金螳螂", "theme": "装修装饰", "boards": 4, "zbc": 9, "fbt": "093054",
         "hs": 18.26, "cje_yi": 27.83, "zsz_yi": 154.54, "lt_mktcap": 153.89, "seal_amt": 1.16,
         "cap_score": 81.47, "is_sector_dragon": True, "ladder_health": 4.6, "ladder_grade": "C",
         "is_capacity_core": False, "is_one_word": False, "is_st": False, "pct": 10.02, "lead_score": 20.0,
         "tags": ["先发", "空间"], "sector_zt_count": 1, "sector_flow_yi": -5.0, "sector_promote_rate": 20.0},
    ]
    for r in rank_relay_candidates(
        samples,
        env,
        sector_agg={
            "通信设备": {"zt_count": 4, "flow_yi": 194.0, "promote_rate": 30.0},
            "医疗服务": {"zt_count": 12, "flow_yi": 30.0, "promote_rate": 45.0},
            "装修装饰": {"zt_count": 1, "flow_yi": -5.0, "promote_rate": 20.0},
        },
    ):
        print(json.dumps(r, ensure_ascii=False))
