#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账本订正工具的行为锁（2026-11-03）。

要点：净值是递推的，错一天后面全错 → 订正必须**连带重推后续每一天**，
且 dry-run 绝对不能落盘。这三条都属于「改钱」的路径，必须有测试。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ledger_repair", ROOT / "wealth_code" / "scripts" / "ledger_repair.py")
lr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lr)          # type: ignore[union-attr]


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "CACHE", tmp_path)
    monkeypatch.setattr(lr, "KLINE_DIR", tmp_path / "kline_qfq")
    monkeypatch.setattr(lr, "AI_LEDGER", tmp_path / "ai_nav_history.jsonl")
    monkeypatch.setattr(lr, "PY_LEDGER", tmp_path / "account_nav_history.jsonl")
    monkeypatch.setattr(lr, "BACKUP_DIR", tmp_path / "ledger_repairs")


def _kline(tmp_path, code, bars):
    d = tmp_path / "kline_qfq"
    # 注意：本环境的 mkdir 拦截层对 exist_ok=True 仍会抛 FileExistsError，先判存在
    if not d.exists():
        d.mkdir(parents=True)
    (d / f"{code}.json").write_text(
        json.dumps({"schema": "kline_qfq_v1", "code": code, "bars": bars}, ensure_ascii=False),
        encoding="utf-8")


def _ledger(path: Path, rows: list[dict]):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _rows():
    """两行：09-30 竞价价写成 09-29 的（21.76），10-08 正常 —— 验证净值连带重推。"""
    return [
        {"trade_date": "2026-09-30", "recommendation_date": "2026-09-29",
         "strategy": "ai_judgement_next_day_close", "stock_count": 1,
         "avg_return_pct": 9.74, "nav": 1.134449,
         "stocks": [{"code": "603533", "name": "掌阅科技", "auction_price": 21.76,
                     "close": 23.88, "return_pct": 9.74}]},
        {"trade_date": "2026-10-08", "recommendation_date": "2026-09-30",
         "strategy": "ai_judgement_next_day_close", "stock_count": 1,
         "avg_return_pct": -5.85, "nav": 1.054867,
         "stocks": [{"code": "000678", "name": "襄阳轴承", "auction_price": 13.0,
                     "close": 12.24, "return_pct": -5.85}]},
    ]


def _setup(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _kline(tmp_path, "603533", [
        {"date": "2026-09-29", "open": 21.76, "close": 23.65},
        {"date": "2026-09-30", "open": 23.90, "close": 23.88},
    ])
    _kline(tmp_path, "000678", [{"date": "2026-10-08", "open": 13.0, "close": 12.24}])
    _ledger(lr.AI_LEDGER, _rows())


def test_dry_run_never_writes(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    before = lr.AI_LEDGER.read_text(encoding="utf-8")
    lr.main.__wrapped__ if hasattr(lr.main, "__wrapped__") else None
    monkeypatch.setattr("sys.argv", ["ledger_repair"])
    lr.main()
    assert lr.AI_LEDGER.read_text(encoding="utf-8") == before, "dry-run 不得落盘"
    out = capsys.readouterr().out
    assert "掌阅科技" in out and "-0.08" in out


def test_apply_fixes_return_and_cascades_nav(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["ledger_repair", "--apply", "--ledger", "ai"])
    lr.main()

    rows = [json.loads(l) for l in lr.AI_LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    r0, r1 = rows[0], rows[1]
    assert r0["stocks"][0]["auction_price"] == 23.90
    assert r0["stocks"][0]["return_pct"] == -0.08
    assert r0["avg_return_pct"] == -0.08
    assert abs(r0["nav"] - round(1.0 * (1 - 0.0008), 6)) < 1e-9
    # 第 2 行收益没变，但净值必须跟着重推（否则递推断裂）
    assert r1["avg_return_pct"] == -5.85
    assert abs(r1["nav"] - round(r0["nav"] * (1 - 0.0585), 6)) < 1e-9
    assert r1["nav"] != 1.054867
    # 备份必须存在且是订正前的内容
    backups = list((tmp_path / "ledger_repairs").glob("*/ai_nav_history.jsonl"))
    assert len(backups) == 1
    old = [json.loads(l) for l in backups[0].read_text(encoding="utf-8").splitlines() if l.strip()]
    assert old[0]["stocks"][0]["return_pct"] == 9.74


def test_idempotent(monkeypatch, tmp_path, capsys):
    """二次运行必须报「无需订正」，⛔ 不能反复改数字。"""
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["ledger_repair", "--apply", "--ledger", "ai"])
    lr.main()
    capsys.readouterr()
    lr.main()
    assert "无需订正" in capsys.readouterr().out


def test_no_kline_row_is_left_alone(monkeypatch, tmp_path, capsys):
    """K线缺失 → 原样保留并标 no_kline，⛔ 不推断、不补零。"""
    _isolate(monkeypatch, tmp_path)
    _ledger(lr.AI_LEDGER, [{
        "trade_date": "2026-09-30", "stock_count": 1, "avg_return_pct": 3.3, "nav": 1.033,
        "stocks": [{"code": "999999", "name": "无K线股", "auction_price": 10.0,
                    "close": 10.33, "return_pct": 3.3}],
    }])
    monkeypatch.setattr("sys.argv", ["ledger_repair", "--apply", "--ledger", "ai"])
    lr.main()
    r = json.loads(lr.AI_LEDGER.read_text(encoding="utf-8").splitlines()[0])
    assert r["stocks"][0]["return_pct"] == 3.3 and r["avg_return_pct"] == 3.3
    assert "no_kline" in capsys.readouterr().out
