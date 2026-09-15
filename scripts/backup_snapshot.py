#!/usr/bin/env python3
"""盘后关键产物快照备份（P1-⑩）
盘后链（17:00 复盘任务内）对关键 JSON/MD 做 tar.gz 备份到 cache/backups/，
保留 N 天（默认 30）自动清理旧备份，误删/损坏可回滚。

用法:
  python3 scripts/backup_snapshot.py               # 备份今日
  python3 scripts/backup_snapshot.py --date 2026-08-17
  python3 scripts/backup_snapshot.py --keep 30 --dry-run
"""
import argparse
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "cache" / "backups"
BJ_TZ = timezone(timedelta(hours=8))

# 关键产物清单（glob 相对 ROOT；新增产物加一行即可）
TARGETS = [
    "cache/yesterday_zt.json",                       # 昨日涨停池（唯一涨停权威）
    "cache/theme_rotation.json",                     # 题材轮动
    "cache/feedback_matrix.json",                    # 昨日涨停反馈矩阵
    "cache/ma5_status.json",                         # 五日线
    "cache/holdings.json",                           # 当前持仓
    "cache/market_memory_*.json",                    # 情绪快照
    "cache/short_term/summary_5d.json",              # 5日情绪汇总
    "cache/short_term/短期情绪周期判断.md",
    "wealth_code/cache/candidates-*.json",           # 选股候选
    "wealth_code/cache/auction_quotes-*.json",       # 竞价快照
    "wealth_code/cache/account_nav_history.jsonl",   # 净值账本
]


def resolve(pattern: str) -> list[Path]:
    return [f for f in ROOT.glob(pattern) if f.is_file()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="备份日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--keep", type=int, default=30, help="保留天数（默认 30）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将备份文件不执行")
    args = ap.parse_args()

    date = args.date or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    files, seen = [], set()
    for pat in TARGETS:
        for f in resolve(pat):
            if str(f) not in seen:
                seen.add(str(f))
                files.append(f)

    if not files:
        print("⚠️ 无产物可备份（可能盘后链未跑或目录为空）")
        return 0

    arc = BACKUP_DIR / f"snapshot_{date}.tar.gz"
    if args.dry_run:
        print(f"[DRY] 将备份 {len(files)} 个文件 → {arc.name}")
        for f in files:
            print("   ", f.relative_to(ROOT))
        return 0

    with tarfile.open(arc, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=str(f.relative_to(ROOT)))

    # 清理超期备份（保留最近 keep 个）
    kept = sorted(BACKUP_DIR.glob("snapshot_*.tar.gz"))[-args.keep:]
    removed = 0
    for old in BACKUP_DIR.glob("snapshot_*.tar.gz"):
        if old not in kept:
            old.unlink()
            removed += 1

    print(f"✅ 备份 {len(files)} 文件 → {arc.name}（{arc.stat().st_size/1024:.0f}KB）"
          f" | 清理旧备份 {removed} 个 | 保留最近 {len(kept)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
