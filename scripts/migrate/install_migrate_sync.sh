#!/usr/bin/env bash
# install_migrate_sync.sh —— 装「数据联动」定时任务（实时层每 60s）
#
# ⚠️ 必须在**你自己的终端**里跑：agent 沙箱里 `launchctl bootstrap` 会报
#    Bootstrap failed: 5: Input/output error，`crontab` 直接 operation not permitted
#
# 两种机制，按可用性自动选：
#   ① launchd（首选，日志规范、可 kickstart）
#   ② cron（launchd 装不上时的兜底；macOS 上 cron 仍可用）
#
# 用法：
#   bash scripts/migrate/install_migrate_sync.sh
#   bash scripts/migrate/install_migrate_sync.sh --status
#   bash scripts/migrate/install_migrate_sync.sh --uninstall
set -uo pipefail

LABEL="com.limao.migrate-sync"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$HERE/../.." && pwd)"
UID_NUM="$(id -u)"
STATE="$HOME/.limao/sync_state"
TICK="$HERE/sync_tick.sh"
CRON_TAG="# limao-data-sync"

case "${1:-}" in
  --status)
    exec env APP_DIR="$APP_DIR" bash "$HERE/status.sh" ;;
  --uninstall)
    launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null && echo "已停止 launchd $LABEL"
    rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
    if crontab -l 2>/dev/null | grep -q "$CRON_TAG"; then
      ( crontab -l 2>/dev/null | grep -v "$CRON_TAG" ) | crontab -
      echo "已移除 cron 条目"
    fi
    echo "卸载完成"
    exit 0 ;;
esac

echo "══ 0) 前置检查"
[ -x "$TICK" ] || { echo "✗ 缺 $TICK"; exit 1; }
if ! ssh -o BatchMode=yes -o ConnectTimeout=8 limao-server true 2>/dev/null \
   && ! ssh -o BatchMode=yes -o ConnectTimeout=8 -i "$HOME/.ssh/id_ed25519_limao" ubuntu@43.136.183.186 true 2>/dev/null; then
  echo "✗ 免密登录未就绪。先跑：REMOTE_PASS='***' bash $HERE/setup_ssh_key.sh"
  exit 1
fi
echo "  ✓ 免密可用；脚本 $TICK"
mkdir -p "$STATE" "$HOME/Library/LaunchAgents"

echo "══ 1) 立即手动跑一拍（先证明链路通，再谈定时）"
if bash "$TICK"; then
  echo "  ✓ 单拍成功"
else
  echo "  ✗ 单拍失败，先修连接再装定时"; exit 1
fi

echo "══ 2) 尝试 launchd"
SRC="$HERE/$LABEL.plist"
DST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null
sed "s#/Users/zxl/WorkBuddy/狸猫猎手#$APP_DIR#g" "$SRC" > "$DST"
if launchctl bootstrap "gui/$UID_NUM" "$DST" 2>/dev/null \
   && launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
  echo "  ✓ launchd 已注册（每 60s 一拍）"
  MECH="launchd"
else
  echo "  ⚠️ launchd 装不上（常见于非交互会话），改用 cron"
  ( crontab -l 2>/dev/null | grep -v "$CRON_TAG"; \
    echo "* * * * * /bin/bash $TICK >/dev/null 2>&1 $CRON_TAG" ) | crontab - \
    || { echo "  ✗ cron 也不可用（可能需要给终端 Full Disk Access）"; exit 1; }
  crontab -l 2>/dev/null | grep -q "$CRON_TAG" && echo "  ✓ cron 已注册（每分钟）"
  MECH="cron"
fi

echo "══ 3) 验证"
[ "$MECH" = "launchd" ] && launchctl kickstart -k "gui/$UID_NUM/$LABEL" 2>/dev/null
sleep 4
if [ -f "$STATE/sync.log" ]; then
  echo "  最近日志:"; tail -3 "$STATE/sync.log" | sed 's/^/    /'
fi

echo
echo "══ 完成（机制: $MECH）"
echo "看状态：bash $HERE/install_migrate_sync.sh --status"
echo "看延迟：bash $HERE/status.sh"
echo "卸载：  bash $HERE/install_migrate_sync.sh --uninstall"
