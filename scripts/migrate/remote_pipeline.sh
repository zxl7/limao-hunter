#!/usr/bin/env bash
# remote_pipeline.sh —— **在服务器上跑**数据链路（不依赖 Mac）
#
# 与 sync 方向相反：这里服务器是**生产者**，Mac 变成消费者。
# 由 systemd timer 每 30s 唤醒一次，脚本内部按交易时段自节流（与 pulse_fetcher 同一套逻辑）。
#
# ⛔ 两种模式：
#   SHADOW=1（默认）只产出数据、**不推送**卡片 —— 用于与 Mac 对照，验证计算是否一致
#   SHADOW=0        同时推送飞书（此时 Mac 的 fetcher 必须停，否则同一张卡会推两遍）
set -uo pipefail

ROOT="${LIMAO_ROOT:-/home/ubuntu/limao/current}"
PY="${LIMAO_PY:-/usr/bin/python3}"
LOG_DIR="${LIMAO_LOG_DIR:-/home/ubuntu/limao/logs}"
SHADOW="${SHADOW:-1}"
STATE="$LOG_DIR/.pipeline_last_ts"
mkdir -p "$LOG_DIR"

cd "$ROOT" || { echo "ROOT 不存在: $ROOT" >&2; exit 1; }

# 单实例锁（mkdir 原子）
LOCKDIR=/tmp/limao_pipeline.lock
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  age=$(( $(date +%s) - $(stat -c %Y "$LOCKDIR" 2>/dev/null || echo 0) ))
  [ "$age" -lt 300 ] && exit 0
  rm -rf "$LOCKDIR"; mkdir "$LOCKDIR" 2>/dev/null || exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

# 交易时段自节流：早盘 30s / 午休 300s / 下午 60s / 非交易 600s（与 pulse_fetcher 一致）
hm=$(date +%H%M); mins=$((10#$hm))
if   (( mins >= 915 && mins <= 1130 )); then target=30
elif (( mins > 1130 && mins < 1300 )); then target=300
elif (( mins >= 1300 && mins <= 1500 )); then target=60
else target=600; fi

now=$(date +%s)
last=$(cat "$STATE" 2>/dev/null || echo 0)
if (( now - last < target )); then exit 0; fi
echo "$now" > "$STATE"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG_DIR/pipeline.log"; }
log "pipeline start (target=${target}s shadow=${SHADOW})"

# ① 数据层：采集 + 计算 + 落盘（脚本自身不发送任何消息）
if timeout 120 "$PY" scripts/realtime_data_pipeline.py >>"$LOG_DIR/pipeline.log" 2>&1; then
  log "data ok"
else
  log "⚠️ data 失败 rc=$?"
fi

# ② 推送层：规则卡片（影子模式下不跑）
if [ "$SHADOW" = "1" ]; then
  log "shadow: 跳过推送"
else
  if timeout 120 "$PY" scripts/realtime_push_pipeline.py >>"$LOG_DIR/pipeline.log" 2>&1; then
    log "push ok"
  else
    log "⚠️ push 失败 rc=$?"
  fi
fi

# ③ 链路断档守卫（只在正式模式告警）
if [ "$SHADOW" = "0" ]; then
  "$PY" scripts/link_guard.py >>"$LOG_DIR/pipeline.log" 2>&1 || true
fi

# 日志轮转（>5MB）
if [ -f "$LOG_DIR/pipeline.log" ] && [ "$(stat -c %s "$LOG_DIR/pipeline.log" 2>/dev/null || echo 0)" -gt 5242880 ]; then
  mv "$LOG_DIR/pipeline.log" "$LOG_DIR/pipeline.log.1"
fi
exit 0
