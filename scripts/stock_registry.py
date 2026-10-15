#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一个股特征底库 · 命令行入口。

底层实现见 `common/stock_registry.py`（数据结构/存储/合并规则/异常处理都在那里）。
本文件只做三件事：**一次性回填**、**查询**、**导出投影**。

用法：
  python3 scripts/stock_registry.py init
  python3 scripts/stock_registry.py backfill [--since YYYY-MM-DD] [--dry-run]
  python3 scripts/stock_registry.py stats
  python3 scripts/stock_registry.py get 600000
  python3 scripts/stock_registry.py find 浦发银行 [--fuzzy]
  python3 scripts/stock_registry.py tag theme 半导体 [--from D] [--to D]
  python3 scripts/stock_registry.py range 2026-10-01 2026-10-14 [--limit-up]
  python3 scripts/stock_registry.py dump [--since ISO]
  python3 scripts/stock_registry.py refresh
  python3 scripts/stock_registry.py prune [--keep-days 40]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import stock_registry as SR  # noqa: E402
from common.json_io import load_json  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# 回填（非网络·只读本地归档·幂等可重跑）
# ══════════════════════════════════════════════════════════════════════

def _zt_history_files() -> dict[str, Path]:
    """收集 `{交易日: 文件}`。主目录优先，镜像目录补缺日。"""
    out: dict[str, Path] = {}
    for directory in (ROOT / "cache" / "zt_history",
                      ROOT / "wealth_code" / "data" / "zt_history"):
        for path in sorted(directory.glob("zt_*.json")):
            date10 = path.stem[3:]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date10):
                continue
            out.setdefault(date10, path)
    return out


def _snap_to_observations(snap: dict) -> list[dict]:
    """涨停池快照 → 观测。**只写正向证据**：池内为 1，池外一律不写。"""
    obs: list[dict] = []
    for item in (snap.get("zt_list") or []):
        if not isinstance(item, dict):
            continue
        code = SR.normalize_code(item.get("code"))
        if not code:
            continue
        obs.append({
            "code": code,
            "name": item.get("name"),
            "symbol": item.get("code"),
            "boards": item.get("boards"),
            "pct": item.get("pct"),
            "price": item.get("price"),
            "seal_yi": item.get("seal"),
            "hs": item.get("hs"),
            "industry": item.get("sector"),
            "limit_up_now": 1,
            "limit_up_touched": 1,
            "limit_up_close": 1,
        })
    return obs


def _gene_pool_to_observations(doc: dict) -> list[dict]:
    """股性池（东财涨停池派生）→ 观测。字段少，只补 name/boards 与涨停结论。"""
    obs: list[dict] = []
    for item in (doc.get("pool") or []):
        if not isinstance(item, dict):
            continue
        code = SR.normalize_code(item.get("code"))
        if not code:
            continue
        obs.append({
            "code": code,
            "name": item.get("name"),
            "symbol": item.get("code"),
            "boards": item.get("zt_boards"),
            "limit_up_now": 1,
            "limit_up_touched": 1,
            "limit_up_close": 1,
        })
    return obs


def _asof_of(snap: dict, date10: str) -> str:
    """观测时间戳：优先文件自带 `generated_ts`，缺失退化为当日 15:00。"""
    stamp = SR._norm_asof(snap.get("generated_ts"))
    return stamp or f"{date10}T15:00:00+08:00"


def _gather_backfill_sources(since: str | None) -> list[tuple[str, str, str, list[dict]]]:
    """汇总所有本地回填素材 → `[(日期, asof, 来源, 观测列表)]`（按日期升序）。"""
    batches: list[tuple[str, str, str, list[dict]]] = []

    for date10, path in sorted(_zt_history_files().items()):
        if since and date10 < since:
            continue
        snap = load_json(path)
        if not isinstance(snap, dict):
            continue
        obs = _snap_to_observations(snap)
        if obs:
            batches.append((date10, _asof_of(snap, date10), "backfill:zt_history", obs))

    # 当前快照（可能比归档更新）
    current = load_json(ROOT / "cache" / "yesterday_zt.json")
    if isinstance(current, dict):
        date10 = SR._coerce_date10(current.get("date"))
        if date10 and (not since or date10 >= since):
            obs = _snap_to_observations(current)
            if obs:
                batches.append((date10, _asof_of(current, date10), "backfill:yesterday_zt", obs))

    # 股性池（补归档缺日）
    for path in sorted((ROOT / "cache" / "zt_genes").glob("pool_*.json")):
        raw = path.stem.split("_", 1)[-1]
        if not re.fullmatch(r"\d{8}", raw):
            continue
        date10 = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        if since and date10 < since:
            continue
        doc = load_json(path)
        if not isinstance(doc, dict):
            continue
        obs = _gene_pool_to_observations(doc)
        if obs:
            batches.append((date10, _asof_of(doc, date10), "backfill:zt_genes_pool", obs))

    batches.sort(key=lambda b: (b[0], b[1]))
    return batches


def cmd_backfill(argv: list[str]) -> int:
    since = _arg_value(argv, "--since")
    dry = "--dry-run" in argv
    batches = _gather_backfill_sources(since)
    if not batches:
        print("[backfill] 无本地素材可回填")
        return 0

    total_codes: set[str] = set()
    skipped_days: list[str] = []
    summary = {"inserted": 0, "updated": 0, "unchanged": 0, "pending": 0}

    for date10, asof, source, obs in batches:
        if not SR._is_trading_day(date10):
            skipped_days.append(date10)
            continue
        total_codes.update(o["code"] for o in obs)
        if dry:
            continue
        res = SR.upsert_many(obs, trade_date=date10, asof_ts=asof,
                             source=source, allow_backfill=True)
        for key in summary:
            summary[key] += res.get(key, 0)

    print(f"[backfill]{'[dry-run]' if dry else ''} 批次 {len(batches)} 个交易日 | "
          f"覆盖个股 {len(total_codes)} 只")
    if skipped_days:
        print(f"[backfill] ⏭ 跳过非交易日 {len(skipped_days)} 天: "
              f"{', '.join(sorted(set(skipped_days))[:8])}"
              f"{' …' if len(set(skipped_days)) > 8 else ''}")
    if not dry:
        print(f"[backfill] 新增 {summary['inserted']} | 更新 {summary['updated']} | "
              f"无变化 {summary['unchanged']} | 无代码待确认 {summary['pending']}")
        SR.dump_json()
        print(f"[backfill] 投影已导出 → {SR.DEFAULT_PROJECTION}")
    return 0


# ══════════════════════════════════════════════════════════════════════
# 查询
# ══════════════════════════════════════════════════════════════════════

def _brief(row: dict) -> str:
    value, basis = SR.is_limit_up_today(row)
    label = {1: "是", 0: "否", None: "未知"}.get(value, "未知")
    themes = ",".join(row.get("themes") or [])[:24] or "-"
    date10 = row.get("daily_asof_date") or "-"
    fresh = "" if row.get("daily_is_latest_trading_day") else "⚠️非最新"
    return (f"  {row.get('code')} {row.get('name') or '-':<8} "
            f"涨停={label}({basis}) 板块={row.get('industry') or '-':<8} "
            f"题材={themes:<24} 数据日={date10}{fresh}")


def cmd_get(argv: list[str]) -> int:
    if not argv:
        print("用法: get <code>")
        return 2
    row = SR.get_by_code(argv[0])
    if not row:
        print(f"未找到 {argv[0]}（底库只沉淀进入过系统可见范围的个股）")
        return 1
    _print_row(row)
    return 0


def _print_row(row: dict) -> None:
    print(json.dumps(row, ensure_ascii=False, indent=2, default=str))


def cmd_find(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print("用法: find <名称> [--fuzzy]")
        return 2
    rows = SR.find_by_name(args[0], fuzzy="--fuzzy" in argv)
    if not rows:
        print(f"未找到名称 {args[0]}")
        return 1
    if len(rows) > 1:
        print(f"⚠️ {args[0]} 命中 {len(rows)} 只，**不做猜测**，请按代码确认：")
    for row in rows:
        print(_brief(row))
    return 0


def cmd_tag(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        print("用法: tag <theme|industry|concept|board> <值> [--from D] [--to D]")
        return 2
    rows = SR.query_by_tag(args[0], args[1],
                           date_from=_arg_value(argv, "--from"),
                           date_to=_arg_value(argv, "--to"))
    print(f"[tag] {args[0]}={args[1]} → {len(rows)} 只")
    for row in rows:
        print(_brief(row))
    return 0


def cmd_range(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        print("用法: range <from> <to> [--limit-up] [--limit N]")
        return 2
    limit = int(_arg_value(argv, "--limit") or 2000)
    rows = SR.query_date_range(args[0], args[1], limit=limit,
                               only_limit_up="--limit-up" in argv)
    print(f"[range] {args[0]} ~ {args[1]} → {len(rows)} 条逐日记录")
    for row in rows[:40]:
        v = row.get("limit_up_close")
        print(f"  {row['trade_date']} {row['code']} 收盘涨停={v} 连板={row.get('boards')}")
    if len(rows) > 40:
        print(f"  … 其余 {len(rows) - 40} 条")
    return 0


def cmd_stats(argv: list[str]) -> int:
    print(json.dumps(SR.stats(), ensure_ascii=False, indent=2))
    return 0


def cmd_dump(argv: list[str]) -> int:
    payload = SR.dump_json(since=_arg_value(argv, "--since"))
    print(f"[dump] {payload['count']} 只 → {SR.DEFAULT_PROJECTION}")
    return 0


def cmd_refresh(argv: list[str]) -> int:
    payload = SR.refresh_projection()
    if payload is None:
        print("[refresh] 投影刷新失败（详见日志）")
        return 1
    print(f"[refresh] {payload['count']} 只 → {SR.DEFAULT_PROJECTION}")
    return 0


def cmd_init(argv: list[str]) -> int:
    path = SR.init_db()
    print(f"[init] 底库就绪 → {path}")
    return 0


def cmd_prune(argv: list[str]) -> int:
    keep = int(_arg_value(argv, "--keep-days") or 40)
    n = SR.prune_observations(keep)
    print(f"[prune] 清理原始观测 {n} 条（保留 {keep} 天）")
    return 0


def _arg_value(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        idx = argv.index(flag)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


COMMANDS = {
    "init": cmd_init,
    "backfill": cmd_backfill,
    "stats": cmd_stats,
    "get": cmd_get,
    "find": cmd_find,
    "tag": cmd_tag,
    "range": cmd_range,
    "dump": cmd_dump,
    "refresh": cmd_refresh,
    "prune": cmd_prune,
}


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    handler = COMMANDS.get(argv[0])
    if not handler:
        print(f"未知子命令 {argv[0]}；可选: {', '.join(COMMANDS)}")
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
