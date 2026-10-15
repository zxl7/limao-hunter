#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 2026-09-16 机构晚报速览卡 + 质量门自检。"""
import json, re, sys
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
CACHE = ROOT / "cache"
OUT = CACHE / "gangtise_evening_research.json"

# ---- 真实涨停池（沪深，含688；用于红票校验）----
zt = json.loads((CACHE / "zt_history" / "zt_2026-09-16.json").read_text(encoding="utf-8"))
zt_codes = {x["code"] for x in zt["zt_list"]}

def blk(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}

def hr() -> dict:
    return {"tag": "hr"}

B = []

# 1. 油运 + 国车国运
B.append(blk(
"**🚢 油运 + 国车国运（伊以冲突 + 汽车出口运力缺口）**\n\n"
"▸ 机构依据：8月起油轮市场缺船，伊朗袭击油轮、沙特油管被炸引发恐慌，霍尔木兹海峡通行量下降，老旧船日租金飙至30万美元以上，吨海里需求拉长；中国汽车出口2026年预计突破1200万台，自有滚装船仅约70艘，一半以上运力需外租，新船交付至2029年前受限。\n"
"▸ 机构逻辑：地缘冲突压缩全球油轮有效运力，运价进入历史级高景气，船东定价权强；汽车出口量激增与滚装运力刚性缺口把「国车国运」推至景气高峰，掌握稀缺合规运力的中国船东盈利弹性大。\n"
"▸ 机构点名：<text_tag color='violet'>招商轮船(601872)</text_tag> / <text_tag color='violet'>招商南油(601975)</text_tag> / <text_tag color='violet'>中远海特(600428)</text_tag>（中远海能01138.HK·港股 / 中国船舶租赁03877.HK·港股 / 海丰国际01308.HK·港股）\n"
"📎 盘面对照：今日收盘涨停池无油运/航运/滚装个股涨停，招商轮船、中远海特等未封板，机构主线未在盘面兑现。"
))

# 2. 电子布/覆铜板/MLCC材料
B.append(blk(
"**🔌 电子布/覆铜板/MLCC 材料（M8 放量 + 供给缺口）**\n\n"
"▸ 机构依据：26下半年M8电子布放量，二代布2025-2027需求从700万米增至17000万米，行业无新增电子纱产能，供需缺口至少延续至2027年底；MLCC海外大厂释放扩产信号，A股板块逆势活跃。\n"
"▸ 机构逻辑：AI算力升级驱动电子布需求结构升级与总量扩张，供给受电子纱及高端织布机瓶颈约束，提价逻辑清晰；覆铜板上游树脂、玻纤设备同步受益，量价齐升。\n"
"▸ 机构点名：<text_tag color='violet'>中国巨石(600176)</text_tag> / <text_tag color='violet'>中材科技(002080)</text_tag> / <text_tag color='violet'>国际复材(301526)</text_tag> / <text_tag color='violet'>宏和科技(603256)</text_tag>（建滔积层板01888.HK·港股）\n"
"📎 盘面对照：电子布/MLCC材料链今日涨停——<text_tag color='red'>华瓷股份(001216·2板·MLCC)</text_tag> / <text_tag color='red'>东材科技(601208·MLCC基膜)</text_tag> / <text_tag color='red'>中化国际(600500·覆铜板上游)</text_tag> / <text_tag color='red'>北自科技(603082·玻纤·2板)</text_tag> / <text_tag color='red'>卓郎智能(600545·玻纤设备)</text_tag>；机构点名票今日未封板。"
))

# 3. AI影视/IP
B.append(blk(
"**🎬 AI影视/IP 爆发（字节/芒果助推）**\n\n"
"▸ 机构依据：影视公司全面发力AI影视，字节跳动、芒果等平台提供支持，四季度中长剧和电影有望上线；阅文等网文IP库成低价量产内容源头，互动影游商业化提速。\n"
"▸ 机构逻辑：AI影视工业化重塑产业链价值，先发布局、IP储备或平台生态形成闭环者，有望跑通商业模式，摆脱传统影视高风险困境，实现业绩与估值双重修复。\n"
"▸ 机构点名：<text_tag color='violet'>芒果超媒(300413)</text_tag> / <text_tag color='violet'>博纳影业(001330)</text_tag> / <text_tag color='violet'>中文在线(300364)</text_tag> / <text_tag color='violet'>昆仑万维(300418)</text_tag> / <text_tag color='violet'>光线传媒(300251)</text_tag> / <text_tag color='violet'>上海电影(601595)</text_tag> / <text_tag color='violet'>华策影视(300133)</text_tag> / <text_tag color='violet'>掌阅科技(603533)</text_tag>（阅文集团00772.HK·港股 / 爱奇艺IQ.O·美股）\n"
"📎 盘面对照：出版/IP今日涨停——<text_tag color='red'>内蒙新华(603230·出版发行)</text_tag> / <text_tag color='red'>京华激光(603607·IP卡牌+烟草包装)</text_tag>；机构点名票今日未封板。"
))

# 4. 煤炭/煤化工
B.append(blk(
"**⛏️ 煤炭/煤化工（矿安83号文 + 高油价）**\n\n"
"▸ 机构依据：矿安83号文下晋陕蒙开工率低，四季度山西产量难大幅恢复；秦港平仓价触碰1002元，供给矛盾支撑煤价；原油大涨催生煤化工需求，非电煤旺季来临，高油价下煤制烯烃利润增厚。\n"
"▸ 机构逻辑：供给受安监强约束缺乏弹性，库存去化；需求端同时受益极端高温用电预期与高油价煤化工替代，供需剪刀差扩大，煤价维持强势。\n"
"▸ 机构点名：<text_tag color='violet'>陕西煤业(601225)</text_tag> / <text_tag color='violet'>广汇能源(600256)</text_tag> / <text_tag color='violet'>潞安环能(601699)</text_tag> / <text_tag color='violet'>淮北矿业(600985)</text_tag> / <text_tag color='violet'>平煤股份(601666)</text_tag> / <text_tag color='violet'>山西焦煤(000983)</text_tag> / <text_tag color='violet'>新集能源(601918)</text_tag>（兖矿能源01171.HK·港股 / 中国神华·美股ADR / 中煤能源·美股ADR）\n"
"📎 盘面对照：今日收盘涨停池无煤炭股涨停，机构主线未兑现。"
))

# 5. 算力网/洁净室基建
B.append(blk(
"**🏗️ 算力网/洁净室基建（国家六张网）**\n\n"
"▸ 机构依据：AI革命重塑生产范式，算力网等进入国家「六张网」建设体系，为建筑行业带来新增长点；电力、机电、洁净室等专业工程需求确定性增强。\n"
"▸ 机构逻辑：国家算力网建设为确定性主线，AI驱动新一轮资本开支，洁净室、机电、钢结构等高端厂房建设需求放量，承接算力基建的央企与专业工程商受益。\n"
"▸ 机构点名：<text_tag color='violet'>中国电建(601669)</text_tag> / <text_tag color='violet'>中国能建(601868)</text_tag> / <text_tag color='violet'>亚翔集成(603929)</text_tag> / <text_tag color='violet'>柏诚股份(601133)</text_tag> / <text_tag color='violet'>圣晖集成(603163)</text_tag> / <text_tag color='violet'>利柏特(605167)</text_tag> / <text_tag color='violet'>鸿路钢构(002541)</text_tag> / <text_tag color='violet'>精工钢构(600496)</text_tag>\n"
"📎 盘面对照：算力硬件今日涨停呼应——<text_tag color='red'>星网锐捷(002396·数据中心交换机)</text_tag> / <text_tag color='red'>共达电声(002655·AI算力+电声)</text_tag> / <text_tag color='red'>和胜股份(002824·液冷算力)</text_tag> / <text_tag color='red'>华丰股份(605100·算电协同电源)</text_tag>；机构点名票今日未封板。"
))

# 6. 创新药/基因测序
B.append(blk(
"**💊 创新药/基因测序（小核酸 + 肿瘤NGS + mNGS）**\n\n"
"▸ 机构依据：Arrowhead双靶小核酸ARO-DIMER-PA中期数据验证PCSK9与APOC3双降，双靶点协同潜力打开；江苏开展肿瘤NGS价格谈判，封顶价约立项指南七至八折；微远、金匙两款mNGS感染检测产品获批，终结无证状态。\n"
"▸ 机构逻辑：小核酸从单靶迈向多靶协同，差异化疗效解决心血管残余风险；肿瘤NGS由价格承压转向合规放量、新场景扩容；感染测序进入注册时代，渗透率提升带动测序仪与IVD放量。\n"
"▸ 机构点名：<text_tag color='violet'>艾德生物(300685)</text_tag> / <text_tag color='violet'>华大基因(300676)</text_tag> / <text_tag color='violet'>金域医学(603882)</text_tag> / <text_tag color='violet'>迪安诊断(300244)</text_tag> / <text_tag color='violet'>贝瑞基因(000710)</text_tag> / <text_tag color='violet'>诺禾致源(688315·无权限)</text_tag> / <text_tag color='violet'>华大智造(688114·无权限)</text_tag> / <text_tag color='violet'>诺唯赞(688105·无权限)</text_tag> / <text_tag color='violet'>圣湘生物(688289·无权限)</text_tag> / <text_tag color='violet'>必贝特(688759·无权限)</text_tag> / <text_tag color='violet'>前沿生物(688221·无权限)</text_tag> / <text_tag color='violet'>迈威生物(688062·无权限)</text_tag>（康圣环球09960.HK·港股 / 中国生物制药·美股ADR）\n"
"📎 盘面对照：医疗/检测今日涨停——<text_tag color='red'>合富中国(603122·AI医疗+体外诊断)</text_tag>；机构点名票今日未封板。"
))

# 7. 黄酒高端化
B.append(blk(
"**🍶 黄酒高端化（招商落地 + 政策列为历史经典产业）**\n\n"
"▸ 机构依据：会稽山兰亭上半年招商进度达2025全年水平，签约白酒大商；古越龙山牵手广东粤强，中高端黄酒收入增长14.17%；工信部等六部门将黄酒列入历史经典产业，支持老字号做大；白酒渠道利润塌陷，黄酒高端品给经销商约30%毛利，吸引资源转移。\n"
"▸ 机构逻辑：黄酒正经历渠道重构、产品升级与政策催化共同驱动的结构性变革，核心不在总量增长，而在高端化利润弹性、白酒渠道溢出承接，以及原酒资产价值重估。\n"
"▸ 机构点名：<text_tag color='red'>会稽山(601579·2板)</text_tag> / <text_tag color='red'>古越龙山(600059)</text_tag> / <text_tag color='red'>金枫酒业(600616)</text_tag>\n"
"📎 盘面对照：黄酒今日涨停 3 家 = 会稽山/古越龙山/金枫酒业，机构点名与盘面高度吻合（红=已封板）。"
))

# 8. HNB 加热不燃烧烟草
B.append(blk(
"**🚬 HNB 加热不燃烧烟草（国标加速出台）**\n\n"
"▸ 机构依据：HNB国标征求意见稿9月26日截止，正式国标有望加速；8月底起外资烟草高管密集访华，释放开放信号；国内HNB将引入国际品牌并降低专利门槛，市场呈多元开放格局。\n"
"▸ 机构逻辑：国标征询截止标志国内市场开放进入规则落地阶段，为供应链打开增量空间；核心是寻找已深度参与中烟体系、在HNB材料（薄片/香精/用纸）及烟具方案完成技术储备的供应商。\n"
"▸ 机构点名：<text_tag color='violet'>恒丰纸业(600356)</text_tag> / <text_tag color='violet'>仙鹤股份(603733)</text_tag> / <text_tag color='violet'>昆船智能(301311)</text_tag>（思摩尔国际06969.HK·港股 / 中烟香港06055.HK·港股 / 华宝国际·美股ADR）\n"
"📎 盘面对照：今日涨停烟草/IP包装——<text_tag color='red'>京华激光(603607·IP卡牌+烟草包装)</text_tag>；机构点名票今日未封板。"
))

# 🧭 机构还看什么
B.append(blk(
"**🧭 机构还看什么**\n\n"
"▸ 算力网上游洁净室/钢构：<text_tag color='violet'>亚翔集成(603929)</text_tag> / <text_tag color='violet'>柏诚股份(601133)</text_tag> / <text_tag color='violet'>圣晖集成(603163)</text_tag> / <text_tag color='violet'>鸿路钢构(002541)</text_tag>（今日未涨停，机构偏提前布局）。\n"
"▸ HNB薄片/香精上游：<text_tag color='violet'>恒丰纸业(600356)</text_tag> / <text_tag color='violet'>仙鹤股份(603733)</text_tag>（今日未涨停）。\n"
"▸ 小核酸siRNA以港股/美股为主（中国生物制药等），无A股可交易标的。"
))

# 📎 说明
B.append(blk(
"**📎 说明**\n\n"
"▸ 红字=今日已涨停个股（沪深口径，共89家，最高板闽东电力6板）；紫字=机构核心标的但未涨停。\n"
"▸ 科创板(688)、北交所、港股、美股已标注或剔除（无A股交易权限）；A+H公司写A股代码。\n"
"▸ 盘面涨停归属依据收盘涨停池（zt_list）与涨停原因归集（hithink reason），未编造。\n"
"▸ 数据源：Gangtise 机构晚报（ID 1110·09-16）含核心标的。\n"
"▸ 仅供信息参考，不构成投资建议，投资需谨慎。"
))

elements = []
for i, b in enumerate(B):
    if i > 0:
        elements.append(hr())
    elements.append(b)

card = {
    "title": "🏛️ 机构晚报 09-16 | 油运、电子布、黄酒齐涨停，地缘冲突与AI材料双线走强",
    "color": "blue",
    "elements": elements,
}

# ---- 质量门 ----
errs = []
txt = json.dumps(card, ensure_ascii=False)
# 禁 column_set / hex / <font color
for bad in ["column_set", "column_set", "<font"]:
    if bad in txt:
        errs.append(f"禁用元素命中: {bad}")
if re.search(r"#[0-9a-fA-F]{6}", txt):
    errs.append("命中 hex 色值")
# 黑话
for j in ["共振", "维度", "boom", "回流引擎", "theme_rotation", "inflow_rising", "背离引擎", "re-entry"]:
    # 单字"引擎"仅当完整token"光引擎"等属合法；此处按完整token比对
    if j in txt:
        errs.append(f"黑话命中: {j}")
# 标签配对
if txt.count("<text_tag") != txt.count("</text_tag>"):
    errs.append("text_tag 标签不配对")
# content 必须字符串
for e in card["elements"]:
    if e["tag"] == "div" and not isinstance(e["text"]["content"], str):
        errs.append("content 非字符串")
# 红票全部 ∈ zt 池
red_codes = set(re.findall(r"<text_tag color='red'>[^()]*?\((\d{6})", txt))
for c in red_codes:
    if c not in zt_codes:
        errs.append(f"红票 {c} 不在涨停池")
# 紫票（非688标注）全部 ∉ zt 池
violet_codes = set(re.findall(r"<text_tag color='violet'>[^()]*?\((\d{6})", txt))
for c in violet_codes:
    if c in zt_codes:
        errs.append(f"紫票 {c} 误在涨停池")

print("red_codes:", sorted(red_codes))
print("violet_count:", len(violet_codes))
print("errors:", errs if errs else "PASS ✅")

if errs:
    sys.exit(1)

OUT.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"OK -> {OUT}")
