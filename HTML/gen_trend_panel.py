#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材趋势追踪面板生成器（ECharts 驱动·增强版）。

链路：
  1. 调 theme_data.export_series_json() → cache/theme_series.json（中转文件·含每日细节+龙头）
  2. 读中转文件 → 构建 折线图(多主题强度排序) + 柱状图(20cm/连板分布) + 情绪图 + 数据表格(龙头+强度)
  3. 嵌入自包含 HTML → cache/trend_panel.html

用法：python3 scripts/gen_trend_panel.py
"""
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"
SERIES_JSON = CACHE / "theme_series.json"
OUT = CACHE / "trend_panel.html"

# ECharts 配色表（A 股规范：红涨·绿跌·金标杆·紫蓝辅助）
# 折线图多主题仅用红/蓝/金/紫/橙等非绿系区分，绿只用于「跌/退潮」语义
COLORS = json.dumps([
    "#ee2500", "#3b82c4", "#f0a020", "#8b5cf6",
    "#f97316", "#ec4899", "#e879f9", "#06b6d4",
    "#ef4444", "#a78bfa", "#fb923c", "#6366f1",
])
MAX_LINES = 8


def load_series():
    return json.loads(SERIES_JSON.read_text(encoding="utf-8"))


def gen_line_option(d):
    """多主题折线图：X=日期，Y=涨停数。排序按 table_rows 顺序（强度排序）。"""
    dates = d["dates"]
    labels = d["theme_labels"]  # 已按强度排序
    themes = d["themes"]
    ranked = set(labels[:MAX_LINES])

    series = []
    for L in labels:
        series.append({
            "name": L, "type": "line",
            "data": themes[L],
            "smooth": True, "symbol": "circle", "symbolSize": 5,
            "lineStyle": {"width": 2},
            "connectNulls": False,
        })
    legend_selected = {L: (L in ranked) for L in labels}

    return {
        "color": json.loads(COLORS),
        "title": {"text": "题材涨停趋势（按强度排序·Top8 默认展开）",
                  "left": "center", "textStyle": {"fontSize": 14, "fontWeight": "bold"}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "legend": {
            "type": "scroll", "bottom": 0,
            "selected": legend_selected,
            "textStyle": {"fontSize": 10},
        },
        "grid": {"left": 50, "right": 35, "top": 40, "bottom": 55},
        "xAxis": {"type": "category", "data": dates,
                  "axisLabel": {"fontSize": 11, "rotate": 15 if len(dates) > 3 else 0}},
        "yAxis": {"type": "value", "name": "涨停数", "minInterval": 1,
                  "splitLine": {"lineStyle": {"color": "#f0f0f3"}}},
        "series": series,
    }


def gen_bar_option(d):
    """每日涨停结构：总数(红柱) + 20cm数(淡红) + 连板≥2(金线) + 连板≥4(虚线)。"""
    dates = d["dates"]
    details = d.get("daily_details") or []
    totals = d["daily_totals"]

    cm20 = [x.get("count_20cm") for x in details]
    b2 = [x.get("boards_2plus") for x in details]
    b4 = [x.get("boards_4plus") for x in details]

    # ── 动态柱色：比前日强→红、弱→绿、持平→灰、首日→蓝 ──
    def _bar_data(values, totals_ref=None):
        ref = totals_ref or values
        out = []
        for i, v in enumerate(values):
            clr = "#3b82c4"  # first bar = blue
            if i > 0 and v is not None and ref[i-1] is not None:
                if v > ref[i-1]:
                    clr = "#ee2500"   # ▲ stronger → red
                elif v < ref[i-1]:
                    clr = "#093"       # ▼ weaker → green
                else:
                    clr = "#86868b"    # → unchanged → gray
            out.append({"value": v, "itemStyle": {"color": clr}})
        return out

    bar_totals = _bar_data(totals)
    bar_cm20 = _bar_data(cm20, totals)  # 20cm also uses totals as reference for consistency

    mark_areas = []
    for i, dt in enumerate(dates):
        if "盘中" in dt:
            mark_areas.append([{"xAxis": i - 0.4}, {"xAxis": i + 0.4}])

    return {
        "color": ["#ee2500", "#f87171", "#f0a020", "#fbbf24"],
        "title": {"text": "每日涨停结构（总数·20cm弹性·连板深度）",
                  "left": "center", "textStyle": {"fontSize": 14, "fontWeight": "bold"}},
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0, "textStyle": {"fontSize": 11}},
        "grid": {"left": 50, "right": 50, "top": 50, "bottom": 40},
        "xAxis": {"type": "category", "data": dates,
                  "axisLabel": {"fontSize": 11, "rotate": 15 if len(dates) > 3 else 0}},
        "yAxis": [
            {"type": "value", "name": "涨停数", "splitLine": {"lineStyle": {"color": "#f0f0f3"}}},
            {"type": "value", "name": "连板层", "min": 0, "splitLine": {"show": False}},
        ],
        "series": [
            {
                "name": "涨停总数", "type": "bar", "data": bar_totals,
                "itemStyle": {"borderRadius": [6, 6, 0, 0]}, "barWidth": "45%", "barGap": "20%",
                "markArea": {"silent": True, "data": mark_areas,
                             "itemStyle": {"color": "rgba(59,130,196,0.08)"}},
                "label": {"show": True, "position": "top", "fontSize": 12, "fontWeight": "bold"},
            },
            {
                "name": "20cm数", "type": "bar", "data": bar_cm20,
                "itemStyle": {"borderRadius": [6, 6, 0, 0]}, "barWidth": "30%",
                "label": {"show": True, "position": "top", "fontSize": 10},
            },
            {
                "name": "连板≥2", "type": "line", "yAxisIndex": 1, "data": b2,
                "symbol": "circle", "symbolSize": 8,
                "lineStyle": {"color": "#f0a020", "width": 2},
                "itemStyle": {"color": "#f0a020"},
                "label": {"show": True, "fontSize": 11, "fontWeight": "bold", "color": "#f0a020"},
            },
            {
                "name": "连板≥4", "type": "line", "yAxisIndex": 1, "data": b4,
                "symbol": "diamond", "symbolSize": 8,
                "lineStyle": {"color": "#fbbf24", "width": 1.5, "type": "dashed"},
                "itemStyle": {"color": "#fbbf24"},
                "label": {"show": True, "fontSize": 10, "color": "#fbbf24"},
            },
        ],
    }


def gen_sentiment_option(d):
    """情绪/封板率趋势图（若有数据则渲染）。"""
    dates = d["dates"]
    details = d.get("daily_details") or []
    sentiments = [x.get("sentiment") for x in details]
    seals = [x.get("seal_rate") for x in details]

    has_any = any(v is not None for v in sentiments) or any(v is not None for v in seals)
    if not has_any:
        return None

    series = []
    if any(v is not None for v in sentiments):
        series.append({
            "name": "情绪分", "type": "line", "data": sentiments,
            "smooth": True, "symbol": "circle", "symbolSize": 8,
            "lineStyle": {"color": "#8b5cf6", "width": 2.5},
            "itemStyle": {"color": "#8b5cf6"},
            "label": {"show": True, "fontSize": 11, "fontWeight": "bold", "color": "#8b5cf6"},
        })
    if any(v is not None for v in seals):
        series.append({
            "name": "封板率%", "type": "line", "data": seals,
            "smooth": True, "symbol": "diamond", "symbolSize": 8,
            "lineStyle": {"color": "#6366f1", "width": 2},
            "itemStyle": {"color": "#6366f1"},
            "label": {"show": True, "fontSize": 11, "color": "#6366f1"},
        })

    return {
        "title": {"text": "市场情绪 + 封板率", "left": "center",
                  "textStyle": {"fontSize": 14, "fontWeight": "bold"}},
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0, "textStyle": {"fontSize": 11}},
        "grid": {"left": 50, "right": 35, "top": 50, "bottom": 40},
        "xAxis": {"type": "category", "data": dates,
                  "axisLabel": {"fontSize": 11, "rotate": 15 if len(dates) > 3 else 0}},
        "yAxis": {"type": "value", "splitLine": {"lineStyle": {"color": "#f0f0f3"}}},
        "series": series,
    }


def gen_table_html(d):
    """生成带龙头 + 强度星星的表格。强度=涨停数×10 + max(0,Δ)×5 + 龙头连板×3。"""
    dates = d["dates"]
    rows = d["table_rows"]

    head = '<tr><th class="sort-desc">主题(强度↓)</th>'
    for dt in dates:
        short = dt.replace("2026-", "").replace("今日盘中 ", "盘中\n")
        head += f'<th>{short}</th>'
    head += '<th>龙头(今日)</th><th>环比</th></tr>'

    max_s = max(
        (r["values"][-1] or 0) * 10 + max(0, r.get("delta", 0)) * 5 +
        ((r.get("leader_today") or {}).get("boards", 0) or 0) * 3
        for r in rows
    ) or 1

    body = ""
    for r in rows:
        td = f'<td class="theme-name">{r["theme"]}</td>'
        for v in r["values"]:
            if v is None:
                td += '<td class="null-val">—</td>'
            else:
                td += f'<td>{v}</td>'

        # 龙头（今日）
        ld = r.get("leader_today") or {}
        if ld.get("name"):
            td += (f'<td><span class="lead-name">{ld["name"]}</span> '
                   f'<span class="lead-boards">{ld["code"]}</span> '
                   f'<span class="pct">{ld.get("pct","")}%</span> '
                   f'<span class="boards-badge">{ld.get("boards",0)}板</span></td>')
        else:
            td += '<td class="null-val">—</td>'

        # 环比上一时间节点（末档 vs 上一档）：增加▲红 / 减少▼绿 / 持平—灰
        dstr = r["delta_str"]
        t = r["trend"]
        if t == "up":
            td += f'<td class="delta-up">▲{dstr}</td>'
        elif t == "down":
            td += f'<td class="delta-down">▼{dstr.replace("-","")}</td>'
        else:
            td += f'<td class="delta-flat">{dstr}</td>'

        body += f"<tr>{td}</tr>"

    return head + body


# ── HTML 模板 ──
HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>题材趋势追踪面板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
:root{{--bg:#f5f6f8;--card:#fff;--line:#ececf0;--txt:#1d1d1f;--sub:#86868b;
       --up:#ee2500;--down:#093;--blue:#3b82c4;--gold:#f0a020;--purple:#8b5cf6}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
      background:var(--bg);color:var(--txt);padding:20px;max-width:1280px;margin:0 auto}}
header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px}}
h1{{font-size:20px}}.sub{{color:var(--sub);font-size:13px}}
.cards{{display:grid;gap:14px}}
.c2{{grid-template-columns:1fr 1fr}}
@media(max-width:860px){{.c2{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;
       box-shadow:0 1px 3px rgba(0,0,0,.04)}}
.chart{{width:100%;height:300px}}
.chart-sm{{width:100%;height:200px}}
.status{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}}
.pill{{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:6px 14px;font-size:13px;white-space:nowrap}}
.pill b{{color:var(--blue)}}.pill .up{{color:var(--up);font-weight:700}}
.pill .down{{color:var(--down);font-weight:700}}
.table-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12px;min-width:720px}}
th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:center}}
th{{color:var(--sub);font-weight:600;position:sticky;top:0;background:var(--card);z-index:1}}
.theme-name{{text-align:left;font-weight:600;white-space:nowrap;min-width:90px}}
.null-val{{color:var(--sub);font-style:italic}}
.delta-up{{color:var(--up);font-weight:700}}.delta-down{{color:var(--down);font-weight:700}}.delta-flat{{color:var(--sub)}}
.lead-name{{font-weight:700;color:var(--txt)}}.lead-boards{{color:var(--sub);font-size:11px}}
.pct{{font-weight:600}}
.boards-badge{{display:inline-block;background:#fff3ea;color:var(--gold);padding:1px 5px;border-radius:8px;font-size:10px;font-weight:700;margin-left:2px}}
.strength{{font-size:14px;letter-spacing:1px}}
.s5{{color:#ee2500}}.s4{{color:#f0a020}}.s3{{color:#3b82c4}}.s2{{color:var(--sub)}}.s1{{color:#ccc}}
.sort-desc::after{{content:" ▼";font-size:10px;color:var(--up)}}
.refresh-note{{font-size:11px;color:var(--sub);text-align:center;margin-top:12px}}
</style>
</head>
<body>
<header>
  <h1>📈 题材趋势追踪面板</h1>
  <span class="sub">中转文件: theme_series.json | 生成: {gen_ts} | 自动刷新 120s</span>
</header>

<div class="status">
  <span class="pill">📊 跨度 <b>{n_days}</b> 天</span>
  <span class="pill">🏷 题材 <b>{n_themes}</b> 个</span>
  <span class="pill">📈 涨停 <b class="up">{today_total}</b> 只</span>
  <span class="pill">💎 20cm <b class="up">{cm20_today}</b> 只</span>
  <span class="pill">🔗 连板≥2 <b class="up">{b2_today}</b> 只</span>
  <span class="pill">🔝 最高 <b class="up">{today_boards}</b> 板</span>
  <span class="pill" id="countdown">🔄 下次刷新 <b>{refresh_secs}</b>s</span>
</div>

<div class="card"><div class="chart" id="line-chart"></div></div>
<div class="card" style="margin-top:14px"><div class="chart" id="bar-chart"></div></div>
<div class="card" style="margin-top:14px" id="sentiment-row">
  <div class="chart chart-sm" id="sentiment-chart"></div></div>

<div class="card" style="margin-top:14px">
  <h2 style="font-size:16px;color:var(--sub);margin-bottom:10px">📋 每日明细（强度公式：涨停数×10 + 正Δ×5 + 龙头连板×3）</h2>
  <div class="table-wrap"><table>{table_content}</table></div>
</div>

<div class="refresh-note">
  ⚠️ 盘中数据为实时快照(仅取板块涨停前10)，未列前10题材显示「—」；收盘后自动补全。★越多=今日越强势
</div>

<script>
const TREND = {data_json};
const REFRESH_SECS = 120;
let charts = {{}};

function initCharts() {{
    ['line-chart','bar-chart','sentiment-chart'].forEach(id => {{
        const el = document.getElementById(id);
        if (el) charts[id] = echarts.init(el);
    }});
    window.addEventListener('resize', () => Object.values(charts).forEach(c => c.resize()));
}}

function render() {{
    if (charts['line-chart']) charts['line-chart'].setOption(TREND.line_option, true);
    if (charts['bar-chart']) charts['bar-chart'].setOption(TREND.bar_option, true);
    if (charts['sentiment-chart'] && TREND.sentiment_option)
        charts['sentiment-chart'].setOption(TREND.sentiment_option, true);
    else {{
        const row = document.getElementById('sentiment-row');
        if (row) row.style.display = 'none';
    }}
}}

let remaining = REFRESH_SECS;
setInterval(() => {{
    remaining--;
    if (remaining <= 0) remaining = REFRESH_SECS;
    document.getElementById('countdown').innerHTML = '🔄 下次刷新 <b>' + remaining + '</b>s';
}}, 1000);

setTimeout(() => {{ location.reload(); }}, REFRESH_SECS * 1000);
document.addEventListener('DOMContentLoaded', () => {{ initCharts(); render(); }});
</script>
</body>
</html>"""


def build_trend_section(d=None):
    """供其他生成器（gen_dashboard）调用的导出函数。
    返回 (body_html: str, js_code: str) —— 可直接嵌入父模板。
    若 d 为 None，自动读 theme_series.json。
    """
    if d is None:
        from theme_data import export_series_json
        export_series_json()
        d = load_series()

    line_opt = gen_line_option(d)
    bar_opt = gen_bar_option(d)
    sentiment_opt = gen_sentiment_option(d)
    table_content = gen_table_html(d)

    details = d.get("daily_details") or []
    last_det = details[-1] if details else {}
    today_total = d["daily_totals"][-1] if d["daily_totals"] else "—"
    today_boards = d["max_boards"][-1] if d["max_boards"] else "—"

    trend_data = {
        "line_option": line_opt,
        "bar_option": bar_opt,
        "sentiment_option": sentiment_opt,
    }
    data_json = json.dumps(trend_data, ensure_ascii=False)

    body = (
        '<div class="section" style="margin-top:18px">'
        '<h2 style="font-size:17px;margin-bottom:10px">📈 题材趋势追踪（ECharts·强度排序）</h2>'
        f'<div class="status"><span class="pill">📊 跨度 <b>{len(d["dates"])}</b> 天</span>'
        f'<span class="pill">🏷 题材 <b>{len(d["theme_labels"])}</b></span>'
        f'<span class="pill">📈 涨停 <b class="up">{today_total}</b> 只</span>'
        f'<span class="pill">💎 20cm <b class="up">{last_det.get("count_20cm","—")}</b> 只</span>'
        f'<span class="pill">🔗 连板≥2 <b class="up">{last_det.get("boards_2plus","—")}</b> 只</span>'
        f'<span class="pill">🔝 最高 <b class="up">{today_boards}</b> 板</span>'
        '<span class="pill" id="trend-countdown">🔄 下次刷新 <b>120</b>s</span></div>'
        '<div class="card section"><div class="chart" id="line-chart"></div></div>'
        '<div class="card section"><div class="chart" id="bar-chart"></div></div>'
        '<div class="card section" id="sentiment-row" style="margin-top:0">'
        '<div class="chart chart-sm" id="sentiment-chart"></div></div>'
        '<div class="card section">'
        '<div style="font-size:12px;color:#86868b;margin-bottom:8px">📋 每日明细（行按复合强度排序·末列=环比上一时间节点涨停变化）</div>'
        f'<div class="table-wrap"><table>{table_content}</table></div>'
        '</div>'
        '<div class="refresh-note">⚠️ 盘中快照仅取板块涨停前10，未列前10显示「—」; 末列「环比」= 涨停数较上一时间节点变化：<span style="color:#ee2500">▲升(红)</span>/<span style="color:#093">▼降(绿)</span>/—持平(灰)</div>'
        '</div>'
    )

    js = (
        f'const TREND = {data_json};\n'
        'const REFRESH_SECS = 120;\n'
        'let trendCharts = {};\n'
        'function initTrendCharts() {\n'
        "  ['line-chart','bar-chart','sentiment-chart'].forEach(function(id) {\n"
        '    var el = document.getElementById(id);\n'
        '    if (el) trendCharts[id] = echarts.init(el);\n'
        '  });\n'
        '  window.addEventListener("resize", function() {\n'
        '    Object.values(trendCharts).forEach(function(c) { c.resize(); });\n'
        '  });\n'
        '}\n'
        'function renderTrend() {\n'
        "  if (trendCharts['line-chart']) trendCharts['line-chart'].setOption(TREND.line_option, true);\n"
        "  if (trendCharts['bar-chart']) trendCharts['bar-chart'].setOption(TREND.bar_option, true);\n"
        "  if (trendCharts['sentiment-chart'] && TREND.sentiment_option) {\n"
        "    trendCharts['sentiment-chart'].setOption(TREND.sentiment_option, true);\n"
        '  } else {\n'
        "    var row = document.getElementById('sentiment-row');\n"
        "    if (row) row.style.display = 'none';\n"
        '  }\n'
        '}\n'
        'var trendRemaining = REFRESH_SECS;\n'
        'setInterval(function() {\n'
        '  trendRemaining--;\n'
        '  if (trendRemaining <= 0) trendRemaining = REFRESH_SECS;\n'
        "  var el = document.getElementById('trend-countdown');\n"
        "  if (el) el.innerHTML = '🔄 下次刷新 <b>' + trendRemaining + '</b>s';\n"
        '}, 1000);\n'
        'setTimeout(function() { location.reload(); }, REFRESH_SECS * 1000);\n'
        'document.addEventListener("DOMContentLoaded", function() {\n'
        '  initTrendCharts();\n'
        '  renderTrend();\n'
        '});\n'
    )

    return body, js


def main():
    from theme_data import export_series_json
    export_series_json()

    d = load_series()

    line_opt = gen_line_option(d)
    bar_opt = gen_bar_option(d)
    sentiment_opt = gen_sentiment_option(d)
    table_content = gen_table_html(d)

    details = d.get("daily_details") or []
    last_det = details[-1] if details else {}
    today_total = d["daily_totals"][-1] if d["daily_totals"] else "—"
    today_boards = d["max_boards"][-1] if d["max_boards"] else "—"

    trend_data = {
        "line_option": line_opt,
        "bar_option": bar_opt,
        "sentiment_option": sentiment_opt,
    }

    html_content = HTML.format(
        gen_ts=datetime.now().strftime("%H:%M:%S"),
        n_days=len(d["dates"]),
        n_themes=len(d["theme_labels"]),
        today_total=today_total,
        today_boards=today_boards,
        cm20_today=last_det.get("count_20cm", "—"),
        b2_today=last_det.get("boards_2plus", "—"),
        refresh_secs=120,
        table_content=table_content,
        data_json=json.dumps(trend_data, ensure_ascii=False),
    )

    OUT.write_text(html_content, encoding="utf-8")
    print(f"✅ 趋势面板已生成(增强): {OUT} ({len(d['dates'])}天, {len(d['theme_labels'])}题材, "
          f"每日细节={len(details)}组)")


if __name__ == "__main__":
    main()
