#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日内题材脉冲盯盘系统 v2
事件驱动型脉冲检测 + 量能扩散分析 + 结构化飞书卡片

用法:
  python3 intraday_pulse.py --mode intra       # 盘中脉冲检测
  python3 intraday_pulse.py --mode post        # 收盘总结
  python3 intraday_pulse.py --mode intra --dry-run
"""

from __future__ import annotations

import argparse, json, os, random, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 统一 JSON 原子写（2026-09-02 · P0-1）：latest.json 曾被并发写坏
# （两个进程各自 O_TRUNC 后按自身 offset 写 → 新内容+旧尾部残余 →
#  展示端解析报 JSONDecodeError: Extra data: line 1697）。
# 铁律：快照类 JSON 一律走 common.json_io.save_json（tmp+fsync+os.replace）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.json_io import save_json as _save_json, load_json as _load_json
from common.em_flow import fetch_sector_flow_details, sector_flow_structure  # P2-18 分层（CapitalPulse 吸收）

# 选股宝板块 → 东财官方板块 映射（2026-10-08 用户「系统自造聚合名改为东财官方板块名」）。
# 纯读表（`cache/sector_official_map.json`，由 scripts/sector_official.py --build 离线生成），
# 运行时不发任何网络请求；映射层缺失/异常 → 静默降级（top5 不带 official 字段，行为同改动前）。
_P_DIR = str(Path(__file__).resolve().parent)
if _P_DIR not in sys.path:
    sys.path.insert(0, _P_DIR)
try:
    from sector_official import lookup as _official_lookup
except Exception:                       # pragma: no cover - 环境缺失时降级
    _official_lookup = None

# ── Config ────────────────────────────────────────────

BJ_TZ = timezone(timedelta(hours=8))


def _load_feishu_webhook():
    """飞书 webhook 单一数据源：优先从 config/settings.json 读取；
    缺失时回退到环境变量 FEISHU_WEBHOOK；都无则返回空串（不硬编码任何密钥，避免入库泄露）。"""
    try:
        cfg = Path(__file__).resolve().parent.parent / "config" / "settings.json"
        with open(cfg, encoding="utf-8") as f:
            s = json.load(f)
        return s.get("feishu_webhook") or s.get("feishu_webhook_prod") or ""
    except Exception:
        return os.environ.get("FEISHU_WEBHOOK") or ""


FEISHU_WEBHOOK = _load_feishu_webhook()
XGB_BASE = "https://flash-api.xuangubao.cn/api"
XGB_NEWS = "https://baoer-api.xuangubao.cn/api/v6"
EM_BASE = "https://emcfgdata.eastmoney.com/api/themeInvest"
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "pulse_snapshots"
CACHE_TTL = 120  # 缓存新鲜度阈值(秒)：<守护进程120s间隔，确保AI任务读到近2分钟快照
SENTINEL = CACHE_DIR / "last_symbols.json"  # 上次已知的异动股票集合
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
# ⚠️ 2026-10-13：选股宝「快讯」接口(baoer-api) 自 09-17 起对简化 UA 一律
#    `Connection reset by peer`（express.log 实证 227/227 全失败）→ 快讯长期静默为空。
#    仅**快讯**改走完整浏览器头（含 Referer/Origin），⛔ 不动通用 UA（flash-api 现正常，
#    避免误伤 plates/stocks/index 等既有接口）。
NEWS_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://xuangubao.cn/",
    "Origin": "https://xuangubao.cn",
    "Connection": "keep-alive",
}

PLATE_FOCUS = ["国产芯片", "军工", "智能电网", "机器人", "医药", "锂电池",
               "金刚石", "玻璃基板", "半导体", "AI", "算力", "光模块"]

RISK_TYPES = {"hold_sel_p": "拟减持", "hold_sell": "减持", "2_loss": "转亏",
              "may_desc": "预减", "less": "不及预期", "restrict": "即将解禁"}

# ── Helpers ───────────────────────────────────────────

def now(): return datetime.now(BJ_TZ)
def ts(): return str(int(time.time()*1000))
def rc():
    """东财接口要求的 randomCode：35位纯数字(13位时间戳+13位随机+9位随机)"""
    return str(int(time.time()*1000)) + str(random.randint(10**12, 10**13-1)) + str(random.randint(10**8, 10**9-1))
def today_c(): return now().strftime("%Y%m%d")
def in_trading(asof=None):
    """asof: 'HH:MM' 流程触发时间。守卫按触发时间判定，而非脚本执行/完成时间。"""
    n = now()
    if asof:
        try:
            hh, mm = asof.strip().split(":")
            t = int(hh)*60 + int(mm)
        except Exception:
            t = n.hour*60 + n.minute
    else:
        t = n.hour*60 + n.minute
    in_window = (540 <= t <= 690) or (780 <= t <= 900)
    if not in_window:
        return False
    # 节假日门禁：法定节假日与周末（含调休补班日）一律不推送（is_a_share_trading_day 为唯一权威）
    try:
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(n)
    except Exception:
        return n.weekday() < 5  # trading-day-fallback: 唯一真源不可用时的周末兜底

def http_get(url, t=10, hdrs=None):
    r = urllib.request.Request(url, headers=hdrs or {"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=t) as resp:
        return json.loads(resp.read().decode("utf-8"))

def http_get_tx_index(url, t=10):
    """腾讯 qt 指数接口返回 GBK 文本，交给 parse_tx_index 解析。"""
    r = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=t) as resp:
        return resp.read().decode("gbk")

def http_post(url, body, t=10):
    d = json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(url, data=d, headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=t) as resp:
        return json.loads(resp.read().decode("utf-8"))


EM_RISING_URL = ("https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2"
                 "&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
                 "&fields=f2,f3,f8,f12,f14,f100")


def _fetch_em_rising():
    """东财全市场涨幅榜兜底（2026-08-18）：拉前 200 只（pn1+2），合并 diff 按 code 去重。"""
    merged = {}
    for pn in (1, 2):
        try:
            d = http_get(EM_RISING_URL.format(pn=pn))
            diff = (d.get("data") or {}).get("diff") or []
            for it in diff:
                c = str(it.get("f12") or "")
                if c:
                    merged[c] = it
        except Exception:
            continue
    return {"data": {"diff": list(merged.values())}}


def parse_em_rising(raw):
    """东财涨幅榜 → 标准异动条目 [{code,name,pct,price,industry,up}]。
    f3=涨幅% f12=代码 f14=名称 f2=现价 f100=东财行业。"""
    out = []
    diff = ((raw or {}).get("data") or {}).get("diff") or []
    for it in diff:
        c = str(it.get("f12") or "").strip()
        if not c or c.startswith("688"):
            continue                      # 688 科创板无权限跳过（与 build_leaders 一致）
        name = str(it.get("f14") or c).strip()
        if name.startswith("ST") or name.startswith("*ST"):
            continue                      # ⛔ 铁律④ 不看ST（2026-08-26 用户抓包 ST海龙进脉冲卡）
        pct = it.get("f3")
        if not isinstance(pct, (int, float)):
            continue
        pct = round(float(pct), 2)
        if pct < 5:
            continue                      # 与 pre_stocks 门槛一致：仅 ≥5% 异动
        is_20 = c[:3] in ("300", "301")
        up = pct >= (19.9 if is_20 else 9.9)   # 涨幅达涨停价视为封板，否则可买
        out.append({
            "code": c,
            "name": name,
            "pct": pct,
            "price": it.get("f2"),
            "industry": str(it.get("f100") or ""),
            "up": up,
        })
    return out


# ── Fetcher ───────────────────────────────────────────

class Fetcher:
    def __init__(self):
        self.errors = []

    def fetch_all(self):
        results = {}
        tasks = {
            "plates":    lambda: http_get(f"{XGB_BASE}/surge_stock/plates?_ts={ts()}"),
            "stocks":    lambda: http_get(f"{XGB_BASE}/surge_stock/stocks?normal=true&uplimit=true&_ts={ts()}"),
            # 涨停前夜·未封板但强势拉升的可买票（提前埋伏信号源）
            "pre":       lambda: http_get(f"{XGB_BASE}/surge_stock/stocks?normal=true&uplimit=false&_ts={ts()}"),
            "newsflash": lambda: http_get(f"{XGB_NEWS}/message/newsflash?subj_ids=9,10,723,35,469,821&platform=pcweb&_ts={ts()}", hdrs=NEWS_HEADERS),
            "tomorrow":  lambda: http_post(f"{EM_BASE}/getFryTomorrowList", {
                "args":{"pageSize":3,"lastTradeDate":""},
                "client":"wap","clientType":"cfw","clientVersion":"9001",
                "randomCode":rc(), "timestamp":ts()
            }),
            # 大盘指数（复用 stock/data 接口）
            "index":     lambda: http_get(f"{XGB_BASE}/stock/data?fields=symbol,stock_chi_name,change_percent,price&strict=true&symbols=000001.SS,399001.SZ,399006.SZ&_ts={ts()}"),
            # 大盘成交额 + 全市场涨跌家数（量能广度·2026-08-17 → 2026-08-25 数据源修正）
            # ⛔ 2026-08-25 用户「东财接口不行·换腾讯」：东财 push2 全天 73 次请求全失败
            # （Remote end closed connection·疑似风控），量能/广度全天缺失 → 情绪分量能修正
            # 系数失效（market_amount_yi=0.0）。修复：**腾讯 qt.gtimg.cn 优先**（指数成交额
            # 字段35/37 实测可用：上证 8588亿+深证 9730亿≈1.83万亿）；东财兜底（有涨跌家数）。
            # 腾讯只给量能（成交额）、不给涨跌家数 → 广度缺失时 parse 层降级（up/down=None，
            # feedback_matrix brd_mult 保持 1.0，诚实不编造）。
            "em_market": lambda: http_get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&"
                "secids=1.000001,0.399001,0.399006&fields=f2,f3,f6,f12,f14,f104,f105,f106"),
            # 腾讯量能兜底源（东财挂时用：qt.gtimg.cn 指数成交额）——fetch_market 层双源切换
            "tx_index":  lambda: http_get_tx_index("http://qt.gtimg.cn/q=sh000001,sz399001,sz399006"),
            # 跌停池 / 炸板池（情绪双指标）
            "ld_pool":   lambda: http_get(f"{XGB_BASE}/pool/detail?pool_name=limit_down&_ts={ts()}"),
            "broken_pool": lambda: http_get(f"{XGB_BASE}/pool/detail?pool_name=limit_up_broken&_ts={ts()}"),
            # 东财全市场涨幅榜兜底（2026-08-18 用户「中大力德拉升无提示」）：选股宝 surge_stock 只收
            # 精选异动票（未封板池仅 ~9 只），涨幅≥5% 的非精选票会被漏——用 push2 涨幅榜 pn1+2
            # 拉前 200 兜底，保证拉升中的票进 leaders 参与 zt/charge/strong/surge 检测。
            "rising_em": lambda: _fetch_em_rising(),
        }
        with ThreadPoolExecutor(max_workers=8) as ex:
            fs = {ex.submit(fn): n for n, fn in tasks.items()}
            for f in as_completed(fs):
                n = fs[f]
                try: results[n] = f.result()
                except Exception as e:
                    self.errors.append(f"[{n}] {e}")
                    results[n] = None

        results.update({"stock_data": None, "labels": None})
        s = results.get("stocks")
        pre = results.get("pre")
        syms = []
        if s and s.get("data"):
            syms += [it[0] for it in (s["data"].get("items") or []) if isinstance(it, list) and it]
        if pre and pre.get("data"):
            syms += [it[0] for it in (pre["data"].get("items") or []) if isinstance(it, list) and it]
        syms = list(dict.fromkeys(syms))[:80]   # 去重，封顶80只
        if syms:
            sd_url = (f"{XGB_BASE}/stock/data?fields=symbol,stock_chi_name,change_percent,"
                      f"price,limit_up_days&strict=true&symbols={','.join(syms)}&_ts={ts()}")
            lb_url = f"{XGB_BASE}/stock_label/labels?symbols={','.join(syms)}&_ts={ts()}"
            with ThreadPoolExecutor(max_workers=2) as ex:
                fsd = ex.submit(http_get, sd_url)
                flb = ex.submit(http_get, lb_url)
                try:
                    results["stock_data"] = fsd.result()
                except Exception as e: self.errors.append(f"[stock_data] {e}")
                try:
                    results["labels"] = flb.result()
                except Exception as e: self.errors.append(f"[labels] {e}")
        # 二级联动：热门题材 → 成分股（东财 getStockList，按 f3 倒序）
        results["theme_stocks"] = {}
        tmr_raw = results.get("tomorrow")
        if tmr_raw:
            top_codes = [t.get("themeCode") for t in parse_tomorrow(tmr_raw)[:5] if t.get("themeCode")]
            if top_codes:
                def _stocklist(code):
                    return http_post(f"{EM_BASE}/getStockList", {
                        "args":{"themeCode":code,"pageSize":1000,"pageNum":1,"sort":-1,"sortField":"f3"},
                        "client":"web","clientVersion":"8.3","clientType":"cfw",
                        "randomCode":rc(), "timestamp":ts()
                    })
                with ThreadPoolExecutor(max_workers=5) as ex:
                    fsl = {ex.submit(_stocklist, c): c for c in top_codes}
                    for f in as_completed(fsl):
                        c = fsl[f]
                        try: results["theme_stocks"][c] = f.result()
                        except Exception as e: self.errors.append(f"[theme_stocks:{c}] {e}")
        return results

# ── Data Aggregation ──────────────────────────────────

def parse_stocks(raw):
    """Parse stocks API into dict keyed by code

    ⚠️ 2026-08-17 晚修复：接口返回结构迁移——旧 `{"stocks":{"data":{fields,items}}}`
    变为新 `{"code":20000,"data":{fields,items}}`（顶层 data，无 stocks 包装层）。
    旧解析读 raw["stocks"]["data"] 恒空 → pre_stocks/stocks 全空 → 早鸟埋伏断链。
    本函数兼容双结构（新结构优先，旧结构兜底）。"""
    out = {}
    sr = raw.get("stocks", {}) if isinstance(raw.get("stocks"), dict) else {}
    # 新结构：顶层 data 直接含 fields/items；旧结构：raw["stocks"]["data"]
    data = raw.get("data") if isinstance(raw.get("data"), dict) else None
    if (not data or "fields" not in data) and sr.get("data"):
        data = sr.get("data")
    if not data or "fields" not in data:
        return out
    fs = data.get("fields", [])
    items = data.get("items") or []
    # fields: code,prod_name,cur_price,px_change_rate,circulation_value,description,
    #         enter_time,up_limit,plates,time_on_market,turnover_ratio,m_days_n_boards,...
    for it in items:
        try:
            code, name = str(it[0]), str(it[1])
            if name.startswith("ST") or name.startswith("*ST"):
                continue                  # ⛔ 铁律④ 不看ST（2026-08-26 用户抓包）
            pct = float(it[3]) if it[3] else 0.0
            turn = float(it[10]) if len(it) > 10 and it[10] else 0.0
            cap = float(it[4]) if it[4] else 0.0
            et = int(it[6]) if len(it) > 6 and it[6] else 0
            up = bool(it[7]) if len(it) > 7 else False
            plates = it[8] if len(it) > 8 else []
            boards = str(it[11]) if len(it) > 11 else ""
            desc = str(it[5]) if len(it) > 5 else ""
            price = float(it[2]) if it[2] else 0.0

            hot_plates = [p["name"] for p in (plates or []) if isinstance(p, dict) and p.get("hot_spot")]

            out[code] = {
                "code": code, "name": name, "pct": round(pct*100, 2),
                "turn": round(turn*100, 1), "cap": cap, "cap_yi": round(cap/1e8, 1),
                "enter_ts": et, "enter_time": datetime.fromtimestamp(et, BJ_TZ).strftime("%H:%M") if et else "--:--",
                "up": up, "plates": plates, "boards": boards, "desc": desc,
                "price": price, "hot_plates": hot_plates,
            }
        except: pass
    return out

def parse_plates(raw):
    out = {}
    pr = raw.get("plates", {})
    if pr and pr.get("data"):
        for p in pr["data"].get("items") or []:
            out[p["id"]] = {"id": p["id"], "name": p.get("name",""), "desc": p.get("description","")}
    return out

def parse_newsflash(raw):
    """解析选股宝快讯。兼容两代接口结构：
    - 新版（baoer-api v6，2026-08 实测）：raw = {"code":20000, "data": {"messages": [...]}}
    - 旧版（flash-api）：raw = {"newsflash": {"data": {"messages": [...]}}}
    ⚠️ 2026-08-17 修复：旧代码只读 raw["newsflash"]["data"]["messages"]，
    新版接口返回结构顶层是 data（newsflash 包装层消失），导致消息面永久为空。
    """
    data = raw.get("data") if isinstance(raw.get("data"), dict) else None
    if data is None:
        nf = raw.get("newsflash") or {}
        data = nf.get("data") if isinstance(nf, dict) else None
    out = []
    if data:
        for m in (data.get("messages") or [])[:15]:
            stks = m.get("stocks", [])
            bkjs = m.get("bkj_infos", [])
            if stks or bkjs:
                mid = m.get("id") or m.get("msg_id") or m.get("news_id") or ""
                ctime = (m.get("created_at") or m.get("ctime") or m.get("ct")
                         or m.get("publish_time") or m.get("ts") or 0)
                out.append({"title": m.get("title",""), "summary": m.get("summary",""),
                           "stocks": [(s["name"], s["symbol"]) for s in stks],
                           "bkj": [b["name"] for b in bkjs],
                           "id": str(mid), "ctime": ctime})
    return out

# ── 快讯去重（按自然日持久化已推集合，只推新快讯，避免重复推送历史消息）──
PUSHED_NF = CACHE_DIR.parent / "pushed_newsflash.json"

def _nf_key(m):
    mid = m.get("id") or m.get("msg_id") or m.get("news_id") or ""
    if mid:
        return f"id:{mid}"
    return f"t:{str(m.get('title',''))[:40]}"

def dedup_newsflash(nf_list):
    """按自然日持久化已推快讯集合，仅返回本轮新快讯，避免重复推送历史消息"""
    if not nf_list:
        return nf_list
    d = today_c()
    store = {}
    if PUSHED_NF.exists():
        try:
            store = json.loads(PUSHED_NF.read_text())
        except Exception:
            store = {}
    pushed = set(store.get(d, []))
    fresh = []
    for m in nf_list:
        k = _nf_key(m)
        if k and k not in pushed:
            fresh.append(m)
            pushed.add(k)
    store[d] = list(pushed)
    # 仅保留最近3天，防止文件无限增长
    keep = sorted(store.keys())[-3:]
    store = {k: store[k] for k in keep}
    try:
        PUSHED_NF.write_text(json.dumps(store, ensure_ascii=False))
    except Exception:
        pass
    return fresh

def parse_index(raw):
    """大盘三指数：上证/深成/创业板"""
    out = []
    idx = raw.get("index", {})
    if idx and idx.get("data"):
        order = ["000001.SS", "399001.SZ", "399006.SZ"]
        for sym in order:
            d = idx["data"].get(sym)
            if d:
                out.append({"name": d.get("stock_chi_name", sym),
                            "pct": round((d.get("change_percent") or 0) * 100, 2),
                            "price": round(d.get("price") or 0, 2)})
    return out


def parse_em_market(raw):
    """东财大盘成交额 + 全市场涨跌家数（P0-量能广度·2026-08-17 → 2026-08-17 晚扩展沪深全市场）。

    数据源 em_market = push2 ulist（f6=成交额元 f104涨/f105跌/f106平）。
    返回 {"total_amount_yi": 两市成交额(亿), "up": 上涨家数, "down": 下跌家数, "flat": 平盘家数}
    缺失时返回空 dict（下游优雅降级，不中断）。

    ⚠️ 口径修正（2026-08-17 用户「仅上证 2000 余只·实际约 5000 家」）：
    - 旧实现只取第一个指数（上证 f104/f105/f106）→ 上涨家数 1777+491+83≈2351 只，
      漏掉深证（深证成指 2324+540+66≈2930 只），全市场 ≈5281 只只统计了一半。
    - 修正：**上证 + 深证成指**两市场 f104/f105/f106 相加 = 全市场约 5281 家；
      创业板指(399006)是深市子集（涨跌家数含在深证内），**跳过防重复计数**。
    - 成交额 total_amount_yi：三指数 f6 相加（上证 1.11万亿 + 深证 1.27万亿 + 创业板 6316亿 ≈ 3.02万亿）
      保持既有口径（用户明确量能 3 万亿不变；创业板成交额独立计入，涨跌家数不重复计）。
    """
    em = raw.get("em_market") or {}
    diff = (em.get("data") or {}).get("diff") or []
    if not diff:
        return {}
    total_yi = 0.0
    up = down = flat = None
    for d in diff:
        amt = float(d.get("f6") or 0.0)
        if amt > 0:
            total_yi += amt / 1e8  # 元→亿
        # 涨跌家数：上证 + 深证成指相加（创业板指是深市子集，跳过防重复）
        nm = str(d.get("f14") or "")
        if nm == "创业板指":
            continue
        f104 = d.get("f104")
        if f104 is not None:
            up = (up or 0) + int(f104)
            down = (down or 0) + int(d.get("f105") or 0)
            flat = (flat or 0) + int(d.get("f106") or 0)
    return {"total_amount_yi": round(total_yi, 1), "up": up, "down": down, "flat": flat}

def parse_tx_index(raw):
    """腾讯 qt.gtimg.cn 指数成交额（2026-08-25 用户「东财接口不行·换腾讯」·量能兜底源）。

    东财 push2 全天失败时（2026-08-25 实测 73 次全挂 Remote end closed connection），
    量能/广度缺失 → 情绪分量能修正系数失效。腾讯 qt.gtimg.cn 实测可用：
      sh000001 字段35 = "3889.44/464117264/858874228922"（现价/手/成交额元）
      sz399001 同理 → 两市合计 = total_amount_yi。
    ⛔ 腾讯**不给涨跌家数**（f104/f105 无）→ up/down/flat 返回 None，下游 brd_mult 保持
    1.0 不修正（诚实降级不编造）；广度修复待后续找源（可选：必盈/akshare）。
    返回 {"total_amount_yi": 亿, "up": None, "down": None, "flat": None}；失败返回 {}。
    """
    tx = raw.get("tx_index") or ""
    if not isinstance(tx, str):
        return {}
    total_yi = 0.0
    for line in tx.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        parts = line.split("~")
        # 字段35 = 现价/成交量(手)/成交额(元)（指数格式）；字段37 = 成交额(万)
        amt = None
        if len(parts) > 37:
            try:
                amt = float(parts[37]) * 1e4  # 万→元
            except (ValueError, TypeError):
                amt = None
        if amt is None and len(parts) > 35 and "/" in str(parts[35]):
            try:
                amt = float(str(parts[35]).split("/")[-1])
            except (ValueError, TypeError, IndexError):
                amt = None
        if amt and amt > 0:
            total_yi += amt / 1e8  # 元→亿
    if total_yi <= 0:
        return {}
    return {"total_amount_yi": round(total_yi, 1), "up": None, "down": None, "flat": None}


def _fetch_tx_selfchanged():
    """腾讯自选股 changedist 涨跌分布（2026-08-25 用户「腾讯自选股接口呢」·广度终极兜底）。

    东财 push2 挂 + 腾讯 qt 无涨跌家数时，用腾讯自选股 skill（westock-data）的 changedist
    命令拿全市场涨跌分布 + 两市成交额（实测：上涨4234/下跌1246/平70/涨停70/跌停4 + 成交额
    18318.44亿·响应~1.3s）。npx 首次拉包慢，已缓存后稳定；失败返回 None（下游降级）。
    """
    import subprocess
    try:
        # ⛔ 禁止硬编码 node 版本路径（2026-09-12 事故）→ 统一 common/npx_path 运行时解析
        from common.npx_path import find_npx
        _npx = find_npx()
        if not _npx:
            print("[intraday_pulse] npx 不可用（changedist 兜底跳过）")
            return None
        # launchd 的 PATH 可能没有 node；npx 的 shebang 通过 env 查找同目录 node。
        _env = os.environ.copy()
        _env["PATH"] = f"{Path(_npx).parent}{os.pathsep}{_env.get('PATH', '')}"
        r = subprocess.run(
            [_npx, "-y", "westock-data-skillhub@1.0.5", "changedist"],
            capture_output=True, text=True, timeout=30, env=_env)
        if r.returncode != 0:
            print(f"[intraday_pulse] changedist npx 失败 rc={r.returncode}: {r.stderr.strip()[:150]}")
            return None
        return r.stdout
    except Exception as e:
        print(f"[intraday_pulse] changedist 异常: {e}")
        return None


def parse_tx_selfchanged(raw):
    """腾讯自选股 changedist 输出 → {total_amount_yi, up, down, flat}。

    解析 markdown 表：`| 上涨 | 下跌 | 平盘 | 涨停 | 跌停 | 停牌 |` 行 + `两市成交额：NNNN.NN亿`。
    失败返回 {}（下游优雅降级）。"""
    if not isinstance(raw, str) or not raw.strip():
        return {}
    up = down = flat = None
    total_yi = 0.0
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("|") and "上涨" in line and "下跌" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            # 表头行跳过；找数值行（下一个 | 开头的行）——改为直接从下一行取
            continue
        if line.startswith("|") and "上涨占比" not in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 6 and all(c.replace(".", "").isdigit() for c in cells[:6] if c):
                up, down, flat = int(cells[0]), int(cells[1]), int(cells[2])
        if "两市成交额" in line:
            import re as _re
            m = _re.search(r"([\d.]+)亿", line)
            if m:
                total_yi = float(m.group(1))
    if up is None and down is None and total_yi <= 0:
        return {}
    return {"total_amount_yi": round(total_yi, 1) if total_yi > 0 else None,
            "up": up, "down": down, "flat": flat}


def fetch_market(raw):
    """量能/广度三级降级（2026-08-25 用户「东财不行换腾讯」）：
    ①东财 push2（含涨跌家数·口径权威）→ ②腾讯 qt.gtimg.cn（量能三指数口径）
    + 腾讯自选股 changedist（广度）→ ③全挂返回 {}。

    ⚠️ 口径铁律（2026-08-25 实测核对）：
    - **量能 = qt 三指数合计**（上证8589+深证9730+创业板4640=22959亿）——与东财历史口径
      「3.02万亿=三指数f6相加」一致（8/17 实证）；changedist 的 18318亿=上证+深证主板
      （漏创业板独立成交）≠ 两市真实成交额，**不用作量能**。
    - **广度 = changedist**（上涨4234/下跌1246/平70·全市场含创业板）——腾讯 qt 不提供、
      东财挂时唯一广度来源。
    - 缺失时返回 {}（下游优雅降级不编造），feedback_matrix 落 market_missing 告警。
    """
    em = parse_em_market(raw)
    if em and em.get("total_amount_yi"):
        return em
    tx = parse_tx_index(raw)
    if tx and tx.get("total_amount_yi"):
        # 腾讯 qt 有量能但无广度 → 补 changedist 拿涨跌家数（npx subprocess·慢源放最后）
        try:
            cd = parse_tx_selfchanged(_fetch_tx_selfchanged())
            if cd and cd.get("up") is not None:
                tx["up"], tx["down"], tx["flat"] = cd["up"], cd["down"], cd["flat"]
        except Exception:
            pass
        return tx
    cd = parse_tx_selfchanged(_fetch_tx_selfchanged())
    if cd and cd.get("total_amount_yi"):
        return cd
    return {}


def parse_pool(raw, key):
    """跌停池/炸板池：返回数量与全量名单。

    ⛔ **科创板 688/689 一律剔除**（2026-09-17 修）：用户无科创板交易权限，属**铁律**。
    此前只过滤了 ST，漏了 688 —— 实证 2026-09-17 炸板池含「近岸蛋白(688137)」，
    被盘中实时页的「极值名单」渲染出来（前端只拿到名字、没有代码，**无法自行过滤**）。
    → 与 `parse_em_rising` / `parse_stocks` 的 688 过滤**同规格**，在源头统一剔除。
    ⚠️ `count` 一并改为**剔除后**的名单长度，避免「数量含 688、名单不含」的自相矛盾。
    """
    p = raw.get(key, {})
    raw_items = (p or {}).get("data") or []
    if not isinstance(raw_items, list): return {"count": 0, "names": [], "items": []}
    names, items = [], []
    for it in raw_items:
        if isinstance(it, dict):
            sym = str(it.get("symbol") or it.get("code") or "")
            if sym[:3] in ("688", "689"):
                continue                      # ⛔ 科创板无权限（铁律）
            nm = it.get("stock_chi_name") or it.get("name") or ""
            if not nm: continue
            names.append(nm)
            # 跌幅：终端「极值名单」要显示每只票跌了多少（2026-10-31 用户要求）。
            # ⚠️ 上游字段名不止一种，且**单位无法在此刻实测确认**（选股宝池接口
            #    在非交易时段超时）→ 原样带出数值，不做单位换算、不做符号修正。
            #    前端只在 |pct| ≥ 1 时显示（即已经像百分比），
            #    否则一律不显示 —— 宁可缺，不可错。
            pct = None
            for k in ("change", "px_change_rate", "pct", "chg"):
                v = it.get(k)
                if isinstance(v, (int, float)):
                    pct = round(float(v), 2)
                    break
            items.append({"code": sym, "name": nm, "pct": pct})
    return {"count": len(names), "names": names, "items": items}

# ── 关注板块重挫归因（方案A：跌停/炸板池 → 关注板块命中）──
# 基于用户关注方向（通信设备/半导体/航天军工/锂电池/CPO光模块，见 USER.md）
# 维护「板块→核心票」映射，对跌停/炸板池做命中检测，让亏钱效应显形。
FOCUS_SECTORS = {
    "CPO/光模块": ["中际旭创","新易盛","天孚通信","剑桥科技","华工科技","光迅科技","太辰光","博创科技","罗博特科","联特科技","铭普光磁"],
    "PCB": ["深南电路","沪电股份","胜宏科技","生益科技","景旺电子","东山精密","鹏鼎控股","兴森科技"],
    "半导体/芯片": ["中芯国际","北方华创","韦尔股份","兆易创新","卓胜微","长电科技","通富微电","寒武纪","海光信息","澜起科技","紫光国微","北京君正"],
    "通信设备": ["中兴通讯","烽火通信","中天科技","亨通光电","星网锐捷","紫光股份","锐捷网络","剑桥科技"],
    "航天军工": ["中航沈飞","航发动力","中航西飞","洪都航空","航天电子","中国卫星","光威复材","中航光电","振华科技"],
    "锂电池": ["宁德时代","比亚迪","亿纬锂能","天赐材料","恩捷股份","赣锋锂业","天齐锂业","璞泰来","先导智能"],
    "AI服务器/算力": ["工业富联","浪潮信息","中科曙光","紫光股份","高新发展","鸿博股份","拓维信息","神州数码","闻泰科技"],
    "机器人": ["埃斯顿","汇川技术","拓斯达","绿的谐波","鸣志电器","柯力传感","江苏北人","机器人"],
}

def build_weak_sectors(ld_pool, broken_pool):
    """跌停/炸板池 → 关注板块命中归因。输出结构化结果供卡片渲染。"""
    ld = set((ld_pool or {}).get("names") or []) if isinstance(ld_pool, dict) else set()
    bk = set((broken_pool or {}).get("names") or []) if isinstance(broken_pool, dict) else set()
    focus_hits = []
    for sector, stocks in FOCUS_SECTORS.items():
        hit_ld = [n for n in stocks if n in ld]
        hit_bk = [n for n in stocks if n in bk]
        if hit_ld or hit_bk:
            focus_hits.append({"sector": sector, "ld": hit_ld, "broken": hit_bk,
                               "total": len(hit_ld) + len(hit_bk)})
    focus_hits.sort(key=lambda x: -x["total"])
    ld_total = (ld_pool or {}).get("count") if isinstance(ld_pool, dict) else None
    bk_total = (broken_pool or {}).get("count") if isinstance(broken_pool, dict) else None
    return {
        "focus_hits": focus_hits,
        "ld_count": ld_total if ld_total is not None else len(ld),
        "broken_count": bk_total if bk_total is not None else len(bk),
        "ld_names": list(ld), "broken_names": list(bk),
    }

# ── 板块跌幅榜（下跌风险检测器·方案B）─────────────────
# 东财板块涨跌幅榜：po=0 跌幅最大在前，一次拉全量覆盖全部下跌板块。
# 直接捕捉"板块普跌/阴跌"（旧系统按涨停数筛选，对未跌停的中间地带完全失明）。
EM_BOARD_HOSTS = ["push2.eastmoney.com", "push2delay.eastmoney.com"]  # push2 实时优先，push2delay 降级兜底(2026-08-18)
EM_BOARD_FS = [("行业", "m:90+t:2"), ("概念", "m:90+t:3")]
SECTOR_FOCUS_KEYWORDS = ["CPO", "光模块", "光通信", "印制电路板", "PCB", "半导体", "芯片",
                         "通信设备", "通信", "军工", "锂电池", "算力", "英伟达", "机器人",
                         "消费电子", "液冷", "铜缆", "高速连接", "封装", "光刻", "数据中心", "服务器"]
SECTOR_RISK_THRESHOLD = -2.5  # 板块单日跌幅≥2.5% 即列为"重挫"
# 资金流向榜噪声过滤词表统一从 scripts/flow_noise.py 引入（单一权威来源，禁止在此维护副本）
try:
    from flow_noise import SECTOR_FLOW_NOISE, is_flow_noise
except ImportError:
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from flow_noise import SECTOR_FLOW_NOISE, is_flow_noise

def _em_board_get(host, fs, po=0, pz=100, fid="f3", tries=3):
    url = f"https://{host}/api/qt/clist/get?" + urllib.parse.urlencode({
        "pn": 1, "pz": pz, "po": str(po), "np": "1", "fltt": "2", "invt": "2",
        "fid": fid, "fs": fs, "fields": "f12,f14,f3,f62",
    })
    hdr = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
           "Referer": "https://quote.eastmoney.com/center/boardlist.html"}
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
                return (d.get("data") or {}).get("diff") or []
        except Exception as e:
            last = e
            time.sleep(0.3)
    return []

def build_sector_risk(threshold=SECTOR_RISK_THRESHOLD):
    """东财板块跌幅榜 + 资金流榜。
    注意：东财 clist 接口每页最多返回 100 条（pz 超限被忽略）。
    跌幅榜按 f3 排序抓前 100 足够；但资金流榜若也按 f3 排序，涨得好/大额流入的板块会落在 100 名外而被漏掉。
    因此资金流榜**独立按 f62(主力净额)排序抓取**（po=1 取净流入最大、po=0 取净流出最大），保证大额资金直接落第 1 页。
    """
    # 60s 缓存仅复用完整快照；不完整抓取不能覆盖最后一次可信数据。
    cache_path = CACHE_DIR / "sector_risk_cache.json"
    cached_data, cached_age = None, None
    try:
        cb = json.loads(cache_path.read_text())
        age = (now() - datetime.fromtimestamp(cb.get("ts", 0), BJ_TZ)).total_seconds()
        if 0 <= age and isinstance(cb.get("data"), dict) and cb["data"].get("complete") is True:
            cached_data, cached_age = cb["data"], age
            if age < 60:
                return cached_data
    except Exception:
        pass

    def unavailable(reason, partial=None):
        # 只允许同交易日、10 分钟内的完整快照回退，并明确标为旧数据。
        if cached_data and cached_age is not None and cached_age <= 600 and \
                str(cached_data.get("asof", ""))[:10] == now().strftime("%Y-%m-%d"):
            return {**cached_data, "ok": False, "stale": True, "error": reason}
        return {**(partial or {}), "ok": False, "complete": False, "error": reason,
                "declining": (partial or {}).get("declining", []),
                "focus_declining": (partial or {}).get("focus_declining", []),
                "count": (partial or {}).get("count"),
                "inflow_top": (partial or {}).get("inflow_top", []),
                "outflow_top": (partial or {}).get("outflow_top", [])}

    try:
        allsec = {}
        def fetch_all(host):
            res, received = {}, set()
            for _label, fs in EM_BOARD_FS:
                rows = _em_board_get(host, fs)
                if rows:
                    received.add(fs)
                for it in rows:
                    nm = it.get("f14", "")
                    pct = it.get("f3")
                    if nm and isinstance(pct, (int, float)):
                        cur = res.get(nm)
                        if cur is None or pct < cur["pct"]:
                            res[nm] = {"name": nm, "pct": round(pct, 2),
                                       "main_inflow": it.get("f62"),
                                       "is_focus": any(k in nm for k in SECTOR_FOCUS_KEYWORDS)}
            return res, received
        # 双源并行抓取；同一类别至少有一个 host 返回才算采集完整。
        with ThreadPoolExecutor(max_workers=2) as ex:
            r0 = ex.submit(fetch_all, EM_BOARD_HOSTS[0])
            r1 = ex.submit(fetch_all, EM_BOARD_HOSTS[1])
            all0, received0 = r0.result()
            all1, received1 = r1.result()
            allsec.update(all0)
            for nm, v in all1.items():
                cur = allsec.get(nm)
                if cur is None or v["pct"] < cur["pct"]:
                    allsec[nm] = v
        # ── 板块资金流向排行（净流入/净流出，按 f62 主力净流入额）──
        # 东财 clist 每页最多 100 条且 pz 超限被忽略；若按 f3 排序，涨得好/大额流入的板块落在 100 名外会被漏掉。
        # 故**独立按 f62 排序抓取**（po=1 净流入最大 / po=0 净流出最大），大额资金直接落第 1 页。
        # f62 单位=元，渲染层 ÷1e8 显示亿；过滤宽基/风格噪声标签，只留真实行业/概念板块。
        def _num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return 0.0
        def _yi(v):
            m = _num(v.get("main_inflow"))
            return round(m / 1e8, 1) if m else 0.0
        def _is_noise(nm):
            return any(k in nm for k in SECTOR_FLOW_NOISE)
        def fetch_flow(host):
            out, received = {}, set()
            for fs in ("m:90+t:2", "m:90+t:3"):
                for po in (1, 0):  # po=1 净流入最大在前；po=0 净流出最大在前
                    rows = _em_board_get(host, fs, po=po, pz=100, fid="f62")
                    if rows:
                        received.add((fs, po))
                    for it in rows:
                        nm = it.get("f14", "")
                        if not nm:
                            continue
                        f62 = _num(it.get("f62"))
                        cur = out.get(nm)
                        if cur is None or abs(f62) > abs(_num(cur.get("main_inflow"))):
                            out[nm] = {"name": nm, "code": str(it.get("f12") or ""),
                                       "pct": round(_num(it.get("f3")), 2),
                                       "main_inflow": f62,
                                       "is_focus": any(k in nm for k in SECTOR_FOCUS_KEYWORDS)}
            return out, received
        # 双源并行抓资金流（两 host 取并集，保留绝对值更大的 f62）
        with ThreadPoolExecutor(max_workers=2) as ex:
            ff0 = ex.submit(fetch_flow, EM_BOARD_HOSTS[0])
            ff1 = ex.submit(fetch_flow, EM_BOARD_HOSTS[1])
            flow_sec, flow_received0 = ff0.result()
            flow1, flow_received1 = ff1.result()
            for nm, v in flow1.items():
                cur = flow_sec.get(nm)
                if cur is None or abs(v["main_inflow"]) > abs(cur["main_inflow"]):
                    flow_sec[nm] = v
        flow_clean = [v for v in flow_sec.values() if not _is_noise(v["name"])]
        # 抓取端保留 Top10（2026-08-17 用户定：数据样本更大更好维护；渲染端各自截 Top5 展示）。
        # 收益：theme_rotation 回流判定(_capital_state 模糊匹配)/relay_score 板块资金覆盖更广，
        # 卡片展示层(_flow_columns [:5]/AI 拼卡)仍只显前 5，阅读负担不变。
        inflow_top = [{"name": v["name"], "code": v["code"], "pct": v["pct"],
                       "main_inflow": v["main_inflow"], "flow_yi": _yi(v), "is_focus": v["is_focus"]}
                      for v in sorted([x for x in flow_clean if x["main_inflow"] > 0],
                                      key=lambda x: x["main_inflow"], reverse=True)[:10]]
        outflow_top = [{"name": v["name"], "code": v["code"], "pct": v["pct"],
                        "main_inflow": v["main_inflow"], "flow_yi": _yi(v), "is_focus": v["is_focus"]}
                       for v in sorted([x for x in flow_clean if x["main_inflow"] < 0],
                                       key=lambda x: x["main_inflow"])[:10]]
        # ── P2-18 资金结构分层补全（CapitalPulse 吸收·2026-09-09）──
        # 榜单 Top10×2 + focus 板块代码池 → ulist.np 一次批量拉 超大/大/中/小 + 源时间戳，
        # 合并入榜单条目（亿口径·与 flow_yi 同级）。缺失/异常自动跳过不阻塞主流程；
        # EM_FLOW_ENABLED=0 关闭后本块空转，榜单回落纯 f62 旧形态（可逆）。
        try:
            _flow_codes = [v["code"] for v in inflow_top] + [v["code"] for v in outflow_top]
            _flow_codes += [v["code"] for v in flow_clean if v.get("is_focus") and v["code"]]
            _detail = fetch_sector_flow_details(list(dict.fromkeys(_flow_codes)))
            for _lst in (inflow_top, outflow_top):
                for _it in _lst:
                    _d = _detail.get(_it.get("code") or "")
                    if _d:
                        _it.update(sector_flow_structure(_d))
        except Exception as _e:
            pass  # 分层为增强字段，任何异常不回退榜单主流程
        declining = sorted([v for v in allsec.values() if v["pct"] <= threshold],
                           key=lambda x: x["pct"])[:12]
        focus_decl = [v for v in declining if v["is_focus"]]
        breadth_complete = len(received0 | received1) == len(EM_BOARD_FS) and bool(allsec)
        flow_complete = len(flow_received0 | flow_received1) == 4 and bool(flow_sec)
        complete = breadth_complete and flow_complete
        result = {"threshold": threshold, "declining": declining,
                  "focus_declining": focus_decl, "count": len(declining),
                  "inflow_top": inflow_top, "outflow_top": outflow_top,
                  "ok": complete, "complete": complete, "stale": False,
                  "asof": now().isoformat()}
        if not complete:
            # 东财一侧超时也可能只返回流出榜；部分结果不能写进可信缓存或资金池。
            return unavailable("板块涨跌幅或资金流接口返回不完整", result)
        # ── 盘中全量板块资金池（2026-09-08 用户「板块流入流出数据本地数据池积累」）──
        # 短线精灵资金行原只查 sector_risk top10 → 强势板块涨停多但资金排不进前10时
        # 一律「💤资金未入榜」（实证 09-08 大消费 zt=6 但资金榜全被通信/有色占满）。
        # 此处把每轮已抓到的全量 flow_clean(≈360 板块)额外落盘为 sector_fund_pool.json，
        # 盘中任意板块可按名查到实时净额；每轮刷新=当日数据池，capture_daily 盘后另有
        # 355 板块日级时间序列沉淀，两层构成完整积累。60s 缓存节流天然限频、零额外抓取。
        try:
            _pool = {"ts": now().timestamp(), "date": now().strftime("%Y-%m-%d"),
                     "boards": {v["name"]: {"pct": v["pct"], "flow_yi": _yi(v)}
                                for v in flow_clean}}
            (CACHE_DIR / "sector_fund_pool.json").write_text(
                json.dumps(_pool, ensure_ascii=False))
        except Exception:
            pass
        try:
            _save_json(cache_path, {"ts": now().timestamp(), "data": result})
        except Exception:
            pass
        return result
    except Exception as e:
        return unavailable(str(e))

def build_sector_flow_snapshot(threshold=SECTOR_RISK_THRESHOLD):
    """板块主力资金流全量快照（日级沉淀用）。

    复用 _em_board_get，按 f62(主力净额) 双源抓取行业+概念全量板块，
    过滤宽基/风格噪声，返回 {name: {pct, flow_yi, is_focus}}。
    与 build_sector_risk 的区别：本函数**不截断 Top3**，保留全量，
    供 capture_daily 沉淀为 cache/sector_flow_history/ 日级时间序列，
    支撑「题材回流预测」的资金边际趋势判定（流入递增/流出收敛）。
    """
    try:
        def fetch_flow_full(host):
            out = {}
            for fs in ("m:90+t:2", "m:90+t:3"):
                for po in (1, 0):  # po=1 净流入在前；po=0 净流出在前
                    for it in _em_board_get(host, fs, po=po, pz=100, fid="f62"):
                        nm = it.get("f14", "")
                        if not nm or any(k in nm for k in SECTOR_FLOW_NOISE):
                            continue
                        f62 = it.get("f62") or 0
                        cur = out.get(nm)
                        if cur is None or abs(f62) > abs(cur.get("main_inflow") or 0):
                            out[nm] = {"name": nm,
                                       "pct": round(it.get("f3") or 0, 2),
                                       "main_inflow": f62,
                                       "flow_yi": round(f62 / 1e8, 2),
                                       "is_focus": any(k in nm for k in SECTOR_FOCUS_KEYWORDS)}
            return out
        with ThreadPoolExecutor(max_workers=2) as ex:
            r0 = ex.submit(fetch_flow_full, EM_BOARD_HOSTS[0])
            r1 = ex.submit(fetch_flow_full, EM_BOARD_HOSTS[1])
            sec = r0.result()
            for nm, v in r1.result().items():
                cur = sec.get(nm)
                if cur is None or abs(v["main_inflow"]) > abs(cur["main_inflow"]):
                    sec[nm] = v
        return {nm: {"pct": v["pct"], "flow_yi": v["flow_yi"], "is_focus": v["is_focus"]}
                for nm, v in sec.items()}
    except Exception as e:
        print(f"[build_sector_flow_snapshot] ERR:{e}")
        return {}


def parse_ladder(raw):
    """连板天梯：从 stock_data 接口取 limit_up_days，按高度分层"""
    sd = raw.get("stock_data", {})
    ladder = {}
    if sd and sd.get("data"):
        data = sd["data"]
        items = data.items() if isinstance(data, dict) else []
        for sym, d in items:
            if not isinstance(d, dict): continue
            days = d.get("limit_up_days") or 0
            if days >= 2:
                ladder.setdefault(days, []).append(d.get("stock_chi_name", sym))
    return [{"boards": k, "stocks": v[:6]} for k, v in sorted(ladder.items(), reverse=True)]

def parse_tomorrow(raw):
    out = []
    # 东财接口实际把题材数组放在顶层 data 字段；兼容旧包裹 raw.tomorrow.data
    tmr = raw.get("data")
    if not tmr and isinstance(raw.get("tomorrow"), dict):
        tmr = raw.get("tomorrow", {}).get("data")
    if tmr:
        seen = {}
        for t in tmr:
            if t.get("isHot") != 1: continue
            name = t.get("themeName","")
            # 盘前/竞价阶段 f3/fex3/cumulateF3 可能为 None，统一兜底为 0 防比较崩溃
            item = {"name": name, "themeCode": str(t.get("themeCode","")),
                    "pct": float(t.get("f3") or 0), "zt": float(t.get("fex3") or 0),
                    "cum_pct": t.get("cumulateF3") or 0, "title": t.get("title","")}
            # 同一题材多新闻记录会重复，按名称去重(保留 fex3 最高)
            if name not in seen or item["zt"] > seen[name]["zt"]:
                seen[name] = item
        out = list(seen.values())
    return sorted(out, key=lambda x: x["zt"] or 0, reverse=True)

def parse_theme_stocks(raw):
    """解析 getStockList 返回，取涨幅前 N 的成分股（接口已按 f3 倒序）"""
    out = []
    data = (raw or {}).get("data") or {}
    sl = data.get("stockList") or []
    for it in sl[:8]:
        # codeSuffix 已是完整代码(如 920130.BJ)，securityCode 仅为数字段，避免重复拼接
        code = str(it.get("codeSuffix") or it.get("securityCode") or "")
        if not code: continue
        name = it.get("securityName") or ""
        f3 = it.get("f3") or 0
        cap = it.get("f20") or 0   # 总市值
        dragon = bool(it.get("dragonStockLabel") or it.get("label"))
        out.append({
            "code": code, "name": name,
            "pct": round(float(f3), 2) if f3 else 0.0,
            "cap_yi": round(float(cap)/1e8, 1) if cap else 0.0,
            "dragon": dragon,
        })
    return out

# ── Sector Builder ────────────────────────────────────

def build_sectors(stocks, plates):
    """Build sector -> stocks mapping with metrics"""
    sectors = {}
    for pid, p in plates.items():
        sectors[pid] = {"name": p["name"], "desc": p["desc"], "stocks": [], "zt": 0, "hot": 0, "turn_sum": 0.0}

    for code, s in stocks.items():
        for pl in (s.get("plates") or []):
            pid = pl.get("id")
            if pid and pid in sectors:
                sectors[pid]["stocks"].append(s)
                sectors[pid]["turn_sum"] += s["turn"]
                if s["up"]: sectors[pid]["zt"] += 1
                if pl.get("hot_spot"): sectors[pid]["hot"] += 1

    # compute avg turnover
    for pid in list(sectors):
        stks = sectors[pid]["stocks"]
        if stks:
            sectors[pid]["turn_avg"] = round(sectors[pid]["turn_sum"] / len(stks), 1)
        if not stks:
            del sectors[pid]

    return sectors

# ── Early Warning (提前埋伏信号) ──────────────────────

def query_pct(symbols):
    """实时查任意个股涨幅/现价/是否封板，用于催化抢跑信号"""
    if not symbols: return {}
    try:
        d = http_get(f"{XGB_BASE}/stock/data?fields=symbol,stock_chi_name,change_percent,price,limit_up_days&strict=true&symbols={','.join(symbols)}&_ts={ts()}")
        out = {}
        for sym, v in (d.get("data") or {}).items():
            out[sym] = {
                "name": v.get("stock_chi_name", sym),
                "pct": round((v.get("change_percent") or 0) * 100, 2),
                "price": round(v.get("price") or 0, 2),
                "up": (v.get("limit_up_days") or 0) >= 1,
                "limit_up_days": v.get("limit_up_days") or 0,
            }
        return out
    except Exception:
        return {}

def build_early_signals(pre_stocks, plates, sectors, newsflash):
    """提前埋伏信号：在板块涨停前给出可买窗口。

    两类信号：
      1) 爆发前夜：板块内出现 limit_up_days==0 且涨幅5%~20% 的未封板强势票
         → 这些票现在能买，且多数会在数分钟内冲板。
      2) 催化抢跑：盘中快讯催化刚落地，被点名个股尚未涨停 → 可埋伏。
    """
    signals = []

    # —— 1) 爆发前夜：按板块聚合未封板拉升票 ——
    sec_pre = {}   # pid -> [stocks]
    for code, s in pre_stocks.items():
        if code[:6].startswith("688"):
            continue                      # 科创板(688)无交易权限，跳过
        if s.get("limit_up_days", 0) >= 1:
            continue                      # 曾涨停=炸板/开板，排除
        if not (5 <= s["pct"] <= 20):
            continue                      # 涨幅不足或非涨停区间
        for pl in (s.get("plates") or []):
            pid = pl.get("id")
            if pid and pid in plates:
                sec_pre.setdefault(pid, []).append(s)

    for pid, stks in sec_pre.items():
        if not stks:
            continue
        p = plates[pid]
        lead = max(stks, key=lambda x: x["pct"])
        is_20 = lead["pct"] > 11          # 20cm票涨幅空间更大
        zt_in = sectors.get(pid, {}).get("zt", 0)
        members = [{"name": x["name"], "pct": x["pct"]} for x in sorted(stks, key=lambda x: -x["pct"])[:3]]
        etype = "爆发前夜" if zt_in == 0 else "爆发中"
        signals.append({
            "type": etype,
            "sector": p["name"],
            "desc": p.get("desc", ""),
            "buy": {"name": lead["name"], "code": lead["code"][:6],
                    "pct": lead["pct"], "price": lead["price"],
                    "is_20cm": is_20, "limit_up_days": lead.get("limit_up_days", 0)},
            "count": len(stks),
            "zt_in_sector": zt_in,
            "members": members,
        })

    # —— 2) 催化抢跑：快讯催化 → 查被点名个股是否未涨停 ——
    cat_syms = []
    for nf in (newsflash or []):
        for nm, sym in nf.get("stocks", []):
            if sym:
                cat_syms.append(sym)
    cat_syms = list(dict.fromkeys(cat_syms))[:30]
    cat_pct = query_pct(cat_syms) if cat_syms else {}
    seen_sectors = set()
    for nf in (newsflash or []):
        bkjs = nf.get("bkj", [])
        stks = nf.get("stocks", [])
        buyables = []
        for nm, sym in stks:
            if sym[:6].startswith("688"):
                continue                  # 科创板(688)无交易权限，跳过
            d = cat_pct.get(sym)
            if d and not d["up"] and 3 <= d["pct"] <= 20:
                buyables.append({"name": nm, "code": sym[:6],
                                  "pct": d["pct"], "price": d["price"]})
        if buyables and bkjs:
            sec = bkjs[0]
            if sec in seen_sectors:
                continue
            seen_sectors.add(sec)
            buyables.sort(key=lambda x: -x["pct"])
            signals.append({
                "type": "催化抢跑",
                "sector": sec,
                "desc": nf.get("title", ""),
                "buy": buyables[0],
                "count": len(buyables),
                "zt_in_sector": 0,
                "members": buyables[:3],
            })

    type_order = {"爆发前夜": 0, "催化抢跑": 1}
    signals.sort(key=lambda x: (type_order.get(x["type"], 9), -x["buy"].get("pct", 0)))
    return signals[:4]


def build_leaders(stocks, pre_stocks, plates):
    """题材领涨龙：每个热门题材涨得最好的个股（领涨标杆/可买机会）。

    数据源：涨停票(stocks,已封板) + 未封板拉升票(pre)，每只都带 plates 题材归属，
    按板块聚合取涨幅前二 = 该题材领涨龙 + 板块次强（2026-08-18 扩：原只取最高 1 只，
    板块次强票如机器人概念中大力德被埋没，用户看到拉升却无任何提示）。
    东财涨幅榜兜底不并入本池——全量 180+ 只会挤占精选；由 latest.json 独立 `rising` 字段
    承载，live_signal 检测池 = leaders + rising 双扫（见 live_signal）。
    - 过滤科创板(688)（无权限）
    - buyable=未封板（能买，优先推）；否则为涨停标杆龙（定方向、买不进）
    - is_20cm 按代码前缀判定（300/301 创业板20cm，主板10cm）
    """
    merged = {}
    for code, s in stocks.items():
        merged[code] = s
    for code, s in pre_stocks.items():
        merged[code] = s                          # 重叠时 pre 覆盖（同票未封板，仍按 up 字段判定）
    sec_stk = {}
    for code, s in merged.items():
        if code[:6].startswith("688"):
            continue                              # 科创板无权限跳过
        for pl in (s.get("plates") or []):
            pid = pl.get("id")
            if pid and pid in plates:
                sec_stk.setdefault(pid, []).append(s)
    leaders = []
    seen_codes = set()
    for pid, stks in sec_stk.items():
        p = plates[pid]
        if p["name"] == "其他" or "ST" in p["name"]:
            continue                              # 过滤 其他/ST 板块
        for lead in sorted(stks, key=lambda x: x["pct"], reverse=True)[:3]:
            if lead["pct"] < 5:
                continue                          # 微弱异动不计为领涨
            c6 = lead["code"][:6]
            if c6 in seen_codes:
                continue                          # 多题材归属去重，保留首个板块
            seen_codes.add(c6)
            buyable = not lead.get("up", False)   # up=True=已封板买不进；否则可买（按接口 up_limit 实判）
            prefix = lead["code"][:3]
            is_20 = prefix in ("300", "301")      # 创业板20cm；主板10cm；688已过滤
            leaders.append({
                "sector": p["name"],
                "name": lead["name"], "code": c6,
                "pct": lead["pct"], "price": lead["price"],
                "buyable": buyable, "is_20cm": is_20,
                "boards": lead.get("boards", ""), "limit_up_days": lead.get("limit_up_days", 0),
            })
    # 排序：可买领涨优先，其次涨停标杆；同组按涨幅
    leaders.sort(key=lambda x: (0 if x["buyable"] else 1, -x["pct"]))
    return leaders[:40]


def build_hot_spots(stocks):
    """官方领涨个股：选股宝 hot_spot=1 标记的板块领涨股，按板块分组。

    数据源：parse_stocks 已从 plates[i].hot_spot 提取 hot_plates 列表。
    每个 hot_spot=1 的票 = 选股宝认定的该板块官方领涨股。
    - 过滤科创板(688)、ST、其他
    - buyable = not up（未封板可买；已封板为官方标杆）
    """
    spot_stocks = {}
    for code, s in stocks.items():
        if code[:6].startswith("688"): continue
        for pn in (s.get("hot_plates") or []):
            if pn == "其他" or "ST" in pn: continue
            prefix = code[:3]
            spot_stocks.setdefault(pn, []).append({
                "name": s["name"], "code": s["code"][:6],
                "pct": s["pct"], "price": s["price"],
                "buyable": not s.get("up", False),
                "is_20cm": prefix in ("300", "301"),
                "boards": s.get("boards", ""),
            })
    # 按板块涨停数降序，板块内按涨幅降序
    return sorted(
        [{"sector": k, "stocks": v} for k, v in spot_stocks.items()],
        key=lambda x: (-len(x["stocks"]), -max(s["pct"] for s in x["stocks"]))
    )


# ── Event Detection ────────────────────────────────────

def detect_new_events(stocks, plates):
    """Find stocks that just entered the surge list (new events) and which sectors they belong to"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    prev = set()
    if SENTINEL.exists():
        try: prev = set(json.loads(SENTINEL.read_text()))
        except: pass

    current = set(stocks.keys())
    new_codes = current - prev

    # Save current for next comparison
    SENTINEL.write_text(json.dumps(list(current)))

    if not new_codes:
        return {}

    # Group new stocks by sector
    events = {}
    for code in new_codes:
        s = stocks.get(code)
        if not s: continue
        for pl in (s.get("plates") or []):
            pid = pl.get("id")
            if not pid: continue
            pname = plates.get(pid, {}).get("name", "?")
            if pid not in events:
                events[pid] = []
            events[pid].append(s)

    # Keep only sectors with >=1 new stock and prioritize focus sectors
    return dict(sorted(events.items(),
               key=lambda x: (x[0] not in [k for k,v in plates.items() if any(f in v.get("name","") for f in PLATE_FOCUS)],
                              -len(x[1]))))

# ── Dragon Diagnosis ──────────────────────────────────
#
# ⛔⛔ 2026-09-17 用户拍板：「**龙头基本不会出现日内切换** —— 龙头讲究的是连续性、领涨性和带领性。
#      一般确定了之后，哪怕是前赴后继变强而已，也不会改变龙头的核心。」
#      → 换人的**唯一条件 = 原龙头退位**（不再封板 = 炸板 / 跌停 / 断板）。
#
# 病根（实证 2026-09-17）：原实现是 `max(up, key=(主板, len(boards), hot, pct))` ——
#   ① 每轮从「当轮上涨的票」里重选 → 拿到的是**排名**，不是**身份**；
#   ② 末档排序键是**当轮涨幅** → 谁这会儿涨得多谁是龙头；
#   ③ `len(boards)` 拿「板数字符串长度」当连板高度 —— 实测 `'4天2板'` 与 `'8天5板'` 长度同为 4
#      → **完全并列** → 直接落到 `pct` 决胜负。
# 后果：一天之内同一个题材"换"好几次龙头，且是**循环**（09-17 PCB板 6 次切换只有 3 个名字：
#      中材科技→中岩大地→中材科技→澳弘电子→中材科技→中岩大地→澳弘电子）。
#      这些抖动经 `memory_lib.detect_events` 变成 `龙头切换` 事件，占全天事件 **51.4%**。

# 龙头锁定开关：⛔ 关掉 = 回到「逐轮重选」的旧行为（可逆，便于对照与回退）
ENABLE_DRAGON_LOCK = True
# 缺失封板时刻时的排序兜底（排最后，⛔ 不让缺失数据胜出）
_ENTER_TS_MISSING = 1 << 62
_BOARD_RE = re.compile(r"(\d+)\s*板")

def parse_board_num(boards) -> int:
    """`m_days_n_boards`（形如 `'4天2板'` / `'2板'` / `'18天9板'`）→ **连板数**（整数）。

    ⛔ 绝对不要用 `len(boards)` 当高度：`'4天2板'`(4) 与 `'8天5板'`(4) 长度相同、
    而 `'18天9板'`(5) 只是**碰巧**更长。长度不是高度。
    取**最后一个** `N板` 匹配（`4天2板` → 2）；无 `N板` 形态时退回第一个数字；再没有 → 0。
    """
    s = str(boards or "")
    hits = _BOARD_RE.findall(s)
    if hits:
        try:
            return int(hits[-1])
        except ValueError:
            return 0
    m = re.search(r"\d+", s)
    return int(m.group()) if m else 0

def _leader_key(s: dict):
    """龙头候选排序键 = **连续性 ↓ → 领涨性 ↑ → 代码（确定性兜底）**。

    · **连续性**：连板数（`parse_board_num`，⛔ 不是字符串长度）
    · **领涨性**：首封时刻（`enter_ts`）越早越像带队 —— 与 `wealth_code/core/leader.py`
      的 `score_leadership(fbt, theme_first_fbt)` 同一口径（那边是「落后越多人越不像带队」）
    · ⛔ **刻意不含 `pct`**：当轮涨幅进判据 = 龙头每轮抖动（就是上面那条病根）
    · ⛔ **刻意不含「主板优先」**：用户 2026-08-13 已撤销主板倾斜（主板/创业板对称参与，
      无板块属性加分/减分），故不再让主板票无条件压过先封板的创业板票
    · 末档用 `code` 而非名单顺序 → **纯确定性**，同一份数据必得同一结论
    """
    return (
        -parse_board_num(s.get("boards", "")),
        s.get("enter_ts") or _ENTER_TS_MISSING,
        str(s.get("code", "")),
    )

def update_lock(prev_lock: dict | None, candidate: dict, now_hm: str) -> dict:
    """按锁定规则更新锁条目（**纯函数**，便于单测）。

    · 同一只票 → 保留**首次确立**时刻（`at` 不变 = 身份延续）
    · 换了票（= 原龙头退位后才可能发生）→ `at` 重置为当前时刻（新身份开始计时）
    """
    code = str((candidate or {}).get("code") or "")
    if not code:
        return dict(prev_lock or {})
    keep_same = bool(prev_lock) and str((prev_lock or {}).get("code")) == code
    return {
        "code": code,
        "name": (candidate or {}).get("name", ""),
        "board": parse_board_num((candidate or {}).get("boards", "")),
        "at": ((prev_lock or {}).get("at") or now_hm) if keep_same else now_hm,
    }

def _dragon_lock_path(day: str | None = None) -> Path:
    """龙头锁的**按日**状态文件（⛔ 文件名带日期 → 次日自动重新确立，不用手动清）"""
    d = day or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    return CACHE_DIR / f"dragon_locks_{d}.json"

def load_dragon_locks(day: str | None = None) -> dict:
    """读当日龙头锁（`{题材名: {code, name, board, at}}`）。⛔ 任何异常 → 空 dict，绝不中断主流程"""
    try:
        d = _load_json(_dragon_lock_path(day)) or {}
        locks = d.get("locks") if isinstance(d, dict) else None
        return locks if isinstance(locks, dict) else {}
    except Exception:
        return {}

def _is_trading_day(d: str) -> bool:
    """A股交易日 —— **唯一真源 = common.trading_calendar**（2026-10-09 唯一化）。

    长假幽灵源：`dragon_locks_<休市日>.json` 实测在 2026-10-01/02/05 各落了一份
    （14 条锁，内容来自接口回吐的上一交易日池）。写入端按交易日拦截。
    """
    try:
        from common.trading_calendar import is_a_share_trading_day
        return is_a_share_trading_day(d)
    except Exception:  # pragma: no cover - 极端环境兜底
        try:
            return datetime.strptime(str(d)[:10], "%Y-%m-%d").weekday() < 5  # trading-day-fallback
        except Exception:
            return True


def save_dragon_locks(locks: dict, day: str | None = None,
                      *, allow_non_trading: bool = False) -> bool:
    """写当日龙头锁（原子写）。**非交易日拒写**（防长假幽灵产物）。

    ⛔ 失败只告警，不影响快照与推送。
    """
    d = day or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    if not allow_non_trading and not _is_trading_day(d):
        print(f"  ⛔ {d} 非A股交易日 → 跳过龙头锁写盘（防幽灵日产物）")
        return False
    try:
        p = _dragon_lock_path(day)
        p.parent.mkdir(parents=True, exist_ok=True)
        ok = _save_json(p, {"day": p.stem.replace("dragon_locks_", ""), "locks": locks or {}})
        if not ok:
            p.write_text(json.dumps({"locks": locks or {}}, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        print(f"  ⚠️ 龙头锁写盘失败（不影响主流程）: {e}")
        return False

def diagnose_dragon(sector_stocks, total_zt, locked: dict | None = None):
    """板块龙头诊断。

    @param locked 当日**已锁定**的龙头 `{code, name}`（来自 `load_dragon_locks`）。
        ⛔ 只要它**本轮仍在封板名单里**（`up`），就**保持身份** —— 即使另有票连板更高、
        涨幅更大，那只是「前赴后继变强」，不是换龙头。**只有它不再封板（退位）才允许换人。**
    """
    up = [s for s in sector_stocks if s["up"]]
    if not up: return None

    candidate = min(up, key=_leader_key)

    # ── 龙头锁定：原龙头仍封板 → 保持身份（只允许加固，不允许易主）──
    lock_held = False
    if ENABLE_DRAGON_LOCK and locked and locked.get("code"):
        still = next((s for s in up if str(s.get("code")) == str(locked["code"])), None)
        if still is not None:
            candidate = still
            lock_held = True

    # Leadership: zt_count in sector / total market zt (max 10)
    leadership = min(10, round(len(up) / max(1, total_zt) * 25, 1))

    # Breakthrough: 连板数 + 20cm bonus
    # ⛔ 原实现读的是 `'N天M板'` 里的 **N（天数）** 当高度 → 任何 ≥5 天的票都直接顶到 10 分。
    #    改用**连板数 M**（`parse_board_num`），与注释「Breakthrough: boards」一致。
    bt = 5.0
    nb = parse_board_num(candidate.get("boards", ""))
    if nb > 0:
        bt = {1: 5.5, 2: 6.5, 3: 7.5, 4: 9.0}.get(nb, min(10, 5 + nb * 1.2))
    if candidate["pct"] >= 19: bt += 1.5
    bt = min(10, bt)

    # Uniqueness: only top board, only 20cm, only hot in sector
    # ⛔ 同样把 `len(boards)` 换成连板数，否则「唯一最高板」判定跟着字符串长度走
    un = 5.0
    cb = parse_board_num(candidate.get("boards", ""))
    mxb = max(parse_board_num(s.get("boards", "")) for s in up)
    if mxb > 0 and cb == mxb and sum(1 for s in up if parse_board_num(s.get("boards", "")) == mxb) == 1:
        un += 2.5
    if candidate["pct"] >= 19 and sum(1 for s in up if s["pct"] >= 19) == 1: un += 2.5
    if candidate.get("hot_plates") and sum(1 for s in up if s.get("hot_plates")) == 1: un += 1.5
    un = min(10, un)

    score = round((leadership + bt + un) / 3, 1)
    return {"candidate": candidate, "leadership": leadership, "breakthrough": round(bt,1),
            "uniqueness": round(un,1), "score": score, "lock_held": lock_held}

# ── Seal Quality ──────────────────────────────────────

def seal_quality(s):
    """Rate seal board quality from enter_time and turnover"""
    et = s.get("enter_ts", 0)
    hour_min = et % 86400 / 3600 if et else 15  # default late afternoon
    turn = s.get("turn", 0)

    if not s.get("up"): return "⚪ 未封板"
    if hour_min < 9.75:  # before 9:45
        return "🔥 秒封板" if turn < 5 else "🔥 早封放量"
    elif hour_min < 10.5:
        return "✅ 早盘封板" if turn < 10 else "✅ 早封换手"
    elif hour_min < 13:
        return "→ 午前封板" if turn < 15 else "→ 午封换手"
    elif hour_min < 14.5:
        return "⚠ 午后封板" if turn < 20 else "⚠ 午后放量"
    else:
        return "❌ 尾盘偷袭"

# ── Risk Scan ─────────────────────────────────────────

def scan_risks(labels_raw, stocks):
    risks = []
    ld = labels_raw.get("data", {}) if labels_raw else {}
    for code, s in stocks.items():
        if not s["up"]: continue
        if code in ld:
            for lbl in ld.get(code, []):
                lt = lbl.get("label_type","")
                if lt in RISK_TYPES:
                    risks.append({"stock": f"{s['name']}({code[:6]})", "pct": s["pct"],
                                  "risk": RISK_TYPES[lt], "detail": lbl.get("description","")[:30]})
    return risks

# ── Positive Label Scan ───────────────────────────────

POSITIVE_TYPES = {
    "hold_buy": "增持", "hold_buy_p": "拟增持", "2_profit": "扭亏",
    "may_inc": "预增", "fewer": "筹码集中", "p_enter": "养老金新进",
    "investor": "密集调研", "placard": "举牌", "c_call": "连阳",
    "dragon_tiger": "龙虎榜",
}

def scan_positives(labels_raw, stocks):
    pos = {}
    ld = labels_raw.get("data", {}) if labels_raw else {}
    for code, s in stocks.items():
        if not s.get("up"): continue
        if code in ld:
            tags = []
            for lbl in ld.get(code, []):
                lt = lbl.get("label_type","")
                if lt in POSITIVE_TYPES:
                    tags.append({"tag": POSITIVE_TYPES[lt], "desc": lbl.get("description","")[:40]})
            if tags:
                pos[code] = tags
    return pos

# ── Insight Engine ────────────────────────────────────

# Sector characteristics knowledge base
SECTOR_CHAIN = {
    "国产芯片": "设备材料→封测→存储→设计",
    "半导体": "设备→材料→封测→设计",
    "军工": "弹药→航空→舰船→信息化",
    "智能电网": "特高压设备→电缆→变压器→储能",
    "机器人": "减速器→伺服→传感器→整机",
    "医药": "创新药→CXO→器械→流通",
    "锂电池": "上游锂矿→电解液→正负极→电池",
    "大消费": "白酒→食品→家电→零售",
    "房地产": "开发→物业→建材",
    "光伏": "硅料→硅片→电池→组件",
}

def generate_insight(plate_name, zt_count, total_zt, leader, desc, turn_avg, up_stocks):
    """基于数据推导题材逻辑，生成一句话深度分析"""
    parts = []

    # 1. 催化级别
    if zt_count >= 8:
        catalyst = "双催化共振"
    elif zt_count >= 5:
        catalyst = "催化有效兑现"
    else:
        catalyst = "催化偏弱"
    parts.append(catalyst)

    # 2. 扩散路径
    chain = SECTOR_CHAIN.get(plate_name, "")
    if chain and zt_count >= 3:
        # Check up_stocks for sub-sector clues
        sub_sectors = set()
        for s in up_stocks:
            ds = s.get("desc", "")
            for kw in ["设备", "材料", "封测", "存储", "设计", "电缆", "变压器"]:
                if kw in ds:
                    sub_sectors.add(kw)
        if sub_sectors:
            chain_hint = "→".join(list(sub_sectors)[:2]) + "端率先反应"
            parts.append(chain_hint)
        else:
            parts.append(f"产业链{chain[:15]}")
    elif zt_count >= 3:
        parts.append("板块整体联动")

    # 3. 龙头阶段
    boards = leader.get("boards", "")
    if "4天" in boards or "5天" in boards:
        parts.append("龙头加速期")
    elif "3天" in boards:
        parts.append("龙头确立期")
    elif "2天" in boards:
        parts.append("龙头晋级中")
    else:
        parts.append("龙头启动")

    # 4. 持续性预测
    conc = zt_count / max(1, total_zt)
    if conc > 0.15 and leader.get("up"):
        parts.append("持续性偏强")
    elif conc > 0.08:
        parts.append("持续性中等")
    else:
        parts.append("需观察持续性")

    # 5. 风险后缀
    if "转亏" in desc or "减持" in desc:
        parts.append("⚠内部分化")

    return "，".join(parts) + "。"

def build_insight_block(sectors, plates, diagnoses, total_zt):
    """为TOP5板块生成题材逻辑分析块"""
    blocks = []
    blocks.append(hr())
    blocks.append(div("**💡 题材逻辑**"))

    ranked = sorted(sectors.items(), key=lambda x: (-x[1]["zt"], -x[1]["hot"]))[:5]

    for pid, sec in ranked:
        pname = sec["name"]
        zt = sec["zt"]
        desc = sec.get("desc", "")
        turn_avg = sec.get("turn_avg", 0)
        diag = diagnoses.get(pid, {})
        leader = diag.get("candidate", {})
        up_stocks = [s for s in sec.get("stocks", []) if s.get("up")]

        if not leader:
            continue

        insight = generate_insight(pname, zt, total_zt, leader, desc, turn_avg, up_stocks)

        # Also check for positive labels on sector leaders
        # (handled separately in the main card, here we focus on logical analysis)

        blocks.append(div(f"→ **{pname}**：{insight}"))

    blocks.append(hr())
    return blocks

# ── Summary JSON for AI ───────────────────────────────

def compute_sentiment(zt_count, broken, ld_count, index):
    """采集阶段只产封板率；本轮 feedback 完成后回填唯一情绪分。

    不读取上一轮反馈冒充本轮，也不再用四因子另造有符号分。
    ld_count/index 保留调用兼容；不是评分输入。
    """
    seal_rate = zt_count / max(1, zt_count + broken["count"])
    return {"label": "未知", "score": None, "seal_rate": round(seal_rate * 100, 1),
            "source": "feedback_matrix", "status": "pending_feedback"}

def _sector_status(name, cur_zt, prev_map):
    """板块增量状态：new(新)/surging(强度跃升)/ongoing(持续平稳)"""
    if name not in prev_map:
        return "new"
    prev = prev_map[name]
    diff = cur_zt - prev
    if diff >= 3 or (prev > 0 and diff / prev >= 0.5) or (prev == 0 and cur_zt >= 2):
        return "surging"
    return "ongoing"


def _previous_theme_zt_count(stocks, previous_zt_codes):
    """按今日题材成分股回看昨日涨停池，得到可比的昨日题材涨停家数。

    `prev_data` 是上一轮盘中快照，只能用于本轮增量状态，不能冒充昨日数据。
    昨日池缺失时返回 None，避免把未知展示成 0。
    """
    if previous_zt_codes is None:
        return None
    codes = {
        str(s.get("code") or "").split(".")[0].strip()
        for s in (stocks or [])
        if str(s.get("code") or "").strip()
    }
    return len(codes & previous_zt_codes)

def build_limit_up_stocks(stocks, total_zt):
    """构造本轮完整涨停集合，供时间轴精确核对涨停家数变化的个股归因。"""
    rows = []
    for stock in stocks.values():
        if not stock.get("up"):
            continue
        code = str(stock.get("code") or "").strip()
        if not code:
            return None
        plates = [str(p.get("name") or "").strip() for p in (stock.get("plates") or [])
                  if isinstance(p, dict) and str(p.get("name") or "").strip()]
        rows.append({
            "code": code,
            "name": str(stock.get("name") or code).strip(),
            "pct": stock.get("pct"),
            "boards": stock.get("boards") or None,
            "sector": "、".join(plates[:3]) or None,
            # 首封时间（HH:MM）—— 题材内个股「按涨停时间排序」的唯一依据
            # （2026-10-31 用户要求）。`enter_time` 恒为 'HH:MM' 或 '--:--'，
            # 后者表示没有时间（不可用），落 None 让前端排到最后而不是当 00:00。
            "fbt": stock.get("enter_time") if stock.get("enter_time") != "--:--" else None,
        })
    # 集合不完整时不落盘，时间轴不得把未知明细伪装成空名单。
    return rows if len(rows) == total_zt and len({r["code"] for r in rows}) == total_zt else None


def build_summary_json(sectors, plates, diagnoses, total_zt, zt_count, risks, positives, stocks, newsflash, tomorrow,
                       index=None, ld_pool=None, broken_pool=None, ladder=None, early=None, leaders=None, hot_spots=None,
                       prev_data=None, em_market=None, rising=None, limit_up_stocks=None):
    # Filter: skip "其他" and sectors with no description + zt < 5
    def keep(pid, sec):
        if sec["name"] == "其他": return False
        if not sec.get("desc") and sec["zt"] < 5: return False
        return True
    ranked = [(pid, sec) for pid, sec in sorted(sectors.items(), key=lambda x: (-x[1]["zt"], -x[1]["hot"]))
              if keep(pid, sec)][:10]  # 放宽候选池，供 AI 全量研判后精选 TOP3
    # 板块增量：对比上一次推送快照（仅供盘中 new/surging/ongoing 状态）。
    intraday_prev_map = {}
    if prev_data:
        for s in prev_data.get("top5", []):
            intraday_prev_map[s.get("name")] = s.get("zt_count", 0)
    # 页面「昨日」必须来自昨日涨停池；不能复用上一分钟盘中快照。
    # 路径：cache/yesterday_zt.json 在 cache/ 根，而本文件 CACHE_DIR 指向 cache/pulse_snapshots/。
    yesterday = _load_json(CACHE_DIR.parent / "yesterday_zt.json")
    previous_zt_codes = None
    if isinstance(yesterday, dict) and isinstance(yesterday.get("zt_list"), list):
        previous_zt_codes = {
            str(row.get("code") or "").split(".")[0].strip()
            for row in yesterday["zt_list"]
            if str(row.get("code") or "").strip()
        }

    top5 = []
    for pid, sec in ranked:
        all_s = sec.get("stocks", [])
        up_s = [s for s in all_s if s.get("up")]
        diag = diagnoses.get(pid, {})
        leader = diag.get("candidate", {})
        top3 = sorted(all_s, key=lambda s: (s["up"], s.get("hot", False), s["pct"]), reverse=True)[:3]
        sector_risks = [r for r in risks if any(r["stock"].startswith(s["name"]) for s in all_s)]
        sector_pos = []
        for s in all_s:
            if s.get("up") and s["code"] in positives:
                sector_pos.append(f"{s['name']}({','.join(t['tag'] for t in positives[s['code']][:2])})")
        _ldr_code = (leader or {}).get("code", "")
        previous_theme_zt = _previous_theme_zt_count(all_s, previous_zt_codes)
        top5.append({
            "rank": len(top5)+1, "name": sec["name"], "desc": sec.get("desc", ""),
            "zt_count": sec["zt"], "turn_avg": sec.get("turn_avg", 0), "total_stocks": len(all_s),
            "leader": {
                "name": leader.get("name",""), "code": leader.get("code",""),
                "pct": leader.get("pct",0), "price": leader.get("price",0),
                "boards": leader.get("boards",""), "turn": leader.get("turn",0),
                "cap_yi": leader.get("cap_yi",0), "desc": leader.get("desc",""),
                "seal": seal_quality(leader) if leader else "",
            } if leader else None,
            # 2026-09-01 修：followers 从「全部涨停票排除 leader」取前3，不再依赖 top3[1:]
            # 旧逻辑 top3[1:] 假设 leader 必排 top3[0]，但 diagnose_dragon 另按(主板/板数)选龙，
            # 当 leader 排在 top3[1] 时被 code 过滤后 followers 变空（实证 09-01 光通信：
            # [青山纸业(龙), 双象股份] → top3 排序双象在前 → top3[1:]=[青山]→被滤→followers=[]，
            # 苗头卡只剩单一票）。排序 key 与 top3 一致（up>hot>pct），保证展示的跟风票质量。
            "followers": [{"name": s["name"], "pct": s["pct"], "turn": s["turn"]}
                          for s in sorted(all_s, key=lambda s: (s["up"], s.get("hot", False), s["pct"]), reverse=True)
                          if s["code"] != _ldr_code and s["up"]][:3],
            # 2026-09-03 新增：题材内全部涨停成分落盘（曾只存 leader+followers 共 4 只代表，
            # 其余涨停票丢失 → 机构卡/复盘卡只能报「机器人 7 家涨停」却点不出 7 只是谁。
            # 此字段=该题材当前全部涨停股，供卡片「盘面对照」逐只点名）。
            # ⛔ 2026-11-04 修「主线题材显示不全」：上限原为 20，而 `zt_count` 是**真实涨停家数**
            #    —— 实证 11-04 国产芯片 zt_count=23 但只落 20 只，再被前端剔掉 10 只科创板后
            #    只剩 10 只可看，用户看到的是「23 家涨停却只列出 10 个」。
            #    提到 60：实测 top5 里仅 1 个题材触及上限、多存 3 只 ≈0.2KB，
            #    而不截断的收益是「标题数字与列表能对上」。
            "zt_stocks": [{"name": s["name"], "code": s["code"], "pct": s["pct"]}
                          for s in sorted(all_s, key=lambda s: (s["up"], s.get("hot", False), s["pct"]), reverse=True)
                          if s.get("up")][:60],
            "diffusion": "共振" if len(up_s)>=3 else ("扩散" if len(up_s)>=2 else "单点"),
            "risks": [f"{r['stock']}({r['risk']})" for r in sector_risks[:3]],
            "positives": sector_pos[:3],
            "delta": _sector_status(sec["name"], sec["zt"], intraday_prev_map) if intraday_prev_map else "new",
            "zt_prev": previous_theme_zt,
            # 东财官方板块身份（2026-10-08）：{"boards":[{bk,name,score,coverage}...],
            # "method":"name+members|members|alias", "members":N}；未映射=None（保持原名）。
            # ⚠️ 只**新增字段**、不改 name —— name 是下游（live_signal/short_spirit/
            # theme_rotation/sector_family）的既有主键，改它会连带打穿已验证链路。
            "official": _official_lookup(sec["name"]) if _official_lookup else None,
        })
    ebb = detect_ebb(sectors, stocks, total_zt)
    index = index or []
    ld_pool = ld_pool or {"count": 0, "names": []}
    broken_pool = broken_pool or {"count": 0, "names": []}
    senti = compute_sentiment(zt_count, broken_pool, ld_pool["count"], index)
    # 板块内涨停股 → 快讯匹配（催化交叉验证）
    plate_names = {sec["name"] for _, sec in ranked}
    nf_hits = []
    for nf in (newsflash or []):
        rel = [b for b in nf.get("bkj", []) if any(p in b or b in p for p in plate_names)]
        stk = [s[0] for s in nf.get("stocks", [])][:3]
        if rel or stk:
            nf_hits.append({"title": nf.get("title", "")[:60], "plates": rel, "stocks": stk})
    summary = {"ts": now().isoformat(), "total_zt": zt_count,
            "sentiment": senti["label"], "sentiment_detail": senti,
            "index": index,
            "limit_down": ld_pool, "broken": broken_pool,
            "weak_sectors": build_weak_sectors(ld_pool, broken_pool),
            "sector_risk": build_sector_risk(),
            "ladder": ladder or [],
            "newsflash": nf_hits[:6],
            "em_expect": (tomorrow or [])[:5],
            "early": early or [],
            "leaders": leaders or [],
            "rising": rising or [],
            "hot_spots": hot_spots or [],
            # P0-量能广度（2026-08-17）：大盘成交额 + 全市场涨跌家数——情绪分/卡片广度维度数据底座
            # 2026-08-17 晚口径修正：涨跌家数沪深全市场（上证+深证，创业板是深市子集不重复计）
            "market": (em_market or {}).get("total_amount_yi"),
            "breadth": {"up": (em_market or {}).get("up"), "down": (em_market or {}).get("down"),
                        "flat": (em_market or {}).get("flat")},
            # 数据统计口径标注（2026-08-17 用户「标注口径与截止时间」）：涨跌家数=沪深全市场(A股)，
            # 成交额=沪深两市合计；截止时间 = 本轮抓取时间（非交易时段为上一轮定格）
            "data_scope": {
                "breadth": "沪深全市场A股(上证+深证·创业板含于深证不重复计·合计约5280家)",
                "amount": "沪深两市合计成交额(上证+深证+创业板·亿元)",
                "asof": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M"),
            },
            "ebb": ebb, "top5": top5,
            "sector_delta": {"new": [s["name"] for s in top5 if s["delta"]=="new"],
                             "surging": [f'{s["name"]}({s["zt_prev"]}→{s["zt_count"]})' for s in top5 if s["delta"]=="surging"],
                             "ongoing": [f'{s["name"]}({s["zt_prev"]}→{s["zt_count"]})' for s in top5 if s["delta"]=="ongoing"]}}
    if limit_up_stocks is not None:
        summary["limit_up_stocks"] = limit_up_stocks
    return summary

# ── 飞书卡片构建 ─────────────────────────────────────

def push_card(title, blocks, color="red"):
    c = {"wathet": "blue", "red": "red", "green": "green", "yellow": "yellow", "turquoise": "turquoise"}
    card = {"msg_type": "interactive", "card": {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": c.get(color, "red")},
        "elements": blocks,
    }}
    data = json.dumps(card, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(FEISHU_WEBHOOK, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8")).get("code") == 0
    except Exception as e:
        print(f"  [push err] {e}")
        return False

def md(t): return {"tag": "lark_md", "content": t}
def div(blocks): return {"tag": "div", "fields": blocks} if isinstance(blocks, list) else {"tag": "div", "text": md(blocks)}
def hr(): return {"tag": "hr"}
def note(t): return {"tag": "note", "elements": [{"tag": "plain_text", "content": t}]}
def col(*texts):
    """Create a column_set with multiple columns"""
    cols = []
    for t in texts:
        cols.append({"tag": "column", "width": "weighted", "weight": 1,
                      "elements": [{"tag": "div", "text": md(t)}]})
    return {"tag": "column_set", "flex_mode": "bisect", "background_style": "default", "columns": cols}

# ── Build TOP5 Sector Card ──────────────────────────────

def build_top5_card(sectors, plates, diagnoses, total_zt, risks, positives):
    """🔒 已退役·仅 debug 工具（2026-09-03 自查）：内容=盘中猎手+快照合集版，
    禁止作生产推送（生产推送卡=猎手/快照/live_signal 事件卡）。保留供本地预览/排错。"""
    """紧凑竖版卡片：板块排行→核心票详情→跟涨→标签→逻辑"""

    blocks = []
    ranked = [(pid, sec) for pid, sec in sorted(sectors.items(), key=lambda x: (-x[1]["zt"], -x[1]["hot"]))
              if sec["name"] != "其他" and (sec.get("desc") or sec["zt"] >= 5)][:5]

    blocks.append(div(f"**📊 涨停{total_zt}只 | 情绪{'亢奋' if total_zt>=20 else '修复' if total_zt>=12 else '分歧'}**"))
    blocks.append(hr())

    for rank, (pid, sec) in enumerate(ranked, 1):
        pname = sec["name"]
        zt = sec["zt"]
        turn_avg = sec.get("turn_avg", 0)
        diag = diagnoses.get(pid, {})
        dscore = diag.get("score", 0)
        pdesc = sec.get("desc", "")

        if zt >= 8: grade = "🔥"
        elif zt >= 5: grade = "🌟"
        elif zt >= 3: grade = "B→"
        else: grade = "C"

        blocks.append(div(
            f"**■ #{rank} {pname} {grade} | 涨停{zt}只 | 换手均值{turn_avg}%**"
            + (f" | 龙头分{dscore}/10" if dscore else "")
        ))

        # ── 核心票（红色）──
        all_s = sec.get("stocks", [])
        top = sorted(all_s, key=lambda s: (s["up"], s.get("hot", False), s["pct"]), reverse=True)[:3]
        leader = top[0] if top else None

        if leader:
            seal = seal_quality(leader)
            boards = leader.get("boards", "") or "首板"
            code_short = leader["code"][:6]
            desc_line = leader.get("desc", "")[:70]

            # 龙头(核心票)整行标红：名字/代码/核心二字/涨幅/价格 全部 red；
            # 换手留在红字外保持蓝色，封板质量保持橙色（避免被红字span阻断蓝色正则）
            blocks.append(div(
                f"<font color='red'>**🔱 {leader['name']} {code_short} 核心** +{leader['pct']}% {leader['price']}</font> "
                f"| {boards} | 换手{leader['turn']}% | <font color='#f0883e'>{seal}</font>"
            ))
            if desc_line:
                blocks.append(div(f"   {desc_line}"))

        # ── 跟涨票一行 ──
        followers = top[1:]
        if followers:
            f_strs = []
            for f in followers:
                f_strs.append(f"{f['name']}+{f['pct']}% 换手{f['turn']}%")
            # 跟涨票：用当前黄色(#f0a020，原龙头金色)做分组强调，与龙头红区分
            blocks.append(div(f"<font color='#f0a020'>   跟涨：{' | '.join(f_strs)}</font>"))

        # ── 联动+扩散一行 ──
        up_s = [s for s in all_s if s["up"]]
        if len(all_s) >= 3:
            diff = "板块共振" if len(up_s) >= 3 else ("扩散初期" if len(up_s) >= 2 else "单点异动")
            blocks.append(div(f"   联动{len(up_s)}只涨停 {diff}"))

        # ── 标签一行 ──
        label_parts = []
        for r in risks:
            for s in all_s:
                if r["stock"].startswith(s["name"]):
                    label_parts.append(f"⚠{s['name'][:6]}{r['risk']}")
                    break
        for s in all_s:
            if s.get("up") and s["code"] in positives:
                tags = ",".join(t["tag"] for t in positives[s["code"]][:2])
                label_parts.append(f"✅{s['name'][:6]}{tags}")
        if label_parts:
            blocks.append(div(f"   {' | '.join(label_parts[:6])}"))

        blocks.append(hr())

    blocks.append(note(f"📡 选股宝+东财 | {now().strftime('%H:%M')}"))
    return blocks

# ── Ebb Tide Detection ─────────────────────────────────

def detect_ebb(sectors, stocks, total_zt) -> Optional[Dict]:
    """检测退潮信号，返回退潮描述或None。

    2026-08-14 灵活化改造（用户定：逻辑不僵化·注重市场情绪与赚钱效应）：
    - 加权评分制：亏钱效应类（核按钮/晋级率/炸板率）权重2，结构类（涨停数/板高/分散）权重1
    - 情绪周期加成：cache/short_term/短期情绪周期判断.md 当日已判"退潮" → +1（周期先行，
      指标滞后时也能提前报退潮）
    - 总分 ≥5 → ⚠ 退潮预警；3-4 → ⚡ 情绪转弱；<3 → 不触发"""
    signals = []   # (score, text)
    cycle_ebb = False

    # 0. 情绪周期先行（盘前/盘后已判退潮 → 加成；周期文件当日有效）
    # ⚠️ 2026-08-17 修复：关键词匹配 "退潮" 太粗糙——文件含「退潮冰点→亢奋反转日」混合表述
    # 也会误触发。改为「周期文件判退潮 AND 情绪分<7」双条件（情绪分终审否决旧周期残留）。
    try:
        cyc = (CACHE_DIR.parent / "short_term" / "短期情绪周期判断.md").read_text(encoding="utf-8")
        if "退潮" in cyc:
            cycle_ebb = True
    except Exception:
        pass

    # 0.5 反馈矩阵亏钱效应（权重2·2026-08-14 新增：涨停热闹≠不退潮）
    # ⚠️ 2026-08-17 v2 口径适配：晋级率<25% 不再一票判退潮（涨停基数膨胀稀释，63→110 翻倍
    # 晋级率仍 23.8% 却是亢奋）——情绪分终审：≥7（偏强/亢奋）时晋级率信号直接跳过，
    # 避免 ebb 与情绪分矛盾（今日实证：feedback 9.0 亢奋 vs ebb 退潮预警）。
    fb_sentiment = None
    try:
        fm = json.loads((CACHE_DIR.parent / "feedback_matrix.json").read_text(encoding="utf-8"))
        if fm.get("error") or fm.get("date") != now().strftime("%Y-%m-%d"):
            raise ValueError("feedback 缺失或非当日")
        nuke = int(fm.get("nuke_count") or 0)
        prom = fm.get("promote_rate")
        brk = fm.get("broken_rate")
        fs = fm.get("sentiment_score")
        if isinstance(fs, (int, float)):
            fb_sentiment = float(fs)
        if nuke >= 10:
            signals.append((2, f"核按钮{nuke}只，高位接力亏钱效应显著"))
        elif nuke >= 6:
            signals.append((1, f"核按钮{nuke}只，亏钱效应抬头"))
        # 晋级率：情绪分终审（≥7 时不判退潮——v2 语义：涨停翻倍+广谱红盘=亢奋，晋级率只作接力质量）
        if fb_sentiment is None or fb_sentiment < 7:
            if isinstance(prom, (int, float)) and prom < 25:
                signals.append((2, f"昨日涨停晋级率{prom}%，接力成功率低"))
            elif isinstance(prom, (int, float)) and prom < 32:
                signals.append((1, f"昨日涨停晋级率{prom}%，接力偏弱"))
        if isinstance(brk, (int, float)) and brk > 30:
            signals.append((2, f"炸板率{brk}%，封板质量差"))
        elif isinstance(brk, (int, float)) and brk > 24:
            signals.append((1, f"炸板率{brk}%，封板转弱"))
    except Exception:
        pass

    # 1. 涨停数偏低（权重1）
    if total_zt <= 8:
        signals.append((1, f"涨停仅{total_zt}只，交投清淡"))
    elif total_zt <= 15:
        signals.append((1, f"涨停{total_zt}只，热度一般"))

    # 2. 龙头高度断档（无3板以上）
    max_board = 0
    max_board_name = ""
    for code, s in stocks.items():
        b = s.get("boards", "")
        if "板" in b:
            try:
                # "3天3板"/"8天5板" → 取"板"前数字（真实连板高度）
                d = int(b.split("天")[-1].replace("板", ""))
                if d > max_board:
                    max_board = d
                    max_board_name = s["name"]
            except: pass

    if max_board <= 2:
        signals.append((1, "连板高度≤2板，龙头缺失"))
    elif max_board <= 3:
        signals.append((1, f"最高仅{max_board}板{max_board_name}，空间未打开"))

    # 3. 主流板块消失（前3板块涨停合计<全市场40%·权重1）
    ranked = sorted(sectors.values(), key=lambda x: -x["zt"])[:3]
    top3_zt = sum(s["zt"] for s in ranked)
    if total_zt > 0 and top3_zt / total_zt < 0.4:
        signals.append((1, "板块分散，无主线方向"))

    # 4. 活跃板块缺热点（权重1）
    cold_sectors = sum(1 for sec in sectors.values() if sec["zt"] >= 2 and sec["hot"] == 0)
    if cold_sectors >= 3:
        signals.append((1, f"{cold_sectors}个活跃板块缺乏热点支撑"))

    # 5. 情绪周期先行加成：当日周期已判退潮 → +1（指标滞后时也提前报退潮）
    # ⚠️ 2026-08-17 v2 适配：情绪分终审否决——≥7（偏强/亢奋）时周期加成不生效
    #（今日实证：周期文件含「退潮冰点→亢奋反转日」混合表述，关键词误触发）
    if cycle_ebb and (fb_sentiment is None or fb_sentiment < 7):
        signals.append((1, "短期情绪周期已判退潮"))

    score = sum(w for w, _ in signals)
    if score >= 5:
        severity = "⚠ 退潮预警"
    elif score >= 3:
        severity = "⚡ 情绪转弱"
    else:
        return None
    return {
        "severity": severity,
        "signals": [t for _, t in signals],
        "max_board": max_board,
        "total_zt": total_zt,
        # P0 情绪印证（2026-08-17）：ebb 与情绪分对照——避免「ebb 退潮 vs 情绪亢奋」矛盾
        # （今日实证：feedback 9.0 亢奋 vs 旧 ebb 退潮预警，已用情绪分终审修复）
        "sentiment_score": fb_sentiment,
        "sentiment_label": "",
    }

def _ebb_escalated(curr, prev):
    """退潮是否值得推送：首次出现 / 级别升级 / 负反馈条数加大。
    平稳（同级且信号数未增）或缓解（当前无）则不推，避免重复打扰。"""
    if not curr:
        return False
    if not prev:
        return True  # 新出现退潮，首告警
    sev = {"情绪转弱": 1, "退潮预警": 2}
    cs = sev.get(curr.get("severity", ""), 0)
    ps = sev.get(prev.get("severity", ""), 0)
    if cs > ps:
        return True  # 级别升级
    if cs == ps and len(curr.get("signals", [])) > len(prev.get("signals", [])):
        return True  # 同级但负反馈条数加大
    return False


# ── 退潮卡时段限频（2026-08-14 用户：退潮卡早上/中午/下午各来一张）────

def _ebb_slot() -> str:
    """当前时段桶：morning(09:25-11:30) / midday(13:00-13:59) / afternoon(14:00-15:59) / none(非交易时段)。"""
    hm = now().strftime("%H:%M")
    if "09:25" <= hm <= "11:30":
        return "morning"
    if "13:00" <= hm <= "13:59":
        return "midday"
    if "14:00" <= hm <= "15:59":
        return "afternoon"
    return "none"


def _ebb_state() -> dict:
    p = CACHE_DIR / "ebb_push_state.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ebb_allowed(slot: str, escalated: bool) -> bool:
    """时段限频：slot 当日未推过 → 允许（保证早/中/下午各一张）；
    已推过但退潮加剧（升级/负反馈加大）→ 允许；否则跳过。"""
    if slot == "none":
        return escalated
    st = _ebb_state()
    pushed = st.get(today_c(), {}).get(slot)
    return (not pushed) or escalated


def _ebb_mark_pushed(slot: str) -> None:
    if slot == "none":
        return
    p = CACHE_DIR / "ebb_push_state.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        st = {}
    st.setdefault(today_c(), {})[slot] = now().strftime("%H:%M")
    try:
        p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def build_ebb_card(ebb: Dict) -> List[Dict]:
    """构建退潮预警卡片（浅绿=风险预警，与跌绿区分）"""
    blocks = []

    severity = ebb["severity"]
    color_intensity = "severe" if "退潮" in severity else "moderate"

    blocks.append(div(
        f"**{severity}** | 涨停{ebb['total_zt']}只 | 最高{ebb['max_board']}板"
    ))
    blocks.append(hr())

    for sig in ebb["signals"]:
        blocks.append(div(f"• {sig}"))

    blocks.append(hr())
    advice = []
    if "⚠ 退潮" in severity:
        advice = ["❌ 严格控制仓位，减少开仓", "❌ 回避高位接力，慎打板", "✅ 等待冰点后的修复信号再出手"]
    else:
        advice = ["⚠ 降低仓位至轻仓", "→ 只做确定性机会，宁可错过", "→ 关注低位首板试错"]

    blocks.append(div("**操作建议**\n" + "\n".join(advice)))
    blocks.append(hr())
    blocks.append(note(f"📡 选股宝情绪检测 | {now().strftime('%H:%M')}"))

    return blocks

# ── Build Post-Market Card ────────────────────────────

def build_post_card(sectors, plates, diagnoses, total_zt, risks, tomorrow):
    """🔒 已退役·仅 debug 工具（2026-09-03 自查）：当日盘面总结=盘后复盘卡 17:18 的子集，
    禁止作生产推送（复盘由 automation-1785767491575 LLM 长卡承担）。保留供本地预览/排错。

    Build structured closing summary card."""
    blocks = []

    # Rank sectors by zt count
    ranked = sorted(sectors.items(), key=lambda x: (-x[1]["zt"], -x[1]["hot"]))[:6]

    blocks.append(div(f"**📊 收盘总览 | 涨停{total_zt}只**"))
    blocks.append(hr())

    # Sector ranking
    blocks.append(div("**━━━ 板块强度排名 ━━━**"))
    for pid, sec in ranked:
        pname = sec["name"]
        zt = sec["zt"]
        turn_avg = sec.get("turn_avg", 0)
        diag = diagnoses.get(pid, {})
        dscore = diag.get("score", 0)
        grade = "S" if zt>=8 else "A" if zt>=5 else "B" if zt>=3 else "C"
        blocks.append(div(
            f"**{grade}级 {pname}** | 涨停{zt}只 | 换手均值{turn_avg}%"
            + (f" | 龙头分{dscore}/10" if dscore else "")
        ))
        if sec.get("desc"):
            blocks.append(div(f"  → {sec['desc'][:50]}"))

    # Top dragons
    blocks.append(hr())
    blocks.append(div("**━━━ 今日龙头扫描 ━━━**"))
    shown = 0
    for pid, diag in sorted(diagnoses.items(), key=lambda x: x[1]["score"], reverse=True):
        if shown >= 3: break
        c = diag["candidate"]
        seal = seal_quality(c)
        blocks.append(div(
            f"**{c['name']}** {c['code'][:6]} | +{c['pct']}% | {c.get('boards','') or '首板'} | {seal}"
        ))
        blocks.append(div(
            f"  综合{diag['score']}/10 | 带领{diag['leadership']} 突破{diag['breakthrough']} 唯一{diag['uniqueness']}"
        ))
        shown += 1

    # Tomorrow themes
    if tomorrow:
        blocks.append(hr())
        blocks.append(div("**━━━ 盘前预期 vs 收盘 ━━━**"))
        for t in tomorrow[:4]:
            matched = "✓" if any(t["name"] in s.get("name","") for _,s in ranked) else "→"
            line = f"{matched} {t['name']} | 预期涨停{t['zt']}只"
            stks = t.get("stocks") or []
            if stks:
                # 龙头：名字+代码(+涨幅)整段标红，去掉🐉；非龙头保持默认
                parts = []
                for s in stks[:3]:
                    code = s.get("code", "")[:6]
                    if s.get("dragon"):
                        nm = f"<font color='red'>{s['name']}({code} +{s['pct']}%)</font>"
                    else:
                        nm = f"{s['name']}({code} +{s['pct']}%)"
                    parts.append(nm)
                top = "、".join(parts)
                line += f"\n   核心股：{top}"
            blocks.append(div(line))

    # Footer
    blocks.append(hr())
    blocks.append(note(f"📡 选股宝+东财 | {now().strftime('%H:%M')} | 数据基于最新接口快照"))

    return blocks

# ── Main ──────────────────────────────────────────────

def _ingest_stock_registry(stocks, pre_stocks, rising, snapshot=None):
    """把本轮盘中命中/接触到的个股沉淀进统一个股特征底库。

    ⚠️ 口径：入库门槛 = **进入系统可见范围**，与今日表现无关（不是只存涨停股）。
    ⚠️ 非致命：底库异常绝不中断快照与推送；受 `STOCK_REGISTRY_INTRADAY` 开关控制。
    """
    try:
        from common import stock_registry as registry

        if not registry.intraday_enabled():
            return
        stamp = now().isoformat(timespec="seconds")
        result = registry.ingest_intraday(
            trade_date=now().strftime("%Y-%m-%d"), asof_ts=stamp,
            stocks=stocks, pre_stocks=pre_stocks, rising=rising)
        if result.get("ok"):
            print(f"  个股底库: 新增{result.get('inserted', 0)} "
                  f"更新{result.get('updated', 0)} 无变化{result.get('unchanged', 0)}")
        elif result.get("reason"):
            print(f"  个股底库跳过: {result['reason']}")
        # latest.json 已在调用方落盘；用同一快照补充选股宝题材/板块标签。
        if isinstance(snapshot, dict):
            xgb = registry.ingest_xgb_snapshot(snapshot)
            if xgb.get("ok"):
                print(f"  选股宝题材: 新增{xgb.get('inserted', 0)} "
                      f"更新{xgb.get('updated', 0)} 无变化{xgb.get('unchanged', 0)}")
    except Exception as e:  # noqa: BLE001 —— 底库失败不影响主链
        print(f"  ⚠️ 个股底库更新失败(不影响主流程): {e}")


def run_mode(mode, dry_run=False, json_only=False):
    print(f"\n{'='*60}")
    print(f"  题材脉冲系统 v2 | {mode} | {'DRY-RUN' if dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    # Fetch
    print("[1] 抓取数据...")
    f = Fetcher()
    raw = f.fetch_all()
    for e in f.errors: print(f"  ⚠ {e}")

    # Parse
    stocks = parse_stocks(raw)
    plates = parse_plates(raw)
    pre_raw = raw.get("pre")
    pre_stocks = parse_stocks({"stocks": pre_raw}) if pre_raw and pre_raw.get("data") else {}
    sectors = build_sectors(stocks, plates)
    newsflash = parse_newsflash(raw)
    # ⛔ 2026-09-07 修复 + 时效门：latest.json 快讯是数据层（供 live_signal 快讯卡/猎手卡/AI 读取），
    #    ①不得 dedup 到 0（原逻辑把「本轮见到」当「已推」，30s 高频+窗口重叠 → 常年空，实证全天快讯0条）；
    #    ②必须只留最近 600s 的当下快讯——baoer 返回 3h 消息池且顺序≠时间序（实证 55min 前传媒排前），
    #    无时间门会把几十分钟前的旧快讯当「新」写入 → 下游推过时内容（实证 09-07 推了 55min 前传媒涨停）。
    def _nfts(m):
        ca = m.get("ctime") or m.get("created_at")   # parse_newsflash 输出字段=ctime；原始消息=created_at
        try:
            if isinstance(ca, (int, float)):
                return float(ca if ca < 1e12 else ca / 1000)
            from datetime import datetime as _dt
            s = str(ca).strip()
            return (_dt.fromisoformat(s.replace("Z", "+00:00")).timestamp()
                    if "T" in s else _dt.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp())
        except Exception:
            return 0.0
    _nowts = time.time()
    newsflash = [m for m in (newsflash or [])
                 if _nfts(m) > 0 and 0 <= _nowts - _nfts(m) <= 600]
    newsflash = sorted(newsflash, key=_nfts, reverse=True)[:8]
    tomorrow = parse_tomorrow(raw)
    # 题材成分股二级联动：把 getStockList 结果挂到对应题材
    theme_stocks_raw = raw.get("theme_stocks") or {}
    theme_stocks_map = {c: parse_theme_stocks(r) for c, r in theme_stocks_raw.items()}
    for t in tomorrow:
        tc = t.get("themeCode")
        if tc and tc in theme_stocks_map:
            t["stocks"] = theme_stocks_map[tc]
    # 用 stock_data 的 limit_up_days 标注涨停天数（区分真拉升 vs 炸板回落）
    sd = raw.get("stock_data")
    if sd and sd.get("data"):
        for sym, d in sd["data"].items():
            days = d.get("limit_up_days") or 0
            if sym in stocks: stocks[sym]["limit_up_days"] = days
            if sym in pre_stocks: pre_stocks[sym]["limit_up_days"] = days
    # 提前埋伏信号（涨停前可买窗口）
    early = build_early_signals(pre_stocks, plates, sectors, newsflash)
    # 东财涨幅榜兜底（2026-08-18：选股宝异动池漏票补齐，如中大力德）
    rising = parse_em_rising(raw.get("rising_em"))
    # 题材领涨龙（每个热门题材涨最好的个股；涨幅榜兜底由 live_signal 扫 latest.json["rising"]）
    leaders = build_leaders(stocks, pre_stocks, plates)
    hot_spots = build_hot_spots(stocks)              # 官方领涨个股（选股宝 hot_spot=1）
    index = parse_index(raw)
    em_market = fetch_market(raw)      # 大盘成交额 + 涨跌家数（东财优先→腾讯兜底·2026-08-25）
    # 成交额挂到 index[0]（上证）供下游展示/情绪分消费
    if em_market and index:
        index[0]["amount_yi"] = em_market.get("total_amount_yi")
    ld_pool = parse_pool(raw, "ld_pool")
    broken_pool = parse_pool(raw, "broken_pool")
    ladder = parse_ladder(raw)
    total_zt = sum(1 for s in stocks.values() if s["up"])
    total_market_zt = sum(sec["zt"] for sec in sectors.values())
    idx_txt = " ".join(f"{i['name']}{i['pct']:+}%" for i in index) or "指数缺失"
    print(f"  异动{len(stocks)}只 | 涨停{total_zt}只 | 板块{len(sectors)}个 | 快讯{len(newsflash)}条")
    print(f"  {idx_txt} | 跌停{ld_pool['count']} | 炸板{broken_pool['count']} | 天梯{len(ladder)}层")

    # Diagnose（含**当日龙头锁定**：确立后只允许加固，⛔ 换人只发生在原龙头退位时）
    # ⚠️ 锁按日隔离（文件名带日期）；读/写失败一律降级为空锁 / 只告警，**不中断快照与推送**。
    locks = load_dragon_locks() if ENABLE_DRAGON_LOCK else {}
    now_hm = now().strftime("%H:%M")
    diagnoses = {}
    for pid, sec in sectors.items():
        sname = sec.get("name", "")
        prev_lock = locks.get(sname)
        d = diagnose_dragon(sec["stocks"], total_market_zt, locked=prev_lock)
        if not d:
            continue                # 该板块本轮无封板票 → 保留原锁不动（等它下一轮回来）
        diagnoses[pid] = d
        c = d.get("candidate") or {}
        if c.get("code"):
            locks[sname] = update_lock(prev_lock, c, now_hm)
    if ENABLE_DRAGON_LOCK:
        save_dragon_locks(locks)

    # Risks & positives
    risks = scan_risks(raw.get("labels"), stocks)
    positives = scan_positives(raw.get("labels"), stocks)
    print(f"  风险{len(risks)}个 | 正向标签{len(positives)}只")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = CACHE_DIR / "latest.json"
    prev_path = CACHE_DIR / "prev.json"
    # 读上一次快照，用于板块增量对比（走统一容错读，坏文件不中断主流程）
    prev_data = _load_json(prev_path)
    limit_up_stocks = build_limit_up_stocks(stocks, total_zt)
    summary = build_summary_json(sectors, plates, diagnoses, total_market_zt, total_zt, risks, positives, stocks, newsflash, tomorrow,
                                 index=index, ld_pool=ld_pool, broken_pool=broken_pool, ladder=ladder, early=early, leaders=leaders, hot_spots=hot_spots, prev_data=prev_data, em_market=em_market, rising=rising,
                                 limit_up_stocks=limit_up_stocks)
    # Shift: latest → prev（先原子落 latest 到临时副本再替换 prev，避免 prev 读到半截文件）
    if latest_path.exists():
        _save_json(prev_path, _load_json(latest_path) or {})
    if not _save_json(latest_path, summary):
        # 原子写失败时降级为直接写，保证链路不断（并在日志留痕）
        print("  ⚠️ latest.json 原子写失败，降级为直接写")
        latest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"  摘要JSON: {latest_path} (prev: {'yes' if prev_path.exists() else 'no'})")

    # 个股特征底库：命中即增量更新（未命中的票原样保留）
    # ⚠️ 非致命 —— 底库异常不得中断快照与推送；受 STOCK_REGISTRY_INTRADAY 开关控制
    _ingest_stock_registry(stocks, pre_stocks, rising, snapshot=summary)

    if dry_run or json_only:
        print("\n  --- JSON ONLY (no push) ---")
        return

    if mode == "intra":
        blocks = build_top5_card(sectors, plates, diagnoses, total_market_zt, risks, positives)
        title = f"盘中脉搏·增量 {now().strftime('%H:%M')} | 涨停{total_zt}只 | TOP5板块"
        color = "red"
        ebb = detect_ebb(sectors, stocks, total_market_zt)

    elif mode == "post":
        blocks = build_post_card(sectors, plates, diagnoses, total_market_zt, risks, tomorrow)
        title = f"📊 收盘总结·全量 {now().strftime('%H:%M')} | 涨停{total_zt}只"
        color = "blue"
        ebb = None

    else:
        blocks = [div("未知模式")]
        title, color, ebb = "", "blue", None

    # Print preview
    print(f"\n[推送预览] {title}")
    for b in blocks[:3]:
        t = b.get("tag","")
        if t == "div":
            txt = b.get("text",{}).get("content","") or b.get("fields",[])
            print(f"  {str(txt)[:100]}")
        elif t == "column_set":
            print(f"  [三列数据]")
        elif t == "hr":
            print(f"  ---")

    if dry_run:
        print("\n  --- DRY RUN ---")
        # Also show ebb
        if mode == "intra":
            ebb = detect_ebb(sectors, stocks, total_market_zt)
            if ebb:
                print(f"\n  [退潮检测] {ebb['severity']}")
                for sig in ebb["signals"]: print(f"    {sig}")
        return

    ok = push_card(title, blocks, color)
    print(f"\n  TOP5板块推送: {'✓' if ok else '✗'}")

    # 退潮浅绿卡片（2026-08-14 改·时段限频：早/中/下午各最多1张，加剧随时推）
    if mode == "intra":
        ebb = detect_ebb(sectors, stocks, total_market_zt)
        prev_ebb = (prev_data or {}).get("ebb")
        escalated = _ebb_escalated(ebb, prev_ebb) if ebb else False
        slot = _ebb_slot()
        if ebb and _ebb_allowed(slot, escalated):
            tag = "🔥加剧 " if (escalated and prev_ebb) else "⚠新 "
            ebb_blocks = build_ebb_card(ebb)
            ebb_ok = push_card(f"{tag}{ebb['severity']}·增量 {now().strftime('%H:%M')}", ebb_blocks, "turquoise")
            _ebb_mark_pushed(slot)
            print(f"  退潮预警推送({slot}·{'加剧' if escalated and prev_ebb else '新出现'}): {'✓' if ebb_ok else '✗'}")
        elif ebb:
            print(f"  退潮{slot}时段已推过且未加剧，跳过（上次severity={prev_ebb.get('severity') if prev_ebb else '-'}）")

    # Save snapshot
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sp = CACHE_DIR / f"{today_c()}_{now().strftime('%H%M')}_{mode}.json"
    sp.write_text(json.dumps({"mode": mode, "ts": now().isoformat(),
        "total_zt": total_zt, "sectors": len(sectors),
        "diagnoses": {str(k): {"name": v["candidate"]["name"], "score": v["score"]} for k,v in diagnoses.items()},
        "risks": risks[:5],
    }, ensure_ascii=False, indent=2))
    print(f"  快照: {sp}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["intra","post"], default="intra")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json-only", action="store_true", help="仅输出JSON不推送")
    p.add_argument("--force", action="store_true", help="强制重抓，忽略缓存短路（守护进程使用）")
    p.add_argument("--asof", default=None, help="流程触发时间 HH:MM，守卫按此判定交易时段（而非当前时间）")
    a = p.parse_args()

    # 交易时段守卫：非交易时段(周末/盘前/午休/盘后)直接退出，不产出数据
    if a.mode == "intra" and not in_trading(a.asof):
        print("非交易时段，跳过盘中脉冲推送")
        sys.exit(0)

    # 缓存新鲜度短路（P1 守护配套）：AI 任务不带 --force 时，若 latest.json 仍新鲜则跳过全量重抓
    if not a.force and a.mode == "intra":
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        lp = CACHE_DIR / "latest.json"
        if lp.exists():
            age = (now() - datetime.fromtimestamp(lp.stat().st_mtime, BJ_TZ)).total_seconds()
            if age < CACHE_TTL and in_trading(a.asof):
                print(f"缓存新鲜（{age:.0f}s < {CACHE_TTL}s），跳过重抓，AI 直接读 latest.json")
                sys.exit(0)

    run_mode(a.mode, a.dry_run, a.json_only)

if __name__ == "__main__":
    main()
