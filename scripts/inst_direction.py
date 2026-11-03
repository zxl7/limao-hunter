#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机构方向验证（纯只读计算·不参与任何交易裁决）

缘起（2026-10-28 用户）：机构卡每天点名 60+ 只票，但票层没有筛选价值
（近30天 1989 票次，3 日内涨停命中 2.5%，接近全市场随机基准）。
用户的真实用法是「找方向 → 看机构在关注什么 → 用盘面验证机构关注对不对」。

本脚本只做三件事，全部基于已落盘事实：
  1. 方向聚合：把当日的机构 theme 文本归成方向，并统计该方向被连续点名多久
  2. 盘面验证：该方向今天在涨停池里兑现了没有（机构票命中 / 同标签涨停家数）
  3. 盲区反向：今天盘面在走的主线板块，机构近 N 日提过没有

⛔ 边界：
  - 不写候选项、不改 relay/watch、不改评分/排序/名额/仓位/账本/09:25 终态
  - 缺数据一律留 null 并记入 warnings，不填 0、不猜
  - 结论（verdict）只做事实归类，不做收益预测

输出：cache/inst_direction_<date>.json（schema=inst_direction_v1）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"
ZT_DIR = CACHE / "zt_history"
POOL_GLOB = str(CACHE / "institution_pool_*.json")
THEME_INDEX = CACHE / "top5_history" / "theme_index.json"

SCHEMA = "inst_direction_v1"
# 方向「先手 / 追高」分界：首次被点名距今 <= 该交易日数，视为机构先手
LEAD_TRADING_DAYS = 3
# 盲区反向回看窗口（交易日）
BLIND_LOOKBACK = 5
# 判定「盘面认了」的最低同标签涨停家数
SECTOR_ZT_MIN = 3
# 判定题材「升温/退潮」的最低绝对涨停家数（低值抖动不算趋势）
TREND_MIN_ZT = 3
# 判定「盘面认了」的最低相对强度：该题材涨停占今日涨停总数的百分比
TAG_SHARE_MIN = 10.0
# 全市场票数兜底（底库不可用时；仅用于算涨停率基准，不参与选股）
MARKET_UNIVERSE_FALLBACK = 5500

# theme 文本里常见的修饰词，聚方向时剥离（只做轻量归一，不做语义归类）
_NOISE = re.compile(r"[：:（(].*$|[\s\u3000]+")

# 元标签：属于「涨停原因 / 资金属性」而非产业方向，不能拿来当方向验证
_META_TAGS = {"业绩增长"}
# 泛词：出现在任何方向文本里都不足以构成题材关联
_GENERIC_WORDS = {
    "经济", "应用", "材料", "设备", "概念", "产业", "行业", "板块", "市场",
    "需求", "价格", "涨价", "订单", "龙头", "公司", "上市", "周期", "业绩",
}
# 从题材名里剥离的后缀，用于取核心词做文本关联
_TAG_SUFFIX = re.compile(r"(概念|板块|产业|行业|指数|/.*|（.*?）|\(.*?\))")


def _tag_core(tag: str) -> str:
    return _TAG_SUFFIX.sub("", tag or "").strip()


def _is_cn(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch <= "\u9fff"


def _contains_word(text: str, word: str) -> bool:
    """中文词面匹配：只要求「前一个字符不是汉字」。

    只看前边界，是因为中文题材名后面常直接跟别的字：
    「铜」在「铜价/铜箔」里是主体，「锂」在「锂电池」里是主体；
    而「覆铜板」的「铜」前面是「覆」，属于词内成分，不是方向本身。
    后边界不做限制，否则「快递」会因为「快递旺季」被判不命中。
    """
    if not word or not text:
        return False
    start = 0
    while True:
        i = text.find(word, start)
        if i < 0:
            return False
        if i == 0 or not _is_cn(text[i - 1]):
            return True
        start = i + 1


def _tag_related(theme: str, tag: str) -> bool:
    """机构 theme 原文与市场题材名是否有词面关联。

    规则（宁漏不错）：
      - 最长公共子串 ≥3 字 → 关联
      - 公共子串 =2 字 → 只有「整个题材核心词就是这两个字」才算（化肥/快递/煤炭）
      - 其余一律不算

    ⛔ 2 字泛匹配会大量错连（实测：宠物经济→低空经济、端侧AI→存储芯片、
    Token出海→托育服务），错配比漏配危险得多。
    更可靠的题材归属由 AI 在生成机构池时用 market_tags 字段给出，
    本函数只是没有该字段时的兜底。
    """
    if not theme or not tag or tag in _META_TAGS:
        return False
    core = _tag_core(tag)
    if not core:
        return False
    for length in range(min(6, len(core)), 1, -1):
        for i in range(len(core) - length + 1):
            frag = core[i:i + length]
            if not _contains_word(theme, frag):
                continue
            if length >= 3:
                return True
            if frag == core:
                return True
    return False


def _load(path: Path):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _date_of_pool(path: str) -> str:
    m = re.search(r"institution_pool_(\d{4}-\d{2}-\d{2})\.json$", path)
    return m.group(1) if m else ""


def _direction_key(theme: str) -> str:
    """粗略方向键：去掉冒号后的解释与括号内容，保留主干。

    ⚠️ 这只是把同一段文本归到一起，不跨不同文本做语义合并。
    跨日方向合并依赖 AI 在生成机构池时补 direction_key（见卡 prompt），
    本函数仅作无该字段时的兜底。
    """
    head = _NOISE.sub("", theme or "").strip()
    return head or (theme or "").strip()[:20]


def _zt_dates() -> list[str]:
    out = []
    for p in glob.glob(str(ZT_DIR / "zt_*.json")):
        m = re.search(r"zt_(\d{4}-\d{2}-\d{2})\.json$", p)
        if m:
            out.append(m.group(1))
    return sorted(out)


def _load_pool(date: str) -> dict:
    p = CACHE / f"institution_pool_{date}.json"
    d = _load(p)
    if not isinstance(d, dict):
        return {}
    return d


def _pool_items(date: str) -> list[dict]:
    d = _load_pool(date)
    items = d.get("items") or []
    return [it for it in items if it.get("code")]


def _load_zt(date: str) -> dict:
    d = _load(ZT_DIR / f"zt_{date}.json")
    return d if isinstance(d, dict) else {}


def _day_theme_counts(dates: list[str], inv: dict[str, set[str]]) -> dict[str, Counter]:
    """每个交易日、每个题材的涨停家数（用来判断方向是在升温还是退潮）。"""
    out: dict[str, Counter] = {}
    for d in dates:
        z = _load_zt(d)
        c: Counter[str] = Counter()
        for x in (z.get("zt_list") or []):
            for t in inv.get(str(x.get("code"))[:6], ()):
                c[t] += 1
        out[d] = c
    return out


def _trend(series: list[int], n: int = 5) -> tuple[str, float | None, float | None]:
    """近 n 日均值 vs 再往前 n 日均值。样本不足返回 unknown。"""
    if len(series) < n + 1:
        return "unknown", None, None
    cur = series[-n:]
    prev = series[-(2 * n):-n] if len(series) >= 2 * n else series[:len(series) - n]
    a, b = sum(cur) / len(cur), sum(prev) / len(prev)
    if b == 0:
        label = "rising" if a > 0 else "flat"
    elif a >= b * 1.3:
        label = "rising"
    elif a <= b * 0.7:
        label = "falling"
    else:
        label = "flat"
    return label, round(a, 1), round(b, 1)


def _theme_codes() -> dict[str, set[str]]:
    """题材 → 成分股（取 theme_index 的历史累计成分，属于市场语言归类）。"""
    d = _load(THEME_INDEX) or {}
    themes = d.get("themes") or {}
    out: dict[str, set[str]] = {}
    for name, v in themes.items():
        codes = {str(c)[:6] for c in (v.get("codes") or []) if c}
        if codes:
            out[name] = codes
    return out


def _code_to_themes(tmap: dict[str, set[str]]) -> dict[str, set[str]]:
    inv: dict[str, set[str]] = defaultdict(set)
    for name, codes in tmap.items():
        for c in codes:
            inv[c].add(name)
    return inv


def _recent_pool_dates(dates: list[str], date: str, n: int) -> list[str]:
    """date 及其之前 n 个交易日（含当日）的机构池日期。"""
    idx = dates.index(date) if date in dates else len(dates)
    window = dates[max(0, idx - n + 1): idx + 1]
    return [d for d in window if (CACHE / f"institution_pool_{d}.json").exists()]


def _market_universe() -> tuple[int, str]:
    """全市场票数（算涨停率基准用）。底库不可用则回退常量并标注来源。"""
    try:
        import duckdb  # noqa: PLC0415
        db = CACHE / "market_daily" / "market_daily.duckdb"
        if not db.exists():
            return MARKET_UNIVERSE_FALLBACK, "fallback_no_db"
        conn = duckdb.connect(str(db), read_only=True)
        try:
            d = conn.execute("SELECT max(trade_date) FROM market_daily_bars").fetchone()[0]
            n = conn.execute(
                "SELECT count(DISTINCT code) FROM market_daily_bars WHERE trade_date=?",
                [d],
            ).fetchone()[0]
        finally:
            conn.close()
        if n and n > 1000:
            return int(n), f"market_daily@{d}"
    except Exception:
        pass
    return MARKET_UNIVERSE_FALLBACK, "fallback_error"


def build(date: str | None = None, lookback: int = BLIND_LOOKBACK) -> dict:
    zt_dates = _zt_dates()
    if not zt_dates:
        return {"schema": SCHEMA, "error": "no_zt_history"}
    date = date or zt_dates[-1]
    zt = _load_zt(date)
    if not zt:
        return {"schema": SCHEMA, "date": date, "error": f"zt_history_missing:{date}"}

    zt_list = zt.get("zt_list") or []
    zt_by = {str(x.get("code"))[:6]: x for x in zt_list if x.get("code")}
    warnings: list[str] = []

    items = _pool_items(date)
    if not items:
        warnings.append(f"institution_pool_missing_or_empty:{date}")

    tmap = _theme_codes()
    inv = _code_to_themes(tmap)

    # ── 机构点名历史：首次出现 / 连续出现天数 ───────────────────────────
    hist_days: dict[str, list[str]] = defaultdict(list)
    for p in sorted(glob.glob(POOL_GLOB)):
        d = _date_of_pool(p)
        if not d or d > date:
            continue
        for it in (_load(Path(p)) or {}).get("items") or []:
            c = str(it.get("code"))[:6]
            if not c:
                continue
            hist_days[c].append(d)

    # ── 方向聚合 ────────────────────────────────────────────────────────
    dirs: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        k = it.get("direction_key") or _direction_key(it.get("theme", ""))
        dirs[k].append(it)

    # 今日涨停的市场语言标签（题材）与行业
    zt_tag_counter: Counter[str] = Counter()
    for x in zt_list:
        for t in inv.get(str(x.get("code"))[:6], ()):
            zt_tag_counter[t] += 1
    zt_sector_counter: Counter[str] = Counter(
        (x.get("sector") or "未分类") for x in zt_list
    )
    sector_max_boards: dict[str, int] = defaultdict(int)
    for x in zt_list:
        s = x.get("sector") or "未分类"
        sector_max_boards[s] = max(sector_max_boards[s], int(x.get("boards") or 1))

    # 全市场涨停率基准：用来判断「某题材今天涨停多」到底是真强还是题材本身票多
    universe, universe_src = _market_universe()
    base_rate = (len(zt_list) / universe) if universe else 0.0

    # 题材涨停家数的日度序列（判断方向在升温 / 退潮）
    day_theme = _day_theme_counts([d for d in zt_dates if d <= date], inv)

    # 今日涨停的题材集中度（市场语言口径），直接体现「今天盘面在做哪条线」
    _tc = [(t, n) for t, n in zt_tag_counter.most_common() if n >= 2 and t not in _META_TAGS]
    _keep: list[tuple[str, int]] = []
    for t, n in _tc:
        if any(t != k and t in k and n <= m for k, m in _keep):
            continue
        _keep.append((t, n))
    top_themes = [
        {"theme": t, "zt": n, "share_pct": round(n / max(1, len(zt_list)) * 100, 1)}
        for t, n in _keep[:8]
    ]

    directions = []
    for key, rows in sorted(dirs.items(), key=lambda kv: -len(kv[1])):
        codes = [str(r["code"])[:6] for r in rows]
        narrative = rows[0].get("theme", "")
        theme_text = " ".join({r.get("theme", "") for r in rows})

        # 市场语言标签：AI 显式指定的优先（direction pool 里的 market_tags），
        # 没有才回退词面匹配（宁可漏，不可错配）
        decl: list[str] = []
        for r in rows:
            for t in (r.get("market_tags") or []):
                if isinstance(t, str) and t and t not in decl:
                    decl.append(t)
        if decl:
            market_tags = [t for t in decl if t in tmap][:8]
            tag_source = "ai_declared"
        else:
            market_tags = sorted(t for t in tmap if _tag_related(theme_text, t))[:8]
            tag_source = "word_match"

        hits = []
        for c in codes:
            if c in zt_by:
                x = zt_by[c]
                hits.append({
                    "code": c,
                    "name": x.get("name"),
                    "boards": x.get("boards"),
                    "sector": x.get("sector"),
                })
        hit_max_boards = max([h.get("boards") or 1 for h in hits], default=0)

        # 同标签今日涨停（含非机构票）——盘面对该方向的真实参与度
        # ⚠️ theme_index 的 codes 是「历史累计涨停过的票」，不是全量成分股，
        # 因此不能用 zt/size 当涨停率。改用「占今日涨停总数的比重」= 盘面集中度。
        tag_zt: dict[str, dict] = {}
        for t in market_tags:
            n = zt_tag_counter[t]
            if n <= 0:
                continue
            tag_zt[t] = {
                "zt": n,
                "share_pct": round(n / max(1, len(zt_list)) * 100, 1),
            }
        # 父子题材去重（「机器人」是「人形机器人」的父串，双计会虚高）
        _drop: set[str] = set()
        for a in tag_zt:
            for b in tag_zt:
                if a != b and a in b and tag_zt[a]["zt"] <= tag_zt[b]["zt"]:
                    _drop.add(a)
        for t in _drop:
            tag_zt.pop(t, None)
        tag_zt_max = max([v["zt"] for v in tag_zt.values()], default=0)
        tag_strength = max([v["share_pct"] for v in tag_zt.values()], default=0)
        strong_tags = [t for t, v in tag_zt.items()
                       if v["zt"] >= SECTOR_ZT_MIN and v["share_pct"] >= TAG_SHARE_MIN]

        # 同标签涨停里，机构没点名的是谁（这是「机构该看到却没看到」）
        tag_codes: set[str] = set()
        for t in market_tags:
            tag_codes |= tmap.get(t, set())
        offpool = [
            {"code": str(x.get("code"))[:6], "name": x.get("name"),
             "boards": x.get("boards"), "sector": x.get("sector")}
            for x in zt_list
            if str(x.get("code"))[:6] in tag_codes and str(x.get("code"))[:6] not in codes
        ]

        # 首次点名（票口径）：方向是否「早就在讲」应该看这批票本身什么时候开始被讲，
        # 不能看 theme 文本归并结果（文本跨日不稳定，会把无关方向并到一起）
        firsts = sorted(min(hist_days[c]) for c in codes if hist_days.get(c))
        first_seen_min = firsts[0] if firsts else None
        first_seen_median = firsts[len(firsts) // 2] if firsts else None
        lead_days = None
        if first_seen_median and first_seen_median in zt_dates and date in zt_dates:
            lead_days = zt_dates.index(date) - zt_dates.index(first_seen_median)

        # ── 方向趋势：机构讲的时长 × 盘面题材涨停家数走向 ──
        # 关联题材里取「近期涨停最多」的那个作为该方向的盘面代表
        rep_tag = None
        if market_tags:
            rep_tag = max(market_tags, key=lambda t: sum(
                day_theme[d].get(t, 0) for d in day_theme))
        seq = [day_theme[d].get(rep_tag, 0) for d in sorted(day_theme)] if rep_tag else []
        t_label, t_cur, t_prev = _trend(seq[-10:] if seq else [])
        # 涨停家数本身是低值整数，0→2 家不该叫「升温」，要求绝对量门槛
        if t_label == "rising" and (t_cur or 0) < TREND_MIN_ZT:
            t_label = "flat"
        elif t_label == "falling" and (t_prev or 0) < TREND_MIN_ZT:
            t_label = "flat"
        # 机构讲了多少个交易日（该方向有出现在机构池里的天数）
        inst_tenure = len({d for c in codes for d in hist_days.get(c, [])})

        if rep_tag is None:
            # 匹配不到题材 ≠ 没有数据。题材库只覆盖近 30 日涨停过的方向，
            # 匹配不到意味着「市场还没炒过」或「本库未覆盖」，都不能当成「盘面不认」。
            quad, qtext = "unpriced", "近30日无涨停样本·市场还没炒过（或题材库未覆盖）"
        elif t_label == "unknown":
            quad, qtext = "unclear", "题材样本不足·不下结论"
        elif inst_tenure >= 5 and t_label == "rising":
            quad, qtext = "mainline", "机构持续讲·盘面持续升温（主线候选）"
        elif inst_tenure >= 5 and t_label == "falling":
            quad, qtext = "fading", "机构还在讲·盘面在退潮（说法过期）"
        elif inst_tenure < 5 and t_label == "rising":
            quad, qtext = "chasing", "机构刚讲·盘面已经升温（要防追高）"
        elif inst_tenure >= 5:
            quad, qtext = "stale", "机构讲了很久·盘面一直没动（说法未兑现）"
        else:
            quad, qtext = "watching", "机构刚讲·盘面还没动（观察）"

        # ── 结论：只做事实归类 ──
        if rep_tag is None and not hits:
            # ⛔ 没有题材样本 ≠ 盘面不认。抓不到只能说不确定。
            verdict, vtext = "no_sample", "题材库无对应方向·无法用涨停家数验证"
        elif hits and lead_days is not None and lead_days <= LEAD_TRADING_DAYS:
            verdict, vtext = "inst_lead", "机构先手·今天开始兑现"
        elif hits and (lead_days is None or lead_days > LEAD_TRADING_DAYS):
            verdict, vtext = "late_follow", "机构讲了一段时间·今天机构票才动"
        elif strong_tags:
            verdict, vtext = "market_partial", "盘面认这个方向·但涨停的不是机构点名的票"
        elif tag_zt_max > 0:
            verdict, vtext = "market_weak", "盘面只有零星参与·不算兑现"
        else:
            verdict, vtext = "market_ignore", "盘面没有反应"

        directions.append({
            "direction_key": key,
            "narrative": narrative,
            "inst_count": len(rows),
            "codes": codes,
            "first_seen_min": first_seen_min,
            "first_seen_median": first_seen_median,
            "inst_tenure_days": inst_tenure,
            "lead_trading_days": lead_days,
            "market_tags": market_tags,
            "tag_source": tag_source,
            "rep_tag": rep_tag,
            "tag_zt_series": seq[-10:],
            "tag_trend": t_label,
            "tag_trend_recent_avg": t_cur,
            "tag_trend_prev_avg": t_prev,
            "quadrant": quad,
            "quadrant_text": qtext,
            "today_zt_of_inst": hits,
            "today_zt_max_boards": hit_max_boards,
            "same_tag_zt": tag_zt_max,
            "same_tag_strength": tag_strength,
            "same_tag_strong": strong_tags,
            "same_tag_detail": tag_zt,
            "offpool_zt": offpool[:10],
            "verdict": verdict,
            "verdict_text": vtext,
        })

    # ── 盲区反向：今天主线板块，机构近 N 日提过没 ────────────────────────
    win = _recent_pool_dates(zt_dates, date, lookback)
    recent_themes: list[str] = []
    recent_codes: set[str] = set()
    for d in win:
        for it in _pool_items(d):
            recent_themes.append(it.get("theme", ""))
            recent_codes.add(str(it.get("code"))[:6])
    joined = " ".join(recent_themes)

    blind = []
    for sec, n in zt_sector_counter.most_common():
        if n < SECTOR_ZT_MIN:
            continue
        # 该行业今日涨停票，有没有被机构近 N 日点过名
        sec_codes = {str(x.get("code"))[:6] for x in zt_list
                     if (x.get("sector") or "未分类") == sec}
        named = sec_codes & recent_codes
        # 关键词命中：机构 theme 原文里出现过该行业名（可能漏判，仅作提示）
        kw = sec in joined if sec != "未分类" else False
        blind.append({
            "sector": sec,
            "today_zt": n,
            "max_boards": sector_max_boards[sec],
            "inst_named_in_window": len(named),
            "inst_named_codes": sorted(named),
            "theme_keyword_hit": bool(kw),
            "window_days": len(win),
        })
    blind.sort(key=lambda r: (-(r["inst_named_in_window"] == 0), -r["today_zt"]))

    top_dirs = [d for d in directions if d["verdict"] in ("inst_lead", "market_partial")]
    return {
        "schema": SCHEMA,
        "date": date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {
            "zt_history": f"cache/zt_history/zt_{date}.json",
            "institution_pool": f"cache/institution_pool_{date}.json",
            "theme_index": "cache/top5_history/theme_index.json",
            "theme_index_updated_at": (_load(THEME_INDEX) or {}).get("updated_at"),
            "universe": universe_src,
        },
        "market": {
            "zt_count": len(zt_list),
            "max_boards": max([int(x.get("boards") or 1) for x in zt_list], default=0),
            "universe": universe,
            "base_zt_rate_pct": round(base_rate * 100, 3),
            "top_sectors": [
                {"sector": s, "zt": n, "max_boards": sector_max_boards[s]}
                for s, n in zt_sector_counter.most_common(8)
            ],
            "top_themes": top_themes,
        },
        "summary": {
            "direction_count": len(directions),
            "inst_stock_count": sum(d["inst_count"] for d in directions),
            "inst_stock_zt_today": sum(len(d["today_zt_of_inst"]) for d in directions),
            "directions_with_market": len([d for d in directions if d["same_tag_strong"]]),
            "top_directions": [d["direction_key"] for d in top_dirs],
        },
        "directions": directions,
        "blind_spots": blind,
        "warnings": warnings,
        "caliber": {
            "verdict": "只按今日涨停事实归类，不含收益预测",
            "market_tags": "题材名来自 theme_index 历史累计成分反查，属于市场语言归类，非机构原话",
            "same_tag_strength": "同标签涨停率 ÷ 全市场涨停率；<2 视为题材本身票多而非方向强",
            "first_seen": "票口径（该方向机构票首次被点名的中位数日期），非 theme 文本口径",
            "blind_spot_kw": "theme_keyword_hit 为原文关键词命中，可能漏判，仅作提示",
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="机构方向盘面验证（只读）")
    ap.add_argument("--date", default=None, help="交易日 YYYY-MM-DD，默认最新涨停历史日")
    ap.add_argument("--lookback", type=int, default=BLIND_LOOKBACK, help="盲区反向回看交易日数")
    ap.add_argument("--out", default=None, help="输出路径，默认 cache/inst_direction_<date>.json")
    ap.add_argument("--json", action="store_true", help="打印完整 JSON")
    args = ap.parse_args(argv)

    payload = build(args.date, lookback=args.lookback)
    if payload.get("error"):
        print(f"[inst_direction] 跳过：{payload['error']}", file=sys.stderr)
        return 0

    out = Path(args.out) if args.out else (CACHE / f"inst_direction_{payload['date']}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    s, m = payload["summary"], payload["market"]
    print(f"【机构方向 {payload['date']}】涨停 {m['zt_count']} 家·最高 {m['max_boards']} 板"
          f"｜全市场 {m['universe']} 票")
    print(f"机构 {s['inst_stock_count']} 票 → {s['direction_count']} 个方向；"
          f"今日机构票涨停 {s['inst_stock_zt_today']} 只\n")

    order = [
        ("mainline", "① 主线候选：机构持续讲 · 盘面在升温"),
        ("chasing", "② 要防追高：机构刚讲 · 盘面已升温"),
        ("fading", "③ 说法过期：机构还在讲 · 盘面在退潮"),
        ("watching", "④ 先记着：机构刚讲 · 盘面还没动"),
        ("stale", "④b 始终没兑现：机构讲很久 · 盘面一直没动"),
        ("unpriced", "⑤ 市场还没炒过：机构在讲 · 题材库近30日无涨停样本"),
        ("unclear", "⑥ 样本不足"),
    ]
    by_q = defaultdict(list)
    for d in payload["directions"]:
        by_q[d["quadrant"]].append(d)

    for q, title in order:
        rows = by_q.get(q) or []
        if not rows:
            continue
        print(title)
        for d in sorted(rows, key=lambda x: -(x["tag_trend_recent_avg"] or 0)):
            ser = ",".join(str(v) for v in d["tag_zt_series"][-6:]) or "—"
            print(f"  ▸ {d['direction_key'][:22]}｜机构{d['inst_count']}票·讲了{d['inst_tenure_days']}天"
                  f"｜题材「{d['rep_tag'] or '—'}」近6日涨停 {ser}")
            if d["today_zt_of_inst"]:
                names = ", ".join(
                    f"{h['name']}{h['boards']}板" if (h["boards"] or 1) > 1 else h["name"]
                    for h in d["today_zt_of_inst"])
                print(f"    今日兑现：机构票涨停 {names}｜{d['verdict_text']}")
                if d["quadrant"] in ("fading", "stale"):
                    print("    ⚠️ 题材整体在退潮，今天是个股逆势启动，不是方向回暖")
            elif d["same_tag_strong"]:
                print(f"    今日兑现：机构票无涨停，同题材涨停 {d['same_tag_zt']} 家"
                      f"（占今日涨停 {d['same_tag_strength']}%）= 方向对、票不对")
            else:
                print(f"    今日兑现：{d['verdict_text']}")
        print()

    if m.get("top_themes"):
        print("今日涨停题材集中度（市场实际在做）：")
        for t in m["top_themes"][:6]:
            print(f"  {t['theme']:<12}{t['zt']:>3}家  占{t['share_pct']:>5.1f}%")

    missed = [b for b in payload["blind_spots"] if b["inst_named_in_window"] == 0]
    if missed:
        print(f"\n盘面在走·机构近{payload['blind_spots'][0]['window_days']}日未点名的行业：")
        for b in missed:
            print(f"  {b['sector']:<10}{b['today_zt']:>3}家涨停·最高{b['max_boards']}板")
    for w in payload["warnings"]:
        print(f"⚠️ {w}", file=sys.stderr)
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
