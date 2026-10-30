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

# 1) 增量同步基础业务缓存 cache/（排除 62MB zt_genes / 私密 workbuddy / 设置密钥 / 访问日志）
#    ⚠️ 不要排除 decision_log/ 与 ai_view_*.json：/api/decision/{archive,zt-pool,manifest}
#    与 /api/ai/view 已放开（2026-10-26），依赖这两个来源。要临时关私密页用服务器侧
#    SHARE_PRIVATE=0 回退（serve.py:59,617），而不是在这里排除——排除会让已放开路由
#    在服务器 404。
rsync -az --delete \
  --exclude='zt_genes/' \
  --exclude='*.workbuddy/' \
  --exclude='settings.json' \
  --exclude='access_log.jsonl' \
  "$LOCAL_ROOT/cache/" "$SERVER:$REMOTE_DIR/cache/"

# 1b) 增量同步 wealth_code/cache/（阻断级必同步）
#     2026-10-26 放开的 /api/decision/*、/api/ai/decisions、/api/ai/nav、/api/account/* 以及
#     盘前/早鸟/混合卡片存档全部读此目录（serve.py:324-338,350-353）。该目录在仓库根的
#     wealth_code/cache/，是 cache/ 的同级目录——漏同步会导致服务器上这些页面全部 404。
rsync -az --delete \
  --exclude='*.workbuddy/' \
  "$LOCAL_ROOT/wealth_code/cache/" "$SERVER:$REMOTE_DIR/wealth_code/cache/"

# 3) 同步前端构建产物
rsync -az "$LOCAL_ROOT/$DIST/" "$SERVER:$REMOTE_DIR/dist/"

echo "[$(date '+%F %T')] sync ok"
