#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机构热议榜（买方口径）· 时间序列 + 与当日晨报消息叠加

缘起（2026-10-29 用户「当日热榜能做机构性挖掘吗」）：
  蓝宝书晨报是**卖方资讯快照**，topicId 每次抓取重新生成（三次实测完全不重叠），
  无法跨日追踪同一题材 —— 天生只能当日用。
  真正的机构口径在 `hot-topics` 机构热议榜：主键 comboSymbol 稳定、历史可回溯、
  分公募/私募/保险三类，能算连榜天数、排名动量、三类共振。

本脚本做四件事（全部只读）：
  1. 拉机构热议榜（一次调用可回溯数十个自然日，含周末独立榜单）
  2. 计算每只 A 股的连榜天数、最新排名、排名变化、三类机构共振
  3. 与当日蓝宝书晨报叠加：区分「两边都对上」「只有卖方在讲」「只有买方在看」
  4. 输出侧面 JSON + 飞书卡（可选推送）

⛔ 边界与口径：
  - 热议 ≠ 买入。它是关注度/信息热度，不是持仓变动、不是资金净流入。
  - 榜单每类机构最多 20 名（limit 可填到 50 但实际返回 20），20 名外一律「不知道」，
    不得当作「机构没关注」。判「新进/退出」时必须以 20 名为界。
  - 周末（周六/周日）也有完整且互不相同的榜单（实测 10-24/10-25 与 10-23 全不同），
    说明它是按自然日聚合的信息热度。故「在榜 N 天」按自然日计，卡面注明。
  - A/H 双上市（中际旭创 300308.SZ + 03308.HK 等）会出现同名两条，按 comboSymbol
    分别保留：A 股一条入统计，H 股入 `ah_dual` 作跨市场共振佐证。
  - 不写候选项、不改评分/排序/名额/仓位/账本/09:25 终态。缺数据留 null 记 warnings。

用法：
  python scripts/inst_hot_board.py --date 2026-10-29 --fetch --json --card /tmp/x.json
  python scripts/inst_hot_board.py --date 2026-10-29 --push --webhook feishu_webhook_test
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.trading_calendar import is_a_share_trading_day as is_trading_day  # noqa: E402

CACHE = ROOT / "cache"
BOARD_DIR = CACHE / "inst_hot_board"
BLUEBOOK_DIR = CACHE / "alphapai_bluebook"
AP_CLI = Path.home() / ".workbuddy/skills/alphapai-research/scripts/alphapai_client.py"

SCHEMA = "inst_hot_board_v1"
INST_TYPES = ("公募", "私募", "保险")
# 榜单硬上限：实测 limit=50 仍只返回 20 条（榜单本身 20 名）
BOARD_DEPTH = 20
# 默认回看自然日数（≈ 一个自然月，覆盖 20 个交易日左右）
LOOKBACK_DAYS = 28
# 排名变化达到该幅度才算「升温/降温」，避免 1-2 名的日内抖动
RANK_MOVE_MIN = 3

# 可交易池：只留主板 / 创业板（⛔ 无科创板权限，剔 688 / 北交所 / 港美股）
MAIN_PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301")


def board_of(symbol: str) -> str | None:
    c = str(symbol or "").split(".")[0]
    if c.startswith(("600", "601", "603", "605")):
        return "沪主板"
    if c.startswith(("000", "001", "002", "003")):
        return "深主板"
    if c.startswith(("300", "301")):
        return "创业板"
    if c.startswith("688"):
        return "科创板"
    return None


def is_tradable(symbol: str) -> bool:
    return str(symbol or "").split(".")[0].startswith(MAIN_PREFIX)


# ── 1. 拉取 ────────────────────────────────────────────────────────────────
def fetch(start: str, end: str, refresh: bool = False) -> Path:
    """调 Alpha派 CLI 拉机构热议榜。只读列表接口，不消耗 AI 额度。"""
    BOARD_DIR.mkdir(parents=True, exist_ok=True)
    out = BOARD_DIR / f"board_{start.replace('-', '')}_{end.replace('-', '')}.json"
    if out.exists() and not refresh:
        print(f"[fetch] 复用缓存 {out.name}")
        return out
    if not AP_CLI.exists():
        raise SystemExit(f"Alpha派 CLI 不存在：{AP_CLI}")
    cmd = [
        sys.executable, str(AP_CLI), "hot-topics", "board-list",
        "--start", start, "--end", end, "--limit", "50",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise SystemExit(f"拉取失败 rc={proc.returncode}: {proc.stderr[:300]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise SystemExit(f"返回非 JSON：{e} / {proc.stdout[:200]}")
    if payload.get("code") != 200000:
        raise SystemExit(f"接口报错 code={payload.get('code')} msg={payload.get('message')}")
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    days = len(payload.get("data") or [])
    print(f"[fetch] {start}~{end} → {days} 个日期分组 → {out.name}")
    return out


# ── 2. 计算 ────────────────────────────────────────────────────────────────
def load_board(path: Path) -> tuple[dict, dict, list[str]]:
    """返回 (by_day, meta, warnings)。

    by_day[day][symbol][inst] = {"rank": int, "hot": int}
    meta[symbol] = {"name": str, "boards": set(inst), "hcode": str}
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("data") or []
    by_day: dict[str, dict[str, dict[str, dict]]] = {}
    meta: dict[str, dict] = {}
    warnings: list[str] = []
    dup_names: dict[str, set[str]] = defaultdict(set)

    for grp in raw:
        day = str(grp.get("tradingDay") or "")
        if not day:
            continue
        slot = by_day.setdefault(day, {})
        for blk in grp.get("instBoardList") or []:
            inst = str(blk.get("instType") or "")
            items = blk.get("itemList") or []
            ranks = [i.get("rank") for i in items if isinstance(i.get("rank"), int)]
            if ranks and max(ranks) > BOARD_DEPTH:
                warnings.append(f"{day} {inst} 榜单深度 {max(ranks)} 超出预期 {BOARD_DEPTH}")
            for it in items:
                sym = str(it.get("comboSymbol") or "")
                if not sym:
                    continue
                slot.setdefault(sym, {})[inst] = {
                    "rank": it.get("rank"),
                    "hot": it.get("hotCount"),
                }
                m = meta.setdefault(sym, {"name": it.get("name") or "", "boards": set()})
                m["boards"].add(inst)
                dup_names[str(it.get("name") or "")].add(sym)

    # A/H 双上市：同名多个 symbol
    ah: dict[str, list[str]] = {}
    for nm, syms in dup_names.items():
        if len(syms) > 1:
            a = sorted(s for s in syms if s.endswith((".SZ", ".SH")))
            other = sorted(s for s in syms if not s.endswith((".SZ", ".SH")))
            for s in a:
                ah[s] = other
    for sym, m in meta.items():
        m["ah_dual"] = ah.get(sym, [])
    return by_day, meta, warnings


def compute(by_day: dict, meta: dict) -> dict:
    """逐票统计连榜/动量/共振。"""
    days = sorted(by_day)
    if not days:
        return {"days": [], "stocks": [], "resonance": [], "momentum": {}}
    latest, prev = days[-1], (days[-2] if len(days) > 1 else None)

    stocks: dict[str, dict] = {}
    for sym, m in meta.items():
        if not is_tradable(sym):
            continue
        per: dict[str, dict] = {}
        on_days: list[str] = []
        for day in days:
            hit = by_day[day].get(sym)
            if not hit:
                continue
            on_days.append(day)
            for inst, v in hit.items():
                s = per.setdefault(inst, {"days": 0, "last": None, "best_rank": None})
                s["days"] += 1
                s["last"] = day
                r = v.get("rank")
                if isinstance(r, int) and (s["best_rank"] is None or r < s["best_rank"]):
                    s["best_rank"] = r
        if not on_days:
            continue
        # 共振日：同日三类机构同时在榜
        reso = [d for d in days if len(by_day[d].get(sym, {})) == 3]
        # 最新一日状态
        cur = by_day[latest].get(sym, {})
        before = by_day[prev].get(sym, {}) if prev else {}
        delta = {}
        for inst in INST_TYPES:
            r_new = (cur.get(inst) or {}).get("rank")
            r_old = (before.get(inst) or {}).get("rank")
            if isinstance(r_new, int) and isinstance(r_old, int):
                d_ = r_old - r_new  # 正=排名前进
                if abs(d_) >= RANK_MOVE_MIN:
                    delta[inst] = {"from": r_old, "to": r_new, "move": d_}
            elif isinstance(r_new, int) and not isinstance(r_old, int) and prev:
                delta[inst] = {"from": None, "to": r_new, "move": None}
        stocks[sym] = {
            "code": sym,
            "name": m["name"],
            "board": board_of(sym),
            "days_on_board": len(on_days),
            "first_day": on_days[0],
            "last_day": on_days[-1],
            "inst": {k: {"days": v["days"], "last": v["last"], "best_rank": v["best_rank"]}
                     for k, v in per.items()},
            "rank_latest": {k: v.get("rank") for k, v in cur.items()},
            "inst_latest": sorted(cur),
            "delta": delta,
            "resonance_days": len(reso),
            "resonance_last": reso[-1] if reso else None,
            # 共振是否仍在持续（最新一日三类机构同时在榜）—— 断掉的共振不能当当前状态播
            "resonance_now": len(cur) == 3,
            "resonance_gap": (days.index(latest) - days.index(reso[-1])) if reso else None,
            "ah_dual": m.get("ah_dual") or [],
            "on_board_latest": bool(cur),
        }

    ranked = sorted(stocks.values(), key=lambda s: (-s["days_on_board"], s["name"]))
    return {
        "days": days,
        "latest": latest,
        "prev": prev,
        "stocks": ranked,
        "resonance": [s for s in ranked if s["resonance_days"] > 0],
        "momentum": {
            "up": [s for s in ranked if any(d["move"] and d["move"] > 0 for d in s["delta"].values())],
            "down": [s for s in ranked if any(d["move"] and d["move"] < 0 for d in s["delta"].values())],
            "new": [s for s in ranked if s["delta"] and all(d["from"] is None for d in s["delta"].values())],
        },
    }


# ── 3. 与当日晨报叠加 ──────────────────────────────────────────────────────
def load_bluebook(card_date: str, batch: str = "morning") -> tuple[dict, str | None]:
    """读当日蓝宝书缓存，返回 code -> {name, topics:[{topic,heat}]}。"""
    p = BLUEBOOK_DIR / f"{card_date.replace('-', '')}_{batch}.json"
    if not p.exists():
        return {}, f"缺当日晨报缓存 {p.name}（叠加热议榜为空）"
    rows = json.loads(p.read_text(encoding="utf-8"))["data"]["data"]
    out: dict[str, dict] = {}
    for x in rows:
        heat = x.get("heatLevel") or 0
        for s in (x.get("stockTag") or []):
            code = s.get("code")
            if not code or not str(code).endswith((".SZ", ".SH")):
                continue
            rec = out.setdefault(code, {"name": s.get("name") or "", "topics": []})
            rec["topics"].append({"topic": x.get("topicName") or "", "heat": heat})
    for rec in out.values():
        rec["topics"].sort(key=lambda t: -(t["heat"] or 0))
    return out, None


def overlay(hot: dict, bb: dict) -> dict:
    """三分类：两边都对上 / 只有卖方在讲 / 只有买方在看。"""
    hot_map = {s["code"]: s for s in hot["stocks"]}
    both, buy_only = [], []
    for code, rec in hot_map.items():
        if code in bb:
            both.append({
                **rec,
                "bb_topics": bb[code]["topics"][:3],
                "bb_heat": bb[code]["topics"][0]["heat"] if bb[code]["topics"] else None,
            })
        else:
            buy_only.append(rec)
    sell_only = []
    for code, rec in bb.items():
        if code in hot_map:
            continue
        if not is_tradable(code):
            continue
        sell_only.append({
            "code": code,
            "name": rec["name"],
            "board": board_of(code),
            "bb_topics": rec["topics"][:2],
            "bb_heat": rec["topics"][0]["heat"] if rec["topics"] else None,
        })
    both.sort(key=lambda x: (-x["days_on_board"], -(x["bb_heat"] or 0)))
    buy_only.sort(key=lambda x: -x["days_on_board"])
    sell_only.sort(key=lambda x: -(x["bb_heat"] or 0))
    return {"both": both, "buy_only": buy_only, "sell_only": sell_only}


# ── 4. 侧面 JSON ───────────────────────────────────────────────────────────
def build_json(card_date: str, board_path: Path, batch: str) -> dict:
    by_day, meta, warns = load_board(board_path)
    hot = compute(by_day, meta)
    bb, bb_warn = load_bluebook(card_date, batch)
    if bb_warn:
        warns.append(bb_warn)
    ov = overlay(hot, bb)
    days = hot.get("days") or []
    trading_days = [d for d in days if is_trading_day(d)]
    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asof": hot.get("latest"),
        "prev": hot.get("prev"),
        "window": {
            "start": days[0] if days else None,
            "end": days[-1] if days else None,
            "all_days": len(days),
            "trading_days": len(trading_days),
        },
        "board_depth": BOARD_DEPTH,
        "source": "alphapai hot-topics board-list",
        "source_note": "买方机构热议榜（公募/私募/保险），关注度口径，非持仓变动、非资金净流入",
        "counts": {
            "stocks_tradable": len(hot.get("stocks") or []),
            "resonance": len(hot.get("resonance") or []),
            "both": len(ov["both"]),
            "buy_only": len(ov["buy_only"]),
            "sell_only": len(ov["sell_only"]),
        },
        "resonance": hot.get("resonance") or [],
        "overlay": ov,
        "stocks": hot.get("stocks") or [],
        "warnings": warns,
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"inst_hot_board_{card_date}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[json] {out}")
    return payload


# ── 5. 卡片 ────────────────────────────────────────────────────────────────
def _inst_brief(s: dict, base: int | None = None) -> str:
    """三类机构在榜情况压成一段。base=窗口日历日数，用于给出 N/总数。"""
    parts = []
    for inst in INST_TYPES:
        v = s["inst"].get(inst)
        if not v:
            continue
        n = f"{v['days']}/{base}" if base else str(v["days"])
        parts.append(f"{inst}{n}天")
    return "·".join(parts) if parts else "—"


def _topics_brief(s: dict, n: int = 2) -> str:
    """该票在晨报里关联到的题材，最多 n 个（同名去重）。"""
    seen, out = set(), []
    for t in s.get("bb_topics") or []:
        name = t["topic"]
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= n:
            break
    return "　".join(out) if out else "—"


def _delta_brief(s: dict) -> str:
    """昨日较前一日的排名变化。箭头标明方向：↑前进 / ↓后退 / ↑新进。"""
    if not s.get("delta"):
        return ""
    bits = []
    for inst, d in s["delta"].items():
        if d.get("move") is None:
            bits.append(f"{inst}↑新进第{d['to']}位")
        elif d["move"] > 0:
            bits.append(f"{inst}↑{d['from']}→{d['to']}")
        else:
            bits.append(f"{inst}↓{d['from']}→{d['to']}")
    return "　".join(bits)


def load_talk(card_date: str) -> dict:
    """读机构原文观点（inst_hot_talk.py 产出）。没有就返回空，卡面降级不报错。"""
    p = CACHE / f"inst_hot_talk_{card_date}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("stocks") or {}
    except Exception:  # noqa: BLE001
        return {}


def build_card(payload: dict, card_date: str, top: int = 6, talk: dict | None = None) -> dict:
    talk = talk or {}
    ov = payload["overlay"]
    w = payload["window"]
    base = w["all_days"]
    els: list[dict] = []

    els.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content":
                 f"**{card_date[5:].replace('-', ' / ')} · 机构在看什么**\n"
                 f"<font color='grey'>买方热议榜　{w['start']} ~ {w['end']} 共 {base} 天有数据"
                 f"（含 {w['trading_days']} 个交易日）　公募 / 私募 / 保险 各前 {payload['board_depth']} 名</font>\n"
                 f"<font color='grey'>榜单盘后更新，今天（{card_date[5:]}）的榜还没出，"
                 f"下面「今早」指今天的晨报消息，机构数据截至 {w['end']}。</font>"},
    })
    els.append({"tag": "hr"})

    # ── 块一：今早消息 × 机构已在聊（按在榜天数排，不看消息热度）─────────────
    both = ov["both"][:top]
    lines = ["**一、今早消息，机构之前就在聊**"]
    if both:
        lines.append(
            f"<font color='grey'>今早被点名、且近 {base} 天进过机构热议前 20 名的共 "
            f"{len(ov['both'])} 只，按在榜天数取前 {len(both)}；下面的「机构原话」是"
            f"最近一周券商与机构交流的原文摘句：</font>"
        )
        for i, s in enumerate(both, 1):
            lines.append(
                f"<font color='carmine'>{i}.</font>　**{s['name']}**"
                f"<font color='grey'>（{s['board']} {s['code'][:6]}）</font>"
                f"　{s['days_on_board']}/{base} 天在榜"
            )
            lines.append(f"<font color='grey'>机构</font>｜{_inst_brief(s, base)}")
            talks = (talk.get(s["code"]) or {}).get("items") or []
            # 每票只展开 2 条（已按信息量排序，取最能看出逻辑的），否则卡片撑爆
            for t in talks[:2]:
                who = t.get("institution") or "机构交流"
                when = (t.get("date") or "")[5:]
                lines.append(
                    f"<font color='grey'>{who} {when}</font>｜{t.get('snippet') or ''}"
                )
            if not talks:
                lines.append("<font color='grey'>机构原话｜本周没抓到针对这只票的观点</font>")
            db = _delta_brief(s)
            if db:
                lines.append(f"<font color='grey'>较前日</font>｜{db}")
            lines.append(f"<font color='grey'>今早</font>｜{_topics_brief(s)}")
    else:
        lines.append("<font color='grey'>今天没有两边都点到的票。</font>")
    els.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    els.append({"tag": "hr"})

    # ── 块二：三类机构同时在看（必须区分仍在共振 / 已经断掉）──────────────────
    reso_now = [s for s in payload["resonance"] if s.get("resonance_now")]
    reso_off = [s for s in payload["resonance"]
                if not s.get("resonance_now") and s["resonance_days"] >= 2]
    reso_off.sort(key=lambda s: -(s["resonance_days"]))
    reso_now.sort(key=lambda s: (-s["resonance_days"], -s["days_on_board"]))

    lines = ["**二、三类机构同时在看**"]
    if reso_now:
        lines.append("<font color='grey'>最新一日公募、私募、保险都还在榜：</font>")
        for i, s in enumerate(reso_now[:top], 1):
            lines.append(
                f"<font color='carmine'>{i}.</font>　**{s['name']}**"
                f"<font color='grey'>（{s['board']} {s['code'][:6]}）</font>\n"
                f"<font color='grey'>机构</font>｜{_inst_brief(s, base)}"
            )
    else:
        lines.append("<font color='grey'>最新一日没有三类机构同时在榜的票。</font>")
    if reso_off:
        lines.append("<font color='grey'>曾经三类共振、但已经断掉（不是当前状态）：</font>")
        for s in reso_off[:4]:
            gap = s.get("resonance_gap")
            lines.append(
                f"　**{s['name']}**<font color='grey'>（{s['board']} {s['code'][:6]}）</font>"
                f"　共振 {s['resonance_days']} 天，最后一次 {s['resonance_last']}"
                + (f"，已隔 {gap} 天" if gap else "")
            )
    els.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    els.append({"tag": "hr"})

    # ── 块三：昨日排名变化（升温 / 降温 分开）────────────────────────────────
    up = [s for s in payload["stocks"]
          if s.get("on_board_latest") and any((d.get("move") or 0) > 0 for d in s["delta"].values())]
    up.sort(key=lambda s: -max((d.get("move") or 0) for d in s["delta"].values()))
    down = [s for s in payload["stocks"]
            if s.get("on_board_latest") and any((d.get("move") or 0) < 0 for d in s["delta"].values())]
    down.sort(key=lambda s: min((d.get("move") or 0) for d in s["delta"].values()))

    lines = ["**三、较前一日排名变化**"]
    if up:
        lines.append("<font color='grey'>往前走的：</font>")
        for i, s in enumerate(up[:5], 1):
            lines.append(f"<font color='carmine'>{i}.</font>　**{s['name']}**"
                         f"<font color='grey'>（{s['board']}）</font>　{_delta_brief(s)}")
    else:
        lines.append("<font color='grey'>往前走的：无。</font>")
    if down:
        lines.append("<font color='grey'>往后退的：</font>")
        for s in down[:5]:
            lines.append(f"　**{s['name']}**<font color='grey'>（{s['board']}）</font>　{_delta_brief(s)}")
    els.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    els.append({"tag": "hr"})

    # ── 块四：机构一直在聊，今早没有新消息 ────────────────────────────────────
    quiet = [s for s in ov["buy_only"]
             if s["days_on_board"] >= 5 and s.get("on_board_latest")]
    quiet.sort(key=lambda s: -s["days_on_board"])
    lines = ["**四、机构一直在聊，今早没有新消息**"]
    if quiet:
        lines.append("<font color='grey'>持续在榜但晨报没点名，消息面还没发酵：</font>")
        lines.append("　".join(
            f"**{s['name']}**<font color='grey'>（{s['board']}）{s['days_on_board']}/{base}</font>"
            for s in quiet[:8]
        ))
    else:
        lines.append("<font color='grey'>无。</font>")
    els.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    els.append({"tag": "hr"})

    # ── 块五：只有卖方在讲（按题材分组，避免同一题材刷屏）────────────────────
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for s in ov["sell_only"]:
        key = s["bb_topics"][0]["topic"] if s["bb_topics"] else "未归类"
        by_topic[key].append(s)
    topic_order = sorted(by_topic, key=lambda k: -(by_topic[k][0]["bb_heat"] or 0))

    lines = ["**五、今早点名了，但买方近一个月没聊过**"]
    if topic_order:
        lines.append("<font color='grey'>同一个题材最多列 2 只，看的是买方需不需要它：</font>")
        for t in topic_order[:3]:
            group = by_topic[t][:2]
            lines.append(f"<font color='grey'>今早</font>｜{t}"
                         f"<font color='grey'>（热度 {group[0]['bb_heat']}）</font>")
            lines.append("　" + "　".join(
                f"**{s['name']}**<font color='grey'>（{s['board']} {s['code'][:6]}）</font>"
                for s in group
            ))
    else:
        lines.append("<font color='grey'>无。</font>")
    els.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    els.append({"tag": "hr"})
    els.append({"tag": "note", "elements": [{"tag": "plain_text", "content":
        f"资料来源：Alpha派机构热议榜（公募/私募/保险）＋蓝宝书 {card_date} 晨报。"
        "热议是关注度，不等于买入，也不是资金净流入；每类榜单只取前 20 名，20 名外视为了解不足，"
        "不当作机构没关注。「在榜天数」按日历日计，周末另有独立榜单。"
        "不构成买卖建议。"}]})

    return {
        "title": f"机构在看什么·全量 | {card_date[5:]}",
        "color": "carmine",
        "elements": els,
    }


def push_card(card_path: Path, card_date: str, webhook: str | None) -> None:
    cmd = [sys.executable, str(ROOT / "scripts/push_card.py"), str(card_path)]
    if webhook:
        cmd += ["--webhook", webhook]
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    if rc != 0:
        raise SystemExit(f"推送失败 rc={rc}")
    print(f"[push] 已推送 {card_date} → {webhook or 'auto'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="卡片业务日期，默认今天")
    ap.add_argument("--batch", default="morning", choices=["morning", "noon", "evening"])
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DAYS, help="回看自然日数")
    ap.add_argument("--fetch", action="store_true", help="先调接口拉取榜单")
    ap.add_argument("--refresh", action="store_true", help="配合 --fetch 强制重拉")
    ap.add_argument("--json", action="store_true", help="输出侧面 JSON")
    ap.add_argument("--card", default=None, help="输出卡片 JSON 到该路径")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--push", action="store_true", help="推送（建议配 --webhook test）")
    ap.add_argument("--webhook", default=None, help="feishu_webhook_test / feishu_webhook_prod")
    args = ap.parse_args()

    card_date = args.date or date.today().isoformat()
    end = card_date
    # 榜单滞后一天（今天盘后才有今天的榜），回看窗口从业务日往前推
    start = (date.fromisoformat(card_date) - timedelta(days=args.lookback)).isoformat()

    if args.fetch:
        board_path = fetch(start, end, refresh=args.refresh)
    else:
        picks = sorted(BOARD_DIR.glob("board_*.json"))
        if not picks:
            raise SystemExit("没有榜单缓存，请加 --fetch")
        board_path = picks[-1]
        print(f"[load] 使用缓存 {board_path.name}")

    if args.json or args.card or args.push:
        payload = build_json(card_date, board_path, args.batch)
        print(f"[stat] 在榜 {payload['counts']['stocks_tradable']} 只 · "
              f"共振 {payload['counts']['resonance']} · "
              f"两边对上 {payload['counts']['both']} · "
              f"只有买方 {payload['counts']['buy_only']} · "
              f"只有卖方 {payload['counts']['sell_only']}")
        for warn in payload["warnings"]:
            print(f"[warn] {warn}")

        if args.card or args.push:
            talk = load_talk(card_date)
            if not talk:
                print("[warn] 没有机构原话缓存（inst_hot_talk_<date>.json），"
                      "先去跑 scripts/inst_hot_talk.py 拿内容；本次卡面只放名单")
            card = build_card(payload, card_date, args.top, talk=talk)
            out = Path(args.card or "/tmp/inst_hot_card.json")
            out.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
            size = len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
            print(f"[card] {out}  elements={len(card['elements'])}  bytes={size}")
            if args.push:
                push_card(out, card_date, args.webhook)


if __name__ == "__main__":
    main()
