#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同花顺金融数据（hithink-finance）统一拉取/校验/后处理脚本。

P0/P1 七项能力落地（2026-08-26，独立落盘 + 交叉校验 + 可逆开关，零侵入现有核心链路）：

  P0-1 涨停池   limitup   [--check] [--date YYYY-MM-DD]   拉涨停池+题材归因，--check 对比必盈 yesterday_zt.json
  P0-2 龙虎榜   lhb       [--check] [--date]              拉机构/游资净额，--check 对比东财 lhb_YYYYMMDD.json
  P0-3 连板天梯 ladder    [--check]                       拉近30日连板梯队，--check 对比 feedback_matrix 最高板
  P1-1 集合竞价 auction   [--date] [--verify-aux]         拉当日竞价风向标，--verify-aux 打印 verify 辅助建议
  P1-2 异动原因 anomaly   [--date]                        拉当日全市场异动原因落盘
  P1-3 估值快照 valuation [--codes 600519.SH,...] [--enrich-candidates [--apply]]
                                                          拉估值快照；--enrich-candidates 给财富密码候选注入估值字段
  P1-4 全市场导出 dump    [--recent] [--apply]              默认10年日K；--recent 拉近10交易日增量（--apply 才下载）
  all   [--check]                                          盘后一键跑 P0 三项 + P1 前两项（不写回候选）

铁律：
- 数据源=同花顺 REST（common.hithink_api），Key 只从凭据文件读，不进日志。
- 全部落盘 cache/hithink_*.json，不改任何现有缓存/核心模块；写回候选必须显式 --apply。
- 688/ST 按系统铁律过滤标注（excluded_688/excluded_st 计数，原始数据保留供校验）。
- 非交易日/数据未就绪返回空不报错（契约），打印提示。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / "cache"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.hithink_api import hithink_get, to_thscode  # noqa: E402
from common.json_io import load_json, save_json  # noqa: E402
# 规范时间戳（2026-10-14 审计 P1-⑦）：全部 hithink_*.json 的 generated_ts 改带 +08:00
# （原 `datetime.now().strftime("%Y-%m-%d %H:%M:%S")` = 空格无时区，是审计列出的 3 种形态之一）。
from common.market_time import iso_bj  # noqa: E402


def _log(msg: str) -> None:
    try:
        from common.logging_setup import get_logger

        get_logger("hithink_fetch").info(msg)
    except Exception:
        pass
    print(msg)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _norm_date(d: str) -> str:
    return d.strip().replace("-", "")  # 纯 YYYYMMDD


# ---------- 通用 ----------

def _filter_flags(items: list[dict], code_key: str = "code") -> tuple[list[dict], int, int]:
    """按系统铁律标注：688 显式剔除、ST 永久过滤。返回 (过滤后列表, excluded_688数, excluded_st数)。"""
    kept, n688, nst = [], 0, 0
    for it in items:
        c = str(it.get(code_key) or "")
        name = str(it.get("name") or "")
        is_st = bool(it.get("is_st"))
        if c.startswith(("688", "689")) or name.startswith(("ST", "*ST")):
            if c.startswith(("688", "689")):
                n688 += 1
            else:
                nst += 1
            continue
        kept.append(it)
    return kept, n688, nst


# ---------- P0-1 涨停池 ----------

def cmd_limitup(date: str | None, check: bool) -> int:
    d = date or _today()
    data = hithink_get("a-share/special-data/limit-up-pool",
                       params={"size": 200, "sort_field": "limit_up_time", "sort_dir": "asc"})
    if not data:
        print(f"[hithink] ❌ 涨停池拉取失败/无数据（{d}，非交易日可能返回空）")
        return 1
    items = data.get("item") or []
    norm = []
    for it in items:
        c = str(it.get("ticker") or "").zfill(6) if str(it.get("ticker") or "").isdigit() else ""
        if not c and it.get("thscode"):
            c = str(it["thscode"]).split(".")[0]
        norm.append({
            "code": c,
            "name": it.get("name", ""),
            "boards": int(it.get("continue_day_cnt") or 0),
            "fbt": str(it.get("limit_up_time") or ""),
            "seal_yi": round(float(it.get("seal_money") or 0) / 1e8, 2),
            "pct": round(float(it.get("price_change_ratio_pct") or 0), 2),
            "reason": it.get("limit_up_reason") or "",
            "is_st": bool(it.get("is_st")),
        })
    kept, n688, nst = _filter_flags(norm)
    out = {
        "source": "hithink", "date": d, "generated_ts": iso_bj(),
        "total": len(items), "zt_count": len(kept),
        "excluded_688": n688, "excluded_st": nst,
        "items": kept, "items_raw": items,
    }
    p = CACHE / f"hithink_zt_{_norm_date(d)}.json"
    save_json(p, out)
    _log(f"[hithink] ✅ 涨停池 {d}: 同花顺{len(items)}只(过滤688×{n688}/ST×{nst}后{len(kept)}) → {p}")
    # 题材归因 Top 连板
    lb = sorted([x for x in kept if x["boards"] >= 2], key=lambda x: -x["boards"])
    if lb:
        _log(f"[hithink]   连板梯队: " + " | ".join(f"{x['name']}{x['boards']}板" for x in lb[:8]))
    for x in sorted(kept, key=lambda v: -v["boards"])[:5]:
        _log(f"[hithink]   {x['name']}({x['code']}) {x['boards']}板 {x['fbt']} 封单{x['seal_yi']}亿 因:{(x['reason'] or '-')[:24]}")
    if check:
        _check_zt(d, kept)
    return 0


def _check_zt(d: str, hithink_kept: list[dict]) -> None:
    snap = load_json(CACHE / "yesterday_zt.json")
    biying = snap.get("zt_list") or []
    if not biying:
        _log(f"[hithink] ⏭ 必盈 yesterday_zt.json 缺失（date={snap.get('date')}），跳过对比")
        return
    if str(snap.get("date")) != d:
        _log(f"[hithink] ⚠️ 必盈快照日期 {snap.get('date')} ≠ 查询日 {d}，对比仅供参考")
    bset = {x["code"] for x in biying}
    hset = {x["code"] for x in hithink_kept}
    only_b = sorted(bset - hset)
    only_h = sorted(hset - bset)
    both = len(bset & hset)
    _log(f"[hithink] ⚖️ 涨停池交叉校验 {d}: 必盈{len(bset)} vs 同花顺{len(hset)} → 重合{both}")
    if only_b:
        _log(f"[hithink]   ⚠️ 仅必盈有({len(only_b)}): {','.join(only_b[:12])}")
    if only_h:
        _log(f"[hithink]   ⚠️ 仅同花顺有({len(only_h)}): {','.join(only_h[:12])}")
    # reason 注入：对重合票给同花顺题材归因（必盈缺 reason 字段）
    reason_map = {x["code"]: x["reason"] for x in hithink_kept if x.get("reason")}
    with_reason = [x for x in biying if x["code"] in reason_map]
    _log(f"[hithink]   💡 可给必盈注入 reason 的票: {len(with_reason)}/{len(biying)}")
    # 落盘差异报告
    diff = {"date": d, "biying_count": len(bset), "hithink_count": len(hset),
            "both": both, "only_biying": only_b, "only_hithink": only_h}
    save_json(CACHE / f"hithink_zt_diff_{_norm_date(d)}.json", diff)


# ---------- P0-2 龙虎榜 ----------

def cmd_lhb(date: str | None, check: bool) -> int:
    d = date or _today()
    data = hithink_get("a-share/special-data/dragon-tiger-list", params={"board_type": "all", "date": d})
    if not data or not data.get("stock_items"):
        # 降级：当日未就绪/非交易日 → 省略 date 取最新可用交易日（契约语义）
        data = hithink_get("a-share/special-data/dragon-tiger-list", params={"board_type": "all"})
        if not data or not data.get("stock_items"):
            print("[hithink] ❌ 龙虎榜拉取失败/无数据")
            return 1
        d = str(data.get("trade_date") or d)
    trade_date = data.get("trade_date") or d
    stocks = data.get("stock_items") or []
    # 游资榜 board_type=all 时 hot_money_items 实测为空 → 单独拉 hot_money 榜合并（2026-08-26 实证）
    hm_data = hithink_get("a-share/special-data/dragon-tiger-list",
                          params={"board_type": "hot_money", "date": trade_date})
    hot = (hm_data or {}).get("hot_money_items") or []
    # 机构净额 Top5
    org_top = sorted([s for s in stocks if s.get("org_net_value")],
                     key=lambda s: -float(s.get("org_net_value") or 0))[:5]
    # 游资买入 Top5
    hm_top = sorted([h for h in hot if h.get("buying")],
                    key=lambda h: -float(h.get("buying") or 0))[:5]
    org_rows = [{"code": str(s.get("ticker") or "").split(".")[0], "name": s.get("name"),
                 "org_net_yi": round(float(s.get("org_net_value") or 0) / 1e8, 3),
                 "net_yi": round(float(s.get("net_value") or 0) / 1e8, 3),
                 "concepts": (s.get("concept_list") or [])[:3]} for s in org_top]
    hm_rows = [{"seat": h.get("name"), "buy_yi": round(float(h.get("buying") or 0) / 1e8, 3),
                "stocks": [{"code": str(r.get("ticker") or "").split(".")[0], "name": r.get("name")}
                           for r in (h.get("rows") or [])[:4]]} for h in hm_top]
    out = {
        "source": "hithink", "date": trade_date, "generated_ts": iso_bj(),
        "stock_count": data.get("stock_count") or len(stocks),
        "org_top5": org_rows, "hotmoney_top5": hm_rows, "stocks": stocks, "hotmoney": hot,
    }
    p = CACHE / f"hithink_lhb_{_norm_date(trade_date)}.json"
    save_json(p, out)
    _log(f"[hithink] ✅ 龙虎榜 {trade_date}: 上榜{data.get('stock_count')}只 → {p}")
    _log(f"[hithink]   机构净额Top5: " + " | ".join(f"{r['name']}{r['org_net_yi']}亿" for r in org_rows))
    _log(f"[hithink]   游资买入Top5: " + " | ".join(f"{r['seat']}{r['buy_yi']}亿" for r in hm_rows))
    if check:
        _check_lhb(trade_date, org_rows, hm_rows)
    return 0


def _check_lhb(d: str, org_top5: list[dict], hm_top5: list[dict]) -> None:
    east = load_json(CACHE / f"lhb_{_norm_date(d)}.json")
    if not east:
        _log(f"[hithink] ⏭ 东财 lhb_{_norm_date(d)}.json 缺失，跳过对比")
        return
    e_org = {x["code"] for x in east.get("institution") or []}
    e_hm = {x["seat"] for x in east.get("hotmoney") or []}
    h_org = {x["code"] for x in org_top5}
    h_hm = {x["seat"] for x in hm_top5}
    _log(f"[hithink] ⚖️ 龙虎榜交叉校验 {d}: 机构 东财{len(e_org)}只 vs 同花顺Top5 {len(h_org & e_org)}只重合")
    _log(f"[hithink]   游资席位 东财{len(e_hm)}家 vs 同花顺Top5 {len(h_hm & e_hm)}家重合")
    if h_org - e_org:
        _log(f"[hithink]   ⚠️ 同花顺机构Top5含东财未上榜: {','.join(sorted(h_org - e_org))}")


# ---------- P0-3 连板天梯 ----------

def cmd_ladder(check: bool) -> int:
    data = hithink_get("a-share/special-data/limit-up-ladder")
    if not data:
        print("[hithink] ❌ 连板天梯拉取失败")
        return 1
    window = data.get("window") or {}
    items = data.get("item") or []
    p = CACHE / "hithink_ladder.json"
    save_json(p, {"source": "hithink", "generated_ts": iso_bj(),
                  "window": window, "items": items})
    _log(f"[hithink] ✅ 连板天梯: 窗口{window.get('length')}日 共{len(items)}日 → {p}")
    # items 实测倒序（最新在前）——按 date 取最近交易日，兼容任意排序（2026-08-26 实证）
    last = max(items, key=lambda x: str(x.get("date") or "")) if items else {}
    boards = last.get("boards") or {}
    for k in ("seven_over", "six_board", "five_board", "four_board", "three_board", "two_board"):
        v = boards.get(k) or []
        if v:
            _log(f"[hithink]   {last.get('date')} {k}: " + " | ".join(
                f"{x.get('name')}{x.get('board_num')}板(sign={x.get('sign_level')})" for x in v[:4]))
    if check:
        _check_ladder(last)
    return 0


def _check_ladder(last: dict) -> None:
    fb = load_json(CACHE / "feedback_matrix.json")
    if not fb or fb.get("error"):
        _log("[hithink] ⏭ feedback_matrix.json 无有效数据，跳过最高板对比")
        return
    boards = (last or {}).get("boards") or {}
    hx_max = 0
    for v in boards.values():
        for x in v:
            hx_max = max(hx_max, int(x.get("board_num") or 0))
    now_max = int(fb.get("now_max_boards") or 0)
    sign = "✅一致" if hx_max == now_max else "⚠️不一致"
    _log(f"[hithink] ⚖️ 最高板趋势校验 {last.get('date')}: 同花顺天梯{hx_max}板 vs 盘中情绪{now_max}板 → {sign}")


# ---------- P1-1 集合竞价 ----------

def cmd_auction(date: str | None, verify_aux: bool) -> int:
    d = date or _today()
    data = hithink_get("a-share/auction/short-term-benchmark", params={"date": d})
    if not data:
        print(f"[hithink] ❌ 竞价风向标拉取失败（{d}，非交易日可能无数据）")
        return 1
    items = data.get("item") or []
    p = CACHE / f"hithink_auction_{_norm_date(d)}.json"
    save_json(p, {"source": "hithink", "date": d, "generated_ts": iso_bj(),
                  "benchmark": items})
    _log(f"[hithink] ✅ 竞价风向标 {d}: {len(items)}条 → {p}")
    for x in items[:10]:
        _log(f"[hithink]   {x.get('name')} {x.get('thscode')} 竞价{x.get('auction_pct')}% tags={x.get('tags')}")
    if verify_aux:
        _auction_verify_aux(d, items)
    return 0


def _auction_verify_aux(d: str, benchmark: list[dict]) -> None:
    """只读辅助：把同花顺竞价数据与财富密码 verify 竞价缓存对齐，打印可参考的强弱标注（不写 verify）。
    双源：①short-term-benchmark 风向标（tags 服务端打标）②auction/snapshot 逐票竞价快照
    （竞价量比/未匹配量=封单强度/相对昨日量比——喂「竞价超预期判定」的核心字段）。
    """
    qp = ROOT / "wealth_code" / "cache" / f"auction_quotes-{d}.json"
    if not qp.exists():
        _log(f"[hithink] ⏭ verify 竞价缓存缺失（{qp}），--verify-aux 跳过")
        return
    quotes = load_json(qp) or {}
    codes = list(quotes.keys())
    bench_map = {str(x.get("thscode") or "").split(".")[0]: x for x in benchmark}
    hits = [c for c in codes if c in bench_map]
    _log(f"[hithink] 💡 verify 辅助: 持仓/候选竞价缓存 {len(codes)} 只中 {len(hits)} 只命中同花顺风向标")
    # 逐票竞价快照（stage=final 终态；非竞价时段返回当日终态，空则诚实标注不补零）
    snap_map: dict[str, dict] = {}
    for i in range(0, len(codes), 100):
        batch = [to_thscode(c) for c in codes[i:i + 100]]
        data = hithink_get("a-share/auction/snapshot", params={"thscodes": ",".join(batch), "stage": "final"})
        for it in (data or {}).get("item") or []:
            c = str(it.get("thscode") or "").split(".")[0]
            snap_map[c] = {k: it.get(k) for k in (
                "auction_pct", "auction_amount", "auction_unmatched",
                "auction_yesterday_ratio_pct", "auction_volume_ratio", "auction_turnover_pct")}
    _log(f"[hithink] 💡 竞价快照(snapshot) 返回 {len(snap_map)}/{len(codes)} 只")
    for code in codes[:8]:
        b = bench_map.get(code)
        s = snap_map.get(code) or {}
        bits = []
        if s.get("auction_pct") is not None:
            bits.append(f"竞价{s.get('auction_pct')}%")
        if s.get("auction_volume_ratio") is not None:
            bits.append(f"量比{s.get('auction_volume_ratio')}")
        if s.get("auction_unmatched") is not None:
            bits.append(f"未匹配{s.get('auction_unmatched')}")
        if s.get("auction_yesterday_ratio_pct") is not None:
            bits.append(f"昨量比{s.get('auction_yesterday_ratio_pct')}%")
        tag = f" tags={b.get('tags')}" if b else ""
        _log(f"[hithink]   {code} {b.get('name') if b else ''} {' | '.join(bits) or '(snapshot未返回)'}{tag}")


# ---------- P1-2 异动原因 ----------

def cmd_anomaly(date: str | None) -> int:
    d = date or _today()
    data = hithink_get("a-share/special-data/anomaly-analysis-list")
    if not data:
        print(f"[hithink] ❌ 异动原因拉取失败/无数据（{d}，仅当日快照）")
        return 1
    items = data.get("item") or []
    norm = []
    for it in items:
        c = str(it.get("thscode") or "").split(".")[0]
        norm.append({"code": c, "name": it.get("stock_name", ""), "tag": it.get("tag_name", ""),
                     "content": (it.get("analysis_content") or ""), "keywords": it.get("keyword_list") or []})
    kept, n688, nst = _filter_flags(norm)
    from collections import Counter

    stats = dict(Counter(x["tag"] for x in kept))
    p = CACHE / f"hithink_anomaly_{_norm_date(d)}.json"
    save_json(p, {"source": "hithink", "date": d, "generated_ts": iso_bj(),
                  "total": len(items), "filtered": len(kept), "excluded_688": n688, "excluded_st": nst,
                  "stats": stats, "items": kept})
    _log(f"[hithink] ✅ 异动原因 {d}: 总{len(items)}条(过滤688×{n688}/ST×{nst}后{len(kept)}) 分布={stats} → {p}")
    return 0


# ---------- P1-3 估值快照 ----------

def cmd_valuation(codes: list[str], enrich: bool, apply: bool) -> int:
    d = _today()
    if enrich:
        cand_path = ROOT / "wealth_code" / "cache" / f"candidates-{d}.json"
        if not cand_path.exists():
            print(f"[hithink] ❌ 候选文件缺失: {cand_path}（今日 eod 未跑？）")
            return 1
        cand = load_json(cand_path)
        code_set: set[str] = set()
        for key in ("relay", "watch", "early_ambush"):
            for it in (cand.get(key) or []):
                c = str(it.get("code") or "")
                if c.isdigit():
                    code_set.add(c)
        if not code_set:
            print("[hithink] ⏭ 候选无 code 字段，跳过 enrich")
            return 1
        _log(f"[hithink] 📥 候选估值 enrich: 从 relay/watch/early_ambush 收集 {len(code_set)} 只")
    else:
        if not codes:
            print("[hithink] ❌ 需 --codes 或 --enrich-candidates")
            return 1
        code_set = set(codes)
    ths = [to_thscode(c) for c in sorted(code_set)]
    val_map: dict[str, dict] = {}
    for i in range(0, len(ths), 100):  # token 上限 100
        batch = ths[i:i + 100]
        data = hithink_get("a-share/valuations/snapshot", params={"thscodes": ",".join(batch)})
        if not data:
            _log(f"[hithink] ⚠️ 估值批次{i//100}失败")
            continue
        for it in (data.get("item") or []):
            c = str(it.get("thscode") or "").split(".")[0]
            # 真实字段（2026-08-26 实测）：pe_ttm/pe_mrq/pb_mrq/ps_ttm/pcf_ttm
            val_map[c] = {k: it.get(m) for k, m in
                          (("pe", "pe_ttm"), ("pb", "pb_mrq"), ("ps", "ps_ttm"), ("pcf", "pcf_ttm"))}
    p = CACHE / f"hithink_valuation_{_norm_date(d)}.json"
    save_json(p, {"source": "hithink", "date": d, "generated_ts": iso_bj(),
                  "requested": len(code_set), "got": len(val_map), "values": val_map})
    _log(f"[hithink] ✅ 估值快照 {d}: 请求{len(code_set)} 返回{len(val_map)} → {p}")
    for c in sorted(val_map)[:8]:
        v = val_map[c]
        _log(f"[hithink]   {c}: PE={v.get('pe')} PB={v.get('pb')} PS={v.get('ps')}")
    if enrich:
        _enrich_candidates(cand, val_map, apply)
    return 0


def _enrich_candidates(cand: dict, val_map: dict, apply: bool) -> None:
    injected = 0
    for key in ("relay", "watch", "early_ambush"):
        for it in (cand.get(key) or []):
            c = str(it.get("code") or "")
            if c in val_map:
                it["hithink_val"] = val_map[c]
                injected += 1
    _log(f"[hithink] 💡 候选注入 hithink_val: {injected} 只")
    if apply:
        cp = ROOT / "wealth_code" / "cache" / f"candidates-{cand.get('date') or _today()}.json"
        save_json(cp, cand)
        _log(f"[hithink] ✅ 已写回 {cp}（--apply）")
    else:
        _log("[hithink] ⏭ 未写回（dry-run，加 --apply 才写回候选）")


# ---------- P1-4 全市场导出 ----------

def cmd_dump(apply: bool, *, recent: bool = False) -> int:
    """下载同花顺日K导出。

    - 默认全量 daily-k 只保留兼容入口，适合明确要做长历史研究时手动调用。
    - ``recent=True`` 使用 daily-k-10d，供最近 20 交易日的轻量日频底库日常增量同步。
    本函数只下载原始 Parquet；导入/裁剪由 market_daily_store.py 显式执行，避免把
    下载动作悄悄接入候选、推送或决策链。
    """
    endpoint = ("dump/market-dumps/daily-k-10d/download-url" if recent
                else "dump/market-dumps/daily-k/download-url")
    label = "近10交易日日K" if recent else "全市场10年日K"
    data = hithink_get(endpoint)
    if not data:
        print(f"[hithink] ❌ {label} 下载链接拉取失败")
        return 1
    url = data.get("presigned_url") or data.get("url") or data.get("download_url") or ""  # 实测 presigned_url
    if not url:
        _log(f"[hithink] ⚠️ 返回结构未知: {json.dumps(data, ensure_ascii=False)[:200]}")
        return 1
    _log(f"[hithink] ✅ {label} Parquet 预签名URL已获取（有效期约5分钟）")
    if not apply:
        target_name = "daily_k_10d.parquet" if recent else "daily_k_full.parquet"
        _log(f"[hithink] ⏭ dry-run：未下载（加 --apply 下载到 cache/hithink_dump/{target_name}）")
        return 0
    import urllib.request

    out_dir = CACHE / "hithink_dump"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ("daily_k_10d.parquet" if recent else "daily_k_full.parquet")
    _log("[hithink] 📥 下载中（约几分钟，可后台跑）...")
    try:
        urllib.request.urlretrieve(url, target)  # noqa: S310（官方预签名URL）
        _log(f"[hithink] ✅ 已下载 → {target} ({target.stat().st_size / 1e6:.1f} MB)")
    except Exception as e:
        _log(f"[hithink] ❌ 下载失败: {e}")
        return 1
    return 0


# ---------- 入口 ----------

def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]
    rest = args[1:]
    date = None
    if "--date" in rest:
        date = rest[rest.index("--date") + 1]
    check = "--check" in rest
    apply = "--apply" in rest

    if cmd == "limitup":
        return cmd_limitup(date, check)
    if cmd == "lhb":
        return cmd_lhb(date, check)
    if cmd == "ladder":
        return cmd_ladder(check)
    if cmd == "auction":
        return cmd_auction(date, "--verify-aux" in rest)
    if cmd == "anomaly":
        return cmd_anomaly(date)
    if cmd == "valuation":
        codes = []
        if "--codes" in rest:
            codes = rest[rest.index("--codes") + 1].split(",")
        return cmd_valuation(codes, "--enrich-candidates" in rest, apply)
    if cmd == "dump":
        return cmd_dump(apply, recent="--recent" in rest)
    if cmd == "all":
        rc = 0
        rc |= cmd_limitup(date, check)
        rc |= cmd_lhb(date, check)
        rc |= cmd_ladder(check)
        rc |= cmd_auction(date, False)
        rc |= cmd_anomaly(date)
        return rc
    print(f"[hithink] ❌ 未知子命令: {cmd}\n{__doc__}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
