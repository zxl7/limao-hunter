#!/usr/bin/env python3
"""题材动量（变化）的分档验证：这条线「在变热」还是「在变冷」，哪个更适合接力？

## 背景

`core/theme_score.py` 的**热度**分验证下来不单调（分越高反而越差），说明「线有多热」
已经被身位/板数覆盖。所以改打**变化**：

| 特征 | 含义 | 取值 |
|---|---|---|
| `dir` | 家数环比方向 | rising(+2↑) / up(+1) / flat(0) / down(-1) / falling(≤-2) |
| `pos_ratio` | 当前家数 ÷ 近 5 日峰值 | 1.0=仍在峰值，0.2=退掉八成 |
| `streak` | 连续有涨停的天数 | 1=首日，≥5=老线 |
| `n` | 绝对家数 | 规模 |

## 为什么每个特征单独分档，而不直接合成一个分

上一轮合成热度分的教训：**先合成再验证，一旦不单调就不知道是哪一维坏了**。
这一轮反过来 —— 先把每一维单独摊开看谁有信号，只对有信号的维合成。

## 口径

- 当日口径（开盘买→当日收盘，主）+ T+1 口径（次日收盘，可执行性参考），⛔ 不相加不折算
- 样本：全池有动量的票（`theme_momentum` 非空）
- ⛔ 不用 `theme_rotation.json`（只有一份，回放＝未来函数）；
  底座是 `cache/theme_momentum/*.json`，由 `build_theme_momentum.py` 从
  `zt_history` + `theme_snapshot` 重建

## 用法

    python3 scripts/research_theme_momentum_ab.py
    python3 scripts/research_theme_momentum_ab.py --days 30 --json
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

OUT = ROOT / "cache" / "research" / "theme_momentum_ab.json"

DIR_ORDER = ("rising", "up", "flat", "down", "falling")


def ret_d0(code: str, date: str) -> float | None:
    try:
        return R.day_return(code, date)[0]
    except Exception:                                  # noqa: BLE001
        return None


def ret_t1(code: str, date: str, days: list[str]) -> float | None:
    try:
        i = days.index(date)
    except ValueError:
        return None
    if i + 1 >= len(days):
        return None
    try:
        b0, b1 = R.bar(code, date), R.bar(code, days[i + 1])
    except Exception:                                  # noqa: BLE001
        return None
    if not b0 or not b1:
        return None
    try:
        o, c = float(b0["open"]), float(b1["close"])
    except (KeyError, TypeError, ValueError):
        return None
    return round((c - o) / o * 100, 2) if o > 0 else None


def bucket(feat: str, v) -> str | None:
    """把一个特征值落进桶。⛔ 边界写死，不用样本分位（分位切跨日不可比）。"""
    if v is None:
        return None
    if feat == "dir":
        return v if v in DIR_ORDER else None
    if feat == "pos_ratio":
        x = float(v)
        if x >= 0.66:
            return "位置≥0.66"
        if x >= 0.33:
            return "位置0.33~0.66"
        return "位置<0.33"
    if feat == "streak":
        n = int(v)
        if n >= 5:
            return "连≥5日"
        if n >= 2:
            return "连2~4日"
        return "首日"
    if feat == "n":
        n = int(v)
        if n >= 6:
            return "n≥6"
        if n >= 3:
            return "n3~5"
        return "n1~2"
    return None


def agg(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "avg": None, "win": None}
    return {"n": len(vals), "avg": round(st.mean(vals), 2),
            "win": round(100 * sum(1 for x in vals if x > 0) / len(vals), 1)}


def run(days_n: int = 40) -> dict:
    days = R.trading_days()[-days_n:]
    rows: list[dict] = []
    for d in days:
        day = R.replay_day(d, days)
        if not day:
            continue
        try:
            res = P.call_pool(day["cands"], day["quotes"], **day["kw"])
        except Exception:                              # noqa: BLE001
            continue
        for code, v in (res.get("views") or {}).items():
            m = v.get("theme_momentum")
            if not isinstance(m, dict):
                continue
            rows.append({
                "date": d, "code": code, "theme": v.get("theme_name"),
                "view": v.get("view"), "boards": v.get("boards"),
                "dir": m.get("dir"), "pos_ratio": m.get("pos_ratio"),
                "streak": m.get("streak"), "n": m.get("n"),
                "d0": ret_d0(code, d), "t1": ret_t1(code, d, days),
            })

    # 🔬 去日期效应：同一天里所有题材共享「大盘涨跌」，不去掉的话
    #    「大题材表现好」可能只是「那几天市场好」。做逐日去均值：
    #    excess = 该笔收益 − 当日动量样本均值。这才是「选对板块」的净贡献。
    by_date: dict[str, list[float]] = {}
    for r in rows:
        if r["d0"] is not None:
            by_date.setdefault(r["date"], []).append(r["d0"])
    day_mean = {d: st.mean(v) for d, v in by_date.items() if v}
    for r in rows:
        r["d0_ex"] = (round(r["d0"] - day_mean[r["date"]], 2)
                      if r["d0"] is not None and r["date"] in day_mean else None)

    out: dict = {"window": [days[0], days[-1]] if days else [], "days": len(days),
                 "n_rows": len(rows), "n_days": len(day_mean), "by_feature": {}}
    for feat in ("dir", "pos_ratio", "streak", "n"):
        groups: dict[str, list[dict]] = {}
        for r in rows:
            b = bucket(feat, r.get(feat))
            if b:
                groups.setdefault(b, []).append(r)
        out["by_feature"][feat] = {
            k: {"n": len(v),
                "d0": agg([x["d0"] for x in v if x["d0"] is not None]),
                "d0_ex": agg([x["d0_ex"] for x in v if x.get("d0_ex") is not None]),
                "t1": agg([x["t1"] for x in v if x["t1"] is not None]),
                "picks": sum(1 for x in v if x.get("view") == "participate")}
            for k, v in groups.items()
        }
    return out


def render(res: dict) -> None:
    if not res["n_rows"]:
        print("⛔ 无动量样本（先跑 scripts/build_theme_momentum.py）")
        return
    print(f"窗口 {res['window'][0]} ~ {res['window'][1]}（{res['days']} 个交易日）"
          f"· 样本 {res['n_rows']} 票次\n")
    labels = {"dir": "方向（家数环比）", "pos_ratio": "位置（÷5日峰值）",
              "streak": "连续天数", "n": "绝对家数"}
    for feat, groups in res["by_feature"].items():
        print(f"── {labels[feat]} ──")
        print(f"{'分档':<16}{'n':>5}{'当日均':>9}{'**超额**':>9}{'当日胜':>8}"
              f"{'T+1均':>9}{'T+1胜':>8}{'买入':>6}")
        print("-" * 76)
        order = DIR_ORDER if feat == "dir" else sorted(groups)
        for k in order:
            g = groups.get(k)
            if not g:
                continue
            f = lambda v, s="%+.2f": (s % v if isinstance(v, (int, float)) else "—")  # noqa: E731
            print(f"{k:<16}{g['n']:>5}{f(g['d0']['avg']):>9}{f(g['d0_ex']['avg']):>9}"
                  f"{f(g['d0']['win'], '%.1f'):>8}{f(g['t1']['avg']):>9}"
                  f"{f(g['t1']['win'], '%.1f'):>8}{g['picks']:>6}")
        # 单调性（只看 n≥15 的档）
        seq = [(k, groups[k]["d0_ex"]["avg"]) for k in order
               if k in groups and groups[k]["n"] >= 15
               and groups[k]["d0_ex"]["avg"] is not None]
        if len(seq) >= 2:
            up = all(a[1] <= b[1] for a, b in zip(seq, seq[1:]))
            print("  超额单调性: " + " → ".join(f"{k} {v:+.2f}%" for k, v in seq)
                  + ("　✅ 递增" if up else "　❌ 不单调"))
        print()

    print("⛔ 每维单独看，只对有信号的维合成；先合成再验证的坑上一轮已经踩过。")
    print("📐 「超额」= 该笔当日收益 − 当日动量样本均值（删掉大盘涨跌这个共同项）。")
    print("   ⛔ 只看「当日均」会被日期效应骗：大题材表现好，可能只是那几天市场好。")
    print("⚠️ 样本窗口受 theme_snapshot 起点（10-16）限制，只能当方向参考，⛔ 不外推。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40)
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
