#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build 盘中猎手-1400 card from FRESHEST cache (live snapshot)."""
import json, sys, importlib.util
sys.path.insert(0, '.')

def load(p):
    return json.load(open(p, encoding='utf-8'))

d = load('cache/pulse_snapshots/latest.json')
fb = load('cache/feedback_matrix.json')
pool = load('cache/institution_pool_2026-10-09.json')['items']
pcn = {it['code']: it['name'] for it in pool}

# ---- dynamic numbers (single fresh read) ----
total_zt = d.get('total_zt', 0)
sd = d.get('sentiment_detail', {}) or {}
seal_rate = sd.get('seal_rate', 0)
broken_n = (d.get('broken') or {}).get('count', 0)
ld_n = (d.get('limit_down') or {}).get('count', 0)
idx = d.get('index', [])
idx_parts = []
for x in idx:
    pn = {'上证指数':'上证','深证成指':'深证','创业板指':'创业板'}.get(x['name'], x['name'])
    idx_parts.append(f"{pn} {x['pct']:+.2f}%")
idx_line = ' ｜ '.join(idx_parts)

sent_score = fb.get('sentiment_score', 0)
sent_label = fb.get('sentiment_label', '')
promote_rate = fb.get('promote_rate')
lb_pr = fb.get('lb_promote_rate')
avg_pct = fb.get('avg_pct')
nuke = fb.get('nuke_count')
prev_max = fb.get('prev_max_boards')
now_max = fb.get('now_max_boards')
prev_zt = fb.get('prev_zt_count')
prev_date = fb.get('prev_date')

# top5
top5 = d.get('top5', [])
def get_sec(name):
    for t in top5:
        if t['name'] == name:
            return t
    return None

# sector_risk (stale flag but 资金榜仍当日真实数据 per memory note)
sr = d.get('sector_risk') or {}
inflow = (sr.get('inflow_top') or [])[:5]
outflow = (sr.get('outflow_top') or [])[:5]
def fmt_flow(x):
    star = '⭐' if x.get('is_focus') else ''
    sign = '+' if x['flow_yi'] >= 0 else ''
    return f"{x['name']} {sign}{x['flow_yi']:.1f}亿{star}"

# institution X / Y / Top
ztc = set(fb.get('today_zt_codes', []))
def norm(c): return c.replace('.SZ','').replace('.SS','').replace('.SH','')
leaders = d.get('leaders', [])
X_codes = [norm(it['code']) for it in pool if norm(it['code']) in ztc]
Y_list = []
for l in leaders:
    c = norm(l.get('code',''))
    if c in pcn and (l.get('pct',0) >= 3) and not (l.get('sealed') is True) and c not in ztc:
        Y_list.append((pcn[c], c, round(l.get('pct',0),2)))
X = len(X_codes)
Y = len(Y_list)
top_inst = []
for it in pool:
    cn = norm(it['code'])
    rec = next((l for l in leaders if norm(l.get('code','')) == cn), None)
    pct = rec.get('pct', 0) if rec else 0
    is_zt = cn in ztc
    top_inst.append((it['name'], it['code'], pct, is_zt))
top_inst = sorted(top_inst, key=lambda r: (r[3], r[2]), reverse=True)[:3]

# buyable top3 (strong 强势可买)
buy = [l for l in leaders if l.get('buyable') is True and (l.get('pct',0) >= 9.9)]
buy_sorted = sorted(buy, key=lambda x: -x.get('pct',0))
buy_top3 = buy_sorted[:3]

# top sector + leader for title
top1 = top5[0] if top5 else None
t1_name = top1['name'] if top1 else ''
t1_lead = (top1.get('leader') or {}) if top1 else {}
t1_lname = t1_lead.get('name','')
t1_lpct = t1_lead.get('pct',0)

title = f"盘中猎手·增量 | 14:00 | {t1_name}·{t1_lname}+{t1_lpct:.2f}% | 涨停{total_zt}"

print("=== dynamic ===")
print("total_zt", total_zt, "sent", sent_score, sent_label, "idx", idx_line, "broken", broken_n, "ld", ld_n, "seal", seal_rate)
print("X", X, "Y", Y, "top_inst", top_inst)
print("buy_top3", [(b['name'], b['code'], b['pct']) for b in buy_top3])

# ---- narrative (oriented to current sentiment = 偏强 7.1) ----
# 结论定调 (≤2 lines) + 🧘 踏空警告
conclusion = ("⚠️踏空时刻·偏强延续但控仓试错（锚定消费/电池/医药前排硬催化）\n"
             f"晋级率{promote_rate}%破阀值·核按钮{nuke}收敛·指数翻红但科技硬件仍净流出出货")

# 大盘概览
overview = (f"<text_tag color='neutral'>📈 大盘概览</text_tag>\n"
            f"{idx_line}\n"
            f"涨停 {total_zt}｜封板率 {seal_rate:.0f}%｜炸板 {broken_n}｜跌停 {ld_n}\n"
            f"📉 情绪演变：分歧(3/10) → <text_tag color='red'>偏强({sent_score}/10)</text_tag>｜<text_tag color='red'>转好</text_tag>（假期后首日出清·午后共振回暖）")

# 信号摘要
sig = (f"<text_tag color='neutral'>📡 信号摘要</text_tag>\n"
       f"🆕新1(AI大模型·0板) 🔥跃升0 ↗持续5\n")
for b in buy_top3:
    sig += f"<text_tag color='purple'>{b['name']}({b['code']}) +{b['pct']:.2f}% 现价{b['price']} [{b['sector']}·20cm强势]</text_tag>\n"

# 昨日反馈
yfb = (f"<text_tag color='neutral'>📊 昨日涨停反馈（{prev_date} 涨停{prev_zt}）</text_tag>\n"
       f"晋级 {promote_rate}%｜连板晋级 {lb_pr}%｜昨涨停今均 +{avg_pct}%｜核按钮 {nuke}｜最高板 {prev_max}→{now_max}｜情绪 {sent_score}/10·{sent_label}")

# 题材榜
def sec_block(t):
    name = t['name']; zt = t['zt_count']; delta = t.get('delta')
    state = '〔↗持续〕' if delta == 'ongoing' else '〔新〕'
    desc = t.get('desc','')
    motiv = desc[:14] if desc else ''
    lead = t.get('leader') or {}
    lname = lead.get('name',''); lcode = lead.get('code','').replace('.SZ','').replace('.SS','')
    lboards = lead.get('boards',''); lpct = lead.get('pct',0)
    fol = t.get('followers',[])[:4]
    fol_s = '·'.join(f"{f['name']}+{f['pct']:.2f}%" for f in fol)
    return (f"📌 <text_tag color='red'>{name} 涨停{zt} {state}</text_tag> ｜ 动因：{motiv}\n"
            f"<text_tag color='red'>{lname}({lcode}) {lboards} +{lpct:.2f}%</text_tag>\n"
            f"跟：{fol_s}")
sec_names = ['大消费','锂电池','医药']
blocks = []
for sn in sec_names:
    t = get_sec(sn)
    if t:
        blocks.append(sec_block(t))
topic = (f"<text_tag color='neutral'>🔥 突发题材榜</text_tag>\n" + "\n\n".join(blocks))

# 资金流向 fields 双列
flow_title = "<text_tag color='neutral'>💰 板块资金流向</text_tag>"
fields = []
for i in range(5):
    inf = inflow[i] if i < len(inflow) else None
    out = outflow[i] if i < len(outflow) else None
    if inf:
        fields.append({"is_short": True, "text": {"tag": "lark_md", "content": f"<text_tag color='red'>{fmt_flow(inf)}</text_tag>"}})
    if out:
        fields.append({"is_short": True, "text": {"tag": "lark_md", "content": f"<text_tag color='green'>{fmt_flow(out)}</text_tag>"}})

# 机构对账
inst_line = (f"🏛️ 机构今日点名·盘面：涨停 {X} 只｜拉升中 {Y} 只(+3%+)｜Top："
             + "·".join(f"<text_tag color='violet'>{n}({c})</text_tag>{'+涨停' if z else f'+{p:.2f}%'}" for n,c,p,z in top_inst))

# 情绪核心
core = (f"<text_tag color='neutral'>👑 情绪核心</text_tag>\n"
        f"<text_tag color='red'>新华传媒(600825) 9板 [资产重组]｜生命周期：加速（一字9板空间龙）</text_tag>\n"
        f"<text_tag color='red'>大消费→九阳股份 5天5板 〔带领性►·突破性►·唯一性►〕</text_tag>\n"
        f"<text_tag color='red'>锂电池→时代万恒 5天5板 〔带领性►·突破性►·唯一性►〕</text_tag>\n"
        "依据：唯一性强（新华传媒9板全市场最高·九阳/时代万恒5板双线并立）｜带领性强（九阳带大消费9家涨停·时代万恒带锂电池7家）｜突破性中（新华传媒破前高但逼近监管红线）\n"
        "明日看点：新华传媒9板逼近监管红线·开板即核按钮风险；九阳/时代万恒5板谁接棒空间龙")

# AI 研判 三段式 (oriented to 偏强)
ai = (f"<text_tag color='neutral'>🧠 AI综合研判</text_tag>\n"
      "① 主线定性：\n"
      f"<text_tag color='purple'>大消费 ⭐确定性最强主线</text_tag>：2500亿以旧换新国债全下达+9家涨停·政策硬催化持续性最强\n"
      f"<text_tag color='blue'>锂电池·确定性次强</text_tag>：十五五新型电池规划催化+时代万恒5板空间+电池链资金净流入霸榜\n"
      f"<text_tag color='yellow'>医药·仅低位可跟踪</text_tag>：创新药出海授权密集+华北制药2板+20cm弹性\n"
      "② 资金与情绪：\n"
      "资金切换——电池/资源链净流入(电池+44亿) vs 科技硬件全线下杀(电子-164亿/通信-144亿/半导体-109亿/PCB-96亿)；情绪分歧→偏强但广度仍弱(涨2515/跌2901)\n"
      "③ 风险与纪律：\n"
      "A. 科技硬件(电子/通信/半导体/PCB)净流出超510亿·节前强势线今日集体出货·禁抄底\n"
      "B. 新华传媒9板一字逼近监管红线·开板即核按钮·不接飞刀\n"
      "C. 晋级率25.6%刚破阀值但promote_downtrend·接力趋势未扭转·控仓试错\n"
      "D. 满屏20cm强势(芒果/科恒/向日葵)弹性大·紧止损")

# assemble
def div(content):
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}
def hrtag():
    return {"tag": "hr"}

elements = [
    div(conclusion), hrtag(),
    div(overview), hrtag(),
    div(yfb), hrtag(),
    div(topic), hrtag(),
    div(flow_title), {"tag": "div", "fields": fields},
    div(inst_line), hrtag(),
    div(core), hrtag(),
    div(ai),
]

card = {
    "title": title,
    "color": "red",
    "elements": elements,
    "note": "📡 选股宝+东财+猎手 | 14:00 | AI盘中诊断\n⚠️ 本报告基于公开信息分析，投资需谨慎"
}

spec = importlib.util.spec_from_file_location("card_lint", "scripts/card_lint.py")
cl = importlib.util.module_from_spec(spec); spec.loader.exec_module(cl)
ok, errs, warns = cl.lint_card(card)
print("LINT ok=%s" % ok)
for e in errs: print("  ERR:", e)
for w in warns: print("  WARN:", w)
if not ok:
    print("LINT FAILED - aborting")
    sys.exit(1)

with open('/tmp/limao_card.json', 'w', encoding='utf-8') as f:
    json.dump(card, f, ensure_ascii=False, indent=2)
print("WROTE /tmp/limao_card.json")
