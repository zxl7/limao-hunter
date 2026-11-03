#!/usr/bin/env bash
# bootstrap_remote.sh —— 在服务器上把运行时准备好（只装不启，可重复执行）
#
# 做四件事：
#   ① 目录与权限（app/data/logs 分离，data 可单独挂盘）
#   ② systemd 单元落盘（本机 src 在 scripts/migrate/systemd/）
#   ③ pipeline 模式才建 venv 装 duckdb（site 模式只用系统 python，零安装）
#   ④ nginx server 片段落盘但不 enable（要不要上域名由你决定）
set -euo pipefail
source "$(dirname "$0")/common.sh"
need_local ssh scp

log "前置检查"
need_remote "tar gzip systemctl"

log "① 目录与时间戳基准"
rsh "set -e; mkdir -p '$REMOTE_ROOT'/{data,logs}; \
     chmod 755 '$REMOTE_ROOT'; echo 'REMOTE_ROOT=$REMOTE_ROOT'; df -h '$REMOTE_ROOT' | tail -1"

if [ "$MODE" = "pipeline" ]; then
  log "② pipeline 模式：建 venv 并装依赖"
  # ⛔ 只装真正需要的：duckdb 是唯一第三方包（其余全是标准库）
  rsh "set -e; cd '$REMOTE_ROOT'; [ -d venv ] || $REMOTE_PY -m venv venv; \
       ./venv/bin/pip install -q --upgrade pip; ./venv/bin/pip install -q duckdb; \
       ./venv/bin/python -c 'import duckdb;print(\"duckdb\",duckdb.__version__)'"
else
  log "② site 模式：跳过 venv（系统 python 足够，服务器不装任何包）"
fi

log "②b 环境变量文件（单元用 EnvironmentFile 读它）"
sed -e "s#__ROOT__#$REMOTE_ROOT#g" -e "s#__PY__#$REMOTE_PY#g" \
  "$MIG_DIR/remote_env.sh" > "/tmp/limao-remote_env.sh"
rput "/tmp/limao-remote_env.sh" "$REMOTE_ROOT/remote_env.sh"
rsh "chmod 644 '$REMOTE_ROOT/remote_env.sh'; cat '$REMOTE_ROOT/remote_env.sh' | sed -n '1,8p'"

log "③ systemd 单元"
for unit in limao-serve.service limao-pulse.service limao-pulse.timer; do
  [ -f "$MIG_DIR/systemd/$unit" ] || die "缺少单元模板: systemd/$unit"
done
TMPU="$(mktemp -d)"
trap 'rm -rf "$TMPU"' EXIT
for unit in limao-serve.service limao-pulse.service limao-pulse.timer; do
  sed -e "s#__ROOT__#$REMOTE_ROOT#g" -e "s#__PY__#$REMOTE_PY#g" -e "s#__MODE__#$MODE#g" \
    "$MIG_DIR/systemd/$unit" > "$TMPU/$unit"
  rput "$TMPU/$unit" "/tmp/$unit"
done
rsh "sudo -n install -m 644 /tmp/limao-*.service /tmp/limao-*.timer /etc/systemd/system/ 2>/dev/null \
     || install -m 644 /tmp/limao-*.service /tmp/limao-*.timer ~/.config/systemd/user/ 2>/dev/null \
     || { mkdir -p ~/.config/systemd/user && install -m 644 /tmp/limao-*.service /tmp/limao-*.timer ~/.config/systemd/user/; }"
rsh "systemctl --user daemon-reload 2>/dev/null || sudo -n systemctl daemon-reload"
log "单元已落盘（未启动）：limao-serve / limao-pulse"

log "④ nginx 片段（仅落盘，不启用）"
if [ -f "$MIG_DIR/nginx/limao.conf.template" ]; then
  sed -e "s#__ROOT__#$REMOTE_ROOT#g" -e "s#__PORT__#$NGINX_LISTEN#g" -e "s#__SERVER_NAME__#$NGINX_SERVER_NAME#g" \
    "$MIG_DIR/nginx/limao.conf.template" > "$TMPU/limao.conf"
  rput "$TMPU/limao.conf" "/tmp/limao-nginx.conf"
  rsh "sudo -n install -m 644 /tmp/limao-nginx.conf /etc/nginx/sites-available/limao.conf 2>/dev/null || echo '（无 sudo，nginx 片段留在 /tmp/limao-nginx.conf，需手动放置）'"
  log "nginx 片段: /etc/nginx/sites-available/limao.conf（需 ln -s 到 sites-enabled 才生效）"
fi

log "完成。下一步: bash cutover.sh（默认 dry-run）"
