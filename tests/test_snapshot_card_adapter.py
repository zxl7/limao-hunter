"""卡片快照适配：只读、严格等价、仅真实推送前接入。"""
import argparse
from copy import deepcopy
from datetime import datetime as RealDatetime
import importlib.util
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "snapshot_card_cli", BASE / "wealth_code/scripts/run_wealth_code.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.fixture
def res():
    return {
        "summary": {"date10": "2099-01-01", "trade_date10": "2099-01-02",
                    "pool_call": {"participate": ["000001"], "effective_quota": 1,
                                  "defense": {"other": True, "anchor": {
                                      "other": "保留", "lines": {
                                          "测试线甲": {"name": "测试甲", "boards": 2,
                                                      "attitude": "strong", "gap": 2.0},
                                          "测试线乙": {"name": "测试乙", "boards": 2,
                                                      "attitude": "strong", "gap": 3.0}}}}}},
        "payload": {"records": []}, "global_narrative": "保留旁白"}


def lines(res):
    return res["summary"]["pool_call"]["defense"]["anchor"]["lines"]


def fail(*args, **kwargs):
    pytest.fail("不应读取快照或触发外部副作用")


def test_equal_snapshot_preserves_full_output_and_inputs(res, monkeypatch):
    before = deepcopy(res)
    snapshot_lines = dict(reversed(list(deepcopy(lines(res)).items())))
    verdict = {"anchor_lines": snapshot_lines, "participate": ["不应采用"]}
    verdict_before = deepcopy(verdict)
    calls = []

    def loader(day):
        calls.append(day)
        return verdict, "snapshot", day

    adapted, diag = cli.adapt_verify_card_snapshot(res, loader, enabled=True)
    assert adapted == before
    assert adapted is not res
    assert calls == ["2099-01-02"]
    assert diag == {"source": "snapshot", "reason": "equal", "date": "2099-01-02"}
    assert list(lines(adapted)) == list(lines(res))
    monkeypatch.setattr(cli.build_cards, "_core_reversal_block", lambda: None)
    monkeypatch.setattr(cli.build_cards, "_nav_drawdown_block", lambda *a: None)
    card = cli.build_cards.build_verify_card(adapted)
    assert card == cli.build_cards.build_verify_card(res)
    assert "测试甲" in str(card) and "测试乙" in str(card)
    assert res == before and verdict == verdict_before
    lines(adapted)["测试线甲"]["gap"] = 99
    assert res == before and verdict == verdict_before


@pytest.mark.parametrize("case,reason", [
    ("wrong_date", "date_mismatch"), ("unknown_date", "date_mismatch"),
    ("different", "lines_mismatch"), ("extra", "lines_mismatch"),
    ("empty", "lines_mismatch"), ("no_lines", "lines_mismatch"),
    ("missing", "missing_snapshot"), ("legacy", "missing_snapshot"),
    ("error", "load_error")])
def test_snapshot_fallback(res, case, reason):
    before = deepcopy(res)
    verdict = {"anchor_lines": deepcopy(lines(res))}
    date = "2099-01-02"
    source = "snapshot"
    if case == "wrong_date":
        date = "2099-01-01"
    elif case == "unknown_date":
        date = None
    elif case == "different":
        verdict["anchor_lines"]["测试线甲"]["gap"] = 0
    elif case == "extra":
        verdict["anchor_lines"]["旧线"] = {}
    elif case == "empty":
        verdict["anchor_lines"] = {}
    elif case == "no_lines":
        verdict = {}
    elif case == "missing":
        verdict, source = None, "missing"
    elif case == "legacy":
        source = "legacy"

    def loader(day):
        if case == "error":
            raise OSError("模拟读取失败")
        return verdict, source, date

    adapted, diag = cli.adapt_verify_card_snapshot(res, loader, enabled=True)
    assert adapted == before and res == before
    assert adapted is not res
    assert diag == {"source": "injected", "reason": reason}


@pytest.mark.parametrize("field", ["summary", "pool_call", "defense", "anchor", "lines", "empty", "none", "date"])
def test_missing_or_empty_injection_never_loads(res, field):
    if field == "summary":
        res.pop("summary")
    elif field == "pool_call":
        res["summary"].pop("pool_call")
    elif field == "defense":
        res["summary"]["pool_call"].pop("defense")
    elif field == "anchor":
        res["summary"]["pool_call"]["defense"].pop("anchor")
    elif field == "date":
        res["summary"].pop("trade_date10")
    else:
        anchor = res["summary"]["pool_call"]["defense"]["anchor"]
        if field == "lines":
            anchor.pop("lines")
        else:
            anchor["lines"] = {} if field == "empty" else None
    before = deepcopy(res)
    adapted, diag = cli.adapt_verify_card_snapshot(res, fail, enabled=True)
    assert adapted == before and res == before
    assert diag["source"] == "injected"


def test_disabled_never_loads(res, monkeypatch):
    monkeypatch.setenv("ENABLE_CARD_SNAPSHOT_READ", "0")
    adapted, diag = cli.adapt_verify_card_snapshot(res, fail)
    assert adapted == res and adapted is not res
    assert diag == {"source": "injected", "reason": "disabled"}


@pytest.mark.parametrize("source_date,as_of,expected", [
    ("2099-01-02", None, "snapshot"),
    ("2099-01-01", "2099-01-02T09:25:00+08:00", "injected"),
    (None, "2099-01-02T09:25:00+08:00", "snapshot"),
    (None, None, "injected")])
def test_default_loader_uses_facade_and_source_date(res, monkeypatch, source_date, as_of, expected):
    from core import snapshot
    calls = []

    def read(day, section):
        calls.append((day, section))
        return {"anchor_lines": deepcopy(lines(res))}, "snapshot"

    monkeypatch.setenv("ENABLE_CARD_SNAPSHOT_READ", "1")
    monkeypatch.setattr(snapshot, "read_with_fallback", read)
    monkeypatch.setattr(snapshot, "read_meta", lambda *a: {"source_date": source_date, "as_of": as_of})
    adapted, diag = cli.adapt_verify_card_snapshot(res)
    assert adapted == res
    assert diag["source"] == expected
    assert calls == [("2099-01-02", "verdict")]


@pytest.mark.parametrize("push,synthesize,early", [
    (False, False, False), (False, True, False),
    (True, True, False), (True, False, True), (True, False, False)])
def test_cmd_only_adapts_real_push_after_guard(res, monkeypatch, push, synthesize, early):
    args = argparse.Namespace(candidates=None, synthesize=synthesize, recompute=False,
                              fetch=False, quotes=None, narratives=None, push=push)
    before = deepcopy(res)
    monkeypatch.setattr(cli, "_load_latest_candidates", lambda: {})
    monkeypatch.setattr(cli.input_auction, "synthesize_quotes_for_test", lambda *a: ({}, {}))
    monkeypatch.setattr(cli.pipeline_verify, "run_verify", lambda **kw: res)
    monkeypatch.setattr(cli, "_archive_decision", lambda *a: None)
    monkeypatch.setattr(cli.pipeline_verify, "sync_holdings_from_verify", lambda *a: {"holdings": []})

    class Clock:
        @staticmethod
        def now(tz):
            return RealDatetime(2099, 1, 2, 9 if early else 10, 0, tzinfo=tz)

    monkeypatch.setattr(cli, "datetime", Clock)
    adapted_calls = []
    rendered = []

    def adapt(value):
        adapted_calls.append(value)
        return deepcopy(value), {"source": "snapshot"}

    monkeypatch.setattr(cli, "adapt_verify_card_snapshot", adapt)
    monkeypatch.setattr(cli, "_load_card_verdict", fail)
    monkeypatch.setattr(cli.build_cards, "build_verify_card", lambda value: rendered.append(value) or {})
    monkeypatch.setattr(cli.build_cards, "push_card_file", lambda *a: (True, "模拟推送"))
    assert cli.cmd_verify(args) == 0
    assert len(adapted_calls) == int(push and not synthesize and not early)
    assert len(rendered) == int(push and not early)
    if adapted_calls:
        assert rendered[0] is not res
    assert res == before
