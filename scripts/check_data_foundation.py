#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据基座自检（工件 E 配套）：单一参考文档的运行时校验。

检查项：
  1. data_catalog 与 serve.py PUBLIC_ROUTES 一致性（verify_against）
  2. 每个非 retired 路由：产物文件是否存在、是否超过 stale 阈值
  3. 输出汇总报告；仅当「一致性差异」存在时退出码非 0（缺失/超期为软告警，
     因盘中未到时点或非交易时段属正常，盘后应归零）。

用法：python3 scripts/check_data_foundation.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import serve  # noqa: E402  (serve 自身会把 REPO 加入 sys.path)
import common.data_catalog as catalog  # noqa: E402


def main() -> int:
    print("=== 数据基座自检 (check_data_foundation) ===\n")

    # 1) 一致性（硬校验）
    diffs = catalog.verify_against(serve.PUBLIC_ROUTES)
    print(f"[1/2] data_catalog ↔ serve.PUBLIC_ROUTES 一致性：{len(diffs)} 处差异")
    for d in diffs:
        print("   ❌", d)
    ok = not diffs

    # 2) 产物存在性 / 新鲜度（软告警）
    print("\n[2/2] 路由产物新鲜度扫描：")
    missing, stale, retired_count = [], [], 0
    for route, defn in catalog.ROUTES.items():
        fr = serve.route_freshness(route, defn)
        if fr.get("retired"):
            retired_count += 1
            continue
        if not fr.get("exists"):
            missing.append(route)
            continue
        if fr.get("stale") is True:
            stale.append((route, fr.get("age_sec"), fr.get("stale_sec")))

    if missing:
        print(f"   ⚠️ 缺失产物（{len(missing)}）：")
        for r in missing:
            print("      -", r)
    if stale:
        print(f"   ⚠️ 超期产物（{len(stale)}）：")
        for r, age, sec in stale:
            print(f"      - {r}  age={age}s 阈值={sec}s")

    healthy = len(catalog.ROUTES) - retired_count - len(missing) - len(stale)
    print(
        f"\n汇总：登记 {len(catalog.ROUTES)} 路由 | 健康 {healthy} |"
        f" 停更 {retired_count} | 缺失 {len(missing)} | 超期 {len(stale)}"
    )
    if missing or stale:
        print("⚠️ 存在缺失/超期产物（多为盘中未到时点或非交易时段，属正常；盘后应为 0）")

    print("\nRESULT:", "PASS ✅" if ok else "FAIL ❌")
    # 仅一致性差异为硬失败；缺失/超期为软告警不阻断 CI
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
