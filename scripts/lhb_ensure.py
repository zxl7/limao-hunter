#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""龙虎榜落盘守护（2026-11-09 新增）

## 为什么不是「再建一个 18:20 的定时任务」

2026-09-29 20:59，`【盘后】龙虎榜速览卡`（automation-1145135d…）在 WorkBuddy 里被删除，
但 `config/tasks.yaml` 仍把它登记为 ACTIVE —— 台账与 DB 漂移，**没有任何告警**；
叠加同期的 `~/.npm` 权限损坏（npx 全程 EPERM），数据从 09-29 断到 11-09，共 41 天。

结论：**不要再依赖「单一 automation 会不会被删」**。把补齐挂到常驻链路
（`pulse_fetcher.sh` 每 10 分钟一拍，见其中的 `lhb_self_heal`），删任务也不会再断。

## 行为（幂等）

1. 取最近 N 个 A 股交易日（⛔ 用 `common.trading_calendar`，不自己数星期几；
   长假/调休都会判错，09-25 已有同源事故）；
2. 缺哪天补哪天：已有**非空**文件 → 跳过；空文件/缺失 → 调 `lhb_fetch.run(day)`；
3. 写 `cache/lhb_status.json`（体检与终端可读）；
4. 退出码：0 = 最近一个交易日已就绪；1 = 仍缺（调用方据此告警，⛔ 不静默）。

用法：
    python3 scripts/lhb_ensure.py                # 守护用：近 3 个交易日
    python3 scripts/lhb_ensure.py --days 10      # 补更长缺口
    python3 scripts/lhb_ensure.py --status       # 只看状态，不抓
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.trading_calendar import is_a_share_trading_day  # noqa: E402

CACHE = ROOT / "cache"
STATUS = CACHE / "lhb_status.json"
_MAX_SCAN = 90


def _load_fetch():
    spec = importlib.util.spec_from_file_location("lhb_fetch", ROOT / "scripts" / "lhb_fetch.py")
    mod = importlib.util.module_from_spec(spec)      # type: ignore[arg-type]
    spec.loader.exec_module(mod)                     # type: ignore[union-attr]
    return mod


def recent_trading_days(n: int, today: date | None = None) -> list[str]:
    """最近 n 个交易日（升序，含今天若今天是交易日）。"""
    day = today or datetime.now(timezone(timedelta(hours=8))).date()
    out: list[str] = []
    for _ in range(_MAX_SCAN):
        if len(out) >= n:
            break
        if is_a_share_trading_day(day):
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(out)


def _has_data(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 2:
        return False
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                # noqa: BLE001
        return False
    return bool(d.get("institution") or d.get("hotmoney"))


def ensure(days: list[str], *, fetch=None, cache: Path = CACHE, with_hithink: bool = False) -> dict:
    """补齐 `days` 中缺失的龙虎榜；返回状态字典（⛔ 单日失败不中断其余）。"""
    fetch = fetch or _load_fetch().run
    missing: list[str] = []
    filled: list[str] = []
    errors: list[str] = []
    for d in days:
        path = cache / f"lhb_{d.replace('-', '')}.json"
        if _has_data(path):
            continue
        missing.append(d)
        try:
            out = fetch(d)
        except Exception as e:                       # noqa: BLE001
            errors.append(f"{d}: {e}")
            continue
        if out and (out.get("institution") or out.get("hotmoney")):
            filled.append(d)
        elif out is not None:
            errors.append(f"{d}: 未披露或接口无数据")
    last = days[-1] if days else ""
    # ⛔ 报「仍缺」而不是「开始时缺哪些」：补齐后还缺的才是需要告警的
    remaining = [d for d in days
                 if not _has_data(cache / f"lhb_{d.replace('-', '')}.json")]
    if with_hithink:
        filled_h = _run_hithink(days, cache, errors)
    else:
        filled_h = []
    status = {
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "window": days,
        "missing": missing,
        "filled": filled,
        "remaining": remaining,
        "errors": errors,
        "last_trading_day": last,
        "ready": bool(last) and last not in remaining,
        "source": "tencent-westock",
        "hithink_filled": filled_h,
    }
    return status


def _run_hithink(days: list[str], cache: Path, errors: list[str]) -> list[str]:
    """同花顺交叉校验源（best-effort：⛔ 失败不影响主源就绪判定）。"""
    filled: list[str] = []
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    for d in days:
        # ⛔ 当天不跑：同花顺接口在未披露时会**回落到上一交易日**并写那天的文件，
        #    对「今天」的检查只会制造假告警（2026-11-09 实测：问 11-09 返回 11-06）。
        if d >= today:
            continue
        path = cache / f"hithink_lhb_{d.replace('-', '')}.json"
        if path.exists() and path.stat().st_size > 64:
            continue
        try:
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "hithink_fetch.py"), "lhb", "--date", d],
                capture_output=True, text=True, timeout=120, cwd=str(ROOT))
            if r.returncode == 0 and path.exists():
                filled.append(d)
            else:
                errors.append(f"hithink {d}: rc={r.returncode} {(r.stderr or '').strip()[:120]}")
        except Exception as e:                       # noqa: BLE001
            errors.append(f"hithink {d}: {e}")
    return filled


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="回补窗口（交易日数，默认 3）")
    ap.add_argument("--status", action="store_true", help="只输出状态，不抓取")
    a = ap.parse_args()

    days = recent_trading_days(max(1, a.days))
    if a.status:
        missing = [d for d in days
                   if not _has_data(CACHE / f"lhb_{d.replace('-', '')}.json")]
        st = {"window": days, "missing": missing, "filled": [], "remaining": missing,
              "errors": [], "last_trading_day": days[-1] if days else "",
              "ready": bool(days) and (not missing or missing[-1] != days[-1])}
    else:
        st = ensure(days, with_hithink=True)
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")

    if st.get("remaining"):
        extra = f"（本次补 {', '.join(st['filled'])}）" if st.get("filled") else ""
        print(f"[lhb_ensure] {st['last_trading_day']} 未就绪 · 仍缺 {', '.join(st['remaining'])}{extra}"
              f"；披露约 18:00-20:00，下次自动重试")
    else:
        print(f"[lhb_ensure] {st['last_trading_day']} 已就绪 · 窗口 {days[0]}~{days[-1]}"
              f"{'（含回补 ' + ', '.join(st['filled']) + '）' if st.get('filled') else ''}")
    if st.get("hithink_filled"):
        print(f"  同花顺交叉源补 {', '.join(st['hithink_filled'])}")
    for e in st.get("errors") or []:
        print(f"  ⚠️ {e}")
    return 0 if st["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
