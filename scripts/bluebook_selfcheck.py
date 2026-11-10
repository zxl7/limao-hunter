#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蓝宝书卡片·审阅自检（第②段 skin 审阅优化的收尾校验）。

抽取卡片全部文本，跑卡片话术门禁 `card_copy_rules.scan`：
  · FAIL 非空 → 存在机器黑话/内部字段 → 退出码 1（阻断推送，须改措辞后重跑）
  · WARN 仅提示行业术语可白话化，不阻断

❗ 本脚本只读、不改卡：只做"是否还有黑话"的判定。
   "优化"（英文行话→白话、删内部痕迹）由 AI 按 SKILL 规则改卡，再用本脚本验收。

用法：
  python scripts/bluebook_selfcheck.py [card.json]   # 默认 cache/bluebook_card_today.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from card_copy_rules import scan  # noqa: E402


def main() -> None:
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "cache" / "bluebook_card_today.json"
    if not p.exists():
        raise SystemExit(f"卡片不存在：{p}")
    card = json.loads(p.read_text(encoding="utf-8"))

    texts: list[str] = []

    def walk(o) -> None:  # noqa: ANN001
        if isinstance(o, str):
            texts.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(card)
    fails, warns = scan("\n".join(texts))
    print("FAIL:", fails)
    print("WARN:", warns)
    if fails:
        print("审阅未通过：存在机器黑话/内部字段，须改措辞（不改事实）后重跑",
              file=sys.stderr)
        sys.exit(1)
    print("审阅通过：无硬性阻断词")


if __name__ == "__main__":
    main()
