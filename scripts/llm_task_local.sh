#!/bin/bash
# ============================================================
# 本地 LLM 任务执行器（2026-11-13 立 · 待启用，未加载 launchd）
#
# 目的：绕开平台服务端排队。平台 automation 的触发时间由服务端队列决定，
#       9:25-9:30 高峰期实测可延迟 20+ 分钟（11-13 盘中猎手 9:30→9:52）。
#       launchd 是本地进程，毫秒级准点（竞价卡 9:25:05→9:26:10 实证）。
#       本脚本把「平台 LLM 任务」搬到 launchd：prompt 复用平台那份，能力同源。
#
# 前提：codebuddy CLI 已登录。未登录时它会返回
#       "Authentication required. Please use /login command to sign in"，
#       本脚本会判定失败并退出，不会假装成功。
#       启用：先在终端跑一次 /Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy
#             交互式执行 /login，登录态会持久化到 ~/.workbuddy。
#
# 用法：llm_task_local.sh <任务名> <prompt文件> [端口]
#   例：llm_task_local.sh 盘中猎手-0930 prompts/盘中猎手-0930.txt 54622
#
# 幂等：/tmp/llm_local_done_<任务名>_<日期> 存在则跳过（防 launchctl 重载重复跑）
# 日志：cache/llm_local_<任务名>.log
# 回退：不加载 plist 即完全无效，对现有链路零影响。
# ============================================================
set -u

ROOT=/Users/zxl/WorkBuddy/狸猫猎手
CB=/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy

TASK=${1:?用法: llm_task_local.sh <任务名> <prompt文件> [端口]}
PROMPT_FILE=${2:?缺少 prompt 文件路径}
# 端口必须错开 GUI 占用的 54571，否则 EADDRINUSE 直接退出
PORT=${3:-54622}

LOG=$ROOT/cache/llm_local_${TASK}.log
MARK=/tmp/llm_local_done_${TASK}_$(TZ=Asia/Shanghai date '+%Y-%m-%d')

log() { echo "[$(TZ=Asia/Shanghai date '+%F %T')] $*" >> "$LOG"; }

if [ -f "$MARK" ]; then
    log "本日已跑（$MARK 存在），退出"
    exit 0
fi

if [ ! -f "$PROMPT_FILE" ]; then
    log "❌ prompt 文件不存在：$PROMPT_FILE"
    exit 2
fi

if [ ! -x "$CB" ]; then
    log "❌ CLI 不存在：$CB"
    exit 2
fi

cd "$ROOT" || { log "❌ cd 失败"; exit 1; }
log "开始：${TASK}（port=${PORT}，prompt=$(wc -c < "$PROMPT_FILE") 字节）"

# 20 分钟上限：盘中卡全流程含 skill 加载/读宪法/推送，正常 3-8 分钟
OUT=$(timeout 1200 "$CB" -p "$(cat "$PROMPT_FILE")" -y --output-format text --port "$PORT" 2>&1)
RC=$?
echo "$OUT" >> "$LOG"

if echo "$OUT" | grep -q "Authentication required"; then
    log "❌ CLI 未登录，任务未执行（先在终端跑一次 codebuddy 并 /login）"
    exit 3
fi

if [ "$RC" -ne 0 ]; then
    log "❌ 退出码 $RC"
    exit "$RC"
fi

touch "$MARK"
log "完成"
exit 0
