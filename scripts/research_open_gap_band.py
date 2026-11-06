#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开盘幅度分档体检：低开该不该放进来？「开得特别高又没封」该不该硬拒？

## 为什么查这个（2026-11-06 14:45 用户）

用户两条指令：
  ① 「可以放进来」 —— 批准把**低开票**纳入 09:25 复核放行范围（现在机制性被挡）。
  ② 「一定要避开这种开的特别高又没有资格和行情秒板的票呀（扬子新材 +8.93%）」
     —— 11-05 现码重跑买的 3 只全是高开：鲁阳节能 +6.99%(+2.86%)、
        扬子新材 +8.93%(**−11.80%**)、桂林三金 +6.56%(**−5.18%**)；
        而当天涨的全是低开票：德才 −0.10%(+10.11%)、马矿 −9.53%(+6.13%)、
        万向德农 −2.30%(+6.23%)。

单日反例不足以定策（项目纪律），故用全部冻结件 + 竞价快照 + 真实日线重测。

## 数据口径（事前可得 + 事后真值分列，⛔ 不混用）

- 候选集：`wealth_code/cache/pool_call-<T>.json` 的 `views`（09:25 真实冻结件）
- 竞价读数：`wealth_code/cache/auction_quotes-<T>.json`
- 昨收质量：`cache/zt_history/zt_<T-1>.json`
- **事前**：`recheck_class`（`core.two_pool`）、题材家数、封单/流通比、换手、量能档、
  `defense.env.level`（当日盘前环境）
- **事后**（只做归因，⛔ 不得进判据）：`zt_<T>.json` 里 T 日是否真封板 + `fbt` 首封时间
- 收益：`cache/kline_qfq/<code>.json`
    · 当日 = T 日 `close/open − 1`（09:25 竞价成交 → 收盘）
    · T+1  = (T+1 `close`) / (T `open`) − 1（与账本一致）
- ⛔ 剔除竞价封死（gap ≥ 涨停 − 0.2pct）与无行情票

## 输出

A. 开盘幅度分档 × 收益（全样本 / 10cm / 20cm）
B. 高开档里「盘后资格」× 「T 日是否真封板」→ 谁在亏钱
C. 事前可判别的替代标尺：题材家数 / 封单比 / 量能档 / 环境 level
D. 规则回测：候选硬闸逐日净效果 + 11-05 / 11-06 反事实
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WC = ROOT / "wealth_code"
sys.path.insert(0, str(WC))
sys.path.insert(0, str(ROOT))

from core import ai_caliber as ac          # noqa: E402
from core import auction_state as ast     # noqa: E402
from core import expected_gap as eg       # noqa: E402
from core.two_pool import recheck_class   # noqa: E402

KLINE = ROOT / "cache/kline_qfq"
ZT = ROOT / "cache/zt_history"
POOL = WC / "cache"
OUT = ROOT / "cache/research/open_gap_band_bt.json"

BANDS = [("≤−5%", None, -5.0), ("−5~−2%", -5.0, -2.0), ("−2~0%", -2.0, 0.0),
         ("0~2%", 0.0, 2.0), ("2~5%", 2.0, 5.0), ("5~8%", 5.0, 8.0),
         ("≥8%", 8.0, None)]


def jl(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _f(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def band_of(gap: float) -> str:
    for lab, lo, hi in BANDS:
        if (lo is None or gap > lo) and (hi is None or gap <= hi):
            return lab
    return "?"


def zt_days() -> list[str]:
    out = []
    for p in ZT.glob("zt_*.json"):
        if len(p.stem) != 13:
            continue
        if (jl(p) or {}).get("zt_list"):
            out.append(p.stem[3:])
    return sorted(out)


def bars(code: str) -> dict[str, dict]:
    d = jl(KLINE / f"{code}.json") or {}
    return {str(b.get("date"))[:10]: b for b in (d.get("bars") or []) if b.get("date")}


def fbt_minutes(fbt) -> float | None:
    s = str(fbt or "").strip()
    if len(s) != 6 or not s.isdigit():
        return None
    h, m = int(s[:2]), int(s[2:4])
    return (h - 9) * 60 + (m - 30)      # 距 09:30 的分钟数


def build() -> tuple[list[dict], list[str]]:
    days = zt_days()
    zt_cache = {d: (jl(ZT / f"zt_{d}.json") or {}).get("zt_list") or [] for d in days}
    rows: list[dict] = []
    notes: list[str] = []
    for f in sorted(POOL.glob("pool_call-*.json")):
        day = f.stem.replace("pool_call-", "")
        prev = max((d for d in days if d < day), default=None)
        if prev is None:
            notes.append(f"{day}: 无前一交易日涨停归档")
            continue
        snap = jl(f) or {}
        views = snap.get("views") or {}
        meta = snap.get("meta") or {}
        env = ((meta.get("defense") or {}).get("env") or {}) or {}
        env_level = str(env.get("level") or "?")
        env_hits = len(env.get("hits") or [])
        quotes = jl(POOL / f"auction_quotes-{day}.json") or {}
        prev_rows = zt_cache.get(prev) or []
        if not prev_rows or not quotes:
            notes.append(f"{day}: 昨池/竞价快照缺失（prev={prev} quotes={len(quotes)}）")
            continue
        # ⛔ 冻结件 `views[].reason` 已被竞价裁决文案覆写，拿它算 recheck_class 会算错。
        #    唯一可信源是 `candidates-<T-1>.json`：那里有盘后原生的 watch_group/watch_reason。
        cand = jl(POOL / f"candidates-{prev}.json") or {}
        cand_map: dict[str, dict] = {}
        for k in ("relay", "watch"):
            for x in (cand.get(k) or []):
                if isinstance(x, dict) and x.get("code"):
                    cand_map[str(x["code"])] = x
        if not cand_map:
            notes.append(f"{day}: 缺 candidates-{prev}.json，资格维度不可还原")
        by_code = {str(r.get("code") or "").strip(): r for r in prev_rows}
        today_zt = {str(r.get("code") or "").strip(): r for r in (zt_cache.get(day) or [])}
        sec_n = defaultdict(int)
        for r in prev_rows:
            sec_n[str(r.get("sector") or "")] += 1
        mm = max((int(_f(r.get("boards")) or 0) for r in prev_rows), default=0)
        hit = miss = 0
        for code, v in views.items():
            if not isinstance(v, dict):
                continue
            q, pr = quotes.get(code), by_code.get(code)
            if not q or not pr:
                miss += 1
                continue
            au, pc = _f(q.get("auction_price")), _f(q.get("prev_close"))
            if not au or not pc:
                miss += 1
                continue
            gap = (au / pc - 1) * 100
            lim = ac.limit_up_pct(bool(pr.get("is_20cm")))
            if gap >= lim - 0.2:
                miss += 1                       # 竞价封死，买不进
                continue
            exp = eg.expected_gap_pct(pr)
            cls = ast.classify(gap_pct=gap, amount_yi=q.get("auction_amount_yi"),
                               prev_amount_yi=pr.get("cje_yi"),
                               expected_gap_pct=exp, max_gap_pct=lim)
            b = bars(code)
            bd = b.get(day)
            nxt = next((d for d in sorted(b) if d > day), None)
            if not bd:
                miss += 1
                continue
            o, c = _f(bd.get("open")), _f(bd.get("close"))
            ret_d = (c / o - 1) * 100 if (o and c) else None
            c2 = _f(b.get(nxt, {}).get("close")) if nxt else None
            ret_n = (c2 / o - 1) * 100 if (o and c2) else None
            if ret_d is None and ret_n is None:
                miss += 1
                continue
            need = (_f(pr.get("cje_yi")) or 0) * 0.03
            vb = ac.volume_band(q.get("auction_amount_yi"), need)
            cr = cand_map.get(code) or {}
            grp = str(cr.get("watch_group") or "")
            why = str(cr.get("watch_reason") or "")
            rc = recheck_class({"code": code, "name": v.get("name") or pr.get("name"),
                                "watch_group": grp, "watch_reason": why,
                                "is_one_word": v.get("is_one_word") or cr.get("is_one_word"),
                                "historical_repair": cr.get("historical_repair")})
            # 资格近似：盘后有没有给「接力资格」（relay 名单 = 已获资格）
            in_relay = bool(cand.get("relay")) and any(
                str(x.get("code")) == code for x in (cand.get("relay") or []))
            zone = ac.gap_zone(gap, None, expected_gap_pct=exp,
                               is_20cm=bool(pr.get("is_20cm")))
            today = today_zt.get(code)
            seal, lt = _f(pr.get("seal")), _f(pr.get("lt_yi"))
            rows.append({
                "date": day, "code": code, "name": v.get("name") or pr.get("name"),
                "boards": int(_f(v.get("boards")) or pr.get("boards") or 0),
                "gap": round(gap, 2), "band": band_of(gap),
                "lim": lim, "is20": bool(pr.get("is_20cm")),
                "gap_of_lim": round(gap / lim * 100, 1),
                "state": cls.get("auction_state"),
                "dev": cls.get("gap_vs_expected"),
                "zone": zone,
                "line_is_core": bool(v.get("line_is_core")),
                "relay_mood": v.get("relay_mood"),
                "line_rank": v.get("line_rank"),
                "watch_group": grp, "watch_reason": why[:40],
                "in_relay": in_relay, "recheck": rc,
                "sec_n": sec_n.get(str(pr.get("sector") or ""), 0),
                "seal_ratio": round(seal / lt * 100, 2) if (seal is not None and lt) else None,
                "hs": _f(pr.get("hs")), "zbc": int(_f(pr.get("zbc")) or 0),
                "prev_fbt": fbt_minutes(pr.get("fbt")),
                "cje_yi": _f(pr.get("cje_yi")),
                "vol_band": vb.get("band"), "vol_ratio": vb.get("ratio"),
                "view": v.get("view"),
                "env_level": env_level, "env_hits": env_hits,
                "sealed": bool(today),
                "fbt_min": fbt_minutes((today or {}).get("fbt")),
                "ret_d": round(ret_d, 2) if ret_d is not None else None,
                "ret_n": round(ret_n, 2) if ret_n is not None else None,
            })
            hit += 1
        notes.append(f"{day}: 入样 {hit} / 缺 {miss}｜昨池 n={len(prev_rows)} 最高 {mm} 板"
                     f"｜env={env_level}({env_hits})｜今日封板 {len(today_zt)}"
                     f"｜候选文件 {len(cand_map)} 只")
    return rows, notes


def agg(rs, key="ret_d"):
    v = [r[key] for r in rs if r.get(key) is not None]
    if not v:
        return {"n": 0}
    return {"n": len(v), "avg": round(st.mean(v), 2), "med": round(st.median(v), 2),
            "win": round(sum(1 for x in v if x > 0) / len(v) * 100, 1)}


def line(lab, a, w=20):
    if not a.get("n"):
        return f"  {lab:<{w}} n=0"
    return (f"  {lab:<{w}} n={a['n']:<4} 当日 {a['avg']:+6.2f}%/中位 {a['med']:+6.2f}%"
            f"/胜 {a['win']:5.1f}%")


def main() -> int:
    rows, notes = build()
    print("=== 数据构建 ===")
    for n in notes:
        print("  ", n)
    days = {r["date"] for r in rows}
    print(f"\n入样票次 {len(rows)}｜交易日 {len(days)}"
          f"｜{min(days) if days else '-'} ~ {max(days) if days else '-'}")
    if not rows:
        return 1

    # ── A. 开盘幅度分档 ─────────────────────────────────────────────
    print("\n=== A. 开盘幅度分档（可成交票，09:25 买 → 收盘）===")
    print("  【全样本】")
    for lab, _, _ in BANDS:
        rs = [r for r in rows if r["band"] == lab]
        print(line(lab, agg(rs)))
    print("  【10cm】")
    for lab, _, _ in BANDS:
        rs = [r for r in rows if r["band"] == lab and not r["is20"]]
        a = agg(rs)
        if a.get("n"):
            print(line(lab, a))
    print("  【20cm（绝对幅度不可比，仅陈列）】")
    for lab, _, _ in BANDS:
        rs = [r for r in rows if r["band"] == lab and r["is20"]]
        a = agg(rs)
        if a.get("n"):
            print(line(lab, a))

    print("\n  【按涨停幅度归一 gap/limit】")
    for lab, lo, hi in [("≤0（低开）", None, 0.0), ("0~20%", 0.0, 20.0),
                        ("20~50%", 20.0, 50.0), ("50~80%", 50.0, 80.0),
                        ("≥80%", 80.0, None)]:
        rs = [r for r in rows
              if (lo is None or r["gap_of_lim"] > lo) and (hi is None or r["gap_of_lim"] <= hi)]
        print(line(lab, agg(rs)))

    # ── B. 高开 × 资格 × 事后封板 ───────────────────────────────────
    print("\n=== B. 开盘 ≥5% 的票：资格 × 是否真封板（事后归因）===")
    hi = [r for r in rows if r["gap"] >= 5.0]
    print(f"  开盘 ≥5% 共 {len(hi)} 只（占全样本 {len(hi)/len(rows)*100:.1f}%）")
    for qlab, qf in (("盘后资格=可复核(state)", lambda r: r["recheck"] == "state"),
                     ("盘后资格=硬拒/未过", lambda r: r["recheck"] != "state")):
        for slab, sf in (("T日真封板", lambda r: r["sealed"]),
                         ("T日没封板", lambda r: not r["sealed"])):
            print(line(f"{qlab} · {slab}", agg([r for r in hi if qf(r) and sf(r)]), 34))

    print("\n  【对照组：开盘 <5%】")
    lo5 = [r for r in rows if r["gap"] < 5.0]
    for qlab, qf in (("可复核 state", lambda r: r["recheck"] == "state"),
                     ("硬拒/未过", lambda r: r["recheck"] != "state")):
        for slab, sf in (("T日真封板", lambda r: r["sealed"]),
                         ("T日没封板", lambda r: not r["sealed"])):
            print(line(f"{qlab} · {slab}", agg([r for r in lo5 if qf(r) and sf(r)]), 24))

    # ── C. 事前可判别的替代标尺 ─────────────────────────────────────
    print("\n=== C. 事前标尺：高开(≥5%)内部还能用什么区分 ===")
    print("  · 题材家数（昨日同 sector 涨停数）")
    for lab, lo, hi2 in [("1 家（孤苗）", None, 1), ("2 家", 1, 2), ("≥3 家", 2, None)]:
        rs = [r for r in hi if (lo is None or r["sec_n"] > lo) and (hi2 is None or r["sec_n"] <= hi2)]
        print(line(lab, agg(rs), 16))
    print("  · 封单/流通比")
    for lab, lo, hi2 in [("<2%", None, 2.0), ("2~5%", 2.0, 5.0), ("≥5%", 5.0, None),
                         ("缺数据", None, None)]:
        if lab == "缺数据":
            rs = [r for r in hi if r["seal_ratio"] is None]
        else:
            rs = [r for r in hi if r["seal_ratio"] is not None
                  and (lo is None or r["seal_ratio"] > lo) and (hi2 is None or r["seal_ratio"] <= hi2)]
        print(line(lab, agg(rs), 16))
    print("  · 竞价量能档")
    for lab in ("full", "soft", "dry", "unknown"):
        print(line(lab, agg([r for r in hi if r["vol_band"] == lab]), 16))
    print("  · 当日盘前环境")
    for lab in ("normal", "defensive", "block", "?"):
        rs = [r for r in hi if r["env_level"] == lab]
        a = agg(rs)
        if a.get("n"):
            print(line(lab, a, 16))
    print("  · 板数")
    for lab, lo, hi2 in [("1 板", None, 1), ("2 板", 1, 2), ("≥3 板", 2, None)]:
        rs = [r for r in hi if (lo is None or r["boards"] > lo) and (hi2 is None or r["boards"] <= hi2)]
        print(line(lab, agg(rs), 16))

    print("  · 竞价三态 / 相对预期")
    for lab in ("A", "B", "C", "unknown"):
        a = agg([r for r in hi if r["state"] == lab])
        if a.get("n"):
            print(line(f"三态 {lab}", a, 16))
    for lab, lo, hi2 in [("<0（低于预期）", None, 0.0), ("0~3", 0.0, 3.0),
                         ("3~6", 3.0, 6.0), ("≥6", 6.0, None), ("缺预期", None, None)]:
        if lab == "缺预期":
            rs = [r for r in hi if r["dev"] is None]
        else:
            rs = [r for r in hi if r["dev"] is not None
                  and (lo is None or r["dev"] > lo) and (hi2 is None or r["dev"] <= hi2)]
        a = agg(rs)
        if a.get("n"):
            print(line(f"DEV {lab}", a, 16))
    print("  · 昨日首封时间（越早=越强，能否预判次日秒板）")
    for r in hi:
        pass
    for lab, lo, hi2 in [("≤10min（秒板）", None, 10.0), ("10~60min", 10.0, 60.0),
                         ("≥60min", 60.0, None), ("缺", None, None)]:
        rs = [r for r in hi if r["prev_fbt"] is None and lab == "缺"] or \
             [r for r in hi if r["prev_fbt"] is not None
              and (lo is None or r["prev_fbt"] > lo) and (hi2 is None or r["prev_fbt"] <= hi2)]
        a = agg(rs)
        if a.get("n"):
            print(line(lab, a, 16))
    print("  · 是否同线最高板 / 情绪")
    for lab, fn in (("line_is_core", lambda r: r["line_is_core"]),
                    ("非核心", lambda r: not r["line_is_core"]),
                    ("relay=pressure", lambda r: r["relay_mood"] == "pressured"),
                    ("relay=supportive", lambda r: r["relay_mood"] == "supportive")):
        a = agg([r for r in hi if fn(r)])
        if a.get("n"):
            print(line(lab, a, 16))

    # ── C2. 归一化：剩余空间还剩多少 ─────────────────────────────────
    print("\n=== C2. 归一化视角 gap/limit（剩余到涨停的空间）===")
    print("  【10cm】")
    for lab, lo, hi2 in [("<0", None, 0.0), ("0~20%", 0.0, 20.0), ("20~35%", 20.0, 35.0),
                         ("35~50%", 35.0, 50.0), (">50%", 50.0, None)]:
        rs = [r for r in rows if not r["is20"] and
              ((lo is None and r["gap_of_lim"] < 0) if lab == "<0" else
               (r["gap_of_lim"] >= 0 and (lo is None or r["gap_of_lim"] > lo)
                and (hi2 is None or r["gap_of_lim"] <= hi2)))]
        a = agg(rs)
        if a.get("n"):
            print(line(lab, a, 16))
    print("  【20cm】")
    for lab, lo, hi2 in [("<0", None, 0.0), ("0~20%", 0.0, 20.0), ("20~35%", 20.0, 35.0),
                         ("35~50%", 35.0, 50.0), (">50%", 50.0, None)]:
        rs = [r for r in rows if r["is20"] and
              ((lo is None and r["gap_of_lim"] < 0) if lab == "<0" else
               (r["gap_of_lim"] >= 0 and (lo is None or r["gap_of_lim"] > lo)
                and (hi2 is None or r["gap_of_lim"] <= hi2)))]
        a = agg(rs)
        if a.get("n"):
            print(line(lab, a, 16))

    # ── D. 规则回测 ─────────────────────────────────────────────────
    print("\n=== D. 候选硬闸：谁会被砍、砍掉的是不是亏的 ===")
    rules = [
        ("R1 高开≥5（绝对）", lambda r: r["gap"] >= 5.0),
        ("R2 高开≥8（绝对）", lambda r: r["gap"] >= 8.0),
        ("R3 剩余空间<涨停一半（gap/limit>50%）", lambda r: r["gap_of_lim"] > 50.0),
        ("R4 剩余空间<涨停六成（gap/limit>40%）", lambda r: r["gap_of_lim"] > 40.0),
        ("R5 R4 且 非state（真没资格）", lambda r: r["gap_of_lim"] > 40.0 and r["recheck"] != "state"),
        ("R6 R1 且 非state", lambda r: r["gap"] >= 5.0 and r["recheck"] != "state"),
        ("R7 R1 且 题材<3家", lambda r: r["gap"] >= 5.0 and r["sec_n"] < 3),
        ("R8 R1 且 3板以上", lambda r: r["gap"] >= 5.0 and r["boards"] >= 3),
    ]
    for lab, fn in rules:
        cut = [r for r in rows if fn(r)]
        keep = [r for r in rows if not fn(r)]
        ac_, ak = agg(cut), agg(keep)
        if not ac_.get("n"):
            continue
        an = agg(cut, "ret_n")
        print(f"  {lab}")
        print(f"    砍掉 {ac_.get('n'):<3} 只 → 当日 {ac_.get('avg', 0):+6.2f}%/胜 {ac_.get('win', 0):5.1f}%"
              f"｜T+1 {an.get('avg', 0):+6.2f}%/胜 {an.get('win', 0):5.1f}%"
              f"  ‖ 留下 {ak.get('n'):<3} 只 → 当日 {ak.get('avg', 0):+6.2f}%/胜 {ak.get('win', 0):5.1f}%")

    print("\n  【被 R3（gap/limit>50%）砍掉的票逐个列出】")
    for r in sorted([x for x in rows if x["gap_of_lim"] > 50.0], key=lambda x: (x["date"], -x["gap"])):
        rd = "NA" if r["ret_d"] is None else "%+6.2f" % r["ret_d"]
        print(f"    {r['date']} {r['code']} {r['name']:<6} 开 {r['gap']:+6.2f}% "
              f"(占涨停 {r['gap_of_lim']:5.1f}%) {r['boards']}板 题材{r['sec_n']}家 "
              f"{r['recheck']:<10} 封={int(r['sealed'])} 当日 {rd}%")

    # ── E. 低开票该不该放进来 ───────────────────────────────────────
    print("\n=== E. 低开票（gap<0）明细 ===")
    low = [r for r in rows if r["gap"] < 0]
    print(line("低开全部", agg(low)))
    for q in ("state", "restricted", "hard"):
        a = agg([r for r in low if r["recheck"] == q])
        if a.get("n"):
            print(line(f"  · recheck={q}", a, 24))
    print("  · 低开 × 深度")
    for lab, lo, hi2 in [("−3~0%", -3.0, 0.0), ("−6~−3%", -6.0, -3.0), ("≤−6%", None, -6.0)]:
        a = agg([r for r in low if (lo is None or r["gap"] > lo)
                 and (hi2 is None or r["gap"] <= hi2)])
        if a.get("n"):
            print(line(lab, a, 14))
    print("  · 低开 × 题材家数")
    for lab, lo, hi2 in [("1 家", None, 1), ("2 家", 1, 2), ("≥3 家", 2, None)]:
        a = agg([r for r in low if (lo is None or r["sec_n"] > lo)
                 and (hi2 is None or r["sec_n"] <= hi2)])
        if a.get("n"):
            print(line(lab, a, 14))
    print("  · 低开 × 当日是否反包封板（事后）")
    for lab, fn in (("低开且当日封板", lambda r: r["sealed"]),
                    ("低开且当日没封", lambda r: not r["sealed"])):
        a = agg([r for r in low if fn(r)])
        if a.get("n"):
            print(line(lab, a, 18))
    print("  · 低开 × 逐日方向（防单日驱动）")
    by_day_low = defaultdict(list)
    for r in low:
        by_day_low[r["date"]].append(r)
    pos = sum(1 for d, v in by_day_low.items() if st.mean([x["ret_d"] for x in v
                                                           if x["ret_d"] is not None]) > 0)
    print(f"    有低开票的 {len(by_day_low)} 天里，当日平均为正 {pos} 天"
          f"（{pos/max(1,len(by_day_low))*100:.0f}%）")

    # ── F. 11-05 / 11-06 反事实 ─────────────────────────────────────
    print("\n=== F. 近两日反事实 ===")
    for day in ("2026-11-05",):
        rs = [r for r in rows if r["date"] == day]
        if not rs:
            continue
        print(f"  {day}：池内可成交 {len(rs)} 只，当日平均 {agg(rs).get('avg', 0):+.2f}%"
              f"/胜 {agg(rs).get('win', 0):.1f}%")
        for lab, fn in (("A 全可复核(state)", lambda r: r["recheck"] == "state"),
                        ("B 可复核 且 剩余空间>半", lambda r: r["recheck"] == "state"
                         and r["gap_of_lim"] <= 50.0),
                        ("C 全部 且 剩余空间>半", lambda r: r["gap_of_lim"] <= 50.0)):
            sel = [r for r in rs if fn(r)]
            a = agg(sel)
            if a.get("n"):
                detail = "、".join("%s(%+.1f%%)" % (x["name"], x["ret_d"] or 0) for x in sel)
                print(f"    {lab:<24} n={a['n']:<3} {a['avg']:+6.2f}%/胜 {a['win']:5.1f}% ← {detail}")

    # ── G. 盘后分组归因 ─────────────────────────────────────────────
    print("\n=== G. 盘后观察分组 → 复核类别 → 收益（哪一组被整组挡住）===")
    by_grp = defaultdict(list)
    for r in rows:
        by_grp[(r["watch_group"] or "(已获资格/无分组)", r["recheck"])].append(r)
    for (g, rc), rs in sorted(by_grp.items(), key=lambda kv: -len(kv[1])):
        a = agg(rs)
        if a.get("n"):
            print(f"  {g:<22} recheck={rc:<10} n={a['n']:<4} "
                  f"当日 {a['avg']:+6.2f}%/胜 {a['win']:5.1f}%")

    # ── H. 链路近似（不含名额/排序，只到「资格 + zone」这一层）──────────
    print("\n=== H. 链路近似：放宽「历史连板修复」进 state + 高开硬拒 ===")
    print("  ⛔ 本段只模拟「资格闸 + DEV zone + 高开闸」三道，不含名额/排序/量能否决")

    def sim(r, *, widen, high_cut):
        if high_cut and r["gap_of_lim"] > high_cut:
            return False
        ok = r["in_relay"] or r["recheck"] == "state"
        if widen and not ok:
            ok = r["watch_group"] == "历史连板修复"
        return ok and r["zone"] != "below"

    variants = [
        ("① 现状（不含历史修复）", dict(widen=False, high_cut=None)),
        ("② 放宽历史修复进复核", dict(widen=True, high_cut=None)),
        ("③ ② + 高开>50%硬拒", dict(widen=True, high_cut=50.0)),
        ("④ 只加高开>50%硬拒", dict(widen=False, high_cut=50.0)),
    ]
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    for lab, kw in variants:
        day_avg, picked, wins = [], 0, 0
        for day, rs in by_day.items():
            sel = [r for r in rs if sim(r, **kw)]
            if not sel:
                day_avg.append(0.0)          # 空仓记 0
                continue
            vs = [r["ret_d"] for r in sel if r["ret_d"] is not None]
            if vs:
                day_avg.append(st.mean(vs))
            picked += len(sel)
            wins += sum(1 for x in vs if x > 0)
        tot = sum(1 for day, rs in by_day.items() for r in rs
                  if sim(r, **kw) and r["ret_d"] is not None)
        allv = [r["ret_d"] for day, rs in by_day.items() for r in rs
                if sim(r, **kw) and r["ret_d"] is not None]
        if not allv:
            continue
        print(f"  {lab:<26} 选中 {tot:<4} 只｜逐票 {st.mean(allv):+6.2f}%"
              f"/胜 {sum(1 for x in allv if x > 0)/len(allv)*100:5.1f}%"
              f"｜逐日等权 {st.mean(day_avg):+6.2f}%（正 {sum(1 for x in day_avg if x > 0)}/{len(day_avg)} 天）")

    print("\n  【② 相对 ① 新增的票（放宽历史修复后进来的）】")
    for day in sorted(by_day):
        add = [r for r in by_day[day]
               if sim(r, widen=True, high_cut=None) and not sim(r, widen=False, high_cut=None)]
        if not add:
            continue
        print(f"    {day}: " + "、".join(
            f"{r['name']}(开{r['gap']:+.1f}%/{r['ret_d'] if r['ret_d'] is not None else 'NA'}%)"
            for r in add))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated": "scripts/research_open_gap_band.py",
                               "rows": rows, "notes": notes}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n产物 → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
