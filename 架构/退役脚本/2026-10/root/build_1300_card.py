# -*- coding: utf-8 -*-
import json

T = '盘中猎手·增量 | 13:00 | 影视·德才股份+10.0% | 涨停34'

elements = []

# 1) 结论定调
elements.append({
    "tag": "div",
    "text": {
        "tag": "lark_md",
        "content": (
            "🎯 结论定调\n"
            "观望偏防守·退潮确认（晋级率24.6%破25%·核按钮9·涨停较昨69腰斩至34）\n"
            "仅影视低位前排+电网/医药低位可轻仓跟踪·禁追10板新华传媒与中位连板\n"
            "⚠️ 修复门槛：晋级率破25%+核按钮<5+涨停回补至50+（当前均未达）"
        )
    }
})

# 2) hr
elements.append({"tag": "hr"})

# 3) 大盘概览 + 情绪演变 + 信号摘要
elements.append({
    "tag": "div",
    "text": {
        "tag": "lark_md",
        "content": (
            "📈 大盘概览\n"
            "上证 <text_tag color='green'>-0.17%</text_tag> ｜ 深证 <text_tag color='green'>-1.10%</text_tag> ｜ 创业板 <text_tag color='green'>-1.28%</text_tag>\n"
            "涨停 <text_tag color='red'>34</text_tag>｜封板率 55.7%｜炸板 <text_tag color='green'>27</text_tag>｜跌停 <text_tag color='green'>9</text_tag>\n"
            "📉 情绪演变：分歧 → <text_tag color='green'>1.3/10·退潮</text_tag>｜转差（涨停较昨69缩量·晋级率破25%·核按钮升）\n"
            "📡 信号摘要：🆕新0 🔥跃升0 ↗持续9\n"
            "<text_tag color='purple'>莱美药业(300006) +19.2% 现价6.52 [医药]</text_tag>\n"
            "<text_tag color='purple'>首航新能(301658) +15.0% 现价25.99 [光伏]</text_tag>\n"
            "<text_tag color='purple'>威力传动(300904) +13.79% 现价54.39 [风电]</text_tag>"
        )
    }
})

elements.append({"tag": "hr"})

# 4) 昨日涨停反馈（全卡唯一数据源）
elements.append({
    "tag": "div",
    "text": {
        "tag": "lark_md",
        "content": (
            "📊 昨日涨停反馈（10-09 涨停69）\n"
            "晋级 <text_tag color='green'>24.6%</text_tag>｜连板晋级 25.0%｜昨涨停今均 +2.39%｜核按钮 <text_tag color='green'>9</text_tag>｜最高板 9→<text_tag color='red'>10</text_tag>｜情绪 <text_tag color='green'>1.3/10·退潮</text_tag>"
        )
    }
})

elements.append({"tag": "hr"})

# 5) 突发题材榜（全 ongoing·压缩）
elements.append({
    "tag": "div",
    "text": {
        "tag": "lark_md",
        "content": (
            "🔥 突发题材榜（全 ongoing·无新爆发）\n"
            "↗ 影视 持续中 | 涨停8只 | <text_tag color='red'>龙一 德才股份(605287) 4天3板 🏛️</text_tag> ｜动因：AI院线电影定档10·23\n"
            "↗ 大消费 持续中 | 涨停4只 | <text_tag color='red'>龙一 九阳股份(002242) 6板</text_tag> ｜动因：财政扩内需\n"
            "↗ 智能电网 持续中 | 涨停3只 | <text_tag color='red'>龙一 金冠电气(688517) +20%</text_tag> ｜动因：十五五5万亿电网\n"
            "其余 传媒/医药/风电/大农业/网络安全/锂电池 均 ongoing·无新爆发"
        )
    }
})

elements.append({"tag": "hr"})

# 6) 板块资金流向（诚实缺口）
elements.append({
    "tag": "div",
    "text": {
        "tag": "lark_md",
        "content": (
            "💰 板块资金流向\n"
            "资金流向数据暂缺（东财接口侧未恢复·不臆造）"
        )
    }
})

# 7) 机构点名对账（步骤4.56）
elements.append({
    "tag": "div",
    "text": {
        "tag": "lark_md",
        "content": (
            "🏛️ 机构今日点名·盘面：涨停 2 只｜Top：博纳影业(001330)+10.04%·果麦文化(301052)+20.01%（均封板）"
        )
    }
})

elements.append({"tag": "hr"})

# 8) 情绪核心
elements.append({
    "tag": "div",
    "text": {
        "tag": "lark_md",
        "content": (
            "👑 情绪核心\n"
            "<text_tag color='red'>市场总核心：新华传媒(600825) 10板 [资产重组]｜加速（10连板封板·退潮期10板高位·将死风险）</text_tag>\n"
            "分线龙一：\n"
            "<text_tag color='red'>影视→德才股份(605287) 4天3板〔带领性►/突破性►/唯一性✗〕</text_tag>\n"
            "<text_tag color='red'>大消费→九阳股份(002242) 6板〔带领性►/突破性►/唯一性✗〕</text_tag>\n"
            "依据：唯一性 中（新华传媒独一份10板空间）｜带领性 强（影视8家/大消费4家）｜突破性 强（10板破近期高度）\n"
            "明日看点：新华传媒10板后分歧考验；影视线看德才股份能否转纯连板"
        )
    }
})

elements.append({"tag": "hr"})

# 9) AI综合研判
elements.append({
    "tag": "div",
    "text": {
        "tag": "lark_md",
        "content": (
            "🧠 AI综合研判\n"
            "① 主线定性：\n"
            "⭐确定性最强主线 影视（AI院线电影定档硬催化·退潮期唯一有持续性的新题材）\n"
            "TOP2 大消费（财政扩内需·但三柏硕炸崩示弱）\n"
            "TOP3 智能电网/医药（十五五5万亿电网政策 / 创新药授权·低位可跟踪）\n"
            "② 资金与情绪：\n"
            "资金流向数据暂缺（接口未恢复）；情绪分歧→退潮，涨停较昨腰斩、核按钮回升、晋级率破25%，亏钱效应扩大\n"
            "③ 风险与纪律：\n"
            "A. 退潮确认：晋级率破25%绿警·核按钮升至高位·涨停较昨腰斩，接力质量未修复（详见昨日反馈）\n"
            "B. 空间龙将死风险：新华传媒10板处历史高位，一旦开板即情绪见顶，跟风票不接\n"
            "C. 大消费内部分化：三柏硕/国芳集团核按钮杀跌，高位消费派发，仅低位首板/2板可跟踪\n"
            "D. 纪律：禁追10板空间龙与中位连板；仅影视低位前排+电网/医药低位轻仓跟踪·控仓"
        )
    }
})

elements.append({"tag": "hr"})

# 底部 notes
elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": "📡 选股宝+东财 | 13:00 | AI盘中诊断"}]})
elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": "⚠️ 本报告基于公开信息分析，投资需谨慎"}]})

card = {
    "msg_type": "interactive",
    "card": {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": T}
        },
        "elements": elements
    }
}

with open("/tmp/limao_card.json", "w", encoding="utf-8") as f:
    json.dump(card, f, ensure_ascii=False, indent=2)
print("written /tmp/limao_card.json")
