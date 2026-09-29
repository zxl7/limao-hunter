#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_card 输出脱敏回归锁（2026-09-23）。

背景：`--dry-run` 曾直接打印**完整 webhook URL**（含 token），该输出会进终端、
日志、审计记录甚至被回贴到对话里 → 凭据泄漏。修复后一律走 `_mask_url()`。
本测试锁两件事：① 掩码后不得包含完整 token；② 仍能区分不同 webhook（可核对身份）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import push_card as pc  # noqa: E402


TOKEN = "abcdef12-3456-7890-abcd-ef1234567890"
URL = f"https://open.feishu.cn/open-apis/bot/v2/hook/{TOKEN}"


def test_mask_url_hides_full_token():
    masked = pc._mask_url(URL)
    assert TOKEN not in masked, f"完整 token 泄漏: {masked}"
    assert masked.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/")
    assert "…" in masked


def test_mask_url_keeps_identity_distinguishable():
    other = "https://open.feishu.cn/open-apis/bot/v2/hook/99999999-1111-2222-3333-444444444444"
    assert pc._mask_url(URL) != pc._mask_url(other)
    # 前缀与后 4 位保留 → 能与 settings.json 里对应条目人工比对
    assert pc._mask_url(URL).endswith(TOKEN[-4:])


def test_mask_url_edge_cases():
    assert pc._mask_url("") == "(空)"
    assert pc._mask_url(None) == "(空)"
    # 无路径分隔符 → 整体当 token
    assert "short" not in pc._mask_url("short")
    # 短 token 不泄漏
    assert pc._mask_url("https://x.cn/hook/abc") == "https://x.cn/hook/***"
    # 非 URL 长串不整段外泄
    long_token = "z" * 40
    out = pc._mask_url(long_token)
    assert long_token not in out and "…" in out
