"""账户净值轨补结算（`scripts/backfill_account_nav.py`）的回归锁。

## 背景（2026-11-09 查出的 P3）

`account_nav_history.jsonl` 从 10-21 之后**结构性停更**：结算链
`run_wealth_code._finalize_prev_batch` 只结算「最近一个批次」，且写行要求
`next_day.status == "covered"`；抓价失败一次就永久丢（第二天批次换成新的，旧的永不重试）。

本脚本是事后补档：拿已定稿的台账数字补缺行。**口径已验证**——
`account_nav.avg_return_pct == pool_ledger.summary.auction_buy_close_sell.avg`
（竞价价买 → 当日收盘、可成交票等权），10-12 / 10-14 / 10-21 三天逐位一致。

## 本文件锁死五条

1. 已存在的 `trade_date` **不覆盖**（不改历史已定稿数字）。
2. **只允许补末尾**；缺口夹在中间 → 报错退出，⛔ 不连带改后面既有行的 nav。
3. `nav` 从最后一行复利递推，小数 6 位。
4. 空仓日（`n=0`）不记行；`caliber` 留空 + `source=pool_ledger_backfill` 留痕（不冒充 ai）。
5. 幂等：连跑两次不产生重复行。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def m(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "backfill_account_nav", ROOT / "scripts" / "backfill_account_nav.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                          # type: ignore[union-attr]

    nav = tmp_path / "account_nav.jsonl"
    led = tmp_path / "pool_ledger"
    led.mkdir()
    monkeypatch.setattr(mod, "NAV", nav)
    monkeypatch.setattr(mod, "LEDGER_DIR", led)
    monkeypatch.setattr(mod, "_prev_trading_day", lambda d: f"prev-{d}")
    return mod


def _nav_line(date: str, nav: float, avg: float = 0.0, n: int = 1) -> str:
    return json.dumps({"trade_date": date, "recommendation_date": f"prev-{date}",
                       "strategy": "equal_weight_next_day_close", "caliber": "ai",
                       "note": "", "stock_count": n, "avg_return_pct": avg,
                       "nav": nav, "codes": ["000001"], "names": ["甲"],
                       "stocks": [{"code": "000001", "name": "甲", "return_pct": avg}]},
                      ensure_ascii=False)


def _ledger(m, date: str, avg: float, n: int = 1, code: str = "000001", name: str = "甲") -> None:
    led = {"date": date,
           "summary": {"auction_buy_close_sell": {"n": n, "avg": avg}},
           "rows": [{"code": code, "name": name, "au_ret": avg, "tradable": True}] * n}
    (m.LEDGER_DIR / f"{date}.json").write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")


def test_fills_tail_rows_and_compounds_nav(m):
    m.NAV.write_text(_nav_line("2026-10-21", 0.846046) + "\n", encoding="utf-8")
    _ledger(m, "2026-10-22", -4.18, n=2)
    _ledger(m, "2026-10-23", +2.62)

    rows, err = m.plan(m._read_nav(), m._read_ledgers())
    assert err is None
    assert [r["trade_date"] for r in rows] == ["2026-10-22", "2026-10-23"]
    # 0.846046 × (1 − 4.18%) = 0.810681…
    assert rows[0]["nav"] == pytest.approx(0.846046 * 0.9582, abs=1e-6)
    # 再 × (1 + 2.62%)
    assert rows[1]["nav"] == pytest.approx(rows[0]["nav"] * 1.0262, abs=1e-6)
    assert rows[1]["stock_count"] == 1
    assert rows[0]["stock_count"] == 2


def test_existing_rows_never_touched(m):
    m.NAV.write_text(_nav_line("2026-10-21", 0.846046) + "\n", encoding="utf-8")
    before = m.NAV.read_text(encoding="utf-8")
    _ledger(m, "2026-10-21", +99.0)                      # 已有日期
    _ledger(m, "2026-10-22", -1.0)

    rows, err = m.plan(m._read_nav(), m._read_ledgers())
    assert err is None
    assert [r["trade_date"] for r in rows] == ["2026-10-22"]   # 10-21 不重写
    # 基准仍用既有行的 nav，不受台账 99.0 影响
    assert rows[0]["nav"] == pytest.approx(0.846046 * 0.99, abs=1e-6)
    assert before == m.NAV.read_text(encoding="utf-8")


def test_refuses_when_gap_in_the_middle(m):
    """缺口夹在中间 → ⛔ 拒绝（会连带改后面既有行的 nav）。"""
    m.NAV.write_text(_nav_line("2026-10-20", 0.9) + "\n"
                     + _nav_line("2026-10-22", 0.8) + "\n", encoding="utf-8")
    _ledger(m, "2026-10-21", +1.0)

    rows, err = m.plan(m._read_nav(), m._read_ledgers())
    assert rows == []
    assert err and "缺口不在末尾" in err


def test_empty_ledger_days_are_skipped(m):
    """空仓日（n=0）不记行 —— 与 nav_ledger 同语义。"""
    m.NAV.write_text(_nav_line("2026-10-21", 0.846046) + "\n", encoding="utf-8")
    _ledger(m, "2026-10-22", 0.0, n=0)

    rows, err = m.plan(m._read_nav(), m._read_ledgers())
    assert err is None and rows == []


def test_backfilled_rows_do_not_fake_caliber(m):
    """补出来的行 caliber 空 + source 留痕（⛔ 不冒充 ai / mechanical）。"""
    m.NAV.write_text(_nav_line("2026-10-21", 0.846046) + "\n", encoding="utf-8")
    _ledger(m, "2026-10-22", -1.0)

    rows, _ = m.plan(m._read_nav(), m._read_ledgers())
    assert rows[0]["caliber"] == ""
    assert rows[0]["source"] == "pool_ledger_backfill"
    assert rows[0]["strategy"] == m.STRATEGY


def test_check_mode_writes_nothing(m, monkeypatch, capsys):
    m.NAV.write_text(_nav_line("2026-10-21", 0.846046) + "\n", encoding="utf-8")
    before = m.NAV.read_text(encoding="utf-8")
    _ledger(m, "2026-10-22", -1.0)

    monkeypatch.setattr("sys.argv", ["backfill_account_nav.py", "--check"])
    assert m.main() == 0
    assert m.NAV.read_text(encoding="utf-8") == before
    assert "--check 未落盘" in capsys.readouterr().out


def test_idempotent_across_two_runs(m, monkeypatch):
    m.NAV.write_text(_nav_line("2026-10-21", 0.846046) + "\n", encoding="utf-8")
    _ledger(m, "2026-10-22", -1.0)
    _ledger(m, "2026-10-23", +2.0)

    monkeypatch.setattr("sys.argv", ["backfill_account_nav.py"])
    assert m.main() == 0
    first = m.NAV.read_text(encoding="utf-8")
    assert m.main() == 0                                      # 第二次
    assert m.NAV.read_text(encoding="utf-8") == first          # ⛔ 不重复追加
    assert len([x for x in first.splitlines() if x.strip()]) == 3


def test_no_base_nav_means_refuse(m):
    """净值文件不存在 → ⛔ 不凭空起一条新序列。"""
    rows, err = m.plan([], {"2026-10-22": {"n": 1, "avg": 1.0, "stocks": []}})
    assert rows == [] and err and "无基准" in err


def test_returns_zero_when_no_gap(m, monkeypatch, capsys):
    m.NAV.write_text(_nav_line("2026-10-21", 0.846046) + "\n", encoding="utf-8")
    _ledger(m, "2026-10-21", +1.0)

    monkeypatch.setattr("sys.argv", ["backfill_account_nav.py"])
    assert m.main() == 0
    assert "无需补行" in capsys.readouterr().out
