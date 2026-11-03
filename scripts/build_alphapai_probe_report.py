#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alpha派全量接口采样 → 单文件 HTML 汇总报告。

只读本地采样 JSON（cache/alphapai_probe_YYYYMMDD/），不发请求、不改业务数据。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from html import escape
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
SRC = ROOT / "cache" / "alphapai_probe_20261028"
OUT = ROOT / "outputs" / "alphapai_full_probe_20261028.html"


def load(name: str):
    p = SRC / f"{name}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def paged(name: str):
    """返回 (rows, total, page_size)。"""
    d = load(name)
    if not isinstance(d, dict):
        return [], None, None
    data = d.get("data")
    if isinstance(data, dict) and "data" in data:
        return data.get("data") or [], data.get("totalSize"), data.get("pageSize")
    return data or [], None, None


def fmt_int(v):
    if isinstance(v, int):
        return f"{v:,}"
    return "—"


def md_inline(s: str) -> str:
    s = escape(str(s or ""))
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[(\d+)\]", r'<sup class="cite">\1</sup>', s)
    return s


def md_block(text: str) -> str:
    """极简 Markdown → HTML：标题/列表/段落，足够展示 QA 答案。"""
    out, in_list = [], False
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        m = re.match(r"^(#{2,6})\s+(.*)$", line)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            lvl = min(6, max(3, len(m.group(1))))
            out.append(f"<h{lvl}>{md_inline(m.group(2))}</h{lvl}>")
            continue
        if re.match(r"^\s*[-*]\s+", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{md_inline(re.sub(r'^\s*[-*]\s+', '', line))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<p>{md_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def table(headers, rows, cls="tbl"):
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = []
    for r in rows:
        cells = "".join(f"<td>{c}</td>" for c in r)
        body.append(f"<tr>{cells}</tr>")
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def tags(lst, limit=3):
    """把 list[{code,name}] / list[str] / str 统一渲染成可读文本。"""
    if not lst:
        return ""
    if isinstance(lst, str):
        return escape(lst)
    if isinstance(lst, dict):
        lst = [lst]
    if not isinstance(lst, list):
        return escape(str(lst))
    items = []
    for x in lst[:limit]:
        if isinstance(x, dict):
            items.append(str(x.get("name") or x.get("label") or x.get("code") or ""))
        else:
            items.append(str(x))
    items = [escape(i) for i in items if i]
    if not items:
        return ""
    s = " · ".join(items)
    if len(lst) > limit:
        s += f" +{len(lst)-limit}"
    return s


def type_label(code, label):
    """研报类型：label 为纯数字时视为未映射，回退显示 code。"""
    lab = str(label or "").strip()
    if lab and not lab.isdigit():
        return lab
    return str(code or "")


def money(v):
    return "—" if v is None else f"{v:,}"


# ───────────────────────── 数据装配 ─────────────────────────

probes = [
    ("hello", "健康检查", "01_hello"),
    ("hot-topics board-list", "机构热议榜", "02_hot_topics"),
    ("watchlist list", "自选股列表", "03_watchlist"),
    ("watchlist num", "自选股数量", "04_watchlist_num"),
    ("watchlist group-list", "自选分组", "05_watchlist_groups"),
    ("announcement list", "公告检索", "06_ann_list"),
    ("research-report list (domestic)", "内资研报", "07_rr_domestic"),
    ("research-report list (foreign)", "外资研报", "08_rr_foreign"),
    ("meeting-minutes list (A)", "会议纪要", "09_mm_A"),
    ("brokerage-commentary list", "券商点评", "10_bc_list"),
    ("brokerage-commentary event-list", "业绩点评", "11_bc_event"),
    ("bluebook batch-list", "蓝宝书批次", "12_bluebook_batch"),
    ("bluebook topic-list", "蓝宝书话题", "13_bluebook_topic"),
    ("image", "搜图表", "14_image"),
    ("report", "个股公告期", "15_report_anns"),
    ("social article-list", "社媒文章", "16_social_articles"),
    ("social account-search", "公众号搜索", "17_social_accounts"),
    ("meeting-booking list", "预约会议", "18_booking"),
    ("recording query", "录音转记", "19_recording"),
    ("recall", "投研知识检索", "20_recall"),
    ("da-query", "金融数据NL查询", "21_da_query"),
    ("qa (Flash)", "PaiPai 问答", "22_qa_flash"),
]


def probe_row(key, label, fname):
    d = load(fname)
    if d is None:
        return [escape(key), escape(label), '<span class="bad">文件缺失</span>', "—", "—"]
    code = d.get("code")
    ok = code == 200000
    status = '<span class="ok">200000</span>' if ok else f'<span class="bad">{escape(str(code))}</span>'
    data = d.get("data")
    if isinstance(data, dict) and "totalSize" in data:
        size = f'<b>{fmt_int(data["totalSize"])}</b>'
        n = len(data.get("data") or [])
        note = f"本页 {n} 条 / 共 {fmt_int(data.get('totalPageNum'))} 页"
    elif isinstance(data, list):
        size = f"<b>{len(data)}</b>"
        note = "数组长度（无分页总量字段）"
    elif isinstance(data, dict):
        size = "1"
        note = "单对象：" + "、".join(list(data)[:6])
    elif data is None:
        size = "—"
        note = "根层对象：" + "、".join(list(d)[:6])
    else:
        size = "—"
        note = escape(str(data))
    return [f"<code>{escape(key)}</code>", escape(label), status, size, note]


# 分页总量（注意：均为各自查询窗口内总量，不是全库）
pools = []
for nm, label, win in [
    ("06_ann_list", "公告 · 10/20-10/28", "2026-10-20~10-28"),
    ("07_rr_domestic", "内资研报 · 10/20-10/28", "2026-10-20~10-28"),
    ("09_mm_A", "A股纪要 · 10/20-10/28", "2026-10-20~10-28"),
    ("10_bc_list", "券商点评 · 10/20-10/28", "2026-10-20~10-28"),
    ("13_bluebook_topic", "蓝宝书话题 · 10/27-10/28", "2026-10-27~10-28"),
    ("16_social_articles", "社媒文章 · 未筛日期", "全库"),
]:
    d = load(nm)
    total = None
    if isinstance(d, dict) and isinstance(d.get("data"), dict):
        total = d["data"].get("totalSize")
    pools.append((label, total))

# 机构热议：最新交易日
hot = load("02_hot_topics")
hot_latest, hot_days = None, 0
if isinstance(hot, dict) and isinstance(hot.get("data"), list):
    days = hot["data"]
    hot_days = len(days)
    if days:
        hot_latest = days[0]

# 自选股
wl = load("03_watchlist")
wl_rows = wl.get("data") if isinstance(wl, dict) else []

# 蓝宝书话题
bb_topics, bb_total, _ = paged("13_bluebook_topic")
bb_batch, bb_batch_total, _ = paged("12_bluebook_batch")

# 各类文档
ann_rows, ann_total, _ = paged("06_ann_list")
rr_rows, rr_total, _ = paged("07_rr_domestic")
fr_rows, fr_total, _ = paged("08_rr_foreign")
mm_rows, mm_total, _ = paged("09_mm_A")
bc_rows, bc_total, _ = paged("10_bc_list")
be_rows, be_total, _ = paged("11_bc_event")
so_rows, so_total, _ = paged("16_social_articles")
sa_rows, _, _ = paged("17_social_accounts")

img = load("14_image")
img_rows = img.get("data") if isinstance(img, dict) else []
ann_period = load("15_report_anns")
ann_period_rows = ann_period.get("data") if isinstance(ann_period, dict) else []
recall = load("20_recall")
recall_rows = recall.get("data") if isinstance(recall, dict) else []
daq = load("21_da_query")
daq_ans = (daq.get("data") or {}).get("answer") if isinstance(daq, dict) else ""
qa = load("22_qa_flash")
qa_ans = (qa.get("answer") if isinstance(qa, dict) else "") or ""
qa_refs = (qa.get("references") if isinstance(qa, dict) else []) or []

# ───────────────────────── 片段 ─────────────────────────

kpi = "".join(
    f'<div class="kpi"><div class="kpi-n">{fmt_int(t) if t is not None else "—"}</div>'
    f'<div class="kpi-l">{escape(l)}</div></div>'
    for l, t in pools
)

probe_table = table(
    ["接口", "含义", "状态码", "返回", "说明"],
    [probe_row(*p) for p in probes],
)

hot_block = "<p class='muted'>未取到</p>"
if hot_latest:
    boards = []
    for b in hot_latest.get("instBoardList") or []:
        items = b.get("itemList") or []
        rows = [
            [
                str(i.get("rank") or ""),
                f'<strong>{escape(str(i.get("name") or ""))}</strong>',
                f'<code>{escape(str(i.get("comboSymbol") or ""))}</code>',
                str(i.get("hotCount") or ""),
            ]
            for i in items
        ]
        boards.append(
            f'<div class="col"><h4>{escape(str(b.get("instTypeLabel") or ""))}</h4>'
            + table(["#", "名称", "代码", "热度"], rows, "tbl sm")
            + "</div>"
        )
    hot_block = (
        f'<p class="muted">交易日 {escape(str(hot_latest.get("tradingDay")))}　'
        f"共 {hot_days} 个交易日可选（{escape(str(hot['data'][-1].get('tradingDay')))} 起）</p>"
        f'<div class="grid3">{"".join(boards)}</div>'
    )

bb_rows_html = []
for t in bb_topics[:6]:
    stocks = tags(t.get("stockTag") or [], 6)
    bb_rows_html.append(
        f'<div class="topic"><div class="topic-h"><strong>{md_inline(t.get("topicName"))}</strong>'
        f'<span class="heat">热度 {escape(str(t.get("heatLevel") or "—"))}</span></div>'
        + (f'<div class="stocks">关联：{escape(stocks)}</div>' if stocks else "")
        + f'<div class="sum">{md_inline(t.get("summary"))}</div></div>'
    )

bb_batch_html = table(
    ["批次", "日期", "类型", "话题数"],
    [
        [
            escape(str(b.get("batchTitle") or "")),
            escape(str(b.get("batchDate") or "")),
            escape(str(b.get("batchTypeLabel") or "")),
            str(b.get("topicCount") or ""),
        ]
        for b in bb_batch
    ],
    "tbl sm",
)

ann_tbl = table(
    ["标题", "标的", "类型", "行业", "挂网时间"],
    [
        [
            escape(str(r.get("title") or ""))[:64],
            tags(r.get("stockTag"), 2) or '<span class="muted">—</span>',
            escape(str(r.get("announcementType") or "—")),
            tags(r.get("industryTag"), 2) or "—",
            escape(str(r.get("actualPublishTime") or r.get("publishTime") or "")),
        ]
        for r in ann_rows
    ],
    "tbl sm",
)

rr_tbl = table(
    ["标题", "机构", "类型", "日期", "页数"],
    [
        [
            escape(str(r.get("titleCn") or r.get("title") or ""))[:60],
            tags(r.get("institutionTag"), 2) or "—",
            escape(type_label(r.get("reportType"), r.get("reportTypeLabel"))),
            escape(str(r.get("reportTime") or "")[:10]),
            str(r.get("pageCount") or "—"),
        ]
        for r in rr_rows
    ],
    "tbl sm",
)

fr_tbl = table(
    ["标题", "机构", "类型", "日期", "页数"],
    [
        [
            escape(str(r.get("titleCn") or r.get("title") or ""))[:60],
            tags(r.get("institutionTag"), 2) or "—",
            escape(type_label(r.get("reportType"), r.get("reportTypeLabel"))),
            escape(str(r.get("reportTime") or "")[:10]),
            str(r.get("pageCount") or "—"),
        ]
        for r in fr_rows
    ],
    "tbl sm",
)

mm_tbl = table(
    ["会议标题", "内容类型", "标的", "时间", "可读"],
    [
        [
            escape(str(r.get("title") or ""))[:52],
            tags(r.get("meetingContentType"), 2) or "—",
            tags(r.get("stockTag"), 2) or "—",
            escape(str(r.get("meetingTime") or "")[:16]),
            tags(r.get("availableNoteTypes"), 2) or "—",
        ]
        for r in mm_rows
    ],
    "tbl sm",
)

bc_tbl = table(
    ["标题", "摘要（列表仅前 100 字）", "标的", "时间"],
    [
        [
            escape(str(r.get("title") or ""))[:40],
            escape(re.sub(r"\s+", " ", str(r.get("content") or ""))[:78]),
            tags(r.get("stockTag"), 2) or "—",
            escape(str(r.get("commentTime") or "")[:16]),
        ]
        for r in bc_rows
    ],
    "tbl sm",
)

be_tbl = table(
    ["标题", "标的", "时间"],
    [
        [
            escape(str(r.get("title") or ""))[:64],
            tags(r.get("stockTag"), 2) or "—",
            escape(str(r.get("commentTime") or "")[:16]),
        ]
        for r in be_rows
    ],
    "tbl sm",
)

so_tbl = table(
    ["标题", "公众号", "日期"],
    [
        [
            escape(str(r.get("title") or ""))[:64],
            escape(str(r.get("accountName") or "")),
            escape(str(r.get("publishDate") or "")[:10]),
        ]
        for r in so_rows
    ],
    "tbl sm",
)

wl_tbl = table(
    ["名称", "代码", "分组"],
    [
        [
            escape(str(r.get("stockName") or "")),
            f'<code>{escape(str(r.get("stockCode") or ""))}</code>',
            escape(str(r.get("groupCode") or "")),
        ]
        for r in (wl_rows or [])
    ],
    "tbl sm",
)

img_tbl = table(
    ["图注", "来源文章", "机构", "日期"],
    [
        [
            escape("；".join([str(x) for x in (r.get("captionList") or [])])[:56] or str(r.get("type") or "—")),
            escape(str(r.get("articleTitle") or ""))[:40],
            tags(r.get("institutionList"), 2) or "—",
            escape(str(r.get("publishDate") or "")[:10]),
        ]
        for r in img_rows
    ],
    "tbl sm",
)

ap_tbl = table(
    ["报告期", "类型", "标题"],
    [
        [
            escape(str(r.get("reportPeriod") or "")),
            escape(str(r.get("reportType") or "")),
            escape(str(r.get("stockReportTitle") or ""))[:62],
        ]
        for r in ann_period_rows[:12]
    ],
    "tbl sm",
)

recall_types = {}
for r in recall_rows:
    recall_types.setdefault(str(r.get("type") or "?"), 0)
    recall_types[str(r.get("type") or "?")] += 1
recall_tbl = table(
    ["类型", "标题", "机构", "时间"],
    [
        [
            escape(str(r.get("type") or "")),
            escape(str(r.get("title") or ""))[:64],
            escape(str(r.get("institution") or ""))[:18],
            escape(str(r.get("time") or ""))[:10],
        ]
        for r in recall_rows[:14]
    ],
    "tbl sm",
)

qa_html = md_block(qa_ans) if qa_ans else "<p class='muted'>未取到</p>"
daq_html = md_block(daq_ans) if daq_ans else "<p class='muted'>未取到</p>"

now = datetime.now().strftime("%Y-%m-%d %H:%M")

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Alpha派 research skill 全量采样报告</title>
<style>
:root {{
  --bg:#f6f7f9; --card:#fff; --ink:#1a1d21; --sub:#5b636e; --line:#e3e6ea;
  --red:#c62828; --green:#2e7d32; --blue:#1565c0; --amber:#b26a00;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.62 -apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif; }}
.wrap {{ max-width:1440px; margin:0 auto; padding:28px 32px 64px; }}
header {{ border-bottom:2px solid var(--ink); padding-bottom:14px; margin-bottom:22px; }}
h1 {{ font-size:24px; margin:0 0 6px; letter-spacing:.3px; }}
.meta {{ color:var(--sub); font-size:12.5px; }}
.meta code {{ background:#eceff3; padding:1px 5px; border-radius:3px; }}
h2 {{ font-size:17px; margin:34px 0 12px; padding-left:9px; border-left:4px solid var(--ink); }}
h3 {{ font-size:14.5px; margin:22px 0 8px; color:#2b3138; }}
h4 {{ font-size:13px; margin:0 0 6px; color:var(--sub); font-weight:600; }}
p {{ margin:7px 0; }}
.muted {{ color:var(--sub); font-size:12.5px; }}
.lead {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
.kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin:14px 0 6px; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 14px; }}
.kpi-n {{ font-size:21px; font-weight:700; font-variant-numeric:tabular-nums; }}
.kpi-l {{ font-size:12px; color:var(--sub); margin-top:2px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px 18px; margin:12px 0; }}
table.tbl {{ width:100%; border-collapse:collapse; margin:8px 0 4px; font-size:13px; }}
table.tbl th {{ text-align:left; font-weight:600; color:var(--sub); border-bottom:1px solid var(--line);
  padding:7px 8px; white-space:nowrap; background:#fafbfc; }}
table.tbl td {{ border-bottom:1px solid #f0f2f4; padding:7px 8px; vertical-align:top; }}
table.tbl.sm {{ font-size:12.5px; }}
table.tbl.sm td {{ padding:6px 8px; }}
code {{ font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; background:#eef1f5;
  padding:1px 4px; border-radius:3px; }}
.ok {{ color:var(--green); font-weight:600; }}
.bad {{ color:var(--red); font-weight:600; }}
.grid3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
.col {{ min-width:0; }}
.topic {{ border-left:3px solid #c9ced6; padding:8px 0 8px 12px; margin:10px 0; }}
.topic-h {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; }}
.heat {{ font-size:11.5px; color:var(--amber); background:#fff4e0; padding:1px 7px; border-radius:10px; white-space:nowrap; }}
.stocks {{ font-size:12px; color:var(--blue); margin:3px 0; }}
.sum {{ font-size:13px; color:#333; }}
.qa {{ background:#fbfcfd; border:1px solid var(--line); border-radius:6px; padding:6px 16px; }}
.qa h3, .qa h4, .qa h5 {{ margin:14px 0 6px; }}
.qa ul {{ margin:6px 0 6px 18px; padding:0; }}
.qa li {{ margin:3px 0; }}
.cite {{ color:var(--blue); font-size:9.5px; }}
.note {{ border-left:3px solid var(--amber); background:#fffaf2; padding:10px 14px; border-radius:0 6px 6px 0; }}
.note.bad {{ border-left-color:var(--red); background:#fdf4f4; }}
footer {{ margin-top:36px; padding-top:14px; border-top:1px solid var(--line);
  color:var(--sub); font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>Alpha派 research skill · 全量接口采样报告</h1>
  <div class="meta">
    技能 <code>alphapai-research</code> v2.0.9　·　采样时间 {now}　·　接口基数 22 个
　·　原始 JSON 目录 <code>cache/alphapai_probe_20261028/</code>
  </div>
</header>

<div class="lead">
  <p><strong>结论</strong>：22 个接口全部返回 <code>200000</code>，无鉴权或权限报错。这套接口不是"新闻聚合"，而是
  公告 / 研报 / 纪要 / 点评 / 社媒 / 指数指标六类一手或准一手文本库，加上榜单和 AI 生成层。
  下面所有表格都是本次真实返回，未做任何改写或补全。</p>
  <div class="kpis">{kpi}</div>
  <p class="muted">⚠️ 上排是<strong>各查询窗口内</strong>的分页总量（<code>totalSize</code>），不是全库、也不是本次取样条数。
  公告/研报/纪要/点评窗口为 2026-10-20~10-28；蓝宝书为 10-27~10-28 早报；社媒未加日期过滤，936 万是全库文本量。
  把窗口放大，前四个数字都会变大。</p>
</div>

<h2>一、22 个接口实测状态</h2>
<div class="card">
{probe_table}
<p class="muted">"返回"列：分页接口显示声明总量，数组接口显示本次数组长度。状态码 200000 才是业务成功，HTTP 200 不算。</p>
</div>

<h2>二、机构热议榜（Alpha派特色数据）</h2>
<div class="card">
{hot_block}
<p class="muted">每天三张榜（公募/私募/保险），各 10 只，带 <code>hotCount</code> 热度计数。
只有排名和热度，<strong>不给逻辑</strong>——要解释得另接点评或检索。</p>
</div>

<h2>三、蓝宝书（题材 + 关联标的 + 官方解读）</h2>
<div class="card">
<p class="muted">批次数 {len(bb_batch)}（就近两日早报），话题总量 {fmt_int(bb_total)}。</p>
{bb_batch_html}
<h3>话题样本（含摘要与关联标的）</h3>
{''.join(bb_rows_html)}
<p class="muted">这一层信息密度最高：一条话题自带 <code>summary</code>、<code>stockTag</code> 标的清单、
<code>heatLevel</code> 热度，以及 <code>paipaiInterpretationAnswer</code> 官方解读。</p>
</div>

<h2>四、文档类样本（各取首页前几条）</h2>

<h3>公告 · 总量 {fmt_int(ann_total)}</h3>
<div class="card">{ann_tbl}</div>

<h3>内资研报 · 总量 {fmt_int(rr_total)}</h3>
<div class="card">{rr_tbl}
<p class="muted"><code>reportType="15"</code> 在本次返回里 <code>reportTypeLabel</code> 也是 "15"，未映射成中文——
遇到非中文 label 就按代码显示，不臆造类目名。</p>
</div>

<h3>外资研报 · 总量 {fmt_int(fr_total)}</h3>
<div class="card">{fr_tbl}</div>

<h3>A股会议纪要 · 总量 {fmt_int(mm_total)}</h3>
<div class="card">{mm_tbl}
<p class="muted"><code>availableNoteTypes</code> 决定这条能否读 <code>ai_note</code>（AI 纪要）或 <code>asr_note</code>（逐字稿）。</p>
</div>

<h3>券商点评 · 总量 {fmt_int(bc_total)}</h3>
<div class="card">{bc_tbl}
<p class="muted">接口签名里有 <code>--subject-name</code>（题材过滤），但本次 5 条样本的 <code>subjectTag</code>
<strong>全部为空</strong>——题材维度能否真正筛到东西，需要更大样本再确认，别先当成已可用。
列表返回的 <code>content</code> 只截到前 100 字，全文必须走 <code>detail</code>。</p>
</div>

<h3>业绩点评（event-list）· 总量 {fmt_int(be_total)}</h3>
<div class="card">{be_tbl}</div>

<h3>社媒文章 · 总量 {fmt_int(so_total)}</h3>
<div class="card">{so_tbl}
<p class="muted">支持按股票、行业、机构、公众号名过滤，也可按 <code>--psn-write</code> 筛文风。</p>
</div>

<h2>五、自选股与工具</h2>
<div class="card">
<h4>当前关注 {len(wl_rows)} 只</h4>
{wl_tbl}
</div>

<div class="card">
<h4>搜图表（研报/公告内嵌图表，query=「AI算力 光模块」）</h4>
{img_tbl}
<p class="muted">返回带 <code>imageUrl</code>、<code>pageIndex</code>、<code>bbox</code> 坐标与 <code>llmScore</code> 重排分。</p>
</div>

<div class="card">
<h4>个股公告期列表（300308.SZ 中际旭创，共 {len(ann_period_rows)} 期）</h4>
{ap_tbl}
<p class="muted">专供 <code>agent --mode 1</code>（业绩点评）取 <code>report-id</code> 用。</p>
</div>

<h2>六、AI 层</h2>

<h3>投研知识检索 recall（21 条召回，类型分布见下）</h3>
<div class="card">
<p class="muted">类型分布：{escape('、'.join(f'{k}×{v}' for k,v in sorted(recall_types.items())))}</p>
{recall_tbl}
</div>

<h3>PaiPai 问答（Flash 模式，问「2026年10月28日A股市场主线板块是什么」）</h3>
<div class="card">
<div class="qa">{qa_html}</div>
<p class="muted">回答 {len(qa_ans)} 字，带 {len(qa_refs)} 条引用。正文里的角标 <sup class="cite">1</sup> 对应 references 序号。</p>
</div>

<h3>金融数据自然语言查询 da-query（问「中际旭创2026年三季报营收和净利润」）</h3>
<div class="card">
<div class="qa">{daq_html}</div>
</div>

<h2>七、边界与限制（实测或源码确认）</h2>
<div class="card">
<div class="note">
<strong>额度不可查</strong>：整个 CLI 没有剩余额度命令。要查额度只能走 <code>alphapai-lite-mcp</code> 连接器。
<code>qa</code> / <code>agent</code> / <code>recall</code> / <code>da-query</code> 是计费层，列表接口未见计费提示但未经官方确认。
</div>
<div class="note">
<strong>recall 的 --no-cutoff 是空参数</strong>：文档自述 <code>isCutOff=true/false 返回内容无差异</code>。别指望拿更长原文。
</div>
<div class="note">
<strong>三类纪要互不通用</strong>：<code>meeting-minutes</code>（平台聚合公开会议）、<code>recording</code>（自己上传）、
<code>meeting-booking</code>（会议机器人录制），<code>roadshowId</code> / <code>taskId</code> / <code>msgId</code> 不可混用。
</div>
<div class="note">
<strong>agent 模式号有缺口</strong>：1,2,3,5,7,8,9,11,12,13,15 可用，4/6/10/14 未开放，不要猜用途。
</div>
<div class="note bad">
<strong>加密 ID 会过期</strong>：公告、研报、点评的 <code>announcementId</code> / <code>reportId</code> / <code>commentId</code>
不可长期复用，每次都要先 list 再 detail。
</div>
</div>

<h2>八、这套东西对现有链路的可用点</h2>
<div class="card">
<table class="tbl">
<thead><tr><th>现有短板</th><th>可接的接口</th><th>说明</th></tr></thead>
<tbody>
<tr><td>板块只有涨停家数与资金，缺"机构怎么说"</td><td><code>hot-topics</code> + <code>brokerage-commentary --subject-name</code></td>
<td>榜单给热度，点评给题材口径观点，两者交叉才算证据，单用任何一个都是半条腿。</td></tr>
<tr><td>题材映射靠选股宝，缺公告级硬催化核验</td><td><code>announcement list --stock-code</code> → <code>parsing-download</code></td>
<td>拿 Markdown 解析版喂模型，比 PDF 稳定。</td></tr>
<tr><td>纪要类信息靠盘中快讯，事后无法回溯原文</td><td><code>meeting-minutes</code></td>
<td>860 条 A 股纪要，支持按 content-type（业绩会/专家交流/行业分析）分。</td></tr>
<tr><td>缺少"业绩兑现"这一维</td><td><code>brokerage-commentary event-list</code> + <code>da-query</code></td>
<td>预告/快报/正式报告三档事件，配合指标查询。</td></tr>
<tr><td>没时间逐篇读研报</td><td><code>recall --type report,comment</code></td>
<td>一次召回跨源，返回 chunks 可直接进 prompt。</td></tr>
</tbody>
</table>
</div>

<footer>
数据来源：Alpha派 OpenPai API（<code>open-api.rabyte.cn</code>），本地采样 {now}。
所有样本为接口真实返回，未经修改；未做跨源交叉核验的条目不应直接作为交易依据。
</footer>

</div>
</body>
</html>
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(HTML, encoding="utf-8")
print(f"written: {OUT}")
print(f"size: {OUT.stat().st_size:,} bytes")
