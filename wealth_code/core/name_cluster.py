#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""名字炒作集群识别（2026-11-04 用户「名字炒作维度可以做」）。

## 为什么需要它

2026-11-04 实证（东方海洋 002086）：
「羊字辈」集体崩塌当天，东方海洋的申万行业是 **农产品加、同组只有它自己**，
而真正的同族分散在 6 个互不相干的行业里：

| 票 | 申万行业 | 今日竞价 |
|---|---|---|
| 阳光照明 600261 | 照明设备 | 0.00% |
| 阳光乳业 001318 | 饮料乳品 | −2.84% |
| 澳洋健康 002172 | 一般零售 | −3.53% |
| 华阳新材 600281 | 化学制品 | −5.18% |
| 天洋新材 603330 | 塑料 | −5.05% |
| 海阳科技 603382 | 纺织制造 | −6.40% |
| 华阳国际 002949 | 装修建材 | −9.97% |
| 红太阳 000525 | 化学制品 | −7.57% |

⇒ **按行业分，它们毫不相干；按名字分，它们是一伙的。**
   任何基于 `sector` / `core_theme` 的板块体检都会把这场崩塌判成「没事」。

## 识别口径

**共字集群**：涨停池里名字含同一汉字的票 ≥ `MIN_CLUSTER` 只，且该字不是通用词。

⛔ 只用**客观共字**，不做拼音/谐音扩展：
   - 拼音需要额外依赖（本机无 pypinyin），且谐音表只能靠人工维护 → 易变成事后编规则。
   - 共字已经能抓到绝大多数名字炒作（「阳」7 只、「洋」3 只，实测）。
   - 「阳/洋」同音但**不合并**：合并需要谐音表，⛔ 不在本模块做。
     若将来要合并，必须单独维护一张**有实证来源**的同音组表。

⛔ 边界：
  - 只回答「哪些票因为名字被归到一起」，**不代表它们真被资金当一族炒**。
    是否成立要看竞价表现（同族红开率/跌幅），本模块不下这个判断。
  - 通用字（科技/股份/中国/新材…）一律剔除，否则「新」「中」这种字会伪造出巨型假簇。
  - 不足 3 只不成簇（2 只可能是巧合）。
"""
from __future__ import annotations

from typing import Any, Iterable

SCHEMA = "name_cluster_v1"

# 至少几只才算「一簇」
MIN_CLUSTER = 3

# ⛔ 通用字：出现在大量股票名里、不构成「炒作族」的字。
#    判据：这类字是行业/组织形式/方位词，任何一天都会撞上。
STOP_CHARS = set(
    "科技股份集团中国新大小高金银"
    "证券银行保险电力能源材料实业发展控股投资基金管理有限公司"
    "星云天地人和正达通联创鑫盛隆泰昌荣华富强"
    "安全环保智能数字网络信息软件系统数据服务"
    "消费医药生物健康食品农业牧渔养殖旅游文化传媒教育"
    "交通物流地产建筑装饰工程机械汽车电气电子通信计算半导体芯器设备制造加工"
    "化学石油燃气煤炭钢铁有色矿"
    "一二三四五六七八九十百千万"
    "东西南北中上下左右前后"
    "国际全球亚洲亚太欧美"
    "第一第二第三"
)

# 这些字放在名字里任何位置都太常见，额外剔除（子串级）
STOP_TOKENS = ("ST", "*ST", "退")


def _name_of(row: dict[str, Any]) -> str:
    return str((row or {}).get("name") or "").strip()


def _code_of(row: dict[str, Any]) -> str:
    return str((row or {}).get("code") or "").strip()


def build_clusters(rows: Iterable[dict[str, Any]],
                   *, min_size: int = MIN_CLUSTER,
                   stop_chars: set[str] | None = None) -> dict[str, list[str]]:
    """共字集群：`{字: [代码...]}`，只返回成员 ≥ `min_size` 的簇。

    ⛔ 只收 6 位代码（A 股），避开指数/板块编号。
    ⛔ 同一只票在一个字下只出现一次（名字里重复字不算两次）。
    """
    stop = STOP_CHARS if stop_chars is None else stop_chars
    hits: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for r in rows or []:
        code, name = _code_of(r), _name_of(r)
        if len(code) != 6 or not code.isdigit() or not name:
            continue
        if any(tok in name.upper() for tok in STOP_TOKENS):
            continue
        for ch in set(name):                 # set → 名字里同字只算一次
            if ch in stop or not ("\u4e00" <= ch <= "\u9fff"):
                continue
            bucket = hits.setdefault(ch, [])
            mark = seen.setdefault(ch, set())
            if code in mark:
                continue
            mark.add(code)
            bucket.append(code)
    return {ch: codes for ch, codes in hits.items() if len(codes) >= min_size}


def cluster_map(rows: Iterable[dict[str, Any]],
                *, min_size: int = MIN_CLUSTER) -> dict[str, list[str]]:
    """`{代码: [所属字...]}` —— 每只票落在哪些簇里。

    ⛔ 一只票可能同时属于多个簇（「阳光照明」既是「阳」也是「光」）。
      调用方自行决定怎么合并；本模块不选「主簇」——那需要主观判断。
    """
    out: dict[str, list[str]] = {}
    for ch, codes in build_clusters(rows, min_size=min_size).items():
        for c in codes:
            out.setdefault(c, []).append(ch)
    return out


def annotate(rows: list[dict[str, Any]], *, min_size: int = MIN_CLUSTER) -> dict[str, Any]:
    """给每行挂 `name_cluster`（所属字列表）+ 返回汇总。

    ⛔ 只写字段，不改任何顺序/评分/资格。
    """
    cmap = cluster_map(rows, min_size=min_size)
    for r in rows or []:
        if isinstance(r, dict):
            r["name_cluster"] = cmap.get(_code_of(r), [])
    groups = build_clusters(rows, min_size=min_size)
    return {
        "schema": SCHEMA,
        "min_size": min_size,
        "cluster_count": len(groups),
        "tagged": len(cmap),
        "clusters": {ch: len(codes) for ch, codes in
                     sorted(groups.items(), key=lambda kv: -len(kv[1]))},
    }
