#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观察池盘中快照 —— 让「竞价弱转强」第一次变得可见。

═══════════════════════════════════════════════════════════════════════
一、为什么必须单独做这一层（2026-11-09 用户提出）
═══════════════════════════════════════════════════════════════════════
用户原话：「竞价的时候，昨天涨停，今天开盘竞价跌停，然后慢慢的居然红开了。
我靠，这种竞价弱转强，我也是非常想要买的呀」＋「不用补全量，但是可以将我们
观察池里面的所有票全部都打上市场快照，因为大概也就只有二三十个票」。

**现有采集只看得见上涨端。** `intraday_pulse` 写底库的三个来源是：
选股宝异动池（涨速/涨停）＋ 昨日涨停池 ＋ 东财涨幅榜 —— 全部是「往上走」的名单。
一只票从跌停往红开走时，**跌的那一段它不在任何名单里**；
等它涨起来进异动池，序列的起点已经是红的。⇒ 弱转强永远看不见，
不是判断错，是**根本没有那条记录**。

实测（2026-11-09 竞价段 09:15~09:26）：
- 411 只票、740 条观测；
- 其中涨幅曾 ≤ −5% 的：**0 只**。
全市场竞价不可能一只深水票都没有 —— 采集视野里没有而己。

═══════════════════════════════════════════════════════════════════════
二、边界（⛔ 一条都不能越）
═══════════════════════════════════════════════════════════════════════
· **只采观察池**：当日 `pool_call` 池 ∪ 最近一期 `candidates` 的 relay+watch，
  上限 `MAX_CODES`。⛔ 不扩全市场（用户明确「不用补全量」）。
· **只写行情事实**：`pct` / `price` / `name`。⛔ 不写 `limit_up_now` /
  `limit_up_touched` —— 涨停闩锁是选股宝的强证据，不能被行情快照污染。
· **只读池子文件**：缺文件返回空池并说明原因，⛔ 不猜、不编、不拿别的名单顶替。
· **不做裁决**：本模块只产出「路径事实」，⛔ 不影响名额 / 仓位 / 买入 / 评分。

═══════════════════════════════════════════════════════════════════════
三、两处落盘，各答一个问题
═══════════════════════════════════════════════════════════════════════
1. `cache/watch_pool/<YYYY-MM-DD>.jsonl` —— **每轮一行**（带轮次心跳）。
   为什么不能只靠 `stock_observations`：那张表按「同日同源同值只留一条」去重，
   一只票竞价全程趴在 −10% 不动，就只会留 09:15 一条记录，读出来会误判成
   「不知道 09:25 时还在不在跌停」。⇒ 心跳必须单独留。
2. `stock_observations`（`source=intraday:watchpool`）—— 有变化才留痕，
   进入统一个股事实主干，供 `stock_profile` / 复盘分析复用。

═══════════════════════════════════════════════════════════════════════
四、竞价路径的读法（`auction_paths`，只描述不判断）
═══════════════════════════════════════════════════════════════════════
`state` 五态（两个方向都读：往上＝弱转强，往下＝转弱）：
  · `reversal`  窗口内曾 ≤ `REVERSAL_DEEP_PCT` 且末条 > 0 → 由深水转红；
  · `fade`      **09:20（不可撤单）起**的最高点 ≥ `FADE_HIGH_PCT`，且从该高点
                回撤 ≤ `FADE_DROP_PP` → 强转弱（退票线索，⛔ 不是必跌）；
  · `pending`   末条早于 09:25（不可撤单期未走完）→ **不知道**，⛔ 不算弱转强；
  · `flat`      走了全程但没有深水反转、也没跌破转弱线；
  · `no_data`   这一轮没抓到它（接口没返回 / 不在池）。
⛔ 「没记录」不等于「没变化」，也不等于「没反转」——只有心跳覆盖到 09:25 之后
才允许下 `reversal` / `fade` 结论。

⛔ **`fade` 的高点必须从 09:20 起算**：09:15~09:20 是可撤单期，首笔涨幅大多是
撤单前的虚顶（11-09 贝特利首 +19.81% / 09:20 后高 +9.29%，用首笔会算出
−13.53pp 的假回撤）。09:15 首笔到末条的大回落单独标 `虚顶回落`，不当 fade。

═══════════════════════════════════════════════════════════════════════
五、全池扫描（`scan`，2026-11-09 用户：「全部股票按同一套竞价逻辑观察筛选」）
═══════════════════════════════════════════════════════════════════════
`scan()` 用与 `auction_paths` **同一份路径事实、同一套参数**，对备选池逐票给
`grade`（S/A/B/C/D/N）＋ `basis`（带数字时点的判断依据）＋ `rank`（固定口径排序），
并挑出 `best`。⛔ 只做观察层比较：不进名额 / 仓位 / 买入 / 评分 / `pool_call` 裁决；
⛔ 不含可买性（买不到由 `ai_caliber` 的 5% 空间闸管）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:          # 直接 `python3 common/watch_pool.py` 也能跑
    sys.path.insert(0, str(_ROOT))

SCHEMA = "watch_pool_v1"
BJ = ZoneInfo("Asia/Shanghai")

ENABLE_WATCH_POOL = os.environ.get("ENABLE_WATCH_POOL_SNAPSHOT", "1") != "0"

POOL_DIR = "wealth_code/cache"       # pool_call-<date>.json / candidates-<date>.json
SNAP_DIR = "cache/watch_pool"        # 每轮心跳
REGISTRY_SOURCE = "intraday:watchpool"

AUCTION_START = "09:15"
AUCTION_END = "09:30"
AUCTION_CONFIRM = "09:25"            # 不可撤单期结束，末条过了这里才算竞价终态
REVERSAL_DEEP_PCT = -5.0             # 曾跌到这里以下才算「深水」
STALE_SECONDS = 180                  # 行情时间戳落后超过这个数 → 不写、不评
AUCTION_STALE_SECONDS = 900          # 竞价段放宽（09:15 的价到 09:24 也只有 9 分钟）
MAX_CODES = 60                       # 观察池上限（超了截断并如实报告）

# ── 转弱（fade）判据常量 ──────────────────────────────────────────────
# ⚠️ 高点必须从 09:20 起算：09:15~09:20 是可撤单期，首笔涨幅多是撤单前的虚顶，
#    拿它当高点会把「撤单」误判成「砸盘」（11-09 实测：贝特利首 +19.81%，
#    09:20 后高只 +9.29%，真实回撤 −3.01pp 而非 −13.53pp）。
FADE_WINDOW_START = "09:20"
FADE_HIGH_PCT = 3.0                  # 09:20 后高点至少到这里才算「强过」
FADE_DROP_PP = -3.0                  # 从该高点回撤到这里以下 → 转弱
REVERSAL_RED_PCT = 0.0               # 弱转强的「转红」线


def _env_num(name: str, default: float) -> float:
    """环境变量读数值；坏值回落默认（⛔ 不静默变成 0）。"""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        out = float(raw)
    except (TypeError, ValueError):
        return default
    return out if out == out and abs(out) != float("inf") else default


def params() -> dict[str, Any]:
    """竞价判据参数（**唯一源**·全部可用环境变量覆盖·可随结果一起输出便于复现）。

    ⛔ 任何消费方都从这里取，不许在自己模块里再写一遍阈值。
    """
    return {
        "window_start": os.environ.get("WP_AUCTION_START", AUCTION_START),
        "window_end": os.environ.get("WP_AUCTION_END", AUCTION_END),
        "confirm": os.environ.get("WP_CONFIRM", AUCTION_CONFIRM),
        "reversal_deep": _env_num("WP_REVERSAL_DEEP", REVERSAL_DEEP_PCT),
        "reversal_red": _env_num("WP_REVERSAL_RED", REVERSAL_RED_PCT),
        "fade_start": os.environ.get("WP_FADE_START", FADE_WINDOW_START),
        "fade_high": _env_num("WP_FADE_HIGH", FADE_HIGH_PCT),
        "fade_drop": _env_num("WP_FADE_DROP", FADE_DROP_PP),
    }


# ── 小工具 ────────────────────────────────────────────────────────────
def _root(root: str | Path | None) -> Path:
    if root is not None:
        return Path(root)
    return Path(__file__).resolve().parent.parent


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _today() -> str:
    return datetime.now(BJ).strftime("%Y-%m-%d")


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _prev_trading_day(date10: str) -> str:
    """往前找一个交易日（用统一日历，⛔ 不自算 weekday）。"""
    try:
        from common.trading_calendar import is_a_share_trading_day
    except Exception:                                    # 日历不可用 → 不猜
        return ""
    day = datetime.strptime(date10, "%Y-%m-%d")
    from datetime import timedelta
    for _ in range(15):
        day -= timedelta(days=1)
        d = day.strftime("%Y-%m-%d")
        try:
            if is_a_share_trading_day(d):
                return d
        except Exception:
            return ""
    return ""


# ── 池子来源（单一入口）────────────────────────────────────────────────
def watch_codes(*, root: str | Path | None = None,
                date: str | None = None) -> dict[str, Any]:
    """返回观察池代码表。

    优先并集：当日 `pool_call` 池 ∪ 最近一期 `candidates` 的 relay+watch。
    - 09:25 前 `pool_call-<date>.json` 还不存在 → 只有 candidates（昨日盘后选的）；
    - 09:25 后两者都在 → 并集，因为「昨天选出来要盯的」和「今天池子里的」
      本来就是同一批观察对象，⛔ 不用一个覆盖另一个。
    """
    root_p = _root(root)
    date10 = date or _today()
    pool = root_p / POOL_DIR

    codes: list[str] = []
    names: dict[str, str] = {}
    sources: list[str] = []
    asof: dict[str, str] = {}

    def _add(code: Any, name: Any = None) -> None:
        c = str(code or "").strip()
        if len(c) != 6 or not c.isdigit():
            return
        if c not in codes:
            codes.append(c)
        if name and c not in names:
            names[c] = str(name)

    # ① 当日 pool_call（终裁池：含 participate / watch 全部）
    call_path = pool / f"pool_call-{date10}.json"
    call = _load_json(call_path)
    if call.get("views"):
        for code, row in (call.get("views") or {}).items():
            if isinstance(row, dict):
                _add(code, row.get("name"))
            else:
                _add(code)
        sources.append("pool_call")
        asof["pool_call"] = str(call.get("ts") or call.get("generated_at") or date10)

    # ② 最近一期 candidates（昨日盘后预案池）
    cands = sorted(pool.glob("candidates-*.json"))
    picked = None
    for path in reversed(cands):
        d = path.stem.replace("candidates-", "")
        if d <= date10:
            picked = path
            break
    if picked is not None:
        cand = _load_json(picked)
        for key in ("relay", "watch", "low_pool"):
            for item in (cand.get(key) or []):
                if isinstance(item, dict):
                    _add(item.get("code"), item.get("name"))
        sources.append(f"candidates:{picked.stem.replace('candidates-', '')}")
        asof["candidates"] = str(cand.get("date") or "")

    truncated = 0
    if len(codes) > MAX_CODES:
        truncated = len(codes) - MAX_CODES
        codes = codes[:MAX_CODES]

    reason = ""
    if not codes:
        reason = "观察池为空（pool_call 与 candidates 都没找到可用池）"
    return {"schema": SCHEMA, "date": date10, "codes": codes, "names": names,
            "sources": sources, "asof": asof, "truncated": truncated,
            "reason": reason}


# ── 采集：一轮 ────────────────────────────────────────────────────────
def _quote_age_seconds(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BJ)
    return (now - dt.astimezone(BJ)).total_seconds()


def _stale_limit(now: datetime) -> float:
    """竞价窗口内放宽陈旧阈值。

    集合竞价只有 10 分钟，09:15 的价到 09:24 也已 9 分钟；若沿用盘中的 180s，
    会把整段竞价判成「旧价」而全部丢掉 —— 恰恰丢掉最需要的那一段。
    ⛔ 放宽不等于不判：真的拿到昨日行情（age 以小时计）照样会被拦下。
    """
    if AUCTION_START <= now.strftime("%H:%M") <= AUCTION_END:
        return AUCTION_STALE_SECONDS
    return STALE_SECONDS


def snapshot(*, root: str | Path | None = None,
             trade_date: str | None = None,
             asof_ts: str | None = None,
             codes: Sequence[str] | None = None,
             persist: bool = True) -> dict[str, Any]:
    """抓一轮观察池行情：写心跳 jsonl（每轮）+ 写 registry 观测（有变化才留）。

    `persist=False` 只取不落（供只读诊断/测试）。
    """
    root_p = _root(root)
    now = datetime.now(BJ)
    date10 = trade_date or now.strftime("%Y-%m-%d")
    stamp = asof_ts or now.isoformat(timespec="seconds")
    out: dict[str, Any] = {"schema": SCHEMA, "enabled": ENABLE_WATCH_POOL,
                           "date": date10, "ts": stamp, "ok": False,
                           "codes": 0, "quoted": 0, "written": 0,
                           "stale": [], "missing": [], "errors": []}
    if not ENABLE_WATCH_POOL:
        out["reason"] = "ENABLE_WATCH_POOL_SNAPSHOT=0"
        return out

    pool = watch_codes(root=root_p, date=date10)
    targets = [c for c in (codes or pool["codes"]) if c]
    out["codes"] = len(targets)
    out["pool_sources"] = pool["sources"]
    if not targets:
        out["reason"] = pool["reason"] or "观察池为空"
        return out

    try:
        from common.em_flow import em_flow_enabled, fetch_quotes
    except Exception as e:                                # noqa: BLE001
        out["reason"] = f"行情层不可用: {e}"
        return out
    if not em_flow_enabled():
        out["reason"] = "em_flow 开关关闭"
        return out

    try:
        quotes = fetch_quotes(targets) or {}
    except Exception as e:                                # noqa: BLE001
        quotes = {}
        out["errors"].append(f"fetch_quotes: {e}")

    rows: list[dict[str, Any]] = []
    obs: list[dict[str, Any]] = []
    stale_limit = _stale_limit(now)
    for code in targets:
        q = quotes.get(code) or {}
        pct = _num(q.get("pct"))
        price = _num(q.get("price"))
        ts_iso = q.get("source_ts_iso")
        name = str(q.get("name") or pool["names"].get(code) or "") or None
        if pct is None or price is None or price <= 0:
            out["missing"].append(code)
            continue
        age = _quote_age_seconds(ts_iso, now)
        if age is not None and age > stale_limit:
            out["stale"].append(code)          # 陈旧行情不进事实、不进心跳值
            continue
        rows.append({"code": code, "name": name, "pct": pct, "price": price,
                     "ts": ts_iso or stamp})
        obs.append({"code": code, "name": name, "pct": pct, "price": price})

    out["quoted"] = len(rows)
    if persist and rows:
        snap_dir = root_p / SNAP_DIR
        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
            with (snap_dir / f"{date10}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": stamp, "rows": rows},
                                    ensure_ascii=False) + "\n")
        except Exception as e:                            # noqa: BLE001
            out["errors"].append(f"heartbeat: {e}")

    if persist and obs:
        try:
            from common import stock_registry as registry
            res = registry.upsert_many(obs, trade_date=date10, asof_ts=stamp,
                                       source=REGISTRY_SOURCE)
            out["written"] = int(res.get("inserted") or 0) + int(res.get("updated") or 0)
            out["unchanged"] = int(res.get("unchanged") or 0)
            if not res.get("ok"):
                out["errors"].append(f"registry: {res.get('reason') or '写入未成功'}")
        except Exception as e:                            # noqa: BLE001
            out["errors"].append(f"registry: {e}")

    out["ok"] = bool(rows)
    return out


# ── 读取：竞价路径事实 ────────────────────────────────────────────────
def _rounds_from_heartbeat(root_p: Path, date10: str) -> list[dict]:
    path = root_p / SNAP_DIR / f"{date10}.jsonl"
    if not path.exists():
        return []
    rounds: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict) and isinstance(item.get("rows"), list):
                rounds.append(item)
    except Exception:
        return []
    return rounds


def _rounds_from_registry(root_p: Path, date10: str) -> list[dict]:
    """心跳文件缺失时的兜底：把观测按轮拆成同结构。

    ⚠️ 语义不同：这里「没有一条记录」只说明**值没变**，不说明没采到 ——
    所以从这条路径读出来的 `pending` 判定会更保守。
    """
    try:
        from common.stock_registry import load_auction_window
    except Exception:
        return []
    try:
        window = load_auction_window(date10)
    except Exception:
        return []
    rounds: list[dict] = []
    for code, series in (window.get("series") or {}).items():
        for item in series or []:
            if not isinstance(item, dict):
                continue
            ts = str(item.get("ts") or "")
            rounds.append({"ts": ts, "rows": [
                {"code": code, "pct": item.get("pct"),
                 "price": item.get("price"), "ts": ts}]})
    rounds.sort(key=lambda r: r["ts"])
    return rounds


def auction_paths(*, root: str | Path | None = None,
                  date: str | None = None,
                  codes: Sequence[str] | None = None,
                  window: tuple[str, str] | None = None,
                  deep_pct: float | None = None) -> dict[str, Any]:
    """观察池的竞价路径事实（只描述，不裁决）。

    每个 code 一行：`first / high / low / last` 涨幅 + 时点 + 心跳轮数 + `state`。
    `state` 见模块 docstring；⛔ `pending` 不得当 `reversal` 用。

    `high_pct` 从 `params()["fade_start"]`（默认 09:20，不可撤单期）起算；
    该段没有采样时 `high_pct/ drop_pp = None`（⛔ 不拿 09:15 虚顶顶替，也不写 0）。
    """
    root_p = _root(root)
    date10 = date or _today()
    p = params()
    pool = watch_codes(root=root_p, date=date10)
    targets = [c for c in (codes or pool["codes"]) if c]
    pool_names = pool.get("names") or {}
    rounds = _rounds_from_heartbeat(root_p, date10)
    source = "heartbeat"
    if not rounds:
        rounds = _rounds_from_registry(root_p, date10)
        source = "registry"

    start, end = window or (p["window_start"], p["window_end"])
    deep_line = p["reversal_deep"] if deep_pct is None else deep_pct
    red_line = p["reversal_red"]
    confirm_at = p["confirm"]
    fade_start = p["fade_start"]

    def _in_window(ts: Any) -> bool:
        hm = str(ts or "")[11:16]
        return bool(hm) and start <= hm <= end

    per: dict[str, list[dict]] = {}
    for rd in rounds:
        if not _in_window(rd.get("ts")):
            continue
        for row in rd.get("rows") or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "")
            if code not in targets:
                continue
            pct = _num(row.get("pct"))
            if pct is None:
                continue
            per.setdefault(code, []).append(
                {"ts": str(rd.get("ts") or row.get("ts") or ""), "pct": pct,
                 "price": _num(row.get("price")), "name": row.get("name")})

    # 心跳里这一窗口完全没有数据（例如快照是当天中午才上线的）→ 回落到观测表。
    # ⛔ 只在「窗口内一条都没有」时回落，绝不把两条来源混在一起算同一段路径。
    if not per and source == "heartbeat":
        alt = _rounds_from_registry(root_p, date10)
        if alt:
            rounds, source = alt, "registry"
            for rd in rounds:
                if not _in_window(rd.get("ts")):
                    continue
                for row in rd.get("rows") or []:
                    code = str(row.get("code") or "")
                    pct = _num(row.get("pct")) if code in targets else None
                    if pct is None:
                        continue
                    per.setdefault(code, []).append(
                        {"ts": str(rd.get("ts") or ""), "pct": pct,
                         "price": _num(row.get("price")),
                         "name": row.get("name")})

    out: list[dict[str, Any]] = []
    # ⏱️ 两种来源的「末条时间」含义完全不同，确认判据必须分开（2026-11-09 实测踩到）：
    #   · heartbeat：每轮全量写 → 末条 = 最后一次采样时点，票级 末条≥09:25 才算确认；
    #   · registry ：同源同值只留一条 → 末条 = 最后一次**变化**时点。一只票从 09:16
    #     起价格没动过，末条就永远停在 09:16 —— 若沿用票级判据，所有「稳住没动」的票
    #     全被判成 pending（今日实测 15 只里多数如此）。
    #     ⇒ 兜底源改用**全局最后采样时点**：全局已覆盖 09:25，说明这一轮采过；
    #       该票无新记录 = 值没变 = 从末条一直延续到全局末刻。
    _win_ts = [str(rd.get("ts") or "")[11:16] for rd in rounds if _in_window(rd.get("ts"))]
    global_last = max(_win_ts) if _win_ts else ""

    for code in targets:
        seq = sorted(per.get(code) or [], key=lambda r: r["ts"])
        if not seq:
            out.append({"code": code, "name": pool_names.get(code) or "",
                        "state": "no_data", "rounds": 0,
                        "first_pct": None, "first_ts": "",
                        "high_pct": None, "high_ts": "",
                        "low_pct": None, "low_ts": "",
                        "last_pct": None, "last_ts": "",
                        "span_pct": None, "drop_pp": None, "flags": [],
                        "reason": "本窗口没有抓到这只票"})
            continue
        first, last = seq[0], seq[-1]
        low = min(seq, key=lambda r: r["pct"])
        # 09:20 起（不可撤单）的最高点：转弱判据的基准。⛔ 不用 09:15 首笔虚顶。
        late = [r for r in seq if r["ts"][11:16] >= fade_start]
        high = max(late, key=lambda r: r["pct"]) if late else None
        if high is None and source == "registry":
            # 观测表同源同值只留一条 ⇒ 09:20 后没有新记录 = 值没变 = 末条值延续到全局末刻。
            # 这不是「没数据」，是「没变化」；若沿用 None 会让这类票无法参与转弱判定。
            high = last
        drop_pp = round(last["pct"] - high["pct"], 2) if high else None

        if source == "heartbeat":
            confirmed = last["ts"][11:16] >= confirm_at
            why_pending = f"末条 {last['ts'][11:16]} 早于 {confirm_at}，竞价终态未到"
        else:
            confirmed = global_last >= confirm_at
            why_pending = (f"全局最后采样 {global_last or '—'} 早于 {confirm_at}，"
                           f"竞价终态未到")

        flags: list[str] = []
        if not confirmed:
            state, why = "pending", why_pending
        elif low["pct"] <= deep_line and last["pct"] > red_line:
            state, why = "reversal", (f"曾 {low['pct']:+.2f}%（{low['ts'][11:16]}）"
                                      f"→ 末条 {last['pct']:+.2f}%")
        elif (high is not None and high["pct"] >= p["fade_high"]
              and drop_pp is not None and drop_pp <= p["fade_drop"]):
            state, why = "fade", (f"{fade_start} 后高 {high['pct']:+.2f}%（{high['ts'][11:16]}）"
                                  f"→ 末条 {last['pct']:+.2f}% · 回撤 {drop_pp:+.2f}pp")
            if last["pct"] < red_line:
                flags.append("翻绿")
        else:
            state, why = "flat", f"首 {first['pct']:+.2f}% → 末 {last['pct']:+.2f}%"
        # 「深水没爬回来」是 flat 的一个子形态，单独标出来给退票侧看（不改状态）
        if state in ("flat", "fade") and low["pct"] <= deep_line and last["pct"] <= red_line:
            flags.append("深水未回")
        # 09:15 首笔到末条的大幅回落＝可撤单期挂单被撤（⛔ 不是砸盘，但也确实说明
        # 竞价预期被下修）。与 09:20 后的 fade 分开标，避免把撤单当成卖压。
        vtop_drop = round(first["pct"] - last["pct"], 2)
        if (state == "flat" and vtop_drop >= abs(p["fade_drop"])
                and vtop_drop > 0):
            flags.append(f"虚顶回落 {vtop_drop:.2f}pp（09:15 可撤单期挂单）")

        out.append({"code": code, "name": last.get("name") or pool_names.get(code) or "",
                    "state": state, "rounds": len(seq),
                    "first_pct": first["pct"], "first_ts": first["ts"][11:16],
                    "high_pct": high["pct"] if high else None,
                    "high_ts": high["ts"][11:16] if high else "",
                    "low_pct": low["pct"], "low_ts": low["ts"][11:16],
                    "last_pct": last["pct"], "last_ts": last["ts"][11:16],
                    "span_pct": round(last["pct"] - low["pct"], 2),
                    "drop_pp": drop_pp, "flags": flags,
                    "reason": why})
    return {"schema": SCHEMA, "date": date10, "source": source,
            "rounds": len(rounds), "global_last_ts": global_last, "items": out,
            "counts": {s: sum(1 for i in out if i["state"] == s)
                       for s in ("reversal", "fade", "flat", "pending", "no_data")},
            "window": [start, end], "deep_pct": deep_line, "params": p}


def card_lines(result: dict[str, Any], *, limit: int = 6) -> list[str]:
    """给卡面用的极简描述（⛔ 不参与任何裁决）。"""
    items = [i for i in (result.get("items") or []) if i.get("state") == "reversal"]
    lines: list[str] = []
    if not items:
        counts = result.get("counts") or {}
        pending = counts.get("pending") or 0
        lines.append("竞价没有出现「深水转红」的观察池票"
                     + (f"（{pending} 只终态未确认）" if pending else ""))
        return lines
    for it in sorted(items, key=lambda x: -(x.get("span_pct") or 0))[:limit]:
        lines.append(
            f"· {it.get('name') or '—'}（{it['code']}）"
            f"最低 {it['low_pct']:+.2f}%（{it['low_ts']}）→ 竞价末 {it['last_pct']:+.2f}%"
            f" · 拉升 {it['span_pct']:+.2f}pp")
    if len(items) > limit:
        lines.append(f"…另有 {len(items) - limit} 只")
    lines.append("<font color='grey'>只描述竞价路径·不进名额不进仓位</font>")
    return lines


# ── 全池扫描：同一套竞价逻辑，逐票给结论与依据（2026-11-09 用户）─────
# 用户原话：「让备选池中的全部股票明天都按同一套竞价逻辑进行观察筛选……对备选池内
# 每只股票逐一应用该逻辑，并输出可供交易员参考的观察结果与判断依据，便于交易员据此
# 比较并识别其中表现最好的标的。竞价是基于原有选股逻辑上的进一步维度优化」。
# ⛔ 排序只用于「看谁竞价最强」，不进名额 / 仓位 / 买入 / 评分 / 排序（外部）。

GRADE_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "N": 5}
GRADE_LABEL = {"S": "深水转红", "A": "竞价走强", "B": "一般",
               "C": "转弱", "D": "深水未回", "N": "无结论"}


def grade_item(it: dict, p: dict) -> str:
    """把一只票的竞价路径压成一个档位（⛔ 仅排序用，不是评分，不进裁决）。

    判定顺序固定：无结论 → 深水转红 → 深水未回 → 转弱 → 走强 → 平稳。
    ⛔ 顺序不可调换：深水未回（`D`）比转弱（`C`）更差，不能因为它末条还红就排在转弱前面。
    """
    state = it.get("state")
    if state in ("pending", "no_data"):
        return "N"
    if state == "reversal":
        return "S"
    last = _num(it.get("last_pct"))
    low = _num(it.get("low_pct"))
    drop = _num(it.get("drop_pp"))
    if low is not None and last is not None and low <= p["reversal_deep"] and last <= p["reversal_red"]:
        return "D"
    if state == "fade":
        return "C"
    # 走强＝末条本身还站在 `fade_high` 之上，且没有跌破转弱线。
    # ⛔ 不要求「末条 ≥ 首笔」：首笔多是 09:15 撤单虚顶，任何票都比不过它，
    #    加这一条会把所有票都踢出 A（11-09 实测踩到）。
    if (last is not None and last >= p["fade_high"]
            and (drop is None or drop > p["fade_drop"])):
        return "A"
    return "B"


def _pct(v: Any) -> str:
    n = _num(v)
    return f"{n:+.2f}%" if n is not None else "—"


def _pp(v: Any) -> str:
    n = _num(v)
    return f"{n:+.2f}pp" if n is not None else "—"


def basis_text(it: dict, p: dict, grade: str) -> str:
    """一票一句判断依据（带数字与时点）——交易员据此比较，⛔ 不给无依据结论。"""
    state = it.get("state")
    if state in ("pending", "no_data"):
        return str(it.get("reason") or "没抓到")
    lines = []
    if grade == "S":
        lines.append(f"曾 {_pct(it.get('low_pct'))}（{it.get('low_ts') or '—'}）"
                     f"→ 末 {_pct(it.get('last_pct'))}（{it.get('last_ts') or '—'}），"
                     f"拉升 {_pp(it.get('span_pct'))}")
    elif grade == "D":
        lines.append(f"最低 {_pct(it.get('low_pct'))}（{it.get('low_ts') or '—'}）"
                     f"未回正，末 {_pct(it.get('last_pct'))}（{it.get('last_ts') or '—'}）")
    elif grade == "C":
        lines.append(f"{p['fade_start']} 后高 {_pct(it.get('high_pct'))}"
                     f"（{it.get('high_ts') or '—'}）→ 末 {_pct(it.get('last_pct'))}"
                     f"（{it.get('last_ts') or '—'}），回撤 {_pp(it.get('drop_pp'))}"
                     f"（转弱线 {_pp(p['fade_drop'])}）")
    elif grade == "A":
        if it.get("high_ts") and it.get("high_ts") == it.get("last_ts"):
            lines.append(f"{p['fade_start']} 后无新采样（值未变），"
                         f"末 {_pct(it.get('last_pct'))}（{it.get('last_ts') or '—'}）"
                         f"未破转弱线 {_pp(p['fade_drop'])}")
        else:
            lines.append(f"{p['fade_start']} 后高 {_pct(it.get('high_pct'))}"
                         f"（{it.get('high_ts') or '—'}）→ 末 {_pct(it.get('last_pct'))}"
                         f"（{it.get('last_ts') or '—'}），回撤 {_pp(it.get('drop_pp'))} 未破线")
    else:
        lines.append(f"首 {_pct(it.get('first_pct'))}（{it.get('first_ts') or '—'}）"
                     f"→ 末 {_pct(it.get('last_pct'))}（{it.get('last_ts') or '—'}）；"
                     f"无深水，{p['fade_start']} 后回撤未超 {_pp(p['fade_drop'])}")
    flags = [f for f in (it.get("flags") or []) if f]
    if flags:
        lines.append("·".join(flags))
    return "，".join(lines)


def _scan_sort_key(it: dict) -> tuple:
    grade = it.get("grade") or "N"
    last = _num(it.get("last_pct"))
    drop = _num(it.get("drop_pp"))
    span = _num(it.get("span_pct"))
    # 排序口径（写死在这里，便于复核）：档位 → 末条涨幅 → 回撤（越接近 0 越好）→ 拉升。
    return (GRADE_ORDER.get(grade, 9),
            -(last if last is not None else -999.0),
            -(drop if drop is not None else 0.0),
            -(span if span is not None else -999.0))


def scan(*, root: str | Path | None = None,
         date: str | None = None,
         codes: Sequence[str] | None = None,
         window: tuple[str, str] | None = None,
         deep_pct: float | None = None) -> dict[str, Any]:
    """**同一套竞价逻辑**对备选池逐票应用，输出可比较的观察结果。

    与 `auction_paths` 同源（同一份路径事实、同一套参数），只多做三件事：
    ① 每票压一个 `grade` 档位；② 每票写一句 `basis` 判断依据；
    ③ 按固定口径排序给 `rank`，并挑出 `best`（竞价表现最好的那只）。

    ⛔ 只做观察层：不进名额 / 仓位 / 买入 / 评分 / `pool_call` 裁决。
    ⛔ 默认**不落盘**（要留档显式传 `--out`）。
    """
    base = auction_paths(root=root, date=date, codes=codes,
                         window=window, deep_pct=deep_pct)
    p = base.get("params") or params()
    items: list[dict[str, Any]] = []
    for raw in base.get("items") or []:
        it = dict(raw)
        it["grade"] = grade_item(it, p)
        it["grade_label"] = GRADE_LABEL[it["grade"]]
        it["basis"] = basis_text(it, p, it["grade"])
        items.append(it)
    items.sort(key=_scan_sort_key)
    rank = 0
    for it in items:
        if it["grade"] == "N":
            it["rank"] = None
        else:
            rank += 1
            it["rank"] = rank

    ranked = [it for it in items if it.get("rank")]
    best = None
    if ranked:
        b = ranked[0]
        best = {"code": b["code"], "name": b.get("name") or "",
                "grade": b["grade"], "grade_label": b["grade_label"],
                "last_pct": b.get("last_pct"), "drop_pp": b.get("drop_pp"),
                "span_pct": b.get("span_pct"), "basis": b["basis"]}
    counts = base.get("counts") or {}
    return {"schema": "watch_pool_scan_v1", "date": base.get("date"),
            "source": base.get("source"), "rounds": base.get("rounds"),
            "global_last_ts": base.get("global_last_ts"),
            "params": p, "window": base.get("window"),
            "pool_size": len(items),
            "coverage": {"pool": len(items),
                         "confirmed": sum(1 for i in items if i["grade"] != "N"),
                         "pending": int(counts.get("pending") or 0),
                         "no_data": int(counts.get("no_data") or 0)},
            "counts": counts, "items": items, "best": best,
            "note": "只做竞价观察层排序·不进名额/仓位/买入/评分/裁决·不含可买性（买不到由 ai_caliber 5% 空间闸管）"}


def scan_lines(result: dict[str, Any], *, limit: int | None = None) -> list[str]:
    """扫描结果的文本表（给终端/脚本直接看）。"""
    p = result.get("params") or {}
    cov = result.get("coverage") or {}
    deep = p.get("reversal_deep")
    red = p.get("reversal_red")
    lines = [
        f"竞价扫描 {result.get('date') or '—'} · 池 {cov.get('pool', 0)} 只 · "
        f"有结论 {cov.get('confirmed', 0)} · 未确认 {cov.get('pending', 0)} · "
        f"没抓到 {cov.get('no_data', 0)} · 来源 {result.get('source') or '—'}",
        f"参数：窗口 {p.get('window_start')}~{p.get('window_end')}"
        f"｜确认 {p.get('confirm')}"
        + (f"｜深水 {deep:g}%" if isinstance(deep, (int, float)) else "")
        + (f"｜转红 {red:g}%" if isinstance(red, (int, float)) else ""),
        f"　　　转弱：{p.get('fade_start')} 起高点 ≥{p.get('fade_high')}%"
        f" 且回撤 ≤{p.get('fade_drop')}pp",
    ]
    items = result.get("items") or []
    if limit:
        items = items[:limit]
    for it in items:
        rk = it.get("rank")
        head = f"{'#' + str(rk):>3s}" if rk else "  —"
        span = _num(it.get("span_pct"))
        extra = f" 拉升 {_pp(span)}" if span is not None and span > 0 else ""
        lines.append(
            f"{head} [{it.get('grade')} {it.get('grade_label')}] "
            f"{it.get('name') or '—'}（{it.get('code')}）"
            f" 末 {_pct(it.get('last_pct'))}{extra} "
            f"回撤 {_pp(it.get('drop_pp'))}")
        lines.append(f"      依据：{it.get('basis')}")
    best = result.get("best")
    if best:
        lines.append(f"最强：{best.get('name') or '—'}（{best.get('code')}）"
                     f" {best.get('grade_label')} · {best.get('basis')}")
    lines.append(result.get("note") or "")
    return lines



# ── CLI ───────────────────────────────────────────────────────────────
def main(argv: Iterable[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="观察池盘中快照 / 竞价观察扫描（只读事实）")
    sub = ap.add_subparsers(dest="cmd")

    p_snap = sub.add_parser("snapshot", help="抓一轮观察池行情并落盘")
    p_snap.add_argument("--date")
    p_snap.add_argument("--dry-run", action="store_true")

    p_path = sub.add_parser("paths", help="读竞价路径事实")
    p_path.add_argument("--date")
    p_path.add_argument("--json", action="store_true")

    p_list = sub.add_parser("pool", help="看观察池代码表")
    p_list.add_argument("--date")

    p_scan = sub.add_parser("scan", help="全池按同一套竞价逻辑逐票筛选（可比较·只读）")
    p_scan.add_argument("--date")
    p_scan.add_argument("--json", action="store_true")
    p_scan.add_argument("--out", help="另存 JSON 到该路径（默认不落盘）")

    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.cmd == "snapshot":
        res = snapshot(trade_date=args.date, persist=not args.dry_run)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.cmd == "pool":
        print(json.dumps(watch_codes(date=args.date), ensure_ascii=False, indent=2))
    elif args.cmd == "scan":
        res = scan(date=args.date)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print("\n".join(scan_lines(res)))
    else:
        res = auction_paths(date=getattr(args, "date", None))
        if getattr(args, "json", False):
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"观察池 {len(res['items'])} 只 · 心跳 {res['rounds']} 轮 · "
                  f"来源 {res['source']} · 计数 {res['counts']}")
            for it in res["items"]:
                print(f"  [{it['state']:8s}] {it.get('name') or '':8s} {it['code']} "
                      f"{it.get('reason') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
