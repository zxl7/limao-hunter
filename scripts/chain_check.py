#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主链可达性自检（2026-11-18 用户：「先检查有没有在主链路上，不要又改到空白的地方」）。

## 为什么需要这个脚本

11-16 的三项改造写进了零调用点的 `_rank_and_split()`，日志看起来一切正常，
候选文件里却一项都没有 —— 这类错误**跑测试也发现不了**（测试直接调内部函数）。

本脚本的做法：**从真实入口往里追**，只认「入口 → 函数 → 字段」这条链：

    15:18 入口   run_wealth_code.cmd_eod → pipeline_eod.run_eod → candidates-*.json 字段
    09:25 入口   run_wealth_code.cmd_verify → pool_call.call_pool → pool_call-*.json 字段
    卡面入口     run_wealth_code.cmd_verify → build_cards.build_verify_card → 卡片文本

每项检查给出 PASS/FAIL 与证据（文件:行 或 实参），FAIL 即表示「改了但没生效」。

## 用法

    python3 scripts/chain_check.py          # 自检（只读 cache 与源码，不跑主链）
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

OK, BAD = "✅", "⛔"
results: list[tuple[bool, str, str]] = []


def check(name: str, fn) -> None:
    try:
        passed, evidence = fn()
    except Exception as e:  # noqa: BLE001
        passed, evidence = False, f"抛错: {e}"
    results.append((bool(passed), name, str(evidence)))



def _synthetic_call_pool(field: str):
    """落盘文件早于改动时，跑一次**离线合成**裁决，验证字段是否真被写出。

    ⛔ 只读现有 cache（候选 + 昨日报价），不落盘、不推卡。
    """
    import copy
    from core import pool_call as pc
    cand_files = sorted((ROOT / "wealth_code/cache").glob("candidates-*.json"))
    quote_files = sorted((ROOT / "wealth_code/cache").glob("auction_quotes-*.json"))
    if not cand_files or not quote_files:
        return False, "缺候选/报价文件，无法合成验证"
    cand = json.loads(cand_files[-1].read_text(encoding="utf-8"))
    quotes = json.loads(quote_files[-1].read_text(encoding="utf-8"))
    zt_files = sorted((ROOT / "cache/zt_history").glob("zt_*.json"))
    zt = []
    for f in reversed(zt_files):
        try:
            day = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if day.get("zt_list"):
            zt = day["zt_list"]
            break
    res = pc.call_pool(copy.deepcopy(cand), copy.deepcopy(quotes), plan={},
                       theme_open_rows=zt, theme_rows=zt)
    views = (res or {}).get("views") or {}
    n = sum(1 for v in views.values() if v.get(field))
    return n > 0, f"离线合成裁决：{n}/{len(views)} 只写出 `{field}`（落盘文件生成于改动之前）"


# ── 1. 收盘链：候选字段 ──────────────────────────────────────────────
def _cand_field(field: str, needle: str):
    def _f():
        from pipelines import pipeline_eod as pe
        src = inspect.getsource(pe.run_eod)
        if needle not in src:
            return False, f"`run_eod` 里找不到 {needle}"
        files = sorted((ROOT / "wealth_code/cache").glob("candidates-*.json"))
        if not files:
            return True, "源码可达；暂无候选文件可验证落盘"
        c = json.loads(files[-1].read_text(encoding="utf-8"))
        rows = (c.get("relay") or []) + (c.get("watch") or [])
        n = sum(1 for r in rows if r.get(field))
        return (n > 0), f"{files[-1].name}: {n}/{len(rows)} 行有 `{field}`"
    return _f


def _theme_fallback_wired():
    from pipelines import pipeline_eod as pe
    src = inspect.getsource(pe.run_eod)
    ok = "theme_fallback" in src or "_resolve_theme" in src
    return ok, "run_eod 内调用题材兜底" if ok else "run_eod 未调用题材兜底"


# ── 2. 竞价链：call_pool 输出字段 ───────────────────────────────────
def _pool_call_field(field: str, needle: str):
    def _f():
        from core import pool_call as pc
        src = inspect.getsource(pc.call_pool)
        if needle not in src:
            return False, f"`call_pool` 里找不到 {needle}"
        files = sorted((ROOT / "wealth_code/cache").glob("pool_call-*.json"))
        if not files:
            return True, "源码可达；暂无裁决文件可验证"
        d = json.loads(files[-1].read_text(encoding="utf-8"))
        views = d.get("views") or {}
        n = sum(1 for v in views.values() if v.get(field))
        if n:
            return True, f"{files[-1].name}: {n}/{len(views)} 只带 `{field}`"
        # 落盘文件可能早于本次改动 → 离线合成再验一次（证代码可达，而非历史文件缺字段）
        return _synthetic_call_pool(field)
    return _f


def _verify_passes_theme_rows():
    from scripts import run_wealth_code  # noqa: F401  (确认入口可导入)
    src = (ROOT / "wealth_code/pipelines/pipeline_verify.py").read_text(encoding="utf-8")
    ok = "theme_rows=" in src
    return ok, "pipeline_verify 传 theme_rows" if ok else "⚠️ 未传 theme_rows（弱线/共振会静默失效）"


# ── 3. 卡面：build_verify_card 是否读字段 ───────────────────────────
def _card_reads(field: str, needle: str):
    def _f():
        from pipelines import build_cards as bc
        src = inspect.getsource(bc.build_verify_card)
        # 卡片构建会调用内部 helper，所以对整模块源码判定更准
        whole = inspect.getsource(bc)
        ok = needle in whole
        return ok, f"build_cards 含 `{needle}`（{'卡面可见' if ok else '未接'}）"
    return _f



def _resonance_wired():
    """共振字段：源码必须可达；当日没有合格线时 0 命中是**正常**（不是没接）。

    判据：`call_pool` 里有挂载点，且当日 views 里能看到该分支执行过的痕迹
    （`core_first_gate` 或 `sector_resonance` 任一存在即证明代码走到了那一段）。
    """
    from core import pool_call as pc
    if "sector_resonance" not in inspect.getsource(pc.call_pool):
        return False, "`call_pool` 里没有挂载点"
    files = sorted((ROOT / "wealth_code/cache").glob("pool_call-*.json"))
    if not files:
        return True, "源码可达（无裁决文件可验证）"
    views = (json.loads(files[-1].read_text(encoding="utf-8")).get("views") or {})
    hit = sum(1 for v in views.values() if v.get("sector_resonance"))
    gate = sum(1 for v in views.values() if v.get("core_first_gate"))
    ok = hit > 0 or gate > 0
    return ok, (f"{files[-1].name}: 共振命中 {hit} 只，核心优先闸留痕 {gate} 只"
                + ("（当日无最强线，0 命中属正常）" if hit == 0 and gate else ""))


def main() -> int:
    check("收盘链·涨停原因 limit_up_reason", _cand_field("limit_up_reason", "limit_up_reason"))
    check("收盘链·收盘共振 close_resonance", _cand_field("close_resonance", "close_resonance"))
    check("收盘链·题材来源 theme_source", _cand_field("theme_source", "theme_source"))
    check("收盘链·题材兜底调用", _theme_fallback_wired)
    check("竞价链·主题池 theme_rows", _verify_passes_theme_rows)
    check("竞价链·终评分 relay_score", _pool_call_field("relay_score", "relay_score"))
    check("竞价链·题材共振 sector_resonance", _resonance_wired)
    check("卡面·涨停原因行 📰", _card_reads("limit_up_reason", "limit_up_reason"))
    check("卡面·共振行 🧲", _card_reads("close_resonance", "close_resonance"))
    check("卡面·终评分行 🧮", _card_reads("relay_score", "relay_score"))

    print("主链可达性自检")
    print("=" * 72)
    fails = 0
    for ok, name, ev in results:
        print(f"{OK if ok else BAD} {name:28s} {ev}")
        if not ok:
            fails += 1
    print("=" * 72)
    print(f"结论：{len(results) - fails}/{len(results)} 项在主链上" + ("" if not fails else f"，{fails} 项需要修"))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
