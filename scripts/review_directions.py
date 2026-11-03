#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘推演落盘 + 次日选股读取（P3 · 2026-10-18 用户「按顺序处理」）。

================================================================================
【治什么】复盘和选股从来没通过话：
  · 接力卡（选股）T 日 **15:30** 跑，候选 15:41 冻结；
  · 盘后复盘 T 日 **17:18** 才出「明日核心推演」；
  · eod 只读「短期情绪日报」的板块 verdict，**不读复盘卡**。
  → 复盘写得再准（10-16 原话：「真正的高度要看冰山冷热4进5、新华文轩3进4；
    新方向里军工的长城军工、低空的万丰奥威与金盾股份是最强的首板接棒候选」），
    选股里也是零体现。

【做法】把复盘推演**结构化落盘**，次日 eod 读它做两件事：
  1. **标记**：候选命中推演 → `review_flag`（height/new_direction/caution）+ 理由原文；
  2. **轻量排序倾斜**：只在 relay/watch 的**展示优先级**上加/减一点
     （`_leader_preference_key` 首位），⛔ **不改 raw_score、不塞池、不改入池资格**。
     raw_score 是 spec 十一维，掺主观判断会破坏"严格贴合 spec"铁律。

【为什么是"次日"】T 日 17:18 的推演针对 T+1，而 T 日 15:30 的 eod 已经跑完。
所以 T+1 日 15:30 的 eod 读 `review_directions_<T>.json`：
  · 推演票今天仍在池里 → 说明逻辑延续，标记 + 轻微提权（"复盘兑现"）
  · 推演票今天不在池里 → 记入 `review_missed`（复盘说对了没走出来 / 说错了没封板）
  · 警示票今天仍在池里 → 标 caution + 轻微降权（例：可能停牌的新华传媒）

【纪律】本模块**不产结论**。推演内容是复盘环节（AI/人）写的，这里只做搬运、校验、
匹配、标记。读不到文件 → 静默跳过（不编造"复盘说了什么"）。

【CLI】
  python scripts/review_directions.py --show 2026-10-16
  python scripts/review_directions.py --save 2026-10-16 /tmp/dirs.json
  python scripts/review_directions.py --path 2026-10-16
================================================================================
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parents[1]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from common.json_io import load_json, save_json  # noqa: E402

SCHEMA = "review_directions_v1"
_BJ = timezone(timedelta(hours=8))

# 三类推演（与复盘卡「明日核心推演」语言一一对应）
KIND_HEIGHT = "height"              # 高度来源（4进5 / 3进4 这种连板接力）
KIND_NEW_DIRECTION = "new_direction"  # 新方向首板接棒候选
KIND_CAUTION = "caution"            # 警示（随时停牌 / 不可当常规龙头）
VALID_KINDS = (KIND_HEIGHT, KIND_NEW_DIRECTION, KIND_CAUTION)

# ⛔ 排序倾斜幅度（只影响展示优先级，不进 raw_score）。
# 数值刻意很小：作用是"近似同分时复盘说的先看"，不能盖过量价证据。
REVIEW_BONUS_HEIGHT = 1.0
REVIEW_BONUS_NEW_DIRECTION = 0.8
REVIEW_PENALTY_CAUTION = -1.2


def review_enabled() -> bool:
    """P3 开关（默认开；ENABLE_REVIEW_DIRECTIONS=0 回旧行为，eod 不读复盘）。"""
    import os
    return os.environ.get("ENABLE_REVIEW_DIRECTIONS", "1") not in ("0", "false", "False", "")


def review_bonus(kind: str) -> float:
    return {
        KIND_HEIGHT: REVIEW_BONUS_HEIGHT,
        KIND_NEW_DIRECTION: REVIEW_BONUS_NEW_DIRECTION,
        KIND_CAUTION: REVIEW_PENALTY_CAUTION,
    }.get(str(kind or ""), 0.0)


def _default_cache_dir() -> Path:
    return _BASE / "cache"


def review_path(date: str, cache_dir: str | Path | None = None) -> Path:
    base = Path(cache_dir) if cache_dir else _default_cache_dir()
    return base / f"review_directions_{str(date or '').strip() or 'latest'}.json"


def normalize_code(code: Any) -> str:
    """代码归一：`600825.SS` / `600825.SH` / `SZ000001` → 6 位纯数字；认不出 → ""。"""
    s = str(code or "").strip().upper()
    if not s:
        return ""
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def _normalize_item(item: Any, kind: str, *, theme: str = "") -> dict[str, Any] | None:
    """把复盘推演条目归一成 {code,name,theme,note,tier}。缺 name 用 code 兜底。"""
    if isinstance(item, str):
        item = {"name": item}
    if not isinstance(item, dict):
        return None
    code = normalize_code(item.get("code") or item.get("dm") or "")
    name = str(item.get("name") or item.get("mc") or "").strip()
    if not code and not name:
        return None
    return {
        "code": code,
        "name": name or code,
        "theme": str(item.get("theme") or item.get("core_theme") or theme or ""),
        "note": str(item.get("note") or item.get("reason") or ""),
        "tier": str(item.get("tier") or ""),
        "boards": item.get("boards"),
        "kind": kind,
    }


def build_payload(
    date: str,
    *,
    height: list[Any] | None = None,
    new_directions: list[Any] | None = None,
    cautions: list[Any] | None = None,
    themes: list[str] | None = None,
    author: str = "财神-复盘",
    summary: str = "",
) -> dict[str, Any]:
    """构造合法 payload（唯一入口，避免各处手搓 schema）。"""
    def _clean(items, kind, theme=""):
        out = []
        for it in items or []:
            n = _normalize_item(it, kind, theme=theme)
            if n:
                out.append(n)
        return out

    return {
        "schema": SCHEMA,
        "date": str(date or ""),
        "generated_at": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "author": author,
        "summary": str(summary or ""),
        "themes": [str(t) for t in (themes or []) if str(t).strip()],
        "height": _clean(height, KIND_HEIGHT),
        "new_directions": _clean(new_directions, KIND_NEW_DIRECTION),
        "cautions": _clean(cautions, KIND_CAUTION),
    }


def save_review_directions(
    date: str,
    payload: dict[str, Any],
    *,
    cache_dir: str | Path | None = None,
) -> Path | None:
    """落盘（原子写）。schema/date 不符 → 拒绝写（防脏数据传染次日选股）。"""
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != SCHEMA:
        return None
    if str(payload.get("date") or "") != str(date or ""):
        return None
    path = review_path(date, cache_dir)
    return path if save_json(path, payload) else None


def load_review_directions(
    date: str, *, cache_dir: str | Path | None = None
) -> dict[str, Any] | None:
    """读推演；不存在/损坏/schema 不符 → None（调用方静默跳过，不编造）。"""
    raw = load_json(review_path(date, cache_dir))
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    return raw


def build_index(directions: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """{key: {kind, note, theme, name}}；**同时注册代码与名称两个 key**。

    · 代码 key = 6 位数字
    · 名称 key = `name:<名称>`（兜底：推演给了代码、但候选行代码口径不同的情况，
      例如推演写 601606.SS、候选池写 601606，或候选只有名称）
    · ⛔ 代码 key 优先；同一只票同时出现多类 → height > new_direction > caution
    """
    idx: dict[str, dict[str, Any]] = {}
    if not isinstance(directions, dict):
        return idx
    # 字段名与 kind 名不完全一致（new_direction → new_directions），显式映射防错
    for kind, field in (
        (KIND_HEIGHT, "height"),
        (KIND_NEW_DIRECTION, "new_directions"),
        (KIND_CAUTION, "cautions"),
    ):
        for it in directions.get(field) or []:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            code = normalize_code(it.get("code"))
            key = code or f"name:{name}"
            if not key or key == "name:":
                continue
            if key in idx:
                continue
            entry = {
                "kind": kind,
                "note": str(it.get("note") or ""),
                "theme": str(it.get("theme") or ""),
                "name": name,
            }
            idx[key] = entry
            # 名称兜底 key（不覆盖已存在的代码 key；同一条目共享同一 dict）
            name_key = f"name:{name}"
            if name and name_key not in idx:
                idx[name_key] = entry
    return idx


def annotate_candidates(
    scored: list[dict[str, Any]],
    directions: dict[str, Any] | None,
) -> dict[str, Any]:
    """给候选行打复盘标记（原地），返回统计。

    写入字段（只在命中时写，未命中不写空字段，避免前端把"没提"显示成"否"）：
      - review_flag    height / new_direction / caution
      - review_note    复盘原文理由（截断 120 字）
      - review_theme   复盘给的题材
      - review_bonus   排序倾斜值（供 _leader_preference_key 用）
    """
    idx = build_index(directions)
    stats = {
        "available": bool(idx),
        # 索引里代码/名称双 key 指向同一条目 → 按 id 去重才是真实推演条数
        "direction_count": len({id(v) for v in idx.values()}),
        "height_hit": 0,
        "new_direction_hit": 0,
        "caution_hit": 0,
        "hits": [],
    }
    if not idx:
        return stats
    for row in scored or []:
        if not isinstance(row, dict):
            continue
        code = normalize_code(row.get("code") or row.get("dm"))
        name = str(row.get("name") or row.get("mc") or "").strip()
        hit = idx.get(code) or idx.get(f"name:{name}")
        if not hit:
            continue
        kind = hit["kind"]
        row["review_flag"] = kind
        row["review_note"] = hit["note"][:120]
        if hit["theme"]:
            row["review_theme"] = hit["theme"]
        row["review_bonus"] = review_bonus(kind)
        key = f"{kind}_hit"
        if key in stats:
            stats[key] += 1
        stats["hits"].append({"code": code, "name": name, "kind": kind})
    return stats


def prev_review_date(date: str) -> str:
    """eod 该读哪一天的复盘：**上一交易日**（T 日 17:18 的推演针对 T+1）。"""
    try:
        from common.trading_calendar import prev_a_share_trading_day
        prev = prev_a_share_trading_day(date)
        return prev.strftime("%Y-%m-%d") if hasattr(prev, "strftime") else str(prev)
    except Exception:
        try:
            d = datetime.strptime(str(date), "%Y-%m-%d")
            return (d - timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            return ""


def load_for_eod(date: str, *, cache_dir: str | Path | None = None) -> tuple[dict[str, Any] | None, str]:
    """eod 入口：返回 (directions, 实际读取的 review_date)。关闭/读不到 → (None, date)。"""
    if not review_enabled():
        return None, ""
    rd = prev_review_date(date)
    if not rd:
        return None, ""
    return load_review_directions(rd, cache_dir=cache_dir), rd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="复盘推演落盘/查看（P3）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", metavar="DATE", help="打印某日推演 JSON")
    g.add_argument("--path", metavar="DATE", help="打印某日推演文件路径")
    g.add_argument("--save", nargs=2, metavar=("DATE", "FILE"), help="把 FILE 内容落盘到 DATE")
    args = ap.parse_args(argv)

    if args.path:
        print(review_path(args.path))
        return 0
    if args.show:
        d = load_review_directions(args.show)
        if not d:
            print(f"[review] {args.show} 无推演文件")
            return 1
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    date, file = args.save
    src = Path(file)
    if not src.exists():
        print(f"[review] 源文件不存在: {src}")
        return 1
    payload = json.loads(src.read_text(encoding="utf-8"))
    out = save_review_directions(date, payload)
    if not out:
        print("[review] 落盘失败（schema/date 校验不通过）")
        return 1
    print(f"[review] 已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
