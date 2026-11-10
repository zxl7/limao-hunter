#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙头基因沉淀池（2026-11-10 用户定义）

## 规则（用户原话，逐条落到本文件）

1. **入选**：连续涨停达到或超过 **4 板**的股票，自动纳入沉淀池；
2. **强票**：**5 板及以上**视为有望突破板块、成为市场情绪龙头的强票（打 `strong` 标）；
3. **退潮跟踪**：断板（不再涨停）后的 **两个交易日内**，累计跌幅不得超过 **10%**（温和撤退）；
4. **淘汰**：断板后两日内跌超 10%（**A 杀**）→ 判定不具备龙头属性，标记 `eliminated`。

## 口径（⛔ 全部只读本地归档，禁编造、禁未来函数）

| 项 | 口径 |
|---|---|
| 交易日序列 | `cache/zt_history/zt_<YYYY-MM-DD>.json` 的**归档日**（有归档=有数据，⛔ 不按日历猜） |
| 当日涨停明细 | 同上文件的 `zt_list`（含 `boards` / `fbt` / `zbc` / `sector`） |
| 连续段（episode） | 相邻交易日、板数逐日 +1；断档或板数归 1 → **新的一段**（N 字二波算新一代） |
| 入选日 | 该段中**首次** `boards >= 4` 的交易日 |
| 断板日 | 该段最后一个涨停日的**下一个交易日**（当天不在涨停池） |
| 两日跌幅 | 以「最后一个涨停日收盘」为基准，取断板日 D1、D1 后一个交易日 D2 收盘：`close(D2)/close(最后涨停日) − 1` |
| A 杀 | `两日跌幅 <= -10%` |
| 价格来源 | `cache/kline_qfq/<code>.json` 收盘价；缺失回落到 `zt_list.price`（最后涨停日收盘）并在 `price_note` 里写明 |
| 未满两日 / 停牌缺 K 线 | `status = "pending"`，⛔ 不判死、不补 0 |

## 状态机

```
tracking   还在涨停（段未结束）        → 继续跟
pending    已断板，但不足两个交易日     → 等数据，不判
retained   满两日且跌幅 > -10%         → 保留（温和撤退，符合龙头属性）
eliminated 满两日且跌幅 <= -10%（A 杀） → 淘汰（记录保留，不物理删除）
```

⛔ 淘汰只改状态、**不删记录**：池子的价值在「历史样本 → 存活率」，删了就统计不出来。
⛔ 同一只票淘汰后再次走出 4 板 → 作为**新的一段**重新入池（`generation` 递增）。

用法：
    python3 scripts/dragon_gene.py            # 用全部归档重建 → cache/dragon_gene.json
    python3 scripts/dragon_gene.py --days 60  # 只看最近 N 个归档日
    python3 scripts/dragon_gene.py --json     # 输出机器可读
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"
ZT_DIR = CACHE / "zt_history"
KLINE_DIR = CACHE / "kline_qfq"
OUT = CACHE / "dragon_gene.json"

ENTRY_BOARDS = 4          # 入选门槛
STRONG_BOARDS = 5         # 强票门槛
A_KILL_PCT = -10.0        # 绝对口径（10cm 票的 A 杀阈值；也作为对照字段保留）
# 🆕 2026-11-10 用户「可以区分口径」：A 杀按**该票一个跌停板的幅度**归一。
#   10cm → −10%；20cm（创业板/科创板）→ −20%；ST → −5%；北交所 → −30%。
#   含义一致：**两个交易日内跌掉超过一个跌停板** = A 杀。绝对 −10% 仍写进
#   `a_kill_abs10` 作对照，方便回看两种口径的差异。
A_KILL_RATIO = 1.0        # 阈值 = −1.0 × 单日跌停幅度


def limit_pct(code: str, name: str | None = None) -> float:
    """该票的单日涨跌停幅度（%）。⛔ 只按代码/名称推断，取不到就按 10cm。"""
    n = str(name or "").upper()
    c = str(code or "")
    if "ST" in n:
        return 5.0
    if c.startswith(("300", "301", "688", "689")):
        return 20.0
    if c.startswith(("8", "4")) or (c.startswith("9") and not c.startswith(("90",))):
        return 30.0
    return 10.0


def _bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def trading_days() -> list[str]:
    """归档日即交易日序列（⛔ 不按日历猜）。"""
    return sorted(p.stem.replace("zt_", "") for p in ZT_DIR.glob("zt_*.json"))


def load_zt(day: str) -> dict[str, dict] | None:
    """返回当日涨停明细；**文件缺 `zt_list` 键 → None（不完整归档）**。

    ⛔ `zt_list: []`（真的有 0 家涨停）与「盘中文件没有逐票名单」必须区分：
       前者是**有效数据**（该日 0 涨停），后者是**未知**。把未知当 0 会凭空判断板。
    """
    p = ZT_DIR / f"zt_{day}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return None
    if "zt_list" not in d:
        return None
    out: dict[str, dict] = {}
    for r in d.get("zt_list") or []:
        code = str(r.get("code") or "").strip()
        if code:
            out[code] = r
    return out


_BARS: dict[str, list[dict]] = {}


def bars(code: str) -> list[dict]:
    if code not in _BARS:
        rows: list[dict] = []
        p = KLINE_DIR / f"{code}.json"
        if p.exists():
            try:
                rows = [b for b in (json.loads(p.read_text()).get("bars") or [])
                        if b.get("date") and b.get("close")]
            except Exception:                          # noqa: BLE001
                rows = []
        rows.sort(key=lambda b: str(b["date"])[:10])
        _BARS[code] = rows
    return _BARS[code]


def _bar_index(code: str, day: str) -> int | None:
    for i, b in enumerate(bars(code)):
        if str(b["date"])[:10] == day:
            return i
    return None


def _close(code: str, day: str) -> float | None:
    i = _bar_index(code, day)
    if i is None:
        return None
    c = float(bars(code)[i].get("close") or 0)
    return c if c > 0 else None


def build_episodes(days: list[str], zt: dict[str, dict[str, dict]]) -> list[dict]:
    """按「相邻交易日 + 板数逐日 +1」切连续段。"""
    episodes: list[dict] = []
    open_ep: dict[str, dict] = {}          # code → 正在进行的段
    for i, d in enumerate(days):
        today = zt.get(d) or {}
        for code, row in today.items():
            b = int(row.get("boards") or 0)
            ep = open_ep.get(code)
            if ep is not None and ep["last_day_idx"] == i - 1 and b == ep["last_boards"] + 1:
                ep["last_day_idx"] = i
                ep["last_day"] = d
                ep["last_boards"] = b
                ep["max_boards"] = max(ep["max_boards"], b)
                ep["rows"][d] = row
            else:
                ep = {"code": code, "name": row.get("name") or "",
                      "start_idx": i, "start_day": d, "last_day_idx": i, "last_day": d,
                      "first_boards": b, "last_boards": b, "max_boards": b,
                      "sector": row.get("sector") or "", "rows": {d: row}}
                episodes.append(ep)
                open_ep[code] = ep
        # 今天没涨停的票：段在当前时点隐式结束（不需要显式关闭，
        # 因为下次出现时 `last_day_idx != i-1` 会自动开新段）
    return episodes


def evaluate(ep: dict, days: list[str]) -> dict:
    """把一段连板行情判成 tracking / pending / retained / eliminated。"""
    entry_day = None
    entry_boards = None
    for d, row in ep["rows"].items():
        if int(row.get("boards") or 0) >= ENTRY_BOARDS:
            entry_day, entry_boards = d, int(row.get("boards") or 0)
            break
    base = {"code": ep["code"], "name": ep["name"], "sector": ep["sector"],
            "start_day": ep["start_day"], "last_day": ep["last_day"],
            "max_boards": ep["max_boards"], "days_in_streak": len(ep["rows"]),
            "entry_day": entry_day, "entry_boards": entry_boards,
            "strong": ep["max_boards"] >= STRONG_BOARDS}
    if entry_day is None:                  # 这一段没到 4 板 → 不入池
        return {}

    last_idx = ep["last_day_idx"]
    # 段仍在最后一天 → 还在涨停
    if last_idx == len(days) - 1:
        return {**base, "status": "tracking", "current_boards": ep["last_boards"],
                "break_day": None, "post_2d_pct": None, "a_kill": None,
                "d1_pct": None, "d2_pct": None, "price_note": None}

    break_day = days[last_idx + 1]
    base.update({"break_day": break_day})
    i0 = _bar_index(ep["code"], ep["last_day"])
    base_close = _close(ep["code"], ep["last_day"])
    note = None
    if base_close is None:
        px = float((ep["rows"].get(ep["last_day"]) or {}).get("price") or 0)
        base_close = px if px > 0 else None
        note = "基准价取 zt_list.price（K 线缺该日）"

    out = {**base, "price_note": note, "d1_pct": None, "d2_pct": None,
           "post_2d_pct": None, "a_kill": None, "status": "pending"}
    if base_close is None or i0 is None:
        out["price_note"] = (note or "") + "｜无基准价，无法计算两日跌幅"
        return out

    seq = bars(ep["code"])
    d1 = seq[i0 + 1] if i0 + 1 < len(seq) else None
    d2 = seq[i0 + 2] if i0 + 2 < len(seq) else None
    if d1 is not None:
        c1 = float(d1["close"])
        out["d1_pct"] = round((c1 / base_close - 1) * 100, 2)
        out["break_day"] = str(d1["date"])[:10]     # 以真实交易日为准（停牌自动顺延）
    if d2 is not None:
        c2 = float(d2["close"])
        out["d2_pct"] = round((c2 / base_close - 1) * 100, 2)
        out["post_2d_pct"] = out["d2_pct"]
        lim = limit_pct(ep["code"], ep["name"])
        thr = -round(lim * A_KILL_RATIO, 2)
        out["limit_pct"] = lim
        out["a_kill_threshold"] = thr
        out["a_kill_abs10"] = out["post_2d_pct"] <= A_KILL_PCT      # 绝对 −10% 对照
        out["a_kill"] = out["post_2d_pct"] <= thr
        out["status"] = "eliminated" if out["a_kill"] else "retained"
    return out


def build(days: list[str] | None = None) -> dict:
    all_days = days or trading_days()
    zt = {d: load_zt(d) for d in all_days}
    # ⛔ 只有「有 zt_list 的归档」才是完整收盘快照：盘中文件（intraday_only=true）没有逐票名单，
    #    若把当天当「不在涨停池」会凭空判出断板（2026-11-10 10:41 实测踩到）。
    days = [d for d in all_days if zt.get(d) is not None]
    skipped = [d for d in all_days if zt.get(d) is None]
    if not days:
        return {"schema": "dragon_gene.v1", "generated_at": _bj().isoformat(timespec="seconds"),
                "window": [], "data_as_of": None, "skipped_days": skipped,
                "rules": {}, "summary": {}, "pool": []}
    eps = build_episodes(days, zt)
    pool: list[dict] = []
    for ep in eps:
        row = evaluate(ep, days)
        if row:
            pool.append(row)

    # generation：同一代码第几次入池（淘汰后再走出 4 板 = 新一代）
    seen: dict[str, int] = {}
    for r in sorted(pool, key=lambda x: (x["code"], x["entry_day"] or "")):
        seen[r["code"]] = seen.get(r["code"], 0) + 1
        r["generation"] = seen[r["code"]]
    pool.sort(key=lambda r: (r["entry_day"] or "", -r["max_boards"]))

    def cnt(st: str) -> int:
        return sum(1 for r in pool if r["status"] == st)

    decided = [r for r in pool if r["status"] in ("retained", "eliminated")]
    strong = [r for r in pool if r["strong"]]
    return {
        "schema": "dragon_gene.v1",
        "generated_at": _bj().isoformat(timespec="seconds"),
        "window": [days[0], days[-1]] if days else [],
        "data_as_of": days[-1] if days else None,
        "skipped_days": skipped,
        "rules": {
            "entry_boards": ENTRY_BOARDS,
            "strong_boards": STRONG_BOARDS,
            "a_kill_pct": A_KILL_PCT,
            "a_kill_mode": "normalized_by_limit",
            "a_kill_ratio": A_KILL_RATIO,
            "a_kill_note": "阈值 = 一个跌停板：10cm→−10% / 20cm→−20% / ST→−5% / 北交所→−30%",
            "post_break_days": 2,
            "price_basis": "最后涨停日收盘 → 断板后第 2 个交易日收盘",
        },
        "summary": {
            "entered": len(pool),
            "strong": len(strong),
            "tracking": cnt("tracking"),
            "pending": cnt("pending"),
            "retained": cnt("retained"),
            "eliminated": cnt("eliminated"),
            "decided": len(decided),
            "retain_rate": (round(100 * cnt("retained") / len(decided), 1) if decided else None),
            "a_kill_rate": (round(100 * cnt("eliminated") / len(decided), 1) if decided else None),
        },
        "pool": pool,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 个归档日（0=全部）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    days = trading_days()
    if not days:
        print("[dragon_gene] 没有 zt_history 归档，无法构建", file=sys.stderr)
        return 2
    if a.days > 0:
        days = days[-a.days:]

    data = build(days)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    s = data["summary"]
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        _skip = f"｜跳过不完整归档 {len(data.get('skipped_days') or [])} 天" if data.get("skipped_days") else ""
        print(f"[dragon_gene] 数据截至 {data.get('data_as_of')}（{data['window'][0]} ~ {data['window'][1]}）"
              f"{_skip}｜入池 {s['entered']} 只"
              f"（强票 {s['strong']}）")
        print(f"  在途 {s['tracking']} · 待判 {s['pending']} · 保留 {s['retained']}"
              f" · 淘汰 {s['eliminated']}｜保留率 {s['retain_rate']}% · A杀率 {s['a_kill_rate']}%")
        print(f"  已落盘 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
