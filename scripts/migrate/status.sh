#!/usr/bin/env bash
# status.sh —— 一眼看清「本地 → 服务器」这条链是活的还是断了
#
# 只看三个问题：
#   ① 定时任务在不在跑（launchd / cron 任一）
#   ② 每层最近一次成功同步是什么时候（决定页面看到的数据有多旧）
#   ③ 服务器上的快照 vs 本地快照，差多少秒
set -uo pipefail
source "$(dirname "$0")/common.sh"

STATE="$HOME/.limao/sync_state"
LABEL="com.limao.migrate-sync"

hm=$(date +%H%M)
market=0
{ [ "$hm" -ge 0900 ] && [ "$hm" -le 1135 ]; } && market=1
{ [ "$hm" -ge 1255 ] && [ "$hm" -le 1510 ]; } && market=1

echo "══ 定时机制"
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "  ✓ launchd $LABEL 在册"
elif crontab -l 2>/dev/null | grep -q "sync_tick.sh"; then
  echo "  ✓ cron 在册（每分钟）"
else
  echo "  ✗ 没有任何定时任务 —— 数据不会自动更新"
  echo "    装：bash scripts/migrate/install_migrate_sync.sh"
fi

echo "══ 各层最近同步"
show_layer() {
  local key="$1" label="$2" limit="$3" ts
  ts=$(cat "$STATE/$key" 2>/dev/null || echo 0)
  if [ "$ts" = "0" ]; then echo "  - $label: 从未同步"; return; fi
  local ago=$(( $(date +%s) - ts ))
  local flag=""
  [ "$market" = "1" ] && [ "$ago" -gt "$limit" ] && flag="  ⚠️ 超时"
  echo "  - $label: ${ago}s 前${flag}"
}
show_layer realtime "实时层 T0R（盘中每 30s）" 120
show_layer hot      "热层 T0（每 5min）"        900
show_layer warm     "温层 T1（每 5min）"        900
if [ -f "$STATE/cold_day" ]; then echo "  - 冷层 T2: 最近 $(cat "$STATE/cold_day")"; else echo "  - 冷层 T2: 未跑过"; fi

echo "══ 数据新鲜度（本地 vs 服务器）"
L=$(python3 - <<'PY' 2>/dev/null
import json,os
p=os.path.join(os.environ.get('APP_DIR','/Users/zxl/WorkBuddy/狸猫猎手'),'cache/pulse_snapshots/latest.json')
try:
    d=json.load(open(p,encoding='utf-8')); print(d.get('ts',''))
except Exception: print('')
PY
)
[ -n "${APP_DIR:-}" ] || APP_DIR="/Users/zxl/WorkBuddy/狸猫猎手"
R=$(rsh "python3 -c \"import json;print(json.load(open('$REMOTE_ROOT/current/cache/pulse_snapshots/latest.json')).get('ts',''))\" 2>/dev/null" || true)
echo "  本地: ${L:-读不到}"
echo "  远端: ${R:-读不到}"
if [ -n "$L" ] && [ -n "$R" ]; then
  lag=$(python3 - "$L" "$R" <<'PY'
import sys,datetime as dt
def p(s):
    try: return dt.datetime.fromisoformat(s.replace('Z','+00:00'))
    except Exception: return None
a,b=p(sys.argv[1]),p(sys.argv[2])
print(int((a-b).total_seconds()) if a and b else -1)
PY
)
  if [ "$lag" -ge 0 ]; then
    if [ "$lag" -le 120 ]; then echo "  延迟: ${lag}s ✓"
    elif [ "$lag" -le 600 ]; then echo "  延迟: ${lag}s （可接受，页面略旧）"
    else echo "  延迟: ${lag}s ⚠️ 偏大"; fi
  fi
fi

echo "══ 最近失败（近 20 行日志里的告警）"
if [ -f "$STATE/sync.log" ]; then
  grep -c "⚠️" "$STATE/sync.log" 2>/dev/null | sed 's/^/  累计告警条数: /'
  grep "⚠️" "$STATE/sync.log" 2>/dev/null | tail -3 | sed 's/^/  /'
else
  echo "  无日志（还没跑过）"
fi
