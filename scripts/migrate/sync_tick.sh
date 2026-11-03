#!/usr/bin/env bash
# sync_tick.sh —— 每 60s 被 launchd 唤醒一次，决定这一拍该推哪一层
#
# 为什么不直接在 plist 里写三条调度：
#   launchd 的 StartInterval 只有一种节奏，而这里需要「盘中 30~60s 热层 + 5min 温层 + 每日冷层」。
#   用一个 tick 脚本内部分派，逻辑集中、易改，也不会出现三条任务互相抢锁。
#
# 节流与并发：
#   · 单实例锁（mkdir 原子），避免上一拍没跑完又叠一轮
#   · 非交易时段只跑温层（每 30 分钟），热层直接跳过
set -uo pipefail
MIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${HOME}/.limao/sync_state"
mkdir -p "$STATE_DIR"

LOCKDIR=/tmp/limao_sync_hot.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  age=$(( $(date +%s) - $(stat -f%m "$LOCKDIR" 2>/dev/null || stat -c %Y "$LOCKDIR" 2>/dev/null || echo 0) ))
  if [ "$age" -lt 600 ]; then exit 0; fi
  rm -rf "$LOCKDIR"; mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

now=$(date +%s)
hm=$(date +%H%M)
market=0
{ [ "$hm" -ge 0900 ] && [ "$hm" -le 1135 ]; } && market=1
{ [ "$hm" -ge 1255 ] && [ "$hm" -le 1510 ]; } && market=1

last_run() { cat "$STATE_DIR/$1" 2>/dev/null || echo 0; }
mark() { date +%s > "$STATE_DIR/$1"; }

LOG="$STATE_DIR/sync.log"
log_line() { echo "[$(date '+%m-%d %H:%M:%S')] $*" >> "$LOG"; }
# 日志轮转（>2MB）
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 2097152 ]; then
  mv "$LOG" "$LOG.1"
fi

# ① 实时层：仅交易时段，**每拍都推**（16 个文件 / 1.8MB，单次 <1s）
#    这一层是「盘中看得到当前盘」的唯一保障，其余层都只是补历史。
if [ "$market" = "1" ]; then
  if TIER=R ONLY_MARKET_HOURS=0 "$MIG_DIR/sync_hot.sh" >>"$LOG" 2>&1; then
    mark realtime
  else
    log_line "⚠️ T0R 失败"
  fi
fi

# ①b 热层：全部单文件路由，每 5 分钟（含非交易时段的盘后产物）
if [ $(( now - $(last_run hot) )) -ge 300 ]; then
  if TIER=0 ONLY_MARKET_HOURS=0 "$MIG_DIR/sync_hot.sh" >>"$LOG" 2>&1; then mark hot; else log_line "⚠️ T0 失败"; fi
fi

# ② 温层：每 5 分钟（或非交易时段每 30 分钟）
warm_every=$([ "$market" = "1" ] && echo 300 || echo 1800)
if [ $(( now - $(last_run warm) )) -ge "$warm_every" ]; then
  if TIER=1 "$MIG_DIR/sync_hot.sh" >>"$LOG" 2>&1; then mark warm; else log_line "⚠️ T1 失败"; fi
fi

# ③ 冷层：每交易日 15:45 之后一次
if [ "$hm" -ge 1545 ] && [ "$hm" -le 1600 ]; then
  day=$(date +%F)
  if [ "$(cat "$STATE_DIR/cold_day" 2>/dev/null)" != "$day" ]; then
    if TIER=2 "$MIG_DIR/sync_hot.sh" >>"$LOG" 2>&1; then echo "$day" > "$STATE_DIR/cold_day"; else log_line "⚠️ T2 失败"; fi
  fi
fi

exit 0
