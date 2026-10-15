#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build 盘中快照-1445 card from FRESHEST cache (横向全景·骨架 B.5).

⛔ 2026-10-14 已废弃：快照卡型并入盘中猎手（骨架 B.5 废弃·生产不再走本脚本）。
   保留仅作历史参考；盘中卡一律由平台任务按【骨架 B：盘中猎手 v3】生成。
"""
import json, sys, importlib.util
sys.path.insert(0, '.')

def load(p):
    return json.load(open(p, encoding='utf-8'))

d = load('cache/pulse_snapshots/latest.json')
fb = load('cache/feedback_matrix.json')

total_zt = d.get('total_zt', 0)
sd = d.get('sentiment_detail', {}) or {}
seal_rate = sd.get('seal_rate', 0)
broken_n = (d.get('broken') or {}).get('count', 0)
ld_n = (d.get('limit_down') or {}).get('count', 0)
idx = d.get('index', [])
name_map = {'上证指数': '上证', '深证成指': '深证', '创业板指': '创业板'}
idx_parts = [f"{name_map.get(x['name'], x['name'])} {x['pct']:+.2f}%" for x in idx]
idx_line = ' ｜ '.join(idx_parts)
amount_yi = next((x.get('amount_yi') for x in idx if x['name'] == '上证指数'), None)
br = d.get('breadth') or {}

sent_score = fb.get('sentiment_score', 0)
sent_label = fb.get('sentiment_label', '')
promote_rate = fb.get('promote_rate')
lb_pr = fb.get('lb_promote_rate')
avg_pct = fb.get('avg_pct')
nuke = fb.get('nuke_count')
up_rate = fb.get('up_rate')
prev_max = fb.get('prev_max_boards')
now_max = fb.get('now_max_boards')
prev_zt = fb.get('prev_zt_count')
prev_date = fb.get('prev_date')

top5 = d.get('top5', [])
def get_sec(name):
    for t in top5:
        if t['name'] == name:
            return t
    return None

sr = d.get('sector_risk') or {}
inflow = (sr.get('inflow_top') or [])[:5]
outflow = (sr.get('outflow_top') or [])[:5]
def fmt_flow(x):
    star = '⭐' if x.get('is_focus') else ''
    sign = '+' if x['flow_yi'] >= 0 else ''
    return f"{x['name']} {sign}{x['flow_yi']:.1f}亿{star}"

print("=== dynamic ===")
print("total_zt", total_zt, "sent", sent_score, sent_label, "idx", idx_line)
print("broken", broken_n, "ld", ld_n, "seal", seal_rate, "breadth", br, "amount", amount_yi)

# ---- 结论定调（≤2 行）----
conclusion = (
    "🎯 **尾盘偏强·新线接棒，做低位不追高位**\n"
    f"晋级率{promote_rate}%刚过修复线、核按钮{nuke}只仍在警戒线上；接力趋势未扭转·9板空间龙逼近监管红线"
)

# ---- 大盘概览 ----
overview = (
    "<text_tag color='neutral'>📈 大盘概览</text_tag>\n"
    f"{idx_line}\n"
    f"**涨停 {total_zt}**｜封板率 {seal_rate:.0f}%｜炸板 {broken_n}｜跌停 {ld_n}\n"
    f"涨 <text_tag color='red'>{br.get('up','-')}</text_tag> : 跌 <text_tag color='green'>{br.get('down','-')}</text_tag>"
    + (f" ｜ 成交 {amount_yi/10000:.2f}万亿" if amount_yi else "")
)

# ---- 情绪演变 ----
_prev_score = 7.1  # 上一张定时卡（盘中猎手-1400·14:29 推送）写回值
if abs(sent_score - _prev_score) >= 2:
    _dir, _dc = "转好" if sent_score > _prev_score else "转差", "red" if sent_score > _prev_score else "green"
elif sent_score > _prev_score:
    _dir, _dc = "转好", "red"
elif sent_score < _prev_score:
    _dir, _dc = "转差", "green"
else:
    _dir, _dc = "持平", "blue"
evo = (
    f"📉 情绪演变：偏强({_prev_score}/10) → <text_tag color='{_dc}'>{sent_label}({sent_score}/10)</text_tag>"
    f"｜<text_tag color='{_dc}'>{_dir}</text_tag>（情绪分与上卡一致·广度尾盘由跌多翻涨多）"
)

# ---- 突发题材榜 ----
MOTIV = {
    '影视': '《三星堆》AI电影定档10·23',
    '大消费': '2500亿以旧换新国债全部下达',
    '锂电池': '新型电池十五五规划落地',
    '医药': '创新药对外授权密集落地',
    '网络安全': 'AI模型访问权限扩大',
}
EXCLUDE = ('麒麟信安',)  # 科创板 688：用户无权限，源头剔除

def sec_block(t, is_new):
    name, zt = t['name'], t['zt_count']
    icon = '🆕' if is_new else '📌'
    state = '〔新〕' if is_new else '〔↗持续〕'
    motiv = MOTIV.get(name, (t.get('desc') or '')[:15])
    lead = t.get('leader') or {}
    lname = lead.get('name', '')
    lcode = lead.get('code', '').replace('.SZ', '').replace('.SS', '').replace('.SH', '')
    lboards = lead.get('boards', '')
    lpct = lead.get('pct', 0)
    fol = [f for f in (t.get('followers') or [])
           if not any(x in f['name'] for x in EXCLUDE)][:3]
    fol_s = '·'.join(f"{f['name']}+{f['pct']:.2f}%" for f in fol)
    head = f"{icon} <text_tag color='red'>{name} 涨停{zt} {state}</text_tag> ｜ 动因：{motiv}"
    ldr = f"<text_tag color='red'>{lname}({lcode}){' ' + lboards if lboards else ''} +{lpct:.2f}%</text_tag>"
    return head + "\n" + ldr + (("\n跟：" + fol_s) if fol_s else "")

blocks = []
sec_specs = [('影视', True), ('大消费', False), ('锂电池', False), ('医药', False), ('网络安全', False)]
for sn, is_new in sec_specs:
    t = get_sec(sn)
    if t:
        blocks.append(sec_block(t, is_new))
topic = "<text_tag color='neutral'>🔥 突发题材榜</text_tag>\n" + "\n\n".join(blocks)

# ---- 核心标的（横向清单·整行红·不评可买）----
core_rows = [
    "新华传媒(600825) 9天9板 +10.01% [资产重组]",
    "九阳股份(002242) 5天5板 +10.04% [大消费]",
    "时代万恒(600241) 5天5板 +10.01% [锂电池]",
    "中文在线(300364) +20.01% [影视·20cm]",
    "芒果超媒(300413) +19.98% [影视·20cm]",
    "领湃科技(300530) 2天2板 +20.02% [锂电池·20cm]",
    "绿盟科技(300369) +20.02% [网络安全·20cm]",
    "中红医疗(300981) +19.98% [医药·20cm]",
    "天融信(002212) +10.04% [网络安全]",
    "华北制药(600812) 2天2板 +10.04% [医药]",
]
core = "<text_tag color='neutral'>📡 核心标的</text_tag>\n" + "\n".join(
    f"<text_tag color='red'>{r}</text_tag>" for r in core_rows)

# ---- 昨日涨停反馈 ----
yfb = (
    f"<text_tag color='neutral'>📊 昨日涨停反馈（{prev_date} 涨停{prev_zt}）</text_tag>\n"
    f"晋级 {promote_rate}%｜连板晋级 {lb_pr}%｜昨涨停今均 +{avg_pct}%｜高开率 {up_rate}%"
    f"｜核按钮 {nuke}｜最高板 {prev_max}→{now_max}｜情绪 {sent_score}/10·{sent_label}"
)

# ---- 板块资金流向 fields 双列 ----
flow_title = "<text_tag color='neutral'>💰 板块资金流向</text_tag>"
fields = []
for i in range(5):
    inf = inflow[i] if i < len(inflow) else None
    out = outflow[i] if i < len(outflow) else None
    if inf:
        fields.append({"is_short": True, "text": {"tag": "lark_md",
                       "content": f"<text_tag color='red'>{fmt_flow(inf)}</text_tag>"}})
    if out:
        fields.append({"is_short": True, "text": {"tag": "lark_md",
                       "content": f"<text_tag color='green'>{fmt_flow(out)}</text_tag>"}})

def div(content):
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}
def hrtag():
    return {"tag": "hr"}

top1 = top5[0] if top5 else {}
hhmm = str(d.get('ts', ''))[11:16] or '14:36'
title = f"盘中快照-1445·增量 {hhmm} | {top1.get('name','')}·涨停{total_zt}"

elements = [
    div(conclusion), hrtag(),
    div(overview), hrtag(),
    div(evo), hrtag(),
    div(topic), hrtag(),
    div(core), hrtag(),
    div(yfb), hrtag(),
    div(flow_title), {"tag": "div", "fields": fields},
]

card = {
    "title": title,
    "color": "red",
    "elements": elements,
    "note": f"📡 选股宝+东财 | {hhmm}\n⚠️ 本报告基于公开信息分析，投资需谨慎",
}

spec = importlib.util.spec_from_file_location("card_lint", "scripts/card_lint.py")
cl = importlib.util.module_from_spec(spec); spec.loader.exec_module(cl)
ok, errs, warns = cl.lint_card(card)
print("LINT ok=%s" % ok)
for e in errs:
    print("  ERR:", e)
for w in warns:
    print("  WARN:", w)
if not ok:
    print("LINT FAILED - aborting")
    sys.exit(1)

with open('/tmp/limao_card.json', 'w', encoding='utf-8') as f:
    json.dump(card, f, ensure_ascii=False, indent=2)
print("WROTE /tmp/limao_card.json")
print("TITLE:", title)
