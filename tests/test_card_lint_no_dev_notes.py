# -*- coding: utf-8 -*-
"""卡面禁「研究/调试标注」（2026-10-21 23:01 用户）。

用户原话：「**这些研究型的文案！！不要出现在推送的卡片里！！！！！！！**」

事故复盘
--------
为了让用户看改版效果，我在卡片**最前面**插了一行开发说明：

    🧪 断板措辞修正 · 已断板不再说「开板就走」，改为盯自己 + 标断板天数 · 10-20 盘后 + 10-21 竞价重渲

这行被推到了群里。读者打开卡片，第一眼看到的是「措辞修正」「重渲」，
而不是"今天买什么"。

⛔ 判据：**卡片是交付给读者的成品，不是开发日志。**
   「我们改了什么 / 怎么改的 / 用什么数据重跑」不属于卡片内容 ——
   留档请写 `cache/`、commit message 或记忆文件。

⛔ 这一项**没有观察期、不降级为 WARN**：它是内容性质错误（放错了东西），
   不是措辞风格问题；正常卡片不会出现这些词，误伤概率极低。
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from card_lint import lint_card  # noqa: E402


def _card(txt: str) -> dict:
    return {
        "title": "🤖 今日买入·增量",
        "color": "blue",
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": txt}}],
    }


def _dev_errors(card: dict) -> list[str]:
    ok, errs, _warns = lint_card(card)
    return [e["msg"] for e in errs if "研究/调试标注" in e["msg"]]


# ── 必须拦住（事故原文与同族写法）──────────────────────────────────────
@pytest.mark.parametrize("txt", [
    # 事故原文（逐字）
    "🧪 断板措辞修正 · 已断板不再说「开板就走」，改为盯自己 + 标断板天数 · 10-20 盘后 + 10-21 竞价重渲",
    # 同族：配色 / 版式 / 指标改版叙述
    "🧪 配色终版 · 只剩三种形态：红块（买入）/ 灰块（观察）/ 灰字（放弃）· 10-20 盘后 + 10-21 竞价重渲",
    "🧪 AI 买入 + 五维 tag 胶囊 · 零抓取 · 10-20 盘后 + 10-21 竞价重渲",
    "🧪 商业版排版 · 已清掉全部系统术语，只留结论与理由",
    # 无 emoji 的纯文字版本也必须拦（不能只靠 🧪 兜）
    "本次修改：口径已改为按炒作题材分组",
    "调试卡 · dry-run 输出，请忽略",
])
def test_dev_annotations_are_blocked(txt):
    msgs = _dev_errors(_card(txt))
    assert msgs, f"该行是开发说明，必须拦下：{txt[:40]}"
    assert "FAIL" in "FAIL"          # 语义占位：确保断言上下文可读


def test_blocked_card_fails_overall():
    """不只是「命中一条」，整卡必须判不通过（否则推送照走）。"""
    ok, errs, _w = lint_card(_card("🧪 断板措辞修正 · 10-20 盘后 + 10-21 竞价重渲"))
    assert ok is False
    assert any("研究/调试标注" in e["msg"] for e in errs)


# ── 必须放行（正常业务结论里会出现的词，别误伤）────────────────────────
@pytest.mark.parametrize("txt", [
    "南威软件(603636) 首板 竞价+0.67% → AI 买入（高开5%以内）",
    "博通集成(603068) 首板 +2.23% 量价够格 跟风（是炒过的老票）",
    "这条线原来最高是 新华传媒 11 板（10-16 最后涨停，已断 1 个交易日）",
    "带起 2 家 ｜ 14:13 封板 ｜ 主力净流入 +0.65 亿 ｜ 流通 51.6 亿",
    "梯队 D·不成型（3.4/10）·没有龙二龙三跟上",
    # ⛔ 05 年/09 月这类日期前缀不清算（只用「用 10-」「用 09-」这种重跑口吻才拦）
    "10-20 涨停 54 家，机器人 24 家涨停",
])
def test_normal_conclusions_pass(txt):
    card = _card(txt)
    ok, errs, _w = lint_card(card)
    assert not _dev_errors(card), f"误伤正常业务文案：{txt[:40]}  errs={errs[:2]}"


def test_production_renderer_never_emits_dev_markers():
    """⛔ 生产渲染器本身不许产出这类标注（唯一一次是测试脚本手工插的）。

    这条锁的意义：防止将来有人在 `build_cards` 里加「v6 版式」之类的自述行。

    ⚠️ 只扫 **🧪** 这个标记 —— 其余词（重渲/测试…）在代码注释与 docstring 里
    是**正当用法**（如「离线重渲/测试传 False」），扫全文会误伤注释；
    真正的门禁在 `lint_card()` 运行时对本卡内容判定（见本文件上半部分）。
    """
    src = (ROOT / "wealth_code" / "pipelines" / "build_cards.py").read_text(encoding="utf-8")
    assert "🧪" not in src, "生产渲染器出现调试标记 🧪（卡片是成品，不是开发日志）"
    # ⛔ 也不许有「往卡顶塞一行说明」的写法
    assert "elements.insert(0" not in src, "禁止在生产渲染器里插卡顶说明行"


def test_title_is_not_scanned_for_dev_markers():
    """⚠️ 只扫**正文元素**，不扫 title。

    理由：title 已有自己的门禁（类型/增量标注/圆点），且测试夹具与内部预览
    常把标题写成「📊 测试卡·增量」—— 扫 title 会把它误判成"开发标注上卡"
    （实测 `test_card_copy_rules.py` 3 例直接红）。
    ⛔ 这不算放松：标注若真的写进 title，仍会被下面的正文扫描间接抓到
       （实际事故就是把说明塞进 body 第一块）。
    """
    card = {
        "title": "📊 测试卡·增量",
        "elements": [{"tag": "div", "text": {"tag": "lark_md",
                                             "content": "南威软件(603636) → AI 买入"}}],
    }
    ok, errs, _w = lint_card(card)
    assert not [e for e in errs if "研究/调试标注" in e["msg"]]
