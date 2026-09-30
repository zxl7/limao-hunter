#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""决策黑匣子归档器（N2 · 2026-09-11 用户拍板「做一批 N1+N2+N3」）

目的：把**每天真实产生的决策产物**固化成不可事后改动的一份，回答两类问题：
  ① 「当时我看到了什么？」——候选 71 因子快照 + 竞价报价 + 涨停池
  ② 「当时我判断了什么？」——AI 参与判断（ai_view）+ 实际推出去的卡

⛔ 为什么需要它（已核实的问题）：
  - `ai_view_{date}.json` 全仓**只有 1 个文件**（且是 09-10 的调试样本）——AI 判断**只有读、没有写**，
    结果永远对不上原因（`build_cards._load_ai_view` 是唯一消费方）。
  - 当日卡片散落在 `wealth_code/cache/` 平铺目录，与调试卡/测试卡混放，无按日归档。
  - 新卡同名前缀会覆盖旧卡（`card_*.json`），历史被静默吃掉。

产物结构：
  cache/decision_log/
    index.jsonl                     一日一行，便于快速检索
    YYYY-MM-DD/
      manifest.json                 来源清单 + 哈希 + 摘要 + 缺口（诚实记录）
      candidates.json               入场日因子快照（副本）
      auction_quotes.json           竞价报价（副本·有则归）
      ai_view.json                  AI 参与判断（副本·有则归）
      zt_pool.json                  涨停池（副本·优先取数据目录）
      cards/                        当日全部卡片（副本）

用法：
  python3 scripts/archive_decision.py                 # 归档最新一天
  python3 scripts/archive_decision.py --date 2026-09-10
  python3 scripts/archive_decision.py --all           # 回填全部可归档日期
  python3 scripts/archive_decision.py --dry-run       # 只看会做什么
  python3 scripts/archive_decision.py --verify        # 校验已归档哈希
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ⚠️ 2026-09-29 修（决策证据黑匣子静默丢失 11 个交易日）：
# 本脚本被 `wealth_code/scripts/run_wealth_code.py::_archive_decision()` 以 subprocess 方式调用；
# 脚本模式下 sys.path[0] = scripts/，**项目根不入 sys.path**（Python 3.11+ 不再把 cwd 入 sys.path），
# 于是模块级 `from common.trading_calendar import ...` 抛 ModuleNotFoundError → 调用方只打印一行
# `[ARCHIVE] 冻结证据失败`、不阻断主流程 → `cache/decision_log/` 自 2026-09-11 起**当天归档每天静默丢失**
# （最后一天 = 2026-09-11），「当时看到什么/判断什么」的证据链断掉，复盘与自我迭代失去底座。
# 同类坑第二次出现（第一次=`ai_ledger._close_of`，2026-09-15）：**凡项目根子目录下的独立脚本，
# 导入 common/ 前必须自己插 sys.path**。修法＝导入前插入项目根（幂等、无副作用）。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.trading_calendar import next_a_share_trading_day  # noqa: E402

WC = ROOT / "wealth_code"
WC_CACHE = WC / "cache"
ROOT_CACHE = ROOT / "cache"
LOG_DIR = ROOT_CACHE / "decision_log"
INDEX = LOG_DIR / "index.jsonl"
BJ = timezone(timedelta(hours=8))

SCHEMA = "decision_log_v1"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _d8(date10: str) -> str:
    return date10.replace("-", "")


def _cand_summary(cand: dict) -> dict:
    """候选摘要 + 每票「当时依据」（供日后归因免解析原文件）。"""
    def _pick(it: dict) -> dict:
        return {
            "code": it.get("code"), "name": it.get("name"), "theme": it.get("theme"),
            "boards": it.get("boards"), "score": it.get("score"),
            "pct": it.get("pct"), "cje_yi": it.get("cje_yi"), "hs": it.get("hs"),
            "zbc": it.get("zbc"), "is_sector_dragon": it.get("is_sector_dragon"),
            "history_relay_prob": it.get("history_relay_prob"),
            "history_relay_samples": it.get("history_relay_samples"),
            "ladder_grade": it.get("ladder_grade"),
            "theme_mates_cnt": it.get("theme_mates_cnt"),
            "main_flow_5d_yi": it.get("main_flow_5d_yi"),
            "expectation": it.get("expectation"),
            "tags": it.get("tags"),
        }
    relay = [_pick(x) for x in (cand.get("relay") or [])]
    watch = [_pick(x) for x in (cand.get("watch") or [])]
    amb = [_pick(x) for x in (cand.get("early_ambush") or [])]
    return {
        "regime": (cand.get("environment") or {}).get("regime"),
        "sentiment_score": (cand.get("environment") or {}).get("sentiment_score"),
        "counts": {"relay": len(relay), "watch": len(watch), "early_ambush": len(amb)},
        "relay": relay, "watch": watch, "early_ambush": amb,
    }


def collect(date10: str) -> tuple[list[dict], dict, list[str]]:
    """→ (来源清单, 摘要, 缺口列表)"""
    d8 = _d8(date10)
    trade_date = next_a_share_trading_day(date10)
    srcs: list[dict] = []
    gaps: list[str] = []
    summary: dict = {}

    # ① 候选快照（核心·必需）
    cand_p = WC_CACHE / f"candidates-{date10}.json"
    if cand_p.exists():
        cand = json.loads(cand_p.read_text(encoding="utf-8"))
        srcs.append({"role": "candidates", "src": str(cand_p.relative_to(ROOT)),
                     "dst": "candidates.json", "bytes": cand_p.stat().st_size,
                     "sha256": _sha256(cand_p)})
        summary["candidates"] = _cand_summary(cand)
    else:
        gaps.append(f"缺 candidates-{date10}.json（无法归档因子快照）")

    # ② 次日竞价报价：候选日=T，实际执行日=T+1；不可再按同日错误归档。
    q_p = WC_CACHE / f"auction_quotes-{trade_date}.json" if trade_date else None
    if q_p and q_p.exists():
        srcs.append({"role": "auction_quotes", "src": str(q_p.relative_to(ROOT)),
                     "dst": "auction_quotes.json", "bytes": q_p.stat().st_size,
                     "sha256": _sha256(q_p), "trade_date": trade_date})
    else:
        gaps.append(f"缺 auction_quotes-{trade_date or '未知'}.json（竞价数据未落地）")

    # ③ 9:25 池内裁决也属于执行日，和竞价快照同一时点冻结。
    pool_p = WC_CACHE / f"pool_call-{trade_date}.json" if trade_date else None
    if pool_p and pool_p.exists():
        srcs.append({"role": "pool_call", "src": str(pool_p.relative_to(ROOT)),
                     "dst": "pool_call.json", "bytes": pool_p.stat().st_size,
                     "sha256": _sha256(pool_p), "trade_date": trade_date})
    else:
        gaps.append(f"缺 pool_call-{trade_date or '未知'}.json（9:25池内裁决未冻结）")

    # ④ AI 参与判断（按 decision_date 归属到候选日）
    ai_p, ai_match = _find_ai_view(date10)
    if ai_p is not None:
        try:
            ai = json.loads(ai_p.read_text(encoding="utf-8"))
            n = len((ai.get("views") or {}))
        except Exception:
            n = None
        srcs.append({"role": "ai_view", "src": str(ai_p.relative_to(ROOT)),
                     "dst": "ai_view.json", "bytes": ai_p.stat().st_size,
                     "sha256": _sha256(ai_p), "match": ai_match})
        summary["ai_view_entries"] = n
    else:
        gaps.append(f"缺 AI 参与判断（decision_date={date10} 的 ai_view_*.json 不存在）")

    # ⑤ 涨停池（数据目录优先，回退归档目录）
    for role, p in (("zt_pool_data", WC / "data" / "zt_history" / f"zt_{date10}.json"),
                    ("zt_pool_cache", ROOT_CACHE / "zt_history" / f"zt_{date10}.json")):
        if p.exists():
            srcs.append({"role": role, "src": str(p.relative_to(ROOT)), "dst": "zt_pool.json",
                         "bytes": p.stat().st_size, "sha256": _sha256(p)})
            break
    else:
        gaps.append(f"缺 zt_{date10}.json（两个 zt_history 目录均无）")

    # ⑥ 当日卡片（盘前/潜伏/竞价验证/其他 card_*）——全留，但标注调试卡
    cards = []
    for pat in (f"panqian_card_{d8}.json", f"ambush_card_{d8}.json", f"card_{d8}_*.json"):
        for p in sorted(WC_CACHE.glob(pat)):
            is_debug = ("debug" in p.name.lower()) or ("test" in p.name.lower())
            cards.append({"role": "card_debug" if is_debug else "card",
                          "src": str(p.relative_to(ROOT)),
                          "dst": f"cards/{p.name}", "bytes": p.stat().st_size,
                          "sha256": _sha256(p)})
    srcs.extend(cards)
    summary["cards"] = len(cards)
    summary["cards_production"] = sum(1 for c in cards if c["role"] == "card")
    summary["cards_debug"] = sum(1 for c in cards if c["role"] == "card_debug")
    if not cards:
        gaps.append(f"当日无卡片文件（panqian/ambush/card_{d8}_*）")

    summary["sources"] = len(srcs)
    summary["trade_date"] = trade_date
    return srcs, summary, [g for g in gaps if g]


def archive_one(date10: str, dry: bool = False) -> dict:
    day_dir = LOG_DIR / date10
    srcs, summary, gaps = collect(date10)
    rec = {"date": date10, "schema": SCHEMA, "archived_at": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S"),
           "sources": srcs, "summary": summary, "gaps": gaps}

    if dry:
        print(f"[dry-run] {date10}: {len(srcs)} 个来源 → {day_dir}")
        for s in srcs:
            print(f"    {s['role']:<16} {s['src']}  ({s['bytes']}B)")
        for g in gaps:
            print(f"    ⚠️ {g}")
        return rec

    (day_dir / "cards").mkdir(parents=True, exist_ok=True)
    for s in srcs:
        dst = day_dir / s["dst"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / s["src"], dst)
    (day_dir / "manifest.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

    # index.jsonl —— 同日覆盖（保持一行一天）
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    if INDEX.exists():
        for line in INDEX.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("date") != date10:
                rows.append(r)
    rows.append({
        "date": date10, "archived_at": rec["archived_at"], "schema": SCHEMA,
        "sources": len(srcs),
        "relay": summary.get("candidates", {}).get("counts", {}).get("relay"),
        "watch": summary.get("candidates", {}).get("counts", {}).get("watch"),
        "ai_view": summary.get("ai_view_entries"),
        "cards": summary.get("cards"),
        "gaps": len(gaps),
    })
    rows.sort(key=lambda r: r.get("date") or "")
    INDEX.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return rec


def verify_all() -> int:
    bad = 0
    if not LOG_DIR.exists():
        print("尚无归档目录")
        return 1
    for man in sorted(LOG_DIR.glob("*/manifest.json")):
        rec = json.loads(man.read_text(encoding="utf-8"))
        for s in rec.get("sources", []):
            p = man.parent / s["dst"]
            if not p.exists():
                print(f"❌ {rec['date']} 缺文件 {s['dst']}"); bad += 1; continue
            if _sha256(p) != s["sha256"]:
                print(f"❌ {rec['date']} 哈希不符 {s['dst']}（归档后被改动）"); bad += 1
    print(f"校验完成：{'全部一致 ✅' if bad == 0 else f'{bad} 处异常'}")
    return 0 if bad == 0 else 1


def available_dates() -> list[str]:
    """可归档日期 = **有候选快照的日期**。

    ⛔ 不得把 ai_view 的文件名当日期源（2026-09-12 修正）：
    `ai_view_{d}.json` 按「**该判断适用的交易日**」命名（= candidates 日的下一交易日），
    由接力卡任务步骤 5 `ai_ledger.py record --decision-date T` 产出。
    若按其文件名生成日期，会造出无候选的幽灵日（实证：曾归档出空的 2026-09-14）。
    """
    return sorted({p.stem.replace("candidates-", "") for p in WC_CACHE.glob("candidates-*.json")})


def _find_ai_view(date10: str) -> tuple[Path | None, str]:
    """定位属于 date10 决策的 AI 判断文件 → (path, 匹配方式)。

    首选 decision_date == date10（口径权威）；回退文件名 == date10（旧命名兼容）。
    """
    cands: list[Path] = []
    for p in sorted(ROOT_CACHE.glob("ai_view_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(d.get("decision_date") or "") == date10:
            cands.append(p)
    if cands:
        return cands[-1], "decision_date"
    p = ROOT_CACHE / f"ai_view_{date10}.json"
    return (p, "filename") if p.exists() else (None, "none")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD（默认=最新可归档日）")
    ap.add_argument("--all", action="store_true", help="回填全部可归档日期")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="校验已归档哈希")
    a = ap.parse_args()

    if a.verify:
        return verify_all()

    dates = available_dates()
    if a.all:
        targets = dates
    elif a.date:
        targets = [a.date]
    else:
        targets = dates[-1:]
    if not targets:
        print("无可归档日期（wealth_code/cache 下无 candidates-*.json）", file=sys.stderr)
        return 1

    for d in targets:
        rec = archive_one(d, dry=a.dry_run)
        if not a.dry_run:
            n = len(rec["sources"])
            tag = "" if not rec["gaps"] else f" · 缺口 {len(rec['gaps'])}"
            print(f"✓ {d}: 归档 {n} 个来源{tag}")
    if not a.dry_run:
        print(f"\n归档目录: {LOG_DIR} · 索引: {INDEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
