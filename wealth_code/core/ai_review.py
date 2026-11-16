#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧠 09:26 池内复核（LLM 层 · 2026-11-13 接入主链）

## 它解决什么问题
接入前主链的 LLM 是一座孤岛：
- `scripts/ai_trader_judge.py` 送进模型的是 `promotion_plan` 的「likely」行（**池外票**），
  实测 2026-11-13 送了 7 只，其中 6 只根本不在池内 ⇒ 模型只能回「机械 not_in_pool → 观察」，
  既正确又无用，白烧 token。
- 产出写 `cache/ai_trade_<date>.json`，`pool_call` / `build_cards` **零消费**。
- 卡面上「🤖 今日9:25判」是规则模板拼的，不是模型输出。

本模块把模型放回它该在的位置：**机械筛完之后、只对池内票复核**。

## 三层分工（⛔ 不越权，与 `final_verdict` 同口径）
| 层 | 作用 | 权限 |
|---|---|---|
| 机械闸（`pool_call`，17 道） | 能不能买、买多重 | **一票否决** |
| 晋级率 | 值不值得打 | 低于板位基准线不买 |
| 本模块（LLM） | 逐票复核 + 市场一句话 | **只能降，不能升** |

### 「只降不升」的落地
- 机械 `view != participate` → 模型说 `buy` 一律压回 `watch`
  （机械否决过的票，模型无权复活；否则 17 道闸形同虚设）
- 机械 `participate` + 模型 `skip` → 降为不买（这是本模块**唯一**能增加的价值）
- 模型输出任何非法值 → 归一化 `watch`，⛔ 不猜 buy

## 时间窗约束
竞价卡 09:26:05 抓价、约 09:26:10 推卡。LLM 实测 3.4s，但偶发慢响应会拖死推卡：
- 硬超时默认 **25s**（`AI_REVIEW_TIMEOUT` 可调）
- 不重试（`llm_client.chat` 已设 `retries=0`）
- 任何失败返回 `None` → 调用方照常推卡，⛔ AI 挂了不能让今天没卡

## 开关
`AI_REVIEW=0` → 整个模块不调模型，返回 None（主链行为与接入前一致）。
"""
from __future__ import annotations

import os
import time
from typing import Any

DEFAULT_TIMEOUT = float(os.environ.get("AI_REVIEW_TIMEOUT", "25") or 25)

SYSTEM = (
    "你是 A 股龙头战法交易员，在 09:25 竞价结束后对**池内票**做最后一道复核。"
    "铁律：① 机械结论否决过的票你无权放行，最多给观察；"
    "② 只评名单内出现的票，不引入名单外的票；③ 每条理由必须引用给出的具体数字；"
    "④ 数据缺失就写未知，不猜。只输出 JSON。"
)


def enabled() -> bool:
    return os.environ.get("AI_REVIEW", "1") not in ("0", "false", "False", "off")


def _f(v, nd: int = 2):
    """安全取数：缺失返回 None（⛔ 不填 0），供 prompt 写「未知」。"""
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _safe_text(text: str, limit: int = 60) -> str:
    """把模型自由文本洗成「上卡不会阻断推送」的形态。

    ⚠️ 为什么必须有这一道：2026-11-13 上午整张竞价卡因文案命中
    `card_copy_rules.PLAIN_FAIL`（`口径：`）被 `push_card` 硬阻断，一条 HTTP 都没发出去。
    模型是自由文本源，比模板更容易吐黑话 ⇒ 必须在**进卡面之前**就洗掉。

    策略（宁可少说，不可断推）：
    1. 先走 `card_copy_rules.sanitize()`（单一源，⛔ 不在此复制词表）
    2. 洗完仍命中阻断词 → 整句丢弃返回 ""，而不是让它流到 lint
    """
    s = str(text or "").strip()[:limit]
    if not s:
        return ""
    try:
        import sys as _sys
        from pathlib import Path as _P
        _root = str(_P(__file__).resolve().parents[2])
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        sys.path.insert(0, str(_P(_root) / "scripts"))
        import card_copy_rules as CCR
        s = CCR.sanitize(s)
        fails, _ = CCR.scan(s)
        if fails:
            return ""      # 洗不干净就别说，⛔ 不许带病上卡
    except Exception:  # noqa: BLE001
        for bad in ("口径：", "冰点禁买", "机械口径", "惯犯", "偶犯"):
            s = s.replace(bad, "")
    return s.strip()


def _item(code: str, v: dict) -> dict:
    """把 views 压缩成模型能读的最小事实集（省 token，只留判据相关）。"""
    gene = v.get("recent_gene") or {}
    return {
        "代码": code,
        "名称": v.get("name"),
        "板位": v.get("boards"),
        "题材": v.get("theme_name"),
        "竞价涨幅": _f(v.get("gap_pct")),
        "距市场最高板": v.get("gap_to_top"),
        "同题材最高板": v.get("line_max_boards"),
        "这条线龙一": bool(v.get("line_is_core")),
        "龙头身份": v.get("leader_role"),
        "竞价额亿": _f(v.get("amount_yi")),
        "需要的量亿": _f(v.get("need_yi")),
        "量能": v.get("zone"),
        "基因": gene.get("label"),
        "机械结论": v.get("view"),
        "机械理由": _safe_text(str(v.get("reason") or ""), 70),
    }


def build_prompt(views: dict[str, Any], market: dict[str, Any] | None = None) -> str:
    mk = market or {}
    env = {k: mk.get(k) for k in (
        "promote_rate", "broken_rate", "nuke_count",
        "now_zt_count", "now_max_boards", "avg_pct") if mk.get(k) is not None}
    items = [_item(str(c), v) for c, v in views.items() if isinstance(v, dict)]
    import json as _json
    return (
        "## 市场环境（今日 09:25）\n"
        f"{_json.dumps(env, ensure_ascii=False)}\n"
        "字段含义：promote_rate 昨日涨停今日晋级率%、broken_rate 炸板率%、"
        "nuke_count 核按钮家数、now_zt_count 涨停数、now_max_boards 最高板、avg_pct 昨日涨停今日均涨幅%\n\n"
        "## 池内候选（机械筛完只剩这些）\n"
        f"{_json.dumps(items, ensure_ascii=False, indent=1)}\n\n"
        "## 任务\n"
        "对每只票给 buy / watch / skip。判断要结合：机械结论（participate 才可买）、"
        "竞价涨幅离涨停还有多少空间、量能够不够、这条线是不是龙一、基因好不好、市场冷热。\n"
        "⛔ 机械结论不是 participate 的票，一律不许给 buy。\n\n"
        "## 输出（只输出 JSON，不要任何解释）\n"
        '{"market":"一句话：今天该不该动手（≤30字，引用市场数字）",'
        '"picks":[{"code":"6位代码","action":"buy|watch|skip","why":"≤30字，引用具体数字"}]}'
    )


def _normalize(raw: Any, views: dict[str, Any]) -> dict[str, Any]:
    """归一化 + 执行「只降不升」。"""
    picks_in = raw.get("picks") if isinstance(raw, dict) else None
    if not isinstance(picks_in, list):
        picks_in = []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in picks_in:
        if not isinstance(p, dict):
            continue
        code = str(p.get("code") or "").strip()
        if code not in views or code in seen:
            continue          # ⛔ 池外票 / 重复票 直接丢弃
        seen.add(code)
        mech = str((views.get(code) or {}).get("view") or "")
        act = str(p.get("action") or "").strip().lower()
        if act not in ("buy", "watch", "skip"):
            act = "watch"
        capped = False
        if act == "buy" and mech != "participate":
            act, capped = "watch", True   # 机械否决过的票，模型无权复活
        out.append({"code": code,
                    "name": (views.get(code) or {}).get("name"),
                    "action": act,
                    "why": _safe_text(p.get("why"), 60),
                    "capped": capped})
    # 池内票模型漏答的，补 watch（⛔ 不补 buy）
    for code in views:
        if code not in seen:
            out.append({"code": code, "name": (views.get(code) or {}).get("name"),
                        "action": "watch", "why": "模型未作答", "capped": False})
    order = {"buy": 0, "watch": 1, "skip": 2}
    out.sort(key=lambda x: order.get(x["action"], 9))
    buys = [x for x in out if x["action"] == "buy"]
    return {"market": _safe_text((raw or {}).get("market"), 60),
            "picks": out,
            "verdict": f"可买 {len(buys)} 只" if buys else "空仓",
            "buy_count": len(buys),
            "capped_count": sum(1 for x in out if x["capped"])}


def review(day: str, views: dict[str, Any],
           market: dict[str, Any] | None = None,
           timeout: float | None = None,
           *, dry_run: bool = False) -> dict[str, Any] | None:
    """对池内票做 LLM 复核。

    返回 {market, picks, verdict, buy_count, elapsed, model} 或 None（关闭/无票/失败）。
    ⛔ 无票时不调模型 —— 池内 0 只意味着机械已经否决一切，模型无话可说。
    """
    if not enabled() or not views:
        return None
    prompt = build_prompt(views, market)
    if dry_run:
        return {"prompt": prompt}
    from core import llm_client
    t0 = time.time()
    budget = float(timeout if timeout is not None else DEFAULT_TIMEOUT)
    text = llm_client.chat(prompt, system=SYSTEM, timeout=budget,
                           max_tokens=900, temperature=0.2)
    elapsed = round(time.time() - t0, 1)
    raw = llm_client.extract_json(text)
    if raw is None:
        print(f"[AI-REVIEW] 模型无有效 JSON（{elapsed}s，预算 {budget}s）→ 降级不显示")
        return None
    res = _normalize(raw, views)
    res.update({"day": day, "elapsed": elapsed,
                "model": (llm_client.resolve_model() or {}).get("model")})
    print(f"[AI-REVIEW] {res['verdict']}（{len(res['picks'])} 票复核 / "
          f"压回 {res['capped_count']} / {elapsed}s）")
    return res
