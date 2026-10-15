# -*- coding: utf-8 -*-
# 机构晚报 09-18 拼卡 + 质量门自检（v5 版式）
import json, re

CACHE = "/Users/zxl/WorkBuddy/狸猫猎手/cache"

# ---- 涨停池（沪深口径，源=zt_history/zt_2026-09-18.json zt_list，质量门权威集）----
zt = json.load(open(f"{CACHE}/zt_history/zt_2026-09-18.json"))
zt_codes = set()
for x in zt["zt_list"]:
    zt_codes.add(str(x["code"]).replace(".SS", "").replace(".SZ", ""))
pulse = json.load(open(f"{CACHE}/pulse_snapshots/latest.json"))
total_zt = pulse.get("total_zt")  # 沪深口径 79


def tag(color, text):
    return f"<text_tag color='{color}'>{text}</text_tag>"


def div(content):
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def hr():
    return {"tag": "hr"}


# ============ 8 主线块 ============
B = []

# 1. 国产AI算力全链
B.append(div(
    "**🎯 国产AI算力全链：芯片量产+软件补齐+服务器扩散+大模型商用**\n\n"
    "▸ 机构依据：寒武纪690计划26年三季度量产、明年出货有望超700亿收入；AI自动生成算子约半数性能已达或超过CUDA原生水平；戴尔、联想股价创新高印证推理服务器需求向企业、政府扩散；OpenRouter数据显示大模型Token周调用量较年初增长1676%，智谱ARR达18亿美元。\n"
    "▸ 机构逻辑：国产算力已跨过“能用”门槛、迈向“好用”，推理时代需求从云厂商扩散到多行业；软件生态从“人工适配”转向“AI量产”，国产替代确定性增强。\n"
    f"▸ 机构点名：{tag('violet','中科曙光(603019)')}/{tag('violet','通富微电(002156)')}/{tag('violet','浪潮信息(000977)')}/{tag('violet','锐捷网络(301165)')}/{tag('violet','深信服(300454)')}\n"
    f"📎 盘面对照：国产芯片涨停11家 = {tag('red','托伦斯(301583·20cm)')}/{tag('red','华软科技(002453·2板)')}/{tag('red','诚邦股份(603316)')}/{tag('red','惠科股份(001399)')}/{tag('red','华天科技(002185·封测)')}/{tag('red','和远气体(002971)')}/{tag('red','盛景微(603375)')}/{tag('red','肯特催化(603120)')}/{tag('red','和顺石油(603353)')}/{tag('red','中旗新材(001212)')}/{tag('red','柏诚股份(601133)')}；机构点名的算力整机/服务器票（中科曙光/浪潮/通富/锐捷/深信服）今日尚未涨停。"
))
B.append(hr())

# 2. AI硬件材料工艺链
B.append(div(
    "**🎯 AI硬件材料工艺链：CPO硅光+高速板材+超快激光**\n\n"
    "▸ 机构依据：1.6T光模块转向CPO/NPO架构、DSP取消后硅光芯片（PIC）与高速电芯片（EIC）合计占物料成本约六成；AI高速板材向M9/M10升级，BCB树脂用量数倍提升、全球产能偏紧；M9/M10材料与玻璃基板推动超快激光设备明年需求有望超2500台。\n"
    "▸ 机构逻辑：AI服务器材料向更高等级演进，硅光价值重估+高端板材涨价+先进工艺设备放量，国产企业在认证与产能上占据有利位置，打开成长空间。\n"
    f"▸ 机构点名：{tag('violet','卓胜微(300782)')}/{tag('violet','天孚通信(300394)')}/{tag('violet','东材科技(601208)')}/{tag('violet','圣泉集团(605589)')}/{tag('violet','英诺激光(301021)')}\n"
    f"📎 盘面对照：PCB板涨停3家 = {tag('red','大亚圣象(000910)')}/{tag('red','天津普林(002134)')}/{tag('red','华塑控股(000509)')}；光通信涨停3家 = {tag('red','远望谷(002161)')}/{tag('red','共进股份(603118)')}/{tag('red','华盛昌(002980)')}；机构点名票（卓胜微/天孚/东材/圣泉/英诺激光）今日未封板。"
))
B.append(hr())

# 3. 半导体设备+功率半导体
B.append(div(
    "**🎯 半导体设备+功率半导体：扩产+第三波涨价**\n\n"
    "▸ 机构依据：长鑫已启动IPO、长存已问询，存储与先进逻辑大扩产趋势向上；前道量测、后道测试等低国产化率环节从验证导入走向批量采购，零部件订单增速或高于设备厂；功率半导体年内已有两轮涨价、部分酝酿第三轮，AI数据中心与800V高压平台拉动碳化硅需求。\n"
    "▸ 机构逻辑：AI需求引爆的国内晶圆扩产周期已至，设备与零部件国产替代迎份额提升+订单放量双机会；功率器件量价齐升、盈利改善弹性大。\n"
    f"▸ 机构点名：{tag('violet','北方华创(002371)')}/{tag('violet','精测电子(300567)')}/{tag('violet','士兰微(600460)')}/{tag('violet','扬杰科技(300373)')}/{tag('violet','斯达半导(603290)')}\n"
    "📎 盘面对照：半导体广义涨停（见国产芯片块11家）；设备/功率专属环节今日无独立涨停，机构点名票（北方华创/精测/士兰微/扬杰/斯达）未封板。"
))
B.append(hr())

# 4. 医药工业十五五
B.append(div(
    "**🎯 医药工业十五五：高端影像+手术机器人**\n\n"
    "▸ 机构依据：9月18日十部门发布医药工业“十五五”规划，目标2030年规上企业营收超3.5万亿，重点发展高端CT等AI辅助诊断产品；9月2日DRG/DIP 3.0版明确手术机器人支付结算，骨科关节置换等机器人辅助手术获独立分组。\n"
    "▸ 机构逻辑：规划与支付方案形成政策合力，利好具备高端影像、手术机器人产品线的国产龙头，入院加速、支付环境改善。\n"
    f"▸ 机构点名：{tag('violet','迈瑞医疗(300760)')}/{tag('violet','开立医疗(300633)')}/{tag('violet','伟思医疗(688580·688无权限)')}/{tag('violet','天智航(688277·688无权限)')}\n"
    f"📎 盘面对照：医药涨停3家 = {tag('red','亚泰集团(600881)')}/{tag('red','瑞康医药(002589)')}/{tag('red','南华生物(000504)')}；高端影像/手术机器人机构点名票（迈瑞/开立）今日未涨停。"
))
B.append(hr())

# 5. AI制药+CXO+创新药出海
B.append(div(
    "**🎯 AI制药+CXO+创新药出海**\n\n"
    "▸ 机构依据：Anthropic与诺和诺德合作、蛋白设计湿实验命中率近五成，海外AI制药板块大涨；2026上半年License-out交易额达997亿美元，超15家药企产品处全球三期；中国临床入组效率远超海外。\n"
    "▸ 机构逻辑：AI制药从概念走向订单落地，上游基因合成、蛋白试剂等“湿实验”环节需求爆发、确定性受益；创新药出海从授权收入迈向持续性销售分成。\n"
    f"▸ 机构点名：{tag('violet','百普赛斯(301080)')}/{tag('violet','义翘神州(301047)')}/{tag('violet','奥浦迈(688293·688无权限)')}/{tag('violet','纳微科技(688690·688无权限)')}（出海主体多为港股/美股：恒瑞/信达/康方）\n"
    "📎 盘面对照：今日医药涨停3家（亚泰/瑞康/南华生物，属医疗流通与服务环节）；AI制药/CXO与出海主线以港股为主，A股可交易核心票（百普赛斯/义翘神州）今日未涨停。"
))
B.append(hr())

# 6. 端侧AI硬件
B.append(div(
    "**🎯 端侧AI硬件扩散：手机/汽车/AIoT**\n\n"
    "▸ 机构依据：大模型从云端向手机、汽车、AIoT、可穿戴下沉，模型小型化与低功耗芯片成熟，端侧AI由“能否部署”进入“能否规模出货”阶段；乐鑫、恒玄、瑞芯微等产业链公司多点发力。\n"
    "▸ 机构逻辑：端侧AI产业拐点确认，具备跨终端操作系统与AIoT芯片矩阵的龙头，享受设备放量与Agent下沉双重红利。\n"
    f"▸ 机构点名：{tag('violet','中科创达(300496)')}/{tag('violet','瑞芯微(603893)')}/{tag('violet','星宸科技(301536)')}/{tag('violet','全志科技(300458)')}\n"
    "📎 盘面对照：今日盘面无端侧AI芯片直接涨停呼应，机构点名票（中科创达/瑞芯微/星宸/全志）未封板。"
))
B.append(hr())

# 7. 多模态AI+AI影视IP
B.append(div(
    "**🎯 多模态AI+AI影视IP**\n\n"
    "▸ 机构依据：7月以来minimax、阿里、字节等多模态产品密集发布；首部纯AI原生长剧《后西游记》播出，实现社会与商业双价值；AI视频游戏实时互动赋能内容升级。\n"
    "▸ 机构逻辑：视频生成模型代际升级使AI可低成本规模化生产长内容，内容产业成本结构与生产方式被改写，率先跑通AI影视工业化的平台型公司受益。\n"
    f"▸ 机构点名：{tag('violet','芒果超媒(300413)')}（其余腾讯/阅文/阿里/网易为港股美股）\n"
    f"📎 盘面对照：出版传媒涨停3家 = {tag('red','内蒙新华(603230·3板)')}/{tag('red','新华文轩(601811)')}/{tag('red','国脉文化(600640·AI大模型)')}；AI影视机构点名票（芒果超媒）今日未涨停。"
))
B.append(hr())

# 8. 外贸新质出海
B.append(div(
    "**🎯 外贸新质出海：广交会AI+跨境电商**\n\n"
    "▸ 机构依据：第140届广交会10月15日开幕、64%参展企业应用AI，首推22个智慧应用场景；前8月中国出口增长14.6%、机电出口增长24.9%，跨境电商AI应用率超62%；境外采购商预登记超21万。\n"
    "▸ 机构逻辑：AI赋能的新质产品出海与服务模式升级同频，关注会展智慧化方案、AI硬件与绿色产品出口供应链、跨境电商服务商。\n"
    f"▸ 机构点名：{tag('violet','米奥会展(300795)')}/{tag('violet','赛维时代(301381)')}/{tag('violet','比亚迪(002594)')}/{tag('violet','焦点科技(002315)')}\n"
    f"📎 盘面对照：外贸受益概念涨停7家 = {tag('red','奥康国际(603001)')}/{tag('red','七匹狼(002029)')}/{tag('red','真爱美家(003041)')}/{tag('red','浙江正特(001238)')}/{tag('red','龙头股份(600630)')}/{tag('red','华纺股份(600448)')}/{tag('red','德豪润达(002005)')}；机构点名票（米奥/赛维/比亚迪/焦点）今日未封板，盘面涨停集中在纺织服饰出口链。"
))
B.append(hr())

# 🧭 速览
B.append(div(
    "**🧭 机构还看什么（速览）**\n\n"
    f"▸ 脑机接口康复拿证：奥义科技仿生手获批，面向3000万手功能障碍患者 → {tag('violet','三博脑科(301293)')}/{tag('violet','乐普医疗(300003)')}/{tag('violet','爱朋医疗(300753)')}（今日未涨停）\n"
    f"▸ 消费级3D打印：千元设备普及+AI建模，FDM耗材需求高增 → {tag('violet','家联科技(301193·核心耗材供应商)')}（今日未涨停）"
))
B.append(hr())

# 📎 说明
B.append(div(
    "**📎 说明**\n\n"
    "机构观点 = Gangtise 晚报（09-18·18 话题浓缩），依据/逻辑/点名单引用原文\n"
    f"盘面印证 = 今日（09-18）涨停池（沪深{total_zt}家·最高4板），只作机构观点佐证；无强关联主题不配印证\n"
    "红 = 今日已涨停 / 紫 = 机构核心票未涨停\n"
    "仅供信息参考，不构成投资建议，投资需谨慎"
))

# ============ 组装卡片 ============
TITLE = "🏛️ 机构晚报 09-18 | 国产AI算力全链领跑·芯片端批量涨停呼应"
card = {
    "title": TITLE,
    "color": "blue",
    "config": {"wide_screen_mode": True},
    "header": {"title": {"tag": "plain_text", "content": TITLE}, "template": "blue"},
    "elements": B,
}

# ============ 质量门自检 ============
BLACKLIST = ["共振", "theme_rotation", "inflow_rising", "boom", "背离引擎",
             "re-entry", "回流引擎", "维度", "研判", "标的", "卡位", "兑现",
             "埋伏", "分歧转一致", "打板", "容量核心", "弱转强", "题材板块效应",
             "孤军", "冰点档", "严格主升", "宽题材兜底", "中位承接不足", "已结算"]
problems = []
all_content = []
red_codes, violet_codes = [], []
for e in B:
    if e["tag"] != "div":
        continue
    c = e["text"]["content"]
    if not isinstance(c, str):
        problems.append("content 非字符串")
    all_content.append(c)
    for m in re.finditer(r"<text_tag color='(red|violet)'>([^<]+)</text_tag>", c):
        col, txt = m.group(1), m.group(2)
        for code in re.findall(r"\b(\d{6})\b", txt):
            (red_codes if col == "red" else violet_codes).append(code)

for c in red_codes:
    if c not in zt_codes:
        problems.append(f"红票不在涨停池: {c}")
for c in violet_codes:
    if c in zt_codes:
        problems.append(f"紫票误红(在涨停池): {c}")
full = "\n".join(all_content)
for w in BLACKLIST:
    if w in full:
        problems.append(f"黑话命中: {w}")
for w in ["column_set", "#", "<font color"]:
    if w in full:
        problems.append(f"禁用项命中: {w}")
if full.count("<text_tag") != full.count("</text_tag>"):
    problems.append("text_tag 不配对")

print("== 质量门 ==")
print(f"红票 {len(red_codes)} 只, 紫票 {len(violet_codes)} 只")
print(f"红票唯一: {sorted(set(red_codes))}")
print(f"紫票唯一: {sorted(set(violet_codes))}")
print(f"涨停池大小: {len(zt_codes)}")
if problems:
    print("❌ 发现问题:")
    for p in problems:
        print("  -", p)
else:
    print("✅ 全部通过")
    out = f"{CACHE}/gangtise_evening_research.json"
    json.dump(card, open(out, "w"), ensure_ascii=False, indent=2)
    print("✅ 已写入", out)
