#!/usr/bin/env python3
"""调度哨兵（P1-⑥ · 防"verify 漏跑 3 天没人发现"类事故）
检测 ACTIVE 盘中任务（猎手×11 + 快照×3）最近一次成功运行是否超期：
  - 交易时段内（09:25-11:30 / 13:00-14:50）检查
  - 超 STALE_MIN（默认 45 分钟）未成功运行 → 自动补跑 pulse_fetcher --force + 推「⚠️调度异常」蓝卡
  - 同内容 30 分钟内去重防刷屏
挂载：独立 WEEKLY 任务（BYHOUR 9,10,11,13,14 × BYMINUTE 25,55）或手动运行。
"""
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 供 in_trading 内 common.trading_calendar 导入（必须在首次调用前注入）
DB = Path.home() / ".workbuddy" / "workbuddy.db"
BJ_TZ = timezone(timedelta(hours=8))
STALE_MIN = 45
DEDUP_FILE = ROOT / "cache" / "sentinel_last_alert.json"
DEDUP_WINDOW = 1800  # 30 分钟


def in_trading(dt: datetime) -> bool:
    t = dt.strftime("%H:%M")
    in_window = ("09:25" <= t <= "11:30") or ("13:00" <= t <= "14:50")
    if not in_window:
        return False
    # 节假日门禁：法定节假日与周末（含调休补班日）不监管也不告警
    try:
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(dt)
    except Exception:
        return dt.weekday() < 5  # trading-day-fallback: 唯一真源不可用时的周末兜底


def parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    s = str(raw).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).astimezone(BJ_TZ)
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(float(raw), BJ_TZ)
    except (ValueError, OSError):
        return None


def main() -> int:
    now = datetime.now(BJ_TZ)
    if not in_trading(now):
        print("非交易时段，跳过")
        return 0

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    # 2026-10-14 快照并入猎手（任务改名 盘中猎手-*）→ 正常只命中猎手；保留 OR 仅为过渡期兜底
    tasks = db.execute(
        """SELECT id, name FROM automations
           WHERE deleted_at IS NULL AND status='ACTIVE'
             AND (name LIKE '盘中猎手%' OR name LIKE '盘中快照%')"""
    ).fetchall()

    stale = []
    for t in tasks:
        row = db.execute(
            """SELECT created_at FROM automation_runs
               WHERE automation_id=? AND result_success=1
               ORDER BY updated_at DESC LIMIT 1""",
            (t["id"],),
        ).fetchone()
        if row is None:
            stale.append((t["name"], "从未成功运行"))
            continue
        last = parse_ts(row["created_at"])
        if last is None:
            continue
        age_min = (now - last).total_seconds() / 60
        if age_min > STALE_MIN:
            stale.append((t["name"], f"{int(age_min)}分钟前"))

    if not stale:
        print("✅ 全部 ACTIVE 盘中任务健康")
        return 0

    detail = "；".join(f"{n}（{s}）" for n, s in stale)

    # 去重：同内容 30 分钟内不重复告警
    dedup = {}
    if DEDUP_FILE.exists():
        try:
            dedup = json.loads(DEDUP_FILE.read_text(encoding="utf-8"))
        except Exception:
            dedup = {}
    if dedup.get("detail") == detail and dedup.get("ts"):
        try:
            last_alert = datetime.fromisoformat(dedup["ts"])
            if (now - last_alert).total_seconds() < DEDUP_WINDOW:
                print(f"30min 内已告警，跳过：{detail}")
                return 0
        except ValueError:
            pass

    # 补跑数据刷新（尽力而为，失败不阻断告警）
    try:
        subprocess.run(
            ["bash", str(ROOT / "scripts" / "pulse_fetcher.sh"), "--force"],
            capture_output=True, timeout=180,
        )
    except Exception as e:
        print(f"补跑失败: {e}")

    # 告警
    sys.path.insert(0, str(ROOT))
    try:
        from common.logging_setup import notify_alert
        notify_alert("⚠️ 调度异常·盘中任务超期", f"{detail}\n已自动补跑 pulse_fetcher，请关注后续卡片是否恢复")
    except Exception as e:
        print(f"告警发送失败: {e}")

    DEDUP_FILE.write_text(
        json.dumps({"ts": now.isoformat(timespec="seconds"), "detail": detail}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"⚠️ 超期任务: {detail} | 已补跑 + 告警")
    return 0


if __name__ == "__main__":
    sys.exit(main())
