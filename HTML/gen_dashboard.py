#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 latest.json 渲染成自包含看板 HTML（服务端预渲染，零 JS 依赖，双击即看）。
用法：python3 gen_dashboard.py
输出：cache/pulse_snapshots/dashboard.html
配色：涨红/跌绿/可买紫/标杆龙金/退潮浅绿（统一规范 2026-07-31）
"""
import json
import os
import pathlib
import datetime
import html
import re
import tempfile
from datetime import timedelta, timezone

BJ = timezone(timedelta(hours=8))

def now_bj() -> datetime.datetime:
    return datetime.datetime.now(BJ)

def _boards(e) -> str:
    b = (e or {}).get("boards")
    if not b:
        return ""
    s = str(b).strip()
    if s.isdigit():
        return f"〔{s}板〕"
    return f"〔{s}〕"

# 题材数据中枢（唯一数据源）：动态扫描本地每日沉淀 + 今日盘中
from theme_data import build_series, last_two_days, THEME_MAP, norm_theme, record_intraday_point
# ECharts 趋势模块（合并到统一看板）
from gen_trend_panel import build_trend_section

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent  # HTML/ → 狸猫猎手/
SRC = ROOT_DIR / "cache" / "pulse_snapshots" / "latest.json"
OUT = pathlib.Path(__file__).resolve().parent / "dashboard.html"
LOG_DIR = ROOT_DIR / "cache" / "logs"
SNAP_DIR = ROOT_DIR / "cache" / "pulse_snapshots"
AUDIT_LOG = ROOT_DIR / "cache" / "audit.jsonl"
# 数据新鲜度阈值（分钟）：盘中快照超过该值视为不新鲜
SNAP_FRESH_MIN = 10
HB_TAIL_BYTES = 96 * 1024   # 只读日志尾部，避免 http_client.log 这类大文件拖慢 30s 循环

# 语义色（A股：涨红跌绿；系统健康沿用：正常=红系/警告=橙/异常=绿）
_HB_DOT = {"ok": "ok", "warn": "warn", "bad": "bad"}
_HB_TEXT = {"ok": "健康", "warn": "警告", "bad": "异常"}


def _tail_lines(path: pathlib.Path, max_bytes: int = HB_TAIL_BYTES):
    """只读文件尾部若干 KB，返回行列表（大日志不整读，防止 30s 循环变慢）。"""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            raw = f.read()
        return raw.decode("utf-8", "ignore").splitlines()
    except Exception:
        return []


def _mtime_age_min(path: pathlib.Path):
    if not path.exists():
        return None
    return (datetime.datetime.now().timestamp() - path.stat().st_mtime) / 60.0


def _scan_log_errors():
    """扫描统一日志 + 快照日志尾部的 ERROR/CRITICAL/Traceback，返回 (条数, 样例列表)。

    ⚠️ 只统计「今日」记录：日志按天轮转但停跑的任务会留下陈旧文件，
    若不过滤日期，历史异常会一直显示在健康条上（08-27 的推送错误曾误显示到 09-02）。
    无日期行（如 traceback 后续行）沿用其上方最近一条带日期记录的时间。
    """
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    hits = []
    files = []
    if LOG_DIR.is_dir():
        files += list(LOG_DIR.glob("*.log"))
    if SNAP_DIR.is_dir():
        files += list(SNAP_DIR.glob("*.log"))
    date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")
    for p in files:
        cur_day = None
        for line in _tail_lines(p)[-600:]:
            m = date_re.search(line[:80])
            if m:
                cur_day = m.group(1)
            if cur_day != today:
                continue
            if ("ERROR" in line or "CRITICAL" in line or "Traceback" in line):
                hits.append(f"{p.stem}: {line.strip()[:110]}")
    return len(hits), hits[:3]


def _audit_push_stat():
    """统计今日推送成功/失败（cache/audit.jsonl，append-only）。"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    ok = fail = 0
    if not AUDIT_LOG.exists():
        return ok, fail
    for line in _tail_lines(AUDIT_LOG, max_bytes=512 * 1024):
        if today not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        ev = str(rec.get("event") or "")
        st = str(rec.get("status") or rec.get("result") or "")
        if ev.endswith("fail") or ev.endswith("exception") or st in ("fail", "error", "exception"):
            fail += 1
        elif ev.endswith("ok") or st in ("ok", "success"):
            ok += 1
    return ok, fail


def build_health_bar(snap_ts: str, render_ts: str) -> str:
    """系统健康条：数据新鲜度 + 守护状态 + 推送 + 异常中心（2026-09-02 · 中枢体检 P2）。"""
    items = []
    level = "ok"

    # ① 数据新鲜度：以 latest.json mtime 为准（快照 ts 可能停留在收盘时刻）
    age = _mtime_age_min(SRC)
    if age is None:
        items.append(("数据", "❌ latest.json 缺失", "bad"))
        level = "bad"
    else:
        age_txt = f"{int(age)}分钟前" if age < 600 else f"{age/60:.1f}小时前"
        # 非交易日/收盘后不误报：仅在交易时段内要求分钟级新鲜
        now = datetime.datetime.now()
        wd = now.weekday() < 5
        hm = now.hour * 60 + now.minute
        in_session = wd and (9 * 60 + 15) <= hm <= (15 * 60 + 5)
        if in_session and age > SNAP_FRESH_MIN:
            lvl = "warn" if age <= SNAP_FRESH_MIN * 3 else "bad"
            items.append(("快照", f"{age_txt} ⚠️滞后", lvl))
            level = "bad" if lvl == "bad" else (level if level == "bad" else "warn")
        else:
            items.append(("快照", age_txt, "ok"))

    # ② 守护状态：dashboard 由 pulse_fetcher 30s 循环重写，gen 时间即守护心跳
    items.append(("渲染", render_ts, "ok"))

    # ③ 推送：今日成功/失败
    ok_n, fail_n = _audit_push_stat()
    if fail_n:
        items.append(("推送", f"成功 {ok_n} · 失败 <span class='hb-err'>{fail_n}</span>", "bad"))
        level = "bad"
    else:
        items.append(("推送", f"今日成功 {ok_n} · 失败 0", "ok"))

    # ④ 异常中心：日志尾部 ERROR 采样
    err_n, samples = _scan_log_errors()
    if err_n:
        items.append(("异常", f"<span class='hb-err'>{err_n} 条</span>", "warn"))
        level = "bad" if level == "bad" else "warn"
    else:
        items.append(("异常", "0 条", "ok"))

    dots = "".join(
        f'<span class="hb-item"><span class="hb-k">{k}</span>'
        f'<span class="hb-v">{v}</span></span>'
        for k, v, _ in items
    )
    tip = ""
    if samples:
        tip = ('<span class="hb-item hb-tip">最近异常：'
               + " ｜ ".join(html.escape(s) for s in samples) + "</span>")
    return (f'<div class="healthbar {level}">'
            f'<span class="hb-item"><span class="hb-dot {_HB_DOT[level]}"></span>'
            f'<span class="hb-state">系统{_HB_TEXT[level]}</span></span>'
            f'{dots}{tip}</div>')


def write_html_atomic(out: pathlib.Path, html_out: str, min_len: int = 2000) -> None:
    """原子写入看板（2026-09-02 · P0-1 同类加固）：临时文件 → fsync → os.replace。
    避免 30s 守护循环被中断时留下半截 dashboard.html。"""
    if len(html_out) < min_len:
        raise ValueError(f"生成内容异常偏小（{len(html_out)} 字符），拒绝覆盖正式看板")
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html_out)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, out)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise

CSS = """
/* ── 短线交易员工作台 · 设计系统（A股国情：涨红跌绿） ── */
:root{
  --up:#ee2500;--up-soft:#ffe9e5;           /* 涨红（A股） */
  --down:#0a8f3c;--down-soft:#e3f4ea;       /* 跌绿（A股） */
  --rec:#8b5cf6;--rec-soft:#f1ecfe;         /* 可买/机会：紫 */
  --lead:#e8940a;--lead-soft:#fdf3e0;       /* 标杆龙：金 */
  --warn:#0d9488;                            /* 退潮/预警：青绿 */
  --bg:#f2f3f6;--card:#ffffff;              /* tinted neutral 底/卡 */
  --line:#e7e8ee;--line-strong:#d3d5dd;     /* 细线/强线 */
  --txt:#16161a;--sub:#666a76;              /* 非纯黑正文/次级 */
  --num:tabular-nums,'SF Mono','Menlo',ui-monospace,monospace; /* 数字表格对齐 */
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;
  background:var(--bg);color:var(--txt);padding:16px 20px;max-width:1280px;margin:0 auto;
  font-size:13px;line-height:1.55;letter-spacing:.01em;
  font-variant-numeric:tabular-nums;font-feature-settings:'tnum';  /* 全局数字等宽对齐 */
  -webkit-font-smoothing:antialiased}

/* 工作台顶栏：标题主导 + 关键状态 */
header{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:6px 12px;
  margin-bottom:14px;padding-bottom:12px;border-bottom:2px solid var(--txt)}
h1{font-size:19px;font-weight:800;letter-spacing:-.01em}
h1 .logo{display:inline-flex;width:20px;height:20px;border-radius:5px;background:var(--up);
  align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:800;
  margin-right:7px;vertical-align:-3px}
header .sub{color:var(--sub);font-size:12px;text-align:right;line-height:1.4}
header .head-right{display:flex;align-items:center;gap:12px}
header .nav-link{flex:none;color:var(--rec);font-size:12px;font-weight:700;text-decoration:none;padding:4px 10px;border-radius:6px;border:1px solid var(--rec-soft);background:var(--rec-soft);white-space:nowrap}
header .nav-link:hover{border-color:var(--rec)}

/* 系统健康条（2026-09-02 · 中枢体检 P2）：一眼判断系统健康 + 数据新鲜度 */
.healthbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin:0 0 12px;
  padding:9px 13px;border:1px solid var(--line);border-radius:10px;background:var(--card);
  box-shadow:0 1px 2px rgba(22,22,26,.05);font-size:12px}
.healthbar .hb-item{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.healthbar .hb-k{color:var(--sub)}
.healthbar .hb-v{font-weight:700;font-variant-numeric:tabular-nums}
.healthbar .hb-dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none}
.healthbar .hb-state{font-weight:800}
.healthbar.ok{border-left:4px solid var(--up)}
.healthbar.warn{border-left:4px solid #d97706}
.healthbar.bad{border-left:4px solid var(--down)}
.healthbar .hb-dot.ok{background:var(--up)}
.healthbar .hb-dot.warn{background:#d97706}
.healthbar .hb-dot.bad{background:var(--down)}
.healthbar .hb-err{color:var(--down);font-weight:700}
.healthbar .hb-tip{color:var(--sub);font-size:11px}

/* 布局节奏：高密度网格 */
.grid{display:grid;gap:10px}
.cols3{grid-template-columns:repeat(3,1fr)}
.cols2{grid-template-columns:repeat(2,1fr)}
.section{margin-top:14px}

/* 卡片：硬朗交易员风（细线、弱阴影、小圆角） */
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 15px;
  box-shadow:0 1px 2px rgba(22,22,26,.05)}
.card:hover{border-color:var(--line-strong)}
.card h2{font-size:15px;margin-bottom:10px;color:var(--txt);font-weight:800;letter-spacing:.01em}

/* 语义色类 */
.up{color:var(--up)} .down{color:var(--down)}
.rec{color:var(--rec);font-weight:700}      /* 可买/埋伏：紫=AI推荐介入机会 */
.rec-row,.rec-row *{color:var(--rec) !important}  /* 推荐/机会整行紫 */
.zt-row,.zt-row *{color:var(--up) !important}     /* 已涨停/封板整行红 */
.lead{color:var(--lead);font-weight:700}    /* 涨停标杆龙：金=标杆非机会 */
.warn{color:var(--warn)}                     /* 退潮/风险预警：青绿 */
.turn{color:#3b82c4;font-weight:600}         /* 换手率：蓝 */

/* 页脚免责 */
.disclaimer{font-size:12px;color:var(--sub);text-align:center;border-style:dashed;background:#fafafa}

/* 指数卡：大数字工作台感 */
.idx{text-align:center;padding:14px 12px}
.idx .pct{font-size:26px;font-weight:800;letter-spacing:-.01em;margin:4px 0;line-height:1.1}
.idx .nm{font-size:12px;color:var(--sub);font-weight:600}
.idx .stat{font-size:12px;color:var(--sub)}

/* 情绪条：醒目状态徽章 */
.mood{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.badge{background:var(--up-soft);color:var(--up);border:1px solid #ffd9d0;padding:4px 11px;
  border-radius:20px;font-size:12px;font-weight:700}
.stat{font-size:12px;color:var(--sub)}

/* 题材卡 */
.theme .name{font-size:14px;font-weight:800}
.theme .meta{font-size:12px;color:var(--sub);margin:4px 0}
.theme .desc{font-size:12px;color:#4a4d57;line-height:1.5}

/* 个股行 */
.ldr{display:flex;justify-content:space-between;align-items:center;padding:6px 0;
  border-bottom:1px solid var(--line);font-size:13px}
.ldr:last-child{border:none}
.tag{font-size:10px;background:var(--lead-soft);color:var(--lead);padding:1px 6px;
  border-radius:5px;margin-left:4px;font-weight:700}
.buy{color:var(--rec);font-size:11px;margin-left:4px;font-weight:700}
ul{list-style:none} li{padding:5px 0;font-size:13px;border-bottom:1px solid var(--line)}
.win{background:var(--lead-soft);border:1px solid #ffe0b0}

.legend{font-size:12px;color:var(--sub)}
.legend b{color:var(--txt)}
.error{color:var(--warn);font-size:14px;padding:20px}

/* 题材对比模块 */
.cmp{margin-top:14px}.cmp h2{font-size:16px;margin-bottom:10px;font-weight:800}
.cmp .bars{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.cmp .half h3{font-size:12px;color:var(--sub);margin-bottom:8px;font-weight:700}
.cmp .row{display:flex;align-items:center;gap:6px;margin:5px 0;font-size:12px}
.cmp .row .lab{width:92px;flex:none;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cmp .row .track{flex:1;background:#f0f0f3;border-radius:6px;height:16px;overflow:hidden}
.cmp .row .fill{height:100%;border-radius:6px}
.cmp .row .val{width:20px;flex:none;text-align:left;font-weight:700}
.cmp .fill.y{background:linear-gradient(90deg,#ffd9d0,#ee2500)}
.cmp .fill.t{background:linear-gradient(90deg,#cfe8ff,#3b82c4)}
.cmp table{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px}
.cmp th,.cmp td{padding:6px 4px;border-bottom:1px solid var(--line);text-align:center}
.cmp th{color:var(--sub);font-weight:700}.cmp td.lab{text-align:left;font-weight:700}
.cmp .new{color:var(--up);font-weight:700}.cmp .out{color:var(--down);font-weight:700}
.cmp .hold{color:var(--sub)}.cmp .weak{color:var(--lead);font-weight:700}
.cmp .insight{font-size:12px;line-height:1.7;margin-top:10px;background:#fafafa;border:1px dashed var(--line);border-radius:10px;padding:10px}

/* 标准表格组件（2026-08-17 · 战绩/明细等通用，风格对齐 .cmp） */
.tbl{width:100%;border-collapse:collapse;font-size:12px;background:var(--card)}
.tbl th,.tbl td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
.tbl thead th{color:var(--sub);font-weight:700;background:#fafafa;white-space:nowrap;
  border-bottom:1px solid var(--line-strong);position:sticky;top:0}
.tbl tbody tr:hover{background:#fafafa}
.tbl td.num,.tbl th.num{text-align:right;font-variant-numeric:tabular-nums}
.tbl .date{font-weight:700;white-space:nowrap;color:var(--txt)}
.tbl tr:last-child td{border-bottom:none}

/* 每日题材趋势矩阵 */
.trend{margin-top:14px}.trend h2{font-size:16px;margin-bottom:10px;font-weight:800}
.trend .scroll{overflow-x:auto}
.trend table{border-collapse:collapse;font-size:12px;min-width:100%}
.trend th,.trend td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:center;position:relative}
.trend th{color:var(--sub);font-weight:700;white-space:nowrap}
.trend .lab{text-align:left;font-weight:700;white-space:nowrap;position:sticky;left:0;background:var(--card);z-index:1}
.trend th.lab{background:var(--card)}
.trend .cell{position:relative;min-width:44px}
.trend .cell .bg{position:absolute;left:0;top:4px;bottom:4px;border-radius:4px;background:rgba(238,37,0,.10)}
.trend .cell.intraday .bg{background:rgba(59,130,196,.14)}
.trend .cell .v{position:relative;z-index:1;font-weight:700}
.trend .none{color:var(--sub);font-weight:400}
.trend .delta.up{color:var(--up);font-weight:700}.trend .delta.down{color:var(--down);font-weight:700}
.trend .intraday-h{color:#3b82c4}
.trend .total{color:var(--sub);font-size:11px}

/* ECharts 趋势面板 */
.chart{width:100%;height:300px}.chart-sm{width:100%;height:200px}
/* ── 📊 资金监控 Tab（2026-09-08）：ECharts 板块资金全景 · 涨红跌绿 ── */
.ffmon .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:10px;margin-bottom:14px}
.ffmon .kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.ffmon .kpi .n{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.25}
.ffmon .kpi .l{font-size:11px;color:var(--sub);margin-top:3px}
.ffmon .kpi .n small{font-size:12px;font-weight:600}
.ffmon .blk{margin-bottom:14px}
.ffmon .blk h3{font-size:13px;margin-bottom:2px}
.ffmon .blk .sub{font-size:11px;color:var(--sub);margin-bottom:6px}
.ffmon .dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:1px}
.ffmon .src{font-size:11px;color:var(--sub);margin-top:6px}

/* 🔄 今日题材轮动 Tab（2026-08-21）：时间线样式随 render_rotation_tab 内联注入 */
/* 垂直柱状图（2026-08-21 用户「多日对比用柱状图：柱高=大小，颜色=流入红/流出绿」） */
.vchart{display:flex;align-items:flex-end;gap:5px;height:88px;margin:8px 0 2px;padding:0 2px;border-bottom:1px solid var(--line,#e7e8ee)}
.vc{display:flex;flex-direction:column;justify-content:flex-end;height:100%;min-width:0}
.vbar{background:#e7e8ee;border-radius:3px 3px 0 0;min-height:3px;transition:height .3s}
.vbar.up{background:var(--up,#e23c3c)}
.vbar.down{background:var(--down,#1aa260)}
.vbar.neu{background:#3b82c4}
.vbar.null{background:#e7e8ee;height:3px}
.vbar.fill{background:transparent;border:2px dashed var(--up,#e23c3c);box-sizing:border-box;opacity:.85}
.vbar.fill.dn{background:transparent;border:2px dashed var(--down,#1aa260);box-sizing:border-box;opacity:.85}
.vlab{font-size:9px;color:var(--sub,#86868b);text-align:center;margin-top:3px;white-space:nowrap}
.vchart-legend{font-size:10px;color:var(--sub,#86868b);margin-top:2px}
.status{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.pill{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:6px 13px;
  font-size:12px;white-space:nowrap}
.pill b{color:#3b82c4}.pill .up{color:var(--up);font-weight:700}
.table-wrap{overflow-x:auto}
.lead-name{font-weight:800}.lead-boards{color:var(--sub);font-size:11px}
.boards-badge{display:inline-block;background:var(--lead-soft);color:var(--lead);padding:1px 5px;
  border-radius:8px;font-size:10px;font-weight:700;margin-left:2px}
.strength{font-size:14px;letter-spacing:1px}
.s5{color:#ee2500}.s4{color:#e8940a}.s3{color:#3b82c4}.s2{color:var(--sub)}.s1{color:#ccc}
.refresh-note{font-size:11px;color:var(--sub);text-align:center;margin-top:12px}

/* 高密度响应式：窄屏降列保密度 */
@media (max-width:900px){.cols3,.cols2{grid-template-columns:1fr}.cmp .bars{grid-template-columns:1fr}}

/* ── Tab 分组（2026-08-14 用户：dashboard 沉淀汇总 · 多 tab 查看） ── */
.tabbar{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:10}
.tab-btn{border:1px solid var(--line-strong);background:var(--card);color:var(--sub);
  padding:7px 18px;border-radius:20px;font-size:13px;font-weight:700;cursor:pointer;
  transition:all .15s ease;letter-spacing:.01em}
.tab-btn:hover{border-color:var(--rec);color:var(--rec)}
.tab-btn.active{background:var(--rec);border-color:var(--rec);color:#fff}
.tab-pane{display:block}
.tab-pane[hidden]{display:none}

/* 连板天梯（日内炒作梯队） */
.ladder .row{display:flex;align-items:baseline;gap:8px;padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}
.ladder .row:last-child{border-bottom:none}
.ladder .b{font-weight:800;font-size:14px;min-width:48px;flex:none}
.ladder .b.hi{color:var(--lead)}
.ladder .b.norm{color:var(--up)}
.ladder .names{color:var(--up);font-weight:600}
.ladder .cnt{color:var(--sub);font-size:12px}
.ladder .sum{font-size:12px;color:var(--sub);margin-top:9px}

/* 板块资金流向（主力净流入/流出） */
.flow .bars{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.flow .half h3{font-size:12px;color:var(--sub);margin-bottom:8px;font-weight:700}
.flow .row{display:flex;align-items:center;gap:6px;margin:5px 0;font-size:12px}
.flow .row .lab{width:96px;flex:none;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.flow .row .track{flex:1;background:#f0f0f3;border-radius:6px;height:16px;overflow:hidden}
.flow .row .fill{height:100%;border-radius:6px}
.flow .fill.in{background:linear-gradient(90deg,#ffd9d0,#ee2500)}
.flow .fill.out{background:linear-gradient(90deg,#d9f5e6,#0a8f3c)}
.flow .row .val{width:56px;flex:none;text-align:left;font-weight:700}
.flow .val.in{color:var(--up)} .flow .val.out{color:var(--down)}
/* P2-18 资金结构分层行（超大/大/中/小·亿·CapitalPulse 吸收 2026-09-09）：
   语义色板映射：流入=红(改善) / 流出=绿(恶化)，与整卡一致。缺分层字段时整行不输出（兼容降级） */
.flow .subrow{margin:-1px 0 7px 102px;font-size:10.5px;line-height:1.5;color:var(--sub);
              white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.flow .subrow .pos{color:var(--up);font-weight:700}
.flow .subrow .neg{color:var(--down);font-weight:700}
.flow .flow-sum{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.flow .flow-sum .badge{font-size:13px}
.flow .insight{font-size:12px;line-height:1.7;margin-top:10px;background:#fafafa;border:1px dashed var(--line);border-radius:10px;padding:10px}

/* 5日资金监管（2026-08-26 用户「5日记录详情、10日记录总览」） */
.flow5 .bars{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.flow5 .half h3{font-size:12px;color:var(--sub);margin-bottom:8px;font-weight:700}
.flow5 .row{display:flex;align-items:center;gap:6px;margin:5px 0;font-size:12px}
.flow5 .row .lab{width:88px;flex:none;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.flow5 .row .track{flex:1;background:#f0f0f3;border-radius:6px;height:16px;overflow:hidden}
.flow5 .row .fill{height:100%;border-radius:6px}
.flow5 .fill.in{background:linear-gradient(90deg,#ffd9d0,#ee2500)}
.flow5 .fill.out{background:linear-gradient(90deg,#d9f5e6,#0a8f3c)}
.flow5 .row .val{width:52px;flex:none;text-align:left;font-weight:700}
.flow5 .val.in{color:var(--up)} .flow5 .val.out{color:var(--down)}
.flow5 .stat5{font-size:10px;color:var(--sub);flex:none;white-space:nowrap}
.flow5 .flow-sum{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;font-size:12px}

/* 盘前竞价定调卡 */
.auction .tone{font-size:14px;font-weight:800;color:var(--txt);margin-bottom:8px;line-height:1.5}
.auction .mains{display:grid;grid-template-columns:1fr 1fr;gap:4px 14px;margin:6px 0}
.auction .mrow{display:flex;justify-content:space-between;align-items:center;font-size:12px;padding:2px 0}
.auction .mname{font-weight:700}
.auction .mstar{color:var(--lead);letter-spacing:1px;font-size:12px}
.auction .cats{margin:6px 0}
.auction .crow{display:flex;gap:6px;align-items:flex-start;font-size:12px;padding:3px 0;border-bottom:1px solid var(--line)}
.auction .crow:last-child{border:none}
.auction .cemoji{flex:none}
.auction .ctitle{font-weight:600;line-height:1.4}
.auction .anchor{font-size:12px;color:#4a4d57;background:#fafafa;border:1px dashed var(--line);border-radius:8px;padding:8px;margin:6px 0;line-height:1.6}
.auction .risks{margin:6px 0}
.auction .rrow{font-size:11px;color:var(--down);line-height:1.5;padding:2px 0}
.auction .ms{font-size:12px;color:var(--rec);font-weight:700;margin-top:6px;background:var(--rec-soft);border-radius:8px;padding:6px 8px}

/* 主线温度计 */
.mainline .trow{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}
.mainline .trow:last-child{border-bottom:none}
.mainline .tname{width:120px;flex:none;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mainline .ttrack{flex:1;background:#f0f0f3;border-radius:6px;height:14px;overflow:hidden}
.mainline .tfill{height:100%;border-radius:6px;background:linear-gradient(90deg,#ffd9d0,#ee2500)}
.mainline .tval{width:96px;flex:none;text-align:left;font-weight:700}
.mainline .sum{font-size:12px;color:var(--sub);margin-top:9px}

/* 题材回流预测 */
.ret .rrow2{border-bottom:1px solid var(--line);padding:8px 0}
.ret .rrow2:last-child{border-bottom:none}
.ret .rtop{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ret .rname{font-size:13px;font-weight:800}
.ret .rphase{font-size:10px;background:var(--warn);color:#fff;padding:1px 6px;border-radius:5px;font-weight:700}
.ret .rconf{font-size:12px;color:var(--sub);margin-left:auto}
.ret .rbar{height:8px;background:#f0f0f3;border-radius:6px;overflow:hidden;margin:6px 0}
.ret .rfill{height:100%;border-radius:6px;background:linear-gradient(90deg,#c4b5fd,#8b5cf6)}
.ret .rmeta{font-size:11px;color:#4a4d57;line-height:1.5;margin-top:2px}
.ret .sum{font-size:12px;color:var(--sub);margin-top:9px}

/* 板块涨停数增长趋势（迷你柱·5交易日） */
.ztgrid{display:flex;flex-direction:column;gap:2px}
.ztrend{display:flex;align-items:center;gap:10px;padding:5px 0;border-bottom:1px solid var(--line)}
.ztrend:last-child{border-bottom:none}
.ztrend .zn{width:96px;flex:none;font-weight:700;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ztbars{display:flex;align-items:flex-end;gap:3px;height:28px;flex:1}
.ztbar{flex:1;min-width:7px;border-radius:2px 2px 0 0;background:var(--line-strong);transition:height .2s}
.ztbar.up{background:var(--up)}
.ztbar.down{background:var(--down)}
.ztbar.zero{background:transparent;border-top:2px solid var(--line-strong)}
.ztrend .zval{width:62px;flex:none;text-align:right;font-size:12px;font-variant-numeric:tabular-nums}
.ztrend .ztag{width:30px;flex:none;text-align:center;font-size:12px;font-weight:700}
.ztrend .ztag.up{color:var(--up)} .ztrend .ztag.down{color:var(--down)} .ztrend .ztag.new{color:var(--lead)}

/* 题材回流：板数趋势 + 资金趋势双条 */
.ret .grp-title{font-size:13px;font-weight:800;margin:10px 0 4px;padding-bottom:3px;border-bottom:2px solid var(--line)}
.ret .grp-title.fire{color:var(--up)} .ret .grp-title.cool{color:var(--down)}
.ret .rstrip{display:flex;align-items:center;gap:8px;margin-top:5px;font-size:11px;color:#4a4d57}
.ret .rstrip .rlab{width:48px;flex:none;color:var(--sub)}
.ret .spark{display:flex;align-items:flex-end;gap:3px;height:22px;flex:1}
.ret .sbar{flex:1;min-width:6px;border-radius:2px 2px 0 0}
.ret .sbar.up{background:var(--up)} .ret .sbar.down{background:var(--down)} .ret .sbar.neu{background:var(--line-strong)}
.ret .strend{width:74px;flex:none;text-align:right;font-size:11px;font-weight:700}
.ret .strend.up{color:var(--up)} .ret .strend.down{color:var(--down)} .ret .strend.neu{color:var(--sub)}

/* 核按钮/跌停预警 */
.nuke .nlst{margin-top:8px}
.nuke .nrow{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--line);font-size:12px}
.nuke .nrow:last-child{border-bottom:none}
.nuke .nname{flex:1;font-weight:600}
.nuke .npct{font-weight:700;width:64px;flex:none}
.nuke .nprev{font-size:11px;color:var(--sub);width:54px;flex:none}
.nuke .sum{font-size:12px;color:var(--sub);margin-top:9px}
"""

TPL = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>狸猫猎手 · 统一看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>{css}</style>
</head>
<body>
<header>
  <h1><span class="logo">狸</span>狸猫猎手 · 短线交易员工作台</h1>
  <div class="head-right">
    <a class="nav-link" href="watch.html">⚡ 全屏盯盘</a>
    <div class="sub">{geninfo}</div>
  </div>
</header>
{healthbar}
<nav class="tabbar">
  <button class="tab-btn active" data-tab="rotation">🔄 今日轮动</button>
  <button class="tab-btn" data-tab="intraday">📈 盘中实时</button>
  <button class="tab-btn" data-tab="mainline">🔥 主线题材</button>
  <button class="tab-btn" data-tab="return">🔄 题材回流</button>
  <button class="tab-btn" data-tab="trade">💰 交易战绩</button>
  <button class="tab-btn" data-tab="flow">💸 资金流向</button>
  <button class="tab-btn" data-tab="fundmon">📊 资金监控</button>
  <button class="tab-btn" data-tab="record">📚 数据记录</button>
  <button class="tab-btn" data-tab="stack">📊 数据堆叠</button>
</nav>
<div class="tab-pane" id="tab-rotation">{tab_rotation}</div>
<div class="tab-pane" id="tab-intraday" hidden>{tab_intraday}</div>
<div class="tab-pane" id="tab-mainline" hidden>{tab_mainline}</div>
<div class="tab-pane" id="tab-return" hidden>{tab_return}</div>
<div class="tab-pane" id="tab-trade" hidden>{tab_trade}</div>
<div class="tab-pane" id="tab-flow" hidden>{tab_flow}</div>
<div class="tab-pane" id="tab-fundmon" hidden>{tab_fundmon}</div>
<div class="tab-pane" id="tab-record" hidden>{tab_record}</div>
<div class="tab-pane" id="tab-stack" hidden>{tab_stack}</div>
{footer}
<script>{tab_js}</script>
<script>{trend_js}</script>
</body>
</html>"""


# Tab 切换 JS（2026-08-14）：三 Tab 分组 + 显示 Tab 内 ECharts resize + 交易 Tab 懒初始化净值曲线
TAB_JS = """
function switchTab(name){
  document.querySelectorAll('.tab-pane').forEach(function(p){ p.hidden = (p.id !== 'tab-'+name); });
  document.querySelectorAll('.tab-btn').forEach(function(b){ b.classList.toggle('active', b.dataset.tab===name); });
  var pane = document.getElementById('tab-'+name);
  if (pane){
    pane.querySelectorAll('[id$="-chart"], [id^="chart-"]').forEach(function(el){
      var inst = echarts.getInstanceByDom(el);
      if (inst) inst.resize();
    });
  }
  if (name==='trade' && window.ensureWealthChart) window.ensureWealthChart();
  if (name==='stack' && window.ensureStackCharts) window.ensureStackCharts();
  if (name==='fundmon' && window.ensureFundMonCharts) window.ensureFundMonCharts();
}
document.addEventListener('DOMContentLoaded', function(){
  document.querySelectorAll('.tab-btn').forEach(function(b){
    b.addEventListener('click', function(){ switchTab(b.dataset.tab); });
  });
});
"""


def upcls(v):
    if v is None or (isinstance(v, float) and (v != v)):
        return ""
    return "up" if v > 0 else ("down" if v < 0 else "")


def pct(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return (f"+{v:.2f}%" if v > 0 else f"{v:.2f}%")


def esc(s):
    return html.escape(str(s)) if s is not None else ""


# ===== 财富密码 · 真实战绩模块（数据源：wealth_code/cache）=====

def _load_wealth():
    """加载财富密码战绩数据（净值账本 / 策略指标 / 最新候选）。

    返回值 dict：nav_rows(净值曲线) / metrics(最新指标) / relay / watch /
    cand_date / cand_ts / reset_note。全部容错，缺文件返回空。
    """
    wc_cache = ROOT_DIR / "wealth_code" / "cache"
    out = {"nav_rows": [], "metrics": None, "relay": [], "watch": [],
           "cand_date": "", "cand_ts": "", "reset_note": ""}

    # 净值账本
    nav_p = wc_cache / "account_nav_history.jsonl"
    if nav_p.exists():
        try:
            for line in nav_p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if isinstance(r, dict) and r.get("trade_date"):
                    out["nav_rows"].append(r)
        except Exception:
            out["nav_rows"] = []

    # 当前持仓（未结算·agent 在 verify 任务写入 cache/holdings.json；dashboard 原不读，2026-08-17 接入）
    h_p = ROOT_DIR / "cache" / "holdings.json"
    if h_p.exists():
        try:
            h = json.loads(h_p.read_text(encoding="utf-8"))
            out["holdings"] = h.get("holdings") or []
            out["holdings_updated"] = str(h.get("updated") or "")
            out["holdings_note"] = str(h.get("note") or "")
        except Exception:
            out["holdings"] = []

    # 策略指标（取最新推荐日 next_day·tradable）
    m_p = wc_cache / "account_strategy_metrics.json"
    if m_p.exists():
        try:
            m = json.loads(m_p.read_text(encoding="utf-8"))
            recs = m.get("records") or []
            out["reset_note"] = str(m.get("reset_note") or "")
            if recs:
                last = recs[-1]
                nd = (last.get("metrics") or {}).get("next_day") or {}
                trad = nd.get("tradable") or {}
                out["metrics"] = {
                    "recommendation_date": last.get("recommendation_date"),
                    "trade_date": last.get("trade_date"),
                    "covered": trad.get("covered", 0),
                    "win_rate": trad.get("win_rate"),
                    "avg_return": trad.get("avg_return"),
                    "win_count": trad.get("win_count", 0),
                    "loss_count": trad.get("loss_count", 0),
                }
        except Exception:
            out["metrics"] = None

    # 最新盘后候选（relay/watch）
    cand_files = sorted(wc_cache.glob("candidates-*.json"))
    if cand_files:
        try:
            cp = cand_files[-1]
            c = json.loads(cp.read_text(encoding="utf-8"))
            out["relay"] = c.get("relay") or []
            out["watch"] = c.get("watch") or []
            out["cand_date"] = str(c.get("date") or "")
            out["cand_ts"] = str(c.get("generated_at_bj") or "")[:16]
        except Exception:
            pass
    return out


def _wealth_nav_curve(nav_rows):
    """净值曲线数据：dates + navs（2026-08-17 起渲染为红色主题·A股涨红语义）。"""
    dates, navs = [], []
    for r in nav_rows:
        dates.append(str(r.get("trade_date") or ""))
        try:
            navs.append(round(float(r.get("nav") or 0.0), 4))
        except Exception:
            navs.append(None)
    return dates, navs


def render_wealth_panel(w):
    """财富密码战绩卡片：指标条 + 净值曲线 + 接力/观察候选。空态优雅降级。"""
    nav_rows = w.get("nav_rows") or []
    metrics = w.get("metrics")
    relay = w.get("relay") or []
    watch = w.get("watch") or []
    reset_note = w.get("reset_note") or ""

    # 净值指标
    if nav_rows:
        try:
            first = nav_rows[0]
            last = nav_rows[-1]
            implied = round(float(first.get("nav", 1.0)) / (1 + float(first.get("avg_return_pct") or 0) / 100.0), 6)
            latest_nav = round(float(last.get("nav", 1.0)), 4)
            total_pct = round((latest_nav / implied - 1) * 100.0, 2)
            trade_days = len(nav_rows)
        except Exception:
            implied, latest_nav, total_pct, trade_days = 1.0, 1.0, 0.0, 0
    else:
        latest_nav, total_pct, trade_days = 1.0, 0.0, 0
    nav_cls = upcls(total_pct)

    # 胜率指标
    if metrics and metrics.get("win_rate") is not None:
        wr = float(metrics["win_rate"])
        avg = metrics.get("avg_return")
        win_txt = (f'隔日胜率 <b class="up">{wr:.1f}%</b>（覆盖 {metrics.get("covered", 0)} 只'
                   f'，胜 {metrics.get("win_count", 0)} / 负 {metrics.get("loss_count", 0)}）')
        avg_txt = f'平均隔日收益 <b class="{upcls(avg)}">{pct(avg)}</b>' if isinstance(avg, (int, float)) else "平均隔日收益 —"
        rec_date = f'（{metrics.get("recommendation_date") or "—"} 推荐）'
    else:
        win_txt = "隔日胜率 <b class='stat'>N/A</b>（等待首笔 09:25 竞价结算）"
        avg_txt = "平均隔日收益 —"
        rec_date = ""

    # 候选列表行（红=接力池 / 紫=观察池）
    def cand_row(it, cls):
        exp = it.get("expectation") or {}
        rng = exp.get("expected_range") or ["—", "—"]
        amt = exp.get("auction_amount_range")
        amt_txt = f'量昨{amt[0]:.0f}~{amt[1]:.0f}%' if isinstance(amt, (list, tuple)) and len(amt) == 2 else ""
        cm = " <span class='tag'>20cm</span>" if it.get("is_20cm") else ""
        return (f'<div class="ldr {cls}"><span>{esc(it.get("name"))} '
                f'<span class="stat">{esc(it.get("code"))}</span>{cm} '
                f'<span class="stat">{esc(it.get("theme") or "")}</span> '
                f'<span class="stat">{it.get("boards", 1)}板</span></span>'
                f'<span class="stat">预期{rng[0]}~{rng[1]}% {amt_txt} 龙头分{it.get("score")}</span></div>')

    parts = ['<div class="card section"><h2>💰 财富密码 · 真实战绩（自 2026-08-12 起）</h2>']
    parts.append('<div class="status">'
                 f'<span class="pill">📈 最新净值 <b>{latest_nav:.4f}</b></span>'
                 f'<span class="pill">累计收益 <b class="{nav_cls}">{total_pct:+.2f}%</b></span>'
                 f'<span class="pill">已结算 <b>{trade_days}</b> 交易日</span>'
                 f'<span class="pill">{win_txt}{rec_date}</span>'
                 f'<span class="pill">{avg_txt}</span></div>')

    # 当前持仓（未结算）—— 直接读 cache/holdings.json，反映今日 09:25 竞价达标买入
    h_items = w.get("holdings") or []
    if h_items:
        h_upd = w.get("holdings_updated") or "—"

        def _ap_html(x):
            ap = x.get("auction_pct")
            if isinstance(ap, (int, float)):
                return f' <span class="{upcls(ap)}">{ap:+.2f}%</span>'
            return ""

        h_cards = " ".join(
            f'<span class="pill">{esc(x.get("name"))} <span class="stat">{esc(x.get("code"))}</span>'
            f'{" · " + esc(x.get("bucket_label")) if x.get("bucket_label") else ""}'
            f'{_ap_html(x)}</span>'
            for x in h_items
        )
        h_note = (w.get("holdings_note") or "").strip()
        h_note_html = f'<div style="font-size:11px;color:#86868b;margin-top:6px;line-height:1.5">{esc(h_note)}</div>' if h_note else ""
        parts.append(
            '<div class="card" style="margin-top:10px"><h2>📌 当前持仓（未结算）</h2>'
            f'<div class="status" style="margin-bottom:2px">{h_cards}</div>'
            f'<div style="font-size:12px;color:#86868b">更新于 {esc(h_upd)} · 09:25 竞价达标买入 · 收益待当日 15:00 盘后结算写入净值账本（建仓日不结算）</div>'
            f'{h_note_html}</div>'
        )
    elif w.get("cand_date"):
        parts.append('<div class="card" style="margin-top:10px"><h2>📌 当前持仓（未结算）</h2>'
                     '<div class="stat">暂无持仓（今日 09:25 无竞价达标信号或未同步）</div></div>')

    # 净值曲线（有数据才渲染 ECharts）
    dates, navs = _wealth_nav_curve(nav_rows)
    if dates:
        parts.append('<div class="card" style="margin-top:10px"><div class="chart chart-sm" id="wealth-nav-chart"></div></div>')

    # 每日交易明细表（2026-08-14 用户：每天买的票和收益；stocks 为 08-14 起新格式）
    # 2026-08-17 改：标准 .tbl 表格组件（thead/tbody，去内联样式）
    if nav_rows:
        rows_html = []
        # 2026-08-27：表格按交易日降序（最新日期置顶），不影响上方净值曲线（需升序）
        for r in sorted(nav_rows, key=lambda r: str(r.get("trade_date", "")), reverse=True):
            td = str(r.get("trade_date") or "—")
            avg = r.get("avg_return_pct")
            avg_cls = upcls(avg)
            avg_txt = f"{avg:+.2f}%" if isinstance(avg, (int, float)) else "—"
            stk = r.get("stocks") or []
            if stk:
                cells = " / ".join(
                    f'{esc(s.get("name"))} <span class="{upcls(s.get("return_pct"))}">{s.get("return_pct"):+.2f}%</span>'
                    f'<span class="stat"> {esc(s.get("code"))}</span>'
                    for s in stk if isinstance(s, dict)
                )
            else:
                names = r.get("names") or []
                cells = "、".join(esc(n) for n in names) if names else "—"
                if cells != "—":
                    cells += ' <span class="stat">（旧格式·无每票收益）</span>'
            rows_html.append(
                f'<tr><td class="date">{td}</td>'
                f'<td>{cells}</td>'
                f'<td class="num"><b class="{avg_cls}">{avg_txt}</b></td></tr>'
            )
        parts.append(
            '<div class="card" style="margin-top:10px"><h2>📋 每日交易明细（买入→次日收盘）</h2>'
            '<div class="table-wrap"><table class="tbl">'
            '<thead><tr><th class="date">交易日</th>'
            '<th>买入标的（每票收益）</th>'
            '<th class="num">平均收益</th></tr></thead>'
            '<tbody>' + "".join(rows_html) + '</tbody>'
            '</table></div></div>'
        )

    # 最新候选
    if relay or watch:
        cand_txt = f'最新候选（{w.get("cand_date") or "—"} 盘后 · {w.get("cand_ts") or ""}）'
        parts.append(f'<div style="font-size:12px;color:#86868b;margin:10px 0 6px">{cand_txt}</div>')
        if relay:
            parts.append('<div style="font-size:13px;font-weight:700;margin:8px 0 2px">🔥 接力池（核心候选）</div>')
            for it in relay:
                parts.append(cand_row(it, "zt-row"))
        if watch:
            parts.append('<div style="font-size:13px;font-weight:700;margin:8px 0 2px">👀 观察池</div>')
            for it in watch:
                parts.append(cand_row(it, "rec-row"))
    elif w.get("cand_date"):
        parts.append('<div class="stat" style="margin-top:8px">暂无候选（盘后任务未产出）</div>')

    if reset_note:
        parts.append(f'<div class="stat" style="margin-top:8px">🔄 {esc(reset_note)}</div>')
    parts.append('</div>')
    return "\n".join(parts)


def wealth_chart_js(w):
    """净值曲线 ECharts JS（追加到 trend_js；无数据返回空串）。"""
    dates, navs = _wealth_nav_curve(w.get("nav_rows") or [])
    if not dates:
        return ""
    data = json.dumps({"dates": dates, "navs": navs}, ensure_ascii=False)
    return (
        f'const WEALTH_NAV = {data};\n'
        "function initWealthChart() {\n"
        "  var el = document.getElementById('wealth-nav-chart');\n"
        "  if (!el) return;\n"
        "  var ch = echarts.init(el);\n"
        "  ch.setOption({\n"
        "    title: {text: '📈 净值曲线（真实交易战绩·等权隔日复利）', left: 'center', textStyle: {fontSize: 13, color: '#86868b'}},\n"
        "    tooltip: {trigger: 'axis', backgroundColor: '#fff', borderColor: '#e7e8ee',\n"
        "      textStyle: {color: '#16161a'},\n"
        "      axisPointer: {lineStyle: {color: 'rgba(238,37,0,.35)'}}},\n"
        "    grid: {left: 50, right: 20, top: 40, bottom: 30},\n"
        "    xAxis: {type: 'category', data: WEALTH_NAV.dates, axisLabel: {color: '#86868b'},\n"
        "      axisLine: {lineStyle: {color: '#e7e8ee'}}},\n"
        "    yAxis: {type: 'value', scale: true, axisLabel: {color: '#86868b'},\n"
        "      axisLine: {lineStyle: {color: '#e7e8ee'}}, splitLine: {lineStyle: {color: '#f0f1f4'}}},\n"
        "    series: [{type: 'line', data: WEALTH_NAV.navs, smooth: true,\n"
        "      lineStyle: {color: '#ee2500', width: 2.5},\n"
        "      itemStyle: {color: '#ee2500'},\n"
        "      areaStyle: {\n"
        "        color: new echarts.graphic.LinearGradient(0, 0, 0, 1,\n"
        "          [{offset: 0, color: 'rgba(238,37,0,.18)'}, {offset: 1, color: 'rgba(238,37,0,.02)'}])\n"
        "      },\n"
        "      markPoint: {data: [{type: 'max', name: '峰值'}],\n"
        "        itemStyle: {color: '#ee2500'},\n"
        "        label: {color: '#fff', fontSize: 10}},\n"
        "      markLine: {silent: true, symbol: 'none',\n"
        "        data: [{yAxis: WEALTH_NAV.navs[0], lineStyle: {color: 'rgba(238,37,0,.28)', type: 'dashed'},\n"
        "          label: {color: '#86868b', fontSize: 10, formatter: '基准'}}]}}]\n"
        "  });\n"
        "  window.addEventListener('resize', function() { ch.resize(); });\n"
        "}\n"
        "// 懒初始化（2026-08-14）：交易 Tab 默认隐藏，切到该 Tab 才 init（隐藏容器尺寸 0 的坑）\n"
        "window.wealthChartInited = false;\n"
        "function ensureWealthChart() {\n"
        "  if (!window.wealthChartInited && document.getElementById('wealth-nav-chart')) {\n"
        "    initWealthChart(); window.wealthChartInited = true;\n"
        "  } else if (window.wealthChartInited) {\n"
        "    var inst = echarts.getInstanceByDom(document.getElementById('wealth-nav-chart'));\n"
        "    if (inst) inst.resize();\n"
        "  }\n"
        "}\n"
    )


# ===== 日内炒作梯队（连板天梯）=====
def render_ladder(d):
    """连板天梯：按板数从高到低展示日内炒作梯队（数据源 latest.json['ladder']）。

    返回 '' 表示无数据（容错降级）。最高板用金标(ladder .b.hi)，在板票整行红。
    """
    ladder = d.get("ladder") or []
    if not ladder:
        return ""
    ladder = sorted(ladder, key=lambda x: -(int(x.get("boards") or 0)))
    rows, total, maxb = [], 0, 0
    for x in ladder:
        b = int(x.get("boards") or 0)
        stocks = x.get("stocks") or []
        if not stocks or b <= 0:
            continue
        total += len(stocks)
        maxb = max(maxb, b)
        bcls = "b hi" if b == maxb else "b norm"
        names = "、".join(esc(s) for s in stocks)
        rows.append(f'<div class="row"><span class="{bcls}">{b}板</span>'
                    f'<span class="names">{names}</span>'
                    f'<span class="cnt">（{len(stocks)}只）</span></div>')
    if not rows:
        return ""
    sumline = (f'连板共 <b class="up">{total}</b> 只 ｜ 最高 <b class="lead">{maxb}</b> 板'
               f'｜{"梯队完整·高位承接强" if maxb >= 4 else "高位偏短·注意断层风险"}')
    return ('<div class="card ladder section"><h2>🪜 日内炒作梯队（连板天梯）</h2>'
            f'{"".join(rows)}<div class="sum">{sumline}</div></div>')


# ===== 板块资金流向（主力净流入/流出）=====
def render_fund_flow(d, ts=None):
    """板块主力资金流向：流入 Top（红）/ 流出 Top（绿）（数据源 latest.json['sector_risk']）。

    增强：顶部显示主力净流入净额（亿，红/绿）+ 今日实时时间戳。
    返回 '' 表示无数据（容错降级）。条形宽度按同侧绝对值最大归一；单位=亿元。
    """
    sr = d.get("sector_risk") or {}
    inflow = sr.get("inflow_top") or []
    outflow = sr.get("outflow_top") or []
    if not inflow and not outflow:
        return ""

    def rows(items):
        if not items:
            return '<div class="stat" style="padding:8px 0">暂无数据</div>'
        mx = max([abs(it.get("flow_yi") or 0) for it in items] + [1])
        parts = []
        for it in items:
            name = esc(it.get("name"))
            fy = it.get("flow_yi") or 0
            pct_v = it.get("pct")
            pw = (abs(fy) / mx * 100) if mx else 0
            cls = "in" if fy >= 0 else "out"
            pct_txt = f' <span class="stat">{pct_v:+.2f}%</span>' if isinstance(pct_v, (int, float)) else ""
            # P2-18 资金结构分层（source_ts 存在=分层可用；缺失/降级不输出 subrow）
            sub_txt = ""
            if it.get("source_ts"):
                seg = []
                for _k, _lbl in (("super_large_yi", "超大"), ("large_yi", "大"),
                                 ("mid_yi", "中"), ("small_yi", "小")):
                    _v = it.get(_k)
                    if isinstance(_v, (int, float)):
                        _c = "pos" if _v > 0 else ("neg" if _v < 0 else "stat")
                        seg.append(f'{_lbl} <span class="{_c}">{_v:+.1f}</span>')
                if seg:
                    sub_txt = ('<div class="subrow">' + " · ".join(seg[:2]) +
                               " ｜ " + " · ".join(seg[2:]) + "</div>")
            parts.append(f'<div class="row"><span class="lab">{name}{pct_txt}</span>'
                         f'<div class="track"><div class="fill {cls}" style="width:{pw:.0f}%"></div></div>'
                         f'<span class="val {cls}">{fy:+.1f}</span></div>{sub_txt}')
        return "".join(parts)

    inflow_html = f'<div class="half"><h3>🔴 主力流入 Top（亿）</h3>{rows(inflow)}</div>'
    outflow_html = f'<div class="half"><h3>🟢 主力流出 Top（亿）</h3>{rows(outflow)}</div>'
    ins = "💡 资金主攻 → " + "、".join(esc(it.get("name")) for it in inflow[:3])
    if outflow:
        ins += "；流出 → " + "、".join(esc(it.get("name")) for it in outflow[:3])
    # 主力净流入净额（流入为正、流出为负，求和即净额）
    net = sum((it.get("flow_yi") or 0) for it in inflow) + sum((it.get("flow_yi") or 0) for it in outflow)
    net_cls = "up" if net >= 0 else "down"
    ts_txt = ""
    if ts:
        try:
            ts_txt = f' · 截至 {str(ts)[11:16]}'
        except Exception:
            ts_txt = ""
    summary = (f'<div class="flow-sum"><span class="badge">主力净流入净额 '
               f'<b class="{net_cls}">{net:+.1f}</b> 亿</span>'
               f'<span class="stat">流入 {len(inflow)} 板块 / 流出 {len(outflow)} 板块{ts_txt}</span></div>')
    return ('<div class="card flow section"><h2>💸 板块资金流向（主力净流入·今日实时）</h2>'
            f'{summary}'
            f'<div class="bars">{inflow_html}{outflow_html}</div>'
            f'<div class="insight">{ins}</div></div>')


def render_flow_5d(tr=None):
    """📅 5日资金监管（2026-08-26 用户「5日记录详情、10日记录总览」）。

    数据源 theme_rotation.json flow_5d（五日累计主力净流入 Top/流出 Top）：
    红=5日累计流入（资金持续进场）、绿=5日累计流出（资金撤离）。
    每行：板块名 + 5日累计(亿) + 流入天数N/有效D + 末日方向 + 趋势标签。
    返回 '' 表示无数据（容错降级）。
    """
    f5 = (tr or {}).get("flow_5d") or {}
    inflow = f5.get("inflow_top") or []
    outflow = f5.get("outflow_top") or []
    if not inflow and not outflow:
        return ""
    days_txt = ""
    dd = f5.get("data_days") or []
    if dd:
        days_txt = f'<span class="stat">窗口 {dd[0][5:]} ~ {dd[-1][5:]}</span>'

    def rows(items):
        if not items:
            return '<div class="stat" style="padding:8px 0">暂无数据</div>'
        mx = max([abs(it.get("total_yi") or 0) for it in items] + [1])
        parts = []
        for it in items:
            name = esc(it.get("name"))
            tot = it.get("total_yi") or 0
            days = it.get("days") or 0
            in_days = it.get("inflow_days") or 0
            last = it.get("last_yi")
            trend = it.get("trend") or ""
            pw = (abs(tot) / mx * 100) if mx else 0
            cls = "in" if tot >= 0 else "out"
            trend_txt = {"inflow": "🟢持续流入", "outflow": "🔴持续流出"}.get(trend, "")
            last_txt = f"<b class='{'up' if (last or 0) >= 0 else 'down'}'>{last:+.1f}</b>" if isinstance(last, (int, float)) else "—"
            parts.append(f'<div class="row"><span class="lab">{name}</span>'
                         f'<div class="track"><div class="fill {cls}" style="width:{pw:.0f}%"></div></div>'
                         f'<span class="val {cls}">{tot:+.1f}</span>'
                         f'<span class="stat5">入{in_days}/{days}天</span>'
                         f'<span class="stat5">末日{last_txt}</span>'
                         f'<span class="stat5">{trend_txt}</span></div>')
        return "".join(parts)

    inflow_html = f'<div class="half"><h3>🔴 5日累计流入 Top（亿）</h3>{rows(inflow[:8])}</div>'
    outflow_html = f'<div class="half"><h3>🟢 5日累计流出 Top（亿）</h3>{rows(outflow[:8])}</div>'
    return ('<div class="card flow5 section"><h2>📅 5日资金监管（累计主力净流入·双窗口）</h2>'
            f'<div class="flow-sum">{days_txt}'
            f'<span class="stat">5日详情=每日序列 · 10日总览见回流预测</span></div>'
            f'<div class="bars">{inflow_html}{outflow_html}</div></div>')


def render_rotation_tab(d=None, fb=None):
    """🔄 今日题材轮动 Tab（2026-08-21 用户「删短线盯盘，把今天轮动的题材和时间线全列出来」）。

    数据源：
      ① cache/theme_sequence.json —— 今日题材轮动序列（bump_themes 按首见顺序跨窗口去重，pulse_aggregator 维护）
      ② cache/pulse_buffer.json   —— 今日窗口桶事件（sector 板块级事件 ts/zt_count/leader_name/stage + zt 涨停事件流）
      ③ latest.json total_zt + feedback_matrix.prev_zt_count —— 涨停数量今昨对比柱状图（2026-08-21 用户）
    展示：今日轮动总览（含电风扇红警）→ 涨停今昨对比柱状图 → 题材时间线（板块级·首见时间/涨停/龙头/阶段）→ 涨停事件流。
    """
    today = now_bj().strftime("%Y-%m-%d")
    today_c = now_bj().strftime("%Y%m%d")
    # ① 题材序列
    seq = {}
    try:
        seq = json.loads((ROOT_DIR / "cache" / "theme_sequence.json").read_text(encoding="utf-8"))
    except Exception:
        seq = {}
    themes = seq.get("themes") or [] if seq.get("date") == today else []
    # ⚠️ 2026-08-26 修复：theme_sequence 只记录「窗口聚合触发时」的 sector 事件，
    # 早盘高频窗口(09:25-09:55·pulse_fetcher 跳过 close_window_step 防双通道)的板块级
    # 爆发不进 buffer → 医药早盘爆发(09:33 汉森)漏记，轮动序列缺医药/顺序失真。
    # 修复（两层）：
    #   A. live_signal 已落地 first_seen_<date>（每轮 top5 首见时间戳·不受窗口聚合影响）→ 优先用
    #   B. 无 first_seen（今日历史）→ 三源合并近似：buffer sector 时间序 + sector_pushed 补漏 + top5 兜底
    try:
        _sig_path = ROOT_DIR / "cache" / f"pushed_signals_{now_bj().strftime('%Y%m%d')}.json"
        _sp = []
        _fs = {}
        if _sig_path.exists():
            _sig = json.loads(_sig_path.read_text(encoding="utf-8"))
            _sp = _sig.get("sector_pushed") or []
            _fs = _sig.get("first_seen_" + now_bj().strftime("%Y%m%d")) or {}
        # A. first_seen 精确时间序（当日已记录≥2 个板块才用，避免盘中过早回退）
        if len(_fs) >= 2:
            _merged = [nm for nm, _ in sorted(_fs.items(), key=lambda x: x[1])]
        else:
            # B. 三源合并近似（buffer sector 事件真实时间主序 + sector_pushed 补漏 + top5 兜底）
            _timed = []
            try:
                _buf = json.loads((ROOT_DIR / "cache" / "pulse_buffer.json").read_text(encoding="utf-8"))
                for _wk, _lst in (_buf.get("windows") or {}).items():
                    if not _wk.startswith(now_bj().strftime("%Y%m%d")):
                        continue
                    for _e in _lst:
                        if _e.get("type") == "sector" and _e.get("name"):
                            _timed.append((_e.get("ts") or "", _e.get("name")))
                _timed.sort(key=lambda x: x[0])
            except Exception:
                pass
            _timed_set = {nm for _, nm in _timed}
            # 从窗口卡补各板块首次出现时间（早盘高频窗口漏掉的板块时间）
            _win_first = {}
            try:
                for _wf in sorted((ROOT_DIR / "cache" / "pulse_windows").glob("window_" + now_bj().strftime("%Y%m%d") + "_*.json")):
                    _wk = _wf.name.split("window_")[1].split(".json")[0]
                    _hm = _wk[9:11] + ":" + _wk[11:13]
                    _d2 = json.loads(_wf.read_text(encoding="utf-8"))
                    _s2 = json.dumps(_d2, ensure_ascii=False)
                    for _nm in _sp:
                        if _nm and _nm not in _win_first and _nm in _s2:
                            _win_first[_nm] = _hm
            except Exception:
                pass
            for _nm, _hm in _win_first.items():
                if _nm and _nm not in _timed_set:
                    _timed.append((f"2026-08-26T{_hm}:00", _nm))
            _timed.sort(key=lambda x: x[0])
            _merged = []
            for _t, _nm in _timed:
                if _nm and _nm not in _merged:
                    _merged.append(_nm)
            _timed_set2 = {nm for _, nm in _timed}
            _early = [nm for nm in _sp if nm and nm not in _timed_set2]   # 真·早盘无任何时间戳（医药）
            for _nm in _early:
                if _nm not in _merged:
                    _merged.insert(0, _nm)
        # ③ theme_sequence 补漏（保历史行为）
        for _nm in themes:
            if _nm and _nm not in _merged:
                _merged.append(_nm)
        # ④ top5 兜底
        _d = d or {}
        for _s in (_d.get("top5") or []):
            _nm = str(_s.get("name") or "")
            if _nm and _nm not in _merged:
                _merged.append(_nm)
        themes = _merged
    except Exception:
        pass
    # ② buffer 事件
    buf = {"windows": {}}
    try:
        buf = json.loads((ROOT_DIR / "cache" / "pulse_buffer.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    sec_evs, zt_evs = [], []
    for wk, lst in buf.get("windows", {}).items():
        if not wk.startswith(today_c):
            continue
        for e in lst:
            t = e.get("type")
            if t == "sector":
                sec_evs.append(e)
            elif t == "zt":
                zt_evs.append(e)
    sec_evs.sort(key=lambda e: e.get("ts", ""))
    zt_evs.sort(key=lambda e: e.get("ts", ""))
    # zt 事件归并（题材 → 涨停数/首见时间/龙头=首个涨停股）
    zt_by_sec = {}
    for e in zt_evs:
        s = e.get("sector") or "—"
        g = zt_by_sec.setdefault(s, {"n": 0, "first": e.get("ts", ""), "leader": e, "max_bd": 0})
        g["n"] += 1
        if e.get("ts", "") < g["first"]:
            g["first"], g["leader"] = e.get("ts", ""), e
        bd = str(e.get("boards") or "")
        if bd.isdigit() and int(bd) > g["max_bd"]:
            g["max_bd"] = int(bd)
    # 板块级题材事件（按 name 取最早一次做首见时间线）
    seen = {}
    for e in sec_evs:
        nm = e.get("name") or ""
        if nm and nm not in seen:
            seen[nm] = e
    sec_by_name = seen
    # ── 总览卡 ──
    n_th = len(themes)
    if n_th >= 5:
        warn = f"<span style='color:var(--warn,#1aa260)'>⚠️ 已轮动 {n_th} 个题材·电风扇行情，追高极难赚钱，防守优先</span>"
    elif n_th >= 3:
        warn = f"<span style='color:var(--sub,#86868b)'>🔆 已轮动 {n_th} 个题材·轮动加快，只做前排龙头，不追后排</span>"
    else:
        warn = f"<span style='color:var(--sub,#86868b)'>今日轮动 {n_th} 个题材，主线聚焦度尚可</span>"
    seq_txt = " → ".join(f"<span class='rot-tag'>{esc(x)}</span>" for x in themes) if themes else "—"
    ov = (f'<div class="card section"><h2 style="font-size:17px;margin-bottom:10px">🔄 今日题材轮动（{today}）</h2>'
          f'<div style="line-height:1.9;margin-bottom:8px">{seq_txt}</div>'
          f'<div style="font-size:12px">{warn}</div>'
          f'<div style="font-size:11px;color:var(--sub,#86868b);margin-top:6px">'
          f'板块级异动 {len(sec_evs)} 条 · 涨停事件 {len(zt_evs)} 条</div></div>')
    # ── 涨停数量今昨对比（柱状图·2026-08-21 用户「柱高=大小·一眼看递增递减」）──
    zt_cmp = ""
    today_zt = (d or {}).get("total_zt") or 0
    prev_zt = (fb or {}).get("prev_zt_count")
    if prev_zt and today_zt:
        zt_clr = "up" if today_zt > prev_zt else ("down" if today_zt < prev_zt else "neu")
        zt_trend_txt = ("📈 递增" if today_zt > prev_zt else
                        "📉 递减" if today_zt < prev_zt else "＝ 持平")
        zt_cmp = ('<div class="card section"><h2 style="font-size:17px;margin-bottom:10px">📊 涨停数量对比（今 vs 昨）</h2>'
                  + _vchart([prev_zt, today_zt], ["昨日", "今日"], unit="只", colors=["neu", zt_clr])
                  + f'<div class="vchart-legend">昨 <b>{prev_zt}</b> 只 → 今 <b>{today_zt}</b> 只（{zt_trend_txt}）'
                    f'｜ 蓝=昨日 · 红=今增 / 绿=今减</div></div>')
    # ── 题材时间线（板块级 + zt 归并补全）──
    def _tl_row(time_txt, badge, title, desc):
        return (f'<div class="tl-row"><div class="tl-time">{esc(time_txt)}</div>'
                f'<div class="tl-body"><div class="tl-title">{badge} {title}</div>'
                f'<div class="tl-desc">{desc}</div></div></div>')
    rows = []
    # 板块级事件（有明确 ts）
    for e in sec_evs:
        nm = e.get("name") or "?"
        zc = e.get("zt_count")
        ld = e.get("leader_name")
        stg = e.get("stage") or ""
        sq = e.get("seq_no")
        zs = f"{zc}只涨停" if zc else ""
        ls = f"｜龙头 {esc(ld)}" if ld else ""
        ss = f"｜{stg}" if stg else ""
        qs = f"｜今日#{sq}" if sq else ""
        rows.append(_tl_row((e.get("ts") or "")[11:16], "📊", f"板块爆发【{esc(nm)}】",
                            f"{zs}{ls}{ss}{qs}"))
    # theme_sequence 中有、但无板块级事件的题材（如机器人）→ 用 zt 归并补首见时间线
    for th in themes:
        if th in sec_by_name:
            continue
        g = None
        for sec, gg in zt_by_sec.items():
            if th in sec or sec in th:
                g = gg
                break
        if g:
            ld = g["leader"]
            bd = _boards(ld)
            rows.append(_tl_row(g["first"][11:16], "🏷️", f"个股归集【{esc(th)}】",
                                f"{g['n']}只涨停｜龙头 {esc(ld.get('name') or '?')} {ld.get('pct') or ''}%{bd}"))
    rows.sort(key=lambda r: r)  # 按时间文本排序（HH:MM 前缀）
    tl_html = ('<div class="card section"><h2 style="font-size:17px;margin-bottom:10px">📋 题材时间线</h2>'
               '<div class="tl">' + "".join(rows) + '</div></div>') if rows else \
              '<div class="card section"><h2 style="font-size:17px;margin-bottom:10px">📋 题材时间线</h2><div class="stat">今日暂无板块级异动</div></div>'
    # ── 涨停事件流（全量时间线）──
    tl_rows = []
    for e in zt_evs:
        nm = e.get("name") or "?"
        sc = e.get("sector") or "—"
        pctv = e.get("pct")
        ptxt = f"{pctv:+.2f}%" if isinstance(pctv, (int, float)) else ""
        bd = _boards(e)
        clr = "#e23c3c" if isinstance(pctv, (int, float)) and pctv >= 9 else ""
        tl_rows.append(_tl_row((e.get("ts") or "")[11:16], "🔥",
                               f"{esc(nm)} <span style='color:{clr};font-weight:600'>{ptxt}</span>{bd}",
                               f"{esc(sc)}"))
    flow_html = ('<div class="card section"><h2 style="font-size:17px;margin-bottom:10px">🔥 涨停事件流（全量·{n}）</h2>'
                 '<div class="tl">' + "".join(tl_rows) + '</div></div>').format(n=len(zt_evs)) if tl_rows else ""
    return ('<style>'
            '.rot-tag{display:inline-block;background:var(--line,#eef0f4);color:var(--sub,#3c4043);'
            'border:1px solid var(--line-strong,#d8dbe3);border-radius:10px;padding:1px 9px;margin:2px 3px 2px 0;font-size:12px}'
            '.tl{margin-top:4px;position:relative}'
            '.tl-row{display:flex;gap:10px;padding:6px 0;border-bottom:1px dashed var(--line,#e7e8ee);font-size:12px}'
            '.tl-row:last-child{border-bottom:none}'
            '.tl-time{flex:0 0 46px;font-variant-numeric:tabular-nums;color:var(--sub,#86868b);font-size:11px;padding-top:1px}'
            '.tl-body{flex:1;min-width:0}'
            '.tl-title{font-weight:600;color:var(--txt,#1d1d1f);line-height:1.5}'
            '.tl-desc{color:var(--sub,#86868b);font-size:11px;margin-top:1px}'
            '</style>'
            f'<div class="section" style="margin-top:18px">{ov}{zt_cmp}{tl_html}{flow_html}</div>')


# ===== 盘前竞价定调卡（数据源：cache/panqian_data_*.json 最新）=====
def _load_panqian():
    # 按修改时间取最新（两种命名格式 panqian_data_YYYYMMDD / panqian_data_MM-DD 并存，
    # 字典序会取错，必须按 mtime）
    files = sorted(ROOT_DIR.glob("cache/panqian_data_*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return {}


def render_auction(pq):
    """盘前竞价定调卡：盘前 09:25 前情绪周期 + 隔夜美股 + 主线星级 + 催化 + 竞价锚点。
    返回 '' 表示无盘前数据（容错降级）。"""
    if not pq:
        return ""
    date = pq.get("date", "")
    tone = pq.get("title_tone", "")
    sd = pq.get("switch_day") or {}
    indices = (pq.get("global_market") or {}).get("indices") or []
    mains = pq.get("main_lines") or []
    cats = pq.get("catalysts") or []
    anchor = pq.get("switch_anchor", "")
    risks = pq.get("risks") or []
    mindset = pq.get("mindset", "")

    # 隔夜美股
    ix = ""
    if indices:
        segs = []
        for x in indices:
            nm = esc(x.get("name"))
            p = x.get("pct")
            segs.append(f'{nm} <span class="{upcls(p)}">{pct(p)}</span>')
        ix = '<div class="stat" style="margin:6px 0">🌐 隔夜：' + " ｜ ".join(segs) + '</div>'

    # 主线星级
    mh = ""
    if mains:
        items = []
        for m in mains[:6]:
            nm = esc(m.get("name"))
            stars = m.get("stars") or 0
            star_str = "★" * int(stars) + "☆" * (5 - int(stars)) if isinstance(stars, int) else ""
            items.append(f'<div class="mrow"><span class="mname">{nm}</span><span class="mstar">{star_str}</span></div>')
        mh = '<div class="mains">' + "".join(items) + '</div>'

    # 隔夜催化（前3）
    ch = ""
    if cats:
        items = []
        for c in cats[:3]:
            emoji = esc(c.get("emoji") or "💡")
            title = esc(c.get("title"))
            items.append(f'<div class="crow"><span class="cemoji">{emoji}</span><span class="ctitle">{title}</span></div>')
        ch = '<div class="cats">' + "".join(items) + '</div>'

    # 竞价锚点
    ah = f'<div class="anchor">{esc(anchor)}</div>' if anchor else ""

    # 风险（前3）+ 心法
    rh = ""
    if risks:
        rs = "".join(f'<div class="rrow">⚠ {esc(r)}</div>' for r in risks[:3])
        rh = f'<div class="risks">{rs}</div>'
    ms = f'<div class="ms">{esc(mindset)}</div>' if mindset else ""

    regime = esc(sd.get("regime", ""))
    senti = esc(sd.get("sentiment", ""))
    return ('<div class="card auction section"><h2>🌅 盘前竞价定调（' + esc(date) + ' 09:25前）</h2>'
            f'<div class="tone">{esc(tone)}</div>'
            f'<div class="stat" style="margin:6px 0">📐 情绪周期：{regime} · {senti}</div>'
            f'{ix}{mh}{ch}{ah}{rh}{ms}</div>')


# ===== 主线温度计（数据源：latest.json['sector_delta']）=====
def render_mainline(d):
    """主线温度计：持续主线(ongoing)按今板数排序展示热度；new/surging 补充新题材/升温提示。"""
    sd = d.get("sector_delta") or {}
    ongoing = sd.get("ongoing") or []
    new_ = sd.get("new") or []
    surging = sd.get("surging") or []
    if not (ongoing or new_ or surging):
        return ""

    def parse(s):
        m = re.match(r'(.+?)\((\d+)→(\d+)\)', s)
        if m:
            return m.group(1), int(m.group(2)), int(m.group(3))
        return s, None, None

    items = [parse(s) for s in ongoing]
    rows = []
    tv = [t for _, _, t in items if t is not None] or [1]
    maxv = max(tv)
    for nm, y, t in sorted(items, key=lambda x: -(x[2] or 0)):
        if t is None:
            rows.append(f'<div class="trow"><span class="tname">{esc(nm)}</span><span class="tval stat">—</span></div>')
            continue
        pw = t / maxv * 100
        diff = (t - y) if y is not None else None
        if diff is None:
            dmark = ""
        elif diff > 0:
            dmark = f'<span class="up">▲{diff}</span>'
        elif diff < 0:
            dmark = f'<span class="down">▼{-diff}</span>'
        else:
            dmark = '<span class="stat">=</span>'
        rows.append(f'<div class="trow"><span class="tname">{esc(nm)}</span>'
                    f'<div class="ttrack"><div class="tfill" style="width:{pw:.0f}%"></div></div>'
                    f'<span class="tval">{t}板 {dmark}</span></div>')
    ongoing_html = "".join(rows)
    extra = ""
    if new_:
        extra += '<div class="stat" style="margin-top:6px">🆕 新题材：' + "、".join(esc(x) for x in new_) + '</div>'
    if surging:
        extra += '<div class="stat" style="margin-top:4px">🚀 升温：' + "、".join(esc(x) for x in surging) + '</div>'
    sumline = f'持续主线 <b class="up">{len(items)}</b> 条 ｜ 涨停≥8 视为高热度主线 · 持平=资金抱团未松动'
    return ('<div class="card mainline section"><h2>🌡️ 主线温度计（持续主线）</h2>'
            f'{ongoing_html}{extra}<div class="sum">{sumline}</div></div>')


# ===== 题材回流预测卡（数据源：theme_rotation.json['candidates']）=====
def _load_theme_rotation():
    p = ROOT_DIR / "cache" / "theme_rotation.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ===== 板块涨停数增长趋势卡（数据源：theme_rotation.json['sector_zt_trends']）=====
def render_sector_zt_trend(tr):
    """板块涨停数增长趋势卡：近5交易日各题材涨停数迷你柱 + 板数与方向标签。
    升温/新晋优先排序；柱高=当日涨停板数，🔴升温 ▼绿退潮 🆕首现。"""
    if not tr:
        return ""
    trends = tr.get("sector_zt_trends") or []
    if not trends:
        return ""
    days = tr.get("data_days") or []
    nd = len(days)

    def yval(v):
        if not v:
            return 0
        return int(v) if float(v).is_integer() else round(v, 1)

    rows = []
    for z in trends:
        nm = esc(z.get("theme"))
        series = list(z.get("zt_series") or [])
        while len(series) < nd:
            series = [None] + series
        series = series[-nd:] if nd else series
        maxv = max([v for v in series if v is not None] + [1])
        bars, prev = [], None
        for v in series:
            if v is None:
                bars.append('<div class="ztbar zero" style="height:3px"></div>')
            else:
                h = max(3, int(v / maxv * 24))
                cls = ("up" if (prev is not None and v > prev)
                       else "down" if (prev is not None and v < prev) else "")
                bars.append(f'<div class="ztbar {cls}" style="height:{h}px" title="{yval(v)}板"></div>')
                prev = v
        latest = z.get("latest") or 0
        d_ = z.get("dir")
        if d_ == "rising":
            tag = f'<span class="ztag up">▲{z.get("delta")}</span>'
        elif d_ == "falling":
            tag = f'<span class="ztag down">▼{-z.get("delta")}</span>'
        elif d_ == "new":
            tag = '<span class="ztag new">🆕</span>'
        else:
            tag = '<span class="ztag">=</span>'
        rows.append(f'<div class="ztrend"><span class="zn">{nm}</span>'
                    f'<div class="ztbars">{"".join(bars)}</div>'
                    f'<span class="zval">{yval(latest)}板</span>{tag}</div>')
    daylabel = "～".join(d[5:] for d in days) if days else ""
    return ('<div class="card ztrend-card section"><h2>📈 板块涨停数增长趋势（近'
            + (daylabel or "5交易日") + '）</h2>'
            + '<div class="ztgrid">' + "".join(rows) + '</div>'
            + '<div class="sum">柱高=当日涨停板数；🔴升温 ▼绿退潮 🆕首现；按升温/新晋优先排序（同一数据源驱动回流预测）</div></div>')


# ===== 题材回流预测卡（数据源：theme_rotation.json['candidates']）=====
def _zt_dir_cn(d):
    return {"rising": "▲升温", "falling": "▼退潮", "new": "🆕首现", "flat": "=平稳"}.get(d, "—")


# ── 辨识度个股三件套（换手/成交额/五日涨幅·2026-08-26 用户「板块与辨识度个股要有关联性」）──
# 数据源：腾讯 qt 实时盘口（换手 f38·成交额 f37万）+ 腾讯日K（五日涨幅）。
# 模块级缓存防重复请求：同一渲染轮内同 code 只拉一次；失败降级"—"（数据即事实不编造）。
_QT_CACHE = {}


def _qt_batch(codes):
    """批量拉腾讯实时盘口 → {code: {turn, amount_yi, pct}}（缺字段 None）。
    ⚠️ qt.gtimg.cn 返回 GBK 编码，不能用 http_client（utf-8 解码失败），直接 urllib+gbk。"""
    import urllib.request as _ur
    need = [c for c in codes if c and c not in _QT_CACHE]
    if need:
        secs = []
        for c in need:
            m = c[:2].lower()
            if m in ("sh", "sz", "bj"):
                secs.append(c.lower())
            elif c.startswith(("6", "9")):
                secs.append(f"sh{c}")
            else:
                secs.append(f"sz{c}")
        url = "https://qt.gtimg.cn/q=" + ",".join(secs)
        try:
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = _ur.urlopen(req, timeout=8).read()
            txt = raw.decode("gbk", errors="ignore")
        except Exception:
            txt = ""
        for line in (txt or "").strip().split(";"):
            if "~" not in line:
                continue
            f = line.split("~")
            if len(f) < 39:
                continue
            code = f[2]
            _QT_CACHE[code] = {
                "turn": _f2(f[38]),
                "amount_yi": _f2(f[37]) / 10000.0 if _f2(f[37]) is not None else None,
                "pct": _f2(f[32]),
            }
    return {c: _QT_CACHE.get(c, {}) for c in codes}


def _f2(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _stock_triple(code: str) -> dict:
    """个股三件套：{turn(换手%), amount_yi(成交额亿), chg5(五日涨幅%)}。缺数据字段=None。"""
    q = _qt_batch([code]).get(code) or {}
    turn = q.get("turn")
    amount_yi = q.get("amount_yi")
    # 五日涨幅：腾讯日K（前复权）最近 6 根 → (第6根收盘 vs 第1根收盘) 隐含 5 个交易日
    chg5 = None
    try:
        import sys as _sys
        _rp = str(ROOT_DIR)
        if _rp not in _sys.path:
            _sys.path.insert(0, _rp)   # gen_dashboard 运行时 cwd=HTML/，common 包在项目根，必须显式加
        from common.tencent_kline import fetch_kline
        kl = fetch_kline(code, n=6)
        if len(kl) >= 2:
            c0 = kl[-6][1] if len(kl) >= 6 else kl[0][1]
            c1 = kl[-1][1]
            if c0 and c1:
                chg5 = round((c1 / c0 - 1) * 100, 2)
    except Exception:
        pass
    return {"turn": turn, "amount_yi": amount_yi, "chg5": chg5}


def _stock_triple_html(w) -> str:
    """单只辨识度个股三件套 HTML（换手蓝/成交额中性/五日涨幅红涨绿跌）+ 关联性标注。"""
    code = (w.get("code") or "").split(".")[0]
    name = esc(w.get("name") or "")
    t = _stock_triple(code)
    turn = t.get("turn")
    amt = t.get("amount_yi")
    chg5 = t.get("chg5")
    # 换手健康度标注（关联性：板块强 + 个股换手健康 = 可跟）
    flag = ""
    if turn is not None:
        if turn < 3:
            flag = '<span style="color:#1aa260">⚠缩量</span>'
        elif turn > 30:
            flag = '<span style="color:#e23c3c">⚠分歧</span>'
        else:
            flag = '<span style="color:#e23c3c">✓健康</span>'
    turn_txt = f"{turn:.1f}%" if turn is not None else "—"
    amt_txt = f"{amt:.1f}亿" if amt is not None else "—"
    chg5_txt = f"{chg5:+.1f}%" if chg5 is not None else "—"
    chg5_cls = "up" if (chg5 or 0) >= 0 else "down"
    return (f'<div style="display:flex;gap:6px;align-items:center;font-size:11px;'
            f'padding:2px 0;border-bottom:1px dashed var(--line,#eee)">'
            f'<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{name}</span>'
            f'<span style="color:#3b82c4">换{turn_txt}</span>{flag}'
            f'<span style="color:var(--sub,#86868b)">额{amt_txt}</span>'
            f'<span class="{chg5_cls}">5日{chg5_txt}</span></div>')


def _last_val(series):
    """取序列最近一个非空值（资金净额/板数通用）。"""
    for v in reversed(series or []):
        if v is not None:
            return v
    return None


def _vchart(values, labels=None, h=88, unit="", colors=None):
    """CSS 垂直柱状图（2026-08-21 用户「多日对比用柱状图」）：
    柱高=数值/最大绝对值（区分大小）；红=正(流入/今强)，绿=负(流出/今弱)，蓝=中性，灰=缺失；
    colors 传入时按位置覆盖颜色类（up/down/neu），用于昨日/今日等非正负对比场景。
    values: list[float|None]；labels: 等长短标签（显示在柱底）。"""
    vals = list(values or [])
    if not vals:
        return ""
    mx = max([abs(v) for v in vals if v is not None] + [0.01])
    cells = []
    for i, v in enumerate(vals):
        lab = ""
        if labels and i < len(labels) and labels[i]:
            lab = esc(str(labels[i]))
        if v is None:
            cells.append(f'<div class="vc" style="flex:1"><div class="vbar null" title="无数据"></div>'
                         f'<div class="vlab">{lab}</div></div>')
        else:
            ht = max(3, abs(v) / mx * 100)
            clr = (colors[i] if (colors and i < len(colors) and colors[i])
                   else "up" if v > 0 else "down" if v < 0 else "neu")
            tip = f"{v:+.2f}{unit}"
            if clr == "fill":
                tip += "（成分股补全·zt池核验）"
            elif clr == "fill dn":
                tip += "（成分股补全·zt池核验）"
            cells.append(f'<div class="vc" style="flex:1"><div class="vbar {clr}" style="height:{ht:.0f}%" '
                         f'title="{tip}"></div><div class="vlab">{lab}</div></div>')
    return '<div class="vchart">' + "".join(cells) + '</div>'


def _nm_overlap(a, b, minlen=2):
    """板块名是否相关：a/b 单边包含，或存在 ≥minlen 公共中文子串（液冷概念↔液冷服务器 类差异）。
    供候选主题 × 今日实时资金榜交叉（板块名体系差异：概念名 vs 东财名）。
    ⚠️ 2026-09-02 修复：先剥泛尾缀（概念/板块/指数）再匹配——否则「粮食概念」与「华为概念」
    因公共子串「概念」误判同族（粮食今日不在流出榜却被误标）。剥后液冷概念→液冷 仍匹配液冷服务器。"""
    a0, b0 = (a or "").strip(), (b or "").strip()
    for _s in ("概念", "板块", "指数"):
        if len(a0) > len(_s) and a0.endswith(_s):
            a0 = a0[:-len(_s)]
        if len(b0) > len(_s) and b0.endswith(_s):
            b0 = b0[:-len(_s)]
    if not a0 or not b0:
        return False
    if a0 in b0 or b0 in a0:
        return True
    for i in range(max(0, len(a0) - minlen + 1)):
        if a0[i:i + minlen] in b0:
            return True
    return False


def render_theme_return(tr, d=None):
    """题材回流预测卡（2026-08-21 改版·用户「图表看不懂，要流入流出的容易懂」）：
    去掉 spark 迷你条/置信度进度条等图形化，改为纯数字卡片——
    每个候选板块直接展示「资金净额」（最近交易日，红=流入/绿=流出）+ 板数趋势文字 + 冷却/触发/催化。

    2026-09-02 盘中实时增强（用户「盘中也要网页显示今天的量能流入流出」）：
    d=latest.json（守护链 30s 刷新）→ 卡顶「⚡ 今日实时资金流」面板（流入/流出 Top5·红入绿出·时间戳）
    + 候选与今日资金榜交叉标注（theme×inflow/outflow 公共子串匹配：
    ⚡今日资金流入榜=回流即时信号 / ⛔今日资金流出榜=回流未至）；d 为空容错只显示盘后序列。"""
    if not tr:
        return ""
    cs = tr.get("candidates") or []
    if not cs:
        return ""
    date = tr.get("date", "")
    days = tr.get("data_days") or []
    FLOW_CN = {"inflow_rising": "流入递增", "outflow_shrinking": "流出收敛",
               "outflow_widening": "流出放大", "neutral": "资金中性"}
    PH_CN = {"re-entry-candidate": "回流候选", "re-entry": "回流确认", "boom": "今日爆发",
             "cooling": "退潮中", "no-reentry": "资金未回流", "dead": "已死", "watch": "观察"}
    fire_phases = {"re-entry-candidate", "re-entry", "boom"}
    UP, DN = "#e23c3c", "#1aa260"  # 涨红/跌绿（A股语义）
    _today_fmt = now_bj().strftime("%Y-%m-%d")  # 2026-08-25 用户：资金回流不显示当天
    # —— ⚡ 今日实时资金面板（2026-09-02 用户「盘中也要显示今天量能流入流出」）——
    # d=latest.json 守护链 30s 刷新（ts=今日实时）；与下方 theme_rotation 盘后序列（date=昨日）
    # 形成「盘后预测 × 今日实时」双时间轴。d 为空（看板首次生成/无快照）→ 不渲染不报错。
    today_panel = ""
    today_in_names, today_out_names = set(), set()
    if d:
        _infl = (d.get("sector_risk") or {}).get("inflow_top") or []
        _outfl = (d.get("sector_risk") or {}).get("outflow_top") or []
        if _infl or _outfl:
            ts = str(d.get("ts") or "")[11:16]
            today_in_names = {str(x.get("name") or "") for x in _infl}
            today_out_names = {str(x.get("name") or "") for x in _outfl}
            in_html = ("、".join(
                f"<span style='color:{UP};font-weight:700'>{esc(x.get('name'))} "
                f"{x.get('flow_yi') or 0:+.1f}亿</span>" for x in _infl[:5])
                if _infl else "<span style='color:#86868b'>—</span>")
            out_html = ("、".join(
                f"<span style='color:{DN};font-weight:700'>{esc(x.get('name'))} "
                f"{x.get('flow_yi') or 0:+.1f}亿</span>" for x in _outfl[:5])
                if _outfl else "<span style='color:#86868b'>—</span>")
            today_panel = (
                '<div style="background:linear-gradient(135deg,#fff5f5,#f4f9ff);'
                'border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:10px">'
                f'<div style="font-weight:800;font-size:13px">⚡ 今日实时资金流'
                f'<span style="font-size:10px;color:#86868b;font-weight:400">（截至 {ts} · 30s 刷新）</span></div>'
                f'<div style="font-size:11px;margin-top:6px;line-height:2">📈 流入：{in_html}</div>'
                f'<div style="font-size:11px;line-height:2">📉 流出：{out_html}</div>'
                '<div style="font-size:10px;color:#86868b">盘后回流序列（下方·date=昨日收盘）与之衔接：'
                '候选今日在流入榜 = 回流即时信号；在流出榜 = 回流未至</div></div>')
    # 柱状图日期标签（与 flow_vals 尾部对齐·2026-08-25 用户「资金回流不要显示当天·
    # 当天未知显示没数据尴尬」）：flow_vals = D-3~D-1 纯历史窗口（theme_rotation 已剔除
    # 今日），标签取 days 中**不含今天**的最后 n 个交易日——禁止把「今天」标进图表
    # 导致 None 柱/数字被误读为当天数据。
    def _flow_labels(fvals):
        n = len(list(fvals or []))
        if not n or not days:
            return []
        hist = [d for d in days if d != _today_fmt]  # 剔除今日（当天资金未知）
        return [d[5:] for d in hist[-n:]] if hist else []

    def card(c):
        phase = c.get("phase", "")
        pc = PH_CN.get(phase, phase)
        conf_pct = int((c.get("confidence") or 0) * 100)
        cooled = c.get("cooled_days")
        cooled_txt = (f"冷却 {cooled} 天" if (cooled is not None and cooled > 0) else "今日现身")
        catalyst = esc(c.get("catalyst") or "")
        trigger = esc(c.get("trigger") or "")
        wp = c.get("watch_pool") or []
        wp_names = "、".join(esc(w.get("name")) for w in wp[:4]) if wp else ""
        # 辨识度个股三件套（2026-08-26 用户「板块与辨识度个股要有关联性」）：
        # 每个候选板块展开 Top3（换手健康优先），展示 换手/成交额/五日涨幅
        triple_html = ""
        if wp:
            def _turn_key(w):
                t = _qt_batch([(w.get("code") or "").split(".")[0]]).get((w.get("code") or "").split(".")[0]) or {}
                tv = t.get("turn")
                return (0 if (tv is not None and 3 <= tv <= 25) else 1, -(tv if tv is not None else 0))
            top3 = sorted(wp[:6], key=_turn_key)[:3]
            triple_html = ('<div style="margin-top:6px;font-size:11px">'
                           '<div style="color:#8b5cf6;font-weight:600;margin-bottom:2px">🔎 辨识度个股 Top3（换手/成交额/5日涨幅）</div>'
                           + "".join(_stock_triple_html(w) for w in top3) + '</div>')
        # 资金净额（最近非空值·D-3~D-1 历史窗口）→ 红=流入 / 绿=流出，最显眼
        # 2026-08-25 用户「资金回流不用显示当天·当天未知显示没数据尴尬」：
        # 只展示前 3 个历史交易日（当天结果未知不展示）；3 天均未上榜 → 显示「近3日未上榜」
        # 而非「暂无数据」（数据存在·只是该题材没进资金榜 Top）。
        fv = _last_val(c.get("flow_vals"))
        ft = c.get("flow_trend") or "neutral"
        if fv is None:
            flow_html = ('<div style="margin:6px 0"><b>💰 资金净额</b>：'
                         '<span style="color:#86868b">近3日未上榜</span> '
                         f'<span style="font-size:11px;color:#86868b">{FLOW_CN.get(ft, "")}</span></div>')
        else:
            fv_f = f"{fv:+.2f} 亿"
            fv_c = UP if fv > 0 else (DN if fv < 0 else "#86868b")
            fv_arrow = "📈" if fv > 0 else "📉"
            flow_html = (f'<div style="margin:6px 0"><b>💰 资金净额</b>：'
                         f'<span style="color:{fv_c};font-weight:800;font-size:15px">{fv_arrow} {fv_f}</span> '
                         f'<span style="font-size:11px;color:#86868b">({FLOW_CN.get(ft, "")})</span></div>')
        # 资金多日柱状图（2026-08-21 用户「柱状图：柱高=大小，红=流入/绿=流出」）
        fvals = c.get("flow_vals") or []
        chart_html = _vchart(fvals, _flow_labels(fvals), unit="亿")
        # 板数趋势柱状图（2026-09-02 量轴可视化·与资金柱同 data_days 尾部轴）：
        # 实心红=eruption 原生日有涨停；虚线空心=eruption 缺席日经 zt 池成分股补全（title 标注）；
        # 灰=缺失（zt_history 当日无全量）。补全日=成分股真实在涨停池·数值可信但源不同。
        zvals = list(c.get("zt_series") or [])
        zfill = c.get("zt_filled") or {}
        hist_dates = [d for d in days if d != _today_fmt]
        zhist = hist_dates[-len(zvals):] if (hist_dates and zvals) else []
        zcols = []
        for _zi, _z in enumerate(zvals):
            if _z is None:
                zcols.append(None)
            elif _zi < len(zhist) and zhist[_zi] in zfill:
                zcols.append("fill")
            elif _z > 0:
                zcols.append("up")
            else:
                zcols.append("neu")
        zchart_html = _vchart(zvals, labels=[d[5:] for d in zhist], unit="板", colors=zcols)
        zt_dir_cn = _zt_dir_cn(c.get("zt_dir"))
        # 数据质量角标（2026-09-02 用户「统计口径统一·逻辑可追溯」）：量/钱有效天数·补全日计数
        zqv = c.get("zt_valid_days")
        fqv = sum(1 for v in fvals if v is not None)
        nfill = len(zfill)
        q_badge = f'<span style="float:right;font-size:10px;color:#86868b;font-weight:400">' \
                  f'量 {zqv}/5{("·补" + str(nfill)) if nfill else ""} ｜ 钱 {fqv}/5</span>'
        # ⚡ 今日实时资金交叉（2026-09-02 用户「盘中显示今天量能流入流出」）：
        # 候选主题名 × 今日 inflow/outflow 榜（公共子串匹配·液冷概念↔液冷服务器）
        today_tag = ""
        if d:
            _tn = str(c.get("theme") or "")
            if any(_nm_overlap(_tn, b) for b in today_in_names):
                today_tag = ('<div style="font-size:11px;margin:4px 0"><span style="color:#e23c3c;font-weight:700">'
                             '⚡ 今日资金流入榜</span>'
                             '<span style="font-size:10px;color:#86868b">（实时·回流即时信号）</span></div>')
            elif any(_nm_overlap(_tn, b) for b in today_out_names):
                today_tag = ('<div style="font-size:11px;margin:4px 0"><span style="color:#1aa260;font-weight:700">'
                             '⛔ 今日资金流出榜</span>'
                             '<span style="font-size:10px;color:#86868b">（实时·回流未至）</span></div>')
        wp_html = f'<div style="font-size:11px;color:#8b5cf6;margin-top:4px">👀 跟踪：{wp_names}</div>' if wp_names else ""
        return ('<div style="background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px">'
                f'<div style="font-weight:800;font-size:13px">{esc(c.get("theme"))} '
                f'<span style="color:#8b5cf6;font-size:11px;font-weight:600">{pc}</span>'
                f'<span style="float:right;font-size:11px;color:#86868b;font-weight:400">置信 {conf_pct}%</span></div>'
                f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:-2px">{q_badge}</div>'
                f'{today_tag}'
                f'{flow_html}'
                f'{chart_html}'
                f'<div style="font-size:11px;color:var(--sub,#3c4043);margin-top:4px">📊 板块涨停数'
                f'<span style="color:#86868b;font-size:10px">（{zt_dir_cn}·红实=原生日 虚=成分股补全 灰=缺失）</span></div>'
                f'{zchart_html}'
                f'<div style="font-size:11px;color:var(--sub,#86868b);margin-top:2px">⏳ {cooled_txt} ｜ ⚡ {trigger}</div>'
                + (f'<div style="font-size:11px;color:var(--sub,#86868b);margin-top:2px">📰 {catalyst}</div>' if catalyst else "")
                + wp_html + triple_html + '</div>')

    fire = [c for c in cs if c.get("phase") in fire_phases]
    cool = [c for c in cs if c.get("phase") not in fire_phases]
    parts = []
    if today_panel:
        parts.append(today_panel)          # ⚡ 今日实时资金面板置顶（盘中 30s 刷新）
    if fire:
        parts.append('<div class="grp-title fire">🔥 即将被资金重新激活</div>')
        parts.append('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px">'
                     + "".join(card(c) for c in fire) + '</div>')
    if cool:
        parts.append('<div class="grp-title cool" style="margin-top:12px">❄️ 正在冷却 / 观望</div>')
        parts.append('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px">'
                     + "".join(card(c) for c in cool) + '</div>')
    return ('<div class="card ret section"><h2>🔄 题材回流预测（盘后·' + esc(date) + '）</h2>'
            + "".join(parts)
            + '<div class="sum">💰 资金净额=最近交易日板块主力净流入（红=流入 / 绿=流出）；回流=冷却后资金二次点火，'
              '盘中竞价高开+资金转流入即确认，龙头破五日线放弃</div></div>')


# ===== 数据记录 Tab（2026-08-25 用户「记录用网页回显·方便查看」）=====
def render_data_record():
    """📚 数据记录 Tab：把本地沉淀数据（JSON）直接回显成表格，方便每天查看数据走向。
    数据源（全部本地·无外部依赖）：
      ① cache/short_term/summary_5d.json —— 10日情绪轨迹（summary10 段）
      ② cache/sector_flow_history/*.json   —— 板块资金流历史（近5日 Top 流入/流出）
      ③ cache/zt_history/*.json            —— 每日涨停统计（涨停数/最高板/题材）
      ④ cache/theme_sequence.json          —— 今日题材轮动序列
    """
    today = now_bj().strftime("%Y-%m-%d")
    parts = []

    # ── ① 10日情绪轨迹表 ──
    try:
        s5 = json.loads((ROOT_DIR / "cache" / "short_term" / "summary_5d.json").read_text(encoding="utf-8"))
        s10 = s5.get("summary10") or {}
        days10 = s10.get("days") or []
        cycle = esc(s10.get("cycle") or s5.get("cycle_hint") or "")
        if days10:
            rows = []
            for r in days10:
                sc = r.get("sentiment_score")
                sc_c = ""
                sc_txt = "—"
                if sc is not None:
                    sc = float(sc)
                    sc_txt = f"{sc:.1f}" if sc != int(sc) else str(int(sc))
                    if sc >= 6:
                        sc_c = "up"
                    elif sc <= 2:
                        sc_c = "down"
                    else:
                        sc_c = "hold"
                pr = r.get("promote_rate")
                pr_txt = f"{pr:.1f}%" if pr is not None else "—"
                nk = r.get("nuke_count")
                nk_txt = str(nk) if nk is not None else "—"
                nk_c = "up" if (nk is not None and nk <= 5) else ("down" if (nk is not None and nk >= 15) else "")
                mb = r.get("max_boards")
                mb_txt = f"{mb}板" if mb else "—"
                lbl = esc(r.get("sentiment_label") or "")
                rows.append(
                    f'<tr><td class="date">{esc((r.get("date") or "")[5:])}</td>'
                    f'<td class="num {sc_c}">{sc_txt}</td>'
                    f'<td style="text-align:center">{lbl}</td>'
                    f'<td class="num">{pr_txt}</td>'
                    f'<td class="num {nk_c}">{nk_txt}</td>'
                    f'<td class="num">{mb_txt}</td></tr>'
                )
            parts.append(
                '<div class="card section"><h2 style="font-size:17px;margin-bottom:8px">📈 10日情绪轨迹（本地沉淀）</h2>'
                f'<div class="scroll"><table class="tbl"><thead><tr>'
                '<th>日期</th><th class="num">情绪分</th><th>定性</th><th class="num">晋级率</th>'
                '<th class="num">核按钮</th><th class="num">最高板</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>'
                f'<div class="legend" style="margin-top:8px">周期：<b>{cycle}</b> · 数据源 summary_5d.json（10日归纳窗口）</div></div>'
            )
    except Exception as e:
        parts.append(f'<div class="error">情绪轨迹读取失败: {esc(str(e))}</div>')

    # ── ② 板块资金流历史（近5日 Top 流入/流出）──
    try:
        files = sorted((ROOT_DIR / "cache" / "sector_flow_history").glob("sector_flow_*.json"))
        if files:
            flow_rows = []
            for f in files[-5:]:
                sf = json.loads(f.read_text(encoding="utf-8"))
                boards = sf.get("boards") or {}
                date_s = esc((sf.get("date") or f.stem[-10:])[5:])
                ins = sorted(boards.items(), key=lambda x: -(x[1].get("flow_yi") or 0))[:3]
                outs = sorted(boards.items(), key=lambda x: (x[1].get("flow_yi") or 0))[:2]
                in_txt = "、".join(f'{esc(k)} <b class="up">+{v.get("flow_yi"):.1f}</b>' for k, v in ins) if ins else "—"
                out_txt = "、".join(f'{esc(k)} <b class="down">{v.get("flow_yi"):.1f}</b>' for k, v in outs) if outs else "—"
                flow_rows.append(
                    f'<tr><td class="date">{date_s}</td><td>{in_txt}</td><td>{out_txt}</td></tr>'
                )
            parts.append(
                '<div class="card section" style="margin-top:12px"><h2 style="font-size:17px;margin-bottom:8px">💸 板块资金流历史（近5日）</h2>'
                '<div class="scroll"><table class="tbl"><thead><tr>'
                '<th>日期</th><th>主力净流入 Top3</th><th>主力净流出 Top2</th></tr></thead>'
                f'<tbody>{"".join(flow_rows)}</tbody></table></div>'
                '<div class="legend" style="margin-top:8px">单位：亿 · 数据源 cache/sector_flow_history/</div></div>'
            )
    except Exception as e:
        parts.append(f'<div class="error">资金流历史读取失败: {esc(str(e))}</div>')

    # ── ③ 每日涨停统计（zt_history）──
    try:
        zt_files = sorted((ROOT_DIR / "cache" / "zt_history").glob("zt_*.json"))
        if zt_files:
            zt_rows = []
            for f in zt_files[-8:]:
                z = json.loads(f.read_text(encoding="utf-8"))
                date_s = esc((z.get("date") or "")[5:])
                zc = z.get("zt_count")
                mb = z.get("max_boards")
                mb_txt = f"{mb}板" if mb else "—"
                zt_rows.append(
                    f'<tr><td class="date">{date_s}</td><td class="num">{zc}</td>'
                    f'<td class="num">{mb_txt}</td></tr>'
                )
            parts.append(
                '<div class="card section" style="margin-top:12px"><h2 style="font-size:17px;margin-bottom:8px">🔥 每日涨停统计（zt_history）</h2>'
                '<div class="scroll"><table class="tbl"><thead><tr>'
                '<th>日期</th><th class="num">涨停数</th><th class="num">最高板</th></tr></thead>'
                f'<tbody>{"".join(zt_rows)}</tbody></table></div>'
                '<div class="legend" style="margin-top:8px">数据源 cache/zt_history/ · 每多一个归档文件自动多一行</div></div>'
            )
    except Exception as e:
        parts.append(f'<div class="error">涨停统计读取失败: {esc(str(e))}</div>')

    # ── ④ 今日题材轮动序列 ──
    try:
        ts = json.loads((ROOT_DIR / "cache" / "theme_sequence.json").read_text(encoding="utf-8"))
        themes = ts.get("themes") or []
        if ts.get("date") == today and themes:
            parts.append(
                '<div class="card section" style="margin-top:12px"><h2 style="font-size:17px;margin-bottom:8px">🔄 今日题材轮动序列</h2>'
                f'<div class="meta">{"、".join(f"<span class=\"rec\">{esc(t)}</span>" for t in themes)}</div>'
                f'<div class="legend" style="margin-top:6px">首见顺序 · 更新 {esc(ts.get("updated") or "")}</div></div>'
            )
    except Exception:
        pass

    if not parts:
        return ""
    return "".join(parts)


# ===== 数据堆叠 Tab（2026-08-25 用户「数据堆叠可以单独开一个 tab」）=====
def render_data_stack():
    """📊 数据堆叠 Tab：多日数据堆叠对比（ECharts 堆叠柱状图）。
    数据源（全部本地）：
      ① cache/sector_flow_history/*.json —— 近5日板块资金流（取跨日 Top 板块·堆叠柱）
      ② cache/zt_history/*.json            —— 近5日题材涨停数（跨日并集题材·堆叠柱）
    """
    today = now_bj().strftime("%Y-%m-%d")
    parts = []

    # ── ① 资金流堆叠 ──
    try:
        files = sorted((ROOT_DIR / "cache" / "sector_flow_history").glob("sector_flow_*.json"))
        if files:
            days = []
            day_labels = []
            per_sector = {}
            for f in files[-5:]:
                sf = json.loads(f.read_text(encoding="utf-8"))
                ds = (sf.get("date") or f.stem[-10:])
                day_labels.append(ds[5:])
                days.append(ds)
                for name, info in (sf.get("boards") or {}).items():
                    per_sector.setdefault(name, {})[ds] = info.get("flow_yi") or 0
            # 取跨日累计净流入 Top6（更稳定）
            ranked = sorted(per_sector.items(), key=lambda x: -sum(x[1].values()))[:6]
            series = []
            for name, vals in ranked:
                data = [vals.get(d, 0) for d in days]
                series.append({"name": name, "data": data})
            if series:
                import html as _h
                parts.append(
                    '<div class="card section"><h2 style="font-size:17px;margin-bottom:8px">💸 板块资金流堆叠（近5日）</h2>'
                    f'<div id="stack-flow-chart" class="chart" data-series="{_h.escape(json.dumps(series, ensure_ascii=False))}" data-labels="{_h.escape(json.dumps(day_labels))}"></div>'
                    '<div class="legend" style="margin-top:8px">柱高=主力净流入(亿)· 堆叠看板块资金体量对比 · 数据源 sector_flow_history/</div></div>'
                )
    except Exception as e:
        parts.append(f'<div class="error">资金流堆叠读取失败: {esc(str(e))}</div>')

    # ── ② 题材涨停数堆叠 ──
    try:
        from theme_data import norm_theme
        zt_files = sorted((ROOT_DIR / "cache" / "zt_history").glob("zt_*.json"))
        if zt_files:
            days = []
            day_labels = []
            per_theme = {}
            for f in zt_files[-5:]:
                z = json.loads(f.read_text(encoding="utf-8"))
                ds = z.get("date") or ""
                day_labels.append(ds[5:])
                days.append(ds)
                # 与 build_series 同源：从 zt_list 逐股 sector → norm_theme 归并（收盘题材）
                for it in (z.get("zt_list") or []):
                    lab = norm_theme(it.get("sector"))
                    if not lab or lab == "其他":
                        continue
                    per_theme.setdefault(lab, {})[ds] = per_theme.get(lab, {}).get(ds, 0) + 1
            ranked = sorted(per_theme.items(), key=lambda x: -sum(x[1].values()))[:8]
            series = []
            for name, vals in ranked:
                data = [vals.get(d, 0) for d in days]
                series.append({"name": name, "data": data})
            if series:
                import html as _h
                parts.append(
                    '<div class="card section" style="margin-top:12px"><h2 style="font-size:17px;margin-bottom:8px">🔥 题材涨停数堆叠（近5日）</h2>'
                    f'<div id="stack-theme-chart" class="chart" data-series="{_h.escape(json.dumps(series, ensure_ascii=False))}" data-labels="{_h.escape(json.dumps(day_labels))}"></div>'
                    '<div class="legend" style="margin-top:8px">柱高=题材涨停家数 · 堆叠看题材强度对比 · 数据源 zt_history/（与主线温度计同源）</div></div>'
                )
    except Exception as e:
        parts.append(f'<div class="error">题材堆叠读取失败: {esc(str(e))}</div>')

    if not parts:
        return ""
    return "".join(parts)


def _stack_charts_js():
    """数据堆叠 Tab 的 ECharts 初始化 JS（懒初始化：切到该 Tab 才 init）。
    数据从 DOM data-series / data-labels 属性读取（render 时内联注入·file:// 安全）。"""
    return """
function ensureStackCharts(){
  var COLORS = ['#e23c3c','#f0a020','#8b5cf6','#3b82c4','#1aa260','#2dd4bf','#ff9aa2','#a78bfa'];
  ['stack-flow-chart','stack-theme-chart'].forEach(function(id){
    var el = document.getElementById(id);
    if (!el || el.offsetWidth === 0) return;
    if (echarts.getInstanceByDom(el)) return;
    var series, labels;
    try { series = JSON.parse(el.getAttribute('data-series')||'[]'); }
    catch(e){ return; }
    try { labels = JSON.parse(el.getAttribute('data-labels')||'[]'); }
    catch(e){ return; }
    if (!series.length) return;
    var ch = echarts.init(el);
    ch.setOption({
      tooltip: {trigger:'axis', axisPointer:{type:'shadow'}},
      legend: {type:'scroll', bottom:0, textStyle:{fontSize:10}},
      grid: {left:45, right:15, top:30, bottom:45},
      xAxis: {type:'category', data:labels, axisLabel:{fontSize:10}},
      yAxis: {type:'value', axisLabel:{fontSize:10}},
      series: series.map(function(s,i){
        return {name:s.name, type:'bar', stack:'total', data:s.data,
                itemStyle:{color: COLORS[i%COLORS.length]},
                barMaxWidth: 26, emphasis:{focus:'series'}};
      })
    });
  });
}
"""


# ===== 📊 资金监控 Tab（2026-09-08 用户：ECharts 全局板块资金流入流出监控）=====
def _load_sector_flow_snapshot():
    """读取板块资金快照：优先当日 sector_flow_{today}.json，否则取最近交易日。

    返回 (date, generated_ts, items) —— items 按主力净额降序：
    {'name','pct','flow'(亿),'tx5','tx10'}。失败返回 (None, None, [])。
    """
    try:
        hist = ROOT_DIR / "cache" / "sector_flow_history"
        files = sorted(hist.glob("sector_flow_*.json"))
        if not files:
            return None, None, []
        today = now_bj().strftime("%Y-%m-%d")
        pick = None
        for f in files:
            if f.stem[-10:] == today:
                pick = f
                break
        if pick is None:
            pick = files[-1]
        sf = json.loads(pick.read_text(encoding="utf-8"))
        items = []
        for name, info in (sf.get("boards") or {}).items():
            try:
                flow = float(info.get("flow_yi") or 0.0)
            except (TypeError, ValueError):
                continue
            items.append({
                "name": str(name),
                "pct": round(float(info.get("pct") or 0.0), 2),
                "flow": round(flow, 1),
                "tx5": round(float(info.get("tx_5d_yi") or 0.0), 1),
                "tx10": round(float(info.get("tx_10d_yi") or 0.0), 1),
            })
        items.sort(key=lambda x: -x["flow"])
        return (sf.get("date") or pick.stem[-10:]), (sf.get("generated_ts") or ""), items
    except Exception as e:
        print("[fund_monitor] 快照读取失败:", e)
        return None, None, []


def render_fund_monitor():
    """📊 资金监控 Tab：全市场板块主力资金流入/流出 ECharts 全景（红流入 / 绿流出）。

    数据源 cache/sector_flow_history/sector_flow_{date}.json（收盘沉淀，盘中沿用最近交易日）。
    结构：KPI 概览 → 净流入 Top25 → 净流出 Top25 → 全板块双向条形（dataZoom）。
    """
    date_s, gen_ts, items = _load_sector_flow_snapshot()
    if not items:
        return ('<div class="card section ffmon"><h2>📊 板块资金监控</h2>'
                '<div class="stat">暂无板块资金快照（cache/sector_flow_history/）· 收盘沉淀后自动生成</div></div>')

    inflow = [x for x in items if x["flow"] > 0]
    outflow = [x for x in items if x["flow"] < 0]
    net = sum(x["flow"] for x in items)
    net_cls = "up" if net >= 0 else "down"
    top_in = inflow[0] if inflow else None
    top_out = outflow[-1] if outflow else None
    gen_txt = str(gen_ts)[:19].replace("T", " ") if gen_ts else ""

    def kpi(n_html, label, color=None):
        c = f' style="color:{color}"' if color else ""
        return f'<div class="kpi"><div class="n"{c}>{n_html}</div><div class="l">{label}</div></div>'

    kpis = "".join([
        kpi(str(len(inflow)), "主力净流入板块", "var(--up)"),
        kpi(str(len(outflow)), "主力净流出板块", "var(--down)"),
        kpi(f'{net:+.0f}', f'合计净额(亿·{len(items)}板块·概念重叠口径)',
            "var(--up)" if net >= 0 else "var(--down)"),
        kpi(f'{esc(top_in["name"])}<small> +{top_in["flow"]:.0f}亿</small>', "最强流入板块", "var(--up)") if top_in else "",
        kpi(f'{esc(top_out["name"])}<small> {top_out["flow"]:.0f}亿</small>', "最大流出板块", "var(--down)") if top_out else "",
    ])

    raw_json = esc(json.dumps(items, ensure_ascii=False))
    all_h = max(360, min(1400, len(items) * 3 + 80))
    body = (
        '<div class="card section ffmon">'
        f'<h2>📊 板块资金监控 <span class="sub">｜ {esc(date_s)} · 主力净流入(亿) · 全市场 {len(items)} 个板块</span></h2>'
        f'<div class="kpis">{kpis}</div>'
        f'<div id="fundmon-raw" data-items="{raw_json}" style="display:none"></div>'
        '<div class="blk"><h3><span class="dot" style="background:#ee2500"></span>主力净流入 Top 25</h3>'
        '<div class="sub">今日资金主线：钱往哪里去</div>'
        '<div id="fundmon-in-chart" class="chart" style="height:560px"></div></div>'
        '<div class="blk"><h3><span class="dot" style="background:#0a8f3c"></span>主力净流出 Top 25</h3>'
        '<div class="sub">今日失血板块：钱从哪里撤</div>'
        '<div id="fundmon-out-chart" class="chart" style="height:560px"></div></div>'
        f'<div class="blk"><h3>全板块资金全景（{len(items)} 个 · 拖动下方滑条查看全部）</h3>'
        '<div class="sub">红=流入 绿=流出 · 双向条形 · 按净额排序（数值越大越长）</div>'
        f'<div id="fundmon-all-chart" class="chart" style="height:{all_h}px"></div></div>'
        f'<div class="src">数据源：东财板块口径（概念含重叠）· 沉淀 {esc(gen_txt)}｜红=流入 · 绿=流出（A股配色铁律）'
        ' · 盘中沿用最近收盘快照（后续迭代盘中动态）</div>'
        '</div>'
    )
    return body


def _fund_monitor_js():
    """资金监控 Tab 的 ECharts 懒初始化 JS（切到该 Tab 才 init，数据来自 DOM data-items）。"""
    return """
function ensureFundMonCharts(){
  var UP='#ee2500', DOWN='#0a8f3c';
  var host = document.getElementById('fundmon-raw');
  if (!host) return;
  var RAW;
  try { RAW = JSON.parse(host.getAttribute('data-items')||'[]'); } catch(e){ return; }
  if (!RAW.length) return;
  var fmt = function(v){ return (v>0?'+':'') + v.toFixed(1); };
  function tip(p){
    var n = p[0].name, d = null;
    for (var i=0;i<RAW.length;i++){ if (RAW[i].name === n){ d = RAW[i]; break; } }
    if (!d) return n;
    return '<b>'+d.name+'</b><br/>主力净额：<span style="color:'+(d.flow>=0?UP:DOWN)+'">'+fmt(d.flow)+' 亿</span>'+
      '<br/>涨跌幅：'+(d.pct>=0?'+':'')+d.pct.toFixed(2)+'%'+
      '<br/>近5日资金：'+fmt(d.tx5)+' 亿｜近10日：'+fmt(d.tx10)+' 亿';
  }
  function mkBar(id, list, axisName, withLabel){
    var el = document.getElementById(id);
    if (!el || el.offsetWidth === 0) return;
    if (echarts.getInstanceByDom(el)) return;
    var asc = list.slice().reverse();
    var ch = echarts.init(el);
    ch.setOption({
      tooltip: {trigger:'axis', axisPointer:{type:'shadow'}, formatter: tip},
      grid: {left:8, right:70, top:6, bottom:6, containLabel:true},
      xAxis: {type:'value', name:axisName, nameTextStyle:{color:'#999', fontSize:10},
              axisLabel:{fontSize:10, color:'#888'}, splitLine:{lineStyle:{color:'#eef0f4'}}},
      yAxis: {type:'category', data: asc.map(function(x){return x.name;}),
              axisLabel:{fontSize:11, color:'#444'}, axisLine:{show:false}, axisTick:{show:false}},
      series: [{type:'bar', data: asc.map(function(x){return x.flow;}), barWidth:'62%',
        itemStyle:{color:function(p){return p.value>=0?UP:DOWN;}, borderRadius:[0,3,3,0]},
        label: withLabel ? {show:true, position:'right', fontSize:10, color:'#666',
               formatter:function(p){return fmt(p.value)+' 亿';}} : {show:false}}]
    });
  }
  function mkAll(){
    var el = document.getElementById('fundmon-all-chart');
    if (!el || el.offsetWidth === 0) return;
    if (echarts.getInstanceByDom(el)) return;
    var asc = RAW.slice().reverse();
    var ch = echarts.init(el);
    ch.setOption({
      tooltip: {trigger:'axis', axisPointer:{type:'shadow'}, formatter: tip},
      grid: {left:8, right:60, top:6, bottom:60, containLabel:true},
      xAxis: {type:'value', name:'主力净额(亿)', nameTextStyle:{color:'#999', fontSize:10},
              axisLabel:{fontSize:10, color:'#888'}, splitLine:{lineStyle:{color:'#eef0f4'}}},
      yAxis: {type:'category', data: asc.map(function(x){return x.name;}), show:false},
      dataZoom: [{type:'slider', height:16, bottom:8, start:0, end:12}, {type:'inside'}],
      series: [{type:'bar', data: asc.map(function(x){return x.flow;}),
        itemStyle:{color:function(p){return p.value>=0?UP:DOWN;}}, label:{show:false}}]
    });
  }
  var pos = RAW.filter(function(x){return x.flow>0;}).slice(0,25);
  var neg = RAW.filter(function(x){return x.flow<0;}).slice(-25).reverse();
  mkBar('fundmon-in-chart', pos, '净流入(亿)', true);
  mkBar('fundmon-out-chart', neg, '净流出(亿)', true);
  mkAll();
}
"""


# ===== 核按钮/跌停预警卡（数据源：feedback_matrix + latest.json）=====
def render_nuke_alert(d, fb):
    """核按钮/跌停预警：实时跌停/炸板 + 盘后核按钮列表（含破位分级）。
    breakdown=真破位(红警) / break_ma5=仅分歧回踩(不报恶劣)。"""
    ld = (d.get("limit_down") or {}).get("count") or 0
    broken = (d.get("broken") or {}).get("count") or 0
    nuke = fb.get("nuke_count", 0) if fb else 0
    break_cnt = fb.get("break_count", 0) if fb else 0
    nuke_list = (fb.get("nuke_list") or []) if fb else []

    rows = []
    for x in nuke_list[:6]:
        nm = esc(x.get("name"))
        code = esc(x.get("code"))
        p = x.get("pct")
        pb = x.get("prev_boards")
        breakdown = x.get("breakdown")
        b5 = x.get("break_ma5")
        b10 = x.get("break_ma10")
        if breakdown:
            tag = '<span class="warn" style="color:var(--down);font-weight:700">真破位</span>'
        elif b5:
            tag = '<span class="stat">破5日线·分歧</span>'
        elif b10:
            tag = '<span class="stat">破MA10</span>'
        else:
            tag = '<span class="stat">正常回踩</span>'
        rows.append(f'<div class="nrow"><span class="nname">{nm} <span class="stat">{code}</span></span>'
                    f'<span class="npct down">{p:+.2f}%</span>'
                    f'<span class="nprev">昨{pb}板</span>{tag}</div>')
    nuke_html = "".join(rows) if rows else '<div class="stat">暂无核按钮</div>'

    alarm = (nuke >= 3 or break_cnt >= 1 or ld >= 5)
    sum_cls = 'warn' if alarm else 'stat'
    sum_txt = ('⚠️ 退潮期核按钮未收敛，接力容错极低' if alarm
               else '核按钮收敛，分歧在可控范围')
    return ('<div class="card nuke section"><h2>💥 核按钮 / 跌停预警</h2>'
            '<div class="mood">'
            f'<span class="badge">实时跌停 <b>{ld}</b></span>'
            f'<span class="badge">盘中炸板 <b>{broken}</b></span>'
            f'<span class="badge">盘后核按钮 <b>{nuke}</b></span>'
            f'<span class="badge">真破位 <b>{break_cnt}</b></span>'
            '</div>'
            f'<div class="nlst">{nuke_html}</div>'
            f'<div class="sum {sum_cls}">{sum_txt}</div></div>')


def render(d, trend_body=""):
    """组装看板 HTML（六 Tab 分组，2026-08-14 用户定 + 08-17 资金独立 + 08-17 主线题材/回流独立）：
    tab_intraday=盘中实时 / tab_mainline=主线题材 / tab_return=题材回流预测 /
    tab_review=复盘沉淀 / tab_trade=交易战绩 / tab_flow=资金流向。
    trend_body 从外部注入（ECharts 趋势模块），归入盘中实时。"""
    pi, pt = [], []  # intraday / trade（2026-08-21 复盘沉淀 tab 删除，昨日反馈并入盘中实时）
    pm, prr = [], []  # mainline tab（主线题材）/ theme-return tab（题材回流预测）

    # ── 昨日涨停反馈矩阵（隔日情绪核心·2026-08-21 从复盘沉淀 tab 并入盘中实时） ──
    fb = {}
    try:
        fb = json.loads((ROOT_DIR / "cache" / "feedback_matrix.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    pq = _load_panqian()
    tr = _load_theme_rotation()
    if fb and not fb.get("error"):
        # 语义色板（2026-08-16 用户定）：恶化(核按钮≥3/晋级率低于阀门)用绿 --down，改善用红 --up
        # 阀门=feedback_matrix trend.promotion_gate_10d（前10交易日截尾均值·09-08 用户定），缺失回退固定25
        _pgate = (fb.get("trend") or {}).get("promotion_gate_10d") or 25
        nuke_warn = ' style="color:var(--down);font-weight:700"' if fb.get("nuke_count", 0) >= 3 else ""
        prom_warn = ' style="color:var(--down);font-weight:700"' if (fb.get("promote_rate") or 100) < _pgate else ""
        sc = fb.get("sentiment_score")
        sent_cls = ""
        if isinstance(sc, (int, float)):
            sent_cls = ' style="color:var(--up);font-weight:700"' if sc >= 6 else \
                       (' style="color:var(--down);font-weight:700"' if sc <= 2 else ' style="color:#3b82c4"')
        pi.append(
            '<div class="card section"><h2>📊 昨日涨停反馈</h2>'
            f'<div class="stat">昨涨停 <b>{fb.get("prev_zt_count")}</b> 只｜'
            f'晋级率 <b{prom_warn}>{fb.get("promote_rate")}%</b>｜'
            f'连板晋级 {fb.get("lb_promote_rate")}%｜'
            f'均涨幅 {fb.get("avg_pct")}%｜'
            f'核按钮 <b{nuke_warn}>{fb.get("nuke_count")}</b> 只｜'
            f'最高板 {fb.get("prev_max_boards")}→{fb.get("now_max_boards")}｜'
            f'情绪 <b{sent_cls}>{fb.get("sentiment_score")}/10</b>·{fb.get("sentiment_label")}</div></div>'
        )

    # ── 盘中实时：ECharts 趋势追踪（视觉中枢） ──
    if trend_body:
        pi.append(trend_body)

    # 指数概况（不再独立占模块，由 AI 研判段一笔带过）
    idx_line = ""
    idx = d.get("index") or []
    if idx:
        segs = []
        for x in idx:
            nm = esc(x.get("name") or "")
            p = x.get("pct")
            segs.append(f'{nm} <span class="{upcls(p)}">{pct(p)}</span>')
        idx_line = "<br>📊 <b>大盘概况：</b>" + " ｜ ".join(segs)

    # 盘中实时：情绪条
    sd = d.get("sentiment_detail") or {}
    total_zt = d.get("total_zt") or 0
    ld = (d.get("limit_down") or {}).get("count") or 0
    broken = (d.get("broken") or {}).get("count") or 0
    seal = sd.get("seal_rate")
    pi.append(
        '<div class="card section"><div class="mood">'
        f'<span class="badge">情绪 {esc(d.get("sentiment"))} · {esc(sd.get("score"))}</span>'
        f'<span class="stat">涨停 {total_zt}</span>'
        f'<span class="stat">跌停 {ld}</span>'
        f'<span class="stat">炸板 {broken}</span>'
        f'<span class="stat">封板率 {"" if seal is None else f"{seal:.0f}%"}</span>'
        '</div></div>'
    )

    # 盘中实时：盘前竞价定调（盘前数据）
    auction_html = render_auction(pq)
    if auction_html:
        pi.append(auction_html)

    # 盘中实时：日内炒作梯队（连板天梯）
    ladder_html = render_ladder(d)
    if ladder_html:
        pi.append(ladder_html)

    # 💸 板块资金流向（主力净流入/流出）—— 2026-08-17 起独立 Tab（tab_flow），不再挂盘中实时
    flow_html = render_fund_flow(d, d.get("ts"))

    # 主线题材 Tab：主线温度计（持续主线）
    mainline_html = render_mainline(d)
    if mainline_html:
        pm.append(mainline_html)

    # 主线题材 Tab：板块涨停数增长趋势（近5交易日，与回流引擎同源）
    pm.append(render_sector_zt_trend(tr))

    # 主线题材 Tab：资金扭转（实时题材轮动对比）
    # 2026-08-21 复盘沉淀 tab 删除：每日题材趋势矩阵(render_theme_trend)与盘中实时 ECharts 趋势 + 主线题材板块涨停数趋势重复，不再渲染
    series = build_series()
    if series.get("days"):
        yest, today = last_two_days(series)
        if yest and today:
            pm.append(render_theme_compare(yest, today, idx_line=idx_line))

    # 主线题材 Tab：主线题材 Top
    top = d.get("top5") or []
    if top:
        pm.append(f'<div class="section"><h2 style="font-size:17px;margin-bottom:10px">🔥 主线题材 Top{len(top)}</h2><div class="grid cols2">')
        for t in top:
            L = t.get("leader") or {}
            fl = (t.get("followers") or [])[:3]
            fl_str = "、".join(f'{esc(f.get("name"))} {pct(f.get("pct"))}' for f in fl)
            risks = t.get("risks") or []
            risk_str = "".join(
                f'<div class="meta warn" style="margin-top:4px">⚠ {"、".join(esc(r) for r in risks)}</div>'
            ) if risks else ""
            fl_html = f'<div class="meta" style="margin-top:6px">跟风：{fl_str}</div>' if fl_str else ""
            pi.append(
                f'<div class="card theme"><div class="name">{esc(t.get("rank"))}. {esc(t.get("name"))}</div>'
                f'<div class="meta">涨停 <b>{esc(t.get("zt_count"))}</b> · 总{esc(t.get("total_stocks"))}只 · 均换手<span class="turn">{esc(t.get("turn_avg"))}%</span> · '
                f'龙头 <b class="lead">{esc(L.get("name"))}</b> <span class="{upcls(L.get("pct"))}">{pct(L.get("pct"))}</span></div>'
                f'<div class="desc">{esc(t.get("desc"))}</div>'
                f'{fl_html}{risk_str}</div>'
            )
        pm.append('</div></div>')

    # 盘中实时：可买个股（紫）
    leaders = d.get("leaders") or []
    if leaders:
        pi.append('<div class="section"><h2 style="font-size:17px;margin-bottom:10px">可买个股（紫=AI推荐）</h2><div class="grid cols2">')
        for s in leaders:
            tag = '<span class="tag">20cm</span>' if s.get("is_20cm") else ""
            buyable = bool(s.get("buyable"))
            buy = '<span class="buy">可买</span>' if buyable else ""
            # 机会行（未封板可买）整行紫；已涨停/封板行整行红；其余走分项配色
            _p = float(s.get("pct") or 0)
            # boards 兼容数字与文案（如 "8天4板"）：文案视为在板
            _b_raw = s.get("boards") or 0
            try:
                _b = int(_b_raw)
            except (ValueError, TypeError):
                _b = 1 if str(_b_raw).strip() else 0
            _zt = (_p >= (19.9 if s.get("is_20cm") else 9.9)) or (_b > 0)
            if buyable:
                rowcls = "ldr rec-row"
            elif _zt:
                rowcls = "ldr zt-row"
            else:
                rowcls = "ldr"
            namecls = "" if (buyable or _zt) else ' class="rec"'
            codecls = "" if (buyable or _zt) else ' class="stat"'
            pi.append(
                f'<div class="{rowcls}"><span><span{namecls}>{esc(s.get("name"))}</span> '
                f'<span{codecls}>{esc(s.get("code"))}</span>{tag}{buy}</span>'
                f'<span class="{upcls(s.get("pct"))}">{pct(s.get("pct"))}</span></div>'
            )
        pi.append('</div></div>')

    # 题材回流预测 Tab：盘后回流引擎输出
    theme_return_html = render_theme_return(tr, d)   # d=latest.json → 今日实时资金面板+交叉
    if theme_return_html:
        prr.append(theme_return_html)
    # 盘中实时：核按钮/跌停预警
    nuke_html = render_nuke_alert(d, fb)
    if nuke_html:
        pi.append(nuke_html)

    # 交易战绩：财富密码 · 真实战绩（净值/胜率/候选个股）
    pt.append(render_wealth_panel(_load_wealth()))

    # 盘中实时：爆发前夜
    early = d.get("early") or []
    if early:
        pi.append('<div class="section"><h2 style="font-size:17px;margin-bottom:10px">🌅 爆发前夜</h2><div class="grid cols2">')
        for e in early:
            b = e.get("buy") or {}
            pi.append(
                f'<div class="card win"><div class="name" style="font-weight:700">{esc(e.get("sector"))} '
                f'<span class="tag">{esc(e.get("type"))}</span></div>'
                f'<div class="desc" style="margin:6px 0">{esc(e.get("desc"))}</div>'
                f'<div class="meta">关注：<span class="rec">{esc(b.get("name"))}</span> '
                f'<span class="{upcls(b.get("pct"))}">{pct(b.get("pct"))}</span> {esc(b.get("code"))} '
                f'· 板块涨停{esc(e.get("zt_in_sector"))}</div></div>'
            )
        pi.append('</div></div>')

    # 盘中实时：弱势板块
    dr = ((d.get("sector_risk") or {}).get("declining") or [])
    if dr:
        pi.append('<div class="section"><h2 style="font-size:17px;margin-bottom:10px">📉 弱势板块</h2><div class="card"><ul>')
        for x in dr:
            pi.append(f'<li>{esc(x.get("name"))} <span class="{upcls(x.get("pct"))}">{pct(x.get("pct"))}</span></li>')
        pi.append('</ul></div></div>')

    # 公共页脚（所有 tab 之外）：图例 + 免责声明
    footer = (
        '<div class="card section legend"><b>配色图例：</b> '
        '<span class="up">■ 涨（红）</span> &nbsp; <span class="down">■ 跌（绿）</span> &nbsp; '
        '<span class="rec">■ 可买/埋伏（紫=AI推荐介入机会）</span> &nbsp; '
        '<span class="lead">■ 涨停标杆龙（金=标杆非机会）</span> &nbsp; '
        '<span class="warn">■ 退潮/风险预警（浅绿）</span></div>'
        '<div class="card section disclaimer">⚠️ 本报告基于公开信息分析，投资需谨慎</div>'
    )

    return {
        "tab_rotation": render_rotation_tab(d, fb),
        "tab_intraday": "\n".join(pi),
        "tab_mainline": "\n".join(pm),
        "tab_return": "\n".join(prr),
        "tab_trade": "\n".join(pt),
        "tab_flow": (flow_html or "") + render_flow_5d(tr),
        "tab_record": render_data_record(),
        "tab_stack": render_data_stack(),
        "footer": footer,
    }


# ===== 题材对比渲染（数据全部来自 theme_data.build_series，本文件只做 HTML） =====
def render_theme_trend(series):
    """多日题材趋势矩阵：每行一个题材，每列一个交易日（收盘）+ 末列今日盘中。

    完全本地动态——zt_history 每多一个归档文件，看板自动多一列，无需改代码。
    """
    days = series.get("days") or []
    labels = [L for L in (series.get("theme_labels") or []) if L != "其他"]
    if not days or not labels:
        return ""
    maxv = max([d["themes"].get(L, 0) for d in days for L in labels] + [1])
    head = '<th class="lab">题材</th>'
    for d in days:
        cls = ' class="intraday-h"' if d["kind"] == "intraday" else ""
        head += (f'<th{cls}>{esc(d["date_label"])}'
                 f'<br><span class="total">涨停{d.get("total")}</span></th>')
    head += '<th>最新Δ</th>'
    rows_html = []
    for L in labels:
        cells = []
        for d in days:
            v = d["themes"].get(L, 0)
            if v == 0:
                intra = ' intraday' if d["kind"] == "intraday" else ""
                cells.append(f'<td class="cell{intra}"><span class="v none">—</span></td>')
            else:
                pw = v / maxv * 100
                intra = ' intraday' if d["kind"] == "intraday" else ""
                cells.append(f'<td class="cell{intra}"><div class="bg" style="width:{pw:.0f}%"></div>'
                             f'<span class="v">{v}</span></td>')
        last = days[-1]["themes"].get(L, 0)
        prev = days[-2]["themes"].get(L, 0) if len(days) >= 2 else 0
        diff = last - prev
        if diff > 0:
            dcls, dmark = "up", f"▲{diff}"
        elif diff < 0:
            dcls, dmark = "down", f"▼{-diff}"
        else:
            dcls, dmark = "", "0"
        rows_html.append(f'<tr><td class="lab">{esc(L)}</td>{"".join(cells)}'
                         f'<td class="delta {dcls}">{dmark}</td></tr>')
    note = ('⚠️ <b class="intraday-h">蓝色列</b> = 今日盘中时间点（约每 30 分钟一档，从早到晚可看题材炒作路径）；'
            '盘中快照仅取板块涨停前 10，未列前 10 题材记为 0，非真归零；'
            '收盘后 record_zt_snapshot 自动归档当日收盘数据，次日自动多一收盘列。')
    return ('<div class="card trend section"><h2>📈 每日题材炒作趋势（本地沉淀·动态）</h2>'
            f'<div class="scroll"><table><tr>{head}</tr>{"".join(rows_html)}</table></div>'
            f'<div class="legend" style="margin-top:8px">{note}</div></div>')


def render_theme_compare(yest, today, idx_line=""):
    """最新资金扭转明细：取序列末两个交易日（收盘末日 vs 今日盘中 / 或相邻两收盘日）。
    idx_line：大盘指数概况一行（由 render 从 latest.json 构造，插在 AI 研判开头）。"""
    y = dict(yest.get("themes") or {})
    t = dict(today.get("themes") or {})
    ydate = yest.get("date_label", "昨日")
    ymax = yest.get("max_boards", "?")
    tmax = today.get("max_boards", "?")
    tts = (today.get("ts") or "")[:16]
    labels = [L for L in set(y) | set(t) if L != "其他"]
    labels.sort(key=lambda L: -(y.get(L, 0) + t.get(L, 0)))
    if not labels:
        return ""
    w = max([y.get(L, 0) for L in labels] + [t.get(L, 0) for L in labels] + [1])

    def bar(lab, val, cls, inline_bg=""):
        pw = (val / w * 100) if w else 0
        style = f"width:{pw:.0f}%"
        if inline_bg:
            style += f";{inline_bg}"
        return (f'<div class="row"><span class="lab">{esc(lab)}</span>'
                f'<div class="track"><div class="fill {cls}" style="{style}"></div></div>'
                f'<span class="val">{val}</span></div>')

    # 昨日柱子：统一红色（Baseline）
    ybars = "".join(bar(L, y.get(L, 0), "y") for L in labels)
    # 今日柱子：动态着色——比昨日强→红 / 弱→绿 / 相等→蓝
    tbars_parts = []
    rows = []
    for L in labels:
        yv = y.get(L, 0)
        tv = t.get(L, 0)
        diff = tv - yv
        if diff > 0:
            tbg = "background:linear-gradient(90deg,#ffd9d9,#ee2500)"
        elif diff < 0:
            tbg = "background:linear-gradient(90deg,#d9f5e6,#093)"
        else:
            tbg = ""
        tbars_parts.append(bar(L, tv, "t", tbg))
        if yv == 0 and tv > 0:
            mark = '<span class="new">🔴新晋</span>'
        elif yv > 0 and tv == 0:
            mark = '<span class="out">🟢退潮</span>'
        elif diff >= 0:
            mark = '<span class="hold">➡持续</span>' if diff == 0 else f'<span class="weak">⬆强化+{diff}</span>'
        else:
            mark = f'<span class="weak">⬇弱化{diff}</span>'
        rows.append(f'<tr><td class="lab">{esc(L)}</td><td>{yv}</td><td>{tv}</td>'
                    f'<td>{diff:+d}</td><td>{mark}</td></tr>')
    tbars = "".join(tbars_parts)
    table = ('<table><tr><th>题材</th><th>' + str(ydate) + '涨停</th><th>今日涨停</th>'
             '<th>Δ</th><th>资金</th></tr>' + "".join(rows) + '</table>')
    inflow = [L for L in labels if y.get(L, 0) == 0 and t.get(L, 0) > 0]
    outflow = [L for L in labels if y.get(L, 0) > 0 and t.get(L, 0) == 0]
    hold = [L for L in labels if y.get(L, 0) > 0 and t.get(L, 0) >= y.get(L, 0)]
    ins = idx_line + '<br>💡 <b>资金扭转：</b>'
    if inflow:
        ins += '流入新方向 → ' + "、".join(f'{L}({t[L]})' for L in inflow) + '；'
    if outflow:
        ins += '流出退潮 → ' + "、".join(f'{L}(昨{y[L]})' for L in outflow) + '；'
    if hold:
        ins += '持续抱团 → ' + "、".join(f'{L}({y[L]}→{t[L]})' for L in hold)
    ins += (f'<br>📐 连板高度：{ydate}最高 <b>{ymax}</b>板 → 今日 <b>{tmax}</b>板'
            '（高度退坡，警惕梯队长断板）')
    ins += ('<br>⚠️ 今日为盘中快照(未收盘)，仅取板块涨停前10；未列前10题材记为0，'
            '仍在交易中未必归零，以收盘为准。')
    return ('<div class="card cmp section"><h2>🔄 最新资金扭转 · ' + str(ydate) + '(收盘) vs 今日(盘中 ' + tts + ')</h2>'
            '<div class="bars">'
            f'<div class="half"><h3>■ 昨日收盘 板块涨停分布</h3>{ybars}</div>'
            f'<div class="half"><h3>■ 今日盘中 板块涨停分布</h3>{tbars}</div>'
            '</div>'
            f'<div style="margin-top:14px">{table}</div>'
            f'<div class="insight">{ins}</div></div>')


def main():
    if not SRC.exists():
        err = '<div class="error">❌ 未找到 latest.json，请先运行 intraday_pulse.py 抓取数据。</div>'
        html_out = (TPL.replace('{css}', CSS).replace('{geninfo}', "无数据")
                        .replace('{healthbar}', build_health_bar("无数据", "无数据"))
                        .replace('{tab_rotation}', err).replace('{tab_intraday}', err)
                        .replace('{tab_mainline}', "").replace('{tab_return}', "")
                        .replace('{tab_trade}', "")
                        .replace('{tab_flow}', "").replace('{tab_fundmon}', "")
                        .replace('{footer}', "")
                        .replace('{tab_record}', "").replace('{tab_stack}', "")
                        .replace('{tab_js}', TAB_JS).replace('{trend_js}', '/* no data */'))
        write_html_atomic(OUT, html_out, min_len=500)
        print("❌ 未找到", SRC)
        return
    try:
        d = json.loads(SRC.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        # 快照损坏（并发写历史遗留）→ 保留上一版 dashboard.html，不产出半截页面
        print(f"⚠️ latest.json 不可解析，保留上一版看板: {e}")
        return
    ts = (d.get("ts") or "").replace("T", " ").replace("+08:00", "")[:19]
    gen_ts = datetime.datetime.now().strftime("%H:%M:%S")

    # ── ECharts 趋势模块（先构建，传入 render 归入盘中实时 Tab）──
    # 沉淀当前时刻的题材分布（30 分钟窗），使「每日题材炒作趋势」矩阵带时间轴
    try:
        record_intraday_point()
    except Exception as e:
        print("[intraday_record] 跳过:", e)
    trend_body, trend_js = build_trend_section()
    content = render(d, trend_body)

    # 财富密码净值曲线 JS（追加到趋势 JS 之后，同 <script> 容器；交易 Tab 懒初始化）
    wealth = _load_wealth()
    trend_js += wealth_chart_js(wealth)

    # 数据堆叠 Tab 的 ECharts 懒初始化 JS（2026-08-25 新增 tab）
    trend_js += _stack_charts_js()
    # 资金监控 Tab 的 ECharts 懒初始化 JS（2026-09-08 新增 tab）
    trend_js += _fund_monitor_js()

    geninfo = f"快照 {ts} · 渲染 {gen_ts}"
    html_out = (TPL.replace('{css}', CSS).replace('{geninfo}', geninfo)
                    .replace('{healthbar}', build_health_bar(ts, gen_ts))
                    .replace('{tab_rotation}', content["tab_rotation"])
                    .replace('{tab_intraday}', content["tab_intraday"])
                    .replace('{tab_mainline}', content["tab_mainline"])
                    .replace('{tab_return}', content["tab_return"])
                    .replace('{tab_trade}', content["tab_trade"])
                    .replace('{tab_flow}', content["tab_flow"])
                    .replace('{tab_fundmon}', render_fund_monitor())
                    .replace('{tab_record}', content["tab_record"])
                    .replace('{tab_stack}', content["tab_stack"])
                    .replace('{footer}', content["footer"])
                    .replace('{tab_js}', TAB_JS)
                    .replace('{trend_js}', trend_js))
    write_html_atomic(OUT, html_out)
    print("✅ 统一看板已生成（九 Tab）:", OUT, "| 快照 ts=", d.get("ts"))


if __name__ == "__main__":
    main()
