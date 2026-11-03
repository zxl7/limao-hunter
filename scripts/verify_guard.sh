#!/bin/bash
# ============================================================
# 竞价验证卡 · launchd 存活自检（2026-10-26 立）
# 背景：2026-10-23 / 10-26 两个交易日 launchd 任务「未加载」，
#       09:25 精准触发整段缺失，卡改由平台 09:40 兜底推 → 连续迟发。
#       根因＝服务被 bootout 后未成功 re-bootstrap，且无任何告警。
# 本脚本只做「判定」，不写任何数据、不推卡：
#   OK             → launchd 已加载，交给它 09:26 推，本任务不介入
#   NEED_BOOTSTRAP → 服务不在域内，需在图形会话执行 bootstrap
#   PUSH_NOW       → 已过 09:30 且今日无推送标记，应立即自行 verify --fetch --push
# 用法：bash scripts/verify_guard.sh
# ============================================================
set -u
LABEL=com.limao.wealth-verify
TODAY=$(TZ=Asia/Shanghai date '+%Y-%m-%d')
DONE_MARK=/tmp/wealth_verify_done_${TODAY}
HHMM=$(( 10#$(TZ=Asia/Shanghai date '+%H') * 100 + 10#$(TZ=Asia/Shanghai date '+%M') ))

LOADED=no
launchctl print gui/$(id -u)/${LABEL} >/dev/null 2>&1 && LOADED=yes

echo "trade_date=${TODAY}"
echo "now=${HHMM}"
echo "launchd_loaded=${LOADED}"
echo "done_mark=$([ -f "${DONE_MARK}" ] && echo yes || echo no)"

if [ "${LOADED}" = no ]; then
    echo "status=NEED_BOOTSTRAP"
    echo "action=在图形会话执行：launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/${LABEL}.plist"
    echo "note=本任务此刻无法代为注册（域间调用被拒），若已过 09:26 请由本任务直接 verify --fetch --push 补当日卡"
    exit 0
fi
if [ ! -f "${DONE_MARK}" ] && [ "${HHMM}" -ge 930 ]; then
    echo "status=PUSH_NOW"
    echo "action=launchd 在册但今日未推，本任务立即执行 verify --fetch --push"
    exit 0
fi
echo "status=OK"
echo "action=launchd 在册，等其 09:26 推卡，本任务不介入"
exit 0
