#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机构晚报速览卡 2026-09-23 拼装 + 质量门自检。
数据：Gangtise evening 1131（19 话题，with_related_securities） + zt_history/zt_2026-09-23.json（涨停池权威集）。
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

ROOT = Path("/Users/zxl/WorkBuddy/狸猫猎手")
ZT = json.load(open(ROOT / "cache/zt_history/zt_2026-09-23.json", encoding="utf-8"))
zt_codes = {x["code"] for x in ZT["zt_list"]}
zt_names = {x["name"] for x in ZT["zt_list"]}


def div(content: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def hr() -> dict:
    return {"tag": "hr"}


elements: list = []

# ── 数据线（研报时间 / 收盘数据时点）──
elements.append(div(
    "**📅 数据线：Gangtise 晚报编制日 2026-09-23（报告1131·盘后聚合·底层研报跨多日）｜ 盘面涨停截至 15:00 收盘**\n"
    "各主线块「📅 机构研报」为该块所引最新相关研报的发布时间（来源 Gangtise 卖方研报库，精确到分钟）。"
))
elements.append(hr())

# ── Block 1：AI硬件散热与先进封装材料链（金刚石散热 / 玻璃基板 / 引线框架）──
elements.append(div(
    "**🎯 AI硬件散热与先进封装材料链**\n\n"
    "📅 机构研报：华创证券《先进封装材料的增量逻辑：算力向上 材料先行》2026-09-22 08:24（玻璃基板另见华创 TCL科技 09-23 08:27）\n"
    "▸ 机构依据：工信部十五五规划推动金刚石等超宽禁带半导体产业化，首款金刚石散热GPU预计2027年推出；"
    "京东方玻璃基封装载板试验线拉通，拟2027上半年做量产决策；AI数据中心功率芯片拉动引线框架量价齐升。\n"
    "▸ 机构逻辑：AI芯片功耗跃升与产业规划双驱动，金刚石散热、玻璃基板、蚀刻引线框架进入从零到一的产业化拐点，"
    "头部厂商供应链验证导入节奏决定兑现时点。\n"
    "▸ 机构点名：<text_tag color='red'>沃格光电(603773)</text_tag>/<text_tag color='red'>康强电子(002119)</text_tag>/"
    "<text_tag color='red'>领先股份(603991)</text_tag>/<text_tag color='violet'>黄河旋风(600172)</text_tag>/"
    "<text_tag color='violet'>国机精工(002046)</text_tag>/<text_tag color='violet'>京东方A(000725)</text_tag>\n"
    "📎 盘面对照：玻璃基板涨停 1 家 = <text_tag color='red'>沃格光电(603773)</text_tag>；"
    "引线框架涨停 2 家 = <text_tag color='red'>康强电子(002119)</text_tag>/<text_tag color='red'>领先股份(603991)</text_tag>；"
    "金刚石散热方向核心票今日未涨停"
))
elements.append(hr())

# ── Block 2：AI算力互连与芯片链（交换芯片 / 光互联 / 内存 / PCIe）──
elements.append(div(
    "**⚡ AI算力互连与芯片链**\n\n"
    "📅 机构研报：天风证券《华为发布全球首个采用NPO的超节点，持续看好AI光互联》2026-09-23 17:07\n"
    "▸ 机构依据：有关部门审查国资数据中心对博通交换设备使用，交换芯片与高端射频国产替代从可选项变必选项；"
    "800G向1.6T迭代推升光互联驱动芯片与硅锗需求，全球12寸硅锗进入紧缺；AI瓶颈转向内存带宽，DDR5与HBM需求重估；"
    "超节点架构下PCIe交换芯片国产化加速。\n"
    "▸ 机构逻辑：博通供给刚性叠加超节点架构扩张，交换芯片、高端射频、光互联电芯片、内存接口、PCIe互连进入确定性放量窗口。\n"
    "▸ 机构点名：<text_tag color='red'>慧智微(688512·688)</text_tag>/<text_tag color='violet'>卓胜微(300782)</text_tag>/"
    "<text_tag color='violet'>中际旭创(300308)</text_tag>/<text_tag color='violet'>光迅科技(002281)</text_tag>/"
    "<text_tag color='violet'>锐捷网络(301165)</text_tag>\n"
    "📎 盘面对照：射频国产替代涨停 1 家 = <text_tag color='red'>慧智微(688512·688·20cm)</text_tag>；"
    "交换机方向涨停 1 家 = <text_tag color='red'>瑞斯康达(603803)</text_tag>；"
    "光互联、内存、PCIe机构核心票今日未涨停"
))
elements.append(hr())

# ── Block 3：量子计算容错突破 ──
elements.append(div(
    "**🔌 量子计算容错突破**\n\n"
    "📅 机构研报：国信证券《新质生产力之重点产业链系列：量子计算》2026-09-10 12:45（IonQ解码器为海外事件，A股专项研报集中在 09-09~09-10）\n"
    "▸ 机构依据：IonQ推出业界首个端到端实时量子纠错解码器，可在单颗标准商用CPU运行，经408个逻辑量子比特测试；"
    "英特尔CEO称GPU、CPU与量子计算将协同成为计算体系第三极。\n"
    "▸ 机构逻辑：实时纠错解码器突破降低容错量子的经典资源门槛，量子计算商业化临界点加速，"
    "看好量子硬件整机、纠错软件与抗量子密码环节。\n"
    "▸ 机构点名：<text_tag color='violet'>科大国创(300520)</text_tag>/<text_tag color='violet'>格尔软件(603232)</text_tag>/"
    "<text_tag color='violet'>国盾量子(688027·688)</text_tag>\n"
    "📎 盘面印证：量子计算方向机构点名票（国盾量子/科大国创/格尔软件）今日均未涨停"
))
elements.append(hr())

# ── Block 4：创新药（支柱产业 + WCLC数据 + 口服自免）──
elements.append(div(
    "**💊 创新药（支柱产业+WCLC数据+口服自免）**\n\n"
    "📅 机构研报：国信证券《A股生物医药行业2026半年报总结——创新药及产业链持续高景气》2026-09-22 22:53\n"
    "▸ 机构依据：中国支柱产业首次纳入生物医药，美国FDA启动IND滚动审评加速全球新药；"
    "WCLC会议康方依沃西单抗头对头优效于K药，两款B7-H3抗体偶联药获最高规格汇报；"
    "口服IL-23R环肽强生新药获批，翰森、海思科快速跟进。\n"
    "▸ 机构逻辑：创新药获制度红利与审评提效双重加持，PD-1/VEGF双抗、B7-H3抗体偶联药、口服自免环肽进入管线价值兑现期，"
    "拥有全球权益的龙头优先受益。\n"
    "▸ 机构点名：<text_tag color='violet'>药明康德(603259)</text_tag>/<text_tag color='violet'>海思科(002653)</text_tag>/"
    "<text_tag color='violet'>信立泰(002294)</text_tag>/<text_tag color='violet'>百利天恒(688506·688)</text_tag>\n"
    "📎 盘面对照：医药涨停 2 家 = <text_tag color='red'>浩欧博(688656·688·20cm)</text_tag>/"
    "<text_tag color='red'>济民健康(603222)</text_tag>（医疗器械，非创新药主线）；创新药机构核心票今日未涨停"
))
elements.append(hr())

# ── Block 5：CXO与AI制药卖水人 ──
elements.append(div(
    "**🧪 CXO与AI制药卖水人**\n\n"
    "📅 机构研报：国金证券《医药行业产业研究周报：跨界巨头竞速，CXO上游如箭在弦》2026-09-23 08:09\n"
    "▸ 机构依据：AI制药高景气拓宽研发漏斗，2026上半年全球投融资金额267.6亿美元同比增58.5%，海外CXO龙头上调业绩指引；"
    "AI设计分子海量增加，物理验证通量缺口拉大，湿实验验证拉动重组蛋白、试剂耗材需求激增。\n"
    "▸ 机构逻辑：AI制药从模型比拼进入拼闭环验证阶段，CXO及生命科学上游作为卖水人率先且确定性较强受益，"
    "蛋白表达、试剂耗材、临床前CRO增量明确。\n"
    "▸ 机构点名：<text_tag color='violet'>百普赛斯(301080)</text_tag>/<text_tag color='violet'>义翘神州(301047)</text_tag>/"
    "<text_tag color='violet'>药明康德(603259)</text_tag>/<text_tag color='violet'>昭衍新药(603127)</text_tag>\n"
    "📎 盘面印证：CXO与AI制药方向机构点名票今日均未涨停"
))
elements.append(hr())

# ── Block 6：电子测量测试设备（VNA+开关矩阵）──
elements.append(div(
    "**📡 电子测量测试设备（矢量网络分析仪+开关矩阵）**\n\n"
    "📅 机构研报：中泰证券《机械周报：持续看多光模块测试、人形机器人、液冷》2026-09-20 19:18（采样示波器测试仪器）\n"
    "▸ 机构依据：112Gbps信号需67GHz矢量网络分析仪，进口售价约150万元/套，是德科技份额70%至80%且交付排至一年以上；"
    "AI服务器与光模块测试升级，开关矩阵将测试效率提升5至20倍、降低硬件采购成本。\n"
    "▸ 机构逻辑：高速互联从抽检改全检叠加海外龙头交付瓶颈，掌握高频自研芯片的本土测试设备商迎来国产替代窗口，"
    "矢量网络分析仪与开关矩阵需求同步放量。\n"
    "▸ 机构点名：<text_tag color='red'>普源精电(688337·688)</text_tag>/<text_tag color='red'>优利德(688628·688)</text_tag>/"
    "<text_tag color='violet'>电科思仪(301689)</text_tag>/<text_tag color='violet'>鼎阳科技(688112·688)</text_tag>\n"
    "📎 盘面对照：测量仪器涨停 2 家 = <text_tag color='red'>普源精电(688337·688·20cm)</text_tag>/"
    "<text_tag color='red'>优利德(688628·688·20cm)</text_tag>（均科创板，无权限）"
))
elements.append(hr())

# ── Block 7：网络安全（AI驱动估值低位）──
elements.append(div(
    "**🔒 网络安全（AI驱动估值低位）**\n\n"
    "📅 机构研报：中银证券《TMT行业周报：加息落地，网安风起，重视科技反弹》2026-09-23 08:48\n"
    "▸ 机构依据：AI降低攻击成本并新增虚假信息欺诈风险，企业侧网安采购需求明确提振，海外头部厂商防火墙收入增速达30%至40%；"
    "国内头部网安厂商2027年估值仅3至5倍市销率。\n"
    "▸ 机构逻辑：AI重塑网络安全需求，海外龙头业绩与估值已兑现，国内厂商估值与海外反差突出，"
    "政策与事件催化下板块关注度有望提升。\n"
    "▸ 机构点名：<text_tag color='violet'>深信服(300454)</text_tag>/<text_tag color='violet'>启明星辰(002439)</text_tag>/"
    "<text_tag color='violet'>天融信(002212)</text_tag>/<text_tag color='violet'>中新赛克(002912)</text_tag>\n"
    "📎 盘面印证：网络安全方向机构点名票今日均未涨停"
))
elements.append(hr())

# ── Block 8：化工与原料涨价（TDI+牛磺酸）──
elements.append(div(
    "**🧪 化工与原料涨价（TDI+牛磺酸）**\n\n"
    "📅 机构研报：中信建投《8月化工出口数据分析》2026-09-23 16:30（提及TDI；巴斯夫事故无专项深度研报命中）\n"
    "▸ 机构依据：巴斯夫上海漕泾基地事故停工，TDI产能16万吨/年占国内约8%，价格上行，万华权益产能147万吨、"
    "价涨对应年化利润增厚9至18亿元；牛磺酸龙头10月检修供给收缩，价格自22.5元/千克上调至32.5元/千克，行业集中度超90%。\n"
    "▸ 机构逻辑：TDI供给扰动与牛磺酸龙头检修，在供给收缩、需求稳健、寡头格局三重因素叠加下，价格具备向上弹性，"
    "一体化与龙头标的直接受益。\n"
    "▸ 机构点名：<text_tag color='red'>沧州大化(600230)</text_tag>/<text_tag color='violet'>万华化学(600309)</text_tag>/"
    "<text_tag color='violet'>永安药业(002365)</text_tag>/<text_tag color='violet'>新和成(002001)</text_tag>\n"
    "📎 盘面对照：TDI涨停 1 家 = <text_tag color='red'>沧州大化(600230)</text_tag>；"
    "牛磺酸机构点名票（永安药业等）今日未涨停"
))
elements.append(hr())

# ── 🧭 速览 ──
elements.append(div(
    "**🧭 机构还看什么（一屏速览）**\n\n"
    "▸ 黄酒高端化：会稽山(601579)/古越龙山(600059) 渠道扩容、股价高位（今日未涨停）\n"
    "▸ 大炼化价差修复：恒逸石化(000703)/荣盛石化(002493)/恒力石化(600346) 受益柴油出口收缩与化工品价差扩张（今日未涨停）"
))
elements.append(hr())

# ── 📎 说明 ──
elements.append(div(
    "**📎 说明**\n\n"
    "红=今日已涨停（沪深）；紫=机构核心票未涨停。数据源=Gangtise晚报(编制日 2026-09-23 报告1131)+核心标的+收盘涨停池。"
    "各块「📅 机构研报」时间取自 Gangtise 卖方研报库（文件时间，精确到分钟），为底层研报真实发布钟点，非晚报编制日。"
    "盘面印证以今日收盘涨停池为准。仅供信息参考，不构成投资建议，投资需谨慎。"
))

card = {
    "title": "🏛️ 机构晚报 09-23 | AI硬件材料链机构密集覆盖·测试仪器批量涨停",
    "color": "blue",
    "elements": elements,
}

# ───────────────────────── 质量门 ─────────────────────────
errs, warns = [], []

# 1) div 结构：每个 div 必有 text.lark_md 且 content 为非空字符串；顶层禁扁平 lark_md
for i, el in enumerate(elements):
    if el.get("tag") == "div":
        t = el.get("text")
        if not isinstance(t, dict) or t.get("tag") != "lark_md" or not isinstance(t.get("content"), str) or not t["content"].strip():
            errs.append(f"[div结构] elements[{i}] div 缺 text.lark_md 或 content 非字符串")
    if "lark_md" in el and el.get("tag") != "div":
        errs.append(f"[div结构] elements[{i}] 顶层出现扁平 lark_md 键")

# 2) 提取 red / violet 个股并校验 ∈ / ∉ zt 池
name_code = re.compile(r"([一-龥A-Za-z·]+)\((\d{6})")
red_re = re.compile(r"<text_tag color='red'>(.*?)</text_tag>", re.S)
violet_re = re.compile(r"<text_tag color='violet'>(.*?)</text_tag>", re.S)

red_pairs, violet_pairs = [], []
div_contents = [e["text"]["content"] for e in elements if e.get("tag") == "div"]
for content in div_contents:
    for tag_content in red_re.findall(content):
        for nm, cd in name_code.findall(tag_content):
            red_pairs.append((nm, cd))
    for tag_content in violet_re.findall(content):
        for nm, cd in name_code.findall(tag_content):
            violet_pairs.append((nm, cd))

for nm, cd in red_pairs:
    if cd not in zt_codes:
        errs.append(f"[漏红/红组非法] {nm}({cd}) 不在今日涨停池")
for nm, cd in violet_pairs:
    if cd in zt_codes:
        errs.append(f"[紫组误红] {nm}({cd}) 在涨停池却用 violet")

# 3) 禁 column_set / hex / <font
blob = json.dumps(card, ensure_ascii=False)
if "column_set" in blob:
    errs.append("[版式] 出现 column_set")
if re.search(r"#[0-9a-fA-F]{6}", blob):
    errs.append("[版式] 出现 hex 颜色")
if "<font" in blob:
    errs.append("[版式] 出现 <font")

# 4) 黑话门（完整 token）
jargon = ["共振", "维度", "回流引擎", "theme_rotation", "inflow_rising", "boom", "re-entry", "背离引擎"]
for j in jargon:
    if j in blob:
        errs.append(f"[黑话] 出现禁用词「{j}」")

# 5) content 必须字符串（已在 div 构造保证，复检）
for el in elements:
    if el.get("tag") == "div" and not isinstance(el["text"]["content"], str):
        errs.append("[content] div content 非字符串")

# 6) A+H 误标：violet/red 内括号标「港股」且含 A 股可交易名——本卡未用港股代码，跳过

print(f"== 质量门 ==")
print(f"div 块数: {sum(1 for e in elements if e.get('tag')=='div')} | 总 elements: {len(elements)}")
print(f"红组个股: {len(red_pairs)} | 紫组个股: {len(violet_pairs)}")
print(f"涨停池家数(沪深): {len(zt_codes)}")
if warns:
    for w in warns:
        print(f"[warn] {w}")
if errs:
    print("❌ 质量门未通过：")
    for e in errs:
        print(f"  - {e}")
    sys.exit(1)
print("✅ 质量门全部通过（div结构 / 红⊆zt / 紫∉zt / 无column_set·hex·font / 无黑话 / content字符串）")

out = ROOT / "cache/gangtise_evening_research.json"
json.dump(card, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"✅ 已写入 {out}")
