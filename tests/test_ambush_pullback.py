# -*- coding: utf-8 -*-
"""尾盘阴线回踩 MA10 潜伏策略测试：全隔离、无网络。"""
import importlib.util
import json
from pathlib import Path

WC = Path(__file__).resolve().parents[1] / "wealth_code"
spec = importlib.util.spec_from_file_location("ambush_pullback", WC / "core" / "ambush_pullback.py")
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)

TODAY = "2026-10-08"


def _write_zt(cache, day, rows):
    d = cache / "zt_history"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"zt_{day}.json").write_text(json.dumps({"date": day, "zt_list": rows}, ensure_ascii=False), encoding="utf-8")


def _row(code="000001", name="测试股份", boards=2, sector="测试题材", cje_yi=8):
    return {"code": code, "name": name, "boards": boards, "sector": sector, "cje_yi": cje_yi}


def _bars(open_=9.70, close=9.55, volume=70, prev_volume=100):
    # 前12根收盘线性走高，最近三根 MA10 连续上行；末根阴线贴 MA10。
    closes = [8.8, 8.9, 9.0, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 10.0, close]
    rows = []
    for i, c in enumerate(closes):
        rows.append({"date": f"2026-09-{10+i:02d}", "open": c - 0.05, "close": c,
                     "high": c + 0.1, "low": c - 0.1, "volume": 100.0})
    rows[-2]["volume"] = prev_volume
    rows[-1].update({"date": TODAY, "open": open_, "close": close, "volume": volume})
    return rows


def _seed(cache):
    # 自身曾2连板即可构成历史热门爆发资格，避免夹具里的同题材票同时入池。
    _write_zt(cache, "2026-09-25", [_row()])


def test_scan_selects_only_quantified_pullback_shape(tmp_path):
    _seed(tmp_path)
    out = ap.scan(tmp_path, bars_fetcher=lambda code, n: _bars(), today=TODAY)
    assert out["stats"]["hits"] == 1
    row = out["candidates"][0]
    assert row["code"] == "000001"
    assert row["is_bearish"] and row["ma10_up"] and row["near_ma10"] and row["is_shrinking"]
    assert row["volume_ratio"] == 0.7


def test_scan_rejects_yang_line_nonrising_ma10_and_nonshrinking(tmp_path):
    _seed(tmp_path)
    # 阳线
    out = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(open_=9.50, close=9.55), today=TODAY)
    assert out["candidates"] == []
    assert out["stats"]["rejected"]["当日非阴线"] == 1
    # 不缩量
    out = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(volume=90, prev_volume=100), today=TODAY)
    assert out["candidates"] == []
    assert out["stats"]["rejected"]["当日未缩量"] == 1


def test_hot_sector_or_history_boards_is_required(tmp_path):
    # 单只首板、板块涨停数不足3，不是历史热门爆发方向。
    _write_zt(tmp_path, "2026-09-25", [_row(boards=1)])
    out = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(), today=TODAY)
    assert out["candidates"] == []


def test_pct_recorded_for_bearish_candle(tmp_path):
    """当日涨跌幅必须随候选落盘（卡片要显示，缺了用户无法判断能不能买）。"""
    _seed(tmp_path)
    out = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(), today=TODAY)
    row = out["candidates"][0]
    assert row["prev_close"] == 10.0
    assert row["pct"] == -4.5


def test_entry_plan_records_same_day_buy_and_settles_next_morning(tmp_path):
    """当日推送后买入价（计入战绩）/ T+1 10:00 卖出价：只用真实时点价，缺价不写盘、不结算。"""
    _seed(tmp_path)
    res = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(), today=TODAY)
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
    res = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(), today=TODAY)
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
    bad = ap.scan(tmp_path, bars_fetcher=lambda c, n: [], today=TODAY)
    assert bad["candidates"] == []
    assert bad["data_ok"] is False
    assert bad["fetch_failed"] == bad["attempted"] == 1
    good = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(), today=TODAY)
    assert good["data_ok"] is True
    assert good["fetch_failed"] == 0


def test_pending_decision_date_picks_latest_unsettled(tmp_path):
    """次日10:00 结算任务自动定位最近一个未结算的决策日（跨周末不用人工填日期）。"""
    _seed(tmp_path)
    res = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(), today=TODAY)
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
    result = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(), today=TODAY)
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


# ── 2026-10-13 口径收严（用户「只买阴线…靠近10日线的阴线」+ 实盘教训）──────────────
def test_hot_window_is_last_five_trading_days(tmp_path):
    """⛔ 用户 10-13 定：「前期的热门题材」＝**近 5 个交易日内**爆发过。

    实证动机：10-09 推的精达股份（热点 09-18）/古越龙山（09-16）、10-13 推的大金重工
    （09-15）——热点距今 20 天上下，是**已死的僵尸题材**。「前期」不该这么远。
    """
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


def test_gap_up_bearish_candle_is_rejected(tmp_path):
    """⛔ 「阴线」须**收跌**：跳空高开后收出的小阴线（实际收涨）不算潜伏标的。"""
    _seed(tmp_path)
    out = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(open_=10.30, close=10.10), today=TODAY)
    assert out["candidates"] == []
    assert out["stats"]["rejected"]["当日未收跌（跳空高开收阴不算）"] == 1


def test_crash_beyond_drop_limit_is_rejected(tmp_path):
    """⛔ 跌幅上限 5%：10-13 大金重工 −9.36%、10-12 澳洋健康 −6.94% 被当「缩量回踩」——
    那不是回踩，是砸盘。跌幅超限一律剔除。"""
    _seed(tmp_path)
    out = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(open_=9.60, close=9.40), today=TODAY)
    assert out["candidates"] == []
    assert out["stats"]["rejected"]["当日跌幅>5%（是砸盘·不是回踩）"] == 1
    # 4.5% 仍在限内 → 照常入选（既有夹具）
    ok = ap.scan(tmp_path, bars_fetcher=lambda c, n: _bars(), today=TODAY)
    assert ok["stats"]["hits"] == 1 and ok["candidates"][0]["drop_pct"] == 4.5
