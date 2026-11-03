#!/usr/bin/env bash
# sync_hot.sh —— 把「终端真正会读的文件」增量推给服务器（分冷热三层）
#
# 设计要点：
#   ① 清单来自 manifest.py（读 serve.py 的 PUBLIC_ROUTES 反推），不是拍脑袋列目录
#   ② 三档频率：T0 2.9MB/30s、T1 14.8MB/5min、T2 44MB/每日
#   ③ `--delay-updates`：先传临时名再统一 rename，避免 serve.py 读到写了一半的 JSON
#   ④ SSH 连接复用（ControlMaster）：30s 一次的调度不能每次都做完整握手
#
# 用法：
#   TIER=R bash sync_hot.sh      # 实时子集（cadence=intraday，1.8MB，每 60s）
#   TIER=0 bash sync_hot.sh      # 全部单文件路由（3MB，每 5min）
#   TIER=1 bash sync_hot.sh      # 每 5 分钟
#   TIER=2 bash sync_hot.sh      # 每日盘后
#   TIER=all bash sync_hot.sh    # 首次全量
set -euo pipefail
source "$(dirname "$0")/common.sh"
need_local rsync python3

TIER="${TIER:-0}"
MANIFEST_PY="$MIG_DIR/manifest.py"
ONLY_MARKET_HOURS="${ONLY_MARKET_HOURS:-1}"

# 非交易时段不必 30s 推一次（省电省连接，也避免无意义日志）
if [ "$ONLY_MARKET_HOURS" = "1" ] && [ "$TIER" = "0" ]; then
  hm=$(date +%H%M)
  if ! { [ "$hm" -ge 0900 ] && [ "$hm" -le 1135 ]; } && ! { [ "$hm" -ge 1255 ] && [ "$hm" -le 1510 ]; }; then
    exit 0
  fi
fi

# 清单缺失或超过 10 分钟就重算（盘中会新增当日文件，陈旧清单会漏推）
for t in 0R 0 1 2; do
  f="$PKG_DIR/T$t.txt"
  if [ ! -f "$f" ] || [ -n "$(find "$f" -mmin +10 2>/dev/null)" ]; then
    python3 "$MANIFEST_PY" >/dev/null || die "生成清单失败"
    break
  fi
done

SSH_MUX="$SSH_OPTS -o ControlMaster=auto -o ControlPath=/tmp/limao-sync-%C -o ControlPersist=300"
SYNC_TARGET="$REMOTE_USER@$REMOTE_HOST:$REMOTE_ROOT/current"

push_tier() {
  # ⚠️ 清单文件名是 T0R.txt（不是 TR.txt），这里统一映射
  local tag="$1"
  [ "$tag" = "R" ] && tag="0R"
  local list="$PKG_DIR/T$tag.txt"
  [ -s "$list" ] || { warn "T$1 清单为空，跳过"; return 0; }
  local n; n=$(wc -l < "$list" | tr -d ' ')
  log "T$1 推送（$n 个文件，清单 ${list}）"
  local -a RSYNC=(rsync -a --files-from="$list" --delay-updates --timeout=120 -e "ssh $SSH_MUX")
  # T1/T2 体积稍大，开压缩；T0 只有 3MB 且是 JSON，压不动多少，省 CPU
  # 实时层很小（1.8MB）且要快：不压缩，省 CPU 与延迟
  case "$1" in 1|2) RSYNC+=(-z);; esac
  if [ -n "${REMOTE_PASS:-}" ]; then
    sshpass -p "$REMOTE_PASS" "${RSYNC[@]}" "$APP_DIR/" "$SYNC_TARGET/"
  else
    "${RSYNC[@]}" "$APP_DIR/" "$SYNC_TARGET/"
  fi
}

case "$TIER" in
  R) push_tier R ;;
  0) push_tier 0 ;;
  1) push_tier 1 ;;
  2) push_tier 2 ;;
  all) push_tier 0; push_tier 1; push_tier 2 ;;
  *) die "TIER 只能是 0/1/2/all" ;;
esac

# T0 是高频动作，只在首次/异常时回读校验，避免每次都多一次往返
if [ "${VERBOSE:-0}" = "1" ]; then
  rsh "python3 -c \"import json;d=json.load(open('$REMOTE_ROOT/current/cache/pulse_snapshots/latest.json'));print('远端快照', d.get('ts'))\" 2>/dev/null || echo '远端读不到 latest.json'"
fi
