#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🧠 LLM 交易员裁决（2026-11-12 用户「LLM 不只是看催化，要结合龙头交易员做抉择」）

与 `ai_catalyst.py` 的分工（⛔ 不重叠）：
- `ai_catalyst`：**盘后** 15:35，逐题材给催化等级（题材层）
- 本脚本：**竞价后** 09:26，逐票做交易裁决（个股层），必须综合
  机械闸结果 / 晋级率 / 题材催化 / 情绪周期 / 身位 / 盘口

输出 `cache/ai_trade_<date>.json`：
```json
{"picks": [{"code":"603366","action":"buy|watch|skip","confidence":72,
            "position":"0.4","thesis":"…","risks":"…"}]}
```
⛔ 只给裁决建议；不改阈值、不下单。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))
sys.path.insert(0, str(ROOT / "scripts"))


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def build_prompt(day: str) -> str:
    import promotion_plan as pp
    plan = pp.build(day)
    rows = [r for r in (plan.get("rows") or []) if r.get("likely")][:10]
    freeze = _load(ROOT / "wealth_code" / "cache" / f"pool_call-{day}.json", {})
    views = freeze.get("views") or {}
    cat = _load(ROOT / "cache" / f"ai_advice_{day}.json", {})
    levels = {k: (v or {}).get("level") for k, v in (cat.get("themes") or {}).items()}
    fb = _load(ROOT / "cache" / "feedback_matrix.json", {})
    items = []
    for r in rows:
        v = views.get(str(r["code"])) or {}
        items.append({
            "代码": r["code"], "名称": r.get("name"), "板位": r.get("boards"),
            "竞价涨幅": r.get("gap"), "晋级率": r.get("pct"),
            "题材": r.get("theme"), "题材催化": levels.get(str(r.get("theme") or ""), "unknown"),
            "机械视图": v.get("view") or "not_in_pool",
            "机械理由": (v.get("reason") or "")[:60],
        })
    env = {"晋级率": fb.get("promote_rate"), "炸板率": fb.get("broken_rate"),
           "核按钮": fb.get("nuke_count"), "涨停数": fb.get("now_zt_count"),
           "最高板": fb.get("now_max_boards"), "昨日溢价": fb.get("avg_pct")}
    return f"""你是 A 股龙头战法交易员。今天是 {day}，09:25 竞价刚结束。

## 你的任务
对下面每只票做**交易裁决**：买（buy）/ 观察（watch）/ 放弃（skip）。
必须综合考虑：机械闸结果（participate 才可买）、晋级率（<40% 不值得打）、
题材催化（weak 不买）、市场情绪（晋级率/炸板率/核按钮）、以及身位风险。

## 市场环境
{json.dumps(env, ensure_ascii=False)}

## 候选
{json.dumps(items, ensure_ascii=False, indent=1)}

## 输出（只输出 JSON，不要解释）
{{"picks":[{{"code":"...","action":"buy|watch|skip","confidence":0-100,
  "position":"0~0.5","thesis":"一句话（≤40字，必须引用具体数字）",
  "risks":"一句话（≤30字）"}}]}}

规则：
- 机械视图不是 participate → 最高只能是 watch；
- 晋级率 <40% → skip；
- 题材催化 weak → 最高 watch；
- 一次最多给 2 只 buy，且按性价比排序。
"""


SYSTEM = ("你是 A 股龙头战法交易员，负责在 09:25 竞价后做买卖裁决。"
          "只输出 JSON；每个判断必须引用给出的具体数字；不确定就 watch。")


def call_llm(prompt: str) -> dict | None:
    """独立调用（⛔ 不复用 ai_catalyst 的题材 system prompt）。"""
    from ai_catalyst import _resolve_model
    cfg = _resolve_model()
    if not cfg:
        print("[ai_trader] 无模型配置")
        return None
    try:
        from common.http_client import http_post
        res = http_post(
            f"{cfg['base']}/chat/completions",
            {"model": cfg["model"], "temperature": 0.3,
             "messages": [{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": prompt}]},
            timeout=90,
            headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"},
        )
        text = res["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        print(f"[ai_trader] 调用失败：{e}")
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        print(f"[ai_trader] 无 JSON：{text[:120]}")
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception as e:  # noqa: BLE001
        print(f"[ai_trader] JSON 解析失败：{e}")
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    from datetime import datetime
    day = a.date or datetime.now().strftime("%Y-%m-%d")
    prompt = build_prompt(day)
    if a.dry_run:
        print(prompt[:2000])
        return 0
    res = call_llm(prompt)
    if not res:
        print("[ai_trader] LLM 无返回")
        return 1
    picks = res.get("picks") or res.get("data", {}).get("picks") or []
    out = {"date": day, "picks": picks, "source": "ai_trader_judge"}
    (ROOT / "cache" / f"ai_trade_{day}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for p in picks:
        print(f"  {p.get('action'):<5}{p.get('code')} {p.get('confidence')} | {p.get('thesis')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
