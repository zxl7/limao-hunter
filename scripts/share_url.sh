#!/usr/bin/env bash
# share_url.sh —— 打印当前对外分享地址与三个守护进程的状态
#
# 为什么需要它：cloudflared 快隧每次**重启都会换域名**，域名只写在进程日志里。
# 所以「链接是多少」不能靠记，必须现查。
set -uo pipefail

TUNNEL_LOG="$HOME/.limao/logs/tunnel.log"
SERVE_LOG="/Users/zxl/WorkBuddy/狸猫猎手/cache/logs/terminal-serve.log"

url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | tail -1)"

echo "对外地址: ${url:-（未取到，看 ${TUNNEL_LOG}）}"

for label in com.limao.terminal-serve com.limao.terminal-tunnel com.limao.terminal-awake; do
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    state="$(launchctl print "gui/$(id -u)/$label" | awk -F'= ' '/^\tstate = /{print $2; exit}')"
    echo "  ✓ $label  state=${state:-?}"
  else
    echo "  ✗ $label  未加载"
  fi
done

# 端口与公网连通性（无 token 时 /api/health 应 200）
if curl -sf --max-time 8 http://127.0.0.1:8000/api/health >/dev/null; then
  echo "  本机 :8000 ✓"
else
  echo "  本机 :8000 ✗  （看 ${SERVE_LOG}）"
fi
if [ -n "$url" ] && curl -sf --max-time 12 "$url/api/health" >/dev/null; then
  echo "  公网 /api/health ✓"
else
  echo "  公网 /api/health ✗"
fi
