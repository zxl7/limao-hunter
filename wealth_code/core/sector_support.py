#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块竞价承接力（Sector Auction Support）——2026-08-24 用户思想落地

背景（用户原话·金健米业案例 2026-08-24 补录）：
  「周四(8/20)板块已经掉队了，只有它一个人(孤军)。周五(8/21)它竞价开这么高，
   有可能会被别人砸掉，因为它的板块整体是没有承接力的。这种情况下需要考虑
   额外的变量——板块上有没有新的红开助攻拉高它才会起飞，它一个人的话很
   有可能会被砸。这种情况我们之前是没有考虑到的。」

实证（真实数据核验通过）：
  金健米业 600127，8/20 4连板·炸板9次·换手43.4%·同板块「农产品加工」涨停成员仅它1家；
  8/21 竞价开 9.39（高开 +9.19%），板块零红开助攻 → 全天冲高 9.45 后砸至收 8.20（-4.65%），
  振幅约 15 个点。系统旧逻辑对该高开只给「极端高开·先观察承接」的中性信号，无板块维度。

本模块职责（纯函数·确定性·无 IO）：
  1. eod 阶段：标注候选是否「板块孤军」（同 sector 昨日涨停池成员数==1）
  2. verify 阶段：结合同板块成员竞价快照，计算「板块竞价承接力」：
     - 孤军高开（is_lone ∧ 板块 0 红开 ∧ 个股高开≥3%）→ 被砸风险，买→观察降级
     - 板块助攻（红开≥1 或 高开≥2% 家数≥1）→ 高开有承接，维持原判定并增强标注

数据流：
  pipeline_eod._attach_sector_lone(scored)      → candidates.sector_is_lone / sector_mates
  cmd_verify --fetch 抓 mates 竞价              → sector_support_map[code]
  signal_eval.evaluate_realtime_signal(sector_support=...) → 降级/增强
  build_cards.build_verify_card                  → 🧲板块承接标注
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 个股「高开」触发阈值：孤军票竞价涨幅 ≥ 该值且板块 0 红开 → 判孤军高开·被砸风险
LONE_HIGH_OPEN_GAP_PCT = 3.0
# 板块「高开助攻」阈值：同板块成员竞价涨幅 ≥ 该值算强助攻
STRONG_OPEN_GAP_PCT = 2.0

# 申万二级行业 → 题材板块 聚合映射（2026-08-25 用户「孤军判定不能单一按行业看个股·
# 要结合题材接口看整体板块效应·医药还有其他涨停不是单一的」）：
# 孤军判定的行业粒度（申万二级）太细——医药系分散在 化学制药/中药Ⅱ/生物制品/医疗器械/
# 医药商业/医疗服务 6 个行业，单行业最多 2 家，个股被误判「孤军」，但题材层 8~9 家涨停
# 有明显板块效应。映射把同题材的分散行业归并，判断「题材板块效应」。
# ⛔ 只做客观行业→题材归并，不编造；未命中映射的行业保持原行业孤军判定。
SECTOR_THEME_MAP: dict[str, str] = {
    # 医药系
    "化学制药": "医药", "中药Ⅱ": "医药", "生物制品": "医药", "医疗器械": "医药",
    "医药商业": "医药", "医疗服务": "医药", "原料药": "医药", "化学制剂": "医药",
    "血液制品": "医药", "疫苗": "医药", "中药": "医药", "创新药": "医药",
    "医药": "医药", "医药生物": "医药", "生物医药": "医药",
    # 机器人/高端装备
    "机器人": "机器人", "通用设备": "机器人", "自动化设备": "机器人",
    "专用设备": "机器人", "工控设备": "机器人", "机械基础件": "机器人",
    # 金刚石/散热（2026-08-25 用户「黄河旋风明明是钻石·以题材的涨停为准」：
    # core_theme 优先于行业，钻石类题材独立聚合，不被行业映射误导到机器人）
    "金刚石": "金刚石/培育钻石", "培育钻石": "金刚石/培育钻石",
    "金刚石/培育钻石": "金刚石/培育钻石", "散热": "半导体散热",
    "半导体散热": "半导体散热",
    # AI 算力
    "通信设备": "AI算力", "通信服务": "AI算力", "半导体": "AI算力",
    "元件": "AI算力", "消费电子": "AI算力", "光学光电子": "AI算力",
    "计算机设备": "AI算力", "软件开发": "AI算力", "IT服务Ⅱ": "AI算力",
    "电子化学品Ⅱ": "AI算力", "其他电子Ⅱ": "AI算力", "电子": "AI算力",
    # 大消费
    "一般零售": "大消费", "商业物业经营": "大消费", "旅游及景区": "大消费",
    "酒店餐饮": "大消费", "食品加工": "大消费", "饮料乳品": "大消费",
    "休闲食品": "大消费", "白酒Ⅱ": "大消费", "家电零部件Ⅱ": "大消费",
    "厨卫电器": "大消费", "服装家纺": "大消费", "家用轻工": "大消费",
    "专业连锁": "大消费", "互联网电商": "大消费", "传媒": "大消费",
    "游戏Ⅱ": "大消费", "数字媒体": "大消费",
    # 农业
    "农产品加工": "大农业", "农化制品": "大农业", "种植业": "大农业",
    "饲料": "大农业", "渔业": "大农业", "畜禽养殖": "大农业", "农业综合Ⅱ": "大农业",
    "粮食": "大农业", "种业": "大农业", "化肥": "大农业", "乡村振兴": "大农业",
    # 军工
    "航空装备Ⅱ": "军工", "航天装备Ⅱ": "军工", "军工电子Ⅱ": "军工",
    "地面兵装Ⅱ": "军工", "航海装备Ⅱ": "军工",
    # 新能源
    "光伏设备": "新能源", "电池": "新能源", "风电设备": "新能源",
    "电网设备": "新能源", "能源金属": "新能源",
    # 黄金/有色
    "贵金属": "黄金有色", "工业金属": "黄金有色", "小金属": "黄金有色",
    "金属新材料": "黄金有色", "冶钢原料": "黄金有色",
    # 地产/基建
    "房地产开发": "地产基建", "房地产服务": "地产基建", "装修装饰Ⅱ": "地产基建",
    "基础建设": "地产基建", "房屋建设Ⅱ": "地产基建", "水泥": "地产基建",
    "玻璃玻纤": "地产基建", "工程咨询服务Ⅱ": "地产基建",
    # 化工
    "化学原料": "化工", "化学制品": "化工", "塑料": "化工", "橡胶": "化工",
    "农药兽药": "化工", "纺织化学": "化工",
}


def _theme_key(it: dict[str, Any]) -> str:
    """题材层板块 key：优先用已标注的 core_theme（desc 提炼·更精准），
    否则用 行业→题材 聚合映射兜底（不依赖接口·盘后可用）。
    ⛔ core_theme 也须经映射归一（如「中药」→「医药」）——候选的 core_theme 与
    涨停池行的行业映射必须是同口径，否则归并错位（实证 08-25 汉森 core_theme=中药
    与 pool 行中药Ⅱ→医药 失配，题材mates=0）。
    ⛔ 2026-08-25 二修（带动性时序·用户「小周期载体」）：core_theme 的细分题材
    （液冷散热/半导体散热/云计算数据中心…）也走 EM_THEME_BRIDGE 归并——题材接口
    把液冷拆成 4 桶，英维克(液冷散热)/康盛(半导体散热)/佳力图(数据中心) 互不聚合，
    带动性统计失真（英维克带动 0 家 vs 用户看到「液冷 8+ 涨停」）。桥接后同归 AI算力。"""
    ct = str(it.get("core_theme") or "")
    if ct and ct != "—":
        return EM_THEME_BRIDGE.get(ct, SECTOR_THEME_MAP.get(ct, ct))
    sec = str(it.get("sector") or it.get("hy") or "")
    return SECTOR_THEME_MAP.get(sec, sec)


# 东财题材名 → 本地题材名 补充映射（2026-08-25 用户「题材归纳参考东财接口」）：
# 东财 top5 板块名（医药/大消费/机器人/大农业…）与 SECTOR_THEME_MAP 题材名大部分一致，
# 少数需桥接（液冷服务器→AI算力、光通信→AI算力、煤炭→能源、电子特气→化工…）。
EM_THEME_BRIDGE: dict[str, str] = {
    "液冷服务器": "AI算力", "光通信": "AI算力", "CPO": "AI算力", "算力": "AI算力",
    "半导体": "AI算力", "芯片": "AI算力", "服务器": "AI算力", "PCB": "AI算力",
    "人工智能": "AI算力", "大模型": "AI算力", "存储": "AI算力",
    # 2026-08-25 带动性时序补全：题材接口细分 → 东财口径（液冷系拆 4 桶互不聚合，
    # 英维克/康盛/佳力图 带动性统计失真；桥接后同归 AI算力）
    "液冷散热": "AI算力", "半导体散热": "AI算力", "云计算数据中心": "AI算力",
    "数据中心": "AI算力", "液冷": "AI算力", "AI服务器": "AI算力",
    "煤炭": "能源", "石油": "能源", "天然气": "能源", "电力": "能源",
    "电子特气": "化工", "氟化工": "化工", "化工": "化工",
    "创新药": "医药", "生物医药": "医药", "中药": "医药", "医疗服务": "医药",
    "军工": "军工", "国防": "军工", "航天": "军工",
    "黄金": "黄金有色", "有色金属": "黄金有色", "贵金属": "黄金有色",
    "光伏": "新能源", "锂电": "新能源", "储能": "新能源", "电池": "新能源",
    "房地产": "地产基建", "基建": "地产基建", "建筑": "地产基建",
    "食品饮料": "大消费", "白酒": "大消费", "零售": "大消费", "旅游": "大消费",
    "农业": "大农业", "种业": "大农业", "粮食": "大农业", "养殖": "大农业",
    "机器人": "机器人", "减速器": "机器人", "自动化": "机器人",
}


def _em_theme_match(theme: str, em_name: str) -> bool:
    """本地题材名与东财板块名是否同一题材（桥接映射 + 双向包含）。"""
    if theme == em_name:
        return True
    t = EM_THEME_BRIDGE.get(theme, theme)
    e = EM_THEME_BRIDGE.get(em_name, em_name)
    if t == e:
        return True
    # 双向包含（如「医药」∈「医药商业」「AI算力」∈「算力」）
    return theme in em_name or em_name in theme


def load_em_sector_zt(latest_path: Path | None = None) -> dict[str, int]:
    """读 latest.json 的 top5（东财板块接口收盘快照）→ {板块名: zt_count}。
    2026-08-25 用户「题材归纳/板块行动参考东财接口和已有推送接口」：
    东财板块涨停数是题材板块效应的权威口径（盘后 eod 仍可用·latest.json 15:00 快照
    保留当日收盘状态）。读取失败返回 {}（不编造，降级用涨停池聚合估算）。"""
    try:
        p = latest_path or Path(__file__).resolve().parent.parent.parent / "cache" / "pulse_snapshots" / "latest.json"
        lt = json.loads(p.read_text(encoding="utf-8"))
        return {str(s.get("name") or ""): int(s.get("zt_count") or 0)
                for s in (lt.get("top5") or []) if s.get("name")}
    except Exception:
        return {}


def _sector_key(it: dict[str, Any]) -> str:
    """统一提取板块 key：候选有 sector（=涨停池 hy），涨停池行有 hy；theme 兜底。"""
    return str(it.get("sector") or it.get("hy") or it.get("theme") or "其他")


def annotate_lone(scored: list[dict[str, Any]], pool: list[dict[str, Any]] | None = None,
                  em_sector_zt: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """
    盘后标注：按 sector（申万二级行业）聚合**完整涨停池**（pool），成员数==1 的 sector 中
    唯一涨停股 = 「板块孤军」。
    - sector_is_lone: bool —— 行业孤军标记（供 verify 判定 + 卡片警示 + 期望文案）
    - sector_mates: list[str] —— 同 sector 其他涨停成员 codes（供次日竞价抓助攻）
    - sector_mates_cnt: int —— 同 sector 涨停池成员数（含自己）
    - theme_mates_cnt: int —— **题材层**涨停池成员数（行业→题材归并 / core_theme，2026-08-25 新增）
    - theme_is_lone: bool —— 题材层孤军（题材板块无梯队）
    - sector_is_lone 语义（2026-08-25 用户「孤军判定不准确·医药还有其它涨停·要全局化」）：
      行业孤军 且 题材层无板块效应（theme_mates_cnt<=1）→ True（真孤军·无任何板块承接）；
      行业孤军但题材层有板块效应（theme_mates_cnt>=2）→ **False 并标 theme_has_sector_effect=True**
      （个股行业分散但题材整体有涨停梯队=板块效应存在，不得判孤军——实证 2026-08-25
      凯莱英 医疗服务 行业孤军，但医药题材 8~9 家涨停被误判孤军）。

    ⛔ 2026-08-25 修复（用户抓包「全打上孤军」）：聚合基准必须是**完整涨停池**
    （load_zt_pool 的 ztgc，含 ST/一字/688/低成交额全部涨停股），**不是候选子集**。
    旧实现把 scored（已过滤+topN 的 relay/watch）当聚合基准——候选池内同行业通常
    只剩 1 只 → 全员误判孤军（实证：08-24 贵金属板块涨停 5+ 家，湖南白银/白银有色
    却被标 sector_mates_cnt=1、sector_is_lone=True）。pool 缺省时向后兼容用 scored。

    ⛔ 2026-08-25 二修（用户「题材归纳参考东财接口和已有推送接口」）：题材层板块效应
    优先用 **东财板块接口收盘快照**（latest.json top5 的 zt_count·em_sector_zt 参数，
    权威口径：医药 zt=9 等），按题材名双向匹配校准涨停池聚合估算；东财无数据时
    降级用涨停池聚合（theme_mates_cnt 兜底，不编造）。

    说明：孤军是「板块无梯队承接」的盘后信号，不塞池不降排名——高标孤军仍是
    辨识度候选（用户情绪核心战法认可总龙头独立走强），真正的裁决在次日竞价
    的板块承接力（见 compute_support）。
    """
    pool = pool if pool is not None else scored
    by_sector: dict[str, list[dict[str, Any]]] = {}
    by_theme: dict[str, list[dict[str, Any]]] = {}
    for it in pool:
        by_sector.setdefault(_sector_key(it), []).append(it)
        by_theme.setdefault(_theme_key(it), []).append(it)
    em_zt = em_sector_zt if em_sector_zt is not None else load_em_sector_zt()
    for it in scored:
        sec = _sector_key(it)
        members = by_sector.get(sec) or []
        it["sector_mates_cnt"] = len(members)
        it["sector_mates"] = [
            str(m.get("dm") or m.get("code") or "").strip()
            for m in members
            if str(m.get("dm") or m.get("code") or "").strip()
            and str(m.get("dm") or m.get("code") or "").strip() != str(it.get("dm") or it.get("code") or "").strip()
        ]
        # 题材层板块效应（2026-08-25 用户「全局化眼光·结合题材判断」）：
        # 行业孤军但题材有涨停梯队 → 不是真孤军
        th = _theme_key(it)
        th_members = by_theme.get(th) or []
        pool_cnt = len(th_members)
        it["theme_mates"] = [
            str(m.get("dm") or m.get("code") or "").strip()
            for m in th_members
            if str(m.get("dm") or m.get("code") or "").strip()
            and str(m.get("dm") or m.get("code") or "").strip() != str(it.get("dm") or it.get("code") or "").strip()
        ]
        # 东财板块接口权威口径校准（2026-08-25 用户「题材归纳参考东财接口」）：
        # 匹配东财 top5 同题材板块，用东财真实 zt_count（≥聚合估算时取东财值）
        em_cnt = 0
        for em_name, zt_n in em_zt.items():
            if _em_theme_match(th, em_name):
                em_cnt = max(em_cnt, zt_n)
        theme_cnt = max(pool_cnt, em_cnt)
        it["theme_mates_cnt"] = theme_cnt
        it["theme_name"] = th
        it["theme_em_name"] = next((em for em, zn in em_zt.items() if _em_theme_match(th, em)), "")
        it["theme_is_lone"] = theme_cnt <= 1
        it["theme_has_sector_effect"] = theme_cnt >= 2
        # 最终孤军 = 行业孤军 且 题材层也无梯队（真·板块无承接）
        it["sector_is_lone"] = len(members) == 1 and theme_cnt <= 1
    return scored


def annotate_carry(scored: list[dict[str, Any]], pool: list[dict[str, Any]] | None = None,
                   window_min: int = 30) -> list[dict[str, Any]]:
    """带动性时序标注（2026-08-25 用户「小周期载体·逆环境主动走强+带动同属性+反推板块」·#2）：
    载体三标准里「带动同属性/反推板块」的机械证据——按 fbt（封板时间）排序，
    统计该股封板后 window_min 分钟内、**同题材（_theme_key 归并口径）**新增涨停数。

    - carry_cnt:  int —— 封板后 30min 内同题材新增涨停数（含自己外的跟进者）
    - carry_time: str —— 该股封板时间（HH:MM，供卡片展示）
    - carry_score: float —— 带动性分（0~1 归一）：carry_cnt 越高带动性越强

    ⛔ 口径：①带动性只看**封板之后**的跟进（反推板块=时序因果，不是静态同题材家数——
    汉森 09:45 封板后 4min 带动华森/海南海药 = 带动性强；若只是板块先涨自己后封 = 被推动非带动）；
    ②题材归并用 `_theme_key`（core_theme 优先·与孤军判定同口径）；③fbt 缺失的 pool 行不参与
    时序（无法确定先后·诚实不编造）；④一字板（竞价封死 fbt≈0925）带动性天然为 0——它没有
    「主动走强」过程，与载体哲学一致（一字通道票无辨识度参与权）。

    实证（2026-08-25）：汉森制药 09:45 封板 → 华森 09:49 / 海南海药 09:54 = 2 家跟进（4min 内）
    带动性强；深中华A 09:25 竞价封死 → 饰品板块 0 家跟进 = 带动性弱（孤军·被市场抛弃边缘）。
    """
    pool = pool if pool is not None else scored
    # 按题材归并 + fbt 排序，构建每只的封板顺序
    by_theme: dict[str, list[dict[str, Any]]] = {}
    by_sec: dict[str, list[dict[str, Any]]] = {}
    for it in pool:
        by_theme.setdefault(_theme_key(it), []).append(it)
        sec = str(it.get("sector") or it.get("hy") or "")
        if sec:
            by_sec.setdefault(sec, []).append(it)

    def _fbt_min(it: dict[str, Any]) -> int | None:
        raw = str(it.get("fbt") or "")
        if len(raw) < 4:
            return None
        try:
            return int(raw[:2]) * 60 + int(raw[2:4])
        except ValueError:
            return None

    for it in scored:
        th = _theme_key(it)
        # 行业→题材映射兜底（2026-08-25 补：题材接口细分噪声——海南海药 core=脑机接口
        # 但行业化学制药→医药，纯题材归并漏计带动。行业映射兜底 = 跟进者的行业映射题材
        # == 候选的行业映射题材（中药Ⅱ→医药 == 化学制药→医药），则计入）。
        sec_key = str(it.get("sector") or it.get("hy") or "")
        sec_theme = SECTOR_THEME_MAP.get(sec_key, "")
        members = sorted(by_theme.get(th) or [], key=lambda x: (_fbt_min(x) or 9999))
        my_t = _fbt_min(it)
        carry = 0
        if my_t is not None:
            # 统计同题材中 fbt 在 (my_t, my_t+window_min] 区间的跟进者（不含自己）
            for m in members:
                mt = _fbt_min(m)
                if mt is None or mt <= my_t:
                    continue
                if mt - my_t <= window_min:
                    carry += 1
            # 行业映射兜底（2026-08-25 补：题材接口细分噪声——海南海药 core=脑机接口
            # 但行业化学制药→医药，纯题材归并漏计带动。兜底 = 跟进者行业映射题材
            # == 候选行业映射题材（中药Ⅱ→医药 == 化学制药→医药）且不在主题桶已计）
            if sec_theme:
                for m in pool:
                    if m is it:
                        continue
                    mt = _fbt_min(m)
                    if mt is None or mt <= my_t:
                        continue
                    if mt - my_t > window_min:
                        continue
                    if _theme_key(m) != th:  # 主题桶已计的（key 同）不再加
                        m_sec = str(m.get("sector") or m.get("hy") or "")
                        if SECTOR_THEME_MAP.get(m_sec, "") == sec_theme:
                            carry += 1
        it["carry_cnt"] = carry
        it["carry_time"] = (f"{my_t // 60:02d}:{my_t % 60:02d}" if my_t is not None else "")
        it["carry_score"] = round(min(carry / 3.0, 1.0), 2)  # ≥3 家跟进即满分 1.0
    return scored


def collect_mates_codes(cands: list[dict[str, Any]]) -> list[str]:
    """收集所有候选的同板块成员 codes（去重），供 verify 阶段批量抓竞价。
    2026-08-25 升级（P3·审查「盘中板块加强→次日竞价助攻池打通」）：**合并行业层
    sector_mates + 题材层 theme_mates**——题材板块效应的票（如凯莱英 医药9家涨停·
    sector_is_lone=False 但同行业医疗服务仅自己）仅抓行业成员会拿不到任何助攻报价，
    板块承接力标注缺失。题材成员（theme_mates=同题材涨停 codes）纳入后，次日竞价
    能抓到真实题材助攻（医药其他 8 家竞价），compute_support 才能算出「🧲板块助攻N家」。
    盘中「板块加强」= 早盘板块级爆发(zt_count≥3) + 盘中续聚 → 该题材成员自然进入
    theme_mates，跨链路打通。"""
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        for code in (c.get("sector_mates") or []) + (c.get("theme_mates") or []):
            code = str(code).strip()
            if code and code not in seen:
                seen.add(code)
                out.append(code)
    return out


def compute_support(
    cand: dict[str, Any],
    mates_quotes: dict[str, dict[str, Any]] | None,
    *,
    lone_high_open_gap: float = LONE_HIGH_OPEN_GAP_PCT,
    strong_open_gap: float = STRONG_OPEN_GAP_PCT,
) -> dict[str, Any]:
    """
    竞价阶段计算单只候选的板块承接力。

    入参：
    - cand: 候选（需 sector_is_lone / sector_mates，缺省按 0 成员处理）
    - mates_quotes: {mate_code: {prev_close, auction_price, auction_amount_yi, time}}
      只统计竞价价有效的成员（auction_price>0 and prev_close>0）。

    返回 dict：
      is_lone / mates_total / red_open(红开家数) / strong_open(高开≥2%家数)
      avg_gap(成员平均竞价涨幅) / support_level / label / note

    support_level 语义（裁决口径）：
      - lone_high_open : 孤军 + 板块 0 红开（个股高开与否由 signal_eval 按 gap 判定）
      - lone_no_support: 孤军 + 板块 0 红开（个股是否高开由调用方决定降级）
      - support_ok     : 板块红开≥1 或 高开≥2%≥1 → 有承接
      - no_data        : 拿不到同板块成员竞价（成员缺失/报价缺失）→ 不裁决不阻断
    """
    is_lone = bool(cand.get("sector_is_lone"))
    # 2026-08-25 P3 打通：行业层成员(sector_mates)优先，空时回退题材层(theme_mates)——
    # 题材板块效应的票（如凯莱英 医药9家涨停·sector_is_lone=False 但同行业仅自己）
    # 用题材成员算承接力，否则拿不到任何助攻报价、板块承接标注缺失。
    mates = [str(c).strip() for c in (cand.get("sector_mates") or []) if str(c).strip()]
    if not mates:
        mates = [str(c).strip() for c in (cand.get("theme_mates") or []) if str(c).strip()]
    mq = mates_quotes or {}

    red_open = 0
    strong_open = 0
    gaps: list[float] = []
    for code in mates:
        q = mq.get(code)
        if not q:
            continue
        try:
            pc = float(q.get("prev_close") or 0.0)
            ap = float(q.get("auction_price") or 0.0)
        except (TypeError, ValueError):
            continue
        if pc <= 0 or ap <= 0:
            continue
        gap = round((ap / pc - 1.0) * 100.0, 2)
        gaps.append(gap)
        if gap > 0:
            red_open += 1
        if gap >= strong_open_gap:
            strong_open += 1

    avg_gap = round(sum(gaps) / len(gaps), 2) if gaps else None

    # —— 裁决 ——
    if is_lone and len(gaps) == 0:
        # 孤军且板块成员竞价全部缺失 → 无法证明有承接，但不冤枉：仍按孤军零承接提示
        level = "lone_no_support"
        label = "🧲孤军·板块竞价无数据"
        note = "板块孤军（昨日同板块仅本股涨停），且竞价阶段未取到同板块成员报价，无法确认承接。"
    elif is_lone and red_open == 0:
        level = "lone_no_support"
        label = "🧲孤军·板块0红开"
        note = f"板块孤军（昨日同板块仅本股涨停），竞价同板块{len(gaps)}家报价 0 家红开，板块无承接。"
    elif red_open >= 1 or strong_open >= 1:
        level = "support_ok"
        label = f"🧲板块助攻{red_open}家红开"
        note = (
            f"板块有承接：同板块{len(gaps)}家报价中红开{red_open}家"
            + (f"、高开≥{strong_open_gap:.0f}% {strong_open}家" if strong_open else "")
            + (f"、平均{avg_gap:+.2f}%" if avg_gap is not None else "")
        )
    else:
        level = "support_ok" if red_open >= 1 else "no_data"
        label = "🧲板块无红开" if not is_lone and len(gaps) > 0 and red_open == 0 else "🧲板块承接未知"
        note = f"同板块{len(gaps)}家报价红开{red_open}家" if gaps else "未取到同板块成员竞价报价。"
        if is_lone:
            level = "lone_no_support"

    return {
        "is_lone": is_lone,
        "mates_total": len(mates),
        "mates_quoted": len(gaps),
        "red_open": red_open,
        "strong_open": strong_open,
        "avg_gap": avg_gap,
        "support_level": level,
        "label": label,
        "note": note,
    }


def is_lone_high_open(support: dict[str, Any] | None, gap_pct: float | None,
                      *, lone_high_open_gap: float = LONE_HIGH_OPEN_GAP_PCT) -> bool:
    """
    孤军高开判定（signal_eval 消费）：
    个股竞价高开(≥3%) ∧ 板块孤军 ∧ 板块 0 红开 → True（被砸风险，买→观察降级）。
    """
    if not support:
        return False
    if support.get("support_level") not in ("lone_no_support", "lone_high_open"):
        return False
    if not support.get("is_lone"):
        return False
    if gap_pct is None or gap_pct < lone_high_open_gap:
        return False
    return True
