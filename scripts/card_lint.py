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
  [FAIL] 机器黑话 / 内部字段 / 英文档位不得上卡（白话门禁 Tier-1·2026-09-18）
  [WARN] 行业圈内术语建议白话化（白话门禁 Tier-2·不阻断·防 LLM 拼卡卡死丢卡）
  [WARN] 字面 \\n（反斜杠+n）会被自动修复
  [WARN] 标题含 emoji 圆点（'●'等）违反禁圆点规范
"""
from __future__ import annotations
import argparse, json, re, sys
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# 白话词表单一源（同目录 scripts/card_copy_rules.py）；动态加载 card_lint 时 sys.path 未必含本目录
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# ⛔ 可逆开关：False = 关闭白话门禁（回退 2026-09-18 前行为）
ENABLE_PLAIN_GATE = True
# ⛔ 是否把 Tier-2（行业术语）也升为阻断。默认 False —— LLM 拼的复盘卡若被硬阻断
#    会整卡推不出去（丢卡代价 >> 措辞瑕疵）。如需强制干净改 True。
PLAIN_GATE_STRICT = False
# ⛔ Tier-1 阻断**生效日**（首发观察期·2026-09-18 立）。
#    理由：门禁首次上线时，脚本卡实测有命中（核按钮/回流卡 `MA5`·已修源头），
#    但 **LLM 现场拼的卡（猎手/复盘/龙虎榜/机构研报）无法穷举** → 若首日即硬阻断，
#    一旦命中就是**整卡推不出去=丢卡**（远重于措辞瑕疵）。
#    故观察期内 Tier-1 也只 WARN（照样落 stdout/audit，可收集真实命中），到期自动升级阻断。
#    回退/提前：改此常量即可（置 "1970-01-01" 立即阻断）。
PLAIN_GATE_ENFORCE_FROM = "2026-09-21"

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


def _gate_enforcing() -> bool:
    """Tier-1 是否已进入**阻断期**（观察期内只 WARN·见 PLAIN_GATE_ENFORCE_FROM）。

    解析失败按"已生效"处理（宁可提示也不要静默放行）。
    """
    try:
        return date.today() >= date.fromisoformat(PLAIN_GATE_ENFORCE_FROM)
    except Exception:
        return True


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

    # 2.6) 白话门禁（2026-09-18）：词表单一源 = scripts/card_copy_rules.py
    #      背景：禁项词表曾散落 5 处（tests 4 套 + card_quality_check 1 套）且互不一致，
    #      而 **LLM 拼的复盘卡长期无任何词表约束**（09-17 实测残留 研判/容量核心/梯队/
    #      承接/亢奋/退潮 18 处）。现收口为推送前硬门禁，覆盖全部卡。
    #      Tier-1 FAIL 阻断（机器黑话/内部字段）；Tier-2 WARN 提示（行业术语·不阻断）。
    if ENABLE_PLAIN_GATE:
        try:
            import card_copy_rules as _ccr
            _enforce = _gate_enforcing()
            _txt = " ".join(strings)
            _fails, _warns = _ccr.scan(_txt)
            for t in _fails:
                msg = (f"白话门禁·机器黑话不得上卡：{_ccr.describe(t)}"
                       "（宪法【🗣️ 卡片话术铁律】）")
                if _enforce:
                    errors.append({"level": "FAIL", "msg": msg})
                else:
                    warns.append({"level": "WARN",
                                  "msg": f"{msg}（观察期·{PLAIN_GATE_ENFORCE_FROM} 起阻断）"})
            for t in _warns:
                msg = f"白话门禁·行业术语建议白话化：{_ccr.describe(t)}"
                if PLAIN_GATE_STRICT:
                    errors.append({"level": "FAIL", "msg": msg})
                else:
                    warns.append({"level": "WARN", "msg": msg})
        except Exception as e:      # 词表缺失/异常不得阻断推送
            warns.append({"level": "WARN", "msg": f"白话门禁执行异常（不阻断）: {e}"})

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
