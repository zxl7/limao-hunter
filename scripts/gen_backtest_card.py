#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_backtest_card.py — 回测卡渲染器（财神-回测 skill · 2026-09-01）

读 cache/backtest_report_{date}.json + cache/learning_suggestions.json → 渲染飞书蓝卡。
复用 scripts/push_card.py 单入口推送。布局铁律：
  - 🎯 结论/学习建议置底（呼应 shared_rules 盘中卡渲染铁律·结论最后）
  - 语义色板：命中率高（≥60%）=red 改善；命中率低（<40%）=green 恶化；40-60%=neutral
  - 个股名 text_tag 紫色高亮（已有语义色除外）
  - 数据唯一：每个指标只在专属行出现一次

用法：
  python3 scripts/gen_backtest_card.py                     # 最近回测报告
  python3 scripts/gen_backtest_card.py --date 2026-08-31
  python3 scripts/gen_backtest_card.py --week              # 5日周报版（近5个回测记录汇总）
  python3 scripts/gen_backtest_card.py --out /tmp/backtest_card.json   # 只渲染不推送
推送（render + push）：
  python3 scripts/gen_backtest_card.py --push
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
CACHE = ROOT / "cache"
# 白话词表单一源（同目录 card_copy_rules.py）；本脚本可能被以任意 cwd 调起
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import card_copy_rules as _ccr  # noqa: E402
REPORT_DIR = CACHE
HISTORY_FILE = CACHE / "backtest_history.jsonl"
SUGGEST_FILE = CACHE / "learning_suggestions.json"
BJ_TZ = timezone(timedelta(hours=8))

# ⛔ 2026-10-18 用户拍板：个股/区间的「命中率」是动态描述量，
#    高低不构成对错判据 → 卡面只报事实（几只落区），不报比率红绿判定、不列偏离 Top3。
#    置 True 可恢复「区间命中率 + 偏离 Top3」的旧观感（数据本身一直留在 backtest_report JSON 里，未删）。
RANGE_HIT_VERDICT = False

# 语义色板（A股：改善红/恶化绿；本卡是数据诊断卡，命中率高低=质量好坏）
RED = "red"
GREEN = "green"
NEUTRAL = "neutral"
PURPLE = "purple"
BLUE = "blue"


def _tidy(s: str) -> str:
    """收尾清理（渲染出口·不改判据）：
    1) sanitize 把词替换成白话/空串后可能留下悬空顿号（如「…：A、、\n」）；
    2) R7 会复述它扫到的「黑话词 N 个：A、B」——这些词按宪法本就不许上卡，
       逐个改写后数量与列举必然对不上（2026-11-13 实证：2 个只列出 1 个）→ 只留数量。
    """
    s = re.sub(r"黑话词\s*(\d+)\s*个[：:][^\n]*", r"黑话词 \1 个（已按白话规范改写）", s)
    s = re.sub(r"[、，,]{2,}", "、", s)
    s = re.sub(r"[、，,]{1,}[ \t]*(?=\n|$)", "", s)
    return s


def _div(t: str) -> dict:
    # 2026-09-18：渲染出口统一白话化（词表单一源 = card_copy_rules.sanitize）——
    # 建议文案可能来自已落盘的旧记录，否则 09-21 起阻断期会把整卡拦死。
    return {"tag": "div", "text": {"tag": "lark_md", "content": _tidy(_ccr.sanitize(t))}}


def _hr() -> dict:
    return {"tag": "hr"}


def _tag(txt: str, color: str) -> str:
    return f"<text_tag color='{color}'>{txt}</text_tag>"


def load_report(date_str: str) -> dict | None:
    p = REPORT_DIR / f"backtest_report_{date_str}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_history(n: int = 5) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    rows = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("settle_date", ""))
    return rows[-n:]


def load_suggestions() -> list[dict]:
    if not SUGGEST_FILE.exists():
        return []
    try:
        d = json.loads(SUGGEST_FILE.read_text(encoding="utf-8"))
        return d.get("suggestions", [])
    except Exception:
        return []


def load_proposals() -> list[dict]:
    """读待审 skill 提案流（learning_proposals.jsonl·财神-回测提案模式）。"""
    pf = CACHE / "learning_proposals.jsonl"
    if not pf.exists():
        return []
    rows = []
    for line in pf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def rate_color(rate: float | None) -> str:
    """命中率着色：≥60 红（改善/质量高）/ <40 绿（恶化/质量低）/ 中间蓝。"""
    if rate is None:
        return NEUTRAL
    if rate >= 60:
        return RED
    if rate < 40:
        return GREEN
    return BLUE


def render_daily(report: dict, suggestions: list[dict]) -> dict:
    settle = report["settle_date"]
    rec = report["rec_date"]
    m = report["metrics"]
    s = report["summary"]
    el = []

    # 首屏：标题行
    a, b, c = m["candidate_hit"], m["range_hit"], m["sentiment_align"]
    a_txt = f"{a['hit_rate']}%" if a.get("hit_rate") is not None else "—"
    b_txt = f"{b['hit_rate']}%" if b.get("hit_rate") is not None else "—"
    c_txt = f"{c['diff']:+.1f}" if c.get("diff") is not None else "—"
    if RANGE_HIT_VERDICT:
        b_seg = f"｜区间命中 {_tag(b_txt, rate_color(b.get('hit_rate')))}"
    else:
        b_seg = (f"｜区间落区 {b.get('hit_count')}/{b.get('evaluable')}"
                 if b.get("available") and b.get("evaluable") else "｜区间落区 —")
    el.append(_div(
        f"**📊 推测回测 {settle[5:]}** ｜ 推荐日 {rec[5:]} 结算\n"
        f"候选命中 {_tag(a_txt, rate_color(a.get('hit_rate')))}"
        f"{b_seg}"
        f"｜情绪差 {_tag(c_txt, RED if (c.get('diff') or 0) > 1 else (GREEN if (c.get('diff') or 0) < -1 else NEUTRAL))}"
    ))
    el.append(_hr())

    # 候选命中明细
    el.append(_div(f"**🎯 候选池命中**"))
    if a.get("available"):
        el.append(_div(
            f"推荐 {a.get('stock_count')} 只 ｜ 平均收益 {a.get('avg_return_pct'):+.2f}%"
            f" ｜ 正收益 {a.get('hit_count')} 只（命中率 {_tag(str(a['hit_rate'])+'%', rate_color(a.get('hit_rate')))}）"
        ))
        rows = a.get("stocks", [])
        if rows:
            line = " ｜ ".join(
                f"{_tag(r['name'], PURPLE)}({r['return_pct']:+.1f}%)"
                for r in rows[:5]
            )
            el.append(_div(line))
    else:
        el.append(_div(f"⛔ {a.get('reason', '无数据')}"))
    el.append(_hr())

    # 预期区间（只报落区只数，不做命中率判决）
    el.append(_div(f"**🎯 预期区间**（竞价涨幅 vs 预期区间）"))
    if b.get("available"):
        if b.get("evaluable"):
            if RANGE_HIT_VERDICT:
                el.append(_div(
                    f"评估 {b.get('evaluable')} 只 ｜ 命中 {b.get('hit_count')} 只"
                    f"（命中率 {_tag(str(b['hit_rate'])+'%', rate_color(b.get('hit_rate')))}）"
                ))
                # 展示未命中 Top3（最偏离的）
                rows = b.get("rows", [])
                if rows:
                    misses = [r for r in rows if not r["hit"]]
                    misses.sort(key=lambda r: abs(r["actual_gap_pct"] - (r["expected_range"][0] + r["expected_range"][1]) / 2), reverse=True)
                    for r in misses[:3]:
                        er = r["expected_range"]
                        el.append(_div(
                            f"{_tag(r['name'], PURPLE)} 预期 {er[0]:+.1f}~{er[1]:+.1f}%"
                            f" ｜ 实际 {r['actual_gap_pct']:+.2f}% {_tag('偏离', GREEN)}"
                        ))
            else:
                el.append(_div(f"评估 {b.get('evaluable')} 只 ｜ {b.get('hit_count')} 只落在区间内"))
        else:
            el.append(_div(f"⛔ {b.get('reason', '无评估数据')}"))
    else:
        el.append(_div(f"⛔ {b.get('reason', '无竞价数据')}"))
    el.append(_hr())

    # 情绪分对齐
    el.append(_div(f"**🧠 情绪分对齐**（盘前预测 vs 收盘实际）"))
    if c.get("available"):
        p = c.get("predict")
        p_txt = f"{p}~{c.get('predict_hi')}" if c.get("predict_hi") != p else f"{p}"
        diff = c.get("diff", 0)
        dir_txt = "偏高" if diff > 0 else ("偏低" if diff < 0 else "精准")
        color = RED if abs(diff) > 1 else (GREEN if abs(diff) < 0.3 else BLUE)
        el.append(_div(
            f"预测 {p_txt}/10 ｜ 实际 {c.get('actual')}/10 ｜ "
            f"差 {_tag(f'{diff:+.1f}（{dir_txt}）', color)}"
        ))
    else:
        el.append(_div("⛔ 盘前预测分未解析到（文本格式非「预判 X-Y/10」）"))
    el.append(_hr())

    # 学习建议（置底·结论最后）
    el.append(_div(f"**🔁 自我学习建议**"))
    if suggestions:
        for sug in suggestions:
            sev = sug.get("severity", "low")
            tag_color = RED if sev == "high" else (BLUE if sev == "medium" else NEUTRAL)
            el.append(_div(
                f"{_tag(sug['rule'], tag_color)} {sug['evidence']}\n"
                f"→ {sug['suggest']}"
            ))
    else:
        el.append(_div("窗口内无显著偏差，系统推测与实盘吻合"))

    card = {
        "title": f"📊 推测回测 {settle[5:]}",
        "color": "blue",
        "elements": el,
    }
    return card


def render_week(suggestions: list[dict]) -> dict:
    """5 日周报版：近 5 个回测记录汇总。"""
    hist = load_history(5)
    if not hist:
        return {"title": "📊 推测回测周报", "color": "blue",
                "elements": [_div("近 5 日无回测记录（先跑 backtest_engine）")]}
    el = []
    el.append(_div("**📊 推测回测周报（近 5 日）**"))
    el.append(_hr())

    for r in hist:
        settle = r["settle_date"]
        m = r["metrics"]
        a, b, c = m["candidate_hit"], m["range_hit"], m["sentiment_align"]
        a_txt = f"{a['hit_rate']}%" if a.get("hit_rate") is not None else "—"
        b_txt = f"{b['hit_rate']}%" if b.get("hit_rate") is not None else "—"
        c_txt = f"{c['diff']:+.1f}" if c.get("diff") is not None else "—"
        el.append(_div(
            f"{settle[5:]} 候选 {_tag(a_txt, rate_color(a.get('hit_rate')))}"
            f"｜区间 {_tag(b_txt, rate_color(b.get('hit_rate')))}"
            f"｜情绪 {_tag(c_txt, NEUTRAL)}"
        ))
    el.append(_hr())

    # 汇总均值
    rates_a = [r["summary"].get("candidate_hit_rate") for r in hist if r["summary"].get("candidate_hit_rate") is not None]
    rates_b = [r["summary"].get("range_hit_rate") for r in hist if r["summary"].get("range_hit_rate") is not None]
    avg_a = sum(rates_a) / len(rates_a) if rates_a else None
    avg_b = sum(rates_b) / len(rates_b) if rates_b else None
    el.append(_div(
        f"**均值**：候选命中 {_tag(f'{avg_a:.0f}%' if avg_a is not None else '—', rate_color(avg_a))}"
        f" ｜ 区间命中 {_tag(f'{avg_b:.0f}%' if avg_b is not None else '—', rate_color(avg_b))}"
    ))

    el.append(_hr())
    el.append(_div(f"**🔁 自我学习建议**"))
    if suggestions:
        for sug in suggestions:
            sev = sug.get("severity", "low")
            tag_color = RED if sev == "high" else (BLUE if sev == "medium" else NEUTRAL)
            el.append(_div(f"{_tag(sug['rule'], tag_color)} {sug['evidence']} → {sug['suggest']}"))
    else:
        el.append(_div("窗口内无显著偏差"))

    # 📋 待审提案（2026-09-01 提案模式·置底）
    props = load_proposals()
    pending = [p for p in props if p.get("status") in ("draft", "approved")]
    if pending:
        el.append(_hr())
        el.append(_div(f"**📋 待审 skill 提案**（{len(pending)} 项·确认后由 agent 手动应用）"))
        for p in pending:
            st = "✅已回写" if p.get("status") == "approved" else "🕐待审"
            el.append(_div(
                f"{st} {_tag(p['proposal_id'], BLUE if p.get('status') == 'draft' else RED)}\n"
                f"→ {_tag(p['target_skill'], PURPLE)}：{p['title']}\n"
                f"证据：{p['evidence']}"
            ))

    return {"title": "📊 推测回测周报", "color": "blue", "elements": el}


def main():
    ap = argparse.ArgumentParser(description="回测卡渲染器")
    ap.add_argument("--date", help="回测结算日 YYYY-MM-DD（缺省=最近一份报告）")
    ap.add_argument("--week", action="store_true", help="5 日周报版")
    ap.add_argument("--out", help="输出 JSON 路径（不推送）")
    ap.add_argument("--push", action="store_true", help="渲染后推送（复用 push_card.py）")
    ap.add_argument("--webhook", default="feishu_webhook_test",
                    help="推送群键名（默认 feishu_webhook_test 测试群：回测卡属内部验证类，不进正式群；"
                         "需进正式群显式传 feishu_webhook_prod）")
    args = ap.parse_args()

    suggestions = load_suggestions()

    if args.week:
        card = render_week(suggestions)
        date_tag = "week"
    else:
        if args.date:
            report = load_report(args.date)
        else:
            # 最近一份
            reports = sorted(REPORT_DIR.glob("backtest_report_*.json"), reverse=True)
            report = load_report(reports[0].name.replace("backtest_report_", "").replace(".json", "")) if reports else None
        if not report:
            print("[gen_backtest_card] 无回测报告，先跑 backtest_engine.py")
            sys.exit(1)
        card = render_daily(report, suggestions)
        date_tag = report["settle_date"]

    out_path = args.out or f"/tmp/backtest_card_{date_tag}.json"
    Path(out_path).write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[gen_backtest_card] 已渲染 → {out_path}")

    if args.push:
        import subprocess
        # 2026-09-02 修：回测卡为内部验证卡，默认推测试群（此前未指定 → push_card 按工作日自动选正式群，误投主群）
        subprocess.run([sys.executable, str(ROOT / "scripts" / "push_card.py"), out_path,
                        "--webhook", args.webhook], check=False)


if __name__ == "__main__":
    main()
