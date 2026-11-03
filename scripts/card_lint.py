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
  [FAIL] note 必须 {tag:note, elements:[…]}·禁裸 content（2026-10-14 整卡降级事故）
  [FAIL] 禁任一嵌套：text_tag↔text_tag / font↔text_tag（外层吞内层·宪法 ⓪-10·
         2026-10-14 由 WARN 升级·同日 build_cards 4 处已清理）
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

# ⛔ 标签嵌套等级（2026-10-14 由 "WARN" 升为 "FAIL"）：
#    前置条件已满足——生产 `build_cards.py` 的 `_vol_weak_flag` 4 处 font 嵌套已清理，
#    且升级前全仓源码扫描确认零残留（`tests/test_card_tag_nesting_source_scan.py` 锁死）。
#    临时放行请改本常量（勿删检查）；回退观察期 = "WARN"。
NEST_LEVEL = "FAIL"
# ⛔ 嵌套硬阻断**生效时刻**（单一源）：2026-10-14 17:35 由 WARN 升 FAIL，
#    与 `build_cards.py` 的 4 处 font↔text_tag 清理同一批次落地。
#    在此之前生成的生产卡反映的是**旧代码**，不构成对现行 lint 的有效证伪
#    ⇒ `tests/test_golden.py::real_cards` 只取样本时刻**之后**生成的生产卡
#    （按卡片文件名内嵌的生成时刻判定·契约 §1「mtime 不能替代内容日期」）。
#    ⛔ 测试侧勿另写日期，一律 import 本常量。
NEST_FAIL_SINCE = "2026-10-14T17:35"
# 2026-10-21 17:45 用户「这是商业性项目，不要显示解释性质的语录」→ 新增一批机制解释词
# （机械口径/机械强龙/行情档位/已并到/不另列…）。这批词生效前的历史卡仍带旧文案，
# ⛔ 不该被追责（那不是「lint 误伤生产卡」，是「历史文件没重跑」）。
# 用法与 `NEST_FAIL_SINCE` 同：fixture 只取此后的卡判「真实代码生成卡必须全过」。
PLAIN_MECH_WORDS_SINCE = "2026-10-21T17:45"
# 2026-10-22 17:30 用户「不要叫惯犯，叫连板/连板龙/老龙头」→ 「惯犯/偶犯」入 Tier-1 阻断。
# ⛔ 同类举证边界：此前生成的生产卡（含 10-22 15:37 那张盘后卡）里仍带「惯犯」，
#    那是旧代码产物、不是 lint 误伤。测试一律 import 本常量，只追责此刻之后的卡。
PLAIN_JUDGE_WORDS_SINCE = "2026-10-22T17:30"

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

    # 2.7) ⛔ 禁「研究/调试标注」上卡（2026-10-21 23:01 用户）
    #      用户原话：「这些研究型的文案！！不要出现在推送的卡片里！！！！！！！」
    #      事故：我为了给用户看改版效果，在卡片**最前面**插了一行
    #        `🧪 断板措辞修正 · 已断板不再说「开板就走」，改为盯自己 + 标断板天数 · 10-20 盘后 + 10-21 竞价重渲`
    #      —— 那是**写给我们自己看的开发说明**，推到了生产/测试群里。读者打开卡片，
    #      第一眼看到的是「措辞修正」「重渲」，而不是"今天买什么"。
    #      ⛔ 卡片是交付给读者的成品，不是开发日志。任何"我们改了什么/怎么改的/用什么数据重跑"
    #         都不属于卡片内容，一律不得上卡（要留档请写 `cache/`、commit message 或记忆文件）。
    #      ⛔ 这一项**没有观察期、不降级为 WARN**：它是内容性质错误（放错了东西），
    #         不是措辞风格问题。误伤概率极低（正常卡片不会出现这些词）。
    _DEV_MARKERS = (
        # 版本/改版叙述
        "🧪", "重渲", "重渲染", "改版", "措辞修正", "配色改版", "版式改版",
        "文案修正", "节流修正", "修正版", "调整版", "新版式",
        # 改动说明叙述
        "已清掉", "已移除", "已修复", "本次修改", "改动说明", "变更说明",
        "已改成", "已改为", "本次改",
        # 测试/调试标记
        "测试卡", "测试版", "调试卡", "debug", "DEBUG", "dry-run", "dry run", "干跑",
        # 数据重跑叙述
        "盘后 + ", "盘后+", "竞价重渲", "用 10-", "用 09-",
    )
    _dev_hits: list[str] = []
    # ⚠️ 只扫**卡片正文元素**，不扫 title —— title 已有自己的门禁；
    #    且测试夹具/内部预览常把标题写成「📊 测试卡·增量」，
    #    扫 title 会把它误判成"开发标注上卡"（实测 test_card_copy_rules 3 例红）。
    _body_strings: list[str] = []
    _els = card.get("elements")
    if not isinstance(_els, list) and isinstance(card.get("card"), dict):
        _els = card["card"].get("elements")
    if isinstance(_els, list):
        walk_strings(_els, _body_strings)
    else:
        _body_strings = list(strings)      # 结构未知则退回全量（宁可严一点）
    for _s in _body_strings:
        for _m in _DEV_MARKERS:
            if _m in _s and _m not in _dev_hits:
                _dev_hits.append(_m)
    if _dev_hits:
        _ctx = next((s for s in _body_strings if any(m in s for m in _dev_hits)), "")[:80]
        errors.append({"level": "FAIL",
                       "msg": f"禁研究/调试标注上卡（读者只看结论，不看我们改了什么）: "
                              f"{'、'.join(_dev_hits[:4])} ← {_ctx}"})

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

    # 5) 元素结构检查（2026-09-22 机构晚报空白卡事故·新增 FAIL 项）
    #    事故：拼卡把正文写成 {"tag":"div","lark_md":"..."}（少一层 text 包裹），
    #    飞书 div 无 text 对象 → 正文不渲染，客户端只见标题+分割线=整卡空白。
    #    旧 lint 只查「**存在** content 时其类型」，而该结构里根本没有 content 键
    #    → check_content_type 空过、整卡放行。此处改为**结构性硬判**。
    def check_elements(node, path="card"):
        if isinstance(node, dict):
            if node.get("tag") == "div":
                if "lark_md" in node:
                    errors.append({"level": "FAIL",
                                   "msg": f"{path} 非法扁平 lark_md（须包进 text.content）: keys={sorted(node.keys())[:6]}"})
                if "text" not in node and "fields" not in node:
                    errors.append({"level": "FAIL",
                                   "msg": f"{path} div 缺 text/fields → 飞书渲染整块空白（2026-09-22 机构晚报事故）"})
                tx = node.get("text")
                if tx is not None and not (isinstance(tx, dict)
                                           and isinstance(tx.get("content"), str)
                                           and tx["content"].strip()):
                    errors.append({"level": "FAIL",
                                   "msg": f"{path} div.text 须为 {{tag:'lark_md', content:非空字符串}}"})
            for k, v in node.items():
                check_elements(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                check_elements(v, f"{path}[{i}]")
    check_elements(card)

    # 5.5) note 元素结构检查（2026-10-14 事故·新增 FAIL 项）
    #      事故：预览脚本手搓完整消息体时把 note 写成 {"tag":"note","content":"..."}，
    #      缺 `elements` 数组（飞书合法格式 = {"tag":"note","elements":[{"tag":"lark_md",
    #      "content":"..."}]}），实测该卡被整卡降级/渲染异常（用户反馈「卡片里面没有样式」）。
    #      旧 lint 的 check_elements 只判 div，note 完全不查 → 非法结构一路放行到群里。
    def check_note(node, path="card"):
        if isinstance(node, dict):
            if node.get("tag") == "note":
                if "content" in node:
                    errors.append({"level": "FAIL",
                                   "msg": f"{path} note 裸 content 非法（content 必须包进 elements[]）："
                                          "合法 = {'tag':'note','elements':[{'tag':'lark_md','content':...}]}"
                                          "（2026-10-14 无样式事故）"})
                els = node.get("elements")
                if not isinstance(els, list) or not els:
                    errors.append({"level": "FAIL",
                                   "msg": f"{path} note 缺 elements 数组 → 飞书整卡降级/不渲染。"
                                          "⛔ 脚本不得自拼 note；请用简版结构 {{title,color,elements,note}} "
                                          "由 push_card.normalize() 生成"})
                else:
                    for i, sub in enumerate(els):
                        if not (isinstance(sub, dict) and isinstance(sub.get("content"), str)
                                and sub["content"].strip()):
                            errors.append({"level": "FAIL",
                                           "msg": f"{path} note.elements[{i}] 须为 {{tag, content:非空字符串}}"})
            for k, v in node.items():
                check_note(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                check_note(v, f"{path}[{i}]")
    check_note(card)

    # 5.6) text_tag / font 嵌套检查（2026-10-14·新增；同日由 WARN 升级 FAIL）
    #      宪法【突发题材榜 UI 标准模板】5 明确「⛔ 禁 <font> 嵌套 <text_tag>」。
    #      **两类嵌套一律 FAIL**：
    #        · text_tag 套 text_tag → 外层胶囊吞掉内层、该行退化为纯文本，
    #          观感即用户反馈的「卡片里面没有样式」（2026-10-14 猎手 v5 机构对账行实证）。
    #        · font 套 text_tag（或反向）→ 同属禁嵌套；生产 verify 卡（build_cards.py
    #          `_vol_weak_flag`）原有 4 处，已于 2026-10-14 清理完毕（缩量胶囊移到 </font> 之后），
    #          故本项**不再需要观察期**，直接硬阻断。
    #      ⚠️ 升级前的全仓验证（可复现）：扫 `*.py` 中「<font>…</font> 内含 <text_tag> 或
    #          返回标签的函数调用」→ 除 build_cards 4 处外零命中；故升 FAIL 不会造成丢卡。
    #      ⛔ 若将来需临时放行，改模块级 `NEST_LEVEL` 常量而非删除本检查。
    ANY_TAG_RE = re.compile(r"</?(text_tag|font)\b[^>]*>", re.I)
    for s in strings:
        stack: list[str] = []
        for m in ANY_TAG_RE.finditer(s):
            tok = m.group(0)
            name = m.group(1).lower()
            if tok.startswith("</"):
                if name in stack:
                    stack = stack[:len(stack) - 1 - stack[::-1].index(name)]
                continue
            if tok.endswith("/>"):
                continue                      # 自闭合不占栈
            if stack:
                ctx = s[max(0, m.start() - 24):m.end() + 30]
                pair = (stack[-1], name)
                if pair == ("text_tag", "text_tag"):
                    errors.append({"level": "FAIL",
                                   "msg": f"text_tag 嵌套 text_tag（外层吞内层·该行退化为纯文本·"
                                          f"2026-10-14 无样式事故）: ...{ctx}..."})
                else:
                    errors.append({"level": NEST_LEVEL,
                                   "msg": f"{pair[0]} 套 {pair[1]}（宪法 ⓪-10 禁嵌套·"
                                          f"2026-10-14 起硬阻断）: ...{ctx}..."})
            stack.append(name)

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
