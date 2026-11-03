#!/usr/bin/env python3
"""盘前诊断 CLI —— 看「明早这些在册连板票大概率怎么开」（2026-11-02 立，同日改为薄封装）

⛔ 这不是推送链路（2026-11-02 用户「不要新开链路，优化原有选股+竞价逻辑」）：
   真正上线的地方在两张既有卡里——
     · 选股卡 15:30：`pipeline_eod` 给每只候选挂 `forecast`，`build_cards` 逐票渲染
     · 竞价卡 09:25：`pool_call` 用实测开盘查「这档历史胜率」，写进买入理由
   本文件只是**手动诊断**：盘前想逐只看明细、或跑 `--backtest` 检验准确性时用。
   模型实现只有一份 = `core/relay_forecast.py`，此处⛔ 不重复实现。

用法：
  python3 scripts/morning_forecast.py                    # 看最新台账的次日推测
  python3 scripts/morning_forecast.py --date 2026-11-03  # 指定目标日
  python3 scripts/morning_forecast.py --json             # 只打 JSON
  python3 scripts/morning_forecast.py --backtest 15      # 滚动回测（只用 D 之前的样本）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WC = ROOT / "wealth_code"
for _p in (str(WC), str(ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common.trading_calendar import next_a_share_trading_day  # noqa: E402
from core import relay_forecast as rf                          # noqa: E402

TRACK_DIR = ROOT / "cache/board_track"
FORECAST_DIR = ROOT / "cache/forecast"
ORDER = {"可等": 0, "只观察": 1, "放弃": 2}


def _jload(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return None


def latest_track() -> tuple[str | None, dict]:
    files = sorted(TRACK_DIR.glob("track_*.json"))
    if not files:
        return None, {}
    return files[-1].stem.replace("track_", ""), (_jload(files[-1]) or {})


def load_validation() -> dict:
    """读最近一次滚动回测，给每档建议挂「验证状态」。⛔ 没验证的档必须当场标出来。"""
    d = _jload(FORECAST_DIR / "backtest_latest.json") or {}
    s = d.get("summary") or {}
    if not s.get("n"):
        return {"available": False}
    status = {}
    for k, v in (s.get("by_verdict") or {}).items():
        n = (v or {}).get("bought_n") or 0
        status[k] = ("已验证" if n >= 15 else
                     f"弱验证({n}笔)" if n >= 5 else f"未验证({n}笔)")
    return {"available": True, "days": d.get("days"), "n": d.get("n"),
            "gap_mae": s.get("gap_mae"), "in_band_pct": s.get("in_band_pct"),
            "over5_call_n": s.get("over5_call_n"), "over5_call_hit": s.get("over5_call_hit"),
            "easy_call_n": s.get("easy_call_n"), "easy_call_hit": s.get("easy_call_hit"),
            "by_verdict": s.get("by_verdict") or {}, "status": status}


def decide_tree() -> list[str]:
    """09:25 实测 gap 之后的决策树（与现行纪律 + 开关默认值逐条对齐）。"""
    try:
        from core import pool_call as _pc
        low_open_on = bool(_pc.POOL_CALL_LOW_OPEN_TOP_EXCEPTION)
        leader_on = bool(_pc.POOL_CALL_LEADER_ONLY)
        cliff_on = bool(getattr(_pc, "POOL_CALL_FORECAST_GATE", False))
    except Exception:                                        # noqa: BLE001
        low_open_on, leader_on, cliff_on = False, True, False
    return [
        f"身位闸 {'已开' if leader_on else '已关'}：身位差 ≥2 板的后排今天不参与"
        "（这一档真买到时胜率仅 39.7%、平均 −1.37%）",
        f"崖位闸 {'已开' if cliff_on else '已关'}：≥6 板且该档晋级率 <25% 只观察",
        "开在 >+5%      → 按纪律不追（高开>5% 当日平均 −2.64%，多数根本买不到）",
        "开在 0~+5%     → 只有「可等」档才下手；「只观察/放弃」档一律不买",
        "开在 0~+1%     → 特别警惕（该档胜率仅 27.8%，比 3~5% 更差＝没资金愿意接力）",
        "开在 −3%~0     → 不买（现行纪律不接低开）",
        ("开在 <−3%      → 最高板例外**当前已开**（历史 n=7 胜 71.4%，半仓）"
         if low_open_on else
         "开在 <−3%      → 不买；最高板低开例外**当前是关闭的**（实盘仅触发 1 次且亏，只留痕）"),
    ]


def analyze(target_date: str | None = None, *, cutoff: str | None = None,
            track_path: Path | None = None) -> dict:
    if track_path is not None:
        base_date = track_path.stem.replace("track_", "")
        track = _jload(track_path) or {}
    else:
        base_date, track = latest_track()
    if not track:
        raise SystemExit("⛔ 缺 cache/board_track/track_*.json，先跑 scripts/board_track.py build")

    model = rf.build_model(cutoff=cutoff)
    market = track.get("market") or {}
    market_max = int(market.get("max_boards") or 0)
    rows = [r for r in (track.get("tracked") or []) if isinstance(r, dict)]
    rf.annotate(rows, market_max, model=model, cutoff=cutoff)
    for r in rows:
        f = r.get("forecast") or {}
        r.update({k: f.get(k) for k in
                  ("verdict", "group", "gap_median", "gap_p25", "gap_p75",
                   "p_over5", "bands", "promo_rate", "promo_board",
                   "buy_n", "buy_win", "buy_win_lb", "buy_avg", "by_band", "reasons")})
    rows.sort(key=lambda x: (ORDER.get(str(x.get("verdict")), 9), -int(x.get("boards") or 0)))

    counts: dict[str, int] = {}
    for r in rows:
        counts[str(r.get("verdict"))] = counts.get(str(r.get("verdict")), 0) + 1

    if not target_date and base_date:
        nxt = next_a_share_trading_day(str(base_date)[:10])
        target_date = (nxt if isinstance(nxt, str) else nxt.strftime("%Y-%m-%d")) if nxt else None

    return {
        "schema": "morning_forecast_v2",
        "source": "core.relay_forecast（与选股卡/竞价卡同一份模型）",
        "base_date": base_date,
        "date": base_date,
        "target_date": target_date,
        "market": {"max_boards": market_max, "zt_count": market.get("zt_count"),
                   "tracked": len(rows), "broken": market.get("broken_count")},
        "theme_source": track.get("theme_source"),
        "verdict_counts": counts,
        "rows": rows,
        "decide_tree": decide_tree(),
        "validation": load_validation(),
        "model_meta": model["meta"],
        "model_by_boards": model["by_boards"],
        "disclaimer": "推测基于 32 个交易日的真实归档样本，分组后样本普遍 <30，"
                      "只作方向参考，不构成买入指令。",
    }


def render(a: dict) -> None:
    m = a["market"]
    print(f"\n{'='*96}")
    print(f"次日初步推测（诊断用·非推送）   {a['base_date']} 收盘 → {a['target_date'] or '下一交易日'}")
    print(f"全市场涨停 {m['zt_count']} 只 · 最高 {m['max_boards']} 板 · "
          f"在册 {m['tracked']} 只 · 断板 {m['broken']} 只")
    if a.get("theme_source") == "sector_fallback":
        print("⚠️ 题材快照缺失，本次按申万行业降级归类")
    win = a["model_meta"].get("window") or {}
    print(f"模型样本 {a['model_meta'].get('sample_n')} 个「票·日」"
          f"（{win.get('start')} ~ {win.get('end')}）")
    print(f"{'='*96}")

    print("\n结论分布：" + " / ".join(
        f"{k} {v} 只" for k, v in sorted(a["verdict_counts"].items(),
                                        key=lambda kv: ORDER.get(kv[0], 9))))

    print(f"\n{'票':<10}{'板':>3}{'身位':>5}{'因子':>4}{'组别':<18}"
          f"{'预测开盘(中位)':>14}{'买不到':>7}{'晋级':>7}{'真买到胜率':>18}{'建议':>7}")
    print("-" * 100)
    for r in a["rows"]:
        f = r.get("forecast") or {}
        buy = (f"{f['buy_win']}%/{f['buy_avg']:+.2f}%({f['buy_n']})"
               if f.get("buy_win") is not None else "—")
        med = f.get("gap_median")
        p5 = f.get("p_over5")
        promo = f.get("promo_rate")
        print(f"{(r.get('name') or '')[:8]:<10}{int(r.get('boards') or 0):>3}"
              f"{f.get('gap_to_top'):>5}{f.get('factor_count'):>4}"
              f"  {str(f.get('group')):<18}"
              f"{(f'{med:+.1f}%' if med is not None else '—'):>14}"
              f"{(f'{int(p5)}%' if p5 is not None else '—'):>7}"
              f"{(f'{promo}%' if promo is not None else '—'):>7}"
              f"{buy:>18}{str(f.get('verdict')):>7}")

    print("\n【逐票理由】")
    for r in a["rows"]:
        f = r.get("forecast") or {}
        if f.get("verdict") == "放弃" and (f.get("gap_to_top") or 0) >= 3:
            continue                                  # 深度后排不逐条展开，避免刷屏
        print(f"  {r.get('name')}({r.get('code')}) {r.get('boards')}板 · {f.get('group')}")
        for x in f.get("reasons") or []:
            print(f"    · {x}")
        b = f.get("bands") or {}
        if b:
            print(f"    · 开盘分布：低开>3% {b.get('低开>3%', 0)}% / 低开0~3% {b.get('低开0~3%', 0)}%"
                  f" / 0~3% {b.get('高开0~3%', 0)}% / 3~5% {b.get('高开3~5%', 0)}%"
                  f" / >5% {b.get('高开>5%', 0)}%")

    print("\n【09:25 决策树】")
    for line in a["decide_tree"]:
        print(f"  {line}")

    v = a.get("validation") or {}
    if v.get("available"):
        print(f"\n【模型验证状态】滚动回测 {v['days']} 个交易日 / {v['n']} 个「票·日」")
        print(f"  开盘幅度：实际落在预测区间 {v['in_band_pct']}%（理论 50%）· MAE {v['gap_mae']}pp")
        for k, st in (v.get("status") or {}).items():
            b = (v.get("by_verdict") or {}).get(k) or {}
            print(f"    {k:<6}{st:<12} 红开可买 {b.get('bought_n')} 笔 · "
                  f"胜率 {b.get('bought_win')}% · 均 {b.get('bought_avg')}%")
        print("  ⛔ 「未验证」= 该档可成交样本太少，不能当承诺；下单仍按 09:25 实测 + 现行纪律。")
    else:
        print("\n【模型验证状态】尚未回测（跑 python3 scripts/morning_forecast.py --backtest）")

    print(f"\n  {a['disclaimer']}")


def backtest(days: int = 15) -> dict:
    """滚动回测：预测第 D 日时**只用 D 之前的样本**建模型，再与次日真实结果对账。

    ⛔ 唯一诚实的准确性口径。用全样本模型去"预测"历史＝看着答案考试。
    """
    from weekly_accuracy import evaluate, summarize        # noqa: E402

    files = sorted(TRACK_DIR.glob("track_*.json"))
    if len(files) < 4:
        raise SystemExit("⛔ 台账不足 4 天，无法滚动回测")
    all_rows: list[dict] = []
    per_day: list[dict] = []
    for f in files[-(days + 1):-1]:
        base = f.stem.replace("track_", "")
        try:
            a = analyze(None, cutoff=base, track_path=f)
        except SystemExit:
            continue
        if not a["target_date"]:
            continue
        rows = evaluate(a)
        if not rows:
            continue
        all_rows.extend(rows)
        per_day.append({"base": base, "target": a["target_date"],
                        "n": len(rows), "summary": summarize(rows)})
    return {"schema": "forecast_backtest_v1",
            "source": "board_track 台账 + 滚动重算（预测 D 日只用 D 之前的样本）",
            "days": len(per_day), "n": len(all_rows),
            "summary": summarize(all_rows), "per_day": per_day, "rows": all_rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="目标交易日（默认=台账日的下一交易日）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--backtest", type=int, nargs="?", const=15, default=0, metavar="DAYS")
    args = ap.parse_args()

    FORECAST_DIR.mkdir(parents=True, exist_ok=True)
    if args.backtest:
        from weekly_accuracy import render as render_acc     # noqa: E402
        bt = backtest(args.backtest)
        render_acc(bt)
        (FORECAST_DIR / "backtest_latest.json").write_text(
            json.dumps(bt, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n📄 已落盘 cache/forecast/backtest_latest.json")
        return 0

    a = analyze(args.date)
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=1))
    else:
        render(a)
    name = a["target_date"] or "next"
    (FORECAST_DIR / f"forecast_{name}.json").write_text(
        json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n📄 已落盘 cache/forecast/forecast_{name}.json（诊断留档，⛔ 不推送）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
