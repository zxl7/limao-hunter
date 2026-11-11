#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""尾盘财富密码·强势趋势核心(分歧转一致)潜伏扫描与独立台账。

策略只记录与结算研究信号，绝不下单、绝不推送，也不混入接力策略账户账本。

⛔ 时点口径（2026-10-09 用户「买入是今天推出来之后就买入计入战绩，不是等到明天再买入，
明天10点准时卖出！！！计入收益」）：T 日 14:30 冻结候选 → **T 日卡推送后即按实时价买入、
计入战绩** → T+1 日 10:00 准时卖出、结算收益。买入价取推送时点的真实价；绝不用次日价、
收盘价或相邻价替代，取不到就不记。

CLI（三个时点各一条，均由调度调用）：
- `scan --output …` + `record --file …`：14:30 扫描并冻结（快照 + 信号台账）
- `entry-plan --decision-date …`：卡推送后抓当日实时价作**买入价**（当日已记则跳过·不覆盖）；
  取价范围＝当日 AI 精选推票（卡面「✅ 今日买入」那几只·`ENTRY_PICKS_ONLY`），买几只记几只
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
MIN_HOT_THEME_ZT = 3        # 题材口径（选股宝/东财 getFryTomorrowList 回补）：单日涨停≥3家才算热门题材
MIN_HISTORY_BOARDS = 2
MIN_TURNOVER_YI = 0.05      # 🔴 2026-11-11 用户「5 亿全系统清除」→ 仅留数据防呆（原 5.0）
EXCLUDE_BOARDS = 5
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
# ⛔ 单票硬顶单一源 = core/risk_limits.MAX_POSITION_PER_STOCK（2026-10-30 用户「单股不能超过 50%」）
#   双路径兜底（`core.*` 与 `wealth_code.core.*` 两种顶层包都要能取到同一个数）
try:
    from wealth_code.core.risk_limits import MAX_POSITION_PER_STOCK as _HARD_PER_STOCK  # noqa: E402
except ImportError:  # pragma: no cover
    from core.risk_limits import MAX_POSITION_PER_STOCK as _HARD_PER_STOCK  # noqa: E402

WEAK_ENV_POSITION_PCT = _HARD_PER_STOCK * 100.0   # 退潮日**每只**仓位上限（2 只 → 平摊）
NORMAL_ENV_POSITION_PCT = _HARD_PER_STOCK * 100.0 # 常态每只仓位上限（既有口径：各自不超一半）

# ── 强势趋势核心·分歧转一致（2026-10-15 上线，取代旧的「阴线回踩MA10」口径）──
# 反推样本：华北制药600812(医药/鼠疫链霉素)、欢瑞世纪000892(传媒/AI短剧)。
# 旧口径选的是弱势回踩/破位票（实证 2 赚 12 亏、累计 -35%）；本口径选
# 「强势题材核心、均线多头、近15日放量启动、近1-3日分歧洗盘(仍在MA10上方)后反包转一致」的票。
STRONG_MA10_POS_LOW = 1.03      # 价须站在 MA10 上方至少 +3%
STRONG_MA10_POS_HIGH = 1.15     # 至多 +15%，过高=追板高位
STRONG_MIN_VOL_RATIO = 1.2      # 近5日均量/近20日均量 ≥1.2（放量）
STRONG_START_PCT = 0.07         # 近15日须有 ≥+7% 放量启动阳线
STRONG_START_VOL_RATIO = 1.2    # 启动阳线量比门槛
STRONG_PULLBACK_DROP_LOW = -0.15   # 分歧洗盘阴线跌幅下限(-15%，强票洗盘更狠)
STRONG_PULLBACK_DROP_HIGH = -0.03  # 分歧洗盘阴线跌幅上限(-3%)
STRONG_CONSEC_BOARDS_MAX = 3    # 当日连续涨停≥3板视为高位不接
STRONG_SHAPE_BARS = 70          # 取近70根算 MA60/量比/启动

STRATEGY = "ambush_pullback_ma10_v1"
SIGNAL_LEDGER_NAME = "ambush_pullback_signal_log.jsonl"
SETTLEMENT_LEDGER_NAME = "ambush_pullback_settlement_log.jsonl"

# 买入价台账取价范围（2026-10-15 用户「就按你这个台账判断的买」）：
#   True  = 只记**当日 AI 精选推票**（`cache/ambush_ai_MM-DD.json` 的 picks，且须落在当日冻结池
#           内）—— 与卡面「✅ 今日买入（AI 精选）」逐票一致，买几只就记几只。
#   False = 旧口径：冻结全池逐票记价 —— 含未入选票（实证 2026-10-15 记了 4 只而卡面只推 2 只），
#           结算时会把没买的票也算进战绩。
ENTRY_PICKS_ONLY = True


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


def _top5_days(cache_dir: Path) -> list[str]:
    days = []
    d = cache_dir / "top5_history"
    if not d.exists():
        return days
    for f in d.glob("top5_*.json"):
        stem = f.stem
        if len(stem) >= 15 and stem[5:15][4] == "-":
            days.append(stem[5:15])
    return sorted(set(days))


def _load_top5(path: Path) -> list[tuple[str, int, list[str]]]:
    """读某日题材快照 → [(题材名, 当日涨停家数, 成分股6位code列表)]。失败/空 → []。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    themes = d.get("themes") if isinstance(d.get("themes"), list) else []
    out: list[tuple[str, int, list[str]]] = []
    for t in themes:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        zt = t.get("zt_count")
        zt = int(zt) if isinstance(zt, int) else 0
        codes = [str(c).split(".")[0].strip() for c in (t.get("codes") or [])]
        codes = [c for c in codes if len(c) == 6]
        out.append((name, zt, codes))
    return out


def _build_hot_themes(cache_dir: Path, today: str) -> tuple[dict[str, set], dict[str, int]]:
    """近5个交易日热门题材 → (code→命中题材名集合, 题材名→近5日累计涨停家数)。

    ⛔ 题材口径（选股宝 prev_top5 / 东财 getFryTomorrowList 回补）替代原「东财行业」口径：
    用户 10-13「题材是可以积累和沉淀的」+ 调研结论——题材白名单用选股宝/东财题材名，
    个股→题材落地用题材成分股 codes（跨命名体系必须 code 反查，禁名称匹配）。
    单日涨停 < MIN_HOT_THEME_ZT 的题材不算热门（防脉冲一日游）。
    """
    days = [d for d in _top5_days(cache_dir) if d < today][-LOOKBACK_DAYS:]
    code_to_themes: dict[str, set] = {}
    theme_zt: dict[str, int] = {}
    for day in days:
        for name, zt, codes in _load_top5(cache_dir / "top5_history" / f"top5_{day}.json"):
            if zt < MIN_HOT_THEME_ZT:
                continue
            theme_zt[name] = theme_zt.get(name, 0) + zt
            for c in codes:
                code_to_themes.setdefault(c, set()).add(name)
    return code_to_themes, theme_zt


def _historical_universe(cache_dir: Path, today: str) -> dict[str, dict]:
    """曾出现在热门爆发方向的个股——三轨判定（OR）：

    ① **题材轨**（新·10-13 落地）：个股 ∈ 近5日热门题材的**成分股**（题材名来自选股宝/
       东财题材库，个股落地用成分股 code 反查）；
    ② **行业兜底轨**：近5日同东财行业累计涨停 ≥ MIN_HOT_SECTOR_ZT；
    ③ **独立总龙头轨**：个股自身曾 ≥ MIN_HISTORY_BOARDS 连板。

    只用已落盘数据（zt_history + top5_history），绝不把当前热度/模型标签倒灌到历史条件。
    个股池锚定 zt_history（必须近5日涨停记忆在册）；题材维度只决定「方向够不够热」。
    """
    zt_dir = cache_dir / "zt_history"
    days = [d for d in _zt_days(cache_dir) if d < today][-LOOKBACK_DAYS:]
    sector_count: dict[str, int] = {}
    seen: dict[str, dict] = {}
    last_day: dict[str, str] = {}
    boards_max: dict[str, int] = {}
    for day in days:
        rows = _load_zt(zt_dir / f"zt_{day}.json")
        day_sector: dict[str, int] = {}
        for r in rows:
            sec = str(r.get("sector") or "").strip()
            if sec:
                day_sector[sec] = day_sector.get(sec, 0) + 1
        for sec, n in day_sector.items():
            sector_count[sec] = sector_count.get(sec, 0) + n
        for r in rows:
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            b = int(r.get("boards") or 0)
            if code not in seen:
                seen[code] = r
            boards_max[code] = max(boards_max.get(code, 0), b)
            last_day[code] = day
    code_to_themes, theme_zt = _build_hot_themes(cache_dir, today)
    out: dict[str, dict] = {}
    for code, r in seen.items():
        sec = str(r.get("sector") or "").strip()
        sec_hot = sector_count.get(sec, 0) >= MIN_HOT_SECTOR_ZT
        board_hot = boards_max[code] >= MIN_HISTORY_BOARDS
        themes_hit = sorted(code_to_themes.get(code, set()))
        theme_hot = bool(themes_hit)
        if not (sec_hot or board_hot or theme_hot):
            continue
        theme_zt_max = max((theme_zt[t] for t in themes_hit), default=0)
        out[code] = {
            "code": code, "name": str(r.get("name") or ""),
            "sector": sec, "sector_zt_count": sector_count.get(sec, 0),
            "themes": themes_hit, "theme_zt_max": theme_zt_max,
            "hot_date": last_day.get(code, ""),
            "max_boards": boards_max[code], "turnover_yi": float(r.get("cje_yi") or 0),
        }
    return out


def _ma(vals: list[float], n: int, i: int) -> float | None:
    if i + 1 < n:
        return None
    return sum(vals[i + 1 - n:i + 1]) / n


def _shape_strong(bars: list[dict]) -> tuple[dict | None, str | None]:
    """强势趋势核心·分歧转一致（2026-10-15 启动的新策略）。

    反推自华北制药600812/欢瑞世纪000892 的理想买点（10-14 视角）。六条规律：
    ① 均线多头（MA5≥MA10≥MA20）且 MA20 上行
    ② 价站在 MA10 上方 +3%~+15%（趋势上行，非回踩破位）
    ③ 量能放大：近5日均量/近20日均量 ≥ 1.2
    ④ 近15日有 ≥+7% 的放量启动阳线（题材点火）
    ⑤ 近1-3日出现分歧洗盘阴线（跌幅 -15%~-3%、收跌）且洗盘时价仍在 MA10 上方，
       随后当日反包阳线（分歧转一致）= 尾盘介入点
    ⑥ 风控：当日连续涨停≥3板视为高位不接（防接飞刀）
    bars 按日期正序，需 ≥ STRONG_SHAPE_BARS 根。
    """
    n = len(bars)
    if n < STRONG_SHAPE_BARS:
        return None, f"日K不足{STRONG_SHAPE_BARS}根"
    try:
        rows = [{"date": str(x.get("date") or ""), "open": float(x["open"]),
                 "close": float(x["close"]), "high": float(x["high"]),
                 "low": float(x["low"]), "volume": float(x["volume"])} for x in bars]
    except (KeyError, TypeError, ValueError):
        return None, "日K字段不完整"
    closes = [r["close"] for r in rows]
    opens = [r["open"] for r in rows]
    vols = [r["volume"] for r in rows]
    N = len(rows)
    cur = N - 1
    price = closes[cur]
    if price <= 0:
        return None, "当日收盘价无效"
    ma5, ma10, ma20 = _ma(closes, 5, cur), _ma(closes, 10, cur), _ma(closes, 20, cur)
    if not (ma5 and ma10 and ma20):
        return None, "均线数据不足"
    # ① 多头排列 + MA20 上行（比 15 天前，避免中途挖坑单日拉低近期 MA20 误判）
    if not (ma5 >= ma10 - 1e-6 and ma10 >= ma20 - 1e-6):
        return None, "均线未多头排列(MA5≥MA10≥MA20)"
    ma20_prev = _ma(closes, 20, cur - 15)
    if ma20_prev and ma20 <= ma20_prev:
        return None, "MA20未上行"
    # ② 价在 MA10 上方 +3%~+15%
    pos = price / ma10
    if not (STRONG_MA10_POS_LOW <= pos <= STRONG_MA10_POS_HIGH):
        return None, f"价相对MA10位置={pos:.2f} 不在[{STRONG_MA10_POS_LOW},{STRONG_MA10_POS_HIGH}]"
    # ③ 量能放大
    v5 = sum(vols[-5:]) / 5
    v20 = sum(vols[-20:]) / 20
    vol_ratio = v5 / v20 if v20 > 0 else 0
    if vol_ratio < STRONG_MIN_VOL_RATIO:
        return None, f"量能未放大(量比={vol_ratio:.2f}<{STRONG_MIN_VOL_RATIO})"
    # ④ 近15日有 ≥+7% 启动阳线（放量由 ③ 全局量比保证；不强求单根量比，
    #    否则早期启动阳线相对后期放量的末5日均量天然偏低而被误杀）
    start_hit = False
    for i in range(max(0, N - 15), N):
        if closes[i] / opens[i] - 1 >= STRONG_START_PCT:
            start_hit = True
            break
    if not start_hit:
        return None, "近15日无≥+7%启动阳线"
    # ⑤ 分歧洗盘→反包：近1-3日洗盘阴线(跌-3%~-9%)且洗盘时价仍在MA10上方
    pullback_idx = None
    for i in range(max(0, N - 3), N - 1):
        is_yin = closes[i] < opens[i]
        drop = closes[i] / closes[i - 1] - 1
        if is_yin and STRONG_PULLBACK_DROP_LOW <= drop <= STRONG_PULLBACK_DROP_HIGH:
            mi = _ma(closes, 10, i)
            if mi and closes[i] / mi >= 1.0:
                pullback_idx = i
                break
    if pullback_idx is None:
        return None, "近3日无『MA10上方分歧洗盘阴线』"
    # 反包：当天收阳且收复洗盘日收盘（允许极小误差，避免微持平/微跌被误杀）
    if not (closes[cur] > opens[cur] and closes[cur] >= closes[pullback_idx] * 0.99):
        return None, "洗盘后未反包转一致"
    # ⑥ 风控：当日连续涨停≥3板不接
    consec = 0
    for i in range(cur, max(-1, cur - STRONG_CONSEC_BOARDS_MAX - 1), -1):
        pct = closes[i] / closes[i - 1] - 1 if i > 0 else 0
        if pct >= 0.095:
            consec += 1
        else:
            break
    if consec >= STRONG_CONSEC_BOARDS_MAX:
        return None, f"当日连续{consec}板高位不接"
    ma10_gap_pct = (price / ma10 - 1) * 100
    return {
        "date": rows[cur]["date"], "open": round(opens[cur], 3), "close": round(price, 3),
        "pct": round((price / closes[cur - 1] - 1) * 100, 2),
        "ma5": round(ma5, 3), "ma10": round(ma10, 3), "ma20": round(ma20, 3),
        "ma10_gap_pct": round(ma10_gap_pct, 3), "volume_ratio": round(vol_ratio, 4),
        "pullback_date": rows[pullback_idx]["date"],
        "pullback_drop_pct": round((closes[pullback_idx] / closes[pullback_idx - 1] - 1) * 100, 2),
        "consec_boards": consec, "strategy_mode": "strong_trend",
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
    n_bars = STRONG_SHAPE_BARS
    for code, meta in universe.items():
        reason = _excluded(code, meta["name"], int(meta["max_boards"]), float(meta["turnover_yi"]))
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        attempted += 1
        try:
            shape, reason = _shape_strong(bars_fetcher(code, n_bars))
        except Exception:
            shape, reason = None, "日K抓取失败"
        if not shape:
            if str(reason) in ("日K抓取失败", f"日K不足{STRONG_SHAPE_BARS}根"):
                fetch_failed += 1
            rejected[str(reason)] = rejected.get(str(reason), 0) + 1
            continue
        hot_zt = int(meta.get("theme_zt_max") or 0) or int(meta["sector_zt_count"])
        # 越接近 MA10 上方 +8% 越好；量能放大加分；热度/辨识度加分
        _hot = min(hot_zt, 8)
        _boards = min(int(meta["max_boards"]), 4) * 2
        _vol = (shape["volume_ratio"] - 1) * 10
        _pos = max(0, 5 - abs(shape["ma10_gap_pct"] - 8)) * 2
        score = round(_hot + _boards + _vol + _pos, 2)
        # 🧾 2026-11-11 用户「形态分 9.8 没有分母」：把四项分量一起带出去，
        #    卡面写「形态分 9.8（热度3·板数2·量能4.8·位置0）」——读者能看出
        #    这个分是被哪一项撑起来的，也不会再误以为接近满分。
        #    ⛔ 分量与总分同一处计算，禁止卡面自己重算（必漂移）。
        parts = {"hot": round(_hot, 2), "boards": round(_boards, 2),
                 "volume": round(_vol, 2), "position": round(_pos, 2)}
        candidates.append({**meta, **shape, "score": score, "score_parts": parts})
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
        "params": {"strategy_mode": "strong_trend",
                   "lookback_days": LOOKBACK_DAYS, "min_hot_sector_zt": MIN_HOT_SECTOR_ZT,
                   "min_hot_theme_zt": MIN_HOT_THEME_ZT,
                   "min_history_boards": MIN_HISTORY_BOARDS,
                   "strong_ma10_pos": [STRONG_MA10_POS_LOW, STRONG_MA10_POS_HIGH],
                   "strong_min_vol_ratio": STRONG_MIN_VOL_RATIO,
                   "strong_start_pct": STRONG_START_PCT,
                   "strong_pullback_drop": [STRONG_PULLBACK_DROP_LOW, STRONG_PULLBACK_DROP_HIGH],
                   "strong_consec_boards_max": STRONG_CONSEC_BOARDS_MAX},
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


# ── 仓位加权「账户当日收益」（2026-10-15 用户需求：收益须乘动态仓位）──────────
# 终端原净值把每笔结算当满仓连乘（多票日虚高）；本函数改用当日 relay 动态仓位
# 方案加权，得出「唯一当日收益」= Σ(仓位权重_i × 个股return_pct_i)。
DAILY_PNL_NAME = "ambush_pullback_daily_pnl.jsonl"


def _alloc_position_weights(n: int, per_pick_cap: float, total_cap: float) -> list[float]:
    """N 只成交的仓位权重（占账户比例）。每只=min(per_pick_cap) 且总和不超 total_cap。"""
    per_pick_cap = float(per_pick_cap) if per_pick_cap and per_pick_cap > 0 else 1.0
    total_cap = float(total_cap) if total_cap and total_cap > 0 else 1.0
    if n <= 0:
        return []
    if per_pick_cap * n <= total_cap:
        return [per_pick_cap] * n
    return [total_cap / n] * n


def build_weighted_daily_pnl(cache_dir: Path, path: Path | None = None,
                             relay_loader=None) -> list[dict[str, Any]]:
    """按决策日聚合尾盘潜伏结算，用**当日 relay 动态仓位方案**加权出「账户当日收益」。

    权重：读 decision_date 当天落盘的 relay plan（per_pick_cap/total_cap）；缺 → 中性档兜底
    （relay neutral 2只/40%/60%）并标 missing（诚实降级·不编造）。
    账户当日收益 = Σ(权重_i × 个股return_pct_i)；按 decision_date 排序复利滚 nav（base 1.0）。
    输出 ambush_pullback_daily_pnl.jsonl。
    """
    source = cache_dir / SETTLEMENT_LEDGER_NAME
    rows: list[dict] = []
    if source.exists():
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("strategy") == STRATEGY and r.get("status") == "settled":
                rows.append(r)
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get("decision_date") or ""), []).append(r)
    loader = relay_loader or _default_relay_loader
    out: list[dict] = []
    nav = 1.0
    for dd in sorted(groups.keys()):
        items = groups[dd]
        rets = [float(x.get("return_pct") or 0.0) for x in items]
        plan = loader(dd)
        if isinstance(plan, dict) and plan.get("per_pick_cap") is not None:
            ppc = float(plan["per_pick_cap"])
            tc = float(plan.get("total_cap") or 1.0)
            missing = False
        else:
            ppc, tc, missing = 0.4, 0.6, True  # 中性档兜底（relay neutral: 2只/40%/60%）
        weights = _alloc_position_weights(len(items), ppc, tc)
        weighted = sum(w * r_ for w, r_ in zip(weights, rets))
        nav = round(nav * (1 + weighted / 100.0), 6)
        out.append({
            "decision_date": dd,
            "trade_date": items[0].get("trade_date"),
            "weighted_return_pct": round(weighted, 4),
            "nav": nav,
            "stock_count": len(items),
            "plan": {"tier": plan.get("tier") if isinstance(plan, dict) else None,
                     "per_pick_cap": ppc, "total_cap": tc, "missing": missing},
            "stocks": [{"code": x.get("code"), "name": x.get("name"),
                        "return_pct": round(float(x.get("return_pct") or 0.0), 4),
                        "weight": round(weights[i], 4)} for i, x in enumerate(items)],
        })
    target = path or cache_dir / DAILY_PNL_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out) + ("\n" if out else ""),
        encoding="utf-8")
    return out


def _default_relay_loader(trade_date: str):
    try:
        from core import relay_position as _rp
        return _rp.load_daily_plan(trade_date)
    except Exception:
        return None


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


def picks_declared_empty(cache_dir: Path, decision_date: str) -> bool:
    """AI 注入文件存在且**明确声明今日不买**（`picks` 键存在但为空数组）。

    与「文件缺失 / 字段异常」严格区分：前者是 AI 判断「今天一只都不买」，此时**不得**回落
    冻结全池记账——否则会把没买的票记成成交、污染次日结算战绩（2026-10-30 实证：
    唯一候选被判定不买，回落全池会凭空产生一笔次日结算的交易）。后者才回落全池并标
    `scope="pool"`。
    """
    mmdd = str(decision_date)[5:]
    src = cache_dir / f"ambush_ai_{mmdd}.json"
    try:
        d = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(d, dict) and "picks" in d and isinstance(d.get("picks"), list) and not d["picks"]


def pick_codes(cache_dir: Path, decision_date: str) -> list[str]:
    """当日 AI 精选推票代码（`cache/ambush_ai_MM-DD.json` → `picks[].stocks[].code`）。

    与 `frozen_codes`（当日冻结全池）相对：买入价台账的口径应以**实际买入的票**为准，而非
    「当日扫出过哪些票」。文件缺失、无 picks、字段异常一律返回空列表——调用方据此回落全池，
    ⛔ 不猜、不补、不用别的票顶替。
    """
    mmdd = str(decision_date)[5:]          # YYYY-MM-DD → MM-DD（AI 注入文件命名约定）
    src = cache_dir / f"ambush_ai_{mmdd}.json"
    try:
        d = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[str] = []
    for item in d.get("picks") or []:
        if not isinstance(item, dict):
            continue
        for s in item.get("stocks") or []:
            code = str((s or {}).get("code") or "").strip()
            if code and code not in out:
                out.append(code)
    return out


def plan_entry(cache_dir: Path, decision_date: str,
               price_fetcher: Callable[[list[str]], dict] | None = None,
               out: Path | None = None, overwrite: bool = False) -> dict:
    """T 日卡推送后：抓当日实时价作**买入价**落盘（＝计入战绩的成交价）。缺价不写盘。

    ⛔ 当日已记录买入价时默认**不覆盖**（2026-10-09 用户「推出来之后就买入，计入战绩」）：
    买入价＝推送时点的价，后续时点重跑不得用更晚的价追溯改写战绩。

    取价范围（`ENTRY_PICKS_ONLY`）：默认只取**当日 AI 精选推票**（＝卡面「✅ 今日买入」那几只），
    并强制须落在当日冻结池内（池外票剔除·与卡片 `PICKS_POOL_ENFORCE` 同口径）；AI 注入文件
    缺失时回落冻结全池并置 `scope="pool"`（⛔ 不静默丢票、也不阻断台账）。
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
    scope = "pool"
    if ENTRY_PICKS_ONLY:
        if picks_declared_empty(cache_dir, decision_date):
            # AI 已明确「今日一只都不买」：不回落全池、不记假成交
            codes, scope = [], "none"
        else:
            _pool = set(codes)
            _picks = [c for c in pick_codes(cache_dir, decision_date) if c in _pool]
            if _picks:
                codes, scope = _picks, "picks"
    fetch = price_fetcher or _realtime_prices
    prices: dict[str, float] = {}
    if codes:
        try:
            prices = {str(k): float(v) for k, v in (fetch(codes) or {}).items()}
        except Exception:
            prices = {}
    payload = {"strategy": STRATEGY, "decision_date": decision_date, "slot": "entry_after_push",
               "scope": scope, "captured_at": _now().isoformat(),
               "codes": codes, "prices": prices}
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
    settled = settle(cache_dir, decision_date, trade_date, entry, exit_prices, path=path, entry_time=label)
    # 结算成功后立即重建账户级日收益，保证终端读到的不是过时净值。
    try:
        build_weighted_daily_pnl(cache_dir)
    except Exception:
        # 收益聚合失败不回滚已经完成的逐笔结算；下次 weighted-pnl 可重建。
        pass
    return settled


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
    ap = argparse.ArgumentParser(description="尾盘潜伏(强势趋势核心·分歧转一致)：扫描、冻结与独立结算")
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
    wpn = sub.add_parser("weighted-pnl", help="按动态仓位加权聚合尾盘潜伏当日账户收益")
    wpn.add_argument("--cache-dir", type=Path, default=_PROJ_ROOT / "cache")
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
        print(f"[ambush-pullback] 当日买入价（推送后实时价）：口径={res.get('scope')} · "
              f"冻结{len(res['codes'])}只 / 取到{len(res['prices'])}只 · "
              f"captured_at={res.get('captured_at')}")
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
    if args.cmd == "weighted-pnl":
        out = build_weighted_daily_pnl(args.cache_dir)
        print(f"[ambush-pullback] 加权当日收益 {len(out)} 个决策日")
        return 0
    try:
        entry = json.loads(args.entry_prices.read_text(encoding="utf-8"))
        exit_ = json.loads(args.exit_prices.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ambush-pullback] 无法读取价格文件: {exc}", file=sys.stderr)
        return 2
    settled = settle(args.cache_dir, args.decision_date, args.trade_date, entry, exit_)
    try:
        build_weighted_daily_pnl(args.cache_dir)
    except Exception:
        pass
    print(f"[ambush-pullback] 结算 {settled} 只")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
