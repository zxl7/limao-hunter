#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_engine.py — 推测回测引擎（财神-回测 skill 执行核心 · 2026-09-01）

职责：把「系统的推测输出」与「实际走势」逐日对齐，产出可裁决的回测指标。
定位：只算数、不改参数（改参数=learning.py 的活）；只写回测产物、不碰任何生产链路。

回测对象（预测契约，全部来自已落盘结构化文件，无 AI 编造）：
  A. 候选池命中率  —— candidates-{T}.json relay/watch 名单 vs nav 账本实际收益
  B. 预期区间命中率 —— candidates expectation.expected_range（竞价涨幅区间）
                       vs 次日 auction_quotes 实际竞价涨幅
  C. 情绪分对齐     —— 盘前预测情绪分 vs 收盘实际分（summary_5d / feedback_matrix）

数据源（全部只读）：
  wealth_code/cache/candidates-{T}.json      候选池 + 预期区间
  wealth_code/cache/auction_quotes-{T+1}.json 次日竞价快照（区间命中）
  wealth_code/cache/account_nav_history.jsonl 净值账本（候选实际收益·唯一权威）
  cache/short_term/summary_5d.json           情绪分实际序列
  cache/panqian_card_*.json / fupan_card_*.json  盘前预测分文本（解析）

产出（append-only + 幂等）：
  cache/backtest_report_{date}.json   单日回测报告
  cache/backtest_history.jsonl        累积回测历史（供 learning.py 学习）

用法：
  python3 scripts/backtest_engine.py               # 回测最近一个已完成交易日
  python3 scripts/backtest_engine.py --date 2026-08-31   # 指定结算日
  python3 scripts/backtest_engine.py --dry-run     # 只看将要回测的日期与输入文件
交易守卫：非交易日/当日未收盘（<15:00）不产出，防半程数据污染。
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
CACHE = ROOT / "cache"
WC_CACHE = ROOT / "wealth_code" / "cache"
NAV_FILE = WC_CACHE / "account_nav_history.jsonl"
SUMMARY_5D = CACHE / "short_term" / "summary_5d.json"
REPORT_DIR = CACHE
HISTORY_FILE = CACHE / "backtest_history.jsonl"


def _bj_now():
    from datetime import timezone
    return datetime.now(timezone(timedelta(hours=8)))


def is_trading_day(d: date) -> bool:
    """交易日守卫：**唯一真源 = common.trading_calendar**，feedback 标记作第二道防线。

    ⛔ 2026-10-09 唯一化：原实现自判 `weekday()` + feedback error 兜底 → 工作日休市
       （2026-09-25 中秋·周五）漏网。日历判定前置后，兜底只用于防「接口回吐上一交易日池」。
    """
    try:
        from common.trading_calendar import is_a_share_trading_day
        if not is_a_share_trading_day(d):
            return False
    except Exception:  # pragma: no cover - 极端环境兜底
        if d.weekday() >= 5:  # trading-day-fallback: 唯一真源不可用时的周末兜底
            return False
    fb = CACHE / "feedback_matrix.json"
    if fb.exists():
        try:
            err = (json.loads(fb.read_text(encoding="utf-8")) or {}).get("error") or ""
            if "non_trading_day" in err:
                return False
        except Exception:
            pass
    return True


def load_nav() -> list[dict]:
    """读净值账本（trade_date 结算日唯一权威）。"""
    if not NAV_FILE.exists():
        return []
    rows = []
    for line in NAV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def load_summary5d() -> dict:
    if not SUMMARY_5D.exists():
        return {}
    try:
        return json.loads(SUMMARY_5D.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_candidates(d: str) -> dict:
    p = WC_CACHE / f"candidates-{d}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_auction(d: str) -> dict:
    p = WC_CACHE / f"auction_quotes-{d}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def find_prediction_text(d: str) -> dict:
    """从盘前卡/复盘卡 JSON 解析「预测情绪分」文本（尽力而为，解析不到返回空）。"""
    result = {}
    d_compact = d.replace("-", "")
    d_short = d[5:]  # MM-DD
    candidates = [
        f"panqian_data_{d_compact}.json",
        f"panqian_data_{d}.json",
        f"panqian_data_{d_short}.json",
        f"panqian_card_{d_compact}.json",
        f"fupan_card_{d}.json",
        f"fupan_card_{d_compact}.json",
    ]
    for pat in candidates:
        p = CACHE / pat
        if not p.exists():
            p = WC_CACHE / pat
        if not p.exists():
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            continue
        # ① 严格口径（优先）："预判 7-8/10" / "预测 5/10" / "预判 5~6/10"
        m = re.search(r"[预前][判测]\s*(\d+(?:\.\d+)?)\s*[~\-—至]\s*(\d+(?:\.\d+)?)\s*/\s*10", raw)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            result["predict_lo"], result["predict_hi"] = lo, hi
            result["predict_mid"] = round((lo + hi) / 2, 2)
            result["parse_mode"] = "strict_range"
        m2 = re.search(r"[预前][判测]\s*(\d+(?:\.\d+)?)\s*/\s*10", raw)
        if m2 and "predict_mid" not in result:
            v = float(m2.group(1))
            result["predict_mid"] = v
            result["predict_lo"] = result["predict_hi"] = v
            result["parse_mode"] = "strict_single"
        # ② ⛔ 2026-10-27 实测结论：**不做「情绪 X/10」宽松兜底**。
        #    盘前卡里确实有「情绪1.0/10冰点」这类数字（全库 20 处），但逐条核语境后：
        #    含前瞻词（明日/次日/预判）**0 处**，含回顾词（日期/昨日/收盘/轨迹）5 处，
        #    其余 15 处是「退潮确认：情绪2.4/10」「情绪4.5/10修复(连续第2日…)」——
        #    全部在描述**昨日收盘的实际情绪**，没有一个是在预测明日。
        #    若放宽正则当成预测值，等于拿「昨天的实际」去减「今天的实际」→ 凭空造出
        #    一条系统性偏差曲线，直接污染 R3/P1。宁可少算，不可错算。
        #    → 根治在 P2：盘前卡落 `sentiment_predict` 结构化字段（见 架构/ 待办）。
        #    此处只做**识别并标记**，供卡面说人话，不参与计算。
        if "predict_mid" not in result:
            m3 = re.search(r"(?:情绪|温度|分数)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:[~\-—至]\s*(\d+(?:\.\d+)?)\s*)?/\s*10", raw)
            if m3:
                result["rejected_loose_value"] = float(m3.group(1))
                result["reject_reason"] = "「情绪X/10」为昨日实际分（回顾语境），非明日预判，不采用"
        if result.get("predict_mid") is not None:
            result["source"] = pat
            break
        if result.get("rejected_loose_value") is not None:
            result["source"] = pat
            break
    return result


def actual_sentiment(d: str) -> float | None:
    """当日实际情绪分：summary_5d.days[date] 优先，feedback_matrix 兜底。"""
    s5 = load_summary5d()
    for day in s5.get("days", []):
        if day.get("date") == d and day.get("sentiment_score") is not None:
            return float(day["sentiment_score"])
    fb = CACHE / "feedback_matrix.json"
    if fb.exists():
        try:
            d_ = json.loads(fb.read_text(encoding="utf-8"))
            sc = d_.get("sentiment_score")
            if isinstance(sc, (int, float)) and d_.get("date", "") == d:
                return float(sc)
        except Exception:
            pass
    return None


# ---------------- 回测指标 ----------------

def _vacuous_reason(settle_date: str) -> str | None:
    """当日「零参与」口径（2026-10-27 新增）——把「真空仓」从「数据缺失」里剥出来。

    背景：34 个回测日里候选命中 19 天不可算，其中 10 天是 `pool_call.participate=[]`
    （防守/冰点/买不进 → 零成交 → nav 本来就不该写账）。旧实现一律记
    「nav 无记录」，与真正的链路断（候选文件缺失 / 有票无结算）混为一谈，
    导致 P4 天天假阳性、R2 样本永远凑不齐。

    ⛔ 只认「字段存在且为空列表」这一种形态：`participate=None`（老版本无此字段）
       必须落回原判，不许猜。
    """
    p = WC_CACHE / f"pool_call-{settle_date}.json"
    if not p.exists():
        return None
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    meta = j.get("meta") or {}
    part = meta.get("participate")
    if part is None:
        part = j.get("participate")
    if isinstance(part, list) and len(part) == 0:
        stance = meta.get("stance") or j.get("stance") or "?"
        return f"当日零参与（stance={stance}），nav 无账可写 → 真空仓，非数据缺口"
    return None


def backtest_candidate_hit(rec_date: str, settle_date: str, nav_rows: list) -> dict:
    """A. 候选池命中率：找 nav 账本里 recommendation_date==rec_date 的结算记录。"""
    rec = None
    for r in nav_rows:
        if r.get("recommendation_date") == rec_date:
            # 取最近一个 trade_date（避免历史重复推荐串扰）
            if rec is None or r.get("trade_date", "") > rec.get("trade_date", ""):
                rec = r
    if not rec:
        vac = _vacuous_reason(settle_date)
        if vac:
            # 真空仓：available 仍为 False（无样本可算命中率），但原因可裁决、不计缺口
            return {"available": False, "status": "vacuous", "reason": vac}
        return {"available": False, "status": "gap", "reason": f"nav 无 recommendation_date={rec_date} 记录"}
    avg = rec.get("avg_return_pct")
    stocks = rec.get("stocks", [])
    n = len(stocks)
    pos = sum(1 for s in stocks if isinstance(s.get("return_pct"), (int, float)) and s["return_pct"] > 0)
    return {
        "available": True,
        "trade_date": rec.get("trade_date"),
        "stock_count": n,
        "avg_return_pct": avg,
        "hit_count": pos,
        "hit_rate": round(pos / n * 100, 1) if n else None,
        "stocks": [{"code": s.get("code"), "name": s.get("name"), "return_pct": s.get("return_pct")} for s in stocks],
    }


def backtest_range_hit(rec_date: str, settle_date: str, cand: dict, auction: dict) -> dict:
    """B. 预期区间命中率：candidates expectation.expected_range vs 实际竞价涨幅。"""
    pools = []
    for key in ("relay", "watch"):
        for it in cand.get(key, []):
            pools.append((key, it))
    if not pools:
        return {"available": False, "reason": "candidates 无 relay/watch 池"}
    if not auction:
        return {"available": False, "reason": f"无 {settle_date} 竞价快照（区间命中不可评）"}

    rows = []
    hit_n = 0
    eval_n = 0
    for key, it in pools:
        code = str(it.get("code", "")).split(".")[0]
        exp = it.get("expectation") or {}
        er = exp.get("expected_range")
        q = auction.get(code)
        if not er or not q:
            continue
        prev_close = q.get("prev_close")
        auction_price = q.get("auction_price")
        if not prev_close or not auction_price:
            continue
        gap_pct = round((auction_price - prev_close) / prev_close * 100, 2)
        lo, hi = float(er[0]), float(er[1])
        hit = lo <= gap_pct <= hi
        eval_n += 1
        hit_n += 1 if hit else 0
        rows.append({
            "code": code, "name": it.get("name"), "pool": key,
            "expected_range": er, "actual_gap_pct": gap_pct, "hit": hit,
        })
    if not eval_n:
        return {"available": True, "evaluable": 0, "reason": "候选均无 expectation 或竞价数据"}
    return {
        "available": True,
        "evaluable": eval_n,
        "hit_count": hit_n,
        "hit_rate": round(hit_n / eval_n * 100, 1),
        "rows": rows,
    }


def backtest_sentiment_align(rec_date: str) -> dict:
    """C. 情绪分对齐：盘前预测分 vs 当日收盘实际分。

    ⛔ 2026-10-27：明确区分「盘前卡没写预测分」与「解析器坏了」——
       前者是契约缺失（应补结构化字段），后者是 bug。旧实现一律 available=False 无从分辨。
    """
    pred = find_prediction_text(rec_date)
    actual = actual_sentiment(rec_date)
    mid = pred.get("predict_mid")
    if mid is None:
        reason = pred.get("reject_reason") or (
            "盘前卡无「预判/预测 X/10」预测分（契约缺失，需落 sentiment_predict 结构化字段）"
        )
        return {"available": False, "status": "contract_missing", "reason": reason,
                "candidates_found": pred.get("rejected_loose_value")}
    if actual is None:
        return {"available": False, "status": "actual_missing",
                "reason": f"{rec_date} 实际情绪分不可得（summary_5d 已滚出窗口 / feedback_matrix 无当日）"}
    diff = round(mid - actual, 2)
    return {
        "available": True,
        "predict": pred.get("predict_lo", pred.get("predict_mid")),
        "predict_mid": mid,
        "predict_hi": pred.get("predict_hi"),
        "actual": actual,
        "diff": diff,
        "aligned": abs(diff) <= 1.0,
        "source": pred.get("source"),
    }


# ---------------- 主流程 ----------------

def run_backtest(settle_date_str: str, dry_run: bool = False) -> dict:
    d = date.fromisoformat(settle_date_str)
    if not is_trading_day(d):
        print(f"[backtest] {settle_date_str} 非交易日，跳过")
        return {"date": settle_date_str, "status": "skipped_non_trading"}

    # 找该结算日对应的推荐日：nav 账本中 trade_date==settle 的记录 → recommendation_date
    nav_rows = load_nav()
    rec_date = None
    for r in nav_rows:
        if r.get("trade_date") == settle_date_str:
            rec_date = r.get("recommendation_date")
            break
    if rec_date is None:
        # 回退：尝试最近一个有候选的推荐日（前推 5 天）
        for i in range(1, 6):
            rd = (d - timedelta(days=i)).isoformat()
            if load_candidates(rd):
                rec_date = rd
                break
    if rec_date is None:
        print(f"[backtest] {settle_date_str} 无对应推荐日候选，跳过")
        return {"date": settle_date_str, "status": "skipped_no_candidate"}

    if dry_run:
        print(f"[backtest][dry-run] 结算日={settle_date_str} 推荐日={rec_date} "
              f"输入: candidates-{rec_date}.json + auction_quotes-{settle_date_str}.json + nav")
        return {"date": settle_date_str, "rec_date": rec_date, "status": "dry_run"}

    cand = load_candidates(rec_date)
    auction = load_auction(settle_date_str)
    a = backtest_candidate_hit(rec_date, settle_date_str, nav_rows)
    b = backtest_range_hit(rec_date, settle_date_str, cand, auction)
    c = backtest_sentiment_align(rec_date)

    report = {
        "schema": "backtest_report_v1",
        "generated_at": _bj_now().isoformat(timespec="seconds"),
        "settle_date": settle_date_str,
        "rec_date": rec_date,
        "metrics": {
            "candidate_hit": a,
            "range_hit": b,
            "sentiment_align": c,
        },
        "summary": {
            "candidate_hit_rate": a.get("hit_rate") if a.get("available") else None,
            "range_hit_rate": b.get("hit_rate") if b.get("available") else None,
            "sentiment_diff": c.get("diff") if c.get("available") else None,
        },
    }

    # 幂等落盘：单日报告覆写 + 历史 append（同 settle_date 去重）
    report_path = REPORT_DIR / f"backtest_report_{settle_date_str}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    append_history(report)
    print(f"[backtest] {settle_date_str} 完成: 候选命中 {a.get('hit_rate')}% / "
          f"区间命中 {b.get('hit_rate')}% / 情绪差 {c.get('diff')}")
    return report


def append_history(report: dict):
    """累积历史 append-only，同 settle_date 幂等（删旧写新）。"""
    lines = []
    if HISTORY_FILE.exists():
        lines = [l for l in HISTORY_FILE.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not json.loads(l).get("settle_date") == report["settle_date"]]
    lines.append(json.dumps(report, ensure_ascii=False))
    HISTORY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def last_completed_trading_day() -> str:
    """最近一个已收盘交易日（今天≥15:00 且为交易日 → 今天；否则前推）。"""
    now = _bj_now()
    d = now.date()
    if now.hour >= 15 and is_trading_day(d):
        return d.isoformat()
    for i in range(1, 8):
        dd = d - timedelta(days=i)
        if is_trading_day(dd):
            return dd.isoformat()
    return d.isoformat()


def main():
    ap = argparse.ArgumentParser(description="推测回测引擎")
    ap.add_argument("--date", help="结算日 YYYY-MM-DD（缺省=最近已完成交易日）")
    ap.add_argument("--dry-run", action="store_true", help="只看输入不产出")
    args = ap.parse_args()
    settle = args.date or last_completed_trading_day()
    run_backtest(settle, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
