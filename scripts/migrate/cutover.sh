#!/usr/bin/env bash
# cutover.sh —— 真正开始用服务器（**默认 dry-run**，DRY_RUN=0 才动手）
#
# 两种模式做的事完全不同：
#   site      只启动 serve（只读站点）。Mac 侧一律不动 —— 数据仍由 Mac 产，
#             服务器只是展示副本 ⇒ 这一步不可能影响现有功能。
#   pipeline  启动 pulse 定时器 + 停掉 Mac 的抓取。**这一步会改变数据归属**，
#             脚本要求显式确认，且必须已通过 verify.sh 的影子运行对比。
set -euo pipefail
source "$(dirname "$0")/common.sh"

log "模式: $MODE   dry-run: $DRY_RUN"

log "① 启动只读站点（两种模式都需要）"
run rsh "systemctl --user enable --now limao-serve 2>/dev/null || sudo -n systemctl enable --now limao-serve"
sleep 2
if [ "$DRY_RUN" = "1" ]; then
  warn "dry-run：跳过站点自检（服务没真起，检查必然失败）"
else
log "② 站点自检（远端 :8000）"
rsh "for i in \$(seq 1 15); do curl -sf -m 3 http://127.0.0.1:8000/api/health >/dev/null && { echo 'serve 健康'; exit 0; }; sleep 1; done; echo 'serve 未起，看 '"$REMOTE_ROOT"'/logs/serve.err'; exit 1" \
  || die "远端站点未就绪，中止切换"
fi

if [ "$MODE" = "pipeline" ]; then
  warn "pipeline 模式会接管数据生产：服务器开始抓取，Mac 的 fetcher 必须停"
  [ "${CONFIRM_PIPELINE:-0}" = "1" ] || die "未确认。确认方式：CONFIRM_PIPELINE=1 DRY_RUN=0 bash cutover.sh"
  log "③ 启动抓取定时器"
  run rsh "systemctl --user enable --now limao-pulse.timer 2>/dev/null || sudo -n systemctl enable --now limao-pulse.timer"
  log "④ 停掉 Mac 侧抓取（launchd），避免两边重复抓取/重复推送"
  run launchctl bootout "gui/$(id -u)/com.limao.pulse-fetcher" 2>/dev/null || true
  log "⑤ 停掉 Mac 侧站点（可选）：launchctl bootout gui/$(id -u)/com.limao.terminal-serve"
else
  log "③ site 模式：不动任何抓取/推送链路"
fi

log "切换完成。检查清单："
cat <<EOF
  · 远端: systemctl --user status limao-serve（日志 ${REMOTE_ROOT}/logs/serve.log）
  · 站点: http://$REMOTE_HOST:8000 / http://$REMOTE_HOST:${NGINX_LISTEN}（启用 nginx 片段后）
  · 回滚: bash rollback.sh
EOF
