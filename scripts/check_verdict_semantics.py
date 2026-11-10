#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语义守卫：禁止用中文文案做逻辑判断（2026-11-10 抽离后加的锁）

## 为什么是「禁止」而不是「建议」

实测：改一个词要动四处，其中两处是**子串判断**
（`"低于预期" in verdict` 决定颜色、`"符合预期" in v` 决定是否摘除）。
把文案改成「弱于预期」，这两处**不会报错**，只是静默失效——颜色变红、✅ 不摘。
所以这一层必须有机器看守，靠人记不住。

## 判据

业务代码（`wealth_code/`、`scripts/`，排除 `core/verdict.py` 与测试）里，
出现「中文预期词 + `in`」的写法即判违规。

    python3 scripts/check_verdict_semantics.py        # 0=干净，1=有违规
    python3 scripts/check_verdict_semantics.py --json

## 正确写法

    from core.verdict import Verdict, is_negative, is_ok, tag
    if is_negative(text): ...        # ✅ 语义判断
    label = tag(Verdict.BELOW)       # ✅ 文案只在单一源取
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("wealth_code", "scripts")
EXCLUDE = {"verdict.py", "check_verdict_semantics.py"}
# ⛔ 中文预期词 + in → 把文案当协议
PATTERNS = (
    '"低于预期" in', '"符合预期" in', '"超预期" in',
    "'低于预期' in", "'符合预期' in", "'超预期' in",
    '"低于" not in', "'低于' not in",
)


def scan() -> list[dict]:
    hits: list[dict] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if p.name in EXCLUDE or "tests" in p.parts:
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if any(pat in line for pat in PATTERNS):
                    hits.append({"file": str(p.relative_to(ROOT)), "line": i,
                                 "code": line.strip()[:120]})
    return hits


def main() -> int:
    hits = scan()
    if "--json" in sys.argv:
        print(json.dumps({"hits": hits}, ensure_ascii=False, indent=1))
    elif hits:
        print(f"⛔ 发现 {len(hits)} 处「中文文案当逻辑」：")
        for h in hits:
            print(f"  {h['file']}:{h['line']}  {h['code']}")
        print("\n改法：用 core.verdict.is_negative / is_ok / tag，不要用子串。")
    else:
        print("✓ 语义守卫通过：业务代码没有用中文文案做逻辑判断")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
