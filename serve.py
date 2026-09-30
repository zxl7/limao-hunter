#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serve.py —— 狸猫猎手 对外只读 Web 服务（白名单制）

职责（v3 收敛版 L1，零重构，仅新增此文件）：
  托管终端 React 应用为 http(s)://，且**只暴露该暴露的**。

  · 静态白名单：托管 狸猫终端 dist + SPA fallback
  · API 白名单：只读转发 routes.ts 的「公开盘面数据」子集
      ⛔ 排除 /api/ai/* /api/decision/* /api/account/*（AI 决策 / 净值 / 决策黑匣子）
  · 公网鉴权：SHARE_TOKEN 环境变量（设置后所有请求必须带 ?t= 或 Authorization: Bearer）
  · 访问日志：cache/access_log.jsonl
  · 安全：路径遍历拦截、目录不列举、其余一律 404；Cache-Control: no-store

数据契约：调用 scripts/lib/read-route.mjs 的同语义实现（read_route），
前端 dev 插件与本项目共用同一份契约，故生产环境返回形态必须一致。

用法：
  SHARE_TOKEN=xxxx python3 serve.py                # 0.0.0.0:8000，开启鉴权
  SHARE_PORT=9000 SHARE_TOKEN=xxx python3 serve.py
  python3 serve.py                                # 关闭鉴权（仅限本机/局域网调试）

迁移服务器：用环境变量 LIMAO_DATA_ROOT 覆写 cache 根；LIMAO_DIST_DIR 覆写前端构建目录。
"""

import os
import re
import json
import time
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ────────────────────────────────────────────────────────────────
# 路径与配置
# ────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = HERE  # 狸猫猎手 根目录（routes.ts 的 file 路径以 cache/ 开头，故 DATA_ROOT 指向根）
DATA_ROOT = os.environ.get("LIMAO_DATA_ROOT") or PROJECT_ROOT
DIST_DIR = os.environ.get("LIMAO_DIST_DIR") or os.path.normpath(
    os.path.join(PROJECT_ROOT, "..", "狸猫终端", "dist")
)
CACHE_DIR = os.path.join(DATA_ROOT, "cache")

SHARE_HOST = os.environ.get("SHARE_HOST", "0.0.0.0")
SHARE_PORT = int(os.environ.get("SHARE_PORT", "8000"))
SHARE_TOKEN = os.environ.get("SHARE_TOKEN") or ""  # 空 = 不鉴权

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
    "/api/market/auction": {"file": "cache/pulse_snapshots/auction_live.json", "cadence": "intraday", "desc": "竞价实时报价"},
    "/api/market/signals": {"file": "cache/short_spirit_signals.json", "cadence": "intraday", "desc": "短线精灵信号"},
    "/api/market/relay-effect": {"file": "cache/relay_effect.json", "cadence": "intraday", "desc": "连板量能与承接观察"},
    "/api/market/feedback": {"file": "cache/feedback_matrix.json", "cadence": "intraday", "desc": "涨停反馈矩阵"},
    "/api/market/candidates": {"file": "cache/pulse_snapshots/candidates.json", "cadence": "intraday", "desc": "候选池"},
    # 二、题材 / 资金
    "/api/theme/rotation": {"file": "cache/theme_rotation.json", "cadence": "intraday", "desc": "题材轮动"},
    "/api/theme/series": {"file": "cache/theme_series.json", "cadence": "daily", "desc": "题材多日序列"},
    "/api/theme/sequence": {"file": "cache/theme_sequence.json", "cadence": "daily", "desc": "题材序列(疑似停更)"},
    "/api/flow/history": {"dir": "cache/sector_flow_history", "pattern": r"^sector_flow_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "cadence": "daily", "desc": "板块资金历史"},
    # 三、板块核心票
    "/api/board/core": {"dir": "cache/board_core", "pattern": r"^board_core_.+\.json$", "mode": "all", "cadence": "daily", "desc": "板块核心票"},
    # 四、连板晋级量化
    "/api/promotion/summary": {"file": "cache/promo/promo_summary.json", "cadence": "daily", "desc": "晋级率汇总"},
    "/api/promotion/daily": {"dir": "cache/promo", "pattern": r"^promo_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "每日晋级事件"},
    # 五、股性基因
    "/api/gene/summary": {"file": "cache/zt_genes/summary.json", "cadence": "daily", "desc": "涨停池股性榜"},
    "/api/gene/detail": {"dir": "cache/zt_genes", "pattern": r"^gene_\d{6}\.json$", "mode": "all", "cadence": "daily", "desc": "个股股性档案"},
    "/api/gene/hist": {"dir": "cache/zt_genes", "pattern": r"^hist_\d{6}\.json$", "mode": "all", "cadence": "daily", "desc": "个股历史基因"},
    "/api/gene/pool": {"dir": "cache/zt_genes", "pattern": r"^pool_\d{8}\.json$", "mode": "latest", "cadence": "daily", "desc": "涨停基因池"},
    # 六、情绪周期
    "/api/emotion/summary5d": {"file": "cache/short_term/summary_5d.json", "cadence": "daily", "desc": "5日情绪摘要"},
    "/api/emotion/zt-history": {"dir": "cache/zt_history", "pattern": r"^zt_\d{4}-\d{2}-\d{2}\.json$", "mode": "all", "cadence": "daily", "desc": "盘中涨停时序"},
    "/api/emotion/memory": {"dir": "cache", "pattern": r"^market_memory_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all", "limit": 20, "cadence": "daily", "desc": "情绪快照"},
    "/api/zt/yesterday": {"file": "cache/yesterday_zt.json", "cadence": "daily", "desc": "昨日涨停池"},
    # 七、盘中时间轴（jsonl）
    "/api/intraday/timeline": {"dir": "cache", "pattern": r"^intraday_timeline_\d{8}\.jsonl$", "mode": "all", "limit": 4, "lines": 1200, "format": "jsonl", "cadence": "intraday", "desc": "盘中情绪时间轴"},
    # 十、龙虎榜
    "/api/lhb/tencent": {"dir": "cache", "pattern": r"^lhb_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "龙虎榜(腾讯)"},
    "/api/lhb/hithink": {"dir": "cache", "pattern": r"^hithink_lhb_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "龙虎榜(同花顺)"},
    # 十一、同花顺专业数据
    "/api/hithink/ladder": {"file": "cache/hithink_ladder.json", "cadence": "daily", "desc": "连板天梯"},
    "/api/hithink/zt": {"dir": "cache", "pattern": r"^hithink_zt_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "同花顺涨停池"},
    "/api/hithink/zt-diff": {"dir": "cache", "pattern": r"^hithink_zt_diff_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "涨停池双源校验差异"},
    "/api/hithink/auction": {"dir": "cache", "pattern": r"^hithink_auction_\d{8}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "竞价风向标"},
    "/api/hithink/anomaly": {"dir": "cache", "pattern": r"^hithink_anomaly_\d{8}\.json$", "mode": "latest", "cadence": "daily", "desc": "异动原因"},
    "/api/hithink/valuation": {"dir": "cache", "pattern": r"^hithink_valuation_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "latest", "cadence": "daily", "desc": "同花顺估值"},
    # 十二、机构投研
    "/api/institution/pool": {"dir": "cache", "pattern": r"^institution_pool_\d{4}-?\d{2}-?\d{2}\.json$", "mode": "all", "limit": 10, "cadence": "daily", "desc": "机构池"},
    "/api/institution/morning": {"file": "cache/gangtise_morning_full_v5.json", "cadence": "daily", "desc": "机构研报早盘"},
    "/api/institution/evening": {"file": "cache/gangtise_evening_research.json", "cadence": "daily", "desc": "机构晚报"},
    "/api/institution/weekend": {"file": "cache/gangtise_weekend_0913.json", "cadence": "manual", "desc": "机构周末"},
    # 十三、题材与消息史
    "/api/eruption/history": {"dir": "cache/eruption_history", "pattern": r"^eruption_.+\.json$", "mode": "all", "cadence": "daily", "desc": "题材爆发史"},
    "/api/newsflash/history": {"dir": "cache/newsflash_history", "pattern": r"^daily_.+\.json$", "mode": "all", "cadence": "daily", "desc": "消息史"},
}

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

    def _send_json(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, real_path, ctype):
        try:
            with open(real_path, "rb") as fh:
                data = fh.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
            defn = PUBLIC_ROUTES.get(path)
            if not defn:
                # 含敏感路径（ai/decision/account）一律 404，不泄露存在性
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
            self._send_json(200, payload)
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
    if not SHARE_TOKEN and SHARE_HOST in ("0.0.0.0", ""):
        print(
            "⚠️  警告：未设置 SHARE_TOKEN 且监听 0.0.0.0 —— 公网暴露将无鉴权。"
            "请设置环境变量 SHARE_TOKEN 后再对外。"
        )
    print(f"狸猫猎手 serve.py  数据根={DATA_ROOT}")
    print(f"前端构建={DIST_DIR}  鉴权={'开启' if SHARE_TOKEN else '关闭(本机/局域网)'}")
    print(f"监听 http://{SHARE_HOST}:{SHARE_PORT}/")
    httpd = ThreadingHTTPServer((SHARE_HOST, SHARE_PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()


if __name__ == "__main__":
    main()
