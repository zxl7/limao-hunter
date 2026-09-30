#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财富密码 · 狸猫猎手接入 CLI

用法：
  # 盘后：涨停池 -> 龙头分 -> 接力/观察池候选
  python scripts/run_wealth_code.py eod

  # 次日验证（需真实竞价快照文件）：竞价验证 -> 净值 + 指标
  python scripts/run_wealth_code.py verify --quotes /path/auction_quotes.json

  # 次日验证（dry-run，无实时数据，仅验证算法与口径）：自动合成竞价/收盘价
  python scripts/run_wealth_code.py verify --synthesize

  # 查看最新净值与指标
  python scripts/run_wealth_code.py status
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BJ_TZ = timezone(timedelta(hours=8))

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    """从 wealth_code/.env 注入 BIYING_TOKEN 等密钥到环境（本地、不提交）。"""
    p = ROOT / ".env"
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_dotenv()

from pipelines import pipeline_eod, pipeline_verify, build_cards  # noqa: E402
from pipelines.pipeline_verify import _next_day  # noqa: E402
from adapters import input_auction  # noqa: E402
from core import nav_ledger, strategy_metrics  # noqa: E402


def _load_narratives(path: str | None):
    """
    读取 agent（大模型）写好的拟人点评 JSON：
        {"narratives": {"603758": "个股点评...", ...}, "global_narrative": "全局点评..."}
    无文件/解析失败 → 返回 ({}, None)，保证推送不阻塞（卡片照常纯数据）。
    """
    if not path:
        return {}, None
    p = Path(path)
    if not p.exists():
        sys.stderr.write(f"[narr] 旁白文件不存在: {path}，跳过\n")
        return {}, None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[narr] 旁白文件解析失败: {e}，跳过\n")
        return {}, None
    narr = data.get("narratives") or {}
    g = data.get("global_narrative")
    return narr, g


def _archive_decision(recommendation_date: str) -> None:
    """冻结候选与执行证据；归档失败只告警，不能反向阻断已完成的选股/竞价结算。

    ⚠️ 2026-09-29 修：显式把**项目根**塞进子进程 PYTHONPATH。
    本函数以 subprocess 调 `scripts/archive_decision.py`，脚本模式下项目根不入 sys.path →
    该脚本内 `from common.trading_calendar import ...` 抛 ModuleNotFoundError →
    决策归档自 2026-09-11 起每个交易日静默丢失（调用方只打一行告警、不阻断）。
    脚本侧已加 sys.path 兜底（第一层）；此处再显式传 env（第二层，防同类复发）。
    """
    if not recommendation_date:
        return
    try:
        archive = ROOT.parent / "scripts" / "archive_decision.py"
        env = dict(os.environ)
        _root = str(ROOT.parent)
        env["PYTHONPATH"] = _root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        result = subprocess.run([sys.executable, str(archive), "--date", recommendation_date],
                                capture_output=True, text=True, timeout=60, env=env)
        if result.returncode:
            print(f"[ARCHIVE] 冻结证据失败: {result.stderr.strip() or result.stdout.strip()}")
        else:
            print(f"[ARCHIVE] 冻结证据 OK: {result.stdout.strip().splitlines()[-1] if result.stdout.strip() else recommendation_date}")
    except Exception as exc:
        print(f"[ARCHIVE] 冻结证据跳过: {exc}")


def _load_ai_env(path: str | None):
    """
    读取 agent 写好的 AI 研判 JSON（喂进 spec 自有维度，不另立打分）：
        {"market_regime": "neutral", "ai_env_score": 6.0,
         "sector_priority": [...], "sector_avoid": [...],
         "global_narrative": "...", "notes": "..."}
    无文件/解析失败 → 返回 None（卡片照常纯数据，不阻塞）。
    """
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        sys.stderr.write(f"[ai_env] 文件不存在: {path}，跳过\n")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[ai_env] 解析失败: {e}，跳过\n")
        return None


def cmd_eod(args: argparse.Namespace) -> int:
    narr, g = _load_narratives(args.narratives)
    ai_env = _load_ai_env(args.ai_env)
    payload = pipeline_eod.run_eod(narratives=narr, global_narrative=g, ai_env=ai_env)
    print(f"[EOD] date={payload['date']} regime={payload['regime']} "
          f"relay={len(payload['relay'])} watch={len(payload['watch'])}")
    for it in payload["relay"][:RELAY_PREVIEW]:
        print(f"  接力#{it.get('relay_rank')} {it['name']}({it['code']}) "
              f"龙头分={it['score']} 题材={it['theme']} 预期={it['expectation']['expected_range']}")
    # 盘后收益结算：已收盘(>=15:00)时，用今日收盘价立即结算上一批次（trade_date=今日）的
    # next_day 收益，不必等次日 09:25（2026-08-12 新增；幂等，已结算自动跳过）
    if datetime.now(BJ_TZ).strftime("%H:%M") >= "15:00":
        fin = _finalize_prev_batch(closing=True)
        if fin:
            nav = fin["summary"].get("nav_latest")
            print(f"[EOD] 盘后结算上一批次: covered={fin['summary'].get('covered')} "
                  f"nav_latest={nav}")
            # 持仓自动清空（批次闭合→转净值账本历史）（2026-08-17）
            td = fin["summary"].get("trade_date10")
            if td:
                pipeline_verify.clear_holdings(td)
                print(f"[EOD] 持仓清空 holdings.json（batch trade_date={td} 已结算）")
        else:
            print("[EOD] 盘后结算: 无待结算批次（新系统首个交易日/已结算/缺快照）")
    # 2026-09-09 历史涨停基因预生成：给 relay+watch 候选补 cache/zt_genes/hist_{code}.json
    # （新浪日K自算·每票1次·磁盘缓存），供 build_eod_card 常驻渲染基因行 / verify 卡 / 复盘引用。
    # 失败静默降级（渲染层读不到档案=诚实省略基因行，不阻断推送）。
    _cand_codes = sorted({str(x.get("code"))
                          for x in (payload.get("relay") or []) + (payload.get("watch") or [])
                          if len(str(x.get("code") or "")) == 6})
    if _cand_codes:
        try:
            _zg = ROOT.parent / "scripts" / "zt_genes.py"
            _r = subprocess.run([sys.executable, str(_zg), "hist", "--codes", ",".join(_cand_codes)],
                                capture_output=True, text=True, timeout=240)
            print(f"[EOD] 候选历史基因预生成 {len(_cand_codes)} 只 (rc={_r.returncode})")
        except Exception as _e:
            print(f"[EOD] 候选历史基因预生成跳过: {_e}")
    if args.push:
        if (payload.get("relay") or []) or (payload.get("watch") or []):
            ok, out = build_cards.push_card_file(
                build_cards.build_eod_card(payload), webhook=args.webhook)
            print(f"[EOD] 推送盘后候选卡: {'✓' if ok else '✗'} {out}")
        else:
            print("[EOD] 无候选（涨停池可能为空/非交易日），跳过推送")
    _archive_decision(str(payload.get("date") or ""))
    # 2026-09-29 恢复：真实成绩可视化（净值曲线 + 每日明细 → wealth_code/data/performance.{md,html}）。
    # 背景：`perf_report.py` 在两次 git 基线之间从工作区丢失（`git log --all` 仅旧基线 645205a 有、
    # HEAD 已无），prompt 步骤8 路径失效、产物也从未落盘 → **成绩反馈回路断掉**。
    # 短线情绪迭代 = 「判断 → 结果 → 修正」闭环，反馈半边必须有据可查，故恢复脚本并挂进 eod。
    # 纯本地读账本 + metrics，失败静默降级（不阻断推送/结算/归档）。
    try:
        from pipelines.perf_report import run_perf_report
        _pf = run_perf_report()
        _wr = (_pf.get("winrate") or {}).get("win_rate")
        print(f"[EOD] 成绩视图已刷新: {_pf.get('trade_days')} 个交易日 · 净值 {_pf.get('latest_nav')} "
              f"· 累计 {float(_pf.get('total_return_pct') or 0):+.2f}% · 胜率 {_wr}%")
    except Exception as _e:
        print(f"[EOD] 成绩视图刷新跳过: {_e}")
    return 0


def _finalize_prev_batch(closing: bool = False) -> dict | None:
    """
    结算上一已交易完批次的 next_day 收益，写入净值账本与策略指标。

    两种模式（2026-08-12 扩展，原只支持次日 09:25 结算）：
    - closing=False（次日 09:25 竞价验证时）：选 trade_date 严格早于今日的最大批次，
      抓今日「昨收」(f60) = 其交易日收盘价 → 结算。
    - closing=True（当日盘后 eod 时）：选 trade_date <= 今日 且未结算的最大批次
      （即今日收盘的批次），抓今日「当前价」(p) = 今日收盘价 → 当日即可结算，
      不必等次日 09:25。

    流程：选批次 → 读其持久化的竞价快照 → 抓收盘价 → run_verify(带 next_close) →
    更新 nav + metrics。若已结算或缺快照/收盘价则跳过（幂等）。
    """
    files = sorted((ROOT / "cache").glob("candidates-*.json"))
    today = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    target = None  # (trade_date, path, cand)
    for f in files:
        try:
            cand = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        td = _next_day(str(cand.get("date") or ""))
        ok = (td <= today) if closing else (td < today)
        if ok and (target is None or td > target[0]):
            target = (td, f, cand)
    if not target:
        return None
    trade_date, _, cand = target
    nav_path = ROOT / "cache" / "account_nav_history.jsonl"
    if nav_path.exists():
        for row in nav_ledger._read_jsonl(nav_path):
            if str(row.get("trade_date")) == trade_date:
                if row.get("settled") is False:
                    break  # 早盘买入信号占位行（收益未结算）→ 允许收盘用真实收益覆盖
                return None  # 已结算，避免重复计入
    aq_path = ROOT / "cache" / f"auction_quotes-{trade_date}.json"
    if not aq_path.exists():
        return None
    quotes = input_auction.load_auction_quotes(aq_path)
    if not quotes:
        return None
    codes = [str(c.get("code")) for c in (cand.get("relay", []) + cand.get("watch", [])) if c.get("code")]
    if closing:
        # 盘后：今日「当前价」(p) = 该交易日(T)收盘价 → 当日即可结算
        fetched = input_auction.fetch_realtime_closes(codes)
        closes = {code: px for code, px in fetched.items() if px}
    else:
        # 次日 09:25 的「昨收」(f60) = 该交易日(T-1)的收盘价
        fetched = input_auction.fetch_realtime_auction(codes)
        closes = {code: q["prev_close"] for code, q in fetched.items() if q.get("prev_close")}
    if not closes:
        return None
    return pipeline_verify.run_verify(candidates=cand, quotes=quotes, next_close_map=closes)


def cmd_verify(args: argparse.Namespace) -> int:
    candidates_path = None
    if args.candidates:
        candidates_path = Path(args.candidates)

    if args.synthesize:
        # dry-run：先取候选，再合成竞价/收盘。只计算不落盘（persist=False），
        # 防止合成测试数据污染真实交易战绩 cache（2026-08-12 修复）。
        cand = pipeline_eod.run_eod() if args.recompute else _load_latest_candidates()
        quotes, next_close = input_auction.synthesize_quotes_for_test(
            cand.get("relay", []) + cand.get("watch", [])
        )
        res = pipeline_verify.run_verify(
            candidates=cand, quotes=quotes, next_close_map=next_close, out_nav=False, persist=False
        )
    elif args.fetch:
        # 真实竞价接入：抓最新候选的 09:25 实时报价
        cand = _load_latest_candidates()
        pool_codes = [str(c.get("code")) for c in (cand.get("relay", []) + cand.get("watch", [])) if c.get("code")]
        # 同步冻结完整昨涨停池的 09:25 报价，用于判断红开比例、连板红开比例和量能。
        # ⛔ 不可只抓接力/观察池：筛选样本天然更强，会虚高市场反馈。
        full_zt_codes: list[str] = []
        try:
            p = ROOT.parent / "cache" / "zt_history" / f"zt_{cand.get('date')}.json"
            rows = json.loads(p.read_text(encoding="utf-8")).get("zt_list") or []
            full_zt_codes = [str(r.get("code") or "").strip() for r in rows if isinstance(r, dict)]
        except Exception as _e:
            print(f"[VERIFY] 昨日完整涨停池读取失败（{_e}）——不放宽空仓名额")
        codes = list(dict.fromkeys(pool_codes + [c for c in full_zt_codes if len(c) == 6]))
        quotes = input_auction.fetch_realtime_auction(codes)
        # 板块竞价承接力（2026-08-24 用户「孤军高开·板块无承接被砸」思想）：
        # 抓同板块成员（昨日涨停池同 sector，eod 已回填 sector_mates）竞价 →
        # compute_support 算承接力 → signal_eval 孤军高开降级观察。
        # 老候选文件无 sector_mates 时优雅降级：跳过（sector_support_map 空，不裁决）。
        sector_support_map: dict[str, dict[str, Any]] = {}
        try:
            from core import sector_support
            all_cands = list(cand.get("relay", [])) + list(cand.get("watch", []))
            mates_codes = sector_support.collect_mates_codes(all_cands)
            if mates_codes:
                mates_quotes = input_auction.fetch_realtime_auction(mates_codes)
                for c in all_cands:
                    code = str(c.get("code") or "").strip()
                    if not code:
                        continue
                    sector_support_map[code] = sector_support.compute_support(c, mates_quotes)
                print(f"[VERIFY] 板块承接力：同板块成员 {len(mates_codes)} 只（抓取 {len(mates_quotes)}）")
        except Exception as _e:
            print(f"[VERIFY] 板块承接力计算跳过（{_e}）——不影响竞价判定，仅缺🧲标注")
        trade_date = _next_day(str(cand.get("date") or ""))
        # 持久化今日竞价快照（供次日 09:25 结算上一交易日收益）
        aq_path = ROOT / "cache" / f"auction_quotes-{trade_date}.json"
        aq_path.write_text(json.dumps(quotes, ensure_ascii=False), encoding="utf-8")
        print(f"[VERIFY] 抓取竞价 {len(quotes)}/{len(codes)} 只（缺失 {len(codes)-len(quotes)}）")
        # 当日开盘信号（next_close 未知 → 收益 pending，净值次日结算）
        res = pipeline_verify.run_verify(candidates=cand, quotes=quotes, sector_support_map=sector_support_map)
        # 结算上一交易日的收益（用今日「昨收」= 其收盘）
        fin = _finalize_prev_batch()
        if fin:
            print(f"[VERIFY] 结算上一交易日 nav={fin['summary'].get('nav_latest')}")
    else:
        res = pipeline_verify.run_verify(
            candidates_path=candidates_path,
            quotes_path=Path(args.quotes) if args.quotes else None,
        )
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
    # 实盘竞价已落地后重新归档同一候选日，补齐竞价与 9:25 池内裁决冻结件。
    if not args.synthesize:
        _archive_decision(str(res["summary"].get("date10") or ""))
    # 持仓自动同步（2026-08-17）：真实买入运行写 holdings.json；合成/测试（--synthesize）不写
    if not args.synthesize:
        td = res["summary"].get("trade_date10") or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
        _h = pipeline_verify.sync_holdings_from_verify(res, td)
        print(f"[VERIFY] 同步持仓 holdings.json: {len(_h.get('holdings', []))} 只（updated={td}）")
    if args.narratives:
        _, g = _load_narratives(args.narratives)
        if g:
            res["global_narrative"] = g  # 竞价验证卡全局 🗣️ 由 agent 产出
            # ⏱️ 撰写日留痕（2026-09-18）：agent 是**当日开盘前**写的这份旁白（内容讲昨收盘面、
            #    操作讲今日），与「透传上一交易日盘后旁白」分属两种基准 → 显式标当日，
            #    渲染层据此判定**不做时点平移**（`build_cards._narr_basis_label` 日期一致 ⇒ 空标注、
            #    不平移）。⛔ 不写这个字段会被误当成上一交易日旁白而整体 +1 天平移。
            res["narrative_date"] = res["summary"].get("trade_date10") or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    if args.push:
        # ⛔ 竞价守卫（2026-08-19 用户发现 09:20 提前推无效卡）：
        #   1) 当前北京时间早于 09:25:30（竞价撮合定价前）→ 不推，只记录
        #   2) --fetch 模式竞价报价 0 只 → 数据未落地，不推
        now_bj = datetime.now(BJ_TZ)
        early = now_bj.hour * 100 + now_bj.minute < 9 * 100 + 26
        no_quote = bool(getattr(args, "fetch", False)) and not quotes
        if early:
            print(f"[VERIFY] ⛔ 竞价守卫：当前 {now_bj.strftime('%H:%M:%S')} 早于 09:26 竞价定价，跳过推送（落盘不推）")
            return 0
        if no_quote:
            print(f"[VERIFY] ⛔ 竞价守卫：竞价报价 0 只（{now_bj.strftime('%H:%M:%S')}），数据未落地，跳过推送")
            return 0
        ok, out = build_cards.push_card_file(build_cards.build_verify_card(res))
        print(f"[VERIFY] 推送竞价验证卡: {'✓' if ok else '✗'} {out}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    nav_path = ROOT / "cache" / "account_nav_history.jsonl"
    metrics_path = ROOT / "cache" / "account_strategy_metrics.json"
    print("=== 净值账本 (account_nav_history.jsonl) ===")
    if nav_path.exists():
        rows = nav_ledger._read_jsonl(nav_path)
        if rows:
            print(f"  记录数: {len(rows)}")
            print(f"  起始: {rows[0]['trade_date']} nav={rows[0].get('nav')}")
            print(f"  最新: {rows[-1]['trade_date']} nav={rows[-1]['nav']} "
                  f"avg_return={rows[-1]['avg_return_pct']}%")
        else:
            print("  （空）")
    else:
        print("  （无）")
    print("=== 策略指标 (account_strategy_metrics.json) ===")
    if metrics_path.exists():
        m = json.loads(metrics_path.read_text(encoding="utf-8"))
        recs = m.get("records") or []
        if recs:
            last = recs[-1]
            nd = last["metrics"]["next_day"]["tradable"]
            print(f"  最新推荐日: {last['recommendation_date']} 样本={last['sample_count']}")
            print(f"  尾盘(tradable): 覆盖={nd['covered']} 胜率={nd['win_rate']}% "
                  f"平均收益={nd['avg_return']}%")
        else:
            print("  （空）")
    else:
        print("  （无）")
    return 0


def _load_latest_candidates() -> dict:
    files = sorted((ROOT / "cache").glob("candidates-*.json"))
    if not files:
        return pipeline_eod.run_eod()
    return json.loads(files[-1].read_text(encoding="utf-8"))


RELAY_PREVIEW = 8


def main() -> int:
    ap = argparse.ArgumentParser(description="财富密码 · 狸猫猎手接入")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("eod", help="盘后生成接力/观察池候选")
    pe.add_argument("--push", action="store_true", help="构建并推送盘后候选卡")
    pe.add_argument("--ai-env", help="AI 研判 JSON 路径（喂进 spec 维度：market_regime/ai_env_score/sector_priority/avoid/global_narrative）")
    pe.add_argument("--narratives", help="agent 旁白 JSON 路径（{\"narratives\":{code:text}, \"global_narrative\":str}）")
    pe.add_argument("--webhook", help="推送 webhook 键名（config/settings.json），如 feishu_webhook_test")

    sub.add_parser("status", help="查看最新净值与指标")

    pv = sub.add_parser("verify", help="次日验证（竞价验证->净值+指标）")
    pv.add_argument("--quotes", help="竞价快照 JSON 路径")
    pv.add_argument("--candidates", help="候选 JSON 路径（默认取最新）")
    pv.add_argument("--synthesize", action="store_true", help="dry-run：合成竞价/收盘")
    pv.add_argument("--recompute", action="store_true", help="verify 前重跑 eod")
    pv.add_argument("--fetch", action="store_true", help="真实竞价接入：抓 09:25 实时报价")
    pv.add_argument("--push", action="store_true", help="构建并推送竞价验证卡")
    pv.add_argument("--narratives", help="agent 全局旁白 JSON 路径（{\"global_narrative\":str}）")

    args = ap.parse_args()
    if args.cmd == "eod":
        return cmd_eod(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    if args.cmd == "status":
        return cmd_status(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
