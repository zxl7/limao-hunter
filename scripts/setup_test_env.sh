#!/usr/bin/env bash
# setup_test_env.sh —— 重建仓库内的测试用虚拟环境（2026-11-16）
#
# 为什么要有这个脚本：
#   之前测试跑在 `/tmp/limao-venv2`，它没有 `pyvenv.cfg`（venv 结构损坏），
#   `python -m pytest` 会直接报 “No module named pytest”，且 /tmp 会被系统清理。
#   统一改成仓库内 `.venv`（已被 .gitignore 忽略），一条命令重建。
#
# 用法：
#   bash scripts/setup_test_env.sh          # 建环境 + 跑冒烟测试
#   bash scripts/setup_test_env.sh --skip-test
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PY:-python3}"
VENV=".venv"

echo "[env] 解释器: $("$PY" -V 2>&1)"
if [ ! -x "$VENV/bin/python" ]; then
  echo "[env] 创建 $VENV"
  "$PY" -m venv "$VENV"
fi

echo "[env] 升级 pip"
"$VENV/bin/pip" install -q --upgrade pip

echo "[env] 安装依赖（优先按 requirements 的钉版本；失败则退到不钉版本）"
if ! "$VENV/bin/pip" install -q -r wealth_code/requirements.txt; then
  echo "[env] ⚠️ 钉版本装不上（多为 Python 小版本没有对应 wheel），改用不钉版本"
  "$VENV/bin/pip" install -q pytest PyYAML requests numpy pandas akshare
fi

"$VENV/bin/python" - <<'PY'
import sys
import pytest
print("[env] python", sys.version.split()[0], "| pytest", pytest.__version__)
PY

if [ "${1:-}" != "--skip-test" ]; then
  echo "[env] 冒烟测试"
  "$VENV/bin/python" -m pytest tests/test_pool_call.py -q
fi

echo "[env] 完成。以后跑测试：.venv/bin/python -m pytest tests -q"
