# -*- coding: utf-8 -*-
import json, re, os

CACHE = "/Users/zxl/WorkBuddy/狸猫猎手/cache"
DATE = "2026-09-10"
WEEK = "09-10"

# 每块: (emoji_title, 依据, 逻辑, 点名(html), 印证)
blocks = []

blocks.append((
"🎯 能源危机：油价破百+气价新高",
"布伦特原油突破 100 美元/桶，中东冲突致全球炼油量降 7%-8%；荷兰 TTF 天然气突破 80 欧元/MWh、英国气价破 200 便士，均创 2023 年来新高，欧洲储气率仅 67% 为 16 年最低。",
"地缘供给缺口叠加冬季补库刚性 → 油气、煤化工、炼化价差走阔，景气上行周期开启。",
"<text_tag color='violet'>中国石油(601857)/中国海油(600938)/中国石化(600028)/荣盛石化(002493)/恒力石化(600346)/新奥股份(600803)/九丰能源(605090)/广汇能源(600256)</text_tag>",
"昨日油运链已动（航运 +11.3 亿、招商南油/招商轮船涨停），煤炭 +4.9 亿、云煤能源/郑州煤电涨停；但三桶油等大票未涨停，天然气方向盘面暂未直接响应。",
))

blocks.append((
"⚡ 疆煤外运+焦煤缺口",
"霍尔木兹封锁推升能源保供压力，疆煤战略地位提至突出位置，出疆三大通道运力饱和；蒙煤通关骤降、蒙古柴油危机或扰动焦煤出口，焦炭第五轮提涨将落地。",
"高煤价下外运经济性极佳（疆煤到港成本约 650 元 vs 港口 1000 元），掌握稀缺铁路通道与焦煤资源的公司锁定增量定价权。",
"<text_tag color='violet'>广汇物流(600603)/广汇能源(600256)/大秦铁路(601006)/特变电工(600089)/淮北矿业(600985)/平煤股份(601666)/山西焦煤(000983)/兖矿能源(600188)</text_tag>",
"昨日煤炭板块资金净流入、多股涨停（大有能源、郑州煤电、云煤能源 2 板），焦煤/焦炭盘面已启动。",
))

blocks.append((
"🔌 资源品涨价：铝/磷/沥青",
"铝全产业库存历史最低、远期投产审批收紧，铝价 22000-24000 元时年化股息率 8%-10%；磷矿供给受新规收紧、硫磺涨至 7000-8000 元/吨；沥青吨利润破 1200 元、旺季启动。",
"供给强约束+低库存+需求韧性三重共振，投资范式从博价格弹性转向配置高股息周期红利。",
"<text_tag color='violet'>天山铝业(002532)/神火股份(000933)/云铝股份(000807)/云天化(600096)/兴发集团(600141)/川恒股份(002895)/华锦股份(000059)/招商南油(601975)</text_tag>",
"昨日铝 +2.4 亿小幅流入，工业金属 +8.9 亿、基础化工 +9.1 亿流入（黑猫股份 2 板、彤程新材涨停）；但磷肥及磷化工 -4.5 亿流出，磷化工盘面暂未跟上。",
))

blocks.append((
"🤖 AI硬件：散热/交换机/电感三线齐发",
"AI 芯片功耗迈向 2000 瓦级，金刚石散热从送样迈向小批量；超节点交换机（多芯片集成的大算力机柜）2027 年国内需求翻倍至 2-3 万柜，博通 51.2T 芯片缺口达 10 万片；芯片电感在手订单同比 +400%。",
"算力功率密度飙升 → 散热、交换芯片国产替代、供电电感量价齐升，国产链确定性强。",
"<text_tag color='violet'>黄河旋风(600172)/中石科技(300684)/国机精工(002046)/四方达(300179)/盛科通信(688702·无权限)/锐捷网络(301165)/紫光股份(000938)/英维克(002837)/铂科新材(300811)/顺络电子(002138)/龙磁科技(300835)</text_tag>",
"分化明显——CPO +80.6 亿、光通信模块 +87.5 亿、PCB +98.5 亿、元件 +49.6 亿大幅流入；但半导体 -65.5 亿、人工智能 -106.5 亿整体流出，金刚石/电感细分暂未单独发酵。",
))

blocks.append((
"🎬 人形机器人：量产降本兑现",
"2025 年全球产量上万台，行星滚珠丝杠从 2000 元降至千元内、谐波减速器降至几百元，乐聚单台成本从 70 万降至 20 万；世界机器人大会展示分拣从 2 分钟缩至 30 秒。",
"产业从概念验证进入小批量交付，规模效应带动核心零部件边际变化，关注拿到 Tier1 订单的关节/丝杠/减速器厂商。",
"<text_tag color='violet'>拓普集团(601689)/恒立液压(601100)/五洲新春(603667)/长盈精密(300115)/三花智控(002050)/绿的谐波(688017·无权限)</text_tag>",
"机器人概念 +22.7 亿流入，但人形机器人细分 -14.8 亿流出，盘面暂未聚焦量产降本链，偏主题催化。",
))

blocks.append((
"🚗 特斯拉无人出租车开放",
"特斯拉在奥斯汀运营 420 台无人出租车，其中 45 台纯无人 CyberCab 9 月 4 日起对外开放，制造成本约 2 万美元、运营成本 0.2 美元/英里；国内政策 Q4 有望小幅放松。",
"专用车型凭极致成本加速商业闭环，利好确定性增量部件（线控转向/制动）与国内头部方案商复苏。",
"<text_tag color='violet'>伯特利(603596)/德赛西威(002920)/耐世特(01316.HK·港)/小马智行(PONY.O·美)/文远知行(WRD.O·美)/速腾聚创(02498.HK·港)</text_tag>",
"昨日特斯拉概念 +45.2 亿、新能源车 +79.9 亿流入，线控/激光雷达链盘面已动（激光雷达 +16.9 亿）。",
))

blocks.append((
"✈️ 边缘基础设施：个人智能体爆发",
"Meta Muse 个人智能体爆火，带动边缘节点厂商 Cloudflare、Akamai、Zscaler 大涨；个人智能体流量激增推升边缘推理、身份安全与流量分发需求。",
"智能体普及推动边缘基础设施与安全需求结构性增长，国内边缘节点龙头与交换芯片厂商确定性受益。",
"<text_tag color='violet'>网宿科技(300017)/浪潮信息(000977)/紫光股份(000938)/锐捷网络(301165)/英维克(002837)/中科曙光(603019)/工业富联(601138)/首都在线(300846)/欧陆通(300870)/盛科通信(688702·无权限)</text_tag>",
"通信设备链全线强（5G +126 亿、光纤 +64.9 亿、通信设备 +51.0 亿），液冷服务器 +11.6 亿；但边缘计算 -30.4 亿、算力概念 -18.3 亿流出，Agent 边缘主题暂未单独发酵。",
))

blocks.append((
"🧭 机器视觉国标加速",
"2026 年 6 月国家标准委下达机器视觉在线检测国标计划，工信部首次将机器视觉质检列入部委重点任务；大模型驱动通用质检模型落地（某车厂单一模型替代 150 个小模型）。",
"政策制定-标准统一-技术落地闭环，利好掌握核心成像器件与系统解决方案的平台型厂商。",
"<text_tag color='violet'>海康威视(002415)/矩子科技(300802)/奥普特(688686·无权限)/凌云光(688400·无权限)/天准科技(688003·无权限)/奥比中光(688322·无权限)</text_tag>",
"自动化设备 -7.8 亿流出，机器视觉盘面暂未单独发酵；激光设备 +21.7 亿、工业母机 +17.9 亿相关制造链有资金。",
))

blocks.append((
"💊 DRG3.0落地：手术机器人+创新药+双通道药店",
"DRG 3.0（按病种打包付费的医保新支付标准）约 80% 临床呼声高的技术获独立分组，含机器人手术 9 组、肿瘤靶向与免疫治疗独立成组；双通道药店成创新药院外放量核心渠道。",
"医保支出向真创新倾斜，打通支付障碍的手术机器人、高值耗材、肿瘤创新药及双通道药店迎来需求释放。",
"<text_tag color='violet'>益丰药房(603939)/恒瑞医药(600276)/天智航(688277·无权限)/海吉亚医疗(06078.HK·港)</text_tag>",
"昨日医药全线流出（创新药 -24.2 亿、化学制药 -18.4 亿、医药生物 -39.5 亿），DRG3.0 利好盘面尚未反应，属中线逻辑、短线资金观望。",
))

blocks.append((
"🎬 消费电子：苹果华为新机催化",
"9 月苹果、华为等多家厂商新机发布或开售，折叠屏与端侧 AI 双轮驱动。",
"新机带动铰链、超薄柔性玻璃、散热等核心环节订单落地，华为芯片突破引领国产半导体协同，结构性机会凸显。",
"<text_tag color='violet'>生益科技(600183)/苏州天脉(301626)/大族激光(002008)/华大九天(301269)/景旺电子(603228)/中芯国际(688981·无权限)</text_tag>",
"苹果概念 +47.4 亿、元件 +49.6 亿、PCB +98.5 亿流入，消费电子链盘面已动；但消费电子指数 -3.5 亿微幅流出，催化偏结构性。",
))

# 速览块
overview_lines = [
"▸ 字节 AI 版权平台：手握优质 IP 公司价值凸显 — <text_tag color='violet'>上海电影(601595)/奥飞娱乐(002292)/华策影视(300133)/中文在线(300364)/芒果超媒(300413)/安妮股份(002235)</text_tag>",
"▸ 全球食品价格创 2022 年来新高：种植/种业/饲料受益 — <text_tag color='violet'>苏垦农发(601952)/北大荒(600598)/隆平高科(000998)/登海种业(002041)/中粮糖业(600737)/海大集团(002311)</text_tag>",
"▸ 双节连休 13 天：长线游+县域游爆发 — <text_tag color='violet'>桂林旅游(000978)/长白山(603099)/峨眉山A(000888)/黄山旅游(600054)/曲江文旅(600706)</text_tag>",
]
overview_content = "**🧭 机构还看什么（一屏速览）**\n\n" + "\n".join(overview_lines) + "\n📎 盘面：桂林旅游昨日 3 板涨停；食品/种业、版权/IP 昨日资金均流出，盘面前述未跟上。"

elements = []
for (title, yiju, luoji, dianming, yinzheng) in blocks:
    content = "**" + title + "**\n\n" \
              "▸ 机构依据：" + yiju + "\n" \
              "▸ 机构逻辑：" + luoji + "\n" \
              "▸ 机构点名：" + dianming + "\n" \
              "📎 盘面印证：" + yinzheng
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
    elements.append({"tag": "hr"})

# overview
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": overview_content}})
elements.append({"tag": "hr"})

# 说明
shuoming = "**📎 说明**\n\n" \
           "▸ 数据源：Gangtise 早报 1 篇（" + WEEK + "，含 19 个热点话题）\n" \
           "▸ 盘面印证基准：2026-09-09 收盘（涨停 48 家、最高 5 板；板块资金流向）\n" \
           "▸ 口径：机构观点为主体，盘面印证仅作佐证；英文缩写已转白话，688/港美标的已标注\n" \
           "▸ 免责：基于公开信息整理，不构成投资建议"
elements.append({"tag": "div", "text": {"tag": "lark_md", "content": shuoming}})

card = {
    "title": "🏛️ 机构研报 " + WEEK + " 早盘 | 能源危机主线，AI硬件与机器人并进",
    "color": "blue",
    "config": {"wide_screen_mode": True},
    "header": {
        "title": {"tag": "plain_text", "content": "🏛️ 机构研报 " + WEEK + " 早盘 | 能源危机主线，AI硬件与机器人并进"},
        "template": "blue",
    },
    "elements": elements,
}

out = os.path.join(CACHE, "gangtise_morning_research.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(card, f, ensure_ascii=False, indent=2)
print("WROTE", out, "div blocks:", len([e for e in elements if e['tag']=='div']))

# ---- 6.5 导出机构票池 ----
VALID_PREFIX = ("600","601","603","605","000","001","002","003","300","301")
# 解析每块标题与选项
def strip_title(t):
    # 去 ** 与 emoji
    t = t.replace("**", "")
    # 移除开头 emoji（ord>0x1F000 或特定符号）
    t = re.sub(r'^[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d]+', '', t).strip()
    return t

pool_items = []
seen = set()
# 遍历 blocks（含 overview）提取 机构点名 行
all_blocks_for_pool = list(blocks)  # 主块
# 速览块单独处理
for (title, yiju, luoji, dianming, yin) in all_blocks_for_pool:
    theme = strip_title(title)
    # 提取点名行
    m = re.search(r"▸\s*机构点名：(.*)", dianming)
    # dianming 本身即标签内容，直接从 dianming 解析
    seg = dianming
    tokens = re.findall(r"([\u4e00-\u9fa5A-Za-z·]+)\((\d{6}[A-Za-z]?)[^)]*\)", seg)
    for name, code in tokens:
        code = code[:6]
        if not code.startswith(VALID_PREFIX):
            continue
        key = code
        if key in seen:
            continue
        seen.add(key)
        pool_items.append({"code": code, "name": name, "theme": theme})

# 速览块
ov_theme = "机构还看什么（速览）"
ov_tokens = re.findall(r"([\u4e00-\u9fa5A-Za-z·]+)\((\d{6}[A-Za-z]?)[^)]*\)", overview_content)
for name, code in ov_tokens:
    code = code[:6]
    if not code.startswith(VALID_PREFIX):
        continue
    if code in seen:
        continue
    seen.add(code)
    pool_items.append({"code": code, "name": name, "theme": ov_theme})

pool = {"date": DATE, "count": len(pool_items), "items": pool_items}
pool_out = os.path.join(CACHE, "institution_pool_%s.json" % DATE)
with open(pool_out, "w", encoding="utf-8") as f:
    json.dump(pool, f, ensure_ascii=False, indent=2)
print("WROTE", pool_out, "count:", len(pool_items))
for it in pool_items:
    print("  ", it['code'], it['name'], it['theme'])
