#!/usr/bin/env bash
# setup_ssh_key.sh —— 建免密登录（自动同步的前提）
#
# 为什么必须要：launchd/systemd 定时任务里没人能输密码。
# 没有密钥，「每 30s 推一次数据」就无从谈起。
set -euo pipefail
source "$(dirname "$0")/common.sh"
need_local ssh ssh-keygen ssh-copy-id

KEY="$HOME/.ssh/id_ed25519_limao"
[ -f "$KEY" ] || { log "生成密钥 $KEY"; ssh-keygen -t ed25519 -N '' -C "limao-migrate@$(hostname -s)" -f "$KEY" >/dev/null; }
chmod 600 "$KEY"

if ssh -i "$KEY" -o BatchMode=yes $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" true 2>/dev/null; then
  log "密钥登录已可用"
else
  [ -n "${REMOTE_PASS:-}" ] || die "尚未免密，且没提供 REMOTE_PASS（首次需要它来上传公钥）"
  log "上传公钥（用一次密码）…"
  sshpass -p "$REMOTE_PASS" ssh-copy-id -i "$KEY.pub" -o StrictHostKeyChecking=accept-new "$REMOTE_USER@$REMOTE_HOST" >/dev/null
  ssh -i "$KEY" -o BatchMode=yes $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" true || die "公钥上传后仍无法登录"
  log "密钥登录已建立"
fi

# 写进 ssh config，之后所有脚本/rsync 都不用再带 -i
CFG="$HOME/.ssh/config"
if ! grep -q "Host limao-server" "$CFG" 2>/dev/null; then
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  cat >> "$CFG" <<EOF

Host limao-server
    HostName $REMOTE_HOST
    User $REMOTE_USER
    IdentityFile $KEY
    IdentitiesOnly yes
    ServerAliveInterval 30
EOF
  chmod 600 "$CFG"
  log "已写入 ~/.ssh/config 别名 limao-server"
else
  log "~/.ssh/config 已有 limao-server，跳过"
fi

log "验证：ssh limao-server 'hostname'"
ssh -o BatchMode=yes limao-server 'hostname; echo OK' || warn "别名验证失败，检查 ~/.ssh/config"
