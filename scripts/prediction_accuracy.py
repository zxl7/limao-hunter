#!/usr/bin/env python3
"""预测功能精度诊断（只读·不写盘·不推送）

回答一个问题：系统的「次日竞价涨幅区间预测」到底准不准、有没有越来越准。

⛔ 为什么不看 `range_hit`（区间命中率）：
   区间命中率 ≈ 区间宽度 / 实际离散度，是**区间宽度的函数**，不是预测能力的函数。
   实测区间宽 1.26pt vs 实际 σ 4.14pt ⇒ 命中率天花板约 30%，16% 属数学必然。
   评估预测能力必须换三把尺子：①方向净增益（扣掉市场基准）②中心 MAE ③横截面相关 r。

用法：
    python3 scripts/prediction_accuracy.py            # 人类可读报表
    python3 scripts/prediction_accuracy.py --json     # 结构化（供卡面/下游消费）
    python3 scripts/prediction_accuracy.py --days 10  # 只看最近 N 个结算日

口径（全部取自 cache/backtest_report_*.json 的 metrics.range_hit.rows）：
    预期中枢 = (expected_range[0]+expected_range[1])/2
    实际      = actual_gap_pct（次日竞价涨幅）
    方向净增益 = 模型同号率 − 「恒预测为正」基准率   ← 关键指标，≈0 即无预测力
    横截面相关 = 当日各票 预期中枢 vs 实际 的皮尔逊 r（逐日算，再取均值）
    池间传导率 = (relay 预期中枢 − watch 预期中枢) / (relay 实际均值 − watch 实际均值)
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_GLOB = str(ROOT / "cache" / "backtest_report_*.json")


def _num(v, p: int = 2) -> str:
    return "—" if v is None else f"{v:.{p}f}"


def _snum(v, p: int = 2) -> str:
    """带正负号的数字（None → —）。用于偏差/净增益/相关系数。"""
    return "—" if v is None else f"{v:+.{p}f}"


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return num / den if den else None


def collect(days: int | None = None) -> list[dict]:
    """逐结算日提取预测精度指标。"""
    out: list[dict] = []
    for f in sorted(glob.glob(REPORT_GLOB)):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        m = d.get("metrics") or {}
        rh = m.get("range_hit") or {}
        ch = m.get("candidate_hit") or {}
        sa = m.get("sentiment_align") or {}
        rows = [
            r for r in (rh.get("rows") or [])
            if isinstance(r.get("actual_gap_pct"), (int, float)) and r.get("expected_range")
        ]
        rec: dict = {
            "settle_date": d.get("settle_date"),
            "rec_date": d.get("rec_date"),
            "evaluable": rh.get("evaluable") or 0,
            "hit_rate": rh.get("hit_rate"),
            "candidate_hit": ch.get("hit_rate") if ch.get("available") else None,
            "candidate_n": ch.get("stock_count"),
            "sentiment_diff": sa.get("diff") if sa.get("available") else None,
        }
        if rows:
            cents = [(r["expected_range"][0] + r["expected_range"][1]) / 2 for r in rows]
            acts = [r["actual_gap_pct"] for r in rows]
            widths = [r["expected_range"][1] - r["expected_range"][0] for r in rows]
            rec.update(
                n=len(rows),
                exp_center=statistics.mean(cents),
                act_mean=statistics.mean(acts),
                bias=statistics.mean(acts) - statistics.mean(cents),
                center_mae=statistics.mean(abs(a - c) for a, c in zip(acts, cents)),
                dir_match=sum(1 for a, c in zip(acts, cents) if (a >= 0) == (c >= 0)) / len(rows) * 100,
                base_rate=sum(1 for a in acts if a >= 0) / len(rows) * 100,
                corr=_pearson(cents, acts),
                width_mean=statistics.mean(widths),
                act_sd=statistics.pstdev(acts) if len(acts) > 1 else None,
                center_sd=statistics.pstdev(cents) if len(cents) > 1 else None,
            )
        out.append(rec)
    return out[-days:] if days else out


def _agg(seg: list[dict], key: str) -> float | None:
    v = [r[key] for r in seg if r.get(key) is not None]
    return statistics.mean(v) if v else None


def _weighted_hit(seg: list[dict]) -> float:
    ev = sum(r["evaluable"] for r in seg)
    if not ev:
        return 0.0
    return sum((r.get("hit_rate") or 0) / 100 * r["evaluable"] for r in seg) / ev * 100


def summarize(seg: list[dict]) -> dict:
    w = _weighted_hit(seg)
    ev = sum(r["evaluable"] for r in seg)
    hits = sum((r.get("hit_rate") or 0) / 100 * r["evaluable"] for r in seg)
    rows = [r for r in seg if r.get("dir_match") is not None]
    return {
        "days": len(seg),
        "evaluable": ev,
        "hit_w": round(w, 1),
        "center_mae": _agg(rows, "center_mae"),
        "bias": _agg(rows, "bias"),
        "dir_match": _agg(rows, "dir_match"),
        "base_rate": _agg(rows, "base_rate"),
        "dir_net": (_agg(rows, "dir_match") or 0) - (_agg(rows, "base_rate") or 0) if rows else None,
        "corr": _agg(rows, "corr"),
        "width": _agg(rows, "width_mean"),
        "act_sd": _agg(rows, "act_sd"),
        "center_sd": _agg(rows, "center_sd"),
    }


def pool_report(seg: list[dict]) -> dict:
    """分池：系统预期是否反映了池间真实差异（传导率）。"""
    import json as _json
    acc: dict[str, list[tuple[float, float]]] = {}
    for f in sorted(glob.glob(REPORT_GLOB)):
        try:
            d = _json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if d.get("settle_date") not in {r["settle_date"] for r in seg}:
            continue
        for r in ((d.get("metrics") or {}).get("range_hit") or {}).get("rows") or []:
            if not isinstance(r.get("actual_gap_pct"), (int, float)) or not r.get("expected_range"):
                continue
            acc.setdefault(r.get("pool") or "?", []).append(
                (r["actual_gap_pct"], (r["expected_range"][0] + r["expected_range"][1]) / 2)
            )
    out = {}
    for k, v in acc.items():
        out[k] = {
            "n": len(v),
            "exp_center": round(statistics.mean(c for _, c in v), 2),
            "act_mean": round(statistics.mean(a for a, _ in v), 2),
            "win_rate": round(sum(1 for a, _ in v if a > 0) / len(v) * 100, 1),
            "act_sd": round(statistics.pstdev([a for a, _ in v]), 2) if len(v) > 1 else None,
            "center_sd": round(statistics.pstdev([c for _, c in v]), 2) if len(v) > 1 else None,
        }
    if "relay" in out and "watch" in out:
        de = out["relay"]["exp_center"] - out["watch"]["exp_center"]
        da = out["relay"]["act_mean"] - out["watch"]["act_mean"]
        out["delta"] = {
            "exp_center_gap": round(de, 2),
            "act_gap": round(da, 2),
            "transfer_rate": round(de / da * 100, 1) if da else None,
        }
    return out


def render(seg: list[dict], pools: dict) -> str:
    L: list[str] = []
    L.append("预测功能精度诊断（次日竞价涨幅区间预测）")
    L.append("=" * 92)
    L.append(
        f"{'结算日':<11}{'评估':>4}{'命中%':>7}{'预期中枢':>9}{'实际均值':>9}{'偏差':>7}"
        f"{'中心MAE':>8}{'同号%':>7}{'基准%':>7}{'净增益':>7}{'相关':>7}{'区间宽':>7}{'实际σ':>7}"
    )
    L.append("-" * 92)
    for r in seg:
        cs = "—" if r.get("corr") is None else f"{r['corr']:+.2f}"
        net = None if r.get("dir_match") is None else r["dir_match"] - r["base_rate"]
        L.append(
            f"{r['settle_date']:<11}{r['evaluable']:>4}{_num(r.get('hit_rate'), 1):>7}"
            f"{_num(r.get('exp_center')):>9}{_num(r.get('act_mean')):>9}{_num(r.get('bias')):>7}"
            f"{_num(r.get('center_mae')):>8}{_num(r.get('dir_match'), 1):>7}{_num(r.get('base_rate'), 1):>7}"
            f"{_num(net, 1):>7}{cs:>7}{_num(r.get('width_mean'), 1):>7}{_num(r.get('act_sd'), 1):>7}"
        )
    L.append("-" * 92)
    for label, s in (("全窗口", summarize(seg)), ("近10日", summarize(seg[-10:])), ("近5日", summarize(seg[-5:]))):
        L.append(
            f"{label:<8} 评估{s['evaluable']:>4}  加权命中{s['hit_w']:>5.1f}%  "
            f"中心MAE{_num(s['center_mae']):>5}  偏差{_snum(s['bias']):>6}  "
            f"同号{_num(s['dir_match'], 1):>5}%  基准{_num(s['base_rate'], 1):>5}%  "
            f"净增益{_snum(s['dir_net'], 1):>6}pp  相关{_snum(s['corr']):>6}  "
            f"区间宽{_num(s['width'], 2):>4}  实际σ{_num(s['act_sd'], 1):>4}  中枢σ{_num(s['center_sd'], 2):>4}"
        )
    if len(seg) >= 4:
        half = len(seg) // 2
        a, b = summarize(seg[:half]), summarize(seg[half:])
        L.append("")
        L.append(f"前后半段对比（{seg[0]['settle_date']}~{seg[half - 1]['settle_date']}  vs  {seg[half]['settle_date']}~{seg[-1]['settle_date']}）")
        for k, lab, p in (
            ("hit_w", "区间命中%", 1), ("center_mae", "中心MAE", 2), ("dir_net", "方向净增益pp", 1),
            ("corr", "横截面相关", 2), ("center_sd", "中枢区分度σ", 2),
        ):
            va, vb = a.get(k), b.get(k)
            if va is None or vb is None:
                continue
            delta = vb - va
            better = "改善" if (k in ("hit_w", "dir_net", "corr", "center_sd") and delta > 0) or (k == "center_mae" and delta < 0) else "恶化/持平"
            L.append(f"  {lab:<14} {va:>+8.{p}f} → {vb:>+8.{p}f}   Δ{delta:>+7.{p}f}   {better}")
    if pools:
        L.append("")
        L.append("分池传导（系统预期是否反映池间真实差异）")
        for k in ("relay", "watch"):
            if k not in pools:
                continue
            p = pools[k]
            L.append(
                f"  {k:<6} n={p['n']:>3}  预期中枢{p['exp_center']:>+6.2f}  实际均值{p['act_mean']:>+6.2f}  "
                f"胜率{p['win_rate']:>5.1f}%  中枢σ{p['center_sd']:>5.2f}  实际σ{p['act_sd']:>5.2f}"
            )
        if "delta" in pools:
            dd = pools["delta"]
            L.append(
                f"  → 池间预期差 {dd['exp_center_gap']:+.2f}pt vs 实际差 {dd['act_gap']:+.2f}pt  "
                f"传导率 {dd['transfer_rate']}%  （<50% = 系统知道池子有好坏，但没写进预期）"
            )
    sd = [r for r in seg if r.get("sentiment_diff") is not None]
    if sd:
        av = [r["sentiment_diff"] for r in sd]
        L.append("")
        L.append(
            f"情绪分预测：可算 {len(sd)}/{len(seg)} 日（{len(sd) / len(seg) * 100:.0f}%）  "
            f"平均绝对偏差 {statistics.mean(abs(x) for x in av):.2f}  均值 {statistics.mean(av):+.2f}  "
            f"|偏差|>1 占比 {sum(1 for x in av if abs(x) > 1) / len(av) * 100:.0f}%"
        )
    cc = [r for r in seg if r.get("candidate_hit") is not None]
    if cc:
        tot = sum(r["candidate_n"] or 0 for r in cc)
        h = sum((r["candidate_hit"] or 0) / 100 * (r["candidate_n"] or 0) for r in cc)
        L.append(
            f"候选命中：口径可算 {len(cc)}/{len(seg)} 日（{len(cc) / len(seg) * 100:.0f}%）  "
            f"加权 {h / tot * 100 if tot else 0:.1f}%（{tot} 只）"
        )
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="预测功能精度诊断（只读）")
    ap.add_argument("--days", type=int, default=None, help="只看最近 N 个结算日")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON")
    args = ap.parse_args()

    seg = collect(args.days)
    if not seg:
        print("[prediction_accuracy] 无回测报告可分析", file=sys.stderr)
        return 0
    pools = pool_report(seg)
    payload = {"window": {"days": len(seg), "from": seg[0]["settle_date"], "to": seg[-1]["settle_date"]},
               "summary": summarize(seg), "pools": pools, "daily": seg}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(seg, pools))
        print()
        print(f"窗口 {seg[0]['settle_date']} ~ {seg[-1]['settle_date']}（{len(seg)} 个结算日）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
