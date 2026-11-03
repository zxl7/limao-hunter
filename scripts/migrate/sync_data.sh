#!/usr/bin/env bash
# sync_data.sh —— site 模式的「数据补充泵」：把本机新产出的数据增量推给服务器
#
# 为什么必须有它：site 模式下服务器只有打包那一刻的数据副本，
# 盘中不推就永远是旧盘。但**不推代码**（代码走 pack/push，避免半版本）。
#
# 只同步数据，不碰 dist/ 与脚本：
#   · cache/pulse_snapshots、cache/*.json 等热数据：每分钟级
#   · registry.db（54M）：只在变更时传（rsync 会自己判断）
set -euo pipefail
source "$(dirname "$0")/common.sh"
need_local rsync

REMOTE_DATA="$REMOTE_ROOT/current/cache"

log "增量同步数据 → $REMOTE_USER@$REMOTE_HOST:$REMOTE_DATA"
# ⛔ 排除日志与临时产物：日志是各机器自己的，混在一起会污染排障
if [ -n "${REMOTE_PASS:-}" ]; then
  sshpass -p "$REMOTE_PASS" rsync -az --delete --timeout=120 \
    -e "ssh $SSH_OPTS" \
    --exclude='logs/' --exclude='*.log' --exclude='backups/' --exclude='__pycache__/' \
    "$APP_DIR/cache/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DATA/"
else
  rsync -az --delete --timeout=120 -e "ssh $SSH_OPTS" \
    --exclude='logs/' --exclude='*.log' --exclude='backups/' --exclude='__pycache__/' \
    "$APP_DIR/cache/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DATA/"
fi

log "同步完成。校验远端最新快照："
rsh "python3 -c \"import json;d=json.load(open('$REMOTE_DATA/pulse_snapshots/latest.json'));print('  ts =',d.get('ts'));print('  涨停 =',d.get('total_zt'))\" 2>/dev/null || echo '  (读不到 latest.json)'"
