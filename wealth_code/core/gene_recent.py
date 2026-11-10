"""近段连板基因（时效口径）—— 回答「这票最近还在被资金用吗」。

## 为什么需要它（2026-11-10 用户裁定）

用户原话：「我这边说的历史连板，是指**类似于冰山冷热这种，近一个月有连板，昨天涨停**」。

起因：AI 复盘时拿 `max_boards_hist` 当"股性强度"，报「锋龙股份历史 18 板、最猛」。
实测两只的差别正好反着：

| 票 | `max_boards_hist` | 近一月最高连板 | 近一月涨停次数 | 真实状态 |
|---|---|---|---|---|
| 锋龙股份 002931 | 18 板 | **2 板** | 3 次 | 18 板是老黄历，最近只在试探 |
| 冰山冷热 000530 | 6 板 | **4 板**（10-13~10-16 四连）| 9 次 | 一个月内反复被资金使用 |

⇒ `max_boards_hist` 是**永久峰值**，会把"很多年前猛过一次"的票算成强基因；
用户要的是**窗口内还在动的股性**。

## 与 `gene_height.prev_streak_boards` 的区别（⛔ 不要混用）

- `prev_streak_boards` = 最近一次**已结束**连板段的最高板 → 只看紧邻的那一段，
  中间夹了单板就被打断。冰山冷热 10-16 四连之后又打了 5 次单板，
  它返回的是 **1**（"上一波没有连板"）—— 对用户的问题答错了。
- `recent_gene` = **窗口内出现过的最高连板**，不管中间夹了多少单板。
  冰山冷热返回 **4**。

两者各自有用途：龙一认定用 `prev_streak_boards`（比"上一波谁高"），
量能豁免用 `recent_gene`（比"最近还在不在动"）。⛔ 不互相替换。

## 缺失语义（⛔ 三种"没有"必须分清）

- `max_boards = None` → **没档案**（该票从未进过涨停序列）＝不知道，⛔ 不当成"难连板"；
- `max_boards = 0` → 有档案但窗口内**确实没涨停过**；
- `has_streak = False` 但 `zt_n > 0` → 最近有涨停，只是没连板。
"""
from __future__ import annotations

import os
from typing import Any, Mapping

try:                                                  # 生产：wealth_code/ 在 sys.path 上
    from core.gene_height import norm_series
except ImportError:                                   # 包里被当包导入时
    from .gene_height import norm_series

# 窗口长度（自然日）：用户口径「近一个月」。⛔ 不用交易日数，易与归档缺日混淆。
DEFAULT_WINDOW_DAYS = int(os.environ.get("GENE_RECENT_DAYS", "31"))

# 量能豁免门槛（用户 2026-11-10 授权「量能可以增加强基因豁免」）：
#   **默认 3 板**（2026-11-10 10:13 用户拍板「3」）。
#   起因：2 板在「昨日涨停候选池」内命中 22/25（88%），池子本身就是昨日涨停候选，
#   人人都刚涨停过、近月几乎都有两连板 ⇒ 门槛形同虚设，量能闸在池内被拆掉。
#   收紧到 3 板后命中 8/25（32%），留下的才是「近月真被资金反复用」的票。
#   ⛔ 可逆：POOL_CALL_GENE_MIN_BOARDS=2 一行回退，便于版本间对照。
GENE_MIN_BOARDS = int(os.environ.get("POOL_CALL_GENE_MIN_BOARDS", "3"))
# 涨停次数门槛：默认 0（不设限），因为板数已经承担了区分度；
#   要更严可设 5（近月 ≥3 板 ∧ 涨停 ≥5 次，11-10 池内命中 7/25）。
GENE_MIN_ZT = int(os.environ.get("POOL_CALL_GENE_MIN_ZT", "0"))


def _as_int(v: Any) -> int | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _shift(date10: str, days: int) -> str | None:
    """自然日回退。`days` 为负数；失败返回 None（调用方按"不截断"处理）。"""
    try:
        from datetime import date, timedelta

        y, m, d = (int(x) for x in str(date10)[:10].split("-"))
        return (date(y, m, d) + timedelta(days=days)).isoformat()
    except Exception:
        return None


def recent_gene(raw_series: Any, *, asof: str | None = None,
                window_days: int = DEFAULT_WINDOW_DAYS,
                avail_since: str | None = None) -> dict[str, Any]:
    """窗口内连板基因事实。纯函数，只读入参序列。

    ``raw_series`` = `[(date, boards), ...]`（`defense_gate.load_zt_series` 的一条）。
    ``asof`` = 窗口终点（通常是**昨日**，因为 09:25 裁决时当日还没收盘涨停池）。
    ``avail_since`` = **数据源**能覆盖的最早交易日（不是该票第一条涨停！）。
    只有它早于窗口起点，才说明序列被截断；该票十天没涨停 ≠ 数据缺失。

    返回：
    ``max_boards``  窗口内最高连板（段内 boards 的最大值，不是累计次数）
    ``zt_n``        窗口内涨停次数
    ``has_streak``  窗口内是否出现过 ≥2 连板
    ``last_date`` / ``last_boards``  窗口内最近一次涨停的日期与板数
    ``window``      (since, asof) —— **如实**：数据源不够长时起点 = `avail_since`
    ``truncated``   True=序列未覆盖完整窗口（`zt_n` 可能少算）／None=不知道）
    ``known``       是否有档案（⛔ False ≠ 弱基因）
    """
    seq = norm_series(raw_series)
    if not seq:
        return {"max_boards": None, "zt_n": 0, "has_streak": False,
                "last_date": None, "last_boards": None,
                "window": (None, asof), "known": False, "truncated": False,
                "label": "无股性档案"}
    end = str(asof)[:10] if asof else seq[-1][0]
    since = _shift(end, -abs(int(window_days)))
    win = [(d, b) for d, b in seq if d <= end and (since is None or d >= since)]
    # ⚠️ 只有「数据源」不够长才算截断。调用方未告知覆盖范围 → None（不知道），
    #    ⛔ 不许拿该票第一条涨停日冒充 —— 那会把「这票十几天没涨停」误报成数据缺失。
    #    仍如实报告：序列不足时 `zt_n` 可能少算，但 `max_boards` 不受影响
    #    （当日 `boards` 是累计板数，截头不改变段内峰值）。
    avail = str(avail_since)[:10] if avail_since else None
    truncated: bool | None = None
    win_start = since
    if since and avail:
        truncated = avail > since
        if truncated:
            win_start = avail
    tail = f"（序列仅覆盖到 {win_start}，次数可能少算）" if truncated else ""
    if not win:
        return {"max_boards": 0, "zt_n": 0, "has_streak": False,
                "last_date": None, "last_boards": None,
                "window": (win_start, end), "known": True, "truncated": truncated,
                "label": f"近 {window_days} 天没涨停{tail}"}
    mx = max(b for _, b in win)
    last_d, last_b = win[-1]
    return {"max_boards": mx, "zt_n": len(win), "has_streak": mx >= 2,
            "last_date": last_d, "last_boards": last_b,
            "window": (win_start, end), "known": True, "truncated": truncated,
            "label": f"近 {window_days} 天最高 {mx} 板 · 涨停 {len(win)} 次{tail}"}


def is_strong(gene: Mapping[str, Any] | None, *,
              min_boards: int = GENE_MIN_BOARDS,
              min_zt: int = GENE_MIN_ZT) -> bool:
    """是否够格叫「强基因」。⛔ 无档案 / 窗口内没涨停 一律 False（不猜、不当优势）。

    ⛔ 硬地板 2：无论环境变量怎么配，**单板永远不算强基因**（`max(2, min_boards)`）。
    默认门槛 `GENE_MIN_BOARDS=3`（用户 2026-11-10 拍板）。
    """
    if not isinstance(gene, Mapping):
        return False
    if not gene.get("known"):
        return False
    mx = _as_int(gene.get("max_boards"))
    n = _as_int(gene.get("zt_n")) or 0
    if mx is None:
        return False
    return mx >= max(2, int(min_boards)) and n >= int(min_zt)


def strong_reason(gene: Mapping[str, Any] | None, *,
                  min_boards: int = GENE_MIN_BOARDS) -> str:
    """人话依据，必须带数字与时点。⛔ 不给空口结论。"""
    if not isinstance(gene, Mapping) or not gene.get("known"):
        return "无股性档案"
    mx = gene.get("max_boards")
    n = gene.get("zt_n") or 0
    last_d = gene.get("last_date") or "—"
    last_b = gene.get("last_boards")
    tail = f"{last_d} {last_b} 板" if last_b else last_d
    return f"近月最高 {mx} 板 · 涨停 {n} 次 · 最近 {tail}"


def load_map(*, days: int = 35, upto: str | None = None,
             root: Any = None) -> dict[str, list[tuple[str, int]]]:
    """读取涨停序列（唯一源 `defense_gate.load_zt_series`，⛔ 不另读 zt_history）。

    `days` 要给足窗口 + 余量：窗口 31 自然日 ≈ 22 交易日，取 35 留缓冲。
    """
    try:
        from core.defense_gate import load_zt_series

        series, _ = load_zt_series(days=days, upto=upto, root=root)
        return series or {}
    except Exception:
        return {}
