"""「买隔壁」战法回测 —— 一字顶板核心 → 同题材可买票。

用户 2026-10-09 定的战法：市场核心龙头一字封死（钱买不进），
资金外溢到同板块相邻票 → 买"隔壁"是参与这条线的唯一途径。
离场铁律「开板即走」：核心一开板，隔壁仓位立刻清。

口径
- 核心 core：昨日涨停（boards≥2）+ 今日竞价 gap ≥ 涨停−0.2 → 物理买不进
- 隔壁 neighbor：今日竞价可买、与某 core 的 plates 有交集（排除泛化标签）
- 收益：竞价价 → 当日收盘 / 次日收盘（账本 T+1 口径）

⛔ 本脚本只做研究，不接生产、不改任何裁决。
⛔ 凡标注 FUTURE-LEAK 的分组用了「核心当日是否收盘涨停」（事后信息），
   09:25 不可知，只能用于解释，绝不可转成判据。
⚠️ 竞价快照只覆盖「昨涨停池」，故"隔壁"实为「同题材昨日也涨停的票」，
   不是真正的外溢目标（昨没涨停的首板/低位票没有竞价数据）。
"""
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wealth_code"))
sys.path.insert(0, str(ROOT / "scripts"))

from common.trading_calendar import prev_a_share_trading_day  # noqa: E402
from core import ai_caliber as ac                              # noqa: E402
import sector_gap_daily_trace as tr                            # noqa: E402

SCHEMA = "buy_neighbor_bt_v1"
# 泛化标签：不能作为"同线"依据（次新是身份不是题材）
GENERIC = {"次新股", "其他", "ST", "退市", "昨日涨停", "高送转", "重组", ""}
DEFAULT_OUT = ROOT / "cache" / "research" / "buy_neighbor_bt.json"


def _load(p: Path, d=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {} if d is None else d


def is20(c: str, y: dict) -> bool:
    return bool(y.get("is_20cm")) or c.startswith(("300", "301"))


def plates_of(mapping: dict, c: str) -> set:
    raw = (mapping.get(c) or {}).get("plates") or []
    out = {str(x).strip() for x in raw if str(x).strip() and str(x).strip() not in GENERIC}
    if not out:
        th = str((mapping.get(c) or {}).get("theme") or "").strip()
        if th and th not in GENERIC:
            out = {th}
    return out


def stat(rows, key):
    v = [r[key] for r in rows if r.get(key) is not None]
    if not v:
        return None
    return dict(n=len(rows), cov=len(v), mean=round(st.mean(v), 2),
                med=round(st.median(v), 2),
                win=round(sum(1 for x in v if x > 0) / len(v) * 100, 1),
                zt=sum(1 for r in rows if r.get("zt_today")))


def show(label, rows, note=""):
    a, b = stat(rows, "ret_same_close"), stat(rows, "ret_t1_close")
    if not a:
        print(f"  {label:<30} n=0")
        return
    flag = f"  {note}" if note else ""
    print(f"  {label:<30} n={a['n']:4d} | 当日 {a['mean']:+6.2f}% 中{a['med']:+6.2f} "
          f"胜{a['win']:5.1f}% | T+1 {b['mean']:+6.2f}% 中{b['med']:+6.2f} "
          f"胜{b['win']:5.1f}% | 晋级{a['zt']:3d}({a['zt'] / a['n'] * 100:4.1f}%){flag}")


def run(n_days=45, end="2026-11-06"):
    days = tr.trading_days(end, n_days)
    allrows, dayinfo = [], []
    for i, d in enumerate(days):
        prev = days[i - 1] if i > 0 else prev_a_share_trading_day(d)
        qp = ROOT / "wealth_code" / "cache" / f"auction_quotes-{d}.json"
        thp = ROOT / "cache" / "theme_snapshot" / f"theme_{prev}.json"
        if not qp.exists() or not thp.exists():
            continue
        q = _load(qp, {})
        if len(q) < 10:
            continue
        z4 = {str(r.get("code")): r for r in
              (_load(ROOT / "cache" / "zt_history" / f"zt_{prev}.json", {}).get("zt_list") or [])}
        z5 = {str(r.get("code")): r for r in
              (_load(ROOT / "cache" / "zt_history" / f"zt_{d}.json", {}).get("zt_list") or [])}
        mapping = _load(thp, {}).get("mapping") or {}
        if not isinstance(mapping, dict):
            continue

        # ── 1) 封死核心 ──────────────────────────────────────────────
        cores = []
        for c, y in z4.items():
            v = q.get(c)
            if not isinstance(v, dict):
                continue
            pc, ap = v.get("prev_close"), v.get("auction_price")
            if not (isinstance(pc, (int, float)) and isinstance(ap, (int, float))
                    and pc > 0 and ap > 0):
                continue
            gap = (ap / pc - 1) * 100
            if not ac.unbuyable_gap(gap, is20(c, y)):
                continue
            if (y.get("boards") or 1) < 2:      # 首板一字不算"核心龙头"
                continue
            if c.startswith("688") or "ST" in str(y.get("name") or "").upper():
                continue
            p = plates_of(mapping, c)
            if not p:
                continue
            cores.append(dict(code=c, name=y.get("name"), boards=y.get("boards"),
                              gap=round(gap, 2), plates=p,
                              held=bool(z5.get(c)), zbc=(z5.get(c) or {}).get("zbc")))
        if not cores:
            dayinfo.append(dict(date=d, n_core=0, n_nb=0, cores=[]))
            continue
        day_has_quote = any(c in q for c in [k["code"] for k in cores])
        if not day_has_quote:
            continue

        # ── 2) 逐票分类 ──────────────────────────────────────────────
        n_nb = 0
        for c, v in q.items():
            pc, ap = v.get("prev_close"), v.get("auction_price")
            if not (isinstance(pc, (int, float)) and isinstance(ap, (int, float))
                    and pc > 0 and ap > 0):
                continue
            y = z4.get(c) or {}
            nm = (v.get("name") or y.get("name")
                  or (mapping.get(c) or {}).get("name") or "")
            g = round((ap / pc - 1) * 100, 2)
            if ac.unbuyable_gap(g, is20(c, y)) or c.startswith("688") \
                    or "ST" in str(nm).upper():
                continue
            bars = tr.bars_of(c)
            b_prev = next((b for b in reversed(bars) if str(b["date"]) <= prev), None)
            b_today = next((b for b in bars if str(b["date"]) == d), None)
            b_next = next((b for b in bars if str(b["date"]) > d), None)
            if b_prev and abs(float(b_prev["close"]) - pc) / pc > 0.01:
                continue
            close = (b_today or {}).get("close")
            if not isinstance(close, (int, float)) or close <= 0:
                continue        # 当日 K 未就绪（盘中运行）→ 不计入
            t1 = (b_next or {}).get("close")
            cp = plates_of(mapping, c)
            hit = [k for k in cores if k["code"] != c and (k["plates"] & cp)]
            own_b = y.get("boards")
            e = tr.eg.expected_gap_pct(y)
            row = dict(
                date=d, code=c, name=nm, gap=g, boards=own_b, plates=sorted(cp),
                is_nb=bool(hit), core_codes=[k["code"] for k in hit],
                core_boards=max([k["boards"] for k in hit], default=None),
                board_gap=((max([k["boards"] for k in hit], default=0) - (own_b or 0))
                           if hit else None),
                # FUTURE-LEAK：09:25 不可能知道核心当天会不会收盘涨停
                core_all_held=bool(hit) and all(k["held"] for k in hit),
                core_any_broke=bool(hit) and any(not k["held"] for k in hit),
                dev=(round(g - e, 2) if e is not None else None),
                ret_same_close=round((close / ap - 1) * 100, 2),
                ret_t1_close=(round((t1 / ap - 1) * 100, 2)
                              if isinstance(t1, (int, float)) and t1 > 0 else None),
                zt_today=c in z5)
            if hit:
                n_nb += 1
            allrows.append(row)
        dayinfo.append(dict(date=d, n_core=len(cores), n_nb=n_nb,
                            cores=[f"{k['name']}b{k['boards']}" for k in cores]))
    return allrows, dayinfo


def main():
    rows, dayinfo = run()
    nb = [r for r in rows if r["is_nb"]]
    non = [r for r in rows if not r["is_nb"]]
    print("=" * 104)
    print(f"有效日 {len(dayinfo)} / 有封死核心日 {len([x for x in dayinfo if x['n_core']])} "
          f"/ 可买票次 {len(rows)} / 隔壁 {len(nb)}")
    print("=" * 104)

    print("\n【1】总对照")
    show("隔壁（同题材有封死核心）", nb)
    show("非隔壁（基准）", non, "← 隔壁反而更差" if nb and non else "")

    print("\n【2】核心当日结局 ⚠️ FUTURE-LEAK（09:25 不可知，仅供解释）")
    show("核心全封住", [r for r in nb if r["core_all_held"]])
    show("有核心炸板", [r for r in nb if r["core_any_broke"]], "← 不走就是这代价")

    print("\n【3】核心板数分档（09:25 可知）")
    for lo, hi, lab in ((2, 2, "核心2板"), (3, 3, "核心3板"),
                        (4, 4, "核心4板"), (5, 99, "核心≥5板")):
        show(lab, [r for r in nb if r["core_boards"] and lo <= r["core_boards"] <= hi])

    print("\n【4】板差分档（核心板数 − 自身板数，09:25 可知）")
    for lab, cond in (("差=0 同板", lambda x: x == 0), ("差=1", lambda x: x == 1),
                      ("差=2", lambda x: x == 2), ("差≥3", lambda x: x is not None and x >= 3)):
        show(lab, [r for r in nb if cond(r["board_gap"])])

    print("\n【5】隔壁自身开幅（09:25 可知）")
    for lab, lo, hi in (("低开<0", -99, 0), ("0~3", 0, 3), ("3~6", 3, 6), ("≥6", 6, 99)):
        show(lab, [r for r in nb if lo <= r["gap"] < hi])

    print("\n【6】可执行最优子集拼装（全部 09:25 可知，无未来函数）")
    best = [r for r in nb if r["board_gap"] == 1]
    show("板差=1", best)
    show("板差=1 ∩ 低开", [r for r in best if r["gap"] < 0])
    show("板差=1 ∩ 核心≤3板", [r for r in best if r["core_boards"] and r["core_boards"] <= 3])
    show("板差=1 ∩ 低开 ∩ 核心≤3板",
         [r for r in best if r["gap"] < 0 and r["core_boards"] and r["core_boards"] <= 3])

    print("\n【7】若叠加「核心全封住」（未来函数，仅看上限）")
    show("板差=1 ∩ 核心全封住", [r for r in best if r["core_all_held"]], "⛔ 不可执行")

    byday = {}
    for r in best:
        byday.setdefault(r["date"], []).append(r)
    print("\n【8】板差=1 逐日 T+1（稳定性）")
    for d in sorted(byday):
        s = stat(byday[d], "ret_t1_close")
        print(f"  {d} n={s['n']:2d} T+1 {s['mean']:+7.2f}% 胜{s['win']:5.1f}%")
    pos = sum(1 for d in byday if (stat(byday[d], "ret_t1_close") or {}).get("mean", 0) > 0)
    print(f"  正收益天数 {pos}/{len(byday)}")

    print("\n【9】逐日核心明细")
    for x in dayinfo:
        if not x["n_core"]:
            continue
        print(f"  {x['date']} 核心{x['n_core']} 隔壁{x['n_nb']:3d}  "
              + ", ".join(x["cores"])[:80])

    DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT.write_text(json.dumps(
        dict(schema=SCHEMA, rows=rows, days=dayinfo,
             caveats=[
                 "竞价快照只覆盖昨涨停池，隔壁实为同题材昨日也涨停的票",
                 "core_all_held / core_any_broke 使用当日收盘涨停池，属未来函数，不可作判据",
                 "日 K 无法回测盘中开板时点，「开板即走」的真实效果无法从此口径验证",
             ]), ensure_ascii=False), "utf-8")
    print(f"\n已写 {DEFAULT_OUT}")


if __name__ == "__main__":
    main()
