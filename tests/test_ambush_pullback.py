# -*- coding: utf-8 -*-
"""尾盘潜伏·强势趋势核心(分歧转一致)策略测试：全隔离、无网络。

⛔ 2026-10-15 删除了旧的「阴线回踩 MA10」形态逻辑（实证 2 赚 12 亏、累计 -35%），
本模块只覆盖现行单一策略 `ambush_pullback._shape_strong` 及其选股/记账管线。
"""
import importlib.util
import json
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("ambush_pullback", WC / "core" / "ambush_pullback.py")
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)

import pytest

TODAY = "2026-10-08"


def _write_zt(cache, day, rows):
    d = cache / "zt_history"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"zt_{day}.json").write_text(json.dumps({"date": day, "zt_list": rows}, ensure_ascii=False), encoding="utf-8")


def _write_top5(cache, day, themes):
    d = cache / "top5_history"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"top5_{day}.json").write_text(json.dumps(
        {"date": day, "source": "test", "themes": themes}, ensure_ascii=False), encoding="utf-8")


def _row(code="000001", name="测试股份", boards=2, sector="测试题材", cje_yi=8):
    return {"code": code, "name": name, "boards": boards, "sector": sector, "cje_yi": cje_yi}


def _seed(cache):
    # 自身曾2连板即可构成历史热门爆发资格，避免夹具里的同题材票同时入池。
    _write_zt(cache, "2026-09-25", [_row()])


def _strong_bars():
    """合成 70 根日K，满足强势趋势核心·分歧转一致全部条件（前段平台→快速拉升→洗盘→反包）。"""
    bars = []
    for i in range(50):
        d = f"2026-06-{i % 28 + 1:02d}" if i < 28 else f"2026-07-{(i-28) % 31 + 1:02d}"
        bars.append({"date": d, "open": 4.0, "close": 4.0, "high": 4.1, "low": 3.9, "volume": 100.0})
    for i in range(50, 68):
        p = 4.0 + (i - 50) * 0.07
        bars.append({"date": f"2026-08-{i-49:02d}", "open": round(p, 3), "close": round(p, 3),
                     "high": round(p + 0.1, 3), "low": round(p - 0.1, 3), "volume": 100.0})
    # 启动阳线（近15日内）
    p60 = bars[59]["close"]
    bars[60] = {"date": bars[60]["date"], "open": round(p60, 3), "close": round(p60 * 1.08, 3),
                "high": round(p60 * 1.08, 3), "low": round(p60, 3), "volume": 200.0}
    # 洗盘阴线（分歧）
    p68 = bars[67]["close"]
    bars.append({"date": "2026-10-13", "open": round(p68, 3), "close": round(p68 * 0.97, 3),
                 "high": round(p68, 3), "low": round(p68 * 0.96, 3), "volume": 180.0})
    # 反包阳线
    p69 = bars[68]["close"]
    bars.append({"date": "2026-10-14", "open": round(p69 * 0.99, 3), "close": round(p69 * 1.03, 3),
                 "high": round(p69 * 1.03, 3), "low": round(p69 * 0.98, 3), "volume": 160.0})
    for i in range(65, 70):
        bars[i]["volume"] = 180.0
    return bars


# ── 选股宇宙（题材/行业/连板三轨·与形态无关，2026-10-13 落地）──────────────────
def test_hot_sector_or_history_boards_is_required(tmp_path):
    # 单只首板、板块涨停数不足3，不是历史热门爆发方向 → 不入池。
    _write_zt(tmp_path, "2026-09-25", [_row(boards=1)])
    u = ap._historical_universe(tmp_path, TODAY)
    assert "000001" not in u, "单只首板、非热门方向 → 不入池"


def test_hot_window_is_last_five_trading_days(tmp_path):
    """⛔ 用户 10-13 定：「前期的热门题材」＝**近 5 个交易日内**爆发过。"""
    for i, day in enumerate(["2026-09-22", "2026-09-23", "2026-09-24",
                             "2026-09-25", "2026-09-28", "2026-09-29"]):
        if i == 0:                     # 第 6 个交易日前的老票（曾 2 连板）
            rows = [_row(code="600001", name="老票", boards=2, sector="旧题材")]
        elif day == "2026-09-29":      # 最近一日的新票（曾 2 连板）
            rows = [_row(code="600002", name="新票", boards=2, sector="新题材")]
        else:
            rows = [_row(code=f"0001{i:02d}", name=f"他票{i}", boards=1, sector="别的行业")]
        _write_zt(tmp_path, day, rows)
    u = ap._historical_universe(tmp_path, TODAY)
    assert "600001" not in u, "热点已滑出 5 日窗口 → 不得入池"
    assert "600002" in u, "窗口内的新热点票必须入池"


def test_theme_membership_adds_stock_to_pool(tmp_path):
    """⛔ 题材口径（选股宝/东财题材名 + 成分股落地）替代原东财行业口径。"""
    _write_zt(tmp_path, "2026-09-25", [_row(boards=1, sector="冷门行业")])
    _write_top5(tmp_path, "2026-09-25", [
        {"name": "热门题材A", "zt_count": 5, "leader": "龙头", "leader_code": "600002.SZ",
         "codes": ["000001.SZ", "600002.SZ"]},
    ])
    u = ap._historical_universe(tmp_path, TODAY)
    assert "000001" in u, "属热门题材成分股 → 入池"
    assert u["000001"]["themes"] == ["热门题材A"]
    assert u["000001"]["theme_zt_max"] == 5
    # 卡面/台账会带题材信息
    out = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    cand = next(x for x in out["candidates"] if x["code"] == "000001")
    assert cand["themes"] == ["热门题材A"]


def test_stock_without_theme_sector_or_boards_excluded(tmp_path):
    """题材/行业/连板三轨都不满足 → 不入池（诚实，不编造热门方向）。"""
    _write_zt(tmp_path, "2026-09-25", [_row(boards=1, sector="冷门行业")])
    u = ap._historical_universe(tmp_path, TODAY)
    assert "000001" not in u


def test_theme_window_is_last_five_trading_days(tmp_path):
    """⛔ 题材口径同样受「近5个交易日」窗口约束。"""
    for i, day in enumerate(["2026-09-22", "2026-09-23", "2026-09-24",
                             "2026-09-25", "2026-09-28", "2026-09-29"]):
        _write_zt(tmp_path, day, [_row(code=f"0001{i:02d}", boards=1, sector="别的行业")])
    _write_zt(tmp_path, "2026-09-22", [_row(code="600001", boards=1, sector="冷")])
    _write_zt(tmp_path, "2026-09-29", [_row(code="600002", boards=1, sector="冷")])
    _write_top5(tmp_path, "2026-09-22", [
        {"name": "老题材", "zt_count": 5, "leader": "L", "leader_code": "600001.SZ", "codes": ["600001.SZ"]}])
    _write_top5(tmp_path, "2026-09-29", [
        {"name": "新题材", "zt_count": 5, "leader": "L", "leader_code": "600002.SZ", "codes": ["600002.SZ"]}])
    u = ap._historical_universe(tmp_path, TODAY)
    assert "600001" not in u, "题材热点滑出5日窗口 → 不入池"
    assert "600002" in u, "窗口内热门题材 → 入池"


# ── ⑥ 环境读数 → 仓位建议（⛔ 退潮期控仓位·不砍票）──────────────────────────────
def _write_latest(cache, **kw):
    d = cache / "pulse_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")


def test_env_weak_on_broad_selloff(tmp_path):
    """普跌日（10-12 真实读数：47涨停/16跌停/下跌 4026 vs 涨 1180）→ 判退潮。"""
    _seed(tmp_path)
    _write_latest(tmp_path, total_zt=47, limit_down={"count": 16}, broken={"count": 18},
                  breadth={"up": 1180, "down": 4026})
    out = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    assert out["env"]["level"] == "weak"
    assert out["env"]["hit_count"] >= ap.ENV_WEAK_MIN_HITS
    assert out["advice"]["max_picks"] == ap.WEAK_ENV_MAX_PICKS == 2
    assert out["advice"]["position_pct_each"] == 50.0
    assert "退潮" in out["advice"]["note"] and "仓位" in out["advice"]["note"]


def test_env_normal_does_not_cut_candidates(tmp_path):
    """⛔ 铁律：环境只改**仓位建议**，**绝不裁候选**（用户「控制的是仓位，而不是通知票」）。"""
    _seed(tmp_path)
    weak = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    _write_latest(tmp_path, total_zt=68, limit_down={"count": 9}, broken={"count": 16},
                  breadth={"up": 2670, "down": 2435})
    normal = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    assert normal["env"]["level"] == "normal"
    assert normal["advice"]["max_picks"] is None            # 常态不限只数
    assert normal["candidates"] == weak["candidates"]       # ← 一张票都不许少
    assert len(normal["candidates"]) == 1


def test_env_missing_is_graceful(tmp_path):
    """环境文件缺失 → known=False、level=unknown，按常态处理（不编造、不误判退潮）。"""
    _seed(tmp_path)
    out = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    assert out["env"]["known"] is False and out["env"]["level"] == "unknown"
    assert out["advice"]["max_picks"] is None
    assert out["candidates"] and len(out["candidates"]) == 1


# ── 买入/结算管线（时点口径·与形态无关）──────────────────────────────────────
def test_entry_plan_records_same_day_buy_and_settles_next_morning(tmp_path):
    """当日推送后买入价（计入战绩）/ T+1 10:00 卖出价：只用真实时点价，缺价不写盘、不结算。"""
    _seed(tmp_path)
    res = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    ap.record_selection(tmp_path, res)
    pe = ap.plan_entry(tmp_path, TODAY, price_fetcher=lambda codes: {"000001": 9.60})
    assert pe["slot"] == "entry_after_push"
    assert pe["prices"] == {"000001": 9.60}
    assert (tmp_path / ap.ENTRY_FILE_TMPL.format(TODAY)).exists()
    # 缺价一律不写盘（不得用收盘价或相邻价替代）
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ap.plan_entry(empty, TODAY, price_fetcher=lambda codes: {})["prices"] == {}
    assert not (empty / ap.ENTRY_FILE_TMPL.format(TODAY)).exists()
    assert ap.settle_live(empty, TODAY, "2026-10-09", price_fetcher=lambda codes: {"000001": 10.2}) == 0
    assert ap.settle_live(tmp_path, TODAY, "2026-10-09", price_fetcher=lambda codes: {"000001": 10.2}) == 1
    settled = json.loads((tmp_path / ap.SETTLEMENT_LEDGER_NAME).read_text(encoding="utf-8").strip())
    assert settled["return_pct"] == 6.25
    assert settled["entry_time"].startswith(TODAY) and settled["entry_time"].endswith("买入")
    assert settled["exit_time"] == "2026-10-09 10:00"


def test_entry_price_is_not_overwritten_by_later_reruns(tmp_path):
    """买入价＝推送时点价：当日已记录后，后续重跑不得覆盖（否则战绩被追溯改写）。"""
    _seed(tmp_path)
    res = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    ap.record_selection(tmp_path, res)
    first = ap.plan_entry(tmp_path, TODAY, price_fetcher=lambda codes: {"000001": 9.60})
    again = ap.plan_entry(tmp_path, TODAY, price_fetcher=lambda codes: {"000001": 9.99})
    assert again["prices"] == {"000001": 9.60}
    assert again["captured_at"] == first["captured_at"]
    forced = ap.plan_entry(tmp_path, TODAY, price_fetcher=lambda codes: {"000001": 9.99}, overwrite=True)
    assert forced["prices"] == {"000001": 9.99}


def test_data_failure_is_not_reported_as_no_candidate(tmp_path):
    """日K全量抓取失败 ⇒ data_ok=False。缺数据 ≠ 没候选（2026-10-09 腾讯停用 host 血案）。"""
    _seed(tmp_path)
    bad = ap.scan(tmp_path, bars_fetcher=lambda code, n: [], today=TODAY)
    assert bad["candidates"] == []
    assert bad["data_ok"] is False
    assert bad["fetch_failed"] == bad["attempted"] == 1
    good = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    assert good["data_ok"] is True
    assert good["fetch_failed"] == 0


def test_pending_decision_date_picks_latest_unsettled(tmp_path):
    """次日10:00 结算任务自动定位最近一个未结算的决策日（跨周末不用人工填日期）。"""
    _seed(tmp_path)
    res = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    ap.record_selection(tmp_path, res)
    ap.plan_entry(tmp_path, TODAY, price_fetcher=lambda codes: {"000001": 9.6})
    assert ap.pending_decision_date(tmp_path, "2026-10-09") == TODAY
    # 已结算的不再重复
    ap.settle_live(tmp_path, TODAY, "2026-10-09", price_fetcher=lambda codes: {"000001": 10.2})
    assert ap.pending_decision_date(tmp_path, "2026-10-09") is None
    # 更晚的交易日不结算未来的信号
    assert ap.pending_decision_date(tmp_path, "2026-10-01") is None


def test_record_and_settle_are_append_only_and_use_fixed_times(tmp_path):
    _seed(tmp_path)
    result = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    assert ap.record_selection(tmp_path, result) == 1
    assert ap.record_selection(tmp_path, result) == 0
    log = tmp_path / ap.SIGNAL_LEDGER_NAME
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["planned_entry_time"] == "2026-10-08 推送后（≈14:50）"
    assert row["planned_exit_time"] == "next_trade_day 10:00"
    assert row["status"] == "selected_1430"
    assert ap.settle(tmp_path, TODAY, "2026-10-09", {"000001": 10.0}, {"000001": 10.5}) == 1
    assert ap.settle(tmp_path, TODAY, "2026-10-09", {"000001": 10.0}, {"000001": 10.5}) == 0
    settled = json.loads((tmp_path / ap.SETTLEMENT_LEDGER_NAME).read_text(encoding="utf-8").strip())
    assert settled["return_pct"] == 5.0
    assert settled["entry_time"] == "2026-10-08 推送后（≈14:50）"
    assert settled["exit_time"] == "2026-10-09 10:00"


# ── 现行策略形态：强势趋势核心·分歧转一致 ──
def test_strong_trend_selects_divergence_reversal_shape():
    """新策略选中「强势趋势核心·分歧洗盘后反包」形态。"""
    shape, reason = ap._shape_strong(_strong_bars())
    assert shape is not None, reason
    assert shape["strategy_mode"] == "strong_trend"
    assert shape["pullback_date"] == "2026-10-13"
    assert shape["pullback_drop_pct"] < 0


def test_strong_trend_rejects_non_bullish_ma():
    """均线未多头排列 → 剔除。"""
    bars = _strong_bars()
    for i in range(40, 70):
        bars[i]["close"] = 4.0
        bars[i]["open"] = 4.0
    shape, _ = ap._shape_strong(bars)
    assert shape is None


def test_strong_trend_rejects_price_below_ma10():
    """价在 MA10 下方（弱势回踩，正是旧策略选中的失败形态）→ 剔除。"""
    bars = _strong_bars()
    for i in range(60, 70):
        bars[i]["close"] = 2.0
        bars[i]["open"] = 2.0
    shape, _ = ap._shape_strong(bars)
    assert shape is None


def test_strong_trend_rejects_no_pullback():
    """无分歧洗盘阴线 → 剔除。"""
    bars = _strong_bars()
    bars[68]["open"] = bars[68]["close"] = round(bars[67]["close"] * 1.01, 3)
    bars[68]["high"] = bars[68]["close"]
    shape, _ = ap._shape_strong(bars)
    assert shape is None


def test_strong_trend_scan_mode_flag(tmp_path):
    """scan 输出 strategy_mode=strong_trend 且 params 携带新阈值。"""
    _seed(tmp_path)
    out = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    assert out["params"]["strategy_mode"] == "strong_trend"
    assert "strong_ma10_pos" in out["params"]


def test_weighted_daily_pnl_uses_unique_daily_account_return(tmp_path):
    """同一决策日多票必须先按动态仓位加权，再只生成一个当日收益。"""
    ledger = tmp_path / "ambush_pullback_settlement_log.jsonl"
    rows = [
        {"strategy": ap.STRATEGY, "decision_date": "2026-10-14", "trade_date": "2026-10-15",
         "code": "000001", "name": "甲", "return_pct": 10.0, "status": "settled"},
        {"strategy": ap.STRATEGY, "decision_date": "2026-10-14", "trade_date": "2026-10-15",
         "code": "000002", "name": "乙", "return_pct": -4.0, "status": "settled"},
    ]
    ledger.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    out_path = tmp_path / "daily.jsonl"
    out = ap.build_weighted_daily_pnl(
        tmp_path,
        path=out_path,
        relay_loader=lambda day: {"tier": "neutral", "per_pick_cap": 0.4, "total_cap": 0.6},
    )
    assert len(out) == 1
    assert out[0]["weighted_return_pct"] == 1.8
    assert out[0]["nav"] == 1.018
    assert [stock["weight"] for stock in out[0]["stocks"]] == [0.3, 0.3]
    assert len(out_path.read_text(encoding="utf-8").splitlines()) == 1


def test_weighted_daily_pnl_missing_plan_marks_fallback(tmp_path):
    ledger = tmp_path / "ambush_pullback_settlement_log.jsonl"
    ledger.write_text(json.dumps({
        "strategy": ap.STRATEGY, "decision_date": "2026-10-14", "trade_date": "2026-10-15",
        "code": "000001", "name": "甲", "return_pct": 10.0, "status": "settled",
    }) + "\n", encoding="utf-8")
    out = ap.build_weighted_daily_pnl(tmp_path, relay_loader=lambda day: None)
    assert out[0]["plan"]["missing"] is True
    assert out[0]["weighted_return_pct"] == 4.0


# ── 台账取价范围（2026-10-15 用户「就按你这个台账判断的买」）────────────────────
def test_pick_codes_reads_ai_picks_and_is_safe_on_bad_input(tmp_path):
    """推票代码读取：文件缺失/坏 JSON 一律返回空（⛔ 不猜不补）；去重、跳过无码条目。"""
    assert ap.pick_codes(tmp_path, TODAY) == []
    src = tmp_path / f"ambush_ai_{TODAY[5:]}.json"
    src.write_text("{bad json", encoding="utf-8")
    assert ap.pick_codes(tmp_path, TODAY) == []
    src.write_text(json.dumps({"picks": [{"stocks": [
        {"code": "000001"}, {"code": "000001"}, {"name": "无码"}]}]}), encoding="utf-8")
    assert ap.pick_codes(tmp_path, TODAY) == ["000001"]


def test_entry_plan_records_picks_only_and_falls_back_to_pool(tmp_path):
    """买入价台账只记 AI 精选推票（＝卡面「✅ 今日买入」那几只）；无 AI 文件回落冻结全池。"""
    _seed(tmp_path)
    res = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    ap.record_selection(tmp_path, res)                       # 冻结池：000001

    # ① 无 AI 注入文件 → 回落全池（旧口径，scope=pool 显式标注，不静默）
    pe = ap.plan_entry(tmp_path, TODAY, price_fetcher=lambda codes: {"000001": 9.60})
    assert pe["scope"] == "pool" and pe["codes"] == ["000001"]

    # ② AI 推票在池内 → 只记推票（买几只记几只）
    src = tmp_path / f"ambush_ai_{TODAY[5:]}.json"
    src.write_text(json.dumps({"picks": [{"theme": "测试", "stocks": [
        {"name": "测试股份", "code": "000001", "pct": -2.0}]}]}, ensure_ascii=False),
        encoding="utf-8")
    pe2 = ap.plan_entry(tmp_path, TODAY, overwrite=True,
                        price_fetcher=lambda codes: {"000001": 9.60})
    assert pe2["scope"] == "picks" and pe2["codes"] == ["000001"]

    # ③ 推票全在冻结池外 → 剔除（与卡片 PICKS_POOL_ENFORCE 同口径），回落全池
    src.write_text(json.dumps({"picks": [{"theme": "测试", "stocks": [
        {"name": "池外票", "code": "300999", "pct": -2.0}]}]}, ensure_ascii=False),
        encoding="utf-8")
    pe3 = ap.plan_entry(tmp_path, TODAY, overwrite=True,
                        price_fetcher=lambda codes: {"000001": 9.60})
    assert pe3["scope"] == "pool" and pe3["codes"] == ["000001"]   # 池外票不得进台账


def test_entry_plan_respects_explicit_empty_picks(tmp_path):
    """AI 文件明确 `picks=[]`（今日一只不买）→ 不得回落冻结全池，⛔ 不许把没买的票记成成交。

    2026-10-30 实证：当日池内仅 1 只候选被判定不买，若回落全池会凭空产生一笔次日结算的交易。
    """
    _seed(tmp_path)
    res = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    ap.record_selection(tmp_path, res)                       # 冻结池：000001
    src = tmp_path / f"ambush_ai_{TODAY[5:]}.json"
    src.write_text(json.dumps({"ai_flow": "…", "picks": [], "ai_verdict": "…"},
                              ensure_ascii=False), encoding="utf-8")
    assert ap.picks_declared_empty(tmp_path, TODAY) is True
    pe = ap.plan_entry(tmp_path, TODAY, price_fetcher=lambda codes: {"000001": 9.60})
    assert pe["scope"] == "none" and pe["codes"] == [] and pe["prices"] == {}
    assert not (tmp_path / ap.ENTRY_FILE_TMPL.format(TODAY)).exists()   # 不落盘＝不记账

    # 对照：`picks` 键缺失（字段异常/老文件）仍按「文件不可用」回落全池
    src.write_text(json.dumps({"ai_flow": "…"}, ensure_ascii=False), encoding="utf-8")
    assert ap.picks_declared_empty(tmp_path, TODAY) is False
    pe2 = ap.plan_entry(tmp_path, TODAY, price_fetcher=lambda codes: {"000001": 9.60})
    assert pe2["scope"] == "pool" and pe2["codes"] == ["000001"]


def test_entry_picks_only_switch_is_reversible(tmp_path, monkeypatch):
    """开关可逆：`ENTRY_PICKS_ONLY=False` 回退「冻结全池逐票记价」旧口径。"""
    _seed(tmp_path)
    res = ap.scan(tmp_path, bars_fetcher=lambda code, n: _strong_bars(), today=TODAY)
    ap.record_selection(tmp_path, res)
    (tmp_path / f"ambush_ai_{TODAY[5:]}.json").write_text(
        json.dumps({"picks": [{"theme": "测试", "stocks": [
            {"name": "测试股份", "code": "000001", "pct": -2.0}]}]}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(ap, "ENTRY_PICKS_ONLY", False)
    pe = ap.plan_entry(tmp_path, TODAY, price_fetcher=lambda codes: {"000001": 9.60})
    assert pe["scope"] == "pool"
