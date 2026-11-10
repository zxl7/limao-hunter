#!/usr/bin/env python3
"""把「这条线在变热还是变冷」预先算成每日快照。

## 为什么不用 `theme_rotation.json`

它是**唯一一份、覆盖写**的文件（只有 `cache/theme_rotation.json`，没有按日归档）。
09:25 实时用它没问题（读到的是昨天 15:51 写的那份），但**回放会读到未来数据** ——
用它做分档验证等于自己骗自己。

⇒ 改用可回放的底座：`cache/zt_history/zt_<date>.json`（每日涨停归档，09-10 起）
＋ `cache/theme_snapshot/theme_<date>.json`（code → 题材映射，10-16 起）。
两者都是按日文件，历史可重建，无未来函数。

⚠️ 代价：`theme_snapshot` 只有 10-16 起 ⇒ 本底座的有效起点是 **10-19**（要 5 日窗口）。
比 `zt_history` 短，样本少，所以结论只能当方向参考。

## 产出

    cache/theme_momentum/<买入日>.json

⚠️ 文件名是**买入日**，内容是**买入日之前**已知的数据：
`09:25` 做 D 日决策时，只知道 D-1 及更早的涨停。
⛔ 把 D 日自己的数据写进去就是未来函数。

每题材字段：

| 字段 | 含义 |
|---|---|
| `n` | D-1 该题材涨停家数 |
| `delta` | 相对 D-2 的变化（家数） |
| `dir` | rising(+2↑) / up(+1) / flat(0) / down(-1) / falling(≤-2) |
| `pos_ratio` | n ÷ 近 5 日峰值（1.0=仍在峰值，0.3=退掉七成） |
| `streak` | 连续有涨停的天数（1=首日，≥5=老线） |
| `series` | 近 5 日家数（旧→新） |

## 用法

    python3 scripts/build_theme_momentum.py                 # 全量重建
    python3 scripts/build_theme_momentum.py --days 30       # 只最近 30 个交易日
    python3 scripts/build_theme_momentum.py --check         # 只打印不落盘
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))

from core.pool_call import line_family              # noqa: E402 —— 与池内同一套归并

ZT_DIR = ROOT / "cache" / "zt_history"
SNAP_DIR = ROOT / "cache" / "theme_snapshot"
OUT_DIR = ROOT / "cache" / "theme_momentum"

WINDOW = 5          # pos_ratio / series 的回看窗口


def _jload(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return None


def zt_days() -> list[str]:
    """有涨停归档的交易日（升序）。"""
    return sorted(p.stem.replace("zt_", "") for p in ZT_DIR.glob("zt_*.json"))


def theme_of(code: str, date: str) -> str:
    """code → 该日的炒作题材名。⛔ 取不到返回空串，不拿行业名凑。"""
    d = _jload(SNAP_DIR / f"theme_{date}.json")
    if not isinstance(d, dict):
        return ""
    item = (d.get("mapping") or {}).get(str(code))
    if not isinstance(item, dict):
        return ""
    return str(item.get("theme") or "").strip()


def daily_counts(days: list[str]) -> dict[str, dict[str, int]]:
    """每个交易日的「题材 → 涨停家数」。缺 snapshot 的那天跳过（⛔ 不猜）。"""
    out: dict[str, dict[str, int]] = {}
    for d in days:
        zt = _jload(ZT_DIR / f"zt_{d}.json") or {}
        # ⚠️ 归档里叫 `zt_list`（不是 `list`）；三个 key 都试，防哪天改名。
        rows = zt.get("zt_list") or zt.get("list") or zt.get("rows") or []
        if not rows:
            continue
        snap_ok = (SNAP_DIR / f"theme_{d}.json").exists()
        if not snap_ok:
            continue
        cnt: dict[str, int] = {}
        for r in rows:
            code = str(r.get("code") or "").strip()
            if len(code) != 6:
                continue
            th = theme_of(code, d)
            if not th or th in ("其他", "未分类", "—", "-", "未知"):
                continue
            # ⚠️ 必须归并：池内 `theme_name` 是**线名**（绿电 = 光伏+电力+铝材），
            #    而快照里是原始板块名（光伏 / 光伏设备）。不归并的话，绿电/军工这类
            #    归并线永远匹配不上（11-10 实测：25 只里 9 只拿不到动量）。
            fam = str(line_family({"core_theme": th, "theme": th}) or th).strip()
            cnt[fam] = cnt.get(fam, 0) + 1
        if cnt:
            out[d] = cnt
    return out


def _dir_of(delta: int) -> str:
    if delta >= 2:
        return "rising"
    if delta == 1:
        return "up"
    if delta == 0:
        return "flat"
    if delta == -1:
        return "down"
    return "falling"


def features_for(counts: dict[str, dict[str, int]], trade_date: str,
                 days: list[str]) -> dict[str, dict]:
    """**买入日** trade_date 的 09:25 可用特征（只用 trade_date 之前的数据）。"""
    try:
        i = days.index(trade_date)
    except ValueError:
        return {}
    hist = [d for d in days[:i] if d in counts]        # ⛔ 严格早于 trade_date
    if not hist:
        return {}

    today = counts.get(hist[-1]) or {}
    prev = counts.get(hist[-2]) if len(hist) >= 2 else {}
    window = [counts.get(d) or {} for d in hist[-WINDOW:]]

    out: dict[str, dict] = {}
    themes = set(today)
    for th in themes:
        n = int(today.get(th, 0))
        n_prev = int((prev or {}).get(th, 0))
        delta = n - n_prev
        series = [int(w.get(th, 0)) for w in window]
        peak = max(series) if series else 0
        # 连续有涨停的天数：从最新往前数
        streak = 0
        for w in reversed(window):
            if int(w.get(th, 0)) > 0:
                streak += 1
            else:
                break
        out[th] = {
            "n": n,
            "delta": delta,
            "dir": _dir_of(delta),
            "pos_ratio": round(n / peak, 3) if peak else None,
            "streak": streak,
            "series": series,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="只算最近 N 个交易日（0=全部）")
    ap.add_argument("--check", action="store_true", help="只打印，不落盘")
    a = ap.parse_args()

    days = zt_days()
    if not days:
        print(f"⛔ 无涨停归档：{ZT_DIR}")
        return 3
    counts = daily_counts(days)
    if not counts:
        print("⛔ 算不出任何一天的题材家数（缺 theme_snapshot 映射？）")
        return 3

    usable = sorted(counts)
    targets = [d for d in days if d > usable[0]]          # 至少能回看一天
    if a.days > 0:
        targets = targets[-a.days:]

    wrote = 0
    for d in targets:
        feat = features_for(counts, d, days)
        if not feat:
            continue
        payload = {
            "trade_date": d,
            "basis_date": next((x for x in reversed(days[:days.index(d)]) if x in counts), ""),
            "window": WINDOW,
            "themes": feat,
            "note": ("供该日 09:25 使用；只用该日之前的数据。"
                     "底座 = zt_history + theme_snapshot（可回放，⛔ 不用 theme_rotation）"),
        }
        if not a.check:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"{d}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        wrote += 1

    print(f"题材家数可算的交易日 {len(counts)} 天（{usable[0]} ~ {usable[-1]}）")
    print(f"{'（dry-run）' if a.check else ''}生成 {wrote} 个买入日快照 → "
          f"{OUT_DIR.relative_to(ROOT) if not a.check else '（未落盘）'}")

    if not a.check and wrote:
        last = targets[-1] if targets else ""
        p = OUT_DIR / f"{last}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            top = sorted(d["themes"].items(), key=lambda kv: -kv[1]["n"])[:6]
            print(f"\n{last} 样例（basis={d['basis_date']}）：")
            for th, f in top:
                print(f"  {th:<14} n={f['n']:<3} Δ{f['delta']:>+3}  {f['dir']:<8}"
                      f"位置{f['pos_ratio']:<6}连{f['streak']}日  {f['series']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
