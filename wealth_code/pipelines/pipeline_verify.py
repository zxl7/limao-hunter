#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
次日验证流水线：竞价验证 → 构建结算 payload → 净值 + 指标记账

这是财富密码接入狸猫猎手的“真实交易结算”一端，把盘后候选跑成可统计的真实战绩：
1. 读盘后候选（cache/candidates-*.json）
2. 读次日 9:25 竞价快照（adapters/input_auction）
3. 逐票 signal_eval → open_check（super/expected/pending/reject）
4. 用隔日收盘价算 next_day 收益
5. 组装结算 payload → nav_ledger（净值曲线）+ strategy_metrics（胜率/收益）
6. 输出 cache/account_nav_history.jsonl + account_strategy_metrics.json

注意（口径诚实）：
- 只有 can_enter=True 且 next_day 有收盘价的样本才进入净值
- 高开>5% 自动转 pending（不直接买入）
- hold_2d/hold_3d 默认置 pending（不在主口径计算），不影响 next_day 主口径
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.signal_eval import evaluate_realtime_signal
from core import ai_caliber
from core.nav_ledger import sync_account_nav_ledger
from core.strategy_metrics import build_account_strategy_metrics, sync_account_strategy_metrics
from adapters.input_auction import load_auction_quotes

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
# 持仓展示文件 holdings.json 的权威位置：项目根 cache/（与 ma5_check.py / gen_dashboard.py 共用，
# 非 wealth_code/cache）。ROOT=wealth_code → ROOT.parent=狸猫猎手（项目根）。
HOLDINGS_PATH = ROOT.parent / "cache" / "holdings.json"
BJ_TZ = timezone(timedelta(hours=8))


def _next_day(date10: str) -> str:
    """
    次一交易日（最小日历修正：跳过周六/周日，保证 trade_date 落在交易日）。
    注意：法定节假日未覆盖（无交易日历），长假仍会错位；周末是最常见场景，先修掉。
    """
    try:
        d = datetime.strptime(date10, "%Y-%m-%d") + timedelta(days=1)
        while d.weekday() >= 5:  # 5=周六 6=周日 → 跳到周一
            d += timedelta(days=1)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return date10


def _build_record(cand: dict[str, Any], quote: dict[str, Any] | None, next_close: float | None, *, date10: str, regime: str | None = None, sector_support: dict[str, Any] | None = None, ai_decisions: dict[str, dict] | None = None) -> dict[str, Any]:
    """
    构建单条结算 record（含 performance.open_check / next_day）。
    regime: 情绪体制（P1-6 冰点禁买硬开关·signal_eval 消费）。
    sector_support: 板块竞价承接力（2026-08-24 用户思想·孤军高开门控）——
      由 cmd_verify 抓同板块成员竞价算好传入；signal_eval 据此将
      「孤军高开+板块0红开」的 buy 降级 pending（防被砸），卡片渲染🧲标注。
    ai_decisions: 该执行日的 AI 决策映射 {code: 决策行}（2026-09-14 用户「全量以 AI 为唯一口径」）。
      **非 None** → 启用 AI 口径：can_enter 由 AI 判定（participate ∧ 竞价≤自定上限），
      机械 signal_eval 结果保留为参考信号（mech_can_enter / decision_status 字段）。
      **None** → 该日无 AI 决策（缺失·非空仓）→ 回退机械口径，caliber 标 mechanical_fallback。
    """
    signal = evaluate_realtime_signal(cand, quote, regime=regime, sector_support=sector_support)
    decision = signal.get("decision_status")
    sig_status = signal.get("signal_status")

    # P1-6 冰点禁买·命中打标（2026-08-19 用户澄清）：regime=ice 时命中判定照算
    # （signal_eval 已正常产出 buy 档），但一律不进场——buy → hit（can_enter=False、
    # 不写持仓），卡片仍展示命中票+禁买标注；pending/reject/unavailable 保持原样。
    if regime == "ice" and decision == "buy":
        decision = "hit"
        signal = {
            **signal,
            "decision_status": "hit",
            "decision_label": "冰点禁买·命中",
            "ice_ban": True,
            "note": (str(signal.get("note") or "").rstrip("。")
                     + f" ⛔冰点禁买（情绪≤2.0）：{sig_status}命中但一律不进场，空仓等待情绪修复。"),
        }

    if decision == "buy":
        can_enter = True
        open_status = sig_status  # super / expected
    elif decision == "hit":
        can_enter = False
        open_status = sig_status  # super / expected（命中档位保留，仅禁买不进场）
    elif decision == "pending":
        can_enter = False
        open_status = "pending"
    else:  # reject / unavailable
        can_enter = False
        open_status = sig_status if sig_status else "reject"

    # ── 🤖 AI 口径覆盖（2026-09-14 用户「全量以 AI 为唯一口径！」）──────────────
    # 成交集合与入场规则改由 AI 盘后决策决定（core/ai_caliber）：
    #   AI 判 participate ∧ 竞价涨幅 ≤ AI 自定 entry_max_gap ∧ 有竞价数据 → 成交
    # 机械 signal_eval 结果**保留为参考信号**（decision_status / mech_can_enter 照常写入，
    # 卡面「✅ 竞价达标·机械参考」段照常渲染），但不再决定买卖与净值战绩。
    # AI 决策缺失（该执行日无任何决策行）→ 回退机械口径 + caliber 标注（诚实降级，不静默空仓）。
    mech_can_enter = bool(can_enter)
    mech_decision = decision
    caliber = "mechanical"
    ai_meta: dict[str, Any] = {}
    if ai_caliber.AI_CALIBER_ACCOUNT:
        if ai_decisions is None:
            caliber = "mechanical_fallback"
            ai_meta = {"caliber": caliber,
                       "caliber_note": "该执行日无 AI 决策行 → 诚实回退机械口径（勿读成 AI 空仓）"}
        else:
            caliber = "ai"
            _gap = signal.get("gap_pct")
            _gap = float(_gap) if isinstance(_gap, (int, float)) else None
            # 方案 A（2026-09-17）：把候选的**盘后预期区间**一并传入 → 账本执行
            # 「参与 ∧ ≤5% ∧ 落预期区间」三步裁决，与卡面动态结论同源（gap_zone）。
            can_enter, _ai_note, ai_meta = ai_caliber.decide(
                ai_decisions.get(str(cand.get("code") or "").strip()), _gap,
                expected_range=(cand.get("expectation") or {}).get("expected_range"))
            ai_meta["caliber"] = caliber
            ai_meta["ai_note"] = _ai_note
            ai_meta["caliber_changed"] = bool(can_enter != mech_can_enter)
            if regime == "ice":
                # 冰点禁买（P1-6）在本层不硬拦 AI 决策（AI 判断时已知情绪分·自会收敛），
                # 但打标留痕：卡面/审计可追溯「冰点期 AI 仍参与」。
                ai_meta["ice_warn"] = "⚠️冰点期（做多气氛≤2分）·AI 仍判参与"
    # 隔日收益
    if can_enter and next_close is not None and quote is not None:
        auction_price = float(quote.get("auction_price") or 0.0)
        return_pct = round((next_close - auction_price) / auction_price * 100.0, 2) if auction_price > 0 else None
        next_day_status = "covered" if return_pct is not None else "pending"
    else:
        return_pct = None
        next_day_status = "skipped" if can_enter is False else "pending"

    # 组装验证记录（仅含竞价验证所需字段，不附加选股卡样式数据）
    _rec = {
        "date10": date10,
        "trade_date10": _next_day(date10),
        "code": cand.get("code"),
        "name": cand.get("name"),
        "bucket": cand.get("bucket"),
        "bucket_label": cand.get("bucket_label"),
        "relay_rank": int(cand.get("relay_rank") or 0),
        "watch_rank": int(cand.get("watch_rank") or 0),
        "relay_selection_mode": str(cand.get("relay_selection_mode") or "").strip(),
        "watch_group": str(cand.get("watch_group") or "").strip(),
        "boards": cand.get("boards"),
        "is_20cm": cand.get("is_20cm"),
        "score": cand.get("score"),
        "leader_factor_score": (cand.get("reason") or {}).get("ignition", {}).get("oneWordScore"),
        "main_line": cand.get("theme"),
        # 核心炒作题材双字段（2026-08-17 用户·verify 卡开仓决策显示）
        # eod 候选已回填 industry_sector(行业归属)+core_theme(当前炒作题材)，verify 卡渲染
        # _theme_dual_tag(r) 需要这两个字段——透传，缺省回退 theme（诚实降级）
        "industry_sector": cand.get("industry_sector") or cand.get("theme"),
        "core_theme": cand.get("core_theme"),
        "factor_hint": " ".join(cand.get("tags") or []),
        "expectation": cand.get("expectation") or {},
        "narrative": cand.get("narrative"),
        # 板块竞价承接力透传（2026-08-24）：供 verify 卡 🧲板块承接 标注
        "sector_support": sector_support,
        # 题材板块效应透传（2026-08-25 用户「孤军判定要全局化·结合题材板块效应」）：
        # eod 候选已算 theme_name/theme_mates_cnt/theme_has_sector_effect（行业→题材聚合），
        # verify 卡 _sector_support_tag 用它渲染「🧲题材板块效应·N家涨停」替代孤军误判。
        "theme_name": cand.get("theme_name"),
        "theme_mates_cnt": cand.get("theme_mates_cnt"),
        "theme_has_sector_effect": cand.get("theme_has_sector_effect"),
        "theme_is_lone": cand.get("theme_is_lone"),
        "performance": {
            "open_check": {
                "can_enter": can_enter,
                "status": open_status,
                "decision_status": decision,
                # 🤖 口径留痕（2026-09-14 用户「全量以 AI 为唯一口径」）：
                # caliber = ai / mechanical / mechanical_fallback；mech_can_enter = 机械口径结论
                # （卡面「✅ 竞价达标·机械参考」段按此筛选，与账本口径解耦）。
                "caliber": caliber,
                "mech_can_enter": mech_can_enter,
                "mech_decision_status": mech_decision,
                "caliber_note": ai_meta.get("caliber_note"),
                "ai_view": ai_meta.get("ai_view"),
                "ai_gap_limit": ai_meta.get("ai_gap_limit"),
                # AI 自定上限（仅留痕）：2026-09-14 起**不参与裁决**，生效值恒为用户 5% 硬线
                "ai_declared_limit": ai_meta.get("ai_declared_limit"),
                # 预期区间门判定（2026-09-17「A 计划」）：below/ok/above/unknown（留痕·供审计）
                "zone": ai_meta.get("zone"),
                "ai_note": ai_meta.get("ai_note"),
                "caliber_changed": ai_meta.get("caliber_changed"),
                "note": signal.get("note"),
                "gap_pct": signal.get("gap_pct"),
                "auction_price": signal.get("auction_price"),
                "prev_close": signal.get("prev_close"),
                # 竞价量能对比（E项）：实际竞价成交额 vs 预期量能线（昨量比例/下限兜底后）
                "auction_amount_yi": signal.get("auction_amount_yi"),
                "auction_amount_need_yi": signal.get("auction_amount_need_yi"),
            },
            "next_day": {
                "status": next_day_status,
                "return_pct": return_pct,
                "entry_date": _next_day(date10),
                "exit_date": _next_day(date10),
            },
            "hold_2d": {"status": "pending", "return_pct": None},
            "hold_3d": {"status": "pending", "return_pct": None},
        },
    }
    return _rec


def run_verify(
    candidates: dict[str, Any] | None = None,
    quotes: dict[str, dict[str, Any]] | None = None,
    next_close_map: dict[str, float] | None = None,
    *,
    candidates_path: Path | None = None,
    quotes_path: Path | None = None,
    out_nav: bool = True,
    persist: bool = True,
    sector_support_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    运行次日验证。candidates/quotes 可直接传入（便于测试），也可从文件加载。

    persist=False 时只计算不落盘（不写净值/指标）——供 --synthesize dry-run 使用，
    防止合成测试数据污染真实交易战绩 cache（2026-08-12 修复）。

    sector_support_map（2026-08-24）：{code: 板块承接力 dict}——由 cmd_verify --fetch
    抓同板块成员竞价后经 core/sector_support.compute_support 算出；喂给 signal_eval
    做「孤军高开 + 板块0红开 → 买降级观察」裁决。
    """
    if candidates is None:
        cp = candidates_path or _latest_candidates()
        if cp is None or not cp.exists():
            raise FileNotFoundError("未找到盘后候选文件（先跑 pipeline_eod）")
        candidates = json.loads(cp.read_text(encoding="utf-8"))

    if quotes is None:
        quotes = load_auction_quotes(quotes_path) if quotes_path else {}

    date10 = str(candidates.get("date") or "").strip()
    all_cands = list(candidates.get("relay") or []) + list(candidates.get("watch") or [])
    # P1-6 冰点禁买：regime 从 candidates.environment 读取（eod 时已按 feedback_matrix 判定；
    # ice=情绪分≤2.0 → signal_eval 全部禁买）。兼容旧文件无 regime 字段（默认 None 不触发）。
    regime = str((candidates.get("environment") or {}).get("regime") or "").strip() or None

    # 🤖 AI 口径（2026-09-14 用户「全量以 AI 为唯一口径！」）：
    # AI 决策在**前一交易日盘后**写死（ai_decisions.jsonl·trade_date=本期执行日），
    # 本函数只做「按 AI 自定上限判定能否成交」，不引入当日新信息。
    trade_date10 = _next_day(date10)
    ai_decisions: dict[str, dict] | None = None
    if ai_caliber.AI_CALIBER_ACCOUNT:
        ai_decisions = ai_caliber.load_ai_decisions(trade_date10)
        if ai_decisions is None:
            print(f"[VERIFY] ⚠️ {trade_date10} 无 AI 决策行（ai_decisions.jsonl）→ "
                  f"本批按【机械口径】结算（caliber=mechanical_fallback·勿读成 AI 空仓）")
        else:
            print(f"[VERIFY] 🤖 AI 口径生效：{trade_date10} 决策 {len(ai_decisions)} 只"
                  f"（成交集合与入场上限以 AI 为准）")

    records: list[dict[str, Any]] = []
    for cand in all_cands:
        code = str(cand.get("code") or "").strip()
        q = quotes.get(code)
        nc = (next_close_map or {}).get(code)
        rec = _build_record(cand, q, nc, date10=date10, regime=regime,
                            sector_support=(sector_support_map or {}).get(code),
                            ai_decisions=ai_decisions)
        records.append(rec)

    payload = {
        "schema": "stockResearchBacktest",
        "meta": {"generated_at_bj": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")},
        "records": records,
        # 口径标注（2026-09-14）：ai / mechanical / mechanical_fallback
        "caliber": ("ai" if ai_decisions else
                    ("mechanical_fallback" if ai_caliber.AI_CALIBER_ACCOUNT else "mechanical")),
        # 环境透传（2026-08-16）：feedback 10 分制情绪，供卡片渲染层做规则层风险标记
        "environment": candidates.get("environment"),
    }

    nav_rows = sync_account_nav_ledger(backtest_payload=payload) if (out_nav and persist) else []
    metrics = (
        sync_account_strategy_metrics(backtest_payload=payload)
        if persist
        else build_account_strategy_metrics(backtest_payload=payload)
    )

    # 统计本次
    buys = [r for r in records if r["performance"]["open_check"]["can_enter"]]
    mech_buys = [r for r in records if r["performance"]["open_check"].get("mech_can_enter")]
    covered = [r for r in records if r["performance"]["next_day"]["status"] == "covered"]
    # 冰点命中数（2026-08-19 用户澄清）：ice 期 buy→hit 不进场，但命中票照常统计供卡片展示
    hits = [r for r in records if r["performance"]["open_check"]["decision_status"] == "hit"]
    _caliber = ("ai" if ai_decisions else
                ("mechanical_fallback" if ai_caliber.AI_CALIBER_ACCOUNT else "mechanical"))
    summary = {
        "date10": date10,
        "trade_date10": trade_date10,
        "total_candidates": len(records),
        "buy_signals": len(buys),           # 🤖 口径化：= 实际可进场只数（AI 口径下即 AI 实买）
        "mech_buy_signals": len(mech_buys),  # 机械参考只数（卡面「机械达标 N 只」）
        "caliber": _caliber,
        "ai_decisions": len(ai_decisions) if ai_decisions else 0,
        "caliber_switched": sum(1 for r in records
                                if r["performance"]["open_check"].get("caliber_changed")),
        "hit_signals": len(hits),
        "covered": len(covered),
        "nav_latest": nav_rows[-1]["nav"] if nav_rows else None,
        "nav_rows": len(nav_rows),
    }
    return {
        "payload": payload,
        "summary": summary,
        "metrics": metrics,
        "nav_rows": nav_rows,
        # 盘后卡写入的全局旁白随候选文件透传，使早盘验证卡 🗣️ 与盘后卡一致（无需重传 --narratives）
        "global_narrative": candidates.get("global_narrative"),
    }


def _latest_candidates() -> Path | None:
    files = sorted(CACHE_DIR.glob("candidates-*.json"))
    return files[-1] if files else None


def sync_holdings_from_verify(res: dict[str, Any], date10: str) -> dict[str, Any]:
    """
    把 verify 结果中的买入信号（open_check.can_enter=True）写入 cache/holdings.json
    （未结算持仓），供看板「当前持仓（未结算）」区块展示。

    ⚠️ 口径（2026-09-14 用户「全量以 AI 为唯一口径」）：can_enter 已由 core/ai_caliber 接管
    （AI 判 participate ∧ 竞价 ≤ AI 自定上限），故本函数自动同步的就是 **AI 实际买入的票**，
    与净值账本同源；机械达标票不再进持仓。

    仅真实买入运行调用（cmd_verify 非 --synthesize）；测试/合成（persist=False）不调用，
    避免合成数据污染真实持仓展示。持仓代表「当前未结算敞口」，结算后由 clear_holdings 清空。
    """
    payload_records = (res.get("payload") or {}).get("records") or []
    holdings: list[dict[str, Any]] = []
    for r in payload_records:
        oc = (r.get("performance") or {}).get("open_check") or {}
        if not oc.get("can_enter"):
            continue
        ap = oc.get("auction_price") or 0.0
        pc = oc.get("prev_close") or 0.0
        auction_pct = round((ap / pc - 1) * 100.0, 2) if (pc and ap) else None
        holdings.append({
            "code": r.get("code"),
            "name": (str(r.get("name") or "")).replace(" ", ""),  # 去候选数据里的内嵌空格（如「金 螳 螂」）
            "theme": r.get("main_line"),
            "boards": r.get("boards"),
            "is_20cm": r.get("is_20cm"),
            "bucket_label": r.get("bucket_label"),
            "auction_pct": auction_pct,
            "open_status": oc.get("status"),  # super / expected
        })
    payload = {
        "updated": date10,
        "note": (
            f"{date10} 实际买入 {len(holdings)} 只（口径 = AI 决策：AI 判参与且竞价不超自定上限·"
            f"2026-09-14 起；自动同步自 verify 任务。收益待当日 15:00 盘后结算写入净值账本 "
            f"account_nav_history.jsonl）。"
        ),
        "holdings": holdings,
    }
    out = HOLDINGS_PATH
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def clear_holdings(date10: str) -> None:
    """
    批次盘后结算后清空持仓（持仓已闭合，转为净值账本历史）。
    在 cmd_eod 的 _finalize_prev_batch(closing=True) 成功结算后调用。
    """
    payload = {
        "updated": date10,
        "note": (
            f"{date10} 批次已于盘后结算，持仓清空（逐票收益见净值账本 "
            f"account_nav_history.jsonl，trade_date={date10}）。"
        ),
        "holdings": [],
    }
    out = HOLDINGS_PATH
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    res = run_verify()
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
