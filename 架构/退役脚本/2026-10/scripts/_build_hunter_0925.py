import json, sys
ROOT = "/Users/zxl/WorkBuddy/狸猫猎手"
SNAP = f"{ROOT}/cache/pulse_snapshots"

d = json.load(open(f"{SNAP}/latest.json", encoding="utf-8"))
fb = json.load(open(f"{ROOT}/cache/feedback_matrix.json", encoding="utf-8"))
pool = json.load(open(f"{ROOT}/cache/institution_pool_2026-09-21.json", encoding="utf-8"))

idx = d.get("index", [])
idx_line = " ｜ ".join(f"{x['name']} {x['pct']:+.2f}%" for x in idx)  # 上证/深证/创业板
total_zt = d.get("total_zt")
broken = d.get("broken", {}) or {}
broken_n = broken.get("count") if isinstance(broken, dict) else None
ld = d.get("limit_down", {}) or {}
ld_n = ld.get("count") if isinstance(ld, dict) else (len(ld) if isinstance(ld, list) else None)
seal_rate = d.get("seal_rate")

# sentiment
sent = fb.get("sentiment_score")  # 3.5
slab = fb.get("sentiment_label")   # 分歧

# top5 for 题材榜 + 信号摘要
top5 = d.get("top5", [])
new_n = sum(1 for t in top5 if t.get("delta") == "new")
surge_n = sum(1 for t in top5 if t.get("delta") == "surging")
ongo_n = sum(1 for t in top5 if t.get("delta") == "ongoing")

# buyable leaders (可买Top3)
leads = d.get("leaders", [])
buyable = [l for l in leads if l.get("buyable") and not l.get("sealed")]
buyable.sort(key=lambda l: float(l.get("pct") or 0), reverse=True)
top3 = buyable[:3]

# sector risk
sr = d.get("sector_risk", {})
inflow = sr.get("inflow_top", [])[:5]
outflow = sr.get("outflow_top", [])[:5]

# institution 对账
pool_codes = {it["code"] for it in pool.get("items", [])}
# 今日涨停 codes (ladder boards>=1 + sealed candidates/leaders)
ladder_codes = set()
for lv in d.get("ladder", []):
    for nm in lv.get("stocks", []):
        ladder_codes.add(nm)
active = {}  # code -> (name, pct, sealed)
for l in leads:
    active[l["code"]] = (l.get("name"), float(l.get("pct") or 0), l.get("sealed"))
for c in d.get("candidates", {}).get("candidates", []) if isinstance(d.get("candidates"), dict) else []:
    active.setdefault(c["code"], (c.get("name"), float(c.get("pct") or 0), c.get("sealed")))
zt_codes = set()
for nm in ladder_codes:
    # ladder stores names; map by matching active name
    for code, (name, pct, sealed) in active.items():
        if name == nm:
            zt_codes.add(code)
# also sealed in leaders/candidates
for code, (name, pct, sealed) in active.items():
    if sealed:
        zt_codes.add(code)
X = len(pool_codes & zt_codes)  # 机构票涨停数
Y = 0
top_inst = []
for code, (name, pct, sealed) in active.items():
    if code in pool_codes and not sealed and pct >= 3:
        Y += 1
        top_inst.append((name, code, pct))
top_inst.sort(key=lambda x: x[2], reverse=True)
# 机构票是否进可买Top3
inst_in_top3 = any(c["code"] in pool_codes for c in top3)
render_inst = (X >= 1 or Y >= 2 or inst_in_top3)

print(f"[dbg] idx={idx_line} zt={total_zt} broken={broken_n} ld={ld_n} seal={seal_rate}")
print(f"[dbg] sent={sent}/{slab} new={new_n} surge={surge_n} ongo={ongo_n}")
print(f"[dbg] buyable top3: {[(c['name'],c['code'],c['pct']) for c in top3]}")
print(f"[dbg] inst X={X} Y={Y} top_inst={top_inst[:3]} render={render_inst}")

def red(s): return f"<text_tag color='red'>{s}</text_tag>"
def grn(s): return f"<text_tag color='green'>{s}</text_tag>"
def blu(s): return f"<text_tag color='blue'>{s}</text_tag>"
def pur(s): return f"<text_tag color='purple'>{s}</text_tag>"
def neu(s): return f"<text_tag color='neutral'>{s}</text_tag>"
def yel(s): return f"<text_tag color='yellow'>{s}</text_tag>"

ASOF = "09:42"

elements = []
# E1 结论定调
elements.append({"tag": "div", "text": {"tag": "lark_md", "content":
    f"{neu('🎯 结论定调')}\n观望·控仓试错——医药主线可轻仓跟随，空间龙与高位缩量板不追\n依据：晋级率深破25%修复线 ｜ 情绪{sent}/10分歧 ｜ 华瓷5板处监管临界"}})
elements.append({"tag": "hr"})
# E3 大盘概览
seal_txt = f"{seal_rate:.1f}%" if isinstance(seal_rate, (int, float)) else "待更新"
ld_txt = f"｜跌停 {ld_n}" if isinstance(ld_n, int) else ""
gl = (f"{neu('📈 大盘概览')}\n{idx_line}\n"
      f"涨停 {total_zt}｜封板率 {seal_txt}｜炸板 {broken_n}{ld_txt}\n"
      f"📉 情绪演变：上卡(09-18盘中猎手)1.9/10退潮 → 本卡{sent}/10{slab}｜转好(+1.6)\n"
      f"{neu('📡 信号摘要')}\n🆕新{new_n} 🔥跃升{surge_n} ↗持续{ongo_n}\n" +
      "\n".join(pur(f"{c['name']}({c['code']}) +{c['pct']:.2f}% {c.get('sector','')}" + (" 20cm" if c.get("is_20cm") else "")) for c in top3))
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": gl}})
elements.append({"tag": "hr"})
# E5 昨日反馈
fb_line = (f"{neu('📊 昨日涨停反馈（'+fb.get('prev_date')+' 涨停'+str(fb.get('prev_zt_count'))+'）')}\n"
           f"晋级 {grn(str(fb.get('promote_rate'))+'%')}｜连板晋级 {red(str(fb.get('lb_promote_rate'))+'%')}｜"
           f"昨涨停今均 {fb.get('avg_pct')}%｜核按钮 {fb.get('nuke_count')}｜最高板 {fb.get('prev_max_boards')}→{fb.get('now_max_boards')}｜"
           f"情绪 {sent}/10·{slab}")
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": fb_line}})
elements.append({"tag": "hr"})
# E7 突发题材榜 TOP3
med = next((t for t in top5 if t.get("name") == "医药"), None)
rob = next((t for t in top5 if t.get("name") == "机器人"), None)
chip = next((t for t in top5 if t.get("name") == "国产芯片"), None)
def topic_block(t, leader_name, leader_code, leader_boards, leader_pct, prefix=""):
    icon = "🔥 " if prefix else "📌 "
    name = t.get("name")
    desc = (t.get("desc") or "")[:18]
    leader = red("%s(%s) %s +%s%% [%s]" % (leader_name, leader_code, leader_boards, leader_pct, name))
    return "%s%s 涨停%s〔↗持续〕｜动因：%s\n%s" % (icon, name, t.get("zt_count"), desc, leader)
tb = (topic_block(med, "南华生物", "000504", "3天3板", "9.99") + "\n" +
      topic_block(rob, "博通集成", "603068", "2天2板", "10.01", prefix="🔥") + "\n" +
      topic_block(chip, "华软科技", "002453", "3天3板", "9.93"))
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": tb}})
elements.append({"tag": "hr"})
# E9 资金流向
flow_fields = []
for i in range(5):
    inf = inflow[i] if i < len(inflow) else None
    out = outflow[i] if i < len(outflow) else None
    if inf:
        star = "⭐" if inf.get("is_focus") else ""
        flow_fields.append({"is_short": True, "text": {"tag": "lark_md",
            "content": red(f"{inf['name']} +{inf['flow_yi']}亿{star}")}})
    if out:
        star = "⭐" if out.get("is_focus") else ""
        flow_fields.append({"is_short": True, "text": {"tag": "lark_md",
            "content": grn(f"{out['name']} {out['flow_yi']}亿{star}")}})
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": neu("💰 板块资金流向")}})
elements.append({"tag": "div", "fields": flow_fields})
# 机构对账行
if render_inst:
    top_inst_str = " ｜ ".join(pur(f"{n}({c}) +{p:.2f}%") for n, c, p in top_inst[:3])
    inst_line = (f"🏛️ 机构今日点名·盘面：涨停 {X} 只｜拉升中 {Y} 只(+3%+)｜Top：{top_inst_str}")
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": inst_line}})
elements.append({"tag": "hr"})
# E12 情绪核心
core = (f"{neu('👑 情绪核心')}\n"
        f"{red('华瓷股份(001216) 5板 纯连板 [氧化锆/MLCC/陶瓷基板+特高压]｜加速｜市场总核心')}\n"
        f"{red('医药→南华生物 3天3板〔带领性►·突破性►·唯一性►〕')}\n"
        f"{red('机器人→博通集成 2天2板〔带领性►·突破性✗·唯一性✗〕')}\n"
        f"依据：唯一性强（华瓷5板全市场最高·纯连板无断板）｜带领性中（南华带医药涨停居首）｜突破性中（特斯拉审厂驱动·博通独苗）\n"
        f"明日看点：华瓷5→6能否打开新周期空间；南华3→4确认医药主线延续")
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": core}})
elements.append({"tag": "hr"})
# E14 AI综合研判
ai = (f"{neu('🧠 AI综合研判')}\n"
      "① 主线定性：\n"
      f"⭐确定性最强主线 {pur('医药')}：十五五规划硬政策催化，板块涨停居首，南华生物3板标杆，持续性最强\n"
      f"{blu('机器人')}：特斯拉量产审厂事件驱动，博通集成2板独苗，同方向的票刚成形待扩散\n"
      f"{yel('国产芯片')}：英伟达2027销量翻倍映射，华软科技3板，封板质量待验\n"
      "② 资金与情绪：\n"
      "资金切向通信硬件链（5G/通信技术/光通信净流入居前，先进封装/锂电池净流出），与昨日芯片/算力主线分化；情绪分歧，指数普涨但接力质量未修复\n"
      "③ 风险与纪律：\n"
      "A. 晋级率仍深破25%修复线，接力质量弱，不接高位缩量秒板\n"
      "B. 华瓷5板纯连板处5日+60%监管临界，追高性价比低\n"
      "C. 医药板块广度强但盘中炸板偏多，防分化回落\n"
      "D. 20cm可买票（宏昌/透景）波动大·紧止损")
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": ai}})

title = f"盘中猎手·增量 | {ASOF} | 医药·南华生物+9.99% | 涨停{total_zt}"
card = {
    "title": title,
    "color": "red",
    "elements": elements,
    "note": f"📡 选股宝+东财 | {ASOF}\n⚠️ 本报告基于公开信息分析，投资需谨慎",
}
json.dump(card, open("/tmp/limao_card.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("TITLE:", title)
print("ELEMENTS:", len(elements))
