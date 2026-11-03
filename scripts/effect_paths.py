#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赚钱效应 / 亏钱效应 · 路径归因层（EffectEngine §3.1b · 2026-10-19 用户定）

⛔ 与 §3.1「双轴总量」的分工（不得混淆）：
  - §3.1 回答「多少人赚了、多少人亏了」→ 横截面统计（up_rate / avg_pct / nuke_count）
  - 本模块回答「钱是**怎么**赚到手、又是**怎么**亏掉的」→ 纵向**路径归类**

一句话：§3.1 是温度计（能不能做），本模块是菜谱（用哪种方式做）。

## 九条路径（互斥且穷尽，每票只落一桶）

赚钱侧（今日当前封住）：
  ditian            地天反核 —— 当日曾跌停、现拉至涨停（同日地天板）
  front_leader      前排龙头 —— 昨 ≥3 板仍封住
  new_theme_dragon  新题材板块龙 —— 所属题材昨涨停 ≤1（从无到有）且今日封住
  solo              独走辨识度 —— 题材昨日已有（≥2 家）但今日仅它封住
  non_core_relay    非核心人气接力 —— 题材热、它封住但非龙一/非唯一

亏钱侧（今日未封）：
  high_nuke         高位接力被核 —— 昨 ≥3 板且今日 ≤−5%
  high_fade         高度衰减 —— 昨 ≥3 板但温和下杀（−5%<pct<3%）⛔ §3.1 阈值抓不到的盲区
  low_fail          低位失败出货 —— 昨 ≤2 板且今日 ≤−5%
  mojito            杂毛无催化 —— 昨 ≤2 板、小跌、题材内无跟随

中性：
  strong_unsealed   强但未封 —— 今日 ≥+3% 却没封住（钱还在但没锁）
  neutral           其余

## 数据来源（零新增接口·全部读现成文件）
  cache/feedback_matrix.json        quotes（昨涨停池今日实时）+ 总量标量
  cache/pulse_snapshots/latest.json limit_up_stocks / limit_down / top5
  cache/yesterday_zt.json           昨涨停池的 sector（quotes 不含题材）
  cache/zt_history/zt_<date>.json   收盘后补全板块（盘中为空属正常）

## 铁律
1. **纯只读聚合**：不写 feedback_matrix、不改任何原有产物，只落 `cache/effect_paths_<date>.json`。
2. **缺数据不猜**：字段缺失 → 该票落 neutral 并计入 `data_gaps`，⛔ 不臆造 pct/板数。
3. **不产买卖结论**：本模块只描述「今天钱从哪条路走的」，⛔ 不写「明天买什么」。
4. **必须同刻**：只比较同一份 latest/feedback 快照，⛔ 不跨时点拼接（时间线对齐铁律）。
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.json_io import load_json, save_json  # noqa: E402

CACHE = ROOT / "cache"

# 可逆开关：全量关掉只返回空结果，不影响任何调用方
ENABLE_EFFECT_PATHS = os.environ.get("ENABLE_EFFECT_PATHS", "1") != "0"

# 板数事实校正开关（2026-10-27）：**默认关闭，生产数字保持原样**（用户 2026-10-27 09:56「先不动」）。
# 置 1 才用日线自算连续涨停数覆盖上游 boards（原值留 prev_boards_raw 可回溯）。
# 背景：上游 boards 是「区间累计涨停数」，断板不重置 → 跨波累加。
# 实证 百洋股份 002696：10-09~10-15 五板 + 10-20/21 两板 = 7 板，但 10-23 收盘
# 它其实是 0 板（当日 +4.49% 未涨停），却被分进「前排龙头·高位继续封板」。
# ⛔ 开启会改变分桶 → 卡面归因数字随之变化，必须先由用户拍板；日常核对用
#    `python3 scripts/audit_prev_boards.py --date YYYY-MM-DD`（只读，不影响产物）。
ENABLE_EP_BOARDS_KLINE = os.environ.get("ENABLE_EP_BOARDS_KLINE", "0") == "1"
KLINE_DIR = ROOT / "cache" / "kline_qfq"


def _prev_trade_date(d: str) -> str:
    """d 的前一交易日（唯一真源 trading_calendar；日历不可用才退回工作日近似）。"""
    try:
        from datetime import date as _d
        from common.trading_calendar import prev_a_share_trading_day
        return prev_a_share_trading_day(_d.fromisoformat(d)).isoformat()
    except Exception:
        # trading-day-fallback: 仅日历不可用时兜底
        from datetime import date as _d, timedelta as _td
        x = _d.fromisoformat(d) - _td(days=1)
        while x.weekday() >= 5:
            x -= _td(days=1)
        return x.isoformat()


def _limit_threshold(code: str) -> float:
    """涨停阈值（%）：20cm 板块 19.5，其余 9.7（兜住复权/四舍五入误差）。"""
    c = str(code).split(".")[0]
    return 19.5 if c.startswith(("688", "689", "300", "301")) else 9.7


def _true_boards_kline(code: str, asof: str) -> int | None:
    """截至 asof 收盘的**连续**涨停天数（断板归零）。无日线/无该日 → None（不猜）。"""
    f = KLINE_DIR / f"{str(code).split('.')[0]}.json"
    if not f.exists():
        return None
    try:
        bars = load_json(f).get("bars") or []
    except Exception:
        return None
    idx = next((i for i, b in enumerate(bars) if b.get("date") == asof), None)
    if idx is None or idx == 0:
        return None
    th = _limit_threshold(code)
    n = 0
    i = idx
    while i > 0:
        prev_c = bars[i - 1].get("close")
        if not prev_c:
            break
        if (bars[i].get("close") / prev_c - 1) * 100 >= th:
            n += 1
            i -= 1
        else:
            break
    return n


# ── 路径定义（kind → 展示名 / 归属侧 / 排序权重）────────────────────────────
SIDE_PROFIT = "profit"          # 真赚钱：钱赚到手并锁住
SIDE_WEAK = "profit_weak"       # 赚钱效应残留：涨了但没锁
SIDE_LOSS = "loss"              # 真亏钱
SIDE_NEUTRAL = "neutral"

PATH_DEFS: dict[str, dict[str, str]] = {
    "ditian":           {"label": "地天反核",     "side": SIDE_PROFIT,  "desc": "曾跌停又拉上涨停"},
    "front_leader":     {"label": "前排龙头",     "side": SIDE_PROFIT,  "desc": "高位继续封板"},
    "new_theme_dragon": {"label": "新题材板块龙", "side": SIDE_PROFIT,  "desc": "题材从无到有、它先封"},
    "solo":             {"label": "独走辨识度",   "side": SIDE_PROFIT,  "desc": "同题材只剩它封住"},
    "non_core_relay":   {"label": "人气接力（非唯一）", "side": SIDE_PROFIT, "desc": "题材热、多只封板，它不是唯一"},
    "high_nuke":        {"label": "高位接力被核", "side": SIDE_LOSS,    "desc": "高位票被砸穿"},
    "high_fade":        {"label": "高度衰减",     "side": SIDE_LOSS,    "desc": "高位票温和下杀（不看跌幅）"},
    "low_fail":         {"label": "低位失败出货", "side": SIDE_LOSS,    "desc": "低位票直接崩"},
    "mojito":           {"label": "杂毛无催化",   "side": SIDE_LOSS,    "desc": "没人跟、慢慢阴跌"},
    "strong_unsealed":  {"label": "强但未封",     "side": SIDE_WEAK,    "desc": "涨了但没锁住"},
    "neutral":          {"label": "中性",         "side": SIDE_NEUTRAL, "desc": "不赚不亏"},
}

# 判定阈值（与 §3.1 既有档位对齐，⛔ 不新立一套）
HIGH_BOARDS = 3        # 「高位」= 昨 ≥3 板
NUKE_PCT = -5.0        # 核按钮 = ≤ −5%
STRONG_PCT = 3.0       # 强势 = ≥ +3%
NEW_THEME_PREV_ZT = 1  # 题材昨日涨停 ≤1 → 视作「新题材」

# 泛词题材：家数无法说明「唯一性」，⛔ 不参与 solo 判定（沿用题材归并铁律·禁泛词）
GENERIC_THEMES = {"其他", "其它", "综合", "未分类", "无", ""}

# 数据缺口（如实登记，⛔ 不用假数据补）
BLIND_SPOTS = (
    "水下低吸：需盘中最低价/分时，系统只落首封时刻 fbt，无最低价 → 本模块不算此路径",
    "跨日跌停反核：需「昨日跌停池」历史，系统只落涨停池 → 仅算当日地天板",
)


# ── 小工具 ────────────────────────────────────────────────────────────────
def normalize_code(code: Any) -> str:
    """'603197.SS' / '002085.SZ' / '000573' → 统一 6 位数字。"""
    s = str(code or "").strip().upper()
    m = re.match(r"^(\d{6})", s)
    return m.group(1) if m else s


def to_num(v: Any, default: float | None = None) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else default  # 排除 NaN


def parse_boards(v: Any) -> int | None:
    """'2天2板' / '3板' / 2 → 2；无法解析 → None（不猜）。"""
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"(\d+)\s*板", str(v or ""))
    if m:
        return int(m.group(1))
    n = to_num(v)
    return int(n) if n is not None else None


def _theme_index(latest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """题材名 → {zt_count, zt_prev, leader, is_new}。来源 latest.top5（选股宝题材口径）。"""
    idx: dict[str, dict[str, Any]] = {}
    for t in latest.get("top5") or []:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        zt_prev = to_num(t.get("zt_prev"))
        leader = t.get("leader") if isinstance(t.get("leader"), dict) else {}
        idx[name] = {
            "zt_count": int(to_num(t.get("zt_count"), 0) or 0),
            "zt_prev": int(zt_prev) if zt_prev is not None else None,
            "leader_name": str(leader.get("name") or "").strip(),
            "is_new": (zt_prev is not None and zt_prev <= NEW_THEME_PREV_ZT),
        }
    return idx


def _prev_sector_map(yesterday_zt: dict[str, Any]) -> dict[str, str]:
    """昨涨停池 code → 行业/题材（quotes 不含 sector，必须从这里补）。"""
    out: dict[str, str] = {}
    for r in yesterday_zt.get("zt_list") or []:
        if not isinstance(r, dict):
            continue
        code = normalize_code(r.get("code"))
        sec = str(r.get("sector") or "").strip()
        if code and sec:
            out[code] = sec
    return out


# ── 核心：分类 ────────────────────────────────────────────────────────────
def _classify_one(
    row: dict[str, Any],
    themes: dict[str, dict[str, Any]],
    theme_today_cnt: dict[str, int],
) -> str:
    """单票判定（顺序即优先级，命中即返回 → 天然互斥）。

    row 需含：sealed(bool) / pct(float|None) / prev_boards(int|None) /
             theme(str) / had_limit_down(bool)

    ⚠️ 题材家数口径（2026-10-19 修正）：
      - `top5` 在榜题材 → 用其 `zt_count`（选股宝口径，最准）
      - 不在 top5 → 用**今日涨停池按 sector 实分组**的家数（同口径内部自洽）
      ⛔ 绝不能因「题材不在 top5」就当成 0 家，否则会把一堆多只封板的题材误判成「独走」。
    """
    sealed = bool(row.get("sealed"))
    pct = to_num(row.get("pct"))
    boards = to_num(row.get("prev_boards"))
    theme = str(row.get("theme") or "")
    tinfo = themes.get(theme) or {}
    if tinfo:
        theme_zt_today = int(tinfo.get("zt_count") or 0)
        # 新题材只认 top5 明确的 zt_prev（⛔ 不跨「行业/题材」两套口径推算）
        is_new_theme = tinfo.get("is_new") is True
    else:
        theme_zt_today = int(theme_today_cnt.get(theme, 0)) if theme else 0
        is_new_theme = False

    # ── 赚钱侧：当前封住 ──
    if sealed:
        if row.get("had_limit_down"):
            return "ditian"                     # 当日跌停 → 现在涨停，最强
        if boards is not None and boards >= HIGH_BOARDS:
            return "front_leader"               # 高位继续封，主升延续
        if is_new_theme:
            # 「板块龙」须名副其实：top5 明确给了 leader 就校验；
            # 没给 leader（题材不在 top5）则无法证伪，按龙一处理。
            leader_name = str(tinfo.get("leader_name") or "")
            if not leader_name or row.get("name") == leader_name:
                return "new_theme_dragon"       # 题材昨日≤1家 → 从无到有且它是龙一
            return "non_core_relay"             # 同题材但不是龙一 → 跟风
        if theme not in GENERIC_THEMES and theme_zt_today <= 1:
            return "solo"                       # 老题材里只剩它封住（泛词不判）
        return "non_core_relay"                 # 题材热、多只封板

    # ── 亏钱侧：未封（pct 缺失 → 中性，不猜）──
    if pct is None:
        return "neutral"
    if boards is not None and boards >= HIGH_BOARDS:
        if pct <= NUKE_PCT:
            return "high_nuke"                  # 高位被核
        if pct < STRONG_PCT:
            return "high_fade"                  # 高位温和衰减（§3.1 盲区）
    if pct >= STRONG_PCT:
        return "strong_unsealed"                # 涨了但没锁
    if boards is not None and boards <= 2 and pct <= NUKE_PCT:
        return "low_fail"                       # 低位直接崩
    if boards is not None and boards <= 2 and pct < 0 and theme_zt_today <= 1:
        return "mojito"                         # 没人跟、阴跌
    return "neutral"


def build(
    feedback: dict[str, Any] | None,
    latest: dict[str, Any] | None,
    yesterday_zt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """主入口：产出结构化路径归因（纯函数，不落盘）。"""
    fb = feedback or {}
    lt = latest or {}
    yzt = yesterday_zt or {}
    date = str(fb.get("date") or str(lt.get("ts") or "")[:10] or "")
    prev_date = str(fb.get("prev_date") or yzt.get("date") or "")
    # ⛔ 2026-10-27：实测 10-19～10-26 的产物 prev_date 全部 == date（上游字段被污染，
    #    昨日池写入时把当日日期写进 prev_date），只有 10-27 是对的。prev_date 不可能
    #    等于 date → 一旦相等就用交易日历纠正，否则「昨日板数」会按当日基准算。
    if date and (not prev_date or prev_date >= date):
        prev_date = _prev_trade_date(date)

    themes = _theme_index(lt)
    prev_sector = _prev_sector_map(yzt)

    # 今日跌停名单（仅当日可得）→ 判地天板
    ld_names = {str(n).strip() for n in ((lt.get("limit_down") or {}).get("names") or [])}

    # 今日涨停池（新票来源）
    today_zt: dict[str, dict[str, Any]] = {}
    for r in lt.get("limit_up_stocks") or []:
        if not isinstance(r, dict):
            continue
        code = normalize_code(r.get("code"))
        if code:
            today_zt[code] = r

    # 今日涨停池按 sector 实分组 → 给「不在 top5 的题材」提供家数兜底
    theme_today_cnt: dict[str, int] = Counter(
        str(r.get("sector") or "").strip() for r in today_zt.values() if str(r.get("sector") or "").strip()
    )

    rows: list[dict[str, Any]] = []
    gaps: list[str] = []

    # ① 昨涨停池（quotes）→ 今日表现
    quotes = fb.get("quotes") or {}
    for code_raw, q in quotes.items():
        if not isinstance(q, dict):
            continue
        code = normalize_code(code_raw)
        name = str(q.get("name") or "").strip()
        pct = to_num(q.get("pct"))
        boards = to_num(q.get("prev_boards"))
        # 题材：**今日涨停池的题材名优先**（与今日家数统计同口径），昨池行业名仅兜底。
        # ⛔ 顺序不能反：昨池 sector 是东财行业（汽车零部/出版），与今日题材名不同口径，
        #    反着取会让封板票全都查不到家数 → 被误判成「独走」。
        theme = (
            str((today_zt.get(code) or {}).get("sector") or "").strip()
            or prev_sector.get(code)
            or ""
        )
        if pct is None:
            gaps.append(f"{name or code} 无今日涨幅")
        rows.append({
            "code": code,
            "name": name,
            "pct": pct,
            "prev_boards": boards,
            "theme": theme,
            "sealed": bool(q.get("sealed")),
            "had_limit_down": name in ld_names,
            "source": "prev_zt",
        })

    # ② 今日新涨停（不在昨涨停池的票）
    seen = {r["code"] for r in rows}
    for code, r in today_zt.items():
        if code in seen:
            continue
        theme = str(r.get("sector") or "").strip()
        rows.append({
            "code": code,
            "name": str(r.get("name") or "").strip(),
            "pct": to_num(r.get("pct")),
            "prev_boards": (parse_boards(r.get("boards")) or 1) - 1,  # 今日板数-1 = 昨日板数
            "theme": theme,
            "sealed": True,
            "had_limit_down": False,
            "source": "today_new",
        })

    # ③ 板数事实校正（2026-10-27）——必须在分类之前，因为 board 数决定落在哪个桶。
    #    上游 boards 是「区间累计涨停数」，断板不重置 → 跨波累加（百洋股份 5+2=7 板，
    #    实际 10-23 收盘 0 板）。这里用日线自算「当前连续涨停数」覆盖：
    #    只覆盖能算出事实的票，kline 缺失一律保留原值（⛔ 缺数据不猜，铁律 2）。
    #    原值留在 prev_boards_raw，可追溯、可回滚（ENABLE_EP_BOARDS_KLINE=0 全关）。
    if ENABLE_EP_BOARDS_KLINE and prev_date:
        fixed_n = 0
        for r in rows:
            tb = _true_boards_kline(r["code"], prev_date)
            if tb is None or r.get("prev_boards") == tb:
                continue
            r["prev_boards_raw"] = r.get("prev_boards")
            r["prev_boards"] = tb
            r["boards_source"] = "kline"
            fixed_n += 1
        if fixed_n:
            gaps.append(f"{fixed_n} 只板数按日线校正（上游累计值跨波未重置）；原值见 prev_boards_raw")

    # ④ 分类 + 聚合
    buckets: dict[str, dict[str, Any]] = {}
    for r in rows:
        kind = _classify_one(r, themes, theme_today_cnt)
        r["path"] = kind
        b = buckets.setdefault(kind, {
            "kind": kind,
            "label": PATH_DEFS[kind]["label"],
            "side": PATH_DEFS[kind]["side"],
            "desc": PATH_DEFS[kind]["desc"],
            "count": 0,
            "avg_pct": None,
            "stocks": [],
        })
        b["count"] += 1
        b["stocks"].append({
            "code": r["code"],
            "name": r["name"],
            "pct": r["pct"],
            "prev_boards": r["prev_boards"],
            "theme": r["theme"],
        })

    for b in buckets.values():
        vals = [s["pct"] for s in b["stocks"] if s["pct"] is not None]
        b["avg_pct"] = round(sum(vals) / len(vals), 2) if vals else None
        b["stocks"].sort(key=lambda s: (-(s["pct"] if s["pct"] is not None else -999), s["name"]))

    order = list(PATH_DEFS.keys())
    ordered = sorted(buckets.values(), key=lambda b: order.index(b["kind"]))

    side_count = {SIDE_PROFIT: 0, SIDE_WEAK: 0, SIDE_LOSS: 0, SIDE_NEUTRAL: 0}
    for b in ordered:
        side_count[b["side"]] += b["count"]

    total = len(rows)
    # 主导路径 = 家数最多那条（⛔ 不是按定义顺序取第一条）
    top_profit = max(
        (b for b in ordered if b["side"] == SIDE_PROFIT), key=lambda b: b["count"], default=None
    )
    top_loss = max(
        (b for b in ordered if b["side"] == SIDE_LOSS), key=lambda b: b["count"], default=None
    )

    return {
        "schema": "effect_paths_v1",
        "date": date,
        "prev_date": prev_date,
        "as_of": lt.get("ts") or fb.get("generated_ts"),
        "total": total,
        "prev_zt_count": fb.get("prev_zt_count"),
        "coverage": {
            "prev_pool": len(quotes),
            "today_new": sum(1 for r in rows if r["source"] == "today_new"),
            "classified": total,
        },
        "buckets": ordered,
        "summary": {
            "profit_count": side_count[SIDE_PROFIT],
            "profit_weak_count": side_count[SIDE_WEAK],
            "loss_count": side_count[SIDE_LOSS],
            "neutral_count": side_count[SIDE_NEUTRAL],
            "net_bias": side_count[SIDE_PROFIT] - side_count[SIDE_LOSS],
            "dominant_profit_path": (top_profit["label"] if top_profit else None),
            "dominant_profit_count": (top_profit["count"] if top_profit else 0),
            "dominant_loss_path": (top_loss["label"] if top_loss else None),
            "dominant_loss_count": (top_loss["count"] if top_loss else 0),
        },
        "data_gaps": sorted(set(gaps))[:20],
        "blind_spots": list(BLIND_SPOTS),
    }


def publish(result: dict[str, Any]) -> Path | None:
    """落盘 cache/effect_paths_<YYYY-MM-DD>.json（原子写）。"""
    date = str(result.get("date") or "")
    if not date:
        return None
    path = CACHE / f"effect_paths_{date}.json"
    return path if save_json(path, result) else None


def main() -> int:
    """守护链安全：任何异常都不得让上游 data_ready 判定失败。"""
    if not ENABLE_EFFECT_PATHS:
        print("[effect_paths] 已关闭（ENABLE_EFFECT_PATHS=0）")
        return 0
    try:
        fb = load_json(CACHE / "feedback_matrix.json") or {}
        lt = load_json(CACHE / "pulse_snapshots" / "latest.json") or {}
        yzt = load_json(CACHE / "yesterday_zt.json") or {}
        if not fb or not lt:
            print("[effect_paths] 缺少 feedback_matrix / latest.json，跳过")
            return 0
        result = build(fb, lt, yzt)
        path = publish(result)
        s = result["summary"]
        print(
            f"[effect_paths] {result['date']} 共 {result['total']} 只｜"
            f"赚钱 {s['profit_count']}（主导 {s['dominant_profit_path']}）｜"
            f"亏钱 {s['loss_count']}（主导 {s['dominant_loss_path']}）｜"
            f"净偏 {s['net_bias']:+d} → {path.name if path else '未落盘'}"
        )
    except Exception as exc:  # noqa: BLE001 — 归因是旁路观测，⛔ 不得连累主链
        print(f"[effect_paths] 计算失败（已忽略）：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
