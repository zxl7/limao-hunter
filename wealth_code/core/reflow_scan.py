#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股「断板回流」扫描模块（方案1 · 2026-09-17 用户拍板）。

【为什么有这个东西】
用户原话：「中京电子涨停之后的这个曲线，我觉得就可以作为『趋势推测回流』的一个标准。
你就没有命中呀。」核查确认：本系统此前的「回流」只有 **题材相位回流**（theme_rotation
识别 boom→cooling→re-entry），是个板块维度；而「个股涨停后休整、资金回流再走强」这种
**个股维度**形态完全没有覆盖。样本中京电子（002579）：09-14 首板 → 09-15 断板(-1.01%)
→ 09-16 +5.65%(未封) → 09-17 +6.82%，标准「断板回流」，潜伏卡当日 0 命中。

本模块 = 个股维度扫描，与题材回流**并列不互斥**，补上这块盲区。

【口径】（全部可用本地涨停快照 + 实时行情复算，不硬编码任何板块规律）
  候选宇宙 = 最近 LOOKBACK 个交易日「曾涨停」的个股并集（cache/zt_history/zt_*.json）
  入选条件 = 今日未涨停（pct < 该股涨停阈值）且今日走强（pct ≥ MIN_TODAY_PCT）
             且距最近涨停日 gap ∈ [1, MAX_GAP]
  硬性排除 = ST/*ST、科创板 688*、北交所/B股、曾 ≥ EXCLUDE_BOARDS 板的高位妖股
  排序权重 = 断板越近越优先 + 今日强度 + 曾连板辨识度 + 所属板块今日资金共振

【数据源】
  cache/zt_history/zt_YYYY-MM-DD.json   多日涨停快照（权威·每日盘后落盘）
  common/em_flow.fetch_quotes()         东财 ulist 批量实时行情（60s 缓存·失败降级返回空）
  cache/pulse_snapshots/latest.json     今日强势板块（共振加权·可选）

【产物】
  cache/reflow_pool.json                机器可读候选池（潜伏卡渲染块 + AI 选材池共用）

⛔ 本模块只做**客观筛选**，不预判涨跌；输出仅供展示研判，不构成自动买入信号。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# 项目根（狸猫猎手）加入 sys.path，保证 `from common import em_flow` 可用。
# 与 adapters/input_auction.py 同款写法：生产由 automation 直接 `python3 wealth_code/.../x.py`
# 运行，此时 sys.path[0] 是脚本目录而非 cwd，必须显式补项目根。
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

BJ_TZ = timezone(timedelta(hours=8))

# ── 可逆开关 / 参数（改口径只动这里；置 ENABLED=False 即整体回滚行为）──────────
REFLOW_ENABLED = True        # 总开关
REFLOW_LOOKBACK_DAYS = 5     # 回看交易日数（候选宇宙）
REFLOW_MAX_GAP = 3           # 断板窗口上限：距最近涨停日的交易日数（1=昨涨停今断板）
REFLOW_MIN_TODAY_PCT = 2.0   # 今日走强门槛（%），低于此视为「还没回流」
REFLOW_EXCLUDE_BOARDS = 5    # 曾 ≥N 板的高位妖股不再进潜伏位（方案A ② 铁律）
REFLOW_TOP_N = 10            # 候选池默认精选条数
REFLOW_MAX_LT_YI = 0.0       # 流通市值上限（亿）；0 = 不限制
REFLOW_QUOTE_CHUNK = 80      # 批量行情单请求代码数上限（防 URL 过长）
ZT_PCT_10, ZT_PCT_20 = 9.8, 19.8   # 涨停判定阈值（10cm / 20cm），与既有 _pool_today_status 同口径


# ────────────────────────────── 基础读取 ──────────────────────────────
def _today_str() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d")


def list_zt_days(zt_dir: Path) -> list[str]:
    """zt_history 目录下所有快照日期（升序）。即本系统记录的交易日序列。"""
    days: list[str] = []
    for p in zt_dir.glob("zt_*.json"):
        name = p.stem[3:]                      # zt_2026-09-16 → 2026-09-16
        if len(name) == 10 and name[4] == "-":
            days.append(name)
    return sorted(set(days))


def _load_zt_list(path: Path) -> list[dict]:
    """单日涨停名单；缺失/盘中未落全（intraday_only）时返回 []。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(d, dict):
        return []
    rows = d.get("zt_list")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _excluded(code: str, name: str) -> bool:
    """硬性不可买/不在能力圈：ST、科创板 688（无权限）、北交所/B股。"""
    c = str(code).strip()
    nm = str(name or "")
    if "ST" in nm.upper():
        return True
    if c.startswith(("688", "689")):           # 科创板（用户无权限）
        return True
    if c.startswith(("4", "8", "9")):          # 北交所 / B股
        return True
    return False


def _strong_sectors(cache_dir: Path) -> set[str]:
    """今日资金/热度强势板块名集合（用于共振加权；取不到返回空集，不报错）。"""
    out: set[str] = set()
    try:
        lv = json.loads((cache_dir / "pulse_snapshots" / "latest.json").read_text(encoding="utf-8"))
    except Exception:
        return out
    sr = (lv or {}).get("sector_risk") or {}
    for s in (sr.get("inflow_top") or []):
        if s.get("name"):
            out.add(str(s["name"]))
    for hs in ((lv or {}).get("hot_spots") or []):
        if hs.get("sector"):
            out.add(str(hs["sector"]))
    for b in ((lv or {}).get("top5") or []):
        if b.get("name"):
            out.add(str(b["name"]))
    return out


def _fetch_quotes(codes: list[str]) -> dict[str, dict]:
    """东财 ulist 批量实时行情；任何异常 → 返回已取到的部分（调用方防御）。"""
    if not REFLOW_ENABLED or not codes:
        return {}
    try:
        from common import em_flow
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for i in range(0, len(codes), REFLOW_QUOTE_CHUNK):
        chunk = codes[i:i + REFLOW_QUOTE_CHUNK]
        try:
            out.update(em_flow.fetch_quotes(chunk) or {})
        except Exception:
            continue
    return out


# ────────────────────────────── 核心扫描 ──────────────────────────────
def _build_universe(zt_dir: Path, days: list[str], today: str,
                    lookback: int) -> dict[str, dict]:
    """最近 lookback 个交易日「曾涨停」并集（不含今日盘中名单，今日无完整名单）。"""
    if today not in days:
        days = sorted(set(days) | {today})
    today_idx = days.index(today)
    meta: dict[str, dict] = {}
    for d in days:
        if d == today:
            continue                            # 今日盘中 intraday_only，无个股名单
        if days.index(d) > today_idx:
            continue                            # 回测防护：未来日不参与（today 可传入历史日）
        if today_idx - days.index(d) > lookback:
            continue
        for r in _load_zt_list(zt_dir / f"zt_{d}.json"):
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            it = meta.get(code)
            if it is None:
                meta[code] = {
                    "code": code,
                    "name": r.get("name") or "",
                    "sector": r.get("sector") or "",
                    "max_boards": int(r.get("boards") or 0),
                    "last_date": d,
                    "is_20cm": bool(r.get("is_20cm")),
                    "lt_yi": float(r.get("lt_yi") or 0.0),
                    "seal_yi": float(r.get("seal") or 0.0),
                }
            else:
                it["max_boards"] = max(it["max_boards"], int(r.get("boards") or 0))
                if d >= it["last_date"]:        # 以最近一次涨停的属性为准
                    it["last_date"] = d
                    it["name"] = r.get("name") or it["name"]
                    it["sector"] = r.get("sector") or it["sector"]
                    it["is_20cm"] = bool(r.get("is_20cm"))
                    it["lt_yi"] = float(r.get("lt_yi") or it["lt_yi"])
                    it["seal_yi"] = float(r.get("seal") or it["seal_yi"])
    return meta


def _gap_tag(gap: int) -> str:
    if gap <= 1:
        return "昨日涨停·今日断板回流"
    return f"{gap}天前涨停·今日资金回流"


def _score_row(gap: int, pct: float, max_boards: int, resonance: bool) -> float:
    gap_score = {1: 30.0, 2: 20.0, 3: 10.0}.get(gap, 5.0)   # 断板越近越优先
    strength = min(float(pct), 9.9) * 2.0                    # 今日强度
    ident = 8.0 if max_boards >= 2 else 0.0
    ident += 4.0 if max_boards >= 3 else 0.0                 # 曾连板辨识度
    res_score = 6.0 if resonance else 0.0                    # 板块今日资金共振
    return round(gap_score + strength + ident + res_score, 2)


def scan_reflow(cache_dir: Path,
                quotes: Optional[dict[str, dict]] = None,
                today: Optional[str] = None,
                top_n: Optional[int] = None,
                lookback: Optional[int] = None,
                max_gap: Optional[int] = None) -> dict[str, Any]:
    """执行扫描。返回 dict（见模块 docstring 产物说明）。

    quotes 可注入（测试/复用），缺省走东财批量行情。
    """
    now = today or _today_str()
    n_lb = REFLOW_LOOKBACK_DAYS if lookback is None else lookback
    gap_cap = REFLOW_MAX_GAP if max_gap is None else max_gap
    n_top = REFLOW_TOP_N if top_n is None else top_n
    zt_dir = cache_dir / "zt_history"

    days = list_zt_days(zt_dir)
    if now not in days:
        days = sorted(set(days) | {now})
    today_idx = days.index(now)

    universe_all = _build_universe(zt_dir, days, now, n_lb)
    universe: dict[str, dict] = {}
    dropped: dict[str, int] = {"st_or_kc": 0, "high_board": 0, "big_cap": 0}
    for code, v in universe_all.items():
        if _excluded(code, v["name"]):
            dropped["st_or_kc"] += 1
            continue
        if REFLOW_EXCLUDE_BOARDS is not None and v["max_boards"] >= REFLOW_EXCLUDE_BOARDS:
            dropped["high_board"] += 1
            continue
        if REFLOW_MAX_LT_YI and REFLOW_MAX_LT_YI > 0 and not (0 < v["lt_yi"] <= REFLOW_MAX_LT_YI):
            dropped["big_cap"] += 1
            continue
        universe[code] = v

    if quotes is None:
        quotes = _fetch_quotes(list(universe.keys()))
    strong = _strong_sectors(cache_dir)

    rows: list[dict] = []
    no_quote = 0
    for code, v in universe.items():
        q = quotes.get(code)
        if not q:
            no_quote += 1
            continue
        pct = float(q.get("pct") or 0.0)
        zt_th = ZT_PCT_20 if v["is_20cm"] else ZT_PCT_10
        if pct >= zt_th:                        # 今日已涨停 → 已启动，非潜伏（方案A ③）
            continue
        if pct < REFLOW_MIN_TODAY_PCT:          # 今日未走强 → 还没回流
            continue
        gap = today_idx - days.index(v["last_date"])
        if gap < 1 or gap > gap_cap:
            continue
        resonance = bool(v["sector"]) and any(
            v["sector"] in s or s in v["sector"] for s in strong
        )
        rows.append({
            "code": code,
            "name": q.get("name") or v["name"],
            "sector": v["sector"],
            "last_zt_date": v["last_date"],
            "gap_days": gap,
            "max_boards": v["max_boards"],
            "today_pct": round(pct, 2),
            "lt_yi": round(v["lt_yi"], 1),
            "is_20cm": v["is_20cm"],
            "tag": _gap_tag(gap),
            "resonance": resonance,
            "score": _score_row(gap, pct, v["max_boards"], resonance),
        })
    rows.sort(key=lambda r: -r["score"])

    return {
        "generated_ts": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": now,
        "params": {
            "lookback_days": n_lb, "max_gap": gap_cap,
            "min_today_pct": REFLOW_MIN_TODAY_PCT,
            "exclude_boards": REFLOW_EXCLUDE_BOARDS,
            "max_lt_yi": REFLOW_MAX_LT_YI,
        },
        "stats": {
            "universe": len(universe),
            "excluded": dropped,
            "no_quote": no_quote,
            "hits": len(rows),
        },
        "candidates": rows[:n_top],
        "pool": rows,
    }


def write_pool(cache_dir: Optional[Path] = None, result: Optional[dict] = None) -> Path:
    """写 cache/reflow_pool.json（原子写：临时文件 + replace）。"""
    cache_dir = cache_dir or (_PROJ_ROOT / "cache")
    result = result if result is not None else scan_reflow(cache_dir)
    path = cache_dir / "reflow_pool.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="个股断板回流扫描")
    ap.add_argument("--write", action="store_true", help="写 cache/reflow_pool.json")
    ap.add_argument("--top", type=int, default=REFLOW_TOP_N)
    ap.add_argument("--lookback", type=int, default=REFLOW_LOOKBACK_DAYS)
    ap.add_argument("--max-gap", type=int, default=REFLOW_MAX_GAP)
    args = ap.parse_args(argv)
    cd = _PROJ_ROOT / "cache"
    res = scan_reflow(cd, top_n=args.top, lookback=args.lookback, max_gap=args.max_gap)
    print(f"[reflow] {res['trade_date']} 宇宙 {res['stats']['universe']} 只 · "
          f"命中 {res['stats']['hits']} 只 · 无行情 {res['stats']['no_quote']} 只 · "
          f"排除 {res['stats']['excluded']}")
    for r in res["candidates"]:
        print(f"  {r['name']}({r['code']}) {r['sector']} · {r['tag']} · 今日+{r['today_pct']}% "
              f"· 曾{r['max_boards']}板 · {r['last_zt_date']} · score={r['score']}")
    if args.write:
        p = write_pool(cd, res)
        print("[reflow] 已写", p)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
