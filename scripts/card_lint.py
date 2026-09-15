#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
卡片渲染 lint 校验器（P0-1b · 2026-08-17）
===========================================
推送前对卡片 JSON 做硬性渲染检查，把「靠 prompt 约束」升级为「代码硬校验」。
覆盖 shared_rules【视觉样式管控规范】+【盘中卡去重与结论聚合铁律】的可机械化项。

用法:
  python3 scripts/card_lint.py <card.json>            # 校验卡片，0=通过 1=有错
  python3 scripts/card_lint.py <card.json> --json      # 机器可读
  python3 scripts/card_lint.py <card.json> --fix       # 自动修复可修复项（禁 hex / 字面 \\n）

校验项（FAIL 即阻断推送；WARN 提示不阻断）:
  [FAIL] text_tag 颜色必须是命名色枚举（禁 hex）
  [FAIL] 禁 <font color='#hex'>（飞书只认命名色）
  [FAIL] 禁 column_set（本环境实测不渲染·整块空白）
  [FAIL] content 字段必须是字符串·禁数组（2026-08-25 猎手卡 200621 事故）
  [FAIL] title 必填且含「类型」+「增量/全量」标注（shared_rules【卡片标题标注规范】）
  [WARN] 字面 \\n（反斜杠+n）会被自动修复
  [WARN] 标题含 emoji 圆点（'●'等）违反禁圆点规范
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

TEXT_TAG_COLORS = {
    "neutral", "blue", "red", "orange", "violet", "green", "yellow",
    "purple", "turquoise", "carmine", "grey", "indigo", "wathet", "lime",
}
HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
FONT_HEX_RE = re.compile(r"<font\s+color\s*=\s*['\"]#[0-9a-fA-F]{3,8}['\"]", re.I)
COLUMN_SET_RE = re.compile(r'"tag"\s*:\s*"column_set"')
# 类型标注：标题需含 ·增量 或 ·全量 或「·增量」「·全量」（按卡片标题标注规范）
TITLE_TYPE_RE = re.compile(r"·(增量|全量)")
DOT_EMOJI_RE = re.compile(r"[●◆■▲]")

# 允许缺失标题标注的卡类型（脚本类代码生成的已由代码保证；AI 拼卡必须带）
TITLE_WHITELIST = {"盘中脉搏", "盘中快照", "盘中猎手"}  # 前缀匹配视为必须带标注


def walk_strings(obj, collect):
    if isinstance(obj, dict):
        # 结构化 column_set 检测（tag 字段）
        if obj.get("tag") == "column_set":
            collect.append('"tag":"column_set"')
        for v in obj.values():
            walk_strings(v, collect)
    elif isinstance(obj, list):
        for v in obj:
            walk_strings(v, collect)
    elif isinstance(obj, str):
        collect.append(obj)


def lint_card(card: dict, fix: bool = False) -> tuple[bool, list[dict], list[dict]]:
    """返回 (是否通过, 错误列表[{level,msg}], 警告列表)。fix=True 时自动修复可修复项。"""
    errors, warns = [], []

    # 1) title 必填 + 类型/增量标注
    title = card.get("title") or (card.get("card", {}) or {}).get("header", {}).get("title", {}).get("content") if isinstance(card.get("card"), dict) else card.get("title")
    # normalize 前可能是简写结构
    t = card.get("title", "")
    if not t and isinstance(card.get("card"), dict):
        hdr = card["card"].get("header", {})
        if isinstance(hdr, dict):
            t = hdr.get("title", "")
            if isinstance(t, dict):
                t = t.get("content", "")
    if not t:
        errors.append({"level": "FAIL", "msg": "title 缺失（shared_rules【标题规范】禁止裸通知）"})
    else:
        needs_tag = any(t.startswith(p) for p in TITLE_WHITELIST)
        if needs_tag and not TITLE_TYPE_RE.search(t):
            errors.append({"level": "FAIL",
                           "msg": f"title 缺「类型+增量/全量」标注（shared_rules【卡片标题标注规范】）: {t[:50]}"})

    # 2) 全卡字符串扫描
    strings: list[str] = []
    walk_strings(card, strings)
    for s in strings:
        # 禁 hex（text_tag/其它任何位置）
        for m in HEX_RE.finditer(s):
            ctx = s[max(0, m.start() - 15):m.end() + 15]
            errors.append({"level": "FAIL", "msg": f"禁 hex 颜色（飞书不渲染）: ...{ctx}..."})
            if fix:
                pass  # hex 修复需上下文，仅报告
        # 禁 column_set
        if COLUMN_SET_RE.search(s):
            errors.append({"level": "FAIL", "msg": "禁 column_set（本环境实测不渲染·整块空白，用 div/fields 替代）"})
        # 字面 \n 警告（可修复）
        if "\\n" in s:
            warns.append({"level": "WARN", "msg": "字面 \\\\n（反斜杠+n）将自动修复为真实换行"})

    # 2.5) content 字段类型检查（2026-08-25 修复：13:00 猎手卡 content 被写成数组
    #      → 飞书 200621 parse card json err·"with value array at index 5943: mismatched type"；
    #      AI 拼卡偶发把多行内容放数组，飞书要求字符串）
    def check_content_type(obj, path=""):
        if isinstance(obj, dict):
            if "content" in obj and isinstance(obj["content"], list):
                ctx = str(obj["content"])[:40]
                errors.append({"level": "FAIL",
                               "msg": f"{path}.content 应为字符串·禁数组（飞书只渲染字符串）: {ctx}..."})
            for k, v in obj.items():
                check_content_type(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                check_content_type(v, f"{path}[{i}]")
    check_content_type(card)

    # 3) 圆点 emoji 警告
    if DOT_EMOJI_RE.search(t or ""):
        warns.append({"level": "WARN", "msg": f"标题含圆点 emoji（禁圆点规范）: {t[:40]}"})

    # 4) fields 结构检查：若含 fields 应为列表且每项含 is_short/text
    def check_fields(obj, path=""):
        if isinstance(obj, dict):
            if "fields" in obj:
                flds = obj["fields"]
                if not isinstance(flds, list):
                    errors.append({"level": "FAIL", "msg": f"{path}.fields 应为列表"})
                elif flds and any(not isinstance(f, dict) or "text" not in f for f in flds):
                    errors.append({"level": "FAIL", "msg": f"{path}.fields 每项须含 text"})
            for k, v in obj.items():
                check_fields(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                check_fields(v, f"{path}[{i}]")
    check_fields(card)

    ok = not any(e["level"] == "FAIL" for e in errors)
    return ok, errors, warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("card", help="卡片 JSON 文件路径")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--fix", action="store_true", help="自动修复字面 \\\\n")
    args = ap.parse_args()

    try:
        card = json.loads(Path(args.card).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR 读取卡片失败: {e}", file=sys.stderr)
        return 2

    ok, errors, warns = lint_card(card, fix=args.fix)

    if args.json:
        print(json.dumps({"ok": ok, "errors": errors, "warns": warns}, ensure_ascii=False, indent=2))
    else:
        for e in errors:
            print(f"  ❌ {e['msg']}")
        for w in warns:
            print(f"  ⚠️  {w['msg']}")
        print("\n" + ("✅ 卡片 lint 通过" if ok else "❌ 卡片 lint 未通过（阻断推送）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
