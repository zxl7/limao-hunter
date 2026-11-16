#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
题材基座（2026-11-03 用户「以选股宝的题材分类为核心基座，东财只调一点资金看一看」）。

═══════════════════════════════════════════════════════════════════════════════
【为什么要有这一层·现场实证】
11-03 竞价传媒一字潮，系统报了「短剧/互动影游 4 家」，用户看到的是「传媒大爆发」。
追下去发现**同一个题材在我们系统里有三个名字**：

    数据环节                名字                      11-02 家数
    ─────────────────────────────────────────────────────────
    盘后 eod（关键词提炼）   影视传媒                    44
    选股宝 plates 原值       短剧/互动影游 / 传媒 / 游戏   40 / 4 / 7
    盘中精灵（东财 BK）      广告营销·游戏Ⅱ·出版·…       拆成 6 个行业

根因不是「东财不准」，是**我们在选股宝已经分好类的基础上又做了一层关键词二次提炼**
（`THEME_KEYWORDS`），把三类混成「影视传媒」，同时把不相关的票吞进来：

    华是科技  我们判「机器人」   选股宝判「短剧/互动影游」   ← 关键词被 desc 里的"机器人"带走
    联赢激光  我们判「半导体」   选股宝判「业绩增长」        ← 今日涨停因业绩，与半导体无关
    海看股份  我们判「影视传媒」 选股宝判「短剧/互动影游」   ← 被混进大类，家数失真

【本模块的定位】
  · 选股宝 `plates` = **唯一板块口径（基座）**，负责「谁在这条线、几家」
  · 本模块负责：过滤属性标签 / 家数解析与成员交集 / 族归并 / 完整性守卫
  · ⛔ 不产结论、不评分、不排序，纯分类层

【为什么不直接并进 stock_theme.py】
`stock_theme` 的 `THEME_KEYWORDS` 还要产 `logic`（题材逻辑一句话），那层仍有价值。
本模块只接管「板块名」这一个字段，让两件事解耦：**分类归分类，叙事归叙事。**
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "theme_base_v1"

# ── 属性标签（⛔ 不是题材，不参与家数聚合）──────────────────────────────────
# 实证 11-02：233 只涨停里 **29 只** plates[0] 落在这里（12.4%）。
# 它们是「今日涨停的原因归属」，不是「资金炒作的板块」——
# 把「业绩增长」当板块聚合，会得到一个横跨全市场的假板块（13 家，彼此毫无关系）。
ATTR_PLATES: frozenset[str] = frozenset({
    "业绩增长", "业绩预增", "业绩扭亏", "其他", "ST股", "资产重组", "并购重组",
    "高送转", "次新股", "举牌", "股权转让", "拟减持", "拟收购", "超跌反弹",
    "摘帽", "扭亏为盈", "破净", "回购", "增持",
})

# ── 族归并（选股宝板块名 → 族名）────────────────────────────────────────────
# 用途：**只用于和用户心智对接**（"传媒大爆发" vs "短剧/互动影游 40 家"）。
# ⛔ 不用于家数聚合 —— 家数一律按基座算，族只是同一批票的另一种读法。
# ⛔ 只收录有实证的高置信族；⛔ 不许为了"看起来热闹"随手并。
THEME_FAMILIES: dict[str, list[str]] = {
    # 11-03 实证：短剧/互动影游 40 + 传媒 4 + 游戏 7 = 51 只，用户称之为「传媒」。
    # 2026-11-03 补**东财行业名**：盘中短线精灵的板块名来自东财 `hybk`/`industry`，
    #   同一条传媒线被拆成 游戏Ⅱ/影视院线/数字媒体/广告营销/出版/文娱用品 六个行业，
    #   每个只有 1~2 家 → 「板块联动」永远顶不上来。族归并让两条口径能对上号。
    #   ⛔ 只做「同一批票的另一种读法」，不替东财改分类、不进家数主口径。
    "传媒族": [
        # 选股宝 plates 口径
        "短剧/互动影游", "传媒", "游戏", "出版", "影视", "影视院线",
        "影视动漫制作", "广告营销", "数字阅读", "文化传媒", "文娱用品",
        # 东财行业口径（hybk / f100）
        "游戏Ⅱ", "游戏Ⅲ", "数字媒体", "广播电视", "影视院线", "广告营销",
        "出版", "文娱用品", "文化传媒", "传媒",
    ],
    # 10-12 实证：中文在线/掌阅被误归 AI 算力，真算力票在同一条线上被跟风闸整条误杀。
    "算力族": ["AI算力", "云计算数据中心", "液冷服务器", "光通信", "CPO", "算力租赁"],
    # 11-03 13:07 盘中实证：同一批 8 只锂电，圣阳股份 theme=「锂电池」（top5 命中）、
    #   其余 6 只 theme 落到东财行业「电池」→ 卡面被拆成「电池 6只」+「锂电池 1只」两组，
    #   一条线看成两条。A 股「电池」行业基本等价锂电，故归一族。
    #   ⛔ 不并「燃料电池 / 氢能源 / 储能」——那是另一条逻辑，
    #      靠「电池」二字硬并会造出假主线（同 10-12 传媒错归大消费的教训）。
    "锂电池族": [
        "锂电池", "电池", "固态电池", "钠离子电池", "动力电池", "锂电材料",
        "正极材料", "负极材料", "电解液", "隔膜", "六氟磷酸锂", "碳酸锂", "锂矿",
    ],
}

# 族归并开关（默认为开；关掉则 `family_of` 恒返回 None，只报基座名）
ENABLE_THEME_FAMILY = os.environ.get("ENABLE_THEME_FAMILY", "1") != "0"

# 完整性守卫阈值：当日快照 vs 当日涨停池的覆盖率下限。
# 低于阈值 → 判 not_ok，下游应告警。（⛔ 不自动阻断，先告警观察；拦断要有明确证据再开）
GUARD_MIN_COVERAGE = 0.60


# ══════════════════════════════════════════════════════════════════════════════
# ① 板块名归一
# ══════════════════════════════════════════════════════════════════════════════
def is_attr_plate(name: Any) -> bool:
    """是不是属性标签（业绩/ST/重组…），而非真题材。"""
    return str(name or "").strip() in ATTR_PLATES


def theme_of_plates(plates: Iterable[Any] | None) -> str | None:
    """选股宝 `plates` → 基座板块名（第一个非属性标签项）。

    ⛔ 全部是属性标签 → 返回 None（真不知道它归哪个板块），
    **不要**退化成 "业绩增长" 去聚合，也不要编一个 "其他"。
    """
    for p in (plates or []):
        nm = str(p or "").strip()
        if nm and not is_attr_plate(nm):
            return nm
    return None


def family_of(theme: Any) -> str | None:
    """基座板块名 → 族名（没有归族 → None）。"""
    if not ENABLE_THEME_FAMILY:
        return None
    t = str(theme or "").strip()
    if not t:
        return None
    for fam, members in THEME_FAMILIES.items():
        if t in members:
            return fam
    return None


def display_name(theme: Any, *, prefer_family: bool = False) -> str:
    """卡面展示名。prefer_family=True 时优先族名（"传媒族"），否则用基座名。"""
    t = str(theme or "").strip()
    if not t:
        return "—"
    if prefer_family:
        fam = family_of(t)
        if fam:
            return fam
    return t


# ══════════════════════════════════════════════════════════════════════════════
# ② 家数聚合（⛔ 基座口径·唯一算法）
# ══════════════════════════════════════════════════════════════════════════════
def parse_zt_count(value: Any) -> int | None:
    """家数契约：只收有限非负整数；缺失/布尔/负数/小数不补零。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
        return int(n) if math.isfinite(n) and n >= 0 and n.is_integer() else None
    except (TypeError, ValueError, OverflowError):
        return None


def live_theme_counts(latest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """当轮 Top5 家数契约；精确名字、不归族、不补榜外、不读取峰值。

    source_at 是上游快照时刻，不是假定的接口原始行情时刻。
    同名重复且数值冲突时保留未知，不能挑更大的数字。
    """
    out: dict[str, dict[str, Any]] = {}
    ts = str(latest.get("ts") or "")
    rows = latest.get("top5") or []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name or is_attr_plate(name):
            continue
        n = parse_zt_count(row.get("zt_count"))
        previous = out.get(name)
        if previous is not None and previous["zt_count"] != n:
            n = None
        out[name] = {
            "zt_count": n,
            "zt_count_src": str(row.get("zt_count_src") or "latest_top5_unspecified"),
            "date": ts[:10] or None,
            "source_at": ts or None,
            "scope": "latest.top5",
            "basis": "reported_count",
        }
    return out


def snapshot_theme_members(mapping: dict[str, Any]) -> dict[str, set[str]]:
    """逐日 plates 第一非属性项 → 去重成员；不跨日、不用关键词/族名。"""
    out: dict[str, set[str]] = {}
    for code, row in mapping.items():
        if not isinstance(row, dict):
            continue
        plates = row.get("plates")
        if isinstance(plates, (list, tuple)):
            name = theme_of_plates(plates)
        else:
            # 🔁 2026-11-16 修：同花顺回填的快照没有 `plates`（只有 theme/concepts），
            #    原实现直接 continue → 回填的 19 天对轮动/趋势**完全无效**（实测 zt_series 仍全 null）。
            #    这里退到 `theme`（已是当日池内最热概念），仍保持「不编造」：没有就跳过。
            name = str(row.get("theme") or "").strip()
            if not name:
                continue
        c = str(code or "").strip().split(".")[0]
        if name and c:
            out.setdefault(name, set()).add(c)
    return out


def count_member_limitups(members: Iterable[str] | None,
                         pool_codes: Iterable[str] | None) -> int | None:
    """成员 ∩ 同日池；None 是未知，显式空集合才是 0。调用方负责日期对齐。"""
    if members is None or pool_codes is None:
        return None
    def codes(values):
        return {str(c).strip().split(".")[0] for c in values if str(c or "").strip()}
    return len(codes(members) & codes(pool_codes))


def count_by_theme(rows: Iterable[dict[str, Any]], *, key: str = "theme") -> dict[str, int]:
    """按基座题材统计家数。`None`/空 → 不计入任何题材（单独返回在 `__unclassified__`）。

    ⛔ 属性标签票**不进 `__unclassified__`**（它们有板块归属，只是不入聚合），
    但本函数只看到已是基座名的输入；属性标签在 `theme_of_plates` 就已返回 None。
    """
    out: dict[str, int] = {}
    miss = 0
    for r in (rows or []):
        t = str((r or {}).get(key) or "").strip()
        if not t or t in ("—", "其他"):
            miss += 1
            continue
        out[t] = out.get(t, 0) + 1
    if miss:
        out["__unclassified__"] = miss
    return out


def count_by_family(rows: Iterable[dict[str, Any]], *, key: str = "theme") -> dict[str, int]:
    """按族统计家数（族内成员加总）。⛔ 仅供"用户心智对照"，不替代基座家数。"""
    out: dict[str, int] = {}
    for r in (rows or []):
        t = str((r or {}).get(key) or "").strip()
        fam = family_of(t)
        if not fam:
            continue
        out[fam] = out.get(fam, 0) + 1
    return out


def themes_of_family(family: str) -> list[str]:
    return list(THEME_FAMILIES.get(str(family or ""), []))


# ══════════════════════════════════════════════════════════════════════════════
# ③ 完整性守卫（⚠️ 用户点名的「致命前提」）
# ══════════════════════════════════════════════════════════════════════════════
# 选股宝 `surge_stock` 接口**盘中活·盘后关**：当天没抓到，那天的题材分类就是永久空洞，
# 没有历史可回补。把分类基座压在这上面，必须先能回答「今天抓全了吗」。
#
# 守卫做三件事：
#   1. 覆盖率 = 快照覆盖的涨停票 / 当日涨停池家数 → 低于阈值判 not_ok
#   2. 空壳检测 = 快照存在但 mapping 为空 / 全是属性标签 → 判 empty
#   3. 陈旧检测 = 快照日期 ≠ 目标日期 → 判 stale（⛔ 绝不当今天用）
# ⛔ 守卫只出**判定**，不改数据、不阻断主链；是否拦截由调用方决定（可逆）。
# ══════════════════════════════════════════════════════════════════════════════
def guard_snapshot(
    date: str,
    *,
    cache_dir: str | Path | None = None,
    zt_codes: Iterable[str] | None = None,
    min_coverage: float = GUARD_MIN_COVERAGE,
) -> dict[str, Any]:
    """校验当日题材基座完整性。

    返回 {ok, status, date, covered, zt_total, coverage, missing, why}
      status ∈ ok / not_ok / empty / stale / missing
    ⚠️ `zt_codes` 不传 → 只做 empty/stale 检查，覆盖率为 None（⛔ 不假装 100%）。
    """
    d = str(date or "").strip()
    out: dict[str, Any] = {
        "schema": SCHEMA, "date": d, "ok": False, "status": "missing",
        "covered": None, "zt_total": None, "coverage": None,
        "missing": [], "why": "",
    }
    try:
        from common.stock_theme import theme_snapshot_path, load_theme_snapshot  # 延迟导入防环
    except Exception as e:  # noqa: BLE001
        out["why"] = f"无法加载 stock_theme：{e}"
        return out

    path = theme_snapshot_path(d, cache_dir)
    if not Path(path).exists():
        out["status"] = "missing"
        out["why"] = f"当日快照不存在（{path}）。选股宝接口盘中活·盘后关，这天不可回补。"
        return out

    snap = load_theme_snapshot(d, cache_dir=cache_dir)
    if not snap:
        out["status"] = "empty"
        out["why"] = "快照文件在但 mapping 为空/损坏"
        return out

    mapping = snap["mapping"] or {}
    meta = snap.get("meta") or {}
    snap_date = str(meta.get("snapshot_date") or "").strip()
    if snap_date and d and snap_date != d:
        out["status"] = "stale"
        out["why"] = f"快照日期 {snap_date} ≠ 目标 {d}（⛔ 不许当今天用）"
        return out

    # 盘中生成的快照必然不全（涨停池还没长齐），但 **overwrite=False 会让它永久占位**
    #   → 盘后 eod 读到的是盘中版 → 大面积票没有题材。这里只标记，不判失败
    #   （判失败要等有实证再开，⛔ 先不阻断）。
    ga = str(meta.get("generated_at") or "")
    out["generated_at"] = ga
    out["intraday_snapshot"] = False
    if len(ga) >= 13 and ga[4] == "-" and ga[11] == ":":
        try:
            out["intraday_snapshot"] = int(ga[11:13]) < 15
        except ValueError:
            pass

    # 有基座名的票（属性标签全剔掉的算「无板块」）
    classified = {str(c).zfill(6) for c, v in mapping.items()
                  if theme_of_plates((v or {}).get("plates"))}
    out["covered"] = len(classified)
    out["classified"] = len(classified)
    out["total_in_snapshot"] = len(mapping)

    if not classified:
        out["status"] = "empty"
        out["why"] = "快照内无任何可归类的票（plates 全空或全是属性标签）"
        return out

    if zt_codes is None:
        out["ok"] = True
        out["status"] = "ok"
        out["why"] = "未提供涨停池，仅校验非空/非陈旧（覆盖率未计算）"
        return out

    zt = {str(c).zfill(6) for c in zt_codes if str(c or "").strip()}
    out["zt_total"] = len(zt)
    if not zt:
        out["ok"] = True
        out["status"] = "ok"
        out["why"] = "涨停池为空（非交易日/数据未生成），仅校验快照本身"
        return out

    hit = classified & zt
    missing = sorted(zt - classified)
    cov = len(hit) / len(zt)
    out["coverage"] = round(cov, 4)
    out["covered"] = len(hit)
    out["missing"] = missing
    if cov >= min_coverage:
        out["ok"] = True
        out["status"] = "ok"
        out["why"] = f"覆盖 {len(hit)}/{len(zt)} = {cov:.1%}"
    else:
        out["status"] = "not_ok"
        out["why"] = (f"覆盖仅 {len(hit)}/{len(zt)} = {cov:.1%} < {min_coverage:.0%}，"
                      f"{len(missing)} 只涨停票没有题材")
        if out.get("intraday_snapshot"):
            out["why"] += f"（快照生成于 {out.get('generated_at')}，属盘中版必然不全）"
    return out


def guard_line(g: dict[str, Any] | None) -> str:
    """守卫结果的单行文案（卡面/诊断用）。ok 时返回空串（不占版面）。"""
    if not g or g.get("ok"):
        return ""
    st = g.get("status")
    if st == "missing":
        return "⚠️ 今日题材基座未生成（选股宝接口盘后关闭不可回补）"
    if st == "empty":
        return "⚠️ 今日题材基座为空（抓取失败）"
    if st == "stale":
        return f"⚠️ 题材基座日期不符：{g.get('why')}"
    return f"⚠️ 题材基座不全：{g.get('why')}"


def guard_sidecar(date: str, *, cache_dir: str | Path | None = None,
                  zt_codes: Iterable[str] | None = None) -> Path | None:
    """跑守卫并落 `cache/theme_snapshot/guard_<date>.json`（排查用，不参与决策）。"""
    g = guard_snapshot(date, cache_dir=cache_dir, zt_codes=zt_codes)
    g["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        base = Path(cache_dir) if cache_dir else Path(__file__).resolve().parents[1] / "cache"
        p = base / "theme_snapshot" / f"guard_{str(date or 'latest')}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
        return p
    except Exception:
        return None


if __name__ == "__main__":  # 自检
    import sys
    sys.path.insert(0, ".")
    dd = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    g = guard_snapshot(dd)
    print(json.dumps(g, ensure_ascii=False, indent=1))
