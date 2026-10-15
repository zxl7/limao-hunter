#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡片标签嵌套 · **源码级**回归锁（2026-10-14 立）。

背景：`card_lint` 只能拦「已经拼出来的卡」——而这次（2026-10-14）出问题的写法是
**间接嵌套**：`build_cards.py` 里字面上是

    f"　　<font color='grey'>…亿{_vol_weak_flag(oc)}｜…</font>"

`<text_tag>` 藏在 `_vol_weak_flag()` 的**返回值**里，肉眼与「查字面量」都看不见。
一旦 `card_lint` 把该类嵌套升为 FAIL，这种写法就等于**每日竞价卡整卡推不出去 = 丢卡**。

故本测试把「**任何返回 `text_tag` 的辅助函数，都不得出现在 `<font>…</font>` 之间**」
锁到源码层：既查字面量，也查**间接调用**。

⚠️ 扫描前会**空白化注释与文档字符串**——否则规范条文本身的举例（如
`shared_rules`/`card_lint` 的说明文字里就写着 `<font>…<text_tag>…`）会被误判成违规。

对应规范：`config/shared_rules.md` ⓪-10（禁 text_tag/font 嵌套）。
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import card_lint as L  # noqa: E402

# 扫描范围：卡片/推送相关的生产源码（不含测试自身、归档退役脚本、事故前快照）
SCAN_DIRS = ("scripts", "wealth_code", "common")
SKIP_PARTS = (".git", "node_modules", "架构/退役脚本", "cache/backups", ".pytest_tmp")

FONT_SPAN_RE = re.compile(r"<font\b[^>]*>(.*?)</font>", re.S | re.I)
DEF_RE = re.compile(r"^def\s+(_?[A-Za-z_][\w]*)\s*\(", re.M)


def _blank_comments_and_docstrings(text: str) -> str:
    """把注释与文档字符串替换成等长空格（保留换行与列位置）。"""
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)

    spans: list[tuple[int, int, int, int]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            v = body[0].value
            if isinstance(v, (ast.Constant, ast.JoinedStr)) and isinstance(
                    getattr(v, "lineno", None), int):
                spans.append((v.lineno, v.col_offset, v.end_lineno, v.end_col_offset))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                spans.append((tok.start[0], tok.start[1], tok.end[0], tok.end[1]))
    except Exception:
        pass

    chars = list(text)
    for sl, sc, el, ec in spans:
        if not (1 <= sl <= len(offsets) and 1 <= el <= len(offsets)):
            continue
        start, end = offsets[sl - 1] + sc, offsets[el - 1] + ec
        for i in range(start, min(end, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _iter_py():
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if any(s in rel for s in SKIP_PARTS):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            yield rel, _blank_comments_and_docstrings(text)


def _tag_returning_funcs() -> set[str]:
    """收集「函数体中出现 <text_tag」的函数名（= 返回值可能带胶囊的辅助函数）。"""
    names: set[str] = set()
    for _rel, text in _iter_py():
        marks = list(DEF_RE.finditer(text))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            if "<text_tag" in text[m.end():end]:
                names.add(m.group(1))
    return names


def _call_re(name: str) -> re.Pattern:
    # 词边界：`_tag(` 不得命中 `_theme_dual_tag(`（前一个字符是标识符字符）
    return re.compile(r"(?<![\w.])" + re.escape(name) + r"\s*\(", re.I)


def test_sanity_tag_funcs_detected():
    """自检：至少要能识别出已知的胶囊辅助函数，否则扫描逻辑本身失效。"""
    names = _tag_returning_funcs()
    for expect in ("_vol_weak_flag", "_gap_tag", "_pct_tag"):
        assert expect in names, f"未识别出 {expect}，源码扫描逻辑需修（{sorted(names)[:8]}…）"


def test_no_font_wraps_text_tag_source():
    """生产源码中不得出现 `<font>…<text_tag>…</font>`（字面量）。"""
    offenders = []
    for rel, text in _iter_py():
        for m in FONT_SPAN_RE.finditer(text):
            if "<text_tag" in m.group(1):
                offenders.append(f"{rel}:{text[:m.start()].count(chr(10)) + 1}")
    assert offenders == [], "font 包裹 text_tag（宪法 ⓪-10 禁）:\n" + "\n".join(offenders)


def test_no_font_wraps_tag_helper_call_source():
    """⛔ 核心锁：`<font>…</font>` 内不得调用任何「返回 text_tag 的函数」（间接嵌套）。"""
    call_res = {fn: _call_re(fn) for fn in sorted(_tag_returning_funcs())}
    offenders = []
    for rel, text in _iter_py():
        for m in FONT_SPAN_RE.finditer(text):
            inner = m.group(1)
            if "<text_tag" in inner:
                continue                      # 字面量情形已由上一测试覆盖
            for fn, rx in call_res.items():
                if rx.search(inner):
                    ln = text[:m.start()].count("\n") + 1
                    offenders.append(f"{rel}:{ln} → 调用了 {fn}()")
                    break
    assert offenders == [], (
        "font 内调用了返回 text_tag 的辅助函数（card_lint 已升 FAIL·会整卡推不出去）:\n"
        + "\n".join(offenders))


def test_no_font_inside_font_source():
    """`<font>` 套 `<font>` 同属 ⓪-10 禁嵌套（历史 `ai_tag` 曾产出，2026-08 已置空）。"""
    offenders = []
    for rel, text in _iter_py():
        for m in FONT_SPAN_RE.finditer(text):
            if "<font" in m.group(1).lower():
                offenders.append(f"{rel}:{text[:m.start()].count(chr(10)) + 1}")
    assert offenders == [], "font 套 font（宪法 ⓪-10 禁）:\n" + "\n".join(offenders)


def test_vol_weak_flag_calls_are_outside_font():
    """定点锁：verify 卡 4 处 `_vol_weak_flag(oc)` 必须贴在 `</font>` 之后。"""
    src = _blank_comments_and_docstrings(
        (ROOT / "wealth_code/pipelines/build_cards.py").read_text(encoding="utf-8"))
    assert "_vol_weak_flag(oc)" in src
    for m in FONT_SPAN_RE.finditer(src):
        assert "_vol_weak_flag(" not in m.group(1), \
            f"build_cards.py:{src[:m.start()].count(chr(10)) + 1} font 内仍含 _vol_weak_flag"


def test_scanner_ignores_docstring_examples():
    """反向锁：规范条文里的举例（文档字符串/注释）不得被算成违规。"""
    sample = (
        '"""说明文字：禁 <font color=\'grey\'>…<text_tag color=\'blue\'>缩量</text_tag>…</font>。"""\n'
        "# 注释里也写 <font color='grey'>x<text_tag>y</text_tag></font>\n"
        "VALUE = 1\n"
    )
    cleaned = _blank_comments_and_docstrings(sample)
    assert "<font" not in cleaned and "<text_tag" not in cleaned
    assert "VALUE = 1" in cleaned


def test_nest_level_constant_is_fail():
    """门禁本身：嵌套等级常量必须是 FAIL（⛔ 回退观察期改此常量，勿删检查）。"""
    assert getattr(L, "NEST_LEVEL", None) == "FAIL"


def test_lint_rejects_indirect_nesting():
    """把 build_cards 的「旧写法」还原成一张卡 → card_lint 必须 FAIL。"""
    old_line = ("　　<font color='grey'>预计0.0~1.2%高开 预期可信51%｜额0.33/0.46亿"
                " <text_tag color='blue'>缩量</text_tag>｜观察池｜🔥 机器人｜🏷 数字媒体</font>")
    card = {"msg_type": "interactive", "card": {
        "config": {"wide_screen_mode": True},
        "header": {"template": "red",
                   "title": {"tag": "plain_text",
                             "content": "⚡ 财富密码·竞价验证·增量 | 测试 | 涨停1"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": old_line}}]}}
    ok, errs, _ = L.lint_card(card)
    assert not ok
    assert any("嵌套" in e["msg"] for e in errs)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
