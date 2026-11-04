#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材序列数据中枢。

职责（只做数据，不做展示渲染）：
  1. 集中维护题材映射表 THEME_MAP / norm_theme（单一事实源）
  2. 动态扫描本地沉淀：cache/zt_history/zt_*.json（每日收盘涨停快照，按日归档）
  3. 读取今日盘中：cache/pulse_snapshots/latest.json（top5 板块涨停）
  4. 输出 build_series()：所有交易日（收盘）+ 今日盘中 的题材涨停序列，
     供终端渲染「每日题材炒作变化 / 资金扭转趋势」。

设计要点：
  - 完全本地、动态：每新增一个 zt_history 文件，看板自动多一列，无需改代码。
  - 沉淀不变：只读，不删除/改写任何 zt_history 与 yesterday_zt.json 沉淀文件。
  - 盘中时间轴：每次实时数据发布把当前题材分布按 30 分钟窗写入 zt_history/zt_YYYY-MM-DD.json
    的 intraday_points 字段（并入现有按日归档体系，capture_daily 5 天滚动清理自动覆盖），
    矩阵中当日显示多个盘中时间点列（蓝色标头），与收盘列并存 →
    盘中实时看演变，盘后回看全天「从早炒到晚」的路径。

用法：
  from common.theme_series import build_series, THEME_MAP, norm_theme
  s = build_series()   # -> {"days":[{...}], "theme_labels":[...]}
"""
import json
import glob
import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ZT_HISTORY = ROOT / "cache" / "zt_history"
YESTERDAY_ZT = ROOT / "cache" / "yesterday_zt.json"
LATEST = ROOT / "cache" / "pulse_snapshots" / "latest.json"

# 规范时间戳（2026-10-14 审计 P1-⑦）：generated_ts 统一 ISO8601 带 +08:00。
# 防御性导入（与本文件 trading_calendar 同风格），失败退化为本地 aware 形态。
try:
    from common.market_time import iso_bj
except ImportError:  # pragma: no cover - 包根不在 sys.path 时的兜底
    import sys as _sys
    if str(ROOT) not in _sys.path:
        _sys.path.insert(0, str(ROOT))
    try:
        from common.market_time import iso_bj
    except Exception:
        def iso_bj(_dt=None) -> str:  # type: ignore[misc]
            return (_dt or datetime.datetime.now()).astimezone().isoformat(timespec="seconds")


def _is_trading_day(d: str) -> bool:
    """A股交易日 —— **唯一真源 = common.trading_calendar**（2026-10-09 唯一化）。

    长假幽灵源：`record_intraday_point` 原先只做「时段 + 日期==今日」双重判断，
    休市日若 `latest.json` 被打上当日时间戳，就会给休市日造出 `intraday_only` 空壳
    （实测 `zt_2026-10-01/02/05` 各一份，`intraday_points` 10~11 条、`zt_list` 为空），
    按「文件个数」取窗时占满 5 日窗名额。写入端必须按交易日拦截。
    """
    try:
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(d)
    except Exception:  # pragma: no cover - 极端环境兜底
        try:
            return datetime.datetime.strptime(d, "%Y-%m-%d").weekday() < 5  # trading-day-fallback
        except Exception:
            return True


# ===== 题材映射（单一事实源） =====
# 把东财细分 sector 与聚合板块名统一到核心题材标签，做跨日对齐对比。
# 顺序即「参考优先级」，render 时优先按此顺序排列已知标签。
THEME_MAP = [
    ("半导体/芯片", ["半导体", "芯片"]),
    ("算力硬件/PCB", ["PCB", "元件", "光学光电", "光通信", "光模块", "MLCC", "覆铜板", "算力"]),
    ("通信", ["通信", "5G", "卫星"]),
    ("医药", ["医药", "医疗", "生物"]),
    ("消费电子", ["消费电子", "果链"]),
    ("小金属/磷化铟", ["小金属", "磷化铟", "锗", "钽", "钨"]),
    ("贵金属/黄金", ["黄金", "贵金属"]),
    ("汽车零部件", ["汽车零部", "汽配"]),
    ("电网", ["电网", "特高压"]),
    ("云计算/数据中心", ["云计算", "数据中心", "算力租赁"]),
    ("数字货币", ["数字人民币", "数字货币", "区块链"]),
    ("游戏/AI应用", ["游戏", "文娱", "传媒", "AI应用"]),
    ("化工", ["化学", "塑料", "化工"]),
    ("机器人", ["机器人"]),
    ("计算机/信创", ["计算机", "信创", "软件"]),
]
THEME_LABELS = [label for label, _ in THEME_MAP]


def norm_theme(name):
    """把任一板块/行业名归一到核心题材标签；无法匹配归入「其他」。"""
    if not name:
        return "其他"
    for label, kws in THEME_MAP:
        for kw in kws:
            if kw in name:
                return label
    return "其他"


def _load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_boards(v):
    """robust parse boards value (int / '3' / '3天2板' → 3)."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    import re
    m = re.search(r'\d+', str(v))
    return int(m.group(0)) if m else 0


# ===== 本地动态扫描：每日收盘沉淀 =====
def iter_history():
    """扫描 zt_history/zt_*.json，返回按日期升序的 [(date_raw, snap)] 列表。"""
    items = []
    for p in glob.glob(str(ZT_HISTORY / "zt_*.json")):
        d = _load_json(p)
        if d and d.get("date"):
            items.append((d.get("date_raw") or d["date"].replace("-", ""), d))
    items.sort(key=lambda x: x[0])
    return items


def _load_snap_by_label(date_label):
    """根据日期标签（如 '2026-08-05'）读 zt_history 快照。"""
    return _load_json(ZT_HISTORY / f"zt_{date_label}.json") or _load_json(YESTERDAY_ZT)


def close_day_themes(snap):
    """从一份收盘快照计算 {题材标签: 涨停数}。"""
    out = {}
    if not snap:
        return out
    for it in (snap.get("zt_list") or []):
        lab = norm_theme(it.get("sector"))
        out[lab] = out.get(lab, 0) + 1
    return out


def close_day_details(snap):
    """从一份收盘快照提取每日统计指标（20cm数/连板分布）。"""
    zt = (snap or {}).get("zt_list") or []
    return {
        "count_20cm": sum(1 for it in zt if it.get("is_20cm")),
        "boards_2plus": sum(1 for it in zt if it.get("boards", 0) >= 2),
        "boards_4plus": sum(1 for it in zt if it.get("boards", 0) >= 4),
    }


def close_day_theme_leaders(snap):
    """从收盘快照提取每题材的龙头股（当天该题材中连板数最高的那只）。"""
    leaders = {}
    for it in (snap.get("zt_list") or []):
        lab = norm_theme(it.get("sector"))
        if lab == "其他":
            continue
        cur = leaders.get(lab)
        if not cur or _parse_boards(it.get("boards")) > cur.get("boards", 0):
            leaders[lab] = {
                "name": it.get("name", ""), "code": it.get("code", ""),
                "boards": _parse_boards(it.get("boards")), "pct": it.get("pct"),
            }
    return leaders


# ===== 本地动态读取：今日盘中 =====
def load_today_intraday():
    """从 latest.json 取今日盘中板块涨停分布；无数据返回 None。"""
    d = _load_json(LATEST)
    if not d:
        return None
    themes = {}
    for t in (d.get("top5") or []):
        lab = norm_theme(t.get("name"))
        themes[lab] = themes.get(lab, 0) + int(t.get("zt_count") or 0)
    mb = 0
    for grp in (d.get("ladder") or []):
        b = grp.get("boards") if isinstance(grp, dict) else None
        if isinstance(b, int):
            mb = max(mb, b)
    return {
        "themes": themes,
        "max_boards": mb,
        "total": d.get("total_zt"),
        "ts": (d.get("ts") or "").replace("T", " ").replace("+08:00", "")[:16],
        "sentiment": ((d.get("sentiment_detail") or {}).get("score")
                      if (d.get("sentiment_detail") or {}).get("source") == "feedback_matrix" else None),
        "sentiment_model": (d.get("sentiment_detail") or {}).get("model"),
        "seal_rate": (d.get("sentiment_detail") or {}).get("seal_rate"),
        "broken_count": (d.get("broken") or {}).get("count"),
        # 盘中主题龙头（来自 leaders 数组）
        "theme_leaders": _intraday_theme_leaders(d),
    }


def _intraday_theme_leaders(d):
    """从 latest.json 的 leaders 数组提取每题材龙头。"""
    leaders = {}
    for l in (d.get("leaders") or []):
        sector = norm_theme(l.get("sector") or l.get("板块", ""))
        if sector == "其他" or not l.get("name"):
            continue
        cur = leaders.get(sector)
        if not cur or _parse_boards(l.get("boards")) > cur.get("boards", 0):
            leaders[sector] = {
                "name": l.get("name", ""), "code": l.get("code", ""),
                "boards": _parse_boards(l.get("boards")), "pct": l.get("pct"),
            }
    return leaders


# ===== 盘中时间点沉淀（30 分钟窗去重，并入 zt_history 按日文件） =====
def record_intraday_point():
    """把当前 latest.json 的板块涨停分布按 30 分钟窗写入 zt_history/zt_YYYY-MM-DD.json 的
    intraday_points 字段（复用现有按日归档体系，capture_daily 5 天滚动清理自动覆盖）。

    同窗只保留最新一条（ts 保留真实时刻，展示时用 HH:MM）。
    **非A股交易日**（唯一真源判定）、非交易时段（09:15~15:05 之外）、或快照日期非今日、
    或无题材数据时，一律自动跳过。
    返回 (时刻, 当天点数) 或 None。
    """
    t = load_today_intraday()
    if not t or not t.get("themes") or not t.get("ts"):
        return None
    ts = t["ts"]  # "YYYY-MM-DD HH:MM"
    try:
        date, hm = ts[:10], ts[11:16]
        hh, mm = int(hm[:2]), int(hm[3:])
    except (ValueError, IndexError):
        return None
    hm_min = hh * 60 + mm
    if not (555 <= hm_min <= 905):  # 09:15 ~ 15:05
        return None
    # ⛔ 2026-09-14 修：快照日期非今日 → 只读不写，禁止给**历史日**造/改文件。
    # 事故链：周一盘前 latest.json 仍是周五收盘快照(ts=周五15:00) → 本函数按 ts 判为「周五」，
    # 于是给周五造出一个 intraday_only 的 zt_2026-09-11.json；该文件当日盘后链早已跑完，
    # 永远补不上 zt_list → 形态/N字/回流检测永久少一天料（09-11 实证）。
    if date != datetime.date.today().strftime("%Y-%m-%d"):
        return None
    # ⛔ 2026-10-09 唯一化：非A股交易日一律不沉淀 —— 防长假空壳 `zt_<休市日>.json`
    # （实测 2026-10-01/02/05 各一份；空壳会按「文件个数」占满 5 日窗名额）。
    if not _is_trading_day(date):
        return None
    window = f"{hh:02d}:{'30' if mm >= 30 else '00'}"
    ZT_HISTORY.mkdir(parents=True, exist_ok=True)
    path = ZT_HISTORY / f"zt_{date}.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if not data.get("date"):
        data = {"date": date, "date_raw": date.replace("-", ""),
                "intraday_only": True, "intraday_points": []}
    pts = [p for p in (data.get("intraday_points") or []) if p.get("window") != window]
    pts.append({
        "window": window,
        "ts": ts,
        "themes": t["themes"],
        "total": t.get("total"),
        "max_boards": t.get("max_boards"),
    })
    pts.sort(key=lambda p: p.get("window", ""))
    data["intraday_points"] = pts
    from common.json_io import save_json
    if not save_json(path, data, indent=1):
        raise OSError(f"盘中题材时间点写入失败: {path}")
    return (hm, len(pts))


# ===== 统一输出：多日题材序列 =====
def build_series():
    """返回 {days:[...], theme_labels:[...]}。

    days 元素：
      kind        'close' | 'intraday'
      date_label  展示用（收盘=YYYY-MM-DD；盘中=今日盘中 HH:MM）
      date_raw    排序键（收盘=YYYYMMDD；盘中=99999999，恒排末尾）
      themes      {题材: 涨停数}
      max_boards  连板最高板
      total       当日涨停总数
      ts          生成时间
    theme_labels：跨日并集，已知标签按 THEME_MAP 顺序、其余按名排序。
    """
    days = []
    for raw, snap in iter_history():
        # 盘中预览文件（仅 intraday_points、无收盘数据）不产生收盘列
        if not snap.get("intraday_only"):
            days.append({
                "kind": "close",
                "date_label": snap.get("date"),
                "date_raw": raw,
                "themes": close_day_themes(snap),
                "max_boards": snap.get("max_boards"),
                "total": snap.get("zt_count"),
                "ts": snap.get("generated_ts"),
            })
        # 同日盘中时间点（zt_history 文件内 intraday_points 字段，30 分钟窗）：
        # 与收盘列并存 → 盘中实时看演变、盘后回看全天「从早炒到晚」路径
        for p in sorted((snap.get("intraday_points") or []),
                        key=lambda x: x.get("window", "")):
            days.append({
                "kind": "intraday",
                "date_label": (p.get("ts") or "")[11:16],   # 精确时刻 HH:MM
                "date_raw": "99999999",
                "themes": p.get("themes") or {},
                "max_boards": p.get("max_boards"),
                "total": p.get("total"),
                "ts": p.get("ts"),
            })

    # 题材标签并集（稳定顺序）
    all_labels = set()
    for d in days:
        all_labels |= set(d["themes"].keys())
    ordered = [l for l in THEME_LABELS if l in all_labels]
    ordered += sorted(l for l in all_labels if l not in THEME_LABELS)

    return {"days": days, "theme_labels": ordered}


def last_two_days(series):
    """取序列末尾两天（收盘末日 + 今日盘中，若存在），供「最新资金扭转」明细块。"""
    days = series.get("days") or []
    if not days:
        return None, None
    intraday = next((d for d in reversed(days) if d["kind"] == "intraday"), None)
    closes = [d for d in days if d["kind"] == "close"]
    last_close = closes[-1] if closes else None
    if intraday:
        return last_close, intraday
    # 无盘中（已收盘）：取最后两个收盘日做对比
    if len(closes) >= 2:
        return closes[-2], closes[-1]
    return None, last_close


# ===== 中转文件导出：供趋势面板（ECharts）读取 =====
SERIES_JSON = ROOT / "cache" / "theme_series.json"


def export_series_json(max_days=5, out_path=None):
    """从 build_series() 取最近 max_days 天，输出 chart-ready JSON 中转文件。

    字段（增强版）：
      dates / daily_totals / max_boards          ← 日级别汇总
      daily_details [{total, count_20cm, boards_2plus, boards_4plus,
                      sentiment, seal_rate, broken}]  ← 每日深度指标
      themes {label: [day1, day2, ...]}          ← 主题涨停数序列(null=无数据)
      theme_leaders {label: [{name,code,boards,pct},...]}  ← 每日龙头股
      theme_labels / table_rows                  ← 按强度排序的表格数据
    """
    if out_path is None:
        out_path = SERIES_JSON
    s = build_series()
    all_days = s["days"]
    labels = [L for L in s["theme_labels"] if L != "其他"]
    recent = all_days[-max_days:] if len(all_days) > max_days else all_days

    dates = [d["date_label"] for d in recent]
    daily_totals = [d.get("total") for d in recent]
    max_boards = [d.get("max_boards") for d in recent]

    # —— 每日深度指标 ——
    daily_details = []
    for d in recent:
        detail = {
            "total": d.get("total"), "max_boards": d.get("max_boards"),
            "count_20cm": None, "boards_2plus": None, "boards_4plus": None,
            "sentiment": None, "seal_rate": None, "broken": None,
        }
        if d["kind"] == "close":
            snap = _load_snap_by_label(d["date_label"])
            if snap:
                detail.update(close_day_details(snap))
        elif d["kind"] == "intraday":
            t = load_today_intraday()
            if t:
                detail["sentiment"] = t.get("sentiment")
                detail["seal_rate"] = t.get("seal_rate")
                detail["broken"] = t.get("broken_count")
        daily_details.append(detail)

    # —— 主题涨停数序列 ——
    themes = {}
    for L in labels:
        themes[L] = [d["themes"].get(L) or None for d in recent]

    # —— 每日主题龙头 ——
    theme_leaders = {L: [] for L in labels}
    for d in recent:
        if d["kind"] == "close":
            snap = _load_snap_by_label(d["date_label"])
            day_ld = close_day_theme_leaders(snap) if snap else {}
        else:
            t = load_today_intraday()
            day_ld = t.get("theme_leaders", {}) if t else {}
        for L in labels:
            theme_leaders[L].append(day_ld.get(L))

    # —— 表格行（按复合强度排序：今日涨停数×10 + 正delta×5 + 龙头连板×3） ——
    def _strength(row):
        today = row["values"][-1] or 0
        delta = row["delta"]
        ld = row.get("leader_today") or {}
        return today * 10 + max(0, delta) * 5 + ld.get("boards", 0) * 3

    table_rows = []
    for L in labels:
        vals = themes[L]
        ldr = theme_leaders[L]
        if len(vals) >= 2:
            last = vals[-1] or 0
            prev = vals[-2] or 0
            delta = last - prev
            trend = "up" if delta > 0 else ("down" if delta < 0 else "flat")
        else:
            delta = 0
            trend = "flat"
        leader_today = ldr[-1] if ldr else None
        table_rows.append({
            "theme": L,
            "values": vals,
            "delta": delta,
            "delta_str": f"+{delta}" if delta > 0 else (f"{delta}" if delta < 0 else "0"),
            "trend": trend,
            "leader_today": leader_today,
        })

    table_rows.sort(key=_strength, reverse=True)

    out = {
        "generated_ts": iso_bj(),
        "dates": dates,
        "daily_totals": daily_totals,
        "max_boards": max_boards,
        "daily_details": daily_details,
        "themes": themes,
        "theme_leaders": theme_leaders,
        "theme_labels": [r["theme"] for r in table_rows],  # 排序后的标签顺序
        "table_rows": table_rows,
    }

    from common.json_io import save_json
    if not save_json(out_path, out, indent=2):
        raise OSError(f"题材序列写入失败: {out_path}")
    print(f"[theme_series] ✅ 中转文件已生成: {out_path} ({len(dates)} 天, {len(labels)} 题材, "
          f"{len(daily_details)} 日细节)")
    return out


if __name__ == "__main__":
    s = build_series()
    print(f"交易日数(含盘中): {len(s['days'])} | 题材标签数: {len(s['theme_labels'])}")
    for d in s["days"]:
        print(f"  {d['date_label']:>16} [{d['kind']:>8}] 涨停{d['total']} 最高板{d['max_boards']} "
              f"题材{len(d['themes'])}个")
    print("标签:", s["theme_labels"])
