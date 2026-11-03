#!/usr/bin/env bash
# install_terminal_share.sh —— 让「对外只读服务 + 公网隧道 + 防睡眠」开机自启并常驻
#
# ⚠️ 必须在**你自己的终端窗口**里执行，不能在 AI/agent 的沙箱里跑：
#    实测 agent shell 跨 Aqua 会话注册 LaunchAgent 会报
#    `Bootstrap failed: 5: Input/output error`，换到用户终端即可成功。
#
# 用法：
#   bash /Users/zxl/WorkBuddy/狸猫猎手/scripts/install_terminal_share.sh
#   bash /Users/zxl/WorkBuddy/狸猫猎手/scripts/install_terminal_share.sh --uninstall
set -uo pipefail

UID_NUM="$(id -u)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LABELS=(com.limao.terminal-serve com.limao.terminal-tunnel com.limao.terminal-awake)
TUNNEL_LOG="$HOME/.limao/logs/tunnel.log"
SERVE_LOG="/Users/zxl/WorkBuddy/狸猫猎手/cache/logs/terminal-serve.log"

if [ "${1:-}" = "--uninstall" ]; then
  for l in "${LABELS[@]}"; do
    launchctl bootout "gui/$UID_NUM/$l" 2>/dev/null && echo "已停止 $l"
  done
  echo "（plist 保留在 $AGENTS_DIR，想彻底删除可自行 rm）"
  exit 0
fi

# ── 前置检查 ──────────────────────────────────────────────
for f in "${LABELS[@]}"; do
  [ -f "$AGENTS_DIR/$f.plist" ] || { echo "✗ 缺少 plist: $AGENTS_DIR/$f.plist"; exit 1; }
done
[ -x "$HOME/.limao/bin/cloudflared" ] || { echo "✗ 缺少 cloudflared: $HOME/.limao/bin/cloudflared"; exit 1; }
[ -x /usr/local/bin/python3.12 ] || { echo "✗ 缺少 python3.12"; exit 1; }
mkdir -p "$HOME/.limao/logs" "/Users/zxl/WorkBuddy/狸猫猎手/cache/logs"

# ── 清掉手工起的临时进程，避免端口占用导致 KeepAlive 反复重启 ──
# 按端口精确杀，不按名字模糊匹配（免得误杀别的 python 进程）
lsof -nP -iTCP:8000 -sTCP:LISTEN -t 2>/dev/null | xargs kill 2>/dev/null
pkill -f '.limao/bin/cloudflared' 2>/dev/null
sleep 1

# ── 注册三个守护进程 ──────────────────────────────────────
for l in "${LABELS[@]}"; do
  launchctl bootout "gui/$UID_NUM/$l" 2>/dev/null
  launchctl enable "gui/$UID_NUM/$l" 2>/dev/null
  if launchctl bootstrap "gui/$UID_NUM/$l" 2>/dev/null || launchctl bootstrap "gui/$UID_NUM" "$AGENTS_DIR/$l.plist" 2>/dev/null; then
    echo "✓ 已注册 $l"
  else
    echo "✗ 注册失败 $l（看上方报错）"
  fi
done

# ── 等待隧道拿到域名 ──────────────────────────────────────
echo -n "等待隧道域名…"
url=""
for _ in $(seq 1 40); do
  url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | tail -1)"
  [ -n "$url" ] && break
  echo -n "."
  sleep 1
done
echo

# ── 验活（以实际 HTTP 结果为准，不看「已注册」字样）─────────────
ok_local=0; ok_pub=0
for _ in $(seq 1 20); do
  curl -sf --max-time 5 http://127.0.0.1:8000/api/health >/dev/null && { ok_local=1; break; }
  sleep 2
done
if [ -n "$url" ]; then
  for _ in $(seq 1 20); do
    curl -sf --max-time 8 "$url/api/health" >/dev/null && { ok_pub=1; break; }
    sleep 3
  done
fi

echo "────────────────────────────────────────────────"
if [ "$ok_local" = 1 ]; then echo "本机服务 :8000   ✓"; else echo "本机服务 :8000   ✗  见 $SERVE_LOG"; fi
echo "对外地址        ${url:-（未取到，见 $TUNNEL_LOG）}"
if [ "$ok_pub" = 1 ]; then echo "公网连通        ✓"; else echo "公网连通        ✗  稍等 10s 再跑 scripts/share_url.sh 复查"; fi
echo "────────────────────────────────────────────────"
for l in "${LABELS[@]}"; do
  launchctl print "gui/$UID_NUM/$l" >/dev/null 2>&1 \
    && echo "  ✓ $l" || echo "  ✗ $l 未在册"
done
echo
echo "日常查地址/状态：bash /Users/zxl/WorkBuddy/狸猫猎手/scripts/share_url.sh"
echo "⚠️ cloudflared 快隧重启会换域名，以本脚本末尾打印的地址为准。"
