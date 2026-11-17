"""T+1 台账回补（`pool_ledger.py --backfill-t1`）的回归锁。

## 为什么需要它（2026-11-09 查出的 P1）

16:00 的台账任务只跑「当日」，而 D 日的 `t1_ret` 要等 **D+1 的收盘价**才成立。
写 D 日台账那一刻 D+1 还没开盘 ⇒ 最新一天必然 `n=0`，且**永远不会自动补上**。
实测 11-06 一直 `T+1 n=0`，其余天都靠人工跑过 `--days 12`。

## 本文件锁死四条语义

1. **只填空**：已有数值的 `t1_ret` 绝不被覆盖（不改历史已定稿数字）。
2. **不碰主口径**：`au_ret` / `by_dev_band` / `by_view` 一律原样。
3. **缺次日 K 线**→ 保持 `None` 并计入待补（⛔ 不填 0、不拿当日收盘顶）。
4. **汇总只算可成交票**（`tradable=True`），与 `build()` 同口径。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def pl(monkeypatch, tmp_path):
    """加载 pool_ledger 并把三个路径全部指向 tmp（⛔ 绝不碰真实 cache）。"""
    spec = importlib.util.spec_from_file_location("pool_ledger", ROOT / "scripts" / "pool_ledger.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                      # type: ignore[union-attr]

    kline = tmp_path / "kline"
    out = tmp_path / "ledger"
    pool = tmp_path / "pool"
    kline.mkdir()
    out.mkdir()
    pool.mkdir()
    # ⛔ `backfill_t1` 的「交易日」来自 `net_days()`（扫 POOL 里的 pool_call-*.json），
    #    原来只 patch 了 KLINE/OUT，net_days 仍读生产目录 → 永远取不到 tmp 里的台账，
    #    回补自然填不上（这就是这两个用例长期红的真正原因，不是数据缺失）。
    (pool / "pool_call-2026-11-06.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mod, "KLINE", kline)
    monkeypatch.setattr(mod, "OUT", out)
    monkeypatch.setattr(mod, "POOL", pool)

    def _bars(code: str) -> dict:
        return mod.bars(code)

    mod._kline_dir = kline                            # type: ignore[attr-defined]
    return mod


def _write_kline(kline_dir: Path, code: str, bars: list[tuple[str, float, float]]) -> None:
    """bars = [(date, open, close), …]"""
    (kline_dir / f"{code}.json").write_text(
        json.dumps({"bars": [{"date": d, "open": o, "close": c, "high": c, "low": o}
                             for d, o, c in bars]}, ensure_ascii=False),
        encoding="utf-8")


def _row(code: str, au: float, t1: float | None, tradable: bool = True, **extra) -> dict:
    r = {"code": code, "name": code, "auction_price": au, "t1_ret": t1, "t1_date": None,
         "au_ret": 1.23, "oc_ret": 0.5, "dev": -1.5, "tradable": tradable}
    r.update(extra)
    return r


def _ledger(pl, date: str, rows: list[dict], views: bool = True) -> dict:
    led = {"date": date, "generated_at": "2026-11-06 16:00:00",
           "summary": {"auction_buy_close_sell": {"n": 1, "avg": 1.23},
                       "auction_buy_next_close_sell": {"n": 0}}}
    if views:
        led["summary"]["by_dev_band"] = {"−2~0": {"n": 1, "avg": 1.23}}
        led["summary"]["by_view"] = {"participate": {"n": 1, "avg": 1.23}}
    led["rows"] = rows
    return led


def test_backfill_fills_missing_t1(pl):
    """次日 K 线已落盘 → 填上 t1_ret（竞价价买 → 次日收盘卖）。"""
    _write_kline(pl._kline_dir, "000001", [("2026-11-06", 10.0, 10.5), ("2026-11-09", 10.6, 11.0)])
    led = _ledger(pl, "2026-11-06", [_row("000001", au=10.2, t1=None)])
    pl.OUT.joinpath("2026-11-06.json").write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")

    assert pl.backfill_t1(5, write=True) == 0

    got = json.loads((pl.OUT / "2026-11-06.json").read_text(encoding="utf-8"))
    r = got["rows"][0]
    # 11.0 / 10.2 − 1 = +7.84%
    assert r["t1_ret"] == 7.84
    assert r["t1_date"] == "2026-11-09"
    assert got["summary"]["auction_buy_next_close_sell"]["n"] == 1
    assert got["t1_backfilled_at"]


def test_backfill_never_overwrites_existing(pl):
    """已有数值 ⛔ 不被覆盖 —— 不改历史已定稿数字。"""
    _write_kline(pl._kline_dir, "000001", [("2026-11-06", 10.0, 10.5), ("2026-11-09", 10.6, 99.0)])
    led = _ledger(pl, "2026-11-06", [_row("000001", au=10.2, t1=5.55, t1_date="2026-11-09")])
    pl.OUT.joinpath("2026-11-06.json").write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")

    pl.backfill_t1(5, write=True)

    got = json.loads((pl.OUT / "2026-11-06.json").read_text(encoding="utf-8"))
    assert got["rows"][0]["t1_ret"] == 5.55          # 原值，不是 99/10.2−1


def test_backfill_keeps_none_when_next_bar_missing(pl):
    """次日 K 线没落盘 → 保持 None（⛔ 不填 0、不拿当日收盘顶）。"""
    _write_kline(pl._kline_dir, "000001", [("2026-11-06", 10.0, 10.5)])   # 只有当日
    led = _ledger(pl, "2026-11-06", [_row("000001", au=10.2, t1=None)])
    pl.OUT.joinpath("2026-11-06.json").write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")

    pl.backfill_t1(5, write=True)

    got = json.loads((pl.OUT / "2026-11-06.json").read_text(encoding="utf-8"))
    assert got["rows"][0]["t1_ret"] is None
    assert got["summary"]["auction_buy_next_close_sell"] == {"n": 0}
    assert "t1_backfilled_at" not in got             # 一笔没补 → 不盖时间戳


def test_backfill_does_not_touch_main_caliber(pl):
    """主口径（au_ret）与所有分档汇总原样不动。"""
    _write_kline(pl._kline_dir, "000001", [("2026-11-06", 10.0, 10.5), ("2026-11-09", 10.6, 11.0)])
    led = _ledger(pl, "2026-11-06", [_row("000001", au=10.2, t1=None, au_ret=2.94)])
    before = json.loads(json.dumps(led))
    pl.OUT.joinpath("2026-11-06.json").write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")

    pl.backfill_t1(5, write=True)

    got = json.loads((pl.OUT / "2026-11-06.json").read_text(encoding="utf-8"))
    assert got["rows"][0]["au_ret"] == before["rows"][0]["au_ret"] == 2.94
    assert got["summary"]["auction_buy_close_sell"] == before["summary"]["auction_buy_close_sell"]
    assert got["summary"]["by_dev_band"] == before["summary"]["by_dev_band"]
    assert got["summary"]["by_view"] == before["summary"]["by_view"]
    assert got["generated_at"] == before["generated_at"]      # 台账主体生成时间不改


def test_backfill_summary_counts_only_tradable(pl):
    """汇总只算可成交票（一字/竞价封死的不参与），与 build() 同口径。"""
    _write_kline(pl._kline_dir, "000001", [("2026-11-06", 10.0, 10.5), ("2026-11-09", 10.6, 11.0)])
    _write_kline(pl._kline_dir, "000002", [("2026-11-06", 10.0, 10.5), ("2026-11-09", 10.6, 9.0)])
    led = _ledger(pl, "2026-11-06", [
        _row("000001", au=10.2, t1=None, tradable=True),
        _row("000002", au=10.2, t1=None, tradable=False),   # 一字板 → 不计入
    ])
    pl.OUT.joinpath("2026-11-06.json").write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")

    pl.backfill_t1(5, write=True)

    got = json.loads((pl.OUT / "2026-11-06.json").read_text(encoding="utf-8"))
    agg = got["summary"]["auction_buy_next_close_sell"]
    assert agg["n"] == 1
    assert agg["avg"] == 7.84


def test_backfill_check_mode_writes_nothing(pl):
    """`--check` 只打印不落盘（与主流程同语义）。"""
    _write_kline(pl._kline_dir, "000001", [("2026-11-06", 10.0, 10.5), ("2026-11-09", 10.6, 11.0)])
    led = _ledger(pl, "2026-11-06", [_row("000001", au=10.2, t1=None)])
    fp = pl.OUT / "2026-11-06.json"
    fp.write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")
    before = fp.read_text(encoding="utf-8")

    pl.backfill_t1(5, write=False)

    assert fp.read_text(encoding="utf-8") == before


def test_backfill_skips_days_without_ledger_file(pl):
    """台账文件还没生成过的日子 → 跳过（交给正常 build 去写，不凭空造文件）。"""
    _write_kline(pl._kline_dir, "000001", [("2026-11-06", 10.0, 10.5), ("2026-11-09", 10.6, 11.0)])
    pl.OUT.joinpath("2026-11-05.json").write_text(
        json.dumps(_ledger(pl, "2026-11-05", []), ensure_ascii=False), encoding="utf-8")

    assert pl.backfill_t1(5, write=True) == 0
    assert not (pl.OUT / "2026-11-06.json").exists()


def test_cli_backfill_flag_defaults_to_five(pl, monkeypatch):
    """`--backfill-t1` 不带值 = 最近 5 个交易日（nargs='?' 的 const）。"""
    seen: list[tuple[int, bool]] = []
    monkeypatch.setattr(pl, "backfill_t1", lambda n, write=True: (seen.append((n, write)), 0)[1])
    monkeypatch.setattr(pl, "net_days", lambda: ["2026-11-06"])
    monkeypatch.setattr(pl, "build", lambda d, allow_missing=False: (None, 2))
    monkeypatch.setattr("sys.argv", ["pool_ledger.py", "--backfill-t1"])
    pl.main()
    assert seen == [(5, True)]


def test_cli_backfill_accepts_explicit_days(pl, monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(pl, "backfill_t1", lambda n, write=True: (seen.append(n), 0)[1])
    monkeypatch.setattr(pl, "net_days", lambda: ["2026-11-06"])
    monkeypatch.setattr(pl, "build", lambda d, allow_missing=False: (None, 2))
    monkeypatch.setattr("sys.argv", ["pool_ledger.py", "--backfill-t1", "10"])
    pl.main()
    assert seen == [10]
