# -*- coding: utf-8 -*-
import json

R = "red"; G = "green"; N = "neutral"; P = "purple"

def div(content):
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}

def hr():
    return {"tag": "hr"}

def title(label):
    return div(f"<text_tag color='{N}'>{label}</text_tag>")

def fld(content):
    return {"is_short": True, "text": {"tag": "lark_md", "content": content}}

elements = []

# 1 结论定调 (首屏·≤2行·换角度:结构切换视角)
elements.append(div(
    f"<text_tag color='{R}'>🎯 结论</text_tag>\n"
    "修复延续·医药+外贸双线领涨，芯片资金接力回流（半导体+75.7亿）\n"
    "情绪5.5/修复·核按钮1只(冰点转暖)·但晋级率19.1%<25%·最高板5→4 接力质量待验"
))
elements.append(hr())

# 2 大盘概览 (指数一行并排·⛔禁写深成)
elements.append(title("📈 大盘概览"))
elements.append(div(
    "上证 +0.79% ｜ 深证 +0.71% ｜ 创业板 +0.89%\n"
    "涨停 46｜封板率 74.2%｜炸板率 29.5%"
))
elements.append(hr())

# 3 情绪演变 (本卡 vs 上卡·语义色板)
elements.append(div(
    "📉 情绪演变：上卡(09:41 猎手)偏强 → 本卡 <text_tag color='green'>5.5/10·修复</text_tag>｜转差\n"
    "（核按钮1只较昨16只冰点转暖·但晋级率19.1%<25%·最高板5→4）"
))
elements.append(hr())

# 4 突发题材榜 (delta分级·new/surging全展开·ongoing压缩·⛔禁6位代码)
topic = (
    f"🆕 <text_tag color='{R}'>液冷服务器 涨停1 〔新〕</text_tag> ｜ 动因：祥鑫收购酷尔芯切入液冷赛道\n"
    "祥鑫科技 <text_tag color='red'>+10.01%</text_tag>\n\n"
    f"🔥 <text_tag color='{R}'>外贸受益概念 涨停4 〔↗持续〕</text_tag> ｜ 动因：外贸受益概念发酵\n"
    "华纺股份 2天2板 <text_tag color='red'>+9.94%</text_tag>\n\n"
    f"📌 <text_tag color='{R}'>医药 涨停5 〔↗持续〕</text_tag> ｜ 动因：十五五医药规划+创新药\n"
    "南华生物 2天2板 <text_tag color='red'>+10.0%</text_tag>\n\n"
    f"📌 <text_tag color='{R}'>机器人 涨停4 〔↗持续〕</text_tag>｜国产芯片 涨停4 〔↗持续〕｜PCB板 涨停3 〔↗持续〕"
)
elements.append(title("🔥 突发题材榜"))
elements.append(div(topic))
elements.append(hr())

# 5 核心标的 (整行红·一行一只·不评可买·⛔禁6位代码)
core = "\n".join([
    f"<text_tag color='{R}'>南华生物 +10.0% [医药·2天2板]</text_tag>",
    f"<text_tag color='{R}'>华纺股份 +9.94% [外贸·2天2板]</text_tag>",
    f"<text_tag color='{R}'>三羊马 +10.01% [机器人]</text_tag>",
    f"<text_tag color='{R}'>肯特催化 +9.99% [国产芯片]</text_tag>",
    f"<text_tag color='{R}'>大亚圣象 +10.06% [PCB]</text_tag>",
    f"<text_tag color='{R}'>锡华科技 +9.98% [风电·4天4板·最高板]</text_tag>",
    f"<text_tag color='{R}'>祥鑫科技 +10.01% [液冷]</text_tag>",
    f"<text_tag color='{R}'>经纬股份 +20.0% [20cm·2天2板]</text_tag>",
    f"<text_tag color='{R}'>内蒙新华 +10.04% [3天3板]</text_tag>",
])
elements.append(title("📡 核心标的"))
elements.append(div(core))
elements.append(hr())

# 6 昨日涨停反馈 (全卡唯一数据源·增量基线)
fb = ("📊 昨日涨停反馈（09-17 涨停47）晋级 <text_tag color='green'>19.1%</text_tag>"
      "｜连板晋级 33.3%｜昨涨停今均 +2.85%｜高开率 72.3%｜核按钮 1"
      "｜最高板 <text_tag color='green'>5→4</text_tag>｜情绪 5.5/10·修复")
elements.append(div(fb))
elements.append(hr())

# 7 板块资金流向 (fields 交替双列·Top5全量·⭐=focus)
elements.append(title("💰 板块资金流向"))
inflow = ["半导体 +75.7亿⭐", "国产芯片 +74.3亿⭐", "半导体概念 +55.3亿⭐", "专精特新 +49.1亿", "物联网 +43.0亿"]
outflow = ["PCB -77.3亿⭐", "元件 -39.9亿", "印制电路板 -39.2亿⭐", "新材料 -31.8亿", "周期股 -31.1亿"]
fields = []
for i, o in zip(inflow, outflow):
    fields.append(fld(f"<text_tag color='{R}'>{i}</text_tag>"))
    fields.append(fld(f"<text_tag color='{G}'>{o}</text_tag>"))
elements.append({"tag": "div", "fields": fields})

card = {
    "title": "盘中快照-1000·增量 10:11 | 医药·涨停5",
    "color": "red",
    "elements": elements,
    "note": "📡 选股宝+东财 | 10:11\n⚠️ 本报告基于公开信息分析，投资需谨慎",
}

with open("/tmp/limao_card.json", "w", encoding="utf-8") as f:
    json.dump(card, f, ensure_ascii=False, indent=2)
print("OK written, elements:", len(elements))
