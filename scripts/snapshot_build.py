#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日市场快照 · 从现有产物聚合（2026-11-04 用户架构要求）。

用户原话：「让数据能输出到一个地方去……各个地方引用时都是从这里拿数据，
         但整体数据源是在一个地方的。当我们进行综合判断时，就能对数据源一目了然。」

## 这个脚本干什么

把**已经算好**的各维度结论搬进 `cache/snapshot/<date>.json`：
    market / themes / ladder / lines / candidates / verdict / quotes / gates

⛔ **只搬运，不重算**：不碰任何阈值、分数、闸门结论。
⛔ **不改原始产物**：原文件继续存在，本脚本只读不写它们。
⛔ **幂等**：同一输入重复跑结果一致；已存在的 section 按 upsert 覆盖。

## 用法

```bash
python3 scripts/snapshot_build.py --date 2026-11-04          # 构建当日快照
python3 scripts/snapshot_build.py --date 2026-11-04 --check   # 只报告覆盖，不写
python3 scripts/snapshot_build.py --date 2026-11-04 --guard   # 构建 + 一致性校验 + 落 health（日常守门）
python3 -m core.snapshot coverage --date 2026-11-04           # 看覆盖（含 health/死维度）
python3 -m core.snapshot dimensions                           # 维度状态台账
python3 -m core.snapshot stock --date 2026-11-04 --code 600892  # 单票聚合视图
```
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "wealth_code"))
sys.path.insert(0, str(ROOT))

from core import snapshot as snap  # noqa: E402


def _load(p: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _latest(pattern: str) -> Path | None:
    fs = sorted((ROOT / "cache").glob(pattern))
    return fs[-1] if fs else None


# ── 各 section 的构造（只搬运结论，不重算）────────────────────────────────
def build_market(date10: str) -> dict[str, Any] | None:
    """情绪·广度·涨停数。源：feedback_matrix + market_memory。"""
    fb = _load(ROOT / "cache" / "feedback_matrix.json") or {}
    if fb.get("date") != date10:
        fb = {}
    mm = _load(ROOT / f"cache/market_memory_{date10}.json") or {}
    ms = mm.get("market_state") or {}
    if not fb and not ms:
        return None
    return {
        # ⛔ 情绪只认 feedback_matrix（sentiment_score/label）；market_memory 的文案作对照
        "sentiment_score": fb.get("sentiment_score"),
        "sentiment_label": fb.get("sentiment_label"),
        "sentiment_text": ms.get("sentiment"),
        "feedback_date": fb.get("date"),
        "total_zt": ms.get("total_zt"),
        "broken": ms.get("broken"),
        "breadth_up": ms.get("breadth_up"),
        "breadth_down": ms.get("breadth_down"),
        "index_avg": ms.get("index_avg"),
        "dominant_sector": ms.get("dominant_sector"),
    }


def build_ladder(date10: str) -> dict[str, Any] | None:
    """连板梯队·高度。源：上一交易日 zt_history + board_track。"""
    zt = _load(_prev_zt(date10) or Path("/nonexistent")) or {}
    rows = zt.get("zt_list") or []
    if not rows:
        return None
    by_board: dict[int, list[str]] = {}
    for r in rows:
        try:
            b = int(r.get("boards") or 0)
        except (TypeError, ValueError):
            continue
        if b >= 2:
            by_board.setdefault(b, []).append(str(r.get("name") or ""))
    return {
        "asof_pool_date": zt.get("date"),
        "zt_count": len(rows),
        "max_boards": zt.get("max_boards"),
        "ladder": {str(k): v for k, v in sorted(by_board.items(), reverse=True)},
        "top_codes": zt.get("top_codes"),
    }


def _prev_zt(date10: str) -> Path | None:
    """当日可用的**完整**涨停池：<= date10 的最新一份，且必须带 `zt_list`。

    ⛔ 11-04 实证：当日盘中快照 `zt_2026-11-04.json` 只有 `intraday_points`、
       没有 `zt_list`（盘后才定稿）。按文件名取最新会把盘中半成品当完整池，
       导致梯队 section 直接空掉。故必须按**内容**筛。
    """
    fs = sorted((ROOT / "cache" / "zt_history").glob("zt_*.json"))
    picked = None
    for f in fs:
        day = f.stem[3:]
        if len(day) != 10 or day > date10:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and d.get("zt_list"):
            picked = f
    return picked


def build_lines(date10: str) -> dict[str, Any] | None:
    """每条线的风向标。源：pool_call.load_line_cores（唯一源，⛔ 不在这里重算）。"""
    try:
        from core import pool_call as pc
        cores = pc.load_line_cores(date10, root=ROOT)
    except Exception:
        return None
    if not cores:
        return None
    out: dict[str, Any] = {}
    for fam, v in cores.items():
        if not isinstance(v, dict):
            continue
        out[fam] = {
            "code": v.get("code"), "name": v.get("name"),
            "boards": v.get("boards"),
            "in_pool": v.get("in_pool"),
            "broken": v.get("broken"),          # 上一波更高核心已断
            "beacon": v.get("beacon"),          # 今日在板最强（联动对象）
        }
    return out


def build_candidates(date10: str) -> dict[str, Any] | None:
    """竞价输入候选，只认严格前一交易日的权威文件。"""
    candidate_date = _prev_trade_day(date10)
    if not candidate_date:
        return None
    f = ROOT / "wealth_code" / "cache" / f"candidates-{candidate_date}.json"
    cand = _load(f)
    if not cand or cand.get("date") != candidate_date:
        return None
    sec = snap.build_candidate_section(cand)
    sec["candidate_date"] = cand.get("date")
    sec["source_file"] = f.name
    return sec


def _prev_trade_day(date10: str) -> str:
    """上一交易日（唯一源 `common.trading_calendar`，⛔ 禁止自己 weekday 推算）。

    当日是交易日 → 返回上一个交易日（盘后产物 `candidates-<D>.json` 的 D 是当日）；
    当日不是交易日 → 返回它之前的最近交易日。
    """
    try:
        from common import trading_calendar as tc
        if tc.is_a_share_trading_day(date10):
            return str(tc.prev_a_share_trading_day(date10) or "")
        return str(tc.last_a_share_trading_day(date10) or "")
    except Exception:
        return ""


def build_verdict(date10: str) -> dict[str, Any] | None:
    """09:25 终裁。源：pool_call-<date>.json（已冻结，⛔ 不重跑裁决）。"""
    d = _load(ROOT / "wealth_code" / "cache" / f"pool_call-{date10}.json")
    if not d:
        return None
    meta = d.get("meta") or {}
    return {
        "stance": d.get("stance"),
        "quota": d.get("quota"),
        "effective_quota": meta.get("effective_quota"),
        "participate": meta.get("participate"),
        "market_open": meta.get("market_open"),
        "slot_policy": meta.get("slot_policy"),
        "anchor_lines": ((meta.get("defense") or {}).get("anchor") or {}).get("lines"),
        "views": d.get("views"),
    }


def build_quotes(date10: str) -> dict[str, Any] | None:
    """09:25 竞价报价。源：auction_quotes-<date>.json。"""
    d = _load(ROOT / "wealth_code" / "cache" / f"auction_quotes-{date10}.json")
    return d or None


def build_gates(date10: str) -> dict[str, Any] | None:
    """各闸拦截汇总（谁被拦、为什么）。源：candidates.diagnostics。"""
    candidate_date = _prev_trade_day(date10)
    if not candidate_date:
        return None
    cand = _load(ROOT / "wealth_code" / "cache" / f"candidates-{candidate_date}.json")
    if not cand or cand.get("date") != candidate_date:
        return None
    dg = cand.get("diagnostics") or {}
    if not dg:
        return None
    pick = ("pool_admit", "outpool_check", "thin_seal_top", "dragon_gate",
            "high_win_gate", "seal_gate_exempt", "leader_turnover_exempt")
    out: dict[str, Any] = {}
    for k in pick:
        v = dg.get(k)
        if v is None:
            continue
        # ⛔ 只保留「谁被拦」的结论字段，不搬全量明细
        if isinstance(v, dict):
            out[k] = {kk: v.get(kk) for kk in
                      ("enabled", "before", "after", "dropped", "moved_names",
                       "dropped_names", "count", "names", "regime")
                      if kk in v}
        else:
            out[k] = v
    if out:
        out["candidate_date"] = cand.get("date")
    return out or None


def build_themes(date10: str) -> dict[str, Any] | None:
    """题材轮动·主线。源：theme_rotation + theme_snapshot。"""
    rot = _load(ROOT / "cache" / "theme_rotation.json") or {}
    if rot.get("date") != date10:
        rot = {}
    snapf = _load(ROOT / "cache" / "theme_snapshot" / f"theme_{_prev_trade_day(date10)}.json")
    if not rot and not snapf:
        return None
    out: dict[str, Any] = {}
    if rot:
        out["rotation"] = {k: rot.get(k) for k in
                           ("date", "themes", "top", "leaders") if k in rot}
    if snapf:
        mp = snapf.get("mapping") or {}
        cnt: dict[str, int] = {}
        for v in mp.values():
            th = str((v or {}).get("theme") or "").strip()
            if th:
                cnt[th] = cnt.get(th, 0) + 1
        out["theme_counts"] = dict(sorted(cnt.items(), key=lambda x: -x[1])[:15])
        out["snapshot_date"] = snapf.get("date")
    return out or None


BUILDERS = {
    "market": build_market,
    "themes": build_themes,
    "ladder": build_ladder,
    "lines": build_lines,
    "candidates": build_candidates,
    "verdict": build_verdict,
    "quotes": build_quotes,
    "gates": build_gates,
}


def main(argv: list[str]) -> int:
    global ROOT
    original_root = ROOT
    try:
        return _main(argv)
    finally:
        ROOT = original_root


def _main(argv: list[str]) -> int:
    global ROOT
    import argparse
    from datetime import datetime
    ap = argparse.ArgumentParser(description="构建每日市场快照（数据源聚合层）")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--check", action="store_true", help="只报告，不写盘")
    ap.add_argument("--only", default="", help="只构建指定 section（逗号分隔）")
    ap.add_argument("--guard", action="store_true",
                    help="构建后跑一致性校验并落 health section（日常守门·只告警不阻断）")
    ap.add_argument("--root", default="",
                    help="覆盖项目根目录，同时隔离输入与快照输出（测试用）")
    a = ap.parse_args(argv)

    from pathlib import Path as _Path
    _root = _Path(a.root).resolve() if a.root else None
    if _root is not None:
        ROOT = _root
    want = [x.strip() for x in a.only.split(",") if x.strip()] or list(BUILDERS)
    built, skipped, failed = [], [], []

    for name in want:
        fn = BUILDERS.get(name)
        if fn is None:
            failed.append((name, "未知 section"))
            continue
        try:
            payload = fn(a.date)
        except Exception as e:                       # 单维失败不阻断其它维
            failed.append((name, f"{type(e).__name__}: {e}"))
            continue
        if payload is None:
            skipped.append((name, "源产物缺失"))
            continue
        if a.check:
            built.append(name)
            continue
        try:
            ok = snap.put_section(a.date, name, payload, root=_root)
        except Exception as e:
            failed.append((name, f"写盘失败：{type(e).__name__}: {e}"))
            continue
        (built if ok else failed).append(name if ok else (name, "写盘失败"))

    print(f"📅 {a.date}  快照构建{'（dry-run）' if a.check else ''}")
    if built:
        verb = "可写" if a.check else "写入"   # ⛔ dry-run 不说成已写入（诚实）
        print(f"  ✅ {verb} {len(built)}: {', '.join(str(x) for x in built)}")
    for n, why in skipped:
        print(f"  ⬜ 跳过 {n}：{why}")
    for n, why in failed:
        print(f"  ❌ 失败 {n}：{why}")
    if not a.check:
        c = snap.coverage(a.date, root=_root)
        print(f"  📊 覆盖 {c['present']}/{c['total']}")
        if a.guard:
            # 日常守门（capture_daily 第 14 步调用）：构建完 8 个 section 后
            # 跑一致性校验，把结论落进 `health` section，打开快照即知今日健不健康。
            # ⛔ 有矛盾只告警、返回码仍为 0 —— 守门不阻断盘后链（与其余末步同一纪律）。
            try:
                h = snap.write_health(a.date, root=_root)
                stored = snap.read_section(a.date, "health", root=_root)
                if not stored or stored.get("checked_at") != h.get("checked_at") or stored.get("stale"):
                    raise OSError("health 未成功持久化或已失效")
                mark = "✅ 一致" if h["ok"] else f"⚠️ {len(h['issues'])} 处矛盾"
                print(f"  🩺 守门 {mark} · 校验于 {h['checked_at']}"
                      + (f" · 提示 {len(h['warnings'])}" if h["warnings"] else ""))
                for it in h["issues"]:
                    print(f"  🩺 ⛔ [{it['kind']}] {it.get('hint')}")
                for it in h["warnings"]:
                    print(f"  🩺 ⚠️ [{it['kind']}] {it.get('hint')}")
            except Exception as e:                    # 单步失败不阻断盘后链
                failed.append(("health", f"{type(e).__name__}: {e}"))
                print(f"  守门失败：{type(e).__name__}: {e}")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
