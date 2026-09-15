# -*- coding: utf-8 -*-
"""机构晚报速览卡 2026-09-14 构建 + 质量门自检。"""
import json, re, sys
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
ZT = json.loads((ROOT/"cache/zt_history/zt_2026-09-14.json").read_text(encoding="utf-8"))
ZT_CODES = {x["code"] for x in ZT["zt_list"]}  # 沪深涨停池（含688）

# ---- 文本块组装 ----
def block(title, body):
    return {"tag":"div","text":{"tag":"lark_md","content":f"**{title}**\n\n{body}"}}

E = []  # elements

E.append(block("🎯 AI算力硬件链（光模块CPO+算力基建+国产存储）",
 "▸ 机构依据：工信部印发“人工智能+软件”专项行动，发改委推进重大工程提速；大摩预计2026-2030年中国AI资本开支8.5万亿元，算力容量26GW增至81GW；长鑫LPDDR6量产。\n"
 "▸ 机构逻辑：政策与资本开支双驱动，算力基建全链（芯片/服务器/IDC/电力配套）景气延续；光模块向1.6T与CPO演进，价值向光芯片、ELS模组转移；长鑫量产推动BT基板国产替代。\n"
 "▸ 机构点名：<text_tag color='violet'>浪潮信息(000977)</text_tag> / <text_tag color='violet'>北方华创(002371)</text_tag> / <text_tag color='violet'>工业富联(601138)</text_tag> / <text_tag color='violet'>东山精密(002384)</text_tag> / <text_tag color='violet'>联特科技(301396)</text_tag> / <text_tag color='violet'>深南电路(002916)</text_tag> / <text_tag color='violet'>景旺电子(603228)</text_tag>（中际旭创03308.HK·港股 / 长鑫科技688825·无权限 / 寒武纪688256·无权限）\n"
 "📎 盘面对照：PCB涨停 4 家 = <text_tag color='red'>中京电子(002579)</text_tag> / <text_tag color='red'>满坤科技(301132·20cm)</text_tag> / <text_tag color='red'>科翔股份(300903·20cm)</text_tag> / <text_tag color='red'>澳弘电子(605058·2板)</text_tag>；光模块整机链今日未涨停（中际旭创/新易盛等高位分化）。"))

E.append({"tag":"hr"})
E.append(block("🔌 MLCC/被动元件/电子布涨价（AI服务器拉动）",
 "▸ 机构依据：三星电机签1.07万亿韩元AI服务器MLCC长约并涨价25%-30%，2026年全球AI级MLCC缺口2000万只；Low DK电子布2026放量元年需求超4000万米，供给缺口延续至2027。\n"
 "▸ 机构逻辑：AI服务器高容MLCC量价齐升叠加国产替代；电子布设备硬约束致供给短缺，具备织机与电子纱-布一体化能力者持续兑现业绩弹性。\n"
 "▸ 机构点名：<text_tag color='violet'>风华高科(000636)</text_tag> / <text_tag color='violet'>火炬电子(603678)</text_tag> / <text_tag color='violet'>国瓷材料(300285)</text_tag> / <text_tag color='violet'>博迁新材(605376)</text_tag> / <text_tag color='violet'>洁美科技(002859)</text_tag> / <text_tag color='violet'>中国巨石(600176)</text_tag> / <text_tag color='violet'>中材科技(002080)</text_tag> / <text_tag color='violet'>菲利华(300395)</text_tag> / <text_tag color='violet'>泰坦股份(003036)</text_tag>（三环集团06951.HK·港股）\n"
 "📎 盘面对照：被动元件/电子布涨停 4 家 = <text_tag color='red'>超声电子(000823·3板)</text_tag> / <text_tag color='red'>双星新材(002585·2板)</text_tag> / <text_tag color='red'>东方钽业(000962)</text_tag> / <text_tag color='red'>康达新材(002669)</text_tag>。"))

E.append({"tag":"hr"})
E.append(block("💎 金刚石/培育钻石GPU散热（最强盘面呼应）",
 "▸ 机构依据：金刚石热沉片已为4-5家芯片厂打样，2026年四季度小批量、2027年二季度起量，芯片结温降约15度、算力平均提升约20%；AI芯片功耗破1000-2000W，单晶金刚石导热为铜10倍；渗透率不足1%。\n"
 "▸ 机构逻辑：算力功耗临界点倒逼散热材料代际更替，国产供应链主导叠加领先企业量产突破，渗透率从1%到10%对应约50亿美元空间。\n"
 "▸ 机构点名：<text_tag color='red'>黄河旋风(600172)</text_tag> / <text_tag color='violet'>国机精工(002046)</text_tag> / <text_tag color='violet'>四方达(300179)</text_tag> / <text_tag color='violet'>力量钻石(301071)</text_tag> / <text_tag color='violet'>中兵红箭(000519)</text_tag> / <text_tag color='violet'>斯莱克(300382)</text_tag>（惠丰钻石920725·北交）\n"
 "📎 盘面对照：培育钻石涨停 2 家 = <text_tag color='red'>黄河旋风(600172)</text_tag> / <text_tag color='red'>恒盛能源(605580)</text_tag>（黄河旋风为机构核心标的且今日封板）。"))

E.append({"tag":"hr"})
E.append(block("💊 创新药+CXO（支付改革/数据读出/BD出海/AI4S）",
 "▸ 机构依据：2026版基药目录9月1日施行、医保谈判收官、DRG/DIP 3.0推进；WCLC与ESMO国产PD-1/VEGF双抗与ADC数据读出；2026上半年BD出海首付款69亿美元、总额1156亿美元；药明康德在手订单664亿元同比增25%；AI4S投融资44亿美元驱动试剂与CXO需求。\n"
 "▸ 机构逻辑：支付端改革从医院准入、诊疗支付到多元偿付三端疏通商业化；国产双抗与ADC凭头对头优效打开海外授权；CXO内外需同向走强，景气有望延续1-2年；AI制药放大湿实验需求。\n"
 "▸ 机构点名：<text_tag color='violet'>恒瑞医药(600276)</text_tag> / <text_tag color='violet'>药明康德(603259)</text_tag> / <text_tag color='violet'>康龙化成(300759)</text_tag> / <text_tag color='violet'>凯莱英(002821)</text_tag> / <text_tag color='violet'>泰格医药(300347)</text_tag> / <text_tag color='violet'>昭衍新药(603127)</text_tag> / <text_tag color='violet'>百普赛斯(301080)</text_tag> / <text_tag color='violet'>义翘神州(301047)</text_tag> / <text_tag color='red'>近岸蛋白(688137·20cm·无权限)</text_tag> / <text_tag color='violet'>奥浦迈(688293·无权限)</text_tag> / <text_tag color='violet'>百利天恒(688506·无权限)</text_tag>（信达生物/康方生物/荣昌生物/君实生物/石药集团·港股；百济神州·美股）\n"
 "📎 盘面对照：医药涨停 5 家 = <text_tag color='red'>三力制药(603439·2板)</text_tag> / <text_tag color='red'>万邦医药(301520·20cm)</text_tag> / <text_tag color='red'>澳洋健康(002172)</text_tag> / <text_tag color='red'>津药药业(600488)</text_tag> / <text_tag color='red'>近岸蛋白(688137·20cm·无权限)</text_tag>。"))

E.append({"tag":"hr"})
E.append(block("🤖 特斯拉人形机器人量产爬坡",
 "▸ 机构依据：大客户周产量超500台，9月底预计超1000台、10-11月高速爬坡，首批1.5万台将打满现有产能，T审厂在即。\n"
 "▸ 机构逻辑：量产从周产数百台向千台级跃升，核心零部件供应商产能打满并启动扩产，具备全球化配套能力的国产厂商率先受益。\n"
 "▸ 机构点名：<text_tag color='violet'>恒立液压(601100)</text_tag> / <text_tag color='violet'>拓普集团(601689)</text_tag> / <text_tag color='violet'>双环传动(002472)</text_tag> / <text_tag color='violet'>汇川技术(300124)</text_tag> / <text_tag color='violet'>科达利(002850)</text_tag> / <text_tag color='violet'>长盈精密(300115)</text_tag> / <text_tag color='violet'>五洲新春(603667)</text_tag> / <text_tag color='violet'>浙江荣泰(603119)</text_tag> / <text_tag color='violet'>鸣志电器(603728)</text_tag> / <text_tag color='violet'>斯菱智驱(301550)</text_tag>（三花智控002050·A股 / 绿的谐波688017·无权限 / 奥比中光688322·无权限）\n"
 "📎 盘面对照：机器人方向今日无个股涨停，机构核心标的均未封板，盘面主线在PCB与培育钻石。"))

E.append({"tag":"hr"})
E.append(block("⚡ 半导体设备/存储测试/MFC/功率半导体",
 "▸ 机构依据：长鑫LPDDR6量产带动国产存储扩产；存储ATE国产化率8%-10%迎批量替代（精智达、悦芯科技合计超30亿元订单）；MFC国产替代率不足3%加速；功率半导体进入上行周期，部分产品交期延至52周。\n"
 "▸ 机构逻辑：AI与国产存储扩产带动设备与材料；MFC海外缺货倒逼下游加速认证；功率半导体受AI数据中心拉动与产品涨价双向推动，硅片与器件双业务受益。\n"
 "▸ 机构点名：<text_tag color='violet'>北方华创(002371)</text_tag> / <text_tag color='violet'>长川科技(300604)</text_tag> / <text_tag color='violet'>联动科技(301369)</text_tag> / <text_tag color='violet'>万讯自控(300112)</text_tag> / <text_tag color='violet'>士兰微(600460)</text_tag> / <text_tag color='violet'>三安光电(600703)</text_tag> / <text_tag color='violet'>立昂微(605358)</text_tag> / <text_tag color='red'>高凯技术(688835·20cm·无权限)</text_tag>（星源材质300568·A股 / 圣邦股份300661·A股 / 天岳先进688234·无权限 / 芯联集成688469·无权限）\n"
 "📎 盘面对照：MFC/设备涨停 = <text_tag color='red'>高凯技术(688835·20cm·无权限)</text_tag>；半导体材料 = <text_tag color='red'>天通股份(600330)</text_tag>（盘面国产芯片与半导体约3家涨停）。"))

E.append({"tag":"hr"})
E.append(block("✈️ 原油破百与油运（中东地缘）",
 "▸ 机构依据：沙特东西管道关停、霍尔木兹通行受损，中东石油出口减量千万桶级；布伦特原油突破100美元/桶；阿曼航线运价超20万美元/天，部分航线TCE约42万美元/天；全球库存低位。\n"
 "▸ 机构逻辑：供应中断规模大、修复慢，油价高位与运价暴增并行，上游开采与油运环节直接享受弹性，库存低位进一步放大利润。\n"
 "▸ 机构点名：<text_tag color='violet'>中国海油(600938)</text_tag> / <text_tag color='violet'>中国石油(601857)</text_tag> / <text_tag color='violet'>招商轮船(601872)</text_tag> / <text_tag color='violet'>中海油服(601808)</text_tag> / <text_tag color='violet'>海油工程(600583)</text_tag> / <text_tag color='violet'>石化油服(600871)</text_tag> / <text_tag color='violet'>亚星锚链(601890)</text_tag>（中远海能600026·A股 / 01138.HK·港股）\n"
 "📎 盘面对照：油运与油气今日无个股涨停，招商轮船等高位震荡，盘面未现封板。"))

E.append({"tag":"hr"})
E.append(block("🛒 消费修复（白酒/乳业/跨境电商/名创/户外）",
 "▸ 机构依据：茅台系列酒性价比凸显、渠道利润修复，中秋批价企稳；散奶供应收紧叠加双节备货，乳业反转年预期升温；跨境电商渗透率提升、利润端增40%-50%；名创优品大店升级与自有IP提价；越野跑鞋增速居户外品类首位。\n"
 "▸ 机构逻辑：消费呈收入增长向利润释放切换，品牌化与IP运营提升盈利；白酒控量保价筑底，乳业原奶周期反转，跨境与户外高景气延续。\n"
 "▸ 机构点名：<text_tag color='violet'>贵州茅台(600519)</text_tag> / <text_tag color='violet'>五粮液(000858)</text_tag> / <text_tag color='violet'>山西汾酒(600809)</text_tag> / <text_tag color='violet'>古井贡酒(000596)</text_tag> / <text_tag color='violet'>伊利股份(600887)</text_tag> / <text_tag color='violet'>新乳业(002946)</text_tag> / <text_tag color='violet'>妙可蓝多(600882)</text_tag> / <text_tag color='violet'>小商品城(600415)</text_tag> / <text_tag color='violet'>致欧科技(301376)</text_tag> / <text_tag color='violet'>三夫户外(002780)</text_tag>（安克创新300866·A股 / 蒙牛/安踏/李宁/名创·港股美股）\n"
 "📎 盘面对照：消费零售与旅游涨停 = <text_tag color='red'>欧亚集团(600697)</text_tag> / <text_tag color='red'>国芳集团(601086)</text_tag> / <text_tag color='red'>桂林旅游(000978)</text_tag>（双节备货与出游催化）。"))

E.append({"tag":"hr"})
E.append(block("🧭 机构还看什么",
 "▸ 玻璃基板（京东方试验线通线、三星电机规划2027量产）：<text_tag color='red'>凯盛新能(600876·3板)</text_tag> 盘面领涨；奥来德688378·无权限 / 鼎龙股份(300054) / 帝尔激光(300776)。\n"
 "▸ 电网/电力（闽东电力4板领涨梯队）：<text_tag color='red'>闽东电力(000993·4板)</text_tag> / <text_tag color='red'>通达股份(002560·2板)</text_tag> / <text_tag color='red'>三星电气(601567)</text_tag> / <text_tag color='red'>豫能控股(001896)</text_tag>。\n"
 "▸ 网络安全（盘面异动，机构未单覆盖）：<text_tag color='red'>中新赛克(002912·3板)</text_tag> / <text_tag color='red'>天融信(002212)</text_tag>。\n"
 "▸ 埋嵌PCB（AI电源与先进封装）：中富电路(300814) / 深南电路(002916) / 鹏鼎控股(002938) / 景旺电子(603228)。\n"
 "▸ Robotaxi出海（小马、文远获欧洲许可）：速腾聚创02498.HK·港股 / 德赛西威(002920)。"))

E.append({"tag":"hr"})
E.append(block("📎 说明",
 "▸ 红字=今日已涨停个股（沪深口径，共55家）；紫字=机构核心标的但未涨停。\n"
 "▸ 科创板(688)、北交所、港股、美股已标注或剔除（无A股交易权限）；A+H公司写A股代码。\n"
 "▸ 盘面涨停归属依据收盘涨停池（zt_list）与板块归集，未编造。\n"
 "▸ 数据源：Gangtise 机构晚报（ID 1102·09-14）含核心标的 + 收盘涨停池。\n"
 "▸ 仅供信息参考，不构成投资建议，投资需谨慎。"))

card = {"title":"🏛️ 机构晚报 09-14 | AI硬件链PCB集体涨停，培育钻石散热封板",
        "color":"blue","elements":E}

# ---- 质量门 ----
errs = []
content_all = "\n".join(
    (el["text"]["content"] if el.get("tag")=="div" and "text" in el else "")
    for el in E)
# 1) 红票必须 ∈ 涨停池
red_codes = re.findall(r"<text_tag color='red'>([^<]+?)</text_tag>", content_all)
red_miss = []
for r in red_codes:
    m = re.search(r"\((\d{6})", r)
    if not m or m.group(1) not in ZT_CODES:
        red_miss.append(r)
if red_miss:
    errs.append(f"红票不在涨停池: {red_miss}")
# 2) 紫票必须 ∉ 涨停池
violet_codes = re.findall(r"<text_tag color='violet'>([^<]+?)</text_tag>", content_all)
violet_hit = []
for v in violet_codes:
    m = re.search(r"\((\d{6})", v)
    if m and m.group(1) in ZT_CODES:
        violet_hit.append(v)
if violet_hit:
    errs.append(f"紫票误红(应在涨停池却紫): {violet_hit}")
# 3) 黑话
banned = ["共振","维度","回流引擎","theme_rotation","inflow_rising","boom","re-entry","背离引擎"]
hits = [b for b in banned if b in content_all]
if hits:
    errs.append(f"黑话命中: {hits}")
# 4) 结构禁项
for bad in ["column_set","<font","#"]:
    if bad in content_all:
        errs.append(f"含禁项: {bad}")
# 5) content 必须字符串
for el in E:
    if el.get("tag")=="div" and not isinstance(el.get("text",{}).get("content"), str):
        errs.append("div content 非字符串")

if errs:
    print("❌ 质量门未过:")
    for e in errs: print("  -", e)
    sys.exit(1)
print(f"✅ 质量门通过 | 红票{len(red_codes)}只(全∈涨停池) 紫票{len(violet_codes)}只(全∉涨停池) 块数{len([e for e in E if e.get('tag')=='div'])}")
out = ROOT/"cache/gangtise_evening_research.json"
out.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"✅ 已写入 {out}")
