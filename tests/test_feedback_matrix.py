import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "scripts" / "feedback_matrix.py"
spec = importlib.util.spec_from_file_location("feedback_matrix", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_strong_breadth_and_volume_is_euphoric():
    score, label = mod.sentiment_score(
        0.55, 0.50, 3.0, 0, 0.10, 1, 1.0, 80.0, 1.5,
        market_amount_yi=28000, breadth_up=4200, breadth_down=900,
    )
    assert score >= 9.0
    assert label == "亢奋"


def test_weak_market_is_not_euphoric():
    score, label = mod.sentiment_score(
        0.10, 0.08, -2.0, 5, 0.60, -1, -0.5, 15.0, -1.0,
        market_amount_yi=9000, breadth_up=800, breadth_down=4200,
    )
    assert score <= 3.0
    assert label in {"退潮", "分歧"}


def test_sentiment_returns_bounded_score():
    score, _ = mod.sentiment_score(1, 1, 20, 0, 0, 1, 10, 1, 10)
    assert 1 <= score <= 10


def test_continuity_and_monotonicity():
    def score(up):
        return mod.sentiment_score(.25, .4, 0, 2, .25, 0, 0, up, 0)[0]
    values = [score(up) for up in range(101)]
    assert values == sorted(values)
    assert max(b - a for a, b in zip(values, values[1:])) <= .2
    assert abs(score(69.99) - score(70.01)) <= .1


def test_ordinary_strong_market_has_headroom():
    score, label = mod.sentiment_score(.35, .5, 2, 1, .2, 0, .3, 65, .8)
    assert 6 < score < 9
    assert label in {"修复", "偏强"}


def test_nuke_and_its_break_subset_are_not_double_counted():
    args = (.2, .4, -1, 10, .3, 0, -.2, 40, -.5)
    assert mod.sentiment_score(*args, break_count=0) == mod.sentiment_score(*args, break_count=3)


def test_missing_average_and_lb_sample_are_not_weak_observations():
    args = (.25, None, None, 0, .25, 0, 0, 50, 0)
    assert mod.sentiment_score(*args)[0] == 5


def test_environment_missing_score_is_not_five(tmp_path):
    import json
    from wealth_code.adapters.input_zt import load_environment
    assert load_environment(tmp_path)["sentiment_score"] is None
    path = tmp_path / "feedback_matrix.json"
    for value in (None, False, float("nan"), -1, 11):
        path.write_text(json.dumps({"sentiment_score": value, "break_count": 1}))
        result = load_environment(tmp_path)
        assert result["sentiment_score"] is None
        assert result["regime"] == "risk_off"
    path.write_text(json.dumps({"sentiment_score": 7.2, "sentiment_quality": "partial",
                                "sentiment_inputs_missing": ["idx_avg"]}))
    result = load_environment(tmp_path)
    assert result["sentiment_score"] == 7.2
    assert result["sentiment_quality"] == "partial"
    assert result["sentiment_inputs_missing"] == ["idx_avg"]


def test_all_core_inputs_missing_is_unknown():
    assert mod.sentiment_score(*([None] * 9)) == (None, "未知")


def test_legacy_switch_accepts_missing_quote_fields(monkeypatch):
    monkeypatch.setattr(mod, "SENTIMENT_MODEL", "legacy_v2")
    score, label = mod.sentiment_score(.25, None, None, 0, .25, 0, 0, None, 0)
    assert 1 <= score <= 10
    assert label in {"退潮", "分歧", "修复", "偏强", "亢奋"}


def test_snapshot_sync_only_exact_round(tmp_path):
    import json
    path = tmp_path / "latest.json"
    ts = "2026-11-04T10:00:00+08:00"
    path.write_text(json.dumps({"ts": ts, "sentiment_detail": {"seal_rate": 80}}))
    feedback = {"date": "2026-11-04", "source_snapshot_ts": ts,
                "sentiment_score": 6.8, "sentiment_label": "修复",
                "sentiment_model": "continuous_v3"}
    assert mod.sync_snapshot_sentiment(feedback, path)
    snapshot = json.loads(path.read_text())
    assert snapshot["sentiment_detail"]["score"] == 6.8
    assert snapshot["sentiment_detail"]["seal_rate"] == 80
    before = path.read_bytes()
    assert not mod.sync_snapshot_sentiment({**feedback, "source_snapshot_ts": "other"}, path)
    assert path.read_bytes() == before
    assert not mod.sync_snapshot_sentiment({**feedback, "date": "2026-11-03"}, path)


def test_environment_rejects_legacy_signed_score(tmp_path):
    import json
    from wealth_code.adapters.input_env import load_pulse
    directory = tmp_path / "pulse_snapshots"
    directory.mkdir()
    path = directory / "latest.json"
    path.write_text(json.dumps({"ts": "2026-11-04T10:00:00+08:00",
                                "sentiment": "分歧", "sentiment_detail": {"score": -1}}))
    assert load_pulse(tmp_path)["sentiment_score"] is None
    path.write_text(json.dumps({"ts": "2026-11-04T10:00:00+08:00",
                                "sentiment_detail": {"score": 6.8, "label": "修复",
                                                     "source": "feedback_matrix", "status": "ready"}}))
    assert load_pulse(tmp_path)["sentiment_score"] == 6.8


def test_relay_unknown_sentiment_has_no_mood_penalty():
    from wealth_code.core.relay_score import _env_multiplier, _load_config
    cfg = _load_config()
    for regime in ("neutral", "risk_off", "ice", "risk_on"):
        expected = cfg["env_multiplier"].get(regime, 1.0)
        for value in (None, False, True, float("nan"), float("inf"), -1, 0, 11, "5"):
            assert _env_multiplier({"regime": regime, "sentiment_score": value}, cfg) == (expected, 0)
    assert _env_multiplier({"sentiment_score": 2}, cfg)[1] < 0
    assert _env_multiplier({"sentiment_score": 9}, cfg)[1] > 0
    assert _env_multiplier({"sentiment_score": 5}, cfg)[1] == 0


def test_relay_quality_and_ma5_annotation_are_read_only(tmp_path, monkeypatch):
    import json
    from wealth_code.core import relay_score as relay
    monkeypatch.setattr(relay, "ROOT", tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    path = cache / "ma5_status.json"
    path.write_text(json.dumps({"items": [{"code": "000001", "above_ma5": False, "pct_vs_ma5": -2}]}))
    before = path.read_bytes()
    cand = {"code": "000001", "boards": 2, "cje_yi": 10}
    env = {"regime": "risk_off", "sentiment_score": None, "sentiment_quality": "unavailable"}
    rows = relay.rank_relay_candidates([cand], env, sector_agg={})
    assert rows[0]["sentiment_adjustment_available"] is False
    assert rows[0]["env_adj"] == 0
    assert rows[0]["env_mult"] == relay._load_config()["env_multiplier"]["risk_off"]
    assert rows[0]["ma5_broken"] is True
    assert path.read_bytes() == before


def test_factor_registry_is_single_source():
    keys = [f.key for f in mod.factor_set()]
    assert len(keys) == len(set(keys)), "因子键必须唯一，否则诊断会互相覆盖"
    assert keys == [f.key for f in mod.FACTOR_REGISTRY]
    for f in mod.FACTOR_REGISTRY:
        assert f.dim in mod.DIMENSION_LABELS, f"维度 {f.dim} 缺中文名"
        assert f.inputs, f"因子 {f.key} 未声明输入"
        assert f.source, f"因子 {f.key} 未声明数据来源"
        if isinstance(f.points, dict):
            assert set(f.points) == set(f.inputs), f"因子 {f.key} 的分列点表与实际输入不一致"
        else:
            assert len(f.points) >= 2 and f.points == tuple(sorted(f.points))


def test_factor_set_factory_does_not_fake_legacy_breakdown():
    assert mod.factor_set("continuous_v3") is mod.FACTOR_REGISTRY
    assert mod.factor_set("legacy_v2") == ()


def test_breakdown_matches_score_and_sums_to_strength():
    cases = [
        (.55, .50, 3.0, 0, .10, 1, 1.0, 80.0, 1.5, 0, 28000, 4200, 900, 0),
        (.10, .08, -2.0, 5, .60, -1, -.5, 15.0, -1.0, 3, 9000, 800, 4200, 2.0),
        (.25, .4, 0, 2, .25, 0, 0, 50, 0, 0, 15000, 0, 0, 0),
        (.35, .5, 2, 1, .2, 0, .3, 65, .8, 0, 20000, 2600, 1200, 0),
        (.70, .60, 4.5, 0, .05, 1, .8, 92, 3.0, 0, 32000, 3900, 600, 0),
    ]
    for args in cases:
        kwargs = {"break_count": args[9], "market_amount_yi": args[10],
                  "breadth_up": args[11], "breadth_down": args[12],
                  "regulatory_pressure": args[13]}
        score, label = mod.sentiment_score(*args[:9], **kwargs)
        bd = mod.sentiment_breakdown(*args[:9], **kwargs)
        assert (bd["score"], bd["label"]) == (score, label), "诊断与主分必须同源同值"
        total = sum(d["value"] for d in bd["dimensions"])
        # 维度小计按 4 位小数落值，累加允许末位舍入误差。
        assert abs(total - bd["strength"]) < 1e-3, "维度小计应等于总强度"
        assert all(d["label"] for d in bd["dimensions"])


def test_breakdown_marks_missing_instead_of_zero_evidence():
    args = (.25, None, None, None, None, 0, 0, None, None)
    bd = mod.sentiment_breakdown(*args)
    # promote=.25 / max_trend=0 / zt_expand=0 均在中性点，强度 0 → 5.0「修复」；
    # 但缺的那几项必须标未观测，不能渲染成「中性证据」。
    assert (bd["score"], bd["label"]) == (5.0, "修复")
    missing = set(bd["missing_factors"])
    assert {"avg_pct", "up_rate", "lb_promote", "loss", "broken_rate", "idx_avg"} <= missing
    relay = next(d for d in bd["dimensions"] if d["key"] == "relay")
    lb = next(f for f in relay["factors"] if f["key"] == "lb_promote")
    assert lb["observed"] is False and lb["value"] == 0
    assert "lb_promote" in missing


def test_breakdown_unknown_when_no_core_observation():
    bd = mod.sentiment_breakdown(None, None, None, None, None, None, None, None, None)
    assert bd["score"] is None and bd["label"] == "未知"
    assert bd["dimensions"] == [] and bd["observed_dimensions"] == 0
    assert (bd["coverage"], bd["coverage_grade"]) == (0.0, "none")
    assert bd["observed_factors"] == 0 and bd["total_factors"] == len(mod.factor_set())
    assert mod.sentiment_score(None, None, None, None, None, None, None, None, None) == (None, "未知")


def test_breakdown_coverage_grades_observation_support():
    """覆盖率只描述「有多少观测支撑」，不改分、不产生停发阈值。"""
    # 9 个必填全给，可选三项全给 → 全观测
    full = mod.sentiment_breakdown(.35, .5, 2, 1, .2, 0, .3, 65, .8,
                                   break_count=2, market_amount_yi=20000,
                                   breadth_up=3000, breadth_down=1000,
                                   regulatory_pressure=0.0)
    assert (full["coverage"], full["coverage_grade"]) == (1.0, "high")
    assert full["observed_factors"] == full["total_factors"]
    assert full["missing_factors"] == []
    # 同一组必填只缺可选三项（12 项中观测 9 项）
    partial = mod.sentiment_breakdown(.35, .5, 2, 1, .2, 0, .3, 65, .8, break_count=2)
    assert (partial["coverage"], partial["coverage_grade"]) == (0.75, "medium")
    # 分母是注册表因子数，不是维度数——加因子不许把覆盖率算虚高
    assert partial["total_factors"] == len(mod.factor_set()) == 12
    # 覆盖率是聚合之后的事后统计，不参与强度累加：
    # 观测数 + 缺失数恒等于总数，且诊断分必须等于同参数主分。
    assert partial["observed_factors"] + len(partial["missing_factors"]) == partial["total_factors"]
    assert (partial["score"], partial["label"]) == mod.sentiment_score(
        .35, .5, 2, 1, .2, 0, .3, 65, .8, break_count=2)


def test_breakdown_legacy_is_not_decomposed(monkeypatch):
    monkeypatch.setattr(mod, "SENTIMENT_MODEL", "legacy_v2")
    bd = mod.sentiment_breakdown(.25, .4, 0, 0, .25, 0, 0, 50, 0)
    assert bd["decomposable"] is False
    assert "dimensions" not in bd


def test_relay_loader_preserves_missing_score(tmp_path):
    from wealth_code.core.relay_score import _load_daily_metrics
    data = _load_daily_metrics(tmp_path)
    assert data["sentiment_score"] is None
    assert data["sentiment_quality"] == "unavailable"


def test_expectation_missing_score_is_not_repair():
    from wealth_code.core.expectation_ai import sentiment_phase, build_ai_expectation
    cand = {"code": "000001", "boards": 2, "cje_yi": 10, "hs": 8, "fbt": "100000"}
    for value in (None, False, True, float("nan"), float("inf"), 0, 11, "5"):
        env = {"sentiment_score": value, "sentiment_label": "修复", "nuke_count": 5}
        assert sentiment_phase(env)[0] == "未知"
        result = build_ai_expectation(cand, env)
        assert result["phase"] == "未知"
        assert result["gap_strength"] == "unknown"
        from wealth_code.pipelines.build_cards import _gap_strength_short
        assert _gap_strength_short({"expectation": result}) == "情绪待确认"
        assert result["sentiment_available"] is False
        assert "情绪修复" not in result["low_text"]
        assert "直接买" not in result["super_text"]
        assert "不构成环境放行" in result["super_text"]
        assert result["expected_range"] is not None
        assert env["nuke_count"] == 5


def test_expectation_valid_phase_and_partial_quality_preserved():
    from wealth_code.core.expectation_ai import sentiment_phase, build_ai_expectation
    env = {"sentiment_score": 6.8, "sentiment_label": "修复", "nuke_count": 0,
           "sentiment_model": "continuous_v3", "sentiment_quality": "partial",
           "sentiment_inputs_missing": ["market_amount"]}
    assert sentiment_phase(env)[0] == "修复"
    cand = {"code": "000001", "boards": 2, "cje_yi": 10, "hs": 8, "fbt": "100000"}
    result = build_ai_expectation(cand, env)
    assert result["sentiment_available"] is True
    assert result["sentiment_quality"] == "partial"
    assert result["sentiment_inputs_missing"] == ["market_amount"]
    assert result["sentiment_model"] == "continuous_v3"
