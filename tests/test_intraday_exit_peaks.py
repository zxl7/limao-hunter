import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "scripts" / "intraday_exit_peaks.py"
spec = importlib.util.spec_from_file_location("intraday_exit_peaks", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


NOW = "2026-10-21T10:00:00+08:00"


def _latest(*, pcb=3):
    return {
        "ts": NOW,
        "top5": [
            {"name": "PCB", "zt_count": pcb},
            {"name": "机器人", "zt_count": 2},
        ],
    }


def _feedback(*, seal=4.2):
    return {
        "date": "2026-10-21",
        "quotes": {
            "603936": {"name": "博敏电子", "seal_yi": seal, "quote_at": NOW},
            "600000": {"name": "浦发银行", "seal_yi": None, "quote_at": NOW},
        },
    }


def test_peak_is_recorded_with_observation_time():
    state = mod.update_peaks(latest=_latest(), feedback=_feedback())
    stock = state["stocks"]["603936"]
    assert stock["seal_yi_peak"] == 4.2
    assert stock["seal_yi_peak_at"] == NOW
    assert stock["current_sealed"] is True
    assert state["sectors"]["PCB"]["zt_count_peak"] == 3
    assert state["sectors"]["PCB"]["zt_count_peak_at"] == NOW


def test_lower_current_values_do_not_overwrite_peaks():
    old = mod.update_peaks(latest=_latest(pcb=5), feedback=_feedback(seal=8.8))
    latest = _latest(pcb=2)
    latest["ts"] = "2026-10-21T10:10:00+08:00"
    feedback = _feedback(seal=1.5)
    state = mod.update_peaks(latest=latest, feedback=feedback, old=old)
    stock = state["stocks"]["603936"]
    sector = state["sectors"]["PCB"]
    assert stock["current_seal_yi"] == 1.5
    assert stock["seal_yi_peak"] == 8.8
    assert sector["current_zt_count"] == 2
    assert sector["zt_count_peak"] == 5


def test_unsealed_observation_is_not_forged_to_zero():
    old = mod.update_peaks(latest=_latest(), feedback=_feedback(seal=3.0))
    latest = _latest()
    latest["ts"] = "2026-10-21T10:10:00+08:00"
    state = mod.update_peaks(latest=latest, feedback=_feedback(seal=None), old=old)
    rec = state["stocks"]["603936"]
    assert rec["current_seal_yi"] is None
    assert rec["current_sealed"] is False
    assert rec["seal_yi_peak"] == 3.0


def test_bad_feedback_or_cross_day_input_does_not_change_state():
    old = {"date": "2026-10-21", "sentinel": True}
    bad = mod.update_peaks(latest=_latest(), feedback={"error": "pre_auction"}, old=old)
    assert bad == old
    cross_day = _feedback()
    cross_day["date"] = "2026-10-20"
    assert mod.update_peaks(latest=_latest(), feedback=cross_day, old=old) == old


def test_collect_writes_only_to_injected_cache(tmp_path):
    cache = tmp_path / "cache"
    pulse = cache / "pulse_snapshots"
    pulse.mkdir(parents=True)
    (pulse / "latest.json").write_text(__import__("json").dumps(_latest()), encoding="utf-8")
    (cache / "feedback_matrix.json").write_text(__import__("json").dumps(_feedback()), encoding="utf-8")
    ok, path, state = mod.collect(cache_dir=cache)
    assert ok is True
    assert Path(path).exists()
    assert Path(path).parent == cache
    assert state["stocks"]["603936"]["seal_yi_peak"] == 4.2
