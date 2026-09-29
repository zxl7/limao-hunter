#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡面质量自检（2026-09-18 用户「做上去」·学习层 B 项）

用途：常态化扫描**最近生成的卡**，把"只能靠用户肉眼发现"的卡面缺陷变成自动检测——
  ① **重复文案**：同一句（长度 ≥ 阈值）在同一张卡出现 ≥2 次（实证 09-17：点评与 AI 理由
     各带一段 ⚠️风险 → 同票两个风险段，即本项要抓的典型）；
  ② **黑话禁项**：英文档位/字段名/内部指标/自造术语 —— 词表**单一源** = `scripts/card_copy_rules.py`
     （与推送门禁 `card_lint.py`、禁项测试共用，⛔ 不得各自维护）；分两级：
     Tier-1 机器黑话＝真问题 / Tier-2 行业术语＝措辞提示；
  ③ **结构异常**：卡无元素 / 元素过少 / 标题缺失。

⚠️ 只扫 `QUALITY_SINCE`（默认 2026-09-18·门禁生效日）之后生成的卡——更早的历史卡
   问题已固化、反复报只会淹没新问题。

⛔ 只读不写：仅产出报告（`cache/card_quality_latest.json`），不改卡不改配置。
   改法由人/agent 决定（学习层 R7 只出建议·语义类）。

用法：
  python3 scripts/card_quality_check.py                 # 扫最近 3 天（门禁生效日后）
  python3 scripts/card_quality_check.py --days 7 --json  # 机器可读
  python3 scripts/card_quality_check.py --since all      # 不限生效日（回溯历史）
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

# 白话词表 —— **单一源** = scripts/card_copy_rules.py（2026-09-18 收口）
# 背景：此前本脚本自维护一套 JARGON，与 tests 的 4 套清单 + card_lint 互不一致。
# ⛔ 任何词表改动只改 card_copy_rules.py，本脚本与 card_lint / tests 一律引用它。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import card_copy_rules as _ccr        # noqa: E402

# 问题扫描生效日（2026-09-18）：此前生成的卡属「门禁生效前」的历史产物，
# 反复扫出只会造成噪音（如 09-15/09-16 的重复风险段已在 09-17 修复，
# 但旧卡文件仍在窗口内 → 每天重复报）。只扫生效日之后生成的卡。
QUALITY_SINCE = "2026-09-18"
# ⛔ 重复阈值取 15：过滤掉"模板化短标签"（如每票一行 `🤖 AI 判断：观察`——多票同名属正常，
#    非文案重复）；只抓真正的长文案重复（实证 09-16 `⚠️ 风险：20cm波动大要盯紧止损` x2）。
MIN_SENT_LEN = 15         # 参与重复检测的最短句长
MAX_REPEAT_SAMPLES = 3    # 报告里每条重复最多列几个样例
# 逐票固定裁决标签会随候选数量重复，但每一条对应不同个股，属于必要结构而非文案重复。
# 真正的裁决理由仍保留在下一行参与扫描。
_REPEAT_EXEMPT_PREFIXES = ("🤖 今日9:25判：",)
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
        if s.startswith(_REPEAT_EXEMPT_PREFIXES):
            continue
        seen[s] = seen.get(s, 0) + 1
    dup = sorted([(s, n) for s, n in seen.items() if n >= 2], key=lambda x: -x[1])
    rec["repeat"] = [{"sentence": s[:120], "count": n} for s, n in dup[:MAX_REPEAT_SAMPLES]]

    # ② 白话禁项（去标签后扫·避免 text_tag 属性误命中）；词表来自单一源
    plain = re.sub(r"<[^>]+>", "", all_text)
    _fails, _warns = _ccr.scan(plain)
    rec["jargon"] = sorted(_fails)          # Tier-1 真违规（机器黑话）
    rec["jargon_warn"] = sorted(_warns)     # Tier-2 提示（行业术语）
    return rec


def recent_cards(days: int = 3, since: str | None = QUALITY_SINCE) -> list[Path]:
    """近 N 天内修改过的卡文件（兼容新旧命名与两个目录）。

    since：问题扫描生效日（`YYYY-MM-DD`）→ 早于该日生成的卡不参与（避免历史噪音）；
           传 None / "all" 表示不限（回溯历史用）。
    """
    cutoff = datetime.now(BJ_TZ).timestamp() - days * 86400
    if since and str(since).lower() != "all":
        try:
            _d = datetime.strptime(str(since), "%Y-%m-%d").replace(tzinfo=BJ_TZ)
            cutoff = max(cutoff, _d.timestamp())
        except ValueError:
            pass
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


def run(days: int = 3, since: str | None = QUALITY_SINCE) -> dict:
    cards = recent_cards(days, since)
    reports = [check_card(p) for p in cards]
    # 真问题 = 机器黑话 / 重复文案 / 结构异常；行业术语（jargon_warn）只作提示不计入
    bad = [r for r in reports if r["repeat"] or r["jargon"] or r["struct"]]
    _bad_files = {r["file"] for r in bad}
    hinted = [r for r in reports if r["jargon_warn"] and r["file"] not in _bad_files]
    result = {
        "generated_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        "days": days,
        "since": since or "all",
        "checked": len(reports),
        "issues": len(bad),
        "hinted": len(hinted),
        "cards": bad,
        "cards_hint": hinted,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="卡面质量自检")
    ap.add_argument("--days", type=int, default=3, help="扫描最近 N 天的卡（默认 3）")
    ap.add_argument("--since", default=QUALITY_SINCE,
                    help=f"只扫该日之后生成的卡（默认 {QUALITY_SINCE}；传 all 不限·回溯历史用）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()
    r = run(a.days, a.since)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(f"扫描 {r['checked']} 张卡（自 {r['since']} 起）· 真问题 {r['issues']} 张 · 措辞提示 {r['hinted']} 张")
    for c in r["cards"]:
        print(f"\n▸ {c['file']}  「{c['title'][:40]}」")
        for d in c["repeat"]:
            print(f"    ⚠️ 重复 x{d['count']}：{d['sentence']}")
        if c["jargon"]:
            print(f"    🚫 黑话：{'、'.join(c['jargon'])}")
        for s in c["struct"]:
            print(f"    ❌ 结构：{s}")
    for c in r.get("cards_hint") or []:
        print(f"\n▸ {c['file']}（仅措辞提示）")
        print(f"    💬 建议白话化：{'、'.join(_ccr.describe(t) for t in c['jargon_warn'])}")
    if not r["issues"] and not r["hinted"]:
        print("✅ 未发现卡面质量问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
