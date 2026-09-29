#!/usr/bin/env bash
# sync_cache.sh —— Mac 数据引擎 → 云服务器 增量同步
# 由 launchd 每分钟跑一次（见下方 plist 说明）。只同步小体积 JSON，排除大文件/私密。
#
# 用法：在下方填好 SERVER / REMOTE_DIR，本机执行即同步。
set -euo pipefail

# ⚠️ 改成你的服务器
SERVER="user@your-server-ip"
REMOTE_DIR="/opt/limao"

LOCAL_ROOT="/Users/zxl/WorkBuddy/狸猫猎手"
DIST="../狸猫终端/dist"

# 1) 增量同步 cache 下的小 JSON（排除 62MB zt_genes / 决策黑匣子 / AI 决策 / 设置密钥）
rsync -az --delete \
  --exclude='zt_genes/' \
  --exclude='decision_log/' \
  --exclude='ai_view_*.json' \
  --exclude='*.workbuddy/' \
  --exclude='settings.json' \
  --exclude='access_log.jsonl' \
  "$LOCAL_ROOT/cache/" "$SERVER:$REMOTE_DIR/cache/"

# 2) 同步前端构建产物
rsync -az "$LOCAL_ROOT/$DIST/" "$SERVER:$REMOTE_DIR/dist/"

echo "[$(date '+%F %T')] sync ok"
