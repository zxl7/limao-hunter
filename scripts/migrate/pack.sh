#!/usr/bin/env bash
# pack.sh —— 打一个可复现的迁移包（代码 + 配置 + 必要缓存 + 站点产物）
#
# 为什么打包而不是直接 rsync 整个目录：
#   ① 283M 的 cache 里大部分是可重建的 K 线，全量搬一次要十几分钟，还容易把日志带上去
#   ② 包带 MANIFEST + sha256，远端解包后能自证「搬的是哪一版」，回滚有据
#
# 用法：
#   bash pack.sh            # 按 migrate.conf 的 MODE 打包
#   MODE=pipeline bash pack.sh
#   FULL=1 bash pack.sh     # 连 K 线缓存一起（首次全量、或需要远端跑回测时）
set -euo pipefail
source "$(dirname "$0")/common.sh"

[ -d "$TERMINAL_DIR/dist" ] || die "终端产物不存在: $TERMINAL_DIR/dist —— 先在 狸猫终端 跑 npm run build"
[ -f "$TERMINAL_DIR/dist/index.html" ] || die "dist/index.html 缺失，构建不完整"

mkdir -p "$PKG_DIR"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
log "暂存目录: $STAGE"

# ── 1) 后端（猎手）──
#
# ⛔ 2026-10-30 修正：原来用「排除式」rsync 整个项目（只排除 kline/arc/leader_daily），
#    结果把 285M 财经缓存几乎全打进了包（实测 52M 压缩包 / 服务器解压 279M），
#    而站点真正读的只有路由清单里那 1885 个文件。
#    现在改为**按清单打包**：cache 只带 manifest.py 反推出的文件，代码单独整目录带。
EXC=()
for p in "${EXCLUDES[@]}"; do EXC+=(--exclude="$p"); done

if [ "$MODE" = "pipeline" ]; then
  log "pipeline 模式：整目录打包（服务器要自己产数据，需要完整缓存）"
  for p in "${EXCLUDES_SITE[@]}"; do EXC+=(--exclude="$p"); done
  rsync -a "${EXC[@]}" "$APP_DIR/" "$STAGE/app/"
else
  log "site 模式：代码整带 + 缓存按路由清单带"
  # ① 代码与配置（不含数据）
  rsync -a "${EXC[@]}" \
    --exclude='cache/' --exclude='wealth_code/cache/' --exclude='outputs/' \
    --exclude='tests/' --exclude='dist/' --exclude='架构/' \
    "$APP_DIR/" "$STAGE/app/"
  # ② 数据：只拷清单里的文件，保留相对路径
  python3 "$MIG_DIR/manifest.py" >/dev/null || die "生成同步清单失败"
  LIST_ALL="$(mktemp)"
  cat "$PKG_DIR"/T0.txt "$PKG_DIR"/T1.txt "$PKG_DIR"/T2.txt 2>/dev/null | sort -u > "$LIST_ALL"
  log "  清单条目: $(wc -l < "$LIST_ALL" | tr -d ' ') 个文件"
  rsync -a --files-from="$LIST_ALL" "$APP_DIR/" "$STAGE/app/"
  rm -f "$LIST_ALL"
fi

# 站点产物单独放一份，serve.py 默认从 ./dist 读
mkdir -p "$STAGE/app/dist"
rsync -a --delete "$TERMINAL_DIR/dist/" "$STAGE/app/dist/"

# ── 2) 清单：搬了什么、多大、是什么版本 ──
{
  echo "stamp=$STAMP"
  echo "mode=$MODE"
  echo "full=${FULL:-0}"
  echo "app_dir=$APP_DIR"
  echo "serve_sha256=$(shasum -a 256 "$APP_DIR/serve.py" | awk '{print $1}')"
  echo "dist_index_sha256=$(shasum -a 256 "$TERMINAL_DIR/dist/index.html" | awk '{print $1}')"
  echo "python=$(python3 -V 2>&1)"
  echo "--- files(top 20 by size) ---"
  # ⚠️ pipefail 下 `| head` 会让上游 sort 收到 SIGPIPE 而报错退出，这里显式兜住
  find "$STAGE" -type f -exec du -k {} + 2>/dev/null | sort -rn 2>/dev/null | head -20 || true
} > "$STAGE/MANIFEST.txt"

# ── 3) 打包 ──
log "压缩…"
COPYFILE_DISABLE=1 tar -C "$STAGE" -czf "$PKG" .  # COPYFILE_DISABLE: 别把 macOS xattr 打进包
shasum -a 256 "$PKG" | awk '{print $1}' > "$PKG.sha256"

SIZE="$(du -h "$PKG" | awk '{print $1}')"
FILES="$(tar -tzf "$PKG" | wc -l | tr -d ' ')"
log "完成: ${PKG}（${SIZE}，$FILES 个条目）"
log "校验: $PKG.sha256"
echo
log "下一步: bash push.sh"
