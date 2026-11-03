#!/usr/bin/env bash
# mirror_back.sh —— 反向镜像：把服务器产出的数据**拉回本机**（给 AI 任务读）
#
# 为什么需要它：
#   数据面迁到服务器后，服务器是生产者。但本机那一堆 AI 研判脚本读的是 `cache/*.json` 本地文件。
#   与其把它们全部改成走 HTTP（改动大、易漏），不如把数据**拉回来**——
#   AI 脚本零改造，仍读本地路径，只是数据来源换成了服务器。
#
# 方向：服务器 → 本机（只读拉取）。本机**不再写** cache，所以不会分叉。
set -euo pipefail
source "$(dirname "$0")/common.sh"
need_local rsync

REMOTE_CACHE="$REMOTE_ROOT/current/cache"
LOCAL_CACHE="$APP_DIR/cache"
TIERS="${TIERS:-0}"   # 0=只拉热数据（默认）；all=连冷层一起

RSH="ssh $SSH_OPTS"
[ -n "${REMOTE_PASS:-}" ] && RSH="sshpass -p '$REMOTE_PASS' $RSH"

log "反向拉取 $REMOTE_USER@$REMOTE_HOST:$REMOTE_CACHE → $LOCAL_CACHE"
# ⚠️ 不 --delete：本机可能有 AI 产出的额外文件（如 decision 草稿），删了会丢
if [ "$TIERS" = "all" ]; then
  EXTRA=()
else
  EXTRA=(--exclude='zt_genes/' --exclude='kline_qfq/' --exclude='stock_arc/' --exclude='logs/' --exclude='*.log')
fi

if [ -n "${REMOTE_PASS:-}" ]; then
  sshpass -p "$REMOTE_PASS" rsync -az --timeout=120 -e "ssh $SSH_OPTS" "${EXTRA[@]}" \
    "$REMOTE_USER@$REMOTE_HOST:$REMOTE_CACHE/" "$LOCAL_CACHE/"
else
  rsync -az --timeout=120 -e "ssh $SSH_OPTS" "${EXTRA[@]}" \
    "$REMOTE_USER@$REMOTE_HOST:$REMOTE_CACHE/" "$LOCAL_CACHE/"
fi

log "完成。本机快照："
python3 - "$LOCAL_CACHE/pulse_snapshots/latest.json" <<'PY' 2>/dev/null || echo "  (读不到 latest.json)"
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print("  ts =", d.get("ts"), " 涨停 =", d.get("total_zt"))
PY
