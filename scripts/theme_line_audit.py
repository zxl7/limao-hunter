#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘后题材归并巡检「哨兵」（2026-10-12 传媒事故结构性防御层）。

背景：10-12 中文在线/掌阅 的 `core_theme` 被泛「AI」规则误标「AI算力」，
      而 `sector=数字媒体`（→传媒）→ 两条归线**跨大类矛盾**却无人发现，
      跟风闸遂整条误杀（传媒全涨停却一只没推）。

⚠️ 实测教训（2026-10-12）：简单「core_theme 归线 ≠ sector 归线」布尔告警**误报 62%**——
因为 core_theme（炒作题材）与 sector（行业）**天然可不同线**（丰元「固态电池」vs「新能源」、
九阳「华为链」vs「小家电」都 legit）。**故本脚本只报「高置信跨大类错配」**：

  `HIGH_RISK_PAIRS` = 「硬科技线」×「软行业线」——这种组合几乎不可能 legit
  （如 传媒票被归进「AI算力」），正是 10-12 事故形态。

另产出**归并快照**（每条线成员 + 行业），供事后排查（用户抓包时 AI 秒查）。

⛔ 只读巡检，不改任何数据/结论；检测失败也绝不影响主链路。
CLI：
  python3 scripts/theme_line_audit.py                # 打印 + 落盘（不推送）
  python3 scripts/theme_line_audit.py --push          # 有高置信异常时推异动群
  python3 scripts/theme_line_audit.py --date 2026-10-09
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

# 规范时间戳（2026-10-14 审计 P1-⑦）：generated_ts 统一 ISO8601 带 +08:00。
from common.market_time import iso_bj  # noqa: E402
from core import sector_support as ss   # noqa: E402

CAND_DIR = ROOT / "wealth_code" / "cache"
OUT = ROOT / "cache" / "theme_line_audit.json"

# 高置信「跨大类错配」清单：(题材线集合, 行业线集合) —— 二者同时命中 = 可疑
# 例：core_theme 归「AI算力」而 sector 归「传媒」→ 传媒票被塞进算力线（10-12 事故）。
HIGH_RISK_PAIRS: list[tuple[set[str], set[str]]] = [
    ({"AI算力", "半导体", "PCB"}, {"传媒", "大消费", "医药", "大农业", "黄金有色"}),
    ({"传媒"}, {"AI算力", "半导体", "PCB", "新能源", "绿电", "医药", "大农业"}),
]


def _line(c: dict, key: str) -> str:
    v = str(c.get(key) or "").strip()
    return ss._theme_key({"core_theme": v}) if key == "core_theme" else (
        ss._theme_key({"sector": v}) if v else "")


def find_high_risk(cands: list) -> list[dict]:
    """高置信跨大类错配（纯函数）。返回 [{code,name,core_theme,sector,by_theme,by_sector}]。"""
    out = []
    for c in (cands or []):
        if not isinstance(c, dict):
            continue
        ct = str(c.get("core_theme") or "").strip()
        sec = str(c.get("sector") or "").strip()
        if not ct or ct == "—" or not sec:
            continue
        by_theme = _line(c, "core_theme")
        by_sector = _line(c, "sector")
        if not by_theme or not by_sector or by_theme == by_sector:
            continue
        for hard, soft in HIGH_RISK_PAIRS:
            if by_theme in hard and by_sector in soft:
                out.append({"code": str(c.get("code") or ""), "name": str(c.get("name") or ""),
                            "core_theme": ct, "sector": sec,
                            "by_theme": by_theme, "by_sector": by_sector})
                break
    return out


def snapshot(cands: list) -> dict:
    """归并快照（纯函数）：{线族: [{"code","name","sector"}...]}，供事后排查。"""
    snap: dict[str, list] = {}
    for c in (cands or []):
        if not isinstance(c, dict):
            continue
        fam = ss._theme_key(c)
        if not fam:
            continue
        snap.setdefault(fam, []).append(
            {"code": str(c.get("code") or ""), "name": str(c.get("name") or ""),
             "sector": str(c.get("sector") or "")})
    return snap


def _latest_candidates(date: str | None) -> tuple[str, list]:
    if date:
        p = CAND_DIR / f"candidates-{date}.json"
    else:
        fs = sorted(CAND_DIR.glob("candidates-*.json"))
        p = fs[-1] if fs else None
    if not p or not p.exists():
        return (date or ""), []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return (date or ""), []
    rows = list(d.get("relay") or []) + list(d.get("watch") or [])
    return str(d.get("date") or date or ""), rows


def _push(high_risk: list, d: str) -> None:
    """高置信异常 → 推异动群（复用 push_card.py）。任何失败静默（不阻断）。"""
    try:
        lines = [f"**⚠️ 题材归并巡检告警 | {d}**",
                 "以下票的**炒作题材归线**与**行业归线**跨大类错配（疑似归并错误）：", ""]
        for x in high_risk[:8]:
            lines.append(f"　{x['name']}({x['code']})：{x['core_theme']}→{x['by_theme']}　"
                         f"≠　{x['sector']}→{x['by_sector']}")
        lines.append("")
        lines.append("⛔ 请人工确认：题材提炼是否把无关票并进同一条线。")
        card = {"title": f"⚠️ 题材归并巡检 | {d} | {len(high_risk)}只",
                "color": "orange",
                "elements": [{"tag": "div",
                              "text": {"tag": "lark_md", "content": "\n".join(lines)}}]}
        tmp = ROOT / "cache" / "theme_line_audit_card.json"
        tmp.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "push_card.py"),
                        "--webhook", "feishu_webhook_intraday", str(tmp)],
                       capture_output=True, text=True, timeout=60)
    except Exception:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="盘后题材归并巡检（哨兵）")
    ap.add_argument("--date", default="", help="candidates 日期 YYYY-MM-DD（缺省取最近）")
    ap.add_argument("--push", action="store_true", help="有高置信异常时推异动群")
    a = ap.parse_args(argv)

    d, rows = _latest_candidates(a.date or None)
    high_risk = find_high_risk(rows)
    snap = snapshot(rows)
    OUT.write_text(json.dumps(
        {"checked_date": d, "generated_ts": iso_bj(),
         "n_candidates": len(rows), "n_high_risk": len(high_risk),
         "high_risk": high_risk,
         "snapshot": {k: v for k, v in sorted(snap.items())}},
        ensure_ascii=False, indent=1), encoding="utf-8")

    if high_risk:
        print(f"[theme_line_audit] ⚠️ 高置信归并错配 {len(high_risk)} 只：")
        for x in high_risk[:10]:
            print(f"  [ALERT] {x['name']}({x['code']}) {x['core_theme']}→{x['by_theme']} "
                  f"≠ {x['sector']}→{x['by_sector']}")
        if a.push:
            _push(high_risk, d)
    else:
        print(f"[theme_line_audit] ✅ 无高置信归并错配（检查 {len(rows)} 只 / {len(snap)} 条线）")
    print(f"[theme_line_audit] 快照 {len(snap)} 条线 → cache/theme_line_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
