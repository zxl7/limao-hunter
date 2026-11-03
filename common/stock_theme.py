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

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 依赖公共层（架构 P0 复用优先）
from common.http_client import http_get
from common.json_io import load_json, save_json

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
    # 传媒/影视（2026-10-12 用户当场抓包·从泛「AI」规则拆出）：原泛 `("AI","AI算力")`
    #   规则把 中文在线/掌阅科技（plates=影视）的「AI影视/AI阅读」全吞成「AI算力」
    #   → 与真算力票（传艺科技）同线 → 被跟风闸整条误杀 → 10-12 传媒全涨停却没推。
    ("影视", "影视传媒", "影视·内容制作"),
    ("短剧", "影视传媒", "短剧·互动影游"),
    ("院线", "影视传媒", "院线·电影"),
    ("传媒", "影视传媒", "传媒·内容"),
    ("出版", "影视传媒", "出版·传媒"),
    ("阅读", "影视传媒", "数字阅读·AI应用"),
    ("游戏", "影视传媒", "游戏·内容"),
    # ⛔ 2026-10-12 删除泛「AI」规则：desc 含「AI」不再一律算「AI算力」，
    #   仅下列**具体**算力词才归算力；否则落到 plates 概念（如 影视）正确归类。
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

# ============================================================================
# 题材映射快照（P0 · 2026-10-18 用户「按顺序处理」）
# ----------------------------------------------------------------------------
# 【为什么要有这层】`fetch_all()` 走**实时接口**，返回的映射随抓取时刻变化：
#   同一交易日 15:41 跑一次、之后任何时刻重跑一次，拿到的 theme/plates 可能不同
#   → core_theme 变 → 板块梯队/板块情绪/题材聚合变 → **同一份涨停池算出两套候选**。
#   2026-10-16 实证：同一涨停池(61只)+同一 env(2.0退潮ice)+同一 latest.json 重跑，
#   长城军工 raw 54.51→58.28、尖峰集团 63.48→61.29，接力池从
#   [晨曦航空, 尖峰集团] 变成 [冰山冷热, 新华文轩]。**决策不可复现 = 无法审计、无法改进。**
#
# 【做法】eod 首次抓到映射后落 `cache/theme_snapshot/theme_<date>.json`；
#   同日重跑/回放/审计**优先读快照**，缺快照才请求接口并落盘。
#   快照带 fingerprint（sha256 前12位），写进 candidates 供印证"这份候选用的哪份映射"。
#
# 【可逆开关】
#   THEME_SNAPSHOT_ENABLED=0        → 整体关闭，退回旧行为（每次都实时抓）
#   THEME_SNAPSHOT_FORCE_REFRESH=1  → 强制实时抓并覆盖快照（接口补数据时手动用）
#   两个开关都只读环境变量、不改代码即可回滚。
# ============================================================================
THEME_SNAPSHOT_SCHEMA = "theme_map_snapshot_v1"
THEME_SNAPSHOT_DIRNAME = "theme_snapshot"
_BJ = timezone(timedelta(hours=8))


def _snapshot_enabled() -> bool:
    return os.environ.get("THEME_SNAPSHOT_ENABLED", "1") not in ("0", "false", "False", "")


def _snapshot_force_refresh() -> bool:
    return os.environ.get("THEME_SNAPSHOT_FORCE_REFRESH", "") in ("1", "true", "True")


def _default_cache_dir() -> Path:
    """项目根 cache/（本文件在 <root>/common/ 下）。"""
    return Path(__file__).resolve().parents[1] / "cache"


def theme_snapshot_dir(cache_dir: str | Path | None = None) -> Path:
    base = Path(cache_dir) if cache_dir else _default_cache_dir()
    return base / THEME_SNAPSHOT_DIRNAME


def theme_snapshot_path(date: str, cache_dir: str | Path | None = None) -> Path:
    """`cache/theme_snapshot/theme_<YYYY-MM-DD>.json`（date 为空时用 latest）。"""
    d = str(date or "").strip() or "latest"
    return theme_snapshot_dir(cache_dir) / f"theme_{d}.json"


def theme_fingerprint(mapping: dict[str, Any]) -> str:
    """映射内容指纹（sorted keys + 稳定序列化 → sha256 前 12 位）。"""
    try:
        blob = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
    except (TypeError, ValueError):
        return ""


def load_theme_snapshot(date: str, *, cache_dir: str | Path | None = None) -> dict[str, Any] | None:
    """读快照 → {mapping, meta}；不存在/损坏/空映射 → None（调用方回退实时抓）。"""
    raw = load_json(theme_snapshot_path(date, cache_dir))
    if not isinstance(raw, dict):
        return None
    mapping = raw.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        return None
    meta = {
        "source": "snapshot",
        "snapshot_path": str(theme_snapshot_path(date, cache_dir)),
        "snapshot_date": raw.get("date") or date,
        "generated_at": raw.get("generated_at") or "",
        "count": len(mapping),
        "fingerprint": raw.get("fingerprint") or theme_fingerprint(mapping),
        "original_source": raw.get("source") or "live",
    }
    return {"mapping": mapping, "meta": meta}


def save_theme_snapshot(
    date: str,
    mapping: dict[str, Any],
    *,
    cache_dir: str | Path | None = None,
    source: str = "live",
    overwrite: bool = False,
) -> Path | None:
    """落快照（原子写）。默认**不覆盖**已有快照——重跑不得偷改当日决策依据。

    - overwrite=False：快照已存在且非空 → 直接返回原路径（保护首版）
    - overwrite=True ：强制刷新场景显式覆盖
    """
    if not mapping:
        return None
    path = theme_snapshot_path(date, cache_dir)
    if path.exists() and not overwrite:
        existing = load_json(path)
        if isinstance(existing, dict) and isinstance(existing.get("mapping"), dict) and existing.get("mapping"):
            return path
    payload = {
        "schema": THEME_SNAPSHOT_SCHEMA,
        "date": str(date or ""),
        "generated_at": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "count": len(mapping),
        "fingerprint": theme_fingerprint(mapping),
        "mapping": mapping,
    }
    return path if save_json(path, payload) else None


def get_theme_map(
    date: str,
    *,
    cache_dir: str | Path | None = None,
    fetcher: "ThemeFetcher | None" = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """**eod 唯一入口**：拿当日题材映射 + 来源元信息（可审计、可复现）。

    返回 `(mapping, meta)`，meta.source ∈
      - `snapshot`          命中当日快照（重跑/回放/审计走这条，结果与首跑一致）
      - `live`              首次抓取并落盘
      - `snapshot_fallback` 强制刷新但接口抓空 → 退回已有快照（不空标注降级）
      - `empty`             无快照且接口抓空（诚实空标注，不编造）
      - `disabled`          开关关闭（旧行为）
    """
    if not _snapshot_enabled():
        m = (fetcher or ThemeFetcher()).fetch_all()
        return m, {"source": "disabled", "count": len(m), "fingerprint": theme_fingerprint(m)}

    force = _snapshot_force_refresh()
    if not force:
        snap = load_theme_snapshot(date, cache_dir=cache_dir)
        if snap:
            return snap["mapping"], snap["meta"]

    live = (fetcher or ThemeFetcher()).fetch_all()
    meta: dict[str, Any] = {
        "count": len(live),
        "fingerprint": theme_fingerprint(live),
    }
    if live:
        saved = save_theme_snapshot(date, live, cache_dir=cache_dir, source="live", overwrite=force)
        meta["source"] = "live"
        meta["snapshot_path"] = str(saved) if saved else ""
        meta["snapshot_date"] = str(date or "")
        meta["generated_at"] = datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")
        meta["original_source"] = "live"
        return live, meta

    snap = load_theme_snapshot(date, cache_dir=cache_dir)
    if snap:
        snap["meta"]["source"] = "snapshot_fallback"
        return snap["mapping"], snap["meta"]
    meta["source"] = "empty"
    return {}, meta


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
