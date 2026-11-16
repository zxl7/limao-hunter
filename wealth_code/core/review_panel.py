#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧭 复盘三块面板（2026-11-14 用户「类似图表复盘我们也做一个」）

三块结构（对齐用户截图）：
1. **风险**：明天会不会成批跌停（跌停数/炸板率/核按钮/最高板/上涨家数/指数）
2. **赚钱效应**：昨日涨停池今日均幅 + 红盘率 + 接力最强最弱 + 隔夜最强最弱
3. **主线与板块**：每条线 涨停家数 / 最高板 / 启动第几天 / 强度

数据源（全部本机已有）：
- `cache/pulse_snapshots/latest.json`：index(三大指数) / limit_down / broken / hot_spots
- `cache/feedback_matrix.json`：promote_rate / avg_pct / up_rate / nuke_count
- `cache/zt_history/zt_<date>.json` + `cache/theme_snapshot/theme_<date>.json`
- `cache/kline_qfq/<code>.json`（收盘后算个股当日涨跌）

⛔ 只读汇总，不改任何判据。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_BJ = timezone(timedelta(hours=8))


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def _days() -> list[str]:
    return sorted(p.stem.replace("zt_", "") for p in (ROOT / "cache" / "zt_history").glob("zt_*.json"))


def _pct_of(day: str) -> dict[str, float]:
    """当日个股涨跌幅（收盘后可用；盘中缺失）。"""
    out: dict[str, float] = {}
    for f in (ROOT / "cache" / "kline_qfq").glob("*.json"):
        try:
            bars = json.loads(f.read_text(encoding="utf-8"))["bars"]
        except Exception:  # noqa: BLE001
            continue
        # kline 没有 prev_close 字段 → 用上一根 bar 的收盘
        for i, b in enumerate(bars):
            if str(b.get("date"))[:10] == day and i > 0:
                prev = float(bars[i - 1].get("close") or 0)
                if prev:
                    out[f.stem] = round((float(b.get("close") or 0) / prev - 1) * 100, 2)
                break
    return out


def _theme(day: str, code: str) -> str:
    snap = _load(ROOT / "cache" / "theme_snapshot" / f"theme_{day}.json", {}).get("mapping") or {}
    m = snap.get(str(code)) or {}
    return str(m.get("theme_base") or m.get("theme") or "—")


def risk_block(pulse: dict, fb: dict, prev_zt: list[dict], today_zt: list[dict]) -> dict:
    idx = pulse.get("index") or []
    ld = int((pulse.get("limit_down") or {}).get("count") or 0)
    broken = int((pulse.get("broken") or {}).get("count") or 0)
    total = len(today_zt) or 1
    broken_rate = round(broken / (broken + total) * 100, 1)
    nuke = int(fb.get("nuke_count") or 0)
    promote = fb.get("promote_rate")
    prev_max = max((int(r.get("boards") or 0) for r in prev_zt), default=0)
    today_max = max((int(r.get("boards") or 0) for r in today_zt), default=0)
    sig = [
        {"name": "跌停家数多", "hit": ld >= 10, "value": f"{ld} 家"},
        {"name": "炸板率高", "hit": broken_rate >= 30, "value": f"{broken_rate}%"},
        {"name": "核按钮多", "hit": nuke >= 10, "value": f"{nuke} 家"},
        {"name": "最高板走低", "hit": today_max < prev_max, "value": f"{prev_max}→{today_max} 板"},
        {"name": "指数转跌", "hit": bool(idx) and all(float(i.get("pct") or 0) < 0 for i in idx),
         "value": "、".join(f"{i.get('name')}{float(i.get('pct') or 0):+.2f}%" for i in idx) or "缺数据"},
        {"name": "晋级率低", "hit": isinstance(promote, (int, float)) and promote < 25,
         "value": f"{promote}%" if isinstance(promote, (int, float)) else "缺数据"},
    ]
    hits = sum(1 for s in sig if s["hit"])
    level = "高" if hits >= 4 else "中" if hits >= 2 else "低"
    return {"level": level, "hits": hits, "signals": sig}


def money_block(fb: dict, prev_zt: list[dict], pct_today: dict[str, float]) -> dict:
    def avg(rows: list[dict]) -> float | None:
        v = [pct_today[str(r.get("code"))] for r in rows if str(r.get("code")) in pct_today]
        return round(sum(v) / len(v), 2) if v else None

    prev_max = max((int(r.get("boards") or 0) for r in prev_zt), default=0)
    top = [r for r in prev_zt if int(r.get("boards") or 0) == prev_max]
    two = [r for r in prev_zt if int(r.get("boards") or 0) == 2]
    relay_avg, two_avg = avg(top), avg(two)
    def best_worst(rows: list[dict]) -> tuple[dict | None, dict | None]:
        vals = [(str(r.get("code")), str(r.get("name")), pct_today[str(r.get("code"))])
                for r in rows if str(r.get("code")) in pct_today]
        if not vals:
            return None, None
        vals.sort(key=lambda x: x[2])
        return ({"name": vals[-1][1], "pct": vals[-1][2]}, {"name": vals[0][1], "pct": vals[0][2]})
    rb, rw = best_worst(top)
    ob, ow = best_worst(two)
    # feedback_matrix 缺失时（盘中/未跑）→ 用昨日池全样本自算
    _vals = [pct_today[str(r.get("code"))] for r in prev_zt if str(r.get("code")) in pct_today]
    pool_avg = fb.get("avg_pct")
    if pool_avg is None and _vals:
        pool_avg = round(sum(_vals) / len(_vals), 2)
    red = fb.get("up_rate")
    if red is None and _vals:
        red = round(100 * sum(1 for v in _vals if v > 0) / len(_vals), 1)
    return {
        "zt_pool_avg": pool_avg,
        "red_ratio": red,
        "relay_max_boards": prev_max,
        "relay_avg": relay_avg, "relay_best": rb, "relay_worst": rw,
        "overnight_avg": two_avg, "overnight_best": ob, "overnight_worst": ow,
    }


def theme_block(day: str, zt: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for r in zt:
        by.setdefault(_theme(day, str(r.get("code"))), []).append(r)
    # 连续出现天数（这条线连续有≥3家涨停）
    hist = {}
    for d in _days()[-10:]:
        cnt: dict[str, int] = {}
        for r in _load(ROOT / "cache" / "zt_history" / f"zt_{d}.json", {}).get("zt_list") or []:
            t = _theme(d, str(r.get("code")))
            cnt[t] = cnt.get(t, 0) + 1
        hist[d] = cnt
    out = []
    for name, arr in by.items():
        mx = max(int(r.get("boards") or 0) for r in arr)
        day_no = 0
        for d in sorted(hist)[::-1]:
            if hist[d].get(name, 0) >= 3:
                day_no += 1
            else:
                break
        fbt = min((str(r.get("fbt") or "999999") for r in arr), default="")
        out.append({"name": name, "zt": len(arr), "max_boards": mx,
                    "day": day_no or 1, "first_seal": fbt[:4],
                    "strength": round(min(1.0, len(arr) / 8 + mx / 10), 2)})
    out.sort(key=lambda x: (-x["zt"], -x["max_boards"]))
    return out


def _extras(day: str) -> dict:
    try:
        from core import review_extras as rx
        return rx.build(day)
    except Exception:  # noqa: BLE001
        return {}


def build(day: str | None = None) -> dict:
    days = _days()
    day = day or (days[-1] if days else datetime.now().strftime("%Y-%m-%d"))
    prev = [d for d in days if d < day]
    prev_day = prev[-1] if prev else None
    zt_today = _load(ROOT / "cache" / "zt_history" / f"zt_{day}.json", {}).get("zt_list") or []
    zt_prev = _load(ROOT / "cache" / "zt_history" / f"zt_{prev_day}.json", {}).get("zt_list") or [] if prev_day else []
    pulse = _load(ROOT / "cache" / "pulse_snapshots" / "latest.json", {})
    fb = _load(ROOT / "cache" / "feedback_matrix.json", {})
    pct = _pct_of(day)
    return {
        "date": day, "prev": prev_day,
        # 🕒 生成时间：前端标「数据生成于 X」，避免把旧数据当今天的
        "generated_at": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M"),
        "risk": risk_block(pulse, fb, zt_prev, zt_today),
        "money": money_block(fb, zt_prev, pct),
        "themes": theme_block(day, zt_today)[:8],
        # 🏗 板块梯队（家数 + 高度 + 梯队分布）：复盘判断「这条线是不是真强」的依据
        "ladder": ladder_block(day, zt_today)[:10],
        "commentary": commentary(day),
        "pool": pool_block(day),
        # 👑 高标处境 + ⚠️ 明日跌停预判（懒加载，避免与 review_extras 循环 import）
        "high_board": _extras(day).get("high_board"),
        "dump_risk": _extras(day).get("dump_risk"),
        "sources": {"pulse": bool(pulse), "feedback": bool(fb), "kline": bool(pct)},
    }


def commentary(day: str) -> list[dict]:
    """读取 LLM 复盘卡（`outputs/review_<date>.json`）→ 分段文本。"""
    p = ROOT / "outputs" / f"review_{day}.json"
    try:
        card = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    def text_of(el) -> str:
        if not isinstance(el, dict):
            return ""
        t = el.get("text")
        if isinstance(t, dict):
            return str(t.get("content") or "")
        if isinstance(t, list):
            return "".join(text_of(x) for x in t)
        return ""
    import re as _re
    # 「**📉 ① 情绪演变**」这种才当标题；其余一律当正文（避免把 `**情绪分**　…` 误判成标题）
    head_re = _re.compile(r"^\*\*[^*]{0,24}[①②③④⑤⑥⑦⑧⑨⑩][^*]{0,24}\*\*$")
    sections: list[dict] = []
    cur: dict | None = None
    for el in card.get("elements") or []:
        txt = text_of(el).strip()
        if not txt:
            continue
        if head_re.match(txt):
            cur = {"heading": txt.strip("*").strip(), "lines": []}
            sections.append(cur)
        elif cur is None:
            cur = {"heading": "总览", "lines": []}
            sections.append(cur)
            cur["lines"].append(txt)
        else:
            cur["lines"].append(txt)
    return [{"heading": s["heading"], "lines": s["lines"]} for s in sections if s["lines"] or s["heading"]]



def ladder_block(day: str, zt: list[dict]) -> list[dict]:
    """🏗 板块梯队（2026-11-16 用户「板块梯队+强度就是短线最重要的指标，复盘也要作为依据」）

    每条线给出**梯队长什么样**，而不是只有家数：
      · 首板 / 2板 / 3板+ 各几家  → 看这条线是「一堆首板」（一日游风险）还是「有高度」
      · 最高板                     → 天花板在哪
      · 强度                       → 家数 + 高度合成（只做排序，不当买卖信号）
    数据只读当日涨停池 + 当日题材快照，⛔ 不推算。
    """
    by: dict[str, list[dict]] = {}
    for r in zt:
        by.setdefault(_theme(day, str(r.get("code"))), []).append(r)
    out = []
    for name, arr in by.items():
        boards = [int(r.get("boards") or 1) for r in arr]
        l1 = sum(1 for b in boards if b <= 1)
        l2 = sum(1 for b in boards if b == 2)
        l3 = sum(1 for b in boards if b >= 3)
        mx = max(boards)
        out.append({
            "theme": name, "mates": len(arr), "max_boards": mx,
            "l1": l1, "l2": l2, "l3": l3,
            "strength": round(len(arr) + mx * 1.5 + l3 * 1.0, 1),
        })
    out.sort(key=lambda x: (-x["strength"], -x["mates"]))
    return out


def pool_block(day: str) -> dict:
    """09:25 冻结件 + 候选池：今天推了谁、买了谁。"""
    fz = _load(ROOT / "wealth_code" / "cache" / f"pool_call-{day}.json", {})
    views = fz.get("views") or {}
    buys = [{"name": v.get("name"), "code": c, "cap": v.get("position_cap"),
             "gap": v.get("gap_pct")} for c, v in views.items() if v.get("view") == "participate"]
    watch = [{"name": v.get("name"), "code": c} for c, v in views.items() if v.get("view") == "watch"]
    cand = _load(ROOT / "wealth_code" / "cache" / f"candidates-{day}.json", {})
    return {
        "relay": len(cand.get("relay") or []),
        "watch": len(cand.get("watch") or []),
        "buyable": buys[:6],
        "observe": watch[:8],
        "stance": fz.get("stance"),
        "quota": fz.get("effective_quota") or fz.get("quota"),
    }
