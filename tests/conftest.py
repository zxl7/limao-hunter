#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pytest 统一配置（2026-09-02 · 中枢体检 P1-2）。

作用：把 pytest 的 basetemp 固定到项目内 .pytest_tmp，
避免依赖系统 /tmp（受限环境/沙箱下 mkdir 会被拒绝，导致 tmp_path 夹具全部 ERROR）。
这样 `pytest tests/` 在本机与受限环境下行为一致。
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP_DIR = ROOT / ".pytest_tmp"


def pytest_configure(config):
    """固定 basetemp（默认项目内 .pytest_tmp），但**尊重显式指定**。

    2026-10-27 补：原实现无条件覆盖 `--basetemp`，导致
      ① 命令行传的临时目录被吞掉；
      ② 项目 `.pytest_tmp` 长期不清理（实测积累 1433 个文件），pytest 每次
         开工想清空旧目录 → 被本机 safe-delete 批量删除确认拦下 → 9 个用
         `tmp_path` 的用例整片 ERROR（`SystemExit`），看起来像代码坏了。
    现在：`PYTEST_TMP_DIR` 环境变量 > 命令行 `--basetemp` > 默认 `.pytest_tmp`。
    """
    explicit = os.environ.get("PYTEST_TMP_DIR")
    args = getattr(getattr(config, "invocation_params", None), "args", ()) or ()
    if explicit:
        target = pathlib.Path(explicit)
    elif any(str(a).startswith("--basetemp") for a in args):
        return                       # 用户自己在命令行定了，别再抢
    else:
        target = TMP_DIR
    target.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(target)
