# -*- coding: utf-8 -*-
"""🌅 09:25 机械定调（`stance` 的机械替代源）。

## 为什么需要它（2026-11-06 用户「盘后预案全系统关闭」）

盘后预案里的 `stance`（攻守基调）原本是**昨天盘后 LLM 拍的**，用来定今日名额：
`attack 3 / selective 2 / defend 1 / hibernate 0`。

关掉盘后预案后，这个数没了。**不能让名额变成死常量 2** —— 名额是当天最重要的
风险敞口，它必须由一个**今晨就能看到的客观事实**决定。

所以这里用「昨涨停今晨竞价开出来那一刻」的四个数来定：
昨涨停红开率 / 连板红开率 / 连板竞价量比 / 覆盖率（全部来自 `core.market_open`，
已在 09:25 算好并注入 `pool_call(market_open=…)`）。

⛔ 不看指数涨跌（用户 2026-08-18「重溢价·轻大盘」）。
⛔ 不看 LLM 主观判断，不看昨日收盘盘面。
⛔ 只读入参，不读盘、不落盘（纯函数，测试可完全隔离）。

## 档位（`config/shared_rules.md` 单一源）

```
昨涨停红开率 ≥ 0.60 ∧ 连板红开率 ≥ 0.60   → attack     3 只
昨涨停红开率 ≥ 0.45                       → selective  2 只
昨涨停红开率 ≥ 0.30                       → defend     1 只
否则                                      → hibernate  0 只（空仓）
覆盖率不足 / 缺红开率                      → None（不裁决，走 DEFAULT_QUOTA）
```

## 35 日实测边界（⛔ 必须如实读）

- 与「不设定调 · 名额恒 2」相比：24天/81笔/日胜70.8%/日均+1.96%/复利+57.73%
  **vs** 25天/83笔/日胜72.0%/日均+1.97%/复利+61.05% ⇒ **本模块略差**。
- 差在哪：它的 `hibernate` 档（今早红开率 <30% → 空仓）重复了盘后预案犯过的错
  —— 本窗口里「空仓」整体站在错的那一边（盘后预案的 hibernate 砍掉 16 笔均 +1.70%）。
- 阈值 0.60/0.45/0.30 扫到 0.50/0.35/0.20，日均几乎不动（平台非尖峰）→ 模块本身没坏，
  只是**在这个强势窗口里不如「无条件给名额 + 环境防守腿兜底」**。
- ⇒ **默认关**（`POOL_CALL_STANCE_INTRADAY=0`）。留作弱市窗口的备选。
  ⛔ 开启后不得据此宣称提高胜率。真正的熔断仍是环境防守腿（block → 名额 0）。
"""
from __future__ import annotations

from typing import Any, Mapping

# ── 阈值（单一源；⛔ 改这里必须同步 `config/shared_rules.md`）──────────────
ATTACK_RED_OPEN = 0.60       # 昨涨停红开率 → attack
ATTACK_LEADER_RED_OPEN = 0.60  # 连板红开率（attack 需同时满足）
SELECTIVE_RED_OPEN = 0.45    # → selective
DEFEND_RED_OPEN = 0.30       # → defend；低于此 → hibernate
MIN_COVERAGE = 0.60          # 昨涨停池报价覆盖率下限，不足则不裁决

STANCE_ORDER = ("hibernate", "defend", "selective", "attack")


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def stance_from_open(open_read: Mapping[str, Any] | None) -> dict[str, Any]:
    """由 09:25 开盘读数定出今日攻守基调。

    返回 `{"stance": str|None, "reason": str, "evidence": {...}}`。
    `stance is None` ＝ **不裁决**（覆盖率不足/缺红开率）→ 调用方走 `DEFAULT_QUOTA`。
    """
    out: dict[str, Any] = {"stance": None, "reason": "", "evidence": {}}
    if not isinstance(open_read, Mapping) or not open_read:
        out["reason"] = "今早竞价读数缺失 → 不定调（用缺省名额）"
        return out

    red = _num(open_read.get("red_open_ratio"))
    lead_red = _num(open_read.get("leader_red_open_ratio"))
    vol = _num(open_read.get("leader_volume_median_ratio"))
    cov = _num(open_read.get("coverage"))
    out["evidence"] = {
        "red_open_ratio": red,
        "leader_red_open_ratio": lead_red,
        "leader_volume_median_ratio": vol,
        "coverage": cov,
    }
    if red is None:
        out["reason"] = "昨涨停今晨没有有效报价 → 不定调（用缺省名额）"
        return out
    if cov is not None and cov < MIN_COVERAGE:
        out["reason"] = f"昨涨停只有 {cov * 100:.0f}% 报了价，样本不够 → 不定调（用缺省名额）"
        return out

    if red >= ATTACK_RED_OPEN and lead_red is not None and lead_red >= ATTACK_LEADER_RED_OPEN:
        out["stance"] = "attack"
        out["reason"] = (f"昨涨停 {red * 100:.0f}% 红开、连板 {lead_red * 100:.0f}% 红开 "
                         "→ 今天接力氛围好，可以正常做")
    elif red >= SELECTIVE_RED_OPEN:
        out["stance"] = "selective"
        out["reason"] = f"昨涨停 {red * 100:.0f}% 红开 → 可以做，但不铺开"
    elif red >= DEFEND_RED_OPEN:
        out["stance"] = "defend"
        out["reason"] = (f"昨涨停只有 {red * 100:.0f}% 红开 → 氛围偏弱，"
                         "只做最有把握的一只")
    else:
        out["stance"] = "hibernate"
        out["reason"] = (f"昨涨停只有 {red * 100:.0f}% 红开 → 氛围很差，"
                         "今天空仓待命")
    return out
