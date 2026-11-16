#!/usr/bin/env bash
# lhb_eod.sh —— 龙虎榜盘后抓取（2026-11-16 用户「龙虎榜单独做一个定时任务」）
#
# ## 为什么要独立
# 龙虎榜**交易所 18:00 后才披露**，而收盘复盘链已经提前到 15:18。
# 把它塞进复盘链只有两个结果：要么整体拖到 18 点后，要么天天抓空。
# 所以拆成独立任务，单独在 18:10 跑。
#
# ## 用法（WorkBuddy 定时任务的 prompt 里写这一行）
#     bash /Users/zxl/WorkBuddy/狸猫猎手/scripts/lhb_eod.sh
#
# ## 纪律
# - 只在交易日跑（`common/trading_calendar`）；
# - 未披露/抓空 → 脚本返回 2，日志如实记「未披露」，⛔ 不写假数据；
# - 全程写 `cache/logs/lhb_eod.log`。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-/Users/zxl/.workbuddy/binaries/python/versions/3.13.12/bin/python3}"
LOG="$ROOT/cache/logs/lhb_eod.log"
mkdir -p "$(dirname "$LOG")"
log() { echo "[$(TZ=Asia/Shanghai date '+%F %T')] $*" >> "$LOG"; }

cd "$ROOT" || exit 1

if ! "$PY" - "$ROOT" <<'PYEOF'
import sys
from datetime import datetime, timedelta, timezone
root = sys.argv[1]
sys.path.insert(0, root)
from common.trading_calendar import next_a_share_trading_day
d = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
sys.exit(0 if next_a_share_trading_day(d) else 1)
PYEOF
then
  log "非交易日 → 跳过"
  exit 0
fi

log "=== 抓取龙虎榜 ==="
"$PY" scripts/lhb_fetch.py >> "$LOG" 2>&1
RC=$?
if [ "$RC" = "0" ]; then
  log "✅ 龙虎榜已落盘 cache/lhb_$(TZ=Asia/Shanghai date +%F).json"
else
  log "⚠️ 未披露或抓取失败（rc=$RC）——如实记空，不编造"
fi

# 全量席位信号沉淀（依赖同花顺明细；缺输入会自己降级，不阻断）
"$PY" scripts/lhb_intelligence.py >> "$LOG" 2>&1 || log "lhb_intelligence 跳过/失败（不影响主流程）"

exit 0
