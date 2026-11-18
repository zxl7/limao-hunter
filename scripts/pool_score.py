#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选池评分速查（2026-11-18 用户：「给观察池的票先打个分，我在终端看」）。

## 输出什么

对指定交易日的池子逐票给出：

| 列 | 含义 |
|---|---|
| 票 / 板 | 名称、昨日板数 |
| 开幅 | 09:25 实际开幅（没有竞价数据则写「无报价」） |
| 封板% | 模型给的「今天封住」概率 |
| 期望% | P×封板溢价 + (1−P)×未封损失（**这就是排序依据**） |
| 分 | 盘后 quant_score（决定 relay_rank 的那个） |
| 档 | 盘后分档 / 共振分 / 结局（当日是否封板、当日收益、T+1） |

## 用法

    python3 scripts/pool_score.py                 # 最近一个有报价的交易日
    python3 scripts/pool_score.py 2026-11-17      # 指定交易日
    python3 scripts/pool_score.py 2026-11-17 -v   # 附带每票的期望算式

## 口径

- 池子 = 该交易日**前一晚**建的 `candidates-<D-1>.json`
- 报价 = 该交易日的 `auction_quotes-<D>.json`
- 结局 = `cache/pool_ledger/<D>.json`（T+1 用 `t1_ret`，当日用 `au_ret`，封板看 `chg_pct`）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core import seal_ev as se  # noqa: E402


def _j(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _days() -> list[str]:
    days = sorted(p.stem.split("-", 1)[1] for p in (ROOT / "wealth_code/cache").glob("auction_quotes-*.json"))
    return days


def _pool_for(day: str) -> tuple[dict, str]:
    files = sorted((ROOT / "wealth_code/cache").glob("candidates-*.json"))
    prev = [f for f in files if f.stem.split("-", 1)[1] < day]
    if not prev:
        return {}, ""
    return _j(prev[-1]) or {}, prev[-1].stem.split("-", 1)[1]


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    args = [a for a in argv if not a.startswith("-")]
    days = _days()
    if not days:
        print("⛔ 没有竞价报价文件，无法评分")
        return 2
    day = args[0] if args else days[-1]
    cand, pool_day = _pool_for(day)
    quotes = _j(ROOT / "wealth_code/cache" / f"auction_quotes-{day}.json") or {}
    led = _j(ROOT / "cache/pool_ledger" / f"{day}.json") or {}
    outro = {str(r.get("code")): r for r in (led.get("rows") or [])}

    rows = (cand.get("relay") or []) + (cand.get("watch") or [])
    if not rows:
        print(f"⛔ {day} 没有可用候选（池来自 {pool_day or '缺失'}）")
        return 2

    print(f"交易日 {day}｜池来自 {pool_day}（{len(cand.get('relay') or [])} 接力 + "
          f"{len(cand.get('watch') or [])} 观察）｜报价 {len(quotes)} 只")
    print("-" * 96)
    print(f"{'票':8s}{'板':>3s} {'开幅':>8s} {'封板%':>6s} {'期望%':>7s} {'盘后':>6s} {'共振':>4s} {'结局':>18s}")
    out = []
    for r in rows:
        code = str(r.get("code") or "")
        q = quotes.get(code) or {}
        prev = q.get("prev_close")
        price = q.get("auction_price")
        gap = None
        if isinstance(prev, (int, float)) and prev and isinstance(price, (int, float)):
            gap = (price / prev - 1) * 100
        ev = se.evaluate(gap=gap, eod_score=r.get("score"),
                         mates=r.get("theme_mates_cnt"), zbc=r.get("zbc"))
        res = outro.get(code) or {}
        tail = "无成交"
        if isinstance(res.get("chg_pct"), (int, float)):
            sealed = float(res["chg_pct"]) >= float(res.get("limit_up_pct") or 10) - 0.2
            tail = ("封板" if sealed else "未封")
            if isinstance(res.get("au_ret"), (int, float)):
                tail += f" 当日{res['au_ret']:+.1f}%"
            if isinstance(res.get("t1_ret"), (int, float)):
                tail += f" T+1{res['t1_ret']:+.1f}%"
        out.append((ev["ev"], r, gap, ev, tail))
    out.sort(key=lambda x: -x[0])
    for _, r, gap, ev, tail in out:
        reso = (r.get("close_resonance") or {}).get("score")
        print(f"{(r.get('name') or '')[:7]:8s}{(r.get('boards') or 0):>3} "
              f"{(f'{gap:+.2f}%' if gap is not None else '—'):>8s} "
              f"{ev['p_seal'] * 100:>5.0f}% {ev['ev']:>+7.2f} "
              f"{(r.get('score') if isinstance(r.get('score'), (int, float)) else 0):>6.1f} "
              f"{(reso if reso is not None else '-'):>4} {tail:>18s}")
        if verbose:
            print(f"{'':10s}└ {ev['why']}")
    print("-" * 96)
    ok = [x for x in out if x[3]["ev"] >= 0 and not x[3]["missing"]]
    print(f"按期望降序；期望 ≥ 0 的有 **{len(ok)}** 只"
          + (f"：{'、'.join(str(x[1].get('name')) for x in ok[:5])}" if ok else "（今日无正期望票 → 空仓合理）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
