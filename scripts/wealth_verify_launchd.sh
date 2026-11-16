#!/bin/bash
# ============================================================
# 竞价验证卡 · launchd 精确推送（2026-08-28 用户定）
# 作用：绕过平台调度抖动（±2~5 分），09:25:00 本地精确触发，
#       等竞价报价落地（09:26:05）后执行 verify --fetch --push。
# 职责分工（2026-11-12 已删除 AI 全局旁白链路）：
#   - 平台自动化 automation-1786429073209（9:25 触发）= 数据准备 + 连板台账校验 + 09:40 兜底补推
#   - 本脚本（com.limao.wealth-verify）= 数据+推送：抓报价/结算/推卡
# 幂等：今日已推（/tmp/wealth_verify_done_<date> 存在）直接退出。
# 回滚：launchctl unload ~/Library/LaunchAgents/com.limao.wealth-verify.plist
#       并恢复平台自动化 prompt（原 verify --push 版本）。
# ============================================================
set -u
# ⛔⛔ 变量引用一律写 `${VAR}`，⛔ 不许写 `$VAR` 紧跟中文标点（，）（）。
#   事故（2026-10-22 09:26）：第 69 行 `$NARR_DAY，平台任务…` 在脚本运行环境下
#   bash 把全角逗号的首字节 0xEF 并进了变量名 → `NARR_DAY<?>: unbound variable`
#   → `set -u` 直接终止脚本，推卡前一刻崩掉，只因另有链路兜底才没缺卡。
#   ⛔ 这类 bug 只在特定分支触发，平时看不出来；分支虽已删除，规范仍然生效。
ROOT=/Users/zxl/WorkBuddy/狸猫猎手
PY=/Users/zxl/.workbuddy/binaries/python/versions/3.13.12/bin/python3
LOG=$ROOT/cache/wealth_verify_launchd.log
DONE_MARK=/tmp/wealth_verify_done_$(TZ=Asia/Shanghai date '+%Y-%m-%d')

log() { echo "[$(TZ=Asia/Shanghai date '+%F %T')] $*" >> "$LOG"; }

# ⚠️ 2026-11-13 修复：记录本次运行前的日志行数，末尾「是否真推成功」只查本次新增段。
#    背景：原判据 grep 全量累积日志（11 万行），历史任一天出现过 ✓ 即命中 →
#    lint 阻断当天仍误落 DONE_MARK（11-13 实锤）→ 平台 09:40 兜底失效 → 全天无卡且无声。
LOG_START=$(wc -l < "$LOG" 2>/dev/null || echo 0)

# ⚠️ 2026-11-13 新增：推送失败必须立刻告警。
#    背景：11-13 卡片 lint 阻断 + 全量日志 grep 误判，全天无卡用户自己发现。
#    「静默失败」比「失败」贵得多——失败可兜底，静默会一路哑到收盘。
notify_fail() {
    local wh
    wh=$($PY -c "import json;print(json.load(open('$ROOT/config/settings.json')).get('feishu_webhook_prod',''))" 2>/dev/null)
    if [ -z "$wh" ]; then
        log "告警跳过：feishu_webhook_prod 为空"
        return
    fi
    $PY - "$wh" <<'PYEOF' >> "$LOG" 2>&1
import json, sys, urllib.request
wh = sys.argv[1]
body = {"msg_type": "text", "content": {"text":
    "⚠️ 竞价卡未推送\n"
    "launchd 已执行，但未检测到推送成功（卡面 lint 阻断 / 抓价失败 / 守卫拦截）。\n"
    "今日可能没有竞价卡。请查 cache/wealth_verify_launchd.log。\n"
    "平台 09:40 会自动兜底补推。"}}
req = urllib.request.Request(wh, data=json.dumps(body).encode("utf-8"),
                             headers={"Content-Type": "application/json"})
try:
    print("[NOTIFY]", urllib.request.urlopen(req, timeout=10).read().decode("utf-8")[:200])
except Exception as e:
    print("[NOTIFY] 告警发送失败:", e)
PYEOF
}

cd "$ROOT" || { echo "[$(TZ=Asia/Shanghai date '+%F %T')] cd 失败" >> "$LOG"; exit 1; }

# 幂等：本日已推过则直接退出（防 launchctl 重载/重复触发）
if [ -f "$DONE_MARK" ]; then
    log "本日已推送（$DONE_MARK 存在），退出"
    exit 0
fi
log "launchd 触发，等待竞价报价落地（09:26:05）..."

# 仅交易日（周一~周五）执行；周末直接退出（launchd StartCalendarInterval 无 BYDAY 过滤）
DOW=$(TZ=Asia/Shanghai date '+%u')  # 1=周一 ... 7=周日
if [ "$DOW" -ge 6 ]; then
    log "非工作日（周${DOW}），跳过（避免周末写脏数据）"
    exit 0
fi

# 等待至 09:26:05（verify 内置竞价守卫阈值 09:26，早于则跳过推送）
while :; do
    HHMM=$(( 10#$(TZ=Asia/Shanghai date '+%H') * 100 + 10#$(TZ=Asia/Shanghai date '+%M') ))
    SS=$(( 10#$(TZ=Asia/Shanghai date '+%S') ))
    if [ "$HHMM" -gt 926 ] || { [ "$HHMM" -eq 926 ] && [ "$SS" -ge 5 ]; }; then
        break
    fi
    sleep 2
done
log "时间已到（>=09:26:05），开始抓报价+结算+推送"

# ⚠️ 2026-10-13 加固：DONE_MARK 原**只在脚本开头检查一次**，而开头（09:25:00）到本处
#    （09:26:05）有约 65s 空窗——若期间平台自动化/其它链路已推卡并落下标记，
#    本脚本醒来仍会照推 → 双推。故在真正执行前**再查一次**（幂等双保险）。
if [ -f "$DONE_MARK" ]; then
    log "本日已被其它链路推送（$DONE_MARK 于等待期间出现），本脚本让位退出（防双推）"
    exit 0
fi

# 推送参数固定（2026-11-12 用户拍板删除 AI 全局旁白链路）：
#   竞价卡自 2026-10-21 起默认隐藏 global 块（VERIFY_CARD_HIDE 默认含 global），旁白零决策
#   权重且平台任务恒定晚于 09:26 推卡 ⇒ 不写、不注入、不再每天打「旁白未就绪」虚警。
#   ⛔ 不传 --narratives；盘后卡的全局旁白由 pipeline_eod --narratives 另一条链负责。
#   回滚：恢复本段的 --narratives 注入分支即可（run_wealth_code.py 的参数保留未删）。
PUSH_ARGS="--fetch --push"

# ⚠️ 2026-10-13 修复：原为 `$ROOT/wealth_code` → `pipeline_eod` 等 `from common.*`
#    解析失败（ModuleNotFoundError: No module named 'common'，10-01 日志实证 RC=1）。
#    `common` 在项目根 `$ROOT/common`，`core`/`pipelines` 在 `$ROOT/wealth_code` ⇒ 两个都要给。
export PYTHONPATH=$ROOT:$ROOT/wealth_code
$PY wealth_code/scripts/run_wealth_code.py verify $PUSH_ARGS >> "$LOG" 2>&1
RC=$?
log "verify RC=$RC"

# 落盘校验
TODAY=$(TZ=Asia/Shanghai date '+%Y-%m-%d')
# ⚠️ 2026-09-08 修复：原逻辑无条件 touch DONE_MARK，竞价守卫拦截（报价 0 只/SSL 故障）时
# 卡未推却落标 → 平台 09:40 兜底（仅当 DONE_MARK 缺失才补推）失效 → 当日缺卡。
# 改为「仅 verify 确实推卡成功才落 DONE_MARK」，拦截/失败留缺口给平台兜底重试。
if ls "$ROOT"/wealth_code/cache/card_*.json >/dev/null 2>&1; then
    log "卡 JSON 已落盘 ✓"
else
    log "⚠️ 卡 JSON 缺失（可能被守卫跳过）"
fi
# ⚠️ 2026-09-15 修复：路径原为 $ROOT/cache/（少 wealth_code 一层），
# 而真实落盘在 wealth_code/cache/（run_wealth_code.py: ROOT=wealth_code/ → ROOT/"cache"）。
# 后果：该分支**恒为假** —— 报价明明落地也每日误报「⚠️ 竞价报价缺失」，
# 使告警失去意义（真缺报价时无法与假警区分）。全仓另 8 处引用均为 wealth_code/cache。
if [ -s "$ROOT/wealth_code/cache/auction_quotes-$TODAY.json" ]; then
    log "竞价报价已落地 ✓"
else
    log "⚠️ 竞价报价缺失"
fi
# 仅当今日确有推送成功日志（verify 卡 JSON + "推送竞价验证卡"）才 touch
if ls "$ROOT"/wealth_code/cache/card_${TODAY//-/}_*.json >/dev/null 2>&1 \
    && tail -n +$((LOG_START + 1)) "$LOG" | grep -q "推送竞价验证卡: ✓"; then
    touch "$DONE_MARK"
    log "推送成功，落 DONE_MARK"
else
    log "⚠️ 未检测到推送成功（守卫拦截或失败），不落 DONE_MARK（平台 09:40 兜底可补推）"
    notify_fail
fi
log "完成。----------------------------------------"
exit 0
