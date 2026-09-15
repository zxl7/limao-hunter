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

# 1 结论定调 (首屏)
elements.append(div(
    f"<text_tag color='{R}'>🎯 结论</text_tag>\n"
    "观望·控仓等分歧转一致（资源线逆势承接）\n"
    "情绪4.1/分歧·晋级率18.3%·涨停42(昨93)"
))
elements.append(hr())

# 2 大盘概览
elements.append(title("📈 大盘概览"))
elements.append(div(
    "上证 +0.15% ｜ 深证 -0.39% ｜ 创业板 -1.07%\n"
    "涨停 42｜破板率 19.2%｜涨跌 3101:2002"
))
elements.append(hr())

# 3 情绪演变
elements.append(div(
    "📉 情绪演变：偏强 → <text_tag color='green'>4.1/分歧</text_tag>｜转差"
    "（涨停42 vs 昨93·环比-55%；晋级率18.3%）"
))
elements.append(hr())

# 4 突发题材榜
topic = (
    f"📌 <text_tag color='{R}'>大农业 涨停8 〔↗持续〕</text_tag> ｜ 动因：粮食安全+农业政策预期\n"
    "亚盛集团 9天5板 <text_tag color='red'>+10.0%</text_tag>\n"
    "跟：中粮科技+10.0%·泸天化+10.0%·敦煌种业+9.97%\n\n"
    f"📌 <text_tag color='{R}'>大消费 涨停6 〔↗持续〕</text_tag> ｜ 动因：消费扩容政策+内需\n"
    "海欣食品 3天3板 <text_tag color='red'>+10.05%</text_tag>\n"
    "跟：桂林旅游+10.03%·三元生物+11.64%\n\n"
    f"📌 <text_tag color='{R}'>机器人 涨停3 〔↗持续〕</text_tag> ｜ 动因：人形机器人产业催化\n"
    "爱仕达 4天4板 <text_tag color='red'>+9.99%</text_tag>\n"
    "跟：百合花+9.99%·科森科技+9.99%\n\n"
    f"🆕 <text_tag color='{R}'>有色金属 涨停1 〔新〕</text_tag> ｜ 动因：铜价走强+资源避险\n"
    "北方铜业 <text_tag color='red'>+9.98%</text_tag>\n"
    "跟：电工合金+11.92%·江西铜业+5.93%"
)
elements.append(title("🔥 突发题材榜"))
elements.append(div(topic))
elements.append(hr())

# 5 核心标的 (整行红·一行一只·不评可买)
core = "\n".join([
    f"<text_tag color='{R}'>亚盛集团 +10.0% [大农业·9天5板]</text_tag>",
    f"<text_tag color='{R}'>海欣食品 +10.05% [大消费·3天3板]</text_tag>",
    f"<text_tag color='{R}'>爱仕达 +9.99% [机器人·4天4板]</text_tag>",
    f"<text_tag color='{R}'>北方铜业 +9.98% [有色金属·新]</text_tag>",
    f"<text_tag color='{R}'>广哈通信 +12.69% [6G·20cm]</text_tag>",
    f"<text_tag color='{R}'>龙版传媒 +9.99% [短剧·16天8板]</text_tag>",
])
elements.append(title("📡 核心标的"))
elements.append(div(core))
elements.append(hr())

# 6 昨日涨停反馈
fb = ("📊 昨日涨停反馈（09-07 涨停93）晋级 <text_tag color='green'>18.3%</text_tag>"
      "｜连板晋级 53.8%｜昨涨停今均 2.59%｜高开率 68.5%｜核按钮 3"
      "｜最高板 6→7｜情绪 4.1/10·<text_tag color='green'>分歧</text_tag>")
elements.append(div(fb))
elements.append(hr())

# 7 板块资金流向 (fields 交替双列)
elements.append(title("💰 板块资金流向"))
inflow = ["央国企改革 +31.0亿", "有色金属 +27.9亿", "工业金属 +27.8亿", "黄金概念 +25.8亿", "稀缺资源 +25.3亿"]
outflow = ["新能源车 -52.1亿", "华为概念 -46.0亿", "电子 -45.7亿", "液冷服务器 -38.8亿⭐", "特斯拉概念 -35.6亿"]
fields = []
for i, o in zip(inflow, outflow):
    fields.append(fld(f"<text_tag color='{R}'>{i}</text_tag>"))
    fields.append(fld(f"<text_tag color='{G}'>{o}</text_tag>"))
elements.append({"tag": "div", "fields": fields})

card = {
    "title": "盘中快照-1000·增量 10:02 | 大农业·涨停8",
    "color": "red",
    "elements": elements,
    "note": "📡 选股宝+东财 | 10:02\n⚠️ 本报告基于公开信息分析，投资需谨慎",
}

with open("/tmp/limao_card.json", "w", encoding="utf-8") as f:
    json.dump(card, f, ensure_ascii=False, indent=2)
print("OK written, elements:", len(elements))
