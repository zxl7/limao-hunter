#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 口径层（2026-09-14 用户「全量以 AI 为唯一口径！」）。

背景：同一张竞价验证卡上曾并存两套买入口径 ——
  · 机械轨：`core/signal_eval.py` 按预期区间/量能/孤军门控判 buy → `can_enter`
  · AI 轨：AI 盘后对候选逐票下判断（participate / watch / skip + 自定入场上限 entry_max_gap）
用户 2026-09-14 先统一「卡面显示」，随后拍板「全量以 AI 为唯一口径」——本模块把
**账户净值账本（唯一真实战绩）的成交集合与入场规则**一并切到 AI 口径：

    成交 = AI 判 participate
         ∧ 当日竞价涨幅 ≤ 用户 5% 硬线（USER_ENTRY_MAX_GAP_PCT·2026-09-14 用户纪律）
         ∧ 该票当日有竞价数据（否则无从成交）
    ⛔ AI 自定 entry_max_gap 自 2026-09-14 起降级为留痕字段（ai_declared_limit），
       **不再参与裁决**——AI 决定「买不买」，5% 决定「什么价买得下手」。
    ⛔ 机械 signal_eval 结果降级为「参考信号」：仍照常渲染在卡面（✅ 机械参考段），
       但不再决定买卖与净值战绩。

三条不可违反的边界：
  1. **可逆**：`AI_CALIBER_ACCOUNT = False` → 一键回退机械口径（旧行为逐字不变）。
  2. **诚实降级**：该执行日无任何 AI 决策行（任务漏跑/首日上线）→ 自动回退机械口径，
     并在 summary 标 `caliber="mechanical_fallback"` + 打印显式告警。
     ⛔ 绝不静默按「零成交」结算——那会让净值无声冻结，用户看不出原因。
  3. **不改历史**：账本 append-only，仅影响本次结算的批次；历史行数值不变
     （nav_ledger 重算只按行内 avg_return_pct 复利，不回头改已记行）。

数据源与接缝：
  `wealth_code/cache/ai_decisions.jsonl`（由 `scripts/ai_ledger.py record` 写入，
  decision_date=T 盘后 · trade_date=T+1 执行）。历史行无 trade_date 字段 → 用
  `next_trade_day(decision_date)` 推导（与 pipeline_verify._next_day 同算法）。
  ⚠️ 该文件在 wealth_code/cache，与「AI 判断视图」(`狸猫猎手/cache/ai_view_*.json`)
      **不同目录**，勿混。
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

# core 既可能由财富密码 CLI 导入，也可能由项目根脚本导入；日历在项目根 common/。
HUNTER_ROOT = Path(__file__).resolve().parents[2]
if str(HUNTER_ROOT) not in sys.path:
    sys.path.insert(0, str(HUNTER_ROOT))
from common.trading_calendar import next_a_share_trading_day

# ── 开关（用户拍板「全量以 AI 为唯一口径」）────────────────────────────────
# True  = 账户账本 / 持仓 / 战绩统计口径 = AI 决策（本模块接管）
# False = 一键回退机械口径（signal_eval can_enter，旧行为）
AI_CALIBER_ACCOUNT = True

DEFAULT_ENTRY_MAX_GAP = 3.0     # AI 决策缺 entry_max_gap 时的兜底值（仅留痕·不参与裁决）

# ── 历史口径：5% 高开硬线（2026-09-14 ~ 2026-11-05）────────────────────────────
# 背景：AI 盘后决策自带 entry_max_gap（默认 3.0·可自降到 2.0）→ 一度把当时用户
#   「竞价高开 ≤5% 一律执行」纪律整个架空。实证（09-14 闽东电力 000993）：
#   AI 判 participate（=要买），却因**自己**设的 2% 把 +4.54% 的竞价拒掉；该票当日
#   涨停 +10.01%，账户白白错过 +5.24%。逻辑不自洽：AI 定方向，不该再用隐藏字段否决自己。
# ⚠️ 2026-11-05 17:00 用户已删掉这条线（见下方「逐票质量预期判据」节）：
#   **默认 relative 模式下此常量⛔不参与裁决**，强票可高开、弱票可低开；
#   只在 `AI_EXPECTED_GAP_MODE=absolute` 一键回退时逐字恢复旧行为。
# 常量保留不删：`ladder_heat` / `seal_trend` / `decision_audit` / 卡面旧文案仍引用，
#   多个旧契约测试断言同值；⛔ 引用它≠它生效，新增判据一律走 `gap_zone`。
USER_ENTRY_MAX_GAP_PCT = 5.0
# 单票仓位上限（2026-09-14 用户「买入仓位不超过 50%」·2026-10-30 再次强调）
# ⛔ 单一源 = core/risk_limits.MAX_POSITION_PER_STOCK（此处仅转出，禁再写数字）
#   双路径兜底（`core.*` 与 `wealth_code.core.*` 两种顶层包都要能取到同一个数）
try:  # noqa: E402
    from wealth_code.core.risk_limits import (  # noqa: E402
        MAX_POSITION_PER_STOCK,
        clamp_per_stock as _rl_clamp_per_stock,
    )
except ImportError:  # pragma: no cover
    from core.risk_limits import (  # type: ignore[no-redef]  # noqa: E402
        MAX_POSITION_PER_STOCK,
        clamp_per_stock as _rl_clamp_per_stock,
    )

# 导入后立即做一次范围守卫：上限必须落在 (0,1] 内（0 会让所有仓位变 None，>1 等于没上限）
assert 0 < MAX_POSITION_PER_STOCK <= 1.0, "单票仓位上限必须在 (0,1] 内"

# ── 预期区间门（2026-09-17 用户「A 计划」）────────────────────────────────
# 背景：此前「卡面动态结论」与「账本成交」是**两套独立逻辑**——卡面看预期区间给三档，
#   账本只看 `participate ∧ ≤5%`，实测出现「卡面说继续观察、账本却买入」的自相矛盾。
# 方案 A（用户拍板）= 把卡面判定**下沉为执行条件**，二者共用 `gap_zone` 这**唯一判据源**。
# ⚠️ 下面是 2026-11-05 之前的旧成交式，⛔ 现已失效，仅 `absolute` 回退模式逐字保留：
#    成交 = AI 判 participate ∧ 竞价 ≤ 5% 硬线 ∧ 竞价落在预期区间（含容差）
#    用户口径：「高开太多（超预期）→ 观察」「不及预期 → 放弃」「符合预期 → 买」。
# 现行默认（relative）见下节：成交只判 `DEV = gap − 逐票质量预期`，⛔ 不看绝对开幅，
#    区间上沿 hi 两种模式都不参与，`expected_range` 仅在回退时提供下沿 lo。
EXPECTED_ZONE_TOL = 0.5   # 【仅 absolute 回退模式】预期区间下沿容差（pp）

# ── 逐票质量预期判据（2026-11-05 用户「把 5% 和红开这两条线删掉」）────────────
# 用户原话：「这个5%和红开这两条线你删掉吧，我们还是按照我们的预期管理来计算
#            有些票今天很强势，明天它就可以高开，有些票今天会弱势，那么它低开一样能拉起来」
# 旧链净效果 = 0% < gap ≤ 5%：同日只读审计发现 AI 预期区间下沿 25/26 只 = 0.0，
#   「低于预期」实际线 −0.5%，与「没红开」闸完全重叠（11-05 冻结件 5 只 below 全被 ⑤ 拦）。
#   即这两条绝对线跟「这只票强不强」毫无关系，⛔ 与逐票预期管理自相矛盾。
# 新链：DEV = 实际开幅 − 逐票质量预期（core/expected_gap.py 的 expected_gap_pct）。
#   DEV < EXPECTED_GAP_DEV_MIN → 明显低于预期（放弃）；其余（高开 / 低开 / 超预期）→ 符合。
#   ⛔ 不再有「高开太多」「必须红开」；只保留「竞价封死买不进」这一物理事实
#      （用户既定「一字板不参与」；删 5% 后必须显式落，否则会把一字票当可成交）。
# 历史对照（26 日 / 1001 票次，截至 11-04，只读研究）：
#   旧规则 57/383＝14.9%；DEV≥−2 为 111/607＝18.3%（前 19.9%／后 17.5%）；
#   新放进的 gap>5 票 **48/110＝43.6%**（强票该高开，用户判断成立）；
#   ⚠️ 但新放进的 gap≤0 票仅 **6/130＝4.6%**（低开修复在本样本未获支持）。
#   ⚠️ 竞价封死票 82/102＝80.4% 晋级但物理买不进，⛔ 必须排除，不能计入可买胜率。
#   ⛔ 晋级率 ≠ 买入胜率 ≠ 可执行收益；本改动是执行口径，不是已证明的胜率提升。
EXPECTED_GAP_MODE = os.environ.get("AI_EXPECTED_GAP_MODE", "relative").strip().lower()
try:
    EXPECTED_GAP_DEV_MIN = float(os.environ.get("POOL_CALL_EXPECTED_DEV_MIN", "-2.0"))
except ValueError:
    EXPECTED_GAP_DEV_MIN = -2.0
PHYSICAL_LIMIT_TOL = 0.2     # 竞价价距涨停 0.2pct 内视为封死（防浮点）


def relative_mode() -> bool:
    """True = 逐票预期模式（默认）；`AI_EXPECTED_GAP_MODE=absolute` 逐字回退旧三条线。

    ⚠️ **运行时读环境变量**（不缓存）：架构改动必须可一键回退，且测试/排障时
       无需 reload 模块即可切换；`EXPECTED_GAP_MODE` 只记录启动时默认值。
    """
    return (os.environ.get("AI_EXPECTED_GAP_MODE") or EXPECTED_GAP_MODE).strip().lower() != "absolute"


def limit_up_pct(is_20cm: Any = False) -> float:
    """涨停幅度（%）：20cm 标的 20，其余 10。"""
    return 20.0 if is_20cm else 10.0


def unbuyable_gap(gap_pct: Any, is_20cm: Any = False) -> bool:
    """竞价封死（≥涨停−0.2pct）→ 物理买不进。⛔ 不是「高开太多」的幅度线。"""
    try:
        g = float(gap_pct)
    except (TypeError, ValueError):
        return False
    return math.isfinite(g) and g >= limit_up_pct(is_20cm) - PHYSICAL_LIMIT_TOL

# ── 量能门（2026-09-17 用户「带量高开＝转强」·无量高开＝没有真实资金托底）────────
# AI 判「参与」后，此前**不看竞价量能**（量能只在机械参考层）。用户重视「带量高开」，
# 故把量能纳入 AI 口径：**红开但量能 < 目标×VOLUME_WEAK_RATIO → 降级「观察」**。
# ⚠️ 2026-11-05 起 relative 模式下删掉「必须红开」→ 低开也可能可买，故本门同步
#    覆盖低开（`decide` 里条件为 `relative_mode() or gap > 0`）：「弱票低开要拉起来」
#    同样得有量，⛔ 不许绕过量能举证。absolute 回退仍只拦「红开无量」；
#    绿开/平开在回退模式本就不买。无目标/无数据 → 不判（宽容）。
VOLUME_WEAK_RATIO = 0.7

# ── 量能分档区间（2026-11-05 用户「量能这一块的区间优化一下」）──────────────────
# 病根：上面是**单线二值门**——竞价额 / 计划额 < 0.7 就整批「量不够 → 观察」。
#   计划额来自盘后 VPA 估算，本身是毛估；11-05 实测 18 只有报价的候选里，
#   8 只比值落在 0.22~0.55，一旦前面的闸放行会被一刀切全砍。用户要求改「区间」。
# 分档（唯一判据源：pool_call 裁决 / ai_caliber.decide 账本成交 /  hibernate 阀门共用）：
#   full    ratio >= VOLUME_FULL_RATIO(0.70)   量足 → 正常参与，仓位不打折
#   soft    VOLUME_DRY_RATIO(0.35) <= r < 0.70 量偏弱 → **参与**，按档降仓（不整批否决）
#   dry     r < 0.35                          真没量 → 才观察（保留地板）
#   unknown 缺目标/缺竞价额/目标<=0            不判（宽容，与旧口径一致）
# ⛔ 0.35 地板不拆：31 日真实链路回放「量能软化」把日均 2.79% → 1.30%，
#    被放进来的是**真没量**的票；本改动只救 0.35~0.70 这段，不放 <0.35。
# ⛔ 可逆：`AI_VOLUME_BANDS=0` → 逐字回退旧单线二值门（soft 归 dry）。
VOLUME_FULL_RATIO = 0.70
VOLUME_DRY_RATIO = 0.35
VOLUME_SOFT_BANDS = ((0.50, 0.75), (0.35, 0.50))   # (下沿, 仓位系数)：量越弱仓越轻
AI_VOLUME_BANDS = os.environ.get("AI_VOLUME_BANDS", "1") != "0"


def volume_band(amount_yi: Any, need_yi: Any) -> dict[str, Any]:
    """量能分档判据 —— **唯一实现**（裁决 / 账本 / 阀门禁另写一套）。

    返回 ``{"band": full|soft|dry|unknown, "ratio": float|None,
            "scale": float, "block": bool, "label": str}``。
    ``scale`` = 该档建议仓位系数（1.0 / 0.75 / 0.5 / 0.0），只表达量能，
    ⛔ 不替代单票 50% 硬顶、也不替代名额/平均分配（由调用方再取小）。
    """
    out = {"band": "unknown", "ratio": None, "scale": 1.0, "block": False,
           "label": "无量能目标"}
    try:
        need = float(need_yi)
        amt = float(amount_yi)
    except (TypeError, ValueError):
        return out
    if not (math.isfinite(need) and math.isfinite(amt)) or need <= 0 or amt < 0:
        return out
    ratio = amt / need
    out["ratio"] = round(ratio, 4)
    if ratio >= VOLUME_FULL_RATIO:
        out.update(band="full", scale=1.0, block=False, label="量能达标")
        return out
    if not AI_VOLUME_BANDS or ratio < VOLUME_DRY_RATIO:
        out.update(band="dry", scale=0.0, block=True, label="真没量")
        return out
    scale = 0.5
    for low, coef in VOLUME_SOFT_BANDS:
        if ratio >= low:
            scale = coef
            break
    pct = round(ratio * 100)
    out.update(band="soft", scale=scale, block=False,
               label=f"量偏弱({pct}%·{int(scale * 100)}%仓)")
    return out

# ── 高开接近涨停的连续降权（2026-11-05 用户「大于 6、7 个点要非常小心，降低买入权重」）──
# ⛔ 这不是新硬闸：用户 17:00 已删掉「高开 5%」与「必须红开」两条绝对线，
#    `gap_zone(DEV)` 仍是 09:25 唯一准入判据。本函数只回答「买的话该买多重」，
#    ⛔ 不改 zone / view / participate / quota，⛔ 不把任何票判成 observe/skip。
#
# 依据（40 日名义窗口 / 有效 23 日 / 979 票次，zone=ok 648 只，T+1 账本结算口径）：
#   开幅   5~6%  n=30  −0.28% 胜40.0%   ← 还没坏
#          6~7%  n=19  −3.37% 胜36.8%   ← 拐点
#          7~8%  n=19  −6.91% 胜 5.3%   ← 重灾
#         8~9.8% n=27  −3.00% 胜37.0%
#   ⚠️ 当日收盘口径这几档胜率还有 42~54%（看着不差），换成账本 T+1 才崩 ——
#      又一次「当日/晋级率与可执行收益反向」，故权重只按 T+1 证据定，不按当日胜率。
#   DEV ≥4 透支组 T+1 胜率 27.9%、≥6 组 23.1%（2~4 组 45.2% 最好）→ 第二个降权因子。
#
# 双因子：① 绝对开幅按**涨停幅度归一化**（10cm 的 6/7/8% ≡ 比例 0.60/0.70/0.80；
#   20cm 对称折算，⛔ 不给创业板额外加减分，守 08-13 用户纪律）；② DEV 透支乘子。
#   曲线保持单调（越接近涨停越轻），⛔ 不照 7~8% 那一格的极值做非单调拟合（防过拟合）。
#   下限 GAP_WEIGHT_MIN=0.20 > 0 —— 再危险也留试错仓，绝不归零变成隐形硬闸。
# ⛔ 可逆：`AI_GAP_WEIGHT=0` → weight 恒 1.0、字段全不生效，逐字回到 11-05 20:44 行为。
GAP_NEAR_WEIGHT_BANDS = ((0.80, 0.30), (0.70, 0.45), (0.60, 0.70))  # (gap/涨停 下沿, 权重)
GAP_DEV_WEIGHT_BANDS = ((6.0, 0.60), (4.0, 0.75))                   # (DEV 下沿, 乘子)
GAP_WEIGHT_MIN = 0.20
AI_GAP_WEIGHT = os.environ.get("AI_GAP_WEIGHT", "1") != "0"


def gap_weight(gap_pct: Any, *, expected_gap_pct: Any = None,
               is_20cm: Any = False) -> dict[str, Any]:
    """高开接近涨停 → **建议仓位系数**（唯一实现；裁决 / 账本 / 卡面同源）。

    返回 ``{"weight": float, "near_ratio": float|None, "dev": float|None,
            "abs_scale": float, "dev_scale": float, "active": bool, "label": str}``。

    ``weight`` ∈ [GAP_WEIGHT_MIN, 1.0]，只表达「买多重」；调用方须与
    ``volume_band()['scale']`` **取小**，再乘进单票上限（⛔ 不相乘叠加两次打折）。
    ⛔ 封死买不进由 ``unbuyable_gap`` / ``gap_zone`` 处理，本函数不重复判。
    ⛔ 缺 gap / 缺 E 不算错：缺绝对量仍按 DEV=未知降绝对档，缺 E 只跳相对档，
       诚实留痕，⛔ 不猜 E、不因缺 E 就不降权。
    """
    out: dict[str, Any] = {"weight": 1.0, "near_ratio": None, "dev": None,
                           "abs_scale": 1.0, "dev_scale": 1.0,
                           "active": False, "label": ""}
    if not AI_GAP_WEIGHT:
        return out
    try:
        g = float(gap_pct)
    except (TypeError, ValueError):
        return out
    if not math.isfinite(g):
        return out
    lim = limit_up_pct(is_20cm)
    near = g / lim if lim > 0 else 0.0
    out["near_ratio"] = round(near, 4)
    abs_scale = 1.0
    for low, coef in GAP_NEAR_WEIGHT_BANDS:
        if near >= low:
            abs_scale = coef
            break
    out["abs_scale"] = abs_scale
    dev_scale = 1.0
    try:
        e = float(expected_gap_pct) if expected_gap_pct is not None else None
    except (TypeError, ValueError):
        e = None
    if e is not None and math.isfinite(e):
        dev = g - e
        out["dev"] = round(dev, 2)
        for low, coef in GAP_DEV_WEIGHT_BANDS:
            if dev >= low:
                dev_scale = coef
                break
    out["dev_scale"] = dev_scale
    weight = round(max(GAP_WEIGHT_MIN, abs_scale * dev_scale), 4)
    out["weight"] = weight
    out["active"] = weight < 1.0
    if out["active"]:
        bits = []
        if abs_scale < 1.0:
            bits.append(f"开在 {g:+.2f}% 已接近涨停")
        if dev_scale < 1.0:
            bits.append("明显高过它自己的质量预期")
        # ⛔ 不写具体百分数：那是「权重」不是「实际仓位」（实际 = 单票上限 × 权重，
        #    由 pool_call 下发 position_cap）。写死会与卡面/账本的同源仓位对不上。
        out["label"] = "、".join(bits) + " → 仓位收紧"
    return out

# ── 身位差「分母」修正 + 由硬闸改降权（2026-11-06 用户拍板 A）──────────────────
# 用户原话：
# 「我觉得可以先按A执行一下，因为身位差，我好像没有强制要求百分之百限制吧……
#   有时候龙头，比如说板块之间非板块之间的切换，比如说你传媒板块你已经涨了五板龙头了，
#   但是光伏板块才一个板，这种身位差其实是没有影响的呀，它是走一个新的逻辑，
#   甚至说可以做一个资金的承接方向，你这种身位差估计只能在同一个板块里面适用，
#   不同板块之间它是没有关系的」
#
# ## 病根：分母用错了
# 旧闸（2026-11-02 起）用 `gap = 全市场最高板 − 本票板数`，`≥2` 直接判后排、整只剔出。
# 但「传媒 5 板龙头 / 光伏 1 板」根本不是同一条线，板数差没有任何传导关系。
#
# ## 依据（35 交易日 / 717 个真题材可买票次 / T+1 账本结算口径）
# 脚本 `scripts/research_position_gap.py` → `cache/research/position_gap_bt.json`
#
# ┌─────────────────────┬──────┬────────┬───────┬───────┐
# │ 口径                 │ n    │ T+1    │ 胜率  │ 晋级  │
# ├─────────────────────┼──────┼────────┼───────┼───────┤
# │ 旧闸 保留(市场差≤1)  │  33  │ −1.05% │ 42.4% │ 14.6% │ ← 更差
# │ 旧闸 剔除(市场差≥2)  │ 512  │ −0.93% │ 41.8% │  5.9% │ ← 更好
# │ ── 换成同题材分母 ── │      │        │       │       │
# │ 同题材差 ≤1（前排）  │ 392  │ −0.68% │ 43.6% │  7.1% │
# │ 同题材差 ≥2（后排）  │ 153  │ −1.62% │ 37.3% │  4.4% │
# └─────────────────────┴──────┴────────┴───────┴───────┘
# ⇒ **旧闸在 717 个样本上零区分度、方向还是反的**（剔除批比保留批好 0.12pct）；
#    换成同题材分母后差出 **0.94pct 收益 / 6.3pct 胜率**，与用户直觉一致。
#    旧闸的「错杀批」（市场差≥2 但题材差≤1）n=359：T+1 **−0.64%/胜43.7%**，
#    比旧闸保留批（−1.05%/42.4%）**更好** —— 即旧闸在系统性地扔掉好票。
#
# ## 分档（同题材身位差，越靠后越轻；⛔ 非单调处已标注）
# ┌──────────┬──────┬────────┬───────┐
# │ 题材身位差 │ n    │ T+1    │ 胜率  │
# ├──────────┼──────┼────────┼───────┤
# │ 0（线内最高）│ 289  │ −1.10% │ 40.1% │ ← 自己就是锚，反而差
# │ 0 差一板   │ 103  │ +0.52% │ 53.4% │ ← 最好
# │ 2          │  60  │ −1.19% │ 40.0% │
# │ ≥3         │  93  │ −1.89% │ 35.5% │
# └──────────┴──────┴────────┴───────┘
# ⚠️「线内最高板反而比差一板差」不是噪声：本模块 `_c_ladder` 注释里独立记录的
#    「线内最高板自己高开 n=74 均 −2.68%/胜 44.6%，比非最高板更差」同向；
#    `scripts/research_buy_neighbor.py` 的「板差1∩低开」也是全组最好（+0.36%/48.4%）。
#    三次独立研究同向 → 保留这一档降权，但**不据此否决**。
#
# ## 边界
# - 只回答「买多重」，⛔ 不改 zone / view / participate / quota / 名额排序键。
# - 题材缺失（无 plates/theme，或落行业兜底）→ `basis="unknown"`、**权重 1.0 不裁决**；
#   ⛔ 不用行业分组冒充题材：行业口径实测方向相反（后排 +0.98% 反而更好，
#     n=38 且一个行业里板数天然一致 → 无信息量），与「缺数据不裁决」铁律一致。
# - 下限 `POSITION_WEIGHT_MIN=0.20` > 0：再后排也留试错仓，⛔ 不归零变成隐形硬闸。
# - 旧行为一键回退：`POOL_CALL_POSITION_GAP_SCOPE=market`（分母回全市场最高板）。
POSITION_LINE_GAP_BANDS = ((2, 0.40), (1, 1.00), (0, 0.75))   # (题材身位差下沿, 权重)
POSITION_WEIGHT_MIN = 0.20
AI_POSITION_WEIGHT = os.environ.get("AI_POSITION_WEIGHT", "1") != "0"


def position_weight(boards: Any, *, theme_max_boards: Any = None,
                    market_max_boards: Any = None) -> dict[str, Any]:
    """身位 → **建议仓位系数**（唯一实现；裁决 / 账本 / 卡面同源）。

    返回 ``{"weight": float, "line_gap": int|None, "market_gap": int|None,
            "basis": "line"|"market"|"unknown", "active": bool, "label": str}``。

    分母默认取**同题材最高板**（用户的「不同板块之间没有关系」）；
    `POOL_CALL_POSITION_GAP_SCOPE=market` 或显式 ``basis="market"`` 回退旧全市场口径。
    ⛔ 缺板数 / 缺题材 / 题材无效 → weight 1.0、basis unknown（不猜、不裁决）。
    """
    out: dict[str, Any] = {"weight": 1.0, "line_gap": None, "market_gap": None,
                           "basis": "unknown", "active": False, "label": ""}
    try:
        b = int(boards)
    except (TypeError, ValueError):
        return out
    if b <= 0:
        return out
    mg = None
    try:
        mm = int(market_max_boards)
        mg = mm - b
    except (TypeError, ValueError):
        pass
    out["market_gap"] = mg

    lg = None
    try:
        tm = int(theme_max_boards)
        if tm >= b:
            lg = tm - b
    except (TypeError, ValueError):
        lg = None
    out["line_gap"] = lg

    scope = os.environ.get("POOL_CALL_POSITION_GAP_SCOPE", "theme").strip().lower()
    if scope == "market":
        if mg is None:
            return out
        out["basis"] = "market"
        gap_used, basis = mg, "market"
    else:
        if lg is None:
            return out                  # 题材无效 → 不裁决（行业兜底无信息量）
        out["basis"] = "line"
        gap_used, basis = lg, "line"

    if not AI_POSITION_WEIGHT:
        return out
    weight = 1.0
    for low, coef in POSITION_LINE_GAP_BANDS:
        if gap_used >= low:
            weight = coef
            break
    weight = max(POSITION_WEIGHT_MIN, weight)
    out["weight"] = round(weight, 4)
    out["active"] = weight < 1.0
    if out["active"]:
        where = "同题材" if basis == "line" else "全市场"
        if gap_used == 0:
            out["label"] = f"{where}里它就是最高板（自己当锚）→ 仓位收紧"
        else:
            out["label"] = (f"{where}身位差 {gap_used} 板 → 仓位收紧")
    return out


# ── hibernate 竞价逃生阀（2026-09-18 用户拍板「A」·自评实证）────────────────────
# 背景（`scripts/ai_self_review.py` 自评）：09-16、09-18 两个 hibernate 日全空仓（0 参与），
#   而两个执行日的候选池均收益 **+2.57% / +1.03%**（机会成本合计 +3.60%）。
#   根因=stance 由**依据日盘后**用「昨涨停今晋级率」这类**滞后指标**一次性定死 →
#   退潮末端必然判 hibernate，而次日恰恰是修复日（龙头战法「退潮末期→修复」经典窗口）。
# 规则（宁严勿松·全满足才放 1 只）：hibernate 日不主动买，但若执行日竞价出现
#   「红开且 ≤ 5% 硬线 ∧ 带量（≥目标 × VOLUME_WEAK_RATIO）∧ 方向内位置最高的那只」
#   → 允许**小仓试错**（HIBERNATE_VALVE_POSITION），仍不解除其余四类硬伤。
# ⛔ 可逆（架构改动必留开关）：`HIBERNATE_VALVE_ENABLED = False` → 逐字回到
#   「hibernate 绝对空仓」旧行为；账本 append-only，改开关不动历史行。
# ⛔ 唯一判据源：本函数是阀门的唯一实现，verify / ai_ledger / 卡面必须同调，禁另写一套。
HIBERNATE_VALVE_ENABLED = True
HIBERNATE_VALVE_POSITION = 0.2          # 试错仓位（≤ MAX_POSITION_PER_STOCK）
HIBERNATE_VALVE_MAX_PER_DAY = 1         # 每日最多 1 只
HIBERNATE_VALVE_STANCES = ("hibernate",)  # 仅 hibernate 开阀（ice 仍绝对空仓·见 skill §3）
HIBERNATE_VALVE_MIN_GAP = 0.5           # 转强下沿：低于此视为「平开」不算转强（0.5~5% 才是带量转强）
# 只对 AI 判「观察」的票开阀：AI 判「放弃」= 语义更重的否决（纯跟风/传闻等）→ 阀门不越权放行。
HIBERNATE_VALVE_VIEWS = ("watch",)


def gap_zone(gap_pct: Any, expected_range: Any = None,
             tol: float = EXPECTED_ZONE_TOL, *,
             expected_gap_pct: Any = None,
             is_20cm: Any = False,
             env_offset: Any = 0.0) -> str:
    """竞价「是否符合预期」的 **唯一判据源**（池内裁决 / 账本 / 卡面共用）。

    2026-11-05 用户删掉 5% 与「必须红开」两条绝对线，改为逐票质量预期管理：
      · 任何模式：竞价封死（≥涨停−0.2pct）→ 'above'（**买不进**的物理事实，非幅度线）
      · relative（默认）：`DEV = gap − expected_gap_pct − env_offset`
            DEV < `EXPECTED_GAP_DEV_MIN`(−2.0) → 'below'（明显低于这只票的预期）
            其余 —— 高开、低开、超预期 —— 一律 'ok'
            缺 `expected_gap_pct` → 'unknown'（⛔ 缺数据不裁决，不猜、不否决）
      · absolute（`AI_EXPECTED_GAP_MODE=absolute` 一键回退）：
            gap > 5% → 'above'；gap < lo − tol → 'below'；其余 'ok'
    ⛔ 区间上沿 hi 两种模式都不参与（09-17 A2 修订已废）。
    ⛔ 本函数是唯一实现——任何地方不得再自写一套（防账本/卡面双轨脱节）。

    ## 🧮 `env_offset`（2026-11-06 用户「今天受外盘影响，绿盘那么多票，低开是可以理解的」）

    当日全池整体低开时，**每只票的 DEV 都被同一个负偏移压低**，逐票质量判据会
    集体误杀（11-06 池内 5 只被 DEV 门砍掉、事后均值 +1.11%）。传入池内中位开幅后，
    DEV 改为「相对当日整体开幅」的超额，恢复「这只票比同行强多少」的本意。

    ⛔ 偏移量由调用方（`pool_call`）**按当日池内中位数算好再传**，本函数只做算术：
      不读盘、不判环境、不自己找基准（否则账本/卡面又会各算一套）。
    ⛔ 默认 0.0 = 与 11-05 行为逐字一致；⛔ 不作用于 absolute 回退模式。
    """
    if gap_pct is None:
        return "unknown"
    try:
        g = float(gap_pct)
    except (TypeError, ValueError):
        return "unknown"
    if not math.isfinite(g):
        return "unknown"
    # ① 物理买不进：所有模式共用，⛔ 与「高开太多」无关。
    if unbuyable_gap(g, is_20cm):
        return "above"
    # ② 逐票质量预期（默认）
    if relative_mode():
        try:
            e = float(expected_gap_pct) if expected_gap_pct is not None else None
        except (TypeError, ValueError):
            e = None
        if e is None or not math.isfinite(e):
            return "unknown"          # 缺质量数据 → 不裁决，不否决
        try:
            off = float(env_offset) if env_offset is not None else 0.0
        except (TypeError, ValueError):
            off = 0.0
        if not math.isfinite(off):
            off = 0.0
        return "below" if (g - e - off) < EXPECTED_GAP_DEV_MIN else "ok"
    # ③ 旧绝对线（一键回退，逐字保留 11-05 之前行为）
    if g > USER_ENTRY_MAX_GAP_PCT:
        return "above"
    lo = None
    if isinstance(expected_range, (list, tuple)) and len(expected_range) == 2:
        try:
            lo = float(expected_range[0])
        except (TypeError, ValueError):
            lo = None
    if lo is not None and g < lo - tol:
        return "below"
    return "ok"

ROOT = Path(__file__).resolve().parent.parent          # wealth_code/
if str(ROOT) not in sys.path:                          # 允许独立运行
    sys.path.insert(0, str(ROOT))


def decisions_path() -> Path:
    """AI 决策台账路径（跟随 ROOT 计算，便于测试 monkeypatch 隔离）。"""
    return ROOT / "cache" / "ai_decisions.jsonl"


def next_trade_day(date10: str) -> str:
    """次一A股交易日；与 `pipeline_verify._next_day` 共享法定休市日历。"""
    return next_a_share_trading_day(date10) or str(date10)


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def load_ai_decisions(trade_date: str) -> dict[str, dict] | None:
    """读该执行日（trade_date=T+1）的 AI 决策 → {code: 决策行}。

    返回 None 表示**该日无任何 AI 决策**（缺失·非「AI 决定空仓」）——
    调用方必须据此回退机械口径并告警，不得当成零成交。
    返回 {} 表示有决策但都不适用（理论上不会出现；防御性保留）。
    """
    if not trade_date:
        return None
    rows = _read_jsonl(decisions_path())
    if not rows:
        return None
    hit: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("code") or "").strip()
        if len(code) != 6:
            continue
        # 优先用行内 trade_date；历史行缺失 → 由 decision_date 推导（同 next_day 算法）
        td = str(r.get("trade_date") or "").strip() or next_trade_day(str(r.get("decision_date") or ""))
        if td == str(trade_date):
            hit[code] = r
    return hit if hit else None


def view_label(view: Any) -> str:
    """AI 判断的白话标签（卡面/日志共用·禁机器黑话上卡）。"""
    return {"participate": "参与", "watch": "观察", "skip": "放弃"}.get(str(view or ""), "—")


def ai_declared_limit(decision: dict | None) -> float:
    """该票 **AI 自定**入场上限（entry_max_gap）——⛔ 仅留痕/展示，不参与裁决。"""
    try:
        v = (decision or {}).get("entry_max_gap")
        return float(v) if v is not None else DEFAULT_ENTRY_MAX_GAP
    except Exception:
        return DEFAULT_ENTRY_MAX_GAP


def entry_limit(decision: dict | None) -> float:
    """**生效**入场上限 = 用户 5% 硬线（2026-09-14 用户纪律·AI 自定值已失效）。

    保留 `decision` 入参只为向后兼容调用签名；返回值恒为 USER_ENTRY_MAX_GAP_PCT。
    """
    return USER_ENTRY_MAX_GAP_PCT


def clamp_position(p: Any, cap: float | None = None) -> float | None:
    """单票仓位裁剪（2026-09-14 用户「买入仓位不超过 50%」）。

    默认上限 = MAX_POSITION_PER_STOCK(0.5)；若传入动态单票上限 `cap`（来自
    core.relay_position 早盘接力动态仓位方案的 per_pick_cap），则取二者更小者
    （动态方案只在行情转弱时收紧·绝不放大）。

    非法/非正/缺失 → None（诚实留空，不编造仓位）。

    ⛔ 2026-10-30：裁剪逻辑收敛到 `core/risk_limits.clamp_per_stock`（单一源），
       本函数只做转出，避免「同一上限两套实现」各自演化。
    """
    return _rl_clamp_per_stock(p, cap)


def decide(decision: dict | None, gap_pct: float | None,
           expected_range: Any = None,
           auction_amount_yi: float | None = None,
           need_yi: float | None = None,
           *, valve_hit: bool = False,
           expected_gap_pct: Any = None) -> tuple[bool, str, dict]:
    """按 AI 决策结算单票；机械信号和逃生阀均不能放行未参与的标的。

    ``valve_hit`` 保留调用签名兼容，但不再拥有任何放行权限。只有 AI 的
    ``view=participate``，并同时通过竞价价格、预期区间和量能校验，才可成交。
    """
    base = {"ai_gap_limit": USER_ENTRY_MAX_GAP_PCT,
            "ai_declared_limit": ai_declared_limit(decision)}
    if decision is None:
        return False, "AI 昨日未对这只票下判断 → 不买", {"ai_view": None, **base}

    from core.two_pool import observation_only, rechecked_entry
    # 🎯 2026-11-05 竞价三态：B/C 是「人工二次自己交易、不做记录」的机会。
    #    冻结件已标 manual_only → 系统侧一律不形成入场/成交记录。
    #    ⛔ 翻案票优先：参与票恒不落 manual_only，不得二次否决 11-05 的翻案链。
    if (decision.get("manual_only") is True
            and str(decision.get("auction_state") or "") in ("B", "C")
            and not rechecked_entry(decision)):
        return False, "人工二次机会，系统不记录", {
            "ai_view": str(decision.get("view") or "watch"), **base}
    # 🔁 11-05：观察票经 09:25 竞价复核翻案（state 类 + 量足 + 全闸通过）后，
    #    冻结件已判 participate，此处不得再拿盘后 observe_only 身份否决 → 「判了买、账本不买」。
    if observation_only(decision) and not rechecked_entry(decision):
        return False, "板块观察参照，不参与交易", {"ai_view": "watch", **base}
    raw_view = str(decision.get("view") or "").strip()
    if not raw_view:
        # 新版盘后预案以 bias 表达意图；只有 focus 才是 AI 明确参与。
        raw_view = {"focus": "participate", "normal": "watch", "avoid": "skip"}.get(
            str(decision.get("bias") or "").strip(), "missing")
    if raw_view != "participate":
        return False, f"AI 判「{view_label(raw_view)}」→ 不买", {"ai_view": raw_view or None, **base}
    if gap_pct is None:
        return False, "无竞价数据 → 不知道能不能买到，不买（诚实跳过）", {
            "ai_view": raw_view, **base}

    gap = float(gap_pct)
    # 🎯 11-05 用户删 5%/红开：判据改为「实际开幅 − 逐票质量预期」。
    #    E 优先取冻结件注入的决策行（_inject_pool_views / _settle_decision），
    #    其次取调用方显式参数；缺 E → gap_zone 返回 unknown，⛔ 不裁决不否决。
    _e = decision.get("expected_gap_pct")
    if _e is None:
        _e = expected_gap_pct
    try:
        _e_num = float(_e) if _e is not None else None
    except (TypeError, ValueError):
        _e_num = None
    zone = gap_zone(gap, expected_range, expected_gap_pct=_e,
                    is_20cm=decision.get("is_20cm"))
    base["zone"] = zone
    if _e_num is not None:
        base["expected_gap_pct"] = _e_num
    base["expected_gap_mode"] = "relative" if relative_mode() else "absolute"
    # 🎯 11-05 21:45 用户「大于 6、7 个点要非常小心，降低买入权重」：
    #    高开降权与池内裁决同源留痕（⛔ absolute 回退模式不写、不降权）。
    #    ⛔ 只留痕权重，不改 zone / 是否成交；仓位由冻结件 position_cap 单一源下发。
    if relative_mode() and AI_GAP_WEIGHT:
        _gw = gap_weight(gap, expected_gap_pct=_e_num,
                         is_20cm=decision.get("is_20cm"))
        base["gap_weight"] = _gw["weight"]
        base["gap_weight_active"] = _gw["active"]
    if zone == "above":
        if relative_mode():
            return False, f"竞价 {gap:+.2f}% 已封在涨停附近 → 买不进，不追", {
                "ai_view": raw_view, **base}
        return False, (f"竞价高开 {gap:+.2f}% 超过 {USER_ENTRY_MAX_GAP_PCT:g}% 上限（高开太多）"
                       " → 不追（纪律：高开超 5% 的不买）"), {"ai_view": raw_view, **base}
    if zone == "below":
        if relative_mode():
            _txt = (f"竞价 {gap:+.2f}%，按昨日质量该开 {_e_num:+.2f}%，明显低于预期 → 放弃"
                    if _e_num is not None else f"竞价 {gap:+.2f}% 低于预期 → 放弃")
            return False, _txt, {"ai_view": raw_view, **base}
        return False, (f"竞价 {gap:+.2f}% 低于预期区间 → 放弃（不及预期·不接）"), {
            "ai_view": raw_view, **base}
    # 🎯 2026-11-05：量能改「分档区间」，与 pool_call 共用 volume_band 唯一判据源。
    #    只有 dry（真没量 <35%）才否决；soft（35%~70%）放行但把 scale 交给账本降仓。
    #    ⛔ 旧模式只判红开（绿开本就不买）；relative 模式下低开也可能可买 → 量能门同步覆盖，
    #       「弱票低开要拉起来」同样得有量，不能绕过量能举证。
    if auction_amount_yi is not None and need_yi and (relative_mode() or gap > 0):
        _vb = volume_band(auction_amount_yi, need_yi)
        base["volume_band"] = _vb["band"]
        base["volume_scale"] = _vb["scale"]
        if _vb["block"]:
            base["volume_weak"] = True
            return False, (f"竞价量能 {float(auction_amount_yi):.2f}亿 只到计划 "
                           f"{float(need_yi):.2f}亿 的 "
                           f"{(_vb['ratio'] or 0) * 100:.0f}%（真没量）→ 观察·不追"), {
                "ai_view": raw_view, **base}
        if _vb["band"] == "soft":
            base["volume_weak"] = True
    if relative_mode():
        _e_txt = (f"（按昨日质量该开 {_e_num:+.2f}%）" if _e_num is not None else "")
        return True, (f"AI 要买·竞价 {gap:+.2f}% 符合这只票的预期{_e_txt} → 买入"), {
            "ai_view": raw_view, **base}
    return True, (f"AI 要买·竞价 {gap:+.2f}% 未超 {USER_ENTRY_MAX_GAP_PCT:g}% 上限、未低于预期"
                  " → 买入"), {"ai_view": raw_view, **base}


# ─────────────────────────────────────────────────────────────────────────────
# hibernate 竞价逃生阀（2026-09-18 用户拍板「A」）
# ─────────────────────────────────────────────────────────────────────────────
def ai_stance(trade_date: str, root: Path | None = None) -> str | None:
    """读该执行日的 AI 定调 stance（`cache/ai_view_{执行日}.json`·依据日盘后自定）。

    返回 'hibernate' / 'defend' / 'selective' / 'attack'；文件缺失/字段缺失 → None（不编造）。
    ⚠️ ai_view 在**项目根 cache/**（非 wealth_code/cache）→ 默认根取 ROOT.parent。
    """
    base = Path(root) if root else ROOT.parent
    p = base / "cache" / f"ai_view_{trade_date}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return str(d.get("stance") or "").strip() or None


def _flatten_candidates(candidates: Any) -> list[dict]:
    """候选池展平：支持 {relay:[], watch:[]} 与直接传 list（禁对同一只票重复计入）。"""
    if isinstance(candidates, dict):
        arr: list[dict] = []
        for k in ("relay", "watch", "early_ambush"):
            arr.extend([c for c in (candidates.get(k) or []) if isinstance(c, dict)])
        return arr
    if isinstance(candidates, list):
        return [c for c in candidates if isinstance(c, dict)]
    return []


def _valve_reason(c: dict, gap: float, boards: Any) -> str:
    """逃生阀放行理由（**大白话**·过 card_copy_rules 门禁：禁身位/梯队/龙一/研判等黑话）。

    ⏱️ 时点口径（2026-09-18）：定调是**昨日收盘**定的、竞价是**今日**开的 —— 两个时点必须分开写，
    ⛔ 禁把「昨日定的空仓」写成「今天是空仓日」（次日读卡会被读成当日结论）。
    """
    th = str(c.get("theme_name") or c.get("theme") or "").strip()
    mates = c.get("theme_mates_cnt")
    _b = f"{int(boards)}板" if isinstance(boards, (int, float)) and boards else "首板"
    _dir = f"{th}方向" if th else "这个方向"
    _mate = f"、同方向还有 {int(mates)} 家" if isinstance(mates, (int, float)) and mates else ""
    return (f"昨日收盘定的是空仓，但它今日开在 {gap:+.2f}% 并且有量，是{_dir}里板数最高的一只"
            f"（{_b}{_mate}）→ 只买一小笔试错")


def pick_hibernate_valve(candidates: Any, quotes: dict[str, dict] | None, *,
                         stance: str | None,
                         views: dict[str, Any] | None = None,
                         enabled: bool | None = None,
                         max_pick: int | None = None) -> dict | None:
    """hibernate 日「竞价逃生阀」：从候选池挑出**唯一 1 只**允许小仓试错的票。

    触发条件（**全满足**·宁严勿松）：
      ① 依据日 AI 定调 = hibernate（`HIBERNATE_VALVE_STANCES`；ice 不开阀）
      ② AI 对该票只判了「观察」（`HIBERNATE_VALVE_VIEWS`）——判「放弃」的不放行（不越权）
      ③ **带量转强**：竞价红开且 ≥ HIBERNATE_VALVE_MIN_GAP(0.5%)、≤ 5% 硬线；量能 ≥ 目标 × VOLUME_WEAK_RATIO
      ④ **方向内位置最高**：同 `theme_name` 里 `boards` 最高（并列取 `score` 高）
      ⑤ 非 ST（硬伤照旧）；每日最多 `HIBERNATE_VALVE_MAX_PER_DAY` 只

    views: {code: "watch"/"skip"/"participate"}；缺省 None → 不做 ② 过滤（向后兼容）。
    返回 {"code","name","gap_pct","boards","theme_name","position","reason"}；无合格者 → None。
    ⛔ 只读：不写账本、不改阈值；成交与否仍由 `decide(..., valve_hit=True)` 落在同一口径上。
    ⛔ 实测（2026-09-18 回测 2 个 hibernate 日）：09-16 抓到 +7.32%、09-18 抓到 -3.27%
      → 均值 +2.03%·**样本极小·非稳赚阀**，定位=「空仓日的试错通道」，故仓位压到 0.2。
    """
    if not (HIBERNATE_VALVE_ENABLED if enabled is None else bool(enabled)):
        return None
    if str(stance or "") not in HIBERNATE_VALVE_STANCES:
        return None
    cands = _flatten_candidates(candidates)
    if not cands or not quotes:
        return None
    try:
        from core.signal_eval import evaluate_realtime_signal
    except Exception:                                   # 独立运行兜底
        from signal_eval import evaluate_realtime_signal  # type: ignore

    pool: list[dict] = []
    for c in cands:
        code = str(c.get("code") or "").strip()
        if len(code) != 6:
            continue
        if views is not None and str(views.get(code) or "") not in HIBERNATE_VALVE_VIEWS:
            continue                                    # ② 只对「观察」开阀
        name = str(c.get("name") or "")
        if "ST" in name.upper():                        # ⑤ 硬伤照旧
            continue
        q = quotes.get(code)
        if not isinstance(q, dict):
            continue
        ap, pc = q.get("auction_price"), q.get("prev_close")
        try:
            gap = (float(ap) - float(pc)) / float(pc) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if not (HIBERNATE_VALVE_MIN_GAP <= gap <= USER_ENTRY_MAX_GAP_PCT):   # ③ 转强且不超硬线
            continue
        sig = evaluate_realtime_signal(c, q)
        need = float(sig.get("auction_amount_need_yi") or 0.0)
        amt = float(sig.get("auction_amount_yi") or 0.0)
        # ③ 带量：逃生阀宁严勿松，只认 full 档（soft 是「量偏弱」，不配开阀）。
        #    判据仍走 volume_band 唯一源，⛔ 不在此另写 0.7。
        if need <= 0 or volume_band(amt, need)["band"] != "full":
            continue
        pool.append({"code": code, "name": name, "gap_pct": round(gap, 2),
                     "boards": c.get("boards") or 0, "score": c.get("score") or 0.0,
                     "theme_name": str(c.get("theme_name") or c.get("theme") or "").strip(),
                     "cand": c})

    # ④ 方向内位置最高：同 theme_name 里 boards 最高（并列看 score）
    best_in_theme: dict[str, dict] = {}
    for x in pool:
        t = x["theme_name"]
        cur = best_in_theme.get(t)
        if cur is None or (x["boards"], x["score"]) > (cur["boards"], cur["score"]):
            best_in_theme[t] = x
    leaders = list(best_in_theme.values())
    if not leaders:
        return None
    leaders.sort(key=lambda x: (x["boards"], x["score"]), reverse=True)
    n = HIBERNATE_VALVE_MAX_PER_DAY if max_pick is None else int(max_pick)
    leaders = leaders[:max(0, n)]
    if not leaders:
        return None
    top = leaders[0]
    return {"code": top["code"], "name": top["name"], "gap_pct": top["gap_pct"],
            "boards": top["boards"], "theme_name": top["theme_name"],
            "position": HIBERNATE_VALVE_POSITION,
            "reason": _valve_reason(top["cand"], top["gap_pct"], top["boards"]),
            "candidates": [x["code"] for x in leaders]}
