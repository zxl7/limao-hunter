#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🩺 技能文档一致性审计（2026-11-12 · 只读，不自动修）

## 为什么有它

`龙头战法交易员/SKILL.md` 过去是追加式文档，多次「覆盖/已撤销」声明与旧章节并存，
已经出现「5% 是硬线」和「DEV 是唯一判据」同时存在的情况。人工读不出来，但脚本能。

## 查什么

1. `SKILL.md`：重复标题、编号碰撞、禁用旧口径词、引用的文件是否存在、数字是否过期；
2. `references/archive_2026-11.md`：每个 H2 是否带 `❄️` 归档标记；
3. 输出 JSON；退出码 0=无 error，1=有 error（warning 不阻断）。

    python3 scripts/skill_doc_audit.py            # 人读
    python3 scripts/skill_doc_audit.py --json     # 机读

⛔ 只读：不改文件、不写 cache、不碰冻结件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = Path.home() / ".workbuddy" / "skills" / "龙头战法交易员"
SKILL = SKILL_DIR / "SKILL.md"
ARCHIVE = SKILL_DIR / "references" / "archive_2026-11.md"

# 旧口径（出现即 error）：这些词一旦回到主文档，说明又被追加回去了
BANNED = (
    "5% 是硬线", "5%是硬线", "超 5%", "超5%", "0<gap≤5%", "0<gap≤5",
    "三档建议", "可等 / 只观察 / 放弃", "可等/只观察/放弃",
    "32 个归档日", "ai_tilt_v2", "成交额分阶段", "回撤只压仓",
    "不可违反的四条",
)
NUMBER_RULES = (
    (re.compile(r"(\d+)\s*个归档日"), "archive_days", lambda: len(list((ROOT / "cache" / "zt_history").glob("zt_*.json")))),
    (re.compile(r"ai_tilt_v(\d+)"), "ai_tilt_version", None),
)


def _headings(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.startswith("#")]


def _num_label(ln: str) -> str | None:
    m = re.match(r"^#{1,3}\s*[^\n]*?(1[⁰¹²³⁴⁵⁶⁷⁸⁹⁺]+[⁾）])", ln)
    return m.group(1) if m else None


def audit() -> dict:
    errors: list[str] = []
    warns: list[str] = []
    info: dict = {}

    if not SKILL.exists():
        return {"errors": [f"缺少 {SKILL}"], "warnings": [], "info": {}}
    text = SKILL.read_text(encoding="utf-8")
    info["lines"] = len(text.splitlines())
    if info["lines"] > 400:
        errors.append(f"SKILL.md 行数 {info['lines']} > 400（主文档又变长了，研究内容应进存档）")

    # 1) 重复标题
    heads = _headings(text)
    dup = {h for h in heads if heads.count(h) > 1}
    if dup:
        errors.append(f"重复标题：{sorted(dup)[:5]}")

    # 2) 编号碰撞（1⁵⁾ 这类）
    labels = [_num_label(h) for h in heads]
    labels = [x for x in labels if x]
    if len(labels) != len(set(labels)):
        errors.append(f"编号碰撞：{sorted({x for x in labels if labels.count(x) > 1})}")

    # 3) 禁用旧口径
    for word in BANNED:
        if word in text:
            errors.append(f"出现旧口径：{word!r}")

    # 4) 引用文件是否存在
    refs = set(re.findall(r"((?:core|scripts|pipelines|common)/[A-Za-z0-9_/]+\.py)", text))
    refs |= set(re.findall(r"(config/[A-Za-z0-9_/]+\.json)", text))
    missing = []
    for r in sorted(refs):
        cands = [ROOT / r, ROOT / "wealth_code" / r]
        if not any(c.exists() for c in cands):
            missing.append(r)
    if missing:
        warns.append(f"引用路径不存在（可能是简写）：{missing}")

    # 5) 数字保鲜
    for pat, key, actual in NUMBER_RULES:
        m = pat.search(text)
        info[key] = m.group(1) if m else None
        if m and actual is not None:
            now = actual()
            info[f"{key}_actual"] = now
            if int(m.group(1)) != int(now):
                warns.append(f"{key} 文档写 {m.group(1)}，实际 {now}（用「取数命令」代替硬编码）")
    if "ai_tilt_version" in info and info["ai_tilt_version"]:
        models = sorted((ROOT / "config" / "models").glob("ai_tilt_v*.json"))
        info["model_files"] = [p.name for p in models]
        if models and f"ai_tilt_v{info['ai_tilt_version']}.json" not in [p.name for p in models]:
            errors.append(f"文档写 ai_tilt_v{info['ai_tilt_version']}，实际只有 {[p.name for p in models]}")

    # 6) 存档标记
    if not ARCHIVE.exists():
        errors.append(f"缺少存档 {ARCHIVE}")
    else:
        bad = [ln.strip() for ln in ARCHIVE.read_text(encoding="utf-8").splitlines()
               if ln.startswith("## ") and not ln.startswith("## ❄️")]
        info["archive_lines"] = len(ARCHIVE.read_text(encoding="utf-8").splitlines())
        if bad:
            errors.append(f"存档有 {len(bad)} 个 H2 没带 ❄️ 标记，例：{bad[:3]}")

    return {"errors": errors, "warnings": warns, "info": info}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="技能文档一致性审计（只读）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = audit()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"[skill_audit] 行数 {res['info'].get('lines')} / 存档 {res['info'].get('archive_lines')}")
        for e in res["errors"]:
            print(f"  ❌ {e}")
        for w in res["warnings"]:
            print(f"  ⚠️ {w}")
        if not res["errors"]:
            print("  ✅ 无 error")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
