#!/usr/bin/env python3
"""审计日志模块（P1-⑪ · 对应 upgrade_plan P2-10）
append-only 写 cache/audit.jsonl：
  - event=push      推送记录（成功/失败/阻断）
  - event=automation 定时任务变更（创建/更新/删除·由调用方显式记录）
  - event=config    配置修改
  - event=error     脚本异常
  - event=system    系统事件（补跑/哨兵触发）
用法（模块内）:
  from common.audit_log import audit
  audit("push", card="card_xxx.json", webhook="prod", result="ok")
CLI:
  python3 common/audit_log.py --event push --msg "..."            # 追加
  python3 common/audit_log.py --query --event push --limit 10     # 查询
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
AUDIT_FILE = ROOT / "cache" / "audit.jsonl"
BJ_TZ = timezone(timedelta(hours=8))


def audit(event: str, **fields) -> dict:
    """写一条审计记录（append-only，永不覆写）。"""
    rec = {
        "ts": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def recent(event: Optional[str] = None, limit: int = 20) -> list[dict]:
    """查询最近审计记录（可选按 event 过滤）。"""
    if not AUDIT_FILE.exists():
        return []
    rows = []
    for line in AUDIT_FILE.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if event:
        rows = [r for r in rows if r.get("event") == event]
    return rows[-limit:]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="审计日志：追加 / 查询")
    ap.add_argument("--event", help="事件类型 push|automation|config|error|system")
    ap.add_argument("--msg", help="事件描述")
    ap.add_argument("--query", action="store_true", help="查询最近记录")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    if args.query:
        for r in recent(args.event, args.limit):
            print(json.dumps(r, ensure_ascii=False))
    else:
        r = audit(args.event or "system", msg=args.msg or "")
        print("OK", r["ts"])
