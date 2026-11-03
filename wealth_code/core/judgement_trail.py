"""
单票判断轨迹（judgement_trail）—— 把「每天怎么判的这只票」按股沉淀成可回看的时间序列。

## 为什么要有它（2026-10-23 用户「研究推进」）

`pipeline_eod.run_eod` 里 `scored = _score_all(zt["ztgc"], ...)` 对当日涨停股全量打过分，
但只有进 relay/watch 的 7~15 只落盘（`candidates-<date>.json`），其余当场丢弃。
后果＝**单票跨日轨迹天然断点**：实测 603278 大业股份在 48 个 `candidates-*.json` 里只出现
2 天（09-28 2 板 65.4 / 10-22 5 板 78.9），而它实际在 09-24 起连续涨停 8 次（1→1→2→断→1→2→3→4→5）。
「必须在第 1~2 板发现它」这条纪律做不到，不是模型不行，是**中间没留痕**。

## 三层数据，各管一件事（⛔ 不混层、不互相顶替）

1. **事实层**（全市场涨停股、逐日全覆盖）
   来源＝`cache/decision_log/<date>/zt_pool.json` 的 `zt_list`（涨停池全量快照，含板数/封板时刻/
   封单/换手/成交额/行业）。这一层**不做任何判断**，只回答「这天它涨停了吗、几板、几点封的」。
2. **门槛层**（可复现）
   按 `pipeline_eod.turnover_floor(boards)` 判定当日成交额是否达门槛 → `gate` 字段。
   `below_floor` 的含义＝**当天它涨停了，但没被送进打分**（实测 10-16：61 只涨停、45 只被门槛剔掉，
   大业 1.58 亿首板就在这 45 只里）。判据唯一源＝`turnover_floor`，⛔ 本模块不自己写阈值。
3. **判断层**（只有进池那天才有）
   来源＝`candidates-<date>.json`（raw_score/池别/标签）+ `pool_call-<date>.json`（当天最终裁决）。
   ⛔ 缺失就留空（`None`），绝不用重算分顶替当天原话。

## 铁律

- **append-only 的语义，rewrite 的实现**：源（decision_log）本身只增不改，因此本模块每次
  `rebuild()` 从源全量重算并覆盖产物 → 幂等、无重复行，且不会因为中途崩溃留下半条轨迹。
- **⛔ 不做「事后重算分数」冒充当日判断**。需要重算时必须另立 `caliber="recalc"` 层，
  与 `caliber="asof"` 分开存（参照外部「评分轨迹」产品的「修订版」标注口径）。
- **⛔ 不把分数当胜率**。`score` 只表示当日排序位，字段名就叫 score，不给它套概率语义。

## 用法

```bash
# 全量重建（读 decision_log 全部归档日 + 当日 live 候选）
python3 wealth_code/scripts/judgement_trail.py rebuild

# 看单票轨迹
python3 wealth_code/scripts/judgement_trail.py show 603278
```
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ── 路径 ────────────────────────────────────────────────
# 本文件位于 <项目根>/wealth_code/core/judgement_trail.py
ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "cache"
LIVE_CACHE_DIR = ROOT / "wealth_code" / "cache"
DECISION_LOG_DIR = CACHE_DIR / "decision_log"
OUT_DIR = CACHE_DIR / "judgement_trail"

SCHEMA = "judgement_trail_v1"

# 一字板封板时刻口径：集合竞价即封（09:25:00~09:25:59）
_ONE_WORD_PREFIX = "0925"
# 早封 / 午后封（HHMMSS 字符串直接比大小即可）
_EARLY_SEAL_UNTIL = "100000"
_AFTERNOON_SEAL_FROM = "130000"
# 量能结构：与前一涨停日对比（用户口径「地量→首板放量→连板缩量」）
_SHRINK_RATIO = 0.60
_EXPAND_RATIO = 1.60


# ── 小工具 ──────────────────────────────────────────────
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 1) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _turnover_floor(boards: int) -> float | None:
    """成交额门槛（亿）。判据唯一源＝`pipeline_eod.turnover_floor`；导入失败返回 None（缺失优于瞎猜）。"""
    try:
        from pipelines.pipeline_eod import turnover_floor  # noqa: PLC0415 — 惰性导入避免拉重依赖

        return float(turnover_floor(boards))
    except Exception:
        return None


# ── 派生标签 ────────────────────────────────────────────
def mech_tags(row: dict[str, Any]) -> list[str]:
    """机制标签（人话，供轨迹展示）：板数 / 封板时点 / 回封 / 量能结构。"""
    tags: list[str] = []
    boards = _i(row.get("boards"), 1)
    tags.append("首板" if boards <= 1 else f"{boards}连板")

    fbt = str(row.get("fbt") or "").strip()
    if fbt.startswith(_ONE_WORD_PREFIX):
        tags.append("一字封")
    elif fbt and fbt <= _EARLY_SEAL_UNTIL:
        tags.append("早封")
    elif fbt and fbt >= _AFTERNOON_SEAL_FROM:
        tags.append("午后封")

    if _i(row.get("zbc"), 0) > 0:
        tags.append("开过板")
    if row.get("is_20cm"):
        tags.append("20cm")

    vol = row.get("vol_state")
    if vol == "shrink":
        tags.append("缩量")
    elif vol == "expand":
        tags.append("放量")
    return tags


def _vol_state(cje_yi: float, prev_cje_yi: float | None) -> str | None:
    """与「上一个涨停日」对比成交额：缩量/放量/持平。没有前值就不判（缺失优于编造）。"""
    if prev_cje_yi is None or prev_cje_yi <= 0 or cje_yi <= 0:
        return None
    ratio = cje_yi / prev_cje_yi
    if ratio < _SHRINK_RATIO:
        return "shrink"
    if ratio > _EXPAND_RATIO:
        return "expand"
    return "flat"


# ── 单日取数 ────────────────────────────────────────────
def _pool_index(cand: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """code → {pool, score, ...}：只有进池的票才有，池外不补。"""
    out: dict[str, dict[str, Any]] = {}
    if not cand:
        return out
    for pool in ("relay", "watch"):
        for it in cand.get(pool) or []:
            code = str(it.get("code") or "").strip()
            if not code:
                continue
            out[code] = {
                "pool": pool,
                "score": _f(it.get("raw_score"), 0.0) or None,
                "bucket_label": it.get("bucket_label"),
                "tags": list(it.get("tags") or []),
                "is_one_word": bool(it.get("is_one_word")),
                "is_capacity_core": bool(it.get("is_capacity_core")),
            }
    return out


def _verdict_index(pool_call: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """code → {verdict, reason}：当天 09:25 竞价之后的最终结论。"""
    out: dict[str, dict[str, Any]] = {}
    if not pool_call:
        return out
    for code, v in (pool_call.get("views") or {}).items():
        if not isinstance(v, dict):
            continue
        view = str(v.get("view") or "")
        out[str(code)] = {
            "verdict": {"participate": "买入", "watch": "观察", "skip": "放弃"}.get(view, view or None),
            "reason": v.get("reason"),
        }
    return out


def day_rows(trade_date: str, *, log_dir: Path | None = None,
             live_dir: Path | None = None) -> list[dict[str, Any]]:
    """某交易日全部涨停股一行一条（事实层）+ 判断层（有则有）。

    数据源优先级：`decision_log/<date>/` 归档 → 当日 live `wealth_code/cache/`。
    两处都没有 zt_pool → 返回空列表（缺数据不推断）。
    """
    log_dir = log_dir or DECISION_LOG_DIR
    live_dir = live_dir or LIVE_CACHE_DIR
    ddir = log_dir / trade_date

    zt = _read_json(ddir / "zt_pool.json") or {}
    zt_list = zt.get("zt_list") or []
    src = "archive"
    if not zt_list:
        # 当日 live：run_eod 自己落的涨停池快照（无归档时兜底）
        for name in (f"yesterday_zt.json", f"zt_list-{trade_date}.json"):
            alt = _read_json(LIVE_CACHE_DIR / name) or {}
            if alt.get("zt_list"):
                zt, zt_list, src = alt, alt["zt_list"], "live"
                break
    if not zt_list:
        return []

    cand = _read_json(ddir / "candidates.json") or _read_json(live_dir / f"candidates-{trade_date}.json")
    pool_call = _read_json(ddir / "pool_call.json") or _read_json(live_dir / f"pool_call-{trade_date}.json")
    pool_idx = _pool_index(cand)
    verdict_idx = _verdict_index(pool_call)

    rows: list[dict[str, Any]] = []
    for it in zt_list:
        code = str(it.get("code") or it.get("dm") or "").strip()
        if not code:
            continue
        boards = _i(it.get("boards") or it.get("lbc"), 1)
        cje_yi = _f(it.get("cje_yi"), 0.0)
        floor = _turnover_floor(boards)
        if floor is None:
            gate = "unknown"
        elif cje_yi >= floor:
            gate = "pass"
        else:
            gate = "below_floor"
        p = pool_idx.get(code) or {}
        v = verdict_idx.get(code) or {}
        rows.append({
            "schema": SCHEMA,
            "date": trade_date,
            "code": code,
            "name": str(it.get("name") or it.get("mc") or "").strip(),
            "boards": boards,
            "fbt": str(it.get("fbt") or "") or None,
            "zbc": _i(it.get("zbc"), 0),
            "seal_yi": round(_f(it.get("seal"), 0.0), 2),
            "hs_pct": round(_f(it.get("hs"), 0.0), 2),
            "cje_yi": round(cje_yi, 2),
            "sector": str(it.get("sector") or it.get("hy") or ""),
            "is_20cm": bool(it.get("is_20cm")),
            "floor_yi": round(floor, 2) if floor is not None else None,
            "gate": gate,
            "pool": p.get("pool"),
            "score": p.get("score"),
            "bucket_label": p.get("bucket_label"),
            "verdict": v.get("verdict"),
            "verdict_reason": v.get("reason"),
            "src": src,
        })
    return rows


# ── 全量重建 ────────────────────────────────────────────
def _all_dates(log_dir: Path, live_dir: Path) -> list[str]:
    dates: set[str] = set()
    if log_dir.exists():
        for p in log_dir.iterdir():
            if p.is_dir() and p.name[:2] == "20" and len(p.name) == 10:
                dates.add(p.name)
    if live_dir.exists():
        for p in live_dir.glob("candidates-*.json"):
            dates.add(p.stem.split("candidates-")[-1])
    return sorted(dates)


def rebuild(*, since: str | None = None, log_dir: Path | None = None,
            live_dir: Path | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    """从归档 + live 全量重算轨迹（幂等：同输入必得同产物）。

    `since`（YYYY-MM-DD）只影响**输出范围**；量能结构的前值来自范围内的更早行，
    因此 since 之后第一天的「缩量/放量」可能为空 —— 这是缺失，不是 bug。
    """
    log_dir = log_dir or DECISION_LOG_DIR
    live_dir = live_dir or LIVE_CACHE_DIR
    out_dir = out_dir or OUT_DIR

    dates = [d for d in _all_dates(log_dir, live_dir) if not since or d >= since]
    all_rows: list[dict[str, Any]] = []
    per_day: list[dict[str, Any]] = []
    for d in dates:
        rows = day_rows(d, log_dir=log_dir, live_dir=live_dir)
        if not rows:
            per_day.append({"date": d, "rows": 0, "note": "无涨停池快照"})
            continue
        per_day.append({"date": d, "rows": len(rows)})
        all_rows.extend(rows)

    # 量能结构：按股按日排序后与「上一个涨停日」对比
    by_code: dict[str, list[dict[str, Any]]] = {}
    for r in all_rows:
        by_code.setdefault(r["code"], []).append(r)
    for rows in by_code.values():
        rows.sort(key=lambda x: x["date"])
        prev: dict[str, Any] | None = None
        for r in rows:
            r["vol_state"] = _vol_state(_f(r["cje_yi"]), _f(prev["cje_yi"]) if prev else None)
            r["prev_date"] = prev["date"] if prev else None
            prev = r

    for r in all_rows:
        r["mech"] = mech_tags(r)

    # 输出：按月一个 jsonl（全量覆盖，天然去重）
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jsonl"):
        old.unlink()
    by_month: dict[str, list[dict[str, Any]]] = {}
    for r in all_rows:
        by_month.setdefault(r["date"][:7], []).append(r)
    for month, rows in sorted(by_month.items()):
        rows.sort(key=lambda x: (x["date"], x["code"]))
        with (out_dir / f"{month}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    index = {
        "schema": SCHEMA,
        "generated_dates": len([x for x in per_day if x.get("rows")]),
        "days_without_pool": [x["date"] for x in per_day if not x.get("rows")],
        "rows": len(all_rows),
        "codes": len(by_code),
        "range": [all_rows[0]["date"], all_rows[-1]["date"]] if all_rows else [],
        "gate_below_floor": sum(1 for r in all_rows if r["gate"] == "below_floor"),
        "in_pool": sum(1 for r in all_rows if r["pool"]),
        "per_day": per_day,
    }
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return index


# ── 查询 ────────────────────────────────────────────────
def query(code: str, *, out_dir: Path | None = None) -> list[dict[str, Any]]:
    """某只票的完整轨迹（按日期升序）。"""
    out_dir = out_dir or OUT_DIR
    code = str(code).strip()
    rows: list[dict[str, Any]] = []
    for p in sorted(out_dir.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip() or f'"{code}"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("code") == code:
                rows.append(r)
    rows.sort(key=lambda x: x["date"])
    return rows
