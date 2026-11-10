#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能判据副本守卫（2026-11-09 建立 · 棘轮机制）

## 为什么需要它

`config/skill_contract.md` 定了铁律：「参数取源码或配置，**不在技能复制第二套参数表**」。
但实测 2026-11-09：`5%` 在 `shared_rules.md` 196 次、`龙头战法交易员` 96 次、`财神-选股` 46 次；
`晋级率` 79 / 51 / 21；收益口径数字（+61.05% / +25.22%）也在两处各写一份。
**阈值一旦有 3–4 份副本，改一处必漏一处**，而且不会报错——只会某天两个页面说法不一致。

## 它怎么工作（棘轮，不是一刀切）

一次性把 96 次砍到 0 会动 31k tokens 正文，风险大、也没必要。
所以先**冻住现状**：记录每个技能里这些「唯一源词」的出现次数作为基线，
以后**只允许减少，不允许增加**。要增加必须显式 `--update` 重写基线——
那一步会进 git，谁加的、为什么加，留痕。

    python3 scripts/check_skill_drift.py            # 体检：有增长 → exit 1
    python3 scripts/check_skill_drift.py --update   # 重写基线（人工确认后）
    python3 scripts/check_skill_drift.py --json

退出码：0=无增长（含下降），1=有技能新增了副本，2=技能目录不可用。

## 口径

- 只扫**我们自己**维护的技能（`财神-*` / `龙头战法*` / `狸猫终端`），第三方技能不代管；
- 只数 `SKILL.md` 正文，`references/` 下的参考文档不计（那是资料，不是规则入口）；
- 词表见 `config/skill_drift_baseline.json` 的 `tokens`。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "config" / "skill_drift_baseline.json"

# 唯一源词的默认词表（首次生成基线用；之后以基线文件内的 tokens 为准）
DEFAULT_TOKENS = ["5%", "晋级率", "09:25", "断板", "净值", "收益口径"]
DEFAULT_SCOPE = ("财神-", "龙头战法", "狸猫终端")


def skills_dir() -> Path:
    env = os.environ.get("LIMAO_SKILLS_DIR")
    return Path(env) if env else Path.home() / ".workbuddy" / "skills"


def scan(base: Path, scope: tuple[str, ...], tokens: list[str]) -> dict[str, dict[str, int]]:
    """返回 {技能名: {词: 次数}}；只统计 scope 命中且有 SKILL.md 的目录。"""
    out: dict[str, dict[str, int]] = {}
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir() or not any(d.name.startswith(p) for p in scope):
            continue
        f = d / "SKILL.md"
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        out[d.name] = {t: text.count(t) for t in tokens}
    return out


def diff(baseline: dict[str, dict[str, int]], current: dict[str, dict[str, int]]):
    """比较基线：increased=副本增加（要拦），decreased=清理成果（可更新基线）。"""
    increased, decreased = [], []
    for skill, counts in current.items():
        for token, n in counts.items():
            was = (baseline.get(skill) or {}).get(token)
            if was is None:                     # 新技能/新词：先记录，不算违规
                decreased.append((skill, token, None, n))
            elif n > was:
                increased.append((skill, token, was, n))
            elif n < was:
                decreased.append((skill, token, was, n))
    return increased, decreased


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def build_baseline(base: Path, tokens: list[str], scope: tuple[str, ...]) -> dict:
    return {
        "schema": "skill_drift_baseline_v1",
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "note": ("技能正文「唯一源词」词频基线（棘轮：只许降不许升）。"
                 "阈值唯一源 = config/shared_rules.md；要增加副本必须 --update 并说明理由。"),
        "scope": list(scope),
        "tokens": tokens,
        "counts": scan(base, scope, tokens),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="用当前词频重写基线")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--skills-dir", default=None, help="覆盖技能目录（默认 ~/.workbuddy/skills）")
    a = ap.parse_args()

    base = Path(a.skills_dir) if a.skills_dir else skills_dir()
    old = _load(BASELINE)
    tokens = old.get("tokens") or DEFAULT_TOKENS
    scope = tuple(old.get("scope") or DEFAULT_SCOPE)

    if a.update:
        data = build_baseline(base, tokens, scope)
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"基线已更新：{BASELINE.relative_to(ROOT)}（{len(data['counts'])} 个技能 × {len(tokens)} 个词）")
        return 0

    if not base.exists():
        print(f"[skill-drift] 技能目录不可用：{base}（跳过）")
        return 2

    current = scan(base, scope, tokens)
    increased, decreased = diff(old.get("counts") or {}, current)
    result = {
        "skills_dir": str(base),
        "tokens": tokens,
        "increased": [{"skill": s, "token": t, "baseline": b, "now": n} for s, t, b, n in increased],
        "decreased": [{"skill": s, "token": t, "baseline": b, "now": n} for s, t, b, n in decreased],
        "counts": current,
    }
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        total = sum(sum(c.values()) for c in current.values())
        print(f"[skill-drift] {len(current)} 个技能 · {len(tokens)} 个唯一源词 · 副本合计 {total} 次")
        for s in sorted(current):
            hot = {t: n for t, n in current[s].items() if n >= 10}
            if hot:
                print("  " + s.ljust(16) + "  " + "  ".join(f"{t}={n}" for t, n in hot.items()))
        if increased:
            print("\n⛔ 新增副本（唯一源应为 config/shared_rules.md）：")
            for s, t, b, n in increased:
                print(f"   {s} · {t}: {b} → {n}")
            print("\n处置：改回引用 shared_rules，或人工确认后 --update 并说明理由。")
        if decreased:
            print("\n✅ 已清理（可 --update 收敛基线）：")
            for s, t, b, n in decreased[:8]:
                print(f"   {s} · {t}: {b if b is not None else '新增记录'} → {n}")
            if len(decreased) > 8:
                print(f"   … 另有 {len(decreased) - 8} 项")
    return 1 if increased else 0


if __name__ == "__main__":
    raise SystemExit(main())
