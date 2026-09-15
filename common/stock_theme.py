#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个股核心炒作题材识别（2026-08-17 用户「识别市场真正炒作的核心题材·非静态行业」）。

背景：eod 选股数据源（biyingapi ztgc / yesterday_zt.json）只带**静态行业**（hy=通用设备），
不含「当前资金炒作的核心题材」。而选股宝 surge_stock/stocks 接口对每只涨停股返回：
  - description：**题材逻辑**（如黄河旋风「金刚石—碳化硅复合材料…半导体散热技术自主可控」）
  - plates：关联**概念板块**（如培育钻石）
这是识别「核心炒作题材」的实时数据源（盘中抓取·事件驱动·跟随热点轮动）。

设计：
- 批量抓取 surge_stock/stocks（uplimit=true 全涨停池）→ 构建 {code: {theme, logic, plates}} 映射
- `core_theme` 标注 = 从 desc 提炼题材名（关键词匹配规则）+ plates 概念 + 热度评级
- 热度等级（H/M/L）：题材所在板块的涨停数/共振强度决定（资金关注度）
- 纯数据标注，不产结论；抓取失败降级为空标注（不编造，数据/AI 边界）
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

# 依赖公共层（架构 P0 复用优先）
from common.http_client import http_get

# 选股宝 surge_stock 接口（盘中活·盘后关闭属正常；返回结构 2026-08-17 已兼容双形态）
XGB_BASE = "https://flash-api.xuangubao.cn/api"

# 题材关键词 → 题材名 提炼规则（description 命中即识别「当前炒作题材」）
# ⛔ 只做客观关键词映射，不编造逻辑；未命中任何规则 → theme 用 plates 概念名或 "—"
THEME_KEYWORDS: list[tuple[str, str, str]] = [
    # (关键词, 题材名, 题材逻辑简述)
    ("金刚石", "金刚石/培育钻石", "金刚石-碳化硅散热·半导体热管理"),
    ("碳化硅", "碳化硅(SiC)", "第三代半导体·新能源/高压器件"),
    ("散热", "半导体散热", "AI算力/芯片热管理·液冷/散热材料"),
    ("CPO", "CPO光模块", "英伟达CPO交换机量产·光互连"),
    ("光模块", "CPO光模块", "光通信·AI算力互联"),
    ("PCB", "PCB", "电子布涨价·AI服务器载板"),
    ("覆铜板", "PCB", "覆铜板涨价·算力硬件"),
    ("存储", "存储芯片", "存储涨价周期·HBM/AI内存"),
    ("HBM", "存储芯片", "HBM高带宽内存·AI"),
    ("机器人", "机器人", "宇树上市在即·人形机器人产业链"),
    ("减速器", "机器人", "人形机器人核心零部件"),
    ("稀土", "稀土永磁", "稀土磁材·机器人/新能源电机"),
    ("磁材", "稀土永磁", "稀土磁材·机器人/新能源电机"),
    ("半导体", "半导体", "国产替代·晶圆厂扩产"),
    ("芯片", "半导体", "国产芯片·自主可控"),
    ("晶圆", "半导体", "晶圆厂扩产·资本开支"),
    ("AI", "AI算力", "AI算力·大模型资本开支"),
    ("算力", "AI算力", "AI算力·数据中心"),
    ("液冷", "液冷散热", "AI服务器液冷·热管理"),
    ("服务器", "AI算力", "AI服务器·算力硬件"),
    ("低空", "低空经济", "低空经济·eVTOL"),
    ("军工", "军工", "国防军工·订单"),
    ("商业航天", "商业航天", "商业航天·卫星互联网"),
    ("航天", "商业航天", "商业航天·火箭/卫星"),
    ("创新药", "创新药", "医药BD出海·创新药"),
    ("中药", "中药", "基药目录·中药"),
    ("创新疫苗", "创新药", "疫苗·生物医药"),
    ("光伏", "光伏", "光伏·供给侧"),
    ("锂电", "锂电池", "锂电·储能"),
    ("储能", "储能", "储能·电网"),
    ("电网", "电网设备", "特高压·电网投资"),
    ("特高压", "电网设备", "特高压·电力设备"),
    ("充电桩", "充电桩", "充电桩·新能源车配套"),
    ("华为", "华为链", "华为产业链·鸿蒙/昇腾"),
    ("鸿蒙", "华为链", "鸿蒙生态"),
    ("昇腾", "华为链", "昇腾AI芯片"),
    ("英伟达", "英伟达链", "英伟达产业链·AI硬件"),
    ("苹果", "苹果链", "苹果产业链·消费电子"),
    ("消费电子", "消费电子", "AI手机/折叠屏·消费电子"),
    ("黄金", "黄金", "黄金·避险/降息"),
    ("铜", "铜", "铜·工业金属"),
    ("铝", "铝", "铝·工业金属"),
    ("化肥", "化肥", "化肥·农业"),
    ("粮食", "粮食", "粮食安全·农业"),
    ("培育钻石", "培育钻石", "培育钻石·金刚石"),
    ("固态电池", "固态电池", "固态电池·新能源"),
    ("脑机", "脑机接口", "脑机接口·前沿科技"),
    ("量子", "量子科技", "量子科技·前沿"),
    ("卫星", "卫星互联网", "卫星互联网·商业航天"),
]

# 热度等级阈值（题材热度 = 所在板块涨停数，来自入参 sector_zt_count）
HEAT_HOT_ZT = 5    # 板块 ≥5 涨停 → H 高热
HEAT_MID_ZT = 3    # 板块 ≥3 涨停 → M 中热


class ThemeFetcher:
    """批量抓取 surge_stock/stocks（uplimit=true 全涨停池）→ {code: 题材信息}。

    - 盘中调用（live_signal 同源接口·每轮 30s 已抓过，此处独立抓一次供 eod 使用）
    - 盘后接口关闭 → 返回空 dict（eod 降级为空标注，不编造）
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] | None = None

    def fetch_all(self) -> dict[str, dict[str, Any]]:
        """返回 {code6: {theme, logic, plates[], desc}}。"""
        if self._cache is not None:
            return self._cache
        out: dict[str, dict[str, Any]] = {}
        try:
            url = f"{XGB_BASE}/surge_stock/stocks?normal=true&uplimit=true&_ts={int(time.time())}"
            raw = http_get(url, t=10, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://www.xuangubao.cn/",
            })
            data = raw.get("data") if isinstance(raw, dict) else None
            # 兼容双结构（2026-08-17：新顶层 data / 旧 stocks.data）
            if (not data or "items" not in data) and isinstance(raw.get("stocks"), dict):
                data = raw.get("stocks", {}).get("data") or {}
            items = (data or {}).get("items") or []
            for it in items:
                try:
                    code = str(it[0]).replace(".SS", "").replace(".SZ", "").replace(".BJ", "")
                    if not code:
                        continue
                    name = str(it[1])
                    desc = str(it[5] or "")
                    plates = it[8] if len(it) > 8 else []
                    plate_names = [p.get("name") for p in (plates or [])
                                   if isinstance(p, dict) and p.get("name")]
                    theme, logic = _extract_theme(desc)
                    if not theme and plate_names:
                        theme = plate_names[0]
                        logic = ""
                    out[code] = {
                        "name": name,
                        "theme": theme or "—",
                        "logic": logic,
                        "plates": plate_names[:4],
                        "desc": desc[:120],
                    }
                except Exception:
                    continue
        except Exception:
            out = {}
        self._cache = out
        return out

    def for_codes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        """只取候选 code 的题材信息（供 eod 回填）。"""
        all_map = self.fetch_all()
        return {c: all_map[c] for c in codes if c in all_map}


def _extract_theme(desc: str) -> tuple[str, str]:
    """从 description 提炼（题材名, 题材逻辑简述）。关键词规则匹配，命中首个即返回。"""
    if not desc:
        return "", ""
    for kw, theme, logic in THEME_KEYWORDS:
        if kw in desc:
            return theme, logic
    return "", ""


def annotate_candidate(
    cand: dict[str, Any],
    theme_info: dict[str, Any] | None,
    sector_zt_count: int = 0,
) -> dict[str, Any]:
    """给单个候选回填核心炒作题材标注（双字段：行业归属 + 当前炒作题材）。

    cand 需含 code/theme（行业）/sector。返回 cand（原地修改并返回）：
      - industry_sector: 行业归属（保留原 theme）
      - core_theme: 当前炒作题材（desc/plates 提炼）
      - core_theme_logic: 题材逻辑简述
      - core_theme_heat: 热度等级 H/M/L（按板块涨停数）
    无题材数据 → core_theme="—"（诚实降级，不编造）。
    """
    code = str(cand.get("code") or "").strip()
    cand["industry_sector"] = str(cand.get("theme") or cand.get("sector") or "—")
    if not theme_info:
        cand["core_theme"] = "—"
        cand["core_theme_logic"] = ""
        cand["core_theme_heat"] = ""
        return cand
    cand["core_theme"] = theme_info.get("theme") or "—"
    cand["core_theme_logic"] = theme_info.get("logic") or theme_info.get("desc") or ""
    cand["core_theme_plates"] = theme_info.get("plates") or []
    # 热度评级：板块涨停数（资金关注度）→ H/M/L
    if sector_zt_count >= HEAT_HOT_ZT:
        heat = "H"
    elif sector_zt_count >= HEAT_MID_ZT:
        heat = "M"
    else:
        heat = "L"
    cand["core_theme_heat"] = heat
    return cand


if __name__ == "__main__":
    # 自检：抓取 + 黄河旋风示例标注
    import sys
    sys.path.insert(0, ".")
    f = ThemeFetcher()
    m = f.fetch_all()
    print(f"抓取涨停池题材映射: {len(m)} 只")
    hh = m.get("600172")
    if hh:
        print(f"黄河旋风: theme={hh['theme']} | logic={hh['logic']} | plates={hh['plates']}")
    else:
        print("600172 不在映射（接口盘后关闭或未涨停）")
