#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一个股特征底库（stock registry）—— 系统底层的**个股持久化数据源**。

═══════════════════════════════════════════════════════════════════════
一、它解决什么问题
═══════════════════════════════════════════════════════════════════════
此前「个股」散落在多处、各有各的口径：
  · 盘中 `cache/pulse_snapshots/latest.json`（一次性快照，下一轮即被覆盖）
  · 涨停当前快照 `cache/yesterday_zt.json`（只代表"最近一次"）
  · 历史归档 `cache/zt_history/zt_*.json`（逐日文件，跨日查要自己拼）
  · 股性 `cache/zt_genes/*`（只是涨停股性，非通用特征）
本模块提供一个**跨日累积、按代码唯一、可扩展**的个股对象底库。

⚠️ 入库门槛 = **进入系统可见范围**，与今日表现无关。
   不是"只存涨停股"，也不是"只存强势股"——只要系统抓到了它，就沉淀。

═══════════════════════════════════════════════════════════════════════
二、存储方式
═══════════════════════════════════════════════════════════════════════
SQLite（真源） + JSON 投影（只读镜像），默认路径 `cache/stock_registry/`：
  · `registry.db`               —— 事务真源（WAL 模式）
  · `stock_registry.json`       —— 只读投影，供前端/脚本沿用文件式读取
选 SQLite 而非单一大 JSON 的理由（都是硬需求，不是偏好）：
  · 唯一标识  → `PRIMARY KEY(code)` 由数据库硬保证，不靠手写去重；
  · 字段级合并 → 读改写在同一事务内，杜绝「并发读改写丢更新」；
  · 准实时并发 → WAL + `BEGIN IMMEDIATE`，盘中 30s 节奏与盘后互不打架；
  · 可扩展    → 新增字段 `ALTER TABLE ADD COLUMN`，或先落 `ext_json`；
  · 检索      → name / tag / date 全部走索引。
`cache/` 已在 `.gitignore` 中，DB 与投影都不会入库。

═══════════════════════════════════════════════════════════════════════
三、数据结构（6 张表）
═══════════════════════════════════════════════════════════════════════
① `stocks`            —— 主表，一票一行。长期属性 + **当日快照**（逐日字段）+ 元数据。
② `stock_daily`       —— 逐日观测，append-only，PK(code, trade_date)。历史真身在此。
③ `stock_tags`        —— 多值标签。**题材与行业严格分列**，不得混为一列。
④ `stock_observations`—— 原始观测审计，带 dedup_hash，可回放、幂等。
⑤ `stock_name_alias`  —— 名称→代码别名。**名称只用于检索，绝不作为主对象键。**
⑥ `stock_pending_review` —— 无代码票 / 名称歧义 / 脏数据，待人工确认。

字段分三类，**混用会出事**：
  · 长期属性：code / symbol / name / market / is_20cm / industry / first_seen_date
  · 逐日属性：limit_up_now / limit_up_touched / limit_up_close / boards / pct /
              price / seal_yi / hs            ← 必须与 `daily_asof_date` 同读
  · 元数据  ：source / last_seen_ts / last_updated_ts / update_count / data_quality

「今日是否涨停」为什么是三个字段而不是一个布尔？
  因为**数据源看不到全市场**——「不在涨停名单里」绝不等于「今天没涨停」。
  把三态压成一个布尔，必然要么造假、要么丢信息：
    `limit_up_now`     当前是否封板（会从 1 变 0：开板是**显式状态变更**）
    `limit_up_touched` 当日是否曾触板（**闩锁**：一旦为 1，当日永不回 0）
    `limit_up_close`   是否收盘涨停（**仅盘后权威源可写**）
  三者均允许 `NULL` = 未知。取用请走 `is_limit_up_today()`，它同时返回 `basis`
  告诉你这个结论是依据哪一态得出的。

═══════════════════════════════════════════════════════════════════════
四、更新与合并规则（核心）
═══════════════════════════════════════════════════════════════════════
1. **命中即 upsert，未命中不动**：只写调用方实际观测到的票，不做全市场扫描。
2. **有值才写**：入参为 `None` ⇒ 该列不动。缺失**绝不覆盖**已有非空值。
3. **跨日轮换**：`trade_date` 前进时，当日快照整体换新（未提供的置空），
   旧值并不会丢——它们留在 `stock_daily` 里。这样"昨天的涨幅"不会被误读成"今天"。
4. **同日合并**：按 `asof_ts` 新者胜；同值写入不改 `update_count`。
5. **乱序保护**：旧 `asof_ts` 晚到时，不覆盖当前态，但闩锁 `max`、标签、别名照常累计。
6. **代码规范化**：`600000.SH` / `sh600000` / `" 600000 "` → 主键 `600000`，
   原值保留在 `symbol`。取不到**恰好 6 位**数字则拒绝建主对象。
7. **名称歧义不猜**：同名多代码且无从辨别 → 进 `stock_pending_review`，不建重复对象。

═══════════════════════════════════════════════════════════════════════
五、异常与缺失
═══════════════════════════════════════════════════════════════════════
· 接口失败/字段缺失 → 留 `NULL`，不编、不猜、不把"缺席"写成"未涨停"。
· 脏类型（`"false"`/`"None"`/`NaN`/空串）→ 严格解析，记 `dirty_type:<字段>` 标记。
  ⛔ 绝不把任意非空字符串当 True（旧代码 `bool("false") is True` 是真实事故源）。
· 非交易日 / 未来日期 / 无法解析的 asof → **拒绝写入**（回填需显式 `allow_backfill=True`）。
· 并发锁超时 → **大声失败**，绝不静默丢弃。
· 测试期未注入 `db_path` 且路径落在生产目录 → **当场报错**（复刻 zt_archive 血案防线）。

═══════════════════════════════════════════════════════════════════════
六、可逆开关
═══════════════════════════════════════════════════════════════════════
`STOCK_REGISTRY_ENABLED`  =0 时所有 ingest 变 no-op（查询仍可读存量）
`STOCK_REGISTRY_INTRADAY` =0 单独关盘中 hook
`STOCK_REGISTRY_EOD`      =0 单独关盘后 hook
`LIMAO_REGISTRY_DB`       覆盖 DB 路径（测试/换机）
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _bj_tz():
    """北京时间时区（优先复用 common.market_time，读不到则退化为固定 +08:00）。"""
    try:
        from common.market_time import BJ_TZ

        return BJ_TZ
    except Exception:
        from datetime import timedelta, timezone

        return timezone(timedelta(hours=8))


_BJ_TZ = _bj_tz()

REGISTRY_DIR = ROOT / "cache" / "stock_registry"
DEFAULT_DB = REGISTRY_DIR / "registry.db"
DEFAULT_PROJECTION = REGISTRY_DIR / "stock_registry.json"

#: 模块级总开关（对齐 capture_daily.ENABLE_SECTOR_OFFICIAL_REFRESH 惯例）
ENABLE_STOCK_REGISTRY = True

#: 生产 DB 路径（测试守卫比对用，勿改）
_PROD_DB = DEFAULT_DB

# ── 列清单（增删字段时同步这里；ext_json 是免改表的扩展位）──
LONG_COLS = ("symbol", "name", "market", "is_20cm", "industry")
DAILY_COLS = ("limit_up_now", "limit_up_touched", "limit_up_close",
              "boards", "pct", "price", "seal_yi", "hs")
STOCK_COLS = (
    "code", *LONG_COLS,
    "limit_up_now", "limit_up_touched", "limit_up_close",
    "boards", "pct", "price", "seal_yi", "hs",
    "daily_asof_date", "daily_asof_ts",
    "source", "first_seen_date", "last_seen_ts", "last_updated_ts",
    "update_count", "data_quality", "ext_json",
)

#: 标签类型白名单（题材≠行业，分列存放）
TAG_TYPES = ("theme", "concept", "board", "sector", "industry")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS stocks(
  code TEXT PRIMARY KEY,
  symbol TEXT,
  name TEXT,
  market TEXT,
  is_20cm INTEGER,
  industry TEXT,
  limit_up_now INTEGER,
  limit_up_touched INTEGER,
  limit_up_close INTEGER,
  boards INTEGER,
  pct REAL,
  price REAL,
  seal_yi REAL,
  hs REAL,
  daily_asof_date TEXT,
  daily_asof_ts TEXT,
  source TEXT,
  first_seen_date TEXT NOT NULL,
  last_seen_ts TEXT,
  last_updated_ts TEXT,
  update_count INTEGER NOT NULL DEFAULT 0,
  data_quality TEXT,
  ext_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_stocks_name ON stocks(name);
CREATE INDEX IF NOT EXISTS idx_stocks_seen ON stocks(last_seen_ts);
CREATE INDEX IF NOT EXISTS idx_stocks_daily_date ON stocks(daily_asof_date);

CREATE TABLE IF NOT EXISTS stock_daily(
  code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  limit_up_now INTEGER,
  limit_up_touched INTEGER,
  limit_up_close INTEGER,
  boards INTEGER,
  pct REAL,
  price REAL,
  seal_yi REAL,
  hs REAL,
  industry TEXT,
  first_hit_ts TEXT,
  last_hit_ts TEXT,
  sources TEXT,
  PRIMARY KEY(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_date ON stock_daily(trade_date);

CREATE TABLE IF NOT EXISTS stock_tags(
  code TEXT NOT NULL,
  tag_type TEXT NOT NULL,
  tag_value TEXT NOT NULL,
  source TEXT,
  first_seen TEXT,
  last_seen TEXT,
  PRIMARY KEY(code, tag_type, tag_value)
);
CREATE INDEX IF NOT EXISTS idx_tags_val ON stock_tags(tag_type, tag_value);

CREATE TABLE IF NOT EXISTS stock_observations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  trade_date TEXT,
  asof_ts TEXT NOT NULL,
  source TEXT NOT NULL,
  payload TEXT NOT NULL,
  dedup_hash TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_dedup ON stock_observations(dedup_hash);
CREATE INDEX IF NOT EXISTS idx_obs_code ON stock_observations(code, asof_ts);

CREATE TABLE IF NOT EXISTS stock_name_alias(
  name TEXT NOT NULL,
  code TEXT NOT NULL DEFAULT '',
  first_seen TEXT,
  last_seen TEXT,
  hit_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(name, code)
);
CREATE INDEX IF NOT EXISTS idx_alias_name ON stock_name_alias(name);

CREATE TABLE IF NOT EXISTS stock_pending_review(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL,
  raw TEXT,
  created_ts TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stock_technical_daily(
  code TEXT NOT NULL,
  bar_date TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ok',
  close REAL,
  high REAL,
  low REAL,
  ma5 REAL,
  ma10 REAL,
  ma20 REAL,
  ret_3d REAL,
  ret_5d REAL,
  ret_10d REAL,
  high_20d REAL,
  high_60d REAL,
  high_120d REAL,
  new_high_20d INTEGER,
  new_high_60d INTEGER,
  new_high_120d INTEGER,
  ma_bullish INTEGER,
  volume_ratio_5 REAL,
  data_quality TEXT,
  trigger TEXT,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  PRIMARY KEY(code, bar_date)
);
CREATE INDEX IF NOT EXISTS idx_technical_date ON stock_technical_daily(bar_date);
CREATE INDEX IF NOT EXISTS idx_technical_code ON stock_technical_daily(code, bar_date);

CREATE TABLE IF NOT EXISTS stock_technical_queue(
  code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  first_enqueued_ts TEXT NOT NULL,
  last_enqueued_ts TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  trigger TEXT,
  PRIMARY KEY(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_technical_queue_status ON stock_technical_queue(status, trade_date);
"""

SCHEMA_VERSION = "2"

# 技术派生层：它是可重算的每日快照，不回写客观身份/题材字段。
TECHNICAL_COLS = (
    "bar_date", "source", "status", "close", "high", "low",
    "ma5", "ma10", "ma20", "ret_3d", "ret_5d", "ret_10d",
    "high_20d", "high_60d", "high_120d", "new_high_20d",
    "new_high_60d", "new_high_120d", "ma_bullish", "volume_ratio_5",
    "data_quality", "trigger",
)


# ══════════════════════════════════════════════════════════════════════
# 开关 / 路径 / 守卫
# ══════════════════════════════════════════════════════════════════════

def _env_flag(name: str, default: str = "1") -> bool:
    """环境变量布尔开关；未设置 → 用 default；置空视同未设置。"""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        raw = default
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "n")


def registry_enabled() -> bool:
    """总开关：`ENABLE_STOCK_REGISTRY` 且有 `STOCK_REGISTRY_ENABLED`。"""
    return bool(ENABLE_STOCK_REGISTRY) and _env_flag("STOCK_REGISTRY_ENABLED", "1")


def intraday_enabled() -> bool:
    return registry_enabled() and _env_flag("STOCK_REGISTRY_INTRADAY", "1")


def eod_enabled() -> bool:
    return registry_enabled() and _env_flag("STOCK_REGISTRY_EOD", "1")


def eod_dump_enabled() -> bool:
    """盘后写入成功后是否刷新只读投影。

    投影是给人看/给前端读的镜像，只在盘后刷一次即可（用户 2026-10-15 定：
    **盘中每 30 秒不刷**，接受盘中投影滞后，以盘后为准）。
    """
    return registry_enabled() and _env_flag("STOCK_REGISTRY_EOD_DUMP", "1")


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """解析 DB 路径：显式入参 > 环境变量 > 默认。"""
    if db_path is not None:
        return Path(db_path)
    env = os.environ.get("LIMAO_REGISTRY_DB")
    if env and str(env).strip():
        return Path(str(env).strip())
    return DEFAULT_DB


def _log(msg: str) -> None:
    try:
        from common.logging_setup import get_logger

        get_logger("stock_registry").warning(msg)
    except Exception:
        print(f"[stock_registry] {msg}")


def _guard_production_write(path: Path, *, explicit: bool) -> None:
    """⛔ 测试期禁止写生产底库（复刻 `zt_archive._guard_test_write` 血案防线）。

    事故同型：夹具里的假票被写进真实数据文件，真数据被静默覆盖。
    这里把「静默写穿」变成「测试当场报错」，强制调用方显式注入 `db_path=`。
    """
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if explicit:
        return
    try:
        same = Path(path).resolve() == _PROD_DB.resolve()
    except Exception:
        return
    if same:
        raise RuntimeError(
            f"测试期禁止写生产底库 {path} —— 请显式传 db_path=(tmp,…) 注入临时库")


# ══════════════════════════════════════════════════════════════════════
# 严格解析器（脏数据唯一入口）
# ══════════════════════════════════════════════════════════════════════

_TRUE_TOKENS = {"1", "true", "yes", "y", "t", "是"}
_FALSE_TOKENS = {"0", "false", "no", "n", "f", "否"}
_NULL_TOKENS = {"", "-", "--", "none", "null", "nan", "n/a", "na", "unknown"}


def parse_tri(value: Any, field: str, flags: list[str]) -> Optional[int]:
    """三态解析 → 1 / 0 / None。非法值返回 None 并在 `flags` 记脏。

    ⛔ 绝不把任意非空字符串当 True（`bool("false") is True` 是真实事故源）。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        if value in (0, 1):
            return int(value)
        flags.append(f"dirty_type:{field}")
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value == 1.0:
            return 1
        if value == 0.0:
            return 0
        flags.append(f"dirty_type:{field}")
        return None
    token = str(value).strip().lower()
    if token in _NULL_TOKENS:
        return None
    if token in _TRUE_TOKENS:
        return 1
    if token in _FALSE_TOKENS:
        return 0
    flags.append(f"dirty_type:{field}")
    return None


def parse_num(value: Any, field: str, flags: list[str]) -> Optional[float]:
    """数值解析 → float / None。空串与非法值 → None（不记脏，缺失属正常）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        flags.append(f"dirty_type:{field}")
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    token = str(value).strip().replace("%", "").replace(",", "")
    if token.lower() in _NULL_TOKENS:
        return None
    try:
        f = float(token)
    except (TypeError, ValueError):
        flags.append(f"dirty_type:{field}")
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def parse_int(value: Any, field: str, flags: list[str]) -> Optional[int]:
    f = parse_num(value, field, flags)
    return None if f is None else int(f)


def parse_text(value: Any) -> Optional[str]:
    """文本规范化：去空白；空串/空值 → None（视同缺失，不覆盖旧值）。"""
    if value is None:
        return None
    token = str(value).strip()
    if not token or token.lower() in _NULL_TOKENS:
        return None
    return token


def parse_boards(value: Any, flags: list[str]) -> Optional[int]:
    """连板数解析，兼容 `"3天2板"` / `"2"` / `2`。"""
    if value is None:
        return None
    if isinstance(value, bool):
        flags.append("dirty_type:boards")
        return None
    if isinstance(value, (int, float)):
        return parse_int(value, "boards", flags)
    token = str(value).strip()
    if not token or token.lower() in _NULL_TOKENS:
        return None
    found = re.findall(r"(\d+)\s*板", token)
    if found:
        return int(found[-1])          # "3天2板" → 2（取最后一个）
    if re.fullmatch(r"\d+", token):
        return int(token)
    flags.append("dirty_type:boards")
    return None


# ══════════════════════════════════════════════════════════════════════
# 代码规范化
# ══════════════════════════════════════════════════════════════════════

_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def normalize_code(symbol: Any) -> Optional[str]:
    """从任意写法提取**恰好 6 位**的A股代码。

    `600000.SH` / `sh600000` / `" 600000 "` → `600000`；取不到 → None（不猜）。
    ⛔ 用前后向断言卡死 6 位，避免 `6000000` 被截成 `600000`。
    """
    if symbol is None:
        return None
    token = str(symbol).strip()
    if not token:
        return None
    m = _CODE_RE.search(token)
    return m.group(1) if m else None


def market_of(code: str) -> Optional[str]:
    """交易所归属（派生）。"""
    if not code:
        return None
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    if code.startswith(("4", "8", "920")):
        return "BJ"
    return None


def is_20cm_code(code: str) -> Optional[bool]:
    """是否 20cm 品种（创业板/科创板/北交所，含 920 新代码）。"""
    if not code:
        return None
    return code.startswith(("300", "301", "688", "689", "8", "4", "920"))


# ══════════════════════════════════════════════════════════════════════
# 交易日期与时间戳
# ══════════════════════════════════════════════════════════════════════

def _is_trading_day(date10: str) -> bool:
    """交易日判定唯一真源 = `common.trading_calendar`（读失败 → False，不猜）。"""
    try:
        from common.trading_calendar import is_a_share_trading_day

        return bool(is_a_share_trading_day(date10))
    except Exception:
        return False


def _coerce_date10(value: Any) -> Optional[str]:
    """宽松取 `YYYY-MM-DD`；非法 → None。"""
    token = str(value or "").strip()[:10]
    try:
        return datetime.strptime(token, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _norm_asof(value: Any) -> Optional[str]:
    """asof 时间戳规范化 → ISO8601（统一带 +08:00）；无法解析 → None（调用方应拒绝该观测）。

    ⚠️ 时区必须补齐：`limit_up_*` 的乱序保护靠**字符串比大小**，
    裸时间戳（无 offset）与国际时间戳混在一起会静默错序。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _apply_bj(value).isoformat(timespec="seconds")
    token = str(value).strip()
    if not token:
        return None
    parsed: Optional[datetime] = None
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
            try:
                parsed = datetime.strptime(token, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return _apply_bj(parsed).isoformat(timespec="seconds")


def _apply_bj(dt: datetime) -> datetime:
    """naive → 视作北京时间；aware → 换算到北京时间。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_BJ_TZ)
    return dt.astimezone(_BJ_TZ)


def now_iso() -> str:
    """当前北京时间 ISO8601（+08:00）。"""
    try:
        from common.market_time import iso_bj

        return iso_bj()
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _today_iso() -> str:
    try:
        from common.market_time import today

        return today()
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _max_ts(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """取较新的时间戳（字符串 ISO8601 可直接比大小）。"""
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


def _min_ts(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if not a:
        return b
    if not b:
        return a
    return a if a <= b else b


# ══════════════════════════════════════════════════════════════════════
# 连接与建表
# ══════════════════════════════════════════════════════════════════════

def connect(db_path: str | Path | None = None, *, create: bool = True,
            explicit: bool | None = None) -> sqlite3.Connection:
    """打开底库连接（WAL + 事务安全 PRAGMA）。默认建表。

    `explicit=None` → 自动判断：显式传入 `db_path` 即视为已注入（测试友好）。
    """
    path = resolve_db_path(db_path)
    explicit_injected = explicit if explicit is not None else (db_path is not None)
    _guard_production_write(path, explicit=explicit_injected)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        if create:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )
    except Exception:
        conn.close()
        raise
    return conn


def init_db(db_path: str | Path | None = None) -> Path:
    """建库建表（幂等）→ 返回 DB 路径。"""
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return resolve_db_path(db_path)


def _row_to_dict(row: sqlite3.Row | None) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None


# ══════════════════════════════════════════════════════════════════════
# 观测标准化
# ══════════════════════════════════════════════════════════════════════

def normalize_observation(raw: dict[str, Any]) -> tuple[Optional[dict[str, Any]], list[str]]:
    """把上游任意形态的一条个股记录 → 标准观测。

    返回 `(obs | None, flags)`；`obs is None` 表示**必须拒绝**（无 6 位代码）。
    `obs` 中的键与 `STOCK_COLS` 对齐；缺失一律 `None`（不覆盖规则的上游保证）。
    """
    flags: list[str] = []
    symbol_raw = raw.get("symbol") or raw.get("code") or raw.get("dm")
    code = normalize_code(symbol_raw)
    if not code:
        return None, flags + ["no_code"]

    obs: dict[str, Any] = {
        "code": code,
        "symbol": parse_text(raw.get("symbol")) or parse_text(symbol_raw),
        "name": parse_text(raw.get("name") or raw.get("mc")),
        "market": market_of(code),
        "is_20cm": 1 if is_20cm_code(code) else 0,
        "industry": parse_text(raw.get("industry") or raw.get("sector")),
        "limit_up_now": parse_tri(raw.get("limit_up_now", raw.get("up")), "limit_up_now", flags),
        "limit_up_touched": parse_tri(
            raw.get("limit_up_touched", raw.get("touched")), "limit_up_touched", flags),
        "limit_up_close": parse_tri(
            raw.get("limit_up_close", raw.get("close_limit_up")), "limit_up_close", flags),
        "boards": parse_boards(raw.get("boards"), flags),
        "pct": parse_num(raw.get("pct"), "pct", flags),
        "price": parse_num(raw.get("price"), "price", flags),
        "seal_yi": parse_num(raw.get("seal_yi", raw.get("seal")), "seal_yi", flags),
        "hs": parse_num(raw.get("hs"), "hs", flags),
    }
    # 语义补全：当前封板 / 收盘封板 ⇒ **当日必然触过板**，闩锁自动置 1。
    # （调用方漏传 touched 时不应丢掉这个确定性结论。）
    if obs["limit_up_touched"] is None and (obs["limit_up_now"] == 1 or obs["limit_up_close"] == 1):
        obs["limit_up_touched"] = 1
    # 多值标签与扩展位不落 STOCK_COLS，单独携带（normalize 白名单会丢掉未知键）
    obs["_tags"] = _normalize_tags(raw)
    ext = raw.get("ext") if isinstance(raw.get("ext"), dict) else raw.get("ext_json")
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except Exception:
            ext = None
    obs["ext_json"] = ext if isinstance(ext, dict) else None
    return obs, flags


def _normalize_tags(raw: dict[str, Any]) -> list[tuple[str, str]]:
    """抽出多值标签 `[(tag_type, tag_value)]`。题材与行业分列，不强求跨类型去重。"""
    out: list[tuple[str, str]] = []
    # 行业单值（必盈 hy / 东财 f100）—— 与题材**分列**，不可混为一列
    industry = parse_text(raw.get("industry") or raw.get("sector"))
    if industry:
        out.append(("industry", industry))
    for tag_type, key in (("theme", "themes"), ("theme", "plates"),
                          ("concept", "concepts"), ("board", "boards_tags"),
                          ("sector", "sectors")):
        values = raw.get(key)
        if values is None:
            continue
        if isinstance(values, dict):
            values = list(values.values())
        if isinstance(values, (str, int, float)):
            values = [values]
        if not isinstance(values, (list, tuple, set)):
            continue
        for item in values:
            if isinstance(item, dict):
                value = item.get("name") or item.get("plate_name") or item.get("value")
            else:
                value = item
            token = parse_text(value)
            if token:
                out.append((tag_type, token))
    return out


def _dedup_hash(code: str, trade_date: str, source: str, obs: dict[str, Any]) -> str:
    payload = json.dumps(
        {k: obs.get(k) for k in (*LONG_COLS, *DAILY_COLS)},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(
        f"{code}|{trade_date}|{source}|{payload}".encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════
# 写入主流程
# ══════════════════════════════════════════════════════════════════════

def upsert_many(
    observations: Iterable[dict[str, Any]],
    *,
    trade_date: str,
    asof_ts: str,
    source: str,
    db_path: str | Path | None = None,
    allow_backfill: bool = False,
    conn: sqlite3.Connection | None = None,
    enqueue_technical: bool = False,
) -> dict[str, Any]:
    """批量增量写入（单事务）。

    `trade_date` / `asof_ts` **必须由调用方显式给出**——禁止隐式「今天」，
    否则盘后补写会把旧数据当成当日状态。

    返回统计：`{ok, inserted, updated, unchanged, skipped, rejected, pending}`
    """
    date10 = _coerce_date10(trade_date)
    stamp = _norm_asof(asof_ts)
    summary = {"ok": False, "inserted": 0, "updated": 0, "unchanged": 0,
               "skipped": 0, "rejected": 0, "pending": 0, "reason": ""}
    seen_codes: set[str] = set()

    if not date10:
        summary["reason"] = f"trade_date 无法解析: {trade_date!r}"
        _log(f"⛔ 拒绝写入：{summary['reason']}")
        return summary
    if not stamp:
        summary["reason"] = f"asof_ts 无法解析: {asof_ts!r}"
        _log(f"⛔ 拒绝写入：{summary['reason']}")
        return summary
    if not allow_backfill:
        if date10 > _today_iso():
            summary["reason"] = f"trade_date 不可为未来:{date10}"
            _log(f"⛔ 拒绝写入：{summary['reason']}")
            return summary
        if not _is_trading_day(date10):
            summary["reason"] = f"{date10} 非A股交易日"
            _log(f"⛔ 拒绝写入：{summary['reason']}")
            return summary

    own = conn is None
    c = conn or connect(db_path)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            for raw in observations:
                if not isinstance(raw, dict):
                    summary["skipped"] += 1
                    continue
                obs, flags = normalize_observation(raw)
                if obs is None:
                    _record_pending(c, "no_code", raw, stamp)
                    summary["pending"] += 1
                    summary["rejected"] += 1
                    continue
                outcome = _apply_one(c, obs, flags, date10, stamp, source)
                summary[outcome] += 1
                seen_codes.add(obs["code"])
            c.execute("COMMIT")
            summary["ok"] = True
        except Exception:
            c.execute("ROLLBACK")
            raise
        if enqueue_technical and seen_codes:
            q = enqueue_technical_derivatives(seen_codes, trade_date=date10,
                                              trigger=source, db_path=db_path)
            summary["technical_enqueued"] = q.get("enqueued", 0)
    finally:
        if own:
            c.close()
    return summary


def _record_pending(conn: sqlite3.Connection, kind: str, raw: dict[str, Any],
                    stamp: str, detail: str = "") -> None:
    try:
        conn.execute(
            "INSERT INTO stock_pending_review(kind, detail, raw, created_ts) VALUES(?,?,?,?)",
            (kind, detail or kind,
             json.dumps(raw, ensure_ascii=False, default=str)[:2000], stamp),
        )
    except Exception:
        pass


def _merge_text(cur: Optional[str], new: Optional[str]) -> Optional[str]:
    """文本合并：新值非空才覆盖（缺失不覆盖）。"""
    return new if new not in (None, "") else cur


def _merge_daily_cur(new_val: Any, old_val: Any) -> tuple[Any, bool]:
    """当前态字段合并：新值非 None 才覆盖。"""
    if new_val is None:
        return old_val, False
    return new_val, new_val != old_val


def _merge_ext(cur_json: Optional[str], incoming: Any) -> tuple[Optional[str], bool]:
    """扩展列合并：字典按键覆盖（值为 None 的键不动）。"""
    if not isinstance(incoming, dict) or not incoming:
        return cur_json, False
    try:
        cur = json.loads(cur_json) if cur_json else {}
        if not isinstance(cur, dict):
            cur = {}
    except Exception:
        cur = {}
    merged = dict(cur)
    changed = False
    for k, v in incoming.items():
        if v is None:
            continue
        if merged.get(k) != v:
            merged[k] = v
            changed = True
    if not changed:
        return cur_json, False
    return json.dumps(merged, ensure_ascii=False, sort_keys=True), True


def _apply_one(conn: sqlite3.Connection, obs: dict[str, Any], flags: list[str],
               date10: str, stamp: str, source: str) -> str:
    """把一条观测合并进 `stocks` + `stock_daily` + 标签/别名/审计。返回 outcome。"""
    code = obs["code"]
    cur = _row_to_dict(conn.execute(
        "SELECT * FROM stocks WHERE code=?", (code,)).fetchone())

    is_eod = source.startswith("eod") or source.startswith("backfill")

    if cur is None:
        row = {k: None for k in STOCK_COLS}
        row.update({
            "code": code,
            "first_seen_date": date10,
            "update_count": 1,
            "source": source,
            "last_seen_ts": stamp,
            "last_updated_ts": stamp,
            "daily_asof_date": date10,
            "daily_asof_ts": stamp,
        })
        for col in LONG_COLS:
            row[col] = obs.get(col)
        for col in DAILY_COLS:
            val = obs.get(col)
            if col == "limit_up_close" and not is_eod:
                val = None
            row[col] = val
        row["data_quality"] = json.dumps(flags, ensure_ascii=False) if flags else None
        _write_stock_row(conn, row)
        outcome = "inserted"
    else:
        row = dict(cur)
        cur_date = cur.get("daily_asof_date")
        cur_daily_ts = cur.get("daily_asof_ts")
        changed = False

        # ── 长期属性：非空才覆盖（乱序的旧观测不夺权）──
        order_ok = not (cur.get("last_seen_ts") and stamp < cur["last_seen_ts"])
        if order_ok:
            for col in LONG_COLS:
                if col == "symbol":
                    continue          # 溯源信息：首次登记为准，避免多源写法交替刷 update_count
                val = obs.get(col)
                if col in ("market", "is_20cm"):
                    if val is not None and row.get(col) != val:
                        row[col] = val
                        changed = True
                    continue
                merged = _merge_text(row.get(col), val)
                if merged != row.get(col):
                    row[col] = merged
                    changed = True
        if not row.get("symbol") and obs.get("symbol"):
            row["symbol"] = obs["symbol"]
            changed = True

        # ── 逐日属性：跨日轮换 / 同日按 asof 新者胜 / 旧日不回写 ──
        if cur_date is None or date10 > cur_date:
            for col in DAILY_COLS:
                val = obs.get(col)
                if col == "limit_up_close" and not is_eod:
                    val = None
                if row.get(col) != val:
                    row[col] = val
                    changed = True
            row["daily_asof_date"] = date10
            row["daily_asof_ts"] = stamp
            changed = True
        elif date10 == cur_date:
            stale = bool(cur_daily_ts and stamp < cur_daily_ts)
            if not stale:
                for col in DAILY_COLS:
                    if col in ("limit_up_touched", "limit_up_close"):
                        continue          # 闩锁与收盘另处理
                    row[col], did = _merge_daily_cur(obs.get(col), row.get(col))
                    changed = changed or did
                close_val = obs.get("limit_up_close")
                if close_val is not None and is_eod:
                    row["limit_up_close"], did = _merge_daily_cur(close_val, row.get("limit_up_close"))
                    changed = changed or did
            # 闩锁：当日一旦为 1，永不回 0（乱序也要累计）
            touched = obs.get("limit_up_touched")
            if touched == 1 and row.get("limit_up_touched") != 1:
                row["limit_up_touched"] = 1
                changed = True
            elif touched == 0 and row.get("limit_up_touched") is None:
                row["limit_up_touched"] = 0
                changed = True
            row["daily_asof_ts"] = _max_ts(cur_daily_ts, stamp)
        # date10 < cur_date → 旧日补写：只进 stock_daily，不动当日快照

        merged_quality = sorted(set(
            (json.loads(cur.get("data_quality") or "[]") if cur.get("data_quality") else [])
            + flags))
        if merged_quality:
            encoded = json.dumps(merged_quality, ensure_ascii=False)
            if encoded != cur.get("data_quality"):
                row["data_quality"] = encoded
                changed = True

        ext_json, ext_changed = _merge_ext(cur.get("ext_json"), obs.get("ext_json"))
        row["ext_json"] = ext_json
        changed = changed or ext_changed

        row["last_seen_ts"] = _max_ts(cur.get("last_seen_ts"), stamp)
        row["source"] = source
        if changed:
            row["update_count"] = int(cur.get("update_count") or 0) + 1
            row["last_updated_ts"] = stamp
        _write_stock_row(conn, row)
        outcome = "updated" if changed else "unchanged"

    _upsert_daily(conn, obs, date10, stamp, source, is_eod)
    _upsert_tags(conn, code, obs, date10, stamp, source)
    _upsert_alias(conn, obs.get("name"), code, date10, stamp)
    _insert_observation(conn, code, date10, stamp, source, obs)
    return outcome


def _write_stock_row(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = [c for c in STOCK_COLS if c != "code"]
    sql = ("UPDATE stocks SET " + ", ".join(f"{c}=:{c}" for c in cols)
           + " WHERE code=:code")
    params = {c: row.get(c) for c in cols}
    params["code"] = row["code"]
    if conn.execute("SELECT 1 FROM stocks WHERE code=?", (row["code"],)).fetchone():
        conn.execute(sql, params)
    else:
        keys = ["code", *cols]
        conn.execute(
            f"INSERT INTO stocks({','.join(keys)}) VALUES({','.join(':' + k for k in keys)})",
            {k: row.get(k) for k in keys})


_DAILY_TABLE_COLS = ("limit_up_now", "limit_up_touched", "limit_up_close",
                     "boards", "pct", "price", "seal_yi", "hs", "industry")


def _upsert_daily(conn: sqlite3.Connection, obs: dict[str, Any], date10: str,
                  stamp: str, source: str, is_eod: bool) -> None:
    """逐日观测：append-only 语义下的同日合并（闩锁 + asof 新者胜）。"""
    cur = _row_to_dict(conn.execute(
        "SELECT * FROM stock_daily WHERE code=? AND trade_date=?",
        (obs["code"], date10)).fetchone())

    if cur is None:
        row = {c: obs.get(c) for c in _DAILY_TABLE_COLS}
        if not is_eod:
            row["limit_up_close"] = None
        row.update({
            "code": obs["code"], "trade_date": date10,
            "first_hit_ts": stamp, "last_hit_ts": stamp,
            "sources": json.dumps([source], ensure_ascii=False),
        })
        keys = ["code", "trade_date", *_DAILY_TABLE_COLS,
                "first_hit_ts", "last_hit_ts", "sources"]
        conn.execute(
            f"INSERT INTO stock_daily({','.join(keys)}) VALUES({','.join(':' + k for k in keys)})",
            {k: row.get(k) for k in keys})
        return

    row = dict(cur)
    stale = bool(cur.get("last_hit_ts") and stamp < cur["last_hit_ts"])
    if not stale:
        for col in _DAILY_TABLE_COLS:
            if col in ("limit_up_touched", "limit_up_close"):
                continue
            merged = obs.get(col)
            if col == "industry":
                merged = _merge_text(row.get(col), merged)
            elif merged is None:
                merged = row.get(col)
            row[col] = merged
        if is_eod and obs.get("limit_up_close") is not None:
            row["limit_up_close"] = obs["limit_up_close"]
    touched = obs.get("limit_up_touched")
    if touched == 1:
        row["limit_up_touched"] = 1
    elif touched == 0 and row.get("limit_up_touched") is None:
        row["limit_up_touched"] = 0

    row["first_hit_ts"] = _min_ts(cur.get("first_hit_ts"), stamp)
    row["last_hit_ts"] = _max_ts(cur.get("last_hit_ts"), stamp)
    try:
        sources = json.loads(cur.get("sources") or "[]")
        if not isinstance(sources, list):
            sources = []
    except Exception:
        sources = []
    if source not in sources:
        sources.append(source)
    row["sources"] = json.dumps(sources[-20:], ensure_ascii=False)

    cols = [*_DAILY_TABLE_COLS, "first_hit_ts", "last_hit_ts", "sources"]
    conn.execute(
        "UPDATE stock_daily SET " + ", ".join(f"{c}=:{c}" for c in cols)
        + " WHERE code=:code AND trade_date=:trade_date",
        {**{c: row.get(c) for c in cols},
         "code": obs["code"], "trade_date": date10})


def _upsert_tags(conn: sqlite3.Connection, code: str, obs: dict[str, Any],
                 date10: str, stamp: str, source: str) -> None:
    tags = obs.get("_tags")
    if tags is None:
        tags = _normalize_tags(obs)
    for tag_type, tag_value in tags:
        if tag_type not in TAG_TYPES:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO stock_tags(code, tag_type, tag_value, source, first_seen, last_seen)"
            " VALUES(?,?,?,?,?,?)", (code, tag_type, tag_value, source, date10, date10))
        conn.execute(
            "UPDATE stock_tags SET last_seen=?, source=?,"
            " first_seen=COALESCE(first_seen, ?) WHERE code=? AND tag_type=? AND tag_value=?",
            (date10, source, date10, code, tag_type, tag_value))


def _upsert_alias(conn: sqlite3.Connection, name: Optional[str], code: str,
                  date10: str, stamp: str) -> None:
    """名称→代码别名。名称**只做检索**，绝不作为主对象键（不产生重复记录）。"""
    if not name:
        return
    conn.execute(
        "INSERT OR IGNORE INTO stock_name_alias(name, code, first_seen, last_seen, hit_count)"
        " VALUES(?,?,?,?,0)", (name, code, date10, date10))
    conn.execute(
        "UPDATE stock_name_alias SET last_seen=?, hit_count=hit_count+1,"
        " first_seen=COALESCE(first_seen, ?) WHERE name=? AND code=?",
        (date10, date10, name, code))


def _insert_observation(conn: sqlite3.Connection, code: str, date10: str,
                        stamp: str, source: str, obs: dict[str, Any]) -> None:
    """原始观测审计。`dedup_hash` 唯一索引保证同日同源同值的重复观测只留一条。"""
    digest = _dedup_hash(code, date10, source, obs)
    conn.execute(
        "INSERT OR IGNORE INTO stock_observations(code, trade_date, asof_ts, source, payload, dedup_hash)"
        " VALUES(?,?,?,?,?,?)",
        (code, date10, stamp, source,
         json.dumps({k: obs.get(k) for k in (*LONG_COLS, *DAILY_COLS)}, ensure_ascii=False),
         digest))


# ══════════════════════════════════════════════════════════════════════
# 便捷写入：单票
# ══════════════════════════════════════════════════════════════════════

def upsert_one(raw: dict[str, Any], *, trade_date: str, asof_ts: str,
               source: str, db_path: str | Path | None = None,
               allow_backfill: bool = False) -> dict[str, Any]:
    """单票增量写入（命中即更新）。"""
    return upsert_many([raw], trade_date=trade_date, asof_ts=asof_ts,
                       source=source, db_path=db_path, allow_backfill=allow_backfill)


# ══════════════════════════════════════════════════════════════════════
# 便捷写入：业务入口适配（盘中 / 盘后）
# ══════════════════════════════════════════════════════════════════════

def build_intraday_observations(stocks: dict | None = None,
                                pre_stocks: dict | None = None,
                                rising: Sequence[dict] | None = None) -> list[dict]:
    """把盘中已解析的三类来源 → 标准观测列表（**只取样，不落库**）。

    证据强度不同，**不混为一谈**：
      · 选股宝异动池 `stocks`：`up_limit` 字段 → 可写 `limit_up_now` / 闩锁；
        其 `plates` 是**题材**。
      · 东财涨幅榜 `rising`：`up` 是**涨幅阈值推断**，不是确切封板 →
        只作弱证据，仅在为真时写；`industry` 是**行业**。
    即便 `up` 为假也**不写 0**：源看不到全市场，"不在名单" 推不出"没涨停"。
    """
    out: list[dict] = []

    def _from_pool(pool: dict | None, *, strong: bool) -> None:
        for item in (pool or {}).values():
            if not isinstance(item, dict):
                continue
            code = normalize_code(item.get("code") or item.get("symbol"))
            if not code:
                continue
            obs: dict[str, Any] = {
                "code": code,
                "name": item.get("name"),
                "boards": item.get("boards"),
                "pct": item.get("pct"),
                "price": item.get("price"),
                "themes": [p.get("name") if isinstance(p, dict) else p
                           for p in (item.get("plates") or [])],
            }
            if strong and item.get("up") is True:
                obs["limit_up_now"] = 1
                obs["limit_up_touched"] = 1
            out.append(obs)

    _from_pool(stocks, strong=True)
    _from_pool(pre_stocks, strong=False)

    for item in (rising or []):
        if not isinstance(item, dict):
            continue
        code = normalize_code(item.get("code"))
        if not code:
            continue
        obs = {
            "code": code,
            "name": item.get("name"),
            "pct": item.get("pct"),
            "price": item.get("price"),
            "industry": item.get("industry"),
        }
        if item.get("up") is True:
            obs["limit_up_now"] = 1
            obs["limit_up_touched"] = 1
        out.append(obs)
    return out


def ingest_intraday(*, trade_date: str, asof_ts: str,
                    stocks: dict | None = None, pre_stocks: dict | None = None,
                    rising: Sequence[dict] | None = None,
                    db_path: str | Path | None = None) -> dict[str, Any]:
    """盘中入口：命中即增量更新。未命中的票**原样保留**。"""
    if not intraday_enabled():
        return {"ok": False, "reason": "registry/intraday 开关关闭", "skipped": 0}
    obs = build_intraday_observations(stocks, pre_stocks, rising)
    if not obs:
        return {"ok": False, "reason": "无观测", "skipped": 0}
    return upsert_many(obs, trade_date=trade_date, asof_ts=asof_ts,
                       source="intraday:xuangubao+em", db_path=db_path,
                       enqueue_technical=True)


def _xgb_code(value: Any) -> Optional[str]:
    """选股宝快照代码规范化；无代码时返回 None，不用名称猜代码。"""
    return normalize_code(value)


def build_xgb_snapshot_observations(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """把已落盘的选股宝盘中快照转换为底库观测（只取样，不落库）。

    可靠映射：
    - `hot_spots[].stocks` 的 sector → 题材；
    - `top5[].zt_stocks` 的主题名 → 题材，official.boards → 板块标签；
    - `limit_up_stocks` 是本轮选股宝已确认的涨停集合，只写正向涨停证据；
    - `leaders` / `early[].buy` 作为补充命中来源。

    只接收带 code 的个股。名称不能反推代码；同一代码在多个题材出现时合并标签，
    不创建重复对象。缺失字段由底库的字段级合并规则处理，不覆盖已有值。
    """
    if not isinstance(snapshot, dict):
        return []
    by_code: dict[str, dict[str, Any]] = {}

    def add(item: Any, *, theme: Any = None, board_tags: list[Any] | None = None,
            limit_up: bool = False) -> None:
        if not isinstance(item, dict):
            return
        code = _xgb_code(item.get("code") or item.get("symbol"))
        if not code:
            return
        row = by_code.setdefault(code, {"code": code, "themes": [], "boards_tags": [],
                                        "sectors": []})
        for key in ("name", "price", "pct", "boards"):
            value = item.get(key)
            if value not in (None, "", "-"):
                row[key] = value
        if theme not in (None, "", "其他"):
            values = theme if isinstance(theme, (list, tuple, set)) else [theme]
            row["themes"].extend(str(x).strip() for x in values if str(x).strip())
        if board_tags:
            row["boards_tags"].extend(str(x).strip() for x in board_tags if str(x).strip())
            # 主表 industry 只在原值缺失时填充；已有行业由原权威来源保留。
            if not row.get("industry"):
                first = next((str(x).strip() for x in board_tags if str(x).strip()), None)
                if first:
                    row["industry"] = first
        if limit_up:
            row["limit_up_now"] = 1
            row["limit_up_touched"] = 1

    for group in snapshot.get("hot_spots") or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("stocks") or []:
            add(item, theme=group.get("sector"))

    for group in snapshot.get("top5") or []:
        if not isinstance(group, dict):
            continue
        theme = group.get("name")
        official = group.get("official") or {}
        boards = [b.get("name") for b in (official.get("boards") or [])
                  if isinstance(b, dict) and b.get("name")]
        for item in group.get("zt_stocks") or []:
            add(item, theme=theme, board_tags=boards, limit_up=True)
        add(group.get("leader"), theme=theme, board_tags=boards, limit_up=True)
        for item in group.get("followers") or []:
            # 选股宝跟涨表常只给 name/pct，无 code；有 code 时才沉淀。
            add(item, theme=theme, board_tags=boards)

    for item in snapshot.get("limit_up_stocks") or []:
        raw_themes = str(item.get("sector") or "").replace("/", "、").split("、")
        add(item, theme=raw_themes, limit_up=True)

    for item in snapshot.get("leaders") or []:
        add(item, theme=item.get("sector"))
    for group in snapshot.get("early") or []:
        if isinstance(group, dict):
            add(group.get("buy"), theme=group.get("sector"))

    result: list[dict[str, Any]] = []
    for row in by_code.values():
        for key in ("themes", "boards_tags", "sectors"):
            row[key] = sorted({x for x in row.get(key, []) if x})
        result.append(row)
    return result


def ingest_xgb_snapshot(snapshot: dict[str, Any], *, trade_date: str | None = None,
                        asof_ts: str | None = None,
                        db_path: str | Path | None = None) -> dict[str, Any]:
    """用选股宝已落盘快照增量填充底库；未命中的个股不动。"""
    if not registry_enabled():
        return {"ok": False, "reason": "registry 开关关闭", "skipped": 0}
    if not isinstance(snapshot, dict):
        return {"ok": False, "reason": "snapshot 非 dict", "skipped": 0}
    stamp = _norm_asof(asof_ts or snapshot.get("ts"))
    if not stamp:
        return {"ok": False, "reason": "snapshot 缺合法 ts", "skipped": 0}
    date10 = _coerce_date10(trade_date or stamp[:10])
    if not date10:
        return {"ok": False, "reason": "snapshot 缺合法日期", "skipped": 0}
    obs = build_xgb_snapshot_observations(snapshot)
    if not obs:
        return {"ok": False, "reason": "选股宝快照无可识别代码", "skipped": 0}
    return upsert_many(obs, trade_date=date10, asof_ts=stamp,
                       source="xuangubao:snapshot", db_path=db_path,
                       enqueue_technical=True)


def ingest_eod_snapshot(snap: dict, *, trade_date: str | None = None,
                        asof_ts: str | None = None,
                        db_path: str | Path | None = None) -> dict[str, Any]:
    """盘后入口：涨停池快照 → 收盘权威口径（唯一允许写 `limit_up_close` 的来源）。

    ⚠️ 只对**池内个股**写 1；额外可选地把"当日曾触板但收盘不在池内"的票写 0
    （`STOCK_REGISTRY_EOD_CLOSE_ZERO`，默认开）——这依赖必盈池是**全市场**涨停名单，
    且只作用于当日已有记录的票，不用全市场扫描。
    """
    if not eod_enabled():
        return {"ok": False, "reason": "registry/eod 开关关闭", "skipped": 0}
    if not isinstance(snap, dict):
        return {"ok": False, "reason": "snap 非 dict", "skipped": 0}
    date10 = _coerce_date10(trade_date or snap.get("date"))
    if not date10:
        return {"ok": False, "reason": "snap 缺 date", "skipped": 0}
    stamp = _norm_asof(asof_ts or snap.get("generated_ts")) or f"{date10}T15:00:00+08:00"

    pool = snap.get("zt_list") or []
    obs: list[dict] = []
    for it in pool:
        if not isinstance(it, dict):
            continue
        code = normalize_code(it.get("code"))
        if not code:
            continue
        obs.append({
            "code": code,
            "name": it.get("name"),
            "symbol": it.get("code"),
            "boards": it.get("boards"),
            "pct": it.get("pct"),
            "price": it.get("price"),
            "seal_yi": it.get("seal"),
            "hs": it.get("hs"),
            "industry": it.get("sector"),      # 必盈 hy = 行业，不是题材
            "limit_up_now": 1,
            "limit_up_touched": 1,
            "limit_up_close": 1,
        })
    if not obs:
        return {"ok": False, "reason": "涨停池为空", "skipped": 0}

    result = upsert_many(obs, trade_date=date10, asof_ts=stamp,
                         source="eod:biying_zt", db_path=db_path,
                         enqueue_technical=True)

    if _env_flag("STOCK_REGISTRY_EOD_CLOSE_ZERO", "1"):
        try:
            result["close_zero"] = _mark_absent_from_pool(
                date10, stamp, {o["code"] for o in obs}, db_path=db_path)
        except Exception as e:  # noqa: BLE001 — 非致命
            _log(f"⚠️ 收盘未封标记失败（不影响主流程）: {e}")

    # 投影刷新：**只在盘后**刷一次（用户 2026-10-15 定，盘中每 30 秒不刷）。
    # ⛔ 放在成功路径末尾 —— 上面的"池为空/开关关闭"等早退分支不刷（无变更无意义）。
    # ⛔ 必须透传 db_path，否则临时库/测试会误刷生产投影。
    if eod_dump_enabled():
        refresh_projection(db_path=db_path)
    return result


def _mark_absent_from_pool(date10: str, stamp: str, pool_codes: set[str],
                           *, db_path: str | Path | None = None) -> int:
    """当日曾触板但收盘不在涨停池 → `limit_up_close=0`（盘后权威源专有）。

    只扫"当日已有记录且 touched=1"的票（数量级几百，不做全市场扫描）。
    """
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT code FROM stocks WHERE daily_asof_date=? AND limit_up_touched=1"
            " AND (limit_up_close IS NULL OR limit_up_close<>0)", (date10,)).fetchall()
        absent = [r["code"] for r in rows if r["code"] not in pool_codes]
        if not absent:
            return 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for code in absent:
                conn.execute(
                    "UPDATE stocks SET limit_up_close=0, last_updated_ts=?,"
                    " update_count=update_count+1 WHERE code=?", (stamp, code))
                conn.execute(
                    "UPDATE stock_daily SET limit_up_close=0 WHERE code=? AND trade_date=?",
                    (code, date10))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return len(absent)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# 查询
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# 技术派生层（可重算、按日快照、与客观身份层分离）
# ══════════════════════════════════════════════════════════════════════

def _technical_schema(conn: sqlite3.Connection) -> None:
    """为旧库补建技术派生表；CREATE TABLE IF NOT EXISTS 可安全重跑。"""
    conn.executescript(_SCHEMA)


def _float_or_none(value: Any) -> Optional[float]:
    try:
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _pct_change(closes: list[float], days: int) -> Optional[float]:
    if len(closes) <= days or not closes[-1] or not closes[-1 - days]:
        return None
    return round((closes[-1] / closes[-1 - days] - 1.0) * 100.0, 3)


def calculate_technical_derivative(code: str, bars: Sequence[dict[str, Any]], *,
                                   source: str = "tencent:qfq",
                                   trigger: str = "daily_hit") -> dict[str, Any]:
    """根据前复权日K计算一票技术快照，不联网、不写库。

    bars 必须按日期升序，字段至少包含 date/close；高低量缺失时只降级对应字段。
    新高采用「当日收盘/最高价 >= 前一交易日滚动窗口最高」的严格口径，
    避免把今天自身放进比较窗口而永远得到假新高。
    """
    code = normalize_code(code) or str(code)
    clean = []
    for bar in bars or []:
        if not isinstance(bar, dict) or not bar.get("date"):
            continue
        close = _float_or_none(bar.get("close"))
        if close is None:
            continue
        clean.append({"date": str(bar["date"])[:10], "close": close,
                      "high": _float_or_none(bar.get("high")),
                      "low": _float_or_none(bar.get("low")),
                      "volume": _float_or_none(bar.get("volume"))})
    clean.sort(key=lambda x: x["date"])
    if not clean:
        return {"code": code, "bar_date": None, "status": "no_data",
                "source": source, "trigger": trigger,
                "data_quality": ["missing_kline"]}
    last = clean[-1]
    closes = [x["close"] for x in clean]
    row: dict[str, Any] = {"code": code, "bar_date": last["date"], "source": source,
                           "status": "ok", "close": last["close"], "high": last["high"],
                           "low": last["low"], "trigger": trigger, "data_quality": []}
    for n in (5, 10, 20):
        row[f"ma{n}"] = round(sum(closes[-n:]) / n, 3) if len(closes) >= n else None
    row["ret_3d"] = _pct_change(closes, 3)
    row["ret_5d"] = _pct_change(closes, 5)
    row["ret_10d"] = _pct_change(closes, 10)
    highs = [x["high"] if x["high"] is not None else x["close"] for x in clean]
    for n in (20, 60, 120):
        key = f"high_{n}d"
        if len(highs) <= n:
            row[key] = None
            row[f"new_high_{n}d"] = None
        else:
            prior_max = max(highs[-n-1:-1])
            row[key] = round(prior_max, 3)
            row[f"new_high_{n}d"] = 1 if last["high"] is not None and last["high"] > prior_max else 0
    row["ma_bullish"] = (1 if row["ma5"] is not None and row["ma10"] is not None
                          and row["ma20"] is not None
                          and row["ma5"] > row["ma10"] > row["ma20"] else
                          (0 if row["ma5"] is not None and row["ma10"] is not None
                           and row["ma20"] is not None else None))
    volumes = [x["volume"] for x in clean]
    if last["volume"] is not None and len(volumes) >= 6 and all(v is not None for v in volumes[-6:-1]):
        avg5 = sum(volumes[-6:-1]) / 5
        row["volume_ratio_5"] = round(last["volume"] / avg5, 3) if avg5 else None
    else:
        row["volume_ratio_5"] = None
    if len(closes) < 20:
        row["data_quality"].append("bars_lt_20")
    if last["high"] is None:
        row["data_quality"].append("missing_high")
    if last["volume"] is None:
        row["data_quality"].append("missing_volume")
    return row


def save_technical_derivative(derivative: dict[str, Any], *,
                              db_path: str | Path | None = None) -> dict[str, Any]:
    """幂等写入技术派生层；无 bar_date 的缺数结果只返回，不造一条假日记录。"""
    code = normalize_code(derivative.get("code"))
    bar_date = _coerce_date10(derivative.get("bar_date"))
    if not code or not bar_date:
        return {"ok": False, "reason": "缺 code/bar_date", "written": False}
    conn = connect(db_path)
    try:
        _technical_schema(conn)
        now = now_iso()
        values = {k: derivative.get(k) for k in TECHNICAL_COLS}
        values["code"] = code
        values["bar_date"] = bar_date
        values["source"] = values.get("source") or "unknown"
        values["status"] = values.get("status") or "ok"
        values["data_quality"] = json.dumps(values.get("data_quality") or [], ensure_ascii=False)
        values["updated_ts"] = now
        values["created_ts"] = now
        cols = ["code", *TECHNICAL_COLS, "created_ts", "updated_ts"]
        assignments = [f"{c}=excluded.{c}" for c in [*TECHNICAL_COLS, "updated_ts"]]
        conn.execute("INSERT INTO stock_technical_daily(" + ",".join(cols) + ") VALUES(" +
                     ",".join("?" for _ in cols) + ") ON CONFLICT(code,bar_date) DO UPDATE SET " +
                     ",".join(assignments), tuple(values.get(c) for c in cols))
        return {"ok": True, "written": True, "code": code, "bar_date": bar_date}
    finally:
        conn.close()


def get_technical_derivative(code: str, *, bar_date: str | None = None,
                             db_path: str | Path | None = None) -> Optional[dict[str, Any]]:
    norm = normalize_code(code)
    if not norm:
        return None
    conn = connect(db_path)
    try:
        if bar_date:
            row = conn.execute("SELECT * FROM stock_technical_daily WHERE code=? AND bar_date=?",
                               (norm, bar_date)).fetchone()
        else:
            row = conn.execute("SELECT * FROM stock_technical_daily WHERE code=? ORDER BY bar_date DESC LIMIT 1",
                               (norm,)).fetchone()
        out = dict(row) if row else None
        if out:
            try:
                out["data_quality"] = json.loads(out.get("data_quality") or "[]")
            except Exception:
                out["data_quality"] = []
        return out
    finally:
        conn.close()


def upsert_technical_derivatives(derivatives: Iterable[dict[str, Any]], *,
                                  db_path: str | Path | None = None) -> dict[str, Any]:
    """批量保存技术派生快照，单连接事务写入。"""
    conn = connect(db_path)
    written = skipped = 0
    try:
        _technical_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for item in derivatives:
                code = normalize_code(item.get("code")) if isinstance(item, dict) else None
                date10 = _coerce_date10(item.get("bar_date")) if isinstance(item, dict) else None
                if not code or not date10:
                    skipped += 1
                    continue
                now = now_iso()
                vals = {k: item.get(k) for k in TECHNICAL_COLS}
                vals.update({"code": code, "bar_date": date10, "source": vals.get("source") or "unknown",
                             "status": vals.get("status") or "ok",
                             "data_quality": json.dumps(vals.get("data_quality") or [], ensure_ascii=False),
                             "created_ts": now, "updated_ts": now})
                cols = ["code", *TECHNICAL_COLS, "created_ts", "updated_ts"]
                assignments = [f"{c}=excluded.{c}" for c in [*TECHNICAL_COLS, "updated_ts"]]
                conn.execute("INSERT INTO stock_technical_daily(" + ",".join(cols) + ") VALUES(" +
                             ",".join("?" for _ in cols) + ") ON CONFLICT(code,bar_date) DO UPDATE SET " +
                             ",".join(assignments), tuple(vals.get(c) for c in cols))
                written += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return {"ok": True, "written": written, "skipped": skipped}


def enqueue_technical_derivatives(codes: Iterable[str], *, trade_date: str,
                                   trigger: str = "daily_hit",
                                   db_path: str | Path | None = None) -> dict[str, Any]:
    """把当天命中的代码进入技术计算队列；命中来源可重复、队列幂等。"""
    date10 = _coerce_date10(trade_date)
    if not date10:
        return {"ok": False, "reason": "非法 trade_date", "enqueued": 0}
    normalized = sorted({c for c in (normalize_code(x) for x in codes) if c})
    if not normalized:
        return {"ok": True, "enqueued": 0}
    conn = connect(db_path)
    try:
        _technical_schema(conn)
        now = now_iso()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for code in normalized:
                conn.execute("""INSERT INTO stock_technical_queue
                    (code,trade_date,first_enqueued_ts,last_enqueued_ts,status,trigger)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(code,trade_date) DO UPDATE SET
                      last_enqueued_ts=excluded.last_enqueued_ts,
                      trigger=excluded.trigger,
                      status=CASE WHEN stock_technical_queue.status='done' THEN 'pending'
                                  ELSE stock_technical_queue.status END""",
                             (code, date10, now, now, "pending", trigger))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"ok": True, "enqueued": len(normalized), "trade_date": date10}
    finally:
        conn.close()


def enqueue_daily_codes(*, trade_date: str, trigger: str = "daily_batch",
                        db_path: str | Path | None = None) -> dict[str, Any]:
    """把当天已有逐日观测的全部代码入队，覆盖历史库首次启用技术层的冷启动。"""
    date10 = _coerce_date10(trade_date)
    if not date10:
        return {"ok": False, "reason": "非法 trade_date", "enqueued": 0}
    conn = connect(db_path)
    try:
        codes = [r["code"] for r in conn.execute(
            "SELECT code FROM stock_daily WHERE trade_date=? ORDER BY code", (date10,)).fetchall()]
    finally:
        conn.close()
    return enqueue_technical_derivatives(codes, trade_date=date10, trigger=trigger,
                                         db_path=db_path)


def technical_queue_codes(*, trade_date: str, status: str = "pending",
                          db_path: str | Path | None = None) -> list[str]:
    date10 = _coerce_date10(trade_date)
    if not date10:
        return []
    conn = connect(db_path)
    try:
        return [r["code"] for r in conn.execute(
            "SELECT code FROM stock_technical_queue WHERE trade_date=? AND status=? ORDER BY code",
            (date10, status)).fetchall()]
    finally:
        conn.close()


def mark_technical_queue(*, code: str, trade_date: str, status: str,
                         error: str | None = None,
                         db_path: str | Path | None = None) -> None:
    norm, date10 = normalize_code(code), _coerce_date10(trade_date)
    if not norm or not date10:
        return
    conn = connect(db_path)
    try:
        conn.execute("UPDATE stock_technical_queue SET status=?, attempts=attempts+1, last_error=? WHERE code=? AND trade_date=?",
                     (status, error, norm, date10))
    finally:
        conn.close()


def process_technical_queue(*, trade_date: str, limit: int | None = None,
                            db_path: str | Path | None = None,
                            fetcher: Any | None = None,
                            max_workers: int = 8) -> dict[str, Any]:
    """盘后处理当天所有命中代码的技术派生队列。

    默认使用统一腾讯前复权日K；测试/离线回放可注入 fetcher(code, n)。
    这是 best-effort 派生层：缺K线只标记失败，不影响事实底库和盘后主链。
    """
    date10 = _coerce_date10(trade_date)
    if not date10:
        return {"ok": False, "reason": "非法 trade_date", "queued": 0}
    codes = technical_queue_codes(trade_date=date10, status="pending", db_path=db_path)
    if limit is not None:
        codes = codes[:max(0, int(limit))]
    if not codes:
        return {"ok": True, "trade_date": date10, "queued": 0, "written": 0,
                "failed": 0}
    if fetcher is None:
        from common.tencent_kline import fetch_kline_ohlc
        fetcher = lambda code, n=130: fetch_kline_ohlc(code, n=n)

    def one(code: str) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            bars = fetcher(code, 130)
            derivative = calculate_technical_derivative(code, bars,
                                                        source="tencent:qfq",
                                                        trigger="eod_all_hits")
            if derivative.get("bar_date"):
                return code, derivative, None
            return code, None, "missing_kline"
        except Exception as exc:  # noqa: BLE001
            return code, None, str(exc)[:300]

    results: list[tuple[str, dict[str, Any] | None, str | None]] = []
    workers = max(1, min(int(max_workers or 1), len(codes)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, code) for code in codes]
        for future in as_completed(futures):
            results.append(future.result())
    derivatives = [item for _, item, error in results if item is not None and error is None]
    saved = upsert_technical_derivatives(derivatives, db_path=db_path)
    failed = 0
    for code, derivative, error in results:
        if derivative is not None:
            mark_technical_queue(code=code, trade_date=date10, status="done", db_path=db_path)
        else:
            failed += 1
            mark_technical_queue(code=code, trade_date=date10, status="error",
                                 error=error, db_path=db_path)
    return {"ok": True, "trade_date": date10, "queued": len(codes),
            "written": saved.get("written", 0), "failed": failed}


def is_limit_up_today(row: dict[str, Any]) -> tuple[Optional[int], str]:
    """「今日是否涨停」的统一取用口径 → `(值, basis)`。

    `basis` 说明结论来源，避免把盘中态当收盘态用：
      `close` 收盘口径（最权威）/ `now` 盘中当前态 / `touched` 今日曾触板 /
      `unknown` 无证据（**不要显示成"否"**）
    """
    if row.get("limit_up_close") is not None:
        return row["limit_up_close"], "close"
    if row.get("limit_up_now") is not None:
        return row["limit_up_now"], "now"
    if row.get("limit_up_touched") is not None:
        return row["limit_up_touched"], "touched"
    return None, "unknown"


def latest_trading_day() -> Optional[str]:
    """最近一个已发生的A股交易日（≤今天）。"""
    try:
        from common.trading_calendar import last_a_share_trading_day

        day = last_a_share_trading_day()
        return day.isoformat() if day else None
    except Exception:
        return None


def _decorate(row: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    """补上标签与派生结论。"""
    out = dict(row)
    tags = conn.execute(
        "SELECT tag_type, tag_value FROM stock_tags WHERE code=? ORDER BY tag_type, tag_value",
        (row["code"],)).fetchall()
    grouped: dict[str, list[str]] = {}
    for t in tags:
        grouped.setdefault(t["tag_type"], []).append(t["tag_value"])
    out["tags"] = grouped
    out["themes"] = grouped.get("theme", [])
    try:
        out["ext"] = json.loads(row.get("ext_json") or "{}")
    except Exception:
        out["ext"] = {}
    try:
        out["data_quality_flags"] = json.loads(row.get("data_quality") or "[]")
    except Exception:
        out["data_quality_flags"] = []
    value, basis = is_limit_up_today(row)
    out["is_limit_up_today"] = value
    out["is_limit_up_basis"] = basis
    # ⚠️ 逐日字段可能属于**更早的交易日**（该票之后没再进入系统）。
    #    不比对最新交易日就把 limit_up_close=1 读成"今天涨停"，是明确事故源。
    out["daily_is_latest_trading_day"] = bool(
        row.get("daily_asof_date") and row["daily_asof_date"] == latest_trading_day())
    tech = conn.execute(
        "SELECT * FROM stock_technical_daily WHERE code=? ORDER BY bar_date DESC LIMIT 1",
        (row["code"],)).fetchone()
    if tech:
        out["technical"] = dict(tech)
        try:
            out["technical"]["data_quality"] = json.loads(
                out["technical"].get("data_quality") or "[]")
        except Exception:
            out["technical"]["data_quality"] = []
    else:
        out["technical"] = None
    return out


def get_by_code(code: str, *, db_path: str | Path | None = None) -> Optional[dict[str, Any]]:
    """按代码（或任意含 6 位代码的写法）取单票对象。"""
    norm = normalize_code(code)
    if not norm:
        return None
    conn = connect(db_path)
    try:
        row = _row_to_dict(conn.execute("SELECT * FROM stocks WHERE code=?", (norm,)).fetchone())
        return _decorate(row, conn) if row else None
    finally:
        conn.close()


def find_by_name(name: str, *, fuzzy: bool = False, limit: int = 20,
                 db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """按名称检索（兜底手段）。歧义时返回多只——**不做猜测**。"""
    token = parse_text(name)
    if not token:
        return []
    conn = connect(db_path)
    try:
        if fuzzy:
            rows = conn.execute(
                "SELECT DISTINCT code FROM stock_name_alias WHERE name LIKE ?"
                " UNION SELECT code FROM stocks WHERE name LIKE ? LIMIT ?",
                (f"%{token}%", f"%{token}%", limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT code FROM stock_name_alias WHERE name=?"
                " UNION SELECT code FROM stocks WHERE name=? LIMIT ?",
                (token, token, limit)).fetchall()
        out = []
        for r in rows:
            row = _row_to_dict(conn.execute("SELECT * FROM stocks WHERE code=?", (r["code"],)).fetchone())
            if row:
                out.append(_decorate(row, conn))
        return out
    finally:
        conn.close()


def query_by_tag(tag_type: str, tag_value: str, *, date_from: str | None = None,
                 date_to: str | None = None, limit: int = 500,
                 db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """按标签（题材/行业/板块）查个股。"""
    ttype = parse_text(tag_type)
    tvalue = parse_text(tag_value)
    if not ttype or not tvalue:
        return []
    conn = connect(db_path)
    try:
        if date_from or date_to:
            codes = conn.execute(
                "SELECT DISTINCT code FROM stock_tags WHERE tag_type=? AND tag_value=?"
                " AND (first_seen IS NULL OR first_seen<=COALESCE(?, first_seen))"
                " AND (last_seen IS NULL OR last_seen>=COALESCE(?, last_seen)) LIMIT ?",
                (ttype, tvalue, date_to, date_from, limit)).fetchall()
        else:
            codes = conn.execute(
                "SELECT DISTINCT code FROM stock_tags WHERE tag_type=? AND tag_value=? LIMIT ?",
                (ttype, tvalue, limit)).fetchall()
        out = []
        for r in codes:
            row = _row_to_dict(conn.execute("SELECT * FROM stocks WHERE code=?", (r["code"],)).fetchone())
            if row:
                out.append(_decorate(row, conn))
        return out
    finally:
        conn.close()


def query_date_range(date_from: str, date_to: str, *, limit: int = 2000,
                     only_limit_up: bool = False,
                     db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """按交易日期段查逐日观测。"""
    start, end = _coerce_date10(date_from), _coerce_date10(date_to)
    if not start or not end:
        return []
    conn = connect(db_path)
    try:
        sql = ("SELECT * FROM stock_daily WHERE trade_date>=? AND trade_date<=?"
               + (" AND limit_up_close=1" if only_limit_up else "")
               + " ORDER BY trade_date, code LIMIT ?")
        rows = conn.execute(sql, (start, end, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def iter_stocks(*, since: str | None = None, limit: int | None = None,
                db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """遍历个股（可按 `since` 过滤最后命中日期）。"""
    conn = connect(db_path)
    try:
        sql = "SELECT * FROM stocks"
        params: list[Any] = []
        if since:
            sql += " WHERE COALESCE(last_seen_ts,'')>=?"
            params.append(since)
        sql += " ORDER BY code"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def stats(*, db_path: str | Path | None = None) -> dict[str, Any]:
    """底库概况（体检用）。"""
    conn = connect(db_path)
    try:
        def one(sql: str, params: tuple = ()) -> Any:
            r = conn.execute(sql, params).fetchone()
            return r[0] if r else None

        return {
            "db_path": str(resolve_db_path(db_path)),
            "stocks": one("SELECT COUNT(*) FROM stocks") or 0,
            "tags": one("SELECT COUNT(*) FROM stock_tags") or 0,
            "daily_rows": one("SELECT COUNT(*) FROM stock_daily") or 0,
            "observations": one("SELECT COUNT(*) FROM stock_observations") or 0,
            "aliases": one("SELECT COUNT(*) FROM stock_name_alias") or 0,
            "technical_rows": one("SELECT COUNT(*) FROM stock_technical_daily") or 0,
            "technical_pending": one("SELECT COUNT(*) FROM stock_technical_queue WHERE status='pending'") or 0,
            "pending_review": one(
                "SELECT COUNT(*) FROM stock_pending_review WHERE resolved=0") or 0,
            "first_seen_min": one("SELECT MIN(first_seen_date) FROM stocks"),
            "last_seen_max": one("SELECT MAX(last_seen_ts) FROM stocks"),
            "limit_up_close_1": one("SELECT COUNT(*) FROM stocks WHERE limit_up_close=1") or 0,
            "schema_version": one("SELECT value FROM schema_meta WHERE key='version'"),
        }
    finally:
        conn.close()


def dump_json(out_path: str | Path | None = None, *, since: str | None = None,
              db_path: str | Path | None = None) -> dict[str, Any]:
    """导出只读投影 JSON（前端/脚本沿用文件式读取，无需懂 SQLite）。"""
    target = Path(out_path) if out_path else DEFAULT_PROJECTION
    conn = connect(db_path)
    try:
        sql = "SELECT * FROM stocks"
        params: list[Any] = []
        if since:
            sql += " WHERE COALESCE(last_seen_ts,'')>=?"
            params.append(since)
        sql += " ORDER BY code"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        latest = latest_trading_day()
        # 标签**一次取全量**再内存分组：逐票查是 N+1（1249 只 = 1249 次查询）。
        tags_by_code: dict[str, dict[str, list[str]]] = {}
        for t in conn.execute(
                "SELECT code, tag_type, tag_value FROM stock_tags"
                " ORDER BY code, tag_type, tag_value").fetchall():
            tags_by_code.setdefault(t["code"], {}).setdefault(t["tag_type"], []).append(t["tag_value"])
        # 技术派生一次取每票最新快照，保持投影导出不退化为 N+1 查询。
        technical_by_code: dict[str, dict[str, Any]] = {}
        for t in conn.execute(
                "SELECT t.* FROM stock_technical_daily t "
                "JOIN (SELECT code, MAX(bar_date) AS max_date FROM stock_technical_daily GROUP BY code) latest "
                "ON latest.code=t.code AND latest.max_date=t.bar_date").fetchall():
            technical_by_code[t["code"]] = dict(t)
        items = []
        name_index: dict[str, list[str]] = {}
        for row in rows:
            grouped = tags_by_code.get(row["code"], {})
            value, basis = is_limit_up_today(row)
            item = {
                "code": row["code"],
                "name": row["name"],
                "market": row["market"],
                "is_20cm": bool(row["is_20cm"]) if row["is_20cm"] is not None else None,
                "industry": row["industry"],
                "themes": grouped.get("theme", []),
                "tags": grouped,
                "is_limit_up_today": value,
                "is_limit_up_basis": basis,
                "limit_up_now": row["limit_up_now"],
                "limit_up_touched": row["limit_up_touched"],
                "limit_up_close": row["limit_up_close"],
                "boards": row["boards"],
                "pct": row["pct"],
                "price": row["price"],
                "daily_asof_date": row["daily_asof_date"],
                "daily_is_latest_trading_day": bool(
                    row["daily_asof_date"] and row["daily_asof_date"] == latest),
                "first_seen_date": row["first_seen_date"],
                "last_seen_ts": row["last_seen_ts"],
                "update_count": row["update_count"],
                "source": row["source"],
            }
            item["technical"] = technical_by_code.get(row["code"])
            if item["technical"]:
                try:
                    item["technical"]["data_quality"] = json.loads(
                        item["technical"].get("data_quality") or "[]")
                except Exception:
                    item["technical"]["data_quality"] = []
            items.append(item)
            if row["name"]:
                name_index.setdefault(row["name"], []).append(row["code"])
        payload = {
            "generated_ts": now_iso(),
            "count": len(items),
            "schema_version": SCHEMA_VERSION,
            "stocks": items,
            "name_index": name_index,
        }
    finally:
        conn.close()

    try:
        from common.json_io import save_json

        if _projection_write_blocked(target, out_path):
            _log(f"测试期跳过写生产投影 {target} —— 需要写入请显式传 out_path=")
        else:
            save_json(target, payload)
    except Exception as e:  # noqa: BLE001
        _log(f"投影导出失败 {target}: {e}")
    return payload


def refresh_projection(*, out_path: str | Path | None = None,
                       db_path: str | Path | None = None) -> dict[str, Any] | None:
    """刷新只读投影（镜像文件）——`dump_json` 的非致命包装。

    ⚠️ 这是"给人和前端读"的镜像，**不是真源**（真源永远是 SQLite）。因此失败只记日志、
    返回 None，绝不中断调用方主链（与两个 ingest 入口同策略）。

    ⚠️ 刷新时机（用户 2026-10-15 定）：**只在盘后写入成功后刷一次**；盘中每 30 秒不刷。
    ⚠️ 测试期未显式传 `out_path` 会被 `_projection_write_blocked` 拦截 → 不污染生产投影。
    """
    try:
        return dump_json(out_path, db_path=db_path)
    except Exception as e:  # noqa: BLE001 — 非致命
        _log(f"⚠️ 刷新投影失败（不影响主流程）: {e}")
        return None


def _projection_write_blocked(target: Path, out_path: str | Path | None) -> bool:
    """测试期禁止写生产投影（与 DB 守卫同源，防止夹具数据覆盖真实投影）。"""
    if out_path is not None or not os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        return Path(target).resolve() == DEFAULT_PROJECTION.resolve()
    except Exception:
        return False


def prune_observations(keep_days: int = 40, *, db_path: str | Path | None = None) -> int:
    """清理过老的原始观测（默认保留 40 个自然日，对齐 capture_daily.RETAIN_DAYS）。"""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "DELETE FROM stock_observations"
                " WHERE trade_date IS NOT NULL"
                " AND trade_date < date('now', ?)", (f"-{int(keep_days)} day",))
            conn.execute("COMMIT")
            return cur.rowcount or 0
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
