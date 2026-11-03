#!/usr/bin/env bash
# common.sh —— migrate 脚本的公共底座：读配置、日志、ssh/rsync 封装、前置检查
#
# 设计原则：
#   ① 一切路径/账号来自 migrate.conf，脚本里不出现硬编码主机
#   ② 所有远端操作都先 dry-run 打印命令，DRY_RUN=0 才真执行（cutover 尤其）
#   ③ 失败就停（set -e），不做「跳过继续」——迁移这种动作半途而废比不动更糟
set -euo pipefail

MIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR_DEFAULT="$(cd "$MIG_DIR/../.." && pwd)"
# shellcheck source=migrate.conf
source "$MIG_DIR/migrate.conf"
APP_DIR="${APP_DIR:-$APP_DIR_DEFAULT}"
DRY_RUN="${DRY_RUN:-1}"
STAMP="$(date +%Y%m%d-%H%M%S)"
PKG_DIR="$APP_DIR/dist/migrate"
PKG="$PKG_DIR/limao-${MODE}-${STAMP}.tar.gz"

# 有密钥就用密钥；没有密码也没密钥时用 BatchMode 快速失败（⛔ 不要卡在交互输密码上）
KEY_FILE="${LIMAO_SSH_KEY:-$HOME/.ssh/id_ed25519_limao}"
if [ -z "${REMOTE_PASS:-}" ]; then
  if [ -f "$KEY_FILE" ]; then
    SSH_OPTS="$SSH_OPTS -i $KEY_FILE -o IdentitiesOnly=yes"
  else
    SSH_OPTS="$SSH_OPTS -o BatchMode=yes"
  fi
fi

log()  { printf '\033[36m[migrate]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

# 远端执行：有密码走 sshpass，没有则用密钥
# ⚠️ 带重试：实测连续建连会被 sshd 抖动拒绝一次（Permission denied），不是密码错
_rssh() {
  if [ -n "${REMOTE_PASS:-}" ]; then
    sshpass -p "$REMOTE_PASS" ssh $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" "$@"
  else
    ssh $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" "$@"
  fi
}
rsh() {
  local attempt rc
  for attempt in 1 2 3; do
    _rssh "$@"; rc=$?
    # ⛔ 只对「连接层失败」(ssh 自身返回 255) 重试；
    #    远端命令返回 1 是业务结论（例如健康检查没起来），重试只会掩盖问题
    if [ "$rc" = "0" ] || [ "$rc" != "255" ]; then return "$rc"; fi
    [ "$attempt" = "3" ] && return "$rc"
    warn "SSH 连接层失败（255），2s 后重试 $attempt/3"
    sleep 2
  done
}

# 远端文件传输（同样带重试）
_rput() {
  if [ -n "${REMOTE_PASS:-}" ]; then
    sshpass -p "$REMOTE_PASS" scp $SSH_OPTS "$1" "$REMOTE_USER@$REMOTE_HOST:$2"
  else
    scp $SSH_OPTS "$1" "$REMOTE_USER@$REMOTE_HOST:$2"
  fi
}
rput() {
  local attempt rc
  for attempt in 1 2 3; do
    _rput "$1" "$2"; rc=$?
    if [ "$rc" = "0" ] || [ "$rc" != "255" ]; then return "$rc"; fi
    [ "$attempt" = "3" ] && return "$rc"
    warn "scp 连接层失败（255），2s 后重试 $attempt/3"
    sleep 2
  done
}

# 带 dry-run 的执行器
run() {
  if [ "$DRY_RUN" = "1" ]; then
    log "[dry-run] $*"
  else
    "$@"
  fi
}

need_local() {
  for c in "$@"; do command -v "$c" >/dev/null || die "本机缺少命令: $c"; done
}

need_remote() {
  rsh "for c in $*; do command -v \$c >/dev/null || { echo MISSING:\$c; exit 1; }; done" \
    || die "远端缺少命令: $*"
}

# 迁移前必须确认的事实，避免「在错的机器上迁错东西」
preflight() {
  [ -d "$APP_DIR" ] || die "本机项目目录不存在: $APP_DIR"
  [ -f "$APP_DIR/serve.py" ] || die "找不到 serve.py，APP_DIR 是否指错？"
  log "本机项目: $APP_DIR"
  log "远端目标: $REMOTE_USER@$REMOTE_HOST:$REMOTE_ROOT  模式=$MODE  dry-run=$DRY_RUN"
  log "远端连通性…"
  rsh "echo ok >/dev/null" || die "SSH 连不上 $REMOTE_HOST"
  local tz; tz="$(rsh 'cat /etc/timezone 2>/dev/null || true')"
  [ "$tz" = "Asia/Shanghai" ] || warn "远端时区不是 Asia/Shanghai（实际: ${tz:-未知}）——交易日历会错位"
  log "远端时区: ${tz:-未知}"
}
