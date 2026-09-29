#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中分析快照：只读取当轮行情，生成实时展示层，不改盘后历史与交易账本。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
PULSE = CACHE / "pulse_snapshots"
LOCK = PULSE / ".realtime_analytics.lock"
STATE = PULSE / ".realtime_analytics_ts"


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
        _write(PULSE / "realtime_analytics.json", {"date": day, "generated_ts": datetime.now().isoformat(), "source_ts": snapshot.get("ts"), "themes": len(themes)})
        STATE.write_text(str(datetime.now().timestamp()), encoding="utf-8")
    finally:
        try:
            LOCK.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    main()
