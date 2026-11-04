#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单票聚合视图（stock profile）—— 一只票的**唯一读侧入口**。

═══════════════════════════════════════════════════════════════════════
一、它解决什么问题
═══════════════════════════════════════════════════════════════════════
回答「这票现在什么状况」需要跨 8 处拼装：

| 维度 | 位置 |
|---|---|
| 身份 / 当日 / 逐日 | `cache/stock_registry/registry.db`（stocks / stock_daily）|
| 技术形态 | `cache/stock_registry/registry.db`（stock_technical_daily）|
| 题材归属 | `cache/stock_registry/registry.db`（stock_tags）|
| 股性（涨停基因）| `cache/zt_genes/hist_<code>.json` |
| 连板生命周期 | `cache/board_track/track_<date>.json` |
| 前复权日K | `cache/kline_qfq/` |
| 接力效应 | `cache/relay_effect/<date>.json` |

实测 3 只当日样本票各命中 8/10 个来源，**没有任何一处能一次取全**。
各消费方自己拼、拼法不同，口径漂移正是从拼装处长出来的
（同一个「封单强度」，board_track 用 `seal_ratio_pct`、卡面用 `seal_yi`）。

本模块只做**读侧聚合**：
  · 不写任何文件、不改任何写入路径、不参与候选/裁决/推送/调度；
  · 每维独立状态：`missing`（未观测）**≠ 0**，也不代表「没有」；
  · 每维带 `asof`：过期数据不被当成当前。

═══════════════════════════════════════════════════════════════════════
二、设计模式
═══════════════════════════════════════════════════════════════════════
· **单一数据源**：`registry.db` 是「单票事实」的主干（跨日累积、按代码唯一）。
  文件型产物（股性 / 连板轨迹）作为**补充维**接入，⛔ 不另建第二套真相；
  缺什么就标 `missing`，而不是自己算一个近似值顶上。
· **注册表 + 工厂**：每个维度是一条 `Dim` 声明（键 / 中文名 / 装配器 / 是否必需）。
  新增维度只改声明，不改装配逻辑；`build_profile` 与 `build_profiles` 共用
  同一份 `DIMS` —— ⛔ 禁止「单个一套、批量另一套」。
· **缺失语义**：`status` 四态 `ok / stale / missing / error`，
  ⛔ 不把未观测写成 0，也不把「不在册」写成「没涨停」。

═══════════════════════════════════════════════════════════════════════
三、与 `core/snapshot.stock_view` 的分工（⚠️ 两者都叫「单票视图」，不是一回事）
═══════════════════════════════════════════════════════════════════════
| | `core.snapshot.stock_view(date, code)` | 本模块 `profile(code)` |
|---|---|---|
| 组织维度 | **单个交易日**的 section（候选/终裁/竞价/风向标）| **跨日**事实（身份/历史/股性/轨迹）|
| 回答 | 「这票今天在决策链里各环节怎么说」| 「这票是什么、走到哪了、变化如何」|
| 数据 | `cache/snapshot/` 当日快照文件 | registry 底库 + 股性档案 + 连板台账 |
| 时间性 | 快照冻结，过期即失效 | 每维自带 asof，过期标 stale 仍可读 |

⛔ 不要再造第三套：判断类（今天买不买）走 `snapshot.stock_view`，
事实类（这票什么状况）走本模块。

═══════════════════════════════════════════════════════════════════════
四、边界
═══════════════════════════════════════════════════════════════════════
· `board` 维只覆盖 `boards >= 2` 的票（board_track 的入册门槛）。
  `missing` 的正确读法是「**未进入连板跟踪**」，不是「今天没涨停」。
· `gene` 维只有 555 只票有档案（占底库 17.6%）。缺档案 = 不知道，
  ⛔ 不许据此判「难连板」。
· `history` 维默认取近 10 个有观测的交易日，不是近 10 个自然交易日。
· 时间以**最新交易日**为锚：数据 `asof` 早于锚点 → `stale`，仍返回原值并标注。

═══════════════════════════════════════════════════════════════════════
五、开关
═══════════════════════════════════════════════════════════════════════
`ENABLE_STOCK_PROFILE=0` → `build_profile` 直接返回 `enabled=False`，不读任何源。
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:      # 允许 `python3 common/stock_profile.py` 直跑自检
    sys.path.insert(0, str(ROOT))

CACHE = ROOT / "cache"
REGISTRY_DB = CACHE / "stock_registry" / "registry.db"
GENE_DIR = CACHE / "zt_genes"
TRACK_DIR = CACHE / "board_track"

PROFILE_SCHEMA = "stock_profile_v1"
CODE_RE = re.compile(r"^\d{6}$")

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"

#: 逐日序列默认深度（有观测的交易日，非自然日）
HISTORY_DEPTH = 10

#: 覆盖率分级（与 sentiment.breakdown 同名同阈值，避免两套口径）
COVERAGE_HIGH = 0.85
COVERAGE_MEDIUM = 0.60


# ══════════════════════════════════════════════════════════════════════
# 开关 / 规范化
# ══════════════════════════════════════════════════════════════════════

def enabled() -> bool:
    """`ENABLE_STOCK_PROFILE=0` → 关闭（未设置或空串视同默认开）。"""
    raw = os.environ.get("ENABLE_STOCK_PROFILE")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "n")


def normalize_code(code: Any) -> str | None:
    """严格 6 位数字；其余一律 None（⛔ 不从名称或长串里猜代码）。"""
    token = str(code or "").strip()
    return token if CODE_RE.fullmatch(token) else None


def _num(value: Any) -> float | None:
    """数值投影：bool / 非有限数 / 非法字符串 → None（⛔ 不返回 0）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _json_load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _coverage_grade(ratio: float | None) -> str:
    if ratio is None:
        return "none"
    if ratio >= COVERAGE_HIGH:
        return "high"
    if ratio >= COVERAGE_MEDIUM:
        return "medium"
    if ratio > 0:
        return "low"
    return "none"


# ══════════════════════════════════════════════════════════════════════
# 维度声明（注册表）
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Dim:
    """一个数据维度的声明。

    `loader(ctx) -> (value, meta)`：
      · `value` 为该维正文；`None` 表示**无观测**；
      · `meta` 至少含 `status`，可选 `asof` / `reason`。
    `required=True` 的维度缺失时，整张视图仍会返回，但 `found=False` 的判定
    只依赖 `identity`，⛔ 不因辅助维缺失就把票判成不存在。
    """
    key: str
    label: str
    loader: Callable[["_Ctx"], tuple[Any, dict]]
    required: bool = False
    note: str = ""


def _meta(status: str, asof: Any = None, reason: str | None = None,
          source: str | None = None) -> dict:
    out: dict[str, Any] = {"status": status}
    if asof:
        out["asof"] = str(asof)
    if reason:
        out["reason"] = reason
    if source:
        out["source"] = source
    return out


# ── 装配器 ────────────────────────────────────────────────────────────

def _dim_identity(ctx: "_Ctx") -> tuple[Any, dict]:
    row = ctx.stock_row
    if not row:
        return None, _meta(STATUS_MISSING, reason="未在底库登记（系统从未抓到过这只票）",
                           source="registry.stocks")
    value = {
        "code": row["code"],
        "name": row.get("name"),
        "market": row.get("market"),
        "is_20cm": bool(row["is_20cm"]) if row.get("is_20cm") is not None else None,
        "industry": row.get("industry"),
        "first_seen_date": row.get("first_seen_date"),
        "last_seen_ts": row.get("last_seen_ts"),
        "source": row.get("source"),
        "data_quality_flags": _json_list(row.get("data_quality")),
    }
    return value, _meta(STATUS_OK, asof=row.get("last_seen_ts"), source="registry.stocks")


def _dim_today(ctx: "_Ctx") -> tuple[Any, dict]:
    row = ctx.stock_row
    if not row:
        return None, _meta(STATUS_MISSING, reason="无底库记录", source="registry.stocks")
    if not row.get("daily_asof_date"):
        return None, _meta(STATUS_MISSING, reason="无逐日观测", source="registry.stocks")
    # 复用 registry 的三态判定，⛔ 不在这里另写一套「今日是否涨停」。
    value, basis = _is_limit_up_today(row)
    latest = ctx.latest_trading_day
    is_latest = bool(row["daily_asof_date"] == latest) if latest else None
    body = {
        "asof_date": row["daily_asof_date"],
        "is_latest_trading_day": is_latest,
        "is_limit_up_today": value,
        "is_limit_up_basis": basis,
        "limit_up_now": row.get("limit_up_now"),
        "limit_up_touched": row.get("limit_up_touched"),
        "limit_up_close": row.get("limit_up_close"),
        "boards": row.get("boards"),
        "pct": _num(row.get("pct")),
        "price": _num(row.get("price")),
        "seal_yi": _num(row.get("seal_yi")),
        "hs": _num(row.get("hs")),
    }
    status = STATUS_OK if is_latest is not False else STATUS_STALE
    reason = None if status == STATUS_OK else f"逐日快照停在 {row['daily_asof_date']}，非最新交易日 {latest}"
    return body, _meta(status, asof=row.get("daily_asof_ts"), reason=reason,
                       source="registry.stocks")


def _dim_history(ctx: "_Ctx") -> tuple[Any, dict]:
    rows = ctx.daily_rows
    if not rows:
        return None, _meta(STATUS_MISSING, reason="无逐日观测", source="registry.stock_daily")
    items = [{
        "trade_date": r.get("trade_date"),
        "boards": r.get("boards"),
        "pct": _num(r.get("pct")),
        "price": _num(r.get("price")),
        "seal_yi": _num(r.get("seal_yi")),
        "hs": _num(r.get("hs")),
        "limit_up_close": r.get("limit_up_close"),
    } for r in rows]
    return items, _meta(STATUS_OK, asof=rows[-1].get("trade_date"),
                        source="registry.stock_daily")


def _dim_technical(ctx: "_Ctx") -> tuple[Any, dict]:
    row = ctx.tech_row
    if not row:
        return None, _meta(STATUS_MISSING, reason="未计算技术派生（盘后队列未覆盖）",
                           source="registry.stock_technical_daily")
    fields = ("bar_date", "source", "status", "close", "high", "low", "ma5", "ma10",
              "ma20", "ret_3d", "ret_5d", "ret_10d", "high_20d", "high_60d",
              "new_high_20d", "new_high_60d", "ma_bullish", "volume_ratio_5")
    value = {k: row.get(k) for k in fields}
    value["data_quality"] = _json_list(row.get("data_quality"))
    latest = ctx.latest_trading_day
    stale = bool(latest and row.get("bar_date") and row["bar_date"] < latest)
    return value, _meta(STATUS_STALE if stale else STATUS_OK, asof=row.get("bar_date"),
                        reason="技术快照早于最新交易日" if stale else None,
                        source="registry.stock_technical_daily")


def _dim_themes(ctx: "_Ctx") -> tuple[Any, dict]:
    tags = ctx.tags
    if not tags:
        return None, _meta(STATUS_MISSING, reason="无标签记录", source="registry.stock_tags")
    # ⛔ 题材与行业分列：混为一列会让「行业」冒充「题材」污染主线判断。
    return {
        "themes": tags.get("theme", []),
        "industry": tags.get("industry", []) or ([ctx.stock_row.get("industry")]
                                                if ctx.stock_row and ctx.stock_row.get("industry") else []),
        "concepts": tags.get("concept", []),
        "boards": tags.get("board", []),
        "sectors": tags.get("sector", []),
    }, _meta(STATUS_OK, source="registry.stock_tags")


def _dim_gene(ctx: "_Ctx") -> tuple[Any, dict]:
    facts = ctx.gene
    if not facts:
        return None, _meta(
            STATUS_MISSING,
            reason="无股性档案（cache/zt_genes/hist_<code>.json 不存在）——未知，不是「难连板」",
            source="zt_genes.hist")
    return facts, _meta(STATUS_OK, source="zt_genes.hist")


def _track_lag(ctx: "_Ctx") -> bool:
    """台账本身是否落后于最新交易日（⇒ 数据过期，而非该票状态变化）。"""
    return bool(ctx.latest_trading_day and ctx.board_latest
                and ctx.board_latest < ctx.latest_trading_day)


def _dim_board(ctx: "_Ctx") -> tuple[Any, dict]:
    series = ctx.board_series
    latest = ctx.board_latest
    if not series:
        broken = ctx.board_broken
        if broken:
            return {
                "in_track": False,
                "broken": {k: broken.get(k) for k in
                           ("prev_boards", "last_date", "first_date", "days_in_track", "sector")},
            }, _meta(STATUS_OK, asof=broken.get("last_date"),
                     reason="已断板（曾入册，当前不在涨停池）", source="board_track")
        return None, _meta(
            STATUS_MISSING,
            reason="未进入连板跟踪（入册门槛 boards>=2）——不是「今天没涨停」",
            source="board_track")

    last = series[-1]
    last_date = last.get("date")
    in_current = bool(latest and last_date == latest)

    # ⚠️ 两种完全不同的情况，⛔ 不可混为一谈：
    #    ① 台账没更新（board_latest 落后于最新交易日）→ stale，数据过期；
    #    ② 台账是最新的、这票不在里面 → 状态正常，只是它断板/降为首板。
    #    把 ② 报成 stale，会让「首板票」看起来像「数据坏了」，反过来污染判断。
    if _track_lag(ctx):
        return {**last, "is_current": False}, _meta(
            STATUS_STALE, asof=last_date,
            reason=f"连板台账最新 {latest}，落后于最新交易日 {ctx.latest_trading_day}",
            source="board_track")
    if not in_current:
        return {**last, "is_current": False}, _meta(
            STATUS_OK, asof=last_date,
            reason=f"最近在册 {last_date}，此后未入册（断板或降为首板）",
            source="board_track")
    return {**last, "is_current": True}, _meta(STATUS_OK, asof=last_date,
                                               source="board_track")


def _dim_board_lifecycle(ctx: "_Ctx") -> tuple[Any, dict]:
    series = ctx.board_series
    if not series:
        return None, _meta(STATUS_MISSING, reason="无连板轨迹", source="board_track")
    last_date = series[-1].get("date")
    in_current = bool(ctx.board_latest and last_date == ctx.board_latest)
    # ⚠️ 轨迹最后一天 ≠ 今天：该票可能早就断板离开台账。
    #    不标 is_current=False 就会被读成「现在还在连板」——600241 实测就是这样
    #    把 10-12 的 6 板当成当日的（当日其实是 1 板）。
    lag = _track_lag(ctx)
    if lag:
        status, reason = STATUS_STALE, (
            f"连板台账最新 {ctx.board_latest}，落后于最新交易日 {ctx.latest_trading_day}")
    elif not in_current:
        status, reason = STATUS_OK, (
            f"末次在册 {last_date}，此后未入册；轨迹为历史，不代表当前")
    else:
        status, reason = STATUS_OK, None
    return {
        "days": len(series),
        "first_date": series[0].get("date"),
        "last_date": last_date,
        "is_current": in_current and not lag,
        "series": series,
        # 只给「变化事实」，⛔ 不裁决、不出卖出信号。
        "changes": _board_changes(series),
    }, _meta(status, asof=last_date, reason=reason, source="board_track")


#: 维度注册表 —— 装配唯一入口；新增维度只改这里。
DIMS: tuple[Dim, ...] = (
    Dim("identity", "身份", _dim_identity, required=True,
        note="长期属性：代码/名称/市场/行业/首次入池"),
    Dim("today", "当日状态", _dim_today, required=True,
        note="逐日快照 + 今日是否涨停（带 basis 说明证据强度）"),
    Dim("history", "逐日序列", _dim_history,
        note="近 N 个有观测的交易日的板数/涨幅/封单"),
    Dim("technical", "技术形态", _dim_technical,
        note="均线 / 新高 / 量比 / 回撤"),
    Dim("themes", "题材归属", _dim_themes,
        note="题材与行业分列，⛔ 不混列"),
    Dim("gene", "股性", _dim_gene,
        note="历史涨停基因：晋级率 / 近一年次数 / 最高板"),
    Dim("board", "连板跟踪", _dim_board,
        note="当日是否在册、封单比、炸板、带动家数"),
    Dim("board_timeline", "连板轨迹", _dim_board_lifecycle,
        note="跨日在册序列 + 封单/炸板/家数的变化事实"),
)


def dims() -> tuple[Dim, ...]:
    """当前启用的维度集（工厂出口，与 `build_profile` 共用同一份声明）。"""
    return DIMS


# ══════════════════════════════════════════════════════════════════════
# 数据加载
# ══════════════════════════════════════════════════════════════════════

def _is_limit_up_today(row: dict) -> tuple[Any, str]:
    """复用 registry 的三态口径；导入失败时按同一优先级就地判定（保持同义）。"""
    try:
        from common.stock_registry import is_limit_up_today
        return is_limit_up_today(row)
    except Exception:
        if row.get("limit_up_close") is not None:
            return row["limit_up_close"], "close"
        if row.get("limit_up_now") is not None:
            return row["limit_up_now"], "now"
        if row.get("limit_up_touched") is not None:
            return row["limit_up_touched"], "touched"
        return None, "unknown"


def _latest_trading_day() -> str | None:
    try:
        from common.stock_registry import latest_trading_day
        return latest_trading_day()
    except Exception:
        return None


def _json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _open_registry_readonly(db_path: Path) -> sqlite3.Connection | None:
    """只读取连接：⛔ 不建表、不拿写锁、不触碰 WAL 写入路径。

    盘中守护每 30s 写库，读侧必须走 `mode=ro`，否则会和写入抢锁。
    """
    if not db_path.exists():
        return None
    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _load_registry(conn: sqlite3.Connection, codes: Sequence[str],
                   depth: int) -> dict[str, dict]:
    """一次取全这批票的 registry 事实（4 条 SQL，⛔ 不做逐票查询）。"""
    out: dict[str, dict] = {c: {"stock_row": None, "daily_rows": [], "tech_row": None,
                                "tags": {}} for c in codes}
    if not conn or not codes:
        return out
    marks = ",".join("?" for _ in codes)
    params = tuple(codes)

    for r in conn.execute(f"SELECT * FROM stocks WHERE code IN ({marks})", params):
        item = out.get(r["code"])
        if item is not None:
            item["stock_row"] = dict(r)

    # 逐日：按票取最近 depth 条（窗口函数在 SQLite 3.25+ 可用，这里用 row_number）
    sql = (
        "SELECT * FROM ("
        "  SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn"
        f"  FROM stock_daily WHERE code IN ({marks})"
        ") WHERE rn <= ? ORDER BY code, trade_date"
    )
    for r in conn.execute(sql, (*params, int(depth))):
        item = out.get(r["code"])
        if item is not None:
            row = dict(r)
            row.pop("rn", None)
            item["daily_rows"].append(row)

    sql = (
        "SELECT * FROM ("
        "  SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY bar_date DESC) AS rn"
        f"  FROM stock_technical_daily WHERE code IN ({marks})"
        ") WHERE rn = 1"
    )
    for r in conn.execute(sql, params):
        item = out.get(r["code"])
        if item is not None:
            row = dict(r)
            row.pop("rn", None)
            item["tech_row"] = row

    for r in conn.execute(
            f"SELECT code, tag_type, tag_value FROM stock_tags WHERE code IN ({marks})"
            " ORDER BY code, tag_type, tag_value", params):
        item = out.get(r["code"])
        if item is not None:
            item["tags"].setdefault(r["tag_type"], []).append(r["tag_value"])
    return out


# ── board_track 索引（进程内缓存，按文件 mtime 失效）────────────────────

_TRACK_CACHE: dict[str, Any] = {"stamp": None, "index": None}


def _load_track_index(track_dir: Path = TRACK_DIR) -> dict[str, Any]:
    """把 33 个「按日」文件翻成「按票」索引，一次读完后续查询全走内存。

    这是本模块相对「各消费方自己翻文件」的核心收益：
    查 1 只票与查 100 只票，文件 IO 次数相同。
    """
    files = sorted(track_dir.glob("track_*.json")) if track_dir.exists() else []
    stamp = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    if _TRACK_CACHE["stamp"] == stamp and _TRACK_CACHE["index"] is not None:
        return _TRACK_CACHE["index"]

    by_code: dict[str, list[dict]] = {}
    broken_by_code: dict[str, dict] = {}
    dates: list[str] = []
    for path in files:
        doc = _json_load(path)
        date = str(doc.get("date") or path.stem[6:])[:10]
        if not date:
            continue
        dates.append(date)
        for row in doc.get("tracked") or []:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("code"))
            if not code:
                continue
            track = row.get("track") if isinstance(row.get("track"), dict) else {}
            factors = row.get("factors") if isinstance(row.get("factors"), dict) else {}
            hist = row.get("hist") if isinstance(row.get("hist"), dict) else {}
            by_code.setdefault(code, []).append({
                "date": date,
                "theme": row.get("theme"),
                "boards": _int_or_none(row.get("boards")),
                "seal_yi": _num(row.get("seal_yi")),
                # 封单强度唯一可跨日比较的口径：封单 ÷ 流通市值（亿元不可比）
                "seal_ratio_pct": _num(row.get("seal_ratio_pct")),
                "fbt": row.get("fbt"),
                "fbt_min": _int_or_none(row.get("fbt_min")),
                "zbc": _int_or_none(row.get("zbc")),
                "hs": _num(row.get("hs")),
                "cje_yi": _num(row.get("cje_yi")),
                "lt_yi": _num(row.get("lt_yi")),
                "is_20cm": row.get("is_20cm"),
                "is_sector_dragon": row.get("is_sector_dragon"),
                "dragon_rank": _int_or_none(row.get("dragon_rank")),
                "mates": _int_or_none(row.get("mates")),
                "days_in_track": _int_or_none(track.get("days_in_track")),
                "streak_clean": track.get("streak_clean"),
                "factor_count": _int_or_none(factors.get("count")),
                "promo_sample": (hist.get("cond") or {}).get("n") if isinstance(hist.get("cond"), dict) else None,
                "promo_rate": (hist.get("cond") or {}).get("rate") if isinstance(hist.get("cond"), dict) else None,
            })
        for row in doc.get("broken") or []:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("code"))
            if code:
                broken_by_code[code] = dict(row, date=date)

    for code, rows in by_code.items():
        rows.sort(key=lambda x: x["date"])
    index = {
        "dates": sorted(dates),
        "latest": max(dates) if dates else None,
        "by_code": by_code,
        "broken_by_code": broken_by_code,
        "files": len(files),
    }
    _TRACK_CACHE["stamp"] = stamp
    _TRACK_CACHE["index"] = index
    return index


def _int_or_none(value: Any) -> int | None:
    f = _num(value)
    return int(f) if f is not None else None


def _board_changes(series: list[dict]) -> dict | None:
    """相邻两次在册的变化事实。⛔ 不做判读、不出信号，只把数摆出来。"""
    if len(series) < 2:
        return None
    a, b = series[-2], series[-1]

    def delta(key: str) -> float | None:
        x, y = _num(a.get(key)), _num(b.get(key))
        if x is None or y is None:
            return None
        return round(y - x, 3)

    return {
        "from_date": a["date"],
        "to_date": b["date"],
        "seal_ratio_delta_pp": delta("seal_ratio_pct"),
        "zbc_delta": delta("zbc"),
        "mates_delta": delta("mates"),
        "boards_delta": delta("boards"),
        "has_prior_sample": True,
    }


_GENE_MOD: Any = None
_GENE_TRIED = False


def _gene_module():
    """股性判据模块：`core.zt_gene` 与 `wealth_code.core.zt_gene` 是同一份文件的两种路径，
    谁先可导入用谁（⛔ 只试一次，避免按票重复付 ImportError 开销）。"""
    global _GENE_MOD, _GENE_TRIED
    if _GENE_TRIED:
        return _GENE_MOD
    _GENE_TRIED = True
    import importlib
    for name in ("core.zt_gene", "wealth_code.core.zt_gene"):
        try:
            _GENE_MOD = importlib.import_module(name)
            return _GENE_MOD
        except Exception:
            continue
    return None


def _load_gene(code: str, gene_dir: Path) -> dict:
    """股性走单一判据源 `core.zt_gene`，⛔ 不在这里重写晋级率档位。"""
    mod = _gene_module()
    if mod is None:
        return {}
    try:
        return mod.gene_facts(code, gene_dir) or {}
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════

class _Ctx:
    """一次装配的上下文：数据在构造时已就位，装配器不再读盘。"""

    __slots__ = ("code", "stock_row", "daily_rows", "tech_row", "tags",
                 "gene", "board_series", "board_broken", "board_latest",
                 "latest_trading_day")

    def __init__(self, code: str, *, stock_row: dict | None, daily_rows: list[dict],
                 tech_row: dict | None, tags: dict, gene: dict,
                 board_series: list[dict], board_broken: dict | None,
                 board_latest: str | None, latest_trading_day: str | None):
        self.code = code
        self.stock_row = stock_row
        self.daily_rows = daily_rows
        self.tech_row = tech_row
        self.tags = tags
        self.gene = gene
        self.board_series = board_series
        self.board_broken = board_broken
        self.board_latest = board_latest
        self.latest_trading_day = latest_trading_day


def _assemble(ctx: _Ctx) -> dict[str, Any]:
    """遍历维度注册表，逐维独立装配；单维异常不拖垮整张视图。"""
    body: dict[str, Any] = {}
    sources: dict[str, dict] = {}
    for dim in dims():
        try:
            value, meta = dim.loader(ctx)
        except Exception as exc:  # noqa: BLE001 — 单维故障独立降级
            value, meta = None, _meta(STATUS_ERROR, reason=f"{type(exc).__name__}: {exc}")
        body[dim.key] = value
        sources[dim.key] = {**meta, "label": dim.label}

    observed = sum(1 for m in sources.values() if m.get("status") in (STATUS_OK, STATUS_STALE))
    total = len(sources)
    ratio = round(observed / total, 3) if total else 0.0
    missing = [k for k, m in sources.items() if m.get("status") in (STATUS_MISSING, STATUS_ERROR)]
    stale = [k for k, m in sources.items() if m.get("status") == STATUS_STALE]

    view = {
        "schema": PROFILE_SCHEMA,
        "enabled": True,
        "code": ctx.code,
        "found": ctx.stock_row is not None,
        "latest_trading_day": ctx.latest_trading_day,
        **body,
        "_sources": sources,
        "_quality": {
            "observed": observed,
            "total": total,
            "ratio": ratio,
            "grade": _coverage_grade(ratio),
            "missing_dims": missing,
            "stale_dims": stale,
        },
    }
    return view


def build_profile(code: Any, *, db_path: Path | str | None = None,
                  gene_dir: Path | str | None = None,
                  track_dir: Path | str | None = None,
                  history_depth: int = HISTORY_DEPTH) -> dict[str, Any]:
    """单票事实画像。`build_profile(code)` 等价于 `build_profiles([code])` 的第一项。"""
    views = build_profiles([code], db_path=db_path, gene_dir=gene_dir,
                           track_dir=track_dir, history_depth=history_depth)
    token = normalize_code(code)
    if not views.get("enabled", True):
        # ⛔ 关闭时也要给出明确结构，不返回一个「什么都没说」的空壳。
        return {"schema": PROFILE_SCHEMA, "enabled": False, "code": token,
                "found": False, "reason": views.get("disabled_reason")}
    item = views["items"].get(token) if token else None
    if item is not None:
        return item
    return {"schema": PROFILE_SCHEMA, "enabled": True, "code": token,
            "found": False, "invalid": True, "reason": "code 必须为 6 位数字"}


def build_profiles(codes: Iterable[Any], *, db_path: Path | str | None = None,
                gene_dir: Path | str | None = None,
                track_dir: Path | str | None = None,
                history_depth: int = HISTORY_DEPTH) -> dict[str, Any]:
    """批量聚合：registry 一次连接、board_track 一次索引。

    ⛔ 与 `build_profile` 共用同一份 `DIMS`，禁止单独维护一条「批量口径」。
    """
    raw = list(codes or [])
    valid: list[str] = []
    invalid: list[str] = []
    for item in raw:
        token = normalize_code(item)
        if token and token not in valid:
            valid.append(token)
        elif not token:
            invalid.append(str(item))

    out = {
        "schema": PROFILE_SCHEMA,
        "enabled": enabled(),
        "count": 0,
        "invalid": invalid,
        "items": {},
    }
    if not out["enabled"]:
        out["disabled_reason"] = "ENABLE_STOCK_PROFILE=0"
        return out
    if not valid:
        return out

    db = Path(db_path) if db_path else REGISTRY_DB
    gdir = Path(gene_dir) if gene_dir else GENE_DIR
    tdir = Path(track_dir) if track_dir else TRACK_DIR
    latest = _latest_trading_day()
    track = _load_track_index(tdir)

    conn = _open_registry_readonly(db)
    try:
        facts = _load_registry(conn, valid, history_depth) if conn else {
            c: {"stock_row": None, "daily_rows": [], "tech_row": None, "tags": {}}
            for c in valid
        }
    finally:
        if conn:
            conn.close()

    for code in valid:
        f = facts.get(code, {})
        ctx = _Ctx(
            code,
            stock_row=f.get("stock_row"),
            daily_rows=f.get("daily_rows") or [],
            tech_row=f.get("tech_row"),
            tags=f.get("tags") or {},
            gene=_load_gene(code, gdir),
            board_series=track["by_code"].get(code, []),
            board_broken=track["broken_by_code"].get(code),
            board_latest=track.get("latest"),
            latest_trading_day=latest,
        )
        out["items"][code] = _assemble(ctx)
    out["count"] = len(out["items"])
    return out


# ══════════════════════════════════════════════════════════════════════
# 自检（只读，供人工核对）
# ══════════════════════════════════════════════════════════════════════

def _main(argv: Sequence[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="单票聚合视图（只读自检）")
    ap.add_argument("codes", nargs="+", help="6 位代码，可多只")
    ap.add_argument("--depth", type=int, default=HISTORY_DEPTH)
    args = ap.parse_args(argv)

    res = build_profiles(args.codes, history_depth=args.depth)
    if not res["enabled"]:
        print("已关闭：ENABLE_STOCK_PROFILE=0")
        return 0
    for code, view in res["items"].items():
        q = view["_quality"]
        print(f"\n=== {code} {((view.get('identity') or {}).get('name')) or '(未登记)'} "
              f"| 覆盖 {q['observed']}/{q['total']} {q['grade']} ===")
        for key, meta in view["_sources"].items():
            flag = {"ok": "✓", "stale": "~", "missing": "·", "error": "!"}.get(meta["status"], "?")
            extra = f" [{meta['asof']}]" if meta.get("asof") else ""
            note = f" — {meta['reason']}" if meta.get("reason") else ""
            print(f"  {flag} {meta['label']:6}{extra}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
