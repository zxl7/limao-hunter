#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""涨停池归档**单一写入源**（2026-09-18 立·根治「双路径分裂」）。

════════════════════════════════════════════════════════════════════════
事故链（09-17 发现 / 09-18 取证）
════════════════════════════════════════════════════════════════════════
涨停池归档长期有**两个目录、两个写入者、互不知道对方**：

  · `record_zt_snapshot.py`  只写 `wealth_code/data/zt_history/`（17:19 盘后链）
  · `capture_daily.py`       只写 `cache/zt_history/`（17:0x 盘后链）
  · 消费方读哪个？**不统一**：
      - `theme_roll.py` / `live_signal.py` / `decision_audit.py` / `pipeline_eod.py`
        → 读 `cache/zt_history/`
      - `archive_decision.py` → 两个都查（唯一做对的）
      - `share` 脚本 → 读 `wealth_code/data/zt_history/`

后果（09-18 实测）：15:30 财富码 eod 跑 theme_roll 时，`cache/zt_history/zt_2026-09-18.json`
还是**盘中时间轴创建的空壳**（有 `intraday_points`、无 `zt_list`）→ theme_roll 当日行
`zt_count=0` → 题材广度当日全丢（数据失真，非报错，**静默**）。

════════════════════════════════════════════════════════════════════════
本模块的职责（唯一出口）
════════════════════════════════════════════════════════════════════════
1. `write_archives(snap)` —— 双目录**同写**，且 `cache/` 侧**合并保留** `intraday_points`
   （那是 HTML 题材时间轴的产物，收盘后可丢，但盘中/复盘要读，故只能补不能覆盖）。
2. `ensure_today_archive()` —— 「缺料自愈」：收盘后（≥15:00）当日 `cache` 归档若**无
   `zt_list`**，主动抓一次并双写。**⛔ 绝不写 `cache/yesterday_zt.json`**——那个文件
   的语义是「供盘中判昨日反馈」，被提前覆盖会让盘中链自指（拿今天的池当昨天的池）。

⛔ 纪律：任何新脚本要落盘涨停池归档，**一律 import 本模块**，不得再自己拼路径写文件；
   否则双路径分裂会以第三种形态复发。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / "cache"

# 归档双目录（唯一权威清单·新增目录必须在此登记）
ZT_DIRS: tuple[Path, ...] = (
    CACHE / "zt_history",                        # 消费主目录（theme_roll/live_signal/…）
    ROOT / "wealth_code" / "data" / "zt_history",  # 财富码侧（share/归档）
)
# 盘中时间轴产物的保留键（只补不覆盖）
_MERGE_KEYS = ("intraday_points",)


def archive_paths(date10: str) -> dict[Path, Path]:
    """`{目录: 该日归档路径}`（不保证存在）。"""
    return {d: d / f"zt_{date10}.json" for d in ZT_DIRS}


def write_archives(snap: dict, merge_existing: bool = True,
                   dirs: tuple[Path, ...] | None = None) -> list[Path]:
    """把一份快照写进**全部**归档目录 → 返回实际写入路径列表。

    `dirs=None` → 写模块默认双目录；调用方若自持目录配置（如 `capture_daily.ZT_DIR`
    可被测试注入），**必须显式传入**，否则会写穿到生产路径。

    `merge_existing=True`（默认）：目标文件已存在且含 `intraday_points` 等保留键时，
    写盘前把它们并回 `snap`（**不覆盖**）——防「盘后补全 zt_list」把盘中时间轴数据抹掉。
    """
    date10 = str(snap.get("date") or "").strip()
    if not date10:
        raise ValueError("snap 缺 date 字段，拒绝归档")
    targets = tuple(dirs) if dirs else ZT_DIRS
    _guard_test_write(targets)
    written: list[Path] = []
    for d in targets:
        try:
            p = d / f"zt_{date10}.json"
            payload = snap
            if merge_existing and p.exists():
                try:
                    old = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    old = None
                if isinstance(old, dict):
                    keep = {k: old[k] for k in _MERGE_KEYS
                            if old.get(k) and not snap.get(k)}
                    if keep:
                        payload = {**snap, **keep}
            d.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            written.append(p)
        except Exception as e:  # noqa: BLE001 —— 单目录失败不拖垮另一目录
            print(f"[zt_archive] ⚠️ 归档失败 {d}: {e}")
    return written


def _cache_archive_has_pool(date10: str, dirs: tuple[Path, ...] | None = None) -> bool:
    p = (dirs or ZT_DIRS)[0] / f"zt_{date10}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(isinstance(d, dict) and (d.get("zt_list") or []))


def _guard_test_write(targets: tuple[Path, ...]) -> None:
    """⛔ 测试期**禁止写生产归档**（2026-09-18 血案防线）。

    事故：`capture_daily._heal_residual_archives` 的测试把 `cd.ZT_DIR` 注入 tmp，
    但自愈改走本模块后仍用**模块默认目录** → 测试夹具的 1 只假票被写进真实
    `cache/zt_history/zt_2026-09-11.json`（真数据被覆盖）。此守卫把「静默写穿」
    变成「测试当场报错」，强制调用方显式注入 `dirs=`。
    """
    import os
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    prod = {CACHE / "zt_history", ROOT / "wealth_code" / "data" / "zt_history"}
    for d in targets:
        try:
            if Path(d).resolve() in {p.resolve() for p in prod}:
                raise RuntimeError(
                    f"测试期禁止写生产归档目录 {d} —— 请显式传 dirs=(tmp,…) 注入临时目录")
        except RuntimeError:
            raise
        except Exception:
            continue


def _load_codes(date10: str, dirs: tuple[Path, ...] | None = None) -> set[str]:
    try:
        d = json.loads(((dirs or ZT_DIRS)[0] / f"zt_{date10}.json").read_text(encoding="utf-8"))
    except Exception:
        return set()
    return {str(it.get("code")) for it in (d.get("zt_list") or []) if it.get("code")}


def _prev_archive_date(date10: str, dirs: tuple[Path, ...] | None = None) -> str | None:
    dates = sorted(p.stem[3:] for p in (dirs or ZT_DIRS)[0].glob("zt_*.json"))
    earlier = [d for d in dates if d < date10]
    return earlier[-1] if earlier else None


def ensure_today_archive(today: str | None = None, now_hour: int | None = None,
                         allow_backfill: bool = False,
                         dirs: tuple[Path, ...] | None = None) -> str:
    """收盘后自愈：当日 `cache` 归档缺 `zt_list` → 抓一次并双写。

    返回动作描述（供调用方打印）：
      `ok` / `before-close` / `not-trading-day` / `skip-non-today` /
      `skip-selfsame(...)` / `filled(N)` / `fetch-failed(...)`

    ⛔ 三道守卫（照搬本项目「涨停池空 / 自交」双保险口径）：
      ① 15:00 前不动（盘中池不完整，抓了就是污染）；
      ② 非交易日不动（周一~周五判定 · common.market_time 同源）；
      ③ **自交守卫**——抓到的新池与前一个归档**代码集完全相同**（法定节假日东财/biying
         会返回上一交易日池）→ 拒绝写入，防「节假日假数据」污染当日归档；
      ④ 默认只补**今天**（`today=` 仅测试/回填用，须显式 allow_backfill=True）。

    ⛔ 只写 zt_history 归档，**不碰 `yesterday_zt.json`**（见模块头「纪律」）。
    """
    date10 = today or datetime.now().strftime("%Y-%m-%d")
    real_today = datetime.now().strftime("%Y-%m-%d")
    targets = tuple(dirs) if dirs else ZT_DIRS
    if _cache_archive_has_pool(date10, targets):
        return "ok"
    if date10 != real_today and not allow_backfill:
        return "skip-non-today"
    if now_hour is None:
        try:
            if str(ROOT / "common") not in sys.path:
                sys.path.insert(0, str(ROOT / "common"))
            import market_time as _mt
            if not _mt.is_trading_day():
                return "not-trading-day"
        except Exception:
            if datetime.now().weekday() >= 5:
                return "not-trading-day"
    hour = datetime.now().hour if now_hour is None else now_hour
    if hour < 15:
        return "before-close"
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import record_zt_snapshot as rz  # 懒导入：复用其抓取+组装（零逻辑重复）
        snap = rz.build_snapshot(date10)
        pool = snap.get("zt_list") or []
        if not pool:
            return "fetch-failed(空池)"
        new_codes = {str(it.get("code")) for it in pool if it.get("code")}
        prev = _prev_archive_date(date10, targets)
        if prev and new_codes and new_codes == _load_codes(prev, targets):
            return f"skip-selfsame(与 {prev} 完全同池·疑似非交易日返回上一交易日池)"
        write_archives(snap, dirs=dirs)
        return f"filled({len(pool)})"
    except Exception as e:  # noqa: BLE001 —— 自愈失败不得影响主流程
        return f"fetch-failed({type(e).__name__}: {e})"


if __name__ == "__main__":
    print(f"[zt_archive] 归档目录：{[str(d) for d in ZT_DIRS]}")
    print(f"[zt_archive] 今日自愈：{ensure_today_archive()}")
