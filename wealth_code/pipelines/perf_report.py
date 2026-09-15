#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真实成绩可视化 —— 财富密码·数据沉淀（二）

输入：
  - wealth_code/cache/account_nav_history.jsonl   （净值账本：等权尾盘收益复利）
  - wealth_code/cache/account_strategy_metrics.json（策略指标：胜率/平均收益/覆盖度）

输出：
  - wealth_code/data/performance.md     （纯文本表格，便于飞书/终端查看）
  - wealth_code/data/performance.html   （自包含 SVG 图表：净值曲线 + 胜率，离线可用）

配色（中国习惯）：涨=红 #e23c3c，跌=绿 #1aa260。
说明（真实战绩）：仅展示已结算交易日的真实交易结果（实际竞价买入 → 尾盘收盘卖出口径），
不含任何历史回测面板；胜率取最新推荐日的「尾盘收益·可入场(tradable)」口径。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
DATA_DIR = ROOT / "data"
NAV_PATH = CACHE_DIR / "account_nav_history.jsonl"
METRICS_PATH = CACHE_DIR / "account_strategy_metrics.json"
MD_PATH = DATA_DIR / "performance.md"
HTML_PATH = DATA_DIR / "performance.html"

BJ_TZ = timezone(timedelta(hours=8))

# 中国习惯：涨红 / 跌绿
RED = "#e23c3c"      # 涨
GREEN = "#1aa260"    # 跌
INK = "#1f2933"
MUTED = "#6b7280"
GRID = "#e5e7eb"
PANEL = "#ffffff"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _load_metrics() -> dict[str, Any] | None:
    if not METRICS_PATH.exists():
        return None
    try:
        return json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _agg_winrate(nav_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """跨账本所有已结算日聚合（与卡片 _load_metrics 口径一致）。

    账本 account_nav_history.jsonl 是已结算战绩的单一真相源：每行=一个交易日
    的等权尾盘收益。胜率=盈利日数/总交易日，平均收益=各交易日平均收益之均值。
    此前只读最新一条记录（_latest_winrate），会丢失历史盈利/亏损日 → 胜率失真。
    """
    if not nav_rows:
        return {}
    rets = [float(r.get("avg_return_pct") or 0.0) for r in nav_rows]
    n = len(rets)
    wins = sum(1 for x in rets if x > 0)
    losses = sum(1 for x in rets if x < 0)
    win_rate = round(wins * 100.0 / n, 1) if n else 0.0
    avg_return = round(sum(rets) / n, 2) if n else 0.0
    last = nav_rows[-1]
    return {
        "recommendation_date": str(last.get("recommendation_date") or ""),
        "trade_date": str(last.get("trade_date") or ""),
        "covered": n,
        "win_rate": win_rate,
        "avg_return": avg_return,
        "win_count": wins,
        "loss_count": losses,
        "avg_win_return": round(sum(x for x in rets if x > 0) / wins, 2) if wins else 0.0,
        "avg_loss_return": round(sum(x for x in rets if x < 0) / losses, 2) if losses else 0.0,
    }


def _color_pct(v: float) -> str:
    if v > 0:
        return RED
    if v < 0:
        return GREEN
    return MUTED


def build_perf_payload() -> dict[str, Any]:
    nav_rows = _read_jsonl(NAV_PATH)
    metrics = _load_metrics()
    wr = _agg_winrate(nav_rows)

    if nav_rows:
        first = nav_rows[0]
        last = nav_rows[-1]
        # 起始净值：首行 nav / (1 + 首行收益%)
        try:
            base_nav = round(last.get("nav", 1.0), 6)
            first_ret = float(first.get("avg_return_pct") or 0)
            implied_base = round(first.get("nav", 1.0) / (1 + first_ret / 100.0), 6)
        except Exception:
            implied_base = 1.0
        total_return = round((last.get("nav", 1.0) - implied_base) * 100.0, 2)
        latest_nav = round(last.get("nav", 1.0), 4)
        trade_days = len(nav_rows)
    else:
        implied_base = 1.0
        total_return = 0.0
        latest_nav = 1.0
        trade_days = 0

    return {
        "generated_at_bj": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "base_nav": implied_base,
        "latest_nav": latest_nav,
        "total_return_pct": total_return,
        "trade_days": trade_days,
        "nav_rows": nav_rows,
        "winrate": wr,
        "metrics_records": (metrics or {}).get("records", []),
    }


def _nav_svg(nav_rows: list[dict[str, Any]]) -> str:
    """自包含净值曲线 SVG（离线可用，无外部依赖）。"""
    W, H = 760, 260
    pad_l, pad_r, pad_t, pad_b = 56, 24, 24, 40
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b

    if not nav_rows:
        return (f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
                f'role="img" aria-label="净值曲线（暂无数据）">'
                f'<rect x="0" y="0" width="{W}" height="{H}" fill="{PANEL}"/>'
                f'<text x="{W/2}" y="{H/2}" fill="{MUTED}" font-size="14" '
                f'text-anchor="middle">暂无已结算净值数据</text></svg>')

    n = len(nav_rows)
    navs = [float(r.get("nav", 1.0)) for r in nav_rows]
    lo, hi = min(navs), max(navs)
    if hi - lo < 1e-9:
        hi += 0.01
        lo -= 0.01
    # y 留白
    span = hi - lo
    lo -= span * 0.15
    hi += span * 0.15

    def x(i: int) -> float:
        return pad_l + (plot_w * i / max(n - 1, 1))

    def y(v: float) -> float:
        return pad_t + plot_h * (1 - (v - lo) / (hi - lo))

    # 网格 + y 轴刻度
    grid_lines = ""
    for k in range(5):
        val = lo + (hi - lo) * k / 4
        yy = y(val)
        grid_lines += (
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{W-pad_r}" y2="{yy:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
            f'<text x="{pad_l-8}" y="{yy+4:.1f}" fill="{MUTED}" font-size="10" '
            f'text-anchor="end">{val:.3f}</text>'
        )

    # x 轴标签（最多 8 个避免拥挤）
    x_labels = ""
    step = max(1, (n + 7) // 8)
    for i in range(n):
        if i % step == 0 or i == n - 1:
            xx = x(i)
            label = str(nav_rows[i].get("trade_date") or "")[5:]  # MM-DD
            x_labels += (
                f'<text x="{xx:.1f}" y="{H-pad_b+18}" fill="{MUTED}" font-size="10" '
                f'text-anchor="middle">{label}</text>'
            )

    # 分段着色（涨红/跌绿）
    segs = ""
    pts = []
    for i, r in enumerate(nav_rows):
        px, py = x(i), y(navs[i])
        pts.append((px, py))
    for i in range(1, n):
        col = RED if navs[i] >= navs[i - 1] else GREEN
        segs += (
            f'<line x1="{pts[i-1][0]:.1f}" y1="{pts[i-1][1]:.1f}" '
            f'x2="{pts[i][0]:.1f}" y2="{pts[i][1]:.1f}" '
            f'stroke="{col}" stroke-width="2.5" stroke-linecap="round"/>'
        )
    # 数据点（最新点放大）
    dots = ""
    for i, (px, py) in enumerate(pts):
        large = i == n - 1
        col = RED if (i == 0 or navs[i] >= navs[i - 1]) else GREEN
        dots += (
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{3.2 if large else 2.2}" '
            f'fill="{col}" stroke="#fff" stroke-width="1"/>'
        )

    return (
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="净值曲线">'
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="{PANEL}"/>'
        f'{grid_lines}{segs}{dots}'
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{H-pad_b}" stroke="{INK}" stroke-width="1"/>'
        f'<line x1="{pad_l}" y1="{H-pad_b}" x2="{W-pad_r}" y2="{H-pad_b}" stroke="{INK}" stroke-width="1"/>'
        f'{x_labels}'
        f'<text x="{pad_l}" y="{pad_t-8}" fill="{INK}" font-size="11" font-weight="600">'
        f'账户净值（等权尾盘收益·复利）</text>'
        f'</svg>'
    )


def _winrate_html(wr: dict[str, Any]) -> str:
    if not wr:
        return '<p style="color:#6b7280">暂无胜率数据</p>'
    rate = float(wr.get("win_rate", 0.0))
    col = RED if rate >= 50 else GREEN
    covered = int(wr.get("covered", 0))
    win = int(wr.get("win_count", 0))
    loss = int(wr.get("loss_count", 0))
    avg = float(wr.get("avg_return", 0.0))
    avg_col = _color_pct(avg)
    return f"""
    <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:8px 0 4px">
      <div style="font-size:34px;font-weight:700;color:{col}">{rate:.1f}%</div>
      <div style="font-size:13px;color:#6b7280;line-height:1.6">
        尾盘胜率（可入场口径）<br>
        覆盖 <b style="color:#1f2933">{covered}</b> 只 · 胜 <b style="color:{RED}">{win}</b> ·
        负 <b style="color:{GREEN}">{loss}</b><br>
        平均尾盘收益 <b style="color:{avg_col}">{avg:+.2f}%</b>
      </div>
    </div>
    <div style="background:#f3f4f6;border-radius:6px;height:10px;overflow:hidden;margin-top:6px">
      <div style="width:{rate:.1f}%;background:{col};height:100%"></div>
    </div>
    """


def build_html(payload: dict[str, Any]) -> str:
    nav_rows = payload["nav_rows"]
    wr = payload["winrate"]
    base = payload["base_nav"]
    latest = payload["latest_nav"]
    total = payload["total_return_pct"]
    total_col = _color_pct(total)
    trade_days = payload["trade_days"]
    svg = _nav_svg(nav_rows)

    # 明细表
    rows_html = ""
    for r in nav_rows:
        ret = float(r.get("avg_return_pct") or 0)
        rcol = _color_pct(ret)
        navv = float(r.get("nav", 1.0))
        ncol = _color_pct(navv - base)
        names = "、".join(r.get("names") or []) or "—"
        rows_html += (
            f"<tr><td>{r.get('trade_date','')}</td><td>{r.get('recommendation_date','')}</td>"
            f"<td>{r.get('stock_count',0)}</td>"
            f"<td style='color:{rcol};font-weight:600'>{ret:+.2f}%</td>"
            f"<td style='color:{ncol};font-weight:600'>{navv:.4f}</td>"
            f"<td style='color:#6b7280;font-size:12px'>{names}</td></tr>"
        )
    if not rows_html:
        rows_html = "<tr><td colspan='6' style='color:#6b7280'>暂无明细</td></tr>"

    wr_html = _winrate_html(wr)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>财富密码·真实成绩</title>
<style>
  body {{ font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         background:#f7f8fa;color:#1f2933;margin:0;padding:24px; }}
  .wrap {{ max-width:820px;margin:0 auto; }}
  .card {{ background:#fff;border:1px solid #eceef1;border-radius:12px;
          padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.04); }}
  h1 {{ font-size:20px;margin:0 0 4px }}
  .sub {{ color:#6b7280;font-size:12px;margin-bottom:12px }}
  .kpis {{ display:flex;gap:14px;flex-wrap:wrap; }}
  .kpi {{ flex:1;min-width:120px;background:#fafbfc;border:1px solid #eef0f3;
         border-radius:10px;padding:12px 14px; }}
  .kpi .v {{ font-size:22px;font-weight:700; }}
  .kpi .l {{ font-size:12px;color:#6b7280;margin-top:2px }}
  table {{ width:100%;border-collapse:collapse;font-size:13px;margin-top:8px }}
  th,td {{ text-align:left;padding:7px 8px;border-bottom:1px solid #f0f1f3 }}
  th {{ color:#6b7280;font-weight:600;font-size:12px }}
  .foot {{ color:#9ca3af;font-size:11px;margin-top:10px }}
  .sec-title {{ font-size:15px;font-weight:600;margin:0 0 6px }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>财富密码 · 真实成绩</h1>
    <div class="sub">账户净值与胜率面板 · 生成于 {payload['generated_at_bj']}（北京时间）</div>
    <div class="kpis">
      <div class="kpi"><div class="v" style="color:{total_col}">{total:+.2f}%</div>
        <div class="l">累计收益（基准 {base:.4f}）</div></div>
      <div class="kpi"><div class="v">{latest:.4f}</div>
        <div class="l">最新净值</div></div>
      <div class="kpi"><div class="v">{trade_days}</div>
        <div class="l">已结算交易日</div></div>
    </div>
  </div>

  <div class="card">
    <p class="sec-title">净值曲线（真实交易·等权尾盘复利）</p>
    {svg}
    <div class="foot">红=较前一交易日上涨，绿=下跌（中国习惯）。仅含已结算的真实交易样本。</div>
  </div>

  <div class="card">
    <p class="sec-title">尾盘胜率（最新推荐日·可入场口径）</p>
    {wr_html}
  </div>

  <div class="card">
    <p class="sec-title">每日明细</p>
    <table>
      <thead><tr><th>交易日</th><th>推荐日</th><th>股数</th>
        <th>平均收益</th><th>净值</th><th>个股</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <div class="foot">数据源：account_nav_history.jsonl + account_strategy_metrics.json
    ｜财富密码闭环·数据沉淀</div>
</div>
</body>
</html>
"""


def build_md(payload: dict[str, Any]) -> str:
    nav_rows = payload["nav_rows"]
    wr = payload["winrate"]
    base = payload["base_nav"]
    latest = payload["latest_nav"]
    total = payload["total_return_pct"]
    trade_days = payload["trade_days"]

    lines = []
    lines.append("# 财富密码 · 真实成绩")
    lines.append("")
    lines.append(f"> 生成于 {payload['generated_at_bj']}（北京时间）")
    lines.append("")
    lines.append("## 关键指标")
    lines.append("")
    lines.append(f"- 累计收益：**{total:+.2f}%**（基准 {base:.4f}）")
    lines.append(f"- 最新净值：**{latest:.4f}**")
    lines.append(f"- 已结算交易日：**{trade_days}**")
    if wr:
        lines.append(f"- 尾盘胜率（可入场）：**{float(wr.get('win_rate',0)):.1f}%** "
                     f"（覆盖 {wr.get('covered',0)} 只，胜 {wr.get('win_count',0)} / 负 {wr.get('loss_count',0)}）")
        lines.append(f"- 平均尾盘收益：**{float(wr.get('avg_return',0)):+.2f}%**")
    lines.append("")
    lines.append("## 每日明细")
    lines.append("")
    lines.append("| 交易日 | 推荐日 | 股数 | 平均收益 | 净值 | 个股 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in nav_rows:
        ret = float(r.get("avg_return_pct") or 0)
        navv = float(r.get("nav", 1.0))
        names = "、".join(r.get("names") or []) or "—"
        sign = "+" if ret >= 0 else ""
        lines.append(f"| {r.get('trade_date','')} | {r.get('recommendation_date','')} | "
                     f"{r.get('stock_count',0)} | {sign}{ret:.2f}% | {navv:.4f} | {names} |")
    lines.append("")
    lines.append("---")
    lines.append("数据源：account_nav_history.jsonl + account_strategy_metrics.json ｜ "
                 "红涨绿跌（中国习惯）｜ 真实交易战绩（等权尾盘收益·复利）")
    return "\n".join(lines)


def run_perf_report(*, md_path: Path | None = None, html_path: Path | None = None) -> dict[str, Any]:
    payload = build_perf_payload()
    md_path = md_path or MD_PATH
    html_path = html_path or HTML_PATH
    md_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(build_md(payload), encoding="utf-8")
    html_path.write_text(build_html(payload), encoding="utf-8")
    return payload


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="真实成绩可视化")
    ap.add_argument("--md", type=Path, default=MD_PATH)
    ap.add_argument("--html", type=Path, default=HTML_PATH)
    args = ap.parse_args()
    payload = run_perf_report(md_path=args.md, html_path=args.html)
    print(str(args.html))
    print(f"trade_days={payload['trade_days']} latest_nav={payload['latest_nav']} "
          f"total={payload['total_return_pct']:+.2f}% "
          f"winrate={payload['winrate'].get('win_rate') if payload['winrate'] else 'N/A'}")


if __name__ == "__main__":
    main()
