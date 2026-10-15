# -*- coding: utf-8 -*-
"""盘中猎手 v4（2026-10-14 模块精简）预览卡构造器 —— 读真实 cache，不重算网络数据。"""
import json, os

ROOT = "/Users/zxl/WorkBuddy/狸猫猎手"
l = json.load(open(f"{ROOT}/cache/pulse_snapshots/latest.json", encoding="utf-8"))
fb = json.load(open(f"{ROOT}/cache/feedback_matrix.json", encoding="utf-8"))
try:
    pool_items = json.load(open(f"{ROOT}/cache/institution_pool_2026-10-14.json", encoding="utf-8"))["items"]
except Exception:
    pool_items = []
pool_codes = {i["code"] for i in pool_items}
pool_names = {i["name"] for i in pool_items}

def inst(code, name):
    return " <text_tag color='violet'>🏛️</text_tag>" if (code in pool_codes or name in pool_names) else ""

def red(s):   return f"<text_tag color='red'>{s}</text_tag>"
def grn(s):   return f"<text_tag color='green'>{s}</text_tag>"
def pur(s):   return f"<text_tag color='purple'>{s}</text_tag>"
def blu(s):   return f"<text_tag color='blue'>{s}</text_tag>"
def yel(s):   return f"<text_tag color='yellow'>{s}</text_tag>"
def neu(s):   return f"<text_tag color='neutral'>{s}</text_tag>"
def vio(s):   return f"<text_tag color='violet'>{s}</text_tag>"
def pct_tag(p):
    return red(f"+{p:.2f}%") if p > 0 else grn(f"{p:.2f}%")

asof = l["ts"][11:16]
total_zt = l.get("total_zt", 0)
idx = {x["name"]: x["pct"] for x in (l.get("index") or [])}
seal_rate = (l.get("sentiment_detail") or {}).get("seal_rate", 0)
broken_cnt = (l.get("broken") or {}).get("count", 0)
sd = l.get("sector_delta") or {}
top5 = l.get("top5") or []
leaders = l.get("leaders") or []

# ---- 可买 Top3 ----
broken_names = set((l.get("broken") or {}).get("names") or [])
buyable = [d for d in leaders if d.get("buyable") and d["name"] not in broken_names]
buyable.sort(key=lambda x: x["pct"], reverse=True)
top3 = buyable[:3]

# ---- 资金双列 ----
sr = l["sector_risk"]
inflow = sr["inflow_top"][:5]
outflow = sr["outflow_top"][:5]
def flow_field(name, yi, focus, kind):
    star = "⭐" if focus else ""
    sign = "+" if yi >= 0 else ""
    col = "red" if kind == "in" else "green"
    return {"is_short": True, "text": {"tag": "lark_md",
            "content": f"<text_tag color='{col}'>{name} {sign}{yi}亿{star}</text_tag>"}}
fields = []
for i in range(5):
    fields.append(flow_field(inflow[i]["name"], inflow[i]["flow_yi"], inflow[i].get("is_focus"), "in"))
    fields.append(flow_field(outflow[i]["name"], outflow[i]["flow_yi"], outflow[i].get("is_focus"), "out"))

# ---- 市场总核心（全场最高纯连板）----
ladder = l.get("ladder") or []
max_board = ladder[0]["boards"] if ladder else 0
core_name, core_code, core_boards, core_theme = "—", "", max_board, ""
for t in top5:
    ld = t.get("leader") or {}
    if ld.get("boards") and str(ld["boards"]).startswith(f"{max_board}天{max_board}板"):
        core_name = ld.get("name"); core_code = (ld.get("code") or "").split(".")[0]
        core_boards = ld.get("boards"); core_theme = t.get("name", "")
        break
if core_name == "—" and top5:
    ld = top5[0].get("leader") or {}
    core_name = ld.get("name", "—"); core_code = (ld.get("code") or "").split(".")[0]
    core_boards = ld.get("boards", ""); core_theme = top5[0].get("name", "")
lifecycle = "加速" if max_board >= 2 else "诞生"

# ---- 题材块（TOP3）----
def icon(name):
    if name in sd.get("new", []): return "🆕"
    if name in sd.get("surging", []): return "🔥跃升"
    return "↗"
tags = ["〔带领性►·突破性►·唯一性✗〕", "〔带领性✗·突破性►·唯一性►〕", "〔带领性►·突破性►·唯一性✗〕"]
topic_lines = []
for i, t in enumerate(top5[:3]):
    nm = t["name"]; zt = t["zt_count"]; ld = t.get("leader") or {}
    desc = (t.get("desc") or "")[:22]
    topic_lines.append(f"{icon(nm)} {red(nm)} {red(f'涨停{zt}')} 〔↗持续〕｜动因：{desc}")
    if ld.get("name"):
        code = (ld.get("code") or "").split(".")[0]
        topic_lines.append(red(f"　龙一 {ld['name']}({code}) {ld.get('boards','')} +{ld.get('pct',0):.2f}%") + " " + red(tags[i]))
    fol = (t.get("followers") or [])[:2]
    if fol:
        seg = "｜".join(f"{f['name']} {pct_tag(f['pct'])}" for f in fol)
        topic_lines.append(f"　跟风 {seg}")
    if i == 0:
        topic_lines.append("　💡 诊断：涨停9只居首，字节短剧下载破亿是真动因；但指数走弱、20cm 跟风有炸板风险")
        topic_lines.append(grn("　⚠️ 风险：题材已发酵至第 4 天，追高标易吃面；20cm 换手 38.9% 过热"))
        topic_lines.append(red("　🎯 操作：只低吸不追高；") + grn("止损：破当日均价或龙头开板即走"))

merged = (
    red(f"👑 市场总核心：{core_name}({core_code}) {core_boards} [{core_theme}]｜生命周期：{lifecycle}") + "\n"
    + "\n".join(topic_lines) + "\n"
    + "📌 依据：" + yel("唯一性弱") + "（4板三只并列·最高板压4板）｜"
    + blu("带领性中") + "（短剧9只、PCB5只涨停）｜" + blu("突破性中") + "（最高板3→4）" + "\n"
    + "📌 明日看点：4板能否打开空间决定情绪能否修复；短剧与PCB谁接力成新主线"
)

# ---- 机构对账 ----
hit_zt, hit_up = [], []
for d in leaders:
    if d["code"] in pool_codes or d["name"] in pool_names:
        (hit_zt if d["pct"] >= 9.9 else hit_up).append((d["name"], d["code"], d["pct"]))
recon = ""
if hit_zt or len(hit_up) >= 2:
    seg = "｜".join(f"{n}({c}) {pct_tag(p)}" for n, c, p in (hit_zt + hit_up)[:3])
    recon = vio(f"🏛️ 机构今日点名·盘面：涨停 {len(hit_zt)} 只｜拉升中 {len(hit_up)} 只(+3%+)｜Top：{seg}")

# ---- 组装 ----
sig_lines = [
    neu("📡 信号摘要"),
    f"📉 情绪演变：首卡基准 → {red(str(fb['sentiment_score']))}/10·{red(fb['sentiment_label'])}｜{grn('转差')}（涨停{total_zt}·封板率{seal_rate:.0f}%）",
    f"🆕新{len(sd.get('new',[]))} 🔥跃升{len(sd.get('surging',[]))} ↗持续{len(sd.get('ongoing',[]))}",
]
sig_lines += [pur(f"{d['name']}({d['code']}) +{d['pct']:.2f}% 现价{d['price']} [{d.get('sector','')}]") for d in top3]

verdict = ("🎯 结论定调\n"
           "退潮未见底·不追高标，题材只做低吸快进快出\n"
           + red(f"情绪 {fb['sentiment_score']}/10·{fb['sentiment_label']}")
           + f"｜核按钮 {fb['nuke_count']} 只｜晋级率 {fb['promote_rate']}%")

elements = []
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": verdict}})
elements.append({"tag": "hr"})
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(sig_lines)}})
elements.append({"tag": "hr"})
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": neu("🔥 题材与情绪核心") + "\n" + merged}})
elements.append({"tag": "hr"})
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": neu("💰 板块资金流向")}})
elements.append({"tag": "div", "fields": fields})
if recon:
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": recon}})
elements.append({"tag": "hr"})
elements.append({"tag": "div", "text": {"tag": "lark_md", "content":
    neu("🧠 AI综合研判") + "\n"
    + "① 主线定性：\n"
    + pur("⭐确定性最强主线 短剧/互动影游") + "：涨停9只+字节短剧下载破亿，强度第一，但已到第4天\n"
    + blu("确定性次强 PCB板") + "：三季报预增+英伟达HC树脂催化，5只涨停，产业逻辑更硬\n"
    + yel("仅低位可跟踪 大消费") + "：财政政策扩内需，3只涨停但无强龙头\n"
    + "② 资金与情绪：\n"
    + "资金切向传媒娱乐（PCB+21.6/AIGC+14.1/文娱+13.7） vs 硬件出货（CPO-42.2/算力-34.3/锂电-35.1）→ 高低切换\n"
    + "③ 风险与纪律：\n"
    + "A. 指数全线走弱（创业板 -1.88%），退潮期反弹≠反转\n"
    + "B. " + red(f"核按钮 {fb['nuke_count']} 只、炸板 {broken_cnt} 只") + f"，情绪分仅 {fb['sentiment_score']}，接力质量差\n"
    + "C. 20cm 跟风换手 38.9% 过热，炸板即走不接飞刀"
}})

# ⛔ 2026-10-14 修复：脚本不得自拼 note 元素（{"tag":"note","content":...} 非法·缺 elements 数组，
#    飞书会整卡降级/渲染异常）→ 一律输出简版结构，note 交给 push_card.normalize() 生成合法 note。
header_title = f"盘中猎手·增量 | {asof} | {top5[0]['name']}·{(top5[0].get('leader') or {}).get('name','')}涨停 | 涨停{total_zt}"
card = {
    "title": header_title,
    "color": "red",
    "elements": elements,
    "note": f"📡 选股宝+东财+猎手 | {asof} | AI盘中诊断\n⚠️ 本报告基于公开信息分析，投资需谨慎",
}
out = "/tmp/limao_hunter_v4.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(card, f, ensure_ascii=False, indent=2)
print("OK elements =", len(elements))
print("header:", header_title)
print("out:", out)
print("=" * 70)
for e in elements:
    if e.get("tag") == "div" and e.get("text"):
        print(e["text"]["content"])
    elif e.get("tag") == "div" and e.get("fields"):
        print("  ".join(f["text"]["content"] for f in e["fields"][:4]))
    elif e.get("tag") == "hr":
        print("---")
print("(note)", card["note"])
