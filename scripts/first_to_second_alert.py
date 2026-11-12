#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🔔 一进二打板提醒（2026-11-12 用户「一进二我想打板，二进三我想定心·可以提前提醒我」）

数据：`cache/first_to_second_<上一交易日>.json`（`scripts/first_to_second_watch.py` 生成）
行为：盘中每轮（pulse_fetcher 每 30s）检查候选票的实时涨幅
  · 逼近涨停（主板 ≥7.5% / 20cm ≥15%，未封）→ 推「⚡ 可打板准备」
  · 已封板（≥9.8% / ≥19.8%）→ 推「🔔 一进二封板」
  · 每票每档只推一次（状态落 `cache/pulse_snapshots/fts_state_<date>.json`）

⛔ 只提醒，不下单、不改判据、不进候选池。
开关：`ENABLE_FIRST_TO_SECOND_ALERT=0` 关闭。
用法：
    python3 scripts/first_to_second_alert.py            # 正常（仅交易时段）
    python3 scripts/first_to_second_alert.py --dry-run  # 不推送，打印将推内容
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

STATE_DIR = ROOT / "cache" / "pulse_snapshots"
PUSH = ROOT / "scripts" / "push_card.py"

APPROACH_MAIN, APPROACH_20CM = 7.5, 15.0
SEALED_MAIN, SEALED_20CM = 9.8, 19.8


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _is_trading_time(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (time(9, 25) <= t <= time(11, 32)) or (time(13, 0) <= t <= time(15, 2))


def _load_watch(today: str, exact: str | None = None) -> dict:
    if exact:
        p = ROOT / "cache" / f"first_to_second_{exact}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    files = sorted((ROOT / "cache").glob("first_to_second_*.json"))
    for p in reversed(files):
        d = p.stem.replace("first_to_second_", "")
        if d < today:
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return {}
    return {}


def _load_state(day: str) -> dict:
    p = STATE_DIR / f"fts_state_{day}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(day: str, st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"fts_state_{day}.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _push(title: str, rows: list[str], *, dry: bool) -> None:
    if not rows:
        return
    card = {"title": title, "color": "purple",
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(rows)}}],
            "note": "一进二打板提醒 · 只提示不执行"}
    tmp = Path("/tmp/limao_fts_card.json")
    tmp.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    if dry:
        print(f"[dry] {title}\n" + "\n".join(rows))
        return
    subprocess.run([sys.executable, str(PUSH), str(tmp)], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ignore-hours", action="store_true")
    ap.add_argument("--watch-date", help="指定候选文件日期（默认取上一交易日）")
    a = ap.parse_args(argv)
    if os.environ.get("ENABLE_FIRST_TO_SECOND_ALERT", "1") == "0":
        return 0
    now = datetime.now()
    if not a.ignore_hours and not _is_trading_time(now):
        return 0
    day = _today()
    watch = _load_watch(day, a.watch_date)
    picks = watch.get("picks") or []
    if not picks:
        return 0
    codes = [p["code"] for p in picks]
    try:
        from feedback_matrix import fetch_quotes
        q = fetch_quotes(codes)
    except Exception as e:  # noqa: BLE001
        print(f"[fts] 行情失败：{e}")
        return 0
    st = _load_state(day)
    appr, sealed = [], []
    for p in picks:
        c = p["code"]
        it = q.get(c) or {}
        pct = it.get("pct")
        if pct is None:
            continue
        pct = float(pct)
        is20 = c[:3] in ("300", "301", "688")
        a_thr, s_thr = (APPROACH_20CM, SEALED_20CM) if is20 else (APPROACH_MAIN, SEALED_MAIN)
        s = st.setdefault(c, {})
        s["last_pct"] = pct
        if pct >= s_thr and not s.get("sealed"):
            s["sealed"] = True
            sealed.append(f"🔔 <font color='red'>{p['name']}({c}) 已封板 +{pct:.2f}%"
                          f"〔{p['theme']}·{p['mates']}家·评分{p['score']}〕</font>")
        elif pct >= a_thr and not s.get("approach"):
            s["approach"] = True
            gap = (s_thr - pct)
            appr.append(f"⚡ <font color='purple'>{p['name']}({c}) +{pct:.2f}%"
                        f"（距涨停 {gap:.1f} 点）〔{p['theme']}·评分{p['score']}〕</font>")
    _save_state(day, st)
    _push("⚡ 一进二·逼近涨停（可打板准备）", appr, dry=a.dry_run)
    _push("🔔 一进二·已封板", sealed, dry=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
