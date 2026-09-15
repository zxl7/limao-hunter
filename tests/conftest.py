#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pytest 统一配置（2026-09-02 · 中枢体检 P1-2）。

作用：把 pytest 的 basetemp 固定到项目内 .pytest_tmp，
避免依赖系统 /tmp（受限环境/沙箱下 mkdir 会被拒绝，导致 tmp_path 夹具全部 ERROR）。
这样 `pytest tests/` 在本机与受限环境下行为一致。
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP_DIR = ROOT / ".pytest_tmp"


def pytest_configure(config):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(TMP_DIR)
