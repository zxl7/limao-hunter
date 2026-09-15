#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脉冲窗口汇总层（档位A·2026-08-15 落地）。

解决问题:
  - live_signal 每条新信号立即各推一张 → 盘中刷屏、干巴巴
  - 11盘中猎手AI任务每次从 latest.json 全量重拼 → 重复话语、无增量脉络
办法:
  常规信号(涨停/涨幅突增/强势/快讯/板块异动)先入「窗口缓冲」，按时间窗口(默认15分钟)
  聚合；窗口闭合时生成一张「窗口汇总卡」(本窗口事件 + 相对上一窗口增量Δ + 一句机器小结)
  推送。紧急信号(核按钮/回流前兆)仍由 live_signal 即时推，不进缓冲。

数据流:
  live_signal.py (--buffer)  →  append_events([...])     →  cache/pulse_buffer.json
  pulse_aggregator.py --close now            → 闭合当前窗口 → 聚合 → 生成卡 → 推送
  pulse_aggregator.py --simulate 2026-08-14  → 从真实缓存还原当天脉冲流 → 分窗口 → 逐个闭合

用法:
  python3 pulse_aggregator.py --simulate 2026-08-14 --dry-run
  python3 pulse_aggregator.py --simulate 2026-08-14 --push --webhook feishu_webhook_test
  python3 pulse_aggregator.py --close now --push --webhook feishu_webhook_test
"""
from __future__ import annotations
import argparse, json, os, sys, tempfile
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / "cache"
BUFFER = CACHE / "pulse_buffer.json"
WDIR = CACHE / "pulse_windows"
PUSH = HERE / "push_card.py"
PY = sys.executable

WINDOW_MIN = 5  # 窗口粒度(分钟)；2026-08-18 从 15→5（用户「盯盘脉冲卡太慢」：15min 聚合让 10:00 信号 10:15 才推，时效损失大）
MIN_EVENTS = 2  # 最小出卡事件数；2026-08-18 用户「卡太频繁」：单条弱信号(如1条快讯)不推，≥2条才聚合出卡（空窗口仍跳过）
VOLUME_MIN_EVENTS = 15  # 量触发阈值：窗口内有题材爆发且事件≥15 条 → 不等5分钟边界立即闭合推送（2026-08-20 用户「早盘5分钟累积不止一张」）
VOLUME_COOLDOWN = 120  # 量触发最小间隔(秒)；防 launchd 30s 循环连续触发刷屏
# 2026-08-19 用户「无题材爆发窗口：个股累积10个左右再推」：无题材爆发时跨窗口累积个股(去重)，
# 攒够 FOCUS_MIN_STOCKS 或跨 FOCUS_MAX_WINDOWS 窗(约20min兜底)才推一张「个股聚焦卡」，不逐窗刷屏
FOCUS_MIN_STOCKS = 10
FOCUS_MAX_WINDOWS = 4
FOCUS_FILE = WDIR / "focus_buffer.json"
SNAP = CACHE / "pulse_snapshots"
BOOST_STATE = SNAP / "boost_state.json"  # 板块加强状态：{date, sectors:{名称:{count, first_wk, peak_zt}}}（2026-08-25 首/续加强区分）
BJ = timezone(timedelta(hours=8))

# 配色(飞书命名色, 与 shared_rules 一致)
C_RED = "red"        # 涨停/标杆
C_PURPLE = "purple"  # 机会/强势/板块
C_TURQ = "turquoise" # 增量/中性解读


def now_bj() -> datetime:
    return datetime.now(BJ)


def window_key(dt: datetime) -> str:
    """时间按 WINDOW_MIN 桶取整 → YYYYMMDD_HHMM (如 20260814_0930)。"""
    m = (dt.minute // WINDOW_MIN) * WINDOW_MIN
    return dt.strftime("%Y%m%d_") + f"{dt.hour:02d}{m:02d}"


def window_label(wk: str) -> str:
    try:
        return datetime.strptime(wk, "%Y%m%d_%H%M").strftime("%H:%M")
    except Exception:
        return wk


# ── 缓冲读写 ────────────────────────────────────────
def load_buffer() -> dict:
    try:
        return json.loads(BUFFER.read_text(encoding="utf-8"))
    except Exception:
        return {"windows": {}, "updated": ""}


def save_buffer(buf: dict) -> None:
    BUFFER.parent.mkdir(parents=True, exist_ok=True)
    buf["updated"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
    BUFFER.write_text(json.dumps(buf, ensure_ascii=False, indent=2), encoding="utf-8")


def append_events(events: list, ts: datetime = None) -> int:
    """把一批事件写入对应时间窗口缓冲。返回去重后新增条数。
    event: {type, code?, name?, sector?, pct?, boards?, is_20cm?, title?}
    type ∈ {zt, strong, surge, nf, sector}
    """
    buf = load_buffer()
    wins = buf.setdefault("windows", {})
    ts = ts or now_bj()
    added = 0
    for ev in events:
        t = ev.get("type")
        if not t:
            continue
        wk = ev.get("window") or window_key(ts)
        lst = wins.setdefault(wk, [])
        key_id = ev.get("code") or ev.get("title") or ev.get("name")
        dup = any(e.get("type") == t and
                  (e.get("code") or e.get("title") or e.get("name")) == key_id
                  for e in lst)
        if dup:
            continue
        e2 = dict(ev)
        e2["ts"] = ts.strftime("%Y-%m-%d %H:%M:%S")
        lst.append(e2)
        added += 1
    save_buffer(buf)
    return added


# ── 聚合 ────────────────────────────────────────────
_THEMES_CACHE = {"ts": 0.0, "data": None}
SEQ = CACHE / "theme_sequence.json"  # 当日题材轮动序列（按首次出现顺序·跨窗口去重累计）


def load_seq() -> dict:
    try:
        return json.loads(SEQ.read_text(encoding="utf-8"))
    except Exception:
        return {}


def bump_themes(names: list, date: str = None) -> list:
    """把新题材名按首次出现顺序加入当日序列(去重)，返回更新后的完整序列。
    轮动题材计数 = len(序列)：题材轮动越多越难赚钱（≥5 电风扇·防守优先）。"""
    d = date or now_bj().strftime("%Y-%m-%d")
    s = load_seq()
    if s.get("date") != d:
        s = {"date": d, "themes": []}
    themes = s["themes"]
    orig_len = len(themes)
    for nm in names:
        if nm and nm not in themes:
            themes.append(nm)
    if len(themes) != orig_len:
        s["updated"] = now_bj().strftime("%Y-%m-%d %H:%M:%S")
        SEQ.parent.mkdir(parents=True, exist_ok=True)
        SEQ.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return themes


def load_sector_pushed(date: str = None) -> list:
    """当日已板块级爆发的板块名列表（live_signal 写入 pushed_signals_{date}.json 的 sector_pushed）。
    早盘板块级爆发(zt_count≥3)推过板块强势卡后，盘中个股续聚应识别为「板块加强」而非「非板块爆发」
    （2026-08-25 用户「早盘医药爆发过·盘中个股又爆发·叠加为医药加强」）。"""
    d = date or now_bj().strftime("%Y%m%d")
    try:
        sig = json.loads((CACHE / f"pushed_signals_{d}.json").read_text(encoding="utf-8"))
        return list(sig.get("sector_pushed") or [])
    except Exception:
        return []


def is_boost(main_line: str, sector_pushed: list) -> bool:
    """个股归集 main_line 是否命中当日已板块级爆发板块（子串双向匹配，兼容「医药」vs「化学制药」粒度差异）。"""
    if not main_line or main_line == "—":
        return False
    for nm in sector_pushed:
        if nm and (nm in main_line or main_line in nm):
            return True
    return False


def _match_boost_sector(main_line: str, sector_pushed: list) -> str:
    """返回命中的板块名（is_boost 的取名版本）；未命中返回 ''。"""
    if not main_line or main_line == "—":
        return ""
    for nm in sector_pushed:
        if nm and (nm in main_line or main_line in nm):
            return nm
    return ""


def _load_latest() -> dict:
    """读 latest.json（板块资金流/涨停数佐证）；失败返回 {}（数据即事实，不编造）。"""
    try:
        return json.loads((SNAP / "latest.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_boost_state() -> dict:
    """读板块加强状态；跨日自动重置。"""
    try:
        s = json.loads(BOOST_STATE.read_text(encoding="utf-8"))
        if s.get("date") == now_bj().strftime("%Y-%m-%d"):
            return s
    except Exception:
        pass
    return {"date": now_bj().strftime("%Y-%m-%d"), "sectors": {}}


def _save_boost_state(s: dict) -> None:
    SNAP.mkdir(parents=True, exist_ok=True)
    BOOST_STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def _sector_flow(nm: str, latest: dict = None) -> dict:
    """板块资金方向（sector_risk 主力净流入/流出 Top5）；不在榜返回 None（不编造）。"""
    if not nm:
        return None
    latest = latest if latest is not None else _load_latest()
    sr = latest.get("sector_risk") or {}
    for f in sr.get("inflow_top") or []:
        if str(f.get("name") or "") == nm:
            return {"dir": "in", "flow_yi": float(f.get("flow_yi") or 0)}
    for f in sr.get("outflow_top") or []:
        if str(f.get("name") or "") == nm:
            return {"dir": "out", "flow_yi": abs(float(f.get("flow_yi") or 0))}
    return None


def boost_status(main_line: str, sector_pushed: list, latest: dict = None,
                 wk: str = None, date: str = None) -> dict:
    """板块加强完整判定（2026-08-25 用户「叠加为加强」+ 同日优化：资金验证+首/续区分）：
    - 未命中已爆发板块 → 返回 None（维持个股归集口径）
    - 命中 → 返回 {name, first(首次/续), count(第N次), zt_now, zt_peak, flow}
    防刷屏：同板块持续加强只轻提示（first=False），涨停数创新高才再强调。"""
    nm = _match_boost_sector(main_line, sector_pushed)
    if not nm:
        return None
    d = date or now_bj().strftime("%Y-%m-%d")
    state = _load_boost_state()
    sectors = state.setdefault("sectors", {})
    ent = sectors.get(nm) or {"count": 0, "peak_zt": 0}
    ent["count"] += 1
    # 涨停数（latest.top5 当前 zt_count）用于"强度创新高"再强调
    latest = latest if latest is not None else _load_latest()
    zt_now = 0
    for s in latest.get("top5") or []:
        if str(s.get("name") or "") == nm:
            zt_now = int(s.get("zt_count") or 0)
            break
    first = ent["count"] == 1
    new_high = (not first) and zt_now > (ent.get("peak_zt") or 0)
    if new_high or first:
        ent["peak_zt"] = max(ent.get("peak_zt") or 0, zt_now)
    sectors[nm] = ent
    _save_boost_state(state)
    return {"name": nm, "first": first, "count": ent["count"],
            "zt_now": zt_now, "zt_peak": ent.get("peak_zt") or zt_now,
            "new_high": new_high, "flow": _sector_flow(nm, latest)}


def load_themes() -> list:
    """读 theme_rotation.json 的 candidates（题材跟踪池），带 mtime 缓存；失败返回[]。"""
    try:
        p = CACHE / "theme_rotation.json"
        st = p.stat().st_mtime
        if _THEMES_CACHE["data"] is None or st != _THEMES_CACHE["ts"]:
            tr = json.loads(p.read_text(encoding="utf-8"))
            _THEMES_CACHE["data"] = tr.get("candidates") or []
            _THEMES_CACHE["ts"] = st
        return _THEMES_CACHE["data"]
    except Exception:
        return []


def _stamp_sector_stage(sector: list, date: str = None) -> None:
    """板块事件标注题材阶段 + 今日题材序数（真实数据·theme_rotation 跟踪池）：
    爆发期/回流候选/无回流迹象；不在池=新爆发首见。盘中强势=题材爆发≠主线。"""
    cands = load_themes()
    for s in sector:
        nm = s.get("name") or ""
        stage = "✨新爆发"
        for c in cands:
            th = c.get("theme") or ""
            if th and (th in nm or nm in th):
                ph = c.get("phase")
                if ph == "boom":
                    stage = f"🔥爆发期({str(c.get('boom_day', ''))[-5:]})"
                elif ph == "re-entry-candidate":
                    stage = f"📎回流候选(冷却{c.get('cooled_days', '?')}天)"
                else:
                    stage = "💤无回流迹象"
                break
        s["stage"] = stage
    # 今日题材序数（跨窗口去重累计，按首次出现顺序）
    names = [s.get("name") for s in sector if s.get("name")]
    if names:
        themes = bump_themes(names, date)
        for s in sector:
            nm = s.get("name")
            if nm in themes:
                s["seq_no"] = themes.index(nm) + 1
                s["seq_total"] = len(themes)


def _money_effect() -> dict:
    """读当日赚钱效应（feedback_matrix）；date 非当日或缺失则返回 None（数据即事实，不引用）。"""
    try:
        fb = json.loads((CACHE / "feedback_matrix.json").read_text(encoding="utf-8"))
        # date 字段 2026-08-19 前缺失（feedback_matrix 只写 generated_ts）→ 兼容取 generated_ts 前10位
        d = fb.get("date") or (fb.get("generated_ts") or "")[:10]
        if not d or d != now_bj().strftime("%Y-%m-%d"):
            return None
        if fb.get("promote_rate") is None:
            return None
        return {"promote_rate": fb.get("promote_rate"), "nuke_count": fb.get("nuke_count", 0),
                "sentiment": fb.get("sentiment_score", 0),
                "avg_pct": fb.get("avg_pct"),
                "trend": fb.get("trend") or {}}
    except Exception:
        return None


def summarize_window(wk: str, buf: dict, prev_summary: dict = None) -> dict:
    evs = buf.get("windows", {}).get(wk, [])
    zt, strong, surge, nf, sector, charge = [], [], [], [], [], []
    for e in evs:
        t = e.get("type")
        if t == "zt": zt.append(e)
        elif t == "strong": strong.append(e)
        elif t == "surge": surge.append(e)
        elif t == "nf": nf.append(e)
        elif t == "sector": sector.append(e)
        elif t == "charge": charge.append(e)
    # 20cm涨停票已在 zt, 从 strong/charge 去重避免重复计数
    zt_codes = {z.get("code") for z in zt}
    strong = [e for e in strong if e.get("code") not in zt_codes]
    charge = [e for e in charge if e.get("code") not in zt_codes]
    # 题材阶段标注 + 今日序数（盘中强势=题材爆发≠主线；主线需3日强度+赚钱效应持续迭代检验）
    d = f"{wk[:4]}-{wk[4:6]}-{wk[6:8]}" if len(wk) >= 8 else None
    _stamp_sector_stage(sector, date=d)
    # 增量Δ vs 上一窗口
    prev_sectors = set((prev_summary or {}).get("sectors", []))
    prev_zt_total = (prev_summary or {}).get("zt_total", 0)
    cur_sectors = [s.get("name") for s in sector if s.get("name")]
    new_sectors = [s for s in cur_sectors if s not in prev_sectors]
    zt_total = prev_zt_total + len(zt)
    # 主线: 优先板块异动(最代表主线) → 其次个股 sector 频次; 全分散则"多题材并行"
    sector_names = [s.get("name") for s in sector if s.get("name")]
    if sector_names:
        main_line = "、".join(sector_names[:2])
    else:
        cnt = Counter()
        for e in zt + strong:
            s = e.get("sector")
            if s: cnt[s] += 1
        if cnt:
            top = cnt.most_common(2)
            main_line = "多题材并行" if top[0][1] == 1 else top[0][0]
        else:
            main_line = "—"
    return {
        "zt": zt, "strong": strong, "surge": surge, "nf": nf, "sector": sector,
        "charge": charge,
        "sectors": cur_sectors, "new_sectors": new_sectors,
        "zt_total": zt_total, "main_line": main_line,
    }


def _pct(e: dict) -> str:
    p = e.get("pct")
    return f"+{p}%" if isinstance(p, (int, float)) else ""


def _boards(e: dict) -> str:
    b = e.get("boards")
    if not b:
        return ""
    s = str(b).strip()
    if s.isdigit():
        return f"〔{s}板〕"
    return f"〔{s}〕"


def _insight(summary: dict, prev: dict = None, boost: dict = None) -> str:
    """小结·一句话行情（2026-08-19 用户「不要说这么多」）：{窗口现象}｜{晋级率/核按钮/情绪} {基调}。
    话术结构稳定，措辞随真实数据分档：情绪分(≥6回暖/3~6中性/<3冰点防守)，晋级率<30%或核按钮≥5追加回避高位。
    2026-08-25 板块加强：个股归集命中当日已板块级爆发板块 → 「板块加强」替代「非板块爆发」。
    boost：build_window_card 已算好的加强状态（首/续/资金），传入避免重复计数。”"""
    zt_n, st_n = len(summary["zt"]), len(summary["strong"])
    cm_n = f"{zt_n}只涨停" + (f"+{st_n}只20cm" if st_n else "")
    ml = summary.get("main_line", "—")
    new_sec = summary.get("new_sectors") or []
    eff = _money_effect()
    eff_txt = ""
    mood = ""
    if eff:
        pr, nk, st = eff["promote_rate"], eff["nuke_count"], eff["sentiment"]
        eff_txt = f"晋级率{pr}% 核按钮{nk} 情绪{st}/10"
        if st >= 6:
            mood = "情绪回暖，前排可看"
        elif st >= 3:
            mood = "情绪中性，去弱留强"
        else:
            mood = "冰点退潮，防守为先"
        if pr < 30 or nk >= 5:
            mood += "，回避高位"
        elif pr >= 50 and nk <= 1:
            mood += "，接力顺畅"
        # 2026-08-20 用户「晋级率越来越低、溢价越来越低、越不好做」：趋势防守信号
        trend = eff.get("trend") or {}
        dl = trend.get("defense_level") or 0
        ap = eff.get("avg_pct")
        if dl >= 2:
            mood += "，⚠️晋级率/溢价连续走弱·防守优先"
        elif dl == 1:
            mood += "，接力氛围走弱·谨慎"
        elif ap is not None and ap < 0:
            mood += f"，昨日涨停今均{ap}%·溢价为负"
    if prev is None:
        # 首个窗口开盘定调：有板块事件称「题材」；仅个股归集时禁称题材（2026-08-21 口径统一）
        if summary["sector"]:
            phen = f"开盘{cm_n}，题材{ml}"
        elif ml and ml != "—":
            phen = f"开盘{cm_n}，个股归集【{ml}】"
        else:
            phen = f"开盘{cm_n}，无明确题材"
    elif new_sec:
        phen = f"新题材爆发【{('、'.join(new_sec[:2]))}】，{cm_n}共振"
    elif ml and ml != "—":
        # 2026-08-20 口径统一：无板块级爆发、仅个股归集（个股 sector 频次聚合，非板块信号）
        # 2026-08-21 修：删「题材【X】」前缀——归集题材≠板块题材，禁称主线（题材≠主线纪律）
        # 2026-08-25 加：个股归集命中当日 sector_pushed（早盘已板块级爆发）→ 升级「板块加强」；
        # 同日二迭代：资金验证（主力流入=真加强/流出=资金背离）+ 首/续区分（持续只轻提示）
        bst = boost or None
        if bst:
            nm, first = bst["name"], bst["first"]
            tag = "板块加强" if first else "板块持续"
            if bst.get("new_high"):
                tag = "板块加强·强度新高"
            flow = bst.get("flow")
            flow_txt = ""
            if flow and flow["dir"] == "in":
                flow_txt = f"💰主力净流入 +{flow['flow_yi']:.1f}亿"
            elif flow and flow["dir"] == "out":
                flow_txt = f"⚠️资金背离·主力净流出 -{flow['flow_yi']:.1f}亿"
            fz = f"｜{flow_txt}" if flow_txt else ""
            phen = f"{tag}【{nm}】（早盘板块级爆发+盘中续聚·第{bst['count']}次）{fz}，{cm_n}共振"
        else:
            phen = f"个股归集【{ml}】（非板块爆发），{cm_n}共振"
    else:
        phen = f"{cm_n}，无明确题材"
    core = f"{phen}｜{eff_txt}，{mood}" if eff else phen
    return f"<font color='{C_TURQ}'>{core}。</font>"


def _is_st(ev: dict) -> bool:
    """⛔ ST 过滤（铁律④·2026-08-26 用户抓包 ST海龙进脉冲卡）：
    个股事件 name 以 ST/*ST 开头 → 丢弃（渲染级兜底，源头 intraday_pulse 已过滤）。"""
    nm = str((ev or {}).get("name") or "")
    return nm.startswith("ST") or nm.startswith("*ST")


def build_window_card(summary: dict, wk: str, prev_summary: dict = None,
                      label_wk: str = None, focus: bool = False, is_span: bool = False) -> dict:
    # 2026-08-19 修：label_wk=推送边界时刻，消除「09:40 脉冲 09:45 才推」的 5 分钟命名偏移。
    # 数据桶 wk 仍用于查缓冲/文件名/防重；卡片标题/note 用 label_wk（缺省回退 wk）。
    # 2026-08-19 focus：无题材爆发窗口 → 个股聚焦卡（约10只关键个股），不再整窗跳过。
    label = window_label(label_wk or wk)
    zt = [e for e in (summary["zt"]) if not _is_st(e)]
    strong = [e for e in (summary["strong"]) if not _is_st(e)]
    surge = [e for e in (summary["surge"]) if not _is_st(e)]
    nf, sector = (summary["nf"], summary["sector"])
    charge = [e for e in (summary.get("charge") or []) if not _is_st(e)]
    max_zt = 6 if focus else 8
    max_st = 3 if focus else 8
    max_ch = 2 if focus else 6
    max_nf = 2 if focus else 4
    lines = []
    # 首屏定调（标题固定·写入模板底层；冒号后换行、内容缩进，布局不杂乱）
    # 2026-08-25 板块加强：无板块事件但个股归集命中当日 sector_pushed（早盘已板块级爆发）→ 升级语义
    # 同日二迭代：资金验证（主力流入=真加强/流出=资金背离）+ 首/续区分（持续只轻提示防刷屏）
    _bst = focus and boost_status(summary.get("main_line") or "", load_sector_pushed())
    if nf:
        core = "；".join(n.get("title", "")[:42] for n in nf[:3])
    else:
        ml = summary.get("main_line") or "—"
        if sector:
            core = f"本窗口 {len(zt)}只涨停 / {len(strong)}只20cm强势 / {len(sector)}个题材爆发"
        elif ml and ml != "—":
            if _bst:
                nm, first = _bst["name"], _bst["first"]
                tag = "板块加强" if first else "板块持续"
                if _bst.get("new_high"):
                    tag = "板块加强·强度新高"
                flow = _bst.get("flow")
                flow_txt = ""
                if flow and flow["dir"] == "in":
                    flow_txt = f"💰主力净流入 +{flow['flow_yi']:.1f}亿"
                elif flow and flow["dir"] == "out":
                    flow_txt = f"⚠️资金背离·主力净流出 -{flow['flow_yi']:.1f}亿"
                fz = f"｜{flow_txt}" if flow_txt else ""
                core = (f"本窗口 {len(zt)}只涨停 / {len(strong)}只20cm强势 / "
                        f"{tag}【{nm}】（第{_bst['count']}次）{fz}")
            else:
                core = f"本窗口 {len(zt)}只涨停 / {len(strong)}只20cm强势 / 无板块爆发·个股归集【{ml}】"
        else:
            core = f"本窗口 {len(zt)}只涨停 / {len(strong)}只20cm强势 / 无题材爆发"
    lines.append("**📡 本窗口核心**：")
    lines.append(f"　{core}")
    # 快讯
    for n in nf[:max_nf]:
        lines.append(f"　📰 {n.get('title', '')}")
    # 冲板预警（接近涨停·未封板，先于涨停播报）
    if charge:
        lines.append(f"**🚀 冲板预警 {len(charge)}只**（接近涨停·未封板）：")
        for e in charge[:max_ch]:
            sc = f"〔{e.get('sector', '')}〕" if e.get("sector") else ""
            lines.append(f"　<font color='{C_PURPLE}'>{e.get('name')}({e.get('code')}) {_pct(e)}{sc}</font>")
    # 新涨停
    if zt:
        lines.append(f"**🔥 新涨停 {len(zt)}只**：")
        for e in zt[:max_zt]:
            bd = _boards(e)
            sc = f"〔{e.get('sector', '')}〕" if e.get("sector") else ""
            lines.append(f"　<font color='{C_RED}'>{e.get('name')}({e.get('code')}) {_pct(e)} {bd}{sc}</font>")
    # 20cm强势
    if strong:
        lines.append(f"**🔆 20cm强势 {len(strong)}只**（未封板·埋伏）：")
        for e in strong[:max_st]:
            sc = f"〔{e.get('sector', '')}〕" if e.get("sector") else ""
            lines.append(f"　<font color='{C_PURPLE}'>{e.get('name')}({e.get('code')}) {_pct(e)}{sc}</font>")
    # 板块异动
    if sector:
        lines.append(f"**📊 题材爆发 {len(sector)}个**：")
        for s in sector[:6]:
            zc = s.get("zt_count")
            zcs = f"｜{zc}只涨停" if zc else ""
            ld = s.get("leader_name")
            lds = f"｜龙头 {ld}" if ld else ""
            stg = s.get("stage") or ""
            stgs = f"｜{stg}" if stg else ""
            seq = f"｜今日题材#{s.get('seq_no')}" if s.get("seq_no") else ""
            lines.append(f"　<font color='{C_PURPLE}'>▸ {s.get('name')}{zcs}{lds}{stgs}{seq}</font>")
    # 增量Δ（标题换行·内容缩进，布局统一；focus 无题材可讲，跳过）
    new_sec = summary.get("new_sectors") or []
    if is_span:
        # 下午 30min 合并段卡：不显示「开盘定调/增量」语义，改为段内累积。
        # 2026-08-21：focus（无板块爆发）时 main_line 是个股归集 → 禁称「题材」，与首屏口径一致
        ml = summary.get("main_line") or "—"
        ml_txt = f"个股归集【{ml}】" if focus and ml != "—" else f"题材【{ml}】"
        lines.append("**📈 段内累积**：")
        lines.append(f"　<font color='{C_TURQ}'>本段 {len(zt)}只涨停/{len(strong)}只20cm，{ml_txt}</font>")
    elif not focus and prev_summary is None:
        lines.append("**📈 开盘定调**：")
        lines.append(f"　<font color='{C_TURQ}'>本窗口 {len(zt)}只涨停/{len(strong)}只20cm启动，"
                     f"题材【{summary.get('main_line')}】</font>")
    elif not focus and new_sec:
        lines.append("**📈 增量**：")
        lines.append(f"　<font color='{C_TURQ}'>新涌现【{('、'.join(new_sec[:3]))}】；"
                     f"累计涨停 {summary.get('zt_total')}只</font>")
    elif not focus:
        lines.append("**📈 增量**：")
        lines.append(f"　<font color='{C_TURQ}'>题材延续【{summary.get('main_line')}】，"
                     f"累计涨停 {summary.get('zt_total')}只</font>")
    # 今日轮动题材计数预警（用户定·2026-08-15）：轮动越多越难赚钱，≥5 电风扇·防守
    seq_total = max((s.get("seq_total") or 0) for s in sector) if sector else 0
    if seq_total >= 5:
        lines.append(f"<font color='red'>⚠️ 今日已轮动 {seq_total} 个题材，电风扇行情无聚焦主线，"
                     f"追高极难赚钱，防守优先</font>")
    elif seq_total >= 3:
        lines.append(f"<font color='{C_TURQ}'>🔆 今日已轮动 {seq_total} 个题材，轮动加快，"
                     f"只做前排龙头，不追后排</font>")
    lines.append("")
    lines.append("**🧭 小结**：")
    lines.append(f"　{_insight(summary, prev_summary, boost=_bst)}")
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]
    note = (f"📡 选股宝+东财+猎手 | {label} | AI盘中诊断\n"
            f"⚠️ 本报告基于公开信息分析，投资需谨慎")
    # 标题（2026-08-21 用户「有题材就把题材写标题上」）：
    #   板块爆发 → ·题材爆发【首个题材】；个股归集(无板块) → ·个股聚焦【归集题材】；全无 → ·个股聚焦
    #   2026-08-25 加：个股归集命中当日已板块级爆发板块 → ·板块加强【归集题材】；
    #   同日二迭代：持续加强（非首次）→ ·板块持续，防 5min 刷屏
    ml = summary.get("main_line") or ""
    title_tail = ""
    if sector and ml and ml != "—":
        title_tail = f"·题材爆发【{ml.split('、')[0][:6]}】"
    elif focus:
        if _bst and ml and ml != "—":
            tag = "板块加强" if _bst["first"] else "板块持续"
            title_tail = f"·{tag}【{_bst['name'][:6]}】"
        else:
            title_tail = f"·个股聚焦【{ml[:6]}】" if ml and ml != "—" else "·个股聚焦"
    title = f"📊 盘中脉冲·{label}窗口{title_tail}"
    return {
        "title": title,
        "color": C_RED,
        "elements": elements,
        "note": note,
    }


# ── 个股聚焦累积（2026-08-19 用户「无题材爆发：个股累积10个左右再推」）──
def _focus_load() -> dict:
    try:
        return json.loads(FOCUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"stocks": [], "windows": []}


def _focus_save(buf: dict) -> None:
    FOCUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    FOCUS_FILE.write_text(json.dumps(buf, ensure_ascii=False, indent=2), encoding="utf-8")


def _focus_append(summary: dict, wk: str) -> int:
    """把无题材爆发窗口的个股事件(charge/zt/strong, 按code去重)累积进缓冲，返回新增数。"""
    buf = _focus_load()
    stocks = buf.setdefault("stocks", [])
    seen = {e.get("code") for e in stocks}
    added = 0
    for e in (summary.get("charge") or []) + summary["zt"] + summary["strong"]:
        c = e.get("code")
        if c and c not in seen:
            stocks.append(e)
            seen.add(c)
            added += 1
    wins = buf.setdefault("windows", [])
    if wk not in wins:
        wins.append(wk)
    _focus_save(buf)
    return added


def _focus_clear() -> None:
    try:
        FOCUS_FILE.unlink()
    except FileNotFoundError:
        pass


def _focus_summary() -> dict:
    """由累积缓冲构造 summary 供聚焦卡渲染（main_line 取个股 sector 最高频）。"""
    buf = _focus_load()
    stocks = buf.get("stocks", [])
    zt = [e for e in stocks if e.get("type") == "zt"]
    strong = [e for e in stocks if e.get("type") == "strong"]
    charge = [e for e in stocks if e.get("type") == "charge"]
    cnt = Counter(e.get("sector") for e in stocks if e.get("sector"))
    ml = cnt.most_common(1)[0][0] if cnt else "—"
    return {"zt": zt, "strong": strong, "surge": [], "nf": [], "sector": [],
            "charge": charge, "sectors": [], "new_sectors": [], "zt_total": len(zt),
            "main_line": ml}


def close_window(wk: str, push: bool = False, webhook: str = None, dry: bool = False,
                 prev_only: bool = False, label_wk: str = None, clear_after_push: bool = False,
                 force: bool = False) -> dict:
    """闭合单个 5min 窗口桶并出卡。
    force=True（时间保底节拍）：数量不够也出卡——无题材爆发时用当前个股累积直接出聚焦卡，
    有题材爆发时事件 ≥1 就推（不卡 MIN_EVENTS）；纯空窗口(0事件且无累积)仍跳过。
    数量驱动（volume_trigger --close now）不带 force，维持原阈值。"""
    buf = load_buffer()
    wins = buf.get("windows", {})
    keys = sorted(wins.keys())
    # 生产防重：--close prev 时该窗口已闭合过则跳过（launchd 每30s可能同边界触发多次）
    mark = WDIR / f".closed_{wk}"
    if prev_only and mark.exists():
        print(f"[aggregator] 窗口 {wk} 已闭合过，跳过（防重）")
        return None
    prev_summary = None
    if wk in keys:
        idx = keys.index(wk)
        if idx > 0:
            prev_summary = summarize_window(keys[idx - 1], buf)
    summary = summarize_window(wk, buf, prev_summary)
    # 2026-08-19 用户「无题材爆发窗口：个股累积10个左右再推」：
    # 有题材爆发 → 立即推完整窗口卡并重置累积；无题材爆发 → 跨窗口累积个股(去重)，攒够10只/4窗才推聚焦卡
    # 2026-08-21 用户「早盘5min/下午30min保底·数量不够也推」：force 节拍时不再攒，直接用当前累积出卡
    focus = not summary["sector"]
    if focus:
        # 14:04 用户「为什么不堆积」：零星事件(每窗1个)也须进累积队列，不能被 MIN_EVENTS 拦在门外
        added = _focus_append(summary, wk)
        fb = _focus_load()
        n = len(fb.get("stocks", []))
        nwin = len(fb.get("windows", []))
        if not force and n < FOCUS_MIN_STOCKS and nwin < FOCUS_MAX_WINDOWS:
            print(f"[aggregator] 窗口 {wk} 无题材爆发，个股累积 {n}/{FOCUS_MIN_STOCKS}（{nwin}窗，本窗+{added}），暂不推")
            return None
        if n == 0:
            print(f"[aggregator] 窗口 {wk} 无题材爆发且无累积个股，保底空窗跳过")
            return None
        fsum = _focus_summary()
        card = build_window_card(fsum, wk, {}, label_wk, focus=True)
        print(f"[aggregator] 窗口 {wk} 个股聚焦卡(累积 {n}只/{nwin}窗){'·保底节拍' if force else ''} → 推送")
        _focus_clear()
    else:
        total_ev = (len(summary["zt"]) + len(summary["strong"]) + len(summary["surge"]) +
                    len(summary["nf"]) + len(summary["sector"]) + len(summary["charge"]))
        min_ev = 1 if force else MIN_EVENTS
        if total_ev < min_ev:
            print(f"[aggregator] 窗口 {wk} 事件数 {total_ev} < {min_ev}，跳过（弱信号窗口不推）")
            return None
        _focus_clear()
        card = build_window_card(summary, wk, prev_summary, label_wk)
    WDIR.mkdir(parents=True, exist_ok=True)
    cf = WDIR / f"window_{wk}.json"
    cf.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[aggregator] 窗口 {wk} 闭合: zt={len(summary['zt'])} strong={len(summary['strong'])} "
          f"nf={len(summary['nf'])} sector={len(summary['sector'])} → {cf.name}")
    if dry:
        print(f"[aggregator][dry-run] 卡片已写 {cf}（未推送）")
        return card
    if push:
        tf = tempfile.gettempdir() + f"/limao_window_{wk}.json"
        os.system(f'cp "{cf}" "{tf}"')
        rc = os.system(f'{PY} "{PUSH}" "{tf}"' + (f' --webhook {webhook}' if webhook else ''))
        print(f"[aggregator] 推送{'成功' if rc == 0 else '失败'} wk={wk}")
        if rc == 0 and prev_only:
            mark.write_text("", encoding="utf-8")
            print(f"[aggregator] 已写防重标记 {mark.name}")
        # 量触发（--close now --clear）：推完清空该窗口，避免与节拍 prev 闭合重复；
        # 不清标记 → 后续新信号继续入该桶，5min 节拍闭合 prev 时推「增量」，早盘实现多张快卡
        if rc == 0 and clear_after_push:
            wins.pop(wk, None)
            save_buffer(buf)
            print(f"[aggregator] 量触发已清空窗口 {wk}（节拍将推增量）")
    return card


# ── 下午 30min 保底：合并段闭合（2026-08-21 用户「下午尽量半小时一次·数量不够也推」）──
def close_span(wk: str, span_min: int, push: bool = False, webhook: str = None,
               dry: bool = False, label_wk: str = None) -> dict:
    """合并闭合 wk 所在 span 段内所有未闭合 5min 桶 → 一张卡（时间保底·force 语义）。
    wk = 边界前1分钟所在 5min 桶；段 = 该桶对齐到 span_min 的起始桶 → wk。
    段内全部未闭合桶的事件合并、按 code/name 去重后出卡；纯空段(0事件)跳过。"""
    buf = load_buffer()
    wins = buf.get("windows", {})
    try:
        base = datetime.strptime(wk, "%Y%m%d_%H%M").replace(tzinfo=BJ)
    except Exception:
        # wk 非法时退化为单桶保底闭合
        return close_window(wk, push=push, webhook=webhook, dry=dry,
                            prev_only=True, label_wk=label_wk, force=True)
    seg_min = (base.minute // span_min) * span_min
    seg_start = base.replace(minute=seg_min, second=0, microsecond=0)
    span_keys = []
    cur = seg_start
    while cur <= base:
        span_keys.append(cur.strftime("%Y%m%d_%H%M"))
        cur += timedelta(minutes=WINDOW_MIN)
    mark = WDIR / f".closed_{wk}_s{span_min}"
    if mark.exists():
        print(f"[aggregator] 段 {seg_start:%H:%M}–{base:%H:%M} 已闭合过，跳过（防重）")
        return None
    unclosed = [k for k in span_keys if k in wins and not (WDIR / f".closed_{k}").exists()]
    if not unclosed:
        print(f"[aggregator] 段 {seg_start:%H:%M}–{base:%H:%M} 无未闭合桶，跳过")
        return None
    merged = {"zt": [], "strong": [], "surge": [], "nf": [], "sector": [], "charge": []}
    for k in unclosed:
        s = summarize_window(k, buf, None)
        for t in merged:
            merged[t].extend(s[t])

    def _dedup(items: list, key: str) -> list:
        seen, out = set(), []
        for it in items:
            k = it.get(key)
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(it)
        return out

    for t in ("zt", "charge", "strong", "surge"):
        merged[t] = _dedup(merged[t], "code")
    merged["sector"] = _dedup(merged["sector"], "name")
    # 20cm涨停已在 zt，从 strong/charge 去重
    zt_codes = {z.get("code") for z in merged["zt"]}
    strong2 = [e for e in merged["strong"] if e.get("code") not in zt_codes]
    charge2 = [e for e in merged["charge"] if e.get("code") not in zt_codes]
    sec_names = [s.get("name") for s in merged["sector"] if s.get("name")]
    if sec_names:
        main_line = "、".join(sec_names[:2])
    else:
        cnt = Counter(e.get("sector") for e in merged["zt"] + strong2 if e.get("sector"))
        main_line = cnt.most_common(1)[0][0] if cnt else "—"
    summary = {"zt": merged["zt"], "strong": strong2, "surge": merged["surge"],
               "nf": merged["nf"], "sector": merged["sector"], "charge": charge2,
               "sectors": sec_names, "new_sectors": [], "zt_total": len(merged["zt"]),
               "main_line": main_line}
    total_ev = sum(len(v) for v in merged.values())
    if total_ev == 0:
        print(f"[aggregator] 段 {seg_start:%H:%M}–{base:%H:%M} 0事件，保底空段跳过")
        return None
    focus = not merged["sector"]
    card = build_window_card(summary, wk, None, label_wk, focus=focus, is_span=True)
    WDIR.mkdir(parents=True, exist_ok=True)
    cf = WDIR / f"window_{wk}.json"
    cf.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[aggregator] 段 {seg_start:%H:%M}–{base:%H:%M} 闭合({len(unclosed)}桶): "
          f"zt={len(summary['zt'])} strong={len(summary['strong'])} nf={len(summary['nf'])} "
          f"sector={len(summary['sector'])} → {cf.name}")
    if dry:
        print(f"[aggregator][dry-run] 卡片已写 {cf}（未推送）")
        return card
    if push:
        tf = tempfile.gettempdir() + f"/limao_window_{wk}.json"
        os.system(f'cp "{cf}" "{tf}"')
        rc = os.system(f'{PY} "{PUSH}" "{tf}"' + (f' --webhook {webhook}' if webhook else ''))
        print(f"[aggregator] 推送{'成功' if rc == 0 else '失败'} wk={wk}")
        if rc == 0:
            mark.write_text("", encoding="utf-8")
            for k in unclosed:
                (WDIR / f".closed_{k}").write_text("", encoding="utf-8")
            print(f"[aggregator] 已写段防重标记 {mark.name}")
    return card


# ── 模拟还原 ────────────────────────────────────────
def assign_simulated_windows(events: list) -> "OrderedDict":
    """把真实事件按语义时段分到 4 个窗口(开盘/早盘/午盘/尾盘)。"""
    zt = [e for e in events if e["type"] == "zt"]
    strong = [e for e in events if e["type"] == "strong"]
    nf = [e for e in events if e["type"] == "nf"]
    sector = [e for e in events if e["type"] == "sector"]
    W = ["20260814_0930", "20260814_1000", "20260814_1300", "20260814_1400"]
    nf_map = {0: W[0], 1: W[0], 2: W[1], 3: W[1], 4: W[2], 5: W[2], 6: W[2], 7: W[3]}
    sec_map = {0: W[1], 1: W[1], 2: W[2], 3: W[2], 4: W[3], 5: W[3]}
    out = OrderedDict((k, []) for k in W)
    for i, e in enumerate(nf):
        out[nf_map.get(i, W[-1])].append(e)
    for i, e in enumerate(sector):
        out[sec_map.get(i, W[-1])].append(e)
    for i, e in enumerate(zt):
        seg = 0 if i < 8 else (1 if i < 16 else (2 if i < 23 else 3))
        out[W[seg]].append(e)
    for i, e in enumerate(strong):
        seg = 0 if i < 5 else (1 if i < 10 else (2 if i < 15 else 3))
        out[W[seg]].append(e)
    return out


def simulate(date: str, push: bool, webhook: str, dry: bool, max_windows: int = 0):
    d = date.replace("-", "")
    ps = CACHE / f"pushed_signals_{d}.json"
    yz = CACHE / "yesterday_zt.json"
    latest = CACHE / "pulse_snapshots" / "latest.json"
    if not ps.exists():
        print(f"[simulate] 找不到 {ps}", file=sys.stderr)
        sys.exit(2)
    sig = json.loads(ps.read_text(encoding="utf-8"))
    yz_data = json.loads(yz.read_text(encoding="utf-8")) if yz.exists() else {}
    lt = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else {}

    zt_by_code = {z.get("code"): z for z in (yz_data.get("zt_list") or [])}
    ld_by_code = {l.get("code"): l for l in (lt.get("leaders") or [])}
    top5_by_name = {s.get("name"): s for s in (lt.get("top5") or [])}

    events = []
    for code in (sig.get("zt_codes") or []):
        z = zt_by_code.get(code) or ld_by_code.get(code) or {}
        events.append({"type": "zt", "code": code, "name": (z.get("name") or code).replace(" ", ""),
                       "sector": z.get("sector") or "", "pct": z.get("pct"),
                       "boards": z.get("boards") if z.get("boards") else "",
                       "is_20cm": bool(z.get("is_20cm"))})
    for code in (sig.get("strong_codes") or []):
        l = ld_by_code.get(code) or {}
        events.append({"type": "strong", "code": code, "name": (l.get("name") or code).replace(" ", ""),
                       "sector": l.get("sector") or "", "pct": l.get("pct"),
                       "is_20cm": bool(l.get("is_20cm"))})
    for t in (sig.get("nf_titles") or []):
        events.append({"type": "nf", "title": t})
    for nm in (sig.get("sector_pushed") or []):
        s = top5_by_name.get(nm) or {}
        ld = s.get("leader") or {}
        events.append({"type": "sector", "name": nm, "desc": s.get("desc") or "",
                       "zt_count": s.get("zt_count"),
                       "leader_name": (ld.get("name") or "").replace(" ", "")})

    windows = assign_simulated_windows(events)
    if BUFFER.exists():
        BUFFER.unlink()  # 模拟专用, 清空旧缓冲
    if SEQ.exists():
        SEQ.unlink()  # 模拟专用, 清空当日题材序列
    wkeys = sorted(windows.keys())
    if max_windows:
        wkeys = wkeys[:max_windows]
    for wk in wkeys:
        append_events(windows[wk], ts=datetime.strptime(wk, "%Y%m%d_%H%M").replace(tzinfo=BJ))
        close_window(wk, push=push, webhook=webhook, dry=dry)
    print(f"[simulate] 完成: {len(wkeys)} 个窗口, 共 {len(events)} 条真实事件(来自 {ps.name})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", metavar="DATE", help="用真实缓存模拟当天(YYYY-MM-DD)")
    ap.add_argument("--close", metavar="WHEN", help="闭合窗口: now / 窗口key")
    ap.add_argument("--clear", action="store_true", help="闭合推送成功后清空该窗口（量触发专用，防节拍重复）")
    ap.add_argument("--force", action="store_true",
                    help="时间保底节拍：信号不足也强制出卡（数量不够不攒，直接推当前累积）")
    ap.add_argument("--span", type=int, default=0,
                    help="合并闭合段粒度(分钟)：下午 30 → 段内全部未闭合桶合并一张卡；0=单桶")
    ap.add_argument("--push", action="store_true", help="闭合时推送到群")
    ap.add_argument("--webhook", default=None, help="推送 webhook 键名(默认测试群)")
    ap.add_argument("--dry-run", action="store_true", help="不推送, 仅生成卡片文件")
    ap.add_argument("--max-windows", type=int, default=0, help="模拟时最多处理前N个窗口")
    args = ap.parse_args()

    if args.simulate:
        wh = args.webhook or "feishu_webhook_test"
        simulate(args.simulate, push=args.push, webhook=wh, dry=args.dry_run,
                 max_windows=args.max_windows)
        return
    if args.close:
        label_wk = None
        if args.close == "now":
            wk = window_key(now_bj())
        elif args.close == "prev":
            # 生产边界用：闭合"刚结束的上一窗口"（边界时刻前1分钟所在桶）
            wk = window_key(now_bj() - timedelta(minutes=1))
            # 2026-08-19 修：标题用「推送边界时刻」，数据=刚结束窗口，标题=用户看盘的此刻
            label_wk = window_key(now_bj())
        else:
            wk = args.close
            label_wk = None
        # 生产 close 不显式指定 webhook → push_card.py 按交易日自动选群（工作日=prod 正式群）。
        # 2026-08-18 修：原先默认 feishu_webhook_test 导致窗口卡全进测试群；测试请显式 --webhook feishu_webhook_test。
        wh = args.webhook
        if args.close == "prev" and args.span and args.span > WINDOW_MIN:
            # 下午 30min 保底：合并段闭合（时间保底·数量不够也推）
            close_span(wk, args.span, push=args.push, webhook=wh, dry=args.dry_run,
                       label_wk=label_wk)
        else:
            close_window(wk, push=args.push, webhook=wh, dry=args.dry_run,
                         prev_only=(args.close == "prev"), label_wk=label_wk,
                         clear_after_push=args.clear, force=args.force)
        return
    print("用法: --simulate DATE | --close now [--push] [--webhook KEY] [--dry-run]")


if __name__ == "__main__":
    main()
