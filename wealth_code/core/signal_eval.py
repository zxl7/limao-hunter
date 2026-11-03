#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
竞价验证（次日 9:25 实时报价 -> 买入决策）——财富密码精华·抽取移植版

来源：quant-review/scripts/build_stock_research_backtest.py::_evaluate_realtime_signal
移植：狸猫猎手/wealth_code/core/signal_eval.py
说明：纯函数，仅依赖标准库。把"龙头接力"的入场判定从原 2770 行文件里
      抽取出来，输入干净的 record + quote，输出 super/expected/reject 三档。

判定口径（2026-08-12 调整）：
- 可买·红(buy/super|expected)：竞价涨幅达标 + 量能≥目标×80%（且≥0.08亿）+ 涨幅≤5%
- 观察·黄(pending)：涨幅量能达标但 gap>5%（高开过猛，等承接确认）
- 不达标·灰(reject)：涨幅或量能不达标
- 成交额80%容忍度 + super→expected回退（量能差一点降档检查）
"""

from __future__ import annotations

from typing import Any

# 成交额容忍度：实际竞价额 >= 目标线 * 80% 即视为达标
# 2026-08-18 下调 0.8→0.7（用户「量能区间再往下放一点」）：符合预期但量能差一点的
# 龙头（如金螳螂 1.36亿/线1.90亿）不再被硬拒；量能绝对底线 AMOUNT_FLOOR_YI 仍生效防无量假信号。
AMOUNT_TOLERANCE = 0.7
# 成交额绝对底线（亿元）：低于此值一律不买（防微盘无量假信号）
AMOUNT_FLOOR_YI = 0.08

# 高开过猛阈值：竞价涨幅超过该值 → 降级为"观察(黄)"，不直接买入(红)
CAUTION_GAP_PCT = 5.0


def _sector_support_suffix(sector_support: dict[str, Any] | None) -> str:
    """
    板块承接力标注后缀（2026-08-24 用户「板块无承接·孤军高开被砸」思想落地）：
    - 板块有红开助攻 → 附「🧲板块助攻N家红开」（增强买入置信）
    - 板块孤军且 0 红开 → 附「🧲孤军·板块0红开」（警示，配合 lone_high_open 降级）
    - 无数据 → 空（不编造）
    """
    if not sector_support:
        return ""
    level = str(sector_support.get("support_level") or "")
    label = str(sector_support.get("label") or "")
    if level == "support_ok":
        return f"（{label}）"
    if level == "lone_no_support":
        return f"（{label}）"
    return ""


def _theme_linkage_gate(
    base: dict[str, Any],
    record: dict[str, Any],
    sector_support: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """板块联动纪律门：低板确认独苗不能因量价达标而直接买入。

    只消费盘后冻结事实与同方向竞价承接；数据不足是 unknown，不等于板块没人。
    off/annotate 只留痕，soft（默认）拦 1~2 板独苗，strict 才扩到高标。
    """
    try:
        from core.theme_linkage import apply_auction_gate
        discipline = apply_auction_gate(record, sector_support)
    except Exception:
        return None
    if not discipline.get("block"):
        return None
    return {
        **base,
        "decision_status": "pending",
        "decision_label": "观察",
        "signal_status": "pending",
        "signal_label": "板块联动待确认",
        "rule_text": "",
        "linkage_discipline": discipline,
        "note": f"{discipline.get('note') or '板块联动待确认'}，不把单股竞价达标直接当成买点。",
    }


def _lone_high_open_gate(
    base: dict[str, Any],
    sector_support: dict[str, Any] | None,
    gap_pct: float | None,
    *,
    lone_high_open_gap: float = 3.0,
) -> dict[str, Any] | None:
    """
    孤军高开门控（2026-08-24 用户思想·金健米业实证）：
    个股竞价高开(≥3%) + 板块孤军 + 板块 0 红开 → 高开无承接，被砸风险大。
    裁决：buy → 降级 pending「孤军高开·防被砸」，绝不直接买入（等板块红开助攻）。
    非孤军 / 板块有红开 / 个股未高开 / 无数据 → 返回 None（不拦截，走原逻辑）。
    """
    from core.sector_support import is_lone_high_open  # 局部导入避免循环依赖

    if not is_lone_high_open(sector_support, gap_pct, lone_high_open_gap=lone_high_open_gap):
        return None
    sup = sector_support or {}
    return {
        **base,
        "decision_status": "pending",
        "decision_label": "观察",
        "signal_status": "pending",
        "signal_label": "孤军高开·防被砸",
        "rule_text": "",
        "note": (
            f"竞价涨幅 {gap_pct:+.2f}% 高开，但板块零承接——{(sup.get('note') or '同板块无红开').rstrip('。')}。"
            f"孤军高开大概率被砸（金健米业 8/21 实证：高开+9.19%→收-4.65%），不追高，"
            f"等板块红开助攻再考虑。"
        ),
    }


def evaluate_realtime_signal(
    record: dict[str, Any],
    quote: dict[str, Any] | None,
    *,
    caution_gap_pct: float = CAUTION_GAP_PCT,
    regime: str | None = None,
    sector_support: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    次日 9:25 竞价验证 -> 买入决策。

    入参：
    - record: 候选记录，关键字段：
        * expectation: {expected_range:(lo,hi), super_gap_min, auction_amount_min_yi,
                        expected_text, super_text, low_text}
        * 其余透传字段（code/name/relay_rank/bucket/...）会原样回写
    - quote: 9:25 实时报价，字段：
        * prev_close: 昨收
        * auction_price: 竞价价（开盘价）
        * auction_amount_yi: 竞价成交额（亿元）
        * time: 报价时间（可选）
    - caution_gap_pct: 高开谨慎阈值，默认 5%
    - regime: 情绪体制（risk_on/neutral/risk_off/ice）。**ice=冰点禁买硬开关（P1-6）**：
      **命中判定照算**（符合预期/超预期档位 signal_status 保留），但**一律不进场**——
      由调用方（pipeline_verify._build_record）将 buy 档改标为 hit（can_enter=False，不写持仓）；
      2026-08-19 用户澄清：冰点期仍要推「哪些票被命中」，只是不买。

    返回：含 decision_status / signal_status / note / open_check 等价字段的字典
    """
    exp = record.get("expectation") or {}
    expected_range = exp.get("expected_range")
    super_gap_min = exp.get("super_gap_min")
    # 量价双线：expected 用预期线、super 用更足的超预期线（昨量比例动态计算）
    auction_amount_min_yi = float(exp.get("auction_amount_min_yi") or 0.0)
    auction_amount_super_yi = float(exp.get("auction_amount_super_yi") or auction_amount_min_yi or 0.0)

    base: dict[str, Any] = {
        "date10": record.get("date10"),
        "code": record.get("code"),
        "name": record.get("name"),
        "bucket": record.get("bucket"),
        "bucket_label": record.get("bucket_label"),
        "placement_label": record.get("placement_label"),
        "score": record.get("score"),
        "daily_rank": int(record.get("daily_rank") or 0),
        "relay_rank": int(record.get("relay_rank") or 0),
        "watch_rank": int(record.get("watch_rank") or 0),
        "relay_selection_mode": str(record.get("relay_selection_mode") or "").strip(),
        "watch_group": str(record.get("watch_group") or "").strip(),
        "main_line": record.get("main_line"),
        "score_label": str(record.get("score_label") or "").strip(),
        "reason_text": record.get("reason_text"),
        "factor_hint": str(record.get("factor_hint") or "").strip(),
        "hit_rules": [str(x).strip() for x in (record.get("hit_rules") or []) if str(x).strip()],
        "block_reasons": [str(x).strip() for x in (record.get("block_reasons") or []) if str(x).strip()],
        "leader_factor_score": record.get("leader_factor_score"),
        "relay_factor_score": record.get("relay_factor_score"),
        "leader_philosophy_score": record.get("leader_philosophy_score"),
        "break_risk": record.get("break_risk"),
        "environment_score": record.get("environment_score"),
        "capacity_factor_score": record.get("capacity_factor_score"),
        "step_context_score": record.get("step_context_score"),
        "tide_relay_gate": record.get("tide_relay_gate"),
        "expected_text": exp.get("expected_text") or "",
        "super_text": exp.get("super_text") or "",
        "low_text": exp.get("low_text") or "",
        "quote_time": str((quote or {}).get("time") or "").strip(),
        "auction_amount_need_yi": round(auction_amount_min_yi, 2),
    }

    if not isinstance(quote, dict):
        return {
            **base,
            "decision_status": "unavailable",
            "decision_label": "报价缺失",
            "signal_status": "unavailable",
            "signal_label": "无法判断",
            "note": "未拿到 9:25 实时报价，当前不生成买入信号。",
        }

    prev_close = float(quote.get("prev_close") or 0.0)
    auction_price = float(quote.get("auction_price") or 0.0)
    auction_amount_yi = float(quote.get("auction_amount_yi") or 0.0)
    gap_pct = round((auction_price - prev_close) / prev_close * 100.0, 2) if prev_close > 0 and auction_price > 0 else None
    # 爆量高开实际判定（2026-08-12 双维度规则引擎）：实际竞价额 ≥ 爆量线且涨幅 ≥ 预期上沿
    boom_yi = float(exp.get("auction_boom_yi") or 0.0)
    exp_hi = None
    if isinstance(expected_range, (list, tuple)) and len(expected_range) == 2:
        try:
            exp_hi = float(expected_range[1])
        except (TypeError, ValueError):
            exp_hi = None
    boom_open = (
        boom_yi > 0 and auction_amount_yi >= boom_yi
        and gap_pct is not None and exp_hi is not None and gap_pct >= exp_hi
    )
    base.update(
        {
            "prev_close": round(prev_close, 2) if prev_close > 0 else None,
            "auction_price": round(auction_price, 2) if auction_price > 0 else None,
            "open_price": round(auction_price, 2) if auction_price > 0 else None,
            "close_price": None,
            "close_pct": None,
            "auction_amount_yi": round(auction_amount_yi, 2),
            "auction_boom_yi": round(boom_yi, 2),
            "boom_open": boom_open,
            "gap_pct": gap_pct,
            "trade_date10": str(record.get("trade_date10") or "").strip(),
            "next_day_status": "pending",
            "next_day_label": "尾盘收益",
            "next_day_return_pct": None,
            "hold_2d_status": "pending",
            "hold_2d_return_pct": None,
            "hold_3d_status": "pending",
            "hold_3d_return_pct": None,
        }
    )
    if prev_close <= 0 or auction_price <= 0 or auction_amount_yi <= 0:
        return {
            **base,
            "decision_status": "unavailable",
            "decision_label": "价格/量能不完整",
            "signal_status": "unavailable",
            "signal_label": "无法判断",
            "note": "9:25 价格或竞价成交额缺失，暂不纳入买入列表。",
        }

    # P1-6 冰点禁买（2026-08-17 用户定·2026-08-19 语义澄清）：
    # regime==ice（情绪分≤2.0）时**不做提前拦截**——命中判定照常计算（buy 档保留），
    # 由 pipeline_verify._build_record 将 buy 改标 hit（禁买·不进场·不写持仓）。
    # 这样冰点期卡片仍能展示「哪些票符合预期被命中」，只是不买入（用户 2026-08-19 要求）。

    super_gap_ok = super_gap_min is not None and gap_pct is not None and gap_pct >= float(super_gap_min)
    expected_ok = expected_range is not None and gap_pct is not None and expected_range[0] <= gap_pct <= expected_range[1]
    # 边界容忍：gap 略超预期上限但贴近（2026-08-17 区间收窄后同步收窄：宽容=区间宽10%且≤0.3pp，
    # 旧固定 0.5pp 在区间宽 2.0 时占 1/4 过宽，会把「真超预期」误标为符合预期贴上限），
    # 仍算"贴近预期"（避免跌入预期/super夹缝）；**大于宽容段的涨幅一律归 super（超预期）**——
    # 用户铁律 2026-08-17「大于区间的都要作为超预期，不要大于之后反被排除」。
    _near_tol = 0.3
    if expected_range is not None and isinstance(expected_range, (list, tuple)) and len(expected_range) == 2:
        _near_tol = min(0.3, max(0.1, round((float(expected_range[1]) - float(expected_range[0])) * 0.10, 2)))
    expected_near_hi = (
        expected_range is not None and gap_pct is not None
        and expected_range[1] < gap_pct <= expected_range[1] + _near_tol
        and not super_gap_ok
    )
    # 量价双线：80% 容忍度 + 绝对底线 0.08 亿（2026-08-12 放宽）
    _exp_need = auction_amount_min_yi * AMOUNT_TOLERANCE
    _super_need = auction_amount_super_yi * AMOUNT_TOLERANCE
    _floor_ok = auction_amount_yi >= AMOUNT_FLOOR_YI
    auction_exp_ok = (_floor_ok and auction_amount_yi >= _exp_need) if auction_amount_min_yi > 0 else True
    auction_super_ok = (_floor_ok and auction_amount_yi >= _super_need) if auction_amount_super_yi > 0 else True
    high_gap_caution = gap_pct is not None and gap_pct > caution_gap_pct
    # 板块联动门（2026-10-22）：昨日确认独苗 × 今早无同方向助攻。
    # soft 模式只拦 1~2 板，strict 才扩到高标；缺竞价同伴数据不会伪称“0红开”。
    _linkage_gate = _theme_linkage_gate(base, record, sector_support)
    # 孤军高开门（2026-08-24）：gap≥3% 孤军高开 + 板块0红开 → 优先于一切 buy/极端高开判定
    _lone_gate = _lone_high_open_gate(base, sector_support, gap_pct)
    _support_gate = _linkage_gate or _lone_gate

    # 超预期：涨幅达标 + 量能达标（超预期线）-> 直接买入
    if super_gap_ok and auction_super_ok:
        if _support_gate:
            return _support_gate
        if high_gap_caution:
            return {
                **base,
                "decision_status": "pending",
                "decision_label": "观察",
                "signal_status": "pending",
                "signal_label": "谨慎接力",
                "rule_text": exp.get("super_text") or "",
                "note": f"竞价涨幅 {gap_pct:+.2f}% 大于 {caution_gap_pct:.0f}% ，属于极端高开，先观察承接，不直接买入。{_sector_support_suffix(sector_support)}",
            }
        return {
            **base,
            "decision_status": "buy",
            "decision_label": "直接买入",
            "signal_status": "super",
            "signal_label": "超预期",
            "rule_text": exp.get("super_text") or "",
            "note": f"竞价涨幅 {gap_pct:+.2f}% ≥ 超预期阈值 {super_gap_min:+.2f}%，竞价成交额 {auction_amount_yi:.2f}亿 ≥ {_super_need:.2f}亿（{AMOUNT_TOLERANCE*100:.0f}%线），直接买入。{_sector_support_suffix(sector_support)}",
        }

    # 符合预期：涨幅落预期区间 + 量能达标（预期线）-> 直接买入
    # 或贴近预期上沿（gap 超 hi ≤0.5pp 且未达 super）→ 同样按符合预期买入
    if (expected_ok or expected_near_hi) and auction_exp_ok:
        if _support_gate:
            return _support_gate
        if high_gap_caution:
            return {
                **base,
                "decision_status": "pending",
                "decision_label": "观察",
                "signal_status": "pending",
                "signal_label": "谨慎接力",
                "rule_text": exp.get("expected_text") or "",
                "note": f"竞价涨幅 {gap_pct:+.2f}% 大于 {caution_gap_pct:.0f}% ，虽然符合预期，但不直接买入，先看承接。{_sector_support_suffix(sector_support)}",
            }
        near_note = "（贴上限·宽容买入）" if expected_near_hi else ""
        return {
            **base,
            "decision_status": "buy",
            "decision_label": "直接买入",
            "signal_status": "expected",
            "signal_label": "符合预期" + near_note,
            "rule_text": exp.get("expected_text") or "",
            "note": f"竞价涨幅 {gap_pct:+.2f}% 贴近预期上限 {expected_range[1]:+.2f}%，量能 {auction_amount_yi:.2f}亿 ≥ {_exp_need:.2f}亿（{AMOUNT_TOLERANCE*100:.0f}%线），按计划买入。{_sector_support_suffix(sector_support)}",
        }

    # 超预期涨幅达标但量能不达标 -> 回退检查预期线（super → expected fallback）
    if super_gap_ok and not auction_super_ok:
        if _support_gate:
            return _support_gate
        # 超线涨幅但极端高开(>5%)：无论量能，先观察不硬拒（用户2026-08-13：除非>5cm）
        if high_gap_caution:
            return {
                **base,
                "decision_status": "pending",
                "decision_label": "观察",
                "signal_status": "pending",
                "signal_label": "谨慎接力",
                "rule_text": exp.get("super_text") or "",
                "note": f"竞价涨幅 {gap_pct:+.2f}% ≥ 超预期 {super_gap_min:+.2f}% 但大于 {caution_gap_pct:.0f}%，极端高开，先观察承接，不直接买入。{_sector_support_suffix(sector_support)}",
            }
        if expected_ok and auction_exp_ok:
            # 涨幅够超预期但量能不够超线 → 降一档：若符合预期量线则按 expected 买入
            return {
                **base,
                "decision_status": "buy",
                "decision_label": "直接买入",
                "signal_status": "expected",
                "signal_label": "符合预期(量降档)",
                "rule_text": exp.get("expected_text") or "",
                "note": f"竞价涨幅 {gap_pct:+.2f}% ≥ 超预期 {super_gap_min:+.2f}%，但成交额 {auction_amount_yi:.2f}亿 < {_super_need:.2f}亿（超线）→ 降档检查：量能 {auction_amount_yi:.2f}亿 ≥ {_exp_need:.2f}亿（预期线）✓，按符合预期买入。{_sector_support_suffix(sector_support)}",
            }
        # 2026-08-17 修复（用户反馈"超预期被判不达标"）：super 线涨幅达标（≥super_min）
        # 但超线量能不足，且涨幅 > 预期上沿（真正超预期、非极端高开 ≤5%）→ 按
        # 2026-08-13 终裁口径「超预期一样给买入，除非>5cm」直接买入，
        # 量能偏弱仅备注提示不阻断——消除"涨幅超预期却落 reject"死胡同
        if expected_range and gap_pct is not None and gap_pct > float(expected_range[1]):
            weak_amt = "" if auction_exp_ok else f"（量能偏弱 {auction_amount_yi:.2f}亿 < {_exp_need:.2f}亿·仅提示）"
            return {
                **base,
                "decision_status": "buy",
                "decision_label": "直接买入",
                "signal_status": "super",
                "signal_label": "超预期" + ("" if auction_exp_ok else "·量能弱"),
                "rule_text": exp.get("super_text") or exp.get("expected_text") or "",
                "note": f"竞价涨幅 {gap_pct:+.2f}% ≥ 超预期 {super_gap_min:+.2f}%（≤{caution_gap_pct:.0f}%非极端高开），超线量能 {auction_amount_yi:.2f}亿 < {_super_need:.2f}亿 未达但按终裁口径买入{weak_amt}。{_sector_support_suffix(sector_support)}",
            }
        return {
            **base,
            "decision_status": "reject",
            "decision_label": "量能不达标",
            "signal_status": "reject",
            "signal_label": "低于预期",
            "rule_text": exp.get("low_text") or exp.get("super_text") or "",
            "note": f"竞价涨幅 {gap_pct:+.2f}% 达标，但成交额 {auction_amount_yi:.2f}亿 < {_super_need:.2f}亿（超线）且不满足预期线，量能不达标。",
        }

    # 涨幅落预期区间但量能不达标 -> 低于预期（极端高开>5% 例外，降级观察）
    if expected_ok and not auction_exp_ok:
        if _support_gate:
            return _support_gate
        if high_gap_caution:
            return {
                **base,
                "decision_status": "pending",
                "decision_label": "观察",
                "signal_status": "pending",
                "signal_label": "谨慎接力",
                "rule_text": exp.get("expected_text") or "",
                "note": f"竞价涨幅 {gap_pct:+.2f}% > {caution_gap_pct:.0f}%（极端高开），量能不足，先观察承接。{_sector_support_suffix(sector_support)}",
            }
        return {
            **base,
            "decision_status": "reject",
            "decision_label": "量能不达标",
            "signal_status": "reject",
            "signal_label": "低于预期",
            "rule_text": exp.get("low_text") or exp.get("expected_text") or "",
            "note": f"竞价涨幅 {gap_pct:+.2f}% 虽落预期区间，但成交额 {auction_amount_yi:.2f}亿 < {_exp_need:.2f}亿（{AMOUNT_TOLERANCE*100:.0f}%线），量能不达标。",
        }

    # 高于预期上限但未达超线（夹缝区）：用户2026-08-13终裁口径=「超预期一样给买入，除非>5cm」，
    # 即价格超过预期上限、且涨幅≤5% 即直接买入；量能偏弱仅作备注提示，不阻断买入（量能门槛已从本档移除）。
    if (expected_range and gap_pct is not None and gap_pct > float(expected_range[1])
            and (super_gap_min is None or gap_pct < float(super_gap_min))):
        if _support_gate:
            return _support_gate
        if high_gap_caution:
            return {
                **base,
                "decision_status": "pending",
                "decision_label": "观察",
                "signal_status": "pending",
                "signal_label": "谨慎接力",
                "rule_text": exp.get("expected_text") or "",
                "note": f"竞价涨幅 {gap_pct:+.2f}% 大于 {caution_gap_pct:.0f}%，极端高开，先观察承接，不直接买入。{_sector_support_suffix(sector_support)}",
            }
        weak_amt = "" if auction_exp_ok else f"（量能偏弱 {auction_amount_yi:.2f}亿 < {_exp_need:.2f}亿·仅提示）"
        return {
            **base,
            "decision_status": "buy",
            "decision_label": "直接买入",
            "signal_status": "super",
            "signal_label": "超预期" + ("" if auction_exp_ok else "·量能弱"),
            "rule_text": exp.get("expected_text") or "",
            "note": f"竞价涨幅 {gap_pct:+.2f}% 高于预期上限 {expected_range[1]:+.2f}%，直接买入{weak_amt}。{_sector_support_suffix(sector_support)}",
        }

    # 微红·弱于预期但仍有溢价（涨停票红开即非硬伤）：谨慎观察而非硬拒
    if gap_pct is not None and expected_range and 0 < gap_pct < float(expected_range[0]):
        return {
            **base,
            "decision_status": "pending",
            "decision_label": "观察",
            "signal_status": "pending",
            "signal_label": "微红偏弱",
            "rule_text": exp.get("expected_text") or "",
            "note": f"竞价涨幅 {gap_pct:+.2f}% 微红但弱于预期下沿 {expected_range[0]:+.2f}%，仍有溢价；结合量能与板块观察，不急于买。",
        }

    # 涨幅不达标 -> 低于预期
    return {
        **base,
        "decision_status": "reject",
        "decision_label": "未达买点",
        "signal_status": "reject",
        "signal_label": "低于预期",
        "rule_text": exp.get("low_text") or exp.get("expected_text") or "",
        "note": f"竞价涨幅 {gap_pct:+.2f}%，未落在预期区间或超预期范围。",
    }
