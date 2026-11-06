"""9:25 竞价三环联动「影子上下文」（2026-11-05 用户拍板 A 档）。

职责边界（⛔ 必须守住）：
  · **只计算、只留痕，不参与任何裁决**：不改 zone / view / participate / quota / 仓位 /
    账本 / 结算。字段写进 pool_call 的 base + meta，供逐日攒样本外证据。
  · 三条硬约束：
      1. 缺选股宝 plates 题材 → 不裁决（20 日回测：行业兜底段守卫全线不改善或变差）；
      2. 缺任一环（板块 / 梯队 / 预期）→ 不裁决，不猜、不当通过；
      3. 量能**只作证伪层**（R 失真 / 昨日一字次日巨量开板），⛔ 不进背书分子分母。
  · `POOL_CALL_SECTOR_GAP_CONTEXT=0` → 完全不写任何新字段（base / meta 零感知）。

三环是**串联证据链**，不是三个并列打分项：
  板块（分母：这条线今早值不值高开）→ 梯队（顺序：上层先开、自己跟上，别越位）
  → 量能（证伪：结构是否失真）→ 个股预期（定价：DEV 有没有透支昨日质量）。

证据（scripts/research_sector_gap_backtest.py，20 日 / 18 有效日 / 902 可买票次）：
  · 高开>5%：三环 0~1 环 n=74 均 -3.05%，≥2 环 n=46 均 -0.48%，单调且 7 组阈值同向；
  · 但守卫改善仅 0.3~0.5pct / 胜率 +0.5~0.9pct，⛔ 不足以称提升胜率、⛔ 不得开硬闸；
  · 量能固定阈值与当日横截面分位两次方向反转 ⇒ 定稿只作证伪否决。

数据全部 09:25 前可得（⛔ 无未来函数）：
  quotes      当日 09:25 **全池**竞价报价 {code: {prev_close, auction_price, auction_amount_yi}}
  zt_rows     昨日**完整**涨停池（含 boards / cje_yi / sector / is_20cm）
  plates_map  昨收选股宝题材快照 cache/theme_snapshot/theme_<T-1>.json 的 mapping
  bars        T-1 及更早的日线（只判昨日一字；本模块显式裁掉 T 日及以后）
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent   # 项目根（wealth_code/core/ → 上三级）

SCHEMA = "sector_gap_context_v1"
ENV = "POOL_CALL_SECTOR_GAP_CONTEXT"

# ── 研究定稿阈值（⛔ 影子用，不是生产裁决参数；改动须重跑前后分段）──────────
SECTOR_MIN_PEERS = 2        # 同线至少几家有今早报价
SECTOR_RED_MIN = 0.50       # 同线红开率下限
LADDER_OVERTAKE_MAX = 3.0   # 非线内最高板：自己超出同线上层竞价中位的上限（pct）
DEV_OVERHEAT = 6.0          # 开幅超出自身质量预期太多 → 不给「预期」背书
VOL_R_EXTREME = 60.0        # R 超此值或昨一字 → 量能结构失真
VOL_R_PCTL = 0.75           # 当日全池 R 横截面分位（仅留痕，不打分）
ISOLATED_MIN = 6.0          # 高开脱离同题材中位超过此值 = 孤立高开
HI_GAP_MIN = 5.0            # 影子对照只针对高开票（⛔ 不是「高开不买」硬线）
CREDIT_MIN = 2              # LC 守卫影子口径：≥2/3 环

# 写入 base 的字段白名单。⛔ 绝不含 zone / view / gap_pct / expected_gap_pct /
# gap_vs_expected / auction_r / volume_band 等任何既有裁决字段，避免覆盖。
FIELDS = (
    # A 档约定核心六字段
    "theme_gap_med", "gap_vs_theme", "upper_gap_med", "gap_vs_upper",
    "credits", "R_extreme",
    # 其余留痕字段（加前缀防撞名）
    "linked_schema", "linked_theme", "linked_theme_src", "linked_theme_zt_count",
    "linked_theme_peer_count", "linked_theme_red_rate", "linked_gap_vs_market",
    "linked_lower_gap_med", "linked_theme_max_boards", "linked_is_line_top",
    "linked_ladder_complete", "linked_boards", "linked_auction_r_pct",
    "linked_day_R_p75", "linked_dev", "linked_expected_gap_pct",
    "linked_status", "linked_isolated_high_open", "linked_would_cut",
    "linked_unbuyable",
)


def enabled() -> bool:
    """运行时读开关（⛔ 不做模块级常量缓存，便于测试/回退即时生效）。"""
    return os.environ.get(ENV, "1") not in ("0", "false", "False", "")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _med(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 2)


def main_theme(code: str, yrow: dict | None, mapping: dict | None) -> tuple[str, str]:
    """主题材单一源：选股宝 plates[0] > theme > 昨归档 sector（兜底并标来源）。

    ⛔ 兜底来的 sector 只是「行业」，不是炒作题材；影子判据遇它一律不裁决。
    """
    m = (mapping or {}).get(code) or {}
    if not isinstance(m, dict):
        m = {}
    plates = [str(x).strip() for x in (m.get("plates") or []) if str(x).strip()]
    if plates:
        return plates[0], "plates"
    t = str(m.get("theme") or "").strip()
    if t:
        return t, "theme"
    return str((yrow or {}).get("sector") or "").strip(), "sector"


def _prev_yizi(bars: list[dict] | None, prev_date: str | None) -> bool | None:
    """昨日是否严格一字（开=高=低=收）。缺 → None，不猜。"""
    if not bars or not prev_date:
        return None
    b = next((x for x in bars if str(x.get("date") or "") == prev_date), None)
    if not b:
        return None
    o, h, l, c = (_num(b.get(k)) for k in ("open", "high", "low", "close"))
    if None in (o, h, l, c):
        return None
    return abs(o - h) < 1e-6 and abs(l - h) < 1e-6 and abs(c - h) < 1e-6


def _c_sector(peer_count: int | None, med: float | None, red: float | None,
              min_peers: float, red_min: float) -> bool | None:
    if peer_count is None or peer_count < min_peers or med is None or red is None:
        return None
    return med > 0 and red >= red_min


def _c_ladder(is_line_top: bool, upper_med: float | None, gap_vs_upper: float | None,
              overtake_max: float) -> bool | None:
    """线内最高板 **不额外背书也不否决 → None 不裁决**。

    20 日实测：线内最高板自己高开 n=74 均 -2.68%/胜率 44.6%，比非最高板更差；
    「自己是锚」不等于「有资格高开」，不能白送一分。
    """
    if is_line_top or upper_med is None or gap_vs_upper is None:
        return None
    return upper_med > 0 and gap_vs_upper <= overtake_max


def _c_expected(dev: float | None, overheat: float) -> bool | None:
    return None if dev is None else dev <= overheat


def build_context(*, quotes: dict[str, dict] | None,
                  zt_rows: list[dict] | None,
                  plates_map: dict | None,
                  bars_by_code: dict[str, list[dict]] | None = None,
                  prev_date: str | None = None,
                  expected_gaps: dict[str, float] | None = None,
                  sector_min_peers: float = SECTOR_MIN_PEERS,
                  sector_red_min: float = SECTOR_RED_MIN,
                  ladder_overtake_max: float = LADDER_OVERTAKE_MAX,
                  dev_overheat: float = DEV_OVERHEAT,
                  vol_r_extreme: float = VOL_R_EXTREME,
                  ) -> dict[str, dict[str, Any]]:
    """纯计算：由 09:25 前事实算出全池三环影子上下文 → {code: fields}。

    ⛔ 不读盘、不改入参、不产裁决。IO 在 `build_day_context` 里做。
    """
    q = quotes or {}
    yr = {str(r.get("code") or ""): r for r in (zt_rows or []) if isinstance(r, dict)}
    mp = plates_map or {}
    bars = bars_by_code or {}
    e_map = expected_gaps if isinstance(expected_gaps, dict) else {}
    try:
        from core import expected_gap as _eg
        from core import ai_caliber as _ac
    except ImportError:                                   # pragma: no cover
        from wealth_code.core import expected_gap as _eg  # type: ignore[no-redef]
        from wealth_code.core import ai_caliber as _ac    # type: ignore[no-redef]

    gaps: dict[str, float] = {}
    for c, v in q.items():
        pc, ap = _num((v or {}).get("prev_close")), _num((v or {}).get("auction_price"))
        if pc and pc > 0 and ap and ap > 0:
            gaps[str(c)] = round((ap / pc - 1) * 100, 2)
    market_med = _med(list(gaps.values()))

    theme_of: dict[str, str] = {}
    src_of: dict[str, str] = {}
    for c in q:
        theme_of[str(c)], src_of[str(c)] = main_theme(str(c), yr.get(str(c)), mp)
    # 昨日完整涨停池归线（⛔ 不是候选池，否则梯队/家数被选股结果高估）
    y_groups: dict[str, list[str]] = defaultdict(list)
    for c, r in yr.items():
        t, _ = main_theme(c, r, mp)
        if t and t not in ("其他", "未分类"):
            y_groups[t].append(c)
    today_groups: dict[str, list[str]] = defaultdict(list)
    for c, g in gaps.items():
        t = theme_of.get(c)
        if t and t not in ("其他", "未分类"):
            today_groups[t].append(c)
    theme_max_boards: dict[str, int] = {}
    theme_board_set: dict[str, list[int]] = {}
    for t, codes in y_groups.items():
        bs = [int(_num(yr[c].get("boards")) or 0) for c in codes]
        theme_max_boards[t] = max(bs) if bs else 0
        theme_board_set[t] = sorted(set(bs))

    # 当日全池 R 横截面（仅留痕；⛔ 量能已定稿为证伪层，不用它打分）
    day_Rs: list[float] = []
    for c, v in q.items():
        amt, pcje = _num((v or {}).get("auction_amount_yi")), _num((yr.get(str(c)) or {}).get("cje_yi"))
        if amt is None or pcje is None or pcje <= 0:
            continue
        rr = amt / pcje * 100
        if rr <= vol_r_extreme and _prev_yizi(bars.get(str(c)), prev_date) is not True:
            day_Rs.append(rr)
    day_Rs.sort()
    day_R_p75 = (day_Rs[int((len(day_Rs) - 1) * VOL_R_PCTL)]
                 if len(day_Rs) >= 4 else None)

    out: dict[str, dict[str, Any]] = {}
    for c, gap in gaps.items():
        v = q.get(c) or {}
        y = yr.get(c) or {}
        t, src = theme_of.get(c) or "", src_of.get(c) or ""
        valid = bool(t) and t not in ("其他", "未分类")
        peers = [x for x in today_groups.get(t, []) if x != c] if valid else []
        peer_gaps = [gaps[x] for x in peers]
        tmed = _med(peer_gaps)
        red_rate = (round(sum(1 for g in peer_gaps if g > 0) / len(peer_gaps), 3)
                    if peer_gaps else None)
        boards = int(_num(y.get("boards")) or 0) or None
        upper = [gaps[x] for x in peers
                 if int(_num((yr.get(x) or {}).get("boards")) or 0) >= (boards or 0)]
        lower = [gaps[x] for x in peers
                 if int(_num((yr.get(x) or {}).get("boards")) or 0) < (boards or 0)]
        upper_med, lower_med = _med(upper), _med(lower)
        tmax = theme_max_boards.get(t) if valid else None
        is_line_top = bool(boards and tmax and boards >= tmax)
        bs = theme_board_set.get(t, []) if valid else []
        ladder_complete = bool(bs) and bs == list(range(1, max(bs) + 1))

        E = _num(e_map.get(c))
        if E is None:
            E = _eg.expected_gap_pct(y)
        dev = round(gap - E, 2) if E is not None else None
        amt, pcje = _num(v.get("auction_amount_yi")), _num(y.get("cje_yi"))
        R = (round(amt / pcje * 100, 2)
             if amt is not None and pcje is not None and pcje > 0 else None)
        yizi = _prev_yizi(bars.get(c), prev_date)
        r_extreme = R is None or R > vol_r_extreme or yizi is True

        # ── 三环 + 量能证伪（与研究脚本判据逐字同源）──────────────────────
        c_sector = _c_sector(len(peers) if valid else None, tmed, red_rate,
                             sector_min_peers, sector_red_min)
        c_ladder = _c_ladder(is_line_top,
                             upper_med,
                             round(gap - upper_med, 2) if upper_med is not None else None,
                             ladder_overtake_max)
        c_expected = _c_expected(dev, dev_overheat)
        volume = False if (R is None or r_extreme) else None   # ⛔ 只证伪，不给好背书
        rings = (c_sector, c_ladder, c_expected)
        n = sum(1 for x in rings if x is True)
        known = sum(1 for x in rings if x is not None)
        veto = volume is False
        gap_vs_theme = round(gap - tmed, 2) if tmed is not None else None
        isolated = bool(gap > HI_GAP_MIN and gap_vs_theme is not None
                        and gap_vs_theme > ISOLATED_MIN)
        unbuyable = bool(_ac.unbuyable_gap(gap, bool(y.get("is_20cm"))))

        # ── 状态：只描述数据是否够，⛔ 不是裁决结论 ────────────────────────
        if src != "plates":
            status = "abstain_no_plates"
        elif known < 3:
            status = "abstain_missing_ring"
        elif veto:
            status = "volume_veto"
        else:
            status = "ok"

        # 影子对照：若将来开 LC 守卫，这只票会不会被砍。⛔ 当前恒不据此改 view。
        # 硬约束优先：缺 plates / 缺任一环 / 竞价封死买不进 → 不裁决（False）。
        # ⚠️ 与研究脚本 credit_ok 的唯一差异：veto 与缺环同时命中时，生产按 A 档
        #    「缺任一环不裁决」优先，研究脚本按 veto 优先；影子不改裁决，仅注明口径。
        if src != "plates" or gap <= HI_GAP_MIN or unbuyable:
            would_cut = False
        elif known < 3:
            would_cut = False
        elif veto:
            would_cut = True
        else:
            would_cut = bool(n < CREDIT_MIN or isolated)

        out[c] = {
            "linked_schema": SCHEMA,
            "linked_theme": t or None, "linked_theme_src": src or None,
            "linked_theme_zt_count": len(y_groups.get(t, [])) if valid else None,
            "linked_theme_peer_count": len(peers) if valid else None,
            "linked_theme_red_rate": red_rate,
            "theme_gap_med": tmed,
            "gap_vs_theme": gap_vs_theme,
            "linked_gap_vs_market": (round(gap - market_med, 2)
                                     if market_med is not None else None),
            "linked_boards": boards,
            "linked_theme_max_boards": tmax,
            "linked_is_line_top": is_line_top,
            "linked_ladder_complete": ladder_complete,
            "upper_gap_med": upper_med, "linked_lower_gap_med": lower_med,
            "gap_vs_upper": (round(gap - upper_med, 2)
                             if upper_med is not None else None),
            "linked_auction_r_pct": R, "R_extreme": r_extreme,
            "linked_day_R_p75": round(day_R_p75, 2) if day_R_p75 is not None else None,
            "linked_expected_gap_pct": E, "linked_dev": dev,
            "credits": {"sector": c_sector, "ladder": c_ladder,
                        "expected": c_expected, "volume": volume,
                        "n": n, "known": known, "veto": veto, "max": 3},
            "linked_status": status,
            "linked_isolated_high_open": isolated,
            "linked_would_cut": would_cut,
            "linked_unbuyable": unbuyable,
        }
    return out


def summary(contexts: dict[str, dict] | None,
            candidate_codes: set[str] | None = None) -> dict[str, Any]:
    """meta 留痕统计（⛔ 只读，不参与任何裁决）。"""
    ctx = contexts or {}
    cand = candidate_codes or set()
    def _cnt(rs, key):
        d: dict[str, int] = defaultdict(int)
        for r in rs:
            d[str(r.get(key))] += 1
        return dict(d)
    allr, cr = list(ctx.values()), [ctx[c] for c in cand if c in ctx]
    hi = [r for r in allr if r.get("linked_isolated_high_open")]
    return {
        "schema": SCHEMA,
        "enabled": enabled(),
        "definition": ("三环联动影子: 板块(同题材今早中位>0且红开率>=50%且>=2家) / "
                       "梯队(非线内最高板,上层中位>0且不越位<=3pct) / 预期(DEV<=6); "
                       "量能只证伪(R失真或昨一字); 缺plates或缺任一环不裁决; "
                       "linked_would_cut仅为LC守卫影子对照,⛔不改view/zone/quota/仓位"),
        "source": "xuangubao_plates" if ctx else "missing",
        "quotes_covered": len(ctx),
        "candidates_covered": len(cr),
        "theme_src": _cnt(allr, "linked_theme_src"),
        "status": _cnt(allr, "linked_status"),
        "credits_n": {str(k): sum(1 for r in allr if (r.get("credits") or {}).get("n") == k)
                      for k in range(4)},
        "high_open": {
            "isolated": len(hi),
            "would_cut": sum(1 for r in allr if r.get("linked_would_cut")),
            "volume_veto": sum(1 for r in allr if (r.get("credits") or {}).get("veto")),
        },
        "candidate_status": _cnt(cr, "linked_status"),
        # 排序保证冻结件可复现（跨 PYTHONHASHSEED 不摆动）
        "candidate_would_cut": sorted(c for c in cand
                                      if (ctx.get(c) or {}).get("linked_would_cut")),
    }


# ── 只读 IO 包装（供 pipeline_verify 在 09:25 调用；⛔ pool_call 保持纯函数）──
def load_plates_map(prev_date: str, root: Path | None = None) -> dict:
    """昨收选股宝题材快照 cache/theme_snapshot/theme_<T-1>.json 的 mapping。缺 → {}。"""
    p = (Path(root) if root else ROOT) / "cache" / "theme_snapshot" / f"theme_{prev_date}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    mp = d.get("mapping") if isinstance(d, dict) else None
    return mp if isinstance(mp, dict) else {}


def load_prev_bars(codes, prev_date: str, root: Path | None = None) -> dict[str, list[dict]]:
    """只读 T-1 及更早的日线（⛔ 显式裁掉 T 日，防未来函数）。缺文件 → []。"""
    base = (Path(root) if root else ROOT) / "cache" / "kline_qfq"
    out: dict[str, list[dict]] = {}
    for c in codes:
        try:
            bars = (json.loads((base / f"{c}.json").read_text(encoding="utf-8")) or {}).get("bars") or []
            out[str(c)] = [b for b in bars if str(b.get("date") or "") <= prev_date]
        except Exception:
            out[str(c)] = []
    return out


def build_day_context(*, prev_date: str, quotes: dict | None,
                      zt_rows: list[dict] | None, root: Path | None = None) -> dict[str, dict]:
    """pipeline_verify 唯一入口：读昨收题材 + 昨日日线 → 纯计算上下文。

    缺 plates 快照 → {}（A 档硬约束：全池不裁决、pool_call 不写任何影子字段）。
    """
    plates = load_plates_map(prev_date, root=root)
    if not plates:
        return {}
    bars = load_prev_bars((quotes or {}).keys(), prev_date, root=root)
    return build_context(quotes=quotes, zt_rows=zt_rows, plates_map=plates,
                         bars_by_code=bars, prev_date=prev_date)
