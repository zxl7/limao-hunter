#!/usr/bin/env python3
"""连板接力「胜率优先」分析（龙头战法交易员 · 2026-11-02 立项）。

与 `relay_review_30d.py` 的分工：
  relay_review_30d  = **描述**全貌（晋级率 / 一日游收益 / 各维度分布）
  relay_winrate     = **决策**导向：只问一句「哪一类票买了能赚（胜率最高）」

⛔ 为什么必须单独一套：晋级率与胜率在「开盘幅度」上完全反向（30 日实测），
   用晋级率排序会把名额全部让给「开盘溢价被吃掉、买到就亏」的票。本脚本以
   **胜率（赚钱的样本占比）** 为主指标，平均收益只作辅助，样本量不足一律降权。

判定标准（写死，防自欺）：
  ① 主排序键 = 胜率 Wilson 95% 置信下界（小样本自动降权，避免 3 中 3 就吹 100%）
  ② 次排序键 = 期望收益（胜率相同看谁赚得多）
  ③ n < MIN_N 的组合只输出不参与「最优规则」评选

用法：
  python3 scripts/relay_winrate.py            # 全量报告
  python3 scripts/relay_winrate.py --json     # 只打 JSON
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEW = ROOT / "cache/relay_review/latest.json"
AI_NAV = ROOT / "wealth_code/cache/ai_nav_history.jsonl"
OUT_DIR = ROOT / "cache/relay_review"

MIN_N = 10          # 组合评选的最小样本量（低于此只展示）
Z = 1.96            # 95% 置信
EXEC_MAX_GAP = 5.0  # 用户 5% 硬线：高开超过此值按纪律不买 → 不计入「可执行」口径


# ── 统计工具 ────────────────────────────────────────────────

def wilson_lower(k: int, n: int, z: float = Z) -> float | None:
    """胜率 Wilson 95% 置信下界（%）。n=0 返回 None。"""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (centre - margin) / denom) * 100, 1)


def summarize(rets: list[float]) -> dict:
    """收益列表 → 胜率类指标（胜=收益>0，平=0 视为不赚）。"""
    n = len(rets)
    if not n:
        return {"n": 0, "win": 0, "win_rate": None, "win_lb": None,
                "avg": None, "median": None, "best": None, "worst": None}
    win = sum(1 for r in rets if r > 0)
    return {
        "n": n, "win": win,
        "win_rate": round(win / n * 100, 1),
        "win_lb": wilson_lower(win, n),
        "avg": round(statistics.mean(rets), 2),
        "median": round(statistics.median(rets), 2),
        "best": round(max(rets), 2), "worst": round(min(rets), 2),
    }


# ── 数据 ───────────────────────────────────────────────────

def load_samples() -> list[dict]:
    d = json.loads(REVIEW.read_text(encoding="utf-8"))
    return d.get("samples") or []


def tradable(samples: list[dict]) -> list[dict]:
    """真正成交得了的样本：有次日、非一字板/停牌、收益已算出。"""
    return [s for s in samples
            if s.get("relay_note") == "ok" and s.get("relay_return") is not None]


def executable(samples: list[dict]) -> list[dict]:
    """**可执行口径**：在 `tradable` 之上再排除「高开 >5%」。

    ⛔ 为什么必须分口径：用户 5% 硬线是执行纪律，高开超 5% 根本不成交。若把它们
       混进统计，「封板质量好」会被算成亏钱（−2.64%）——那是**开盘就冲到涨停、买不进的样本**在
       记账，会得出「封板越差越好」的错误结论。可执行口径才对应真金白银。
    """
    out = []
    for s in samples:
        g = s.get("open_gap")
        if g is None:
            continue
        if float(g) > EXEC_MAX_GAP:
            continue
        out.append(s)
    return out


# ── 晋级因子（与 board_track.py 同口径，从裸字段重建）───────────
# ⚠️ latest.json 的样本只落裸字段（fbt_min/seal_ratio/hs/zbc），
#    不带 factors 字典，故此处按同一阈值重算，⛔ 不要另立一套阈值。
EARLY_SEAL_MIN = 570      # 09:30 前封板
THICK_SEAL_RATIO = 3.0    # 封单/流通 ≥3%
LOW_TURNOVER = 10.0       # 换手 <10%


def factor_count(x: dict) -> int:
    """命中几条晋级因子。⛔ 缺字段**不算达标**（缺失≠0，别白送一条）。"""
    return sum((
        x.get("fbt_min") is not None and int(x["fbt_min"]) <= EARLY_SEAL_MIN,
        x.get("seal_ratio") is not None and float(x["seal_ratio"]) >= THICK_SEAL_RATIO,
        x.get("hs") is not None and float(x["hs"]) < LOW_TURNOVER,
        x.get("zbc") is not None and int(x["zbc"]) == 0,
    ))


# ── 分组 ───────────────────────────────────────────────────

def group(samples: list[dict], sel) -> list[float]:
    return [float(s["relay_return"]) for s in samples if sel(s)]


def ranked(groups: dict[str, list[float]], title: str, min_n: int = MIN_N) -> list[dict]:
    rows = []
    for label, rets in groups.items():
        st = summarize(rets)
        st["label"] = label
        st["eligible"] = st["n"] >= min_n
        rows.append(st)
    rows.sort(key=lambda r: (r["win_lb"] or -1, r["avg"] or -99), reverse=True)
    return rows


# ── 主分析 ─────────────────────────────────────────────────

def analyze() -> dict:
    all_s = load_samples()
    tradable_s = tradable(all_s)
    s = executable(tradable_s)          # 主口径：真金白银能成交的那部分
    base = summarize([float(x["relay_return"]) for x in s])
    unexec = [x for x in tradable_s if x not in s]
    base_unexec = summarize([float(x["relay_return"]) for x in unexec])

    # 【一】单维度：全部候选因子
    def band(x):
        return x.get("gap_band") or "未知"

    def boards_seg(x):
        b = int(x.get("boards") or 0)
        return "2板" if b == 2 else "3板" if b == 3 else "4板" if b == 4 else "5板+"

    def gap_top(x):
        g = x.get("gap_to_top")
        return "最高板" if g == 0 else "差1板" if g == 1 else "差≥2板"

    def fbt_seg(x):
        m = x.get("fbt_min")
        if m is None:
            return "未知"
        return "早封≤09:30" if m <= 570 else "09:30~10:00" if m < 600 else "晚封≥10:00"

    def ratio_seg(x):
        r = x.get("seal_ratio")
        if r is None:
            return "未知"
        return "封厚≥3%" if r >= 3 else "封1~3%" if r >= 1 else "封薄<1%"

    def hs_seg(x):
        h = x.get("hs")
        if h is None:
            return "未知"
        return "换手<10%" if h < 10 else "换手10~20%" if h < 20 else "换手≥20%"

    def zbc_seg(x):
        return "零炸板" if int(x.get("zbc") or 0) == 0 else "炸≥1次"

    def cje_seg(x):
        c = x.get("cje_yi")
        if c is None:
            return "未知"
        return "成交<5亿" if c < 5 else "成交5~10亿" if c < 10 else "成交≥10亿"

    def fcount(x):
        return f"命中{factor_count(x)}条"

    dims = {
        "开盘幅度": (band, ["低开>3%", "低开0~3%", "高开0~3%", "高开3~5%", "高开>5%"]),
        "身位": (gap_top, ["最高板", "差1板", "差≥2板"]),
        "板数": (boards_seg, ["2板", "3板", "4板", "5板+"]),
        "封板时段": (fbt_seg, ["早封≤09:30", "09:30~10:00", "晚封≥10:00"]),
        "封单厚度": (ratio_seg, ["封厚≥3%", "封1~3%", "封薄<1%"]),
        "换手": (hs_seg, ["换手<10%", "换手10~20%", "换手≥20%"]),
        "炸板": (zbc_seg, ["零炸板", "炸≥1次"]),
        "成交额": (cje_seg, ["成交<5亿", "成交5~10亿", "成交≥10亿"]),
        "因子命中数": (fcount, [f"命中{k}条" for k in range(5)]),
    }

    single = {}
    for name, (fn, order) in dims.items():
        g = defaultdict(list)
        for x in s:
            g[fn(x)].append(float(x["relay_return"]))
        single[name] = {lab: summarize(g.get(lab, [])) for lab in order}

    # 【二】开盘幅度 × 身位 全交叉
    cross = {}
    for gb in ["低开>3%", "低开0~3%", "高开0~3%", "高开3~5%", "高开>5%"]:
        for gt in ["最高板", "差1板", "差≥2板"]:
            rets = group(s, lambda x, a=gb, b=gt: band(x) == a and gap_top(x) == b)
            if rets:
                cross[f"{gb} × {gt}"] = summarize(rets)

    # 【三】时间切分验证（前 60% 交易日训练 / 后 40% 验证）
    dates = sorted({x["date"] for x in s})
    cut = dates[int(len(dates) * 0.6)] if dates else None
    train = [x for x in s if cut and x["date"] < cut]
    test = [x for x in s if cut and x["date"] >= cut]

    def rule_stats(pool: list[dict], sel) -> dict:
        return summarize([float(x["relay_return"]) for x in pool if sel(x)])

    rules = {
        "R0 全买(基线)": lambda x: True,
        "R1 只做≥3板": lambda x: int(x.get("boards") or 0) >= 3,
        "R2 只做最高板": lambda x: x.get("gap_to_top") == 0,
        "R3 最高板 ∧ 红开≤5%": lambda x: (x.get("gap_to_top") == 0
                                       and x.get("open_gap") is not None
                                       and 0 < float(x["open_gap"]) <= 5),
        "R4 最高板 ∧ 低开>3%": lambda x: (x.get("gap_to_top") == 0
                                       and x.get("open_gap") is not None
                                       and float(x["open_gap"]) < -3),
        "R5 最低板 ∧ 低开>3%": lambda x: (x.get("gap_to_top") is not None
                                        and x.get("gap_to_top") >= 2
                                        and x.get("open_gap") is not None
                                        and float(x["open_gap"]) < -3),
        "R6 ≥3板 ∧ 低开>3%": lambda x: (int(x.get("boards") or 0) >= 3
                                      and x.get("open_gap") is not None
                                      and float(x["open_gap"]) < -3),
        "R7 ≥3板 ∧ 红开≤5%": lambda x: (int(x.get("boards") or 0) >= 3
                                      and x.get("open_gap") is not None
                                      and 0 < float(x["open_gap"]) <= 5),
        "R8 红开≤5% ∧ 换手<20%": lambda x: (x.get("open_gap") is not None
                                         and 0 < float(x["open_gap"]) <= 5
                                         and (x.get("hs") or 999) < 20),
        "R9 最高板 ∧ 红开≤5% ∧ 换手<20%": lambda x: (x.get("gap_to_top") == 0
                                                 and x.get("open_gap") is not None
                                                 and 0 < float(x["open_gap"]) <= 5
                                                 and (x.get("hs") or 999) < 20),
    }
    rule_table = {k: {"all": rule_stats(s, f), "train": rule_stats(train, f),
                      "test": rule_stats(test, f)} for k, f in rules.items()}

    # 【四】日级模拟：每天按规则挑票（同票同日只算一次），quota 只数上限
    def simulate(sel, order_key, quota: int = 2, pool: list[dict] | None = None) -> dict:
        src = s if pool is None else pool
        by_day = defaultdict(list)
        for x in src:
            if sel(x):
                by_day[x["date"]].append(x)
        day_rets, picks = [], 0
        for d, items in by_day.items():
            items.sort(key=order_key)
            chosen = items[:quota]
            if not chosen:
                continue
            picks += len(chosen)
            day_rets.append(sum(float(c["relay_return"]) for c in chosen) / len(chosen))
        st = summarize(day_rets)
        st["days"] = len(day_rets)
        st["picks"] = picks
        st["avg_picks_per_day"] = round(picks / len(day_rets), 2) if day_rets else 0
        return st

    def by_boards_desc(x):
        return (-int(x.get("boards") or 0), -float(x.get("open_gap") or 0))

    def by_top_first(x):
        """身位优先：全市场最高板排最前，再按板数、再按低开幅度（越低越前）。"""
        return (0 if x.get("gap_to_top") == 0 else 1,
                -int(x.get("boards") or 0),
                float(x.get("open_gap") or 0))

    sim = {
        "全池·板数优先(现状近似)": simulate(lambda x: True, by_boards_desc),
        "全池·身位优先(拟改)": simulate(lambda x: True, by_top_first),
        "只做≥3板·板数优先": simulate(lambda x: int(x.get("boards") or 0) >= 3, by_boards_desc),
        "只做最高板·板数优先": simulate(lambda x: x.get("gap_to_top") == 0, by_boards_desc),
        "最高板∧红开≤5%": simulate(lambda x: (x.get("gap_to_top") == 0
                                          and x.get("open_gap") is not None
                                          and 0 < float(x["open_gap"]) <= 5), by_boards_desc),
    }
    # 排序改动的**时间切分验证**：训练段调参不算，只看验证段是否同向
    sim_split = {
        "板数优先 训练段": simulate(lambda x: True, by_boards_desc, pool=train),
        "板数优先 验证段": simulate(lambda x: True, by_boards_desc, pool=test),
        "身位优先 训练段": simulate(lambda x: True, by_top_first, pool=train),
        "身位优先 验证段": simulate(lambda x: True, by_top_first, pool=test),
    }

    # 【五】AI 真实成交（按板位/开盘幅度）
    ai = []
    if AI_NAV.exists():
        for line in AI_NAV.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            for st in r.get("stocks") or []:
                if st.get("return_pct") is None:
                    continue
                ai.append({"trade_date": r.get("trade_date"), "name": st.get("name"),
                           "code": str(st.get("code")).zfill(6),
                           "ret": float(st["return_pct"])})

    return {
        "window": {"days": len(dates), "start": dates[0] if dates else None,
                   "end": dates[-1] if dates else None},
        "universe": {"tradable": len(tradable_s), "executable": len(s),
                     "excluded_high_open": len(unexec)},
        "base": base,
        "base_excluded": base_unexec,
        "single": single,
        "cross": cross,
        "split": {"cut": cut, "train_n": len(train), "test_n": len(test)},
        "rules": rule_table,
        "sim": sim,
        "sim_split": sim_split,
        "ai": {"n": len(ai), "stat": summarize([x["ret"] for x in ai])},
    }


# ── 打印 ───────────────────────────────────────────────────

def _fmt_row(label: str, st: dict, mark: bool = True) -> str:
    if not st.get("n"):
        return f"  {label:<30} 无样本"
    flag = "" if st.get("n", 0) >= MIN_N else " ⚠小样本"
    return (f"  {label:<30} n={st['n']:<4} 胜率{st['win_rate']:>5}% "
            f"下界{st['win_lb']:>5}% 均{st['avg']:>7}% 中位{st['median']:>7}%{flag}")


def render(a: dict) -> None:
    w = a["window"]
    b = a["base"]
    print(f"\n{'='*96}")
    print(f"连板接力 · 胜率优先分析   窗口 {w['start']} ~ {w['end']}（{w['days']} 个交易日）")
    print(f"{'='*96}")

    print(f"\n【基线】口径说明")
    u = a["universe"]
    print(f"  全部可成交 {u['tradable']} 个样本；剔除「高开>{EXEC_MAX_GAP:g}% 按纪律不买」{u['excluded_high_open']} 个"
          f" → **可执行口径 {u['executable']} 个**（以下全部按此口径）")
    print(f"  可执行口径基线：n={b['n']}  胜率 {b['win_rate']}%（下界 {b['win_lb']}%）"
          f"  平均 {b['avg']}%  中位 {b['median']}%")
    e = a["base_excluded"]
    if e.get("n"):
        print(f"  被剔除那批（高开>EXEC_MAX_GAP，按旧纪律不买）：n={e['n']} 胜率 {e['win_rate']}% 平均 {e['avg']}%"
              f"  ← 拿它算账会得出「封板越好越亏」，属口径错误")

    print(f"\n【一】单维度胜率排序（主指标 = 胜率置信下界）")
    for dim, table in a["single"].items():
        rows = [{"label": k, **v} for k, v in table.items() if v.get("n")]
        if not rows:
            continue
        rows.sort(key=lambda r: (r["win_lb"] or -1), reverse=True)
        print(f"  ── {dim}")
        for r in rows:
            print(_fmt_row(r["label"], r))

    print(f"\n【二】开盘幅度 × 身位（09:25 唯一可观测变量的全交叉）")
    rows = [{"label": k, **v} for k, v in a["cross"].items()]
    rows.sort(key=lambda r: (r["win_lb"] or -1), reverse=True)
    for r in rows:
        print(_fmt_row(r["label"], r))

    sp = a["split"]
    print(f"\n【三】规则对比（训练 <{sp['cut']} n={sp['train_n']} / 验证 ≥{sp['cut']} n={sp['test_n']}）")
    print(f"  {'规则':<28}{'全样本':<44}{'验证集'}")
    for name, v in a["rules"].items():
        al, te = v["all"], v["test"]
        if not al.get("n"):
            print(f"  {name:<28}无样本")
            continue
        av = (f"n={al['n']:<4}胜{al['win_rate']:>5}% 下界{al['win_lb']:>5}% 均{al['avg']:>7}%")
        tv = (f"n={te['n']:<3}胜{te['win_rate']:>5}% 均{te['avg']:>7}%"
              if te.get("n") else "无样本")
        print(f"  {name:<28}{av:<44}{tv}")

    print(f"\n【四】日级模拟（同期多只符合时取前 2，等权平均）")
    for name, st in a["sim"].items():
        if st.get("days"):
            print(f"  {name:<24} 出手{st['days']:>2}天/{st['picks']:>2}笔 "
                  f"日胜率{st['win_rate']:>5}% 日均{st['avg']:>7}% 中位{st['median']:>7}%")
        else:
            print(f"  {name:<24} 无样本")

    print(f"\n【四·B】排序改动的时间切分验证（训练段涨了不算数，要看验证段同不同向）")
    for name, st in a["sim_split"].items():
        if st.get("days"):
            print(f"  {name:<20} 出手{st['days']:>2}天/{st['picks']:>2}笔 "
                  f"日胜率{st['win_rate']:>5}% 日均{st['avg']:>7}% 中位{st['median']:>7}%")
        else:
            print(f"  {name:<20} 无样本")

    ai = a["ai"]
    print(f"\n【五】AI 真实成交（独立账本，仅供参考）")
    print(_fmt_row("全部", ai["stat"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    a = analyze()
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=1))
    else:
        render(a)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "winrate.json").write_text(
        json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
