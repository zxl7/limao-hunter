#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选股宝板块 → 东财官方板块 映射层（2026-10-08 用户要求）

用户原话：「**系统自造聚合名改为东财官方板块名！！**」

背景（实测事实，2026-10-08）
----------------------------------------------------------------------
top5 的板块名来自**选股宝**（flash-api.xuangubao.cn）的「热点板块」体系
（`surge_stock/plates` 每日约 16 个，如 电子树脂/航运/锂电池/大消费/资产重组），
是选股宝自造的聚合概念，与**东财官方板块体系**（行业 + 概念，带 BK 码）
没有任何对应关系 → 三个连锁病灶：

  ① **下游按名字匹配东财板块天然失灵**：资金行「💤资金未入榜」、接力卡扫不到成分股；
  ② **同一只票在两边归属不同维度**：领湃科技 = 选股宝「锂电池」，
     东财 = 「电池 BK1033」+「锂电池 BK1303」+「钠离子电池」…（实测 15 个板块）；
  ③ 「大消费/资产重组」这类聚合名**东财根本没有同名板块** → 名字匹配永远失败。

本模块 = 映射的【唯一权威源】（类比 flow_noise.py / sector_family.py）。
算法 = **成分股交集 + 名称相似度 双证据**，⛔ 不靠纯名字猜测
（实测精确同名率仅 ~36%：14 个选股宝名里只有 5 个能在东财找到同名板块）。

数据源选择（实测）
----------------------------------------------------------------------
⛔ **不用东财 clist 拉全量板块表**：本机长期被风控 `RemoteDisconnected`
（2026-10-08 实测 clist 连生产兜底 `_fetch_em_rising` 都返回 0 只）。
✅ **改用 slist 逐票累积**：`push2/api/qt/slist/get?secid=..&spt=3`
返回「个股 → 所属全部东财板块（带 BK 码）」，实测稳定可用（15~19 个/票）。
   20 只票即累积出 163 个东财板块 → 字典**自增长**，跑得越久覆盖越全。

产物 / 消费方式（运行时零网络开销）
----------------------------------------------------------------------
  离线生成：`python3 scripts/sector_official.py --build`
  → cache/em_stock_sectors.json    个股 → 所属东财板块（长期缓存，个股归属极少变）
  → cache/sector_official_map.json 选股宝板块名 → 东财官方板块（BK + 名）
  在线消费：intraday_pulse 读映射表给 top5 挂 `official` 字段（纯读表，不发请求）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# scripts 目录注入 path，供 import sector_family（B2：复用其 SECTOR_ALIAS 单一权威源）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.http_client import http_get          # 统一出口铁律（2026-08-17）
from common.json_io import load_json, save_json

# B2（2026-10-11）：别名兜底统一走 sector_family.SECTOR_ALIAS（消除与 sector_official.ALIAS 平行表）。
# 导入失败兜底为空表 → 不影响主链路（仅聚合别名映射退化为 none）。
try:
    from sector_family import SECTOR_ALIAS as _XGB_ALIAS
except Exception:
    _XGB_ALIAS = {}

ROOT = Path(__file__).resolve().parent.parent    # ⚠️ 测试可替换（隔离生产 cache）

# ── 总开关（可逆）─────────────────────────────────────────────────────
# False = 本层空转：不查网络、不写表、`lookup` 恒返回 None（下游保持改动前行为）。
ENABLE_SECTOR_OFFICIAL = True

EM_HOSTS = ("push2.eastmoney.com", "push2delay.eastmoney.com", "82.push2.eastmoney.com")
EM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
}
SLIST_PATH = "/api/qt/slist/get"

# 抓取预算（单次调用最多联网查多少只新票；防盘中阻塞）
DEFAULT_MAX_FETCH = 120
SLIST_SLEEP = 0.35          # 逐票间隔（风控友好）

# ── 噪声板块过滤 ─────────────────────────────────────────────────────
# 东财 slist 返回的板块里混着大量**统计标签/地域板块**（非题材本体），
# 实测举例：昨日连板_含一字 / 最近多板 / 题材股 / 东方财富热股 / 破增发价股 /
#           高市净率 / QFII重仓 / 近期新高 / 深股通 / 融资融券 / 湖南板块 / 长江三角。
# ⛔ 不过滤 → 「深股通」「高市净率」会被当成题材参与映射。
NOISE_KEYWORDS = (
    "昨日", "最近多板", "题材股", "东方财富热股", "破增发价股", "破发股",
    "高市净率", "高成长股", "近期新高", "百日新高", "QFII", "深股通", "沪股通",
    "融资融券", "转融券", "含可转债", "送转", "回购", "增持", "减持", "股权激励",
    "MSCI", "富时罗素", "标准普尔", "创业板综", "深成500", "沪深300", "中证500",
    "上证50", "上证180", "百元股", "低价股", "微利股", "绩优股", "超跌股",
    "微盘股", "小盘股", "大盘股", "举牌", "参股", "预盈", "预亏",
    "中报", "年报", "季报", "摘帽", "ST股", "次新股", "注册制",
    "长江三角", "珠三角", "京津冀",
)


def _is_noise_board(name: str) -> bool:
    """True = 统计标签 / 地域板块，不参与映射。"""
    if not name:
        return True
    if any(k in name for k in NOISE_KEYWORDS):
        return True
    if name.endswith("板块"):          # 东财地域板块统一以「板块」结尾
        return True
    return False


# ── 别名兜底（聚合名 → 东财板块名列表）──────────────────────────────
# B2（2026-10-11）：别名表已迁至 sector_family.SECTOR_ALIAS（唯一权威源），
# 本模块通过顶部 `from sector_family import SECTOR_ALIAS as _XGB_ALIAS` 复用，不再另写一份。

# ── 打分参数 ─────────────────────────────────────────────────────────
MIN_COVERAGE = 0.5          # 名字相似（非精确）时的最低成分覆盖率
MIN_COVERAGE_EXACT = 0.15   # **精确同名**的覆盖率门槛（放宽）
#   ⚠️ 实证必要（2026-10-08）：选股宝「锂电池」与东财「锂电池 BK1303」字面完全一致，
#   但只有部分成员属于该板块（另一部分属「电池 BK1033」）→ 覆盖率仅 ~0.44，
#   被 0.5 门槛挡掉后错配到「锂电池概念 BK0574」。字面 100% 一致是强证据，
#   不应被成分覆盖率否决 → 精确同名单列低门槛。
STRONG_COVERAGE = 0.8       # 无名字证据时的纯交集门槛（更严）
MAX_BOARDS = 3              # 一个选股宝板块最多映射几个东财板块（名字证据口径）
MAX_BOARDS_ALIAS = 5        # 别名（聚合概念）口径可多列几个成员板块
W_NAME, W_COV = 0.6, 0.4    # 综合分权重


def _name_score(xgb: str, em: str) -> float:
    """名称相似度（不对称规则，防裸双向子串假命中）。

      1.00 精确同名                     锂电池 == 锂电池
      0.90 选股宝名 ⊂ 东财名（短名≥2字）  医药 ⊂ 医药生物 / 机器人 ⊂ 机器人概念
      0.80 东财名 ⊂ 选股宝名（东财名≥3字） 电池 ⊂ 电池化学品
      ⛔ 东财名 <3 字禁止反向包含 —— 挡「电子 ⊂ 电子布」「电网 ⊂ 智能电网」假命中
         （同 09-15 回流卡铁律 `_reflow_name_hit`）。
    """
    if not xgb or not em:
        return 0.0
    if xgb == em:
        return 1.0
    if xgb in em and len(xgb) >= 2:
        return 0.9
    if em in xgb and len(em) >= 3:
        return 0.8
    return 0.0


# ── 路径 ─────────────────────────────────────────────────────────────
def stock_cache_path() -> Path:
    return ROOT / "cache" / "em_stock_sectors.json"


def map_path() -> Path:
    return ROOT / "cache" / "sector_official_map.json"


def _secid(code: str) -> str:
    """东财 secid：6/9 开头 = 沪市(1.)，其余 = 深市(0.)。"""
    c = str(code or "").strip()
    return ("1." if c[:1] in ("6", "9") else "0.") + c


# ── 数据抓取（slist 逐票）────────────────────────────────────────────
def fetch_em_sectors(code: str) -> list[list[str]]:
    """个股 → 所属全部东财板块 [[BK码, 板块名], ...]；失败返回 []。

    接口：push2/api/qt/slist/get?secid={0|1}.{code}&spt=3
    实测（2026-10-08）领湃科技 300530 → 15 个板块，含 电池(BK1033)/锂电池(BK1303)。
    """
    c = str(code or "").strip()
    if not c.isdigit() or len(c) != 6:
        return []
    q = (f"https://{{host}}{SLIST_PATH}?secid={_secid(c)}"
         f"&fields=f12,f13,f14,f3&pn=1&pz=40&po=1&spt=3&fid=f3&invt=2")
    for host in EM_HOSTS:
        d = http_get(q.format(host=host), t=10, headers=EM_HEADERS, retries=1)
        if not isinstance(d, dict):
            continue
        diff = (d.get("data") or {}).get("diff")
        if not diff:
            continue
        rows = list(diff.values()) if isinstance(diff, dict) else diff
        out = []
        for r in rows:
            bk = str(r.get("f12") or "").strip()
            nm = str(r.get("f14") or "").strip()
            if bk and nm:
                out.append([bk, nm])
        if out:
            return out
    return []


def load_stock_cache() -> dict[str, Any]:
    d = load_json(stock_cache_path()) or {}
    return d if isinstance(d, dict) else {}


def save_stock_cache(cache: dict[str, Any]) -> None:
    save_json(stock_cache_path(), cache)


def ensure_codes(codes: Iterable[str], cache: Optional[dict] = None,
                 max_fetch: int = DEFAULT_MAX_FETCH, sleep: float = SLIST_SLEEP) -> dict:
    """补查缺失个股的东财板块归属（增量·长期缓存）。

    个股所属板块极少变化 → 缓存长期有效，跨日复用；只在遇到新 code 时联网。
    返回更新后的缓存 dict。
    """
    cache = cache if cache is not None else load_stock_cache()
    if not ENABLE_SECTOR_OFFICIAL:
        return cache
    todo = [c for c in dict.fromkeys(str(x).strip() for x in codes)
            if c and c.isdigit() and len(c) == 6 and c not in cache]
    for i, c in enumerate(todo[:max_fetch]):
        boards = fetch_em_sectors(c)
        cache[c] = {"boards": boards, "ts": int(time.time())}
        if i < len(todo) - 1 and sleep:
            time.sleep(sleep)
    if todo:
        save_stock_cache(cache)
    return cache


# ── 映射解析 ─────────────────────────────────────────────────────────
def resolve_sector(name: str, member_codes: Iterable[str],
                   cache: Optional[dict] = None) -> dict:
    """选股宝板块名 + 成员票 → 东财官方板块。

    返回 {"boards": [{"bk","name","score","coverage"}...], "method", "members"}
      method: name+members（名字证据+成分交集）| members（纯交集）| alias（别名表）| none
    """
    cache = cache if cache is not None else load_stock_cache()
    cnt: Counter = Counter()
    bkof: dict[str, str] = {}
    # 全局反向索引（名 → BK）：别名兜底时成员票未必命中该板块，仍需补上官方码
    bk_index: dict[str, str] = {}
    for row in cache.values():
        for bk, nm in ((row or {}).get("boards") or []):
            bk_index.setdefault(str(nm), str(bk))
    valid = 0
    for c in member_codes:
        row = cache.get(str(c).strip())
        boards = (row or {}).get("boards") if isinstance(row, dict) else None
        if not boards:
            continue
        valid += 1
        for bk, nm in boards:
            if _is_noise_board(nm):
                continue
            cnt[nm] += 1
            bkof[nm] = bk

    def _hit(nm: str, n: int) -> dict:
        return {"bk": bkof.get(nm) or bk_index.get(nm, ""), "name": nm,
                "score": round(_name_score(name, nm), 3),
                "coverage": round(n / valid, 3) if valid else 0.0}

    # ① 名字证据 + 成分交集（最可靠）
    #    精确同名 → 低覆盖率门槛（字面一致即强证据）；近似名 → 常规门槛
    cands = []
    for nm, n in cnt.items():
        cov = n / valid if valid else 0.0
        sc = _name_score(name, nm)
        need = MIN_COVERAGE_EXACT if sc >= 1.0 else MIN_COVERAGE
        if sc > 0 and cov >= need:
            cands.append((W_NAME * sc + W_COV * cov, sc, cov, nm, n))
    if cands:
        # 排序：**名字相似度优先**（精确同名 → 简称 → 反向包含），再按综合分、覆盖率。
        # ⚠️ 实证必要：选股宝「锂电池」下，「锂电池概念(BK0574)」成分覆盖更高（0.78）
        # 会压过字面完全一致的「锂电池(BK1303)」（0.44）——但用户要的是"名字对齐官方"，
        # 字面一致必须排第一，覆盖率只用于内部排序。
        cands.sort(key=lambda x: (-x[1], -x[0], -x[2]))
        return {"boards": [_hit(nm, n) for _, _, _, nm, n in cands[:MAX_BOARDS]],
                "method": "name+members", "members": valid}

    # ② 别名兜底（东财无同名板块的聚合概念，如「大消费」；复用 sector_family 唯一权威源）
    if name in _XGB_ALIAS:
        return {"boards": [_hit(nm, cnt.get(nm, 0)) for nm in _XGB_ALIAS[name][:MAX_BOARDS_ALIAS]],
                "method": "alias", "members": valid}

    # ③ 纯交集兜底（只有足够多成员、且覆盖极高才敢认）
    if valid >= 3:
        strong = [(nm, n) for nm, n in cnt.most_common() if n / valid >= STRONG_COVERAGE]
        if strong:
            return {"boards": [_hit(nm, n) for nm, n in strong[:MAX_BOARDS]],
                    "method": "members", "members": valid}

    return {"boards": [], "method": "none", "members": valid}


# ── 映射表维护 ───────────────────────────────────────────────────────
def member_codes_of(sector: dict) -> list[str]:
    """从 top5 板块条目里收集成员票代码（leader + followers + zt_stocks）。"""
    out: list[str] = []

    def _add(x: Any) -> None:
        c = str((x or "") if not isinstance(x, dict) else (x.get("code") or "")).strip()
        c = c.split(".")[0]
        if c.isdigit() and len(c) == 6:
            out.append(c)

    _add((sector.get("leader") or {}).get("code"))
    for f in (sector.get("followers") or []):
        _add(f)
    for z in (sector.get("zt_stocks") or []):
        _add(z)
    return list(dict.fromkeys(out))


def load_map() -> dict[str, Any]:
    d = load_json(map_path()) or {}
    return d if isinstance(d, dict) else {}


def build_map(top5: list[dict], cache: Optional[dict] = None,
              prior: Optional[dict] = None, max_fetch: int = DEFAULT_MAX_FETCH) -> dict:
    """用 top5 生成/刷新映射表（保留历史条目，只增不减）。"""
    cache = cache if cache is not None else load_stock_cache()
    m = dict(prior or load_map())
    all_codes: list[str] = []
    for s in (top5 or []):
        all_codes.extend(member_codes_of(s))
    ensure_codes(all_codes, cache=cache, max_fetch=max_fetch)
    now = int(time.time())
    for s in (top5 or []):
        nm = str(s.get("name") or "").strip()
        if not nm:
            continue
        r = resolve_sector(nm, member_codes_of(s), cache=cache)
        r["updated"] = now
        # ⚠️ 已有更优结果时不被降级覆盖（method 优先级 name+members > members > alias > none）
        old = m.get(nm) or {}
        if _rank(r) >= _rank(old):
            m[nm] = r
    return m


_METHOD_RANK = {"name+members": 3, "members": 2, "alias": 1, "none": 0}


def _rank(r: Optional[dict]) -> int:
    if not isinstance(r, dict):
        return -1
    if not (r.get("boards") or []):
        return 0
    return _METHOD_RANK.get(str(r.get("method") or ""), 0)


def save_map(m: dict) -> None:
    save_json(map_path(), m)


_MAP_MEM: dict[str, Any] = {"mtime": None, "data": {}}


def _map_cached() -> dict:
    """映射表进程内缓存（按文件 mtime 失效）。

    在线消费端（intraday_pulse 每 30s 一轮）不必每轮读盘。
    """
    p = map_path()
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {}
    if _MAP_MEM["mtime"] != mt:
        _MAP_MEM["data"] = load_map()
        _MAP_MEM["mtime"] = mt
    return _MAP_MEM["data"]


def lookup(name: str) -> Optional[dict]:
    """在线消费入口（纯读表 + 进程内缓存，零网络请求）。返回 {"boards":[...], "method"} 或 None。"""
    if not ENABLE_SECTOR_OFFICIAL or not name:
        return None
    r = _map_cached().get(str(name).strip())
    if not isinstance(r, dict) or not (r.get("boards") or []):
        return None
    return r


def official_display(name: str, sep: str = "/") -> str:
    """给卡面/展示用：返回东财官方板块名串；未映射返回空串（调用方保持原名）。

    例：锂电池 → "锂电池"        大消费 → "食品饮料/商贸零售/一般零售"
    """
    r = lookup(name)
    if not r:
        return ""
    return sep.join(str(b.get("name")) for b in r["boards"] if b.get("name"))


def official_tag(name: str, min_score: float = 0.8) -> str:
    """卡面上挂在板块名后的官方码，如 "(BK1303)"；无充分证据返回 ""。

    ⛔ 只在**名字证据充分**（score ≥ `min_score`，即精确同名 1.0 / 简称 0.9 /
       反向包含 0.8）时才标码。聚合别名（大消费 → 食品饮料…）**不标** ——
       否则「大消费(BK0438)」会把子板块码冒充成聚合题材的官方身份，属误导。
    """
    r = lookup(name)
    if not r:
        return ""
    boards = r.get("boards") or []
    if not boards:
        return ""
    b = boards[0]
    if not b.get("bk") or float(b.get("score") or 0) < min_score:
        return ""
    return f"({b['bk']})"


# ── CLI ──────────────────────────────────────────────────────────────
def _latest_top5() -> list[dict]:
    d = load_json(ROOT / "cache" / "pulse_snapshots" / "latest.json") or {}
    return d.get("top5") or [] if isinstance(d, dict) else []


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="选股宝板块 → 东财官方板块 映射层")
    ap.add_argument("--build", action="store_true", help="读 latest.json 的 top5，刷新映射表")
    ap.add_argument("--show", action="store_true", help="打印现有映射表")
    ap.add_argument("--codes", default="", help="调试：逗号分隔个股代码，打印其东财板块")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    a = ap.parse_args(argv)

    if a.codes:
        cache = ensure_codes([x.strip() for x in a.codes.split(",") if x.strip()])
        for c in [x.strip() for x in a.codes.split(",") if x.strip()]:
            bs = (cache.get(c) or {}).get("boards") or []
            print(f"{c}: " + " / ".join(f"{nm}({bk})" for bk, nm in bs))
        return 0

    if a.build:
        t5 = _latest_top5()
        if not t5:
            print("latest.json 无 top5，跳过")
            return 1
        before = load_map()
        m = build_map(t5)
        save_map(m)
        print(f"映射表已刷新：{len(before)} → {len(m)} 条")
        _print_map(m, t5)
        return 0

    m = load_map()
    if a.json:
        print(json.dumps(m, ensure_ascii=False, indent=1))
    else:
        _print_map(m, _latest_top5())
    return 0


def _print_map(m: dict, top5: Optional[list[dict]] = None) -> None:
    names = [str(s.get("name")) for s in (top5 or []) if s.get("name")] or list(m)
    print(f"{'选股宝板块':<18} {'方式':<13} {'东财官方板块'}")
    print("-" * 78)
    for nm in names:
        r = m.get(nm)
        if not r:
            print(f"{nm:<18} {'—':<13} (未映射)")
            continue
        bs = r.get("boards") or []
        seg = " / ".join(f"{b['name']}{'(' + b['bk'] + ')' if b.get('bk') else ''}"
                         for b in bs) or "—"
        print(f"{nm:<18} {str(r.get('method')):<13} {seg}   成员{r.get('members', 0)}只")


if __name__ == "__main__":
    raise SystemExit(main())
