"""盘后候选的次日反馈：复用已发布行情，给终端提供同日对齐的只读快照。"""
from __future__ import annotations

from pathlib import Path
from statistics import mean, median

from common.json_io import load_json, save_json
from common.trading_calendar import next_a_share_trading_day

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
WEALTH_CACHE = ROOT / "wealth_code" / "cache"


def _auction_gap(quote: dict, trade_date: str) -> float | None:
    # 报价来源日和冻结时点必须同时匹配；文件名不能证明报价是今日竞价。
    stamp = str(quote.get("time") or "")
    if stamp[:10] != trade_date or not ("09:25" <= stamp[11:16] <= "09:30"):
        return None
    price, close = quote.get("auction_price"), quote.get("prev_close")
    if not isinstance(price, (int, float)) or not isinstance(close, (int, float)) or price <= 0 or close <= 0:
        return None
    return round((price / close - 1) * 100, 2)


def _prior_failed_codes(prev_date: str) -> set[str] | None:
    files = sorted((CACHE / "zt_history").glob("zt_*.json"))
    earlier = [p for p in files if p.stem[3:] < prev_date]
    if not earlier:
        return None
    prior = load_json(earlier[-1])
    current = load_json(CACHE / "zt_history" / f"zt_{prev_date}.json")
    if (not isinstance(prior, dict) or not isinstance(current, dict)
            or next_a_share_trading_day(str(prior.get("date") or "")) != prev_date):
        return None
    # 只用两个完整收盘池确认“前日连板、昨日断板”，缺口时不推断反包。
    for day in (prior, current):
        rows = day.get("zt_list")
        stamp = str(day.get("generated_ts") or "")
        if not isinstance(rows, list) or day.get("zt_count") != len(rows) or stamp[:10] != day.get("date") or stamp[11:16] < "15:00":
            return None
    current_codes = {str(row["code"]) for row in current["zt_list"]}
    return {str(row["code"]) for row in prior["zt_list"]
            if int(row.get("boards") or 0) >= 2 and str(row["code"]) not in current_codes}


def _progression_stage(codes: set[str], promoted: set[str], gaps: dict[str, float],
                       nuke_codes: set[str]) -> dict:
    """同一批昨日板数样本的晋级、竞价溢价和失败核按钮，供次日同路径预案参考。"""
    failed = codes - promoted
    values = [gaps[code] for code in codes if code in gaps]
    quoted = len(values)
    rate = round(len(codes & promoted) / len(codes) * 100, 1) if codes else None
    premium = round(mean(values), 2) if values else None
    failed_nukes = len(failed & nuke_codes)
    # 小样本或竞价缺报只展示事实，不将其转为选股/买入门控。
    reliable = len(codes) >= 3 and quoted == len(codes)
    if reliable and rate >= 50 and premium > 0 and failed_nukes <= 1:
        mood = "supportive"
    elif reliable and rate < 40 and premium < 0 and failed_nukes >= 1:
        mood = "pressured"
    else:
        mood = "mixed" if reliable else "insufficient"
    return {"sample_count": len(codes), "promote_count": len(codes & promoted),
            "promote_rate": rate, "auction_quoted": quoted,
            "auction_premium_avg": premium, "failed_count": len(failed),
            "failed_nuke_count": failed_nukes, "mood": mood}


def build(feedback: dict, candidates: dict, auction: dict) -> dict | None:
    date = str(feedback.get("date") or "")
    prev_date = str(feedback.get("prev_date") or "")
    if (feedback.get("error") or not date or candidates.get("date") != prev_date
            or not isinstance(feedback.get("today_zt_codes"), list)
            or not isinstance(feedback.get("promote_codes"), list)
            or not isinstance(feedback.get("quotes"), dict)):
        return None
    prev_pool = load_json(CACHE / "zt_history" / f"zt_{prev_date}.json")
    if (not isinstance(prev_pool, dict) or prev_pool.get("date") != prev_date
            or not isinstance(prev_pool.get("zt_list"), list)
            or prev_pool.get("zt_count") != len(prev_pool["zt_list"])
            or str(prev_pool.get("generated_ts") or "")[:10] != prev_date
            or str(prev_pool.get("generated_ts") or "")[11:16] < "15:00"):
        return None
    lb_codes = {str(row["code"]) for row in prev_pool.get("zt_list", [])
                if int(row.get("boards") or 0) >= 2}
    if feedback.get("lb_prev_count") != len(lb_codes):
        return None
    promoted = set(feedback["promote_codes"])
    today_zt = set(feedback["today_zt_codes"])
    quotes = feedback["quotes"]
    nuke_codes = {str(row["code"]) for row in feedback["nuke_list"]}
    failed = lb_codes - promoted
    candidate_codes = {str(row["code"]) for bucket in ("relay", "watch") for row in candidates[bucket]}
    gaps = {code: gap for code in lb_codes | candidate_codes
            if (gap := _auction_gap(auction.get(code) or {}, date)) is not None}
    failed_nukes = failed & nuke_codes
    prior_failed = _prior_failed_codes(prev_date)
    repair_codes = sorted(prior_failed & today_zt) if prior_failed is not None else None
    lb_premiums = [gap for code, gap in gaps.items() if code in lb_codes]
    promoted_premiums = [gap for code, gap in gaps.items() if code in lb_codes & promoted]
    failed_premiums = [gap for code, gap in gaps.items() if code in failed]
    one_codes = {str(row["code"]) for row in prev_pool["zt_list"] if int(row.get("boards") or 0) == 1}
    two_codes = {str(row["code"]) for row in prev_pool["zt_list"] if int(row.get("boards") or 0) == 2}
    high_codes = lb_codes - two_codes
    repair_sample = len(prior_failed) if prior_failed is not None else None
    repair_rate = round(len(repair_codes) / repair_sample * 100, 1) if repair_sample else None
    # N+N 的二波入口只认前段连板、昨日断板、今日回封；后续连板由候选历史路径确认。
    if repair_sample is not None and repair_sample >= 3:
        repair_mood = "supportive" if repair_rate >= 40 else "pressured" if repair_rate == 0 else "mixed"
    else:
        repair_mood = "insufficient"
    stages = {
        "one_to_two": _progression_stage(one_codes, promoted, gaps, nuke_codes),
        "two_to_three": _progression_stage(two_codes, promoted, gaps, nuke_codes),
        "high_relay": _progression_stage(high_codes, promoted, gaps, nuke_codes),
        "n_plus_n": {"sample_count": repair_sample, "repair_count": len(repair_codes) if repair_codes is not None else None,
                     "repair_rate": repair_rate, "mood": repair_mood},
    }
    market = {
        "sample_count": len(lb_codes),
        "promote_count": int(feedback["lb_promote_count"]),
        "promote_rate": feedback["lb_promote_rate"],
        "auction_quoted": len(lb_premiums),
        "auction_premium_avg": round(mean(lb_premiums), 2) if lb_premiums else None,
        "auction_premium_median": round(median(lb_premiums), 2) if lb_premiums else None,
        "promoted_auction_premium_avg": round(mean(promoted_premiums), 2) if promoted_premiums else None,
        "failed_auction_premium_avg": round(mean(failed_premiums), 2) if failed_premiums else None,
        "failed_count": len(failed),
        "failed_nuke_count": len(failed_nukes),
        "failed_nuke_codes": sorted(failed_nukes),
        "repair_sample_count": len(prior_failed) if prior_failed is not None else None,
        "repair_count": len(repair_codes) if repair_codes is not None else None,
        "repair_codes": repair_codes,
        "confirmed_limit_down_count": None,  # 无逐票可靠跌停代码源，不把跌幅<-5%冒充跌停。
        "stages": stages,
    }
    # 连板晋级低于弱势线、竞价整体负溢价，且失败核按钮集中而无反包修复时才收窄。
    # 竞价必须覆盖全部连板样本，避免选择性缺报造成负溢价错判。
    market["pressure"] = bool(
        market["sample_count"] and market["auction_quoted"] == market["sample_count"]
        and market["repair_sample_count"]
        and market["promote_rate"] < 40
        and market["auction_premium_avg"] < 0
        and market["failed_nuke_count"] >= 3
        and market["repair_count"] == 0
    )
    rows = []
    for bucket in ("relay", "watch"):
        for row in candidates[bucket]:
            code = str(row["code"])
            quote = quotes.get(code) or {}
            quote_at = str(quote.get("quote_at") or "")
            rows.append({
                "code": code, "name": row["name"], "bucket": bucket,
                "rank": row.get("relay_rank") if bucket == "relay" else row.get("watch_rank"),
                "boards": row["boards"], "raw_score": row.get("raw_score"),
                "auction_gap_pct": gaps.get(code),
                "current_pct": quote.get("pct") if quote_at[:10] == date else None,
                "quote_at": quote_at if quote_at[:10] == date else None,
                "promoted": code in promoted,
                "nuke": code in nuke_codes,
            })
    return {"date": date, "prev_date": prev_date, "generated_ts": feedback["generated_ts"],
            "market": market, "candidates": rows}


def publish(feedback: dict) -> bool:
    prev_date = str(feedback.get("prev_date") or "")
    candidates = load_json(WEALTH_CACHE / f"candidates-{prev_date}.json")
    auction = load_json(WEALTH_CACHE / f"auction_quotes-{feedback.get('date')}.json")
    if not isinstance(candidates, dict):
        return False
    # 竞价文件缺失仍发布真实的晋级/负反馈；竞价溢价保持未知。
    result = build(feedback, candidates, auction if isinstance(auction, dict) else {})
    return save_json(CACHE / "selection_effect.json", result) if result is not None else False
