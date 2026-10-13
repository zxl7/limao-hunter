#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""尾盘财富密码·阴线回踩 MA10 潜伏扫描与独立台账。

策略只记录与结算研究信号，绝不下单、绝不推送，也不混入接力策略账户账本。

⛔ 时点口径（2026-10-09 用户「买入是今天推出来之后就买入计入战绩，不是等到明天再买入，
明天10点准时卖出！！！计入收益」）：T 日 14:30 冻结候选 → **T 日卡推送后即按实时价买入、
计入战绩** → T+1 日 10:00 准时卖出、结算收益。买入价取推送时点的真实价；绝不用次日价、
收盘价或相邻价替代，取不到就不记。

CLI（三个时点各一条，均由调度调用）：
- `scan --output …` + `record --file …`：14:30 扫描并冻结（快照 + 信号台账）
- `entry-plan --decision-date …`：卡推送后抓当日实时价作**买入价**（当日已记则跳过·不覆盖）
- `settle-live --decision-date … --trade-date …`：T+1 10:00 抓真实价作卖出价并结算收益

⛔ 2026-10-09 用户拍板：本策略＝「尾盘潜伏卡」的**唯一选材与推票来源**；
卡片由 `build_cards.py` 只读 14:30 快照渲染，不在渲染期扫描/记账/下单。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

BJ_TZ = timezone(timedelta(hours=8))

# 可逆策略参数：任何阈值变动都要同步 shared_rules 与测试。
ENABLED = True
LOOKBACK_DAYS = 5           # 2026-10-13 用户定：「前期的热门题材」＝ 近 5 个交易日内爆发过（原 20 日）
MIN_HOT_SECTOR_ZT = 3
MIN_HISTORY_BOARDS = 2
MA10_TOUCH_LOW = 0.98       # 收盘允许低于 MA10 最多 2%
MA10_TOUCH_HIGH = 1.03      # 收盘允许高于 MA10 最多 3%
VOLUME_SHRINK_RATIO = 0.80  # 当日量 <= 前一日量 80% 才算缩量
MIN_TURNOVER_YI = 5.0
EXCLUDE_BOARDS = 5
# ⛔ 2026-10-13 用户「只买阴线…靠近10日线的阴线」+ 实盘教训（10-09 三只全亏 / 10-13 大金重工
#   当日 −9.36% 被当「缩量回踩」）：把「阴线」与「回踩」的口径收严——
MAX_DROP_PCT = 5.0          # 当日跌幅上限（%，相对前收）→ 超过＝**砸盘**，不是「回踩」
REQUIRE_CLOSE_DOWN = True   # 「阴线」须**同时收跌**（close < prev_close）：跳空高开收阴不算
DATA_FAIL_MAX_RATIO = 0.5   # 日K抓取失败占比 ≥ 此值 且 ≥ DATA_FAIL_MIN 只 → data_ok=False
DATA_FAIL_MIN = 20

# ── ⑥ 环境读数（退潮期·**只控仓位·不砍票**）────────────────────────────────────
# 用户 2026-10-13 原话：「还是要推，尽量推两个吧，平摊一下仓位。但是我们要控制哦，
#   退潮期我们控制的是**仓位**，而不是通知票，这些东西还是**按照逻辑推**。」
# → 环境闸**不过滤任何候选**（出票逻辑不变），只输出「今日怎么下注」。
# 数据源＝`cache/pulse_snapshots/latest.json`（盘中 30s 刷新·14:30 时点可得）。
LATEST_SNAPSHOT = "pulse_snapshots/latest.json"
ENV_WEAK_MIN_HITS = 2          # 命中 ≥ 该条数 → 判「退潮」
ENV_MAX_DOWN_LIMIT = 10        # 跌停家数 ≥ 该值算弱
ENV_MIN_ZT_TOTAL = 40          # 涨停家数 < 该值算弱
ENV_MAX_BROKEN_RATE = 35.0     # 炸板率（%）≥ 该值算弱
WEAK_ENV_MAX_PICKS = 2         # 退潮日最多推几只（用户：尽量推两个）
WEAK_ENV_POSITION_PCT = 50.0   # 退潮日**每只**仓位上限（2 只 → 平摊）
NORMAL_ENV_POSITION_PCT = 50.0 # 常态每只仓位上限（既有口径：各自不超一半）
STRATEGY = "ambush_pullback_ma10_v1"
SIGNAL_LEDGER_NAME = "ambush_pullback_signal_log.jsonl"
SETTLEMENT_LEDGER_NAME = "ambush_pullback_settlement_log.jsonl"


def _now() -> datetime:
    return datetime.now(BJ_TZ)


def _trade_date() -> str:
    return _now().strftime("%Y-%m-%d")


def _num(v: Any) -> float | None:
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _excluded(code: str, name: str, boards: int, turnover_yi: float) -> str | None:
    if "ST" in str(name).upper():
        return "ST"
    if str(code).startswith(("688", "689", "4", "8", "9")):
        return "无交易权限市场"
    if boards >= EXCLUDE_BOARDS:
        return f"曾{boards}板高位"
    if turnover_yi and turnover_yi < MIN_TURNOVER_YI:
        return f"成交额<{MIN_TURNOVER_YI:g}亿"
    return None


def _load_zt(path: Path) -> list[dict]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return [x for x in (d.get("zt_list") or []) if isinstance(x, dict)]
    except Exception:
        return []


def _zt_days(cache_dir: Path) -> list[str]:
    days = []
    for f in (cache_dir / "zt_history").glob("zt_*.json"):
        d = f.stem[3:]
        if len(d) == 10 and d[4] == "-":
            days.append(d)
    return sorted(set(days))


def _historical_universe(cache_dir: Path, today: str) -> dict[str, dict]:
    """曾出现在热门爆发板块的个股。

    热门爆发按历史涨停快照量化为：同板块当日至少 3 家涨停，或个股曾至少 2 连板。
    只用已落盘涨停历史，避免把当前热度或模型标签倒灌到历史条件。
    """
    zt_dir = cache_dir / "zt_history"
    days = [d for d in _zt_days(cache_dir) if d < today][-LOOKBACK_DAYS:]
    out: dict[str, dict] = {}
    for day in days:
        rows = _load_zt(zt_dir / f"zt_{day}.json")
        sector_count: dict[str, int] = {}
        for r in rows:
            sec = str(r.get("sector") or "").strip()
            if sec:
                sector_count[sec] = sector_count.get(sec, 0) + 1
        for r in rows:
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            boards = int(r.get("boards") or 0)
            sec = str(r.get("sector") or "").strip()
            hot = sector_count.get(sec, 0) >= MIN_HOT_SECTOR_ZT or boards >= MIN_HISTORY_BOARDS
            if not hot:
                continue
            old = out.get(code)
            item = {
                "code": code, "name": str(r.get("name") or ""), "sector": sec,
                "hot_date": day, "sector_zt_count": sector_count.get(sec, 0),
                "max_boards": boards, "turnover_yi": float(r.get("cje_yi") or 0),
            }
            if old is None:
                out[code] = item
            else:
                old["max_boards"] = max(int(old["max_boards"]), boards)
                if day >= str(old["hot_date"]):
                    old.update(item)
                    old["max_boards"] = max(int(old["max_boards"]), boards)
    return out


def _shape(bars: list[dict]) -> tuple[dict | None, str | None]:
    """计算阴线、MA10 上行、回踩及缩量。bars 必须按日期正序并含当天日K。"""
    if len(bars) < 12:
        return None, "日K不足12根"
    tail = bars[-12:]
    try:
        closes = [float(x["close"]) for x in tail]
        current = tail[-1]
        prev = tail[-2]
        open_ = float(current["open"])
        close = float(current["close"])
        prev_close = float(prev["close"])
        volume = float(current["volume"])
        prev_volume = float(prev["volume"])
    except (KeyError, TypeError, ValueError):
        return None, "日K字段不完整"
    if open_ <= 0 or close <= 0 or volume <= 0 or prev_volume <= 0:
        return None, "日K价格或成交量无效"
    ma10 = sum(closes[-10:]) / 10
    ma10_prev = sum(closes[-11:-1]) / 10
    ma10_prev2 = sum(closes[-12:-2]) / 10
    is_bearish = close < open_
    is_down = close < prev_close
    drop_pct = (prev_close - close) / prev_close * 100 if prev_close > 0 else 0.0
    ma10_up = ma10 > ma10_prev > ma10_prev2
    ma10_gap_pct = (close / ma10 - 1) * 100
    near_ma10 = MA10_TOUCH_LOW <= close / ma10 <= MA10_TOUCH_HIGH
    volume_ratio = volume / prev_volume
    is_shrinking = volume_ratio <= VOLUME_SHRINK_RATIO
    if not is_bearish:
        return None, "当日非阴线"
    if REQUIRE_CLOSE_DOWN and not is_down:
        return None, "当日未收跌（跳空高开收阴不算）"
    if drop_pct > MAX_DROP_PCT:
        return None, f"当日跌幅>{MAX_DROP_PCT:g}%（是砸盘·不是回踩）"
    if not ma10_up:
        return None, "MA10未连续上行"
    if not near_ma10:
        return None, "收盘未回踩或接近MA10"
    if not is_shrinking:
        return None, "当日未缩量"
    return {
        "date": str(current.get("date") or ""), "open": round(open_, 3), "close": round(close, 3),
        "prev_close": round(prev_close, 3), "pct": round((close / prev_close - 1) * 100, 2),
        "drop_pct": round(drop_pct, 2),
        "volume": volume, "prev_volume": prev_volume, "volume_ratio": round(volume_ratio, 4),
        "ma10": round(ma10, 3), "ma10_prev": round(ma10_prev, 3), "ma10_prev2": round(ma10_prev2, 3),
        "ma10_gap_pct": round(ma10_gap_pct, 3), "is_bearish": True, "is_down": bool(is_down),
        "ma10_up": True, "near_ma10": True, "is_shrinking": True,
    }, None


def env_read(cache_dir: Path, path: Path | None = None) -> dict[str, Any]:
    """当日实时环境读数（**只读** `pulse_snapshots/latest.json`）。

    ⛔ 只回答「今天该下多大注」，**不参与选票**：用户 2026-10-13 明确「退潮期我们控制的是
    仓位，而不是通知票，这些东西还是按照逻辑推」。缺失/字段不全 → `known=False`，
    调用方按常态处理（诚实降级，不编造）。
    """
    p = path or (Path(cache_dir) / LATEST_SNAPSHOT)
    base = {"known": False, "level": "unknown", "hits": [], "hit_count": 0,
            "facts": {}, "note": ""}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return base
    if not isinstance(d, dict):
        return base
    zt = _num(d.get("total_zt"))
    dt = _num((d.get("limit_down") or {}).get("count"))
    broken = _num((d.get("broken") or {}).get("count"))
    br = d.get("breadth") if isinstance(d.get("breadth"), dict) else {}
    up, down = _num(br.get("up")), _num(br.get("down"))
    hits: list[str] = []
    if up is not None and down is not None and down > up:
        hits.append(f"跌的比涨的多（{down:.0f} 家跌 vs {up:.0f} 家涨）")
    if dt is not None and dt >= ENV_MAX_DOWN_LIMIT:
        hits.append(f"跌停 {dt:.0f} 家")
    if zt is not None and zt < ENV_MIN_ZT_TOTAL:
        hits.append(f"涨停只有 {zt:.0f} 家")
    rate = None
    if zt is not None and broken is not None and (zt + broken) > 0:
        rate = broken / (zt + broken) * 100
        if rate >= ENV_MAX_BROKEN_RATE:
            hits.append(f"炸板率 {rate:.0f}%")
    facts = {"total_zt": zt, "limit_down": dt, "broken": broken, "up": up, "down": down,
             "broken_rate": round(rate, 1) if rate is not None else None}
    level = "weak" if len(hits) >= ENV_WEAK_MIN_HITS else "normal"
    note = ("今天市场在退潮（" + "、".join(hits) + "）——票照常按形态推，但仓位要收："
            f"最多推 {WEAK_ENV_MAX_PICKS} 只、每只 ≤{WEAK_ENV_POSITION_PCT:.0f}%（平摊）"
            ) if level == "weak" else ""
    return {"known": True, "level": level, "hits": hits, "hit_count": len(hits),
            "facts": facts, "note": note}


def position_advice(env: dict[str, Any] | None) -> dict[str, Any]:
    """环境 → **仓位建议**（⛔ 不裁候选）。

    用户 10-13：「还是要推，尽量推两个，平摊一下仓位」——退潮日**照常按形态出票**，
    只是把下注方式收一收：最多 `WEAK_ENV_MAX_PICKS` 只、每只 `WEAK_ENV_POSITION_PCT`%（平摊）。
    """
    e = env if isinstance(env, dict) else {}
    weak = str(e.get("level") or "") == "weak"
    return {
        "level": e.get("level", "unknown"),
        "max_picks": WEAK_ENV_MAX_PICKS if weak else None,
        "position_pct_each": WEAK_ENV_POSITION_PCT if weak else NORMAL_ENV_POSITION_PCT,
        "note": e.get("note") or "",
    }


def scan(cache_dir: Path, bars_fetcher: Callable[[str, int], list[dict]] | None = None,
         today: str | None = None) -> dict[str, Any]:
    """生成 14:30 候选快照。bars_fetcher 仅为测试/离线复核注入点。"""
    day = today or _trade_date()
    if not ENABLED:
        return {"strategy": STRATEGY, "trade_date": day, "enabled": False, "candidates": []}
    if bars_fetcher is None:
        from common.tencent_kline import fetch_kline_ohlc
        bars_fetcher = lambda code, n: fetch_kline_ohlc(code, n=n)
    universe = _historical_universe(cache_dir, day)
    candidates, rejected = [], {}
    attempted = 0
    fetch_failed = 0
    for code, meta in universe.items():
        reason = _excluded(code, meta["name"], int(meta["max_boards"]), float(meta["turnover_yi"]))
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        attempted += 1
        try:
            shape, reason = _shape(bars_fetcher(code, 15))
        except Exception:
            shape, reason = None, "日K抓取失败"
        if not shape:
            if str(reason) in ("日K抓取失败", "日K不足12根"):
                fetch_failed += 1
            rejected[str(reason)] = rejected.get(str(reason), 0) + 1
            continue
        score = round((3.0 - abs(shape["ma10_gap_pct"])) * 10 + (1 - shape["volume_ratio"]) * 20
                      + min(int(meta["sector_zt_count"]), 8) + min(int(meta["max_boards"]), 4) * 2, 2)
        candidates.append({**meta, **shape, "score": score})
    candidates.sort(key=lambda x: (-x["score"], x["code"]))
    # ⛔ 数据源健康度（2026-10-09 血案：腾讯停用 host → 全量日K空 → 候选 0 只被当成
    #    「今日无符合条件」的假阴性）。两条任一即判 data_ok=False：
    #    ① 全部抓取失败（任何规模都算）② 失败占比 ≥50% 且样本 ≥DATA_FAIL_MIN。
    #    卡面必须显式声明「数据异常」而不是「无候选」——缺数据 ≠ 没候选。
    total_fail = bool(attempted) and fetch_failed == attempted
    ratio_fail = attempted >= DATA_FAIL_MIN and (fetch_failed / attempted) >= DATA_FAIL_MAX_RATIO
    data_ok = not (total_fail or ratio_fail)
    # ⑥ 环境读数 → 仓位建议（⛔ 不动 `candidates`：退潮期控的是**仓位**不是票）
    env = env_read(cache_dir)
    return {
        "strategy": STRATEGY, "trade_date": day, "selection_time": f"{day} 14:30",
        "generated_at": _now().isoformat(),
        "planned_entry_time": f"{day} 推送后（≈14:50）", "planned_exit_time": "next_trade_day 10:00",
        "data_ok": data_ok, "fetch_failed": fetch_failed, "attempted": attempted,
        "params": {"lookback_days": LOOKBACK_DAYS, "min_hot_sector_zt": MIN_HOT_SECTOR_ZT,
                   "min_history_boards": MIN_HISTORY_BOARDS, "ma10_touch_low": MA10_TOUCH_LOW,
                   "ma10_touch_high": MA10_TOUCH_HIGH, "volume_shrink_ratio": VOLUME_SHRINK_RATIO,
                   "max_drop_pct": MAX_DROP_PCT, "require_close_down": REQUIRE_CLOSE_DOWN},
        "stats": {"universe": len(universe), "hits": len(candidates), "rejected": rejected},
        "env": env, "advice": position_advice(env),
        "candidates": candidates,
    }


def _append_once(path: Path, rows: list[dict], key: Callable[[dict], tuple]) -> int:
    known: set[tuple] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                known.add(key(json.loads(line)))
            except Exception:
                continue
    new = [x for x in rows if key(x) not in known]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for row in new:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(new)


def record_selection(cache_dir: Path, result: dict, path: Path | None = None) -> int:
    """冻结 14:30 候选到独立信号台账；买入按时点口径（当日推送后）计入战绩。"""
    ledger = path or cache_dir / SIGNAL_LEDGER_NAME
    day = str(result.get("trade_date") or "")
    rows = []
    for item in result.get("candidates") or []:
        rows.append({"strategy": STRATEGY, "decision_date": day, "code": item["code"], "name": item["name"],
                     "status": "selected_1430", "planned_entry_time": f"{day} 推送后（≈14:50）",
                     "planned_exit_time": "next_trade_day 10:00", "entry_price": None, "exit_price": None,
                     "return_pct": None, "candidate_snapshot": item, "recorded_at": _now().isoformat()})
    return _append_once(ledger, rows, lambda x: (x.get("strategy"), x.get("decision_date"), x.get("code")))


def settle(cache_dir: Path, decision_date: str, trade_date: str,
           entry_prices: dict[str, float], exit_prices: dict[str, float], path: Path | None = None,
           entry_time: str | None = None) -> int:
    """按「当日推送后买入价 / 次一交易日10:00卖出价」结算，计入战绩与收益。缺任一价格不结算、不填0。"""
    source = cache_dir / SIGNAL_LEDGER_NAME
    if not source.exists():
        return 0
    plans = []
    for line in source.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("strategy") == STRATEGY and r.get("decision_date") == decision_date:
            plans.append(r)
    entry_label = entry_time or f"{decision_date} 推送后（≈14:50）"
    rows = []
    for r in plans:
        code = str(r.get("code") or "")
        entry, exit_ = _num(entry_prices.get(code)), _num(exit_prices.get(code))
        if not entry or not exit_ or entry <= 0 or exit_ <= 0:
            continue
        rows.append({"strategy": STRATEGY, "decision_date": decision_date, "trade_date": trade_date,
                     "code": code, "name": r.get("name"), "entry_time": entry_label,
                     "exit_time": f"{trade_date} 10:00", "entry_price": entry, "exit_price": exit_,
                     "return_pct": round((exit_ / entry - 1) * 100, 4), "status": "settled"})
    return _append_once(path or cache_dir / SETTLEMENT_LEDGER_NAME, rows,
                        lambda x: (x.get("strategy"), x.get("decision_date"), x.get("code")))


ENTRY_FILE_TMPL = "ambush_pullback_entry_{}.json"


def _realtime_prices(codes: list[str]) -> dict[str, float]:
    """取「当前真实价」（当日推送后即买入价、T+1 10:00 即卖出价）。

    复用竞价链路同源实时接口（`adapters.input_auction.fetch_realtime_closes`）。
    失败/取不到一律返回空 dict —— 缺价不结算、不补数、不用收盘价替代。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        return {}
    try:
        wc = str(_PROJ_ROOT / "wealth_code")
        if wc not in sys.path:
            sys.path.insert(0, wc)
        from adapters.input_auction import fetch_realtime_closes
    except Exception:
        return {}
    try:
        got = fetch_realtime_closes(codes) or {}
    except Exception:
        return {}
    out: dict[str, float] = {}
    for k, v in got.items():
        try:
            px = float(v)
        except (TypeError, ValueError):
            continue
        if px > 0:
            out[str(k)] = px
    return out


def frozen_codes(cache_dir: Path, decision_date: str, path: Path | None = None) -> list[str]:
    """读取某决策日已冻结在信号台账里的代码（正序去重）。"""
    source = path or cache_dir / SIGNAL_LEDGER_NAME
    if not source.exists():
        return []
    out: list[str] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("strategy") == STRATEGY and r.get("decision_date") == decision_date:
            code = str(r.get("code") or "")
            if code and code not in out:
                out.append(code)
    return out


def plan_entry(cache_dir: Path, decision_date: str,
               price_fetcher: Callable[[list[str]], dict] | None = None,
               out: Path | None = None, overwrite: bool = False) -> dict:
    """T 日卡推送后：抓当日实时价作**买入价**落盘（＝计入战绩的成交价）。缺价不写盘。

    ⛔ 当日已记录买入价时默认**不覆盖**（2026-10-09 用户「推出来之后就买入，计入战绩」）：
    买入价＝推送时点的价，后续时点重跑不得用更晚的价追溯改写战绩。
    """
    target = out or cache_dir / ENTRY_FILE_TMPL.format(decision_date)
    if not overwrite:
        try:
            old = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(old, dict) and old.get("prices"):
                return old
        except Exception:
            pass
    codes = frozen_codes(cache_dir, decision_date)
    fetch = price_fetcher or _realtime_prices
    prices: dict[str, float] = {}
    if codes:
        try:
            prices = {str(k): float(v) for k, v in (fetch(codes) or {}).items()}
        except Exception:
            prices = {}
    payload = {"strategy": STRATEGY, "decision_date": decision_date, "slot": "entry_after_push",
               "captured_at": _now().isoformat(), "codes": codes, "prices": prices}
    if prices:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def settle_live(cache_dir: Path, decision_date: str, trade_date: str,
                price_fetcher: Callable[[list[str]], dict] | None = None,
                entry_file: Path | None = None, path: Path | None = None) -> int:
    """T+1 日 10:00：读当日买入价 + 抓该时点真实价作卖出价，写入结算台账（计入收益）。"""
    src = entry_file or cache_dir / ENTRY_FILE_TMPL.format(decision_date)
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
        entry = raw.get("prices") or {}
        captured = str(raw.get("captured_at") or "")
    except Exception:
        return 0
    codes = [str(c) for c in entry]
    fetch = price_fetcher or _realtime_prices
    exit_prices: dict[str, float] = {}
    if codes:
        try:
            exit_prices = {str(k): float(v) for k, v in (fetch(codes) or {}).items()}
        except Exception:
            exit_prices = {}
    label = None
    if len(captured) >= 16 and captured[10] in ("T", " "):
        label = f"{decision_date} {captured[11:16]} 买入"
    return settle(cache_dir, decision_date, trade_date, entry, exit_prices, path=path, entry_time=label)


def pending_decision_date(cache_dir: Path, before: str) -> str | None:
    """最近一个「已记当日买入价、且尚未结算」的决策日（早于 before）。"""
    settled: set[str] = set()
    ledger = cache_dir / SETTLEMENT_LEDGER_NAME
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("strategy") == STRATEGY:
                settled.add(str(r.get("decision_date") or ""))
    best: str | None = None
    for f in sorted(cache_dir.glob(ENTRY_FILE_TMPL.format("*"))):
        day = f.stem.replace("ambush_pullback_entry_", "")
        if day and day < before and day not in settled:
            best = day
    return best


def _cli(argv: list[str]) -> int:
    """仅供定时任务调用的显式入口；不包含下单或推送能力。"""
    import argparse
    ap = argparse.ArgumentParser(description="尾盘阴线回踩MA10潜伏：扫描、冻结与独立结算")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("scan", help="14:30 扫描并输出JSON；默认不落盘")
    sp.add_argument("--date", dest="day")
    sp.add_argument("--output", type=Path)
    rp = sub.add_parser("record", help="把扫描结果冻结进独立信号台账")
    rp.add_argument("--file", type=Path, required=True)
    rp.add_argument("--cache-dir", type=Path, default=_PROJ_ROOT / "cache")
    epn = sub.add_parser("entry-plan", help="当日卡推送后抓实时价作买入价（当日已记则不覆盖）")
    epn.add_argument("--decision-date", required=True)
    epn.add_argument("--out", type=Path)
    epn.add_argument("--overwrite", action="store_true", help="覆盖当日已记录的买入价（默认不覆盖）")
    epn.add_argument("--cache-dir", type=Path, default=_PROJ_ROOT / "cache")
    slv = sub.add_parser("settle-live", help="T+1 10:00抓卖出价并结算收益（缺价不结算）")
    slv.add_argument("--decision-date", help="缺省＝自动取最近一个未结算的决策日")
    slv.add_argument("--trade-date", help="缺省＝今天")
    slv.add_argument("--entry-file", type=Path)
    slv.add_argument("--cache-dir", type=Path, default=_PROJ_ROOT / "cache")
    ep = sub.add_parser("settle", help="按已采集的14:50/次日10:00价格结算")
    ep.add_argument("--decision-date", required=True)
    ep.add_argument("--trade-date", required=True)
    ep.add_argument("--entry-prices", type=Path, required=True, help="{code: 14:50价格}")
    ep.add_argument("--exit-prices", type=Path, required=True, help="{code: 次日10:00价格}")
    ep.add_argument("--cache-dir", type=Path, default=_PROJ_ROOT / "cache")
    args = ap.parse_args(argv)
    if args.cmd == "scan":
        res = scan(_PROJ_ROOT / "cache", today=args.day)
        text = json.dumps(res, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        print(text)
        return 0
    if args.cmd == "record":
        try:
            res = json.loads(args.file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[ambush-pullback] 无法读取扫描结果: {exc}", file=sys.stderr)
            return 2
        print(f"[ambush-pullback] 冻结 {record_selection(args.cache_dir, res)} 只候选")
        return 0
    if args.cmd == "entry-plan":
        res = plan_entry(args.cache_dir, args.decision_date, out=args.out, overwrite=args.overwrite)
        print(f"[ambush-pullback] 当日买入价（推送后实时价）：冻结{len(res['codes'])}只 / "
              f"取到{len(res['prices'])}只 · captured_at={res.get('captured_at')}")
        return 0
    if args.cmd == "settle-live":
        trade_date = args.trade_date or _trade_date()
        decision = args.decision_date
        if not decision and not args.entry_file:
            decision = pending_decision_date(args.cache_dir, trade_date)
        if not decision and not args.entry_file:
            print("[ambush-pullback] 无可结算信号（未找到当日买入价文件）")
            return 0
        n = settle_live(args.cache_dir, decision, trade_date, entry_file=args.entry_file)
        print(f"[ambush-pullback] 结算 {n} 只（买入日 {decision} → 卖出日 {trade_date}）")
        return 0
    try:
        entry = json.loads(args.entry_prices.read_text(encoding="utf-8"))
        exit_ = json.loads(args.exit_prices.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ambush-pullback] 无法读取价格文件: {exc}", file=sys.stderr)
        return 2
    print(f"[ambush-pullback] 结算 {settle(args.cache_dir, args.decision_date, args.trade_date, entry, exit_)} 只")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
