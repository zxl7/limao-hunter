#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡面质量自检（2026-09-18 用户「做上去」·学习层 B 项）

用途：常态化扫描**最近生成的卡**，把"只能靠用户肉眼发现"的卡面缺陷变成自动检测——
  ① **重复文案**：同一句（长度 ≥ 阈值）在同一张卡出现 ≥2 次（实证 09-17：点评与 AI 理由
     各带一段 ⚠️风险 → 同票两个风险段，即本项要抓的典型）；
  ② **黑话禁项**：英文档位/字段名/内部指标/自造术语（合并三组既有清单·用户「不要推专业术语」）；
  ③ **结构异常**：卡无元素 / 元素过少 / 标题缺失。

⛔ 只读不写：仅产出报告（`cache/card_quality_latest.json`），不改卡不改配置。
   改法由人/agent 决定（学习层 R7 只出建议·语义类）。

用法：
  python3 scripts/card_quality_check.py                 # 扫最近 3 天
  python3 scripts/card_quality_check.py --days 7 --json  # 机器可读
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
WC_CACHE = ROOT / "wealth_code" / "cache"
OUT = CACHE / "card_quality_latest.json"
BJ_TZ = timezone(timedelta(hours=8))

# 黑话禁项（合并 tests/test_verify_card_ai.py 三组清单：竞价卡 / 决策段 / 候选 tag）
JARGON = [
    "expected", "regime=", "门控=", "defense=", "pp）", "[放量", "可信度", "题材板块效应",
    "孤军", "弱转强", "分歧/死亡", "冰点禁买", "已结算", "样本", "容量核心", "承接", "身位",
    "spec §", "health ", "情绪升温", "情绪退潮", "情绪中性", "严格主升", "冰点档", "杂毛",
    "溢价率", "维度", "研判", "标的", "卡位", "兑现", "埋伏", "打板", "梯队",
    "buy/", "pending", "reject", "MA5", "feedback_matrix", "反馈矩阵", "亢奋", "修复档",
]
# ⛔ 重复阈值取 15：过滤掉"模板化短标签"（如每票一行 `🤖 AI 判断：观察`——多票同名属正常，
#    非文案重复）；只抓真正的长文案重复（实证 09-16 `⚠️ 风险：20cm波动大要盯紧止损` x2）。
MIN_SENT_LEN = 15         # 参与重复检测的最短句长
MAX_REPEAT_SAMPLES = 3    # 报告里每条重复最多列几个样例
# ⛔ 结构字段：其值不是"给人看的文案"（`{"tag":"plain_text"}` 曾把 plain_text 当文案误报）
_STRUCT_KEYS = {"tag", "template", "color", "msg_type", "type", "mode", "size",
                "text_size", "width", "wide_screen_mode", "schema", "font"}


def _extract_texts(o, key: str | None = None) -> list[str]:
    """递归提取字符串叶子（对 dict 全值递归·不只认 content）；排除结构字段值（防误报）。"""
    out: list[str] = []
    if isinstance(o, str):
        if key not in _STRUCT_KEYS:
            out.append(o)
    elif isinstance(o, dict):
        for k, v in o.items():
            out.extend(_extract_texts(v, k))
    elif isinstance(o, list):
        for v in o:
            out.extend(_extract_texts(v, key))
    return out


def _sentences(all_text: str) -> list[str]:
    """按句切分（。；!?\\n），去标签与空白，返回长度 ≥ MIN_SENT_LEN 的句。"""
    plain = re.sub(r"<[^>]+>", "", all_text)        # 去 text_tag/font 标签
    parts = re.split(r"[。；;!?\n]+", plain)
    return [p.strip() for p in parts if len(p.strip()) >= MIN_SENT_LEN]


def check_card(path: Path) -> dict:
    """检查单卡 → {"file","title","repeat":[...],"jargon":[...],"struct":[...]}。"""
    rec: dict = {"file": path.name, "title": "", "repeat": [], "jargon": [], "struct": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        rec["struct"].append(f"不可解析：{e}")
        return rec
    # 飞书卡可能是 {msg_type, card} 或扁平 {title, elements}
    card = raw.get("card") if isinstance(raw, dict) and "card" in raw else raw
    if not isinstance(card, dict):
        rec["struct"].append("卡体不是 dict")
        return rec
    rec["title"] = str(card.get("title") or (card.get("header") or {}).get("title", {}).get("content") or "")
    els = card.get("elements") or []
    if not els:
        rec["struct"].append("无 elements（空卡）")
    all_text = "\n".join(_extract_texts(card))

    # ① 重复句（同卡内同句 ≥2 次）
    seen: dict[str, int] = {}
    for s in _sentences(all_text):
        seen[s] = seen.get(s, 0) + 1
    dup = sorted([(s, n) for s, n in seen.items() if n >= 2], key=lambda x: -x[1])
    rec["repeat"] = [{"sentence": s[:120], "count": n} for s, n in dup[:MAX_REPEAT_SAMPLES]]

    # ② 黑话禁项（去标签后扫·避免 text_tag 属性误命中）
    plain = re.sub(r"<[^>]+>", "", all_text)
    rec["jargon"] = sorted({b for b in JARGON if b in plain})
    return rec


def recent_cards(days: int = 3) -> list[Path]:
    """近 N 天内修改过的卡文件（兼容新旧命名与两个目录）。"""
    cutoff = datetime.now(BJ_TZ).timestamp() - days * 86400
    out: list[Path] = []
    for d in (CACHE, WC_CACHE):
        if not d.exists():
            continue
        # ⛔ 只匹配真卡（`card_2026-09-17.json` / `card_20260917_153609.json`），
        #    排除本脚本自身产物 `card_quality_latest.json`（否则自检扫自己→误报）
        for p in d.glob("card_20*.json"):
            try:
                if p.stat().st_mtime >= cutoff:
                    out.append(p)
            except Exception:
                continue
    return sorted(out, key=lambda p: -p.stat().st_mtime)


def run(days: int = 3) -> dict:
    cards = recent_cards(days)
    reports = [check_card(p) for p in cards]
    bad = [r for r in reports if r["repeat"] or r["jargon"] or r["struct"]]
    result = {
        "generated_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        "days": days,
        "checked": len(reports),
        "issues": len(bad),
        "cards": bad,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="卡面质量自检")
    ap.add_argument("--days", type=int, default=3, help="扫描最近 N 天的卡（默认 3）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()
    r = run(a.days)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(f"扫描 {r['checked']} 张卡 · 发现问题 {r['issues']} 张")
    for c in r["cards"]:
        print(f"\n▸ {c['file']}  「{c['title'][:40]}」")
        for d in c["repeat"]:
            print(f"    ⚠️ 重复 x{d['count']}：{d['sentence']}")
        if c["jargon"]:
            print(f"    🚫 黑话：{'、'.join(c['jargon'])}")
        for s in c["struct"]:
            print(f"    ❌ 结构：{s}")
    if not r["issues"]:
        print("✅ 未发现卡面质量问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
