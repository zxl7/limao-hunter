# -*- coding: utf-8 -*-
"""历史涨停基因（股性）判据 —— 单一真源。

⛔ 术语（2026-10-22 用户定）：「惯犯 / 偶犯」是司法用语，**不许上卡、不许进对话**；
   统一叫「连板龙」（接力强·能连）/「难连板」（涨停之后基本不连）。

为什么单独成模块：同一套判据既被**卡面渲染**（`pipelines/build_cards.py`）用，
又被**盘后选股**（`pipelines/pipeline_eod.py` 的低位连板龙雷达）用。
判据写在两处必然漂移（一处改了另一处没改，就会「卡上说连板龙、池子却不认」），
故抽到 core 层，两边都调这里。

数据源：`cache/zt_genes/hist_{code}.json`（新浪不复权日 K 自算·6 年窗，
由 `scripts/zt_genes.py hist` 产出）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

# 晋级率档位（历史「涨停次日再涨停」%）
GENE_HABITUAL_MIN = 40.0   # ≥40% → 连板龙（接力强·优先）
GENE_WEAK_MAX = 15.0       # <15%  → 难连板（接力弱·降级）
GENE_MIN_HIST_YEARS = 1.0  # 历史窗口不足 1 年 → 年均失真，单独标「历史短」

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache" / "zt_genes"


def load_hist_gene(code: Any, cache_dir: Path | None = None) -> dict:
    """读该票历史涨停基因档案；缺失/异常 → {}（诚实省略，不硬凑）。"""
    code = str(code or "").strip()
    if len(code) != 6:
        return {}
    path = (cache_dir or _CACHE_DIR) / f"hist_{code}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def hist_annual_avg(g: dict) -> float | None:
    """历史年均涨停次数 = 全历史涨停次数 ÷ 历史年数（`first_day`→`last_day`）。

    ⛔ 用途＝给「连板龙」加**样本量门槛**时的基准（2026-10-22 用户定）：
       判据是「**近一年涨停次数 > 该股自身历史年均**」，⛔ 不是固定值（如 ≥8 次）。
       理由（用户原话）：「你不能说今年才刚开始两天你就找大于8的，肯定找不出来呀」——
       固定值在次新股 / 窗口起始段必然误杀；用自身年均＝**自适应**，语义正是
       「它现在是不是处在自己的活跃期」。

    返回 None 的情形：日期缺失/格式异常、或历史窗口 < `GENE_MIN_HIST_YEARS`
    （不足 1 年时年均被极小分母放大，宁可诚实标「历史短」也不给假门槛）。
    """
    try:
        a = datetime.strptime(str(g.get("first_day") or ""), "%Y%m%d")
        b = datetime.strptime(str(g.get("last_day") or ""), "%Y%m%d")
        years = (b - a).days / 365.25
        total = float(g.get("zt_total") or 0)
    except (TypeError, ValueError):
        return None
    if years < GENE_MIN_HIST_YEARS:
        return None
    return total / years


def gene_grade(g: dict) -> tuple[str, str]:
    """股性档 → (档名, 颜色)。

    ≥40% 且 近一年涨停次数 **> 该股历史年均** → 连板龙
    ≥40% 但 次数未超年均 → 连板龙·次数少（能力像，次数不够·⛔ 不当同档给足仓位）
    ≥40% 但 历史不足 1 年 → 连板龙·历史短（年均失真·只作参考）
    15~40% → 普通
    <15%  → 难连板
    数据缺失 → ("", "")（诚实省略·不误判）
    """
    pr = g.get("promo_rate_hist")
    if pr is None:
        return "", ""
    try:
        v = float(pr)
    except (TypeError, ValueError):
        return "", ""
    if v >= GENE_HABITUAL_MIN:
        base = hist_annual_avg(g)
        if base is None:
            return "连板龙·历史短", "red"
        try:
            zt = float(g.get("zt_250") or 0)
        except (TypeError, ValueError):
            zt = 0.0
        return ("连板龙", "red") if zt > base else ("连板龙·次数少", "red")
    if v < GENE_WEAK_MAX:
        return "难连板", "green"
    return "普通", "neutral"


def is_lianban_long(code: Any, cache_dir: Path | None = None) -> bool:
    """严格档「连板龙」（晋级≥40% ∧ 近一年次数 > 历史年均）——雷达只认这一档。"""
    label, _ = gene_grade(load_hist_gene(code, cache_dir))
    return label == "连板龙"


def gene_facts(code: Any, cache_dir: Path | None = None) -> dict:
    """给雷达/卡面用的一次性汇总（数字原样，不做二次加工）。"""
    g = load_hist_gene(code, cache_dir)
    if not g:
        return {}
    label, color = gene_grade(g)
    avg = hist_annual_avg(g)
    return {
        "grade": label,
        "grade_color": color,
        "zt_250": g.get("zt_250"),
        "promo_rate_hist": g.get("promo_rate_hist"),
        "max_boards_hist": g.get("max_boards_hist"),
        "first_prem_open_hist": g.get("first_prem_open_hist"),
        "annual_avg": round(avg, 2) if avg else None,
    }
