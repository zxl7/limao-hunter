#!/usr/bin/env python3
"""板块打分的分档验证：分数高的板块，买进去真的更赚吗？

## 为什么要这一步

`core/theme_score.py` 出的分**目前只留痕、不进判据**。要让它进权重，
必须先回答一个问题：**分档单调吗？**

- 单调（分越高、收益越高）→ 这个数有信息量，可以考虑进权重（另开一轮）
- 不单调（中间高两头低 / 无关）→ 它就是个好看的摆设，⛔ 不许进判据

⛔ 这一步不做就上线，等于拿一个未经验证的数去改仓位 —— 与本项目
「任何判据改动必须与基线 diff」的铁律冲突。

## 统计口径

- **策略口径（当日开盘买 → 当日收盘卖）**：主口径，与 `pool_ledger.au_ret` 同源
- **T+1 口径（当日开盘买 → 次日收盘卖）**：可执行性参考
  ⛔ 两条并列，禁止相加或折算（与 `shared_rules.md`「两条收益记录并列」一致）

样本分两组，⛔ 不能混：
- **全池**：每天所有有分的票（样本大，看板块分的整体区分度）
- **买入笔**：真正 `participate` 的那批（样本小，看它跟现有判据是否重复）

## 用法

    python3 scripts/research_theme_score_ab.py            # 表格
    python3 scripts/research_theme_score_ab.py --json     # 机器可读
    python3 scripts/research_theme_score_ab.py --days 35  # 窗口（默认 35）
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("pcr", ROOT / "scripts" / "pool_call_replay.py")
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

from core import pool_call as P            # noqa: E402

OUT = ROOT / "cache" / "research" / "theme_score_ab.json"

# 分档：⛔ 边界固定写死，不按样本分位切（分位切会让「今天的 60 分」和「昨天的 60 分」
# 含义不同，跨日不可比 —— 那正是这类评分最常犯的错）。
BANDS = ((70.0, "≥70 强"), (50.0, "50~69 中"), (30.0, "30~49 弱"), (0.0, "<30 无"))


def _bar(code: str, date: str):
    try:
        return R.bar(code, date)
    except Exception:                                  # noqa: BLE001
        return None


def ret_d0(code: str, date: str) -> float | None:
    """策略口径：当日开盘买 → 当日收盘卖（%）"""
    try:
        v, _ = R.day_return(code, date)
        return v
    except Exception:                                  # noqa: BLE001
        return None


def ret_t1(code: str, date: str, days: list[str]) -> float | None:
    """T+1 口径：当日开盘买 → **次日**收盘卖（%）。次日未落盘 → None。"""
    try:
        i = days.index(date)
    except ValueError:
        return None
    if i + 1 >= len(days):
        return None
    b0, b1 = _bar(code, date), _bar(code, days[i + 1])
    if not b0 or not b1:
        return None
    try:
        o = float(b0["open"])
        c = float(b1["close"])
    except (KeyError, TypeError, ValueError):
        return None
    return round((c - o) / o * 100, 2) if o > 0 else None


def band_of(score: float) -> str:
    for floor, label in BANDS:
        if score >= floor:
            return label
    return BANDS[-1][1]


def agg(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "avg": None, "med": None, "win": None}
    return {"n": len(vals), "avg": round(st.mean(vals), 2),
            "med": round(st.median(vals), 2),
            "win": round(100 * sum(1 for x in vals if x > 0) / len(vals), 1)}


def band_stats(rows: list[dict]) -> dict:
    """一组样本按分档统计（当日 + T+1）。"""
    out: dict[str, dict] = {}
    for _, label in BANDS:
        sub = [r for r in rows if band_of(r["score"]) == label]
        out[label] = {
            "n": len(sub),
            "d0": agg([r["d0"] for r in sub if r["d0"] is not None]),
            "t1": agg([r["t1"] for r in sub if r["t1"] is not None]),
            "themes": sorted({str(r["theme"]) for r in sub})[:6],
        }
    return out


def run(days_n: int = 35) -> dict:
    days = R.trading_days()[-days_n:]
    pool_rows: list[dict] = []
    pick_rows: list[dict] = []

    for d in days:
        day = R.replay_day(d, days)
        if not day:
            continue
        try:
            res = P.call_pool(day["cands"], day["quotes"], **day["kw"])
        except Exception:                              # noqa: BLE001
            continue
        for code, v in (res.get("views") or {}).items():
            sc = v.get("theme_score")
            if not isinstance(sc, dict) or not isinstance(sc.get("total"), (int, float)):
                continue
            row = {"date": d, "code": code, "name": v.get("name"),
                   "theme": v.get("theme_name"), "score": float(sc["total"]),
                   "boards": v.get("boards"),
                   "n_dims": sc.get("n_dims"), "grade": sc.get("grade"),
                   "view": v.get("view"),
                   "d0": ret_d0(code, d), "t1": ret_t1(code, d, days)}
            pool_rows.append(row)
            if v.get("view") == "participate":
                pick_rows.append(row)

    out: dict = {"window": [days[0], days[-1]] if days else [], "days": len(days),
                 "bands": {}}
    out["bands"]["pool"] = band_stats(pool_rows)
    out["bands"]["picks"] = band_stats(pick_rows)
    out["pool_n"] = len(pool_rows)
    out["picks_n"] = len(pick_rows)

    # 🔬 分层：按板数切开再看板块分 —— 板块分的「高度」维本身就是板数，
    #    不分层的话，看到的可能只是「高位板收益差」的复述，不是板块的净效应。
    def _bd(r):
        try:
            return int(r.get("boards") or 0)
        except (TypeError, ValueError):
            return 0
    layered: dict[str, dict] = {}
    for label, lo, hi in (("2板", 2, 2), ("3板", 3, 3), ("≥4板", 4, 99)):
        sub = [r for r in pool_rows if lo <= _bd(r) <= hi]
        if sub:
            layered[label] = {"n": len(sub), "bands": band_stats(sub)}
    out["layered"] = layered
    return out


def render(res: dict) -> None:
    print(f"窗口 {res['window'][0]} ~ {res['window'][1]}（{res['days']} 个交易日）\n")
    for key, title in (("pool", "全池（每天所有有分的票）"),
                       ("picks", "买入笔（真正 participate 的那批）")):
        bands = res["bands"][key]
        print(f"── {title} · 共 {res.get(key + '_n', 0)} 条 ──")
        print(f"{'分档':<12}{'n':>5}{'当日均':>9}{'当日中位':>9}{'当日胜':>8}"
              f"{'T+1均':>9}{'T+1胜':>8}  代表板块")
        print("-" * 96)
        for _, label in BANDS:
            b = bands[label]
            d0, t1 = b["d0"], b["t1"]
            if not b["n"]:
                print(f"{label:<12}{0:>5}{'—':>9}{'—':>9}{'—':>8}{'—':>9}{'—':>8}")
                continue
            f = lambda v, s="%+.2f": (s % v if isinstance(v, (int, float)) else "—")  # noqa: E731
            print(f"{label:<12}{b['n']:>5}{f(d0['avg']):>9}{f(d0['med']):>9}"
                  f"{f(d0['win'], '%.1f'):>8}{f(t1['avg']):>9}{f(t1['win'], '%.1f'):>8}"
                  f"  {'、'.join(b['themes'][:3])}")
        print()

    # 单调性判定：只看样本足够的档（n≥10），按分从高到低比当日均值
    print("── 单调性判定（当日口径，样本 ≥10 的档）──")
    ok = []
    for key, title in (("pool", "全池"), ("picks", "买入笔")):
        seq = []
        for _, label in BANDS:
            b = res["bands"][key][label]
            if b["n"] >= 10 and b["d0"]["avg"] is not None:
                seq.append((label, b["d0"]["avg"]))
        if len(seq) < 2:
            print(f"  {title}: 样本不足（{len(seq)} 档可评），不下结论")
            continue
        mono = all(a[1] >= b[1] for a, b in zip(seq, seq[1:]))
        arrow = " → ".join(f"{l} {v:+.2f}%" for l, v in seq)
        print(f"  {title}: {arrow}  ⇒ {'✅ 单调' if mono else '❌ 不单调'}")
        ok.append(mono)
    # 🔬 控板数分层：板块分的「高度」维 = 板数，不分层就分不清是板块效应还是板数效应
    layered = res.get("layered") or {}
    if layered:
        print("\n── 控板数分层（全池 · 当日口径）—— 同板数内比板块分，才是板块的净效应 ──")
        print(f"{'板数档':<8}{'分档':<12}{'n':>5}{'当日均':>9}{'当日胜':>8}")
        print("-" * 46)
        for blabel, blob in layered.items():
            first = True
            for _, label in BANDS:
                b = blob["bands"][label]
                if not b["n"]:
                    continue
                d0 = b["d0"]
                head = f"{blabel}（{blob['n']}）" if first else ""
                first = False
                f = lambda v, s="%+.2f": (s % v if isinstance(v, (int, float)) else "—")  # noqa: E731
                print(f"{head:<8}{label:<12}{b['n']:>5}{f(d0['avg']):>9}{f(d0['win'], '%.1f'):>8}")
            print()

    print("\n⛔ 分档不单调就不许进判据。⛔ 本脚本只读不写，不改任何裁决。")
    print("⚠️ T+1 与当日口径并列展示，禁止相加或折算。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    res = run(a.days)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        render(res)
        print(f"\n产物：{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
