#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短线精灵·盘中异动信号引擎（2026-08-18 用户定）。

从盘中异动中筛选高质量、低噪音个股并推送：
  1. 信号池：东财涨幅榜（≥min_pct，两页 200 条，带 市值/成交额/上市日/主力净额/行业）
  2. 噪音过滤：ST/退市、次新(≤max_new_stock_days 天)、市值<min_market_cap_yi、成交额不达标（上午/下午分档）、非板块龙头
  3. 共振度：code 在 top5 板块 leader/followers=强；行业/名称命中热点关键词=中；名称含热点词=弱
  4. 信号强度 = 涨幅*0.4 + 成交额*0.2 + 共振*0.3 + 主力净流入*0.1（权重可配置）
  5. 推送：飞书卡按强度降序（当天每 code 去重 + push_cooldown_min 冷却）

用法：
  python3 scripts/short_spirit.py --push       # 检测+推送（守护链调用）
  python3 scripts/short_spirit.py --dry-run    # 只出 JSON 不推
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path  # 2026-09-04 --send 统一出口用

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "config" / "short_spirit.json"
OUT_PATH = ROOT / "cache" / "short_spirit_signals.json"
PUSHED_PATH = ROOT / "cache" / "short_spirit_pushed.json"
SNAP = ROOT / "cache" / "pulse_snapshots" / "latest.json"
ROUND_PATH = ROOT / "cache" / "short_spirit_round.json"    # 涨停观测轮快照（当下性硬门·2026-09-07）
ENABLE_SPIRIT_TIMELINESS = True  # ⛔ 涨停当下性硬门（用户「时间顺序第一」·2026-09-07 盘中激活）：
#   每轮记录「被观测为涨停」的票；上轮已观测=存量涨停(非当下新封)→剔除不推，
#   仅本轮新观测涨停才推。拦截「激活晚/东财恢复把上午涨停下午补推」的旧闻。

EM_URL = ("https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2"
          "&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
          "&fields=f2,f3,f6,f12,f14,f18,f20,f21,f26,f62,f100")

# ── 短线精灵·市值门槛豁免（2026-09-07 用户 A+B 方案 · 默认关）──────────────
# 背景：用户情绪核心战法（08-14）连板龙头多中小盘，原 min_market_cap_yi=100 把
#   青山纸业(87.6亿·10天5板)等龙头漏推。A=连板龙头/板块核心票豁免市值门槛；
#   B=非豁免票门槛降到 30 亿。⛔ 两开关默认 False=与改动前行为完全一致；
#   盘中禁改生效，收盘后翻 True + 次日盘中验证（09-03 双推教训）。
ENABLE_SPIRIT_CAPEXEMPT = True    # A：连板(boards>=2)或板块核心票 豁免市值门槛（2026-09-07 用户「直接开始执行」盘中激活）
SPIRIT_LOWCAP_YI = 30             # B：开启 LOWCAP 后非豁免票市值门槛（亿）
ENABLE_SPIRIT_LOWCAP = True       # B：非豁免票市值门槛降至 30 亿（2026-09-07 用户「直接开始执行」盘中激活）
ENABLE_SPIRIT_LEADER_FALLBACK = True  # C：东财抓取失败(下午常空池)→用 latest.json 连板龙头(boards>=2)兜底（同上盘中激活）


def http_get(url, t=8):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=t) as resp:
        return json.loads(resp.read().decode())


def load_cfg():
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"min_pct": 3.0, "min_market_cap_yi": 100, "amount_morning_yi": 5,
                "amount_afternoon_yi": 10, "max_new_stock_days": 365, "top_per_sector": 2,
                "max_signals": 10, "batch_threshold": 10, "batch_timeout_min": 30,
                "weight": {"pct": 0.4, "amount": 0.2, "resonance": 0.3, "flow": 0.1}}


def in_trading() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    # 09:25 起（2026-08-24：早盘前半小时高频实时窗口从竞价结束 09:25 开始，秒板/冲板
    # 信号不因 09:25-09:30 区间被挡；原 09:30 起点会漏掉开盘前 5 分钟的异动）
    return (9 * 60 + 25) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60)


def session() -> str:
    """上午 / 下午（成交额阈值分档）"""
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    return "morning" if hm <= (11 * 60 + 30) else "afternoon"


def fetch_pool(cfg):
    """东财涨幅榜两页 → 原始信号池"""
    raw = []
    for pn in (1, 2):
        try:
            d = http_get(EM_URL.format(pn=pn))
            raw.extend(((d.get("data") or {}).get("diff") or []))
        except Exception:
            break
    pool = []
    for it in raw:
        try:
            pool.append({
                "code": str(it.get("f12") or ""),
                "name": str(it.get("f14") or ""),
                "pct": float(it.get("f3") or 0),
                "price": float(it.get("f2") or 0),
                "amount_yi": round(float(it.get("f6") or 0) / 1e8, 2),
                "market_cap_yi": round(float(it.get("f20") or 0) / 1e8, 1),
                "float_cap_yi": round(float(it.get("f21") or 0) / 1e8, 1),
                "list_date": str(it.get("f26") or "") or None,
                "prev_close": float(it.get("f18") or 0),  # 2026-09-04 涨停封板校验（炸板票不误标涨停）
                "main_flow_yi": round(float(it.get("f62") or 0) / 1e8, 2),
                "industry": str(it.get("f100") or ""),
            })
        except Exception:
            continue
    return pool


def hot_keywords(d):
    """热点关键词集：top5 板块名 + 资金流入板块 + leaders 板块"""
    kws = set()
    for s in (d.get("top5") or [])[:5]:
        nm = str(s.get("name") or "")
        if nm and nm != "其他":
            kws.add(nm)
            kws.add(nm.replace("概念", "").replace("板块", ""))
    for x in ((d.get("sector_risk") or {}).get("inflow_top") or [])[:5]:
        nm = str(x.get("name") or "")
        if nm:
            kws.add(nm)
    for l in (d.get("leaders") or [])[:20]:
        nm = str(l.get("sector") or "")
        if nm and nm != "其他":
            kws.add(nm)
    return {k for k in kws if len(k) >= 2}


def top5_codes(d):
    """top5 板块 leader/followers 的 code 集 = 强共振票"""
    codes = set()
    for s in (d.get("top5") or [])[:5]:
        L = s.get("leader") or {}
        if L.get("code"):
            codes.add(str(L["code"])[:6])
        for f in (s.get("followers") or []):
            if f.get("code"):
                codes.add(str(f["code"])[:6])
    return codes


def _boards_map(d):
    """code → 连板数（来自 latest leaders，短线精灵豁免连板龙头市值门槛用）。"""
    import re
    m = {}
    for l in (d.get("leaders") or []):
        c = str(l.get("code") or "")
        b = l.get("boards")
        if c and b is not None:
            mm = re.search(r"(\d+)", str(b))
            if mm:
                m[c[:6]] = int(mm.group(1))
    return m


def _leaders_as_pool(d):
    """东财涨幅榜抓取失败时的兜底个股池：用本地面板快照 latest.json 的连板龙头
    （boards>=2）直接构造候选，不依赖东财网络。这些票经 A 豁免（_boards_map 命中）
    绕过市值门槛，确保连板龙头在盘中任何时段都能即时推送（2026-09-07 用户铁律）。
    prev_close 由 price/(1+pct) 反推，使 signal_type 正确标「涨停」而非「炸板·回落」。"""
    import re
    out = []
    for l in (d.get("leaders") or []):
        b = l.get("boards")
        nb = 0
        if b:
            mm = re.search(r"(\d+)", str(b))
            if mm:
                nb = int(mm.group(1))
        if nb < 2:
            continue
        code = str(l.get("code") or "")
        if not code:
            continue
        code = code[:6]
        pct = float(l.get("pct") or 0)
        price = float(l.get("price") or 0)
        prev_close = price / (1 + pct / 100) if pct > 0 else 0.0
        out.append({
            "code": code,
            "name": str(l.get("name") or ""),
            "pct": pct,
            "price": price,
            "amount_yi": 999.0,
            "market_cap_yi": 9999.0,
            "float_cap_yi": 9999.0,
            "list_date": None,
            "prev_close": prev_close,
            "main_flow_yi": 0.0,
            "industry": str(l.get("sector") or ""),
        })
    return out


def _zt_observe_codes(pool, signals):
    """本轮「被观测为涨停」的 code 集 = 信号标涨停 ∪ 池内涨幅达涨停线（20cm 按 19.7）。"""
    zt = set()
    for s in signals:
        if s.get("type") == "涨停":
            zt.add(str(s.get("code") or ""))
    for p in pool:
        code = str(p.get("code") or "")
        pct = float(p.get("pct") or 0)
        if not code:
            continue
        if code[:3] in ("300", "301", "688"):
            if pct >= 19.7:
                zt.add(code)
        elif pct >= 9.8:
            zt.add(code)
    return zt


def _timeliness_gate(signals, pool, now):
    """⛔ 涨停当下性硬门（2026-09-07 用户「时间顺序第一」·盘中激活）。

    涨停信号必须是「本轮新观测到」——上轮(30-60s前)已被观测为涨停 = 存量涨停
    （可能上午就封住一直封到现在），不是当下新封板 → 一律剔除不推。
    这拦截三类旧闻补推：①A/B 开关盘中激活晚，存量连板龙头被当新信号；
    ②东财涨幅榜下午 RemoteDisconnected 恢复后，把上午涨停的票补推；
    ③降级源(latest.json 连板龙头)把整天封板的存量当「涨停事件」反复产出。
    非涨停信号(接近涨停/快速拉升)天然是当下行为，不受限。
    每轮覆盖更新观测快照（炸板那轮不记录 → 回封算新一轮，可再推）。"""
    date = now.strftime("%Y-%m-%d")
    prev_zt = set()
    try:
        r = json.loads(ROUND_PATH.read_text(encoding="utf-8"))
        if r.get("date") == date:
            prev_zt = set(r.get("zt") or [])
    except Exception:
        pass
    keep = [s for s in signals
            if not (s.get("type") == "涨停" and str(s.get("code") or "") in prev_zt)]
    ROUND_PATH.write_text(json.dumps({
        "date": date,
        "ts": now.strftime("%H:%M:%S"),
        "zt": sorted(_zt_observe_codes(pool, signals)),
    }, ensure_ascii=False), encoding="utf-8")
    return keep


def noise_filter(pool, cfg, now, exempt_codes=None, boards_map=None):
    """噪音过滤链：ST/次新/市值/成交额/涨幅下限"""
    keep = []
    min_cap = cfg["min_market_cap_yi"]
    amt_thr = cfg["amount_morning_yi"] if session() == "morning" else cfg["amount_afternoon_yi"]
    cutoff = now - timedelta(days=int(cfg["max_new_stock_days"]))
    _exempt_set = exempt_codes or set()
    _bmap = boards_map or {}
    for s in pool:
        if "ST" in s["name"] or "退" in s["name"] or "*" in s["name"]:
            continue
        if not s["code"] or not s["name"]:
            continue
        if s["pct"] < cfg["min_pct"]:
            continue
        # ── 市值门槛（2026-09-07 A/B 方案·开关默认关）──
        mc = s["market_cap_yi"]
        _exempt = ENABLE_SPIRIT_CAPEXEMPT and (
            s["code"] in _exempt_set or int(_bmap.get(s["code"], 0) or 0) >= 2)
        if not _exempt:
            _cap_thr = SPIRIT_LOWCAP_YI if ENABLE_SPIRIT_LOWCAP else min_cap
            if mc < _cap_thr:
                continue
        if s["amount_yi"] < amt_thr:
            continue
        # 次新：上市日期距今 < max_new_stock_days
        if s["list_date"] and len(s["list_date"]) == 8:
            try:
                ld = datetime.strptime(s["list_date"], "%Y%m%d")
                if ld > cutoff:
                    continue
            except ValueError:
                pass
        keep.append(s)
    return keep


def resonance(s, kws, t5codes, t5names):
    """共振度 0~1：强=top5 板块核心票；中=行业/名称命中热点；弱=名称含热点词"""
    score = 0.0
    hit = []
    if s["code"] in t5codes:
        score += 2.0
        hit.append("板块核心")
    for kw in kws:
        if kw and (kw in s["industry"] or s["industry"] in kw):
            score += 1.0
            hit.append(kw)
            break
    for kw in t5names:
        if kw and kw in s["name"]:
            score += 0.5
            hit.append("题材名")
            break
    return min(score / 2.5, 1.0), hit[:2]


def signal_type(s):
    """异动类型：涨停 / 冲板 / 快速拉升 + 大单流入/放量 标签"""
    code3 = s["code"][:3]
    is20 = code3 in ("300", "301") or code3 == "688"
    thr_zt, thr_chg = (19.9, 15.0) if is20 else (9.9, 7.5)
    # 2026-09-04 封板校验（用户「欢瑞世纪炸板被标涨停」）：涨停=现价≈涨停价（真封住）。
    # 涨幅达涨停线但现价未到位（曾触板炸开/回落中）=「炸板·回落」，不标涨停——
    # 实证欢瑞世纪 000892 反复炸板：封住时 +10.04%（价=5.59），炸开后 +9.25%（价 5.55<5.59）。
    pc = float(s.get("prev_close") or 0)
    limit = round(pc * (1.2 if is20 else 1.1), 2) if pc > 0 else 0.0
    if limit > 0 and s["price"] >= limit - 0.001:
        t = "涨停"
    elif s["pct"] >= thr_zt:
        t = "炸板·回落"
    elif s["pct"] >= thr_chg:
        t = "接近涨停"
    else:
        t = "快速拉升"
    tags = []
    if s["main_flow_yi"] > 0:
        tags.append("大单流入")
    if s["amount_yi"] >= 10:
        tags.append("放量")
    return t, tags


def strength(s, res, flow_ratio, cfg):
    """信号强度 = 涨幅归一 + 成交额归一 + 共振 + 主力净流入方向"""
    w = cfg["weight"]
    pct_n = min(s["pct"] / 20.0, 1.0)
    amt_n = min(s["amount_yi"] / 30.0, 1.0)
    flow_n = 1.0 if s["main_flow_yi"] > 0 else 0.0
    return round(pct_n * w["pct"] + amt_n * w["amount"] + res * w["resonance"] + flow_n * w["flow"], 3)


def build_signals(d, now, cfg):
    """主流程：拉池 → 过滤 → 龙头保留 → 共振 → 排序"""
    pool = fetch_pool(cfg)
    # ⛔ C 降级：东财涨幅榜抓取失败（沙箱下午常 RemoteDisconnected→池0）时，
    # 用本地面板快照 latest.json 的连板龙头(boards>=2)兜底，确保连板龙头盘中任何时段可推
    if ENABLE_SPIRIT_LEADER_FALLBACK and len(pool) < 5:
        fb_pool = _leaders_as_pool(d)
        if fb_pool:
            pool = pool + fb_pool
    kws = hot_keywords(d)
    t5codes = top5_codes(d)
    t5names = [str(s.get("name") or "") for s in (d.get("top5") or [])[:5]]
    boards_map = _boards_map(d)
    keep = noise_filter(pool, cfg, now, exempt_codes=t5codes, boards_map=boards_map)
    # 板块龙头保留：同行业按涨幅取 top_per_sector
    sec_groups = {}
    for s in keep:
        sec_groups.setdefault(s["industry"] or "其他", []).append(s)
    core = []
    for sec, arr in sec_groups.items():
        arr.sort(key=lambda x: -x["pct"])
        core.extend(arr[: cfg["top_per_sector"]])
    # 共振 + 强度 + 题材/逻辑（2026-08-18 用户定：只显示题材+逻辑，去掉标签词）
    signals = []
    # code → top5 题材名映射（强共振的题材化表达，替代"主线核心"标签）
    code_t5 = {}
    for s5 in (d.get("top5") or [])[:5]:
        nm = str(s5.get("name") or "")
        L = s5.get("leader") or {}
        if L.get("code"):
            code_t5[str(L["code"])[:6]] = nm
        for f in (s5.get("followers") or []):
            if f.get("code"):
                code_t5[str(f["code"])[:6]] = nm
    for s in core:
        res, hit = resonance(s, kws, t5codes, t5names)
        typ, tags = signal_type(s)
        sc = strength(s, res, 0, cfg)
        # 逻辑描述：共振[题材]（top5板块 > 行业热点词 > 题材共振），无则类型兜底
        t5 = code_t5.get(s["code"], "")
        kw_hit = [h for h in hit if h not in ("板块核心", "题材名")]
        if not t5 and kw_hit:
            t5 = kw_hit[0]
        if not t5 and "题材名" in hit:
            t5 = "题材共振"
        reason = f"共振[{t5[:8]}]" if t5 else typ
        signals.append({
            "name": s["name"], "code": s["code"], "sector": s["industry"],
            "market_cap_yi": s["market_cap_yi"], "amount_yi": s["amount_yi"],
            "pct": s["pct"], "type": typ, "tags": tags,
            "resonance": round(res * 100), "flow_yi": s["main_flow_yi"],
            "strength": sc, "reason": reason,
        })
    signals.sort(key=lambda x: -x["strength"])
    # ⛔ 涨停当下性硬门（2026-09-07 用户「时间顺序第一」）：剔除上轮已观测为涨停的存量票
    if ENABLE_SPIRIT_TIMELINESS:
        signals = _timeliness_gate(signals, pool, now)
    return signals[: cfg["max_signals"]], session(), len(keep), len(pool)


def load_pushed():
    try:
        return json.loads(PUSHED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"date": "", "codes": [], "last_push": "", "pending": [], "pending_ts": ""}


def save_pushed(pushed, now, update_last=True):
    """整体保存去重/池状态（含待推池 pending，2026-08-18 双触发改造）。
    update_last=False：仅保存池状态，不刷新 last_push（超时兜底计时不受干扰）。"""
    pushed["date"] = now.strftime("%Y-%m-%d")
    if update_last:
        pushed["last_push"] = now.strftime("%H:%M:%S")
    PUSHED_PATH.write_text(json.dumps(pushed, ensure_ascii=False), encoding="utf-8")


# 个股名语义色（2026-08-25 用户定·与涨幅数字同色号）：涨停/快速拉升=red（与涨幅红一致）、
# 接近涨停=purple（机会紫）。⛔ 类型区分靠分组标题（🔥/🚀/⚡），个股名不再承担类型色——
# 曾用 orange 标快速拉升，飞书渲染偏橘红与涨幅正红不一致（用户抓包「红色颜色不对」）。
TYPE_COLORS = {"涨停": "red", "接近涨停": "purple", "快速拉升": "red"}


def _pct_tag(pct: float) -> str:
    """涨幅数字红涨绿跌（A股铁律·shared_rules 配色铁律）：+红 / -绿 / 0中性。"""
    if pct > 0:
        return f"<text_tag color='red'>{pct:+.2f}%</text_tag>"
    if pct < 0:
        return f"<text_tag color='green'>{pct:+.2f}%</text_tag>"
    return f"<text_tag color='neutral'>{pct:+.2f}%</text_tag>"


def build_card(signals, sess, now):
    """飞书卡（2026-08-18 用户定·向窗口卡事件流风格看齐）：
    按异动类型分组（🔥涨停/🚀冲板/⚡快速拉升），一行一只，字段压缩到一行。
    ⛔ 2026-08-25 修复（用户抓包「又忘了红涨绿跌」+「红色颜色不对」）：
       - `<font color>` 飞书不渲染（shared_rules 实测 08-14）→ 一律 `<text_tag>` 胶囊；
       - 涨幅数字按涨跌着色（+红/-绿），个股名与涨幅同色号：涨停/快速拉升=red、接近涨停=purple
         （快速拉升曾用 orange，飞书渲染偏橘红与涨幅正红不一致 → 已改 red，类型靠分组标题区分）。
    """
    def md(*lines):
        return {"tag": "lark_md", "content": "\n".join(lines)}

    def block(*lines):
        """正文块：飞书 elements 顶层必须是 div，lark_md 须包在 text 里（否则空白）"""
        return {"tag": "div", "text": md(*lines)}

    c = load_cfg()
    sess_cn = "上午" if sess == "morning" else "下午"
    amt_thr = c["amount_morning_yi"] if sess == "morning" else c["amount_afternoon_yi"]
    groups = {"涨停": [], "接近涨停": [], "快速拉升": []}
    for s in signals:
        groups.setdefault(s["type"], []).append(s)

    lines = []
    if signals:
        top = signals[0]
        tcol = TYPE_COLORS.get(top["type"], "red")
        lines.append(f"**🧭 本批 {len(signals)} 只**｜最强 "
                     f"<text_tag color='{tcol}'>{top['name']}({top['code']})</text_tag> "
                     f"{_pct_tag(top['pct'])}（{top['sector']}）｜⚡{top['reason']}｜{sess_cn}时段")
    for typ, title, col in (("涨停", "🔥 涨停信号", "red"),
                            ("接近涨停", "🚀 接近涨停", "purple"),
                            ("快速拉升", "⚡ 快速拉升", "red")):
        arr = groups.get(typ, [])
        if not arr:
            continue
        lines.append(f"**{title} {len(arr)}只**：")
        for s in arr:
            lines.append(
                f"　<text_tag color='{col}'>{s['name']}({s['code']})</text_tag> "
                f"{_pct_tag(s['pct'])}（{s['sector']}）｜⚡{s['reason']}")
    lines.append("")
    # 2026-09-07 修复：卡面市值门槛必须显示「实际生效阈值」（B 开=30亿），不得写死 cfg 100亿
    _cap_show = SPIRIT_LOWCAP_YI if ENABLE_SPIRIT_LOWCAP else c["min_market_cap_yi"]
    _cap_note = "·连板/板块核心票豁免" if ENABLE_SPIRIT_CAPEXEMPT else ""
    lines.append(f"**🧭 小结**：异动聚焦短线热点，按强度降序；"
                 f"市值≥{_cap_show}亿{_cap_note}·当日总成交额≥{amt_thr}亿（{sess_cn}档）·剔除ST/次新")
    return {
        "title": f"短线精灵·异动信号 | {now.strftime('%H:%M')} | 命中{len(signals)}只",
        "color": "red",
        "elements": [
            block(*lines),
            {"tag": "hr"},
            {"tag": "note", "elements": [md("📡 东财+选股宝 | 短线精灵引擎 | 市值/成交额/次新/ST 过滤")]},
        ],
    }


def _send_dedup_path() -> Path:
    return ROOT / "cache" / "spirit_send_dedup.json"


def _send_card(card_path: str, webhook: str | None = None, dry: bool = False) -> int:
    """统一出口模式（2026-09-04 深度合并·短线精灵=异动群唯一推送执行引擎）：
    live_signal（苗头/核按钮/N字/回流/资金切换/首板/盘中异动）与节拍卡不再自行
    push_card，全部透传本函数出卡——标题已统一「⚡短线精灵·」系列。
    幂等=内容 hash 短窗 45s 防抖（防守护轮+外部双写同一卡文件重推）；
    各卡自身语义幂等（核按钮档/苗头 map/精灵 codes）仍由各自检测侧负责。
    """
    cf = Path(card_path)
    if not cf.exists():
        print(f"[short_spirit] --send 卡文件不存在: {cf}")
        return 1
    try:
        card = json.loads(cf.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[short_spirit] --send 卡解析失败: {e}")
        return 1
    if not in_trading():
        print("[short_spirit] --send 非交易时段·跳过")
        return 0
    content = json.dumps(card, ensure_ascii=False)
    h = hashlib.sha1(content.encode()).hexdigest()
    dp = _send_dedup_path()
    ded = {}
    if dp.exists():
        try:
            ded = json.loads(dp.read_text(encoding="utf-8"))
        except Exception:
            ded = {}
    now = datetime.now()
    last = ded.get(h, 0)
    if now.timestamp() - float(last) < 45.0:
        print(f"[short_spirit] --send 同内容 45s 内已推·跳过（防抖 {cf.name}）")
        return 0
    ded = {k: v for k, v in ded.items() if now.timestamp() - float(v) < 600}  # 只留 10min 窗口
    ded[h] = now.timestamp()
    dp.write_text(json.dumps(ded), encoding="utf-8")
    if dry:
        print(f"[short_spirit] --send(dry) {cf.name}: {card.get('title', '')[:60]}")
        return 0
    wc = f"--webhook {webhook} " if webhook else "--webhook feishu_webhook_intraday "
    rc = __import__("os").system(f'"{sys.executable}" "{ROOT}/scripts/push_card.py" {wc}"{cf}"')
    print(f"[short_spirit] --send 推送{'成功' if rc == 0 else '失败'} {cf.name} | {card.get('title', '')[:50]}")
    return 0 if rc == 0 else 1


def main():
    global cfg
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="检测并推送")
    ap.add_argument("--force", action="store_true",
                    help="跳过 batch/timeout 双触发，直接推当前待推池（2026-08-24 早盘高频窗口用）")
    ap.add_argument("--dry-run", action="store_true", help="只出 JSON 不推")
    ap.add_argument("--send", metavar="CARD_JSON",
                    help="统一出口模式（2026-09-04）：透传已拼好的短线精灵系列卡到异动群·内容级 45s 防抖")
    ap.add_argument("--webhook", default=None, help="--send 时可选覆盖推送目标（默认异动群；测试用 feishu_webhook_test）")
    args = ap.parse_args()

    cfg = load_cfg()
    if args.send:
        sys.exit(_send_card(args.send, webhook=args.webhook, dry=args.dry_run))
    if not in_trading():
        print("[short_spirit] 非交易时段，跳过")
        return
    now = datetime.now()
    if not SNAP.exists():
        print("[short_spirit] latest.json 缺失")
        return
    d = json.loads(SNAP.read_text(encoding="utf-8"))

    signals, sess, kept, total = build_signals(d, now, cfg)
    result = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "session": sess,
              "thresholds": {k: cfg[k] for k in ("min_pct", "min_market_cap_yi", "amount_morning_yi", "amount_afternoon_yi")},
              "pool_total": total, "after_filter": kept, "signals": signals}
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[short_spirit] 池{total}→过滤{kept}→信号{len(signals)} | 时段={sess}")

    if args.dry_run or not args.push:
        return

    # 双触发推送（2026-08-18 用户定：数量累计 + 超时兜底，替代固定冷却）：
    #   - 新信号（code ∉ 已推 ∪ 池内）入待推池
    #   - 池 ≥ batch_threshold（数量达标） 或 距上推 ≥ batch_timeout_min（超时兜底且池非空）→ 推卡
    pushed = load_pushed()
    date_now = now.strftime("%Y-%m-%d")
    if pushed.get("date") != date_now:
        pushed = {"date": date_now, "codes": [], "last_push": "", "pending": [], "pending_ts": ""}
    pending = pushed.get("pending", []) or []
    pending_codes = {s["code"] for s in pending}
    pushed_codes = set(pushed.get("codes", []) or [])
    new_pool = [s for s in signals if s["code"] not in pushed_codes and s["code"] not in pending_codes]
    if new_pool:
        pending.extend(new_pool)
        pushed["pending"] = pending
        if not pushed.get("pending_ts"):
            pushed["pending_ts"] = now.isoformat()

    batch = cfg["batch_threshold"]
    timeout_min = cfg["batch_timeout_min"]
    last = pushed.get("last_push", "")
    elapsed = 1e9
    if last:
        try:
            elapsed = (now - datetime.strptime(f"{date_now} {last}", "%Y-%m-%d %H:%M:%S")).total_seconds()
        except ValueError:
            elapsed = 1e9
    by_count = len(pending) >= batch
    by_timeout = bool(pending) and elapsed >= timeout_min * 60
    # --force（2026-08-24 早盘高频实时窗口调用）：跳过双触发门槛，有信号即推。
    # 幂等：当天 code 去重仍生效（新 code 才入池），--force 只是"不等数量/超时"。
    if not (by_count or by_timeout) and not args.force:
        save_pushed(pushed, now, update_last=False)
        print(f"[short_spirit] 累计池 {len(pending)}/{batch}（或{timeout_min}min超时）未达阈值，跳过"
              f"{'，本轮新增' + str(len(new_pool)) if new_pool else ''}")
        return
    # --force 但待推池为空 → 无信号可推，防空卡误推（2026-08-24 早盘高频窗口）
    if not pending:
        save_pushed(pushed, now, update_last=False)
        print("[short_spirit] --force 但待推池为空，跳过（无新信号）")
        return

    card_signals = sorted(pending, key=lambda x: -x["strength"])[: cfg["max_signals"]]
    card = build_card(card_signals, sess, now)
    cf = ROOT / "cache" / "short_spirit_card.json"
    cf.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    rc = __import__("os").system(f'"{sys.executable}" "{ROOT}/scripts/push_card.py" --webhook feishu_webhook_intraday "{cf}"')
    if rc == 0:
        pushed["codes"] = (pushed.get("codes", []) or []) + [s["code"] for s in card_signals]
        # 池内超出 max_signals 的部分保留，不丢（下次触发再推）
        pushed["pending"] = [s for s in pending if s["code"] not in {x["code"] for x in card_signals}]
        if not pushed["pending"]:
            pushed["pending_ts"] = ""
        save_pushed(pushed, now)
        print(f"[short_spirit] 推送成功 {len(card_signals)} 只（触发：{'数量达标' if by_count else '超时兜底'}）")


if __name__ == "__main__":
    main()
