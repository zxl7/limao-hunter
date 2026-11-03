#!/usr/bin/env python3
"""每周「数据准确性归纳」—— 上周的推测到底准不准（2026-11-02 立，同日改为对账选股卡产物）。

⛔ 定位（2026-11-02 用户「不要新开链路」）：**不产任何新文件供推送**，只对账
   `wealth_code/cache/candidates-<D>.json` —— 那正是 15:30 选股卡当天真正推出去的东西，
   每只候选里的 `forecast` 字段就是「我们当时说它明早会怎么开」。
   拿它跟次日真实开盘对账，得到的是**真正上线过的那套判断**的准确率，
   而不是另跑一份模型的自我评价。

回答三件事，全部用**已实现的真实结果**对账，不许事后改口：
  ① 开盘幅度：分组预测的 P25/中位/P75 有没有盖住实际？预测「大概率买不到」的票，
     实际真的高开 >5% 了吗？（模型校准）
  ② 建议档：判「放弃」的票，实际买了是不是真的亏？判「可等」的有没有赚？
     ⛔ 这是唯一能证伪模型的指标——不许只看「晋级率对了」就说模型准。
  ③ 晋级：预测晋级率 vs 实际晋级比例（分板数档）。

用法：
  python3 scripts/weekly_accuracy.py                # 归纳最近 5 个交易日
  python3 scripts/weekly_accuracy.py --days 10
  python3 scripts/weekly_accuracy.py --json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WC = ROOT / "wealth_code"
for _p in (str(WC), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common.trading_calendar import next_a_share_trading_day  # noqa: E402

CAND_DIR = WC / "cache"
ZT_DIR = ROOT / "cache/zt_history"
KLINE_DIR = ROOT / "cache/kline_qfq"
OUT_DIR = ROOT / "cache/forecast"


def _jload(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


_KL: dict[str, dict[str, dict]] = {}


def bar(code: str, date: str) -> dict | None:
    if code not in _KL:
        m: dict[str, dict] = {}
        p = KLINE_DIR / f"{code}.json"
        if p.exists():
            for b in (_jload(p) or {}).get("bars") or []:
                m[str(b["date"])[:10]] = b
        _KL[code] = m
    return _KL[code].get(date)


def zt_map(date: str) -> dict[str, int]:
    d = _jload(ZT_DIR / f"zt_{date}.json") or {}
    out = {}
    for r in d.get("zt_list") or []:
        try:
            out[str(r.get("code")).zfill(6)] = int(r.get("boards") or 0)
        except Exception:
            continue
    return out


def _norm(r: dict) -> dict:
    """兼容两种来源：候选行（`forecast` 子对象）与扁平行（回测直接传 rows）。"""
    f = r.get("forecast") if isinstance(r.get("forecast"), dict) else None
    if f and not f.get("error"):
        return {"verdict": f.get("verdict"), "group": f.get("group"),
                "gap_median": f.get("gap_median"), "gap_p25": f.get("gap_p25"),
                "gap_p75": f.get("gap_p75"), "p_over5": f.get("p_over5"),
                "promo_rate": f.get("promo_rate")}
    return {k: r.get(k) for k in
            ("verdict", "group", "gap_median", "gap_p25", "gap_p75", "p_over5", "promo_rate")}


def evaluate(payload: dict) -> list[dict]:
    """把一份「当时推出去的预测」与次日实际逐票对账。

    `payload`：选股卡产物（`candidates-<D>.json`）或回测拼的同形 dict，
    必须有 `date`（或 `base_date`）+ `rows`（relay+watch，每行带 forecast）。
    """
    base = str(payload.get("date") or payload.get("base_date") or "")[:10]
    tgt = str(payload.get("target_date") or "")[:10]
    if not tgt and base:
        nxt = next_a_share_trading_day(base)
        tgt = (nxt if isinstance(nxt, str) else nxt.strftime("%Y-%m-%d")) if nxt else ""
    if not base or not tgt:
        return []
    zt_tgt = zt_map(tgt)
    rows = []
    for r in payload.get("rows") or []:
        p = _norm(r)
        if p.get("gap_median") is None:
            continue
        code = str(r.get("code") or "").zfill(6)
        b0, b1 = bar(code, base), bar(code, tgt)
        if not b0 or not b1:
            continue
        prev_close = float(b0["close"])
        if prev_close <= 0:
            continue
        gap = round((float(b1["open"]) - prev_close) / prev_close * 100, 2)
        one_word = float(b1["high"]) == float(b1["low"])
        ret = None if one_word else round((float(b1["close"]) - float(b1["open"]))
                                          / float(b1["open"]) * 100, 2)
        next_boards = zt_tgt.get(code, 0)
        promoted = next_boards > int(r.get("boards") or 0)

        rows.append({
            "date": tgt,
            "base_date": base,
            "code": code,
            "name": r.get("name"),
            "boards": r.get("boards"),
            "verdict": p.get("verdict"),
            "group": p.get("group"),
            "pred_median": p.get("gap_median"),
            "pred_p25": p.get("gap_p25"),
            "pred_p75": p.get("gap_p75"),
            "pred_p_over5": p.get("p_over5"),
            "pred_promo": p.get("promo_rate"),
            "actual_gap": gap,
            "actual_ret": ret,
            "one_word": one_word,
            "actual_promoted": promoted,
            "next_boards": next_boards,
            "in_band": (p.get("gap_p25") is not None and p.get("gap_p75") is not None
                        and float(p["gap_p25"]) <= gap <= float(p["gap_p75"])),
            "over5_hit": gap > 5,
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}

    gaps = [r["actual_gap"] for r in rows]
    errs = [abs(r["actual_gap"] - float(r["pred_median"]))
            for r in rows if r["pred_median"] is not None]

    # 校准：预测「买不到概率」高（≥50%）的那批，实际高开>5% 的比例
    over_calls = [r for r in rows if (r["pred_p_over5"] or 0) >= 50]
    over_ok = sum(1 for r in over_calls if r["over5_hit"])
    # 反向：预测「买不到概率」低（<30%）
    easy_calls = [r for r in rows if (r["pred_p_over5"] or 0) < 30]
    easy_ok = sum(1 for r in easy_calls if not r["over5_hit"])

    def bucket(verdict: str) -> dict:
        g = [r for r in rows if r["verdict"] == verdict]
        if not g:
            return {"n": 0}
        tradable = [r for r in g if r["actual_ret"] is not None
                    and 0 < r["actual_gap"] <= 5]
        rets = [r["actual_ret"] for r in tradable]
        return {
            "n": len(g),
            "bought_n": len(rets),
            "bought_win": (round(sum(1 for v in rets if v > 0) / len(rets) * 100, 1)
                           if rets else None),
            "bought_avg": round(statistics.mean(rets), 2) if rets else None,
            "promo": round(sum(1 for r in g if r["actual_promoted"]) / len(g) * 100, 1),
            "over5": round(sum(1 for r in g if r["over5_hit"]) / len(g) * 100, 1),
        }

    promo_rows = [r for r in rows if r["pred_promo"] is not None]
    return {
        "n": len(rows),
        "gap_mae": round(statistics.mean(errs), 2) if errs else None,
        "in_band_pct": round(sum(1 for r in rows if r["in_band"]) / len(rows) * 100, 1),
        "actual_gap_median": round(statistics.median(gaps), 2),
        "over5_call_n": len(over_calls),
        "over5_call_hit": round(over_ok / len(over_calls) * 100, 1) if over_calls else None,
        "easy_call_n": len(easy_calls),
        "easy_call_hit": round(easy_ok / len(easy_calls) * 100, 1) if easy_calls else None,
        "promo_pred_avg": (round(statistics.mean([float(r["pred_promo"]) for r in promo_rows]), 1)
                           if promo_rows else None),
        "promo_actual": (round(sum(1 for r in rows if r["actual_promoted"]) / len(rows) * 100, 1)
                         if rows else None),
        "by_verdict": {v: bucket(v) for v in ("可等", "只观察", "放弃")},
    }


def analyze(days: int = 5) -> dict:
    """对账最近 N 个交易日的**选股卡产物**（candidates-<D>.json 里挂的 forecast）。"""
    files = sorted(CAND_DIR.glob("candidates-2026-*.json"))
    picked = files[-days:]
    all_rows: list[dict] = []
    per_day = []
    skipped: list[str] = []
    for f in picked:
        d = _jload(f) or {}
        rows_in = (d.get("relay") or []) + (d.get("watch") or [])
        if not rows_in:
            continue
        # 2026-11-02 之前的候选文件没有 forecast 字段 → 无可对账（⛔ 不用估算值填充）
        if not any((r.get("forecast") or {}).get("gap_median") is not None for r in rows_in):
            skipped.append(f.name)
            continue
        payload = {"date": d.get("date"), "rows": rows_in}
        rows = evaluate(payload)
        if not rows:
            continue
        all_rows.extend(rows)
        per_day.append({"file": f.name, "base": str(d.get("date"))[:10],
                        "target": rows[0]["date"], "n": len(rows),
                        "summary": summarize(rows)})
    return {
        "schema": "weekly_accuracy_v2",
        "source": "财富密码选股卡产物 candidates-<D>.json（含 relay/watch 的 forecast 字段）",
        "days": len(per_day),
        "files": [p["file"] for p in per_day],
        "skipped_no_forecast": skipped,
        "n": len(all_rows),
        "summary": summarize(all_rows),
        "per_day": per_day,
        "rows": all_rows,
    }


def render(a: dict) -> None:
    print(f"\n{'='*88}")
    print(f"数据准确性归纳   覆盖 {a['days']} 个交易日 / {a['n']} 个「票·日」")
    print(f"  对账源：{a.get('source', '选股卡产物')}")
    print(f"{'='*88}")
    if not a["n"]:
        print("  暂无可对账的预测（选股卡产物里还没有已到期的 forecast 字段）")
        if a.get("skipped_no_forecast"):
            print(f"  ⛔ 跳过 {len(a['skipped_no_forecast'])} 份旧候选（2026-11-02 之前没有预测字段）")
        return
    s = a["summary"]
    print(f"\n【一】开盘幅度")
    print(f"  与实际中位偏差 MAE {s['gap_mae']}pp · 实际落在预测 P25~P75 区间 {s['in_band_pct']}%")
    print(f"  实际开盘中位 {s['actual_gap_median']}%")
    print(f"\n【二】「买不到」的预测准不准")
    if s["over5_call_n"]:
        print(f"  预测买不到概率≥50% 的 {s['over5_call_n']} 只 → 实际真高开>5% {s['over5_call_hit']}%")
    if s["easy_call_n"]:
        print(f"  预测买不到概率<30% 的 {s['easy_call_n']} 只 → 实际真能买 {s['easy_call_hit']}%")
    print(f"\n【三】三档建议的实际结果（唯一能证伪模型的指标）")
    print(f"  {'建议':<8}{'只数':>5}{'红开可买':>8}{'胜率':>7}{'平均':>8}{'实际晋级':>9}{'实际>5%':>9}")
    for k, v in s["by_verdict"].items():
        if not v.get("n"):
            print(f"  {k:<8}{0:>5}")
            continue
        print(f"  {k:<8}{v['n']:>5}{v['bought_n']:>8}"
              f"{(str(v['bought_win'])+'%' if v['bought_win'] is not None else '—'):>7}"
              f"{(str(v['bought_avg'])+'%' if v['bought_avg'] is not None else '—'):>8}"
              f"{str(v['promo'])+'%':>9}{str(v['over5'])+'%':>9}")
    print(f"\n【四】晋级率校准")
    print(f"  分组预测均值 {s['promo_pred_avg']}% · 实际 {s['promo_actual']}%")
    if a["per_day"]:
        print(f"\n【逐日】")
        for p in a["per_day"]:
            ps = p["summary"]
            print(f"  {p['target']}  n={p['n']:<3} MAE {ps['gap_mae']}pp  "
                  f"区间内 {ps['in_band_pct']}%  晋级预测 {ps['promo_pred_avg']}%→实际 {ps['promo_actual']}%")
    print("\n  注：样本少时准确率波动大；只看方向，不因单周偏差改模型。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    a = analyze(args.days)
    if args.json:
        print(json.dumps({k: v for k, v in a.items() if k != "rows"},
                         ensure_ascii=False, indent=1))
    else:
        render(a)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "accuracy_latest.json").write_text(
        json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n📄 已落盘 cache/forecast/accuracy_latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
