#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智能评分层 · 只做「允许集内」的排序/弃权（2026-11-11 计划 P1）

## 它是什么

机械层（`pool_call`）决定**谁能进池**；本模块只在机械**已经允许**的票里，
用历史数据学一个 `p_win`（当日竞价买→收盘卖为正的概率），供上层做三件事：

1. **tie-break**：机械排序键完全相同时，按 `p_win` 决定先后；
2. **abstain**：机械判 watch、且 `p_win` 极低 → 标记不买（⛔ 不能增加 participate）；
3. **sizing**（阶段二、默认关）：`size_mult ∈ [0.6, 1.0]`。

⛔ 硬边界（与计划 §5 一致）：
- 不碰 `view=skip`；不改任何硬闸；不改名额上限；
- 模型缺失/损坏/版本不符/开关 off → **返回空**，行为与纯机械逐字节一致。

## 为什么不是「又一个 AI」

样本只有 **394 票日 / 18 笔真实买入**（2026-11-11 实测）。这个量级只够学一个
≤8 维、强正则的线性模型；更复杂的模型一定过拟合。
所以：**线性 + 强 L2 + walk-forward + 验证不过就不产出模型**。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
# ⛔ 放 config/models（**tracked**）：cache/ 被 .gitignore 忽略，
#    模型若放 cache 会在干净检出/服务器镜像上凭空消失。
MODEL_DIR = ROOT / "config" / "models"
SCHEMA = "ai_tilt_v1"

# ⛔ 特征表是契约：改它必须重训 + 升版本号（旧模型读到不同维度要拒绝加载）
FEATURES = (
    "boards",          # 连板数
    "line_rank",       # 同题材身位（1=龙一，截断到 3）
    "is_core",         # 是否这条线核心
    "gap_pct",         # 竞价涨幅
    "vol_ratio",       # 竞价额 / 需要额（截断 0~2）
    "role",            # 龙头=2 / 准龙头=1 / 其它=0
    "gap_to_top",      # 距全市场最高板（缺失用 -1 + 指示位）
    "gap_top_missing", # 缺失指示位（1=没有该字段）
    "carry_cnt",       # 带动家数（截断 0~10）
    # ── 2026-11-11 正交特征（与机械排序键不重合；用户「开始训练」）──
    "breadth_level",    # 该线竞价广度：2=ok / 1=分化 / 0=不判 / -1=弱
    "prev_zbc",         # 该票昨日炸板次数（0~10）——承接质量
    "prev_seal_ratio",  # 该票昨日封单/流通%（0~5）
)

ROLE_SCORE = {"龙头": 2.0, "准龙头": 1.0, "跟风": 0.0}


class ModelError(Exception):
    """模型不可用（缺失/损坏/维度不符）——上层必须回退到纯机械。"""


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def features(row: Mapping[str, Any], ctx: Mapping[str, Any] | None = None) -> list[float]:
    """从候选/冻结行抽特征。缺失一律**中性值 + 指示位**，⛔ 不把「没数据」当利空。

    `ctx`（可选）＝当日上下文 `{code: {breadth_level, prev_zbc, prev_seal_ratio}}`，
    由 `build_day_context(day)` 生成；缺失时三项取中性值（0），模型仍可用。
    """
    c = (ctx or {}).get(str(row.get("code") or "")) or {}
    boards = _f(row.get("boards"))
    lr = _f(row.get("line_rank"), 9.0)
    is_core = 1.0 if row.get("line_is_core") else 0.0
    gap = _f(row.get("gap_pct"), 0.0)
    amt, need = row.get("amount_yi"), row.get("need_yi")
    vol = 1.0
    if isinstance(amt, (int, float)) and isinstance(need, (int, float)) and need > 0:
        vol = min(max(float(amt) / float(need), 0.0), 2.0)
    role = ROLE_SCORE.get(str(row.get("leader_role") or ""), 0.0)
    gtt = row.get("gap_to_top")
    has_gtt = isinstance(gtt, (int, float))
    gap_to_top = min(_f(gtt), 8.0) if has_gtt else 0.0
    carry = min(max(_f(row.get("carry_cnt")), 0.0), 10.0)
    breadth = min(max(_f(c.get("breadth_level")), -1.0), 2.0)
    prev_zbc = min(max(_f(c.get("prev_zbc")), 0.0), 10.0)
    prev_seal = min(max(_f(c.get("prev_seal_ratio")), 0.0), 5.0)
    return [
        boards,
        min(lr, 3.0),
        is_core,
        gap,
        vol,
        role,
        gap_to_top,
        0.0 if has_gtt else 1.0,
        carry,
        breadth,
        prev_zbc,
        prev_seal,
    ]


def load_model(path: str | Path | None = None) -> dict[str, Any] | None:
    """读模型；任何异常返回 None（调用方回退机械）。⛔ 不抛错、不猜权重。"""
    p = Path(path) if path else None
    if p is None:
        cands = sorted(MODEL_DIR.glob("ai_tilt_v*.json")) if MODEL_DIR.exists() else []
        if not cands:
            return None
        p = cands[-1]
    try:
        m = json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(m, dict):
        return None
    if list(m.get("features") or []) != list(FEATURES):
        return None                     # 维度/顺序不符 → 拒绝加载（防静默错配）
    w, b, mean, std = m.get("weights"), m.get("bias"), m.get("mean"), m.get("std")
    if not (isinstance(w, list) and isinstance(b, (int, float))
            and isinstance(mean, list) and isinstance(std, list)):
        return None
    if len(w) != len(FEATURES) or len(mean) != len(FEATURES) or len(std) != len(FEATURES):
        return None
    return m


def predict(model: Mapping[str, Any], row: Mapping[str, Any],
            ctx: Mapping[str, Any] | None = None) -> float:
    """`p_win` ∈ (0,1)。数值稳定：先标准化，再 sigmoid（截断防止溢出）。"""
    x = features(row, ctx)
    mean = [float(v) for v in model["mean"]]
    std = [float(v) or 1.0 for v in model["std"]]
    w = [float(v) for v in model["weights"]]
    z = float(model["bias"])
    for xi, mi, si, wi in zip(x, mean, std, w):
        z += wi * ((xi - mi) / si)
    z = max(min(z, 30.0), -30.0)
    return 1.0 / (1.0 + math.exp(-z))


def score_rows(model: Mapping[str, Any] | None,
               rows: list[Mapping[str, Any]],
               ctx: Mapping[str, Any] | None = None) -> dict[str, float]:
    """批量打分；模型为空直接返回 {}（上层据此等价于关闭）。"""
    if not model:
        return {}
    out: dict[str, float] = {}
    for r in rows:
        code = str(r.get("code") or "")
        if code:
            out[code] = round(predict(model, r, ctx), 4)
    return out


def advice_payload(model: Mapping[str, Any] | None, rows: list[Mapping[str, Any]],
                   *, mode: str = "shadow", ctx: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """交给 `pool_call` 的建议包（只读、无副作用）。"""
    scores = score_rows(model, rows, ctx)
    return {
        "schema": SCHEMA,
        "mode": mode,
        "model": (model or {}).get("version") or None,
        "scores": scores,
    }


# ── 当日上下文（正交特征）────────────────────────────────────────────────
ZT_DIR = ROOT / "cache" / "zt_history"
_BREADTH_NUM = {"ok": 2.0, "divergent": 1.0, "weak": -1.0}


def _prev_archive(day: str) -> tuple[str, list[dict]] | None:
    """取 day 之前最近一个**完整收盘**涨停池（`zt_list` 缺失或被标 intraday 的跳过）。"""
    days = sorted(p.stem.replace("zt_", "") for p in ZT_DIR.glob("zt_*.json"))
    for d in reversed([x for x in days if x < day]):
        try:
            data = json.loads((ZT_DIR / f"zt_{d}.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if data.get("intraday_only") or "zt_list" not in data:
            continue
        return d, [r for r in (data.get("zt_list") or []) if isinstance(r, dict)]
    return None


def build_day_context(day: str) -> dict[str, dict[str, float]]:
    """当日上下文：`{code: {breadth_level, prev_zbc, prev_seal_ratio}}`。

    · `breadth_level`：该票所属线的竞价广度（ok/分化/不判/弱 → 2/1/0/-1）
    · `prev_zbc` / `prev_seal_ratio`：昨日炸板次数、封单占流通%（承接质量）
    ⛔ 任何一步缺数据都退化为中性值，⛔ 不猜、不编。
    """
    got = _prev_archive(day)
    if not got:
        return {}
    prev, rows = got
    ctx: dict[str, dict[str, float]] = {}
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        try:
            zbc = float(r.get("zbc") or 0)
        except (TypeError, ValueError):
            zbc = 0.0
        seal, lt = r.get("seal"), r.get("lt_yi")
        ratio = 0.0
        try:
            if seal is not None and lt:
                ratio = max(0.0, float(seal) / float(lt) * 100.0)
        except (TypeError, ValueError):
            ratio = 0.0
        ctx[code] = {"prev_zbc": zbc, "prev_seal_ratio": min(ratio, 5.0),
                     "breadth_level": 0.0}

    # 广度：用昨日涨停池分线 + 当日竞价快照现算（与 auction_breadth 同一函数）
    try:
        from .auction_breadth import build_breadth
        from .theme_strength import theme_of_factory
        quotes = json.loads((WC_CACHE / f"auction_quotes-{day}.json").read_text(encoding="utf-8"))
        theme_of, _src = theme_of_factory(prev)
        themes: dict[str, list[dict]] = {}
        for r in rows:
            code = str(r.get("code") or "").strip()
            if code:
                themes.setdefault(theme_of(code, r), []).append(r)
        data = build_breadth(day, themes, quotes)
        lvl_by_theme = {t["theme"]: _BREADTH_NUM.get(str(t.get("level")), 0.0)
                        for t in data.get("themes") or []}
        for r in rows:
            code = str(r.get("code") or "").strip()
            if code in ctx:
                ctx[code]["breadth_level"] = lvl_by_theme.get(theme_of(code, r), 0.0)
    except Exception:  # noqa: BLE001
        pass
    return ctx


# ── 标签计算（训练与贡献报告共用，⛔ 不各自造一套口径）──────────────────────
WC_CACHE = ROOT / "wealth_code" / "cache"
LEDGER_DIR = ROOT / "cache" / "pool_ledger"
KLINE_DIR = ROOT / "cache" / "kline_qfq"


def ledger_ret(day: str, code: str) -> float | None:
    """账本口径：竞价价买 → 当日收盘卖（%）。已结算日优先用它。"""
    try:
        d = json.loads((LEDGER_DIR / f"{day}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for r in d.get("rows") or []:
        if str(r.get("code")) == code and r.get("au_ret") is not None:
            try:
                return float(r["au_ret"])
            except (TypeError, ValueError):
                return None
    return None


def kline_ret(day: str, code: str) -> float | None:
    """未结算日：用竞价价 + 当日收盘现算（同一口径）。缺任一 → None（⛔ 不当 0）。"""
    try:
        q = json.loads((WC_CACHE / f"auction_quotes-{day}.json").read_text(encoding="utf-8"))
        ap = float((q.get(code) or {}).get("auction_price"))
    except Exception:  # noqa: BLE001
        return None
    if not ap or ap <= 0:
        return None
    try:
        bars = json.loads((KLINE_DIR / f"{code}.json").read_text(encoding="utf-8")).get("bars") or []
    except Exception:  # noqa: BLE001
        return None
    for b in bars:
        if str(b.get("date"))[:10] == day and b.get("close") is not None:
            return round((float(b["close"]) / ap - 1) * 100, 3)
    return None


def au_ret(day: str, code: str) -> float | None:
    r = ledger_ret(day, code)
    return r if r is not None else kline_ret(day, code)
