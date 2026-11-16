#!/bin/bash
# ============================================================
# LLM 催化层 · launchd 触发（2026-11-12 用户选 A：仓库直连 API）
# 作用：每天收盘后 15:35 生成「下一交易日各题材催化强度」→ cache/ai_advice_<执行日>.json
#       09:25 的 pipeline_verify 会自动读取（cat 层 shadow，只记录）。
# 凭据：~/.config/limao/llm.env（chmod 600），内容形如：
#         export LLM_API_KEY=sk-xxxx
#         export LLM_BASE_URL=https://api.deepseek.com   # 可选
#         export LLM_MODEL=deepseek-chat                  # 可选
# ⛔ 绝不把 key 写进仓库。
# 回滚：launchctl unload ~/Library/LaunchAgents/com.limao.ai-catalyst.plist
# ============================================================
set -u
ROOT=/Users/zxl/WorkBuddy/狸猫猎手
PY=/Users/zxl/.workbuddy/binaries/python/versions/3.13.12/bin/python3
ENVF=$HOME/.config/limao/llm.env
LOG=$ROOT/cache/ai_catalyst_launchd.log

log() { echo "[$(TZ=Asia/Shanghai date '+%F %T')] $*" >> "$LOG"; }

cd "$ROOT" || exit 1

# 🥇 一进二打板候选（2026-11-12）：每个交易日收盘后生成，供次日盘中提醒读取
#    ⛔ 只读计算，不依赖 LLM key；失败不影响后续催化链。
"$PY" scripts/first_to_second_watch.py >> "$LOG" 2>&1 || log "first_to_second_watch 失败（不影响主链）"

# 👑⚠️ 高标处境 + 明日跌停预判（复盘卡引用）
"$PY" scripts/review_extras.py >> "$LOG" 2>&1 || log "review_extras 失败（不影响主链）"
"$PY" scripts/review_series.py >> "$LOG" 2>&1 || log "review_series 失败（不影响主链）"
# 🏷 题材基座兜底：选股宝快照若 15:30 没落成（接口盘后关闭/抓空），这里再试一次并报覆盖度
"$PY" scripts/capture_theme_snapshot.py >> "$LOG" 2>&1 || log "题材快照兜底未成功（当日池已定稿则会重试）"

# 🔒 持仓结算：断板（收盘未涨停）→ 退出并推卡
"$PY" scripts/streak_holdings.py --execute >> "$LOG" 2>&1 || true   # T+1：先执行昨日信号的卖出
"$PY" scripts/streak_holdings.py --settle --push >> "$LOG" 2>&1 || log "streak_holdings 结算失败（不影响主链）"

[ -f "$ENVF" ] || { log "未配置 ${ENVF}（缺 LLM_API_KEY）→ 跳过"; exit 0; }
# shellcheck disable=SC1090
. "$ENVF"
[ -n "${LLM_API_KEY:-}" ] || { log "LLM_API_KEY 为空 → 跳过"; exit 0; }

cd "$ROOT" || exit 1

# 只在交易日跑（周末/节假日跳过）
"$PY" - <<'PYEOF' || { log "非交易日 → 跳过"; exit 0; }
import sys
sys.path.insert(0, "/Users/zxl/WorkBuddy/狸猫猎手")
from common.trading_calendar import next_a_share_trading_day
from datetime import datetime, timedelta, timezone
d = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
sys.exit(0 if next_a_share_trading_day(d) else 1)
PYEOF

OUT=$("$PY" scripts/ai_catalyst.py --next 2>&1)
RC=$?
log "$OUT"
log "RC=$RC"
exit $RC
