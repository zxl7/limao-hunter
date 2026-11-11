#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材催化强度：就绪的 LLM 调用入口（2026-11-11）

用户 11-11：「机械全买肯定不合理，我们还是要挑着做题材催化强度这一块，LLM 层我们不能介入吗？」

能。两条路都支持：

A. **仓库内直连 LLM**（本脚本）：需要环境变量
     LLM_BASE_URL  （默认 https://api.deepseek.com，OpenAI 兼容）
     LLM_API_KEY   （必需；缺失则 exit 2，⛔ 不假装成功）
     LLM_MODEL     （默认 deepseek-chat）
   产出 `cache/ai_advice_<date>.json`（结构见 core/ai_advice.py）。

B. **由 WorkBuddy 的 AI 直接写**（推荐，零密钥）：让自动化在 15:30/09:20 把同结构文件写到
   `cache/ai_advice_<date>.json`，再跑 `--from-file` 校验即可。

⛔ 本脚本只产出**题材催化强度**，不产出买卖结论；是否采纳由交易员/`pool_call` 决定。

用法：
    python3 scripts/ai_catalyst.py --date 2026-11-12 --dry-run   # 看 prompt（不调用）
    python3 scripts/ai_catalyst.py --date 2026-11-12             # 真调用（需 key）
    python3 scripts/ai_catalyst.py --validate 2026-11-12         # 校验已有文件
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wealth_code.core import ai_advice as AA  # noqa: E402

WC = ROOT / "wealth_code" / "cache"
CACHE = ROOT / "cache"

SYSTEM = (
    "你是A股短线交易员的题材研究员。你只做一件事：给每个题材打**催化强度**分。"
    "⛔ 不判断买不买、不给目标价、不预测涨跌。"
    "只依据给定的新闻/事件与盘面事实，没有证据就写 unknown，⛔ 不许编。"
)

TEMPLATE = """今天是 {day}。请评估下面这些题材的**催化强度**（0~1），只输出 JSON。

题材清单（含昨日涨停家数、当前最高板）：
{themes}

可用新闻/事件（可能为空）：
{news}

输出格式（严格 JSON，不要多余文字）：
{{"themes": {{"题材名": {{"catalyst": 0.0-1.0, "level": "strong|normal|weak|unknown",
  "basis": "一句话依据", "sources": ["来源1"]}}}}}}

判分口径：
- strong (>=0.65)：有明确政策/产业/公司级催化，且能解释这条线今天的强度
- normal (0.35~0.65)：只有间接或常规催化
- weak (<0.35)：没有催化，纯情绪/资金
- unknown：没有足够信息（⛔ 不要猜）
"""


def _bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _themes(day: str) -> list[dict]:
    """从最近一期候选档案取题材与家数（⛔ 不联网）。"""
    days = sorted(WC.glob("candidates-*.json"))
    if not days:
        return []
    d = json.loads(days[-1].read_text(encoding="utf-8"))
    agg: dict[str, dict] = {}
    for b in ("relay", "watch"):
        for r in d.get(b) or []:
            th = str(r.get("core_theme") or r.get("theme") or "").strip()
            if not th:
                continue
            a = agg.setdefault(th, {"theme": th, "n": 0, "max_boards": 0})
            a["n"] += 1
            a["max_boards"] = max(a["max_boards"], int(r.get("boards") or 0))
    return sorted(agg.values(), key=lambda x: -x["n"])


def _news(day: str) -> list[str]:
    """可用新闻：优先当日快照的 newsflash，其次归档文件。缺失返回空（⛔ 不编）。"""
    out: list[str] = []
    try:
        s = json.loads((CACHE / "snapshot" / f"{day}.json").read_text(encoding="utf-8"))
        for sec in (s.get("sections") or {}).values():
            p = sec.get("payload") if isinstance(sec, dict) else None
            if isinstance(p, dict) and isinstance(p.get("newsflash"), list):
                out += [str(x.get("title") or "") for x in p["newsflash"] if isinstance(x, dict)]
    except Exception:  # noqa: BLE001
        pass
    try:
        nf = json.loads((CACHE / "newsflash_history" / f"daily_{day}.json").read_text(encoding="utf-8"))
        out += [str(x.get("title") or "") for x in (nf.get("newsflash") or []) if isinstance(x, dict)]
    except Exception:  # noqa: BLE001
        pass
    return [x for x in out if x][:80]


def build_prompt(day: str) -> str:
    themes = _themes(day)
    tlines = "\n".join(f"- {t['theme']}（昨日涨停 {t['n']} 家，最高 {t['max_boards']} 板）"
                       for t in themes) or "- （无）"
    news = _news(day)
    nlines = "\n".join(f"- {x}" for x in news) or "（无）"
    return TEMPLATE.format(day=day, themes=tlines, news=nlines)


def call_llm(prompt: str) -> dict | None:
    key = os.environ.get("LLM_API_KEY")
    if not key:
        return None
    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    try:
        from common.http_client import http_post
        res = http_post(
            f"{base}/chat/completions",
            {"model": model, "temperature": 0.2,
             "messages": [{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": prompt}]},
            timeout=60,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ai_catalyst] 调用失败：{e}")
        return None
    try:
        text = res["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        print(f"[ai_catalyst] 返回结构异常：{str(res)[:200]}")
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        print("[ai_catalyst] 返回里没有 JSON")
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        print("[ai_catalyst] JSON 解析失败")
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="题材催化强度（LLM）")
    ap.add_argument("--date", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只打印 prompt，不调用")
    ap.add_argument("--validate", default=None, help="校验已有 ai_advice 文件")
    args = ap.parse_args(argv)

    if args.validate:
        adv = AA.load(args.validate)
        if not adv:
            print(f"[ai_catalyst] {args.validate} 不存在或不可解析")
            return 1
        print(f"[ai_catalyst] 合法={adv['valid']} 题材 {len(adv['themes'])} 条 "
              f"错误 {adv['errors'] or '无'}")
        for name, v in list(adv["themes"].items())[:5]:
            print(f"  {name}: {v['level']} ({v['catalyst']}) {v['basis'][:40]}")
        return 0

    day = args.date or _bj().strftime("%Y-%m-%d")
    prompt = build_prompt(day)
    if args.dry_run:
        print(prompt)
        return 0

    data = call_llm(prompt)
    if data is None:
        print("[ai_catalyst] 未调用（缺 LLM_API_KEY 或调用失败）→ ⛔ 不写文件，不假装有建议")
        return 2
    payload = {
        "schema": AA.SCHEMA, "date": day, "source": "llm",
        "model": os.environ.get("LLM_MODEL", "deepseek-chat"),
        "generated_at": _bj().strftime("%Y-%m-%d %H:%M:%S"),
        "themes": data.get("themes") or {},
        "stocks": data.get("stocks") or {},
    }
    checked = AA.validate(payload)
    AA.path_for(day).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                                encoding="utf-8")
    print(f"[ai_catalyst] 已写 cache/ai_advice_{day}.json（题材 {len(checked['themes'])} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
