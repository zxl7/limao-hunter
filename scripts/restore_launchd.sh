#!/bin/bash
# ============================================================
# 恢复竞价验证卡 launchd 定时任务（2026-10-22）
# ------------------------------------------------------------
# 背景：2026-10-22 09:2x 排障时对 com.limao.wealth-verify 执行了
#       `launchctl bootout`，之后在受限执行环境里 `bootstrap` 一律报
#       `Bootstrap failed: 5: Input/output error`（工作会话无法向 launchd 写），
#       任务因此处于**未加载**状态。
#       后果：每天 09:25 的本地精确推卡不会触发（平台 09:40 兜底不可靠 ——
#       它挂在与主推同一个 9:25 任务里，要求那一刻仍 ≥09:40）。
#
# 用法（在**你自己的终端**里执行，不要在受限会话里跑）：
#   bash /Users/zxl/WorkBuddy/狸猫猎手/scripts/restore_launchd.sh
#
# 验证：看到 `state = not running` 且 `runs = 0` 即为已加载（等 09:25 触发）。
# ============================================================
set -u
LABEL=com.limao.wealth-verify
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_N=$(id -u)

echo "== 1. 检查 plist =="
if [ ! -f "$PLIST" ]; then
    echo "❌ plist 不存在：$PLIST"
    exit 1
fi
plutil -lint "$PLIST" || exit 1

echo
echo "== 2. 清理残留（已加载则先卸载；未加载忽略报错）=="
launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null && echo "已卸载旧实例" || echo "（无旧实例）"

echo
echo "== 3. 加载 =="
if launchctl bootstrap "gui/$UID_N" "$PLIST" 2>&1; then
    echo "bootstrap ✓"
else
    echo "bootstrap 失败，改用旧式 load -w"
    launchctl load -w "$PLIST" 2>&1 || true
fi

echo
echo "== 4. 验证 =="
sleep 1
if launchctl print "gui/$UID_N/$LABEL" 2>/dev/null | head -8; then
    echo
    echo "✅ 已加载。下次 09:25 自动触发；也可手动跑："
    echo "   bash /Users/zxl/WorkBuddy/狸猫猎手/scripts/wealth_verify_launchd.sh"
else
    echo "❌ 仍未加载。请尝试重启终端 / 重新登录，或执行："
    echo "   launchctl load -w \"$PLIST\""
    echo "   若仍失败，重启 Mac 后 launchd 会自动加载 ~/Library/LaunchAgents 下的 plist。"
fi
