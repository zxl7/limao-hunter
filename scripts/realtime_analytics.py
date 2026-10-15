#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中分析快照：只读取当轮行情，生成实时展示层，不改盘后历史与交易账本。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
PULSE = CACHE / "pulse_snapshots"
LOCK = PULSE / ".realtime_analytics.lock"
STATE = PULSE / ".realtime_analytics_ts"

# 规范时间戳（2026-10-14 审计 P1-⑦）：落盘 generated_ts 统一带 +08:00。
# 防御性导入（与本文件 trading_calendar 同风格）：脚本可能从任意 cwd 被调起。
try:
    from common.market_time import iso_bj
except ImportError:  # pragma: no cover
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from common.market_time import iso_bj
    except Exception:
        def iso_bj(dt: datetime | None = None) -> str:  # type: ignore[misc]
            return (dt or datetime.now()).astimezone().isoformat(timespec="seconds")

# ── 时段守卫（2026-09-29 新增·防止盘中链**反向覆盖**盘后链产物）──────────────
# ⛔ 事故：本脚本每轮无条件覆盖 `theme_rotation.json`，而收盘后 `latest.json` 的 ts
#    仍是当日 15:00:08 ⇒ 原守卫（只比「快照日期 == 今日」）放行，于是 09-29 在
#    17:41 / 17:51 又写了两次实时版本（`realtime=true`），把文件盖成**盘中态**。
#    后果有二：① 盘后链真跑过也会被抹掉 ② `guard_eod_chain` 因此误判为盘后口径。
# 判据：只在**本交易日盘中时段**（09:25 竞价纪元 ~ 16:00）写盘；
#    16:00 与 `guard_eod_chain.EOD_COVER_HHMM` 对齐 —— 该时刻起产出一律归盘后链。
# 可用 `REALTIME_ANALYTICS_SESSION_ONLY=0` 回退（排障时不建议，会让上述事故复现）。
SESSION_START_HHMM = 925
EOD_CUTOFF_HHMM = 1600


def _session_open(now: datetime | None = None) -> bool:
    if os.environ.get("REALTIME_ANALYTICS_SESSION_ONLY", "1") == "0":
        return True
    now = now or datetime.now()
    # 交易日判定走唯一真源（⛔ 本文件不得自判 weekday——工作日休市会漏网）
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from common.trading_calendar import is_a_share_trading_day
        if not is_a_share_trading_day(now):
            return False
    except Exception:  # pragma: no cover - 极端环境兜底
        if now.weekday() >= 5:  # trading-day-fallback: 唯一真源不可用时的周末兜底
            return False
    return SESSION_START_HHMM <= now.hour * 100 + now.minute < EOD_CUTOFF_HHMM


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _fresh_enough() -> bool:
    try:
        return datetime.now().timestamp() - float(STATE.read_text()) < 55
    except (OSError, ValueError):
        return False


def _live_themes(snapshot: dict) -> list[dict]:
    """主快照 top5 是本轮已确认题材；只传递字段，不自行猜测板块归属。"""
    themes = []
    for item in snapshot.get("top5") or []:
        name = item.get("name")
        if not name:
            continue
        stocks = []
        for stock in item.get("zt_stocks") or []:
            code = str(stock.get("code") or "").split(".")[0]
            if code:
                stocks.append({"code": code, "name": stock.get("name"), "pct": stock.get("pct"), "dragon": stock.get("name") == (item.get("leader") or {}).get("name")})
        themes.append({"name": name, "zt": item.get("zt_count"), "pct": None, "title": item.get("desc") or "", "stocks": stocks})
    return themes


def main() -> None:
    if not _session_open():
        return
    if _fresh_enough():
        return
    try:
        LOCK.mkdir()
    except FileExistsError:
        return
    try:
        snapshot = _load(PULSE / "latest.json")
        day = str(snapshot.get("ts") or "")[:10]
        # 盘中链只消费本交易日快照，休市时不得拿旧盘面重算“实时”结论。
        if day != datetime.now().astimezone().strftime("%Y-%m-%d"):
            return
        import theme_rotation
        themes = _live_themes(snapshot)
        codes = {str(item.get("code") or "").split(".")[0] for item in snapshot.get("limit_up_stocks") or []}
        rotation = theme_rotation.build(live_day=day, live_themes=themes, live_codes=codes)
        rotation["realtime"] = True
        rotation["source_ts"] = snapshot.get("ts")
        _write(CACHE / "theme_rotation.json", rotation)
        _write(PULSE / "realtime_analytics.json", {"date": day, "generated_ts": iso_bj(), "source_ts": snapshot.get("ts"), "themes": len(themes)})
        STATE.write_text(str(datetime.now().timestamp()), encoding="utf-8")
    finally:
        try:
            LOCK.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    main()
