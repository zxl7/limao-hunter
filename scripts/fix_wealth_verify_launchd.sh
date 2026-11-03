#!/bin/bash
# ============================================================
# 竞价验证卡 launchd · 一键修复（2026-10-26）
# ⚠️ 必须在**图形会话的 Terminal.app** 里执行（不能由 agent / ssh / 后台进程代跑）：
#    域间调用 bootstrap 会被拒，报 "Bootstrap failed: 5: Input/output error"。
# 用法：bash /Users/zxl/WorkBuddy/狸猫猎手/scripts/fix_wealth_verify_launchd.sh
# ============================================================
set -u
LABEL=com.limao.wealth-verify
PLIST=$HOME/Library/LaunchAgents/${LABEL}.plist
UID_N=$(id -u)

echo "== 1/4 前置检查 =="
[ -f "$PLIST" ] || { echo "✗ plist 不存在：$PLIST"; exit 1; }
plutil -lint "$PLIST" >/dev/null 2>&1 || { echo "✗ plist 语法错误"; plutil -lint "$PLIST"; exit 1; }
echo "  plist 存在且语法 OK"

echo "== 2/4 清残留 + 复位启用态 =="
launchctl bootout gui/${UID_N}/${LABEL} 2>/dev/null && echo "  已踢掉旧实例" || echo "  无旧实例（正常）"
launchctl enable gui/${UID_N}/${LABEL} 2>&1 && echo "  覆盖表已置 enabled"

echo "== 3/4 注册 =="
if launchctl bootstrap gui/${UID_N} "$PLIST" 2>&1; then
    echo "  ✓ bootstrap 成功"
else
    echo "  bootstrap 失败，回退 legacy load -w"
    launchctl load -w "$PLIST" 2>&1 || { echo "✗ 两种方式都失败，请把上面完整报错发我"; exit 1; }
fi

echo "== 4/4 验证 =="
sleep 1
if launchctl print gui/${UID_N}/${LABEL} >/dev/null 2>&1; then
    echo "  ✓ 服务已在册"
    launchctl print gui/${UID_N}/${LABEL} 2>/dev/null | grep -E "state|program|last exit|runs" | head -6
    echo
    echo "结论：下一个交易日 09:25:00 由本机精准触发，09:26 推卡。"
    echo "临时冒烟测（不推卡、只看是否真被唤起）："
    echo "  launchctl kickstart -p gui/${UID_N}/${LABEL}   # 若今日已推会因 DONE_MARK 直接退出"
else
    echo "  ✗ 仍未在册，把本段完整输出发我"
    exit 1
fi
