#!/usr/bin/env python3
"""T+1 可执行口径验收：把「开→收」代理换成真正的 A 股往返。

## 为什么必须有这个脚本（2026-11-06 21:xx 用户「这个收益是不是有点牛逼了」）

`pool_call_replay.day_return` 用的是 **当日开盘买 → 当日收盘卖**。这在 A 股**不可执行**：
T+1 制度下，今天买入的股票今天不能卖。该口径只是**收益代理**（`config/shared_rules.md`
与「龙头战法交易员」skill 都明确写了「开盘至同日收盘仅为评估代理，不是满足 T+1 的
可执行往返收益」）。

本脚本把同一批买入名单换成三种口径并列，**只有后两种能真金白银下单**：

| 口径 | 含义 | 可执行 |
|---|---|---|
| `D0` | D 日竞价买 → D 日收盘卖 | ⛔ 违反 T+1，仅代理 |
| `T1C` | D 日竞价买 → D+1 日**收盘**卖 | ✅ |
| `T1O` | D 日竞价买 → D+1 日**开盘**卖 | ✅ 最保守（不吃次日日内） |

⚠️ T1C/T1O 的持仓会**跨夜**：同一只票连买两天时，资金占用不是线性的。
本脚本按「等权、不做资金重叠校验」计，是**乐观上限**，真实资金曲线还要打折。
⛔ 不能把 T1C 的复利当成账户真实复利。

用法：
    python3 scripts/research_t1_caliber_ab.py
    python3 scripts/research_t1_caliber_ab.py --json    # → cache/research/t1_caliber_ab_bt.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "wealth_code"))

spec = importlib.util.spec_from_file_location("pcr", ROOT / "scripts" / "pool_call_replay.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

from core import pool_call as P               # noqa: E402

OUT = ROOT / "cache" / "research" / "t1_caliber_ab_bt.json"

# 配置：(回避否决, 环境偏移, 低开豁免, 基因复核, 换位, 盘后预案)
CONFIGS: dict[str, tuple[bool, bool, bool, bool, bool, bool]] = {
    # 逐层阶梯：每一层只加一件事，用来摊开「到底是谁在贡献」
    "L0_v10":    (True,  False, False, False, False, True),   # v1.0 旧口径
    "L1_noavoid": (False, False, False, False, False, True),  # ① 关盘后回避
    "L2_rescue": (False, False, True,  False, False, True),   # ② 深低开豁免
    "L3_gene":   (False, False, True,  True,  True,  True),   # ③ 龙一基因·换位
    "L4_v11":    (False, False, True,  True,  True,  False),  # ④ 盘后预案整链关闭
}
LABEL = {"L0_v10": "v1.0 旧口径", "L1_noavoid": "① 关盘后回避",
         "L2_rescue": "② +深低开豁免", "L3_gene": "③ +龙一基因·换位",
         "L4_v11": "④ +关盘后预案（v1.1）"}


def _apply(cfg: tuple[bool, ...]) -> tuple[bool, ...]:
    avoid, env, rescue, gene, swap, plan = cfg
    saved = (P.POOL_CALL_BIAS_AVOID_VETO, P.POOL_CALL_DEV_ENV_OFFSET,
             P.POOL_CALL_DEEP_LOW_RESCUE, P.POOL_CALL_DRAGON_GENE,
             P.POOL_CALL_DRAGON_GENE_SWAP, P.POOL_CALL_EOD_PLAN)
    P.POOL_CALL_BIAS_AVOID_VETO = avoid
    P.POOL_CALL_DEV_ENV_OFFSET = env
    P.POOL_CALL_DEEP_LOW_RESCUE = rescue
    P.POOL_CALL_DRAGON_GENE = gene
    P.POOL_CALL_DRAGON_GENE_SWAP = swap
    P.POOL_CALL_EOD_PLAN = plan
    return saved


def _restore(saved: tuple[bool, ...]) -> None:
    (P.POOL_CALL_BIAS_AVOID_VETO, P.POOL_CALL_DEV_ENV_OFFSET,
     P.POOL_CALL_DEEP_LOW_RESCUE, P.POOL_CALL_DRAGON_GENE,
     P.POOL_CALL_DRAGON_GENE_SWAP, P.POOL_CALL_EOD_PLAN) = saved


def _bar(code: str, date: str):
    try:
        return R.bar(code, date)
    except Exception:                                    # noqa: BLE001
        return None


def _ret(code: str, date: str, mode: str, days: list[str]) -> float | None:
    """口径统一入口。缺任一端 K 线 → None（⛔ 不猜、不填 0）。"""
    b0 = _bar(code, date)
    if not b0:
        return None
    o = float(b0.get("open") or 0)
    if o <= 0:
        return None
    if str(b0.get("high")) == str(b0.get("low")):
        return None                                      # 一字板买不进
    if mode == "D0":
        c = float(b0.get("close") or 0)
        return round((c - o) / o * 100, 2) if c > 0 else None
    i = days.index(date)
    if i + 1 >= len(days):
        return None
    b1 = _bar(code, days[i + 1])
    if not b1:
        return None
    p1 = float(b1.get("close" if mode == "T1C" else "open") or 0)
    return round((p1 - o) / o * 100, 2) if p1 > 0 else None


def run(cfg: str, days: list[str]) -> list[dict]:
    saved = _apply(CONFIGS[cfg])
    rows: list[dict] = []
    try:
        for d in days:
            day = R.replay_day(d, days)
            if not day:
                continue
            kw = dict(day["kw"])
            # ⛔ 关预案时生产是 `pipeline_verify` 传 `stance=None`（连 ai_view 都不读）。
            #    回放里 `day["kw"]["stance"]` 来自旧快照 meta，若不抹掉就等于**没关** ——
            #    这正是 2026-11-06 差点得出的假结论（`stance or plan.stance` 会回落）。
            if not CONFIGS[cfg][5] and cfg == "L4_v11":
                kw["stance"] = None
            try:
                res = P.call_pool(day["cands"], day["quotes"], **kw)
            except Exception:                            # noqa: BLE001
                continue
            for c, v in (res.get("views") or {}).items():
                if v.get("view") != "participate":
                    continue
                rows.append({"date": d, "code": c, "name": v.get("name"),
                             "D0": _ret(c, d, "D0", days),
                             "T1C": _ret(c, d, "T1C", days),
                             "T1O": _ret(c, d, "T1O", days)})
    finally:
        _restore(saved)
    return rows


def summ(rows: list[dict], mode: str) -> dict:
    bd: dict[str, list[float]] = {}
    for r in rows:
        if r.get(mode) is not None:
            bd.setdefault(r["date"], []).append(r[mode])
    da = [st.mean(v) for v in bd.values()]
    if not da:
        return {}
    nav, peak, mdd = 1.0, 1.0, 0.0
    for x in da:
        nav *= 1 + x / 100
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1)
    rets = [x for v in bd.values() for x in v]
    return {"days": len(da), "picks": len(rets),
            "day_win": round(100 * sum(1 for x in da if x > 0) / len(da), 1),
            "pick_win": round(100 * sum(1 for x in rets if x > 0) / len(rets), 1),
            "day_avg": round(st.mean(da), 2),
            "compound": round((nav - 1) * 100, 2),
            "mdd": round(mdd * 100, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--days", type=int, default=35)
    a = ap.parse_args()
    days = R.trading_days()[-a.days:]
    print(f"回放窗口：{days[0]} ~ {days[-1]}（{len(days)} 个交易日）")
    print("⚠️ D0＝当日开→收（违反 T+1，仅代理）；T1C/T1O 才是可执行口径\n")

    out: dict = {}
    for cfg in CONFIGS:
        rows = run(cfg, days)
        out[cfg] = {"rows": rows, "T0": summ(rows, "D0"),
                    "T1C": summ(rows, "T1C"), "T1O": summ(rows, "T1O")}

    hdr = (f"{'配置':<16}{'口径':<7}{'天':>4}{'笔':>5}{'日胜':>7}{'单笔胜':>8}"
           f"{'日均':>9}{'复利':>10}{'回撤':>9}")
    print(hdr)
    print("-" * len(hdr))
    name = {"T0": "D0⛔代理", "T1C": "T1C✅", "T1O": "T1O✅"}
    for cfg in CONFIGS:
        for k in ("T0", "T1C", "T1O"):
            s = out[cfg][k]
            if not s:
                continue
            lbl = LABEL[cfg] if k == "T0" else ""
            print(f"{lbl:<16}{name[k]:<7}{s['days']:>4}{s['picks']:>5}{s['day_win']:>6.1f}%"
                  f"{s['pick_win']:>7.1f}%{s['day_avg']:>8.2f}%{s['compound']:>9.2f}%"
                  f"{s['mdd']:>8.2f}%")
        print()

    if a.json:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"window": [days[0], days[-1]], "trading_days": len(days),
                   "configs": {k: list(v) for k, v in CONFIGS.items()},
                   "summary": {c: {k: out[c][k] for k in ("T0", "T1C", "T1O")}
                               for c in CONFIGS},
                   "rows": {c: out[c]["rows"] for c in CONFIGS}},
                  open(OUT, "w"), ensure_ascii=False, indent=1)
        print(f"已落盘：{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
