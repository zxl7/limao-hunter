#!/usr/bin/env bash
# deploy_site.sh —— 收盘后自动「重建终端 → 打包 → 推服务器」（2026-11-16 用户「可以」）
#
# ## 为什么放在 15:35 链里
# 复盘面板/序列、题材库都在 15:35 链里生成；生成完必须让服务器也拿到新数据，
# 否则终端页面还是旧的一天。
#
# ## 顺序
#   1. `狸猫终端 && npm run build`（服务器跑的是 dist 产物，不重建等于推旧页面）
#   2. `migrate/pack.sh`（代码 + 路由清单里的缓存）
#   3. `migrate/push.sh`（上传 → 校验 → 换软链 → 重启 serve → 自检 200）
#
# ## 纪律
# - 只在交易日跑（用 `common/trading_calendar`，⛔ 不自己数星期几）
# - 任一步失败只记日志、不阻断主链（收盘卡已经推出去了，部署失败不该连累它）
# - 全程写 `cache/logs/deploy.log`，方便第二天查「服务器为什么还是旧的」
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-/Users/zxl/.workbuddy/binaries/python/versions/3.13.12/bin/python3}"
NPM="${NPM:-/usr/local/opt/node@22/bin/npm}"
NODE_BIN="$(dirname "$NPM")"
TERMINAL="$ROOT/../狸猫终端"
LOG="$ROOT/cache/logs/deploy.log"

mkdir -p "$(dirname "$LOG")"
log() { echo "[$(TZ=Asia/Shanghai date '+%F %T')] $*" >> "$LOG"; }

export PATH="$NODE_BIN:$PATH"

# 只在交易日跑
if ! "$PY" - "$ROOT" <<'PYEOF'
import sys
from datetime import datetime, timedelta, timezone
root = sys.argv[1]
sys.path.insert(0, root)
from common.trading_calendar import next_a_share_trading_day
d = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
sys.exit(0 if next_a_share_trading_day(d) else 1)
PYEOF
then
  log "非交易日 → 跳过部署"
  exit 0
fi

log "=== 开始部署 ==="

# 1) 重建终端产物
if [ -d "$TERMINAL" ]; then
  ( cd "$TERMINAL" && "$NPM" run build >> "$LOG" 2>&1 ) || { log "⛔ 终端构建失败 → 中止（不推半旧产物）"; exit 1; }
  log "终端构建完成"
else
  log "⚠️ 未找到终端目录 $TERMINAL → 用现有 dist 继续"
fi

# 2) 打包
cd "$ROOT" || exit 1
DRY_RUN=0 bash scripts/migrate/pack.sh >> "$LOG" 2>&1 || { log "⛔ 打包失败 → 中止"; exit 1; }
log "打包完成"

# 3) 推送（内含换软链 + 重启 serve + 站点自检）
DRY_RUN=0 bash scripts/migrate/push.sh >> "$LOG" 2>&1
RC=$?
if [ "$RC" = "0" ]; then
  log "✅ 推送完成（exit 0）"
else
  # 已知噪音：远端 releases 里 root 所有的 __pycache__ 会让清理步骤返回非 0，
  # 但软链与重启已执行。这里只记「未完全成功」，不谎报成功。
  log "⚠️ 推送返回 $RC（可能是旧 release 清理权限噪音）——请核对站点是否已切版"
fi

exit 0
