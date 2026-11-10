#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞价一字板 → 炒作方向线索（2026-11-09 用户发现 + 拍板「先看见」）。

用户原话：
  「竞价的时候如果有一字涨停，需要留意他的炒作方向，
    比如今天的西部材料带动商业航天逻辑！！」
  「可以 没问题！！先看见」

⛔ **本模块只回答「今天哪条线在竞价就被点亮」，不参与任何裁决。**
   · 不进 `pool_call` 名额、不进仓位、不改 `view`、不改评分排序；
   · 一字板的隔壁票⛔ 不得因此成为买入理由 ——
     11-06 已量化：同题材有龙头一字封死时，隔壁票 T+1 −1.49%、胜率 40.4%，
     比基准还差（是虹吸不是外溢）。「看见方向」≠「买隔壁」。

## 为什么不能只看「09:15 涨幅≈10%」
2026-11-09 实测：09:15:09 那批 pct≥9.9 的有 259 只，但**绝大多数是撤单虚顶**。
两反例（当日真实序列）：
  · 阳光照明 600261：09:15 5.94(+10.0%) → 09:17 5.93 → 09:20 5.83(+7.96%) → 09:24 5.70(+5.56%)
  · 润贝航科 001316：09:15 30.60(+9.99%) → 09:22 30.40(+9.27%) → 09:23 29.86(+7.33%)
而真一字（西部材料 002149）：09:15 41.51 → 09:26 41.51 → 09:31 41.51，**中间一条记录都没有**。

判据由此而来 —— 利用 `stock_observations` 的**去重语义**：
`_insert_observation` 用 `INSERT OR IGNORE` + `dedup_hash`（同日同源同值只留一条），
所以「**中间没有记录**」在语义上等于「**价格没变过**」。据此：

  sealed  = 窗口内所有观测 pct 均 ≥ 涨停线 − 容差（从未下探）
  confirmed = 末条观测时间 ≥ 09:25（不可撤单期结束后仍封 → 真一字）
  sealed ∧ confirmed          → 竞价一字（可信）
  sealed ∧ ¬confirmed          → 无法确认（只在 09:15~09:19 出现过，可能随后撤单）
  ¬sealed ∧ 首条 ≥ 涨停线     → 竞价虚顶（顶过板又掉下来）

⛔ 三态必须分开报，不许把「无法确认」当「一字」——那就是把 259 只虚顶当方向。

## 方向归属
⛔ **不读 `stocks.industry`**。002149 在库里 `industry='小金属'`，
但资金炒的是「商业航天」——读行业名会把方向线索整个丢掉（11-09 实证）。
题材取 `stock_tags` 的 `board` / `theme`，经 `theme_base.theme_of_plates` 过滤属性标签
（业绩增长/ST/资产重组…），全属性 → `None`，⛔ 不编「其他」。

零依赖纯计算：⛔ 不读盘、不抓行情、不写缓存。调用方负责喂数据。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Iterable, Sequence

SCHEMA = "auction_seal_v1"

ENABLE_AUCTION_SEAL = os.environ.get("ENABLE_AUCTION_SEAL", "1") != "0"

# 竞价窗口（含不可撤单期）。09:15 起，09:30 止（开盘后不再算竞价）。
WINDOW_START = "09:15:00"
WINDOW_END = "09:30:00"

# 「末条仍封」的最小时间 = 09:25（不可撤单期于 09:25 结束）。
# ⛔ 早于此刻的末条不能证明「竞价结束时仍封」，只能算无法确认。
SEAL_CONFIRM_TS = "09:25:00"

# 距涨停线的容差（百分点）。与 `core.ai_caliber.PHYSICAL_LIMIT_TOL` 同源；
# `test_auction_seal.py` 有等值断言锁住漂移。
LIMIT_TOL = 0.2

STATE_SEALED = "sealed"          # 全程未下探 + 竞价结束仍封 = 真一字
STATE_UNCONFIRMED = "unconfirmed"  # 全程未下探，但末条 < 09:25 → 不知道
STATE_WOBBLED = "wobbled"        # 顶过板又掉下来 = 虚顶
STATE_NONE = "none"              # 没顶过板

# 方向里最多列几只（卡面只给线索，不给全表）
MAX_ITEMS = 12


def limit_up_pct(is_20cm: Any = False) -> float:
    """涨停幅度（%）：20cm 标的 20，其余 10。

    ⚠️ 与 `core.ai_caliber.limit_up_pct` **必须同值**（测试有断言）。
    本模块保持零依赖，故本地实现一份；改动口径必须两处同改。
    """
    return 20.0 if is_20cm else 10.0


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _hhmmss(ts: Any) -> str:
    """`2026-11-09T09:15:09+08:00` → `09:15:09`；不可解析 → ''。"""
    raw = str(ts or "")
    if "T" in raw:
        raw = raw.split("T", 1)[1]
    raw = raw.split("+", 1)[0].split("Z", 1)[0].strip()
    return raw[:8] if len(raw) >= 8 else ""


def classify_series(
    series: Sequence[dict[str, Any]],
    *,
    is_20cm: Any = False,
    limit_fn: Callable[[Any], float] | None = None,
    tol: float = LIMIT_TOL,
    window_start: str = WINDOW_START,
    window_end: str = WINDOW_END,
    confirm_ts: str = SEAL_CONFIRM_TS,
) -> dict[str, Any]:
    """单只票的竞价封板判定（纯函数）。

    series: `[{"ts": "09:15:09", "pct": 9.99, "price": 41.51}, ...]`，按时间升序。
    返回 `{state, line, first_ts, last_ts, n_obs, peak_pct, low_pct}`。
    ⛔ 缺 pct 的观测直接跳过（未观测 ≠ 没下探）。
    """
    fn = limit_fn or limit_up_pct
    line = fn(is_20cm) - tol

    rows: list[tuple[str, float]] = []
    for row in series or []:
        if not isinstance(row, dict):
            continue
        hh = _hhmmss(row.get("ts"))
        pct = _f(row.get("pct"))
        if not hh or pct is None:
            continue
        if hh < window_start or hh > window_end:
            continue
        rows.append((hh, pct))

    if not rows:
        return {"state": STATE_NONE, "line": line, "first_ts": "", "last_ts": "",
                "n_obs": 0, "peak_pct": None, "low_pct": None}

    rows.sort(key=lambda r: r[0])
    first_ts, first_pct = rows[0]
    last_ts, last_pct = rows[-1]
    low_pct = min(p for _, p in rows)
    peak_pct = max(p for _, p in rows)

    if first_pct < line and peak_pct < line:
        state = STATE_NONE
    elif low_pct < line:
        # 中途下探过 → 撤单虚顶。⛔ 末条回封也不算「一字」：
        # 09:42 回封的润贝航科不是一字，是二次打板。
        state = STATE_WOBBLED
    elif last_ts >= confirm_ts:
        state = STATE_SEALED
    else:
        state = STATE_UNCONFIRMED

    return {"state": state, "line": line, "first_ts": first_ts, "last_ts": last_ts,
            "n_obs": len(rows), "peak_pct": peak_pct, "low_pct": low_pct}


def pick_theme(plates: Iterable[Any] | None,
               *, attr_filter: Callable[[Any], bool] | None = None) -> str | None:
    """板块名列表 → 第一个非属性标签项。全属性 / 空 → None。

    ⛔ 与 `common/theme_base.theme_of_plates` 同语义。调用方应优先传入真正的
       `theme_of_plates`；本函数只是零依赖兜底（attr_filter 缺省时不过滤）。
    """
    for p in (plates or []):
        nm = str(p or "").strip()
        if not nm:
            continue
        if attr_filter is not None and attr_filter(nm):
            continue
        return nm
    return None


def build_seals(
    series_by_code: dict[str, Sequence[dict[str, Any]]],
    *,
    meta_by_code: dict[str, dict[str, Any]] | None = None,
    plates_by_code: dict[str, Sequence[str]] | None = None,
    attr_filter: Callable[[Any], bool] | None = None,
    limit_fn: Callable[[Any], float] | None = None,
    max_items: int = MAX_ITEMS,
) -> dict[str, Any]:
    """批量判定 → 竞价一字清单 + 方向聚合（纯函数）。

    series_by_code:  `{code: [{ts, pct, price}, ...]}`（当日 09:15~09:30 观测）
    meta_by_code:    `{code: {"name": str, "is_20cm": bool, "industry": str}}`
    plates_by_code:  `{code: [板块名, ...]}` —— **必须是题材标签，不是行业名**
    attr_filter:     `theme_base.is_attr_plate`（建议传，过滤业绩增长/ST/重组）

    返回 `{schema, items, themes, states, meta}`：
      · `items` 只含 `sealed`（真一字），按 `last_ts` 降序（越晚确认越硬）
      · `states` 带全量三态计数，让「多少只无法确认」可见 —— ⛔ 不藏
    """
    meta_by_code = meta_by_code or {}
    plates_by_code = plates_by_code or {}

    items: list[dict[str, Any]] = []
    counts = {STATE_SEALED: 0, STATE_UNCONFIRMED: 0, STATE_WOBBLED: 0, STATE_NONE: 0}
    wobbled_names: list[dict[str, Any]] = []

    for code, series in (series_by_code or {}).items():
        meta = meta_by_code.get(code) or {}
        verdict = classify_series(series, is_20cm=meta.get("is_20cm"),
                                  limit_fn=limit_fn)
        state = verdict["state"]
        counts[state] = counts.get(state, 0) + 1

        if state == STATE_WOBBLED and len(wobbled_names) < max_items:
            wobbled_names.append({
                "code": code, "name": meta.get("name"),
                "peak_pct": verdict["peak_pct"], "low_pct": verdict["low_pct"],
            })
        if state != STATE_SEALED:
            continue

        plates = list(plates_by_code.get(code) or [])
        theme = pick_theme(plates, attr_filter=attr_filter)
        items.append({
            "code": code,
            "name": meta.get("name"),
            "pct": verdict["peak_pct"],
            "theme": theme,
            "plates": plates,
            # ⚠️ 仅作对照展示：行业名不是炒作方向（002149 行业=小金属，题材=商业航天）
            "industry": meta.get("industry"),
            "first_ts": verdict["first_ts"],
            "last_ts": verdict["last_ts"],
            "n_obs": verdict["n_obs"],
        })

    # 越晚确认越硬：09:26 仍封优先于 09:25 仍封
    items.sort(key=lambda it: (it["last_ts"], it["n_obs"]), reverse=True)

    # 方向聚合：按封面题材计家数（⛔ 不按行业）
    theme_map: dict[str, list[str]] = {}
    for it in items:
        th = it.get("theme")
        if th:
            theme_map.setdefault(th, []).append(it["code"])
    themes = [
        {"theme": th, "n": len(codes), "codes": codes}
        for th, codes in sorted(theme_map.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    known = counts[STATE_SEALED] + counts[STATE_UNCONFIRMED]
    return {
        "schema": SCHEMA,
        "items": items[:max_items],
        "themes": themes,
        "states": {
            "sealed": counts[STATE_SEALED],
            "unconfirmed": counts[STATE_UNCONFIRMED],
            "wobbled": counts[STATE_WOBBLED],
            "none": counts[STATE_NONE],
        },
        "wobbled_sample": wobbled_names,
        "meta": {
            "window": [WINDOW_START, WINDOW_END],
            "confirm_ts": SEAL_CONFIRM_TS,
            "limit_tol": LIMIT_TOL,
            "coverage": (round(counts[STATE_SEALED] / known, 3) if known else None),
            "note": "只做方向线索；⛔ 不进名额/仓位/买入",
        },
    }


def card_lines(result: dict[str, Any] | None) -> list[str]:
    """把结果压成卡面可用的短句（调用方负责包成飞书 md）。

    ⛔ 无数据 → 空列表（不写「无一字」——抓不到 ≠ 没有）。
    """
    if not isinstance(result, dict) or result.get("schema") != SCHEMA:
        return []
    items = result.get("items") or []
    states = result.get("states") or {}
    # ⛔ 三样都没有才不出卡：有票顶过板（哪怕没确认）就得说，⛔ 不许静默成「无一字」
    if (not items and not states.get("wobbled")
            and not states.get("unconfirmed")):
        return []

    lines: list[str] = []
    themes = result.get("themes") or []
    if themes:
        head = "、".join(f"{t['theme']}{t['n']}只" for t in themes[:4])
        lines.append(f"竞价一字 **{len(items)}** 只 → 方向：{head}")
        for it in items[:6]:
            th = it.get("theme") or "题材未标注"
            lines.append(f"· {it['name']}（{it['code']}）{it['pct']:+.2f}% → **{th}**")
    elif items:
        lines.append(f"竞价一字 **{len(items)}** 只，但题材全部未标注（⛔ 不编方向）")
    elif states.get("unconfirmed"):
        # ⏱️ 时序诚实：卡可能生成在 09:25 后、09:26 快照写入前 → 「还没确认」≠「没有」。
        lines.append(f"{states['unconfirmed']} 只顶过板但末条早于 09:25 → 一字尚未确认")

    unc = states.get("unconfirmed") or 0
    wob = states.get("wobbled") or 0
    bits = []
    if unc and items:
        bits.append(f"{unc} 只未到确认时点")
    if wob:
        bits.append(f"{wob} 只顶板后撤单（虚顶）")
    if bits:
        lines.append("<font color='grey'>" + "；".join(bits) + "</font>")
    return lines
