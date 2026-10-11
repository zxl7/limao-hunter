#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块家族聚合（2026-09-07 · 优化C·解决"用户感知粒度 vs 东财板块粒度"断层）
============================================================================
背景：用户看盘感知是「农业在不停加强」，而东财把农业拆成多个板块单列——
  top5 里 大农业 zt=7、养猪 zt=2、种业/转基因 各自独立计数 → 系统按单板块
  判强度会低估跨子板块共振（用户眼里是"农业 9 家"，系统只看到"大农业 7 家"）。

本模块=家族映射的**单一权威源**（类比 flow_noise.py）：
  - 改家族成员只改这里，live_signal / 渲染层 import 本模块，禁止各自另写映射。
  - 家族名 → 子板块名单（东财 top5 板块名粒度）。
  - 仅聚合「涨停数/资金」这类可加维度；龙头取成员中涨停数最多者，不编造新龙头。

接入点（live_signal.py 板块级检测模块；开关 ENABLE_SECTOR_FAMILY 定义于 live_signal.py:284，当前=True）：
  1) 子板块首次达标 → 家族首次报（家族名去重，避免大农业报完养猪再报）；
  2) 卡面渲染标注家族构成：泛农业〔大农业7+养猪2〕9只涨停。
"""
from __future__ import annotations

from typing import Any

# ── 家族映射（单一权威源）─────────────────────────────────────────────
# key=家族名（卡面展示用），value=东财 top5 板块名成员。
# ⚠️ 成员名必须与 intraday_pulse top5 返回的板块名完全一致（可先跑一次对账再定）。
# 2026-09-07 首版：仅收录用户实际感知的「泛农业」族；后续按需扩（如 泛科技/泛资源）。
SECTOR_FAMILIES: dict[str, list[str]] = {
    "泛农业": ["大农业", "养猪", "种业", "转基因", "农业种植", "农机", "乡村振兴", "宠物经济", "水产养殖", "化肥"],
}

# ── 题材聚合名 → 东财板块资金成员（板块资金口径补全 2026-09-08）────────────────
# 背景：live_signal 板块加强卡用本表把 top5 题材名映射到其在东财板块资金池
#   (cache/sector_fund_pool.json·360 板块·东财 clist 行业/概念资金榜) 中的成员板块，
#   加总净额 → 该题材「板块合计主力净额」（卡面附成员明细）。
# ⚠️ 口径说明（2026-10-10 校正）：top5 题材名当前来自【选股宝】，本表 key（大消费/大农业/
#   大金融…）须与 top5 题材名一致才命中；value（新零售/食品饮料…）是东财资金池 board 名，
#   与 top5 来源无关、始终有效。若 key 与选股宝命名不匹配 → 静默跳过（成员缺失自动跳过），
#   不产生错误，但相应题材的资金行会退化为「💤资金未入榜」，属已知覆盖盲区。
# ⚠️ 成员名必须与资金池 board 名一致；加总时成员缺失自动跳过（容忍当日某板块不在池）。
# ⚠️ B2（2026-10-11）：本表现为【聚合名 → 东财板块】的**唯一权威源**。
#   sector_official.py 的别名兜底已改为 import 本表（不再另写一份 → 消除平行表）。
#   value = 东财板块名（资金口径 board 名 / 映射口径通用，名字一致）。
SECTOR_ALIAS: dict[str, list[str]] = {
    "大消费": ["新零售", "一般零售", "商贸零售", "食品饮料", "食品加工", "旅游及景区"],
    "大农业": ["农业种植", "生态农业", "养殖业", "生猪养殖", "农药", "化肥"],
    "大金融": ["非银金融", "多元金融", "金融控股", "银行", "参股银行", "互联网金融"],
    "闪存": ["存储芯片"],
    "油服": ["炼油化工", "油气开采"],
    "影视": ["影视院线", "影视动漫制作"],
    "数字人民币": ["数字货币"],
    # ↓ B2 从 sector_official.ALIAS 合并而来的 4 项聚合概念别名（原并行维护，现已统一到此）
    "资产重组": ["并购重组", "重组概念"],
    "智能电网": ["电网设备", "特高压"],
    "机器人": ["机器人概念", "人形机器人", "工业母机"],
    "国产芯片": ["国产芯片", "半导体", "芯片概念"],
}

# 家族内哪些成员可以跨板块合并（防御：个别泛标签不参与聚合，如「乡村振兴」与种植无关时易误并）
# 默认全部合并；若发现误并，在此剔除该成员（不移除映射，仅不聚合）。
FAMILY_EXCLUDE: dict[str, set[str]] = {}


def get_family(board_name: str) -> str | None:
    """返回板块名所属家族名；不属于任何家族返回 None。"""
    if not board_name:
        return None
    for fam, members in SECTOR_FAMILIES.items():
        if board_name in members and board_name not in FAMILY_EXCLUDE.get(fam, set()):
            return fam
    return None


def aggregate_family(top5: list[dict[str, Any]], family: str) -> dict[str, Any] | None:
    """把 top5 中属于某家族的成员聚合为一个板块对象。

    返回结构（对齐 top5 单板块字段，供 live_signal 检测/渲染直接消费）：
      {name: 家族名, family_members: [{name,zt_count,leader}...],
       zt_count: 合计, leader: 涨停数最多成员的原 leader, sub_text: "大农业7+养猪2",
       member_count: 聚合成员数, _family: True}
    家族内无成员出现在 top5 → 返回 None。
    """
    members = [s for s in (top5 or []) if (s.get("name") or "") in SECTOR_FAMILIES.get(family, [])]
    if not members:
        return None
    total_zt = sum(int(s.get("zt_count") or 0) for s in members)
    # 龙头：取涨停数最多成员的 leader；平手取板块 zt_count 最大者
    best = max(members, key=lambda s: (int(s.get("zt_count") or 0)))
    sub_text = "+".join(f"{s.get('name')}{int(s.get('zt_count') or 0)}" for s in members)
    # 涨停成分聚合（2026-09-08 用户「板块加强卡列出涨停个股」）：合并成员 zt_stocks，
    # 按 code 去重、涨幅降序——家族板块(泛农业)卡面才能列出全部涨停股（成员=top5 板块
    # 自带 zt_stocks·live 当前涨停成分）。缺失(成员无该字段/旧数据)时降级为空不报错。
    zt_pool: dict[str, dict] = {}
    for s in members:
        for z in (s.get("zt_stocks") or []):
            code = str(z.get("code") or "")
            if not code:
                continue
            cur = zt_pool.get(code)
            if cur is None or float(z.get("pct") or 0) > float(cur.get("pct") or 0):
                zt_pool[code] = z
    zt_stocks = sorted(zt_pool.values(), key=lambda z: -float(z.get("pct") or 0))
    return {
        "name": family,
        "family_members": [
            {"name": s.get("name"), "zt_count": int(s.get("zt_count") or 0),
             "leader": (s.get("leader") or {}).get("name")}
            for s in members
        ],
        "zt_count": total_zt,
        "zt_stocks": zt_stocks,
        "leader": best.get("leader") or {},
        "sub_text": sub_text,
        "member_count": len(members),
        "_family": True,
        "delta": members[0].get("delta"),  # 沿用首个成员的 delta 作展示参考
    }


def family_of_top5(top5: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """把 top5 全量归并：{家族名: 聚合对象}。仅供检测层快速消费。"""
    out: dict[str, dict[str, Any]] = {}
    for fam in SECTOR_FAMILIES:
        agg = aggregate_family(top5, fam)
        if agg:
            out[fam] = agg
    return out


def flatten_top5_with_family(top5: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检测/渲染用：返回「子板块 + 家族聚合对象」的合并列表。

    ⛔ 注意：此函数用于**展示层**（把家族成员合并展示）；推送去重仍按家族名，
    由调用方用 family_of_top5 + 成员判定自行处理，避免成员被双计。
    """
    out: list[dict[str, Any]] = []
    covered: set[str] = set()
    fams = family_of_top5(top5)
    for fam, agg in fams.items():
        out.append(agg)
        covered.update(SECTOR_FAMILIES.get(fam, []))
    for s in top5 or []:
        nm = s.get("name") or ""
        if nm and nm not in covered:
            out.append(s)
    return out
