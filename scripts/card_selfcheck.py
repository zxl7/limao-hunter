#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡片数字自洽自查（推送前门禁 · 2026-11-09）。

来历：11-09 复盘卡连推 3 次。第一次把「机器人 13 只 / 商业航天 11 只」推出去，
第二次改成 12/10 重推，第三次又补修 3 处残留「13 只」再重推。
`card_lint.py` 只查词汇不查数字自洽 ⇒ 三次都过了门禁。
本脚本补这一层：**主题实体在全卡只能有一个计数**，并与当日涨停池对账。

用法:
  python3 scripts/card_selfcheck.py outputs/review_2026-11-09.json
  python3 scripts/card_selfcheck.py <card.json> --date 2026-11-09
  python3 scripts/card_selfcheck.py <card.json> --verbose     # 打印全卡「N 只」清单供人工扫
  python3 scripts/card_selfcheck.py <card.json> --json

检查项:
  C1 主题计数唯一（FAIL）：加粗片段里定出的主题（**机器人 12 只**），
     全文所有「主题…N 只」的 N 必须一致。
  C2 涨停总数一致（FAIL）：卡内「涨停 N 只」≠ 当日 zt_history.zt_count → 阻断。
  C3 最高板不超限（FAIL）：卡内「最高板 N」> 当日 max_boards → 阻断。
  C4 池外四字词（WARN）：看着像个股名却不在当日涨停池 → 人工确认没被写成涨停。
  C6 全卡计数清单（INFO·仅 --verbose）：逐条列出「N 只」的上下文，供 10 秒扫读。

退出码: 0=通过(可含 WARN) / 1=有 FAIL / 2=输入或源数据不可读
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 加粗片段里的「主题 N 只」：**机器人 12 只**
BOLD_CNT = re.compile(r"\*\*([^*\n]{2,26}?)\s*(\d{1,3})\s*只")
# 任意位置「主题 N 只」（用于追踪已确认主题的其它出现）
ANY_CNT = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,12})\s*(\d{1,3})\s*只")
ZT_TOTAL = re.compile(r"涨停\s*(\d{1,3})\s*只")
MAXBOARD = re.compile(r"最高板\s*(\d{1,2})")

SEP = "，。：；、（）()·｜|—　"
# 主题名不允许包含的字符（出现即为句子片段，不是主题）
BAD_IN_NAME = set("只的了是在和与把被就也都还又而但却来到出去上下里中这个条件次种")
STOPWORD = {"涨停", "封住", "封板", "连板", "首板", "跌停", "大面", "核按钮", "炸板",
            "摸板", "红盘", "收红", "上涨", "下跌", "晋级", "新高", "题材", "板块", "个股"}


def extract_text(card: dict) -> str:
    """抽卡片纯文本（去标签）。⛔ 只从 content 字段取，避免同一段被重复添加。"""
    buf: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            c = node.get("content")
            if isinstance(c, str):
                buf.append(c)
            for k, v in node.items():
                if k != "content":
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(card)
    raw = "\n".join(buf)
    return re.sub(r"<[^>]+>", "", raw)


def _clean_entity(seg: str) -> str:
    """从加粗片段里切出主题名。

    `**机器人 12 只**`          → 机器人
    `**今天机器人在动 12 只**`   → 今天机器人在动 → 剥前缀 → 机器人（含禁用字则丢弃）
    `**，最高的就是首板 5 只**`  → 丢弃（句子片段）
    """
    s = seg.strip("　 ")
    if any(ch in s for ch in SEP):
        s = re.split("[" + re.escape(SEP) + "]", s)[-1].strip("　 ")
    s = s.strip()
    for p in ("今天", "今日", "昨日", "昨天", "其中", "这", "那", "共", "约", "仅", "有", "是", "为", "再", "还"):
        if s.startswith(p) and len(s) > len(p) + 1:
            s = s[len(p):]
    if not (2 <= len(s) <= 10):
        return ""
    if s in STOPWORD:
        return ""
    if any(ch in BAD_IN_NAME for ch in s):
        return ""
    if s[-1] in BAD_IN_NAME:
        return ""
    return s


def find_theme_entities(text: str) -> dict[str, set]:
    """从加粗片段定出主题实体 → 该实体在全文的计数集合。"""
    themes: list[str] = []
    for m in BOLD_CNT.finditer(text):
        e = _clean_entity(m.group(1))
        if e and e not in themes:
            themes.append(e)
    out: dict[str, set] = {}
    for e in themes:
        pat = re.compile(re.escape(e) + r"[^\d\n]{0,6}(\d{1,3})\s*只")
        nums = {int(x) for x in pat.findall(text)}
        if nums:
            out[e] = nums
    return out


def check_theme_counts(text: str) -> list:
    fails = []
    for e, nums in find_theme_entities(text).items():
        if len(nums) > 1:
            fails.append({
                "code": "C1",
                "msg": f"主题「{e}」计数不一致：全卡出现 " + " / ".join(f"{n} 只" for n in sorted(nums)),
            })
    return fails


def _load_zt(date: str):
    p = ROOT / "cache" / "zt_history" / f"zt_{date}.json"
    if not p.exists():
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def _guess_date(card_path: str, override: str | None = None) -> str | None:
    if override:
        return override
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", Path(card_path).name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


_COMMON = set("""涨停封板连板首板跌停炸板大面核按资金流入流出板块题材主线高度情绪指数上证深证创业科创成交缩量放量涨跌家数晋级接力龙头断板高开低开收盘开盘盘中沪深两市北向主力净额个股区间关注风险机会方向逻辑催化事件消息盘面赚钱亏钱效应周期退潮修复分歧亢奋冰点今天今日昨日明天明日上午午后尾盘早盘全天当日五日十日合计平均中位最大最小以上以下左右""".split())
_FRAG = ("涨停", "跌停", "封板", "炸板", "连板", "首板", "资金", "板块", "题材", "情绪", "指数",
         "成交", "涨跌", "龙头", "断板", "高开", "低开", "收盘", "开盘", "盘中", "两市", "净额",
         "个股", "风险", "机会", "方向", "逻辑", "消息", "盘面", "赚钱", "亏钱", "效应", "周期",
         "退潮", "修复", "分歧", "环境", "市场", "上午", "午后", "尾盘", "早盘", "全天", "当日",
         "今日", "昨日", "明天", "明日", "五日", "十日", "合计", "平均", "中位", "最大", "最小",
         "以上", "以下", "左右", "附近", "不是", "没有", "只是", "就是", "还有", "而且", "所以")


def check_pool_names(text: str, date: str):
    fails, warns = [], []
    zt = _load_zt(date)
    if not zt:
        warns.append({"code": "C0", "msg": f"未找到 cache/zt_history/zt_{date}.json，跳过源数据对账（C2/C3/C4）"})
        return fails, warns

    zt_count = zt.get("zt_count") or len(zt.get("zt_list") or [])
    max_boards = zt.get("max_boards")

    totals = {int(x) for x in ZT_TOTAL.findall(text)}
    if totals:
        mx = max(totals)
        if mx > zt_count:
            fails.append({"code": "C2",
                          "msg": f"卡内最大「涨停 {mx} 只」超过当日 zt_history 的 {zt_count} 只"})
        elif mx < zt_count:
            # 可能只是引用了昨日数（如「涨停 32 只（昨）」），不该硬阻断
            warns.append({"code": "C2",
                          "msg": f"卡内最大「涨停 {mx} 只」小于当日 {zt_count} 只，确认没有漏写今日总数"})

    if max_boards:
        over = sorted({int(x) for x in MAXBOARD.findall(text)} - {x for x in range(0, int(max_boards) + 1)})
        if over:
            fails.append({"code": "C3", "msg": f"卡内出现「最高板 {over}」，超过当日真实最高板 {max_boards}"})

    pool = {s.get("name") for s in (zt.get("zt_list") or []) if s.get("name")}
    if pool:
        cand = [w for w in set(re.findall(r"[\u4e00-\u9fa5]{4}", text))
                if w not in pool and w not in _COMMON and not any(f in w for f in _FRAG)]
        if cand:
            warns.append({"code": "C4",
                          "msg": "卡内四字词不在当日涨停池（可能是断板/跌停/普通词，人工确认没被写成涨停）："
                                 + "、".join(sorted(cand)[:12])})
    return fails, warns


def dump_counts(text: str) -> list:
    """C6：全卡「N 只」清单（带 12 字上下文），供人工 10 秒扫读。"""
    rows = []
    for m in ANY_CNT.finditer(text):
        s = max(0, m.start() - 12)
        rows.append(f"  {m.group(2):>3} 只  ←  …{text[s:m.end()].replace(chr(10), ' ')}")
    return rows


def selfcheck(card_path: str, date: str | None = None):
    card = json.load(open(card_path, encoding="utf-8"))
    text = extract_text(card)
    fails = check_theme_counts(text)
    warns: list = []
    d = _guess_date(card_path, date)
    if d:
        f2, w2 = check_pool_names(text, d)
        fails += f2
        warns += w2
    else:
        warns.append({"code": "C0", "msg": "文件名与 --date 都取不到业务日，跳过源数据对账"})
    return (len(fails) == 0), fails, warns, text


def main():
    ap = argparse.ArgumentParser(description="卡片数字自洽自查（推送前门禁）")
    ap.add_argument("card", help="卡片 JSON 路径")
    ap.add_argument("--date", default=None, help="业务日 YYYY-MM-DD（默认从文件名解析）")
    ap.add_argument("--verbose", action="store_true", help="打印全卡「N 只」清单供人工扫读")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    try:
        ok, fails, warns, text = selfcheck(args.card, args.date)
    except FileNotFoundError:
        print(f"ERROR 卡片不存在: {args.card}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"ERROR 卡片 JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        payload = {"ok": ok, "fails": fails, "warns": warns}
        if args.verbose:
            payload["counts"] = dump_counts(text)
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        for w in warns:
            if w["code"] == "C4" and not args.verbose:
                continue      # 池外四字词噪声大（会被截断成「七腾机器」），只在 --verbose 里看
            print(f"[{w['code']}] ⚠️ {w['msg']}")
        for f in fails:
            print(f"[{f['code']}] ❌ {f['msg']}")
        if args.verbose:
            print("── 全卡「N 只」清单 ──")
            for row in dump_counts(text):
                print(row)
        print("SELFCHECK " + ("PASS" if ok else f"FAIL（{len(fails)} 项）"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
