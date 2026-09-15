#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 mtime 缓存（P1-⑦ 基础层 · 2026-08-17）
用于「输入文件未变 → 跳过下游重算」：比较输入文件集与输出文件集的 mtime。
  - 任一输入不存在 → 需重算
  - 任一输出不存在 → 需重算
  - max(输入 mtime) > min(输出 mtime) → 输入更新，需重算
  - 否则 → 输出比输入新，跳过重算（缓存命中）
用法（明天接入 pulse_fetcher 链路时）：
    from common.mtime_cache import needs_recompute, skip_msg
    if args.force or needs_recompute(inputs=["cache/pulse_snapshots/latest.json"],
                                     outputs=["cache/candidates.json"]):
        ... 执行重算 ...
    else:
        print(skip_msg("gen_candidates"))   # 输出「[mtime] 跳过 gen_candidates：输入未变」
"""
import os
from pathlib import Path


def _mtime(p: str) -> float:
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def needs_recompute(inputs, outputs, force: bool = False) -> bool:
    """判断是否需要重算。inputs/outputs 为路径列表（可含不存在项）。"""
    if force:
        return True
    ins = [str(i) for i in inputs]
    outs = [str(o) for o in outputs]
    # 任一输入缺失 → 保守重算
    if any(not os.path.exists(i) for i in ins):
        return True
    # 任一输出缺失 → 重算
    if any(not os.path.exists(o) for o in outs):
        return True
    newest_in = max(_mtime(i) for i in ins)
    oldest_out = min(_mtime(o) for o in outs)
    return newest_in > oldest_out


def skip_msg(step: str) -> str:
    """缓存命中时的提示行（与 fetch.log 格式一致）。"""
    return f"[{__import__('datetime').datetime.now():%H:%M:%S}] [mtime] 跳过 {step}：输入未变（缓存命中）"


def fingerprint(paths) -> str:
    """输入文件集签名（路径+mtime+size 拼接 hash），用于更严格的缓存判定。"""
    import hashlib
    h = hashlib.md5()
    for p in sorted(str(i) for i in paths):
        try:
            st = os.stat(p)
            h.update(f"{p}|{st.st_mtime}|{st.st_size}|".encode("utf-8"))
        except OSError:
            h.update(f"{p}|missing|".encode("utf-8"))
    return h.hexdigest()
