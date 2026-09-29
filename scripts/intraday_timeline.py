#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中情绪时间轴记录器（2026-09-17 用户「要记录整个交易时间的情绪」新增）
=====================================================================
## 为什么需要它
用户要的时间轴**不是收盘快照**，而是「**整个 A 股交易时间的短线情绪起落**」：
既有题材板块的**爆发**、也有板块的**杀跌**；既有个股的**涨**、也有个股的**跌**。

而系统原有落盘全是**收盘口径**：
  · `zt_history/zt_<date>.json` 的 `zt_list`（含首封时刻）**收盘后才写**；
  · `zt_history.intraday_points` 只有**每 30 分钟**一个采样点，且**只有涨停一侧**；
  · `latest.json` / `prev.json` 每轮**覆盖写** → 盘中历史**全部丢失**。

→ 本脚本补上缺口：**每轮把「本轮新发生的情绪事件」追加到按日切分的 JSONL**，
   up/down 双向、带真实时刻，供时间轴页面逐条还原。

## ⛔ 硬约束（不可破）
1. **纯记录，零推送** —— 绝不调用 push_card，绝不改任何推送行为/节奏。
2. **任何异常都不得影响守护链** —— 整体 try 包裹，失败仅打印。
3. **追加写，绝不覆盖** —— 历史不可丢（覆盖写正是原设计的问题）。
4. **不编造** —— 只记录 `latest.json` 里**真实存在**的字段；拿不到的不写。

## 数据来源与诚实边界
| 方向 | 来源字段 | 覆盖 |
|---|---|---|
| 涨停总数 | `total_zt` | 全市场口径 |
| 板块走强 | `top5[].zt_count` 环比上升 | 板块级 |
| 题材爆发/衰竭/易主 | （由页面读 `market_memory` 事件，不在此重复） | 题材级 |
| 个股涨停/走强 | `leaders[]` 中达涨停线者 | ⚠️ 仅「板块领涨精选」，**非全量涨停池** |
| 炸板 | `broken.names` 新增 | 全量 |
| 跌停 | `limit_down.names` 新增 | 全量 |
| 杀跌板块 | `sector_risk.declining[]` | 板块级（含跌幅与主力净流出） |

⚠️ **个股级「跌」目前只有跌停**（`limit_down`）；系统未落盘「个股跌幅榜」，
故个股大跌**无法逐个记录** —— 如实标注，不伪造。

用法: python3 scripts/intraday_timeline.py   （守护每轮调用·独立可跑·可重复执行）
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
SNAP = CACHE / "pulse_snapshots"
LATEST = SNAP / "latest.json"
BJ = timezone(timedelta(hours=8))

# 涨停线（与 short_spirit.signal_type 同口径）
ZT_MAIN, ZT_20CM = 9.9, 19.9


def _today() -> str:
    return datetime.now(BJ).strftime("%Y%m%d")


def timeline_path() -> Path:
    return CACHE / f"intraday_timeline_{_today()}.jsonl"


def state_path() -> Path:
    return CACHE / f"intraday_timeline_state_{_today()}.json"


def _is_st(name: str) -> bool:
    """ST/*ST —— ⛔ 铁律④ 不看 ST（任何卡片/名单禁出现）。"""
    n = str(name or "").strip()
    return n.startswith("ST") or n.startswith("*ST")


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_zt(pct: float, is_20cm: bool) -> bool:
    """是否达涨停线（⛔ 与 short_spirit 同口径：主板 9.9% / 20cm 19.9%）"""
    try:
        return float(pct) >= (ZT_20CM if is_20cm else ZT_MAIN)
    except Exception:
        return False


def _limit_up_map(rows, total):
    """校验完整涨停集合；缺字段、重码或数量不符一律视为不可采集。"""
    if not isinstance(total, (int, float)) or not isinstance(rows, list) or len(rows) != int(total):
        return None
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        code = str(row.get("code") or "").strip()
        if not code or code in out:
            return None
        out[code] = {
            "code": code, "name": str(row.get("name") or code).strip(),
            "pct": row.get("pct"), "boards": row.get("boards"), "sector": row.get("sector"),
        }
    return out


def collect(latest: dict, state: dict) -> tuple[list, dict]:
    """**纯函数**：本轮快照 + 上轮状态 → (本轮新事件, 新状态)。

    不丢不重的不变量：同一事件只在**首次出现的那一轮**产出一次；
    同样的快照连跑两次，第二次产出**空**。
    """
    st = {
        # total 与集合必须来自同一次完整采集，才可作为下一轮差分基准。
        "total": state.get("total"),
        "limit_up_stocks": list(state.get("limit_up_stocks") or []),
        "sector_up": dict(state.get("sector_up") or {}),
        "seen_zt": set(state.get("seen_zt") or []),
        "seen_ld": set(state.get("seen_ld") or []),
        "seen_broken": set(state.get("seen_broken") or []),
        "seen_decl": set(state.get("seen_decl") or []),
    }
    evs: list = []

    # ── 涨停总数变化（必须有完整个股差分，采集不完整就不写节点）──
    total = latest.get("total_zt")
    current = _limit_up_map(latest.get("limit_up_stocks"), total)
    previous = _limit_up_map(st["limit_up_stocks"], st["total"])
    if current is not None:
        if previous is not None:
            added_codes = set(current) - set(previous)
            removed_codes = set(previous) - set(current)
            delta = int(total) - int(st["total"])
            # 只接受单向且数量完全对齐的集合差分，避免漏采/切换造成伪归因。
            valid = ((delta > 0 and len(added_codes) == delta and not removed_codes) or
                     (delta < 0 and len(removed_codes) == -delta and not added_codes))
            if valid:
                evs.append({"kind": "zt_total", "total": int(total), "delta": delta,
                            "added": [current[c] for c in sorted(added_codes)],
                            "removed": [previous[c] for c in sorted(removed_codes)]})
        # 仅完整集合可更新基准；缺失/不完整快照保留上一轮可靠集合。
        st["total"] = int(total)
        st["limit_up_stocks"] = [current[c] for c in sorted(current)]

    # ── 板块走强（top5 涨停家数环比上升）──
    for s in (latest.get("top5") or []):
        name = str(s.get("name") or "").strip()
        if not name:
            continue
        try:
            zc = int(s.get("zt_count") or 0)
        except Exception:
            continue
        prev = int(st["sector_up"].get(name) or 0)
        if zc > prev:
            evs.append({"kind": "sector_up", "name": name, "zt_count": zc,
                        "delta": zc - prev, "leader": s.get("leader") or None})
            st["sector_up"][name] = zc

    # ── 个股涨停/走强（⚠️ 仅板块领涨精选，非全量涨停池）──
    for l in (latest.get("leaders") or []):
        code = str(l.get("code") or "").strip()
        if not code or code in st["seen_zt"]:
            continue
        if not _is_zt(l.get("pct"), bool(l.get("is_20cm"))):
            continue
        evs.append({"kind": "stock_up", "code": code, "name": l.get("name"),
                    "sector": l.get("sector"), "pct": l.get("pct"),
                    "boards": l.get("boards") or None})
        st["seen_zt"].add(code)

    # ── 炸板（新增）—— 杀跌前兆 ──
    for n in ((latest.get("broken") or {}).get("names") or []):
        n = str(n).strip()
        if n and not _is_st(n) and n not in st["seen_broken"]:
            evs.append({"kind": "broken_new", "name": n})
            st["seen_broken"].add(n)

    # ── 跌停（新增）—— 杀跌 ──
    for n in ((latest.get("limit_down") or {}).get("names") or []):
        n = str(n).strip()
        if n and not _is_st(n) and n not in st["seen_ld"]:
            evs.append({"kind": "limit_down_new", "name": n})
            st["seen_ld"].add(n)

    # ── 杀跌板块（跌幅榜：含跌幅与主力净流出）──
    for d in ((latest.get("sector_risk") or {}).get("declining") or []):
        name = str(d.get("name") or "").strip()
        if name and name not in st["seen_decl"]:
            evs.append({"kind": "sector_down", "name": name, "pct": d.get("pct"),
                        "inflow": d.get("main_inflow")})
            st["seen_decl"].add(name)

    new_state = {
        "total": st["total"],
        "limit_up_stocks": st["limit_up_stocks"],
        "sector_up": st["sector_up"],
        "seen_zt": sorted(st["seen_zt"]),
        "seen_ld": sorted(st["seen_ld"]),
        "seen_broken": sorted(st["seen_broken"]),
        "seen_decl": sorted(st["seen_decl"]),
    }
    return evs, new_state


def append_events(evs: list, at: str | None = None) -> int:
    """追加写（⛔ 绝不覆盖）；返回写入条数。"""
    if not evs:
        return 0
    ts = at or datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
    p = timeline_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for e in evs:
            f.write(json.dumps({"at": ts, **e}, ensure_ascii=False) + "\n")
    return len(evs)


def main() -> int:
    try:
        latest = _load(LATEST)
        if not isinstance(latest, dict):
            print("[intraday_timeline] latest.json 缺失/不可读，跳过")
            return 0
        state = _load(state_path()) or {}
        evs, new_state = collect(latest, state)
        n = append_events(evs)
        state_path().write_text(json.dumps(new_state, ensure_ascii=False), encoding="utf-8")
        print(f"[intraday_timeline] 记录 {n} 条 → {timeline_path().name}")
        return 0
    except Exception as e:  # ⛔ 绝不影响守护链
        print(f"[intraday_timeline] 降级（不影响主流程）: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
