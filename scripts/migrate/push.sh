#!/usr/bin/env bash
# push.sh —— 把迁移包送到服务器，校验、解到 releases/<stamp>、切换 current 软链
#
# 只做「送过去并摆好」，不启动任何服务 —— 启动由 bootstrap_remote.sh / cutover.sh 负责。
# 好处：服务器上随时有上一版（releases/ 里保留最近 3 个），回滚只是换软链。
set -euo pipefail
source "$(dirname "$0")/common.sh"
need_local ssh scp shasum

LATEST="$(ls -t "$PKG_DIR"/limao-"$MODE"-*.tar.gz 2>/dev/null | head -1 || true)"
[ -n "$LATEST" ] || die "没有找到迁移包，先跑 bash pack.sh"
[ -f "$LATEST.sha256" ] || die "缺少校验文件: $LATEST.sha256"
log "使用包: $LATEST"

EXPECT="$(cat "$LATEST.sha256")"
REMOTE_TMP="$REMOTE_ROOT/incoming"
REL_NAME="$(basename "$LATEST" .tar.gz)"

log "确保远端目录结构"
# ⚠️ 带上 set -e：目录建不出来必须立刻停，否则后面会在「不存在的路径」上假装成功
rsh "set -e; mkdir -p '$REMOTE_ROOT'/incoming '$REMOTE_ROOT'/releases '$REMOTE_ROOT'/data '$REMOTE_ROOT'/logs; \
     test -w '$REMOTE_ROOT' || { echo NOT_WRITABLE; exit 1; }" \
  || die "远端目录建不了（权限或磁盘）: $REMOTE_ROOT"

log "上传（${LATEST}）…"
rput "$LATEST" "$REMOTE_TMP/$(basename "$LATEST")"
rput "$LATEST.sha256" "$REMOTE_TMP/$(basename "$LATEST").sha256"

log "远端校验 sha256"
rsh "cd '$REMOTE_TMP' && echo '$EXPECT  $(basename "$LATEST")' | sha256sum -c -" \
  || die "校验失败：包在传输中损坏，重跑 push.sh"

log "解包到 releases/$REL_NAME"
rsh "set -e; cd '$REMOTE_ROOT'; rm -rf 'releases/$REL_NAME'; mkdir -p 'releases/$REL_NAME'; \
     tar -xzf 'incoming/$(basename "$LATEST")' -C 'releases/$REL_NAME'; \
     test -f 'releases/$REL_NAME/app/serve.py' || { echo NO_SERVE; exit 1; }; \
     ln -sfn 'releases/$REL_NAME/app' '$REMOTE_ROOT/current'; \
     touch 'releases/$REL_NAME'; \
     sudo -n chown -R \$(id -u):\$(id -g) releases 2>/dev/null || true; \
     ls -1d releases/* | sort -r | tail -n +4 | xargs -r rm -rf; \
     echo '当前指向:' \$(readlink -f '$REMOTE_ROOT/current')"

# ⛔ 换 symlink 后必须重启 serve：serve.py 在**启动时**解析 dist 的真实路径并写入白名单，
#    不重启会继续用旧 release 的 dist（实测切版后 / 与 /timeline 直接 404）。
# 解包成功后清掉上传包，否则 incoming 会一直堆着历史 tar（实测攒到 52M+）
rsh "find '$REMOTE_TMP' -name 'limao-*.tar.gz*' -type f -mmin +1 -delete 2>/dev/null; true"

log "重启 serve（让新版本生效）"
rsh "systemctl is-active --quiet limao-serve && { sudo -n systemctl restart limao-serve 2>/dev/null || systemctl restart limao-serve 2>/dev/null; sleep 1; }; true"
rsh "curl -sf -m 5 http://127.0.0.1:8000/ >/dev/null && echo '  站点自检: 200' || echo '  ⚠️ 站点自检失败，看 logs/serve.log'"

log "完成。服务器上当前版本: $REL_NAME"
log "下一步: bash bootstrap_remote.sh（首次）或 bash cutover.sh（切换）"
