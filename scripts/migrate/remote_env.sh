#!/usr/bin/env bash
# remote_env.sh —— 服务器侧的环境变量（被 systemd 单元 source，不被 interactively 调用）
#
# 为什么需要它：本项目脚本原本把「项目路径 / python 解释器」写死在 macOS 上。
# 与其改一堆脚本，不如在入口处统一注入这两个变量（脚本内已改为 `${LIMAO_ROOT:-...}` 形式）。
export LIMAO_ROOT="__ROOT__/current"
export LIMAO_PY="__PY__"
# 前端构建目录（服务器上没有 ../狸猫终端/dist，必须显式给）
export LIMAO_DIST_DIR="__ROOT__/current/dist"
export TZ=Asia/Shanghai
export PYTHONUNBUFFERED=1
# 服务器上不启用任何推送（影子运行阶段）：见 MIGRATION.md「阶段 3」
