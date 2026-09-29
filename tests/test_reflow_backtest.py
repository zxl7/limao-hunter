#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回流回测 + 日K层守卫（2026-09-22 立）

锁三件事：
  1. **只读铁律**：`reflow_backtest.py` 不改阈值 / 不推送 / 不碰自动化 / 不改 live_signal.py
     （源码静态断言，防日后被"顺手加个回写"污染）
  2. **口径不变量**：缺失一律 None（⛔ 不填 0）；账本注入必须可测试化
  3. **两个已修静默缺陷的回归锁**：
     · `tencent_kline.tk_market` 的 `920→bj` 死代码（曾被 `9→sh` 分支遮蔽）
     · `decision_audit.fetch_bars` 的「已入缓存即永不刷新」陈旧缺陷（`need_dates`）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
for p in (str(BASE), str(BASE / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import common.tencent_kline as tk          # noqa: E402
import decision_audit as da                # noqa: E402
import reflow_backtest as rb               # noqa: E402

SRC_RB = (BASE / "scripts" / "reflow_backtest.py").read_text(encoding="utf-8")


# ────────────────── 1. 只读铁律（源码静态断言） ──────────────────
@pytest.mark.parametrize("forbidden", [
    "push_card",           # 禁推送
    "requests.post",       # 禁直接发网
    "automation_update",   # 禁碰自动化
    "expectation_rules",   # 禁改预期配置
    "relay_score_config",  # 禁改候选配置
    "learning_config",     # 禁改学习开关
    "yaml.dump",           # 禁写配置
])
def test_reflow_backtest_is_read_only(forbidden: str) -> None:
    assert forbidden not in SRC_RB, f"回流回测不得引用 {forbidden}（只读铁律）"


def test_reflow_backtest_does_not_import_live_signal() -> None:
    """可提「回流前兆」但⛔ 不得 import / 调用该模块（本脚本只读它的落盘产物）。"""
    assert "import live_signal" not in SRC_RB
    assert "from live_signal" not in SRC_RB


def test_reflow_backtest_never_writes_settings() -> None:
    """产物只允许 3 个路径：报告 json/md + 前向账本。"""
    for allowed in ("reflow_backtest.json", "reflow_backtest.md", "reflow_signal_log.jsonl"):
        assert allowed in SRC_RB
    assert "save_close_cache" in SRC_RB          # 日K缓存（与 decision_audit 共用·非配置）
    # ⛔ 不得写出任何 .json 配置到 config/ 或 wealth_code/config/
    assert "config/" not in SRC_RB.replace("wealth_code\" / \"cache", "")


# ────────────────── 2. 日K层：市场前缀（死代码回归锁） ──────────────────
@pytest.mark.parametrize("code,expect", [
    ("600000", "sh"),   # 沪主板
    ("900901", "sh"),   # 沪B股（首位 9 → sh，不得被 920 规则误伤）
    ("688981", "sh"),   # 科创板
    ("000001", "sz"),   # 深主板
    ("300750", "sz"),   # 创业板
    ("920298", "bj"),   # ⚠️ 北交所新段：曾被 "9"→sh 分支遮蔽 → 必为 bj
    ("920128", "bj"),
    ("830799", "bj"),   # 北交所老段
    ("430047", "bj"),
])
def test_tk_market_prefix(code: str, expect: str) -> None:
    assert tk.tk_market(code) == expect


def test_tk_market_920_not_shadowed() -> None:
    """专项锁：920 判定必须出现在 `code[0] in ('6','9')` 之前。"""
    src = (BASE / "common" / "tencent_kline.py").read_text(encoding="utf-8")
    i920 = src.find('startswith("920")')
    i9 = src.find('in ("6", "9")')
    assert i920 != -1 and i9 != -1
    assert i920 < i9, "920→bj 分支被 9→sh 遮蔽（死代码），北交所日K会静默取不到"


# ────────────────── 3. 日K层：缓存陈旧必须刷新（need_dates） ──────────────────
def test_fetch_bars_refreshes_stale_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """已入缓存但缺所需日期 → 必须重取；否则 t+1/t+3 口径静默变 None。"""
    fetched: list[str] = []

    def fake_ohlc(code: str, n: int = 80, timeout: int = 10):
        fetched.append(code)
        return [{"date": "2026-09-22", "open": 10.0, "close": 11.0},
                {"date": "2026-09-23", "open": 11.0, "close": 12.0}]

    monkeypatch.setattr(tk, "fetch_kline_ohlc", fake_ohlc)
    cache = {"000823": {"20260918": {"o": 1.0, "c": 2.0}}}      # 陈旧：停在 09-18
    out, failed = da.fetch_bars(["000823"], cache, allow_fetch=True,
                               need_dates={"20260922"})
    assert "000823" in fetched, "陈旧缓存未被重取（静默陈旧缺陷回归）"
    assert "20260922" in out["000823"]
    assert failed == []


def test_fetch_bars_skips_complete_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """日期齐全 → 不重取（防 need_dates 变成天天全量拉取）。"""
    fetched: list[str] = []
    monkeypatch.setattr(tk, "fetch_kline_ohlc",
                        lambda code, n=80, timeout=10: fetched.append(code) or [])
    cache = {"000823": {"20260922": {"o": 1.0, "c": 2.0}}}
    da.fetch_bars(["000823"], cache, allow_fetch=True, need_dates={"20260922"})
    assert fetched == []


def test_fetch_bars_backward_compatible() -> None:
    """need_dates 缺省 = 旧行为（已在缓存即跳过），保证既有调用方不受影响。"""
    cache = {"000823": {"20260918": {"o": 1.0, "c": 2.0}}}
    out, failed = da.fetch_bars(["000823"], cache, allow_fetch=False)
    assert failed == []
    assert "20260922" not in out["000823"]


# ────────────────── 4. 信号重建与解析 ──────────────────
def test_split_sig_map_parses_board_and_codes() -> None:
    board, codes = rb._split_sig_map("光通信模块#000070.SZ|002281.SZ|600487.SH|603042.SH")
    assert board == "光通信模块"
    assert codes == ["000070", "002281", "600487", "603042"]


def test_split_sig_map_without_board_is_not_gap() -> None:
    """无 "#" = 未命中当日资金榜（board 空串），不是数据缺口。"""
    board, codes = rb._split_sig_map("#000823.SZ|002080.SZ")
    assert board == ""
    assert codes == ["000823", "002080"]
    assert rb._split_sig_map("") == ("", [])


@pytest.mark.parametrize("hhmm,expect", [
    (915, "pre"), (924, "pre"), (925, "am"), (1129, "am"), (1130, "am"),
    (1300, "pm"), (1403, "pm"), (1452, "pm"),
])
def test_bucket_of(hhmm: int, expect: str) -> None:
    assert rb.bucket_of(hhmm) == expect


# ────────────────── 5. 账本幂等 + 注入（防写穿生产） ──────────────────
def test_append_ledger_idempotent_and_injectable(tmp_path: Path) -> None:
    p = tmp_path / "ledger.jsonl"
    sigs = [{"signal_date": "2026-09-22", "theme": "电子布", "hhmm": 915, "codes": []},
            {"signal_date": "2026-09-22", "theme": "光通信", "hhmm": 1016, "codes": []}]
    rb.append_ledger(sigs, path=p)
    rb.append_ledger(sigs, path=p)                       # 幂等：同 date+theme 不重复
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert {json.loads(x)["theme"] for x in lines} == {"电子布", "光通信"}


def test_append_ledger_does_not_touch_production_by_default(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """测试期必须能完全绕开生产账本（对齐 zt_archive 的注入铁律）。"""
    proxy = tmp_path / "prod_proxy.jsonl"
    monkeypatch.setattr(rb, "LEDGER", proxy)
    rb.append_ledger([{"signal_date": "2026-09-22", "theme": "X", "hhmm": 915, "codes": []}])
    assert proxy.exists()


# ────────────────── 6. 真值口径：缺失一律 None（⛔ 不填 0） ──────────────────
D0, D1 = "2026-09-18", "20260921"
K0, K1 = "20260918", "20260921"        # ⚠️ 涨停池字典键 = **无横线 8 位**（与日K层对齐）


def _mk_cache() -> dict:
    """D 日 09-18；D+1 日 = 09-21（日历里 09-18 的下一个交易日）。
    000001 两天齐全；**000002 缺 D+1** → 其次日口径必须 None。"""
    return {
        "000001": {"20260918": {"o": 10.0, "c": 10.0},
                   "20260921": {"o": 11.0, "c": 11.0}},
        "000002": {"20260918": {"o": 5.0, "c": 5.0}},
    }


def test_attach_truth_missing_is_none_not_zero() -> None:
    cache = _mk_cache()
    cal = ["20260918", "20260921"]
    row = {"signal_date": D0, "theme": "T", "hhmm": 1016,
           "codes": ["000001", "000002"], "_cand_codes": []}
    rb.attach_truth(row, cache, cal, {})
    per = {p["code"]: p for p in row["pool"]}
    assert per["000001"]["ret_d1"] == 10.0                 # 10.0 → 11.0
    assert per["000002"]["ret_d1"] is None                 # ⛔ 不是 0
    assert row["pool_ret_d1"] == 10.0                      # 只对可用值求均值
    # 无基准样本 → 基准与超额均 None（⛔ 不填 0）
    assert row["base_cand_ret_d1"] is None
    assert row["base_zt_ret_d1"] is None
    assert row["excess_cand_d1"] is None
    assert row["excess_zt_d1"] is None


def test_attach_truth_baselines_and_excess() -> None:
    cache = _mk_cache()
    cal = ["20260918", "20260921"]
    row = {"signal_date": D0, "theme": "T", "hhmm": 1016,
           "codes": ["000002"], "_cand_codes": ["000001"]}
    rb.attach_truth(row, cache, cal, {K0: {"000001", "000002"}})
    assert row["pool_ret_d1"] is None                      # 池缺 D+1
    assert row["base_cand_ret_d1"] == 10.0                 # 候选基准可用
    assert row["base_zt_ret_d1"] == 10.0                   # 涨停池基准只算可用票
    assert row["base_zt_n"] == 1                           # 000002 无 D+1 → 不计入
    assert row["excess_cand_d1"] is None                   # 池缺失 → 超额必 None
    assert row["excess_zt_d1"] is None
    assert row["pool_zt_d1_n"] == 0                        # 无涨停
    assert row["pool_zt_d1_known"] == 0                    # ⚠️ 且"可判定数=0"（归档缺失≠未涨停）


def test_attach_truth_excess_is_diff_from_baseline() -> None:
    """有基准时超额 = 池内 − 基准（不是绝对收益）。"""
    cache = _mk_cache()
    cal = ["20260918", "20260921"]
    row = {"signal_date": D0, "theme": "T", "hhmm": 1016,
           "codes": ["000001"], "_cand_codes": ["000002"]}
    # 000002 的 D+1 缺失 → 候选基准无可用样本 → 超额 None
    rb.attach_truth(row, cache, cal, {})
    assert row["pool_ret_d1"] == 10.0
    assert row["base_cand_ret_d1"] is None
    assert row["excess_cand_d1"] is None


def test_attach_truth_zt_flags() -> None:
    cache = _mk_cache()
    cal = ["20260918", "20260921"]
    row = {"signal_date": D0, "theme": "T", "hhmm": 915,
           "codes": ["000001"], "_cand_codes": []}
    rb.attach_truth(row, cache, cal, {K0: {"000001"}, K1: set()})
    assert row["pool"][0]["zt_d0"] is True
    assert row["pool"][0]["zt_d1"] is False
    assert row["pool_zt_d0_n"] == 1
    assert row["pool_zt_d0_known"] == 1
    assert row["pool_zt_d1_n"] == 0
    assert row["pool_zt_d1_known"] == 1                    # 归档在 → 可判定


def test_zt_codes_key_format_is_undashed() -> None:
    """⚠️ 格式锁：`load_zt_codes` 的键必须是无横线 8 位。

    日K层的日期（`kline_calendar`/`nth_next`）是无横线格式，而 `zt_list` 归档的 `date`
    字段是带横线格式。两者混用曾使 `dp1 in zt` 恒 False ⇒ **次日涨停率静默全空**。
    """
    zt = rb.load_zt_codes()
    assert zt, "涨停池归档为空 → 无法校验键格式"
    for k in zt:
        assert len(k) == 8 and k.isdigit(), f"涨停池键非无横线 8 位：{k!r}"
        assert "-" not in k


def test_cand_codes_key_format_is_undashed() -> None:
    cand = rb.load_cand_codes()
    if not cand:
        pytest.skip("无 candidates 文件")
    for k in cand:
        assert len(k) == 8 and k.isdigit(), f"候选池键非无横线 8 位：{k!r}"


def test_d8_normalizes() -> None:
    assert rb._d8("2026-09-22") == "20260922"
    assert rb._d8("20260922") == "20260922"
    assert rb._d8(None) is None
    assert rb._d8("") is None


def test_zt_flag_is_none_when_archive_missing() -> None:
    """⛔ 涨停池归档缺失 ⇒ 判定为 None（未知），**不得**记 False（未涨停）。"""
    cache = _mk_cache()
    cal = ["20260918", "20260921"]
    row = {"signal_date": D0, "theme": "T", "hhmm": 915,
           "codes": ["000001"], "_cand_codes": []}
    rb.attach_truth(row, cache, cal, {})                   # 两日归档均缺
    assert row["pool"][0]["zt_d0"] is None
    assert row["pool"][0]["zt_d1"] is None
    assert row["pool_zt_d0_known"] == 0
    assert row["pool_zt_d1_known"] == 0


# ────────────────── 7. 钝化标记 ──────────────────
def test_mark_repeat_flags_consecutive_days() -> None:
    rows = [
        {"theme": "电子布", "signal_date": "2026-09-15"},
        {"theme": "电子布", "signal_date": "2026-09-16"},
        {"theme": "电子布", "signal_date": "2026-09-17"},
        {"theme": "光通信", "signal_date": "2026-09-17"},
    ]
    rb.mark_repeat(rows)
    assert rows[0]["is_repeat"] is False            # 首次
    assert rows[1]["is_repeat"] is True             # 前一日刚发过
    assert rows[2]["is_repeat"] is True
    assert rows[3]["is_repeat"] is False            # 新题材
    assert rows[2]["theme_all_days"] == 3


def test_dull_candidates_threshold() -> None:
    rows = [{"theme": "电子布", "signal_date": d, "hhmm": 915, "codes": [],
             "n_pool": 0} for d in ("2026-09-15", "2026-09-16", "2026-09-17")]
    rows.append({"theme": "一次性题材", "signal_date": "2026-09-18", "hhmm": 915,
                 "codes": [], "n_pool": 0})
    res = rb.build_report(rows)
    dull = {d["theme"]: d["days"] for d in res["dull_candidates"]}
    assert dull == {"电子布": 3}


def test_report_flags_low_sample() -> None:
    rows = [{"theme": "T", "signal_date": "2026-09-22", "hhmm": 915, "codes": [],
             "n_pool": 0}]
    res = rb.build_report(rows)
    assert res["windows"]["overall"]["low_sample"] is True
    assert res["thresholds"]["LOW_SAMPLE"] == rb.LOW_SAMPLE
