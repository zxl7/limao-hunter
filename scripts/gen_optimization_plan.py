#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_optimization_plan.py — 每周五系统优化方案生成器（财神-回测 skill · 2026-09-01）

职责：聚合系统六维信号 → 生成一份「系统优化方案」（编号决策清单），
      供用户每周五逐条拍板（改 / 不改 / 改但调整）。
聚合源（全部客观）：
  1. 本周回测摘要   cache/backtest_history.jsonl（候选命中/区间命中/情绪对齐）
  2. 学习建议       cache/learning_suggestions.json（R1-R4）
  3. 待审提案       cache/learning_proposals.jsonl（P1-P4）
  4. 体检异常       python3 scripts/system_health.py --json（run_health/launchd/cache）
  5. 升级待办       system_health.upgrade_todo.todo（upgrade_plan.md 未落地项）

产出：
  cache/optimization_plan_YYYY-MM-DD.json   结构化方案（决策清单）
  cache/optimization_plan_latest.md         人类可读版（落盘）
  /tmp/optimization_plan_card.json          飞书卡（--push 时推送）

卡片布局（呼应 shared_rules 布局铁律·结论/决策清单置底）：
  首屏摘要 → 本周回测 → 学习建议 → 待审提案 → 体检异常 → 升级待办 → 📋 决策清单(置底)

用法：
  python3 scripts/gen_optimization_plan.py           # 生成方案（默认本周五）
  python3 scripts/gen_optimization_plan.py --out /tmp/opt.json
  python3 scripts/gen_optimization_plan.py --push    # 生成 + 推送飞书
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
CACHE = ROOT / "cache"
HISTORY_FILE = CACHE / "backtest_history.jsonl"
SUGGEST_FILE = CACHE / "learning_suggestions.json"
PROPOSAL_FILE = CACHE / "learning_proposals.jsonl"
PLAN_JSON = CACHE / "optimization_plan_latest.json"
PLAN_MD = CACHE / "optimization_plan_latest.md"
BJ_TZ = timezone(timedelta(hours=8))

RED = "red"
GREEN = "green"
NEUTRAL = "neutral"
PURPLE = "purple"
BLUE = "blue"


def _div(t: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": t}}


def _hr() -> dict:
    return {"tag": "hr"}


def _tag(txt: str, color: str) -> str:
    return f"<text_tag color='{color}'>{txt}</text_tag>"


def load_json_lines(p: Path) -> list[dict]:
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def load_history(n: int = 5) -> list[dict]:
    rows = load_json_lines(HISTORY_FILE)
    rows.sort(key=lambda r: r.get("settle_date", ""))
    return rows[-n:]


def load_suggestions() -> list[dict]:
    if not SUGGEST_FILE.exists():
        return []
    try:
        return (json.loads(SUGGEST_FILE.read_text(encoding="utf-8")) or {}).get("suggestions", [])
    except Exception:
        return []


def load_proposals() -> list[dict]:
    return load_json_lines(PROPOSAL_FILE)


def load_learning_effect() -> dict | None:
    """学习效果回看（PDCA · 2026-09-18 增强）：读 learning_history 最后一条带 `effect` 的记录。

    周度方案展示「上轮自动学习改了什么 · 改后命中率是否改善」——迭代闭环的验证端。
    """
    p = CACHE / "learning_history.jsonl"
    try:
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return None
    for r in reversed(rows):
        if r.get("effect"):
            return {"effect": r["effect"], "applied": r.get("applied_summary"),
                    "ts": r.get("generated_at"), "range_hit_rate_avg": r.get("range_hit_rate_avg")}
    return None


def load_factor_attr() -> dict | None:
    """因子有效性归因（R6 数据源 · 只读账本 + 候选快照·零新增抓取）。样本 <20 → None。"""
    p = ROOT / "scripts" / "factor_attribution.py"
    if not p.exists():
        return None
    try:
        r = subprocess.run([sys.executable, str(p), "--json"],
                           capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout)
    except Exception:
        return None
    return d if isinstance(d, dict) and int(d.get("n_all") or 0) >= 20 else None


def load_card_quality(days: int = 7) -> dict | None:
    """卡面质量自检（R7 数据源）：重复文案 / 黑话 / 结构异常。"""
    p = ROOT / "scripts" / "card_quality_check.py"
    if not p.exists():
        return None
    try:
        r = subprocess.run([sys.executable, str(p), "--json", "--days", str(days)],
                           capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout)
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def load_health() -> dict:
    """跑体检拿客观数据（六维）。"""
    try:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "system_health.py"), "--json"],
                           capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        return json.loads(r.stdout) if r.stdout else {}
    except Exception:
        return {}


# ---------------- 决策清单构建 ----------------

def build_decisions(hist, sugs, props, health) -> list[dict]:
    """聚合六维信号 → 编号决策项。每项含：id/类别/标题/证据/建议动作/影响面/默认建议。"""
    decisions = []
    n = 1

    # --- 组A：回测/学习偏差（回测层·最高优先级）---
    if hist:
        rates_a = [r["summary"].get("candidate_hit_rate") for r in hist if r["summary"].get("candidate_hit_rate") is not None]
        rates_b = [r["summary"].get("range_hit_rate") for r in hist if r["summary"].get("range_hit_rate") is not None]
        avg_a = sum(rates_a) / len(rates_a) if rates_a else None
        avg_b = sum(rates_b) / len(rates_b) if rates_b else None
        if avg_b is not None and avg_b < 40:
            decisions.append({
                "id": f"O{n}", "cat": "回测/学习", "sev": "high",
                "title": f"预期区间命中率持续偏低（均值 {avg_b:.0f}%）",
                "evidence": f"近{len(rates_b)}日区间命中率均值 {avg_b:.1f}% < 40%（预期区间系统性偏乐观）",
                "action": "R1 已自动回写 base_range（幅度 0.3·可回滚）；建议保持窗口≥3日观察，或人工审阅 expectation_vpa 相位/溢价叠加逻辑",
                "impact": "影响竞价预期区间→买入判定",
                "default": "保持（已自动回写·观察）",
            })
            n += 1
        if avg_a is not None and avg_a < 40:
            decisions.append({
                "id": f"O{n}", "cat": "回测/学习", "sev": "high",
                "title": f"候选池命中率持续走低（均值 {avg_a:.0f}%）",
                "evidence": f"近{len(rates_a)}日候选命中率均值 {avg_a:.1f}% < 40%",
                "action": "P2 提案已生成：核查财神-选股 relay/watch 门槛（first_choice_min 上调 2 分候选）；样本≥3才生效",
                "impact": "影响选股质量→eod/verify 卡",
                "default": "待拍板（提案 P2）",
            })
            n += 1

    # --- 组B：待审提案（skill 更新层·用户拍板核心）---
    pending_props = [p for p in props if p.get("status") in ("draft",)]
    for p in pending_props:
        decisions.append({
            "id": f"O{n}", "cat": "提案", "sev": "medium",
            "title": p.get("title", "skill 更新提案"),
            "evidence": p.get("evidence", ""),
            "action": f"提案 {p['proposal_id']}：改 {p['target_skill']}:{p['target_section']}（{p.get('suggest','')}）",
            "impact": f"影响 {p['target_skill']} skill 规则",
            "default": "待拍板（approved 后 agent 手动应用）",
        })
        n += 1

    # --- 组C：体检异常（系统健康层）---
    rh = health.get("run_health", {})
    for s in rh.get("stale", []):
        decisions.append({
            "id": f"O{n}", "cat": "体检", "sev": "high",
            "title": f"任务异常：{s.get('name')}",
            "evidence": f"最近成功运行超过预期频率（rrule: {s.get('rrule')}）",
            "action": "检查该任务日志 cache/logs/ + 审计 cache/audit.jsonl，确认是否 rrule 漂移/调度未生效；必要时 PAUSED→ACTIVE 刷新",
            "impact": "任务推送链路",
            "default": "排查修复",
        })
        n += 1
    never = [x for x in rh.get("never", []) if "待首次触发" not in x.get("note", "")]
    for s in never:
        decisions.append({
            "id": f"O{n}", "cat": "体检", "sev": "medium",
            "title": f"任务从未运行：{s.get('name')}",
            "evidence": f"rrule: {s.get('rrule')}（非排期待触发）",
            "action": "重点核查：创建后调度可能未生效，检查 run_health.never",
            "impact": "任务推送链路",
            "default": "核查修复",
        })
        n += 1
    ld = health.get("launchd", {})
    for item in ld.get("running", []):
        if "未加载" in item:
            decisions.append({
                "id": f"O{n}", "cat": "体检", "sev": "medium",
                "title": f"launchd 守护未加载：{item}",
                "evidence": "守护进程未加载 → 盘中数据刷新靠兜底扫盘+猎手覆盖",
                "action": f"可选：launchctl load ~/Library/LaunchAgents/{item.split(' ')[0]}（系统操作·先确认兜底 ACTIVE）",
                "impact": "盘中数据实时性（dashboard 刷新）",
                "default": "可暂不处理（有兜底）",
            })
            n += 1
    for issue in health.get("cache", {}).get("issues", [])[:3]:
        # 过滤信息性提示（体检自身标注"非故障"的守卫态，如 pre_auction）
        if "非故障" in str(issue) or str(issue).startswith("ℹ️"):
            continue
        decisions.append({
            "id": f"O{n}", "cat": "体检", "sev": "medium",
            "title": f"数据产物异常：{issue}",
            "evidence": "cache 体检发现问题项",
            "action": "检查对应脚本补跑（record_zt_snapshot/capture_daily/theme_rotation 等）",
            "impact": "盘后数据链",
            "default": "核查补跑",
        })
        n += 1

    # --- 组D：升级待办（upgrade_plan 未落地·提醒）---
    for t in health.get("upgrade_todo", {}).get("todo", [])[:4]:
        decisions.append({
            "id": f"O{n}", "cat": "升级", "sev": "low",
            "title": f"升级待办提醒：{t.get('id')} {t.get('title', '')[:30]}",
            "evidence": "upgrade_plan.md 未落地项",
            "action": "拍板是否本期启动（P1-5 退潮聚合卡 / P2-7 涨停降噪 / P2-10 审计日志 / P1-7 外部新闻源 / P2-11 / P2-12）",
            "impact": "功能增强",
            "default": "暂缓",
        })
        n += 1

    return decisions


# ---------------- 输出 ----------------

def rate_color(rate: float | None) -> str:
    if rate is None:
        return NEUTRAL
    if rate >= 60:
        return RED
    if rate < 40:
        return GREEN
    return BLUE


def render_card(plan: dict) -> dict:
    el = []
    decisions = plan["decisions"]
    hist = plan["week_hist"]
    sug_count = len(plan["suggestions"])
    prop_pending = len([p for p in plan["proposals"] if p.get("status") == "draft"])
    health_issues = plan["health_issue_count"]
    high_count = len([d for d in decisions if d.get("sev") == "high"])

    # 首屏摘要
    if hist:
        rates_a = [r["summary"].get("candidate_hit_rate") for r in hist if r["summary"].get("candidate_hit_rate") is not None]
        rates_b = [r["summary"].get("range_hit_rate") for r in hist if r["summary"].get("range_hit_rate") is not None]
        avg_a = sum(rates_a) / len(rates_a) if rates_a else None
        avg_b = sum(rates_b) / len(rates_b) if rates_b else None
        summary_txt = (f"候选命中 {_tag(f'{avg_a:.0f}%' if avg_a is not None else '—', rate_color(avg_a))}"
                       f" ｜ 区间命中 {_tag(f'{avg_b:.0f}%' if avg_b is not None else '—', rate_color(avg_b))}")
    else:
        summary_txt = "近 5 日无回测记录"
    el.append(_div(
        f"**🛠 系统优化方案 {plan['week_label']}**\n"
        f"{summary_txt} ｜ 建议 {len(decisions)} 项"
        f"（高优 {_tag(str(high_count), RED)}）"
        f"｜ 提案 {_tag(str(prop_pending), BLUE)}"
        f"｜ 体检异常 {_tag(str(health_issues), GREEN)}"
    ))
    el.append(_hr())

    # 本周回测
    el.append(_div("**📊 本周回测**"))
    if hist:
        for r in hist:
            m = r["metrics"]
            a, b, c = m["candidate_hit"], m["range_hit"], m["sentiment_align"]
            a_txt = f"{a['hit_rate']}%" if a.get("hit_rate") is not None else "—"
            b_txt = f"{b['hit_rate']}%" if b.get("hit_rate") is not None else "—"
            c_txt = f"{c['diff']:+.1f}" if c.get("diff") is not None else "—"
            el.append(_div(
                f"{r['settle_date'][5:]} 候选 {_tag(a_txt, rate_color(a.get('hit_rate')))}"
                f"｜区间 {_tag(b_txt, rate_color(b.get('hit_rate')))}"
                f"｜情绪 {_tag(c_txt, NEUTRAL)}"
            ))
    else:
        el.append(_div("（回测数据尚未积累）"))
    el.append(_hr())

    # 学习建议
    el.append(_div(f"**🔁 学习建议**（{sug_count}）"))
    for s in plan["suggestions"]:
        sev = s.get("severity", "low")
        sc = RED if sev == "high" else (BLUE if sev == "medium" else NEUTRAL)
        el.append(_div(f"{_tag(s['rule'], sc)} {s['evidence']} → {s['suggest']}"))
    el.append(_hr())

    # 🔄 学习效果回看（PDCA·2026-09-18）——"上轮自动改了什么、改后有没有变好"
    eff = plan.get("learning_effect")
    if eff and eff.get("effect"):
        e = eff["effect"]
        imp = e.get("improved")
        el.append(_div(f"**🔄 学习效果回看** {_tag('改善' if imp else '未改善', RED if imp else GREEN)}"))
        el.append(_div(f"　上轮自动应用：{('、'.join(eff.get('applied') or []))[:80]}"))
        el.append(_div(f"　区间命中率：{_tag(str(e.get('range_hit_rate')), rate_color(eff.get('range_hit_rate_avg')))}（自 {(e.get('since') or '')[:10]}）"))
        if not imp:
            el.append(_div("　⚠️ 效果未改善 → 可考虑 `learning.py --rollback <快照>` 回退该次改动"))
        el.append(_hr())

    # 📈 因子归因（R6·2026-09-18）——哪些因子在拖后腿
    fa = plan.get("factor_attr")
    if fa:
        bb = fa.get("by_bucket") or {}
        el.append(_div(f"**📈 因子归因**（样本 {fa.get('n_all')} 笔·胜率 {fa.get('win_rate_pct')}%）"))
        for k, lbl in (("relay", "接力池"), ("watch", "观察池")):
            b = bb.get(k) or {}
            if b.get("win_rate_pct") is not None:
                el.append(_div(f"　{lbl} {b.get('n')} 笔·胜率 {_tag(f'{b['win_rate_pct']}%', rate_color(b.get('win_rate_pct')))}"
                               f"｜均收益 {b.get('avg_ret'):+.2f}%"))
        rev = sorted([f for f in (fa.get("numeric") or [])
                      if f.get("diff") is not None and float(f["diff"]) < 0
                      and min(int(f.get("n_win") or 0), int(f.get("n_lose") or 0)) >= 10],
                     key=lambda x: x["diff"])[:3]
        if rev:
            el.append(_div("　反向因子（值越高越易亏）："
                           + "、".join(f"{f.get('label')} {f['diff']:+.2f}" for f in rev)))
        el.append(_hr())

    # 🔍 卡面质量（R7·2026-09-18）
    cq = plan.get("card_quality")
    if cq and cq.get("checked"):
        _cards = cq.get("cards") or []
        jar = sorted({j for c in _cards for j in (c.get("jargon") or [])})
        rep = sum(len(c.get("repeat") or []) for c in _cards)
        ok = not (rep or jar or sum(len(c.get("struct") or []) for c in _cards))
        el.append(_div(f"**🔍 卡面质量**（近{cq.get('days')}天 {cq.get('checked')} 张·问题 {cq.get('issues')} 张）"
                       + (_tag("干净", GREEN) if ok else "")))
        if rep:
            el.append(_div(f"　⚠️ 重复文案 {rep} 处"))
        if jar:
            el.append(_div(f"　🚫 黑话 {len(jar)} 个：{'、'.join(jar[:8])}"))
        el.append(_hr())

    # 待审提案
    draft_props = [p for p in plan["proposals"] if p.get("status") == "draft"]
    el.append(_div(f"**📋 待审提案**（{len(draft_props)}）"))
    for p in draft_props:
        el.append(_div(f"{_tag(p['proposal_id'], BLUE)} {p['title']} → {_tag(p['target_skill'], PURPLE)}:{p['target_section']}"))
    el.append(_hr())

    # 体检异常
    el.append(_div(f"**🏥 体检异常**（{plan['health_issue_count']}）"))
    if plan["health_issue_count"] == 0:
        el.append(_div("系统体检全绿"))
    else:
        for d in decisions:
            if d.get("cat") == "体检":
                el.append(_div(f"{_tag(d['id'], NEUTRAL)} {d['title']}"))
    el.append(_hr())

    # 📋 决策清单（置底·结论最后）
    el.append(_div(f"**📋 决策清单**（{len(decisions)} 项·回复编号拍板）"))
    for d in decisions:
        sc = RED if d.get("sev") == "high" else (BLUE if d.get("sev") == "medium" else NEUTRAL)
        el.append(_div(
            f"{_tag(d['id'], sc)} {d['title']}\n"
            f"  证据：{d['evidence']}\n"
            f"  建议：{d['action']}\n"
            f"  影响：{d['impact']}｜默认：{d['default']}"
        ))

    return {"title": f"🛠 系统优化方案 {plan['week_label']}", "color": "blue", "elements": el}


def build_plan() -> dict:
    hist = load_history(5)
    sugs = load_suggestions()
    props = load_proposals()
    health = load_health()
    decisions = build_decisions(hist, sugs, props, health)

    # 本周标签（当前周五）
    now = datetime.now(BJ_TZ)
    week_label = f"{now.strftime('%m-%d')} 周"

    plan = {
        "schema": "optimization_plan_v1",
        "generated_at": now.isoformat(timespec="seconds"),
        "week_label": week_label,
        "week_hist": hist,
        "suggestions": sugs,
        "proposals": props,
        "health": {k: health.get(k) for k in ("run_health", "launchd", "cache", "upgrade_todo")},
        "health_issue_count": len([d for d in decisions if d.get("cat") == "体检"]),
        "decisions": decisions,
        # 2026-09-18 增强（用户「做上去」）：PDCA 效果回看 + 因子归因 + 卡面质量
        "learning_effect": load_learning_effect(),
        "factor_attr": load_factor_attr(),
        "card_quality": load_card_quality(7),
    }
    PLAN_JSON.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(plan)
    return plan


def write_md(plan: dict):
    """人类可读版落盘。"""
    lines = [f"# 🛠 系统优化方案 {plan['week_label']}", ""]
    for d in plan["decisions"]:
        sev = d.get("sev", "low")
        lines += [
            f"## {d['id']} [{d['cat']}·{sev}] {d['title']}",
            f"- 证据：{d['evidence']}",
            f"- 建议：{d['action']}",
            f"- 影响：{d['impact']}",
            f"- 默认：{d['default']}",
            "",
        ]
    PLAN_MD.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="周五系统优化方案生成器")
    ap.add_argument("--out", help="输出卡 JSON 路径（默认 /tmp/optimization_plan_card.json）")
    ap.add_argument("--push", action="store_true", help="生成后推送飞书")
    ap.add_argument("--webhook", default="feishu_webhook_test",
                    help="推送群键名（默认 feishu_webhook_test 测试群：与回测卡同族内部验证卡，不进正式群；"
                         "需进正式群显式传 feishu_webhook_prod）")
    args = ap.parse_args()
    plan = build_plan()
    card = render_card(plan)
    out = args.out or "/tmp/optimization_plan_card.json"
    Path(out).write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[gen_optimization_plan] 已生成 {len(plan['decisions'])} 项决策 → {out} + {PLAN_MD.name}")
    if args.push:
        # 2026-09-02 修：优化方案卡属回测族内部卡，默认推测试群（与 gen_backtest_card 一致）
        subprocess.run([sys.executable, str(ROOT / "scripts" / "push_card.py"), out,
                        "--webhook", args.webhook], check=False)


if __name__ == "__main__":
    main()
