# -*- coding: utf-8 -*-
import json

ROOT = "/Users/zxl/WorkBuddy/狸猫猎手"
latest = json.load(open(f"{ROOT}/cache/pulse_snapshots/latest.json", encoding="utf-8"))
fb = json.load(open(f"{ROOT}/cache/feedback_matrix.json", encoding="utf-8"))
pool_items = json.load(open(f"{ROOT}/cache/institution_pool_2026-09-17.json", encoding="utf-8"))["items"]
pool = {i["code"]: i for i in pool_items}
pool_names = {i["name"] for i in pool_items}

def inst(code, name):
    return " 🏛️" if (code in pool or name in pool_names) else ""

def red(s): return f"<text_tag color='red'>{s}</text_tag>"
def grn(s): return f"<text_tag color='green'>{s}</text_tag>"
def pur(s): return f"<text_tag color='purple'>{s}</text_tag>"
def yel(s): return f"<text_tag color='yellow'>{s}</text_tag>"
def blu(s): return f"<text_tag color='blue'>{s}</text_tag>"
def neu(s): return f"<text_tag color='neutral'>{s}</text_tag>"

# ---- 资金 Top5 交替双列 ----
inflow = latest["sector_risk"]["inflow_top"][:5]
outflow = latest["sector_risk"]["outflow_top"][:5]
def flow_field(name, yi, is_focus, kind):
    star = "⭐" if is_focus else ""
    sign = "+" if yi >= 0 else ""
    col = "red" if kind == "in" else "green"
    return {"is_short": True, "text": {"tag": "lark_md",
            "content": f"<text_tag color='{col}'>{name} {sign}{yi}亿{star}</text_tag>"}}
fields = []
for i in range(5):
    inf = inflow[i]; ouf = outflow[i]
    fields.append(flow_field(inf["name"], inf["flow_yi"], inf.get("is_focus", False), "in"))
    fields.append(flow_field(ouf["name"], ouf["flow_yi"], ouf.get("is_focus", False), "out"))

# ---- 可买 Top3 ----
broken = latest.get("broken", {})
broken = set(broken.get("names", [])) if isinstance(broken, dict) else set(broken)
buyable = [d for d in latest.get("leaders", []) if d.get("buyable") and d["name"] not in broken]
buyable.sort(key=lambda x: x["pct"], reverse=True)
top3 = buyable[:3]

# ---- 机构对账 ----
def pool_hits():
    zt, up = [], []
    for d in latest.get("leaders", []):
        if d["code"] in pool or d["name"] in pool_names:
            if d["pct"] >= 9.9 or (d.get("is_20cm") and d["pct"] >= 19.9):
                zt.append((d["name"], d["code"], d["pct"]))
            elif d["pct"] >= 3:
                up.append((d["name"], d["code"], d["pct"]))
    for e in latest.get("early", []):
        b = e.get("buy")
        if b and (b["code"] in pool or b["name"] in pool_names) and b["name"] not in {x[0] for x in zt + up}:
            up.append((b["name"], b["code"], b["pct"]))
    return zt, up
zt_hits, up_hits = pool_hits()
render_recon = (len(zt_hits) >= 1 or len(up_hits) >= 2 or any((t["code"] in pool or t["name"] in pool_names) for t in top3))
recon_line = ""
if render_recon:
    zt_s = "｜".join(f"{n}({c})+{p}%" for n, c, p in zt_hits)
    up_s = "｜".join(f"{n}({c})+{p}%" for n, c, p in up_hits[:3])
    recon_line = f"🏛️ 机构今日点名·盘面：涨停 {len(zt_hits)} 只｜拉升中 {len(up_hits)} 只(+3%+)｜Top：{zt_s+'｜' if zt_s else ''}{up_s}"

# ---- 指数 ----
idx = {x["name"]: x["pct"] for x in latest.get("index", [])}
sh = idx.get("上证指数", 0); sz = idx.get("深证成指", 0); cy = idx.get("创业板指", 0)
total_zt = latest.get("total_zt", fb.get("now_zt_count", 0))
seal_rate = latest.get("sentiment_detail", {}).get("seal_rate", 0)
broken_cnt = latest.get("broken", {}).get("count", 0) if isinstance(latest.get("broken"), dict) else 0

# ---- 题材榜（数据驱动 TOP3 + 备注）----
sd = latest.get("sector_delta", {})
def icon(name):
    if name in sd.get("new", []): return "🆕"
    if name in sd.get("surging", []): return "🔥跃升"
    return "↗"
def leader_line(t):
    ld = t.get("leader") or {}
    nm = ld.get("name")
    if not nm: return ""
    b = ld.get("boards") or ""
    sec = t.get("name", "")
    return red(f"{nm} {b} [{sec}]")
top3_topics = latest["top5"][:3]
topic_lines = []
for t in top3_topics:
    name = t["name"]; delta = t["delta"]
    zt = t["zt_count"]
    desc = (t.get("desc") or "")[:18]
    ic = icon(name)
    line = f"{ic} {red(name)} 涨停{zt}只｜动因：{desc}"
    ll = leader_line(t)
    topic_lines.append(line + ("\n" + ll if ll else ""))
# 备注行：其余活跃
notes = []
for t in latest["top5"][3:6]:
    notes.append(f"{icon(t['name'])} {t['name']}(涨停{t['zt_count']})")
topic_note = "▸ " + "｜".join(notes)

# ---- 情绪核心（数据驱动最高板）----
ladder = latest.get("ladder", [])
max_board = ladder[0]["boards"] if ladder else 0
three_board = []
for l in ladder:
    if l["boards"] == 3:
        three_board.extend(l["stocks"])
# 市场总核心：取最高板纯连板票
core_stocks = three_board if three_board else (ladder[0]["stocks"] if ladder else [])
core_names = "／".join(core_stocks[:2])
core_sector = "被动元件/国产芯片"
core = (
    f"👑 情绪核心\n"
    f"{red(f'市场总核心：{core_names} 3天3板 [{core_sector}]｜加速（全场最高纯连板）')}\n"
    f"{red('分线龙一：国产芯片→中晶科技(003026) 3天3板〔突破►·唯一✗〕')}\n"
    f"依据：唯一性中（多只3板无绝对龙头）｜带领性中（芯片3只涨停+玻璃基板3只共振）｜突破性中（突破2板压制）\n"
    f"明日看点：3板能否打开空间（昨最高6板→今3板，空间压缩一半）；闽东电力脱离跌停但智能电网换龙(积成电子)，旧核心退潮未了"
)

# ---- AI 综合研判 ----
ai = (
    f"🧠 AI综合研判\n"
    f"① 主线定性：\n{pur('⭐确定性最强主线 玻璃基板封装')}：康宁涨价15%+3只涨停，新主线强度第一\n"
    f"{blu('确定性次强 国产芯片')}：ASML EUV催化+3只涨停+中晶科技3板纯连板，退潮后最早修复梯队\n"
    f"{yel('仅低位可跟踪 医药')}：AI制药获批+医药跃升1→2，百花医药领涨+{pur('百普赛斯(301080) 20cm可买'+inst('301080','百普赛斯'))}\n"
    f"② 资金与情绪：\n硬件链(算力+31.4⭐/电子+26.3/CPO+24.5⭐/半导体+23.2⭐/华为+21.0)抱团流入 vs 旧高位多板(-22.8)/有色/黄金/小金属出货 → 资金切向AI硬件+新题材\n"
    f"③ 风险与纪律：\n"
    f"A. 隔夜退潮铁证仍存：晋级率6.7%、核按钮15只、最高板6→3断崖（feedback滞后指标）\n"
    f"B. 盘中修复拐点：涨停9→{total_zt}、闽东电力脱离跌停、新主线玻璃基板封装/液冷/新能源汽车涌现 → 冰点修复中，反弹≠反转\n"
    f"C. 西陇科学(3板)炸板掉队、高度仍压制3板，追高标易吃面，控仓试错新核心"
)

# ---- 组装 ----
asof = latest["ts"][11:16]
top_sector = top3_topics[0]["name"]
top_leader = (top3_topics[0].get("leader") or {}).get("name", "")
header_title = f"盘中猎手·增量 | {asof} | {top_sector}·{top_leader}涨停 | 涨停{total_zt}"

elements = []
elements.append({"tag": "div", "text": {"tag": "lark_md", "content":
    f"🎯 结论定调\n修复试错·新主线玻璃基板封装/液冷可轻仓跟随，退潮期反弹控仓不追高标\n隔夜退潮仍在(晋级率6.7%/核按钮15)·盘中涨停{total_zt}修复·最高板3板压制"}})
elements.append({"tag": "hr"})

def fmt_top3(d):
    tag = "20cm" if d.get("is_20cm") else d.get("sector", "")
    return pur(f"{d['name']}({d['code']}) +{d['pct']}% 现价{d['price']} [{tag}]{inst(d['code'], d['name'])}")
sig_top3 = "\n".join(fmt_top3(d) for d in top3)
dash = (
    f"📈 大盘概览\n"
    f"上证 {sh:+.2f}% ｜ 深证 {sz:+.2f}% ｜ 创业板 {cy:+.2f}%\n"
    f"涨停 {total_zt}｜封板率 {seal_rate:.0f}%｜炸板 {broken_cnt}\n"
    f"📉 情绪演变：首卡基准 → {fb['sentiment_score']}/10·{fb['sentiment_label']}｜隔夜退潮·盘中修复（涨停9→{total_zt}·闽东电力脱跌停·新主线涌现）\n"
    f"📡 信号摘要\n🆕新{len(sd.get('new',[]))} 🔥跃升{len(sd.get('surging',[]))} ↗持续{len(sd.get('ongoing',[]))}\n{sig_top3}"
)
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": dash}})
elements.append({"tag": "hr"})

yester = (
    f"📊 昨日涨停反馈（{fb['prev_date']} 涨停{fb['prev_zt_count']}）\n"
    f"晋级 {fb['promote_rate']}%｜连板晋级 {fb['lb_promote_rate']}%｜昨涨停今均 {fb['avg_pct']}%｜核按钮 {fb['nuke_count']}｜最高板 {fb['prev_max_boards']}→{fb['now_max_boards']}｜情绪 {fb['sentiment_score']}/10·{fb['sentiment_label']}"
)
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": yester}})
elements.append({"tag": "hr"})

topics = "🔥 突发题材榜（TOP3）\n" + "\n".join(topic_lines) + "\n" + topic_note
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": topics}})
elements.append({"tag": "hr"})

elements.append({"tag": "div", "text": {"tag": "lark_md", "content": neu("💰 板块资金流向")}})
elements.append({"tag": "div", "fields": fields})
if recon_line:
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": recon_line}})
elements.append({"tag": "hr"})

elements.append({"tag": "div", "text": {"tag": "lark_md", "content": core}})
elements.append({"tag": "hr"})

elements.append({"tag": "div", "text": {"tag": "lark_md", "content": ai}})

# ⛔ 2026-10-14 修复：脚本不得自拼 note 元素（{"tag":"note","content":...} 非法·缺 elements 数组，
#    飞书会整卡降级/渲染异常）→ 一律输出简版结构，note 交给 push_card.normalize() 生成合法 note。
card = {
    "title": header_title,
    "color": "red",
    "elements": elements,
    "note": f"📡 选股宝+东财+猎手 | {asof} | AI盘中诊断\n⚠️ 本报告基于公开信息分析，投资需谨慎",
}
with open("/tmp/limao_card.json", "w", encoding="utf-8") as f:
    json.dump(card, f, ensure_ascii=False, indent=2)
print("OK elements=", len(elements))
print("header:", header_title)
print("top3:", [(d['name'], d['pct']) for d in top3])
print("topics:", [t['name'] for t in top3_topics])
print("recon:", recon_line)
print("core:", core_names)
