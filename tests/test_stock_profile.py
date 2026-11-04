#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单票聚合视图（common/stock_view）专项。

边界约定（⛔ 与本模块同义，改这里等于改口径）：
  · 未登记的票 → `found=False`，但视图仍完整返回，不抛异常；
  · 某一维缺失 → `missing`，⛔ 不是 0，也不是「没有」；
  · 不在 board_track → 「未进入连板跟踪」，⛔ 不是「今天没涨停」；
  · 数据早于最新交易日 → `stale`，仍返回原值（不静默丢弃）；
  · 批量与单个必须同一份维度声明，结果逐字相等。
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import stock_profile as sp  # noqa: E402
from common import stock_registry as registry  # noqa: E402

TRADE_DATE = "2026-11-03"


@pytest.fixture(autouse=True)
def _clean_track_cache():
    """board_track 索引是进程内缓存，测试间必须清，否则跨用例串数据。"""
    sp._TRACK_CACHE["stamp"] = None
    sp._TRACK_CACHE["index"] = None
    yield
    sp._TRACK_CACHE["stamp"] = None
    sp._TRACK_CACHE["index"] = None


@pytest.fixture
def db(tmp_path):
    """真实写入链路建的临时底库（⛔ 不碰生产 registry.db）。"""
    path = tmp_path / "registry.db"
    res = registry.upsert_many(
        [{
            "code": "002086", "name": "东方海洋", "industry": "农业种植",
            "themes": ["大消费", "大农业"], "boards": 4, "pct": 9.97,
            "price": 3.42, "seal_yi": 2.16, "hs": 14.58,
            "limit_up_now": 1, "limit_up_touched": 1, "limit_up_close": 1,
        }, {
            "code": "000995", "name": "皇台酒业", "industry": "白酒",
            "themes": ["白酒"], "boards": 3, "pct": 10.0,
            "price": 20.0, "limit_up_now": 1, "limit_up_touched": 1,
        }],
        trade_date=TRADE_DATE, asof_ts=f"{TRADE_DATE}T15:31:00+08:00",
        source="eod:biying_zt", db_path=path,
    )
    assert res["ok"] is True, res
    return path


@pytest.fixture
def gene_dir(tmp_path):
    d = tmp_path / "zt_genes"
    d.mkdir()
    (d / "hist_002086.json").write_text(json.dumps({
        "code": "002086", "first_day": "20200101", "last_day": "20261103",
        "zt_total": 40, "zt_250": 10, "zt_500": 20, "max_boards_hist": 4,
        "promo_rate_hist": 42.9,
    }, ensure_ascii=False), encoding="utf-8")
    return d


@pytest.fixture
def track_dir(tmp_path):
    d = tmp_path / "board_track"
    d.mkdir()
    (d / "track_2026-11-02.json").write_text(json.dumps({
        "date": "2026-11-02",
        "tracked": [{
            "code": "002086", "name": "东方海洋", "boards": 3, "theme": "大消费",
            "seal_yi": 2.63, "seal_ratio_pct": 5.35, "zbc": 1, "mates": 14,
            "hs": 11.02, "cje_yi": 5.4, "lt_yi": 49.17, "dragon_rank": 1,
            "is_sector_dragon": True,
            "track": {"days_in_track": 2, "streak_clean": True},
            "factors": {"count": 3},
            "hist": {"cond": {"n": 5, "rate": 40.0, "quality": "weak"}},
        }],
        "broken": [{"code": "000525", "name": "红太阳", "prev_boards": 2,
                    "last_date": "2026-11-02", "days_in_track": 2}],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "track_2026-11-03.json").write_text(json.dumps({
        "date": "2026-11-03",
        "tracked": [{
            "code": "002086", "name": "东方海洋", "boards": 4, "theme": "大消费",
            "seal_yi": 2.16, "seal_ratio_pct": 3.99, "zbc": 3, "mates": 14,
            "hs": 14.58, "cje_yi": 7.88, "lt_yi": 54.07, "dragon_rank": 1,
            "is_sector_dragon": True,
            "track": {"days_in_track": 3, "streak_clean": True},
            "factors": {"count": 2},
            "hist": {"cond": {"n": 5, "rate": 40.0, "quality": "weak"}},
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return d


@pytest.fixture
def tech_ready(db):
    """给临时库补一条技术派生，让「全维命中」名副其实。"""
    res = registry.save_technical_derivative({
        "code": "002086", "bar_date": TRADE_DATE, "source": "tencent:qfq",
        "status": "ok", "close": 3.42, "high": 3.42, "low": 3.10,
        "ma5": 3.10, "ma10": 2.95, "ma20": 2.80,
        "ret_3d": 12.0, "ret_5d": 20.0, "ret_10d": 31.0,
        "ma_bullish": 1, "volume_ratio_5": 1.42, "data_quality": [],
    }, db_path=db)
    assert res["ok"] is True
    return db


def _view(code="002086", **kw):
    kw.setdefault("db_path", None)
    return sp.build_profile(code, **kw)


# ── 基本装配 ─────────────────────────────────────────────────────────

def test_full_view_assembles_all_dims(tech_ready, gene_dir, track_dir):
    v = sp.build_profile("002086", db_path=tech_ready, gene_dir=gene_dir, track_dir=track_dir)
    assert v["schema"] == sp.PROFILE_SCHEMA
    assert v["found"] is True
    assert v["identity"]["name"] == "东方海洋"
    assert v["today"]["is_limit_up_basis"] == "close"
    assert v["today"]["boards"] == 4
    assert v["gene"]["grade"] == "连板龙"
    assert v["board"]["boards"] == 4
    assert v["technical"]["ma_bullish"] == 1
    assert v["_quality"]["observed"] == v["_quality"]["total"]
    assert v["_quality"]["missing_dims"] == []


def test_themes_and_industry_are_separated(db, gene_dir, track_dir):
    """⛔ 题材≠行业：混列会让行业冒充题材污染主线判断。"""
    v = sp.build_profile("002086", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert set(v["themes"]["themes"]) == {"大消费", "大农业"}
    assert "农业种植" in v["themes"]["industry"]
    assert "农业种植" not in v["themes"]["themes"]


def test_history_series_is_ascending(db, gene_dir, track_dir):
    v = sp.build_profile("002086", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    dates = [h["trade_date"] for h in v["history"]]
    assert dates == sorted(dates)
    assert dates[-1] == TRADE_DATE


# ── 缺失语义 ─────────────────────────────────────────────────────────

def test_missing_technical_is_missing_not_zero(db, gene_dir, track_dir):
    """未算技术派生 → missing，⛔ 不返回一组 0 当「均线全破」。"""
    v = sp.build_profile("002086", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert v["technical"] is None
    assert v["_sources"]["technical"]["status"] == "missing"
    assert "technical" in v["_quality"]["missing_dims"]


def test_unregistered_code_is_not_found_but_shape_intact(db, gene_dir, track_dir):
    v = sp.build_profile("600000", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert v["found"] is False
    assert v["identity"] is None
    assert v["_sources"]["identity"]["status"] == "missing"
    # 视图仍是完整结构，不抛异常、不缺键
    for dim in sp.dims():
        assert dim.key in v


def test_missing_gene_is_unknown_not_weak(db, track_dir):
    """⛔ 缺档案 = 不知道，绝不能渲染成「难连板」。"""
    v = sp.build_profile("000995", db_path=db, gene_dir=tmp_gene_empty(db), track_dir=track_dir)
    assert v["gene"] is None
    meta = v["_sources"]["gene"]
    assert meta["status"] == "missing"
    assert "未知" in meta["reason"] and "难连板" in meta["reason"]


def tmp_gene_empty(db):
    """空股性目录（不建任何 hist_ 文件）。"""
    d = db.parent / "empty_genes"
    d.mkdir(exist_ok=True)
    return d


def test_not_in_board_track_is_not_a_limit_down_claim(db, gene_dir, track_dir):
    """不在连板台账 = 未进入跟踪（2 板以下），⛔ 不等于今天没涨停。"""
    v = sp.build_profile("000995", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert v["board"] is None
    meta = v["_sources"]["board"]
    assert meta["status"] == "missing"
    assert "不是「今天没涨停」" in meta["reason"]


def test_broken_stock_is_marked_broken_not_missing(db, gene_dir, track_dir):
    v = sp.build_profile("000525", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert v["board"]["in_track"] is False
    assert v["board"]["broken"]["prev_boards"] == 2
    assert v["_sources"]["board"]["status"] == "ok"


def test_stale_when_daily_snapshot_before_latest_trading_day(db, gene_dir, track_dir, monkeypatch):
    monkeypatch.setattr(sp, "_latest_trading_day", lambda: "2026-11-04")
    v = sp.build_profile("002086", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert v["today"]["is_latest_trading_day"] is False
    assert v["_sources"]["today"]["status"] == "stale"
    # stale 仍保留原值，不静默丢成 None
    assert v["today"]["boards"] == 4


def test_missing_numeric_fields_stay_none_not_zero(db, gene_dir, track_dir):
    """⛔ 未观测不能写成 0：「没取到」与「确认是 0」是两件事。"""
    v = sp.build_profile("000995", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert v["today"]["seal_yi"] is None
    assert v["today"]["hs"] is None


# ── 轨迹与变化事实 ───────────────────────────────────────────────────

def test_board_timeline_reports_change_facts_without_verdict(db, gene_dir, track_dir):
    v = sp.build_profile("002086", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    tl = v["board_timeline"]
    assert tl["days"] == 2
    ch = tl["changes"]
    assert ch["seal_ratio_delta_pp"] == pytest.approx(-1.36)
    assert ch["zbc_delta"] == pytest.approx(2.0)
    assert ch["mates_delta"] == pytest.approx(0.0)
    # 只给事实，⛔ 不出现裁决性字段
    joined = json.dumps(ch, ensure_ascii=False)
    for banned in ("卖出", "信号", "见顶", "risk", "action"):
        assert banned not in joined


def test_track_not_in_current_is_state_not_stale(db, gene_dir, track_dir, tmp_path, monkeypatch):
    """该票不在最新台账 = 它断板/降为首板（状态），⛔ 不是数据过期（stale）。

    600241 实测：10-12 在册 6 板，当日其实是首板。若按「末次在册日 < 台账最新日」
    判 stale，就会把一堆首板票标成「数据坏了」。
    """
    monkeypatch.setattr(sp, "_latest_trading_day", lambda: "2026-11-03")
    one = tmp_path / "gone_track"
    one.mkdir()
    (one / "track_2026-10-12.json").write_text(json.dumps({
        "date": "2026-10-12",
        "tracked": [{"code": "002086", "boards": 6, "seal_ratio_pct": 0.81,
                     "zbc": 3, "mates": 1, "seal_yi": 0.5}],
    }, ensure_ascii=False), encoding="utf-8")
    (one / "track_2026-11-03.json").write_text(json.dumps({
        "date": "2026-11-03", "tracked": [],
    }, ensure_ascii=False), encoding="utf-8")

    v = sp.build_profile("002086", db_path=db, gene_dir=gene_dir, track_dir=one)
    assert v["board_timeline"]["last_date"] == "2026-10-12"
    assert v["board_timeline"]["is_current"] is False
    assert v["_sources"]["board_timeline"]["status"] == "ok"
    assert "未入册" in v["_sources"]["board_timeline"]["reason"]
    assert "不代表当前" in v["_sources"]["board_timeline"]["reason"]
    assert v["_sources"]["board"]["status"] == "ok"
    # 当前态必须以 today 为准，⛔ 不能拿轨迹末尾冒充
    assert v["today"]["boards"] == 4


def test_track_lagging_globally_is_stale(db, gene_dir, track_dir, monkeypatch):
    """台账本身落后于最新交易日 → stale（这才是数据过期）。"""
    monkeypatch.setattr(sp, "_latest_trading_day", lambda: "2026-11-06")
    v = sp.build_profile("002086", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert v["_sources"]["board"]["status"] == "stale"
    assert "落后于最新交易日" in v["_sources"]["board"]["reason"]
    assert v["_sources"]["board_timeline"]["status"] == "stale"
    assert v["board_timeline"]["is_current"] is False


def test_single_day_track_has_no_changes(db, gene_dir, track_dir, tmp_path):
    """只有一天在册 → changes 为 None，⛔ 不造一个「0 变化」当证据。"""
    one = tmp_path / "one_day"
    one.mkdir()
    (one / "track_2026-11-03.json").write_text(
        (track_dir / "track_2026-11-03.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    v = sp.build_profile("002086", db_path=db, gene_dir=gene_dir, track_dir=one)
    assert v["board_timeline"]["days"] == 1
    assert v["board_timeline"]["changes"] is None


# ── 批量与单个一致（同一份维度声明）────────────────────────────────────

def test_batch_equals_single(db, gene_dir, track_dir):
    batch = sp.build_profiles(["002086", "000995"], db_path=db,
                           gene_dir=gene_dir, track_dir=track_dir)
    for code in ("002086", "000995"):
        single = sp.build_profile(code, db_path=db, gene_dir=gene_dir, track_dir=track_dir)
        assert batch["items"][code] == single


def test_batch_dedups_and_reports_invalid(db, gene_dir, track_dir):
    res = sp.build_profiles(["002086", "002086", "abc", "600000.SH", ""],
                         db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert res["count"] == 1
    assert res["invalid"] == ["abc", "600000.SH", ""]


# ── 开关与只读 ───────────────────────────────────────────────────────

def test_disabled_returns_no_sources(db, monkeypatch):
    monkeypatch.setenv("ENABLE_STOCK_PROFILE", "0")
    res = sp.build_profiles(["002086"], db_path=db)
    assert res["enabled"] is False
    assert res["items"] == {}
    assert "disabled_reason" in res


def test_read_only_does_not_touch_any_file(db, gene_dir, track_dir):
    """聚合层是纯读：跑一遍后源文件 mtime 与大小都不变。"""
    targets = [db, *sorted(gene_dir.glob("*.json")), *sorted(track_dir.glob("*.json"))]
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in targets}
    sp.build_profiles(["002086", "000995", "000525"], db_path=db,
                   gene_dir=gene_dir, track_dir=track_dir)
    after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in targets}
    assert before == after


def test_missing_registry_db_degrades_not_raises(tmp_path, gene_dir, track_dir):
    v = sp.build_profile("002086", db_path=tmp_path / "nope.db",
                      gene_dir=gene_dir, track_dir=track_dir)
    assert v["found"] is False
    assert v["_sources"]["identity"]["status"] == "missing"


# ── 索引缓存 ─────────────────────────────────────────────────────────

def test_track_index_is_cached_between_calls(track_dir):
    first = sp._load_track_index(track_dir)
    second = sp._load_track_index(track_dir)
    assert first is second          # 同一对象 = 没有重复读盘
    assert first["latest"] == "2026-11-03"
    assert set(first["by_code"]) == {"002086"}


def test_track_index_reloads_when_file_changes(track_dir):
    first = sp._load_track_index(track_dir)
    p = track_dir / "track_2026-11-03.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["tracked"][0]["seal_ratio_pct"] = 1.11
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))
    second = sp._load_track_index(track_dir)
    assert second is not first
    assert second["by_code"]["002086"][-1]["seal_ratio_pct"] == 1.11


# ── 覆盖率 ───────────────────────────────────────────────────────────

def test_quality_grade_reflects_observed_dims(db, gene_dir, track_dir):
    full = sp.build_profile("002086", db_path=db, gene_dir=gene_dir, track_dir=track_dir)
    assert full["_quality"]["grade"] == "high"

    bare = sp.build_profile("002086", db_path=db,
                            gene_dir=tmp_gene_empty(db),
                            track_dir=db.parent / "no_track")
    assert bare["_quality"]["observed"] < bare["_quality"]["total"]
    assert "gene" in bare["_quality"]["missing_dims"]
    assert bare["_quality"]["grade"] != "high"


# ── 查询边界（common.data_gateway 投影）────────────────────────────────

def test_gateway_rejects_non_six_digit_code():
    from common import data_gateway

    for bad in ("abc", "600000.SH", "", None, "6000000"):
        with pytest.raises(ValueError):
            data_gateway.stock_profile(bad)


def test_gateway_reports_disabled_instead_of_raising(monkeypatch):
    """关闭开关是配置状态，⛔ 不该被当成请求错误抛出。"""
    from common import data_gateway

    monkeypatch.setenv("ENABLE_STOCK_PROFILE", "0")
    out = data_gateway.stock_profile("002086")
    assert out["disabled"] is True
    assert out["found"] is False
    assert "ENABLE_STOCK_PROFILE" in out["reason"]
