#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成财富密码执行审计报告（只读生产链，写入独立审计产物）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WC = ROOT / "wealth_code"
# 统一交易日历位于项目根 common/，独立执行审计脚本须显式保留该导入根。
for path in (ROOT, WC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from core.execution_audit import AuditPaths, build_execution_audit, risk_register  # noqa: E402


def _markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# 财富密码执行审计",
        "",
        f"主口径：{report['main_metric']['label']}（不含手续费与滑点）。",
        "",
        "## 覆盖度",
        "",
        f"- 批次：{summary['batches']}；候选：{summary['stocks']}；实际入场：{summary['entered']}；有收益：{summary['covered_returns']}",
        f"- 不完整批次：{summary['incomplete_batches']}；机械回退成交：{summary['mechanical_fallback_entries']}；AI预案缺失候选：{summary['ai_plan_missing_candidates']}；冰点 AI 放行：{summary['ice_ai_entries']}",
        "",
        "## 风险登记",
        "",
    ]
    for risk in risk_register(report):
        lines.append(f"- **{risk['level']} · {risk['code']}**：{risk['message']}")
    lines.extend(["", "## 批次完整性", ""])
    for batch in report["batches"]:
        issues = "；".join(batch["integrity"]["issues"]) or "完整"
        lines.append(
            f"- {batch['recommendation_date']} → {batch['trade_date'] or '待证实'}："
            f"候选 {len(batch['stocks'])}，实际入场 {batch['source']['account_entries']}，{issues}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="财富密码执行审计（只读）")
    parser.add_argument("--root", type=Path, default=ROOT, help="狸猫猎手根目录")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON 摘要")
    parser.add_argument("--dry-run", action="store_true", help="只计算并输出，不写审计报告")
    args = parser.parse_args()
    paths = AuditPaths(args.root.resolve())
    report = build_execution_audit(paths)
    out_json = paths.wealth_cache / "execution_audit.json"
    out_md = paths.wealth_cache / "execution_audit.md"
    if not args.dry_run:
        out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out_md.write_text(_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report["summary"], ensure_ascii=False))
    else:
        print("[execution_audit] dry-run，未写审计报告" if args.dry_run
              else f"[execution_audit] 已写 {out_json} / {out_md}")
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
