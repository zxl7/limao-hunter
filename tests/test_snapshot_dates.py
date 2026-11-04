"""快照日期与生产接线回归；所有持久化均隔离到临时目录。"""
import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "wealth_code"))
from scripts import snapshot_build as sb
from core import snapshot as snap
from pipelines import pipeline_verify as pv

D = "2026-11-04"
PREV = "2026-11-03"


def write_json(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "ROOT", tmp_path)
    monkeypatch.setattr(sb, "_prev_trade_day", lambda day: PREV)
    return tmp_path


@pytest.mark.parametrize("builder", [sb.build_candidates, sb.build_gates])
def test_no_today_fallback(isolated, builder):
    write_json(isolated, f"wealth_code/cache/candidates-{D}.json",
               {"date": D, "relay": [{"code": "000001"}],
                "diagnostics": {"dragon_gate": {"enabled": True}}})
    assert builder(D) is None


@pytest.mark.parametrize("builder", [sb.build_candidates, sb.build_gates])
def test_candidate_file_content_date_must_match(isolated, builder):
    write_json(isolated, f"wealth_code/cache/candidates-{PREV}.json",
               {"date": D, "diagnostics": {"dragon_gate": {"enabled": True}}})
    assert builder(D) is None


def test_previous_candidates_and_gates(isolated):
    write_json(isolated, f"wealth_code/cache/candidates-{PREV}.json",
               {"date": PREV, "relay": [{"code": "000001"}],
                "diagnostics": {"dragon_gate": {"enabled": True, "secret": 1}}})
    assert sb.build_candidates(D)["candidate_date"] == PREV
    gates = sb.build_gates(D)
    assert gates["candidate_date"] == PREV
    assert gates["dragon_gate"] == {"enabled": True}


@pytest.mark.parametrize("source,builder", [("feedback_matrix", sb.build_market),
                                            ("theme_rotation", sb.build_themes)])
def test_rolling_date_must_match(isolated, source, builder):
    write_json(isolated, f"cache/{source}.json", {"date": PREV, "sentiment_score": 8, "top": ["旧"]})
    assert builder(D) is None
    write_json(isolated, f"cache/{source}.json", {"date": D, "sentiment_score": 8, "top": ["新"]})
    assert builder(D) is not None


def test_root_isolates_inputs_and_restores_global(tmp_path):
    original = sb.ROOT
    write_json(tmp_path, f"wealth_code/cache/candidates-{PREV}.json", {"date": PREV, "relay": []})
    assert sb.main(["--date", D, "--root", str(tmp_path), "--only", "candidates"]) == 0
    assert snap.read_section(D, "candidates", root=tmp_path)["candidate_date"] == PREV
    assert sb.ROOT == original


def test_lines_receives_root(isolated, monkeypatch):
    from core import pool_call
    seen = []
    def load(day, *, root):
        seen.append((day, root))
        return {"线": {"code": "000001"}}
    monkeypatch.setattr(pool_call, "load_line_cores", load)
    assert sb.build_lines(D)["线"]["code"] == "000001"
    assert seen == [(D, isolated)]


@pytest.mark.parametrize("failure", ["builder", "write_false", "write_raise", "health"])
def test_build_failures_return_two(isolated, monkeypatch, failure):
    def boom(*args, **kwargs):
        raise OSError("测试失败")
    monkeypatch.setitem(sb.BUILDERS, "market", boom if failure == "builder" else lambda day: {"x": 1})
    if failure == "write_false":
        monkeypatch.setattr(snap, "put_section", lambda *args, **kwargs: False)
    if failure == "write_raise":
        monkeypatch.setattr(snap, "put_section", boom)
    if failure == "health":
        monkeypatch.setattr(snap, "write_health", boom)
    assert sb.main(["--date", D, "--root", str(isolated), "--only", "market", "--guard"]) == 2


def test_health_silent_write_failure_returns_two(isolated, monkeypatch):
    monkeypatch.setattr(snap, "_atomic_write", lambda *args: False)
    assert sb.main(["--date", D, "--root", str(isolated), "--only", "market", "--guard"]) == 2


def test_business_contradiction_returns_zero(isolated):
    snap.put_section(D, "candidates", {"stocks": {}}, root=isolated)
    snap.put_section(D, "verdict", {"participate": ["000001"]}, root=isolated)
    assert sb.main(["--date", D, "--root", str(isolated), "--only", "market", "--guard"]) == 0
    assert snap.read_section(D, "health", root=isolated)["ok"] is False


@pytest.mark.parametrize("persist,production", [(True, True), (False, True), (True, False)])
def test_verify_actual_candidate_date(tmp_path, monkeypatch, persist, production):
    monkeypatch.setattr(pv, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pv, "_PROD_CACHE_DIR", tmp_path if production else tmp_path / "other")
    monkeypatch.setattr(pv.ai_caliber, "AI_CALIBER_ACCOUNT", False)
    monkeypatch.setattr(pv, "sync_account_strategy_metrics", lambda **kwargs: {})
    monkeypatch.setattr(pv, "build_account_strategy_metrics", lambda **kwargs: {})
    calls = []
    monkeypatch.setattr(snap, "put_section", lambda day, section, payload: calls.append((day, section, payload)))
    pv.run_verify({"date": PREV, "relay": []}, {}, out_nav=False, persist=persist)
    if persist and production:
        assert calls[0][0:2] == (D, "candidates")
        assert calls[0][2]["candidate_date"] == PREV
    else:
        assert calls == []


def test_eod_aggregation_preserves_morning(isolated):
    # 仅执行现有聚合语句块，不调用生产 run_eod 或其数据获取/决策流程。
    tree = ast.parse((BASE / "wealth_code/pipelines/pipeline_eod.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_eod")
    block = next(n for n in fn.body if isinstance(n, ast.Try)
                 and any(isinstance(x, ast.ImportFrom) and any(a.name == "snapshot" for a in x.names)
                         for x in n.body))
    snap.put_section(D, "candidates", {"candidate_date": PREV}, root=isolated)
    snap.put_section(D, "gates", {"candidate_date": PREV}, root=isolated)
    real_put = snap.put_section
    from unittest.mock import patch
    with patch.object(snap, "put_section", side_effect=lambda day, section, payload: real_put(day, section, payload, root=isolated)):
        exec(compile(ast.Module(body=[block], type_ignores=[]), "eod_aggregation", "exec"),
             {"zt": {"date": D}, "payload": {"date": D, "relay": []},
              "diagnostics": {"dragon_gate": {"enabled": True}}})
    assert snap.read_section(D, "candidates", root=isolated)["candidate_date"] == PREV
    assert snap.read_section(D, "gates", root=isolated)["candidate_date"] == PREV
    assert snap.read_section(D, "eod_candidates", root=isolated)["candidate_date"] == D
    assert snap.read_section(D, "eod_gates", root=isolated)["candidate_date"] == D
    assert snap.coverage(D, root=isolated)["total"] == 8


def test_capture_step14_checks_returncode(monkeypatch):
    tree = ast.parse((BASE / "scripts/capture_daily.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    block = next(n for n in fn.body if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Name) and n.test.id == "ENABLE_SNAPSHOT_GUARD")
    import subprocess
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=2, stdout="覆盖 4/8", stderr="写入失败")
    monkeypatch.setattr(subprocess, "run", run)
    env = {"ENABLE_SNAPSHOT_GUARD": True, "sys": sys, "Path": Path,
           "__file__": str(BASE / "scripts/capture_daily.py"), "today": D}
    exec(compile(ast.Module(body=[block], type_ignores=[]), "capture_step14", "exec"), env)
    assert commands[0][commands[0].index("--only") + 1] == "market,themes,ladder,lines"
    assert "ERR:rc=2" in env["sg_msg"]
