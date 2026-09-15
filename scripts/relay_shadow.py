#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-13 影子模式：接力五维评分对照（2026-09-11 落地 · **零侵入**）。

背景：`wealth_code/core/relay_score.py`（五维加权：领涨地位/容量规模/板块联动/连板梯队/
资金净流入 · v0.1 已建并验证排序合理）**未接入 eod 主流程** → 等于造了把尺子没用。
本脚本以「影子模式」并行跑评分并落盘对照，⛔ **不改变任何产池**（relay/watch 保持 eod 现状）。

对照用法：积累数日后比较「脚本原序 vs 五维评分序」的次日实际表现（晋级/溢价），
再决定是否切换主流程（切换=另行拍板，属高风险改动）。

用法：
  python3 scripts/relay_shadow.py [--date YYYY-MM-DD]     # 默认取最近 candidates 文件
输出：
  cache/shadow/relay_shadow_{date}.json
  {date, generated_ts, source_file, env_digest, eod_relay[], shadow_ranked[], bands{}, diff{}}
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "wealth_code"))

CAND_DIR = ROOT / "wealth_code" / "cache"
OUT_DIR = ROOT / "cache" / "shadow"


def _latest_candidates(date: str | None) -> Path | None:
    pat = f"candidates-{date}.json" if date else "candidates-*.json"
    fs = sorted(glob.glob(str(CAND_DIR / pat)))
    return Path(fs[-1]) if fs else None


def build(date: str | None = None, verbose: bool = True) -> dict | None:
    try:
        from core.relay_score import rank_relay_candidates
    except Exception as e:
        print(f"[relay_shadow] 导入 relay_score 失败: {e}")
        return None
    f = _latest_candidates(date)
    if not f:
        print("[relay_shadow] 未找到 candidates 文件")
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[relay_shadow] 读取 {f.name} 失败: {e}")
        return None

    eod_relay = list(d.get("relay") or [])
    watch = list(d.get("watch") or [])
    pool = [dict(x) for x in eod_relay] + [dict(x) for x in watch]
    if not pool:
        print(f"[relay_shadow] {f.name} 无候选（relay+watch 均空）→ 跳过")
        return None

    env = dict(d.get("environment") or {})
    env.setdefault("max_boards", max((int(x.get("boards") or 0) for x in pool), default=0))

    try:
        ranked = rank_relay_candidates(pool, env)
    except Exception as e:
        print(f"[relay_shadow] 评分失败: {e}")
        return None

    def _row(x: dict) -> dict:
        return {"code": str(x.get("code") or ""), "name": x.get("name"),
                "theme": x.get("theme"), "boards": x.get("boards"),
                "score": x.get("score"), "band": x.get("band"),
                "pass": x.get("pass", True), "exclude_reason": x.get("exclude_reason")}

    # ⚠️ 两套评分刻度不同，字段分开命名防混淆：
    #   eod_relay[].score_eod  = 现产池 quant_score（0-100·当期 eod 口径）
    #   shadow_ranked[].score  = 五维加权分（relay_score 口径·绝对分）
    eod_rows = []
    for x in eod_relay:
        r = _row(x)
        r["score_eod"] = r.pop("score")
        eod_rows.append(r)
    sh_rows = [_row(x) for x in ranked]
    sh_pass = [r for r in sh_rows if r["pass"]]
    bands: dict[str, int] = {}
    for r in sh_rows:
        b = str(r.get("band") or "—")
        bands[b] = bands.get(b, 0) + 1

    eod_codes = [r["code"] for r in eod_rows]
    sh_codes = [r["code"] for r in sh_pass]
    n_top = min(3, len(eod_codes), len(sh_codes))
    out = {
        "date": d.get("date"),
        "generated_ts": datetime.now().isoformat(),
        "source_file": f.name,
        "env_digest": {k: env.get(k) for k in
                       ("regime", "sentiment_score", "sentiment_label", "promote_rate",
                        "nuke_count", "now_zt_count", "max_boards")},
        "mode": "shadow（只观察·不改产池）",
        "eod_relay": eod_rows,          # eod 主流程实际入池顺序（对照基线）
        "shadow_ranked": sh_rows,       # 五维评分排序（影子）
        "bands": bands,                 # 分档分布（首选/次选/观察/排除）
        "diff": {
            "top3_same": sum(1 for a, b in zip(eod_codes[:n_top], sh_codes[:n_top]) if a == b),
            "top_n_compared": n_top,
            "shadow_promoted": [c for c in sh_codes[:5] if c not in eod_codes[:5]],
            "eod_only": [c for c in eod_codes if c not in sh_codes],
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"relay_shadow_{d.get('date')}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if verbose:
        print(f"[relay_shadow {out['date']}] eod_relay={len(eod_rows)} | 评分池={len(sh_rows)} "
              f"(pass={len(sh_pass)}) | 分档={bands} | Top{n_top} 重合={out['diff']['top3_same']}/{n_top}")
        if out["diff"]["shadow_promoted"]:
            print(f"  影子提升票（前5）：{out['diff']['shadow_promoted']}")
        print(f"  → cache/shadow/relay_shadow_{out['date']}.json")
    return out


def main():
    ap = argparse.ArgumentParser(description="P2-13 接力五维评分影子对照（不改产池）")
    ap.add_argument("--date", default=None, help="candidates 日期 YYYY-MM-DD（默认最近）")
    args = ap.parse_args()
    build(args.date)


if __name__ == "__main__":
    main()
