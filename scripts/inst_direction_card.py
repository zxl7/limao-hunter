#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机构方向·晚间卡（Alpha派数据源 × 当日盘面交叉验证）

定位：替代原来只做「机构在聊什么」的 Gangtise 早报卡。
本卡不点名 60 只票，只回答三件事：
  1. 今晚机构在给哪些方向开会 / 出订单（会议纪要 + 隔夜公告）
  2. 这些方向今天盘面认不认（涨停家数、最高板、集中度）
  3. 明天该盯哪个

数据来源分工（⛔ 严格分开，不许互相冒充）：
  - 盘面：cache/zt_history/zt_<date>.json（唯一事实）
  - 方向四象限：cache/inst_direction_<date>.json（scripts/inst_direction.py 产出）
  - 机构侧（会议/公告/研报）：由 AI 从 Alpha派 拉取后作为参数传入（本脚本不联网）

用法：
  python3 scripts/inst_direction_card.py --date 2026-10-28 --input /tmp/inst_side.json
  python3 scripts/inst_direction_card.py --date 2026-10-28 --input /tmp/inst_side.json --push
  （--push 走 scripts/push_card.py；不带 --push 只落盘 JSON，便于先看内容）

inst_side.json 结构（AI 拉完 Alpha派 后人工整理，⛔ 禁止脚本自行编造）：
{
  "index": [{"name":"上证指数","close":3681.47,"pct":0.91}, ...],
  "meetings": [{"title":"鼎泰高科 三季报","stock":"301377","name":"鼎泰高科",
                "industry":"通用设备","brokers":["中金","华创"],"time":"14:00"}, ...],
  "bids": [{"code":"300360","name":"炬华科技","industry":"电网设备","pct":0.37,"type":"中标"}, ...],
  "increases": [{"code":"600188","name":"兖矿能源","note":"控股股东增持进展"}, ...],
  "research": [{"direction":"人形机器人","brokers":20,"gist":"20+家券商覆盖","split":"分歧在本体vs零部件"}, ...]
}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
ZT_DIR = CACHE / "zt_history"
PUSH = ROOT / "scripts" / "push_card.py"

# 科创 / 北交所：无权限，卡面一律不出现
_BLOCK_PREFIX = ("688", "689", "8", "4", "920")


def _load(path: Path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _tradable(code: str) -> bool:
    c = str(code)[:6]
    return not c.startswith(_BLOCK_PREFIX)


def _color_pct(pct: float | None) -> str:
    """A股配色：涨红跌绿。"""
    if pct is None:
        return "grey"
    return "red" if pct > 0 else ("green" if pct < 0 else "grey")


def build_payload(date: str, side: dict) -> dict:
    zt = _load(ZT_DIR / f"zt_{date}.json") or {}
    zt_list = [x for x in (zt.get("zt_list") or []) if x.get("code")]
    inst = _load(CACHE / f"inst_direction_{date}.json") or {}

    sector_c = Counter((x.get("sector") or "未分类") for x in zt_list)
    max_boards = max([int(x.get("boards") or 1) for x in zt_list], default=0)
    leaders = sorted(
        [x for x in zt_list if int(x.get("boards") or 1) >= 2],
        key=lambda y: -int(y.get("boards") or 1),
    )

    top_themes = ((inst.get("market") or {}).get("top_themes") or [])[:3]

    # 会议按方向聚合：用行业字段，多个会撞同一行业就是「这个行业今晚被集中开会」
    m_by_ind: dict[str, list[dict]] = defaultdict(list)
    for m in side.get("meetings") or []:
        m_by_ind[m.get("industry") or "其他"].append(m)

    return {
        "date": date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "market": {
            "zt_count": len(zt_list),
            "max_boards": max_boards,
            "sector_top": sector_c.most_common(6),
            "leaders": [
                {"code": str(x.get("code"))[:6], "name": x.get("name"),
                 "boards": int(x.get("boards") or 1), "sector": x.get("sector")}
                for x in leaders[:5]
            ],
            "top_themes": top_themes,
        },
        "meetings": {
            "count": len(side.get("meetings") or []),
            "by_industry": [
                {"industry": k, "items": v} for k, v in
                sorted(m_by_ind.items(), key=lambda kv: -len(kv[1]))
            ],
            "multi_broker": [
                m for m in (side.get("meetings") or [])
                if len(m.get("brokers") or []) >= 2
            ],
        },
        "bids": side.get("bids") or [],
        "increases": side.get("increases") or [],
        "research": side.get("research") or [],
        "quadrants": [
            {"direction": d.get("direction_key"), "quadrant": d.get("quadrant"),
             "verdict": d.get("verdict"), "tenure": d.get("inst_tenure_days"),
             "series": d.get("tag_zt_series")}
            for d in (inst.get("directions") or [])
        ],
        "caliber": {
            "zt": f"cache/zt_history/zt_{date}.json（当日收盘涨停池）",
            "inst_direction": f"cache/inst_direction_{date}.json",
            "inst_side": "Alpha派：会议纪要 / 公告列表 / 研报召回",
            "announce_date": "公告发布日按次一交易日计（收盘后披露惯例）",
        },
    }


def _div(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def render_card(p: dict) -> dict:
    m = p["market"]
    els: list[dict] = []

    # ── 1. 今日盘面定调 ──
    idx_txt = "｜".join(
        f"{i['name']} <text_tag color='{_color_pct(i.get('pct'))}'>{i['close']}"
        f"（{i['pct']:+.2f}%）</text_tag>"
        for i in (p.get("index") or [])
    ) or "指数未取到"
    sec_txt = "、".join(f"{s} {n}家" for s, n in m["sector_top"][:4])
    lead_txt = "、".join(
        f"{x['name']}{x['boards']}板" for x in m["leaders"][:3]
    ) or "无连板"
    theme_txt = "、".join(
        f"{t['theme']} {t['zt']}家（占{t['share_pct']}%）" for t in m["top_themes"]
    ) or "—"

    els.append(_div(
        f"**📐 今日盘面（{p['date']} 收盘）**\n\n"
        f"{idx_txt}\n"
        f"涨停 <text_tag color='red'>{m['zt_count']} 家</text_tag>·"
        f"最高 <text_tag color='red'>{m['max_boards']} 板</text_tag>（{lead_txt}）\n"
        f"按行业看：{sec_txt}\n"
        f"按题材看：{theme_txt}（题材分父子、有重叠，不是相加）\n"
        f"▸ 一句话：{p.get('market_note') or '普涨但高度没打开，属修复不是新周期'}"
    ))
    els.append({"tag": "hr"})

    # ── 2. 今晚机构在开会 ──
    mt = p["meetings"]
    if mt["count"]:
        lines = []
        for grp in mt["by_industry"][:5]:
            items = grp["items"]
            names = "、".join(
                # 单只票不重复写行业名；机构数>=2 标出来，那是关注度信号
                (f"{i['name']}（{'/'.join(i['brokers'])}）"
                 if len(i.get("brokers") or []) >= 2 else i["name"])
                for i in items
            )
            lines.append(f"▸ {grp['industry']} {len(items)} 场：{names}")
        multi = mt["multi_broker"]
        extra = ""
        if multi:
            mm = multi[0]
            extra = (f"\n▸ 值得注意：<text_tag color='violet'>{mm['name']}</text_tag>"
                     f" 今晚被 {'+'.join(mm['brokers'])} 同时开会"
                     f"——多家机构同一晚开一只票，是关注度而非结论")
        total = p.get("meeting_total")
        head = (f"**🎤 今晚机构在开会（{mt['count']} 场"
                + (f"，全库共 {total} 场，此处只列 A 股能参与的" if total else "")
                + "）**")
        els.append(_div(head + "\n\n" + "\n".join(lines) + extra))
        els.append({"tag": "hr"})

    # ── 3. 明天公告先手（隔夜口径）──
    bids = [b for b in p["bids"] if _tradable(b.get("code"))]
    inc = [i for i in p["increases"] if _tradable(i.get("code"))]
    if bids or inc:
        parts = []
        if bids:
            bid_by_ind: dict[str, list[dict]] = defaultdict(list)
            for b in bids:
                bid_by_ind[b.get("industry") or "其他"].append(b)
            bl = []
            for ind, items in sorted(bid_by_ind.items(), key=lambda kv: -len(kv[1])):
                txt = "、".join(
                    f"{i['name']} <text_tag color='{_color_pct(i.get('pct'))}'>"
                    f"{i['pct']:+.2f}%</text_tag>"
                    if i.get("pct") is not None else i["name"]
                    for i in items
                )
                bl.append(f"▸ {ind}：{txt}")
            parts.append("**中标/订单**\n" + "\n".join(bl))
        if inc:
            txt = "、".join(f"{i['name']}{('（' + i['note'] + '）') if i.get('note') else ''}"
                           for i in inc)
            parts.append(f"**增持/回购**\n▸ {txt}")
        els.append(_div(
            "**📋 明天公告先手**\n"
            "（今晚收盘后披露，公告日计到次一交易日——这是隔夜信息，明早开盘前就是先手）\n\n"
            + "\n\n".join(parts)
        ))
        els.append({"tag": "hr"})

    # ── 4. 交叉验证：机构关注 vs 盘面认不认 ──
    cross = p.get("cross") or []
    if cross:
        rows = []
        for c in cross:
            rows.append(
                f"**{c['direction']}**\n"
                f"　机构侧：{c['inst']}\n"
                f"　盘面侧：{c['market']}\n"
                f"　▸ {c['verdict']}"
            )
        els.append(_div(
            "**🔍 机构关注 × 盘面认不认**\n\n" + "\n\n".join(rows)
        ))
        els.append({"tag": "hr"})

    # ── 5. 明天盯什么 ──
    watch = p.get("watch") or []
    if watch:
        els.append(_div(
            "**🎯 明天盯什么**\n\n"
            + "\n".join(f"{n}. {w}" for n, w in enumerate(watch, 1))
        ))

    note = (
        "数据源：Alpha派（会议纪要 / 公告 / 研报）＋ 当日收盘涨停池；"
        "公告日按次一交易日计（收盘后披露惯例）。仅供研究参考，不构成投资建议。"
        f"生成 {p['generated_at'][:16].replace('T', ' ')}"
    )
    return {
        "title": p.get("title") or f"🏛️ 机构方向 {p['date'][5:]} 晚 | 今晚开会·明天先手",
        "color": p.get("color") or "blue",
        "elements": els,
        "note": note,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--input", required=True, help="AI 从 Alpha派 整理的机构侧数据 JSON")
    ap.add_argument("--out", default=None)
    ap.add_argument("--push", action="store_true", help="落盘后调用 push_card.py")
    ap.add_argument("--webhook", default="feishu_webhook_test")
    args = ap.parse_args()

    side = _load(Path(args.input))
    if not isinstance(side, dict):
        print(f"[error] 读不到 {args.input}", file=sys.stderr)
        return 2

    payload = build_payload(args.date, side)
    payload["index"] = side.get("index") or []
    payload["cross"] = side.get("cross") or []
    payload["watch"] = side.get("watch") or []
    payload["market_note"] = side.get("market_note")
    payload["meeting_total"] = side.get("meeting_total")
    payload["title"] = side.get("title")
    payload["color"] = side.get("color")

    out = Path(args.out) if args.out else CACHE / f"inst_direction_card_{args.date}.json"
    card = render_card(payload)
    out.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"卡片已写入 {out}（{len(card['elements'])} 个元素）")

    if args.push:
        cmd = [sys.executable, str(PUSH), str(out), "--webhook", args.webhook]
        print("推送：", " ".join(cmd))
        return subprocess.call(cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
