#!/usr/bin/env bash
# rollback.sh —— 出事就回退（默认 dry-run）
#
# 回退顺序有讲究：**先恢复 Mac 的数据生产，再停服务器**。
# 反过来做会出现「两边都不抓」的空窗，盘中断数据比多抓一份更糟。
set -euo pipefail
source "$(dirname "$0")/common.sh"

log "回滚（mode=$MODE, dry-run=${DRY_RUN}）"

if [ "$MODE" = "pipeline" ]; then
  log "① 先恢复 Mac 的抓取"
  run launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.limao.pulse-fetcher.plist" 2>/dev/null || \
    warn "Mac fetcher 恢复失败，手动: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.limao.pulse-fetcher.plist"
  log "② 再停服务器的抓取定时器"
  run rsh "systemctl --user disable --now limao-pulse.timer 2>/dev/null || sudo -n systemctl disable --now limao-pulse.timer"
fi

log "③ 停服务器站点"
run rsh "systemctl --user disable --now limao-serve 2>/dev/null || sudo -n systemctl disable --now limao-serve"

log "④ 保证 Mac 站点在跑（本地展示不受影响）"
if ! curl --noproxy '*' -sf -m 3 "http://127.0.0.1:$LOCAL_SERVE_PORT/api/health" >/dev/null 2>&1; then
  warn "本机 :$LOCAL_SERVE_PORT 未响应。恢复: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.limao.terminal-serve.plist"
else
  log "本机站点正常"
fi

log "回滚完成。上一版代码可用：远端 /opt/limao/releases/ 下换 current 软链即可"
