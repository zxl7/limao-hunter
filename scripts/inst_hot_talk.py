#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机构在聊什么 · 给热议榜上的票反查机构原文观点

缘起（2026-10-29 用户「只看到他们在聊，没有看到聊什么呀？讨论了什么，什么消息，
什么资讯，什么逻辑推化，不要干巴巴地放个股」）：
  `hot-topics` 热议榜只有 rank + hotCount，**不含任何文字**。
  所以「谁被聊」有了，「为什么被聊」必须另一条路取。

三条路实测（2026-10-29）：
  · 券商点评 list → content 被截到 100 字，且混入大量「日报/收盘/涨幅榜」泛内容，噪音高
  · 机构研报 list → 条数太少（中际旭创 10-15~10-29 只有 1 条），summary 还常为空
  · 投研检索 recall → ✅ 返回完整正文段落 + 机构 + 时间 + 标题，含票名命中率高（实测
    16/19、18/31、12/16），且能看出机构分歧 —— 采用这一条

本脚本只做「抓原文 + 机械抽取」，不写候选项、不改评分/排序/名额/仓位/账本。
一句话观点直接用机构自己的标题（标题本身就是结论），不靠 AI 编。

⛔ 边界：
  - 只在 chunk 里真的出现该股票名时才算命中（recall 是语义检索，会漂）
  - 剔除「日报/收盘/涨幅榜/早报」类泛内容
  - 单票单独缓存，重跑不重复花钱；缺数据留 null 记 warnings

输出：cache/inst_hot_talk_<date>.json（schema=inst_hot_talk_v1）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"
TALK_DIR = CACHE / "inst_hot_talk"
AP_CLI = Path.home() / ".workbuddy/skills/alphapai-research/scripts/alphapai_client.py"

SCHEMA = "inst_hot_talk_v1"
# 两次检索之间的间隔（秒）。实测限流约 19 次/30 秒，1.5 秒已很安全
SLEEP_BETWEEN = 1.8
# 单票最多保留几条观点（要看出分歧，2 条不够——实测每票有 5~9 篇千字研报）
MAX_ITEMS = 4
# 单条摘要最大字数（原 130 字只够念一句财报，撑不起逻辑链）
SNIPPET_MAX = 240

# 泛内容标题：不是针对个股的观点，跳过
_NOISE_TITLE = (
    "日报", "收盘", "涨幅榜", "早报", "晨会", "盘前", "盘后", "龙虎榜",
    "异动", "涨停复盘", "市场综述", "周观点", "月报", "月度", "金股",
    "策略报告", "投资策略", "组合报告", "周报", "速览", "持仓", "复盘表",
    "初识", "扫盲", "科普", "入门",
    # 聚合型内容：一篇里拼好几家公司好几个行业，抽出来的句子必然串味
    # （实测银河「晨报」把三环集团和乳制品、煤炭混在一段）
    "晨报", "一览", "集萃", "系列", "专题汇总",
)


def _run_recall(name: str, start: str, end: str) -> list[dict]:
    """调 CLI 检索。返回原始 rows。"""
    cmd = [
        sys.executable, str(AP_CLI), "recall",
        "-q", f"{name} 机构最新观点 核心逻辑 催化",
        "--start", start, "--end", end,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"recall 失败 rc={proc.returncode}: {proc.stderr[:200]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"recall 返回非 JSON: {e} / {proc.stdout[:150]}")
    if payload.get("code") != 200000:
        raise RuntimeError(f"recall 业务错误 code={payload.get('code')}")
    return payload.get("data") or []


def _clean(text: str) -> str:
    """压平空白。⚠️ 换行要变句号，不能变空格。

    研报 chunk 里的换行就是段落/小标题边界，压成空格会让小标题黏在正文前面，
    读起来像「MLCC周期强势 1、26Q1-3营收115亿」「毛利率主受海外收入占比影响
    单26Q3，公司实现…」。换成句号后切句逻辑才能正确断开。
    """
    text = re.sub(r"\r\n?", "\n", str(text or ""))
    text = re.sub(r"\n+", "。", text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    # ⚠️ 只去空格不要去句号：chunk 末尾的句号是句界，strip 掉会让相邻 chunk 黏成一句
    #    （实测「0.082元/Wh核心驱动并非终端需求崩塌」）
    return text.strip()


# 券商原文里的元信息句（日期、评级、报告类型），不是逻辑，抽摘要时要剔
_META_SENT = re.compile(
    r"证券研究报告|维持评级|投资评级|公司点评|业绩简评|业绩概览|业绩回顾|"
    r"买入（|增持（|维持.{0,4}评级|^20\d\d年\d{1,2}月\d{1,2}日"
)
# 研报里的图表/数据源说明，整句无信息量，无条件剔
_CHART_SENT = re.compile(r"图表标题|图表内容|图表\s*\d|资料来源|数据来源|注：")
# 散户炒股感言 / 直播开场白：不是机构在聊的内容
_NOISE_BODY = re.compile(
    r"持股体验|美滋滋|年化收益率|成本为负|超级大牛股|发财|梭哈|"
    r"假如.{0,10}买入|持有到现在|股变成|送股很多|复权收益|传家宝|打死不卖|满仓干|割肉|"
    r"各位上午好|各位下午好|欢迎来到|我是.{0,20}(分析师|行业)|今天的.{0,4}直播|讨论三个主题|"
    r"会聊一下|路演的一个反馈|下面请|接下来由|由.{0,8}首席|disclaimer|免责声明|例行需要|"
    r"本次会议|不对媒体开放|仅供.{0,6}参考|未经.{0,4}许可|敬请退出|来自媒体|"
    r"另外提示|随时在屏幕|发送您的问题|会议的过程中|回到主题|"
    r"分析师股票评级|未来 12-18 个月|预期相对基准指数|FINRA|美国主要机构投资者|"
    # 研报模板废话（中泰「建议重点关注」+ 各家「风险提示」）
    r"建议重点关注|风险提示|风险因素|相关风险|"
    # 英文研报的章节标题与免责（摩根士丹利 3Q26 Key Takeaways 里全是这些）
    r"Valuation Methodology|Key [Rr]isks?|[Dd]ownside [Rr]isks?|[Uu]pside [Rr]isks?|"
    r"[Dd]isclosures?|Important [Dd]isclosure|Regulation [A-Z]|For the exclusive use|"
    # 中文免责声明（海通国际那段「以美元以外的其他货币计价…正面或负面影响」）
    r"本研究报告中|任何证券或相关金融工具|正面或负面影响|投资或收益的价值|"
    r"本报告.{0,10}(?:仅供参考|不构成)|不构成.{0,8}建议|"
    # 反问式标题党（实测「下一个隆基还是一个黄金坑」被抽成摘要）
    r"下一个.{0,8}还是|还是一个.{0,4}坑|是不是.{0,6}顶"
)
# 业务词：命中越多，越可能是真正在讲逻辑，排序时给权重
_BIZ_WORD = re.compile(
    r"营收|净利|毛利|订单|产能|份额|价格|涨价|同比|环比|出货|招标|中标|"
    r"目标价|评级|估值|扩产|在手|装机|渗透率|市占|指引|预期"
)
# 结论句：机构的态度（维持/上调/下调/看好/见底/承压）。这是「他们在争论什么」的正身
_CONCL_SENT = re.compile(
    r"维持(?:买入|增持|中性|优于|跑赢|推荐)|上调|下调|首予|首次覆盖|"
    r"我们(?:认为|判断|预计|维持|看好)|看好|看淡|承压|见底|筑底|底部|"
    r"拐点|修复|超预期|不及预期|低于预期|高于预期|跑赢|跑输|推荐|建议关注"
)
# 原因句：把结论和数据连起来的因果链，缺了它就只是念报表
_REASON_SENT = re.compile(
    r"主要(?:系|由于|来自|原因)|由于|因为|受.{0,14}(?:影响|拖累|带动)|驱动|"
    r"得益于|带动|原因在于|反映|意味着|说明"
)
# 真机构（券商研究所 / 外资行）。自媒体公众号（梦想大作手、小小的渔夫、
# 猫哥读研报…）也能出好内容，但整体信噪比低，排序时不给加成。
_REAL_INST = re.compile(
    r"证券|中金|中信|华泰|国泰|君安|招商|申万|宏源|广发|海通|兴业|光大|民生|东方|"
    r"长江|天风|中泰|华创|国信|东吴|浙商|方正|华西|国金|西部|开源|华源|华福|中银|"
    r"银河|财通|东兴|国海|太平洋|山西|华安|信达|首创|中航|中邮|湘财|国元|东北|华宝|"
    r"国盛|华鑫|德邦|红塔|川财|华金|交银|工银|建银|星展|大和|瑞银|汇丰|巴克莱|麦格理|"
    r"高盛|花旗|野村|摩根|Bernstein|Goldman|Morgan|Citi|UBS|Nomura|HSBC|CLSA|"
    r"JPMorgan|Jefferies|Macquarie|Barclays|BofA|Deutsche|Daiwa|CICC|CITIC|CLSA",
    re.I,
)
_NUM_TOKEN = re.compile(r"\d{2,}(?:\.\d+)?")
# 英文研报的结论句。外资行原文写 maintain Buy / TP cut to / 90% upside，
# 中文正则一个都认不出，实测高盛那条只抽到「商用车换电」这种边缘段落，漏掉了目标价。
_EN_CONCL = re.compile(
    r"\b(?:maintain|reiterate|retain|keep)\s+(?:a\s+|our\s+)?"
    r"(?:Buy|Neutral|Sell|Outperform|Underperform|Overweight|Underweight)"
    r"|\bbottom line\b|\bwe think\b|\bin our view\b|\bwe believe\b|\bwe expect\b"
    r"|\bTP\s+(?:cut|raise[d]?|of|to)\b|\btarget price\b|\bupside\b"
    r"|\binvestment conclusion\b|\bvaluation\b",
    re.I,
)

# 中英对照别名。英文研报只写 CATL / Luxshare，不含中文名，
# 原先只认中文名 → 实测把宁德时代 10 条外资行研报全杀了（含 Bernstein 6455 字、
# 高盛目标价、野村 408 元），卡面只剩中文快评，这就是「质量差」的主因之一。
# 可用 config/inst_stock_alias.json 追加，不必改代码。
_ALIAS: dict[str, tuple[str, ...]] = {
    "宁德时代": ("CATL", "Contemporary Amperex"),
    "立讯精密": ("Luxshare",),
    "中际旭创": ("Innolight",),
    "三环集团": ("CCTC", "Chaozhou Three-circle", "Three-circle"),
    "兆易创新": ("GigaDevice",),
    "大金重工": ("Dajin Heavy", "Dajin"),
    "英维克": ("Envicool",),
    "比亚迪": ("BYD",),
    "隆基绿能": ("LONGi",),
    "阳光电源": ("Sungrow",),
    "亿纬锂能": ("EVE Energy",),
    "天赐材料": ("Tinci",),
    "恩捷股份": ("Semcorp",),
    "汇川技术": ("Inovance",),
    "京东方A": ("BOE Technology",),
    "北方华创": ("NAURA",),
    "中微公司": ("AMEC",),
    "澜起科技": ("Montage Technology",),
    "韦尔股份": ("Will Semiconductor",),
    "工业富联": ("Foxconn Industrial",),
    "沪电股份": ("WUS Printed Circuit",),
    "生益科技": ("Shengyi Technology",),
    "深南电路": ("Shennan Circuits",),
    "鹏鼎控股": ("Avary",),
    "歌尔股份": ("Goertek",),
    "三安光电": ("Sanan Optoelectronics",),
    "美的集团": ("Midea Group",),
    "格力电器": ("Gree Electric",),
}


def _alias_for(name: str) -> tuple[str, ...]:
    """别名查表；外部 JSON 优先，方便临时补票而不动代码。"""
    p = ROOT / "config" / "inst_stock_alias.json"
    if p.exists():
        try:
            ext = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(ext, dict) and ext.get(name):
                return tuple(ext[name]) + _ALIAS.get(name, ())
        except Exception:  # noqa: BLE001
            pass
    return _ALIAS.get(name, ())


def _hit_kind(title: str, body: str, name: str, code: str | None) -> str | None:
    """这条结果是不是真的在讲这只票。返回 strong / ok / None。

    strong = 标题直接点了中文名或英文别名
    ok     = 标题或正文出现股票代码（外资行标题爱写 CATL (300750.SZ)）
    """
    six = (code or "").split(".")[0]
    head = title
    if name in head or any(a.lower() in head.lower() for a in _alias_for(name)):
        return "strong"
    if six and six in head:
        return "ok"
    if six and six in body[:1500]:
        return "ok"
    if any(a.lower() in body[:600].lower() for a in _alias_for(name)):
        return "ok"
    return None


def _numkey(text: str) -> set[str]:
    """提取有辨识度的数字（排除 20xx 年份），用于判断两条是不是同一篇内容。"""
    nums = set(_NUM_TOKEN.findall(str(text or "")))
    return {n for n in nums if not re.fullmatch(r"20\d\d", n)}


def _strip_html(text: str) -> str:
    """研报 chunk 里常整段嵌 HTML 表格（高盛/花旗尤其多），先压成纯文本再说。

    表格里的数字对「结论」没有帮助，且会把摘要撑成全角标点乱码，
    所以整块 <table> 直接丢弃，只留表格外的叙述。
    """
    text = re.sub(r"<table\b.*?</table>", "。", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]{1,80}>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _snippet(title: str, chunks: list[str], name: str | None = None,
             code: str | None = None) -> str:
    """抽一段能说明「逻辑」的话：结论 + 数据 + 原因，而不是随手一句带数字的。

    ⚠️ 2026-10-29 用户「感觉质量很差」后重写：原版只抽「一句带数字的句子」，
    结果把 6000 字的 Bernstein 研报压成一句财报复述，还把「下一个隆基还是一
    个黄金坑」这种标题党选中。现在改成按语义角色拼：
      ① 结论句（维持/上调/下调/看好/见底/承压…）—— 机构的态度
      ② 数据句（营收/净利/毛利/份额…带数字）—— 凭什么这么说
      ③ 原因句（主要系/由于/受…影响/驱动）—— 逻辑链条
    三者拼起来才是「逻辑推演」，缺哪段就少哪段，不编。
    """
    body = " ".join(_strip_html(_clean(c)) for c in chunks if c)
    if not body:
        return ""
    # 去掉正文开头与标题重复的部分（机构原文常有「标题-正文」结构，标题可能带括号后缀）
    head = _clean(title)
    if head:
        for cut in (head, head[:24], head[:16]):
            if cut and body.startswith(cut):
                body = body[len(cut):].lstrip("-—· ：:，,.")
                break
    # 研报正文里的小节标题（「业绩概览：」「核心观点：」）改写成断句点，避免黏在摘要里
    body = re.sub(r"(业绩概览|业绩简评|核心观点|投资逻辑|业绩回顾)\s*[：:]", "。", body)
    # 「风险提示：A、B、C」是免责段不是观点；整段删掉，按句切会残留半句
    # （实测剩「储能需求不及预期，海外竞争加剧等」黏到下一句开头）
    body = re.sub(r"风险(?:提示|因素)[：:][^。]{0,140}", "。", body)
    # 切句：中文句读 + 英文句点（仅当后面跟空格，避免切断 43.63 这种小数）
    # 用后视断言保留句末标点，否则拼回去会变成两句黏连
    parts = [
        p.strip() for p in
        re.split(r"(?<=[。；;！!？?])|(?<=\.)(?=\s)", body) if p.strip()
    ]
    parts = [
        p for p in parts
        if not _CHART_SENT.search(p)
        and not _NOISE_BODY.search(p)
        and not (_META_SENT.search(p) and len(p) < 34)
    ]
    if name:
        # ⛔ 跑题句：同一篇研报的 chunk 会把别的行业段落也拼进来。
        #   实测银河证券的三环集团报告里混着「国内深加工乳制品市场规模 690 亿」
        #   和「山西煤矿瓦斯爆炸导致煤价上涨」。
        #   判据：①点了本票名/别名/代码 ②含经营指标词 ③不含与本票无关的行业词
        parts = [p for p in parts
                 if (_self_evident(p, name, code) or _BIZ_WORD.search(p))
                 and not _cross_industry(p, title)] or parts
    if not parts:
        return body[:SNIPPET_MAX]
    num = re.compile(r"\d+(?:\.\d+)?\s*(?:%|亿|万|GW|GW\+|元|倍|条|台|片|吨|pp|pct)")

    def role_of(sent: str) -> int:
        """0=结论 1=数据依据 2=原因 3=其他。数字优先于业务词，避免抽到空话。"""
        if _CONCL_SENT.search(sent) or _EN_CONCL.search(sent):
            return 0
        if num.search(sent) and _BIZ_WORD.search(sent):
            return 1
        if _REASON_SENT.search(sent):
            return 2
        return 3

    ranked = sorted(
        enumerate(parts),
        key=lambda kv: (role_of(kv[1]), 0 if num.search(kv[1]) else 1,
                        0 if _BIZ_WORD.search(kv[1]) else 1, kv[0]),
    )
    picked: list[str] = []
    used_role: set[int] = set()
    total = 0
    for _, sent in ranked:
        role = role_of(sent)
        if role in used_role and picked:
            continue                       # 同一角色只取一句，避免两句都在报数字
        if picked and total + len(sent) > SNIPPET_MAX:
            continue
        picked.append(sent)
        used_role.add(role)
        total += len(sent)
        if total >= SNIPPET_MAX * 0.7:
            break
    out = ""
    for sent in picked or [parts[0]]:
        # 英文句子拼接要补空格，否则变成「...p.a.The key reason」
        if out and out[-1].isascii() and sent[:1].isascii():
            out += " "
        out += sent
    # 切句后常残留前导标点/连接词
    out = re.sub(r"^[，,、；;：:。\s—\-]+", "", out)
    # 「旺盛，。」这类双标点（原句逗号后直接接段落边界）
    out = re.sub(r"[，,、]\s*。", "。", out)
    if len(out) > SNIPPET_MAX:
        # 按句号截，别切在半句话上（花旗那条曾被切成「('Contracts …」）
        cut = max(out.rfind("。", 0, SNIPPET_MAX), out.rfind("；", 0, SNIPPET_MAX),
                  out.rfind(". ", 0, SNIPPET_MAX))
        out = out[:cut + 1].rstrip("，、；") if cut > SNIPPET_MAX * 0.5 else \
            out[:SNIPPET_MAX].rstrip("，、；") + "…"
    return out


def _first_evident_sentence(body: str, name: str, code: str | None) -> str:
    """从正文里找第一句能自证「在讲这只票」的句子，用作摘要的前缀。

    英文研报的摘要常常只写 CATL，中文名一个没有，读者无法确认讲的是谁；
    补一句带名的原文即可，不改动机构原话。
    """
    for sent in re.split(r"(?<=[。；;！!？?])|(?<=\.)(?=\s)", body):
        s = _clean(sent)
        if 8 <= len(s) <= 120 and _self_evident(s, name, code):
            return s
    return ""


# 跨行业串味兜底。只有当本票标题里也不含该词时才剔，避免把煤炭股的煤炭句杀掉。
_OTHER_INDUSTRY = (
    "煤炭", "焦煤", "煤价", "煤矿", "乳制品", "乳业", "白酒", "啤酒", "养殖", "生猪",
    "水泥", "钢材", "房地产", "银行", "保险股", "港口", "航运", "机场", "航空",
    "旅游", "酒店", "餐饮", "纺织", "服装", "造纸", "农药", "化肥", "种业",
)


def _cross_industry(sent: str, title: str) -> bool:
    """这句话是不是在讲别的行业（本票标题里压根没这个行业）。"""
    return any(w in sent and w not in title for w in _OTHER_INDUSTRY)


def _info_score(it: dict) -> int:
    """这条观点的信息量。分越高越靠前。

    只看署名会翻车：券商直播闲聊也带署名，讲清 driver 的深度报告反被挤掉。
    所以按「有没有因果 + 有没有硬数字 + 是不是真机构」打分。
    """
    text = f"{it['title']} {it['snippet']}"
    s = 0
    if _REASON_SENT.search(text):
        s += 3                                  # 有因果链，这才是逻辑
    if _EN_CONCL.search(text) or _CONCL_SENT.search(text):
        s += 2                                  # 有明确表态
    hard = re.findall(r"\d+(?:\.\d+)?\s*(?:%|亿|万|GW|GWh|元|倍|Wh|pp|pct)", text)
    s += min(len(hard), 3)                      # 硬数字，最多算 3 分
    if _REAL_INST.search(it["institution"] or ""):
        s += 2                                  # 真机构（券商/外资行），自媒体不加
    if it.get("hit") == "strong":
        s += 1                                  # 标题直接点名，不是靠代码蹭的
    if len(it["snippet"]) >= 100:
        s += 1                                  # 太短的多数是残句
    # 会议纪要的语音转录：填充词成串、数字重复（「17.7和17.7%会上升到19.7，19.7%」），
    # 读起来像流水账，信息密度远低于书面研报 → 降权，不删（原始观点仍有价值）
    if len(re.findall(r"就是|这个|然后|其实|比如说|这样的一个|的话|呢？|对吧", text)) >= 4:
        s -= 3
    if "答：" in text or text.startswith("问："):
        s -= 2                                  # 问答体，摘出来常常没有主语
    return s


def _self_evident(text: str, name: str, code: str | None) -> bool:
    """这段文字自己能不能证明「在讲这只票」。证明不了就宁缺，不硬凑。"""
    six = (code or "").split(".")[0]
    if name in text:
        return True
    if six and six in text:
        return True
    low = text.lower()
    return any(a.lower() in low for a in _alias_for(name))


def pick_items(rows: list[dict], name: str, max_items: int = MAX_ITEMS,
               code: str | None = None) -> list[dict]:
    """从 recall 结果里挑出真的讲到这只票的观点。

    排序：有机构署名的排前面（可信度与立场可追溯），未署名的会议纪要在后；
    同组内保留 recall 原本的相关性顺序。
    """
    out: list[dict] = []
    seen_title: set[str] = set()
    for r in rows:
        chunks = [_strip_html(_clean(c)) for c in (r.get("chunks") or [])]
        if not chunks:
            continue
        title = _clean(r.get("title"))
        if not title or title in seen_title:
            continue
        if any(w in title for w in _NOISE_TITLE):
            continue
        body = " ".join(chunks)
        kind = _hit_kind(title, body, name, code)
        if not kind:
            continue                      # ⛔ 没真提到这只票，不要
        snippet = _snippet(title, chunks, name, code)
        # ⛔ 反漂移：标题或摘要合起来必须能证明在讲这只票。
        #   ⚠️ 只看摘要会误杀——中文研报摘要常写「公司」，名字只在标题里
        #   （实测立讯精密 6 条真调研纪要因此全被丢，只剩散户帖）。
        if not _self_evident(title + snippet, name, code):
            alt = _first_evident_sentence(body, name, code)
            if not alt:
                continue                  # 抽不到能自证的句子，宁可不放
            snippet = (alt + snippet)[:SNIPPET_MAX]
        seen_title.add(title)
        out.append({
            "institution": _clean(r.get("institution")) or None,
            "date": (r.get("time") or "")[:10] or None,
            "title": title[:70],
            "snippet": snippet,
            "hit": kind,
            "type": r.get("type"),
        })
    # 排序按「这条能不能看出逻辑」打分，不再只看有没有机构署名。
    # ⚠️ 原版按署名排序，结果摩根士丹利的直播闲谈（「下一个隆基还是黄金坑」）
    #    压过了伯恩斯坦讲清 driver 的那条，用户直接判定「质量很差」。
    out.sort(key=lambda x: (-_info_score(x), x["institution"] or "~"))
    # 同票去重：数字重合 ≥2 视为同一篇内容的转述（实测两个公众号会转述同一份高盛研报）
    dedup: list[dict] = []
    for it in out:
        k = _numkey(it["snippet"]) | _numkey(it["title"])
        if any(len(k & (_numkey(d["snippet"]) | _numkey(d["title"]))) >= 2 for d in dedup):
            continue
        dedup.append(it)
    return dedup[:max_items]


def fetch_one(code: str, name: str, card_date: str, start: str, end: str,
              refresh: bool = False) -> list[dict]:
    """单票抓取，带单票缓存。

    ⚠️ 缓存存**原始召回结果**（raw），读取时才做抽取。
    这样调整摘要/去重规则不需要重新调用接口，不重复花时间也不撞限流。
    """
    TALK_DIR.mkdir(parents=True, exist_ok=True)
    cache = TALK_DIR / f"{card_date}_{code.split('.')[0]}.json"
    if cache.exists() and not refresh:
        try:
            rows = json.loads(cache.read_text(encoding="utf-8")).get("raw")
            if rows is not None:
                return pick_items(rows, name, code=code)
        except Exception:  # noqa: BLE001
            pass
    rows = _run_recall(name, start, end)
    cache.write_text(json.dumps({
        "code": code, "name": name, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "raw_count": len(rows), "raw": rows,
    }, ensure_ascii=False), encoding="utf-8")
    return pick_items(rows, name, code=code)


def build(card_date: str, targets: list[tuple[str, str]], start: str, end: str,
          refresh: bool = False) -> dict:
    """targets = [(code, name)]。"""
    stocks: dict[str, dict] = {}
    warns: list[str] = []
    for i, (code, name) in enumerate(targets):
        # 命中单票缓存时不发请求，也就不需要等限流间隔
        cached = (TALK_DIR / f"{card_date}_{code.split('.')[0]}.json").exists() and not refresh
        try:
            items = fetch_one(code, name, card_date, start, end, refresh=refresh)
        except Exception as e:  # noqa: BLE001
            warns.append(f"{name}({code}) 抓取失败：{e}")
            items = []
        stocks[code] = {"name": name, "items": items}
        print(f"[talk] {name}({code}) → {len(items)} 条观点{'（缓存）' if cached else ''}")
        if not cached and i < len(targets) - 1:
            time.sleep(SLEEP_BETWEEN)

    payload = {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "date": card_date,
        "window": {"start": start, "end": end},
        "source": "alphapai recall（投研知识检索）",
        "stocks": stocks,
        "warnings": warns,
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"inst_hot_talk_{card_date}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[json] {out}")
    return payload


def targets_from_board(card_date: str, top: int = 6) -> list[tuple[str, str]]:
    """从热议榜侧面 JSON 里取「最值得深挖」的票：块一要展示的那批。"""
    p = CACHE / f"inst_hot_board_{card_date}.json"
    if not p.exists():
        raise SystemExit(f"缺 {p.name}，先跑 inst_hot_board.py --json")
    d = json.loads(p.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for s in (d.get("overlay", {}).get("both") or [])[:top]:
        if s["code"] in seen:
            continue
        seen.add(s["code"])
        out.append((s["code"], s["name"]))
    # 再补共振票
    for s in (d.get("resonance") or []):
        if len(out) >= top + 2:
            break
        if s["code"] in seen:
            continue
        seen.add(s["code"])
        out.append((s["code"], s["name"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="卡片业务日期，默认今天")
    ap.add_argument("--lookback", type=int, default=9, help="检索回看自然日数")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--codes", nargs="*", default=None,
                    help="显式指定 code:name，如 300308.SZ:中际旭创")
    ap.add_argument("--refresh", action="store_true", help="忽略单票缓存重抓")
    args = ap.parse_args()

    from datetime import date, timedelta
    card_date = args.date or date.today().isoformat()
    end = (date.fromisoformat(card_date) - timedelta(days=1)).isoformat()
    start = (date.fromisoformat(card_date) - timedelta(days=args.lookback)).isoformat()

    if args.codes:
        targets = []
        for c in args.codes:
            code, _, name = c.partition(":")
            targets.append((code, name or code))
    else:
        targets = targets_from_board(card_date, args.top)

    if not targets:
        raise SystemExit("没有目标票")
    print(f"[plan] {len(targets)} 只票 × 1 次检索，间隔 {SLEEP_BETWEEN}s")
    payload = build(card_date, targets, start, end, refresh=args.refresh)
    for w in payload["warnings"]:
        print(f"[warn] {w}")


if __name__ == "__main__":
    main()
