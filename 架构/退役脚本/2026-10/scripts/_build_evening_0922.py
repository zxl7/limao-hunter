# -*- coding: utf-8 -*-
"""机构晚报速览卡 2026-09-22 拼卡 + 质量门自检（v2 修复版）。

⛔ v1 事故（2026-09-22 21:09 首推）：elements 误写成 {"tag":"div","lark_md":...}
   —— 飞书 div 必须用 {"tag":"div","text":{"tag":"lark_md","content":...}}，
   缺 text 包裹 → div 无正文，客户端只见标题+分割线（整卡空白）。
   且 v1 自检只查「存在 content 时其类型」，键根本不存在 → 空过。
   本版：① 恢复 text/content 结构；② 门禁加「div 必须有非空 text.content」硬判；
   ③ 门禁加「涨停池个股不得以明文出现」（防漏红）；④ A+H 一律写 A 股代码。
"""
import json, re, sys
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
ZT = json.load(open(ROOT / "cache/zt_history/zt_2026-09-22.json", encoding="utf-8"))

zt_codes, zt_names = set(), {}
for z in ZT["zt_list"]:
    zt_codes.add(z["code"])
    zt_names[z["code"]] = z["name"]

HU_SHEN = 63  # pulse latest.json total_zt（沪深口径·剔除北交所 920526 凯华材料）

# ── 渲染助手 ───────────────────────────────────────────────────────────────
def R(t):  # 已涨停 → 红（🏛️-5 涨停必红）
    return f"<text_tag color='red'>{t}</text_tag>"

def V(t):  # 机构点名未涨停 → 紫
    return f"<text_tag color='violet'>{t}</text_tag>"

def names(*pairs, color="violet"):
    """pairs: (简称, 代码6位[, 后缀]) → 逐个着色后用 / 连接"""
    fn = R if color == "red" else V
    out = []
    for p in pairs:
        nm, cd = p[0], p[1]
        suf = p[2] if len(p) > 2 else ""
        out.append(fn(f"{nm}({cd}{suf})"))
    return "/".join(out)

def div(stock, content):
    """⛔ 飞书 div 唯一合法结构（缺 text 包裹=整块空白·09-22 事故）"""
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}

HR = {"tag": "hr"}


# ── 机构主线块（同链合并·最重要在前）─────────────────────────────────────
blocks = []

# 1) AI Agent 算力（话题 1 CPU + 话题 6 Agentic AI 消费落地）
blocks.append(
    "**🎯 AI Agent 算力：CPU 从配角变调度核心**\n\n"
    "▸ 机构依据：Meta 个人智能体 Muse 上线一周登顶美国免费应用总榜，9 月 19 日下载 26.4 万、日活 44.8 万，为每个用户开独立云端虚拟机跑网页浏览与代码任务，大量消耗 CPU；英特尔称 CPU 需求远超产能，目前只能满足约五成客户订单；智能体负载下 CPU 与 GPU 配比有望从 1:8 提升至 1:1。\n"
    "▸ 机构逻辑：智能体把 CPU 从算力配角推升为调度核心，服务器 CPU 量价齐升，国产厂商迎来互联网大厂导入与份额扩张；Agent 消费落地同时让电商平台变成交易基础设施、数字营销按真实交易意图变现。\n"
    "▸ 机构点名：" + names(("中国长城", "000066"), ("通富微电", "002156"), ("兴森科技", "002436"),
                            ("值得买", "300785"), ("易点天下", "301171"), ("蓝色光标", "300058"),
                            ("壹网壹创", "300792"), ("凯淳股份", "301001"))
    + "（海光信息/聚辰股份/寒武纪/龙芯中科/光云科技 688·无权限·阿里巴巴/腾讯控股 港美上市）\n"
    "📎 盘面对照：半导体涨停 3 家 = " + names(("先导基电", "600641"), ("瑞芯微", "603893"),
                                                  ("博通集成", "603068", "·3板"), color="red")
    + "；计算机设备涨停 2 家 = " + names(("旋极信息", "300324", "·20cm"), ("中新赛克", "002912"), color="red")
    + "；机构点名的中国长城盘中触板后回落，收在涨停下方。")

# 2) 算力租赁与 AIDC（话题 3 阿里真武 V900 + 话题 7 海外租金上调）
blocks.append(
    "**⚡ 算力租赁与 AIDC：海外租金再上调，国内长约排至 2028**\n\n"
    "▸ 机构依据：海外头部算力平台 Nebius 计划 10 月 1 日起第二次全面上调按需租金，H100 等机型小时租金上调 17%-21%，B300 年内累计涨约 56%；国内十亿级算力大单密集落地，长约排到 2028 年；阿里云栖大会发布真武 V900 芯片，算力较前代提升 3 倍、单集群可扩至 50 万卡，目标 2032 年数据中心规模超 20GW。\n"
    "▸ 机构逻辑：算力租赁从拼规模转向比价格话语权，高端算力成稀缺资产，合同负债与盈利能力进入加速释放期；芯片—模型—云相互拉动，服务器、数据中心与租赁服务商依次受益。\n"
    "▸ 机构点名：" + names(("协创数据", "300857"), ("宏景科技", "301396"), ("润泽科技", "300442"),
                            ("数据港", "603881"), ("赛意信息", "300687"), ("利通电子", "603629"))
    + "（寒武纪/盛科通信 688·无权限）\n"
    "📎 盘面对照：数据中心/IT 服务涨停 3 家 = " + names(("新炬网络", "605398"), ("南威软件", "603636", "·2板"),
                                                            ("南兴股份", "002757"), color="red"))

# 3) 机器人（话题 2）
blocks.append(
    "**🤖 机器人：特斯拉 Optimus 量产在即**\n\n"
    "▸ 机构依据：特斯拉团队 9 月 17 日开启新一轮机器人审厂，有望向供应链下达订单；Optimus 2026 年预计下线约 5 万台用于内部超级工厂，得州工厂主结构接近完工、2027 年夏拟投产，年产能目标 1000 万台；花旗称特斯拉拟把中国供应商升为认证量产伙伴，附带约 5000 台初始订单。\n"
    "▸ 机构逻辑：Optimus 正从方案冻结走向规模爬产，首批批量订单验证供应链量产能力，已获核心份额、具备跨国交付能力的中国零部件商率先受益。\n"
    "▸ 机构点名：" + names(("恒立液压", "601100"), ("三花智控", "002050"), ("拓普集团", "601689"),
                            ("浙江荣泰", "603119"), ("模塑科技", "000700"), ("北特科技", "603009"),
                            ("科达利", "002850"))
    + "（绿的谐波 688·无权限）\n"
    "📎 盘面对照：机器人涨停 3 家 = " + names(("日海智能", "002313"), ("联合精密", "001268"),
                                                ("三羊马", "001317", "·3板"), color="red"))

# 4) AI 眼镜（话题 4）
blocks.append(
    "**🕶️ AI 眼镜：Meta/Rokid 新品周期叠加**\n\n"
    "▸ 机构依据：Meta 拟 9 月 24 日 Connect 大会发布新一代 AI 眼镜并搭载 Muse 智能体能力；Rokid 二代同日数贸会全球首秀，全彩显示样机已下线；云栖大会千问新款 AI 眼镜亮相，支持拍摄与单绿显示；亚马逊称今年将部署 5000 台智能眼镜。\n"
    "▸ 机构逻辑：新品周期叠加技术突破，AI 眼镜向智能体交互与全彩显示进化，具备光学模组量产能力与镜片定制渠道的企业价值凸显。\n"
    "▸ 机构点名：" + names(("歌尔股份", "002241"), ("水晶光电", "002273"), ("博士眼镜", "300622"))
    + "（康耐特光学 港股）\n"
    "📎 盘面印证：光学光电 " + R("伟时电子(605218)") + " 封板，但机构点名的 AI 眼镜整机与显示光学票今日未动。")

# 5) AI 算力材料链（话题 5 先进封装/覆铜板/铜箔 + 话题 11 探针卡 + 话题 12 玻璃基板）
blocks.append(
    "**🔌 AI 算力材料链：先进封装 + 覆铜板 + 铜箔 + 玻璃基板**\n\n"
    "▸ 机构依据：瑞银将 2027 年底全球先进封装（CoWoS）总产能预期由 26 万片/月上调至 27 万片/月；摩根士丹利预计 2030 年覆铜板市场空间达 470 亿美元，M8 等级均价是 M7 两倍以上，超低轮廓铜箔（HVLP4）2027 年或现约 30% 供给缺口；探针卡平均针数由 3179 支增至 13297 支、均价由 23 万元升至 82 万元；玻璃基板方面台积电 2028 下半年量产面板级封装（CoPoS）。\n"
    "▸ 机构逻辑：AI 芯片向多元发展，通过先进封装、高端板材、铜箔与玻璃载板形成需求联动，具备技术认证与量产能力的厂商获超越行业增速的利润弹性。\n"
    "▸ 机构点名：" + names(("长电科技", "600584"), ("生益科技", "600183"), ("铜冠铜箔", "301217"),
                            ("德福科技", "301511"), ("帝尔激光", "300776"), ("凯盛科技", "600552"),
                            ("旗滨集团", "601636"), ("大族数控", "301200"))
    + "（盛合晶微/和林微纳/精智达/东威科技/芯碁微装/强一股份 688·无权限·ASMPT 港股）\n"
    "📎 盘面对照：元件涨停 3 家 = " + names(("贤丰控股", "002141"), ("澳弘电子", "605058"),
                                            ("协和电子", "605258"), color="red"))

# 6) 光互联与散热（话题 8 NPO + 话题 9 TIM/VC）
blocks.append(
    "**🌐 光互联与散热：NPO 电芯片 + 导热材料/均热板**\n\n"
    "▸ 机构依据：阿里云栖大会拟发布 6.4T NPO（近封装光学）方案，DSP 被弱化甚至取消，信号完整性压力转向 TIA、Driver 等高速模拟电芯片，整体价值量升至 2-3 倍；AI 芯片功耗提升带动导热界面材料（TIM）与均热板（VC）量价齐升，光模块向 1.6T 演进推升半导体制冷片（TEC）需求。\n"
    "▸ 机构逻辑：NPO 渗透使高速电芯片与硅光价值重新分配；散热从系统外围升级为核心瓶颈，材料供应商价值量迎来系统性重估。\n"
    "▸ 机构点名：" + names(("卓胜微", "300782"), ("金字火腿", "002515"), ("新易盛", "300502"),
                            ("光迅科技", "002281"), ("锐捷网络", "301165"), ("烽火通信", "600498"),
                            ("中际旭创", "300308"), ("立讯精密", "002475"), ("圣邦股份", "300661"),
                            ("中石科技", "300684"), ("思泉新材", "301489"), ("飞荣达", "300602"))
    + "（优迅股份/德邦科技/富信科技 688·无权限）\n"
    "📎 盘面印证：光模块/CPO 链今日资金净流出居前，机构点名票未封板；液冷服务器 " + R("宏英智能(001266)")
    + " 涨停，散热链局部呼应。")

# 7) 磷化铟衬底（话题 10）
blocks.append(
    "**💡 磷化铟衬底：AI 光互连核心材料需求 20 倍**\n\n"
    "▸ 机构依据：英伟达预测 2026-2030 年全球磷化铟晶圆整体需求将激增 20 倍；海外住友电工、AXT 等占据全球 90% 以上产能，上游铟资源伴生与出口管制、高纯红磷依赖海外，供给弹性弱；国内光智科技已获 6 英寸订单并加速验证。\n"
    "▸ 机构逻辑：磷化铟在光通信中难以替代，需求指数增长与上游原料、中游产能的刚性约束严重错配，具备高纯原料自给与大尺寸制造能力的企业抢占国产替代先机。\n"
    "▸ 机构点名：" + names(("光智科技", "300489"), ("云南锗业", "002428"), ("兴发集团", "600141"),
                            ("锡业股份", "000960"), ("株冶集团", "600961"), ("豫光金铅", "600531")) + "\n"
    "📎 盘面印证：有色金属与铟链今日未现涨停，机构点名票均未封板。")

# 8) 创新药出海 + 医药研发外包 + AI 制药（话题 13 + 话题 17）
blocks.append(
    "**💊 创新药出海 + 医药研发外包 + AI 制药**\n\n"
    "▸ 机构依据：26 上半年中国创新药对外授权超千亿美元；多肽等技术平台带动医药研发外包需求，临床研究端需求持续向上；AI 制药需求端翻倍增长，上游蛋白合成等环节订单翻倍、业绩已落地。\n"
    "▸ 机构逻辑：对外授权首付款与技术平台合作快速扩容，研发资金向早研与生产端高效传导，具备全链条能力与差异化壁垒的外包服务商进入订单密集转化期。\n"
    "▸ 机构点名：" + names(("百普赛斯", "301080"), ("义翘神州", "301047"), ("药明康德", "603259"),
                            ("凯莱英", "002821"), ("康龙化成", "300759"), ("泰格医药", "300347"),
                            ("昭衍新药", "603127"))
    + "（近岸蛋白/奥浦迈/纳微科技/百奥赛图/药康生物 688·无权限·药明生物/药明合联/金斯瑞生物科技 港股）\n"
    "📎 盘面对照：医药涨停 7 家 = " + names(("南华生物", "000504", "·4板"), ("百花医药", "600721", "·2板"),
                                              ("新华制药", "000756", "·2板"), ("哈药股份", "600664", "·2板"),
                                              ("华仁药业", "300110", "·20cm·2板"), ("五洲医疗", "301234", "·20cm"),
                                              ("千金药业", "600479"), color="red")
    + "（机构点名的义翘神州盘中触板后回落，收涨超 16%）")

# ── 🧭 零散小主线速览 ────────────────────────────────────────────────────
scan = (
    "**🧭 机构还看什么（一屏速览）**\n\n"
    "▸ 快递旺季联合涨价（反内卷）：" + names(("圆通速递", "600233"), ("申通快递", "002468"), ("韵达股份", "002120"))
    + "（中通快递 美股）—— 快递板块今日 -2.5%，盘面未动\n"
    "▸ 油运 VLCC 运价高位："
    + names(("招商轮船", "601872"), ("招商南油", "601975"), ("中远海能", "600026")) + " —— 今日未封板\n"
    "▸ AI 娱乐内容供给爆发（短剧/游戏/内容授权）："
    + names(("芒果超媒", "300413"), ("中文在线", "300364"))
    + "（心动公司/阅文集团/哔哩哔哩 港股·腾讯控股 港美上市）—— 传媒出版涨停 8 家 = "
    + names(("内蒙新华", "603230", "·5板"), ("新华文轩", "601811", "·3板"), ("新华传媒", "600825", "·2板"),
            ("华媒控股", "000607", "·2板"), ("中国出版", "601949"), ("天威视讯", "002238"),
            ("智度股份", "000676"), ("引力传媒", "603598"), color="red") + "\n"
    "▸ 染料涨价（金九银十 + 反内卷）："
    + names(("浙江龙盛", "600352"), ("闰土股份", "002440"), ("吉华集团", "603980")) + " —— 今日未动")

note = (
    "**📎 说明**\n\n"
    "▸ 红 = 今日已涨停；紫 = 机构点名个股今日未涨停（688·无权限与港股/港美上市不在 A 股可交易范围）。\n"
    "▸ 数据源：Gangtise 机构晚报 18 个话题 + 各话题点名个股 + 今日涨停池（沪深口径 " + str(HU_SHEN) + " 家）。\n"
    "▸ 对照名单逐只核验：红票均在涨停池内，紫票今日无一涨停。\n"
    "⚠️ 仅供信息参考，不构成投资建议，投资需谨慎。")

# ── 组装（div/hr）────────────────────────────────────────────────────────
elements = []
for i, md in enumerate(blocks):
    elements.append(div(None, md))
    if i < len(blocks) - 1:
        elements.append(HR)
elements.append(HR)
elements.append(div(None, scan))
elements.append(HR)
elements.append(div(None, note))

card = {
    "title": "🏛️ 机构晚报 09-22 | AI Agent 算力获密集看好，机器人量产在即",
    "color": "blue",
    "elements": elements,
}

# ── 质量门 ───────────────────────────────────────────────────────────────
errs = []
warns = []

# 1) div 结构硬判（09-22 事故根因）：必须有 text.tag=lark_md 且 content 为非空字符串
for i, e in enumerate(elements):
    tag = e.get("tag")
    if tag == "div":
        if "text" not in e:
            errs.append(f"elements[{i}] div 缺 text 包裹（飞书渲染整块空白）: keys={list(e.keys())}")
            continue
        t = e.get("text") or {}
        if t.get("tag") != "lark_md":
            errs.append(f"elements[{i}] div.text.tag 应为 lark_md: {t.get('tag')!r}")
        if not isinstance(t.get("content"), str) or not t.get("content").strip():
            errs.append(f"elements[{i}] div.text.content 必须是非空字符串")
    if "lark_md" in e:
        errs.append(f"elements[{i}] 非法扁平 lark_md（须包在 text.content 内）")
    if tag not in ("div", "hr"):
        errs.append(f"elements[{i}] 非预期 tag: {tag}")

# 2) 禁 hex / column_set / <font
raw = json.dumps(card, ensure_ascii=False)
for bad in ["column_set", "#", "<font"]:
    if bad in raw:
        errs.append(f"命中禁用 token: {bad}")

# 3) 黑话（完整 token 比对）
jargon = ["共振", "维度", "回流引擎", "背离引擎", "theme_rotation", "inflow_rising",
          "re-entry", "boom", "标的"]
for j in jargon:
    if j in raw:
        errs.append(f"命中黑话: {j}")

# 4) text_tag 配对
opens, closes = raw.count("<text_tag"), raw.count("</text_tag>")
if opens != closes:
    errs.append(f"text_tag 不配对 open={opens} close={closes}")

# 5) 红组 ⊆ 涨停池；紫组 ∉ 涨停池
def codes_of(color):
    out = set()
    for m in re.finditer(rf"<text_tag color='{color}'>(.*?)</text_tag>", raw):
        cm = re.search(r"\((\d{6})", m.group(1))
        if cm:
            out.add(cm.group(1))
    return out

red_codes, violet_codes = codes_of("red"), codes_of("violet")
red_bad = sorted(c for c in red_codes if c not in zt_codes)
violet_bad = sorted(c for c in violet_codes if c in zt_codes)
if red_bad:
    errs.append(f"红组不在涨停池: {[f'{zt_names.get(c, c)}({c})' for c in red_bad]}")
if violet_bad:
    errs.append(f"紫组误含涨停票: {[f'{zt_names.get(c, c)}({c})' for c in violet_bad]}")

# 6) 涨停池个股不得以明文出现（🛰 09-22 内蒙新华漏红事故）
red_spans = [(m.start(), m.end()) for m in re.finditer(r"<text_tag color='red'>.*?</text_tag>", raw)]
plain_hits = []
for nm, cd in zt_names.items():
    if nm == "凯华材料":  # 北交所·非沪深口径，说明块不提及
        continue
    for m in re.finditer(re.escape(nm), raw):
        if not any(a <= m.start() < b for a, b in red_spans):
            plain_hits.append(f"{nm}({cd})")
if plain_hits:
    errs.append(f"涨停个股未着红（明文出现）: {sorted(set(plain_hits))}")

# 7) A+H 公司不得被标注「港股」（🏛️-4：A+H 写 A 股代码）
AH_A_SHARE = {"三花智控", "大族数控", "中际旭创", "立讯精密", "圣邦股份", "药明康德",
              "凯莱英", "康龙化成", "泰格医药", "昭衍新药", "中远海能", "澜起科技",
              "广合科技", "蓝思科技", "芯碁微装"}
for m in re.finditer(r"（[^）]*）", raw):
    seg = m.group(0)
    if "港股" not in seg and "港美" not in seg:
        continue
    # 括号内按 · 分组（如「… 688·无权限·ASMPT 港股」），只检查落在港股组内的名字
    for sec in seg.strip("（）").split("·"):
        if "港股" not in sec and "港美" not in sec:
            continue
        for part in sec.split("/"):
            for nm in AH_A_SHARE:
                if nm in part:
                    errs.append(f"A+H 公司「{nm}」被归入港股组（须写 A 股代码）: {sec[:70]}")

# 8) 字符量
divs = [e for e in elements if e.get("tag") == "div"]
chars = sum(len(e["text"]["content"]) for e in divs)

# 9) 软提示
if "兑现" in raw:
    warns.append("用词「兑现」为行业通用词·非阻断")

print(f"[质检] 元素={len(elements)} div={len(divs)} hr={len(elements) - len(divs)} 字符={chars}")
print(f"[质检] 红组={len(red_codes)} 紫组={len(violet_codes)} 红不在池={red_bad} 紫在池={violet_bad} 漏红={sorted(set(plain_hits))}")
for w in warns:
    print("  ⚠️", w)
if errs:
    print("❌ 质量门未过:")
    for e in errs:
        print("  -", e)
    sys.exit(1)
print("✅ 质量门 PASS")

out = ROOT / "cache/gangtise_evening_research.json"
json.dump(card, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"✅ 已写 {out}")
