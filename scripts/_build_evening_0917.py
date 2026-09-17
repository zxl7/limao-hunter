# -*- coding: utf-8 -*-
# 机构晚报 09-17 拼卡 + 质量门自检（v5 版式）
import json, re

CACHE = "/Users/zxl/WorkBuddy/狸猫猎手/cache"

# ---- 涨停池（沪深口径，去 .SS/.SZ 后缀）----
zt = json.load(open(f"{CACHE}/zt_history/zt_2026-09-17.json"))
zt_codes = set()
zt_names = {}
for x in zt["zt_list"]:
    c = str(x["code"]).replace(".SS", "").replace(".SZ", "")
    zt_codes.add(c)
    zt_names[c] = x["name"]
pulse = json.load(open(f"{CACHE}/pulse_snapshots/latest.json"))
total_zt = pulse.get("total_zt")


def tag(color, text):
    return f"<text_tag color='{color}'>{text}</text_tag>"


def div(content):
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def hr():
    return {"tag": "hr"}


# ============ 8 主线块 ============
B = []

B.append(div(
    "**🎯 国产算力链：昇腾960提前+智谱扩容+算力租赁涨价**\n\n"
    "▸ 机构依据：华为全联接大会昇腾960 DT 提前至27Q1、首搭NPO超节点（单节点4096卡互联）；智谱完成50亿美金融资、年末ARR指引上调至30亿美金；海外GPU租金再涨17%-21%、国内算力合同金额大幅上调。\n"
    "▸ 机构逻辑：国产算力从单芯片迈向系统级集群，上游晶圆制造、先进封装、光互连、设备环节随新品放量+订单增长全面受益；算力租赁从「租资源」转向「保底+调用分成」高附加值模式。\n"
    f"▸ 机构点名：{tag('violet','北方华创(002371)')}/{tag('violet','中微公司(688012·688无权限)')}/{tag('violet','光迅科技(002281)')}/{tag('violet','长电科技(600584)')}/{tag('violet','浪潮信息(000977)')}/{tag('violet','协创数据(300857)')}\n"
    f"📎 盘面对照：国产芯片涨停3家 = {tag('red','中晶科技(003026·3板)')}/{tag('red','沃顿科技(000920)')}/{tag('red','华软科技(002453)')}；PCB涨停2家 = {tag('red','澳弘电子(605058·5板)')}/{tag('red','中岩大地(003001·2板)')}；通信设备涨停2家 = {tag('red','世嘉科技(002796)')}/{tag('red','共达电声(002655)')}。机构点名算力核心票（北方华创/光迅/浪潮等）今日尚未涨停。"
))
B.append(hr())

B.append(div(
    "**🎯 AI硬件材料链：ABF载板+覆铜板+玻璃基板+金刚石散热**\n\n"
    "▸ 机构依据：ABF载板设备交期延至2031年、高端基板短缺率2026下半年14%→2028年51%；覆铜板产业链计划逐月提价5%-15%；22层玻璃芯基板技术突破；金刚石散热因芯片热流密度破500W/cm²从实验室迈向产业化。\n"
    "▸ 机构逻辑：AI服务器材料向更高等级演进，涨价周期具「成本滞后于提价」的超额利润弹性，叠加高端材料升级打开成长空间——周期重估+成长兑现双特征。\n"
    f"▸ 机构点名：{tag('violet','深南电路(002916)')}/{tag('violet','生益科技(600183)')}/{tag('violet','华正新材(603186)')}/{tag('violet','国机精工(002046)')}/{tag('violet','旗滨集团(601636)')}/{tag('violet','凯盛科技(600552)')}\n"
    f"📎 盘面对照：玻璃玻纤涨停1家 = {tag('red','三峡新材(600293)')}（玻璃基板方向呼应）；金刚石/覆铜板/ABF载板环节今日无涨停，机构点名票尚未封板。"
))
B.append(hr())

B.append(div(
    "**🎯 半导体设备+功率半导体：国产替代加速**\n\n"
    "▸ 机构依据：设备零部件交期显著拉长，国产替代从备份升级为主供；AI机柜功率迈向兆瓦级、800V高压架构使碳化硅/氮化镓渗透提速，全球8英寸产能同比萎缩2.4%。\n"
    "▸ 机构逻辑：AI算力迈入兆瓦级供电，功率器件价值量跃升3-5倍+国产份额替代，叠加设备零部件供不应求，具备产能/技术壁垒的国产厂商迎确定增长。\n"
    f"▸ 机构点名：{tag('violet','士兰微(600460)')}/{tag('violet','三安光电(600703)')}/{tag('violet','富创精密(688409·688无权限)')}/{tag('violet','江丰电子(300666)')}/{tag('violet','新莱应材(300260)')}\n"
    f"📎 盘面对照：半导体涨停2家 = {tag('red','中晶科技(003026·3板)')}/{tag('red','先导基电(600641)')}；功率半导体（士兰微/三安等）今日无涨停。"
))
B.append(hr())

B.append(div(
    "**🎯 人形机器人：特斯拉量产提速+手术机器人DRG支付**\n\n"
    "▸ 机构依据：特斯拉机器人年底周产目标2000台、产能规划100万台，GPT-6 Astra展现具身智能控制能力；DRG3.0设立9个手术机器人独立病组、支付标准较传统手术高3万余元，2027年3月底前落地。\n"
    "▸ 机构逻辑：特斯拉机器人跨过「0到1」进入「1到N」量产兑现期；DRG打通医院支付堵点，存量设备利用率提升直接增厚设备与耗材业绩。\n"
    f"▸ 机构点名：{tag('violet','拓普集团(601689)')}/{tag('violet','贝斯特(300580)')}/{tag('violet','五洲新春(603667)')}/{tag('violet','绿的谐波(688017·688无权限)')}/{tag('violet','天智航(688277·688无权限)')}\n"
    f"📎 盘面对照：机器人涨停7家 = {tag('red','科森科技(603626)')}/{tag('red','克来机电(603960)')}/{tag('red','均胜电子(600699)')}/{tag('red','冠盛股份(605088)')}/{tag('red','北特科技(603009)')}/{tag('red','天海电子(001365)')}/{tag('red','凯迪股份(605288)')}。机构T链点名票（拓普/贝斯特/五洲新春）今日尚未涨停。"
))
B.append(hr())

B.append(div(
    "**🎯 先进制造智能化：工业母机+工业软件自主可控**\n\n"
    "▸ 机构依据：总书记就发展先进制造业作出重要指示，强调智能化与自主可控，提升产业链自主可控水平。\n"
    "▸ 机构逻辑：聚焦智能制造与自主可控双主线，把握工业母机及高端装备国产替代，以及「人工智能+制造」下的工业软件与机器人核心部件。\n"
    f"▸ 机构点名：{tag('violet','中控技术(688777·688无权限)')}/{tag('violet','东土科技(300353)')}/{tag('violet','奥比中光(688322·688无权限)')}\n"
    "📎 盘面印证：今日盘面无工业母机/工业软件直接涨停呼应，机构主线偏政策驱动、尚未在盘面兑现。"
))
B.append(hr())

B.append(div(
    "**🎯 创新药+CXO+AI制药+干细胞**\n\n"
    "▸ 机构依据：2026上半年全国BD交易达1000亿美金，单个deal超100亿已五六个；「八一八号令」明确干细胞法律地位；AI制药密集融资（字节Anew Labs 2.9亿美元）；CD47重组蛋白突破血液毒性瓶颈、拓展动脉粥样硬化。\n"
    "▸ 机构逻辑：国产创新药BD出海从事件催化切换至商业化与临床兑现；干细胞法规扫清商业化障碍；AI制药从技术探索迈入产业验证，上游湿实验/CRO需求进入兑现期。\n"
    f"▸ 机构点名：{tag('violet','海思科(002653)')}/{tag('violet','中源协和(600645)')}/{tag('violet','美迪西(688202·688无权限)')}/{tag('violet','百普赛斯(301080)')}/{tag('violet','奥浦迈(688293·688无权限)')}\n"
    f"📎 盘面对照：医药涨停3家 = {tag('red','金石亚药(300434·20cm)')}/{tag('red','南华生物(000504)')}/{tag('red','百花医药(600721)')}；机构创新药/CXO点名票（海思科/中源协和/美迪西等）今日未涨停。"
))
B.append(hr())

B.append(div(
    "**🎯 油运+炼化：中东油管受损+VLCC运价新高**\n\n"
    "▸ 机构依据：VLCC即期运价创历史新高、1年期租租金达14万美元；沙特油管被损触发供应风险，国内释放原油商业储备平抑炼厂成本，成品油裂解价差走强。\n"
    "▸ 机构逻辑：供给长期受限+运距拉长+补库驱动，油运运价中枢有望维持高位，船东盈利弹性强；国内储备释放保障炼厂成本可控，高开工率捕捉高裂解利润窗口。\n"
    f"▸ 机构点名：{tag('red','招商轮船(601872)')}/{tag('violet','招商南油(601975)')}/{tag('violet','荣盛石化(002493)')}/{tag('violet','恒力石化(600346)')}/{tag('violet','恒逸石化(000703)')}\n"
    f"📎 盘面对照：油运涨停1家 = {tag('red','招商轮船(601872)')}（机构点名票直接命中）；炼化板块（荣盛/恒力/恒逸等）今日无涨停。"
))
B.append(hr())

B.append(div(
    "**🎯 AI影视IP+OTA酒店：多模态提速+反垄断重构**\n\n"
    "▸ 机构依据：芒果首部AI纪录片9月17日播出、豆包上线工作任务模式；携程反垄断调整佣金规则，流量向品质化头部连锁酒店倾斜、单体酒店加速出清。\n"
    "▸ 机构逻辑：AI影视多模态从主题变产业趋势，短剧/漫剧规模化盈利验证模型；OTA反垄断使平台流量向头部连锁酒店倾斜，连锁化率提升、行业结构优化。\n"
    f"▸ 机构点名：{tag('violet','芒果超媒(300413)')}/{tag('violet','中文在线(300364)')}/{tag('violet','上海电影(601595)')}/{tag('violet','锦江酒店(600754)')}/{tag('violet','首旅酒店(600258)')}\n"
    f"📎 盘面对照：出版传媒涨停2家 = {tag('red','内蒙新华(603230·2板)')}/{tag('red','龙版传媒(605577)')}；机构AI影视点名票（芒果/中文在线/上海电影）今日未涨停。"
))
B.append(hr())

# 🧭 速览
B.append(div(
    "**🧭 机构还看什么（速览）**\n\n"
    f"▸ 中药基药：十五五规划培育10个中成药品种、新版基药9月生效 → {tag('violet','以岭药业(002603)')}/{tag('violet','济川药业(600566)')}/{tag('violet','康缘药业(600557)')}（今日未涨停）\n"
    f"▸ 快递反内卷：派费不降+严跨区揽收，盈利稳定性预期增强 → {tag('violet','圆通速递(600233)')}/{tag('violet','申通快递(002468)')}/{tag('violet','韵达股份(002120)')}\n"
    "▸ 香港基建：首个五年规划利好北部都会区基建 → 中国建筑国际(03311.HK)/中国中铁(00390.HK)/中国铁建(01186.HK)（港股）"
))
B.append(hr())

# 📎 说明
B.append(div(
    "**📎 说明**\n\n"
    "机构观点 = Gangtise 晚报（09-17·25 话题浓缩），依据/逻辑/点名单引用原文\n"
    f"盘面印证 = 今日（09-17）涨停池（沪深{total_zt}家），只作机构观点佐证；无强关联主题不配印证\n"
    "红 = 今日已涨停 / 紫 = 机构核心标的未涨停\n"
    "仅供信息参考，不构成投资建议，投资需谨慎"
))

# ============ 组装卡片 ============
TITLE = "🏛️ 机构晚报 09-17 | 国产算力链领跑·机器人油运局部呼应"
card = {
    "title": TITLE,
    "color": "blue",
    "config": {"wide_screen_mode": True},
    "header": {"title": {"tag": "plain_text", "content": TITLE}, "template": "blue"},
    "elements": B,
}

# ============ 质量门自检 ============
BLACKLIST = ["共振", "theme_rotation", "inflow_rising", "boom", "背离引擎",
             "re-entry", "回流引擎", "维度"]
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
