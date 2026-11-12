#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
次日验证流水线：竞价验证 → 构建结算 payload → 净值 + 指标记账

这是财富密码接入狸猫猎手的“真实交易结算”一端，把盘后候选跑成可统计的真实战绩：
1. 读盘后候选（cache/candidates-*.json）
2. 读次日 9:25 竞价快照（adapters/input_auction）
3. 逐票 signal_eval → open_check（super/expected/pending/reject）
4. 用隔日收盘价算 next_day 收益
5. 组装结算 payload → nav_ledger（净值曲线）+ strategy_metrics（胜率/收益）
6. 输出 cache/account_nav_history.jsonl + account_strategy_metrics.json

注意（口径诚实）：
- 只有 can_enter=True 且 next_day 有收盘价的样本才进入净值
- 高开>5% 自动转 pending（不直接买入）
- hold_2d/hold_3d 默认置 pending（不在主口径计算），不影响 next_day 主口径
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.signal_eval import evaluate_realtime_signal
from core import ai_caliber
from core import pool_call
from core import auction_state as _ast
from core import expected_gap as _expected_gap
from core.nav_ledger import sync_account_nav_ledger
from core.strategy_metrics import build_account_strategy_metrics, sync_account_strategy_metrics
from adapters.input_auction import load_auction_quotes
from core.market_open import build_market_open

# ⛔ `common/` 在**项目根**、不在 wealth_code/ 下；以脚本/CLI 方式运行时 cwd 不入 sys.path，
#    调用方只把 wealth_code 导进 PYTHONPATH 就会在此行抛 ModuleNotFoundError。
#    2026-10-01 实证：launchd 竞价卡整轮 import 期即崩（verify RC=1，当日无卡）。
#    与 core/ai_caliber.py、adapters/input_auction.py 同法：本模块自插项目根。
_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))
from common.trading_calendar import next_a_share_trading_day  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
# 生产缓存目录（导入期快照·⛔ 不随 monkeypatch 变化）——供测试期写穿守卫比对基准用。
_PROD_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
# 持仓展示文件 holdings.json 的权威位置：项目根 cache/（与 ma5_check.py 共用，
# 非 wealth_code/cache）。ROOT=wealth_code → ROOT.parent=狸猫猎手（项目根）。
HOLDINGS_PATH = ROOT.parent / "cache" / "holdings.json"
BJ_TZ = timezone(timedelta(hours=8))

# ── 早盘冻结守卫（2026-11-05「旁白/数据准备链路综合」）─────────────────────
# 平台 09:25 的 `verify --fetch` 只做数据准备，但该入口默认 persist=True：
# 若它在真实竞价报价落地前抢跑成功，会用残缺报价算出「今天买哪只」并冻结，
# 且 save_pool_call 幂等不覆盖 → 09:26 正式推卡时 load_pool_call 直接沿用错误快照。
# ⛔ 只挡「冻结结论」，不挡报价抓取 / 昨日结算 / 卡面计算；历史日期回放不挡。
#    回退：VERIFY_EARLY_FREEZE_GUARD=0。
VERIFY_FREEZE_NOT_BEFORE = (9, 25, 30)
ENABLE_VERIFY_EARLY_FREEZE_GUARD = os.environ.get("VERIFY_EARLY_FREEZE_GUARD", "1") != "0"


def early_freeze_blocked(trade_date: str, now: datetime | None = None) -> bool:
    """当日 09:25:30 之前不允许把池内裁决冻结落盘。"""
    if not ENABLE_VERIFY_EARLY_FREEZE_GUARD:
        return False
    n = now or datetime.now(BJ_TZ)
    if str(trade_date) != n.strftime("%Y-%m-%d"):
        return False                      # 历史/回放不挡
    return (n.hour, n.minute, n.second) < VERIFY_FREEZE_NOT_BEFORE


def pool_call_path(trade_date: str) -> Path:
    """9:25 池内裁决结论落盘路径（`wealth_code/cache/pool_call-<执行日>.json`）。

    ⛔ 为什么必须落盘冻结：结论要被**四处**同源消费——① 09:26 竞价卡渲染、
    ② 账户账本（`_finalize_prev_batch` 盘后重跑 run_verify）、③ AI 独立轨
    （`ai_ledger settle`）、④ 事后审计。若各处各算一次，竞价快照被覆盖
    （该文件当天会被后续运行重写，见 skill 铁律 15 注）就会出现「卡上买 A、账本买 B」。
    """
    return CACHE_DIR / f"pool_call-{trade_date}.json"


def load_pool_call(trade_date: str) -> dict[str, Any] | None:
    """读已冻结的 9:25 池内裁决结论；缺失/损坏 → None（调用方重算或回退旧口径）。"""
    if not trade_date:
        return None
    try:
        d = json.loads(pool_call_path(trade_date).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("views") else None
    except Exception:
        return None


def save_pool_call(trade_date: str, result: dict[str, Any]) -> None:
    """冻结 9:25 池内裁决结论（幂等：已存在则不覆盖，保证当日结论唯一）。

    ⛔ **测试期禁止写生产路径**（2026-09-21 血案防线·沿用 `scripts/zt_archive._guard_test_write`
    的模式）：本函数是「写盘」型公共入口，若单测直接调 `run_verify` 而未注入临时目录，
    夹具数据会被冻结进真实 `wealth_code/cache/pool_call-<date>.json` —— 09-21 实测：
    `test_hibernate_valve` / `test_ai_caliber_account` 两个用例把「甲票 / 示例A」写成了
    `pool_call-2026-09-14.json`、`pool_call-2026-09-18.json`，随后被 `load_pool_call`
    当成"当日已冻结快照"读回 → 后续所有判定跟着跑偏。守卫把「静默写穿」变成「当场报错」，
    强制调用方 monkeypatch `CACHE_DIR` 注入 tmp_path。
    """
    import os
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("POOL_CALL_ALLOW_TEST_WRITE"):
        # ⚠️ 比对基准必须是**导入期算出的生产目录**（`_PROD_CACHE_DIR`），⛔ 不能用
        #    `CACHE_DIR`——测试 monkeypatch 后两者会相等，守卫反而误杀正常隔离的用例。
        try:
            if pool_call_path(trade_date).resolve().parent == _PROD_CACHE_DIR.resolve():
                raise RuntimeError(
                    f"测试期禁止写生产缓存目录 {_PROD_CACHE_DIR} —— 请 monkeypatch "
                    f"`pipeline_verify.CACHE_DIR` 注入 tmp_path")
        except RuntimeError:
            raise
        except Exception:
            pass
    p = pool_call_path(trade_date)
    if p.exists():
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"trade_date": trade_date, **result},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as _e:                                  # noqa: BLE001
        print(f"[VERIFY] ⚠️ 池内裁决结论落盘失败（{_e}）——不影响本次判定")


def _attach_premium_board(pool_res: dict[str, Any], zt_rows: list[dict[str, Any]],
                          quotes: dict[str, Any], *, prev_date: str,
                          trade_date: str) -> None:
    """把「昨日连板 · 今晨竞价溢价榜」挂到 `pool_res["meta"]`（2026-11-04 用户）。

    ⛔ 纯**读数**：不参与名额裁断、不改 `participate`、不影响任何已冻结结论。
      因此**无论裁决是新算还是读回已冻结快照，都允许补算**（旧快照没有这个字段）。
    ⛔ 失败只打印，绝不打断裁决 —— 溢价榜看不到是小事，裁决丢掉是大事。
    """
    try:
        from core import auction_premium as _ap
        prem = _ap.build_premium_board(zt_rows, quotes,
                                       prev_date=prev_date, trade_date=trade_date)
        pool_res.setdefault("meta", {})["premium_board"] = prem
        print(f"[VERIFY] 📈 昨日连板竞价：{prem['count']} 只 → "
              f"溢价 {prem['premium_count']} / 折价 {prem['discount_count']}"
              f"（中位 {prem['median_gap']}%）")
    except Exception as e:  # noqa: BLE001
        print(f"[VERIFY] 昨日连板溢价榜跳过：{e}")


def _load_full_zt_rows(date10: str) -> list[dict[str, Any]]:
    """读取依据日的完整涨停池，个股事实优先来自统一底库。

    ``market_open`` 只需要代码、板数和成交额等竞价闸门字段；其中代码、
    收盘涨停和板数先读 ``stock_registry.stock_daily``，旧归档只补充底库
    尚未结构化的 ``cje_yi`` 等字段。底库冷启动/缺料时再回退旧归档，
    不改变原有结果形状，也不把缺料解释成「没有涨停」。
    """
    legacy_rows: list[dict[str, Any]] = []
    try:
        p = ROOT.parent / "cache" / "zt_history" / f"zt_{date10}.json"
        raw = json.loads(p.read_text(encoding="utf-8"))
        legacy_rows = [row for row in (raw.get("zt_list") or [])
                       if isinstance(row, dict)]
    except Exception:
        legacy_rows = []

    try:
        from adapters.stock_registry_reader import get_daily_facts, get_stock

        registry_rows = get_daily_facts(date10, date10, only_limit_up=True)
    except Exception:
        registry_rows = []
    if not registry_rows:
        return legacy_rows

    supplements = {
        str(row.get("code") or "").strip(): row
        for row in legacy_rows
        if str(row.get("code") or "").strip()
    }
    out: list[dict[str, Any]] = []
    for item in registry_rows:
        code = str(item.get("code") or "").strip()
        if len(code) != 6:
            continue
        # 底库是个股事实主源；只对尚未结构化的字段做显式旧归档补充。
        row = {
            "code": code,
            "name": item.get("name") or supplements.get(code, {}).get("name"),
            "boards": item.get("boards"),
            "pct": item.get("pct"),
            "price": item.get("price"),
        }
        old = supplements.get(code) or {}
        stock = get_stock(code) or {}
        row["name"] = stock.get("name") or old.get("name") or row["name"]
        for key in ("cje_yi", "cje", "fbt", "zbc", "hs", "seal", "seal_yi",
                    "lt", "lt_yi", "zsz", "zsz_yi", "sector", "industry",
                    "is_20cm"):
            if old.get(key) is not None:
                row[key] = old[key]
        out.append(row)
    return out or legacy_rows


def _load_plan(trade_date: str) -> dict[str, Any]:
    """读盘后**预案**（`狸猫猎手/cache/ai_view_<执行日>.json`·项目根 cache，非 wealth_code）。

    ⛔ **2026-11-06 起默认不调用**：用户「盘后预案全系统关闭」→ `pool_call.POOL_CALL_EOD_PLAN=0`
    时本函数不被执行（连文件都不读），名额改走 `DEFAULT_QUOTA`。只有显式
    `POOL_CALL_EOD_PLAN=1` 回退旧口径时才会走到这里。

    2026-09-21 语义变更：该文件的 `views[code].view` 不再是「买不买」的终态（旧口径），
    9:25 池内裁决只从这里读 `stance`（攻守基调·定当日可买只数）与 `views[code].bias`（预案档）。
    文件缺失 → {}（诚实降级：按 DEFAULT_QUOTA 走，不编造）。
    """
    if not trade_date:
        return {}
    try:
        p = ROOT.parent / "cache" / f"ai_view_{trade_date}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _inject_pool_views(ai_decisions: dict[str, dict] | None,
                       pool_res: dict[str, Any],
                       candidates: dict[str, Any] | None = None) -> dict[str, dict]:
    """把 9:25 池内裁决结论注入决策行（`view` 覆写为**今日实判**）。

    · 决策行原生 `view`（盘后预案的旧口径终态）保留为 `plan_view` 留痕；
    · 池内票若**没有**盘后决策行 → 补一条壳行（池＝当日可交易范围，9:25 才是拍板时点）；
    · 池外票不动（无 9:25 结论 → `decide()` 仍按原行 `view` 走，行为不变）。
    """
    out: dict[str, dict] = {str(k): dict(v or {}) for k, v in (ai_decisions or {}).items()}
    marks = pool_res.get("views") or {}
    for code, m in marks.items():
        r = out.get(code)
        if r is None:
            r = {"code": code, "name": m.get("name") or "", "source": "pool_call",
                 "reason": m.get("reason") or ""}
            out[code] = r
        if not r.get("plan_view"):
            r["plan_view"] = r.get("view")
        r["view"] = m.get("view")
        r["plan_bias"] = m.get("bias")
        r["pool_reason"] = m.get("reason")
        # 🔁 11-05：竞价复核翻案标记必须跟着 `view` 一起注入决策行，
        #    否则 ai_caliber.decide 仍会拿盘后 observe_only 身份把翻案票判回不买。
        for _k in ("rechecked_from_observe", "recheck_class",
                   "auction_recheck", "volume_band",
                   # 🎯 11-05 竞价三态：manual_only 必须跟 view 一起注入，
                   #    否则 decide() 可能把「人工二次机会」写成系统成交记录。
                   "auction_state", "auction_r", "auction_route",
                   "auction_state_reason", "manual_only",
                   # 🎯 11-05 纠偏：逐票质量预期开幅与相对预期偏离，随冻结件透传。
                   #    11-05 删 5%/红开后，20cm 标志与低开留痕也须随决策行进 decide。
                   "expected_gap_pct", "gap_vs_expected",
                   "is_20cm", "low_open_expected", "low_open_guard"):
            if m.get(_k) is not None:
                r[_k] = m.get(_k)
        r["line_is_core"] = bool(m.get("line_is_core"))
        r["line_core"] = m.get("line_core")
        r["follower_gate"] = bool(m.get("follower_gate"))
        r["linkage_gate"] = bool(m.get("linkage_gate"))
        r["linkage_discipline"] = m.get("linkage_discipline")
        # 🚧 身位（2026-10-21 92科比 口径）：line_tier = front/second/back；
        #    backrow_note = 买的是「隔壁」（前排断板或买不到）。卡面据此标注，不改裁决。
        r["line_tier"] = m.get("line_tier")
        r["backrow_note"] = m.get("backrow_note")
        # 🛡️ 前置防守闸门（2026-10-08 经验）：which / why / 只留痕的警示——卡面据此点名
        #    「竞价达标但没买」的原因（否则读者只能自己猜）。⛔ 理由正文仍由个股段
        #    `_ai_view_line` 渲染（pool_reason），本块不重复。
        r["defense_gate"] = m.get("defense_gate")
        r["defense_reason"] = (m.get("defense_detail") or {}).get("note") or None
        r["defense_warn"] = m.get("defense_warn")
        r["position_warn"] = m.get("position_warn")
        # 机械强龙融合留痕：盘后 bias 仍是原始预案，09:25 名额排序可由此复核。
        r["mechanical_leader"] = bool(m.get("mechanical_leader"))
        r["mechanical_leader_note"] = m.get("mechanical_leader_note")
        r["leader_role"] = m.get("leader_role")
        r["leader_evidence"] = m.get("leader_evidence")
        r["dragon_confirm"] = m.get("dragon_confirm")   # 🐉 龙头身份（P2·卡面只读）
        # 🔍 池外龙头校验（2026-10-21）：盘后建池时判定「这条线的龙头在不在池内」，
        #    ⛔ 不透传 → 卡面 `_outpool_badge` 永远读不到、后端判了也白判。
        r["line_tier_outpool"] = m.get("line_tier_outpool")
        r["line_tier_why"] = m.get("line_tier_why")
        r["line_top"] = m.get("line_top")
        r["line_tops"] = m.get("line_tops")
    # 池内票的预期区间/量能目标：优先用候选（最新），缺则用决策行（record 时补写的）
    if candidates:
        _by = {str(c.get("code")): c for c in
               list(candidates.get("relay") or []) + list(candidates.get("watch") or [])
               + list(candidates.get("low_pool") or [])}
        for code, m in marks.items():
            c = _by.get(code)
            if not isinstance(c, dict):
                continue
            exp = c.get("expectation") or {}
            r = out.get(code) or {}
            if isinstance(exp.get("expected_range"), (list, tuple)) and len(exp["expected_range"]) == 2:
                r.setdefault("expected_range", [float(exp["expected_range"][0]),
                                                float(exp["expected_range"][1])])
            need = exp.get("auction_amount_min_yi")
            if isinstance(need, (int, float)) and need > 0:
                r.setdefault("amount_need_yi", float(need))
            out[code] = r
    return out


def _next_day(date10: str) -> str:
    """
    次一A股交易日。统一走法定休市日历，长假不再仅靠跳周末猜测。
    """
    return next_a_share_trading_day(date10) or date10


def _build_record(cand: dict[str, Any], quote: dict[str, Any] | None, next_close: float | None, *, date10: str, regime: str | None = None, regime_trusted: bool = True, sector_support: dict[str, Any] | None = None, ai_decisions: dict[str, dict] | None = None, valve_code: str | None = None, pool_views: dict[str, dict] | None = None) -> dict[str, Any]:
    """
    构建单条结算 record（含 performance.open_check / next_day）。
    regime: 情绪体制（P1-6 冰点禁买硬开关·signal_eval 消费）。
    regime_trusted: 该 regime 是否**当日实时读数**（2026-11-09）。False = 沿用昨收标签，
      **只标注不禁买**——昨收冰点不该锁掉今早已经判出来的买点（详见 `_resolve_regime`）。
      signal_eval 侧仍然收到 regime（档位阈值照旧），是否降级 buy→hit 由本函数的闸门决定。
    sector_support: 板块竞价承接力（2026-08-24 用户思想·孤军高开门控）——
      由 cmd_verify 抓同板块成员竞价算好传入；signal_eval 据此将
      「孤军高开+板块0红开」的 buy 降级 pending（防被砸），卡片渲染🧲标注。
    ai_decisions: 该执行日的 AI 决策映射 {code: 决策行}（2026-09-14 用户「全量以 AI 为唯一口径」）。
      **非 None** → 启用 AI 口径：can_enter 由 AI 判定（participate ∧ 竞价≤自定上限），
      机械 signal_eval 结果保留为参考信号（mech_can_enter / decision_status 字段）。
      **None** → 该日无 AI 决策（缺失）→ 账户不交易，机械结果仅作展示与审计。
    valve_code: 保留旧调用签名；AI 决策为唯一入场授权，不再放行观察票。
    """
    signal = evaluate_realtime_signal(cand, quote, regime=regime, sector_support=sector_support)
    decision = signal.get("decision_status")
    sig_status = signal.get("signal_status")
    # 🎯 9:25 池内裁决留痕（2026-09-21）：该票的「昨日预案 → 今日实判」两个时点都要能上卡。
    _plan_row = (ai_decisions or {}).get(str(cand.get("code") or "").strip()) or {}

    # P1-6 冰点禁买·命中打标（2026-08-19 用户澄清）：regime=ice 时命中判定照算
    # （signal_eval 已正常产出 buy 档），但一律不进场——buy → hit（can_enter=False、
    # 不写持仓），卡片仍展示命中票+禁买标注；pending/reject/unavailable 保持原样。
    # ⏱️ `regime_trusted`（2026-11-09）：只有**当日实时读数**判出的 ice 才禁买；
    #    沿用昨收标签时不禁买（昨收冰点≠今天的冰点，用户「我要买入那一套逻辑」）。
    if regime == "ice" and regime_trusted and decision == "buy":
        decision = "hit"
        signal = {
            **signal,
            "decision_status": "hit",
            "decision_label": "冰点禁买·命中",
            "ice_ban": True,
            "note": (str(signal.get("note") or "").rstrip("。")
                     + f" ⛔冰点禁买（情绪≤2.0）：{sig_status}命中但一律不进场，空仓等待情绪修复。"),
        }

    if decision == "buy":
        can_enter = True
        open_status = sig_status  # super / expected
    elif decision == "hit":
        can_enter = False
        open_status = sig_status  # super / expected（命中档位保留，仅禁买不进场）
    elif decision == "pending":
        can_enter = False
        open_status = "pending"
    else:  # reject / unavailable
        can_enter = False
        open_status = sig_status if sig_status else "reject"

    # ── 🤖 AI 口径覆盖（2026-09-14 用户「全量以 AI 为唯一口径！」）──────────────
    # 成交集合与入场规则改由 AI 盘后决策决定（core/ai_caliber）：
    #   AI 判 participate ∧ 竞价涨幅 ≤ AI 自定 entry_max_gap ∧ 有竞价数据 → 成交
    # 机械 signal_eval 结果**保留为参考信号**（decision_status / mech_can_enter 照常写入，
    # 卡面「✅ 竞价达标·机械参考」段照常渲染），但不再决定买卖与净值战绩。
    # AI 决策缺失（该执行日无任何决策行）→ 回退机械口径 + caliber 标注（诚实降级，不静默空仓）。
    mech_can_enter = bool(can_enter)
    mech_decision = decision
    caliber = "mechanical"
    ai_meta: dict[str, Any] = {}
    if ai_caliber.AI_CALIBER_ACCOUNT:
        if ai_decisions is None:
            caliber = "ai_missing_no_trade"
            can_enter = False
            ai_meta = {"caliber": caliber,
                       "caliber_note": "该执行日无 AI 决策行 → AI 口径缺失，账户不交易；机械结果仅供参考"}
        else:
            caliber = "ai"
            _gap = signal.get("gap_pct")
            _gap = float(_gap) if isinstance(_gap, (int, float)) else None
            _code = str(cand.get("code") or "").strip()
            # hibernate 竞价逃生阀（2026-09-18 用户拍板 A）：当日唯一放行标的下，
            # AI 判「观察」也允许小仓试错（其余口径不变·同调 decide）。
            # 方案 A（2026-09-17）：把候选的**盘后预期区间**一并传入 → 账本执行
            # 「参与 ∧ ≤5% ∧ 落预期区间」三步裁决，与卡面动态结论同源（gap_zone）。
            can_enter, _ai_note, ai_meta = ai_caliber.decide(
                ai_decisions.get(_code), _gap,
                expected_range=(cand.get("expectation") or {}).get("expected_range"),
                # 量能门（2026-09-17）：红开但量能 < 目标×0.7 → 降级观察（无量高开不追）
                auction_amount_yi=signal.get("auction_amount_yi"),
                need_yi=signal.get("auction_amount_need_yi"))
            ai_meta["caliber"] = caliber
            ai_meta["ai_note"] = _ai_note
            ai_meta["caliber_changed"] = bool(can_enter != mech_can_enter)
            if regime == "ice" and can_enter:
                # 冰点禁买是账户硬闸：AI/池内裁决保留为复盘证据，但不能形成真实入场。
                can_enter = False
                ai_meta["ice_ban"] = True
                ai_meta["ice_warn"] = "冰点期AI预案命中，但账户硬闸禁止入场"
    # ⛔ 两池契约（2026-11-03 用户「观察仅作参照，不自动交易」）：
    #    `participation_eligibility=observe_only` 的票无论 AI/机械怎么判，都不得形成入场。
    #    ⛔ 无论这个标记来自哪个方向 —— 回暖解冻、机械回退、冻结消费都不解锁。
    from core.two_pool import observation_only, rechecked_entry
    _pool_mark = (pool_views or {}).get(str(cand.get("code") or "").strip())
    # 🔁 11-05：竞价复核翻案票以冻结件为准，盘后 observe_only 身份不再二次否决。
    if observation_only(cand) and not rechecked_entry(_pool_mark):
        can_enter = False
        mech_can_enter = False
        ai_meta.update(ai_view="watch", ai_note="板块观察参照，不参与交易")
    # 🎯 2026-11-05 用户「其他机会交给人工二次自己交易不做记录」：
    #    B/C 态是人工机会，⛔ 不进系统的成交/净值/战绩记录。
    #    翻案票（rechecked_entry）优先，⛔ 不得被分层标二次否决。
    if ((_pool_mark or {}).get("manual_only") is True
            and not rechecked_entry(_pool_mark)):
        can_enter = False
        mech_can_enter = False
        ai_meta.update(manual_only=True, ai_note="人工二次机会，系统不记录")
    # 隔日收益
    if can_enter and next_close is not None and quote is not None:
        auction_price = float(quote.get("auction_price") or 0.0)
        return_pct = round((next_close - auction_price) / auction_price * 100.0, 2) if auction_price > 0 else None
        next_day_status = "covered" if return_pct is not None else "pending"
    else:
        return_pct = None
        next_day_status = "skipped" if can_enter is False else "pending"

    # 组装验证记录（仅含竞价验证所需字段，不附加选股卡样式数据）
    _rec = {
        "date10": date10,
        "trade_date10": _next_day(date10),
        "code": cand.get("code"),
        "name": cand.get("name"),
        "participation_eligibility": cand.get("participation_eligibility"),
        "recheck_unlocked": bool(rechecked_entry(_pool_mark)),
        "bucket": cand.get("bucket"),
        "bucket_label": cand.get("bucket_label"),
        "relay_rank": int(cand.get("relay_rank") or 0),
        "watch_rank": int(cand.get("watch_rank") or 0),
        "relay_selection_mode": str(cand.get("relay_selection_mode") or "").strip(),
        "watch_group": str(cand.get("watch_group") or "").strip(),
        "boards": cand.get("boards"),
        "is_20cm": cand.get("is_20cm"),
        "score": cand.get("score"),
        "leader_factor_score": (cand.get("reason") or {}).get("ignition", {}).get("oneWordScore"),
        "main_line": cand.get("theme"),
        # 核心炒作题材双字段（2026-08-17 用户·verify 卡开仓决策显示）
        # eod 候选已回填 industry_sector(行业归属)+core_theme(当前炒作题材)，verify 卡渲染
        # _theme_dual_tag(r) 需要这两个字段——透传，缺省回退 theme（诚实降级）
        "industry_sector": cand.get("industry_sector") or cand.get("theme"),
        "core_theme": cand.get("core_theme"),
        "factor_hint": " ".join(cand.get("tags") or []),
        "expectation": cand.get("expectation") or {},
        # 🎯 11-05 删 5%/红开：逐票质量预期（卡面三档判读与 ai_caliber.decide 同源）。
        #    优先冻结件终裁值；缺冻结件时从候选昨日封板质量现算，⛔ 不编造。
        "expected_gap_pct": ((_pool_mark or {}).get("expected_gap_pct")
                             if (_pool_mark or {}).get("expected_gap_pct") is not None
                             else _expected_gap.expected_gap_pct(cand)),
        "narrative": cand.get("narrative"),
        # 板块竞价承接力透传（2026-08-24）：供 verify 卡 🧲板块承接 标注
        "sector_support": sector_support,
        # 题材板块效应透传（2026-08-25 用户「孤军判定要全局化·结合题材板块效应」）：
        # eod 候选已算 theme_name/theme_mates_cnt/theme_has_sector_effect（行业→题材聚合），
        # verify 卡 _sector_support_tag 用它渲染「🧲题材板块效应·N家涨停」替代孤军误判。
        "theme_name": cand.get("theme_name"),
        "theme_mates_cnt": cand.get("theme_mates_cnt"),
        "theme_has_sector_effect": cand.get("theme_has_sector_effect"),
        "theme_is_lone": cand.get("theme_is_lone"),
        # 盘后完整涨停池冻结的题材联动事实；卡面只渲染白话，不展示内部 schema/计数冲突字段。
        "linkage_facts": cand.get("linkage_facts"),
        "linkage_discipline": signal.get("linkage_discipline") or cand.get("linkage_discipline"),
        "performance": {
            "open_check": {
                "can_enter": can_enter,
                "status": open_status,
                "decision_status": decision,
                # 🤖 口径留痕（2026-09-14 用户「全量以 AI 为唯一口径」）：
                # caliber = ai / mechanical / mechanical_fallback；mech_can_enter = 机械口径结论
                # （卡面「✅ 竞价达标·机械参考」段按此筛选，与账本口径解耦）。
                "caliber": caliber,
                "mech_can_enter": mech_can_enter,
                "mech_decision_status": mech_decision,
                "caliber_note": ai_meta.get("caliber_note"),
                "ai_view": ai_meta.get("ai_view"),
                "ai_gap_limit": ai_meta.get("ai_gap_limit"),
                # AI 自定上限（仅留痕）：2026-09-14 起**不参与裁决**，生效值恒为用户 5% 硬线
                "ai_declared_limit": ai_meta.get("ai_declared_limit"),
                # 预期区间门判定（2026-09-17「A 计划」）：below/ok/above/unknown（留痕·供审计）
                "zone": ai_meta.get("zone"),
                # hibernate 竞价逃生阀（2026-09-18 用户拍板 A）：True = 该票为当日唯一
                # 放行标的（AI 判观察仍小仓试错）；meta.via == "hibernate_valve" 同义。
                "hibernate_valve": bool(ai_meta.get("hibernate_valve")),
                "ai_note": ai_meta.get("ai_note"),
                "caliber_changed": ai_meta.get("caliber_changed"),
                # 冰点期即使 AI 预案命中，也必须保留硬闸与冲突原因，供账本和审计复核。
                "ice_ban": bool(ai_meta.get("ice_ban") or signal.get("ice_ban")),
                "ice_warn": ai_meta.get("ice_warn"),
                # 🎯 9:25 池内裁决（2026-09-21 用户「接力池＝提前缩范围，不是提前拍板」）：
                # plan_view/plan_bias = 昨日预案（旧口径终态/预案档）；pool_reason = 今日实判理由；
                # line_is_core/line_core = 同线身位（核心 vs 跟风）；follower_gate = 跟风闸是否命中。
                "plan_view": _plan_row.get("plan_view"),
                "plan_bias": _plan_row.get("plan_bias"),
                "pool_reason": _plan_row.get("pool_reason"),
                "line_is_core": _plan_row.get("line_is_core"),
                "line_core": _plan_row.get("line_core"),
                "follower_gate": bool(_plan_row.get("follower_gate")),
                "line_tier": _plan_row.get("line_tier"),
                "backrow_note": _plan_row.get("backrow_note"),
                # 🛡️ 前置防守闸门（2026-10-08 经验·distribution/sector_drain/high_position）
                "defense_gate": _plan_row.get("defense_gate"),
                "defense_reason": _plan_row.get("defense_reason"),
                "defense_warn": _plan_row.get("defense_warn"),
                "position_warn": _plan_row.get("position_warn"),
                "mechanical_leader": bool(_plan_row.get("mechanical_leader")),
                "mechanical_leader_note": _plan_row.get("mechanical_leader_note"),
                "leader_role": _plan_row.get("leader_role"),
                "leader_evidence": _plan_row.get("leader_evidence"),
                "dragon_confirm": _plan_row.get("dragon_confirm"),
                "line_tier_outpool": _plan_row.get("line_tier_outpool"),
                "line_tier_why": _plan_row.get("line_tier_why"),
                # True 只会来自“昨日确认独苗 + 今早有效同题材报价均未红开”；
                # 报价缺失时保留 False，防止卡面把数据不足说成板块走弱。
                "linkage_gate": bool(_plan_row.get("linkage_gate")),
                "linkage_discipline": _plan_row.get("linkage_discipline") or signal.get("linkage_discipline"),
                "note": signal.get("note"),
                "gap_pct": signal.get("gap_pct"),
                "auction_price": signal.get("auction_price"),
                "prev_close": signal.get("prev_close"),
                # 竞价量能对比（E项）：实际竞价成交额 vs 预期量能线（昨量比例/下限兜底后）
                "auction_amount_yi": signal.get("auction_amount_yi"),
                "auction_amount_need_yi": signal.get("auction_amount_need_yi"),
            },
            "next_day": {
                "status": next_day_status,
                "return_pct": return_pct,
                "entry_date": _next_day(date10),
                "exit_date": _next_day(date10),
            },
            "hold_2d": {"status": "pending", "return_pct": None},
            "hold_3d": {"status": "pending", "return_pct": None},
        },
    }
    return _rec


def run_verify(
    candidates: dict[str, Any] | None = None,
    quotes: dict[str, dict[str, Any]] | None = None,
    next_close_map: dict[str, float] | None = None,
    *,
    candidates_path: Path | None = None,
    quotes_path: Path | None = None,
    out_nav: bool = True,
    persist: bool = True,
    sector_support_map: dict[str, dict[str, Any]] | None = None,
    relay: bool = False,
    relay_plan: dict | None = None,
) -> dict[str, Any]:
    """
    运行次日验证。candidates/quotes 可直接传入（便于测试），也可从文件加载。

    persist=False 时只计算不落盘（不写净值/指标）——供 --synthesize dry-run 使用，
    防止合成测试数据污染真实交易战绩 cache（2026-08-12 修复）。

    sector_support_map（2026-08-24）：{code: 板块承接力 dict}——由 cmd_verify --fetch
    抓同板块成员竞价后经 core/sector_support.compute_support 算出；喂给 signal_eval
    做「孤军高开 + 板块0红开 → 买降级观察」裁决。

    relay（2026-10-13 用户需求·早盘接力动态仓位）：True = 用**真实 09:25 读数**
    （read-open 市场读数 + 候选池竞价 + 题材沉淀 + 净值回撤）合成动态仓位方案，
    并在池内裁决时**向下裁断名额**（行情转弱→收紧；回撤破硬线→禁买）。
    ⛔ 默认 False：合成/测试不套用（保持结论确定性、可离线复现）；真实 09:25 运行
    （cmd_verify --fetch）显式置 True。relay_plan 可外部直接注入（优先级最高）。
    """
    if candidates is None:
        cp = candidates_path or _latest_candidates()
        if cp is None or not cp.exists():
            raise FileNotFoundError("未找到盘后候选文件（先跑 pipeline_eod）")
        candidates = json.loads(cp.read_text(encoding="utf-8"))

    if quotes is None:
        quotes = load_auction_quotes(quotes_path) if quotes_path else {}

    date10 = str(candidates.get("date") or "").strip()
    all_cands = (list(candidates.get("relay") or []) + list(candidates.get("watch") or [])
                 + list(candidates.get("low_pool") or []))
    # 🤖 AI 口径（2026-09-14 用户「全量以 AI 为唯一口径！」）：
    # AI 决策在**前一交易日盘后**写死（ai_decisions.jsonl·trade_date=本期执行日），
    # 本函数只做「按 AI 自定上限判定能否成交」，不引入当日新信息。
    trade_date10 = _next_day(date10)

    # P1-6 冰点禁买·**时效闸门**（2026-11-09 用户「我要买入的那一套逻辑，不要禁用的这一套逻辑」）：
    # 昨收标签（candidates.environment.regime）只当背景，⛔ 不再单独禁掉今天的买；
    # 只有「今日实时读数」判出的 ice 才禁买。详见 `_resolve_regime` 的时点说明。
    _regime_eod = str((candidates.get("environment") or {}).get("regime") or "").strip() or None
    _reg = _resolve_regime(trade_date10, _regime_eod)
    regime = _reg.get("regime")
    regime_trusted = bool(_reg.get("trusted"))
    ai_decisions: dict[str, dict] | None = None
    valve: dict[str, Any] | None = None
    pool_res: dict[str, Any] | None = None
    if ai_caliber.AI_CALIBER_ACCOUNT:
        ai_decisions = ai_caliber.load_ai_decisions(trade_date10)
        if ai_decisions is None:
            print(f"[VERIFY] ⚠️ {trade_date10} 无 AI 决策行（ai_decisions.jsonl）→ "
                  f"本批按【机械口径】结算（caliber=mechanical_fallback·勿读成 AI 空仓）")
        else:
            print(f"[VERIFY] 🤖 AI 口径生效：{trade_date10} 决策 {len(ai_decisions)} 只"
                  f"（成交集合与入场上限以 AI 为准）")
        # ── 🎯 9:25 池内裁决（2026-09-21 用户「接力池＝提前缩范围，不是提前拍板」）──
        # 竞价已落地 → **只在池内**（relay+watch）用当日实况判命中，定「今天买哪只」。
        # 优先读**当日已冻结**的结论（09:26 首次判定时落盘），保证盘后重跑
        # （`_finalize_prev_batch`）与 AI 轨结算读到的都是同一份（卡面＝账本＝决策）。
        # ⛔ 前置条件＝**该执行日有盘后预案**（`ai_decisions` 非 None）。池内裁决取代的是
        #    「盘后定买哪只」，不是「无预案也能凭空开仓」——无预案时保持旧的诚实降级
        #    （caliber=mechanical_fallback + 显式告警），⛔ 不得让池内裁决把降级悄悄升级成 AI 口径。
        # ⛔ persist 门（2026-09-21 实测 bug·必修）：dry-run / `--synthesize` 走 persist=False，
        #    其语义是"只计算不落盘"（见函数 docstring）。原实现在此处**无条件** `save_pool_call`
        #    → 一次离线重渲染就把结论冻进生产 `wealth_code/cache/pool_call-<date>.json`，
        #    随后被 `cmd_settle` / `_finalize_prev_batch` 当"当日真实裁决"读回（09-21 11:00 实测）。
        #    故 read+write **一并**受 persist 约束：
        #      · persist=True（真实 09:25 运行）→ 读已冻结快照，缺失才计算并冻结；
        #      · persist=False（离线重渲染/合成）→ 一律**用传入 quotes 现算**，既不读也不写
        #        （避免读到真实冻结结论、让 dry-run 输出与所传数据不自洽）。
        if pool_call.POOL_CALL_ENABLED and ai_decisions is not None:
            pool_res = load_pool_call(trade_date10) if persist else None
            if pool_res:
                print(f"[VERIFY] 🎯 池内裁决（当日已冻结快照）→ 买入 "
                      f"{len((pool_res.get('meta') or {}).get('participate') or [])} 只")
                # 旧快照没有溢价榜 → 补算（纯读数，不动已冻结裁决）
                if "premium_board" not in (pool_res.get("meta") or {}):
                    _attach_premium_board(pool_res, _load_full_zt_rows(date10), quotes,
                                          prev_date=date10, trade_date=trade_date10)
            else:
                # 09:25 首次抓取时同步冻结昨日完整涨停池；不能只用已筛选的候选池，
                # 否则红开比例会被选股结果高估。全市场报价不足则闸门自然不放宽。
                full_zt_rows = _load_full_zt_rows(date10)
                market_open_read = build_market_open(full_zt_rows, quotes)
                # 🎯 2026-11-05 用户拍板 A 档：三环联动**影子上下文**（只留痕，⛔ 不裁决）。
                #    数据全部 09:25 前可得：全池报价 + 昨收完整涨停池 + 昨收选股宝题材快照
                #    + T-1 及更早的日线（模块内显式裁掉 T 日，⛔ 无未来函数）。
                #    缺题材快照 / 计算失败 → None，pool_call 不写任何影子字段，裁决完全不受影响。
                _sgc_ctx = None
                try:
                    from core import sector_gap_context as _sgc
                    if _sgc.enabled():
                        _sgc_ctx = _sgc.build_day_context(
                            prev_date=date10, quotes=quotes, zt_rows=full_zt_rows) or None
                        if not _sgc_ctx:
                            print("[VERIFY] 三环影子上下文跳过：缺选股宝题材快照（不裁决）")
                except Exception as _e:                # 诚实降级，绝不打断 09:25 主链
                    _sgc_ctx = None
                    print(f"[VERIFY] 三环影子上下文跳过：{_e}")
                # 🎯 早盘接力·动态仓位（2026-10-13 用户需求·relay_position 唯一判据源）
                # 用**真实 09:25 读数**合成方案（昨日强势 + 今晨竞价 + 题材沉淀 + 净值回撤），
                # 注入 call_pool 做名额的最终向下裁断。⛔ 失败/未开启 → 不裁断（诚实降级）。
                _relay_plan = relay_plan
                if _relay_plan is None and relay:
                    try:
                        from core import relay_position as _rp
                        _relay_plan = _rp.build_plan_from_files(
                            market_open=market_open_read, candidates=candidates, quotes=quotes)
                    except Exception as _e:              # 诚实降级·不得打断裁决
                        print(f"[VERIFY] 早盘接力动态仓位方案跳过：{_e}")
                        _relay_plan = None
                # 📏 同题材身位（2026-11-06 用户 A）：身位差的分母必须是**炒作题材**，
                #    不是申万行业。`full_zt_rows` 只带 sector/industry，直接用它会把
                #    「同行业最高板」当成分母（一个行业里板数天然一致 → 身位差恒 0），
                #    等于让新口径静默失效。⇒ 用选股宝 plates 快照算好后显式注入。
                #    ⛔ 落行业兜底的票**不注入**：`main_theme` 会标 src=sector，
                #    这类票在 pool_call 里拿不到题材 → basis=unknown → 不裁决（不猜）。
                _code_theme_pos = None
                try:
                    from core import sector_gap_context as _sgc_theme
                    _plates = _sgc_theme.load_plates_map(date10)
                    if _plates:
                        _code_theme_pos = {}
                        for _row in (full_zt_rows or []):
                            _cd = str(_row.get("code") or "").strip()
                            if len(_cd) != 6:
                                continue
                            _th, _src = _sgc_theme.main_theme(_cd, _row, _plates)
                            if _th and _src in ("plates", "theme"):
                                _code_theme_pos[_cd] = _th
                        _code_theme_pos = _code_theme_pos or None
                except Exception as _e:              # 诚实降级：缺题材 → 身位不裁决
                    _code_theme_pos = None
                    print(f"[VERIFY] 同题材身位上下文跳过：{_e}")
                # 🚫 盘后预案（2026-11-06 用户「全系统关闭」）：开关关时**根本不读** ai_view，
                #    并从 09:25 竞价读数机械定调（`core/stance_intraday`），名额不再来自
                #    「昨天盘后 LLM 拍的攻守基调」。
                _eod_plan_on = bool(getattr(pool_call, "POOL_CALL_EOD_PLAN", False))
                _intraday_on = bool(getattr(pool_call, "POOL_CALL_STANCE_INTRADAY", False))
                _stance_why = ""
                _plan_in = _load_plan(trade_date10) if _eod_plan_on else {}
                _stance_in = ai_caliber.ai_stance(trade_date10) if _eod_plan_on else None
                if not _eod_plan_on and _intraday_on:
                    try:
                        from core import stance_intraday as _si
                        _st = _si.stance_from_open(market_open_read)
                        _stance_in = _st.get("stance")
                        _stance_why = _st.get("reason")
                    except Exception as _e:      # 诚实降级：定调失败 → 不裁决
                        _stance_in, _stance_why = None, f"机械定调跳过：{_e}"
                # AI_TILT: 2026-11-11 intelligent scoring layer (advice only)
                # 题材催化强度（LLM 写 ai_advice_<date>.json；缺失=None，等于没有 LLM 意见）
                _catalyst = None
                try:
                    from core import ai_advice as _aa
                    _catalyst = _aa.load(trade_date10)
                    if _catalyst and not _catalyst.get("valid"):
                        _catalyst = None
                except Exception as _e:  # noqa: BLE001
                    print(f"[VERIFY] catalyst skipped: {_e}")
                    _catalyst = None
                _tilt_advice = None
                try:
                    _mode = str(getattr(pool_call, 'AI_TILT_MODE', 'off') or 'off').lower()
                    if _mode in ('shadow', 'live'):
                        from core import ai_tilt as _at
                        _m = _at.load_model()
                        if _m:
                            _pool_rows = [*(candidates.get('relay') or []),
                                          *(candidates.get('watch') or [])]
                            _tilt_advice = _at.advice_payload(_m, _pool_rows, mode=_mode)
                except Exception as _e:
                    print(f'[VERIFY] ai_tilt skipped: {_e}')
                    _tilt_advice = None
                pool_res = pool_call.call_pool(
                    candidates, quotes, plan=_plan_in,
                    advice=_tilt_advice,
                    catalyst=_catalyst,
                    stance=_stance_in,
                    market_open=market_open_read,
                    # 🌊 2026-11-04 独苗高开闸：要**昨日完整涨停池**算「同板块其余票红开率」。
                    #    ⛔ 不能只给候选池：池内大消费只有 3 只、红开 2 只=67%，
                    #       全市场同族 10 只只 3 只红开=30% —— 结论完全相反（11-04 东方海洋）。
                    #    ⛔ 必须用 `theme_open_rows` 而非 `zt_rows`：后者还负责「市场最高板」
                    #       兜底，传进去会改身位闸参照系，并让测试吃到生产文件（11-04 实测 3 例红）。
                    theme_open_rows=full_zt_rows,
                    # 🌊 题材动量（变化）：家数环比 / 位置 / 连续天数。
                    #    读 `cache/theme_momentum/<买入日>.json`（由 build_theme_momentum 预生成，
                    #    内容严格早于买入日）。⛔ 只写 views[code].theme_momentum，不改裁决。
                    theme_momentum=pool_call.load_theme_momentum(trade_date10),
                    # 🎯 11-05 竞价三态分母 = 昨日全天成交额（昨收归档 cje_yi，
                    #    09:25 前已存在）。复用上面已冻结的 full_zt_rows，不额外抓行情。
                    prev_amounts=_ast.prev_amounts_from_rows(full_zt_rows),
                    sector_gap=_sgc_ctx,
                    code_theme=_code_theme_pos,
                    relay_plan=_relay_plan)
                # ⚖️ 仓位/暴露控制（2026-11-12 吸收 quant-review 四条优点）
                #    edge 五档 / 连续走弱降档 / 崩盘预警 / 满仓三条件 → 取小。
                #    `EXPOSURE_MODE=off|shadow|live`（默认 shadow：只记录，不改仓位）。
                try:
                    from core import position_edge as _pe
                    _exp = _pe.snapshot()
                    _exp["mode"] = str(os.environ.get("EXPOSURE_MODE", "shadow")).lower()
                    if _exp["mode"] == "live" and _exp["exposure"] < 1:
                        for _c, _v in (pool_res.get("views") or {}).items():
                            if _v.get("view") != "participate":
                                continue
                            _old = _v.get("position_cap")
                            if isinstance(_old, (int, float)) and _old > 0:
                                _v["position_cap"] = round(float(_old) * _exp["exposure"], 4)
                                _v["reason"] = ((_v.get("reason") or "")
                                                + f"（暴露系数 {_exp['exposure']}：{_exp['why']}）")
                    pool_res.setdefault("meta", {})["exposure"] = _exp
                    print(f"[VERIFY] 暴露系数 {_exp['exposure']}（{_exp['mode']}）"
                          f" · 主因 {_exp['driver']} · {_exp['why']}")
                except Exception as _e:  # noqa: BLE001
                    print(f"[VERIFY] 暴露控制跳过：{_e}")

                # 🩺 判据链体检（2026-11-12 用户「看不出今天到底用了哪些闸」）
                #    只读：把「哪几条闸真的开了、哪几条缺数据」汇总成一行，写 meta.chain。
                try:
                    from core import chain_health as _ch
                    _meta_now = pool_res.get("meta") or {}
                    _chain = _ch.build(
                        pool_res,
                        catalyst=bool(_catalyst),
                        radar=len(((_meta_now.get("theme_auction") or {}).get("lines")) or []),
                        ai_layer=bool(_meta_now.get("ai_layer")))
                    pool_res.setdefault("meta", {})["chain"] = _chain
                    print(f"[VERIFY] {_chain['line']}")
                except Exception as _e:  # noqa: BLE001
                    print(f"[VERIFY] 判据链体检跳过：{_e}")

                # 🧭 板块竞价雷达（2026-11-12 用户「看见开一字了，要看这个题材里开得怎么样」）
                #    只写 meta（只读观察），⛔ 不参与任何买入判据。
                try:
                    from core import theme_auction_radar as _tar
                    from core import sector_gap_context as _sgcr
                    _plates_r = _sgcr.load_plates_map(date10)
                    def _theme_of_r(c: str, r: dict):
                        _t = (_code_theme_pos or {}).get(c)
                        if _t:
                            return _t
                        try:
                            _tt, _src = _sgcr.main_theme(c, r, _plates_r)
                            return _tt if _src in ("plates", "theme") else ""
                        except Exception:  # noqa: BLE001
                            return ""
                    _radar = _tar.build(full_zt_rows or [], quotes or {}, _theme_of_r)
                    pool_res.setdefault("meta", {})["theme_auction"] = {
                        "schema": _tar.SCHEMA,
                        "lines": _radar[:12],
                    }
                except Exception as _e:  # noqa: BLE001
                    print(f"[VERIFY] 板块竞价雷达跳过：{_e}")

                # 📈 昨日连板 · 今晨竞价溢价榜（2026-11-04 用户）
                _attach_premium_board(pool_res, full_zt_rows, quotes,
                                      prev_date=date10, trade_date=trade_date10)
                # 🧾 定调来源留痕（只读诊断·⛔ 不改裁决）
                try:
                    _pm = pool_res.setdefault("meta", {})
                    _pm["stance_source"] = {
                        "eod_plan": bool(_eod_plan_on),
                        "intraday": bool(_intraday_on),
                        "stance": _stance_in,
                        "why": _stance_why,
                    }
                except Exception:               # noqa: BLE001
                    pass

                _freeze_blocked = early_freeze_blocked(trade_date10)
                if _freeze_blocked:
                    print("[VERIFY] ⏳ 09:25:30 前不冻结池内裁决（防数据准备抢跑）；"
                          "本次照常算卡，正式结论留给 09:26")
                if persist and not _freeze_blocked:
                    save_pool_call(trade_date10, pool_res)
                    # 🗂 数据源聚合（2026-11-04 用户「源必须聚合，展现可以分散」）：
                    #    当日竞价终裁 + 报价写进 `cache/snapshot/<date>.json`，
                    #    下游（卡面/诊断/单票视图）从这里读，不再各自去找原始文件。
                    #    ⛔ 只搬运已冻结的结论，不重算、不改裁决；失败不影响主链。
                    #    ⛔ 只在 persist=True 时写（dry-run 绝不落盘，09-21 血案同源铁律）。
                    #    ⛔ 只在**生产** CACHE_DIR 时写：测试把 CACHE_DIR 指到 tmp_path，
                    #       若照旧写死 HUNTER_ROOT，就会把 9-14/9-15 这类测试日期灌进
                    #       生产快照目录（2026-11-04 实测：跑完测试多出两个幽灵日期）。
                    _prod_cache = Path(__file__).resolve().parent.parent / "cache"
                    if Path(CACHE_DIR).resolve() == _prod_cache.resolve():
                        try:
                            from core import snapshot as _snap
                            _snap.put_section(trade_date10, "verdict", {
                                "stance": pool_res.get("stance"),
                                "quota": pool_res.get("quota"),
                                "effective_quota": (pool_res.get("meta") or {}).get("effective_quota"),
                                "participate": (pool_res.get("meta") or {}).get("participate"),
                                "market_open": (pool_res.get("meta") or {}).get("market_open"),
                                "slot_policy": (pool_res.get("meta") or {}).get("slot_policy"),
                                "recheck": (pool_res.get("meta") or {}).get("recheck"),
                                "anchor_lines": (((pool_res.get("meta") or {}).get("defense")
                                                  or {}).get("anchor") or {}).get("lines"),
                                "views": pool_res.get("views"),
                            })
                            if quotes:
                                _snap.put_section(trade_date10, "quotes", quotes)
                        except Exception as _se:      # 聚合失败 ≠ 裁决失败
                            print(f"[VERIFY] 快照聚合跳过：{_se}")
                    # 🎯 动态仓位方案落盘（2026-10-15）：供尾盘潜伏按 decision_date
                    # 取当天仓位约束、加权出「账户当日收益」。落盘失败不得打断裁决。
                    if _relay_plan is not None:
                        try:
                            from core import relay_position as _rp
                            _rp.persist_daily_plan(_relay_plan, trade_date10)
                        except Exception as _pe:
                            print(f"[VERIFY] 动态仓位方案落盘跳过：{_pe}")
                _pm = pool_res.get("meta") or {}
                _slot_v = _pm.get("slot_policy") or {}
                _quota_txt = ("不限只数" if _slot_v.get("unlimited_slots")
                              else f"{_pm.get('effective_quota', _pm.get('quota'))} 只")
                print(f"[VERIFY] 🎯 9:25 池内裁决：池内 {_pm.get('pool_size')} 只 · "
                      f"定调 {_pm.get('stance') or '—'} · 当日可买 "
                      f"{_quota_txt} → "
                      f"买入 {len(_pm.get('participate') or [])} 只 {_pm.get('participate') or ''}"
                      + ("" if persist else "（dry-run·不落盘）"))
                _rc_v = _pm.get("recheck") or {}
                if _rc_v.get("enabled"):
                    print(f"[VERIFY] 🔁 观察票竞价复核：{_rc_v.get('observed', 0)} 只逐票判过 · "
                          f"{_rc_v.get('state_entered', 0)} 只进完整裁决 · "
                          f"翻案买入 {len(_rc_v.get('participated') or [])} 只")
            ai_decisions = _inject_pool_views(ai_decisions, pool_res, candidates)
        # AI 定调只影响 AI 预案本身；观察/放弃票不能由机械逃生阀改写为成交。

    # 聚合实际使用的竞价输入；日期沿用候选权威日期，不换算或重新取最新文件。
    if persist and Path(CACHE_DIR).resolve() == _PROD_CACHE_DIR.resolve():
        try:
            from core import snapshot as _snap
            _candidate_section = _snap.build_candidate_section(candidates)
            _candidate_section["candidate_date"] = date10
            _snap.put_section(trade_date10, "candidates", _candidate_section)
        except Exception as exc:
            print(f"[VERIFY] 候选快照聚合跳过：{exc}")

    # ── 卡面「指标打满」要用的静态属性（2026-10-21 用户）──────────────────
    # ⛔ 为什么要有这个白名单：`_build_record` 只从候选行搬它自己认识的字段
    #    （26 个），量价/身份/梯队/资金这些**全被丢掉** → 卡面 `_buy_indicator_lines`
    #    读了全是 None，五行只渲染出两行（实测）。
    # ⛔ 只透传**不参与裁决**的展示字段，⛔ 不许把裁决相关字段塞进来（避免两处口径打架）。
    _DISPLAY_FIELDS = (
        # 量价
        "cje_yi", "hs", "seal_amt", "seal_ratio_pct", "zbc", "fbt",
        # 板块与身份
        "core_theme", "core_theme_logic", "core_theme_heat", "industry_sector",
        "theme_mates_cnt", "sector_mates_cnt", "is_sector_dragon",
        "leader_role", "style_tag",
        # 梯队
        "ladder_grade", "ladder_health", "ladder_has_complete",
        # 带动与资金
        "carry_cnt", "carry_time", "carry_score", "main_flow_yi",
        "main_flow_5d_yi", "lt_mktcap", "zsz_yi",
        # 筹码
        "chip_profit_rate", "chip_conc90",
        # 历史
        "history_relay_prob", "ret_10d_pct", "dev_10d_pct",
    )

    records: list[dict[str, Any]] = []
    for cand in all_cands:
        code = str(cand.get("code") or "").strip()
        q = quotes.get(code)
        nc = (next_close_map or {}).get(code)
        rec = _build_record(cand, q, nc, date10=date10, regime=regime,
                            regime_trusted=regime_trusted,
                            sector_support=(sector_support_map or {}).get(code),
                            ai_decisions=ai_decisions,
                            valve_code=(valve or {}).get("code"),
                            pool_views=(pool_res or {}).get("views"))
        # 🔍 池外龙头校验（2026-10-21）：record 由 `_build_record(cand, ...)` 从**候选行**重建，
        #    ⛔ `_build_record` 不认这个字段 → 必须在出口处补，否则后端判了卡面也读不到
        #    （实测：views 里是 back、record 里却是 None，卡面 `_outpool_badge` 静默失效）。
        for _k in ("line_tier_outpool", "line_tier_why", "line_top", "line_tops"):
            if cand.get(_k) is not None:
                rec[_k] = cand.get(_k)
        # 🎯 卡面「指标打满」的静态属性（白名单·见上方 _DISPLAY_FIELDS 注释）
        for _k in _DISPLAY_FIELDS:
            if _k in cand and cand.get(_k) is not None:
                rec[_k] = cand.get(_k)
        records.append(rec)

    payload = {
        "schema": "stockResearchBacktest",
        "meta": {"generated_at_bj": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")},
        "records": records,
        # 口径标注（2026-09-14）：ai / mechanical / mechanical_fallback
        "caliber": ("ai" if ai_decisions else
                    ("ai_missing_no_trade" if ai_caliber.AI_CALIBER_ACCOUNT else "mechanical")),
        # 环境透传（2026-08-16）：feedback 10 分制情绪，供卡片渲染层做规则层风险标记。
        # ⏱️ 2026-11-09：regime 用**当日实时重判**结果覆盖昨收标签，并带 `regime_trusted`
        #    让渲染层知道「这个 ice 是不是今天的」——⛔ 不 trusted 的 ice 不得渲染成禁买。
        "environment": {
            **(candidates.get("environment") or {}),
            "regime": regime,
            "regime_source": _reg.get("source"),
            "regime_trusted": regime_trusted,
            "regime_score": _reg.get("score"),
            "regime_label": _reg.get("label"),
            "regime_eod": _reg.get("eod_regime"),
            # 昨收分 = candidates.environment.sentiment_score（盘后写死），⛔ 不读实时文件
            "regime_eod_score": (candidates.get("environment") or {}).get("sentiment_score"),
        },
    }

    # 竞价一字 → 方向线索（2026-11-09 用户「先看见」）
    # ⛔ 纯解释层：不进 records / 不改 can_enter / 不影响 quota。
    #    数据来自 stock_observations 的 09:15~09:30 段（只读），
    #    判据见 core/auction_seal.py 文档（「中间没记录 = 价格没变过」）。
    #    ⚠️ 用执行日 `trade_date10`，⛔ 不是候选日 `date10`。
    payload["auction_seal"] = _auction_seal_payload(trade_date10)

    # 📉→📈 观察池竞价弱转强（2026-11-09 用户「弱转强写进竞价卡里面」）
    # ⛔ 同样纯解释层：不进 records / 不改 can_enter / 不影响 quota。
    #    数据来自 cache/watch_pool/<date>.jsonl 每轮心跳（带时间戳的多次采样），
    #    心跳缺失才回落 stock_observations；⛔ 两条来源不混算同一段路径。
    payload["watch_reversal"] = _watch_reversal_payload(trade_date10)

    nav_rows = sync_account_nav_ledger(backtest_payload=payload) if (out_nav and persist) else []
    metrics = (
        sync_account_strategy_metrics(backtest_payload=payload)
        if persist
        else build_account_strategy_metrics(backtest_payload=payload)
    )

    # 统计本次
    buys = [r for r in records if r["performance"]["open_check"]["can_enter"]]
    mech_buys = [r for r in records if r["performance"]["open_check"].get("mech_can_enter")]
    covered = [r for r in records if r["performance"]["next_day"]["status"] == "covered"]
    # 冰点命中数（2026-08-19 用户澄清）：ice 期 buy→hit 不进场，但命中票照常统计供卡片展示
    hits = [r for r in records if r["performance"]["open_check"]["decision_status"] == "hit"]
    _caliber = ("ai" if ai_decisions else
                ("ai_missing_no_trade" if ai_caliber.AI_CALIBER_ACCOUNT else "mechanical"))
    summary = {
        "date10": date10,
        "trade_date10": trade_date10,
        "total_candidates": len(records),
        "buy_signals": len(buys),           # 🤖 口径化：= 实际可进场只数（AI 口径下即 AI 实买）
        "mech_buy_signals": len(mech_buys),  # 机械参考只数（卡面「机械达标 N 只」）
        "caliber": _caliber,
        "ai_decisions": len(ai_decisions) if ai_decisions else 0,
        "caliber_switched": sum(1 for r in records
                                if r["performance"]["open_check"].get("caliber_changed")),
        # hibernate 逃生阀（2026-09-18 用户拍板 A）：当日是否开阀 + 放行标的（留痕·供卡面/审计）
        "ai_stance": ai_caliber.ai_stance(trade_date10) if ai_caliber.AI_CALIBER_ACCOUNT else None,
        "hibernate_valve": valve,
        "hit_signals": len(hits),
        "covered": len(covered),
        # 🎯 9:25 池内裁决（2026-09-21）：当日可买只数 / 池内逐票结论（留痕·供卡面与审计）
        "pool_call": (pool_res or {}).get("meta"),
        "pool_views": (pool_res or {}).get("views"),
        "nav_latest": nav_rows[-1]["nav"] if nav_rows else None,
        "nav_rows": len(nav_rows),
    }
    return {
        "payload": payload,
        "summary": summary,
        "metrics": metrics,
        "nav_rows": nav_rows,
        # 盘后卡写入的全局旁白随候选文件透传，使早盘验证卡 🗣️ 与盘后卡一致（无需重传 --narratives）
        "global_narrative": candidates.get("global_narrative"),
        # 🎯 2026-11-05 旁白口径统一：撰写日显式写候选日（= 上一交易日盘后），
        #    渲染层据此标「昨日」并做时点平移；⛔ 不再靠 _narr_basis_label 回退推断。
        #    若 launchd 注入了当日 AI 旁白，cmd_verify 会用当日覆盖本字段（优先级更高）。
        "narrative_date": summary.get("date10"),
    }


def _resolve_regime(date10: str, eod_regime: str | None) -> dict[str, Any]:
    """冰点闸门的**时效**裁决（2026-11-09 用户「我要买入的那一套逻辑，不要禁用的这一套逻辑」）。

    背景与病灶：
      `candidates.environment.regime` 是**上一交易日盘后**按当时情绪打的标签。拿昨收的温度
      去锁今早 09:25 已经逐票判出来的买点，等于用昨天的天气决定今天穿什么。
      实测 2026-11-09：11-06 收盘 1.8 分（ice）→ 11-09 09:29 已 6.3 分（修复），
      卡片标题却仍写「市场很冷·不买·空仓」，而正文 AI 实判了阳光照明买入 —— 一卡两结论。

    规则（`VERIFY_REGIME_LIVE=0` 一键回退旧行为：无条件信任昨收标签）：
      ① `feedback_matrix.date == 今日` → **当日实时读数**重算 regime（唯一权威，trusted=True）
      ② 昨日标签 / 无读数 → 保留 regime 但 `trusted=False`：**只标注不禁买**
         （「昨收冰点」是背景信息，不是今天的闸门）

    ⛔ 不新抓行情、不改 signal_eval 的档位阈值：只决定「这个 ice 是不是今天的 ice」。
    """
    if os.environ.get("VERIFY_REGIME_LIVE", "1") == "0":
        return {"regime": eod_regime, "source": "eod_legacy", "score": None,
                "label": "", "trusted": True, "eod_regime": eod_regime, "eod_score": None}

    out: dict[str, Any] = {
        "regime": eod_regime, "source": "eod_prev", "score": None, "label": "",
        "trusted": False, "eod_regime": eod_regime, "eod_score": None,
    }
    # ⚠️ 两个 cache 不是一个目录：candidates-*.json 在 `wealth_code/cache/`（ROOT/cache），
    #    而 feedback_matrix.json / holdings.json 在**项目根** cache/（ROOT.parent/cache）。
    #    2026-11-09 实测：读 ROOT/cache 拿到 not-exists，静默降级成「昨日标签」。
    _fb_root = ROOT.parent / "cache"
    fb_path = _fb_root / "feedback_matrix.json"
    try:
        fb = json.loads(fb_path.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(fb, dict) or fb.get("error"):
        return out

    if str(fb.get("date") or "") != str(date10):
        # 文件还停在上一个交易日 → 昨收标签，不禁买（昨收分由调用方从 candidates 补）
        return out

    # 当日有读数：用 load_environment 的口径重算（与盘后同一套阈值，⛔ 不另写一份）
    from adapters.input_zt import load_environment
    try:
        env = load_environment(_fb_root)
    except Exception:
        return out
    _reg = str(env.get("regime") or "").strip() or None
    if _reg:
        out.update({"regime": _reg, "source": "live_today", "trusted": True,
                    "score": env.get("sentiment_score"),
                    "label": str(env.get("sentiment_label") or "")})
    return out


def _auction_seal_payload(trade_date10: str) -> dict[str, Any]:
    """竞价一字清单 + 方向线索（2026-11-09 用户「先看见」）。

    用户原话：「竞价的时候如果有一字涨停，需要留意他的炒作方向，
              比如今天的西部材料带动商业航天逻辑！！」

    ⛔ **纯解释层**：不进 `records`、不改 `can_enter`/`view`、不影响 `quota`/名额。
       一字板的隔壁票不得因此成为买入理由（11-06 实测：虹吸 −1.49%/胜 40.4%）。

    ⛔ 取不到就说取不到（`available=False` + reason），**不写「无一字」**——
       抓不到 ≠ 没有，这是短线精灵踩过的坑。
    """
    try:
        from common.data_gateway import auction_seal
    except Exception as exc:                       # pragma: no cover - 层缺失
        return {"available": False,
                "reason": f"聚合层不可用: {type(exc).__name__}: {exc}"}

    try:
        res = auction_seal(trade_date10)
    except Exception as exc:                       # 只读查询失败不得拖垮竞价卡
        return {"available": False,
                "reason": f"读取失败: {type(exc).__name__}: {exc}"}

    if not isinstance(res, dict):
        return {"available": False, "reason": "返回结构异常"}
    if res.get("disabled"):
        return {"available": False, "reason": res.get("reason") or "已关闭"}

    seal = res.get("auction_seal") or {}
    states = seal.get("states") or {}
    if not states:
        return {"available": False, "reason": res.get("reason") or "无竞价观测"}

    return {
        "available": True,
        "schema": seal.get("schema"),
        "date": seal.get("date"),
        "items": seal.get("items") or [],
        "themes": seal.get("themes") or [],
        "states": states,
        "wobbled_sample": seal.get("wobbled_sample") or [],
        "meta": seal.get("meta") or {},
    }


def _watch_reversal_payload(trade_date10: str) -> dict[str, Any]:
    """观察池竞价弱转强（2026-11-09 用户「弱转强写进竞价卡里面」）。

    用户原话：「竞价的时候，昨天涨停，今天开盘竞价跌停，然后慢慢的居然红开了。
              我靠，这种竞价弱转强，我也是非常想要买的呀」
              「我们只需要看我们这边昨天准备好的那些票就好了呀。不需要看场外的那些票，
                只关注我们当天收盘锁定的那些票。」
              「我们可以根据时间戳来更新嘛，记录一次时间，再记录一次时间，
                对比一下价格变化，就能看出来它的趋势变化了呀。哪怕没数据也没关系，
                我们这边做好数据的兼容就好。」

    池子 = 观察池（昨日盘后 `candidates` 的 relay+watch+low_pool，09:25 后并上当日
    `pool_call`）。⛔ 不扩全市场；⛔ 只看自己锁定的票。

    ⛔ **纯解释层**：不进 `records`、不改 `can_enter`/`view`、不影响 `quota`/名额。
    ⛔ `pending`（末条早于 09:25）**不得当弱转强**；`no_data` 不等于「没反转」。
    ⛔ 取不到就写取不到（`available=False` + reason），不写「没有弱转强」。
    """
    try:
        from common import watch_pool
    except Exception as exc:                       # pragma: no cover - 层缺失
        return {"available": False,
                "reason": f"观察池层不可用: {type(exc).__name__}: {exc}"}

    if not getattr(watch_pool, "ENABLE_WATCH_POOL", True):
        return {"available": False, "reason": "观察池快照已关闭（ENABLE_WATCH_POOL_SNAPSHOT=0）"}

    try:
        res = watch_pool.auction_paths(date=trade_date10)
    except Exception as exc:                       # 只读查询失败不得拖垮竞价卡
        return {"available": False,
                "reason": f"读取失败: {type(exc).__name__}: {exc}"}

    if not isinstance(res, dict):
        return {"available": False, "reason": "返回结构异常"}

    items = [it for it in (res.get("items") or []) if isinstance(it, dict)]
    counts = res.get("counts") or {}
    if not items:
        return {"available": False,
                "reason": "观察池为空（昨日 candidates 与当日 pool_call 都没可用池）",
                "pool_size": 0}

    # 全部 no_data：一层数据都没取到 —— 这与「没有反转」是两件事，⛔ 不可混。
    if int(counts.get("no_data") or 0) >= len(items):
        return {"available": False,
                "reason": "本轮没抓到观察池行情（快照未覆盖竞价窗口）",
                "pool_size": len(items), "counts": counts,
                "rounds": res.get("rounds") or 0}

    return {
        "available": True,
        "schema": res.get("schema"),
        "date": res.get("date"),
        "source": res.get("source"),
        "rounds": res.get("rounds") or 0,
        "global_last_ts": res.get("global_last_ts") or "",
        "pool_size": len(items),
        "items": items,
        "counts": counts,
        "window": res.get("window"),
        "deep_pct": res.get("deep_pct"),
    }


def _latest_candidates() -> Path | None:
    files = sorted(CACHE_DIR.glob("candidates-*.json"))
    return files[-1] if files else None


def sync_holdings_from_verify(res: dict[str, Any], date10: str) -> dict[str, Any]:
    """
    把 verify 结果中的买入信号（open_check.can_enter=True）写入 cache/holdings.json
    （未结算持仓），供看板「当前持仓（未结算）」区块展示。

    ⚠️ 口径（2026-09-14 用户「全量以 AI 为唯一口径」）：can_enter 已由 core/ai_caliber 接管
    （AI 判 participate ∧ 竞价 ≤ AI 自定上限），故本函数自动同步的就是 **AI 实际买入的票**，
    与净值账本同源；机械达标票不再进持仓。

    仅真实买入运行调用（cmd_verify 非 --synthesize）；测试/合成（persist=False）不调用，
    避免合成数据污染真实持仓展示。持仓代表「当前未结算敞口」，结算后由 clear_holdings 清空。
    """
    payload_records = (res.get("payload") or {}).get("records") or []
    holdings: list[dict[str, Any]] = []
    for r in payload_records:
        oc = (r.get("performance") or {}).get("open_check") or {}
        if not oc.get("can_enter"):
            continue
        ap = oc.get("auction_price") or 0.0
        pc = oc.get("prev_close") or 0.0
        auction_pct = round((ap / pc - 1) * 100.0, 2) if (pc and ap) else None
        holdings.append({
            "code": r.get("code"),
            "name": (str(r.get("name") or "")).replace(" ", ""),  # 去候选数据里的内嵌空格（如「金 螳 螂」）
            "theme": r.get("main_line"),
            "boards": r.get("boards"),
            "is_20cm": r.get("is_20cm"),
            "bucket_label": r.get("bucket_label"),
            "auction_pct": auction_pct,
            "open_status": oc.get("status"),  # super / expected
        })
    payload = {
        "updated": date10,
        "note": (
            f"{date10} 实际买入 {len(holdings)} 只（口径 = AI 决策：AI 判参与且竞价不超自定上限·"
            f"2026-09-14 起；自动同步自 verify 任务。收益待当日 15:00 盘后结算写入净值账本 "
            f"account_nav_history.jsonl）。"
        ),
        "holdings": holdings,
    }
    out = HOLDINGS_PATH
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def clear_holdings(date10: str) -> None:
    """
    批次盘后结算后清空持仓（持仓已闭合，转为净值账本历史）。
    在 cmd_eod 的 _finalize_prev_batch(closing=True) 成功结算后调用。
    """
    payload = {
        "updated": date10,
        "note": (
            f"{date10} 批次已于盘后结算，持仓清空（逐票收益见净值账本 "
            f"account_nav_history.jsonl，trade_date={date10}）。"
        ),
        "holdings": [],
    }
    out = HOLDINGS_PATH
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    res = run_verify()
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
