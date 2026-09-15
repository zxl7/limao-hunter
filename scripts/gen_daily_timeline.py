#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_daily_timeline.py — 当日题材时间线（融合进 daily_YYYY-MM-DD.md，不新建文件）

原理：每张卡推送后都会向 .workbuddy/memory/daily_YYYY-MM-DD.md 追加「## HH:MM xxx」推送记录
（含 主线/TOP3题材/突发题材Top3-5/核心标的/AI核心判断/AI定调 字段）。
本脚本解析该文件自身的推送记录 → 按时间排序生成「📅 当日题材时间线」总览章节，
原位写回同一文件（幂等：已存在则整体替换，不重复不新建）。

用法：
  python3 scripts/gen_daily_timeline.py [YYYY-MM-DD]   # 缺省=今天
挂载：capture_daily.py 盘后归档链末尾调用（第 4 步）。
"""
import re
import sys
from pathlib import Path

MEM_DIR = Path("/Users/zxl/WorkBuddy/狸猫猎手/.workbuddy/memory")
TIMELINE_TITLE = "📅 当日题材时间线（自动生成·盘后刷新）"

# 字段提取规则（各卡推送记录字段名不统一，全部兼容）
TOPIC_KEYS = ["TOP3题材", "突发题材Top3-5", "主线"]
CORE_KEY = "核心标的"
TONE_KEYS = ["AI核心判断", "AI定调", "定调"]

TIME_RE = re.compile(r"^##\s+(?:盘前预测\s+\S+\s+)?(\d{2}:\d{2})\s*(.*)$")


def parse_sections(text: str):
    """按 ## 节切分，返回 [(time, title, lines_dict)]"""
    sections = []
    cur = None
    for ln in text.split("\n"):
        m = TIME_RE.match(ln)
        if m:
            cur = {"time": m.group(1), "title": m.group(2).strip(), "topics": "", "core": "", "tone": ""}
            sections.append(cur)
        elif cur is not None:
            for key in TOPIC_KEYS:
                mm = re.match(rf"^-\s*{key}[:：]\s*(.*)$", ln)
                if mm and not cur["topics"]:
                    cur["topics"] = mm.group(1).strip()
            mm = re.match(rf"^-\s*{CORE_KEY}[:：]\s*(.*)$", ln)
            if mm and not cur["core"]:
                cur["core"] = mm.group(1).strip()
            for key in TONE_KEYS:
                mm = re.match(rf"^-\s*{key}[:：]\s*(.*)$", ln)
                if mm and not cur["tone"]:
                    cur["tone"] = mm.group(1).strip()
    return [s for s in sections if s["time"]]


def compact_topics(topics: str, max_len: int = 46) -> str:
    """题材字段归一化：按 ＞ 拆主线优先级段；段内按「 / 」（带空格）拆快照多题材；
    无空格斜杠（医药/CRO）保留为复合名。累计取前 3 个题材。
    ⚠️ 必须先拆「 / 」再去括号——否则 "医药(涨停7) / 光通信(3)" 会被第一个 ( 截断只留"医药"。"""
    if not topics:
        return ""
    out = []
    for seg in [p.strip() for p in re.split(r"[＞>]", topics) if p.strip()]:
        seg = re.sub(r"^[⭐*★\d]+", "", seg)   # 去星级
        seg = re.sub(r"（.*?）", "", seg)        # 去中文括号细节
        if not seg:
            continue
        for sub in [s.strip() for s in seg.split(" / ")]:  # 带空格斜杠=多题材分隔
            sub = sub.split("(")[0].strip()     # 去英文括号细节（拆后逐段去）
            if sub and sub not in out:
                out.append(sub)
        if len(out) >= 3:
            break
    return "/".join(out[:3])[:max_len]


def compact_core(core: str, max_stocks: int = 3) -> str:
    """核心标的取完整「名(代码)」票对（正则提取，避免 · 截断在括号中间）；
    保留可买/可买Top3/已涨停前缀语义。"""
    if not core:
        return ""
    prefix = ""
    for p in ["可买Top3", "可买", "已涨停"]:
        if core.startswith(p):
            prefix = p + " "
            break
    stocks = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]+\([^()]*\)", core)
    if not stocks:
        return core.split("；")[0][:40]
    return prefix + "·".join(stocks[:max_stocks])


def build_timeline(sections):
    """按时间排序生成时间线行列表"""
    rows = []
    for s in sorted(sections, key=lambda x: x["time"]):
        topics = compact_topics(s["topics"]) if s["topics"] else ""
        core = compact_core(s["core"]) if s["core"] else ""
        tone = s["tone"][:44] if s["tone"] else ""
        bits = []
        if topics:
            bits.append(f"题材 {topics}")
        if core:
            bits.append(f"核心 {core}")
        if tone:
            bits.append(f"定调 {tone}")
        rows.append(f"- **{s['time']}**：{' ｜ '.join(bits)}" if bits else f"- **{s['time']}**：{s['title']}")
    return rows


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else _today()
    path = MEM_DIR / f"daily_{date_str}.md"
    if not path.exists():
        print(f"[gen_daily_timeline] 无推送记录文件 {path.name}，跳过")
        return
    text = path.read_text(encoding="utf-8")

    sections = parse_sections(text)
    if not sections:
        print(f"[gen_daily_timeline] {path.name} 无 `## HH:MM` 推送节，跳过")
        return

    rows = build_timeline(sections)
    block = [f"## {TIMELINE_TITLE}", "", *rows, ""]

    # 幂等替换：已存在该章节则整体替换，否则插到「# 推送记录」之后
    old_pat = re.compile(rf"##\s*{re.escape(TIMELINE_TITLE)}.*?(?=## |\Z)", re.S)
    if old_pat.search(text):
        new_text = old_pat.sub("\n".join(block) + "\n", text, count=1)
    elif text.startswith("# 推送记录"):
        new_text = text.replace("# 推送记录", "# 推送记录\n\n" + "\n".join(block), 1)
    else:
        new_text = "\n".join(block) + "\n\n" + text

    path.write_text(new_text, encoding="utf-8")
    print(f"[gen_daily_timeline] {path.name} 时间线已刷新（{len(sections)} 节 → {len(rows)} 行）")


def _today():
    from datetime import date
    return date.today().isoformat()


if __name__ == "__main__":
    main()
