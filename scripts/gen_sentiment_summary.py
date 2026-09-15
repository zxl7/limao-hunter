#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_sentiment_summary.py — 5 日交易日情绪汇总生成器（2026-08-16）

数据源（全部为已沉淀的结构化快照，非 AI 手工输入）：
- cache/short_term/daily/emotion_YYYY-MM-DD.json  情绪数值日快照（capture_daily 归档 + 8/10-8/14 种子补录）
- cache/eruption_history/eruption_YYYY-MM-DD.json 题材爆发快照（主线程材）
- cache/newsflash_history/daily_YYYY-MM-DD.json   外部新闻日归档（催化维度）

产出：
- cache/short_term/summary_5d.json    结构化 5 日汇总（供盘前/盘后卡片 Read 直接引用）
- 刷新 cache/short_term/短期情绪周期判断.md 的「## 5日情绪演变表」数据表（幂等替换；
  周期定性段落仍由盘前/盘后 AI 维护，本脚本只更新纯数据表并标注自动生成）

用法：python3 scripts/gen_sentiment_summary.py [YYYY-MM-DD]   # 缺省=今天
挂载：capture_daily.py 盘后链第 5 步。
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

CACHE = Path("/Users/zxl/WorkBuddy/狸猫猎手/cache")
DAILY_DIR = CACHE / "short_term" / "daily"
ERUPT_DIR = CACHE / "eruption_history"
NEWS_DIR = CACHE / "newsflash_history"
MD_PATH = CACHE / "short_term" / "短期情绪周期判断.md"
OUT_PATH = CACHE / "short_term" / "summary_5d.json"
TR_PATH = CACHE / "theme_rotation.json"  # P2-20① 轮动反哺输入（theme_rotation 盘后链产出）
WINDOW = 5    # 情绪演变近端窗口（最近 5 交易日·卡面「📉情绪演变」用）
WINDOW10 = 10  # 归纳周期窗口（2026-08-25 用户「以10个交易日为一个归纳周期」：10日数据归纳
#              ——情绪推进/题材轮动/资金走向的周期基准；双窗口并存，days 仍 5 日，
#              summary10 段单独输出 10 日轨迹供复盘/AI 归纳查阅）


def _recent(paths: Path, prefix: str, n: int):
    """取最近 n 个交易日文件（升序）"""
    dated = []
    for p in paths.glob(f"{prefix}*.json"):
        stem = p.name[len(prefix):-5]
        try:
            dd = date.fromisoformat(stem)
        except Exception:
            continue
        dated.append((dd, p))
    dated.sort(key=lambda x: x[0], reverse=True)
    dated = dated[:n]
    dated.sort(key=lambda x: x[0])
    return dated


def _load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _main_themes(eruption_files: dict):
    """从 eruption 快照取当日 Top3 题材名"""
    for dd in sorted(eruption_files, reverse=True):
        data = _load(eruption_files[dd])
        if not data:
            continue
        th = [t.get("name", "") for t in (data.get("themes") or []) if t.get("name")]
        return th[:3]
    return []


def _cycle_hint(days):
    """简单规则周期提示（AI 定调的机械输入，非最终结论）"""
    if not days:
        return "数据不足"
    scores = [d.get("sentiment_score") for d in days]
    scores = [s for s in scores if s is not None]
    last3 = scores[-3:] if len(scores) >= 3 else scores
    if len(last3) >= 3 and last3[-1] <= 2 and all(x <= last3[0] + 1 for x in last3):
        return "退潮/冰点（连续低位）"
    if len(last3) >= 3 and last3[-1] > last3[0] + 1:
        return "修复/升温（近3日回升）"
    if len(last3) >= 2 and last3[-1] < last3[0] - 1:
        return "退潮恶化（近3日走低）"
    if scores and scores[-1] >= 6:
        return "亢奋/修复（情绪高位）"
    return "分歧/震荡（方向待确认）"


def _rotation_digest():
    """P2-20① 轮动反哺情绪：把 theme_rotation 的轮动状态量化为情绪周期的机械输入维度。

    修复三段式漏洞①（轮动结果未反哺情绪）——回流候选数/新爆发数/相位分布/题材涨停趋势
    作为情绪周期提示的输入项，不再与情绪汇总各自孤立。纯统计不产结论；文件缺失返回 None。
    """
    d = _load(TR_PATH) if TR_PATH.exists() else None
    if not d:
        return None
    cands = d.get("candidates") or []
    phases: dict[str, int] = {}
    for c in cands:
        ph = c.get("phase") or "未知"
        phases[ph] = phases.get(ph, 0) + 1
    dd = list(d.get("data_days") or [])
    recent2 = set(dd[-2:]) if dd else set()          # 最近 2 个交易日内的 boom = 新爆发
    new_boom = [c.get("theme") for c in cands if c.get("boom_day") in recent2 and c.get("theme")]
    trends = d.get("sector_zt_trends") or []
    mc = d.get("market_context") or {}
    return {
        "date": d.get("date"),
        "re_entry_count": len(cands),
        "new_boom_count": len(new_boom),
        "new_boom_themes": new_boom[:5],
        "phase_dist": phases,
        "top_themes": [c.get("theme") for c in cands if c.get("theme")][:5],
        "sector_zt_up": len([t for t in trends if t.get("dir") == "up"]),
        "sector_zt_down": len([t for t in trends if t.get("dir") == "down"]),
        "market_sentiment": mc.get("sentiment_score"),
    }


def _cycle_why(days, news_rows, rotation):
    """P2-20② 情绪因果链：把情绪序列变化与催化/轮动显式关联（机械归因·供 AI 定调引用）。

    修复三段式漏洞②（情绪汇总无"事件因果"）——输出 direction/delta/drivers/text，
    drivers 只列真实发生变化的维度（无变化不硬凑），text 可直接作为卡面「为何变」一行。
    数据不足（<2 个有分日）返回 None，不编造。
    """
    scored = [d for d in days if d.get("sentiment_score") is not None]
    if len(scored) < 2:
        return None
    prev, cur = scored[-2], scored[-1]
    delta = round((cur.get("sentiment_score") or 0) - (prev.get("sentiment_score") or 0), 1)
    direction = "up" if delta > 0.3 else ("down" if delta < -0.3 else "flat")
    drivers: list[str] = []
    # ① 情绪自身维度（只列显著变化）
    if cur.get("nuke_count") is not None and prev.get("nuke_count") is not None:
        dn = (cur["nuke_count"] or 0) - (prev["nuke_count"] or 0)
        if abs(dn) >= 2:
            drivers.append(f"核按钮 {prev['nuke_count']}→{cur['nuke_count']}")
    if cur.get("promote_rate") is not None and prev.get("promote_rate") is not None:
        if abs((cur["promote_rate"] or 0) - (prev["promote_rate"] or 0)) >= 3:
            drivers.append(f"晋级率 {prev['promote_rate']}%→{cur['promote_rate']}%")
    if cur.get("zt_count") is not None and prev.get("zt_count") is not None:
        if abs((cur["zt_count"] or 0) - (prev["zt_count"] or 0)) >= 8:
            drivers.append(f"涨停 {prev['zt_count']}→{cur['zt_count']}")
    # ② 主线切换（题材维度）
    pth = (prev.get("main_themes") or [])[:1]
    cth = (cur.get("main_themes") or [])[:1]
    if pth and cth and pth != cth:
        drivers.append(f"主线切换 {pth[0]}→{cth[0]}")
    # ③ 轮动维度（P2-20① 反哺：回流候选/新爆发）
    if rotation:
        if rotation.get("new_boom_count"):
            themes = "、".join(rotation.get("new_boom_themes") or [])[:20]
            drivers.append(f"新爆发题材 {rotation['new_boom_count']} 个（{themes}）")
        if rotation.get("re_entry_count"):
            drivers.append(f"回流候选 {rotation['re_entry_count']} 个")
    # ④ 催化维度（当日新闻标题 Top1）
    if news_rows:
        last = news_rows[-1]
        if last.get("date") == cur.get("date") and last.get("titles"):
            drivers.append(f"催化「{str(last['titles'][0])[:26]}」")

    def _fmt(v):
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)

    arrow = f"{_fmt(prev['sentiment_score'])}→{_fmt(cur['sentiment_score'])}"
    sign = "+" if delta > 0 else ""
    text = f"情绪 {arrow}（{sign}{delta}）" + ("：" + "；".join(drivers[:4]) if drivers else "（维度无显著变化）")
    return {"date": cur.get("date"), "direction": direction, "delta": delta,
            "drivers": drivers, "text": text}


# ── 情绪评估窗口分层（2026-09-10 用户拍板·窗口实测校准）─────────────────────
# 实测（23 交易日情绪序列）：当日值→次日涨停溢价 r=0.458（最强·执行层）；
# 3 日窗 std 1.57 / 方向翻转仅 5 次（噪声最少）且拐点滞后仅 1 日（方向层）；
# 5 日窗=MA5 纪律/一周节拍（纪律层）；10 日窗 std 0.53 几乎贴平（仅周期定位·不可做买卖触发）。
# 分工：当日=触发 · 3日=拐点确认 · 5日=纪律 · 10日=归纳背景。
MA3_WINDOW = 3


def ma3(seq: list) -> list:
    """3 日滚动均值（None 安全·逐日对齐原序列）。"""
    out = []
    for i in range(len(seq)):
        vals = [v for v in seq[max(0, i - MA3_WINDOW + 1):i + 1] if isinstance(v, (int, float))]
        out.append(round(sum(vals) / len(vals), 2) if vals else None)
    return out


def turn_signal(seq: list) -> dict:
    """3 日窗拐点判定：ma3 连续 2 日上行=repair(修复确认)/连续下行=ebb(退潮确认)/否则 flat。
    附当日值 vs ma3 背离提示（当日显著强于窗均值=单日脉冲，需次日确认）。"""
    ma = ma3(seq)
    vals = [v for v in ma if v is not None]
    if len(vals) < 3:
        return {"dir": "unknown", "label": "样本不足", "ma3_last": vals[-1] if vals else None}
    a, b, c = vals[-3], vals[-2], vals[-1]
    if b > a and c > b:
        d, label = "repair", "修复确认（3日窗连续上行）"
    elif b < a and c < b:
        d, label = "ebb", "退潮确认（3日窗连续下行）"
    else:
        d, label = "flat", "方向未明（3日窗震荡）"
    cur = seq[-1] if seq and isinstance(seq[-1], (int, float)) else None
    diverg = None
    if cur is not None and c is not None:
        if cur - c >= 2.0:
            diverg = f"当日({cur})显著强于3日窗({c})·单日脉冲待次日确认"
        elif c - cur >= 2.0:
            diverg = f"当日({cur})显著弱于3日窗({c})·恐慌杀跌待修复确认"
        # ⛔ 多维核验（推断多维核验铁律）：名义方向与当日背离时不得下单一结论
        # （实证 09-09：3日窗 4.17→5.37→5.7 名义上行，但当日 2.0 已转弱=假修复）
        if d == "repair" and cur - c <= -2.0:
            d, label = "ebb_warn", "3日窗名义上行但当日转弱·假修复风险"
        elif d == "ebb" and cur - c >= 2.0:
            d, label = "repair_warn", "3日窗名义下行但当日转强·修复前兆（待确认）"
    return {"dir": d, "label": label, "ma3_last": c,
            "sentiment_seq_last3": [v for v in seq[-3:]], "divergence": diverg}


def build():
    today = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    emo_files = {str(d): p for d, p in _recent(DAILY_DIR, "emotion_", WINDOW)}
    erupt_files = {str(d): p for d, p in _recent(ERUPT_DIR, "eruption_", WINDOW)}
    news_files = {str(d): p for d, p in _recent(NEWS_DIR, "daily_", WINDOW)} if NEWS_DIR.exists() else {}

    days = []
    for ds in sorted(emo_files):
        e = _load(emo_files[ds]) or {}
        if not e:
            continue
        row = {
            "date": ds,
            "sentiment_score": e.get("sentiment_score"),
            "sentiment_label": e.get("sentiment_label"),
            "zt_count": e.get("zt_count"),
            "promote_rate": e.get("promote_rate"),
            "nuke_count": e.get("nuke_count"),
            "max_boards": e.get("max_boards"),
            "seed": bool(e.get("seed")),
        }
        # 主线：eruption 快照对齐
        if ds in erupt_files:
            er = _load(erupt_files[ds]) or {}
            row["main_themes"] = [t.get("name", "") for t in (er.get("themes") or []) if t.get("name")][:3]
        days.append(row)

    # 新闻催化维度（最近 5 日）
    news_rows = []
    for ds in sorted(news_files):
        nf = _load(news_files[ds]) or {}
        titles = []
        for it in (nf.get("newsflash") or []):
            t = it.get("title") or it.get("text") or ""
            if t and t not in titles:
                titles.append(t)
        if titles:
            news_rows.append({"date": ds, "count": len(nf.get("newsflash") or []), "titles": titles[:8]})

    summary = {
        "generated_ts": __import__("datetime").datetime.now().isoformat(),
        "window_days": WINDOW,
        "days": days,
        "trajectory": {
            "sentiment_seq": [d.get("sentiment_score") for d in days],
            "promote_seq": [d.get("promote_rate") for d in days],
            "nuke_seq": [d.get("nuke_count") for d in days],
            "zt_seq": [d.get("zt_count") for d in days],
            # 窗口分层（2026-09-10 用户拍板·当日=执行/3日=拐点确认/5日=纪律/10日=归纳）
            "sentiment_ma3": ma3([d.get("sentiment_score") for d in days]),
            "ma3_window": MA3_WINDOW,
            "turn_signal": turn_signal([d.get("sentiment_score") for d in days]),
        },
        "cycle_hint": _cycle_hint(days),
        "news": news_rows,
    }
    # ── P2-20 情绪因果链 + 轮动反哺（2026-09-10 落地）──
    # ① rotation：theme_rotation 轮动状态（回流候选/新爆发/相位/题材涨停趋势）作为情绪周期输入维度；
    # ② cycle_why：情绪序列变化的机械归因（情绪为何变），供盘前/复盘 AI 定调直接引用。
    # 两者均为纯增量键，缺失时跳过（不破坏既有消费方契约）。
    _rotation = _rotation_digest()
    if _rotation:
        summary["rotation"] = _rotation
    _why = _cycle_why(days, news_rows, _rotation)
    if _why:
        summary["cycle_why"] = _why
    # 10 交易日归纳窗口（2026-08-25 用户「以10个交易日为一个归纳周期」）：
    # 情绪/晋级率/核按钮/最高板 的 10 日轨迹 + 周期演进（冰点持续天数/修复拐点等），
    # 供复盘/盘前 AI 归纳查阅（沉淀数据→每日推送时查阅总结性言论/题材）。
    emo10 = {str(d): p for d, p in _recent(DAILY_DIR, "emotion_", WINDOW10)}
    days10 = []
    for ds in sorted(emo10):
        e = _load(emo10[ds]) or {}
        if not e:
            continue
        days10.append({
            "date": ds,
            "sentiment_score": e.get("sentiment_score"),
            "sentiment_label": e.get("sentiment_label"),
            "promote_rate": e.get("promote_rate"),
            "nuke_count": e.get("nuke_count"),
            "max_boards": e.get("max_boards"),
        })
    summary["summary10"] = {
        "window_days": WINDOW10,
        "days": days10,
        "trajectory": {
            "sentiment_seq": [d.get("sentiment_score") for d in days10],
            "promote_seq": [d.get("promote_rate") for d in days10],
            "nuke_seq": [d.get("nuke_count") for d in days10],
            "max_boards_seq": [d.get("max_boards") for d in days10],
        },
        "cycle": _cycle_hint(days10),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    _refresh_md(days, _why, _rotation)
    print(f"[gen_sentiment_summary] {today} 汇总 {len(days)} 个交易日 → {OUT_PATH.name} | 周期提示: {summary['cycle_hint']} | 10日归纳窗口: {len(days10)} 交易日"
          + (f" | 因果: {_why['text'][:60]}" if _why else ""))
    return summary


def _refresh_md(days, why=None, rotation=None):
    """幂等刷新 短期情绪周期判断.md 的「## 5日情绪演变表」数据表（含 P2-20 因果/轮动注脚）"""
    if not days or not MD_PATH.exists():
        return
    text = MD_PATH.read_text(encoding="utf-8")
    _seq = [d.get("sentiment_score") for d in days]
    _ma3 = ma3(_seq)
    _turn = turn_signal(_seq)
    lines = ["## 5日情绪演变表", "", "> 🤖 自动生成（gen_sentiment_summary.py · 数据源 emotion_*.json 结构化快照）", ""]
    lines.append("| 日期 | 情绪分 | 3日窗 | 涨停 | 晋级率 | 核按钮 | 最高板 | 主线 |")
    lines.append("|------|--------|-------|------|--------|--------|--------|------|")
    for _i, d in enumerate(days):
        themes = "、".join((d.get("main_themes") or [])[:3]) or "—"
        if len(themes) > 22:
            themes = themes[:21] + "…"
        _m = _ma3[_i] if _i < len(_ma3) else None
        lines.append(
            f"| {d['date'][5:].replace('-','/')} | {d.get('sentiment_score')}/10·{d.get('sentiment_label') or '—'} "
            f"| {_m if _m is not None else '—'} "
            f"| {d.get('zt_count') or '—'} | {d.get('promote_rate') or '—'}% | {d.get('nuke_count') or '—'} "
            f"| {d.get('max_boards') or '—'} | {themes} |"
        )
    block = "\n".join(lines)
    # 🌡️ 3 日窗拐点（2026-09-10 窗口分层·当日=执行/3日=拐点确认/5日=纪律/10日=归纳）
    if _turn.get("dir") and _turn["dir"] != "unknown":
        block += (f"\n\n> 🌡️ 3日窗拐点：**{_turn.get('label')}**"
                  f"（ma3 {_turn.get('ma3_last')}·当日 {_seq[-1] if _seq else '—'}）")
        if _turn.get("divergence"):
            block += f"｜{_turn['divergence']}"
    # P2-20 注脚：情绪因果链 + 轮动反哺（机械归因·供 AI 定调引用）
    if why and why.get("text"):
        block += f"\n\n> 🧭 情绪因果（{why.get('date')}）：{why['text']}"
    if rotation:
        _ph = "、".join(f"{k}{v}" for k, v in (rotation.get("phase_dist") or {}).items())
        _ztd = ""
        if (rotation.get("sector_zt_up") or 0) + (rotation.get("sector_zt_down") or 0) > 0:
            _ztd = f" · 题材涨停趋势 ↑{rotation.get('sector_zt_up')}/↓{rotation.get('sector_zt_down')}"
        block += (f"\n> 🔄 轮动反哺：回流候选 {rotation.get('re_entry_count')} 个"
                  f"（{_ph or '—'}）{_ztd}")
    # 替换旧的「## 5日情绪演变表」到下一个「## 」之间；找不到则在文件末尾追加
    pat = re.compile(r"##\s*5日情绪演变表.*?(?=## |\Z)", re.S)
    if pat.search(text):
        new_text = pat.sub(block + "\n\n", text, count=1)
    else:
        new_text = text.rstrip() + "\n\n" + block + "\n"
    MD_PATH.write_text(new_text, encoding="utf-8")


if __name__ == "__main__":
    build()
