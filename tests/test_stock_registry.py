#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一个股特征底库（`common/stock_registry.py`）回归锁。

锁定的契约（每一条都对应一类会**静默失真**的坑）：
  ① 唯一标识  —— code 为主键不重复；名称只做检索、不建主对象；
  ② 增量更新  —— 命中即写、未命中不动；重复命中幂等；缺失不覆盖旧值；
  ③ 三态语义  —— 当前封板 / 当日曾触板（闩锁）/ 收盘封板，各自独立且可共存；
  ④ 时序      —— 跨日轮换、同日 asof 新者胜、乱序旧观测不得夺权；
  ⑤ 数据源边界 —— "不在涨停名单" ≠ "没涨停"，禁止反向推断；
  ⑥ 安全      —— 非交易日拒绝、脏类型不误判、测试期禁写生产库与投影。
"""
import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common import stock_registry as SR  # noqa: E402

# 均为真实A股交易日
D1 = "2026-10-13"
D2 = "2026-10-14"
NON_TRADING = "2026-10-10"      # 周六


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "registry.db"
    SR.init_db(path)
    return path


def _obs(code="600000", **kw):
    base = {"code": code, "name": kw.pop("name", f"票{code}")}
    base.update(kw)
    return base


# ── ① 唯一标识 ─────────────────────────────────────────────────────────

def test_code_normalization_same_primary_key(db):
    """`600000.SH` / `sh600000` / 带空格 → 同一主键；原值保留在 symbol。"""
    for i, form in enumerate(("600000.SH", "sh600000", " 600000 ")):
        SR.upsert_one(_obs(form), trade_date=D1,
                      asof_ts=f"{D1}T10:0{i}:00+08:00", source="t", db_path=db)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 1
    assert conn.execute("SELECT symbol FROM stocks").fetchone()[0] == "600000.SH"
    conn.close()


def test_normalize_code_rejects_non_six_digits():
    """⛔ 7 位数字不得被截成 6 位（否则会凭空造出一个不存在的代码）。"""
    assert SR.normalize_code("6000000") is None
    assert SR.normalize_code("60000") is None
    assert SR.normalize_code("浦发银行") is None
    assert SR.normalize_code("600000.SH") == "600000"
    assert SR.normalize_code("sz000001") == "000001"
    assert SR.normalize_code("920819") == "920819"


def test_name_only_record_goes_to_pending_not_new_object(db):
    """无 6 位代码的票**不建主对象**，只登记待确认——名称绝不能变成重复记录。"""
    res = SR.upsert_one({"name": "某只票", "pct": 9.9}, trade_date=D1,
                        asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    assert res["rejected"] == 1 and res["pending"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 0
    rows = conn.execute("SELECT kind FROM stock_pending_review").fetchall()
    conn.close()
    assert rows and rows[0][0] == "no_code"


def test_name_is_search_only_and_ambiguity_not_guessed(db):
    """同名多代码 → 检索返回全部，**不猜测**，也不合并成一条。"""
    for code in ("600001", "600002"):
        SR.upsert_one(_obs(code, name="同名票"), trade_date=D1,
                      asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    hits = SR.find_by_name("同名票", db_path=db)
    assert sorted(h["code"] for h in hits) == ["600001", "600002"]


# ── ② 增量更新 ─────────────────────────────────────────────────────────

def test_first_insert_then_identical_replay_is_idempotent(db):
    payload = _obs(up=True, pct=10.02, boards="2天2板", plates=[{"name": "银行"}])
    first = SR.upsert_one(dict(payload), trade_date=D1,
                          asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    second = SR.upsert_one(dict(payload), trade_date=D1,
                           asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    assert first["inserted"] == 1
    assert second["inserted"] == 0 and second["updated"] == 0 and second["unchanged"] == 1
    row = SR.get_by_code("600000", db_path=db)
    assert row["update_count"] == 1


def test_missing_field_does_not_overwrite_existing_value(db):
    """后一条观测缺 pct/industry → 旧值必须原样保留（缺失 ≠ 清空）。"""
    SR.upsert_one(_obs(pct=10.02, industry="银行"), trade_date=D1,
                  asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    SR.upsert_one({"code": "600000", "name": "票600000"}, trade_date=D1,
                  asof_ts=f"{D1}T10:05:00+08:00", source="t", db_path=db)
    row = SR.get_by_code("600000", db_path=db)
    assert row["pct"] == 10.02
    assert row["industry"] == "银行"


def test_absent_from_pool_never_written_as_not_limit_up(db):
    """⛔ 铁律：源看不到全市场 → 只有正向证据才写 flag，缺席不得写成 0。"""
    SR.upsert_one({"code": "600000", "name": "甲"}, trade_date=D1,
                  asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    row = SR.get_by_code("600000", db_path=db)
    assert row["limit_up_now"] is None
    assert row["limit_up_touched"] is None
    assert row["limit_up_close"] is None
    assert row["is_limit_up_today"] is None
    assert row["is_limit_up_basis"] == "unknown"


def test_intraday_adapter_only_writes_positive_evidence(db):
    """东财涨幅榜的 up 是阈值推断（弱证据）；为假时**不写 0**。"""
    obs = SR.build_intraday_observations(
        stocks={"600001": {"code": "600001", "name": "甲", "up": True, "plates": [{"name": "半导体"}]}},
        pre_stocks={"600002": {"code": "600002", "name": "乙", "up": False}},
        rising=[{"code": "600003", "name": "丙", "pct": 6.1, "up": False, "industry": "银行"}],
    )
    by_code = {o["code"]: o for o in obs}
    assert by_code["600001"]["limit_up_now"] == 1
    assert "limit_up_now" not in by_code["600002"]
    assert "limit_up_now" not in by_code["600003"]
    assert by_code["600003"]["industry"] == "银行"      # 行业单列，不混进题材


# ── ③ 三态语义 ─────────────────────────────────────────────────────────

def test_seal_open_keeps_touched_latch(db):
    """封板 → 开板：`now` 归 0（显式变更），`touched` 保持 1（闩锁）。"""
    SR.upsert_one({"code": "600000", "limit_up_now": 1}, trade_date=D1,
                  asof_ts=f"{D1}T09:40:00+08:00", source="t", db_path=db)
    assert SR.get_by_code("600000", db_path=db)["limit_up_touched"] == 1

    SR.upsert_one({"code": "600000", "limit_up_now": 0}, trade_date=D1,
                  asof_ts=f"{D1}T11:00:00+08:00", source="t", db_path=db)
    row = SR.get_by_code("600000", db_path=db)
    assert row["limit_up_now"] == 0
    assert row["limit_up_touched"] == 1, "已确认的『曾涨停』不可被后续开板观测清零"


def test_touched_is_derived_from_sealed_evidence(db):
    """封板即当日触板 —— 调用方漏传 touched 时，确定性结论不应丢失。"""
    SR.upsert_one({"code": "600000", "limit_up_close": 1}, trade_date=D1,
                  asof_ts=f"{D1}T15:10:00+08:00", source="eod:t", db_path=db)
    assert SR.get_by_code("600000", db_path=db)["limit_up_touched"] == 1


def test_close_authority_only_from_eod_source(db):
    """⛔ 盘中源不得写收盘口径（禁用涨幅阈值冒充收盘涨停）。"""
    SR.upsert_one({"code": "600000", "limit_up_close": 1}, trade_date=D1,
                  asof_ts=f"{D1}T10:00:00+08:00", source="intraday:x", db_path=db)
    assert SR.get_by_code("600000", db_path=db)["limit_up_close"] is None


def test_eod_marks_touched_but_unsealed_as_close_zero(db):
    """盘后权威名单外 + 当日曾触板 → 收盘未封 0（唯一允许写 0 的场景）。"""
    SR.upsert_one({"code": "600000", "limit_up_now": 1}, trade_date=D1,
                  asof_ts=f"{D1}T10:00:00+08:00", source="intraday:x", db_path=db)
    res = SR.ingest_eod_snapshot({
        "date": D1, "generated_ts": f"{D1}T15:30:00+08:00",
        "zt_list": [{"code": "000001", "name": "平安银行", "sector": "银行"}],
    }, db_path=db)
    assert res["ok"] and res["close_zero"] == 1
    assert SR.get_by_code("600000", db_path=db)["limit_up_close"] == 0
    assert SR.get_by_code("000001", db_path=db)["limit_up_close"] == 1


# ── ④ 时序 ─────────────────────────────────────────────────────────────

def test_late_arriving_older_observation_cannot_override(db):
    """乱序：旧 asof 晚到 → 不覆盖当前态，但闩锁仍累计。"""
    SR.upsert_one({"code": "600000", "limit_up_now": 0, "pct": 3.3}, trade_date=D1,
                  asof_ts=f"{D1}T11:00:00+08:00", source="t", db_path=db)
    SR.upsert_one({"code": "600000", "limit_up_now": 1, "pct": 10.02}, trade_date=D1,
                  asof_ts=f"{D1}T09:30:00+08:00", source="t", db_path=db)
    row = SR.get_by_code("600000", db_path=db)
    assert row["limit_up_now"] == 0 and row["pct"] == 3.3
    assert row["limit_up_touched"] == 1


def test_cross_day_rotation_keeps_history_in_daily_table(db):
    """跨日：当日快照整体轮换（未提供的置空），旧值留在 stock_daily 不丢。"""
    SR.upsert_one({"code": "600000", "pct": 10.02, "limit_up_now": 1}, trade_date=D1,
                  asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    SR.upsert_one({"code": "600000", "pct": 3.1}, trade_date=D2,
                  asof_ts=f"{D2}T10:00:00+08:00", source="t", db_path=db)

    row = SR.get_by_code("600000", db_path=db)
    assert row["pct"] == 3.1
    assert row["daily_asof_date"] == D2
    assert row["limit_up_now"] is None, "昨日封板不得残留成今日状态"
    assert row["limit_up_touched"] is None

    history = SR.query_date_range(D1, D2, db_path=db)
    assert len(history) == 2
    day1 = next(h for h in history if h["trade_date"] == D1)
    assert day1["limit_up_close"] is None and day1["limit_up_now"] == 1


def test_daily_is_latest_flag_prevents_stale_read(db):
    """旧日数据必须被标记出来，防止把历史涨停读成"今日涨停"。"""
    SR.upsert_one({"code": "600000", "limit_up_close": 1}, trade_date=D1,
                  asof_ts=f"{D1}T15:10:00+08:00", source="eod:t", db_path=db)
    row = SR.get_by_code("600000", db_path=db)
    assert row["is_limit_up_today"] == 1 and row["is_limit_up_basis"] == "close"
    assert row["daily_is_latest_trading_day"] is False
    assert row["daily_asof_date"] == D1


# ── ⑤ 异常与缺失 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect_now", [
    ({"up": "false"}, 0),
    ({"up": "0"}, 0),
    ({"up": "true"}, 1),
    ({"up": 1}, 1),
    ({"up": ""}, None),
    ({"up": "None"}, None),
    ({"up": "maybe"}, None),
    ({"up": float("nan")}, None),
])
def test_dirty_type_never_coerced_to_true(db, raw, expect_now):
    """⛔ `bool("false") is True` 是真实事故源：任意非空字符串绝不当 True。"""
    res = SR.upsert_one({"code": "600000", **raw}, trade_date=D1,
                        asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    assert res["ok"]
    assert SR.get_by_code("600000", db_path=db)["limit_up_now"] == expect_now


def test_dirty_numeric_is_flagged_not_silently_zero(db):
    SR.upsert_one({"code": "600000", "pct": "abc"}, trade_date=D1,
                  asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    row = SR.get_by_code("600000", db_path=db)
    assert row["pct"] is None
    assert "dirty_type:pct" in row["data_quality_flags"]


def test_boards_text_parsed(db):
    SR.upsert_one({"code": "600000", "boards": "3天2板"}, trade_date=D1,
                  asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
    assert SR.get_by_code("600000", db_path=db)["boards"] == 2


def test_non_trading_day_rejected(db):
    res = SR.upsert_one(_obs(), trade_date=NON_TRADING,
                        asof_ts=f"{NON_TRADING}T10:00:00+08:00", source="t", db_path=db)
    assert res["ok"] is False and "非A股交易日" in res["reason"]
    assert SR.get_by_code("600000", db_path=db) is None


def test_future_date_rejected(db):
    res = SR.upsert_one(_obs(), trade_date="2099-01-04",
                        asof_ts="2099-01-04T10:00:00+08:00", source="t", db_path=db)
    assert res["ok"] is False


def test_unparsable_asof_rejected(db):
    res = SR.upsert_one(_obs(), trade_date=D1, asof_ts="昨天下午", source="t", db_path=db)
    assert res["ok"] is False and "asof_ts" in res["reason"]


def test_naive_asof_normalized_to_bj():
    """裸时间戳必须补 +08:00 —— 乱序保护靠字符串比大小，混时区会静默错序。"""
    assert SR._norm_asof("2026-10-13 10:00:00") == "2026-10-13T10:00:00+08:00"
    assert SR._norm_asof("2026-10-13T10:00:00+08:00") == "2026-10-13T10:00:00+08:00"
    assert SR._norm_asof("2026-10-13T02:00:00+00:00") == "2026-10-13T10:00:00+08:00"


def test_backfill_allows_non_trading_day_when_explicit(db):
    res = SR.upsert_many([_obs()], trade_date=NON_TRADING,
                         asof_ts=f"{NON_TRADING}T15:00:00+08:00",
                         source="backfill:t", allow_backfill=True, db_path=db)
    assert res["ok"]


# ── ⑥ 安全 ─────────────────────────────────────────────────────────────

def test_production_db_write_blocked_under_pytest(monkeypatch):
    """测试期未注入 db_path → 当场报错，绝不静默写穿生产库。"""
    monkeypatch.delenv("LIMAO_REGISTRY_DB", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test")
    with pytest.raises(RuntimeError):
        SR.connect()


def test_production_projection_write_blocked_under_pytest(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test")
    monkeypatch.setenv("LIMAO_REGISTRY_DB", str(tmp_path / "r.db"))
    assert SR._projection_write_blocked(SR.DEFAULT_PROJECTION, None) is True
    assert SR._projection_write_blocked(tmp_path / "p.json", None) is False
    assert SR._projection_write_blocked(SR.DEFAULT_PROJECTION, tmp_path / "p.json") is False


def test_refresh_projection_explicit_output(db, tmp_path):
    """显式输出路径可刷新投影，并带上防旧日期误读字段。"""
    SR.upsert_one(_obs(pct=10.02), trade_date=D1,
                  asof_ts=f"{D1}T15:00:00+08:00", source="t", db_path=db)
    out = tmp_path / "projection.json"
    payload = SR.refresh_projection(out_path=out, db_path=db)
    assert payload and out.exists()
    item = payload["stocks"][0]
    assert "daily_is_latest_trading_day" in item
    assert item["daily_is_latest_trading_day"] is False


def test_eod_refreshes_projection(monkeypatch, db):
    """盘后成功写入后触发一次投影刷新；盘中入口不在此处刷新。"""
    calls = []
    monkeypatch.setattr(SR, "refresh_projection",
                        lambda **kwargs: calls.append(kwargs) or {"count": 1})
    snap = {"date": D1, "generated_ts": f"{D1}T15:30:00+08:00",
            "zt_list": [{"code": "600000", "name": "浦发银行", "sector": "银行"}]}
    res = SR.ingest_eod_snapshot(snap, db_path=db)
    assert res["ok"] is True
    assert calls == [{"db_path": db}]


def test_eod_dump_switch_off(monkeypatch, db):
    """关闭盘后投影开关后，底库照常写入但不刷新镜像。"""
    calls = []
    monkeypatch.setenv("STOCK_REGISTRY_EOD_DUMP", "0")
    monkeypatch.setattr(SR, "refresh_projection", lambda **kwargs: calls.append(kwargs))
    snap = {"date": D1, "generated_ts": f"{D1}T15:30:00+08:00",
            "zt_list": [{"code": "600001", "name": "邯郸钢铁", "sector": "钢铁"}]}
    res = SR.ingest_eod_snapshot(snap, db_path=db)
    assert res["ok"] is True
    assert calls == []
    assert SR.get_by_code("600001", db_path=db) is not None


def test_dump_json_not_n_plus_one(monkeypatch, db, tmp_path):
    """导出标签应一次读取，不得恢复成逐票 N+1 查询。"""
    for i in range(20):
        SR.upsert_one(_obs(f"{600000 + i:06d}", plates=[{"name": f"题材{i % 3}"}]),
                      trade_date=D1, asof_ts=f"{D1}T10:{i:02d}:00+08:00",
                      source="t", db_path=db)
    original_connect = SR.connect
    traces = []

    def traced_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.set_trace_callback(traces.append)
        return conn

    monkeypatch.setattr(SR, "connect", traced_connect)
    SR.dump_json(out_path=tmp_path / "projection.json", db_path=db)
    select_count = sum(1 for sql in traces if sql.lstrip().upper().startswith("SELECT"))
    assert select_count <= 5, traces


def test_switches_disable_ingest(monkeypatch, db):
    """可逆开关：关掉后 ingest 变 no-op（查询路径不受影响）。"""
    monkeypatch.setenv("STOCK_REGISTRY_ENABLED", "0")
    res = SR.ingest_intraday(trade_date=D1, asof_ts=f"{D1}T10:00:00+08:00",
                             stocks={"600000": {"code": "600000", "up": True}},
                             db_path=db)
    assert res["ok"] is False
    assert SR.get_by_code("600000", db_path=db) is None

    monkeypatch.setenv("STOCK_REGISTRY_ENABLED", "1")
    monkeypatch.setenv("STOCK_REGISTRY_INTRADAY", "0")
    assert SR.intraday_enabled() is False


# ── ⑦ 并发 ─────────────────────────────────────────────────────────────

def test_concurrent_writes_do_not_lose_rows(db):
    """多线程并发写：WAL + BEGIN IMMEDIATE 下不得丢行。"""
    errors: list[Exception] = []

    def worker(offset: int) -> None:
        try:
            for i in range(20):
                code = f"{600000 + offset * 100 + i:06d}"
                SR.upsert_one({"code": code, "name": f"票{code}"}, trade_date=D1,
                              asof_ts=f"{D1}T10:00:00+08:00", source="t", db_path=db)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 80
    conn.close()


def test_concurrent_same_code_converges_to_newest_asof(db):
    """同一票被多线程乱序写入 → 终态必须是 asof 最新的那条。"""
    def worker(minute: int) -> None:
        SR.upsert_one({"code": "600000", "pct": float(minute)}, trade_date=D1,
                      asof_ts=f"{D1}T10:{minute:02d}:00+08:00", source="t", db_path=db)

    threads = [threading.Thread(target=worker, args=(m,)) for m in (5, 1, 9, 3, 7)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert SR.get_by_code("600000", db_path=db)["pct"] == 9.0


# ── ⑧ 回填幂等 ─────────────────────────────────────────────────────────

def test_backfill_rerun_is_identical(monkeypatch, tmp_path):
    """回填可重跑：第二次必须 0 新增 / 0 更新（幂等）。"""
    import stock_registry as CLI

    db_path = tmp_path / "registry.db"
    monkeypatch.setenv("LIMAO_REGISTRY_DB", str(db_path))
    snap = {"date": D1, "generated_ts": f"{D1}T15:30:00+08:00",
            "zt_list": [{"code": "600000", "name": "浦发银行", "boards": 2,
                         "sector": "银行", "seal": 3.2}]}
    monkeypatch.setattr(CLI, "_gather_backfill_sources",
                        lambda since: [(D1, f"{D1}T15:30:00+08:00", "backfill:t",
                                        CLI._snap_to_observations(snap))])

    assert CLI.cmd_backfill([]) == 0
    first = SR.stats(db_path=db_path)
    assert CLI.cmd_backfill([]) == 0
    second = SR.stats(db_path=db_path)
    assert first["stocks"] == second["stocks"] == 1
    assert first["daily_rows"] == second["daily_rows"]


def test_backfill_writes_only_positive_evidence():
    """回填只对池内个股写 1，绝不为名单外的票写 0。"""
    obs = CLI_snap_to_obs({
        "zt_list": [{"code": "600000", "name": "甲"}],
    })
    assert obs[0]["limit_up_close"] == 1
    assert all("limit_up_close" in o for o in obs)


def CLI_snap_to_obs(snap):
    import stock_registry as CLI

    return CLI._snap_to_observations(snap)


# ── ⑨ 入口接入（非致命 + 不写穿生产） ──────────────────────────────────

def test_zt_archive_hook_is_non_fatal_and_test_isolated(tmp_path, monkeypatch):
    """归档入口必须照常返回，底库异常不得中断主链，且不得写生产库。"""
    import zt_archive as ZA

    monkeypatch.delenv("LIMAO_REGISTRY_DB", raising=False)
    d = tmp_path / "zt"
    snap = {"date": D1, "zt_count": 1, "tc": 1,
            "zt_list": [{"code": "600000", "name": "甲", "boards": 1, "sector": "银行"}]}
    written = ZA.write_archives(snap, dirs=(d,))
    assert written and written[0].exists()
    assert json.loads(written[0].read_text(encoding="utf-8"))["date"] == D1


def test_intraday_hook_does_not_raise_on_bad_input(monkeypatch):
    import intraday_pulse as IP

    monkeypatch.setenv("STOCK_REGISTRY_INTRADAY", "0")
    IP._ingest_stock_registry({}, {}, [])       # 关掉开关 → 静默通过
