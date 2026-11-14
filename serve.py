#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serve.py —— 狸猫猎手 对外只读 Web 服务（白名单制）

职责（v3 收敛版 L1，零重构，仅新增此文件）：
  托管终端 React 应用为 http(s)://，且**只暴露该暴露的**。

  · 静态白名单：托管 狸猫终端 dist + SPA fallback
  · API 白名单：只读转发 routes.ts 的业务数据子集
      ✅ 2026-10-26 起按用户要求放开 /api/ai/* /api/decision/* /api/account/*
         （持链接即可查看全部业务页面；回退开关 SHARE_PRIVATE=0）
      ⛔ 但凭据类字段一律脱敏：webhook / token / secret / password / api_key 等
      ⛔ 且凭据备份文件永不入白名单（如 settings.json.bak_*）
  · 公网鉴权：SHARE_TOKEN 环境变量（设置后所有请求必须带 ?t= 或 Authorization: Bearer）
  · 访问日志：cache/access_log.jsonl
  · 安全：路径遍历拦截、目录不列举、其余一律 404；Cache-Control: no-store

数据契约：调用 scripts/lib/read-route.mjs 的同语义实现（read_route），
前端 dev 插件与本项目共用同一份契约，故生产环境返回形态必须一致。

用法：
  python3 serve.py                                # 默认 127.0.0.1:8000，无鉴权（仅本机）
  SHARE_TOKEN=xxxx SHARE_HOST=0.0.0.0 python3 serve.py   # 对外分享：必须带 token
  SHARE_PORT=9000 SHARE_TOKEN=xxx SHARE_HOST=0.0.0.0 python3 serve.py
  ⛔ 对外监听（非 loopback）未设 SHARE_TOKEN 时**直接拒绝启动**，不再只打印警告。

迁移服务器：用环境变量 LIMAO_DATA_ROOT 覆写 cache 根；LIMAO_DIST_DIR 覆写前端构建目录。
"""

import os
import re
import json
import sys
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ────────────────────────────────────────────────────────────────
# 路径与配置
# ────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# 数据基座单一登记处（工件 E）：路由/治理元数据单一真源
from common import data_catalog

# 严格过期模式（可逆开关）：数据超 stale 阈值直接 404（默认关闭=温和标注）
_STRICT_STALE = os.getenv("SERVE_STRICT_STALE", "") == "1"

PROJECT_ROOT = HERE  # 狸猫猎手 根目录（routes.ts 的 file 路径以 cache/ 开头，故 DATA_ROOT 指向根）
DATA_ROOT = os.environ.get("LIMAO_DATA_ROOT") or PROJECT_ROOT
DIST_DIR = os.environ.get("LIMAO_DIST_DIR") or os.path.normpath(
    os.path.join(PROJECT_ROOT, "..", "狸猫终端", "dist")
)
CACHE_DIR = os.path.join(DATA_ROOT, "cache")

SHARE_HOST = os.environ.get("SHARE_HOST", "127.0.0.1")  # 默认只监听本机
SHARE_PORT = int(os.environ.get("SHARE_PORT", "8000"))
SHARE_TOKEN = os.environ.get("SHARE_TOKEN") or ""  # 空 = 不鉴权（仅允许本机监听）
# 2026-10-26 用户要求「持链接即可访问全部业务页面」 ⇒ 默认放开 ai/decision/account。
# 回退：SHARE_PRIVATE=0 拒绝私有业务路由（含聚合快照），其余业务路由照常。
SHARE_PRIVATE = (os.environ.get("SHARE_PRIVATE") or "1").strip().lower() not in {"0", "false", "no", "off"}
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

ACCESS_LOG = os.path.join(CACHE_DIR, "access_log.jsonl")
ALLOWED_ROOTS = [os.path.realpath(p) for p in (DIST_DIR, CACHE_DIR)]

# ────────────────────────────────────────────────────────────────
# 路由语义（复刻 scripts/lib/read-route.mjs）
# ────────────────────────────────────────────────────────────────
JSONL_DEFAULT_TAIL = 500


def safe_listdir(abs_dir):
    try:
        return os.listdir(abs_dir)
    except OSError:
        return None


def parse_jsonl(raw, lines=None):
    rows = raw.split("\n")
    tail = rows[-lines:] if (lines and lines > 0) else rows[-JSONL_DEFAULT_TAIL:]
    out = []
    for line in tail:
        t = line.strip()
        if not t:
            continue
        try:
            out.append(json.loads(t))
        except Exception:
            pass  # 半行（写入中）跳过
    return out


def read_route(data_root, defn):
    # 形态③：两层目录 <dir>/<dirPattern>/<file>
    if defn.get("dir") and defn.get("dirPattern") and defn.get("file"):
        base = os.path.join(data_root, defn["dir"])
        names = safe_listdir(base)
        if names is None:
            return [] if defn.get("mode") == "all" else None
        dre = re.compile(defn["dirPattern"])
        dirs = sorted(
            n for n in names if dre.match(n) and os.path.isdir(os.path.join(base, n))
        )

        def read_one(d):
            rel = f"{d}/{defn['file']}"
            try:
                with open(os.path.join(base, d, defn["file"]), encoding="utf-8") as fh:
                    return {"file": rel, "data": json.load(fh)}
            except Exception:
                return {"file": rel, "data": None}

        if defn.get("mode") == "all":
            sel = dirs[-defn["limit"] :] if defn.get("limit") else dirs
            return [read_one(d) for d in sel]
        last = dirs[-1] if dirs else None
        return read_one(last)["data"] if last else None

    # 形态①：单文件
    if defn.get("file"):
        with open(os.path.join(data_root, defn["file"]), encoding="utf-8") as fh:
            raw = fh.read()
        if defn.get("format") == "jsonl":
            return parse_jsonl(raw, defn.get("lines"))
        if defn.get("format") == "text":
            return raw
        return json.loads(raw)

    # 形态②：目录 + pattern
    if defn.get("dir") and defn.get("pattern"):
        d = os.path.join(data_root, defn["dir"])
        names = safe_listdir(d)
        if names is None:
            return [] if defn.get("mode") == "all" else None
        re_p = re.compile(defn["pattern"])
        files = sorted(f for f in names if re_p.match(f))

        def read_one(f):
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                raw = fh.read()
            if defn.get("format") == "jsonl":
                return parse_jsonl(raw, defn.get("lines"))
            if defn.get("format") == "text":
                return raw
            return json.loads(raw)

        if defn.get("mode") == "all":
            sel = files[-defn["limit"] :] if defn.get("limit") else files
            out = []
            for f in sel:
                try:
                    out.append({"file": f, "data": read_one(f)})
                except Exception:
                    out.append({"file": f, "data": None})
            return out
        last = files[-1] if files else None
        return read_one(last) if last else None

    return None


# ────────────────────────────────────────────────────────────────
# 数据基座增强（工件 C）：投影契约 + 新鲜度标注
# ────────────────────────────────────────────────────────────────
def route_files(data_root, defn):
    """按 read_route 的排序、模式和 limit 选择实际产物，不以目录代替文件。"""
    if defn.get("dir") and defn.get("dirPattern") and defn.get("file"):
        base = os.path.join(data_root, defn["dir"])
        rx = re.compile(defn["dirPattern"])
        names = sorted(n for n in (safe_listdir(base) or [])
                       if rx.match(n) and os.path.isdir(os.path.join(base, n)))
        paths = [os.path.join(base, n, defn["file"]) for n in names]
    elif defn.get("file"):
        return [os.path.join(data_root, defn["file"])]
    elif defn.get("dir") and defn.get("pattern"):
        base = os.path.join(data_root, defn["dir"])
        rx = re.compile(defn["pattern"])
        paths = [os.path.join(base, n) for n in sorted(safe_listdir(base) or [])
                 if rx.match(n)]
    else:
        return []
    if defn.get("mode") == "all":
        return paths[-defn["limit"]:] if defn.get("limit") else paths
    return paths[-1:]


def route_freshness(route, defn):
    """最新一期返回产物的新鲜度（all 模式按文件名排序的最后一期）。
    缺失时 mtime/age/stale 均为 None；不回退到旧文件或目录 mtime。
    """
    meta = data_catalog.meta_for(route) or {}
    fresh = {"exists": False, "mtime": None, "age_sec": None, "stale": None,
             "retired": bool(meta.get("retired")), "producer": meta.get("producer"),
             "stale_sec": meta.get("stale_sec")}
    paths = route_files(DATA_ROOT, defn)
    if not paths or not os.path.isfile(paths[-1]):
        return fresh
    try:
        st = os.stat(paths[-1])
    except OSError:
        return fresh
    age = int(time.time() - st.st_mtime)
    fresh.update(exists=True, mtime=st.st_mtime, age_sec=age,
                 stale=None if fresh["stale_sec"] is None else age > fresh["stale_sec"])
    return fresh


def freshness_headers(fresh):
    """未知不伪装为新鲜；元数据只走响应头，不改业务数据形态。"""
    return {
        "X-Data-Age": str(fresh["age_sec"]) if fresh["age_sec"] is not None else "unknown",
        "X-Data-Stale": "unknown" if fresh["stale"] is None else str(int(fresh["stale"])),
        "X-Data-Exists": str(int(fresh["exists"])),
        "X-Data-Mtime": str(fresh["mtime"]) if fresh["mtime"] is not None else "unknown",
        "X-Data-Stale-Sec": str(fresh["stale_sec"]) if fresh["stale_sec"] is not None else "unknown",
        "X-Data-Retired": str(int(fresh["retired"])),
        "X-Data-Producer": str(fresh["producer"] or ""),
    }


def project_fields(payload, fields):
    """字段投影（?fields=a,b,c）：仅保留白名单字段。非 dict/list 原样返回。"""
    if not fields:
        return payload
    fset = set(fields)
    if isinstance(payload, dict):
        return {k: v for k, v in payload.items() if k in fset}
    if isinstance(payload, list):
        return [
            {k: v for k, v in it.items() if k in fset} if isinstance(it, dict) else it
            for it in payload
        ]
    return payload


def paginate_list(payload, cursor, limit):
    """分页游标（?cursor=N&limit=M）：对 list 结果再切片。非 list 原样返回。"""
    if not isinstance(payload, list):
        return payload
    start = cursor if (cursor and cursor > 0) else 0
    if limit and limit > 0:
        return payload[start:start + limit]
    return payload[start:]


CODE_RE = re.compile(r"^\d{6}$")
MARKET_DAILY_LIMIT_MAX = 20


def read_data_query(path, query):
    """数据底座的受限查询边界；绝不开放SQL、路径或数据库文件。"""
    if path not in {"/api/data/stock-registry", "/api/data/market-daily",
                    "/api/data/stock-profile"}:
        return None

    raw_code = (query.get("code") or [""])[0]
    if not CODE_RE.fullmatch(raw_code):
        return 400, {"error": "invalid_request", "route": path, "message": "code 必须为 6 位数字"}

    if path == "/api/data/stock-registry":
        if set(query) != {"code"}:
            return 400, {"error": "invalid_request", "route": path, "message": "stock-registry 仅接受 code 参数"}
        from common import data_gateway
        return 200, data_gateway.stock_registry(raw_code)

    if path == "/api/data/stock-profile":
        if set(query) != {"code"}:
            return 400, {"error": "invalid_request", "route": path, "message": "stock-profile 仅接受 code 参数"}
        from common import data_gateway
        stock_profile = getattr(data_gateway, "stock_profile", None)
        if not callable(stock_profile):
            return 503, {"error": "data_unavailable", "route": path,
                         "message": "stock-profile 查询能力暂不可用"}
        return 200, stock_profile(raw_code)

    if not set(query).issubset({"code", "limit"}):
        return 400, {"error": "invalid_request", "route": path, "message": "market-daily 仅接受 code 与 limit 参数"}
    raw_limit = (query.get("limit") or [None])[0]
    try:
        limit = MARKET_DAILY_LIMIT_MAX if raw_limit in (None, "") else int(raw_limit)
    except (TypeError, ValueError):
        limit = 0
    if not 1 <= limit <= MARKET_DAILY_LIMIT_MAX:
        return 400, {"error": "invalid_request", "route": path,
                     "message": f"limit 必须为 1 到 {MARKET_DAILY_LIMIT_MAX} 的整数"}
    from common import data_gateway
    return 200, data_gateway.market_daily(raw_code, limit)


def health_payload(data_root, file_routes):
    now = time.time()
    files = []
    for route, defn in file_routes:
        target = defn.get("file") or defn.get("dir")
        abs_p = os.path.join(data_root, target)
        probe = abs_p
        if not defn.get("file") and defn.get("dir"):
            names = safe_listdir(abs_p) or []
            if names:
                probe = os.path.join(abs_p, sorted(names)[-1])
        try:
            st = os.stat(probe)
            files.append(
                {
                    "route": route,
                    "file": target,
                    "desc": defn.get("desc"),
                    "retired": bool(defn.get("retired")),
                    "exists": True,
                    "mtime": datetime.fromtimestamp(
                        st.st_mtime, timezone.utc
                    ).isoformat(),
                    "age_sec": int(now - st.st_mtime),
                    "size": st.st_size,
                }
            )
        except OSError:
            files.append(
                {
                    "route": route,
                    "file": target,
                    "desc": defn.get("desc"),
                    "retired": bool(defn.get("retired")),
                    "exists": False,
                    "mtime": None,
                    "age_sec": None,
                    "size": 0,
                }
            )
    return {
        "data_root": data_root,
        "ts": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


# ────────────────────────────────────────────────────────────────
# 公开 API 白名单（routes.ts 子集，排除 ai/decision/account）
# ────────────────────────────────────────────────────────────────
PUBLIC_ROUTES = {
    # 一、行情主链路
    "/api/market/latest": {"file": "cache/pulse_snapshots/latest.json", "cadence": "intraday", "desc": "主快照"},
    "/api/market/prev": {"file": "cache/pulse_snapshots/prev.json", "cadence": "intraday", "desc": "上一版快照"},
    # ⚠️ 2026-10-14 审计（A3）：全仓**无任何写入方**产出 `auction_live.json`
    #    （最后内容 2026-09-25 15:30 · 46 B · 是旧看板时代的遗留件）。真实竞价数据在
    #    `wealth_code/cache/auction_quotes-<date>.json`（09:25 落盘），已由
    #    `/api/decision/auction-history` 暴露。
    #    故：cadence intraday→manual（**停止 30s 轮询一个死文件**）+ retired 标记，
    #    前端按「已停更」降级渲染，不把 19 天前的报价当今日。
    "/api/market/auction": {"file": "cache/pulse_snapshots/auction_live.json", "cadence": "manual",
                            "retired": True,
                            "desc": "竞价实时报价（⚠️ 已停更·无写入方·真实竞价见 /api/decision/auction-history）"},
    "/api/market/signals": {"file": "cache/short_spirit_signals.json", "cadence": "intraday", "desc": "短线精灵信号"},
    "/api/market/relay-effect": {"file": "cache/relay_effect.json", "cadence": "intraday", "desc": "连板量能与承接观察"},
    "/api/market/feedback": {"file": "cache/feedback_matrix.json", "cadence": "intraday", "desc": "涨停反馈矩阵"},
    "/api/review/panel": {"file": "cache/review_panel.json", "cadence": "daily",
                          "desc": "复盘三块面板（风险/赚钱效应/主线+LLM点评）"},
    "/api/market/candidates": {"file": "cache/pulse_snapshots/candidates.json", "cadence": "intraday", "desc": "候选池"},
    # 二、题材 / 资金
    "/api/theme/rotation": {"file": "cache/theme_rotation.json", "cadence": "intraday", "desc": "题材轮动"},
    "/api/theme/series": {"file": "cache/theme_series.json", "cadence": "daily", "desc": "题材多日序列"},
    # ⚠️ 2026-10-14 审计（A7）：最后写入 2026-09-03（0 B），已被 `/api/theme/series`
    #    （theme_series.json · 当日仍在更新）取代。刻意不删（保留 /record 新鲜度证据），
    #    但 cadence daily→manual + retired 标记，明确它是留档而非「日频在产」。
    "/api/theme/sequence": {"file": "cache/theme_sequence.json", "cadence": "manual",
                            "retired": True,
                            "desc": "题材序列（⚠️ 已停更·已被 theme/series 取代·仅留档）"},
    "/api/flow/history": {"dir": "cache/sector_flow_history", "pattern": r"^sector_flow_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "cadence": "daily", "desc": "板块资金历史"},
    # 三、板块核心票
    # ⚠️ 2026-11-04 维度审计：cache/board_core/ 全部停在 2026-09-09 15:46（近 2 月无写入方）。
    #    与 /api/market/auction 同处置：retired + cadence manual，明确它是留档而非日频在产。
    #    ⛔ 只标注不删脚本/产物；恢复日常产出后把 retired/cadence 改回即可。
    "/api/board/core": {"dir": "cache/board_core", "pattern": r"^board_core_.+\.json$", "mode": "all", "cadence": "manual", "retired": True, "desc": "板块核心票（⚠️ 已停更·停在 09-09·仅留档）"},
    # 三·二、连板日观察（scripts/board_leader_daily.py · 2026-10-27）
    #   ⚠️ 与 routes.ts 对齐：原系统已产出 cache/board_leader_daily/leader_<date>.json，
    #   LeaderDailyPage 会请求本路由；此前漏登导致 prod 404（dev 正常）——2026-10-27 补登。
    "/api/board/leader-daily": {"dir": "cache/board_leader_daily", "pattern": r"^leader_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 30, "cadence": "daily", "desc": "连板日观察（板块×交易日：龙一/板数/换人/熄火 + 昨日龙一今日状态）"},
    # 四、连板晋级量化
    "/api/promotion/summary": {"file": "cache/promo/promo_summary.json", "cadence": "daily", "desc": "晋级率汇总"},
    "/api/promotion/daily": {"dir": "cache/promo", "pattern": r"^promo_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "每日晋级事件"},
    # 五、股性基因
    "/api/gene/summary": {"file": "cache/zt_genes/summary.json", "cadence": "daily", "desc": "涨停池股性榜"},
    "/api/gene/detail": {"dir": "cache/zt_genes", "pattern": r"^gene_\d{6}\.json$", "mode": "all", "cadence": "daily", "desc": "个股股性档案"},
    "/api/gene/hist": {"dir": "cache/zt_genes", "pattern": r"^hist_\d{6}\.json$", "mode": "all", "cadence": "daily", "desc": "个股历史基因"},
    "/api/gene/pool": {"dir": "cache/zt_genes", "pattern": r"^pool_\d{8}\.json$", "mode": "latest", "cadence": "daily", "desc": "涨停基因池"},
    "/api/dragon/gene": {"file": "cache/dragon_gene.json", "cadence": "daily", "desc": "龙头基因沉淀池（≥4板入池 · 5板强票 · 断板两日跌幅≤−10% A杀淘汰）"},
    "/api/rules": {"file": "config/rules_registry.json", "cadence": "daily", "desc": "判据登记表（准入闸/排序/仓位/数据语义/展示口径，含开关与回退）"},
    "/api/rules/fired": {"dir": "cache", "pattern": r"^rules_fired_\d{4}-\d{2}-\d{2}\.json$", "mode": "latest", "cadence": "daily", "desc": "判据命中日报（每条判据今日命中数/零命中候选/三态分布）"},
    "/api/ai/tilt": {"file": "config/ai_tilt_state.json", "cadence": "daily", "desc": "智能层状态（模型版本/特征维度/验证AUC/迭代记录·只读）"},
    # 六、情绪周期
    "/api/emotion/summary5d": {"file": "cache/short_term/summary_5d.json", "cadence": "daily", "desc": "5日情绪摘要"},
    "/api/emotion/zt-history": {"dir": "cache/zt_history", "pattern": r"^zt_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "cadence": "daily", "desc": "盘中涨停时序"},
    "/api/emotion/memory": {"dir": "cache", "pattern": r"^market_memory_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all", "limit": 20, "cadence": "daily", "desc": "情绪快照"},
    "/api/zt/yesterday": {"file": "cache/yesterday_zt.json", "cadence": "daily", "desc": "昨日涨停池"},
    # 数据底座静态事实档（数据库型单票查询在 read_data_query 中专门处理）
    "/api/data/exit-peaks": {"dir": "cache", "pattern": r"^intraday_exit_peaks_\d{8}\.json$", "mode": "latest", "cadence": "intraday", "desc": "昨日涨停池封单与Top5题材日内峰值"},
    "/api/data/theme-snapshot": {"dir": "cache/theme_snapshot", "pattern": r"^theme_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 20, "cadence": "daily", "desc": "题材冻结快照"},
    # 十六、数据源聚合层（2026-11-04 用户「展现可分散，源必须聚合」·纯新增只读入口）
    #   一个文件＝当日全部结论层：8 section + health（一致性守门）+ 维度状态台账。
    #   ⛔ 现有 /api/decision/* 照旧直读原始产物，行为逐字不变（渐进迁移，不一刀切）。
    #   ⛔ 判定链不读它（CQRS 读写分离，避免「自己算完再读自己」＝自我印证）。
    "/api/data/market-snapshot": {"dir": "cache/snapshot", "pattern": r"^\d{4}-\d{2}-\d{2}\.json$", "mode": "latest", "cadence": "daily", "desc": "当日聚合数据源（8 section + health 守门 + 维度状态·单一事实源）"},
    "/api/data/top5-history": {"dir": "cache/top5_history", "pattern": r"^top5_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 20, "cadence": "daily", "desc": "每日Top5题材原始归档"},
    # 七、盘中时间轴（jsonl）
    "/api/intraday/timeline": {"dir": "cache", "pattern": r"^intraday_timeline_\d{8}\.jsonl$", "mode": "all", "limit": 4, "lines": 1200, "format": "jsonl", "cadence": "intraday", "desc": "盘中情绪时间轴"},
    # 十、龙虎榜
    # ⚠️ 2026-11-04 维度审计：三套龙虎榜产物互不统一、且均无 tasks.yaml 日常登记 ——
    #    lhb_*.json 停 09-16 / lhb_card_* 停 09-29 / hithink_lhb_* 停 10-19。
    #    这本身就是「数据分散、没有单一源」的典型症状：三个路由读三套不同口径的文件。
    #    同 /api/market/auction 处置：retired + cadence manual，⛔ 只标注不删产物。
    "/api/lhb/tencent": {"dir": "cache", "pattern": r"^lhb_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "manual", "retired": True, "desc": "龙虎榜(腾讯)（⚠️ 已停更·停在 09-16·仅留档）"},
    "/api/lhb/hithink": {"dir": "cache", "pattern": r"^hithink_lhb_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "manual", "retired": True, "desc": "龙虎榜(同花顺)（⚠️ 已停更·停在 10-19·仅留档）"},
    # 十一、同花顺专业数据
    "/api/hithink/ladder": {"file": "cache/hithink_ladder.json", "cadence": "daily", "desc": "连板天梯"},
    "/api/hithink/zt": {"dir": "cache", "pattern": r"^hithink_zt_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "同花顺涨停池"},
    "/api/hithink/zt-diff": {"dir": "cache", "pattern": r"^hithink_zt_diff_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "涨停池双源校验差异"},
    "/api/hithink/auction": {"dir": "cache", "pattern": r"^hithink_auction_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "竞价风向标"},
    "/api/hithink/anomaly": {"dir": "cache", "pattern": r"^hithink_anomaly_\d{8}\.json$", "mode": "latest", "cadence": "daily", "desc": "异动原因"},
    "/api/hithink/valuation": {"dir": "cache", "pattern": r"^hithink_valuation_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "latest", "cadence": "daily", "desc": "同花顺估值"},
    # 十二、机构投研
    "/api/institution/pool": {"dir": "cache", "pattern": r"^institution_pool_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "机构池"},
    # ⚠️ 2026-10-14 审计（A2）：原指向 `gangtise_morning_full_v5.json` —— 那是**版式定版样本**
    #    （停在 2026-09-03），每日真实产物是 `gangtise_morning_research.json`（当日 07:54）。
    "/api/institution/morning": {"file": "cache/gangtise_morning_research.json", "cadence": "daily", "desc": "机构研报早盘"},
    "/api/institution/evening": {"file": "cache/gangtise_evening_research.json", "cadence": "daily", "desc": "机构晚报"},
    "/api/institution/weekend": {"file": "cache/gangtise_weekend_0913.json", "cadence": "manual", "desc": "机构周末"},
    # 十三、题材与消息史
    "/api/eruption/history": {"dir": "cache/eruption_history", "pattern": r"^eruption_.+\.json$", "mode": "all", "cadence": "daily", "desc": "题材爆发史"},
    "/api/newsflash/history": {"dir": "cache/newsflash_history", "pattern": r"^daily_.+\.json$", "mode": "all", "cadence": "daily", "desc": "消息史"},
    # 十四、尾盘潜伏（加权当日收益·2026-10-15）
    "/api/ambush/pullback/weighted-pnl": {"file": "cache/ambush_pullback_daily_pnl.jsonl", "cadence": "daily", "desc": "尾盘潜伏加权当日收益"},
    # ────────────────────────────────────────────────────────────
    # 十五、以下为 2026-10-26 按用户要求放开的业务路由（原「敏感三族」）
    #     仅只读转发已落盘事实；凭据字段在发送前统一脱敏（见 redact_secrets）。
    # ────────────────────────────────────────────────────────────
    # 15.1 盘中实时补充
    "/api/market/selection-effect": {"file": "cache/selection_effect.json", "cadence": "intraday", "desc": "盘后候选次日反馈"},
    "/api/flow/live": {"file": "cache/pulse_snapshots/sector_fund_pool.json", "cadence": "intraday", "desc": "板块资金盘中全量快照"},
    "/api/emotion/daily": {"dir": "cache/short_term/daily", "pattern": r"^emotion_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "cadence": "daily", "desc": "情绪日档历史（周期温度）"},
    # 15.2 决策 / 候选
    "/api/decision/candidates": {"dir": "wealth_code/cache", "pattern": r"^candidates-\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 3, "cadence": "daily", "desc": "选股候选存档（限3期）"},
    "/api/decision/latest": {"dir": "wealth_code/cache", "pattern": r"^candidates-\d{4}-\d{2}-\d{2}\.json$", "mode": "latest", "cadence": "daily", "desc": "最新一期选股候选（完整字段）"},
    "/api/decision/execution-audit": {"file": "wealth_code/cache/execution_audit.json", "cadence": "daily", "desc": "执行审计（候选冻结、竞价、裁决、账户收益与缺口）"},
    "/api/decision/auction-history": {"dir": "wealth_code/cache", "pattern": r"^auction_quotes-\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "竞价报价存档"},
    "/api/decision/archive": {"dir": "cache/decision_log", "dirPattern": r"^\d{4}-\d{2}-\d{2}$", "file": "candidates.json", "mode": "all", "limit": 3, "cadence": "daily", "desc": "决策黑匣子存档（限3期）"},
    "/api/decision/zt-pool": {"dir": "cache/decision_log", "dirPattern": r"^\d{4}-\d{2}-\d{2}$", "file": "zt_pool.json", "mode": "latest", "cadence": "daily", "desc": "决策时点涨停池明细"},
    "/api/decision/manifest": {"dir": "cache/decision_log", "dirPattern": r"^\d{4}-\d{2}-\d{2}$", "file": "manifest.json", "mode": "latest", "cadence": "daily", "desc": "存档清单"},
    "/api/decision/pool-call": {"dir": "wealth_code/cache", "pattern": r"^pool_call-\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 3, "cadence": "daily", "desc": "09:25 池内裁决终态"},
    "/api/decision/pool-ledger": {"dir": "cache/pool_ledger", "pattern": r"^\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 20, "cadence": "daily", "desc": "池内台账（收盘评估）"},
    # 「今天 48 只涨停为什么只剩这几只」—— 全量过筛逐票结论（pass/watch_only/reject + 理由链）
    "/api/decision/screen": {"dir": "cache/daily_screen", "pattern": r"^screen_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 5, "cadence": "daily", "desc": "全量涨停逐票过筛结论"},
    # 竞价三环影子逐日留痕（scripts/sector_gap_daily_trace.py 产出 · 只读、不裁决）
    "/api/decision/sector-gap-trace": {"file": "cache/sector_gap_trace/daily_trace.json", "cadence": "daily", "desc": "竞价三环影子逐日留痕（影子标记 × T+1 结算对账 · 恒不改裁决）"},
    "/api/research/backtest": {"file": "cache/research/backtest_summary.json", "cadence": "manual", "desc": "回测战绩（三口径研究快照，只读）"},
    # 15.3 AI 判断 / 净值
    "/api/ai/view": {"dir": "cache", "pattern": r"^ai_view_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "limit": 20, "cadence": "daily", "desc": "AI 判断（参与/放弃/观察 + 理由）"},
    "/api/ai/decisions": {"file": "wealth_code/cache/ai_decisions.jsonl", "format": "jsonl", "cadence": "daily", "desc": "AI 判断台账"},
    "/api/ai/nav": {"file": "wealth_code/cache/ai_nav_history.jsonl", "format": "jsonl", "cadence": "daily", "desc": "AI 轨净值台账"},
    "/api/ai/nav-t1": {"file": "wealth_code/cache/ai_nav_history_t1.jsonl", "format": "jsonl", "cadence": "daily", "desc": "AI 轨 T+1 口径净值（竞价买→次日收盘卖，独立序列）"},
    "/api/account/nav": {"file": "wealth_code/cache/account_nav_history.jsonl", "format": "jsonl", "cadence": "daily", "desc": "净值账本（脚本轨）"},
    "/api/account/metrics": {"file": "wealth_code/cache/account_strategy_metrics.json", "cadence": "daily", "desc": "策略指标（胜率/盈亏比聚合）"},
    # 15.4 尾盘潜伏明细
    "/api/ambush/pullback/signals": {"file": "cache/ambush_pullback_signal_log.jsonl", "format": "jsonl", "cadence": "daily", "desc": "尾盘潜伏信号账本"},
    "/api/ambush/pullback/settlements": {"file": "cache/ambush_pullback_settlement_log.jsonl", "format": "jsonl", "cadence": "daily", "desc": "尾盘潜伏逐笔结算记录"},
    # 15.5 题材 / 盘面补充
    "/api/theme/weekly-map": {"file": "cache/weekly_zt_map.json", "cadence": "daily", "desc": "周涨停地图"},
    "/api/carrier/state": {"file": "cache/carrier_state.json", "cadence": "daily", "desc": "载体证伪追踪"},
    "/api/ma5/status": {"file": "cache/ma5_status.json", "cadence": "daily", "desc": "五日线状态"},
    "/api/shadow/relay": {"dir": "cache/shadow", "pattern": r"^relay_shadow_.+\.json$", "mode": "latest", "cadence": "daily", "desc": "接力权重影子模式"},
    "/api/risk/watch": {"dir": "cache", "pattern": r"^risk_watch_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "风险监控快照"},
    "/api/backtest/report": {"dir": "cache", "pattern": r"^backtest_report_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "推测回测报告"},
    # 15.6 卡片存档
    "/api/card/fupan": {"dir": "cache", "pattern": r"^fupan_card_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "盘后复盘卡存档"},
    "/api/card/panqian": {"dir": "wealth_code/cache", "pattern": r"^panqian_card_\d+\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "盘前预测卡存档"},
    "/api/card/ambush": {"dir": "wealth_code/cache", "pattern": r"^ambush_card_\d+\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "早鸟埋伏卡存档"},
    "/api/card/lhb": {"dir": "cache", "pattern": r"^lhb_card_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "龙虎榜速览卡存档"},
    "/api/card/mixed": {"dir": "wealth_code/cache", "pattern": r"^card_\d{8}_\d{6}\.json$", "mode": "all", "limit": 12, "cadence": "daily", "desc": "财富密码混合卡存档"},
    # 15.7 推送 / 审计 / 系统
    "/api/pulse/windows": {"dir": "cache/pulse_windows", "pattern": r"^window_\d{8}_\d{4}\.json$", "mode": "all", "limit": 40, "cadence": "manual", "retired": True, "desc": "盘中脉冲窗口存档（已停更·仅留档）"},
    "/api/pulse/pushed-signals": {"dir": "cache", "pattern": r"^pushed_signals_\d+\.json$", "mode": "all", "limit": 5, "cadence": "daily", "desc": "推送信号状态"},
    "/api/audit/log": {"file": "cache/audit.jsonl", "format": "jsonl", "cadence": "intraday", "desc": "推送审计日志"},
    "/api/push/dedup": {"file": "cache/push_dedup.json", "cadence": "daily", "desc": "推送去重状态"},
    "/api/system/tasks": {"file": "config/tasks.yaml", "format": "text", "cadence": "daily", "desc": "自动化任务台账"},
    "/api/system/learning": {"file": "config/learning_config.json", "cadence": "daily", "desc": "自学习阈值配置"},
    "/api/system/short-spirit": {"file": "config/short_spirit.json", "cadence": "daily", "desc": "短线精灵配置"},
    "/api/system/rules": {"dir": "wealth_code/config", "pattern": r"^.+[.]json$", "mode": "all", "cadence": "daily", "desc": "策略规则配置"},
    "/api/system/engine": {"dir": "cache/pulse_snapshots", "pattern": r"^(boost_state|pulse_pending|sector_risk_cache|sector_fund_pool)[.]json$", "mode": "all", "cadence": "intraday", "desc": "盘中引擎状态"},
    # ⛔ cache/backups 仍按前缀白名单（该目录混有含明文 webhook 的 settings 备份）
    "/api/system/backups": {"dir": "cache/backups", "pattern": r"^(nav_|ai_decisions_|learning_rollback_)[^/]*[.](json|jsonl)$", "mode": "all", "limit": 20, "cadence": "daily", "desc": "系统备份快照（白名单制）"},
    "/api/system/archive-index": {"file": "cache/decision_log/index.jsonl", "format": "jsonl", "cadence": "daily", "desc": "决策存档索引"},
    "/api/promo/backtest": {"dir": "cache/promo", "pattern": r"^backtest_.+[.]json$", "mode": "all", "cadence": "daily", "desc": "晋级量化回测报告"},
}

# 私有业务路由前缀（聚合快照含决策数据，SHARE_PRIVATE=0 时整路由 404）。
PRIVATE_PREFIXES = (
    "/api/ai/", "/api/decision/", "/api/account/", "/api/data/market-snapshot",
)

# ────────────────────────────────────────────────────────────────
# 凭据脱敏（业务数据放开 ≠ 凭据放开）
# ────────────────────────────────────────────────────────────────
# 只做「键名命中即掩码」，不猜内容：命中后统一替换为 "***"。
# 目的：即使某个业务 JSON 里混入 webhook / token，也不会随分享链接出门。
SECRET_KEY_RE = re.compile(
    r"(webhook|token|secret|passw|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"authorization|cookie|session[_-]?id|credential)",
    re.IGNORECASE,
)
SECRET_MASK = "***"


def redact_secrets(value, _depth=0):
    """递归掩码凭据字段；非容器原样返回。深度上限防异常嵌套。"""
    if _depth > 12:
        return value
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and SECRET_KEY_RE.search(k):
                out[k] = SECRET_MASK
            else:
                out[k] = redact_secrets(v, _depth + 1)
        return out
    if isinstance(value, list):
        return [redact_secrets(v, _depth + 1) for v in value]
    return value


def redact_text(text):
    """YAML/纯文本路由（如 tasks.yaml）：按行掩码 `key: value` 形式的凭据。"""
    if not isinstance(text, str):
        return text
    out = []
    for line in text.split("\n"):
        m = re.match(r"^(\s*)([A-Za-z0-9_.-]+)(\s*:\s*)(.*)$", line)
        if m and SECRET_KEY_RE.search(m.group(2)):
            out.append(f"{m.group(1)}{m.group(2)}{m.group(3)}{SECRET_MASK}")
        else:
            out.append(line)
    return "\n".join(out)

# health 体检用：单文件 + 目录型路由（file 或 dir 至少一个）
HEALTH_ROUTES = [(r, d) for r, d in PUBLIC_ROUTES.items() if d.get("file") or d.get("dir")]

# ────────────────────────────────────────────────────────────────
# 静态白名单
# ────────────────────────────────────────────────────────────────
STATIC_MAP = {
    "/": ("dist", "index.html"),
    "/index.html": ("dist", "index.html"),
}
RETIRED_ROUTES = {"/watch", "/dashboard"}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json; charset=utf-8",
}


def _root_path(key, *parts):
    if key == "dist":
        return os.path.join(DIST_DIR, *parts)
    return None


def _safe_resolve(candidate):
    """确保解析后的真实路径落在允许根目录内，否则返回 None（防遍历）。"""
    real = os.path.realpath(candidate)
    for root in ALLOWED_ROOTS:
        try:
            if real == root or real.startswith(root + os.sep):
                return real
        except Exception:
            pass
    return None


# ────────────────────────────────────────────────────────────────
# 访问日志（线程安全）
# ────────────────────────────────────────────────────────────────
_log_lock = threading.Lock()


def write_access(ts, ip, path, status, ok, token_present):
    try:
        os.makedirs(os.path.dirname(ACCESS_LOG), exist_ok=True)
        rec = {
            "ts": ts,
            "ip": ip,
            "path": path,
            "status": status,
            "ok": ok,
            "token": token_present,
        }
        with _log_lock:
            with open(ACCESS_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 日志失败不影响主流程


# ────────────────────────────────────────────────────────────────
# Handler
# ────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "limao-serve/1.0"

    def log_message(self, *args):
        pass  # 统一走 write_access

    def _auth_ok(self, query):
        if not SHARE_TOKEN:
            return True, False
        tok = query.get("t") or query.get("token")
        if tok and tok[0] == SHARE_TOKEN:
            return True, True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == SHARE_TOKEN:
            return True, True
        return False, False

    # ── gzip（2026-10-26）：生产包 index.js 约 600KB，经隧道冷加载要 40s；
    #    压缩后约 1/4，显著改善外网首屏。只压缩文本类，且尊重 Accept-Encoding。
    GZIP_MIN_BYTES = 1024
    GZIP_TYPES = ("text/", "application/json", "application/javascript",
                  "image/svg+xml", "application/xml")

    def _accepts_gzip(self):
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def _maybe_gzip(self, data, ctype):
        """返回 (payload, gzipped)；过小或非文本类不压缩。"""
        if len(data) < self.GZIP_MIN_BYTES or not ctype.startswith(self.GZIP_TYPES):
            return data, False
        if not self._accepts_gzip():
            return data, False
        try:
            import gzip as _gzip
            return _gzip.compress(data, compresslevel=6), True
        except Exception:
            return data, False

    def _route_etag(self, defn):
        """单文件路由按 (mtime_ns, size) 生成强 ETag；目录路由按「最新 mtime + 文件数」。

        ⚠️ 为什么不能只按 mtime：盘中同一秒内可能写两次（不同大小），
           mtime 精度不够时会误判「没变」而返回旧内容。
        """
        try:
            import hashlib
            f = defn.get("file")
            if f:
                st = os.stat(os.path.join(DATA_ROOT, f))
                seed = f"{f}:{st.st_mtime_ns}:{st.st_size}"
            else:
                d = os.path.join(DATA_ROOT, defn.get("dir", ""))
                pat = defn.get("pattern")
                rx = re.compile(pat) if pat else None
                newest, n = 0, 0
                for name in os.listdir(d):
                    if rx and not rx.search(name):
                        continue
                    try:
                        st = os.stat(os.path.join(d, name))
                    except OSError:
                        continue
                    newest = max(newest, st.st_mtime_ns)
                    n += 1
                if n == 0:
                    return None
                seed = f"{d}:{newest}:{n}"
            return '"' + hashlib.sha1(seed.encode()).hexdigest()[:20] + '"'
        except Exception:
            return None

    def _send_json(self, code, body, etag=None, extra_headers=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        ctype = "application/json; charset=utf-8"
        payload, gz = self._maybe_gzip(data, ctype)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # 有 ETag 时用 no-cache（允许缓存但必须回源校验），
        # 未命中则退回 no-store —— 语义不变，只是给重复访问省一次整包传输
        self.send_header("Cache-Control", "no-cache" if etag else "no-store")
        if etag:
            self.send_header("ETag", etag)
        if extra_headers:
            for _k, _v in extra_headers.items():
                _vs = str(_v)
                # HTTP 头值必须是 latin-1 可编码；非 ASCII（如误入的中文）兜底转义，避免崩溃
                try:
                    _vs.encode("latin-1")
                except UnicodeEncodeError:
                    _vs = _vs.encode("utf-8", "ignore").decode("latin-1")
                self.send_header(_k, _vs)
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, real_path, ctype):
        try:
            with open(real_path, "rb") as fh:
                data = fh.read()
        except OSError:
            self.send_error(404)
            return
        payload, gz = self._maybe_gzip(data, ctype)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        # 构建产物带内容哈希，可长缓存；index.html 必须每次校验，避免旧壳引新包。
        if "/assets/" in real_path.replace("\\", "/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-store")
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        ip = self.client_address[0]
        ts = datetime.now(timezone.utc).isoformat()

        auth_ok, token_present = self._auth_ok(query)
        if not auth_ok:
            self._send_json(403, {"error": "forbidden", "message": "missing or invalid token"})
            write_access(ts, ip, path, 403, False, token_present)
            return

        # ── API ──
        if path == "/api/health":
            self._send_json(200, health_payload(DATA_ROOT, HEALTH_ROUTES))
            write_access(ts, ip, path, 200, True, token_present)
            return

        if path.startswith("/api/"):
            # 先拒绝私有路由，再查询数据或校验缓存，不能只做字段脱敏。
            if not SHARE_PRIVATE and path.startswith(PRIVATE_PREFIXES):
                self._send_json(404, {
                    "error": "not_found",
                    "route": path,
                    "message": "private route disabled by SHARE_PRIVATE=0",
                })
                write_access(ts, ip, path, 404, False, token_present)
                return
            try:
                data_query = read_data_query(path, query)
            except Exception as exc:
                self._send_json(404, {
                    "error": "data_unavailable",
                    "route": path,
                    "message": str(exc)[:240],
                })
                write_access(ts, ip, path, 404, False, token_present)
                return
            if data_query is not None:
                status, payload = data_query
                self._send_json(status, redact_secrets(payload))
                write_access(ts, ip, path, status, status == 200, token_present)
                return

            if path == '/api/decision/participation-quotes' and SHARE_PRIVATE:
                from common.participation_quotes import participation_quotes
                try:
                    self._send_json(200, participation_quotes(DATA_ROOT))
                except (OSError, ValueError, TypeError):
                    self._send_json(503, {'error': 'data_unavailable', 'route': path})
                return

            defn = PUBLIC_ROUTES.get(path)
            if not defn:
                # 白名单外一律 404，不泄露存在性
                self._send_json(
                    404,
                    {
                        "error": "not_found",
                        "route": path,
                        "message": "route not in public whitelist",
                    },
                )
                write_access(ts, ip, path, 404, False, token_present)
                return
            fresh = route_freshness(path, defn)
            extra = freshness_headers(fresh)
            # 必须先检查严格过期，条件请求不能绕过当前治理状态。
            if _STRICT_STALE and fresh.get("stale") is True:
                self._send_json(404, {
                    "error": "data_stale", "route": path,
                    "age_sec": fresh.get("age_sec"), "stale_sec": fresh.get("stale_sec"),
                    "message": "数据已超过过期阈值，严格模式下拒绝返回陈旧内容",
                }, extra_headers=extra)
                write_access(ts, ip, path, 404, False, token_present)
                return
            etag = self._route_etag(defn)
            if etag and self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                for key, value in extra.items():
                    self.send_header(key, value.encode("utf-8").decode("latin-1"))
                self.send_header("Content-Length", "0")
                self.end_headers()
                write_access(ts, ip, path, 304, True, token_present)
                return
            try:
                payload = read_route(DATA_ROOT, defn)
            except (OSError, json.JSONDecodeError) as e:
                self._send_json(
                    404,
                    {
                        "error": "data_unavailable",
                        "route": path,
                        "target": defn.get("file") or defn.get("dir"),
                        "desc": defn.get("desc"),
                        "message": str(e),
                    },
                )
                write_access(ts, ip, path, 404, False, token_present)
                return
            # 文本型（tasks.yaml）走行级掩码，结构化走递归掩码
            payload = redact_text(payload) if isinstance(payload, str) else redact_secrets(payload)

            # ── 数据基座（工件 C）：投影契约 + 新鲜度标注（加性，不影响现有响应结构）──
            cursor = query.get("cursor")
            if cursor:
                try:
                    cursor = int(cursor[0])
                except (TypeError, ValueError):
                    cursor = None
            limit_q = query.get("limit")
            if limit_q:
                try:
                    limit_q = int(limit_q[0])
                except (TypeError, ValueError):
                    limit_q = None
            if isinstance(payload, list):
                payload = paginate_list(payload, cursor, limit_q)
            fields = query.get("fields")
            payload = project_fields(payload, fields[0].split(",") if fields else None)

            self._send_json(200, payload, etag=etag, extra_headers=extra)
            write_access(ts, ip, path, 200, True, token_present)
            return

        # ── 静态 ──
        # 目录遍历拦截
        if ".." in path or path.startswith("//"):
            self.send_error(404)
            write_access(ts, ip, path, 404, False, token_present)
            return

        if path.startswith("/assets/"):
            rel = path[len("/assets/"):]
            cand = _safe_resolve(os.path.join(DIST_DIR, "assets", rel))
            if cand and os.path.isfile(cand):
                ext = os.path.splitext(cand)[1].lower()
                self._send_file(cand, CONTENT_TYPES.get(ext, "application/octet-stream"))
                write_access(ts, ip, path, 200, True, token_present)
                return
            self.send_error(404)
            write_access(ts, ip, path, 404, False, token_present)
            return

        if path in STATIC_MAP:
            key, fname = STATIC_MAP[path]
            cand = _safe_resolve(_root_path(key, fname))
            if cand and os.path.isfile(cand):
                self._send_file(cand, "text/html; charset=utf-8")
                write_access(ts, ip, path, 200, True, token_present)
                return
            self.send_error(404)
            write_access(ts, ip, path, 404, False, token_present)
            return

        # 已退役页面必须明确 404，不能被 SPA fallback 伪装成仍可访问。
        if path in RETIRED_ROUTES:
            self.send_error(404)
            write_access(ts, ip, path, 404, False, token_present)
            return

        # SPA fallback：无扩展名的未知路径 → index.html
        last_seg = path.split("?")[0].rsplit("/", 1)[-1]
        if "." not in last_seg:
            cand = _safe_resolve(os.path.join(DIST_DIR, "index.html"))
            if cand and os.path.isfile(cand):
                self._send_file(cand, "text/html; charset=utf-8")
                write_access(ts, ip, path, 200, True, token_present)
                return

        self.send_error(404)
        write_access(ts, ip, path, 404, False, token_present)
        return


def main():
    # 数据基座一致性自检：data_catalog 是否与本服务 PUBLIC_ROUTES 同步（不一致只告警不阻断）
    diffs = data_catalog.verify_against(PUBLIC_ROUTES)
    if diffs:
        print(f"[serve] ⚠️ data_catalog 与本服务路由不一致（{len(diffs)} 项），请同步：")
        for d in diffs[:20]:
            print(f"   - {d}")
    else:
        print("[serve] ✓ data_catalog 与 PUBLIC_ROUTES 一致")

    # 安全默认（2026-10-18）：默认只监听 127.0.0.1；对外监听必须显式配 SHARE_TOKEN。
    # ⛔ 旧行为（默认 0.0.0.0 + 空 token 仅打印警告）等于把盘面/持仓/审计数据无鉴权暴露到局域网。
    if not SHARE_TOKEN and SHARE_HOST not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"[serve] ⛔ 拒绝启动：SHARE_HOST={SHARE_HOST} 为对外监听，但未设置 SHARE_TOKEN。\n"
            "  · 只在本机看：SHARE_HOST=127.0.0.1（默认）\n"
            "  · 需要分享：SHARE_TOKEN=<随机串> SHARE_HOST=0.0.0.0"
        )
    print(f"狸猫猎手 serve.py  数据根={DATA_ROOT}")
    print(f"前端构建={DIST_DIR}  鉴权={'开启' if SHARE_TOKEN else '关闭(本机)'}")
    print(f"业务范围={'全部业务数据(ai/decision/account 已放开)' if SHARE_PRIVATE else '仅公开盘面(三族已关闭)'}")
    print(f"监听 http://{SHARE_HOST}:{SHARE_PORT}/")
    httpd = ThreadingHTTPServer((SHARE_HOST, SHARE_PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()


if __name__ == "__main__":
    main()
